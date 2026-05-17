@echo off
cd /d "%~dp0"
title Jarvis - Requirements installieren
color 0B
echo.
echo  ============================================
echo    JARVIS  ^|  Requirements installieren
echo  ============================================
echo.

:: Python pruefen
python --version >nul 2>&1
if errorlevel 1 (
    color 0C
    echo  [FEHLER] Python wurde nicht gefunden!
    echo.
    echo  Bitte installiere Python 3.10 bis 3.13:
    echo  https://www.python.org/downloads/
    echo  Wichtig: "Add Python to PATH" aktivieren!
    echo.
    pause
    exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo  [OK] Python %PYVER% gefunden.
echo.

:: Venv erstellen falls nicht vorhanden
if not exist "venv\" (
    echo  Erstelle virtuelles Environment...
    python -m venv venv
    if errorlevel 1 (
        color 0C
        echo  [FEHLER] Konnte venv nicht erstellen.
        pause
        exit /b 1
    )
    echo  [OK] Virtuelles Environment erstellt.
) else (
    echo  [OK] Virtuelles Environment vorhanden.
)
echo.

:: Pip aktualisieren
echo  Aktualisiere pip...
venv\Scripts\python.exe -m pip install --upgrade pip --quiet
echo  [OK] pip aktuell.
echo.

:: Requirements installieren
echo  Installiere Abhaengigkeiten aus requirements.txt...
echo.
venv\Scripts\pip.exe install -r requirements.txt

if errorlevel 1 (
    color 0C
    echo.
    echo  [FEHLER] Installation fehlgeschlagen.
    echo  Pruefe Internetverbindung und versuche es erneut.
    echo.
    pause
    exit /b 1
)

echo.
color 0A
echo  ============================================
echo   [OK]  Alle Requirements installiert!
echo  ============================================
echo.
echo  Du kannst dieses Fenster jetzt schliessen.
echo  Starte Jarvis mit: "Jarvis starten.bat"
echo.
pause
