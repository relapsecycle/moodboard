"""PureRef .pur import entry point (legacy 1.x + modern 2.x)."""

from __future__ import annotations

from .pureref_common import PurRefError, PurScene, is_modern_version, peek_pur_version
from .pureref_legacy import read_legacy_pur
from .pureref_modern import read_modern_pur


def read_pur(path: str) -> PurScene:
    data = open(path, "rb").read(256)
    version = peek_pur_version(data)
    if is_modern_version(version):
        return read_modern_pur(path)
    return read_legacy_pur(path)


__all__ = ["read_pur", "PurScene", "PurRefError"]
