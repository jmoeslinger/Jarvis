"""
Gesprächs-Log für Jarvis — Langzeit-Kontext & Gespräch fortsetzen.

Speichert Konversations-Turns mit Zeitstempel über mehrere Sitzungen hinweg.
Stellt zwei Hauptfunktionen bereit:
  - Langzeit-Kontext: vergangene Gespräche als System-Prompt-Kontext
  - Gespräch fortsetzen: letzte Session in Chat-History laden
"""

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from config.settings import CONFIG_DIR

logger = logging.getLogger("jarvis.conversation_log")

_LOG_FILE = CONFIG_DIR / "conversation_log.json"
_MAX_SESSIONS      = 30   # maximale Anzahl gespeicherter Sessions
_MAX_TURNS_SESSION = 20   # maximale Turns pro Session
_CONTEXT_SESSIONS  = 3    # Sessions im System-Prompt-Kontext
_CONTEXT_TURNS     = 5    # Turns pro Session im Kontext-String
_RESUME_TURNS      = 8    # Turns beim Wiederherstellen einer Session


class ConversationLog:
    """
    Persistenter Gesprächs-Log über mehrere Jarvis-Sitzungen.
    Thread-sicher. Schreibt atomar auf Disk.
    """

    def __init__(self):
        self._file = _LOG_FILE
        self._lock = threading.Lock()
        self._sessions: List[Dict] = self._load()
        self._current_turns: List[Dict] = []
        self._session_start = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        logger.info(
            f"ConversationLog bereit: {len(self._sessions)} gespeicherte Sessions"
        )

    # ── Persistenz ────────────────────────────────────────────────────────────

    def _load(self) -> List[Dict]:
        if not self._file.exists():
            return []
        try:
            raw = json.loads(self._file.read_text(encoding="utf-8"))
            return raw if isinstance(raw, list) else []
        except Exception as exc:
            logger.warning(f"ConversationLog laden fehlgeschlagen: {exc}")
            return []

    def _save(self):
        """Speichert alle Sessions inkl. der aktuellen atomar auf Disk."""
        try:
            all_sessions = list(self._sessions)
            if self._current_turns:
                all_sessions.append({
                    "session_start": self._session_start,
                    "turns":         list(self._current_turns),
                })
            # Auf Maximum begrenzen — älteste entfernen
            if len(all_sessions) > _MAX_SESSIONS:
                all_sessions = all_sessions[-_MAX_SESSIONS:]

            tmp = self._file.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(all_sessions, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self._file)
        except Exception as exc:
            logger.error(f"ConversationLog speichern fehlgeschlagen: {exc}")

    # ── Öffentliche API ───────────────────────────────────────────────────────

    def save_turn(self, user_text: str, assistant_text: str):
        """
        Speichert einen Gesprächs-Turn (Nutzer + Assistent-Antwort).
        Systemhints wie '[Nutzer flüstert]' werden aus dem User-Text entfernt.
        """
        if not user_text.strip() or not assistant_text.strip():
            return

        # Eingebettete Kontext-Hints aus User-Text herausfiltern
        clean_user = user_text.strip()
        if clean_user.startswith("["):
            end = clean_user.find("]")
            if end != -1:
                clean_user = clean_user[end + 1:].strip()
        if not clean_user:
            return

        turn = {
            "ts":        datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "user":      clean_user[:500],
            "assistant": assistant_text.strip()[:1000],
        }
        with self._lock:
            self._current_turns.append(turn)
            # Session auf Maximum begrenzen
            if len(self._current_turns) > _MAX_TURNS_SESSION:
                self._current_turns = self._current_turns[-_MAX_TURNS_SESSION:]
            # Alle 4 Turns periodisch auf Disk schreiben
            if len(self._current_turns) % 4 == 0:
                self._save()

    def flush(self):
        """
        Speichert die aktuelle Session sofort auf Disk.
        Sollte beim Beenden von Jarvis aufgerufen werden.
        """
        with self._lock:
            if self._current_turns:
                self._save()
                logger.info(
                    f"ConversationLog gespeichert: {len(self._current_turns)} Turns"
                )

    def get_last_session_history(self, max_turns: int = _RESUME_TURNS) -> List[Dict]:
        """
        Gibt die letzte gespeicherte Session als Chat-History zurück.
        Format: [{"role": "user", "content": …}, {"role": "assistant", "content": …}, …]

        Verwendung: 'Gespräch fortsetzen' — wird direkt in GrokClient._history geladen.
        Die KI denkt so, dass das letzte Gespräch noch aktiv ist.
        """
        with self._lock:
            if not self._sessions:
                return []
            last_session = self._sessions[-1]
            turns = last_session.get("turns", [])[-max_turns:]

        history: List[Dict] = []
        for turn in turns:
            u = turn.get("user", "")
            a = turn.get("assistant", "")
            if u:
                history.append({"role": "user",      "content": u})
            if a:
                history.append({"role": "assistant", "content": a})
        return history

    def get_context_string(self) -> str:
        """
        Gibt vergangene Gespräche als kompakten Text-Kontext zurück.
        Wird in den System-Prompt eingebettet ('Langzeit-Kontext').
        Gibt leeren String zurück wenn keine früheren Sessions vorhanden.
        """
        with self._lock:
            recent = list(self._sessions[-_CONTEXT_SESSIONS:])

        if not recent:
            return ""

        lines = ["\nLangzeit-Gesprächskontext (frühere Sitzungen):"]
        for session in recent:
            ts       = session.get("session_start", "")
            date_str = ts[:10] if ts else "?"
            turns    = session.get("turns", [])[-_CONTEXT_TURNS:]
            if not turns:
                continue
            lines.append(f"  — Sitzung vom {date_str}:")
            for t in turns:
                u = t.get("user", "")[:100]
                a = t.get("assistant", "")[:180]
                if u:
                    lines.append(f"    Nutzer: {u}")
                if a:
                    lines.append(f"    Jarvis: {a}")

        return "\n".join(lines) if len(lines) > 1 else ""

    def get_session_count(self) -> int:
        """Anzahl gespeicherter (vergangener) Sessions."""
        with self._lock:
            return len(self._sessions)

    def clear(self):
        """Löscht alle gespeicherten Gespräche und die Disk-Datei."""
        with self._lock:
            self._sessions.clear()
            self._current_turns.clear()
            try:
                self._file.unlink(missing_ok=True)
            except Exception:
                pass
        logger.info("ConversationLog vollständig gelöscht.")
