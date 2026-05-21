import logging
import threading
from typing import Callable, Optional

import keyboard

logger = logging.getLogger("jarvis.hotkey")


class HotkeyService:
    def __init__(self):
        self._current: Optional[str] = None
        self._callback: Optional[Callable] = None
        self._lock = threading.Lock()

    def register(self, hotkey: str, callback: Callable):
        with self._lock:
            self._unregister_current()
            try:
                keyboard.add_hotkey(hotkey, callback, suppress=False)
                self._current  = hotkey
                self._callback = callback
                logger.info(f"Hotkey registriert: {hotkey}")
            except Exception as e:
                logger.error(f"Hotkey '{hotkey}' konnte nicht registriert werden: {e}")

    def update(self, new_hotkey: str):
        """BUG-NEW-4: Wenn keyboard.add_hotkey(new_hotkey) fehlschlug, wurde der
        alte Hotkey bereits via _unregister_current() entfernt — aber kein neuer
        registriert.  Der Hotkey war danach komplett tot bis zum nächsten update()-
        Aufruf.  Fix: alten Hotkey als Rollback-Kandidaten speichern und bei Fehler
        wiederherstellen.
        """
        with self._lock:
            if self._callback:
                old_hotkey = self._current   # Rollback-Kandidat
                self._unregister_current()
                try:
                    keyboard.add_hotkey(new_hotkey, self._callback, suppress=False)
                    self._current = new_hotkey
                    logger.info(f"Hotkey aktualisiert: {new_hotkey}")
                except Exception as e:
                    logger.error(f"Hotkey '{new_hotkey}' konnte nicht registriert werden: {e}")
                    # Rollback: alten Hotkey wiederherstellen damit Jarvis weiter erreichbar ist
                    if old_hotkey:
                        try:
                            keyboard.add_hotkey(old_hotkey, self._callback, suppress=False)
                            self._current = old_hotkey
                            logger.info(f"Hotkey-Rollback: '{old_hotkey}' wiederhergestellt.")
                        except Exception as rb_err:
                            logger.error(f"Hotkey-Rollback fehlgeschlagen: {rb_err}")

    def _unregister_current(self):
        if self._current:
            try:
                keyboard.remove_hotkey(self._current)
            except Exception:
                pass
            self._current = None

    def stop(self):
        with self._lock:
            self._unregister_current()
