"""
=============================================================
  AI 3D Model Optimizer — Blender Add-on
=============================================================
  HOW TO INSTALL:
    1. Open Blender
    2. Go to Edit → Preferences → Add-ons
    3. Click "Install from Disk" (Blender 4.2+) or "Install..." (older)
    4. Select this .py file
    5. Enable the add-on by checking the box next to it

  HOW TO USE:
    1. Open the sidebar in the 3D Viewport by pressing N
    2. Click the "AI Optimizer" tab
    3. Adjust settings
    4. Click buttons to run individual steps or the full pipeline
=============================================================
"""

bl_info = {
    "name": "AI 3D Model Optimizer",
    "author": "Claude",
    "version": (1, 3, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > AI Optimizer",
    "description": "Optimize AI-generated 3D models: fix geometry, decimate, clean textures, export compressed GLB",
    "category": "Mesh",
}

import json
import math
import os
import time

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    StringProperty,
)
from bpy.types import (
    Operator,
    Panel,
    PropertyGroup,
)

# =============================================================
#  Helper functions
# =============================================================


def get_image_users(image):
    """Count how many material node trees actually reference this image."""
    count = 0
    for mat in bpy.data.materials:
        if mat.node_tree:
            for node in mat.node_tree.nodes:
                if node.type == "TEX_IMAGE" and node.image == image:
                    count += 1
    return count


def get_selected_meshes():
    """Get all selected mesh objects, or all scene meshes if none selected."""
    meshes = [obj for obj in bpy.context.selected_objects if obj.type == "MESH"]
    if not meshes:
        meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    return meshes


def count_faces(meshes):
    """Count total faces across meshes."""
    return sum(len(obj.data.polygons) for obj in meshes)


def log(context, message, level="INFO"):
    """Log a message to console and status bar."""
    print(f"  [AI Optimizer] {message}")
    if hasattr(context, "window_manager"):
        context.window_manager.progress_update(0)


def get_config_path():
    """Get the path to the saved defaults JSON file."""
    config_dir = bpy.utils.user_resource("CONFIG", path="ai_optimizer")
    os.makedirs(config_dir, exist_ok=True)
    return os.path.join(config_dir, "defaults.json")


def is_print3d_available():
    """Check if the 3D Print Toolbox add-on is installed and enabled."""
    # Check for the operator that our fix_geometry step uses
    return hasattr(bpy.ops.mesh, "print3d_clean_non_manifold")


# -------------------------------------------------------------
#  Extracted helper functions (called by operators & modal pipeline)
# -------------------------------------------------------------


def fix_geometry_single(context, obj, props):
    """Fix geometry on a single mesh object.

    Selects *obj*, enters edit mode, merges doubles, recalculates normals,
    attempts manifold fix, deletes loose geometry, then returns to object mode.

    Returns ``(fixed: bool, method_used: str)`` where *fixed* is True when a
    manifold repair was applied and *method_used* describes which backend was
    used (or ``"none"`` when manifold fixing is disabled).
    """
    bpy.ops.object.select_all(action="DESELECT")
    context.view_layer.objects.active = obj
    obj.select_set(True)

    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")

    # Merge close vertices
    bpy.ops.mesh.remove_doubles(threshold=props.merge_distance)

    # Recalculate normals
    if props.recalculate_normals:
        bpy.ops.mesh.normals_make_consistent(inside=False)

    # Manifold fix
    fixed = False
    method_used = "none"
    if props.fix_manifold:
        try:
            bpy.ops.mesh.print3d_clean_non_manifold()
            fixed = True
            method_used = "3D Print Toolbox"
        except (AttributeError, RuntimeError):
            # Manual fallback
            method_used = "manual fill holes (3D Print Toolbox not available)"
            bpy.ops.mesh.select_all(action="DESELECT")
            bpy.ops.mesh.select_non_manifold(
                extend=False,
                use_wire=True,
                use_boundary=True,
                use_multi_face=True,
                use_non_contiguous=True,
                use_verts=True,
            )
            try:
                bpy.ops.mesh.fill_holes(sides=32)
                fixed = True
            except RuntimeError:
                pass

    # Delete loose
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.delete_loose(use_verts=True, use_edges=True, use_faces=False)

    bpy.ops.object.mode_set(mode="OBJECT")
    return (fixed, method_used)


def decimate_single(context, obj, props):
    """Add and apply a Decimate modifier on *obj*."""
    bpy.ops.object.select_all(action="DESELECT")
    context.view_layer.objects.active = obj
    obj.select_set(True)

    mod = obj.modifiers.new(name="Decimate_Optimize", type="DECIMATE")
    mod.decimate_type = "COLLAPSE"
    mod.ratio = props.decimate_ratio
    mod.use_collapse_triangulate = True
    bpy.ops.object.modifier_apply(modifier=mod.name)


def get_image_fingerprint(img):
    """Create a fingerprint of an image's actual pixel content.

    Compares dimensions + sampled pixel values to detect true duplicates
    without needing to compare every single pixel (which would be slow
    on large textures).
    """
    if not img.has_data:
        return None

    w, h = img.size[0], img.size[1]
    if w == 0 or h == 0:
        return None

    channels = img.channels
    pixels = img.pixels  # flat RGBA array, read-only access

    # Start fingerprint with dimensions and channel count
    fp = (w, h, channels)

    # Sample pixels at fixed positions across the image to build
    # a content hash. 16 sample points is enough to distinguish
    # different textures while being very fast even on 8K images.
    total_pixels = w * h
    sample_count = min(16, total_pixels)

    sampled = []
    for i in range(sample_count):
        # Spread samples evenly across the pixel array
        pixel_index = int((i / sample_count) * total_pixels)
        offset = pixel_index * channels
        # Read RGBA values rounded to avoid float precision issues
        sample = tuple(round(pixels[offset + c], 4) for c in range(min(channels, 4)))
        sampled.append(sample)

    return fp + tuple(sampled)


def images_are_identical(img_a, img_b):
    """Full pixel comparison between two images.

    Only called when fingerprints match, so this is a rare slow path for
    confirmation.
    """
    if img_a.size[0] != img_b.size[0] or img_a.size[1] != img_b.size[1]:
        return False
    if img_a.channels != img_b.channels:
        return False

    px_a = img_a.pixels[:]
    px_b = img_b.pixels[:]

    if len(px_a) != len(px_b):
        return False

    # Compare in chunks for efficiency
    chunk = 4096
    for start in range(0, len(px_a), chunk):
        end = min(start + chunk, len(px_a))
        for i in range(start, end):
            if abs(px_a[i] - px_b[i]) > 0.001:
                return False
    return True


def clean_images_all(context):
    """Remove truly identical images by comparing pixel content.

    Returns ``(removed_count, detail_string)``.
    """
    removed = 0
    images = []
    for img in bpy.data.images:
        try:
            if img.type == "IMAGE" and img.has_data and img.name not in ("Render Result", "Viewer Node"):
                images.append(img)
        except ReferenceError:
            continue

    if len(images) < 2:
        return (0, "Not enough images to compare")

    # Phase 1: Group images by fingerprint (fast)
    fingerprint_groups = {}
    for img in images:
        fp = get_image_fingerprint(img)
        if fp is None:
            continue
        fingerprint_groups.setdefault(fp, []).append(img)

    # Phase 2: For groups with matching fingerprints, do full comparison
    for _fp, group in fingerprint_groups.items():
        if len(group) < 2:
            continue

        # Find clusters of truly identical images within this group
        merged = set()
        for i, img_a in enumerate(group):
            try:
                if img_a.name in merged:
                    continue
            except ReferenceError:
                continue
            for j in range(i + 1, len(group)):
                img_b = group[j]
                try:
                    if img_b.name in merged:
                        continue
                except ReferenceError:
                    continue

                if images_are_identical(img_a, img_b):
                    # Keep whichever has more material users, or img_a as tiebreaker
                    users_a = get_image_users(img_a)
                    users_b = get_image_users(img_b)

                    if users_b > users_a:
                        keeper, duplicate = img_b, img_a
                    else:
                        keeper, duplicate = img_a, img_b

                    print(
                        f"  [AI Optimizer] Identical: '{duplicate.name}' == '{keeper.name}'"
                        f" → removing '{duplicate.name}'"
                    )
                    duplicate.user_remap(keeper)
                    bpy.data.images.remove(duplicate)
                    removed += 1
                    merged.add(duplicate.name)

                    # If img_a was the duplicate, stop comparing it
                    if duplicate == img_a:
                        break

    return (removed, f"Removed {removed} truly identical image(s)")


def clean_unused_all(context):
    """Remove all unused data blocks (orphaned materials, textures, meshes).

    Returns ``(removed_count, detail_string)``.
    """
    before = len(bpy.data.images) + len(bpy.data.materials) + len(bpy.data.meshes) + len(bpy.data.textures)

    bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)

    after = len(bpy.data.images) + len(bpy.data.materials) + len(bpy.data.meshes) + len(bpy.data.textures)

    removed = before - after
    return (removed, f"Removed {removed} unused data block(s)")


def resize_texture_single(img, props):
    """Resize a single image according to *props* settings.

    Returns ``True`` if the image was resized.
    """
    max_size = props.max_texture_size

    if img.type != "IMAGE" or not img.has_data:
        return False
    if img.name in ("Render Result", "Viewer Node"):
        return False

    w, h = img.size[0], img.size[1]

    needs_resize = (w != max_size or h != max_size) if props.resize_mode == "ALL" else (w > max_size or h > max_size)

    if not needs_resize:
        return False

    if props.resize_mode == "ALL":
        new_w, new_h = max_size, max_size
    else:
        scale = max_size / max(w, h)
        new_w = max(1, 2 ** round(math.log2(max(1, int(w * scale)))))
        new_h = max(1, 2 ** round(math.log2(max(1, int(h * scale)))))

    img.scale(new_w, new_h)
    img.pack()
    return True


def export_glb_all(context, props):
    """Export the scene as a compressed GLB. Returns a detail string."""
    # Determine output path
    if props.output_folder:
        output_dir = props.output_folder
    elif bpy.data.filepath:
        output_dir = os.path.dirname(bpy.data.filepath)
    else:
        output_dir = os.path.expanduser("~")

    output_path = os.path.join(output_dir, props.output_filename)

    export_settings = {
        "filepath": output_path,
        "export_format": "GLB",
        "use_selection": props.export_selected_only,
        "export_apply": True,
        "export_yup": True,
        "export_draco_mesh_compression_enable": props.use_draco,
        "export_draco_mesh_compression_level": props.draco_level,
        "export_draco_position_quantization": 14,
        "export_draco_normal_quantization": 10,
        "export_draco_texcoord_quantization": 12,
        "export_draco_color_quantization": 10,
        "export_image_format": props.image_format,
    }

    if props.image_format in ("JPEG", "WEBP"):
        export_settings["export_image_quality"] = props.image_quality

    try:
        bpy.ops.export_scene.gltf(**export_settings)
    except TypeError:
        # Fallback for older Blender versions
        bpy.ops.export_scene.gltf(
            filepath=output_path,
            export_format="GLB",
            export_apply=True,
            export_draco_mesh_compression_enable=props.use_draco,
        )

    if os.path.exists(output_path):
        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        return f"Exported: {output_path} ({size_mb:.2f} MB)"
    return "Export may have failed — file not found"


# Properties to save/load (must match AIOPT_Properties attribute names)
SAVEABLE_PROPS = [
    "run_fix_geometry",
    "run_decimate",
    "run_clean_images",
    "run_clean_unused",
    "run_resize_textures",
    "run_export",
    "merge_distance",
    "recalculate_normals",
    "fix_manifold",
    "decimate_ratio",
    "max_texture_size",
    "resize_mode",
    "output_filename",
    "output_folder",
    "export_selected_only",
    "use_draco",
    "draco_level",
    "image_format",
    "image_quality",
]


def save_defaults(props):
    """Save current settings to JSON."""
    data = {}
    for key in SAVEABLE_PROPS:
        data[key] = getattr(props, key)
    config_path = get_config_path()
    with open(config_path, "w") as f:
        json.dump(data, f, indent=2)
    return config_path


def load_defaults(props):
    """Load saved settings from JSON. Returns True if loaded, False if no file."""
    config_path = get_config_path()
    if not os.path.exists(config_path):
        return False
    try:
        with open(config_path) as f:
            data = json.load(f)
        for key, value in data.items():
            if key in SAVEABLE_PROPS:
                try:
                    setattr(props, key, value)
                except (TypeError, AttributeError):
                    pass  # Skip if property type changed between versions
        return True
    except (OSError, json.JSONDecodeError):
        return False


# =============================================================
#  Operators (one per step + full pipeline)
# =============================================================


class AIOPT_OT_fix_geometry(Operator):
    bl_idname = "ai_optimizer.fix_geometry"
    bl_label = "Fix Geometry"
    bl_description = "Fix non-manifold geometry, merge close vertices, recalculate normals"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.ai_optimizer
        meshes = get_selected_meshes()

        if not meshes:
            self.report({"ERROR"}, "No mesh objects found")
            return {"CANCELLED"}

        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        fixed = 0
        method_used = "none"
        for obj in meshes:
            obj_fixed, obj_method = fix_geometry_single(context, obj, props)
            if obj_fixed:
                fixed += 1
            if obj_method != "none":
                method_used = obj_method

        msg = f"Fixed geometry on {len(meshes)} object(s), {fixed} manifold fix(es)"
        if props.fix_manifold:
            msg += f" — method: {method_used}"
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class AIOPT_OT_decimate(Operator):
    bl_idname = "ai_optimizer.decimate"
    bl_label = "Decimate"
    bl_description = "Reduce polygon count using the Decimate modifier"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.ai_optimizer
        meshes = get_selected_meshes()

        if not meshes:
            self.report({"ERROR"}, "No mesh objects found")
            return {"CANCELLED"}

        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        faces_before = count_faces(meshes)

        for obj in meshes:
            decimate_single(context, obj, props)

        faces_after = count_faces(meshes)
        reduction = (1 - faces_after / max(faces_before, 1)) * 100

        self.report({"INFO"}, f"Decimated: {faces_before:,} → {faces_after:,} faces ({reduction:.1f}% reduction)")
        return {"FINISHED"}


class AIOPT_OT_clean_images(Operator):
    bl_idname = "ai_optimizer.clean_images"
    bl_label = "Clean Duplicate Images"
    bl_description = (
        "Remove truly identical images by comparing pixel content. "
        "Safe for multi-import sessions — different textures with similar names are kept"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        _removed, detail = clean_images_all(context)
        self.report({"INFO"}, detail)
        return {"FINISHED"}


class AIOPT_OT_clean_unused(Operator):
    bl_idname = "ai_optimizer.clean_unused"
    bl_label = "Clean Unused Data"
    bl_description = "Remove all unused data blocks (orphaned materials, textures, meshes)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        _removed, detail = clean_unused_all(context)
        self.report({"INFO"}, detail)
        return {"FINISHED"}


class AIOPT_OT_resize_textures(Operator):
    bl_idname = "ai_optimizer.resize_textures"
    bl_label = "Resize Textures"
    bl_description = "Resize all textures to the configured maximum size"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.ai_optimizer
        resized = 0

        for img in bpy.data.images:
            if resize_texture_single(img, props):
                resized += 1

        self.report({"INFO"}, f"Resized {resized} texture(s) to max {props.max_texture_size}px")
        return {"FINISHED"}


class AIOPT_OT_export_glb(Operator):
    bl_idname = "ai_optimizer.export_glb"
    bl_label = "Export GLB"
    bl_description = "Export as compressed GLB with optimized settings"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = context.scene.ai_optimizer
        detail = export_glb_all(context, props)

        if detail.startswith("Export may have failed"):
            self.report({"ERROR"}, detail)
        else:
            self.report({"INFO"}, detail)

        return {"FINISHED"}


class AIOPT_OT_run_all(Operator):
    bl_idname = "ai_optimizer.run_all"
    bl_label = "Run Full Pipeline"
    bl_description = "Run all optimization steps in order"
    bl_options = {"REGISTER"}

    _timer = None
    _steps: list  # list of (name, setup_fn, tick_fn, teardown_fn)
    _sub_items: list  # items for current step
    _start_time: float
    _step_start_time: float
    _faces_before: int  # for decimate summary

    @classmethod
    def poll(cls, context):
        state = context.window_manager.ai_optimizer_pipeline
        if state.is_running:
            return False
        meshes = get_selected_meshes()
        return len(meshes) > 0

    # ----- invoke -----

    def invoke(self, context, event):
        props = context.scene.ai_optimizer
        state = context.window_manager.ai_optimizer_pipeline

        # Build step list based on enabled toggles
        self._steps = []
        if props.run_fix_geometry:
            self._steps.append(
                (
                    "Fix Geometry",
                    self._setup_fix_geometry,
                    self._tick_fix_geometry,
                    self._teardown_fix_geometry,
                )
            )
        if props.run_decimate:
            self._steps.append(("Decimate", self._setup_decimate, self._tick_decimate, self._teardown_decimate))
        if props.run_clean_images:
            self._steps.append(("Clean Images", self._setup_clean_images, self._tick_clean_images, self._teardown_noop))
        if props.run_clean_unused:
            self._steps.append(("Clean Unused", self._setup_clean_unused, self._tick_clean_unused, self._teardown_noop))
        if props.run_resize_textures:
            self._steps.append(
                (
                    "Resize Textures",
                    self._setup_resize_textures,
                    self._tick_resize_textures,
                    self._teardown_resize_textures,
                )
            )
        if props.run_export:
            self._steps.append(("Export GLB", self._setup_export, self._tick_export, self._teardown_noop))

        if not self._steps:
            self.report({"WARNING"}, "No pipeline steps enabled")
            return {"CANCELLED"}

        # Push undo snapshot so we can roll back on cancel
        bpy.ops.ed.undo_push(message="Before AI Optimizer Pipeline")

        # Initialise runtime state
        self._sub_items = []
        self._start_time = time.monotonic()
        self._step_start_time = self._start_time
        self._faces_before = 0

        state.is_running = True
        state.was_cancelled = False
        state.current_step_index = 0
        state.current_step_name = self._steps[0][0]
        state.current_sub_step = 0
        state.total_sub_steps = 0
        state.step_results = "[]"
        state.total_elapsed = 0.0
        state.total_steps = len(self._steps)
        state.step_names = json.dumps([s[0] for s in self._steps])

        # First modal tick will run setup — don't do it here so the UI
        # has a chance to redraw and show the progress panel first.
        self._needs_setup = True

        # Start timer & modal handler
        self._timer = context.window_manager.event_timer_add(0.01, window=context.window)
        context.window_manager.modal_handler_add(self)

        # Force immediate redraw so progress panel appears
        for area in context.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()

        return {"RUNNING_MODAL"}

    # ----- modal -----

    def modal(self, context, event):
        state = context.window_manager.ai_optimizer_pipeline

        if event.type == "ESC" or state.was_cancelled:
            self._cancel_pipeline(context)
            return {"CANCELLED"}

        if event.type != "TIMER":
            return {"PASS_THROUGH"}

        # First tick after invoke (or after advancing to a new step):
        # run setup and return so the UI redraws before heavy work starts.
        if self._needs_setup:
            step_idx = state.current_step_index
            count = self._steps[step_idx][1](context)  # setup function
            state.total_sub_steps = count
            state.current_sub_step = 0
            self._step_start_time = time.monotonic()
            self._needs_setup = False
            for area in context.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()
            return {"RUNNING_MODAL"}

        step_idx = state.current_step_index
        _name, _setup, tick, teardown = self._steps[step_idx]

        sub = state.current_sub_step
        total = state.total_sub_steps

        if sub < total:
            # Process one sub-item
            tick(context, sub)
            state.current_sub_step = sub + 1
        else:
            # Teardown current step
            teardown_detail = teardown(context)
            elapsed = time.monotonic() - self._step_start_time
            results = json.loads(state.step_results)
            results.append(
                {
                    "name": self._steps[step_idx][0],
                    "status": "completed",
                    "detail": teardown_detail or "",
                    "duration": round(elapsed, 2),
                }
            )
            state.step_results = json.dumps(results)

            # Move to next step
            next_idx = step_idx + 1
            if next_idx >= len(self._steps):
                # All steps done
                self._finish(context)
                return {"FINISHED"}

            # Defer setup to next tick so the UI redraws first
            state.current_step_index = next_idx
            state.current_step_name = self._steps[next_idx][0]
            self._needs_setup = True

        state.total_elapsed = round(time.monotonic() - self._start_time, 2)

        # Force UI redraw
        for area in context.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()

        return {"RUNNING_MODAL"}

    # ----- finish / cancel -----

    def _finish(self, context):
        state = context.window_manager.ai_optimizer_pipeline
        state.is_running = False
        state.total_elapsed = round(time.monotonic() - self._start_time, 2)
        state.current_step_name = ""
        context.window_manager.event_timer_remove(self._timer)
        self._timer = None
        self.report({"INFO"}, f"Pipeline complete in {state.total_elapsed:.1f}s")

    def _cancel_pipeline(self, context):
        state = context.window_manager.ai_optimizer_pipeline

        # Mark current step as cancelled, remaining as skipped
        results = json.loads(state.step_results)
        for i in range(state.current_step_index, len(self._steps)):
            status = "cancelled" if i == state.current_step_index else "skipped"
            results.append(
                {
                    "name": self._steps[i][0],
                    "status": status,
                    "detail": "",
                    "duration": 0.0,
                }
            )
        state.step_results = json.dumps(results)
        state.was_cancelled = True
        state.is_running = False
        state.total_elapsed = round(time.monotonic() - self._start_time, 2)
        state.current_step_name = ""

        context.window_manager.event_timer_remove(self._timer)
        self._timer = None

        # Undo all changes
        bpy.ops.ed.undo()

        self.report({"WARNING"}, "Pipeline cancelled — all changes undone")

    # ----- setup / tick / teardown helpers -----

    def _teardown_noop(self, context):
        return None

    # -- Fix Geometry --

    def _setup_fix_geometry(self, context):
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        self._sub_items = get_selected_meshes()
        self._fix_count = 0
        self._fix_method = "none"
        return len(self._sub_items)

    def _tick_fix_geometry(self, context, index):
        props = context.scene.ai_optimizer
        obj = self._sub_items[index]
        fixed, method = fix_geometry_single(context, obj, props)
        if fixed:
            self._fix_count += 1
        if method != "none":
            self._fix_method = method
        return f"{obj.name}: {method}"

    def _teardown_fix_geometry(self, context):
        count = self._fix_count
        method = self._fix_method
        total = len(self._sub_items)
        detail = f"{count}/{total} fixed"
        if method != "none":
            detail += f" — method: {method}"
        return detail

    # -- Decimate --

    def _setup_decimate(self, context):
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        self._sub_items = get_selected_meshes()
        self._faces_before = count_faces(self._sub_items)
        return len(self._sub_items)

    def _tick_decimate(self, context, index):
        props = context.scene.ai_optimizer
        obj = self._sub_items[index]
        decimate_single(context, obj, props)
        return obj.name

    def _teardown_decimate(self, context):
        meshes = get_selected_meshes()
        faces_after = count_faces(meshes)
        reduction = (1 - faces_after / max(self._faces_before, 1)) * 100
        return f"{self._faces_before:,} → {faces_after:,} faces ({reduction:.1f}% reduction)"

    # -- Clean Images --

    def _setup_clean_images(self, context):
        self._sub_items = [None]  # single item
        return 1

    def _tick_clean_images(self, context, index):
        _removed, detail = clean_images_all(context)
        return detail

    # -- Clean Unused --

    def _setup_clean_unused(self, context):
        self._sub_items = [None]
        return 1

    def _tick_clean_unused(self, context, index):
        _removed, detail = clean_unused_all(context)
        return detail

    # -- Resize Textures --

    def _setup_resize_textures(self, context):
        props = context.scene.ai_optimizer
        max_size = props.max_texture_size
        self._sub_items = []
        for img in bpy.data.images:
            if img.type != "IMAGE" or not img.has_data:
                continue
            if img.name in ("Render Result", "Viewer Node"):
                continue
            w, h = img.size[0], img.size[1]
            needs_resize = (
                (w != max_size or h != max_size) if props.resize_mode == "ALL" else (w > max_size or h > max_size)
            )
            if needs_resize:
                self._sub_items.append(img)
        self._resized_count = 0
        return len(self._sub_items)

    def _tick_resize_textures(self, context, index):
        props = context.scene.ai_optimizer
        img = self._sub_items[index]
        if resize_texture_single(img, props):
            self._resized_count += 1
        return img.name

    def _teardown_resize_textures(self, context):
        return f"Resized {self._resized_count} texture(s)"

    # -- Export GLB --

    def _setup_export(self, context):
        self._sub_items = [None]
        return 1

    def _tick_export(self, context, index):
        props = context.scene.ai_optimizer
        detail = export_glb_all(context, props)
        return detail


class AIOPT_OT_cancel_pipeline(Operator):
    bl_idname = "ai_optimizer.cancel_pipeline"
    bl_label = "Cancel Pipeline"
    bl_description = "Cancel the running pipeline and undo all changes"

    @classmethod
    def poll(cls, context):
        state = context.window_manager.ai_optimizer_pipeline
        return state.is_running

    def execute(self, context):
        state = context.window_manager.ai_optimizer_pipeline
        state.was_cancelled = True
        return {"FINISHED"}


class AIOPT_OT_dismiss_pipeline(Operator):
    bl_idname = "ai_optimizer.dismiss_pipeline"
    bl_label = "Dismiss"
    bl_description = "Dismiss the pipeline results"

    @classmethod
    def poll(cls, context):
        state = context.window_manager.ai_optimizer_pipeline
        return not state.is_running and state.step_results != "[]"

    def execute(self, context):
        state = context.window_manager.ai_optimizer_pipeline
        state.step_results = "[]"
        state.step_names = "[]"
        state.was_cancelled = False
        state.total_elapsed = 0.0
        state.current_step_index = 0
        state.total_steps = 0
        return {"FINISHED"}


class AIOPT_OT_show_stats(Operator):
    bl_idname = "ai_optimizer.show_stats"
    bl_label = "Refresh Stats"
    bl_description = "Update the model statistics display"

    def execute(self, context):
        # Stats are drawn dynamically, this just forces a redraw
        for area in context.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()
        return {"FINISHED"}


class AIOPT_OT_save_defaults(Operator):
    bl_idname = "ai_optimizer.save_defaults"
    bl_label = "Save as Default"
    bl_description = "Save current settings as defaults for all future sessions"

    def execute(self, context):
        props = context.scene.ai_optimizer
        config_path = save_defaults(props)
        self.report({"INFO"}, f"Defaults saved to {config_path}")
        return {"FINISHED"}


class AIOPT_OT_load_defaults(Operator):
    bl_idname = "ai_optimizer.load_defaults"
    bl_label = "Load Defaults"
    bl_description = "Restore previously saved default settings"

    def execute(self, context):
        props = context.scene.ai_optimizer
        if load_defaults(props):
            self.report({"INFO"}, "Defaults loaded")
        else:
            self.report({"WARNING"}, "No saved defaults found — save some first")
        return {"FINISHED"}


class AIOPT_OT_reset_defaults(Operator):
    bl_idname = "ai_optimizer.reset_defaults"
    bl_label = "Reset to Factory"
    bl_description = "Reset all settings to factory defaults and delete saved config"

    def execute(self, context):
        props = context.scene.ai_optimizer

        # Reset all properties to their defaults
        for key in SAVEABLE_PROPS:
            prop = props.bl_rna.properties.get(key)
            if prop:
                setattr(props, key, prop.default)

        # Delete saved config
        config_path = get_config_path()
        if os.path.exists(config_path):
            os.remove(config_path)

        self.report({"INFO"}, "All settings reset to factory defaults")
        return {"FINISHED"}


# =============================================================
#  Properties
# =============================================================


class AIOPT_Properties(PropertyGroup):
    # -- Pipeline toggles --
    run_fix_geometry: BoolProperty(
        name="Fix Geometry", default=True, description="Fix non-manifold geometry, merge vertices, recalculate normals"
    )
    run_decimate: BoolProperty(name="Decimate", default=True, description="Reduce polygon count")
    run_clean_images: BoolProperty(name="Clean Images", default=True, description="Remove duplicate images")
    run_clean_unused: BoolProperty(name="Clean Unused", default=True, description="Remove unused data blocks")
    run_resize_textures: BoolProperty(name="Resize Textures", default=True, description="Resize textures to max size")
    run_export: BoolProperty(name="Export GLB", default=True, description="Export optimized GLB")

    # -- Geometry settings --
    merge_distance: FloatProperty(
        name="Merge Distance",
        default=0.0001,
        min=0.00001,
        max=1.0,
        precision=5,
        description="Merge vertices closer than this distance",
    )
    recalculate_normals: BoolProperty(name="Recalculate Normals", default=True, description="Fix flipped normals")
    fix_manifold: BoolProperty(
        name="Fix Manifold", default=True, description="Attempt to fix non-manifold (holes, open edges)"
    )

    # -- Decimate settings --
    decimate_ratio: FloatProperty(
        name="Ratio",
        default=0.1,
        min=0.01,
        max=1.0,
        step=1,
        precision=3,
        description="Decimation ratio. 0.1 = keep 10% of faces",
        subtype="FACTOR",
    )

    # -- Texture settings --
    max_texture_size: IntProperty(
        name="Max Size (px)", default=1024, min=64, max=8192, description="Maximum texture dimension in pixels"
    )
    resize_mode: EnumProperty(
        name="Resize Mode",
        items=[
            ("DOWNSIZE", "Downsize Only", "Only shrink textures larger than max size"),
            ("ALL", "Resize All", "Resize all textures to exactly max size"),
        ],
        default="DOWNSIZE",
        description="How to handle texture resizing",
    )

    # -- Export settings --
    output_filename: StringProperty(name="Filename", default="optimized_model.glb", description="Output filename")
    output_folder: StringProperty(
        name="Folder", default="", subtype="DIR_PATH", description="Output folder (blank = same as .blend file)"
    )
    export_selected_only: BoolProperty(name="Selected Only", default=False, description="Export only selected objects")
    use_draco: BoolProperty(
        name="Draco Compression", default=True, description="Use Draco mesh compression (recommended for web)"
    )
    draco_level: IntProperty(
        name="Draco Level",
        default=6,
        min=0,
        max=10,
        description="Draco compression level (higher = smaller file, slower decode)",
    )
    image_format: EnumProperty(
        name="Image Format",
        items=[
            ("WEBP", "WebP", "Smallest file size, good quality"),
            ("JPEG", "JPEG", "Good compression, widely supported"),
            ("NONE", "PNG (Original)", "Keep original PNG, largest file size"),
        ],
        default="WEBP",
        description="Image format for textures in the GLB",
    )
    image_quality: IntProperty(
        name="Quality", default=85, min=1, max=100, description="Image quality for JPEG/WebP (80-90 recommended)"
    )


class AIOPT_PipelineState(PropertyGroup):
    """Runtime state for pipeline progress tracking. Stored on WindowManager."""

    is_running: BoolProperty(default=False)
    was_cancelled: BoolProperty(default=False)
    current_step_index: IntProperty(default=0)
    current_step_name: StringProperty(default="")
    current_sub_step: IntProperty(default=0)
    total_sub_steps: IntProperty(default=0)
    step_results: StringProperty(default="[]")  # JSON array
    total_elapsed: FloatProperty(default=0.0)
    total_steps: IntProperty(default=0)
    step_names: StringProperty(default="[]")  # JSON array of step names


# =============================================================
#  UI Panels
# =============================================================


class AIOPT_PT_main_panel(Panel):
    bl_label = "AI Model Optimizer"
    bl_idname = "AIOPT_PT_main_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AI Optimizer"

    def draw(self, context):
        layout = self.layout
        state = context.window_manager.ai_optimizer_pipeline

        # While the pipeline is running or showing results, hide everything
        # — the progress sub-panel handles all UI during that time.
        if state.is_running or state.step_results != "[]":
            return

        props = context.scene.ai_optimizer

        # --- Stats ---
        meshes = get_selected_meshes()
        if meshes:
            box = layout.box()
            col = box.column(align=True)
            col.label(text="Model Stats", icon="INFO")

            total_faces = count_faces(meshes)
            total_verts = sum(len(obj.data.vertices) for obj in meshes)
            total_images = len(
                [i for i in bpy.data.images if i.type == "IMAGE" and i.name not in ("Render Result", "Viewer Node")]
            )
            total_materials = len(bpy.data.materials)

            col.label(text=f"Objects: {len(meshes)}")
            col.label(text=f"Faces: {total_faces:,}")
            col.label(text=f"Vertices: {total_verts:,}")
            col.label(text=f"Images: {total_images}")
            col.label(text=f"Materials: {total_materials}")

            if is_print3d_available():
                col.label(text="3D Print Toolbox: installed", icon="CHECKMARK")
            else:
                col.label(text="3D Print Toolbox: not found", icon="ERROR")

            col.operator("ai_optimizer.show_stats", icon="FILE_REFRESH")
        else:
            layout.label(text="No mesh objects found", icon="ERROR")

        layout.separator()

        # --- Run All ---
        box = layout.box()
        col = box.column(align=True)
        col.label(text="Full Pipeline", icon="PLAY")
        col.scale_y = 1.5
        col.operator("ai_optimizer.run_all", icon="PLAY")
        col.scale_y = 1.0
        col.separator()

        col.label(text="Steps to include:")
        row = col.row(align=True)
        row.prop(props, "run_fix_geometry", toggle=True, text="Geometry")
        row.prop(props, "run_decimate", toggle=True, text="Decimate")
        row = col.row(align=True)
        row.prop(props, "run_clean_images", toggle=True, text="Images")
        row.prop(props, "run_clean_unused", toggle=True, text="Unused")
        row = col.row(align=True)
        row.prop(props, "run_resize_textures", toggle=True, text="Resize")
        row.prop(props, "run_export", toggle=True, text="Export")


class AIOPT_PT_progress_panel(Panel):
    bl_label = "Pipeline Progress"
    bl_idname = "AIOPT_PT_progress_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AI Optimizer"
    bl_parent_id = "AIOPT_PT_main_panel"
    bl_options = set()

    @classmethod
    def poll(cls, context):
        state = context.window_manager.ai_optimizer_pipeline
        results = json.loads(state.step_results) if state.step_results != "[]" else []
        return state.is_running or len(results) > 0

    def draw(self, context):
        layout = self.layout
        state = context.window_manager.ai_optimizer_pipeline
        results = json.loads(state.step_results) if state.step_results != "[]" else []
        all_names = json.loads(state.step_names) if state.step_names != "[]" else []

        if state.is_running:
            self._draw_running(layout, state, results, all_names)
        elif state.was_cancelled:
            self._draw_cancelled(layout, state, results, all_names)
        else:
            self._draw_completed(layout, state, results)

    def _draw_running(self, layout, state, results, all_names):
        col = layout.column(align=True)

        # Completed steps
        for r in results:
            row = col.row()
            row.label(
                text=f"{r['name']} ({r['duration']:.1f}s)",
                icon="CHECKMARK",
            )

        # Current step
        row = col.row()
        if state.total_sub_steps > 1:
            row.label(
                text=f"{state.current_step_name} ({state.current_sub_step}/{state.total_sub_steps})",
                icon="PLAY",
            )
        else:
            row.label(text=state.current_step_name, icon="PLAY")

        # Pending steps
        completed_count = len(results)
        for i in range(completed_count + 1, len(all_names)):
            row = col.row()
            row.label(text=all_names[i], icon="RADIOBUT_OFF")

        # Overall progress box
        completed = len(results)
        total_sub = max(state.total_sub_steps, 1)
        step_fraction = state.current_sub_step / total_sub
        overall = (completed + step_fraction) / max(state.total_steps, 1)

        box = layout.box()
        row = box.row()
        row.label(text=f"Step {completed + 1}/{state.total_steps}")
        row.label(text=f"{overall:.0%}")

        # Cancel button
        layout.operator("ai_optimizer.cancel_pipeline", icon="CANCEL")

    def _draw_completed(self, layout, state, results):
        col = layout.column(align=True)

        for r in results:
            row = col.row()
            row.label(
                text=f"{r['name']} ({r['duration']:.1f}s)",
                icon="CHECKMARK",
            )
            if r.get("detail"):
                row = col.row()
                row.label(text=f"    {r['detail']}")

        col.separator()
        col.label(text=f"Total: {state.total_elapsed:.1f}s")

        layout.operator("ai_optimizer.dismiss_pipeline", icon="X")

    def _draw_cancelled(self, layout, state, results, all_names):
        col = layout.column(align=True)

        for r in results:
            status = r.get("status", "completed")
            if status == "completed":
                icon = "CHECKMARK"
            elif status == "cancelled":
                icon = "X"
            else:
                icon = "RADIOBUT_OFF"
            row = col.row()
            row.label(text=r["name"], icon=icon)

        col.separator()
        row = col.row()
        row.label(text="Changes have been undone.", icon="LOOP_BACK")

        layout.operator("ai_optimizer.dismiss_pipeline", icon="X")


class AIOPT_PT_geometry_panel(Panel):
    bl_label = "Geometry Fix"
    bl_idname = "AIOPT_PT_geometry_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AI Optimizer"
    bl_parent_id = "AIOPT_PT_main_panel"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return not context.window_manager.ai_optimizer_pipeline.is_running

    def draw(self, context):
        layout = self.layout
        props = context.scene.ai_optimizer

        col = layout.column(align=True)
        col.prop(props, "merge_distance")
        col.prop(props, "recalculate_normals")
        col.prop(props, "fix_manifold")

        if props.fix_manifold:
            layout.separator()
            if is_print3d_available():
                row = layout.row()
                row.label(text="3D Print Toolbox detected", icon="CHECKMARK")
            else:
                box = layout.box()
                col = box.column(align=True)
                col.label(text="3D Print Toolbox not found", icon="ERROR")
                col.label(text="Using manual manifold fix (fill holes).")
                col.label(text="Results may be less reliable.")
                col.separator()
                col.label(text="For better results, install it from:")
                col.label(text="Edit → Preferences → Get Extensions")
                col.label(text="and search '3D Print Toolbox'")

        layout.separator()
        layout.operator("ai_optimizer.fix_geometry", icon="MESH_DATA")


class AIOPT_PT_decimate_panel(Panel):
    bl_label = "Decimate"
    bl_idname = "AIOPT_PT_decimate_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AI Optimizer"
    bl_parent_id = "AIOPT_PT_main_panel"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return not context.window_manager.ai_optimizer_pipeline.is_running

    def draw(self, context):
        layout = self.layout
        props = context.scene.ai_optimizer

        col = layout.column(align=True)
        col.prop(props, "decimate_ratio", slider=True)

        # Show preview of what this ratio means
        meshes = get_selected_meshes()
        if meshes:
            current = count_faces(meshes)
            estimated = int(current * props.decimate_ratio)
            col.label(text=f"Current: {current:,} faces")
            col.label(text=f"Estimated after: ~{estimated:,} faces")

        layout.separator()
        layout.operator("ai_optimizer.decimate", icon="MOD_DECIM")


class AIOPT_PT_textures_panel(Panel):
    bl_label = "Textures"
    bl_idname = "AIOPT_PT_textures_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AI Optimizer"
    bl_parent_id = "AIOPT_PT_main_panel"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return not context.window_manager.ai_optimizer_pipeline.is_running

    def draw(self, context):
        layout = self.layout
        props = context.scene.ai_optimizer

        col = layout.column(align=True)
        col.label(text="Duplicate Removal:", icon="IMAGE_DATA")
        col.operator("ai_optimizer.clean_images", icon="BRUSH_DATA")
        col.operator("ai_optimizer.clean_unused", icon="TRASH")

        layout.separator()

        col = layout.column(align=True)
        col.label(text="Resize:", icon="IMAGE_REFERENCE")
        col.prop(props, "max_texture_size")
        col.prop(props, "resize_mode", text="")

        # Show current texture sizes
        for img in bpy.data.images:
            if img.type == "IMAGE" and img.has_data and img.name not in ("Render Result", "Viewer Node"):
                w, h = img.size[0], img.size[1]
                icon = "ERROR" if max(w, h) > props.max_texture_size else "CHECKMARK"
                col.label(text=f"  {img.name}: {w}x{h}", icon=icon)

        layout.separator()
        layout.operator("ai_optimizer.resize_textures", icon="FULLSCREEN_EXIT")


class AIOPT_PT_export_panel(Panel):
    bl_label = "Export"
    bl_idname = "AIOPT_PT_export_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AI Optimizer"
    bl_parent_id = "AIOPT_PT_main_panel"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return not context.window_manager.ai_optimizer_pipeline.is_running

    def draw(self, context):
        layout = self.layout
        props = context.scene.ai_optimizer

        col = layout.column(align=True)
        col.prop(props, "output_filename")
        col.prop(props, "output_folder")
        col.prop(props, "export_selected_only")

        layout.separator()

        col = layout.column(align=True)
        col.label(text="Compression:", icon="PACKAGE")
        col.prop(props, "use_draco")
        if props.use_draco:
            col.prop(props, "draco_level", slider=True)

        layout.separator()

        col = layout.column(align=True)
        col.label(text="Image Format:", icon="IMAGE_DATA")
        col.prop(props, "image_format", text="")
        if props.image_format in ("JPEG", "WEBP"):
            col.prop(props, "image_quality", slider=True)

        layout.separator()
        layout.operator("ai_optimizer.export_glb", icon="EXPORT")


class AIOPT_PT_presets_panel(Panel):
    bl_label = "Presets"
    bl_idname = "AIOPT_PT_presets_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AI Optimizer"
    bl_parent_id = "AIOPT_PT_main_panel"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return not context.window_manager.ai_optimizer_pipeline.is_running

    def draw(self, context):
        layout = self.layout

        row = layout.row(align=True)
        row.operator("ai_optimizer.save_defaults", icon="FILE_TICK")
        row.operator("ai_optimizer.load_defaults", icon="FILE_FOLDER")
        layout.operator("ai_optimizer.reset_defaults", icon="LOOP_BACK")

        # Show config file path for transparency
        config_path = get_config_path()
        if os.path.exists(config_path):
            col = layout.column(align=True)
            col.scale_y = 0.8
            col.label(text="Saved defaults found", icon="CHECKMARK")
        else:
            col = layout.column(align=True)
            col.scale_y = 0.8
            col.label(text="No saved defaults yet", icon="INFO")


# =============================================================
#  Registration
# =============================================================

classes = (
    AIOPT_Properties,
    AIOPT_PipelineState,
    AIOPT_OT_fix_geometry,
    AIOPT_OT_decimate,
    AIOPT_OT_clean_images,
    AIOPT_OT_clean_unused,
    AIOPT_OT_resize_textures,
    AIOPT_OT_export_glb,
    AIOPT_OT_run_all,
    AIOPT_OT_cancel_pipeline,
    AIOPT_OT_dismiss_pipeline,
    AIOPT_OT_show_stats,
    AIOPT_OT_save_defaults,
    AIOPT_OT_load_defaults,
    AIOPT_OT_reset_defaults,
    AIOPT_PT_main_panel,
    AIOPT_PT_progress_panel,
    AIOPT_PT_geometry_panel,
    AIOPT_PT_decimate_panel,
    AIOPT_PT_textures_panel,
    AIOPT_PT_export_panel,
    AIOPT_PT_presets_panel,
)


def _load_defaults_on_file(dummy):
    """Auto-load saved defaults when a new file is opened."""
    if hasattr(bpy.context, "scene") and hasattr(bpy.context.scene, "ai_optimizer"):
        props = bpy.context.scene.ai_optimizer
        if load_defaults(props):
            print("[AI Model Optimizer] Loaded saved defaults")


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ai_optimizer = bpy.props.PointerProperty(type=AIOPT_Properties)
    bpy.types.WindowManager.ai_optimizer_pipeline = bpy.props.PointerProperty(type=AIOPT_PipelineState)

    # Auto-load defaults when opening files
    if _load_defaults_on_file not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_load_defaults_on_file)

    # Load defaults now for the current session
    if hasattr(bpy.context, "scene") and bpy.context.scene is not None:
        load_defaults(bpy.context.scene.ai_optimizer)

    print("[AI Model Optimizer] Add-on registered")


def unregister():
    # Remove handler
    if _load_defaults_on_file in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_defaults_on_file)

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.WindowManager.ai_optimizer_pipeline
    del bpy.types.Scene.ai_optimizer
    print("[AI Model Optimizer] Add-on unregistered")


if __name__ == "__main__":
    register()
