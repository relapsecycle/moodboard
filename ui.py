import bpy

from .constants import NODE_ID, TREE_ID

_ADDON_VERSION = "1.0.13"


def _is_moodboard(context):
    space = context.space_data
    return bool(space and space.type == "NODE_EDITOR" and space.tree_type == TREE_ID)


def _selected_count(tree):
    if not tree:
        return 0
    return sum(1 for n in tree.nodes if n.bl_idname == NODE_ID and n.select and n.image)


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
        tree = context.space_data.edit_tree

        header = layout.row(align=True)
        header.label(text=f"v{_ADDON_VERSION}")
        if tree:
            header.label(text=tree.name, icon="NODETREE")
        else:
            header.label(text="No board", icon="ERROR")

        layout.operator("moodboard.new_board", icon="ADD")

        layout.separator()
        col = layout.column(align=True)
        col.enabled = tree is not None
        col.label(text="Add")
        col.operator("moodboard.add_image", icon="IMAGE_DATA", text="Image")
        col.operator("moodboard.import_folder", icon="FILEBROWSER", text="Folder")
        col.operator("moodboard.import_pur", icon="IMPORT", text="PureRef (.pur)")
        col.operator("moodboard.paste_clipboard", icon="PASTEDOWN", text="Paste")

        layout.separator()
        tip = layout.column(align=True)
        tip.scale_y = 0.9
        tip.label(text="Drop files or browser images")
        tip.label(text="Shift+click · multi-select")
        tip.label(text="MMB pan · scroll zoom")


class MOODBOARD_PT_selected(bpy.types.Panel):
    bl_label = "Selection"
    bl_idname = "MOODBOARD_PT_selected"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "MoodBoard"
    bl_parent_id = "MOODBOARD_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        if not _is_moodboard(context):
            return False
        tree = context.space_data.edit_tree
        if not tree:
            return False
        active = tree.nodes.active
        return bool(active and active.bl_idname == NODE_ID and active.image)

    def draw_header(self, context):
        tree = context.space_data.edit_tree
        count = _selected_count(tree)
        if count > 1:
            self.layout.label(text=str(count))

    def draw(self, context):
        layout = self.layout
        tree = context.space_data.edit_tree
        node = tree.nodes.active
        count = _selected_count(tree)

        if count > 1:
            layout.label(text=f"{count} images selected")
            layout.label(text="Transforms use the group box")

        layout.template_ID(node, "image", open="image.open", unlink="image.unlink")

        col = layout.column(align=True)
        col.prop(node, "scale", slider=True)
        col.prop(node, "rotation", slider=True)

        col = layout.column(align=True)
        col.prop(node, "brightness", slider=True)
        col.prop(node, "contrast", slider=True)


class MOODBOARD_PT_help(bpy.types.Panel):
    bl_label = "Shortcuts"
    bl_idname = "MOODBOARD_PT_help"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "MoodBoard"
    bl_parent_id = "MOODBOARD_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return _is_moodboard(context)

    def draw(self, context):
        col = self.layout.column(align=True)
        col.scale_y = 0.9
        col.label(text="Drag · move")
        col.label(text="Corners · scale")
        col.label(text="Blue knob · rotate")
        col.label(text="Shift+click · add / remove")
        col.label(text="Ctrl+V · paste clipboard")
        col.label(text="PureRef import · legacy 1.x")


classes = (
    MOODBOARD_PT_main,
    MOODBOARD_PT_selected,
    MOODBOARD_PT_help,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
