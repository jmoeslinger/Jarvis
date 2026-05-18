"""
Kamera-Service — OpenCV Webcam-Zugriff und Base64-JPEG-Export.
Faellt zurueck auf Bildschirmfoto wenn keine Kamera verfuegbar ist.
"""
import base64
import logging
import threading
from typing import List, Optional

logger = logging.getLogger("jarvis.vision.camera")


class CameraService:
    def __init__(self, camera_index: int = 0):
        self._camera_index = camera_index
        self._lock = threading.Lock()

    # ── Oeffentliche API ──────────────────────────────────────────────────────

    def is_available(self) -> bool:
        """Prueft ob mindestens eine Kamera verfuegbar ist.
        BUG-041: Lock verwenden damit kein gleichzeitiger Zugriff mit capture_base64() entsteht.
        """
        try:
            import cv2
            with self._lock:
                cap = cv2.VideoCapture(self._camera_index, cv2.CAP_DSHOW)
                try:
                    ok = cap.isOpened()
                finally:
                    cap.release()
            return ok
        except Exception:
            return False

    def capture_base64(self, quality: int = 80) -> Optional[str]:
        """
        Nimmt ein Foto auf und gibt es als Base64-JPEG-String zurueck.
        Gibt None zurueck wenn die Kamera nicht erreichbar ist.
        Versucht zuerst die konfigurierte Kamera, dann Index 0.
        BUG-053: Reihenfolge explizit erzwingen statt set{} zu verwenden.
        """
        # BUG-053: set{} verliert Reihenfolge — konfigurierte Kamera immer zuerst
        if self._camera_index == 0:
            indices = [0]
        else:
            indices = [self._camera_index, 0]
        for idx in indices:
            b64 = self._capture_from(idx, quality)
            if b64:
                return b64
        logger.error("Keine Kamera verfuegbar fuer Aufnahme.")
        return None

    def list_cameras(self) -> List[int]:
        """Gibt Liste verfuegbarer Kamera-Indizes (0-4) zurueck.
        BUG-057: cap.release() in try/finally sicherstellen.
        """
        available = []
        try:
            import cv2
            for i in range(5):
                cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
                try:
                    if cap.isOpened():
                        available.append(i)
                finally:
                    cap.release()
        except Exception:
            pass
        return available

    def capture_video_frames(
        self,
        duration_seconds: float = 3.0,
        max_frames: int = 9,
        quality: int = 75,
    ) -> List[str]:
        """
        Nimmt mehrere Frames ueber duration_seconds auf und gibt sie als
        Base64-JPEG-Liste zurueck (gleichmaessig ueber die Dauer verteilt).
        Oeffnet die Kamera einmalig und liest Frames in einem Gleichmaessig-
        verteilten Zeitraster — schneller als mehrfaches Oeffnen/Schliessen.

        BUG-073 fix: Lock is held only while opening the VideoCapture handle.
        The frame-reading loop (which contains time.sleep() calls spanning the
        full recording duration) runs lock-free.  This prevents is_available()
        and capture_base64() from blocking for up to 30 seconds.
        """
        import time as _time
        try:
            import cv2
        except ImportError:
            logger.error("OpenCV nicht installiert.")
            return []

        duration_seconds = max(0.5, min(30.0, float(duration_seconds)))
        max_frames       = max(1, min(30, int(max_frames)))
        interval         = duration_seconds / max_frames

        frames_b64: List[str] = []

        # BUG-073: Acquire lock only to open the camera, then release immediately.
        # The cap handle is our exclusive resource for the rest of the method.
        with self._lock:
            cap = cv2.VideoCapture(self._camera_index, cv2.CAP_DSHOW)
            opened = cap.isOpened()

        if not opened:
            cap.release()
            logger.error(f"Kamera {self._camera_index} fuer Video nicht erreichbar.")
            return []

        # Frame-reading loop — no lock held here (avoids blocking other callers)
        try:
            # Auto-Fokus stabilisieren
            for _ in range(5):
                cap.read()

            t_start = _time.monotonic()
            encode_params = [cv2.IMWRITE_JPEG_QUALITY, quality]

            for i in range(max_frames):
                # Auf naechsten Frame-Zeitpunkt warten
                target = t_start + i * interval
                now    = _time.monotonic()
                if target > now:
                    _time.sleep(target - now)

                ret, frame = cap.read()
                if not ret or frame is None:
                    continue

                ok, buf = cv2.imencode(".jpg", frame, encode_params)
                if ok:
                    frames_b64.append(
                        base64.b64encode(buf.tobytes()).decode("utf-8")
                    )
        finally:
            cap.release()

        logger.info(
            f"Video aufgenommen: {len(frames_b64)}/{max_frames} Frames, "
            f"{duration_seconds:.1f}s, Kamera {self._camera_index}"
        )
        return frames_b64

    def set_camera_index(self, index: int):
        self._camera_index = index

    # ── Intern ────────────────────────────────────────────────────────────────

    def _capture_from(self, index: int, quality: int) -> Optional[str]:
        try:
            import cv2

            # BUG-042: cap.release() in try/finally — kein Leak bei Exception
            with self._lock:
                cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
                try:
                    if not cap.isOpened():
                        return None

                    # Mehrere Frames lesen damit der Auto-Fokus / Weissabgleich stabil ist
                    frame = None
                    for _ in range(8):
                        ret, f = cap.read()
                        if ret:
                            frame = f
                finally:
                    cap.release()

            if frame is None:
                return None

            encode_params = [cv2.IMWRITE_JPEG_QUALITY, quality]
            ok, buffer = cv2.imencode(".jpg", frame, encode_params)
            if not ok:
                return None

            b64 = base64.b64encode(buffer.tobytes()).decode("utf-8")
            logger.info(f"Kamera {index}: Foto aufgenommen ({len(b64)} B64-Zeichen)")
            return b64

        except ImportError:
            logger.error("OpenCV nicht installiert — bitte 'pip install opencv-python' ausfuehren.")
            return None
        except Exception as e:
            logger.debug(f"Kamera {index} Fehler: {e}")
            return None
