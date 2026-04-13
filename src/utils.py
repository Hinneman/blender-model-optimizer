import json
import math
import os

import bpy


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


def _tag_3d_redraw(self, context):
    """Force 3D viewport sidebar to redraw (used by properties that affect the size estimate)."""
    for area in context.screen.areas:
        if area.type == "VIEW_3D":
            area.tag_redraw()


def estimate_glb_size(meshes, props):
    """Estimate GLB export file size in bytes based on scene data and settings."""
    # -- Geometry --
    geo_bytes = 0
    for obj in meshes:
        mesh = obj.data
        verts = len(mesh.vertices)
        faces = len(mesh.polygons)
        # position (12B) + normal (12B) + UV (8B) per vertex + index (12B per tri)
        geo_bytes += verts * 32 + faces * 3 * 4

    # Apply decimate ratio if the decimate step is enabled
    if props.run_decimate:
        geo_bytes *= props.decimate_ratio

    # Symmetry mirror — assume ~40% geometry reduction if enabled
    if props.run_symmetry:
        geo_bytes *= 0.6

    if props.use_draco:
        # Draco compression factor: ~2x at level 0, ~6x at level 10
        draco_factor = 2.0 + (props.draco_level / 10.0) * 4.0
        geo_bytes /= draco_factor

    # -- Textures --
    tex_bytes = 0
    images = [
        i
        for i in bpy.data.images
        if i.type == "IMAGE" and i.name not in ("Render Result", "Viewer Node") and get_image_users(i) > 0
    ]
    for img in images:
        w, h = img.size[0], img.size[1]
        if w == 0 or h == 0:
            continue

        # Cap to max_texture_size if resize step is enabled
        if props.run_resize_textures:
            max_s = props.max_texture_size
            if props.resize_mode == "ALL":
                w, h = max_s, max_s
            else:  # DOWNSIZE
                if w > max_s or h > max_s:
                    scale = max_s / max(w, h)
                    w = max(1, 2 ** round(math.log2(max(1, int(w * scale)))))
                    h = max(1, 2 ** round(math.log2(max(1, int(h * scale)))))

        raw = w * h * 4  # RGBA
        fmt = props.image_format
        if fmt == "WEBP":
            # quality=100 → ratio ~8 (light compression), quality=1 → ratio ~40 (heavy)
            ratio = 8.0 + (1.0 - props.image_quality / 100.0) * 32.0
            tex_bytes += raw / ratio
        elif fmt == "JPEG":
            # quality=100 → ratio ~5 (light compression), quality=1 → ratio ~30 (heavy)
            ratio = 5.0 + (1.0 - props.image_quality / 100.0) * 25.0
            tex_bytes += raw / ratio
        else:  # NONE (PNG)
            tex_bytes += raw / 5.0

    # Normal map bake adds a texture
    if props.bake_normal_map:
        nmap_res = int(props.normal_map_resolution)
        # Normal maps compress well (~4:1 from raw RGB)
        tex_bytes += nmap_res * nmap_res * 3 / 4.0

    # -- Overhead (GLB container, materials, scene graph) --
    overhead = 10 * 1024

    return geo_bytes + tex_bytes + overhead


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
    "merge_materials",
    "merge_materials_threshold",
    "join_meshes",
    "join_mode",
    "run_remove_interior",
    "interior_method",
    "dissolve_angle",
    "decimate_ratio",
    "bake_normal_map",
    "normal_map_resolution",
    "normal_map_cage_extrusion",
    "max_texture_size",
    "resize_mode",
    "output_filename",
    "output_folder",
    "export_selected_only",
    "use_draco",
    "draco_level",
    "draco_position_quantization",
    "draco_normal_quantization",
    "draco_texcoord_quantization",
    "image_format",
    "image_quality",
    "run_lod",
    "lod_levels",
    "lod_suffix_pattern",
    "lod_ratios",

    "run_symmetry",
    "symmetry_axis",
    "symmetry_threshold",
    "symmetry_min_score",
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

    # Blender's exporter uses "AUTO" to keep original formats (PNG→PNG, JPEG→JPEG).
    # Our "NONE" enum value means "keep as PNG / no conversion", so we map it to "AUTO".
    # Blender's own "NONE" means "export no images at all", which is the bug we're fixing.
    blender_image_format = "AUTO" if props.image_format == "NONE" else props.image_format

    export_settings = {
        "filepath": output_path,
        "export_format": "GLB",
        "use_selection": props.export_selected_only,
        "export_apply": True,
        "export_yup": True,
        "export_draco_mesh_compression_enable": props.use_draco,
        "export_draco_mesh_compression_level": props.draco_level,
        "export_draco_position_quantization": props.draco_position_quantization,
        "export_draco_normal_quantization": props.draco_normal_quantization,
        "export_draco_texcoord_quantization": props.draco_texcoord_quantization,
        "export_draco_color_quantization": 10,
        "export_image_format": blender_image_format,
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


def generate_lods(context, props):
    """Generate multiple LOD levels as separate GLB files.

    LOD0 is the current (already-optimized) state exported by the normal
    export step.  This function exports LOD1, LOD2, … with progressively
    more aggressive decimation.  Returns a detail string.
    """
    # ------------------------------------------------------------------
    # 1. Parse ratios
    # ------------------------------------------------------------------
    try:
        ratios = [float(r.strip()) for r in props.lod_ratios.split(",") if r.strip()]
    except ValueError:
        return "LOD generation failed: invalid ratios string"

    if len(ratios) < 2:
        return "LOD generation failed: need at least 2 ratios (LOD0 + one extra)"

    # ------------------------------------------------------------------
    # 2. Output directory (same logic as export_glb_all)
    # ------------------------------------------------------------------
    if props.output_folder:
        output_dir = props.output_folder
    elif bpy.data.filepath:
        output_dir = os.path.dirname(bpy.data.filepath)
    else:
        output_dir = os.path.expanduser("~")

    # ------------------------------------------------------------------
    # 3. Base name
    # ------------------------------------------------------------------
    base_name = os.path.splitext(props.output_filename)[0]

    # ------------------------------------------------------------------
    # 4. Generate each LOD beyond LOD0
    # ------------------------------------------------------------------
    exported = []
    meshes = get_selected_meshes()

    for i, ratio in enumerate(ratios):
        if i == 0:
            continue  # LOD0 is the normal export
        if ratio >= 1.0:
            continue  # nothing to reduce

        # Add non-destructive decimate modifiers (export_apply will apply them)
        mod_name = f"_AIOPT_LOD{i}"
        for obj in meshes:
            mod = obj.modifiers.new(name=mod_name, type="DECIMATE")
            mod.decimate_type = "COLLAPSE"
            mod.ratio = ratio
            mod.use_collapse_triangulate = True

        # Build filename using the suffix pattern
        suffix = props.lod_suffix_pattern.replace("{n}", str(i))
        filename = f"{base_name}{suffix}.glb"
        output_path = os.path.join(output_dir, filename)

        # Select meshes for export
        bpy.ops.object.select_all(action="DESELECT")
        for obj in meshes:
            obj.select_set(True)
        if meshes:
            context.view_layer.objects.active = meshes[0]

        # Export — export_apply=True applies modifiers non-destructively during export
        blender_image_format = "AUTO" if props.image_format == "NONE" else props.image_format

        export_settings = {
            "filepath": output_path,
            "export_format": "GLB",
            "use_selection": True,
            "export_apply": True,
            "export_yup": True,
            "export_draco_mesh_compression_enable": props.use_draco,
            "export_draco_mesh_compression_level": props.draco_level,
            "export_draco_position_quantization": props.draco_position_quantization,
            "export_draco_normal_quantization": props.draco_normal_quantization,
            "export_draco_texcoord_quantization": props.draco_texcoord_quantization,
            "export_draco_color_quantization": 10,
            "export_image_format": blender_image_format,
        }

        if props.image_format in ("JPEG", "WEBP"):
            export_settings["export_image_quality"] = props.image_quality

        try:
            bpy.ops.export_scene.gltf(**export_settings)
        except TypeError:
            bpy.ops.export_scene.gltf(
                filepath=output_path,
                export_format="GLB",
                export_apply=True,
                export_draco_mesh_compression_enable=props.use_draco,
            )

        # Record result
        if os.path.exists(output_path):
            size_mb = os.path.getsize(output_path) / (1024 * 1024)
            exported.append(f"LOD{i}: {filename} ({size_mb:.1f} MB)")
        else:
            print(f"  [AI Optimizer] LOD{i} export failed — file not found: {output_path}")

        # Remove the modifiers (non-destructive — mesh data unchanged)
        for obj in meshes:
            mod = obj.modifiers.get(mod_name)
            if mod:
                obj.modifiers.remove(mod)

    if not exported:
        return "LOD generation: no extra LOD levels were exported"

    return f"Generated {len(exported)} LOD(s): {'; '.join(exported)}"
