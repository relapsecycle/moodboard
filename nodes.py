import bpy
from bpy.types import Node

from . import drawing
from .constants import NODE_ID, TREE_ID


def _update_node_size(self, context):
    drawing.sync_node_size(self)
    drawing.tag_redraw_all_node_editors()


class MoodBoardTreeNode:
    @classmethod
    def poll(cls, ntree):
        return ntree.bl_idname == TREE_ID


class MoodBoardImageNode(MoodBoardTreeNode, Node):
    bl_idname = NODE_ID
    bl_label = " "
    bl_icon = "NONE"
    bl_width_default = 20
    bl_width_min = 20

    image: bpy.props.PointerProperty(
        name="Image",
        type=bpy.types.Image,
        update=_update_node_size,
    )
    scale: bpy.props.FloatProperty(
        name="Scale",
        description="Image display scale",
        default=1.0,
        min=0.05,
        soft_max=8.0,
        update=_update_node_size,
    )
    rotation: bpy.props.FloatProperty(
        name="Rotation",
        description="Image rotation in degrees",
        default=0.0,
        soft_min=-180.0,
        soft_max=180.0,
        update=_update_node_size,
    )

    def init(self, context):
        self.width = 20
        self.use_custom_color = True
        self.color = (0.15, 0.15, 0.15)

    def copy(self, node):
        self.image = node.image
        self.scale = node.scale
        self.rotation = node.rotation

    def draw_buttons(self, context, layout):
        pass

    def draw_buttons_ext(self, context, layout):
        layout.template_ID(self, "image", open="image.open", unlink="image.unlink")
        layout.prop(self, "scale", slider=True)
        layout.prop(self, "rotation", slider=True)

    def draw_label(self):
        return " "


def draw_add_menu(self, context):
    if context.space_data.tree_type != TREE_ID:
        return
    self.node_operator(self.layout, NODE_ID)


classes = (MoodBoardImageNode,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.NODE_MT_add.append(draw_add_menu)
    if hasattr(bpy.types, "NODE_MT_swap"):
        bpy.types.NODE_MT_swap.append(draw_add_menu)


def unregister():
    if hasattr(bpy.types, "NODE_MT_swap"):
        bpy.types.NODE_MT_swap.remove(draw_add_menu)
    bpy.types.NODE_MT_add.remove(draw_add_menu)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
