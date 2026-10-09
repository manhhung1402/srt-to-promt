# -*- coding: utf-8 -*-
"""
Tầng trình duyệt: mỗi lần gọi Web LLM là MỘT TRANSACTION hoàn chỉnh.

    READY -> INJECT -> SEND CONFIRMED -> NEW RESPONSE -> VALIDATE

Chỉ khi cả chuỗi đó thành công, lớp trên mới được báo "xong". Bất kỳ
bước nào thất bại đều ném lỗi có phân loại, để vòng lặp Auto biết phải
chờ người dùng hay tự thử lại.

Điểm khác biệt cốt lõi so với bản cũ (Selenium):
  - Playwright chạy trên MỘT thread chủ sở hữu duy nhất. Các thread khác
    gửi lệnh qua hàng đợi và chờ Future. Không ai chạm Page từ thread lạ.
  - READY phải qua kiểm tra state machine, không phải "thấy textarea".
  - Send phải có BẰNG CHỨNG (lượt user mới xuất hiện), không phải "click xong".
  - Response phải là lượt MỚI sau baseline, không phải "phần tử cuối".
  - wait nào cũng ngắt được bằng nút Dừng.
"""
import os
import queue
import threading
import time

from core.faults import (BrowserActionRequired, StopRun, StepFailure,
                         looks_like, LOGIN_TEXTS)
from core import page_state as PS
from core.providers import (get_adapter, host_matches, composer_selectors,
                            send_selectors, generating_selectors,
                            assistant_selectors, user_selectors)

# Ngưỡng: sau thời gian này thì báo "tôi đang chờ" mỗi vòng (log, không spam).
RECOVERY_TICK = 60.0
# Số lần thử lại giới hạn cho lỗi KỸ THUẬT (không phải lỗi người dùng).
TECHNICAL_RETRIES = 4


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


class BrowserSession:
    """Facade đồng bộ cho lớp trên. Mọi việc trình duyệt chạy trên thread riêng."""

    def __init__(self, base_dir, get_llm_fn, status_cb=None, profile_dir="chrome_profile_tool",
                 headless=False):
        self.base_dir = base_dir
        self.get_llm = get_llm_fn
        self.status_cb = status_cb
        self.profile_dir = os.path.join(base_dir, profile_dir)
        self.log_path = os.path.join(base_dir, "browser_debug.log")
        self.headless = headless

        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.minimize = True

        self._tasks = {}
        self._lock = threading.RLock()
        self._cmd_q = queue.Queue()
        self._owner = None
        self._owner_ready = threading.Event()
        self._tx_counter = 0

    # -------------------------------------------------------------- logging
    def log(self, event, **fields):
        """Ghi chẩn đoán. TUYỆT ĐỐI không ghi prompt/API key/cookie (spec 20/42)."""
        parts = [f"[{_now()}]", str(event)]
        for key, value in fields.items():
            if value is None:
                continue
            text = str(value).replace("\n", " ").replace("\r", " ")[:200]
            parts.append(f"{key}={text}")
        line = " | ".join(parts)
        try:
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            pass
        print(line, flush=True)

    def status(self, text, color="orange"):
        if self.status_cb:
            try:
                self.status_cb(text, color)
            except Exception:
                pass

    # -------------------------------------------------------------- stop/pause
    def check_stop(self, task=None):
        """Chặn cho tới khi hết pause, ném StopRun khi người dùng bấm Dừng."""
        while self.pause_event.is_set():
            if self.stop_event.is_set():
                break
            time.sleep(0.2)
        if self.stop_event.is_set():
            raise StopRun()

    def sleep(self, seconds, task=None):
        """Ngủ có thể ngắt bằng Dừng. Trả False nếu bị ngắt."""
        deadline = time.time() + max(0.0, seconds)
        while time.time() < deadline:
            self.check_stop(task)
            time.sleep(min(0.2, max(0.0, deadline - time.time())))
        return not self.stop_event.is_set()

    def _sleep_on_owner(self, seconds):
        """Ngủ trong thread chủ sở hữu, vẫn ngắt được."""
        deadline = time.time() + max(0.0, seconds)
        while time.time() < deadline:
            if self.stop_event.is_set():
                raise StopRun()
            time.sleep(min(0.15, max(0.0, deadline - time.time())))

    # -------------------------------------------------------------- owner thread
    def _ensure_owner(self):
        """Bắt đầu thread chủ sở hữu Playwright nếu chưa có.

        Thất bại lúc khởi động KHÔNG được biến thành "hỏng vĩnh viễn": thread
        cũ bị giữ nguyên sau lỗi đầu tiên, nên một lần Playwright không khởi
        động được sẽ chết tool vĩnh viễn. Ở đây mọi lần gọi đều thử lại từ
        đầu, nên lần sau có thể thành công.
        """
        with self._lock:
            if self._owner and self._owner.is_alive():
                return
            self._owner_ready.clear()
            self._cmd_q = queue.Queue()
            self._owner = threading.Thread(target=self._owner_loop,
                                           name="browser-owner", daemon=True)
            self._owner.start()
        if not self._owner_ready.wait(30):
            raise StepFailure("Không khởi động được thread trình duyệt.",
                              "browser", True)

    def _owner_loop(self):
        from playwright.sync_api import sync_playwright
        pw = None
        try:
            pw = sync_playwright().start()
        except Exception as exc:
            # Ghi log rồi thoát; KHÔNG set cờ "hỏng vĩnh viễn".
            self.log("playwright_start_failed", err=f"{type(exc).__name__}: {exc}")
            self._owner_ready.set()
            return
        self._playwright = pw
        self._ctx = None
        self._pages = {}
        self._page_provider = {}
        self._owner_ready.set()
        self.log("browser_thread_started")
        while True:
            item = self._cmd_q.get()
            if item is None:
                break
            fn, args, result = item
            try:
                result.put((True, fn(*args)))
            except BaseException as exc:      # StopRun cũng phải trả về, không nuốt
                result.put((False, exc))
        try:
            if self._ctx:
                self._ctx.close()
        except Exception:
            pass
        try:
            self._playwright.stop()
        except Exception:
            pass
        self.log("browser_thread_stopped")

    def _run(self, fn, *args, timeout=600):
        """Gửi lệnh tới thread chủ sở hữu và chờ kết quả (Future-like)."""
        self._ensure_owner()
        result = queue.Queue(maxsize=1)
        self._cmd_q.put((fn, args, result))
        try:
            ok, value = result.get(timeout=timeout)
        except queue.Empty:
            raise StepFailure("Lệnh trình duyệt quá thời gian cho phép.",
                              "browser", True)
        if ok:
            return value
        raise value

    def _next_tx(self, task):
        with self._lock:
            self._tx_counter += 1
            return f"{task}-{self._tx_counter:06d}"

    # -------------------------------------------------------------- public API
    def get_selectors(self, kind):
        """Tương thích với code cũ (scene_grouper hỏi file_upload_input)."""
        adapter = get_adapter(self.get_llm())
        if kind == "file_upload_input":
            return list(adapter.file_upload)
        if kind == "input":
            return composer_selectors(adapter)
        if kind == "send_button":
            return send_selectors(adapter)
        if kind == "stop_button":
            return generating_selectors(adapter)
        if kind == "response_block":
            return assistant_selectors(adapter)
        return []

    def kill_chrome(self):
        """Dừng hẳn trình duyệt (dùng khi thoát app)."""
        self.stop_event.set()
        self.pause_event.clear()
        try:
            self._run(self._do_kill, timeout=60)
        except Exception:
            pass

    def restart_browser(self):
        """Đóng rồi mở lại, GIỮ profile trên đĩa, KHÔNG set stop_event.

        Quan trọng: retry trình duyệt không được tự biến thành Dừng Auto
        (nguyên tắc: chỉ người dùng mới dừng được).
        """
        self.log("browser_restart_begin")
        try:
            self._run(self._do_kill, timeout=90)
        except Exception as exc:
            self.log("browser_restart_kill_error", err=str(exc)[:150])
        self.log("browser_restart_done")

    def close_task_tab(self, task):
        try:
            self._run(lambda t=task: self._do_close_tab(t), timeout=90)
        except Exception as exc:
            self.log("tab_close_error", task=task, err=str(exc)[:150])

    # -------------------------------------------------------------- ask_llm
    def ask_llm(self, task, payload, timeout=180, send_timeout=20):
        """Một lần gọi Web LLM = MỘT transaction trọn vẹn.

        Trả về (text, meta) khi PASS đủ 5 điều kiện. Ném
        BrowserActionRequired nếu cần người dùng, StepFailure nếu lỗi kỹ thuật.
        Cả hai đều KHÔNG kết thúc Auto.
        """
        tx = self._next_tx(task)
        deadline = timeout + 180          # READY + inject + send + response
        return self._run(self._do_ask_llm, task, payload, tx, timeout, send_timeout,
                         timeout=deadline)

    def _do_ask_llm(self, task, payload, tx, timeout, send_timeout):
        """Chạy TRÊN thread chủ sở hữu. Không được gọi trực tiếp từ nơi khác."""
        from core import browser_tx as TX
        adapter = get_adapter(self.get_llm())
        t0 = time.time()
        self.log("transaction_begin", task=task, tx=tx, provider=adapter.name,
                 payload_len=len(payload))

        page, adapter, state = TX.ensure_ready(self, task)
        self._dismiss_cookie(page)
        base_user, base_asst = TX.capture_baseline(self, page, adapter)
        TX.inject(self, task, page, adapter, payload)

        outcome = TX.send(self, task, page, adapter, payload, base_user,
                          base_asst, tx, attempts=3)
        if not outcome.confirmed:
            raise StepFailure(
                f"{adapter.name}: click gửi xong nhưng KHÔNG có bằng chứng "
                f"(tx={tx}). Không coi là đã gửi.", "send_unconfirmed", True)

        text = TX.wait_response(self, task, page, adapter, outcome, timeout=timeout)
        meta = {"tx": tx, "provider": adapter.name, "wait": round(time.time() - t0, 1),
                "method": outcome.method}
        self.log("transaction_pass", task=task, tx=tx, provider=adapter.name,
                 seconds=meta["wait"], response_len=len(text))
        return text, meta

    def restore_browser(self):
        """Đưa tab cần người dùng thao tác lên trước mặt."""
        try:
            self._run(self._do_bring_to_front, timeout=30)
        except Exception as exc:
            self.log("bring_to_front_error", err=str(exc)[:120])

    def _do_bring_to_front(self):
        for page in list(getattr(self, "_pages", {}).values()):
            try:
                page.bring_to_front()
            except Exception:
                continue

    # ================================================================ internals
    def _do_kill(self):
        ctx = getattr(self, "_ctx", None)
        self._ctx = None
        self._pages = {}
        self._page_provider = {}
        if ctx is not None:
            try:
                ctx.close()          # chỉ đóng context của TOOL, không đụng Chrome của user
            except Exception:
                pass
        self.log("browser_closed")

    def _do_close_tab(self, task):
        page = self._pages.pop(task, None)
        if page is None:
            return False
        self._page_provider.pop(task, None)
        try:
            page.close()
            self.log("tab_closed", task=task)
            return True
        except Exception:
            return False

    def _ensure_context(self):
        if self._ctx is not None:
            return self._ctx
        os.makedirs(self.profile_dir, exist_ok=True)
        browser_path = None
        for cand in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                     r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                     r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"):
            if os.path.exists(cand):
                browser_path = cand
                break
        kwargs = dict(headless=self.headless, args=[
            "--disable-blink-features=AutomationControlled",
            "--no-first-run", "--no-default-browser-check",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
        ])
        if browser_path:
            kwargs["executable_path"] = browser_path
        self._ctx = self._playwright.chromium.launch_persistent_context(
            self.profile_dir, **kwargs)
        self.log("browser_started", profile=self.profile_dir,
                 browser=os.path.basename(browser_path) if browser_path else "bundled")
        return self._ctx

    def _task_page(self, task, adapter):
        """Lấy tab đúng provider cho task. Không bao giờ dùng lại tab sai host."""
        ctx = self._ensure_context()
        page = self._pages.get(task)
        if page is not None:
            try:
                if page.is_closed():
                    raise RuntimeError("closed")
                if host_matches(page.url, adapter):
                    self.log("tab_reused", task=task, provider=adapter.name)
                    return page
                # Sai provider -> điều hướng lại, KHÔNG tạo tab mới
                self.log("tab_wrong_provider", task=task, provider=adapter.name,
                         current=page.url[:80])
                page.goto(adapter.url, wait_until="domcontentloaded", timeout=60000)
                return page
            except Exception:
                pass
        page = ctx.new_page()
        page.set_default_timeout(30000)
        page.goto(adapter.url, wait_until="domcontentloaded", timeout=90000)
        self._pages[task] = page
        self._page_provider[task] = adapter.name
        self.log("tab_opened", task=task, provider=adapter.name, url=adapter.url)
        return page

    @staticmethod
    def _first_visible(page, selectors, timeout_ms=0):
        """Trả về (selector, locator) của phần tử nhìn thấy được đầu tiên.

        Luôn trả về CHÚNG, không giữ tham chiếu cũ -> không bao giờ stale.
        """
        for sel in selectors:
            loc = page.locator(sel).last
            try:
                if loc.count() > 0 and loc.is_visible():
                    return sel, loc
            except Exception:
                continue
        return None, None

    @staticmethod
    def _count(page, selectors):
        """Đếm phần tử khớp BẤT KỲ selector nào (dùng làm baseline)."""
        total = 0
        for sel in selectors:
            try:
                total += page.locator(sel).count()
            except Exception:
                continue
        return total

    def _page_text(self, page):
        try:
            return (page.inner_text("body") or "")[:20000]
        except Exception:
            return ""

    def _composer_length(self, loc):
        """ĐỌC textContent/value thật, KHÔNG dùng innerText.

        innerText trả phần ĐANG HIỂN THỊ (bị cắt ngắn), còn payload thật có thể
        hàng trăm KB. Dùng innerText làm ngưỡng kiểm tra sẽ luôn pass -> tool tưởng
        đã gửi xong. Đây là một nguyên nhân gốc của "tưởng đã gửi nhưng chưa gửi".
        """
        try:
            return loc.evaluate(
                "el => (el.textContent || el.value || '').trim().length")
        except Exception:
            return 0

    def _set_composer(self, loc, text):
        """Điền text vào composer (fill cho textarea, insertText cho contenteditable)."""
        tag = ""
        try:
            tag = (loc.evaluate("el => el.tagName") or "").lower()
        except Exception:
            pass
        if tag == "textarea":
            loc.fill(text, timeout=30000)
        elif tag == "input":
            loc.fill(text, timeout=30000)
        else:
            # contenteditable: click để có focus rồi chèn, tránh mất phím bị nuốt.
            try:
                loc.click(timeout=10000)
            except Exception:
                pass
            self._clear(loc)
            loc.evaluate(
                "(el, t) => { const d = document.createRange();"
                " d.selectNodeContents(el); d.deleteContents();"
                " const s = document.createTextNode(t);"
                " d.insertNode(s); el.dispatchEvent(new Event('input',{bubbles:true})); }",
                text)

    def _clear(self, loc):
        try:
            tag = (loc.evaluate("el => el.tagName") or "").lower()
            if tag in ("textarea", "input"):
                loc.fill("", timeout=15000)
            else:
                loc.evaluate("(el) => { const d = document.createRange();"
                             " d.selectNodeContents(el); d.deleteContents();"
                             " el.dispatchEvent(new Event('input',{bubbles:true})); }")
        except Exception:
            pass

    def _dismiss_cookie(self, page):
        """Đóng popup cookie/consent CHẤP NHẬN có tên rõ ràng (spec 45).

        Cố ý KHÔNG bấm mọi nút có chữ "OK" -> dễ bấm nhầm nút nguy hiểm.
        """
        labels = ("Accept all", "Accept All", "Accept", "Got it", "I agree",
                  "Đồng ý", "Chấp nhận tất cả", "Chấp nhận")
        for label in labels:
            try:
                btn = page.get_by_role("button", name=label, exact=True).first
                if btn.count() > 0 and btn.is_visible():
                    btn.click(timeout=5000)
                    self.log("cookie_dismissed", label=label)
                    return True
            except Exception:
                continue
        return False