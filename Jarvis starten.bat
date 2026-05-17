@echo off
cd /d "%~dp0"
title Jarvis starten

:: Pruefen ob venv vorhanden
if not exist "venv\Scripts\pythonw.exe" (
    color 0C
    echo.
    echo  [FEHLER] Jarvis ist noch nicht eingerichtet!
    echo.
    echo  Bitte zuerst "requirements installieren.bat" ausfuehren.
    echo.
    pause
    exit /b 1
)

:: Alte Instanzen beenden + PID-Datei loeschen
taskkill /f /im pythonw.exe >nul 2>&1
if exist "%APPDATA%\Jarvis\jarvis.pid" del "%APPDATA%\Jarvis\jarvis.pid" >nul 2>&1

:: Kurz warten
timeout /t 1 /nobreak >nul

:: Jarvis ohne Konsolenfenster starten
start "" /b venv\Scripts\pythonw.exe main.py

echo  Jarvis wird gestartet...
timeout /t 2 /nobreak >nul
echo  Das Tray-Icon erscheint gleich unten rechts in der Taskleiste.
timeout /t 2 /nobreak >nul
exit
