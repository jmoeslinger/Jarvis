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

def _pil_to_png_bytes(pil_image) -> bytes:
    """Konvertiert ein PIL-Image in PNG-Bytes fuer tk.PhotoImage."""
    import io
    buf = io.BytesIO()
    pil_image.save(buf, format="PNG")
    return buf.getvalue()


def _cv2_available() -> bool:
    """Gibt True zurueck wenn OpenCV importierbar ist."""
    try:
        import cv2  # noqa: F401
        return True
    except ImportError:
        return False


def _get_camera_names_windows() -> dict:
    """
    Gibt ein Dict {index: name} zurueck, das Windows-Kameranamen via PowerShell ermittelt.
    Virtuelle Kameras (OBS, etc.) sind identifizierbar am Namen.
    Gibt leeres Dict zurueck wenn nicht aufrufbar.
    """
    import subprocess
    try:
        result = subprocess.run(
            ["powershell", "-NonInteractive", "-Command",
             "Get-PnpDevice | Where-Object {$_.Class -eq 'Camera'} | "
             "Select-Object FriendlyName, Status | ConvertTo-Json"],
            capture_output=True, text=True, timeout=8,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return {}
        import json
        raw = json.loads(result.stdout)
        if isinstance(raw, dict):
            raw = [raw]  # Einzelnes Objekt → Liste
        names = {}
        for i, entry in enumerate(raw):
            if isinstance(entry, dict):
                names[i] = entry.get("FriendlyName", f"Kamera {i}")
        return names
    except Exception:
        return {}


def _is_virtual_camera(name: str) -> bool:
    """
    Gibt True zurueck wenn der Kameraname auf eine virtuelle/Software-Kamera hindeutet.
    """
    lower = (name or "").lower()
    virtual_keywords = ["obs", "virtual", "vcam", "snap camera", "droidcam",
                        "iriun", "epoccam", "camo", "mmhmm", "xsplit",
                        "nvidia broadcast", "amd noise", "manycam"]
    return any(kw in lower for kw in virtual_keywords)


def _list_cameras() -> List[Tuple[int, str]]:
    """
    Gibt Liste von (index, label) verfuegbarer Kameras zurueck.
    Testet Indizes 0–5 und kennzeichnet virtuelle Kameras im Label.
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
                # BUG-035: ok-Ergebnis pruefen — nur aufnehmen wenn Frame lesbar
                ok, frame = cap.read()
                if ok and frame is not None:
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


def _find_best_camera(cameras: List[Tuple[int, str]], exclude_virtual: bool = True) -> int:
    """
    Waehlt die beste (echte) Kamera aus der Liste aus.
    Bevorzugt Kameras, deren Name NICHT auf eine virtuelle Kamera hinweist.
    Gibt den ersten verfuegbaren Index zurueck.
    """
    if not cameras:
        return 0

    # Zuerst nicht-virtuelle Kameras bevorzugen
    if exclude_virtual:
        real_cams = [(i, lbl) for i, lbl in cameras if not _is_virtual_camera(lbl)]
        if real_cams:
            return real_cams[0][0]

    # Fallback: erste verfuegbare Kamera
    return cameras[0][0]


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
        self._preview_canvas = None          # tk.Canvas fuer Kamera-Vorschau
        self._preview_label = None           # Alias auf _preview_canvas
        self._canvas_image_id: Optional[int] = None
        self._canvas_tk_img = None
        self._pending_frame = None           # BUG-027: letztes PIL-Frame fuer Deduplikation
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

        # Video-Aufnahme
        self._video_btn:           Optional[ctk.CTkButton] = None
        self._is_recording_video:  bool                    = False
        self._last_frame_raw = None         # letzter BGR-numpy-Frame (fuer Gesture)

        # Gestensteuerung
        self._gesture_enabled_var: Optional[ctk.BooleanVar] = None
        self._gesture_hold_var:    Optional[ctk.StringVar]   = None
        self._gesture_cmd_vars:    dict                      = {}   # {id: StringVar}
        self._gesture_overlay_id:  Optional[int]             = None  # Canvas-Text-ID
        self._gesture_bar_id:      Optional[int]             = None  # Canvas-Rechteck-ID

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

        # tk.Canvas fuer die Kamera-Vorschau — erlaubt korrekte Zentrierung
        import tkinter as tk
        self._preview_canvas = tk.Canvas(
            left,
            bg="#1e293b",
            highlightthickness=0,
        )
        self._preview_canvas.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        self._preview_canvas.bind("<Configure>", self._on_canvas_resize)
        # Kompatibilitaets-Alias damit bestehende Aufrufe weiter funktionieren
        self._preview_label = self._preview_canvas
        self._canvas_image_id: Optional[int] = None
        self._canvas_tk_img = None   # Referenz halten — GC-Schutz

        # Start-Text (wird durch erstes Frame ersetzt)
        self._preview_canvas.create_text(
            10, 10, anchor="nw",
            text="⏳ Suche Kamera...",
            fill="#475569", font=("Segoe UI", 13),
        )

        # Button-Leiste (Zeile 1)
        btn_bar = ctk.CTkFrame(left, fg_color="transparent")
        btn_bar.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 2))
        btn_bar.columnconfigure((0, 1, 2), weight=1, uniform="bb")

        ctk.CTkButton(
            btn_bar, text="📸  Snapshot",
            fg_color="#1e3a5f", hover_color="#2563eb",
            text_color="#93c5fd",
            font=ctk.CTkFont(size=12, weight="bold"), height=36, corner_radius=8,
            command=self._act_snapshot,
        ).grid(row=0, column=0, padx=(0, 3), sticky="ew")

        self._analyze_btn = ctk.CTkButton(
            btn_bar, text="🔍  KI analysieren",
            fg_color="#134e4a", hover_color="#0f766e",
            text_color="#5eead4",
            font=ctk.CTkFont(size=12, weight="bold"), height=36, corner_radius=8,
            command=self._act_analyze,
        )
        self._analyze_btn.grid(row=0, column=1, padx=3, sticky="ew")

        ctk.CTkButton(
            btn_bar, text="🗣  Jarvis fragen",
            fg_color="#1e293b", hover_color="#334155",
            text_color="#94a3b8",
            font=ctk.CTkFont(size=12, weight="bold"), height=36, corner_radius=8,
            command=self._act_ask_jarvis,
        ).grid(row=0, column=2, padx=(3, 0), sticky="ew")

        # Button-Leiste (Zeile 2)
        btn_bar2 = ctk.CTkFrame(left, fg_color="transparent")
        btn_bar2.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 8))
        btn_bar2.columnconfigure((0, 1), weight=1, uniform="bb2")

        self._video_btn = ctk.CTkButton(
            btn_bar2, text="🎬  Video aufnehmen",
            fg_color="#1a1a4a", hover_color="#2d2d7a",
            text_color="#a5b4fc",
            font=ctk.CTkFont(size=12, weight="bold"), height=34, corner_radius=8,
            command=self._act_record_video,
        )
        self._video_btn.grid(row=0, column=0, padx=(0, 3), sticky="ew")

        ctk.CTkButton(
            btn_bar2, text="🖐  Gesten kalibrieren",
            fg_color="#1e293b", hover_color="#334155",
            text_color="#94a3b8",
            font=ctk.CTkFont(size=12, weight="bold"), height=34, corner_radius=8,
            command=self._act_calibrate_gesture,
        ).grid(row=0, column=1, padx=(3, 0), sticky="ew")

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

        # ── Gestensteuerung ────────────────────────────────────────────────────
        _sec("GESTENSTEUERUNG  (MediaPipe)")

        # Aktivierung + Hold-Dauer
        gesture_top = ctk.CTkFrame(right, fg_color="transparent")
        gesture_top.pack(fill="x", padx=6, pady=(0, 4))

        from services.vision.gesture_recognizer import mp_available
        gesture_ok = mp_available()
        gesture_hint = "" if gesture_ok else "  ⚠ pip install mediapipe"

        self._gesture_enabled_var = ctk.BooleanVar(
            value=self._jarvis.settings.gesture_enabled
        )
        ctk.CTkCheckBox(
            gesture_top,
            text=f"Gestensteuerung aktiv{gesture_hint}",
            variable=self._gesture_enabled_var,
            font=ctk.CTkFont(size=11),
            text_color="#94a3b8" if gesture_ok else "#f59e0b",
            checkmark_color="#a5b4fc", fg_color="#2d2d7a",
            hover_color="#1a1a4a", border_color="#334155",
            command=self._on_gesture_toggle,
        ).pack(side="left")

        hold_row = ctk.CTkFrame(right, fg_color="transparent")
        hold_row.pack(fill="x", padx=6, pady=(0, 4))

        ctk.CTkLabel(hold_row, text="Hold-Dauer (s):",
            font=ctk.CTkFont(size=10), text_color="#475569",
        ).pack(side="left")

        self._gesture_hold_var = ctk.StringVar(
            value=str(self._jarvis.settings.gesture_hold_seconds)
        )
        ctk.CTkEntry(
            hold_row,
            textvariable=self._gesture_hold_var,
            fg_color="#1e293b", border_color="#334155",
            text_color="#e2e8f0", height=26, width=52,
        ).pack(side="left", padx=(6, 0))

        # Geste → Befehl Mapping
        _lbl("Geste → Jarvis-Befehl  (leer = deaktiviert)")

        from services.vision.gesture_recognizer import GESTURE_LABELS
        mappings = self._jarvis.settings.gesture_mappings

        for gesture_id, gesture_label in GESTURE_LABELS.items():
            row = ctk.CTkFrame(right, fg_color="transparent")
            row.pack(fill="x", padx=6, pady=1)

            ctk.CTkLabel(row, text=gesture_label,
                font=ctk.CTkFont(size=11), text_color="#64748b",
                width=130, anchor="w",
            ).pack(side="left")

            var = ctk.StringVar(value=mappings.get(gesture_id, ""))
            self._gesture_cmd_vars[gesture_id] = var

            ctk.CTkEntry(
                row,
                textvariable=var,
                placeholder_text="z.B.: Öffne Spotify",
                fg_color="#1e293b", border_color="#334155",
                text_color="#e2e8f0", height=28,
            ).pack(side="left", fill="x", expand=True)

        ctk.CTkButton(
            right, text="💾  Gesten speichern",
            fg_color="#2d2d7a", hover_color="#3b3baa",
            text_color="#a5b4fc",
            font=ctk.CTkFont(size=12), height=34, corner_radius=8,
            command=self._save_gesture_settings,
        ).pack(fill="x", padx=6, pady=(4, 8))

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
        2. Holt Kameranamen aus Windows (PnP)
        3. Scannt alle per OpenCV erreichbaren Kameras
        4. Waehlt automatisch die beste (echte, nicht-virtuelle) Kamera
        5. Baut die Kamera-Auswahl-Buttons auf
        6. Startet den Preview-Loop
        """
        if not _cv2_available():
            self._set_status("⚠ OpenCV fehlt — pip install opencv-python", "#ef4444")
            self._update_cam_info("OpenCV nicht installiert.\nBitte: pip install opencv-python")
            return

        # Windows-Kameranamen abfragen (Hintergrund-Thread OK)
        self._update_cam_info("Ermittle Kameranamen...")
        win_names = _get_camera_names_windows()  # {index: "Integrated Camera"}

        # Kameras scannen (per OpenCV)
        self._update_cam_info("Scanne Kameras...")
        cameras_raw = _list_cameras()  # [(index, "Kamera N")]

        if not cameras_raw:
            # Vielleicht ist nur die Windows-Kamera disabled — hinweisen
            if win_names:
                disabled_names = ", ".join(win_names.values())
                self._set_status("⚠ Kamera deaktiviert", "#f59e0b")
                self._update_cam_info(
                    f"Gefunden (deaktiviert): {disabled_names}\n\n"
                    "Bitte aktivieren:\n"
                    "Geräte-Manager → Kameras →\n"
                    "Rechtsklick → Gerät aktivieren"
                )
            else:
                self._set_status("✗ Keine Kamera gefunden", "#ef4444")
                self._update_cam_info("Keine Kamera gefunden.\nUSB-Webcam anstecken?")
            return

        # Labels mit Windows-Namen anreichern
        cameras: List[Tuple[int, str]] = []
        for idx, raw_label in cameras_raw:
            # Win-Namen sind nach Erkennungsreihenfolge nummeriert — versuchen zu matchen
            win_label = win_names.get(idx, "")
            if win_label:
                label = f"{win_label}"
                if _is_virtual_camera(win_label):
                    label += " (virtuell)"
            else:
                # Kein Windows-Name: OpenCV hat direkten Zugriff → wahrscheinlich virtuelle Cam
                label = f"Kamera {idx}"
                # Versuche OBS Virtual Camera per typischen Merkmalen zu erkennen
                # BUG-005: try/finally stellt sicher dass cap immer released wird
                cap = None
                try:
                    import cv2
                    cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
                    if cap.isOpened():
                        for _ in range(3): cap.read()
                        ok, frame = cap.read()
                        if ok and frame is not None:
                            h, w = frame.shape[:2]
                            if w == 1920 and h == 1080:  # Typisch fuer OBS Virtual Camera
                                label = f"Kamera {idx} (OBS/Virtuell)"
                except Exception:
                    pass
                finally:
                    if cap is not None:
                        try: cap.release()
                        except Exception: pass
            cameras.append((idx, label))

        # Beste (echte) Kamera ermitteln
        configured = self._jarvis.settings.vision_camera_index
        if any(i == configured for i, _ in cameras):
            best = configured
        else:
            best = _find_best_camera(cameras, exclude_virtual=True)

        self._active_cam_idx = best

        # Hinweis wenn nur virtuelle Kameras gefunden
        real_count = sum(1 for _, lbl in cameras if "virtuell" not in lbl.lower() and "obs" not in lbl.lower())
        if real_count == 0:
            info = f"Nur virtuelle Kamera(s) gefunden"
            if win_names:
                disabled = [n for n in win_names.values() if not _is_virtual_camera(n)]
                if disabled:
                    info += f"\n⚠ Deaktiviert: {', '.join(disabled)}\n→ Geräte-Manager → aktivieren"
        else:
            info = f"Gefunden: {len(cameras)} Kamera(s)"

        # Info-Text + Buttons im Hauptthread aufbauen
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
            is_virtual = "virtuell" in label.lower() or "obs" in label.lower()
            icon = "🖥" if is_virtual else "📷"

            if is_active:
                fg = "#0369a1"
                txt = "#7dd3fc"
            elif is_virtual:
                fg = "#1e293b"
                txt = "#f59e0b"  # Orange fuer virtuelle Kameras
            else:
                fg = "#1e293b"
                txt = "#94a3b8"

            btn = ctk.CTkButton(
                self._cam_btn_frame,
                text=f"{icon} {label}",
                fg_color=fg,
                hover_color="#0369a1",
                text_color=txt,
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

        # Gestensteuerung anbinden wenn aktiviert
        if (
            self._jarvis.settings.gesture_enabled
            and self._jarvis.gesture.available
            and not self._jarvis.gesture._running
        ):
            self._jarvis.gesture.start(lambda: self._last_frame_raw)

    def _stop_preview(self):
        """
        Stoppt den Preview-Thread sauber.
        BUG-003: kein time.sleep() auf dem Main-Thread mehr.
        BUG-014: Thread wird mit Timeout ge-joined damit kein doppelter
                 Zugriff auf self._cap / self._canvas_tk_img entsteht.
        Darf von beliebigem Thread aus aufgerufen werden.
        Stoppt auch den GestureRecognizer.
        """
        self._running = False
        # Gestensteuerung stoppen wenn der Preview aufhoert
        try:
            self._jarvis.gesture.stop()
        except Exception:
            pass
        # Kamera sofort freigeben damit der Thread nicht in read() haengt
        cap = self._cap
        if cap is not None:
            self._cap = None
            try:
                cap.release()
            except Exception:
                pass
        # Thread joinen (max 1 s) — vermeidet Race auf self._cap beim Neustart
        t = self._preview_thread
        if t is not None and t.is_alive():
            t.join(timeout=1.0)

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

            # Rohen Frame fuer Gestensteuerung bereitstellen (BGR, numpy)
            self._last_frame_raw = frame

            # PIL-Bild erzeugen und via after() an den Canvas senden
            # BUG-013: kein _light_image-Hack mehr — PIL direkt uebergeben
            # BUG-027: nur EIN pending after()-Call — neuer Frame ersetzt alten
            try:
                from PIL import Image
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                pil = Image.fromarray(rgb)
                pil.thumbnail((530, 400), Image.LANCZOS)
                if self._win and self._win.winfo_exists():
                    # Atomisch: altes pending-Flag setzen, neues Bild einreihen
                    self._pending_frame = pil   # BUG-027: letztes Bild merken
                    self._win.after(0, self._update_preview_pil, pil)
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

    def _on_canvas_resize(self, event=None):
        """Zentriert das letzte Frame neu wenn das Fenster vergroessert/verkleinert wird."""
        if self._canvas_tk_img is not None:
            self._draw_canvas_image(self._canvas_tk_img)

    def _draw_canvas_image(self, tk_img):
        """Zeichnet tk_img zentriert auf dem Canvas."""
        try:
            if not (self._preview_canvas and self._win and self._win.winfo_exists()):
                return
            cw = self._preview_canvas.winfo_width()
            ch = self._preview_canvas.winfo_height()
            if cw < 2 or ch < 2:
                return
            self._preview_canvas.delete("all")
            self._canvas_image_id = self._preview_canvas.create_image(
                cw // 2, ch // 2, anchor="center", image=tk_img
            )
        except Exception:
            pass

    def _update_preview_pil(self, pil_img):
        """
        Hauptthread-Callback: PIL-Bild → tk.PhotoImage → Canvas.
        BUG-013: kein _light_image-Hack mehr.
        BUG-027: ignoriert Frame wenn inzwischen ein neuerer pending ist.
        Zeichnet zusaetzlich ein Gesten-Overlay wenn Gestensteuerung aktiv.
        """
        try:
            if not (self._preview_canvas and self._win and self._win.winfo_exists()):
                return
            # BUG-027: Frame verwerfen wenn bereits ein neueres Bild wartet
            if pil_img is not self._pending_frame:
                return
            import tkinter as tk
            tk_img = tk.PhotoImage(data=_pil_to_png_bytes(pil_img))
            self._canvas_tk_img = tk_img   # GC-Schutz
            self._pending_frame = None
            self._draw_canvas_image(tk_img)
            # Gesten-Overlay aktualisieren
            self._update_gesture_overlay()
        except Exception as e:
            logger.debug(f"Preview-Update-Fehler: {e}")

    def _update_gesture_overlay(self):
        """Zeichnet Geste + Hold-Fortschritt als Overlay auf den Canvas."""
        try:
            if not (self._preview_canvas and self._win and self._win.winfo_exists()):
                return
            gesture = self._jarvis.gesture.current_gesture
            progress = self._jarvis.gesture.hold_progress

            # Alte Overlays entfernen
            self._preview_canvas.delete("gesture_overlay")

            if not (self._jarvis.settings.gesture_enabled and gesture):
                return

            from services.vision.gesture_recognizer import GESTURE_LABELS
            label = GESTURE_LABELS.get(gesture, gesture)

            cw = self._preview_canvas.winfo_width()
            ch = self._preview_canvas.winfo_height()

            # Hintergrund-Rechteck oben links
            bar_w = min(230, cw - 10)
            self._preview_canvas.create_rectangle(
                8, 8, 8 + bar_w, 56,
                fill="#0d0d1e", outline="#2d2d7a", width=1,
                tags="gesture_overlay",
            )
            # Geste-Name
            self._preview_canvas.create_text(
                16, 20, anchor="nw",
                text=label,
                fill="#a5b4fc",
                font=("Segoe UI", 13, "bold"),
                tags="gesture_overlay",
            )
            # Fortschrittsbalken
            bar_x1, bar_y1, bar_x2, bar_y2 = 16, 36, 8 + bar_w - 8, 48
            self._preview_canvas.create_rectangle(
                bar_x1, bar_y1, bar_x2, bar_y2,
                fill="#1a1a4a", outline="#334155",
                tags="gesture_overlay",
            )
            fill_w = int((bar_x2 - bar_x1) * max(0.0, min(1.0, progress)))
            if fill_w > 0:
                self._preview_canvas.create_rectangle(
                    bar_x1, bar_y1, bar_x1 + fill_w, bar_y2,
                    fill="#818cf8", outline="",
                    tags="gesture_overlay",
                )
        except Exception as e:
            logger.debug(f"Gesten-Overlay Fehler: {e}")

    def _update_preview(self, img):
        """Legacy-Compat fuer alten CTkImage-Aufruf (nicht mehr verwendet intern)."""
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

    def _act_record_video(self):
        """Startet oder zeigt Video-Aufnahme-Fortschritt an."""
        if self._is_recording_video:
            return  # bereits laeuft

        question = self._question_entry.get().strip() if self._question_entry else ""
        if not question:
            question = "Was passiert in diesem Video? Beschreibe die Szene auf Deutsch."

        self._is_recording_video = True
        if self._video_btn:
            self._video_btn.configure(
                state="disabled", text="⏳  Aufnehme 3s...",
                fg_color="#3b0764", text_color="#e879f9",
            )
        self._set_result_text("🎬 Video-Aufnahme läuft (3 Sekunden)...")
        if self._result_lbl:
            self._result_lbl.configure(text="Bitte warten...", text_color="#f59e0b")

        def _run():
            try:
                frames = self._jarvis.camera.capture_video_frames(
                    duration_seconds=3.0, max_frames=9,
                )
                if not frames:
                    result = "⚠ Video-Aufnahme fehlgeschlagen. Kamera prüfen."
                else:
                    result = self._jarvis.vision.analyze_video(frames, question)
            except Exception as e:
                result = f"Fehler: {e}"
            if self._win and self._win.winfo_exists():
                self._win.after(0, lambda r=result: self._on_video_done(r))

        threading.Thread(target=_run, daemon=True).start()

    def _on_video_done(self, text: str):
        """Callback wenn Video-Aufnahme + Analyse fertig."""
        self._is_recording_video = False
        self._set_result_text(text)
        if self._result_lbl:
            self._result_lbl.configure(text="✓ Video-Analyse abgeschlossen", text_color="#22c55e")
        if self._video_btn:
            self._video_btn.configure(
                state="normal", text="🎬  Video aufnehmen",
                fg_color="#1a1a4a", text_color="#a5b4fc",
            )

    def _act_calibrate_gesture(self):
        """Zeigt kurzen Kalibrierungs-Hinweis in der Ergebnis-Box."""
        from services.vision.gesture_recognizer import GESTURE_LABELS, mp_available
        if not mp_available():
            self._set_result_text(
                "⚠ MediaPipe ist nicht installiert.\n\n"
                "Bitte installieren:\n  pip install mediapipe\n\n"
                "Danach Jarvis neu starten."
            )
            if self._result_lbl:
                self._result_lbl.configure(text="MediaPipe fehlt", text_color="#ef4444")
            return

        lines = [
            "🖐 Gestenerkennung — erkannte Gesten:\n",
            "Zeige der Kamera eine Geste für 1–2 Sekunden.\n",
        ]
        for gid, glabel in GESTURE_LABELS.items():
            cmd = self._jarvis.settings.gesture_mappings.get(gid, "")
            if cmd:
                lines.append(f"  {glabel}  →  '{cmd}'")
            else:
                lines.append(f"  {glabel}  →  (kein Befehl)")
        lines.append("\nGestensteuerung unten aktivieren und Befehle zuweisen.")
        self._set_result_text("\n".join(lines))
        if self._result_lbl:
            self._result_lbl.configure(text="Gesten-Übersicht", text_color="#a5b4fc")

    def _on_gesture_toggle(self):
        """Wird aufgerufen wenn die Gesture-Checkbox umgeschaltet wird."""
        enabled = self._gesture_enabled_var.get() if self._gesture_enabled_var else False
        frame_provider = (lambda: self._last_frame_raw) if self._running else None
        self._jarvis.set_gesture_enabled(enabled, frame_provider=frame_provider)
        # Checkbox aktualisieren (falls set_gesture_enabled die Einstellung zuruecksetzt)
        if self._gesture_enabled_var:
            self._gesture_enabled_var.set(self._jarvis.settings.gesture_enabled)

    def _save_gesture_settings(self):
        """Speichert Gesten-Mappings + Hold-Dauer."""
        mappings = {
            gid: var.get().strip()
            for gid, var in self._gesture_cmd_vars.items()
        }
        try:
            hold = float(self._gesture_hold_var.get()) if self._gesture_hold_var else 1.5
        except (ValueError, TypeError):
            hold = 1.5

        self._jarvis.save_gesture_mappings(mappings, hold_seconds=hold)
        if self._result_lbl:
            self._result_lbl.configure(
                text="✓ Gesten gespeichert", text_color="#a5b4fc"
            )

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
        # Gestensteuerung sauber beenden
        try:
            self._jarvis.gesture.stop()
        except Exception:
            pass
        if self._cap:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
        if self._win and self._win.winfo_exists():
            self._win.destroy()
        self._win = None
