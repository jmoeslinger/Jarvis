"""
Jarvis Kamera-Fenster — Live-Vorschau, Vision-KI-Analyse und Kamera-Einstellungen.

Layout:
  Links:  Echtzeit-Kamera-Vorschau (640x480, 30 fps)
  Rechts: Einstellungen (Index, Provider, Keys) + Analyse-Ergebnis

Threads:
  _preview_loop()  liest Frames via OpenCV im Hintergrund
  after()          sendet jeden Frame sicher in den Tk-Hauptthread
"""
import base64
import logging
import threading
import time
from typing import List, Optional, Tuple

import customtkinter as ctk

logger = logging.getLogger("jarvis.camera_win")

# ──────────────────────────────────────────────────────────────────────────────
# Hilfsfunktionen (ausserhalb der Klasse — laufen in Hintergrund-Threads)
# ──────────────────────────────────────────────────────────────────────────────

def _cv2_available() -> bool:
    """Gibt True zurueck wenn OpenCV importierbar ist."""
    try:
        import cv2  # noqa: F401
        return True
    except ImportError:
        return False


def _list_cameras() -> List[Tuple[int, str]]:
    """
    Gibt Liste von (index, label) verfuegbarer Kameras zurueck.
    Testet Indizes 0–5 ohne CAP_DSHOW-Flag als Fallback.
    Laeuft in einem Hintergrund-Thread — nie im Hauptthread aufrufen!
    """
    import cv2
    result = []
    for i in range(6):
        cap = None
        try:
            # Erst mit DirectShow versuchen (schneller auf Windows)
            cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
            if not cap.isOpened():
                cap.release()
                cap = cv2.VideoCapture(i)
            if cap.isOpened():
                # Kurz einen Frame lesen damit wir wissen ob es echtes Video gibt
                ok, frame = cap.read()
                label = f"Kamera {i}"
                result.append((i, label))
        except Exception:
            pass
        finally:
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
    return result


def _find_best_camera(exclude_indices: List[int] = None) -> int:
    """
    Versucht die erste echte (nicht-virtuelle) Kamera zu finden.
    Gibt den Index zurueck, oder 0 wenn nichts besseres gefunden wurde.
    Unterscheidungsmerkmal: echte Webcams haben hoeheren Farbvarianzen als
    Vollbild-Einfarbkameras oder Logo-Streams (virtuelle Kameras).
    """
    try:
        import cv2
        import numpy as np
        exclude = set(exclude_indices or [])
        best_idx = 0
        best_variance = -1.0

        for i in range(6):
            if i in exclude:
                continue
            cap = None
            try:
                cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
                if not cap.isOpened():
                    cap.release()
                    cap = cv2.VideoCapture(i)
                if not cap.isOpened():
                    continue
                # Mehrere Frames skippen damit die Kamera sich stabilisiert
                for _ in range(5):
                    cap.read()
                ok, frame = cap.read()
                if not ok or frame is None:
                    continue
                # Farbvarianz als Echtheit-Indikator (virtuelle Kameras = niedrig)
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                variance = float(np.var(gray))
                logger.debug(f"Kamera {i}: Varianz={variance:.1f}")
                if variance > best_variance:
                    best_variance = variance
                    best_idx = i
            except Exception:
                pass
            finally:
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        pass

        return best_idx
    except Exception:
        return 0


# ──────────────────────────────────────────────────────────────────────────────

class CameraWindow:
    """
    Vollstaendiges Kamera-Fenster als CTkToplevel im HUD-Mainloop.
    Oeffnen via show(hud_root=...).
    """

    def __init__(self, jarvis):
        self._jarvis = jarvis
        self._win: Optional[ctk.CTkToplevel] = None
        self._hud_root = None

        # Preview-State
        self._preview_label: Optional[ctk.CTkLabel] = None
        self._status_lbl:    Optional[ctk.CTkLabel] = None
        self._running = False
        self._cap = None
        self._preview_thread: Optional[threading.Thread] = None
        self._current_frame_b64: Optional[str] = None
        self._active_cam_idx: int = jarvis.settings.vision_camera_index

        # UI-Widgets
        self._cam_idx_var:      Optional[ctk.StringVar]  = None
        self._flip_var:         Optional[ctk.BooleanVar] = None
        self._gemini_entry:     Optional[ctk.CTkEntry]   = None
        self._ollama_model_var: Optional[ctk.StringVar]  = None
        self._question_entry:   Optional[ctk.CTkEntry]   = None
        self._result_box:       Optional[ctk.CTkTextbox] = None
        self._result_lbl:       Optional[ctk.CTkLabel]   = None
        self._analyze_btn:      Optional[ctk.CTkButton]  = None
        self._cam_info_lbl:     Optional[ctk.CTkLabel]   = None
        self._cam_btns:         List[ctk.CTkButton]      = []

    # ── Oeffentlich ────────────────────────────────────────────────────────────

    def show(self, hud_root=None):
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
        self._on_close()

    # ── Fenster (Hauptthread) ──────────────────────────────────────────────────

    def _create_window(self):
        self._win = ctk.CTkToplevel(self._hud_root)
        self._win.title("Jarvis — Vision & Kamera")
        self._win.geometry("900x600+60+60")
        self._win.resizable(True, True)
        self._win.minsize(720, 500)
        self._win.configure(fg_color="#0f172a")
        self._win.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build_ui()
        self._win.update()
        self._win.state("normal")
        self._win.deiconify()
        self._win.lift()
        self._win.focus_force()
        # Erst Kameras scannen, dann Preview starten
        threading.Thread(target=self._init_cameras, daemon=True).start()

    # ── UI bauen ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        win = self._win

        # Header
        header = ctk.CTkFrame(win, fg_color="#1e293b", corner_radius=0, height=52)
        header.pack(fill="x")
        header.pack_propagate(False)

        ctk.CTkLabel(
            header, text="📷  JARVIS VISION",
            font=ctk.CTkFont(family="Segoe UI", size=18, weight="bold"),
            text_color="#f1f5f9",
        ).place(x=16, rely=0.5, anchor="w")

        self._status_lbl = ctk.CTkLabel(
            header, text="⏳ Suche Kamera...",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#f59e0b",
        )
        self._status_lbl.place(relx=1.0, x=-16, rely=0.5, anchor="e")

        # Hauptbereich
        main = ctk.CTkFrame(win, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=10, pady=10)
        main.columnconfigure(0, weight=5)
        main.columnconfigure(1, weight=3)
        main.rowconfigure(0, weight=1)

        # ── LINKS: Kamera-Vorschau ─────────────────────────────────────────────
        left = ctk.CTkFrame(main, fg_color="#1e293b", corner_radius=12)
        left.grid(row=0, column=0, padx=(0, 6), sticky="nsew")
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)

        self._preview_label = ctk.CTkLabel(
            left,
            text="📷\n\nSuche Kamera...",
            font=ctk.CTkFont(size=15),
            text_color="#475569",
        )
        self._preview_label.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)

        # Button-Leiste
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

        # ── RECHTS: Einstellungen + Ergebnis ──────────────────────────────────
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

        # ── Kamera-Auswahl ────────────────────────────────────────────────────
        _sec("KAMERA WÄHLEN")

        # Info-Label (wird nach Scan befuellt)
        self._cam_info_lbl = ctk.CTkLabel(right,
            text="Scanne verfügbare Kameras...",
            font=ctk.CTkFont(size=10), text_color="#475569", anchor="w",
            wraplength=220,
        )
        self._cam_info_lbl.pack(fill="x", padx=6, pady=(0, 4))

        # Container fuer dynamische Kamera-Buttons (wird nach Scan befuellt)
        self._cam_btn_frame = ctk.CTkFrame(right, fg_color="transparent")
        self._cam_btn_frame.pack(fill="x", padx=6, pady=(0, 4))

        # Manueller Index + Neustart
        manual_row = ctk.CTkFrame(right, fg_color="transparent")
        manual_row.pack(fill="x", padx=6, pady=(0, 4))

        ctk.CTkLabel(manual_row, text="Manueller Index:",
            font=ctk.CTkFont(size=10), text_color="#475569",
        ).pack(side="left")

        self._cam_idx_var = ctk.StringVar(value=str(self._active_cam_idx))
        ctk.CTkEntry(
            manual_row, textvariable=self._cam_idx_var,
            fg_color="#1e293b", border_color="#334155",
            text_color="#e2e8f0", height=28, width=50,
        ).pack(side="left", padx=(6, 4))

        ctk.CTkButton(
            manual_row, text="↺",
            fg_color="#1e293b", hover_color="#334155",
            text_color="#94a3b8",
            font=ctk.CTkFont(size=13), height=28, width=34, corner_radius=6,
            command=self._restart_camera,
        ).pack(side="left")

        # Selfie-Modus
        self._flip_var = ctk.BooleanVar(value=True)   # Standard: gespiegelt (natuerlicher)
        ctk.CTkCheckBox(
            right, text="Selfie-Modus (horizontal spiegeln)",
            variable=self._flip_var,
            font=ctk.CTkFont(size=12), text_color="#94a3b8",
            checkmark_color="#22c55e", fg_color="#0f766e",
            hover_color="#134e4a", border_color="#334155",
        ).pack(anchor="w", padx=6, pady=(0, 6))

        # ── Vision-KI ─────────────────────────────────────────────────────────
        _sec("VISION-KI  (Groq → Gemini → Ollama)")

        _lbl("Gemini API Key (optional — aistudio.google.com)")
        self._gemini_entry = ctk.CTkEntry(
            right,
            placeholder_text="AIza... (Groq-Key reicht für die meisten Fälle)",
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

    # ── Kamera-Initialisierung ─────────────────────────────────────────────────

    def _init_cameras(self):
        """
        Laeuft einmalig beim Start im Hintergrund:
        1. Prueft ob OpenCV verfuegbar ist
        2. Scannt alle Kameras
        3. Waehlt automatisch die beste (echte) Kamera
        4. Baut die Kamera-Auswahl-Buttons auf
        5. Startet den Preview-Loop
        """
        if not _cv2_available():
            self._set_status("⚠ OpenCV fehlt — pip install opencv-python", "#ef4444")
            self._update_cam_info("OpenCV nicht installiert.\nBitte: pip install opencv-python")
            return

        # Kameras scannen
        self._update_cam_info("Scanne Kameras...")
        cameras = _list_cameras()

        if not cameras:
            self._set_status("✗ Keine Kamera gefunden", "#ef4444")
            self._update_cam_info("Keine Kamera gefunden.\nUSB-Webcam anstecken?")
            return

        # Beste (echte) Kamera ermitteln
        configured = self._jarvis.settings.vision_camera_index
        if any(i == configured for i, _ in cameras):
            best = configured
        else:
            # Automatische Auswahl — Kamera mit hoechster Farbvarianz
            best = _find_best_camera()
            if not any(i == best for i, _ in cameras):
                best = cameras[0][0]

        self._active_cam_idx = best

        # Info-Text + Buttons im Hauptthread aufbauen
        info = f"Gefunden: {len(cameras)} Kamera(s)"
        if self._win and self._win.winfo_exists():
            self._win.after(0, lambda c=cameras, b=best, t=info:
                self._build_cam_buttons(c, b, t)
            )

        # Index-Eingabe synchronisieren
        if self._cam_idx_var and self._win and self._win.winfo_exists():
            self._win.after(0, lambda b=best:
                self._cam_idx_var.set(str(b))
            )

        # Preview starten
        self._start_preview(best)

    def _build_cam_buttons(self, cameras: List[Tuple[int, str]], active: int, info: str):
        """Baut Kamera-Auswahl-Buttons im Hauptthread auf."""
        if not (self._win and self._win.winfo_exists()):
            return
        if self._cam_info_lbl:
            self._cam_info_lbl.configure(text=info)

        # Alte Buttons entfernen
        for w in self._cam_btn_frame.winfo_children():
            w.destroy()
        self._cam_btns.clear()

        for i, (idx, label) in enumerate(cameras):
            is_active = (idx == active)
            btn = ctk.CTkButton(
                self._cam_btn_frame,
                text=f"📷 {label}",
                fg_color="#0369a1" if is_active else "#1e293b",
                hover_color="#0369a1",
                text_color="#7dd3fc" if is_active else "#94a3b8",
                font=ctk.CTkFont(size=11), height=30, corner_radius=6,
                command=lambda ix=idx: self._switch_camera(ix),
            )
            btn.pack(fill="x", pady=1)
            self._cam_btns.append(btn)

    def _switch_camera(self, idx: int):
        """Wechselt zu einer anderen Kamera per Button-Klick."""
        if idx == self._active_cam_idx and self._running:
            return  # Schon aktiv
        if self._cam_idx_var:
            self._cam_idx_var.set(str(idx))
        self._stop_preview()
        self._active_cam_idx = idx
        # Buttons aktualisieren
        for btn in self._cam_btns:
            label = btn.cget("text")
            is_active = (f"Kamera {idx}" in label)
            btn.configure(
                fg_color="#0369a1" if is_active else "#1e293b",
                text_color="#7dd3fc" if is_active else "#94a3b8",
            )
        threading.Thread(
            target=lambda: self._start_preview(idx), daemon=True
        ).start()

    # ── Preview-Loop ──────────────────────────────────────────────────────────

    def _start_preview(self, cam_idx: Optional[int] = None):
        if cam_idx is None:
            cam_idx = self._active_cam_idx
        self._running = True
        self._preview_thread = threading.Thread(
            target=self._preview_loop,
            args=(cam_idx,),
            daemon=True, name="CameraPreview",
        )
        self._preview_thread.start()

    def _stop_preview(self):
        self._running = False
        if self._cap:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
        time.sleep(0.3)

    def _preview_loop(self, cam_idx: int):
        """Liest Frames und sendet sie via after() in den Hauptthread."""
        try:
            import cv2
        except ImportError:
            self._set_status("⚠ OpenCV fehlt", "#ef4444")
            return

        # Kamera oeffnen
        cap = self._open_cap(cv2, cam_idx)
        if cap is None:
            self._set_status(f"✗ Kamera {cam_idx} nicht erreichbar", "#ef4444")
            return

        self._cap = cap
        self._active_cam_idx = cam_idx
        self._set_status(f"● Live  —  Kamera {cam_idx}", "#22c55e")

        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        while self._running:
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.05)
                continue

            # Selfie-Modus: horizontal spiegeln
            if self._flip_var and self._flip_var.get():
                frame = cv2.flip(frame, 1)

            # Base64 fuer KI-Analyse
            try:
                ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 82])
                if ok:
                    self._current_frame_b64 = base64.b64encode(buf.tobytes()).decode("utf-8")
            except Exception:
                pass

            # PIL → CTkImage → UI
            try:
                from PIL import Image
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                pil = Image.fromarray(rgb)
                pil.thumbnail((530, 400), Image.LANCZOS)
                ctk_img = ctk.CTkImage(
                    light_image=pil, dark_image=pil,
                    size=(pil.width, pil.height),
                )
                if self._win and self._win.winfo_exists():
                    self._win.after(0, self._update_preview, ctk_img)
            except Exception as e:
                logger.debug(f"Frame-Fehler: {e}")

            time.sleep(1 / 30)

        cap.release()
        self._cap = None

    @staticmethod
    def _open_cap(cv2, index: int):
        """Oeffnet VideoCapture mit CAP_DSHOW-Fallback."""
        for backend in (cv2.CAP_DSHOW, 0):
            try:
                cap = cv2.VideoCapture(index, backend) if backend else cv2.VideoCapture(index)
                if cap and cap.isOpened():
                    return cap
                if cap:
                    cap.release()
            except Exception:
                pass
        return None

    def _update_preview(self, img):
        try:
            if self._preview_label and self._win and self._win.winfo_exists():
                self._preview_label.configure(image=img, text="")
        except Exception:
            pass

    # ── Kamera neu starten ────────────────────────────────────────────────────

    def _restart_camera(self):
        """Stoppt Preview und startet mit dem eingegebenen Index neu."""
        try:
            new_idx = int(self._cam_idx_var.get())
        except (ValueError, TypeError):
            new_idx = 0
        self._stop_preview()
        threading.Thread(
            target=lambda: self._start_preview(new_idx), daemon=True
        ).start()

    # ── Status-Hilfsmethoden ──────────────────────────────────────────────────

    def _set_status(self, text: str, color: str = "#94a3b8"):
        if self._win and self._win.winfo_exists():
            self._win.after(0, lambda t=text, c=color:
                self._status_lbl.configure(text=t, text_color=c)
                if self._status_lbl else None
            )

    def _update_cam_info(self, text: str):
        if self._win and self._win.winfo_exists():
            self._win.after(0, lambda t=text:
                self._cam_info_lbl.configure(text=t)
                if self._cam_info_lbl else None
            )

    # ── Aktionen ──────────────────────────────────────────────────────────────

    def _act_snapshot(self):
        if self._current_frame_b64:
            self._set_result_text("📸 Snapshot bereit — klicke 'KI analysieren'.")
            if self._result_lbl:
                self._result_lbl.configure(text_color="#22c55e")
        else:
            self._set_result_text("⚠ Kein Bild verfügbar — Kamera prüfen.")
            if self._result_lbl:
                self._result_lbl.configure(text_color="#ef4444")

    def _act_analyze(self):
        b64 = self._current_frame_b64
        if not b64:
            self._set_result_text("⚠ Kein Bild — Kamera prüfen.")
            return
        question = self._question_entry.get().strip() if self._question_entry else ""
        if not question:
            question = "Was siehst du auf diesem Bild? Beschreibe den Inhalt präzise auf Deutsch."
        if self._analyze_btn:
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
        self._set_result_text(text)
        if self._result_lbl:
            self._result_lbl.configure(text="✓ Analyse abgeschlossen", text_color="#22c55e")
        if self._analyze_btn:
            self._analyze_btn.configure(state="normal", text="🔍  KI analysieren")

    def _act_ask_jarvis(self):
        question = self._question_entry.get().strip() if self._question_entry else ""
        if not question:
            question = "Was siehst du?"
        self._jarvis.send_text_command(f"schau mal durch die kamera und beantworte: {question}")
        if self._result_lbl:
            self._result_lbl.configure(text="✓ Befehl an Jarvis gesendet", text_color="#22c55e")

    # ── Einstellungen ─────────────────────────────────────────────────────────

    def _save_settings(self):
        try:
            new_idx = int(self._cam_idx_var.get())
        except (ValueError, TypeError):
            new_idx = self._active_cam_idx
        gemini_key   = self._gemini_entry.get().strip() if self._gemini_entry else ""
        ollama_model = self._ollama_model_var.get() if self._ollama_model_var else "moondream"

        self._jarvis.settings.vision_camera_index = new_idx
        self._jarvis.settings.gemini_api_key      = gemini_key
        self._jarvis.settings.vision_ollama_model = ollama_model
        self._jarvis.settings.save()
        self._jarvis.camera.set_camera_index(new_idx)
        self._jarvis.vision.update_config(
            gemini_api_key=gemini_key, ollama_model=ollama_model,
        )
        if self._result_lbl:
            self._result_lbl.configure(text="✓ Einstellungen gespeichert", text_color="#22c55e")

    # ── Ergebnis-Box ──────────────────────────────────────────────────────────

    def _set_result_text(self, text: str):
        if not self._result_box:
            return
        self._result_box.configure(state="normal")
        self._result_box.delete("1.0", "end")
        self._result_box.insert("1.0", text)
        self._result_box.configure(state="disabled")

    # ── Schliessen ────────────────────────────────────────────────────────────

    def _on_close(self):
        self._running = False
        if self._cap:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
        if self._win and self._win.winfo_exists():
            self._win.destroy()
        self._win = None
