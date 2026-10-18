"""
PureRef 2.x (.pur) reader for MoodBoard.

PureRef 2.x files are SQLite databases with a twist: the first N bytes
(custom header + OS preview thumbnail) are swapped to the end of the file
so Explorer can show a preview. Restoring that prefix yields a normal
SQLite DB with tables: images, items, items_images, metadata, …
"""

from __future__ import annotations

import math
import sqlite3
import struct
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

from .pureref_common import (
    PurImagePlacement,
    PurRefError,
    PurScene,
    is_modern_version,
    peek_pur_version,
)

_SQLITE_MAGIC = b"SQLite format 3\x00"


def _read_qstring(data: bytes, pos: int) -> Tuple[str, int]:
    if pos + 4 > len(data):
        raise PurRefError("Truncated PureRef 2.x header")
    nbytes = struct.unpack_from(">I", data, pos)[0]
    pos += 4
    if nbytes == 0xFFFFFFFF:
        return "", pos
    if nbytes < 0 or pos + nbytes > len(data):
        raise PurRefError("Invalid string in PureRef 2.x header")
    text = data[pos : pos + nbytes].decode("utf-16-be", errors="replace").strip("\x00")
    return text, pos + nbytes


def _sqlite_region(data: bytes) -> Tuple[int, int]:
    """Return (swap_len, sqlite_end) for the restored DB slice."""
    if len(data) < 32:
        raise PurRefError("File too small to be a PureRef 2.x scene")

    pos = 0
    ver_len = struct.unpack_from(">I", data, pos)[0]
    pos = 4 + ver_len
    if pos + 12 > len(data):
        raise PurRefError("Truncated PureRef 2.x header")
    # two unknown u32s, then byte offset where the swapped SQLite head lives
    _u0 = struct.unpack_from(">I", data, pos)[0]
    _u1 = struct.unpack_from(">I", data, pos + 4)[0]
    sqlite_end = struct.unpack_from(">I", data, pos + 8)[0]
    pos += 12
    _app_ver, pos = _read_qstring(data, pos)
    _checksum, pos = _read_qstring(data, pos)

    if sqlite_end <= 0 or sqlite_end >= len(data):
        # Fallback: find trailing SQLite header.
        idx = data.rfind(_SQLITE_MAGIC)
        if idx <= 0:
            raise PurRefError("PureRef 2.x SQLite payload not found")
        sqlite_end = idx

    if data[sqlite_end : sqlite_end + 16] != _SQLITE_MAGIC:
        idx = data.rfind(_SQLITE_MAGIC)
        if idx <= 0:
            raise PurRefError("PureRef 2.x SQLite header missing")
        sqlite_end = idx

    swap_len = len(data) - sqlite_end
    if swap_len < 100 or swap_len >= sqlite_end:
        raise PurRefError("PureRef 2.x swap region looks invalid")
    return swap_len, sqlite_end


def _restore_sqlite(data: bytes) -> bytes:
    swap_len, sqlite_end = _sqlite_region(data)
    tail = data[sqlite_end:]
    db = bytearray(data[:sqlite_end])
    db[:swap_len] = tail
    if db[:16] != _SQLITE_MAGIC:
        raise PurRefError("Failed to restore PureRef 2.x SQLite database")
    return bytes(db)


_TMP_PATHS: dict[int, str] = {}


def _open_restored_db(data: bytes) -> sqlite3.Connection:
    db_bytes = _restore_sqlite(data)
    # sqlite needs a real file path for some BLOB sizes; temp file is fine.
    tmp = tempfile.NamedTemporaryFile(prefix="moodboard_pur_", suffix=".sqlite", delete=False)
    try:
        tmp.write(db_bytes)
        tmp.close()
        conn = sqlite3.connect(tmp.name)
        conn.row_factory = sqlite3.Row
        _TMP_PATHS[id(conn)] = tmp.name
        return conn
    except Exception:
        try:
            Path(tmp.name).unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _close_db(conn: sqlite3.Connection) -> None:
    path = _TMP_PATHS.pop(id(conn), None)
    try:
        conn.close()
    finally:
        if path:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass


def _format_suffix(fmt: Optional[str]) -> str:
    text = (fmt or "png").strip().upper()
    if text in {"JPEG", "JPG"}:
        return ".jpg"
    if text == "WEBP":
        return ".webp"
    if text == "GIF":
        return ".gif"
    return ".png"


def _text_as_bytes(value) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("latin-1", errors="replace")
    return bytes(value)


def _parse_qtransform(blob_text) -> Optional[Tuple[float, ...]]:
    """
    PureRef stores QTransform-like payloads as TEXT containing:
      u32be size | u8 pad | 9 x f64be  (m11..m33)
    """
    raw = _text_as_bytes(blob_text)
    if len(raw) < 4 + 1 + 72:
        return None
    off = 5  # skip size + pad
    values = []
    for _ in range(9):
        if off + 8 > len(raw):
            return None
        values.append(struct.unpack_from(">d", raw, off)[0])
        off += 8
    return tuple(values)


def _placement_from_transforms(
    width_px: int,
    height_px: int,
    item_tr,
    image_tr,
) -> Tuple[float, float, float, float, float]:
    """Return center x, y, display w, display h, rotation_deg."""
    it = _parse_qtransform(item_tr)
    im = _parse_qtransform(image_tr)

    scale_x = 1.0
    scale_y = 1.0
    rotation = 0.0
    center_x = 0.0
    center_y = 0.0

    if it:
        m11, m12, _m13, m21, m22, _m23, m31, m32, _m33 = it
        scale_x = math.hypot(m11, m12) or abs(m11) or 1.0
        scale_y = math.hypot(m21, m22) or abs(m22) or 1.0
        rotation = math.degrees(math.atan2(m12, m11)) if (m11 or m12) else 0.0
        center_x = m31
        center_y = m32

    # image_transform usually centers local pixels (-w/2, -h/2).
    # When present with identity scale, item translation is already the center.
    if im:
        _a, _b, _c, _d, _e, _f, im_tx, im_ty, _ = im
        # If item transform missing, fall back to image translation.
        if not it:
            center_x = -im_tx
            center_y = -im_ty

    disp_w = max(float(width_px) * scale_x, 1.0)
    disp_h = max(float(height_px) * scale_y, 1.0)
    return center_x, center_y, disp_w, disp_h, rotation


def _grid_fallback(
    width_px: int,
    height_px: int,
    index: int,
    count: int,
    cell: float = 280.0,
) -> Tuple[float, float, float, float, float]:
    columns = max(1, int(math.ceil(math.sqrt(max(count, 1)))))
    col = index % columns
    row = index // columns
    longest = max(width_px, height_px, 1)
    scale = cell / longest
    disp_w = width_px * scale
    disp_h = height_px * scale
    return col * cell, row * cell, disp_w, disp_h, 0.0


def read_modern_pur(path: str) -> PurScene:
    data = open(path, "rb").read()
    if not data:
        raise PurRefError("Empty file")

    version = peek_pur_version(data)
    if not is_modern_version(version):
        raise PurRefError(f"Not a PureRef 2.x scene (version {version!r})")

    try:
        conn = _open_restored_db(data)
    except PurRefError:
        raise
    except Exception as exc:
        raise PurRefError(f"Could not open PureRef 2.x database: {exc}") from exc

    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "images" not in tables or "items_images" not in tables:
            raise PurRefError("PureRef 2.x database is missing image tables")

        rows = conn.execute(
            """
            SELECT
                i.id AS item_id,
                i.name AS item_name,
                i.z AS z,
                i.transform AS item_transform,
                ii.image_transform AS image_transform,
                im.id AS image_id,
                im.source AS source,
                im.format AS format,
                im.data AS data,
                im.width AS width,
                im.height AS height
            FROM items_images AS ii
            JOIN items AS i ON i.id = ii.id
            JOIN images AS im ON im.id = ii.image
            WHERE im.data IS NOT NULL AND length(im.data) > 32
            ORDER BY i.z ASC, i.id ASC
            """
        ).fetchall()

        if not rows:
            raise PurRefError(
                "PureRef 2.x scene found, but no embedded images were in the database. "
                "Linked-only images are not supported yet."
            )

        placements: List[PurImagePlacement] = []
        used_transforms = 0
        for index, row in enumerate(rows):
            blob = bytes(row["data"])
            width = int(row["width"] or 0)
            height = int(row["height"] or 0)
            if width <= 0 or height <= 0:
                continue

            x, y, disp_w, disp_h, rotation = _placement_from_transforms(
                width,
                height,
                row["item_transform"],
                row["image_transform"],
            )
            if _parse_qtransform(row["item_transform"]):
                used_transforms += 1
            else:
                x, y, disp_w, disp_h, rotation = _grid_fallback(
                    width, height, index, len(rows)
                )

            name = row["item_name"] or f"pur_{int(row['item_id']):03d}"
            if isinstance(name, bytes):
                name = name.decode("utf-8", errors="replace")
            source = row["source"] or ""
            if isinstance(source, bytes):
                source = source.decode("utf-8", errors="replace")

            placements.append(
                PurImagePlacement(
                    data=blob,
                    suffix=_format_suffix(row["format"]),
                    x=float(x),
                    y=float(y),
                    width=float(disp_w),
                    height=float(disp_h),
                    rotation_deg=float(rotation),
                    name=str(name),
                    source=str(source),
                    z=float(row["z"] or index),
                )
            )

        if not placements:
            raise PurRefError("PureRef 2.x database had images, but none could be placed")

        note = ""
        if used_transforms == 0:
            note = "grid layout - transforms missing"
        elif used_transforms < len(placements):
            note = f"layout from transforms ({used_transforms}/{len(placements)})"

        return PurScene(
            version=version,
            placements=placements,
            layout_note=note,
        )
    finally:
        _close_db(conn)
