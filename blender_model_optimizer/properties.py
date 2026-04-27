import os

from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    StringProperty,
)
from bpy.types import PropertyGroup

from .utils import _export_format_update, _tag_3d_redraw


class AIOPT_Properties(PropertyGroup):
    # -- Pipeline toggles --
    run_fix_geometry: BoolProperty(
        name="Fix Geometry", default=True, description="Fix non-manifold geometry, merge vertices, recalculate normals"
    )
    run_decimate: BoolProperty(name="Decimate", default=True, description="Reduce polygon count", update=_tag_3d_redraw)
    run_floor_snap: BoolProperty(
        name="Floor Snap",
        default=True,
        description="Translate the model so its lowest point sits at Z=0 (world floor). XY position is unchanged",
    )
    run_clean_images: BoolProperty(name="Clean Images", default=True, description="Remove duplicate images")
    run_clean_unused: BoolProperty(name="Clean Unused", default=True, description="Remove unused data blocks")
    run_resize_textures: BoolProperty(
        name="Resize Textures", default=True, description="Resize textures to max size", update=_tag_3d_redraw
    )
    run_export: BoolProperty(name="Export Model", default=True, description="Export the optimized model")

    # -- Geometry settings --
    merge_distance_mm: FloatProperty(
        name="Merge Distance (mm)",
        default=0.1,
        min=0.01,
        max=1000.0,
        precision=2,
        description="Merge vertices closer than this distance in millimeters",
    )
    recalculate_normals: BoolProperty(name="Recalculate Normals", default=True, description="Fix flipped normals")
    manifold_method: EnumProperty(
        name="Manifold Fix",
        items=[
            ("OFF", "Off", "Don't attempt manifold repair"),
            ("FILL_HOLES", "Fill Holes", "Fill holes with n-gons (up to 32 sides). Safe on thin-shell meshes"),
            (
                "PRINT3D",
                "3D Print Toolbox",
                "Aggressive non-manifold cleanup. Best for watertight solid meshes. "
                "Warning: deletes geometry around non-manifold edges \u2014 NOT suitable for "
                "thin shells, draped covers, cloth, or any single-layer surface",
            ),
        ],
        default="FILL_HOLES",
        description="Method used to repair non-manifold geometry",
    )

    # -- Material & Mesh cleanup --
    merge_materials: BoolProperty(
        name="Merge Materials",
        default=True,
        description="Merge materials with identical shader setups (same textures and values)",
    )
    merge_materials_threshold_pct: FloatProperty(
        name="Material Tolerance (%)",
        default=1.0,
        min=0.1,
        max=10.0,
        precision=1,
        description="Color/value tolerance when comparing materials (percent)",
    )
    join_meshes: BoolProperty(
        name="Join Meshes",
        default=True,
        description="Join separate mesh objects that share materials to reduce draw calls",
    )
    join_mode: EnumProperty(
        name="Join Mode",
        items=[
            ("BY_MATERIAL", "By Material", "Group objects by shared material, join each group"),
            ("ALL", "All", "Join all objects into a single mesh"),
        ],
        default="BY_MATERIAL",
        description="How to group objects when joining",
    )

    # -- Remove Interior settings --
    run_remove_interior: BoolProperty(
        name="Remove Interior", default=True, description="Remove hidden interior geometry"
    )
    interior_method: EnumProperty(
        name="Method",
        items=[
            (
                "LOOSE_PARTS",
                "Enclosed Parts",
                "Remove disconnected mesh parts fully inside other geometry. Fast, best for AI-generated models",
            ),
            (
                "RAY_CAST",
                "Ray Cast",
                "Cast rays from each face to detect occlusion."
                " Slower but catches interior faces within connected geometry",
            ),
        ],
        default="RAY_CAST",
        description="Method used to detect interior faces",
    )

    # -- Symmetry settings --
    run_symmetry: BoolProperty(
        name="Symmetry Mirror (Experimental)",
        default=False,
        description="Detect near-symmetric meshes and apply mirror optimization. Best on CAD meshes",
    )
    symmetry_axis: EnumProperty(
        name="Axis",
        items=[
            ("X", "X", "Mirror along X axis"),
            ("Y", "Y", "Mirror along Y axis"),
            ("Z", "Z", "Mirror along Z axis"),
        ],
        default="X",
        description="Axis to test symmetry along",
    )
    symmetry_threshold_mm: FloatProperty(
        name="Threshold (mm)",
        default=1.0,
        min=0.1,
        max=100.0,
        precision=1,
        description="Max distance between a vertex and its mirror to count as matched (mm)",
    )
    symmetry_min_score: FloatProperty(
        name="Min Score",
        default=0.85,
        min=0.5,
        max=1.0,
        step=1,
        precision=2,
        description="Minimum fraction of vertices that must have a mirror match",
        subtype="FACTOR",
    )

    # -- Decimate settings --
    decimate_ratio: FloatProperty(
        name="Ratio",
        default=0.1,
        min=0.01,
        max=1.0,
        step=1,
        precision=3,
        description="Decimation ratio. 0.5 = keep 50% of faces after dissolve",
        subtype="FACTOR",
        update=_tag_3d_redraw,
    )
    decimate_passes: IntProperty(
        name="Passes",
        default=1,
        min=1,
        max=5,
        description=(
            "Split decimation into N passes targeting the final ratio. "
            "Per-pass ratio is ratio ** (1/passes). Higher pass counts preserve "
            "detail better at low ratios but take proportionally longer"
        ),
        update=_tag_3d_redraw,
    )
    protect_uv_seams: BoolProperty(
        name="Protect UV Seams",
        default=True,
        description=(
            "Mark UV island boundaries via a vertex-group weight bias so the "
            "collapse solver is ~10x less likely to collapse across them. "
            "Near-free; keep on for most meshes. Disable only if you see "
            "over-tessellation clustering around seam vertices"
        ),
    )
    run_planar_prepass: BoolProperty(
        name="Planar Pre-Pass",
        default=True,
        description=(
            "Before collapse decimation, run a planar-dissolve pass that merges "
            "adjacent near-coplanar faces into n-gons. Dramatically reduces triangle count "
            "in flat regions (tops of cylinders, panels, ground planes) without changing "
            "curved surfaces, and frees COLLAPSE's budget for curved geometry. UV islands "
            "are preserved natively by the modifier. Disable if your mesh has subtle "
            "curvature that should not be flattened"
        ),
    )
    planar_angle: FloatProperty(
        name="Planar Angle",
        default=0.0872665,
        min=0.0,
        max=0.523599,
        step=1,
        precision=3,
        description=(
            "Max angle between adjacent faces for planar-dissolve to merge them. "
            "5 deg (default) is conservative; 10-15 deg reduces more faces but may "
            "flatten subtle curvature"
        ),
        subtype="ANGLE",
    )
    bake_normal_map: BoolProperty(
        name="Bake Normal Map",
        default=True,
        description="Bake high-poly detail into a normal map before decimating. Requires Cycles",
    )
    normal_map_resolution: EnumProperty(
        name="Normal Map Size",
        items=[
            ("512", "512px", ""),
            ("1024", "1024px", ""),
            ("2048", "2048px", ""),
        ],
        default="1024",
        description="Resolution of the baked normal map",
    )
    auto_cage_extrusion: BoolProperty(
        name="Auto Cage Distance",
        default=True,
        description=(
            "Automatically size the bake ray distance as 1% of the mesh bounding-box diagonal. "
            "Disable to set the distance manually"
        ),
    )
    cage_extrusion_mm: FloatProperty(
        name="Cage Extrusion (mm)",
        default=10.0,
        min=1.0,
        max=1000.0,
        precision=1,
        description="Ray distance for baking from high-poly to low-poly surface (mm)",
    )

    # -- Texture settings --
    max_texture_size: IntProperty(
        name="Max Size (px)",
        default=1024,
        min=64,
        max=8192,
        description="Maximum texture dimension in pixels",
        update=_tag_3d_redraw,
    )
    resize_mode: EnumProperty(
        name="Resize Mode",
        items=[
            ("DOWNSIZE", "Downsize Only", "Only shrink textures larger than max size"),
            ("ALL", "Resize All", "Resize all textures to exactly max size"),
        ],
        default="DOWNSIZE",
        description="How to handle texture resizing",
        update=_tag_3d_redraw,
    )

    # -- Export settings --
    export_format: EnumProperty(
        name="Format",
        items=[
            ("GLB", "GLB", "glTF binary — best for web and real-time"),
            ("FBX", "FBX", "Autodesk FBX — best for game engines (Unreal, Unity)"),
            ("OBJ", "OBJ", "Wavefront OBJ — DCC interchange (no animations, basic materials)"),
        ],
        default="GLB",
        description="Export file format",
        update=_export_format_update,
    )
    output_filename: StringProperty(name="Filename", default="optimized_model.glb", description="Output filename")
    output_folder: StringProperty(
        name="Folder",
        default=os.path.join(os.path.expanduser("~"), "Downloads"),
        subtype="DIR_PATH",
        description="Output folder",
    )
    export_selected_only: BoolProperty(name="Selected Only", default=True, description="Export only selected objects")
    use_draco: BoolProperty(
        name="Draco Compression",
        default=True,
        description="Use Draco mesh compression (recommended for web)",
        update=_tag_3d_redraw,
    )
    draco_level: IntProperty(
        name="Draco Level",
        default=6,
        min=0,
        max=10,
        description="Draco compression level (higher = smaller file, slower decode)",
        update=_tag_3d_redraw,
    )
    draco_position_quantization: IntProperty(
        name="Position Bits",
        default=14,
        min=8,
        max=16,
        description="Draco position quantization bits. Lower = smaller file, less precision",
        update=_tag_3d_redraw,
    )
    draco_normal_quantization: IntProperty(
        name="Normal Bits",
        default=10,
        min=8,
        max=16,
        description="Draco normal quantization bits",
        update=_tag_3d_redraw,
    )
    draco_texcoord_quantization: IntProperty(
        name="UV Bits",
        default=12,
        min=8,
        max=16,
        description="Draco texture coordinate quantization bits",
        update=_tag_3d_redraw,
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
        update=_tag_3d_redraw,
    )
    image_quality: IntProperty(
        name="Quality",
        default=85,
        min=1,
        max=100,
        description="Image quality for JPEG/WebP (80-90 recommended)",
        update=_tag_3d_redraw,
    )

    # -- FBX-specific --
    fbx_axis_preset: EnumProperty(
        name="Axis Preset",
        items=[
            (
                "UNREAL",
                "Unreal Engine",
                "FBX axes set so the model imports into Unreal Engine with correct orientation and 1 unit = 1 cm",
            ),
            (
                "UNITY",
                "Unity",
                "FBX axes set so the model imports into Unity with correct orientation and 1 unit = 1 m",
            ),
            (
                "DEFAULT",
                "Blender Default",
                "Blender's FBX exporter defaults. Use if you know the receiving tool's axis convention "
                "and want to match it manually",
            ),
        ],
        default="UNREAL",
        description="Pre-configured axis and scale settings for common target engines",
    )
    fbx_embed_textures: BoolProperty(
        name="Embed Textures",
        default=True,
        description=(
            "Pack textures into the FBX so it can be moved to another machine without losing material maps. "
            "Increases file size"
        ),
        # update redraws the size estimate when an FBX-aware estimator is added (see Task 6 of the plan)
        update=_tag_3d_redraw,
    )
    fbx_smoothing: EnumProperty(
        name="Smoothing",
        items=[
            ("OFF", "Off", "No smoothing groups"),
            ("FACE", "Face", "Per-face smoothing groups (recommended)"),
            ("EDGE", "Edge", "Per-edge smoothing"),
        ],
        default="FACE",
        description="Smoothing data written to the FBX",
    )

    # -- OBJ-specific --
    obj_export_materials: BoolProperty(
        name="Export Materials",
        default=True,
        description="Write a companion .mtl file referencing the materials",
    )
    obj_forward_axis: EnumProperty(
        name="Forward",
        items=[
            ("X", "X", ""),
            ("Y", "Y", ""),
            ("Z", "Z", ""),
            ("NEGATIVE_X", "-X", ""),
            ("NEGATIVE_Y", "-Y", ""),
            ("NEGATIVE_Z", "-Z", ""),
        ],
        default="NEGATIVE_Z",
        description="Forward axis written to the OBJ",
    )
    obj_up_axis: EnumProperty(
        name="Up",
        items=[
            ("X", "X", ""),
            ("Y", "Y", ""),
            ("Z", "Z", ""),
            ("NEGATIVE_X", "-X", ""),
            ("NEGATIVE_Y", "-Y", ""),
            ("NEGATIVE_Z", "-Z", ""),
        ],
        default="Y",
        description="Up axis written to the OBJ",
    )

    # -- LOD settings --
    run_lod: BoolProperty(
        name="LOD Generation",
        default=False,
        description="Generate multiple LOD levels as separate GLB files",
    )
    lod_levels: IntProperty(
        name="LOD Levels",
        default=3,
        min=2,
        max=5,
        description="Number of LOD levels (including full detail as LOD0)",
    )
    lod_suffix_pattern: StringProperty(
        name="Suffix Pattern",
        default="_LOD{n}",
        description="Filename suffix pattern. {n} is replaced with the LOD level number",
    )
    lod_ratios: StringProperty(
        name="LOD Ratios",
        default="1.0, 0.5, 0.25",
        description="Comma-separated decimate ratios per LOD level (LOD0 should be 1.0)",
    )

    # -- Analysis settings --
    analysis_target_preset: EnumProperty(
        name="Target",
        items=[
            ("MOBILE", "Mobile", "Target ~5,000 faces for mobile"),
            ("WEB", "Web", "Target ~25,000 faces for web/desktop"),
            ("DESKTOP", "Desktop", "Target ~75,000 faces for high-end"),
            ("CUSTOM", "Custom", "Enter a specific face count"),
        ],
        default="WEB",
        description="Target face count for decimate ratio recommendation",
    )
    analysis_target_faces: IntProperty(
        name="Target Faces",
        default=25000,
        min=100,
        max=10000000,
        description="Custom target face count for decimate recommendation",
    )

    # -- Small Pieces settings --
    run_remove_small_pieces: BoolProperty(
        name="Remove Small Pieces",
        default=True,
        description="Delete disconnected mesh islands below size threshold",
    )
    small_pieces_face_threshold: IntProperty(
        name="Min Faces",
        default=50,
        min=1,
        max=10000,
        description="Delete loose parts with fewer than this many faces",
    )
    small_pieces_size_threshold: FloatProperty(
        name="Min Size (cm)",
        default=1.0,
        min=0.0,
        max=50.0,
        precision=1,
        description="Delete loose parts smaller than this cube edge length in centimeters",
    )

    # -- Diagnostics --
    verbose_logging: BoolProperty(
        name="Verbose Logging",
        default=False,
        description=(
            "Print detailed per-step settings and checkpoints. Logs appear in "
            "Blender's system console (Window > Toggle System Console on Windows; "
            "launching terminal on macOS/Linux) and can be opened with the "
            "Open Debug Log button below"
        ),
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
    faces_before: IntProperty(default=0)
    faces_after: IntProperty(default=0)
    export_size: StringProperty(default="")


class AIOPT_AnalysisState(PropertyGroup):
    """Analysis results from the last Run Analysis call. Stored on WindowManager."""

    has_results: BoolProperty(default=False)
    total_faces: IntProperty(default=0)
    non_manifold_edges: IntProperty(default=0)
    zero_edges: IntProperty(default=0)
    zero_faces: IntProperty(default=0)
    thin_faces: IntProperty(default=0)
    thin_face_pct: FloatProperty(default=0.0)
    intersecting_faces: IntProperty(default=0)
    intersecting_faces_available: BoolProperty(default=False)
    recommended_ratio: FloatProperty(default=0.5)
    recommended_merge_distance: FloatProperty(default=0.0001)
