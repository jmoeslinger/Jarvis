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
