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
    """Gibt Liste von (index, name) für alle Eingabegeräte zurück."""
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
        self._input_devices: List[tuple] = _get_input_devices()  # (index, name)

    def run(self):
        self._root = ctk.CTk()
        self._root.title("Jarvis — Einstellungen")
        self._root.geometry("560x760")
        self._root.resizable(False, False)
        self._root.configure(fg_color="#08080F")
        self._build_ui()
        self._root.mainloop()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        # Header
        header = ctk.CTkFrame(self._root, fg_color="#0C0C1A", corner_radius=0)
        header.pack(fill="x")
        ctk.CTkLabel(
            header,
            text="J A R V I S  —  Einstellungen",
            font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold"),
            text_color="#3366FF",
        ).pack(side="left", padx=22, pady=16)

        # Scroll-Container
        scroll = ctk.CTkScrollableFrame(
            self._root, fg_color="#08080F",
            scrollbar_button_color="#1A1A30",
        )
        scroll.pack(fill="both", expand=True, padx=0, pady=0)

        # ── Abschnitt: Aktivierung ────────────────────────────────────
        self._section(scroll, "Aktivierung")

        hotkey_card = self._card(scroll)

        self._label(hotkey_card, "Tastenkürzel", "Drücke den Button und dann dein Kürzel")
        hk_row = ctk.CTkFrame(hotkey_card, fg_color="transparent")
        hk_row.pack(fill="x", padx=20, pady=(0, 6))

        self._hotkey_display = ctk.CTkEntry(
            hk_row,
            width=200, height=38,
            font=ctk.CTkFont(size=13),
            fg_color="#14142A",
            border_color="#222244",
            text_color="#88AAFF",
            state="readonly",
        )
        self._hotkey_display.pack(side="left")
        self._hotkey_display.configure(state="normal")
        self._hotkey_display.insert(0, self._settings.activation_hotkey)
        self._hotkey_display.configure(state="readonly")

        self._record_btn = ctk.CTkButton(
            hk_row,
            text="Neu belegen",
            width=120, height=38,
            font=ctk.CTkFont(size=12),
            fg_color="#1A3A8A",
            hover_color="#2244AA",
            corner_radius=8,
            command=self._start_record_hotkey,
        )
        self._record_btn.pack(side="left", padx=(10, 0))

        self._hotkey_hint = ctk.CTkLabel(
            hotkey_card,
            text="Tipp: z.B. ctrl+shift+j  oder  alt+j",
            font=ctk.CTkFont(size=11),
            text_color="#333355",
        )
        self._hotkey_hint.pack(anchor="w", padx=20, pady=(0, 16))

        # Wake-Words
        self._label(hotkey_card, "Wake-Words", "Kommagetrennt")
        self._wake_entry = ctk.CTkEntry(
            hotkey_card, height=38,
            font=ctk.CTkFont(size=12),
            fg_color="#14142A", border_color="#222244",
            text_color="#D0D0E8",
        )
        self._wake_entry.insert(0, ", ".join(self._settings.wake_words))
        self._wake_entry.pack(fill="x", padx=20, pady=(0, 16))

        # Mikrofon-Auswahl
        self._label(hotkey_card, "Mikrofon", "Standard = automatisch")
        mic_names = ["Standard (automatisch)"] + [name for _, name in self._input_devices]
        # Aktuellen Wert bestimmen
        current_device = self._settings.input_device
        current_mic_name = "Standard (automatisch)"
        for idx, name in self._input_devices:
            if idx == current_device:
                current_mic_name = name
                break
        self._mic_var = ctk.StringVar(value=current_mic_name)
        ctk.CTkOptionMenu(
            hotkey_card,
            values=mic_names,
            variable=self._mic_var,
            height=38,
            fg_color="#14142A",
            button_color="#1A3A8A",
            button_hover_color="#2244AA",
            dropdown_fg_color="#0E0E1E",
            font=ctk.CTkFont(size=12),
        ).pack(fill="x", padx=20, pady=(0, 8))

        # ── Headset-Prüfer ────────────────────────────────────────────
        test_row = ctk.CTkFrame(hotkey_card, fg_color="transparent")
        test_row.pack(fill="x", padx=20, pady=(0, 4))

        self._mic_test_btn = ctk.CTkButton(
            test_row,
            text="🎤 Mikrofon testen",
            width=150, height=34,
            font=ctk.CTkFont(size=12),
            fg_color="#1A3A8A",
            hover_color="#2244AA",
            corner_radius=8,
            command=self._start_mic_test,
        )
        self._mic_test_btn.pack(side="left")

        self._mic_result_lbl = ctk.CTkLabel(
            test_row,
            text="Klicke zum Testen...",
            font=ctk.CTkFont(size=12),
            text_color="#333355",
        )
        self._mic_result_lbl.pack(side="left", padx=(12, 0))

        # Pegel-Balken
        self._mic_bar_frame = ctk.CTkFrame(hotkey_card, fg_color="#0A0A18", corner_radius=6, height=10)
        self._mic_bar_frame.pack(fill="x", padx=20, pady=(4, 16))
        self._mic_bar = ctk.CTkFrame(self._mic_bar_frame, fg_color="#1A3A8A", corner_radius=6, height=10)
        self._mic_bar.place(relx=0, rely=0, relwidth=0.0, relheight=1.0)

        # ── Abschnitt: KI ────────────────────────────────────────────
        self._section(scroll, "KI & Sprache")
        ai_card = self._card(scroll)

        self._label(ai_card, "Modell")
        models = _MODELS_GROK if self._settings.api_provider == "grok" else _MODELS_GROQ
        self._model_var = ctk.StringVar(value=self._settings.grok_model)
        ctk.CTkOptionMenu(
            ai_card, values=models, variable=self._model_var,
            height=38, fg_color="#14142A",
            button_color="#1A3A8A", button_hover_color="#2244AA",
            dropdown_fg_color="#0E0E1E", font=ctk.CTkFont(size=12),
        ).pack(fill="x", padx=20, pady=(0, 14))

        self._label(ai_card, "Sprachausgabe-Stimme")
        self._voice_var = ctk.StringVar(value=self._settings.tts_voice)
        ctk.CTkOptionMenu(
            ai_card, values=_VOICES, variable=self._voice_var,
            height=38, fg_color="#14142A",
            button_color="#1A3A8A", button_hover_color="#2244AA",
            dropdown_fg_color="#0E0E1E", font=ctk.CTkFont(size=12),
        ).pack(fill="x", padx=20, pady=(0, 14))

        self._label(ai_card, "Sprechgeschwindigkeit")
        rate_row = ctk.CTkFrame(ai_card, fg_color="transparent")
        rate_row.pack(fill="x", padx=20, pady=(0, 16))

        current_rate = int(self._settings.tts_rate.replace("%", "").replace("+", ""))
        self._rate_var = ctk.IntVar(value=current_rate)
        self._rate_lbl = ctk.CTkLabel(
            rate_row, text=f"{current_rate:+d}%",
            font=ctk.CTkFont(size=12), text_color="#8888AA", width=48,
        )
        self._rate_lbl.pack(side="right")
        ctk.CTkSlider(
            rate_row, from_=-30, to=30,
            variable=self._rate_var,
            command=self._on_rate_change,
            height=20, progress_color="#1A3A8A",
        ).pack(side="left", fill="x", expand=True)

        # ── Abschnitt: System ────────────────────────────────────────
        self._section(scroll, "System")
        sys_card = self._card(scroll)

        self._autostart_var = ctk.BooleanVar(value=self._settings.autostart_enabled)
        ctk.CTkCheckBox(
            sys_card,
            text="Mit Windows automatisch starten",
            variable=self._autostart_var,
            font=ctk.CTkFont(size=13), text_color="#AAAACC",
            checkmark_color="#3366FF", fg_color="#1A3A8A",
            hover_color="#2244AA", border_color="#333355",
        ).pack(anchor="w", padx=20, pady=(14, 20))

        # ── Abschnitt: Sprach-Features ────────────────────────────────
        self._section(scroll, "Sprach-Features")
        speech_card = self._card(scroll)

        self._whisper_var = ctk.BooleanVar(value=self._settings.whisper_mode)
        ctk.CTkCheckBox(
            speech_card,
            text="Flüstermodus — auf leise Sprache reagieren",
            variable=self._whisper_var,
            font=ctk.CTkFont(size=13), text_color="#AAAACC",
            checkmark_color="#3366FF", fg_color="#1A3A8A",
            hover_color="#2244AA", border_color="#333355",
        ).pack(anchor="w", padx=20, pady=(14, 6))
        ctk.CTkLabel(
            speech_card,
            text="Jarvis erkennt Flüstersprache und antwortet angepasst",
            font=ctk.CTkFont(size=11), text_color="#333355",
        ).pack(anchor="w", padx=20, pady=(0, 8))

        self._multi_cmd_var = ctk.BooleanVar(value=self._settings.multi_command)
        ctk.CTkCheckBox(
            speech_card,
            text="Mehrfachbefehle — mehrere Aktionen in einem Satz",
            variable=self._multi_cmd_var,
            font=ctk.CTkFont(size=13), text_color="#AAAACC",
            checkmark_color="#3366FF", fg_color="#1A3A8A",
            hover_color="#2244AA", border_color="#333355",
        ).pack(anchor="w", padx=20, pady=(6, 6))
        ctk.CTkLabel(
            speech_card,
            text="z.B. \"Öffne Spotify und dann setze einen Timer für 10 Minuten\"",
            font=ctk.CTkFont(size=11), text_color="#333355",
        ).pack(anchor="w", padx=20, pady=(0, 16))

        # ── Abschnitt: Lern & Adaption ────────────────────────────────
        self._section(scroll, "Lern & Adaption")
        adapt_card = self._card(scroll)

        self._noise_filter_var = ctk.BooleanVar(value=self._settings.noise_filter)
        ctk.CTkCheckBox(
            adapt_card,
            text="Geräuschfilter — Hintergrundgeräusche reduzieren",
            variable=self._noise_filter_var,
            font=ctk.CTkFont(size=13), text_color="#AAAACC",
            checkmark_color="#3366FF", fg_color="#1A3A8A",
            hover_color="#2244AA", border_color="#333355",
        ).pack(anchor="w", padx=20, pady=(14, 4))
        ctk.CTkLabel(
            adapt_card,
            text="Spektrale Subtraktion vor der Spracherkennung (laute Umgebungen)",
            font=ctk.CTkFont(size=11), text_color="#333355",
        ).pack(anchor="w", padx=20, pady=(0, 8))

        self._long_term_ctx_var = ctk.BooleanVar(value=self._settings.long_term_context)
        ctk.CTkCheckBox(
            adapt_card,
            text="Langzeit-Kontext — frühere Gespräche einbeziehen",
            variable=self._long_term_ctx_var,
            font=ctk.CTkFont(size=13), text_color="#AAAACC",
            checkmark_color="#3366FF", fg_color="#1A3A8A",
            hover_color="#2244AA", border_color="#333355",
        ).pack(anchor="w", padx=20, pady=(4, 4))
        ctk.CTkLabel(
            adapt_card,
            text="Jarvis erinnert sich an Themen aus früheren Sitzungen",
            font=ctk.CTkFont(size=11), text_color="#333355",
        ).pack(anchor="w", padx=20, pady=(0, 8))

        self._conv_resume_var = ctk.BooleanVar(value=self._settings.conversation_resume)
        ctk.CTkCheckBox(
            adapt_card,
            text="Gespräch fortsetzen — beim Start an letztes Gespräch anknüpfen",
            variable=self._conv_resume_var,
            font=ctk.CTkFont(size=13), text_color="#AAAACC",
            checkmark_color="#3366FF", fg_color="#1A3A8A",
            hover_color="#2244AA", border_color="#333355",
        ).pack(anchor="w", padx=20, pady=(4, 4))
        ctk.CTkLabel(
            adapt_card,
            text="Lädt die letzten Nachrichten der vorherigen Session in den Verlauf",
            font=ctk.CTkFont(size=11), text_color="#333355",
        ).pack(anchor="w", padx=20, pady=(0, 8))

        self._style_learning_var = ctk.BooleanVar(value=self._settings.style_learning)
        ctk.CTkCheckBox(
            adapt_card,
            text="Sprachstil lernen — Ton und Länge automatisch anpassen",
            variable=self._style_learning_var,
            font=ctk.CTkFont(size=13), text_color="#AAAACC",
            checkmark_color="#3366FF", fg_color="#1A3A8A",
            hover_color="#2244AA", border_color="#333355",
        ).pack(anchor="w", padx=20, pady=(4, 4))
        ctk.CTkLabel(
            adapt_card,
            text="Analysiert deine Befehle und lernt deinen bevorzugten Stil",
            font=ctk.CTkFont(size=11), text_color="#333355",
        ).pack(anchor="w", padx=20, pady=(0, 8))

        self._adapt_time_var = ctk.BooleanVar(value=self._settings.adaptive_response_time)
        ctk.CTkCheckBox(
            adapt_card,
            text="Reaktionszeit anpassen — schneller bei kurzen Befehlen",
            variable=self._adapt_time_var,
            font=ctk.CTkFont(size=13), text_color="#AAAACC",
            checkmark_color="#3366FF", fg_color="#1A3A8A",
            hover_color="#2244AA", border_color="#333355",
        ).pack(anchor="w", padx=20, pady=(4, 4))
        ctk.CTkLabel(
            adapt_card,
            text="Kürzeres Token-Limit für einfache Befehle (Timer, Apps öffnen…)",
            font=ctk.CTkFont(size=11), text_color="#333355",
        ).pack(anchor="w", padx=20, pady=(0, 8))

        self._adapt_resp_var = ctk.BooleanVar(value=self._settings.adaptive_responses)
        ctk.CTkCheckBox(
            adapt_card,
            text="Adaptive Antworten — Verhalten dynamisch anpassen",
            variable=self._adapt_resp_var,
            font=ctk.CTkFont(size=13), text_color="#AAAACC",
            checkmark_color="#3366FF", fg_color="#1A3A8A",
            hover_color="#2244AA", border_color="#333355",
        ).pack(anchor="w", padx=20, pady=(4, 4))
        ctk.CTkLabel(
            adapt_card,
            text="Wendet gelernte Stil-Präferenzen automatisch an (ab 15 Befehlen)",
            font=ctk.CTkFont(size=11), text_color="#333355",
        ).pack(anchor="w", padx=20, pady=(0, 16))

        # ── Abschnitt: Persoenlichkeit ────────────────────────────────
        self._section(scroll, "Persoenlichkeit")
        pers_card = self._card(scroll)

        self._label(pers_card, "Charakter-Preset", "Wie soll Jarvis sich verhalten?")
        _PERSONALITY_PRESETS = [
            "assistant", "friend", "butler", "coach", "scientist", "custom"
        ]
        _PERSONALITY_LABELS = {
            "assistant":  "Assistent (Standard)",
            "friend":     "Freund (locker, du-Form)",
            "butler":     "Butler (formell, Sie-Form)",
            "coach":      "Coach (motivierend)",
            "scientist":  "Wissenschaftler (analytisch)",
            "custom":     "Eigene Beschreibung",
        }
        self._personality_var = ctk.StringVar(
            value=_PERSONALITY_LABELS.get(self._settings.personality, "Assistent (Standard)")
        )
        self._personality_display_to_key = {v: k for k, v in _PERSONALITY_LABELS.items()}
        ctk.CTkOptionMenu(
            pers_card,
            values=[_PERSONALITY_LABELS[k] for k in _PERSONALITY_PRESETS],
            variable=self._personality_var,
            height=38, fg_color="#14142A",
            button_color="#1A3A8A", button_hover_color="#2244AA",
            dropdown_fg_color="#0E0E1E", font=ctk.CTkFont(size=12),
        ).pack(fill="x", padx=20, pady=(0, 8))

        self._label(pers_card, "Eigene Beschreibung", "Nur wenn 'Eigene' gewaehlt")
        self._personality_custom_entry = ctk.CTkEntry(
            pers_card, height=38,
            font=ctk.CTkFont(size=12),
            fg_color="#14142A", border_color="#222244",
            text_color="#D0D0E8",
            placeholder_text="z.B. 'Antworte immer mit Wiener Dialekt und Humor'",
        )
        self._personality_custom_entry.insert(0, self._settings.personality_custom)
        self._personality_custom_entry.pack(fill="x", padx=20, pady=(0, 16))

        # ── Abschnitt: Vision ─────────────────────────────────────────
        self._section(scroll, "Vision (Kamera)")
        vision_card = self._card(scroll)

        self._vision_enabled_var = ctk.BooleanVar(value=self._settings.vision_enabled)
        ctk.CTkCheckBox(
            vision_card,
            text="Kamera-Feature aktivieren",
            variable=self._vision_enabled_var,
            font=ctk.CTkFont(size=13), text_color="#AAAACC",
            checkmark_color="#3366FF", fg_color="#1A3A8A",
            hover_color="#2244AA", border_color="#333355",
        ).pack(anchor="w", padx=20, pady=(14, 4))
        ctk.CTkLabel(
            vision_card,
            text="Jarvis kann Fotos aufnehmen und mit Vision-KI analysieren",
            font=ctk.CTkFont(size=11), text_color="#333355",
        ).pack(anchor="w", padx=20, pady=(0, 8))

        self._label(vision_card, "Kamera-Index", "0 = Standard-Webcam")
        self._cam_idx_var = ctk.IntVar(value=self._settings.vision_camera_index)
        cam_row = ctk.CTkFrame(vision_card, fg_color="transparent")
        cam_row.pack(fill="x", padx=20, pady=(0, 8))
        self._cam_idx_lbl = ctk.CTkLabel(
            cam_row, text=str(self._settings.vision_camera_index),
            font=ctk.CTkFont(size=12), text_color="#8888AA", width=28,
        )
        self._cam_idx_lbl.pack(side="right")
        ctk.CTkSlider(
            cam_row, from_=0, to=4,
            variable=self._cam_idx_var,
            command=lambda v: self._cam_idx_lbl.configure(text=str(int(float(v)))),
            height=20, progress_color="#1A3A8A", number_of_steps=4,
        ).pack(side="left", fill="x", expand=True)

        self._label(vision_card, "Gemini API Key", "Kostenloser Google-Key (optional)")
        self._gemini_key_entry = ctk.CTkEntry(
            vision_card, height=38,
            font=ctk.CTkFont(size=12),
            fg_color="#14142A", border_color="#222244",
            text_color="#D0D0E8", show="*",
            placeholder_text="AIza... (leer lassen um Groq zu verwenden)",
        )
        self._gemini_key_entry.insert(0, self._settings.gemini_api_key)
        self._gemini_key_entry.pack(fill="x", padx=20, pady=(0, 8))

        self._label(vision_card, "Ollama Vision-Modell", "Fallback wenn kein API-Key")
        self._ollama_vision_var = ctk.StringVar(value=self._settings.vision_ollama_model)
        ctk.CTkOptionMenu(
            vision_card,
            values=["moondream", "llava", "llava:13b", "bakllava"],
            variable=self._ollama_vision_var,
            height=38, fg_color="#14142A",
            button_color="#1A3A8A", button_hover_color="#2244AA",
            dropdown_fg_color="#0E0E1E", font=ctk.CTkFont(size=12),
        ).pack(fill="x", padx=20, pady=(0, 16))

        # ── Abschnitt: App Integration ────────────────────────────────
        self._section(scroll, "App Integration")
        apps_card = self._card(scroll)

        ctk.CTkLabel(
            apps_card,
            text="Eigene Apps hinzufuegen (Name → Pfad zur .exe)",
            font=ctk.CTkFont(size=11), text_color="#333355",
        ).pack(anchor="w", padx=20, pady=(12, 6))

        # Scrollbare Liste
        self._apps_frame = ctk.CTkScrollableFrame(
            apps_card, fg_color="#0A0A18", corner_radius=6, height=100,
            scrollbar_button_color="#1A1A30",
        )
        self._apps_frame.pack(fill="x", padx=20, pady=(0, 6))
        self._app_rows: list = []
        self._refresh_apps_ui()

        # Hinzufuegen-Zeile
        add_row = ctk.CTkFrame(apps_card, fg_color="transparent")
        add_row.pack(fill="x", padx=20, pady=(0, 6))
        self._new_app_name = ctk.CTkEntry(
            add_row, height=32, width=100,
            font=ctk.CTkFont(size=11),
            fg_color="#14142A", border_color="#222244",
            text_color="#D0D0E8", placeholder_text="Name",
        )
        self._new_app_name.pack(side="left")
        self._new_app_path = ctk.CTkEntry(
            add_row, height=32,
            font=ctk.CTkFont(size=11),
            fg_color="#14142A", border_color="#222244",
            text_color="#D0D0E8", placeholder_text="C:\\Pfad\\app.exe",
        )
        self._new_app_path.pack(side="left", fill="x", expand=True, padx=(4, 4))
        ctk.CTkButton(
            add_row, text="+", width=32, height=32,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#1A3A8A", hover_color="#2244AA", corner_radius=6,
            command=self._add_custom_app,
        ).pack(side="left")

        ctk.CTkLabel(
            apps_card,
            text="Jarvis kann diese Apps per Sprache starten (z.B. 'Oeffne Spotify')",
            font=ctk.CTkFont(size=11), text_color="#333355",
        ).pack(anchor="w", padx=20, pady=(0, 16))

        # ── Fehler & Speichern ────────────────────────────────────────
        self._err_lbl = ctk.CTkLabel(
            self._root, text="", text_color="#FF4455",
            font=ctk.CTkFont(size=12),
        )
        self._err_lbl.pack(pady=(8, 0))

        ctk.CTkButton(
            self._root,
            text="Speichern",
            height=48,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#1A3A8A", hover_color="#2244AA",
            corner_radius=10,
            command=self._save,
        ).pack(fill="x", padx=30, pady=(4, 16))

    # ------------------------------------------------------------------
    # Hotkey aufnehmen
    # ------------------------------------------------------------------

    def _start_record_hotkey(self):
        if self._recording_hotkey:
            return
        self._recording_hotkey = True
        self._record_btn.configure(text="Drücke jetzt...", fg_color="#443300")
        self._hotkey_display.configure(state="normal")
        self._hotkey_display.delete(0, "end")
        self._hotkey_display.insert(0, "...")
        self._hotkey_display.configure(state="readonly")
        threading.Thread(target=self._capture_hotkey, daemon=True).start()

    def _capture_hotkey(self):
        try:
            # suppress=True damit der Hotkey nicht gleichzeitig woanders landet
            combo = keyboard.read_hotkey(suppress=True)
            # Escape = Abbruch
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
        self._record_btn.configure(text="Neu belegen", fg_color="#1A3A8A")

    # ------------------------------------------------------------------
    # Hilfsmethoden
    # ------------------------------------------------------------------

    def _on_rate_change(self, val):
        v = int(float(val))
        self._rate_lbl.configure(text=f"{v:+d}%")

    def _section(self, parent, title: str):
        ctk.CTkLabel(
            parent, text=title.upper(),
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color="#333355",
        ).pack(anchor="w", padx=30, pady=(18, 4))

    def _card(self, parent) -> ctk.CTkFrame:
        card = ctk.CTkFrame(parent, fg_color="#0E0E1E", corner_radius=14)
        card.pack(fill="x", padx=24, pady=(0, 4))
        return card

    def _label(self, parent, text: str, hint: str = ""):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=(14, 4))
        ctk.CTkLabel(
            row, text=text,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#9999BB",
        ).pack(side="left")
        if hint:
            ctk.CTkLabel(
                row, text=hint,
                font=ctk.CTkFont(size=11), text_color="#333355",
            ).pack(side="right")

    # ------------------------------------------------------------------
    # Mikrofon-Test
    # ------------------------------------------------------------------

    def _start_mic_test(self):
        """Startet einen 2-Sekunden-Aufnahmetest im Hintergrund."""
        self._mic_test_btn.configure(text="⏳ Aufnahme...", state="disabled")
        self._mic_result_lbl.configure(text="Bitte sprich jetzt...", text_color="#BB8800")
        self._mic_bar.place(relwidth=0.0)
        threading.Thread(target=self._run_mic_test, daemon=True).start()

    def _run_mic_test(self):
        import numpy as np

        # Aktuell gewähltes Gerät ermitteln
        selected = self._mic_var.get()
        device = None
        for idx, name in self._input_devices:
            if name == selected:
                device = idx
                break

        try:
            samples = sd.rec(
                int(2.0 * 16000), samplerate=16000,
                channels=1, dtype="int16", device=device,
            )
            sd.wait()
            rms = np.sqrt(np.mean(samples.astype(np.float64) ** 2))
            db  = 20.0 * np.log10(max(rms, 1.0))
            self._root.after(0, lambda d=db: self._show_mic_result(d))
        except Exception as e:
            self._root.after(0, lambda: self._show_mic_error(str(e)))

    def _show_mic_result(self, db: float):
        """Zeigt das Testergebnis mit Farb-Feedback."""
        # Pegelbalken: 0 dB → 0%, 60 dB → 100%
        fill = min(db / 60.0, 1.0)
        self._mic_bar.place(relwidth=fill)

        if db < 10:
            color = "#CC2233"
            icon  = "✗"
            text  = f"Sehr leise ({db:.0f} dB) — Mikrofon stumm oder falsches Gerät?"
        elif db < 28:
            color = "#BB8800"
            icon  = "▲"
            text  = f"Leise ({db:.0f} dB) — näher ans Mikrofon?"
        elif db < 52:
            color = "#22AA55"
            icon  = "✓"
            text  = f"Gut ({db:.0f} dB) — Mikrofon funktioniert"
        else:
            color = "#BB8800"
            icon  = "▲"
            text  = f"Sehr laut ({db:.0f} dB) — viel Hintergrundgeräusch?"

        self._mic_bar.configure(fg_color=color)
        self._mic_result_lbl.configure(text=f"{icon} {text}", text_color=color)
        self._mic_test_btn.configure(text="🎤 Mikrofon testen", state="normal")

    def _show_mic_error(self, error: str):
        self._mic_result_lbl.configure(
            text=f"✗ Fehler: {error[:60]}", text_color="#CC2233"
        )
        self._mic_test_btn.configure(text="🎤 Mikrofon testen", state="normal")

    # ------------------------------------------------------------------
    # App Integration Hilfsmethoden
    # ------------------------------------------------------------------

    def _refresh_apps_ui(self):
        """Baut die App-Liste neu auf."""
        for widget in self._apps_frame.winfo_children():
            widget.destroy()
        self._app_rows.clear()

        apps = self._settings.custom_apps
        if not apps:
            ctk.CTkLabel(
                self._apps_frame,
                text="Keine eigenen Apps konfiguriert.",
                font=ctk.CTkFont(size=10), text_color="#333355",
            ).pack(anchor="w", padx=8, pady=4)
            return

        for name, path in apps.items():
            row = ctk.CTkFrame(self._apps_frame, fg_color="#0E0E1E", corner_radius=4, height=28)
            row.pack(fill="x", pady=1)
            row.pack_propagate(False)
            ctk.CTkLabel(
                row, text=f"{name}: {path[:40]}{'...' if len(path) > 40 else ''}",
                font=ctk.CTkFont(size=10), text_color="#9999BB", anchor="w",
            ).place(x=6, rely=0.5, anchor="w")
            ctk.CTkButton(
                row, text="×", width=22, height=20,
                fg_color="#3A0A0A", hover_color="#CC2233",
                text_color="#FF6666", font=ctk.CTkFont(size=11),
                corner_radius=3,
                command=lambda n=name: self._remove_custom_app(n),
            ).place(relx=1.0, x=-4, rely=0.5, anchor="e")
            self._app_rows.append(row)

    def _add_custom_app(self):
        name = self._new_app_name.get().strip()
        path = self._new_app_path.get().strip()
        if not name or not path:
            self._err_lbl.configure(text="Name und Pfad benoetigt.", text_color="#FF4455")
            return
        self._settings.custom_apps[name] = path
        self._new_app_name.delete(0, "end")
        self._new_app_path.delete(0, "end")
        self._refresh_apps_ui()
        self._err_lbl.configure(text="")

    def _remove_custom_app(self, name: str):
        self._settings.custom_apps.pop(name, None)
        self._refresh_apps_ui()

    # ------------------------------------------------------------------
    # Speichern
    # ------------------------------------------------------------------

    def _save(self):
        # Wake-Words
        raw_words = self._wake_entry.get()
        words = [w.strip().lower() for w in raw_words.split(",") if w.strip()]
        if not words:
            self._err_lbl.configure(text="Mindestens ein Wake-Word erforderlich.")
            return

        # Hotkey
        hotkey = self._hotkey_display.get().strip()
        if not hotkey or hotkey == "...":
            self._err_lbl.configure(
                text="Bitte zuerst ein Tastenkürzel aufnehmen.",
                text_color="#FF4455",
            )
            return

        # Rate
        rate_val = self._rate_var.get()
        rate_str = f"+{rate_val}%" if rate_val >= 0 else f"{rate_val}%"

        # Mikrofon-Index ermitteln
        selected_mic = self._mic_var.get()
        input_device = -1
        for idx, name in self._input_devices:
            if name == selected_mic:
                input_device = idx
                break

        # Speichern
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

        # Persoenlichkeit
        pers_label = self._personality_var.get()
        pers_key = getattr(self, "_personality_display_to_key", {}).get(pers_label, "assistant")
        self._settings.personality        = pers_key
        self._settings.personality_custom = self._personality_custom_entry.get().strip()

        # Vision
        self._settings.vision_enabled       = self._vision_enabled_var.get()
        self._settings.vision_camera_index  = int(self._cam_idx_var.get())
        self._settings.gemini_api_key       = self._gemini_key_entry.get().strip()
        self._settings.vision_ollama_model  = self._ollama_vision_var.get()

        # custom_apps werden direkt in _settings.custom_apps gepflegt
        self._settings.save()

        # Live anwenden
        if self._jarvis:
            try:
                self._jarvis.hotkey.update(hotkey)
                logger.info(f"Hotkey live aktualisiert: {hotkey}")
            except Exception as e:
                logger.error(f"Hotkey-Update Fehler: {e}")

            # HUD-Hinweistext aktualisieren
            try:
                from ui.tray import TrayIcon  # zirkulär vermeiden
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

            # Mikrofon-Gerät live aktualisieren falls geändert
            new_device = None if input_device < 0 else input_device
            if self._jarvis.recognizer._device != new_device:
                self._jarvis.recognizer._device = new_device
                try:
                    self._jarvis.recognizer.recalibrate()
                    logger.info(f"Mikrofon-Gerät aktualisiert: {input_device}")
                except Exception as e:
                    logger.error(f"Mikrofon-Kalibrierung Fehler: {e}")

            # Sprach-Feature-Einstellungen live anwenden
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
                logger.error(f"Feature-Settings Fehler: {e}")

            # Neue Features live anwenden
            try:
                self._jarvis.set_personality(
                    self._settings.personality,
                    self._settings.personality_custom,
                )
                self._jarvis.set_vision_enabled(self._settings.vision_enabled)
                # Kamera-Index und Vision-Config aktualisieren
                self._jarvis.camera.set_camera_index(self._settings.vision_camera_index)
                self._jarvis.vision.update_config(
                    groq_api_key=self._settings.grok_api_key,
                    gemini_api_key=self._settings.gemini_api_key,
                    ollama_model=self._settings.vision_ollama_model,
                )
                # Custom Apps in AppLauncher uebertragen
                if hasattr(self._jarvis.launcher, "set_custom_apps"):
                    self._jarvis.launcher.set_custom_apps(self._settings.custom_apps)
            except Exception as e:
                logger.error(f"Neue Feature-Settings Fehler: {e}")

        autostart = Autostart()
        if self._settings.autostart_enabled:
            autostart.enable()
        else:
            autostart.disable()

        self._err_lbl.configure(text="Gespeichert.", text_color="#33AA55")
        self._root.after(1500, self._root.destroy)
