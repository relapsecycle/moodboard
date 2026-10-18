"""Windows clipboard image reader (no third-party deps)."""

from __future__ import annotations

import ctypes
import os
import struct
import tempfile
from ctypes import wintypes


CF_DIB = 8
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


class BITMAPFILEHEADER(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("bfType", wintypes.WORD),
        ("bfSize", wintypes.DWORD),
        ("bfReserved1", wintypes.WORD),
        ("bfReserved2", wintypes.WORD),
        ("bfOffBits", wintypes.DWORD),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = wintypes.BOOL
user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
user32.RegisterClipboardFormatW.restype = wintypes.UINT

kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = wintypes.LPVOID
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalSize.restype = ctypes.c_size_t


class ClipboardError(RuntimeError):
    pass


def _read_handle_bytes(handle) -> bytes:
    if not handle:
        raise ClipboardError("Clipboard handle was empty")
    ptr = kernel32.GlobalLock(handle)
    if not ptr:
        raise ClipboardError("Failed to lock clipboard data")
    try:
        size = kernel32.GlobalSize(handle)
        return ctypes.string_at(ptr, size)
    finally:
        kernel32.GlobalUnlock(handle)


def _dib_to_bmp_bytes(dib: bytes) -> bytes:
    if len(dib) < ctypes.sizeof(BITMAPINFOHEADER):
        raise ClipboardError("Clipboard DIB was too small")

    header = BITMAPINFOHEADER.from_buffer_copy(dib[: ctypes.sizeof(BITMAPINFOHEADER)])
    if header.biSize < ctypes.sizeof(BITMAPINFOHEADER):
        raise ClipboardError("Unsupported DIB header")

    # Color table for <= 8bpp
    colors = 0
    if header.biBitCount <= 8:
        colors = header.biClrUsed or (1 << header.biBitCount)
    elif header.biCompression == 3:  # BI_BITFIELDS
        colors = 3

    offset = header.biSize + colors * 4
    file_size = 14 + len(dib)

    bf = BITMAPFILEHEADER()
    bf.bfType = 0x4D42  # 'BM'
    bf.bfSize = file_size
    bf.bfReserved1 = 0
    bf.bfReserved2 = 0
    bf.bfOffBits = 14 + offset

    return bytes(bf) + dib


def save_clipboard_image_to_tempfile() -> str:
    """Write clipboard image to a temp file and return its path."""
    png_format = user32.RegisterClipboardFormatW("PNG")

    if not user32.OpenClipboard(None):
        raise ClipboardError("Could not open the clipboard")

    try:
        # Prefer PNG (common from browsers / modern apps)
        if png_format and user32.IsClipboardFormatAvailable(png_format):
            data = _read_handle_bytes(user32.GetClipboardData(png_format))
            fd, path = tempfile.mkstemp(prefix="moodboard_", suffix=".png")
            os.close(fd)
            with open(path, "wb") as handle:
                handle.write(data)
            return path

        if user32.IsClipboardFormatAvailable(CF_DIB):
            dib = _read_handle_bytes(user32.GetClipboardData(CF_DIB))
            bmp = _dib_to_bmp_bytes(dib)
            fd, path = tempfile.mkstemp(prefix="moodboard_", suffix=".bmp")
            os.close(fd)
            with open(path, "wb") as handle:
                handle.write(bmp)
            return path

        raise ClipboardError("No image data on the clipboard")
    finally:
        user32.CloseClipboard()


def get_clipboard_text() -> str:
    """Return Unicode text from the clipboard, or empty string."""
    if not user32.OpenClipboard(None):
        return ""
    try:
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return ""
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return ""
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def get_clipboard_html() -> str:
    """Return CF_HTML fragment text if present."""
    html_format = user32.RegisterClipboardFormatW("HTML Format")
    if not html_format:
        return ""
    if not user32.OpenClipboard(None):
        return ""
    try:
        if not user32.IsClipboardFormatAvailable(html_format):
            return ""
        data = _read_handle_bytes(user32.GetClipboardData(html_format))
        # CF_HTML is typically UTF-8.
        return data.decode("utf-8", errors="ignore")
    except Exception:
        return ""
    finally:
        user32.CloseClipboard()
