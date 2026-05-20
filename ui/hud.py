import logging
import socket
import threading
import time
from typing import Optional

import customtkinter as ctk

from core.jarvis import JarvisCore, State

logger = logging.getLogger("jarvis.hud")

# ── Farben & Texte je State ───────────────────────────────────────────────────

_STATE_COLOR = {
    State.IDLE:           "#1C2C3C",
    State.WAKE_LISTENING: "#00BFFF",
    State.CMD_LISTENING:  "#00FF88",
    State.PROCESSING:     "#FF9500",
    State.SPEAKING:       "#00FF88",
    State.ERROR:          "#FF2060",
}

_STATE_LABEL = {
    State.IDLE:           "STANDBY",
    State.WAKE_LISTENING: "LISTENING",
    State.CMD_LISTENING:  "RECORDING",
    State.PROCESSING:     "THINKING",
    State.SPEAKING:       "SPEAKING",
    State.ERROR:          "ERROR",
}

_PULSE_STATES = {State.WAKE_LISTENING, State.CMD_LISTENING, State.PROCESSING, State.SPEAKING}

_HUD_W = 270
_HUD_H  = 78


class HUD:
    def __init__(self, jarvis: JarvisCore):
        self._jarvis = jarvis
        self._root: Optional[ctk.CTkToplevel] = None
        self._state = State.WAKE_LISTENING
        self._last_msg = ""

        self._dot_lbl:    Optional[ctk.CTkLabel] = None
        self._state_lbl:  Optional[ctk.CTkLabel] = None
        self._msg_lbl:    Optional[ctk.CTkLabel] = None
        self._net_dot:    Optional[ctk.CTkLabel] = None
        self._stop_btn:   Optional[ctk.CTkButton] = None
        self._accent_bar: Optional[ctk.CTkFrame]  = None
        self._pulse_alpha = 1.0
        self._pulse_dir   = -1
        self._hidden = False
        self._online = True

        self._drag_x = 0
        self._drag_y = 0
        self._last_clear_ts = 0.0   # Debounce für "Verlauf löschen"
        self._open_panel_cb = None
        # BUG-072: Stop-Event fuer _connectivity_loop — ersetzt winfo_exists()-
        # Aufruf aus Hintergrund-Thread (tkinter ist nicht thread-sicher)
        self._net_stop = threading.Event()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def run(self):
        """Läuft im Haupt-Thread — blockiert bis Fenster geschlossen."""
        self._root = ctk.CTk()
        self._root.title("")
        self._root.geometry(f"{_HUD_W}x{_HUD_H}+80+80")
        self._root.overrideredirect(True)           # kein Titelbalken
        self._root.attributes("-topmost", True)     # immer im Vordergrund
        self._root.attributes("-alpha", 0.96)
        self._root.configure(fg_color="#030305")

        self._build_ui()
        self._register_callbacks()
        self._root.after(60, self._tick)
        self._root.mainloop()
        # Mainloop beendet → Hintergrund-Thread sauber stoppen (BUG-072)
        self._net_stop.set()

    def update_hotkey_hint(self):
        """Aktualisiert den Hinweistext wenn der Hotkey in den Einstellungen geändert wurde."""
        if self._root and self._msg_lbl and not self._last_msg:
            hotkey = self._jarvis.settings.activation_hotkey.upper()
            self._root.after(0, lambda: self._msg_lbl.configure(
                text=f"[ {hotkey} ]  ready for input...",
                text_color="#102030",
            ))

    def set_open_panel_callback(self, cb):
        """Wird von TrayIcon gesetzt, damit der ⚙-Button das Control Panel öffnet."""
        self._open_panel_cb = cb

    def show(self):
        if self._root:
            self._hidden = False
            self._root.after(0, self._root.deiconify)

    def hide(self):
        if self._root:
            self._hidden = True
            self._root.after(0, self._root.withdraw)

    def is_alive(self) -> bool:
        try:
            return self._root is not None and bool(self._root.winfo_exists())
        except Exception:
            return False

    # ------------------------------------------------------------------
    # UI aufbauen
    # ------------------------------------------------------------------

    def _build_ui(self):
        # ── State-Accent-Bar (oben, ändert Farbe mit State) ──────────
        self._accent_bar = ctk.CTkFrame(
            self._root, height=2, corner_radius=0,
            fg_color=_STATE_COLOR[self._state],
        )
        self._accent_bar.pack(fill="x")

        # ── Äußerer Rahmen ────────────────────────────────────────────
        outer = ctk.CTkFrame(
            self._root,
            fg_color="#060810",
            corner_radius=0,
            border_width=1,
            border_color="#0A1422",
        )
        outer.pack(fill="both", expand=True)

        # Drag-Handling
        outer.bind("<ButtonPress-1>",   self._drag_start)
        outer.bind("<B1-Motion>",       self._drag_move)

        # ── Obere Zeile ───────────────────────────────────────────────
        top = ctk.CTkFrame(outer, fg_color="transparent")
        top.pack(fill="x", padx=10, pady=(7, 0))

        self._dot_lbl = ctk.CTkLabel(
            top, text="◆",
            font=ctk.CTkFont(size=8),
            text_color=_STATE_COLOR[self._state],
            width=12,
        )
        self._dot_lbl.pack(side="left")

        ctk.CTkLabel(
            top, text="JRV//",
            font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
            text_color="#00BFFF",
        ).pack(side="left", padx=(4, 0))

        self._state_lbl = ctk.CTkLabel(
            top,
            text=_STATE_LABEL[self._state],
            font=ctk.CTkFont(family="Consolas", size=9),
            text_color="#1C3040",
        )
        self._state_lbl.pack(side="left", padx=(6, 0))

        self._net_dot = ctk.CTkLabel(
            top, text="◉",
            font=ctk.CTkFont(size=7),
            text_color="#00CC66",
            width=12,
        )
        self._net_dot.pack(side="left", padx=(5, 0))

        # ── Buttons rechts ────────────────────────────────────────────
        _ibtn = dict(
            height=18, corner_radius=3,
            fg_color="#080C14", hover_color="#0C1420",
            border_width=1, border_color="#0A1828",
        )

        ctk.CTkButton(
            top, text="✕", width=18,
            font=ctk.CTkFont(size=9),
            text_color="#2A1A24",
            hover_color="#1A0810",
            fg_color="#080C14",
            corner_radius=3,
            command=self.hide,
        ).pack(side="right", padx=(2, 0))

        ctk.CTkButton(
            top, text="⚙", width=20,
            font=ctk.CTkFont(size=10),
            text_color="#1C4060",
            **_ibtn,
            command=self._on_panel_click,
        ).pack(side="right", padx=(2, 0))

        ctk.CTkButton(
            top, text="🎤", width=20,
            font=ctk.CTkFont(size=9),
            text_color="#1C4060",
            **_ibtn,
            command=self._on_mic_click,
        ).pack(side="right", padx=(2, 0))

        self._stop_btn = ctk.CTkButton(
            top, text="■", width=20,
            font=ctk.CTkFont(size=9),
            text_color="#3A1020",
            hover_color="#140408",
            fg_color="#080C14",
            corner_radius=3,
            command=self._on_stop_click,
        )
        self._stop_btn.pack(side="right", padx=(2, 0))

        # ── Trennlinie ────────────────────────────────────────────────
        ctk.CTkFrame(outer, height=1, fg_color="#0A1422", corner_radius=0).pack(
            fill="x", padx=0, pady=(6, 0)
        )

        # ── Nachrichtenzeile ──────────────────────────────────────────
        hotkey = self._jarvis.settings.activation_hotkey.upper()
        self._msg_lbl = ctk.CTkLabel(
            outer,
            text=f"[ {hotkey} ]  ready for input...",
            font=ctk.CTkFont(family="Consolas", size=9),
            text_color="#102030",
            wraplength=_HUD_W - 18,
            justify="left",
            anchor="w",
        )
        self._msg_lbl.pack(fill="x", padx=10, pady=(4, 6), anchor="w")
        self._msg_lbl.bind("<ButtonPress-1>",  self._drag_start)
        self._msg_lbl.bind("<B1-Motion>",      self._drag_move)

    # ------------------------------------------------------------------
    # Drag-Logik
    # ------------------------------------------------------------------

    def _drag_start(self, event):
        self._drag_x = event.x_root - self._root.winfo_x()
        self._drag_y = event.y_root - self._root.winfo_y()

    def _drag_move(self, event):
        x = event.x_root - self._drag_x
        y = event.y_root - self._drag_y
        self._root.geometry(f"+{x}+{y}")

    # ------------------------------------------------------------------
    # Puls-Animation
    # ------------------------------------------------------------------

    def _tick(self):
        if not self.is_alive():
            return

        state_color = _STATE_COLOR.get(self._state, "#1C2C3C")

        if self._state in _PULSE_STATES:
            self._pulse_alpha += self._pulse_dir * 0.06
            if self._pulse_alpha <= 0.25:
                self._pulse_dir = 1
            elif self._pulse_alpha >= 1.0:
                self._pulse_dir = -1
            faded = self._blend_color(state_color, self._pulse_alpha)
            if self._dot_lbl:
                self._dot_lbl.configure(text_color=faded)
            if self._accent_bar:
                self._accent_bar.configure(fg_color=faded)
            if self._state_lbl:
                self._state_lbl.configure(text_color=self._blend_color(state_color, self._pulse_alpha * 0.7))
        else:
            if self._dot_lbl:
                self._dot_lbl.configure(text_color=state_color)
            if self._accent_bar:
                self._accent_bar.configure(fg_color=state_color)
            if self._state_lbl:
                self._state_lbl.configure(text_color=self._blend_color(state_color, 0.5))

        # Stop-Button: leuchtet auf wenn aktiv
        if self._stop_btn:
            active = self._state in (State.SPEAKING, State.CMD_LISTENING)
            self._stop_btn.configure(
                text_color="#FF2060" if active else "#3A1020",
                hover_color="#200008" if active else "#140408",
            )

        self._root.after(60, self._tick)

    @staticmethod
    def _blend_color(hex_color: str, alpha: float) -> str:
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)
        bg_r, bg_g, bg_b = 6, 8, 16
        r = int(r * alpha + bg_r * (1 - alpha))
        g = int(g * alpha + bg_g * (1 - alpha))
        b = int(b * alpha + bg_b * (1 - alpha))
        return f"#{r:02x}{g:02x}{b:02x}"

    # ------------------------------------------------------------------
    # Internet-Status
    # ------------------------------------------------------------------

    def _check_connectivity(self) -> bool:
        try:
            conn = socket.create_connection(("8.8.8.8", 53), timeout=2)
            conn.close()
            return True
        except OSError:
            return False

    def _connectivity_loop(self):
        # BUG-072: _net_stop.wait() statt is_alive() — kein winfo_exists()-
        # Aufruf aus Hintergrund-Thread (tkinter ist nicht thread-sicher).
        # wait(timeout=10) blockiert 10s ODER kehrt sofort zurück wenn das
        # Event gesetzt wird (mainloop beendet) → sauberer Thread-Exit.
        while not self._net_stop.wait(timeout=10):
            online = self._check_connectivity()
            if online != self._online:
                self._online = online
                color = "#22AA55" if online else "#CC2233"
                if self._root and self._net_dot:
                    self._root.after(0, lambda c=color: self._net_dot.configure(text_color=c))

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def _register_callbacks(self):
        self._jarvis.on_state_change(self._on_state)
        self._jarvis.on_message(self._on_message)

        # Internet-Prüfung im Hintergrund
        t = threading.Thread(target=self._connectivity_loop, daemon=True, name="net-check")
        t.start()

    def _on_state(self, state: State):
        self._state = state
        if self._root:
            color = _STATE_COLOR.get(state, "#444455")
            label = _STATE_LABEL.get(state, "")
            self._root.after(0, lambda: self._apply_state(color, label))

    def _apply_state(self, color: str, label: str):
        try:
            if self._state_lbl and self._state_lbl.winfo_exists():
                self._state_lbl.configure(text=label, text_color=color)
        except Exception:
            pass

    def _on_message(self, role: str, content: str):
        if role in ("system", "confidence"):
            return
        # assistant_partial: Live-Update während Streaming, kein Prefix-Cutoff
        if role == "assistant_partial":
            preview = content[:100] + ("..." if len(content) > 100 else "")
            if self._root:
                def _apply_partial(p=preview):
                    try:
                        if self._msg_lbl and self._msg_lbl.winfo_exists():
                            self._msg_lbl.configure(text=p, text_color="#006080")
                    except Exception:
                        pass
                self._root.after(0, _apply_partial)
            return
        preview = content[:80] + ("..." if len(content) > 80 else "")
        self._last_msg = preview
        if self._root:
            color = "#00BFFF" if role == "assistant" else "#FF9500"
            def _apply_msg(p=preview, c=color):
                try:
                    if self._msg_lbl and self._msg_lbl.winfo_exists():
                        self._msg_lbl.configure(text=p, text_color=c)
                except Exception:
                    pass
            self._root.after(0, _apply_msg)

    def _on_panel_click(self):
        """Control Panel öffnen."""
        if self._open_panel_cb:
            self._open_panel_cb()

    def _on_mic_click(self):
        """Manuell Jarvis aktivieren ohne Wake-Word — kein 'Ja?' nötig."""
        if self._jarvis.state == State.WAKE_LISTENING:
            if self._jarvis._activation_lock.acquire(blocking=False):
                threading.Thread(
                    target=self._jarvis._activate_and_release,
                    kwargs={"silent": True},
                    daemon=True,
                ).start()

    def _on_stop_click(self):
        """Stop-Button: TTS und/oder Lauschen abbrechen."""
        self._jarvis.synthesizer.stop()

    def _on_repeat_click(self):
        """Letzten Befehl wiederholen."""
        cmd = self._jarvis._last_command
        if cmd:
            self._jarvis.send_text_command(cmd)

    def _on_clear_history_click(self):
        """Gesprächsverlauf löschen — mit 2s Debounce."""
        now = time.monotonic()
        if now - self._last_clear_ts < 2.0:
            return
        self._last_clear_ts = now
        self._jarvis.grok.clear_history()
        if self._root and self._msg_lbl:
            self._root.after(0, lambda: self._msg_lbl.configure(
                text="// history cleared", text_color="#004030"
            ))
