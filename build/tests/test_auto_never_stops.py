# -*- coding: utf-8 -*-
"""
Test cho lời hứa cốt lõi: Auto KHÔNG BAO GIỜ tự dừng.

Mô phỏng đúng tình huống người dùng ngủ: các bước hỏng đại trà, không ai
bấm Dừng. Tool phải tự vượt qua và hoàn thành.

  A1  mọi bước thất bại N lần rồi thành công -> phải hoàn thành
  A2  lỗi API 402 hết tiền vô hạn -> KHÔNG dừng (retry tiếp)
  A3  login/Cloudflare vô hạn -> KHÔNG dừng, không tốn lượt retry
  A4  nhấn Dừng giữa chừng -> dừng ngay
  A5  một folder hỏng -> folder sau VẪN chạy
  A6  folder thiếu bước 4 -> KHÔNG được đánh dấu hoàn thành
  A7  checkpoint ghi atom, khôi phục đúng sau "khởi động lại"
  A8  bước 4 hỏng -> bước 1-3 KHÔNG chạy lại
  A9  không có đường thoát nào ngoài Dừng (kiểm tra tĩnh mã nguồn)

Lịch backoff được rút ngắn bằng FAST_BASE/FAST_MAX để test chạy nhanh;
sản phẩm dùng lịch thật.

Chạy:  python tests/test_auto_never_stops.py
"""
import os
import re
import shutil
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import checkpoint as CK
from core.auto_runner import AutoRunner
from core.faults import BrowserActionRequired, LLMApiError, StopRun, StepFailure

FAST = dict(delay_base=0.05, delay_max=0.2)
BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tmp_auto")
SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core")

PASS = []
FAIL = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    suffix = f"  <- {detail}" if (detail and not cond) else ""
    print(("  OK   " if cond else "  FAIL ") + name + suffix)


class Harness:
    def __init__(self):
        self.stop = threading.Event()
        self.logs = []

    def log(self, event, **f):
        self.logs.append((event, f))

    def status(self, *a, **k):
        pass

    def progress(self, *a, **k):
        pass

    def log_count(self, event):
        return sum(1 for e, _ in self.logs if e == event)

    @staticmethod
    def make_folders(n):
        root = os.path.join(BASE_DIR, "input")
        shutil.rmtree(root, ignore_errors=True)
        made = []
        for i in range(n):
            path = os.path.join(root, f"kichban_{i:02d}")
            os.makedirs(path, exist_ok=True)
            with open(os.path.join(path, "script.txt"), "w", encoding="utf-8") as fh:
                fh.write(f"kich ban so {i}\n" * 50)
            made.append(path)
        return root, made


def build(h, steps, **kw):
    return AutoRunner(BASE_DIR, steps, stop_event=h.stop, log=h.log,
                      status=h.status, progress=h.progress, **FAST, **kw)


def main():
    shutil.rmtree(BASE_DIR, ignore_errors=True)
    os.makedirs(BASE_DIR, exist_ok=True)

    # ============================================================ A1
    print("\n[A1] buoc hong N lan roi thanh cong -> phai hoan thanh")
    h = Harness()
    root, folders = h.make_folders(1)
    counts = {1: 0, 2: 0, 3: 0, 4: 0}

    def step(n, fail_times, factory):
        def fn(folder, task):
            counts[n] += 1
            if counts[n] <= fail_times:
                raise factory()
            out = os.path.join(folder, f"out_{n}.txt")
            with open(out, "w", encoding="utf-8") as fh:
                fh.write("ok")
            return out
        return fn

    steps = {
        1: step(1, 3, lambda: LLMApiError("mat chet", "network")),
        2: step(2, 5, lambda: LLMApiError("502", "limit")),
        3: step(3, 2, lambda: StepFailure("parser loi", "parser")),
        4: step(4, 4, lambda: StepFailure("khong co response", "no_response")),
    }
    runner = build(h, steps)
    t0 = time.time()
    res = runner.run(root)
    check("A1 hoan thanh du co loi lap lai", res["completed"] == 1, str(res))
    check("A1 du so lan thu", counts == {1: 4, 2: 6, 3: 3, 4: 5}, str(counts))
    check("A1 folder danh dau hoan thanh",
          CK.Checkpoint(folders[0]).load().completed(), "chua complete")
    check("A1 khong ton nhieu thoi gian", time.time() - t0 < 60,
          f"{time.time() - t0:.0f}s")

    # ============================================================ A2
    print("\n[A2] loi 402 het tien VÔ HAN -> khong dung, van retry")
    h = Harness()
    root, folders = h.make_folders(1)
    calls = {"n": 0}

    def forever(folder, task):
        calls["n"] += 1
        raise LLMApiError("402 requires_purchase credits", "credit")

    runner = build(h, {1: forever, 2: forever, 3: forever, 4: forever})
    threading.Thread(target=lambda: (time.sleep(6), h.stop.set()),
                     daemon=True).start()
    res = runner.run(root)
    check("A2 da thu lai nhieu lan", calls["n"] > 5, f"chi {calls['n']} lan")
    check("A2 dung vi nguoi dung bam Dung", h.stop.is_set(), "stop chua set")
    check("A2 bao end_by_user", h.log_count("auto_end_by_user") == 1, "")
    check("A2 khong bao hoan thanh (khong output gia)", res["completed"] == 0, str(res))
    check("A2 bao permanent_looking_error sau nhieu lan",
          h.log_count("permanent_looking_error") >= 1,
          f"chi {h.log_count('permanent_looking_error')} lan")

    # ============================================================ A3
    print("\n[A3] login/Cloudflare VÔ HAN -> khong dung, khong ton retry API")
    h = Harness()
    root, folders = h.make_folders(1)
    human = {"n": 0}
    later = {"n": 0}

    def needs_login(folder, task):
        human["n"] += 1
        raise BrowserActionRequired("ChatGPT: can dang nhap", provider="ChatGPT")

    def later_ok(folder, task):
        later["n"] += 1
        out = os.path.join(folder, "out.txt")
        with open(out, "w", encoding="utf-8") as fh:
            fh.write("ok")
        return out

    runner = build(h, {1: needs_login, 2: needs_login, 3: needs_login, 4: later_ok})
    threading.Thread(target=lambda: (time.sleep(6), h.stop.set()), daemon=True).start()
    runner.run(root)
    check("A3 cho nguoi dung, khong tu bo", human["n"] >= 1, str(human))
    check("A3 ghi log awaiting_human", h.log_count("awaiting_human") >= 1, "")
    check("A3 khong danh dau hoan thanh",
          not CK.Checkpoint(folders[0]).load().completed(), "bi danh dau xong")
    check("A3 buoc 4 KHONG chay khi buoc 2 con cho nguoi", later["n"] == 0,
          f"buoc 4 chay {later['n']} lan")

    # ============================================================ A4
    print("\n[A4] bam Dung giua chung -> dung nhanh")
    h = Harness()
    root, folders = h.make_folders(5)

    def slow(folder, task):
        time.sleep(0.4)
        raise LLMApiError("mat chet", "network")

    runner = build(h, {i: slow for i in (1, 2, 3, 4)})
    threading.Thread(target=lambda: (time.sleep(2), h.stop.set()), daemon=True).start()
    t0 = time.time()
    runner.run(root)
    check("A4 dung trong vai giay", time.time() - t0 < 30, f"{time.time() - t0:.1f}s")
    check("A4 log end_by_user", h.log_count("auto_end_by_user") == 1, "")

    # ============================================================ A5
    print("\n[A5] mot folder hong vinh vien -> folder sau VAN chay (vong tron)")
    h = Harness()
    root, folders = h.make_folders(3)
    visited = []

    def mixed(folder, task):
        name = os.path.basename(folder)
        if name.endswith("00"):                  # folder đầu hỏng vĩnh viễn
            raise LLMApiError("het tien", "credit")
        visited.append(name)
        return os.path.join(folder, "out.txt")

    runner = build(h, {i: mixed for i in (1, 2, 3, 4)})
    # Vòng 1: folder_00 thử 4 lần rồi nhường; folder_01, _02 phải chạy.
    # Dừng ngay sau vòng đầu để test kịp.
    def stop_after_first_good():
        for _ in range(200):
            time.sleep(0.05)
            if len(visited) >= 2:
                h.stop.set()
                return

    threading.Thread(target=stop_after_first_good, daemon=True).start()
    res = runner.run(root)
    check("A5 folder hong khong chan folder sau", len(visited) >= 2, str(visited))
    check("A5 da co folder xong", res["completed"] >= 2, str(res))
    check("A5 ghi log auto_defer_step", h.log_count("auto_defer_step") >= 1,
          f"{h.log_count('auto_defer_step')} lan")
    check("A5 folder hong van con trong hang doi (khong bi bo)",
          not CK.Checkpoint(folders[0]).load().completed(), "bị coi là xong")

    # ============================================================ A6
    print("\n[A6] thieu buoc 4 -> KHONG danh dau hoan thanh")
    h = Harness()
    root, folders = h.make_folders(1)

    def ok_step(n):
        def fn(folder, task):
            out = os.path.join(folder, f"out_{n}.txt")
            with open(out, "w", encoding="utf-8") as fh:
                fh.write("ok")
            return out
        return fn

    runner = build(h, {1: ok_step(1), 2: ok_step(2), 3: ok_step(3)})
    runner.run(root)
    cp = CK.Checkpoint(folders[0]).load()
    check("A6 buoc 4 = PENDING", cp.status(4) == CK.STEP_PENDING, cp.status(4))
    check("A6 completed = False", cp.completed() is False, str(cp.data["completed"]))
    check("A6 next_pending_step = 4", cp.next_pending_step() == 4,
          str(cp.next_pending_step()))

    # ============================================================ A7
    print("\n[A7] checkpoint ben vung: khoi phuc dung sau khoi dong lai")
    cp = CK.Checkpoint(folders[0])
    cp.mark_running(1)
    cp.mark_done(1, "a.srt")
    cp.mark_running(2)
    cp.mark_failed(2, LLMApiError("502", "limit"))
    cp.save()
    again = CK.Checkpoint(folders[0]).load()
    check("A7 buoc 1 giu DONE", again.is_done(1), again.status(1))
    check("A7 buoc 2 quay lai PENDING", again.status(2) == CK.STEP_PENDING,
          again.status(2))
    check("A7 nho loi cuoi", "502" in again.data["steps"]["2"]["last_error"],
          again.data["steps"]["2"]["last_error"])
    check("A7 dem so lan thu", again.attempts(2) >= 1, str(again.attempts(2)))
    check("A7 buoc ke tiep = 2", again.next_pending_step() == 2,
          str(again.next_pending_step()))
    check("A7 khong con file .tmp sot lai", not os.path.exists(again.path + ".tmp"),
          "con .tmp")
    blob = open(again.path, encoding="utf-8").read().lower()
    check("A7 khong luu bi mat",
          not any(k in blob for k in ("api_key", "password", "cookie", "prompt")), "")

    # ============================================================ A8
    print("\n[A8] buoc 4 hong -> buoc 1-3 KHONG chay lai")
    h = Harness()
    root, folders = h.make_folders(1)
    runs = {1: 0, 2: 0, 3: 0, 4: 0}
    state = {"fail4": True}

    def counting(n):
        def fn(folder, task):
            runs[n] += 1
            if n == 4 and state["fail4"]:
                raise LLMApiError("mat chet", "network")
            return os.path.join(folder, f"out_{n}.txt")
        return fn

    runner = build(h, {i: counting(i) for i in (1, 2, 3, 4)})

    def flip():
        time.sleep(2)
        state["fail4"] = False
        time.sleep(1)
        h.stop.set()

    threading.Thread(target=flip, daemon=True).start()
    runner.run(root)
    check("A8 buoc 1 chi chay 1 lan", runs[1] == 1, str(runs))
    check("A8 buoc 2 chi chay 1 lan", runs[2] == 1, str(runs))
    check("A8 buoc 3 chi chay 1 lan", runs[3] == 1, str(runs))
    check("A8 buoc 4 thu lai nhieu lan", runs[4] > 1, str(runs))

    # ============================================================ A9
    print("\n[A9] ma nguon khong co duong thoat ngoai Dung")
    fatal_patterns = [
        r"sys\.exit", r"raise SystemExit", r"os\._exit",
        r"fatal", r"unrecoverable",
    ]
    banned_files = ["auto_runner.py", "recovery.py", "checkpoint.py",
                    "browser_session.py", "browser_tx.py"]
    offenders = []
    for name in banned_files:
        path = os.path.join(SRC, name)
        if not os.path.exists(path):
            continue
        text = open(path, encoding="utf-8").read()
        for lineno, line in enumerate(text.split("\n"), 1):
            low = line.lower()
            for pat in fatal_patterns:
                if pat == "fatal":
                    if "fatal" in low and "#" not in line and '"""' not in line:
                        offenders.append(f"{name}:{lineno} fatal -> {line.strip()[:60]}")
                elif re.search(pat, low) and '"""' not in line and "#" not in line:
                    offenders.append(f"{name}:{lineno} {pat} -> {line.strip()[:60]}")
    check("A9 khong co lenh tu dung tool", not offenders, "; ".join(offenders[:4]))

    runner_src = open(os.path.join(SRC, "auto_runner.py"), encoding="utf-8").read()
    check("A9 chi ket thuc khi het folder hoac Dung",
          "auto_end_by_user" in runner_src and "auto_end_all_done" in runner_src,
          "thieu dieu kien ket thuc")
    check("A9 co checkpoint theo tung folder",
          "mark_complete" in runner_src and "all(self.is_done" in
          open(os.path.join(SRC, "checkpoint.py"), encoding="utf-8").read(),
          "thieu kiem tra 4 buoc")

    shutil.rmtree(BASE_DIR, ignore_errors=True)
    print("\n" + "=" * 52)
    print(f"AUTO NEVER-STOPS TESTS: {len(PASS)} pass, {len(FAIL)} fail")
    for name in FAIL:
        print("  FAILED: " + name)
    print("=" * 52)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())