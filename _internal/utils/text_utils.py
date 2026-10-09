# -*- coding: utf-8 -*-
"""
Các hàm tiện ích xử lý chuỗi, regex, thời gian
(String, regex, and time utility functions)
"""
import re

def has_meaningful_text(text):
    """Kiểm tra text có chứa ÍT NHẤT 1 ký tự chữ/số thật hay không.
    Dòng chỉ toàn dấu câu/khoảng trắng → False.
    (Check if text contains at least 1 real alphanumeric character. Punctuation-only → False.)"""
    if not text:
        return False
    return any(ch.isalnum() for ch in text)

def clean_llm_response(text):
    """Lọc bỏ khối suy nghĩ <think>...</think> / <thought>...</thought>
    của Reasoning models (DeepSeek R1, Qwen Think...).
    (Strip reasoning blocks from LLM responses.)"""
    if not text:
        return ""
    cleaned = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r'<thought>.*?</thought>', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    return cleaned.strip()

def extract_code_block(text):
    """Trích xuất nội dung bên trong ```json ... ``` hoặc ``` ... ```
    (Extract content inside markdown code blocks.)"""
    if not text:
        return ""
    match = re.search(r'```(?:json)?\s*(.*?)```', text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()

def format_timestamp(seconds):
    """Chuyển giây → định dạng SRT: HH:MM:SS,mmm
    (Convert seconds to SRT timestamp format HH:MM:SS,mmm)"""
    total_msecs = int(round(seconds * 1000))
    hours = total_msecs // 3600000
    remainder = total_msecs % 3600000
    minutes = remainder // 60000
    remainder %= 60000
    secs = remainder // 1000
    msecs = remainder % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{msecs:03d}"

def parse_timestamp(ts_str):
    """Chuyển SRT timestamp string về số giây float.
    (Parse SRT timestamp string to float seconds.)"""
    ts_str = ts_str.strip().replace(',', '.')
    parts = ts_str.split(':')
    if len(parts) == 3:
        h, m, s = parts
        return float(h) * 3600 + float(m) * 60 + float(s)
    elif len(parts) == 2:
        m, s = parts
        return float(m) * 60 + float(s)
    return float(parts[0])
