import logging
import threading
from typing import List, Optional

import customtkinter as ctk
import keyboard
import sounddevice as sd

from config.settings import Settings
from services.system.autostart import Autostart

logger = logging.getLogger("jarvis.settings")

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ── Cyberpunk-Palette (identisch zu control_panel / hud) ─────────────────────
_C_BG     = "#040608"
_C_CARD   = "#06080E"
_C_BORDER = "#0A1828"
_C_CYAN   = "#00BFFF"
_C_GREEN  = "#00FF88"
_C_ORANGE = "#FF9500"
_C_RED    = "#FF2060"
_C_TEXT   = "#C8D8F0"
_C_DIM    = "#1A3A50"
_C_DIMMED = "#3A7090"

_VOICES = [
    "de-DE-FlorianMultilingualNeural",
    "de-DE-KillianNeural",
    "de-DE-ConradNeural",
    "de-DE-KatjaNeural",
    "de-AT-JonasNeural",
    "de-CH-LeniNeural",
]

_MODELS_GROQ = [
    "llama-3.3-70b-versatile",
    "llama-3.1-70b-versatile",
    "llama3-groq-70b-8192-tool-use-preview",
    "mixtral-8x7b-32768",
]

_MODELS_GROK = [
    "grok-3",
    "grok-3-mini",
    "grok-2-1212",
]


def _get_input_devices() -> List[tuple]:
    devices = []
    try:
        for i, dev in enumerate(sd.query_devices()):
            if dev.get("max_input_channels", 0) > 0:
                devices.append((i, dev["name"]))
    except Exception:
        pass
    return devices


class SettingsWindow:
    def __init__(self, settings: Settings, jarvis=None):
        self._settings = settings
        self._jarvis   = jarvis
        self._root: Optional[ctk.CTk] = None
        self._recording_hotkey = False
        self._input_devices: List[tuple] = _get_input_devices()

    def run(self):
        self._root = ctk.CTk()
        self._root.title("Jarvis — Settings")
        self._root.geometry("560x780")
        self._root.resizable(False, False)
        self._root.configure(fg_color=_C_BG)
        self._build_ui()
        self._root.mainloop()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Accent line
        ctk.CTkFrame(self._root, height=2, corner_radius=0, fg_color=_C_CYAN).pack(fill="x")

        # Header
        header = ctk.CTkFrame(self._root, fg_color=_C_CARD, corner_radius=0, height=50)
        header.pack(fill="x")
        header.pack_propagate(False)
        ctk.CTkLabel(
            header, text="JRV//CFG",
            font=ctk.CTkFont(family="Consolas", size=16, weight="bold"),
            text_color=_C_CYAN,
        ).place(x=14, rely=0.5, anchor="w")
        ctk.CTkLabel(
            header, text="SETTINGS",
            font=ctk.CTkFont(family="Consolas", size=10),
            text_color=_C_DIM,
        ).place(relx=1.0, x=-14, rely=0.5, anchor="e")

        # Divider
        ctk.CTkFrame(self._root, height=1, fg_color=_C_BORDER, corner_radius=0).pack(fill="x")

        # Scroll area
        scroll = ctk.CTkScrollableFrame(
            self._root, fg_color=_C_BG,
            scrollbar_button_color=_C_BORDER,
            scrollbar_button_hover_color=_C_DIMMED,
        )
        scroll.pack(fill="both", expand=True)

        # ── AKTIVIERUNG ───────────────────────────────────────────────────────
        self._section(scroll, "ACTIVATION")
        card = self._card(scroll)

        self._label(card, "HOTKEY", "press button then key combo")
        hk_row = ctk.CTkFrame(card, fg_color="transparent")
        hk_row.pack(fill="x", padx=14, pady=(0, 8))

        self._hotkey_display = ctk.CTkEntry(
            hk_row, width=190, height=34,
            font=ctk.CTkFont(family="Consolas", size=12),
            fg_color=_C_BG, border_color=_C_BORDER, border_width=1,
            text_color=_C_CYAN, state="readonly",
        )
        self._hotkey_display.pack(side="left")
        self._hotkey_display.configure(state="normal")
        self._hotkey_display.insert(0, self._settings.activation_hotkey)
        self._hotkey_display.configure(state="readonly")

        self._record_btn = ctk.CTkButton(
            hk_row, text="REMAP",
            width=100, height=34,
            font=ctk.CTkFont(family="Consolas", size=11),
            fg_color=_C_BG, hover_color="#001A28",
            border_width=1, border_color=_C_CYAN, text_color=_C_CYAN,
            corner_radius=4,
            command=self._start_record_hotkey,
        )
        self._record_btn.pack(side="left", padx=(8, 0))

        self._hotkey_hint = ctk.CTkLabel(
            card, text="e.g.  ctrl+shift+j  or  alt+j",
            font=ctk.CTkFont(family="Consolas", size=9), text_color=_C_DIM,
        )
        self._hotkey_hint.pack(anchor="w", padx=14, pady=(0, 12))

        self._label(card, "WAKE WORDS", "comma-separated")
        self._wake_entry = self._entry(card)
        self._wake_entry.insert(0, ", ".join(self._settings.wake_words))
        self._wake_entry.pack(fill="x", padx=14, pady=(0, 12))

        self._label(card, "MICROPHONE", "default = auto")
        mic_names = ["Default (auto)"] + [name for _, name in self._input_devices]
        current_device = self._settings.input_device
        current_mic_name = "Default (auto)"
        for idx, name in self._input_devices:
            if idx == current_device:
                current_mic_name = name
                break
        self._mic_var = ctk.StringVar(value=current_mic_name)
        self._optmenu(card, mic_names, self._mic_var)

        # Mic test
        test_row = ctk.CTkFrame(card, fg_color="transparent")
        test_row.pack(fill="x", padx=14, pady=(8, 4))

        self._mic_test_btn = ctk.CTkButton(
            test_row, text="TEST MIC",
            width=110, height=30,
            font=ctk.CTkFont(family="Consolas", size=10),
            fg_color=_C_BG, hover_color="#001A28",
            border_width=1, border_color=_C_CYAN, text_color=_C_CYAN,
            corner_radius=4,
            command=self._start_mic_test,
        )
        self._mic_test_btn.pack(side="left")

        self._mic_result_lbl = ctk.CTkLabel(
            test_row, text="click to test...",
            font=ctk.CTkFont(family="Consolas", size=10), text_color=_C_DIM,
        )
        self._mic_result_lbl.pack(side="left", padx=(12, 0))

        self._mic_bar_frame = ctk.CTkFrame(card, fg_color=_C_BORDER, corner_radius=3, height=6)
        self._mic_bar_frame.pack(fill="x", padx=14, pady=(4, 14))
        self._mic_bar = ctk.CTkFrame(self._mic_bar_frame, fg_color=_C_CYAN, corner_radius=3, height=6)
        self._mic_bar.place(relx=0, rely=0, relwidth=0.0, relheight=1.0)

        # ── KI & SPRACHE ──────────────────────────────────────────────────────
        self._section(scroll, "AI & VOICE")
        ai_card = self._card(scroll)

        self._label(ai_card, "AI MODEL")
        models = _MODELS_GROK if self._settings.api_provider == "grok" else _MODELS_GROQ
        self._model_var = ctk.StringVar(value=self._settings.grok_model)
        self._optmenu(ai_card, models, self._model_var)

        self._label(ai_card, "TTS VOICE")
        self._voice_var = ctk.StringVar(value=self._settings.tts_voice)
        self._optmenu(ai_card, _VOICES, self._voice_var)

        self._label(ai_card, "SPEECH RATE")
        rate_row = ctk.CTkFrame(ai_card, fg_color="transparent")
        rate_row.pack(fill="x", padx=14, pady=(0, 14))

        current_rate = int(self._settings.tts_rate.replace("%", "").replace("+", ""))
        self._rate_var = ctk.IntVar(value=current_rate)
        self._rate_lbl = ctk.CTkLabel(
            rate_row, text=f"{current_rate:+d}%",
            font=ctk.CTkFont(family="Consolas", size=11), text_color=_C_DIMMED, width=52,
        )
        self._rate_lbl.pack(side="right")
        ctk.CTkSlider(
            rate_row, from_=-30, to=30,
            variable=self._rate_var,
            command=self._on_rate_change,
            height=18,
            progress_color=_C_CYAN,
            button_color=_C_CYAN,
            button_hover_color=_C_GREEN,
        ).pack(side="left", fill="x", expand=True)

        # ── SYSTEM ────────────────────────────────────────────────────────────
        self._section(scroll, "SYSTEM")
        sys_card = self._card(scroll)

        self._autostart_var = ctk.BooleanVar(value=self._settings.autostart_enabled)
        self._checkbox(sys_card, "AUTOSTART WITH WINDOWS", self._autostart_var)

        # ── SPRACH-FEATURES ───────────────────────────────────────────────────
        self._section(scroll, "VOICE FEATURES")
        speech_card = self._card(scroll)

        self._whisper_var = ctk.BooleanVar(value=self._settings.whisper_mode)
        self._checkbox(speech_card, "WHISPER MODE", self._whisper_var,
                       hint="respond to quiet speech")

        self._multi_cmd_var = ctk.BooleanVar(value=self._settings.multi_command)
        self._checkbox(speech_card, "MULTI COMMAND", self._multi_cmd_var,
                       hint="multiple actions in one sentence")

        # ── LERN & ADAPTION ───────────────────────────────────────────────────
        self._section(scroll, "ADAPTATION")
        adapt_card = self._card(scroll)

        self._noise_filter_var = ctk.BooleanVar(value=self._settings.noise_filter)
        self._checkbox(adapt_card, "NOISE FILTER", self._noise_filter_var,
                       hint="spectral subtraction before STT")

        self._long_term_ctx_var = ctk.BooleanVar(value=self._settings.long_term_context)
        self._checkbox(adapt_card, "LONG-TERM CONTEXT", self._long_term_ctx_var,
                       hint="include topics from earlier sessions")

        self._conv_resume_var = ctk.BooleanVar(value=self._settings.conversation_resume)
        self._checkbox(adapt_card, "RESUME CONVERSATION", self._conv_resume_var,
                       hint="load last session messages on start")

        self._style_learning_var = ctk.BooleanVar(value=self._settings.style_learning)
        self._checkbox(adapt_card, "STYLE LEARNING", self._style_learning_var,
                       hint="auto-adapt tone and length")

        self._adapt_time_var = ctk.BooleanVar(value=self._settings.adaptive_response_time)
        self._checkbox(adapt_card, "ADAPTIVE TIMING", self._adapt_time_var,
                       hint="faster for short commands")

        self._adapt_resp_var = ctk.BooleanVar(value=self._settings.adaptive_responses)
        self._checkbox(adapt_card, "ADAPTIVE RESPONSES", self._adapt_resp_var,
                       hint="apply learned style preferences (15+ cmds)")

        # ── PERSÖNLICHKEIT ────────────────────────────────────────────────────
        self._section(scroll, "PERSONALITY")
        pers_card = self._card(scroll)

        self._label(pers_card, "CHARACTER PRESET", "how should Jarvis behave?")
        _PERSONALITY_PRESETS = [
            "assistant", "friend", "butler", "coach", "scientist", "custom"
        ]
        _PERSONALITY_LABELS = {
            "assistant":  "ASSISTANT (default)",
            "friend":     "FRIEND (casual, informal)",
            "butler":     "BUTLER (formal)",
            "coach":      "COACH (motivating)",
            "scientist":  "SCIENTIST (analytical)",
            "custom":     "CUSTOM",
        }
        self._personality_var = ctk.StringVar(
            value=_PERSONALITY_LABELS.get(self._settings.personality, "ASSISTANT (default)")
        )
        self._personality_display_to_key = {v: k for k, v in _PERSONALITY_LABELS.items()}
        self._optmenu(pers_card, [_PERSONALITY_LABELS[k] for k in _PERSONALITY_PRESETS],
                      self._personality_var)

        self._label(pers_card, "CUSTOM DESCRIPTION", "only used when CUSTOM is selected")
        self._personality_custom_entry = self._entry(
            pers_card, placeholder="e.g. 'Always respond with Viennese humor'"
        )
        self._personality_custom_entry.insert(0, self._settings.personality_custom)
        self._personality_custom_entry.pack(fill="x", padx=14, pady=(0, 14))

        # ── VISION ────────────────────────────────────────────────────────────
        self._section(scroll, "VISION")
        vision_card = self._card(scroll)

        self._vision_enabled_var = ctk.BooleanVar(value=self._settings.vision_enabled)
        self._checkbox(vision_card, "ENABLE VISION", self._vision_enabled_var,
                       hint="camera capture + AI analysis")

        self._label(vision_card, "CAMERA INDEX", "0 = default webcam")
        self._cam_idx_var = ctk.IntVar(value=self._settings.vision_camera_index)
        cam_row = ctk.CTkFrame(vision_card, fg_color="transparent")
        cam_row.pack(fill="x", padx=14, pady=(0, 10))
        self._cam_idx_lbl = ctk.CTkLabel(
            cam_row, text=str(self._settings.vision_camera_index),
            font=ctk.CTkFont(family="Consolas", size=11), text_color=_C_DIMMED, width=28,
        )
        self._cam_idx_lbl.pack(side="right")
        ctk.CTkSlider(
            cam_row, from_=0, to=4,
            variable=self._cam_idx_var,
            command=lambda v: self._cam_idx_lbl.configure(text=str(int(float(v)))),
            height=18, number_of_steps=4,
            progress_color=_C_CYAN, button_color=_C_CYAN,
            button_hover_color=_C_GREEN,
        ).pack(side="left", fill="x", expand=True)

        self._label(vision_card, "GEMINI API KEY", "free Google key (optional)")
        self._gemini_key_entry = self._entry(
            vision_card, placeholder="AIza... (leave empty to use Groq)", show="*"
        )
        self._gemini_key_entry.insert(0, self._settings.gemini_api_key)
        self._gemini_key_entry.pack(fill="x", padx=14, pady=(0, 10))

        self._label(vision_card, "OLLAMA VISION MODEL", "fallback without API key")
        self._ollama_vision_var = ctk.StringVar(value=self._settings.vision_ollama_model)
        self._optmenu(vision_card,
                      ["moondream", "llava", "llava:13b", "bakllava"],
                      self._ollama_vision_var)

        # ── APP INTEGRATION ───────────────────────────────────────────────────
        self._section(scroll, "APP INTEGRATION")
        apps_card = self._card(scroll)

        ctk.CTkLabel(
            apps_card, text="custom apps launchable by voice  (e.g. 'open spotify')",
            font=ctk.CTkFont(family="Consolas", size=9), text_color=_C_DIM,
        ).pack(anchor="w", padx=14, pady=(10, 6))

        self._apps_frame = ctk.CTkScrollableFrame(
            apps_card, fg_color=_C_BG, corner_radius=4, height=90,
            border_width=1, border_color=_C_BORDER,
            scrollbar_button_color=_C_BORDER,
            scrollbar_button_hover_color=_C_DIMMED,
        )
        self._apps_frame.pack(fill="x", padx=14, pady=(0, 6))
        self._app_rows: list = []
        self._refresh_apps_ui()

        add_row = ctk.CTkFrame(apps_card, fg_color="transparent")
        add_row.pack(fill="x", padx=14, pady=(0, 14))

        self._new_app_name = ctk.CTkEntry(
            add_row, height=30, width=90,
            font=ctk.CTkFont(family="Consolas", size=10),
            fg_color=_C_BG, border_color=_C_BORDER, border_width=1,
            text_color=_C_TEXT, placeholder_text="name",
            placeholder_text_color=_C_DIM,
        )
        self._new_app_name.pack(side="left")

        self._new_app_path = ctk.CTkEntry(
            add_row, height=30,
            font=ctk.CTkFont(family="Consolas", size=10),
            fg_color=_C_BG, border_color=_C_BORDER, border_width=1,
            text_color=_C_TEXT, placeholder_text="C:\\path\\app.exe",
            placeholder_text_color=_C_DIM,
        )
        self._new_app_path.pack(side="left", fill="x", expand=True, padx=(4, 4))

        ctk.CTkButton(
            add_row, text="+", width=30, height=30,
            font=ctk.CTkFont(family="Consolas", size=14, weight="bold"),
            fg_color=_C_BG, hover_color="#001A28",
            border_width=1, border_color=_C_CYAN, text_color=_C_CYAN,
            corner_radius=4,
            command=self._add_custom_app,
        ).pack(side="left")

        # ── Error + Save ──────────────────────────────────────────────────────
        self._err_lbl = ctk.CTkLabel(
            self._root, text="",
            font=ctk.CTkFont(family="Consolas", size=10), text_color=_C_RED,
        )
        self._err_lbl.pack(pady=(6, 0))

        ctk.CTkButton(
            self._root,
            text="▶  SAVE & APPLY",
            height=42,
            font=ctk.CTkFont(family="Consolas", size=13, weight="bold"),
            fg_color=_C_BG, hover_color="#001A28",
            border_width=1, border_color=_C_CYAN, text_color=_C_CYAN,
            corner_radius=4,
            command=self._save,
        ).pack(fill="x", padx=20, pady=(4, 14))

    # ── UI-Helfer ─────────────────────────────────────────────────────────────

    def _section(self, parent, title: str):
        row = ctk.CTkFrame(parent, fg_color="transparent", height=24)
        row.pack(fill="x", padx=0, pady=(12, 3))
        row.pack_propagate(False)
        ctk.CTkFrame(row, height=1, fg_color=_C_BORDER, corner_radius=0,
        ).place(relx=0, rely=0.5, relwidth=1.0, anchor="w")
        ctk.CTkLabel(row, text=f" // {title} ",
            font=ctk.CTkFont(family="Consolas", size=9),
            text_color=_C_DIM, fg_color=_C_BG, anchor="w",
        ).place(x=8, rely=0.5, anchor="w")

    def _card(self, parent) -> ctk.CTkFrame:
        card = ctk.CTkFrame(parent, fg_color=_C_CARD, corner_radius=4,
                            border_width=1, border_color=_C_BORDER)
        card.pack(fill="x", padx=8, pady=(0, 4))
        return card

    def _label(self, parent, text: str, hint: str = ""):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(12, 3))
        ctk.CTkLabel(
            row, text=text,
            font=ctk.CTkFont(family="Consolas", size=10, weight="bold"),
            text_color=_C_DIMMED,
        ).pack(side="left")
        if hint:
            ctk.CTkLabel(
                row, text=hint,
                font=ctk.CTkFont(family="Consolas", size=9), text_color=_C_DIM,
            ).pack(side="right")

    def _entry(self, parent, placeholder: str = "", show: str = "") -> ctk.CTkEntry:
        kw = {"show": show} if show else {}
        return ctk.CTkEntry(
            parent, height=34,
            font=ctk.CTkFont(family="Consolas", size=11),
            fg_color=_C_BG, border_color=_C_BORDER, border_width=1,
            text_color=_C_TEXT,
            placeholder_text=placeholder, placeholder_text_color=_C_DIM,
            corner_radius=4,
            **kw,
        )

    def _optmenu(self, parent, values: list, variable: ctk.StringVar):
        ctk.CTkOptionMenu(
            parent,
            values=values,
            variable=variable,
            height=34,
            font=ctk.CTkFont(family="Consolas", size=11),
            fg_color=_C_BG,
            button_color=_C_BORDER,
            button_hover_color=_C_DIMMED,
            dropdown_fg_color=_C_CARD,
            dropdown_text_color=_C_TEXT,
            dropdown_font=ctk.CTkFont(family="Consolas", size=11),
            text_color=_C_TEXT,
            corner_radius=4,
        ).pack(fill="x", padx=14, pady=(0, 10))

    def _checkbox(self, parent, text: str, variable: ctk.BooleanVar, hint: str = ""):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(8, 0))
        ctk.CTkCheckBox(
            row,
            text=text,
            variable=variable,
            font=ctk.CTkFont(family="Consolas", size=11),
            text_color=_C_TEXT,
            checkmark_color=_C_BG,
            fg_color=_C_CYAN,
            hover_color=_C_DIMMED,
            border_color=_C_BORDER,
            corner_radius=3,
        ).pack(side="left", anchor="w")
        if hint:
            ctk.CTkLabel(
                parent, text=hint,
                font=ctk.CTkFont(family="Consolas", size=9), text_color=_C_DIM,
            ).pack(anchor="w", padx=38, pady=(1, 4))
        else:
            ctk.CTkFrame(parent, height=4, fg_color="transparent").pack()

    # ── Hotkey aufnehmen ──────────────────────────────────────────────────────

    def _start_record_hotkey(self):
        if self._recording_hotkey:
            return
        self._recording_hotkey = True
        self._record_btn.configure(text="PRESS NOW...", border_color=_C_ORANGE,
                                   text_color=_C_ORANGE)
        self._hotkey_display.configure(state="normal")
        self._hotkey_display.delete(0, "end")
        self._hotkey_display.insert(0, "...")
        self._hotkey_display.configure(state="readonly")
        threading.Thread(target=self._capture_hotkey, daemon=True).start()

    def _capture_hotkey(self):
        # BUG-098: keyboard.read_hotkey(suppress=True) blockiert alle Tastatureingaben
        # systemweit solange es wartet. suppress=False vermeidet dieses Problem —
        # der Benutzer kann weiterhin tippen und andere Anwendungen nutzen.
        try:
            combo = keyboard.read_hotkey(suppress=False)
            if combo.lower() in ("escape", "esc"):
                self._root.after(0, self._reset_record_btn)
                return
            self._root.after(0, lambda c=combo: self._apply_hotkey(c))
        except Exception as e:
            logger.error(f"Hotkey-Aufnahme Fehler: {e}")
            self._root.after(0, self._reset_record_btn)

    def _apply_hotkey(self, combo: str):
        self._hotkey_display.configure(state="normal")
        self._hotkey_display.delete(0, "end")
        self._hotkey_display.insert(0, combo)
        self._hotkey_display.configure(state="readonly")
        self._reset_record_btn()

    def _reset_record_btn(self):
        self._recording_hotkey = False
        self._record_btn.configure(text="REMAP", border_color=_C_CYAN,
                                   text_color=_C_CYAN)

    # ── Mikrofon-Test ─────────────────────────────────────────────────────────

    def _on_rate_change(self, val):
        v = int(float(val))
        self._rate_lbl.configure(text=f"{v:+d}%")

    def _start_mic_test(self):
        self._mic_test_btn.configure(text="RECORDING...", state="disabled",
                                     border_color=_C_ORANGE, text_color=_C_ORANGE)
        self._mic_result_lbl.configure(text="speak now...", text_color=_C_ORANGE)
        self._mic_bar.place(relwidth=0.0)
        threading.Thread(target=self._run_mic_test, daemon=True).start()

    def _run_mic_test(self):
        import numpy as np
        selected = self._mic_var.get()
        device = None
        for idx, name in self._input_devices:
            if name == selected:
                device = idx
                break
        try:
            samples = sd.rec(int(2.0 * 16000), samplerate=16000,
                             channels=1, dtype="int16", device=device)
            sd.wait()
            rms = np.sqrt(np.mean(samples.astype(np.float64) ** 2))
            db  = 20.0 * np.log10(max(rms, 1.0))
            self._root.after(0, lambda d=db: self._show_mic_result(d))
        except Exception as e:
            self._root.after(0, lambda: self._show_mic_error(str(e)))

    def _show_mic_result(self, db: float):
        fill = min(db / 60.0, 1.0)
        self._mic_bar.place(relwidth=fill)
        if db < 10:
            color, icon, text = _C_RED,    "✗", f"very quiet ({db:.0f} dB) — mic muted?"
        elif db < 28:
            color, icon, text = _C_ORANGE, "▲", f"quiet ({db:.0f} dB) — move closer?"
        elif db < 52:
            color, icon, text = _C_GREEN,  "✓", f"good ({db:.0f} dB) — mic OK"
        else:
            color, icon, text = _C_ORANGE, "▲", f"very loud ({db:.0f} dB) — noise?"
        self._mic_bar.configure(fg_color=color)
        self._mic_result_lbl.configure(text=f"{icon}  {text}", text_color=color)
        self._mic_test_btn.configure(text="TEST MIC", state="normal",
                                     border_color=_C_CYAN, text_color=_C_CYAN)

    def _show_mic_error(self, error: str):
        self._mic_result_lbl.configure(
            text=f"✗  {error[:50]}", text_color=_C_RED)
        self._mic_test_btn.configure(text="TEST MIC", state="normal",
                                     border_color=_C_CYAN, text_color=_C_CYAN)

    # ── App Integration ───────────────────────────────────────────────────────

    def _refresh_apps_ui(self):
        for widget in self._apps_frame.winfo_children():
            widget.destroy()
        self._app_rows.clear()

        apps = self._settings.custom_apps
        if not apps:
            ctk.CTkLabel(
                self._apps_frame, text="no custom apps configured",
                font=ctk.CTkFont(family="Consolas", size=9), text_color=_C_DIM,
            ).pack(anchor="w", padx=8, pady=6)
            return

        for name, path in apps.items():
            row = ctk.CTkFrame(self._apps_frame, fg_color=_C_CARD,
                               border_width=1, border_color=_C_BORDER,
                               corner_radius=3, height=28)
            row.pack(fill="x", pady=1)
            row.pack_propagate(False)
            ctk.CTkLabel(
                row, text=f"{name}  →  {path[:38]}{'…' if len(path) > 38 else ''}",
                font=ctk.CTkFont(family="Consolas", size=9),
                text_color=_C_DIMMED, anchor="w",
            ).place(x=8, rely=0.5, anchor="w")
            ctk.CTkButton(
                row, text="✕", width=22, height=20,
                fg_color=_C_BG, hover_color="#1A0010",
                border_width=1, border_color="#4A1020", text_color="#AA3040",
                font=ctk.CTkFont(family="Consolas", size=9),
                corner_radius=3,
                command=lambda n=name: self._remove_custom_app(n),
            ).place(relx=1.0, x=-4, rely=0.5, anchor="e")
            self._app_rows.append(row)

    def _add_custom_app(self):
        name = self._new_app_name.get().strip()
        path = self._new_app_path.get().strip()
        if not name or not path:
            self._err_lbl.configure(text="name and path required.", text_color=_C_RED)
            return
        self._settings.custom_apps[name] = path
        self._new_app_name.delete(0, "end")
        self._new_app_path.delete(0, "end")
        self._refresh_apps_ui()
        self._err_lbl.configure(text="")

    def _remove_custom_app(self, name: str):
        self._settings.custom_apps.pop(name, None)
        self._refresh_apps_ui()

    # ── Speichern ─────────────────────────────────────────────────────────────

    def _save(self):
        raw_words = self._wake_entry.get()
        words = [w.strip().lower() for w in raw_words.split(",") if w.strip()]
        if not words:
            self._err_lbl.configure(text="at least one wake word required.",
                                    text_color=_C_RED)
            return

        hotkey = self._hotkey_display.get().strip()
        if not hotkey or hotkey == "...":
            self._err_lbl.configure(text="please record a hotkey first.",
                                    text_color=_C_RED)
            return

        rate_val = self._rate_var.get()
        rate_str = f"+{rate_val}%" if rate_val >= 0 else f"{rate_val}%"

        selected_mic = self._mic_var.get()
        input_device = -1
        for idx, name in self._input_devices:
            if name == selected_mic:
                input_device = idx
                break

        self._settings.wake_words         = words
        self._settings.activation_hotkey  = hotkey
        self._settings.grok_model         = self._model_var.get()
        self._settings.tts_voice          = self._voice_var.get()
        self._settings.tts_rate           = rate_str
        self._settings.autostart_enabled  = self._autostart_var.get()
        self._settings.input_device       = input_device
        self._settings.whisper_mode             = self._whisper_var.get()
        self._settings.multi_command            = self._multi_cmd_var.get()
        self._settings.noise_filter             = self._noise_filter_var.get()
        self._settings.long_term_context        = self._long_term_ctx_var.get()
        self._settings.conversation_resume      = self._conv_resume_var.get()
        self._settings.style_learning           = self._style_learning_var.get()
        self._settings.adaptive_response_time   = self._adapt_time_var.get()
        self._settings.adaptive_responses       = self._adapt_resp_var.get()

        pers_label = self._personality_var.get()
        pers_key = getattr(self, "_personality_display_to_key", {}).get(
            pers_label, "assistant")
        self._settings.personality        = pers_key
        self._settings.personality_custom = self._personality_custom_entry.get().strip()

        self._settings.vision_enabled       = self._vision_enabled_var.get()
        self._settings.vision_camera_index  = int(self._cam_idx_var.get())
        self._settings.gemini_api_key       = self._gemini_key_entry.get().strip()
        self._settings.vision_ollama_model  = self._ollama_vision_var.get()

        self._settings.save()

        if self._jarvis:
            try:
                self._jarvis.hotkey.update(hotkey)
            except Exception as e:
                logger.error(f"Hotkey-Update: {e}")
            try:
                import gc
                for obj in gc.get_objects():
                    if type(obj).__name__ == "HUD":
                        obj.update_hotkey_hint()
                        break
            except Exception:
                pass
            self._jarvis.synthesizer.voice = self._settings.tts_voice
            self._jarvis.synthesizer.rate  = rate_str
            self._jarvis.grok._model       = self._settings.grok_model

            new_device = None if input_device < 0 else input_device
            if self._jarvis.recognizer._device != new_device:
                self._jarvis.recognizer._device = new_device
                try:
                    self._jarvis.recognizer.recalibrate()
                except Exception as e:
                    logger.error(f"Mic recalibrate: {e}")

            try:
                self._jarvis.set_whisper_mode(self._settings.whisper_mode)
                self._jarvis.set_multi_command(self._settings.multi_command)
                self._jarvis.set_noise_filter(self._settings.noise_filter)
                self._jarvis.set_long_term_context(self._settings.long_term_context)
                self._jarvis.set_conversation_resume(self._settings.conversation_resume)
                self._jarvis.set_style_learning(self._settings.style_learning)
                self._jarvis.set_adaptive_response_time(self._settings.adaptive_response_time)
                self._jarvis.set_adaptive_responses(self._settings.adaptive_responses)
            except Exception as e:
                logger.error(f"Feature-Settings: {e}")

            try:
                self._jarvis.set_personality(self._settings.personality,
                                             self._settings.personality_custom)
                self._jarvis.set_vision_enabled(self._settings.vision_enabled)
                self._jarvis.camera.set_camera_index(self._settings.vision_camera_index)
                self._jarvis.vision.update_config(
                    groq_api_key=self._settings.grok_api_key,
                    gemini_api_key=self._settings.gemini_api_key,
                    ollama_model=self._settings.vision_ollama_model,
                )
                if hasattr(self._jarvis.launcher, "set_custom_apps"):
                    self._jarvis.launcher.set_custom_apps(self._settings.custom_apps)
            except Exception as e:
                logger.error(f"Vision/personality settings: {e}")

        autostart = Autostart()
        if self._settings.autostart_enabled:
            autostart.enable()
        else:
            autostart.disable()

        self._err_lbl.configure(text="saved.", text_color=_C_GREEN)
        self._root.after(1200, self._root.destroy)
