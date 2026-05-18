import datetime
import difflib
import logging
import re
import threading
import time
import webbrowser
from enum import Enum, auto
from pathlib import Path
from typing import Callable, Dict, List, Optional

import keyboard

from config.settings import Settings
from core.task_manager import TaskManager
from services.ai.grok_client import GrokClient
from services.ai.ollama_client import OllamaClient
from services.ai.router_client import RouterClient
from services.speech.recognizer import SpeechRecognizer
from services.speech.synthesizer import SpeechSynthesizer
from services.system.app_launcher import AppLauncher
from services.system.hotkey import HotkeyService
from services.web.search import WebSearchService

logger = logging.getLogger("jarvis.core")

# Phrasen die Unsicherheit signalisieren → Konfidenz sinkt
_HEDGE_WORDS = [
    "vielleicht", "wahrscheinlich", "ich denke", "ich glaube", "glaube ich",
    "könnte", "möglicherweise", "bin mir nicht sicher", "nicht sicher",
    "nicht genau", "ungefähr", "etwa", "schätzungsweise", "ich vermute",
    "vermutlich", "anscheinend", "scheint", "evtl", "es könnte",
    "ich bin nicht", "leider nicht sicher",
]

_LENGTH_HINTS = {
    "short":    "Antworte in maximal 2 kurzen Sätzen. Sehr prägnant und direkt.",
    "normal":   "",
    "detailed": "Antworte ausführlich. Erkläre Zusammenhänge, nenne Beispiele und Details.",
}

# Reine Funktionsnamen die on_chunk herausfiltern soll
_KNOWN_TOOL_NAMES = {
    "get_time", "search_internet", "launch_app", "close_app",
    "open_website", "set_timer", "youtube_play", "google_first_result",
    "remember_info", "forget_info", "update_info", "search_memory",
    "run_terminal", "run_script", "read_file", "write_file",
    "read_clipboard", "analyze_screenshot",
    "analyze_camera", "add_day_task", "get_day_plan", "complete_day_task",
}

_TONE_HINTS = {
    "formal":    (
        "Antworte formell und höflich. Benutze Sie-Anrede. "
        "Professionelle, sachliche Sprache ohne Umgangssprache oder Slang."
    ),
    "normal":    "",
    "casual":    (
        "Antworte locker und freundlich wie ein guter Kumpel. Du-Anrede. "
        "Umgangssprache ist OK. Gerne etwas Humor und persönliche Wärme."
    ),
    "technical": (
        "Antworte technisch präzise und detailliert. Nutze Fachbegriffe korrekt, "
        "erkläre Konzepte exakt ohne zu vereinfachen. Zahlen und Fakten bevorzugen."
    ),
    "creative":  (
        "Antworte kreativ und bildreich. Nutze Metaphern, lebendige Beschreibungen "
        "und ungewöhnliche Perspektiven. Bringe Persönlichkeit in jede Antwort."
    ),
}


class State(Enum):
    IDLE            = auto()
    WAKE_LISTENING  = auto()
    CMD_LISTENING   = auto()
    PROCESSING      = auto()
    SPEAKING        = auto()
    ERROR           = auto()


class JarvisCore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.state = State.IDLE

        self._state_listeners: List[Callable[[State], None]] = []
        self._message_listeners: List[Callable[[str, str], None]] = []

        self._stop_bg_listen: Optional[Callable] = None
        self._running = False
        self._activation_lock = threading.Lock()
        self._last_command: Optional[str] = None
        self._cmd_stop_event = threading.Event()
        self._active_timers: List[threading.Timer] = []

        # ── Hörmodus ──────────────────────────────────────────────────────────
        self._local_ww_detector = None          # LocalWakeWordDetector (lazy init)
        self._settings_listeners: List[Callable[[], None]] = []

        # ── Streaming-Abbruch ─────────────────────────────────────────────────
        # Wird in on_chunk geprüft — cancel_current_task() setzt dieses Event
        self._cancel_streaming = threading.Event()

        # ── Task Manager ──────────────────────────────────────────────────────
        self.task_manager = TaskManager(on_update=self._on_task_update)
        self._task_listeners: List[Callable[[List[Dict]], None]] = []

        logger.info("Initialisiere Dienste...")
        from services.memory.memory_store import MemoryStore
        from services.memory.conversation_log import ConversationLog
        from services.memory.style_learner import StyleLearner
        self.memory           = MemoryStore()
        self.conversation_log = ConversationLog()
        self.style_learner    = StyleLearner()

        whisper_base = (
            "https://api.groq.com/openai/v1"
            if settings.api_provider == "groq"
            else ""
        )
        self.hotkey = HotkeyService()
        self.recognizer = SpeechRecognizer(
            settings.speech_language,
            api_key=settings.grok_api_key,
            api_base=whisper_base,
            device=settings.input_device,
        )
        self.synthesizer = SpeechSynthesizer(settings.tts_voice, settings.tts_rate)

        # AI-Provider aufbauen
        _grok_client = GrokClient(
            settings.grok_api_key,
            settings.grok_model,
            settings.api_provider,
            get_memories=self.memory.format_for_prompt,
        )
        _ollama_client = OllamaClient(
            base_url=settings.ollama_url,
            model=settings.ollama_model,
            get_memories=self.memory.format_for_prompt,
        )
        provider_name = "Groq" if settings.api_provider == "groq" else "Grok"
        self.grok = RouterClient(
            providers=[
                (f"{provider_name} ({settings.grok_model})", _grok_client),
                ("Ollama (Lokal)",                            _ollama_client),
            ],
            mode=settings.routing_mode,
            on_switch=self._on_provider_switch,
        )

        self.search      = WebSearchService()
        self.launcher    = AppLauncher()

        # ── Neue Features ─────────────────────────────────────────────────────
        from services.ai.day_planner import DayPlanner
        from services.vision.camera import CameraService
        from services.vision.vision_client import VisionAnalyzer
        self.day_planner = DayPlanner()
        self.camera      = CameraService(camera_index=settings.vision_camera_index)
        self.vision      = VisionAnalyzer(
            groq_api_key=settings.grok_api_key,
            gemini_api_key=settings.gemini_api_key,
            ollama_model=settings.vision_ollama_model,
        )

        # Gespeicherte Modus-Einstellungen wiederherstellen
        if settings.response_length != "normal":
            self.grok.set_length_hint(_LENGTH_HINTS.get(settings.response_length, ""))
        if settings.response_tone != "normal":
            self.grok.set_tone(_TONE_HINTS.get(settings.response_tone, ""))
        if settings.multi_step_reasoning:
            self.grok.set_multi_step(True)
        if settings.task_planning:
            self.grok.set_task_planning(True)
        if settings.parallel_tasks:
            self.grok.set_parallel_tasks(True)

        # Persoenlichkeit anwenden
        if settings.personality and settings.personality != "assistant":
            self.grok.set_personality(settings.personality, settings.personality_custom)

        self._register_tools()
        self.hotkey.register(settings.activation_hotkey, self._on_hotkey)

        # Proaktiver Engine (Hintergrund-Daemon)
        from services.ai.proactive_engine import ProactiveEngine
        self._proactive_engine = ProactiveEngine(
            on_suggestion=lambda text: self.send_text_command(text),
            is_idle=lambda: self.state.name == "WAKE_LISTENING",
            get_open_tasks=self.day_planner.get_open_count,
        )
        if settings.proactive_suggestions:
            self._proactive_engine.start()

        # Gespeicherte wiederholende Tasks wiederherstellen
        self._restore_recurring_tasks()

        # Lokalen Wake-Word Detektor initialisieren (falls aktiviert)
        if settings.local_wake_word:
            self._init_local_ww_detector(silent=True)

        # ── Sprach-Analyse ────────────────────────────────────────────────────
        self._speaker_id = None     # SpeakerIdentifier (lazy init)
        self._interrupt_active = threading.Event()   # verhindert parallele Monitore

        # Smart-Pause in Recognizer aktivieren
        if settings.smart_pause:
            self.recognizer.smart_pause = True

        # Geräuschfilter
        if settings.noise_filter:
            self.recognizer.noise_filter = True

        # SpeakerIdentifier initialisieren falls aktiviert
        if settings.speaker_recognition:
            self._init_speaker_id(silent=True)

        # Langzeit-Kontext: ConversationLog als Context-Provider registrieren
        if settings.long_term_context:
            self.grok.set_context_provider(self.conversation_log.get_context_string)

        # Adaptive Antworten: Stil-Präferenzen aus StyleLearner anwenden
        if settings.adaptive_responses and self.style_learner.get_total_commands() >= 15:
            self._apply_style_preferences()

        # Escape-Taste zum Stoppen
        try:
            keyboard.add_hotkey("escape", self._on_stop)
        except Exception as e:
            logger.warning(f"Escape-Hotkey konnte nicht registriert werden: {e}")

        logger.info("Alle Dienste bereit.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def on_state_change(self, cb: Callable[[State], None]):
        self._state_listeners.append(cb)

    def on_message(self, cb: Callable[[str, str], None]):
        self._message_listeners.append(cb)

    def on_settings_change(self, cb: Callable[[], None]):
        """Registriert einen Listener der bei Settings-Änderungen benachrichtigt wird."""
        self._settings_listeners.append(cb)

    def _fire_settings_changed(self):
        """Informiert alle Settings-Listener (z. B. Control Panel)."""
        for cb in self._settings_listeners:
            try:
                cb()
            except Exception as exc:
                logger.debug(f"Settings-Listener Fehler: {exc}")

    def start(self):
        self._running = True
        self._set_state(State.WAKE_LISTENING)
        self._start_bg_listen()
        logger.info("Jarvis hoert auf Wake-Word.")

        # Gespräch fortsetzen: letzte Session in Chat-History laden
        if self.settings.conversation_resume:
            history = self.conversation_log.get_last_session_history()
            if history:
                # RouterClient-History und alle Provider-Clients synchron setzen
                self.grok._history = list(history)
                for _, client in self.grok._providers:
                    client._history = list(history)
                turns = len(history) // 2
                self._notify("system", f"🔄 Gespräch fortgesetzt ({turns} Turns geladen)")
                logger.info(f"Gespräch fortgesetzt: {len(history)} History-Einträge")

        # Im Hintergrundmodus kein Startup-Sound
        if not self.settings.background_mode:
            self.synthesizer.speak_async("Jarvis bereit.")

    def stop(self):
        self._running = False
        self._cancel_streaming.set()        # laufendes Streaming stoppen
        self.task_manager.stop()            # TaskManager Worker beenden
        self._stop_bg_listen_safe()
        # Gesprächs-Log persistent speichern
        try:
            self.conversation_log.flush()
        except Exception:
            pass
        # Alle ausstehenden Timer abbrechen damit sie nach dem Stop nicht noch feuern
        for t in self._active_timers:
            try:
                t.cancel()
            except Exception:
                pass
        self._active_timers.clear()
        self.synthesizer.stop()
        self.hotkey.stop()
        self._set_state(State.IDLE)
        logger.info("Jarvis gestoppt.")

    def send_text_command_direct(self, text: str):
        """
        Führt einen Text-Befehl direkt aus (ohne Queue) — für interne Nutzung.
        Für externe Aufrufe bitte send_text_command() verwenden.
        """
        if self.state in (State.PROCESSING, State.SPEAKING, State.CMD_LISTENING):
            return
        threading.Thread(
            target=self._handle_text_command, args=(text,), daemon=True
        ).start()

    def improve_last_response(self):
        """Fordert eine verbesserte Version der letzten KI-Antwort an."""
        last = self.grok.get_last_response()
        if not last:
            self._notify("system", "Keine vorherige Antwort zum Verbessern.")
            return
        self.send_text_command(
            "Bitte verbessere deine letzte Antwort — präziser, "
            "vollständiger und klarer formuliert."
        )

    def set_response_length(self, mode: str):
        """Setzt Antwortlänge: 'short' | 'normal' | 'detailed'."""
        hint = _LENGTH_HINTS.get(mode, "")
        self.grok.set_length_hint(hint)
        self.settings.response_length = mode
        self.settings.save()
        labels = {"short": "Kurz", "normal": "Normal", "detailed": "Detailliert"}
        self._notify("system", f"📏 Antwortlänge: {labels.get(mode, mode)}")

    def set_tone(self, mode: str):
        """Setzt Antwort-Ton: 'formal' | 'normal' | 'casual' | 'technical' | 'creative'."""
        hint = _TONE_HINTS.get(mode, "")
        self.grok.set_tone(hint)
        self.settings.response_tone = mode
        self.settings.save()
        labels = {
            "formal":    "Formell",
            "normal":    "Normal",
            "casual":    "Casual",
            "technical": "Technisch",
            "creative":  "Kreativ",
        }
        self._notify("system", f"🎭 Ton: {labels.get(mode, mode)}")

    def set_multi_step(self, enabled: bool):
        """Schaltet Schritt-für-Schritt Reasoning ein/aus."""
        self.grok.set_multi_step(enabled)
        self.settings.multi_step_reasoning = enabled
        self.settings.save()
        self._notify("system", f"🧠 Multi-Step Reasoning: {'AN' if enabled else 'AUS'}")

    def set_task_planning(self, enabled: bool):
        """Schaltet Aufgaben-Planung ein/aus."""
        self.grok.set_task_planning(enabled)
        self.settings.task_planning = enabled
        self.settings.save()
        self._notify("system", f"📋 Aufgaben-Planung: {'AN' if enabled else 'AUS'}")

    def set_parallel_tasks(self, enabled: bool):
        """Schaltet parallele Tool-Ausführung ein/aus."""
        self.grok.set_parallel_tasks(enabled)
        self.settings.parallel_tasks = enabled
        self.settings.save()
        self._notify("system", f"⚡ Parallel-Modus: {'AN' if enabled else 'AUS'}")

    # ── Hörmodus ─────────────────────────────────────────────────────────────

    def set_local_wake_word(self, enabled: bool):
        """
        Schaltet lokale Wake-Word Erkennung ein/aus.
        Bei Aktivierung wird openwakeword geladen (einmalig ~5 MB Download).
        """
        if enabled and self._local_ww_detector is None:
            # Detector noch nicht initialisiert → jetzt laden (kann dauern)
            self._notify("system", "🎯 Lade lokales Wake-Word Modell …")

            def _init_and_apply():
                ok = self._init_local_ww_detector(silent=False)
                if ok:
                    self.settings.local_wake_word = True
                    self.settings.save()
                    self._fire_settings_changed()
                    self._notify("system", "🎯 Lokale Wake-Word Erkennung AN (offline)")
                    # Listener neu starten mit lokalem Detektor
                    if self.state == State.WAKE_LISTENING and self._running:
                        self._stop_bg_listen_safe(wait=True)
                        self._start_bg_listen()
                else:
                    # Init fehlgeschlagen — Settings zurücksetzen + UI informieren
                    self.settings.local_wake_word = False
                    self.settings.save()
                    self._fire_settings_changed()

            threading.Thread(target=_init_and_apply, daemon=True).start()
            return

        # Detektor bereits vorhanden oder deaktivieren
        self.settings.local_wake_word = enabled
        self.settings.save()
        self._fire_settings_changed()

        if self.state == State.WAKE_LISTENING and self._running:
            self._stop_bg_listen_safe(wait=True)
            self._start_bg_listen()

        if enabled:
            self._notify("system", "🎯 Lokale Wake-Word Erkennung AN (offline)")
        else:
            self._notify("system", "🎯 Cloud Wake-Word Erkennung AN")

    def set_background_mode(self, enabled: bool):
        """Hintergrundmodus: Jarvis startet stumm und zeigt Panel nicht automatisch."""
        self.settings.background_mode = enabled
        self.settings.save()
        self._fire_settings_changed()
        if enabled:
            self._notify("system", "🔇 Hintergrundmodus AN — stumme Aktivierung")
        else:
            self._notify("system", "🔇 Hintergrundmodus AUS")

    def set_continuous_listening(self, enabled: bool):
        """
        Dauerhaftes Zuhören: kein Wake-Word nötig — jede Spracheingabe
        wird als Befehl verarbeitet.
        Startet den Hintergrund-Listener mit dem neuen Modus neu.
        """
        self.settings.continuous_listening = enabled
        self.settings.save()
        self._fire_settings_changed()

        # Listener neu starten (Modus wechselt zwischen Wake-Word und Alles)
        if self.state == State.WAKE_LISTENING and self._running:
            self._stop_bg_listen_safe(wait=True)
            self._start_bg_listen()

        if enabled:
            self._notify("system", "🔊 Dauerhaftes Zuhören AN — kein Wake-Word nötig")
        else:
            self._notify("system", "🔊 Dauerhaftes Zuhören AUS")

    # ── Sprach-Analyse ────────────────────────────────────────────────────────

    def set_speaker_recognition(self, enabled: bool):
        """Schaltet Sprechererkennung ein/aus."""
        if enabled and self._speaker_id is None:
            self._notify("system", "👤 Lade Sprechererkennung …")

            def _init():
                ok = self._init_speaker_id(silent=False)
                if ok:
                    self.settings.speaker_recognition = True
                    self.settings.save()
                    self._fire_settings_changed()
                    enrolled = self._speaker_id.has_enrollment()
                    status = "Stimme einrichten!" if not enrolled else "Profil geladen"
                    self._notify("system", f"👤 Sprechererkennung AN — {status}")
                else:
                    self.settings.speaker_recognition = False
                    self.settings.save()
                    self._fire_settings_changed()

            threading.Thread(target=_init, daemon=True).start()
            return

        self.settings.speaker_recognition = enabled
        self.settings.save()
        self._fire_settings_changed()
        self._notify("system", f"👤 Sprechererkennung: {'AN' if enabled else 'AUS'}")

    def set_emotion_detection(self, enabled: bool):
        """Schaltet Emotionserkennung ein/aus."""
        self.settings.emotion_detection = enabled
        self.settings.save()
        self._fire_settings_changed()
        self._notify("system", f"😊 Emotionserkennung: {'AN' if enabled else 'AUS'}")

    def set_smart_pause(self, enabled: bool):
        """Schaltet adaptive Pausen-Erkennung im Recognizer ein/aus."""
        self.settings.smart_pause = enabled
        self.settings.save()
        self.recognizer.smart_pause = enabled
        self._fire_settings_changed()
        self._notify("system", f"⏸ Pausen verstehen: {'AN' if enabled else 'AUS'}")

    def set_interrupt_handling(self, enabled: bool):
        """Schaltet Interrupt-Handling ein/aus."""
        self.settings.interrupt_handling = enabled
        self.settings.save()
        self._fire_settings_changed()
        self._notify("system", f"✋ Interrupt-Erkennung: {'AN' if enabled else 'AUS'}")

    def set_whisper_mode(self, enabled: bool):
        """
        Schaltet den Flüstermodus ein/aus.
        Wenn aktiv: Jarvis erkennt leise Sprache und antwortet entsprechend gedämpft.
        """
        self.settings.whisper_mode = enabled
        self.settings.save()
        self._fire_settings_changed()
        self._notify("system", f"🤫 Flüstermodus: {'AN' if enabled else 'AUS'}")

    def set_multi_command(self, enabled: bool):
        """
        Schaltet Mehrfachbefehle ein/aus.
        Wenn aktiv: Sätze mit 'und dann', 'danach', 'außerdem' werden
        in einzelne Aufgaben aufgeteilt und nacheinander abgearbeitet.
        """
        self.settings.multi_command = enabled
        self.settings.save()
        self._fire_settings_changed()
        self._notify("system", f"📋 Mehrfachbefehle: {'AN' if enabled else 'AUS'}")

    def set_noise_filter(self, enabled: bool):
        """
        Schaltet den Geräuschfilter ein/aus.
        Wenn aktiv: spektrale Subtraktion reduziert stationäres Hintergrundgeräusch
        vor der Übertragung an Whisper — verbessert STT-Qualität in lauten Umgebungen.
        """
        self.settings.noise_filter = enabled
        self.settings.save()
        self.recognizer.noise_filter = enabled
        self._fire_settings_changed()
        self._notify("system", f"🔇 Geräuschfilter: {'AN' if enabled else 'AUS'}")

    def set_long_term_context(self, enabled: bool):
        """
        Schaltet den Langzeit-Kontext ein/aus.
        Wenn aktiv: Zusammenfassungen vergangener Gespräche werden in den
        System-Prompt eingebettet — die KI erinnert sich sitzungsübergreifend.
        """
        self.settings.long_term_context = enabled
        self.settings.save()
        if enabled:
            self.grok.set_context_provider(self.conversation_log.get_context_string)
        else:
            self.grok.set_context_provider(None)
        self._fire_settings_changed()
        sessions = self.conversation_log.get_session_count()
        status = f"{sessions} Sessions gespeichert" if sessions else "noch keine Sessions"
        self._notify("system", f"🧠 Langzeit-Kontext: {'AN' if enabled else 'AUS'} ({status})")

    def set_conversation_resume(self, enabled: bool):
        """
        Schaltet 'Gespräch fortsetzen' ein/aus.
        Wenn aktiv: beim nächsten Start von Jarvis werden die letzten Nachrichten
        der vorherigen Session in den Chat-Verlauf geladen.
        """
        self.settings.conversation_resume = enabled
        self.settings.save()
        self._fire_settings_changed()
        self._notify("system", f"🔄 Gespräch fortsetzen: {'AN' if enabled else 'AUS'}")

    def set_style_learning(self, enabled: bool):
        """
        Schaltet das Sprachstil-Lernen ein/aus.
        Wenn aktiv: Jarvis analysiert jeden Befehl und lernt Ton und bevorzugte
        Antwortlänge. Bei 'Adaptive Antworten' werden diese automatisch angewendet.
        """
        self.settings.style_learning = enabled
        self.settings.save()
        self._fire_settings_changed()
        n = self.style_learner.get_total_commands()
        status = f"{n} Commands gelernt" if n else "noch kein Profil"
        self._notify("system", f"📚 Sprachstil lernen: {'AN' if enabled else 'AUS'} ({status})")

    def set_adaptive_response_time(self, enabled: bool):
        """
        Schaltet die adaptive Reaktionszeit ein/aus.
        Wenn aktiv: einfache kurze Befehle (Timer, App öffnen, Uhrzeit…) erhalten
        ein niedrigeres Token-Limit → kürzere, schnellere Antwort.
        Komplexe Fragen werden weiterhin vollständig beantwortet.
        """
        self.settings.adaptive_response_time = enabled
        self.settings.save()
        self._fire_settings_changed()
        self._notify("system", f"⚡ Reaktionszeit anpassen: {'AN' if enabled else 'AUS'}")

    def set_adaptive_responses(self, enabled: bool):
        """
        Schaltet adaptive Antworten ein/aus.
        Kombiniert: Stil-Lernen + automatische Anwendung der gelernten Präferenzen.
        Wenn aktiv: Ton und Länge werden aus dem Style-Profil abgeleitet und
        automatisch in GrokClient übernommen — ohne manuelle Einstellung nötig.
        """
        self.settings.adaptive_responses = enabled
        self.settings.save()
        self._fire_settings_changed()
        if enabled and self.style_learner.get_total_commands() >= 15:
            self._apply_style_preferences()
            length = self.style_learner.get_preferred_length()
            tone   = self.style_learner.get_preferred_tone()
            self._notify("system",
                f"🎯 Adaptive Antworten: AN (Länge: {length}, Ton: {tone})")
        else:
            n = self.style_learner.get_total_commands()
            needed = max(0, 15 - n)
            self._notify("system",
                f"🎯 Adaptive Antworten: {'AN' if enabled else 'AUS'}"
                + (f" — noch {needed} Commands zum Lernen nötig" if enabled and needed > 0 else ""))

    def enroll_speaker(self, duration_s: float = 6.0) -> bool:
        """
        Nimmt eine Stimmprobe auf und erstellt das Sprecher-Profil.
        Gibt True zurück wenn erfolgreich.
        Muss auf einem Background-Thread aufgerufen werden (blockiert duration_s Sekunden).
        """
        if self._speaker_id is None:
            self._notify("system", "⚠ Sprechererkennung nicht initialisiert.")
            return False

        self._notify("system", f"🎙 Aufnahme startet ({duration_s:.0f}s) — bitte sprechen …")
        # Kurz warten damit TTS-Ansage abgespielt werden kann
        time.sleep(0.5)

        # Aufnahme via Recognizer (max_duration = duration_s)
        audio = self.recognizer._record(
            max_wait=None,
            max_duration=duration_s,
            stop_event=None,
        )

        if not audio:
            self._notify("system", "⚠ Keine Aufnahme — bitte Mikrofon prüfen.")
            return False

        ok = self._speaker_id.enroll(audio)
        if ok:
            self._notify("system", "✅ Stimm-Profil erstellt und gespeichert.")
        else:
            self._notify("system", "❌ Enrollment fehlgeschlagen.")
        self._fire_settings_changed()
        return ok

    def has_speaker_enrollment(self) -> bool:
        """True wenn ein Stimm-Profil vorhanden ist."""
        return self._speaker_id is not None and self._speaker_id.has_enrollment()

    def _init_speaker_id(self, silent: bool = False) -> bool:
        """Initialisiert den SpeakerIdentifier. Gibt True bei Erfolg zurück."""
        try:
            from services.speech.speaker_id import SpeakerIdentifier
            self._speaker_id = SpeakerIdentifier()
            logger.info("SpeakerIdentifier bereit.")
            return True
        except ImportError as exc:
            if not silent:
                self._notify("system", f"⚠ {exc}")
            logger.warning(f"SpeakerIdentifier: {exc}")
            self._speaker_id = None
            return False
        except RuntimeError as exc:
            if not silent:
                self._notify("system", f"⚠ SpeakerIdentifier: {exc}")
            logger.error(f"SpeakerIdentifier: {exc}")
            self._speaker_id = None
            return False

    # ── Interrupt-Monitor ─────────────────────────────────────────────────────

    def _start_interrupt_monitor(self):
        """
        Startet einen Hintergrund-Thread der das Mikrofon überwacht während
        Jarvis spricht. Bei Benutzer-Unterbrechung: TTS stoppen + Befehl aufnehmen.
        Verhindert parallele Monitore über self._interrupt_active.
        """
        if self._interrupt_active.is_set():
            return
        self._interrupt_active.set()
        threading.Thread(
            target=self._interrupt_monitor_loop,
            daemon=True,
            name="InterruptMonitor",
        ).start()

    def _interrupt_monitor_loop(self):
        """Loop: überwacht Energie während State == SPEAKING."""
        import sounddevice as sd
        import numpy as _np

        interrupt_threshold = self.recognizer._threshold + 7   # dB über Ambient
        _MONITOR_SR   = 16_000
        _MONITOR_CHUNK = 1_600   # 100 ms

        try:
            # Kurze Anlaufverzögerung → TTS hat Zeit zu starten
            time.sleep(0.4)

            with sd.InputStream(
                samplerate=_MONITOR_SR,
                channels=1,
                dtype="int16",
                blocksize=_MONITOR_CHUNK,
                device=self.recognizer._device,
            ) as stream:
                loud_count = 0

                while self.state == State.SPEAKING and self._running:
                    try:
                        chunk, _ = stream.read(_MONITOR_CHUNK)
                    except Exception:
                        break

                    samples = chunk[:, 0].astype(_np.float64)
                    rms     = _np.sqrt(_np.mean(samples ** 2))
                    db      = 20.0 * _np.log10(max(rms, 1.0))

                    if db >= interrupt_threshold:
                        loud_count += 1
                        if loud_count >= 2:   # 200 ms laute Sprache → Interrupt
                            logger.info(
                                f"Interrupt: {db:.1f} dB ≥ {interrupt_threshold:.1f} dB"
                            )
                            self._handle_interrupt()
                            return
                    else:
                        loud_count = 0

        except Exception as exc:
            logger.debug(f"Interrupt-Monitor: {exc}")
        finally:
            self._interrupt_active.clear()

    def _handle_interrupt(self):
        """Reagiert auf eine Benutzer-Unterbrechung: TTS stoppen + Befehl aufnehmen."""
        self.synthesizer.stop()
        self._notify("system", "✋ Unterbrochen")
        self._interrupt_active.clear()

        # Unterbrechung dem StyleLearner melden (für Antwortlängen-Anpassung)
        if self.settings.style_learning:
            try:
                self.style_learner.record_interrupt()
            except Exception:
                pass

        if not self._running:
            return

        time.sleep(0.15)   # kurze Pause damit Mikrofon-Echo abklingt
        self._set_state(State.CMD_LISTENING)
        self._cmd_stop_event.clear()

        cmd = self.recognizer.listen_once(
            timeout=6,
            phrase_time_limit=12,
            stop_event=self._cmd_stop_event,
        )

        if cmd:
            cmd = self._apply_emotion_hint(cmd)
            cmd = self._apply_whisper_hint(cmd)
            self._notify("user", cmd)
            self._dispatch_command(cmd)
        else:
            self._notify("system", "Kein Folgebefehl erkannt.")

        if self._running:
            self._set_state(State.WAKE_LISTENING)
            self._start_bg_listen()

    # ── Emotion-Hint Hilfsmethode ─────────────────────────────────────────────

    def _apply_emotion_hint(self, command: str) -> str:
        """
        Fügt einen Emotions-Kontext-Hinweis an den Befehl an, falls
        emotion_detection aktiviert und eine Stimmung erkannt wurde.
        """
        if not self.settings.emotion_detection:
            return command
        audio = self.recognizer._last_audio
        if not audio:
            return command
        try:
            from services.speech.emotion_detector import detect_emotion
            result = detect_emotion(audio)
            if result["hint"]:
                logger.debug(f"Emotion erkannt: {result['label']}")
                return f"[Nutzer klingt {result['hint']}] {command}"
        except Exception as exc:
            logger.debug(f"Emotion-Hint: {exc}")
        return command

    def _apply_style_preferences(self):
        """
        Übernimmt die vom StyleLearner gelernten Präferenzen in GrokClient.
        Wird beim Aktivieren von 'Adaptive Antworten' und nach jedem Lernschritt aufgerufen.
        """
        length = self.style_learner.get_preferred_length()
        tone   = self.style_learner.get_preferred_tone()
        self.grok.set_length_hint(_LENGTH_HINTS.get(length, ""))
        self.grok.set_tone(_TONE_HINTS.get(tone, ""))
        logger.debug(f"Style-Präferenzen angewendet: Länge={length}, Ton={tone}")

    def _classify_complexity(self, command: str) -> str:
        """
        Klassifiziert die Komplexität eines Befehls: 'simple', 'normal' oder 'complex'.
        Wird von 'Reaktionszeit anpassen' genutzt um max_tokens zu setzen.

        Simple  → kurze Aktionsbefehle, Faktenabfragen
        Normal  → mittlere Komplexität
        Complex → Erklärungen, technische Fragen, mehrstufige Aufgaben
        """
        import re as _re
        text = command.lower().strip()
        # System-Hints herausfiltern
        if text.startswith("["):
            end = text.find("]")
            if end != -1:
                text = text[end + 1:].strip()

        words = text.split()
        wc    = len(words)

        # Direkte Aktionsverben → einfach (wenn Befehl kurz)
        _SIMPLE_STARTS = (
            "öffne", "starte", "schließe", "spiel", "zeig", "sag mir",
            "was ist", "wer ist", "wie spät", "wieviel uhr", "uhrzeit",
            "timer", "erinnere", "beende", "stoppe", "suche nach",
            "öffne mal", "mach", "spiele",
        )
        # Komplexitätsindikatoren → komplex
        _COMPLEX = (
            "erkläre", "warum", "wie funktioniert", "was bedeutet",
            "vergleiche", "analysiere", "schreibe", "erstelle",
            "berechne", "löse", "schritt für schritt", "ausführlich",
            "im detail", "zusammenfasse", "übersetze", "generiere",
            "entwickle", "plane", "entwirf",
        )

        if wc <= 3:
            return "simple"
        if any(text.startswith(s) for s in _SIMPLE_STARTS) and wc <= 7:
            return "simple"
        if any(m in text for m in _COMPLEX) or wc >= 16:
            return "complex"
        return "normal"

    def _apply_whisper_hint(self, command: str) -> str:
        """
        Fügt einen Flüster-Kontext-Hinweis an den Befehl an, falls
        whisper_mode aktiviert ist und die letzte Aufnahme als Flüstern erkannt wurde.
        Jarvis antwortet dann leiser / gedämpfter (KI bekommt den Kontext).
        """
        if not self.settings.whisper_mode:
            return command
        if not getattr(self.recognizer, "_was_whisper", False):
            return command
        logger.debug("Flüstermodus: leise Eingabe erkannt")
        return f"[Nutzer flüstert] {command}"

    def _split_commands(self, text: str) -> list:
        """
        Zerlegt einen Satz mit mehreren Befehlen in Einzelbefehle.
        Erkennt deutsche Verbindungswörter: 'und dann', 'danach', 'außerdem', etc.
        Gibt immer mindestens eine Liste mit einem Element zurück.
        """
        import re
        # Trennmuster: Konjunktionen die neue Teilbefehle einleiten
        # Reihenfolge: längere Muster zuerst um Überschneidungen zu vermeiden
        _SPLIT_PATTERN = re.compile(
            r"(?:,?\s+(?:und\s+dann|und\s+danach|und\s+außerdem|danach|"
            r"außerdem|dann\s+auch|dann\s+bitte|anschließend|daneben|"
            r"zusätzlich\s+(?:dazu\s+)?(?:auch\s+)?)|"
            r";\s*)",
            re.IGNORECASE,
        )
        parts = _SPLIT_PATTERN.split(text.strip())
        # Nur nicht-leere Teile mit mindestens 4 Zeichen behalten
        result = [p.strip() for p in parts if p and len(p.strip()) >= 4]
        return result if result else [text.strip()]

    def _init_local_ww_detector(self, silent: bool = False) -> bool:
        """
        Initialisiert den LocalWakeWordDetector.
        Gibt True zurück wenn erfolgreich, False bei Fehler.
        silent=True unterdrückt die Fehler-Benachrichtigung (beim Start).
        """
        try:
            from services.speech.local_wake_word import LocalWakeWordDetector
            self._local_ww_detector = LocalWakeWordDetector(
                device=self.recognizer._device,
            )
            logger.info("LocalWakeWordDetector bereit.")
            return True
        except ImportError as exc:
            msg = str(exc)
            logger.warning(f"LocalWakeWordDetector: {msg}")
            if not silent:
                self._notify("system", f"⚠ {msg}")
            self._local_ww_detector = None
            return False
        except RuntimeError as exc:
            msg = str(exc)
            logger.error(f"LocalWakeWordDetector: {msg}")
            if not silent:
                self._notify("system", f"⚠ Lokaler Detektor: {exc}")
            self._local_ww_detector = None
            return False

    # ── Task Queue / Abbruch ──────────────────────────────────────────────────

    def on_task_update(self, cb: Callable[[List[Dict]], None]):
        """Registriert einen Listener für Task-Status-Updates."""
        self._task_listeners.append(cb)

    def cancel_current_task(self):
        """
        Bricht den aktuell laufenden Task ab:
        1. Streaming-Flag setzen → on_chunk hört auf zu verarbeiten
        2. TTS stoppen
        3. Task-Manager informieren
        """
        self._cancel_streaming.set()
        self.synthesizer.stop()
        self.task_manager.cancel_current()
        self._notify("system", "⛔ Task abgebrochen.")
        logger.info("Task manuell abgebrochen.")

    def cancel_all_tasks(self):
        """Bricht alle Tasks (wartend + laufend) ab."""
        self._cancel_streaming.set()
        self.synthesizer.stop()
        self.task_manager.cancel_all()
        self.task_manager.clear_done()
        self._notify("system", "⛔ Alle Tasks abgebrochen und Queue geleert.")

    def send_text_command(self, text: str, priority: int = 5):
        """
        Reiht einen Text-Befehl in die Task-Warteschlange ein.
        Falls Jarvis gerade beschäftigt ist, wird der Befehl in die Queue gestellt.
        """
        if not text.strip():
            return
        label = text[:50] + "…" if len(text) > 50 else text
        self.task_manager.submit(
            self._run_text_task,
            args=(text,),
            priority=priority,
            label=label,
        )

    def _run_text_task(self, text: str, cancel_event=None):
        """
        Wrapper für den TaskManager: führt einen Text-Befehl aus.
        Wartet falls Jarvis durch die Sprach-Aktivierung beschäftigt ist.
        """
        # Kurz warten wenn Voice-Flow gerade aktiv ist (max. 30s)
        wait_s = 0.0
        while self.state in (State.PROCESSING, State.SPEAKING, State.CMD_LISTENING):
            if cancel_event and cancel_event.is_set():
                return
            if wait_s > 30:
                self._notify("system", "⚠ Task-Timeout: Jarvis war zu lange beschäftigt.")
                return
            time.sleep(0.2)
            wait_s += 0.2

        was_listening = self.state == State.WAKE_LISTENING
        if was_listening:
            self._stop_bg_listen_safe()

        self._notify("user", text)
        self._run_command(text, cancel_event=cancel_event)

        if was_listening and self._running:
            self._set_state(State.WAKE_LISTENING)
            self._start_bg_listen()

    # ── Wiederholende Tasks ───────────────────────────────────────────────────

    def add_recurring_task(
        self,
        label:            str,
        interval_minutes: float,
        command:          str,
        priority:         int = 7,
    ) -> str:
        """
        Fügt einen wiederholenden Task hinzu.
        command wird per send_text_command() ausgeführt.
        Gibt eine ID zurück (zum späteren Entfernen).
        """
        interval_s = interval_minutes * 60

        def _recurring_func(cancel_event=None):
            self._notify("system", f"🔄 Automatisierung: {label}")
            self.send_text_command(command, priority=priority)

        # Im TaskManager registrieren
        tm_rid = self.task_manager.add_recurring(
            label=label,
            interval_seconds=interval_s,
            func=_recurring_func,
            priority=priority,
        )

        # In Settings speichern
        entry = {
            "id":               tm_rid,
            "label":            label,
            "interval_minutes": interval_minutes,
            "command":          command,
            "priority":         priority,
        }
        self.settings.recurring_tasks.append(entry)
        self.settings.save()

        self._notify("system", f"🔄 Automatisierung hinzugefügt: '{label}' alle {interval_minutes:.0f} Min.")
        logger.info(f"Recurring task: '{label}' alle {interval_minutes}min → '{command}'")
        return tm_rid

    def remove_recurring_task(self, rid: str):
        """Entfernt einen wiederholenden Task anhand der ID."""
        self.task_manager.remove_recurring(rid)
        # Aus Settings entfernen
        self.settings.recurring_tasks = [
            t for t in self.settings.recurring_tasks if t.get("id") != rid
        ]
        self.settings.save()
        self._notify("system", f"🗑 Automatisierung entfernt: {rid}")

    def get_recurring_tasks(self) -> List[Dict]:
        """Gibt alle wiederholenden Tasks zurück."""
        return self.task_manager.list_recurring()

    def _restore_recurring_tasks(self):
        """Stellt gespeicherte wiederholende Tasks beim Start wieder her."""
        for entry in self.settings.recurring_tasks:
            if not isinstance(entry, dict):
                logger.warning(f"Ungültiger Task-Eintrag (kein dict) übersprungen: {entry!r}")
                continue
            try:
                label    = entry.get("label", "Task") or "Task"
                interval = entry.get("interval_minutes", 60)
                command  = entry.get("command", "")
                priority = entry.get("priority", 7)

                # Typvalidierung — JSON null oder falsche Typen abfangen
                if not isinstance(interval, (int, float)) or interval <= 0:
                    interval = 60
                if not isinstance(priority, int):
                    priority = 7
            except Exception as exc:
                logger.warning(f"Task-Eintrag konnte nicht gelesen werden: {exc}")
                continue

            if not command:
                continue

            rid = entry.get("id", "")

            def make_func(lbl=label, cmd=command, prio=priority):
                def _func(cancel_event=None):
                    self._notify("system", f"🔄 Automatisierung: {lbl}")
                    self.send_text_command(cmd, priority=prio)
                return _func

            try:
                new_rid = self.task_manager.add_recurring(
                    label=label,
                    interval_seconds=interval * 60,
                    func=make_func(),
                    priority=priority,
                )
            except Exception as exc:
                logger.error(f"Recurring Task '{label}' konnte nicht wiederhergestellt werden: {exc}")
                continue
            # ID in Settings aktualisieren falls nötig
            entry["id"] = new_rid

        if self.settings.recurring_tasks:
            self.settings.save()
            logger.info(
                f"{len(self.settings.recurring_tasks)} wiederholende Tasks wiederhergestellt."
            )

    def _on_task_update(self, task_list: List[Dict]):
        """Wird vom TaskManager aufgerufen — leitet Updates an UI-Listener weiter."""
        for cb in self._task_listeners:
            try:
                cb(task_list)
            except Exception as exc:
                logger.debug(f"Task-Listener Fehler: {exc}")

    def _compute_confidence(self, response: str, tool_used: bool = False) -> int:
        """Heuristischer Konfidenz-Score 1–10."""
        score = 7
        text = response.lower()
        hedge_count = sum(1 for w in _HEDGE_WORDS if w in text)
        score -= min(hedge_count * 2, 4)
        if tool_used:
            score += 2          # Tool-Ergebnis → fundierte Antwort
        if len(response) > 200:
            score = min(10, score + 1)  # ausführliche Antwort = mehr Substanz
        if len(response) < 20:
            score = max(1, score - 1)
        return max(1, min(10, score))

    # ------------------------------------------------------------------
    # Internal state management
    # ------------------------------------------------------------------

    def _set_state(self, state: State):
        self.state = state
        for cb in self._state_listeners:
            try:
                cb(state)
            except Exception as e:
                logger.error(f"State-Listener Fehler: {e}")

    def _notify(self, role: str, content: str):
        for cb in self._message_listeners:
            try:
                cb(role, content)
            except Exception as e:
                logger.error(f"Message-Listener Fehler: {e}")

    def _on_provider_switch(self, old: str, new: str):
        """Wird vom RouterClient aufgerufen wenn der aktive AI-Provider wechselt."""
        msg = f"KI gewechselt: {old} → {new}"
        logger.info(msg)
        self._notify("system", f"🤖 {msg}")

    # ------------------------------------------------------------------
    # Stop / Escape
    # ------------------------------------------------------------------

    def _on_stop(self):
        """Escape gedrückt: TTS stoppen oder Lauschen abbrechen."""
        if self.state == State.SPEAKING:
            logger.info("Escape: Sprachausgabe gestoppt.")
            self.synthesizer.stop()
        elif self.state == State.CMD_LISTENING:
            logger.info("Escape: Befehlseingabe abgebrochen.")
            self._cmd_stop_event.set()   # bricht listen_once() sofort ab
            self.synthesizer.stop()
        elif self.state == State.PROCESSING:
            logger.info("Escape: Verarbeitung gestoppt.")
            self.synthesizer.stop()

    # ------------------------------------------------------------------
    # Background listening / wake word
    # ------------------------------------------------------------------

    def _start_bg_listen(self):
        """Startet den Hintergrund-Listener — je nach Modus lokal oder Cloud."""
        # Sicherstellen dass ein ggf. noch laufender Listener zuerst gestoppt wird
        # um einen Leak des alten Background-Threads zu vermeiden.
        self._stop_bg_listen_safe()

        # Dauerhaftes Zuhören braucht immer Cloud-STT (muss alles transkribieren)
        if self.settings.continuous_listening:
            self._stop_bg_listen = self.recognizer.listen_in_background(
                self._on_bg_audio
            )
        # Lokaler Wake-Word Detektor (kein API-Aufruf)
        elif self.settings.local_wake_word and self._local_ww_detector:
            self._stop_bg_listen = self._local_ww_detector.start(
                self._on_local_wake_word
            )
        # Standard: Cloud-STT mit Wake-Word Check
        else:
            self._stop_bg_listen = self.recognizer.listen_in_background(
                self._on_bg_audio
            )

    def _stop_bg_listen_safe(self, wait: bool = False):
        if self._stop_bg_listen:
            try:
                self._stop_bg_listen(wait_for_stop=wait)
            except Exception:
                pass
            self._stop_bg_listen = None

    def _on_local_wake_word(self):
        """Callback: Lokaler Wake-Word Detektor hat 'hey Jarvis' erkannt."""
        if not self._running or self.state != State.WAKE_LISTENING:
            return
        logger.info("Lokales Wake-Word → aktiviere Jarvis.")
        if self._activation_lock.acquire(blocking=False):
            # Im Hintergrundmodus kein 'Ja?' — sonst mit Bestätigung
            silent = self.settings.background_mode
            threading.Thread(
                target=self._activate_and_release,
                kwargs={"silent": silent},
                daemon=True,
            ).start()

    def _on_bg_audio(self, text: str):
        if not self._running or self.state != State.WAKE_LISTENING:
            return

        # ── Dauerhaftes Zuhören: alles als Befehl behandeln ──────────────────
        if self.settings.continuous_listening:
            text_clean = text.strip() if text else ""
            if len(text_clean) < 3:
                return  # Zu kurz / Rauschen ignorieren
            # Sprechererkennung
            if self.settings.speaker_recognition and self._speaker_id:
                if not self._speaker_id.is_known_speaker(self.recognizer._last_audio):
                    logger.info("Unbekannter Sprecher (continuous) — ignoriert.")
                    return
            # Wake-Word aus dem Text herausschneiden falls vorhanden
            inline = self._extract_inline_command(text_clean) or text_clean
            inline = self._apply_emotion_hint(inline)
            inline = self._apply_whisper_hint(inline)
            logger.info(f"Dauerhaftes Zuhören: '{inline}'")
            if self._activation_lock.acquire(blocking=False):
                threading.Thread(
                    target=self._activate_and_release,
                    args=(inline,),
                    kwargs={"silent": True},
                    daemon=True,
                ).start()
            return

        # ── Normal: Wake-Word prüfen ──────────────────────────────────────────
        if self._is_wake_word(text):
            inline = self._extract_inline_command(text)
            logger.info(f"Wake-Word erkannt: '{text}'" + (f" → Befehl: '{inline}'" if inline else ""))
            if self._activation_lock.acquire(blocking=False):
                silent = self.settings.background_mode
                threading.Thread(
                    target=self._activate_and_release,
                    args=(inline,),
                    kwargs={"silent": silent},
                    daemon=True,
                ).start()

    def _activate_and_release(self, inline_command: Optional[str] = None, silent: bool = False):
        try:
            self._activate(inline_command, silent=silent)
        finally:
            try:
                self._activation_lock.release()
            except RuntimeError:
                pass  # Lock war nicht gehalten (sollte nicht vorkommen)

    def _on_hotkey(self):
        """Globaler Hotkey gedrückt → direkt aktivieren (aus jedem Ruhezustand)."""
        # Busy-States: ignorieren
        if self.state in (State.CMD_LISTENING, State.PROCESSING, State.SPEAKING):
            logger.debug(f"Hotkey ignoriert — State: {self.state.name}")
            return

        logger.info(f"Hotkey gedrückt (State: {self.state.name}) — aktiviere Jarvis.")

        # Falls State hängt (z. B. ERROR/IDLE) → zuerst Background-Listen stoppen
        if self.state != State.WAKE_LISTENING:
            self._stop_bg_listen_safe()

        if self._activation_lock.acquire(blocking=False):
            threading.Thread(
                target=self._activate_and_release,
                kwargs={"silent": True},
                daemon=True,
            ).start()
        else:
            logger.warning("Hotkey: Aktivierung läuft bereits, übersprungen.")

    def _is_wake_word(self, text: str) -> bool:
        text_lower = text.lower().strip()

        # 1. Exakter Substring-Match
        for ww in self.settings.wake_words:
            if ww in text_lower:
                return True

        # 2. Fuzzy-Match: Whisper transkribiert "Jarvis" oft als "Javes", "Javis", "Harvey" etc.
        words = text_lower.split()
        for word in words:
            if len(word) < 3:
                continue
            score = difflib.SequenceMatcher(None, "jarvis", word).ratio()
            if score >= 0.72:
                logger.debug(f"Fuzzy Wake-Word: '{word}' ≈ 'jarvis' ({score:.2f})")
                return True

        return False

    def _extract_inline_command(self, text: str) -> Optional[str]:
        """Extrahiert den Befehl nach dem Wake-Word im selben Satz.
        Vergleich ist case-insensitiv: Wake-Words werden lowercase gespeichert,
        aber STT-Ausgabe bewahrt Originalschreibweise (z. B. 'Hey Jarvis').
        """
        text_lower = text.lower()
        for ww in sorted(self.settings.wake_words, key=len, reverse=True):
            if ww in text_lower:
                pos   = text_lower.find(ww)
                after = text[pos + len(ww):]
                command = after.lstrip(" ,;:.").strip()
                if len(command) > 2:
                    return command
        return None

    # ------------------------------------------------------------------
    # Activation flow
    # ------------------------------------------------------------------

    def _activate(self, inline_command: Optional[str] = None, silent: bool = False):
        self._set_state(State.CMD_LISTENING)
        self._stop_bg_listen_safe(wait=True)

        if inline_command:
            # Sprechererkennung für Inline-Befehle (Wake-Word im selben Satz)
            if self.settings.speaker_recognition and self._speaker_id:
                if not self._speaker_id.is_known_speaker(self.recognizer._last_audio):
                    logger.info("Unbekannter Sprecher (inline) — ignoriert.")
                    self._notify("system", "🔒 Unbekannter Sprecher.")
                    inline_command = None
            if inline_command:
                inline_command = self._apply_emotion_hint(inline_command)
                inline_command = self._apply_whisper_hint(inline_command)
                self._notify("user", inline_command)
                self._dispatch_command(inline_command)
        else:
            # Nur Wake-Word → bestätigen (außer bei Hotkey-Aktivierung)
            if not silent:
                self.synthesizer.speak("Ja?")
            logger.info("Warte auf Befehl...")
            self._cmd_stop_event.clear()
            command = self.recognizer.listen_once(
                timeout=8, phrase_time_limit=12, stop_event=self._cmd_stop_event
            )
            if self._cmd_stop_event.is_set():
                # Escape gedrückt — still abbrechen, kein "nicht verstanden"
                logger.info("Befehlseingabe per Escape abgebrochen.")
            elif command:
                # Sprechererkennung: unbekannte Stimme ablehnen
                if self.settings.speaker_recognition and self._speaker_id:
                    if not self._speaker_id.is_known_speaker(self.recognizer._last_audio):
                        logger.info("Unbekannter Sprecher — Befehl ignoriert.")
                        self._notify("system", "🔒 Unbekannter Sprecher.")
                        command = None
                if command:
                    command = self._apply_emotion_hint(command)
                    command = self._apply_whisper_hint(command)
                    self._notify("user", command)
                    self._dispatch_command(command)
            else:
                self.synthesizer.speak("Entschuldigung, ich habe nichts verstanden.")
                self._notify("system", "Kein Befehl erkannt.")

        if self._running:
            self._set_state(State.WAKE_LISTENING)
            self._start_bg_listen()

    def _handle_text_command(self, text: str):
        was_listening = self.state == State.WAKE_LISTENING
        if was_listening:
            self._stop_bg_listen_safe()

        self._notify("user", text)
        self._run_command(text)

        if was_listening and self._running:
            self._set_state(State.WAKE_LISTENING)
            self._start_bg_listen()

    def _dispatch_command(self, command: str):
        """
        Verteilt einen Befehl — entweder direkt oder als Mehrfachbefehle.
        Bei aktivierten Mehrfachbefehlen wird der Text auf bekannte Konjunktionen geprüft.
        Der erste Teilbefehl wird sofort ausgeführt, weitere werden in die Queue eingereiht.
        """
        if self.settings.multi_command:
            parts = self._split_commands(command)
        else:
            parts = [command]

        if len(parts) > 1:
            n = len(parts)
            self._notify("system", f"📋 {n} Befehle erkannt — werden nacheinander ausgeführt")
            logger.info(f"Mehrfachbefehle: {parts}")
            # Ersten Befehl sofort ausführen
            self._run_command(parts[0])
            # Rest in Queue stellen
            for extra in parts[1:]:
                self._notify("user", extra)
                self.send_text_command(extra)
        else:
            self._run_command(command)

    def _run_command(self, command: str, cancel_event: Optional[threading.Event] = None):
        """
        Verarbeitet einen Befehl über die AI (Streaming).
        cancel_event: wenn gesetzt, wird die Verarbeitung abgebrochen.
        Zusätzlich wird self._cancel_streaming geprüft (gesetzt von cancel_current_task()).
        """
        # Streaming-Flag für diesen Run zurücksetzen
        self._cancel_streaming.clear()

        self._set_state(State.PROCESSING)
        self._last_command = command
        logger.info(f"Verarbeite: '{command}'")

        # ── Sprachstil lernen ─────────────────────────────────────────────────
        if self.settings.style_learning:
            try:
                self.style_learner.learn_command(command)
                # Adaptive Antworten: Präferenzen nach jeweils 5 neuen Commands anwenden
                if (
                    self.settings.adaptive_responses
                    and self.style_learner.get_total_commands() >= 15
                    and self.style_learner.get_total_commands() % 5 == 0
                ):
                    self._apply_style_preferences()
            except Exception as _sl_err:
                logger.debug(f"StyleLearner Fehler: {_sl_err}")

        # ── Reaktionszeit anpassen: Token-Limit setzen ────────────────────────
        if self.settings.adaptive_response_time:
            try:
                complexity = self._classify_complexity(command)
                if complexity == "simple":
                    self.grok.set_adaptive_max_tokens(120)
                    logger.debug("Reaktionszeit: simple → max_tokens=120")
                else:
                    self.grok.set_adaptive_max_tokens(None)   # kein Limit
            except Exception as _rt_err:
                logger.debug(f"Reaktionszeit Fehler: {_rt_err}")
        pipeline = None
        try:
            accumulated_text = ""
            pipeline = self.synthesizer.create_pipeline()
            first_chunk = True
            tool_used = False
            was_cancelled = False

            spoken_text = ""   # nur echter Text, kein Function-Call-XML

            def on_chunk(chunk: str):
                nonlocal accumulated_text, first_chunk, spoken_text, tool_used, was_cancelled

                # Abbruch-Check (cancel_event vom TaskManager ODER cancel_streaming von cancel_current_task)
                if (
                    self._cancel_streaming.is_set()
                    or (cancel_event and cancel_event.is_set())
                ):
                    was_cancelled = True
                    return

                accumulated_text += chunk

                # Function-Call-Syntax (llama-Format) komplett herausfiltern
                if "<function=" in accumulated_text or "</function>" in accumulated_text:
                    tool_used = True
                    return

                # Reine Funktionsnamen ohne Wrapper herausfiltern
                # (Modell gibt manchmal nur "get_time" o.ä. als Text aus)
                chunk_stripped = chunk.strip().rstrip("()")
                if chunk_stripped in _KNOWN_TOOL_NAMES:
                    tool_used = True
                    return

                spoken_text += chunk

                # Beim ersten echten Chunk State auf SPEAKING setzen
                if first_chunk and chunk.strip():
                    first_chunk = False
                    self._set_state(State.SPEAKING)
                    # Interrupt-Monitor starten sobald Jarvis zu sprechen beginnt
                    if self.settings.interrupt_handling:
                        self._start_interrupt_monitor()

                # Live HUD-Update (nur gesprochenen Text zeigen)
                self._notify("assistant_partial", spoken_text)
                # Chunk in die TTS-Pipeline einspeisen
                pipeline.feed(chunk)

            full_response = self.grok.chat_streaming(command, on_chunk)

            # ── Gesprächs-Log speichern (Langzeit-Kontext / Gespräch fortsetzen) ──
            if full_response and not was_cancelled:
                try:
                    self.conversation_log.save_turn(command, full_response)
                except Exception as _log_err:
                    logger.debug(f"ConversationLog Fehler: {_log_err}")

            if was_cancelled:
                self.synthesizer.stop()   # stoppt Pipeline und setzt Flags unter Lock
                self._notify("system", "⛔ Antwort abgebrochen.")
                return

            # Falls on_chunk nie aufgerufen wurde (Tool-Only oder leere Antwort)
            if first_chunk:
                self._set_state(State.SPEAKING)

            # Pipeline abschließen (flusht restlichen Buffer + wartet)
            pipeline.finish()
            with self.synthesizer._lock:
                self.synthesizer._active_pipeline = None
                self.synthesizer._speaking = False

            # Finale Nachricht: Function-Call-XML aus der Anzeige entfernen
            clean_response = re.sub(r"<function=.*?</function>", "", full_response,
                                    flags=re.DOTALL).strip()
            self._notify("assistant", clean_response or full_response)

            # Konfidenz-Score berechnen und broadcasten
            confidence = self._compute_confidence(
                clean_response or full_response, tool_used=tool_used
            )
            self._notify("confidence", str(confidence))

        except Exception as e:
            logger.error(f"Fehler bei der Verarbeitung: {e}")
            self._notify("system", f"Fehler: {e}")
            # Pipeline aufräumen bevor Fallback-TTS abgespielt wird
            if pipeline is not None:
                try:
                    pipeline.stop()
                except Exception:
                    pass
            with self.synthesizer._lock:
                self.synthesizer._active_pipeline = None
                self.synthesizer._speaking = False
            self.synthesizer.speak("Es gab leider einen Fehler.")
            self._set_state(State.ERROR)
            self._notify("confidence", "0")
            # Nach Fehler wieder in Hörbereitschaft wechseln
            if self._running:
                self._set_state(State.WAKE_LISTENING)

    # ------------------------------------------------------------------
    # Tool handlers (registered with GrokClient)
    # ------------------------------------------------------------------

    def _register_tools(self):
        # Bestehende Tools
        self.grok.register_tool("search_internet",    self._tool_search)
        self.grok.register_tool("launch_app",          self._tool_launch)
        self.grok.register_tool("close_app",           self._tool_close)
        self.grok.register_tool("get_time",            self._tool_time)
        self.grok.register_tool("open_website",        self._tool_open_website)
        self.grok.register_tool("set_timer",           self._tool_set_timer)
        self.grok.register_tool("youtube_play",        self._tool_youtube_play)
        self.grok.register_tool("google_first_result", self._tool_google_first_result)
        self.grok.register_tool("remember_info",       self._tool_remember)
        self.grok.register_tool("forget_info",         self._tool_forget)
        self.grok.register_tool("update_info",         self._tool_update)
        self.grok.register_tool("search_memory",       self._tool_search_memory)
        # Neue System-Tools
        self.grok.register_tool("run_terminal",        self._tool_terminal)
        self.grok.register_tool("run_script",          self._tool_run_script)
        self.grok.register_tool("read_file",           self._tool_read_file)
        self.grok.register_tool("write_file",          self._tool_write_file)
        self.grok.register_tool("read_clipboard",      self._tool_clipboard)
        self.grok.register_tool("analyze_screenshot",  self._tool_screenshot)
        # Vision & Tagesplanung
        self.grok.register_tool("analyze_camera",      self._tool_analyze_camera)
        self.grok.register_tool("add_day_task",         self._tool_add_day_task)
        self.grok.register_tool("get_day_plan",         self._tool_get_day_plan)
        self.grok.register_tool("complete_day_task",    self._tool_complete_day_task)

    def _tool_search(self, query: str) -> str:
        self._notify("system", f"Suche: {query}")
        return self.search.search(query)

    def _tool_launch(self, app_name: str) -> str:
        self._notify("system", f"Starte: {app_name}")
        return self.launcher.launch(app_name)

    def _tool_close(self, app_name: str) -> str:
        self._notify("system", f"Schließe: {app_name}")
        return self.launcher.close(app_name)

    def _tool_time(self) -> str:
        now = datetime.datetime.now()
        return now.strftime("Datum: %d.%m.%Y — Uhrzeit: %H:%M:%S")

    def _tool_open_website(self, url: str) -> str:
        from services.web.browser_control import open_website
        return open_website(url)

    def _tool_youtube_play(self, query: str) -> str:
        from services.web.browser_control import youtube_play, youtube_homepage
        if not query or query.strip() in ("", "startseite", "homepage"):
            return youtube_homepage()
        return youtube_play(query)

    def _tool_google_first_result(self, query: str) -> str:
        from services.web.browser_control import google_first_result
        return google_first_result(query)

    def _tool_set_timer(self, seconds: int, label: str = "Timer") -> str:
        def _ring():
            try:
                logger.info(f"Timer abgelaufen: '{label}'")
                import winsound
                winsound.Beep(1000, 600)          # zuverlässiger Piepton als Failsafe
            except Exception:
                pass
            try:
                self._notify("system", f"⏰ Timer abgelaufen: {label}")
                self.synthesizer.speak_async(f"{label} ist abgelaufen!")
            except Exception as e:
                logger.error(f"Timer-Ring TTS-Fehler: {e}")

        # Alte abgeschlossene Timer aufräumen
        self._active_timers = [t for t in self._active_timers if t.is_alive()]

        t = threading.Timer(float(seconds), _ring)
        t.daemon = True
        t.start()
        self._active_timers.append(t)
        logger.info(f"Timer gestartet: '{label}' in {seconds}s")

        mins = seconds // 60
        secs = seconds % 60
        min_str = f"{mins} {'Minute' if mins == 1 else 'Minuten'}"
        sec_str = f"{secs} {'Sekunde' if secs == 1 else 'Sekunden'}"
        time_str = f"{min_str} {sec_str}" if mins else sec_str
        return f"Timer gesetzt: {label} in {time_str}."

    def _tool_remember(self, info: str) -> str:
        result = self.memory.remember(info)
        self._notify("system", f"💾 {result}")
        return result

    def _tool_forget(self, info: str) -> str:
        result = self.memory.forget(info)
        self._notify("system", f"🗑 {result}")
        return result

    def _tool_update(self, search_term: str, new_info: str) -> str:
        result = self.memory.update(search_term, new_info)
        self._notify("system", f"✏️ {result}")
        return result

    def _tool_search_memory(self, query: str) -> str:
        result = self.memory.search(query)
        self._notify("system", f"🔍 Memory: {query}")
        return result

    # ── Neue System-Tool-Handler ──────────────────────────────────────────────

    def _tool_terminal(self, command: str, timeout: int = 30) -> str:
        self._notify("system", f"💻 Terminal: {command}")
        from services.tools.system_tools import run_terminal
        result = run_terminal(command, timeout=min(timeout, 120))
        logger.info(f"Terminal '{command}' → {len(result)} Zeichen")
        return result

    def _tool_run_script(self, script_path: str, args: str = "") -> str:
        name = Path(script_path).name
        self._notify("system", f"📜 Skript: {name}")
        from services.tools.system_tools import run_script
        result = run_script(script_path, args)
        logger.info(f"Skript '{name}' → {len(result)} Zeichen")
        return result

    def _tool_read_file(self, path: str) -> str:
        name = Path(path).name
        self._notify("system", f"📂 Lese Datei: {name}")
        from services.tools.system_tools import read_file
        result = read_file(path)
        logger.info(f"Datei gelesen: '{path}' → {len(result)} Zeichen")
        return result

    def _tool_write_file(self, path: str, content: str, append: bool = False) -> str:
        name   = Path(path).name
        action = "anhängen" if append else "schreiben"
        self._notify("system", f"💾 Datei {action}: {name}")
        from services.tools.system_tools import write_file
        result = write_file(path, content, append=append)
        logger.info(f"Datei {action}: '{path}'")
        return result

    def _tool_clipboard(self) -> str:
        self._notify("system", "📋 Lese Zwischenablage...")
        from services.tools.system_tools import read_clipboard
        result = read_clipboard()
        logger.info(f"Clipboard: {len(result)} Zeichen")
        return result

    def _tool_screenshot(self, question: str = "") -> str:
        self._notify("system", "📸 Screenshot wird analysiert...")
        from services.tools.system_tools import analyze_screenshot
        result = analyze_screenshot(question)
        logger.info(f"Screenshot analysiert: {len(result)} Zeichen")
        return result

    # ── Vision & Tagesplanung ─────────────────────────────────────────────────

    def _tool_analyze_camera(self, question: str = "") -> str:
        """Nimmt ein Kamera-Foto auf und analysiert es mit Vision-KI."""
        if not self.settings.vision_enabled:
            return "Vision-Feature ist deaktiviert. Bitte in den Einstellungen aktivieren."
        self._notify("system", "📷 Kamera-Aufnahme...")
        b64 = self.camera.capture_base64()
        if not b64:
            return "Kamera-Aufnahme fehlgeschlagen. Bitte Kamera pruefen."
        if not question:
            question = "Was siehst du auf diesem Bild? Beschreibe den Inhalt praezise auf Deutsch."
        self._notify("system", "🔍 Bild wird analysiert...")
        result = self.vision.analyze(b64, question)
        logger.info(f"Kamera analysiert: {len(result)} Zeichen")
        return result

    def _tool_add_day_task(self, task: str, priority: str = "normal", time_hint: str = "") -> str:
        """Fuegt eine Aufgabe zum Tagesplan hinzu."""
        result = self.day_planner.add_task(task, priority, time_hint)
        self._notify("system", f"📋 {result}")
        return result

    def _tool_get_day_plan(self) -> str:
        """Gibt den aktuellen Tagesplan zurueck."""
        return self.day_planner.get_plan()

    def _tool_complete_day_task(self, task_id_or_name: str) -> str:
        """Markiert eine Aufgabe als erledigt."""
        result = self.day_planner.complete_task(task_id_or_name)
        self._notify("system", f"✅ {result}")
        return result

    # ── Neue Feature-Setter ───────────────────────────────────────────────────

    def set_personality(self, preset: str, custom_text: str = ""):
        """
        Setzt das Persoenlichkeitsprofil.
        preset: 'assistant' | 'friend' | 'butler' | 'coach' | 'scientist' | 'custom'
        """
        self.grok.set_personality(preset, custom_text)
        self.settings.personality = preset
        self.settings.personality_custom = custom_text
        self.settings.save()
        labels = {
            "assistant": "Assistent",
            "friend":    "Freund",
            "butler":    "Butler",
            "coach":     "Coach",
            "scientist": "Wissenschaftler",
            "custom":    "Eigene",
        }
        self._notify("system", f"🎭 Persoenlichkeit: {labels.get(preset, preset)}")

    def set_mood_based_responses(self, enabled: bool):
        """Schaltet mood-basierte Antworten ein/aus."""
        self.settings.mood_based_responses = enabled
        self.settings.save()
        self._fire_settings_changed()
        self._notify("system", f"😊 Mood-Antworten: {'AN' if enabled else 'AUS'}")

    def set_proactive_suggestions(self, enabled: bool):
        """Schaltet den proaktiven Vorschlags-Engine ein/aus."""
        self.settings.proactive_suggestions = enabled
        self.settings.save()
        self._fire_settings_changed()
        if enabled:
            self._proactive_engine.start()
            self._notify("system", "💡 Proaktive Vorschlaege AN")
        else:
            self._proactive_engine.stop()
            self._notify("system", "💡 Proaktive Vorschlaege AUS")

    def set_day_planning(self, enabled: bool):
        """Schaltet Tagesplanung ein/aus."""
        self.settings.day_planning = enabled
        self.settings.save()
        self._fire_settings_changed()
        if enabled:
            count = self.day_planner.get_open_count()
            self._notify("system", f"📋 Tagesplanung AN ({count} offene Aufgaben)")
        else:
            self._notify("system", "📋 Tagesplanung AUS")

    def set_vision_enabled(self, enabled: bool):
        """Schaltet das Vision/Kamera-Feature ein/aus."""
        self.settings.vision_enabled = enabled
        self.settings.save()
        self._fire_settings_changed()
        if enabled:
            available = self.camera.is_available()
            status = "Kamera verfuegbar" if available else "Keine Kamera gefunden"
            self._notify("system", f"📷 Vision AN — {status}")
        else:
            self._notify("system", "📷 Vision AUS")
