# -*- coding: utf-8 -*-
"""Quản lý Whisper cài ngoài EXE (External Whisper runtime)."""
import importlib
import os
import subprocess
import sys
import threading

WHISPER_MODELS = {
    "tiny": {"label": "Tiny — nhẹ nhất, nhanh nhất, độ chính xác thấp", "size": "~75 MB"},
    "base": {"label": "Base — cân bằng tốc độ/chất lượng", "size": "~150 MB"},
    "small": {"label": "Small — chính xác hơn, chậm hơn", "size": "~500 MB"},
    "medium": {"label": "Medium — chính xác cao, nặng", "size": "~1.5 GB"},
    "large-v3": {"label": "Large-v3 — chính xác cao nhất, rất nặng", "size": "~3 GB"},
}

class WhisperRuntime:
    """Cài/import faster-whisper ngoài thư mục EXE."""
    def __init__(self, base_dir):
        self.base_dir = base_dir
        self.runtime_dir = os.path.join(base_dir, "runtime", "whisper")
        self.models_dir = os.path.join(base_dir, "runtime", "whisper_models")
        os.makedirs(self.runtime_dir, exist_ok=True)
        os.makedirs(self.models_dir, exist_ok=True)

    def _python_command(self):
        candidates = [sys.executable]
        if getattr(sys, "frozen", False):
            candidates = []
        candidates += ["python", "py"]
        for command in candidates:
            try:
                result = subprocess.run([command, "--version"], capture_output=True, text=True, timeout=10)
                if result.returncode == 0:
                    return command
            except Exception:
                continue
        return None

    def is_installed(self):
        try:
            importlib.import_module("faster_whisper")
            return True
        except ImportError:
            pass
        package_dir = os.path.join(self.runtime_dir, "faster_whisper")
        return os.path.isdir(package_dir)

    def install(self, progress_cb=None):
        python_cmd = self._python_command()
        if not python_cmd:
            raise RuntimeError("Chưa tìm thấy Python trên máy. Cài Python 3.10+ rồi bấm Tải Whisper lại.")
        os.makedirs(self.runtime_dir, exist_ok=True)
        command = [python_cmd, "-m", "pip", "install", "--upgrade", "--target", self.runtime_dir, "faster-whisper"]
        if progress_cb:
            progress_cb("Đang tải faster-whisper và thư viện phụ thuộc...", "orange")
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        for line in process.stdout:
            if progress_cb and line.strip():
                progress_cb(line.strip()[:180], "orange")
        code = process.wait()
        if code != 0:
            raise RuntimeError("Cài faster-whisper thất bại. Kiểm tra Internet/Python/pip rồi thử lại.")
        if self.runtime_dir not in sys.path:
            sys.path.insert(0, self.runtime_dir)
        importlib.invalidate_caches()
        if not self.is_installed():
            raise RuntimeError("Đã cài nhưng không tìm thấy faster-whisper trong thư mục runtime.")

    def load_model(self, model_name, progress_cb=None):
        if model_name not in WHISPER_MODELS:
            raise ValueError(f"Model Whisper không hợp lệ: {model_name}")
        if self.runtime_dir not in sys.path:
            sys.path.insert(0, self.runtime_dir)
        try:
            module = importlib.import_module("faster_whisper")
        except ImportError as exc:
            raise RuntimeError("Chưa cài Whisper. Bấm 'Tải Whisper' trước.") from exc
        if progress_cb:
            progress_cb(f"Đang tải model Whisper '{model_name}' lần đầu...", "orange")
        WhisperModel = module.WhisperModel
        return WhisperModel(model_name, device="cpu", compute_type="int8", download_root=self.models_dir)
