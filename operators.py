import os
import sys
import tempfile
from pathlib import Path

import bpy

from . import drawing, geometry
from .constants import BASE_DISPLAY_SIZE, IMAGE_EXTENSIONS, NODE_ID, TREE_ID
from .pureref_legacy import PurRefError, PurRefModernError, read_legacy_pur


def _active_tree(context):
    space = context.space_data
    if not space or space.type != "NODE_EDITOR":
        return None
    if space.tree_type != TREE_ID:
        return None
    return space.edit_tree


def _ensure_tree(context):
    space = context.space_data
    tree = _active_tree(context)
    if tree:
        return tree

    tree = bpy.data.node_groups.new("MoodBoard", TREE_ID)
    space.node_tree = tree
    return tree


def _view_center(context):
    region = context.region
    space = context.space_data
    if region is None or space is None:
        return 0.0, 0.0

    # Convert region center to view2d coordinates (node space).
    center = (region.width * 0.5, region.height * 0.5)
    try:
        return region.view2d.region_to_view(*center)
    except Exception:
        return 0.0, 0.0


def _add_image_node(tree, image, location, scale=1.0, rotation=0.0):
    drawing.ensure_display_colorspace(image)
    node = tree.nodes.new(NODE_ID)
    node.image = image
    node.scale = scale
    node.rotation = rotation
    node.location = location
    drawing.sync_node_size(node)
    return node


def _image_from_png_bytes(png: bytes, name: str):
    temp_dir = bpy.app.tempdir or tempfile.gettempdir()
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in (name or "pur"))[:48]
    path = os.path.join(temp_dir, f"moodboard_pur_{safe}_{os.getpid()}.png")
    with open(path, "wb") as handle:
        handle.write(png)
    try:
        image = bpy.data.images.load(path, check_existing=False)
        image.name = name or image.name
        try:
            image.pack()
        except Exception:
            pass
        drawing.ensure_display_colorspace(image)
        return image
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _place_pur_node(tree, image, placement):
    iw, ih = image.size
    longest = max(iw, ih, 1)
    # Match PureRef on-canvas height in board units.
    scale = (placement.height * longest) / (max(ih, 1) * BASE_DISPLAY_SIZE)
    scale = max(float(scale), 0.05)
    node = _add_image_node(tree, image, (0.0, 0.0), scale=scale, rotation=placement.rotation_deg)
    # PureRef Y grows downward; MoodBoard view Y grows upward.
    geometry.set_node_location_from_center(node, placement.x, -placement.y)
    return node


def _layout_grid_positions(count, start_x, start_y, cell_w=280.0, cell_h=280.0, columns=None):
    if count <= 0:
        return []
    if columns is None:
        columns = max(1, int(count**0.5 + 0.999))
    positions = []
    for index in range(count):
        col = index % columns
        row = index // columns
        positions.append((start_x + col * cell_w, start_y - row * cell_h))
    return positions


class MOODBOARD_OT_new_board(bpy.types.Operator):
    bl_idname = "moodboard.new_board"
    bl_label = "New Board"
    bl_description = "Create a new MoodBoard in this editor"
    bl_options = {"REGISTER", "UNDO"}

    name: bpy.props.StringProperty(name="Name", default="MoodBoard")

    @classmethod
    def poll(cls, context):
        space = context.space_data
        return bool(space and space.type == "NODE_EDITOR" and space.tree_type == TREE_ID)

    def execute(self, context):
        space = context.space_data
        tree = bpy.data.node_groups.new(self.name, TREE_ID)
        space.node_tree = tree
        drawing.tag_redraw_all_node_editors()
        self.report({"INFO"}, f"Created board '{tree.name}'")
        return {"FINISHED"}


class MOODBOARD_OT_import_folder(bpy.types.Operator):
    bl_idname = "moodboard.import_folder"
    bl_label = "Import Folder"
    bl_description = "Import all images from a folder onto the board"
    bl_options = {"REGISTER", "UNDO"}

    directory: bpy.props.StringProperty(subtype="DIR_PATH")
    filter_folder: bpy.props.BoolProperty(default=True, options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        space = context.space_data
        return bool(space and space.type == "NODE_EDITOR" and space.tree_type == TREE_ID)

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        tree = _ensure_tree(context)
        folder = Path(bpy.path.abspath(self.directory))
        if not folder.is_dir():
            self.report({"ERROR"}, "Folder not found")
            return {"CANCELLED"}

        files = sorted(
            path
            for path in folder.iterdir()
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        )
        if not files:
            self.report({"WARNING"}, "No supported images in that folder")
            return {"CANCELLED"}

        origin_x, origin_y = _view_center(context)
        # Offset so the grid grows down-right from the view center.
        start_x = origin_x - 140.0
        start_y = origin_y + 140.0
        positions = _layout_grid_positions(len(files), start_x, start_y)

        created = 0
        for path, location in zip(files, positions):
            try:
                image = bpy.data.images.load(str(path), check_existing=True)
            except Exception as exc:
                self.report({"WARNING"}, f"Skipped {path.name}: {exc}")
                continue
            _add_image_node(tree, image, location)
            created += 1

        drawing.tag_redraw_all_node_editors()
        self.report({"INFO"}, f"Imported {created} image(s)")
        return {"FINISHED"}


class MOODBOARD_OT_paste_clipboard(bpy.types.Operator):
    bl_idname = "moodboard.paste_clipboard"
    bl_label = "Paste Clipboard Image"
    bl_description = "Paste an image from the system clipboard onto the board"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        space = context.space_data
        return bool(space and space.type == "NODE_EDITOR" and space.tree_type == TREE_ID)

    def execute(self, context):
        if sys.platform != "win32":
            self.report(
                {"ERROR"},
                "Clipboard paste is currently supported on Windows only",
            )
            return {"CANCELLED"}

        from .clipboard_win import (
            ClipboardError,
            get_clipboard_html,
            get_clipboard_text,
            save_clipboard_image_to_tempfile,
        )
        from . import web_fetch

        tree = _ensure_tree(context)
        temp_paths = []
        image = None
        try:
            try:
                temp_path = save_clipboard_image_to_tempfile()
                temp_paths.append(temp_path)
                image = bpy.data.images.load(temp_path, check_existing=False)
                image.pack()
                image.name = "Clipboard"
                image.filepath = ""
            except ClipboardError:
                # Fall back to URL / HTML copied from a browser (Google, etc.).
                html = get_clipboard_html()
                text = get_clipboard_text()
                urls = []
                if html:
                    urls.extend(web_fetch.extract_image_urls_from_html(html))
                if text:
                    urls.extend(web_fetch.extract_urls_from_text(text))
                if not urls:
                    raise ClipboardError(
                        "No image or image link on the clipboard. "
                        "Try Copy image / Copy image address, then paste."
                    )
                temp_path = web_fetch.download_image_url(urls[0])
                temp_paths.append(temp_path)
                image = bpy.data.images.load(temp_path, check_existing=False)
                image.pack()
                image.name = "Web Image"
                image.filepath = ""
        except ClipboardError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:
            self.report({"ERROR"}, f"Failed to paste image: {exc}")
            return {"CANCELLED"}
        finally:
            for temp_path in temp_paths:
                if temp_path and os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass

        location = _view_center(context)
        node = _add_image_node(tree, image, location)
        tree.nodes.active = node
        for other in tree.nodes:
            other.select = other == node

        drawing.tag_redraw_all_node_editors()
        self.report({"INFO"}, "Pasted clipboard image")
        return {"FINISHED"}


class MOODBOARD_OT_import_pur(bpy.types.Operator):
    bl_idname = "moodboard.import_pur"
    bl_label = "Import PureRef (.pur)"
    bl_description = "Import a legacy PureRef 1.x scene onto the board (2.0+ not yet supported)"
    bl_options = {"REGISTER", "UNDO"}

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    directory: bpy.props.StringProperty(subtype="DIR_PATH", options={"HIDDEN", "SKIP_SAVE"})
    files: bpy.props.CollectionProperty(
        type=bpy.types.OperatorFileListElement,
        options={"HIDDEN", "SKIP_SAVE"},
    )
    filter_glob: bpy.props.StringProperty(default="*.pur", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        space = context.space_data
        return bool(space and space.type == "NODE_EDITOR" and space.tree_type == TREE_ID)

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def _resolve_paths(self):
        paths = []
        if self.files and self.directory:
            for entry in self.files:
                name = getattr(entry, "name", "")
                if not name:
                    continue
                paths.append(Path(bpy.path.abspath(self.directory)) / name)
        if self.filepath:
            paths.append(Path(bpy.path.abspath(self.filepath)))
        unique = []
        seen = set()
        for path in paths:
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            if path.suffix.lower() == ".pur" and path.is_file():
                unique.append(path)
        return unique

    def execute(self, context):
        tree = _ensure_tree(context)
        paths = self._resolve_paths()
        if not paths:
            self.report({"ERROR"}, "File not found")
            return {"CANCELLED"}

        created = 0
        last_version = ""
        for path in paths:
            try:
                scene = read_legacy_pur(str(path))
            except PurRefModernError as exc:
                self.report({"ERROR"}, str(exc))
                continue
            except PurRefError as exc:
                self.report({"ERROR"}, str(exc))
                continue
            except Exception as exc:
                self.report({"ERROR"}, f"Failed to read {path.name}: {exc}")
                continue

            last_version = scene.version or "1.x"
            stem = path.stem
            for index, placement in enumerate(scene.placements):
                name = placement.name or f"{stem}_{index:03d}"
                try:
                    image = _image_from_png_bytes(placement.png, name)
                except Exception as exc:
                    self.report({"WARNING"}, f"Skipped image: {exc}")
                    continue
                _place_pur_node(tree, image, placement)
                created += 1

        drawing.tag_redraw_all_node_editors()
        if created == 0:
            self.report({"WARNING"}, "No images imported from that .pur")
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            f"Imported {created} image(s) from legacy PureRef ({last_version})",
        )
        return {"FINISHED"}


class MOODBOARD_OT_add_image(bpy.types.Operator):
    bl_idname = "moodboard.add_image"
    bl_label = "Add Image"
    bl_description = "Add a reference image node to the board"
    bl_options = {"REGISTER", "UNDO"}

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    filter_glob: bpy.props.StringProperty(
        default="*.png;*.jpg;*.jpeg;*.webp;*.bmp;*.tga;*.tif;*.tiff;*.exr",
        options={"HIDDEN"},
    )

    @classmethod
    def poll(cls, context):
        space = context.space_data
        return bool(space and space.type == "NODE_EDITOR" and space.tree_type == TREE_ID)

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        tree = _ensure_tree(context)
        path = bpy.path.abspath(self.filepath)
        if not os.path.isfile(path):
            self.report({"ERROR"}, "File not found")
            return {"CANCELLED"}

        try:
            image = bpy.data.images.load(path, check_existing=True)
        except Exception as exc:
            self.report({"ERROR"}, f"Could not load image: {exc}")
            return {"CANCELLED"}

        node = _add_image_node(tree, image, _view_center(context))
        tree.nodes.active = node
        for other in tree.nodes:
            other.select = other == node
        drawing.tag_redraw_all_node_editors()
        return {"FINISHED"}


def _drop_candidate_strings(directory, files, filepath):
    """Collect raw drop strings (local paths, file://, http urls, .url/.htm)."""
    from . import web_fetch

    candidates = []
    raw_filepath = (filepath or "").strip().strip('"')
    raw_directory = (directory or "").strip().strip('"')

    if raw_filepath:
        candidates.append(raw_filepath)

    if files:
        for item in files:
            name = (item.name or "").strip().strip('"')
            if not name:
                continue
            if web_fetch.is_http_url(name) or name.lower().startswith("file:"):
                candidates.append(name)
            elif web_fetch.is_http_url(raw_directory):
                candidates.append(raw_directory.rstrip("/") + "/" + name.lstrip("/"))
            elif raw_directory:
                candidates.append(str(Path(bpy.path.abspath(raw_directory)) / name))
            else:
                candidates.append(name)
    elif raw_directory:
        candidates.append(raw_directory)

    # Normalize file:// and de-dupe
    cleaned = []
    seen = set()
    for value in candidates:
        value = web_fetch.strip_file_url(value.strip().strip('"'))
        if not value or value in seen:
            continue
        seen.add(value)
        cleaned.append(value)
    return cleaned


def _schedule_pack_and_cleanup(items):
    """Pack images and delete temp files shortly after the drop."""

    def _run():
        from . import drawing

        for image_name, temp_path in items:
            image = bpy.data.images.get(image_name)
            if image is not None:
                try:
                    image.pack()
                    image.filepath = ""
                except Exception:
                    pass
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
        drawing.tag_redraw_all_node_editors()
        return None

    try:
        bpy.app.timers.register(_run, first_interval=0.05)
    except Exception:
        _run()


def _resolve_drop_to_temp_files(directory, files, filepath, quick=False):
    """
    Turn a browser/Explorer drop into local image files we own.
    Browser temp files (e.g. %TEMP%\\images.jpg) are copied immediately with retries.
    Returns (local_paths, cleanup_paths).
    """
    from . import web_fetch

    local_paths = []
    cleanup_paths = []
    seen = set()

    def _add_owned(path_str: str):
        key = path_str
        if key in seen:
            return
        seen.add(key)
        local_paths.append(Path(path_str))
        cleanup_paths.append(path_str)

    def _add_url(url: str):
        temp = web_fetch.download_image_url(url)
        _add_owned(temp)

    for raw in _drop_candidate_strings(directory, files, filepath):
        if web_fetch.is_http_url(raw):
            _add_url(raw)
            continue

        path = Path(raw)
        if not path.is_absolute():
            path = Path(bpy.path.abspath(raw))

        if path.is_dir():
            for child in sorted(path.iterdir()):
                owned, cleanup = web_fetch.snapshot_local_path(child)
                cleanup_paths.extend(cleanup)
                if owned:
                    _add_owned(owned)
            continue

        suffix = path.suffix.lower()

        owned, cleanup = web_fetch.snapshot_local_path(path)
        cleanup_paths.extend(cleanup)
        if owned:
            _add_owned(owned)
            continue

        # Fallbacks if the temp file vanished before we could read it.
        if suffix == ".url" and path.exists():
            url = web_fetch.parse_internet_shortcut(path)
            if url:
                _add_url(url)
                continue

        urls = web_fetch.extract_urls_from_text(raw)
        if urls:
            for url in urls:
                _add_url(url)

    if not local_paths and not quick:
        reported = None
        for raw in _drop_candidate_strings(directory, files, filepath):
            if raw and not web_fetch.is_http_url(raw):
                reported = raw
                break
        owned, cleanup = web_fetch.salvage_browser_drop(reported)
        cleanup_paths.extend(cleanup)
        if owned:
            _add_owned(owned)

    # Unique local_paths (cleanup may have duplicates from helpers)
    unique_paths = []
    seen_paths = set()
    for path in local_paths:
        key = str(path)
        if key in seen_paths:
            continue
        seen_paths.add(key)
        unique_paths.append(path)

    unique_cleanup = []
    seen_cleanup = set()
    for item in cleanup_paths:
        if item in seen_cleanup:
            continue
        seen_cleanup.add(item)
        unique_cleanup.append(item)

    return unique_paths, unique_cleanup


class MOODBOARD_OT_drop_images(bpy.types.Operator):
    """Handle drag-and-drop of images onto MoodBoard."""

    bl_idname = "moodboard.drop_images"
    bl_label = "Drop Images"
    bl_description = "Add dropped images (files or browser links) onto the collage board"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    directory: bpy.props.StringProperty(subtype="DIR_PATH", options={"HIDDEN", "SKIP_SAVE"})
    filepath: bpy.props.StringProperty(subtype="FILE_PATH", options={"HIDDEN", "SKIP_SAVE"})
    files: bpy.props.CollectionProperty(
        type=bpy.types.OperatorFileListElement,
        options={"HIDDEN", "SKIP_SAVE"},
    )

    def invoke(self, context, event):
        self._drop_region_x = event.mouse_region_x
        self._drop_region_y = event.mouse_region_y
        return self.execute(context)

    def execute(self, context):
        space = context.space_data
        if not space or space.type != "NODE_EDITOR" or space.tree_type != TREE_ID:
            return {"CANCELLED"}

        cleanup_paths = []
        paths = []
        try:
            if _hover_drop_cache.get("paths"):
                paths = [Path(p) for p in list(_hover_drop_cache["paths"])]
                cleanup_paths = list(_hover_drop_cache.get("cleanup", []))
                _hover_drop_cache["paths"] = []
                _hover_drop_cache["cleanup"] = []
            else:
                paths, cleanup_paths = _resolve_drop_to_temp_files(
                    self.directory,
                    self.files,
                    self.filepath,
                    quick=True,
                )
                if not paths:
                    from . import web_fetch

                    preview = _drop_candidate_strings(
                        self.directory, self.files, self.filepath
                    )
                    reported = preview[0] if preview else None
                    owned, cleanup = web_fetch.salvage_browser_drop(reported)
                    cleanup_paths.extend(cleanup)
                    if owned:
                        paths = [Path(owned)]
        except Exception as exc:
            self.report({"ERROR"}, f"Could not import drop: {exc}")
            return {"CANCELLED"}

        if not paths:
            preview = _drop_candidate_strings(self.directory, self.files, self.filepath)
            hint = preview[0][:80] if preview else "empty drop payload"
            self.report(
                {"WARNING"},
                "Browser deleted the temp image before Blender could read it ("
                + hint
                + "). Try Copy image, then Ctrl+V.",
            )
            return {"CANCELLED"}

        tree = _ensure_tree(context)
        region = context.region
        if region is not None and hasattr(self, "_drop_region_x"):
            origin_x, origin_y = region.view2d.region_to_view(
                self._drop_region_x,
                self._drop_region_y,
            )
        else:
            origin_x, origin_y = _view_center(context)

        positions = _layout_grid_positions(
            len(paths),
            origin_x,
            origin_y,
        )

        created = 0
        last_node = None
        cleanup_set = set(cleanup_paths)
        deferred_cleanup = []

        for path, location in zip(paths, positions):
            try:
                check_existing = str(path) not in cleanup_set
                image = bpy.data.images.load(str(path), check_existing=check_existing)
                if str(path) in cleanup_set:
                    image.name = path.stem or "Web Image"
                    deferred_cleanup.append((image.name, str(path)))
                last_node = _add_image_node(tree, image, location)
                created += 1
            except Exception as exc:
                self.report({"WARNING"}, f"Skipped {path.name}: {exc}")
                if str(path) in cleanup_set and os.path.exists(str(path)):
                    try:
                        os.remove(str(path))
                    except OSError:
                        pass

        if deferred_cleanup:
            _schedule_pack_and_cleanup(deferred_cleanup)

        if last_node is not None:
            tree.nodes.active = last_node
            for other in tree.nodes:
                other.select = other == last_node

        drawing.tag_redraw_all_node_editors()
        if created:
            self.report({"INFO"}, f"Added {created} image(s)")
            return {"FINISHED"}
        self.report({"WARNING"}, "Drop contained no loadable images")
        return {"CANCELLED"}


# Filled while the drag hovers over our editor (temp file still alive then).
_hover_drop_cache = {"paths": [], "cleanup": [], "stamp": 0.0}


def _capture_hover_browser_temp():
    """Copy Chrome's Temp image while the drag is still over the editor."""
    import time

    from . import web_fetch

    global _hover_drop_cache
    now = time.time()
    if now - float(_hover_drop_cache.get("stamp", 0.0)) < 0.2:
        return

    temp_root = Path(os.environ.get("TEMP") or __import__("tempfile").gettempdir())
    suspects = [
        temp_root / "images.jpg",
        temp_root / "images.jpeg",
        temp_root / "images.png",
        temp_root / "image.jpg",
        temp_root / "image.png",
    ]

    owned_paths = []
    cleanup = []
    for path in suspects:
        data = web_fetch.read_path_bytes(path)
        if not data:
            continue
        owned, owned_cleanup = web_fetch.bytes_to_owned_temp(data, hint_name="hover")
        cleanup.extend(owned_cleanup)
        if owned:
            owned_paths.append(owned)
            break

    _hover_drop_cache["stamp"] = now
    if not owned_paths:
        return

    for old in _hover_drop_cache.get("cleanup", []):
        if old not in cleanup and old not in owned_paths and os.path.exists(old):
            try:
                os.remove(old)
            except OSError:
                pass
    _hover_drop_cache["paths"] = owned_paths
    _hover_drop_cache["cleanup"] = cleanup


class MOODBOARD_FH_pur(bpy.types.FileHandler):
    bl_idname = "MOODBOARD_FH_pur"
    bl_label = "MoodBoard PureRef Drop"
    bl_import_operator = "moodboard.import_pur"
    bl_file_extensions = ".pur"

    @classmethod
    def poll_drop(cls, context):
        area = getattr(context, "area", None)
        if area is None or area.type != "NODE_EDITOR":
            return False
        space = context.space_data
        return bool(space and getattr(space, "tree_type", None) == TREE_ID)


class MOODBOARD_FH_images(bpy.types.FileHandler):
    bl_idname = "MOODBOARD_FH_images"
    bl_label = "MoodBoard Image Drop"
    bl_import_operator = "moodboard.drop_images"
    bl_file_extensions = (
        ".png;.jpg;.jpeg;.webp;.bmp;.tga;.tif;.tiff;.exr;.gif;.url;.htm;.html"
    )

    @classmethod
    def poll_drop(cls, context):
        area = getattr(context, "area", None)
        if area is None or area.type != "NODE_EDITOR":
            return False
        space = context.space_data
        ok = bool(space and getattr(space, "tree_type", None) == TREE_ID)
        if ok:
            try:
                _capture_hover_browser_temp()
            except Exception:
                pass
        return ok


classes = (
    MOODBOARD_OT_new_board,
    MOODBOARD_OT_import_folder,
    MOODBOARD_OT_import_pur,
    MOODBOARD_OT_paste_clipboard,
    MOODBOARD_OT_add_image,
    MOODBOARD_OT_drop_images,
    MOODBOARD_FH_pur,
    MOODBOARD_FH_images,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
