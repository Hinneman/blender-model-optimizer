# Topology Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply the high/medium-value subset of the Gemini topology-improvements review to the AI 3D Model Optimizer add-on, in 7 independently testable phases.

**Architecture:** Each phase is an isolated change to the existing multi-file Blender add-on. No new modules. Phases 1-3 are small behavior/UX fixes. Phase 4 adds an auto-sized bake cage. Phase 5 protects UV seams during decimation. Phase 6 snaps origin inside the symmetry step. Phase 7 adds a brand-new pipeline step (floor snap) and its operator.

**Tech Stack:** Blender Python API (`bpy`, `bmesh`, `mathutils`). Python 3.10+. Manual Blender testing (no unit-test harness in this project).

**Convention notes for the executor:**
- **Never commit.** The user commits all changes themselves. Stage/edit only.
- **No worktrees.** Work directly in the main checkout on the current branch.
- **Update `CHANGELOG.md` at the end of each phase.** Add an entry under a new `## [X.Y.Z] - YYYY-MM-DD` heading that matches the version in `pyproject.toml`. Bump `pyproject.toml` when a phase requires it (see the `Version` field on each phase). A pending version that's already been bumped in an earlier phase does not need another bump for a follow-up bug/cleanup phase — just append entries under the existing heading.
- **Build command:** `python build.py` produces `build/model-optimizer-addon.py`. Install via Blender: Edit → Preferences → Add-ons → Install from Disk → select the built `.py`.
- **Lint command:** `ruff check src/` and `ruff format src/`.
- **Reference spec:** [docs/superpowers/specs/2026-04-18-topology-improvements-design.md](../specs/2026-04-18-topology-improvements-design.md).

---

## Phase 1 — Fix `progress_update(0)` No-Op

**Version:** patch bump (e.g. `1.6.2` → `1.6.3`). If a patch bump is already pending in `pyproject.toml` / `CHANGELOG.md`, reuse it.

**Files:**
- Modify: `src/utils.py` (function `log`)
- Modify: `pyproject.toml` (version, if not already bumped)
- Modify: `CHANGELOG.md`

### Task 1.1: Remove the dead `progress_update` call

- [ ] **Step 1: Edit `src/utils.py`.**

Replace the body of `log` (currently [src/utils.py:64-68](../../../src/utils.py#L64-L68)):

```python
def log(context, message, level="INFO"):
    """Log a message to console."""
    print(f"  [AI Optimizer] {message}")
```

Remove the `if hasattr(context, "window_manager")` block entirely. The `context` and `level` parameters stay on the signature for call-site compatibility.

- [ ] **Step 2: Lint and build.**

Run: `ruff check src/` — expect no new errors.
Run: `python build.py` — expect `build/model-optimizer-addon.py` regenerated with no errors.

- [ ] **Step 3: Manual test.**

1. Install the built add-on in Blender.
2. Load any mesh and run the full pipeline.
3. **Expected:** console log messages still appear (`[AI Optimizer] …`). No visible behavior change. Progress panel still updates correctly (it's driven by the modal operator, not by `progress_update`).

### Task 1.2: Changelog + version

- [ ] **Step 1: Check version state.**

Read `pyproject.toml` and `CHANGELOG.md`. If the top changelog entry is an unreleased/pending version newer than the last release tag, reuse it. Otherwise bump the patch number in `pyproject.toml` and add a new `## [X.Y.Z] - 2026-04-18` heading in `CHANGELOG.md`.

- [ ] **Step 2: Add changelog entry.**

Under the `[1.7.0]` heading heading in `CHANGELOG.md`, add a line under `### Fixed`:

```markdown
### Fixed
- Removed a leftover `progress_update(0)` call in `log()` that was a silent no-op (no matching `progress_begin`).
```

- [ ] **Step 3: Report phase complete and stop for user review/commit.**

---

## Phase 2 — Dependency Status Line

**Observation from code.** A status line already exists at [src/panels.py:60-63](../../../src/panels.py#L60-L63) inside the "Model Stats" box. It is gated on `if meshes:` (line 35), so when the scene is empty or nothing is selected, the user sees nothing. The fix is to surface the status line outside that gate so it's always visible when the panel is open.

**Version:** patch bump (reuse the one from Phase 1 if still pending).

**Files:**
- Modify: `src/panels.py` (function `AIOPT_PT_main_panel.draw`)
- Modify: `CHANGELOG.md`

### Task 2.1: Move the dependency status line above the meshes gate

- [ ] **Step 1: Read the current structure.**

Open `src/panels.py` and read lines 23-65 of the `draw` method. Locate the `if meshes:` block starting at line 35 and the existing "3D Print Toolbox" lines inside it (60-63).

- [ ] **Step 2: Remove the existing status lines from inside the `if meshes:` block.**

Delete these four lines inside the meshes box:

```python
            if is_print3d_available():
                col.label(text="3D Print Toolbox: installed", icon="CHECKMARK")
            else:
                col.label(text="3D Print Toolbox: not found", icon="ERROR")
```

- [ ] **Step 3: Add a new top-of-panel row before the Model Stats box.**

Insert immediately after the early-return check (after `if state.is_running or state.step_results != "[]": return`) and before `meshes = get_selected_meshes()`:

```python
        # Dependency status — always visible
        dep_row = layout.row()
        if is_print3d_available():
            dep_row.label(text="3D Print Toolbox available", icon="CHECKMARK")
        else:
            dep_row.label(text="3D Print Toolbox not installed — fallback active", icon="ERROR")
```

- [ ] **Step 4: Lint and build.**

Run: `ruff check src/` and `ruff format src/`.
Run: `python build.py`.

- [ ] **Step 5: Manual test.**

1. Install the rebuilt add-on.
2. In Blender, open a scene with **no selected meshes**. Open the AI Optimizer sidebar. **Expected:** the dependency status line is visible at the top of the panel.
3. With 3D Print Toolbox enabled: **Expected:** `✓ 3D Print Toolbox available`.
4. Disable 3D Print Toolbox via Edit → Preferences → Add-ons. Reopen the sidebar. **Expected:** `⚠ 3D Print Toolbox not installed — fallback active`. No Blender restart required.
5. With a mesh selected, confirm the Model Stats box no longer duplicates the status line.

### Task 2.2: Changelog

- [ ] **Step 1: Add entry.**

Under the `[1.7.0]` heading's `### Changed` section in `CHANGELOG.md`:

```markdown
### Changed
- Dependency status ("3D Print Toolbox installed / not installed") is now always visible at the top of the panel, not only when a mesh is selected.
```

- [ ] **Step 2: Report phase complete and stop for user review/commit.**

---

## Phase 3 — Degenerate Dissolve Pre-Pass

**Version:** patch bump (reuse).

**Files:**
- Modify: `src/geometry.py` (function `fix_geometry_single`)
- Modify: `CHANGELOG.md`

### Task 3.1: Add the dissolve_degenerate pre-pass

- [ ] **Step 1: Edit `src/geometry.py`.**

In `fix_geometry_single` ([src/geometry.py:5-60](../../../src/geometry.py#L5-L60)), insert a call *before* the existing `remove_doubles` at line 23. The file currently looks like:

```python
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")

    # Merge close vertices
    bpy.ops.mesh.remove_doubles(threshold=props.merge_distance_mm / 1000.0)
```

Change to:

```python
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")

    # Pre-pass: collapse zero-area faces and zero-length edges that AI meshes
    # commonly contain. Threshold is intentionally very small — only truly
    # degenerate geometry is affected.
    bpy.ops.mesh.dissolve_degenerate(threshold=1e-6)

    # Merge close vertices
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.remove_doubles(threshold=props.merge_distance_mm / 1000.0)
```

The extra `select_all(action="SELECT")` before `remove_doubles` is defensive: `dissolve_degenerate` can leave the selection in an unexpected state.

- [ ] **Step 2: Lint and build.**

Run: `ruff check src/` and `ruff format src/`.
Run: `python build.py`.

- [ ] **Step 3: Manual test — AI mesh.**

1. Install the rebuilt add-on.
2. Load a test AI mesh (Meshy/Tripo export recommended — they typically contain degenerate geometry).
3. Note face count in Edit Mode statistics.
4. Disable every pipeline step except **Fix Geometry** and run the pipeline.
5. **Expected:** face count drops slightly; the model looks identical in solid shading. No holes, no visible artifacts.

- [ ] **Step 4: Manual test — clean mesh regression.**

1. Add a default cube (Add → Mesh → Cube). Note face count is 6.
2. Run Fix Geometry.
3. **Expected:** face count is still 6. `dissolve_degenerate` is a no-op on clean geometry.

### Task 3.2: Changelog

- [ ] **Step 1: Add entry.**

Under `### Added` (`[1.7.0]` heading):

```markdown
### Added
- `Fix Geometry` now includes a degenerate-dissolve pre-pass (threshold 1e-6) that removes zero-area faces and zero-length edges common in AI-generated meshes before the existing merge-by-distance step.
```

- [ ] **Step 2: Report phase complete and stop for user review/commit.**

---

## Phase 4 — Auto-Ray Distance for Normal Bake

**Version:** minor bump (new property). e.g. `1.6.x` → `1.7.0`. Update `pyproject.toml`.

**Files:**
- Modify: `src/properties.py` (add `auto_cage_extrusion`)
- Modify: `src/utils.py` (add to `SAVEABLE_PROPS`)
- Modify: `src/geometry.py` (function `bake_normal_map_for_decimate`)
- Modify: `src/panels.py` (gate the manual `cage_extrusion_mm` field)
- Modify: `pyproject.toml` (version)
- Modify: `CHANGELOG.md`

### Task 4.1: Add the `auto_cage_extrusion` property

- [ ] **Step 1: Edit `src/properties.py`.**

Immediately *before* the existing `cage_extrusion_mm: FloatProperty(` block at line 166, insert:

```python
    auto_cage_extrusion: BoolProperty(
        name="Auto Cage Distance",
        default=True,
        description="Automatically size the bake ray distance as 1% of the mesh bounding-box diagonal. Disable to set the distance manually",
    )
```

- [ ] **Step 2: Register property in SAVEABLE_PROPS.**

In `src/utils.py`, add `"auto_cage_extrusion",` to the `SAVEABLE_PROPS` list (keep alphabetical or insert next to `cage_extrusion_mm`).

- [ ] **Step 3: Lint.**

Run: `ruff check src/`.

### Task 4.2: Use the property in the bake function

- [ ] **Step 1: Edit `src/geometry.py`.**

In `bake_normal_map_for_decimate` ([src/geometry.py:388-470](../../../src/geometry.py#L388-L470)), replace the single-line `cage_extrusion` calculation at line 442:

```python
            cage_extrusion=props.cage_extrusion_mm / 1000.0,
```

with:

```python
            cage_extrusion=_compute_cage_extrusion(obj, props),
```

Then add this helper function immediately above `bake_normal_map_for_decimate` (around line 387):

```python
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
```

- [ ] **Step 2: Lint and build.**

Run: `ruff check src/` and `ruff format src/`.
Run: `python build.py`.

### Task 4.3: Gate the manual field in the panel

- [ ] **Step 1: Edit `src/panels.py`.**

Find the bake-normal-map UI block ([src/panels.py:446-449](../../../src/panels.py#L446-L449)):

```python
        col.prop(props, "bake_normal_map")
        if props.bake_normal_map:
            col.prop(props, "normal_map_resolution", text="")
            col.prop(props, "cage_extrusion_mm")
```

Replace with:

```python
        col.prop(props, "bake_normal_map")
        if props.bake_normal_map:
            col.prop(props, "normal_map_resolution", text="")
            col.prop(props, "auto_cage_extrusion")
            if not props.auto_cage_extrusion:
                col.prop(props, "cage_extrusion_mm")
```

- [ ] **Step 2: Lint and build.**

Run: `ruff check src/` and `ruff format src/`.
Run: `python build.py`.

### Task 4.4: Manual test

- [ ] **Step 1: Small mesh test.**

1. Install the rebuilt add-on.
2. Add a default cube. Scale to ~10cm (Dimensions ≈ 0.1 m).
3. Enable Decimate + Bake Normal Map. `auto_cage_extrusion` should be on by default.
4. Run Decimate + bake.
5. **Expected:** normal map baked without black patches. The baked cage extrusion ≈ 0.001 m (1% of 0.1).

- [ ] **Step 2: Large mesh test.**

1. Reload scene, add a cube, scale to 50 m.
2. Run the same pipeline.
3. **Expected:** still clean bake. Auto extrusion ≈ 0.5 m.

- [ ] **Step 3: Manual override test.**

1. Disable `auto_cage_extrusion` in the panel.
2. **Expected:** `Cage Extrusion (mm)` field becomes visible and editable.
3. Set it to 5mm and run bake. **Expected:** works as before.

- [ ] **Step 4: Save/load defaults test.**

1. Toggle `auto_cage_extrusion` off. Click "Save Defaults".
2. Restart Blender, open the sidebar. **Expected:** toggle is still off.

### Task 4.5: Version bump + changelog

- [ ] **Step 1: Bump `pyproject.toml` version** to the next minor (e.g., `1.6.2` → `1.7.0`).

- [ ] **Step 2: Add changelog entry.**

Add a new `## [1.7.0] - 2026-04-18` heading (or match whatever version you chose) with:

```markdown
### Added
- `Auto Cage Distance` option for the normal-map bake: ray distance is automatically set to 1% of the mesh bounding-box max dimension. Works correctly regardless of model scale. Disable to fall back to the manual `Cage Extrusion (mm)` field.
```

- [ ] **Step 3: Report phase complete and stop for user review/commit.**

---

## Phase 5 — UV Seam Protection Before Decimate

**Version:** reuse Phase 4's minor version if still pending; else bump patch.

**Files:**
- Modify: `src/geometry.py` (function `decimate_single`, add helper)
- Modify: `CHANGELOG.md`

### Task 5.1: Add seam-marking helper

- [ ] **Step 1: Edit `src/geometry.py`.**

Add a new helper function immediately above `decimate_single` (around line 472):

```python
def _protect_uv_seams(obj):
    """Mark UV-island boundaries as Sharp so DECIMATE preserves the UV layout.

    No-op when the mesh has no UV map. Auto-generates seams from islands when
    the mesh has UVs but no explicit seams. Called before the DECIMATE
    modifier is applied — the modifier respects Sharp edges as collapse
    boundaries, which prevents texture smearing across UV seams.
    """
    import bmesh

    if not obj.data.uv_layers:
        return

    # Auto-mark seams from islands if none exist yet
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    has_seams = any(e.seam for e in bm.edges)
    bm.free()

    if not has_seams:
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.seams_from_islands()
        bpy.ops.object.mode_set(mode="OBJECT")

    # Mark all seam edges as Sharp
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    for edge in bm.edges:
        if edge.seam:
            edge.smooth = False  # smooth=False means "sharp"
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
```

- [ ] **Step 2: Call the helper from `decimate_single`.**

In `decimate_single` ([src/geometry.py:473-499](../../../src/geometry.py#L473-L499)), insert the helper call *before* the `Decimate_Optimize` modifier is added (before line 486):

```python
    # Pre-pass: dissolve nearly-coplanar faces (cleans flat surfaces, preserves UVs)
    if props.dissolve_angle > 0:
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.dissolve_limited(angle_limit=props.dissolve_angle, delimit={"UV"})
        bpy.ops.object.mode_set(mode="OBJECT")

    # Protect UV seams: mark island boundaries Sharp so DECIMATE doesn't
    # collapse edges that define the texture layout.
    _protect_uv_seams(obj)

    mod = obj.modifiers.new(name="Decimate_Optimize", type="DECIMATE")
```

- [ ] **Step 3: Lint and build.**

Run: `ruff check src/` and `ruff format src/`.
Run: `python build.py`.

### Task 5.2: Manual test

- [ ] **Step 1: Textured AI mesh test.**

1. Install the rebuilt add-on.
2. Load an AI mesh that has a visible texture and known UV seams (Meshy export works well).
3. Enable **only** Decimate (disable Fix Geometry, Clean Images, etc.) with ratio 0.3.
4. Run the pipeline.
5. **Expected:** texture remains aligned on the decimated mesh. No visible smears at UV boundaries.

- [ ] **Step 2: No-UV regression test.**

1. Add a default cube (no UV map). Delete any auto-generated UVs (Object Data Properties → UV Maps → remove).
2. Run Decimate at 0.5.
3. **Expected:** decimation runs, no error in console.

- [ ] **Step 3: Already-seamed mesh test.**

1. Add a UV Sphere with auto UVs (it has seams by default).
2. Run Decimate at 0.5.
3. **Expected:** decimation runs, original seams preserved, no duplicate seams added. Check Mesh Analyze shows no new issues.

### Task 5.3: Changelog

- [ ] **Step 1: Add entry.**

Under `### Changed` (`[1.7.0]` heading):

```markdown
### Changed
- Decimate step now protects UV seams by marking island boundaries as Sharp before the DECIMATE modifier runs. Prevents texture smearing on AI meshes whose UV layout would otherwise be destroyed by edge collapse. Meshes without UVs are unaffected.
```

- [ ] **Step 2: Report phase complete and stop for user review/commit.**

---

## Phase 6 — Symmetry Origin Snap

**Version:** reuse the pending version.

**Files:**
- Modify: `src/geometry.py` (function `detect_and_apply_symmetry`)
- Modify: `CHANGELOG.md`

### Task 6.1: Snap object origin to symmetry plane before mirror

- [ ] **Step 1: Edit `src/geometry.py`.**

In `detect_and_apply_symmetry` ([src/geometry.py:309-385](../../../src/geometry.py#L309-L385)), the score check passes at line 362-366 and then vertex deletion runs at 369-374, then the Mirror modifier is added at 377. The origin snap must happen *after* vertex deletion and *before* the Mirror modifier.

Insert after line 374 (`obj.data.update()`) and before line 377 (`mod = obj.modifiers.new(...)`):

```python
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
```

- [ ] **Step 2: Lint and build.**

Run: `ruff check src/` and `ruff format src/`.
Run: `python build.py`.

### Task 6.2: Manual test

- [ ] **Step 1: Off-plane origin test.**

1. Install the rebuilt add-on.
2. Load any roughly-symmetric mesh (a humanoid AI export works well).
3. Move its origin off the symmetry plane: Object → Set Origin → Origin to 3D Cursor, after placing the cursor at an arbitrary offset point.
4. Enable **only** Symmetry with axis X and min_score 0.80.
5. Run the pipeline.
6. **Expected:** mirrored mesh has no visible gap or overlap at the symmetry plane. Compare against the same flow on a commit before this phase — that version will show an obvious seam or overlap.

- [ ] **Step 2: On-plane origin regression test.**

1. Undo to restore the mesh, then Object → Set Origin → Origin to Geometry.
2. Enable Symmetry and run.
3. **Expected:** identical result to pre-phase behavior — the snap is effectively a no-op when origin is already on the plane.

- [ ] **Step 3: Below-threshold test.**

1. Use a non-symmetric mesh (default monkey Suzanne rotated 30° on Y).
2. Set symmetry min_score to 0.95. Run.
3. **Expected:** symmetry skipped (score too low); no origin change should happen, because the snap is inside the success branch. Verify object location is unchanged.

### Task 6.3: Changelog

- [ ] **Step 1: Add entry.**

Under `### Fixed`:

```markdown
### Fixed
- Symmetry Mirror step now snaps the object origin onto the detected symmetry plane before applying the Mirror modifier. Prevents gaps or overlaps at the seam when the source AI mesh's origin is not already on the symmetry axis.
```

- [ ] **Step 2: Report phase complete and stop for user review/commit.**

---

## Phase 7 — Floor Snap Pipeline Step

**Version:** reuse Phase 4's minor version (it already adds a property in the same release cycle).

This is the largest phase. It adds a new pipeline step, which means: new property, new operator, new registered class, new modal setup/tick/teardown trio, new panel entry.

**Files:**
- Modify: `src/properties.py` (add `run_floor_snap`)
- Modify: `src/utils.py` (add to `SAVEABLE_PROPS`)
- Create/Modify: `src/geometry.py` (add `floor_snap_all` helper)
- Modify: `src/operators.py` (add `AIOPT_OT_floor_snap` + pipeline step trio)
- Modify: `src/__init__.py` (register new operator)
- Modify: `src/panels.py` (add toggle to the pipeline section)
- Modify: `CHANGELOG.md`

### Task 7.1: Add the helper function in `geometry.py`

- [ ] **Step 1: Edit `src/geometry.py`.**

Append this function at the end of the file:

```python
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
```

- [ ] **Step 2: Lint.**

Run: `ruff check src/geometry.py`.

### Task 7.2: Add the property

- [ ] **Step 1: Edit `src/properties.py`.**

In the `# -- Pipeline toggles --` section at the top of `AIOPT_Properties`, add (order: after `run_decimate`, before `run_clean_images`, matching pipeline order):

```python
    run_floor_snap: BoolProperty(
        name="Floor Snap",
        default=True,
        description="Translate the model so its lowest point sits at Z=0 (world floor). XY position is unchanged",
    )
```

- [ ] **Step 2: Register in `SAVEABLE_PROPS`.**

In `src/utils.py`, add `"run_floor_snap",` to `SAVEABLE_PROPS`.

### Task 7.3: Add the standalone operator

- [ ] **Step 1: Edit `src/operators.py`.**

Add import for `floor_snap_all` in the existing import block from `.geometry` at the top (lines 8-15):

```python
from .geometry import (
    bake_normal_map_for_decimate,
    decimate_single,
    detect_and_apply_symmetry,
    fix_geometry_single,
    floor_snap_all,
    remove_interior_single,
    remove_small_pieces_single,
)
```

Append this operator class near the end of the file, directly after `AIOPT_OT_remove_small_pieces` (around line 1050):

```python
class AIOPT_OT_floor_snap(Operator):
    bl_idname = "ai_optimizer.floor_snap"
    bl_label = "Floor Snap"
    bl_description = "Translate selected meshes so the lowest vertex sits at Z=0. XY position is unchanged"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        meshes = get_selected_meshes()
        if not meshes:
            self.report({"ERROR"}, "No mesh objects found")
            return {"CANCELLED"}

        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        shift = floor_snap_all(meshes)
        if shift == 0.0:
            self.report({"INFO"}, "Floor snap: no shift needed")
        else:
            self.report({"INFO"}, f"Floor snap: shifted {shift * 1000:.1f} mm up")
        return {"FINISHED"}
```

### Task 7.4: Register the operator in the add-on

- [ ] **Step 1: Edit `src/__init__.py` — add to imports.**

The `from .operators import (...)` block is alphabetical. Insert `AIOPT_OT_floor_snap,` between `AIOPT_OT_fix_geometry,` and `AIOPT_OT_load_defaults,`:

```python
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
    AIOPT_OT_remove_interior,
    AIOPT_OT_remove_small_pieces,
    AIOPT_OT_reset_defaults,
    AIOPT_OT_resize_textures,
    AIOPT_OT_run_all,
    AIOPT_OT_save_defaults,
    AIOPT_OT_show_stats,
    AIOPT_OT_symmetry_mirror,
)
```

- [ ] **Step 2: Edit `src/__init__.py` — add to `classes` tuple.**

The `classes` tuple follows pipeline/display order, not alphabetical. Floor Snap runs between Decimate and Clean Images in the pipeline, so insert `AIOPT_OT_floor_snap,` between `AIOPT_OT_decimate,` and `AIOPT_OT_clean_images,`:

```python
    AIOPT_OT_decimate,
    AIOPT_OT_floor_snap,
    AIOPT_OT_clean_images,
```

- [ ] **Step 3: Lint and build.**

Run: `ruff check src/` and `python build.py`.

### Task 7.5: Wire into the modal pipeline

- [ ] **Step 1: Edit `src/operators.py` — add the step to the builder.**

In `AIOPT_OT_run_all.invoke` ([src/operators.py:291-369](../../../src/operators.py#L291-L369)), add the step registration between `run_decimate` and `run_clean_images` (pipeline ordering):

Find:

```python
        if props.run_decimate:
            self._steps.append(("Decimate", self._setup_decimate, self._tick_decimate, self._teardown_decimate))
        if props.run_clean_images:
```

Insert between them:

```python
        if props.run_floor_snap:
            self._steps.append(
                (
                    "Floor Snap",
                    self._setup_floor_snap,
                    self._tick_floor_snap,
                    self._teardown_floor_snap,
                )
            )
```

- [ ] **Step 2: Add the setup/tick/teardown trio methods.**

In the same class, find the symmetry trio (`_setup_symmetry` / `_tick_symmetry` / `_teardown_symmetry` around line 699-725) and add a new floor-snap trio immediately after it. Floor snap operates on all meshes as a group — it's a single-step action, so `setup` returns `1` and `tick` does all the work on `index == 0`:

```python
    # -- Floor Snap --

    def _setup_floor_snap(self, context):
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        self._floor_snap_meshes = get_selected_meshes()
        self._floor_snap_shift = 0.0
        # Single tick — operates on the group, not per-object.
        return 1 if self._floor_snap_meshes else 0

    def _tick_floor_snap(self, context, index):
        self._floor_snap_shift = floor_snap_all(self._floor_snap_meshes, token=self._token)
        return f"Shifted {self._floor_snap_shift * 1000:.1f} mm"

    def _teardown_floor_snap(self, context):
        if self._floor_snap_shift == 0.0:
            return "Floor snap: no shift needed"
        return f"Floor snap: shifted {self._floor_snap_shift * 1000:.1f} mm up"
```

- [ ] **Step 3: Declare the runtime attributes.**

Near the top of `AIOPT_OT_run_all` (where `_interior_removed`, `_small_pieces_deleted` etc. are declared around line 276-279), add:

```python
    _floor_snap_meshes: list
    _floor_snap_shift: float
```

- [ ] **Step 4: Lint and build.**

Run: `ruff check src/` and `python build.py`.

### Task 7.6: Add the panel toggle

- [ ] **Step 1: Edit `src/panels.py`.**

Find the existing pipeline toggles section. Locate the `col.prop(props, "run_decimate")` line and insert after it:

```python
        col.prop(props, "run_floor_snap")
```

(Use `Grep` for `run_decimate` in panels.py to find the exact location — it's in the main panel's pipeline section.)

- [ ] **Step 2: Lint and build.**

Run: `ruff check src/` and `python build.py`.

### Task 7.7: Manual test

- [ ] **Step 1: Standalone operator test.**

1. Install the rebuilt add-on.
2. Add a default cube. In the N-panel, set its Z location to `-0.7` so the bottom is at `-1.7` and top at `0.3`.
3. Click the new **Floor Snap** button (in the panel) or invoke it via F3 search.
4. **Expected:** cube's Z location changes to `1.0 - 0.7 + 0.7 = 1.0` — wait, simpler: bottom was at `-1.7`, so shift = `1.7`; new Z = `-0.7 + 1.7 = 1.0`; bottom now at `1.0 - 1.0 = 0.0`. Verify the cube's bottom face sits on Z=0 in the viewport.
5. XY should be unchanged (X=0, Y=0).

- [ ] **Step 2: Pipeline toggle test.**

1. Load a centered AI mesh (typical Meshy output — centered on origin, half above Z=0, half below).
2. Verify the **Floor Snap** toggle is visible under the pipeline section, default ON.
3. Run the full pipeline.
4. **Expected:** final mesh's lowest point is at Z=0 after the pipeline finishes.

- [ ] **Step 3: Multi-object group test.**

1. Add two cubes at different heights (cube A at Z=5, cube B at Z=10).
2. Select both and run Floor Snap.
3. **Expected:** both cubes shift by the same amount. Cube A's bottom sits at Z=0; cube B is still 5 units higher than cube A. Their relative heights are preserved.

- [ ] **Step 4: Toggle-off regression test.**

1. Disable `run_floor_snap` in the panel.
2. Run the pipeline on a centered mesh.
3. **Expected:** mesh remains centered (pre-phase behavior).

- [ ] **Step 5: Save/load defaults test.**

1. Toggle `run_floor_snap` off. Click "Save Defaults".
2. Restart Blender, open the sidebar.
3. **Expected:** toggle is still off.

- [ ] **Step 6: Cancellation test.**

1. Select many meshes (to make the loop non-instant, though floor snap is fast).
2. Run the pipeline and press ESC during the Floor Snap step.
3. **Expected:** pipeline cancels cleanly (handled by the existing undo rollback).

### Task 7.8: Version bump + changelog

- [ ] **Step 1: Ensure `pyproject.toml` reflects the version chosen for Phase 4.**

This phase reuses the same release (single minor bump across phases 4+7). If Phase 4 already bumped to `1.7.0`, leave the version alone.

- [ ] **Step 2: Add changelog entry.**

Under the `[1.7.0]` heading's `### Added`:

```markdown
### Added
- New pipeline step `Floor Snap`: translates the selected meshes so the lowest world-space vertex sits at Z=0, leaving XY unchanged. Useful for AI exports that arrive centered on origin. Runs between Decimate and Clean Images. Available as a standalone operator (`ai_optimizer.floor_snap`) and as a toggleable pipeline step, default ON.
```

- [ ] **Step 3: Report phase complete and stop for user review/commit.**

---

## Final Wrap-Up

After Phase 7 is committed:

- [ ] Sanity-check: confirm all added `SAVEABLE_PROPS` entries (`auto_cage_extrusion`, `run_floor_snap`) load cleanly after a full Save Defaults → Blender restart → Load Defaults cycle.
- [ ] Run a full end-to-end pipeline on a real AI export (Meshy or Tripo) with every step enabled. Expect a clean decimated, mirrored, floor-snapped, exported GLB.
- [ ] Leave the branch state ready for the user to merge/tag as they see fit — the user handles commits and releases.
