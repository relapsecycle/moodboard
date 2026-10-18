import math

import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from .constants import (
    HANDLE_EDGE,
    HANDLE_FILL,
    NODE_ID,
    ROTATE_COLOR,
    SELECT_COLOR,
    TREE_ID,
)
from . import geometry


_handle = None
_NODE_CHROME_HEIGHT = 36.0


def tag_redraw_all_node_editors():
    wm = bpy.context.window_manager
    if not wm:
        return
    for window in wm.windows:
        for area in window.screen.areas:
            if area.type == "NODE_EDITOR":
                area.tag_redraw()


def sync_node_size(node):
    if getattr(node, "bl_idname", None) != NODE_ID:
        return
    # Keep the real Blender node tiny so its header/outline stays easy to cover.
    node.width = 20.0


def _theme_backdrop_color():
    try:
        color = bpy.context.preferences.themes[0].node_editor.space.back
        return (color[0], color[1], color[2], 1.0)
    except Exception:
        return (0.15, 0.15, 0.15, 1.0)


def _draw_line(shader, points, color, width=1.5):
    batch = batch_for_shader(shader, "LINE_STRIP", {"pos": points})
    shader.bind()
    shader.uniform_float("color", color)
    gpu.state.line_width_set(width)
    batch.draw(shader)


def _draw_filled_rect(shader, cx, cy, size, color, rotation_deg=0.0):
    corners = geometry.quad_corners(cx, cy, size, size, rotation_deg)
    batch = batch_for_shader(shader, "TRI_FAN", {"pos": corners})
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_axis_rect(shader, x, y, width, height, color):
    """Cover Blender's axis-aligned node widget (location = top-left)."""
    corners = (
        (x, y),
        (x + width, y),
        (x + width, y - height),
        (x, y - height),
    )
    batch = batch_for_shader(shader, "TRI_FAN", {"pos": corners})
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_circle(shader, cx, cy, radius, color, segments=20, filled=True):
    if filled:
        pts = [(cx, cy)]
        pts.extend(
            (
                cx + math.cos((i / segments) * math.tau) * radius,
                cy + math.sin((i / segments) * math.tau) * radius,
            )
            for i in range(segments + 1)
        )
        mode = "TRI_FAN"
    else:
        pts = [
            (
                cx + math.cos((i / segments) * math.tau) * radius,
                cy + math.sin((i / segments) * math.tau) * radius,
            )
            for i in range(segments + 1)
        ]
        mode = "LINE_STRIP"
    batch = batch_for_shader(shader, mode, {"pos": pts})
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_gizmos(line_shader, solid_shader, node, region):
    giz = geometry.gizmo_points(node, region)
    corners = giz["corners"]
    hs = giz["handle_size"]

    _draw_line(line_shader, list(corners) + [corners[0]], SELECT_COLOR, width=2.0)
    _draw_line(line_shader, [giz["top_mid"], giz["handles"]["rotate"]], ROTATE_COLOR, width=1.5)

    for name, pos in giz["handles"].items():
        if name == "rotate":
            _draw_circle(solid_shader, pos[0], pos[1], hs * 0.55, ROTATE_COLOR, filled=True)
            _draw_circle(line_shader, pos[0], pos[1], hs * 0.55, HANDLE_EDGE, filled=False)
        else:
            _draw_filled_rect(solid_shader, pos[0], pos[1], hs, HANDLE_FILL, node.rotation)
            edge = geometry.quad_corners(pos[0], pos[1], hs, hs, node.rotation)
            _draw_line(line_shader, list(edge) + [edge[0]], HANDLE_EDGE, width=1.0)


def _cover_node_widgets(solid_shader, tree, color):
    for node in tree.nodes:
        if node.bl_idname != NODE_ID:
            continue
        x, y = node.location
        width = max(float(getattr(node, "width", 20.0)), 20.0)
        try:
            height = max(float(node.dimensions.y), _NODE_CHROME_HEIGHT)
        except Exception:
            height = _NODE_CHROME_HEIGHT
        # Pad a bit so selection outlines do not peek out when the image rotates.
        pad = 4.0
        _draw_axis_rect(
            solid_shader,
            x - pad,
            y + pad,
            width + pad * 2.0,
            height + pad * 2.0,
            color,
        )


def draw_callback():
    context = bpy.context
    space = context.space_data
    if not space or space.type != "NODE_EDITOR":
        return
    if space.tree_type != TREE_ID:
        return

    tree = space.edit_tree
    if not tree or tree.bl_idname != TREE_ID:
        return

    region = context.region

    try:
        image_shader = gpu.shader.from_builtin("IMAGE")
    except ValueError:
        image_shader = gpu.shader.from_builtin("IMAGE_COLOR")

    line_shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    solid_shader = line_shader
    gpu.state.blend_set("ALPHA")

    # Paint over Blender's node widgets first so rotation never reveals them.
    _cover_node_widgets(solid_shader, tree, _theme_backdrop_color())

    for node in tree.nodes:
        if node.bl_idname != NODE_ID or not node.image:
            continue

        image = node.image
        try:
            image.gl_load()
            texture = gpu.texture.from_image(image)
        except Exception:
            continue

        cx, cy, draw_w, draw_h = geometry.node_image_rect(node)
        corners = geometry.quad_corners(cx, cy, draw_w, draw_h, node.rotation)

        uvs = ((0.0, 1.0), (1.0, 1.0), (1.0, 0.0), (0.0, 0.0))
        batch = batch_for_shader(
            image_shader,
            "TRI_FAN",
            {"pos": corners, "texCoord": uvs},
        )
        image_shader.bind()
        try:
            image_shader.uniform_sampler("image", texture)
        except Exception:
            continue
        batch.draw(image_shader)

        if node.select:
            _draw_gizmos(line_shader, solid_shader, node, region)

    gpu.state.blend_set("NONE")
    gpu.state.line_width_set(1.0)


def register():
    global _handle
    if _handle is None:
        _handle = bpy.types.SpaceNodeEditor.draw_handler_add(
            draw_callback,
            (),
            "WINDOW",
            "POST_VIEW",
        )


def unregister():
    global _handle
    if _handle is not None:
        bpy.types.SpaceNodeEditor.draw_handler_remove(_handle, "WINDOW")
        _handle = None
