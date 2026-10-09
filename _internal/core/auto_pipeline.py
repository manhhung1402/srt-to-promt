# -*- coding: utf-8 -*-
"""Chế độ Auto nhiều kịch bản: quét thư mục con, tìm file input và sắp xếp thứ tự chạy."""

import os

MEDIA_EXTS = (".mp3", ".wav", ".m4a", ".mp4", ".mkv", ".aac", ".flac", ".ogg")

SORT_ALPHABET = "alphabet"
SORT_NEWEST = "newest"
SORT_OLDEST = "oldest"
SORT_LABELS = {
    SORT_ALPHABET: "Alphabet (A → Z)",
    SORT_NEWEST: "Ngày tạo mới nhất",
    SORT_OLDEST: "Ngày tạo cũ nhất",
}


def list_script_folders(root_dir, order=SORT_ALPHABET):
    """Các thư mục con của root_dir, đã sắp xếp theo `order`."""
    if not root_dir or not os.path.isdir(root_dir):
        return []
    try:
        names = os.listdir(root_dir)
    except OSError:
        return []
    folders = [os.path.join(root_dir, name) for name in names
               if os.path.isdir(os.path.join(root_dir, name))]
    if order == SORT_NEWEST:
        folders.sort(key=lambda path: os.path.getmtime(path), reverse=True)
    elif order == SORT_OLDEST:
        folders.sort(key=lambda path: os.path.getmtime(path))
    else:
        folders.sort(key=lambda path: os.path.basename(path).lower())
    return folders


def _first_by_ext(folder, extensions):
    """File đầu tiên trong `folder` có đuối trong `extensions` (ưu tiên tên A→Z)."""
    try:
        names = sorted(os.listdir(folder), key=str.lower)
    except OSError:
        return ""
    for name in names:
        if os.path.splitext(name)[1].lower() in extensions:
            full = os.path.join(folder, name)
            if os.path.isfile(full):
                return full
    return ""


def find_input_files(folder):
    """Tìm input của 1 thư mục con kịch bản: media hoặc srt (cái nào có trước) + 1 txt."""
    srt = _first_by_ext(folder, (".srt",))
    media = _first_by_ext(folder, MEDIA_EXTS)
    return {
        "srt": srt,
        "media": media,
        # ưu tiên SRT: khởi Whisper, chạy nhanh hơn.
        "audio": "" if srt else media,
        "txt": _first_by_ext(folder, (".txt",)),
    }


def missing_inputs(found):
    """Kiểm tra thư mục con theo thứ tự ưu tiên SRT > MP3 > txt.
    txt là tu chọn (dùng sửa chính tả), nên chỉ thiếu khi không có SRT lẫn MP3.
    (A folder needs an SRT or an MP3; the .txt script is optional.)"""
    if not found.get("srt") and not found.get("media"):
        return ["thiếu file SRT hoặc MP3"]
    return []
