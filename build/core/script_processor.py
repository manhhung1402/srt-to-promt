# -*- coding: utf-8 -*-
"""
Bước 3: Lọc rác SRT, tách câu và thống kê câu/từ/ký tự
Step 3: Filter SRT junk, split sentences, compute word/char statistics
"""
import re
from utils.text_utils import has_meaningful_text


def process_script_core(raw_text):
    """
    Lọc bỏ các STT và Timestamp của SRT, làm sạch văn bản, tách câu
    theo các dấu kết thúc (. ? ! 。 ！ ？), đồng thời tính toán thống kê chi tiết.
    (Strip SRT index/timestamp lines, split sentences only on . ! ? and CJK
    end marks. Colon and semicolon are NOT sentence ends.)
    """
    if not raw_text or not raw_text.strip():
        return [], "", "Thống kê: 0 câu | 0 từ | 0 ký tự (0 không cách)"

    lines = raw_text.splitlines()
    cleaned_lines = []

    for line in lines:
        l_str = line.strip()
        if not l_str:
            continue
        if re.match(r'^\d+$', l_str):
            continue
        if re.search(r'\d{1,2}:\d{2}:\d{2}[,\.]\d{3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[,\.]\d{3}', l_str):
            continue
        if not has_meaningful_text(l_str):
            continue
        cleaned_lines.append(l_str)

    full_clean_text = " ".join(cleaned_lines)
    full_clean_text = re.sub(r'\s+', ' ', full_clean_text).strip()

    raw_sentences = [s.strip() for s in re.split(r'(?<=[.!?。！？])\s+', full_clean_text) if s.strip()]
    if not raw_sentences and full_clean_text:
        raw_sentences = [full_clean_text]

    valid_sentences = [s for s in raw_sentences if has_meaningful_text(s)]

    total_sentences = len(valid_sentences)
    total_words = sum(len(s.split()) for s in valid_sentences)
    total_chars_with_space = sum(len(s) for s in valid_sentences)
    total_chars_no_space = sum(len(s.replace(" ", "")) for s in valid_sentences)

    stats_str = f"Thống kê: {total_sentences} câu | {total_words:,} từ | {total_chars_with_space:,} ký tự ({total_chars_no_space:,} không cách)"
    out_text = "\n".join(valid_sentences)

    return valid_sentences, out_text, stats_str
