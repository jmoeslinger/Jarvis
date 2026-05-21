import json
import logging
import os
import threading
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List

_logger = logging.getLogger("jarvis.settings")

CONFIG_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "Jarvis"
CONFIG_FILE = CONFIG_DIR / "config.json"
LOG_DIR = CONFIG_DIR / "logs"


@dataclass
class Settings:
    grok_api_key: str = ""
    grok_model: str = "llama-3.3-70b-versatile"
    tts_voice: str = "de-DE-FlorianMultilingualNeural"
    tts_rate: str = "+0%"
    speech_language: str = "de-DE"
    wake_words: List[str] = field(default_factory=lambda: [
        "jarvis",
        "hallo jarvis",
        "hey jarvis",
        "hi jarvis",
        "ok jarvis",
        "okay jarvis",
    ])
    autostart_enabled: bool = True
    volume: float = 1.0
    api_provider: str = "groq"
    activation_hotkey: str = "ctrl+shift+j"
    input_device: int = -1
    routing_mode: str = "auto"
    ollama_url: str = "http://localhost:11434/v1"
    ollama_model: str = "llama3.2:3b"
    response_length: str = "normal"    # "short" | "normal" | "detailed"
    response_tone: str = "normal"      # "formal" | "normal" | "casual" | "technical" | "creative"
    multi_step_reasoning: bool = False  # Schritt-für-Schritt Denkmodus
    task_planning: bool = False         # Aufgaben in Schritte zerlegen
    parallel_tasks: bool = False        # Mehrere Tools gleichzeitig ausführen

    # ── Hörmodus ──────────────────────────────────────────────────────────────
    local_wake_word: bool = False       # Wake-Word lokal erkennen (openwakeword, offline)
    background_mode: bool = False       # Stumm starten, Panel nicht automatisch öffnen
    continuous_listening: bool = False  # Dauerhaftes Zuhören — kein Wake-Word nötig

    # ── Sprach-Analyse ────────────────────────────────────────────────────────
    speaker_recognition: bool = False   # Nur auf eingerichteten Benutzer reagieren
    emotion_detection: bool = False     # Stimmung aus Audio-Features erkennen
    smart_pause: bool = False           # Adaptive Pausen-Erkennung (keine Abschneid-Fehler)
    interrupt_handling: bool = False    # TTS bei Benutzer-Unterbrechung sofort stoppen
    whisper_mode: bool = True           # Reagiert auf Flüstersprache (leise Stimme)
    multi_command: bool = False         # Mehrere Befehle in einem Satz verarbeiten
    noise_filter: bool = False          # Spektrale Rauschunterdrückung vor STT
    long_term_context: bool = False     # Frühere Gespräche als KI-Kontext einbeziehen
    conversation_resume: bool = False   # Letzte Session beim Start wiederherstellen
    style_learning: bool = False        # Sprachstil lernen und Ton/Länge auto-anpassen
    adaptive_response_time: bool = False  # max_tokens je Befehlskomplexität anpassen
    adaptive_responses: bool = False    # Adaptives Verhalten (Stil + Reaktionszeit)

    # ── Persoenlichkeit ───────────────────────────────────────────────────────
    personality: str = "assistant"   # "assistant"|"friend"|"butler"|"coach"|"scientist"|"custom"
    personality_custom: str = ""     # Eigene Persoenlichkeits-Beschreibung (wenn custom)

    # ── Mood-basierte Antworten ────────────────────────────────────────────────
    mood_based_responses: bool = False  # Stimmung des Nutzers in Antwort-Stil einbeziehen

    # ── Proaktive Vorschlaege ─────────────────────────────────────────────────
    proactive_suggestions: bool = False  # Jarvis macht eigenstaendig Vorschlaege

    # ── Tagesplanung ──────────────────────────────────────────────────────────
    day_planning: bool = False          # Tagesplan-Feature aktivieren

    # ── App Integration ───────────────────────────────────────────────────────
    custom_apps: dict = field(default_factory=dict)  # {"App-Name": "C:\\path\\to\\app.exe"}

    # ── Vision ────────────────────────────────────────────────────────────────
    vision_enabled: bool = False        # Kamera-Feature aktivieren
    vision_camera_index: int = 0        # Kamera-Index (0 = Standard)
    gemini_api_key: str = ""            # Google Gemini API Key (optional, kostenlos)
    vision_ollama_model: str = "moondream"  # Lokales Vision-Modell

    # ── Gestensteuerung ───────────────────────────────────────────────────────
    gesture_enabled: bool = False       # Gestensteuerung aktivieren (MediaPipe)
    gesture_hold_seconds: float = 1.5  # Wie lange Geste gehalten werden muss (Sekunden)
    gesture_mappings: dict = field(default_factory=lambda: {
        "open_palm":  "",
        "fist":       "",
        "thumbs_up":  "",
        "peace":      "",
        "pointing":   "",
        "rock":       "",
    })

    # Wiederholende Tasks — gespeichert als Liste von Dicts:
    # [{id, label, interval_minutes, command}, ...]
    recurring_tasks: List[Dict] = field(default_factory=list)

    def __post_init__(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        # BUG-070: Lock als normales Attribut (kein Dataclass-Feld) — wird nicht in
        # asdict() aufgenommen. object.__setattr__ umgeht den Dataclass-Mechanismus.
        object.__setattr__(self, '_save_lock', threading.Lock())
        self._load()

    def _load(self):
        if CONFIG_FILE.exists():
            try:
                # utf-8-sig toleriert BOM (z.B. von Windows-Tools geschriebene Dateien)
                with open(CONFIG_FILE, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    return
                for key, value in data.items():
                    if not hasattr(self, key):
                        continue
                    # Typvalidierung für listenbasierte Felder — schützt gegen
                    # korrupte Configs (null, falsche Typen) die später TypeError auslösen.
                    current = getattr(self, key)
                    if isinstance(current, list) and not isinstance(value, list):
                        continue   # Listenfeld bleibt beim Default
                    if isinstance(current, dict) and not isinstance(value, dict):
                        continue   # Dictfeld bleibt beim Default
                    # BUG-077: For dict fields, merge loaded value INTO the current
                    # default rather than replacing it outright.  This ensures that
                    # keys added in newer versions (e.g. new gesture slots) are
                    # present even when the saved config pre-dates them.
                    if isinstance(current, dict) and isinstance(value, dict):
                        merged = dict(current)   # start from default (all current keys)
                        merged.update(value)     # overlay saved values
                        value = merged
                    if isinstance(current, bool) and not isinstance(value, bool):
                        # JSON int (0/1) als bool tolerieren, sonst überspringen
                        if isinstance(value, int):
                            value = bool(value)
                        else:
                            continue
                    # Numerische Felder: Typ validieren und ggf. konvertieren
                    # (bool ist Unterklasse von int → explizit ausschließen)
                    elif isinstance(current, float) and not isinstance(current, bool):
                        if isinstance(value, (int, float)) and not isinstance(value, bool):
                            value = float(value)
                        else:
                            continue
                    elif isinstance(current, int) and not isinstance(current, bool):
                        if isinstance(value, (int, float)) and not isinstance(value, bool):
                            value = int(value)
                        else:
                            continue
                    setattr(self, key, value)
            except (json.JSONDecodeError, OSError):
                pass

    def save(self):
        """Atomisches, thread-sicheres Speichern der Settings.
        BUG-070: Snapshot unter Lock, dann Temp-Datei + replace() ausserhalb —
        verhindert Datei-Korruption bei Absturz und gleichzeitige Schreibzugriffe.
        BUG-089: _save_lock muss auch die tmp-write+replace-Phase abdecken.
        Ohne das koennen zwei gleichzeitige save()-Aufrufe beide in dieselbe
        .tmp-Datei schreiben und sie korrumpieren (gleicher Fehler wie BUG-080
        in MemoryStore, der dort durch einen separaten _save_lock behoben wurde).
        """
        with self._save_lock:
            data = asdict(self)
            tmp = CONFIG_FILE.with_suffix(".tmp")
            try:
                tmp.write_text(
                    json.dumps(data, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                tmp.replace(CONFIG_FILE)
            except Exception as exc:
                _logger.warning(f"Settings speichern fehlgeschlagen: {exc}")
                try:
                    tmp.unlink(missing_ok=True)
                except Exception:
                    pass

    def reload(self):
        """BUG-NEW-3: reload() rief _load() ohne _save_lock auf.  Gleichzeitige
        save()-Aufrufe (die asdict() unter _save_lock lesen) konnten so auf
        halbwegs-aktualisierte Felder treffen, da _load() Felder einzeln mit
        setattr() schreibt ohne Lock.  Beide Methoden müssen denselben Lock
        halten damit save() und reload() sich nicht gegenseitig unterbrechen.
        """
        with self._save_lock:
            self._load()

    def is_configured(self) -> bool:
        return bool(self.grok_api_key)
