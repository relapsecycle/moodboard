"""Download images from browser drops and URLs."""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import Request, urlopen

IMAGE_MAGIC = {
    b"\x89PNG\r\n\x1a\n": ".png",
    b"\xff\xd8\xff": ".jpg",
    b"GIF87a": ".gif",
    b"GIF89a": ".gif",
    b"RIFF": ".webp",  # needs WEBP check below
    b"BM": ".bmp",
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


def is_http_url(value: str) -> bool:
    value = (value or "").strip().strip('"')
    return value.lower().startswith("http://") or value.lower().startswith("https://")


def strip_file_url(value: str) -> str:
    value = (value or "").strip().strip('"')
    if not value.lower().startswith("file:"):
        return value
    parsed = urlparse(value)
    path = unquote(parsed.path)
    # Windows: file:///C:/path -> /C:/path
    if re.match(r"^/[A-Za-z]:", path):
        path = path[1:]
    return path


def unwrap_google_image_url(url: str) -> str:
    """Pull the real image URL out of Google imgres / imgurl wrappers."""
    try:
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower()
        qs = parse_qs(parsed.query)
        for key in ("imgurl", "image_url", "imgrefurl", "url", "mediaurl"):
            if key in qs and qs[key]:
                candidate = unquote(qs[key][0])
                if is_http_url(candidate) and key != "imgrefurl":
                    return candidate
                if key == "imgurl" and is_http_url(candidate):
                    return candidate
        # /imgres?imgurl=...
        if "google." in host and "imgurl" in qs:
            return unquote(qs["imgurl"][0])
    except Exception:
        pass
    return url


def extension_from_bytes(data: bytes, content_type: str = "", url: str = "") -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return ".gif"
    if data.startswith(b"BM"):
        return ".bmp"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    if data.startswith(b"II*\x00") or data.startswith(b"MM\x00*"):
        return ".tif"

    ctype = (content_type or "").split(";")[0].strip().lower()
    mapping = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/bmp": ".bmp",
        "image/tiff": ".tif",
    }
    if ctype in mapping:
        return mapping[ctype]

    path = urlparse(url).path
    suffix = Path(path).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".exr"}:
        return ".jpg" if suffix == ".jpeg" else suffix
    return ".png"


def download_image_url(url: str) -> str:
    """Download URL to a temp image file. Returns local path."""
    url = unwrap_google_image_url(url.strip().strip('"'))
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "image/*,*/*"})
    with urlopen(request, timeout=45) as response:
        data = response.read()
        content_type = response.headers.get("Content-Type", "")
        final_url = response.geturl()

    if not data:
        raise RuntimeError("Downloaded image was empty")

    # If Google returned HTML, try to extract an image URL once.
    head = data[:256].lstrip().lower()
    if head.startswith(b"<!doctype html") or head.startswith(b"<html"):
        text = data.decode("utf-8", errors="ignore")
        extracted = extract_image_urls_from_html(text)
        if not extracted:
            raise RuntimeError("Link pointed to a web page, not an image")
        return download_image_url(extracted[0])

    ext = extension_from_bytes(data, content_type, final_url or url)
    fd, path = tempfile.mkstemp(prefix="moodboard_web_", suffix=ext)
    os.close(fd)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def parse_internet_shortcut(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    for line in text.splitlines():
        if line.upper().startswith("URL="):
            url = line.split("=", 1)[1].strip()
            if is_http_url(url):
                return url
    return None


_IMG_SRC_RE = re.compile(
    r"(?:(?:og:image|twitter:image)[^>]*content|img[^>]*src)\s*=\s*[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)
_BARE_IMG_RE = re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"']", re.IGNORECASE)


def extract_image_urls_from_html(html: str) -> list[str]:
    urls = []
    for match in _IMG_SRC_RE.finditer(html):
        urls.append(match.group(1))
    for match in _BARE_IMG_RE.finditer(html):
        urls.append(match.group(1))
    # Also catch plain image links in text/uri-list style HTML drops
    for match in re.finditer(r"https?://[^\s\"'<>]+", html):
        candidate = match.group(0)
        lower = candidate.lower()
        if any(ext in lower for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")):
            urls.append(candidate)
        elif "imgurl=" in lower or "gstatic.com" in lower or "googleusercontent.com" in lower:
            urls.append(candidate)

    cleaned = []
    seen = set()
    for url in urls:
        url = unquote(url.strip())
        if url.startswith("//"):
            url = "https:" + url
        if not is_http_url(url):
            continue
        url = unwrap_google_image_url(url)
        if url in seen:
            continue
        seen.add(url)
        cleaned.append(url)
    return cleaned


def extract_urls_from_text(text: str) -> list[str]:
    text = text or ""
    urls = []
    for match in re.finditer(r"https?://[^\s\"'<>]+", text):
        urls.append(unwrap_google_image_url(match.group(0).rstrip(").,;")))
    # uri-list style
    for line in text.splitlines():
        line = line.strip()
        if is_http_url(line):
            urls.append(unwrap_google_image_url(line))
    seen = set()
    out = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def looks_like_image_bytes(data: bytes) -> bool:
    if not data:
        return False
    if data.startswith(b"\x89PNG\r\n\x1a\n") or data.startswith(b"\xff\xd8\xff"):
        return True
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a") or data.startswith(b"BM"):
        return True
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return True
    if data.startswith(b"II*\x00") or data.startswith(b"MM\x00*"):
        return True
    return False


def looks_like_html_bytes(data: bytes) -> bool:
    head = data[:256].lstrip().lower()
    return (
        head.startswith(b"<!doctype html")
        or head.startswith(b"<html")
        or head.startswith(b"<head")
        or b"<img" in head
        or b"http://" in head
        or b"https://" in head
    )


def looks_like_image_file(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.suffix.lower() in {
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
        ".bmp",
        ".tga",
        ".tif",
        ".tiff",
        ".exr",
    }:
        # Browsers sometimes save HTML under a .jpg name.
        try:
            with path.open("rb") as handle:
                header = handle.read(64)
            if looks_like_html_bytes(header):
                return False
            if looks_like_image_bytes(header):
                return True
            return len(header) > 0
        except OSError:
            return True
    try:
        with path.open("rb") as handle:
            header = handle.read(16)
    except OSError:
        return False
    return looks_like_image_bytes(header)


def _read_bytes_shared_win(path: Path) -> bytes | None:
    """Read with FILE_SHARE_READ|WRITE|DELETE so Chrome locks don't block us."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    GENERIC_READ = 0x80000000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    FILE_SHARE_DELETE = 0x00000004
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x80
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.GetFileSizeEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_longlong)]
    kernel32.GetFileSizeEx.restype = wintypes.BOOL
    kernel32.ReadFile.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    kernel32.ReadFile.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.CreateFileW(
        str(path),
        GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        None,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL,
        None,
    )
    if handle == INVALID_HANDLE_VALUE or handle is None or int(handle) == -1:
        return None
    try:
        size = ctypes.c_longlong(0)
        if not kernel32.GetFileSizeEx(handle, ctypes.byref(size)):
            return None
        if size.value <= 0:
            return None
        buf = (ctypes.c_char * size.value)()
        read = wintypes.DWORD(0)
        if not kernel32.ReadFile(handle, buf, size.value, ctypes.byref(read), None):
            return None
        return bytes(buf[: read.value])
    finally:
        kernel32.CloseHandle(handle)


def read_path_bytes(path: Path) -> bytes | None:
    """Single read attempt using shared-mode open when possible."""
    data = _read_bytes_shared_win(path)
    if data:
        return data
    try:
        if path.is_file():
            data = path.read_bytes()
            return data or None
    except OSError:
        return None
    return None


def read_file_with_retry(path: Path, attempts: int = 8, delay: float = 0.01) -> bytes | None:
    """
    Browser drops (Chrome/Google) often expose a Temp file that appears briefly.
    Short retry loop for missing temp files.
    """
    import time

    path = Path(str(path).strip().strip('"').rstrip("\x00"))
    for index in range(attempts):
        data = read_path_bytes(path)
        if data:
            return data
        if index + 1 < attempts:
            time.sleep(delay)

    try:
        folder = path.parent
        stem = path.stem.lower()
        if folder.is_dir():
            for candidate in folder.iterdir():
                try:
                    if not candidate.is_file():
                        continue
                    name = candidate.name.lower()
                    if name == path.name.lower() or (stem in name and name.endswith((".jpg", ".jpeg", ".png", ".webp"))):
                        data = read_path_bytes(candidate)
                        if data:
                            return data
                except OSError:
                    continue
    except OSError:
        pass

    return None


def find_recent_temp_image_bytes(max_age_sec: float = 30.0) -> bytes | None:
    """Chrome may delete the reported path; grab the newest image bytes from Temp."""
    import time

    roots: list[Path] = []
    for value in (
        tempfile.gettempdir(),
        os.environ.get("TEMP"),
        os.environ.get("TMP"),
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Temp"),
    ):
        if value:
            roots.append(Path(value))

    now = time.time()
    newest = None  # (mtime, data)
    seen: set[str] = set()

    def _consider(path: Path):
        nonlocal newest
        key = str(path)
        if key in seen:
            return
        seen.add(key)
        try:
            stat = path.stat()
        except OSError:
            return
        if now - stat.st_mtime > max_age_sec:
            return
        if stat.st_size <= 0 or stat.st_size > 80 * 1024 * 1024:
            return
        lower = path.name.lower()
        if not (
            lower.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"))
            or "image" in lower
        ):
            return
        data = read_path_bytes(path)
        if not data:
            return
        # Prefer real image bytes; keep HTML-as-jpg as secondary.
        score = stat.st_mtime
        if looks_like_image_bytes(data):
            score += 1000.0
        elif looks_like_html_bytes(data):
            score += 10.0
        else:
            return
        if newest is None or score > newest[0]:
            newest = (score, data)

    for root in roots:
        if not root.is_dir():
            continue
        try:
            entries = list(root.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_file():
                    _consider(entry)
                elif entry.is_dir():
                    # Chrome drag folders / nested temp dirs
                    name = entry.name.lower()
                    if not (
                        "chrom" in name
                        or "dnd" in name
                        or "drag" in name
                        or name.startswith("tmp")
                        or name.startswith("ns")  # firefox
                    ):
                        continue
                    for child in entry.iterdir():
                        if child.is_file():
                            _consider(child)
            except OSError:
                continue

    return None if newest is None else newest[1]


def bytes_to_owned_temp(data: bytes, hint_name: str = "drop") -> tuple[str | None, list[str]]:
    """Turn raw bytes into an owned temp image (or download if HTML/URL)."""
    cleanup: list[str] = []
    if not data:
        return None, cleanup

    head_text = data[:64].lstrip().lower()
    if head_text.startswith(b"[internetshortcut") or b"URL=" in data[:220]:
        text = data.decode("utf-8", errors="ignore")
        for line in text.splitlines():
            if line.upper().startswith("URL="):
                url = line.split("=", 1)[1].strip()
                if is_http_url(url):
                    downloaded = download_image_url(url)
                    cleanup.append(downloaded)
                    return downloaded, cleanup

    if looks_like_html_bytes(data):
        text = data.decode("utf-8", errors="ignore")
        urls = extract_image_urls_from_html(text)
        if not urls:
            urls = extract_urls_from_text(text)
        if urls:
            downloaded = download_image_url(urls[0])
            cleanup.append(downloaded)
            return downloaded, cleanup
        return None, cleanup

    if looks_like_image_bytes(data):
        ext = extension_from_bytes(data)
    else:
        ext = ".jpg"

    fd, owned = tempfile.mkstemp(prefix=f"moodboard_{hint_name}_", suffix=ext)
    os.close(fd)
    with open(owned, "wb") as handle:
        handle.write(data)
    cleanup.append(owned)
    return owned, cleanup


def snapshot_local_path(path: Path) -> tuple[str | None, list[str]]:
    """
    Copy a dropped local path into an owned temp image file.
    Returns (local_image_path_or_none, cleanup_paths).
    May download if the dropped "image" is actually HTML/URL content.
    """
    path = Path(str(path).strip().strip('"').rstrip("\x00"))
    data = read_file_with_retry(path)
    if not data:
        return None, []
    return bytes_to_owned_temp(data, hint_name=path.stem or "drop")


def salvage_browser_drop(reported_path: str | None = None) -> tuple[str | None, list[str]]:
    """
    Fallback when Chrome removes Temp\\images.jpg before the drop operator runs.
    """
    if reported_path:
        data = read_path_bytes(Path(str(reported_path).strip().strip('"')))
        if data:
            return bytes_to_owned_temp(data, hint_name="salvage")

    temp_root = Path(os.environ.get("TEMP") or tempfile.gettempdir())
    for name in ("images.jpg", "images.jpeg", "images.png", "image.jpg", "image.png"):
        data = read_path_bytes(temp_root / name)
        if data:
            return bytes_to_owned_temp(data, hint_name="temp_scan")

    # Clipboard fallback (instant if browser put pixels there).
    if sys.platform == "win32":
        try:
            from .clipboard_win import (
                ClipboardError,
                get_clipboard_html,
                get_clipboard_text,
                save_clipboard_image_to_tempfile,
            )

            try:
                clip_path = save_clipboard_image_to_tempfile()
                return clip_path, [clip_path]
            except ClipboardError:
                pass

            html = get_clipboard_html()
            text = get_clipboard_text()
            urls = []
            if html:
                urls.extend(extract_image_urls_from_html(html))
            if text:
                urls.extend(extract_urls_from_text(text))
            if urls:
                downloaded = download_image_url(urls[0])
                return downloaded, [downloaded]
        except Exception:
            pass

    return None, []
