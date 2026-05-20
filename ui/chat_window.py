import logging
import threading
from datetime import datetime
from typing import Optional

import customtkinter as ctk

from core.jarvis import JarvisCore, State

logger = logging.getLogger("jarvis.chat")

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ── Cyberpunk palette (matches HUD / ControlPanel) ────────────────────────────
_C_BG     = "#030305"
_C_CARD   = "#060810"
_C_BORDER = "#0A1828"
_C_CYAN   = "#00BFFF"
_C_GREEN  = "#00FF88"
_C_ORANGE = "#FF9500"
_C_RED    = "#FF2060"
_C_TEXT   = "#C8D8F0"
_C_DIM    = "#1A3A50"
_C_DIMMED = "#3A7090"

_STATUS_COLOR = {
    State.IDLE:           _C_DIMMED,
    State.WAKE_LISTENING: _C_CYAN,
    State.CMD_LISTENING:  _C_GREEN,
    State.PROCESSING:     _C_ORANGE,
    State.SPEAKING:       _C_GREEN,
    State.ERROR:          _C_RED,
}

_STATUS_TEXT = {
    State.IDLE:           "◆  STANDBY",
    State.WAKE_LISTENING: "◆  LISTENING",
    State.CMD_LISTENING:  "◆  RECORDING",
    State.PROCESSING:     "◆  THINKING",
    State.SPEAKING:       "◆  SPEAKING",
    State.ERROR:          "◆  ERROR",
}

_ROLE_CONFIG = {
    "user": {
        "label":       "YOU",
        "label_color": _C_ORANGE,
        "bubble_bg":   "#0A1220",
        "bubble_border": "#0F1E38",
        "text_color":  _C_TEXT,
        "anchor":      "e",
        "bar_color":   _C_ORANGE,
    },
    "assistant": {
        "label":       "JRV",
        "label_color": _C_CYAN,
        "bubble_bg":   "#050A12",
        "bubble_border": "#0A1828",
        "text_color":  "#B8D8E8",
        "anchor":      "w",
        "bar_color":   _C_CYAN,
    },
    "system": {
        "label":       "SYS",
        "label_color": _C_DIM,
        "bubble_bg":   "#030508",
        "bubble_border": "#080C14",
        "text_color":  _C_DIM,
        "anchor":      "center",
        "bar_color":   _C_DIM,
    },
}


class _MessageBubble(ctk.CTkFrame):
    def __init__(self, parent, role: str, content: str, timestamp: str):
        super().__init__(parent, fg_color="transparent")

        cfg = _ROLE_CONFIG.get(role, _ROLE_CONFIG["system"])
        is_center = cfg["anchor"] == "center"
        is_right  = cfg["anchor"] == "e"

        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.pack(
            fill="x",
            padx=10,
            pady=(2, 1),
            anchor="center" if is_center else cfg["anchor"],
        )

        # Header row
        header = ctk.CTkFrame(outer, fg_color="transparent")
        header.pack(fill="x")

        role_lbl = ctk.CTkLabel(
            header,
            text=cfg["label"],
            text_color=cfg["label_color"],
            font=ctk.CTkFont(family="Consolas", size=9, weight="bold"),
        )
        ts_lbl = ctk.CTkLabel(
            header,
            text=timestamp,
            text_color=_C_DIM,
            font=ctk.CTkFont(family="Consolas", size=8),
        )

        if is_right:
            ts_lbl.pack(side="left")
            role_lbl.pack(side="right")
        else:
            role_lbl.pack(side="left")
            ts_lbl.pack(side="left", padx=(6, 0))

        # Accent bar (thin colored line left/right of bubble)
        bubble_wrapper = ctk.CTkFrame(outer, fg_color="transparent")
        bubble_wrapper.pack(anchor=cfg["anchor"], pady=(2, 0))

        if not is_center:
            bar = ctk.CTkFrame(bubble_wrapper, width=2, corner_radius=0,
                               fg_color=cfg["bar_color"])
            bar.pack(side="right" if is_right else "left", fill="y")

        bubble = ctk.CTkFrame(bubble_wrapper,
                              fg_color=cfg["bubble_bg"],
                              border_width=1,
                              border_color=cfg["bubble_border"],
                              corner_radius=6)
        bubble.pack(side="right" if is_right else "left",
                    padx=(0 if is_right else 4, 4 if is_right else 0))

        self._content_label = ctk.CTkLabel(
            bubble,
            text=content,
            wraplength=380,
            justify="left",
            text_color=cfg["text_color"],
            font=ctk.CTkFont(family="Consolas", size=11),
            padx=12,
            pady=8,
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
        self._partial_bubble: Optional[_MessageBubble] = None
        self._partial_label:  Optional[ctk.CTkLabel] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def run(self):
        self._root = ctk.CTk()
        self._root.title("Jarvis")
        self._root.geometry("560x720")
        self._root.minsize(400, 480)
        self._root.configure(fg_color=_C_BG)

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
        # BUG-096: winfo_exists() ist nicht thread-sicher. Diese Methode wird
        # aus Background-Threads aufgerufen (_on_state, _on_message). Als
        # thread-sicherer Proxy prüfen wir nur ob _root gesetzt ist — der Tk-
        # interne Zustand bleibt unberührt. after() prüft dann selbst ob das
        # Widget noch existiert.
        try:
            return self._root is not None
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
        # ── Top accent line ───────────────────────────────────────────
        ctk.CTkFrame(self._root, height=2, corner_radius=0,
                     fg_color=_C_CYAN).pack(fill="x")

        # ── Header ───────────────────────────────────────────────────
        header = ctk.CTkFrame(self._root, height=48, fg_color=_C_CARD,
                              corner_radius=0)
        header.pack(fill="x")
        header.pack_propagate(False)

        ctk.CTkLabel(
            header,
            text="JRV//CHAT",
            font=ctk.CTkFont(family="Consolas", size=14, weight="bold"),
            text_color=_C_CYAN,
        ).pack(side="left", padx=14, pady=10)

        self._status_lbl = ctk.CTkLabel(
            header,
            text="◆  LISTENING",
            font=ctk.CTkFont(family="Consolas", size=9),
            text_color=_C_DIM,
        )
        self._status_lbl.pack(side="right", padx=14)

        # Divider
        ctk.CTkFrame(self._root, height=1, fg_color=_C_BORDER,
                     corner_radius=0).pack(fill="x")

        # ── Chat area ────────────────────────────────────────────────
        self._scroll = ctk.CTkScrollableFrame(
            self._root,
            fg_color=_C_BG,
            scrollbar_button_color=_C_BORDER,
            scrollbar_button_hover_color=_C_DIMMED,
            corner_radius=0,
        )
        self._scroll.pack(fill="both", expand=True)

        # Divider
        ctk.CTkFrame(self._root, height=1, fg_color=_C_BORDER,
                     corner_radius=0).pack(fill="x")

        # ── Input bar ────────────────────────────────────────────────
        bar = ctk.CTkFrame(self._root, height=56, fg_color=_C_CARD,
                           corner_radius=0)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        # Prompt indicator
        ctk.CTkLabel(
            bar,
            text=">",
            font=ctk.CTkFont(family="Consolas", size=13, weight="bold"),
            text_color=_C_CYAN,
        ).pack(side="left", padx=(14, 0))

        self._input = ctk.CTkEntry(
            bar,
            placeholder_text="enter command...",
            height=34,
            font=ctk.CTkFont(family="Consolas", size=11),
            fg_color=_C_BG,
            border_color=_C_BORDER,
            border_width=1,
            text_color=_C_TEXT,
            placeholder_text_color=_C_DIM,
            corner_radius=3,
        )
        self._input.pack(side="left", fill="x", expand=True, padx=(6, 6), pady=11)
        self._input.bind("<Return>", self._on_send)

        send_btn = ctk.CTkButton(
            bar,
            text="▶",
            width=34,
            height=34,
            font=ctk.CTkFont(family="Consolas", size=12, weight="bold"),
            fg_color=_C_BG,
            hover_color="#001A28",
            border_width=1,
            border_color=_C_CYAN,
            text_color=_C_CYAN,
            corner_radius=3,
            command=self._on_send,
        )
        send_btn.pack(side="right", padx=(0, 14), pady=11)

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def _register_callbacks(self):
        self._jarvis.on_state_change(self._on_state)
        self._jarvis.on_message(self._on_message)

    def _on_state(self, state: State):
        if not self.is_alive() or not self._status_lbl:
            return
        color = _STATUS_COLOR.get(state, _C_DIMMED)
        text  = _STATUS_TEXT.get(state, "◆  JARVIS")
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
        if role in ("confidence",):
            return

        try:
            if role == "assistant_partial":
                if self._partial_label and self._partial_label.winfo_exists():
                    preview = content[:400] + ("..." if len(content) > 400 else "")
                    self._partial_label.configure(text=preview)
                else:
                    self._partial_bubble, self._partial_label = self._add_bubble(
                        "assistant", content[:400], streaming=True
                    )
                self._scroll_to_bottom()
                return

            if role == "assistant":
                if self._partial_label and self._partial_label.winfo_exists():
                    self._partial_label.configure(text=content)
                    self._partial_bubble = None
                    self._partial_label  = None
                else:
                    self._partial_bubble = None
                    self._partial_label  = None
                    self._add_bubble("assistant", content)
                self._scroll_to_bottom()
                return

            self._partial_bubble = None
            self._partial_label  = None
            self._add_bubble(role, content)
            self._scroll_to_bottom()
        except Exception as e:
            logger.debug(f"_handle_message error: {e}")

    def _add_bubble(self, role: str, content: str, streaming: bool = False):
        """Erstellt eine neue Nachrichtenblase."""
        ts = datetime.now().strftime("%H:%M")
        bubble = _MessageBubble(self._scroll, role=role, content=content, timestamp=ts)
        bubble.pack(fill="x", pady=2)
        if streaming:
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
        try:
            self._root.destroy()
        except Exception:
            pass
