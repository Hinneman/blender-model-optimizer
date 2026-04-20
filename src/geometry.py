import bpy
from mathutils import Vector


def _fill_holes_manifold():
    """Select non-manifold geometry on the active mesh in Edit mode and fill holes up to 32 sides.

    Caller must have already entered Edit mode on the target object. Returns
    True on successful fill, False if Blender reports a RuntimeError (typically
    when there's nothing to fill or the selection is invalid). Safe on thin-
    shell meshes because it only adds n-gons to close open boundaries; it
    never deletes geometry.
    """
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
        return True
    except RuntimeError:
        return False


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

    # Pre-pass: collapse zero-area faces and zero-length edges that AI meshes
    # commonly contain. Threshold is intentionally very small — only truly
    # degenerate geometry is affected.
    bpy.ops.mesh.dissolve_degenerate(threshold=1e-6)

    # Merge close vertices
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.remove_doubles(threshold=props.merge_distance_mm / 1000.0)

    # Recalculate normals
    if props.recalculate_normals:
        bpy.ops.mesh.normals_make_consistent(inside=False)

    # Manifold fix — dispatch on the enum. PRINT3D is the aggressive option
    # (deletes geometry around non-manifold edges); FILL_HOLES is safe on
    # thin-shell meshes. OFF skips entirely.
    fixed = False
    method_used = "none"
    if props.manifold_method == "PRINT3D":
        try:
            bpy.ops.mesh.print3d_clean_non_manifold()
            fixed = True
            method_used = "3D Print Toolbox"
        except (AttributeError, RuntimeError):
            # Plugin went missing between property set and pipeline run.
            # Fall through to FILL_HOLES rather than fail the whole pipeline.
            fixed = _fill_holes_manifold()
            method_used = "manual fill holes (3D Print Toolbox not available)"
    elif props.manifold_method == "FILL_HOLES":
        fixed = _fill_holes_manifold()
        method_used = "manual fill holes"
    # OFF: no-op, method_used stays "none"

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


def _remove_interior_loose_parts(context, obj, token=None):
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
        if token is not None:
            token.check()
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


def _remove_interior_raycast(context, obj, token=None):
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
    # Sample 13 outward directions across a ~55° cone around the face normal.
    # Each offset is added to the unit normal and re-normalized before casting.
    # Using a wider cone (vs. the previous ~6° cluster) lets exterior faces in
    # concave regions have at least one ray exit to open space, which breaks
    # the "all blocked" rule and prevents them from being flagged as interior.
    #
    # Layout:
    #   - 1 ray along pure normal           (0° from normal)
    #   - 6 rays at ~30° from normal        (inner ring, r = tan(30°) ≈ 0.577)
    #   - 6 rays at ~55° from normal        (outer ring, r = tan(55°) ≈ 1.428),
    #     rotated 30° from the inner ring so the two rings don't line up.
    jitter_offsets = [
        # Pure normal
        Vector((0.0, 0.0, 0.0)),
        # Inner ring at ~30°, 6 rays spaced 60° apart
        Vector((0.577, 0.0, 0.0)),
        Vector((0.289, 0.500, 0.0)),
        Vector((-0.289, 0.500, 0.0)),
        Vector((-0.577, 0.0, 0.0)),
        Vector((-0.289, -0.500, 0.0)),
        Vector((0.289, -0.500, 0.0)),
        # Outer ring at ~55°, 6 rays rotated 30° from the inner ring
        Vector((1.237, 0.714, 0.0)),
        Vector((0.0, 1.428, 0.0)),
        Vector((-1.237, 0.714, 0.0)),
        Vector((-1.237, -0.714, 0.0)),
        Vector((0.0, -1.428, 0.0)),
        Vector((1.237, -0.714, 0.0)),
    ]

    interior_faces = []
    for face in bm.faces:
        if token is not None:
            token.check()
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


def remove_interior_single(context, obj, props, token=None):
    """Remove interior faces from *obj* using the configured method.
    Returns the number of faces removed.
    """
    if props.interior_method == "RAY_CAST":
        return _remove_interior_raycast(context, obj, token=token)
    return _remove_interior_loose_parts(context, obj, token=token)


def remove_small_pieces_single(context, obj, props, token=None):
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
        if token is not None:
            token.check()
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


def detect_and_apply_symmetry(context, obj, axis="X", threshold=0.001, min_score=0.85, token=None):
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
        if token is not None and i % 1024 == 0:
            token.check()
        kd.insert(v.co, i)
    kd.balance()

    # Check symmetry for vertices on the positive side
    positive_verts = [v for v in bm.verts if v.co[axis_index] >= center]
    matched = 0
    for i, v in enumerate(positive_verts):
        if token is not None and i % 1024 == 0:
            token.check()
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

    # Snap object origin to the symmetry plane so the mirror modifier pivots
    # exactly on the plane. Without this, meshes whose origin is off-plane
    # produce a gap or overlap at the seam when mirrored.
    #
    # We translate obj.location along the symmetry axis to match `center`
    # (in local space, since `center` was computed from local vert coords),
    # then counter-translate the mesh data so the world-space shape is
    # unchanged.
    axis_vec = Vector((0.0, 0.0, 0.0))
    axis_vec[axis_index] = center
    world_offset = obj.matrix_world.to_3x3() @ axis_vec
    obj.location = obj.location + world_offset
    # Counter-translate mesh verts by -center on the axis
    bm_origin = bmesh.new()
    bm_origin.from_mesh(obj.data)
    for v in bm_origin.verts:
        v.co[axis_index] -= center
    bm_origin.to_mesh(obj.data)
    bm_origin.free()
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


def _compute_cage_extrusion(obj, props):
    """Return cage extrusion distance in meters.

    When ``props.auto_cage_extrusion`` is True, returns 1% of the object's
    bounding-box max dimension. Otherwise returns the user-configured
    ``cage_extrusion_mm`` converted to meters.
    """
    if props.auto_cage_extrusion:
        max_dim = max(obj.dimensions.x, obj.dimensions.y, obj.dimensions.z)
        if max_dim <= 0:
            return props.cage_extrusion_mm / 1000.0
        return max_dim * 0.01
    return props.cage_extrusion_mm / 1000.0


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
    highpoly.hide_set(False)
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
            cage_extrusion=_compute_cage_extrusion(obj, props),
        )
    except RuntimeError as exc:
        print(f"  [AI Optimizer] Normal map bake failed for '{obj.name}': {exc}")
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


def _protect_uv_seams(obj):
    """Build a vertex-group weight bias to protect UV-island boundaries from DECIMATE.

    Creates (or refreshes) a vertex group named ``AIOPT_Seam_Protect`` on
    ``obj``. Seam-endpoint vertices and their one-ring neighbors receive
    weight 1.0; every other vertex receives weight 0.1. When the caller
    sets ``mod.vertex_group = "AIOPT_Seam_Protect"`` with
    ``mod.invert_vertex_group = True`` on a COLLAPSE modifier, Blender's
    quadric solver treats the weighted vertices as ~10x more expensive to
    collapse. Mesh topology is not changed — the protection is a numerical
    cost bias, not a hard constraint.

    Returns ``"AIOPT_Seam_Protect"`` on success or ``None`` when the mesh
    has no UV layer or seam detection fails. No-op on meshes without UVs.
    """
    import bmesh

    if not obj.data.uv_layers:
        return None

    # Auto-mark seams from islands if none exist yet. seams_from_islands is
    # a UV editor operator and requires Edit mode with a selection.
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    has_seams = any(e.seam for e in bm.edges)
    bm.free()

    if not has_seams:
        try:
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.select_all(action="SELECT")
            bpy.ops.uv.seams_from_islands()
            bpy.ops.object.mode_set(mode="OBJECT")
        except RuntimeError as exc:
            print(f"  [AI Optimizer] Seam detection failed: {exc}")
            return None

    # Collect seam-endpoint vertex indices, then expand by one edge hop to
    # include the immediate neighbors. Interior vertices that collapse onto
    # a seam vertex are the main source of texture smearing; protecting the
    # one-ring buffer blocks that collapse direction.
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    protected = set()
    for edge in bm.edges:
        if edge.seam:
            for v in edge.verts:
                protected.add(v.index)
    # One-ring expansion: for every protected vertex, add its neighbors.
    bm.verts.ensure_lookup_table()
    expanded = set(protected)
    for idx in protected:
        v = bm.verts[idx]
        for e in v.link_edges:
            other = e.other_vert(v)
            expanded.add(other.index)
    total_verts = len(bm.verts)
    bm.free()

    # Remove any stale group from a previous run so weights don't accumulate.
    existing = obj.vertex_groups.get("AIOPT_Seam_Protect")
    if existing is not None:
        obj.vertex_groups.remove(existing)

    try:
        group = obj.vertex_groups.new(name="AIOPT_Seam_Protect")
    except RuntimeError as exc:
        print(f"  [AI Optimizer] Vertex group creation failed: {exc}")
        return None

    # Assign weights. Protected verts (seam endpoints + one-ring) get 0.5;
    # every other vert gets 0.1. With invert_vertex_group=True on the
    # COLLAPSE modifier, these function as collapse costs — 0.5 is strongly
    # biased against collapse but NOT immune, 0.1 is cheap. We deliberately
    # avoid 1.0 here because Blender treats it as a hard "do-not-collapse"
    # rather than a continuous bias, which stalls multi-pass COLLAPSE after
    # pass 1 on fragmented-UV meshes (where >40% of verts end up protected —
    # they occupy the entire remaining collapse budget of the quadric solver
    # and later passes find nothing to do).
    protected_list = list(expanded)
    if protected_list:
        group.add(index=protected_list, weight=0.5, type="REPLACE")

    all_other = [i for i in range(total_verts) if i not in expanded]
    if all_other:
        group.add(index=all_other, weight=0.1, type="REPLACE")

    return "AIOPT_Seam_Protect"


def decimate_single(context, obj, props):
    """Triangulate, planar-dissolve flat regions, collapse-decimate (optionally multi-pass) *obj*.

    The mesh is triangulated up front, then an unweighted planar DISSOLVE
    merges flat regions into n-gons before COLLAPSE runs. Doing the planar
    merge first lets COLLAPSE focus its budget on curved regions where the
    quadric solver is most useful; leaving it for a post-pass (tried in
    1.8.0-beta) caused COLLAPSE to stall after pass 1 with seam protection
    on, because flat seam-adjacent regions were weight-blocked and COLLAPSE
    ran out of cheap collapse candidates.

    When ``props.decimate_passes > 1``, collapse decimation is split across
    N passes. The per-pass ratio is computed *after* the planar pre-pass,
    based on the remaining reduction needed to reach
    ``start_faces * decimate_ratio``, so the planar dissolve doesn't compound
    with the requested ratio and overshoot the user's target.

    When ``props.protect_uv_seams`` is True and the mesh has UV layers, a
    temporary vertex group ``AIOPT_Seam_Protect`` biases the COLLAPSE solver
    against collapsing seam-endpoint vertices and their one-ring neighbors.
    Protected verts get weight 0.5 (strongly biased but not immune — weight
    1.0 would make them hard-uncollapsible and stall multi-pass COLLAPSE on
    fragmented-UV meshes). The group is removed after decimation so the
    exported mesh stays clean.

    When ``props.run_planar_prepass`` is True, a planar (DISSOLVE) decimate
    modifier runs once before the collapse loop at angle threshold
    ``props.planar_angle`` with ``delimit={"UV"}``.

    The remove-doubles / delete-loose cleanup runs once at the end.
    """
    bpy.ops.object.select_all(action="DESELECT")
    context.view_layer.objects.active = obj
    obj.select_set(True)

    passes = max(1, int(getattr(props, "decimate_passes", 1)))
    start_faces = len(obj.data.polygons)
    target_faces = max(1, int(start_faces * props.decimate_ratio))

    # Protect UV seams: build the vertex-group weight bias before triangulation
    # so seam-endpoint vertex indices are stable. Triangulation preserves vertex
    # identity, only adding new edges, so the group stays valid.
    seam_group_name = None
    if getattr(props, "protect_uv_seams", False):
        seam_group_name = _protect_uv_seams(obj)

    # Triangulate up front so every COLLAPSE pass sees one-polygon-one-triangle.
    # Without this, dissolving coplanar n-gons and then re-triangulating inside
    # COLLAPSE inflates the face count on pass 1 and overshoots the estimate.
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.quads_convert_to_tris()
    bpy.ops.object.mode_set(mode="OBJECT")

    # Planar pre-pass: merge adjacent near-coplanar faces into n-gons BEFORE
    # COLLAPSE. Un-weighted (DISSOLVE ignores the vertex group), so it eats
    # flat regions that COLLAPSE would later refuse under seam protection.
    # delimit={"UV"} preserves UV island boundaries. Runs before COLLAPSE so
    # the quadric solver can spend its budget on curved regions where it's
    # actually useful.
    if getattr(props, "run_planar_prepass", True) and props.planar_angle > 0:
        mod = obj.modifiers.new(name="Decimate_Planar", type="DECIMATE")
        mod.decimate_type = "DISSOLVE"
        mod.angle_limit = props.planar_angle
        mod.delimit = {"UV"}
        bpy.ops.object.modifier_apply(modifier=mod.name)

    # Compute per-pass ratio AFTER the planar pre-pass. The planar pre-pass
    # has already changed the face count COLLAPSE sees, so anchoring
    # per_pass_ratio on props.decimate_ratio directly would compound the two
    # reductions and undershoot the user's target. Solve for the ratio that
    # takes current_faces down to target_faces over `passes` passes.
    current_faces = len(obj.data.polygons)
    overall_needed = 1.0 if current_faces <= target_faces else target_faces / current_faces
    per_pass_ratio = max(0.01, min(1.0, overall_needed)) ** (1.0 / passes)

    for _pass_idx in range(passes):
        mod = obj.modifiers.new(name="Decimate_Optimize", type="DECIMATE")
        mod.decimate_type = "COLLAPSE"
        mod.ratio = per_pass_ratio
        mod.use_collapse_triangulate = False
        if seam_group_name:
            mod.vertex_group = seam_group_name
            mod.invert_vertex_group = True
        bpy.ops.object.modifier_apply(modifier=mod.name)

    # Remove the seam-protect vertex group: its purpose ends with decimation
    # and we don't want diagnostic groups leaking into the exported GLB.
    if seam_group_name:
        group = obj.vertex_groups.get(seam_group_name)
        if group is not None:
            obj.vertex_groups.remove(group)

    # Post-decimate cleanup: merge close verts and delete stray loose geometry.
    # No normals_make_consistent here: flood-fill flips islands inside-out on
    # thin-shell meshes, and COLLAPSE preserves winding anyway.
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.remove_doubles(threshold=props.merge_distance_mm / 1000.0)
    bpy.ops.mesh.delete_loose(use_verts=True, use_edges=True, use_faces=False)
    bpy.ops.object.mode_set(mode="OBJECT")


def floor_snap_all(meshes, token=None):
    """Translate all meshes so the group's lowest world-space vertex sits at Z=0.

    Computes the minimum world-Z across every vertex of every mesh, then
    shifts each mesh's ``obj.location.z`` up by that amount. XY is not
    touched. Preserves relative heights between objects.

    Returns the shift amount (in meters). Returns 0.0 when there are no
    meshes or no vertices.
    """
    if not meshes:
        return 0.0

    min_z = float("inf")
    for obj in meshes:
        if token is not None:
            token.check()
        mw = obj.matrix_world
        for v in obj.data.vertices:
            world_z = (mw @ v.co).z
            if world_z < min_z:
                min_z = world_z

    if min_z == float("inf"):
        return 0.0

    shift = -min_z
    if abs(shift) < 1e-9:
        return 0.0

    for obj in meshes:
        obj.location.z += shift

    return shift
