# -*- coding: utf-8 -*-
"""Design tokens + ttk theme. Codex-style dark/light for every widget."""
import re
import tkinter as tk
from tkinter import ttk

THEMES = {
    "dark": {
        "surface": "#151515", "panel": "#1b1b1b", "card": "#232323", "card_alt": "#2c2c2c",
        "border": "#3a3a3a", "border_strong": "#5a5a5a", "text": "#f1f1f1", "muted": "#a6a6a6",
        "primary": "#10a37f", "primary_hover": "#12b78f", "primary_pressed": "#0c8a6c",
        "primary_soft": "#1d3f38", "accent": "#d97757", "focus": "#7c9cff",
        "status_ok": "#4ade80", "status_warn": "#fbbf24", "status_error": "#ff6b6b",
        "status_info": "#78a9ff", "status_muted": "#a6a6a6", "default": "#f1f1f1",
    },
    "light": {
        "surface": "#f6f6f5", "panel": "#ececeb", "card": "#ffffff", "card_alt": "#f2f2f1",
        "border": "#dcdcd9", "border_strong": "#b6b6b2", "text": "#1f1f1f", "muted": "#5f6368",
        "primary": "#0d8a6f", "primary_hover": "#0b7a61", "primary_pressed": "#096b53",
        "primary_soft": "#d7f2ea", "accent": "#c2410c", "focus": "#2563eb",
        "status_ok": "#047857", "status_warn": "#b45309", "status_error": "#b91c1c",
        "status_info": "#1d4ed8", "status_muted": "#5f6368", "default": "#1f1f1f",
    },
}

_ALIASES = {
    "green": "status_ok", "red": "status_error", "orange": "status_warn", "blue": "status_info",
    "gray": "status_muted", "grey": "status_muted", "black": "default", "": "default",
    "ok": "status_ok", "fail": "status_error", "warn": "status_warn", "info": "status_info",
    "muted": "status_muted", "default": "default",
}
_CURRENT = dict(THEMES["dark"])
_HOOKS = []

MODE = "dark"


def token(name):
    return _CURRENT[name]


def __getattr__(name):
    if name in _CURRENT:
        return _CURRENT[name]
    if name == "STATUS":
        return {key: _CURRENT["status_" + key] for key in ("ok", "warn", "error", "info", "muted")}
    raise AttributeError(name)


def status(color_name):
    if not color_name:
        return _CURRENT["default"]
    value = str(color_name).strip().lower()
    if value.startswith("#"):
        return value
    return _CURRENT[_ALIASES.get(value, "default")]


SP_XS, SP_SM, SP_MD, SP_LG = 3, 6, 10, 14
UI = ("Segoe UI", 9)
UI_SMALL = ("Segoe UI", 8)
UI_SEMI = ("Segoe UI", 9, "bold")
UI_TITLE = ("Segoe UI", 14, "bold")
MONO = ("Consolas", 9)


def register_restyle_hook(fn):
    _HOOKS.append(fn)


def apply_theme(root, mode=None):
    global MODE, _CURRENT
    if mode:
        MODE = mode
    _CURRENT = dict(THEMES[MODE])
    root.configure(background=_CURRENT["panel"])
    style = ttk.Style(root)
    style.theme_use("clam")
    _configure_options(root)
    _configure_base(style)
    _configure_buttons(style)
    _configure_inputs(style)
    _configure_tables(style)
    _configure_misc(style)
    return style


def set_theme(root, mode):
    apply_theme(root, mode)
    _restyle_tree(root)
    _restyle_number_tags()
    for hook in _HOOKS:
        try:
            hook(mode)
        except Exception:
            pass
    return mode


def toggle_theme(root):
    return set_theme(root, "light" if MODE == "dark" else "dark")


def _configure_options(root):
    c = _CURRENT
    root.option_add("*TCombobox*Listbox.background", c["card"])
    root.option_add("*TCombobox*Listbox.foreground", c["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", c["primary"])
    root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
    root.option_add("*TCombobox*Listbox.font", UI)
    root.option_add("*TCombobox*Listbox.highlightThickness", 0)


def _configure_base(style):
    c = _CURRENT
    style.configure(".", background=c["panel"], foreground=c["text"], font=UI,
                    bordercolor=c["border"], troughcolor=c["panel"], lightcolor=c["border"],
                    darkcolor=c["border"], focuscolor=c["focus"], focusthickness=1)
    for name, bg in (("TFrame", c["panel"]), ("Page.TFrame", c["surface"]), ("Card.TFrame", c["card"]),
                     ("Sidebar.TFrame", c["panel"]), ("Header.TFrame", c["panel"]), ("Body.TFrame", c["surface"])):
        style.configure(name, background=bg, borderwidth=0, relief="flat")
    for name in ("TLabel", "Card.TLabel", "Sidebar.TLabel", "Header.TLabel", "Body.TLabel"):
        style.configure(name, background=c["panel"], foreground=c["text"], padding=(2, 2))
    for name in ("Muted.TLabel", "Card.Muted.TLabel", "Sidebar.Muted.TLabel", "Body.Muted.TLabel"):
        style.configure(name, background=c["panel"], foreground=c["muted"], font=UI_SMALL)
    for name in ("Section.TLabel", "Card.Section.TLabel", "Body.Section.TLabel"):
        style.configure(name, background=c["card"], foreground=c["text"], font=UI_SEMI)
    style.configure("HeaderTitle.TLabel", background=c["panel"], foreground=c["text"], font=UI_TITLE)
    style.configure("HeaderHint.TLabel", background=c["panel"], foreground=c["muted"])
    style.configure("SidebarItem.TLabel", background=c["panel"], foreground=c["muted"],
                    padding=(11, 9), font=UI, borderwidth=1, relief="solid", bordercolor=c["panel"])
    style.map("SidebarItem.TLabel", background=[("active", c["card_alt"])],
              foreground=[("active", c["text"])], bordercolor=[("active", c["border_strong"])])
    style.configure("SidebarActive.TLabel", background=c["card"], foreground=c["text"],
                    padding=(11, 9), font=UI_SEMI, borderwidth=1, relief="solid",
                    bordercolor=c["primary"])
    style.configure("Status.TLabel", background=c["panel"], foreground=c["muted"], padding=(10, 8))
    style.configure("TLabelframe", background=c["card"], bordercolor=c["border"], relief="flat", borderwidth=1)
    style.configure("TLabelframe.Label", background=c["card"], foreground=c["muted"],
                    font=UI_SMALL, padding=(8, 3))


def _configure_buttons(style):
    c = _CURRENT
    base = dict(borderwidth=1, relief="solid", focuscolor=c["focus"])
    style.configure("TButton", background=c["card_alt"], foreground=c["text"], font=UI,
                    padding=(10, 6), bordercolor=c["border_strong"],
                    lightcolor=c["card_alt"], darkcolor=c["card_alt"], **base)
    style.map("TButton", background=[("disabled", c["panel"]), ("active", c["border"])],
              foreground=[("disabled", c["muted"])],
              bordercolor=[("focus", c["focus"]), ("disabled", c["panel"])],
              lightcolor=[("active", c["border"])], darkcolor=[("active", c["border"])])

    style.configure("Primary.TButton", background=c["primary"], foreground="#ffffff", font=UI_SEMI,
                    padding=(14, 7), bordercolor=c["primary"], lightcolor=c["primary"],
                    darkcolor=c["primary"], **base)
    style.map("Primary.TButton", background=[("disabled", c["panel"]), ("pressed", c["primary_pressed"]),
                                             ("active", c["primary_hover"])],
              foreground=[("disabled", c["muted"])],
              bordercolor=[("disabled", c["panel"]), ("active", c["primary_hover"])],
              lightcolor=[("active", c["primary_hover"])], darkcolor=[("active", c["primary_hover"])])

    style.configure("Ghost.TButton", background=c["card"], foreground=c["text"], padding=(10, 6),
                    bordercolor=c["border_strong"], lightcolor=c["card"], darkcolor=c["card"], **base)
    style.map("Ghost.TButton", background=[("disabled", c["panel"]), ("active", c["card_alt"])],
              bordercolor=[("focus", c["focus"]), ("active", c["border_strong"]), ("disabled", c["panel"])],
              lightcolor=[("active", c["card_alt"])], darkcolor=[("active", c["card_alt"])])

    style.configure("Danger.TButton", background=c["status_error"], foreground="#ffffff", font=UI_SEMI,
                    padding=(12, 7), bordercolor=c["status_error"], lightcolor=c["status_error"],
                    darkcolor=c["status_error"], **base)
    style.map("Danger.TButton", background=[("disabled", c["panel"]), ("active", c["accent"])],
              foreground=[("disabled", c["muted"])],
              bordercolor=[("disabled", c["panel"]), ("active", c["accent"])],
              lightcolor=[("active", c["accent"])], darkcolor=[("active", c["accent"])])

    style.configure("Theme.TButton", background=c["card"], foreground=c["text"], padding=(8, 5),
                    font=UI_SEMI, bordercolor=c["border_strong"], lightcolor=c["card"],
                    darkcolor=c["card"], **base)
    style.map("Theme.TButton", background=[("active", c["card_alt"])],
              bordercolor=[("active", c["border_strong"])],
              lightcolor=[("active", c["card_alt"])], darkcolor=[("active", c["card_alt"])])


def _configure_inputs(style):
    c = _CURRENT
    for name in ("TEntry", "TSpinbox", "TCombobox"):
        style.configure(name, fieldbackground=c["card"], background=c["card_alt"], foreground=c["text"],
                        insertcolor=c["text"], bordercolor=c["border_strong"], borderwidth=1,
                        relief="flat", padding=(7, 5), arrowcolor=c["muted"], arrowsize=12)
        style.map(name, bordercolor=[("focus", c["focus"]), ("readonly", c["border"])],
                  lightcolor=[("focus", c["focus"])], darkcolor=[("focus", c["focus"])],
                  fieldbackground=[("readonly", c["card"]), ("disabled", c["panel"])],
                  foreground=[("disabled", c["muted"])],
                  arrowcolor=[("disabled", c["muted"]), ("active", c["text"])])
    for name in ("TCheckbutton", "TRadiobutton"):
        style.configure(name, background=c["card"], foreground=c["text"], font=UI,
                        padding=(6, 5), indicatorsize=14)
        style.map(name, background=[("active", c["card"]), ("disabled", c["panel"])],
                  foreground=[("disabled", c["muted"])],
                  indicatorcolor=[("selected", c["primary"]), ("alternate", c["muted"])])


def _configure_tables(style):
    c = _CURRENT
    style.configure("Treeview", background=c["card"], fieldbackground=c["card"], foreground=c["text"],
                    rowheight=26, borderwidth=0, relief="flat", font=UI, separatorcolor=c["border"])
    style.map("Treeview", background=[("selected", c["primary_soft"])],
              foreground=[("selected", c["text"])])
    style.configure("Treeview.Heading", background=c["card_alt"], foreground=c["muted"],
                    font=UI_SEMI, relief="flat", padding=(8, 6), bordercolor=c["border"])
    style.map("Treeview.Heading", background=[("active", c["border"])])
    style.configure("TScrollbar", background=c["card_alt"], troughcolor=c["panel"], bordercolor=c["panel"],
                    relief="flat", borderwidth=0, arrowsize=10, arrowcolor=c["muted"], gripcount=0)
    style.map("TScrollbar", background=[("active", c["border"])], arrowcolor=[("active", c["text"])])


def _configure_misc(style):
    c = _CURRENT
    style.configure("TPanedwindow", background=c["surface"])
    style.configure("TPanedwindow.Sash", sashwidth=5, background=c["border"])
    style.configure("TNotebook", background=c["surface"], borderwidth=0, tabmargins=(10, 8, 10, 0))
    style.configure("TNotebook.Tab", background=c["surface"], foreground=c["muted"], font=UI_SEMI,
                    padding=(14, 9), borderwidth=0)
    style.map("TNotebook.Tab", background=[("selected", c["card"]), ("active", c["card_alt"])],
              foreground=[("selected", c["text"]), ("active", c["text"])])


def _restyle_number_tags():
    """Mời text có số thứ tự: làm màu tiền đồ khop theme đang áp dụng."""
    for widget in _NUMBERED_TEXT:
        try:
            if widget.winfo_exists():
                widget.tag_configure("lineno", foreground=_CURRENT["muted"])
        except Exception:
            pass


def _restyle_tree(widget):
    _restyle_tk(widget)
    try:
        children = widget.winfo_children()
    except Exception:
        return
    for child in children:
        _restyle_tree(child)


def _restyle_tk(widget):
    if isinstance(widget, tk.Canvas):
        # Canvas nền cuộn dọc của trang: đổi màu theo theme đang áp dụng.
        widget.configure(background=_CURRENT["surface"])
    elif isinstance(widget, tk.Text):
        _apply_text_colors(widget)
    elif isinstance(widget, tk.Listbox):
        c = _CURRENT
        widget.configure(background=c["card"], foreground=c["text"],
                         selectbackground=c["primary"], selectforeground="#ffffff",
                         highlightbackground=c["border"], highlightcolor=c["focus"])
    elif isinstance(widget, tk.Toplevel):
        widget.configure(background=_CURRENT["surface"])


def style_text(widget, mono=True, height=None):
    widget.configure(font=MONO if mono else UI, padx=SP_MD, pady=SP_SM, spacing1=2, spacing3=2)
    _apply_text_colors(widget)
    if height:
        widget.configure(height=height)
    if not widget.bind("<MouseWheel>"):
        widget.bind("<MouseWheel>", lambda event: _wheel_y(widget, event), add="+")
        widget.bind("<Shift-MouseWheel>", lambda event: _wheel_x(widget, event), add="+")
    return widget


def _apply_text_colors(widget):
    c = _CURRENT
    widget.configure(background=c["card"], foreground=c["text"], insertbackground=c["text"],
                     relief="flat", borderwidth=0, highlightthickness=1,
                     highlightbackground=c["border"], highlightcolor=c["focus"],
                     selectbackground=c["primary_soft"], selectforeground=c["text"])


def _wheel_y(widget, event):
    if widget.yview()[:1][0] <= 0.0 and event.delta > 0:
        return "break"
    if widget.yview()[1] >= 1.0 and event.delta < 0:
        return "break"
    widget.yview_scroll(-1 if event.delta > 0 else 1, "units")
    return "break"


def _wheel_x(widget, event):
    widget.xview_scroll(-2 if event.delta > 0 else 2, "units")
    return "break"


# Số thứ tự dòng: viết thẳng trong box (không dùng cột riêng), tách bằng dấu "|".
# Đọc/xuất file luôn bỏ tiền tố này nên file ra đúng như cũ.
# (Line numbers live inside the box as a "N | " prefix; always stripped on read/export.)
_LINE_NUMBER_RE = re.compile(r"^\s*\d+\s*\|\s?")


def number_lines(text):
    """Thêm tiền tố số thứ tự vào từng dòng (dòng rỗng giữ nguyên).
    (Prefix each non-empty line with its line number.)"""
    out = []
    number = 0
    for line in text.split("\n"):
        if line.strip():
            number += 1
            out.append(f"{number} | {line}")
        else:
            out.append(line)
    return "\n".join(out)


def strip_numbers(text):
    """Bỏ tiền tố số thứ tự -> lấy lại đúng nội dung gốc.
    Có ghep để an: chỉ chấp nhắn khi kết quả gộp lại khớp hặt thoa thích nội dung
    co so thu tu. Neu noi dung that cua nguoi dung vua tinh co duong "N | " thi
    giu nguyen, khong bao gio lam hong du lieu.
    (Strips the prefix, but only when re-numbering reproduces the input exactly -
    só phục để thật của bạn có "N | " không bao giờ bề hỏng.)"""
    lines = text.split("\n")
    stripped = "\n".join(_LINE_NUMBER_RE.sub("", line) for line in lines)
    if number_lines(stripped) == text:
        return stripped
    # Box vẫn có dấng trống cuối (do ".strip()" cắt newline) -> so sánh thêm một lần.
    if number_lines(stripped) == text.rstrip("\n"):
        return stripped
    return text


_NUMBERED_TEXT = []


def mark_numbered(widget):
    """Đánh dấu 1 Text widget là 'có số thứ tự' để đổi màu theo theme."""
    if widget not in _NUMBERED_TEXT:
        _NUMBERED_TEXT.append(widget)
    widget.tag_configure("lineno", foreground=_CURRENT["muted"])


def _tag_prefixes(widget, from_line=1):
    """Tô màu phần tiền 'N | ' cho các dòng có số thứ tự.
    (Grey out just the number prefix, keeping the text readable.)"""
    widget.tag_configure("lineno", foreground=_CURRENT["muted"])
    widget.tag_remove("lineno", "1.0", tk.END)
    for index, line in enumerate(widget.get("1.0", tk.END).split("\n"), start=1):
        if index < from_line:
            continue
        match = _LINE_NUMBER_RE.match(line)
        if match:
            start = f"{index}.0"
            end = f"{index}.{len(match.group(0))}"
            widget.tag_add("lineno", start, end)


def set_numbered(widget, text):
    """Thay toàn bố nội dung box bằng bản có số thứ tự."""
    widget.delete("1.0", tk.END)
    content = number_lines(text)
    if content:
        widget.insert(tk.END, content)
    _tag_prefixes(widget)
    return content


def append_numbered(widget, text):
    """Nối thêm nội dung vào cuối, số thứ tự chạy liên tục - không chễn
    dòng trống (dòng trống cuối không được đểm số thứ tự).
    (Append while keeping numbering contiguous; blank filler lines must not
    consume a number or the sequence would jump.)"""
    added = strip_numbers(text).rstrip("\n")
    if not added.strip():
        return
    existing = get_numbered(widget)
    number = len([ln for ln in existing.split("\n") if ln.strip()])
    lines = []
    for line in added.split("\n"):
        if line.strip():
            number += 1
            lines.append(f"{number} | {line}")
        else:
            lines.append(line)
    widget.insert(tk.END, ("\n" if existing else "") + "\n".join(lines))
    _tag_prefixes(widget)
    return number


def get_numbered(widget):
    """Đọc nội dung box, bỏ tiền tố số thứ tự."""
    try:
        raw = widget.get("1.0", tk.END)
    except tk.TclError:
        return ""
    return strip_numbers(raw.rstrip("\n"))


def make_scrollable(text, horizontal=False):
    parent = text.master
    vertical = ttk.Scrollbar(parent, orient="vertical", command=text.yview)
    vertical.pack(side="right", fill="y", before=text)
    text.configure(yscrollcommand=vertical.set)
    if horizontal:
        horizontal_bar = ttk.Scrollbar(parent, orient="horizontal", command=text.xview)
        horizontal_bar.pack(side="bottom", fill="x", before=text)
        text.configure(xscrollcommand=horizontal_bar.set)
    return text
