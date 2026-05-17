import logging
import sys
import winreg
from pathlib import Path

logger = logging.getLogger("jarvis.autostart")

_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
_APP_NAME = "Jarvis"


class Autostart:
    def _get_command(self) -> str:
        if getattr(sys, "frozen", False):
            return f'"{Path(sys.executable).resolve()}"'
        main_py = (Path(__file__).parent.parent.parent / "main.py").resolve()
        python = Path(sys.executable).resolve()
        return f'"{python}" "{main_py}"'

    def enable(self) -> bool:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, _REG_PATH, 0, winreg.KEY_SET_VALUE
            ) as key:
                cmd = self._get_command()
                winreg.SetValueEx(key, _APP_NAME, 0, winreg.REG_SZ, cmd)
            logger.info(f"Autostart aktiviert: {cmd}")
            return True
        except OSError as e:
            logger.error(f"Autostart-Aktivierung fehlgeschlagen: {e}")
            return False

    def disable(self) -> bool:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, _REG_PATH, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.DeleteValue(key, _APP_NAME)
            logger.info("Autostart deaktiviert.")
            return True
        except FileNotFoundError:
            return True  # Eintrag existierte ohnehin nicht
        except OSError as e:
            logger.error(f"Autostart-Deaktivierung fehlgeschlagen: {e}")
            return False

    def is_enabled(self) -> bool:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, _REG_PATH, 0, winreg.KEY_READ
            ) as key:
                winreg.QueryValueEx(key, _APP_NAME)
            return True
        except (FileNotFoundError, OSError):
            return False
