"""
Memory-Fenster fuer Jarvis.
Laeuft als eigenstaendiger Prozess (via memory_app.py).
Zeigt alle Eintraege, Suche, Kategorie-Filter, Bearbeiten, Loeschen, Neu.
"""

import customtkinter as ctk

from services.memory.memory_store import (
    CATEGORY_LABELS,
    VALID_CATEGORIES,
    MemoryStore,
)

# ── Design-Konstanten ──────────────────────────────────────────────────────────
_BG     = "#1a1a2e"
_PANEL  = "#16213e"
_CARD   = "#0f3460"
_ACCENT = "#e94560"
_ACC2   = "#533483"
_TEXT   = "#eaeaea"
_SUB    = "#8899aa"
_BORDER = "#2a3a5a"

_CAT_COLOR = {
    "facts":       "#3a7bd5",
    "preferences": "#e94560",
    "tasks":       "#00b894",
    "general":     "#6c757d",
}


def _prio_color(p: int) -> str:
    if p <= 3:  return "#6c757d"
    if p <= 6:  return "#f39c12"
    return "#e94560"


def _prio_stars(p: int) -> str:
    filled = round(p / 2)
    return "★" * filled + "☆" * (5 - filled)


# ── Haupt-Fenster ──────────────────────────────────────────────────────────────

class MemoryWindow:
    """
    Erstellt und verwaltet das Memory-Fenster.
    run() blockiert bis das Fenster geschlossen wird.
    """

    def __init__(self, memory: MemoryStore):
        self._mem        = memory
        self._filter_cat = "all"
        self._search_txt = ""
        self._root: ctk.CTk | None = None

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def run(self):
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self._root = ctk.CTk()
        self._root.title("Jarvis — Memory")
        self._root.geometry("860x640")
        self._root.minsize(660, 480)
        self._root.configure(fg_color=_BG)

        # Windows-Taskleisten-Icon
        try:
            self._root.iconbitmap(default="")
        except Exception:
            pass

        self._build_ui()
        self._refresh()
        self._root.mainloop()

    # ── UI aufbauen ────────────────────────────────────────────────────────────

    def _build_ui(self):
        r = self._root

        # ── Kopfzeile ──────────────────────────────────────────────────────────
        header = ctk.CTkFrame(r, fg_color=_PANEL, corner_radius=0, height=58)
        header.pack(fill="x")
        header.pack_propagate(False)

        ctk.CTkLabel(
            header, text="🧠  Jarvis Memory",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=_TEXT,
        ).pack(side="left", padx=20, pady=10)

        self._count_lbl = ctk.CTkLabel(
            header, text="",
            font=ctk.CTkFont(size=12),
            text_color=_SUB,
        )
        self._count_lbl.pack(side="left", padx=4, pady=10)

        ctk.CTkButton(
            header, text="＋  Neuer Eintrag",
            fg_color=_ACCENT, hover_color="#c73652",
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=8, width=155, height=34,
            command=self._dlg_edit,
        ).pack(side="right", padx=16, pady=12)

        # ── Toolbar ────────────────────────────────────────────────────────────
        toolbar = ctk.CTkFrame(r, fg_color=_PANEL, corner_radius=0, height=48)
        toolbar.pack(fill="x")
        toolbar.pack_propagate(False)

        self._search_var = ctk.StringVar()
        ctk.CTkEntry(
            toolbar, textvariable=self._search_var,
            placeholder_text="🔍  Suchen…",
            fg_color=_CARD, border_color=_BORDER, text_color=_TEXT,
            font=ctk.CTkFont(size=13), width=230, height=30, corner_radius=7,
        ).pack(side="left", padx=14, pady=9)
        self._search_var.trace_add("write", lambda *_: self._on_search())

        # Kategorie-Buttons
        self._cat_btns: dict[str, ctk.CTkButton] = {}
        cats = [("all", "Alle")] + [(k, CATEGORY_LABELS[k]) for k in
                ("facts", "preferences", "tasks", "general")]
        for cid, label in cats:
            b = ctk.CTkButton(
                toolbar, text=label,
                fg_color=_ACCENT if cid == "all" else _CARD,
                hover_color=_ACC2, text_color=_TEXT,
                font=ctk.CTkFont(size=12),
                corner_radius=6, width=10, height=28,
                command=lambda c=cid: self._set_cat(c),
            )
            b.pack(side="left", padx=3, pady=10)
            self._cat_btns[cid] = b

        # ── Liste ──────────────────────────────────────────────────────────────
        self._scroll = ctk.CTkScrollableFrame(
            r, fg_color=_BG, corner_radius=0,
            scrollbar_button_color=_BORDER,
            scrollbar_button_hover_color=_ACCENT,
        )
        self._scroll.pack(fill="both", expand=True, padx=10, pady=(6, 0))

        # ── Statusleiste ───────────────────────────────────────────────────────
        self._status = ctk.CTkLabel(
            r, text="",
            font=ctk.CTkFont(size=11),
            text_color=_SUB,
            fg_color=_PANEL,
            height=22,
            anchor="w",
        )
        self._status.pack(fill="x", side="bottom")

    # ── Filter-Logik ───────────────────────────────────────────────────────────

    def _set_cat(self, cat: str):
        self._filter_cat = cat
        for cid, b in self._cat_btns.items():
            b.configure(fg_color=_ACCENT if cid == cat else _CARD)
        self._refresh()

    def _on_search(self):
        self._search_txt = self._search_var.get().lower()
        self._refresh()

    # ── Liste neu aufbauen ─────────────────────────────────────────────────────

    def _refresh(self):
        for w in self._scroll.winfo_children():
            w.destroy()

        entries = self._mem.get_all()
        total   = len(entries)

        if self._filter_cat != "all":
            entries = [e for e in entries if e.category == self._filter_cat]
        if self._search_txt:
            entries = [e for e in entries if self._search_txt in e.content.lower()]

        entries = sorted(entries, key=lambda e: (e.priority, e.updated_at), reverse=True)

        self._count_lbl.configure(text=f"{total} Einträge")

        if not entries:
            msg = ("Keine Einträge gefunden."
                   if (self._search_txt or self._filter_cat != "all")
                   else 'Noch keine Erinnerungen.\nSag z.B. „Merk dir, ich heiße Max".')
            ctk.CTkLabel(
                self._scroll, text=msg,
                font=ctk.CTkFont(size=14), text_color=_SUB, justify="center",
            ).pack(pady=50)
        else:
            for e in entries:
                self._make_card(e)

        self._status.configure(
            text=f"   {len(entries)} von {total} Einträgen angezeigt"
        )

    # ── Eintragskarte ──────────────────────────────────────────────────────────

    def _make_card(self, entry):
        cc = _CAT_COLOR.get(entry.category, "#6c757d")
        pc = _prio_color(entry.priority)

        # äußerer Rahmen
        outer = ctk.CTkFrame(self._scroll, fg_color=_BORDER, corner_radius=10)
        outer.pack(fill="x", pady=3, padx=2)

        # innerer Rahmen (1px Abstand ergibt border-Effekt)
        inner = ctk.CTkFrame(outer, fg_color=_CARD, corner_radius=9)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        # Farbstreifen links
        ctk.CTkFrame(inner, fg_color=cc, width=5, corner_radius=0).pack(
            side="left", fill="y"
        )

        # Inhalt-Bereich
        mid = ctk.CTkFrame(inner, fg_color="transparent")
        mid.pack(side="left", fill="both", expand=True, padx=10, pady=8)

        # Zeile 1: Kategorie-Label, Sterne, Datum
        top = ctk.CTkFrame(mid, fg_color="transparent")
        top.pack(fill="x")

        ctk.CTkLabel(
            top, text=CATEGORY_LABELS.get(entry.category, entry.category),
            font=ctk.CTkFont(size=11, weight="bold"), text_color=cc,
        ).pack(side="left")

        ctk.CTkLabel(
            top, text="  " + _prio_stars(entry.priority),
            font=ctk.CTkFont(size=10), text_color=pc,
        ).pack(side="left")

        ctk.CTkLabel(
            top, text="  " + entry.updated_at[:10],
            font=ctk.CTkFont(size=10), text_color=_SUB,
        ).pack(side="left")

        # Zeile 2: Inhalt
        ctk.CTkLabel(
            mid, text=entry.content,
            font=ctk.CTkFont(size=13), text_color=_TEXT,
            anchor="w", justify="left", wraplength=500,
        ).pack(fill="x", anchor="w", pady=(2, 0))

        # Buttons rechts
        btns = ctk.CTkFrame(inner, fg_color="transparent")
        btns.pack(side="right", padx=10)

        ctk.CTkButton(
            btns, text="✏", width=34, height=30, corner_radius=6,
            fg_color=_ACC2, hover_color="#7048a8",
            font=ctk.CTkFont(size=14),
            command=lambda e=entry: self._dlg_edit(e),
        ).pack(pady=(0, 4))

        ctk.CTkButton(
            btns, text="🗑", width=34, height=30, corner_radius=6,
            fg_color="#3d1515", hover_color="#7d2020",
            font=ctk.CTkFont(size=14),
            command=lambda e=entry: self._dlg_delete(e),
        ).pack()

    # ── Dialog: Bearbeiten / Neu ───────────────────────────────────────────────

    def _dlg_edit(self, entry=None):
        d = ctk.CTkToplevel(self._root)
        d.title("Eintrag bearbeiten" if entry else "Neuer Eintrag")
        d.geometry("520x400")
        d.resizable(False, False)
        d.configure(fg_color=_PANEL)
        d.grab_set()
        d.focus_force()
        d.lift()

        def _section(label):
            ctk.CTkLabel(
                d, text=label,
                font=ctk.CTkFont(size=13, weight="bold"), text_color=_TEXT,
            ).pack(anchor="w", padx=22, pady=(16, 3))

        _section("Inhalt:")
        content_var = ctk.StringVar(value=entry.content if entry else "")
        ctk.CTkEntry(
            d, textvariable=content_var,
            fg_color=_CARD, border_color=_BORDER, text_color=_TEXT,
            font=ctk.CTkFont(size=13), height=36, corner_radius=8,
        ).pack(fill="x", padx=22)

        _section("Kategorie:")
        cat_var = ctk.StringVar(value=entry.category if entry else "general")
        cat_row = ctk.CTkFrame(d, fg_color="transparent")
        cat_row.pack(fill="x", padx=22)
        for cid in ("facts", "preferences", "tasks", "general"):
            ctk.CTkRadioButton(
                cat_row, text=CATEGORY_LABELS[cid],
                variable=cat_var, value=cid,
                fg_color=_ACCENT, hover_color=_ACC2,
                text_color=_TEXT, font=ctk.CTkFont(size=12),
            ).pack(side="left", padx=6)

        _section("Priorität (0 – 10):")
        prio_var = ctk.IntVar(value=entry.priority if entry else 5)
        prow = ctk.CTkFrame(d, fg_color="transparent")
        prow.pack(fill="x", padx=22)

        plbl = ctk.CTkLabel(
            prow, text=str(prio_var.get()),
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=_prio_color(prio_var.get()), width=30,
        )
        plbl.pack(side="left")

        def _slide(v):
            iv = int(round(float(v)))
            prio_var.set(iv)
            plbl.configure(text=str(iv), text_color=_prio_color(iv))

        ctk.CTkSlider(
            prow, from_=0, to=10, number_of_steps=10,
            variable=prio_var,
            fg_color=_BORDER, progress_color=_ACCENT,
            button_color=_ACCENT, button_hover_color="#c73652",
            command=_slide,
        ).pack(side="left", fill="x", expand=True, padx=(8, 0))

        # Buttons
        brow = ctk.CTkFrame(d, fg_color="transparent")
        brow.pack(fill="x", padx=22, pady=(22, 12))

        def _save():
            txt = content_var.get().strip()
            if not txt:
                return
            if entry:
                self._mem.update_entry(entry.id, txt, cat_var.get(), prio_var.get())
            else:
                self._mem.add_entry(txt, cat_var.get(), prio_var.get())
            d.destroy()
            self._refresh()

        ctk.CTkButton(
            brow, text="Speichern",
            fg_color=_ACCENT, hover_color="#c73652",
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=8, height=36,
            command=_save,
        ).pack(side="left", fill="x", expand=True, padx=(0, 6))

        ctk.CTkButton(
            brow, text="Abbrechen",
            fg_color=_CARD, hover_color=_BORDER,
            font=ctk.CTkFont(size=13),
            corner_radius=8, height=36,
            command=d.destroy,
        ).pack(side="left", fill="x", expand=True, padx=(6, 0))

        d.bind("<Return>", lambda _: _save())
        d.bind("<Escape>", lambda _: d.destroy())

    # ── Dialog: Loeschen ───────────────────────────────────────────────────────

    def _dlg_delete(self, entry):
        d = ctk.CTkToplevel(self._root)
        d.title("Eintrag löschen")
        d.geometry("420x210")
        d.resizable(False, False)
        d.configure(fg_color=_PANEL)
        d.grab_set()
        d.focus_force()
        d.lift()

        ctk.CTkLabel(
            d, text="Eintrag wirklich löschen?",
            font=ctk.CTkFont(size=15, weight="bold"), text_color=_TEXT,
        ).pack(pady=(26, 6))

        preview = entry.content[:65] + ("…" if len(entry.content) > 65 else "")
        ctk.CTkLabel(
            d, text=f'„{preview}"',
            font=ctk.CTkFont(size=12), text_color=_SUB, wraplength=380,
        ).pack(padx=20)

        brow = ctk.CTkFrame(d, fg_color="transparent")
        brow.pack(fill="x", padx=24, pady=(22, 12))

        def _do():
            self._mem.delete_entry(entry.id)
            d.destroy()
            self._refresh()

        ctk.CTkButton(
            brow, text="🗑  Löschen",
            fg_color="#7d2020", hover_color="#a02828",
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=8, height=36, command=_do,
        ).pack(side="left", fill="x", expand=True, padx=(0, 6))

        ctk.CTkButton(
            brow, text="Abbrechen",
            fg_color=_CARD, hover_color=_BORDER,
            font=ctk.CTkFont(size=13), corner_radius=8, height=36,
            command=d.destroy,
        ).pack(side="left", fill="x", expand=True, padx=(6, 0))

        d.bind("<Escape>", lambda _: d.destroy())
        d.bind("<Return>",  lambda _: _do())
