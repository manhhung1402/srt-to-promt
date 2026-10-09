# -*- coding: utf-8 -*-
"""
Giao dien + dieu phoi cho che do "Auto nhieu kich ban".
Chay lien tuc: buoc 1 -> buoc 2 -> tach kich ban -> sinh prompt, lap lai cho tung
thu muc con, moi san pham xuat ngay vao thu muc con do.
(UI + orchestration for the unattended multi-script run.)

ponytail: dieu phoi chay tren luong nen, moi thao tac widget duoc goi qua root.after
va cho bang Event de khong chan mainloop. Khi can chay song song nhieu kich ban:
tach thanh nhieu luong worker roi.
"""

import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, ttk

from core import auto_pipeline
from core import checkpoint as CK
from core.faults import (DeferFolder, BrowserActionRequired, StepFailure,
                         StopRun)
from core.recovery import Recovery, run_step_with_recovery
from utils.exceptions import browser_action_state
from core.prompt_generator import _resolve_max_attempts
from ui import theme

# So lan thu lien tiep truoc khi nhuong cho folder khac. KHONG phai gioi
# han so lan thu: folder hong vinh vien van duoc thu vo han qua cac vong,
# chi la khong gian cho cac folder con lai.
DEFER_AFTER = 3

SORT_KEYS = (auto_pipeline.SORT_ALPHABET, auto_pipeline.SORT_NEWEST, auto_pipeline.SORT_OLDEST)
SORT_BY_LABEL = {auto_pipeline.SORT_LABELS[key]: key for key in SORT_KEYS}

# Mau cua hop nhom auto -> muc do log trong nhat ky (chi dung de tach file log loi).
_AUTO_LOG_LEVEL = {"red": "error", "orange": "warn", "green": "info", "blue": "info"}


def _category_of_message(message):
    """Doan loi GUI -> nhom, de recovery biet co phai loi trinh duyet hay khong."""
    state = browser_action_state(message)
    if state:
        return "browser_action"
    return "step"


def _auto_log_level(color):
    return _AUTO_LOG_LEVEL.get(str(color or "blue").lower(), "info")


class AutoRunner:
    """Chạy chuỗi bước trên nhiều thư mục con, báo cáo tiến độ qua callback."""

    def __init__(self, app, log_cb):
        self.app = app
        self.log_cb = log_cb
        self.stop_event = threading.Event()
        # Hai hàng đỡi: lệnh cần chạy trên luồng chính, và kết quả bước GUI vừa xong.
        # Luồng nền chỉ đấy lên hàng đỡi - KHÔNG gọi root.after trực tiếp
        # (Tk không an toàn khi gọi từ thread khác).
        self._tasks = queue.Queue()
        self._steps = queue.Queue()
        self._task_result = queue.Queue()
        self._done = threading.Event()
        # Phuc hoi dung chu quy tac "khong tu dung": dem loi, quyet dinh cho
        # bao lau, va cho biet may phai cho ai.
        self.recovery = Recovery(log=self._log_event, status=lambda *a, **k: None)
        self.defer_after = DEFER_AFTER

    def request_stop(self):
        self.stop_event.set()
        self.app.browser_mgr.stop_event.set()

    def _log_event(self, event, **fields):
        """Ghi 1 su kien phuc hoi vao nhat ky (dung khi co thong tin can lai)."""
        if fields:
            detail = " ".join(f"{k}={str(v)[:60]}" for k, v in sorted(fields.items()))
            self.log(f"[{event}] {detail}", "blue")
        else:
            self.log(f"[{event}]", "blue")

    def _check_stop(self):
        if self.stop_event.is_set():
            raise StopRun()

    def start_pump(self):
        """Khởi động vòng bơm lệnh trên luồng chính (gọi từ chính, không phải thread)."""
        self._pump()

    def _pump(self):
        try:
            while True:
                task = self._tasks.get_nowait()
                if task is None:
                    return
                fn, args, kwargs = task
                try:
                    self._task_result.put((True, fn(*args, **kwargs)))
                except BaseException as exc:
                    self._task_result.put((False, exc))
                self._done.set()          # lenh chinh da chay xong
        except queue.Empty:
            pass
        try:
            if not self.stop_event.is_set():
                self.app.root.after(30, self._pump)
        except Exception:
            pass

    # ---------- tiện ích giao diện ----------
    def call_gui(self, fn, *args, **kwargs):
        """Gọi hàm trên luồng chính và chờ xong (không bao giờ chặn mainloop)."""
        self._done.clear()
        self._tasks.put((fn, args, kwargs))
        while not self._done.wait(0.05):
            self._check_stop()
        ok, value = self._task_result.get()
        if not ok:
            raise value
        return value

    def report_step(self, step, ok, message=""):
        """Được app gọi khi 1 bước GUI kết thúc (xem VideoWorkflowTool._notify_auto_step)."""
        self._steps.put((step, bool(ok), str(message)))

    def wait_step(self, step=None):
        """Chờ kết quả của đúng bước `step`; bỏ qua tín hiệu cũ của bước trước."""
        while True:
            try:
                got_step, ok, message = self._steps.get(timeout=0.2)
            except queue.Empty:
                self._check_stop()
                continue
            if step is None or got_step == step:
                return ok, message

    def drain_signals(self):
        """Xoá hết tín hiệu còn tồn đọng trước khi bắt đầu lượt thử mới.
        (Clear stale signals before a fresh attempt.)"""
        while True:
            try:
                self._steps.get_nowait()
            except queue.Empty:
                return

    def take_step_signal(self, step, timeout=0.0):
        """Lấy tín hiệu của đúng bước `step` nếu có (không chờ vô hạn).
        (Non-blocking check for this step's signal; ignores other steps.)"""
        deadline = time.time() + max(0.0, timeout)
        while True:
            try:
                got_step, ok, message = self._steps.get_nowait()
            except queue.Empty:
                if time.time() >= deadline:
                    return None, ""
                return None, ""
            if got_step == step:
                return ok, message

    def log(self, text, color="blue"):
        # Ghi vao ca hop nhom 8 dong cua auto panel VA nhat ky 50 dong + file log.
        # Truoc day chi ghi vao hop nhom nen buoc auto khong hien trong nhat ky,
        # mat phong "da chay toi dau" khi can tim kich ban treo.
        self.app.log_event(text, _auto_log_level(color))
        self.call_gui(self.log_cb, text, color)

    def text_of(self, widget):
        # Box kết quả có số thứ tự -> phải bỏ tiền tố trước khi xuất file.
        return self.call_gui(lambda: theme.get_numbered(widget)).strip()

    def set_text(self, widget, value):
        def apply():
            widget.delete("1.0", tk.END)
            widget.insert(tk.END, value)
        self.call_gui(apply)

    def set_entry(self, entry, value):
        def apply():
            entry.delete(0, tk.END)
            entry.insert(0, value)
        self.call_gui(apply)

    # ---------- vòng lặp chính ----------
    def run(self, config):
        self.app._auto_step_hook = self.report_step
        try:
            folders = self.call_gui(auto_pipeline.list_script_folders,
                                    config["root"], config["sort"])
            if not folders:
                self.log(f"Không có thư mục con nào trong: {config['root']}", "red")
                return
            self.log(
                f"Tìm thấy {len(folders)} kịch bản - bấm Dừng để thoát. "
                "Auto tự chạy tiếp tới khi hết hoặc bạn bấm Dừng.", "green")

            # VÒNG TRÒN, không phải một lượt tuần tự.
            # Lý do: bản cũ duyệt folder tuần tự và "bỏ qua" folder lỗi.
            # Người dùng ngủ, sáng dậy thấy 1 folder hỏng kéo theo 20 folder
            # khác chưa chạy. Nay mot folder hong thi thu vo han, nhung
            # nhuong cho folder khac chay truoc. Giu ca hai loi hua: khong
            # bo folder nao, va khong de folder chet chon dua khac.
            pending = list(folders)
            total = len(folders)
            done = failed = round_no = 0

            while pending:
                if self.stop_event.is_set():
                    break
                round_no += 1
                still_pending = []
                for index, folder in enumerate(pending, start=1):
                    if self.stop_event.is_set():
                        still_pending.append(folder)
                        continue
                    name = os.path.basename(folder)
                    self.log(f"[{index}/{total}] (vòng {round_no}) ▶ {name}", "orange")
                    try:
                        self._run_folder(folder, config)
                        done += 1
                        self.log(f"[{index}/{total}] ✔ Hoàn thành: {name}", "green")
                        continue          # xong -> khong quay lai nua
                    except StopRun:
                        still_pending.append(folder)
                        continue
                    except DeferFolder as exc:
                        # Nhuong cho folder khac. KHONG phai loi, KHONG phai dung.
                        self.log(f"[{index}/{total}] … {name}: {str(exc)[:110]}", "orange")
                        still_pending.append(folder)
                        continue
                    except Exception as exc:
                        failed += 1
                        self.log(f"[{index}/{total}] ⚠ {name}: {str(exc)[:150]}", "red")
                        still_pending.append(folder)   # vong sau thu lai, KHONG bo
                    if not self._interruptible_wait(1.0):
                        break
                pending = still_pending
                if pending and not self.stop_event.is_set():
                    self.log(f"Hết vòng {round_no} - còn {len(pending)} kịch bản chờ. "
                             "Tiếp tục thử lại.", "blue")
                    if not self._interruptible_wait(2.0):
                        break

            retries = self.recovery.total_retries
            if self.stop_event.is_set():
                self.log(f"⏹ Đã dừng theo yêu cầu - xong {done}/{total}.", "blue")
            elif pending:
                self.log(f"⏹ Xong {done}/{total}, còn {len(pending)} kịch bản chờ.", "red")
            else:
                self.log(f"✅ Hoàn thành {done}/{total} kịch bản "
                         f"(thử lại {retries} lần).", "green")
        except StopRun:
            self.log("Đã dừng theo yêu cầu.", "blue")
        except Exception as exc:
            self.log(f"Lỗi chung: {exc}", "red")
        finally:
            self.app._auto_step_hook = None

    def run_step(self, label, start_fn, limit=None, sync=False, cp=None, step=None):
        """Chạy 1 bước, thử lại cho tới khi xong.

        limit=None là chế độ đêm: không giới hạn số lần thử, đúng yêu cầu
        "auto liên tục là tất yếu". Sau `defer_after` lần thử liên tiếp thì
        ném DeferFolder để folder khác có cơ hội chạy; tổng số lần thử vẫn
        vô hạn qua các vòng.
        cp/step: checkpoint của folder + số bước (1..4) để ghi tiến độ.
        """
        label_id = label

        def attempt_once():
            self.call_gui(setattr, self.app, "_auto_current_step", label)
            self.drain_signals()      # xoá tín hiệu tồn đọng của lần thử trước
            self.call_gui(start_fn)
            if sync:
                # Hàm chạy ĐỒNG BỘ trong luồng giao diện: call_gui chỉ trả về
                # khi hàm đã xong, nên không có tín hiệu = đã xong.
                # Hàm sync chỉ báo tín hiệu khi HỎNG (qua _auto_step_cancelled):
                #   None -> thành công, False -> lỗi.
                # Trước đây coi None là lỗi -> Bước 3 chết ngay "không trả kết
                # quả" dù đã chạy xong (test giả có notify(True) nên lọt).
                ok, message = self.take_step_signal(label)
                if ok is False:
                    raise StepFailure(f"{label}: {str(message)[:200]}",
                                      _category_of_message(message), True)
                return
            ok, message = self.wait_step(label)
            if not ok:
                raise StepFailure(f"{label}: {str(message)[:200]}",
                                  _category_of_message(message), True)

        attempt = 0
        while True:
            attempt += 1
            if limit is not None and attempt > limit:
                raise StepFailure(
                    f"{label}: vượt số lần thử cho phép ({limit}). "
                    "Giữ nguyên trạng thái, không đánh dấu hoàn thành.",
                    "max_attempts", True)
            if self.defer_after and attempt > self.defer_after:
                raise DeferFolder(
                    f"{label}: đã thử {attempt} lần, nhường chỗ cho kịch bản khác. "
                    "Sẽ thử lại ở vòng sau.")
            tag = f"lần {attempt}" if limit is None else f"lần {attempt}/{limit}"
            self.log(f"{label} ({tag})...", "blue")
            try:
                # checkpoint/step truyen vao chi de ghi loi, KHONG de gan DONE.
                # DONE chi gan sau khi file output da ghi that (xem _export).
                # Gan o day se tao trang thai "DONE ma khong co file": vong sau
                # bo qua buoc, buoc khong bao gio chay lai duoc, ca dem thang.
                run_step_with_recovery(
                    attempt_once, label, self.recovery, None, None,
                    max_attempts=None if limit is None else 1,
                    on_tick=self._on_tick,
                    defer_after=self.defer_after)
                self.log(f"{label} ✓ ({tag})", "green")
                return
            except (DeferFolder, StopRun):
                raise
            except BrowserActionRequired as exc:
                # KHONG phai loi ky thuat. Cho nguoi xu ly, khong ton luot thu.
                self.log(f"{label}: Web LLM cần xử lý {exc.state or 'UNKNOWN'}. "
                         "Hoàn tất trên Chrome; Auto sẽ tự tiếp tục.", "orange")
                if self.app.browser_mgr.wait_for_manual_recovery(timeout_secs=300):
                    self.log(f"{label}: Web LLM đã sẵn sàng - tiếp tục Auto.", "green")
                    attempt -= 1   # login/Cloudflare khong tinh la mot lan loi
                    continue
                raise
            except Exception as exc:
                # run_step_with_recovery da lo backoff va ghi checkpoint;
                # o day chi la loi khong quay lai duoc (nhu DeferFolder).
                self.log(f"{label} lỗi: {str(exc)[:120]}", "red")
                if not self._interruptible_wait(2.0):
                    raise StopRun()
                raise

    def _on_tick(self, mode="", exc=None):
        """recovery goi moi vong. False = nguoi dung bam Dung -> phai dung."""
        if self.stop_event.is_set():
            return False
        if mode == "restart_browser":
            # Restart KHONG set stop_event -> Auto van chay tiep.
            try:
                self.app.browser_mgr.restart_chrome()
            except Exception as err:
                self._log_event("browser_restart_failed", err=str(err)[:140])
        return True

    def _interruptible_wait(self, seconds):
        deadline = time.time() + max(1, int(seconds))
        while time.time() < deadline:
            if self.stop_event.is_set():
                return False
            time.sleep(0.3)
        return not self.stop_event.is_set()

    def _run_folder(self, folder, config):
        app = self.app
        limit = _resolve_max_attempts(config.get("retries"))
        found = self.call_gui(auto_pipeline.find_input_files, folder)
        missing = auto_pipeline.missing_inputs(found)
        if missing:
            raise ValueError("; ".join(missing))

        stem = os.path.basename(folder)
        out_dir = config["out"] or folder

        # ---- Checkpoint: ghi de moi buoc, dung de buoc da xong khong chay lai
        # Khong co checkpoint, tool bao "xong" roi chay lai tu Buoc 1 -> moi dem
        # dot tien API cho viec da lam xong. Day la cho ton kem nhat.
        cp = CK.Checkpoint(out_dir).load()
        if cp.completed():
            self.log(f"↷ {stem}: đã có đủ 4 bước trong checkpoint, bỏ qua.", "green")
            return

        cp.save()
        # Checkpoint co the noi "DONE" nhung file khong con (nguoi dung xoa,
        # may chet giua luc ghi). Khoi phuc: chi tin file that.
        for st in (1, 2, 3, 4):
            if cp.is_done(st) and cp.output(st) and not os.path.exists(
                    os.path.join(out_dir, cp.output(st))):
                self.log(f"↷ Bước {st} ghi xong nhưng thiếu file "
                         f"{cp.output(st)} - chạy lại.", "orange")
                cp.data["steps"][str(st)]["status"] = CK.STEP_PENDING
        cp.save()
        self._prepare_folder(folder, out_dir, stem, found)

        # ---- Buoc 1: doi chieu & tao SRT (tu dong, khong goi LLM)
        # BO QUA Bước 1 CHỈ KHI state trong RAM còn dung. Không phải chỉ
        # khi file output còn.
        #
        # Ly do: Bước 2 đọc app.temp_sentence_timings - chi nam trong RAM,
        # KHONG ghi ra file. Ban cu chi kiem file nen restart la tool bao
        # "buoc 1 xong" roi bo qua, nhung RAM rong -> Bước 2 chet ngay voi
        # "Hãy hoàn thành Bước 1 trước!". Do lai Bước 1 KHONG ton API: khi
        # co file SRT dau vao, no chi doc SRT + doi chieu, khong goi Whisper.
        # Chay lai 1 lan de an toan hon la bo qua roi lam Bước 2 hong.
        state_alive = bool(getattr(app, "temp_sentence_timings", None))
        if not cp.is_done(1):
            self.run_step("Bước 1 · Đối chiếu & tạo SRT",
                          app.start_srt_sentence_thread, limit, cp=cp, step=1)
            self._export(app.txt_srt_sentence, out_dir, stem + "_01_srt_cau.srt", cp, 1)
        elif state_alive:
            self.log("↷ Bước 1 đã xong và dữ liệu còn trong bộ nhớ - bỏ qua.", "blue")
        else:
            self.log("↷ Bước 1 đã xong nhưng dữ liệu trong bộ nhớ đã mất "
                     "(vừa khởi động lại) - chạy lại Bước 1, không tốn API.", "blue")
            cp.data["steps"]["1"]["status"] = CK.STEP_PENDING
            self.run_step("Bước 1 · Đối chiếu & tạo SRT",
                          app.start_srt_sentence_thread, limit, cp=cp, step=1)
            self._export(app.txt_srt_sentence, out_dir, stem + "_01_srt_cau.srt", cp, 1)

        # ---- Buoc 2: gop SRT (API hoac LLM Web, khong lui ve thuat toan)
        if not cp.is_done(2):
            self.run_step("Bước 2 · Gộp SRT",
                          app.start_group_scenes_thread, limit, cp=cp, step=2)
            self._export(app.txt_srt_scene, out_dir, stem + "_02_srt_gop.srt", cp, 2)
        else:
            self.log("↷ Bước 2 đã xong trước đó - bỏ qua.", "blue")

        # ---- Buoc 3: loc & tach kich ban
        from_step1 = config["script_source"] == "step1"
        out_widget = app.txt_script_out_l if from_step1 else app.txt_script_out_r
        stem3 = stem + ("_03_kichban_cau.txt" if from_step1 else "_03_kichban_doan.txt")
        if not cp.is_done(3):
            # Widget co the rong sau khi restart -> nap lai tu file output
            # Buoc 1/2 da co, truoc khi goi ham doc widget.
            src_widget = app.txt_srt_sentence if from_step1 else app.txt_srt_scene
            src_name = stem + ("_01_srt_cau.srt" if from_step1 else "_02_srt_gop.srt")
            if not self.text_of(src_widget):
                self._restore_widget(src_widget, out_dir, src_name, numbered=True)
            self.run_step("Bước 3 · Lọc & tách kịch bản",
                          app.load_script_left if from_step1 else app.load_script_right,
                          limit, sync=True, cp=cp, step=3)
            self.run_step("Bước 3 · Chạy lọc & tách",
                          app.process_script_left if from_step1 else app.process_script_right,
                          limit, sync=True, cp=cp, step=3)
        else:
            self.log("↷ Bước 3 đã xong trước đó - bỏ qua.", "blue")
        self._export(out_widget, out_dir, stem3, cp, 3)

        # ---- Buoc 4: sinh prompt (chi chuyen thu muc con sau khi HOAN THANH)
        from_sentence = config["prompt_source"] == "sentence"
        if not cp.is_done(4):
            # Tuong tu Buoc 3: nap lai ket qua Buoc 3 tu file neu widget rong.
            src_widget = app.txt_script_out_l if from_sentence else app.txt_script_out_r
            src_name = stem + ("_03_kichban_cau.txt" if from_sentence
                               else "_03_kichban_doan.txt")
            if not self.text_of(src_widget):
                self._restore_widget(src_widget, out_dir, src_name, numbered=True)
            self.run_step("Bước 4 · Nạp kịch bản sinh prompt",
                          app.load_prompt_in_left if from_sentence else app.load_prompt_in_right,
                          limit, sync=True, cp=cp, step=4)
            self.run_step("Bước 4 · Sinh Prompt",
                          app.start_llm_thread, limit, cp=cp, step=4)
        else:
            self.log("↷ Bước 4 đã xong trước đó - bỏ qua.", "blue")
        self._export(app.txt_prompts, out_dir, stem + "_04_prompts.txt", cp, 4)

        self.call_gui(self._reset_scenario)
        # KHONG co duong nao danh dau xong khi buoc 4 chua co output that.
        if cp.mark_complete():
            self.log(f"✔ {stem}: đủ 4 bước.", "green")
        else:
            self.log(f"⚠ {stem}: buoc 4 chua DONE, van cho thu lai.", "orange")
            raise ValueError(f"{stem}: bước 4 chưa hoàn thành")

    def _restore_widget(self, widget, out_dir, name, numbered=True):
        """Nạp lại nội dung widget từ file output đã có (sau khi restart).

        Widget chỉ là ô trên màn hình — mất trắng khi tool khởi động lại, nhưng
        file vẫn còn. Bước 3/4 đọc widget nên không nạp lại thì chết với
        "Ô ... đang rỗng!" dù công việc đã xong.

        numbered=True cho widget hiển thị có số thứ tự (đọc qua get_numbered).
        """
        path = os.path.join(out_dir, name)
        if not os.path.exists(path):
            return False
        for encoding in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
            try:
                with open(path, "r", encoding=encoding) as fh:
                    content = fh.read().strip()
            except (UnicodeDecodeError, OSError):
                continue
            if content:
                self.call_gui(theme.set_numbered, widget, content) if numbered \
                    else self.set_text(widget, content)
                self.log(f"↷ Nạp lại {name} từ file ({len(content)} ký tự).", "blue")
                return True
        return False

    def _prepare_folder(self, folder, out_dir, stem, found):
        """Nap du lieu dau vao vao giao dien (giu nguyen logic cu)."""
        app = self.app
        self.set_entry(app.entry_srt_in_path, found["srt"])
        self.set_entry(app.entry_audio_path, found["audio"])
        reference = self._read_text_file(found["txt"]) if found["txt"] else ""
        self.set_text(app.txt_reference_text, reference)

    def _export(self, widget, out_dir, name, cp=None, step=None):
        """Ghi widget ra file, roi danh dau buoc DONE trong checkpoint.

        KHONG danh dau DONE truoc khi file da ghi thanh cong: buoc chua co
        output that thi phai chay lai, khong duoc cong cha.
        """
        path = os.path.join(out_dir, name)
        if cp is not None and step is not None and os.path.exists(path):
            # Da co output tu lien truoc -> buoc nay xong that, khong ghi lai.
            cp.mark_done(step, os.path.basename(path))
            self.log(f"↷ {name} đã có sẵn - bỏ qua.", "blue")
            return
        try:
            self.export_widget(widget, path)
        except Exception as exc:
            # Khong gan DONE. Ghi lai ly do de sang mai doc ra biet.
            if cp is not None and step is not None:
                cp.mark_failed(step, exc, "export")
            raise
        if cp is not None and step is not None:
            cp.mark_done(step, os.path.basename(path))

    @staticmethod
    def _read_text_file(path):
        for encoding in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
            try:
                with open(path, "r", encoding=encoding) as handle:
                    return handle.read().strip()
            except UnicodeDecodeError:
                continue
            except OSError as exc:
                raise ValueError(f"Không đọc được {path}: {exc}")
        raise ValueError(f"Không đọc được file txt: {path}")

    def export_widget(self, widget, path):
        """Ghi nội dung widget ra file. PHḊI gọi từ luồng nền: đọc nội dung qua
        call_gui rồi tự ghi file, không được gọi call_gui bên trong hàm chạy trên
        luồng chính (sẹ deadlock)."
        (Runs on the worker thread: read via call_gui, then write the file here.)"""
        content = self.text_of(widget)
        if not content:
            raise ValueError(f"Không có nội dung để xuất: {os.path.basename(path)}")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)

    def _reset_scenario(self):
        app = self.app
        app.temp_sentence_timings = []
        app.temp_script_lines = []
        for entry in (app.entry_audio_path, app.entry_srt_in_path):
            entry.delete(0, tk.END)
        for widget in (app.txt_reference_text, app.txt_srt_scene, app.txt_prompts):
            widget.delete("1.0", tk.END)
        theme.set_numbered(app.txt_srt_sentence, "")


def create_auto_panel(app, parent):
    """Nút 'Cài đặt auto' + bảng tuò chọn nhỏ, đặt ngay bên dưới phần input."""
    app.auto_root = tk.StringVar()
    app.auto_out = tk.StringVar()
    app.auto_sort_label = tk.StringVar(value=auto_pipeline.SORT_LABELS[auto_pipeline.SORT_ALPHABET])
    app.auto_script_source = tk.StringVar(value="step1")
    app.auto_prompt_source = tk.StringVar(value="sentence")
    app._auto_step_hook = None
    app._auto_runner = None

    def label(master, text, style="Card.TLabel"):
        return ttk.Label(master, text=text, style=style)

    auto_box = ttk.Frame(parent, style="Card.TFrame", padding=(theme.SP_LG, theme.SP_MD))

    def toggle_panel():
        if auto_box.winfo_ismapped():
            auto_box.pack_forget()
            btn_toggle.config(text="⚙ Cài đặt auto")
        else:
            auto_box.pack(fill="x", pady=(theme.SP_MD, 0), before=btn_toggle)
            btn_toggle.config(text="⚙ Cài đặt auto (đang mở)")

    def browse_root():
        path = filedialog.askdirectory(title="Chọn thư mục chứa các thư mục con kịch bản")
        if path:
            app.auto_root.set(path)

    def browse_out():
        path = filedialog.askdirectory(title="Chọn thư mục xuất kết quả")
        if path:
            app.auto_out.set(path)

    head = ttk.Frame(auto_box, style="Card.TFrame")
    head.pack(fill="x")
    label(head, "AUTO NHIỀU KỊCH BẢN", "Card.Section.TLabel").pack(side="left")
    ttk.Button(head, text="Đóng", style="Ghost.TButton",
               command=toggle_panel).pack(side="right")

    # KHÔNG có "Phương thức" ở đây: Bước 2 và Bước 4 dùng cài đặt riêng
    # của từng tab (ai_source_var / prompt_source_var), độc lập nhau.

    row_root = ttk.Frame(auto_box, style="Card.TFrame")
    row_root.pack(fill="x", pady=(theme.SP_SM, 0))
    label(row_root, "Thư mục kịch bản", "Section.TLabel").pack(side="left")
    ttk.Entry(row_root, textvariable=app.auto_root).pack(
        side="left", fill="x", expand=True, padx=(theme.SP_MD, theme.SP_SM))
    ttk.Button(row_root, text="Chọn", style="Ghost.TButton", command=browse_root).pack(side="left")

    row_sort = ttk.Frame(auto_box, style="Card.TFrame")
    row_sort.pack(fill="x", pady=(theme.SP_SM, 0))
    label(row_sort, "Thứ tự chạy", "Section.TLabel").pack(side="left")
    ttk.Combobox(row_sort, textvariable=app.auto_sort_label, state="readonly", width=26,
                 values=[auto_pipeline.SORT_LABELS[key] for key in SORT_KEYS]).pack(
        side="left", padx=(theme.SP_MD, 0))

    row_out = ttk.Frame(auto_box, style="Card.TFrame")
    row_out.pack(fill="x", pady=(theme.SP_SM, 0))
    label(row_out, "Thư mục xuất", "Section.TLabel").pack(side="left")
    ttk.Entry(row_out, textvariable=app.auto_out).pack(
        side="left", fill="x", expand=True, padx=(theme.SP_MD, theme.SP_SM))
    ttk.Button(row_out, text="Chọn", style="Ghost.TButton", command=browse_out).pack(side="left")
    ttk.Label(row_out, text="để trống = xuất vào cùng thư mục con",
              style="Card.Muted.TLabel").pack(side="left", padx=theme.SP_SM)

    row_split = ttk.Frame(auto_box, style="Card.TFrame")
    row_split.pack(fill="x", pady=(theme.SP_SM, 0))
    label(row_split, "Tách kịch bản", "Section.TLabel").pack(side="left")
    ttk.Radiobutton(row_split, text="Từ bước 1", value="step1",
                    variable=app.auto_script_source).pack(side="left", padx=(theme.SP_MD, theme.SP_SM))
    ttk.Radiobutton(row_split, text="Từ bước 2", value="step2",
                    variable=app.auto_script_source).pack(side="left")

    row_prompt = ttk.Frame(auto_box, style="Card.TFrame")
    row_prompt.pack(fill="x", pady=(theme.SP_SM, theme.SP_SM))
    label(row_prompt, "Sinh prompt", "Section.TLabel").pack(side="left")
    ttk.Radiobutton(row_prompt, text="Từ câu", value="sentence",
                    variable=app.auto_prompt_source).pack(side="left", padx=(theme.SP_MD, theme.SP_SM))
    ttk.Radiobutton(row_prompt, text="Từ đoạn", value="paragraph",
                    variable=app.auto_prompt_source).pack(side="left")

    app.lbl_auto_log = ttk.Label(auto_box, text="Sẵn sàng (Ready)", style="Card.Muted.TLabel",
                                 anchor="w", justify="left", wraplength=780)
    app.lbl_auto_log.pack(fill="x")

    def append_log(text, color="blue"):
        lines = str(app.lbl_auto_log.cget("text")).splitlines()
        lines.append(str(text))
        app.lbl_auto_log.config(text="\n".join(lines[-8:]), foreground=theme.status(color))

    app._auto_log = append_log
    app.btn_auto_run = ttk.Button(parent, text="▶ Auto nhiều kịch bản", style="Primary.TButton",
                                  command=app.start_auto_run)
    app.btn_auto_run.pack(fill="x", pady=(theme.SP_SM, theme.SP_XS))
    app.btn_auto_stop = ttk.Button(parent, text="⏹ Dừng auto", style="Danger.TButton",
                                   command=app.stop_auto_run, state="disabled")
    app.btn_auto_stop.pack(fill="x")

    btn_toggle = ttk.Button(parent, text="⚙ Cài đặt auto", style="Ghost.TButton",
                            command=toggle_panel)
    btn_toggle.pack(fill="x", pady=(theme.SP_MD, 0))
    return auto_box
