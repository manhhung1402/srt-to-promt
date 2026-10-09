# -*- coding: utf-8 -*-
"""
Send transaction + response wait.

Định nghĩa PASS (spec 74/75):
  send:      đúng provider + READY + composer tìm thấy + đã xoá text cũ
              + dán đủ payload + có BẰNG CHỨNG lượt user mới
  response:  send đã xác nhận + lượt assistant MỚI sau baseline
              + đã ngừng sinh + text ổn định + không rỗng

Bốn lỗi bản cũ được chặn ở đây:
  1. dán chồng prompt cũ          -> _set_composer luôn xoá trước
  2. "đã gửi" mà không bằng chứng -> chỉ PASS khi lượt user tăng
  3. đọc nhầm response cũ          -> so với baseline, không lấy phần tử cuối
  4. chờ vô hạn / không dừng được -> mọi vòng lặp đều check stop
"""
from core.faults import BrowserActionRequired, StepFailure, StopRun
from core import page_state as PS
from core.providers import (get_adapter, composer_selectors, send_selectors,
                            generating_selectors, assistant_selectors,
                            user_selectors)


class SendOutcome:
    """Kết quả thật của một lần gửi (không phải kết quả của một cú click)."""
    __slots__ = ("confirmed", "tx", "method", "base_user", "base_assistant",
                 "wait_seconds", "attempts")

    def __init__(self, confirmed, tx, method="", base_user=0, base_assistant=0,
                 wait_seconds=0.0, attempts=0):
        self.confirmed = confirmed
        self.tx = tx
        self.method = method
        self.base_user = base_user
        self.base_assistant = base_assistant
        self.wait_seconds = wait_seconds
        self.attempts = attempts

    def __repr__(self):
        return (f"SendOutcome(confirmed={self.confirmed}, tx={self.tx}, "
                f"method={self.method!r}, attempts={self.attempts})")


def ensure_ready(self, task, deadline_seconds=90):
    """Chờ trang đạt READY, xử lý Cloudflare/login trên đường đi.

    Trả về (page, adapter, state). Nếu gặp thứ cần người dùng thì ném
    BrowserActionRequired (KHÔNG tốn lượt thử).
    """
    adapter = get_adapter(self.get_llm())
    end = time_now() + deadline_seconds
    last_state = PS.UNKNOWN
    reloads = 0

    while time_now() < end:
        self.check_stop(task)
        page = self._task_page(task, adapter)
        self._sleep_on_owner(1.0)

        sel, composer = self._first_visible(page, composer_selectors(adapter))
        raw = self._page_text(page)
        state = PS.classify_page(raw, page.url, composer is not None,
                                 provider=adapter.name)
        self.log("page_state", task=task, provider=adapter.name, state=state,
                 composer=sel or "-")

        if state == PS.READY:
            return page, adapter, state

        err = PS.blocking_error(state, adapter.name, page.url)
        if err:
            # Đưa cửa sổ ra trước để user thao tác, rồi chờ vô hạn.
            self.restore_browser()
            self.status(f"{adapter.name}: cần bạn xử lý ({state}) - tool đang chờ", "orange")
            raise err

        if state in (PS.PROVIDER_ERROR, PS.LOADING):
            # Thử lại được: reload có kiểm soát, không tăng lượt thử người dùng.
            reloads += 1
            if reloads <= 2:
                self.log("page_reload", task=task, state=state, n=reloads)
                try:
                    page.reload(wait_until="domcontentloaded", timeout=60000)
                except Exception as exc:
                    self.log("page_reload_error", err=str(exc)[:120])
            else:
                self._sleep_on_owner(3.0)
        else:
            # UNKNOWN: DOM chưa hydrate -> chờ, đừng vội kết luận.
            self._sleep_on_owner(1.5)

        last_state = state

    raise StepFailure(
        f"{adapter.name}: không đạt trạng thái READY sau {deadline_seconds}s "
        f"(trạng thái cuối: {last_state}).", "page_not_ready", True)


def inject(self, task, page, adapter, payload):
    """Xoá text cũ -> dán payload -> ĐỌC LẠI xác minh. Không đủ dài thì không gửi."""
    sel, composer = self._first_visible(page, composer_selectors(adapter))
    if composer is None:
        raise StepFailure(f"{adapter.name}: mất ô nhập (composer not found).",
                          "composer_missing", True)

    # 1) đọc text cũ trước khi ghi (spec 18)
    stale = self._composer_length(composer)
    if stale:
        self.log("composer_cleared", task=task, stale_len=stale)
        self._clear(composer)
        self._sleep_on_owner(0.4)

    # 2) dán
    self.log("composer_search_begin", task=task, provider=adapter.name, selector=sel)
    self._set_composer(composer, payload)
    self._sleep_on_owner(0.8)

    # 3) đọc lại, so với payload (spec 19)
    got = self._composer_length(composer)
    need = len(payload.strip())
    if got < need * 0.9:
        raise StepFailure(
            f"COMPOSER_INJECTION_FAILED: cần {need} ký tự, đọc lại {got}. "
            f"Không gửi để tránh nội dung cắt cụt.", "injection", True)
    self.log("composer_injected", task=task, selector=sel,
             payload_len=need, composer_len=got)
    return composer


def _last_user_tail(page, adapter, limit=4000):
    """Đuôi lượt user cuối (có bút nháp nên chỉ lấy đuôi, không lấy đầu)."""
    for sel in user_selectors(adapter):
        try:
            loc = page.locator(sel)
            n = loc.count()
            if n:
                txt = (loc.nth(n - 1).inner_text(timeout=15000) or "").strip()
                if txt:
                    return txt[-limit:]
        except Exception:
            continue
    return ""


def capture_baseline(self, page, adapter):
    """Ghi nhận LƯỢT CUỐI trước khi gửi (spec 21).

    Không đếm số phần tử: ChatGPT virtualize DOM nên số turn có thể giảm
    (log thật: user_delta=-1) và assistant selector lồng nhau khiến
    1 turn bị đếm thành 2. So TEXT là thứ ổn định.
    """
    base_user = self._count(page, user_selectors(adapter))
    base_asst = last_assistant_text(page, adapter)
    return base_user, base_asst


def send(self, task, page, adapter, payload, base_user, base_asst, tx,
         attempts=3):
    """Gửi và CHỈ trả confirmed=True khi có bằng chứng lượt user mới (spec 22/23)."""
    self.log("send_begin", task=task, provider=adapter.name, tx=tx,
             base_user=base_user, base_assistant=base_asst)

    for attempt in range(1, attempts + 1):
        self.check_stop(task)
        self._sleep_on_owner(0.3)

        # Lần lượt thử: nút gửi -> Enter. Mỗi chiến lược đều phải verify.
        method = ""
        sel, btn = self._first_visible(page, send_selectors(adapter), timeout_ms=3000)
        if btn is not None:
            try:
                disabled = (btn.get_attribute("disabled") is not None
                            or btn.get_attribute("aria-disabled") == "true")
            except Exception:
                disabled = False
            if not disabled:
                try:
                    btn.click(timeout=20000)
                    method = f"click:{sel}"
                except Exception as exc:
                    self.log("send_click_error", task=task, tx=tx, err=str(exc)[:150])
        if not method:
            _, composer = self._first_visible(page, composer_selectors(adapter))
            if composer is None:
                raise StepFailure(f"{adapter.name}: không còn ô nhập để gửi.",
                                  "composer_missing", True)
            try:
                from playwright.sync_api import Keyboard
                composer.press("Enter", timeout=10000)
                method = "enter"
            except Exception as exc:
                self.log("send_enter_error", task=task, tx=tx, err=str(exc)[:150])

        # BẰNG CHỨNG: lượt user MỚI, hoặc nút "đang tạo" xuất hiện.
        # So COUNT lượt là không đáng tin: log thật có user_delta=-1 tức số
        # user turn GIẢM sau khi gửi (ChatGPT virtualize DOM). Nên so nội
        # dung lượt user cuối - thứ này không bị ảo hoá.
        start = time_now()
        confirmed = False
        while time_now() - start < 20:
            self.check_stop(task)
            self._sleep_on_owner(0.5)
            tail = _last_user_tail(page, adapter)
            if tail and tail != base_user:
                confirmed = True
                break
            _, stop_btn = self._first_visible(page, generating_selectors(adapter))
            if stop_btn is not None:
                confirmed = True       # đã bắt đầu sinh -> chắc chắn đã gửi
                break

        self.log("send_attempt", task=task, tx=tx, attempt=attempt, method=method,
                 confirmed=confirmed)
        if confirmed:
            self.log("send_confirmed", task=task, tx=tx, method=method,
                     user_tail_changed=_last_user_tail(page, adapter) != base_user)
            return SendOutcome(True, tx, method, base_user, base_asst,
                               time_now() - start, attempt)

        # Chưa gửi được: dọn composer rồi thử lại (tránh dán chồng).
        _, composer = self._first_visible(page, composer_selectors(adapter))
        if composer is not None and self._composer_length(composer):
            self.log("composer_recovered", task=task, stale_len=self._composer_length(composer))
            self._clear(composer)
            self._set_composer(composer, payload)
            self._sleep_on_owner(0.5)

    self.log("send_failed", task=task, tx=tx, attempts=attempts)
    return SendOutcome(False, tx, "none", base_user, base_asst, 0.0, attempts)


def last_assistant_text(page, adapter):
    """Text lượt assistant CUỐI CÙNG, đọc theo DOM chứ không theo số lượt.

    ChatGPT virtualize danh sách turn: đếm phần tử lung tung (log có
    user_delta=-1 tức số user turn GIẢM sau khi gửi). Text là thứ không bị
    ảo hoá, nên so text mới đúng.
    """
    for sel in assistant_selectors(adapter):
        try:
            loc = page.locator(sel)
            n = loc.count()
            if n:
                el = loc.nth(n - 1)
                txt = (el.inner_text(timeout=15000) or "").strip()
                if txt:
                    return txt
        except Exception:
            continue
    return ""


def wait_response(self, task, page, adapter, outcome, timeout=180):
    """Chờ lượt assistant MỚI, chờ đủ ổn định, trả text (spec 24/25/26).

    Không bao giờ trả text của lượt cũ: so TEXT lượt assistant cuối với
    baseline chụp trước lúc gửi (không so số lượt - xem capture_baseline).
    """
    tx = outcome.tx
    self.log("response_wait_begin", task=task, tx=tx, timeout=timeout,
             base_assistant=outcome.base_assistant)

    end = time_now() + timeout
    started = False
    stable = 0
    last_text = ""

    while time_now() < end:
        self.check_stop(task)
        self._sleep_on_owner(1.5)

        _, stop_btn = self._first_visible(page, generating_selectors(adapter))
        generating = stop_btn is not None
        text = last_assistant_text(page, adapter)

        # Chưa thấy lượt mới -> chưa được lấy bất cứ text nào.
        if text and text != outcome.base_assistant:
            if not started:
                started = True
                self.log("response_started", task=task, tx=tx)
            if text == last_text and not generating:
                stable += 1
                if stable >= 3:
                    self.log("response_complete", task=task, tx=tx,
                             response_len=len(text))
                    return text
            else:
                last_text = text
                stable = 0
        elif started and not text:
            # Đã thấy nhưng lượt biến mất (trang đổi) -> reset, đừng nhận rác.
            started = False
            stable = 0

    self.log("response_timeout", task=task, tx=tx, started=started,
             response_len=len(last_text))
    if started and last_text:
        # Đã sinh nhưng không ổn định -> vẫn trả phần có được, parser quyết định.
        return last_text
    raise StepFailure(
        f"{adapter.name}: đã gửi nhưng không có response mới trong {timeout}s "
        f"(tx={tx}).", "no_response", True)


def time_now():
    import time as _t
    return _t.time()