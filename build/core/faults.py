# -*- coding: utf-8 -*-
"""
Phân loại lỗi - nền tảng để tool KHÔNG BAO GIỜ tự dừng.

Quy tắc số 1 của tool: chỉ dừng khi người dùng bấm Dừng.
Mọi lỗi khác đều phải phân loại để biết:
  - chờ người dùng (login / Cloudflare)  -> không tốn lượt thử
  - thử lại được (mạng, 429, DOM chưa hydrate) -> tự thử
  - lỗi vĩnh viễn (hết credit, sai model) -> vẫn thử, nhưng báo log rõ

Nếu phân loại sai hậu quả khác nhau:
  - coi nhầm lỗi vĩnh viễn là tạm thời  -> quay vô hạn im lặng (đốt tiền API)
  - coi nhầm lỗi tạm thời là vĩnh viễn -> dừng sớm, mất đêm của user
"""
import re

# ---------------------------------------------------------------- base classes


class StopRun(Exception):
    """Ném ra CHỈ khi người dùng bấm Dừng/Thoát. Lớp trên cùng, không ai nuốt.

    Đây là tín hiệu duy nhất được phép dừng toàn bộ tool.
    (The one and only signal allowed to halt the tool.)
    """


class DeferFolder(Exception):
    """Folder này hãy nhường chỗ cho folder khác, thử lại ở vòng sau.

    KHÔNG phải lỗi và KHÔNG phải dừng. Chỉ để một folder hỏng (hết tiền,
    kịch bản hỏng, website đang sửa) không chiếm mãi một chỗ khiến các
    folder khác không bao giờ được chạy. Số lần thử tổng vẫn vô hạn.
    """


class BrowserActionRequired(Exception):
    """Cần NGƯỜI DÙNG thao tác tay: đăng nhập, Cloudflare, chọn tài khoản.

    Không phải lỗi nội dung LLM. Không được tốn lượt thử lại.
    Tool đưa cửa sổ trình duyệt ra trước, chờ vô hạn, rồi tự chạy tiếp
    đúng bước đang dở.
    (Needs a human. Never counts as a retry. Waits forever, then resumes
    the SAME step -- the user never has to press Start again.)
    """

    def __init__(self, reason, provider="", url="", state=""):
        super().__init__(reason)
        self.reason = reason
        self.provider = provider
        self.url = url
        self.state = state

    def state_str(self):
        """Chuoi state de gui qua hang doi GUI (khong phai dobject)."""
        return self.state or "UNKNOWN"

class StepFailure(Exception):
    """Một bước (2 hoặc 4) thất bại sau khi đã hết cách xử lý hợp lý.

    Vẫn KHÔNG dừng tool: vòng lặp Auto bắt lại và thử lại từ checkpoint.
    """

    def __init__(self, message, category="unknown", retryable=True):
        super().__init__(message)
        self.category = category
        self.retryable = retryable


class LLMApiError(Exception):
    """Lỗi gọi API OpenAI-compatible. Mang thông báo tiếng Việt cho người dùng."""

    def __init__(self, message, category="unknown", status_code=0):
        super().__init__(message)
        self.category = category
        self.status_code = status_code


class ParserError(Exception):
    """Phản hồi LLM không đúng định dạng / không phủ kín dữ liệu cần thiết."""


# ---------------------------------------------------------------- phân loại

# Lỗi KHÔNG BAO GIỜ tự khỏi: cứ retry vô hạn là đốt tiền + đốt thời gian.
# Nhưng theo nguyên tắc của tool, ta vẫn thử lại -- chỉ là báo log rõ hơn
# và đánh dấu để người dùng sáng dậy nhìn thấy ngay.
PERMANENT_HINTS = (
    "requires_purchase", "platform credits", "insufficient", "purchase credits",
    "no credit", "deposit required", "billing", "premium models",
    "invalid api key", "unauthorized", "authentication_error", "invalid x-api-key",
    "access_denied", "no endpoints available", "is not available",
    "model not found", "does not exist",
)

# Lỗi tạm thời: retry là đúng.
TRANSIENT_HINTS = (
    "rate limit", "too many requests", "reset after", "429", "500", "502",
    "503", "504", "timeout", "timed out", "temporarily", "connection",
    "connection reset", "bad gateway", "service unavailable", "overloaded",
    "server error", "eof occurred", "read timed",
)


def classify_api_error(detail):
    """Chuỗi lỗi -> (nhóm, có_tự_khỏi_được_không).

    nhóm: credit | auth | missing | limit | network | other
    """
    low = (detail or "").lower()
    if any(k in low for k in PERMANENT_HINTS):
        return "credit" if ("credit" in low or "purchase" in low or "billing" in low
                            or "premium" in low or "deposit" in low) else "auth", False
    if "no endpoints available" in low or "is not available" in low or "not found" in low:
        return "missing", False
    if any(k in low for k in ("rate limit", "too many requests", "reset after")):
        return "limit", True
    if any(k in low for k in ("connection", "timeout", "timed out", "eof",
                             "network", "bad gateway", "unreachable")):
        return "network", True
    if re.search(r"\b(429|500|502|503|504)\b", low):
        return "limit", True
    return "other", True


def classify_http(status_code):
    """HTTP status -> (nhóm, có_tự_khỏi_được_không)."""
    if status_code in (401, 403):
        return "auth", False
    if status_code == 402:
        return "credit", False
    if status_code == 404:
        return "missing", False
    if status_code in (408, 409, 429, 500, 502, 503, 504):
        return "limit", True
    if status_code == 400:
        return "other", False
    if 200 <= status_code < 300:
        return "ok", True
    return "other", True


# ---------------------------------------------------------------- thông điệp

# Những lỗi này cần NGƯỜI, không phải chờ. Nhận diện trên nhiều bản dịch
# vì Cloudflare/ChatGPT đổi câu chữ theo thời gian.
LOGIN_TEXTS = (
    "log in", "login", "sign in", "signin", "đăng nhập", "dang nhap",
    "continue with google", "choose an account", "select an account",
    "chọn tài khoản", "tao khoang", "account chooser", "welcome back",
    "log in to chatgpt", "you must be logged in",
)

# Chữ "log in" xuất hiện rất nhiều trong nội dung hội thoại ("cách log in vào
# hệ thống"), nên khớp text KHÔNG đủ để kết luận cần đăng nhập. Phải có thêm
# control đăng nhập thật trên trang.
LOGIN_STRONG_TEXTS = (
    "welcome back", "log in to chatgpt", "you must be logged in",
    "continue with google", "choose an account", "select an account",
    "account chooser",
)

CLOUDFLARE_TEXTS = (
    "just a moment", "checking your browser", "verify you are human",
    "verify that you are human", "performing security verification",
    "chờ một chút", "cho mot chut", "xác minh", "xac minh",
    "đang kiểm tra trình duyệt", "dang kiem tra trinh duyet",
    "attention required", "ddos protection", "enable javascript and cookies",
    "cloudflare", "ray id",
)

PROVIDER_ERROR_TEXTS = (
    "something went wrong", "network error", "rate limit", "usage limit",
    "model unavailable", "try again", "too many requests",
    "unable to load", "an error occurred", "service unavailable",
)


def looks_like(text, table):
    """So khớp không phân biệt hoa thường trên nhiều mẫu."""
    low = (text or "").lower()
    return any(t in low for t in table)