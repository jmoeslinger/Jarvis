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

_STATE_INFO = {
    "idle":           ("#6b7280", "Inaktiv"),
    "wake_listening": ("#22c55e", "Hört zu"),
    "cmd_listening":  ("#f59e0b", "Nimmt auf..."),
    "processing":     ("#3b82f6", "Denkt..."),
    "speaking":       ("#a855f7", "Spricht"),
    "error":          ("#ef4444", "Fehler"),
}

_MODE_LABELS = {
    "auto":   "🔄 Auto",
    "online": "☁️ Online",
    "local":  "💻 Lokal",
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
        w, h = 320, 620
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
        win.configure(fg_color="#0f172a")

        # Header
        header = ctk.CTkFrame(win, fg_color="#1e293b", corner_radius=0, height=56)
        header.pack(fill="x")
        header.pack_propagate(False)

        ctk.CTkLabel(
            header, text="⚡  JARVIS",
            font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
            text_color="#f1f5f9",
        ).place(x=16, rely=0.5, anchor="w")

        sf = ctk.CTkFrame(header, fg_color="transparent")
        sf.place(relx=1.0, x=-14, rely=0.5, anchor="e")

        self._dot_state = ctk.CTkLabel(sf, text="●",
            font=ctk.CTkFont(size=11), text_color="#22c55e")
        self._dot_state.pack(side="left")

        self._lbl_state = ctk.CTkLabel(sf, text="Hört zu",
            font=ctk.CTkFont(family="Segoe UI", size=11), text_color="#94a3b8")
        self._lbl_state.pack(side="left", padx=(4, 0))

        # KI-Provider
        prov = ctk.CTkFrame(win, fg_color="#1e293b", corner_radius=10)
        prov.pack(fill="x", padx=12, pady=(10, 0))

        ctk.CTkLabel(prov, text="🤖  KI-Provider",
            font=ctk.CTkFont(size=10, weight="bold"), text_color="#475569",
        ).pack(anchor="w", padx=12, pady=(8, 0))

        self._lbl_provider = ctk.CTkLabel(prov,
            text=self._jarvis.grok.active_name,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#60a5fa",
        )
        self._lbl_provider.pack(anchor="w", padx=12, pady=(2, 0))

        mode_f = ctk.CTkFrame(prov, fg_color="transparent")
        mode_f.pack(fill="x", padx=10, pady=(6, 10))
        mode_f.columnconfigure((0, 1, 2), weight=1, uniform="mode")

        current = getattr(self._jarvis.grok, "_mode", "auto")
        _MODE_LABELS_SHORT = [("🔄 Auto", "auto"), ("☁ Online", "online"), ("💻 Lokal", "local")]
        self._mode_btns = {}
        for col, (label, value) in enumerate(_MODE_LABELS_SHORT):
            btn = ctk.CTkButton(
                mode_f, text=label,
                fg_color="#2563eb" if current == value else "#334155",
                hover_color="#2563eb", text_color="#e2e8f0",
                font=ctk.CTkFont(size=11), height=28, corner_radius=6,
                command=lambda v=value: self._set_mode(v),
            )
            btn.grid(row=0, column=col, padx=2, sticky="ew")
            self._mode_btns[value] = btn

        # ── Antwortlänge ──────────────────────────────────────────────────────────
        lenf_outer = ctk.CTkFrame(win, fg_color="#1e293b", corner_radius=10)
        lenf_outer.pack(fill="x", padx=12, pady=(8, 0))

        ctk.CTkLabel(lenf_outer, text="📏  Antwortlänge",
            font=ctk.CTkFont(size=10, weight="bold"), text_color="#475569",
        ).pack(anchor="w", padx=12, pady=(8, 4))

        len_f = ctk.CTkFrame(lenf_outer, fg_color="transparent")
        len_f.pack(fill="x", padx=10, pady=(0, 10))
        len_f.columnconfigure((0, 1, 2), weight=1, uniform="len")

        _LEN_MODES = [("Kurz", "short"), ("Normal", "normal"), ("Detail", "detailed")]
        cur_len = getattr(self._jarvis.settings, "response_length", "normal")
        self._len_btns = {}
        for col, (lbl, val) in enumerate(_LEN_MODES):
            btn = ctk.CTkButton(
                len_f, text=lbl,
                fg_color="#0f766e" if cur_len == val else "#334155",
                hover_color="#0f766e", text_color="#e2e8f0",
                font=ctk.CTkFont(size=11), height=28, corner_radius=6,
                command=lambda v=val: self._set_length(v),
            )
            btn.grid(row=0, column=col, padx=2, sticky="ew")
            self._len_btns[val] = btn

        # ── Buttons — scrollbarer Bereich ─────────────────────────────────────
        body = ctk.CTkScrollableFrame(win, fg_color="#0f172a", corner_radius=0,
                                      scrollbar_button_color="#334155",
                                      scrollbar_button_hover_color="#475569")
        body.pack(fill="both", expand=True, padx=12, pady=(10, 0))

        # ── TON ───────────────────────────────────────────────────────────────────
        self._section(body, "TON")
        _TONE_MODES = [
            ("Formell",    "formal"),
            ("Normal",     "normal"),
            ("Casual",     "casual"),
            ("Technisch",  "technical"),
            ("Kreativ",    "creative"),
        ]
        cur_tone = getattr(self._jarvis.settings, "response_tone", "normal")
        self._tone_btns = {}

        # Zeile 1: Formell | Normal | Casual
        tone_row1 = ctk.CTkFrame(body, fg_color="transparent")
        tone_row1.pack(fill="x", pady=(0, 2))
        tone_row1.columnconfigure((0, 1, 2), weight=1, uniform="tone1")
        for col, (lbl, val) in enumerate(_TONE_MODES[:3]):
            btn = ctk.CTkButton(
                tone_row1, text=lbl,
                fg_color="#7c3aed" if cur_tone == val else "#334155",
                hover_color="#7c3aed", text_color="#e2e8f0",
                font=ctk.CTkFont(size=11), height=28, corner_radius=6,
                command=lambda v=val: self._set_tone(v),
            )
            btn.grid(row=0, column=col, padx=2, sticky="ew")
            self._tone_btns[val] = btn

        # Zeile 2: Technisch | Kreativ
        tone_row2 = ctk.CTkFrame(body, fg_color="transparent")
        tone_row2.pack(fill="x", pady=(0, 4))
        tone_row2.columnconfigure((0, 1), weight=1, uniform="tone2")
        for col, (lbl, val) in enumerate(_TONE_MODES[3:]):
            btn = ctk.CTkButton(
                tone_row2, text=lbl,
                fg_color="#7c3aed" if cur_tone == val else "#334155",
                hover_color="#7c3aed", text_color="#e2e8f0",
                font=ctk.CTkFont(size=11), height=28, corner_radius=6,
                command=lambda v=val: self._set_tone(v),
            )
            btn.grid(row=0, column=col, padx=2, sticky="ew")
            self._tone_btns[val] = btn

        # ── KI-MODUS TOGGLES ─────────────────────────────────────────────────────
        self._section(body, "KI-MODUS")
        _TOGGLE_MODES = [
            ("🧠 Multi-Step",  "multi_step"),
            ("📋 Planung",     "task_planning"),
            ("⚡ Parallel",    "parallel_tasks"),
        ]
        s = self._jarvis.settings
        _toggle_states = {
            "multi_step":    getattr(s, "multi_step_reasoning", False),
            "task_planning": getattr(s, "task_planning",        False),
            "parallel_tasks":getattr(s, "parallel_tasks",       False),
        }
        self._toggle_btns = {}
        toggle_row = ctk.CTkFrame(body, fg_color="transparent")
        toggle_row.pack(fill="x", pady=(0, 4))
        toggle_row.columnconfigure((0, 1, 2), weight=1, uniform="tog")
        for col, (lbl, key) in enumerate(_TOGGLE_MODES):
            active = _toggle_states.get(key, False)
            btn = ctk.CTkButton(
                toggle_row, text=lbl,
                fg_color="#d97706" if active else "#334155",
                hover_color="#d97706", text_color="#e2e8f0",
                font=ctk.CTkFont(size=11), height=30, corner_radius=6,
                command=lambda k=key: self._toggle_mode(k),
            )
            btn.grid(row=0, column=col, padx=2, sticky="ew")
            self._toggle_btns[key] = btn

        # ── SPRACH-ANALYSE ────────────────────────────────────────────────────────
        self._section(body, "SPRACH-ANALYSE")

        _ANALYSE_MODES = [
            ("👤   Sprecher erkennen",     "speaker_recognition"),
            ("😊   Emotion erkennen",       "emotion_detection"),
            ("⏸   Pausen verstehen",        "smart_pause"),
            ("✋   Interrupt stoppen",       "interrupt_handling"),
            ("🤫   Flüstermodus",           "whisper_mode"),
            ("📋   Mehrfachbefehle",        "multi_command"),
        ]
        self._analyse_btns: Dict[str, ctk.CTkButton] = {}
        for lbl, key in _ANALYSE_MODES:
            active = getattr(self._jarvis.settings, key, False)
            btn = ctk.CTkButton(
                body, text=lbl,
                fg_color="#065f46" if active else "#1e293b",
                hover_color="#065f46",
                text_color="#e2e8f0",
                font=ctk.CTkFont(family="Segoe UI", size=13),
                height=40, corner_radius=8, anchor="w",
                command=lambda k=key: self._toggle_analyse(k),
            )
            btn.pack(fill="x", pady=2)
            self._analyse_btns[key] = btn

        # Enrollment-Button (nur sichtbar wenn speaker_recognition aktiv)
        self._enroll_btn = ctk.CTkButton(
            body, text="🎙   Stimme einrichten",
            fg_color="#1e3a5f", hover_color="#2563eb",
            text_color="#93c5fd",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            height=34, corner_radius=8,
            command=self._act_enroll_speaker,
        )
        self._enroll_lbl = ctk.CTkLabel(
            body, text="",
            font=ctk.CTkFont(size=9), text_color="#475569",
        )
        self._refresh_enroll_ui()

        # ── LERN & ADAPTION ───────────────────────────────────────────────────────
        self._section(body, "LERN & ADAPTION")

        _ADAPTION_MODES = [
            ("🔇   Geräuschfilter",          "noise_filter"),
            ("🧠   Langzeit-Kontext",         "long_term_context"),
            ("🔄   Gespräch fortsetzen",      "conversation_resume"),
            ("📚   Sprachstil lernen",         "style_learning"),
            ("⚡   Reaktionszeit anpassen",    "adaptive_response_time"),
            ("🎯   Adaptive Antworten",        "adaptive_responses"),
        ]
        self._adaption_btns: dict = {}
        for lbl, key in _ADAPTION_MODES:
            active = getattr(self._jarvis.settings, key, False)
            btn = ctk.CTkButton(
                body, text=lbl,
                fg_color="#4c1d95" if active else "#1e293b",
                hover_color="#4c1d95",
                text_color="#e2e8f0",
                font=ctk.CTkFont(family="Segoe UI", size=13),
                height=40, corner_radius=8, anchor="w",
                command=lambda k=key: self._toggle_adaption(k),
            )
            btn.pack(fill="x", pady=2)
            self._adaption_btns[key] = btn

        # ── HÖRMODUS ─────────────────────────────────────────────────────────────
        self._section(body, "HÖRMODUS")

        _HOER_MODES = [
            ("🎯   Wake-Word lokal",     "local_wake_word",
             "Erkennt 'Hey Jarvis' offline (openwakeword)"),
            ("🔇   Hintergrundmodus",    "background_mode",
             "Startet stumm, Panel nicht automatisch"),
            ("🔊   Dauerhaftes Zuhören", "continuous_listening",
             "Kein Wake-Word nötig — alles wird verarbeitet"),
        ]
        self._hoehmodus_btns = {}
        for lbl, key, _ in _HOER_MODES:
            active = getattr(self._jarvis.settings, key, False)
            btn = ctk.CTkButton(
                body, text=lbl,
                fg_color="#0369a1" if active else "#1e293b",
                hover_color="#0369a1",
                text_color="#e2e8f0",
                font=ctk.CTkFont(family="Segoe UI", size=13),
                height=40, corner_radius=8, anchor="w",
                command=lambda k=key: self._toggle_hoehmodus(k),
            )
            btn.pack(fill="x", pady=2)
            self._hoehmodus_btns[key] = btn

        # ── PERSOENLICHKEIT & VISION ──────────────────────────────────────────────
        self._section(body, "PERSOENLICHKEIT & VISION")

        _PERSONALITY_MODES = [
            ("🤖 Assistent",     "assistant"),
            ("👫 Freund",         "friend"),
            ("🎩 Butler",         "butler"),
            ("💪 Coach",          "coach"),
            ("🔬 Wissenschaftler","scientist"),
        ]
        cur_pers = getattr(self._jarvis.settings, "personality", "assistant")
        self._personality_btns: dict = {}

        # Zeile 1: Assistent | Freund | Butler
        pers_row1 = ctk.CTkFrame(body, fg_color="transparent")
        pers_row1.pack(fill="x", pady=(0, 2))
        pers_row1.columnconfigure((0, 1, 2), weight=1, uniform="pers1")
        for col, (lbl, val) in enumerate(_PERSONALITY_MODES[:3]):
            btn = ctk.CTkButton(
                pers_row1, text=lbl,
                fg_color="#0f4c4c" if cur_pers == val else "#334155",
                hover_color="#0f4c4c", text_color="#e2e8f0",
                font=ctk.CTkFont(size=11), height=28, corner_radius=6,
                command=lambda v=val: self._set_personality(v),
            )
            btn.grid(row=0, column=col, padx=2, sticky="ew")
            self._personality_btns[val] = btn

        # Zeile 2: Coach | Wissenschaftler
        pers_row2 = ctk.CTkFrame(body, fg_color="transparent")
        pers_row2.pack(fill="x", pady=(0, 4))
        pers_row2.columnconfigure((0, 1), weight=1, uniform="pers2")
        for col, (lbl, val) in enumerate(_PERSONALITY_MODES[3:]):
            btn = ctk.CTkButton(
                pers_row2, text=lbl,
                fg_color="#0f4c4c" if cur_pers == val else "#334155",
                hover_color="#0f4c4c", text_color="#e2e8f0",
                font=ctk.CTkFont(size=11), height=28, corner_radius=6,
                command=lambda v=val: self._set_personality(v),
            )
            btn.grid(row=0, column=col, padx=2, sticky="ew")
            self._personality_btns[val] = btn

        # ── Kamera-Fenster Button (hervorgehoben) ────────────────────────────────
        ctk.CTkButton(
            body, text="🎥   Kamera-Fenster öffnen",
            fg_color="#0e4f72", hover_color="#0369a1",
            text_color="#7dd3fc",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            height=44, corner_radius=8, anchor="w",
            command=self._act_open_camera,
        ).pack(fill="x", pady=(0, 4))

        _VISION_MODES = [
            ("📷   Vision aktivieren",        "vision_enabled"),
            ("💡   Proaktive Vorschlaege",    "proactive_suggestions"),
            ("📋   Tagesplanung",             "day_planning"),
            ("😊   Mood-Antworten",           "mood_based_responses"),
        ]
        self._vision_btns: dict = {}
        for lbl, key in _VISION_MODES:
            active = getattr(self._jarvis.settings, key, False)
            btn = ctk.CTkButton(
                body, text=lbl,
                fg_color="#134e4a" if active else "#1e293b",
                hover_color="#134e4a",
                text_color="#e2e8f0",
                font=ctk.CTkFont(family="Segoe UI", size=13),
                height=40, corner_radius=8, anchor="w",
                command=lambda k=key: self._toggle_vision(k),
            )
            btn.pack(fill="x", pady=2)
            self._vision_btns[key] = btn

        # ── KONFIDENZ ─────────────────────────────────────────────────────────────
        self._section(body, "KONFIDENZ")
        conf_row = ctk.CTkFrame(body, fg_color="#1e293b", corner_radius=8, height=40)
        conf_row.pack(fill="x", pady=2)
        conf_row.pack_propagate(False)

        self._confidence_dots = ctk.CTkLabel(conf_row, text="─ ─ ─ ─ ─ ─ ─ ─ ─ ─",
            font=ctk.CTkFont(size=11), text_color="#334155")
        self._confidence_dots.place(x=10, rely=0.5, anchor="w")

        self._confidence_val = ctk.CTkLabel(conf_row, text="–",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#475569")
        self._confidence_val.place(relx=1.0, x=-12, rely=0.5, anchor="e")

        self._btn(body, "🔄   Antwort verbessern", self._act_improve)

        # ── TASK QUEUE ────────────────────────────────────────────────────────────
        self._section(body, "TASK QUEUE")

        task_frame = ctk.CTkFrame(body, fg_color="#1e293b", corner_radius=8)
        task_frame.pack(fill="x", pady=(0, 2))

        # Aktueller Task
        task_top = ctk.CTkFrame(task_frame, fg_color="transparent")
        task_top.pack(fill="x", padx=10, pady=(8, 0))

        ctk.CTkLabel(task_top, text="Aktuell:",
            font=ctk.CTkFont(size=9, weight="bold"), text_color="#475569",
        ).pack(side="left")

        self._task_lbl_current = ctk.CTkLabel(task_top, text="Kein Task",
            font=ctk.CTkFont(family="Segoe UI", size=10),
            text_color="#64748b", anchor="w",
        )
        self._task_lbl_current.pack(side="left", padx=(6, 0), fill="x", expand=True)

        # Fortschrittsbalken
        self._task_progress = ctk.CTkProgressBar(
            task_frame, height=6, corner_radius=3,
            fg_color="#0f172a", progress_color="#3b82f6",
        )
        self._task_progress.pack(fill="x", padx=10, pady=(6, 0))
        self._task_progress.set(0)

        # Queue-Status + Abbruch-Buttons
        task_bot = ctk.CTkFrame(task_frame, fg_color="transparent")
        task_bot.pack(fill="x", padx=10, pady=(6, 8))
        task_bot.columnconfigure((0, 1, 2), weight=1, uniform="tq")

        self._task_lbl_queue = ctk.CTkLabel(task_bot, text="0 wartend",
            font=ctk.CTkFont(size=9), text_color="#475569",
        )
        self._task_lbl_queue.grid(row=0, column=0, sticky="w")

        ctk.CTkButton(
            task_bot, text="⛔ Stopp",
            fg_color="#450a0a", hover_color="#dc2626",
            text_color="#fca5a5",
            font=ctk.CTkFont(size=9), height=24, corner_radius=5,
            command=self._act_cancel_task,
        ).grid(row=0, column=1, padx=1, sticky="ew")

        ctk.CTkButton(
            task_bot, text="🗑 Alle",
            fg_color="#1e293b", hover_color="#334155",
            text_color="#94a3b8",
            font=ctk.CTkFont(size=9), height=24, corner_radius=5,
            command=self._act_cancel_all_tasks,
        ).grid(row=0, column=2, padx=1, sticky="ew")

        # ── AUTOMATISIERUNG ───────────────────────────────────────────────────────
        self._section(body, "AUTOMATISIERUNG")

        # Hinzufügen-Button
        ctk.CTkButton(
            body, text="+ Neue Automatisierung",
            fg_color="#1e3a5f", hover_color="#2563eb",
            text_color="#93c5fd",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            height=34, corner_radius=8,
            command=self._act_add_recurring,
        ).pack(fill="x", pady=(0, 4))

        # Liste der wiederholenden Tasks
        self._recurring_frame = ctk.CTkScrollableFrame(
            body, fg_color="#0f172a", corner_radius=6, height=80,
            scrollbar_button_color="#334155",
            scrollbar_button_hover_color="#475569",
        )
        self._recurring_frame.pack(fill="x", pady=(0, 4))

        # Bestehende Tasks anzeigen
        self._refresh_recurring_ui()

        self._section(body, "GESPRÄCH")
        self._btn(body, "💬   Chat öffnen",      self._act_chat,  primary=True)
        self._btn(body, "📺   HUD anzeigen",      self._act_hud)
        self._btn(body, "🗑   Gespräch löschen",  self._act_clear)

        self._section(body, "MEMORY")
        self._btn(body, "🧠   Memory öffnen",    self._act_memory)
        self._btn(body, "💾   Memory sichern",   self._act_backup)

        self._section(body, "SYSTEM")
        self._btn(body, "🎙   Neu kalibrieren",  self._act_recalibrate)
        self._btn(body, "⚙️   Einstellungen",    self._act_settings)

        # Beenden
        footer = ctk.CTkFrame(win, fg_color="#0f172a", corner_radius=0)
        footer.pack(fill="x", side="bottom", padx=12, pady=(4, 12))
        ctk.CTkButton(
            footer, text="⏹   Beenden",
            fg_color="#450a0a", hover_color="#dc2626",
            text_color="#fca5a5",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            height=40, corner_radius=8,
            command=self._act_quit,
        ).pack(fill="x")

    def _section(self, parent, title):
        ctk.CTkLabel(parent, text=title,
            font=ctk.CTkFont(size=9, weight="bold"),
            text_color="#334155", anchor="w",
        ).pack(fill="x", padx=2, pady=(12, 2))

    def _btn(self, parent, text, cmd, primary=False):
        ctk.CTkButton(parent, text=text,
            fg_color="#1e3a5f" if primary else "#1e293b",
            hover_color="#2563eb" if primary else "#334155",
            text_color="#e2e8f0",
            font=ctk.CTkFont(family="Segoe UI", size=13),
            height=40, corner_radius=8, anchor="w",
            command=cmd,
        ).pack(fill="x", pady=2)

    # ── KI-Modus ──────────────────────────────────────────────────────────────

    def _set_mode(self, mode: str):
        self._jarvis.grok.set_mode(mode)
        self._jarvis.settings.routing_mode = mode
        self._jarvis.settings.save()
        self._jarvis._notify("system", f"🤖 KI-Modus: {_MODE_LABELS.get(mode, mode)}")
        for v, btn in self._mode_btns.items():
            btn.configure(fg_color="#2563eb" if v == mode else "#334155")

    # ── Antwortlänge ──────────────────────────────────────────────────────────

    def _set_length(self, mode: str):
        self._jarvis.set_response_length(mode)
        for v, btn in self._len_btns.items():
            btn.configure(fg_color="#0f766e" if v == mode else "#334155")

    # ── Ton ───────────────────────────────────────────────────────────────────

    def _set_tone(self, mode: str):
        self._jarvis.set_tone(mode)
        for v, btn in self._tone_btns.items():
            btn.configure(fg_color="#7c3aed" if v == mode else "#334155")

    # ── KI-Modus Toggles ─────────────────────────────────────────────────────

    def _toggle_mode(self, key: str):
        """Schaltet einen KI-Modus um (Multi-Step / Planung / Parallel)."""
        s = self._jarvis.settings
        # aktuellen Zustand lesen
        state_map = {
            "multi_step":     "multi_step_reasoning",
            "task_planning":  "task_planning",
            "parallel_tasks": "parallel_tasks",
        }
        setting_key = state_map.get(key, key)
        current = getattr(s, setting_key, False)
        new_val = not current

        # JarvisCore-Methode aufrufen
        if key == "multi_step":
            self._jarvis.set_multi_step(new_val)
        elif key == "task_planning":
            self._jarvis.set_task_planning(new_val)
        elif key == "parallel_tasks":
            self._jarvis.set_parallel_tasks(new_val)

        # Button-Farbe aktualisieren
        if key in self._toggle_btns:
            self._toggle_btns[key].configure(
                fg_color="#d97706" if new_val else "#334155"
            )

    # ── Aktionen ──────────────────────────────────────────────────────────────

    def _act_improve(self):
        threading.Thread(
            target=self._jarvis.improve_last_response, daemon=True
        ).start()

    def _act_chat(self):
        # BUG-040: ChatWindow darf nicht im Nicht-Hauptthread erstellt werden.
        # Öffnen immer im Hauptthread via after(); der run()-Mainloop läuft separat.
        if self._chat_thread and self._chat_thread.is_alive():
            # Thread läuft noch — Fenster war evtl. nur minimiert → wieder zeigen
            if self._chat_window:
                try:
                    if self._hud_root:
                        self._hud_root.after(0, self._chat_window.show)
                    else:
                        self._chat_window.show()
                except Exception:
                    pass
            return
        # Neues Fenster: zuerst ChatWindow-Objekt im Hauptthread anlegen,
        # dann run() (Mainloop) in eigenem Thread starten.
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
        self._jarvis._notify("system", "🗑 Gespräch gelöscht.")

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
            "system", f"💾 {self._jarvis.memory.backup()}"
        ), daemon=True).start()

    def _act_recalibrate(self):
        def _do():
            self._jarvis._notify("system", "🎙 Kalibrierung läuft...")
            self._jarvis.recognizer.recalibrate()
            self._jarvis._notify("system", "🎙 Kalibrierung abgeschlossen.")
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
        color, text = _STATE_INFO.get(key, ("#6b7280", state.name))
        if self._dot_state:
            self._dot_state.configure(text_color=color)
        if self._lbl_state:
            self._lbl_state.configure(text=text)
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

        # Farbe je nach Score
        if score == 0:
            color, text_color = "#334155", "#475569"
            dots = "─ ─ ─ ─ ─ ─ ─ ─ ─ ─"
            label = "Fehler"
        else:
            filled = score
            empty  = 10 - filled
            # Farbverlauf: rot (1-3) → gelb (4-6) → grün (7-10)
            if score <= 3:
                color = "#ef4444"
            elif score <= 6:
                color = "#f59e0b"
            else:
                color = "#22c55e"
            dots  = ("●" * filled + "○" * empty)
            label = f"{score}/10"
            text_color = color

        self._confidence_dots.configure(text=dots, text_color=color)
        self._confidence_val.configure(text=label, text_color=text_color)

    # ── Task Queue UI ─────────────────────────────────────────────────────────

    def _act_cancel_task(self):
        """Bricht den aktuell laufenden Task ab."""
        threading.Thread(
            target=self._jarvis.cancel_current_task, daemon=True
        ).start()

    def _act_cancel_all_tasks(self):
        """Bricht alle Tasks ab und leert die Queue."""
        threading.Thread(
            target=self._jarvis.cancel_all_tasks, daemon=True
        ).start()

    def _on_task_update(self, task_list: List):
        """Callback vom TaskManager — throttled (max. 5×/s) um Scroll-Lag zu vermeiden.
        Wenn bereits ein Update geplant ist, wird kein weiterer after()-Aufruf geplant.
        """
        if not self._hud_root:
            return
        if self._task_update_id is not None:
            return   # bereits ein Update in 200 ms geplant — einfach warten
        self._task_update_id = self._hud_root.after(200, self._do_task_update)

    def _do_task_update(self):
        """Fuehrt das Task-UI-Update aus und gibt den Throttle frei."""
        self._task_update_id = None
        self._update_task_ui([])

    def _update_task_ui(self, task_list: List):
        """Aktualisiert die Task-Queue-Anzeige. Läuft im Hauptthread."""
        if not (self._win and self._win.winfo_exists()):
            return

        current  = self._jarvis.task_manager.get_current()
        pending  = self._jarvis.task_manager.get_pending_count()

        # Aktueller Task
        if current:
            label    = current.get("label", "Task")
            progress = current.get("progress", 0)
            if len(label) > 28:
                label = label[:25] + "…"
            if self._task_lbl_current:
                self._task_lbl_current.configure(
                    text=label, text_color="#60a5fa"
                )
            if self._task_progress:
                self._task_progress.configure(progress_color="#3b82f6")
                self._task_progress.set(progress / 100)
        else:
            if self._task_lbl_current:
                self._task_lbl_current.configure(
                    text="Kein Task", text_color="#475569"
                )
            if self._task_progress:
                self._task_progress.configure(progress_color="#1e293b")
                self._task_progress.set(0)

        # Queue-Zähler
        if self._task_lbl_queue:
            if pending == 0:
                self._task_lbl_queue.configure(
                    text="Queue leer", text_color="#475569"
                )
            else:
                self._task_lbl_queue.configure(
                    text=f"{pending} wartend",
                    text_color="#f59e0b",
                )

    # ── Automatisierung UI ────────────────────────────────────────────────────

    def _refresh_recurring_ui(self):
        """
        Baut die Liste der wiederholenden Tasks neu auf.
        Läuft im Hauptthread (oder über after() aufgerufen).
        """
        if not self._recurring_frame:
            return

        # Alte Widgets entfernen
        for widget in self._recurring_frame.winfo_children():
            widget.destroy()
        self._recurring_rows.clear()

        tasks = self._jarvis.get_recurring_tasks()

        if not tasks:
            ctk.CTkLabel(
                self._recurring_frame,
                text="Keine Automatisierungen",
                font=ctk.CTkFont(size=10),
                text_color="#334155",
            ).pack(anchor="w", padx=8, pady=6)
            return

        for entry in tasks:
            rid      = entry["id"]
            label    = entry["label"]
            interval = entry["interval_s"]

            # Intervall lesbar formatieren
            if interval >= 3600:
                iv_str = f"{interval / 3600:.1f}h"
            elif interval >= 60:
                iv_str = f"{interval / 60:.0f}min"
            else:
                iv_str = f"{interval:.0f}s"

            row = ctk.CTkFrame(
                self._recurring_frame, fg_color="#1e293b", corner_radius=6, height=32
            )
            row.pack(fill="x", pady=1)
            row.pack_propagate(False)

            ctk.CTkLabel(
                row, text=f"🔄 {label}",
                font=ctk.CTkFont(size=10), text_color="#94a3b8", anchor="w",
            ).place(x=8, rely=0.5, anchor="w")

            ctk.CTkLabel(
                row, text=iv_str,
                font=ctk.CTkFont(size=9), text_color="#475569",
            ).place(relx=0.72, rely=0.5, anchor="center")

            ctk.CTkButton(
                row, text="×", width=24, height=22,
                fg_color="#450a0a", hover_color="#dc2626",
                text_color="#fca5a5", font=ctk.CTkFont(size=11),
                corner_radius=4,
                command=lambda r=rid: self._remove_recurring(r),
            ).place(relx=1.0, x=-4, rely=0.5, anchor="e")

            self._recurring_rows[rid] = row

    def _act_add_recurring(self):
        """Öffnet den Dialog zum Hinzufügen einer neuen Automatisierung."""
        if not (self._win and self._win.winfo_exists()):
            return
        dialog = _RecurringDialog(self._win, on_confirm=self._on_recurring_confirmed)
        dialog.show()

    def _on_recurring_confirmed(self, label: str, interval_min: float, command: str):
        """Callback wenn der Dialog bestätigt wird."""
        def _add():
            self._jarvis.add_recurring_task(label, interval_min, command)
            if self._hud_root:
                self._hud_root.after(0, self._refresh_recurring_ui)

        threading.Thread(target=_add, daemon=True).start()

    def _remove_recurring(self, rid: str):
        """Entfernt eine Automatisierung."""
        def _remove():
            self._jarvis.remove_recurring_task(rid)
            if self._hud_root:
                self._hud_root.after(0, self._refresh_recurring_ui)

        threading.Thread(target=_remove, daemon=True).start()

    # ── Sprach-Analyse ────────────────────────────────────────────────────────

    def _toggle_analyse(self, key: str):
        """Schaltet einen Sprach-Analyse-Toggle um."""
        s = self._jarvis.settings
        new_val = not getattr(s, key, False)

        # Optimistic UI-Update
        if key in self._analyse_btns:
            self._analyse_btns[key].configure(
                fg_color="#065f46" if new_val else "#1e293b"
            )

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

        # Enrollment-UI ggf. aktualisieren
        if key == "speaker_recognition" and self._hud_root:
            self._hud_root.after(300, self._refresh_enroll_ui)

    def _act_enroll_speaker(self):
        """Startet die Stimm-Einrichtung in einem Background-Thread."""
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
        """Zeigt/versteckt den Enrollment-Button je nach Settings-Zustand."""
        if not (self._win and self._win.winfo_exists()):
            return
        if self._enroll_btn is None or self._enroll_lbl is None:
            return

        speaker_on = self._jarvis.settings.speaker_recognition
        if speaker_on:
            self._enroll_btn.pack(fill="x", pady=(2, 0))
            enrolled = self._jarvis.has_speaker_enrollment()
            status = "✅ Profil vorhanden" if enrolled else "⚠ Noch kein Profil"
            self._enroll_lbl.configure(
                text=status,
                text_color="#22c55e" if enrolled else "#f59e0b",
            )
            self._enroll_lbl.pack(anchor="w", padx=4, pady=(0, 2))
        else:
            self._enroll_btn.pack_forget()
            self._enroll_lbl.pack_forget()

    def _refresh_analyse_buttons(self):
        """Aktualisiert Sprach-Analyse-Buttons anhand der Settings."""
        if not (self._win and self._win.winfo_exists()):
            return
        s = self._jarvis.settings
        for key, btn in self._analyse_btns.items():
            active = getattr(s, key, False)
            btn.configure(fg_color="#065f46" if active else "#1e293b")
        self._refresh_enroll_ui()

    # ── Hörmodus ──────────────────────────────────────────────────────────────

    def _toggle_hoehmodus(self, key: str):
        """Schaltet einen Hörmodus-Toggle um."""
        s = self._jarvis.settings
        current = getattr(s, key, False)
        new_val = not current

        # Optimistic UI-Update (wird bei Fehler durch on_settings_change korrigiert)
        if key in self._hoehmodus_btns:
            self._hoehmodus_btns[key].configure(
                fg_color="#0369a1" if new_val else "#1e293b"
            )

        dispatch = {
            "local_wake_word":    self._jarvis.set_local_wake_word,
            "background_mode":    self._jarvis.set_background_mode,
            "continuous_listening": self._jarvis.set_continuous_listening,
        }
        fn = dispatch.get(key)
        if fn:
            threading.Thread(target=fn, args=(new_val,), daemon=True).start()

    # ── Lern & Adaption ───────────────────────────────────────────────────────

    def _toggle_adaption(self, key: str):
        """Schaltet einen Lern-&-Adaption-Toggle um."""
        s       = self._jarvis.settings
        new_val = not getattr(s, key, False)

        # Optimistic UI-Update
        if key in self._adaption_btns:
            self._adaption_btns[key].configure(
                fg_color="#4c1d95" if new_val else "#1e293b"
            )

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
        """Aktualisiert die Lern-&-Adaption-Buttons anhand der Settings."""
        if not (self._win and self._win.winfo_exists()):
            return
        s = self._jarvis.settings
        for key, btn in self._adaption_btns.items():
            active = getattr(s, key, False)
            btn.configure(fg_color="#4c1d95" if active else "#1e293b")

    # ── Persoenlichkeit & Vision ──────────────────────────────────────────────

    def _set_personality(self, preset: str):
        """Setzt das Persoenlichkeitsprofil."""
        threading.Thread(
            target=self._jarvis.set_personality, args=(preset,), daemon=True
        ).start()
        for val, btn in self._personality_btns.items():
            btn.configure(fg_color="#0f4c4c" if val == preset else "#334155")

    def _toggle_vision(self, key: str):
        """Schaltet einen Vision/Feature-Toggle um."""
        s = self._jarvis.settings
        new_val = not getattr(s, key, False)

        # Optimistic UI-Update
        if key in self._vision_btns:
            self._vision_btns[key].configure(
                fg_color="#134e4a" if new_val else "#1e293b"
            )

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
        """Aktualisiert die Vision/Feature-Buttons anhand der Settings."""
        if not (self._win and self._win.winfo_exists()):
            return
        s = self._jarvis.settings
        cur_pers = getattr(s, "personality", "assistant")
        for val, btn in self._personality_btns.items():
            btn.configure(fg_color="#0f4c4c" if val == cur_pers else "#334155")
        for key, btn in self._vision_btns.items():
            active = getattr(s, key, False)
            btn.configure(fg_color="#134e4a" if active else "#1e293b")

    def _on_settings_changed(self):
        """Callback: Settings wurden geändert — debounced (150 ms) um Scroll-Lag zu vermeiden.
        Mehrere schnell aufeinanderfolgende Aufrufe werden zu einem einzigen UI-Update zusammengefasst.
        """
        if not self._hud_root:
            return
        # Ausstehenden Refresh abbrechen und neu planen
        if self._settings_refresh_id is not None:
            try:
                self._hud_root.after_cancel(self._settings_refresh_id)
            except Exception:
                pass
        self._settings_refresh_id = self._hud_root.after(150, self._do_settings_refresh)

    def _do_settings_refresh(self):
        """Fuehrt alle Button-Refreshes in einem einzigen Mainloop-Tick durch."""
        self._settings_refresh_id = None
        self._refresh_hoehmodus_buttons()
        self._refresh_analyse_buttons()
        self._refresh_adaption_buttons()
        self._refresh_vision_buttons()

    def _refresh_hoehmodus_buttons(self):
        """Aktualisiert die Hörmodus-Button-Farben anhand der aktuellen Settings."""
        if not (self._win and self._win.winfo_exists()):
            return
        s = self._jarvis.settings
        for key, btn in self._hoehmodus_btns.items():
            active = getattr(s, key, False)
            btn.configure(fg_color="#0369a1" if active else "#1e293b")


# ── Dialog: Neue Automatisierung ─────────────────────────────────────────────

class _RecurringDialog:
    """
    Kleines modales Fenster zum Erstellen einer neuen Automatisierung.
    Felder: Label, Intervall (Minuten), Befehl.
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
        self._win.title("Automatisierung hinzufügen")
        self._win.resizable(False, False)
        self._win.configure(fg_color="#0f172a")
        self._win.geometry("340x280+150+150")
        self._win.grab_set()   # Modal

        pad = {"padx": 16, "pady": 6}

        ctk.CTkLabel(
            self._win, text="🔄  Neue Automatisierung",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            text_color="#f1f5f9",
        ).pack(anchor="w", padx=16, pady=(14, 4))

        # Label
        ctk.CTkLabel(
            self._win, text="Name / Beschreibung",
            font=ctk.CTkFont(size=10, weight="bold"), text_color="#475569",
        ).pack(anchor="w", **pad)
        self._entry_label = ctk.CTkEntry(
            self._win, placeholder_text="z.B. Wetter-Update",
            fg_color="#1e293b", border_color="#334155",
            text_color="#e2e8f0", height=32,
        )
        self._entry_label.pack(fill="x", padx=16, pady=(0, 4))

        # Intervall
        ctk.CTkLabel(
            self._win, text="Intervall (Minuten)",
            font=ctk.CTkFont(size=10, weight="bold"), text_color="#475569",
        ).pack(anchor="w", **pad)
        self._entry_interval = ctk.CTkEntry(
            self._win, placeholder_text="z.B. 60",
            fg_color="#1e293b", border_color="#334155",
            text_color="#e2e8f0", height=32,
        )
        self._entry_interval.pack(fill="x", padx=16, pady=(0, 4))

        # Befehl
        ctk.CTkLabel(
            self._win, text="Befehl (was Jarvis sagen soll)",
            font=ctk.CTkFont(size=10, weight="bold"), text_color="#475569",
        ).pack(anchor="w", **pad)
        self._entry_cmd = ctk.CTkEntry(
            self._win, placeholder_text="z.B. Wie wird das Wetter?",
            fg_color="#1e293b", border_color="#334155",
            text_color="#e2e8f0", height=32,
        )
        self._entry_cmd.pack(fill="x", padx=16, pady=(0, 8))

        # Buttons
        btn_row = ctk.CTkFrame(self._win, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(4, 14))
        btn_row.columnconfigure((0, 1), weight=1, uniform="dlg")

        ctk.CTkButton(
            btn_row, text="Abbrechen",
            fg_color="#1e293b", hover_color="#334155",
            text_color="#94a3b8", height=34, corner_radius=7,
            command=self._win.destroy,
        ).grid(row=0, column=0, padx=(0, 4), sticky="ew")

        ctk.CTkButton(
            btn_row, text="✓ Hinzufügen",
            fg_color="#1e3a5f", hover_color="#2563eb",
            text_color="#93c5fd", height=34, corner_radius=7,
            command=self._confirm,
        ).grid(row=0, column=1, padx=(4, 0), sticky="ew")

        # Enter-Taste zum Bestätigen
        self._win.bind("<Return>", lambda _: self._confirm())

    def _confirm(self):
        """Validiert Eingaben und ruft on_confirm auf."""
        label    = self._entry_label.get().strip()
        interval = self._entry_interval.get().strip()
        command  = self._entry_cmd.get().strip()

        errors = []
        if not label:
            errors.append("Name darf nicht leer sein.")
        try:
            interval_f = float(interval.replace(",", "."))
            if interval_f <= 0:
                errors.append("Intervall muss größer als 0 sein.")
        except ValueError:
            errors.append("Intervall muss eine Zahl sein (z.B. 30).")
            interval_f = 0.0
        if not command:
            errors.append("Befehl darf nicht leer sein.")

        if errors:
            # Fehler anzeigen
            for widget in self._win.winfo_children():
                if isinstance(widget, ctk.CTkLabel) and widget.cget("text_color") == "#ef4444":
                    widget.destroy()
            ctk.CTkLabel(
                self._win, text=" | ".join(errors),
                font=ctk.CTkFont(size=9), text_color="#ef4444",
            ).pack(anchor="w", padx=16, pady=(0, 4))
            return

        self._win.destroy()
        self._on_confirm(label, interval_f, command)
