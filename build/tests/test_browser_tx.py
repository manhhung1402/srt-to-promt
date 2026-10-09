# -*- coding: utf-8 -*-
"""
Regression test cho tầng trình duyệt (Playwright).

Chạy thật với Chrome thật + trang HTML fixture nội bộ, KHÔNG gọi
ChatGPT/Gemini thật, KHÔNG cần mạng ngoài. Mục tiêu: chứng minh 4 lỗi
chết người của bản cũ KHÔNG THỂ quay lại.

  T1  composer cũ đang có text -> phải bị xoá trước khi dán (không dán chồng)
  T2  click gửi nhưng KHÔNG có lượt user mới -> phải báo send_unconfirmed
  T3  lượt assistant CŨ đã có sẵn -> không được trả về làm câu trả lời
  T4  payload bị cắt cụt (DOM giữ 1/3) -> phải ném COMPOSER_INJECTION_FAILED
  T5  overlay login -> phải ném BrowserActionRequired, không phải READY
  T6  Cloudflare -> BrowserActionRequired
  T7  đúng READY -> trả về đúng response, log transaction_pass
  T8  nút Dừng -> ask_llm ném StopRun ngay cả khi đang chờ response
  T9  Page KHÔNG được chạm từ thread khác (invariant của kiến trúc)

Chạy:  python tests/test_browser_tx.py
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS = []
FAIL = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    suffix = f"  <- {detail}" if (detail and not cond) else ""
    print(("  OK   " if cond else "  FAIL ") + name + suffix)


# ------------------------------------------------------------------ fixtures
PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>t</title></head>
<body>
<div id="log"></div>
<textarea id="prompt-textarea" rows="3" style="width:600px;height:80px"></textarea>
<button id="send" data-testid="send-button">Send</button>
<button id="stop" data-testid="stop-button" style="display:none">Stop</button>
<script>
window.__n = { user: 0, asst: 0, sent: 0 };
window.__reply = ['A','B','C'];

function addUser(text){
  var d=document.createElement('div');
  d.setAttribute('data-message-author-role','user');
  var m=document.createElement('div'); m.className='markdown';
  m.textContent=text.slice(0,40);
  d.appendChild(m); document.getElementById('log').appendChild(d);
  window.__n.user++;
}
function addAsst(text){
  var d=document.createElement('div');
  d.setAttribute('data-message-author-role','assistant');
  var m=document.createElement('div'); m.className='markdown';
  m.textContent=text;
  d.appendChild(m); document.getElementById('log').appendChild(d);
  window.__n.asst++;
  document.getElementById('stop').style.display='none';
}
function respond(){
  document.getElementById('stop').style.display='';
  var parts=window.__reply, i=0;
  var iv=setInterval(function(){
    addAsst(parts[i]||'Z'); i++;
    if(i>=parts.length){ clearInterval(iv); addAsst(parts.join(' ')); }
  }, 120);
}
document.getElementById('send').addEventListener('click', function(){
  if(window.__n.deadSend) return;   // click xong, KHÔNG tạo lượt user -> mô phỏng nút chết
  var ta=document.getElementById('prompt-textarea');
  var v=ta.value||ta.textContent||'';
  if(v.trim()){ addUser(v); window.__n.sent++; if(window.__n.autoreply!==false) respond(); }
});
</script>
</body></html>"""

LOGIN_PAGE = """<!doctype html><html><body>
<div>Welcome back</div><button>Log in</button></body></html>"""

CF_PAGE = """<!doctype html><html><head><title>Just a moment...</title></head>
<body><div>Checking your browser before accessing</div>
<div>Verify you are human</div></body></html>"""


def serve(html):
    """HTTP server một chút, để page.url là http://127.0.0.1 thật (host check chạy)."""
    import http.server
    import socketserver

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    socketserver.TCPServer.allow_reuse_address = True
    srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/"


def make_session(tmp, tag, url):
    """Đăng ký adapter giả trỏ về fixture, trả session headless."""
    from core.browser_session import BrowserSession
    from core.providers import ProviderAdapter, ADAPTERS

    ADAPTERS["TEST"] = ProviderAdapter(
        name="TEST", url=url, host="127.0.0.1",
        composers=("textarea#prompt-textarea",),
        sends=("button[data-testid='send-button']",),
        generating=("button[data-testid='stop-button']",),
        assistants=("div[data-message-author-role='assistant'] .markdown",),
        users=("div[data-message-author-role='user']",),
        file_upload=())
    return BrowserSession(tmp, lambda: "TEST",
                         profile_dir="prof_" + tag, headless=True)


def open_task_page(s, task):
    """Mở tab của task TRÊN thread chủ sở hữu (invariant T9)."""
    from core.providers import get_adapter

    def _go():
        page = s._task_page(task, get_adapter("TEST"))
        page.wait_for_selector("textarea#prompt-textarea", timeout=20000)
        return True

    s._run(_go, timeout=120)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    tmp = os.path.join(here, "_tmp_profile")
    os.makedirs(tmp, exist_ok=True)

    srv, url = serve(PAGE)
    login_srv, login_url = serve(LOGIN_PAGE)
    cf_srv, cf_url = serve(CF_PAGE)

    from core.faults import BrowserActionRequired, StepFailure

    # ============================================ T1 + T7
    print("\n[T1/T7] composer cũ bị xoá, gửi đúng, trả đúng response")
    s = make_session(tmp, "t1", url)
    open_task_page(s, "t1")
    s._run(lambda: s._pages["t1"].fill("textarea#prompt-textarea", "NOI DUNG CU BI SOI"),
           timeout=60)
    text, meta = s.ask_llm("t1", "HAY GOM CAU 1-10", timeout=25)
    check("T7 trả về text khác rỗng", bool(text and text.strip()), repr(text[:60]))
    check("T1 composer cũ KHÔNG bị dán chồng vào response",
          "NOI DUNG CU" not in text, repr(text[:80]))
    check("T7 meta có tx", str(meta.get("tx", "")).startswith("t1-"), str(meta))
    check("T7 meta provider", meta.get("provider") == "TEST", str(meta))
    s.kill_chrome()

    # ============================================ T2
    print("\n[T2] click xong nhưng không có lượt user mới -> send_unconfirmed")
    s = make_session(tmp, "t2", url)
    open_task_page(s, "t2")
    s._run(lambda: s._pages["t2"].evaluate("window.__n.deadSend=true"), timeout=60)
    try:
        s.ask_llm("t2", "NOI DUNG GI", timeout=15)
        check("T2 ném StepFailure send_unconfirmed", False, "không ném")
    except StepFailure as exc:
        check("T2 ném StepFailure send_unconfirmed",
              exc.category == "send_unconfirmed", exc.category)
    except Exception as exc:
        check("T2 ném StepFailure send_unconfirmed", False, type(exc).__name__)
    s.kill_chrome()

    # ============================================ T3
    print("\n[T3] lượt assistant CŨ có sẵn -> không được trả về")
    s = make_session(tmp, "t3", url)
    open_task_page(s, "t3")
    s._run(lambda: s._pages["t3"].evaluate(
        "addAsst('CAU TRA LOI CU'); window.__n.autoreply=false;"), timeout=60)
    try:
        s.ask_llm("t3", "CAU MOI", timeout=18)
        check("T3 không trả lượt assistant cũ", False, "trả về rồi")
    except StepFailure as exc:
        check("T3 không trả lượt assistant cũ",
              exc.category in ("no_response", "send_unconfirmed"), exc.category)
    except Exception as exc:
        check("T3 không trả lượt assistant cũ", False, type(exc).__name__)
    s.kill_chrome()

    # ============================================ T4
    print("\n[T4] DOM chỉ giữ 1/3 payload -> COMPOSER_INJECTION_FAILED")
    s = make_session(tmp, "t4", url)
    open_task_page(s, "t4")
    s._run(lambda: s._pages["t4"].evaluate("""() => {
      const ta = document.getElementById('prompt-textarea');
      Object.defineProperty(ta, 'value', {
        get(){ return this._v || ''; },
        set(v){ this._v = String(v).slice(0, 1000); }
      });
    }"""), timeout=60)
    try:
        s.ask_llm("t4", "X" * 3000, timeout=15)
        check("T4 chặn nội dung cắt cụt", False, "không ném lỗi")
    except StepFailure as exc:
        check("T4 chặn nội dung cắt cụt", exc.category == "injection", exc.category)
    except Exception as exc:
        check("T4 chặn nội dung cắt cụt", False, type(exc).__name__)
    s.kill_chrome()

    # ============================================ T5
    print("\n[T5] trang login -> BrowserActionRequired, KHÔNG phải READY")
    s = make_session(tmp, "t5", login_url)
    try:
        s.ask_llm("t5", "BAT KY GI", timeout=20)
        check("T5 nhận diện login", False, "không ném lỗi")
    except BrowserActionRequired as exc:
        check("T5 nhận diện login", "đăng nhập" in str(exc).lower(), str(exc)[:70])
    except Exception as exc:
        check("T5 nhận diện login", False, type(exc).__name__)
    s.kill_chrome()

    # ============================================ T6
    print("\n[T6] Cloudflare -> BrowserActionRequired")
    s = make_session(tmp, "t6", cf_url)
    try:
        s.ask_llm("t6", "BAT KY GI", timeout=20)
        check("T6 nhận diện Cloudflare", False, "không ném lỗi")
    except BrowserActionRequired as exc:
        check("T6 nhận diện Cloudflare", "cloudflare" in str(exc).lower(), str(exc)[:70])
    except Exception as exc:
        check("T6 nhận diện Cloudflare", False, type(exc).__name__)
    s.kill_chrome()

    # ============================================ T8
    print("\n[T8] nhấn Dừng khi đang chờ -> StopRun ngay")
    s = make_session(tmp, "t8", url)
    open_task_page(s, "t8")
    err = {}

    def worker():
        try:
            s.ask_llm("t8", "CHO", timeout=120)
        except BaseException as exc:
            err["exc"] = exc

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    time.sleep(3)
    s.stop_event.set()
    t.join(timeout=20)
    check("T8 thread kết thúc sau khi Dừng", not t.is_alive(), "vẫn còn chạy sau 20s")
    check("T8 ném StopRun (không phải lỗi khác)",
          type(err.get("exc")).__name__ == "StopRun", type(err.get("exc")).__name__)
    s.kill_chrome()

    # ============================================ T9 invariant
    print("\n[T9] Page chỉ được chạm trên thread chủ sở hữu")
    s = make_session(tmp, "t9", url)
    open_task_page(s, "t9")
    try:
        page = s._pages["t9"]          # cố ý lấy Page ngoài owner thread
        page.inner_text("body")
        check("T9 chặn truy cập Page sai thread", False, "không ném lỗi")
    except Exception as exc:
        check("T9 chặn truy cập Page sai thread",
              "different thread" in str(exc).lower()
              or "greenlet" in type(exc).__name__.lower(),
              type(exc).__name__)
    s.kill_chrome()

    print("\n" + "=" * 52)
    print(f"BROWSER TX TESTS: {len(PASS)} pass, {len(FAIL)} fail")
    for name in FAIL:
        print("  FAILED: " + name)
    print("=" * 52)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())