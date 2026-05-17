import logging
from typing import Optional

import customtkinter as ctk

from config.settings import Settings
from services.system.autostart import Autostart

logger = logging.getLogger("jarvis.setup")

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

_VOICES = [
    "de-DE-FlorianMultilingualNeural",   # natürlichster Klang (empfohlen)
    "de-DE-KillianNeural",
    "de-DE-ConradNeural",
    "de-DE-KatjaNeural",
    "de-AT-JonasNeural",
    "de-AT-IngridNeural",
    "de-CH-LeniNeural",
]

_MODELS_GROK = [
    "grok-3",
    "grok-3-mini",
    "grok-2-1212",
    "grok-beta",
]

_MODELS_GROQ = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "llama3-70b-8192",
    "mixtral-8x7b-32768",
    "gemma2-9b-it",
]


def _detect_provider(key: str) -> str:
    if key.startswith("xai-"):
        return "grok"
    if key.startswith("gsk_"):
        return "groq"
    return "unknown"


class SetupDialog:
    def __init__(self, settings: Optional[Settings] = None):
        self._settings = settings or Settings()
        self._saved = False
        self._root: Optional[ctk.CTk] = None
        self._model_menu: Optional[ctk.CTkOptionMenu] = None

    def run(self) -> bool:
        self._root = ctk.CTk()
        self._root.title("Jarvis — Einrichtung")
        self._root.geometry("520x660")
        self._root.resizable(False, False)
        self._root.configure(fg_color="#08080F")
        self._build_ui()
        self._root.mainloop()
        return self._saved

    # ------------------------------------------------------------------

    def _build_ui(self):
        # ── Logo ──────────────────────────────────────────────────────
        logo = ctk.CTkFrame(self._root, fg_color="transparent")
        logo.pack(pady=(32, 4))

        ctk.CTkLabel(
            logo,
            text="J A R V I S",
            font=ctk.CTkFont(family="Segoe UI", size=34, weight="bold"),
            text_color="#3366FF",
        ).pack()

        ctk.CTkLabel(
            logo,
            text="KI-Assistent — Ersteinrichtung",
            font=ctk.CTkFont(size=13),
            text_color="#444466",
        ).pack(pady=(4, 0))

        # ── Formular ──────────────────────────────────────────────────
        card = ctk.CTkFrame(self._root, fg_color="#0E0E1E", corner_radius=16)
        card.pack(fill="x", padx=30, pady=18)

        # API-Key
        self._build_field(card, label="API-Schlüssel", hint="Grok (xai-...) oder Groq (gsk_...)")
        self._api_entry = ctk.CTkEntry(
            card,
            placeholder_text="xai-...  oder  gsk_...",
            height=42,
            font=ctk.CTkFont(size=13),
            show="•",
            fg_color="#14142A",
            border_color="#222244",
            text_color="#D0D0E8",
            corner_radius=8,
        )
        if self._settings.grok_api_key:
            self._api_entry.insert(0, self._settings.grok_api_key)
        self._api_entry.pack(fill="x", padx=20, pady=(0, 4))
        self._api_entry.bind("<FocusOut>", self._on_key_changed)
        self._api_entry.bind("<KeyRelease>", self._on_key_changed)

        # Provider-Anzeige
        self._provider_lbl = ctk.CTkLabel(
            card,
            text="",
            font=ctk.CTkFont(size=11),
            text_color="#446644",
        )
        self._provider_lbl.pack(anchor="w", padx=20, pady=(0, 10))

        # Modell
        self._build_field(card, label="KI-Modell")
        current_model = self._settings.grok_model
        initial_models = _MODELS_GROQ if self._settings.grok_api_key.startswith("gsk_") else _MODELS_GROK
        if current_model not in initial_models:
            current_model = initial_models[0]

        self._model_var = ctk.StringVar(value=current_model)
        self._model_menu = ctk.CTkOptionMenu(
            card,
            values=initial_models,
            variable=self._model_var,
            height=42,
            fg_color="#14142A",
            button_color="#1A3A8A",
            button_hover_color="#2244AA",
            dropdown_fg_color="#0E0E1E",
            font=ctk.CTkFont(size=13),
            corner_radius=8,
        )
        self._model_menu.pack(fill="x", padx=20, pady=(0, 16))

        # Stimme
        self._build_field(card, label="Sprachausgabe-Stimme")
        self._voice_var = ctk.StringVar(value=self._settings.tts_voice)
        ctk.CTkOptionMenu(
            card,
            values=_VOICES,
            variable=self._voice_var,
            height=42,
            fg_color="#14142A",
            button_color="#1A3A8A",
            button_hover_color="#2244AA",
            dropdown_fg_color="#0E0E1E",
            font=ctk.CTkFont(size=13),
            corner_radius=8,
        ).pack(fill="x", padx=20, pady=(0, 20))

        # Autostart
        self._autostart_var = ctk.BooleanVar(value=self._settings.autostart_enabled)
        ctk.CTkCheckBox(
            card,
            text="Automatisch mit Windows starten",
            variable=self._autostart_var,
            font=ctk.CTkFont(size=13),
            text_color="#AAAACC",
            checkmark_color="#3366FF",
            fg_color="#1A3A8A",
            hover_color="#2244AA",
            border_color="#333355",
        ).pack(anchor="w", padx=20, pady=(0, 22))

        # Fehler-Label
        self._error_lbl = ctk.CTkLabel(
            self._root, text="", text_color="#FF4455", font=ctk.CTkFont(size=12)
        )
        self._error_lbl.pack()

        # Speichern
        ctk.CTkButton(
            self._root,
            text="Speichern & Starten",
            height=50,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#1A3A8A",
            hover_color="#2244AA",
            corner_radius=12,
            command=self._save,
        ).pack(fill="x", padx=30, pady=(6, 6))

        ctk.CTkLabel(
            self._root,
            text="Grok: x.ai/api  •  Groq: console.groq.com",
            font=ctk.CTkFont(size=11),
            text_color="#2A2A3A",
        ).pack(pady=(2, 0))

        # Initiales Update der Provider-Anzeige
        if self._settings.grok_api_key:
            self._update_provider_ui(self._settings.grok_api_key)

    # ------------------------------------------------------------------

    def _build_field(self, parent, label: str, hint: str = ""):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=(14, 4))
        ctk.CTkLabel(
            row,
            text=label,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#9999BB",
        ).pack(side="left")
        if hint:
            ctk.CTkLabel(
                row,
                text=hint,
                font=ctk.CTkFont(size=11),
                text_color="#334455",
            ).pack(side="right")

    def _on_key_changed(self, _event=None):
        key = self._api_entry.get().strip()
        self._update_provider_ui(key)

    def _update_provider_ui(self, key: str):
        provider = _detect_provider(key)
        if provider == "grok":
            self._provider_lbl.configure(
                text="✓  xAI Grok erkannt", text_color="#33AA55"
            )
            self._model_menu.configure(values=_MODELS_GROK)
            if self._model_var.get() not in _MODELS_GROK:
                self._model_var.set(_MODELS_GROK[0])
        elif provider == "groq":
            self._provider_lbl.configure(
                text="✓  Groq erkannt", text_color="#33AA55"
            )
            self._model_menu.configure(values=_MODELS_GROQ)
            if self._model_var.get() not in _MODELS_GROQ:
                self._model_var.set(_MODELS_GROQ[0])
        elif key:
            self._provider_lbl.configure(
                text="⚠  Unbekanntes Format (xai-... oder gsk_... erwartet)",
                text_color="#AA6600",
            )
        else:
            self._provider_lbl.configure(text="")

    def _save(self):
        key = self._api_entry.get().strip()

        if not key:
            self._error_lbl.configure(text="Bitte einen API-Schlüssel eingeben.")
            return

        provider = _detect_provider(key)
        if provider == "unknown":
            self._error_lbl.configure(
                text="Ungültiger Schlüssel — Grok beginnt mit 'xai-', Groq mit 'gsk_'."
            )
            return

        self._settings.grok_api_key      = key
        self._settings.grok_model        = self._model_var.get()
        self._settings.tts_voice         = self._voice_var.get()
        self._settings.autostart_enabled = self._autostart_var.get()
        self._settings.api_provider      = provider
        self._settings.save()

        autostart = Autostart()
        if self._settings.autostart_enabled:
            autostart.enable()
        else:
            autostart.disable()

        self._saved = True
        self._root.destroy()
