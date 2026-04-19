# Hard UV Seam Constraint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the soft-hint `Protect UV Seams` mechanism with a topological seam-split-and-restitch approach so the decimator physically cannot collapse across UV island boundaries, eliminating texture smearing after decimate.

**Architecture:** Split the mesh topologically along UV seams before decimation (duplicating seam vertices into per-island copies), run the existing N-pass COLLAPSE DECIMATE against the split mesh, then merge-by-distance scoped only to the recorded seam positions using a tight tolerance. This replaces the `_protect_uv_seams` soft constraint with a hard topological one. The `protect_uv_seams` user-facing toggle is removed since the behavior is now always-on when UVs are present.

**Tech Stack:** Python 3, Blender `bpy` + `bmesh` APIs, Blender 4.0+ add-on conventions.

**Reference spec:** [docs/superpowers/specs/2026-04-19-hard-uv-seam-constraint-design.md](docs/superpowers/specs/2026-04-19-hard-uv-seam-constraint-design.md)

---

## Background for the Implementer

You are modifying an installed Blender add-on. Testing happens by building the single-file add-on and running it inside Blender — there is no Python unit-test suite for this project (confirmed by the existing convention: features are validated manually in Blender).

**Key files you will touch:**

- [src/geometry.py](src/geometry.py) — contains `_protect_uv_seams` and `decimate_single`. Core changes here.
- [src/properties.py](src/properties.py) — remove the `protect_uv_seams` BoolProperty.
- [src/panels.py](src/panels.py) — remove the UI row for `protect_uv_seams`.
- [CHANGELOG.md](CHANGELOG.md) — update the unreleased `## [1.8.0]` section.

**Do not touch:** the version string in `pyproject.toml` (stays 1.8.0). The `Planar Post-Pass` and `Passes` features added in 1.8.0 stay.

**Git rules (from CLAUDE.md):** never run `git commit`. Stage files with `git add` but let the user commit. Do not create worktrees. Work directly on the current branch.

**How to run lint:** `ruff check src/` and `ruff format src/` — config in `pyproject.toml`.

**How to build:** `python build.py` produces `build/model-optimizer-addon.py`. Install in Blender via Edit → Preferences → Add-ons → Install from Disk.

---

## File Structure

**Modified:**
- `src/geometry.py` — replace `_protect_uv_seams` with `_split_uv_seams`; add `_restitch_seams`; modify `decimate_single` to call both unconditionally.
- `src/properties.py` — remove `protect_uv_seams` BoolProperty.
- `src/panels.py` — remove the `protect_uv_seams` UI row.
- `CHANGELOG.md` — edit the 1.8.0 section per the spec.

No new files are created. The split/restitch helpers live next to `decimate_single` in `geometry.py` because they are internal to the decimate step and share its concerns.

---

## Task 1: Remove the `protect_uv_seams` property

**Files:**
- Modify: `src/properties.py:168-177`

- [ ] **Step 1: Delete the `protect_uv_seams` BoolProperty definition**

In [src/properties.py](src/properties.py), locate the block:

```python
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
```

Delete those 10 lines entirely. The surrounding context is:

```python
    decimate_passes: IntProperty(
        ...
    )
    # <-- protect_uv_seams block was here -->
    run_planar_postpass: BoolProperty(
        ...
    )
```

- [ ] **Step 2: Verify no other references exist in the codebase**

Run:

```bash
grep -rn "protect_uv_seams" src/
```

Expected output after the edit: one remaining reference in `src/panels.py` (handled in Task 2) and one reference in `src/geometry.py` inside `decimate_single` (handled in Task 4). No other references.

- [ ] **Step 3: Stage the change**

```bash
git add src/properties.py
```

Do NOT commit. The user commits.

---

## Task 2: Remove the `protect_uv_seams` UI row

**Files:**
- Modify: `src/panels.py:449-450`

- [ ] **Step 1: Delete the UI row and the separator that precedes it**

In [src/panels.py](src/panels.py), locate lines around 449-450:

```python
        layout.separator()
        layout.prop(props, "protect_uv_seams")
```

Context:

```python
                col.label(text=f"Per-pass ratio: {per_pass:.3f} \u00d7 {props.decimate_passes}")

        layout.separator()
        layout.prop(props, "protect_uv_seams")

        col = layout.column(align=True)
        col.prop(props, "run_planar_postpass")
```

Delete the two lines: the `layout.separator()` call that was added specifically for this toggle, and the `layout.prop(props, "protect_uv_seams")` line. Result:

```python
                col.label(text=f"Per-pass ratio: {per_pass:.3f} \u00d7 {props.decimate_passes}")

        col = layout.column(align=True)
        col.prop(props, "run_planar_postpass")
```

Note: the existing `col = layout.column(align=True)` line already provides visual grouping for the planar post-pass, so dropping the separator does not harm the layout.

- [ ] **Step 2: Verify the reference is gone**

Run:

```bash
grep -n "protect_uv_seams" src/panels.py
```

Expected output: nothing (exit code 1, no matches).

- [ ] **Step 3: Stage the change**

```bash
git add src/panels.py
```

---

## Task 3: Replace `_protect_uv_seams` with `_split_uv_seams`

**Files:**
- Modify: `src/geometry.py:536-569`

This task replaces the soft-hint helper (marks seams Sharp) with a topological helper (physically splits the mesh along seams).

- [ ] **Step 1: Replace the `_protect_uv_seams` function with `_split_uv_seams`**

In [src/geometry.py](src/geometry.py), locate the existing function at line 536:

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

Replace the entire function with:

```python
def _split_uv_seams(obj):
    """Split the mesh topologically along UV seams.

    After calling this, vertices that sat on a UV island boundary are
    duplicated into disconnected copies (one per incident island). Since
    the DECIMATE modifier can only collapse shared edges, collapses across
    seams become physically impossible.

    Returns a list of quantized world-space positions (tuples of 3 floats,
    rounded to QUANTIZATION_DP decimal places) for every vertex that was
    incident to a seam edge before splitting. The caller uses these to
    scope the post-decimate restitch pass.

    No-op when the mesh has no UV map. Returns an empty list in that case
    and in the case where no seams are detected.
    """
    import bmesh

    if not obj.data.uv_layers:
        return []

    # Auto-mark seams from islands if none exist yet. Mirrors the old
    # _protect_uv_seams behavior so that meshes with UVs but no explicit
    # seams still get protection.
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    has_seams = any(e.seam for e in bm.edges)
    bm.free()

    if not has_seams:
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.seams_from_islands()
        bpy.ops.object.mode_set(mode="OBJECT")

    # Record seam-vertex positions in object-local space. Quantize so the
    # restitch pass can hash them without float-equality headaches. The
    # mesh has not been modified yet, so these positions are stable.
    seam_positions = []
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    seam_vert_indices = set()
    for edge in bm.edges:
        if edge.seam:
            for v in edge.verts:
                seam_vert_indices.add(v.index)
    for idx in seam_vert_indices:
        co = bm.verts[idx].co
        seam_positions.append((round(co.x, 6), round(co.y, 6), round(co.z, 6)))
    bm.free()

    if not seam_positions:
        return []

    # Select seam edges and edge-split them. This duplicates shared verts
    # into per-island copies. The quantized positions above still match
    # both copies since they start at identical coordinates.
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="DESELECT")
    bpy.ops.mesh.select_mode(type="EDGE")
    bpy.ops.object.mode_set(mode="OBJECT")

    for edge in obj.data.edges:
        edge.select = edge.use_seam

    bpy.ops.object.mode_set(mode="EDIT")
    try:
        bpy.ops.mesh.edge_split(type="EDGE")
    except RuntimeError as exc:
        # Degenerate meshes can make edge_split fail. Fall back to no-op
        # behavior: decimate proceeds without seam protection.
        print(f"  [AI Optimizer] edge_split failed, skipping seam protection: {exc}")
        bpy.ops.object.mode_set(mode="OBJECT")
        return []
    bpy.ops.object.mode_set(mode="OBJECT")

    return seam_positions
```

Key behaviors to preserve from the original:
- No-op when `obj.data.uv_layers` is empty — returns `[]`.
- Auto-generates seams via `bpy.ops.uv.seams_from_islands()` when none exist.

New behaviors:
- Records vertex positions **before** splitting, so both copies of each split vertex share the same recorded position.
- Uses `edge.use_seam` (the Mesh API name) when iterating `obj.data.edges`; the BMesh attribute name is `edge.seam`. Both are valid but refer to the same flag through different APIs — do not mix them inside one block.
- Wraps `edge_split` in try/except per the spec's error-handling requirement.

- [ ] **Step 2: Run the linter**

```bash
ruff check src/geometry.py
ruff format src/geometry.py
```

Expected: no errors; formatter may reflow whitespace.

- [ ] **Step 3: Stage the change**

```bash
git add src/geometry.py
```

---

## Task 4: Add `_restitch_seams` helper

**Files:**
- Modify: `src/geometry.py` (add a new function immediately after `_split_uv_seams`)

- [ ] **Step 1: Add the `_restitch_seams` function**

Insert this function into [src/geometry.py](src/geometry.py) immediately after `_split_uv_seams` (i.e. before `decimate_single`):

```python
def _restitch_seams(obj, seam_positions, threshold_m):
    """Re-weld vertices that were split by _split_uv_seams.

    Scoped merge-by-distance: only vertices whose current position is
    within *threshold_m* of any recorded seam position are considered.
    Unrelated nearby vertices (e.g. two originally-distinct surfaces that
    happened to end up close after decimation) are not touched.

    ``seam_positions`` is the list returned by _split_uv_seams (quantized
    object-local coords). When empty, this function is a no-op.
    """
    import bmesh

    if not seam_positions:
        return

    # Build a spatial hash of seam positions at cell size *threshold_m*.
    # A vertex is a candidate for welding iff its own cell or any of the
    # 26 neighbor cells contains a recorded seam position.
    cell = max(threshold_m, 1e-9)

    def key_for(co):
        return (int(co[0] / cell), int(co[1] / cell), int(co[2] / cell))

    seam_cells = set()
    for pos in seam_positions:
        seam_cells.add(key_for(pos))

    bm = bmesh.new()
    bm.from_mesh(obj.data)

    candidates = []
    for v in bm.verts:
        k = key_for((v.co.x, v.co.y, v.co.z))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    if (k[0] + dx, k[1] + dy, k[2] + dz) in seam_cells:
                        candidates.append(v)
                        break
                else:
                    continue
                break
            else:
                continue
            break

    unwelded_before = len(candidates)
    if candidates:
        bmesh.ops.remove_doubles(bm, verts=candidates, dist=threshold_m)

    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()

    # Log a warning if a sizable fraction of seam candidates failed to
    # merge — usually means decimation drifted them apart by more than
    # *threshold_m*. Does not abort; caller continues the pipeline.
    if unwelded_before:
        after_count = len(obj.data.vertices)
        print(
            f"  [AI Optimizer] Seam restitch: {unwelded_before} candidate vertices "
            f"near seams, mesh now has {after_count} vertices total"
        )
```

Design notes for the implementer:
- The 3×3×3 neighborhood scan handles the case where a vertex is one cell-width away from the seam-position cell but still within Euclidean distance `threshold_m`.
- `bmesh.ops.remove_doubles` merges verts whose pairwise distance is ≤ `dist`. This is the Blender-native scoped weld.
- The function does not call `bpy.ops.mesh.remove_doubles` because that operator has no vertex-scoped variant — it operates on the current selection globally, which defeats the purpose of this design.

- [ ] **Step 2: Run the linter**

```bash
ruff check src/geometry.py
ruff format src/geometry.py
```

Expected: no errors.

- [ ] **Step 3: Stage the change**

```bash
git add src/geometry.py
```

---

## Task 5: Wire `_split_uv_seams` and `_restitch_seams` into `decimate_single`

**Files:**
- Modify: `src/geometry.py:604-627` (approximately — the section inside `decimate_single` that currently references `protect_uv_seams`)

- [ ] **Step 1: Replace the conditional `_protect_uv_seams` block with the unconditional split-and-restitch flow**

In [src/geometry.py](src/geometry.py), locate the existing section inside `decimate_single`:

```python
    # Pre-pass: dissolve nearly-coplanar faces (cleans flat surfaces, preserves UVs)
    if props.dissolve_angle > 0:
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.dissolve_limited(angle_limit=props.dissolve_angle, delimit={"UV"})
        bpy.ops.object.mode_set(mode="OBJECT")

    # Protect UV seams: mark island boundaries Sharp so DECIMATE doesn't
    # collapse edges that define the texture layout. Run once — seam and
    # sharp flags are preserved by subsequent collapses. Off by default:
    # AI-generated meshes typically have fragmented UVs that create fan
    # artifacts when seams are protected.
    if getattr(props, "protect_uv_seams", False):
        _protect_uv_seams(obj)

    for _ in range(passes):
        mod = obj.modifiers.new(name="Decimate_Optimize", type="DECIMATE")
        mod.decimate_type = "COLLAPSE"
        mod.ratio = per_pass_ratio
        mod.use_collapse_triangulate = True
        bpy.ops.object.modifier_apply(modifier=mod.name)

    # Optional planar post-pass: merge adjacent near-coplanar faces into
    # n-gons. Reduces triangle count in flat regions without touching curved
    # surfaces. delimit={"UV"} preserves UV island boundaries natively.
    if getattr(props, "run_planar_postpass", True) and props.planar_angle > 0:
        mod = obj.modifiers.new(name="Decimate_Planar", type="DECIMATE")
        mod.decimate_type = "DISSOLVE"
        mod.angle_limit = props.planar_angle
        mod.delimit = {"UV"}
        bpy.ops.object.modifier_apply(modifier=mod.name)
```

Replace it with:

```python
    # Pre-pass: dissolve nearly-coplanar faces (cleans flat surfaces, preserves UVs)
    if props.dissolve_angle > 0:
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.dissolve_limited(angle_limit=props.dissolve_angle, delimit={"UV"})
        bpy.ops.object.mode_set(mode="OBJECT")

    # Hard UV seam constraint: topologically split the mesh along UV
    # island boundaries so the COLLAPSE modifier physically cannot collapse
    # across them. Restitched after the collapse passes. No-op on meshes
    # without UVs. See docs/superpowers/specs/2026-04-19-hard-uv-seam-constraint-design.md
    seam_positions = _split_uv_seams(obj)

    for _ in range(passes):
        mod = obj.modifiers.new(name="Decimate_Optimize", type="DECIMATE")
        mod.decimate_type = "COLLAPSE"
        mod.ratio = per_pass_ratio
        mod.use_collapse_triangulate = True
        bpy.ops.object.modifier_apply(modifier=mod.name)

    # Restitch seams: re-weld the vertex pairs that _split_uv_seams
    # duplicated. Tolerance must be tighter than the user's global merge
    # distance so we don't accidentally weld unrelated geometry near former
    # seams. 0.01 mm ceiling is well below any realistic mesh feature.
    restitch_threshold_m = min(props.merge_distance_mm, 0.01) / 1000.0
    _restitch_seams(obj, seam_positions, restitch_threshold_m)

    # Optional planar post-pass: merge adjacent near-coplanar faces into
    # n-gons. Reduces triangle count in flat regions without touching curved
    # surfaces. delimit={"UV"} preserves UV island boundaries natively.
    if getattr(props, "run_planar_postpass", True) and props.planar_angle > 0:
        mod = obj.modifiers.new(name="Decimate_Planar", type="DECIMATE")
        mod.decimate_type = "DISSOLVE"
        mod.angle_limit = props.planar_angle
        mod.delimit = {"UV"}
        bpy.ops.object.modifier_apply(modifier=mod.name)
```

- [ ] **Step 2: Update the `decimate_single` docstring**

The current docstring (lines ~572-589) says:

```python
    """Dissolve coplanar faces, collapse-decimate, optionally planar-dissolve *obj*.

    When ``props.decimate_passes > 1``, collapse decimation is split into N passes
    targeting ``props.decimate_ratio`` overall. Per-pass ratio is
    ``decimate_ratio ** (1/passes)`` so the cumulative ratio closely approximates
    the final ratio. The dissolve pre-pass and UV seam protection run once
    up front; only the COLLAPSE modifier runs per iteration, so the quadric
    solver recomputes its error field between passes without re-constraining
    already-protected seams.

    When ``props.run_planar_postpass`` is True, a planar (DISSOLVE) decimate
    modifier runs once after the collapse loop to merge near-coplanar faces
    into n-gons (angle threshold ``props.planar_angle``). This reduces triangle
    count in flat regions without touching curved surfaces.

    The remove-doubles / normals / delete-loose cleanup runs once at the end.
    """
```

Update it to reflect the new mechanism:

```python
    """Dissolve coplanar faces, collapse-decimate, optionally planar-dissolve *obj*.

    When ``props.decimate_passes > 1``, collapse decimation is split into N passes
    targeting ``props.decimate_ratio`` overall. Per-pass ratio is
    ``decimate_ratio ** (1/passes)`` so the cumulative ratio closely approximates
    the final ratio. The dissolve pre-pass and seam split run once up front;
    only the COLLAPSE modifier runs per iteration.

    If the mesh has UV layers, edges on UV island boundaries are topologically
    split before decimation so the collapse solver physically cannot collapse
    across them. After the collapse passes the split vertex pairs are welded
    back with a tight scoped merge-by-distance.

    When ``props.run_planar_postpass`` is True, a planar (DISSOLVE) decimate
    modifier runs once after the collapse loop to merge near-coplanar faces
    into n-gons (angle threshold ``props.planar_angle``). This reduces triangle
    count in flat regions without touching curved surfaces.

    The remove-doubles / normals / delete-loose cleanup runs once at the end.
    """
```

- [ ] **Step 3: Verify no `protect_uv_seams` references remain**

Run:

```bash
grep -rn "protect_uv_seams" src/
```

Expected output: nothing (exit code 1). If any match appears, something is missed — go back and address it.

- [ ] **Step 4: Verify the old `_protect_uv_seams` function name is fully removed**

Run:

```bash
grep -rn "_protect_uv_seams" src/
```

Expected: nothing. Task 3 replaced the definition with `_split_uv_seams`, and Task 5 removed the call site.

- [ ] **Step 5: Run the linter**

```bash
ruff check src/geometry.py
ruff format src/geometry.py
```

Expected: no errors.

- [ ] **Step 6: Stage the change**

```bash
git add src/geometry.py
```

---

## Task 6: Update CHANGELOG.md for 1.8.0

**Files:**
- Modify: `CHANGELOG.md:7-18`

- [ ] **Step 1: Edit the 1.8.0 section**

In [CHANGELOG.md](CHANGELOG.md), locate the existing 1.8.0 block:

```markdown
## [1.8.0] - 2026-04-19

### Added

- `Passes` setting on the Decimate step (1–5, default 1). Splits decimation into N iterations targeting the same final ratio, with per-pass ratio computed as `ratio ** (1/passes)` — so 3 passes at final ratio 0.1 collapses at ~0.464 per pass and still lands at ~10% of the original face count. The dissolve pre-pass and UV seam protection run once up front; only the COLLAPSE modifier runs per iteration, so the quadric solver recomputes its error field between passes without re-constraining already-protected seams. For the same final face count, higher pass counts preserve silhouette and texture detail noticeably better at aggressive ratios (e.g. 3 passes reaching 0.1 produces a much smoother result than a single pass at 0.1). Default of 1 keeps existing behavior unchanged.
- `Protect UV Seams` toggle on the Decimate step. When on, UV island boundaries are marked as Sharp edges before decimation so the collapse solver won't collapse across them — useful for CAD-style meshes with clean UV layouts. Users with clean-UV CAD models should enable this.
- `Planar Post-Pass` toggle on the Decimate step, **default on**, with a `Planar Angle` slider (default 5 deg). Runs a second DECIMATE modifier in `DISSOLVE` (planar) mode after the collapse pass, merging adjacent near-coplanar faces into n-gons. Dramatically reduces triangle count in flat regions — cylinder tops, flat panels, ground planes — without touching curved surfaces. UV island boundaries are preserved natively via the modifier's `delimit={'UV'}` setting. Fixes the radial fan artifact where the COLLAPSE solver left dozens of triangles meeting at a central vertex on flat discs. Disable if your mesh has subtle curvature that should not be flattened.

### Changed

- UV seam protection in the Decimate step now **defaults off**. Previously (1.7.0 onwards) seam protection ran unconditionally, but it caused visible fan artifacts on AI-generated meshes with fragmented UVs — radial triangle stars on barrel tops, flat panels, etc. — because hundreds of arbitrary seam edges over-constrained the quadric solver. The new `Protect UV Seams` toggle exposes this as an explicit opt-in. Existing users who relied on seam protection should enable "Protect UV Seams" in the Decimate panel.
- Decimate step now runs a planar post-pass by default (see above). On AI-generated meshes with large flat regions this produces noticeably lower final face counts and eliminates the radial fan artifact; on meshes with subtle curvature you may want to disable `Planar Post-Pass` to preserve it.
```

Replace the entire block with:

```markdown
## [1.8.0] - 2026-04-19

### Added

- `Passes` setting on the Decimate step (1–5, default 1). Splits decimation into N iterations targeting the same final ratio, with per-pass ratio computed as `ratio ** (1/passes)` — so 3 passes at final ratio 0.1 collapses at ~0.464 per pass and still lands at ~10% of the original face count. The dissolve pre-pass and seam split run once up front; only the COLLAPSE modifier runs per iteration, so the quadric solver recomputes its error field between passes. For the same final face count, higher pass counts preserve silhouette and texture detail noticeably better at aggressive ratios (e.g. 3 passes reaching 0.1 produces a much smoother result than a single pass at 0.1). Default of 1 keeps existing behavior unchanged.
- `Planar Post-Pass` toggle on the Decimate step, **default on**, with a `Planar Angle` slider (default 5 deg). Runs a second DECIMATE modifier in `DISSOLVE` (planar) mode after the collapse pass, merging adjacent near-coplanar faces into n-gons. Dramatically reduces triangle count in flat regions — cylinder tops, flat panels, ground planes — without touching curved surfaces. UV island boundaries are preserved natively via the modifier's `delimit={'UV'}` setting. Fixes the radial fan artifact where the COLLAPSE solver left dozens of triangles meeting at a central vertex on flat discs. Disable if your mesh has subtle curvature that should not be flattened.
- **Topological UV seam constraint in the Decimate step.** When the mesh has UV layers, edges on UV island boundaries are now physically split before decimation so the COLLAPSE solver cannot collapse across them, then re-welded with a scoped merge-by-distance after the collapse passes. Replaces the 1.7.0 soft-hint seam protection (Sharp edges), which the collapse solver could still violate at aggressive ratios, causing texture smearing where rims bled into lids and neighboring UV islands. Always on when UVs are present; no configuration. Tradeoff: on meshes with highly fragmented UVs the final face count may exceed the target ratio because boundary edges become uncollapsible — the `Planar Post-Pass` default partially compensates by flattening coplanar regions.

### Changed

- Decimate step now runs a planar post-pass by default (see above). On AI-generated meshes with large flat regions this produces noticeably lower final face counts and eliminates the radial fan artifact; on meshes with subtle curvature you may want to disable `Planar Post-Pass` to preserve it.
```

Summary of the CHANGELOG edits:
- Dropped the `Protect UV Seams` toggle entry from **Added** (that feature is replaced, not shipped).
- Dropped the seam-protection default-off paragraph from **Changed** (that decision is reversed by the topological approach).
- Added a new **Added** entry for the topological seam constraint.
- Tightened the `Passes` entry to remove the now-stale "already-protected seams" wording.

- [ ] **Step 2: Stage the change**

```bash
git add CHANGELOG.md
```

---

## Task 7: Manual validation in Blender

**Files:** none (testing only)

- [ ] **Step 1: Build the add-on**

```bash
python build.py
```

Expected: `build/model-optimizer-addon.py` is produced without errors. The output will echo the version number — should read `1.8.0`.

- [ ] **Step 2: Install the built add-on in Blender**

In Blender:
1. Edit → Preferences → Add-ons → Install from Disk
2. Select `build/model-optimizer-addon.py`
3. Enable it if not auto-enabled

- [ ] **Step 3: Verify the `Protect UV Seams` toggle is gone from the UI**

Open the AI Optimizer sidebar in the 3D Viewport. In the Decimate panel, confirm:
- `Passes` slider is present
- `Planar Post-Pass` toggle is present
- `Planar Angle` slider is present
- `Protect UV Seams` toggle is **NOT** present

- [ ] **Step 4: Barrel test — clean UVs (primary motivating case)**

Import or open the barrel mesh that produced the texture-smear screenshots in the spec. Set:
- Decimate ratio: 0.1
- Passes: 1
- Planar Post-Pass: off (for isolation of the seam-split effect)

Run the Decimate step only (disable other pipeline steps for clarity, or run standalone).

Expected: the rim-to-lid UV boundary shows no color bleed. Compare visually against the "after decimate without seam protection" screenshot in the spec — the triangular brown wedges across the teal lid should be gone. Some geometry quality loss at the rim is acceptable; the texture must be clean.

If the texture still bleeds, check the console for the `[AI Optimizer] edge_split failed` log line (indicates the fallback fired).

- [ ] **Step 5: Camo-texture test — fragmented UVs (regression case)**

Import or open the AI mesh with the camo-style texture referenced in the spec. Use the same decimate settings as Step 4.

Expected: no radial fan artifacts in flat regions (previously observed on barrel tops etc. under the old unconditional seam protection). Final face count will be higher than an unconstrained decimate would produce — this is the accepted tradeoff. The texture is visually preserved.

- [ ] **Step 6: No-UV regression test**

Create a fresh cube (Add → Mesh → Cube) with no UV layer (delete the default UV map via Object Data Properties → UV Maps → minus). Run Decimate at ratio 0.5.

Expected: decimate completes without errors or warnings. Face count approximately halves. No console errors referencing `edge_split` or `seams_from_islands`.

- [ ] **Step 7: Multi-pass regression test**

Using the barrel mesh again, set Passes: 3 and Decimate ratio: 0.1. Run Decimate.

Expected: completes without error. Final face count ~10% of original (as with 1 pass). Texture still clean at the rim-to-lid boundary. The seam split runs once before the pass loop; restitch runs once after.

- [ ] **Step 8: Confirm overall success**

If Steps 4-7 all pass by their expected criteria, the implementation is validated. If any step fails:
- Capture the Blender system console output
- Note the exact step that failed and the expected vs. observed behavior
- Do NOT mark Task 7 complete

---

## Task 8: Final verification and handoff

**Files:** none

- [ ] **Step 1: Run the linter on all modified files**

```bash
ruff check src/
ruff format src/ --check
```

Expected: no errors, no formatting diffs.

- [ ] **Step 2: Confirm all `protect_uv_seams` references are gone**

```bash
grep -rn "protect_uv_seams" src/ CHANGELOG.md
```

Expected: empty output.

- [ ] **Step 3: Confirm all `_protect_uv_seams` references are gone**

```bash
grep -rn "_protect_uv_seams" src/
```

Expected: empty output.

- [ ] **Step 4: Confirm the new helpers exist and are wired up**

```bash
grep -n "_split_uv_seams\|_restitch_seams" src/geometry.py
```

Expected: at least four matches — the two function definitions, and two call sites inside `decimate_single`.

- [ ] **Step 5: Verify staged changes**

```bash
git status
git diff --cached --stat
```

Expected staged files:
- `CHANGELOG.md`
- `src/geometry.py`
- `src/panels.py`
- `src/properties.py`

Unstaged: nothing relevant (the pre-existing staged state from `git status` at the start of this session carries over; the user manages their own commits).

- [ ] **Step 6: Report completion**

Summarize to the user:
- What changed (seam protection is now topological, always-on, toggle removed)
- What the user needs to test manually (steps from Task 7, if not yet walked through)
- Where the CHANGELOG was updated (1.8.0 section)

Remind the user (per CLAUDE.md) that Claude never commits — the user needs to commit these changes themselves when ready.

---

## Notes for the implementer

**On the `edge.seam` vs `edge.use_seam` distinction:**
When reading edges through BMesh (`bm = bmesh.new(); bm.from_mesh(...)`), the seam flag is `edge.seam`. When reading edges through the direct Mesh data API (`obj.data.edges`), the flag is `edge.use_seam`. Both refer to the same underlying data. Task 3's function uses both, in their respective contexts — this is correct, not a typo.

**On why `seams_from_islands` needs Edit mode:**
`bpy.ops.uv.seams_from_islands()` is a UV-editor operator — it requires an Edit-mode object with its UV layer accessible. The existing `_protect_uv_seams` already handles this; `_split_uv_seams` preserves the pattern.

**On why the restitch uses `bmesh.ops.remove_doubles` and not `bpy.ops.mesh.remove_doubles`:**
The `bpy.ops` version operates on the current selection; there is no clean way to restrict it to a proximity-scoped subset without first selecting those vertices, which requires toggling into Edit mode and back. The `bmesh.ops` version accepts an explicit `verts=` argument and is both cleaner and faster for this use case.

**On the restitch tolerance:**
The formula `min(props.merge_distance_mm, 0.01) / 1000` keeps the restitch weld at most 0.01 mm regardless of the user's global merge distance. This is tight enough to avoid false merges even on very small meshes while being loose enough to re-join seam pairs that drifted a tiny amount during decimation.

**On the 3×3×3 neighbor scan in `_restitch_seams`:**
A vertex one cell-width away from a recorded seam cell can still be within Euclidean distance `threshold_m` of a recorded seam position (the position may sit near the cell boundary). The 3×3×3 neighborhood includes all cells that could contain a seam position within the tolerance.
