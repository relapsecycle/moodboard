"""Move, scale, and rotate images on the board."""

from __future__ import annotations

import math

import bpy

from . import drawing
from . import geometry
from .constants import TREE_ID


def _view_mouse(context, event):
    return context.region.view2d.region_to_view(event.mouse_region_x, event.mouse_region_y)


def _select_node(tree, node, extend=False):
    if node is None:
        if not extend:
            for n in tree.nodes:
                n.select = False
            tree.nodes.active = None
        return

    if not extend:
        for n in tree.nodes:
            n.select = n == node
    else:
        node.select = True
    tree.nodes.active = node


class MOODBOARD_OT_transform(bpy.types.Operator):
    bl_idname = "moodboard.transform"
    bl_label = "Transform Reference"
    bl_description = "Move, scale, or rotate reference images with on-canvas handles"
    bl_options = {"REGISTER", "UNDO", "GRAB_CURSOR", "BLOCKING"}

    def poll_context(self, context):
        space = context.space_data
        return bool(
            space
            and space.type == "NODE_EDITOR"
            and space.tree_type == TREE_ID
            and space.edit_tree
            and context.region
            and context.region.type == "WINDOW"
        )

    @classmethod
    def poll(cls, context):
        space = context.space_data
        return bool(
            space
            and space.type == "NODE_EDITOR"
            and space.tree_type == TREE_ID
            and space.edit_tree
        )

    def invoke(self, context, event):
        if not self.poll_context(context):
            return {"PASS_THROUGH"}

        # Let pan / box-select modifiers through.
        if event.alt or event.type != "LEFTMOUSE":
            return {"PASS_THROUGH"}

        tree = context.space_data.edit_tree
        vx, vy = _view_mouse(context, event)
        node, mode = geometry.hit_test_board(tree, vx, vy, context.region)

        if node is None:
            if not event.shift:
                _select_node(tree, None)
                drawing.tag_redraw_all_node_editors()
            return {"PASS_THROUGH"}

        extend = event.shift
        _select_node(tree, node, extend=extend)

        self._tree_name = tree.name
        self._node_name = node.name
        self._mode = mode
        self._start_mouse = (vx, vy)
        self._start_scale = node.scale
        self._start_rotation = node.rotation
        cx, cy, _w, _h = geometry.node_image_rect(node)
        self._start_center = (cx, cy)
        self._start_location = node.location[:]
        self._start_dist = max(math.hypot(vx - cx, vy - cy), 1e-5)
        self._start_angle = math.atan2(vy - cy, vx - cx)
        self._moved = False

        if mode == "rotate":
            context.window.cursor_set("SCROLL_Y")
        elif mode.startswith("scale"):
            context.window.cursor_set("SCROLL_XY")
        else:
            context.window.cursor_set("SCROLL_XY")

        context.window_manager.modal_handler_add(self)
        drawing.tag_redraw_all_node_editors()
        return {"RUNNING_MODAL"}

    def _get_node(self, context):
        tree = context.space_data.edit_tree
        if not tree or tree.name != self._tree_name:
            return None, None
        node = tree.nodes.get(self._node_name)
        return tree, node

    def modal(self, context, event):
        tree, node = self._get_node(context)
        if tree is None or node is None:
            context.window.cursor_set("DEFAULT")
            return {"CANCELLED"}

        if event.type in {"ESC", "RIGHTMOUSE"}:
            node.location = self._start_location
            node.scale = self._start_scale
            node.rotation = self._start_rotation
            drawing.sync_node_size(node)
            context.window.cursor_set("DEFAULT")
            drawing.tag_redraw_all_node_editors()
            return {"CANCELLED"}

        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            context.window.cursor_set("DEFAULT")
            drawing.tag_redraw_all_node_editors()
            return {"FINISHED"}

        if event.type == "MOUSEMOVE":
            vx, vy = _view_mouse(context, event)
            dx = vx - self._start_mouse[0]
            dy = vy - self._start_mouse[1]
            if abs(dx) + abs(dy) > 1e-4:
                self._moved = True

            mode = self._mode
            if mode == "move":
                geometry.set_node_location_from_center(
                    node,
                    self._start_center[0] + dx,
                    self._start_center[1] + dy,
                )
            elif mode == "rotate":
                angle = math.atan2(vy - self._start_center[1], vx - self._start_center[0])
                delta_deg = math.degrees(angle - self._start_angle)
                node.rotation = self._start_rotation + delta_deg
                # Keep center fixed while rotating.
                geometry.set_node_location_from_center(node, *self._start_center)
            elif mode.startswith("scale"):
                dist = max(
                    math.hypot(vx - self._start_center[0], vy - self._start_center[1]),
                    1e-5,
                )
                node.scale = max(0.05, self._start_scale * (dist / self._start_dist))
                geometry.set_node_location_from_center(node, *self._start_center)
                drawing.sync_node_size(node)

            drawing.tag_redraw_all_node_editors()
            return {"RUNNING_MODAL"}

        # Allow view navigation while transforming.
        if event.type in {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE"}:
            return {"PASS_THROUGH"}

        return {"RUNNING_MODAL"}


classes = (MOODBOARD_OT_transform,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
