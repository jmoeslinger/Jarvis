import asyncio
import io
import logging
import queue
import re
import threading
from typing import Callable, Optional

import sounddevice as sd
import soundfile as sf
import edge_tts
import pyttsx3

logger = logging.getLogger("jarvis.synthesizer")

# Regex für Satz-Grenzen
_SENTENCE_RE = re.compile(r'(?<=[.!?])\s+')


class SentencePipeline:
    """Streaming TTS-Pipeline: nimmt Text-Chunks an, synthetisiert und spielt satzweise ab.

    Architektur: zwei Queues, zwei dedizierte Threads.
      _synth_queue  → _synth_thread  → _audio_queue  → _player_thread → Lautsprecher

    Die Synthese läuft SEQUENZIELL (ein Satz nach dem anderen), weil parallele
    Synth-Threads die Sätze in zufälliger Reihenfolge in die Audio-Queue einreihen
    würden — kürzere Sätze kämen vor längeren an und würden falsch abgespielt.
    Da edge-tts deutlich schneller ist als die Echtzeit-Wiedergabe, entsteht
    durch die sequenzielle Synthese keine hörbare Lücke zwischen den Sätzen.
    """

    def __init__(self, synthesizer: "SpeechSynthesizer"):
        self._synth = synthesizer
        self._buffer = ""
        self._synth_queue: queue.Queue = queue.Queue()   # Sätze → Synth-Thread
        self._audio_queue: queue.Queue = queue.Queue()   # fertige Audio → Player-Thread
        self._stopped = False
        self._done = threading.Event()

        # Synth-Thread: verarbeitet Sätze sequenziell, erhält Reihenfolge
        self._synth_thread = threading.Thread(
            target=self._synth_loop, daemon=True, name="tts-synth"
        )
        self._synth_thread.start()

        # Player-Thread: spielt fertige Audio-Daten in Ankunftsreihenfolge ab
        self._player_thread = threading.Thread(
            target=self._player_loop, daemon=True, name="tts-player"
        )
        self._player_thread.start()

    def feed(self, chunk: str):
        """Empfängt einen neuen Text-Chunk und reiht abgeschlossene Sätze ein."""
        if self._stopped:
            return
        self._buffer += chunk
        parts = _SENTENCE_RE.split(self._buffer)
        # Letzter Teil ist möglicherweise unvollständig
        if len(parts) > 1:
            complete_sentences = parts[:-1]
            self._buffer = parts[-1]
            for sentence in complete_sentences:
                sentence = sentence.strip()
                if sentence:
                    self._synth_queue.put(sentence)

    def finish(self):
        """Flusht verbleibenden Puffer und wartet bis alles abgespielt wurde."""
        remaining = self._buffer.strip()
        if remaining and not self._stopped:
            self._synth_queue.put(remaining)
        self._buffer = ""

        # Sentinel → Synth-Thread beendet sich nach Abarbeitung aller Sätze
        self._synth_queue.put(None)
        # Warten bis Synth-Thread fertig ist (er schickt dann Sentinel an Player)
        self._synth_thread.join(timeout=60)
        self._done.wait(timeout=30)

    def stop(self):
        """Bricht alles sofort ab."""
        self._stopped = True
        # Synth-Thread aufwecken und beenden
        self._synth_queue.put(None)
        # Audio-Queue leeren
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break
        # Sentinel damit Player-Thread aufwacht und beendet
        self._audio_queue.put(None)
        try:
            sd.stop()
        except Exception:
            pass
        self._done.set()

    # ------------------------------------------------------------------
    # Interne Methoden
    # ------------------------------------------------------------------

    def _synth_loop(self):
        """Sequenzieller Synthese-Thread: verarbeitet Sätze in Reihenfolge."""
        while True:
            try:
                sentence = self._synth_queue.get(timeout=60)
            except queue.Empty:
                break

            if sentence is None:
                # Sentinel — alle Sätze verarbeitet
                break

            if self._stopped:
                continue   # Queue leeren, aber nichts mehr synthetisieren

            try:
                audio = self._synth._prepare_edge_tts(sentence)
                if not self._stopped:
                    self._audio_queue.put(audio)
            except Exception as e:
                logger.warning(f"SentencePipeline Synth-Fehler: {e}")
                if not self._stopped:
                    self._audio_queue.put(("fallback", sentence))

        # Synth-Thread fertig → Sentinel an Player-Thread schicken
        if not self._stopped:
            self._audio_queue.put(None)

    def _player_loop(self):
        """Spielt Audio-Daten der Reihe nach ab."""
        while True:
            try:
                item = self._audio_queue.get(timeout=60)
            except queue.Empty:
                break

            if item is None:
                # Sentinel — fertig
                break

            if self._stopped:
                continue

            try:
                # item ist entweder ("fallback", text) oder (numpy_array, samplerate)
                # isinstance(item[0], str) unterscheidet die beiden Fälle sicher
                if isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str):
                    # pyttsx3 Fallback
                    self._synth._speak_fallback(item[1])
                else:
                    data, samplerate = item
                    if not self._stopped:
                        sd.play(data, samplerate)
                        sd.wait()
            except Exception as e:
                logger.warning(f"SentencePipeline Player-Fehler: {e}")

        self._done.set()


class SpeechSynthesizer:
    def __init__(self, voice: str = "de-DE-FlorianMultilingualNeural", rate: str = "+0%"):
        self.voice = voice
        self.rate  = rate
        self._speaking = False
        self._stopped  = False
        self._lock     = threading.Lock()
        self._fallback: Optional[pyttsx3.Engine] = None
        self._active_pipeline: Optional[SentencePipeline] = None

        # Persistenter asyncio-Loop in eigenem Thread — kein Neuerstellen pro Aufruf
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._loop.run_forever, daemon=True, name="tts-loop"
        )
        self._loop_thread.start()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def prepare(self, text: str):
        """Synthetisiert Audio und gibt (data, samplerate) zurück — noch kein Ton."""
        with self._lock:
            self._speaking = True
            self._stopped  = False
            try:
                return self._prepare_edge_tts(text)
            except Exception as e:
                logger.warning(f"edge-tts Fehler beim Vorbereiten: {e}")
                return None  # Fallback wird in play() behandelt

    def play(self, audio, text: str = ""):
        """Spielt vorbereitetes Audio sofort ab. Falls None → SAPI-Fallback."""
        try:
            if self._stopped:
                return
            if audio is not None:
                data, samplerate = audio
                if not self._stopped:
                    sd.play(data, samplerate)
                    sd.wait()
            else:
                if not self._stopped:
                    self._speak_fallback(text)
        finally:
            self._speaking = False

    def speak(self, text: str):
        """Komfort-Methode: prepare + play in einem Aufruf."""
        audio = self.prepare(text)   # prepare() setzt _stopped=False und _speaking=True
        self.play(audio, text)

    def speak_async(self, text: str) -> threading.Thread:
        t = threading.Thread(target=self.speak, args=(text,), daemon=True)
        t.start()
        return t

    def create_pipeline(self) -> SentencePipeline:
        """Erstellt eine neue SentencePipeline für Streaming-TTS."""
        with self._lock:
            self._stopped = False
            self._speaking = True
            pipeline = SentencePipeline(self)
            self._active_pipeline = pipeline
        return pipeline

    def stop(self):
        """Stoppt sofort jede laufende Sprachausgabe."""
        with self._lock:
            self._stopped  = True
            self._speaking = False
            pipeline = self._active_pipeline
            self._active_pipeline = None
        try:
            sd.stop()
        except Exception:
            pass
        if pipeline:
            pipeline.stop()

    def reset_pipeline(self):
        """BUG-001: Setzt Pipeline-State sicher zurueck ohne externen Lock-Zugriff."""
        with self._lock:
            self._active_pipeline = None
            self._speaking = False

    # ------------------------------------------------------------------
    # edge-tts (online, hochwertig) — alles im Speicher, kein Temp-File
    # ------------------------------------------------------------------

    async def _fetch_audio(self, text: str) -> bytes:
        """Lädt TTS-Audio als MP3-Bytes (In-Memory, kein Disk-IO)."""
        comm = edge_tts.Communicate(text, self.voice, rate=self.rate)
        chunks = []
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                chunks.append(chunk["data"])
        return b"".join(chunks)

    def _prepare_edge_tts(self, text: str):
        """Netzwerkanfrage + Dekodierung — gibt (data, samplerate) zurück."""
        future = asyncio.run_coroutine_threadsafe(self._fetch_audio(text), self._loop)
        mp3_data = future.result(timeout=30)

        if not mp3_data:
            raise RuntimeError("edge-tts lieferte keine Audiodaten")

        buf = io.BytesIO(mp3_data)
        data, samplerate = sf.read(buf, dtype="float32")
        return data, samplerate

    # ------------------------------------------------------------------
    # pyttsx3 (offline SAPI, Fallback)
    # ------------------------------------------------------------------

    def _get_fallback(self) -> pyttsx3.Engine:
        # BUG-032: Initialisierung des Fallback-Engines unter Lock schuetzen
        with self._lock:
            if self._fallback is None:
                self._fallback = pyttsx3.init()
                self._fallback.setProperty("rate", 175)
                for v in self._fallback.getProperty("voices"):
                    if "german" in v.name.lower() or "de_" in v.id.lower():
                        self._fallback.setProperty("voice", v.id)
                        break
            return self._fallback

    def _speak_fallback(self, text: str):
        try:
            engine = self._get_fallback()
            engine.say(text)
            engine.runAndWait()
        except Exception as e:
            logger.error(f"SAPI-Fallback Fehler: {e}")
