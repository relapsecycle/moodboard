import math

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


_handle = None
_NODE_CHROME_HEIGHT = 36.0
_display_shader = None
_adjust_shader = None
_display_shader_name = ""


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
    node.width = 20.0


def ensure_display_colorspace(image):
    """sRGB files → scene-linear on sample; pair with IMAGE_SCENE_LINEAR_TO_REC709_SRGB."""
    settings = getattr(image, "colorspace_settings", None)
    if settings is not None and settings.name != "sRGB":
        try:
            settings.name = "sRGB"
            image.gl_free()
        except Exception:
            pass
    try:
        if getattr(image, "alpha_mode", None) not in {None, "NONE"}:
            image.alpha_mode = "STRAIGHT"
    except Exception:
        pass


def display_shader_name():
    _get_display_shader()
    return _display_shader_name or "none"


def _get_display_shader():
    """
    Blender 5.x Image textures sample scene-linear. Node editor target is Rec.709 sRGB.
    Plain IMAGE draws linear as if it were sRGB → crushed/dark vs the browser.
    """
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


def _draw_line(shader, points, color, width=1.5):
    batch = batch_for_shader(shader, "LINE_STRIP", {"pos": _as_pos(points)})
    shader.bind()
    shader.uniform_float("color", color)
    gpu.state.line_width_set(width)
    batch.draw(shader)


def _draw_filled_rect(shader, cx, cy, size, color, rotation_deg=0.0):
    corners = geometry.quad_corners(cx, cy, size, size, rotation_deg)
    batch = batch_for_shader(shader, "TRI_FAN", {"pos": _as_pos(corners)})
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_axis_rect(shader, x, y, width, height, color):
    corners = (
        (x, y),
        (x + width, y),
        (x + width, y - height),
        (x, y - height),
    )
    batch = batch_for_shader(shader, "TRI_FAN", {"pos": _as_pos(corners)})
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
    batch = batch_for_shader(shader, mode, {"pos": _as_pos(pts)})
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
        pad = 4.0
        _draw_axis_rect(
            solid_shader,
            x - pad,
            y + pad,
            width + pad * 2.0,
            height + pad * 2.0,
            color,
        )


def _needs_adjust(node):
    brightness = float(getattr(node, "brightness", 0.0))
    contrast = float(getattr(node, "contrast", 1.0))
    return abs(brightness) > 1e-5 or abs(contrast - 1.0) > 1e-5


def _draw_with_builtin(shader, corners, uvs, texture):
    batch = batch_for_shader(
        shader,
        "TRI_FAN",
        {"pos": _as_pos(corners), "texCoord": uvs},
    )
    shader.bind()
    try:
        shader.uniform_float("color", (1.0, 1.0, 1.0, 1.0))
    except Exception:
        pass
    shader.uniform_sampler("image", texture)
    batch.draw(shader)


def _draw_node_image(node, texture, display_shader, adjust_shader):
    cx, cy, draw_w, draw_h = geometry.node_image_rect(node)
    corners = geometry.quad_corners(cx, cy, draw_w, draw_h, node.rotation)
    uvs = ((0.0, 1.0), (1.0, 1.0), (1.0, 0.0), (0.0, 0.0))

    if _needs_adjust(node) and adjust_shader is not None:
        batch = batch_for_shader(
            adjust_shader,
            "TRI_FAN",
            {"pos": _as_pos3(corners), "texCoord": uvs},
        )
        adjust_shader.bind()
        _bind_mvp(adjust_shader)
        adjust_shader.uniform_sampler("image", texture)
        adjust_shader.uniform_float("brightness", float(node.brightness))
        adjust_shader.uniform_float("contrast", float(node.contrast))
        batch.draw(adjust_shader)
        return

    if display_shader is None:
        return
    _draw_with_builtin(display_shader, corners, uvs, texture)


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

    display_shader = _get_display_shader()
    adjust_shader = _get_adjust_shader()
    if display_shader is None and adjust_shader is None:
        return

    line_shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    solid_shader = line_shader

    # Cover chrome with alpha, then draw photos opaque so premul/alpha cannot crush them.
    gpu.state.blend_set("ALPHA")
    _cover_node_widgets(solid_shader, tree, _theme_backdrop_color())
    gpu.state.blend_set("NONE")

    for node in tree.nodes:
        if node.bl_idname != NODE_ID or not node.image:
            continue

        if getattr(node, "use_custom_color", False):
            node.use_custom_color = False

        image = node.image
        try:
            ensure_display_colorspace(image)
            image.gl_load()
            texture = gpu.texture.from_image(image)
        except Exception:
            continue

        try:
            _draw_node_image(node, texture, display_shader, adjust_shader)
        except Exception:
            continue

        if node.select:
            gpu.state.blend_set("ALPHA")
            _draw_gizmos(line_shader, solid_shader, node, context.region)
            gpu.state.blend_set("NONE")

    gpu.state.blend_set("NONE")
    gpu.state.line_width_set(1.0)


def register():
    global _handle, _display_shader, _adjust_shader, _display_shader_name
    _display_shader = None
    _adjust_shader = None
    _display_shader_name = ""
    if _handle is None:
        _handle = bpy.types.SpaceNodeEditor.draw_handler_add(
            draw_callback,
            (),
            "WINDOW",
            "POST_VIEW",
        )


def unregister():
    global _handle, _display_shader, _adjust_shader, _display_shader_name
    if _handle is not None:
        bpy.types.SpaceNodeEditor.draw_handler_remove(_handle, "WINDOW")
        _handle = None
    _display_shader = None
    _adjust_shader = None
    _display_shader_name = ""
