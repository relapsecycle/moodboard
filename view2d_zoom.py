"""Relax Node Editor View2D zoom-out clamp for MoodBoard regions.

Blender hardcodes node-editor minzoom at 0.05 in C and does not expose it to RNA.
We adjust the region's View2D DNA fields while MoodBoard is active.
"""

from __future__ import annotations

import ctypes
from ctypes import c_char, c_float, c_int, c_short, c_void_p


# Match DNA rctf / rcti / View2D field order (Blender 4.2–5.x).
class _Rctf(ctypes.Structure):
    _fields_ = (
        ("xmin", c_float),
        ("xmax", c_float),
        ("ymin", c_float),
        ("ymax", c_float),
    )


class _Rcti(ctypes.Structure):
    _fields_ = (
        ("xmin", c_int),
        ("xmax", c_int),
        ("ymin", c_int),
        ("ymax", c_int),
    )


class _View2D(ctypes.Structure):
    _fields_ = (
        ("tot", _Rctf),
        ("cur", _Rctf),
        ("vert", _Rcti),
        ("hor", _Rcti),
        ("mask", _Rcti),
        ("min", c_float * 2),
        ("max", c_float * 2),
        ("minzoom", c_float),
        ("maxzoom", c_float),
        ("scroll", c_short),
        ("scroll_ui", c_short),
        ("keeptot", c_short),
        ("keepzoom", c_short),
        ("keepofs", c_short),
        ("flag", c_short),
        ("align", c_short),
        ("winx", c_short),
        ("winy", c_short),
        ("oldwinx", c_short),
        ("oldwiny", c_short),
        ("around", c_short),
        ("alpha_vert", c_char),
        ("alpha_hor", c_char),
        ("_pad", c_char * 2),
        ("page_size_y", c_float),
        ("sms", c_void_p),
        ("smooth_timer", c_void_p),
    )


# Default node editor minzoom is 0.05; allow ~50x further zoom-out.
_MOODBOARD_MINZOOM = 0.001
_MOODBOARD_MAXZOOM = 8.0  # slightly freer zoom-in too
_patched = set()


def _view2d_from_region(region):
    try:
        ptr = region.view2d.as_pointer()
    except Exception:
        return None
    if not ptr:
        return None
    return ctypes.cast(ptr, ctypes.POINTER(_View2D)).contents


def relax_moodboard_zoom(region) -> bool:
    """Lower zoom-out clamp on this region. Returns True if applied."""
    if region is None or getattr(region, "type", None) != "WINDOW":
        return False

    v2d = _view2d_from_region(region)
    if v2d is None:
        return False

    key = region.as_pointer()
    # Already at our target (or lower) — skip writes.
    if key in _patched and v2d.minzoom <= _MOODBOARD_MINZOOM + 1e-6:
        return False

    try:
        if v2d.minzoom > _MOODBOARD_MINZOOM:
            v2d.minzoom = _MOODBOARD_MINZOOM
        if v2d.maxzoom < _MOODBOARD_MAXZOOM:
            v2d.maxzoom = _MOODBOARD_MAXZOOM
        # Keep LIMITZOOM so we still have a soft floor; just a much lower one.
        _patched.add(key)
        return True
    except Exception:
        return False


def clear_patch_cache():
    _patched.clear()
