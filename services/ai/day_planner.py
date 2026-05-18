"""
Tagesplanung — einfacher persistenter Aufgabenplaner.

Speichert Tages-Aufgaben in %APPDATA%\Jarvis\day_plan.json.
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
        self._lock:  threading.Lock = threading.Lock()
        self._today: str = str(date.today())
        self._tasks: List[Dict] = []
        self._load()

    # ── Persistenz ────────────────────────────────────────────────────────────

    def _load(self):
        try:
            if _PLAN_FILE.exists():
                data = json.loads(_PLAN_FILE.read_text("utf-8"))
                if isinstance(data, dict) and data.get("date") == self._today:
                    self._tasks = data.get("tasks", [])
                    logger.info(f"Tagesplan geladen: {len(self._tasks)} Aufgaben")
                    return
        except Exception as e:
            logger.warning(f"Tagesplan-Laden: {e}")
        self._tasks = []

    def _save(self):
        try:
            tmp = _PLAN_FILE.with_suffix(".tmp")
            tmp.write_text(
                json.dumps({"date": self._today, "tasks": self._tasks}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(_PLAN_FILE)
        except Exception as e:
            logger.warning(f"Tagesplan-Speichern: {e}")

    def _check_day_reset(self):
        """Setzt den Plan beim Tageswechsel automatisch zurueck."""
        today = str(date.today())
        if today != self._today:
            self._today = today
            self._tasks = []
            self._save()
            logger.info("Tageswechsel — Tagesplan zurueckgesetzt.")

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
                "id":        len(self._tasks),
                "task":      task.strip(),
                "priority":  priority,
                "time_hint": time_hint,
                "done":      False,
            }
            self._tasks.append(entry)
            self._save()
            icons = {"high": "Hoch", "normal": "Normal", "low": "Niedrig"}
            icon = icons.get(priority, "Normal")
            time_str = f" um {time_hint}" if time_hint else ""
            return f"Aufgabe hinzugefuegt [{icon}]: '{task}'{time_str}."

    def complete_task(self, task_id_or_name: str) -> str:
        """Markiert eine Aufgabe als erledigt (per ID oder Namens-Substring)."""
        with self._lock:
            self._check_day_reset()
            # Erst per ID versuchen
            try:
                tid = int(task_id_or_name)
                for t in self._tasks:
                    if t["id"] == tid and not t["done"]:
                        t["done"] = True
                        self._save()
                        return f"Aufgabe #{tid} '{t['task']}' erledigt."
            except ValueError:
                pass
            # Dann per Substring
            query = task_id_or_name.lower()
            for t in self._tasks:
                if query in t["task"].lower() and not t["done"]:
                    t["done"] = True
                    self._save()
                    return f"Aufgabe '{t['task']}' als erledigt markiert."
            return f"Keine offene Aufgabe '{task_id_or_name}' gefunden."

    def get_plan(self, filter_done: bool = False) -> str:
        """Gibt den aktuellen Tagesplan als lesbaren String zurueck."""
        with self._lock:
            self._check_day_reset()
            tasks = [t for t in self._tasks if not t["done"]] if filter_done else self._tasks

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
            self._save()
        return f"{removed} erledigte Aufgaben entfernt."

    def get_open_count(self) -> int:
        """Gibt die Anzahl offener Aufgaben zurueck."""
        with self._lock:
            return sum(1 for t in self._tasks if not t["done"])
