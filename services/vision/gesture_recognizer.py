"""
Gestenerkennung fuer Jarvis via MediaPipe Hands.

Erkennt 7 Standardgesten aus Kamera-Frames und loest konfigurierbare
Jarvis-Befehle aus sobald eine Geste lang genug gehalten wird (Hold-to-Trigger).

Gesten-IDs:
    open_palm   ✋ Alle vier Finger ausgestreckt
    fist        ✊ Alle Finger eingerollt (Faust)
    thumbs_up   👍 Daumen nach oben, Rest eingerollt
    peace       ✌ Zeige- + Mittelfinger ausgestreckt
    pointing    ☝ Nur Zeigefinger ausgestreckt
    rock        🤘 Zeige- + kleiner Finger, Rest eingerollt
    open_hand_L ✋ Linke offene Hand (Spiegelung von open_palm)

Benoetigt:  pip install mediapipe
Graceful Degradation: wenn MediaPipe nicht installiert ist, wird
    GestureRecognizer.available == False, kein Absturz.

Verwendung:
    gr = GestureRecognizer(
        on_trigger=lambda gesture, cmd: jarvis.send_text_command(cmd),
        get_mappings=lambda: settings.gesture_mappings,
        hold_seconds=1.5,
    )
    gr.start(shared_frame_provider)   # shared_frame_provider() liefert np.ndarray|None
    gr.stop()
"""

import logging
import threading
import time
from typing import Callable, Dict, Optional

logger = logging.getLogger("jarvis.gesture")

# ── Geste-Metadaten ───────────────────────────────────────────────────────────

GESTURE_LABELS: Dict[str, str] = {
    "open_palm":  "✋ Offene Hand",
    "fist":       "✊ Faust",
    "thumbs_up":  "👍 Daumen hoch",
    "peace":      "✌ Victory / Peace",
    "pointing":   "☝ Zeigefinger",
    "rock":       "🤘 Rock-Zeichen",
}

# Standard-Befehle (leer = kein Befehl)
DEFAULT_MAPPINGS: Dict[str, str] = {
    "open_palm":  "",
    "fist":       "",
    "thumbs_up":  "",
    "peace":      "",
    "pointing":   "",
    "rock":       "",
}


def mp_available() -> bool:
    """Gibt True zurueck wenn MediaPipe installiert ist."""
    try:
        import mediapipe  # noqa: F401
        return True
    except ImportError:
        return False


# ── Geste-Klassifikation ──────────────────────────────────────────────────────

def _classify_gesture(hand_landmarks) -> Optional[str]:
    """
    Klassifiziert eine Hand-Geste aus 21 MediaPipe-Landmarks.
    Gibt eine Geste-ID zurueck oder None wenn keine eindeutige Geste erkannt.

    Landmark-Indizes (MediaPipe Hands):
        Daumen:      1-4   (MCP, IP, TIP je 2,3,4)
        Zeigefinger: 5-8
        Mittelfinger: 9-12
        Ringfinger:  13-16
        Kleiner:     17-20
    """
    lm = hand_landmarks.landmark

    def _extended(tip_idx: int, pip_idx: int) -> bool:
        """Finger ausgestreckt: Spitze hoeher als Mittelgelenk (y sinkt nach oben)."""
        return lm[tip_idx].y < lm[pip_idx].y

    index_ext  = _extended(8,  6)   # Zeigefinger
    middle_ext = _extended(12, 10)  # Mittelfinger
    ring_ext   = _extended(16, 14)  # Ringfinger
    pinky_ext  = _extended(20, 18)  # Kleiner Finger

    # Daumen: Spitze ueber IP-Gelenk → nach oben gestreckt
    thumb_up_ext = lm[4].y < lm[3].y

    fingers_folded = not index_ext and not middle_ext and not ring_ext and not pinky_ext

    # ── open_palm: alle vier Finger ausgestreckt ──────────────────────────────
    if index_ext and middle_ext and ring_ext and pinky_ext:
        return "open_palm"

    # ── thumbs_up: alle eingerollt + Daumen nach oben ────────────────────────
    if fingers_folded and thumb_up_ext:
        return "thumbs_up"

    # ── fist: alle eingerollt, Daumen nicht nach oben ────────────────────────
    if fingers_folded and not thumb_up_ext:
        return "fist"

    # ── peace: Zeige + Mittel ausgestreckt, Ring + Klein eingerollt ──────────
    if index_ext and middle_ext and not ring_ext and not pinky_ext:
        return "peace"

    # ── pointing: nur Zeigefinger ausgestreckt ────────────────────────────────
    if index_ext and not middle_ext and not ring_ext and not pinky_ext:
        return "pointing"

    # ── rock: Zeige + Klein ausgestreckt, Mittel + Ring eingerollt ───────────
    if index_ext and not middle_ext and not ring_ext and pinky_ext:
        return "rock"

    return None


# ── GestureRecognizer ─────────────────────────────────────────────────────────

class GestureRecognizer:
    """
    Liest Frames aus einem shared_frame_provider, erkennt Gesten per MediaPipe
    und feuert on_trigger sobald eine Geste hold_seconds lang gehalten wird.
    """

    def __init__(
        self,
        on_trigger:    Callable[[str, str], None],
        get_mappings:  Callable[[], Dict[str, str]],
        hold_seconds:  float = 1.5,
        fps_limit:     int   = 15,
    ):
        """
        on_trigger(gesture_id, command): wird aufgerufen wenn Trigger ausgeloest
        get_mappings(): gibt aktuelles {gesture_id: command} Dict zurueck
        hold_seconds: wie lange eine Geste gehalten werden muss
        fps_limit: max. Frames pro Sekunde (spart CPU)
        """
        self._on_trigger   = on_trigger
        self._get_mappings = get_mappings
        self._hold_seconds = hold_seconds
        self._fps_limit    = fps_limit

        self._running  = False
        self._thread:  Optional[threading.Thread] = None

        # Aktuell erkannte Geste + Zeitpunkt seit wann sie gehalten wird
        self._current_gesture:     Optional[str] = None
        self._gesture_since:       float         = 0.0
        self._last_trigger_gesture: Optional[str] = None
        self._last_trigger_time:   float          = 0.0

        # Fuer UI: letzter Erkennungs-Frame-Snapshot
        self._last_gesture:       Optional[str] = None
        self._hold_progress:      float         = 0.0   # 0.0 – 1.0

        # Frame-Provider (wird per start() gesetzt)
        self._get_frame: Optional[Callable] = None

        self.available = mp_available()
        if not self.available:
            logger.warning(
                "GestureRecognizer: MediaPipe nicht installiert. "
                "Bitte 'pip install mediapipe' ausfuehren."
            )

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def current_gesture(self) -> Optional[str]:
        """Aktuell erkannte Geste (oder None)."""
        return self._current_gesture

    @property
    def hold_progress(self) -> float:
        """Fortschritt des Hold-Timers (0.0–1.0)."""
        return min(1.0, self._hold_progress)

    def start(self, frame_provider: Callable) -> bool:
        """
        Startet den Erkennungs-Loop in einem Hintergrund-Thread.
        frame_provider() muss einen numpy-Frame (BGR, uint8) oder None liefern.
        Gibt False zurueck wenn MediaPipe nicht verfuegbar ist.
        """
        if not self.available:
            return False
        if self._running:
            return True

        self._get_frame = frame_provider
        self._running   = True
        self._thread    = threading.Thread(
            target=self._loop,
            daemon=True,
            name="GestureRecognizer",
        )
        self._thread.start()
        logger.info("GestureRecognizer gestartet.")
        return True

    def stop(self):
        """Stoppt den Erkennungs-Loop."""
        self._running = False
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=2.0)
        self._thread  = None
        self._current_gesture = None
        self._hold_progress   = 0.0
        logger.info("GestureRecognizer gestoppt.")

    def update_hold_seconds(self, seconds: float):
        """Aktualisiert die Hold-Dauer live (kein Neustart noetig)."""
        self._hold_seconds = max(0.3, float(seconds))

    # ── Interner Loop ─────────────────────────────────────────────────────────

    def _loop(self):
        """Haupt-Loop: Frame holen → Geste erkennen → Trigger pruefen."""
        try:
            import mediapipe as mp
        except ImportError:
            logger.error("MediaPipe Import fehlgeschlagen im Loop.")
            self._running = False
            return

        hands_solution = mp.solutions.hands
        min_dt = 1.0 / self._fps_limit

        with hands_solution.Hands(
            static_image_mode=False,
            max_num_hands=1,
            min_detection_confidence=0.65,
            min_tracking_confidence=0.55,
        ) as hands:
            while self._running:
                t0 = time.monotonic()

                frame = self._get_frame() if self._get_frame else None
                if frame is None:
                    time.sleep(0.05)
                    continue

                try:
                    import cv2
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    result = hands.process(rgb)
                except Exception as e:
                    logger.debug(f"Gesture Frame-Fehler: {e}")
                    time.sleep(0.05)
                    continue

                now = time.monotonic()

                if result.multi_hand_landmarks:
                    gesture = _classify_gesture(result.multi_hand_landmarks[0])
                else:
                    gesture = None

                self._update_state(gesture, now)

                # FPS-Limiter
                elapsed = time.monotonic() - t0
                sleep_t = min_dt - elapsed
                if sleep_t > 0:
                    time.sleep(sleep_t)

    def _update_state(self, gesture: Optional[str], now: float):
        """
        Zustandsmaschine: verfolgt aktuelle Geste + Hold-Timer.
        Feuert on_trigger wenn Hold-Dauer erreicht.
        """
        if gesture != self._current_gesture:
            # Neue (oder keine) Geste → Timer zuruecksetzen
            self._current_gesture = gesture
            self._gesture_since   = now if gesture else 0.0
            self._hold_progress   = 0.0
        elif gesture is not None:
            # Gleiche Geste weiter gehalten
            held = now - self._gesture_since
            self._hold_progress = held / self._hold_seconds

            if held >= self._hold_seconds:
                # Trigger-Debounce: gleiche Geste nicht sofort nochmal ausloesen
                # (min. 3s Cooldown nach jedem Trigger)
                since_last = now - self._last_trigger_time
                same_as_last = (gesture == self._last_trigger_gesture)

                if not same_as_last or since_last >= 3.0:
                    self._fire_trigger(gesture)
                    self._last_trigger_gesture = gesture
                    self._last_trigger_time    = now
                    self._gesture_since        = now   # Timer fuer naechsten Hold zurueck
                    self._hold_progress        = 0.0
        else:
            self._hold_progress = 0.0

        self._last_gesture = gesture

    def _fire_trigger(self, gesture: str):
        """Liest das Mapping und ruft on_trigger auf wenn ein Befehl zugeordnet ist."""
        try:
            mappings = self._get_mappings()
            command  = mappings.get(gesture, "").strip()
            if not command:
                logger.debug(f"Geste '{gesture}' erkannt — kein Befehl zugeordnet.")
                return
            label = GESTURE_LABELS.get(gesture, gesture)
            logger.info(f"Geste ausgeloest: {label} → '{command}'")
            self._on_trigger(gesture, command)
        except Exception as e:
            logger.error(f"Gesture Trigger Fehler: {e}")
