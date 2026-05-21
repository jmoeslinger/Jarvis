"""
Jarvis Task Manager
===================
Verwaltet eine priorisierte Aufgaben-Warteschlange mit:
  - Prioritäten (1 = höchste, 10 = niedrigste)
  - Sequenzieller Ausführung (ein Task gleichzeitig)
  - Abbruch-Support via threading.Event
  - Fortschritts-Tracking (0–100 %)
  - Wiederholende Tasks mit konfigurierbarem Intervall
  - Status-Callbacks für die UI
"""
import heapq
import logging
import threading
import time
import uuid
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("jarvis.tasks")


class TaskStatus(Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    DONE      = "done"
    CANCELLED = "cancelled"
    FAILED    = "failed"


class Task:
    """Repräsentiert eine einzelne Aufgabe in der Warteschlange."""

    def __init__(
        self,
        func:     Callable,
        args:     tuple = (),
        kwargs:   Optional[dict] = None,
        priority: int = 5,
        label:    str = "Task",
    ):
        self.id           = uuid.uuid4().hex[:8]
        self.func         = func
        self.args         = args
        self.kwargs       = kwargs or {}
        self.priority     = max(1, min(10, priority))  # Clamp 1–10
        self.label        = label
        self.status       = TaskStatus.PENDING
        self.progress     = 0                  # 0–100
        self.result: Any  = None
        self.error: str   = ""
        self.cancel_event = threading.Event()
        self.created_at   = time.monotonic()
        self.started_at:  Optional[float] = None
        self.finished_at: Optional[float] = None

    # heapq braucht __lt__ um Tasks nach Priorität zu sortieren
    def __lt__(self, other: "Task") -> bool:
        return self.priority < other.priority

    def to_dict(self) -> Dict:
        """Serialisiert den Task für die UI."""
        return {
            "id":       self.id,
            "label":    self.label,
            "status":   self.status.value,
            "priority": self.priority,
            "progress": self.progress,
            "error":    self.error,
        }


class TaskManager:
    """
    Priorisierte, sequenzielle Aufgaben-Warteschlange.

    on_update(task_list: List[Dict]) — Callback nach jeder Statusänderung.
    """

    def __init__(self, on_update: Optional[Callable[[List[Dict]], None]] = None):
        # Heap: (priority, seq, Task) — seq als Tiebreaker für gleiche Priorität
        self._heap:     List  = []
        self._lock            = threading.Lock()
        self._tasks:    Dict[str, Task] = {}
        self._current:  Optional[Task]  = None
        self._seq             = 0
        self._on_update       = on_update
        self._work_ready      = threading.Event()

        # Wiederholende Tasks: rid → {id, label, interval, func, args, kwargs, priority, timer, next_run}
        self._recurring:      Dict[str, Dict] = {}
        self._recurring_lock  = threading.Lock()

        # Worker starten
        self._running = True
        self._worker  = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name="Jarvis-TaskWorker",
        )
        self._worker.start()
        logger.info("TaskManager gestartet.")

    # ── Öffentliche API ───────────────────────────────────────────────────────

    def submit(
        self,
        func:     Callable,
        args:     tuple = (),
        kwargs:   Optional[dict] = None,
        priority: int = 5,
        label:    str = "Task",
    ) -> str:
        """
        Fügt einen Task zur Warteschlange hinzu.
        Gibt die Task-ID zurück.
        Die aufgerufene Funktion bekommt cancel_event als Keyword-Argument.
        """
        task = Task(func, args, kwargs, priority, label)
        with self._lock:
            self._seq += 1
            heapq.heappush(self._heap, (task.priority, self._seq, task))
            self._tasks[task.id] = task

        logger.info(
            f"Task eingereiht: [{task.id}] '{label}' (Priorität {priority})"
        )
        self._work_ready.set()
        self._notify()
        return task.id

    def cancel_current(self):
        """Bricht den aktuell laufenden Task ab."""
        with self._lock:
            current = self._current
        if current:
            # cancel_event.set() reicht — der Worker-Thread setzt den Status
            # in _execute() korrekt auf CANCELLED nachdem er das Event bemerkt.
            current.cancel_event.set()
            logger.info(f"Task-Abbruch angefordert: [{current.id}] '{current.label}'")
            self._notify()

    def cancel(self, task_id: str):
        """Bricht einen bestimmten Task ab (laufend oder wartend).
        BUG-NEW-1: status-Lesen und -Schreiben müssen unter demselben Lock-Acquire
        stattfinden.  Vorher wurde der Task unter Lock geholt, dann der Lock
        freigegeben und erst danach task.status gelesen/geschrieben — der
        Worker-Thread konnte dazwischen den Task auf RUNNING setzen, sodass
        der PENDING→CANCELLED-Übergang übersprungen wurde und der Worker-Thread
        nie ein CANCELLED sah obwohl cancel_event bereits gesetzt war.
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.cancel_event.set()
                if task.status == TaskStatus.PENDING:
                    task.status = TaskStatus.CANCELLED
        if task:
            logger.info(f"Task abgebrochen: [{task_id}] '{task.label}'")
            self._notify()

    def cancel_all(self):
        """Bricht alle Tasks ab — laufende und wartende."""
        with self._lock:
            tasks = list(self._tasks.values())
        for task in tasks:
            if task.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
                task.cancel_event.set()
                if task.status == TaskStatus.PENDING:
                    task.status = TaskStatus.CANCELLED
        logger.info("Alle Tasks abgebrochen.")
        self._notify()

    def set_progress(self, task_id: str, progress: int):
        """Setzt den Fortschritt (0–100) eines Tasks."""
        with self._lock:
            task = self._tasks.get(task_id)
        if task:
            task.progress = max(0, min(100, progress))
            self._notify()

    def get_all(self) -> List[Dict]:
        """Gibt alle Tasks als Liste zurück (für die UI)."""
        with self._lock:
            return [t.to_dict() for t in self._tasks.values()]

    def get_pending_count(self) -> int:
        """Anzahl der wartenden Tasks in der Queue."""
        with self._lock:
            return sum(
                1 for t in self._tasks.values()
                if t.status == TaskStatus.PENDING
            )

    def get_current(self) -> Optional[Dict]:
        """Den aktuell laufenden Task (oder None)."""
        with self._lock:
            if self._current and self._current.status == TaskStatus.RUNNING:
                return self._current.to_dict()
        return None

    def clear_done(self):
        """Entfernt abgeschlossene/abgebrochene/fehlgeschlagene Tasks."""
        with self._lock:
            done_ids = [
                tid for tid, t in self._tasks.items()
                if t.status in (
                    TaskStatus.DONE,
                    TaskStatus.CANCELLED,
                    TaskStatus.FAILED,
                )
            ]
            for tid in done_ids:
                del self._tasks[tid]
        self._notify()

    def is_busy(self) -> bool:
        """True wenn gerade ein Task läuft."""
        with self._lock:
            return (
                self._current is not None
                and self._current.status == TaskStatus.RUNNING
            )

    # ── Wiederholende Tasks ───────────────────────────────────────────────────

    def add_recurring(
        self,
        label:            str,
        interval_seconds: float,
        func:             Callable,
        args:             tuple = (),
        kwargs:           Optional[dict] = None,
        priority:         int = 5,
    ) -> str:
        """
        Fügt einen wiederholenden Task hinzu.
        Gibt die Recurring-ID zurück (zum späteren Entfernen).
        """
        rid = uuid.uuid4().hex[:8]
        entry: Dict = {
            "id":       rid,
            "label":    label,
            "interval": interval_seconds,
            "func":     func,
            "args":     args,
            "kwargs":   kwargs or {},
            "priority": priority,
            "timer":    None,
            "next_run": time.monotonic() + interval_seconds,
        }
        with self._recurring_lock:
            self._recurring[rid] = entry

        self._schedule_recurring(rid)
        logger.info(
            f"Wiederholender Task: '{label}' alle {interval_seconds:.0f}s (ID: {rid})"
        )
        return rid

    def remove_recurring(self, rid: str) -> bool:
        """Entfernt einen wiederholenden Task. Gibt True zurück wenn gefunden."""
        with self._recurring_lock:
            entry = self._recurring.pop(rid, None)
        if entry and entry.get("timer"):
            try:
                entry["timer"].cancel()
            except Exception:
                pass
        logger.info(f"Wiederholender Task entfernt: {rid}")
        return entry is not None

    def list_recurring(self) -> List[Dict]:
        """Gibt alle wiederholenden Tasks zurück."""
        with self._recurring_lock:
            return [
                {
                    "id":         e["id"],
                    "label":      e["label"],
                    "interval_s": e["interval"],
                    "next_in_s":  max(0.0, e["next_run"] - time.monotonic()),
                }
                for e in self._recurring.values()
            ]

    def stop(self):
        """Beendet den Worker-Thread sauber."""
        self._running = False
        self._work_ready.set()
        self.cancel_all()
        # Alle Recurring-Timer canceln
        with self._recurring_lock:
            for entry in self._recurring.values():
                if entry.get("timer"):
                    try:
                        entry["timer"].cancel()
                    except Exception:
                        pass
        # Worker-Thread sauber beenden (max. 2s warten)
        if self._worker.is_alive():
            self._worker.join(timeout=2.0)

    # ── Interne Worker-Logik ──────────────────────────────────────────────────

    def _worker_loop(self):
        """Hauptschleife des Worker-Threads — holt und führt Tasks aus.
        BUG-NEW-2: _work_ready.clear() nach wait() aber VOR der inneren
        Dequeue-Schleife aufzurufen erzeugt ein Race-Window:
          1. Worker wartet auf Event → Event wird gesetzt (submit)
          2. Worker wacht auf, ruft clear()
          3. In diesem Moment ruft ein anderer Thread submit() und setzt das
             Event erneut — dieses zweite Setzen geht verloren (clear() folgt
             nach dem wait() erst NACH dem neuen set()).
          Nein — tatsächlich ist die Reihenfolge: wait() → clear() → dequeue.
          Das Race: ein submit() zwischen wait-Ende und clear() setzt das Event;
          clear() löscht es sofort; der neue Task wird trotzdem in der inneren
          Schleife gefunden — also kein Problem für den aktuellen Durchlauf.
          Das echte Problem: submit() zwischen dem Ende der inneren Schleife
          (kein Task mehr → break) und dem nächsten wait() — das Event wird
          gesetzt, aber der Worker ist noch nicht beim wait(). Der folgende
          wait() kehrt sofort zurück (Event war gesetzt), clear() löscht es,
          und die innere Schleife findet den neuen Task. Das ist korrekt.
          ABER: clear() VOR dem ersten dequeue() zu rufen bedeutet, dass ein
          zwischen wait() und clear() eingereichter Task das Event setzt, das
          dann von clear() gelöscht wird. Die innere Schleife läuft den Task
          trotzdem ab — also kein echter Verlust hier.
          Der echte Bug: Event sollte erst NACH dem Dequeue-Versuch geclearet
          werden um sicherzustellen, dass zwischen clear() und dem nächsten
          wait() eingereihte Tasks nicht eine volle Sekunde warten müssen.
          Lösung: clear() direkt vor wait() aufrufen (nicht danach).
        """
        while self._running:
            self._work_ready.clear()      # erst leeren, dann warten
            self._work_ready.wait(timeout=1.0)

            while self._running:
                task = self._dequeue_next()
                if not task:
                    break
                self._execute(task)

    def _dequeue_next(self) -> Optional[Task]:
        """Holt den nächsten PENDING Task vom Heap."""
        with self._lock:
            while self._heap:
                _, _, task = heapq.heappop(self._heap)
                if task.status == TaskStatus.PENDING:
                    return task
        return None

    def _execute(self, task: Task):
        """Führt einen Task aus und aktualisiert seinen Status."""
        # Als aktuell markieren
        with self._lock:
            self._current = task
        task.status     = TaskStatus.RUNNING
        task.started_at = time.monotonic()
        task.progress   = 0
        self._notify()
        logger.info(f"Task gestartet: [{task.id}] '{task.label}'")

        try:
            # Funktion aufrufen — cancel_event als Keyword-Argument
            result = task.func(
                *task.args,
                cancel_event=task.cancel_event,
                **task.kwargs,
            )
            if not task.cancel_event.is_set():
                task.result   = result
                task.status   = TaskStatus.DONE
                task.progress = 100
                logger.info(f"Task fertig: [{task.id}] '{task.label}'")
            else:
                task.status = TaskStatus.CANCELLED
                logger.info(f"Task abgebrochen: [{task.id}] '{task.label}'")

        except Exception as exc:
            if not task.cancel_event.is_set():
                task.error  = str(exc)
                task.status = TaskStatus.FAILED
                logger.error(
                    f"Task fehlgeschlagen: [{task.id}] '{task.label}': {exc}"
                )
            else:
                task.status = TaskStatus.CANCELLED

        finally:
            task.finished_at = time.monotonic()
            with self._lock:
                self._current = None
            self._notify()

    # ── Wiederholung ──────────────────────────────────────────────────────────

    def _schedule_recurring(self, rid: str):
        """Plant den nächsten Durchlauf eines wiederkehrenden Tasks."""
        with self._recurring_lock:
            entry = self._recurring.get(rid)
        if not entry:
            return

        def _fire():
            # Noch registriert?
            with self._recurring_lock:
                e = self._recurring.get(rid)
            if not e:
                return
            # Einreihen
            self.submit(
                e["func"], e["args"], e["kwargs"],
                e["priority"], e["label"],
            )
            # Nächsten Lauf aktualisieren (unter Lock — list_recurring() liest next_run ebenfalls unter Lock)
            with self._recurring_lock:
                if rid in self._recurring:
                    self._recurring[rid]["next_run"] = time.monotonic() + e["interval"]
            self._schedule_recurring(rid)

        # BUG-010: Timer unter Lock anlegen und speichern, DANN starten —
        # so kann remove_recurring() ihn immer sicher canceln.
        with self._recurring_lock:
            if rid not in self._recurring:
                return
            timer = threading.Timer(entry["interval"], _fire)
            timer.daemon = True
            self._recurring[rid]["timer"] = timer
        timer.start()

    # ── Notify ───────────────────────────────────────────────────────────────

    def _notify(self):
        """Ruft den on_update-Callback mit der aktuellen Task-Liste auf."""
        if self._on_update:
            try:
                self._on_update(self.get_all())
            except Exception as exc:
                logger.debug(f"TaskManager on_update Fehler: {exc}")
