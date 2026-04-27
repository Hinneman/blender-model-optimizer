import collections
import json
import math
import os

import bpy

_DEBUG_BUFFER_MAX = 2000
_DEBUG_BUFFER = collections.deque(maxlen=_DEBUG_BUFFER_MAX)


class PipelineCancelled(Exception):
    """Raised by CancelToken.check() when the user has cancelled the pipeline.

    The modal operator catches this at the tick boundary and routes to the
    normal cancel path. Step functions should let it propagate — do not
    catch it with a bare ``except Exception``.
    """


class CancelToken:
    """Cooperative cancellation flag threaded through pipeline step functions.

    The modal operator owns one token per run and sets ``cancelled`` to True
    when the user clicks Cancel or presses ESC. Step functions call
    ``check()`` at loop boundaries; when the flag is set it raises
    ``PipelineCancelled`` which the modal catches.

    Single-step operators (``AIOPT_OT_fix_geometry`` etc.) pass no token,
    and step functions treat ``token=None`` as a no-op — they behave
    exactly as before.
    """

    __slots__ = ("cancelled",)

    def __init__(self):
        self.cancelled = False

    def check(self):
        if self.cancelled:
            raise PipelineCancelled()


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
    """Print a message to the Blender system console and append it to the debug buffer.

    INFO lines always print. DEBUG lines print only when the user has enabled
    ``verbose_logging`` on ``context.scene.ai_optimizer``. Both levels are
    appended to a bounded module-level ring buffer so the Open Debug Log
    operator can dump the full history to a file. Safe against missing
    context or property group: a missing attribute suppresses DEBUG but never
    raises.
    """
    if level == "DEBUG":
        scene = getattr(context, "scene", None) if context is not None else None
        props = getattr(scene, "ai_optimizer", None) if scene is not None else None
        if not getattr(props, "verbose_logging", False):
            return
        formatted = f"  [AI Optimizer][DEBUG] {message}"
    else:
        formatted = f"  [AI Optimizer] {message}"
    print(formatted)
    _DEBUG_BUFFER.append(formatted)


def get_debug_log_text():
    """Return the full buffered log as a single newline-joined string.

    Called by ``AIOPT_OT_open_debug_log`` to materialize the buffer to a file.
    Returns an empty string when the buffer is empty.
    """
    return "\n".join(_DEBUG_BUFFER)


def debug_buffer_is_empty():
    """Cheap predicate for ``AIOPT_OT_open_debug_log.poll()``."""
    return len(_DEBUG_BUFFER) == 0


def get_config_path():
    """Get the path to the saved defaults JSON file."""
    config_dir = bpy.utils.user_resource("CONFIG", path="ai_optimizer")
    os.makedirs(config_dir, exist_ok=True)
    return os.path.join(config_dir, "defaults.json")


def is_print3d_available():
    """Check if the 3D Print Toolbox add-on is installed and enabled."""
    # hasattr(bpy.ops.mesh, ...) is unreliable: bpy.ops uses dynamic attribute
    # lookup and returns a wrapper for any name, so it always reports True.
    # The operator only raises RuntimeError at call time if the add-on is
    # disabled. Ask addon_utils directly instead, covering both the legacy
    # bundled module name and the 4.2+ Extensions repo path.
    import addon_utils

    candidates = ("object_print3d_utils", "bl_ext.blender_org.print3d_toolbox")
    return any(addon_utils.check(name)[1] for name in candidates)


def _tag_3d_redraw(self, context):
    """Force 3D viewport sidebar to redraw (used by properties that affect the size estimate)."""
    for area in context.screen.areas:
        if area.type == "VIEW_3D":
            area.tag_redraw()


_KNOWN_EXPORT_EXTENSIONS = {".glb", ".fbx", ".obj"}
_FORMAT_EXTENSIONS = {"GLB": ".glb", "FBX": ".fbx", "OBJ": ".obj"}


def swap_export_extension(filename: str, fmt: str) -> str:
    """Replace a known export extension on ``filename`` with the one for ``fmt``.

    If ``filename`` ends with .glb / .fbx / .obj (case-insensitive), the
    suffix is stripped before the new extension is appended. Otherwise the
    new extension is just appended — this preserves user-typed names like
    ``model.unknown`` without silently mangling them.
    """
    new_ext = _FORMAT_EXTENSIONS[fmt]
    base, dot, ext = filename.rpartition(".")
    if dot and ("." + ext.lower()) in _KNOWN_EXPORT_EXTENSIONS:
        return base + new_ext
    return filename + new_ext


def _export_format_update(self, context):
    """EnumProperty update callback: swap the output filename's extension."""
    self.output_filename = swap_export_extension(self.output_filename, self.export_format)
    _tag_3d_redraw(self, context)


def estimate_export_size(meshes, props):
    """Estimate the export file size in bytes for the active export format.

    Returns a rough estimate — accurate to within ~10% for GLB on calibrated
    inputs and rougher (~30%) for FBX/OBJ. The number is used in the sidebar
    "Est. Export Size" label, not for any export-time decision.
    """
    fmt = props.export_format
    if fmt == "GLB":
        return _estimate_glb(meshes, props)
    if fmt == "FBX":
        return _estimate_fbx(meshes, props)
    if fmt == "OBJ":
        return _estimate_obj(meshes, props)
    return _estimate_glb(meshes, props)  # defensive fallback


def _estimate_fbx(meshes, props):
    """Estimate FBX binary size: uncompressed mesh data + optional embedded textures."""
    overhead = 2 * 1024

    geo_bytes = 0
    for obj in meshes:
        mesh = obj.data
        verts = len(mesh.vertices)
        faces = len(mesh.polygons)
        geo_bytes += verts * 32 + faces * 12

    if props.run_decimate:
        geo_bytes *= props.decimate_ratio
    if props.run_symmetry:
        geo_bytes *= 0.6

    geo_bytes *= 1.25  # FBX binary overhead

    tex_bytes = 0
    if props.fbx_embed_textures:
        images = [
            i
            for i in bpy.data.images
            if i.type == "IMAGE" and i.name not in ("Render Result", "Viewer Node") and get_image_users(i) > 0
        ]
        for img in images:
            w, h = img.size[0], img.size[1]
            if w == 0 or h == 0:
                continue
            if props.run_resize_textures:
                max_s = props.max_texture_size
                if props.resize_mode == "ALL":
                    w, h = max_s, max_s
                elif w > max_s or h > max_s:
                    scale = max_s / max(w, h)
                    w = max(1, 2 ** round(math.log2(max(1, int(w * scale)))))
                    h = max(1, 2 ** round(math.log2(max(1, int(h * scale)))))
            tex_bytes += w * h * 4 * 0.3

        if props.bake_normal_map:
            nmap_res = int(props.normal_map_resolution)
            tex_bytes += nmap_res * nmap_res * 3 * 0.3

    return geo_bytes + tex_bytes + overhead


def _estimate_obj(meshes, props):
    """Estimate OBJ ASCII size. Textures are never embedded in OBJ."""
    overhead = 1 * 1024

    geo_bytes = 0
    for obj in meshes:
        mesh = obj.data
        verts = len(mesh.vertices)
        faces = len(mesh.polygons)
        geo_bytes += verts * (32 + 32 + 22) + faces * 22

    if props.run_decimate:
        geo_bytes *= props.decimate_ratio
    if props.run_symmetry:
        geo_bytes *= 0.6

    return geo_bytes + overhead


def _estimate_glb(meshes, props):
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
        # Draco compression factor: ~6x at level 0, ~30x at level 10
        draco_factor = 6.0 + (props.draco_level / 10.0) * 24.0
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
        # Compression ratios calibrated from real-world exports of baked AI-mesh
        # textures (noisy diffuse + normal maps). Earlier values (WebP=15 at
        # q=100) were best-case estimates from flat color content and produced
        # 2x under-estimates on typical AI output. Current curve: q=85 yields
        # ~13x for WebP and ~9x for JPEG, which matches measured output within
        # ~10% on several test meshes.
        if fmt == "WEBP":
            # quality=100 → ratio ~8, quality=1 → ratio ~60
            ratio = 8.0 + (1.0 - props.image_quality / 100.0) * 52.0
            tex_bytes += raw / ratio
        elif fmt == "JPEG":
            # quality=100 → ratio ~5, quality=1 → ratio ~35
            ratio = 5.0 + (1.0 - props.image_quality / 100.0) * 30.0
            tex_bytes += raw / ratio
        else:  # NONE (PNG)
            tex_bytes += raw / 3.0

    # Normal map bake adds a texture (compressed with same image format).
    # Normal maps are high-frequency by nature so they compress worse than
    # diffuse — use the same calibrated ratios, applied to the per-pixel RGB
    # size (3 channels, not 4: alpha is dropped).
    if props.bake_normal_map:
        nmap_res = int(props.normal_map_resolution)
        nmap_raw = nmap_res * nmap_res * 3
        fmt = props.image_format
        if fmt == "WEBP":
            nmap_ratio = 8.0 + (1.0 - props.image_quality / 100.0) * 52.0
        elif fmt == "JPEG":
            nmap_ratio = 5.0 + (1.0 - props.image_quality / 100.0) * 30.0
        else:
            nmap_ratio = 3.0
        tex_bytes += nmap_raw / nmap_ratio

    # -- Overhead (GLB container, materials, scene graph) --
    overhead = 10 * 1024

    return geo_bytes + tex_bytes + overhead


# Properties to save/load (must match AIOPT_Properties attribute names)
SAVEABLE_PROPS = [
    "run_fix_geometry",
    "run_decimate",
    "run_floor_snap",
    "run_clean_images",
    "run_clean_unused",
    "run_resize_textures",
    "run_export",
    "merge_distance_mm",
    "recalculate_normals",
    "manifold_method",
    "merge_materials",
    "merge_materials_threshold_pct",
    "join_meshes",
    "join_mode",
    "run_remove_interior",
    "interior_method",
    "decimate_ratio",
    "decimate_passes",
    "protect_uv_seams",
    "run_planar_prepass",
    "planar_angle",
    "bake_normal_map",
    "normal_map_resolution",
    "auto_cage_extrusion",
    "cage_extrusion_mm",
    "max_texture_size",
    "resize_mode",
    "output_filename",
    "output_folder",
    "export_selected_only",
    "export_format",
    "use_draco",
    "draco_level",
    "draco_position_quantization",
    "draco_normal_quantization",
    "draco_texcoord_quantization",
    "image_format",
    "image_quality",
    "fbx_axis_preset",
    "fbx_embed_textures",
    "fbx_smoothing",
    "obj_export_materials",
    "obj_forward_axis",
    "obj_up_axis",
    "run_lod",
    "lod_levels",
    "lod_suffix_pattern",
    "lod_ratios",
    "run_symmetry",
    "symmetry_axis",
    "symmetry_threshold_mm",
    "symmetry_min_score",
    "run_remove_small_pieces",
    "small_pieces_face_threshold",
    "small_pieces_size_threshold",
    "analysis_target_preset",
    "analysis_target_faces",
    "verbose_logging",
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

        # One-shot migrations from pre-1.8.0 / 1.8.0-beta configs. Applied to the
        # loaded dict before setattr so old keys can't reach the property group.

        # fix_manifold (bool) -> manifold_method (enum). True -> FILL_HOLES
        # (safe default), False -> OFF. fix_manifold=True does NOT migrate to
        # PRINT3D because the Toolbox was damaging thin-shell meshes; users
        # who want it can opt back in explicitly.
        if "fix_manifold" in data:
            if "manifold_method" not in data:
                data["manifold_method"] = "FILL_HOLES" if data["fix_manifold"] else "OFF"
            del data["fix_manifold"]

        # dissolve_angle removed; planar_angle (post-pass) covers flat merging.
        data.pop("dissolve_angle", None)

        # UV dilate step removed entirely.
        data.pop("run_uv_dilate", None)
        data.pop("uv_dilate_pixels", None)

        # protect_uv_seams forced to True. The "off" default that some saved
        # configs have was based on a misdiagnosis (seam protection was blamed
        # for texture damage that actually came from the 3D Print Toolbox).
        if "protect_uv_seams" in data:
            data["protect_uv_seams"] = True

        # run_planar_postpass -> run_planar_prepass. The property was renamed
        # after moving the planar DISSOLVE from after COLLAPSE to before it
        # (see decimate_single). Preserve the user's on/off setting.
        if "run_planar_postpass" in data:
            if "run_planar_prepass" not in data:
                data["run_planar_prepass"] = data["run_planar_postpass"]
            del data["run_planar_postpass"]

        for key, value in data.items():
            if key in SAVEABLE_PROPS:
                try:
                    setattr(props, key, value)
                except (TypeError, AttributeError):
                    pass  # Skip if property type changed between versions
        return True
    except (OSError, json.JSONDecodeError):
        return False


def _resolve_output_dir(props):
    """Return the absolute output directory for the export step.

    Falls back through: explicit folder → blend file dir → user home.
    """
    if props.output_folder:
        return props.output_folder
    if bpy.data.filepath:
        return os.path.dirname(bpy.data.filepath)
    return os.path.expanduser("~")


def _resolve_output_path(props):
    """Return the absolute output path for a single export.

    Forces the filename's extension to match ``props.export_format`` so a
    user who typed ``model.glb`` and then switched the format dropdown to
    FBX still ends up with ``model.fbx`` written to disk.
    """
    out_dir = _resolve_output_dir(props)
    filename = swap_export_extension(props.output_filename, props.export_format)
    return os.path.join(out_dir, filename)


def export_model(context, props):
    """Dispatch to the right exporter based on ``props.export_format``."""
    output_path = _resolve_output_path(props)
    if props.export_format == "GLB":
        return _export_glb(context, props, output_path)
    if props.export_format == "FBX":
        return _export_fbx(context, props, output_path)
    if props.export_format == "OBJ":
        return _export_obj(context, props, output_path)
    return _export_glb(context, props, output_path)


def _export_glb(context, props, output_path):
    """Export the scene as a compressed GLB. Returns a detail string."""
    log(
        context,
        f"  export: path={output_path}, blender_image_format="
        f"{'AUTO' if props.image_format == 'NONE' else props.image_format}",
        level="DEBUG",
    )

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
        log(
            context,
            f"  export: wrote {size_mb:.2f} MB to {output_path}",
            level="DEBUG",
        )
        return f"Exported: {output_path} ({size_mb:.2f} MB)"
    log(context, f"  export: file not found at {output_path}", level="DEBUG")
    return "Export may have failed — file not found"


_FBX_AXIS_PRESETS = {
    # (axis_forward, axis_up, global_scale, bake_space_transform)
    "UNREAL": ("X", "Z", 1.0, True),
    "UNITY": ("-Z", "Y", 1.0, False),
    "DEFAULT": ("-Z", "Y", 1.0, False),
}


def _export_fbx(context, props, output_path):
    """Export the scene as a binary FBX. Returns a detail string."""
    axis_forward, axis_up, global_scale, bake_space = _FBX_AXIS_PRESETS[props.fbx_axis_preset]
    log(
        context,
        f"  export: path={output_path}, axis=({axis_forward},{axis_up}), "
        f"scale={global_scale}, bake_space={bake_space}, embed={props.fbx_embed_textures}, "
        f"smooth={props.fbx_smoothing}",
        level="DEBUG",
    )

    bpy.ops.export_scene.fbx(
        filepath=output_path,
        use_selection=props.export_selected_only,
        use_mesh_modifiers=True,
        path_mode="COPY" if props.fbx_embed_textures else "AUTO",
        embed_textures=props.fbx_embed_textures,
        mesh_smooth_type=props.fbx_smoothing,
        axis_forward=axis_forward,
        axis_up=axis_up,
        global_scale=global_scale,
        bake_space_transform=bake_space,
        object_types={"MESH", "EMPTY"},
        add_leaf_bones=False,
        bake_anim=False,
    )

    if os.path.exists(output_path):
        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        log(context, f"  export: wrote {size_mb:.2f} MB to {output_path}", level="DEBUG")
        return f"Exported: {output_path} ({size_mb:.2f} MB)"
    log(context, f"  export: file not found at {output_path}", level="DEBUG")
    return "Export may have failed — file not found"


def _export_obj(context, props, output_path):
    """Export the scene as Wavefront OBJ. Returns a detail string."""
    log(
        context,
        f"  export: path={output_path}, materials={props.obj_export_materials}, "
        f"axis=(forward={props.obj_forward_axis}, up={props.obj_up_axis})",
        level="DEBUG",
    )

    bpy.ops.wm.obj_export(
        filepath=output_path,
        export_selected_objects=props.export_selected_only,
        apply_modifiers=True,
        export_materials=props.obj_export_materials,
        export_uv=True,
        export_normals=True,
        export_colors=True,
        export_triangulated_mesh=False,
        forward_axis=props.obj_forward_axis,
        up_axis=props.obj_up_axis,
    )

    if os.path.exists(output_path):
        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        log(context, f"  export: wrote {size_mb:.2f} MB to {output_path}", level="DEBUG")
        return f"Exported: {output_path} ({size_mb:.2f} MB)"
    log(context, f"  export: file not found at {output_path}", level="DEBUG")
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
    # 2. Output directory (same logic as _export_glb)
    # ------------------------------------------------------------------
    output_dir = _resolve_output_dir(props)

    # ------------------------------------------------------------------
    # 3. Base name
    # ------------------------------------------------------------------
    base_name = os.path.splitext(props.output_filename)[0]
    extension = _FORMAT_EXTENSIONS[props.export_format]

    # ------------------------------------------------------------------
    # 4. Generate each LOD beyond LOD0
    # ------------------------------------------------------------------
    exported = []
    meshes = get_selected_meshes()
    if not meshes:
        return "LOD generation: no meshes selected"

    for i, ratio in enumerate(ratios):
        if i == 0:
            continue  # LOD0 is the normal export
        if ratio >= 1.0:
            continue  # nothing to reduce

        # Add non-destructive decimate modifiers (export_apply will apply them)
        suffix = props.lod_suffix_pattern.replace("{n}", str(i))
        log(
            context,
            f"  LOD{i}: ratio={ratio}, suffix='{suffix}'",
            level="DEBUG",
        )
        mod_name = f"_AIOPT_LOD{i}"
        for obj in meshes:
            mod = obj.modifiers.new(name=mod_name, type="DECIMATE")
            mod.decimate_type = "COLLAPSE"
            mod.ratio = ratio
            mod.use_collapse_triangulate = True

        # Build filename using the suffix pattern
        filename = f"{base_name}{suffix}{extension}"
        output_path = os.path.join(output_dir, filename)

        # Select meshes for export
        bpy.ops.object.select_all(action="DESELECT")
        for obj in meshes:
            obj.select_set(True)
        if meshes:
            context.view_layer.objects.active = meshes[0]

        # Export this LOD using the active format's exporter
        if props.export_format == "GLB":
            _export_glb(context, props, output_path)
        elif props.export_format == "FBX":
            _export_fbx(context, props, output_path)
        elif props.export_format == "OBJ":
            _export_obj(context, props, output_path)

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

    log(
        context,
        f"  LOD generate: {len(exported)} file(s) written",
        level="DEBUG",
    )
    if not exported:
        return "LOD generation: no extra LOD levels were exported"

    return f"Generated {len(exported)} LOD(s): {'; '.join(exported)}"
