"""Move, scale, and rotate images on the board (supports multi-select)."""

from __future__ import annotations

import math

import bpy

from . import drawing
from . import geometry
from .constants import NODE_ID, TREE_ID


def _view_mouse(context, event):
    return context.region.view2d.region_to_view(event.mouse_region_x, event.mouse_region_y)


def _selected_image_nodes(tree):
    return [
        n
        for n in tree.nodes
        if n.bl_idname == NODE_ID and n.image and n.select
    ]


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


def _rotate_around(px, py, cx, cy, degrees):
    angle = math.radians(degrees)
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    dx = px - cx
    dy = py - cy
    return cx + dx * cos_a - dy * sin_a, cy + dx * sin_a + dy * cos_a


class MOODBOARD_OT_transform(bpy.types.Operator):
    bl_idname = "moodboard.transform"
    bl_label = "Transform Reference"
    bl_description = "Move, scale, or rotate reference images (Shift+click to multi-select)"
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
                drawing.tag_redraw_all_node_editors(context.area)
            return {"PASS_THROUGH"}

        extend = event.shift
        already_selected = bool(node.select)
        multi_before = len(_selected_image_nodes(tree)) > 1

        if extend:
            # Shift+click toggles membership.
            node.select = not already_selected
            tree.nodes.active = node
            # Don't start a transform on pure selection tweaks if we just deselected.
            if not node.select:
                drawing.tag_redraw_all_node_editors(context.area)
                return {"FINISHED"}
        elif already_selected and multi_before:
            # Clicking a member of an existing multi-selection keeps the group.
            tree.nodes.active = node
        else:
            _select_node(tree, node, extend=False)

        targets = _selected_image_nodes(tree)
        if not targets:
            return {"CANCELLED"}

        # Ensure the hit node is included and active.
        if node not in targets:
            node.select = True
            targets = _selected_image_nodes(tree)
        tree.nodes.active = node

        snapshots = []
        cx_sum = 0.0
        cy_sum = 0.0
        for item in targets:
            cx, cy, _w, _h = geometry.node_image_rect(item)
            snapshots.append(
                {
                    "name": item.name,
                    "location": item.location[:],
                    "scale": float(item.scale),
                    "rotation": float(item.rotation),
                    "center": (cx, cy),
                }
            )
            cx_sum += cx
            cy_sum += cy

        count = len(snapshots)
        pivot = (cx_sum / count, cy_sum / count)

        self._tree_name = tree.name
        self._primary_name = node.name
        self._mode = mode
        self._start_mouse = (vx, vy)
        self._pivot = pivot
        self._snapshots = snapshots
        self._moved = False

        # Scale/rotate factor measured from group pivot (or single center).
        self._start_dist = max(math.hypot(vx - pivot[0], vy - pivot[1]), 1e-5)
        self._start_angle = math.atan2(vy - pivot[1], vx - pivot[0])

        if mode == "rotate":
            context.window.cursor_set("SCROLL_Y")
        else:
            context.window.cursor_set("SCROLL_XY")

        context.window_manager.modal_handler_add(self)
        drawing.tag_redraw_all_node_editors(context.area)
        return {"RUNNING_MODAL"}

    def _get_tree(self, context):
        tree = context.space_data.edit_tree
        if not tree or tree.name != self._tree_name:
            return None
        return tree

    def _restore(self, tree):
        for snap in self._snapshots:
            node = tree.nodes.get(snap["name"])
            if node is None:
                continue
            node.location = snap["location"]
            node.scale = snap["scale"]
            node.rotation = snap["rotation"]
            drawing.sync_node_size(node)

    def modal(self, context, event):
        tree = self._get_tree(context)
        if tree is None:
            context.window.cursor_set("DEFAULT")
            return {"CANCELLED"}

        if event.type in {"ESC", "RIGHTMOUSE"}:
            self._restore(tree)
            context.window.cursor_set("DEFAULT")
            drawing.tag_redraw_all_node_editors(context.area)
            return {"CANCELLED"}

        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            context.window.cursor_set("DEFAULT")
            drawing.tag_redraw_all_node_editors(context.area)
            return {"FINISHED"}

        if event.type == "MOUSEMOVE":
            vx, vy = _view_mouse(context, event)
            dx = vx - self._start_mouse[0]
            dy = vy - self._start_mouse[1]
            if abs(dx) + abs(dy) > 1e-4:
                self._moved = True

            mode = self._mode
            pivot = self._pivot

            if mode == "move":
                for snap in self._snapshots:
                    node = tree.nodes.get(snap["name"])
                    if node is None:
                        continue
                    geometry.set_node_location_from_center(
                        node,
                        snap["center"][0] + dx,
                        snap["center"][1] + dy,
                    )
            elif mode == "rotate":
                angle = math.atan2(vy - pivot[1], vx - pivot[0])
                delta_deg = math.degrees(angle - self._start_angle)
                for snap in self._snapshots:
                    node = tree.nodes.get(snap["name"])
                    if node is None:
                        continue
                    node.rotation = snap["rotation"] + delta_deg
                    nx, ny = _rotate_around(
                        snap["center"][0],
                        snap["center"][1],
                        pivot[0],
                        pivot[1],
                        delta_deg,
                    )
                    geometry.set_node_location_from_center(node, nx, ny)
            elif mode.startswith("scale"):
                dist = max(math.hypot(vx - pivot[0], vy - pivot[1]), 1e-5)
                factor = dist / self._start_dist
                for snap in self._snapshots:
                    node = tree.nodes.get(snap["name"])
                    if node is None:
                        continue
                    node.scale = max(0.05, snap["scale"] * factor)
                    nx = pivot[0] + (snap["center"][0] - pivot[0]) * factor
                    ny = pivot[1] + (snap["center"][1] - pivot[1]) * factor
                    geometry.set_node_location_from_center(node, nx, ny)
                    drawing.sync_node_size(node)

            drawing.tag_redraw_all_node_editors(context.area)
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
