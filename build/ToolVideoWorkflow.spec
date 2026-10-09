# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('config', 'config'), ('core', 'core'), ('ui', 'ui'), ('utils', 'utils')]
binaries = []
hiddenimports = []
# KHONG collect_all('selenium') / ('webdriver_manager') nua. Tool da chuyen
# sang Playwright; dong them Selenium chi lam EXE to them ~22.6 MB.
tmp_ret = collect_all('requests')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# Playwright: driver (node + package) đi kèm dạng DATA, không phải import
# thường -> thiếu khai báo này thì EXE chạy được tới lúc bấm Web LLM
# rồi mới chết với ImportError.
for _pkg in ('playwright', 'greenlet', 'pyee'):
    try:
        tmp_ret = collect_all(_pkg)
        datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
    except Exception as _e:
        print('collect_all(%s) failed: %s' % (_pkg, _e))
hiddenimports += ['playwright.sync_api', 'playwright._impl._browser_type',
                  'playwright._impl._driver', 'greenlet', 'pyee.asyncio']


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['torch', 'torchaudio', 'faster_whisper', 'ctranslate2', 'onnxruntime', 'numba', 'llvmlite', 'av', 'scipy', 'pandas', 'pyarrow', 'matplotlib', 'imageio', 'imageio_ffmpeg', 'tensorflow', 'sympy', 'networkx', 'boto3', 'botocore', 'tensorflow_intel', 'PIL', 'numpy', 'psutil', 'yaml', 'pytest', '_pytest', 'pluggy', 'py', 'iniconfig', 'tomli', 'tomllib'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ToolVideoWorkflow',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ToolVideoWorkflow',
)
