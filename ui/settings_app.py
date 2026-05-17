"""
Jarvis Einstellungen — eigenstaendiger Prozess.
Wird von "Einstellungen.bat" gestartet.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config.settings import Settings
from ui.settings_window import SettingsWindow

if __name__ == "__main__":
    settings = Settings()
    SettingsWindow(settings, jarvis=None).run()
