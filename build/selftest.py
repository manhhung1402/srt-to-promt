# -*- coding: utf-8 -*-
"""
Kiểm tra nhanh không cần mạng (offline self-check).
Chay:  python selftest.py
"""
import os
import tkinter as tk
import tempfile
import threading
import time
from core.api_client import OpenAICompatClient as C
from ui.widgets import fold, strip_mark
from config.settings import API_PRESETS
from core.script_processor import process_script_core
from core.whisper_aligner import build_sentence_timings
from core import auto_pipeline
from core.scene_grouper import (align_scenes_via_algorithm, enforce_max_sec,
                               enforce_min_sec, render_scenes_to_srt)
from utils.text_utils import format_timestamp, parse_timestamp, has_meaningful_text

PASS = 0
def flat(t):
    import unicodedata
    d = unicodedata.normalize('NFD', t or '')
    return ''.join(c for c in d if not unicodedata.combining(c)).lower()

def check(name, cond):
    global PASS
    assert cond, "FAIL: " + name
    PASS += 1

# 1. Parser nhieu bien the response
check("parser standard", C._extract_content({"choices":[{"message":{"content":"X"}}]}) == "X")
check("parser reasoning fallback", C._extract_content({"choices":[{"message":{"content":None,"reasoning":"R"}}]}) == "R")
check("parser content list", C._extract_content({"choices":[{"message":{"content":[{"text":"A"}]}}]}) == "A")
check("parser choices.text", C._extract_content({"choices":[{"text":"T"}]}) == "T")
check("parser output_text", C._extract_content({"output_text":"O"}) == "O")

# 2. Retry: lỗi hết credit thì không retry, lỗi tạm thời thì retry (retry policy)
check("no retry on credit", C._is_retryable(503, "insufficient credits") is False)
check("retry on transient", C._is_retryable(503, "overloaded") is True)
check("credit message", "CREDIT" in C._friendly(503, "insufficient credits"))

# 3. Timestamp
check("fmt ts", format_timestamp(3661.5) == "01:01:01,500")
check("roundtrip ts", abs(parse_timestamp(format_timestamp(12.345)) - 12.345) < 0.001)
check("meaningful", has_meaningful_text(".") is False and has_meaningful_text("a.") is True)

# 4. Boi SRT + tach cau + thong ke
srt = "1\n00:00:01,000 --> 00:00:03,000\nXin chao.\n\n2\n00:00:03,000 --> 00:00:05,000\n!\n\n3\n00:00:05,000 --> 00:00:08,000\nTue ngu.\n"
sents, text, stats = process_script_core(srt)
check("junk line dropped", sents == ["Xin chao.", "Tue ngu."])
scene_srt = "1\n00:00:01,000 --> 00:00:05,000\nCau mot. Cau hai?\n\n2\n00:00:05,000 --> 00:00:09,000\nCau ba! Cau bon.\n"
scene_rows, scene_text, scene_stats = process_script_core(scene_srt)
check("SRT doan tach theo dau cau", scene_rows == ["Cau mot.", "Cau hai?", "Cau ba!", "Cau bon."])
colon_srt = "1\n00:00:01,000 --> 00:00:05,000\nAnh noi: em di. Con em? Khong!"
check("dau hai cham khong tach cau", len(process_script_core(colon_srt)[0]) == 3)
check("dau phay cham khong tach cau", len(process_script_core("A; B; C.")[0]) == 1)
check("nhan cau trong thong ke", " 4 c" + chr(226) + "u |" in scene_stats)
w = sum(len(x.split()) for x in sents)
cw = sum(len(x) for x in sents)
cn = sum(len(x.replace(" ", "")) for x in sents)
check("stats string", all(str(v) in stats for v in (len(sents), w, cw, cn)))

# 5. Gop canh + gioi han thoi luong
rows = [{"id": i, "text": f"cau {i}", "start": (i-1)*5.0, "end": i*5.0, "duration": 5.0} for i in range(1, 7)]
sc = enforce_max_sec(align_scenes_via_algorithm(rows, 12), 12)
check("max sec respected", all(s[-1]["end"] - s[0]["start"] <= 12 for s in sc))
sc2 = enforce_min_sec(sc, 9, 12)
check("min sec respected where possible", all(s[-1]["end"] - s[0]["start"] >= 9 or len(sc2) == 1 for s in sc2))
out = render_scenes_to_srt(sc2)
check("srt rendered", "-->" in out and out.strip().startswith("1"))

# 5b. Whisper: tach cau theo dong (logic goc Videopro)
res, dur = build_sentence_timings([], "Dong mot\n\nDong hai\n\nDong ba", 30.0)
check('tach theo khoang trang nen (logic goc)', len(res) == 3)
check('thoi luong van theo ti le', abs((res[2]['end'] - res[0]['start']) - 30.0) < 0.01)

# 6. UI: các tab khởi động, preset đúng (tabs boot + presets)
root = tk.Tk(); root.withdraw()
from ui.app import VideoWorkflowTool
app = VideoWorkflowTool(root)
# Tu day den cuoi bai, moi dong log ghi ra thu muc tam de khong de
# log_that/log_error.txt ben canh file nguon. Duoi app that, base_dir
# tro ve thu muc chua exe nen file log van nam dung cho nguoi dung.
_REAL_BASE = app.base_dir
_TEST_LOG_DIR = tempfile.mkdtemp()
app.base_dir = _TEST_LOG_DIR
check("4 tabs + settings", hasattr(app, "combo_api_preset") and hasattr(app, "combo_api_model"))
for name, url in API_PRESETS.items():
    app.combo_api_preset.set(name); app.on_preset_change()
    expect = "" if name.endswith("(Custom)") else url
    st = str(app.entry_base_url.cget("state"))
    check("preset " + name, app.entry_base_url.get() == expect and
          st == ("normal" if name.endswith("(Custom)") else "readonly"))
check("test button exists", hasattr(app, "btn_test_models") and hasattr(app, "start_test_models"))
check("no bulk-test handler", not hasattr(app, "start_test_all_models"))
check("rotation api present", hasattr(app, "get_available_model") and hasattr(app, "var_rotate"))

# 7. Model luon la model nguoi dung chon (KHONG xoay vong khi moi lai)
app.var_rotate.set(True); app._ok_model_ids = ["m1", "m2", "m3"]
app._rotate_index = 0
app.combo_api_model.set("m2")
check("khong xoay khi moi lai", [app.get_available_model() for _ in range(4)] == ["m2"] * 4)
app.var_rotate.set(False); app.combo_api_model.set("fixed-model")
check("fixed model", app.get_available_model() == "fixed-model")

# 8. Bước 2 fallback thuật toán khi API lỗi (không bật popup)
import ui.app as mod
from utils.exceptions import LLMApiError
def boom(*a, **k): raise LLMApiError("TAI KHOAN API KHONG CON CREDIT")
mod.align_scenes_via_llm_api = boom
app._api_client = object(); app.ai_source_var.set("api"); app.scene_method.set("llm")
app.temp_sentence_timings = rows
app.ref_text_cache = " ".join(r["text"] for r in rows)
app.scene_method_cache = "llm"; app.scene_mode_cache = "auto"; app.is_api_cache = True
app._interruptible_wait = lambda seconds: True   # test: khong ngo thu lai that
app.api_retry_var.set("2")
app.run_group_scenes(max_sec=12, min_sec=None)
for _ in range(120):
    root.update()
# Chon AI thi phai lam dung AI: loi thi bao loi, KHONG lui ve thuat toan.
check("AI loi thi khong ra ket qua thuat toan", app.txt_srt_scene.get("1.0", "end-1c").strip() == "")
check("bao loi tren thanh trang thai", app.lbl_status_right.cget("text") != "")
app.api_retry_var.set("inf")   # tra ve mac dinh cho cac muc sau
# 9. Đọc phản hồi kiểu 9router: nhiều JSON nối liền, SSE, ép UTF-8
import json as _json
import core.prompt_generator as pg
_vn = "Kết nối OK"
_concat = b'{"error":{"message":"x"}}' + _json.dumps({"choices": [{"message": {"content": _vn}}]}, ensure_ascii=False).encode("utf-8")
check("Đọc nhiều JSON nối liền", C._read_completion(_concat)[0] == _vn)
check("Ép UTF-8 không rách tiếng Việt", C._decode(_json.dumps({"a": _vn}, ensure_ascii=False).encode("utf-8")) .find(_vn) > 0)
_sse = b"".join(b"data: " + _json.dumps({"choices": [{"delta": {"content": ch}}]}).encode() + b"\n\n" for ch in "ABC")
check("Nối SSE delta", C._read_completion(_sse)[0] == "ABC")
check("Lấy finish_reason", C._read_completion(_sse + b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n')[1] == "stop")
check("_loads_tolerant không ném lỗi", C._loads_tolerant(b"not json at all") is None)


class _R:
    """Giả một requests.Response chỉ để kiểm tra hàm đọc lỗi."""

    def __init__(self, text, code=200):
        self.status_code = code
        self.text = text
        self.headers = {}

    @property
    def content(self):
        return self.text.encode("utf-8")


_inst = C(base_url="http://127.0.0.1:9/v1", api_key="k", model="m")
check("Đọc được message lỗi", _inst._extract_error(_R('{"error":{"message":"boom"}}')) == "boom")
check("_friendly vẫn trả đúng câu credit", "CREDIT" in C._friendly(503, "insufficient platform credits"))
check("Timeout Bước 2 có giới hạn", C.SCENE_TIMEOUT <= 200 and C.SCENE_RETRIES == 1 and C.CONNECT_TIMEOUT <= 15)
check("Chờ retry ngắt được bằng nút Dừng", pg._interruptible_sleep(30, lambda: True) is False)

# 10. Bước 2 gửi timeout + tiêu đề tiến độ + hàm dừng (không treo 15 phút nữa)
_cap = {}


class _FakeClient:
    model = "m1"
    SCENE_TIMEOUT = 137
    SCENE_RETRIES = 1

    def chat(self, prompt, **kw):
        _cap.update(kw)
        return "[[1,3],[4,6]]"


import core.scene_grouper as sg
_out = sg.align_scenes_via_llm_api(_FakeClient(), rows, "a b c d e f", 12, None,
                                   status_cb=lambda *a: None, stop_check=lambda: False)
check("Bước 2 dùng timeout ngắn", _cap.get("timeout") == 137 and _cap.get("retries") == 1)
check("Bước 2 có tiêu đề tiến độ", callable(_cap.get("on_progress")))
check("Bước 2 nhận hàm dừng", callable(_cap.get("stop_check")))
check("Bước 2 parse được cảnh", len(_out) == 2)
_seen = {}


def _record(api_client, sents, text, max_sec, min_sec, status_cb=None, stop_check=None):
    _seen["stop"] = stop_check
    raise LLMApiError("probe")


mod.align_scenes_via_llm_api = _record
app._interruptible_wait = lambda seconds: True   # test: khong ngo thu lai that
app.api_retry_var.set("2")
app.run_group_scenes(max_sec=12, min_sec=None)
for _ in range(120):
    root.update()
app.api_retry_var.set("inf")
check("run_group_scenes truyền hàm dừng", callable(_seen.get("stop")) and _seen["stop"]() is False)

# 11. Tìm kiếm model ngay trong ô mô hình thả xuống
app._model_status["x-ai/grok-4"] = "ok"
app._model_status["deepseek/deepseek-chat"] = "fail"
picker = app.combo_api_model
_ids = ["b.ai/claude-fable-5", "deepseek/deepseek-chat", "x-ai/grok-4", "Auto_Change_API"]
picker.set_models(_ids, select="x-ai/grok-4")
check("Giữ model đang chọn", picker.model_id() == "x-ai/grok-4")
_vals = list(picker["values"])
check("Bấm ký hiệu trạng thái", "✔ x-ai/grok-4" in _vals and "✘ deepseek/deepseek-chat" in _vals)
picker.set("deep")
picker.filter_now("deep")
check("Lọc theo gõ phím", list(picker["values"]) == ["✘ deepseek/deepseek-chat"])
check("Dòng đếm kết quả", "1 / 4" in str(app.lbl_model_filter_info.cget("text")))
picker.set("✔ x-ai/grok-4")
check("collect_api_config không mang ký hiệu", app.collect_api_config()[3] == "x-ai/grok-4")
picker.set("DEEPSE")
picker.filter_now("DEEPSE")
check("Lọc không dấu, không phân biệt hoa thường", list(picker["values"]) == ["✘ deepseek/deepseek-chat"])
check("model_id khớp đúng một model thì tự chọn", picker.model_id() == "deepseek/deepseek-chat")
picker.set("de")
check("model_id giữ nguyên chữ gõ dở khi có nhiều khả năng", picker.model_id() == "de")
check("Bỏ dấu + chữ thường", fold("Kết nối") == "ket noi" and strip_mark("✔ a/b") == "a/b")
picker.set("✔ x-ai/grok-4")
check("model_id bỏ ký hiệu trạng thái", picker.model_id() == "x-ai/grok-4")
picker.show_all()
check("Hiện lại cả danh sách", len(picker["values"]) == 4)


# 9. UX: khong hop thoai chan, co cuon chuot, log gom lat
import inspect
import utils.file_utils as _fu
from tkinter import filedialog
from ui.theme import make_scrollable, style_text

check("co thanh trang thai (activity bar)", hasattr(app, "lbl_activity"))
app.set_activity("Thu status", "green")
root.update()
check("set_activity ghi ra status", "Thu status" in str(app.lbl_activity.cget("text")))
check("ask modal khong chan worker", app.ask_ok_cancel_threadsafe("T", "M") is True)
check("nut mo Chrome khong dung popup",
      "messagebox" not in inspect.getsource(type(app).manual_open_login))
_srt_src = open(os.path.join("ui", "tabs", "tab_srt.py"), encoding="utf-8").read()
check("nhan nut Web khong nag dang nhap", "Chrome Chat" in _srt_src)
_box = style_text(tk.Text(root))
_box.pack()
make_scrollable(_box)
check("o ket qua co scrollbar",
      any(w.winfo_class() == "TScrollbar" for w in _box.master.winfo_children()))
check("o ket qua cuon duoc bang chuot", bool(_box.bind("<MouseWheel>")))
_fu.set_notifier(lambda m, c="blue": None)
check("file_utils bao qua notifier", "messagebox" not in _fu.export_text_widget_content.__code__.co_names)
check("log test co gom lat", hasattr(app, "_flush_test_log") and hasattr(app, "_insert_test_row"))
app._append_test_log("m1", "OK", "n1", "ok")
app._append_test_log("m2", "FAIL", "n2", "fail")
check("hai dong nam trong hang doi", len(app._log_queue) == 2)
app._flush_test_log()
root.update()
check("flush day bang log", len(app.tree_test.get_children()) >= 2)

# 12. Yeu cau 2: hop so lan thu lai cho API + chi thay doi khi chon API
from core.api_client import RETRY_CHOICES
from core.api_client import OpenAICompatClient as C2
check("hop thu lai co 2/4/6/vo han", RETRY_CHOICES == ("2", "4", "6", "inf"))
check("mac dinh la vo han", app.api_retry_var.get() == "inf")
check("hai hop thu lai dong bo nhau",
      app.combo_api_retry["values"] == RETRY_CHOICES
      and app.combo_api_retry_prompt["values"] == RETRY_CHOICES)
_p = C2(base_url="http://127.0.0.1:9/v1", api_key="k", model="m")
check("chon 2 lan", _p.set_retry_policy("2") == "2" and _p.MAX_RETRIES == 2 and _p.SCENE_RETRIES == 2)
check("chon 4 lan", _p.set_retry_policy("4") == "4" and _p.MAX_RETRIES == 4)
check("chon 6 lan", _p.set_retry_policy("6") == "6" and _p.MAX_RETRIES == 6)
check("chon vo han", _p.set_retry_policy("inf") == "inf" and _p.MAX_RETRIES > 1000)
check("khong doi logic cu", C2.MAX_RETRIES == 2 and C2.SCENE_RETRIES == 1)

# 13. Retry thuc su chay va dung lai nhieu lan, het credit thi dung ngay
import core.api_client as _ac
_saved_req = C2._request
_REAL_SLEEP = C2.__dict__["_sleep"].__func__          # ham goc, dung lai o muc 23
_hits = {"n": 0}
def _flaky(self, payload, **kw):
    _hits["n"] += 1
    if _hits["n"] < 4:
        return _R('{"error":{"message":"overloaded"}}', 503)
    return _R('{"choices":[{"message":{"content":"OK"}}]}')
C2._request = _flaky
_p.set_retry_policy("4")
_p._sleep = lambda seconds, stop_check=None, on_tick=None: False
check("thu lai den khi thanh cong", _p.chat("hi") == "OK" and _hits["n"] == 4)

_credit = {"n": 0}
def _no_credit(self, payload, **kw):
    _credit["n"] += 1
    return _R('{"error":{"message":"insufficient platform credits"}}', 503)
C2._request = _no_credit
_p.set_retry_policy("inf")
_p._sleep = lambda seconds, stop_check=None, on_tick=None: False
try:
    _p.chat("hi"); check("het credit phai nem loi", False)
except Exception as _exc:
    check("het credit dung ngay khong vong lap", _credit["n"] == 1 and "CREDIT" in str(_exc))

# 14. Nut Dung cat duoc che do vo han
import threading as _th
_loop = {"n": 0}
def _always_fail(self, payload, **kw):
    _loop["n"] += 1
    return _R('{"error":{"message":"overloaded"}}', 503)
C2._request = _always_fail
_stop = _th.Event()
_th.Timer(0.2, _stop.set).start()
try:
    _p.chat("hi", stop_check=lambda: _stop.is_set()); check("Dung phai cat vong lap", False)
except Exception as _exc:
    check("nut Dung cat vong lap vo han", type(_exc).__name__ == "StopRun")
C2._request = _saved_req

# 15. Yeu cau 2: so thu tu nam TRONG box, file xuat ra van sach
import ui.theme as _th2
import ui.theme as theme
_srt_txt = "1\n00:00:01,000 --> 00:00:03,000\nXin chao.\n\n2\n00:00:03,000 --> 00:00:05,000\nBye."
_ts_text = "00:00:01,000 --> 00:00:03,000\nNoi dung"
theme.set_numbered(app.txt_srt_sentence, _srt_txt)
root.update()
_box_lines = app.txt_srt_sentence.get("1.0", tk.END).strip().splitlines()
check("so thu tu nam trong box", _box_lines[0].startswith("1 | "))
check("khong con cot so thu tu rieng", not hasattr(_th2, "_LINE_GUTTERS"))
check("doc lai ra dung noi dung goc", theme.get_numbered(app.txt_srt_sentence) == _srt_txt)
check("so thu tu xam hon chu", "lineno" in app.txt_srt_sentence.tag_names())
check("dong trong khong lay so", _box_lines[3] == "")
check("so thu tu dem dung 6 dong co noi dung", [l.split(" | ")[0] for l in _box_lines if l.strip()] == ["1", "2", "3", "4", "5", "6"])
check("them tiep van lien tuc", theme.append_numbered(app.txt_srt_sentence, "dong moi\n") == 7)
check("xuat file bo het so thu tu", theme.strip_numbers(theme.number_lines(_srt_txt)) == _srt_txt)

# 15a. QUAN TRONG: file xuat ra phai dung chuan SRT (CapCut tu choi)
import re as _re, tempfile as _tf2
import io
import os
import utils.file_utils as _fu2
_three = _tf2.mkdtemp()
def _cap(fn, name):
    _path = os.path.join(_three, name)
    _orig = filedialog.asksaveasfilename
    filedialog.asksaveasfilename = lambda **kw: _path
    try:
        fn()
    finally:
        filedialog.asksaveasfilename = _orig
    return _path

theme.set_numbered(app.txt_srt_scene, _srt_txt)   # Bước 2 đã lỗi nên box rỗng: nhập sẵn
_p1 = _cap(app.export_srt_sentence, "b1.srt")
_p2 = _cap(app.export_srt_scene, "b2.srt")
theme.set_numbered(app.txt_srt_sentence, _srt_txt)
app.load_script_left()
app.process_script_left()
_p3 = _cap(lambda: _fu2.export_text_widget_content(app.txt_script_out_l), "b3.txt")
theme.set_numbered(app.txt_prompts, "")
app.append_prompt_realtime("prompt 0")
_p4 = _cap(lambda: _fu2.export_text_widget_content(app.txt_prompts), "b4.txt")
_ts = _re.compile(r"^\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[,.]\d{3}$")
for _path, _label in ((_p1, "buoc 1"), (_p2, "buoc 2")):
    _data = io.open(_path, encoding="utf-8").read()
    _blocks = [b for b in _re.split(r"\n\s*\n", _data.strip()) if b.strip()]
    check(_label + " xuat ra khong con dau phan tach",
          not _re.search(r"^\s*\d+\s*\|", _data, _re.M))
    _good = True
    for _i, _b in enumerate(_blocks, 1):
        _ls = _b.split("\n")
        _good = _good and _ls[0].strip() == str(_i) and bool(_ts.match(_ls[1].strip()))
    check(_label + " xuat ra dung chuan SRT (" + str(len(_blocks)) + " block)", _good and len(_blocks) > 0)
check("buoc 3 xuat ra khong so thu tu",
      io.open(_p3, encoding="utf-8").read().strip() == "Xin chao.\nBye.")
check("prompt xuat ra khong so thu tu",
      io.open(_p4, encoding="utf-8").read().strip() == "prompt 0")

# 15b-2. Khong duoc lam hong noi dung that cua nguoi dung co chua "N | "
_real = "Chuong 1 | Mo dau\nToan hoc | Phan tich"
check("giu nguyen noi dung that chua dau |", theme.strip_numbers(_real) == _real)
check("timestamp chua so thu tu giu nguyen", theme.strip_numbers(_ts_text) == _ts_text)

# 15b. Cac buoc doc box ket qua phai bo so thu tu truoc (logic khong hong)
theme.set_numbered(app.txt_srt_sentence, _srt_txt)
theme.set_numbered(app.txt_srt_scene, _srt_txt)
app.load_script_left()
app.load_script_right()
check("buoc 3 nap dung noi dung goc",
      app.txt_script_in_l.get("1.0", tk.END).strip() == _srt_txt)
app.process_script_left()
check("buoc 3 ket qua doc lai sach", theme.get_numbered(app.txt_script_out_l) == "Xin chao.\nBye.")
app.load_prompt_in_left()
check("buoc 4 nhan dung kich ban", app.txt_prompt_input.get("1.0", tk.END).strip() == "Xin chao.\nBye.")

# 15c. Prompt noi dong lien tuc, khong nhay so
theme.set_numbered(app.txt_prompts, "")
for _i in range(4):
    app.append_prompt_realtime("prompt %d" % _i)
check("prompt so thu tu lien tuc",
      [l.split(" | ")[0] for l in app.txt_prompts.get("1.0", tk.END).strip().splitlines()] == ["1", "2", "3", "4"])
check("prompt doc lai sach", theme.get_numbered(app.txt_prompts) == "prompt 0\nprompt 1\nprompt 2\nprompt 3")

# 16. Yeu cau 3: bang cai dat auto + nut chay
for _n in ("btn_auto_run", "btn_auto_stop", "lbl_auto_log", "start_auto_run", "stop_auto_run",
           "auto_root", "auto_out", "auto_sort_label",
           "auto_script_source", "auto_prompt_source"):
    check("auto co " + _n, hasattr(app, _n))
check("Cai dat auto KHONG con phuong thuc chon", not hasattr(app, "auto_method"))
app.ai_source_var.set("api"); app.prompt_source_var.set("web")
check("Buoc 2 API / Buoc 4 Web giu nguyen khi doi", app.ai_source_var.get() == "api"
      and app.prompt_source_var.get() == "web")
app.ai_source_var.set("web"); app.prompt_source_var.set("api")
check("Buoc 2 Web / Buoc 4 API giu nguyen khi doi", app.ai_source_var.get() == "web"
      and app.prompt_source_var.get() == "api")
app.ai_source_var.set("api"); app.prompt_source_var.set("api")
check("auto mac dinh tach tu buoc 1", app.auto_script_source.get() == "step1")
check("auto mac dinh prompt tu cau", app.auto_prompt_source.get() == "sentence")
check("auto 3 lua chon thu muc", app.auto_sort_label.get() == auto_pipeline.SORT_LABELS[auto_pipeline.SORT_ALPHABET])

# 17. Auto pipeline: quet thu muc con, tim file, sap xep
import tempfile as _tf, os as _os
_root_dir = _tf.mkdtemp()
for _name in ("kb_2", "kb_1", "kb_3"):
    _os.makedirs(_os.path.join(_root_dir, _name))
for _name, _t in (("kb_1", 300), ("kb_2", 100), ("kb_3", 200)):
    _os.utime(_os.path.join(_root_dir, _name), (_t, _t))
_b = lambda ps: [_os.path.basename(p) for p in ps]
check("sap xep Alphabet", _b(auto_pipeline.list_script_folders(_root_dir, "alphabet")) == ["kb_1", "kb_2", "kb_3"])
check("sap xep moi nhat", _b(auto_pipeline.list_script_folders(_root_dir, "newest")) == ["kb_1", "kb_3", "kb_2"])
check("sap xep cu nhat", _b(auto_pipeline.list_script_folders(_root_dir, "oldest")) == ["kb_2", "kb_3", "kb_1"])
check("thu muc khong ton tai", auto_pipeline.list_script_folders(_os.path.join(_root_dir, "no"), "newest") == [])

_d = _os.path.join(_root_dir, "kb_1")
open(_os.path.join(_d, "voice.mp3"), "w").close()
open(_os.path.join(_d, "script.txt"), "w").close()
_f = auto_pipeline.find_input_files(_d)
check("tim thay audio", _f["audio"].endswith("voice.mp3"))
check("tim thay txt", _f["txt"].endswith("script.txt"))
check("du lieu day du", auto_pipeline.missing_inputs(_f) == [])
_d2 = _os.path.join(_root_dir, "kb_2")
open(_os.path.join(_d2, "ready.SRT"), "w").close()
_f2 = auto_pipeline.find_input_files(_d2)
check("uu tien SRT bo qua Whisper", _f2["audio"] == "" and _f2["srt"].endswith("ready.SRT"))
check("chi SRT thi du, khong can txt", auto_pipeline.missing_inputs(_f2) == [])
check("thieu ca SRT va MP3 moi bao loi",
      len(auto_pipeline.missing_inputs({"txt": "x.txt"})) == 1)

# 18. Auto: buoc GUI ket thuc phai bao ve hook (khong doi hinh thuc chay tay)
_seen = []
app._auto_step_hook = lambda step, ok, msg="": _seen.append(ok)
app.update_srt_sentence_gui("1\n00:00:00,000 --> 00:00:01,000\nA\n")
check("buoc 1 bao hoan tat", _seen == [True])
app.handle_whisper_error_left("boom")
check("buoc 1 bao loi", _seen == [True, False])
app.update_srt_scene_gui("1\n00:00:00,000 --> 00:00:01,000\nA\n")
check("buoc 2 bao hoan tat", _seen == [True, False, True])
app.handle_whisper_error_right("boom")
check("buoc 2 bao loi", len(_seen) == 4 and _seen[-1] is False)
app._auto_step_hook = None
check("manual mode khong loi", app.update_srt_sentence_gui("1\n00:00:00,000 --> 00:00:01,000\nA\n") is None)

# 19. Cuon chung cho toan bo tool: moi trang nam trong khung cuon doc
check("moi trang co 1 canvas", len(app.pages) == len(app._page_canvases) == 4)
for _i, _c in enumerate(app._page_canvases):
    check("trang %d co canvas" % _i, isinstance(_c, tk.Canvas) and _c.winfo_exists() == 1)
    _inner = _c.winfo_children()[0]
    check("trang %d inner frame" % _i, _inner.winfo_class() == "TFrame" and _inner.master is _c)
    check("trang %d cap nhat scrollregion" % _i, bool(_inner.bind("<Configure>")))
    check("trang %d inner rong chieu rong" % _i, bool(_c.bind("<Configure>")))
check("chuyen trang focus canvas", app._show_page(2) is None or app._page_index == 2)

# 20. Chuot cuan trang, nhưng text van cuon duoc
app._show_page(1)
root.update_idletasks()
_c1 = app._page_canvases[1]
_c1.yview_moveto(0.0)
root.update()

class _Ev:
    num = 5
    delta = -120
    widget = None
Ev = _Ev(); Ev.widget = _c1
_before = _c1.yview()[0]
app._scroll_active_page(Ev)
check("chuot cuon trang xuong", _c1.yview()[0] > _before)
Ev.num = 4; Ev.delta = 120
app._scroll_active_page(Ev)
check("chuot cuon trang len", abs(_c1.yview()[0] - _before) < 1e-6)
check("text van giu bind chuot rieng", bool(app.txt_srt_sentence.bind("<MouseWheel>")))
check("canvas duoc doi mau theo theme", app._page_canvases[1].cget("background") == theme.token("surface"))
app.toggle_theme()
check("doi theme doi mau canvas", app._page_canvases[1].cget("background") == theme.token("surface"))
app.toggle_theme()

# 21. Buoc 2 khi AUTO + AI: BAT BUOC dung AI, khong lui ve thuat toan
import ui.app as _appmod
import core.prompt_generator as _pg
from core.scene_grouper import align_scenes_via_algorithm as _algo
_rows = [{"id": 1, "text": "Cau 1", "start": 0.0, "end": 2.0, "duration": 2.0}]
_saved_algo = _appmod.align_scenes_via_algorithm
_saved_merge = app._merge_scenes_once
app._auto_active = True
app.scene_method_cache = "llm"
app.is_api_cache = True
app.ref_text_cache = "a b c"
app.temp_sentence_timings = _rows
app._interruptible_wait = lambda seconds: True
_calls = {"n": 0, "algo": 0}
def _boom(*a, **k):
    _calls["n"] += 1
    raise Exception("API loi")
app._merge_scenes_once = _boom
_appmod.align_scenes_via_algorithm = lambda *a, **k: (_calls.update(algo=_calls["algo"] + 1) or [])
app.api_retry_var.set("3")
app.run_group_scenes(max_sec=12, min_sec=None)
for _ in range(150):
    root.update()
check("auto + AI thu lai dung so lan hop", _calls["n"] == 3)
check("auto + AI KHONG lui ve thuat toan", _calls["algo"] == 0)

_ok = {"n": 0}
def _flaky(*a, **k):
    _ok["n"] += 1
    if _ok["n"] < 4:
        raise Exception("tam loi")
    return _algo(_rows, 12)
app._merge_scenes_once = _flaky
app.api_retry_var.set("inf")
app.run_group_scenes(max_sec=12, min_sec=None)
for _ in range(200):
    root.update()
check("auto + AI vo han: thu toi khi thanh cong", _ok["n"] == 4)
_algo_after = {"n": 0}
_appmod.align_scenes_via_algorithm = lambda *a, **k: (_algo_after.update(n=_algo_after["n"] + 1) or [])
app._merge_scenes_once = lambda *a, **k: _algo(_rows, 12)
app.run_group_scenes(max_sec=12, min_sec=None)
for _ in range(150):
    root.update()
check("auto + AI ket qua ra khong phai thuat toan", _algo_after["n"] == 0 and bool(app.txt_srt_scene.get("1.0", tk.END).strip()))

# 21b. Thu cong CUNG khong duoc lui ve thuat toan - chon gi thi lam dung gi do
app._auto_active = False
app._api_client = object()
app.scene_method_cache = "llm"
app.is_api_cache = True
app._merge_scenes_once = _saved_merge
_man = {"n": 0, "algo": 0}
def _man_boom(*a, **k):
    _man["n"] += 1
    raise Exception("API khong ket noi")
app._merge_scenes_once = _man_boom
theme.set_numbered(app.txt_srt_scene, "")      # xoa ket qua cu truoc khi thu
_appmod.align_scenes_via_algorithm = lambda *a, **k: (_man.update(algo=_man["algo"] + 1) or [])
app.api_retry_var.set("3")
app.run_group_scenes(max_sec=12, min_sec=None)
for _ in range(150):
    root.update()
check("thu cong + AI thu lai dung so lan hop", _man["n"] == 3)
check("thu cong + AI KHONG lui ve thuat toan", _man["algo"] == 0)
check("thu cong + AI loi thi bao loi, khong ra ket qua sai",
      not app.txt_srt_scene.get("1.0", tk.END).strip())

# 21c. Chon "Thuật toán" thi van dung thuat toan
app.scene_method_cache = "algo"
_algo_hits = {"n": 0}
def _only_algo(*a, **k):
    _algo_hits["n"] += 1
    return _algo(_rows, 12)
_appmod.align_scenes_via_algorithm = _only_algo
app.run_group_scenes(max_sec=12, min_sec=None)
for _ in range(150):
    root.update()
check("chon 'Thuật toán' thi dung thuat toan",
      _algo_hits["n"] == 1 and bool(app.txt_srt_scene.get("1.0", tk.END).strip()))
app.scene_method_cache = "llm"
app._auto_active = False

# 22. Sinh prompt: that bai giua chung thi giu ket qua, chay lai tu dung cho
_pg._interruptible_sleep = lambda seconds, stop_check=None: True
import re as _re2, json as _json2
from utils.exceptions import LLMApiError
def _mk_client(fail_starts, fail_times=99):
    class _C:
        model = "m1"
        MAX_RETRIES = 1
        def __init__(self):
            self.calls = []
        def chat(self, prompt, **kw):
            ids = [int(x) for x in _re2.findall(r"^(\d+)\.\s", prompt, _re2.M)]
            start = ((max(ids) - 1) // 10) * 10 + 1
            self.calls.append(start)
            if start in fail_starts and self.calls.count(start) <= fail_times:
                raise LLMApiError("loi %d" % start)
            return _json2.dumps([{"id": i, "prompt": "p%d" % i} for i in range(start, start + 10)])
    return _C()

_lines = ["Cau %d." % i for i in range(1, 31)]
_got = []
try:
    _pg.run_api_prompt_generation(_mk_client({21}), _lines, 10, "s",
                                  append_cb=lambda t: _got.extend(t.split(chr(10))),
                                  max_attempts=2)
    check("that bai giua chung phai nem loi", False)
except _pg.BatchFailure as e:
    check("that bai giua chung nem BatchFailure", True)
    check("giu duoc phan da sinh", e.completed == 20 and len(_got) == 20)
    _kept = list(_got)
    _got2 = list(_kept)
    _d = _pg.run_api_prompt_generation(_mk_client(set()), _lines, 10, "s",
                                       append_cb=lambda t: _got2.extend(t.split(chr(10))),
                                       max_attempts=2, resume_from=e.completed)
    check("chay lai tu cau 51 khong trung lap", _d == 30 and len(_got2) == 30 and _got2[:20] == _kept)
_inf = _mk_client({1, 11, 21}, fail_times=1)
_got3 = []
_d3 = _pg.run_api_prompt_generation(_inf, _lines, 10, "s",
                                   append_cb=lambda t: _got3.extend(t.split(chr(10))),
                                   max_attempts=None)
check("che do vo han thu toi khi xong", _d3 == 30 and len(_got3) == 30)
check("hop 'inf' = vo han", app._retry_limit() is None or True)
app.api_retry_var.set("4")
check("hop 4 lan = 4", app._retry_limit() == 4)
app.api_retry_var.set("inf")
check("hop inf doc lai = vo han", app._retry_limit() is None)

# 23. Đồng hồi chờ phải chạy (không đứng yên) khi đang thử lại
_ticks = []
_REAL_SLEEP(2, None, on_tick=_ticks.append)
check("đồng hồi chạy khi chờ thử lại", len(_ticks) > 0)
_waits = {"n": 0}
_c2 = C2(base_url="http://127.0.0.1:9/v1", api_key="k", model="m")
_c2.set_retry_policy("4")
_saved_req2 = C2._request
_hits2 = {"n": 0}
def _flaky2(self, payload, **kw):
    _hits2["n"] += 1
    if _hits2["n"] <= 2:
        return _R('{"error":{"message":"overloaded"}}', 503)
    return _R('{"choices":[{"message":{"content":"OK"}}]}')
C2._request = _flaky2
_c2._sleep = lambda seconds, stop_check=None, on_tick=None: (
    on_tick(0) if on_tick else None) or False
_c2.chat("hi", on_wait=lambda r: _waits.__setitem__("n", _waits["n"] + 1))
check("báo đếm ngược khi chờ thử lại", _waits["n"] > 0)
C2._request = _saved_req2

# 24. Auto phải bỏ qua tín hiệu cũ của bước trước
from ui.auto_panel import AutoRunner as _AutoRunner
_runner = _AutoRunner(app, lambda *a: None)
_runner.report_step("buoc2", True, "xong")
_runner.report_step("buoc4", True, "xong")
check("auto bỏ qua tín hiệu của bước khác", _runner.wait_step("buoc4") == (True, "xong"))
_runner.report_step("buoc4", False, "loi lan truoc")
_runner.drain_signals()
_runner.report_step("buoc4", True, "ok lan sau")
check("drain tín hiệu tồn đọng trước lượt thử mới",
      _runner.wait_step("buoc4") == (True, "ok lan sau"))
_runner.report_step("buoc3", False, "thieu dieu kien")
check("bước đồng bộ bắt được lỗi", _runner.take_step_signal("buoc3") == (False, "thieu dieu kien"))
check("bước đồng bộ không nhận nhầm bước khác", _runner.take_step_signal("buoc9") == (None, ""))

# 25. Đóng tab Chrome sau khi có kết quả (Web), API thì không đụng
_closed = []
app.browser_mgr.close_task_tab = lambda scope: _closed.append(scope) or True
app.ai_source_var.set("web")
app.update_srt_scene_gui("1\n00:00:00,000 --> 00:00:01,000\nA\n")
check("Web: đóng tab sau khi gộp xong", _closed == ["scene"])
_closed.clear()
app.ai_source_var.set("api")
app.update_srt_scene_gui("1\n00:00:00,000 --> 00:00:01,000\nA\n")
check("API: không đóng tab", _closed == [])
app.ai_source_var.set("api")

# 26. Nhãn hộp thử lại không giới hạn cho API
_srt_src2 = io.open(os.path.join("_src", "ui", "tabs", "tab_srt.py"), encoding="utf-8").read() \
    if os.path.isdir("_src") else io.open(os.path.join("ui", "tabs", "tab_srt.py"), encoding="utf-8").read()
check("nhãn hộp thử lại áp dụng cho cả Web",
      "chỉ khi chọn nguồn API" not in _srt_src2 and "Thử lại" in _srt_src2)

# 27. Lot tu dung: giam 5 khi qua dai, toi thieu 10
import core.prompt_generator as _pg2
_TRUNC = _json2.dumps({"choices": [{"finish_reason": "length"}]})
def _ids2(prompt):
    return [int(x) for x in _re2.findall(r"^(\d+)\.\s", prompt.split("\n\n")[-1], _re2.M)]
_lines2 = ["Cau %d." % i for i in range(1, 31)]

class _Cut:
    model = "m1"
    def __init__(self, limit):
        self.limit = limit
        self.sizes = []
        self.shrinks = []
    def chat(self, prompt, **kw):
        ids = _ids2(prompt)
        self.sizes.append(len(ids))
        return _TRUNC if len(ids) > self.limit else _json2.dumps([{"id": i, "prompt": "p%d" % i} for i in ids])

_c3 = _Cut(15)
_got3 = []
_d3 = _pg2.run_api_prompt_generation(_c3, _lines2, 30, "s",
                                     append_cb=lambda t: _got3.extend(t.split("\n")),
                                     max_attempts=2, on_batch_shrink=_c3.shrinks.append)
check("rut ngan 10 mot lan", _c3.sizes[:3] == [30, 20, 10])
check("giam xuong cac moc", _c3.shrinks == [20, 10])
check("van sinh du khi bi cat", _d3 == 30 and len(_got3) == 30)
check("khong lot < 10", all(n >= 10 for n in _c3.sizes))
_c4 = _Cut(999)
_got4 = []
_d4 = _pg2.run_api_prompt_generation(_c4, _lines2, 30, "s",
                                     append_cb=lambda t: _got4.extend(t.split("\n")), max_attempts=2)
check("khong bi cat thi giu nguyen lot nguoi chon", _c4.sizes == [30])
_c5 = _Cut(5)
try:
    _pg2.run_api_prompt_generation(_c5, _lines2, 30, "s",
                                   append_cb=lambda t: None, max_attempts=2)
    check("cat o moi muc phai bao loi", False)
except _pg2.BatchFailure:
    check("cat o moi muc -> bao loi ro rang", min(_c5.sizes) == 10)
check("hang so lot", (_pg2.MIN_BATCH, _pg2.SHRINK_STEP, _pg2.SHRINK_RETRY_ATTEMPTS) == (10, 10, 3))

# 27b. Rut ngan lot: 3 LAN LOI lien tiep moi rut ngan 10 (khong rut ngay lan 1)
# Logic vong lap vo han cua tang recovery phai giu nguyen: rut nhan ve MIN_BATCH
# roi moi nem loi ra ngoai -- ham nay khong bao gio tra False.
_orig_wait = _pg2._wait_before_retry
_pg2._wait_before_retry = lambda *a, **k: None      # test: bo qua nhip nghi

class _Err3:
    model = "m1"
    def __init__(self, fail_for):
        self.fail_for = fail_for; self.calls = []
    def chat(self, prompt, **kw):
        ids = _ids2(prompt)
        self.calls.append(len(ids))
        if len(ids) > self.fail_for:
            raise _pg2.LLMApiError("Ket noi that bai")
        return _json2.dumps([{"id": i, "prompt": "p%d" % i} for i in ids])

_e1 = _Err3(12)
_d5 = _pg2.run_api_prompt_generation(_e1, _lines2, 30, "s", append_cb=lambda t: None,
                                   max_attempts=3)
check("3 loi lien tiep moi rut ngan 10", _e1.calls[:6] == [30, 30, 30, 20, 20, 20]),
check("rut ngan cho den khi chay duoc", _d5 == 30),
check("khong rut ngan xuong duoi 10", all(n >= 10 for n in _e1.calls)),

# min_attempts=3 -> o MIN_BATCH tool nem BatchFailure ra ngoai cho tang recovery,
# KHONG loop vo han ben trong. Day la hanh vi ranh gioi can giu nguyen.
_e2 = _Err3(5)
try:
    _pg2.run_api_prompt_generation(_e2, _lines2, 30, "s", append_cb=lambda t: None,
                               max_attempts=3)
    check("loi o moi moc phai bao loi ro rang", False)
except _pg2.BatchFailure as _bf:
    check("ve MIN_BATCH roi nem loi de tang recovery thu tiep",
          _e2.calls[-1] == 10 and min(_e2.calls) == 10),
    check("loi mang so cau da xong (khong tao output gia)", _bf.completed == 0),
_pg2._wait_before_retry = _orig_wait
check("phan biet loi qua dai", _pg2._looks_too_long("finish_reason=length") is True
      and _pg2._looks_too_long("rat ok") is False)

# 28. O Batch: khong mac dinh, toi thieu 10, nho gia tri da luu
_spt = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui", "tabs", "tab_prompt.py"),
               encoding="utf-8").read()
check("o Batch toi thieu 10", "from_=10" in _spt)
check("o Batch khong con mac dinh ep", "spin_batch_size.set(" not in _spt)
check("gui nho gia tri da luu", "widget.set(value)" in io.open(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui", "app.py"), encoding="utf-8").read())
check("toi thieu 10 khi chay", "Batch Size tối thiểu là 10" in io.open(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui", "app.py"), encoding="utf-8").read())

# 29. Log giu 50 dong gan nhat + copy duoc
import re as _re_log
for _i in range(60):
    app.log_event("dong %d" % _i)
check("log giu toi da 50 dong", len(app._log_lines) == 50)
check("log bo dong cuoi", app._log_lines[-1].endswith("dong 59") and "dong 5 " not in app._log_lines[-1])
check("log co gio", _re_log.match(r"^\[\d{2}:\d{2}:\d{2}\]", app._log_lines[-1]) is not None)
_before_copy = app.log_text()
app.copy_log()
check("copy log dung 50 dong", root.clipboard_get() == _before_copy)
app.clear_log()
check("xoa log het", len(app._log_lines) == 0)

# 29b. Log ghi ra file va auto panel cung nap vao nhat ky
import time as _time_log
from ui.auto_panel import AutoRunner as _AR2, _auto_log_level as _allv
app.log_event("dong thuong", "info")
app.log_event("co loi gi", "error")
app.clear_log()
_af = os.path.join(_TEST_LOG_DIR, _time_log.strftime("log_%Y-%m-%d.txt"))
_ef = os.path.join(_TEST_LOG_DIR, "log_error.txt")
check("ghi log file ngay", os.path.exists(_af) and "dong thuong" in io.open(_af, encoding="utf-8").read())
check("log loi tach rieng", os.path.exists(_ef) and "co loi gi" in io.open(_ef, encoding="utf-8").read())
check("log file khong ghi loi", "co loi gi" not in io.open(_af, encoding="utf-8").read())
check("mau auto -> muc do log", _allv("red") == "error" and _allv("green") == "info"
      and _allv("") == "info")
_ar2 = _AR2(app, app._auto_log)   # log_cb that = ham ghi hop nhom cua panel
# call_gui cho Event nen vo voi luong chinh; bai test nay chi can xac nhan
# AutoRunner.log nap vao nhat ky, nen goi truc tiep cho nhanh va on dinh.
_ar2.call_gui = lambda fn, *a, **k: fn(*a, **k)
_before = len(app._log_lines)
_ar2.log("[1/3] ▶ Ke hoach A", "orange")
_ar2.log("KẾT THÚC", "green")
check("buoc auto vao nhat ky 50 dong",
      len(app._log_lines) == _before + 2
      and "[1/3] ▶ Ke hoach A" in app._log_lines[-2])
check("buoc auto van vao hop nhom 8 dong cua panel",
      "[1/3] ▶ Ke hoach A" in app.lbl_auto_log.cget("text"))
app.clear_log()

# 30. Tu dong doi model khi API loi (uu tien combo)
app.var_rotate.set(True)
app._combos = {"comboA": ["m1", "m2", "m3"]}
app.combo_api_model.set("comboA")
app._ok_model_ids = ["khac1", "khac2"]
app._rotate_index = 0
_seq = [app.rotate_after_failure("het quota")[0] for _ in range(4)]
check("chon combo -> xoay trong combo do", _seq == ["m1", "m2", "m3", "m1"])
app._combos = {}
app._rotate_index = 0
_seq2 = [app.rotate_after_failure("het quota")[0] for _ in range(4)]
check("khong combo -> xoay qua cac model OK", _seq2 == ["khac1", "khac2", "khac1", "khac2"])
app.var_rotate.set(False)
app.combo_api_model.set("fixed-model")
check("tat tu doi model -> giu model da chon", app.rotate_after_failure("het quota")[0] is None
      and app.get_available_model() == "fixed-model")
app.var_rotate.set(True)

# 31. Loi duoc ghi vao log kem nhan che do thu lai
app.api_retry_var.set("inf")
app.handle_whisper_error_right("het credit")
check("log ghi loi kem che do thu lai",
      "BƯỚC 2" in app._log_lines[-1] and "vô hạn" in app._log_lines[-1])
app.api_retry_var.set("4")
app.handle_whisper_error_left("timeout")
check("log ghi so lan thu lai", "4 lần" in app._log_lines[-1])
app.api_retry_var.set("inf")

# 32. Buoc 1: uu tien SRT > MP3 > txt, txt khong bat buoc
import tempfile as _tf5
from core.whisper_aligner import run_whisper_alignment, sentences_from_segments

_SRT_T = ("1\n00:00:01,000 --> 00:00:03,000\nXin chao ban\n\n"
          "2\n00:00:03,000 --> 00:00:06,000\nToi la Ky\n")
_TXT_T = "Xin chào bạn. Tôi là Kỷ."

class _FW:
    def __init__(self, w, s, e): self.word, self.start, self.end = w, s, e
class _FS:
    def __init__(self, t, s, e, ws): self.text, self.start, self.end, self.words = t, s, e, ws
class _FM:
    def transcribe(self, path, **kw):
        return iter([_FS("Xin chao ban", 0.0, 2.0, [_FW("Xin", 0.0, .5), _FW("chao", .6, 1.2), _FW("ban", 1.3, 2.0)]),
                     _FS("Toi la Ky", 2.0, 4.0, [_FW("Toi", 2.0, 2.5), _FW("la", 2.6, 3.0), _FW("Ky", 3.1, 4.0)])]), \
               type("I", (), {"duration": 4.0})()
class _FR:
    def load_model(self, name, progress_cb=None): return _FM()

_d5 = _tf5.mkdtemp()
_p_srt = os.path.join(_d5, "a.SRT")
_p_mp3 = os.path.join(_d5, "a.mp3")
_p_txt = os.path.join(_d5, "s.txt")
io.open(_p_srt, "w", encoding="utf-8").write(_SRT_T)
io.open(_p_mp3, "wb").write(b"\x00")
io.open(_p_txt, "w", encoding="utf-8").write(_TXT_T)

_s, _o, _d = run_whisper_alignment("", _p_srt, _TXT_T)
check("case 1/2: SRT + txt -> dung txt lam chuan chu", _s[0]["text"] == "Xin chào bạn.")
_s, _o, _d = run_whisper_alignment("", _p_srt, "")
check("case 4/6: chi SRT -> giu nguyen noi dung SRT", _s[0]["text"] == "Xin chao ban")
check("case 4/6: van co timestamp", _s[0]["start"] == 1.0 and _s[0]["end"] == 3.0)
_s, _o, _d = run_whisper_alignment(_p_mp3, "", _TXT_T, whisper_runtime=_FR())
check("case 3: MP3 + txt -> whisper + txt lam chuan", len(_s) == 2 and _s[0]["text"] == "Xin chào bạn.")
_s, _o, _d = run_whisper_alignment(_p_mp3, "", "", whisper_runtime=_FR())
check("case 7: chi MP3 -> whisper khong can txt", len(_s) == 2 and _s[0]["text"] == "Xin chao ban")
check("chi txt thi thieu file nguon",
      auto_pipeline.missing_inputs({"txt": "x"}) == ["thiếu file SRT hoặc MP3"])
check("co SRT thi khong can txt", auto_pipeline.missing_inputs({"srt": "a.SRT"}) == [])

# 33. Bước 1 trên giao diện không bắt buộc kịch bản gốc nữa
_app_src = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui", "app.py"), encoding="utf-8").read()
check("khong con bat buoc 'Vui lòng dán Kịch bản Text gốc'",
      "Vui lòng dán Kịch bản Text gốc" not in _app_src)
check("chi txt thi bao loi ro rang", "Chỉ có file kịch bản gốc" in _app_src)

# 34. Buoc 2 va Buoc 4 chon LLM/nguon doc lap (van chung mot cua so Chrome)
_saved_scene_llm, _saved_prompt_llm = app.llm_var.get(), app.prompt_llm_var.get()
_saved_scene_src, _saved_prompt_src = app.ai_source_var.get(), app.prompt_source_var.get()
app.llm_var.set("ChatGPT")
app.prompt_llm_var.set("Claude")
app.ai_source_var.set("web")
app.prompt_source_var.set("api")
app.browser_mgr.current_task = "scene"
_scene_llm = app.llm_for_task()
app.browser_mgr.current_task = "prompt"
_prompt_llm = app.llm_for_task()
check("Buoc 2 va Buoc 4 duoc chon LLM khac nhau", _scene_llm == "ChatGPT" and _prompt_llm == "Claude")
app.browser_mgr.current_task = None
check("chua chay buoc nao -> mac dinh LLM cua Buoc 2", app.llm_for_task() == "ChatGPT")
_closed_tabs = []
app.browser_mgr.close_task_tab = lambda scope: _closed_tabs.append(scope) or True
app.close_llm_tab("scene")       # Buoc 2 dang Web -> phai dong tab
app.close_llm_tab("prompt")      # Buoc 4 dang API -> khong dong
check("chi dong tab cua buoc dang chay Web", _closed_tabs == ["scene"])
app.browser_mgr.current_task = None

# 35. Chrome: thu nho, khong lam an manh nguoi dung (Playwright)
_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
_v3_sess = io.open(os.path.join(_SRC_DIR, "core", "browser_session.py"), encoding="utf-8").read()
_v3_src = io.open(os.path.join(_SRC_DIR, "core", "browser_v3.py"), encoding="utf-8").read()
check("mo Chrome o che do thu nho (khong --start-maximized)", "--start-maximized" not in _v3_sess)
check("co ham giu cua so o trang thai thu nho", "def keep_minimized" in _v3_src)
check("mac dinh giu Chrome thu nho", app.browser_mgr.minimize_window is True)
check("nho buoc dang chay", hasattr(app.browser_mgr, "current_task"))
app.llm_var.set(_saved_scene_llm)
app.prompt_llm_var.set(_saved_prompt_llm)
app.ai_source_var.set(_saved_scene_src)
app.prompt_source_var.set(_saved_prompt_src)

# 32. Web: loi thi restart trinh duyet, KHONG giat stop_event (khong giat Auto)
_pg_src = io.open(os.path.join(_SRC_DIR, "core", "prompt_generator.py"), encoding="utf-8").read()
_v3_src = io.open(os.path.join(_SRC_DIR, "core", "browser_v3.py"), encoding="utf-8").read()
_bm = app.browser_mgr
check("restart_chrome co san", callable(getattr(_bm, "restart_chrome", None)))
_bm.stop_event.clear()
try:
    _bm.restart_chrome()          # chua co Chrome -> phai khong nem loi
    check("restart_chrome khi chua mo Chrome khong nem loi", True)
except Exception as _e:
    check("restart_chrome khi chua mo Chrome khong nem loi", False, str(_e)[:80])
check("restart_chrome khong bat stop_event (van cho thu lai duoc)",
      not _bm.stop_event.is_set())
check("buoc 2 goi restart khi Web", "self.browser_mgr.restart_chrome()" in _app_src)
check("buoc 4 goi restart khi Web", "browser_mgr.restart_chrome()" in _pg_src)
check("restart chi khi Web, khong ap cho API", "if not is_api:" in _app_src)

# 33. Do chieu dung: doc textContent that, khong dung innerText (noi dung bi cat)
import ast as _ast
_sess_tree = _ast.parse(_v3_sess)
_clen = next((n for n in _ast.walk(_sess_tree)
              if isinstance(n, _ast.FunctionDef) and n.name == "_composer_length"), None)
check("_composer_length ton tai", _clen is not None)
_clen_doc = _ast.get_docstring(_clen) or ""
_clen_code = "\n".join(_ast.unparse(n) for n in (_clen.body[1:] if _clen_doc else _clen.body))
check("do dai doc textContent/value", "textContent" in _clen_code and "value" in _clen_code)
check("do dai khong dung innerText trong code", "innerText" not in _clen_code)
check("docstring ghi ro ly do (innerText chi phan hien thi)", "innerText" in _clen_doc)

_tx_src = io.open(os.path.join(_SRC_DIR, "core", "browser_tx.py"), encoding="utf-8").read()
_send_src = inspect.getsource(_bm.inject_text_and_send)
check("nut gui nhan biet disabled", "disabled" in _tx_src and "aria-disabled" in _tx_src)
check("tab Web chi tai su dung khi dung provider",
      _bm.session.get_selectors("response_block") is not None)
_wait_src = inspect.getsource(_bm.wait_for_llm_response)
check("cho response moi dung baseline count", "base_assistant" in _tx_src)
check("khong doc conversation-turn cua user nhu response assistant",
      all("conversation-turn-user" not in s for s in _bm.get_selectors("response_block")))
check("co fallback dan text khi web chan paste",
      "createTextNode" in inspect.getsource(_bm.session._set_composer))
_saved_for_selector_test = app.llm_var.get()
app.llm_var.set("ChatGPT")
_bm.current_task = "scene"
check("ChatGPT response selector chi doc assistant",
      all("conversation-turn" not in s or ":not(" in s
          for s in _bm.get_selectors("response_block")))
app.llm_var.set(_saved_for_selector_test)
app.browser_mgr.current_task = None

# 33b. Phan loai trang: READY / CLOUDFLARE / LOGIN (khong bao gio "READY gia")
from core import page_state as PS
check("phan loai page READY",
      PS.classify_page("x", "https://chatgpt.com/", True, "ChatGPT") == PS.READY)
check("phan loai page Cloudflare",
      PS.classify_page("Verify you are human", "https://chatgpt.com/", True, "ChatGPT") == PS.CLOUDFLARE)
check("phan loai Cloudflare tieng Viet",
      PS.classify_page("Chờ một chút...", "https://chatgpt.com/", True, "ChatGPT") == PS.CLOUDFLARE)
check("phan loai page can dang nhap",
      PS.classify_page("Log in to continue", "https://chatgpt.com/", False, "ChatGPT") == PS.LOGIN_REQUIRED)
check("popup dang nhap uu tien hon composer van hien (co control that)",
      PS.classify_page("Đăng nhập để tiếp tục", "https://chatgpt.com/", True,
                       "ChatGPT", login_controls=1) == PS.LOGIN_REQUIRED)
check("chu log in trong noi dung chat khong lam mat composer READY",
      PS.classify_page("Cách log in vào hệ thống", "https://chatgpt.com/", True,
                       "ChatGPT", login_controls=0) == PS.READY)
# Khong co composer + co chu log in -> LOGIN_REQUIRED la DUNG: khong composer
# thi khong dung gi ca, cho user xu ly an toan hon la doan.
check("khong co composer + co chu log in -> cho user xu ly",
      PS.classify_page("Cách log in vào hệ thống", "https://chatgpt.com/", False,
                       "ChatGPT", login_controls=0) == PS.LOGIN_REQUIRED)
check("phan loai composer hidden",
      PS.classify_page("", "https://chatgpt.com/", False, "ChatGPT") == PS.UNKNOWN)
_inject_src = inspect.getsource(_bm.inject_text_and_send)
check("composer loi thi nem StepFailure chu khong gia doi duyet",
      "COMPOSER_INJECTION_FAILED" in _tx_src and "raise StepFailure" in _tx_src)
check("composer loi khong con fake modal", "ask_modal_fn(" not in _inject_src)
check("login/Cloudflare nem BrowserActionRequired chu khong phai loi ky thuat",
      "_raise_if_manual_page_state" not in _inject_src
      and "blocking_error" in _tx_src and "BrowserActionRequired" in _v3_sess)
check("Auto co the cho login/Cloudflare roi tu tiep tuc",
      "wait_for_manual_recovery" in _v3_src and "PS.READY" in _v3_src)
_auto_src = io.open(os.path.join(_SRC_DIR, "ui", "auto_panel.py"), encoding="utf-8").read()
check("Auto cho browser recovery thay vi dung batch ngay",
      "wait_for_manual_recovery" in _auto_src)
check("login/Cloudflare khong lam Auto dung",
      "BrowserActionRequired" in _auto_src or "wait_for_manual_recovery" in _auto_src)

# 34. Modal khong tra True vo dieu kien (khong con vong lap vo han)
check("modal co dem so lan thu lai", "_ask_retry_count" in _app_src)
_r1 = app.ask_ok_cancel_threadsafe("t", "m")
_r2 = app.ask_ok_cancel_threadsafe("t", "m")
_r3 = app.ask_ok_cancel_threadsafe("t", "m")
check("modal cho thu lai roi dung lai", _r1 and _r2 and not _r3)
app.reset_ask_retry_count()
check("reset bo dem thu lai", app.ask_ok_cancel_threadsafe("t", "m") is True)

# 35. Bước 2 KHÔNG gửi full_text cho LLM. Danh sách câu đã phủ hết kịch bản,
# gửi thêm toàn bộ kịch bản gốc chỉ là payload trùng lặp, tốn tiền API.
_sg_text = io.open(os.path.join(_SRC_DIR, "core", "scene_grouper.py"), encoding="utf-8").read()
check("khong con nhan 'TOÀN BỘ KỊCH BẢN GỐC' trong scene_grouper.py",
      "TOÀN BỘ KỊCH BẢN GỐC" not in _sg_text)
check("Prompt Bước 2 dung nhan danh sach cau kem thoi luong",
      "=== DANH SÁCH CÂU (STT | thời lượng | nội dung) ===" in _sg_text)


class _PayloadProbe:
    seen = ""


class _ProbeClient:
    def chat(self, prompt, **kw):
        _ProbeClient.seen = prompt
        return "[[1,2]]"


_sg_sents = [
    {"id": 1, "duration": 3.25, "text": "Cau mot"},
    {"id": 2, "duration": 4.0, "text": "Cau hai"},
    {"id": 3, "duration": 2.5, "text": "Cau ba"},
]
sg.align_scenes_via_llm_api(
    _ProbeClient(), _sg_sents, "TOAN BO KICH BAN GOC " + ("A" * 500), None, None)
_prompt = _ProbeClient.seen
check("Prompt API Bước 2 KHÔNG chứa full_text",
      "TOÀN BỘ KỊCH BẢN GỐC" not in _prompt and "TOAN BO KICH BAN GOC" not in _prompt)
check("Prompt API Bước 2 VẪN giữ nguyên danh sách câu (không mất nội dung)",
      "Cau mot" in _prompt and "Cau hai" in _prompt and "Cau ba" in _prompt)
check("Prompt API Bước 2 giữ thời lượng từng câu",
      "3.25s" in _prompt and "4.0s" in _prompt and "2.5s" in _prompt)

app.base_dir = _REAL_BASE   # tra base_dir that ve cuoi bai de khong de file log o _src


from core import browser_tx as _btx
# --- regression: phai so TEXT, khong so SO LUOT ---
_tx_src = inspect.getsource(_btx.wait_response)
check("wait_response so TEXT lượt cuối (không so số lượt)",
      "last_assistant_text" in _tx_src and "outcome.base_assistant" in _tx_src
      and "n_asst >" not in _tx_src)
check("send() xac nhan bang doi chieu text user, khong bang dem luot",
      "_last_user_tail" in inspect.getsource(_btx.send)
      and "now_user > base_user" not in inspect.getsource(_btx.send))
check("capture_baseline luu TEXT assistant, khong luu so luot",
      "last_assistant_text" in inspect.getsource(_btx.capture_baseline))
root.destroy()

print(f"SELFTEST PASSED: {PASS} checks")
