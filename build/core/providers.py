# -*- coding: utf-8 -*-
"""
Provider adapters cho Web LLM.

Mỗi provider một adapter riêng: URL, composer, nút gửi, khối trả lời,
nút "đang tạo", ô upload. Một website đổi giao diện chỉ làm hỏng đúng
adapter của nó, không kéo theo các provider khác.

Thứ tự locator theo độ ổn định:
  data-testid -> id -> role/aria -> semantic -> provider-specific -> CSS generic
Không bao giờ bám vào class CSS ngẫu nhiên do React sinh ra.

Mọi selector trả về là CHUỖI để Playwright tự re-resolve mỗi lần dùng
(giải quyết "stale element" — thứ làm Selenium phải tìm lại liên tục).
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProviderAdapter:
    name: str
    url: str
    host: str
    # ô nhập, xếp theo độ ổn định
    composers: tuple = ()
    sends: tuple = ()
    # nút "đang tạo" -> có nghĩa response CHƯA xong
    generating: tuple = ()
    # khối trả lời của ASSISTANT (không được chung/generic dễ bắt nhầm user turn)
    assistants: tuple = ()
    # lượt hội thoại của USER (bằng chứng gửi thành công mạnh nhất)
    users: tuple = ()
    # ô upload file; None = provider không hỗ trợ đính kèm
    file_upload: tuple = ()
    # selector nút kết nối -> user chưa đăng nhập
    login_marks: tuple = ()

    @property
    def supports_file(self):
        return bool(self.file_upload)


# Locator chung, chỉ dùng CUỐI CÙNG sau các locator đặc thù provider.
GENERIC_COMPOSERS = ("textarea#prompt-textarea", "textarea#chat-input",
                      "div[contenteditable='true'][role='textbox']",
                      "div[contenteditable='true']", "textarea")
GENERIC_SENDS = ("button[data-testid='send-button']",
                 "button[data-testid='composer-submit-button']",
                 "#composer-submit-button",
                 "button[aria-label*='Send' i]", "button[aria-label*='Gửi' i]",
                 "button[type='submit']", "button.send-button")
GENERIC_GENERATING = ("button[data-testid='stop-button']",
                      "button[aria-label*='Stop' i]",
                      "button[aria-label*='Dừng' i]")
# KHÔNG dùng `article[data-testid^='conversation-turn']` cho assistant:
# `^=` khớp CẢ `conversation-turn-user` -> lượt của user bị đọc nhầm làm
# câu trả lời. Luôn loại trừ hậu tố "-user".
_NO_USER = ":not([data-testid*='user'])"

GENERIC_ASSISTANTS = ("[data-message-author-role='assistant']",
                      f"article[data-testid^='conversation-turn']{_NO_USER} .markdown",
                      "message-content", ".markdown-body", "div.prose")
GENERIC_USERS = ("[data-message-author-role='user']",
                 "article[data-testid^='conversation-turn-user']")
GENERIC_FILE_UPLOAD = ("input[type='file']",)

_CHATGPT_USERS = ("[data-message-author-role='user']", "article[data-testid^='conversation-turn-user']")

ADAPTERS = {
    "ChatGPT": ProviderAdapter(
        name="ChatGPT",
        url="https://chatgpt.com/",
        host="chatgpt.com",
        composers=("textarea#prompt-textarea", "#prompt-textarea",
                   "div[contenteditable='true'][id='prompt-textarea']"),
        sends=("button[data-testid='send-button']",
               "button[data-testid='composer-submit-button']",
               "#composer-submit-button",
               "button[aria-label*='Send' i]", "button[aria-label*='Gửi' i]"),
        generating=("button[data-testid='stop-button']",
                    "button[data-testid='composer-stop-button']",
                    "button[data-testid='fruitjuice-stop-button']",
                    "button[aria-label*='Stop' i]", "button[aria-label*='Dừng' i]"),
        assistants=("div[data-message-author-role='assistant'] .markdown",
                    "div[data-message-author-role='assistant']",
                    f"article[data-testid^='conversation-turn']{_NO_USER} .markdown"),
        users=_CHATGPT_USERS,
        file_upload=GENERIC_FILE_UPLOAD,
        login_marks=("Log in", "Sign up", "Welcome back", "Đăng nhập"),
    ),
    "Gemini": ProviderAdapter(
        name="Gemini",
        url="https://gemini.google.com/app",
        host="gemini.google.com",
        composers=("rich-textarea div[contenteditable='true']",
                   "div.ql-editor[contenteditable='true']",
                   "div[contenteditable='true'][role='textbox']"),
        sends=("button.send-button", "button[aria-label*='Send' i]",
               "button[aria-label*='Gửi' i]",
               "mat-icon[data-mat-icon-name='send']"),
        generating=("button[aria-label*='Stop' i]", "button[aria-label*='Dừng' i]",
                    "mat-icon[data-mat-icon-name='stop_circle']"),
        assistants=("message-content .markdown", "message-content",
                    "div.model-response-text"),
        users=("user-query", "div.query-text"),
        file_upload=("input[type='file']",),
        login_marks=("Sign in", "Đăng nhập", "Google Account"),
    ),
    "Claude": ProviderAdapter(
        name="Claude",
        url="https://claude.ai/",
        host="claude.ai",
        composers=("div[contenteditable='true'].ProseMirror",
                   "div[contenteditable='true'][role='textbox']"),
        sends=("button[aria-label*='Send message' i]", "button[aria-label*='Send' i]",
               "button[aria-label*='Gửi' i]", "button[type='submit']"),
        generating=("button[aria-label*='Stop response' i]",
                    "button[aria-label*='Stop' i]", "button[aria-label*='Dừng' i]"),
        assistants=(".font-claude-message",
                    f"div[data-testid='conversation-turn']{_NO_USER} .prose",
                    "div.prose"),
        users=("div[data-testid='user-message']",),
        file_upload=GENERIC_FILE_UPLOAD,
        login_marks=("Log in", "Continue with", "Đăng nhập"),
    ),
    "DeepSeek": ProviderAdapter(
        name="DeepSeek",
        url="https://chat.deepseek.com/",
        host="chat.deepseek.com",
        composers=("textarea#chat-input", "textarea"),
        sends=("div._7436101", ".send-btn", "div[class*='send-btn']",
               "button[aria-label*='Send' i]"),
        generating=("div._6e084f", "div[class*='stop-btn']",
                    "button[aria-label*='Stop' i]"),
        assistants=(".ds-markdown", "div[class*='ds-markdown']", "div[class*='ds-message']"),
        users=("div[class*='fbb737a4']", "div[class*='user-message']"),
        file_upload=(),      # DeepSeek web không cho đính kèm file
        login_marks=("Sign in", "登录", "Đăng nhập"),
    ),
    "Qwen": ProviderAdapter(
        name="Qwen",
        url="https://chat.qwenlm.ai/",
        host="chat.qwenlm.ai",
        composers=("textarea#chat-input", "textarea"),
        sends=("button.send-button", "button[aria-label*='Send' i]",
               "button[class*='send' i]"),
        generating=("button[aria-label*='Stop' i]", "button[class*='stop' i]"),
        assistants=("div.markdown-body", "div[class*='markdown']"),
        users=("div[class*='user']",),
        file_upload=(),
        login_marks=("Sign in", "Login", "Đăng nhập"),
    ),
    "Duck.ai": ProviderAdapter(
        name="Duck.ai",
        url="https://duck.ai/",
        host="duck.ai",
        composers=("#chat-input", "textarea", "div[contenteditable='true']"),
        sends=("button[type='submit']", "button[aria-label*='Send' i]",
               "button[aria-label*='Gửi' i]"),
        generating=GENERIC_GENERATING,
        assistants=("[data-testid='answer']",
                    "article[data-message-author-role='assistant']",
                    "div[class*='markdown']"),
        users=("article[data-message-author-role='user']",),
        file_upload=GENERIC_FILE_UPLOAD,
        login_marks=("Sign in", "Log in", "Đăng nhập"),
    ),
    "Dola": ProviderAdapter(
        name="Dola",
        url="https://www.dola.com/ai-chat",
        host="dola.com",
        composers=("div[contenteditable='true']", "div[role='textbox']", "textarea"),
        sends=("button[aria-label*='Send' i]", "button[type='submit']",
               "div[class*='send' i]", "button[class*='submit' i]"),
        generating=GENERIC_GENERATING,
        assistants=("div[class*='message-content']", "div[class*='markdown']",
                    ".markdown-body"),
        users=("div[class*='user-message']",),
        file_upload=GENERIC_FILE_UPLOAD,
        login_marks=("Sign in", "Log in", "Get started"),
    ),
    "Sakana": ProviderAdapter(
        name="Sakana",
        url="https://chat.sakana.ai/",
        host="chat.sakana.ai",
        composers=("textarea[data-slot='textarea']", "textarea"),
        sends=("button[aria-label*='Send' i]", "button[aria-label*='gửi' i]",
               "button[type='submit']"),
        generating=GENERIC_GENERATING,
        assistants=("div[data-slot*='assistant']", "div.markdown-body",
                    "div[class*='markdown']"),
        users=("div[data-slot*='user']",),
        file_upload=(),
        login_marks=("Sign in", "Log in", "ログイン"),
    ),
}


def get_adapter(name):
    """Tên provider -> adapter; tên lạ thì lấy ChatGPT (không crash)."""
    return ADAPTERS.get(name) or ADAPTERS["ChatGPT"]


def provider_names():
    return list(ADAPTERS.keys())


def host_matches(current_url, adapter):
    """Tab đang mở có đúng provider không (so theo hostname, không so path).

    URL đổi path sau khi gửi (chatgpt.com/ -> chatgpt.com/c/<id>) là BÌNH THƯỜNG
    và tuyệt đối không được tạo tab mới chỉ vì việc đó.
    """
    from urllib.parse import urlparse
    try:
        cur = (urlparse(current_url).hostname or "").lower()
    except Exception:
        return False
    if not cur:
        return False
    host = adapter.host.lower()
    return cur == host or cur.endswith("." + host)


def selector_chain(primary, generic):
    """Ghép locator đặc thù + generic, bỏ trùng, giữ thứ tự ưu tiên."""
    out, seen = [], set()
    for sel in list(primary) + list(generic):
        if sel and sel not in seen:
            seen.add(sel)
            out.append(sel)
    return out


def composer_selectors(adapter):
    return selector_chain(adapter.composers, GENERIC_COMPOSERS)


def send_selectors(adapter):
    return selector_chain(adapter.sends, GENERIC_SENDS)


def generating_selectors(adapter):
    return selector_chain(adapter.generating, GENERIC_GENERATING)


def assistant_selectors(adapter):
    return selector_chain(adapter.assistants, ())


def user_selectors(adapter):
    return selector_chain(adapter.users, ())