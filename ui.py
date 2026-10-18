import bpy

from . import drawing
from .constants import NODE_ID, TREE_ID

_ADDON_VERSION = "1.0.7"


def _is_moodboard(context):
    space = context.space_data
    return bool(space and space.type == "NODE_EDITOR" and space.tree_type == TREE_ID)


class MOODBOARD_PT_main(bpy.types.Panel):
    bl_label = "MoodBoard"
    bl_idname = "MOODBOARD_PT_main"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "MoodBoard"

    @classmethod
    def poll(cls, context):
        return _is_moodboard(context)

    def draw(self, context):
        layout = self.layout
        space = context.space_data
        tree = space.edit_tree

        col = layout.column(align=True)
        col.operator("moodboard.new_board", icon="ADD")

        layout.label(text=f"MoodBoard {_ADDON_VERSION}")
        layout.label(text=f"Draw: {drawing.display_shader_name()}")

        if tree:
            layout.label(text=f"Board: {tree.name}")
        else:
            layout.label(text="No board assigned", icon="ERROR")
            layout.label(text="Click New Board to start")

        layout.separator()

        col = layout.column(align=True)
        col.enabled = tree is not None
        col.operator("moodboard.import_folder", icon="FILEBROWSER")
        col.operator("moodboard.add_image", icon="IMAGE_DATA")
        col.operator("moodboard.paste_clipboard", icon="PASTEDOWN")

        layout.separator()
        box = layout.box()
        box.label(text="Controls", icon="MOUSE_LMB")
        col = box.column(align=True)
        col.label(text="Drop files or browser images")
        col.label(text="Or Copy image, then Ctrl+V")
        col.label(text="Drag image to move")
        col.label(text="Corner squares to scale")
        col.label(text="Blue knob to rotate")
        col.label(text="MMB pan, scroll zoom")


class MOODBOARD_PT_selected(bpy.types.Panel):
    bl_label = "Selected Image"
    bl_idname = "MOODBOARD_PT_selected"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "MoodBoard"
    bl_parent_id = "MOODBOARD_PT_main"

    @classmethod
    def poll(cls, context):
        if not _is_moodboard(context):
            return False
        tree = context.space_data.edit_tree
        if not tree:
            return False
        active = tree.nodes.active
        return bool(active and active.bl_idname == NODE_ID)

    def draw(self, context):
        layout = self.layout
        node = context.space_data.edit_tree.nodes.active

        layout.template_ID(node, "image", open="image.open", unlink="image.unlink")
        col = layout.column(align=True)
        col.prop(node, "scale", slider=True)
        col.prop(node, "rotation", slider=True)
        col.separator()
        col.label(text="Display (not node Color)")
        col.prop(node, "brightness", slider=True)
        col.prop(node, "contrast", slider=True)


classes = (
    MOODBOARD_PT_main,
    MOODBOARD_PT_selected,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
