import logging
from typing import Optional

import pystray

from core.jarvis import JarvisCore, State
from ui.assets.icon_generator import get_icon_image

logger = logging.getLogger("jarvis.tray")

_TRAY_TITLE = {
    State.IDLE:           "Jarvis — Inaktiv",
    State.WAKE_LISTENING: "Jarvis — Hoert zu",
    State.CMD_LISTENING:  "Jarvis — Nimmt auf...",
    State.PROCESSING:     "Jarvis — Denkt...",
    State.SPEAKING:       "Jarvis — Spricht",
    State.ERROR:          "Jarvis — Fehler",
}


class TrayIcon:
    def __init__(self, jarvis: JarvisCore):
        self._jarvis = jarvis
        self._icon: Optional[pystray.Icon] = None
        self._hud = None

        # Control Panel — Haupt-UI für alle Funktionen
        from ui.control_panel import ControlPanel
        self._panel = ControlPanel(jarvis)

        self._jarvis.on_state_change(self._on_state)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def set_hud(self, hud):
        """Wird von main.py aufgerufen, nachdem das HUD erstellt wurde."""
        self._hud = hud
        self._panel.set_hud(hud)
        hud.set_open_panel_callback(self._panel.show)
        # Im Hintergrundmodus Panel nicht automatisch öffnen
        if not self._jarvis.settings.background_mode:
            self._panel.show()

    def run(self):
        icon_img = get_icon_image(64, "wake_listening")
        self._icon = pystray.Icon(
            name="Jarvis",
            icon=icon_img,
            title="Jarvis — Hoert zu",
            menu=self._build_menu(),
        )
        self._icon.run()

    def stop(self):
        if self._icon:
            self._icon.stop()

    # ------------------------------------------------------------------
    # Menü — minimal, Rest im Control Panel
    # ------------------------------------------------------------------

    def _build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem("Jarvis öffnen",  self._cmd_open_panel, default=True),
            pystray.MenuItem("HUD anzeigen",   self._cmd_show_hud),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Beenden",        self._cmd_quit),
        )

    def _cmd_open_panel(self, icon=None, item=None):
        self._panel.show()

    def _cmd_show_hud(self, icon=None, item=None):
        if self._hud and self._hud.is_alive():
            self._hud.show()

    def _cmd_quit(self, icon=None, item=None):
        logger.info("Jarvis wird beendet...")
        self._jarvis.stop()
        # HUD (Tkinter-Hauptfenster) schließen damit der Haupt-Thread beendet wird
        if self._hud and hasattr(self._hud, "_root") and self._hud._root:
            try:
                self._hud._root.after(0, self._hud._root.destroy)
            except Exception:
                pass
        if self._icon:
            self._icon.stop()

    # ------------------------------------------------------------------
    # State-Listener → Tray-Icon + Titel aktualisieren
    # ------------------------------------------------------------------

    def _on_state(self, state: State):
        if self._icon:
            try:
                self._icon.icon  = get_icon_image(64, state.name.lower())
                self._icon.title = _TRAY_TITLE.get(state, "Jarvis")
            except Exception as e:
                logger.debug(f"Tray-Icon Update: {e}")
