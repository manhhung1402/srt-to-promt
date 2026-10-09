import tkinter as tk
from tkinter import ttk
from utils.file_utils import export_text_widget_content
from ui import theme


def create_tab_script(app, parent):
    tab = ttk.Frame(parent, style='Body.TFrame')
    paned = ttk.PanedWindow(tab, orient=tk.HORIZONTAL)
    paned.pack(fill='both', expand=True)

    def build(title, load_text, load_cmd, process_cmd, in_attr, stats_attr, out_attr):
        frame = ttk.Frame(paned, style='Card.TFrame', padding=(theme.SP_LG, theme.SP_MD))
        paned.add(frame, weight=1)
        ttk.Label(frame, text=title, style='HeaderHint.TLabel').pack(anchor='w')
        ttk.Button(frame, text=load_text, style='Ghost.TButton', command=load_cmd).pack(fill='x', pady=(theme.SP_SM, theme.SP_XS))
        text_in = theme.style_text(tk.Text(frame, wrap=tk.WORD), mono=False, height=6)
        text_in.pack(fill='x', pady=theme.SP_XS)
        theme.make_scrollable(text_in)
        setattr(app, in_attr, text_in)
        ttk.Button(frame, text='Lọc & tách', style='Primary.TButton', command=process_cmd).pack(fill='x', pady=(theme.SP_XS, theme.SP_SM))
        stats = ttk.Label(frame, text='Thống kê: 0 câu | 0 từ | 0 ký tự (0 không cách)', style='Card.Muted.TLabel')
        stats.pack(anchor='w')
        setattr(app, stats_attr, stats)
        text_out = theme.style_text(tk.Text(frame, wrap=tk.NONE))
        text_out.pack(fill='both', expand=True, pady=(theme.SP_SM, 0))
        theme.make_scrollable(text_out, horizontal=True)
        theme.mark_numbered(text_out)
        setattr(app, out_attr, text_out)
        ttk.Button(frame, text='Xuất Text', style='Ghost.TButton',
                   command=lambda: export_text_widget_content(text_out)).pack(anchor='e', pady=(theme.SP_SM, 0))

    build('TÁCH TỪ SRT CÂU', 'Lấy Bước 1', app.load_script_left, app.process_script_left,
          'txt_script_in_l', 'lbl_stats_l', 'txt_script_out_l')
    build('TÁCH TỪ SRT ĐOẠN', 'Lấy Bước 2', app.load_script_right, app.process_script_right,
          'txt_script_in_r', 'lbl_stats_r', 'txt_script_out_r')
    return tab
