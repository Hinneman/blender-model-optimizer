import json
import os

import bpy
from bpy.types import Panel

from .utils import (
    count_faces,
    estimate_glb_size,
    get_config_path,
    get_selected_meshes,
    is_print3d_available,
)


class AIOPT_PT_main_panel(Panel):
    bl_label = "AI Model Optimizer"
    bl_idname = "AIOPT_PT_main_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AI Optimizer"

    def draw(self, context):
        layout = self.layout
        props = context.scene.ai_optimizer
        state = context.window_manager.ai_optimizer_pipeline

        # While the pipeline is running or showing results, hide everything
        # — the progress sub-panel handles all UI during that time.
        if state.is_running or state.step_results != "[]":
            return

        # Dependency status — always visible
        dep_row = layout.row()
        if is_print3d_available():
            dep_row.label(text="3D Print Toolbox available", icon="CHECKMARK")
        else:
            dep_row.label(text="3D Print Toolbox not installed — fallback active", icon="ERROR")

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

            est_bytes = estimate_glb_size(meshes, props)
            if est_bytes >= 1024 * 1024:
                est_label = f"~{est_bytes / (1024 * 1024):.1f} MB"
            else:
                est_label = f"~{est_bytes / 1024:.0f} KB"
            col.label(text=f"Est. Export Size: {est_label}")

            col.separator()
            col.operator("ai_optimizer.show_stats", icon="FILE_REFRESH")

            # --- Analysis ---
            col.separator()
            col.label(text="Mesh Analysis:", icon="VIEWZOOM")
            row = col.row(align=True)
            row.prop(props, "analysis_target_preset", text="")
            if props.analysis_target_preset == "CUSTOM":
                col.prop(props, "analysis_target_faces")
            col.operator("ai_optimizer.analyze_mesh", icon="VIEWZOOM")

            analysis = context.window_manager.ai_optimizer_analysis
            if analysis.has_results:
                col.separator()
                col.label(text="Issues found:", icon="INFO")

                def _issue_row(label, count):
                    row = col.row()
                    row.label(
                        text=f"  {label}: {count:,}",
                        icon="CHECKMARK" if count == 0 else "ERROR",
                    )

                _issue_row("Non-manifold edges", analysis.non_manifold_edges)
                _issue_row("Zero edges", analysis.zero_edges)
                _issue_row("Zero faces", analysis.zero_faces)
                _issue_row("Thin faces", analysis.thin_faces)

                col.separator()
                col.label(text="Recommendations:", icon="LIGHT")
                col.label(text=f"  Decimate ratio: {analysis.recommended_ratio:.3f}")
                if analysis.thin_face_pct > 5.0:
                    col.label(text=f"  ({analysis.thin_face_pct:.0f}% thin — can push lower)")
                col.label(text=f"  Merge distance: {analysis.recommended_merge_distance * 1000:.1f} mm")
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


class AIOPT_PT_progress_panel(Panel):
    bl_label = "Pipeline Progress"
    bl_idname = "AIOPT_PT_progress_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AI Optimizer"
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
        hint = layout.column(align=True)
        hint.scale_y = 0.8
        hint.label(text="Cancel takes effect at the next checkpoint.")
        hint.label(text="An in-flight Blender op (decimate, export,")
        hint.label(text="bake) will finish first.")

    def _draw_completed(self, layout, state, results):
        # Summary box
        if state.faces_before > 0:
            box = layout.box()
            col = box.column(align=True)
            col.label(text="Result", icon="CHECKMARK")
            reduction = (1 - state.faces_after / max(state.faces_before, 1)) * 100
            col.label(text=f"Faces: {state.faces_before:,} \u2192 {state.faces_after:,} ({reduction:.1f}%)")
            if state.export_size:
                col.label(text=f"Export: {state.export_size}")
            col.label(text=f"Time: {state.total_elapsed:.1f}s")

        layout.separator()

        # Step details
        col = layout.column(align=True)
        for r in results:
            row = col.row()
            row.label(
                text=f"{r['name']} ({r['duration']:.1f}s)",
                icon="CHECKMARK",
            )
            if r.get("detail"):
                for line in r["detail"].split("\n"):
                    line = line.strip()
                    if line:
                        row = col.row()
                        row.label(text=f"    {line}")

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
    bl_label = "Clean & Prepare Geometry"
    bl_idname = "AIOPT_PT_geometry_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AI Optimizer"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        state = context.window_manager.ai_optimizer_pipeline
        return not state.is_running and state.step_results == "[]"

    def draw_header(self, context):
        props = context.scene.ai_optimizer
        self.layout.prop(props, "run_fix_geometry", text="")

    def draw(self, context):
        layout = self.layout
        props = context.scene.ai_optimizer

        col = layout.column(align=True)
        col.prop(props, "merge_distance_mm")
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
        col = layout.column(align=True)
        col.label(text="Cleanup:", icon="MATERIAL")
        col.prop(props, "merge_materials")
        if props.merge_materials:
            col.prop(props, "merge_materials_threshold_pct")
        col.prop(props, "join_meshes")
        if props.join_meshes:
            col.prop(props, "join_mode", text="")

        layout.separator()
        layout.operator("ai_optimizer.fix_geometry", icon="MESH_DATA")


class AIOPT_PT_remove_interior_panel(Panel):
    bl_label = "Remove Interior"
    bl_idname = "AIOPT_PT_remove_interior_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AI Optimizer"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        state = context.window_manager.ai_optimizer_pipeline
        return not state.is_running and state.step_results == "[]"

    def draw_header(self, context):
        props = context.scene.ai_optimizer
        self.layout.prop(props, "run_remove_interior", text="")

    def draw(self, context):
        layout = self.layout
        props = context.scene.ai_optimizer

        col = layout.column(align=True)
        col.prop(props, "interior_method")

        # Dynamic help text based on selected method
        box = layout.box()
        help_col = box.column(align=True)
        help_col.scale_y = 0.8
        if props.interior_method == "LOOSE_PARTS":
            help_col.label(text="Removes disconnected mesh parts", icon="INFO")
            help_col.label(text="fully inside other geometry.")
            help_col.label(text="Fast, best for AI-generated models.")
        else:
            help_col.label(text="Casts rays from each face to detect", icon="INFO")
            help_col.label(text="occlusion. Slower but catches interior")
            help_col.label(text="faces within connected geometry.")

        layout.separator()
        layout.operator("ai_optimizer.remove_interior", icon="MESH_DATA")


class AIOPT_PT_small_pieces_panel(Panel):
    bl_label = "Remove Small Pieces"
    bl_idname = "AIOPT_PT_small_pieces_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AI Optimizer"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        state = context.window_manager.ai_optimizer_pipeline
        return not state.is_running and state.step_results == "[]"

    def draw_header(self, context):
        props = context.scene.ai_optimizer
        self.layout.prop(props, "run_remove_small_pieces", text="")

    def draw(self, context):
        layout = self.layout
        props = context.scene.ai_optimizer

        col = layout.column(align=True)
        col.prop(props, "small_pieces_face_threshold")
        col.prop(props, "small_pieces_size_threshold")

        box = layout.box()
        help_col = box.column(align=True)
        help_col.scale_y = 0.8
        help_col.label(text="Deletes loose parts smaller than", icon="INFO")
        help_col.label(text="either threshold. Targets floating")
        help_col.label(text="debris in AI-generated models.")

        layout.separator()
        layout.operator("ai_optimizer.remove_small_pieces", icon="MESH_DATA")


class AIOPT_PT_symmetry_panel(Panel):
    bl_label = "Symmetry Mirror (Experimental)"
    bl_idname = "AIOPT_PT_symmetry_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AI Optimizer"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        state = context.window_manager.ai_optimizer_pipeline
        return not state.is_running and state.step_results == "[]"

    def draw_header(self, context):
        props = context.scene.ai_optimizer
        self.layout.prop(props, "run_symmetry", text="")

    def draw(self, context):
        layout = self.layout
        props = context.scene.ai_optimizer

        col = layout.column(align=True)
        col.prop(props, "symmetry_axis")
        col.prop(props, "symmetry_threshold_mm")
        col.prop(props, "symmetry_min_score", slider=True)

        box = layout.box()
        help_col = box.column(align=True)
        help_col.scale_y = 0.8
        help_col.label(text="Experimental — results vary.", icon="ERROR")
        help_col.label(text="Works best on CAD or manually")
        help_col.label(text="modeled meshes with precise symmetry.")
        help_col.label(text="AI-generated models may produce artifacts.")

        layout.separator()
        layout.operator("ai_optimizer.symmetry_mirror", icon="MOD_MIRROR")


class AIOPT_PT_decimate_panel(Panel):
    bl_label = "Decimate"
    bl_idname = "AIOPT_PT_decimate_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AI Optimizer"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        state = context.window_manager.ai_optimizer_pipeline
        return not state.is_running and state.step_results == "[]"

    def draw_header(self, context):
        props = context.scene.ai_optimizer
        self.layout.prop(props, "run_decimate", text="")

    def draw(self, context):
        layout = self.layout
        props = context.scene.ai_optimizer

        col = layout.column(align=True)
        col.prop(props, "dissolve_angle", slider=True)
        col.prop(props, "decimate_ratio", slider=True)
        col.prop(props, "decimate_passes", slider=True)

        # Show preview of what this ratio means
        meshes = get_selected_meshes()
        if meshes:
            current = count_faces(meshes)
            estimated = int(current * props.decimate_ratio)
            col.label(text=f"Current: {current:,} faces")
            col.label(text=f"Estimated after: ~{estimated:,} faces")
            if props.decimate_passes > 1:
                per_pass = props.decimate_ratio ** (1.0 / props.decimate_passes)
                col.label(text=f"Per-pass ratio: {per_pass:.3f} \u00d7 {props.decimate_passes}")

        layout.separator()
        layout.prop(props, "protect_uv_seams")

        col = layout.column(align=True)
        col.prop(props, "run_planar_postpass")
        if props.run_planar_postpass:
            col.prop(props, "planar_angle", slider=True)

        layout.separator()
        col = layout.column(align=True)
        col.label(text="Normal Map Baking:", icon="IMAGE_DATA")
        col.prop(props, "bake_normal_map")
        if props.bake_normal_map:
            col.prop(props, "normal_map_resolution", text="")
            col.prop(props, "auto_cage_extrusion")
            if not props.auto_cage_extrusion:
                col.prop(props, "cage_extrusion_mm")
            box = layout.box()
            warn_col = box.column(align=True)
            warn_col.scale_y = 0.8
            warn_col.label(text="Requires Cycles render engine.", icon="INFO")
            warn_col.label(text="Best with aggressive decimation (ratio < 0.2).")

        layout.separator()
        layout.operator("ai_optimizer.decimate", icon="MOD_DECIM")


class AIOPT_PT_floor_snap_panel(Panel):
    bl_label = "Floor Snap"
    bl_idname = "AIOPT_PT_floor_snap_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AI Optimizer"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        state = context.window_manager.ai_optimizer_pipeline
        return not state.is_running and state.step_results == "[]"

    def draw_header(self, context):
        props = context.scene.ai_optimizer
        self.layout.prop(props, "run_floor_snap", text="")

    def draw(self, context):
        layout = self.layout

        box = layout.box()
        help_col = box.column(align=True)
        help_col.scale_y = 0.8
        help_col.label(text="Translates the model so the", icon="INFO")
        help_col.label(text="lowest vertex sits at Z=0.")
        help_col.label(text="XY position is unchanged.")

        layout.separator()
        layout.operator("ai_optimizer.floor_snap", icon="TRIA_DOWN_BAR")


class AIOPT_PT_textures_panel(Panel):
    bl_label = "Textures"
    bl_idname = "AIOPT_PT_textures_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AI Optimizer"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        state = context.window_manager.ai_optimizer_pipeline
        return not state.is_running and state.step_results == "[]"

    def draw(self, context):
        layout = self.layout
        props = context.scene.ai_optimizer

        # Pipeline toggles for the three texture steps
        row = layout.row(align=True)
        row.prop(props, "run_clean_images", toggle=True, text="Clean Images")
        row.prop(props, "run_clean_unused", toggle=True, text="Clean Unused")
        row.prop(props, "run_resize_textures", toggle=True, text="Resize")

        layout.separator()

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
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        state = context.window_manager.ai_optimizer_pipeline
        return not state.is_running and state.step_results == "[]"

    def draw_header(self, context):
        props = context.scene.ai_optimizer
        self.layout.prop(props, "run_export", text="")

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
            col.separator()
            col.label(text="Quantization (advanced):")
            col.prop(props, "draco_position_quantization", slider=True)
            col.prop(props, "draco_normal_quantization", slider=True)
            col.prop(props, "draco_texcoord_quantization", slider=True)

        layout.separator()

        col = layout.column(align=True)
        col.label(text="Image Format:", icon="IMAGE_DATA")
        col.prop(props, "image_format", text="")
        if props.image_format in ("JPEG", "WEBP"):
            col.prop(props, "image_quality", slider=True)

        layout.separator()
        col = layout.column(align=True)
        col.label(text="LOD Generation:", icon="MOD_DECIM")
        col.prop(props, "run_lod")
        if props.run_lod:
            col.prop(props, "lod_levels")
            col.prop(props, "lod_suffix_pattern")
            col.prop(props, "lod_ratios")

        layout.separator()
        layout.operator("ai_optimizer.export_glb", icon="EXPORT")


class AIOPT_PT_presets_panel(Panel):
    bl_label = "Presets"
    bl_idname = "AIOPT_PT_presets_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AI Optimizer"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        state = context.window_manager.ai_optimizer_pipeline
        return not state.is_running and state.step_results == "[]"

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
