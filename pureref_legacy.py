"""
Legacy PureRef 1.x (.pur) reader for MoodBoard.

Original MoodBoard code. Format layout is publicly documented
(header → embedded PNGs → item transforms → path → id/address refs).
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import List, Tuple

from .pureref_common import (
    PurImagePlacement,
    PurRefError,
    PurScene,
    is_modern_version,
    peek_pur_version,
)


PNG_SIG = b"\x89PNG\r\n\x1a\n"
PNG_IEND = b"\x00\x00\x00\x00IEND\xaeB`\x82"

# Item type discriminator: byte length of the type-name string field.
_TYPE_IMAGE = 34
_TYPE_TEXT = 32


class _ByteCursor:
    def __init__(self, data: bytearray):
        self.data = data
        self.origin = 0  # absolute file offset of data[0]

    @property
    def abs_pos(self) -> int:
        return self.origin

    def remaining(self) -> int:
        return len(self.data)

    def peek(self, fmt: str, at: int = 0):
        size = struct.calcsize(fmt)
        return struct.unpack_from(fmt, self.data, at)[0]

    def take(self, fmt: str):
        size = struct.calcsize(fmt)
        value = struct.unpack_from(fmt, self.data, 0)[0]
        self._drop(size)
        return value

    def take_bytes(self, n: int) -> bytes:
        chunk = bytes(self.data[:n])
        self._drop(n)
        return chunk

    def take_matrix(self) -> Tuple[float, float, float, float]:
        # 6 doubles; PureRef only uses the four diagonal/off-diagonal slots.
        a = self.peek(">d", 0)
        b = self.peek(">d", 8)
        c = self.peek(">d", 24)
        d = self.peek(">d", 32)
        self._drop(48)
        return a, b, c, d

    def take_string(self) -> str:
        nbytes = self.take(">I")
        raw = self.take_bytes(nbytes)
        return raw.decode("utf-16-be", errors="replace")

    def _drop(self, n: int):
        del self.data[:n]
        self.origin += n


def _read_header(cur: _ByteCursor) -> Tuple[str, Tuple[float, float, float, float], float]:
    if cur.remaining() < 224:
        raise PurRefError("Truncated PureRef header")
    version = peek_pur_version(bytes(cur.data[:80]))
    canvas = (
        cur.peek(">d", 112),
        cur.peek(">d", 120),
        cur.peek(">d", 128),
        cur.peek(">d", 136),
    )
    zoom = cur.peek(">d", 144)
    cur._drop(224)
    return version, canvas, zoom


@dataclass
class _EmbeddedImage:
    address: Tuple[int, int]
    payload: bytes  # PNG bytes, or 4-byte transform-id duplicate token


@dataclass
class _ImageItem:
    id: int
    x: float
    y: float
    z: float
    matrix: Tuple[float, float, float, float]
    points: Tuple[List[float], List[float]]
    name: str
    source: str


def _read_images(cur: _ByteCursor) -> List[_EmbeddedImage]:
    images: List[_EmbeddedImage] = []

    while PNG_SIG in cur.data:
        start = cur.data.find(PNG_SIG)
        end = cur.data.find(PNG_IEND)
        if end < 0:
            raise PurRefError("Corrupt PureRef image block (missing PNG end)")
        end += len(PNG_IEND)

        if start >= 4:
            # Duplicate / link marker before the next real PNG.
            addr0 = cur.abs_pos
            payload = cur.take_bytes(4)
            images.append(_EmbeddedImage((addr0, addr0 + 4), payload))
            continue

        if start != 0:
            # Unexpected padding; skip up to PNG.
            cur._drop(start)
            continue

        addr0 = cur.abs_pos
        payload = cur.take_bytes(end)
        images.append(_EmbeddedImage((addr0, addr0 + end), payload))

    # Trailing duplicate tokens until item records begin.
    while cur.remaining() >= 12:
        type_len = cur.peek(">I", 8)
        if type_len in {_TYPE_IMAGE, _TYPE_TEXT}:
            break
        addr0 = cur.abs_pos
        payload = cur.take_bytes(4)
        images.append(_EmbeddedImage((addr0, addr0 + 4), payload))

    return images


def _skip_text_children(cur: _ByteCursor, count: int):
    for _ in range(count):
        _read_text_item(cur)


def _read_text_item(cur: _ByteCursor):
    end_addr = cur.peek(">Q", 0)
    type_len = cur.peek(">I", 8)
    cur._drop(12 + type_len)
    cur.take_string()
    cur.take_matrix()
    cur.take(">d")
    cur.take(">d")
    cur._drop(8)
    cur.take(">I")
    cur.take(">d")
    # colors
    cur.take(">b")
    cur.take(">H")
    cur.take(">H")
    cur.take(">H")
    cur.take(">H")
    cur._drop(2)
    cur.take(">b")
    cur.take(">H")
    cur.take(">H")
    cur.take(">H")
    cur.take(">H")
    children = cur.peek(">I", 2)
    need = end_addr - cur.abs_pos
    if need > 0:
        cur._drop(need)
    _skip_text_children(cur, children)


def _read_image_item(cur: _ByteCursor) -> _ImageItem:
    end_addr = cur.peek(">Q", 0)
    type_len = cur.peek(">I", 8)
    cur._drop(12 + type_len)

    brute = False
    if cur.peek(">I", 0) == 0:
        brute = True
        cur._drop(4)

    if cur.peek(">i", 0) == -1:
        cur._drop(4)
        source = ""
    else:
        source = cur.take_string()

    name = "image"
    if not brute:
        if cur.peek(">i", 0) == -1:
            cur._drop(4)
        else:
            name = cur.take_string()

    cur._drop(8)
    matrix = cur.take_matrix()
    x = cur.take(">d")
    y = cur.take(">d")
    cur._drop(8)
    item_id = cur.take(">I")
    z = cur.take(">d")
    cur.take_matrix()  # matrix before crop
    cur.take(">d")  # crop x
    cur.take(">d")  # crop y
    cur.take(">d")  # crop scale

    point_count = cur.take(">I")
    xs: List[float] = []
    ys: List[float] = []
    for _ in range(point_count):
        cur._drop(4)
        xs.append(cur.take(">d"))
        ys.append(cur.take(">d"))

    children = cur.peek(">I", 21)
    need = end_addr - cur.abs_pos
    if need > 0:
        cur._drop(need)
    _skip_text_children(cur, children)

    return _ImageItem(
        id=item_id,
        x=x,
        y=y,
        z=z,
        matrix=matrix,
        points=(xs, ys),
        name=name or "image",
        source=source or "",
    )


def _read_items(cur: _ByteCursor) -> List[_ImageItem]:
    items: List[_ImageItem] = []
    while cur.remaining() >= 12:
        type_len = cur.peek(">I", 8)
        if type_len == _TYPE_IMAGE:
            items.append(_read_image_item(cur))
        elif type_len == _TYPE_TEXT:
            _read_text_item(cur)
        else:
            break
    return items


def _item_size(item: _ImageItem) -> Tuple[float, float, float]:
    xs, ys = item.points
    if len(xs) >= 3 and len(ys) >= 3:
        base_w = abs(xs[2] - xs[0])
        base_h = abs(ys[2] - ys[0])
    else:
        base_w, base_h = 1000.0, 1000.0

    a, b, c, d = item.matrix
    # 2x2 linear map [a b; c d] — scale from column lengths, rotation from first column.
    scale_x = math.hypot(a, c) or abs(a) or 1.0
    scale_y = math.hypot(b, d) or abs(d) or 1.0
    rotation = math.degrees(math.atan2(c, a)) if (a or c) else 0.0

    width = max(base_w * scale_x, 1.0)
    height = max(base_h * scale_y, 1.0)
    return width, height, rotation


def _is_duplicate_token(payload: bytes) -> bool:
    return len(payload) == 4 and payload != b"\xff\xff\xff\xff"


def read_legacy_pur(path: str) -> PurScene:
    raw = open(path, "rb").read()
    if not raw:
        raise PurRefError("Empty file")

    version_peek = peek_pur_version(raw)
    if is_modern_version(version_peek):
        raise PurRefError(
            f"PureRef {version_peek} should be read with the modern importer."
        )

    # Quick reject: legacy packs PNGs.
    if PNG_SIG not in raw:
        raise PurRefError(
            "No embedded PNGs found. This may be a PureRef 2.0+ scene "
            "(or a corrupt / empty board)."
        )

    cur = _ByteCursor(bytearray(raw))
    version, canvas, zoom = _read_header(cur)
    if is_modern_version(version):
        raise PurRefError(
            f"PureRef {version} should be read with the modern importer."
        )

    images = _read_images(cur)
    items = _read_items(cur)

    # Saved folder path (utf-16 string) — skip.
    if cur.remaining() >= 4:
        try:
            cur.take_string()
        except Exception:
            pass

    # id → image address refs
    id_to_address = {}
    for _ in range(len(items)):
        if cur.remaining() < 20:
            break
        ref_id = cur.take(">I")
        addr0 = cur.take(">Q")
        addr1 = cur.take(">Q")
        id_to_address[ref_id] = (addr0, addr1)

    # Build transform-id → real PNG map, resolving duplicates.
    id_to_png: dict[int, bytes] = {}
    address_to_png = {
        img.address[0]: img.payload
        for img in images
        if len(img.payload) > 4 and img.payload.startswith(PNG_SIG)
    }

    for item in items:
        addr = id_to_address.get(item.id)
        if not addr:
            continue
        png = address_to_png.get(addr[0])
        if png:
            id_to_png[item.id] = png

    # Duplicate tokens: 4 bytes = transform id of the image that owns the PNG.
    for img in images:
        if not _is_duplicate_token(img.payload):
            continue
        owner_tid = struct.unpack(">I", img.payload)[0]
        for item in items:
            addr = id_to_address.get(item.id)
            if not addr:
                continue
            if addr[0] == img.address[0] and owner_tid in id_to_png:
                id_to_png[item.id] = id_to_png[owner_tid]

    # Fallback: if refs failed, zip PNGs to items in order.
    real_pngs = [img.payload for img in images if img.payload.startswith(PNG_SIG)]
    if not id_to_png and real_pngs:
        for item, png in zip(items, real_pngs):
            id_to_png[item.id] = png

    placements: List[PurImagePlacement] = []
    for item in items:
        png = id_to_png.get(item.id)
        if not png:
            continue
        width, height, rotation = _item_size(item)
        placements.append(
            PurImagePlacement(
                data=png,
                suffix=".png",
                x=item.x,
                y=item.y,
                width=width,
                height=height,
                rotation_deg=rotation,
                name=item.name,
                source=item.source,
                z=item.z,
            )
        )

    placements.sort(key=lambda p: p.z)

    if not placements:
        raise PurRefError("Legacy .pur parsed, but no image placements were found")

    return PurScene(version=version, placements=placements, canvas=canvas, zoom=zoom)
