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
        with self._lock:
            if self._callback:
                # _unregister_current is called inside register() which also takes the lock,
                # so we must call it directly here to avoid double-locking.
                self._unregister_current()
                try:
                    keyboard.add_hotkey(new_hotkey, self._callback, suppress=False)
                    self._current = new_hotkey
                    logger.info(f"Hotkey aktualisiert: {new_hotkey}")
                except Exception as e:
                    logger.error(f"Hotkey '{new_hotkey}' konnte nicht registriert werden: {e}")

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
