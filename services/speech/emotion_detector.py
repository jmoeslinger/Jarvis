"""
Emotionserkennung aus Sprach-Audio — rein lokal, kein KI-Modell nötig.

Analyse basiert auf Audio-Features (nur numpy):
  • RMS-Energie       → Lautstärke / Aktivierungsgrad
  • Zero-Crossing-Rate → Sprach-Anspannung / Stimmschärfe
  • Energie-Varianz   → Dynamik und Ausdrucksstärke der Stimme

Erkannte Zustände:
  neutral   → Normale Stimme, kein besonderer Hinweis
  aufgeregt → Hohe Energie, hohe Dynamik (Begeisterung / Aufregung)
  angespannt → Hohe Energie, ruhige Dynamik (Druck / Ungeduld)
  gestresst → Niedrige Energie, hohe ZCR (Anspannung / Unsicherheit)
  ruhig     → Niedrige Energie, wenig Dynamik (Entspannung / Erschöpfung)
"""
import io
import logging
import wave

import numpy as np

logger = logging.getLogger("jarvis.emotion")

# Schwellen (empirisch kalibriert für Sprache bei 16 kHz, int16)
_ENERGY_HIGH_NORM  = 0.40   # rel. RMS: aufgeregt / angespannt
_ENERGY_LOW_NORM   = 0.18   # rel. RMS: ruhig / gestresst
_ZCR_HIGH          = 0.11   # Zero-Crossing-Rate: angespannte Stimme
_VAR_HIGH          = 300.0  # Energie-Varianz: dynamische Stimme
_MIN_SAMPLES       = 1600   # 100 ms @ 16 kHz — unter diesem Wert kein Urteil


def detect_emotion(wav_bytes: bytes) -> dict:
    """
    Analysiert WAV-Audio und schätzt die emotionale Stimmung.

    Parameters
    ----------
    wav_bytes : bytes
        WAV-Audio (16-Bit PCM, beliebige Sample-Rate, mono)

    Returns
    -------
    dict mit:
        label  : str   — "neutral" | "aufgeregt" | "angespannt" |
                         "gestresst" | "ruhig"
        hint   : str   — Natürlichsprachlicher Hinweis für den KI-Prompt
                         (leer wenn neutral)
        energy : float — Relative Energie 0.0 … 1.0
    """
    _neutral = {"label": "neutral", "hint": "", "energy": 0.5}

    if not wav_bytes:
        return _neutral

    try:
        # ── WAV → numpy ───────────────────────────────────────────────────────
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            raw        = wf.readframes(wf.getnframes())
            samplerate = wf.getframerate()

        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)

        if len(samples) < _MIN_SAMPLES:
            return _neutral

        # ── Feature 1: Normierte RMS-Energie ─────────────────────────────────
        rms        = float(np.sqrt(np.mean(samples ** 2)))
        energy_norm = min(rms / 7_000.0, 1.0)   # 7000 ≈ normale Sprechlautstärke

        # ── Feature 2: Zero-Crossing-Rate ────────────────────────────────────
        # Hohe ZCR → reibungsreiche, angespannte Stimme
        signs = np.sign(samples)
        zcr   = float(np.sum(np.abs(np.diff(signs))) / (2 * len(samples)))

        # ── Feature 3: Energie-Varianz (Ausdrucksdynamik) ────────────────────
        frame_size = max(1, int(samplerate * 0.05))   # 50 ms Frames
        n_f        = len(samples) // frame_size
        if n_f >= 4:
            frames    = samples[: n_f * frame_size].reshape(n_f, frame_size)
            frame_rms = np.sqrt(np.mean(frames ** 2, axis=1))
            energy_var = float(np.std(frame_rms))
        else:
            energy_var = 0.0

        # ── Klassifikation ────────────────────────────────────────────────────
        energy_high = energy_norm >= _ENERGY_HIGH_NORM
        energy_low  = energy_norm <= _ENERGY_LOW_NORM
        zcr_high    = zcr >= _ZCR_HIGH
        var_high    = energy_var >= _VAR_HIGH

        if energy_high and var_high:
            label, hint = "aufgeregt", "aufgeregt oder begeistert"
        elif energy_high and not var_high:
            label, hint = "angespannt", "etwas angespannt oder unter Druck"
        elif energy_low and zcr_high:
            label, hint = "gestresst", "möglicherweise unsicher oder angespannt"
        elif energy_low and not zcr_high:
            label, hint = "ruhig", "ruhig und entspannt"
        else:
            return {"label": "neutral", "hint": "", "energy": energy_norm}

        logger.debug(
            f"Emotion: {label!r}  "
            f"energy={energy_norm:.2f}  zcr={zcr:.3f}  var={energy_var:.0f}"
        )
        return {"label": label, "hint": hint, "energy": energy_norm}

    except Exception as exc:
        logger.debug(f"Emotion-Erkennung Fehler: {exc}")
        return _neutral
