"""
AI Router — verwaltet mehrere Provider und wählt automatisch den besten.

Modi:
  auto   — Online zuerst, bei Fehler/Rate-Limit automatisch auf Lokal wechseln
  online — Nur Online-Provider (Groq/Grok)
  local  — Nur lokaler Provider (Ollama)

Fallback-Reihenfolge (auto):
  1. Groq / Grok  (schnell, kostenlos, Cloud)
  2. Ollama        (lokal, immer verfügbar, kein Limit)
"""

import logging
import time
from typing import Callable, Dict, List, Optional, Tuple, Any

from services.ai.exceptions import RateLimitError, ProviderError

logger = logging.getLogger("jarvis.router")


class RouterClient:
    """
    Transparenter Wrapper um mehrere AI-Clients.
    Stellt dieselbe Schnittstelle wie GrokClient bereit.
    """

    def __init__(
        self,
        providers: List[Tuple[str, Any]],   # [(name, client), ...]
        mode: str = "auto",
        on_switch: Optional[Callable[[str, str], None]] = None,
    ):
        self._providers  = providers          # [(name, client), ...]
        self._mode       = mode               # "auto" | "online" | "local"
        self._on_switch  = on_switch          # callback(old_name, new_name)
        self._active_idx = 0
        self._cooldowns: Dict[str, float] = {}   # name → epoch when available again
        self._history: List[Dict[str, Any]] = []
        self._tools: Dict[str, Callable] = {}

        logger.info(
            f"RouterClient: {[n for n, _ in providers]} | Modus: {mode}"
        )
        self._select_initial()

    # ── Initialisierung ───────────────────────────────────────────────────────

    def _select_initial(self):
        """Setzt den ersten verfügbaren Provider als aktiv."""
        for i, (name, client) in enumerate(self._providers):
            if self._provider_allowed(name) and self._is_client_ready(client):
                self._active_idx = i
                logger.info(f"Aktiver AI-Provider: {name}")
                return
        logger.warning("Kein verfügbarer AI-Provider beim Start gefunden.")

    def _provider_allowed(self, name: str) -> bool:
        """Prüft ob der Provider im aktuellen Modus erlaubt ist."""
        if self._mode == "online":
            return "lokal" not in name.lower() and "ollama" not in name.lower()
        if self._mode == "local":
            return "lokal" in name.lower() or "ollama" in name.lower()
        return True   # auto: alle erlaubt

    @staticmethod
    def _is_client_ready(client) -> bool:
        """Gibt False zurück wenn der Client offensichtlich nicht verfügbar ist."""
        if hasattr(client, "is_available"):
            return client.is_available()
        return True   # Online-Provider: immer als verfügbar annehmen

    # ── Öffentliche Eigenschaften ─────────────────────────────────────────────

    @property
    def active_name(self) -> str:
        return self._providers[self._active_idx][0]

    @property
    def _model(self) -> str:
        return self._providers[self._active_idx][1]._model

    @_model.setter
    def _model(self, value: str):
        self._providers[self._active_idx][1]._model = value

    @property
    def history(self) -> List[Dict[str, Any]]:
        return list(self._history)

    def set_mode(self, mode: str):
        """Wechselt den Routing-Modus und wählt passenden Provider."""
        self._mode = mode
        self._select_initial()
        logger.info(f"Routing-Modus: {mode}")

    def force_provider(self, name: str) -> bool:
        """Erzwingt einen bestimmten Provider. Gibt True zurück wenn gefunden."""
        for i, (n, _) in enumerate(self._providers):
            if n == name:
                old = self.active_name
                self._active_idx = i
                self._cooldowns.pop(name, None)   # Cooldown aufheben
                if old != name and self._on_switch:
                    self._on_switch(old, name)
                logger.info(f"Provider manuell gewechselt: {old} → {name}")
                return True
        return False

    # ── Tools ─────────────────────────────────────────────────────────────────

    def register_tool(self, name: str, handler: Callable):
        self._tools[name] = handler
        for _, client in self._providers:
            client.register_tool(name, handler)

    def set_length_hint(self, hint: str):
        """Setzt Antwortlänge-Hinweis für alle Provider."""
        for _, client in self._providers:
            if hasattr(client, "set_length_hint"):
                client.set_length_hint(hint)

    def set_tone(self, hint: str):
        """Setzt Ton/Stil-Hinweis für alle Provider."""
        for _, client in self._providers:
            if hasattr(client, "set_tone"):
                client.set_tone(hint)

    def set_multi_step(self, enabled: bool):
        """Aktiviert/deaktiviert Schritt-für-Schritt Reasoning bei allen Providern."""
        for _, client in self._providers:
            if hasattr(client, "set_multi_step"):
                client.set_multi_step(enabled)

    def set_task_planning(self, enabled: bool):
        """Aktiviert/deaktiviert Aufgaben-Planung bei allen Providern."""
        for _, client in self._providers:
            if hasattr(client, "set_task_planning"):
                client.set_task_planning(enabled)

    def set_parallel_tasks(self, enabled: bool):
        """Aktiviert/deaktiviert parallele Tool-Ausführung bei allen Providern."""
        for _, client in self._providers:
            if hasattr(client, "set_parallel_tasks"):
                client.set_parallel_tasks(enabled)

    def set_context_provider(self, provider):
        """Setzt den Langzeit-Kontext-Provider für alle Provider."""
        for _, client in self._providers:
            if hasattr(client, "set_context_provider"):
                client.set_context_provider(provider)

    def set_adaptive_max_tokens(self, max_tokens):
        """Setzt das Token-Limit (One-Shot) für alle Provider."""
        for _, client in self._providers:
            if hasattr(client, "set_adaptive_max_tokens"):
                client.set_adaptive_max_tokens(max_tokens)

    def set_personality(self, preset: str, custom_text: str = ""):
        """Setzt das Persoenlichkeitsprofil fuer alle Provider."""
        for _, client in self._providers:
            if hasattr(client, "set_personality"):
                client.set_personality(preset, custom_text)

    def get_last_response(self) -> str:
        """Gibt die letzte Assistenten-Antwort aus der History zurück."""
        for msg in reversed(self._history):
            if msg.get("role") == "assistant":
                content = msg.get("content", "")
                if isinstance(content, str) and content.strip():
                    return content
        return ""

    def clear_history(self):
        self._history.clear()
        for _, client in self._providers:
            client.clear_history()
        logger.info("Gesprächsverlauf (alle Provider) gelöscht.")

    # ── Routing-Kern ──────────────────────────────────────────────────────────

    def _available_providers(self) -> List[Tuple[int, str, Any]]:
        """
        Gibt verfügbare Provider in Prioritätsreihenfolge zurück.
        Aktiver Provider immer zuerst, dann die anderen.
        """
        now      = time.monotonic()
        ordered  = (
            [(self._active_idx, *self._providers[self._active_idx])]
            + [(i, n, c) for i, (n, c) in enumerate(self._providers)
               if i != self._active_idx]
        )
        return [
            (i, n, c) for i, n, c in ordered
            if self._provider_allowed(n) and self._cooldowns.get(n, 0) <= now
        ]

    def _set_cooldown(self, name: str, seconds: float):
        self._cooldowns[name] = time.monotonic() + seconds
        logger.warning(f"Provider '{name}' für {seconds/60:.1f} Min. pausiert.")

    def _switch_active(self, new_idx: int):
        old_name = self.active_name
        self._active_idx = new_idx
        new_name = self.active_name
        if old_name != new_name:
            logger.info(f"Wechsel: {old_name} → {new_name}")
            if self._on_switch:
                try:
                    self._on_switch(old_name, new_name)
                except Exception:
                    pass

    def _sync_to(self, client):
        """Setzt die History des Clients auf den aktuellen Router-Stand."""
        client._history = list(self._history)

    def _sync_from(self, client):
        """Übernimmt die History nach einem erfolgreichen Call."""
        self._history = list(client._history)

    # ── Chat-Methoden (gleiche API wie GrokClient) ────────────────────────────

    def chat_streaming(self, user_message: str, on_chunk: Callable[[str], None]) -> str:
        providers = self._available_providers()
        if not providers:
            # Alle auf Cooldown → Cooldowns löschen, nochmal versuchen
            logger.warning("Alle Provider auf Cooldown — setze zurück.")
            self._cooldowns.clear()
            providers = self._available_providers()

        last_msg = ""
        for i, name, client in providers:
            self._sync_to(client)
            try:
                result = client.chat_streaming(user_message, on_chunk)
                self._sync_from(client)
                self._switch_active(i)
                return result

            except RateLimitError as e:
                logger.warning(f"Rate-Limit bei '{name}' ({e.wait_seconds:.0f}s Pause).")
                self._set_cooldown(name, e.wait_seconds)
                # Nächsten Provider versuchen — on_chunk noch nicht aufgerufen

            except Exception as e:
                logger.warning(f"Fehler bei '{name}': {e}")
                self._set_cooldown(name, 30)

        # Alle fehlgeschlagen
        last_msg = "Kein KI-Provider verfügbar. Bitte Internetverbindung und Ollama prüfen."
        on_chunk(last_msg)
        return last_msg

    def chat(self, user_message: str) -> str:
        providers = self._available_providers()
        if not providers:
            self._cooldowns.clear()
            providers = self._available_providers()

        for i, name, client in providers:
            self._sync_to(client)
            try:
                result = client.chat(user_message)
                self._sync_from(client)
                self._switch_active(i)
                return result

            except RateLimitError as e:
                self._set_cooldown(name, e.wait_seconds)

            except Exception as e:
                logger.warning(f"Fehler bei '{name}': {e}")
                self._set_cooldown(name, 30)

        return "Kein KI-Provider verfügbar. Bitte Internetverbindung und Ollama prüfen."

    # ── Zusatz-Info ───────────────────────────────────────────────────────────

    def provider_status(self) -> List[Dict]:
        """Gibt Status aller Provider zurück (für UI)."""
        now = time.monotonic()
        result = []
        for i, (name, client) in enumerate(self._providers):
            cd = self._cooldowns.get(name, 0)
            ready = cd <= now
            available = self._is_client_ready(client)
            result.append({
                "name":      name,
                "active":    i == self._active_idx,
                "ready":     ready and available,
                "cooldown_s": max(0, cd - now),
                "allowed":   self._provider_allowed(name),
            })
        return result
