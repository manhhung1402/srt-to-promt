# -*- coding: utf-8 -*-
"""
Cac Exception va cau truc du lieu noi bo.

GHI CHAM QUAN TRONG: StopRun / LLMApiError / BrowserActionRequired bay gio
tro ve core/faults.py, khong con la class rieng o day.

Ly do: truoc day chung ton tai o CA HAI noi. Mot ben goi tu day, ben kia tu
core.faults. Hai class cung ten nhung KHONG phai cung doi tuong, nen
`except StopRun` o mot noi KHONG bat duoc StopRun nem o noi kia. Nghi
do la lenh "Dung" bi bo qua - dung thu can dung.

core/faults.py khong import tu file nay nen khong tao vong tron.
(These names are aliases, not duplicate classes -- canonical home is core/faults.py.)
"""
from core.faults import BrowserActionRequired, LLMApiError, StopRun

__all__ = ["StopRun", "LLMApiError", "BrowserActionRequired",
           "browser_action_state", "FakeSegment", "FakeWord"]


def browser_action_state(message):
    """Lay state tu loi da truyen qua signal GUI duoi dang chuoi."""
    text = str(message or "")
    marker = "[BROWSER_ACTION_REQUIRED:"
    start = text.find(marker)
    if start < 0:
        return None
    start += len(marker)
    end = text.find("]", start)
    return text[start:end] if end > start else None


class FakeWord:
    """Mo phong 1 tu (word) voi timestamp bat dau/ket thuc.
    (Simulated word object with start/end timestamps.)"""
    def __init__(self, word, start, end):
        self.word = word
        self.start = start
        self.end = end


class FakeSegment:
    """Mo phong 1 segment SRT (text + timestamp + danh sach word).
    (Simulated SRT segment with text, timestamps, and word list.)"""
    def __init__(self, text, start, end, words):
        self.text = text
        self.start = start
        self.end = end
        self.words = words
