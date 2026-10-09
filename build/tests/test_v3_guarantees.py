# -*- coding: utf-8 -*-
"""
Port các ĐẢM BẢO của selftest.py sang engine Playwright.

selftest.py cũ kiểm tra chi tiết CÀI ĐẶT của Selenium (driver.quit,
tab_handles, Input.insertText, data-disabled). Những thứ đó không tồn tại
ở Playwright và không còn ý nghĩa. File này kiểm tra CÙNG ĐẢM BẢO nhưng
theo cách đúng cho engine mới.

Mapping: đảm bảo -> cách kiểm
  S1  Chrome chạy ở chế độ thu nhỏ/không lấn át   -> không có --start-maximized,
                                                     có minimize_window
  S2  restart đóng Chrome, xoá tab, KHÔNG bật stop  -> hành vi thật qua session
  S3  đo composer bằng textContent (không .text)   -> _composer_length dùng
                                                     textContent/value
  S4  nút gửi bị disable thì không click           -> send() kiểm disabled
  S5  tab chỉ tái dùng khi đúng provider           -> host_matches theo hostname
  S6  chờ response MỚI so baseline                   -> wait_response so base_assistant
  S7  không đọc lượt user như assistant            -> assistant selectors tách rõ
  S8  fallback khi paste bị web chặn               -> _set_composer có insertText
  S9  response selector của ChatGPT không lẫn user  -> get_selectors sạch
  S10 buoc 2/4 gọi restart khi Web                  -> caller giữ nguyên lời gọi
  S11 Không dùng selenium trong đường chạy thật     -> app import Playwright path

Chạy:  python tests/test_v3_guarantees.py
"""
import inspect
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PASS = []
FAIL = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    suffix = f"  <- {detail}" if (detail and not cond) else ""
    print(("  OK   " if cond else "  FAIL ") + name + suffix)


def read(rel):
    return io.open(os.path.join(ROOT, rel), encoding="utf-8").read()


def main():
    from core.browser_v3 import BrowserManagerV3
    from core.browser_session import BrowserSession
    from core.providers import (get_adapter, host_matches, assistant_selectors,
                                composer_selectors)
    mgr = BrowserManagerV3.__new__(BrowserManagerV3)
    sess_src = read("core/browser_session.py")
    tx_src = read("core/browser_tx.py")

    # ------------------------------------------------------------------ S1
    print("\n[S1] Chrome khong lam an manh cua so nguoi dung")
    check("S1 khong co --start-maximized", "--start-maximized" not in sess_src, "")
    check("S1 giu co minimize_window (tuong thich API cu)", "minimize_window" in
          inspect.getsource(BrowserManagerV3), "")
    check("S1 mo o che do headless mac dinh = False (can thay may)",
          "self.headless = headless" in inspect.getsource(BrowserSession.__init__),
          "")
    check("S1 co --disable-background-timer-throttling (chay nen qua dem)",
          "--disable-background-timer-throttling" in sess_src, "")

    # ------------------------------------------------------------------ S2
    print("\n[S2] restart: dong Chrome, xoa trang thai, KHONG bat stop")
    # Hành vi thật: gọi restart_browser với session chưa có context.
    import threading
    s = BrowserSession(ROOT, lambda: "ChatGPT", headless=True)
    try:
        s.restart_browser()
        check("S2 restart khi chua co Chrome khong nem loi", True, "")
    except Exception as exc:
        check("S2 restart khi chua co Chrome khong nem loi", False,
              f"{type(exc).__name__}: {exc}")
    check("S2 restart KHONG bat stop_event", not s.stop_event.is_set(), "")
    sess = s._ensure_owner_safe() if hasattr(s, "_ensure_owner_safe") else None
    check("S2 _do_kill xoa context", "self._ctx = None" in sess_src, "")
    check("S2 _do_kill xoa danh sach page", "self._pages = {}" in sess_src, "")

    # ------------------------------------------------------------------ S3
    print("\n[S3] do dai composer doc textContent, khong dung .text")
    body = inspect.getsource(BrowserSession._composer_length)
    check("S3 dung textContent hoac value", "textContent" in body and "value" in body,
          body.strip()[:80])
    # Phân tích trực tiếp module thật bằng AST. Cách chắc chắn tách được code
# với docstring: đọc thẳng các nút con (không dùng unparse, nó escape dấu
# nháy và làm hỏng so khớp chuỗi).
    import ast as _ast
    mod_tree = _ast.parse(read("core/browser_session.py"))
    fn = next((n for n in _ast.walk(mod_tree)
               if isinstance(n, _ast.FunctionDef) and n.name == "_composer_length"),
              None)
    check("S3 tim thay _composer_length", fn is not None, "")
    doc = _ast.get_docstring(fn) or ""
    # Code = mọi thứ trừ docstring. Lấy từ body bỏ node Expr đầu (docstring).
    body_nodes = fn.body[1:] if doc else fn.body
    code_only = "\n".join(_ast.unparse(n) for n in body_nodes)
    check("S3 code khong goi innerText (chi nen trong docstring/comment)",
          "innerText" not in code_only, code_only.strip()[:90])
    check("S3 docstring ghi ly do innerText bi cat",
          "innerText" in doc and ("cắt" in doc or "hien thi" in doc or "hiển thị" in doc),
          doc[:70])
    check("S3 code doc value/textContent that",
          "textContent" in code_only and "value" in code_only, code_only.strip()[:90])

    # ------------------------------------------------------------------ S4
    print("\n[S4] nut gui bi disable -> khong bam")
    send_body = inspect.getsource(__import__("core.browser_tx", fromlist=["x"]).send)
    check("S4 kiem tra disabled", "disabled" in send_body, "")
    check("S4 kiem tra aria-disabled", "aria-disabled" in send_body, "")
    check("S4 co fallback Enter", '"enter"' in send_body or "'enter'" in send_body, "")

    # ------------------------------------------------------------------ S5
    print("\n[S5] tab chi tai dung khi dung provider")
    check("S5 chatgpt.com/c/<id> van la dung host",
          host_matches("https://chatgpt.com/c/abc", get_adapter("ChatGPT")), "")
    check("S5 deepseek khong phai chatgpt",
          not host_matches("https://chat.deepseek.com/", get_adapter("ChatGPT")), "")
    check("S5 gemini subdomain hop le",
          host_matches("https://a.gemini.google.com/x", get_adapter("Gemini")), "")
    check("S5 host sai -> dieu huong lai, KHONG tao tab moi",
          "tab_wrong_provider" in sess_src and "page.goto" in sess_src, "")
    check("S5 doc hostname bang urlparse (khong so path)",
          "urlparse" in read("core/providers.py"), "")

    # ------------------------------------------------------------------ S6
    print("\n[S6] chi response MOI, so voi baseline")
    # So TEXT lượt cuối, KHÔNG so số lượt: ChatGPT virtualize DOM nên
    # số turn có thể tăng/giảm lung tung (log thật có user_delta=-1).
    check("S6 wait_response so TEXT lượt cuối",
          "last_assistant_text" in tx_src
          and "text != outcome.base_assistant" in tx_src, "")
    check("S6 capture_baseline chup TRUOC khi gui",
          "base_assistant" in tx_src and "capture_baseline" in sess_src, "")
    check("S6 reset neu luot bi mat (trang doi)",
          "started = False" in tx_src, "")
    check("S6 het gio -> nem loi, khong tra chuoi rong",
          "response_timeout" in tx_src and "raise StepFailure" in tx_src, "")

    # ------------------------------------------------------------------ S7
    print("\n[S7] khong doc luot user nhu assistant")
    a = assistant_selectors(get_adapter("ChatGPT"))
    check("S7 assistant selector chi tro assistant",
          all("[data-message-author-role='user']" not in s for s in a), str(a)[:60])
    check("S7 selector conversation-turn loai tru luot user",
          all(("conversation-turn" not in s) or (":not(" in s and "user" in s)
              for s in a), str(a)[:70])
    from core.providers import user_selectors
    u = user_selectors(get_adapter("ChatGPT"))
    check("S7 user selector tach biet", len(u) > 0 and a != u, str(u)[:60])

    # ------------------------------------------------------------------ S8
    print("\n[S8] fallback khi paste bi web chan")
    setter = inspect.getsource(BrowserSession._set_composer)
    check("S8 co duong dan fallback cho contenteditable",
          "createTextNode" in setter or "insertText" in setter, "")
    check("S8 textarea dung fill (nhanh + on dinh)", "fill(" in setter, "")
    check("S8 doc lai sau khi dan (verify)", "COMPOSER_INJECTION_FAILED" in tx_src,
          "")

    # ------------------------------------------------------------------ S9
    print("\n[S9] response selector cua ChatGPT khon lam user")
    class _Stub:
        stop_event = None
        pause_event = None

        def get_selectors(self, kind):
            from core.providers import composer_selectors, send_selectors, \
                assistant_selectors
            ad = get_adapter("ChatGPT")
            return {"input": composer_selectors(ad), "send_button": send_selectors(ad),
                    "response_block": assistant_selectors(ad),
                    "file_upload_input": list(ad.file_upload)}.get(kind, [])
    mgr.session = _Stub()
    got = mgr.get_selectors("response_block")
    check("S9 get_selectors('response_block') khong loot user turn",
          all(("[data-message-author-role='user']" not in s)
              and ("conversation-turn-user" not in s) for s in got),
          str(got)[:70])

    # ------------------------------------------------------------------ S10
    print("\n[S10] caller goi restart khi Web, khong ap cho API")
    app_src = read("ui/app.py")
    pg_src = read("core/prompt_generator.py")
    check("S10 buoc 2 goi restart", "browser_mgr.restart_chrome()" in app_src, "")
    check("S10 buoc 4 goi restart", "browser_mgr.restart_chrome()" in pg_src, "")
    check("S10 chi khi Web (if not is_api)", "if not is_api:" in app_src, "")

    # ------------------------------------------------------------------ S11
    print("\n[S11] duong chay that dung Playwright, khong con selenium")
    check("S11 app import browser_v3", "from core.browser_v3 import BrowserManager"
          in app_src, "")
    check("S11 khong import browser_manager (Selenium)",
          "from core.browser_manager import" not in app_src, "")
    check("S11 session dung playwright.sync_api",
          "from playwright.sync_api import sync_playwright" in sess_src, "")
    v3_src = read("core/browser_v3.py")
    v3_code = "\n".join(ln for ln in v3_src.split("\n")
                         if not ln.strip().startswith("#"))
    v3_imports = [ln for ln in v3_code.split("\n")
                  if ln.startswith("import ") or ln.startswith("from ")]
    check("S11 browser_v3 khong import selenium",
          not any("selenium" in ln for ln in v3_imports),
          str([ln for ln in v3_imports if "selenium" in ln]))
    spec = read("ToolVideoWorkflow.spec")
    check("S11 spec ship playwright",
          "collect_all(_pkg)" in spec and "'playwright'" in spec, "")
    check("S11 spec ship greenlet + pyee",
          "'greenlet'" in spec and "'pyee'" in spec, "")
    check("S11 spec co hiddenimport sync_api",
          "playwright.sync_api" in spec, "")

    print("\n" + "=" * 52)
    print(f"V3 GUARANTEE TESTS: {len(PASS)} pass, {len(FAIL)} fail")
    for name in FAIL:
        print("  FAILED: " + name)
    print("=" * 52)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())