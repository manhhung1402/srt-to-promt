import tkinter as tk
from tkinter import ttk
from config.settings import LLM_URLS, DEFAULT_STYLE_PROMPT
from utils.file_utils import export_text_widget_content
from ui import theme


def create_tab_prompt(app, parent):
    tab = ttk.Frame(parent, style='Body.TFrame')
    config = ttk.Frame(tab, style='Card.TFrame', padding=(theme.SP_LG, theme.SP_MD))
    config.pack(fill='x')
    ttk.Label(config, text='CẤU HÌNH', style='HeaderHint.TLabel').pack(anchor='w')
    row1 = ttk.Frame(config, style='Card.TFrame')
    row1.pack(fill='x', pady=(theme.SP_SM, 0))
    ttk.Label(row1, text='LLM (Bước 4)', style='Section.TLabel').pack(side='left')
    app.combo_llm_step4 = ttk.Combobox(row1, textvariable=app.prompt_llm_var,
                                      values=list(LLM_URLS.keys()), state='readonly')
    app.combo_llm_step4.pack(side='left', padx=(theme.SP_MD, theme.SP_LG))
    ttk.Button(row1, text='Mở Chrome', style='Ghost.TButton', command=app.manual_open_login).pack(side='left')
    ttk.Label(row1, text='Batch', style='Section.TLabel').pack(side='left', padx=(theme.SP_LG, theme.SP_MD))
    app.spin_batch_size = ttk.Spinbox(row1, from_=10, to=200, width=7)
    app.spin_batch_size.pack(side='left')
    row2 = ttk.Frame(config, style='Card.TFrame')
    row2.pack(fill='x', pady=(theme.SP_SM, 0))
    ttk.Label(row2, text='Nguồn (Bước 4)', style='Section.TLabel').pack(side='left')
    ttk.Radiobutton(row2, text='API', value='api', variable=app.prompt_source_var).pack(side='left', padx=(theme.SP_MD, theme.SP_LG))
    ttk.Radiobutton(row2, text='Web', value='web', variable=app.prompt_source_var).pack(side='left')
    ttk.Label(row2, text='(Bước 4 dùng nguồn riêng, không ảnh hưởng Bước 2)',
              style='Card.Muted.TLabel').pack(side='left', padx=theme.SP_MD)
    row2b = ttk.Frame(config, style='Card.TFrame')
    row2b.pack(fill='x', pady=(theme.SP_XS, 0))
    ttk.Label(row2b, text='Thử lại', style='Section.TLabel').pack(side='left')
    app.combo_api_retry_prompt = ttk.Combobox(row2b, textvariable=app.api_retry_var,
                                               state='readonly', width=10)
    app.combo_api_retry_prompt['values'] = app.RETRY_CHOICES
    app.combo_api_retry_prompt.pack(side='left', padx=(theme.SP_MD, theme.SP_SM))
    ttk.Label(row2b, text="'inf' = vô hạn (áp dụng cho API và LLM Web)", style='Card.Muted.TLabel').pack(side='left')
    row3 = ttk.Frame(config, style='Card.TFrame')
    row3.pack(fill='x', pady=(theme.SP_SM, 0))
    ttk.Label(row3, text='Style', style='Section.TLabel').pack(side='left')
    app.entry_style = ttk.Entry(row3)
    app.entry_style.insert(0, DEFAULT_STYLE_PROMPT)
    app.entry_style.pack(side='left', fill='x', expand=True, padx=(theme.SP_MD, 0))
    ttk.Button(row3, text='Lưu style', style='Ghost.TButton',
               command=app._remember_style).pack(side='left', padx=(theme.SP_SM, 0))

    row3b = ttk.Frame(config, style='Card.TFrame')
    row3b.pack(fill='x', pady=(theme.SP_XS, 0))
    ttk.Label(row3b, text='Lịch sử style', style='Section.TLabel').pack(side='left')
    app.combo_style_history = ttk.Combobox(row3b, state='readonly', values=app.style_presets)
    app.combo_style_history.pack(side='left', fill='x', expand=True, padx=(theme.SP_MD, theme.SP_SM))
    app.combo_style_history.bind('<<ComboboxSelected>>', lambda event: app._apply_style_history())
    ttk.Label(row3b, text='chọn để áp dụng lại · lưu khi bấm "Lưu style" hoặc đóng tool',
              style='Card.Muted.TLabel').pack(side='left')

    inputs = ttk.Frame(tab, style='Card.TFrame', padding=(theme.SP_LG, theme.SP_MD))
    inputs.pack(fill='x', pady=(theme.SP_LG, 0))
    ttk.Label(inputs, text='KỊCH BẢN', style='HeaderHint.TLabel').pack(anchor='w')
    actions = ttk.Frame(inputs, style='Card.TFrame')
    actions.pack(fill='x', pady=(theme.SP_SM, 0))
    ttk.Button(actions, text='Từ câu', style='Ghost.TButton', command=app.load_prompt_in_left).pack(side='left')
    ttk.Button(actions, text='Từ đoạn', style='Ghost.TButton', command=app.load_prompt_in_right).pack(side='left', padx=6)
    app.txt_prompt_input = theme.style_text(tk.Text(inputs, height=4), mono=False)
    app.txt_prompt_input.pack(fill='x', pady=(theme.SP_SM, 0))
    theme.make_scrollable(app.txt_prompt_input)

    run = ttk.Frame(tab, style='Card.TFrame', padding=(theme.SP_LG, theme.SP_MD))
    run.pack(fill='x', pady=(theme.SP_LG, 0))
    app.btn_run_llm = ttk.Button(run, text='Bắt đầu sinh Prompt', style='Primary.TButton', command=app.start_llm_thread)
    app.btn_run_llm.pack(side='left')
    app.btn_pause_prompt = ttk.Button(run, text='Tạm dừng', command=app.toggle_pause_prompt, state='disabled')
    app.btn_pause_prompt.pack(side='left', padx=6)
    ttk.Button(run, text='Dừng / đóng Chrome', style='Danger.TButton',
               command=lambda: app.manual_reset_chrome(scope='prompt')).pack(side='left')
    app.lbl_llm_status = ttk.Label(run, text='Trạng thái: Chưa chạy', style='Card.Muted.TLabel')
    app.lbl_llm_status.pack(side='left', padx=theme.SP_LG)

    outputs = ttk.Frame(tab, style='Card.TFrame', padding=(theme.SP_LG, theme.SP_MD))
    outputs.pack(fill='both', expand=True, pady=(theme.SP_LG, 0))
    head = ttk.Frame(outputs, style='Card.TFrame')
    head.pack(fill='x')
    ttk.Label(head, text='PROMPT OUTPUT', style='HeaderHint.TLabel').pack(side='left')
    app.txt_prompts = theme.style_text(tk.Text(outputs, wrap=tk.NONE))
    ttk.Button(head, text='Xuất Prompt', style='Ghost.TButton',
               command=lambda: export_text_widget_content(app.txt_prompts)).pack(side='right')
    app.txt_prompts.pack(fill='both', expand=True, pady=(theme.SP_SM, 0))
    theme.make_scrollable(app.txt_prompts, horizontal=True)
    theme.mark_numbered(app.txt_prompts)
    return tab
