# Tool Video Workflow

Tool desktop Windows (Tkinter) dùng để **chia cảnh video dài thành SRT** và **sinh prompt ảnh theo từng cảnh**, bằng AI — hoạt động qua Web LLM (tự động thao tác trình duyệt) hoặc API OpenAI-compatible.

Mã nguồn nằm trong `build/` (đây là thư mục nguồn, không phải output của PyInstaller).

---

## Tính năng

**4 tab chính:**
- **Kịch bản** — đọc kịch bản, tách cảnh theo nhiều phương pháp, sinh SRT
- **SRT** — tinh chỉnh / kiểm tra file SRT
- **Prompt** — sinh prompt ảnh cho từng cảnh theo style tuỳ chọn
- **Cài đặt** — cấu hình API, LLM Web, Whisper

**Chế độ Auto:** `▶ Auto nhiều kịch bản` — chạy liên tục nhiều thư mục kịch bản, có thể bắt đầu lại từ bước 1 hoặc bước 2, tách prompt theo câu hoặc đoạn.

**Web LLM hỗ trợ (tự động thao tác trình duyệt):** ChatGPT · Gemini · Claude · DeepSeek · Qwen · Duck.ai · Dola · Sakana

**Nguồn AI:**
- API OpenAI-compatible (OpenRouter, OpenAI, Ollama, LM Studio, 9router, hoặc URL tuỳ chỉnh)
- Web LLM — đăng nhập một lần, profile Chrome được lưu lại
- Whisper (cài ngoài, tự tải model) để tự động căn thời lượng cảnh

---

## Yêu cầu

| | |
|---|---|
| OS | Windows 10/11 |
| Chrome hoặc Edge | Bắt buộc — tool tự tìm ở `Program Files`, không cần Playwright download browser |
| Python | Chỉ khi **chạy từ source** — Python 3.10+ |
| Dung lượng | ~151 MB (EXE + runtime) |

**Không cần** `pip install playwright` hay `playwright install` — driver Node đã nằm trong `_internal/playwright/driver/`.

---

## Cách chạy

### 1. Bản có sẵn (khuyến nghị)

```
ToolVideoWorkflow.exe
```

Chạy thẳng, không cần cài gì. Bản Windows đã đóng gói kèm Python runtime, Playwright, Tcl/Tk.

### 2. Chạy từ mã nguồn

```bash
cd build
pip install requests playwright
python main.py
```

---

## Dữ liệu sinh ra lúc chạy

Tất cả nằm trong `data/` và **không có trong git** (đã khai trong `.gitignore`):

| Đường dẫn | Nội dung |
|---|---|
| `data/api_config.json` | API key + base URL + model |
| `data/ui_state.json` | Trạng thái form, đường dẫn file gần nhất, style đã lưu |
| `data/chrome_profile_tool/` | Profile Chrome — cookies, đăng nhập Web LLM |
| `data/runtime/` | Whisper runtime + model |
| `data/log_*.txt`, `*.log` | Log |

Xoá thư mục `data/` không làm hỏng tool — app tự tạo lại khi khởi động. 

---

## Đóng gói lại EXE

```bash
cd build
pip install pyinstaller
pyinstaller ToolVideoWorkflow.spec
```

Output ra `dist/ToolVideoWorkflow/`.

---

## Kiểm thử

```bash
cd build
python selftest.py
```

Self-test offline, **272 check**, không cần mạng. Chạy được trên máy không có gì cài sẵn ngoài `requests`.

---

## Kiến trúc

```
build/
  main.py              Entry point
  selftest.py          Self-test offline (272 checks)
  config/settings.py   Preset API, URL LLM, selector trình duyệt
  core/
    api_client.py      Client OpenAI-compatible + retry
    browser_session.py Playwright, profile Chrome, quản lý tab
    browser_manager.py Điều phối luồng thao tác Web LLM
    providers.py       Adapter cho từng provider (input/send/response)
    auto_pipeline.py   Quét thư mục kịch bản, tìm file đầu vào
    scene_grouper.py   Tách cảnh
    prompt_generator.py Sinh prompt ảnh
    whisper_aligner.py Căn thời lượng bằng Whisper
    checkpoint.py      Lưu tiến độ (khôi phục được sau crash)
    recovery.py        Tự phục hồi lỗi
  ui/
    app.py             Cửa sổ chính
    auto_panel.py      Chế độ auto
    tabs/              4 tab
  utils/file_utils.py  get_data_dir() — tách dữ liệu khỏi mã nguồn
```

---

## Lưu ý

- Profile Chrome dùng chung cho mọi provider Web LLM. Xoá `data/chrome_profile_tool/` = phải đăng nhập lại từ đầu.
- Tool dùng Chrome/Edge hệ thống, không phải Chromium đóng gói. Nếu đã cài Chrome ở vị trí khác (portable), sửa đường dẫn trong `core/browser_session.py`.
- Whisper được cài riêng vào `data/runtime/` bằng `pip install --target`, không đụng tới Python hệ thống.

---

## License

MIT
