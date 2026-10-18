bl_info = {
    "name": "MoodBoard",
    "author": "ADDICT",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "Editor Type > MoodBoard",
    "description": "Image reference collage board inside Blender",
    "category": "User Interface",
}

import bpy

from . import drawing, nodes, operators, transform_ops, tree, ui

_addon_keymaps = []


def _register_keymaps():
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if not kc:
        return

    km = kc.keymaps.new(name="Node Editor", space_type="NODE_EDITOR")

    kmi = km.keymap_items.new(
        "moodboard.transform",
        type="LEFTMOUSE",
        value="PRESS",
    )
    _addon_keymaps.append((km, kmi))

    kmi = km.keymap_items.new(
        "moodboard.paste_clipboard",
        type="V",
        value="PRESS",
        ctrl=True,
    )
    _addon_keymaps.append((km, kmi))


def _unregister_keymaps():
    for km, kmi in _addon_keymaps:
        km.keymap_items.remove(kmi)
    _addon_keymaps.clear()


def register():
    tree.register()
    nodes.register()
    operators.register()
    transform_ops.register()
    ui.register()
    drawing.register()
    _register_keymaps()


def unregister():
    _unregister_keymaps()
    drawing.unregister()
    ui.unregister()
    transform_ops.unregister()
    operators.unregister()
    nodes.unregister()
    tree.unregister()


if __name__ == "__main__":
    register()
