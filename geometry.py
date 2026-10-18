"""Shared geometry for image quads, gizmos, and hit-testing."""

from __future__ import annotations

import math

from .constants import (
    BASE_DISPLAY_SIZE,
    HANDLE_PIXELS,
    HIT_PADDING_PIXELS,
    NODE_ID,
    ROTATE_HANDLE_OFFSET_PIXELS,
)


def pixels_to_view(region, pixels: float) -> float:
    if region is None:
        return pixels
    x0, _y0 = region.view2d.region_to_view(0.0, 0.0)
    x1, _y1 = region.view2d.region_to_view(float(pixels), 0.0)
    size = abs(x1 - x0)
    return size if size > 1e-6 else float(pixels)


def image_draw_size(node, image=None):
    image = image or getattr(node, "image", None)
    if not image:
        return BASE_DISPLAY_SIZE, BASE_DISPLAY_SIZE * 0.75

    width, height = image.size
    longest = max(width, height, 1)
    factor = (BASE_DISPLAY_SIZE / longest) * max(node.scale, 0.05)
    return width * factor, height * factor


def rotate_point(px, py, cx, cy, rotation_deg):
    angle = math.radians(rotation_deg)
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    dx = px - cx
    dy = py - cy
    return cx + dx * cos_a - dy * sin_a, cy + dx * sin_a + dy * cos_a


def quad_corners(center_x, center_y, width, height, rotation_deg):
    hw = width * 0.5
    hh = height * 0.5
    local = ((-hw, hh), (hw, hh), (hw, -hh), (-hw, -hh))
    return [
        rotate_point(center_x + dx, center_y + dy, center_x, center_y, rotation_deg)
        for dx, dy in local
    ]


def node_image_rect(node):
    """Return (center_x, center_y, draw_w, draw_h) with top-left at node.location."""
    draw_w, draw_h = image_draw_size(node)
    x, y = node.location
    center_x = x + draw_w * 0.5
    center_y = y - draw_h * 0.5
    return center_x, center_y, draw_w, draw_h


def set_node_location_from_center(node, center_x, center_y):
    draw_w, draw_h = image_draw_size(node)
    node.location = (center_x - draw_w * 0.5, center_y + draw_h * 0.5)


def gizmo_points(node, region):
    """Corner + rotate handle positions in view space."""
    cx, cy, draw_w, draw_h = node_image_rect(node)
    corners = quad_corners(cx, cy, draw_w, draw_h, node.rotation)
    handle = pixels_to_view(region, HANDLE_PIXELS)
    rot_offset = pixels_to_view(region, ROTATE_HANDLE_OFFSET_PIXELS)

    top_mid = rotate_point(cx, cy + draw_h * 0.5, cx, cy, node.rotation)
    # Offset along the rotated "up" axis from center through top mid.
    up = rotate_point(cx, cy + 1.0, cx, cy, node.rotation)
    up_dir_x = up[0] - cx
    up_dir_y = up[1] - cy
    rotate_pos = (top_mid[0] + up_dir_x * rot_offset, top_mid[1] + up_dir_y * rot_offset)

    return {
        "center": (cx, cy),
        "size": (draw_w, draw_h),
        "corners": corners,
        "handles": {
            "scale_tl": corners[0],
            "scale_tr": corners[1],
            "scale_br": corners[2],
            "scale_bl": corners[3],
            "rotate": rotate_pos,
        },
        "handle_size": handle,
        "top_mid": top_mid,
    }


def point_in_rotated_rect(px, py, cx, cy, width, height, rotation_deg, padding=0.0):
    angle = math.radians(-rotation_deg)
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    dx = px - cx
    dy = py - cy
    lx = dx * cos_a - dy * sin_a
    ly = dx * sin_a + dy * cos_a
    hw = width * 0.5 + padding
    hh = height * 0.5 + padding
    return abs(lx) <= hw and abs(ly) <= hh


def _dist2(a, b):
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    return dx * dx + dy * dy


def hit_test_board(tree, view_x, view_y, region):
    """Return (node, mode) where mode is rotate/scale_*/move, or (None, None)."""
    if tree is None:
        return None, None

    pad = pixels_to_view(region, HIT_PADDING_PIXELS)
    handle_r = pixels_to_view(region, HANDLE_PIXELS) * 0.5 + pad
    handle_r2 = handle_r * handle_r

    nodes = [n for n in tree.nodes if n.bl_idname == NODE_ID and n.image]

    for node in reversed(nodes):
        if not node.select:
            continue
        giz = gizmo_points(node, region)
        for name, pos in giz["handles"].items():
            if _dist2((view_x, view_y), pos) <= handle_r2:
                return node, name

    for node in reversed(nodes):
        cx, cy, draw_w, draw_h = node_image_rect(node)
        if point_in_rotated_rect(view_x, view_y, cx, cy, draw_w, draw_h, node.rotation, pad):
            return node, "move"

    return None, None
