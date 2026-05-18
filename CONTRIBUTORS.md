# Contributors — Jarvis

Thanks to everyone who helped make Jarvis possible.

---

## 👨‍💻 Lead Developer

| Name | GitHub | Role |
|------|--------|------|
| Jakub Möslinger | [@jmoeslinger](https://github.com/jmoeslinger) | Creator, Developer, Project Lead |

---

## 🤖 AI Assistance

| Name | Period | Role |
|------|--------|------|
| Claude (Anthropic) | v0.1.10 – v0.3.0 | Autonomous debugging, bug fixes & feature development |

### What Claude contributed

**Autonomous Debugging (v0.1.10 – v0.2.8)**
> Claude systematically reviewed all Python files across multiple autonomous debug sessions, identified bugs and implemented fixes — without Jakub having to describe each fix individually.
> Over 72 bugs were found and resolved (BUG-001 through BUG-072), including:
> - Thread-safety issues (tkinter, locks, events)
> - Atomic config file writes (crash-safe persistence)
> - Hangs caused by incorrect executor usage
> - Race conditions in background threads
> - Memory leaks and missing `release()` calls

**Feature Development (v0.3.0)**
> Based on Jakub's ideas, Claude designed and implemented two complete new features:
> - **Video analysis**: capture multiple frames → stitch into grid collage → Vision AI analysis
> - **Gesture control**: real-time hand detection via MediaPipe with hold-to-trigger,
>   configurable gesture→command mappings, live overlay in the camera window

---

## 🙌 How to contribute

Pull requests are very welcome! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Commit your changes (`v0.x.x: short description`)
4. Open a pull request

For bugs or ideas: just [open an issue](https://github.com/jmoeslinger/Jarvis/issues).
