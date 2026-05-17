"""
Jarvis Memory — eigenstaendiger Prozess.
Wird von tray.py per subprocess gestartet.
"""
import sys
from pathlib import Path

# Sicherstellen, dass das Jarvis-Verzeichnis im Python-Pfad ist
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from services.memory.memory_store import MemoryStore
from ui.memory_window import MemoryWindow

if __name__ == "__main__":
    memory = MemoryStore()
    MemoryWindow(memory).run()
