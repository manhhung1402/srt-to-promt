# -*- coding: utf-8 -*-
"""
Điều phối ứng dụng VideoWorkflowTool (Video Workflow Tool / Export SRT & Prompt Generator)
"""
import os
import json
import threading
import time
from collections import deque
import time
import traceback
import tkinter as tk
from tkinter import filedialog, ttk

from config.settings import API_PRESETS, API_CONFIG, LLM_URLS
from config.settings import DEFAULT_STYLE_PROMPT
from utils import file_utils
from utils.file_utils import get_data_dir, export_text_widget_content
from utils.exceptions import StopRun, LLMApiError, BrowserActionRequired
from core.api_client import OpenAICompatClient, RETRY_CHOICES
from core.browser_v3 import BrowserManager
from core.whisper_aligner import run_whisper_alignment
from core.whisper_runtime import WhisperRuntime, WHISPER_MODELS
from core.scene_grouper import (
    enforce_max_sec, enforce_min_sec, align_scenes_via_algorithm,
    align_scenes_via_llm_api, align_scenes_via_llm_web, render_scenes_to_srt
)
from core.script_processor import process_script_core
from core.prompt_generator import (run_api_prompt_generation, run_selenium_prompt_generation,
                                   BatchFailure, _resolve_max_attempts)

from ui.tabs.tab_srt import create_tab_srt
from ui.tabs.tab_script import create_tab_script
from ui.tabs.tab_prompt import create_tab_prompt
from ui.tabs.tab_settings import create_tab_settings
from ui.auto_panel import AutoRunner, create_auto_panel, SORT_BY_LABEL
from ui import theme

LOG_KEEP_LINES = 50        # so dong log giu lai de xem lai / gui AI kiem tra


class VideoWorkflowTool:
    """Ứng dụng chính điều phối giao diện Tkinter và các Services."""

    def __init__(self, root):
        self.root = root
        self.root.title("Tool Video Workflow - Tạo SRT & Sinh Prompt")
        self.root.geometry("1280x980")
        self.root.minsize(1120, 760)
        theme.apply_theme(self.root)          # Design tokens + ttk style (giao diện)

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.root.report_callback_exception = self.show_error

        # Mọi thứ app ghi ra (config, log, ui_state, Chrome profile, model
        # Whisper) đều là DỮ LIỆU, không phải mã nguồn → nằm trong `data/`
        # để `build/` và `_internal/` luôn sạch.
        self.base_dir = get_data_dir()
        self.whisper_runtime = WhisperRuntime(self.base_dir)

        self.temp_sentence_timings = []
        self.audio_duration = 0.0
        self.temp_script_lines = []
        self.batch_size_cache = 50
        self.style_prefix_cache = ''
        self.ref_text_cache = ''
        self.scene_method_cache = 'algo'
        self.scene_mode_cache = 'auto'
        self.is_api_cache = True

        # Buoc 2 va Buoc 4 dung LLM/nguon rieng biet (van chung mot cua so Chrome).
        self.llm_var = tk.StringVar(value="DeepSeek")          # Buoc 2
        self.ai_source_var = tk.StringVar(value="api")         # Buoc 2
        self.prompt_llm_var = tk.StringVar(value="DeepSeek")   # Buoc 4
        self.prompt_source_var = tk.StringVar(value="api")      # Buoc 4
        # Số lần thử lại cho API. "inf" = vô hạn, mặc định là vô hạn.
        self.api_retry_var = tk.StringVar(value="inf")
        self.RETRY_CHOICES = RETRY_CHOICES
        self._auto_active = False
        self._auto_current_step = None
        self._rotate_index = 0                            # vi tri xoay vong model hien tai
        self._log_lines = deque(maxlen=LOG_KEEP_LINES)   # 50 dong gan nhat
        self._log_widget = None
        self.style_presets = [DEFAULT_STYLE_PROMPT]
        self._ui_state_path = os.path.join(self.base_dir, "ui_state.json")

        self.browser_mgr = BrowserManager(
            base_dir=self.base_dir,
            get_current_llm_fn=lambda: self.llm_for_task(),
            ask_modal_fn=self.ask_ok_cancel_threadsafe
        )

        self._api_client = None
        self._all_model_ids = []
        self._model_owner = {}
        self._combos = {}
        self._tested_group = ''
        self._ok_model_ids = []
        self._test_results = {}
        self._test_results_cache = {}
        self._model_status = {}
        self._stop_test = threading.Event()

        self.api_cfg = {
            "preset": "OpenRouter",
            "base_url": API_CONFIG["default_base_url"],
            "api_key": "",
            "model": API_CONFIG["default_model"],
        }
        cfg_path = os.path.join(self.base_dir, API_CONFIG["config_file"])
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    for k in ("preset", "base_url", "api_key", "model"):
                        if k in loaded and loaded[k] is not None:
                            self.api_cfg[k] = str(loaded[k])
            except Exception:
                pass

        self.create_widgets()

    # ==========================================
    # HIỆN THỊ LỖI
    # ==========================================
    def show_error(self, exc, val, tb):
        err_msg = "".join(traceback.format_exception(exc, val, tb))
        self.set_activity("Lỗi hệ thống (Internal error): " + err_msg.strip()[-300:], "red")

    def show_error_threadsafe(self, title, msg):
        self.set_activity(f"{title}: {msg}", "red")

    def set_activity(self, text, color="blue"):
        """Ghi thông báo lên thanh trạng thái dưới cùng thay cho hộp thoại bật lên.
        (Posts a message on the bottom status bar instead of a popup dialog.)"""
        def _apply():
            try:
                self.lbl_activity.config(text=str(text), foreground=theme.status(color))
            except Exception:
                pass
        try:
            self.root.after(0, _apply)
        except Exception:
            pass

    def _notify_error(self, title, msg=""):
        self.set_activity(f"{title}: {msg}", "red")
        return False

    def _notify_warn(self, title, msg=""):
        self.set_activity(f"{title}: {msg}", "orange")
        return False

    def _auto_step_cancelled(self, title, msg=""):
        """Khi một bước tự cho về vì thiếu điều kiện: cảnh báo như cũ và báo cho auto."""
        self._notify_warn(title, msg)
        self._notify_auto_step(False, msg)
        return False

    # ==========================================
    # LOG 50 DONG + COPY
    # ==========================================
    def log_event(self, text, level="info"):
        """Ghi 1 dong log (co gio) vao hop 50 dong gan nhat, hop nhom tren man hinh,
        va 1 file log ben canh tool. File log ghi lai de sau khi mo app len van
        biet auto da chay toi dau - nho RAM bi mat khi dong app.
        (Append a timestamped line to the ring buffer, the on-screen box, and a file.)"""
        stamp = time.strftime("%H:%M:%S")
        line = "[%s] %s" % (stamp, str(text).replace(chr(10), " ").strip())
        self._log_lines.append(line)
        self._write_log_file(stamp, line, level)
        # Tk khong an toan khi goi tu thread khac: phan hop nhom phai chay tren
        # luong chinh qua root.after. Auto panel thi goi ham nay tu luong nen.
        try:
            widget = self._log_widget
            if widget is not None:
                self.root.after(0, lambda w=widget, t=line: self._append_log_widget(w, t))
        except Exception:
            pass
        return line

    def _append_log_widget(self, widget, line):
        """Noi 1 dong vao hop nhom log (chi goi tren luong chinh).
        (Append one line to the on-screen log box; main thread only.)"""
        try:
            widget.configure(state="normal")
            widget.insert("end", line + "\n")
            lines = int(widget.index("end-1c").split(".")[0])
            if lines > LOG_KEEP_LINES:
                widget.delete("1.0", "%d.0" % (lines - LOG_KEEP_LINES))
            widget.see("end")
            widget.configure(state="disabled")
        except tk.TclError:
            pass

    def _write_log_file(self, stamp, line, level="info"):
        """Noi 1 dong vao file log cua ngay hom do. Log loi tach rieng de doc nhanh.
        (Append to today's log file; failures go to their own file.)"""
        try:
            if level == "error":
                path = os.path.join(self.base_dir, "log_error.txt")
            else:
                path = os.path.join(self.base_dir,
                                    time.strftime("log_%Y-%m-%d.txt"))
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except Exception:
            # Khong ghi duoc log thi dung hien thi, khong duoc lam treo app.
            pass

    def log_text(self):
        """Noi dung log hien tai (de copy di gui AI kiem tra)."""
        return "\n".join(self._log_lines)

    def copy_log(self):
        text = self.log_text()
        if not text:
            return self._notify_warn("Log trống (Empty log)", "Chưa có gì để copy.")
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update_idletasks()
            self.set_activity("Đã copy %d dòng log" % len(self._log_lines), "green")
            self.log_event("Đã copy %d dòng log vào clipboard" % len(self._log_lines))
        except Exception as err:
            self._notify_error("Lỗi copy log", str(err))

    def clear_log(self):
        self._log_lines.clear()
        if self._log_widget is not None:
            try:
                self._log_widget.configure(state="normal")
                self._log_widget.delete("1.0", "end")
                self._log_widget.configure(state="disabled")
            except tk.TclError:
                pass
        self.set_activity("Đã xoá log", "blue")

    def _notify_info(self, title, msg=""):
        self.set_activity(f"{title}: {msg}", "green")
        return False

    def ask_ok_cancel_threadsafe(self, title, msg):
        """Khong mo hop thoai (no modal): bao sang thanh trang thai va cho chay lai.

        Truoc day ham nay LUON tra True. Nen khi nhap/Gui that bai, vong lap
        thu lai khong bao gio co duong thoat (treo vo han). Gio dem so lan:
        cho thu lai vai lan roi tra False de loi thuoc ve nguoi dung.
        (Never returned True unconditionally - that made retries spin forever.)
        """
        self.set_activity(f"{title}: {msg}", "orange")
        self._ask_retry_count = getattr(self, "_ask_retry_count", 0) + 1
        if self._ask_retry_count >= 3:
            self._ask_retry_count = 0
            self.set_activity(
                "Da thu lai nhieu lan ma van loi. Hay xu ly tren cua so Chrome roi chay lai.", "red")
            return False
        return True

    def reset_ask_retry_count(self):
        """Dat lai bo dem thu lai sau khi mot buoc thanh cong."""
        self._ask_retry_count = 0

    # ==========================================
    # ĐÓNG ỨNG DỤNG AN TOÀN (safe shutdown)
    # ==========================================
    def on_closing(self):
        self._persist_all()
        self.browser_mgr.stop_event.set()
        try:
            self.browser_mgr.kill_chrome()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    # ==========================================
    def create_widgets(self):
        self.root.configure(background=theme.token("panel"))
        shell = ttk.Frame(self.root, style="Header.TFrame")
        shell.pack(fill="both", expand=True)

        sidebar = ttk.Frame(shell, style="Sidebar.TFrame", padding=(0, theme.SP_LG, 0, theme.SP_MD))
        sidebar.pack(side="left", fill="y")
        ttk.Label(sidebar, text="TOOL VIDEO", style="HeaderTitle.TLabel",
                  padding=(18, 0, 18, 2)).pack(anchor="w")
        ttk.Label(sidebar, text="Workflow · SRT · Prompt", style="Sidebar.Muted.TLabel",
                  padding=(18, 0, 18, theme.SP_LG)).pack(anchor="w")

        nav_items = (
            ("Cài đặt", create_tab_settings, "Cấu hình API, model, kết nối"),
            ("SRT", create_tab_srt, "Tạo SRT theo câu và phân cảnh"),
            ("Kịch bản", create_tab_script, "Tách và chuẩn hoá kịch bản"),
            ("Prompt", create_tab_prompt, "Sinh prompt ảnh tự động"),
        )
        self._nav_frames = []
        for title, builder, hint in nav_items:
            item = ttk.Frame(sidebar, style="Sidebar.TFrame")
            item.pack(fill="x", pady=2)
            label = ttk.Label(item, text=title, style="SidebarItem.TLabel", cursor="hand2")
            label.pack(fill="x")
            label.bind("<Button-1>", lambda event, index=len(self._nav_frames): self._show_page(index))
            item.bind("<Button-1>", lambda event, index=len(self._nav_frames): self._show_page(index))
            self._nav_frames.append((item, label))

        self._theme_mode = "dark"
        theme_button = ttk.Button(sidebar, text="◐ Dark", style="Theme.TButton", width=10,
                                 command=self.toggle_theme)
        theme_button.pack(side="bottom", fill="x", padx=18, pady=(theme.SP_MD, 6))
        self._theme_button = theme_button

        main = ttk.Frame(shell, style="Body.TFrame")
        main.pack(side="left", fill="both", expand=True)
        header = ttk.Frame(main, style="Header.TFrame", padding=(theme.SP_LG, theme.SP_MD, theme.SP_LG, theme.SP_SM))
        header.pack(fill="x")
        self._page_title = ttk.Label(header, text="Cài đặt", style="HeaderTitle.TLabel")
        self._page_title.pack(anchor="w")
        self._page_hint = ttk.Label(header, text="Cấu hình API, model, kết nối", style="HeaderHint.TLabel")
        self._page_hint.pack(anchor="w", pady=(2, 0))

        self.pages = []
        self._page_hints = []
        self._page_canvases = []
        for _, builder, hint in nav_items:
            # Mỗi trang nằm trong một khung cuộn dọc riêng: nội dung cao hơn cửa sổ
            # vẫn cuộn được, không bị cắt hay che khuất chức năng.
            viewport = ttk.Frame(main, style="Body.TFrame")
            canvas = tk.Canvas(viewport, highlightthickness=0, borderwidth=0,
                               background=theme.token("surface"))
            scroll = ttk.Scrollbar(viewport, orient="vertical", command=canvas.yview)
            canvas.configure(yscrollcommand=scroll.set)
            scroll.pack(side="right", fill="y")
            canvas.pack(side="left", fill="both", expand=True)
            inner = ttk.Frame(canvas, style="Body.TFrame",
                              padding=(theme.SP_LG, theme.SP_SM, theme.SP_LG, theme.SP_LG))
            window = canvas.create_window((0, 0), window=inner, anchor="nw")
            inner.bind("<Configure>",
                       lambda event, c=canvas: c.configure(scrollregion=c.bbox("all")))
            canvas.bind("<Configure>",
                        lambda event, c=canvas, w=window: c.itemconfigure(w, width=event.width))
            page = builder(self, inner)
            page.pack(fill="both", expand=True)
            self.pages.append(viewport)
            self._page_canvases.append(canvas)
            self._page_hints.append(hint)

        logbox = ttk.Frame(main, style="Body.TFrame", padding=(theme.SP_LG, 0, theme.SP_LG, theme.SP_XS))
        logbox.pack(side="bottom", fill="x")
        loghead = ttk.Frame(logbox, style="Body.TFrame")
        loghead.pack(fill="x")
        ttk.Label(loghead, text="NHẬT KÝ (50 dòng gần nhất)", style="HeaderHint.TLabel").pack(side="left")
        ttk.Button(loghead, text="📋 Copy log", style="Ghost.TButton",
                   command=self.copy_log).pack(side="right")
        ttk.Button(loghead, text="🗑 Xoá", style="Ghost.TButton",
                   command=self.clear_log).pack(side="right", padx=(0, theme.SP_SM))
        self._log_widget = theme.style_text(tk.Text(logbox, height=5, wrap="none"), mono=False)
        self._log_widget.pack(fill="x", pady=(theme.SP_XS, 0))
        self._log_widget.configure(state="disabled")
        theme.make_scrollable(self._log_widget, horizontal=True)

        bar = ttk.Frame(main, style="Header.TFrame", padding=(theme.SP_LG, 0, theme.SP_LG, theme.SP_MD))
        bar.pack(side="bottom", fill="x")
        self.lbl_activity = ttk.Label(bar, style="Status.TLabel", anchor="w", text="Sẵn sàng (Ready)")
        self.lbl_activity.pack(side="left", fill="x", expand=True)

        self._show_page(0)
        self.root.bind_all("<MouseWheel>", self._scroll_active_page, add="+")
        self.root.bind_all("<Button-4>", self._scroll_active_page, add="+")
        self.root.bind_all("<Button-5>", self._scroll_active_page, add="+")

        self.load_initial_api_config()
        self.check_whisper_status()
        file_utils.set_notifier(self.set_activity)
        self._load_ui_state()

    def _show_page(self, index):
        self._page_index = index
        for position, page in enumerate(self.pages):
            if position == index:
                page.pack(fill="both", expand=True)
            else:
                page.pack_forget()
        for position, (frame, label) in enumerate(self._nav_frames):
            style_name = "SidebarActive.TLabel" if position == index else "SidebarItem.TLabel"
            label.configure(style=style_name)
        titles = [item[0] for item in (
            ("Cài đặt", create_tab_settings, ""),
            ("SRT", create_tab_srt, ""),
            ("Kịch bản", create_tab_script, ""),
            ("Prompt", create_tab_prompt, ""),
        )]
        self._page_title.configure(text=titles[index])
        self._page_hint.configure(text=self._page_hints[index])
        self._page_canvases[index].focus_set()

    def _scroll_active_page(self, event):
        """Cuộn trang đang mở khi chuột con trên vùng trống (chuột trên ô text
        thì text tự cuộn, không chạy vào đây).
        (Wheel scrolls the active page; Text widgets scroll themselves first.)"""
        if not getattr(self, "_page_canvases", None):
            return
        canvas = self._page_canvases[self._page_index]
        if event.num == 4 or event.delta > 0:
            amount = -3
        elif event.num == 5 or event.delta < 0:
            amount = 3
        else:
            return "break"
        canvas.yview_scroll(amount, "units")
        return "break"

    def toggle_theme(self):
        self._theme_mode = "light" if self._theme_mode == "dark" else "dark"
        theme.set_theme(self.root, self._theme_mode)
        self._theme_button.configure(text="◐ Dark" if self._theme_mode == "dark" else "◑ Light")

    # ==========================================
    # LƯU CÀI ĐẶT GIỮA PHIÊN + LỊCH SỬ STYLE
    # (Persist UI settings across sessions + style history)
    # ==========================================
    def _load_ui_state(self):
        try:
            if not os.path.exists(self._ui_state_path):
                return
            with open(self._ui_state_path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if not isinstance(saved, dict):
                return
            presets = saved.get("style_presets")
            if isinstance(presets, list):
                merged = [str(s).strip() for s in presets if str(s).strip()]
                current = self.entry_style.get().strip()
                self.style_presets = list(dict.fromkeys(([current] if current else []) + merged))
                self.combo_style_history["values"] = self.style_presets
            # API o day du lieu cu khi file api_config.json trong (chua tung bam Luu)
            # (Restore API fields when api_config.json was never saved.)
            try:
                _, loaded_base, loaded_key, loaded_model = self.collect_api_config()
            except Exception:
                loaded_base = loaded_key = loaded_model = ""
            if not loaded_key and saved.get("api_key"):
                self.entry_api_key.delete(0, tk.END)
                self.entry_api_key.insert(0, saved["api_key"])
            if saved.get("preset") and saved["preset"] in list(self.combo_api_preset["values"]):
                if not loaded_base.strip() or loaded_base.strip() == API_CONFIG["default_base_url"]:
                    self.combo_api_preset.set(saved["preset"])
                    self.on_preset_change()
            if saved.get("base_url") and not loaded_base.strip():
                self.entry_base_url.config(state="normal")
                self.entry_base_url.delete(0, tk.END)
                self.entry_base_url.insert(0, saved["base_url"])
                self.on_preset_change(preset_name=self.combo_api_preset.get(), keep_custom=True)
            if saved.get("model") and not loaded_model.strip():
                self.combo_api_model.set(saved["model"])
            text_widgets = {"ref_text": self.txt_reference_text}
            for key, widget in text_widgets.items():
                if saved.get(key):
                    widget.delete("1.0", tk.END)
                    widget.insert(tk.END, saved[key])
            for key, widget in (("audio_path", self.entry_audio_path),
                                ("srt_in_path", self.entry_srt_in_path),
                                ("min_duration", self.entry_min_duration),
                                ("max_duration", self.entry_max_duration),
                                ("batch_size", self.spin_batch_size),
                                ("style", self.entry_style)):
                value = str(saved.get(key, "") or "").strip()
                if key == "batch_size" and value:
                    # Giá trị lưu cũ có thể nhỏ hơn mức tối thiểu mới -> bỏ qua,
                    # để người dùng tự nhập, không âm thầm ép giá trị.
                    try:
                        if int(float(value)) < 10:
                            value = ""
                    except ValueError:
                        value = ""
                if value:
                    # Dùng set() thay vì delete+insert: delete(0, END) không
                    # xoá hết với ttk.Spinbox nên giá trị cũ bị nối vào.
                    try:
                        widget.set(value)
                    except Exception:
                        widget.delete(0, tk.END)
                        widget.insert(0, value)
            for key, combo in (("whisper_model", self.combo_whisper_model),
                               ("llm", self.combo_llm_step2),
                               ("api_retry", self.combo_api_retry)):
                value = str(saved.get(key, "") or "").strip()
                if value and value in list(combo["values"]):
                    combo.set(value)
            for key, variable in (("ai_source", self.ai_source_var),
                                  ("scene_method", self.scene_method),
                                  ("scene_input_mode", self.scene_input_mode),
                                  ("llm", self.llm_var),
                                  ("prompt_llm", self.prompt_llm_var),
                                  ("prompt_source", self.prompt_source_var),
                                  ("api_retry", self.api_retry_var),
                                  ("auto_root", self.auto_root),
                                  ("auto_out", self.auto_out),
                                  ("auto_sort_label", self.auto_sort_label),
                                  ("auto_script_source", self.auto_script_source),
                                  ("auto_prompt_source", self.auto_prompt_source)):
                value = saved.get(key)
                if value:
                    variable.set(value)
            if saved.get("rotate") is not None:
                self.var_rotate.set(bool(saved["rotate"]))
            mode = saved.get("theme")
            if mode in theme.THEMES and mode != self._theme_mode:
                self.toggle_theme()
            self.combo_style_history.set(self.entry_style.get().strip())
        except Exception:
            pass

    def _remember_style(self):
        style = self.entry_style.get().strip()
        if style and style not in self.style_presets:
            self.style_presets.insert(0, style)
        self.style_presets = self.style_presets[:20]
        try:
            self.combo_style_history.configure(values=self.style_presets)
        except Exception:
            pass

    def _apply_style_history(self):
        if not hasattr(self, "combo_style_history"):
            return
        selected = str(self.combo_style_history.get() or "").strip()
        if selected:
            self.entry_style.delete(0, tk.END)
            self.entry_style.insert(0, selected)
            self._remember_style()
            self.set_activity("Đã áp dụng Style từ lịch sử (Style loaded from history)", "green")

    def _ui_state(self):
        def get(widget):
            try:
                return widget.get()
            except Exception:
                return ""
        return {
            "audio_path": get(self.entry_audio_path),
            "srt_in_path": get(self.entry_srt_in_path),
            "ref_text": self.txt_reference_text.get("1.0", tk.END).strip(),
            "whisper_model": get(self.combo_whisper_model),
            "min_duration": get(self.entry_min_duration),
            "max_duration": get(self.entry_max_duration),
            "batch_size": get(self.spin_batch_size),
            "style": get(self.entry_style),
            "style_presets": self.style_presets,
            "ai_source": self.ai_source_var.get(),
            "scene_method": self.scene_method.get(),
            "scene_input_mode": self.scene_input_mode.get(),
            "llm": self.llm_var.get(),
            "prompt_llm": self.prompt_llm_var.get(),
            "prompt_source": self.prompt_source_var.get(),
            "rotate": self.var_rotate.get(),
            "theme": self._theme_mode,
            "auto_root": self.auto_root.get(),
            "auto_out": self.auto_out.get(),
            "auto_sort_label": self.auto_sort_label.get(),
            "auto_script_source": self.auto_script_source.get(),
            "auto_prompt_source": self.auto_prompt_source.get(),
            "api_retry": self.api_retry_var.get(),
        }

    def _persist_all(self):
        try:
            self._remember_style()
            state = self._ui_state()
            try:
                preset, base_url, api_key, model = self.collect_api_config()
                state.update({"preset": preset, "base_url": base_url,
                              "api_key": api_key, "model": model})
            except Exception:
                pass
            with open(self._ui_state_path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    # ==========================================
    # QUẢN LÝ API (OpenAI-compatible: provider + model + test)
    # ==========================================
    def load_initial_api_config(self):
        saved_key = self.api_cfg.get('api_key', '')
        if saved_key:
            self.entry_api_key.insert(0, saved_key)
        saved_base = self.api_cfg.get('base_url', '')
        if saved_base:
            self.entry_base_url.delete(0, tk.END)
            self.entry_base_url.insert(0, saved_base)
        saved_model = self.api_cfg.get('model', '')
        if saved_model:
            self.combo_api_model.set(saved_model)
        saved_preset = self.api_cfg.get('preset', '')
        if saved_preset:
            self.combo_api_preset.set(saved_preset)
        else:
            self.combo_api_preset.set('OpenRouter')
        self.on_preset_change(keep_custom=True)
        is_local = any(h in (saved_base or '').lower() for h in ('localhost', '127.0.0.1', '0.0.0.0'))
        if saved_key or is_local:
            self.start_fetch_models(silent=True)

    def on_preset_change(self, preset_name=None, keep_custom=False):
        """Áp preset -> điền Base URL. Preset 'Custom' thì giữ nguyên để tự nhập.
        keep_custom=True dùng khi nạp cấu hình đã lưu (giữ URL người dùng đã lưu)."""
        if preset_name is None:
            preset_name = self.combo_api_preset.get()
        if preset_name.endswith("(Custom)"):
            self.entry_base_url.config(state="normal")
            if not keep_custom:
                self.entry_base_url.delete(0, tk.END)
            return
        url = API_PRESETS.get(preset_name, "")
        self.entry_base_url.config(state="normal")
        self.entry_base_url.delete(0, tk.END)
        self.entry_base_url.insert(0, url)
        self.entry_base_url.config(state="readonly")

    def toggle_key_visibility(self):
        if self.entry_api_key.cget('show') == '*':
            self.entry_api_key.config(show='')
        else:
            self.entry_api_key.config(show='*')

    def collect_api_config(self):
        base_url = self.entry_base_url.get().strip()
        api_key = self.entry_api_key.get().strip()
        model = self.combo_api_model.model_id()
        preset = self.combo_api_preset.get().strip()
        return preset, base_url, api_key, model

    def save_api_config(self):
        preset, base_url, api_key, model = self.collect_api_config()
        self.api_cfg = {'preset': preset, 'base_url': base_url, 'api_key': api_key, 'model': model}
        cfg_path = os.path.join(self.base_dir, API_CONFIG['config_file'])
        try:
            with open(cfg_path, 'w', encoding='utf-8') as f:
                json.dump(self.api_cfg, f, ensure_ascii=False, indent=2)
            self._api_client = None
            self.lbl_api_status.config(text='✅ Đã lưu (Saved)', foreground=theme.status("green"))
        except Exception as e:
            self.lbl_api_status.config(text=f'❌ Lỗi lưu file cấu hình (Save error): {e}', foreground=theme.status("red"))

    def build_api_client(self, model=None):
        _, base_url, api_key, _ = self.collect_api_config()
        if model:
            return self._apply_retry_policy(OpenAICompatClient(base_url=base_url, api_key=api_key, model=model))
        _, _, _, model = self.collect_api_config()
        return self._apply_retry_policy(OpenAICompatClient(base_url=base_url, api_key=api_key, model=model))

    def _apply_batch_shrink(self, size):
        """Tool tự giảm lô: cập nhật lại ô Batch cho người dùng thấy.
        (Reflect the auto-shrunk batch size back into the UI box.)"""
        self.batch_size_cache = int(size)
        self.root.after(0, lambda: self.spin_batch_size.set(str(int(size))))

    def _retry_tag(self):
        """Nhan hien thi cho log: dang thu lai bao nhieu lan / vo han."""
        choice = str(self.api_retry_var.get() or "").strip()
        return "thử lại vô hạn" if choice in ("inf", "") else "thử lại %s lần" % choice

    def _retry_limit(self):
        """Số lần thử theo hộp 'Thử lại API': None = vô hạn."""
        return _resolve_max_attempts(self.api_retry_var.get())

    def _retry_is_infinite(self):
        return self._retry_limit() is None

    def _apply_retry_policy(self, client):
        """Gán số lần thử do người dùng chọn trong hộp 'Thử lại API'.
        (Apply the retry count chosen in the UI box to a fresh API client.)"""
        try:
            client.set_retry_policy(self.api_retry_var.get())
        except Exception:
            pass
        return client

    def prepare_api_client(self):
        try:
            self._api_client = self.build_api_client()
            return True
        except LLMApiError as e:
            self._notify_error('Cấu Hình API Chưa Đầy Đủ (Incomplete API Config)', str(e))
            self._notify_auto_step(False, str(e))
            return False

    def _append_test_log(self, model, result, note="", tag="info"):
        """Xep vao hang doi rồi flush từng lát: tránh nghẽn mainloop khi test hàng trăm model."""
        queue = getattr(self, "_log_queue", None)
        if queue is None:
            queue = self._log_queue = []
        queue.append((model, result, note, tag))
        if not getattr(self, "_log_flushing", False):
            self._log_flushing = True
            self.root.after(120, self._flush_test_log)
        return None

    def _flush_test_log(self, event=None):
        queue = getattr(self, "_log_queue", [])
        batch, self._log_queue = queue[:60], queue[60:]
        last = None
        for model, result, note, tag in batch:
            last = self._insert_test_row(model, result, note, tag)
        if last is not None:
            try:
                self.tree_test.see(last)
            except Exception:
                pass
        if self._log_queue:
            self.root.after(60, self._flush_test_log)
        else:
            self._log_flushing = False
        return "break"

    def _insert_test_row(self, model, result, note="", tag="info"):
        """Thêm 1 dòng vào bảng kết quả test; tag: ok / fail / warn / info (màu chữ)."""
        try:
            self._tree_rows = getattr(self, "_tree_rows", 0) + 1
            parity = ("roweven",) if self._tree_rows % 2 == 0 else ()
            row = self.tree_test.insert("", tk.END, values=(model, result, note), tags=(tag,) + parity)
            self.tree_test.see(row)
            return row
        except Exception:
            return None

    def copy_test_log(self, event=None, model_only=False):
        """Copy dòng log hoặc riêng tên model vào clipboard.
        (Copy selected test-log row or only its model id.)"""
        try:
            rows = self.tree_test.selection() or ((self.tree_test.focus(),) if self.tree_test.focus() else ())
            if not rows:
                return "break"
            values = self.tree_test.item(rows[0], "values")
            text = str(values[0]) if model_only else " | ".join(str(v) for v in values)
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update_idletasks()
            self.lbl_api_status.config(text=("Đã copy tên model" if model_only else "Đã copy dòng log"),
                                       foreground=theme.status("ok"))
        except Exception:
            pass
        return "break"

    def show_test_log_menu(self, event=None):
        try:
            row = self.tree_test.identify_row(event.y)
            if row:
                self.tree_test.selection_set(row)
                self.tree_test.focus(row)
            menu = tk.Menu(self.root, tearoff=False)
            menu.add_command(label="Copy dòng log", command=lambda: self.copy_test_log())
            menu.add_command(label="Copy tên model", command=lambda: self.copy_test_log(model_only=True))
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass
        return "break"

    def _clear_test_log(self):
        try:
            self._tree_rows = 0
            for row in self.tree_test.get_children():
                self.tree_test.delete(row)
        except Exception:
            pass

    def api_stop_requested(self):
        """True khi người dùng bấm Dừng / Thoát trình duyệt khi một luồng API đang chờ.
        (Predicate used by chat() so a hanging request is cut immediately.)
        Dùng được trong worker thread vì chỉ đọc Event, không chạm widget."""
        try:
            return self.browser_mgr.stop_event.is_set()
        except Exception:
            return False

    def start_connect(self):
        _, base_url, api_key, _ = self.collect_api_config()
        self.lbl_api_status.config(text=f"Đang kết nối tới {base_url}... (Connecting)", foreground=theme.status("orange"))
        threading.Thread(target=self._connect_worker, args=(base_url, api_key), daemon=True).start()


    def _connect_worker(self, base_url, api_key):
        try:
            client = OpenAICompatClient(base_url=base_url, api_key=api_key, model="__probe__")
            details = client.list_model_details()
            combos, combo_err = client.list_combos()
            def done():
                self._model_owner = {m["id"]: m["owned_by"] for m in details}
                self._combos = combos
                combo_ids = list(combos.keys())
                normal_ids = [m["id"] for m in details if m["id"] not in combos]
                ids = combo_ids + normal_ids
                self._all_model_ids = ids
                keep = self.combo_api_model.model_id()
                self.combo_api_model.set_models(ids, select=keep if keep in ids else (ids[0] if ids else ""))
                self.btn_test_models.config(state="normal")
                msg = f"Kết nối OK - {len(ids)} model ({len(combo_ids)} điều phối, {len(normal_ids)} đơn)"
                if combo_err:
                    msg += " | Cảnh báo combo: " + combo_err[:80]
                self.lbl_api_status.config(text=msg, foreground=theme.status("orange") if combo_err else "green")
                self.lbl_model_count.config(text=f"Tổng: {len(ids)} model", foreground=theme.status("green"))
                self._update_model_kind_label()
            self.root.after(0, done)
        except Exception as e:
            err = str(e)
            def fail():
                self.lbl_api_status.config(text="Kết nối thất bại (Failed)", foreground=theme.status("red"))
                self._notify_error("Lỗi Kết Nối (Connection Error)", err)
            self.root.after(0, fail)

    def start_fetch_models(self, silent=False):
        _, base_url, api_key, _ = self.collect_api_config()
        threading.Thread(target=self._connect_worker, args=(base_url, api_key), daemon=True).start()


    def start_test_models(self):
        """Test model đang chọn. Nếu là combo/điều phối -> test toàn bộ model con."""
        selected = self.combo_api_model.model_id()
        if not selected:
            return self._notify_warn("Cảnh báo (Warning)", "Hãy chọn model cần test trước!")
        _, base_url, api_key, _ = self.collect_api_config()

        self._stop_test.clear()
        self._test_results = {}
        self._ok_model_ids = []
        self._tested_group = selected
        self.btn_test_models.config(state="disabled")
        self.btn_stop_test.config(state="normal")
        self._clear_test_log()
        self.lbl_model_count.config(text="Đang đọc danh sách model... (Reading list)", foreground=theme.status("orange"))
        self._append_test_log(selected, "Đang xác định danh sách test", "", "info")
        threading.Thread(target=self._resolve_and_test_worker,
                         args=(base_url, api_key, selected), daemon=True).start()

    def stop_test_models(self):
        """Yêu cầu dừng đợt test model đang chạy."""
        self._stop_test.set()
        self._append_test_log("(hệ thống)", "Đã yêu cầu dừng test", "", "info")

    def _resolve_and_test_worker(self, base_url, api_key, selected):
        try:
            client = OpenAICompatClient(base_url=base_url, api_key=api_key, model=selected)
            targets, is_combo = client.resolve_test_targets(selected)
        except Exception as e:
            err = str(e)
            def bad():
                self._append_test_log(selected, "KHÔNG ĐỌC ĐƯỢC DANH SÁCH", err[:150], "fail")
                self.lbl_model_count.config(text="Test thất bại (Failed)", foreground=theme.status("red"))
                self.btn_test_models.config(state="normal")
                self.btn_stop_test.config(state="disabled")
            self.root.after(0, bad)
            return
        if not targets:
            def empty():
                self._append_test_log(selected, "COMBO RỖNG", "Không có model con nào để test.", "fail")
                self.lbl_model_count.config(text="Không có model để test", foreground=theme.status("red"))
                self.btn_test_models.config(state="normal")
                self.btn_stop_test.config(state="disabled")
            self.root.after(0, empty)
            return
        kind_label = "combo (điều phối)" if is_combo else "model đơn"
        self.root.after(0, lambda: self._append_test_log(
            selected, f"Test {kind_label}", f"{len(targets)} model cần đo", "info"))
        self._test_models_worker(base_url, api_key, targets, selected, is_combo)

    def _test_models_worker(self, base_url, api_key, targets, selected, is_combo):
        from concurrent.futures import ThreadPoolExecutor
        results = {}

        try:
            provider_status = OpenAICompatClient(base_url=base_url, api_key=api_key,
                                                 model=selected).list_providers()
        except Exception:
            provider_status = {}

        def lookup_status(mid):
            low = (mid or "").lower()
            best_len, best_val = -1, ""
            for key, status in provider_status.items():
                if key and low.startswith(key.lower()) and len(key) > best_len:
                    best_len, best_val = len(key), status
            return best_val

        def probe(mid):
            pstat = lookup_status(mid)
            try:
                client = OpenAICompatClient(base_url=base_url, api_key=api_key, model=mid)
                text, warns = client.probe()
                note = "OK: " + (text[:60] or "(rỗng)")
                if warns:
                    note += " | " + "; ".join(warns)
                return mid, note, pstat
            except Exception as exc:
                detail = str(exc).replace(chr(10), " ")
                group, label = OpenAICompatClient.classify_error(detail)
                return mid, "FAIL[" + group + "] " + label + " :: " + detail[:180], pstat

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(probe, m) for m in targets]
            for fut in futures:
                if self._stop_test.is_set():
                    pool.shutdown(wait=False, cancel_futures=True)
                    break
                mid, res, pstat = fut.result()
                results[mid] = res
                ok = res.startswith("OK")
                warn = ok and " | " in res
                note = ""
                if not ok:
                    result = "FAIL"
                    note = res.split(" :: ", 1)[-1][:150]
                    if pstat and pstat != "active":
                        note += "  (nhà cung cấp: " + pstat + ")"
                elif warn:
                    result = "OK!"
                    note = res.split(" | ", 1)[-1][:150]
                else:
                    result = "OK"
                    note = res[4:].split(" | ", 1)[0][:150]
                color = "ok" if result == "OK" else ("warn" if result == "OK!" else "fail")
                self._model_status[mid] = color
                self.root.after(0,
                    lambda m=mid, r=result, n=note, c=color: self._append_test_log(m, r, n, c))

        self._test_results_cache = results

        def finalize():
            self._test_results = results
            if is_combo:
                ok_models = [m for m in targets if results.get(m, "").startswith("OK")]
            else:
                ok_models = [selected] if results.get(selected, "").startswith("OK") else []
            self._ok_model_ids = ok_models
            ok_count = sum(1 for v in results.values() if v.startswith("OK"))
            fail_count = len(results) - ok_count
            usable = list(dict.fromkeys(ok_models))
            if usable:
                keep = selected if (not is_combo and results.get(selected, "").startswith("OK")) else usable[0]
                self.combo_api_model.set_models(self._all_model_ids or list(results), select=keep)
            else:
                self.combo_api_model.refresh()
            groups = {}
            for text in results.values():
                if text.startswith("FAIL["):
                    groups[text[5:text.index("]")]] = groups.get(text[5:text.index("]")], 0) + 1
            note = ""
            if groups:
                readable = {"credit": "thiếu credit", "missing": "chưa có endpoint",
                            "auth": "key sai", "limit": "nghẽn lượt gọi", "other": "lỗi khác"}
                note = "  (" + ", ".join(f"{readable.get(k, k)}: {v}" for k, v in sorted(groups.items())) + ")"
            self.lbl_model_count.config(
                text=f"Test '{selected}': {ok_count} OK / {fail_count} FAIL" + note,
                foreground=theme.status("green") if ok_count else "red")
            self.combo_api_model.refresh()
            self.btn_test_models.config(state="normal")
            self.btn_stop_test.config(state="disabled")
            self._update_model_kind_label()
        self.root.after(0, finalize)

    def show_test_detail(self, event=None):
        """Nháy đúp một dòng trong bảng -> hộp thoại hiện chi tiết đầy đủ."""
        try:
            row = self.tree_test.focus()
            if not row:
                return
            values = self.tree_test.item(row, "values")
            model = values[0] if values else ""
        except Exception:
            return
        detail = self._test_results.get(model) or self._test_results_cache.get(model)
        if detail:
            self._notify_info("Chi tiết: " + model, str(detail))

    def _update_model_kind_label(self):
        try:
            mid = self.combo_api_model.model_id()
            if mid in self._combos:
                n = len(self._combos[mid])
                text = f"Loại: Điều phối API (combo {n} model con) - Test sẽ đo từng model con"
            elif self._model_owner.get(mid, "").lower() == "combo":
                text = "Loại: Điều phối API (combo)"
            elif mid:
                text = f"Loại: Model đơn ({self._model_owner.get(mid) or 'không rõ'})"
            else:
                text = "Loại: -"
            self.lbl_model_kind.config(text=text)
        except Exception:
            pass

    def rotation_pool(self):
        """Danh sach model de xoay vong. Neu dang chon COMBO thi xoay trong chinh
        combo do; nguoc lai xoay qua cac model da Test OK.
        (Rotation pool: members of the selected combo, else every model that tested OK.)"""
        current = self.combo_api_model.model_id()
        members = self._combos.get(current)
        if members:
            ok_members = [m for m in members if self._model_status.get(m) in ("ok", "warn")]
            return (ok_members or list(members)), current
        return list(self._ok_model_ids), current

    def rotate_after_failure(self, reason=""):
        """API lỗi (hết quota / không phản hồi) -> chọn model kế tiếp để thử.
        Trả về (model mới, '') hoặc (None, lý do không xoay được).
        (Switch to the next model after an API failure; honours combo membership.)"""
        if not self.var_rotate.get():
            return None, "đang tắt tự động đổi model"
        pool, current = self.rotation_pool()
        if current:
            self._model_status[current] = "fail"
        if len(pool) <= 1:
            return None, "chỉ có 1 model trong nhóm xoay vòng"
        nxt = pool[self._rotate_index % len(pool)]
        self._rotate_index += 1
        detail = "nhóm combo" if current in self._combos else "các model OK"
        self.log_event("Lỗi API (%s) -> xoay vòng sang model: %s [%s]"
                       % (str(reason)[:120] or "không rõ", nxt, detail), "warn")
        self.set_activity("API lỗi → đang thử model khác: %s" % nxt, "orange")
        return nxt, ""

    def get_available_model(self):
        """Model se chay: dung model nguoi dung chon.
        KHONG xoay vong o day - xoay chi xay ra trong rotate_after_failure,
        tuc la khi API that bai. Neu xoay o day thi moi lot lai doi model ma
        khong co loi gi, nguoi dung chon model nao cung chay khac.
        (Model for this run: the selected one. Rotation happens only on failure.)"""
        return self.combo_api_model.model_id()

    # ==========================================
    # DIEU KHIEEN BROWSER / CHROME (Web LLM mode)
    # ==========================================
    def manual_reset_chrome(self, scope="all"):
        """Dong Chrome o nen vi driver.quit()/taskkill co the mat vai giay."""
        self.browser_mgr.stop_event.set()
        self.browser_mgr.pause_event.clear()
        threading.Thread(target=self.browser_mgr.kill_chrome, daemon=True).start()
        if scope in ("prompt", "all"):
            self.btn_run_llm.config(state="normal")
            self.btn_pause_prompt.config(state="disabled", text="⏸ Tạm dừng")
            self.lbl_llm_status.config(text="Đã dừng an toàn (Safely stopped)", foreground=theme.status("blue"))
        if scope in ("scene", "all"):
            self.btn_group_scenes.config(state="normal")
            self.btn_pause_scene.config(state="disabled", text="⏸ Tạm dừng")
            self.lbl_status_right.config(text="Đã dừng an toàn (Safely stopped)", foreground=theme.status("blue"))

    def toggle_pause_scene(self):
        self._toggle_pause(self.btn_pause_scene, self.lbl_status_right)

    def toggle_pause_prompt(self):
        self._toggle_pause(self.btn_pause_prompt, self.lbl_llm_status)

    def _toggle_pause(self, btn, lbl):
        if self.browser_mgr.pause_event.is_set():
            self.browser_mgr.pause_event.clear()
            btn.config(text="⏸ Tạm dừng")
            lbl.config(text="Đã tiếp tục (Resumed)", foreground=theme.status("orange"))
        else:
            self.browser_mgr.pause_event.set()
            btn.config(text="▶ Tiếp tục")
            lbl.config(text="Đã tạm dừng — bấm Tiếp tục", foreground=theme.status("blue"))

    def llm_for_task(self):
        """LLM của bước đang chạy: Bước 2 và Bước 4 chọn độc lập.
        (LLM for the running step: step 2 and step 4 choose independently.)"""
        task = getattr(self.browser_mgr, "current_task", None)
        var = self.prompt_llm_var if task == "prompt" else self.llm_var
        name = (var.get() or "").strip()
        if name not in LLM_URLS:
            name = "ChatGPT"
            var.set(name)
        return name

    def web_llm_name(self):
        """LLM cho chế độ Web, luôn nằm trong LLM_URLS (khi cấu hình lưu tên lạ thì tự sửa).
        (Normalises the Web LLM name so selectors/URL lookup never miss.)"""
        name = (self.llm_var.get() or "").strip()
        if name not in LLM_URLS:
            self.set_activity(f"LLM Web không nhận diện được ({name}) -> dùng ChatGPT", "orange")
            self.llm_var.set("ChatGPT")
            name = "ChatGPT"
        return name

    def manual_open_login(self):
        """Mở Chrome tới trang chat. ChatGPT/Gemini chat được không cần đăng nhập nên
        không hiện hộp thoại, không nag đăng nhập - chỉ ghi tình trạng lên thanh dưới cùng,
        kèm theo dõi tiến trình để báo nếu chromedriver treo (no popup, with watchdog)."""
        llm_name = self.web_llm_name()
        target_url = LLM_URLS.get(llm_name, "https://chatgpt.com/")
        self._chrome_pending = True
        self._chrome_started = time.time()
        self.set_activity(f"Đang mở Chrome tới {llm_name}... (Opening Chrome)", "blue")

        def worker():
            strays = 0
            try:
                import subprocess
                out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq chromedriver.exe", "/NH"],
                                     capture_output=True, text=True, timeout=5).stdout
                strays = out.lower().count("chromedriver.exe")
            except Exception:
                strays = 0
            if strays >= 3:
                self.set_activity(f"Có {strays} chromedriver cũ đang chạy nền - có thể làm lần mở này chậm hơn, "
                                  "bấm 🛑 Dừng để dọn", "orange")
            try:
                self.browser_mgr.get_task_tab("login_prep", target_url)
            except Exception as err:
                self._chrome_pending = False
                self.set_activity(f"Không mở được Chrome: {err}", "red")
                return
            self._chrome_pending = False
            self.set_activity(f"Đã mở {llm_name} sau {round(time.time() - self._chrome_started, 1)}s - "
                              "dùng được ngay (trang hỏi tài khoản thì đăng nhập, tool tự ghi nhớ)",
                              "green")

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(20000, self._chrome_launch_watchdog)

    def _chrome_launch_watchdog(self):
        """Báo tiến trình mở Chrome trên thanh trạng thái; không hộp thoại, không chặn."""
        if not getattr(self, "_chrome_pending", False):
            return
        waited = time.time() - getattr(self, "_chrome_started", time.time())
        if waited >= 90:
            self.set_activity(f"Chrome chưa mở xong sau {round(waited)}s - bấm 🛑 Dừng để đóng tiến trình cũ rồi thử lại "
                              "(thường do còn cửa sổ Chrome/driver cũ đang giữ)", "red")
        elif waited >= 25:
            self.set_activity(f"Vẫn đang chờ Chrome/chromedriver ({round(waited)}s) - "
                              "lần đầu có thể mất chút thời gian tải driver", "orange")
        self.root.after(10000, self._chrome_launch_watchdog)

    # ==========================================
    # BƯỚC 1: XỬ LÝ ÂM THANH / SRT CÂU
    # ==========================================

    def check_whisper_status(self):
        installed = self.whisper_runtime.is_installed()
        if installed:
            self.lbl_whisper_status.config(text='✅ Đã sẵn sàng (Ready)', foreground=theme.status("green"))
            self.btn_whisper_download.config(state='disabled', text='✅ Whisper đã cài')
        else:
            self.lbl_whisper_status.config(text='⚠️ Chưa cài (Not installed)', foreground=theme.status("red"))

    def start_whisper_install(self):
        self.btn_whisper_download.config(state="disabled")
        self.lbl_whisper_status.config(text='Đang tải Whisper... (Downloading)', foreground=theme.status("orange"))
        threading.Thread(target=self._whisper_install_worker, daemon=True).start()

    def _whisper_install_worker(self):
        try:
            def progress(msg, color):
                self.root.after(0, lambda m=msg, c=color: self.lbl_whisper_status.config(text=m[:80], foreground=theme.status(c)))
            self.whisper_runtime.install(progress_cb=progress)
            self.root.after(0, self.check_whisper_status)
        except Exception as e:
            err = str(e)
            self.root.after(0, lambda m=err: [
                self.lbl_whisper_status.config(text='❌ Tải thất bại (Failed)', foreground=theme.status("red")),
                self.btn_whisper_download.config(state='normal'),
                self._notify_error('Lỗi Tải Whisper (Whisper Download Error)', m)
            ])

    def browse_audio(self):
        file_path = filedialog.askopenfilename(
            filetypes=[("Audio/Video Files", "*.mp3 *.wav *.m4a *.mp4 *.mkv *.aac *.flac *.ogg"), ("All Files", "*.*")])
        if file_path:
            self.entry_audio_path.delete(0, tk.END)
            self.entry_audio_path.insert(0, file_path)

    def browse_srt_in(self):
        file_path = filedialog.askopenfilename(filetypes=[("SRT Files", "*.srt"), ("All Files", "*.*")])
        if file_path:
            self.entry_srt_in_path.delete(0, tk.END)
            self.entry_srt_in_path.insert(0, file_path)


    def start_srt_sentence_thread(self):
        audio_p = self.entry_audio_path.get().strip()
        srt_p = self.entry_srt_in_path.get().strip()
        ref_t = self.txt_reference_text.get("1.0", tk.END).strip()
        model_name = self.combo_whisper_model.get().strip() or "base"

        # Thứ tự ưu tiên: SRT > MP3 > txt. txt là tu chọn, dùng để sửa chính tả.
        if not audio_p and not srt_p:
            if ref_t:
                return self._auto_step_cancelled(
                    "Thiếu file nguồn (Missing source)",
                    "Chỉ có file kịch bản gốc. Cần thêm file SRT hoặc file MP3 để chạy Bước 1.")
            return self._auto_step_cancelled("Cảnh báo (Warning)", "Vui lòng chọn File SRT hoặc File MP3!")
        if not ref_t and srt_p:
            self.log_event("Bước 1: không có file kịch bản gốc - dùng nội dung SRT làm chuẩn.", "info")
        elif not ref_t:
            self.log_event("Bước 1: không có file kịch bản gốc - dùng nội dung Whisper.", "info")
        if audio_p and not srt_p and not self.whisper_runtime.is_installed():
            return self._auto_step_cancelled("Cần Whisper (Whisper required)",
                                          "Chưa cài Whisper. Bấm nút Tải Whisper ở trên trước.")

        self.btn_gen_srt_sentence.config(state="disabled")
        self.lbl_status_left.config(text="Đang nhận diện & đối chiếu... (Working)", foreground=theme.status("orange"))
        threading.Thread(target=self._run_srt_sentence_worker,
                         args=(audio_p, srt_p, ref_t, model_name), daemon=True).start()


    def _run_srt_sentence_worker(self, audio_path, srt_in_path, ref_text, model_name):
        try:
            def on_status(msg, color="orange"):
                self.root.after(0, lambda m=msg: self.lbl_status_left.config(text=m, foreground=theme.status("orange")))

            sentences_data, srt_content, audio_dur = run_whisper_alignment(
                audio_path, srt_in_path, ref_text,
                whisper_runtime=self.whisper_runtime, model_name=model_name, status_cb=on_status)
            self.temp_sentence_timings = sentences_data
            self.audio_duration = audio_dur
            self.root.after(0, self.update_srt_sentence_gui, srt_content)
        except StopRun:
            self.root.after(0, self.handle_whisper_error_left, "Đã dừng theo yêu cầu (Stopped)")
        except Exception as e:
            self.root.after(0, self.handle_whisper_error_left, str(e))

    def _notify_auto_step(self, ok, message=""):
        """Báo cho luồng auto biết 1 bước GUI đã xong (True) hoặc lỗi (False).
        Kèm tên bước đang chạy để auto bỏ qua tín hiệu cũ của bước trước,
        tránh bước sau bị tưởng đã xong khi bước trước còn chạy.
        (Tagged with the active step name so stale signals are ignored.)"""
        hook = getattr(self, "_auto_step_hook", None)
        if hook is not None:
            hook(getattr(self, "_auto_current_step", None), bool(ok), message)

    def update_srt_sentence_gui(self, srt_content):
        self.btn_gen_srt_sentence.config(state="normal")
        theme.set_numbered(self.txt_srt_sentence, srt_content)
        self.lbl_status_left.config(text="Hoàn tất Bước 1!", foreground=theme.status("green"))
        self._notify_auto_step(True)

    def handle_whisper_error_left(self, err_msg):
        self.btn_gen_srt_sentence.config(state="normal")
        self.lbl_status_left.config(text="Thất bại!", foreground=theme.status("red"))
        self.log_event("BƯỚC 1 thất bại [%s]: %s" % (self._retry_tag(), str(err_msg)[:300]), "error")
        self._notify_error("Lỗi Bước 1", f"Chi tiết:\n{err_msg}")
        self._notify_auto_step(False, err_msg)

    # ==========================================
    # BƯỚC 2: GỘP SRT THEO ĐOẠN / PHÂN CẢNH
    # ==========================================

    def start_group_scenes_thread(self):
        if not self.temp_sentence_timings:
            return self._auto_step_cancelled("Cảnh báo (Warning)", "Hãy hoàn thành Bước 1 trước!")

        raw_max = self.entry_max_duration.get().strip()
        raw_min = self.entry_min_duration.get().strip()
        max_sec, min_sec = None, None
        try:
            max_sec = float(raw_max) if raw_max else None
            min_sec = float(raw_min) if raw_min else None
        except ValueError:
            return self._auto_step_cancelled("Lỗi nhập liệu (Bad input)",
                                        "Min/Max giây phải là số (numbers only).")
        if max_sec is not None and max_sec <= 0:
            return self._auto_step_cancelled("Lỗi nhập liệu (Bad input)", "Max giây phải lớn hơn 0.")
        if min_sec is not None and min_sec <= 0:
            return self._auto_step_cancelled("Lỗi nhập liệu (Bad input)", "Min giây phải lớn hơn 0.")
        if min_sec is not None and max_sec is not None and min_sec > max_sec:
            return self._auto_step_cancelled("Lỗi nhập liệu (Bad input)", "Min giây không được lớn hơn Max giây.")

        self.ref_text_cache = self.txt_reference_text.get("1.0", tk.END).strip()
        self.scene_method_cache = self.scene_method.get()
        self.scene_mode_cache = self.scene_input_mode.get()
        self.is_api_cache = self.ai_source_var.get() == "api"

        if self.scene_method_cache == "llm" and self.is_api_cache:
            self._api_client = None
            if not self.prepare_api_client():
                return

        self.browser_mgr.stop_event.clear()
        self.browser_mgr.pause_event.clear()
        self.browser_mgr.active_run_llm["scene"] = None
        self.btn_group_scenes.config(state="disabled")
        self.btn_pause_scene.config(state="normal", text="⏸ Tạm dừng")
        threading.Thread(target=self.run_group_scenes,
                         args=(max_sec, min_sec), daemon=True).start()


    def _merge_scenes_once(self, client, ref_text, max_sec, min_sec, set_status):
        """Gộp bằng API. Lỗi thì ném lên để vòng lặp bên ngoài quyết định thử lại.
        (One API attempt; raises so the outer loop decides whether to retry.)"""
        scenes = align_scenes_via_llm_api(client, self.temp_sentence_timings, ref_text, max_sec, min_sec,
                                          status_cb=set_status, stop_check=self.api_stop_requested)
        if not scenes:
            raise LLMApiError("AI trả kết quả không hợp lệ (không có cảnh nào đọc được).")
        return scenes

    def _merge_scenes_web_once(self, ref_text, max_sec, min_sec, set_status):
        """Gộp bằng LLM Web. KHÔNG fallback thuật toán - thất bại thì ném lỗi.
        (One Web LLM attempt; never falls back to the algorithm.)"""
        scenes = align_scenes_via_llm_web(self.browser_mgr, self.temp_sentence_timings, ref_text,
                                          max_sec, min_sec, mode=self.scene_mode_cache,
                                          status_cb=set_status, allow_algorithm_fallback=False)
        if not scenes:
            raise LLMApiError("LLM Web không trả về kết quả gộp cảnh hợp lệ.")
        return scenes

    def run_group_scenes(self, max_sec=None, min_sec=None):
        try:
            self.browser_mgr.check_stop("scene")
            ref_text = self.ref_text_cache
            method = self.scene_method_cache
            is_api = self.is_api_cache
            limit = self._retry_limit()

            def set_status(text, color="orange"):
                self.root.after(0, lambda t=text, c=color: self.lbl_status_right.config(text=t, foreground=theme.status(c)))

            if method == "llm":
                # Chọn AI thì phải làm đúng AI: thất bại thì thử lại theo hộp,
                # tuyệt đối không tự đổi sang thuật toán.
                source = "API" if is_api else "LLM Web"
                attempt = 0
                while True:
                    attempt += 1
                    self.api_stop_requested()      # ném StopRun nếu người dùng bấm Dừng
                    tag = "lần %d" % attempt if limit is None else "lần %d/%d" % (attempt, limit)
                    try:
                        set_status("%s đang gộp cảnh - %s..." % (source, tag), "orange")
                        if is_api:
                            client = self._api_client or self.build_api_client()
                            scenes = self._merge_scenes_once(client, ref_text, max_sec, min_sec, set_status)
                        else:
                            scenes = self._merge_scenes_web_once(ref_text, max_sec, min_sec, set_status)
                        break
                    except StopRun:
                        raise
                    except BrowserActionRequired as err:
                        set_status("LLM Web cần xử lý trên Chrome: %s" % str(err)[:150], "red")
                        raise
                    except Exception as err:
                        if limit is not None and attempt >= limit:
                            set_status("%s thất bại sau %d lần: %s" % (source, limit, str(err)[:150]), "red")
                            raise
                        # Web: đóng hẳn Chrome rồi mở lại từ đầu. Dùng lại cửa sổ cũ
                        # đang treo sẽ dán chồng prompt vào ô chứa đầy -> quay vô hạn.
                        # KHÔNG áp cho API: API không có cửa sổ.
                        if not is_api:
                            set_status("%s lỗi - đóng Chrome và mở lại từ đầu..." % source, "red")
                            self.browser_mgr.restart_chrome()
                        wait = min(3 * attempt, 30)
                        set_status("%s lỗi - thử lại sau %ds (%s)" % (source, wait, tag), "red")
                        if not self._interruptible_wait(wait):
                            raise StopRun()
                self.reset_ask_retry_count()
                set_status("%s đã gộp xong." % source, "green")
            else:
                set_status("Đang gộp bằng thuật toán... (Algorithm)", "blue")
                scenes = align_scenes_via_algorithm(self.temp_sentence_timings, max_sec)

            scenes = enforce_max_sec(scenes, max_sec)
            scenes = enforce_min_sec(scenes, min_sec, max_sec)
            srt_content = render_scenes_to_srt(scenes)
            self.root.after(0, self.update_srt_scene_gui, srt_content)
        except StopRun:
            self.root.after(0, self.handle_whisper_error_right, "Đã dừng (Stopped)")
        except Exception as e:
            self.root.after(0, self.handle_whisper_error_right, str(e))

    def _interruptible_wait(self, seconds):
        """Chờ có thể bị nút Dừng cắt ngay."""
        deadline = time.time() + max(1, int(seconds))
        while time.time() < deadline:
            if self.api_stop_requested():
                return False
            time.sleep(0.3)
        return not self.api_stop_requested()

    def close_llm_tab(self, scope):
        """Đóng tab LLM đã dùng xong (chỉ khi đang chạy Web LLM).
        (Close the Web LLM tab after a successful step.)"""
        try:
            var = self.prompt_source_var if scope == "prompt" else self.ai_source_var
            if var.get() != "web":
                return
            self.browser_mgr.close_task_tab(scope)
        except Exception:
            pass

    def update_srt_scene_gui(self, srt_content):
        self.btn_group_scenes.config(state="normal")
        self.btn_pause_scene.config(state="disabled", text="⏸ Tạm dừng")
        theme.set_numbered(self.txt_srt_scene, srt_content)
        self.close_llm_tab("scene")
        self.lbl_status_right.config(text="Hoàn tất Bước 2!", foreground=theme.status("green"))
        self._notify_auto_step(True)

    def handle_whisper_error_right(self, err_msg):
        self.btn_group_scenes.config(state="normal")
        self.btn_pause_scene.config(state="disabled", text="⏸ Tạm dừng")
        self.lbl_status_right.config(text="Thất bại!", foreground=theme.status("red"))
        self.log_event("BƯỚC 2 thất bại [%s]: %s" % (self._retry_tag(), str(err_msg)[:300]), "error")
        self._notify_error("Lỗi", f"Chi tiết:\n{err_msg}")
        self._notify_auto_step(False, err_msg)

    # ==========================================
    # XUẤT FILE & NẠP DỮ LIỆU
    # ==========================================
    # ==========================================
    # CHE DO AUTO NHIEU KICH BAN
    # ==========================================
    def start_auto_run(self):
        root = self.auto_root.get().strip()
        if not root or not os.path.isdir(root):
            return self._notify_warn("Cảnh báo (Warning)",
                                     "Hãy chọn thư mục chứa các thư mục con kịch bản!")
        if self._auto_runner is not None:
            return self._notify_warn("Cảnh báo (Warning)", "Auto đang chạy, bấm 'Dừng auto' trước!")

        out_dir = self.auto_out.get().strip()
        if out_dir and not os.path.isdir(out_dir):
            return self._notify_warn("Cảnh báo (Warning)", "Thư mục xuất không tồn tại!")

        # KHÔNG ghi đè cài đặt của Bước 2 / Bước 4 ở đây: mỗi bước giữ
        # đúng lựa chọn API/Web của riêng tab nó (độc lập với nhau).
        config = {
            "root": root,
            "out": out_dir,
            "sort": SORT_BY_LABEL.get(self.auto_sort_label.get(), "alphabet"),
            "script_source": self.auto_script_source.get(),
            "prompt_source": self.auto_prompt_source.get(),
            "retries": self.api_retry_var.get(),
        }

        self._auto_active = True
        self.btn_auto_run.config(state="disabled")
        self.btn_auto_stop.config(state="normal")
        self._auto_log("Đang chạy tự động...", "blue")
        self._auto_runner = AutoRunner(self, self._auto_log)
        self._auto_runner.start_pump()      # bom lenh tren luong chinh
        threading.Thread(target=self._auto_worker, args=(config,), daemon=True).start()

    def _auto_worker(self, config):
        runner = self._auto_runner
        try:
            runner.run(config)
        finally:
            self.root.after(0, self._finish_auto_run)

    def _finish_auto_run(self):
        self._auto_active = False
        self._auto_runner = None
        self.btn_auto_run.config(state="normal")
        self.btn_auto_stop.config(state="disabled")

        # PTV2: het TOAN BO kich ban (hoac nguoi dung bam Dung) -> dung hanh
        # Chrome cua tool. Thread nen vi kill_chrome() cho Playwright tra loi,
        # goi truc tiep tren luong giao dien se treo vai giay.
        def _shutdown_browser():
            self.browser_mgr.kill_chrome()      # set stop_event ben trong
            # Auto đã kết thúc: xoá cờ Dừng lại, nếu không nút 'Mở Chrome Chat'
            # và các lần chạy tay sau đó sẽ bị StopRun ngay lập tức.
            self.browser_mgr.stop_event.clear()
        threading.Thread(target=_shutdown_browser, daemon=True).start()

    def stop_auto_run(self):
        runner = getattr(self, "_auto_runner", None)
        if runner is not None:
            runner.request_stop()
        self.browser_mgr.stop_event.set()
        self._auto_log("Đã yêu cầu dừng auto...", "blue")

    def export_srt_sentence(self):
        export_text_widget_content(self.txt_srt_sentence, default_ext=".srt")

    def export_srt_scene(self):
        export_text_widget_content(self.txt_srt_scene, default_ext=".srt")

    # ==========================================
    # BƯỚC 3: TÁCH KỊCH BẢN
    # ==========================================
    def load_script_left(self):
        content = theme.get_numbered(self.txt_srt_sentence)
        if not content:
            return self._auto_step_cancelled("Cảnh báo", "Ô SRT Câu (Bản trái Bước 1) đang rỗng!")
        self.txt_script_in_l.delete("1.0", tk.END)
        self.txt_script_in_l.insert(tk.END, content)

    def process_script_left(self):
        raw_text = self.txt_script_in_l.get("1.0", tk.END).strip()
        sentences, out_text, stats_str = process_script_core(raw_text)
        theme.set_numbered(self.txt_script_out_l, out_text)
        self.lbl_stats_l.config(text=stats_str)

    def load_script_right(self):
        content = theme.get_numbered(self.txt_srt_scene)
        if not content:
            return self._auto_step_cancelled("Cảnh báo", "Ô SRT Đoạn (Bản phải Bước 2) đang rỗng!")
        self.txt_script_in_r.delete("1.0", tk.END)
        self.txt_script_in_r.insert(tk.END, content)

    def process_script_right(self):
        raw_text = self.txt_script_in_r.get("1.0", tk.END).strip()
        sentences, out_text, stats_str = process_script_core(raw_text)
        theme.set_numbered(self.txt_script_out_r, out_text)
        self.lbl_stats_r.config(text=stats_str)

    # ==========================================
    # BƯỚC 4: SINH PROMPT
    # ==========================================
    def load_prompt_in_left(self):
        content = theme.get_numbered(self.txt_script_out_l)
        if not content:
            return self._auto_step_cancelled("Cảnh báo", "Kết quả Kịch bản Câu (Bản trái Bước 3) đang rỗng!")
        self.txt_prompt_input.delete("1.0", tk.END)
        self.txt_prompt_input.insert(tk.END, content)

    def load_prompt_in_right(self):
        content = theme.get_numbered(self.txt_script_out_r)
        if not content:
            return self._auto_step_cancelled("Cảnh báo", "Kết quả Kịch bản Đoạn (Bản phải Bước 3) đang rỗng!")
        self.txt_prompt_input.delete("1.0", tk.END)
        self.txt_prompt_input.insert(tk.END, content)


    def start_llm_thread(self):
        script_text = self.txt_prompt_input.get("1.0", tk.END).strip()
        if not script_text:
            return self._auto_step_cancelled("Cảnh báo (Warning)", "Hãy nhập kịch bản cần sinh prompt!")

        lines = [l.strip() for l in script_text.splitlines() if l.strip()]
        if not lines:
            return self._auto_step_cancelled("Cảnh báo (Warning)", "Kịch bản không có dòng hợp lệ nào!")

        try:
            batch_size = int(self.spin_batch_size.get())
        except (ValueError, tk.TclError):
            if str(self.spin_batch_size.get()).strip():
                return self._auto_step_cancelled("Lỗi nhập liệu (Bad input)", "Batch Size phải là số nguyên.")
            return self._auto_step_cancelled("Thiếu số Batch (Missing Batch)",
                                             "Nhập số prompt muốn gửi mỗi lần vào ô Batch (tối thiểu 10).")
        if batch_size < 10:
            return self._auto_step_cancelled("Lỗi nhập liệu (Bad input)",
                                             "Batch Size tối thiểu là 10 prompt/lần.")

        style_prefix = self.entry_style.get().strip()
        is_api = self.prompt_source_var.get() == "api"

        if is_api:
            self._api_client = None
            if not self.prepare_api_client():
                return
            chosen = self.get_available_model()
            if not chosen:
                return self._auto_step_cancelled("Chưa chọn model (No model)",
                                            "Vào tab Cài Đặt Chung để kết nối và chọn model trước.")
            self._api_client.model = chosen

        self.temp_script_lines = lines
        self.batch_size_cache = batch_size
        self.style_prefix_cache = style_prefix

        self.browser_mgr.stop_event.clear()
        self.browser_mgr.pause_event.clear()
        self.browser_mgr.active_run_llm["prompt"] = None

        self.btn_run_llm.config(state="disabled")
        self.btn_pause_prompt.config(state="normal", text="⏸ Tạm dừng")
        self.lbl_llm_status.config(text="Đang chuẩn bị dữ liệu... (Preparing)", foreground=theme.status("orange"))
        self.txt_prompts.delete("1.0", tk.END)

        if is_api:
            threading.Thread(target=self._run_api_prompt_worker, daemon=True).start()
        else:
            threading.Thread(target=self._run_web_prompt_worker, daemon=True).start()

    def append_prompt_realtime(self, new_text):
        if not new_text:
            return
        if not new_text.endswith("\n"):
            new_text += "\n"
        theme.append_numbered(self.txt_prompts, new_text)
        self.txt_prompts.see(tk.END)


    def _run_api_prompt_worker(self):
        try:
            client = self._api_client or self.build_api_client()
            batch_size = self.batch_size_cache
            style_prefix = self.style_prefix_cache
            total = len(self.temp_script_lines)

            def set_status(text, color="orange"):
                self.root.after(0, lambda t=text, c=color: self.lbl_llm_status.config(text=t, foreground=theme.status(c)))

            def append_text(text):
                self.root.after(0, self.append_prompt_realtime, text)

            def on_api_error(reason):
                # Hết quota / không phản hồi -> đổi model ngay, không chờ hết số lần thử.
                m, _why = self.rotate_after_failure(reason)
                if m:
                    client.model = m

            # Giữ phần đã sinh: lỗi giữa chừng -> chạy lại từ đúng thứ kế tiếp,
            # không sinh lại những dòng đã có kết quả.
            done = 0
            while True:
                try:
                    done = run_api_prompt_generation(
                        api_client=client,
                        script_lines=self.temp_script_lines,
                        batch_size=batch_size,
                        style_prefix=style_prefix,
                        status_cb=set_status,
                        append_cb=append_text,
                        check_stop_fn=self.browser_mgr.check_stop,
                        stop_check=self.api_stop_requested,
                        max_attempts=self._retry_limit(),
                        resume_from=done,
                        on_batch_shrink=self._apply_batch_shrink,
                        on_error=on_api_error,
                    )
                    break
                except BatchFailure as partial:
                    done = max(done, partial.completed)
                    raise
            final_model = client.model
            self.root.after(0, lambda m=final_model: [
                self.btn_run_llm.config(state="normal"),
                self.btn_pause_prompt.config(state="disabled", text="⏸ Tạm dừng"),
                self.lbl_llm_status.config(text=f"✅ Hoàn tất sinh Prompt qua API ({m})!", foreground=theme.status("green")),
                self.close_llm_tab("prompt"),
                self._notify_auto_step(True)
            ])
        except StopRun:
            self.root.after(0, lambda: self.lbl_llm_status.config(text="Đã dừng (Stopped)", foreground=theme.status("blue")))
            self.root.after(0, lambda: self._notify_auto_step(False, "Đã dừng"))
        except Exception as e:
            err = str(e)
            def _fail():
                self.btn_run_llm.config(state="normal")
                self.btn_pause_prompt.config(state="disabled", text="⏸ Tạm dừng")
                self.lbl_llm_status.config(text="Thất bại! (Failed)", foreground=theme.status("red"))
                self.log_event("BƯỚC 4 sinh prompt thất bại [%s]: %s" % (self._retry_tag(), err[:300]), "error")
                self._notify_error("Lỗi Sinh Prompt (Prompt error)", err)
                self._notify_auto_step(False, err)
            self.root.after(0, _fail)

    def _run_web_prompt_worker(self):
        try:
            batch_size = self.batch_size_cache
            style_prefix = self.style_prefix_cache

            def set_status(text, color="orange"):
                self.root.after(0, lambda t=text, c=color: self.lbl_llm_status.config(text=t, foreground=theme.status(c)))

            def append_text(text):
                self.root.after(0, self.append_prompt_realtime, text)

            # Giữ phần đã sinh, chạy lại từ đúng thứ kế tiếp nếu giữa chừng lỗi.
            done = 0
            while True:
                try:
                    done = run_selenium_prompt_generation(
                        browser_mgr=self.browser_mgr,
                        script_lines=self.temp_script_lines,
                        batch_size=batch_size,
                        style_prefix=style_prefix,
                        status_cb=set_status,
                        append_cb=append_text,
                        max_attempts=self._retry_limit(),
                        resume_from=done,
                        on_batch_shrink=self._apply_batch_shrink,
                    )
                    break
                except BatchFailure as partial:
                    done = max(done, partial.completed)
                    raise
            self.root.after(0, lambda: [
                self.btn_run_llm.config(state="normal"),
                self.btn_pause_prompt.config(state="disabled", text="⏸ Tạm dừng"),
                self.lbl_llm_status.config(text="✅ Hoàn tất sinh Prompt!", foreground=theme.status("green")),
                self.close_llm_tab("prompt"),
                self._notify_auto_step(True)
            ])
        except StopRun:
            self.root.after(0, lambda: self.lbl_llm_status.config(text="Đã dừng (Stopped)", foreground=theme.status("blue")))
            self.root.after(0, lambda: self._notify_auto_step(False, "Đã dừng"))
        except Exception as e:
            err = str(e)
            def _fail():
                self.btn_run_llm.config(state="normal")
                self.btn_pause_prompt.config(state="disabled", text="⏸ Tạm dừng")
                self.lbl_llm_status.config(text="Thất bại! (Failed)", foreground=theme.status("red"))
                self.log_event("BƯỚC 4 sinh prompt thất bại [%s]: %s" % (self._retry_tag(), err[:300]), "error")
                self._notify_error("Lỗi Sinh Prompt (Prompt error)", err)
                self._notify_auto_step(False, err)
            self.root.after(0, _fail)
