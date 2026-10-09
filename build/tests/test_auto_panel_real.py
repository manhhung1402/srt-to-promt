# -*- mode: python ; coding: utf-8 -*-
"""
Chung minh 3 thieu so cua lop Auto that (ui/auto_panel.py) da duoc sua:

  A1 checkpoint  : buoc da DONE khong chay lai (khong dot tien API)
  A2 DeferFolder : folder hong khong chan cac folder khac
  A3 vong tron   : khong bo folder nao, chi ket thuc khi het hoac nguoi Dung

Chay that voi app gia lap (khong mo Tkinter, khong goi LLM that).
Tien do checkpoint va so vong deu doc tu file that tren dia.
"""
import faulthandler
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

# Treo bao lau 120s roi in ra vi tri chet - khong doan.
# Giup khi test nao treo: biet ngay cho nao, khong phai doan.
faulthandler.dump_traceback_later(120, exit=True)

from core import checkpoint as CK                       # noqa: E402
from core.faults import DeferFolder, StopRun            # noqa: E402
import ui.auto_panel as AP                              # noqa: E402

PASS = [0]
FAIL = []


def check(cond, name, extra=""):
    if cond:
        PASS[0] += 1
        print("  ok   %s" % name)
    else:
        FAIL.append(name)
        print("  FAIL %s %s" % (name, extra))


# --------------------------------------------------------------- app gia lap
class FakeEntry:
    def __init__(self):
        self.value = ""

    def delete(self, *a):
        pass

    def insert(self, idx, v):
        self.value = v


class FakeWidget:
    """Text widget gia lap: get() tra noi dung da duoc 'insert' vao."""

    def __init__(self):
        self.buf = ""
        self.numbered = False

    def tag_configure(self, *a, **k):
        pass

    def tag_add(self, *a, **k):
        pass

    def tag_remove(self, *a, **k):
        pass

    def delete(self, *a):
        self.buf = ""

    def insert(self, idx, v):
        self.buf += v

    def get(self, *a):
        return self.buf


class FakeBrowser:
    def __init__(self):
        self.stop_event = threading.Event()
        self.restarts = 0
        self.recover_calls = 0

    def get_current_llm(self):
        return "ChatGPT"

    def restart_chrome(self):
        self.restarts += 1

    def wait_for_manual_recovery(self, timeout_secs=300):
        self.recover_calls += 1
        return True


class FakeApp:
    """App gia lap kiem du kieu gui ma Auto that goi."""

    def __init__(self):
        self.entry_srt_in_path = FakeEntry()
        self.entry_audio_path = FakeEntry()
        self.txt_reference_text = FakeWidget()
        self.txt_srt_sentence = FakeWidget()
        self.txt_srt_scene = FakeWidget()
        self.txt_script_in_l = FakeWidget()
        self.txt_script_in_r = FakeWidget()
        self.txt_script_out_l = FakeWidget()
        self.txt_script_out_r = FakeWidget()
        self.txt_prompts = FakeWidget()
        self.temp_sentence_timings = []
        self.temp_script_lines = []
        self.browser_mgr = FakeBrowser()
        self._auto_step_hook = None
        self._auto_current_step = None
        self.step_log = []
        self.logs = []

    def log_event(self, text, level="info"):
        self.logs.append(str(text))

    # Buoc 1 / 2 / 4: app that chay luong nen roi bao _notify_auto_step.
    # Test mo phong y het, neu khong wait_step se cho vo han.
    def start_srt_sentence_thread(self):
        self.step_log.append("b1")
        # App that gan temp_sentence_timings (RAM) o day. App gia lap phai
        # gan nhu vay, neu khong thi state_alive luon False va test A6 xanh
        # vi LY DO SAI (fix luon chay) chu khong phai vi fix dung.
        self.temp_sentence_timings = [{"text": "chao", "start": 1.0, "end": 2.0}]
        self.txt_srt_sentence.buf = "1\n00:00:01,000 --> 00:00:02,000\nchao\n"
        self.notify(self._auto_current_step, True, "")

    def update_srt_sentence_gui(self, txt):
        self.txt_srt_sentence.buf = txt

    def start_group_scenes_thread(self):
        self.step_log.append("b2")
        self.txt_srt_scene.buf = "1\n00:00:01,000 --> 00:00:03,000\nchao buoi\n"
        self.notify(self._auto_current_step, True, "")

    def update_srt_scene_gui(self, txt):
        self.txt_srt_scene.buf = txt

    # buoc 3
    def load_script_left(self):
        self.step_log.append("b3-load")
        # App THAT im lang khi thanh cong (chi bao tin hieu khi HONG),
        # nen app gia lap cung phai im lang.

    def process_script_left(self):
        self.step_log.append("b3-run")
        self.txt_script_out_l.buf = "kich ban"

    # buoc 4
    def load_prompt_in_left(self):
        self.step_log.append("b4-load")

    def start_llm_thread(self):
        # Buoc 4 chay LUONG NEN -> bat buoc phai bao tin hieu khi xong
        # (khac voi cac ham sync cua Buoc 3/4-nap, chi bao khi HONG).
        self.step_log.append("b4-run")
        self.txt_prompts.buf = "prompt"
        self.notify(self._auto_current_step, True, "")

    def notify(self, step, ok, message=""):
        hook = self._auto_step_hook
        if hook is not None:
            hook(step, bool(ok), str(message))


def make_folder(root, name, with_inputs=True):
    d = os.path.join(root, name)
    os.makedirs(d, exist_ok=True)
    if with_inputs:
        for fn, body in (("01.srt", "1\n00:00:01,000 --> 00:00:02,000\nhi\n"),
                         ("audio.wav", "RIFF"), ("kichban.txt", "text")):
            open(os.path.join(d, fn), "w", encoding="utf-8").write(body)
    return d


class Harness:
    """AutoRunner that, nhung khong can Tkinter: call_gui chay thang."""

    def __init__(self, app, log=True):
        self.app = app
        # log cua AutoRunner -> app.log_event (de test doc duoc app.logs)
        self.runner = AP.AutoRunner(app, app.log_event)
        # Dong thoi ra stderr de phat hien vong lap. BO COMMENT DONG NAY KHI
        # CHAY BINH THUONG (nhieu du lieu) - chi bat trong luc dieu tra.
        _real_log = self.runner.log

        def both(text, color="blue"):
            _real_log(text, color)
            sys.stderr.write("    LOG %s\n" % text)
        self.runner.log = both
        # Khong co Tkinter trong test: chay lenh giao dien ngay trong luong nay.
        self.runner.call_gui = lambda fn, *a, **k: fn(*a, **k)
        self.runner._interruptible_wait = lambda sec: not self.runner.stop_event.is_set()
        self.runner.recovery.delay_base = 0.01
        self.runner.recovery.delay_max = 0.05
        if not log:
            self.runner.log = lambda *a, **k: None
            self.runner._log_event = lambda *a, **k: None

    def run(self, config):
        return self.runner.run(config)


def run_bounded(harness, config, seconds=40, label=""):
    """Chay harness.run() trong thread voi thoi gian toi da.

    BAT BUOC: tool khong bao gio tu dung, retry vo han la CHU Y. Neu code hong
    thi vong lap vo han -> test treo ma khong bao FAIL. Test phai co duong lui.
    (Run in a thread with a hard timeout so a broken build fails, not hangs.)
    """
    import threading as _th
    t = _th.Thread(target=harness.run, args=(config,), daemon=True)
    t.start()
    t.join(timeout=seconds)
    if t.is_alive():
        harness.runner.request_stop()
        t.join(timeout=10)
        if t.is_alive():
            raise AssertionError(
                "%s: run() khong dung sau %ds - vong lap vo han (code hong)"
                % (label or "run", seconds))
    return True


def base_config(root, out=""):
    return {"root": root, "out": out, "sort": "alphabet",
            "script_source": "step1", "prompt_source": "sentence",
            "retries": None}


# ================================================================= A1
print("\n=== A1: buoc da DONE khong chay lai (checkpoint) ===")
tmp = tempfile.mkdtemp(prefix="ap_a1_")
inp = os.path.join(tmp, "input")
os.makedirs(inp)
f1 = make_folder(inp, "kichban_01")

app = FakeApp()
h = Harness(app)
h.runner._notify = None
run_bounded(h, base_config(inp))

b1 = app.step_log.count("b1")
b2 = app.step_log.count("b2")
b3 = app.step_log.count("b3-run")
b4 = app.step_log.count("b4-run")
check(b1 == 1 and b2 == 1 and b3 == 1 and b4 == 1,
      "lan 1 chay day du 4 buoc", "b1=%d b2=%d b3=%d b4=%d" % (b1, b2, b3, b4))

cp_path = os.path.join(f1, CK.Checkpoint.FILENAME)
check(os.path.exists(cp_path), "checkpoint duoc ghi ra file that")
if os.path.exists(cp_path):
    data = json.load(io.open(cp_path, encoding="utf-8"))
    check(all(data["steps"][str(i)]["status"] == "DONE" for i in (1, 2, 3, 4)),
          "ca 4 buoc DONE trong checkpoint")
    check(data.get("completed") is True, "folder danh dau completed")
    check(CK.Checkpoint(f1).load().completed(), "doc lai van thay completed")

# lan 2: cung folder, phai bo qua het
app2 = FakeApp()
h2 = Harness(app2)
run_bounded(h2, base_config(inp))
check(len(app2.step_log) == 0,
      "lan 2 KHONG chay buoc nao (bo qua vi checkpoint)",
      "step_log=%r" % (app2.step_log,))

# buoc 4 done nhung buoc 1 chua -> phai chay 1, bo 2-4
app3 = FakeApp()
h3 = Harness(app3)
cp3 = CK.Checkpoint(f1).load()
cp3.data["steps"]["1"]["status"] = "PENDING"
cp3.data["completed"] = False
cp3.save()
run_bounded(h3, base_config(inp))
check(app3.step_log.count("b1") == 1, "buoc 1 chua DONE -> chay lai",
      "b1=%d" % app3.step_log.count("b1"))
check(app3.step_log.count("b4-run") == 0, "buoc 4 da DONE -> khong chay lai (tiet kien API)",
      "b4=%d" % app3.step_log.count("b4-run"))

# file output con nguyen -> bo qua
shutil.rmtree(tmp)
print("  (A1 xong)")

# ================================================================= A2
print("\n=== A2: folder hong khong chan folder khac (DeferFolder) ===")
tmp2 = tempfile.mkdtemp(prefix="ap_a2_")
inp2 = os.path.join(tmp2, "input")
os.makedirs(inp2)
bad = make_folder(inp2, "aa_hong")
good1 = make_folder(inp2, "bb_tot")
good2 = make_folder(inp2, "cc_tot")

app4 = FakeApp()
# buoc 2 cua folder 'aa_hong' luon that bai
bad_marker = {"n": 0}
orig_group = app4.start_group_scenes_thread


def group_always_fail():
    bad_marker["n"] += 1
    if os.path.basename(os.path.dirname(app4.entry_srt_in_path.value)).startswith("aa_hong"):
        app4.notify(app4._auto_current_step, False, "loi API 402 het credit")
    else:
        orig_group()
        app4.notify(app4._auto_current_step, True, "")


app4.start_group_scenes_thread = group_always_fail
h4 = Harness(app4)
h4.runner.defer_after = 2

# Tool KHONG bao gio tu dung theo quy dinh -> test phai bam Dung, dung nhu
# nguoi dung se lam. Bam Dung ngay khi 2 folder tot da xong.
stop_at = {"n": 0}


def grp_and_maybe_stop():
    # Bam Dung sau khi 2 folder tot da DEN buoc 4 (completed chi duoc gan
    # o cuoi _run_folder, nen phai dem buoc 4 DONE chu khong phai completed).
    done_now = 0
    for f in (good1, good2):
        c = CK.Checkpoint(f).load()
        if c.is_done(4) or c.completed():
            done_now += 1
    if done_now >= 2:
        h4.runner.request_stop()
        return
    group_always_fail()


app4.start_group_scenes_thread = grp_and_maybe_stop
t0 = time.time()
run_bounded(h4, base_config(inp2))
elapsed = time.time() - t0

# 2 folder tot phai xong
done_folders = [f for f in (good1, good2)
                if CK.Checkpoint(f).load().completed()]
check(len(done_folders) == 2,
      "2 folder tot VAN XONG du folder hong khong chan",
      "done=%d/2" % len(done_folders))
check(bad_marker["n"] >= 2, "folder hong da duoc thu nhieu lan",
      "n=%d" % bad_marker["n"])
check("aa_hong" not in [os.path.basename(d) for d in done_folders],
      "folder hong khong bi bao xong gia")
check(elapsed < 60, "khong treo vo han tren folder hong",
      "elapsed=%.1fs" % elapsed)
shutil.rmtree(tmp2)
print("  (A2 xong)")

# ================================================================= A3
print("\n=== A3: chi dung khi het hoac nguoi dung bam Dung ===")
tmp3 = tempfile.mkdtemp(prefix="ap_a3_")
inp3 = os.path.join(tmp3, "input")
os.makedirs(inp3)
make_folder(inp3, "k1")
make_folder(inp3, "k2")

# A3a: nguoi dung bam Dung ngay khi moi bat Auto -> phai dung ngay.
# (Tool khong bao gio tu dung, nen test phai bam Dung nhu nguoi dung that.)
app5 = FakeApp()
h5 = Harness(app5)
h5.runner.request_stop()          # "vua bam nut Dung"
run_bounded(h5, base_config(inp3))
check(all(CK.Checkpoint(os.path.join(inp3, d)).load().completed() is False
          for d in ("k1", "k2")),
      "bam Dung truoc khi chay -> khong tao output gia")
# Log that co dau tieng Viet ("Đã dừng theo yêu cầu"). So sanh phai giu dau.
check(any("Đã dừng theo yêu cầu" in l for l in app5.logs),
      "co log 'Đã dừng theo yêu cầu'",
      "logs=%r" % (app5.logs[-3:],))

# A3b: Dung giua vong -> khong chay tiep folder sau
app6 = FakeApp()
h6 = Harness(app6)
orig5 = app6.start_srt_sentence_thread
stop_after = {"n": 0}


def b1_then_stop():
    stop_after["n"] += 1
    orig5()
    app6.notify(app6._auto_current_step, True, "")
    if stop_after["n"] == 1:
        h6.runner.request_stop()


app6.start_srt_sentence_thread = b1_then_stop
run_bounded(h6, base_config(inp3))
check(any("dừng theo yêu cầu" in l for l in app6.logs),
      "Dừng giữa vòng được tôn trọng (StopRun là tín hiệu duy nhất)",
      "logs=%r" % (app6.logs[-3:],))
k2_cp = CK.Checkpoint(os.path.join(inp3, "k2")).load()
check(k2_cp.completed() is False, "folder sau khong bi chay khi da Dung")
shutil.rmtree(tmp3)

# A3c: khong folder nao bi bo qua khi folder loi viet dai
tmp4 = tempfile.mkdtemp(prefix="ap_a3c_")
inp4 = os.path.join(tmp4, "input")
os.makedirs(inp4)
mk = make_folder(inp4, "m1")
make_folder(inp4, "m2")
make_folder(inp4, "m3")
app7 = FakeApp()
h7 = Harness(app7)
h7.runner.defer_after = 1
seen = set()
orig_g = app7.start_group_scenes_thread


def grp(name=None):
    # entry_srt_in_path = <folder>/<file>.srt -> can thu muc cha, khong phai ten file.
    stem = os.path.basename(os.path.dirname(app7.entry_srt_in_path.value))
    seen.add(stem)
    if stem.startswith("m1"):
        app7.notify(app7._auto_current_step, False, "loi tam thoi")
    else:
        orig_g()
        app7.notify(app7._auto_current_step, True, "")


app7.start_group_scenes_thread = grp
# Dung sau khi ca 3 folder deu da duoc thu it nhat mot lan.
h7.runner.request_stop = h7.runner.request_stop
_orig_wait = h7.runner._interruptible_wait


def wait_then_stop(sec):
    if len(seen) >= 3:
        h7.runner.stop_event.set()
    return not h7.runner.stop_event.is_set()

h7.runner._interruptible_wait = wait_then_stop
run_bounded(h7, base_config(inp4))
check(len(seen) >= 3, "ca 3 folder deu duoc thu it nhat mot lan",
      "seen=%r" % (sorted(seen),))
shutil.rmtree(tmp4)
print("  (A3 xong)")

# ================================================================= A6 RESTART
print("\n=== A6: chay lai sau khi restart (RAM mat, file con) ===")
# Day la tinh huong that da gap: buoc 1 da xong -> restart tool -> buoc 2 chet
# voi "Hãy hoàn thành Bước 1 trước!". Nguyen nhan: temp_sentence_timings chi
# nam trong RAM, checkpoint chi biet file da ghi.
tmp6 = tempfile.mkdtemp(prefix="ap_a6_")
inp6 = os.path.join(tmp6, "input")
os.makedirs(inp6)
f6 = make_folder(inp6, "restart_case")
appA = FakeApp()
hA = Harness(appA)
run_bounded(hA, base_config(inp6))
b1_first = appA.step_log.count("b1")
check(b1_first == 1, "A6 lan dau chay buoc 1", "b1=%d" % b1_first)
check(CK.Checkpoint(f6).load().is_done(1), "A6 buoc 1 ghi DONE")
del appA

# Mo phong dung tinh huong that: may chet giua Buoc 1 va Buoc 2 -> buoc 1
# DONE, 2-4 chua chay. Day moi la tinh huong "RAM mat".
cp6 = CK.Checkpoint(f6).load()
for st in (2, 3, 4):
    cp6.data["steps"][str(st)]["status"] = CK.STEP_PENDING
    cp6.data["steps"][str(st)]["output"] = ""
cp6.data["completed"] = False
cp6.data["completed_at"] = ""
cp6.save()
for f in ("_02_srt_gop.srt", "_03_kichban_cau.txt", "_04_prompts.txt"):
    pth = os.path.join(f6, "restart_case" + f)
    if os.path.exists(pth):
        os.remove(pth)


# Mo phong restart: app moi, RAM rong, checkpoint con.
appB = FakeApp()
hB = Harness(appB)
run_bounded(hB, base_config(inp6))

check(appB.step_log.count("b1") >= 1,
      "A6 buoc 1 CHAY LAI khi RAM mat (khong bo qua)",
      "b1=%d" % appB.step_log.count("b1"))
check(len(appB.step_log) > b1_first,
      "A6 vong sau co chay tiep (khong chi 1 buoc)",
      "steps=%r" % (appB.step_log,))
cpB = CK.Checkpoint(f6).load()
check(cpB.completed(), "A6 folder hoan thanh sau restart",
      "steps=%r" % {k: v["status"] for k, v in cpB.data["steps"].items()})

# A6b: giu dung trang thai "buoc 1 DONE, 2-4 pending", app moi (RAM rong)
cp6b = CK.Checkpoint(f6).load()
for st in (2, 3, 4):
    cp6b.data["steps"][str(st)]["status"] = CK.STEP_PENDING
    cp6b.data["steps"][str(st)]["output"] = ""
cp6b.data["completed"] = False
cp6b.save()
for f in ("_02_srt_gop.srt", "_03_kichban_cau.txt", "_04_prompts.txt"):
    pth = os.path.join(f6, "restart_case" + f)
    if os.path.exists(pth):
        os.remove(pth)

appC = FakeApp()
hC = Harness(appC)
run_bounded(hC, base_config(inp6))
check(appC.step_log.count("b1") >= 1,
      "A6b app moi: buoc 1 chay lai de nap lai RAM",
      "b1=%d" % appC.step_log.count("b1"))
check(appC.step_log.count("b2") >= 1,
      "A6b buoc 2 chay duoc (khong chet 'Hãy hoàn thành Buoc 1')",
      "b2=%d" % appC.step_log.count("b2"))

# A6d: buoc 1 DONE + RAM CON data -> BO QUA buoc 1 (không làm lại không cần)
cp6d = CK.Checkpoint(f6).load()
cp6d.data["steps"]["1"]["status"] = CK.STEP_DONE
cp6d.data["steps"]["1"]["output"] = "restart_case_01_srt_cau.srt"
cp6d.data["steps"]["2"]["status"] = CK.STEP_PENDING
cp6d.data["steps"]["2"]["output"] = ""
cp6d.data["completed"] = False
cp6d.save()
open(os.path.join(f6, "restart_case_01_srt_cau.srt"), "w", encoding="utf-8").write(
    "1\n00:00:01,000 --> 00:00:02,000\nchao\n")
for fn2 in ("_02_srt_gop.srt", "_03_kichban_cau.txt", "_04_prompts.txt"):
    pth = os.path.join(f6, "restart_case" + fn2)
    if os.path.exists(pth):
        os.remove(pth)
appE = FakeApp()
appE.temp_sentence_timings = [{"text": "x", "start": 0.0, "end": 1.0}]  # RAM con data
hE = Harness(appE)
run_bounded(hE, base_config(inp6))
check(any("dữ liệu còn trong bộ nhớ" in l for l in appE.logs),
      "A6d RAM còn data -> BỎ QUA bước 1 (không làm lại không cần)",
      "logs=%r" % (appE.logs[-5:],))
check(appE.step_log.count("b2") >= 1,
      "A6d bước 2 vẫn chạy được sau khi bỏ bước 1",
      "b2=%d" % appE.step_log.count("b2"))

# A6c: file bi xoa sau khi checkpoint DONE -> phai chay lai
os.remove(os.path.join(f6, "restart_case_01_srt_cau.srt"))
cp6c = CK.Checkpoint(f6).load()
for st in (2, 3, 4):
    cp6c.data["steps"][str(st)]["status"] = CK.STEP_PENDING
    cp6c.data["steps"][str(st)]["output"] = ""
cp6c.data["completed"] = False
cp6c.save()
for f in ("_02_srt_gop.srt", "_03_kichban_cau.txt", "_04_prompts.txt"):
    pth = os.path.join(f6, "restart_case" + f)
    if os.path.exists(pth):
        os.remove(pth)
appD = FakeApp()
hD = Harness(appD)
run_bounded(hD, base_config(inp6))
check(appD.step_log.count("b1") >= 1,
      "A6c buoc 1 DONE nhung file bi xoa -> chay lai",
      "b1=%d" % appD.step_log.count("b1"))
shutil.rmtree(tmp6)
print("  (A6 xong)")

# ================================================================= A4 tu dong dung
print("\n=== A4: khong co duong tu dung do loi ===")
src = io.open(os.path.join(ROOT, "ui", "auto_panel.py"), encoding="utf-8").read()
run_body = src[src.find("    def run(self, config):"):
               src.find("    def run_step(self")]
check("sys.exit" not in run_body and "raise SystemExit" not in run_body,
      "run() khong co sys.exit/SystemExit")
check("except Exception" in run_body and "still_pending.append" in run_body,
      "run() giu folder loi vao vong sau (khong bo)")
check("def request_stop" in src and "StopRun" in src,
      "StopRun van la duong dung duy nhat")
cp_src = io.open(os.path.join(ROOT, "core", "checkpoint.py"), encoding="utf-8").read()
for banned in ("api_key", "apikey", "token", "password", "secret", "cookie"):
    check(banned in cp_src, "checkpoint loai khoa nhay cam '%s'" % banned)
sample = CK.Checkpoint(tempfile.mkdtemp(prefix="cp_"))
sample.mark_done(1, "out.srt")
raw = io.open(sample.path, encoding="utf-8").read()
check("sk-" not in raw and "Bearer" not in raw, "checkpoint khong ro ri secret")

# ================================================================= A5 khong sua pham vi
# =====================================================================
# A7: ham SYNC im lang khi THANH CONG -> buoc phai coi la xong.
# App that chi goi _notify_auto_step khi HONG (qua _auto_step_cancelled).
# Test gia lap truoc day goi notify(True) o moi ham nen code sai van xanh.
# A7 kiem truc tiep: khong tin hieu + ham chay = THANH CONG.
# =====================================================================
print("\n=== A7: ham sync khong can tin hieu khi thanh cong ===")
_ap = io.open(os.path.join(ROOT, "ui", "auto_panel.py"), encoding="utf-8").read()
_i = _ap.find("if sync:")
_blk = _ap[_i:_i + 900]
check("A7 sync khong nem loi khi khong co tin hieu",
      "ok is None" not in _blk, "van co kiem tra ok is None trong nhanh sync")
check("A7 sync van nem loi khi bao tin hieu that bai",
      "if ok is False:" in _blk)

# App gia lap phai im lang: 4 ham sync khong duoc goi notify(True)
_fs = io.open(os.path.abspath(__file__), encoding="utf-8").read()
for _fn in ("load_script_left", "process_script_left", "load_prompt_in_left"):
    _j = _fs.find("def %s(self" % _fn)
    _seg = _fs[_j:_j + 700].split("\n\n    def ")[0]
    check("A7 app gia lap %s im lang (giong app that)" % _fn,
          "notify(self._auto_current_step, True" not in _seg,
          "van goi notify(True) -> se bo loi khi app that im lang")
_j = _fs.find("def start_llm_thread(self)")
_seg = _fs[_j:_j + 700].split("\n\n    def ")[0]
check("A7 start_llm_thread (bat buoc) van bao tin hieu",
      "notify(self._auto_current_step, True" in _seg)

# Chay that: buoc 3 + buoc 4 phai xong khi app KHONG bao tin hieu
# PHẢI có giới hạn thời gian: nếu code hỏng thì retry vô hạn (đúng thiết kế
# "không tự dừng") -> treo chứ không FAIL. Chạy trong thread, đợi 15s rồi
# bấm Dừng như người dùng thật, rồi kiểm tra bước 3 có chạy không.
import threading as _th

_tmp7 = tempfile.mkdtemp(prefix="ap_a7_")
_inp7 = os.path.join(_tmp7, "input")
make_folder(_inp7, "kichban_01")
_a7 = FakeApp()
_a7.temp_sentence_timings = [{"text": "x", "start": 0.0, "end": 1.0}]
_h7 = Harness(_a7, log=False)
_th7 = _th.Thread(target=_h7.run, args=(base_config(_inp7),), daemon=True)
_th7.start()
_th7.join(timeout=15)
if _th7.is_alive():
    _h7.runner.request_stop()
    _th7.join(timeout=8)
check(not _th7.is_alive(),
      "A7 vong chay ket thuc (co gioi han thoi gian)",
      "thread van chay -> co vong lap vo han")
# KHONG kiem "da goi ham" — ham duoc goi TRUOC roi moi nem loi nen
# step_log van ghi du code hong. Phai do KET QUA: checkpoint buoc 3/4 DONE
# (chi danh DONE sau khi file that da ghi) va folder da completed.
_cp7 = CK.Checkpoint(os.path.join(_inp7, "kichban_01"))
_cp7.load()
check(_cp7.is_done(3),
      "A7 buoc 3 THAT SU xong khi app im lang (truoc day chet 'khong tra ket qua')",
      "status3=%r logs=%r" % (_cp7.status(3), _a7.step_log[-4:]))
check(_cp7.is_done(4) and _cp7.completed,
      "A7 buoc 4 xong va folder completed",
      "status4=%r completed=%r" % (_cp7.status(4), _cp7.completed))

print("\n=== A5: pham vi sua doi ===")
for f in ("utils/exceptions.py", "core/faults.py", "ui/auto_panel.py"):
    p = os.path.join(ROOT, f)
    check(os.path.exists(p), "file ton tai: %s" % f)
bak = os.path.join(os.path.dirname(ROOT), os.path.join("backup", "_src_BAK_before_rewrite"), "utils", "exceptions.py")
check(os.path.exists(bak), "backup ton tai de rollback")

# ================================================================= tong
print("\n" + "=" * 62)
print("AUTO PANEL TESTS: %d pass, %d fail" % (PASS[0], len(FAIL)))
for f in FAIL:
    print("  FAIL:", f)
sys.exit(1 if FAIL else 0)
