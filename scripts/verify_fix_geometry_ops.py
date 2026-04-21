"""Verify that Fix Geometry's dissolve_degenerate and remove_doubles operators
actually work on input that's guaranteed to exercise them.

Run from inside Blender's Text Editor (Run Script).

The script:
  1. Removes only the two test objects from any previous run (safe).
  2. Builds a mesh with N zero-area faces (exercises dissolve_degenerate).
  3. Builds a mesh with N overlapping duplicate vertices (exercises remove_doubles).
  4. Runs AI Optimizer's Fix Geometry step on each with verbose logging on.
  5. Asserts face / edge / vert counts dropped as expected.
     Prints PASS / FAIL per scenario and a final summary.

Prerequisite: the AI 3D Model Optimizer add-on must already be installed and
enabled in this Blender build. If it isn't, install build/model-optimizer-addon.py
through Edit > Preferences > Add-ons > Install from Disk first.

IMPORTANT: Run this from the 3D Viewport or Text Editor. It will NOT work
headless (`--background`) because the fix_geometry operator enters edit mode,
which requires a viewport context.
"""

import bmesh
import bpy

TEST_NAMES = ("AIOPT_TEST_DegenerateTest", "AIOPT_TEST_DoublesTest")


def _cleanup_previous_run():
    """Remove any test objects/meshes left over from a previous run.

    Does NOT call select_all/delete or orphans_purge (either can crash Blender
    when called from the Text Editor context). Instead, removes each known
    test object individually via the data API.
    """
    for name in TEST_NAMES:
        obj = bpy.data.objects.get(name)
        if obj is not None:
            mesh = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if mesh and mesh.users == 0:
                bpy.data.meshes.remove(mesh)


def _make_obj_from_bmesh(name, bm):
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    return obj


def build_degenerate_mesh():
    """Build a mesh whose triangles include zero-area faces."""
    bm = bmesh.new()

    good_verts = [
        bm.verts.new((0.0, 0.0, 0.0)),
        bm.verts.new((1.0, 0.0, 0.0)),
        bm.verts.new((1.0, 1.0, 0.0)),
        bm.verts.new((0.0, 1.0, 0.0)),
    ]
    bm.faces.new((good_verts[0], good_verts[1], good_verts[2]))
    bm.faces.new((good_verts[0], good_verts[2], good_verts[3]))

    # 5 zero-area triangles: each has two coincident verts so the triangle
    # collapses to a line segment (zero area). dissolve_degenerate at 1e-6
    # threshold catches these.
    for i in range(5):
        x = 2.0 + i
        v1 = bm.verts.new((x, 0.0, 0.0))
        v2 = bm.verts.new((x + 1.0, 0.0, 0.0))
        v3 = bm.verts.new((x, 0.0, 0.0))  # identical position to v1
        bm.faces.new((v1, v2, v3))

    return _make_obj_from_bmesh("AIOPT_TEST_DegenerateTest", bm)


def build_doubles_mesh():
    """Build a mesh with 8 verts at 4 positions (4 coincident pairs)."""
    bm = bmesh.new()

    a = [
        bm.verts.new((0.0, 0.0, 0.0)),
        bm.verts.new((1.0, 0.0, 0.0)),
        bm.verts.new((1.0, 1.0, 0.0)),
        bm.verts.new((0.0, 1.0, 0.0)),
    ]
    bm.faces.new((a[0], a[1], a[2]))
    bm.faces.new((a[0], a[2], a[3]))

    b = [
        bm.verts.new((0.0, 0.0, 0.0)),
        bm.verts.new((1.0, 0.0, 0.0)),
        bm.verts.new((1.0, 1.0, 0.0)),
        bm.verts.new((0.0, 1.0, 0.0)),
    ]
    bm.faces.new((b[0], b[1], b[2]))
    bm.faces.new((b[0], b[2], b[3]))

    return _make_obj_from_bmesh("AIOPT_TEST_DoublesTest", bm)


def counts(obj):
    return (len(obj.data.polygons), len(obj.data.edges), len(obj.data.vertices))


def _find_view3d_override():
    """Find a 3D Viewport area/region/window for use with a context override.

    The fix_geometry operator toggles edit mode, which requires a 3D Viewport
    context. When the script is run from the Text Editor, bpy.context points
    at the Text Editor area and the mode toggle fails (or crashes). Return
    a dict suitable for `bpy.context.temp_override(**override)`.

    Returns None if no 3D Viewport is open — caller should abort in that case.
    """
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                for region in area.regions:
                    if region.type == "WINDOW":
                        return {"window": window, "area": area, "region": region}
    return None


def run_fix_geometry(obj, override):
    """Select *obj* only and run the AI Optimizer Fix Geometry step.

    *override* is the dict returned by _find_view3d_override(). The operator
    is called inside a temp_override so it sees a 3D Viewport context.
    """
    props = bpy.context.scene.ai_optimizer
    props.verbose_logging = True
    props.merge_distance_mm = 0.1
    props.recalculate_normals = False
    props.manifold_method = "OFF"
    props.merge_materials = False
    props.join_meshes = False

    with bpy.context.temp_override(**override):
        # Make sure we're in object mode before selection changes.
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        # Deselect everything, then select only *obj*.
        for o in bpy.context.view_layer.objects:
            o.select_set(False)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)

        bpy.ops.ai_optimizer.fix_geometry()


def check(name, before, after, expect_face_drop, expect_vert_drop):
    """Pass if face/vert drops meet the minimums. Both minimums treat the
    expected value as a lower bound — 0 means "any drop >= 0, including a
    larger drop caused by legitimate side effects of the operator".
    """
    f_before, e_before, v_before = before
    f_after, e_after, v_after = after
    face_drop = f_before - f_after
    vert_drop = v_before - v_after
    face_ok = face_drop >= expect_face_drop
    vert_ok = vert_drop >= expect_vert_drop
    status = "PASS" if face_ok and vert_ok else "FAIL"
    print(
        f"\n[{status}] {name}: "
        f"faces {f_before}->{f_after} (drop {face_drop}, expected >= {expect_face_drop}); "
        f"edges {e_before}->{e_after}; "
        f"verts {v_before}->{v_after} (drop {vert_drop}, expected >= {expect_vert_drop})"
    )
    return status == "PASS"


def main():
    if not hasattr(bpy.context.scene, "ai_optimizer"):
        print(
            "\n[ABORT] AI 3D Model Optimizer add-on is not enabled in this "
            "Blender instance. Install build/model-optimizer-addon.py first."
        )
        return

    override = _find_view3d_override()
    if override is None:
        print(
            "\n[ABORT] No 3D Viewport area is open. "
            "Open a default Blender window (with a 3D Viewport visible) "
            "and run the script again."
        )
        return

    _cleanup_previous_run()

    # --- Scenario 1: dissolve_degenerate ---
    obj1 = build_degenerate_mesh()
    before1 = counts(obj1)
    run_fix_geometry(obj1, override)
    after1 = counts(obj1)
    ok1 = check("dissolve_degenerate", before1, after1, expect_face_drop=5, expect_vert_drop=0)

    # --- Scenario 2: remove_doubles ---
    obj2 = build_doubles_mesh()
    before2 = counts(obj2)
    run_fix_geometry(obj2, override)
    after2 = counts(obj2)
    ok2 = check("remove_doubles", before2, after2, expect_face_drop=0, expect_vert_drop=4)

    print("\n" + "=" * 60)
    print(f"dissolve_degenerate: {'PASS' if ok1 else 'FAIL'} | remove_doubles: {'PASS' if ok2 else 'FAIL'}")
    print("=" * 60)


if __name__ == "__main__":
    main()
