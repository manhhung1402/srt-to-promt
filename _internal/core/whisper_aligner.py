# -*- coding: utf-8 -*-
"""
Bước 1: Xử lý Whisper (cài ngoài) và căn chỉnh câu theo text gốc
Step 1: Run external Whisper runtime and align sentences to reference text
"""
import os
import re
import difflib
from core.whisper_runtime import WhisperRuntime
from utils.text_utils import format_timestamp, has_meaningful_text
from utils.file_utils import load_srt_as_segments

def run_whisper_alignment(audio_path, srt_in_path, ref_text, whisper_runtime=None, model_name="base", status_cb=None):
    """Chạy Whisper (hoặc nạp SRT) rồi đối chiếu text gốc để tính timestamp từng câu.
    (Run Whisper or load existing SRT, then align with reference text.)"""
    whisper_segments = []
    audio_duration = 0.0

    if srt_in_path:
        whisper_segments = load_srt_as_segments(srt_in_path)
        if whisper_segments:
            audio_duration = whisper_segments[-1].end
    elif audio_path:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        runtime = whisper_runtime or WhisperRuntime(base)
        model = runtime.load_model(model_name, progress_cb=status_cb)
        segments_gen, info = model.transcribe(audio_path, beam_size=5, word_timestamps=True, language="vi")
        audio_duration = info.duration
        whisper_segments = list(segments_gen)
    else:
        raise Exception("Vui lòng chọn File Voice hoặc File SRT có sẵn!")

    if not whisper_segments:
        raise Exception("Không nhận diện được giọng nói hoặc file SRT rỗng!")

    # Không có file kịch bản gốc -> lấy chính nội dung SRT/Whisper làm chuẩn.
    if not (ref_text or "").strip():
        sentences_data, audio_dur = sentences_from_segments(whisper_segments)
    else:
        sentences_data, audio_dur = build_sentence_timings(whisper_segments, ref_text, audio_duration)
    srt_content = render_sentences_srt(sentences_data)
    return sentences_data, srt_content, audio_dur

def sentences_from_segments(whisper_segments):
    """Không có kịch bản gốc: mỗi đoạn SRT/Whisper thành một câu, giữ nguyên timestamp.
    (No reference script: take the SRT/Whisper text as-is, timestamps untouched.)"""
    results = []
    for seg in whisper_segments:
        text = str(getattr(seg, "text", "") or "").strip()
        if not has_meaningful_text(text):
            continue
        start = float(getattr(seg, "start", 0.0) or 0.0)
        end = float(getattr(seg, "end", start) or start)
        if end <= start:
            end = start + 1.0
        results.append({"id": len(results) + 1, "text": text,
                        "start": start, "end": end, "duration": end - start})
    if not results:
        raise Exception("File SRT rỗng hoặc không có câu nào hợp lệ!")
    return results, results[-1]["end"]


def build_sentence_timings(whisper_segments, ref_text, audio_duration):
    """Đối chiếu câu với word-level Whisper, tính start/end cho từng câu.
    (Align sentences to Whisper word timestamps.)"""
    raw_sentences = [s.strip() for s in re.split(r'(?<=[.!?\u3002\uff01\uff1f\n])\s+', ref_text) if s.strip()]
    if not raw_sentences and ref_text.strip():
        raw_sentences = [ref_text.strip()]

    target_sentences = [s for s in raw_sentences if has_meaningful_text(s)]
    if not target_sentences:
        raise Exception("Kịch bản gốc rỗng hoặc không có chữ hợp lệ!")

    all_whisper_words = []
    for seg in whisper_segments:
        if hasattr(seg, 'words') and seg.words:
            for w in seg.words:
                cleaned_w = re.sub(r'[^\w\s]', '', w.word).strip().lower()
                if cleaned_w:
                    all_whisper_words.append({'word': cleaned_w, 'start': w.start, 'end': w.end})

    if not all_whisper_words:
        total_chars = sum(len(s) for s in target_sentences)
        curr_time = 0.0
        results = []
        for idx, s in enumerate(target_sentences, 1):
            dur = (len(s) / max(total_chars, 1)) * audio_duration
            results.append({'id': idx, 'text': s, 'start': curr_time, 'end': curr_time + dur, 'duration': dur})
            curr_time += dur
        return results, audio_duration

    w_texts = [w['word'] for w in all_whisper_words]
    target_tokenized = []
    for s in target_sentences:
        toks = re.sub(r'[^\w\s]', '', s).strip().lower().split()
        target_tokenized.append(toks)

    all_target_tokens = [tok for toks in target_tokenized for tok in toks]

    matcher = difflib.SequenceMatcher(None, all_target_tokens, w_texts)
    matching_blocks = matcher.get_matching_blocks()
    target_to_whisper_map = {}
    for match in matching_blocks:
        for offset in range(match.size):
            target_to_whisper_map[match.a + offset] = match.b + offset

    results = []
    curr_target_idx = 0
    last_known_end = 0.0

    for idx, (sent_text, tokens) in enumerate(zip(target_sentences, target_tokenized), 1):
        num_toks = len(tokens)
        matched_whisper_indices = []
        for t_idx in range(curr_target_idx, curr_target_idx + num_toks):
            if t_idx in target_to_whisper_map:
                matched_whisper_indices.append(target_to_whisper_map[t_idx])

        if matched_whisper_indices:
            first_w = all_whisper_words[min(matched_whisper_indices)]
            last_w = all_whisper_words[max(matched_whisper_indices)]
            start_t = max(first_w['start'], last_known_end)
            end_t = max(last_w['end'], start_t + 0.1)
        else:
            start_t = last_known_end
            end_t = start_t + max(1.0, len(tokens) * 0.3)

        end_t = min(end_t, audio_duration)
        if end_t <= start_t:
            end_t = start_t + 0.5

        results.append({'id': idx, 'text': sent_text, 'start': start_t, 'end': end_t, 'duration': end_t - start_t})
        last_known_end = end_t
        curr_target_idx += num_toks

    for i in range(len(results) - 1):
        if results[i]['end'] > results[i + 1]['start']:
            mid = (results[i]['end'] + results[i + 1]['start']) / 2.0
            results[i]['end'] = mid
            results[i + 1]['start'] = mid
            results[i]['duration'] = results[i]['end'] - results[i]['start']
            results[i + 1]['duration'] = results[i + 1]['end'] - results[i + 1]['start']

    return results, audio_duration

def render_sentences_srt(sentences_data):
    """Render danh sách câu thành nội dung SRT.
    (Render sentence list to SRT content.)"""
    srt_result = ""
    for item in sentences_data:
        srt_result += f"{item['id']}\n{format_timestamp(item['start'])} --> {format_timestamp(item['end'])}\n{item['text']}\n\n"
    return srt_result
