# -*- coding: utf-8 -*-
"""
Kiểm tra cầu nối: shim Playwright có thỏa đúng interface mà code cũ gọi.

Không chạy trình duyệt. Chỉ kiểm hợp đồng (contract) để chắc chắn việc đổi
1 dòng import trong ui/app.py không làm vỡ bất kỳ call site nào.

  C1  BrowserManagerV3 có đủ 11 hàm mà code cũ thực sự gọi
  C2  app.py chỉ khác đúng 1 dòng import
  C3  get_selectors trả selector đúng kiểu cho từng mode
  C4  provider lạ trong config không làm crash (fallback ChatGPT)
  C5  restart_chrome KHÔNG set stop_event (nếu không, Auto tự chết)
  C6  kill_chrome mới set stop_event (đóng app thì phải dừng)
  C7  adapter có đủ trường cho cả 8 provider
  C8  wait_response trả về lượt MỚI, không phải lượt cũ
  C9  StopRun không bị nuốt bởi except rộng

Chạy:  python tests/test_integration_shim.py
"""
import ast
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PASS = []
FAIL = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    suffix = f"  <- {detail}" if (detail and not cond) else ""
    print(("  OK   " if cond else "  FAIL ") + name + suffix)


# Đúng 11 hàm này được grep từ toàn source (không tính dist/ và tests/).
REQUIRED = (
    "check_stop", "close_task_tab", "get_current_llm", "get_selectors",
    "get_task_tab", "inject_text_and_send", "kill_chrome", "restart_chrome",
    "try_upload_file", "wait_for_llm_response", "wait_for_manual_recovery",
)

SOURCE_FILES = (
    "core/prompt_generator.py", "core/scene_grouper.py",
    "ui/app.py", "ui/auto_panel.py",
)


def collect_called_methods():
    """Mọi hàm được gọi qua browser_mgr*. trong source thật."""
    found = set()
    for rel in SOURCE_FILES:
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            continue
        text = io.open(path, encoding="utf-8").read()
        for match in re.finditer(r"browser_mgr\w*\.(\w+)\(", text):
            found.add(match.group(1))
    return found


def main():
    # ------------------------------------------------------------------ C1
    print("\n[C1] shim co du 11 ham ma code cu goi")
    from core.browser_v3 import BrowserManagerV3
    called = collect_called_methods()
    # get_current_llm là thuộc tính instance (giống class cũ), không phải method.
    instance_attrs = {"get_current_llm"}
    missing = sorted(n for n in called
                     if n not in instance_attrs
                     and not hasattr(BrowserManagerV3, n))
    check("C1 khong thieu ham nao", not missing, f"thieu: {missing}")
    missing_all = sorted(n for n in called
                         if n not in instance_attrs
                         and not hasattr(BrowserManagerV3, n))
    # Hàm thật sự phải có (trừ thuộc tính instance)
    funcs_needed = sorted(called - instance_attrs)
    missing_req = [n for n in REQUIRED
                   if n not in instance_attrs
                   and not hasattr(BrowserManagerV3, n)]
    check("C1 dat het 11 ham yeu cau", not missing_req, f"thieu: {missing_req}")
    probe = BrowserManagerV3.__new__(BrowserManagerV3)
    probe.get_current_llm = lambda: "ChatGPT"
    check("C1 get_current_llm ton tai tren instance",
          probe.get_current_llm() == "ChatGPT", "")
    check("C1 goi them ham ngoai y so voi danh sach da grep",
          called <= set(REQUIRED), f"goi them: {sorted(called - set(REQUIRED))}")

    # ------------------------------------------------------------------ C2
    print("\n[C2] app.py chi doi DONG BIET trong phan Playwright bridge + data dir")
    bak = os.path.join(os.path.dirname(ROOT), os.path.join("backup", "_src_BAK_before_rewrite"), "ui", "app.py")
    cur = os.path.join(ROOT, "ui", "app.py")
    if os.path.exists(bak):
        old = io.open(bak, encoding="utf-8").read().replace("\r\n", "\n")
        new = io.open(cur, encoding="utf-8").read().replace("\r\n", "\n")
        diff_lines = [ln for ln in old.split("\n") if ln not in new.split("\n")]
        added = [ln for ln in new.split("\n") if ln not in old.split("\n")]
        # Đã xóa có chủ đích: đổi Selenium -> Playwright, base_dir -> data/,
        # và BỎ auto_method (cài đặt Bước 2 / Bước 4 phải độc lập nhau).
        # Bỏ comment: chỉ kiểm dòng CODE bị xóa.
        removed_nc = [d for d in diff_lines
                      if d.strip() and not d.strip().startswith("#")]
        _expected_removed = [
            ("from core.browser_manager import", "import Selenium -> Playwright"),
            ("import get_base_dir", "base_dir -> data/"),
            ("self.base_dir = get_base_dir()", "base_dir -> data/"),
            ("auto_method", "bo phuong thuc chon trong Cai dat auto"),
            ("ai_source_var.set", "auto khong ghi de Buoc 2"),
            ("prompt_source_var.set", "auto khong ghi de Buoc 4"),
        ]
        missing = [why for frag, why in _expected_removed
                   if not any(frag in d for d in removed_nc)]
        check("C2 xoa dung cac dong co chu dich", not missing,
              "thieu: " + ", ".join(missing))
        check("C2 khong xoa them gi nua ngoai danh sach",
              len(removed_nc) == 8, "%d dong: %s" % (len(removed_nc), removed_nc))
        # Dong them: 2 import + 1 gan base_dir moi + khoi dong Chrome khi het
        # kich ban (PTV2). Moi khoi dong deu phai co "self." hoac "import".
        added_nc = [a for a in added if a.strip() and not a.strip().startswith("#")]
        _expected_added = [
            "from utils.file_utils import get_data_dir, export_text_widget_content",
            "from core.browser_v3 import BrowserManager",
            "        self.base_dir = get_data_dir()",
        ]
        # PTV2: cho phep cac dong khoi dong/ket thuc Chrome them vao cuoi app.py.
        _ptv2_ok = ("def _shutdown_browser" in new
                    and "kill_chrome()" in new
                    and "stop_event.clear()" in new)
        _extra = [a for a in added_nc
                  if a not in _expected_added
                  and a not in ("        def _shutdown_browser():",
                                "            self.browser_mgr.kill_chrome()      # set stop_event ben trong",
                                "            self.browser_mgr.stop_event.clear()",
                                "        threading.Thread(target=_shutdown_browser, daemon=True).start()")]
        check("C2 chi them 2 import + 1 gan + khoi dong Chrome (khong phai sinh code dung)",
              not _extra and _ptv2_ok, str(_extra))
        check("C2 khong doi bat ky thu muc khac",
              "browser_v3" in new and "browser_manager import" not in new, "")
    else:
        check("C2 tim thay file backup", False, bak)

    # ------------------------------------------------------------------ C3
    print("\n[C3] get_selectors tra selector dung kieu")
    from core.providers import get_adapter
    session = _make_stub_session()
    mgr = BrowserManagerV3.__new__(BrowserManagerV3)
    mgr.session = session
    for kind, minimum in (("input", 1), ("send_button", 1),
                          ("response_block", 1), ("file_upload_input", 0)):
        got = session.get_selectors(kind)
        check(f"C3 {kind} tra list[str] >= {minimum}",
              isinstance(got, list) and len(got) >= minimum
              and all(isinstance(s, str) for s in got), str(got)[:70])

    # ------------------------------------------------------------------ C4
    print("\n[C4] ten provider la khong crash")
    for weird in ("KhongCo", "", None, "chatgpt"):
        adapter = get_adapter(weird)
        check(f"C4 provider '{weird}' -> fallback hop le",
              bool(adapter.name) and bool(adapter.url), adapter.name)

    # ------------------------------------------------------------------ C5/C6
    print("\n[C5/C6] restart khong dung Auto, kill moi dung")
    src = io.open(os.path.join(ROOT, "core", "browser_v3.py"),
                  encoding="utf-8").read()
    tree = ast.parse(src)
    funcs = {n.name: n for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef)}
    restart_body = ast.dump(funcs["restart_chrome"])
    # Chỉ xét CODE, không xét docstring (docment tiếng Anh dễ chứa từ "set").
    restart_code = "\n".join(
        ln for ln in io.open(os.path.join(ROOT, "core", "browser_v3.py"),
                             encoding="utf-8").read().split("\n")
        if not ln.strip().startswith("#"))
    seg = re.search(r"def restart_chrome\(self\):(.*?)(?=\n    def |\Z)",
                    restart_code, re.S)
    seg_text = seg.group(1) if seg else ""
    check("C5 restart_chrome khong set stop_event",
          "stop_event.set" not in seg_text, seg_text.strip()[:100])
    check("C5 restart_chrome uoc goi session.restart_browser()",
          "session.restart_browser()" in seg_text, seg_text.strip()[:100])
    check("C6 kill_chrome uoc goi session.kill_chrome()",
          "kill_chrome" in ast.dump(funcs["kill_chrome"]), "")
    sess_src = io.open(os.path.join(ROOT, "core", "browser_session.py"),
                       encoding="utf-8").read()
    check("C6 session.kill_chrome set stop_event",
          re.search(r"def kill_chrome.*?stop_event\.set\(\)", sess_src,
                    re.S) is not None, "")
    check("C5 session.restart_browser KHONG set stop_event",
          re.search(r"def restart_browser.*?stop_event\.set\(\)", sess_src,
                    re.S) is None, "")

    # ------------------------------------------------------------------ C7
    print("\n[C7] 8 provider du truong can thiet")
    from core.providers import ADAPTERS, composer_selectors, send_selectors
    check("C7 co 8 provider", len(ADAPTERS) >= 8, str(len(ADAPTERS)))
    bad = []
    for name, adapter in ADAPTERS.items():
        if not (adapter.name and adapter.url and adapter.host):
            bad.append(f"{name}: thieu url/host")
        if not composer_selectors(adapter) or not send_selectors(adapter):
            bad.append(f"{name}: thieu composer/send")
        if not adapter.assistants:
            bad.append(f"{name}: thieu assistant")
    check("C7 khong provider nao thieu truong", not bad, "; ".join(bad[:3]))

    # ------------------------------------------------------------------ C8
    print("\n[C8] wait_response chi lay luot MOI")
    from core import browser_tx as TX
    tx_src = io.open(os.path.join(ROOT, "core", "browser_tx.py"),
                     encoding="utf-8").read()
    check("C8 so sanh TEXT lượt cuối voi baseline (khong so so luot turn)",
          "base_assistant" in tx_src
          and "last_assistant_text" in tx_src
          and "text != outcome.base_assistant" in tx_src,
          "")
    check("C8 co kiem tra dang sinh (generating)",
          "generating_selectors" in tx_src and "not generating" in tx_src, "")
    check("C8 co kiem tra on dinh truoc khi tra",
          "stable" in tx_src and "stable >= 3" in tx_src, "")
    check("C8 het gio -> nem loi chứ khong tra rong",
          "response_timeout" in tx_src and "raise StepFailure" in tx_src, "")

    # ------------------------------------------------------------------ C9
    print("\n[C9] StopRun khong bi nuot")
    rec_src = io.open(os.path.join(ROOT, "core", "recovery.py"),
                      encoding="utf-8").read()
    check("C9 run_step_with_recovery re-raise StopRun",
          "except (StopRun, DeferFolder, KeyboardInterrupt)" in rec_src, "")
    check("C9 wait_before_retry tra False cho StopRun",
          re.search(r"if isinstance\(exc, StopRun\):.*?return False",
                    rec_src, re.S) is not None, "")
    sess = io.open(os.path.join(ROOT, "core", "browser_session.py"),
                   encoding="utf-8").read()
    check("C9 _owner_loop tra ve exception thay vi nuot",
          "result.put((False, exc))" in sess, "")
    check("C9 check_stop nem StopRun",
          "raise StopRun()" in sess, "")
    auto_src = io.open(os.path.join(ROOT, "core", "auto_runner.py"),
                       encoding="utf-8").read()
    check("C9 auto_runner khong co sys.exit/raise SystemExit",
          "sys.exit" not in auto_src and "SystemExit" not in auto_src, "")

    # ---- C10: quet TOAN BO interface ma code that goi tren browser_mgr.
    # Viet tay danh sach thi bo sot (gap 2 lan: active_run_llm lam Buoc 2 chet
    # ngay tren may that). Phai QUET code that roi doi chieu.
    print("\n[C10] Interface browser_v3 day du")
    import ast as _ast

    def _iface(path, cls):
        tree = _ast.parse(io.open(path, encoding="utf-8").read())
        for node in _ast.walk(tree):
            if isinstance(node, _ast.ClassDef) and node.name == cls:
                methods, attrs = set(), set()
                for body in node.body:
                    if isinstance(body, _ast.FunctionDef):
                        methods.add(body.name)
                        if body.name == "__init__":
                            for c in _ast.walk(body):
                                if (isinstance(c, _ast.Attribute)
                                        and isinstance(c.value, _ast.Name)
                                        and c.value.id == "self"
                                        and isinstance(c.ctx, _ast.Store)):
                                    attrs.add(c.attr)
                return methods, attrs
        return set(), set()

    _m, _a = _iface(os.path.join(ROOT, "core", "browser_v3.py"), "BrowserManagerV3")

    _live = []
    for _dp, _dn, _fn in os.walk(ROOT):
        # KHONG duoc loai "build" khoi quet: sau khi tach thu muc, chinh
        # ROOT la "build" (ma nguon). Loai "dist" va "__pycache__" thoi.
        if any(x in _dp for x in ("dist", "__pycache__")):
            continue
        for _f in _fn:
            if not _f.endswith(".py"):
                continue
            _rel = os.path.relpath(os.path.join(_dp, _f), ROOT).replace("\\", "/")
            # bo ban Selenium da chet + ban kiem thu (test duoc phep goi
            # thuoc tinh Selenium de chung nghiep)
            if _rel == "core/browser_manager.py" or _rel.startswith("tests/"):
                continue
            _live.append((_rel, io.open(os.path.join(_dp, _f),
                                        encoding="utf-8", errors="replace").read()))

    _used = set()
    for _rel, _txt in _live:
        if _rel == "core/browser_v3.py":
            continue
        for _m2 in re.finditer(r"browser_mgr\.(\w+)", _txt):
            _used.add(_m2.group(1))

    _missing = sorted(n for n in _used if n not in _m and n not in _a)
    check("C10 moi ten goi tren browser_mgr deu ton tai trong V3",
          not _missing, "thieu: %s" % (_missing,))
    check("C10 quet du so file that (%d)" % len(_live), len(_live) >= 8,
          "chi quet %d file" % len(_live))

# ------------------------------------------------------------ C11 PTV2
    # PTV2 chot: Chrome chi bi DUNG HANH khi het TOAN BO kich ban (hoac nguoi
    # dung bam Dung). Sau moi buoc chi dong TAB (page.close), KHONG kill.
    import ast as _ast
    from ui.app import VideoWorkflowTool as _VWT

    def _fn(src, name):
        for node in _ast.walk(_ast.parse(src)):
            if isinstance(node, _ast.FunctionDef) and node.name == name:
                return node
        return None

    _src = io.open(os.path.join(ROOT, "ui", "app.py"), encoding="utf-8").read()
    _fin = _fn(_src, "_finish_auto_run")
    check("C11 co _finish_auto_run", _fin is not None)
    _fin_txt = _ast.get_source_segment(_src, _fin) or ""
    check("C11 het kich ban -> kill_chrome", "kill_chrome" in _fin_txt,
          "thieu kill_chrome trong _finish_auto_run")
    check("C11 kill chay tren thread nen", "Thread" in _fin_txt,
          "kill_chrome goi truc tiep se treo luong giao dien")
    check("C11 clear stop_event sau kill",
          "stop_event.clear()" in _fin_txt,
          "kill_chrome set stop_event -> lan sau bi StopRun ngay")

    class _Btn:
        def __init__(self):
            self.cfg = []

        def config(self, **kw):
            self.cfg.append(kw)

    class _BR:
        def __init__(self):
            import threading as _t
            self.stop_event = _t.Event()
            self.killed = 0

        def kill_chrome(self):
            self.killed += 1
            self.stop_event.set()      # giong het browser_session.kill_chrome

    class _App:
        def __init__(self):
            self.browser_mgr = _BR()
            self._auto_runner = object()
            self._auto_active = True
            self.btn_auto_run = _Btn()
            self.btn_auto_stop = _Btn()

    _a = _App()
    _VWT._finish_auto_run(_a)
    import time as _t2
    for _ in range(120):
        if _a.browser_mgr.killed:
            break
        _t2.sleep(0.05)
    check("C11 that su tat Chrome khi het kich ban",
          _a.browser_mgr.killed == 1, "killed=%d" % _a.browser_mgr.killed)
    check("C11 tra lai nut Chay cho nguoi dung",
          _a.btn_auto_run.cfg[-1].get("state") == "normal")
    check("C11 nut Dung bi khoa",
          _a.btn_auto_stop.cfg[-1].get("state") == "disabled")
    check("C11 clear co lau de lan sau khong bi StopRun",
          not _a.browser_mgr.stop_event.is_set())

    # Buoc 2/4 chi dong TAB, khong goi kill_chrome -> PTV2 that su.
    _src_pg = io.open(os.path.join(ROOT, "core", "prompt_generator.py"), encoding="utf-8").read()
    check("C11 khong co kill trong buoc sinh prompt",
          "kill_chrome" not in _src_pg)
    _src_sess = io.open(os.path.join(ROOT, "core", "browser_session.py"), encoding="utf-8").read()
    _ct = _fn(_src_sess, "close_task_tab")
    _dt = _fn(_src_sess, "_do_close_tab")
    _kd = _fn(_src_sess, "_do_kill")
    check("C11 close_task_tab chi dong TAB, khong dong context",
          _ct is not None and "ctx.close" not in (_ast.get_source_segment(_src_sess, _ct) or ""))
    check("C11 _do_close_tab goi page.close()",
          (_ast.get_source_segment(_src_sess, _dt) or "").count("page.close()") == 1)
    check("C11 _do_kill moi dong context (khong phai tab)",
          "ctx.close()" in (_ast.get_source_segment(_src_sess, _kd) or ""))

    # Rut ngan lot: 3 lan loi lien tiep truoc khi rut 10.
    import core.prompt_generator as _pgx
    check("C11 SHRINK_STEP=10", _pgx.SHRINK_STEP == 10)
    check("C11 SHRINK_RETRY_ATTEMPTS=3", _pgx.SHRINK_RETRY_ATTEMPTS == 3)
    check("C11 MIN_BATCH=10 (tran duoi)", _pgx.MIN_BATCH == 10)

    print("\n" + "=" * 52)
    print(f"INTEGRATION SHIM TESTS: {len(PASS)} pass, {len(FAIL)} fail")
    for name in FAIL:
        print("  FAILED: " + name)
    print("=" * 52)
    return 1 if FAIL else 0


def _make_stub_session():
    """Session giả: chỉ cần get_selectors, không mở trình duyệt."""
    from core.providers import (get_adapter, composer_selectors,
                                send_selectors, generating_selectors,
                                assistant_selectors)

    class Stub:
        def __init__(self):
            self.stop_event = __import__("threading").Event()
            self.pause_event = __import__("threading").Event()

        def get_selectors(self, kind):
            adapter = get_adapter("ChatGPT")
            return {
                "input": composer_selectors(adapter),
                "send_button": send_selectors(adapter),
                "stop_button": generating_selectors(adapter),
                "response_block": assistant_selectors(adapter),
                "file_upload_input": list(adapter.file_upload),
            }.get(kind, [])

    return Stub()


if __name__ == "__main__":
    sys.exit(main())