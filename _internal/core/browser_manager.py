# -*- coding: utf-8 -*-
"""
Quản lý trình duyệt Chrome Selenium và thao tác giao diện Web LLM
(Selenium Chrome manager and Web LLM interaction)
"""
import os
import json
import time
import subprocess
import threading
from urllib.parse import urlparse
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import NoSuchWindowException, WebDriverException
from webdriver_manager.chrome import ChromeDriverManager

from config.settings import LLM_PROFILES, FALLBACK_SELECTORS, LLM_URLS
from utils.exceptions import StopRun, BrowserActionRequired

class BrowserManager:
    """Quản lý driver Chrome và các tác vụ tương tác Web LLM an toàn luồng.
    (Manages Chrome driver and thread-safe Web LLM operations.)"""

    def __init__(self, base_dir, get_current_llm_fn, ask_modal_fn):
        self.base_dir = base_dir
        self.get_current_llm = get_current_llm_fn
        self.ask_modal_fn = ask_modal_fn

        self.current_task = None          # "scene" hay "prompt": de moi buoc lay LLM rieng
        self.minimize_window = True       # giu Chrome o che do thu nho
        self.standalone_chrome_profile = os.path.join(self.base_dir, "chrome_profile_tool")
        os.makedirs(self.standalone_chrome_profile, exist_ok=True)

        self.driver = None
        self.driver_lock = threading.RLock()
        self.tab_handles = {"scene": None, "prompt": None}
        self.active_run_llm = {"scene": None, "prompt": None}
        self._last_input_selector = None
        self._browser_log_path = os.path.join(self.base_dir, "browser_debug.log")

        self.stop_event = threading.Event()
        self.pause_event = threading.Event()

    def _diag(self, event, **fields):
        """Ghi log chẩn đoán trình duyệt, không ghi nội dung prompt/credential."""
        try:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            parts = [f"[{stamp}]", str(event)]
            for key, value in fields.items():
                if value is None:
                    continue
                text = str(value).replace("\n", " ").replace("\r", " ")[:300]
                parts.append(f"{key}={text}")
            with open(self._browser_log_path, "a", encoding="utf-8") as handle:
                handle.write(" | ".join(parts) + "\n")
        except Exception:
            pass

    @staticmethod
    def _url_matches_target(current_url, target_url):
        """Chỉ tái sử dụng tab khi hostname vẫn đúng provider mong đợi."""
        try:
            current_host = (urlparse(current_url).hostname or "").lower()
            target_host = (urlparse(target_url).hostname or "").lower()
            if not current_host or not target_host:
                return False
            return current_host == target_host or current_host.endswith("." + target_host)
        except Exception:
            return False

    def _current_url_for_log(self):
        try:
            parsed = urlparse(self.driver.current_url)
            host = parsed.hostname or ""
            path = (parsed.path or "/")[:100]
            return f"{parsed.scheme}://{host}{path}"
        except Exception:
            return "unknown"

    def check_stop(self, task_name=None):
        """Kiểm tra lệnh dừng/tạm dừng; ném StopRun nếu người dùng bấm Dừng. (Checks stop/pause events.)"""
        while self.pause_event.is_set():
            if self.stop_event.is_set():
                break
            time.sleep(0.3)
        if self.stop_event.is_set():
            raise StopRun()

    def clean_chrome_profile_locks(self):
        """Xóa lock file thừa từ Chrome crash trước đó. (Remove stale Chrome profile locks.)"""
        lock_names = ["SingletonLock", "SingletonSocket", "SingletonCookie", "lockfile"]
        for root_dir, _, files in os.walk(self.standalone_chrome_profile):
            for f in files:
                if f in lock_names:
                    try:
                        os.remove(os.path.join(root_dir, f))
                    except Exception:
                        pass

    def patch_chrome_preferences(self):
        """Ghi đè exit_type trong Preferences để Chrome không báo khôi phụcsession. (Patch prefs to prevent crash recovery dialog.)"""
        pref_path = os.path.join(self.standalone_chrome_profile, "Default", "Preferences")
        if os.path.exists(pref_path):
            try:
                with open(pref_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if "profile" in data:
                    data["profile"]["exit_type"] = "Normal"
                    data["profile"]["exited_cleanly"] = True
                with open(pref_path, "w", encoding="utf-8") as f:
                    json.dump(data, f)
            except Exception:
                pass

    def get_or_create_driver(self):
        """Trả về driver Chrome đang sống, hoặc tạo mới. (Returns existing or new Chrome driver.)"""
        with self.driver_lock:
            if self.driver is not None:
                try:
                    _ = self.driver.window_handles
                    return self.driver
                except Exception:
                    self.driver = None

            self.clean_chrome_profile_locks()
            self.patch_chrome_preferences()

            options = webdriver.ChromeOptions()
            options.add_argument(f"--user-data-dir={self.standalone_chrome_profile}")
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option("useAutomationExtension", False)
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-background-timer-throttling")
            options.add_argument("--disable-backgrounding-occluded-windows")
            options.add_argument("--disable-renderer-backgrounding")
            options.add_argument("--disable-notifications")
            options.add_argument("--disable-popup-blocking")
            options.add_argument("--disable-infobars")
            # Chay an min: khong bung len canh man hinh giua chung
            options.add_argument("--start-minimized")
            options.add_argument("--no-first-run")
            options.add_argument("--no-default-browser-check")

            try:
                service = Service(ChromeDriverManager().install())
                self.driver = webdriver.Chrome(service=service, options=options)
            except Exception:
                try:
                    self.driver = webdriver.Chrome(options=options)
                except Exception as e:
                    raise Exception(
                        f"Không thể khởi động Chrome (Cannot start Chrome). Chi tiết: {e}\n\n"
                        "Hãy đảm bảo bạn đã đóng mọi cửa sổ Chrome của tool và thử lại."
                    )

            # Giói han cho wait de worker khong nam long driver-lock (bounded waits).
            try:
                self.driver.set_page_load_timeout(90)
                self.driver.set_script_timeout(30)
            except Exception:
                pass

            self.tab_handles = {"scene": None, "prompt": None}
            self._diag("driver_started")
            return self.driver

    def get_task_tab(self, task_name, target_url, force_new=False):
        """Lấy hoặc tạo tab cho task (scene/prompt). Tự mở trang LLM nếu cần.
        (Get or create a browser tab for a given task.)"""
        with self.driver_lock:
            current_llm = self.get_current_llm()
            if self.active_run_llm.get(task_name) not in (None, current_llm):
                force_new = True

            driver = self.get_or_create_driver()
            existing_handles = driver.window_handles
            saved_handle = self.tab_handles.get(task_name)

            if not force_new and saved_handle and saved_handle in existing_handles:
                driver.switch_to.window(saved_handle)
                if not self._url_matches_target(driver.current_url, target_url):
                    self._diag("tab_wrong_provider", task=task_name,
                               current=self._current_url_for_log(), target=target_url)
                    driver.get(target_url)
                self.active_run_llm[task_name] = current_llm
                return saved_handle

            if len(existing_handles) == 1:
                try:
                    driver.switch_to.window(existing_handles[0])
                    cur_u = driver.current_url.lower()
                    if cur_u in ("data:,", "about:blank", "chrome://newtab/") or "newtab" in cur_u:
                        driver.get(target_url)
                        self.tab_handles[task_name] = existing_handles[0]
                        self.active_run_llm[task_name] = current_llm
                        time.sleep(2)
                        self.dismiss_cookie_popups()
                        return existing_handles[0]
                except Exception:
                    pass

            driver.execute_script("window.open('about:blank', '_blank');")
            new_handle = driver.window_handles[-1]
            driver.switch_to.window(new_handle)
            driver.get(target_url)
            self.tab_handles[task_name] = new_handle
            self.active_run_llm[task_name] = current_llm
            time.sleep(2)
            self.dismiss_cookie_popups()
            return new_handle

    def keep_minimized(self):
        """Giu cua so Chrome o trang thai thu nho trong khi tool chay.
        (Keep the Chrome window minimised so it does not keep popping up.)"""
        if not self.minimize_window:
            return
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            handle = user32.FindWindowW(None, "Chrome")     # type: ignore[attr-defined]
            if not handle:
                handle = user32.FindWindowW(None, "Google Chrome")
            if handle:
                user32.ShowWindow(handle, 6)                 # SW_MINIMIZE
        except Exception:
            pass

    def close_task_tab(self, task_name):
        """Đóng tab vừa dùng cho task (scene/prompt) sau khi đã có kết quả.
        Giữ nguyên driver để lần sau mở nhanh hơn.
        (Close the task tab once its result has been received; keep the driver alive.)
        """
        with self.driver_lock:
            handle = self.tab_handles.get(task_name)
            self.tab_handles.pop(task_name, None)
            self.active_run_llm[task_name] = None
            try:
                driver = self.get_or_create_driver()
                handles = driver.window_handles
                if handle and handle in handles:
                    driver.switch_to.window(handle)
                    driver.close()
                    driver.switch_to.window(handles[0])
                return True
            except Exception:
                return False

    def ensure_valid_tab(self, task_name, target_url):
        """Đảm bảo tab đang hợp lệ (vẫn còn trên giao diện đúng LLM). (Ensure tab is still valid.)"""
        with self.driver_lock:
            driver = self.get_or_create_driver()
            handle = self.tab_handles.get(task_name)
            current_llm = self.get_current_llm()
            llm_matches = self.active_run_llm.get(task_name) in (None, current_llm)
            try:
                if llm_matches and handle and handle in driver.window_handles:
                    driver.switch_to.window(handle)
                    if self._url_matches_target(driver.current_url, target_url):
                        return handle
                    self._diag("cached_tab_wrong_provider", task=task_name,
                               current=self._current_url_for_log(), target=target_url)
                    driver.get(target_url)
                    self.active_run_llm[task_name] = current_llm
                    return handle
            except (NoSuchWindowException, WebDriverException):
                pass
            return self.get_task_tab(task_name, target_url, force_new=True)

    def get_selectors(self, kind):
        """Trộn selector priority (LLM profile + fallback). (Merge LLM profile + fallback selectors.)"""
        llm_name = self.get_current_llm()
        profile = LLM_PROFILES.get(llm_name, {})
        if kind in profile and profile[kind] is None:
            return []
        primary = profile.get(kind, []) or []
        fallback = FALLBACK_SELECTORS.get(kind, [])
        seen = set()
        merged = []
        for sel in primary + fallback:
            if sel not in seen:
                seen.add(sel)
                merged.append(sel)
        # ChatGPT conversation-turn can be either a user or assistant turn.
        # Never use the generic turn container as an assistant response selector.
        if kind == "response_block" and llm_name == "ChatGPT":
            merged = [sel for sel in merged if "conversation-turn" not in sel]
        return merged

    @staticmethod
    def _classify_page_state(ready_state, visible_inputs, matched_inputs, page_text="", title="",
                             login_controls=0):
        """Phân loại trạng thái trang khi composer chưa sẵn sàng."""
        haystack = f"{title}\n{page_text}".lower()
        cloudflare_markers = (
            "just a moment", "verify you are human", "checking your browser",
            "security verification", "cf-chl", "chờ một chút",
            "xác minh bạn là con người", "đang kiểm tra trình duyệt",
        )
        error_markers = (
            "something went wrong", "internal server error", "service unavailable",
            "too many requests", "rate limit", "temporarily unavailable",
        )
        login_markers = (
            "log in", "login", "sign in", "đăng nhập",
        )
        if any(marker in haystack for marker in cloudflare_markers):
            return "CLOUDFLARE"
        if any(marker in haystack for marker in error_markers):
            return "ERROR_PAGE"
        has_login_marker = any(marker in haystack for marker in login_markers)
        if has_login_marker and (login_controls > 0 or visible_inputs == 0):
            return "LOGIN_REQUIRED"
        if visible_inputs > 0:
            return "READY"
        if str(ready_state).lower() not in ("interactive", "complete"):
            return "PAGE_LOADING"
        if matched_inputs > 0:
            return "COMPOSER_HIDDEN"
        return "COMPOSER_NOT_FOUND"

    def _inspect_page_state(self, log_event=True):
        """Thu thập chẩn đoán DOM tối thiểu, không ghi prompt/cookie/account data."""
        selectors = self.get_selectors("input")
        matched_inputs = 0
        visible_inputs = 0
        for sel in selectors:
            try:
                elements = self.driver.find_elements(By.CSS_SELECTOR, sel)
                matched_inputs += len(elements)
                visible_inputs += sum(1 for el in elements if el.is_displayed())
            except Exception:
                continue

        try:
            ready_state = self.driver.execute_script("return document.readyState || '';") or ""
        except Exception:
            ready_state = ""
        try:
            title = self.driver.title or ""
        except Exception:
            title = ""
        try:
            page_text = self.driver.execute_script(
                "return (document.body && document.body.innerText || '').slice(0, 5000);"
            ) or ""
        except Exception:
            page_text = ""
        try:
            login_controls = int(self.driver.execute_script(
                """
                const markers = ['log in', 'login', 'sign in', 'đăng nhập'];
                const els = document.querySelectorAll('button,a,[role="button"],[role="dialog"]');
                let count = 0;
                for (const el of els) {
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    if (style.display === 'none' || style.visibility === 'hidden'
                            || rect.width <= 0 || rect.height <= 0) continue;
                    const text = ((el.innerText || '') + ' ' + (el.getAttribute('aria-label') || ''))
                        .toLowerCase();
                    if (markers.some(marker => text.includes(marker))) count += 1;
                }
                return count;
                """
            ) or 0)
        except Exception:
            login_controls = 0

        def _count(css):
            try:
                return len(self.driver.find_elements(By.CSS_SELECTOR, css))
            except Exception:
                return 0

        details = {
            "ready": ready_state,
            "title": title[:120],
            "input_matches": matched_inputs,
            "input_visible": visible_inputs,
            "textareas": _count("textarea"),
            "contenteditables": _count("[contenteditable='true']"),
            "iframes": _count("iframe"),
            "login_controls": login_controls,
        }
        state = self._classify_page_state(
            ready_state, visible_inputs, matched_inputs, page_text=page_text, title=title,
            login_controls=login_controls
        )
        if log_event:
            self._diag("page_state", state=state, url=self._current_url_for_log(), **details)
        return state, details

    def _wait_for_document_ready(self, timeout_secs=20):
        deadline = time.time() + timeout_secs
        while time.time() < deadline:
            self.check_stop(self.current_task)
            try:
                ready_state = self.driver.execute_script("return document.readyState || '';")
                if ready_state in ("interactive", "complete"):
                    return True
            except Exception:
                pass
            time.sleep(0.5)
        return False

    def _show_browser_for_user(self):
        """Hiện Chrome khi Web LLM cần đăng nhập/xác minh thủ công."""
        try:
            self.driver.maximize_window()
        except Exception:
            pass

    def _raise_if_manual_page_state(self, state):
        if state == "LOGIN_REQUIRED":
            self._show_browser_for_user()
            raise BrowserActionRequired(
                state,
                f"{self.get_current_llm()} đang yêu cầu đăng nhập (LOGIN_REQUIRED). "
                "Mở Chrome của tool, đăng nhập xong rồi chạy lại bước này."
            )
        if state == "CLOUDFLARE":
            self._show_browser_for_user()
            raise BrowserActionRequired(
                state,
                f"{self.get_current_llm()} đang ở màn hình xác minh Cloudflare "
                "(CLOUDFLARE). Hoàn tất xác minh trên Chrome của tool rồi chạy lại."
            )

    def wait_for_manual_recovery(self, timeout_secs=300):
        """Chờ người dùng xử lý login/Cloudflare rồi tiếp tục Auto khi trang READY."""
        self._show_browser_for_user()
        started = time.time()
        deadline = started + max(1, int(timeout_secs))
        last_state = None
        self._diag("manual_recovery_wait", provider=self.get_current_llm(), timeout=timeout_secs)
        while time.time() < deadline:
            self.check_stop(self.current_task)
            try:
                state, _ = self._inspect_page_state(log_event=False)
            except Exception:
                state = None
            if state == "READY":
                self._diag("manual_recovery_ready", provider=self.get_current_llm(),
                           elapsed=round(time.time() - started, 1))
                self.keep_minimized()
                return True
            if state and state != last_state:
                self._diag("manual_recovery_state", provider=self.get_current_llm(), state=state)
                last_state = state
            time.sleep(1.0)
        self._diag("manual_recovery_timeout", provider=self.get_current_llm(),
                   elapsed=round(time.time() - started, 1))
        return False

    def find_input_box(self, timeout_secs=25):
        """Tìm ô nhập liệu (input box) trên trang, chờ tối đa timeout_secs giây.
        (Find the chat input box on the page, waiting up to timeout_secs.)"""
        selectors = self.get_selectors("input")
        self._last_input_selector = None
        self._wait_for_document_ready(timeout_secs=min(20, timeout_secs))
        self.dismiss_cookie_popups()
        started = time.time()
        last_state_check = 0.0
        deadline = time.time() + timeout_secs
        while time.time() < deadline:
            self.check_stop(self.current_task)
            for sel in selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, sel)
                    visible_elements = [el for el in elements if el.is_displayed()]
                    if visible_elements:
                        state, _ = self._inspect_page_state(log_event=False)
                        if state in ("LOGIN_REQUIRED", "CLOUDFLARE"):
                            self._inspect_page_state(log_event=True)
                            self._raise_if_manual_page_state(state)
                        self._last_input_selector = sel
                        return visible_elements[-1]
                except BrowserActionRequired:
                    raise
                except Exception:
                    pass
            elapsed = time.time() - started
            if elapsed >= 6 and elapsed - last_state_check >= 2:
                last_state_check = elapsed
                state, _ = self._inspect_page_state(log_event=False)
                if state in ("LOGIN_REQUIRED", "CLOUDFLARE"):
                    self._inspect_page_state(log_event=True)
                    self._raise_if_manual_page_state(state)
            time.sleep(0.5)
        return None

    def _find_input_box_quick(self):
        """Tìm input box nhanh không chờ. (Quick-find input box without waiting.)"""
        for sel in self.get_selectors("input"):
            try:
                elements = self.driver.find_elements(By.CSS_SELECTOR, sel)
                visible = [el for el in elements if el.is_displayed()]
                if visible:
                    self._last_input_selector = sel
                    return visible[-1]
            except Exception:
                continue
        return None

    def count_response_blocks(self):
        """Đếm số block response hiện có trên trang (dùng để phát hiện response mới).
        (Count current response blocks on the page — used to detect new responses.)"""
        try:
            selectors_json = json.dumps(self.get_selectors("response_block"))
            js = ("const selectors = " + selectors_json + ";"
                  "for (let sel of selectors) {"
                  "  let els = document.querySelectorAll(sel);"
                  "  if (els && els.length > 0) return els.length;"
                  "}"
                  "return 0;")
            return self.driver.execute_script(js) or 0
        except Exception:
            return 0

    def _composer_text_len(self, input_box):
        """Do dai noi dung trong o nhap lieu.
        (Text length inside the input box.)

        Phai doc qua textContent, KHONG dung selenium .text: o div
        contenteditable, .text chi tra ve phan chu DANG HIEN THI (bi cat
        ngan), con payload that co the hang trăm KB. Dung .text se lam
        nguong kiem tra "da gui" luon true -> tool tuong da gui xong.
        (Must read textContent, not selenium .text - see above.)
        """
        for box in (input_box, self._find_input_box_quick()):
            if box is None:
                continue
            try:
                n = self.driver.execute_script(
                    "return (arguments[0].textContent||arguments[0].value||'').trim().length;",
                    box)
                if n is not None:
                    return int(n)
            except Exception:
                pass
        return 0

    def is_generating(self):
        """Kiểm tra LLM có đang trong trạng thái 'đang tạo' không (nút Stop hiện ra).
        (Check if LLM is still generating — stop button is visible.)"""
        try:
            for sel in self.get_selectors("stop_button"):
                els = self.driver.find_elements(By.CSS_SELECTOR, sel)
                if els and any(e.is_displayed() for e in els):
                    return True
        except Exception:
            pass
        return False

    def dismiss_cookie_popups(self):
        """Tự đóng các popup cookie / Cloudflare trên trang.
        (Auto-dismiss cookie / Cloudflare popups.)"""
        try:
            cookie_texts = [
                "Accept all", "Accept All", "Đồng ý", "Chấp nhận tất cả", "Chấp nhận",
                "Got it", "I agree", "OK", "Tôi đồng ý", "Accept"
            ]
            xpath_parts = " or ".join([f"normalize-space(text())='{t}'" for t in cookie_texts])
            xpath = f"//button[{xpath_parts}]"
            btns = self.driver.find_elements(By.XPATH, xpath)
            for b in btns:
                if b.is_displayed() and b.is_enabled():
                    try:
                        self.driver.execute_script("arguments[0].click();", b)
                        time.sleep(0.5)
                    except Exception:
                        pass
        except Exception:
            pass

    def try_upload_file(self, file_path):
        """Thử upload file vào ô input[type=file]. (Try uploading a file via the file input.)"""
        selectors = self.get_selectors("file_upload_input")
        if not selectors or not file_path or not os.path.exists(file_path):
            return False
        try:
            for sel in selectors:
                elements = self.driver.find_elements(By.CSS_SELECTOR, sel)
                for el in elements:
                    try:
                        el.send_keys(file_path)
                        time.sleep(3)
                        return True
                    except Exception:
                        continue
        except Exception:
            pass
        return False

    def inject_text_and_send(self, task_name, target_url, text_payload, file_path=None):
        """Dán text vào input box và gửi tin nhắn. Trả về (tab_handle, baseline_count).
        (Paste text into the input box and send. Returns (tab_handle, baseline_response_count).)"""
        self.check_stop(task_name)
        self.current_task = task_name
        with self.driver_lock:
            try:
                target_tab_handle = self.ensure_valid_tab(task_name, target_url)
                self.driver.switch_to.window(target_tab_handle)
            except (NoSuchWindowException, WebDriverException):
                target_tab_handle = self.get_task_tab(task_name, target_url, force_new=True)
                self.driver.switch_to.window(target_tab_handle)

            self._diag("send_begin", task=task_name, provider=self.get_current_llm(),
                       url=self._current_url_for_log(), payload_len=len(text_payload or ""))

            if file_path:
                self.try_upload_file(file_path)

            input_box = self.find_input_box(timeout_secs=25)
            if not input_box:
                state, _ = self._inspect_page_state(log_event=True)
                self._diag("composer_not_found", task=task_name, provider=self.get_current_llm(),
                           state=state, url=self._current_url_for_log())

                self._raise_if_manual_page_state(state)

                # Với trang tải lỗi/DOM hydrate dở/selector tạm chưa xuất hiện:
                # tải lại đúng tab một lần, sau đó để tầng retry Web bên ngoài
                # restart Chrome nếu vẫn thất bại.
                self._diag("composer_recovery_reload", task=task_name, state=state)
                try:
                    self.driver.get(target_url)
                except Exception as err:
                    self._diag("composer_reload_error", task=task_name,
                               error=type(err).__name__)
                self._wait_for_document_ready(timeout_secs=20)
                self.dismiss_cookie_popups()
                input_box = self.find_input_box(timeout_secs=25)

            if not input_box:
                state, _ = self._inspect_page_state(log_event=True)
                raise Exception(
                    "Không tìm thấy ô nhập tin nhắn sau khi đã tải lại trang một lần "
                    f"(state={state}). Xem browser_debug.log để biết trạng thái DOM."
                )

            self._diag("composer_found", task=task_name, selector=self._last_input_selector,
                       url=self._current_url_for_log())

            # Retry có thể gặp composer còn prompt cũ. Xóa trước khi dán để không
            # chồng payload mới lên nội dung còn sót lại.
            try:
                if self._composer_text_len(input_box) > 0:
                    input_box.click()
                    input_box.send_keys(Keys.CONTROL, "a")
                    input_box.send_keys(Keys.BACKSPACE)
                    time.sleep(0.2)
            except Exception:
                pass

            js_paste = """
                let el = arguments[0];
                let text = arguments[1];
                el.focus();
                let success = document.execCommand('insertText', false, text);
                if (!success) {
                    try {
                        let dt = new DataTransfer();
                        dt.setData('text/plain', text);
                        let evt = new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true });
                        el.dispatchEvent(evt);
                    } catch(e){}
                }
                if (el.tagName.toLowerCase() === 'textarea' || el.tagName.toLowerCase() === 'input') {
                    if (!el.value || el.value.length === 0) {
                        el.value = text;
                    }
                }
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            """
            self.driver.execute_script(js_paste, input_box, text_payload)
            time.sleep(0.8)

            payload_len = len(text_payload)
            min_expected = 1 if payload_len < 10 else max(10, payload_len // 3)
            pasted_len = self._composer_text_len(input_box)

            # CDP insertText là fallback đáng tin cậy hơn cho textarea/contenteditable
            # khi trang chặn execCommand/paste event.
            if pasted_len < min_expected:
                try:
                    input_box.click()
                    input_box.send_keys(Keys.CONTROL, "a")
                    input_box.send_keys(Keys.BACKSPACE)
                    self.driver.execute_cdp_cmd("Input.insertText", {"text": text_payload})
                    time.sleep(0.8)
                    pasted_len = self._composer_text_len(input_box)
                except Exception:
                    pass

            self._diag("composer_injected", task=task_name, selector=self._last_input_selector,
                       payload_len=payload_len, composer_len=pasted_len)
            if pasted_len < min_expected:
                raise Exception(
                    "Không thể dán đầy đủ prompt vào ô nhập liệu (Composer injection failed). "
                    f"Độ dài cần gửi={payload_len}, đọc lại={pasted_len}."
                )

            baseline_count = self.count_response_blocks()
            sent_ok = False

            for attempt in range(1, 4):
                clicked = False
                matched_selector = None
                for sel in self.get_selectors("send_button"):
                    try:
                        btns = self.driver.find_elements(By.CSS_SELECTOR, sel)
                    except Exception:
                        btns = []
                    target_btns = []
                    for b in btns:
                        try:
                            if b.tag_name.lower() in ("button", "div") and (b.get_attribute("role") in (None, "button") or b.tag_name.lower() == "button"):
                                target_btns.append(b)
                            else:
                                try:
                                    parent_btn = b.find_element(By.XPATH, "./ancestor::button[1]")
                                    target_btns.append(parent_btn)
                                except Exception:
                                    target_btns.append(b)
                        except Exception:
                            pass
                    for b in target_btns:
                        try:
                            is_disabled = (b.get_attribute("aria-disabled") == "true"
                                            or b.get_attribute("disabled") is not None
                                            or b.get_attribute("data-disabled") == "true")
                            if b.is_displayed() and not is_disabled:
                                try:
                                    b.click()
                                except Exception:
                                    self.driver.execute_script("arguments[0].click();", b)
                                clicked = True
                                matched_selector = sel
                                break
                        except Exception:
                            pass
                    if clicked:
                        break

                if not clicked:
                    try:
                        input_box.click()
                        input_box.send_keys(Keys.ENTER)
                    except Exception:
                        pass

                for _ in range(5):
                    time.sleep(1)
                    state, _ = self._inspect_page_state(log_event=False)
                    if state in ("LOGIN_REQUIRED", "CLOUDFLARE"):
                        self._inspect_page_state(log_event=True)
                        self._raise_if_manual_page_state(state)
                    composer_len = self._composer_text_len(input_box)
                    response_count = self.count_response_blocks()
                    if (composer_len < min_expected or self.is_generating()
                            or response_count > baseline_count):
                        sent_ok = True
                        break
                self._diag("send_attempt", task=task_name, attempt=attempt,
                           strategy=("button" if clicked else "enter"),
                           selector=matched_selector, sent=sent_ok,
                           composer_len=self._composer_text_len(input_box),
                           baseline_responses=baseline_count,
                           current_responses=self.count_response_blocks())
                if sent_ok:
                    break

                new_box = self._find_input_box_quick()
                if new_box is not None:
                    input_box = new_box

            if not sent_ok:
                self._diag("send_failed", task=task_name, provider=self.get_current_llm(),
                           url=self._current_url_for_log())
                raise Exception(
                    "Tool đã dán nội dung nhưng KHÔNG GỬI ĐƯỢC tin nhắn sau 3 lần thử (Message not sent after 3 attempts)!\n\n"
                    "Nguyên nhân:\n"
                    "1. Bạn CHƯA ĐĂNG NHẬP trên trình duyệt.\n"
                    "2. Nút gửi bị khóa hoặc giao diện web vừa thay đổi.\n\n"
                    "Hãy đăng nhập rồi chạy lại. Hoặc chuyển sang nguồn 🔑 API để không phụ thuộc trình duyệt.")

            self._diag("send_confirmed", task=task_name, baseline_responses=baseline_count)
            return target_tab_handle, baseline_count

    def wait_for_llm_response(self, task_name, target_url, target_tab_handle, timeout=180, baseline_count=None):
        self.current_task = task_name
        self.keep_minimized()
        time.sleep(2.0)
        last_text = ""
        stable_count = 0
        start_t = time.time()

        response_selectors = self.get_selectors("response_block")
        selectors_json = json.dumps(response_selectors)

        js_get_response = """
            const selectors = """ + selectors_json + """;
            for (let sel of selectors) {
                let els = document.querySelectorAll(sel);
                if (els && els.length > 0) {
                    let lastEl = els[els.length - 1];
                    let text = lastEl.innerText ? lastEl.innerText.trim() : "";
                    if (text.length > 0) return text;
                }
            }
            return "";
        """

        self._diag("response_wait_begin", task=task_name, provider=self.get_current_llm(),
                   baseline_responses=baseline_count, timeout=timeout,
                   url=self._current_url_for_log())
        saw_new_response = baseline_count is None
        last_response_count = baseline_count if baseline_count is not None else 0

        while time.time() - start_t < timeout:
            self.check_stop(task_name)
            time.sleep(1.5)
            with self.driver_lock:
                try:
                    target_tab_handle = self.ensure_valid_tab(task_name, target_url)
                    self.driver.switch_to.window(target_tab_handle)
                    state, _ = self._inspect_page_state(log_event=False)
                    if state in ("LOGIN_REQUIRED", "CLOUDFLARE"):
                        self._inspect_page_state(log_event=True)
                        self._raise_if_manual_page_state(state)
                    response_count = self.count_response_blocks()
                    curr_text = self.driver.execute_script(js_get_response)
                    still_generating = self.is_generating()

                    if baseline_count is not None and response_count > baseline_count:
                        saw_new_response = True
                    last_response_count = response_count

                    if saw_new_response and curr_text:
                        curr_text = curr_text.strip()
                        if curr_text == last_text and len(curr_text) > 0 and not still_generating:
                            stable_count += 1
                            if stable_count >= 3:
                                self._diag("response_complete", task=task_name,
                                           responses=response_count, response_len=len(curr_text))
                                return curr_text
                        else:
                            last_text = curr_text
                            stable_count = 0
                except (NoSuchWindowException, WebDriverException):
                    pass
                except BrowserActionRequired:
                    raise
                except Exception:
                    pass

        self._diag("response_timeout", task=task_name, baseline_responses=baseline_count,
                   current_responses=last_response_count,
                   saw_new_response=saw_new_response, response_len=len(last_text))
        return last_text if saw_new_response else ""

    def kill_chrome(self):
        """Đóng Chrome một cách an toàn: set stop_event, quit driver, dọn process thừa.
        (Gracefully stop Chrome: set stop event, quit driver, clean up orphan processes.)"""
        self.stop_event.set()
        self.pause_event.clear()
        with self.driver_lock:
            if self.driver:
                try:
                    self.driver.quit()
                    if hasattr(self.driver, 'service') and self.driver.service.process:
                        driver_pid = self.driver.service.process.pid
                        subprocess.call(f"taskkill /F /T /PID {driver_pid}", shell=True,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    pass
                self.driver = None
                self.tab_handles = {"scene": None, "prompt": None}
                self.active_run_llm = {"scene": None, "prompt": None}

    def restart_chrome(self):
        """Dong Chrome hoan toan va mo lai tu dau (dung khi che do Web bi loi).
        KHONG dung stop_event - nen khong giat luan chay dang thu lai.
        (Fully close and relaunch Chrome for Web retry. Does NOT touch stop_event.)
        Do dong mot cua so cu dang treo, chi giu lai profile de giu dang nhap.
        """
        self._diag("driver_restart_begin")
        with self.driver_lock:
            if self.driver:
                try:
                    self.driver.quit()
                except Exception:
                    pass
                try:
                    if getattr(self.driver, "service", None) and self.driver.service.process:
                        subprocess.call(
                            "taskkill /F /T /PID %d" % self.driver.service.process.pid,
                            shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    pass
            # Bo handle cu: neu giu, ensure_valid_tab se dung lai cua so cu.
            self.driver = None
            self.tab_handles = {"scene": None, "prompt": None}
            self.active_run_llm = {"scene": None, "prompt": None}
        self._diag("driver_restart_done")
        # get_or_create_driver() tu dung lai Chrome tu profile tren dia.
