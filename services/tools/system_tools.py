"""
Jarvis System-Tools
===================
Stellt Funktionen bereit die als KI-Tools registriert werden:

  run_terminal(command, timeout)     — Windows-Systembefehl ausführen
  run_script(script_path, args)      — Python / Batch / PowerShell-Skript starten
  read_file(path)                    — Textdatei lesen
  write_file(path, content, append)  — Textdatei schreiben / anhängen
  read_clipboard()                   — Zwischenablage lesen
  analyze_screenshot(question)       — Screenshot + OCR-Analyse

Alle Funktionen akzeptieren optional cancel_event: threading.Event
für saubere Abbruch-Unterstützung durch den TaskManager.
"""
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("jarvis.tools.system")

# ── Sicherheitskonfiguration ──────────────────────────────────────────────────

# Erlaubte Basis-Verzeichnisse für Datei-Operationen
_HOME        = Path(os.environ.get("USERPROFILE", str(Path.home())))
_SAFE_ROOTS  = [_HOME]

# Gesperrte Terminal-Befehle (destruktiv / gefährlich)
_BLOCKED_CMDS = [
    "rm -rf", "rmdir /s /q", "format ", "mkfs",
    "del /f /s", "rd /s", "shutdown", ":(){:|:&};:",
    "dd if=", "> /dev/", "reg delete",
]

_MAX_FILE_BYTES = 512 * 1024   # 512 KB — mehr würde den AI-Context sprengen
_MAX_OUTPUT     = 4000          # max. Zeichen Terminal-Ausgabe


def _is_safe_path(path: Path) -> bool:
    """Prüft ob der Pfad innerhalb eines erlaubten Verzeichnisses liegt."""
    try:
        resolved = path.resolve()
        for root in _SAFE_ROOTS:
            try:
                resolved.relative_to(root.resolve())
                return True
            except ValueError:
                continue
        return False
    except Exception:
        return False


# ── run_terminal ──────────────────────────────────────────────────────────────

def run_terminal(
    command:      str,
    timeout:      int = 30,
    cancel_event: Optional[threading.Event] = None,
    working_dir:  str = "",
) -> str:
    """
    Führt einen Windows-Shell-Befehl aus und gibt stdout + stderr zurück.

    Sicherheit:
      - Destruktive Befehle werden blockiert.
      - Output wird auf _MAX_OUTPUT Zeichen begrenzt.
      - cancel_event ermöglicht saubere Unterbrechung.
    """
    if not command or not command.strip():
        return "Fehler: Leerer Befehl."

    # Timeout auf sicheren Bereich begrenzen (1 s … 120 s)
    timeout = max(1, min(timeout, 120))

    cmd_lower = command.lower()
    for blocked in _BLOCKED_CMDS:
        if blocked in cmd_lower:
            return (
                f"Fehler: Der Befehl enthält '{blocked}' — "
                "aus Sicherheitsgründen gesperrt."
            )

    cwd = None
    if working_dir and Path(working_dir).exists():
        cwd = working_dir

    logger.info(f"Terminal: {command!r}")
    try:
        proc = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=cwd,
        )

        # Polling mit Abbruch-Check (in separatem Thread, damit Pipes nie blockieren)
        _stdout_buf: list = []
        _stderr_buf: list = []

        def _reader(pipe, buf):
            try:
                for line in pipe:
                    buf.append(line)
            except Exception:
                pass

        t_out = threading.Thread(target=_reader, args=(proc.stdout, _stdout_buf), daemon=True)
        t_err = threading.Thread(target=_reader, args=(proc.stderr, _stderr_buf), daemon=True)
        t_out.start()
        t_err.start()

        deadline = time.monotonic() + timeout
        while proc.poll() is None:
            if cancel_event and cancel_event.is_set():
                proc.kill()
                t_out.join(timeout=1)
                t_err.join(timeout=1)
                return "Befehl abgebrochen."
            if time.monotonic() > deadline:
                proc.kill()
                t_out.join(timeout=1)
                t_err.join(timeout=1)
                return f"Timeout nach {timeout}s — Befehl abgebrochen."
            time.sleep(0.1)

        t_out.join(timeout=2)
        t_err.join(timeout=2)
        stdout = "".join(_stdout_buf)
        stderr = "".join(_stderr_buf)
        output = (stdout + stderr).strip()

        if not output:
            code = proc.returncode
            return (
                "Befehl erfolgreich ausgeführt (keine Ausgabe)."
                if code == 0
                else f"Befehl beendet mit Fehlercode {code} (keine Ausgabe)."
            )

        if len(output) > _MAX_OUTPUT:
            output = (
                output[:_MAX_OUTPUT]
                + f"\n... [gekürzt auf {_MAX_OUTPUT} Zeichen, "
                f"gesamt {len(output)} Zeichen]"
            )
        return output

    except FileNotFoundError:
        return "Fehler: Befehl nicht gefunden."
    except Exception as exc:
        logger.error(f"Terminal-Fehler: {exc}")
        return f"Terminal-Fehler: {exc}"


# ── run_script ────────────────────────────────────────────────────────────────

def run_script(
    script_path:  str,
    args:         str = "",
    timeout:      int = 60,
    cancel_event: Optional[threading.Event] = None,
) -> str:
    """
    Führt ein Skript aus (.py / .bat / .cmd / .ps1).
    Gibt die Ausgabe zurück.
    """
    path = Path(script_path)

    if not _is_safe_path(path):
        return (
            f"Fehler: Skript liegt außerhalb des erlaubten Bereichs. "
            f"Nur Skripte in {_HOME} sind zugänglich."
        )

    if not path.exists():
        return f"Fehler: Skript nicht gefunden: {script_path!r}"

    suffix = path.suffix.lower()

    if suffix == ".py":
        cmd = f'"{sys.executable}" "{path.resolve()}" {args}'.strip()
    elif suffix in (".bat", ".cmd"):
        cmd = f'"{path.resolve()}" {args}'.strip()
    elif suffix == ".ps1":
        cmd = (
            f'powershell -NoProfile -ExecutionPolicy Bypass '
            f'-File "{path.resolve()}" {args}'
        ).strip()
    else:
        return (
            f"Fehler: Unbekannter Skript-Typ '{suffix}'. "
            "Unterstützt: .py, .bat, .cmd, .ps1"
        )

    logger.info(f"Skript: {cmd!r}")
    return run_terminal(
        cmd,
        timeout=timeout,
        cancel_event=cancel_event,
        working_dir=str(path.parent),
    )


# ── read_file ─────────────────────────────────────────────────────────────────

def read_file(
    path:         str,
    encoding:     str = "utf-8",
    cancel_event: Optional[threading.Event] = None,
) -> str:
    """
    Liest eine Textdatei und gibt den Inhalt zurück.
    Nur Dateien innerhalb des Nutzer-Home-Verzeichnisses erlaubt.
    """
    file_path = Path(path)

    if not _is_safe_path(file_path):
        return (
            f"Fehler: Datei liegt außerhalb des erlaubten Bereichs. "
            f"Nur Dateien in {_HOME} sind zugänglich."
        )
    if not file_path.exists():
        return f"Fehler: Datei nicht gefunden: {path!r}"
    if not file_path.is_file():
        return f"Fehler: {path!r} ist kein reguläres File."

    size = file_path.stat().st_size
    if size > _MAX_FILE_BYTES:
        return (
            f"Fehler: Datei zu groß ({size // 1024} KB). "
            f"Maximum: {_MAX_FILE_BYTES // 1024} KB."
        )

    if cancel_event and cancel_event.is_set():
        return "Abgebrochen."

    try:
        content = file_path.read_text(encoding=encoding, errors="replace")
        logger.info(f"Datei gelesen: {path!r} ({len(content)} Zeichen)")
        return content
    except Exception as exc:
        logger.error(f"Datei lesen: {exc}")
        return f"Fehler beim Lesen der Datei: {exc}"


# ── write_file ────────────────────────────────────────────────────────────────

def write_file(
    path:         str,
    content:      str,
    append:       bool = False,
    encoding:     str = "utf-8",
    cancel_event: Optional[threading.Event] = None,
) -> str:
    """
    Schreibt Text in eine Datei (oder hängt ihn an).
    Nur im Nutzer-Home-Verzeichnis erlaubt.
    append=True → Inhalt anhängen, False → überschreiben.
    """
    file_path = Path(path)

    if not _is_safe_path(file_path):
        return (
            f"Fehler: Pfad liegt außerhalb des erlaubten Bereichs. "
            f"Nur Dateien in {_HOME} sind erlaubt."
        )

    if cancel_event and cancel_event.is_set():
        return "Abgebrochen."

    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        with open(file_path, mode, encoding=encoding) as fh:
            fh.write(content)
        action = "angehängt" if append else "geschrieben"
        logger.info(f"Datei {action}: {path!r} ({len(content)} Zeichen)")
        return f"Datei '{path}' erfolgreich {action} ({len(content)} Zeichen)."
    except Exception as exc:
        logger.error(f"Datei schreiben: {exc}")
        return f"Fehler beim Schreiben der Datei: {exc}"


# ── read_clipboard ────────────────────────────────────────────────────────────

def read_clipboard(
    cancel_event: Optional[threading.Event] = None,
) -> str:
    """
    Liest den Inhalt der Windows-Zwischenablage.
    Nutzt PowerShell — thread-sicher, kein Tkinter-Konflikt.
    """
    if cancel_event and cancel_event.is_set():
        return "Abgebrochen."

    try:
        # PowerShell Get-Clipboard ist thread-sicher und braucht kein Tkinter
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )

        if result.returncode != 0:
            err = result.stderr.strip() or "Unbekannter Fehler"
            logger.error(f"Get-Clipboard fehlgeschlagen (Code {result.returncode}): {err}")
            return f"Clipboard-Fehler: {err}"

        content = result.stdout.rstrip("\n")

        if not content or not content.strip():
            return "Die Zwischenablage ist leer."

        # Zu langen Inhalt kürzen
        if len(content) > 3000:
            content = (
                content[:3000]
                + f"\n\n... [auf 3000 Zeichen gekürzt, gesamt {len(content)} Zeichen]"
            )

        logger.info(f"Clipboard gelesen: {len(content)} Zeichen")
        return content

    except Exception as exc:
        logger.error(f"Clipboard-Fehler: {exc}")
        return f"Clipboard-Fehler: {exc}"


# ── analyze_screenshot ────────────────────────────────────────────────────────

def analyze_screenshot(
    question:     str = "",
    cancel_event: Optional[threading.Event] = None,
) -> str:
    """
    Macht einen Screenshot des primären Monitors und analysiert ihn.

    Analyse-Strategie (in Reihenfolge):
      1. pytesseract (OCR) — wenn installiert: Text aus Bildschirm extrahieren
      2. PIL Bildinfo      — Fallback mit Auflösung und Hinweis

    Rückgabe ist ein Text-String den die KI als Tool-Ergebnis erhält.
    """
    if cancel_event and cancel_event.is_set():
        return "Abgebrochen."

    # Screenshot aufnehmen
    try:
        from PIL import ImageGrab
    except ImportError:
        return (
            "Fehler: Pillow nicht installiert. "
            "Bitte 'pip install Pillow' ausführen."
        )

    try:
        img = ImageGrab.grab(all_screens=False)
    except Exception as exc:
        logger.error(f"Screenshot Fehler: {exc}")
        return f"Screenshot konnte nicht aufgenommen werden: {exc}"

    if cancel_event and cancel_event.is_set():
        return "Abgebrochen."

    width, height = img.size
    logger.info(f"Screenshot aufgenommen: {width}×{height}px")

    # ── OCR-Versuch ───────────────────────────────────────────────────────────
    try:
        import pytesseract

        # Automatisch Tesseract-Pfad auf Windows suchen
        candidates = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ]
        for candidate in candidates:
            if Path(candidate).exists():
                pytesseract.pytesseract.tesseract_cmd = candidate
                break

        # OCR ausführen (Deutsch + Englisch)
        text = pytesseract.image_to_string(img, lang="deu+eng", timeout=15)
        text = text.strip()

        if text:
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            clean_text = "\n".join(lines)

            if len(clean_text) > 3000:
                clean_text = (
                    clean_text[:3000]
                    + f"\n... [auf 3000 Zeichen gekürzt]"
                )

            result = (
                f"Screenshot aufgenommen ({width}×{height}px).\n\n"
                f"=== Auf dem Bildschirm erkannter Text (OCR) ===\n"
                f"{clean_text}"
            )
            if question:
                result += f"\n\n=== Frage zum Bild ===\n{question}"

            logger.info(f"OCR erfolgreich: {len(clean_text)} Zeichen")
            return result
        else:
            logger.info("OCR: Kein Text erkannt.")

    except ImportError:
        logger.debug("pytesseract nicht installiert — OCR nicht verfügbar.")
    except Exception as exc:
        logger.warning(f"OCR-Fehler: {exc}")

    # ── Fallback ohne OCR ─────────────────────────────────────────────────────
    result = (
        f"Screenshot aufgenommen ({width}×{height}px). "
        f"Für Text-Erkennung bitte 'pip install pytesseract' installieren "
        f"und Tesseract-OCR von https://github.com/UB-Mannheim/tesseract/wiki herunterladen."
    )
    if question:
        result += f" Frage: {question}"
    return result
