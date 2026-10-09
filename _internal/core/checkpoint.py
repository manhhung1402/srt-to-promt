# -*- coding: utf-8 -*-
"""
Checkpoint bền vững cho mỗi folder input.

Vì sao cần: người dùng bấm Auto rồi đi ngủ. Nếu máy khởi động lại, Chrome
crash, hoặc tool bị đóng giữa đêm, sáng dậy folder đó phải tiếp tục ĐÚNG
chỗ đang dở chứ không làm lại từ đầu (mất tiền API) và không báo xong
khi chưa xong (mất tiền tin cậy).

Nguyên tắc: MỘT folder chỉ được coi là xong khi BƯỚC 4 có output.
Không có ngoại lệ.
"""
import json
import os
import time

# Trạng thái bước
STEP_PENDING = "PENDING"
STEP_RUNNING = "RUNNING"
STEP_DONE = "DONE"

# Không bao giờ ghi key/cookie/prompt vào đây.
# Chỉ số thống kê + tên file, đủ để khôi phục mà không rò rỉ bí mật.
_SECRET_KEYS = ("api_key", "apikey", "token", "password", "secret", "cookie",
                "authorization", "prompt", "text", "content", "response")


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


class Checkpoint:
    """Trạng thái 4 bước của MỘT folder. Ghi atom, không để file nửa vời."""

    FILENAME = "_checkpoint.json"

    def __init__(self, folder, path=None):
        self.folder = folder
        self.name = os.path.basename(os.path.normpath(folder))
        self.path = path or os.path.join(folder, self.FILENAME)
        self.data = {
            "folder": self.name,
            "created": _now(),
            "updated": _now(),
            "steps": {str(i): {"status": STEP_PENDING, "attempts": 0,
                               "output": "", "last_error": "",
                               "last_try": "", "category": ""}
                      for i in (1, 2, 3, 4)},
            "completed": False,
            "completed_at": "",
            "provider": "",
            "mode": "",
        }

    # ------------------------------------------------------------------ I/O
    def load(self):
        """Đọc nếu có; file hỏng thì coi như chưa chạy (không crash tool)."""
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                if isinstance(raw, dict) and "steps" in raw:
                    self.data.update(raw)
                    for key in self.data["steps"]:
                        self.data["steps"].setdefault(
                            key, {"status": STEP_PENDING, "attempts": 0,
                                  "output": "", "last_error": "",
                                  "last_try": "", "category": ""})
                    return self
        except Exception:
            pass
        return self

    def save(self):
        """Ghi atom: file tạm -> replace. Không để lại JSON nửa vời."""
        self.data["updated"] = _now()
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass

    # -------------------------------------------------------------- truy vấn
    def status(self, step):
        return self.data["steps"][str(step)]["status"]

    def is_done(self, step):
        return self.status(step) == STEP_DONE

    def output(self, step):
        return self.data["steps"][str(step)]["output"]

    def attempts(self, step):
        return self.data["steps"][str(step)]["attempts"]

    def completed(self):
        return bool(self.data.get("completed"))

    def next_pending_step(self):
        """Bước nhỏ nhất chưa xong (1..4), hoặc None nếu đã đủ 4."""
        for step in (1, 2, 3, 4):
            if not self.is_done(step):
                return step
        return None

    # ---------------------------------------------------------------- cập nhật
    def mark_running(self, step, provider="", mode=""):
        rec = self.data["steps"][str(step)]
        rec["status"] = STEP_RUNNING
        rec["attempts"] = rec.get("attempts", 0) + 1
        rec["last_try"] = _now()
        if provider:
            self.data["provider"] = provider
        if mode:
            self.data["mode"] = mode
        self.save()

    def mark_done(self, step, output=""):
        rec = self.data["steps"][str(step)]
        rec["status"] = STEP_DONE
        rec["output"] = output or rec.get("output", "")
        rec["last_error"] = ""
        self.save()

    def mark_failed(self, step, message, category=""):
        """Ghi nhận lỗi nhưng KHÔNG đổi trạng thái thành hỏng vĩnh viễn.

        Cố ý giữ nguyên: một lần thất bại không có nghĩa folder không bao
        giờ chạy được. Đây là chỗ bản cũ hay bỏ folder.
        """
        rec = self.data["steps"][str(step)]
        rec["status"] = STEP_PENDING
        rec["last_error"] = str(message)[:300]
        rec["last_try"] = _now()
        rec["category"] = category or ""
        self.save()

    def mark_complete(self):
        """Chỉ gọi khi cả 4 bước DONE."""
        if all(self.is_done(i) for i in (1, 2, 3, 4)):
            self.data["completed"] = True
            self.data["completed_at"] = _now()
            self.save()
            return True
        return False

    def reset(self):
        self.data["steps"] = {str(i): {"status": STEP_PENDING, "attempts": 0,
                                        "output": "", "last_error": "",
                                        "last_try": "", "category": ""}
                              for i in (1, 2, 3, 4)}
        self.data["completed"] = False
        self.data["completed_at"] = ""
        self.save()


def scan_inputs(input_dir):
    """Liệt kê folder con của thư mục input, bỏ qua ẩn và chính nó."""
    out = []
    try:
        for entry in sorted(os.listdir(input_dir)):
            full = os.path.join(input_dir, entry)
            if entry.startswith(".") or entry.startswith("_"):
                continue
            if os.path.isdir(full):
                out.append(full)
    except Exception:
        pass
    return out


def scan_checkpoints(input_dir):
    """Đọc checkpoint của mọi folder input (dùng khi khởi động lại)."""
    return [(path, Checkpoint(path).load()) for path in scan_inputs(input_dir)]