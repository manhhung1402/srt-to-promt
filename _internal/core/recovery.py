# -*- coding: utf-8 -*-
"""
Vòng lặp phục hồi: chỗ ENFORCE quy tắc "tool không bao giờ tự dừng".

Nguyên tắc của tool:
  - Auto chỉ kết thúc khi MỌI folder đã xong, hoặc người dùng nhấn Dừng.
  - Bất kỳ lỗi nào (API, Web, browser, parser, login, Cloudflare, mạng)
    đều dẫn tới ĐỢI rồi THỬ LẠI đúng chỗ đang dở.
  - Không bao giờ bỏ folder. Không bao giờ chuyển bước khi bước trước chưa xong.
  - Không bao giờ tạo output giả.

Sự phân biệt then chốt: lỗi CẦN NGƯỜI (login/Cloudflare) không tốn lượt
thử nhưng cũng không dừng -- tool chờ vô hạn rồi tự chạy tiếp. Lỗi KỸ
THUẬT thì backoff tăng dần rồi thử lại. Lỗi VĨNH VIỄN (hết tiền, sai
model) vẫn thử, nhưng log nói rõ để sáng dậy nhìn thấy ngay.
"""
import time

from core.faults import (BrowserActionRequired, LLMApiError, ParserError,
                         StepFailure, StopRun, DeferFolder)
from core import checkpoint as CK

# Backoff: lần lỗi kỹ thuật liên tiếp -> chờ tăng dần, có trần.
# Trần là để máy không kẹt cứng 1 giờ không làm gì nếu lỗi vĩnh viễn.
BASE_DELAY = 5.0
MAX_DELAY = 300.0
# Ngưỡng đánh dấu "lỗi này có vẻ vĩnh viễn" -> báo log dữ hơn, KHÔNG dừng.
PERMANENT_HINT_AT = 8

# Lỗi kỹ thuật trình duyệt -> restart Chrome, giữ nguyên trạng thái Auto.
BROWSER_CATEGORIES = frozenset((
    "browser", "page_not_ready", "composer_missing", "injection",
    "send_unconfirmed", "no_response", "parser", "transaction",
))


class Recovery:
    """Đếm lỗi, quyết định chờ bao lâu, và có bao nhiêu việc đã bị "chờ người"."""

    def __init__(self, log=None, status=None, delay_base=BASE_DELAY,
                 delay_max=MAX_DELAY):
        self.log = log or (lambda *a, **k: None)
        self.status = status or (lambda *a, **k: None)
        # Lịch backoff là tham số để test chạy nhanh được; sản phẩm dùng mặc định.
        self.delay_base = delay_base
        self.delay_max = delay_max
        self.tech_failures = 0
        self.needs_human_failures = 0
        self.total_retries = 0
        self.last_wait_until = 0.0
        self.permanent_flags = []

    def reset_backoff(self):
        self.tech_failures = 0

    def _note(self, event, **fields):
        self.log(event, **fields)

    def wait_before_retry(self, exc, checkpoint=None, step=None, on_tick=None):
        """Chờ rồi trả về True để thử lại. Trả False khi phải dừng.

        Đây là hàm DUY NHẤT được phép quyết định độ dài nhịp nghỉ. Nó không
        bao giờ trả False vì lỗi -- chỉ vì người dùng bấm Dừng.
        """
        # 1) Người dùng bấm Dừng -> dừng. Đây là lý do duy nhất.
        if isinstance(exc, StopRun):
            self._note("stop_by_user", step=step)
            return False

        # 2) Cần người thao tác tay -> chờ vô hạn, KHÔNG tốn lượt thử.
        if isinstance(exc, BrowserActionRequired):
            self.needs_human_failures += 1
            self._note("awaiting_human", step=step, provider=exc.provider,
                       state=exc.state, reason=exc.reason[:120])
            self.status(f"⏸ Cần bạn: {exc.reason[:90]} — tool đang chờ", "orange")
            # False = người dùng bấm Dừng trong lúc chờ -> phải DỪNG,
            # tuyệt đối không thử lại (bản cũ bỏ qua mất lệnh Dừng).
            return self._await_human(checkpoint, step, exc, on_tick)

        # 3) Lỗi API hết credit / sai key -> vẫn thử, nhưng báo to.
        category = ""
        retryable = True
        if isinstance(exc, LLMApiError):
            category, retryable = exc.category, exc.category not in (
                "credit", "auth", "missing")
        elif isinstance(exc, StepFailure):
            category, retryable = exc.category, exc.retryable
        elif isinstance(exc, ParserError):
            category = "parser"

        # 4) Lỗi kỹ thuật -> backoff tăng dần.
        self.tech_failures += 1
        self.total_retries += 1
        if self.tech_failures >= PERMANENT_HINT_AT and category in (
                "credit", "auth", "missing"):
            if category not in self.permanent_flags:
                self.permanent_flags.append(category)
                self._note("permanent_looking_error", category=category,
                           hint="vẫn tiếp tục thử, không tự dừng")
        delay = min(self.delay_max,
                    self.delay_base * (2 ** min(self.tech_failures - 1, 6)))

        if category in BROWSER_CATEGORIES:
            self._note("browser_error_restart", step=step, category=category)
            on_tick("restart_browser") if on_tick else None

        self._note("retry_wait", step=step, category=category or "unknown",
                   fail_no=self.tech_failures, wait=round(delay),
                   error=str(exc)[:140])
        self.status(f"↻ Lỗi ({category or 'unknown'}), thử lại sau "
                    f"{int(delay)}s — lần {self.tech_failures}", "red")
        return self._sleep_interruptible(delay, on_tick)

    def _await_human(self, checkpoint, step, exc, on_tick):
        """Chờ người dùng xử lý login/Cloudflare.

        Trả True = nên thử lại (đã qua xong hoặc chưa ai bấm Dừng).
        Trả False = người dùng bấm Dừng -> kết thúc, KHÔNG thử lại.
        """
        self.status(f"⏸ {exc.reason[:100]} — tool chờ, tự chạy tiếp khi xong", "orange")
        last_log = 0.0
        while True:
            if on_tick:
                # tick() trả False nghĩa là người dùng bấm Dừng.
                if on_tick("await_human", exc) is False:
                    self._note("awaiting_human_stopped_by_user", step=step)
                    return False
            time.sleep(1)
            now = time.time()
            if now - last_log > 300:      # nhắc lại mỗi 5 phút
                last_log = now
                self._note("awaiting_human_still", step=step,
                           waited_sec=int(now - self.last_wait_until))
            self.last_wait_until = now

    @staticmethod
    def _sleep_interruptible(delay, on_tick):
        """Ngủ có thể ngắt. Trả False nếu bị ngắt bởi Dừng."""
        deadline = time.time() + delay
        while time.time() < deadline:
            if on_tick:
                alive = on_tick("wait")
                if alive is False:
                    return False
            time.sleep(min(1.0, max(0.0, deadline - time.time())))
        return True


def run_step_with_recovery(step_fn, name, recovery, checkpoint=None, step=None,
                          on_tick=None, max_attempts=None, defer_after=None):
    """Chạy 1 bước, thử lại cho tới khi thành công, người dùng Dừng, hoặc nhường.

    max_attempts=None là chế độ đêm: không giới hạn số lần thử.
    defer_after=N thì sau N lần thử liên tiếp thì ném DeferFolder để folder
    khác có cơ hội chạy; tổng số lần thử vẫn vô hạn qua các vòng.
    """
    attempts = 0
    while True:
        attempts += 1
        if max_attempts and attempts > max_attempts:
            raise StepFailure(
                f"{name}: vượt số lần thử cho phép ({max_attempts}). "
                "Giữ nguyên trạng thái, không đánh dấu hoàn thành.",
                "max_attempts", True)
        if defer_after and attempts > defer_after:
            raise DeferFolder(
                f"{name}: đã thử {attempts} lần, nhường chỗ cho folder khác. "
                "Sẽ thử lại ở vòng sau.")
        try:
            if checkpoint and step:
                checkpoint.mark_running(step)
            result = step_fn()
            recovery.reset_backoff()
            if checkpoint and step:
                checkpoint.mark_done(step)
            recovery._note("step_ok", step=name, attempts=attempts)
            return result
        except (StopRun, DeferFolder, KeyboardInterrupt):
            raise
        except BaseException as exc:      # KHÔNG được lọc bớt ở đây
            if checkpoint and step:
                checkpoint.mark_failed(step, exc, _category_of(exc))
            if not recovery.wait_before_retry(exc, checkpoint, step, on_tick):
                raise StopRun()


def _category_of(exc):
    for attr in ("category",):
        value = getattr(exc, attr, "")
        if value:
            return str(value)
    if isinstance(exc, BrowserActionRequired):
        return "browser_action"
    if isinstance(exc, LLMApiError):
        return "api"
    if isinstance(exc, ParserError):
        return "parser"
    if isinstance(exc, StepFailure):
        return "step"
    return type(exc).__name__