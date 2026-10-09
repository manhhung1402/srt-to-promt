# -*- coding: utf-8 -*-
"""
Cầu nối giữa code cũ và tầng trình duyệt mới (Playwright).

Vì sao cần: `scene_grouper` và `prompt_generator` gọi `browser_mgr` theo một
interface cũ (11 hàm). Thay toàn bộ hai file đó là viết lại rủi ro hơn là
làm lại. Nên ở đây ta giữ NGUYÊN interface và đổi bên trong.

Interface giữ nguyên (đã kiểm bằng grep trên toàn source):
    check_stop, close_task_tab, get_current_llm, get_selectors, get_task_tab,
    inject_text_and_send, kill_chrome, restart_chrome, try_upload_file,
    wait_for_llm_response, wait_for_manual_recovery

Khác biệt quan trọng: mỗi lần gọi Web LLM giờ là MỘT TRANSACTION trọn vẹn,
nên `inject_text_and_send` + `wait_for_llm_response` vẫn giữ nguyên hình dạng
nhưng bên trong đã có bằng chứng gửi và baseline.
"""
import os
import time

from core.faults import BrowserActionRequired, StopRun, StepFailure
from core.browser_session import BrowserSession
from core import page_state as PS
from core.providers import get_adapter, host_matches


class BrowserManagerV3:
    """Drop-in thay BrowserManager cũ (Selenium)."""

    def __init__(self, base_dir, get_current_llm_fn, ask_modal_fn=None,
                 status_cb=None, log_cb=None):
        self.base_dir = base_dir
        # scene_grouper/prompt_generator gọi browser_mgr.get_current_llm(),
        # nên tên thuộc tính này phải giữ nguyên.
        self.get_current_llm = get_current_llm_fn
        self.ask_modal_fn = ask_modal_fn
        self.status_cb = status_cb

        self.session = BrowserSession(base_dir, get_current_llm_fn,
                                      status_cb=status_cb)
        self.stop_event = self.session.stop_event
        self.pause_event = self.session.pause_event

        self.minimize_window = True
        self.current_task = None
        # Lưu transaction đang chờ, để wait_for_llm_response ghép đúng.
        self._pending = {}
        # LLM nào đang chạy cho tab nào. ui/app.py gán None vào đây mỗi lần
        # bấm chạy; get_task_tab đọc để biết có phải đổi provider không.
        # Thiếu dict này thì Bước 2 chết ngay với
        # "object has no attribute 'active_run_llm'".
        # Bản chạy thật dùng _task_page (browser_session) đã tự điều hướng
        # lại khi sai host, nên dict này chỉ để tương thích + reset trong app.
        self.active_run_llm = {"scene": None, "prompt": None}

    # ------------------------------------------------------------------ log
    def _diag(self, event, **fields):
        self.session.log(event, **fields)

    def _show(self, text, color="orange"):
        if self.status_cb:
            try:
                self.status_cb(text, color)
            except Exception:
                pass

    # ------------------------------------------------------------- stop/pause
    def check_stop(self, task_name=None):
        """Tương thích cũ: ném StopRun khi người dùng bấm Dừng."""
        self.session.check_stop(task_name)

    def _check_stop_flag(self):
        return self.stop_event.is_set()

    def sleep(self, seconds, task_name=None):
        return self.session.sleep(seconds, task_name)

    def keep_minimized(self):
        """Tương thích cũ: giữ cửa sổ Chrome ở chế độ thu nhỏ."""
        self.minimize_window = True

    # ------------------------------------------------------------- selectors
    def get_selectors(self, kind):
        return self.session.get_selectors(kind)

    # ------------------------------------------------------------------- tabs
    def get_task_tab(self, task_name, target_url, force_new=False):
        """Cũ trả về tab handle; mới trả về handle giả (task key) vì caller
        chỉ truyền lại cho wait_for_llm_response, không dùng driver."""
        # Đổi LLM giữa chừng -> tab cũ là của provider trước, không dán
        # nhầm. Giữ đúng logic bản Selenium: so active_run_llm với LLM hiện tại.
        current_llm = self.get_current_llm()
        if self.active_run_llm.get(task_name) not in (None, current_llm):
            force_new = True
        if force_new:
            self.session.close_task_tab(task_name)
        self.active_run_llm[task_name] = current_llm
        # Nút "Mở Chrome" của người dùng đi qua đây: PHẢI mở tab thật,
        # không thì bấm nút không thấy gì. Tab mở sẵn thì tái dùng.
        if task_name == "login_prep":
            from core.providers import get_adapter
            adapter = get_adapter(current_llm)
            self.session._run(lambda: self.session._task_page(task_name, adapter),
                              timeout=180)
        return task_name

    def close_task_tab(self, task_name):
        self.session.close_task_tab(task_name)

    def ensure_valid_tab(self, task_name, target_url):
        self.get_task_tab(task_name, target_url, force_new=True)
        return task_name

    def try_upload_file(self, task_name, file_path):
        """Đính kèm file cho provider (Bước 2 chế độ file).

        Provider không hỗ trợ thì trả False - caller tự xử lý, KHÔNG ném lỗi
        để làm chết Auto.
        """
        if not os.path.exists(file_path):
            self._diag("upload_missing_file", task=task_name)
            return False
        adapter = get_adapter(self.get_current_llm())
        if not adapter.supports_file:
            self._diag("upload_unsupported", provider=adapter.name, task=task_name)
            return False

        def _upload():
            page = self.session._task_page(task_name, adapter)
            for sel in adapter.file_upload:
                loc = page.locator(sel).first
                try:
                    if loc.count() > 0:
                        loc.set_input_files(file_path)
                        self._diag("upload_ok", provider=adapter.name, task=task_name)
                        return True
                except Exception as exc:
                    self._diag("upload_error", sel=sel, err=str(exc)[:120])
            return False

        try:
            return self.session._run(_upload, timeout=120)
        except Exception as exc:
            self._diag("upload_failed", err=str(exc)[:140])
            return False

    # ------------------------------------------------------ transaction: send
    def inject_text_and_send(self, task_name, target_url, text, timeout=180):
        """Dán + gửi. Trả (tab_key, baseline) đúng như bản cũ để caller dùng tiếp.

        Ném StepFailure nếu không có bằng chứng đã gửi. Không bao giờ trả về
        "đã gửi" một cách giả như bản cũ.
        """
        self.current_task = task_name
        self.keep_minimized()
        tx = self.session._next_tx(task_name)

        def _send():
            from core import browser_tx as TX
            adapter = get_adapter(self.get_current_llm())
            page, adapter, _state = TX.ensure_ready(self.session, task_name)
            self.session._dismiss_cookie(page)
            base_user, base_asst = TX.capture_baseline(self.session, page, adapter)
            TX.inject(self.session, task_name, page, adapter, text)
            outcome = TX.send(self.session, task_name, page, adapter, text,
                              base_user, base_asst, tx, attempts=3)
            if not outcome.confirmed:
                raise StepFailure(
                    f"{adapter.name}: đã bấm gửi nhưng không có bằng chứng "
                    f"(tx={tx}). Không coi là đã gửi.", "send_unconfirmed", True)
            self._diag("send_confirmed", task=task_name, tx=tx,
                       method=outcome.method, provider=adapter.name)
            return outcome

        outcome = self.session._run(_send, timeout=timeout + 180)
        self._pending[task_name] = outcome
        return task_name, outcome.base_assistant

    def wait_for_llm_response(self, task_name, target_url, tab_key,
                              timeout=180, baseline_count=None):
        """Chờ response mới rồi trả text (rỗng nếu không có).

        Giữ hợp đồng cũ: trả "" thay vì ném, để caller tự quyết định retry.
        Nhưng lỗi READY/login vẫn ném để vòng phục hồi xử lý đúng.
        """
        self.current_task = task_name
        self.keep_minimized()
        outcome = self._pending.get(task_name)
        if outcome is None:
            self._diag("wait_without_send", task=task_name)
            return ""
        try:
            def _wait():
                from core import browser_tx as TX
                adapter = get_adapter(self.get_current_llm())
                page = self.session._task_page(task_name, adapter)
                return TX.wait_response(self.session, task_name, page, adapter,
                                        outcome, timeout=timeout)
            return self.session._run(_wait, timeout=timeout + 120)
        finally:
            self._pending.pop(task_name, None)

    # ---------------------------------------------------------- manual action
    def wait_for_manual_recovery(self, timeout_secs=300):
        """Cũ: chờ user xử lý login/Cloudflare. Mới: chờ vô hạn nhưng ngắt được."""
        self.session.restore_browser()
        self._show("Đang chờ bạn đăng nhập/xác minh trong cửa sổ Chrome...", "orange")
        started = time.time()
        last_remind = started
        while not self.stop_event.is_set():
            try:
                state = self.session._run(self._probe_state, "manual", timeout=120)
            except Exception as exc:
                self._diag("manual_probe_error", err=str(exc)[:120])
                state = PS.UNKNOWN
            if state == PS.READY:
                self._diag("manual_recovered", secs=round(time.time() - started))
                self._show("Đã vào được trang chat. Tool chạy tiếp.", "green")
                return True
            if time.time() - last_remind > 60:
                self._show("Vẫn chờ bạn đăng nhập/xác minh trong Chrome...", "orange")
                last_remind = time.time()
            self.sleep(3)
        return False

    def _probe_state(self, task_name):
        """Đọc trạng thái trang. Được _run gọi TRÊN thread chủ sở hữu."""
        from core.providers import composer_selectors
        adapter = get_adapter(self.get_current_llm())
        page = self.session._task_page(task_name, adapter)
        _sel, composer = self.session._first_visible(
            page, composer_selectors(adapter))
        raw = self.session._page_text(page)
        return PS.classify_page(raw, page.url, composer is not None,
                                provider=adapter.name)

    def manual_open_login(self, task_name="login_prep"):
        """Mở tab chat để user đăng nhập thủ công."""
        adapter = get_adapter(self.get_current_llm())
        self.session.restore_browser()

        def _open():
            return bool(self.session._task_page(task_name, adapter))
        try:
            self.session._run(_open, timeout=180)
        except Exception as exc:
            self._diag("manual_open_login_error", err=str(exc)[:140])
            return False
        self._show(f"Đã mở {adapter.name}. Hãy đăng nhập xong bấm Dừng rồi chạy lại.",
                   "blue")
        return True

    # ------------------------------------------------------------ lifecycle
    def restart_chrome(self):
        """Đóng rồi mở lại. KHÔNG set stop_event (nếu không, Auto tự chết)."""
        self.session.restart_browser()

    def kill_chrome(self):
        """Dừng hẳn (dùng khi đóng app)."""
        self.session.kill_chrome()

    # ------------------------------------------------------------ diagnostics
    def _diag_current(self):
        return ""


# Tên cũ để các import hiện tại không phải đổi.
BrowserManager = BrowserManagerV3