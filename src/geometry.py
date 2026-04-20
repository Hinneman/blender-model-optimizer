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

    # Assign weights. Protected verts (seam endpoints + one-ring) get 1.0;
    # every other vert gets 0.1. With invert_vertex_group=True on the
    # COLLAPSE modifier, weight=1.0 means max resistance to collapse.
    protected_list = list(expanded)
    if protected_list:
        group.add(index=protected_list, weight=1.0, type="REPLACE")

    # Explicitly weight every other vertex at 0.1 so the quadric solver sees
    # the full contrast (a vertex not assigned to a group is treated as 0,
    # which would make non-seam verts infinitely cheap to collapse instead
    # of just 10x cheaper).
    all_other = [i for i in range(total_verts) if i not in expanded]
    if all_other:
        group.add(index=all_other, weight=0.1, type="REPLACE")

    return "AIOPT_Seam_Protect"


def decimate_single(context, obj, props):
    """Dissolve coplanar faces, collapse-decimate, optionally planar-dissolve *obj*.

    When ``props.decimate_passes > 1``, collapse decimation is split into N passes
    targeting ``props.decimate_ratio`` overall. Per-pass ratio is
    ``decimate_ratio ** (1/passes)`` so the cumulative ratio closely approximates
    the final ratio. The dissolve pre-pass and seam-group build run once up
    front; only the COLLAPSE modifier runs per iteration. Blender propagates
    vertex-group weights through collapse, so the seam group stays correct
    across passes without rebuilding.

    When ``props.protect_uv_seams`` is True and the mesh has UV layers, a
    temporary vertex group ``AIOPT_Seam_Protect`` biases the COLLAPSE solver
    against collapsing seam-endpoint vertices and their one-ring neighbors.
    The group is removed after decimation so the exported mesh stays clean.

    When ``props.run_planar_postpass`` is True, a planar (DISSOLVE) decimate
    modifier runs once after the collapse loop to merge near-coplanar faces
    into n-gons (angle threshold ``props.planar_angle``). This reduces triangle
    count in flat regions without touching curved surfaces.

    The remove-doubles / normals / delete-loose cleanup runs once at the end.
    """
    bpy.ops.object.select_all(action="DESELECT")
    context.view_layer.objects.active = obj
    obj.select_set(True)

    passes = max(1, int(getattr(props, "decimate_passes", 1)))
    per_pass_ratio = props.decimate_ratio ** (1.0 / passes)

    # Pre-pass: dissolve nearly-coplanar faces (cleans flat surfaces, preserves UVs)
    if props.dissolve_angle > 0:
        before = len(obj.data.polygons)
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.dissolve_limited(angle_limit=props.dissolve_angle, delimit={"UV"})
        bpy.ops.object.mode_set(mode="OBJECT")
        after = len(obj.data.polygons)
        print(f"  [AI Optimizer] Dissolve pre-pass: {before:,} -> {after:,} faces")

    # Protect UV seams: build a vertex-group weight bias that the COLLAPSE
    # solver treats as ~10x more expensive to collapse. Mesh topology is
    # unchanged — the protection is a numerical cost bias, not a hard
    # constraint. Off by default: on AI-generated meshes with fragmented
    # UVs the protection degrades gracefully because most vertices end up
    # in the protected set, reducing the relative bias.
    seam_group_name = None
    if getattr(props, "protect_uv_seams", False):
        seam_group_name = _protect_uv_seams(obj)

    for pass_idx in range(passes):
        before = len(obj.data.polygons)
        mod = obj.modifiers.new(name="Decimate_Optimize", type="DECIMATE")
        mod.decimate_type = "COLLAPSE"
        mod.ratio = per_pass_ratio
        # use_collapse_triangulate=False: when the dissolve pre-pass has merged
        # near-coplanar tris into n-gons, forcing re-triangulation here undoes
        # that work and makes the "ratio" math act on a larger-than-reported
        # triangle count — so pass 1 can grow the face count instead of
        # shrinking it. Downstream consumers (GLB exporter) triangulate anyway.
        mod.use_collapse_triangulate = False
        if seam_group_name:
            mod.vertex_group = seam_group_name
            mod.invert_vertex_group = True
        bpy.ops.object.modifier_apply(modifier=mod.name)
        after = len(obj.data.polygons)
        actual_ratio = (after / before) if before > 0 else 0.0
        print(
            f"  [AI Optimizer] Decimate pass {pass_idx + 1}/{passes}: "
            f"{before:,} -> {after:,} faces "
            f"(ratio {actual_ratio:.3f}, requested {per_pass_ratio:.3f})"
        )

    # Optional planar post-pass: merge adjacent near-coplanar faces into
    # n-gons. Reduces triangle count in flat regions without touching curved
    # surfaces. delimit={"UV"} preserves UV island boundaries natively.
    if getattr(props, "run_planar_postpass", True) and props.planar_angle > 0:
        before = len(obj.data.polygons)
        mod = obj.modifiers.new(name="Decimate_Planar", type="DECIMATE")
        mod.decimate_type = "DISSOLVE"
        mod.angle_limit = props.planar_angle
        mod.delimit = {"UV"}
        bpy.ops.object.modifier_apply(modifier=mod.name)
        after = len(obj.data.polygons)
        print(f"  [AI Optimizer] Planar post-pass: {before:,} -> {after:,} faces")

    # Remove the seam-protect vertex group: its purpose ends with decimation
    # and we don't want diagnostic groups leaking into the exported GLB.
    if seam_group_name:
        group = obj.vertex_groups.get(seam_group_name)
        if group is not None:
            obj.vertex_groups.remove(group)

    # Post-decimate cleanup: fix degenerate geometry without adding new faces
    # (hole-filling creates faces with bad UVs that cause texture artifacts).
    # Deliberately no normals_make_consistent here: on thin-shell meshes
    # (draped covers, cloth, single-layer surfaces) the flood-fill algorithm
    # flips whole face islands inside-out, producing apparent holes where the
    # back-faces render. COLLAPSE preserves input winding so there's nothing
    # for it to fix anyway.
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
