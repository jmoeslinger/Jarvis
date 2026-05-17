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
        self._client        = OpenAI(api_key="ollama", base_url=base_url)
        self._model         = model
        self._history       = []
        self._tools         = {}
        self._get_memories  = get_memories
        self._current_query = ""
        self._MAX_HISTORY   = 6   # lokale Modelle haben kleineres Kontextfenster
        self._length_hint   = ""
        self._tone_hint     = ""
        self._multi_step    = False
        self._task_planning = False
        self._parallel_tasks = False
        self._base_url      = base_url
        logger.info(f"Ollama-Client initialisiert: {base_url} / {model}")

    def _build_system_prompt(self) -> str:
        """Baut den System-Prompt mit allen aktiven Modus-Hinweisen."""
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
