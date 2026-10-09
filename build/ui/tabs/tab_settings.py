import tkinter as tk
from tkinter import ttk
from config.settings import API_PRESETS, API_CONFIG
from ui import theme
from ui.widgets import ModelPicker


def create_tab_settings(app, parent):
    tab = ttk.Frame(parent, style='Card.TFrame', padding=(theme.SP_LG, theme.SP_MD))
    tab.configure(style='Card.TFrame')

    def field_label(master, text):
        return ttk.Label(master, text=text, style='Section.TLabel')

    provider = ttk.Frame(tab, style='Card.TFrame', padding=(theme.SP_LG, theme.SP_MD))
    provider.pack(fill='x')
    ttk.Label(provider, text='PROVIDER', style='HeaderHint.TLabel').grid(row=0, column=0, columnspan=4, sticky='w')
    field_label(provider, 'Preset').grid(row=1, column=0, sticky='w', pady=(theme.SP_MD, 0))
    field_label(provider, 'Base URL').grid(row=2, column=0, sticky='w', pady=(theme.SP_SM, 0))
    app.combo_api_preset = ttk.Combobox(provider, values=list(API_PRESETS.keys()), state='readonly')
    app.combo_api_preset.set('OpenRouter')
    app.combo_api_preset.grid(row=1, column=1, sticky='w', padx=(theme.SP_MD, theme.SP_LG), pady=(theme.SP_MD, 0))
    app.entry_base_url = ttk.Entry(provider)
    app.entry_base_url.insert(0, API_CONFIG['default_base_url'])
    app.entry_base_url.grid(row=2, column=1, columnspan=3, sticky='ew', padx=(theme.SP_MD, 0), pady=(theme.SP_SM, 0))
    app.combo_api_preset.bind('<<ComboboxSelected>>', lambda event: app.on_preset_change())

    row2 = ttk.Frame(provider, style='Card.TFrame')
    row2.grid(row=3, column=0, columnspan=4, sticky='ew', pady=(theme.SP_LG, 0))
    field_label(row2, 'API Key').pack(side='left')
    app.entry_api_key = ttk.Entry(row2, show='*')
    app.entry_api_key.pack(side='left', fill='x', expand=True, padx=(theme.SP_MD, theme.SP_SM))
    ttk.Button(row2, text='Hiện/Ẩn', style='Ghost.TButton', command=app.toggle_key_visibility).pack(side='left')
    ttk.Button(row2, text='Lưu', style='Ghost.TButton', command=app.save_api_config).pack(side='left', padx=6)
    ttk.Button(row2, text='Kết nối', style='Primary.TButton', command=app.start_connect).pack(side='left')
    app.lbl_api_status = ttk.Label(provider, text='Nhập key → Kết nối → chọn model → Test', style='Card.Muted.TLabel')
    app.lbl_api_status.grid(row=4, column=0, columnspan=4, sticky='w', pady=(theme.SP_SM, 0))
    provider.columnconfigure(1, weight=1)
    provider.columnconfigure(3, weight=2)

    models = ttk.Frame(tab, style='Card.TFrame', padding=(theme.SP_LG, theme.SP_MD))
    models.pack(fill='both', expand=True, pady=(theme.SP_LG, 0))
    ttk.Label(models, text='MODELS', style='HeaderHint.TLabel').pack(anchor='w')
    row3 = ttk.Frame(models, style='Card.TFrame')
    row3.pack(fill='x', pady=(theme.SP_SM, 0))
    field_label(row3, 'Model').pack(side='left')
    app.combo_api_model = ModelPicker(row3, status_map=app._model_status,
                                      on_change=app._update_model_kind_label, width=46, height=20)
    app.combo_api_model.pack(side='left', fill='x', expand=True, padx=(theme.SP_MD, theme.SP_SM))
    app.btn_test_models = ttk.Button(row3, text='Test model', style='Primary.TButton',
                                     command=app.start_test_models, state='disabled')
    app.btn_test_models.pack(side='left')
    app.btn_stop_test = ttk.Button(row3, text='Dừng', style='Danger.TButton',
                                   command=app.stop_test_models, state='disabled')
    app.btn_stop_test.pack(side='left', padx=6)
    ttk.Button(row3, text='Load', style='Ghost.TButton', command=app.start_fetch_models).pack(side='left')

    row4 = ttk.Frame(models, style='Card.TFrame')
    row4.pack(fill='x', pady=(theme.SP_MD, theme.SP_XS))
    app.lbl_model_count = ttk.Label(row4, text='Chưa tải danh sách model', style='Card.Muted.TLabel')
    app.lbl_model_count.pack(side='left')
    app.lbl_model_filter_info = ttk.Label(row4, text='', style='Card.Muted.TLabel')
    app.lbl_model_filter_info.pack(side='left', padx=(theme.SP_LG, 0))
    app.combo_api_model.info_label = app.lbl_model_filter_info
    app.combo_api_model.show_all()
    # Mac dinh BAT: khi API loi thi tu doi model. KHONG xoay khi moi lai khong loi.
    app.var_rotate = tk.BooleanVar(value=True)
    ttk.Checkbutton(row4, text='Tu động đổi model khi API lỗi',
                    variable=app.var_rotate).pack(side='right')
    app.lbl_model_kind = ttk.Label(models, text='Loại: -', style='Card.Muted.TLabel')
    app.lbl_model_kind.pack(anchor='w')

    table_wrap = ttk.Frame(models, style='Card.TFrame')
    table_wrap.pack(fill='both', expand=True, pady=(theme.SP_SM, 0))
    columns = ('model', 'result', 'note')
    app.tree_test = ttk.Treeview(table_wrap, columns=columns, show='headings')
    app.tree_test.heading('model', text='Model')
    app.tree_test.heading('result', text='Kết quả')
    app.tree_test.heading('note', text='Ghi chú')
    app.tree_test.column('model', width=360, anchor='w')
    app.tree_test.column('result', width=100, anchor='center')
    app.tree_test.column('note', width=520, anchor='w')
    app.tree_test.tag_configure('ok', foreground=theme.status('ok'))
    app.tree_test.tag_configure('fail', foreground=theme.status('fail'))
    app.tree_test.tag_configure('warn', foreground=theme.status('warn'))
    app.tree_test.tag_configure('info', foreground=theme.status('info'))
    app.tree_test.tag_configure('roweven', background=theme.token('card_alt'))
    scroll = ttk.Scrollbar(table_wrap, orient='vertical', command=app.tree_test.yview)
    app.tree_test.configure(yscrollcommand=scroll.set)
    app.tree_test.pack(side='left', fill='both', expand=True)
    scroll.pack(side='right', fill='y')
    app.tree_test.bind('<Double-1>', app.show_test_detail)
    app.tree_test.bind('<Control-c>', app.copy_test_log)
    app.tree_test.bind('<Button-3>', app.show_test_log_menu)
    ttk.Label(models, text='Nháy đúp để xem chi tiết · Ctrl+C copy log', style='Card.Muted.TLabel').pack(anchor='w')
    return tab
