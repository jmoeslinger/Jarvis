import logging
import threading
from datetime import datetime
from typing import Optional

import customtkinter as ctk

from core.jarvis import JarvisCore, State

logger = logging.getLogger("jarvis.chat")

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

_STATUS_COLOR = {
    State.IDLE:            "#555566",
    State.WAKE_LISTENING:  "#2266DD",
    State.CMD_LISTENING:   "#22AA66",
    State.PROCESSING:      "#CC9900",
    State.SPEAKING:        "#22AA66",
    State.ERROR:           "#CC2233",
}

_STATUS_TEXT = {
    State.IDLE:            "●  Inaktiv",
    State.WAKE_LISTENING:  "●  Hoert auf Jarvis...",
    State.CMD_LISTENING:   "●  Nimmt Befehl auf...",
    State.PROCESSING:      "●  Verarbeitet...",
    State.SPEAKING:        "●  Spricht...",
    State.ERROR:           "●  Fehler",
}

_ROLE_CONFIG = {
    "user": {
        "label":       "Sie",
        "label_color": "#7799CC",
        "bubble_bg":   "#1A3A6A",
        "text_color":  "#E8E8F0",
        "anchor":      "e",
    },
    "assistant": {
        "label":       "Jarvis",
        "label_color": "#5588EE",
        "bubble_bg":   "#141428",
        "text_color":  "#D8D8E8",
        "anchor":      "w",
    },
    "system": {
        "label":       "System",
        "label_color": "#445566",
        "bubble_bg":   "#1A1A22",
        "text_color":  "#666677",
        "anchor":      "center",
    },
}


class _MessageBubble(ctk.CTkFrame):
    def __init__(self, parent, role: str, content: str, timestamp: str):
        super().__init__(parent, fg_color="transparent")

        cfg = _ROLE_CONFIG.get(role, _ROLE_CONFIG["system"])
        is_center = cfg["anchor"] == "center"

        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.pack(
            fill="x",
            padx=12,
            pady=(3, 1),
            anchor=cfg["anchor"] if not is_center else "center",
        )

        header = ctk.CTkFrame(outer, fg_color="transparent")
        header.pack(fill="x")

        ctk.CTkLabel(
            header,
            text=cfg["label"],
            text_color=cfg["label_color"],
            font=ctk.CTkFont(size=11, weight="bold"),
        ).pack(side="right" if cfg["anchor"] == "e" else "left")

        ctk.CTkLabel(
            header,
            text=timestamp,
            text_color="#333344",
            font=ctk.CTkFont(size=10),
        ).pack(
            side="left" if cfg["anchor"] == "e" else "right",
            padx=6,
        )

        bubble = ctk.CTkFrame(outer, fg_color=cfg["bubble_bg"], corner_radius=14)
        bubble.pack(anchor=cfg["anchor"], pady=(2, 0))

        self._content_label = ctk.CTkLabel(
            bubble,
            text=content,
            wraplength=420,
            justify="left",
            text_color=cfg["text_color"],
            font=ctk.CTkFont(size=13),
            padx=14,
            pady=10,
        )
        self._content_label.pack()


class ChatWindow:
    def __init__(self, jarvis: JarvisCore):
        self._jarvis = jarvis
        self._root: Optional[ctk.CTk] = None
        self._scroll: Optional[ctk.CTkScrollableFrame] = None
        self._status_lbl: Optional[ctk.CTkLabel] = None
        self._input: Optional[ctk.CTkEntry] = None
        self._ready = threading.Event()
        self._partial_bubble: Optional[_MessageBubble] = None   # aktuelle Streaming-Bubble
        self._partial_label:  Optional[ctk.CTkLabel] = None     # Label darin zum Updaten

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def run(self):
        self._root = ctk.CTk()
        self._root.title("Jarvis")
        self._root.geometry("620x780")
        self._root.minsize(420, 500)
        self._root.configure(fg_color="#08080F")

        self._build_ui()
        self._register_callbacks()
        self._root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._ready.set()
        self._root.mainloop()

    def show(self):
        self._ready.wait(timeout=3)
        if self._root:
            try:
                self._root.after(0, self._root.deiconify)
                self._root.after(0, self._root.lift)
            except Exception:
                pass

    def is_alive(self) -> bool:
        try:
            return self._root is not None and bool(self._root.winfo_exists())
        except Exception:
            return False

    def clear_messages(self):
        if self._scroll and self._root:
            self._root.after(0, self._do_clear)

    def _do_clear(self):
        if self._scroll:
            for w in self._scroll.winfo_children():
                w.destroy()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        # ── Header ───────────────────────────────────────────────────
        header = ctk.CTkFrame(self._root, height=64, fg_color="#0C0C1A", corner_radius=0)
        header.pack(fill="x")
        header.pack_propagate(False)

        ctk.CTkLabel(
            header,
            text=" J A R V I S",
            font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
            text_color="#3366FF",
        ).pack(side="left", padx=22, pady=14)

        self._status_lbl = ctk.CTkLabel(
            header,
            text="●  Hoert auf Jarvis...",
            font=ctk.CTkFont(size=12),
            text_color="#2266DD",
        )
        self._status_lbl.pack(side="right", padx=22)

        # Divider
        ctk.CTkFrame(self._root, height=1, fg_color="#18182A", corner_radius=0).pack(fill="x")

        # ── Chat-Bereich ──────────────────────────────────────────────
        self._scroll = ctk.CTkScrollableFrame(
            self._root,
            fg_color="#08080F",
            scrollbar_button_color="#1E1E38",
            scrollbar_button_hover_color="#2A2A50",
            corner_radius=0,
        )
        self._scroll.pack(fill="both", expand=True)

        # Divider
        ctk.CTkFrame(self._root, height=1, fg_color="#18182A", corner_radius=0).pack(fill="x")

        # ── Eingabe ───────────────────────────────────────────────────
        bar = ctk.CTkFrame(self._root, height=72, fg_color="#0C0C1A", corner_radius=0)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        self._input = ctk.CTkEntry(
            bar,
            placeholder_text="Nachricht eingeben...",
            height=44,
            font=ctk.CTkFont(size=13),
            fg_color="#141428",
            border_color="#222244",
            border_width=1,
            text_color="#D8D8F0",
            placeholder_text_color="#404055",
            corner_radius=10,
        )
        self._input.pack(side="left", fill="x", expand=True, padx=(16, 8), pady=14)
        self._input.bind("<Return>", self._on_send)

        send_btn = ctk.CTkButton(
            bar,
            text="↑",
            width=44,
            height=44,
            font=ctk.CTkFont(size=18, weight="bold"),
            fg_color="#1A3A8A",
            hover_color="#2244AA",
            corner_radius=10,
            command=self._on_send,
        )
        send_btn.pack(side="right", padx=(0, 16), pady=14)

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def _register_callbacks(self):
        self._jarvis.on_state_change(self._on_state)
        self._jarvis.on_message(self._on_message)

    def _on_state(self, state: State):
        if not self.is_alive() or not self._status_lbl:
            return
        color = _STATUS_COLOR.get(state, "#555566")
        text  = _STATUS_TEXT.get(state, "Jarvis")
        def _apply(t=text, c=color):
            try:
                if self._status_lbl and self._status_lbl.winfo_exists():
                    self._status_lbl.configure(text=t, text_color=c)
            except Exception:
                pass
        self._root.after(0, _apply)

    def _on_message(self, role: str, content: str):
        if not self.is_alive():
            return
        self._root.after(0, lambda: self._handle_message(role, content))

    def _handle_message(self, role: str, content: str):
        """Wird im Tkinter-Haupt-Thread ausgeführt."""
        # Interne Rollen die nicht im Chat angezeigt werden sollen
        if role in ("confidence",):
            return

        try:
            if role == "assistant_partial":
                # Streaming: letzte Bubble aktualisieren statt neue zu erstellen
                if self._partial_label and self._partial_label.winfo_exists():
                    preview = content[:400] + ("..." if len(content) > 400 else "")
                    self._partial_label.configure(text=preview)
                else:
                    # Erste Partial-Nachricht → neue Bubble anlegen
                    self._partial_bubble, self._partial_label = self._add_bubble(
                        "assistant", content[:400], streaming=True
                    )
                self._scroll_to_bottom()
                return

            if role == "assistant":
                # Finale Antwort: Partial-Bubble updaten oder neue anlegen
                if self._partial_label and self._partial_label.winfo_exists():
                    self._partial_label.configure(text=content)
                    self._partial_bubble  = None
                    self._partial_label   = None
                else:
                    self._partial_bubble = None
                    self._partial_label  = None
                    self._add_bubble("assistant", content)
                self._scroll_to_bottom()
                return

            # user / system: Streaming-Bubble abschließen, neue anlegen
            self._partial_bubble = None
            self._partial_label  = None
            self._add_bubble(role, content)
            self._scroll_to_bottom()
        except Exception as e:
            logger.debug(f"_handle_message Fehler (Fenster ggf. geschlossen): {e}")

    def _add_bubble(self, role: str, content: str, streaming: bool = False):
        """Erstellt eine neue Nachrichtenblase. Gibt (bubble, label) zurück wenn streaming=True."""
        ts = datetime.now().strftime("%H:%M")
        bubble = _MessageBubble(self._scroll, role=role, content=content, timestamp=ts)
        bubble.pack(fill="x", pady=2)
        if streaming:
            # Gibt das Label zurück damit es direkt aktualisiert werden kann
            return bubble, bubble._content_label
        return bubble, None

    def _scroll_to_bottom(self):
        def _do_scroll():
            try:
                if self._scroll and self._scroll.winfo_exists():
                    self._scroll._parent_canvas.yview_moveto(1.0)
            except Exception:
                pass
        self._root.after(120, _do_scroll)

    def _on_send(self, _event=None):
        text = (self._input.get() or "").strip()
        if not text:
            return
        self._input.delete(0, "end")
        self._jarvis.send_text_command(text)

    def _on_close(self):
        # destroy() beendet die mainloop → Thread kann neu gestartet werden
        # withdraw() würde den Thread am Leben lassen und erneutes Öffnen verhindern
        try:
            self._root.destroy()
        except Exception:
            pass
