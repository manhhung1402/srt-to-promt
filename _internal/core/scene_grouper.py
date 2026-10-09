# -*- coding: utf-8 -*-
"""
Bước 2: Gộp SRT theo đoạn / phân cảnh (Thuật toán, LLM API, Web LLM)
"""
import os
import json
import re
import tempfile
from utils.text_utils import clean_llm_response, extract_code_block, format_timestamp, has_meaningful_text
from config.settings import LLM_URLS


def enforce_max_sec(scenes, max_sec):
    if not max_sec:
        return scenes
    final_scenes = []
    for scene in scenes:
        curr_sub_scene = []
        for item in scene:
            if not curr_sub_scene:
                curr_sub_scene.append(item)
            else:
                timeline_duration = item['end'] - curr_sub_scene[0]['start']
                if timeline_duration > max_sec:
                    final_scenes.append(curr_sub_scene)
                    curr_sub_scene = [item]
                else:
                    curr_sub_scene.append(item)
        if curr_sub_scene:
            final_scenes.append(curr_sub_scene)
    return final_scenes


def enforce_min_sec(scenes, min_sec, max_sec=None):
    if not min_sec or len(scenes) <= 1:
        return scenes
    result = [list(sc) for sc in scenes]
    i = 0
    while i < len(result):
        scene = result[i]
        dur = scene[-1]['end'] - scene[0]['start']
        if dur >= min_sec:
            i += 1
            continue

        merged = False
        if i + 1 < len(result):
            candidate_duration = result[i + 1][-1]['end'] - scene[0]['start']
            if not max_sec or candidate_duration <= max_sec:
                result[i + 1] = scene + result[i + 1]
                del result[i]
                merged = True
        if not merged and i - 1 >= 0:
            candidate_duration = scene[-1]['end'] - result[i - 1][0]['start']
            if not max_sec or candidate_duration <= max_sec:
                result[i - 1] = result[i - 1] + scene
                del result[i]
                merged = True
                i -= 1

        if not merged:
            i += 1
    return result


def align_scenes_via_algorithm(sentences_data, max_sec=None):
    scenes = []
    curr_scene = []
    for item in sentences_data:
        if not curr_scene:
            curr_scene.append(item)
        else:
            scene_timeline_duration = item['end'] - curr_scene[0]['start']
            if max_sec is not None and scene_timeline_duration > max_sec:
                scenes.append(curr_scene)
                curr_scene = [item]
            else:
                curr_scene.append(item)
    if curr_scene:
        scenes.append(curr_scene)
    return scenes


def build_scene_limit_prompt(max_sec, min_sec):
    limit_lines = []
    if max_sec:
        limit_lines.append(f"- Mỗi cảnh KHÔNG ĐƯỢC VƯỢT QUÁ {max_sec} GIÂY.")
    if min_sec:
        limit_lines.append(f"- Mỗi cảnh nên có tối thiểu khoảng {min_sec} GIÂY nếu mạch nội dung cho phép gộp.")
    if not limit_lines:
        limit_lines.append("- Không có giới hạn thời lượng cứng, hãy tự do phân chia theo mạch nội dung liền mạch nhất.")
    return "\n".join(limit_lines) + "\n"


def align_scenes_via_llm_api(api_client, sentences_data, full_text, max_sec=None, min_sec=None, status_cb=None, stop_check=None):
    limit_prompt = build_scene_limit_prompt(max_sec, min_sec)
    total_sentences = len(sentences_data)
    sentence_lines = [f"{s['id']}. [{round(s['duration'], 2)}s]: {s['text']}" for s in sentences_data]

    if status_cb:
        status_cb(f"[API/{getattr(api_client, 'model', '?')}] Đang gửi phân tích cảnh / Sending scene analysis...", "orange")

    prompt_payload = (
        "Bạn là Đạo diễn hình ảnh và Biên kịch chuyên nghiệp.\n"
        "Nhiệm vụ: Đọc danh sách câu dưới đây để phân chia thành các ĐOẠN/CẢNH hợp lý.\n\n"
        "=== DANH SÁCH CÂU (STT | thời lượng | nội dung) ===\n" + "\n".join(sentence_lines) + "\n\n"
        "=== QUY TẮC BẮT BUỘC ===\n"
        "1. Gom các câu cùng bối cảnh, sự việc vào chung 1 Cảnh.\n" + limit_prompt +
        "2. CHỈ trả về DUY NHẤT một mảng JSON, không markdown, không giải thích, không code block.\n"
        "   Định dạng CHÍNH XÁC: [[start1,end1],[start2,end2],...]\n"
        f"   Ví dụ với {total_sentences} câu: [[1,3],[4,4],[5,{total_sentences}]]\n"
        f"   Mỗi cặp số là STT câu đầu và STT câu cuối của 1 cảnh. Bắt buộc từ 1 đến {total_sentences}.\n"
    )
    # Timeout ngắn + tiến độ từng giây: người dùng thấy đang chờ bao lâu
    # và ngắt được ngay bằng nút Dừng. (Bounded timeout + per-second progress.)
    model_tag = getattr(api_client, "model", "?")

    def report(elapsed, nbytes=0):
        got = f" · đã nhận {max(1, nbytes // 1024)} KB" if nbytes >= 1024 else ""
        status_cb(f"[API/{model_tag}] Đang chờ AI trả lời... {elapsed}s{got} · bấm 🛑 để dừng", "orange")

    def on_wait(remaining):
        if status_cb:
            status_cb("[API/%s] Lỗi, thử lại sau %ds (vổ hạn)" % (model_tag, remaining), "red")

    def on_retry(attempt, wait_s, infinite):
        if not status_cb:
            return
        mode = "vô hạn" if infinite else f"còn {max(0, getattr(api_client, 'SCENE_RETRIES', 1) - attempt)} lần"
        status_cb(f"[API/{model_tag}] Lỗi, lần {attempt} - thử lại sau {wait_s}s ({mode})", "red")

    response_text = api_client.chat(prompt_payload,
                                    timeout=getattr(api_client, "SCENE_TIMEOUT", 180),
                                    retries=getattr(api_client, "SCENE_RETRIES", 1),
                                    stop_check=stop_check,
                                    on_progress=report if status_cb else None,
                                    on_retry=on_retry if status_cb else None,
                                    on_wait=on_wait if status_cb else None)

    if status_cb:
        status_cb("[API] Đã nhận phản hồi, đang kiểm tra định dạng / Response received, checking format...", "blue")

    scenes = parse_llm_json_scenes(response_text, sentences_data)
    if not scenes:
        scenes = parse_llm_ranges_to_scenes(response_text, sentences_data)
    return scenes


def align_scenes_via_llm_web(browser_mgr, sentences_data, full_text, max_sec=None, min_sec=None,
                            mode="auto", status_cb=None, allow_algorithm_fallback=True):
    """Gop cảnh qua LLM Web. allow_algorithm_fallback=False: KHONG duoc lặng lẽ
    chạy thuật toán - thất bại thì phải báo lỗi để caller xử lý.
    (Web LLM scene merge; can forbid the silent algorithm fallback.)"""
    llm_name = browser_mgr.get_current_llm()

    if mode == "file":
        supports_file = bool(browser_mgr.get_selectors("file_upload_input"))
        if not supports_file:
            raise Exception(
                f"{llm_name} không hỗ trợ đính kèm file / File upload not supported.\n"
                "Chuyển sang 'Tự động' hoặc 'Nhập trực tiếp'."
            )
        scenes = _align_scenes_via_llm_web_file(browser_mgr, sentences_data, full_text, max_sec, min_sec, status_cb)
        if scenes:
            return scenes
        raise Exception(f"Đã thử đính kèm file cho {llm_name} nhưng không nhận được kết quả hợp lệ. Vui lòng thử lại hoặc đổi sang 'Tự động'.")

    scenes = _align_scenes_via_llm_web_single(browser_mgr, sentences_data, full_text, max_sec, min_sec, status_cb)
    if scenes:
        return scenes
    if not allow_algorithm_fallback:
        raise Exception(f"{llm_name} không trả về kết quả gộp cảnh hợp lệ / No valid scenes returned.")
    return align_scenes_via_algorithm(sentences_data, max_sec)


def _align_scenes_via_llm_web_single(browser_mgr, sentences_data, full_text, max_sec=None, min_sec=None, status_cb=None):
    llm_name = browser_mgr.get_current_llm()
    target_url = LLM_URLS.get(llm_name, "https://chatgpt.com/")
    limit_prompt = build_scene_limit_prompt(max_sec, min_sec)
    total_sentences = len(sentences_data)
    sentence_lines = [f"{s['id']}. [{round(s['duration'], 2)}s]: {s['text']}" for s in sentences_data]

    if status_cb:
        status_cb(f"Đang gửi lên {llm_name}... / Sending to {llm_name}...", "orange")

    prompt_payload = (
        "Bạn là Đạo diễn hình ảnh và Biên kịch chuyên nghiệp.\n"
        "Nhiệm vụ: Đọc danh sách câu dưới đây để phân chia thành các ĐOẠN/CẢNH hợp lý.\n\n"
        "=== DANH SÁCH CÂU (STT | thời lượng | nội dung) ===\n" + "\n".join(sentence_lines) + "\n\n"
        "=== QUY TẮC BẮT BUỘC ===\n"
        "1. Gom các câu cùng bối cảnh, sự việc vào chung 1 Cảnh.\n" + limit_prompt +
        "2. CHỈ trả về DUY NHẤT một mảng JSON, không markdown, không giải thích, không code block.\n"
        "   Định dạng CHÍNH XÁC: [[start1,end1],[start2,end2],...]\n"
        f"   Ví dụ với {total_sentences} câu: [[1,3],[4,4],[5,{total_sentences}]]\n"
        f"   Bắt buộc bắt đầu từ câu 1 và kết thúc ở câu {total_sentences}, các cặp nối tiếp nhau không bỏ sót STT nào.\n"
    )
    scene_tab, baseline = browser_mgr.inject_text_and_send("scene", target_url, prompt_payload)

    if status_cb:
        status_cb(f"Đang chờ {llm_name} phản hồi... / Waiting for {llm_name} response...", "blue")

    response_text = browser_mgr.wait_for_llm_response("scene", target_url, scene_tab, timeout=180, baseline_count=baseline)
    scenes = parse_llm_json_scenes(response_text, sentences_data)
    if not scenes:
        scenes = parse_llm_ranges_to_scenes(response_text, sentences_data)

    if not scenes and response_text:
        browser_mgr.check_stop("scene")
        if status_cb:
            status_cb(f"{llm_name} trả lời chưa đúng dạng / Response format incorrect, retrying...", "orange")
        retry_note = (
            f"\n\nLƯU Ý: Phản hồi trước chưa đúng định dạng. "
            f"Hãy trả lời LẠI ngay bây giờ, CHỈ một mảng JSON chứa các cặp câu [[start1,end1], [start2,end2], ...] "
            f"từ câu 1 đến câu {total_sentences}, không thêm bất kỳ văn bản hay giải thích nào khác."
        )
        retry_tab, retry_baseline = browser_mgr.inject_text_and_send("scene", target_url, retry_note)
        retry_response = browser_mgr.wait_for_llm_response("scene", target_url, retry_tab, timeout=180, baseline_count=retry_baseline)
        if retry_response:
            scenes = parse_llm_json_scenes(retry_response, sentences_data)
            if not scenes:
                scenes = parse_llm_ranges_to_scenes(retry_response, sentences_data)

    return scenes


def _align_scenes_via_llm_web_file(browser_mgr, sentences_data, full_text, max_sec=None, min_sec=None, status_cb=None):
    llm_name = browser_mgr.get_current_llm()
    target_url = LLM_URLS.get(llm_name, "https://chatgpt.com/")
    limit_prompt = build_scene_limit_prompt(max_sec, min_sec)
    total_sentences = len(sentences_data)
    sentence_lines = [f"{s['id']}. [{round(s['duration'], 2)}s]: {s['text']}" for s in sentences_data]

    if status_cb:
        status_cb(f"Đang đính kèm kịch bản cho {llm_name}... / Attaching script for {llm_name}...", "orange")

    tmp_path = None
    try:
        # File đính kèm CHỈ chứa danh sách câu. Danh sách này đã là toàn bộ
        # kịch bản (mỗi câu đều có STT + thời lượng + nội dung), nên gửi thêm
        # full_text chỉ làm payload đôi lên, tốn tiền API và khiến LLM đọc
        # trùng. Không có ngoại lệ.
        file_content = "=== DANH SÁCH CÂU (STT | thời lượng | nội dung) ===\n" + "\n".join(sentence_lines)
        fd, tmp_path = tempfile.mkstemp(suffix=".txt", prefix="scene_input_")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(file_content)

        upload_ok = browser_mgr.try_upload_file(tmp_path)
        if not upload_ok:
            return _align_scenes_via_llm_web_single(browser_mgr, sentences_data, full_text, max_sec, min_sec, status_cb)

        prompt_payload = (
            "Bạn là Đạo diễn hình ảnh và Biên kịch chuyên nghiệp.\n"
            "Trong file đính kèm là DANH SÁCH CÂU đánh số STT kèm thời lượng từng câu.\n"
            "Nhiệm vụ: đọc file và phân chia các câu thành các ĐOẠN/CẢNH hợp lý.\n\n"
            "=== QUY TẮC BẮT BUỘC ===\n"
            "1. Gom các câu cùng bối cảnh, sự việc vào chung 1 Cảnh.\n" + limit_prompt +
            "2. CHỈ trả về DUY NHẤT một mảng JSON, không markdown, không giải thích, không code block.\n"
            "   Định dạng CHÍNH XÁC: [[start1,end1],[start2,end2],...]\n"
            f"   Ví dụ với {total_sentences} câu: [[1,3],[4,4],[5,{total_sentences}]]\n"
            f"   Mỗi cặp số là STT câu đầu và STT câu cuối của 1 cảnh. Các cặp phải nối tiếp nhau từ 1 đến {total_sentences}.\n"
        )
        scene_tab, baseline = browser_mgr.inject_text_and_send("scene", target_url, prompt_payload)

        if status_cb:
            status_cb(f"Đang chờ {llm_name} phản hồi... / Waiting for {llm_name} response...", "blue")

        response_text = browser_mgr.wait_for_llm_response("scene", target_url, scene_tab, timeout=180, baseline_count=baseline)
        scenes = parse_llm_json_scenes(response_text, sentences_data)
        if not scenes:
            scenes = parse_llm_ranges_to_scenes(response_text, sentences_data)
        return scenes
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def parse_llm_json_scenes(llm_response, sentences_data):
    if not llm_response or not sentences_data:
        return None

    id_map = {s['id']: s for s in sentences_data}
    valid_ids = sorted(id_map.keys())
    if not valid_ids:
        return None
    min_id, max_id = valid_ids[0], valid_ids[-1]

    clean_text = clean_llm_response(llm_response)
    extracted_code = extract_code_block(clean_text)
    candidates = [extracted_code, clean_text]

    raw_ranges = None
    for cand in candidates:
        if not cand:
            continue

        try:
            loaded = json.loads(cand)
            if isinstance(loaded, list) and loaded:
                raw_ranges = loaded
                break
        except Exception:
            pass

        json_matches = re.findall(r'\[\s*\[.*?\]\s*\]', cand, re.DOTALL)
        for jm in reversed(json_matches):
            try:
                loaded = json.loads(jm)
                if isinstance(loaded, list) and loaded:
                    raw_ranges = loaded
                    break
            except Exception:
                try:
                    fixed_jm = re.sub(r',\s*\]', ']', jm)
                    loaded = json.loads(fixed_jm)
                    if isinstance(loaded, list) and loaded:
                        raw_ranges = loaded
                        break
                except Exception:
                    pass
        if raw_ranges:
            break

        pair_matches = re.findall(r'\[\s*(\d+)\s*,\s*(\d+)\s*\]', cand)
        if len(pair_matches) >= 1:
            raw_ranges = [[int(s), int(e)] for s, e in pair_matches]
            break

    if not raw_ranges:
        return None

    parsed_ranges = []
    for pair in raw_ranges:
        if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
            continue
        try:
            s_id, e_id = int(pair[0]), int(pair[1])
            if s_id > e_id:
                s_id, e_id = e_id, s_id
            parsed_ranges.append((s_id, e_id))
        except Exception:
            continue

    if not parsed_ranges:
        return None

    parsed_ranges.sort(key=lambda r: (r[0], r[1]))

    healed_ranges = []
    curr_expected = min_id

    for s_id, e_id in parsed_ranges:
        if e_id < min_id or s_id > max_id:
            continue
        s_id = max(min_id, s_id)
        e_id = min(max_id, e_id)

        if not healed_ranges:
            healed_ranges.append([min_id, max(min_id, e_id)])
            curr_expected = healed_ranges[-1][1] + 1
        else:
            if s_id <= curr_expected - 1:
                if e_id >= curr_expected:
                    healed_ranges.append([curr_expected, e_id])
                    curr_expected = e_id + 1
            elif s_id > curr_expected:
                healed_ranges.append([curr_expected, e_id])
                curr_expected = e_id + 1
            else:
                healed_ranges.append([s_id, e_id])
                curr_expected = e_id + 1

    if not healed_ranges:
        return None

    if healed_ranges[-1][1] < max_id:
        healed_ranges[-1][1] = max_id

    scenes = []
    for s_id, e_id in healed_ranges:
        scene_items = [id_map[i] for i in range(s_id, e_id + 1) if i in id_map]
        if scene_items:
            scenes.append(scene_items)

    return scenes if scenes else None


def parse_llm_ranges_to_scenes(llm_response, sentences_data):
    if not llm_response or not sentences_data:
        return None
    clean_text = clean_llm_response(llm_response)
    id_map = {s['id']: s for s in sentences_data}
    valid_ids = sorted(id_map.keys())
    if not valid_ids:
        return None
    max_id = valid_ids[-1]
    min_id = valid_ids[0]

    raw_pairs = []
    for line in clean_text.splitlines():
        line = line.strip()
        if not line:
            continue
        range_match = re.search(r'(?:câu|stt)?\s*(\d+)\s*(?:[-–—~]|to|đến|\.\.)\s*(?:câu|stt)?\s*(\d+)', line, flags=re.IGNORECASE)
        if range_match:
            try:
                s_id, e_id = int(range_match.group(1)), int(range_match.group(2))
                raw_pairs.append((s_id, e_id))
                continue
            except Exception:
                pass

        single_matches = re.findall(r'(?:câu|stt)\s*(\d+)', line, flags=re.IGNORECASE)
        if single_matches:
            for sm in single_matches:
                try:
                    val = int(sm)
                    raw_pairs.append((val, val))
                except Exception:
                    pass

    if not raw_pairs:
        matches = re.findall(r'(\d+)\s*(?:[-–—~]|to|đến|\.\.)\s*(\d+)', clean_text, flags=re.IGNORECASE)
        for m in matches:
            try:
                raw_pairs.append((int(m[0]), int(m[1])))
            except Exception:
                pass

    if not raw_pairs:
        return None

    parsed_ranges = []
    for s_id, e_id in raw_pairs:
        if s_id in id_map:
            if s_id > e_id:
                s_id, e_id = e_id, s_id
            e_id = min(max(s_id, e_id), max_id)
            parsed_ranges.append((s_id, e_id))

    if not parsed_ranges:
        return None

    parsed_ranges.sort(key=lambda r: (r[0], r[1]))

    healed_ranges = []
    curr_expected = min_id
    for s_id, e_id in parsed_ranges:
        if e_id < min_id or s_id > max_id:
            continue
        s_id = max(min_id, s_id)
        e_id = min(max_id, e_id)

        if not healed_ranges:
            healed_ranges.append([min_id, max(min_id, e_id)])
            curr_expected = healed_ranges[-1][1] + 1
        else:
            if s_id <= curr_expected - 1:
                if e_id >= curr_expected:
                    healed_ranges.append([curr_expected, e_id])
                    curr_expected = e_id + 1
            elif s_id > curr_expected:
                healed_ranges.append([curr_expected, e_id])
                curr_expected = e_id + 1
            else:
                healed_ranges.append([s_id, e_id])
                curr_expected = e_id + 1

    if not healed_ranges:
        return None

    if healed_ranges[-1][1] < max_id:
        healed_ranges[-1][1] = max_id

    scenes = []
    for s_id, e_id in healed_ranges:
        scene_items = [id_map[i] for i in range(s_id, e_id + 1) if i in id_map]
        if scene_items:
            scenes.append(scene_items)

    return scenes if scenes else None


def render_scenes_to_srt(scenes):
    """
    Ghép các câu của mỗi cảnh thành 1 khối SRT.
    Dấu . giữa các câu -> thay bằng ; ; câu CUỐI CÙNG của cảnh giữ dấu .
    """
    srt_result = ""
    scene_number = 0
    for scene in scenes:
        valid_items = [itm for itm in scene if has_meaningful_text(itm['text'])]
        if not valid_items:
            continue

        cleaned = []
        for itm in valid_items:
            t = re.sub(r'\s+', ' ', itm['text'].strip())
            t = re.sub(r'[.!?。！？]+(["\'”’»)\]]*)\s*$', r'\1', t).strip()
            if has_meaningful_text(t):
                cleaned.append(t)

        if not cleaned:
            continue

        joined_texts = []
        last_idx = len(cleaned) - 1
        for i, t in enumerate(cleaned):
            joined_texts.append(t + ("; " if i < last_idx else "."))

        scene_text = "".join(joined_texts).strip()
        if not has_meaningful_text(scene_text):
            continue

        scene_number += 1
        st = valid_items[0]['start']
        et = valid_items[-1]['end']
        srt_result += f"{scene_number}\n{format_timestamp(st)} --> {format_timestamp(et)}\n{scene_text}\n\n"
    return srt_result
