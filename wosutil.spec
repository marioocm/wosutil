# -*- mode: python ; coding: utf-8 -*-
r"""PyInstaller spec: single-file, windowed WosUtil.exe.

RapidOCR models, ONNX Runtime binaries and the templates folder are bundled.
data/, logs/ and debug/ are written to %LOCALAPPDATA%\WosUtil at runtime (see
config._app_dir).
"""

import os

from PyInstaller.utils.hooks import collect_data_files

ROOT_DIR = os.path.abspath(SPECPATH)

rapidocr_datas = collect_data_files("rapidocr")

a = Analysis(
    [os.path.join(ROOT_DIR, "main.py")],
    pathex=[os.path.join(ROOT_DIR, "src")],
    binaries=[],
    datas=[
        (os.path.join(ROOT_DIR, "templates"), "templates"),
        *rapidocr_datas,
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
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
