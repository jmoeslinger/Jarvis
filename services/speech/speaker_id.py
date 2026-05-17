"""
Sprechererkennung — stellt sicher dass nur der eingerichtete Benutzer gehört wird.

Backend : resemblyzer  (pip install resemblyzer)
          d-Vektor Embeddings + Cosine-Similarity

Ablauf:
  1. Einmalige Einrichtung: SpeakerIdentifier.enroll(wav_bytes)
     → Stimm-Abdruck wird in %APPDATA%/Jarvis/speaker.npy gespeichert
  2. Bei jedem Befehl: SpeakerIdentifier.is_known_speaker(wav_bytes)
     → True wenn Similarity ≥ Schwelle (Standard: 0.72)

Wichtig:
  - Ohne Enrollment akzeptiert die Klasse alle Sprecher (Fail-Open)
  - Bei fehlendem resemblyzer → ImportError mit Installationshinweis
"""
import io
import logging
import os
import wave
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger("jarvis.speaker_id")

_APPDATA    = Path(os.environ.get("APPDATA", str(Path.home()))) / "Jarvis"
_EMBED_FILE = _APPDATA / "speaker.npy"
_THRESHOLD  = 0.72   # Cosine-Similarity — empfindlicher als 0.75 für Alltagssprache


class SpeakerIdentifier:
    """
    Erkennt ob der sprechende Benutzer dem eingerichteten Profil entspricht.

    Installation: pip install resemblyzer

    Nutzung:
        sid  = SpeakerIdentifier()
        ok   = sid.enroll(wav_bytes_5s)  # Einmalig einrichten
        bool = sid.is_known_speaker(wav_bytes)  # Bei jedem Befehl

    Raises:
        ImportError  — resemblyzer nicht installiert
        RuntimeError — Encoder konnte nicht geladen werden
    """

    def __init__(self, threshold: float = _THRESHOLD):
        self._threshold  = threshold
        self._encoder    = None
        self._embedding: Optional[np.ndarray] = None

        self._load_encoder()
        self._load_saved_embedding()

    # ── Initialisierung ───────────────────────────────────────────────────────

    def _load_encoder(self):
        try:
            from resemblyzer import VoiceEncoder
            self._encoder = VoiceEncoder()
            logger.info("SpeakerIdentifier: VoiceEncoder geladen.")
        except ImportError as exc:
            raise ImportError(
                "resemblyzer nicht installiert.\n"
                "Bitte ausführen:  pip install resemblyzer"
            ) from exc
        except Exception as exc:
            raise RuntimeError(
                f"VoiceEncoder Ladefehler: {exc}"
            ) from exc

    def _load_saved_embedding(self):
        """Lädt gespeicherten Stimm-Abdruck (falls vorhanden)."""
        if _EMBED_FILE.exists():
            try:
                self._embedding = np.load(str(_EMBED_FILE))
                logger.info("Stimm-Abdruck aus Datei geladen.")
            except Exception as exc:
                logger.warning(f"Stimm-Abdruck laden fehlgeschlagen: {exc}")

    # ── Public API ────────────────────────────────────────────────────────────

    def has_enrollment(self) -> bool:
        """True wenn ein Stimm-Abdruck vorhanden ist."""
        return self._embedding is not None

    def enroll(self, wav_bytes: bytes) -> bool:
        """
        Erstellt einen Stimm-Abdruck aus WAV-Audio und speichert ihn.

        wav_bytes : WAV-Audio (mind. 3 Sekunden, besser 5-10 Sekunden)
        Gibt True zurück wenn erfolgreich, False bei Fehler.
        """
        if not wav_bytes:
            return False
        try:
            samples = self._wav_to_float(wav_bytes)
            if samples is None or len(samples) < 16000:
                logger.warning("Enrollment: Aufnahme zu kurz (< 1 Sekunde)")
                return False

            embed = self._encoder.embed_utterance(samples)
            self._embedding = embed

            _APPDATA.mkdir(parents=True, exist_ok=True)
            np.save(str(_EMBED_FILE), embed)
            logger.info(f"Stimm-Abdruck erstellt und gespeichert → {_EMBED_FILE}")
            return True

        except Exception as exc:
            logger.error(f"Enrollment fehlgeschlagen: {exc}")
            return False

    def delete_enrollment(self):
        """Löscht den gespeicherten Stimm-Abdruck."""
        self._embedding = None
        try:
            _EMBED_FILE.unlink(missing_ok=True)
            logger.info("Stimm-Abdruck gelöscht.")
        except Exception as exc:
            logger.warning(f"Stimm-Abdruck löschen: {exc}")

    def is_known_speaker(self, wav_bytes: bytes) -> bool:
        """
        Prüft ob die sprechende Person dem eingerichteten Benutzer entspricht.

        Gibt True zurück wenn:
          - Kein Enrollment vorhanden (Fail-Open — akzeptiert alle)
          - Similarity ≥ self._threshold
          - Audio zu kurz für sinnvollen Vergleich
          - Fehler aufgetreten (Fail-Open)
        """
        if self._embedding is None:
            return True   # Kein Profil → alle akzeptieren

        if not wav_bytes:
            return True

        try:
            samples = self._wav_to_float(wav_bytes)
            if samples is None or len(samples) < 8000:
                return True   # < 0.5s → zu kurz für Vergleich

            embed = self._encoder.embed_utterance(samples)

            # Cosine-Similarity
            norm_ref  = np.linalg.norm(self._embedding)
            norm_cur  = np.linalg.norm(embed)
            if norm_ref < 1e-9 or norm_cur < 1e-9:
                return True

            sim = float(np.dot(self._embedding, embed) / (norm_ref * norm_cur))
            logger.debug(
                f"Speaker-Similarity: {sim:.3f} "
                f"(Schwelle: {self._threshold}) → {'OK' if sim >= self._threshold else 'ABGELEHNT'}"
            )
            return sim >= self._threshold

        except Exception as exc:
            logger.debug(f"Speaker-Check Fehler: {exc}")
            return True   # Bei Fehler → Fail-Open

    # ── Hilfsmethoden ─────────────────────────────────────────────────────────

    @staticmethod
    def _wav_to_float(wav_bytes: bytes) -> Optional[np.ndarray]:
        """Konvertiert WAV-Bytes zu float32 Array [-1, 1]."""
        try:
            with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
                raw = wf.readframes(wf.getnframes())
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32_768.0
            return samples
        except Exception:
            return None
