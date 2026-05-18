"""
Proaktiver Vorschlags-Engine — Hintergrund-Daemon der Vorschlaege macht.

Regeln (zeitbasiert + kontextbasiert):
  - Morgens (07-09 Uhr):  Tagesplan-Erinnerung falls Aufgaben offen
  - Mittags (12-13 Uhr):  Pausen-Erinnerung
  - Abends  (18-20 Uhr):  Zusammenfassung des Tages anbieten
  - Alle 2h (wenn idle):  Auf offene Aufgaben hinweisen

Voraussetzungen:
  - Jarvis muss idle sein (State.WAKE_LISTENING)
  - Mindestens 10 Minuten seit dem letzten Vorschlag
"""
import logging
import threading
import time
from datetime import datetime
from typing import Callable, Optional

logger = logging.getLogger("jarvis.proactive")

# Mindestabstand zwischen Vorschlaegen (Sekunden)
_MIN_INTERVAL_S = 600   # 10 Minuten


class ProactiveEngine:
    """
    Hintergrund-Daemon der regelbasierte Vorschlaege generiert.
    Wird ueber start()/stop() gesteuert.
    """

    def __init__(
        self,
        on_suggestion:   Callable[[str], None],
        is_idle:         Callable[[], bool],
        get_open_tasks:  Optional[Callable[[], int]] = None,
    ):
        """
        on_suggestion:  Callback wenn ein Vorschlag bereit ist (Text -> Jarvis spricht ihn)
        is_idle:        Gibt True zurueck wenn Jarvis gerade wartet (nicht spricht/hoert)
        get_open_tasks: Optional — gibt Anzahl offener Tagesaufgaben zurueck
        """
        self._on_suggestion  = on_suggestion
        self._is_idle        = is_idle
        self._get_open_tasks = get_open_tasks

        self._running    = False
        self._thread:    Optional[threading.Thread] = None
        self._stop_event = threading.Event()          # BUG-028: sofortiger stop()
        self._last_suggestion_t: float = 0.0

    def start(self):
        if self._running:
            return
        # BUG-079: create a fresh Event per start() so each loop-thread owns its
        # own stop signal.  stop() sets self._stop_event, which always points to
        # the event of the currently running thread — no cross-thread interference.
        self._running = True
        stop_ev = threading.Event()
        self._stop_event = stop_ev
        self._thread = threading.Thread(
            target=self._loop, args=(stop_ev,), daemon=True, name="ProactiveEngine"
        )
        self._thread.start()
        logger.info("ProactiveEngine gestartet.")

    def stop(self):
        self._running = False
        self._stop_event.set()  # BUG-028: unterbricht sofort jeden sleep()
        logger.info("ProactiveEngine gestoppt.")

    # ── Interner Loop ──────────────────────────────────────────────────────────

    def _loop(self, stop_ev: threading.Event):
        # Startverzoegerung: erst nach 2 Minuten aktiv werden (BUG-028: unterbrechbar)
        # BUG-079: stop_ev is the per-invocation event captured at start() time.
        # Using a local parameter (not self._stop_event) prevents a rapid
        # stop()+start() sequence from silently clearing the signal that this
        # thread is still waiting on — which would have caused two _loop threads
        # to run simultaneously and fire duplicate suggestions.
        stop_ev.wait(timeout=120)
        if not self._running:
            return

        while self._running:
            try:
                self._tick()
            except Exception as e:
                logger.debug(f"ProactiveEngine Tick-Fehler: {e}")
            # Alle 5 Minuten pruefen — sofort unterbrechbar (BUG-028)
            # No stop_ev.clear() here: each start() creates a fresh Event, so
            # there is nothing stale to clear in subsequent iterations.
            stop_ev.wait(timeout=300)

    def _tick(self):
        now = datetime.now()
        hour = now.hour

        # Cooldown pruefen
        elapsed = time.monotonic() - self._last_suggestion_t
        if elapsed < _MIN_INTERVAL_S:
            return

        # Jarvis muss idle sein
        if not self._is_idle():
            return

        suggestion = self._pick_suggestion(hour)
        if suggestion:
            self._last_suggestion_t = time.monotonic()
            logger.info(f"Proaktiver Vorschlag: {suggestion[:60]}")
            self._on_suggestion(suggestion)

    def _pick_suggestion(self, hour: int) -> Optional[str]:
        """
        Waehlt einen passenden Vorschlag basierend auf der Tageszeit.
        Gibt None zurueck wenn kein Vorschlag passend ist.
        """
        open_tasks = 0
        if self._get_open_tasks:
            try:
                open_tasks = self._get_open_tasks()
            except Exception:
                pass

        # Morgens: Tagesplan
        if 7 <= hour < 9:
            if open_tasks > 0:
                n = open_tasks
                s = "n" if n != 1 else ""
                return f"Guten Morgen! Du hast {n} Aufgabe{s} fuer heute. Soll ich sie vorlesen?"
            return "Guten Morgen! Soll ich dir beim Planen des Tages helfen?"

        # Mittags: Pause
        if 12 <= hour < 13:
            return "Es ist Mittagszeit. Vergiss nicht, eine Pause zu machen!"

        # Abends: Rueckblick
        if 18 <= hour < 20:
            if open_tasks > 0:
                n = open_tasks
                s = "n" if n != 1 else ""
                return f"Der Tag neigt sich dem Ende zu. Du hast noch {n} offene Aufgabe{s}. Soll ich sie dir zeigen?"
            return "Guter Abend! Kann ich dir noch etwas helfen?"

        # Generell: offene Aufgaben
        if open_tasks >= 3 and (9 <= hour < 18):
            return f"Nur zur Erinnerung: Du hast {open_tasks} offene Aufgaben. Soll ich dir deinen Tagesplan zeigen?"

        return None
