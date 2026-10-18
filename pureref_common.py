"""Shared PureRef import types for legacy + modern readers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple


class PurRefError(Exception):
    pass


@dataclass
class PurImagePlacement:
    """One on-canvas image instance."""

    data: bytes
    suffix: str  # .png / .jpg / .webp
    x: float
    y: float
    width: float
    height: float
    rotation_deg: float = 0.0
    name: str = "image"
    source: str = ""
    z: float = 0.0


@dataclass
class PurScene:
    version: str
    placements: List[PurImagePlacement] = field(default_factory=list)
    canvas: Tuple[float, float, float, float] = (-10000.0, -10000.0, 10000.0, 10000.0)
    zoom: float = 1.0
    layout_note: str = ""  # e.g. "grid (transforms not yet parsed)"


def peek_pur_version(data: bytes) -> str:
    import struct

    if len(data) < 8:
        raise PurRefError("File too small to be a PureRef scene")
    (nbytes,) = struct.unpack_from(">I", data, 0)
    if nbytes <= 0 or nbytes > 64 or 4 + nbytes > len(data):
        raise PurRefError("Not a PureRef scene (bad version header)")
    raw = data[4 : 4 + nbytes]
    if nbytes >= 2 and nbytes % 2 == 0:
        return raw.decode("utf-16-be", errors="replace").strip("\x00")
    return raw.decode("ascii", errors="replace").strip("\x00")


def is_modern_version(version: str) -> bool:
    text = version.lower().replace("pureref", "").strip()
    return text.startswith("2") or text.startswith("3")
