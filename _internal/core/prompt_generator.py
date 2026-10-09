# -*- coding: utf-8 -*-
"""
Bước 4: Tự động sinh Prompt ảnh qua API hoặc Web Selenium
"""
import json
import re
import time
from utils.text_utils import clean_llm_response, extract_code_block
from utils.exceptions import StopRun, LLMApiError
from config.settings import LLM_URLS


def parse_llm_json_prompts(llm_response, expected_min_id, expected_max_id):
    """
    Phân tích kết quả JSON prompt trả về từ LLM (hỗ trợ code block,
    auto-healing STT, danh sách đánh số line-by-line).
    """
    if not llm_response:
        return None

    clean_text = clean_llm_response(llm_response)
    extracted = extract_code_block(clean_text)
    candidates = [extracted, clean_text]

    data = None
    for cand in candidates:
        if not cand:
            continue
        try:
            loaded = json.loads(cand)
            if isinstance(loaded, list) and loaded:
                data = loaded
                break
        except Exception:
            pass

        # Tìm chuỗi mảng JSON từ dấu [ đầu tiên đến ] cuối cùng
        first_bracket = cand.find('[')
        last_bracket = cand.rfind(']')
        if first_bracket != -1 and last_bracket != -1 and last_bracket > first_bracket:
            arr_str = cand[first_bracket:last_bracket + 1]
            try:
                fixed = re.sub(r',\s*\}', '}', arr_str)
                fixed = re.sub(r',\s*\]', ']', fixed)
                loaded = json.loads(fixed)
                if isinstance(loaded, list) and loaded:
                    data = loaded
                    break
            except Exception:
                pass

        # Trích xuất từng object { ... }
        obj_matches = re.findall(r'\{\s*(?:[^{}]*?)\s*\}', cand, re.DOTALL)
        if obj_matches:
            extracted_objs = []
            for om in obj_matches:
                try:
                    fixed_om = re.sub(r',\s*\}', '}', om)
                    obj = json.loads(fixed_om)
                    if isinstance(obj, dict):
                        extracted_objs.append(obj)
                except Exception:
                    id_m = re.search(r'"(?:id|stt|câu|num)":\s*(\d+)', om, re.IGNORECASE)
                    pr_m = re.search(r'"(?:prompt|description|text|content)":\s*"([^"]+)"', om, re.IGNORECASE)
                    if pr_m:
                        extracted_objs.append({
                            "id": int(id_m.group(1)) if id_m else None,
                            "prompt": pr_m.group(1)
                        })
            if extracted_objs:
                data = extracted_objs
                break

    prompt_map = {}
    ordered_prompts = []

    if isinstance(data, list):
        for idx, item in enumerate(data):
            if isinstance(item, dict):
                pid = None
                for k in ["id", "ID", "stt", "STT", "câu", "index", "num", "no"]:
                    if k in item and item[k] is not None:
                        try:
                            pid = int(item[k])
                            break
                        except Exception:
                            pass

                p_text = None
                for pk in ["prompt", "Prompt", "PROMPT", "image_prompt", "description", "text", "content", "result"]:
                    if pk in item and item[pk] and str(item[pk]).strip():
                        p_text = str(item[pk]).strip()
                        break

                if p_text:
                    if pid is not None:
                        prompt_map[pid] = p_text
                    ordered_prompts.append(p_text)
            elif isinstance(item, str) and item.strip():
                ordered_prompts.append(item.strip())

    expected_ids = list(range(expected_min_id, expected_max_id + 1))
    expected_set = set(expected_ids)
    actual_set = set(prompt_map.keys())

    if expected_set.issubset(actual_set):
        return [prompt_map[pid] for pid in expected_ids]

    reindexed_set = set(range(1, len(expected_ids) + 1))
    if reindexed_set.issubset(actual_set):
        return [prompt_map[i] for i in range(1, len(expected_ids) + 1)]

    if len(ordered_prompts) >= len(expected_ids):
        return ordered_prompts[:len(expected_ids)]

    # Fallback theo từng dòng (1. A scenic view... hoặc Câu 1: ...)
    line_map = {}
    line_ordered = []
    for line in clean_text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r'^(?:câu|stt|id)?\s*(\d+)[\.:\-\)]\s*(.+)', line, re.IGNORECASE)
        if m:
            try:
                lid = int(m.group(1))
                ltxt = m.group(2).strip()
                if ltxt:
                    line_map[lid] = ltxt
                    line_ordered.append(ltxt)
            except Exception:
                pass

    if expected_set.issubset(set(line_map.keys())):
        return [line_map[pid] for pid in expected_ids]
    if reindexed_set.issubset(set(line_map.keys())):
        return [line_map[i] for i in range(1, len(expected_ids) + 1)]
    if len(line_ordered) >= len(expected_ids):
        return line_ordered[:len(expected_ids)]

    return None


class BatchFailure(Exception):
    """Chạy được một pha rồi hỏng, không được nhiều hơn.
    completed = số câu ĐÃ có prompt, để gọi lại đúng chỗ còn thiếu.
    (Partial run: carries progress so the caller can resume where it stopped.)"""

    def __init__(self, completed, last_error=""):
        super().__init__(last_error or f"Chỉ tạo được {completed} câu")
        self.completed = completed
        self.last_error = last_error


MIN_BATCH = 10          # khong duoi 10 prompt/lan
SHRINK_STEP = 10        # giam 10 prompt/lan khi nhieu lan loi lien tiep
# So lan thu that bai LIEN TIEP truoc khi rut ngan lot. Logic vong lap vo han
# cua tang recovery giu nguyen: rut ngan cho den khi ve MIN_BATCH roi moi nem loi.
SHRINK_RETRY_ATTEMPTS = 3


def _looks_too_long(text):
    """Phan hoi bi cat / qua dai khong giu duoc so luong prompt yeu cau.
    (True when the reply looks truncated or over the token budget.)"""
    low = (text or "").lower()
    return any(k in low for k in (
        "finish_reason=length", "hết ngân sách token", "max_tokens",
        "too long", "context length", "quá dài", "token limit"))


def _wait_before_retry(attempt, status_cb, prefix, stop_check):
    """Nghỉ có lập giữa 2 lần thử, nghỉ dài hơn mỗi lần (tối đa 30s)."""
    seconds = min(3 * attempt, 30)
    if status_cb:
        status_cb(f"{prefix} lần {attempt} - chờ {seconds}s rồi thử lại...", "red")
    if not _interruptible_sleep(seconds, stop_check):
        raise StopRun()


def _resolve_max_attempts(value):
    """None / 'inf' = vô hạn; còn lại là số nguyên >= 1."""
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in ("inf", "infinite", "vô hạn", "vo han", ""):
        return None
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 2


def _interruptible_sleep(seconds, stop_check=None):
    """Ngủ giữa hai lần thử lại nhưng vẫn đáp ứng nút Dừng ngay lập tức.
    Trả về True nếu ngủ đủ, False nếu người dùng bấm Dừng.
    (Interruptible retry wait so the Stop button is honored.)"""
    for _ in range(int(seconds)):
        if stop_check is not None and stop_check():
            return False
        time.sleep(1)
    return True


def run_api_prompt_generation(api_client, script_lines, batch_size, style_prefix,
                              status_cb=None, append_cb=None, check_stop_fn=None,
                              stop_check=None,
                              max_attempts=2, resume_from=0, on_batch_shrink=None,
                              on_error=None):
    """Sinh prompt theo lô. Lô KHÔNG cố định: nếu phản hồi quá dài (bị cắt) thì
    thử 3 lần rồi rút ngắn 10 prompt/lần cho tới 10.
    Cho phép chạy lại từ câu `resume_from`; max_attempts=None = vô hạn.
    (Adaptive batching: shrinks the batch when replies come back truncated.)"""
    limit = _resolve_max_attempts(max_attempts)
    total = len(script_lines)
    size = max(MIN_BATCH, int(batch_size or MIN_BATCH))
    done = int(resume_from or 0)

    prompt_tmpl = (
        "Bạn là chuyên gia Prompt Engineer cho AI vẽ ảnh.\n"
        "Nhiệm vụ: Viết 1 Prompt tiếng Anh chi tiết tả khung cảnh hình ảnh tương ứng với MỖI CÂU dưới đây.\n"
        "QUY TẮC:\n"
        f"1. Tiền tố Style: {style_prefix}\n"
        "2. CHỈ trả về DUY NHẤT một mảng JSON, không markdown, không giải thích, không code block.\n"
        "   Định dạng CHÍNH XÁC: [{\"id\": <STT số nguyên>, \"prompt\": \"<nội dung prompt tiếng Anh>\"}, ...]\n"
        "   PHẢI có ĐỦ 1 phần tử cho MỖI STT trong danh sách câu bên dưới, không bỏ sót, không trùng STT.\n\n"
    )

    order = 0
    while done < total:
        chunk = script_lines[done:done + size]
        b_start, b_end = done + 1, done + len(chunk)
        batch_text = "\n".join(["%d. %s" % (i, line) for i, line in
                                 enumerate(chunk, start=b_start)])
        order += 1
        if check_stop_fn:
            check_stop_fn("prompt")
        # KHONG goi rotate_fn o day: doi model chi lam khi API that bai, do
        # on_error/rotate_after_failure lo. Xoay o moi lot se chay model khac
        # ngay ca khi moi lai khong co loi nao.

        def report(elapsed, nbytes=0):
            if status_cb:
                got = " · đã nhận %d KB" % (max(1, nbytes // 1024)) if nbytes >= 1024 else ""
                status_cb("[API] %s: câu %d-%d, đã chờ %ss%s · bấm 🛑 để dừng"
                          % (api_client.model, b_start, b_end, elapsed, got), "orange")

        def on_wait(remaining):
            if status_cb:
                status_cb("[API] Lỗi, thử lại sau %ds (vô hạn)" % remaining, "red")

        def on_retry(attempt, wait_s, infinite):
            if status_cb:
                mode = "vô hạn" if infinite else "sắp hết số lần thử"
                status_cb("[API] Lỗi lần %d - thử lại sau %ds (%s)"
                          % (attempt, wait_s, mode), "red")

        attempt = 0
        too_long = False
        succeeded = False
        last_error = ""
        while True:
            attempt += 1
            if stop_check is not None and stop_check():
                raise StopRun()
            if check_stop_fn:
                check_stop_fn("prompt")
            if status_cb:
                tag = "%d" % attempt if limit is None else "%d/%d" % (attempt, limit)
                status_cb("[API] %s: sinh câu %d-%d · %d prompt/lần (lần %s)"
                          % (api_client.model, b_start, b_end, len(chunk), tag), "orange")
            try:
                response = api_client.chat(prompt_tmpl + batch_text, stop_check=stop_check,
                                          on_progress=report if status_cb else None,
                                          on_retry=on_retry if status_cb else None,
                                          on_wait=on_wait if status_cb else None)
            except LLMApiError as exc:
                last_error = str(exc)
                too_long = too_long or _looks_too_long(last_error)
                if on_error is not None:
                    try:
                        on_error(last_error)
                    except Exception:
                        pass
                if (too_long or attempt >= SHRINK_RETRY_ATTEMPTS) \
                    and len(chunk) > MIN_BATCH:
                    break
                if limit is not None and attempt >= limit:
                    raise BatchFailure(done, last_error)
                _wait_before_retry(attempt, status_cb, "[API] API lỗi:", stop_check)
                continue
            except StopRun:
                raise

            parsed_lines = parse_llm_json_prompts(response, b_start, b_end)
            if parsed_lines is not None:
                if append_cb:
                    append_cb("\n".join(parsed_lines))
                done = b_end
                succeeded = True
                break

            # Sai định dạng: thường do phản hồi bị cắt cụt -> ưu tiên thu nhỏ lô.
            last_error = "trả lời sai định dạng (thiếu/trùng STT %d-%d)" % (b_start, b_end)
            too_long = True
            if len(chunk) > MIN_BATCH:
                break
            if limit is not None and attempt >= limit:
                raise BatchFailure(done, last_error)
            _wait_before_retry(attempt, status_cb, "[API] Sai định dạng:", stop_check)

        if succeeded:
            continue
        # 3 lan that bai lien tiep -> rut ngan lot SHRINK_STEP=10 prompt/lan (toi thieu 10)
        # roi thu lai CHINH doan nay. Vong lap vo han cua tang recovery giu nguyen.
        if len(chunk) > MIN_BATCH:
            size = max(MIN_BATCH, len(chunk) - SHRINK_STEP)
            if status_cb:
                status_cb("[API] %d lan that bai (%s) - rut ngan con %d prompt/lan"
                          % (SHRINK_RETRY_ATTEMPTS, last_error[:60], size), "red")
            if on_batch_shrink:
                on_batch_shrink(size)
            continue
        raise BatchFailure(done, last_error)
    return done


def run_selenium_prompt_generation(browser_mgr, script_lines, batch_size, style_prefix,
                                    status_cb=None, append_cb=None,
                                    max_attempts=2, resume_from=0, on_batch_shrink=None):
    """Sinh prompt qua LLM Web (Selenium). Cùng cơ chế như API: rút ngắn 10 prompt/lần
    khi phản hồi quá dài (tối thiểu 10), và chỉ gửi câu CHƯA có prompt.
    (Web LLM prompt generation with the same adaptive batching.)"""
    limit = _resolve_max_attempts(max_attempts)
    llm_name = browser_mgr.get_current_llm()
    target_url = LLM_URLS.get(llm_name, "https://chatgpt.com/")

    prompt_tmpl = (
        "Bạn là chuyên gia Prompt Engineer cho AI vẽ ảnh.\n"
        "Nhiệm vụ: Viết 1 Prompt tiếng Anh chi tiết tả khung cảnh hình ảnh tương ứng với MỖI CÂU dưới đây.\n"
        "QUY TẮC:\n"
        f"1. Tiền tố Style: {style_prefix}\n"
        "2. CHỈ trả về DUY NHẤT một mảng JSON, không markdown, không giải thích, không code block.\n"
        "   Định dạng CHÍNH XÁC: [{\"id\": <STT số nguyên>, \"prompt\": \"<nội dung prompt tiếng Anh>\"}, ...]\n"
        "   PHẢI có ĐỦ 1 phần tử cho MỖI STT trong danh sách câu bên dưới, không bỏ sót, không trùng STT.\n\n"
    )

    total = len(script_lines)
    size = max(MIN_BATCH, int(batch_size or MIN_BATCH))
    done = int(resume_from or 0)
    retry_note = (
        "\n\nLƯU Ý: Lần trước bạn trả lời SAI ĐỊNH DẠNG hoặc THIẾU/TRÙNG STT. "
        "Hãy trả lời LẠI ngay bây giờ, CHỈ một mảng JSON, ĐỦ ĐÚNG các STT được yêu cầu, "
        "không thiếu, không trùng, không thêm chữ nào khác."
    )

    while done < total:
        chunk = script_lines[done:done + size]
        b_start, b_end = done + 1, done + len(chunk)
        batch_text = "\n".join(["%d. %s" % (i, line) for i, line in
                                 enumerate(chunk, start=b_start)])
        attempt = 0
        succeeded = False
        last_error = ""
        while True:
            attempt += 1
            browser_mgr.check_stop("prompt")
            if status_cb:
                tag = "%d" % attempt if limit is None else "%d/%d" % (attempt, limit)
                status_cb("[Web] %s: câu %d-%d · %d prompt/lần (lần %s)"
                          % (llm_name, b_start, b_end, len(chunk), tag), "orange")
            prompt_tab, baseline = browser_mgr.inject_text_and_send("prompt", target_url, prompt_tmpl + batch_text)
            if status_cb:
                status_cb("[Web] Đang chờ %s trả lời câu %d-%d..." % (llm_name, b_start, b_end), "blue")
            response = browser_mgr.wait_for_llm_response("prompt", target_url, prompt_tab,
                                                         timeout=180, baseline_count=baseline)
            parsed_lines = parse_llm_json_prompts(response, b_start, b_end) if response else None
            if parsed_lines is None:
                browser_mgr.check_stop("prompt")
                last_error = "%s trả lời rỗng hoặc sai định dạng (STT %d-%d)" % (llm_name, b_start, b_end)
                if attempt >= SHRINK_RETRY_ATTEMPTS and len(chunk) > MIN_BATCH:
                    break
                retry_tab, retry_baseline = browser_mgr.inject_text_and_send("prompt", target_url, retry_note)
                retry_response = browser_mgr.wait_for_llm_response("prompt", target_url, retry_tab,
                                                                   timeout=180, baseline_count=retry_baseline)
                parsed_lines = parse_llm_json_prompts(retry_response, b_start, b_end) if retry_response else None
            if parsed_lines is not None:
                if append_cb:
                    append_cb("\n".join(parsed_lines))
                done = b_end
                succeeded = True
                break
            if limit is not None and attempt >= limit:
                raise BatchFailure(done, last_error)
            if status_cb:
                status_cb("[Web] %s lỗi - đóng Chrome và mở lại từ đầu, thử lại lần %d"
                          % (llm_name, attempt + 1), "red")
            # Web: đóng hẳn Chrome rồi mở lại từ đầu. Dùng lại cửa sổ cũ đang treo
            # sẽ dán chồng prompt vào ô chứa đầy -> quay vô hạn.
            browser_mgr.restart_chrome()
            if not _interruptible_sleep(min(3 * attempt, 30), browser_mgr.stop_event.is_set):
                raise StopRun()

        if succeeded:
            time.sleep(2)
            continue
        if len(chunk) > MIN_BATCH:
            size = max(MIN_BATCH, len(chunk) - SHRINK_STEP)
            if status_cb:
                status_cb("[Web] %d lan that bai (%s) - rut ngan con %d prompt/lan"
                          % (SHRINK_RETRY_ATTEMPTS, last_error[:60], size), "red")
            if on_batch_shrink:
                on_batch_shrink(size)
            continue
        raise BatchFailure(done, last_error)
    return done
