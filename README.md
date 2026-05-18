# Jarvis — Personal AI Voice Assistant for Windows

Jarvis is a local voice assistant for Windows, built in my free time. It runs entirely on your PC, listens for your commands, and connects to modern AI models to actually help you — not just throw keywords back at you.

The idea was simple: I wanted an assistant that feels like something out of a sci-fi film, but actually works and makes daily PC use easier.

---

## What can Jarvis do?

### Voice & AI
- **Voice control** — speak naturally, Jarvis listens (wake word "Hey Jarvis" or configurable hotkey)
- **AI responses** — powered by Groq (Llama 3.3 / 4, fast & free) or locally via Ollama
- **Streaming answers** — responses appear and are spoken word by word, no waiting
- **Multiple commands** — "Open Spotify and then set a timer for 10 minutes"
- **Interrupt handling** — stops speaking the moment you start talking
- **Smart pause** — natural speech detection, no premature cut-offs
- **Whisper mode** — responds even when you speak quietly
- **Local wake word** — "Hey Jarvis" fully offline, no internet required
- **Continuous listening** — optional mode: no wake word needed at all

### Memory & Context
- **Long-term memory** — Jarvis permanently remembers facts, preferences and tasks about you
- **Long-term context** — summaries of past conversations are included in every request
- **Conversation resume** — last session is restored on the next start
- **Style learning** — learns your preferred tone and answer length over time
- **Adaptive responses** — automatically applies learned style preferences

### PC Control
- **Open & close apps** — "Open Spotify", "Close Chrome"
- **Terminal commands** — run Windows commands by voice
- **Read & write files** — dictate notes, have files read aloud
- **Read clipboard** — "What did I copy?"
- **Web search** — current info, weather, news (DuckDuckGo)
- **YouTube & browser** — "Play Lo-Fi Hip Hop on YouTube"
- **Set timers** — "Set a timer for 10 minutes"
- **Recurring tasks** — automated reminders and scheduled commands
- **Task queue** — multiple commands processed one after another

### Vision & Camera (NEW in v0.3.0)
- **Live camera preview** — real-time webcam feed in the camera window
- **Screenshot analysis** — "What do you see on my screen?"
- **Camera photo analysis** — "Look through the camera and describe what you see"
- **Video analysis** — records a short clip (3 s, 9 frames), stitches them into a grid and sends it to the Vision AI — "What's happening on camera?"
- **Gesture control** — assign any Jarvis command to a hand gesture (open palm, fist, thumbs up, peace, pointing, rock sign); hold the gesture for ~1.5 s to trigger it; live overlay shows current gesture and progress bar
- Vision providers: Groq Llama-4 Scout → Google Gemini Flash → Ollama (moondream / llava)

### Personalization
- **Personality presets** — Assistant, Friend, Butler, Coach, Scientist, or fully custom
- **Response length** — Short / Normal / Detailed
- **Response tone** — Formal / Normal / Casual / Technical / Creative
- **Multi-step reasoning** — step-by-step thinking mode for complex questions
- **Proactive suggestions** — Jarvis offers suggestions on its own when idle
- **Day planning** — add and complete daily tasks by voice
- **Mood-based responses** — tone adapts to detected emotion in your voice

### Privacy & Security
- **Speaker recognition** — only reacts to your enrolled voice
- **Emotion detection** — detects mood from audio features
- **Noise filter** — spectral noise reduction before speech recognition

---

## Requirements

- Windows 10 / 11
- Python 3.10 or newer
- A microphone
- A free [Groq API key](https://console.groq.com) (for AI — takes 2 minutes to register)
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

On first launch a setup dialog opens. Enter your Groq API key there (register for free at [console.groq.com](https://console.groq.com)).

### 4. Use it

- Press **Ctrl+Shift+J** or say **"Hey Jarvis"** → Jarvis listens
- Say your command
- Done

---

## Optional Features

| Feature | Additional package | Install command |
|---|---|---|
| Camera / Vision | `opencv-python` | `pip install opencv-python` |
| Gesture control | `mediapipe` | `pip install mediapipe` |
| Local wake word (offline) | `openwakeword` + `onnxruntime` | `pip install openwakeword onnxruntime` |
| Speaker recognition | `resemblyzer` | `pip install resemblyzer` |
| Local AI (no internet) | Ollama | Download at [ollama.ai](https://ollama.ai) |
| Google Gemini Vision | — | Free API key at [aistudio.google.com](https://aistudio.google.com) |

> All optional features degrade gracefully — if a package is missing, that feature is simply disabled without any crash.

---

## Project Structure

```
Jarvis/
├── main.py                      # Entry point
├── config/
│   └── settings.py              # All user settings (persistent JSON)
├── core/
│   ├── jarvis.py                # Core logic, tool registry, state machine
│   └── task_manager.py          # Task queue with priorities
├── services/
│   ├── ai/                      # AI clients (Groq, Ollama, Router, ProactiveEngine, DayPlanner)
│   ├── speech/                  # STT, TTS, emotion detector, speaker ID, style learner
│   ├── memory/                  # Memory store, conversation log
│   ├── tools/                   # System tools (terminal, files, screenshot, clipboard)
│   ├── vision/
│   │   ├── camera.py            # Webcam capture (photo + video frames)
│   │   ├── vision_client.py     # Vision AI (Groq / Gemini / Ollama), video grid analysis
│   │   └── gesture_recognizer.py# Hand gesture detection via MediaPipe (NEW v0.3.0)
│   ├── web/                     # DuckDuckGo search, browser control
│   └── system/                  # App launcher, hotkeys, autostart
├── ui/
│   ├── hud.py                   # Floating HUD overlay
│   ├── control_panel.py         # Main settings & control window
│   ├── camera_window.py         # Camera preview, vision analysis, gesture config
│   ├── memory_window.py         # Memory manager
│   ├── tray.py                  # System tray icon
│   └── setup_dialog.py          # First-launch wizard
└── requirements.txt
```

---

## API Keys & Privacy

- Your **Groq API key** is stored locally in `%APPDATA%\Jarvis\config.json` — never uploaded anywhere
- Voice recordings are sent **directly** to the Groq API (Whisper) for transcription — only when you speak
- Jarvis itself collects **no** usage data and sends **no** telemetry
- Memory is stored locally at `%APPDATA%\Jarvis\memories.json`
- If you don't want to use any cloud: set up Ollama as a fully local alternative

---

## Known Limitations

- Some voice commands may be misunderstood — speak clearly and directly
- The wake word works best in a quiet environment
- Local wake word requires `openwakeword` to be installed separately
- Gesture control requires `mediapipe` and a working webcam
- Some terminal commands may require admin rights

---

## Contributors

| Name | GitHub | Role |
|------|--------|------|
| Jakub Möslinger | [@jmoeslinger](https://github.com/jmoeslinger) | Creator & Lead Developer |
| Claude (Anthropic) | — | Autonomous debugging & feature development (v0.1.10 – v0.3.0) |

Full list: [CONTRIBUTORS.md](CONTRIBUTORS.md)

---

## License & Legal

This project is licensed under the **MIT License** — you are free to use, share, modify and incorporate it into your own projects. See [LICENSE](LICENSE) for details.

By using Jarvis you agree to the [Terms of Use](TERMS.md).

---

## Acknowledgements

- [Groq](https://groq.com) — lightning-fast LLM inference, free tier available
- [MediaPipe](https://developers.google.com/mediapipe) — hand gesture recognition (Google)
- [OpenWakeWord](https://github.com/dscripka/openWakeWord) — local wake word detection
- [Resemblyzer](https://github.com/resemble-ai/Resemblyzer) — speaker recognition
- [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) — modern GUI
- [edge-tts](https://github.com/rany2/edge-tts) — natural text-to-speech

---

## Changelog

| Version | Highlights |
|---------|-----------|
| **v0.3.0** | Video analysis, gesture control (MediaPipe), camera grid collage |
| v0.2.8 | Thread-safety fixes: atomic settings save, DDG search hang, HUD connectivity thread |
| v0.2.x | Day planner, recurring tasks, adaptive responses, style learning, noise filter, personality presets, multi-step reasoning, conversation resume, long-term context |
| v0.2.0 | Camera/Vision feature, screenshot & camera analysis, Gemini support |
| v0.1.x | Initial release: voice control, Groq AI, memory, timers, web search, app control |

---

*Version v0.3.0 — built with too much coffee and too little sleep*
