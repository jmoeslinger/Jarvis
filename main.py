"""
 ─────────────────────────────────────────────────────────────
   J A R V I S  —  Persoenlicher KI-Assistent
   Entwickelt mit Grok API, Spracheingabe & System-Tray-Integration
 ─────────────────────────────────────────────────────────────
"""
import os
import sys
import threading
from pathlib import Path

# Projekt-Wurzel zum Python-Suchpfad hinzufuegen
sys.path.insert(0, str(Path(__file__).parent))

from utils.logger import setup_logger

logger = setup_logger()

# ── Single-Instance-Schutz ────────────────────────────────────────────────────
_PID_FILE = Path(os.environ.get("APPDATA", str(Path.home()))) / "Jarvis" / "jarvis.pid"


def _check_single_instance() -> bool:
    """Gibt True zurück wenn diese Instanz starten darf, False wenn bereits eine läuft."""
    _PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    if _PID_FILE.exists():
        try:
            old_pid = int(_PID_FILE.read_text().strip())
            import psutil
            if psutil.pid_exists(old_pid):
                proc = psutil.Process(old_pid)
                # Nur blockieren wenn es wirklich unser Jarvis main.py ist
                name = proc.name().lower()
                try:
                    cmdline = " ".join(proc.cmdline()).lower()
                except Exception:
                    cmdline = ""
                is_jarvis = (
                    "python" in name
                    and ("main.py" in cmdline or "jarvis" in cmdline)
                )
                if is_jarvis:
                    logger.warning(f"Jarvis läuft bereits (PID {old_pid}). Beende.")
                    return False
        except Exception:
            pass  # PID-Datei defekt oder Prozess nicht mehr da
    _PID_FILE.write_text(str(os.getpid()))
    return True


def _remove_pid_file():
    try:
        _PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def main():
    if not _check_single_instance():
        sys.exit(0)

    logger.info("=" * 60)
    logger.info("  Jarvis wird gestartet")
    logger.info("=" * 60)

    from config.settings import Settings
    settings = Settings()

    # ── Ersteinrichtung ───────────────────────────────────────────
    if not settings.is_configured():
        logger.info("Ersteinrichtung erforderlich.")
        from ui.setup_dialog import SetupDialog
        dialog = SetupDialog(settings)
        configured = dialog.run()
        if not configured:
            logger.info("Einrichtung abgebrochen. Jarvis wird beendet.")
            sys.exit(0)
        settings.reload()
        logger.info("Einrichtung abgeschlossen.")

    # ── Autostart sicherstellen ───────────────────────────────────
    from services.system.autostart import Autostart
    autostart = Autostart()
    if settings.autostart_enabled and not autostart.is_enabled():
        autostart.enable()
    elif not settings.autostart_enabled and autostart.is_enabled():
        autostart.disable()

    # ── Jarvis-Kern initialisieren ────────────────────────────────
    from core.jarvis import JarvisCore
    try:
        jarvis = JarvisCore(settings)
    except OSError as e:
        logger.critical(f"Mikrofon nicht gefunden: {e}")
        _show_error(
            "Mikrofon-Fehler",
            f"Kein Mikrofon gefunden:\n{e}\n\nBitte ein Mikrofon anschliessen und Jarvis neu starten.",
        )
        sys.exit(1)

    # ── Jarvis starten (Hintergrund-Thread) ───────────────────────
    jarvis_thread = threading.Thread(target=jarvis.start, daemon=True)
    jarvis_thread.start()

    # ── System-Tray starten (Hintergrund-Thread) ──────────────────
    from ui.tray import TrayIcon
    tray = TrayIcon(jarvis)
    tray_thread = threading.Thread(target=tray.run, daemon=True)
    tray_thread.start()

    logger.info("Jarvis laeuft. System-Tray-Icon aktiv.")
    logger.info('Sage "Hallo Jarvis" um zu beginnen.')

    # ── HUD im Haupt-Thread starten (Tkinter benötigt den Haupt-Thread) ──
    from ui.hud import HUD
    hud = HUD(jarvis)
    tray.set_hud(hud)
    try:
        hud.run()          # blockiert, solange HUD offen ist
    except KeyboardInterrupt:
        logger.info("Abbruch durch Nutzer.")
    finally:
        jarvis.stop()
        _remove_pid_file()

    logger.info("Jarvis beendet.")


def _show_error(title: str, message: str):
    try:
        import customtkinter as ctk
        root = ctk.CTk()
        root.withdraw()
        import tkinter.messagebox as mb
        mb.showerror(title, message)
        root.destroy()
    except Exception:
        print(f"[FEHLER] {title}: {message}", file=sys.stderr)


if __name__ == "__main__":
    main()
