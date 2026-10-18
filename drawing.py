import math
import time

import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Matrix

from .constants import (
    HANDLE_EDGE,
    HANDLE_FILL,
    NODE_ID,
    ROTATE_COLOR,
    SELECT_COLOR,
    TREE_ID,
)
from . import geometry
from . import view2d_zoom


_handle = None
_NODE_CHROME_HEIGHT = 36.0
_display_shader = None
_adjust_shader = None
_display_shader_name = ""
_line_shader = None

# --- performance caches ---
_texture_cache = {}  # image.as_pointer() -> (texture, size_xy)
_colorspace_ready = set()  # image.as_pointer()
_unit_image_batch = None
_unit_image_batch_shader = None
_unit_adjust_batch = None
_unit_rect_batch = None
_unit_line_batch = None
_last_cache_prune = 0.0
_CACHE_PRUNE_INTERVAL = 2.0
_MIN_SCREEN_PX = 2.0
_PROXY_SCREEN_PX = 10.0
_VIEW_MARGIN_PX = 64.0


def tag_redraw_all_node_editors(area=None):
    """Redraw MoodBoard editors only (optionally a single area)."""
    if area is not None:
        area.tag_redraw()
        return

    wm = bpy.context.window_manager
    if not wm:
        return
    for window in wm.windows:
        screen = window.screen
        if not screen:
            continue
        for area_item in screen.areas:
            if area_item.type != "NODE_EDITOR":
                continue
            space = area_item.spaces.active
            if space and getattr(space, "tree_type", None) == TREE_ID:
                area_item.tag_redraw()


def sync_node_size(node):
    if getattr(node, "bl_idname", None) != NODE_ID:
        return
    node.width = 20.0


def ensure_display_colorspace(image):
    """sRGB files → scene-linear on sample; pair with IMAGE_SCENE_LINEAR_TO_REC709_SRGB."""
    if image is None:
        return
    key = image.as_pointer()
    if key in _colorspace_ready:
        return

    settings = getattr(image, "colorspace_settings", None)
    if settings is not None and settings.name != "sRGB":
        try:
            settings.name = "sRGB"
            image.gl_free()
            _texture_cache.pop(key, None)
        except Exception:
            pass
    try:
        if getattr(image, "alpha_mode", None) not in {None, "NONE"}:
            image.alpha_mode = "STRAIGHT"
    except Exception:
        pass
    _colorspace_ready.add(key)


def invalidate_image(image):
    if image is None:
        return
    key = image.as_pointer()
    _texture_cache.pop(key, None)
    _colorspace_ready.discard(key)


def display_shader_name():
    _get_display_shader()
    return _display_shader_name or "none"


def _get_display_shader():
    global _display_shader, _display_shader_name
    if _display_shader is not None:
        return _display_shader

    for name in (
        "IMAGE_SCENE_LINEAR_TO_REC709_SRGB",
        "IMAGE_COLOR_SCENE_LINEAR_TO_REC709_SRGB",
        "IMAGE",
    ):
        try:
            _display_shader = gpu.shader.from_builtin(name)
            _display_shader_name = name
            return _display_shader
        except ValueError:
            continue
    _display_shader_name = "none"
    return None


def _get_adjust_shader():
    global _adjust_shader
    if _adjust_shader is not None:
        return _adjust_shader

    try:
        from gpu.types import GPUShaderCreateInfo, GPUStageInterfaceInfo
    except ImportError:
        return None

    try:
        vert_out = GPUStageInterfaceInfo("moodboard_img_iface")
        vert_out.smooth("VEC2", "uvInterp")

        info = GPUShaderCreateInfo()
        info.vertex_in(0, "VEC3", "pos")
        info.vertex_in(1, "VEC2", "texCoord")
        info.sampler(0, "FLOAT_2D", "image")
        info.vertex_out(vert_out)
        info.fragment_out(0, "VEC4", "fragColor")
        info.push_constant("MAT4", "ModelViewProjectionMatrix")
        info.push_constant("FLOAT", "brightness")
        info.push_constant("FLOAT", "contrast")
        info.vertex_source(
            """
void main()
{
  uvInterp = texCoord;
  gl_Position = ModelViewProjectionMatrix * vec4(pos, 1.0);
}
"""
        )
        info.fragment_source(
            """
float linearrgb_to_srgb(float c)
{
  if (c < 0.0031308) {
    return (c < 0.0) ? 0.0 : c * 12.92;
  }
  return 1.055 * pow(c, 1.0 / 2.4) - 0.055;
}

vec3 linearrgb_to_srgb_v3(vec3 c)
{
  return vec3(linearrgb_to_srgb(c.r), linearrgb_to_srgb(c.g), linearrgb_to_srgb(c.b));
}

void main()
{
  vec4 tex = texture(image, uvInterp);
  vec3 srgb = linearrgb_to_srgb_v3(max(tex.rgb, vec3(0.0)));
  srgb = (srgb - vec3(0.5)) * contrast + vec3(0.5) + vec3(brightness);
  fragColor = vec4(clamp(srgb, 0.0, 1.0), 1.0);
}
"""
        )
        _adjust_shader = gpu.shader.create_from_info(info)
    except Exception:
        _adjust_shader = None
    return _adjust_shader


def _get_line_shader():
    global _line_shader
    if _line_shader is None:
        _line_shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    return _line_shader


def _bind_mvp(shader):
    try:
        mvp = gpu.matrix.get_projection_matrix() @ gpu.matrix.get_model_view_matrix()
        shader.uniform_float("ModelViewProjectionMatrix", mvp)
    except Exception:
        try:
            shader.uniform_float("ModelViewProjectionMatrix", Matrix.Identity(4))
        except Exception:
            pass


def _theme_backdrop_color():
    try:
        color = bpy.context.preferences.themes[0].node_editor.space.back
        return (color[0], color[1], color[2], 1.0)
    except Exception:
        return (0.15, 0.15, 0.15, 1.0)


def _as_pos(points_2d):
    if bpy.app.version >= (5, 0, 0):
        return [(p[0], p[1], 0.0) for p in points_2d]
    return [(p[0], p[1]) for p in points_2d]


def _as_pos3(points_2d):
    return [(p[0], p[1], 0.0) for p in points_2d]


def _unit_quad_pos_uv():
    # Bottom-left origin unit quad (BL, BR, TR, TL). V=1 is image top.
    pos = _as_pos3(((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)))
    uvs = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
    return pos, uvs


def _get_unit_image_batch(shader):
    global _unit_image_batch, _unit_image_batch_shader
    if _unit_image_batch is not None and _unit_image_batch_shader is shader:
        return _unit_image_batch
    pos, uvs = _unit_quad_pos_uv()
    _unit_image_batch = batch_for_shader(shader, "TRI_FAN", {"pos": pos, "texCoord": uvs})
    _unit_image_batch_shader = shader
    return _unit_image_batch


def _get_unit_adjust_batch(shader):
    global _unit_adjust_batch
    if _unit_adjust_batch is not None:
        return _unit_adjust_batch
    pos, uvs = _unit_quad_pos_uv()
    _unit_adjust_batch = batch_for_shader(shader, "TRI_FAN", {"pos": pos, "texCoord": uvs})
    return _unit_adjust_batch


def _get_unit_rect_batch(shader):
    global _unit_rect_batch
    if _unit_rect_batch is not None:
        return _unit_rect_batch
    pos = _as_pos(((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)))
    _unit_rect_batch = batch_for_shader(shader, "TRI_FAN", {"pos": pos})
    return _unit_rect_batch


def _prune_caches(active_images):
    global _last_cache_prune
    now = time.monotonic()
    if now - _last_cache_prune < _CACHE_PRUNE_INTERVAL:
        return
    _last_cache_prune = now
    live = {img.as_pointer() for img in active_images if img is not None}
    dead = [key for key in _texture_cache if key not in live]
    for key in dead:
        _texture_cache.pop(key, None)
        _colorspace_ready.discard(key)


def _get_texture(image):
    key = image.as_pointer()
    size = (int(image.size[0]), int(image.size[1]))
    cached = _texture_cache.get(key)
    if cached is not None and cached[1] == size:
        return cached[0]

    ensure_display_colorspace(image)
    try:
        image.gl_load()
        texture = gpu.texture.from_image(image)
    except Exception:
        return None
    _texture_cache[key] = (texture, size)
    return texture


def _view_rect(region):
    x0, y0 = region.view2d.region_to_view(0.0, 0.0)
    x1, y1 = region.view2d.region_to_view(float(region.width), float(region.height))
    xmin, xmax = (x0, x1) if x0 <= x1 else (x1, x0)
    ymin, ymax = (y0, y1) if y0 <= y1 else (y1, y0)
    return xmin, ymin, xmax, ymax


def _px_per_view_unit(region, view_rect):
    xmin, _ymin, xmax, _ymax = view_rect
    view_w = max(xmax - xmin, 1e-6)
    return float(region.width) / view_w


def _is_visible(cx, cy, draw_w, draw_h, view_rect, margin_view):
    radius = 0.5 * math.hypot(draw_w, draw_h) + margin_view
    xmin, ymin, xmax, ymax = view_rect
    return not (
        cx + radius < xmin
        or cx - radius > xmax
        or cy + radius < ymin
        or cy - radius > ymax
    )


def _push_quad_matrix(cx, cy, draw_w, draw_h, rotation_deg):
    gpu.matrix.push()
    gpu.matrix.translate((cx, cy, 0.0))
    if abs(rotation_deg) > 1e-4:
        gpu.matrix.multiply(Matrix.Rotation(math.radians(rotation_deg), 4, "Z"))
    gpu.matrix.translate((-draw_w * 0.5, -draw_h * 0.5, 0.0))
    gpu.matrix.scale((draw_w, draw_h, 1.0))


def _draw_unit_colored(shader, batch, color):
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_line(shader, points, color, width=1.5):
    batch = batch_for_shader(shader, "LINE_STRIP", {"pos": _as_pos(points)})
    shader.bind()
    shader.uniform_float("color", color)
    gpu.state.line_width_set(width)
    batch.draw(shader)


def _draw_filled_rect(shader, cx, cy, size, color, rotation_deg=0.0):
    batch = _get_unit_rect_batch(shader)
    _push_quad_matrix(cx, cy, size, size, rotation_deg)
    _draw_unit_colored(shader, batch, color)
    gpu.matrix.pop()


def _draw_circle(shader, cx, cy, radius, color, segments=12, filled=True):
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
    batch = batch_for_shader(shader, mode, {"pos": _as_pos(pts)})
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_selection_outline(line_shader, node):
    cx, cy, draw_w, draw_h = geometry.node_image_rect(node)
    corners = geometry.quad_corners(cx, cy, draw_w, draw_h, node.rotation)
    _draw_line(line_shader, list(corners) + [corners[0]], SELECT_COLOR, width=2.0)


def _draw_gizmo_handles(line_shader, solid_shader, giz):
    corners = giz["corners"]
    hs = giz["handle_size"]
    rotation = float(giz.get("rotation", 0.0))

    _draw_line(line_shader, list(corners) + [corners[0]], SELECT_COLOR, width=2.0)
    _draw_line(line_shader, [giz["top_mid"], giz["handles"]["rotate"]], ROTATE_COLOR, width=1.5)

    for name, pos in giz["handles"].items():
        if name == "rotate":
            _draw_circle(solid_shader, pos[0], pos[1], hs * 0.55, ROTATE_COLOR, filled=True)
            _draw_circle(line_shader, pos[0], pos[1], hs * 0.55, HANDLE_EDGE, filled=False)
        else:
            _draw_filled_rect(solid_shader, pos[0], pos[1], hs, HANDLE_FILL, rotation)
            edge = geometry.quad_corners(pos[0], pos[1], hs, hs, rotation)
            _draw_line(line_shader, list(edge) + [edge[0]], HANDLE_EDGE, width=1.0)


def _draw_selection_ui(line_shader, solid_shader, selected, region):
    if not selected:
        return
    if len(selected) == 1:
        _draw_gizmo_handles(line_shader, solid_shader, geometry.gizmo_points(selected[0], region))
        return

    # Multi-select: highlight each image, one shared control box.
    for node in selected:
        _draw_selection_outline(line_shader, node)
    group = geometry.group_gizmo_points(selected, region)
    if group is not None:
        _draw_gizmo_handles(line_shader, solid_shader, group)


def _cover_node_widgets(solid_shader, nodes, color, view_rect, margin_view):
    batch = _get_unit_rect_batch(solid_shader)
    pad = 4.0
    width = 20.0 + pad * 2.0
    height = _NODE_CHROME_HEIGHT + pad * 2.0
    solid_shader.bind()
    solid_shader.uniform_float("color", color)

    for node in nodes:
        x, y = node.location
        # Cheap AABB vs view (chrome is tiny).
        if x + width < view_rect[0] - margin_view or x > view_rect[2] + margin_view:
            continue
        if y < view_rect[1] - margin_view or y - height > view_rect[3] + margin_view:
            continue
        gpu.matrix.push()
        gpu.matrix.translate((x - pad, y - height + pad, 0.0))
        gpu.matrix.scale((width, height, 1.0))
        batch.draw(solid_shader)
        gpu.matrix.pop()


def _needs_adjust(node):
    brightness = float(getattr(node, "brightness", 0.0))
    contrast = float(getattr(node, "contrast", 1.0))
    return abs(brightness) > 1e-5 or abs(contrast - 1.0) > 1e-5


def _draw_image_quad(node, texture, display_shader, adjust_shader, cx, cy, draw_w, draw_h):
    rot = float(node.rotation)
    if _needs_adjust(node) and adjust_shader is not None:
        batch = _get_unit_adjust_batch(adjust_shader)
        _push_quad_matrix(cx, cy, draw_w, draw_h, rot)
        adjust_shader.bind()
        _bind_mvp(adjust_shader)
        adjust_shader.uniform_sampler("image", texture)
        adjust_shader.uniform_float("brightness", float(node.brightness))
        adjust_shader.uniform_float("contrast", float(node.contrast))
        batch.draw(adjust_shader)
        gpu.matrix.pop()
        return

    if display_shader is None:
        return
    batch = _get_unit_image_batch(display_shader)
    _push_quad_matrix(cx, cy, draw_w, draw_h, rot)
    display_shader.bind()
    try:
        display_shader.uniform_float("color", (1.0, 1.0, 1.0, 1.0))
    except Exception:
        pass
    display_shader.uniform_sampler("image", texture)
    batch.draw(display_shader)
    gpu.matrix.pop()


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
    if region is None:
        return

    # Node editor clamps zoom-out at 0.05 in C; relax it for MoodBoard boards.
    view2d_zoom.relax_moodboard_zoom(region)

    display_shader = _get_display_shader()
    adjust_shader = _get_adjust_shader()
    if display_shader is None and adjust_shader is None:
        return

    line_shader = _get_line_shader()
    solid_shader = line_shader

    view_rect = _view_rect(region)
    px_per_unit = _px_per_view_unit(region, view_rect)
    margin_view = _VIEW_MARGIN_PX / max(px_per_unit, 1e-6)

    nodes = [n for n in tree.nodes if n.bl_idname == NODE_ID]
    images = [n.image for n in nodes if n.image]
    _prune_caches(images)

    # Cover chrome with alpha, then draw photos opaque.
    gpu.state.blend_set("ALPHA")
    _cover_node_widgets(solid_shader, nodes, _theme_backdrop_color(), view_rect, margin_view)
    gpu.state.blend_set("NONE")

    selected = []
    proxy_batch = _get_unit_rect_batch(solid_shader)

    for node in nodes:
        image = node.image
        if not image:
            continue

        if getattr(node, "use_custom_color", False):
            node.use_custom_color = False

        cx, cy, draw_w, draw_h = geometry.node_image_rect(node)
        if not _is_visible(cx, cy, draw_w, draw_h, view_rect, margin_view):
            continue

        screen_w = draw_w * px_per_unit
        screen_h = draw_h * px_per_unit
        if screen_w < _MIN_SCREEN_PX and screen_h < _MIN_SCREEN_PX:
            continue

        # Far zoom: skip texture sampling, draw a cheap stand-in.
        if screen_w < _PROXY_SCREEN_PX and screen_h < _PROXY_SCREEN_PX:
            _push_quad_matrix(cx, cy, draw_w, draw_h, float(node.rotation))
            _draw_unit_colored(solid_shader, proxy_batch, (0.22, 0.22, 0.22, 1.0))
            gpu.matrix.pop()
            if node.select:
                selected.append(node)
            continue

        texture = _get_texture(image)
        if texture is None:
            continue

        try:
            _draw_image_quad(node, texture, display_shader, adjust_shader, cx, cy, draw_w, draw_h)
        except Exception:
            invalidate_image(image)
            continue

        if node.select:
            selected.append(node)

    if selected:
        gpu.state.blend_set("ALPHA")
        _draw_selection_ui(line_shader, solid_shader, selected, region)
        gpu.state.blend_set("NONE")

    gpu.state.blend_set("NONE")
    gpu.state.line_width_set(1.0)


def register():
    global _handle, _display_shader, _adjust_shader, _display_shader_name
    global _line_shader, _unit_image_batch, _unit_image_batch_shader
    global _unit_adjust_batch, _unit_rect_batch
    _display_shader = None
    _adjust_shader = None
    _display_shader_name = ""
    _line_shader = None
    _unit_image_batch = None
    _unit_image_batch_shader = None
    _unit_adjust_batch = None
    _unit_rect_batch = None
    _texture_cache.clear()
    _colorspace_ready.clear()
    view2d_zoom.clear_patch_cache()
    if _handle is None:
        _handle = bpy.types.SpaceNodeEditor.draw_handler_add(
            draw_callback,
            (),
            "WINDOW",
            "POST_VIEW",
        )


def unregister():
    global _handle, _display_shader, _adjust_shader, _display_shader_name
    global _line_shader, _unit_image_batch, _unit_image_batch_shader
    global _unit_adjust_batch, _unit_rect_batch
    if _handle is not None:
        bpy.types.SpaceNodeEditor.draw_handler_remove(_handle, "WINDOW")
        _handle = None
    _display_shader = None
    _adjust_shader = None
    _display_shader_name = ""
    _line_shader = None
    _unit_image_batch = None
    _unit_image_batch_shader = None
    _unit_adjust_batch = None
    _unit_rect_batch = None
    _texture_cache.clear()
    _colorspace_ready.clear()
    view2d_zoom.clear_patch_cache()
