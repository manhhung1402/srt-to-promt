# -*- coding: utf-8 -*-
"""
Page state machine cho Web LLM.

Vì sao cần: bug cũ chỉ kiểm "thấy textarea = READY". Nhưng ChatGPT có thể
hiện ô nhập PHÍA SAU login popup. Tool vẫn dán, vẫn bấm, vẫn ghi log
send_confirmed, rồi chờ 180s và timeout. Người dùng ngủ, sáng dậy thấy
folder bị bỏ qua mà không hiểu vì sao.

Nguyên tắc: trạng thái CHẶN phải được kiểm TRƯỚC READY. READY chỉ hợp
lệ khi composer dùng được, không bị overlay che, đúng hostname.
"""
from core.faults import (BrowserActionRequired, CLOUDFLARE_TEXTS, LOGIN_TEXTS,
                         LOGIN_STRONG_TEXTS, PROVIDER_ERROR_TEXTS, looks_like)

# Trạng thái
BROWSER_CLOSED = "BROWSER_CLOSED"
LOADING = "LOADING"
CLOUDFLARE = "CLOUDFLARE"
LOGIN_REQUIRED = "LOGIN_REQUIRED"
PROVIDER_ERROR = "PROVIDER_ERROR"
READY = "READY"
UNKNOWN = "UNKNOWN"

BLOCKING = (CLOUDFLARE, LOGIN_REQUIRED, PROVIDER_ERROR)


def classify_page(raw_text, url, composer_ok, provider="", login_controls=0):
    """Chuỗi -> trạng thái trang.

    raw_text: nội dung text của trang (rút gọn, đã lowercase ở caller).
    composer_ok: adapter đã tìm thấy composer usable hay chưa.
    login_controls: SỐ control đăng nhập THẬT tìm thấy trên trang (nút
        "Log in", form đăng nhập, account chooser). Đây là bằng chứng đáng
        tin; text trong nội dung hội thoại thì KHÔNG.

    Vì sao cần: "Cách log in vào hệ thống" là nội dung chat bình thường.
    Khớp LOGIN_TEXTS rồi kết luận cần đăng nhập -> tool treo vô hạn chờ
    người dùng dù trang hoàn toàn bình thường. Bản cũ có guard này.
    """
    low = (raw_text or "").lower()
    url_low = (url or "").lower()

    # Redirect sang domain đăng nhập -> login, không phải READY.
    if any(d in url_low for d in ("/auth/", "accounts.google.com", "/login",
                                  "signin", "log-in", "challenge")):
        return LOGIN_REQUIRED

    # Cloudflare / bot check phải trước login: cùng nằm trên overlay.
    if looks_like(low, CLOUDFLARE_TEXTS):
        return CLOUDFLARE

    # Lỗi chính provider (rate limit, model hết hạn...) -> thử lại được.
    if looks_like(low, PROVIDER_ERROR_TEXTS):
        return PROVIDER_ERROR

    # Không có trang nào load được.
    if url_low.startswith("about:") or url_low.startswith("chrome://"):
        return LOADING

    # Login/account chooser.
    # Ưu tiên bằng chứng cứng (control thật trên trang) trước khi đoán
    # bằng chữ.
    if login_controls > 0:
        return LOGIN_REQUIRED

    # READY đòi cả composer dùng được. Đây là điều kiện bắt buộc,
    # không phải điều kiện đủ.
    if composer_ok:
        return READY

    # Chưa có composer: mới đến lúc đoán bằng chữ, và chỉ dùng mẫu mạnh.
    if looks_like(low, LOGIN_STRONG_TEXTS) or looks_like(low, LOGIN_TEXTS):
        return LOGIN_REQUIRED
    return UNKNOWN


def blocking_error(state, provider="", url=""):
    """Trạng thái chặn -> BrowserActionRequired, hoặc None nếu không chặn."""
    if state == CLOUDFLARE:
        return BrowserActionRequired(
            f"{provider}: đang hiển thị Cloudflare verification. "
            "Hãy mở cửa sổ Chrome và hoàn tất xác minh; tool sẽ tự chạy tiếp.",
            provider=provider, url=url, state=state)
    if state == LOGIN_REQUIRED:
        return BrowserActionRequired(
            f"{provider}: đang yêu cầu đăng nhập. "
            "Hãy đăng nhập trong cửa sổ Chrome; tool sẽ tự chạy tiếp.",
            provider=provider, url=url, state=state)
    return None


def is_blocking(state):
    return state in (CLOUDFLARE, LOGIN_REQUIRED)