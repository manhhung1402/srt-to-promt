# -*- coding: utf-8 -*-
"""
Cấu hình hệ thống, API tương thích OpenAI (OpenAI-compatible), và bộ chọn (selector) Web LLM
"""

API_PRESETS = {
    "OpenRouter": "https://openrouter.ai/api/v1",
    "9router (local)": "http://localhost:20128/v1",
    "OpenAI": "https://api.openai.com/v1",
    "Ollama (local)": "http://localhost:11434/v1",
    "LM Studio (local)": "http://localhost:1234/v1",
    "Tùy chỉnh (Custom)": "",
}

API_CONFIG = {
    "default_base_url": API_PRESETS["OpenRouter"],
    "default_model": "openrouter/free",
    "config_file": "api_config.json",
}

API_ERROR_VI = {
    400: "Yêu cầu không hợp lệ (Bad Request). Kiểm tra lại tên model hoặc tham số.",
    401: "API Key KHÔNG ĐÚNG hoặc đã bị xóa (Unauthorized). Kiểm tra lại key của nhà cung cấp.",
    402: "Model này cần trả phí nhưng tài khoản chưa đủ tiền (Payment Required). Đổi model miễn phí hoặc nạp thêm.",
    403: "Key không đủ quyền dùng model này (Forbidden). Hãy chọn model khác.",
    404: "Không tìm thấy model hoặc đường dẫn API (Not Found). Kiểm tra Base URL và tên model, bấm 🔄 để tải danh sách model.",
    429: ("VƯỢT GIỚI HẠN lượt gọi (Rate Limit). Chờ vài phút rồi chạy lại, hoặc tăng Batch Size để tốn ít lượt hơn."
         "Nếu máy chủ báo '" + chr(39) + "platform credits" + chr(39) + "': tài khoản API cần nạp thêm credit (billing/payment)."),
    500: "Máy chủ API gặp lỗi nội bộ (Internal Server Error). Thử lại sau.",
    502: "Máy chủ API không phản hồi đúng (Bad Gateway). Thử lại sau.",
    503: "Dịch vụ API tạm thời quá tải (Service Unavailable). Thử lại sau.",
}
LLM_URLS = {
    "ChatGPT": "https://chatgpt.com/",
    "Gemini": "https://gemini.google.com/app",
    "Claude": "https://claude.ai/",
    "DeepSeek": "https://chat.deepseek.com/",
    "Qwen": "https://chat.qwenlm.ai/",
    "Duck.ai": "https://duck.ai/",
    "Dola": "https://www.dola.com/ai-chat",
    "Sakana": "https://chat.sakana.ai/"
}

LLM_PROFILES = {
    "ChatGPT": {
        "input": [
            "#prompt-textarea",
            "div[contenteditable='true']",
            "textarea#prompt-textarea",
            "textarea"
        ],
        "send_button": [
            "button[data-testid='send-button']",
            "button[data-testid='composer-submit-button']",
            "#composer-submit-button",
            "button[aria-label*='Send' i]",
            "button[aria-label*='Gửi' i]",
            "button.send-button"
        ],
        "stop_button": [
            "button[data-testid='stop-button']",
            "button[data-testid='composer-stop-button']",
            "button[data-testid='fruitjuice-stop-button']",
            "button[aria-label*='Stop' i]",
            "button[aria-label*='Dừng' i]"
        ],
        "response_block": [
            "div[data-message-author-role='assistant'] .markdown",
            "div[data-message-author-role='assistant']",
            "article[data-testid^='conversation-turn'] .markdown",
            "article[data-testid^='conversation-turn']",
            "div.agent-turn"
        ],
        "file_upload_input": ["input[type='file']"],
    },
    "Gemini": {
        "input": [
            "div.ql-editor[contenteditable='true']",
            "rich-textarea div[contenteditable='true']",
            "div[contenteditable='true']"
        ],
        "send_button": [
            "button.send-button",
            "button[aria-label*='Send' i]",
            "button[aria-label*='Gửi' i]",
            "mat-icon[data-mat-icon-name='send']"
        ],
        "stop_button": [
            "button[aria-label*='Stop' i]",
            "button[aria-label*='Dừng' i]"
        ],
        "response_block": [
            "message-content .markdown",
            "message-content",
            "div.model-response-text"
        ],
        "file_upload_input": ["input[type='file']"],
    },
    "Claude": {
        "input": [
            "div[contenteditable='true'].ProseMirror",
            "div[contenteditable='true']"
        ],
        "send_button": [
            "button[aria-label*='Send message' i]",
            "button[aria-label*='Send' i]",
            "button[aria-label*='Gửi' i]"
        ],
        "stop_button": [
            "button[aria-label*='Stop response' i]",
            "button[aria-label*='Stop' i]"
        ],
        "response_block": [
            ".font-claude-message",
            "div[data-testid='conversation-turn'] .prose",
            "div.prose"
        ],
        "file_upload_input": ["input[type='file']"],
    },
    "DeepSeek": {
        "input": [
            "textarea#chat-input",
            "textarea"
        ],
        "send_button": [
            "div[role='button'][aria-disabled='false']",
            ".send-btn",
            "button[aria-label*='Send' i]",
            "div[class*='send-btn']"
        ],
        "stop_button": [
            "div._7436101",
            "button[aria-label*='Stop' i]",
            "div[class*='stop-btn']"
        ],
        "response_block": [
            ".ds-markdown",
            "div[class*='ds-markdown']",
            "div[class*='ds-message']"
        ],
        "file_upload_input": None,
    },
    "Qwen": {
        "input": [
            "textarea#chat-input",
            "textarea"
        ],
        "send_button": [
            "button.send-button",
            "button[aria-label*='Send' i]",
            "button[class*='send' i]"
        ],
        "stop_button": [
            "button[aria-label*='Stop' i]"
        ],
        "response_block": [
            "div.markdown-body",
            "div[class*='markdown']"
        ],
        "file_upload_input": None,
    },
    "Duck.ai": {
        "input": ["#chat-input", "textarea", "div[contenteditable='true']"],
        "send_button": [
            "button[type='submit']",
            "button[aria-label*='Send' i]",
            "button[aria-label*='Gửi' i]"
        ],
        "stop_button": ["button[aria-label*='Stop' i]"],
        "response_block": [
            "[data-testid='answer']",
            "div[data-message-author-role='assistant']",
            "article",
            "div[class*='markdown']"
        ],
        "file_upload_input": ["input[type='file']"],
    },
    "Dola": {
        "input": ["div[contenteditable='true']", "div[role='textbox']", "textarea"],
        "send_button": [
            "button[aria-label*='Send' i]",
            "button[type='submit']",
            "div[class*='send' i]",
            "button[class*='submit' i]"
        ],
        "stop_button": ["button[aria-label*='Stop' i]"],
        "response_block": [
            "div[class*='message-content']",
            "div[class*='markdown']",
            ".markdown-body"
        ],
        "file_upload_input": ["input[type='file']"],
    },
    "Sakana": {
        "input": ["textarea[data-slot='textarea']", "textarea"],
        "send_button": [
            "button[aria-label*='Send' i]",
            "button[aria-label*='gửi' i]",
            "button[aria-label*='送信']",
            "button[type='submit']"
        ],
        "stop_button": ["button[aria-label*='Stop' i]"],
        "response_block": [
            "div[data-slot*='assistant']",
            "div.markdown-body",
            "div[class*='markdown']"
        ],
        "file_upload_input": None,
    },
}

FALLBACK_SELECTORS = {
    "input": [
        "#prompt-textarea",
        "div[contenteditable='true']",
        "textarea#chat-input",
        "textarea"
    ],
    "send_button": [
        "button[data-testid='send-button']",
        "button[data-testid='composer-submit-button']",
        "#composer-submit-button",
        "button[aria-label*='Send' i]",
        "button[aria-label*='Gửi' i]",
        "button.send-button",
        "mat-icon[data-mat-icon-name='send']",
        ".send-btn"
    ],
    "stop_button": [
        "button[data-testid='stop-button']",
        "button[data-testid='composer-stop-button']",
        "button[aria-label*='Stop' i]",
        "button[aria-label*='Dừng' i]"
    ],
    "response_block": [
        "div[data-message-author-role='assistant'] .markdown",
        "div[data-message-author-role='assistant']",
        "article[data-testid^='conversation-turn'] .markdown",
        "article[data-testid^='conversation-turn']",
        ".ds-markdown",
        "div[class*='ds-markdown']",
        "div[class*='ds-message']",
        ".font-claude-message",
        "message-content",
        "div.markdown-body",
        "div.prose",
        "div[class*='markdown']"
    ],
}

DEFAULT_STYLE_PROMPT = "Ancient Chinese historical setting, imperial China, cinematic historical fantasy illustration, highly detailed, 8k resolution, widescreen composition."

