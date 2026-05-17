import json
import logging
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from openai import OpenAI

from services.ai.exceptions import RateLimitError

logger = logging.getLogger("jarvis.grok")

_SYSTEM_PROMPT = """\
Du bist Jarvis, ein persoenlicher KI-Assistent fuer Spracheingabe. Antworte kurz, direkt, auf Deutsch. Kein Markdown.

TOOLS — wann nutzen:
- get_time(): bei jeder Uhrzeit-/Datumsfrage. NIE aus dem Gedaechtnis antworten.
- search_internet(): bei aktuellen Infos, Wetter, Nachrichten, Fakten, allem nach 2024. Nie "weiss nicht" sagen.
- launch_app()/close_app(): App oeffnen/schliessen.
- open_website(), youtube_play(), google_first_result(): Browser/YouTube steuern.
- set_timer(seconds, label): Timer. "5 Minuten" → seconds=300.

SYSTEM-TOOLS — fuer Aufgaben am Computer:
- run_terminal(command): Windows-Befehle ausfuehren (ipconfig, tasklist, dir, etc.).
- run_script(script_path): Python/Batch/PowerShell-Skript starten.
- read_file(path): Dateiinhalt lesen (nur im Nutzer-Verzeichnis).
- write_file(path, content, append): Text in Datei schreiben/anhaengen.
- read_clipboard(): Zwischenablage-Inhalt lesen — nutze dies wenn Nutzer sagt "ich habe etwas kopiert" oder "was ist in meiner Zwischenablage".
- analyze_screenshot(question): Aktuellen Bildschirm analysieren — nutze dies wenn Nutzer fragt "was siehst du" oder "was ist auf meinem Bildschirm".

GEDAECHTNIS — automatisch nutzen:
- remember_info(): Sofort speichern wenn Nutzer Name, Alter, Wohnort, Beruf, Vorlieben, Familie erwaehnt — auch ohne "merk dir".
- update_info(stichwort, neu): Bei Korrekturen ("nein ich heisse...", "stimmt nicht mehr", "eigentlich...").
- forget_info(): Bei "vergiss das".
- search_memory("Nutzer"): Bei "was weisst du ueber mich".
Gespeicherte Infos aktiv nutzen: Namen gelegentlich verwenden, Vorlieben in Empfehlungen einbauen. Dezent, natuerlich.\
"""

_TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_internet",
            "description": "Durchsucht das Internet nach aktuellen Informationen.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Die Suchanfrage auf Deutsch oder Englisch.",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "launch_app",
            "description": "Startet eine Windows-Anwendung oder ein Programm.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {
                        "type": "string",
                        "description": "Name der Anwendung, z.B. 'Chrome', 'Notepad', 'Spotify'.",
                    }
                },
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "close_app",
            "description": "Schliesst/beendet eine laufende Windows-Anwendung.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {
                        "type": "string",
                        "description": "Name der zu schließenden Anwendung, z.B. 'Chrome', 'Discord', 'Spotify'.",
                    }
                },
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "Gibt das aktuelle Datum und die aktuelle Uhrzeit zurueck.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_website",
            "description": "Öffnet eine Webseite im Standard-Browser.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Die vollständige URL der Webseite, z.B. 'https://www.google.com'.",
                    }
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember_info",
            "description": "Speichert eine Information dauerhaft. Nutze dies wenn der Nutzer sagt 'merk dir', 'speichere', 'vergiss nicht', 'denk daran'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "info": {
                        "type": "string",
                        "description": "Die zu merkende Information, z.B. 'Nutzer heißt Max', 'Nutzer mag Kaffee ohne Zucker'.",
                    }
                },
                "required": ["info"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "forget_info",
            "description": "Löscht eine gespeicherte Information. Nutze dies wenn der Nutzer sagt 'vergiss das', 'vergiss dass', 'das stimmt nicht mehr'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "info": {
                        "type": "string",
                        "description": "Die zu löschende Information oder ein Stichwort dazu.",
                    }
                },
                "required": ["info"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "youtube_play",
            "description": "Sucht auf YouTube und öffnet das erste Video sofort im Standard-Browser. Bei leerem query wird die YouTube-Startseite geöffnet.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Suchbegriff für YouTube, z.B. 'Billie Eilish', 'Lo-Fi Hip Hop'. Leer lassen für YouTube-Startseite.",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "google_first_result",
            "description": "Öffnet das erste echte Google-Suchergebnis (kein Werbung) im Standard-Browser.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Suchanfrage für Google.",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_timer",
            "description": "Setzt einen Timer der nach einer bestimmten Zeit klingelt.",
            "parameters": {
                "type": "object",
                "properties": {
                    "seconds": {
                        "type": "integer",
                        "description": "Anzahl der Sekunden bis der Timer abläuft.",
                    },
                    "label": {
                        "type": "string",
                        "description": "Bezeichnung des Timers, z.B. 'Nudeln', 'Meeting'.",
                    },
                },
                "required": ["seconds"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_info",
            "description": "Korrigiert oder aktualisiert eine bereits gespeicherte Information. Nutze dies wenn der Nutzer eine frühere Aussage korrigiert (z.B. 'ich heiße jetzt Felix', 'ich wohne jetzt in München').",
            "parameters": {
                "type": "object",
                "properties": {
                    "search_term": {
                        "type": "string",
                        "description": "Stichwort des zu aktualisierenden Eintrags, z.B. 'heißt', 'wohnt'.",
                    },
                    "new_info": {
                        "type": "string",
                        "description": "Der neue, korrekte Inhalt, z.B. 'Nutzer heißt Felix'.",
                    },
                },
                "required": ["search_term", "new_info"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_memory",
            "description": "Sucht in den gespeicherten Erinnerungen. Nutze dies wenn der Nutzer fragt was du über ihn weißt, oder nach einer gespeicherten Info sucht.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Suchbegriff, z.B. 'Name', 'Hobby', 'Nutzer'.",
                    }
                },
                "required": ["query"],
            },
        },
    },
    # ── System-Tools ──────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "run_terminal",
            "description": (
                "Führt einen Windows-Befehl in der Eingabeaufforderung (cmd) aus "
                "und gibt die Ausgabe zurück. Nützlich für Systeminformationen, "
                "Prozesse, Netzwerk, Verzeichnisse, etc. "
                "Beispiele: 'ipconfig', 'tasklist', 'dir C:\\Users', 'ping google.com -n 2'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Der Shell-Befehl, z.B. 'ipconfig /all' oder 'tasklist | findstr python'.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Maximale Wartezeit in Sekunden (Standard: 30, Maximum: 120).",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_script",
            "description": (
                "Führt ein Skript-File aus (.py, .bat, .cmd oder .ps1). "
                "Der vollständige Pfad zur Skript-Datei ist erforderlich."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "script_path": {
                        "type": "string",
                        "description": "Vollständiger Pfad zum Skript, z.B. 'C:\\Users\\Max\\scripts\\backup.py'.",
                    },
                    "args": {
                        "type": "string",
                        "description": "Optionale Kommandozeilen-Argumente für das Skript.",
                    },
                },
                "required": ["script_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Liest den Inhalt einer Textdatei. "
                "Nur Dateien im Nutzer-Verzeichnis (C:\\Users\\...) sind zugänglich. "
                "Nützlich für Notizen, Konfigurationen, Log-Dateien, etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Vollständiger Dateipfad, z.B. 'C:\\Users\\Max\\Desktop\\notizen.txt'.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Schreibt Text in eine Datei oder hängt ihn an. "
                "Nur im Nutzer-Verzeichnis erlaubt. "
                "Die Datei wird erstellt falls sie nicht existiert."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Vollständiger Dateipfad, z.B. 'C:\\Users\\Max\\Desktop\\notizen.txt'.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Der zu schreibende Text-Inhalt.",
                    },
                    "append": {
                        "type": "boolean",
                        "description": "True = Inhalt anhängen, False = Datei überschreiben (Standard: False).",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_clipboard",
            "description": (
                "Liest den aktuellen Inhalt der Windows-Zwischenablage. "
                "Nutze dies wenn der Nutzer sagt 'ich habe etwas kopiert', "
                "'was ist in meiner Zwischenablage' oder 'nutze meinen kopierten Text'."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_screenshot",
            "description": (
                "Macht einen Screenshot des aktuellen Bildschirms und analysiert dessen Inhalt via OCR. "
                "Nutze dies wenn der Nutzer fragt 'was siehst du auf meinem Bildschirm', "
                "'was ist geöffnet', 'lies was da steht' oder 'analysiere meinen Bildschirm'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "Spezifische Frage zum Bildschirminhalt, z.B. 'Was steht in der Fehlermeldung?'",
                    },
                },
                "required": [],
            },
        },
    },
]


_BASE_URLS = {
    "grok": "https://api.x.ai/v1",
    "groq": "https://api.groq.com/openai/v1",
}


def _detect_provider(api_key: str) -> str:
    if api_key.startswith("xai-"):
        return "grok"
    if api_key.startswith("gsk_"):
        return "groq"
    return "groq"


class GrokClient:
    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile",
                 provider: str = "", get_memories: Callable = None):
        if not provider:
            provider = _detect_provider(api_key)
        base_url = _BASE_URLS.get(provider, _BASE_URLS["groq"])
        logger.info(f"API-Provider: {provider} ({base_url})")
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self._history: List[Dict[str, Any]] = []
        self._tools: Dict[str, Callable] = {}
        self._get_memories = get_memories   # callable → memory string
        self._current_query = ""            # aktuelle Nutzerfrage für Memory-Kontext
        self._MAX_HISTORY = 10              # max. Nachrichten im Verlauf (Token sparen)
        self._length_hint: str = ""         # Antwortlänge-Steuerung
        self._tone_hint: str = ""           # Ton-/Stil-Steuerung
        self._multi_step: bool = False      # Schritt-für-Schritt Reasoning
        self._task_planning: bool = False   # Aufgaben in Schritte zerlegen
        self._parallel_tasks: bool = False  # Tools parallel ausführen

    def set_length_hint(self, hint: str):
        """Setzt den Längenhinweis für zukünftige Antworten."""
        self._length_hint = hint

    def set_tone(self, hint: str):
        """Setzt den Ton-/Stilhinweis für zukünftige Antworten."""
        self._tone_hint = hint

    def set_multi_step(self, enabled: bool):
        """Aktiviert/deaktiviert Schritt-für-Schritt Reasoning."""
        self._multi_step = enabled

    def set_task_planning(self, enabled: bool):
        """Aktiviert/deaktiviert Aufgaben-Planung."""
        self._task_planning = enabled

    def set_parallel_tasks(self, enabled: bool):
        """Aktiviert/deaktiviert parallele Tool-Ausführung."""
        self._parallel_tasks = enabled

    def _build_system_prompt(self) -> str:
        """Baut den System-Prompt dynamisch — kombiniert Basis, Modus-Hinweise und Memory."""
        prompt = _SYSTEM_PROMPT

        if self._length_hint:
            prompt += f"\n\nANTWORTLÄNGE: {self._length_hint}"

        if self._tone_hint:
            prompt += f"\n\nTON & STIL: {self._tone_hint}"

        if self._multi_step:
            prompt += (
                "\n\nREASONING-MODUS AKTIV: Bei komplexen Aufgaben, "
                "Berechnungen oder mehrstufigen Problemen denke zuerst Schritt für Schritt. "
                "Zeige deinen Gedankengang mit nummerierten Schritten (1. ... 2. ...). "
                "Bei einfachen Fragen antworte direkt ohne Schritt-Anzeige."
            )

        if self._task_planning:
            prompt += (
                "\n\nAUFGABEN-PLANUNG AKTIV: Wenn der Nutzer eine komplexe Aufgabe beschreibt, "
                "erstelle zuerst einen nummerierten Aktionsplan, zeige diesen an, "
                "und führe dann jeden Schritt aus. Berichte kurz nach jedem Schritt. "
                "Bei einfachen Befehlen direkt ausführen ohne Plan."
            )

        if self._parallel_tasks:
            prompt += (
                "\n\nPARALLEL-MODUS AKTIV: Wenn der Nutzer mehrere unabhängige Aufgaben nennt "
                "(z.B. 'öffne X und suche Y und starte Z'), erkenne alle Aufgaben, "
                "rufe alle nötigen Tools auf und berichte strukturiert über alle Ergebnisse."
            )

        if self._get_memories:
            mem = self._get_memories(self._current_query)
            if mem:
                prompt += mem

        return prompt

    def _trimmed_history(self) -> List[Dict[str, Any]]:
        """Gibt die letzten N Nachrichten zurück um Token zu sparen."""
        return self._history[-self._MAX_HISTORY:]

    @staticmethod
    def _parse_rate_limit_seconds(err_str: str) -> float:
        """Extrahiert die Wartezeit in Sekunden aus einer 429-Fehlermeldung."""
        m = re.search(r"try again in ([\d]+m[\d.]+s|[\d.]+s|[\d]+m)", err_str)
        if m:
            raw = m.group(1)
            mins = re.search(r"(\d+)m", raw)
            secs = re.search(r"(\d+(?:\.\d+)?)s", raw)
            total = 0.0
            if mins:
                total += int(mins.group(1)) * 60
            if secs:
                total += float(secs.group(1))
            return total if total > 0 else 1200.0
        return 1200.0  # Default: 20 Minuten

    @staticmethod
    def _parse_rate_limit_wait(err_str: str) -> str:
        """Extrahiert die Wartezeit als lesbaren String aus einer 429-Fehlermeldung."""
        m = re.search(r"try again in ([\d]+m[\d.]+s|[\d.]+s|[\d]+m)", err_str)
        if m:
            raw = m.group(1)
            mins = re.search(r"(\d+)m", raw)
            secs = re.search(r"(\d+(?:\.\d+)?)s", raw)
            parts = []
            if mins:
                parts.append(f"{mins.group(1)} Minuten")
            if secs:
                parts.append(f"{int(float(secs.group(1)))} Sekunden")
            return " und ".join(parts) if parts else raw
        return "einigen Minuten"

    @staticmethod
    def _is_rate_limit(err) -> bool:
        return "429" in str(err) or "rate_limit" in str(err).lower()

    def register_tool(self, name: str, handler: Callable):
        self._tools[name] = handler

    def chat(self, user_message: str) -> str:
        self._current_query = user_message
        history_len_before = len(self._history)
        self._history.append({"role": "user", "content": user_message})
        last_err = None
        for attempt in range(3):
            try:
                return self._call()
            except Exception as e:
                err_str = str(e)
                # Rate-Limit → sofort abbrechen, nicht wiederholen
                if self._is_rate_limit(e):
                    wait_s = self._parse_rate_limit_seconds(err_str)
                    logger.warning(f"Rate-Limit erreicht. Warte {wait_s:.0f}s.")
                    del self._history[history_len_before:]
                    raise RateLimitError(wait_s)
                # Modell hat Tool-Call falsch formatiert → manuell parsen
                if "tool_use_failed" in err_str or "failed_generation" in err_str:
                    logger.warning("Tool-Call fehlgeschlagen, parse manuell...")
                    try:
                        return self._recover_from_failed_tool(err_str)
                    except Exception as e2:
                        logger.warning(f"Manuelles Parsen fehlgeschlagen: {e2}")
                        last_err = e2
                else:
                    last_err = e
                    logger.warning(f"API Versuch {attempt + 1}/3 fehlgeschlagen: {e}")
                    if attempt < 2:
                        time.sleep(1.5)
                # Verlauf auf Ausgangszustand zurücksetzen für den nächsten Versuch
                del self._history[history_len_before:]
                self._history.append({"role": "user", "content": user_message})
        del self._history[history_len_before:]
        logger.error(f"API nach 3 Versuchen fehlgeschlagen: {last_err}")
        return "Entschuldigung, ich habe gerade keine Verbindung. Bitte versuch es gleich nochmal."

    def chat_streaming(self, user_message: str, on_chunk: Callable[[str], None]) -> str:
        """Streaming chat — ruft on_chunk für jeden Text-Chunk auf.
        Gibt den vollständigen Text zurück wenn fertig.
        Tool-Calls werden transparent behandelt: nach Ausführung streamt die finale Antwort.
        """
        self._current_query = user_message
        history_len_before = len(self._history)
        self._history.append({"role": "user", "content": user_message})
        try:
            return self._call_streaming(on_chunk)
        except RateLimitError:
            # Verlauf auf Stand vor diesem Call zurücksetzen
            del self._history[history_len_before:]
            raise   # direkt weiterwerfen — RouterClient fängt es ab
        except Exception as e:
            if self._is_rate_limit(e):
                wait_s = self._parse_rate_limit_seconds(str(e))
                logger.warning(f"Rate-Limit erreicht. Warte {wait_s:.0f}s.")
                del self._history[history_len_before:]
                raise RateLimitError(wait_s)
            logger.error(f"Streaming fehlgeschlagen, Fallback auf chat(): {e}")
            # Verlauf vollständig zurücksetzen — ggf. wurden schon Tool-Einträge angehängt
            del self._history[history_len_before:]
            result = self.chat(user_message)
            on_chunk(result)
            return result

    def _call_streaming(self, on_chunk: Callable[[str], None]) -> str:
        messages = [{"role": "system", "content": self._build_system_prompt()}] + self._trimmed_history()

        stream = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            tools=_TOOLS,
            tool_choice="auto",
            stream=True,
            timeout=30,
        )

        # ── Phase 1: Erst ALLES sammeln, NICHTS sofort sprechen ──────────────
        # Grund: Das Modell gibt manchmal Funktionsnamen ("get_time") als
        # Text-Content aus, bevor oder statt eines echten tool_call-Deltas.
        # Erst nach dem Ende des Streams entscheiden wir was gesprochen wird.
        accumulated_content = ""
        tool_calls_acc: Dict[int, Dict] = {}  # index → partial tool call
        finish_reason = None

        for chunk in stream:
            choice = chunk.choices[0] if chunk.choices else None
            if choice is None:
                continue

            finish_reason = choice.finish_reason
            delta = choice.delta
            if delta is None:
                continue

            # Text-Delta sammeln — noch NICHT an on_chunk weitergeben
            if delta.content:
                accumulated_content += delta.content

            # Tool-Call Deltas sammeln
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {
                            "id": "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        }
                    entry = tool_calls_acc[idx]
                    if tc_delta.id:
                        entry["id"] += tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            entry["function"]["name"] += tc_delta.function.name
                        if tc_delta.function.arguments:
                            entry["function"]["arguments"] += tc_delta.function.arguments

        # ── Phase 2a: Tool-Calls ausführen + finale Antwort streamen ─────────
        if tool_calls_acc:
            # accumulated_content (z.B. "Ich rufe get_time auf…") wird VERWORFEN
            tool_calls_list = [tool_calls_acc[i] for i in sorted(tool_calls_acc)]
            assistant_entry = {
                "role": "assistant",
                "content": accumulated_content or "",
                "tool_calls": tool_calls_list,
            }
            self._history.append(assistant_entry)
            messages.append(assistant_entry)

            # Ergebnisse sammeln — parallel oder sequenziell
            stream_results: Dict[str, str] = {}

            if self._parallel_tasks and len(tool_calls_list) > 1:
                threads = []
                for tc in tool_calls_list:
                    name = tc["function"]["name"]
                    try:
                        raw_args = json.loads(tc["function"]["arguments"] or "{}")
                        args = raw_args if isinstance(raw_args, dict) else {}
                    except json.JSONDecodeError:
                        args = {}
                    logger.info(f"Streaming Tool-Aufruf (parallel): {name}({args})")

                    def _run(tc_id=tc["id"], n=name, a=args):
                        try:
                            stream_results[tc_id] = str(self._tools[n](**a)) \
                                if n in self._tools else f"Unbekanntes Tool: '{n}'"
                        except BaseException as e:
                            stream_results[tc_id] = f"Tool-Fehler: {e}"

                    t = threading.Thread(target=_run, daemon=True)
                    threads.append(t)
                    t.start()
                for t in threads:
                    t.join(timeout=30)
            else:
                for tc in tool_calls_list:
                    name = tc["function"]["name"]
                    try:
                        raw_args = json.loads(tc["function"]["arguments"] or "{}")
                        args = raw_args if isinstance(raw_args, dict) else {}
                    except json.JSONDecodeError:
                        args = {}
                    logger.info(f"Streaming Tool-Aufruf: {name}({args})")
                    try:
                        stream_results[tc["id"]] = str(self._tools[name](**args)) \
                            if name in self._tools else f"Unbekanntes Tool: '{name}'"
                    except Exception as e:
                        stream_results[tc["id"]] = f"Tool-Fehler: {e}"

            for tc in tool_calls_list:
                tool_entry = {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": stream_results.get(tc["id"], "Kein Ergebnis"),
                }
                messages.append(tool_entry)
                self._history.append(tool_entry)

            # Finale menschenlesbare Antwort streamen — JETZT on_chunk aufrufen
            # tool_choice="none" + tools=_TOOLS verhindert erneute Tool-Calls
            final_stream = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                tools=_TOOLS,
                tool_choice="none",
                stream=True,
                timeout=30,
            )
            final_content = ""
            for chunk in final_stream:
                choice = chunk.choices[0] if chunk.choices else None
                if choice and choice.delta and choice.delta.content:
                    final_content += choice.delta.content
                    on_chunk(choice.delta.content)

            self._history.append({"role": "assistant", "content": final_content})
            return final_content

        # ── Phase 2b: Kein Tool-Call — Text-Antwort jetzt erst sprechen ──────
        # accumulated_content enthält die vollständige Antwort.
        # Wir prüfen zusätzlich auf Llama-Format-Funktionsaufrufe im Text.
        if accumulated_content:
            # Llama-Format <function=...> herausfiltern bevor gesprochen wird
            clean = re.sub(r"<function=.*?</function>", "", accumulated_content,
                           flags=re.DOTALL).strip()
            if clean:
                # Ist clean nur ein Funktionsname (z.B. "get_time" oder "get_time()")?
                # → würde in History als Assistant-Antwort stehen, ist aber ein Tool-Call.
                clean_bare = clean.strip().rstrip("()")
                if clean_bare in self._tools:
                    logger.warning(
                        f"Antwort ist nur Funktionsname '{clean_bare}' — versuche Recovery"
                    )
                    try:
                        return self._recover_from_failed_tool(accumulated_content)
                    except Exception as e:
                        logger.warning(f"Recovery fehlgeschlagen: {e}")
                        self._history.append({"role": "assistant", "content": accumulated_content})
                else:
                    # Normaler Text — History eintragen und sprechen
                    self._history.append({"role": "assistant", "content": accumulated_content})
                    on_chunk(clean)
            # Wenn nur Function-Call-Text übrig war → Recovery versuchen
            elif "<function=" in accumulated_content:
                # Kein History-Eintrag hier — _recover_from_failed_tool übernimmt das
                logger.warning("Nur Function-Call-Text ohne tool_call — versuche Recovery")
                try:
                    return self._recover_from_failed_tool(accumulated_content)
                except Exception as e:
                    logger.warning(f"Recovery fehlgeschlagen: {e}")
                    self._history.append({"role": "assistant", "content": accumulated_content})
            else:
                # Leerer oder unbekannter Inhalt — trotzdem eintragen
                self._history.append({"role": "assistant", "content": accumulated_content})
        else:
            self._history.append({"role": "assistant", "content": accumulated_content})
        return accumulated_content

    def _recover_from_failed_tool(self, err_str: str) -> str:
        """Parst kaputte Tool-Calls aus der Fehlermeldung und führt sie aus."""
        # Llama-Format: <function=name {"key": "val"} </function>
        pattern = r'<function=(\w+)\s*(\{.*?\})\s*</function>'
        matches = re.findall(pattern, err_str, re.DOTALL)

        if not matches:
            return self._call_no_tools()

        messages = [{"role": "system", "content": self._build_system_prompt()}] + self._trimmed_history()
        fake_calls = []

        tool_results_recovery = []
        for name, args_str in matches:
            try:
                raw_args = json.loads(args_str)
                args = raw_args if isinstance(raw_args, dict) else {}
            except json.JSONDecodeError:
                args = {}

            call_id = f"recover_{name}"
            fake_calls.append({"id": call_id, "type": "function",
                                "function": {"name": name, "arguments": json.dumps(args)}})

            try:
                result = self._tools[name](**args) if name in self._tools else f"Tool '{name}' nicht gefunden."
            except Exception as e:
                result = f"Tool-Fehler: {e}"
            logger.info(f"Manuell ausgeführt: {name}({args}) → {str(result)[:60]}")
            tool_results_recovery.append({"id": call_id, "content": str(result)})

        # Assistant entry with all tool calls — added once after collecting all calls
        messages.append({"role": "assistant", "content": "", "tool_calls": fake_calls})
        self._history.append({"role": "assistant", "content": "", "tool_calls": fake_calls})
        for tr in tool_results_recovery:
            messages.append({"role": "tool", "tool_call_id": tr["id"], "content": tr["content"]})
            self._history.append({"role": "tool", "tool_call_id": tr["id"], "content": tr["content"]})

        # Finale Antwort — tool_choice="none" + tools=_TOOLS verhindert erneute Tool-Calls
        response = self._client.chat.completions.create(
            model=self._model, messages=messages, tools=_TOOLS, tool_choice="none", timeout=20,
        )
        content = response.choices[0].message.content or ""
        self._history.append({"role": "assistant", "content": content})
        return content

    def _call_no_tools(self) -> str:
        """Letzter Fallback ohne Tool-Calling.
        tool_choice wird weggelassen (nicht 'none' ohne tools= Parameter übergeben),
        da manche APIs einen 400-Fehler liefern wenn tool_choice ohne tools gesetzt ist.
        """
        messages = [{"role": "system", "content": self._build_system_prompt()}] + self._trimmed_history()
        response = self._client.chat.completions.create(
            model=self._model, messages=messages, timeout=20,
        )
        content = response.choices[0].message.content or ""
        self._history.append({"role": "assistant", "content": content})
        return content

    def _call(self) -> str:
        messages = [{"role": "system", "content": self._build_system_prompt()}] + self._trimmed_history()

        response = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            tools=_TOOLS,
            tool_choice="auto",
            timeout=20,
        )

        msg = response.choices[0].message

        if msg.tool_calls:
            return self._handle_tool_calls(msg, messages)

        content = msg.content or ""
        self._history.append({"role": "assistant", "content": content})
        return content

    def _handle_tool_calls(self, msg, messages: List[Dict]) -> str:
        tool_calls_data = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in msg.tool_calls
        ]

        assistant_entry = {
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": tool_calls_data,
        }
        messages.append(assistant_entry)
        self._history.append(assistant_entry)

        # Tool-Calls ausführen — bei parallel_tasks gleichzeitig
        tool_results: Dict[str, str] = {}

        if self._parallel_tasks and len(msg.tool_calls) > 1:
            # Parallele Ausführung aller Tools
            threads = []
            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    raw_args = json.loads(tc.function.arguments or "{}")
                    args = raw_args if isinstance(raw_args, dict) else {}
                except json.JSONDecodeError:
                    args = {}
                logger.info(f"Tool-Aufruf (parallel): {name}({args})")

                def _run(tc_id=tc.id, n=name, a=args):
                    try:
                        tool_results[tc_id] = str(self._tools[n](**a)) if n in self._tools \
                            else f"Unbekanntes Tool: '{n}'"
                    except BaseException as e:
                        tool_results[tc_id] = f"Tool-Fehler: {e}"

                t = threading.Thread(target=_run, daemon=True)
                threads.append(t)
                t.start()
            for t in threads:
                t.join(timeout=30)
        else:
            # Sequenzielle Ausführung (Standard)
            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    raw_args = json.loads(tc.function.arguments or "{}")
                    args = raw_args if isinstance(raw_args, dict) else {}
                except json.JSONDecodeError:
                    args = {}
                logger.info(f"Tool-Aufruf: {name}({args})")
                try:
                    tool_results[tc.id] = str(self._tools[name](**args)) if name in self._tools \
                        else f"Unbekanntes Tool: '{name}'"
                except Exception as e:
                    tool_results[tc.id] = f"Tool-Fehler: {e}"

        # Ergebnisse in History schreiben
        for tc in msg.tool_calls:
            tool_entry = {
                "role": "tool",
                "tool_call_id": tc.id,
                "content": tool_results.get(tc.id, "Kein Ergebnis"),
            }
            messages.append(tool_entry)
            self._history.append(tool_entry)

        final = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            tools=_TOOLS,
            tool_choice="none",
            timeout=30,
        )
        content = final.choices[0].message.content or ""
        self._history.append({"role": "assistant", "content": content})
        return content

    def clear_history(self):
        self._history.clear()
        logger.info("Gespraesverlauf geloescht.")

    @property
    def history(self) -> List[Dict[str, Any]]:
        return list(self._history)
