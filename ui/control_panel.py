"""
Jarvis Control Panel — läuft als CTkToplevel im HUD-Mainloop.
Erscheint in der Windows-Taskbar und ist per Alt+Tab erreichbar.
"""
import logging
import subprocess
import sys
import threading
from pathlib import Path
from typing import Dict, List, Optional

import customtkinter as ctk

from core.jarvis import State

logger = logging.getLogger("jarvis.panel")

# ── Cyberpunk-Palette ─────────────────────────────────────────────────────────
_C_BG      = "#040608"   # main background
_C_CARD    = "#06080E"   # card / panel bg
_C_BORDER  = "#0A1828"   # subtle border
_C_BODY    = "#050709"   # scrollable body bg
_C_CYAN    = "#00BFFF"   # main accent
_C_GREEN   = "#00FF88"   # on / active
_C_ORANGE  = "#FF9500"   # warm / processing
_C_PURPLE  = "#8844FF"   # creative / personality
_C_RED     = "#FF2060"   # error / stop
_C_TEXT    = "#C8D8F0"   # primary text
_C_DIM     = "#0A2030"   # dim / placeholder text
_C_DIMMED  = "#1C3040"   # slightly brighter dim

# ── Button presets ────────────────────────────────────────────────────────────
def _btn_inactive():
    return dict(fg_color=_C_BG, hover_color="#060E1A",
                border_width=1, border_color=_C_BORDER,
                text_color=_C_DIM)

def _btn_active(accent: str, bg: str):
    return dict(fg_color=bg, hover_color=bg,
                border_width=1, border_color=accent,
                text_color=accent)

_ACTIVE_CYAN   = lambda: _btn_active(_C_CYAN,   "#001A28")
_ACTIVE_GREEN  = lambda: _btn_active(_C_GREEN,  "#001A14")
_ACTIVE_ORANGE = lambda: _btn_active(_C_ORANGE, "#1A0A00")
_ACTIVE_PURPLE = lambda: _btn_active(_C_PURPLE, "#100028")
_ACTIVE_RED    = lambda: _btn_active(_C_RED,    "#1A0010")

# ── State info (matches HUD labels) ──────────────────────────────────────────
_STATE_INFO = {
    "idle":           (_C_DIMMED, "STANDBY"),
    "wake_listening": (_C_CYAN,   "LISTENING"),
    "cmd_listening":  (_C_GREEN,  "RECORDING"),
    "processing":     (_C_ORANGE, "THINKING"),
    "speaking":       (_C_GREEN,  "SPEAKING"),
    "error":          (_C_RED,    "ERROR"),
}

_MODE_LABELS = {
    "auto":   "AUTO",
    "online": "ONLINE",
    "local":  "LOCAL",
}


class ControlPanel:
    """
    Steuerpanel als CTkToplevel im HUD-Mainloop (Hauptthread).
    Kein eigener Thread → kein Multi-CTk-Konflikt.
    show() retry-loopt bis der HUD-Root verfügbar ist.
    """

    def __init__(self, jarvis):
        self._jarvis = jarvis
        self._hud = None
        self._hud_root = None          # Tk-Root des HUD (Hauptthread)
        self._win: Optional[ctk.CTkToplevel] = None

        # Sub-Windows
        self._memory_proc: Optional[subprocess.Popen] = None
        self._chat_thread: Optional[threading.Thread] = None
        self._chat_window = None   # Referenz auf aktives ChatWindow (für erneutes Öffnen)
        self._settings_open = False
        self._enroll_running = False

        # Widgets
        self._dot_state: Optional[ctk.CTkLabel] = None
        self._lbl_state: Optional[ctk.CTkLabel] = None
        self._lbl_provider: Optional[ctk.CTkLabel] = None
        self._mode_btns: Dict[str, ctk.CTkButton] = {}
        self._len_btns: Dict[str, ctk.CTkButton] = {}
        self._tone_btns: Dict[str, ctk.CTkButton] = {}
        self._toggle_btns: Dict[str, ctk.CTkButton] = {}
        self._confidence_dots: Optional[ctk.CTkLabel] = None
        self._confidence_val: Optional[ctk.CTkLabel] = None

        # Task-Queue Widgets
        self._task_lbl_current: Optional[ctk.CTkLabel] = None
        self._task_progress: Optional[ctk.CTkProgressBar] = None
        self._task_lbl_queue: Optional[ctk.CTkLabel] = None

        # Automatisierung Widgets
        self._recurring_frame: Optional[ctk.CTkScrollableFrame] = None
        self._recurring_rows: Dict[str, ctk.CTkFrame] = {}   # rid → Frame

        # Sprach-Analyse Buttons
        self._analyse_btns: Dict[str, ctk.CTkButton] = {}
        self._enroll_btn: Optional[ctk.CTkButton] = None
        self._enroll_lbl: Optional[ctk.CTkLabel] = None

        # Hörmodus-Buttons
        self._hoehmodus_btns: Dict[str, ctk.CTkButton] = {}

        # Persoenlichkeit & Vision Buttons
        self._personality_btns: dict = {}
        self._vision_btns: dict = {}
        self._adaption_btns: dict = {}

        # Kamera-Fenster
        self._camera_window = None

        # BUG-022: Verhindert mehrere parallele Retry-Ketten in show()
        self._show_pending = False

        # Debounce / Throttle: verhindert Scroll-Lag durch akkumulierte after(0)-Callbacks
        self._settings_refresh_id: Optional[str] = None   # after()-ID fuer Settings-Debounce
        self._task_update_id:      Optional[str] = None   # after()-ID fuer Task-Throttle
        self._state_update_id:     Optional[str] = None   # after()-ID fuer State-Debounce
        self._pending_state = None                         # letzter State fuer State-Debounce

        self._jarvis.on_state_change(self._on_state)
        self._jarvis.on_message(self._on_message)
        self._jarvis.on_task_update(self._on_task_update)
        self._jarvis.on_settings_change(self._on_settings_changed)

    # ── Öffentlich ────────────────────────────────────────────────────────────

    def set_hud(self, hud):
        self._hud = hud

    def show(self, _retries: int = 0):
        """
        Panel öffnen/in Vordergrund bringen.
        Wartet per Timer bis der HUD-Root bereit ist (maximal 10 Sekunden).
        BUG-022: _show_pending verhindert mehrere parallele Retry-Ketten.
        """
        root = getattr(self._hud, "_root", None)
        if root and root.winfo_exists():
            self._show_pending = False
            self._hud_root = root
            root.after(0, self._open_or_show)
        elif _retries == 0:
            # Erste show()-Anfrage: nur starten wenn keine Kette läuft
            if self._show_pending:
                return
            self._show_pending = True
            t = threading.Timer(0.3, self.show, kwargs={"_retries": 1})
            t.daemon = True
            t.start()
        elif _retries < 33:   # max. 33 × 0.3s ≈ 10 Sekunden
            t = threading.Timer(0.3, self.show, kwargs={"_retries": _retries + 1})
            t.daemon = True
            t.start()
        else:
            self._show_pending = False
            logger.warning("ControlPanel.show(): HUD-Root nicht verfügbar nach 10s.")

    def is_alive(self) -> bool:
        return self._win is not None and self._win.winfo_exists()

    # ── Fenster-Lifecycle ─────────────────────────────────────────────────────

    def _open_or_show(self):
        """Läuft im Hauptthread."""
        if self._win and self._win.winfo_exists():
            self._win.state("normal")
            self._win.deiconify()
            self._win.lift()
            self._win.focus_force()
        else:
            self._create_window()

    def _create_window(self):
        """Erstellt das CTkToplevel im HUD-Mainloop."""
        self._win = ctk.CTkToplevel(self._hud_root)
        self._win.title("Jarvis")
        self._win.resizable(False, False)
        self._win.protocol("WM_DELETE_WINDOW", self._on_close)

        # Sichere Startposition nahe oben-links (vermeidet DPI-Probleme)
        w, h = 310, 660
        self._win.geometry(f"{w}x{h}+100+100")

        self._build_ui()

        # CTkToplevel ruft intern withdraw() auf.
        # update() → alle ausstehenden Tk-Events abarbeiten (inkl. CTkToplevel-Init)
        # Layout-Berechnungen abschliessen während Fenster noch withdrawn ist
        self._win.update()

        # WS_EX_APPWINDOW VOR deiconify() setzen → kein visueller Glitch durch
        # nachgelagerten SetWindowPos(SWP_FRAMECHANGED) bei sichtbarem Fenster
        self._force_taskbar()

        # Fenster einmalig sauber anzeigen
        self._win.state("normal")
        self._win.deiconify()
        self._win.lift()
        self._win.focus_force()

        # Sicherheitsprüfung nach kurzer Verzögerung
        self._win.after(400, self._ensure_visible)

    def _on_close(self):
        if self._win:
            self._win.withdraw()

    def _ensure_visible(self):
        """Letzte Sicherheitsprüfung: Falls das Fenster nach _force_taskbar
        ikonifiziert wurde, state('normal') wiederherstellen."""
        if self._win and self._win.winfo_exists():
            if self._win.state() != "normal":
                self._win.state("normal")
                self._win.deiconify()
                self._win.lift()

    def _force_taskbar(self):
        """
        Setzt WS_EX_APPWINDOW via SetWindowPos(SWP_FRAMECHANGED).
        Kein Flackern — Fenster bleibt sichtbar.
        """
        try:
            import ctypes
            hwnd = self._win.winfo_id()
            if not hwnd:
                return
            GWL_EXSTYLE      = -20
            WS_EX_APPWINDOW  = 0x00040000
            WS_EX_TOOLWINDOW = 0x00000080
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            style = (style & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
            ctypes.windll.user32.SetWindowPos(
                hwnd, 0, 0, 0, 0, 0,
                0x0002 | 0x0001 | 0x0004 | 0x0020  # NOMOVE|NOSIZE|NOZORDER|FRAMECHANGED
            )
            logger.debug("Taskbar WS_EX_APPWINDOW gesetzt.")
        except Exception as e:
            logger.debug(f"Taskbar: {e}")

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        win = self._win
        win.configure(fg_color=_C_BG)

        # ── Accent-Linie oben ─────────────────────────────────────────────────
        ctk.CTkFrame(win, height=2, corner_radius=0, fg_color=_C_CYAN).pack(fill="x")

        # ── Header ────────────────────────────────────────────────────────────
        header = ctk.CTkFrame(win, fg_color=_C_CARD, corner_radius=0, height=48)
        header.pack(fill="x")
        header.pack_propagate(False)

        ctk.CTkLabel(
            header, text="JRV//SYS",
            font=ctk.CTkFont(family="Consolas", size=15, weight="bold"),
            text_color=_C_CYAN,
        ).place(x=12, rely=0.5, anchor="w")

        sf = ctk.CTkFrame(header, fg_color="transparent")
        sf.place(relx=1.0, x=-12, rely=0.5, anchor="e")

        self._dot_state = ctk.CTkLabel(sf, text="◆",
            font=ctk.CTkFont(size=8), text_color=_C_GREEN)
        self._dot_state.pack(side="left")

        self._lbl_state = ctk.CTkLabel(sf, text="STANDBY",
            font=ctk.CTkFont(family="Consolas", size=9), text_color=_C_DIM)
        self._lbl_state.pack(side="left", padx=(4, 0))

        # ── Provider Card ─────────────────────────────────────────────────────
        prov = ctk.CTkFrame(win, fg_color=_C_CARD, corner_radius=0,
                            border_width=1, border_color=_C_BORDER)
        prov.pack(fill="x", padx=8, pady=(8, 0))

        prov_inner = ctk.CTkFrame(prov, fg_color="transparent")
        prov_inner.pack(fill="x", padx=10, pady=(6, 8))

        ctk.CTkLabel(prov_inner, text="// AI PROVIDER",
            font=ctk.CTkFont(family="Consolas", size=8),
            text_color=_C_DIM,
        ).pack(anchor="w")

        self._lbl_provider = ctk.CTkLabel(prov_inner,
            text=self._jarvis.grok.active_name,
            font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
            text_color=_C_CYAN,
        )
        self._lbl_provider.pack(anchor="w", pady=(1, 4))

        mode_f = ctk.CTkFrame(prov_inner, fg_color="transparent")
        mode_f.pack(fill="x")
        mode_f.columnconfigure((0, 1, 2), weight=1, uniform="mode")

        current = getattr(self._jarvis.grok, "_mode", "auto")
        self._mode_btns = {}
        for col, (label, value) in enumerate([("AUTO", "auto"), ("ONLINE", "online"), ("LOCAL", "local")]):
            is_active = (current == value)
            kw = _ACTIVE_CYAN() if is_active else _btn_inactive()
            btn = ctk.CTkButton(
                mode_f, text=label, **kw,
                font=ctk.CTkFont(family="Consolas", size=9),
                height=22, corner_radius=3,
                command=lambda v=value: self._set_mode(v),
            )
            btn.grid(row=0, column=col, padx=2, sticky="ew")
            self._mode_btns[value] = btn

        # ── Antwortlänge ──────────────────────────────────────────────────────
        lenf = ctk.CTkFrame(win, fg_color=_C_CARD, corner_radius=0,
                            border_width=1, border_color=_C_BORDER)
        lenf.pack(fill="x", padx=8, pady=(6, 0))

        lenf_inner = ctk.CTkFrame(lenf, fg_color="transparent")
        lenf_inner.pack(fill="x", padx=10, pady=(5, 7))

        ctk.CTkLabel(lenf_inner, text="// RESPONSE LENGTH",
            font=ctk.CTkFont(family="Consolas", size=8), text_color=_C_DIM,
        ).pack(anchor="w", pady=(0, 4))

        len_f = ctk.CTkFrame(lenf_inner, fg_color="transparent")
        len_f.pack(fill="x")
        len_f.columnconfigure((0, 1, 2), weight=1, uniform="len")

        cur_len = getattr(self._jarvis.settings, "response_length", "normal")
        self._len_btns = {}
        for col, (lbl, val) in enumerate([("SHORT", "short"), ("NORMAL", "normal"), ("DETAIL", "detailed")]):
            is_active = (cur_len == val)
            kw = _ACTIVE_CYAN() if is_active else _btn_inactive()
            btn = ctk.CTkButton(
                len_f, text=lbl, **kw,
                font=ctk.CTkFont(family="Consolas", size=9),
                height=22, corner_radius=3,
                command=lambda v=val: self._set_length(v),
            )
            btn.grid(row=0, column=col, padx=2, sticky="ew")
            self._len_btns[val] = btn

        # ── Scrollbarer Body ──────────────────────────────────────────────────
        body = ctk.CTkScrollableFrame(win, fg_color=_C_BG, corner_radius=0,
                                      scrollbar_button_color="#0A1828",
                                      scrollbar_button_hover_color="#0D2040")
        body.pack(fill="both", expand=True, padx=8, pady=(6, 0))

        # ── TON ───────────────────────────────────────────────────────────────
        self._section(body, "TONE")
        cur_tone = getattr(self._jarvis.settings, "response_tone", "normal")
        self._tone_btns = {}
        _TONE_MODES = [
            ("FORMAL",     "formal"),
            ("NORMAL",     "normal"),
            ("CASUAL",     "casual"),
            ("TECHNICAL",  "technical"),
            ("CREATIVE",   "creative"),
        ]
        tone_row1 = ctk.CTkFrame(body, fg_color="transparent")
        tone_row1.pack(fill="x", pady=(0, 2))
        tone_row1.columnconfigure((0, 1, 2), weight=1, uniform="tone1")
        for col, (lbl, val) in enumerate(_TONE_MODES[:3]):
            is_active = (cur_tone == val)
            kw = _ACTIVE_PURPLE() if is_active else _btn_inactive()
            btn = ctk.CTkButton(
                tone_row1, text=lbl, **kw,
                font=ctk.CTkFont(family="Consolas", size=9),
                height=22, corner_radius=3,
                command=lambda v=val: self._set_tone(v),
            )
            btn.grid(row=0, column=col, padx=2, sticky="ew")
            self._tone_btns[val] = btn

        tone_row2 = ctk.CTkFrame(body, fg_color="transparent")
        tone_row2.pack(fill="x", pady=(0, 2))
        tone_row2.columnconfigure((0, 1), weight=1, uniform="tone2")
        for col, (lbl, val) in enumerate(_TONE_MODES[3:]):
            is_active = (cur_tone == val)
            kw = _ACTIVE_PURPLE() if is_active else _btn_inactive()
            btn = ctk.CTkButton(
                tone_row2, text=lbl, **kw,
                font=ctk.CTkFont(family="Consolas", size=9),
                height=22, corner_radius=3,
                command=lambda v=val: self._set_tone(v),
            )
            btn.grid(row=0, column=col, padx=2, sticky="ew")
            self._tone_btns[val] = btn

        # ── KI-MODUS ──────────────────────────────────────────────────────────
        self._section(body, "AI MODE")
        s = self._jarvis.settings
        _TOGGLE_MODES = [
            ("MULTI-STEP",  "multi_step",     "multi_step_reasoning"),
            ("PLANNING",    "task_planning",   "task_planning"),
            ("PARALLEL",    "parallel_tasks",  "parallel_tasks"),
        ]
        self._toggle_btns = {}
        toggle_row = ctk.CTkFrame(body, fg_color="transparent")
        toggle_row.pack(fill="x", pady=(0, 2))
        toggle_row.columnconfigure((0, 1, 2), weight=1, uniform="tog")
        for col, (lbl, key, setting_key) in enumerate(_TOGGLE_MODES):
            active = getattr(s, setting_key, False)
            kw = _ACTIVE_ORANGE() if active else _btn_inactive()
            btn = ctk.CTkButton(
                toggle_row, text=lbl, **kw,
                font=ctk.CTkFont(family="Consolas", size=9),
                height=22, corner_radius=3,
                command=lambda k=key: self._toggle_mode(k),
            )
            btn.grid(row=0, column=col, padx=2, sticky="ew")
            self._toggle_btns[key] = btn

        # ── SPRACH-ANALYSE ────────────────────────────────────────────────────
        self._section(body, "VOICE ANALYSIS")
        _ANALYSE_MODES = [
            ("SPEAKER ID",    "speaker_recognition"),
            ("EMOTION DETECT","emotion_detection"),
            ("SMART PAUSE",   "smart_pause"),
            ("INTERRUPT",     "interrupt_handling"),
            ("WHISPER MODE",  "whisper_mode"),
            ("MULTI CMD",     "multi_command"),
        ]
        self._analyse_btns = {}
        for lbl, key in _ANALYSE_MODES:
            active = getattr(self._jarvis.settings, key, False)
            kw = _ACTIVE_GREEN() if active else _btn_inactive()
            btn = ctk.CTkButton(
                body, text=lbl, **kw,
                font=ctk.CTkFont(family="Consolas", size=9),
                height=26, corner_radius=3, anchor="w",
                command=lambda k=key: self._toggle_analyse(k),
            )
            btn.pack(fill="x", pady=1)
            self._analyse_btns[key] = btn

        # Enrollment-Button
        self._enroll_btn = ctk.CTkButton(
            body, text="▶  ENROLL VOICE",
            fg_color="#001828", hover_color="#001A28",
            border_width=1, border_color=_C_CYAN,
            text_color=_C_CYAN,
            font=ctk.CTkFont(family="Consolas", size=9),
            height=26, corner_radius=3,
            command=self._act_enroll_speaker,
        )
        self._enroll_lbl = ctk.CTkLabel(
            body, text="",
            font=ctk.CTkFont(family="Consolas", size=8), text_color=_C_DIM,
        )
        self._refresh_enroll_ui()

        # ── LERN & ADAPTION ───────────────────────────────────────────────────
        self._section(body, "ADAPTATION")
        _ADAPTION_MODES = [
            ("NOISE FILTER",     "noise_filter"),
            ("LONG CONTEXT",     "long_term_context"),
            ("RESUME CONV",      "conversation_resume"),
            ("STYLE LEARN",      "style_learning"),
            ("ADAPT TIMING",     "adaptive_response_time"),
            ("ADAPTIVE ANS",     "adaptive_responses"),
        ]
        self._adaption_btns = {}
        for lbl, key in _ADAPTION_MODES:
            active = getattr(self._jarvis.settings, key, False)
            kw = _ACTIVE_PURPLE() if active else _btn_inactive()
            btn = ctk.CTkButton(
                body, text=lbl, **kw,
                font=ctk.CTkFont(family="Consolas", size=9),
                height=26, corner_radius=3, anchor="w",
                command=lambda k=key: self._toggle_adaption(k),
            )
            btn.pack(fill="x", pady=1)
            self._adaption_btns[key] = btn

        # ── HÖRMODUS ──────────────────────────────────────────────────────────
        self._section(body, "LISTEN MODE")
        _HOER_MODES = [
            ("LOCAL WAKE WORD",   "local_wake_word"),
            ("BACKGROUND MODE",   "background_mode"),
            ("CONTINUOUS LISTEN", "continuous_listening"),
        ]
        self._hoehmodus_btns = {}
        for lbl, key in _HOER_MODES:
            active = getattr(self._jarvis.settings, key, False)
            kw = _ACTIVE_CYAN() if active else _btn_inactive()
            btn = ctk.CTkButton(
                body, text=lbl, **kw,
                font=ctk.CTkFont(family="Consolas", size=9),
                height=26, corner_radius=3, anchor="w",
                command=lambda k=key: self._toggle_hoehmodus(k),
            )
            btn.pack(fill="x", pady=1)
            self._hoehmodus_btns[key] = btn

        # ── PERSÖNLICHKEIT ────────────────────────────────────────────────────
        self._section(body, "PERSONALITY")
        _PERSONALITY_MODES = [
            ("ASSISTANT",   "assistant"),
            ("FRIEND",      "friend"),
            ("BUTLER",      "butler"),
            ("COACH",       "coach"),
            ("SCIENTIST",   "scientist"),
        ]
        cur_pers = getattr(self._jarvis.settings, "personality", "assistant")
        self._personality_btns = {}

        pers_row1 = ctk.CTkFrame(body, fg_color="transparent")
        pers_row1.pack(fill="x", pady=(0, 2))
        pers_row1.columnconfigure((0, 1, 2), weight=1, uniform="pers1")
        for col, (lbl, val) in enumerate(_PERSONALITY_MODES[:3]):
            is_active = (cur_pers == val)
            kw = _ACTIVE_PURPLE() if is_active else _btn_inactive()
            btn = ctk.CTkButton(
                pers_row1, text=lbl, **kw,
                font=ctk.CTkFont(family="Consolas", size=9),
                height=22, corner_radius=3,
                command=lambda v=val: self._set_personality(v),
            )
            btn.grid(row=0, column=col, padx=2, sticky="ew")
            self._personality_btns[val] = btn

        pers_row2 = ctk.CTkFrame(body, fg_color="transparent")
        pers_row2.pack(fill="x", pady=(0, 2))
        pers_row2.columnconfigure((0, 1), weight=1, uniform="pers2")
        for col, (lbl, val) in enumerate(_PERSONALITY_MODES[3:]):
            is_active = (cur_pers == val)
            kw = _ACTIVE_PURPLE() if is_active else _btn_inactive()
            btn = ctk.CTkButton(
                pers_row2, text=lbl, **kw,
                font=ctk.CTkFont(family="Consolas", size=9),
                height=22, corner_radius=3,
                command=lambda v=val: self._set_personality(v),
            )
            btn.grid(row=0, column=col, padx=2, sticky="ew")
            self._personality_btns[val] = btn

        # ── VISION ────────────────────────────────────────────────────────────
        self._section(body, "VISION")

        ctk.CTkButton(
            body, text="▶  OPEN CAMERA WINDOW",
            fg_color="#001828", hover_color="#001A28",
            border_width=1, border_color=_C_CYAN,
            text_color=_C_CYAN,
            font=ctk.CTkFont(family="Consolas", size=9),
            height=26, corner_radius=3, anchor="w",
            command=self._act_open_camera,
        ).pack(fill="x", pady=(0, 2))

        _VISION_MODES = [
            ("VISION ENABLED",    "vision_enabled"),
            ("PROACTIVE HINTS",   "proactive_suggestions"),
            ("DAY PLANNING",      "day_planning"),
            ("MOOD RESPONSES",    "mood_based_responses"),
        ]
        self._vision_btns = {}
        for lbl, key in _VISION_MODES:
            active = getattr(self._jarvis.settings, key, False)
            kw = _ACTIVE_GREEN() if active else _btn_inactive()
            btn = ctk.CTkButton(
                body, text=lbl, **kw,
                font=ctk.CTkFont(family="Consolas", size=9),
                height=26, corner_radius=3, anchor="w",
                command=lambda k=key: self._toggle_vision(k),
            )
            btn.pack(fill="x", pady=1)
            self._vision_btns[key] = btn

        # ── KONFIDENZ ─────────────────────────────────────────────────────────
        self._section(body, "CONFIDENCE")
        conf_row = ctk.CTkFrame(body, fg_color=_C_CARD,
                                border_width=1, border_color=_C_BORDER,
                                corner_radius=3, height=32)
        conf_row.pack(fill="x", pady=(0, 2))
        conf_row.pack_propagate(False)

        self._confidence_dots = ctk.CTkLabel(
            conf_row, text="○○○○○○○○○○",
            font=ctk.CTkFont(family="Consolas", size=10), text_color=_C_DIM,
        )
        self._confidence_dots.place(x=10, rely=0.5, anchor="w")

        self._confidence_val = ctk.CTkLabel(
            conf_row, text="–",
            font=ctk.CTkFont(family="Consolas", size=10, weight="bold"),
            text_color=_C_DIM,
        )
        self._confidence_val.place(relx=1.0, x=-10, rely=0.5, anchor="e")

        self._btn(body, "◈  IMPROVE RESPONSE", self._act_improve)

        # ── TASK QUEUE ────────────────────────────────────────────────────────
        self._section(body, "TASK QUEUE")

        task_frame = ctk.CTkFrame(body, fg_color=_C_CARD,
                                  border_width=1, border_color=_C_BORDER,
                                  corner_radius=3)
        task_frame.pack(fill="x", pady=(0, 2))

        task_top = ctk.CTkFrame(task_frame, fg_color="transparent")
        task_top.pack(fill="x", padx=8, pady=(6, 0))

        ctk.CTkLabel(task_top, text="CURRENT:",
            font=ctk.CTkFont(family="Consolas", size=8), text_color=_C_DIM,
        ).pack(side="left")

        self._task_lbl_current = ctk.CTkLabel(task_top, text="idle",
            font=ctk.CTkFont(family="Consolas", size=9),
            text_color=_C_DIM, anchor="w",
        )
        self._task_lbl_current.pack(side="left", padx=(6, 0), fill="x", expand=True)

        self._task_progress = ctk.CTkProgressBar(
            task_frame, height=4, corner_radius=0,
            fg_color=_C_BORDER, progress_color=_C_CYAN,
        )
        self._task_progress.pack(fill="x", padx=0, pady=(5, 0))
        self._task_progress.set(0)

        task_bot = ctk.CTkFrame(task_frame, fg_color="transparent")
        task_bot.pack(fill="x", padx=8, pady=(5, 7))
        task_bot.columnconfigure((0, 1, 2), weight=1, uniform="tq")

        self._task_lbl_queue = ctk.CTkLabel(task_bot, text="0 queued",
            font=ctk.CTkFont(family="Consolas", size=8), text_color=_C_DIM,
        )
        self._task_lbl_queue.grid(row=0, column=0, sticky="w")

        ctk.CTkButton(
            task_bot, text="STOP",
            fg_color=_C_BG, hover_color="#1A0010",
            border_width=1, border_color="#3A1020",
            text_color="#3A1020",
            font=ctk.CTkFont(family="Consolas", size=9),
            height=20, corner_radius=3,
            command=self._act_cancel_task,
        ).grid(row=0, column=1, padx=1, sticky="ew")

        ctk.CTkButton(
            task_bot, text="CLEAR",
            **_btn_inactive(),
            font=ctk.CTkFont(family="Consolas", size=9),
            height=20, corner_radius=3,
            command=self._act_cancel_all_tasks,
        ).grid(row=0, column=2, padx=1, sticky="ew")

        # ── AUTOMATISIERUNG ───────────────────────────────────────────────────
        self._section(body, "AUTOMATION")

        ctk.CTkButton(
            body, text="+  ADD AUTOMATION",
            fg_color=_C_BG, hover_color="#001A28",
            border_width=1, border_color=_C_BORDER,
            text_color=_C_DIMMED,
            font=ctk.CTkFont(family="Consolas", size=9),
            height=26, corner_radius=3,
            command=self._act_add_recurring,
        ).pack(fill="x", pady=(0, 2))

        self._recurring_frame = ctk.CTkScrollableFrame(
            body, fg_color=_C_CARD, corner_radius=3, height=72,
            border_width=1, border_color=_C_BORDER,
            scrollbar_button_color=_C_BORDER,
            scrollbar_button_hover_color=_C_DIMMED,
        )
        self._recurring_frame.pack(fill="x", pady=(0, 2))
        self._refresh_recurring_ui()

        # ── GESPRÄCH ──────────────────────────────────────────────────────────
        self._section(body, "CHAT")
        self._btn(body, "▶  OPEN CHAT",       self._act_chat,   primary=True)
        self._btn(body, "◈  SHOW HUD",         self._act_hud)
        self._btn(body, "✕  CLEAR HISTORY",    self._act_clear,  danger=True)

        # ── MEMORY ────────────────────────────────────────────────────────────
        self._section(body, "MEMORY")
        self._btn(body, "◈  MEMORY MANAGER",  self._act_memory)
        self._btn(body, "▼  BACKUP MEMORY",   self._act_backup)

        # ── SYSTEM ────────────────────────────────────────────────────────────
        self._section(body, "SYSTEM")
        self._btn(body, "◈  RECALIBRATE MIC", self._act_recalibrate)
        self._btn(body, "⚙  SETTINGS",        self._act_settings)

        # ── Footer / Beenden ──────────────────────────────────────────────────
        footer = ctk.CTkFrame(win, fg_color=_C_CARD, corner_radius=0,
                              border_width=1, border_color=_C_BORDER)
        footer.pack(fill="x", side="bottom", padx=8, pady=(4, 8))

        ctk.CTkButton(
            footer, text="■  SHUTDOWN",
            fg_color=_C_BG, hover_color="#1A0010",
            border_width=1, border_color="#3A1020",
            text_color="#3A1020", hover_color="#1A0010",
            font=ctk.CTkFont(family="Consolas", size=10, weight="bold"),
            height=32, corner_radius=3,
            command=self._act_quit,
        ).pack(fill="x", padx=6, pady=6)

    # ── UI-Helfer ─────────────────────────────────────────────────────────────

    def _section(self, parent, title: str):
        row = ctk.CTkFrame(parent, fg_color="transparent", height=22)
        row.pack(fill="x", padx=0, pady=(8, 2))
        row.pack_propagate(False)
        ctk.CTkFrame(row, height=1, fg_color=_C_BORDER, corner_radius=0,
        ).place(relx=0, rely=0.5, relwidth=1.0, anchor="w")
        ctk.CTkLabel(row, text=f" // {title} ",
            font=ctk.CTkFont(family="Consolas", size=8),
            text_color=_C_DIM, fg_color=_C_BG, anchor="w",
        ).place(x=4, rely=0.5, anchor="w")

    def _btn(self, parent, text: str, cmd, primary: bool = False, danger: bool = False):
        if primary:
            kw = _ACTIVE_CYAN()
        elif danger:
            kw = dict(fg_color=_C_BG, hover_color="#1A0010",
                      border_width=1, border_color="#3A1020",
                      text_color="#3A1020")
        else:
            kw = dict(fg_color=_C_BG, hover_color="#060E1A",
                      border_width=1, border_color=_C_BORDER,
                      text_color=_C_DIMMED)
        ctk.CTkButton(parent, text=text, **kw,
            font=ctk.CTkFont(family="Consolas", size=9),
            height=28, corner_radius=3, anchor="w",
            command=cmd,
        ).pack(fill="x", pady=1)

    # ── KI-Modus ──────────────────────────────────────────────────────────────

    def _set_mode(self, mode: str):
        self._jarvis.grok.set_mode(mode)
        self._jarvis.settings.routing_mode = mode
        self._jarvis.settings.save()
        self._jarvis._notify("system", f"AI mode: {mode.upper()}")
        for v, btn in self._mode_btns.items():
            kw = _ACTIVE_CYAN() if v == mode else _btn_inactive()
            btn.configure(**kw)

    # ── Antwortlänge ──────────────────────────────────────────────────────────

    def _set_length(self, mode: str):
        self._jarvis.set_response_length(mode)
        for v, btn in self._len_btns.items():
            kw = _ACTIVE_CYAN() if v == mode else _btn_inactive()
            btn.configure(**kw)

    # ── Ton ───────────────────────────────────────────────────────────────────

    def _set_tone(self, mode: str):
        self._jarvis.set_tone(mode)
        for v, btn in self._tone_btns.items():
            kw = _ACTIVE_PURPLE() if v == mode else _btn_inactive()
            btn.configure(**kw)

    # ── KI-Modus Toggles ─────────────────────────────────────────────────────

    def _toggle_mode(self, key: str):
        """Schaltet einen KI-Modus um (Multi-Step / Planung / Parallel)."""
        s = self._jarvis.settings
        state_map = {
            "multi_step":     "multi_step_reasoning",
            "task_planning":  "task_planning",
            "parallel_tasks": "parallel_tasks",
        }
        setting_key = state_map.get(key, key)
        current = getattr(s, setting_key, False)
        new_val = not current

        if key == "multi_step":
            self._jarvis.set_multi_step(new_val)
        elif key == "task_planning":
            self._jarvis.set_task_planning(new_val)
        elif key == "parallel_tasks":
            self._jarvis.set_parallel_tasks(new_val)

        if key in self._toggle_btns:
            kw = _ACTIVE_ORANGE() if new_val else _btn_inactive()
            self._toggle_btns[key].configure(**kw)

    # ── Aktionen ──────────────────────────────────────────────────────────────

    def _act_improve(self):
        threading.Thread(
            target=self._jarvis.improve_last_response, daemon=True
        ).start()

    def _act_chat(self):
        # BUG-040: ChatWindow darf nicht im Nicht-Hauptthread erstellt werden.
        if self._chat_thread and self._chat_thread.is_alive():
            if self._chat_window:
                try:
                    if self._hud_root:
                        self._hud_root.after(0, self._chat_window.show)
                    else:
                        self._chat_window.show()
                except Exception:
                    pass
            return
        try:
            from ui.chat_window import ChatWindow
            self._chat_window = ChatWindow(self._jarvis)
        except Exception as e:
            logger.error(f"Chat erstellen: {e}")
            return
        self._chat_thread = threading.Thread(target=self._run_chat, daemon=True)
        self._chat_thread.start()

    def _run_chat(self):
        try:
            self._chat_window.run()
        except Exception as e:
            logger.error(f"Chat: {e}")
        finally:
            self._chat_window = None

    def _act_hud(self):
        if self._hud and self._hud.is_alive():
            self._hud.show()

    def _act_clear(self):
        self._jarvis.grok.clear_history()
        self._jarvis._notify("system", "History cleared.")

    def _act_memory(self):
        if self._memory_proc and self._memory_proc.poll() is None:
            return
        script = Path(__file__).resolve().parent / "memory_app.py"
        try:
            self._memory_proc = subprocess.Popen(
                [sys.executable, str(script)],
                cwd=str(Path(__file__).resolve().parent.parent),
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception as e:
            logger.error(f"Memory: {e}")

    def _act_backup(self):
        threading.Thread(target=lambda: self._jarvis._notify(
            "system", f"{self._jarvis.memory.backup()}"
        ), daemon=True).start()

    def _act_recalibrate(self):
        def _do():
            self._jarvis._notify("system", "Calibrating mic...")
            self._jarvis.recognizer.recalibrate()
            self._jarvis._notify("system", "Calibration done.")
        threading.Thread(target=_do, daemon=True).start()

    def _act_settings(self):
        if self._settings_open:
            return
        threading.Thread(target=self._run_settings, daemon=True).start()

    def _run_settings(self):
        self._settings_open = True
        try:
            from ui.settings_window import SettingsWindow
            SettingsWindow(self._jarvis.settings, self._jarvis).run()
        except Exception as e:
            logger.error(f"Einstellungen: {e}")
        finally:
            self._settings_open = False

    def _act_open_camera(self):
        """Oeffnet das dedizierte Kamera-Fenster mit Live-Vorschau."""
        try:
            from ui.camera_window import CameraWindow
            if self._camera_window is None:
                self._camera_window = CameraWindow(self._jarvis)
            self._camera_window.show(hud_root=self._hud_root)
        except Exception as e:
            logger.error(f"Kamera-Fenster: {e}")

    def _act_quit(self):
        self._jarvis.stop()
        if self._hud_root:
            self._hud_root.after(200, self._hud_root.destroy)

    # ── Live-Updates (thread-safe) ────────────────────────────────────────────

    def _on_state(self, state: State):
        """Debounced (80 ms): schnell aufeinanderfolgende State-Wechsel werden zusammengefasst."""
        if not self._hud_root:
            return
        self._pending_state = state
        if self._state_update_id is not None:
            try:
                self._hud_root.after_cancel(self._state_update_id)
            except Exception:
                pass
        self._state_update_id = self._hud_root.after(80, self._do_state_update)

    def _do_state_update(self):
        self._state_update_id = None
        if self._pending_state is not None:
            self._update_state_ui(self._pending_state)
            self._pending_state = None

    def _update_state_ui(self, state: State):
        if not (self._win and self._win.winfo_exists()):
            return
        key = state.name.lower()
        color, text = _STATE_INFO.get(key, (_C_DIM, state.name))
        if self._dot_state:
            self._dot_state.configure(text_color=color)
        if self._lbl_state:
            self._lbl_state.configure(text=text, text_color=color)
        if self._lbl_provider:
            self._lbl_provider.configure(text=self._jarvis.grok.active_name)

    def _on_message(self, role: str, content: str):
        if role == "system" and "KI gewechselt" in content and self._hud_root:
            self._hud_root.after(0, self._refresh_provider)
        elif role == "confidence" and self._hud_root:
            try:
                score = int(content)
            except ValueError:
                score = 0
            self._hud_root.after(0, self._update_confidence, score)

    def _refresh_provider(self):
        if self._lbl_provider and self._win and self._win.winfo_exists():
            self._lbl_provider.configure(text=self._jarvis.grok.active_name)

    def _update_confidence(self, score: int):
        """Aktualisiert die Konfidenz-Anzeige (score 0–10). Läuft im Hauptthread."""
        if not (self._win and self._win.winfo_exists()):
            return
        if not self._confidence_dots or not self._confidence_val:
            return

        if score == 0:
            color = _C_DIM
            dots  = "○○○○○○○○○○"
            label = "–"
        else:
            filled = score
            empty  = 10 - filled
            if score <= 3:
                color = _C_RED
            elif score <= 6:
                color = _C_ORANGE
            else:
                color = _C_GREEN
            dots  = "●" * filled + "○" * empty
            label = f"{score}/10"

        self._confidence_dots.configure(text=dots, text_color=color)
        self._confidence_val.configure(text=label, text_color=color)

    # ── Task Queue UI ─────────────────────────────────────────────────────────

    def _act_cancel_task(self):
        threading.Thread(target=self._jarvis.cancel_current_task, daemon=True).start()

    def _act_cancel_all_tasks(self):
        threading.Thread(target=self._jarvis.cancel_all_tasks, daemon=True).start()

    def _on_task_update(self, task_list: List):
        """Callback vom TaskManager — throttled (max. 5×/s)."""
        if not self._hud_root:
            return
        if self._task_update_id is not None:
            return
        self._task_update_id = self._hud_root.after(200, self._do_task_update)

    def _do_task_update(self):
        self._task_update_id = None
        self._update_task_ui([])

    def _update_task_ui(self, task_list: List):
        """Aktualisiert die Task-Queue-Anzeige. Läuft im Hauptthread."""
        if not (self._win and self._win.winfo_exists()):
            return

        current = self._jarvis.task_manager.get_current()
        pending = self._jarvis.task_manager.get_pending_count()

        if current:
            label    = current.get("label", "task")
            progress = current.get("progress", 0)
            if len(label) > 28:
                label = label[:25] + "..."
            if self._task_lbl_current:
                self._task_lbl_current.configure(text=label, text_color=_C_CYAN)
            if self._task_progress:
                self._task_progress.configure(progress_color=_C_CYAN)
                self._task_progress.set(progress / 100)
        else:
            if self._task_lbl_current:
                self._task_lbl_current.configure(text="idle", text_color=_C_DIM)
            if self._task_progress:
                self._task_progress.configure(progress_color=_C_BORDER)
                self._task_progress.set(0)

        if self._task_lbl_queue:
            if pending == 0:
                self._task_lbl_queue.configure(text="0 queued", text_color=_C_DIM)
            else:
                self._task_lbl_queue.configure(
                    text=f"{pending} queued", text_color=_C_ORANGE)

    # ── Automatisierung UI ────────────────────────────────────────────────────

    def _refresh_recurring_ui(self):
        if not self._recurring_frame:
            return

        for widget in self._recurring_frame.winfo_children():
            widget.destroy()
        self._recurring_rows.clear()

        tasks = self._jarvis.get_recurring_tasks()

        if not tasks:
            ctk.CTkLabel(
                self._recurring_frame, text="no automations",
                font=ctk.CTkFont(family="Consolas", size=9),
                text_color=_C_DIM,
            ).pack(anchor="w", padx=8, pady=6)
            return

        for entry in tasks:
            rid      = entry["id"]
            label    = entry["label"]
            interval = entry["interval_s"]

            if interval >= 3600:
                iv_str = f"{interval / 3600:.1f}h"
            elif interval >= 60:
                iv_str = f"{interval / 60:.0f}m"
            else:
                iv_str = f"{interval:.0f}s"

            row = ctk.CTkFrame(self._recurring_frame, fg_color=_C_BG,
                               border_width=1, border_color=_C_BORDER,
                               corner_radius=3, height=28)
            row.pack(fill="x", pady=1)
            row.pack_propagate(False)

            ctk.CTkLabel(row, text=label,
                font=ctk.CTkFont(family="Consolas", size=9),
                text_color=_C_DIMMED, anchor="w",
            ).place(x=8, rely=0.5, anchor="w")

            ctk.CTkLabel(row, text=iv_str,
                font=ctk.CTkFont(family="Consolas", size=8),
                text_color=_C_DIM,
            ).place(relx=0.75, rely=0.5, anchor="center")

            ctk.CTkButton(
                row, text="✕", width=22, height=20,
                fg_color=_C_BG, hover_color="#1A0010",
                border_width=1, border_color="#3A1020",
                text_color="#3A1020",
                font=ctk.CTkFont(family="Consolas", size=9),
                corner_radius=3,
                command=lambda r=rid: self._remove_recurring(r),
            ).place(relx=1.0, x=-4, rely=0.5, anchor="e")

            self._recurring_rows[rid] = row

    def _act_add_recurring(self):
        if not (self._win and self._win.winfo_exists()):
            return
        dialog = _RecurringDialog(self._win, on_confirm=self._on_recurring_confirmed)
        dialog.show()

    def _on_recurring_confirmed(self, label: str, interval_min: float, command: str):
        def _add():
            self._jarvis.add_recurring_task(label, interval_min, command)
            if self._hud_root:
                self._hud_root.after(0, self._refresh_recurring_ui)
        threading.Thread(target=_add, daemon=True).start()

    def _remove_recurring(self, rid: str):
        def _remove():
            self._jarvis.remove_recurring_task(rid)
            if self._hud_root:
                self._hud_root.after(0, self._refresh_recurring_ui)
        threading.Thread(target=_remove, daemon=True).start()

    # ── Sprach-Analyse ────────────────────────────────────────────────────────

    def _toggle_analyse(self, key: str):
        s = self._jarvis.settings
        new_val = not getattr(s, key, False)

        if key in self._analyse_btns:
            kw = _ACTIVE_GREEN() if new_val else _btn_inactive()
            self._analyse_btns[key].configure(**kw)

        dispatch = {
            "speaker_recognition": self._jarvis.set_speaker_recognition,
            "emotion_detection":   self._jarvis.set_emotion_detection,
            "smart_pause":         self._jarvis.set_smart_pause,
            "interrupt_handling":  self._jarvis.set_interrupt_handling,
            "whisper_mode":        self._jarvis.set_whisper_mode,
            "multi_command":       self._jarvis.set_multi_command,
        }
        fn = dispatch.get(key)
        if fn:
            threading.Thread(target=fn, args=(new_val,), daemon=True).start()

        if key == "speaker_recognition" and self._hud_root:
            self._hud_root.after(300, self._refresh_enroll_ui)

    def _act_enroll_speaker(self):
        if self._enroll_running:
            return
        self._enroll_running = True

        def _do():
            try:
                self._jarvis.enroll_speaker(duration_s=6.0)
            finally:
                self._enroll_running = False
                if self._hud_root:
                    self._hud_root.after(0, self._refresh_enroll_ui)

        threading.Thread(target=_do, daemon=True).start()

    def _refresh_enroll_ui(self):
        if not (self._win and self._win.winfo_exists()):
            return
        if self._enroll_btn is None or self._enroll_lbl is None:
            return

        speaker_on = self._jarvis.settings.speaker_recognition
        if speaker_on:
            self._enroll_btn.pack(fill="x", pady=(2, 0))
            enrolled = self._jarvis.has_speaker_enrollment()
            status = "profile ok" if enrolled else "no profile"
            self._enroll_lbl.configure(
                text=status,
                text_color=_C_GREEN if enrolled else _C_ORANGE,
            )
            self._enroll_lbl.pack(anchor="w", padx=4, pady=(0, 2))
        else:
            self._enroll_btn.pack_forget()
            self._enroll_lbl.pack_forget()

    def _refresh_analyse_buttons(self):
        if not (self._win and self._win.winfo_exists()):
            return
        s = self._jarvis.settings
        for key, btn in self._analyse_btns.items():
            active = getattr(s, key, False)
            kw = _ACTIVE_GREEN() if active else _btn_inactive()
            btn.configure(**kw)
        self._refresh_enroll_ui()

    # ── Hörmodus ──────────────────────────────────────────────────────────────

    def _toggle_hoehmodus(self, key: str):
        s = self._jarvis.settings
        new_val = not getattr(s, key, False)

        if key in self._hoehmodus_btns:
            kw = _ACTIVE_CYAN() if new_val else _btn_inactive()
            self._hoehmodus_btns[key].configure(**kw)

        dispatch = {
            "local_wake_word":      self._jarvis.set_local_wake_word,
            "background_mode":      self._jarvis.set_background_mode,
            "continuous_listening": self._jarvis.set_continuous_listening,
        }
        fn = dispatch.get(key)
        if fn:
            threading.Thread(target=fn, args=(new_val,), daemon=True).start()

    # ── Lern & Adaption ───────────────────────────────────────────────────────

    def _toggle_adaption(self, key: str):
        s       = self._jarvis.settings
        new_val = not getattr(s, key, False)

        if key in self._adaption_btns:
            kw = _ACTIVE_PURPLE() if new_val else _btn_inactive()
            self._adaption_btns[key].configure(**kw)

        dispatch = {
            "noise_filter":           self._jarvis.set_noise_filter,
            "long_term_context":      self._jarvis.set_long_term_context,
            "conversation_resume":    self._jarvis.set_conversation_resume,
            "style_learning":         self._jarvis.set_style_learning,
            "adaptive_response_time": self._jarvis.set_adaptive_response_time,
            "adaptive_responses":     self._jarvis.set_adaptive_responses,
        }
        fn = dispatch.get(key)
        if fn:
            threading.Thread(target=fn, args=(new_val,), daemon=True).start()

    def _refresh_adaption_buttons(self):
        if not (self._win and self._win.winfo_exists()):
            return
        s = self._jarvis.settings
        for key, btn in self._adaption_btns.items():
            active = getattr(s, key, False)
            kw = _ACTIVE_PURPLE() if active else _btn_inactive()
            btn.configure(**kw)

    # ── Persoenlichkeit & Vision ──────────────────────────────────────────────

    def _set_personality(self, preset: str):
        threading.Thread(
            target=self._jarvis.set_personality, args=(preset,), daemon=True
        ).start()
        for val, btn in self._personality_btns.items():
            kw = _ACTIVE_PURPLE() if val == preset else _btn_inactive()
            btn.configure(**kw)

    def _toggle_vision(self, key: str):
        s = self._jarvis.settings
        new_val = not getattr(s, key, False)

        if key in self._vision_btns:
            kw = _ACTIVE_GREEN() if new_val else _btn_inactive()
            self._vision_btns[key].configure(**kw)

        dispatch = {
            "vision_enabled":        self._jarvis.set_vision_enabled,
            "proactive_suggestions": self._jarvis.set_proactive_suggestions,
            "day_planning":          self._jarvis.set_day_planning,
            "mood_based_responses":  self._jarvis.set_mood_based_responses,
        }
        fn = dispatch.get(key)
        if fn:
            threading.Thread(target=fn, args=(new_val,), daemon=True).start()

    def _refresh_vision_buttons(self):
        if not (self._win and self._win.winfo_exists()):
            return
        s = self._jarvis.settings
        cur_pers = getattr(s, "personality", "assistant")
        for val, btn in self._personality_btns.items():
            kw = _ACTIVE_PURPLE() if val == cur_pers else _btn_inactive()
            btn.configure(**kw)
        for key, btn in self._vision_btns.items():
            active = getattr(s, key, False)
            kw = _ACTIVE_GREEN() if active else _btn_inactive()
            btn.configure(**kw)

    # ── Settings-Refresh (debounced) ──────────────────────────────────────────

    def _on_settings_changed(self):
        if not self._hud_root:
            return
        if self._settings_refresh_id is not None:
            try:
                self._hud_root.after_cancel(self._settings_refresh_id)
            except Exception:
                pass
        self._settings_refresh_id = self._hud_root.after(150, self._do_settings_refresh)

    def _do_settings_refresh(self):
        self._settings_refresh_id = None
        self._refresh_mode_buttons()
        self._refresh_length_buttons()
        self._refresh_tone_buttons()
        self._refresh_toggle_buttons()
        self._refresh_hoehmodus_buttons()
        self._refresh_analyse_buttons()
        self._refresh_adaption_buttons()
        self._refresh_vision_buttons()

    def _refresh_mode_buttons(self):
        if not (self._win and self._win.winfo_exists()):
            return
        current = getattr(self._jarvis.grok, "_mode", "auto")
        for val, btn in self._mode_btns.items():
            kw = _ACTIVE_CYAN() if val == current else _btn_inactive()
            btn.configure(**kw)

    def _refresh_length_buttons(self):
        if not (self._win and self._win.winfo_exists()):
            return
        cur = getattr(self._jarvis.settings, "response_length", "normal")
        for val, btn in self._len_btns.items():
            kw = _ACTIVE_CYAN() if val == cur else _btn_inactive()
            btn.configure(**kw)

    def _refresh_tone_buttons(self):
        if not (self._win and self._win.winfo_exists()):
            return
        cur = getattr(self._jarvis.settings, "response_tone", "normal")
        for val, btn in self._tone_btns.items():
            kw = _ACTIVE_PURPLE() if val == cur else _btn_inactive()
            btn.configure(**kw)

    def _refresh_toggle_buttons(self):
        if not (self._win and self._win.winfo_exists()):
            return
        s = self._jarvis.settings
        state_map = {
            "multi_step":     "multi_step_reasoning",
            "task_planning":  "task_planning",
            "parallel_tasks": "parallel_tasks",
        }
        for key, btn in self._toggle_btns.items():
            active = getattr(s, state_map.get(key, key), False)
            kw = _ACTIVE_ORANGE() if active else _btn_inactive()
            btn.configure(**kw)

    def _refresh_hoehmodus_buttons(self):
        if not (self._win and self._win.winfo_exists()):
            return
        s = self._jarvis.settings
        for key, btn in self._hoehmodus_btns.items():
            active = getattr(s, key, False)
            kw = _ACTIVE_CYAN() if active else _btn_inactive()
            btn.configure(**kw)


# ── Dialog: Neue Automatisierung ─────────────────────────────────────────────

class _RecurringDialog:
    """
    Kleines modales Fenster zum Erstellen einer neuen Automatisierung.
    """

    def __init__(self, parent, on_confirm):
        self._parent     = parent
        self._on_confirm = on_confirm
        self._win: Optional[ctk.CTkToplevel] = None

    def show(self):
        if self._win and self._win.winfo_exists():
            self._win.lift()
            return

        self._win = ctk.CTkToplevel(self._parent)
        self._win.title("Add Automation")
        self._win.resizable(False, False)
        self._win.configure(fg_color=_C_BG)
        self._win.geometry("320x260+150+150")
        self._win.grab_set()

        # Header accent
        ctk.CTkFrame(self._win, height=2, corner_radius=0, fg_color=_C_CYAN).pack(fill="x")

        ctk.CTkLabel(
            self._win, text="// NEW AUTOMATION",
            font=ctk.CTkFont(family="Consolas", size=12, weight="bold"),
            text_color=_C_CYAN,
        ).pack(anchor="w", padx=14, pady=(12, 8))

        def _field(label_text, placeholder):
            ctk.CTkLabel(self._win, text=label_text,
                font=ctk.CTkFont(family="Consolas", size=8), text_color=_C_DIM,
            ).pack(anchor="w", padx=14)
            entry = ctk.CTkEntry(self._win,
                placeholder_text=placeholder,
                fg_color=_C_CARD, border_color=_C_BORDER, border_width=1,
                text_color=_C_TEXT, placeholder_text_color=_C_DIM,
                font=ctk.CTkFont(family="Consolas", size=10),
                height=28, corner_radius=3,
            )
            entry.pack(fill="x", padx=14, pady=(2, 6))
            return entry

        self._entry_label    = _field("NAME", "Weather update")
        self._entry_interval = _field("INTERVAL (min)", "60")
        self._entry_cmd      = _field("COMMAND", "What is the weather?")

        btn_row = ctk.CTkFrame(self._win, fg_color="transparent")
        btn_row.pack(fill="x", padx=14, pady=(4, 14))
        btn_row.columnconfigure((0, 1), weight=1, uniform="dlg")

        ctk.CTkButton(
            btn_row, text="CANCEL",
            **_btn_inactive(),
            font=ctk.CTkFont(family="Consolas", size=9),
            height=28, corner_radius=3,
            command=self._win.destroy,
        ).grid(row=0, column=0, padx=(0, 3), sticky="ew")

        ctk.CTkButton(
            btn_row, text="CONFIRM",
            **_ACTIVE_CYAN(),
            font=ctk.CTkFont(family="Consolas", size=9),
            height=28, corner_radius=3,
            command=self._confirm,
        ).grid(row=0, column=1, padx=(3, 0), sticky="ew")

        self._win.bind("<Return>", lambda _: self._confirm())

    def _confirm(self):
        label    = self._entry_label.get().strip()
        interval = self._entry_interval.get().strip()
        command  = self._entry_cmd.get().strip()

        errors = []
        if not label:
            errors.append("Name required.")
        try:
            interval_f = float(interval.replace(",", "."))
            if interval_f <= 0:
                errors.append("Interval must be > 0.")
        except ValueError:
            errors.append("Interval must be a number.")
            interval_f = 0.0
        if not command:
            errors.append("Command required.")

        if errors:
            for widget in self._win.winfo_children():
                if isinstance(widget, ctk.CTkLabel) and widget.cget("text_color") == _C_RED:
                    widget.destroy()
            ctk.CTkLabel(
                self._win, text=" | ".join(errors),
                font=ctk.CTkFont(family="Consolas", size=8),
                text_color=_C_RED,
            ).pack(anchor="w", padx=14, pady=(0, 4))
            return

        self._win.destroy()
        self._on_confirm(label, interval_f, command)
