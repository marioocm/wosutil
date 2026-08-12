# -*- mode: python ; coding: utf-8 -*-
r"""PyInstaller spec: single-file, windowed WosUtil.exe with bundled Tesseract.

Build:
    .venv\Scripts\pyinstaller --noconfirm --clean wosutil.spec

Tesseract OCR must first be staged into build/tesseract (tesseract.exe, its
DLLs and tessdata/eng.traineddata) by scripts/build.ps1. The whole folder is
bundled under the ``tesseract`` destination, so at runtime it is found at
``<_MEIPASS>/tesseract/tesseract.exe`` and image_utils.resolve_tesseract_cmd()
points TESSDATA_PREFIX at its tessdata folder. The templates folder is bundled
under ``templates`` and resolved at runtime via sys._MEIPASS.

Only what ships inside the exe is defined here; data/, logs/ and debug/ are
written to %LOCALAPPDATA%\WosUtil at runtime (see config._app_dir).
"""

import os

ROOT_DIR = os.path.abspath(SPECPATH)

a = Analysis(
    [os.path.join(ROOT_DIR, "main.py")],
    pathex=[os.path.join(ROOT_DIR, "src")],
    binaries=[(os.path.join(ROOT_DIR, "build", "tesseract"), "tesseract")],
    datas=[(os.path.join(ROOT_DIR, "templates"), "templates")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

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
