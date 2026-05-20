"""
Strukturierter, dauerhafter Speicher für Jarvis.

Kategorien:  facts | preferences | tasks | general
Priorität:   0 (niedrig) … 10 (hoch)  — neuere/korrigierte Einträge bekommen mehr Gewicht
Filterung:   beim Prompt-Aufbau werden nur zur Kontext-Anfrage passende Einträge genutzt
"""

import difflib
import json
import logging
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from config.settings import CONFIG_DIR

logger = logging.getLogger("jarvis.memory")

_MEMORY_FILE = CONFIG_DIR / "memories.json"

# Menschlich lesbare Kategorienamen
CATEGORY_LABELS = {
    "facts":       "📋 Fakten",
    "preferences": "❤️ Vorlieben",
    "tasks":       "✅ Aufgaben",
    "general":     "💬 Allgemein",
}

VALID_CATEGORIES = set(CATEGORY_LABELS.keys())

# Schlüsselwörter die eine Kategorie nahelegen (für Auto-Erkennung)
_CAT_KEYWORDS = {
    "preferences": [
        "mag", "mögen", "liebe", "lieblings", "bevorzuge", "vorliebe",
        "esse", "trinke", "höre", "schaue", "spiele", "genre",
    ],
    "tasks": [
        "erinnere", "vergiss nicht", "aufgabe", "todo", "soll", "muss",
        "timer", "termin", "meeting", "deadline",
    ],
    "facts": [
        "heiße", "bin", "wohne", "arbeite", "studiere", "alter",
        "geburtstag", "beruf", "sprache", "nationalität",
    ],
}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _guess_category(info: str) -> str:
    """Erkennt die Kategorie anhand von Schlüsselwörtern im Text."""
    text = info.lower()
    for cat, keywords in _CAT_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return cat
    return "general"


class MemoryEntry:
    __slots__ = ("id", "content", "category", "priority", "created_at", "updated_at")

    def __init__(self, content: str, category: str = "general", priority: int = 5,
                 entry_id: str = "", created_at: str = "", updated_at: str = ""):
        self.id         = entry_id or str(uuid.uuid4())[:8]
        self.content    = content.strip()
        self.category   = category if category in VALID_CATEGORIES else "general"
        self.priority   = max(0, min(10, priority))
        self.created_at = created_at or _now()
        self.updated_at = updated_at or self.created_at

    def to_dict(self) -> dict:
        return {
            "id":         self.id,
            "content":    self.content,
            "category":   self.category,
            "priority":   self.priority,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MemoryEntry":
        return cls(
            content    = d.get("content", ""),
            category   = d.get("category", "general"),
            priority   = d.get("priority", 5),
            entry_id   = d.get("id", ""),
            created_at = d.get("created_at", ""),
            updated_at = d.get("updated_at", ""),
        )


_BACKUP_FILE = CONFIG_DIR / "memories.backup.json"


class MemoryStore:
    def __init__(self):
        self._file: Path = _MEMORY_FILE
        self._lock = threading.Lock()        # guards in-memory _entries
        # BUG-080: separate lock for disk I/O so two concurrent _save() calls
        # (e.g. from parallel_tasks running remember_info + update_info at the
        # same time) cannot interleave writes to the shared .tmp file and
        # corrupt the stored memory.  _lock is NOT held during disk I/O (BUG-068
        # pattern) — _save_lock serialises only the write/replace pair.
        self._save_lock = threading.Lock()
        self._entries: list[MemoryEntry] = self._load()
        self._last_mtime: float = self._file.stat().st_mtime if self._file.exists() else 0.0
        logger.info(f"Memory geladen: {len(self._entries)} Einträge")
        # Automatisches Backup beim Start (lautlos)
        self._auto_backup()

    def _auto_backup(self):
        """Erstellt beim Start leise ein Backup der memories.json."""
        try:
            if self._file.exists() and self._entries:
                shutil.copy2(self._file, _BACKUP_FILE)
        except Exception as e:
            logger.debug(f"Auto-Backup fehlgeschlagen: {e}")

    def backup(self) -> str:
        """Manuelles Backup — wird aus dem Tray aufgerufen."""
        try:
            with self._lock:
                if not self._entries:
                    return "Keine Einträge zum Sichern."
                count = len(self._entries)
            # Zeitgestempeltes Backup zusätzlich zum Basis-Backup
            ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
            dest = _BACKUP_FILE.parent / f"memories_{ts}.backup.json"
            shutil.copy2(self._file, _BACKUP_FILE)   # aktuelles Basis-Backup
            shutil.copy2(self._file, dest)            # zeitgestempeltes Backup
            logger.info(f"Memory gesichert: {dest.name}")
            return f"{count} Einträge gesichert → {dest.name}"
        except Exception as e:
            logger.error(f"Backup fehlgeschlagen: {e}")
            return f"Backup fehlgeschlagen: {e}"

    def restore_backup(self) -> str:
        """Stellt das letzte Backup wieder her."""
        try:
            if not _BACKUP_FILE.exists():
                return "Kein Backup vorhanden."
            shutil.copy2(_BACKUP_FILE, self._file)
            # BUG-088: _load() does file I/O — must NOT be called while holding
            # self._lock (same BUG-068 pattern as _save()).  Load outside, then
            # swap the result in under the lock.
            new_entries = self._load()
            with self._lock:
                self._entries    = new_entries
                self._last_mtime = self._file.stat().st_mtime if self._file.exists() else 0.0
            count = len(new_entries)
            logger.info(f"Memory wiederhergestellt: {count} Einträge")
            return f"Backup wiederhergestellt: {count} Einträge."
        except Exception as e:
            return f"Wiederherstellung fehlgeschlagen: {e}"

    def _maybe_reload(self):
        """Lädt die Datei neu, wenn sie von einem anderen Prozess verändert wurde.
        BUG-093: Disk-I/O darf NICHT unter self._lock laufen (BUG-068/088-Muster).
        Double-Checked Locking: stat() + _load() ausserhalb, dann unter Lock eintragen.
        Darf NICHT unter self._lock aufgerufen werden — Callers muessen es vorher rufen.
        """
        try:
            if not self._file.exists():
                return
            mtime = self._file.stat().st_mtime   # stat() ausserhalb des Locks
            with self._lock:
                if mtime <= self._last_mtime:
                    return   # keine Aenderung — schneller Pfad
            # Disk-I/O ausserhalb des Locks
            new_entries = self._load()
            with self._lock:
                if mtime > self._last_mtime:   # Double-Check nach erneutem Lock
                    self._entries    = new_entries
                    self._last_mtime = mtime
        except Exception:
            pass

    # ──────────────────────────────────────────────────────────────
    # Persistenz
    # ──────────────────────────────────────────────────────────────

    def _load(self) -> list[MemoryEntry]:
        if not self._file.exists():
            return []
        try:
            raw = json.loads(self._file.read_text(encoding="utf-8"))
            # Altes Format (plain list of strings) migrieren
            if raw and isinstance(raw[0], str):
                logger.info("Altes Memory-Format erkannt, migriere...")
                return [MemoryEntry(s) for s in raw]
            return [MemoryEntry.from_dict(d) for d in raw if isinstance(d, dict)]
        except Exception as e:
            logger.warning(f"Memory konnte nicht geladen werden: {e}")
            return []

    def _save(self):
        """Atomisches Schreiben des Memory-Stores auf Disk.
        BUG-068: Nicht unter self._lock aufrufen — holt intern Lock fuer Snapshot,
        schreibt Disk-I/O ausserhalb um den Lock nicht zu blockieren.
        BUG-080: _save_lock serialisiert die tmp-Schreibphase, damit zwei
        gleichzeitige _save()-Aufrufe (z.B. durch parallel_tasks) die .tmp-Datei
        nicht gleichzeitig beschreiben und korrumpieren.
        """
        with self._lock:
            data = [e.to_dict() for e in self._entries]
        content = json.dumps(data, indent=2, ensure_ascii=False)
        # Atomisches Schreiben: erst in temporäre Datei, dann umbenennen
        tmp = self._file.with_suffix(".tmp")
        with self._save_lock:
            try:
                tmp.write_text(content, encoding="utf-8")
                tmp.replace(self._file)
            except Exception:
                # Fallback: direkt schreiben
                try:
                    tmp.unlink(missing_ok=True)
                except Exception:
                    pass
                self._file.write_text(content, encoding="utf-8")
        try:
            self._last_mtime = self._file.stat().st_mtime
        except Exception:
            pass

    # ──────────────────────────────────────────────────────────────
    # Fuzzy-Suche (intern)
    # ──────────────────────────────────────────────────────────────

    def _find_similar(self, query: str, cutoff: float = 0.65) -> Optional[MemoryEntry]:
        """Findet den ähnlichsten Eintrag (exakter Substring, dann fuzzy)."""
        q = query.lower()
        # 1. Exakter Substring
        for e in self._entries:
            if q in e.content.lower() or e.content.lower() in q:
                return e
        # 2. Fuzzy
        contents = [e.content.lower() for e in self._entries]
        matches  = difflib.get_close_matches(q, contents, n=1, cutoff=cutoff)
        if matches:
            idx = contents.index(matches[0])
            return self._entries[idx]
        return None

    # ──────────────────────────────────────────────────────────────
    # Öffentliche API – für KI-Tools
    # ──────────────────────────────────────────────────────────────

    def remember(self, info: str, category: str = "") -> str:
        """Speichert eine neue Information. Erkennt Duplikate und Kategorie automatisch."""
        self._maybe_reload()   # BUG-093: ausserhalb des Locks aufrufen
        with self._lock:
            info = info.strip()
            if not info:
                return "Keine Information angegeben."

            cat = category if category in VALID_CATEGORIES else _guess_category(info)

            # Duplikat-Prüfung (≥ 90 % ähnlich)
            similar = self._find_similar(info, cutoff=0.90)
            if similar:
                return f"Das weiß ich bereits: {similar.content}"

            entry = MemoryEntry(info, category=cat, priority=5)
            self._entries.append(entry)
            logger.info(f"Gespeichert [{cat}]: '{info}'")
        # BUG-068: _save() ausserhalb des Locks aufrufen
        self._save()
        return f"Gespeichert: {info}"

    def update(self, search_term: str, new_info: str, category: str = "") -> str:
        """Überschreibt einen vorhandenen Eintrag (Korrektur).
        BUG-023: _maybe_reload() vor der Suche — verhindert TOCTOU wenn Datei
        extern geändert wurde, bevor update() die Suche startet.
        BUG-093: _maybe_reload() ausserhalb des Locks aufrufen.
        """
        self._maybe_reload()   # BUG-023/BUG-093: ausserhalb des Locks
        need_save  = False
        result_msg = None
        with self._lock:
            entry = self._find_similar(search_term, cutoff=0.55)
            if entry:
                old_content = entry.content
                entry.content    = new_info.strip()
                entry.category   = category if category in VALID_CATEGORIES else entry.category
                entry.priority   = min(10, entry.priority + 2)   # Korrigierte Einträge = höhere Priorität
                entry.updated_at = _now()
                need_save  = True
                logger.info(f"Aktualisiert: '{old_content}' → '{new_info}'")
                result_msg = f"Korrigiert: '{old_content}' → '{new_info}'"
        # BUG-068: _save() ausserhalb des Locks aufrufen
        if need_save:
            self._save()
            return result_msg
        # Nichts gefunden → neu anlegen (außerhalb des Locks um Deadlock mit remember() zu vermeiden)
        return self.remember(new_info, category)

    def forget(self, search_term: str) -> str:
        """Löscht einen Eintrag (fuzzy)."""
        self._maybe_reload()   # BUG-095: fehlte — inkonsistent mit remember()/update()
        found_content = None
        with self._lock:
            entry = self._find_similar(search_term, cutoff=0.55)
            if not entry:
                return "Diese Information habe ich nicht gespeichert."
            found_content = entry.content
            self._entries.remove(entry)
            logger.info(f"Gelöscht: '{found_content}'")
        # BUG-068: _save() ausserhalb des Locks aufrufen
        self._save()
        return f"Vergessen: {found_content}"

    def forget_all(self) -> str:
        with self._lock:
            count = len(self._entries)
            self._entries.clear()
        # BUG-068: _save() ausserhalb des Locks aufrufen
        self._save()
        return f"Alle {count} Einträge gelöscht."

    def search(self, query: str) -> str:
        """Sucht Einträge und gibt sie als lesbaren Text zurück."""
        with self._lock:
            q = query.lower()
            found = [e for e in self._entries if q in e.content.lower()]
            if not found:
                # Fuzzy-Fallback
                # BUG-NEW-8: contents.index(c) liefert immer den ERSTEN Index des
                # Strings — wenn zwei Eintraege identischen (lowercase) Content haben,
                # wird derselbe Eintrag zweimal zurueckgegeben (Duplikat), waehrend
                # der zweite Eintrag nie erreichbar ist.
                # Fix: enumerate() nutzen und per Index direkt zugreifen.
                contents = [e.content.lower() for e in self._entries]
                close    = difflib.get_close_matches(q, contents, n=5, cutoff=0.45)
                seen_ids: set = set()
                found = []
                for c in close:
                    for idx, lc in enumerate(contents):
                        if lc == c and self._entries[idx].id not in seen_ids:
                            seen_ids.add(self._entries[idx].id)
                            found.append(self._entries[idx])
                            break
            if not found:
                return f"Keine gespeicherten Informationen zu '{query}' gefunden."
            lines = [f"- [{e.category}] {e.content}" for e in found]
            return "Gefundene Einträge:\n" + "\n".join(lines)

    def get_all(self) -> list[MemoryEntry]:
        with self._lock:
            return list(self._entries)

    def get_by_category(self, category: str) -> list[MemoryEntry]:
        with self._lock:
            return [e for e in self._entries if e.category == category]

    def get_entry_by_id(self, entry_id: str) -> Optional[MemoryEntry]:
        with self._lock:
            for e in self._entries:
                if e.id == entry_id:
                    return e
            return None

    def update_entry(self, entry_id: str, content: str, category: str, priority: int):
        """Direkt-Update aus dem UI (Memory-Fenster)."""
        need_save = False
        with self._lock:
            e = next((x for x in self._entries if x.id == entry_id), None)
            if e:
                e.content    = content.strip()
                e.category   = category
                e.priority   = max(0, min(10, priority))
                e.updated_at = _now()
                need_save    = True
        # BUG-068: _save() ausserhalb des Locks aufrufen
        if need_save:
            self._save()

    def delete_entry(self, entry_id: str):
        """Löscht einen Eintrag anhand seiner ID (aus dem UI)."""
        with self._lock:
            self._entries = [e for e in self._entries if e.id != entry_id]
        # BUG-068: _save() ausserhalb des Locks aufrufen
        self._save()

    def add_entry(self, content: str, category: str, priority: int = 5):
        """Fügt einen neuen Eintrag direkt hinzu (aus dem UI)."""
        with self._lock:
            e = MemoryEntry(content, category=category, priority=priority)
            self._entries.append(e)
        # BUG-068: _save() ausserhalb des Locks aufrufen
        self._save()
        return e

    # ──────────────────────────────────────────────────────────────
    # Prompt-Injection (smart, gefiltert)
    # ──────────────────────────────────────────────────────────────

    def format_for_prompt(self, context: str = "") -> str:
        """
        Gibt relevante Memories als Text zurück.
        Bei < 15 Einträgen: alle.
        Bei >= 15: nur die 15 relevantesten (keyword-scored + Priorität).
        """
        self._maybe_reload()   # BUG-093: ausserhalb des Locks aufrufen
        with self._lock:
            if not self._entries:
                return ""

            entries = sorted(self._entries, key=lambda e: e.priority, reverse=True)

            if len(entries) <= 15 or not context:
                selected = entries[:20]
            else:
                ctx = context.lower()
                def _score(e: MemoryEntry) -> float:
                    words   = ctx.split()
                    content = e.content.lower()
                    hits    = sum(1 for w in words if len(w) > 3 and w in content)
                    return hits * 2 + e.priority * 0.5

                entries.sort(key=_score, reverse=True)
                selected = entries[:15]

            if not selected:
                return ""

            # Kategorienweise gruppieren
            by_cat: dict[str, list[str]] = {}
            for e in selected:
                by_cat.setdefault(e.category, []).append(e.content)

            lines = ["\nGespeicherte Informationen über den Nutzer:"]
            for cat in ("facts", "preferences", "tasks", "general"):
                if cat in by_cat:
                    label = CATEGORY_LABELS[cat]
                    for item in by_cat[cat]:
                        lines.append(f"  {label}: {item}")

            return "\n".join(lines)
