# -*- coding: utf-8 -*-
"""
AutoRunner: đi qua từng folder trong input, chạy đủ 4 bước, không bao giờ
tự dừng.

Kịch bản người dùng: bấm Auto -> cắm máy đi ngủ -> sáng dậy mọi thứ xong.
Nên điều kiện kết thúc DUY NHẤT là:
    (mọi folder đã hoàn thành)  HOẶC  (người dùng nhấn Dừng)

Không có đường thoát thứ ba. Không có "lỗi nặng -> dừng cả batch".

Bốn bước trên mỗi folder:
    1  SRT câu        (luôn tự động, không gọi LLM)
    2  gộp SRT       (API hoặc Web LLM, tuỳ chế độ)
    3  tách kịch bản  (từ output bước 2, không gọi LLM)
    4  sinh prompt   (API hoặc Web LLM)

Một folder chỉ coi là xong khi bước 4 có file output.
"""
import os
import time
import traceback

from core.faults import BrowserActionRequired, StopRun, DeferFolder
from core import checkpoint as CK
from core.recovery import Recovery, run_step_with_recovery


class AutoRunner:
    def __init__(self, base_dir, steps, session=None, stop_event=None,
                 log=None, status=None, progress=None, on_folder_done=None,
                 headless=False, delay_base=None, delay_max=None):
        self.base_dir = base_dir
        self.steps = steps                 # dict 1..4 -> callable(folder, task)
        self.session = session
        self.stop_event = stop_event
        self.log = log or (lambda *a, **k: None)
        self.status = status or (lambda *a, **k: None)
        self.progress = progress or (lambda *a, **k: None)
        self.on_folder_done = on_folder_done or (lambda *a, **k: None)
        self.headless = headless
        extra = {}
        if delay_base is not None:
            extra["delay_base"] = delay_base
        if delay_max is not None:
            extra["delay_max"] = delay_max
        self.recovery = Recovery(log=self.log, status=self.status, **extra)
        self.stats = {"folders": 0, "done": 0, "failed": 0, "skipped": 0,
                    "retries": 0, "rounds": 0}
        # Lần thử liên tiếp của cùng 1 folder trước khi nhường chỗ cho folder
        # khác. KHÔNG phải giới hạn số lần thử: folder hỏng vĩnh viễn vẫn được
        # thử vô hạn qua các vòng, chỉ là không gian chỗ cho folder còn lại.
        self.defer_after = 3

    # ------------------------------------------------------------------ tick
    def _tick(self, mode="", exc=None):
        """Được recovery gọi mỗi vòng. False = người dùng bấm Dừng."""
        if self.stop_event is not None and self.stop_event.is_set():
            return False
        if mode == "restart_browser" and self.session is not None:
            # Restart KHÔNG set stop_event -> Auto vẫn chạy tiếp.
            try:
                self.session.restart_browser()
            except Exception as exc:
                self.log("browser_restart_failed", err=str(exc)[:140])
        return True

    # ------------------------------------------------------------------ main
    def run(self, input_dir, only_folders=None):
        """Chạy hết các folder theo vòng tròn cho tới khi xong hết hoặc Dừng.

        Vòng tròn, không phải một lượt tuần tự: một folder hỏng vĩnh viễn (hết
        tiền API, kịch bản hỏng) sẽ thử vô hạn và nếu chạy tuần tự thì CHẶN
        vĩnh viễn tất cả folder còn lại. Người dùng ngủ, sáng dậy thấy 1
        folder lỗi kéo theo 20 folder khác chưa chạy. Vòng tròn giữ đúng cả
        hai lời hứa: không bỏ folder nào, và không để folder chết chôn đứa khác.
        """
        folders = only_folders if only_folders else CK.scan_inputs(input_dir)
        if not folders:
            self.status("Không có folder nào trong input.", "red")
            self.log("auto_no_folders", input_dir=input_dir)
            return {"completed": 0, "failed": 0}

        self.stats["folders"] = len(folders)
        self.log("auto_begin", folders=len(folders), input=input_dir)

        pending = list(folders)
        total = len(pending)
        round_no = 0

        while pending:
            if self.stop_event is not None and self.stop_event.is_set():
                self.log("auto_stop_by_user", pending=len(pending), total=total)
                break
            round_no += 1
            self.stats["rounds"] = round_no
            # Folder nào đã xong trong vòng này -> bỏ khỏi hàng đợi.
            still_pending = []
            for folder in pending:
                if self.stop_event is not None and self.stop_event.is_set():
                    still_pending.append(folder)
                    continue
                name = os.path.basename(os.path.normpath(folder))
                index = total - len(pending) + 1
                self.status(f"[{index}/{total}] Đang xử lý: {name}", "orange")
                self.progress(index, total, name)
                try:
                    self._run_folder(folder, name, index, total)
                    self.stats["done"] += 1
                    self.on_folder_done(folder, True)
                    continue                      # xong -> không quay lại nữa
                except StopRun:
                    self.log("auto_folder_stopped", folder=name)
                    self.on_folder_done(folder, False)
                    still_pending.append(folder)
                    continue
                except DeferFolder as exc:
                    # Nhường chỗ, KHÔNG phải lỗi và KHÔNG phải dừng.
                    self.log("auto_defer_step", folder=name, detail=str(exc)[:140])
                    still_pending.append(folder)
                    continue
                except Exception as exc:
                    self.stats["failed"] += 1
                    self.log("auto_folder_failed", folder=name, err=str(exc)[:200],
                             tb=traceback.format_exc()[-400:])
                    self.status(f"⚠ {name}: {str(exc)[:90]}", "red")
                    self.on_folder_done(folder, False)
                    still_pending.append(folder)   # vòng sau thử lại, KHÔNG bỏ
                pending = still_pending
                if pending:
                    self.log("auto_defer_folder", folder=name,
                             pending_left=len(pending))
                    # Nghỉ ngắn trước khi sang folder kế tiếp.
                    if not self._sleep_between(1.0):
                        break
            pending = still_pending
            if pending and not (self.stop_event and self.stop_event.is_set()):
                self.log("auto_round_end", round=round_no, pending=len(pending))
                if not self._sleep_between(2.0):
                    break

        self.stats["retries"] = self.recovery.total_retries
        if self.stop_event is not None and self.stop_event.is_set():
            self.log("auto_end_by_user", **self.stats)
            self.status(f"⏹ Đã dừng theo yêu cầu. Xong "
                        f"{self.stats['done']}/{self.stats['folders']} folder.",
                        "red")
        else:
            self.log("auto_end_all_done", **self.stats)
            self.status(f"✅ Hoàn thành {self.stats['done']}/{self.stats['folders']} "
                        f"folder (thử lại {self.stats['retries']} lần).", "green")
        self.progress(total, total, "")
        return {"completed": self.stats["done"], "failed": self.stats["failed"],
                "retries": self.stats["retries"]}

    def _sleep_between(self, seconds):
        """Nghỉ giữa các folder, ngắt được bằng Dừng."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            if self.stop_event is not None and self.stop_event.is_set():
                return False
            time.sleep(0.1)
        return True

    # --------------------------------------------------------------- 1 folder
    def _run_folder(self, folder, name, index, total):
        cp = CK.Checkpoint(folder).load()
        if cp.completed():
            self.log("folder_already_complete", folder=name)
            self.stats["skipped"] += 1
            self.status(f"[{index + 1}/{total}] {name}: đã có đủ 4 bước, bỏ qua",
                        "green")
            return

        cp.save()
        for step in (1, 2, 3, 4):
            if self.stop_event is not None and self.stop_event.is_set():
                raise StopRun()
            # Bước đã DONE thì BỎ QUA, không chạy lại. Không có ngoại lệ.
            # Bản cũ chạy lại từ đầu mỗi lần thử -> mỗi đêm đốt tiền API
            # cho công việc đã xong. Đây là chỗ tốn kém nhất.
            if cp.is_done(step):
                self.log("step_already_done", folder=name, step=step)
                continue
            fn = self.steps.get(step)
            if fn is None:
                self.log("step_missing", folder=name, step=step)
                continue
            self.status(f"[{index + 1}/{total}] {name} — bước {step}/4", "orange")
            run_step_with_recovery(
                lambda s=step, f=fn: f(folder, f"{name}|s{s}"),
                f"{name} bước {step}", self.recovery, cp, step,
                on_tick=lambda m="", e=None: self._tick(m, e),
                defer_after=self.defer_after)

        # Không có đường nào đánh dấu xong nếu bước 4 chưa DONE.
        if cp.mark_complete():
            self.log("folder_complete", folder=name)
        else:
            self.log("folder_incomplete_no_step4", folder=name)