"""
Lokaler AI-Client via Ollama.
Ollama stellt eine OpenAI-kompatible API bereit → selbe Logik wie GrokClient.
Voraussetzung: Ollama läuft auf localhost:11434 (https://ollama.ai)
"""

import logging
import requests

from services.ai.grok_client import GrokClient

logger = logging.getLogger("jarvis.ollama")

_DEFAULT_URL   = "http://localhost:11434/v1"
_DEFAULT_MODEL = "llama3.2:3b"

# Kürzerer Prompt für lokale Modelle (weniger leistungsfähig, weniger Kontext)
_LOCAL_SYSTEM_PROMPT = """\
Du bist Jarvis, ein KI-Assistent für Spracheingabe. Antworte kurz, direkt, auf Deutsch. Kein Markdown.

Tools nutzen wenn passend:
- get_time(): Uhrzeit/Datum
- search_internet(): aktuelle Infos, Fakten nach 2024
- launch_app()/close_app(): Apps starten/schließen
- set_timer(seconds, label): Timer
- remember_info(): persönliche Infos automatisch speichern (Name, Alter, Vorlieben...)
- update_info(stichwort, neu): Korrekturen speichern
- forget_info(): Info löschen
- search_memory(): nachschlagen was du weißt
Gespeicherte Infos aktiv nutzen.\
"""


class OllamaClient(GrokClient):
    """
    Lokaler AI-Client — erbt die gesamte GrokClient-Logik,
    zeigt aber auf Ollama statt Groq/Grok.
    """
    name = "Ollama (Lokal)"

    def __init__(self, base_url: str = _DEFAULT_URL,
                 model: str = _DEFAULT_MODEL,
                 get_memories=None):
        from openai import OpenAI
        # GrokClient.__init__ nicht aufrufen — direkt initialisieren
        # BUG-025: alle GrokClient-Attribute explizit setzen um AttributeError zu vermeiden
        import threading as _threading
        self._client            = OpenAI(api_key="ollama", base_url=base_url)
        self._model             = model
        self._history           = []
        # BUG-NEW-6: OllamaClient überspringt GrokClient.__init__ und initialisiert
        # alle Attribute manuell. _history_lock fehlte → _trim_history() und
        # clear_history() werfen AttributeError wenn OllamaClient aktiv ist.
        self._history_lock      = _threading.Lock()
        self._tools             = {}
        self._get_memories      = get_memories
        self._current_query     = ""
        self._MAX_HISTORY       = 6        # lokale Modelle: kleineres Kontextfenster
        self._length_hint       = ""
        self._tone_hint         = ""
        self._multi_step        = False
        self._task_planning     = False
        self._parallel_tasks    = False
        self._base_url          = base_url
        # Weitere GrokClient-Attribute die via set_*() gesetzt werden koennen
        self._personality       = "assistant"
        self._personality_custom = ""
        self._context_provider  = None    # BUG-025: fehlt sonst in _build_system_prompt
        self._adaptive_max_tokens = None   # BUG-069: war False (bool), muss None (Optional[int]) sein
        self._noise_filter      = False
        self._long_term_context = False
        self._style_learner     = None
        self._adaptive_responses = False
        logger.info(f"Ollama-Client initialisiert: {base_url} / {model}")

    # Persoenlichkeits-Hinweise identisch zu GrokClient
    _PERSONALITY_HINTS = {
        "assistant":  "",
        "friend":     "Verhalte dich wie ein guter Freund: locker, persoenlich, humorvoll, du-Form. Manchmal Witze oder Anekdoten.",
        "butler":     "Verhalte dich wie ein hoeflicher englischer Butler: formell, diskret, zuvorkommend, Sie-Form. Elegante Sprache.",
        "coach":      "Verhalte dich wie ein motivierender Coach: ermutigend, loesung-orientiert, direkt, du-Form. Fokus auf Wachstum.",
        "scientist":  "Verhalte dich wie ein neugieriger Wissenschaftler: praezise, faktenbasiert, analytisch. Fachbegriffe erklaeren.",
    }

    def _build_system_prompt(self) -> str:
        """Baut den System-Prompt mit allen aktiven Modus-Hinweisen.
        BUG-092: _context_provider fehlte — Langzeit-Kontext nie in Ollama-Prompt eingebettet.
        BUG-096: _personality fehlte — Persoenlichkeitsprofil hatte keinen Effekt auf Ollama.
        """
        # BUG-096: Persoenlichkeit voranstellen (identisch zu GrokClient)
        personality_hint = ""
        if self._personality == "custom" and self._personality_custom:
            personality_hint = f"PERSOENLICHKEIT: {self._personality_custom}"
        elif self._personality in self._PERSONALITY_HINTS:
            personality_hint = self._PERSONALITY_HINTS[self._personality]

        if personality_hint:
            prompt = f"PERSOENLICHKEIT & VERHALTEN: {personality_hint}\n\n{_LOCAL_SYSTEM_PROMPT}"
        else:
            prompt = _LOCAL_SYSTEM_PROMPT
        if self._length_hint:
            prompt += f"\n\nANTWORTLÄNGE: {self._length_hint}"
        if self._tone_hint:
            prompt += f"\n\nTON & STIL: {self._tone_hint}"
        if self._multi_step:
            prompt += (
                "\n\nREASONING-MODUS: Denke bei komplexen Fragen Schritt für Schritt. "
                "Nummeriere die Schritte."
            )
        if self._task_planning:
            prompt += (
                "\n\nAUFGABEN-PLANUNG: Erstelle bei komplexen Aufgaben zuerst einen Plan, "
                "dann führe ihn aus."
            )
        if self._parallel_tasks:
            prompt += (
                "\n\nPARALLEL-MODUS: Wenn mehrere unabhängige Aufgaben genannt werden, "
                "erkenne alle und rufe die nötigen Tools auf."
            )
        if self._get_memories:
            mem = self._get_memories(self._current_query)
            if mem:
                prompt += mem
        # BUG-092: Langzeit-Kontext einbetten (identisch zu GrokClient)
        if self._context_provider:
            try:
                ctx = self._context_provider()
                if ctx:
                    prompt += ctx
            except Exception:
                pass
        return prompt

    # ── Verfügbarkeits-Check ───────────────────────────────────────────────────

    def _tags_url(self) -> str:
        """Leitet die /api/tags URL aus der konfigurierten base_url ab."""
        # base_url endet typisch auf "/v1" → eine Ebene höher + /api/tags
        base = self._base_url.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        return base + "/api/tags"

    def is_available(self) -> bool:
        """Gibt True zurück wenn Ollama läuft."""
        try:
            r = requests.get(self._tags_url(), timeout=2)
            return r.status_code == 200
        except Exception:
            return False

    def list_models(self) -> list[str]:
        """Gibt alle lokal verfügbaren Modelle zurück."""
        try:
            r = requests.get(self._tags_url(), timeout=3)
            data = r.json()
            return [m["name"] for m in data.get("models", [])]
        except Exception:
            return []
