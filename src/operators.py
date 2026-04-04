import json
import os
import time

import bpy
from bpy.types import Operator

from .geometry import (
    bake_normal_map_for_decimate,
    decimate_single,
    detect_and_apply_symmetry,
    fix_geometry_single,
    remove_interior_single,
)
from .materials import join_meshes_by_material, merge_duplicate_materials
from .textures import (
    clean_images_all,
    clean_unused_all,
    resize_texture_single,
)
from .utils import (
    SAVEABLE_PROPS,
    count_faces,
    export_glb_all,
    generate_lods,
    get_config_path,
    get_selected_meshes,
    load_defaults,
    save_defaults,
)


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

        # Material merge (operates on all materials, not per-object)
        mat_merged = 0
        if props.merge_materials:
            mat_merged, _detail = merge_duplicate_materials(context, props.merge_materials_threshold)

        # Mesh join
        join_detail = ""
        if props.join_meshes:
            meshes = get_selected_meshes()  # refresh after geometry fixes
            _result, join_detail = join_meshes_by_material(context, meshes, props.join_mode)

        msg = f"Fixed geometry on {len(meshes)} object(s), {fixed} manifold fix(es)"
        if props.fix_manifold:
            msg += f" — method: {method_used}"
        if mat_merged:
            msg += f", {mat_merged} materials merged"
        if join_detail:
            msg += f", {join_detail}"
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class AIOPT_OT_remove_interior(Operator):
    bl_idname = "ai_optimizer.remove_interior"
    bl_label = "Remove Interior"
    bl_description = "Remove hidden interior geometry"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.ai_optimizer
        meshes = get_selected_meshes()

        if not meshes:
            self.report({"ERROR"}, "No mesh objects found")
            return {"CANCELLED"}

        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        total_removed = 0
        for obj in meshes:
            total_removed += remove_interior_single(context, obj, props)

        method_label = "enclosed parts" if props.interior_method == "LOOSE_PARTS" else "ray cast"
        self.report({"INFO"}, f"Removed {total_removed:,} interior faces ({method_label})")
        return {"FINISHED"}


class AIOPT_OT_symmetry_mirror(Operator):
    bl_idname = "ai_optimizer.symmetry_mirror"
    bl_label = "Symmetry Mirror"
    bl_description = "Detect symmetry and apply mirror optimization"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.ai_optimizer
        meshes = get_selected_meshes()

        if not meshes:
            self.report({"ERROR"}, "No mesh objects found")
            return {"CANCELLED"}

        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        applied = 0
        for obj in meshes:
            was_applied, _score = detect_and_apply_symmetry(
                context, obj, props.symmetry_axis, props.symmetry_threshold, props.symmetry_min_score
            )
            if was_applied:
                applied += 1

        self.report({"INFO"}, f"Applied mirror to {applied}/{len(meshes)} object(s)")
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

        # Create high-poly copies for normal map baking before decimation
        highpoly_copies = {}
        if props.bake_normal_map:
            for obj in meshes:
                copy = obj.copy()
                copy.data = obj.data.copy()
                context.collection.objects.link(copy)
                copy.hide_set(True)
                highpoly_copies[obj.name] = copy

        for obj in meshes:
            decimate_single(context, obj, props)

        faces_after = count_faces(meshes)
        reduction = (1 - faces_after / max(faces_before, 1)) * 100

        msg = f"Decimated: {faces_before:,} → {faces_after:,} faces ({reduction:.1f}% reduction)"

        # Bake normal maps from high-poly copies onto decimated meshes
        if props.bake_normal_map and highpoly_copies:
            baked = 0
            for obj in meshes:
                highpoly = highpoly_copies.get(obj.name)
                if highpoly:
                    result = bake_normal_map_for_decimate(context, obj, highpoly, props)
                    if result:
                        baked += 1
            for copy in highpoly_copies.values():
                bpy.data.objects.remove(copy, do_unlink=True)
            msg += f", {baked} normal map(s) baked"

        self.report({"INFO"}, msg)
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

        # LOD generation (before main export)
        if props.run_lod:
            lod_detail = generate_lods(context, props)
            self.report({"INFO"}, lod_detail)

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
    _redraw_pending: bool  # burn one extra tick so the UI can actually repaint
    _start_time: float
    _step_start_time: float
    _faces_before: int  # for decimate summary
    _interior_removed: int

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
        if props.run_remove_interior:
            self._steps.append(
                (
                    "Remove Interior",
                    self._setup_remove_interior,
                    self._tick_remove_interior,
                    self._teardown_remove_interior,
                )
            )
        if props.run_symmetry:
            self._steps.append(
                (
                    "Symmetry Mirror",
                    self._setup_symmetry,
                    self._tick_symmetry,
                    self._teardown_symmetry,
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
        if props.run_lod:
            self._steps.append(("LOD Generation", self._setup_lod, self._tick_lod, self._teardown_lod))
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
        self._redraw_pending = False

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
            self._redraw_pending = True
            for area in context.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()
            return {"RUNNING_MODAL"}

        # Burn one extra tick after setup so Blender's draw cycle
        # actually paints the updated progress panel before we block
        # the main thread with heavy per-object work.
        if self._redraw_pending:
            self._redraw_pending = False
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
            try:
                tick(context, sub)
            except Exception as e:
                print(f"  [AI Optimizer] Error in step '{_name}': {e}")
                self._cancel_pipeline(context)
                self.report({"ERROR"}, f"Pipeline failed at '{_name}': {e}")
                return {"CANCELLED"}
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
        for area in context.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()
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
        props = context.scene.ai_optimizer
        count = self._fix_count
        method = self._fix_method
        total = len(self._sub_items)
        detail = f"{count}/{total} fixed"
        if method != "none":
            detail += f" — method: {method}"

        if props.merge_materials:
            mat_count, _d = merge_duplicate_materials(context, props.merge_materials_threshold)
            detail += f", {mat_count} materials merged"

        if props.join_meshes:
            meshes = get_selected_meshes()
            _result, join_d = join_meshes_by_material(context, meshes, props.join_mode)
            detail += f", {join_d}"

        return detail

    # -- Remove Interior --

    def _setup_remove_interior(self, context):
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        self._sub_items = get_selected_meshes()
        self._interior_removed = 0
        return len(self._sub_items)

    def _tick_remove_interior(self, context, index):
        props = context.scene.ai_optimizer
        obj = self._sub_items[index]
        removed = remove_interior_single(context, obj, props)
        self._interior_removed += removed
        return obj.name

    def _teardown_remove_interior(self, context):
        return f"Removed {self._interior_removed:,} interior faces"

    # -- Symmetry Mirror --

    def _setup_symmetry(self, context):
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        self._sub_items = get_selected_meshes()
        self._symmetry_applied = 0
        self._symmetry_faces_before = count_faces(self._sub_items)
        return len(self._sub_items)

    def _tick_symmetry(self, context, index):
        props = context.scene.ai_optimizer
        obj = self._sub_items[index]
        applied, score = detect_and_apply_symmetry(
            context, obj, props.symmetry_axis, props.symmetry_threshold, props.symmetry_min_score
        )
        if applied:
            self._symmetry_applied += 1
        return f"{obj.name}: {'applied' if applied else f'skipped ({score:.0%})'}"

    def _teardown_symmetry(self, context):
        faces_after = count_faces(get_selected_meshes())
        removed = self._symmetry_faces_before - faces_after
        return f"Mirrored {self._symmetry_applied} object(s), {removed:,} faces removed"

    # -- Decimate --

    def _setup_decimate(self, context):
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        self._sub_items = get_selected_meshes()
        self._faces_before = count_faces(self._sub_items)
        props = context.scene.ai_optimizer
        self._highpoly_copies = {}
        if props.bake_normal_map:
            for obj in self._sub_items:
                copy = obj.copy()
                copy.data = obj.data.copy()
                context.collection.objects.link(copy)
                copy.hide_set(True)
                self._highpoly_copies[obj.name] = copy
        return len(self._sub_items)

    def _tick_decimate(self, context, index):
        props = context.scene.ai_optimizer
        obj = self._sub_items[index]
        decimate_single(context, obj, props)
        return obj.name

    def _teardown_decimate(self, context):
        props = context.scene.ai_optimizer
        meshes = get_selected_meshes()
        faces_after = count_faces(meshes)
        reduction = (1 - faces_after / max(self._faces_before, 1)) * 100
        detail = f"{self._faces_before:,} → {faces_after:,} faces ({reduction:.1f}% reduction)"

        if props.bake_normal_map and self._highpoly_copies:
            baked = 0
            for obj in meshes:
                highpoly = self._highpoly_copies.get(obj.name)
                if highpoly:
                    result = bake_normal_map_for_decimate(context, obj, highpoly, props)
                    if result:
                        baked += 1
            # Clean up highpoly copies
            for copy in self._highpoly_copies.values():
                bpy.data.objects.remove(copy, do_unlink=True)
            self._highpoly_copies = {}
            detail += f", {baked} normal map(s) baked"

        return detail

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

    # -- LOD Generation --

    def _setup_lod(self, context):
        self._sub_items = [None]
        self._lod_detail = ""
        return 1

    def _tick_lod(self, context, index):
        props = context.scene.ai_optimizer
        detail = generate_lods(context, props)
        self._lod_detail = detail
        return detail

    def _teardown_lod(self, context):
        return self._lod_detail

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
