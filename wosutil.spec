# -*- mode: python ; coding: utf-8 -*-
r"""PyInstaller spec: single-file WosUtil executable.

The native pywebview shell, the UI bundle, RapidOCR models, ONNX Runtime
binaries and the templates folder are bundled. data/, logs/ and debug/ are
written to %LOCALAPPDATA%\WosUtil on Windows and to $XDG_DATA_HOME/WosUtil on
Linux at runtime (see config._app_dir).
"""

import os
import sys

from PyInstaller.utils.hooks import collect_data_files

ROOT_DIR = os.path.abspath(SPECPATH)

rapidocr_datas = collect_data_files("rapidocr")

webui_dir = os.path.join(ROOT_DIR, "src", "wosutil", "webui", "static")
webui_datas = [(webui_dir, "webui")] if os.path.isdir(webui_dir) else []

# pywebview loads its GUI backend dynamically, so PyInstaller cannot see it.
if sys.platform.startswith("win"):
    platform_hiddenimports = [
        "webview.platforms.winforms",
        "webview.platforms.edgechromium",
        "clr",
        "clr_loader",
    ]
elif sys.platform.startswith("linux"):
    platform_hiddenimports = [
        "webview.platforms.gtk",
        "gi",
        "gi.repository.Gtk",
        "gi.repository.Gdk",
        "gi.repository.WebKit2",
    ]
else:
    platform_hiddenimports = []

a = Analysis(
    [os.path.join(ROOT_DIR, "main.py")],
    pathex=[os.path.join(ROOT_DIR, "src")],
    binaries=[],
    datas=[
        (os.path.join(ROOT_DIR, "templates"), "templates"),
        *rapidocr_datas,
        *webui_datas,
    ],
    hiddenimports=[
        "webview",
        *platform_hiddenimports,
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "_tkinter",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "rapidocr.inference_engine.mnn",
        "rapidocr.inference_engine.openvino",
        "rapidocr.inference_engine.paddle",
        "rapidocr.inference_engine.pytorch",
        "rapidocr.inference_engine.tensorrt",
    ],
    noarchive=False,
)

a.binaries = [entry for entry in a.binaries if "opencv_videoio_ffmpeg" not in entry[0]]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="WosUtil",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
