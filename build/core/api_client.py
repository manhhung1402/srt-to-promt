# -*- coding: utf-8 -*-
"""
Client gọi AI qua bất kỳ API tương thích chuẩn OpenAI (OpenAI-compatible):
OpenRouter, 9router, OpenAI, Ollama, LM Studio, ... Chỉ cần Base URL + API Key + Model.
(Works with any OpenAI-compatible endpoint: base URL + API key + model.)
"""
import json
import os
import threading
import time
import requests
from config.settings import API_CONFIG, API_ERROR_VI
from utils.exceptions import LLMApiError, StopRun


class Reply:
    """Kết quả đã đọc hết body, giữ mặt nạ giống requests.Response
    (status_code / text / content / headers) để các hàm parse dùng chung.
    (Minimal stand-in for requests.Response so parsers stay uniform.)"""

    def __init__(self, status_code, text, headers=None):
        self.status_code = status_code
        self.text = text or ""
        self.headers = headers if headers is not None else {}

    @property
    def content(self):
        return self.text.encode("utf-8", "replace")


RETRY_CHOICES = ("2", "4", "6", "inf")


class OpenAICompatClient:
    """Gửi chat completion tới {base_url}/chat/completions. Tự retry lỗi tạm thời (429/5xx)."""

    CONNECT_TIMEOUT = 10      # giây bắt được tay nối TCP/HTTP
    TIMEOUT = 240             # giây chờ dữ liệu cho 1 lần đọc (mỗi chunk được tính lại)
    MAX_RETRIES = 2           # số lần thử với lỗi tạm thời
    SCENE_TIMEOUT = 180       # Bước 2: không để người dùng chờ vô hạn
    SCENE_RETRIES = 1
    RETRYABLE = (408, 409, 429, 500, 502, 503, 504)
    RETRY_INFINITE = 10 ** 9   # "vo han": thu lai lien tuc cho den khi nguoi dung Dung

    def set_retry_policy(self, choice):
        """Dat so lan thu theo lua chon cua nguoi dung: '2' | '4' | '6' | 'inf'.
        (Set retry attempts from the UI box; 'inf' retries until the user stops.)"""
        text = str(choice or "").strip().lower()
        if text in ("inf", "infinite", "vô hạn", "vo han", "∞", ""):
            self.MAX_RETRIES = self.RETRY_INFINITE
            self.SCENE_RETRIES = self.RETRY_INFINITE
            return "inf"
        try:
            count = max(1, int(text))
        except ValueError:
            count = 2
        self.MAX_RETRIES = count
        self.SCENE_RETRIES = count
        return str(count)

    def __init__(self, base_url="", api_key="", model=""):
        self.base_url = (base_url or "").strip().rstrip("/") or API_CONFIG["default_base_url"]
        if not self.base_url.lower().startswith(("http://", "https://")):
            raise LLMApiError(f"Base URL không hợp lệ (Invalid Base URL): '{self.base_url}'. Phải bắt đầu bằng http:// hoặc https://")
        self.api_key = (api_key or "").strip() or os.environ.get("LLM_API_KEY", "").strip()
        self.model = (model or "").strip() or API_CONFIG["default_model"]
        self.is_local = any(h in self.base_url for h in ("localhost", "127.0.0.1", "0.0.0.0"))
        if not self.api_key and not self.is_local:
            raise LLMApiError(
                "Chưa nhập API Key!\n\n"
                "Nhập key của nhà cung cấp (OpenRouter, OpenAI...) vào ô 'API Key', bấm '💾 Lưu' rồi '🔌 Kiểm tra'.\n"
                "Với máy chủ nội bộ (localhost như 9router/Ollama) có thể để trống key."
            )

    # ==========================================
    # ĐỌC PHẢN HỘI CHUẨN DUNG THỨ (tolerant parsing)
    # ==========================================
    @staticmethod
    def _decode(raw):
        """Ép đọc UTF-8. Nhiều máy chủ (9router) không khai báo charset,
        requests sẽ mặc định Latin-1 và làm rách tiếng Việt."""
        if isinstance(raw, bytes):
            for enc in ("utf-8", "utf-8-sig"):
                try:
                    return raw.decode(enc)
                except Exception:
                    continue
            return raw.decode("utf-8", "replace")
        return raw if isinstance(raw, str) else ""

    @staticmethod
    def _objects(text):
        """Tách lần lượt mọi object JSON trong một chuỗi: JSON chuẩn, nhiều JSON
        nối liền, JSON nhiều dòng, hoặc SSE 'data: {...}'. Bỏ qua rác parse hỏng."""
        decoder = json.JSONDecoder()
        found = []
        index = 0
        size = len(text or "")
        while index < size:
            brace = text.find("{", index)
            if brace < 0:
                break
            try:
                obj, end = decoder.raw_decode(text, brace)
            except ValueError:
                index = brace + 1
                continue
            if isinstance(obj, dict):
                found.append(obj)
            index = max(end, brace + 1)
        return found

    @classmethod
    def _loads_tolerant(cls, raw):
        """Lấy object JSON đầu tiên hợp lệ (không ném lỗi, trả None nếu vô phương đọc)."""
        objs = cls._objects(cls._decode(raw))
        return objs[0] if objs else None

    @classmethod
    def _json_of(cls, body):
        """body là requests.Response | Reply | str | bytes -> dict/list."""
        raw = body.content if hasattr(body, "content") else body
        data = cls._loads_tolerant(raw)
        if data is None:
            raise ValueError("Không đọc được JSON từ phản hồi máy chủ")
        return data

    @staticmethod
    def _extract_content(body):
        """Đọc nhiều biến thể response của API OpenAI-compatible."""
        if not isinstance(body, dict):
            return ""
        direct = body.get("output_text") or body.get("response")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()
        choices = body.get("choices") or []
        if not isinstance(choices, list):
            return ""
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
            delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
            values = [choice.get("text"), choice.get("content"),
                      message.get("content"), message.get("text"),
                      message.get("reasoning_content"), message.get("reasoning"),
                      delta.get("content"), delta.get("reasoning_content")]
            for value in values:
                if isinstance(value, str) and value.strip():
                    return value.strip()
                if isinstance(value, list):
                    parts = []
                    for part in value:
                        if isinstance(part, str):
                            parts.append(part)
                        elif isinstance(part, dict) and isinstance(part.get("text"), str):
                            parts.append(part["text"])
                    joined = "".join(parts).strip()
                    if joined:
                        return joined
        return ""

    @classmethod
    def _read_completion(cls, raw):
        """Body bất kỳ -> (nội dung, finish_reason).
        Stream thì nối các mẩu; nhiều object đầy đủ thì lấy bản dài nhất."""
        objs = cls._objects(cls._decode(raw))
        finish = ""
        is_stream = False
        pieces = []
        for obj in objs:
            text = cls._extract_content(obj)
            if text:
                pieces.append(text)
            for choice in (obj.get("choices") or []):
                if isinstance(choice, dict):
                    if choice.get("delta") is not None:
                        is_stream = True
                    if choice.get("finish_reason"):
                        finish = choice["finish_reason"]
        if not pieces:
            return "", finish
        if len(pieces) == 1:
            return pieces[0].strip(), finish
        return ("".join(pieces) if is_stream else max(pieces, key=len)).strip(), finish

    # ==========================================
    # GỌI API
    # ==========================================
    def _headers(self):
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if "openrouter.ai" in self.base_url:
            headers["HTTP-Referer"] = "https://localhost/video-workflow-tool"
            headers["X-Title"] = "Video Workflow Tool"
        return headers

    def _url(self, path):
        return f"{self.base_url}/{path.lstrip('/')}"

    @staticmethod
    def _friendly(status, detail=""):
        """Lỗi máy chủ -> câu tiếng Việt hướng dẫn cách xử lý."""
        low = (detail or "").lower()
        if "platform credits" in low or "purchase credits" in low or "no credit" in low \
                or "deposit required" in low or ("insufficient" in low and "credit" in low):
            return ("TÀI KHOẢN API KHÔNG CÒN CREDIT / hết lượt trả phí.\n"
                    "   Máy chủ báo: nạp thêm credit ở trang billing của nhà cung cấp,\n"
                    "   hoặc chọn model miễn phí khác (bấm 'Test Model Này' để kiểm tra trước khi chạy).")
        base = API_ERROR_VI.get(status, f"Lỗi HTTP {status} từ máy chủ API.")
        return f"{base}\n\n(Chi tiết kỹ thuật / Technical detail: {str(detail)[:300]})" if detail else base

    def _extract_error(self, resp):
        try:
            body = self._json_of(resp)
            err = body.get("error") if isinstance(body, dict) else None
            if isinstance(err, dict):
                return str(err.get("message") or err.get("code") or err)
            if err:
                return str(err)
            if isinstance(body, dict):
                return str(body.get("message") or body.get("detail") or err or "")
        except Exception:
            pass
        text = (getattr(resp, "text", "") or "").strip()
        return text[:300] if text else "(máy chủ trả về thân rỗng / empty body)"

    @staticmethod
    def _retry_after_seconds(resp):
        try:
            val = resp.headers.get("Retry-After")
            return max(0, int(float(val))) if val else 0
        except Exception:
            return 0

    @staticmethod
    def _is_retryable(status, detail):
        """Lỗi hết credit/thanh toán không phải lỗi tạm thời -> không retry,
        dù máy chủ trả 429 hay 503 (9router thường gán 429 thành 503)."""
        low = (detail or "").lower()
        fatal = ("platform credits", "purchase credits", "billing", "insufficient", "no credit",
                 "deposit required", "requires_purchase", "access_denied")
        if any(k in low for k in fatal):
            return False
        return True

    @staticmethod
    def _sleep(seconds, stop_check=None, on_tick=None):
        """Ngủ giữa 2 lần thử, ngắt ngay khi người dùng bấm Dừng. True = bị dừng.
        on_tick(elapsed) được gọi mỗi giây để thanh trạng thái vẫn chạy,
        không bị đứng yên trong lúc chờ.
        (Interruptible wait; ticks every second so the status line keeps moving.)"""
        deadline = time.time() + max(0.0, float(seconds))
        started = time.time()
        last_tick = -1
        while True:
            left = deadline - time.time()
            if left <= 0:
                return False
            if stop_check is not None and stop_check():
                return True
            if on_tick is not None:
                elapsed = int(time.time() - started)
                if elapsed != last_tick:
                    last_tick = elapsed
                    try:
                        on_tick(elapsed)
                    except Exception:
                        pass
            time.sleep(min(0.5, left))

    def _request(self, payload, timeout=None, connect_timeout=None, stop_check=None, on_progress=None):
        """POST /chat/completions và đọc dòng (stream) để:
        - báo tiến độ thật (số giây đã chờ, số byte đã nhận) thay vì ngồi im;
        - ngắt ngay khi bấm Dừng (đóng socket);
        - không timeout oan khi máy chủ trả dữ liệu chậm nhưng đều.
        Trả về Reply. Ném LLMApiError (lỗi mạng) / StopRun (người dùng dừng)."""
        budget = self.TIMEOUT if timeout is None else timeout
        conn = self.CONNECT_TIMEOUT if connect_timeout is None else connect_timeout
        try:
            resp = requests.post(self._url("chat/completions"), headers=self._headers(),
                                 json=payload, timeout=(conn, budget), stream=True)
        except requests.exceptions.Timeout:
            raise LLMApiError(
                f"Máy chủ API không trả đủ dữ liệu trong {budget}s (Timeout).\n"
                "Model có thể đang suy nghĩ quá lâu hoặc đang nghẽn: thử model nhanh hơn, "
                "giảm số câu mỗi lần chạy, hoặc bấm chạy lại.")
        except requests.exceptions.RequestException as exc:
            raise LLMApiError(f"Lỗi kết nối tới {self.base_url} (Connection error): {exc}")

        state = {"secs": 0, "bytes": 0, "abort": False}
        done = threading.Event()

        def heartbeat():
            while not done.wait(1.0):
                state["secs"] += 1
                if on_progress is not None:
                    try:
                        on_progress(state["secs"], state["bytes"])
                    except Exception:
                        pass
                if stop_check is not None:
                    try:
                        if stop_check():
                            state["abort"] = True
                            resp.close()
                            return
                    except Exception:
                        pass

        worker = None
        if on_progress is not None or stop_check is not None:
            worker = threading.Thread(target=heartbeat, daemon=True)
            worker.start()
        chunks = []
        status = 0
        headers = {}
        try:
            status = resp.status_code
            headers = resp.headers
            for chunk in resp.iter_content(8192):
                if chunk:
                    chunks.append(chunk)
                    state["bytes"] += len(chunk)
        except Exception as exc:
            if state["abort"]:
                raise StopRun()
            raise LLMApiError(f"Mất kết nối khi đang đọc phản hồi từ {self.base_url} (Stream error): {exc}")
        finally:
            done.set()
            try:
                resp.close()
            except Exception:
                pass
        if state["abort"]:
            raise StopRun()
        return Reply(status, self._decode(b"".join(chunks)), headers)

    def chat(self, prompt, temperature=0.7, max_tokens=8192, timeout=None, retries=None,
             connect_timeout=None, stop_check=None, on_progress=None, on_retry=None, on_wait=None):
        """Gửi prompt, trả về text. Ném LLMApiError (thông báo tiếng Việt) / StopRun."""
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        last_detail = "?"
        max_tries = self.MAX_RETRIES if retries is None else max(1, int(retries))
        self._infinite_retries = max_tries > 1000
        attempt = 0
        while attempt < max_tries:
            attempt += 1
            if stop_check is not None and stop_check():
                raise StopRun()
            resp = self._request(payload, timeout=timeout, connect_timeout=connect_timeout,
                                 stop_check=stop_check, on_progress=on_progress)

            if resp.status_code == 200:
                content, finish = self._read_completion(resp.content)
                if content:
                    return content
                hint = ""
                if finish in ("length", "max_tokens"):
                    hint = ("\nMáy chủ báo finish_reason=length: model hết ngân sách token trước khi "
                            "trả lời (thường gặp với model reasoning). Chọn model khác hoặc giảm số câu.")
                raise LLMApiError(
                    f"Máy chủ trả dữ liệu nhưng không có nội dung tương thích OpenAI "
                    f"(Empty/Unsupported response from {self.base_url}).{hint}\n"
                    f"Phản hồi: {resp.text[:400]}"
                )

            detail = self._extract_error(resp)
            last_detail = detail

            if resp.status_code in self.RETRYABLE and self._is_retryable(resp.status_code, detail) and attempt < max_tries:
                wait_s = min(2 ** attempt * 5, 30) if resp.status_code == 429 else min(2 ** attempt * 2, 20)
                wait_s = min(max(wait_s, self._retry_after_seconds(resp)), 60)
                # Báo đếm ngược khi chờ thử lại, để số giây trên thanh trạng
                # thái không bị đứng yên.
                if on_wait is not None:
                    total = int(wait_s)
                    if on_progress is not None:
                        on_progress(total, 0)
                    self._sleep(wait_s, stop_check,
                                on_tick=lambda e, t=total: on_wait(t - e))
                else:
                    if self._sleep(wait_s, stop_check):
                        raise StopRun()
                if on_retry is not None:
                    try:
                        on_retry(attempt, int(wait_s), self._infinite_retries)
                    except Exception:
                        pass
                continue

            if resp.status_code == 400:
                lowered = detail.lower()
                for key in ("temperature", "max_tokens"):
                    if key in lowered and key in payload:
                        payload.pop(key)
                        attempt -= 1
                        break
                else:
                    raise LLMApiError(self._friendly(400, detail))
                continue

            raise LLMApiError(self._friendly(resp.status_code, detail))
        raise LLMApiError(self._friendly(503, f"Thất bại sau {max_tries} lần thử. {last_detail}"))

    def probe(self, timeout=60, retries=2, max_tokens=256, connect_timeout=None):
        """Test nhanh 1 model. max_tokens rộng + retry ngắn để tránh FAIL giả
        khi model reasoning kéo dài phần nghĩ. Trả về (text, cảnh_báo)."""
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": "Trả lời đúng 1 từ: OK"}],
            "max_tokens": max_tokens,
        }
        last_detail = ""
        status = 503
        for attempt in range(1, max(1, int(retries)) + 1):
            try:
                resp = self._request(payload, timeout=timeout, connect_timeout=connect_timeout)
            except StopRun:
                raise
            except LLMApiError as exc:
                last_detail = str(exc).replace("\n", " ")
                if attempt < retries and self._is_retryable(503, last_detail):
                    self._sleep(2 * attempt)
                    continue
                raise

            if resp.status_code == 200:
                text, finish = self._read_completion(resp.content)
                warns = []
                if not text:
                    if finish in ("length", "max_tokens"):
                        warns.append("Kết nối OK, nhưng model không xuất được chữ nào trong ngân sách token hiện tại.")
                    else:
                        warns.append("Kết nối OK, nhưng model không trả nội dung.")
                return text, warns

            last_detail = self._extract_error(resp)
            status = resp.status_code
            if not self._is_retryable(status, last_detail):
                break
            if attempt < retries:
                self._sleep(min(2 * attempt, 6))

        raise LLMApiError(self._friendly(status, last_detail))

    # ==========================================
    # DANH SÁCH MODEL / COMBO / NHÀ CUNG CẤP
    # ==========================================
    def _get(self, path, timeout=30, absolute=False):
        if absolute:
            scheme, _, rest = self.base_url.partition("//")
            url = scheme + "//" + rest.split("/", 1)[0] + "/" + path.lstrip("/")
        else:
            url = self._url(path)
        return requests.get(url, headers=self._headers(),
                            timeout=(self.CONNECT_TIMEOUT, timeout))

    def list_model_details(self):
        """GET {base_url}/models -> danh sách dict {id, owned_by}."""
        try:
            resp = self._get("models", 60)
        except requests.exceptions.RequestException as e:
            raise LLMApiError(f"Không tải được danh sách model từ {self.base_url} (Connection error): {e}")
        if resp.status_code != 200:
            raise LLMApiError(self._friendly(resp.status_code, self._extract_error(resp)))
        try:
            data = self._json_of(resp)
        except Exception:
            raise LLMApiError(f"Phản hồi /models không phải JSON: {resp.text[:200]}")
        items = data.get("data", data) if isinstance(data, dict) else data
        found = {}
        for item in items or []:
            if isinstance(item, dict):
                mid = item.get("id")
                if mid:
                    found[str(mid)] = str(item.get("owned_by") or "")
            elif item:
                found[str(item)] = ""
        return [{"id": k, "owned_by": v} for k, v in sorted(found.items())]

    def list_models(self):
        """GET {base_url}/models -> danh sách id model (sorted)."""
        return [m["id"] for m in self.list_model_details()]

    def list_combos(self):
        """Đọc /api/combos của 9router -> ({tên combo: [model con]}, lỗi).
        Nhà cung cấp khác không có endpoint này -> trả {} và không coi là lỗi."""
        try:
            resp = self._get("api/combos", 20, absolute=True)
        except Exception as e:
            return {}, f"Không đọc được /api/combos: {e}"
        if resp.status_code == 404:
            return {}, ""
        if resp.status_code != 200:
            return {}, f"/api/combos trả về HTTP {resp.status_code}"
        try:
            combos = self._json_of(resp).get("combos", [])
        except Exception as e:
            return {}, f"/api/combos không phải JSON: {e}"
        result = {}
        for combo in combos or []:
            if not isinstance(combo, dict):
                continue
            name = str(combo.get("name") or "").strip()
            members = [str(m) for m in (combo.get("models") or []) if m]
            if name and members:
                result[name] = members
        if not result:
            return {}, "Endpoint /api/combos trả về rỗng (không có combo nào)."
        return result, ""

    def list_providers(self):
        """GET /api/providers -> {provider_key: status} (chỉ có ở 9router)."""
        try:
            resp = self._get("api/providers", 20, absolute=True)
            if resp.status_code != 200:
                return {}
            conns = self._json_of(resp).get("connections", [])
        except Exception:
            return {}
        out = {}
        for conn in conns or []:
            if isinstance(conn, dict):
                key = str(conn.get("key") or "").strip()
                if key:
                    out[key] = str(conn.get("testStatus") or conn.get("status") or "")
        return out

    def resolve_test_targets(self, model):
        """Model đơn -> [nó]. Combo/điều phối -> toàn bộ model con trong combo.
        (Single model returns itself; a combo expands to its member models.)"""
        combos, _ = self.list_combos()
        if model in combos:
            return combos[model], True
        details = {m["id"]: m["owned_by"] for m in self.list_model_details()}
        if details.get(model, "").lower() == "combo":
            return [], True
        return [model], False

    @staticmethod
    def classify_error(detail):
        """Phân loại lỗi -> (nhóm, nhận xét). nhóm: credit|missing|auth|limit|other."""
        low = (detail or "").lower()
        if any(k in low for k in ("requires_purchase", "platform credits", "insufficient", "purchase credits",
                                  "no credit", "deposit required", "access restricted", "access_denied",
                                  "premium models")):
            return "credit", "Cần nạp tiền / mở gói premium cho model này"
        if "no endpoints available" in low or "is not available" in low:
            return "missing", "Model chưa có endpoint khả dụng"
        if any(k in low for k in ("invalid api key", "unauthorized", "authentication_error", "invalid x-api-key")):
            return "auth", "Key không hợp lệ"
        if any(k in low for k in ("rate limit", "too many requests", "reset after")):
            return "limit", "Vượt giới hạn lượt gọi (tạm thời)"
        return "other", "Lỗi khác"
