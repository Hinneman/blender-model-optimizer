import json
import math
import os
import subprocess
import sys
import tempfile
import time

import bpy
from bpy.types import Operator

from .geometry import (
    bake_normal_map_for_decimate,
    decimate_single,
    detect_and_apply_symmetry,
    fix_geometry_single,
    floor_snap_all,
    remove_interior_single,
    remove_small_pieces_single,
)
from .materials import join_meshes_by_material, merge_duplicate_materials
from .textures import (
    clean_images_all,
    clean_unused_all,
    resize_texture_single,
)
from .utils import (
    SAVEABLE_PROPS,
    CancelToken,
    PipelineCancelled,
    count_faces,
    debug_buffer_is_empty,
    export_model,
    generate_lods,
    get_config_path,
    get_debug_log_text,
    get_selected_meshes,
    load_defaults,
    log,
    save_defaults,
)


def _format_setting(value):
    """Pretty-print a property value for the verbose start-line log.

    Trims Blender's 32-bit float noise (e.g. 0.10000000149 → 0.1) while
    leaving ints, bools, and strings untouched. Called only inside the
    DEBUG-gated start line, so performance cost is acceptable.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return f"{value:g}"
    return value


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
            mat_merged, _detail = merge_duplicate_materials(context, props.merge_materials_threshold_pct / 100.0)

        # Mesh join
        join_detail = ""
        if props.join_meshes:
            meshes = get_selected_meshes()  # refresh after geometry fixes
            _result, join_detail = join_meshes_by_material(context, meshes, props.join_mode)

        msg = f"Fixed geometry on {len(meshes)} object(s), {fixed} manifold fix(es)"
        if props.manifold_method != "OFF":
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
                context, obj, props.symmetry_axis, props.symmetry_threshold_mm / 1000.0, props.symmetry_min_score
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
    bl_label = "Export Model"
    bl_description = "Export the optimized model in the chosen format"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = context.scene.ai_optimizer

        # LOD generation (before main export)
        if props.run_lod:
            lod_detail = generate_lods(context, props)
            self.report({"INFO"}, lod_detail)

        detail = export_model(context, props)

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
    _token: object  # CancelToken instance for the current run
    _steps: list  # list of (name, setup_fn, tick_fn, teardown_fn)
    _sub_items: list  # items for current step
    _redraw_pending: bool  # burn one extra tick so the UI can actually repaint
    _start_time: float
    _step_start_time: float
    _faces_before: int  # for decimate summary
    _interior_removed: int
    _small_pieces_deleted: int
    _small_pieces_faces_removed: int
    _floor_snap_meshes: list
    _floor_snap_shift: float

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
        if props.run_remove_small_pieces:
            self._steps.append(
                (
                    "Remove Small Pieces",
                    self._setup_remove_small_pieces,
                    self._tick_remove_small_pieces,
                    self._teardown_remove_small_pieces,
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
        if props.run_floor_snap:
            self._steps.append(
                (
                    "Floor Snap",
                    self._setup_floor_snap,
                    self._tick_floor_snap,
                    self._teardown_floor_snap,
                )
            )
        if props.run_clean_images:
            self._steps.append(
                (
                    "Clean Images",
                    self._setup_clean_images,
                    self._tick_clean_images,
                    self._teardown_clean_images,
                )
            )
        if props.run_clean_unused:
            self._steps.append(
                (
                    "Clean Unused",
                    self._setup_clean_unused,
                    self._tick_clean_unused,
                    self._teardown_clean_unused,
                )
            )
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
            self._steps.append(("Export Model", self._setup_export, self._tick_export, self._teardown_export))

        if not self._steps:
            self.report({"WARNING"}, "No pipeline steps enabled")
            return {"CANCELLED"}

        # Push undo snapshot so we can roll back on cancel
        bpy.ops.ed.undo_push(message="Before AI Optimizer Pipeline")

        # Record how many Python-invoked operators Blender has tracked so far.
        # Every bpy.ops.* call the pipeline makes appends one entry here, so on
        # cancel we can stop undoing once the list length returns to this
        # baseline — giving us "just enough" undos instead of walking past our
        # snapshot into the user's prior work.
        self._ops_baseline = len(context.window_manager.operators)

        # Drop RENDERED/MATERIAL viewports to SOLID for the duration of the
        # pipeline. EEVEE's constant material re-sync against mutating meshes
        # can dereference freed image/material pointers on weak iGPUs and
        # crash Blender (seen with Intel integrated graphics + heavy pipelines).
        self._shading_restore = []
        for area in context.screen.areas:
            if area.type != "VIEW_3D":
                continue
            for space in area.spaces:
                if space.type != "VIEW_3D":
                    continue
                if space.shading.type in {"RENDERED", "MATERIAL"}:
                    self._shading_restore.append((space, space.shading.type))
                    space.shading.type = "SOLID"

        # Cooperative cancellation token — long Python loops in step
        # functions poll this between iterations.
        self._token = CancelToken()

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
        state.faces_before = count_faces(get_selected_meshes())
        state.faces_after = 0
        state.export_size = ""

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
            self._token.cancelled = True
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
            except PipelineCancelled:
                self._cancel_pipeline(context)
                return {"CANCELLED"}
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
        state.faces_after = count_faces(get_selected_meshes())
        state.is_running = False
        state.total_elapsed = round(time.monotonic() - self._start_time, 2)
        state.current_step_name = ""
        context.window_manager.event_timer_remove(self._timer)
        self._timer = None
        for space, shading_type in self._shading_restore:
            space.shading.type = shading_type
        self._shading_restore = []
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

        # Calling bpy.ops.ed.undo() from *inside* the modal callback is racy:
        # the modal operator is still on Blender's call stack while undo
        # rewinds the depsgraph / material relations, which has crashed
        # `DepsgraphNodeBuilder::build_materials` with a null deref on weak
        # iGPUs. Defer the undo loop to a one-shot app timer so it runs
        # after the modal returns {"CANCELLED"} and the operator is off
        # the stack.
        shading_restore = self._shading_restore
        self._shading_restore = []
        ops_baseline = getattr(self, "_ops_baseline", 0)

        def _deferred_rollback():
            # Undo until wm.operators returns to the length we saw at invoke,
            # which means every pipeline-pushed entry has been rolled back.
            # Bounded as a safety net so we never walk past our snapshot into
            # user work that happened before the pipeline started.
            max_steps = bpy.context.preferences.edit.undo_steps
            safety_cap = min(max_steps, 128)
            for _ in range(safety_cap):
                if len(bpy.context.window_manager.operators) <= ops_baseline:
                    break
                try:
                    result = bpy.ops.ed.undo()
                except RuntimeError:
                    break
                if "CANCELLED" in result or "PASS_THROUGH" in result:
                    break
            for space, shading_type in shading_restore:
                try:
                    space.shading.type = shading_type
                except (ReferenceError, AttributeError):
                    pass
            return None  # don't reschedule

        bpy.app.timers.register(_deferred_rollback, first_interval=0.05)

        self.report({"WARNING"}, "Pipeline cancelled — rolling back changes")

    # ----- setup / tick / teardown helpers -----

    def _teardown_noop(self, context):
        return None

    def _step_start(self, context, step_name, settings):
        """Record step start time and emit the DEBUG start line.

        ``settings`` is a dict of {prop_name: value} consumed by this step.
        Uses a separate ``_debug_step_start`` attribute so we do not clobber
        the existing ``_step_start_time`` written by the modal loop.
        """
        self._debug_step_start = time.perf_counter()
        if settings:
            pairs = ", ".join(f"{k}={_format_setting(v)}" for k, v in settings.items())
            log(context, f"▶ {step_name} — {pairs}", level="DEBUG")
        else:
            log(context, f"▶ {step_name}", level="DEBUG")

    def _step_end(self, context, step_name, result_line):
        """Emit the DEBUG end line with elapsed time. Returns the original result_line."""
        elapsed = time.perf_counter() - getattr(self, "_debug_step_start", time.perf_counter())
        summary = result_line.replace("\n", " | ")
        log(
            context,
            f"◀ {step_name} — {summary}, {elapsed:.2f}s",
            level="DEBUG",
        )
        return result_line

    # -- Fix Geometry --

    def _setup_fix_geometry(self, context):
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        self._sub_items = get_selected_meshes()
        self._fix_count = 0
        self._fix_method = "none"
        props = context.scene.ai_optimizer
        self._step_start(
            context,
            "Fix Geometry",
            {
                "merge_distance_mm": props.merge_distance_mm,
                "recalculate_normals": props.recalculate_normals,
                "manifold_method": props.manifold_method,
                "merge_materials": props.merge_materials,
                "merge_materials_threshold_pct": props.merge_materials_threshold_pct,
                "join_meshes": props.join_meshes,
                "join_mode": props.join_mode,
                "objects": len(self._sub_items),
            },
        )
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
        lines = []
        detail = f"{count}/{total} fixed"
        if method != "none":
            short = method.split("(")[0].strip()
            detail += f" ({short})"
        lines.append(detail)

        if props.merge_materials:
            mat_count, _d = merge_duplicate_materials(context, props.merge_materials_threshold_pct / 100.0)
            lines.append(f"{mat_count} material(s) merged")

        if props.join_meshes:
            meshes = get_selected_meshes()
            _result, join_d = join_meshes_by_material(context, meshes, props.join_mode)
            lines.append(join_d)

        return self._step_end(context, "Fix Geometry", "\n".join(lines))

    # -- Remove Interior --

    def _setup_remove_interior(self, context):
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        self._sub_items = get_selected_meshes()
        self._interior_removed = 0
        props = context.scene.ai_optimizer
        self._step_start(
            context,
            "Remove Interior",
            {
                "interior_method": props.interior_method,
                "objects": len(self._sub_items),
            },
        )
        return len(self._sub_items)

    def _tick_remove_interior(self, context, index):
        props = context.scene.ai_optimizer
        obj = self._sub_items[index]
        removed = remove_interior_single(context, obj, props, token=self._token)
        self._interior_removed += removed
        return obj.name

    def _teardown_remove_interior(self, context):
        return self._step_end(
            context,
            "Remove Interior",
            f"Removed {self._interior_removed:,} interior faces",
        )

    # -- Remove Small Pieces --

    def _setup_remove_small_pieces(self, context):
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        self._sub_items = get_selected_meshes()
        self._small_pieces_deleted = 0
        self._small_pieces_faces_removed = 0
        props = context.scene.ai_optimizer
        self._step_start(
            context,
            "Remove Small Pieces",
            {
                "small_pieces_face_threshold": props.small_pieces_face_threshold,
                "small_pieces_size_threshold_cm": props.small_pieces_size_threshold,
                "objects": len(self._sub_items),
            },
        )
        return len(self._sub_items)

    def _tick_remove_small_pieces(self, context, index):
        props = context.scene.ai_optimizer
        obj = self._sub_items[index]
        parts, faces = remove_small_pieces_single(context, obj, props, token=self._token)
        self._small_pieces_deleted += parts
        self._small_pieces_faces_removed += faces
        return obj.name

    def _teardown_remove_small_pieces(self, context):
        return self._step_end(
            context,
            "Remove Small Pieces",
            f"Removed {self._small_pieces_deleted:,} piece(s), {self._small_pieces_faces_removed:,} faces",
        )

    # -- Symmetry Mirror --

    def _setup_symmetry(self, context):
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        self._sub_items = get_selected_meshes()
        self._symmetry_applied = 0
        self._symmetry_faces_before = count_faces(self._sub_items)
        props = context.scene.ai_optimizer
        self._step_start(
            context,
            "Symmetry Mirror",
            {
                "axis": props.symmetry_axis,
                "threshold_mm": props.symmetry_threshold_mm,
                "min_score": props.symmetry_min_score,
                "objects": len(self._sub_items),
            },
        )
        return len(self._sub_items)

    def _tick_symmetry(self, context, index):
        props = context.scene.ai_optimizer
        obj = self._sub_items[index]
        applied, score = detect_and_apply_symmetry(
            context,
            obj,
            props.symmetry_axis,
            props.symmetry_threshold_mm / 1000.0,
            props.symmetry_min_score,
            token=self._token,
        )
        if applied:
            self._symmetry_applied += 1
        return f"{obj.name}: {'applied' if applied else f'skipped ({score:.0%})'}"

    def _teardown_symmetry(self, context):
        faces_after = count_faces(get_selected_meshes())
        removed = self._symmetry_faces_before - faces_after
        return self._step_end(
            context,
            "Symmetry Mirror",
            f"Mirrored {self._symmetry_applied} object(s), {removed:,} faces removed",
        )

    # -- Floor Snap --

    def _setup_floor_snap(self, context):
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        self._floor_snap_meshes = get_selected_meshes()
        self._floor_snap_shift = 0.0
        self._step_start(
            context,
            "Floor Snap",
            {"objects": len(self._floor_snap_meshes)},
        )
        # Single tick — operates on the group, not per-object.
        return 1 if self._floor_snap_meshes else 0

    def _tick_floor_snap(self, context, index):
        self._floor_snap_shift = floor_snap_all(self._floor_snap_meshes, token=self._token, context=context)
        return f"Shifted {self._floor_snap_shift * 1000:.1f} mm"

    def _teardown_floor_snap(self, context):
        if self._floor_snap_shift == 0.0:
            detail = "Floor snap: no shift needed"
        else:
            detail = f"Floor snap: shifted {self._floor_snap_shift * 1000:.1f} mm up"
        return self._step_end(context, "Floor Snap", detail)

    # -- Decimate --

    def _setup_decimate(self, context):
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        self._sub_items = get_selected_meshes()
        self._faces_before = count_faces(self._sub_items)
        props = context.scene.ai_optimizer
        self._step_start(
            context,
            "Decimate",
            {
                "decimate_ratio": props.decimate_ratio,
                "decimate_passes": props.decimate_passes,
                "protect_uv_seams": props.protect_uv_seams,
                "run_planar_prepass": props.run_planar_prepass,
                "planar_angle_deg": f"{math.degrees(props.planar_angle):.1f}",
                "bake_normal_map": props.bake_normal_map,
                "normal_map_resolution": props.normal_map_resolution,
                "auto_cage_extrusion": props.auto_cage_extrusion,
                "cage_extrusion_mm": props.cage_extrusion_mm,
                "faces_before": self._faces_before,
                "objects": len(self._sub_items),
            },
        )
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
        lines = [f"{self._faces_before:,} → {faces_after:,} faces ({reduction:.1f}%)"]

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
            lines.append(f"{baked} normal map(s) baked")

        return self._step_end(context, "Decimate", "\n".join(lines))

    # -- Clean Images --

    def _setup_clean_images(self, context):
        self._sub_items = [None]  # single item
        self._clean_detail = ""
        image_count = sum(
            1
            for img in bpy.data.images
            if img.type == "IMAGE" and img.has_data and img.name not in ("Render Result", "Viewer Node")
        )
        self._step_start(context, "Clean Images", {"images": image_count})
        return 1

    def _tick_clean_images(self, context, index):
        _removed, detail = clean_images_all(context, token=self._token)
        self._clean_detail = detail
        return detail

    def _teardown_clean_images(self, context):
        return self._step_end(context, "Clean Images", self._clean_detail)

    # -- Clean Unused --

    def _setup_clean_unused(self, context):
        self._sub_items = [None]
        self._unused_detail = ""
        self._step_start(
            context,
            "Clean Unused",
            {
                "images": len(bpy.data.images),
                "materials": len(bpy.data.materials),
                "meshes": len(bpy.data.meshes),
                "textures": len(bpy.data.textures),
            },
        )
        return 1

    def _tick_clean_unused(self, context, index):
        _removed, detail = clean_unused_all(context)
        self._unused_detail = detail
        return detail

    def _teardown_clean_unused(self, context):
        return self._step_end(context, "Clean Unused", self._unused_detail)

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
        self._step_start(
            context,
            "Resize Textures",
            {
                "max_texture_size": props.max_texture_size,
                "resize_mode": props.resize_mode,
                "candidates": len(self._sub_items),
            },
        )
        return len(self._sub_items)

    def _tick_resize_textures(self, context, index):
        props = context.scene.ai_optimizer
        img = self._sub_items[index]
        if resize_texture_single(img, props, context=context):
            self._resized_count += 1
        return img.name

    def _teardown_resize_textures(self, context):
        return self._step_end(
            context,
            "Resize Textures",
            f"Resized {self._resized_count} texture(s)",
        )

    # -- LOD Generation --

    def _setup_lod(self, context):
        self._sub_items = [None]
        self._lod_detail = ""
        props = context.scene.ai_optimizer
        self._step_start(
            context,
            "LOD Generation",
            {
                "lod_levels": props.lod_levels,
                "lod_ratios": props.lod_ratios,
                "lod_suffix_pattern": props.lod_suffix_pattern,
            },
        )
        return 1

    def _tick_lod(self, context, index):
        props = context.scene.ai_optimizer
        detail = generate_lods(context, props)
        self._lod_detail = detail
        return detail

    def _teardown_lod(self, context):
        return self._step_end(context, "LOD Generation", self._lod_detail)

    # -- Export Model --

    def _setup_export(self, context):
        self._sub_items = [None]
        self._export_detail = ""
        props = context.scene.ai_optimizer
        config = {
            "export_format": props.export_format,
            "output_filename": props.output_filename,
            "output_folder": props.output_folder or "(blend file dir)",
            "export_selected_only": props.export_selected_only,
        }
        if props.export_format == "GLB":
            config.update(
                {
                    "use_draco": props.use_draco,
                    "draco_level": props.draco_level,
                    "draco_position_quantization": props.draco_position_quantization,
                    "draco_normal_quantization": props.draco_normal_quantization,
                    "draco_texcoord_quantization": props.draco_texcoord_quantization,
                    "image_format": props.image_format,
                    "image_quality": props.image_quality,
                }
            )
        elif props.export_format == "FBX":
            config.update(
                {
                    "fbx_axis_preset": props.fbx_axis_preset,
                    "fbx_embed_textures": props.fbx_embed_textures,
                    "fbx_smoothing": props.fbx_smoothing,
                }
            )
        elif props.export_format == "OBJ":
            config.update(
                {
                    "obj_export_materials": props.obj_export_materials,
                    "obj_forward_axis": props.obj_forward_axis,
                    "obj_up_axis": props.obj_up_axis,
                }
            )
        self._step_start(context, "Export Model", config)
        return 1

    def _tick_export(self, context, index):
        props = context.scene.ai_optimizer
        detail = export_model(context, props)
        self._export_detail = detail
        return detail

    def _teardown_export(self, context):
        state = context.window_manager.ai_optimizer_pipeline
        detail = self._export_detail
        if "(" in detail and ")" in detail:
            state.export_size = detail[detail.rfind("(") + 1 : detail.rfind(")")]
        return self._step_end(context, "Export Model", detail)


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
        state.faces_before = 0
        state.faces_after = 0
        state.export_size = ""
        return {"FINISHED"}


class AIOPT_OT_open_debug_log(Operator):
    bl_idname = "ai_optimizer.open_debug_log"
    bl_label = "Open Debug Log"
    bl_description = "Save the current debug log to a temp file and open it in the OS default text editor"

    @classmethod
    def poll(cls, context):
        return not debug_buffer_is_empty()

    def execute(self, context):
        path = os.path.join(tempfile.gettempdir(), "ai_optimizer_debug.log")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(get_debug_log_text())
        except OSError as exc:
            self.report({"ERROR"}, f"Could not write debug log: {exc}")
            return {"CANCELLED"}

        try:
            if sys.platform.startswith("win"):
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except (OSError, FileNotFoundError) as exc:
            self.report(
                {"WARNING"},
                f"Log saved to {path} — open it manually ({exc})",
            )
            return {"FINISHED"}

        self.report({"INFO"}, f"Debug log opened: {path}")
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


class AIOPT_OT_analyze_mesh(Operator):
    bl_idname = "ai_optimizer.analyze_mesh"
    bl_label = "Run Analysis"
    bl_description = "Analyze mesh problems and generate optimization recommendations"
    bl_options = {"REGISTER"}

    _PRESET_TARGETS = {"MOBILE": 5000, "WEB": 25000, "DESKTOP": 75000}

    def execute(self, context):
        import random
        import statistics

        import bmesh

        props = context.scene.ai_optimizer
        state = context.window_manager.ai_optimizer_analysis
        meshes = get_selected_meshes()

        if not meshes:
            self.report({"ERROR"}, "No mesh objects found")
            return {"CANCELLED"}

        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        total_faces = 0
        non_manifold_edges = 0
        zero_edges = 0
        zero_faces = 0
        thin_faces = 0
        all_edge_lengths = []
        THIN_THRESHOLD = 0.0001  # m²

        for obj in meshes:
            bm = bmesh.new()
            try:
                bm.from_mesh(obj.data)
                bm.edges.ensure_lookup_table()
                bm.faces.ensure_lookup_table()

                total_faces += len(bm.faces)

                for edge in bm.edges:
                    length = edge.calc_length()
                    all_edge_lengths.append(length)
                    if not edge.is_manifold:
                        non_manifold_edges += 1
                    if length == 0.0:
                        zero_edges += 1

                for face in bm.faces:
                    area = face.calc_area()
                    if area == 0.0:
                        zero_faces += 1
                    elif area < THIN_THRESHOLD:
                        thin_faces += 1
            finally:
                bm.free()

        # Merge distance: median edge length x 0.1%
        # Sample up to 10,000 edges to stay fast on large meshes
        if all_edge_lengths:
            sample = random.sample(all_edge_lengths, min(len(all_edge_lengths), 10000))
            median_length = statistics.median(sample)
            recommended_merge = round(median_length * 0.001, 4)
            recommended_merge = max(recommended_merge, 0.0001)
        else:
            recommended_merge = 0.0001

        # Decimate ratio from target
        if props.analysis_target_preset == "CUSTOM":
            target = props.analysis_target_faces
        else:
            target = self._PRESET_TARGETS.get(props.analysis_target_preset, 25000)

        ratio = round(min(max(target / max(total_faces, 1), 0.01), 1.0), 3)
        thin_pct = (thin_faces + zero_faces) / max(total_faces, 1) * 100

        # Write results
        state.has_results = True
        state.total_faces = total_faces
        state.non_manifold_edges = non_manifold_edges
        state.zero_edges = zero_edges
        state.zero_faces = zero_faces
        state.thin_faces = thin_faces
        state.thin_face_pct = round(thin_pct, 1)
        state.intersecting_faces = 0
        state.intersecting_faces_available = False
        state.recommended_ratio = ratio
        state.recommended_merge_distance = recommended_merge

        for area in context.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()

        self.report({"INFO"}, f"Analysis complete — {total_faces:,} faces, recommended ratio: {ratio}")
        return {"FINISHED"}


class AIOPT_OT_remove_small_pieces(Operator):
    bl_idname = "ai_optimizer.remove_small_pieces"
    bl_label = "Remove Small Pieces"
    bl_description = "Delete disconnected mesh islands below face count or volume threshold"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.ai_optimizer
        meshes = get_selected_meshes()

        if not meshes:
            self.report({"ERROR"}, "No mesh objects found")
            return {"CANCELLED"}

        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        total_parts = 0
        total_faces = 0

        for obj in meshes:
            parts, faces = remove_small_pieces_single(context, obj, props)
            total_parts += parts
            total_faces += faces

        self.report({"INFO"}, f"Removed {total_parts:,} small piece(s), {total_faces:,} faces")
        return {"FINISHED"}


class AIOPT_OT_floor_snap(Operator):
    bl_idname = "ai_optimizer.floor_snap"
    bl_label = "Floor Snap"
    bl_description = "Translate selected meshes so the lowest vertex sits at Z=0. XY position is unchanged"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        meshes = get_selected_meshes()
        if not meshes:
            self.report({"ERROR"}, "No mesh objects found")
            return {"CANCELLED"}

        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        shift = floor_snap_all(meshes)
        if shift == 0.0:
            self.report({"INFO"}, "Floor snap: no shift needed")
        else:
            self.report({"INFO"}, f"Floor snap: shifted {shift * 1000:.1f} mm up")
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
