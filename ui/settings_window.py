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

        autostart = Autostart()
        if self._settings.autostart_enabled:
            autostart.enable()
        else:
            autostart.disable()

        self._err_lbl.configure(text="Gespeichert.", text_color="#33AA55")
        self._root.after(1500, self._root.destroy)
