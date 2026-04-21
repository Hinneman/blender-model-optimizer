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
    "author": "René Voigt, Claude",
    "version": (0, 0, 0),  # Placeholder — build.py injects from pyproject.toml
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > AI Optimizer",
    "description": "Optimize AI-generated 3D models: fix geometry, decimate, clean textures, export compressed GLB",
    "category": "Mesh",
}

import bpy

from .operators import (
    AIOPT_OT_analyze_mesh,
    AIOPT_OT_cancel_pipeline,
    AIOPT_OT_clean_images,
    AIOPT_OT_clean_unused,
    AIOPT_OT_decimate,
    AIOPT_OT_dismiss_pipeline,
    AIOPT_OT_export_glb,
    AIOPT_OT_fix_geometry,
    AIOPT_OT_floor_snap,
    AIOPT_OT_load_defaults,
    AIOPT_OT_open_debug_log,
    AIOPT_OT_remove_interior,
    AIOPT_OT_remove_small_pieces,
    AIOPT_OT_reset_defaults,
    AIOPT_OT_resize_textures,
    AIOPT_OT_run_all,
    AIOPT_OT_save_defaults,
    AIOPT_OT_show_stats,
    AIOPT_OT_symmetry_mirror,
)
from .panels import (
    AIOPT_PT_decimate_panel,
    AIOPT_PT_export_panel,
    AIOPT_PT_floor_snap_panel,
    AIOPT_PT_geometry_panel,
    AIOPT_PT_main_panel,
    AIOPT_PT_presets_panel,
    AIOPT_PT_progress_panel,
    AIOPT_PT_remove_interior_panel,
    AIOPT_PT_small_pieces_panel,
    AIOPT_PT_symmetry_panel,
    AIOPT_PT_textures_panel,
)
from .properties import AIOPT_AnalysisState, AIOPT_PipelineState, AIOPT_Properties
from .utils import load_defaults

classes = (
    AIOPT_Properties,
    AIOPT_PipelineState,
    AIOPT_AnalysisState,
    AIOPT_OT_fix_geometry,
    AIOPT_OT_remove_interior,
    AIOPT_OT_remove_small_pieces,
    AIOPT_OT_symmetry_mirror,
    AIOPT_OT_decimate,
    AIOPT_OT_floor_snap,
    AIOPT_OT_clean_images,
    AIOPT_OT_clean_unused,
    AIOPT_OT_resize_textures,
    AIOPT_OT_export_glb,
    AIOPT_OT_run_all,
    AIOPT_OT_cancel_pipeline,
    AIOPT_OT_dismiss_pipeline,
    AIOPT_OT_show_stats,
    AIOPT_OT_analyze_mesh,
    AIOPT_OT_save_defaults,
    AIOPT_OT_load_defaults,
    AIOPT_OT_reset_defaults,
    AIOPT_OT_open_debug_log,
    AIOPT_PT_main_panel,
    AIOPT_PT_progress_panel,
    AIOPT_PT_geometry_panel,
    AIOPT_PT_remove_interior_panel,
    AIOPT_PT_small_pieces_panel,
    AIOPT_PT_symmetry_panel,
    AIOPT_PT_decimate_panel,
    AIOPT_PT_floor_snap_panel,
    AIOPT_PT_textures_panel,
    AIOPT_PT_export_panel,
    AIOPT_PT_presets_panel,
)


@bpy.app.handlers.persistent
def _load_defaults_on_file(dummy):
    """Auto-load saved defaults when a new file is opened or Blender starts.

    Must be @persistent — without it, Blender clears the handler the first time
    a .blend file loads, so defaults only load once (or not at all).
    bpy.context.scene is unreliable inside load_post, so iterate bpy.data.scenes.
    """
    loaded_any = False
    for scene in bpy.data.scenes:
        props = getattr(scene, "ai_optimizer", None)
        if props is not None and load_defaults(props):
            loaded_any = True
    if loaded_any:
        print("[AI Model Optimizer] Loaded saved defaults")


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ai_optimizer = bpy.props.PointerProperty(type=AIOPT_Properties)
    bpy.types.WindowManager.ai_optimizer_pipeline = bpy.props.PointerProperty(type=AIOPT_PipelineState)
    bpy.types.WindowManager.ai_optimizer_analysis = bpy.props.PointerProperty(type=AIOPT_AnalysisState)

    # Auto-load defaults when opening files or on fresh startup
    if _load_defaults_on_file not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_load_defaults_on_file)
    if _load_defaults_on_file not in bpy.app.handlers.load_factory_startup_post:
        bpy.app.handlers.load_factory_startup_post.append(_load_defaults_on_file)

    # Load defaults now for the current session
    if hasattr(bpy.context, "scene") and bpy.context.scene is not None:
        load_defaults(bpy.context.scene.ai_optimizer)

    print("[AI Model Optimizer] Add-on registered")


def unregister():
    # Remove handlers
    if _load_defaults_on_file in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_defaults_on_file)
    if _load_defaults_on_file in bpy.app.handlers.load_factory_startup_post:
        bpy.app.handlers.load_factory_startup_post.remove(_load_defaults_on_file)

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.WindowManager.ai_optimizer_pipeline
    del bpy.types.WindowManager.ai_optimizer_analysis
    del bpy.types.Scene.ai_optimizer
    print("[AI Model Optimizer] Add-on unregistered")


if __name__ == "__main__":
    register()
