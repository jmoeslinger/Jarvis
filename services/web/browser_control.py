"""
Browser-Steuerung für Jarvis.
Öffnet URLs immer im Standard-Browser des Systems (Opera GX, Chrome, etc.)
"""

import logging
import re
import webbrowser
from typing import Optional
from urllib.parse import quote_plus

logger = logging.getLogger("jarvis.browser")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}


def _open(url: str) -> None:
    """Öffnet URL immer im Standard-Browser des Betriebssystems."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    webbrowser.open(url)
    logger.info(f"Browser geöffnet: {url}")


def youtube_play(query: str) -> str:
    """Sucht auf YouTube und öffnet das erste Ergebnis direkt."""
    try:
        import requests
        search_url = f"https://www.youtube.com/results?search_query={quote_plus(query)}"
        resp = requests.get(search_url, headers=_HEADERS, timeout=10)

        # YouTube bettet Video-IDs als JSON ein
        video_ids = re.findall(r'"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"', resp.text)

        # Duplikate entfernen, Reihenfolge beibehalten
        seen: set = set()
        unique = [v for v in video_ids if v not in seen and not seen.add(v)]  # type: ignore

        if unique:
            url = f"https://www.youtube.com/watch?v={unique[0]}"
            _open(url)
            logger.info(f"YouTube Video: {url}")
            return f"YouTube spielt jetzt das erste Ergebnis für '{query}'."

    except Exception as e:
        logger.warning(f"YouTube-Videosuche fehlgeschlagen: {e}")

    # Fallback: Suchergebnisseite öffnen
    fallback = f"https://www.youtube.com/results?search_query={quote_plus(query)}"
    _open(fallback)
    return f"YouTube-Suche für '{query}' geöffnet."


def youtube_homepage() -> str:
    """Öffnet YouTube-Startseite und spielt erstes empfohlenes Video."""
    try:
        import requests
        resp = requests.get("https://www.youtube.com/", headers=_HEADERS, timeout=10)
        video_ids = re.findall(r'"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"', resp.text)

        seen: set = set()
        unique = [v for v in video_ids if v not in seen and not seen.add(v)]  # type: ignore

        if unique:
            url = f"https://www.youtube.com/watch?v={unique[0]}"
            _open(url)
            return "YouTube erstes empfohlenes Video wird abgespielt."

    except Exception as e:
        logger.warning(f"YouTube-Startseite fehlgeschlagen: {e}")

    _open("https://www.youtube.com/")
    return "YouTube wurde geöffnet."


def google_first_result(query: str) -> str:
    """Öffnet das erste echte Google-Suchergebnis (kein Werbung)."""
    try:
        import requests
        search_url = f"https://www.google.com/search?q={quote_plus(query)}&hl=de"
        resp = requests.get(search_url, headers=_HEADERS, timeout=10)

        # Google-Weiterleitungs-URLs extrahieren
        redirects = re.findall(r'/url\?q=(https?://[^&"<>\s]+)', resp.text)
        # Werbung und Google-interne Links herausfiltern
        clean = [
            u for u in redirects
            if "google.com" not in u
            and "google.de" not in u
            and not u.startswith("https://accounts.")
        ]

        if clean:
            target = clean[0]
            _open(target)
            logger.info(f"Erstes Google-Ergebnis: {target}")
            return f"Erstes Ergebnis für '{query}' geöffnet."

    except Exception as e:
        logger.warning(f"Google-Ergebnis-Extraktion fehlgeschlagen: {e}")

    # Fallback: Suchergebnisseite öffnen
    _open(f"https://www.google.com/search?q={quote_plus(query)}")
    return f"Google-Suche für '{query}' geöffnet."


def open_website(url: str) -> str:
    """Öffnet eine beliebige Website im Standard-Browser."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    _open(url)
    return f"Website geöffnet: {url}"
