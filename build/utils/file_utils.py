# -*- coding: utf-8 -*-
"""
Các hàm tiện ích thao tác với tệp tin và đường dẫn
(File and path utility functions)
"""
import os
import sys
import re
from tkinter import filedialog, messagebox
from utils.exceptions import FakeWord, FakeSegment
from utils.text_utils import parse_timestamp, has_meaningful_text

def get_base_dir():
    """Trả về thư mục gốc của chương trình (hỗ trợ cả chạy script và file đóng gói .exe).
    (Returns the program's root directory — works for both script and bundled .exe.)"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_data_dir():
    """Thư mục dữ liệu người dùng: config, log, Chrome profile, model Whisper.
    (User data directory: config, logs, Chrome profile, Whisper models.)

    Tách riêng khỏi mã nguồn và thư viện để `build/` và `_internal/` luôn
    sạch — không dính file sinh ra lúc chạy.
    """
    d = os.path.join(get_base_dir(), "data")
    os.makedirs(d, exist_ok=True)
    return d

def load_srt_as_segments(srt_path):
    """Đọc file SRT có sẵn và chuyển thành danh sách segment giả lập (FakeSegment).
    (Read an existing SRT file and convert to a list of FakeSegment objects.)"""
    segments = []
    if not os.path.exists(srt_path):
        return segments

    try:
        with open(srt_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except UnicodeDecodeError:
        with open(srt_path, 'r', encoding='utf-8-sig') as f:
            content = f.read()

    blocks = re.split(r'\n\s*\n', content.strip())
    time_pattern = re.compile(r'(\d{1,2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,\.]\d{3})')

    for block in blocks:
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        if len(lines) >= 2:
            time_match = time_pattern.search(lines[1]) if len(lines) > 1 else None
            text_lines = lines[2:]
            if not time_match and len(lines) >= 2:
                time_match = time_pattern.search(lines[0])
                text_lines = lines[1:]

            if time_match:
                st = parse_timestamp(time_match.group(1))
                et = parse_timestamp(time_match.group(2))
                txt = " ".join(text_lines)
                if has_meaningful_text(txt):
                    words_list = txt.split()
                    w_objs = []
                    if words_list:
                        dur_per_word = (et - st) / len(words_list)
                        for i, w in enumerate(words_list):
                            w_objs.append(FakeWord(w, st + i * dur_per_word, st + (i + 1) * dur_per_word))
                    segments.append(FakeSegment(txt, st, et, w_objs))
    return segments

NOTIFIER = None


def set_notifier(cb):
    """Đăng ký nơi nhận thông báo (thường là thanh trạng thái của tool).
    (Register a callback so utility messages go to the status bar, not popups.)"""
    global NOTIFIER
    NOTIFIER = cb


def _notify(title, msg="", color="blue"):
    if NOTIFIER is not None:
        try:
            NOTIFIER(f"{title}: {msg}" if msg else str(title), color)
            return
        except Exception:
            pass
    if color == "red":
        messagebox.showerror(title, msg)
    else:
        messagebox.showinfo(title, msg)


def export_text_widget_content(text_widget, default_ext=".txt"):
    """Mở hộp thoại lưu tệp cho nội dung trong Text widget.
    LUÔN bỏ tiền tố số thứ tự trước khi ghi: file xuất ra đúng chuẩn SRT /
    text thuần, import được vào CapCut (SRT sai định dạng sẽ bị lỗi ngay).
    (Always strips line-number prefixes so exported files stay standards-compliant.)"""
    from ui.theme import strip_numbers
    content = strip_numbers(text_widget.get("1.0", "end-1c").strip())
    if not content:
        _notify("Không có nội dung để xuất (Nothing to export)", "", "orange")
        return None

    filetypes = [("Text File", "*.txt"), ("SRT File", "*.srt"), ("All Files", "*.*")]
    if default_ext == ".srt":
        filetypes = [("SRT File", "*.srt"), ("Text File", "*.txt"), ("All Files", "*.*")]

    file_path = filedialog.asksaveasfilename(defaultextension=default_ext, filetypes=filetypes)
    if not file_path:
        return None
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError as err:
        _notify("Xuất tệp thất bại (Export failed)", str(err), "red")
        return None
    _notify("Đã xuất tệp", file_path, "green")
    return file_path
