"""
Tagesplanung — einfacher persistenter Aufgabenplaner.

Speichert Tages-Aufgaben in %APPDATA%\\Jarvis\\day_plan.json.
Wird automatisch beim Tageswechsel zurueckgesetzt.
"""
import json
import logging
import os
import threading
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("jarvis.day_planner")

_DATA_DIR  = Path(os.environ.get("APPDATA", str(Path.home()))) / "Jarvis"
_PLAN_FILE = _DATA_DIR / "day_plan.json"


class DayPlanner:
    """Thread-sicherer persistenter Tagesplaner."""

    def __init__(self):
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._lock:    threading.Lock = threading.Lock()
        self._today:   str = str(date.today())
        self._tasks:   List[Dict] = []
        self._next_id: int = 0   # BUG-017: monoton steigender Zaehler statt len()
        self._load()

    # ── Persistenz ────────────────────────────────────────────────────────────

    def _load(self):
        try:
            if _PLAN_FILE.exists():
                data = json.loads(_PLAN_FILE.read_text("utf-8"))
                if isinstance(data, dict) and data.get("date") == self._today:
                    self._tasks = data.get("tasks", [])
                    # BUG-017: next_id aus gespeichertem Wert oder max(ID)+1 ableiten
                    self._next_id = data.get(
                        "next_id",
                        max((t.get("id", 0) for t in self._tasks), default=-1) + 1,
                    )
                    logger.info(f"Tagesplan geladen: {len(self._tasks)} Aufgaben")
                    return
        except Exception as e:
            logger.warning(f"Tagesplan-Laden: {e}")
        self._tasks = []
        self._next_id = 0

    def _save(self):
        """Schreibt den Tagesplan auf Disk.
        BUG-061: Nicht unter self._lock aufrufen — Disk-I/O blockiert den Lock.
        Snapshot unter Lock erstellen, dann ausserhalb schreiben.
        """
        with self._lock:
            snapshot = {
                "date":    self._today,
                "tasks":   list(self._tasks),
                "next_id": self._next_id,
            }
        try:
            tmp = _PLAN_FILE.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(snapshot, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(_PLAN_FILE)
        except Exception as e:
            logger.warning(f"Tagesplan-Speichern: {e}")

    def _check_day_reset(self):
        """Setzt den Plan beim Tageswechsel automatisch zurueck.
        Muss unter self._lock aufgerufen werden.
        BUG-061: _save() wird vom Aufrufer nach Freigabe des Locks aufgerufen.
        Gibt True zurueck wenn ein Reset stattfand.
        """
        today = str(date.today())
        if today != self._today:
            self._today = today
            self._tasks = []
            self._next_id = 0  # BUG-017: Counter bei Tageswechsel zuruecksetzen
            logger.info("Tageswechsel — Tagesplan zurueckgesetzt.")
            return True
        return False

    # ── Oeffentliche API ──────────────────────────────────────────────────────

    def add_task(self, task: str, priority: str = "normal", time_hint: str = "") -> str:
        """
        Fuegt eine Aufgabe zum Tagesplan hinzu.
        priority: 'high' | 'normal' | 'low'
        time_hint: z.B. '14:00', 'morgens', 'abends'
        """
        with self._lock:
            self._check_day_reset()
            entry = {
                "id":        self._next_id,   # BUG-017: eindeutiger monotoner Zaehler
                "task":      task.strip(),
                "priority":  priority,
                "time_hint": time_hint,
                "done":      False,
            }
            self._next_id += 1   # BUG-017: nach Eintrag erhoehen
            self._tasks.append(entry)
            icons = {"high": "Hoch", "normal": "Normal", "low": "Niedrig"}
            icon = icons.get(priority, "Normal")
            time_str = f" um {time_hint}" if time_hint else ""
        # BUG-061: _save() ausserhalb des Locks aufrufen
        self._save()
        return f"Aufgabe hinzugefuegt [{icon}]: '{task}'{time_str}."

    def complete_task(self, task_id_or_name: str) -> str:
        """Markiert eine Aufgabe als erledigt (per ID oder Namens-Substring)."""
        result_msg = None
        with self._lock:
            self._check_day_reset()
            # Erst per ID versuchen
            try:
                tid = int(task_id_or_name)
                for t in self._tasks:
                    if t["id"] == tid and not t["done"]:
                        t["done"] = True
                        result_msg = f"Aufgabe #{tid} '{t['task']}' erledigt."
                        break
            except ValueError:
                pass
            # Dann per Substring
            if result_msg is None:
                query = task_id_or_name.lower()
                for t in self._tasks:
                    if query in t["task"].lower() and not t["done"]:
                        t["done"] = True
                        result_msg = f"Aufgabe '{t['task']}' als erledigt markiert."
                        break
        # BUG-061: _save() ausserhalb des Locks aufrufen
        if result_msg is not None:
            self._save()
            return result_msg
        return f"Keine offene Aufgabe '{task_id_or_name}' gefunden."

    def get_plan(self, filter_done: bool = False) -> str:
        """Gibt den aktuellen Tagesplan als lesbaren String zurueck."""
        with self._lock:
            did_reset = self._check_day_reset()
            # BUG-083: self._tasks direkt zuweisen erzeugt eine Referenz — Iteration
            # ausserhalb des Locks ist dann nicht thread-sicher (z.B. wenn parallel_tasks
            # gleichzeitig add_task() aufruft und die Liste modifiziert wird).
            # list() erzeugt immer eine flache Kopie, egal ob filter_done gesetzt ist.
            tasks = [t for t in self._tasks if not t["done"]] if filter_done else list(self._tasks)
        # BUG-061: Nach Tageswechsel-Reset ausserhalb des Locks speichern
        if did_reset:
            self._save()

        if not tasks:
            return "Dein Tagesplan ist leer. Du kannst Aufgaben hinzufuegen."

        icons = {"high": "[!]", "normal": "[ ]", "low": "[~]"}
        lines = [f"Tagesplan fuer heute ({self._today}):"]
        for t in tasks:
            icon   = icons.get(t["priority"], "[ ]")
            status = "erledigt" if t["done"] else "offen"
            time_s = f" [{t['time_hint']}]" if t.get("time_hint") else ""
            lines.append(f"  {icon} #{t['id']} {t['task']}{time_s} — {status}")
        return "\n".join(lines)

    def clear_done(self) -> str:
        """Entfernt alle erledigten Aufgaben."""
        with self._lock:
            before = len(self._tasks)
            self._tasks = [t for t in self._tasks if not t["done"]]
            removed = before - len(self._tasks)
        # BUG-061: _save() ausserhalb des Locks aufrufen
        self._save()
        return f"{removed} erledigte Aufgaben entfernt."

    def get_open_count(self) -> int:
        """Gibt die Anzahl offener Aufgaben zurueck."""
        with self._lock:
            return sum(1 for t in self._tasks if not t["done"])
