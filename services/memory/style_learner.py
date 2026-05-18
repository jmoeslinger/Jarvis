"""
Sprachstil-Lernmodul für Jarvis — Sprachstil lernen & Adaptive Antworten.

Analysiert Nutzer-Befehle fortlaufend und leitet automatisch bevorzugte
Antwortlänge und Ton ab. Die Ergebnisse werden in GrokClient übernommen
wenn 'adaptive_responses' aktiviert ist.

Gespeichert in: %APPDATA%\\Jarvis\\style_profile.json
"""

import json
import logging
import threading
from typing import Dict

from config.settings import CONFIG_DIR

logger = logging.getLogger("jarvis.style_learner")

_STYLE_FILE = CONFIG_DIR / "style_profile.json"
_MIN_SAMPLES = 15   # Mindestanzahl Befehle vor erster Auto-Anpassung

# ── Sprachindikatoren ─────────────────────────────────────────────────────────

_FORMAL = [
    "bitte", "könnten sie", "würden sie", "ich hätte gerne",
    "entschuldigung", "wären sie so freundlich", "dürfte ich",
]
_CASUAL = [
    "hey ", "jo ", "ok cool", "mach mal", "gib mal", "leg los",
    "jetzt gleich", "schnell mal", "auf jeden", "klar",
]
_TECHNICAL = [
    "fehler", "error", "code", "skript", "python", "debug", "funktion",
    "variable", "log", "terminal", "befehl", "prozess", "port",
    "ip", "api", "json", "datei", "pfad", "speicher", "cpu", "ram",
    "netzwerk", "server", "datenbank",
]
_CLARIFICATION = [
    "was meintest", "erkläre nochmal", "nochmal bitte", "nochmal erklären",
    "habe nicht verstanden", "kannst du das nochmal", "wie genau",
    "was bedeutet", "was heißt", "ich verstehe nicht", "unklar",
]


def _ema(old: float, new: float, alpha: float = 0.10) -> float:
    """Exponential Moving Average — glättet Rauschen in den Scores."""
    return (1.0 - alpha) * old + alpha * new


def _default_profile() -> Dict:
    return {
        "total_commands":      0,
        "avg_word_count":      0.0,
        "formal_score":        0.5,    # 0 = sehr informal, 1 = sehr formal
        "casual_score":        0.0,
        "tech_score":          0.0,
        "interrupt_count":     0,      # TTS-Unterbrechungen durch Nutzer
        "clarification_count": 0,      # Rückfragen → Antwort war unklar
        "preferred_length":    "normal",
        "preferred_tone":      "normal",
    }


class StyleLearner:
    """
    Lernt den Sprachstil des Nutzers durch fortlaufende Analyse seiner Befehle.

    - learn_command(text)  : nach jedem Befehl aufrufen
    - record_interrupt()   : wenn Nutzer TTS unterbricht
    - get_preferred_length(): gibt "short" | "normal" | "detailed"
    - get_preferred_tone() : gibt "formal" | "normal" | "casual" | "technical"
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._profile: Dict = self._load()

    # ── Persistenz ────────────────────────────────────────────────────────────

    def _load(self) -> Dict:
        if _STYLE_FILE.exists():
            try:
                data = json.loads(_STYLE_FILE.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    # Fehlende Keys mit Defaults auffüllen (Vorwärtskompatibilität)
                    defaults = _default_profile()
                    for k, v in defaults.items():
                        data.setdefault(k, v)
                    logger.info(
                        f"Style-Profil geladen: {data['total_commands']} Commands gelernt"
                    )
                    return data
            except Exception as exc:
                logger.warning(f"Style-Profil laden fehlgeschlagen: {exc}")
        return _default_profile()

    def _save(self):
        """Schreibt das Profil auf Disk.
        BUG-058: Nicht unter self._lock aufrufen — Disk-I/O blockiert den Lock.
        Snapshot ausserhalb des Locks serialisieren.
        """
        with self._lock:
            snapshot = dict(self._profile)
        try:
            tmp = _STYLE_FILE.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(snapshot, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(_STYLE_FILE)
        except Exception as exc:
            logger.error(f"Style-Profil speichern fehlgeschlagen: {exc}")

    # ── Lernlogik ─────────────────────────────────────────────────────────────

    def learn_command(self, command: str):
        """
        Analysiert einen Nutzerbefehl und aktualisiert das Stil-Profil.
        Schreibt alle 5 Commands auf Disk. Thread-sicher.
        """
        text = command.lower().strip()
        # Eingebettete Kontext-Hints entfernen (z.B. "[Nutzer flüstert]")
        if text.startswith("["):
            end = text.find("]")
            if end != -1:
                text = text[end + 1:].strip()

        if not text:
            return

        words = text.split()
        wc    = len(words)

        is_formal          = any(m in text for m in _FORMAL)
        is_casual          = any(m in text for m in _CASUAL)
        is_tech            = any(m in text for m in _TECHNICAL)
        is_clarification   = any(m in text for m in _CLARIFICATION)

        with self._lock:
            n = self._profile["total_commands"]

            # Wortanzahl-Durchschnitt
            if n == 0:
                self._profile["avg_word_count"] = float(wc)
            else:
                self._profile["avg_word_count"] = _ema(
                    self._profile["avg_word_count"], float(wc)
                )

            # Tonalitäts-Scores mit EMA
            formal_target = 1.0 if is_formal else (0.0 if is_casual else None)
            if formal_target is not None:
                self._profile["formal_score"] = _ema(
                    self._profile["formal_score"], formal_target, alpha=0.08
                )

            self._profile["casual_score"] = _ema(
                self._profile["casual_score"],
                1.0 if is_casual else 0.0,
                alpha=0.08,
            )
            self._profile["tech_score"] = _ema(
                self._profile["tech_score"],
                1.0 if is_tech else 0.0,
                alpha=0.07,
            )

            if is_clarification:
                self._profile["clarification_count"] += 1

            self._profile["total_commands"] = n + 1

            # Präferenzen ableiten (erst ab Mindestanzahl)
            if n + 1 >= _MIN_SAMPLES:
                self._derive_preferences()

            # Alle 5 Commands auf Disk — Flag setzen, ausserhalb des Locks speichern
            should_save = (n + 1) % 5 == 0

        # BUG-058: _save() ausserhalb des Locks aufrufen (holt intern Lock fuer Snapshot)
        if should_save:
            self._save()

    def record_interrupt(self):
        """
        Notiert eine TTS-Unterbrechung durch den Nutzer.
        Erhöht den Interrupt-Zähler und leitet ggf. neue Präferenzen ab.
        """
        with self._lock:
            self._profile["interrupt_count"] += 1
            if self._profile["total_commands"] >= _MIN_SAMPLES:
                self._derive_preferences()
            _count = self._profile["interrupt_count"]
        # BUG-058: _save() ausserhalb des Locks aufrufen
        self._save()
        logger.debug(f"TTS-Interrupt notiert (gesamt: {_count})")

    def _derive_preferences(self):
        """
        Leitet preferred_length und preferred_tone aus den gelernten Scores ab.
        Muss unter gehaltenem Lock aufgerufen werden.
        """
        avg_wc         = self._profile["avg_word_count"]
        tech           = self._profile["tech_score"]
        formal         = self._profile["formal_score"]
        casual         = self._profile["casual_score"]
        interrupts     = self._profile["interrupt_count"]
        clarifications = self._profile["clarification_count"]

        # ── Antwortlänge ─────────────────────────────────────────────────────
        # Viele Unterbrechungen oder sehr kurze Befehle → kompakt
        if interrupts >= 4 or avg_wc <= 4:
            self._profile["preferred_length"] = "short"
        # Viele Rückfragen oder ausführliche Befehle → mehr Detail
        elif clarifications >= 3 or avg_wc >= 12:
            self._profile["preferred_length"] = "detailed"
        else:
            self._profile["preferred_length"] = "normal"

        # ── Ton ──────────────────────────────────────────────────────────────
        # Priorität: technical > formal/casual
        if tech >= 0.35:
            self._profile["preferred_tone"] = "technical"
        elif formal >= 0.55:
            self._profile["preferred_tone"] = "formal"
        elif casual >= 0.30:
            self._profile["preferred_tone"] = "casual"
        else:
            self._profile["preferred_tone"] = "normal"

    # ── Getter ────────────────────────────────────────────────────────────────

    def get_preferred_length(self) -> str:
        with self._lock:
            return self._profile.get("preferred_length", "normal")

    def get_preferred_tone(self) -> str:
        with self._lock:
            return self._profile.get("preferred_tone", "normal")

    def get_total_commands(self) -> int:
        with self._lock:
            return self._profile.get("total_commands", 0)

    def get_stats(self) -> Dict:
        with self._lock:
            return dict(self._profile)

    def reset(self):
        """Setzt das gesamte Lernprofil zurück und löscht die Disk-Datei."""
        with self._lock:
            self._profile = _default_profile()
        # BUG-058: _save() ausserhalb des Locks aufrufen
        self._save()
        logger.info("Style-Profil zurückgesetzt.")
