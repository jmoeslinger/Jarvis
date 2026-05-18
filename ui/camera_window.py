"""
Jarvis Kamera-Fenster — Live-Vorschau, Vision-KI-Analyse und Kamera-Einstellungen.

Layout:
  Links:  Echtzeit-Kamera-Vorschau (640×480, 30 fps)
  Rechts: Einstellungen (Index, Provider, Keys) + Analyse-Ergebnis

Threads:
  _preview_loop()  liest Frames via OpenCV im Hintergrund
  after()          sendet jeden Frame sicher in den Tk-Hauptthread
"""
import base64
import logging
import threading
import time
from typing import Optional

import customtkinter as ctk

logger = logging.getLogger("jarvis.camera_win")


class CameraWindow:
    """
    Vollstaendiges Kamera-Fenster als CTkToplevel im HUD-Mainloop.
    Oeffnen via show(hud_root=...).
    """

    def __init__(self, jarvis):
        self._jarvis = jarvis
        self._win: Optional[ctk.CTkToplevel] = None
        self._hud_root = None

        # Preview
        self._preview_label: Optional[ctk.CTkLabel] = None
        self._status_lbl:    Optional[ctk.CTkLabel] = None
        self._running = False
        self._cap = None                       # cv2.VideoCapture
        self._preview_thread: Optional[threading.Thread] = None
        self._current_frame_b64: Optional[str] = None

        # UI-Widgets
        self._cam_idx_var:      Optional[ctk.StringVar] = None
        self._flip_var:         Optional[ctk.BooleanVar] = None
        self._gemini_entry:     Optional[ctk.CTkEntry]  = None
        self._ollama_model_var: Optional[ctk.StringVar] = None
        self._question_entry:   Optional[ctk.CTkEntry]  = None
        self._result_box:       Optional[ctk.CTkTextbox] = None
        self._result_lbl:       Optional[ctk.CTkLabel]  = None
        self._analyze_btn:      Optional[ctk.CTkButton] = None
        self._cam_info_lbl:     Optional[ctk.CTkLabel]  = None

    # ── Oeffentlich ────────────────────────────────────────────────────────────

    def show(self, hud_root=None):
        """
        Oeffnet das Kamera-Fenster oder bringt es in den Vordergrund.
        hud_root muss der Tk-Root des HUD sein (Hauptthread!).
        """
        if hud_root:
            self._hud_root = hud_root

        if self._win and self._win.winfo_exists():
            self._win.state("normal")
            self._win.deiconify()
            self._win.lift()
            self._win.focus_force()
            return

        if self._hud_root:
            self._hud_root.after(0, self._create_window)

    def close(self):
        """Schliesst das Fenster und stoppt die Preview."""
        self._on_close()

    # ── Fenster erstellen (Hauptthread) ────────────────────────────────────────

    def _create_window(self):
        self._win = ctk.CTkToplevel(self._hud_root)
        self._win.title("Jarvis — Vision & Kamera")
        self._win.geometry("860x580+80+80")
        self._win.resizable(True, True)
        self._win.minsize(700, 480)
        self._win.configure(fg_color="#0f172a")
        self._win.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()

        self._win.update()
        self._win.state("normal")
        self._win.deiconify()
        self._win.lift()
        self._win.focus_force()

        # Preview starten (Hintergrund-Thread)
        self._start_preview()

    # ── UI bauen ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        win = self._win

        # ── Header ────────────────────────────────────────────────────────────
        header = ctk.CTkFrame(win, fg_color="#1e293b", corner_radius=0, height=52)
        header.pack(fill="x")
        header.pack_propagate(False)

        ctk.CTkLabel(
            header, text="📷  JARVIS VISION",
            font=ctk.CTkFont(family="Segoe UI", size=18, weight="bold"),
            text_color="#f1f5f9",
        ).place(x=16, rely=0.5, anchor="w")

        self._status_lbl = ctk.CTkLabel(
            header, text="● Starte Kamera...",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#f59e0b",
        )
        self._status_lbl.place(relx=1.0, x=-16, rely=0.5, anchor="e")

        # ── Hauptbereich: links Vorschau, rechts Steuerung ────────────────────
        main = ctk.CTkFrame(win, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=10, pady=10)
        main.columnconfigure(0, weight=5)
        main.columnconfigure(1, weight=3)
        main.rowconfigure(0, weight=1)

        # ── LINKE SEITE: Kamera-Vorschau ──────────────────────────────────────
        left = ctk.CTkFrame(main, fg_color="#1e293b", corner_radius=12)
        left.grid(row=0, column=0, padx=(0, 6), sticky="nsew")
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)

        # Vorschau-Label — wird mit CTkImage befuellt
        self._preview_label = ctk.CTkLabel(
            left,
            text="📷\n\nKamera wird gestartet...",
            font=ctk.CTkFont(size=14),
            text_color="#475569",
        )
        self._preview_label.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)

        # Buttons unter der Vorschau
        btn_bar = ctk.CTkFrame(left, fg_color="transparent")
        btn_bar.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 8))
        btn_bar.columnconfigure((0, 1, 2), weight=1, uniform="bb")

        ctk.CTkButton(
            btn_bar, text="📸  Snapshot",
            fg_color="#1e3a5f", hover_color="#2563eb",
            text_color="#93c5fd",
            font=ctk.CTkFont(size=12, weight="bold"), height=38, corner_radius=8,
            command=self._act_snapshot,
        ).grid(row=0, column=0, padx=(0, 3), sticky="ew")

        self._analyze_btn = ctk.CTkButton(
            btn_bar, text="🔍  KI analysieren",
            fg_color="#134e4a", hover_color="#0f766e",
            text_color="#5eead4",
            font=ctk.CTkFont(size=12, weight="bold"), height=38, corner_radius=8,
            command=self._act_analyze,
        )
        self._analyze_btn.grid(row=0, column=1, padx=3, sticky="ew")

        ctk.CTkButton(
            btn_bar, text="🗣  Jarvis fragen",
            fg_color="#1e293b", hover_color="#334155",
            text_color="#94a3b8",
            font=ctk.CTkFont(size=12, weight="bold"), height=38, corner_radius=8,
            command=self._act_ask_jarvis,
        ).grid(row=0, column=2, padx=(3, 0), sticky="ew")

        # ── RECHTE SEITE: Einstellungen + Ergebnis ────────────────────────────
        right = ctk.CTkScrollableFrame(
            main, fg_color="#0f172a", corner_radius=12,
            scrollbar_button_color="#334155",
            scrollbar_button_hover_color="#475569",
        )
        right.grid(row=0, column=1, sticky="nsew")

        def _sec(txt):
            ctk.CTkLabel(right, text=txt,
                font=ctk.CTkFont(size=9, weight="bold"),
                text_color="#334155", anchor="w",
            ).pack(fill="x", padx=6, pady=(12, 3))

        def _lbl(txt):
            ctk.CTkLabel(right, text=txt,
                font=ctk.CTkFont(size=11), text_color="#64748b", anchor="w",
            ).pack(fill="x", padx=6, pady=(0, 2))

        # ── Kamera-Einstellungen ──────────────────────────────────────────────
        _sec("KAMERA")

        _lbl("Kamera-Index")
        cam_row = ctk.CTkFrame(right, fg_color="transparent")
        cam_row.pack(fill="x", padx=6, pady=(0, 4))
        cam_row.columnconfigure(0, weight=1)

        self._cam_idx_var = ctk.StringVar(
            value=str(self._jarvis.settings.vision_camera_index)
        )
        ctk.CTkEntry(
            cam_row, textvariable=self._cam_idx_var,
            fg_color="#1e293b", border_color="#334155",
            text_color="#e2e8f0", height=32, width=60,
        ).grid(row=0, column=0, sticky="w", padx=(0, 6))

        ctk.CTkButton(
            cam_row, text="↺  Neu starten",
            fg_color="#1e293b", hover_color="#334155",
            text_color="#94a3b8",
            font=ctk.CTkFont(size=11), height=32, corner_radius=6,
            command=self._restart_camera,
        ).grid(row=0, column=1, sticky="w")

        self._cam_info_lbl = ctk.CTkLabel(right,
            text="Verfügbare Kameras: wird geprüft...",
            font=ctk.CTkFont(size=9), text_color="#475569", anchor="w",
        )
        self._cam_info_lbl.pack(fill="x", padx=6, pady=(0, 4))
        threading.Thread(target=self._check_cameras, daemon=True).start()

        self._flip_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            right, text="Horizontal spiegeln (Selfie-Modus)",
            variable=self._flip_var,
            font=ctk.CTkFont(size=12), text_color="#94a3b8",
            checkmark_color="#22c55e", fg_color="#0f766e",
            hover_color="#134e4a", border_color="#334155",
        ).pack(anchor="w", padx=6, pady=(0, 6))

        # ── Vision-KI ─────────────────────────────────────────────────────────
        _sec("VISION-KI  (Priorität: Groq → Gemini → Ollama)")

        _lbl("Gemini API Key (kostenlos: aistudio.google.com)")
        self._gemini_entry = ctk.CTkEntry(
            right,
            placeholder_text="AIza... (optional — Groq-Key reicht auch)",
            fg_color="#1e293b", border_color="#334155",
            text_color="#e2e8f0", height=32, show="*",
        )
        if self._jarvis.settings.gemini_api_key:
            self._gemini_entry.insert(0, self._jarvis.settings.gemini_api_key)
        self._gemini_entry.pack(fill="x", padx=6, pady=(0, 6))

        _lbl("Ollama Vision-Modell (lokaler Fallback)")
        _ollama_models = ["moondream", "llava", "llava:7b", "llava:13b", "bakllava"]
        cur_model = self._jarvis.settings.vision_ollama_model or "moondream"
        self._ollama_model_var = ctk.StringVar(value=cur_model)
        ctk.CTkOptionMenu(
            right, values=_ollama_models, variable=self._ollama_model_var,
            height=32, fg_color="#1e293b",
            button_color="#334155", button_hover_color="#475569",
            dropdown_fg_color="#0e1117", font=ctk.CTkFont(size=11),
        ).pack(fill="x", padx=6, pady=(0, 4))

        ctk.CTkButton(
            right, text="💾  Speichern",
            fg_color="#1e3a5f", hover_color="#2563eb",
            text_color="#93c5fd",
            font=ctk.CTkFont(size=12), height=34, corner_radius=8,
            command=self._save_settings,
        ).pack(fill="x", padx=6, pady=(0, 8))

        # ── Analyse-Frage ──────────────────────────────────────────────────────
        _sec("ANALYSE-FRAGE")
        self._question_entry = ctk.CTkEntry(
            right,
            placeholder_text="z.B. Was siehst du? Was liegt auf dem Tisch?",
            fg_color="#1e293b", border_color="#334155",
            text_color="#e2e8f0", height=32,
        )
        self._question_entry.pack(fill="x", padx=6, pady=(0, 8))

        # ── Ergebnis ──────────────────────────────────────────────────────────
        _sec("KI-ERGEBNIS")

        self._result_box = ctk.CTkTextbox(
            right, height=130,
            fg_color="#1e293b", text_color="#cbd5e1",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            corner_radius=8, wrap="word",
            state="disabled",
        )
        self._result_box.pack(fill="x", padx=6, pady=(0, 4))

        self._result_lbl = ctk.CTkLabel(right,
            text="Noch keine Analyse",
            font=ctk.CTkFont(size=9), text_color="#334155", anchor="w",
        )
        self._result_lbl.pack(fill="x", padx=6, pady=(0, 4))

    # ── Preview-Loop (Hintergrund-Thread) ─────────────────────────────────────

    def _start_preview(self):
        self._running = True
        self._preview_thread = threading.Thread(
            target=self._preview_loop, daemon=True, name="CameraPreview"
        )
        self._preview_thread.start()

    def _preview_loop(self):
        """Liest Frames von der Kamera und sendet sie via after() in den Hauptthread."""
        try:
            import cv2
        except ImportError:
            self._set_status("⚠ OpenCV fehlt — pip install opencv-python", "#ef4444")
            logger.error("OpenCV nicht installiert.")
            return

        cam_idx = self._jarvis.settings.vision_camera_index
        cap = self._open_camera(cv2, cam_idx)
        if cap is None:
            cap = self._open_camera(cv2, 0)
        if cap is None:
            self._set_status("✗ Keine Kamera gefunden", "#ef4444")
            return

        self._cap = cap
        self._set_status("● Live", "#22c55e")

        # Aufloesung setzen
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        while self._running:
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.05)
                continue

            # Horizontal spiegeln (Selfie-Modus)
            if self._flip_var and self._flip_var.get():
                frame = cv2.flip(frame, 1)

            # Base64-JPEG fuer KI-Analyse vorhalten
            try:
                ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if ok:
                    self._current_frame_b64 = base64.b64encode(buf.tobytes()).decode("utf-8")
            except Exception:
                pass

            # PIL-Bild fuer die Anzeige erstellen
            try:
                from PIL import Image
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                pil = Image.fromarray(rgb)

                # Auf Anzeigegroesse skalieren (max 520×390, Seitenverhaeltnis erhalten)
                pil.thumbnail((520, 390), Image.LANCZOS)
                img_w, img_h = pil.size

                ctk_img = ctk.CTkImage(
                    light_image=pil, dark_image=pil,
                    size=(img_w, img_h),
                )

                if self._win and self._win.winfo_exists():
                    self._win.after(0, self._update_preview, ctk_img)
            except Exception as e:
                logger.debug(f"Preview: {e}")

            time.sleep(1 / 30)   # ~30 FPS

        cap.release()
        self._cap = None

    @staticmethod
    def _open_camera(cv2, index: int):
        """Versucht die Kamera mit CAP_DSHOW (Windows) zu oeffnen."""
        try:
            cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
            if cap.isOpened():
                return cap
            cap.release()
        except Exception:
            pass
        try:
            cap = cv2.VideoCapture(index)
            if cap.isOpened():
                return cap
            cap.release()
        except Exception:
            pass
        return None

    def _update_preview(self, img):
        """Aktualisiert das Vorschau-Label (laeuft im Hauptthread via after())."""
        try:
            if self._preview_label and self._win and self._win.winfo_exists():
                self._preview_label.configure(image=img, text="")
        except Exception:
            pass

    def _set_status(self, text: str, color: str = "#94a3b8"):
        """Thread-sicher: Status-Text im Header setzen."""
        if self._win and self._win.winfo_exists():
            self._win.after(0, lambda t=text, c=color:
                self._status_lbl.configure(text=t, text_color=c)
                if self._status_lbl else None
            )

    # ── Kamera-Hilfsmethoden ──────────────────────────────────────────────────

    def _check_cameras(self):
        """Prueft welche Kamera-Indizes verfuegbar sind (Hintergrund-Thread)."""
        try:
            import cv2
            available = []
            for i in range(5):
                cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
                if cap.isOpened():
                    available.append(str(i))
                    cap.release()
            text = (
                f"Verfügbar: Kamera {', '.join(available)}"
                if available else
                "Keine Kamera gefunden"
            )
        except ImportError:
            text = "OpenCV nicht installiert"
        except Exception:
            text = "Kamera-Prüfung fehlgeschlagen"

        if self._win and self._win.winfo_exists():
            self._win.after(0, lambda t=text:
                self._cam_info_lbl.configure(text=t) if self._cam_info_lbl else None
            )

    def _restart_camera(self):
        """Stoppt die Preview und startet sie mit dem neuen Kamera-Index neu."""
        self._running = False
        if self._cap:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None

        try:
            new_idx = int(self._cam_idx_var.get())
        except (ValueError, TypeError):
            new_idx = 0

        self._jarvis.settings.vision_camera_index = new_idx
        self._jarvis.settings.save()
        self._jarvis.camera.set_camera_index(new_idx)

        self._set_status("● Starte Kamera...", "#f59e0b")

        def _delayed_start():
            time.sleep(0.6)
            if self._win and self._win.winfo_exists():
                self._start_preview()

        threading.Thread(target=_delayed_start, daemon=True).start()

    # ── Aktionen (Buttons) ────────────────────────────────────────────────────

    def _act_snapshot(self):
        """Sichert den aktuellen Frame fuer die Analyse."""
        if self._current_frame_b64:
            self._set_result_text("📸 Snapshot gespeichert — klicke 'KI analysieren'.")
            self._result_lbl.configure(text_color="#22c55e")
        else:
            self._set_result_text("⚠ Kein Bild verfügbar. Kamera prüfen.")
            self._result_lbl.configure(text_color="#ef4444")

    def _act_analyze(self):
        """Analysiert den aktuellen Frame mit der konfigurierten Vision-KI."""
        b64 = self._current_frame_b64
        if not b64:
            self._set_result_text("⚠ Kein Bild — Kamera prüfen.")
            return

        question = (self._question_entry.get().strip()
                    if self._question_entry else "")
        if not question:
            question = "Was siehst du auf diesem Bild? Beschreibe den Inhalt präzise auf Deutsch."

        self._analyze_btn.configure(state="disabled", text="⏳  Analysiert...")
        self._set_result_text("Analysiere Bild...")
        if self._result_lbl:
            self._result_lbl.configure(text="Bitte warten...", text_color="#f59e0b")

        def _run():
            try:
                result = self._jarvis.vision.analyze(b64, question)
            except Exception as e:
                result = f"Fehler: {e}"

            if self._win and self._win.winfo_exists():
                self._win.after(0, lambda r=result: self._on_analysis_done(r))

        threading.Thread(target=_run, daemon=True).start()

    def _on_analysis_done(self, text: str):
        """Callback wenn die KI fertig analysiert hat (Hauptthread)."""
        self._set_result_text(text)
        if self._result_lbl:
            self._result_lbl.configure(
                text="✓ Analyse abgeschlossen", text_color="#22c55e"
            )
        if self._analyze_btn:
            self._analyze_btn.configure(state="normal", text="🔍  KI analysieren")

    def _act_ask_jarvis(self):
        """
        Nimmt ein Foto auf und schickt es als Befehl an Jarvis
        damit Jarvis es analysiert und laut vorliest.
        """
        question = (self._question_entry.get().strip()
                    if self._question_entry else "")
        if not question:
            question = "Was siehst du?"

        cmd = f"schau mal durch die kamera und beantworte: {question}"
        self._jarvis.send_text_command(cmd)
        if self._result_lbl:
            self._result_lbl.configure(
                text="✓ Befehl an Jarvis gesendet", text_color="#22c55e"
            )

    # ── Einstellungen speichern ───────────────────────────────────────────────

    def _save_settings(self):
        """Speichert Kamera- und Vision-KI-Einstellungen."""
        try:
            new_idx = int(self._cam_idx_var.get())
        except (ValueError, TypeError):
            new_idx = 0

        gemini_key   = self._gemini_entry.get().strip() if self._gemini_entry else ""
        ollama_model = (self._ollama_model_var.get()
                        if self._ollama_model_var else "moondream")

        self._jarvis.settings.vision_camera_index  = new_idx
        self._jarvis.settings.gemini_api_key       = gemini_key
        self._jarvis.settings.vision_ollama_model  = ollama_model
        self._jarvis.settings.save()

        # Live-Update der Services
        self._jarvis.camera.set_camera_index(new_idx)
        self._jarvis.vision.update_config(
            gemini_api_key=gemini_key,
            ollama_model=ollama_model,
        )

        if self._result_lbl:
            self._result_lbl.configure(
                text="✓ Einstellungen gespeichert", text_color="#22c55e"
            )
        logger.info(f"Kamera-Einstellungen gespeichert: idx={new_idx}, model={ollama_model}")

    # ── Ergebnis-Box Hilfsmethode ─────────────────────────────────────────────

    def _set_result_text(self, text: str):
        """Schreibt Text in die Ergebnis-Box (Hauptthread)."""
        if not self._result_box:
            return
        self._result_box.configure(state="normal")
        self._result_box.delete("1.0", "end")
        self._result_box.insert("1.0", text)
        self._result_box.configure(state="disabled")

    # ── Fenster schliessen ────────────────────────────────────────────────────

    def _on_close(self):
        """Preview stoppen und Fenster zerstoeren."""
        self._running = False
        # Kurz warten damit der Preview-Thread sich beendet
        if self._cap:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None

        if self._win and self._win.winfo_exists():
            self._win.destroy()
        self._win = None
        logger.info("Kamera-Fenster geschlossen.")
