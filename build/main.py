# -*- coding: utf-8 -*-
"""
==========================================================================
 TOOL VIDEO WORKFLOW - TẠO SRT & SINH PROMPT
 Phiên bản Module hóa (Modular Architecture)
==========================================================================
"""
import os
import multiprocessing
import tkinter as tk
from ui.app import VideoWorkflowTool

if __name__ == '__main__':
    multiprocessing.freeze_support()
    os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

    root = tk.Tk()
    app = VideoWorkflowTool(root)
    root.mainloop()

