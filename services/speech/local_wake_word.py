"""
Lokaler Wake-Word-Detektor — erkennt "Hey Jarvis" vollständig offline.

Backend : openwakeword  (pip install openwakeword)
Modell  : hey_jarvis   (ONNX, ~5 MB, wird beim ersten Start automatisch geladen)

Vorteile:
  ✓ 100 % offline — kein API-Aufruf, keine Latenz
  ✓ < 2 % CPU-Last
  ✓ Modell-Dateien werden via openwakeword automatisch von HuggingFace geladen

Einschränkung:
  openwakeword muss installiert sein:  pip install openwakeword onnxruntime
"""
import logging
import threading
from typing import Callable, Optional

import numpy as np
import sounddevice as sd


logger = logging.getLogger("jarvis.local_wakeword")

# ── Konstanten ────────────────────────────────────────────────────────────────
_SAMPLERATE  = 16_000   # Hz — openwakeword-Vorgabe
_CHUNK_SIZE  = 1_280    # 80 ms @ 16 kHz — openwakeword-Framegröße
_THRESHOLD   = 0.4      # Erkennungs-Score 0..1 (niedriger = empfindlicher)
_COOLDOWN_S  = 2.0      # Sekunden Pause nach Erkennung (verhindert Doppel-Trigger)


class LocalWakeWordDetector:
    """
    Erkennt das Wake-Word "Hey Jarvis" lokal mit openwakeword (ONNX).
    Benötigt kein Internet. Verbraucht < 2 % CPU.

    Nutzung:
        detector = LocalWakeWordDetector()
        stop_fn  = detector.start(callback=lambda: print("Wake-Word!"))
        # ...
        stop_fn(wait_for_stop=True)

    Raises:
        ImportError  — openwakeword nicht installiert
        RuntimeError — Modell konnte nicht geladen werden
    """

    def __init__(
        self,
        device:    Optional[int] = None,
        threshold: float = _THRESHOLD,
    ):
        self._device      = device
        self._threshold   = threshold
        self._model       = None
        self._stop_ev     = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._in_cooldown = threading.Event()   # thread-safe cooldown flag

        self._load_model()

    # ── Modell laden ──────────────────────────────────────────────────────────

    def _load_model(self):
        """Importiert openwakeword und lädt das hey_jarvis ONNX-Modell."""
        try:
            from openwakeword.model import Model as _OWW
        except ImportError as exc:
            raise ImportError(
                "openwakeword ist nicht installiert.\n"
                "Bitte ausführen:  pip install openwakeword onnxruntime"
            ) from exc

        logger.info("Lade lokales Wake-Word Modell (hey_jarvis) …")
        try:
            self._model = _OWW(
                wakeword_models=["hey_jarvis"],
                inference_framework="onnx",
            )
            logger.info("Lokales Wake-Word Modell geladen.")
        except Exception as exc:
            raise RuntimeError(
                f"hey_jarvis Modell konnte nicht geladen werden: {exc}\n"
                "Prüfe: pip install openwakeword onnxruntime"
            ) from exc

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self, callback: Callable[[], None]) -> Callable:
        """
        Startet den Hintergrund-Audio-Listener.

        callback() wird aufgerufen wenn das Wake-Word erkannt wurde.
        Gibt eine stop(wait_for_stop=False)-Funktion zurück.
        """
        # Erstelle ein neues Event pro Aufruf damit ein ggf. noch laufender
        # alter Thread (bei doppeltem start()-Aufruf) unabhängig gestoppt werden
        # kann, ohne dass ein shared self._stop_ev.clear() das alte Stop-Signal
        # des vorigen Threads aufhebt.
        stop_ev = threading.Event()
        self._in_cooldown.clear()

        thread = threading.Thread(
            target=self._loop,
            args=(callback, stop_ev),
            daemon=True,
            name="LocalWakeWord",
        )
        self._thread = thread
        thread.start()
        logger.info("Lokaler Wake-Word Listener gestartet.")

        def _stop(wait_for_stop: bool = False):
            stop_ev.set()
            if wait_for_stop and thread:
                thread.join(timeout=2.0)

        return _stop

    # ── Interner Audio-Loop ───────────────────────────────────────────────────

    def _loop(self, callback: Callable[[], None], stop_ev: threading.Event):
        """
        Liest kontinuierlich 80-ms-Chunks vom Mikrofon und füttert
        openwakeword. Ruft callback() auf sobald Score ≥ Threshold.
        stop_ev ist ein pro-Aufruf-Event — verhindert Interferenz bei
        erneutem start()-Aufruf ohne vorherigen stop().
        """
        try:
            with sd.InputStream(
                samplerate=_SAMPLERATE,
                channels=1,
                dtype="float32",
                blocksize=_CHUNK_SIZE,
                device=self._device,
            ) as stream:
                logger.debug("LocalWakeWord Audio-Stream geöffnet.")

                while not stop_ev.is_set():
                    # Audio-Chunk lesen
                    try:
                        chunk, _ = stream.read(_CHUNK_SIZE)
                    except Exception as exc:
                        logger.warning(f"Stream-Lesefehler: {exc}")
                        stop_ev.wait(0.3)
                        continue

                    # float32 [-1, 1] → int16 [-32768, 32767]
                    audio = (chunk[:, 0] * 32_768.0).astype(np.int16)

                    # openwakeword-Vorhersage
                    try:
                        scores: dict = self._model.predict(audio)
                    except Exception as exc:
                        logger.debug(f"Vorhersage-Fehler: {exc}")
                        continue

                    # Cooldown-Check (verhindert Doppel-Trigger)
                    if self._in_cooldown.is_set():
                        continue

                    # Besten Score finden
                    if not scores:
                        continue
                    best_key   = max(scores, key=scores.get)
                    best_score = scores[best_key]

                    if best_score >= self._threshold:
                        logger.info(
                            f"Wake-Word erkannt: '{best_key}' "
                            f"(Score {best_score:.2f} ≥ {self._threshold})"
                        )
                        # Modell-Zustand zurücksetzen → kein Doppel-Trigger
                        self._model.reset()
                        self._start_cooldown()

                        # Callback in eigenem Daemon-Thread
                        threading.Thread(
                            target=callback, daemon=True
                        ).start()

        except Exception as exc:
            logger.error(f"LocalWakeWord Loop-Fehler: {exc}")
        finally:
            logger.info("Lokaler Wake-Word Listener gestoppt.")

    def _start_cooldown(self):
        """Setzt _in_cooldown für _COOLDOWN_S Sekunden."""
        self._in_cooldown.set()

        def _clear():
            self._in_cooldown.clear()

        t = threading.Timer(_COOLDOWN_S, _clear)
        t.daemon = True
        t.start()
