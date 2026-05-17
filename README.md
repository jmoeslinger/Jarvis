# 🤖 Jarvis — Dein persönlicher KI-Sprachassistent für Windows

Jarvis ist ein lokaler Sprachassistent für Windows, den ich in meiner Freizeit entwickelt habe. Er läuft komplett auf deinem PC, hört auf dein Wort und verbindet sich mit modernen KI-Modellen, um dir wirklich zu helfen — nicht nur Schlagwörter zurückzuwerfen.

Die Idee war simpel: Ich wollte einen Assistenten, der sich anfühlt wie aus einem Sci-Fi-Film, aber tatsächlich funktioniert und meinen Alltag am PC erleichtert.

---

## ✨ Was kann Jarvis?

- **Sprachsteuerung** — sprich einfach, Jarvis hört zu (Wake-Word „Hey Jarvis" oder Hotkey)
- **KI-Antworten** — verbunden mit Groq (Llama 3.3, schnell & kostenlos) oder lokal via Ollama
- **Apps öffnen & schließen** — „Öffne Spotify", „Schließ Chrome"
- **Uhrzeit & Datum** — „Wie spät ist es?"
- **Internet-Suche** — aktuelle Infos, Wetter, Nachrichten
- **YouTube & Browser** — „Spiel Lo-Fi Hip Hop auf YouTube"
- **Timer setzen** — „Stell einen Timer auf 10 Minuten"
- **Dateien lesen & schreiben** — Notizen diktieren, Dateien vorlesen lassen
- **Terminal-Befehle** — Windows-Befehle per Sprache ausführen
- **Zwischenablage lesen** — „Was hab ich kopiert?"
- **Bildschirm analysieren** — „Was siehst du auf meinem Bildschirm?"
- **Gedächtnis** — Jarvis merkt sich Infos über dich dauerhaft
- **Emotionserkennung** — reagiert auf deine Stimmung in der Stimme
- **Sprechererkennung** — erkennt nur deine Stimme (optional)
- **Lokales Wake-Word** — „Hey Jarvis" offline, kein Internet nötig
- **Wiederkehrende Aufgaben** — automatisierte Erinnerungen & Tasks
- **Task-Queue** — mehrere Aufgaben nacheinander abarbeiten

---

## 🖥️ Voraussetzungen

- Windows 10 / 11
- Python 3.10 oder neuer
- Ein Mikrofon
- Einen kostenlosen [Groq API-Key](https://console.groq.com) (für die KI-Antworten)
- Optional: [Ollama](https://ollama.ai) für vollständig lokale KI ohne Internet

---

## 🚀 Installation

### 1. Repository klonen

```bash
git clone https://github.com/jmoeslinger/Jarvis.git
cd Jarvis
```

### 2. Abhängigkeiten installieren

Einfach die mitgelieferte Batch-Datei doppelklicken:

```
requirements installieren.bat
```

Oder manuell:

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Starten & einrichten

```
Jarvis starten.bat
```

Beim ersten Start öffnet sich ein Einrichtungsdialog. Dort trägst du deinen Groq API-Key ein (kostenlos unter [console.groq.com](https://console.groq.com) registrieren — dauert 2 Minuten).

### 4. Benutzen

- Drücke **H+Strg** oder sag **„Hey Jarvis"** → Jarvis hört zu
- Sag deinen Befehl
- Fertig

---

## ⚙️ Optionale Features

| Feature | Zusätzliches Paket | Befehl |
|---|---|---|
| Lokales Wake-Word (offline) | `openwakeword` + `onnxruntime` | `pip install openwakeword onnxruntime` |
| Sprechererkennung | `resemblyzer` | `pip install resemblyzer` |
| Lokale KI (kein Internet) | Ollama | [ollama.ai](https://ollama.ai) herunterladen |

---

## 📁 Projektstruktur

```
Jarvis/
├── main.py                  # Einstiegspunkt
├── config/                  # Einstellungen
├── core/                    # Kernlogik (jarvis.py, task_manager.py)
├── services/
│   ├── ai/                  # KI-Clients (Groq, Ollama, Router)
│   ├── speech/              # Spracherkennung, TTS, Emotionen, Speaker-ID
│   ├── tools/               # System-Tools (Terminal, Dateien, Screenshot)
│   ├── web/                 # Suche, Browser-Steuerung
│   └── system/              # App-Launcher, Hotkeys, Autostart
├── ui/                      # Benutzeroberfläche (HUD, Control Panel, Chat)
└── requirements.txt
```

---

## 🔑 API-Keys & Datenschutz

- Dein **Groq API-Key** wird lokal in `%APPDATA%\Jarvis\settings.json` gespeichert und nirgendwo hochgeladen
- Sprachaufnahmen werden **direkt** an die Groq-API (Whisper) zur Transkription gesendet — nur wenn du sprichst
- Jarvis selbst sammelt **keine** Nutzungsdaten, sendet **keine** Telemetrie
- Das Gedächtnis (Memories) liegt lokal unter `%APPDATA%\Jarvis\memories.json`
- Wenn du Groq nicht nutzen möchtest: Ollama als vollständig lokale Alternative einrichten

---

## 🐛 Bekannte Einschränkungen

- Manche Sprachbefehle werden vom Modell falsch verstanden — am besten klar und deutlich sprechen
- Das Wake-Word „Hey Jarvis" funktioniert am besten in ruhiger Umgebung
- Für lokales Wake-Word muss `openwakeword` separat installiert werden
- Jarvis benötigt unter Umständen Admin-Rechte für bestimmte Terminal-Befehle

---

## 📜 Lizenz & Rechtliches

Dieses Projekt steht unter der **MIT-Lizenz** — du darfst es frei verwenden, teilen, verändern und in eigenen Projekten einsetzen. Details in [LICENSE](LICENSE).

Durch die Nutzung von Jarvis stimmst du den [Nutzungsbedingungen](TERMS.md) zu.

---

## 🙏 Danke an

- [Groq](https://groq.com) — blitzschnelle LLM-Inferenz, kostenlos
- [OpenWakeWord](https://github.com/dscripka/openWakeWord) — lokale Wake-Word-Erkennung
- [Resemblyzer](https://github.com/resemble-ai/Resemblyzer) — Sprechererkennung
- [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) — modernes GUI
- [edge-tts](https://github.com/rany2/edge-tts) — natürliche Sprachausgabe

---

*Version v0.1.2 — gebaut mit zu viel Kaffee und zu wenig Schlaf* ☕
