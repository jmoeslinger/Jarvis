# Jarvis — Personal AI Voice Assistant for Windows

Jarvis is a local voice assistant for Windows, built in my free time. It runs entirely on your PC, listens for your commands, and connects to modern AI models to actually help you — not just throw keywords back at you.

The idea was simple: I wanted an assistant that feels like something out of a sci-fi film, but actually works and makes daily PC use easier.

---

## What can Jarvis do?

- **Voice control** — just speak, Jarvis listens (wake word "Hey Jarvis" or hotkey)
- **AI responses** — powered by Groq (Llama 3.3, fast & free) or locally via Ollama
- **Open & close apps** — "Open Spotify", "Close Chrome"
- **Time & date** — "What time is it?"
- **Web search** — current info, weather, news
- **YouTube & browser** — "Play Lo-Fi Hip Hop on YouTube"
- **Set timers** — "Set a timer for 10 minutes"
- **Read & write files** — dictate notes, have files read aloud
- **Terminal commands** — run Windows commands by voice
- **Read clipboard** — "What did I copy?"
- **Screen analysis** — "What do you see on my screen?"
- **Memory** — Jarvis permanently remembers information about you
- **Emotion detection** — responds to your mood in your voice
- **Speaker recognition** — only reacts to your voice (optional)
- **Smart pause** — natural speech detection, no premature cut-offs
- **Interrupt handling** — stops speaking when you start talking
- **Whisper mode** — responds when you speak quietly (disableable per user)
- **Multiple commands** — "Open Spotify and then set a timer for 10 minutes"
- **Local wake word** — "Hey Jarvis" offline, no internet required
- **Recurring tasks** — automated reminders and scheduled commands
- **Task queue** — process multiple commands one after another

---

## Requirements

- Windows 10 / 11
- Python 3.10 or newer
- A microphone
- A free [Groq API key](https://console.groq.com) (for AI responses)
- Optional: [Ollama](https://ollama.ai) for fully local AI without internet

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/jmoeslinger/Jarvis.git
cd Jarvis
```

### 2. Install dependencies

Double-click the included batch file:

```
requirements installieren.bat
```

Or manually:

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Start and set up

```
Jarvis starten.bat
```

On first launch a setup dialog opens. Enter your Groq API key there (register for free at [console.groq.com](https://console.groq.com) — takes 2 minutes).

### 4. Use it

- Press **Ctrl+Shift+J** or say **"Hey Jarvis"** -> Jarvis listens
- Say your command
- Done

---

## Optional Features

| Feature | Additional package | Command |
|---|---|---|
| Local wake word (offline) | `openwakeword` + `onnxruntime` | `pip install openwakeword onnxruntime` |
| Speaker recognition | `resemblyzer` | `pip install resemblyzer` |
| Local AI (no internet) | Ollama | Download at [ollama.ai](https://ollama.ai) |

---

## Project Structure

```
Jarvis/
├── main.py                  # Entry point
├── config/                  # Settings
├── core/                    # Core logic (jarvis.py, task_manager.py)
├── services/
│   ├── ai/                  # AI clients (Groq, Ollama, Router)
│   ├── speech/              # Speech recognition, TTS, emotions, speaker ID
│   ├── tools/               # System tools (terminal, files, screenshot)
│   ├── web/                 # Search, browser control
│   └── system/              # App launcher, hotkeys, autostart
├── ui/                      # User interface (HUD, control panel, chat)
└── requirements.txt
```

---

## API Keys & Privacy

- Your **Groq API key** is stored locally in `%APPDATA%\Jarvis\settings.json` and never uploaded anywhere
- Voice recordings are sent **directly** to the Groq API (Whisper) for transcription — only when you speak
- Jarvis itself collects **no** usage data and sends **no** telemetry
- Memory (Memories) is stored locally at `%APPDATA%\Jarvis\memories.json`
- If you don't want to use Groq: set up Ollama as a fully local alternative

---

## Known Limitations

- Some voice commands may be misunderstood by the model — speak clearly and directly
- The wake word "Hey Jarvis" works best in a quiet environment
- Local wake word requires `openwakeword` to be installed separately
- Jarvis may require admin rights for certain terminal commands

---

## Contributors

| Name | GitHub | Role |
|------|--------|------|
| Jakub Möslinger | [@jmoeslinger](https://github.com/jmoeslinger) | Creator & Lead Developer |
| Claude (Anthropic) | — | Autonomous debugging (v0.1.10–v0.2.0) |

Full list: [CONTRIBUTORS.md](CONTRIBUTORS.md)

---

## License & Legal

This project is licensed under the **MIT License** — you are free to use, share, modify and incorporate it into your own projects. See [LICENSE](LICENSE) for details.

By using Jarvis you agree to the [Terms of Use](TERMS.md).

---

## Acknowledgements

- [Groq](https://groq.com) — lightning-fast LLM inference, free
- [OpenWakeWord](https://github.com/dscripka/openWakeWord) — local wake word detection
- [Resemblyzer](https://github.com/resemble-ai/Resemblyzer) — speaker recognition
- [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) — modern GUI
- [edge-tts](https://github.com/rany2/edge-tts) — natural text-to-speech

---

*Version v0.2.1 — built with too much coffee and too little sleep*
