# Contributors — Jarvis

Danke an alle, die Jarvis möglich gemacht haben.

---

## 👨‍💻 Hauptentwickler

| Name | GitHub | Rolle |
|------|--------|-------|
| Jakub Möslinger | [@jmoeslinger](https://github.com/jmoeslinger) | Creator, Entwickler, Projektleiter |

---

## 🤖 KI-Unterstützung

| Name | Zeitraum | Rolle |
|------|----------|-------|
| Claude (Anthropic) | v0.1.10 – v0.3.0 | Autonomes Debugging, Bug-Fixes & Feature-Entwicklung |

### Was Claude beigetragen hat

**Autonomes Debugging (v0.1.10 – v0.2.8)**
> Claude hat in mehreren autonomen Debug-Sessions alle Python-Dateien des Projekts systematisch analysiert, Bugs aufgespürt und behoben — ohne dass Jakub jeden Fix einzeln beschreiben musste.
> Kumulativ wurden über 72 Bugs gefunden und gefixt (BUG-001 bis BUG-072), darunter:
> - Thread-Safety-Probleme (tkinter, Locks, Events)
> - Atomisches Speichern von Konfigurationsdateien
> - Hänger durch falsche Executor-Nutzung
> - Race Conditions in Hintergrund-Threads
> - Memory-Leaks und fehlende `release()`-Aufrufe

**Feature-Entwicklung (v0.3.0)**
> Claude hat auf Basis von Jakubs Ideen zwei vollständige neue Features entworfen und implementiert:
> - **Video-Analyse**: Aufnahme mehrerer Frames → Grid-Kollage → Vision-KI-Analyse
> - **Gestensteuerung**: MediaPipe-basierte Echtzeit-Handerkennung mit Hold-to-Trigger,
>   konfigurierbare Geste→Befehl-Mappings, Live-Overlay im Kamera-Fenster

---

## 🙌 Wie kann ich beitragen?

Pull Requests sind herzlich willkommen! Bitte:

1. Fork das Repository
2. Erstelle einen Feature-Branch (`git checkout -b feature/mein-feature`)
3. Committe deine Änderungen (`v0.x.x: kurze Beschreibung`)
4. Öffne einen Pull Request

Für Bugs oder Ideen: einfach ein [Issue aufmachen](https://github.com/jmoeslinger/Jarvis/issues).
