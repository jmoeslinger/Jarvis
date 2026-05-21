import io
import logging
import threading
import time
import wave
from typing import Callable, Optional

import numpy as np
import sounddevice as sd
import speech_recognition as sr
from openai import OpenAI

logger = logging.getLogger("jarvis.recognizer")

_SAMPLERATE        = 16000
_CHANNELS          = 1
_DTYPE             = "int16"
_CHUNK_S           = 0.1
_CHUNK_SIZE        = int(_SAMPLERATE * _CHUNK_S)
_PHRASE_PAUSE      = 0.8   # Standard-Pause (Sekunden)
_SMART_PAUSE_SHORT = 0.5   # Kurze Äußerung (< 0.8 s Sprache) → früher stoppen
_SMART_PAUSE_LONG  = 1.1   # Lange Äußerung (> 2.5 s Sprache) → geduldiger sein


def _rms_db(samples: np.ndarray) -> float:
    rms = np.sqrt(np.mean(samples.astype(np.float64) ** 2))
    return 20.0 * np.log10(max(rms, 1.0))


def _denoise_spectral(
    samples_1d: np.ndarray,
    noise_1d: np.ndarray,
    strength: float = 1.0,
) -> np.ndarray:
    """
    Spektrale Subtraktion — reduziert stationäres Hintergrundrauschen.

    samples_1d : int16 1D-Array des aufgenommenen Sprach-Audios
    noise_1d   : int16 1D-Array des kalibrierten Umgebungsgeräusches
    strength   : Subtraktionsstärke (1.0 = ausgewogen, >1.5 = aggressiv)

    Algorithmus:
      1. Rauschspektrum als Median über mehrere Noise-Frames schätzen
         (Median ist robuster als Mittelwert gegen Spitzen-Ereignisse).
      2. Overlap-Add: Signal in überlappende Frames mit Hanning-Fenster zerlegen.
      3. Spektrale Subtraktion mit Mindestpegel (verhindert "musikalisches Rauschen").
      4. Overlap-Add-Rekonstruktion und Amplitudenclipping.
    Gibt ein int16 1D-Array zurück.
    """
    N   = 1024           # FFT-Fenstergröße
    hop = N // 2         # 50 % Overlap
    win = np.hanning(N)

    # ── Rauschspektrum schätzen (Median über max. 16 Frames) ─────────────────
    noise_f = noise_1d.astype(np.float64)
    n_noise_frames = max(1, min(16, len(noise_f) // hop))
    noise_mags = []
    for i in range(n_noise_frames):
        start = i * hop
        nf = noise_f[start:start + N]
        if len(nf) < N:
            nf = np.pad(nf, (0, N - len(nf)))
        noise_mags.append(np.abs(np.fft.rfft(nf * win, N)))
    noise_spectrum = np.median(noise_mags, axis=0)   # robust gegen Impulse

    # ── Overlap-Add Verarbeitung ──────────────────────────────────────────────
    sig     = samples_1d.astype(np.float64)
    out_len = len(sig) + N
    out     = np.zeros(out_len, dtype=np.float64)
    norm    = np.zeros(out_len, dtype=np.float64)

    for start in range(0, len(sig), hop):
        frame = sig[start:start + N]
        if len(frame) < N:
            frame = np.pad(frame, (0, N - len(frame)))

        windowed = frame * win
        spec     = np.fft.rfft(windowed, N)
        mag      = np.abs(spec)
        phase    = np.angle(spec)

        # Spektrale Subtraktion — Mindestpegel 5 % verhindert musikalisches Rauschen
        clean_mag = np.maximum(
            mag - strength * noise_spectrum,
            0.05 * mag,
        )

        clean_frame = np.fft.irfft(clean_mag * np.exp(1j * phase), N) * win

        end = min(start + N, out_len)
        out[start:end]  += clean_frame[:end - start]
        norm[start:end] += (win ** 2)[:end - start]

    # ── Normalisierung und Clipping ───────────────────────────────────────────
    valid = norm > 1e-8
    out[valid] /= norm[valid]
    return np.clip(out[:len(sig)], -32767, 32767).astype(np.int16)


class SpeechRecognizer:
    def __init__(self, language: str = "de-DE", api_key: str = "", api_base: str = "", device: int = -1):
        self.language    = language
        self._lang_short = language[:2]
        self._sr         = sr.Recognizer()
        self._threshold: float = 50.0
        self._whisper: Optional[OpenAI] = None
        self._device: Optional[int] = None if device < 0 else device
        self._last_audio: Optional[bytes] = None   # Letztes aufgenommenes Audio (für Emotion-Analyse)
        self.smart_pause: bool = False              # Adaptiver Pausen-Modus
        self._was_whisper: bool = False             # True wenn letzte Aufnahme Flüstersprache war
        self.noise_filter: bool = False             # Spektrale Rauschunterdrückung aktiv
        self._noise_samples: Optional[np.ndarray] = None  # Kalibriertes Umgebungsrauschen (1D int16)

        if api_key and api_base:
            try:
                self._whisper = OpenAI(api_key=api_key, base_url=api_base)
                logger.info("Whisper STT aktiv (Groq).")
            except Exception as e:
                logger.warning(f"Whisper-Client konnte nicht erstellt werden: {e}")

        self._calibrate()

    # ------------------------------------------------------------------
    # Kalibrierung
    # ------------------------------------------------------------------

    def _calibrate(self, duration: float = 1.5):
        """Kalibriert die Erkennungsschwelle anhand des aktuellen Umgebungsgeräuschpegels.
        Adaptive Marge: bei hohem Ambient-Pegel kleinere Marge, damit Stimme noch erkannt wird.
        Absolute Obergrenze: 63 dB — verhindert dass Musik/Hintergrund die Erkennung blockiert.
        BUG-097: Wenn das konfigurierte Gerät 0 dB liefert (falsches/stummes Gerät),
        werden automatisch alle verfügbaren Eingabegeräte als Fallback versucht.
        """
        logger.info("Kalibriere Mikrofon...")
        devices_to_try = [self._device]
        if self._device is not None:
            devices_to_try.append(None)
        # Fallback-Pool: alle verfügbaren Eingabegeräte (wird nur genutzt wenn Standard 0dB liefert)
        _all_input_devices: list = []
        try:
            for i, dev in enumerate(sd.query_devices()):
                if dev.get("max_input_channels", 0) > 0:
                    _all_input_devices.append(i)
        except Exception:
            pass

        for device in devices_to_try:
            try:
                samples = sd.rec(
                    int(duration * _SAMPLERATE),
                    samplerate=_SAMPLERATE,
                    channels=_CHANNELS,
                    dtype=_DTYPE,
                    device=device,
                )
                sd.wait()
                ambient = _rms_db(samples)

                # Rauschprofil für Geräuschfilter speichern (1D int16)
                self._noise_samples = samples.reshape(-1).copy()

                # Adaptive Marge: je lauter die Umgebung, desto kleiner die Marge
                # So bleibt die Schwelle immer erreichbar für eine normale Stimme
                if ambient < 30:
                    margin = 18
                elif ambient < 40:
                    margin = 14
                elif ambient < 50:
                    margin = 10
                else:
                    margin = 6    # sehr laute Umgebung → kleine Marge

                self._threshold = min(ambient + margin, 63.0)  # Obergrenze 63 dB

                if device != self._device:
                    logger.warning("Fallback auf Standard-Gerät beim Kalibrieren.")
                    self._device = device
                if ambient < 10:
                    logger.warning(
                        f"Mikrofon liefert kaum Signal ({ambient:.1f} dB) — "
                        f"falsches Gerät oder Mikrofon stumm geschaltet?"
                    )
                    # BUG-097: 0 dB deutet auf falsches Default-Gerät hin.
                    # Alle anderen Eingabegeräte als Fallback durchprobieren.
                    for fallback_dev in _all_input_devices:
                        if fallback_dev == device:
                            continue
                        try:
                            fb_samples = sd.rec(
                                int(duration * _SAMPLERATE),
                                samplerate=_SAMPLERATE,
                                channels=_CHANNELS,
                                dtype=_DTYPE,
                                device=fallback_dev,
                            )
                            sd.wait()
                            fb_ambient = _rms_db(fb_samples)
                            if fb_ambient >= 10:
                                logger.info(
                                    f"Besseres Mikrofon gefunden: Gerät {fallback_dev} "
                                    f"({fb_ambient:.1f} dB) — wechsle automatisch."
                                )
                                self._device = fallback_dev
                                self._noise_samples = fb_samples.reshape(-1).copy()
                                ambient = fb_ambient
                                if ambient < 30:
                                    margin = 18
                                elif ambient < 40:
                                    margin = 14
                                elif ambient < 50:
                                    margin = 10
                                else:
                                    margin = 6
                                self._threshold = min(ambient + margin, 63.0)
                                logger.info(
                                    f"Mikrofon bereit (Auto-Gerät {fallback_dev}) — "
                                    f"Umgebung: {ambient:.1f} dB, Schwelle: {self._threshold:.1f} dB"
                                )
                                return
                        except Exception as _fb_err:
                            logger.debug(f"Fallback-Gerät {fallback_dev}: {_fb_err}")
                if ambient > 50:
                    logger.warning(
                        f"Hoher Umgebungspegel ({ambient:.1f} dB) — "
                        f"Hintergrundgeräusche können die Erkennung erschweren."
                    )
                logger.info(
                    f"Mikrofon bereit — Umgebung: {ambient:.1f} dB, "
                    f"Schwelle: {self._threshold:.1f} dB"
                )
                return
            except Exception as e:
                logger.warning(f"Kalibrierung mit Gerät {device} fehlgeschlagen: {e}")

        logger.error("Mikrofon-Kalibrierung vollständig fehlgeschlagen.")
        raise OSError("Kein Mikrofon verfügbar.")

    def recalibrate(self):
        """Öffentliche Methode — Neukalibrierung von außen auslösbar (z.B. Tray-Menü)."""
        try:
            self._calibrate()
        except OSError as e:
            logger.error(f"Neukalibrierung fehlgeschlagen: {e}")

    def _try_recover_device(self) -> bool:
        """Versucht das Audio-Gerät neu zu initialisieren. Gibt True zurück wenn erfolgreich."""
        logger.info("Versuche Audio-Gerät neu zu initialisieren...")
        time.sleep(1.0)
        try:
            self._calibrate()
            logger.info("Audio-Gerät erfolgreich wiederhergestellt.")
            return True
        except Exception as e:
            logger.error(f"Audio-Gerät Wiederherstellung fehlgeschlagen: {e}")
            return False

    # ------------------------------------------------------------------
    # Oeffentliche API
    # ------------------------------------------------------------------

    def listen_once(
        self,
        timeout: int = 7,
        phrase_time_limit: int = 20,
        stop_event: Optional[threading.Event] = None,
    ) -> Optional[str]:
        audio = self._record(
            max_wait=float(timeout),
            max_duration=float(phrase_time_limit),
            stop_event=stop_event,
        )
        self._last_audio = audio   # Für externe Analyse (Emotion, Speaker) speichern
        if stop_event and stop_event.is_set():
            return None
        return self._transcribe(audio) if audio else None

    def listen_in_background(self, callback: Callable[[str], None]) -> Callable:
        stop_event = threading.Event()

        def _loop():
            consecutive_errors = 0
            while not stop_event.is_set():
                try:
                    audio = self._record(
                        max_wait=None,
                        max_duration=6.0,
                        stop_event=stop_event,
                    )
                    # BUG-NEW-7: consecutive_errors wurde auf 0 zurückgesetzt
                    # direkt nach _record() — BEVOR geprüft wurde ob audio None ist.
                    # Wenn das Mikrofon stummgeschaltet oder defekt ist, gibt
                    # _record() None zurück (keine Exception, nur kein Signal).
                    # consecutive_errors wurde dann trotzdem genullt, der
                    # Recovery-Mechanismus (>= 3 Fehler) wurde nie ausgelöst,
                    # und Jarvis lauschte stumm ins Nichts ohne je zu versuchen
                    # ein anderes Gerät zu nutzen.
                    # Fix: nur zurücksetzen wenn tatsächlich Audio empfangen wurde.
                    if audio and not stop_event.is_set():
                        consecutive_errors = 0
                        self._last_audio = audio   # Für Emotion/Speaker-Analyse
                        text = self._transcribe(audio)
                        if text:
                            callback(text)
                except Exception as e:
                    consecutive_errors += 1
                    logger.warning(f"BG-Listen Fehler #{consecutive_errors}: {e}")
                    if consecutive_errors >= 3:
                        logger.warning("Audio-Gerät ausgefallen, versuche Neustart...")
                        if self._try_recover_device():
                            consecutive_errors = 0
                        else:
                            # Recovery fehlgeschlagen — kurz warten, dann
                            # consecutive_errors zurücksetzen um Busy-Loop zu vermeiden
                            time.sleep(2.0)
                            consecutive_errors = 0

        t = threading.Thread(target=_loop, daemon=True)
        t.start()
        logger.info("Hintergrundlauschen gestartet.")

        def _stop(wait_for_stop: bool = False):
            stop_event.set()
            if wait_for_stop:
                t.join(timeout=2.0)  # BUG-020: 500ms zu kurz, Loop braucht bis zu 2s

        return _stop

    # ------------------------------------------------------------------
    # Aufnahme (VAD)
    # ------------------------------------------------------------------

    def _record(
        self,
        max_wait: Optional[float],
        max_duration: float,
        stop_event: Optional[threading.Event] = None,
    ) -> Optional[bytes]:
        """Nimmt Audio auf und gibt WAV-Bytes zurück.
        Bei PortAudio-Fehler wird automatisch das Standard-Gerät versucht.
        """
        silence_limit = int(_PHRASE_PAUSE / _CHUNK_S)
        total_chunks  = int(max_duration  / _CHUNK_S)
        start_time    = time.monotonic()

        # Zuerst konfiguriertes Gerät, dann Standard-Gerät als Fallback
        devices_to_try = [self._device]
        if self._device is not None:
            devices_to_try.append(None)

        last_error = None
        for device in devices_to_try:
            try:
                result = self._record_stream(
                    device, max_wait, silence_limit, total_chunks,
                    stop_event, start_time,
                )
                return result
            except Exception as e:
                last_error = e
                if device is not None:
                    logger.warning(
                        f"Aufnahme-Fehler mit Gerät {device}, "
                        f"versuche Standard-Gerät: {e}"
                    )
                    time.sleep(0.3)

        logger.error(f"Aufnahme-Fehler: {last_error}")
        return None

    def _record_stream(
        self,
        device,
        max_wait: Optional[float],
        silence_limit: int,
        total_chunks: int,
        stop_event: Optional[threading.Event],
        start_time: float,
    ) -> Optional[bytes]:
        """Öffnet einen InputStream und nimmt auf. Wirft bei Fehler eine Exception."""
        frames: list[np.ndarray] = []
        speech_started  = False
        silence_count   = 0
        speech_chunks   = 0   # Für Smart-Pause: Sprechdauer messen
        speech_dbs: list[float] = []   # Für Flüstermodus: dB-Werte der Sprachframes

        with sd.InputStream(
            samplerate=_SAMPLERATE,
            channels=_CHANNELS,
            dtype=_DTYPE,
            blocksize=_CHUNK_SIZE,
            device=device,
        ) as stream:
            for _ in range(total_chunks):
                if stop_event and stop_event.is_set():
                    return None
                chunk, _ = stream.read(_CHUNK_SIZE)
                db = _rms_db(chunk)

                if db >= self._threshold:
                    speech_started = True
                    silence_count  = 0
                    speech_chunks += 1
                    speech_dbs.append(db)
                    frames.append(chunk.copy())

                elif speech_started:
                    frames.append(chunk.copy())
                    silence_count += 1

                    # Smart-Pause: adaptiver Pausen-Limit je nach Sprechdauer
                    if self.smart_pause:
                        speech_s     = speech_chunks * _CHUNK_S
                        if speech_s < 0.8:
                            adaptive_pause = _SMART_PAUSE_SHORT
                        elif speech_s > 2.5:
                            adaptive_pause = _SMART_PAUSE_LONG
                        else:
                            adaptive_pause = _PHRASE_PAUSE
                        effective_limit = max(1, int(adaptive_pause / _CHUNK_S))
                    else:
                        effective_limit = silence_limit

                    if silence_count >= effective_limit:
                        break

                else:
                    if max_wait is not None:
                        if time.monotonic() - start_time > max_wait:
                            return None

        if not speech_started or len(frames) < 2:
            return None

        # Flüstermodus: war die mittlere Sprachlautstärke nah am Schwellenwert?
        # Normale Sprache liegt 10–20 dB über dem Threshold; Flüstern kaum darüber.
        if speech_dbs:
            mean_speech_db = sum(speech_dbs) / len(speech_dbs)
            self._was_whisper = mean_speech_db < (self._threshold + 9.0)
        else:
            self._was_whisper = False

        all_samples = np.concatenate(frames)

        # Geräuschfilter: spektrale Subtraktion mit dem Umgebungsrausch-Profil
        if self.noise_filter and self._noise_samples is not None:
            try:
                flat     = all_samples.reshape(-1)            # 2D → 1D
                denoised = _denoise_spectral(flat, self._noise_samples)
                all_samples = denoised.reshape(all_samples.shape)
            except Exception as _denoise_err:
                logger.debug(f"Geräuschfilter übersprungen: {_denoise_err}")
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(_CHANNELS)
            wf.setsampwidth(2)
            wf.setframerate(_SAMPLERATE)
            wf.writeframes(all_samples.tobytes())
        return buf.getvalue()

    # ------------------------------------------------------------------
    # Transkription
    # ------------------------------------------------------------------

    def _transcribe(self, wav_bytes: bytes) -> Optional[str]:
        if self._whisper:
            return self._transcribe_whisper(wav_bytes)
        return self._transcribe_google(wav_bytes)

    def _transcribe_whisper(self, wav_bytes: bytes) -> Optional[str]:
        """Groq Whisper API — schnell, kein Rate-Limit."""
        try:
            buf = io.BytesIO(wav_bytes)
            buf.name = "audio.wav"
            result = self._whisper.audio.transcriptions.create(
                model="whisper-large-v3-turbo",
                file=buf,
                language=self._lang_short,
                response_format="text",
            )
            text = result.strip() if isinstance(result, str) else (result.text or "").strip()
            if not text:
                return None
            logger.debug(f"Whisper erkannt: '{text}'")
            return text.lower().strip()
        except Exception as e:
            logger.warning(f"Whisper Fehler, nutze Google Fallback: {e}")
            return self._transcribe_google(wav_bytes)

    def _transcribe_google(self, wav_bytes: bytes) -> Optional[str]:
        """Google STT — Fallback."""
        try:
            with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
                frames    = wf.readframes(wf.getnframes())
                rate      = wf.getframerate()
                sampwidth = wf.getsampwidth()

            audio = sr.AudioData(frames, rate, sampwidth)
            text  = self._sr.recognize_google(audio, language=self.language)
            logger.debug(f"Google erkannt: '{text}'")
            return text.lower().strip()

        except sr.UnknownValueError:
            return None
        except sr.RequestError as e:
            logger.error(f"Google STT Verbindungsfehler: {e}")
            return None
        except Exception as e:
            logger.error(f"Transkription Fehler: {e}")
            return None
