import os

from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    StringProperty,
)
from bpy.types import PropertyGroup

from .utils import _tag_3d_redraw


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
    run_uv_dilate: BoolProperty(
        name="Dilate UV Gutters",
        default=True,
        description=(
            "Bleed each UV island's edge colors outward into the surrounding texture gutter. "
            "Prevents black smears on fragmented-UV meshes where post-decimate UV drift would "
            "otherwise sample unpainted pixels between islands"
        ),
    )
    run_export: BoolProperty(name="Export GLB", default=True, description="Export optimized GLB")

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
    fix_manifold: BoolProperty(
        name="Fix Manifold", default=True, description="Attempt to fix non-manifold (holes, open edges)"
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
    dissolve_angle: FloatProperty(
        name="Dissolve Angle",
        default=0.2618,
        min=0.0,
        max=0.785398,
        step=1,
        precision=3,
        description="Dissolve faces within this angle (radians). Cleans flat surfaces before decimation. 0 = skip",
        subtype="ANGLE",
    )
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
        default=False,
        description=(
            "Mark UV island boundaries as Sharp edges before decimation so the collapse "
            "solver won't collapse across them. Recommended for CAD-style meshes with "
            "clean UV layouts (few, meaningful islands). Disable for AI-generated meshes "
            "whose fragmented UVs create fan artifacts in flat regions"
        ),
    )
    run_planar_postpass: BoolProperty(
        name="Planar Post-Pass",
        default=True,
        description=(
            "After collapse decimation, run a second planar-dissolve pass that merges "
            "adjacent near-coplanar faces into n-gons. Dramatically reduces triangle count "
            "in flat regions (tops of cylinders, panels, ground planes) without changing "
            "curved surfaces. UV islands are preserved natively by the modifier. Disable "
            "if your mesh has subtle curvature that should not be flattened"
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
    uv_dilate_pixels: IntProperty(
        name="Dilate Pixels",
        default=8,
        min=1,
        max=64,
        description=(
            "Number of pixels to bleed UV-island edge colors outward into the gutter. "
            "Higher values cover larger UV drift at the cost of more processing time. "
            "8 is a good default for most meshes; raise to 16+ if you still see black smears"
        ),
    )

    # -- Export settings --
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
