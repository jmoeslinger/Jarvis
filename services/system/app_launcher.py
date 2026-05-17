import difflib
import logging
import shutil
import subprocess
import winreg
from pathlib import Path
from typing import Optional

import psutil

logger = logging.getLogger("jarvis.launcher")

# ── Normalisierungs-Tabelle (was die KI sagen könnte → interner Key) ─────────

_ALIASES: dict[str, str] = {
    # Browser
    "chrome":                   "chrome",
    "google chrome":            "chrome",
    "google":                   "chrome",
    "chromium":                 "chrome",
    "firefox":                  "firefox",
    "mozilla":                  "firefox",
    "mozilla firefox":          "firefox",
    "edge":                     "edge",
    "microsoft edge":           "edge",
    "msedge":                   "edge",
    "opera":                    "opera",
    "brave":                    "brave",
    "brave browser":            "brave",

    # Office
    "word":                     "word",
    "microsoft word":           "word",
    "excel":                    "excel",
    "microsoft excel":          "excel",
    "powerpoint":               "powerpoint",
    "microsoft powerpoint":     "powerpoint",
    "outlook":                  "outlook",
    "microsoft outlook":        "outlook",
    "onenote":                  "onenote",
    "teams":                    "teams",
    "microsoft teams":          "teams",

    # Entwicklung
    "vs code":                  "vscode",
    "vscode":                   "vscode",
    "visual studio code":       "vscode",
    "visual studio":            "visualstudio",
    "pycharm":                  "pycharm",
    "notepad++":                "notepadpp",

    # Windows-Bordmittel
    "notepad":                  "notepad",
    "notizblock":               "notepad",
    "rechner":                  "calc",
    "calculator":               "calc",
    "taschenrechner":           "calc",
    "paint":                    "paint",
    "explorer":                 "explorer",
    "datei-explorer":           "explorer",
    "dateiexplorer":            "explorer",
    "task-manager":             "taskmgr",
    "taskmanager":              "taskmgr",
    "aufgaben-manager":         "taskmgr",
    "aufgabenmanager":          "taskmgr",
    "task manager":             "taskmgr",
    "powershell":               "powershell",
    "terminal":                 "terminal",
    "windows terminal":         "terminal",
    "cmd":                      "cmd",
    "eingabeaufforderung":      "cmd",
    "einstellungen":            "settings",
    "settings":                 "settings",
    "systemsteuerung":          "control",
    "control panel":            "control",

    # Unterhaltung / Medien
    "spotify":                  "spotify",
    "vlc":                      "vlc",
    "steam":                    "steam",
    "epic games":               "epicgames",
    "epic":                     "epicgames",

    # Kommunikation
    "discord":                  "discord",
    "whatsapp":                 "whatsapp",
    "telegram":                 "telegram",
    "zoom":                     "zoom",
    "skype":                    "skype",
    "slack":                    "slack",
}

# ── Prozessname je App (für close_app) ───────────────────────────────────────

_PROCESS_NAMES: dict[str, list[str]] = {
    "chrome":       ["chrome.exe"],
    "firefox":      ["firefox.exe"],
    "edge":         ["msedge.exe"],
    "opera":        ["opera.exe"],
    "brave":        ["brave.exe"],
    "word":         ["winword.exe", "WINWORD.EXE"],
    "excel":        ["excel.exe", "EXCEL.EXE"],
    "powerpoint":   ["powerpnt.exe", "POWERPNT.EXE"],
    "outlook":      ["outlook.exe", "OUTLOOK.EXE"],
    "onenote":      ["onenote.exe"],
    "teams":        ["teams.exe", "ms-teams.exe"],
    "vscode":       ["Code.exe"],
    "visualstudio": ["devenv.exe"],
    "pycharm":      ["pycharm64.exe"],
    "notepadpp":    ["notepad++.exe"],
    "notepad":      ["notepad.exe"],
    "calc":         ["calc.exe", "CalculatorApp.exe"],
    "paint":        ["mspaint.exe"],
    "taskmgr":      ["taskmgr.exe"],
    "explorer":     ["explorer.exe"],
    "spotify":      ["Spotify.exe"],
    "discord":      ["Discord.exe"],
    "telegram":     ["Telegram.exe"],
    "whatsapp":     ["WhatsApp.exe"],
    "zoom":         ["Zoom.exe"],
    "skype":        ["Skype.exe"],
    "slack":        ["slack.exe"],
    "steam":        ["steam.exe"],
    "epicgames":    ["EpicGamesLauncher.exe"],
    "vlc":          ["vlc.exe"],
    "roblox":       ["RobloxPlayerBeta.exe", "RobloxPlayer.exe"],
}

# ── Registry-Name je App ──────────────────────────────────────────────────────

_REGISTRY_EXE: dict[str, str] = {
    "chrome":        "chrome.exe",
    "firefox":       "firefox.exe",
    "edge":          "msedge.exe",
    "opera":         "opera.exe",
    "brave":         "brave.exe",
    "word":          "winword.exe",
    "excel":         "excel.exe",
    "powerpoint":    "powerpnt.exe",
    "outlook":       "outlook.exe",
    "onenote":       "onenote.exe",
    "teams":         "teams.exe",
    "vscode":        "Code.exe",
    "visualstudio":  "devenv.exe",
    "pycharm":       "pycharm64.exe",
    "notepadpp":     "notepad++.exe",
    "vlc":           "vlc.exe",
    "zoom":          "Zoom.exe",
    "skype":         "Skype.exe",
    "slack":         "slack.exe",
    "steam":         "steam.exe",
}

# ── Glob-Pfade für Apps nicht in der Registry ─────────────────────────────────

_HOME = Path.home()
_SEARCH_ROOTS = [
    Path(r"C:\Program Files"),
    Path(r"C:\Program Files (x86)"),
    _HOME / "AppData" / "Local",
    _HOME / "AppData" / "Roaming",
    _HOME / "AppData" / "Local" / "Programs",
]

_GLOB_PATTERNS: dict[str, list[str]] = {
    "spotify":    ["Spotify/Spotify.exe", "Microsoft/WindowsApps/Spotify.exe"],
    "discord":    ["Discord/app-*/Discord.exe"],
    "telegram":   ["Telegram Desktop/Telegram.exe"],
    "whatsapp":   ["WhatsApp/WhatsApp.exe"],
    "epicgames":  ["Epic Games Launcher/Portal/Binaries/Win64/EpicGamesLauncher.exe"],
    "steam":      ["Steam/steam.exe"],
    "brave":      ["BraveSoftware/Brave-Browser/Application/brave.exe"],
}

# ── Direkte Shell-Befehle (kein Pfad nötig) ───────────────────────────────────

_DIRECT_CMD: dict[str, str] = {
    "notepad":    "notepad.exe",
    "calc":       "calc.exe",
    "paint":      "mspaint.exe",
    "explorer":   "explorer.exe",
    "taskmgr":    "taskmgr.exe",
    "powershell": "powershell.exe",
    "terminal":   "wt.exe",
    "cmd":        "cmd.exe",
    "control":    "control.exe",
    "settings":   "ms-settings:",
}


class AppLauncher:

    def close(self, app_name: str) -> str:
        key        = app_name.lower().strip()
        normalized = _ALIASES.get(key, key)
        logger.info(f"Schließe '{app_name}' (normalisiert: '{normalized}')")

        # Bekannte Prozessnamen für diese App
        targets = _PROCESS_NAMES.get(normalized, [])

        # Fuzzy-Fallback: ähnlichen Key in _PROCESS_NAMES suchen
        if not targets:
            close = difflib.get_close_matches(normalized, _PROCESS_NAMES.keys(), n=1, cutoff=0.45)
            if close:
                targets = _PROCESS_NAMES[close[0]]

        # Immer auch den normalisierten Namen selbst versuchen — als Set für O(1) Lookup
        targets: set = {t.lower() for t in targets} | {normalized + ".exe"}

        killed: list[str] = []
        for proc in psutil.process_iter(["name", "pid"]):
            try:
                if proc.info["name"] and proc.info["name"].lower() in targets:
                    proc.terminate()           # sanftes Beenden
                    killed.append(proc.info["name"])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        # Prozesse die nicht reagiert haben nach kurzer Wartezeit hart killen
        if killed:
            import time; time.sleep(1)
            for proc in psutil.process_iter(["name"]):
                try:
                    if proc.info["name"] and proc.info["name"].lower() in targets:
                        proc.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            unique = list(dict.fromkeys(killed))
            logger.info(f"Geschlossen: {unique}")
            return f"{app_name} wurde geschlossen."

        # Nichts gefunden → Start-Menü-Namen als Hinweis für Prozessname nutzen
        # Letzter Versuch: alle laufenden Prozesse nach App-Namen durchsuchen
        for proc in psutil.process_iter(["name"]):
            try:
                pname = proc.info["name"] or ""
                if normalized in pname.lower() or key in pname.lower():
                    proc.terminate()
                    killed.append(pname)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        if killed:
            return f"{app_name} wurde geschlossen."

        logger.warning(f"Kein laufender Prozess für '{app_name}' gefunden.")
        return f"{app_name} läuft gerade nicht."

    def launch(self, app_name: str) -> str:
        key = app_name.lower().strip()
        normalized = _ALIASES.get(key, key)
        logger.info(f"Starte '{app_name}' (normalisiert: '{normalized}')")

        # 1. Direkte Befehle & Registry (notepad, calc, Chrome, Office …)
        path = self._resolve_direct(normalized)
        if path:
            return self._run(path, app_name)

        # 2. Start-Menü .lnk — zuverlässigste Methode für installierte Apps
        lnk = self._find_in_start_menu(key) or self._find_in_start_menu(normalized)
        if lnk:
            logger.info(f"Start-Menü: {lnk}")
            return self._run_lnk(lnk, app_name)

        # 3. Glob-Suche in bekannten Verzeichnissen
        path = self._resolve_glob(normalized)
        if path:
            return self._run(path, app_name)

        # 4. Windows-Shell (letzter Versuch)
        return self._shell_start(key, app_name)

    # ------------------------------------------------------------------

    def _resolve_direct(self, key: str) -> Optional[str]:
        """Schnelle Auflösung: Shell-Befehle, Registry, PATH."""
        # 1. Direkte Windows-Befehle (notepad, calc …)
        if key in _DIRECT_CMD:
            return _DIRECT_CMD[key]

        # 2. Registry: App Paths (Chrome, Office …)
        if key in _REGISTRY_EXE:
            path = _registry_lookup(_REGISTRY_EXE[key])
            if path:
                return path

        # 3. shutil.which (PATH)
        candidates = [key, key + ".exe", _REGISTRY_EXE.get(key, "")]
        for c in candidates:
            if c:
                found = shutil.which(c)
                if found:
                    return found

        return None

    def _resolve_glob(self, key: str) -> Optional[str]:
        """Glob-Suche in bekannten App-Verzeichnissen."""
        patterns = _GLOB_PATTERNS.get(key, [])
        for root in _SEARCH_ROOTS:
            for pattern in patterns:
                matches = list(root.glob(pattern))
                if matches:
                    return str(sorted(matches)[-1])
        return None

    def _run(self, path: str, display_name: str) -> str:
        try:
            if path.startswith("ms-"):
                subprocess.Popen(f"start {path}", shell=True)
            else:
                subprocess.Popen([path], shell=False)
            logger.info(f"Gestartet: {path}")
            return f"{display_name} wird geöffnet."
        except Exception as e:
            logger.error(f"Fehler beim Starten von '{path}': {e}")
            return self._shell_start(display_name.lower(), display_name)

    def _find_in_start_menu(self, name: str) -> Optional[str]:
        """Durchsucht das Start-Menü nach dem ähnlichsten .lnk-Shortcut."""
        start_menus = [
            Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs",
            Path(r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs"),
        ]

        shortcuts: dict[str, Path] = {}
        for menu in start_menus:
            if menu.exists():
                for lnk in menu.rglob("*.lnk"):
                    shortcuts[lnk.stem.lower()] = lnk

        if not shortcuts:
            return None

        stems = list(shortcuts.keys())

        # 1. Exakter Substring-Match (z. B. "roblox" in "roblox player")
        for stem in stems:
            if name in stem or stem in name:
                logger.info(f"Start-Menü Substring-Match: '{stem}'")
                return str(shortcuts[stem])

        # 2. Fuzzy-Match (Tippfehler, ähnliche Namen)
        close = difflib.get_close_matches(name, stems, n=1, cutoff=0.45)
        if close:
            logger.info(f"Start-Menü Fuzzy-Match: '{close[0]}'")
            return str(shortcuts[close[0]])

        return None

    def _run_lnk(self, lnk_path: str, display_name: str) -> str:
        """Startet einen Shortcut (.lnk) über den Windows-Shell-Start."""
        try:
            # cmd /c start öffnet .lnk-Dateien zuverlässig über den Windows-Shell
            subprocess.Popen(
                ["cmd", "/c", "start", "", lnk_path],
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.info(f"Shortcut gestartet: {lnk_path}")
            return f"{display_name} wird geöffnet."
        except Exception as e:
            logger.error(f"Shortcut-Start Fehler '{lnk_path}': {e}")
            return f"Ich konnte '{display_name}' leider nicht starten."

    def _shell_start(self, cmd: str, display_name: str) -> str:
        try:
            subprocess.Popen(["cmd", "/c", "start", "", cmd], shell=False)
            logger.info(f"Shell-Start: '{cmd}'")
            return f"{display_name} wird geöffnet."
        except Exception as e:
            logger.warning(f"Shell-Start fehlgeschlagen für '{cmd}': {e}")
            return f"Ich konnte '{display_name}' nicht finden. Ist es installiert?"


def _registry_lookup(exe_name: str) -> Optional[str]:
    bases = [
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths",
    ]
    sep = chr(92)  # backslash
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for base in bases:
            try:
                key = winreg.OpenKey(root, base + sep + exe_name)
                path, _ = winreg.QueryValueEx(key, "")
                winreg.CloseKey(key)
                if path and Path(path).exists():
                    return path
            except OSError:
                pass
    return None
