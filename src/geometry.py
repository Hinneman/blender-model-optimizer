import bpy
from mathutils import Vector


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
    bpy.ops.mesh.remove_doubles(threshold=props.merge_distance_mm / 1000.0)

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


def _bbox_contains(outer_obj, inner_obj):
    """Check if inner_obj's bounding box is fully inside outer_obj's bounding box."""
    outer_corners = [outer_obj.matrix_world @ Vector(c) for c in outer_obj.bound_box]
    inner_corners = [inner_obj.matrix_world @ Vector(c) for c in inner_obj.bound_box]

    outer_min = Vector(
        (
            min(c.x for c in outer_corners),
            min(c.y for c in outer_corners),
            min(c.z for c in outer_corners),
        )
    )
    outer_max = Vector(
        (
            max(c.x for c in outer_corners),
            max(c.y for c in outer_corners),
            max(c.z for c in outer_corners),
        )
    )

    for c in inner_corners:
        if c.x <= outer_min.x or c.x >= outer_max.x:
            return False
        if c.y <= outer_min.y or c.y >= outer_max.y:
            return False
        if c.z <= outer_min.z or c.z >= outer_max.z:
            return False
    return True


def _remove_interior_loose_parts(context, obj):
    """Remove disconnected mesh parts that are fully enclosed inside other parts.

    Separates mesh into loose parts, checks bounding-box containment,
    deletes enclosed parts, and re-joins the remainder.
    Returns the number of faces removed.
    """
    faces_before = len(obj.data.polygons)
    original_name = obj.name

    bpy.ops.object.select_all(action="DESELECT")
    context.view_layer.objects.active = obj
    obj.select_set(True)

    # Remember existing scene meshes so we only touch parts from this object
    existing_meshes = set(context.scene.objects)

    # Separate into loose parts
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.separate(type="LOOSE")
    bpy.ops.object.mode_set(mode="OBJECT")

    # Collect only parts that came from this object (original + newly separated)
    parts = [o for o in context.scene.objects if o.type == "MESH" and (o == obj or o not in existing_meshes)]
    if len(parts) <= 1:
        # Nothing was separated — single connected mesh
        return 0

    # Sort by face count descending — largest is most likely the outer shell
    parts.sort(key=lambda o: len(o.data.polygons), reverse=True)

    to_delete = []
    for inner in parts:
        for outer in parts:
            if inner == outer:
                continue
            if _bbox_contains(outer, inner):
                to_delete.append(inner)
                break

    # Delete enclosed parts
    bpy.ops.object.select_all(action="DESELECT")
    for obj_del in to_delete:
        obj_del.select_set(True)

    if to_delete:
        bpy.ops.object.delete()

    # Re-join remaining parts (only from this object, not unrelated scene meshes)
    remaining = [o for o in parts if o not in to_delete]
    if remaining:
        bpy.ops.object.select_all(action="DESELECT")
        for o in remaining:
            o.select_set(True)
        context.view_layer.objects.active = remaining[0]
        if len(remaining) > 1:
            bpy.ops.object.join()
        remaining[0].name = original_name

    faces_after = len(context.view_layer.objects.active.data.polygons) if context.view_layer.objects.active else 0
    return faces_before - faces_after


def _remove_interior_raycast(context, obj):
    """Remove interior faces by casting rays outward from each face center.

    For each face, casts rays along the face normal (and jittered directions).
    If all rays hit back-faces of the same object, the face is considered interior.
    Returns the number of faces removed.
    """
    import bmesh

    faces_before = len(obj.data.polygons)

    bpy.ops.object.select_all(action="DESELECT")
    context.view_layer.objects.active = obj
    obj.select_set(True)

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.faces.ensure_lookup_table()

    # Small offset to avoid self-intersection
    OFFSET = 0.001
    # Jitter directions around the normal
    jitter_offsets = [
        Vector((0, 0, 0)),
        Vector((0.1, 0.1, 0)),
        Vector((-0.1, 0.1, 0)),
        Vector((0.1, -0.1, 0)),
        Vector((-0.1, -0.1, 0)),
    ]

    interior_faces = []
    for face in bm.faces:
        center = obj.matrix_world @ face.calc_center_median()
        normal = (obj.matrix_world.to_3x3() @ face.normal).normalized()

        all_blocked = True
        for jitter in jitter_offsets:
            direction = (normal + jitter).normalized()
            origin = center + normal * OFFSET

            # Cast in object local space
            local_origin = obj.matrix_world.inverted() @ origin
            local_dir = (obj.matrix_world.inverted().to_3x3() @ direction).normalized()

            hit, _loc, hit_normal, _idx = obj.ray_cast(local_origin, local_dir)
            if not hit:
                all_blocked = False
                break
            # Check if we hit a back-face (normal pointing same direction as ray)
            if hit_normal.dot(local_dir) < 0:
                all_blocked = False
                break

        if all_blocked:
            interior_faces.append(face)

    # Delete interior faces
    bmesh.ops.delete(bm, geom=interior_faces, context="FACES")

    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()

    faces_after = len(obj.data.polygons)
    return faces_before - faces_after


def remove_interior_single(context, obj, props):
    """Remove interior faces from *obj* using the configured method.
    Returns the number of faces removed.
    """
    if props.interior_method == "RAY_CAST":
        return _remove_interior_raycast(context, obj)
    return _remove_interior_loose_parts(context, obj)


def remove_small_pieces_single(context, obj, props):
    """Delete disconnected mesh islands below face count or volume threshold.

    A loose part is deleted if ``face_count < face_threshold`` OR
    ``volume < volume_threshold``.  Uses absolute volume so inverted
    normals don't produce false negatives.

    Returns ``(parts_deleted, faces_removed)``.
    """
    import bmesh

    face_threshold = props.small_pieces_face_threshold
    # Convert cm edge length to m³ volume: (cm / 100)³
    volume_threshold = (props.small_pieces_size_threshold / 100.0) ** 3

    faces_before = len(obj.data.polygons)
    original_name = obj.name

    bpy.ops.object.select_all(action="DESELECT")
    context.view_layer.objects.active = obj
    obj.select_set(True)

    existing_objects = set(context.scene.objects)

    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.separate(type="LOOSE")
    bpy.ops.object.mode_set(mode="OBJECT")

    parts = [o for o in context.scene.objects if o.type == "MESH" and (o == obj or o not in existing_objects)]

    if len(parts) <= 1:
        # Single connected mesh — nothing to separate
        return (0, 0)

    to_delete = []
    for part in parts:
        face_count = len(part.data.polygons)

        bm = bmesh.new()
        bm.from_mesh(part.data)
        volume = abs(bm.calc_volume())
        bm.free()

        if face_count < face_threshold or volume < volume_threshold:
            to_delete.append(part)

    # Safety: never delete all parts — keep the largest if everything qualifies
    if len(to_delete) == len(parts):
        largest = max(parts, key=lambda o: len(o.data.polygons))
        to_delete = [o for o in to_delete if o != largest]

    bpy.ops.object.select_all(action="DESELECT")
    for obj_del in to_delete:
        obj_del.select_set(True)
    if to_delete:
        bpy.ops.object.delete()

    remaining = [o for o in parts if o not in to_delete]
    if remaining:
        bpy.ops.object.select_all(action="DESELECT")
        for o in remaining:
            o.select_set(True)
        context.view_layer.objects.active = remaining[0]
        if len(remaining) > 1:
            bpy.ops.object.join()
        remaining[0].name = original_name

    faces_after = len(context.view_layer.objects.active.data.polygons) if context.view_layer.objects.active else 0
    return (len(to_delete), faces_before - faces_after)


def detect_and_apply_symmetry(context, obj, axis="X", threshold=0.001, min_score=0.85):
    """Detect near-symmetric geometry and apply a mirror optimization.

    Checks whether *obj* is approximately symmetric along *axis* (one of
    ``"X"``, ``"Y"``, ``"Z"``).  If the symmetry *score* meets
    *min_score*, the negative-side vertices are deleted and a Mirror
    modifier is applied so that Blender reconstructs the other half
    automatically.

    Returns ``(applied, score)`` where *applied* is ``True`` when the
    mirror was created successfully.
    """
    import bmesh
    from mathutils.kdtree import KDTree

    axis_index = {"X": 0, "Y": 1, "Z": 2}[axis]

    # Select and activate the object
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    context.view_layer.objects.active = obj

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()

    if not bm.verts:
        bm.free()
        return (False, 0.0)

    # Centre of mass along the symmetry axis
    center = sum(v.co[axis_index] for v in bm.verts) / len(bm.verts)

    # Build KDTree from all vertices
    kd = KDTree(len(bm.verts))
    for i, v in enumerate(bm.verts):
        kd.insert(v.co, i)
    kd.balance()

    # Check symmetry for vertices on the positive side
    positive_verts = [v for v in bm.verts if v.co[axis_index] >= center]
    matched = 0
    for v in positive_verts:
        mirrored = v.co.copy()
        mirrored[axis_index] = 2 * center - v.co[axis_index]
        _co, _idx, dist = kd.find(mirrored)
        if dist <= threshold:
            matched += 1

    score = matched / max(len(positive_verts), 1)

    if score < min_score:
        bm.free()
        return (False, score)

    # Delete negative-side vertices
    to_delete = [v for v in bm.verts if v.co[axis_index] < center - threshold]
    bmesh.ops.delete(bm, geom=to_delete, context="VERTS")

    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()

    # Add and apply Mirror modifier
    mod = obj.modifiers.new(name="Symmetry_Mirror", type="MIRROR")
    mod.use_axis[0] = axis_index == 0
    mod.use_axis[1] = axis_index == 1
    mod.use_axis[2] = axis_index == 2
    mod.use_clip = True
    mod.merge_threshold = threshold
    bpy.ops.object.modifier_apply(modifier=mod.name)

    return (True, score)


def bake_normal_map_for_decimate(context, obj, highpoly, props):
    """Bake a normal map from *highpoly* onto the decimated *obj*.

    Called after decimation so that surface detail lost during
    simplification is preserved as a tangent-space normal map.

    Returns the baked ``bpy.types.Image`` or *None* on failure.
    """
    resolution = int(props.normal_map_resolution)

    # --- ensure the decimated mesh has a UV map -------------------------
    if not obj.data.uv_layers:
        bpy.ops.object.select_all(action="DESELECT")
        context.view_layer.objects.active = obj
        obj.select_set(True)
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.smart_project(angle_limit=1.15192)
        bpy.ops.object.mode_set(mode="OBJECT")

    # --- create target image --------------------------------------------
    img = bpy.data.images.new(f"{obj.name}_normal_map", resolution, resolution, alpha=False)
    img.colorspace_settings.name = "Non-Color"

    # --- set up material for baking -------------------------------------
    if not obj.data.materials:
        mat = bpy.data.materials.new(name=f"{obj.name}_Material")
        mat.use_nodes = True
        obj.data.materials.append(mat)
    mat = obj.data.materials[0]
    if not mat.use_nodes:
        mat.use_nodes = True
    tree = mat.node_tree

    tex_node = tree.nodes.new("ShaderNodeTexImage")
    tex_node.image = img
    tree.nodes.active = tex_node
    tex_node.select = True

    # --- bake selection -------------------------------------------------
    bpy.ops.object.select_all(action="DESELECT")
    highpoly.select_set(True)
    obj.select_set(True)
    context.view_layer.objects.active = obj

    # --- bake normal map via Cycles ------------------------------------
    original_engine = context.scene.render.engine
    context.scene.render.engine = "CYCLES"

    try:
        bpy.ops.object.bake(
            type="NORMAL",
            use_selected_to_active=True,
            cage_extrusion=props.cage_extrusion_mm / 1000.0,
        )
    except RuntimeError:
        tree.nodes.remove(tex_node)
        bpy.data.images.remove(img)
        context.scene.render.engine = original_engine
        return None

    # --- wire the normal map into the material -------------------------
    normal_node = tree.nodes.new("ShaderNodeNormalMap")
    normal_node.space = "TANGENT"

    tree.links.new(tex_node.outputs["Color"], normal_node.inputs["Color"])

    # Find the Principled BSDF and connect the normal output
    bsdf = None
    for node in tree.nodes:
        if node.type == "BSDF_PRINCIPLED":
            bsdf = node
            break
    if bsdf is not None:
        tree.links.new(normal_node.outputs["Normal"], bsdf.inputs["Normal"])

    # --- pack & restore ------------------------------------------------
    img.pack()
    context.scene.render.engine = original_engine

    return img


def decimate_single(context, obj, props):
    """Dissolve coplanar faces, then collapse-decimate *obj*."""
    bpy.ops.object.select_all(action="DESELECT")
    context.view_layer.objects.active = obj
    obj.select_set(True)

    # Pre-pass: dissolve nearly-coplanar faces (cleans flat surfaces, preserves UVs)
    if props.dissolve_angle > 0:
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.dissolve_limited(angle_limit=props.dissolve_angle, delimit={"UV"})
        bpy.ops.object.mode_set(mode="OBJECT")

    mod = obj.modifiers.new(name="Decimate_Optimize", type="DECIMATE")
    mod.decimate_type = "COLLAPSE"
    mod.ratio = props.decimate_ratio
    mod.use_collapse_triangulate = True
    bpy.ops.object.modifier_apply(modifier=mod.name)
