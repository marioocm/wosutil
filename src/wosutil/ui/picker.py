"""Native file and folder pickers used by the preferences form."""

import ctypes
import shutil
import subprocess
import sys
from ctypes import wintypes
from typing import Optional

from wosutil.ui.windows import find_app_window


class _BrowseInfoW(ctypes.Structure):
    _fields_ = [
        ("hwndOwner", wintypes.HWND),
        ("pidlRoot", ctypes.c_void_p),
        ("pszDisplayName", wintypes.LPWSTR),
        ("lpszTitle", wintypes.LPCWSTR),
        ("ulFlags", wintypes.UINT),
        ("lpfn", ctypes.c_void_p),
        ("lParam", wintypes.LPARAM),
        ("iImage", ctypes.c_int),
    ]


class _OpenFileNameW(ctypes.Structure):
    _fields_ = [
        ("lStructSize", wintypes.DWORD),
        ("hwndOwner", wintypes.HWND),
        ("hInstance", wintypes.HINSTANCE),
        ("lpstrFilter", wintypes.LPCWSTR),
        ("lpstrCustomFilter", wintypes.LPWSTR),
        ("nMaxCustFilter", wintypes.DWORD),
        ("nFilterIndex", wintypes.DWORD),
        ("lpstrFile", wintypes.LPWSTR),
        ("nMaxFile", wintypes.DWORD),
        ("lpstrFileTitle", wintypes.LPWSTR),
        ("nMaxFileTitle", wintypes.DWORD),
        ("lpstrInitialDir", wintypes.LPCWSTR),
        ("lpstrTitle", wintypes.LPCWSTR),
        ("Flags", wintypes.DWORD),
        ("nFileOffset", wintypes.WORD),
        ("nFileExtension", wintypes.WORD),
        ("lpstrDefExt", wintypes.LPCWSTR),
        ("lCustData", wintypes.LPARAM),
        ("lpfnHook", ctypes.c_void_p),
        ("lpTemplateName", wintypes.LPCWSTR),
        ("pvReserved", ctypes.c_void_p),
        ("dwReserved", wintypes.DWORD),
        ("FlagsEx", wintypes.DWORD),
    ]


def _windll(name: str):
    """Return a Win32 library handle (only used on Windows)."""
    return ctypes.__dict__["windll"][name]


def pick_path(kind: str, title: str = "", initial: str = "") -> Optional[str]:
    """Open the native picker and return the selected absolute path.

    Args:
        kind: ``directory`` or ``file``.
        title: Dialog title.
        initial: Preferred starting directory or file.

    Returns:
        The selected path, or None when the user cancels.
    """
    if kind not in {"directory", "file"}:
        raise ValueError("kind must be 'directory' or 'file'.")
    if sys.platform.startswith("win"):
        return _pick_windows(kind, title, initial)
    if sys.platform == "darwin":
        return _pick_macos(kind)
    return _pick_linux(kind, title, initial)


def _pick_windows(kind: str, title: str, initial: str) -> Optional[str]:
    """Open the Win32 shell dialog with COM initialized for this thread."""
    ole32 = _windll("ole32")
    initialized = ole32.CoInitialize(None) in (0, 1)
    try:
        if kind == "directory":
            return _pick_directory_windows(title)
        return _pick_file_windows(title, initial)
    finally:
        if initialized:
            ole32.CoUninitialize()


def _pick_directory_windows(title: str) -> Optional[str]:
    """Open the standard folder picker."""
    shell32 = _windll("shell32")
    shell32.SHBrowseForFolderW.restype = ctypes.c_void_p
    shell32.SHBrowseForFolderW.argtypes = [ctypes.POINTER(_BrowseInfoW)]
    shell32.SHGetPathFromIDListW.restype = wintypes.BOOL
    shell32.SHGetPathFromIDListW.argtypes = [ctypes.c_void_p, wintypes.LPWSTR]
    _windll("ole32").CoTaskMemFree.argtypes = [ctypes.c_void_p]

    display = ctypes.create_unicode_buffer(1024)
    info = _BrowseInfoW()
    info.hwndOwner = find_app_window() or 0
    info.pszDisplayName = ctypes.cast(display, wintypes.LPWSTR)
    info.lpszTitle = title or "Select a folder"
    info.ulFlags = 0x0001 | 0x0040  # BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE

    pidl = shell32.SHBrowseForFolderW(ctypes.byref(info))
    if not pidl:
        return None
    path = ctypes.create_unicode_buffer(1024)
    try:
        if not shell32.SHGetPathFromIDListW(pidl, ctypes.cast(path, wintypes.LPWSTR)):
            return None
        return path.value or None
    finally:
        _windll("ole32").CoTaskMemFree(pidl)


def _pick_file_windows(title: str, initial: str) -> Optional[str]:
    """Open the standard file picker."""
    comdlg32 = _windll("comdlg32")
    comdlg32.GetOpenFileNameW.restype = wintypes.BOOL
    comdlg32.GetOpenFileNameW.argtypes = [ctypes.POINTER(_OpenFileNameW)]

    buffer = ctypes.create_unicode_buffer(4096)
    dialog = _OpenFileNameW()
    dialog.lStructSize = ctypes.sizeof(_OpenFileNameW)
    dialog.hwndOwner = find_app_window() or 0
    dialog.lpstrFile = ctypes.cast(buffer, wintypes.LPWSTR)
    dialog.nMaxFile = len(buffer)
    dialog.lpstrTitle = title or "Select a file"
    dialog.lpstrInitialDir = initial or None
    dialog.Flags = 0x00001000 | 0x00000800 | 0x00080000 | 0x00000008  # FILEMUSTEXIST | PATHMUSTEXIST | EXPLORER | NOCHANGEDIR

    if not comdlg32.GetOpenFileNameW(ctypes.byref(dialog)):
        return None
    return buffer.value or None


def _pick_linux(kind: str, title: str, initial: str) -> Optional[str]:
    """Use Zenity or KDialog when available."""
    zenity = shutil.which("zenity")
    if zenity:
        arguments = [zenity, "--file-selection", "--title", title or ("Select a folder" if kind == "directory" else "Select a file")]
        if kind == "directory":
            arguments.append("--directory")
        if initial:
            arguments.extend(["--filename", initial])
        return _run_picker(arguments)

    kdialog = shutil.which("kdialog")
    if kdialog:
        arguments = [kdialog, "--getexistingdirectory" if kind == "directory" else "--getopenfilename", initial or "."]
        arguments.extend(["--title", title or ("Select a folder" if kind == "directory" else "Select a file")])
        return _run_picker(arguments)

    return None


def _pick_macos(kind: str) -> Optional[str]:
    """Use AppleScript's native picker."""
    command = "choose folder" if kind == "directory" else "choose file"
    return _run_picker(["osascript", "-e", f"POSIX path of ({command})"])


def _run_picker(arguments: list) -> Optional[str]:
    """Run a picker command and return its selected path."""
    result = subprocess.run(arguments, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None
