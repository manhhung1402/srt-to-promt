# -*- coding: utf-8 -*-
"""Shared model picker: searchable popup + status colours from the active theme."""
import tkinter as tk
import unicodedata
from tkinter import ttk

from ui import theme

MARKS = {"ok": "✔", "fail": "✘", "warn": "!"}
SYMBOLS = tuple(MARKS.values())
_OPEN_POPUPS = []


def fold(text):
    normalized = unicodedata.normalize("NFD", text or "")
    return "".join(c for c in normalized if not unicodedata.combining(c)).lower()


def strip_mark(text):
    text = (text or "").strip()
    for sym in SYMBOLS:
        if text == sym:
            return ""
        if text.startswith(sym + " "):
            return text[len(sym) + 1:].strip()
    return text


def _status_colors():
    return {"ok": theme.status("ok"), "fail": theme.status("fail"),
            "warn": theme.status("warn"), "": theme.status("")}


def _restyle_open_popups(mode):
    for picker in list(_OPEN_POPUPS):
        picker._restyle_popup()


theme.register_restyle_hook(_restyle_open_popups)


class ModelPicker(ttk.Combobox):
    """Ô model mở popup chọn riêng, tìm kiếm từng ký tự không trễ."""

    def __init__(self, master, status_map=None, info_label=None, on_change=None, **kw):
        super().__init__(master, **kw)
        self.status_map = status_map if status_map is not None else {}
        self.info_label = info_label
        self.on_change = on_change
        self._source = []
        self._folded = []
        self._popup = None
        self._popup_query = None
        self._popup_query_var = None
        self._popup_filter = "all"
        self._popup_list = None
        self._popup_count = None
        self.bind("<Button-1>", self._open_from_click, add="+")
        self.bind("<F4>", self._open_from_click, add="+")
        self.bind("<Escape>", self._close_popup, add="+")

    def label_of(self, model_id):
        mark = MARKS.get(self.status_map.get(model_id))
        return f"{mark} {model_id}" if mark else model_id

    def set_models(self, model_ids, select=None):
        self._source = [str(m) for m in (model_ids or [])]
        self._folded = [fold(m) for m in self._source]
        keep = self.get() if select is None else select
        self.set(strip_mark(keep))
        self._render(self._source)

    def get_models(self):
        return list(self._source)

    def refresh(self):
        current = self.model_id()
        self._render(self._source)
        if current:
            self.set(current)
        self._refresh_popup()

    def model_id(self):
        text = strip_mark(self.get())
        if not text or not self._source:
            return text
        if text in self._source:
            return text
        needle = fold(text)
        matches = [m for i, m in enumerate(self._source) if needle in self._folded[i]]
        return matches[0] if len(matches) == 1 else text

    def _match(self, query="", filter_name="all"):
        needle = fold(strip_mark(query))
        return [m for i, m in enumerate(self._source)
                if (not needle or needle in self._folded[i])
                and (filter_name == "all" or self.status_map.get(m) == filter_name)]

    def _render(self, shown):
        self["values"] = [self.label_of(m) for m in shown]
        self._set_info(len(shown))

    def filter_now(self, query):
        self._render(self._match(query))
        self._refresh_popup()

    def show_all(self, event=None):
        self._render(self._source)
        self._refresh_popup()

    def _set_info(self, shown_count):
        if self.info_label is None:
            return
        total = len(self._source)
        ok = sum(self.status_map.get(m) == "ok" for m in self._source)
        fail = sum(self.status_map.get(m) == "fail" for m in self._source)
        self.info_label.config(text=f"{shown_count} / {total} model · {ok} OK · {fail} FAIL" if total else "Chưa tải model")

    def _open_from_click(self, event=None):
        self.open_model_popup()
        return "break"

    def open_model_popup(self):
        if self._popup and self._popup.winfo_exists():
            self._popup.lift()
            self._popup_query.focus_set()
            return
        popup = tk.Toplevel(self)
        self._popup = popup
        popup.title("Chọn model / combo")
        popup.transient(self.winfo_toplevel())
        popup.resizable(True, True)
        popup.minsize(540, 420)
        popup.geometry("700x560")
        popup.protocol("WM_DELETE_WINDOW", self._close_popup)

        head = ttk.Frame(popup, style="Card.TFrame", padding=12)
        head.pack(fill="x", padx=10, pady=(10, 6))
        ttk.Label(head, text="Chọn model hoặc combo", style="Card.TLabel").pack(anchor="w")
        ttk.Label(head, text="Gõ tới đâu, danh sách lọc tới đó · nhấp đúp để chọn",
                  style="Card.Muted.TLabel").pack(anchor="w", pady=(3, 8))
        self._popup_query_var = tk.StringVar(value="")
        self._popup_query_var.trace_add("write", lambda *_: self._refresh_popup())
        self._popup_query = ttk.Entry(head, textvariable=self._popup_query_var)
        self._popup_query.pack(fill="x")
        self._popup_query.bind("<KeyRelease>", lambda e: self._refresh_popup())
        self._popup_query.bind("<Return>", lambda e: self._choose_popup())
        self._popup_query.bind("<Escape>", lambda e: self._close_popup())

        filters = ttk.Frame(popup, style="Card.TFrame", padding=(12, 4))
        filters.pack(fill="x", padx=10)
        self._popup_filter_buttons = {}
        for key, label in (("all", "Tất cả"), ("ok", "✔ OK"), ("fail", "✘ FAIL"), ("warn", "! Cảnh báo")):
            b = ttk.Button(filters, text=label, style="Primary.TButton" if key == "all" else "Ghost.TButton",
                           command=lambda k=key: self._set_popup_filter(k))
            b.pack(side="left", padx=(0, 6))
            self._popup_filter_buttons[key] = b
        self._popup_count = ttk.Label(filters, style="Card.Muted.TLabel")
        self._popup_count.pack(side="right")

        body = ttk.Frame(popup, style="Card.TFrame", padding=8)
        body.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        scroll = ttk.Scrollbar(body, orient="vertical")
        self._popup_list = tk.Listbox(body, activestyle="none", exportselection=False,
                                      font=("Segoe UI", 10), relief="flat", borderwidth=0,
                                      highlightthickness=1, yscrollcommand=scroll.set)
        scroll.config(command=self._popup_list.yview)
        self._popup_list.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self._popup_list.bind("<Double-Button-1>", lambda e: self._choose_popup())
        self._popup_list.bind("<Return>", lambda e: self._choose_popup())

        self._popup_head = head
        self._popup_filters = filters
        self._popup_body = body
        self._popup_scroll = scroll
        _OPEN_POPUPS.append(self)
        self._restyle_popup()
        self._refresh_popup()
        self._popup_query.focus_set()

    def _restyle_popup(self):
        if not (self._popup and self._popup.winfo_exists()):
            return
        # ttk frames/scrollbars follow the active ttk styles; only the raw tk
        # widgets here need explicit colours.
        c = theme._CURRENT
        self._popup.configure(background=c["surface"])
        self._popup_list.configure(background=c["card"], foreground=c["text"],
                                  selectbackground=c["primary_soft"], selectforeground=c["text"],
                                  highlightbackground=c["border"], highlightcolor=c["focus"])
        self._refresh_popup()

    def _set_popup_filter(self, key):
        self._popup_filter = key
        self._refresh_popup()

    def _refresh_popup(self):
        if not self._popup or not self._popup.winfo_exists() or not self._popup_list:
            return
        query = self._popup_query.get() if self._popup_query else ""
        shown = self._match(query, self._popup_filter)
        colors = _status_colors()
        self._popup_list.delete(0, tk.END)
        for i, model in enumerate(shown):
            self._popup_list.insert(tk.END, self.label_of(model))
            self._popup_list.itemconfig(i, foreground=colors.get(self.status_map.get(model), colors[""]))
        if self._popup_count:
            self._popup_count.config(text=f"{len(shown)} / {len(self._source)} model")
        for key, button in self._popup_filter_buttons.items():
            button.configure(style="Primary.TButton" if key == self._popup_filter else "Ghost.TButton")

    def _choose_popup(self):
        if not self._popup_list:
            return
        sel = self._popup_list.curselection()
        if not sel:
            return
        query = self._popup_query.get() if self._popup_query else ""
        shown = self._match(query, self._popup_filter)
        if sel[0] >= len(shown):
            return
        self.set(shown[sel[0]])
        self._close_popup()
        if self.on_change:
            self.on_change()

    def _close_popup(self, event=None):
        if self._popup and self._popup.winfo_exists():
            self._popup.destroy()
        self._popup = None
        self._popup_list = None
        self._popup_query = None
        self._popup_query_var = None
        self._popup_count = None
        self._popup_filter_buttons = {}
        self._popup_head = None
        self._popup_filters = None
        self._popup_body = None
        self._popup_scroll = None
        if self in _OPEN_POPUPS:
            _OPEN_POPUPS.remove(self)
        return "break"
