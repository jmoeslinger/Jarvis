import concurrent.futures
import logging
from typing import List, Dict

import requests
from bs4 import BeautifulSoup
try:
    from ddgs import DDGS          # neues Paket (ddgs)
except ImportError:
    from duckduckgo_search import DDGS  # Fallback altes Paket

logger = logging.getLogger("jarvis.search")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


class WebSearchService:
    def search(self, query: str, max_results: int = 5) -> str:
        logger.info(f"Suche: '{query}'")
        try:
            results = self._ddg_search(query, max_results)
            if not results:
                return "Keine Suchergebnisse gefunden."
            return self._format_results(query, results)
        except Exception as e:
            logger.error(f"Suchfehler: {e}")
            return f"Suche fehlgeschlagen: {e}"

    def _ddg_search(self, query: str, max_results: int) -> List[Dict]:
        """Sucht via DuckDuckGo. Timeout nach 10 Sekunden um Haenger zu vermeiden."""
        def _run():
            with DDGS() as ddgs:
                return list(ddgs.text(query, max_results=max_results, region="de-de"))

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_run)
            try:
                return future.result(timeout=10)
            except concurrent.futures.TimeoutError:
                logger.warning(f"Suche Timeout (10s): '{query}'")
                return []

    def _format_results(self, query: str, results: List[Dict]) -> str:
        lines = [f"Suchergebnisse fuer '{query}':\n"]
        for i, r in enumerate(results, 1):
            title = r.get("title", "Kein Titel")
            body = r.get("body", "")
            href = r.get("href", "")
            lines.append(f"{i}. {title}")
            if body:
                lines.append(f"   {body[:300]}")
            if href:
                lines.append(f"   Quelle: {href}")
            lines.append("")
        return "\n".join(lines).strip()

    def fetch_page(self, url: str, max_chars: int = 3000) -> str:
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=10)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                tag.decompose()
            text = " ".join(soup.get_text(separator=" ").split())
            return text[:max_chars] + ("..." if len(text) > max_chars else "")
        except Exception as e:
            logger.error(f"Seite nicht abrufbar ({url}): {e}")
            return f"Fehler beim Laden: {e}"
