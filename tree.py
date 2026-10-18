import bpy
from bpy.types import NodeTree

from .constants import TREE_ID


class MoodBoardTree(NodeTree):
    bl_idname = TREE_ID
    bl_label = "MoodBoard"
    bl_icon = "IMAGE_DATA"


classes = (MoodBoardTree,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
