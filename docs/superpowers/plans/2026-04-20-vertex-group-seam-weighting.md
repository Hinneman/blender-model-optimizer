# Vertex-Group Seam Weighting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Sharp-edge marking inside `_protect_uv_seams` with a vertex-group weight bias on the COLLAPSE modifier, giving the quadric solver a tunable numerical preference against collapsing seam-adjacent vertices while keeping mesh topology fully connected.

**Architecture:** The helper builds a vertex group named `AIOPT_Seam_Protect` that weights seam-endpoint vertices and their one-ring neighbors at 1.0 and every other vertex at 0.1. `decimate_single` points each COLLAPSE modifier at the group with `invert_vertex_group=True`, then deletes the group after decimation finishes. Property, panel, and version stay unchanged — same `Protect UV Seams` toggle, stronger implementation.

**Tech Stack:** Python 3, Blender `bpy` + `bmesh` APIs, Blender 4.0+ add-on conventions.

**Reference spec:** [docs/superpowers/specs/2026-04-20-vertex-group-seam-weighting-design.md](../specs/2026-04-20-vertex-group-seam-weighting-design.md)

---

## Background for the Implementer

You are modifying an installed Blender add-on. Testing happens by building the single-file add-on and running it inside Blender — there is no Python unit-test suite (confirmed by existing project conventions: features are validated manually in Blender).

**Key files:**

- [src/geometry.py](../../src/geometry.py) — contains `_protect_uv_seams` (line 536) and `decimate_single` (line 572). All functional changes here.
- [CHANGELOG.md](../../CHANGELOG.md) — targeted edit to the 1.8.0 `Protect UV Seams` entry.

**Do not touch:**
- [src/properties.py](../../src/properties.py) — the `protect_uv_seams` BoolProperty stays exactly as it is.
- [src/panels.py](../../src/panels.py) — the UI row stays exactly as it is.
- `pyproject.toml` — version stays at 1.8.0.

**Git rules (from CLAUDE.md):** never run `git commit`. Stage files with `git add` but let the user commit. Do not create worktrees. Work directly on the current branch (`feat/multiple-pass-decimate`).

**Lint/format:** `ruff check src/` and `ruff format src/` — config in `pyproject.toml`.

**Build:** `python build.py` produces `build/model-optimizer-addon.py`. Install in Blender via Edit → Preferences → Add-ons → Install from Disk.

**Blender vertex group API quick reference:**

```python
# Create a vertex group
group = obj.vertex_groups.new(name="AIOPT_Seam_Protect")

# Check if a group already exists by name (returns None if absent)
existing = obj.vertex_groups.get("AIOPT_Seam_Protect")
if existing is not None:
    obj.vertex_groups.remove(existing)

# Assign weights — indices is a list of vertex indices, weight is a float in [0, 1]
group.add(indices=[0, 1, 2], weight=1.0, type="REPLACE")

# COLLAPSE modifier wiring
mod.vertex_group = "AIOPT_Seam_Protect"
mod.invert_vertex_group = True  # higher weight = more protected
```

---

## File Structure

**Modified:**
- [src/geometry.py](../../src/geometry.py) — rewrite the body of `_protect_uv_seams` (preserve signature but change the return type to `Optional[str]`); modify `decimate_single` to capture the return, wire the group into each COLLAPSE modifier, and delete the group after decimation.
- [CHANGELOG.md](../../CHANGELOG.md) — reword the 1.8.0 `Added` entry for `Protect UV Seams`.

No new files. Both helpers stay in `geometry.py` since they are internal to the decimate step.

---

## Task 1: Rewrite `_protect_uv_seams` to build a vertex group

**Files:**
- Modify: `src/geometry.py:536-569`

This task replaces the Sharp-edge marking logic with vertex-group creation. The function's name and signature stay the same; its return type changes from `None` to `Optional[str]` (the vertex group name, or `None` when the helper cannot build one).

- [ ] **Step 1: Replace the body of `_protect_uv_seams`**

Locate the existing function at line 536 of `src/geometry.py`:

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
```

Behaviors preserved from the original:
- No-op when `obj.data.uv_layers` is empty.
- Auto-generates seams via `bpy.ops.uv.seams_from_islands()` when none exist.

New behaviors:
- Returns the vertex-group name (or `None`), instead of returning implicitly.
- Creates a named vertex group with two weight levels.
- Wraps `seams_from_islands` and `vertex_groups.new` in try/except for graceful fallback.
- Removes any stale group named `AIOPT_Seam_Protect` before creating a fresh one.

Implementation notes:
- `bm.verts.ensure_lookup_table()` must be called before indexed access on `bm.verts[...]`. The code follows this pattern.
- `edge.other_vert(v)` returns the vertex at the other end of an edge given one endpoint. Standard BMesh API.
- `group.add(index=[...], weight=..., type="REPLACE")` — **`index` is the keyword, not `indices`**. This is a common foot-gun in the Blender API; use `index=` (singular) with a list value.
- Assigning weight 0.1 to every non-protected vertex is necessary. A vertex not in any group is treated as weight 0 by the solver, which with `invert_vertex_group=True` means infinitely cheap to collapse — the solver would happily collapse everything before touching a seam. We want a 10x bias, not an infinite one.

- [ ] **Step 2: Run the linter**

```bash
ruff check src/geometry.py
ruff format src/geometry.py
```

Expected: no errors. `ruff format` may report "1 file left unchanged" or reflow whitespace.

- [ ] **Step 3: Stage the change**

```bash
git add src/geometry.py
```

Do NOT commit. The user commits.

---

## Task 2: Wire the vertex group into `decimate_single`

**Files:**
- Modify: `src/geometry.py:572-636`

This task captures the helper's return value, passes the vertex-group name into each COLLAPSE modifier, and deletes the group once decimation finishes.

- [ ] **Step 1: Update the call site to capture the returned group name**

In `src/geometry.py`, locate the existing block around line 604-610:

```python
    # Protect UV seams: mark island boundaries Sharp so DECIMATE doesn't
    # collapse edges that define the texture layout. Run once — seam and
    # sharp flags are preserved by subsequent collapses. Off by default:
    # AI-generated meshes typically have fragmented UVs that create fan
    # artifacts when seams are protected.
    if getattr(props, "protect_uv_seams", False):
        _protect_uv_seams(obj)
```

Replace with:

```python
    # Protect UV seams: build a vertex-group weight bias that the COLLAPSE
    # solver treats as ~10x more expensive to collapse. Mesh topology is
    # unchanged — the protection is a numerical cost bias, not a hard
    # constraint. Off by default: on AI-generated meshes with fragmented
    # UVs the protection degrades gracefully because most vertices end up
    # in the protected set, reducing the relative bias.
    seam_group_name = None
    if getattr(props, "protect_uv_seams", False):
        seam_group_name = _protect_uv_seams(obj)
```

- [ ] **Step 2: Wire the vertex group into each COLLAPSE modifier**

In the same function, locate the pass loop around line 612-617:

```python
    for _ in range(passes):
        mod = obj.modifiers.new(name="Decimate_Optimize", type="DECIMATE")
        mod.decimate_type = "COLLAPSE"
        mod.ratio = per_pass_ratio
        mod.use_collapse_triangulate = True
        bpy.ops.object.modifier_apply(modifier=mod.name)
```

Replace with:

```python
    for _ in range(passes):
        mod = obj.modifiers.new(name="Decimate_Optimize", type="DECIMATE")
        mod.decimate_type = "COLLAPSE"
        mod.ratio = per_pass_ratio
        mod.use_collapse_triangulate = True
        if seam_group_name:
            mod.vertex_group = seam_group_name
            mod.invert_vertex_group = True
        bpy.ops.object.modifier_apply(modifier=mod.name)
```

Implementation note: Blender propagates vertex-group weights through COLLAPSE automatically. Surviving vertices at collapse junctions inherit weights from their pre-collapse parents. So the group only needs to be built once before the loop; each modifier references it by name, and all N passes see the correctly-propagated weights.

- [ ] **Step 3: Add cleanup of the vertex group after decimation**

After the planar post-pass block (currently ending around line 627) and before the final cleanup (remove-doubles / normals / delete-loose, starting around line 629), insert the group removal.

Locate:

```python
    # Optional planar post-pass: merge adjacent near-coplanar faces into
    # n-gons. Reduces triangle count in flat regions without touching curved
    # surfaces. delimit={"UV"} preserves UV island boundaries natively.
    if getattr(props, "run_planar_postpass", True) and props.planar_angle > 0:
        mod = obj.modifiers.new(name="Decimate_Planar", type="DECIMATE")
        mod.decimate_type = "DISSOLVE"
        mod.angle_limit = props.planar_angle
        mod.delimit = {"UV"}
        bpy.ops.object.modifier_apply(modifier=mod.name)

    # Post-decimate cleanup: fix degenerate geometry without adding new faces
    # (hole-filling creates faces with bad UVs that cause texture artifacts)
```

Replace with:

```python
    # Optional planar post-pass: merge adjacent near-coplanar faces into
    # n-gons. Reduces triangle count in flat regions without touching curved
    # surfaces. delimit={"UV"} preserves UV island boundaries natively.
    if getattr(props, "run_planar_postpass", True) and props.planar_angle > 0:
        mod = obj.modifiers.new(name="Decimate_Planar", type="DECIMATE")
        mod.decimate_type = "DISSOLVE"
        mod.angle_limit = props.planar_angle
        mod.delimit = {"UV"}
        bpy.ops.object.modifier_apply(modifier=mod.name)

    # Remove the seam-protect vertex group: its purpose ends with decimation
    # and we don't want diagnostic groups leaking into the exported GLB.
    if seam_group_name:
        group = obj.vertex_groups.get(seam_group_name)
        if group is not None:
            obj.vertex_groups.remove(group)

    # Post-decimate cleanup: fix degenerate geometry without adding new faces
    # (hole-filling creates faces with bad UVs that cause texture artifacts)
```

- [ ] **Step 4: Update the `decimate_single` docstring**

The existing docstring (lines 573-589) still describes the old Sharp-edge mechanism. Replace it with:

```python
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
```

- [ ] **Step 5: Run the linter**

```bash
ruff check src/geometry.py
ruff format src/geometry.py
```

Expected: no errors.

- [ ] **Step 6: Verify the function structure is intact**

Run:

```bash
grep -n "^def " src/geometry.py | head -20
```

Expected: `_protect_uv_seams` and `decimate_single` are still present at their original positions (536 and 572 respectively, possibly shifted by a few lines due to docstring edits). No new top-level functions added by this task.

- [ ] **Step 7: Stage the change**

```bash
git add src/geometry.py
```

---

## Task 3: Update CHANGELOG.md for 1.8.0

**Files:**
- Modify: `CHANGELOG.md:7-18` (the 1.8.0 section)

- [ ] **Step 1: Reword the `Protect UV Seams` entry**

In [CHANGELOG.md](../../CHANGELOG.md), locate the existing bullet under `## [1.8.0] - 2026-04-19` → `### Added`:

```markdown
- `Protect UV Seams` toggle on the Decimate step. When on, UV island boundaries are marked as Sharp edges before decimation so the collapse solver won't collapse across them — useful for CAD-style meshes with clean UV layouts. Users with clean-UV CAD models should enable this.
```

Replace with:

```markdown
- `Protect UV Seams` toggle on the Decimate step. When on, a temporary `AIOPT_Seam_Protect` vertex group weights UV island boundary vertices (and their one-ring neighbors) at 1.0 and all other vertices at 0.1; each COLLAPSE decimate modifier references the group with `invert_vertex_group=True`, biasing the quadric solver ~10x against collapsing seam-adjacent vertices. Mesh topology stays fully connected — this is a numerical cost bias, not a hard constraint — so fragmented-UV meshes (AI-generated camo, etc.) degrade gracefully instead of producing the fan artifacts the 1.7.0 Sharp-edge mechanism caused. The group is removed after decimation so the exported mesh stays clean. Recommended for clean-UV meshes (barrels, CAD exports) where rim/lid texture bleed is visible; leave off for heavily fragmented meshes where the bias adds overhead without benefit.
```

Summary of the rewording:
- Dropped the Sharp-edge description.
- Described the new vertex-group mechanism explicitly including group name, weights, invert flag, and rationale.
- Noted the topology-preserving nature as the key distinction from the 1.7.0 mechanism.
- Kept the targeting advice (clean-UV meshes benefit, fragmented meshes less so).

The other two 1.8.0 entries (`Passes`, `Planar Post-Pass`) and the `### Changed` section do not change.

- [ ] **Step 2: Stage the change**

```bash
git add CHANGELOG.md
```

---

## Task 4: Manual validation in Blender

**Files:** none (testing only)

- [ ] **Step 1: Build the add-on**

```bash
python build.py
```

Expected: `build/model-optimizer-addon.py` is produced without errors. Version string in the output should read `1.8.0`.

- [ ] **Step 2: Install the built add-on in Blender**

In Blender:
1. Edit → Preferences → Add-ons → Install from Disk
2. Select `build/model-optimizer-addon.py`
3. Enable if not auto-enabled

- [ ] **Step 3: Verify the UI still has the `Protect UV Seams` toggle**

Open the AI Optimizer sidebar in the 3D Viewport. In the Decimate panel, confirm:
- `Passes` slider is present
- `Protect UV Seams` toggle is present (default off)
- `Planar Post-Pass` toggle is present
- `Planar Angle` slider is present

- [ ] **Step 4: Barrel test — clean UVs, motivating case**

Import or open the barrel mesh that produced the texture-smear screenshots. Settings:
- Decimate ratio: 0.1
- Passes: 1
- `Protect UV Seams`: **ON**
- `Planar Post-Pass`: OFF (isolate the seam-protection effect)

Run the Decimate step only.

Expected: the rim-to-lid UV boundary shows no (or minimal) color bleed. Mesh stays fully watertight — no cracks, no torn faces, no disconnected flaps. This is the success criterion that distinguishes the vertex-group approach from the abandoned topological-split approach.

If texture bleed is still visible but mesh is clean: the bias strength may need tuning (currently 10x). Report the result; tuning is a follow-up, not a blocker.

If cracks or torn faces appear: the vertex group is somehow affecting topology (shouldn't happen — vertex-group weights only bias collapse cost). Check the console for errors; report as a BLOCKED failure.

- [ ] **Step 5: Camo-texture test — fragmented UVs, regression case**

Open the AI mesh with the camo-style texture. Same settings but with `Planar Post-Pass` ON (default).

Expected: no catastrophic geometry damage (unlike the topological-split attempt). Final face count lands near the target ratio (not wildly exceeding it as the hard split did). Texture quality comparable to or slightly better than running with `Protect UV Seams` off. The feature should degrade gracefully — not destructively.

- [ ] **Step 6: No-UV regression test**

Add → Mesh → Cube. Delete the default UV map (Object Data Properties → UV Maps → minus). Run Decimate at ratio 0.5 with `Protect UV Seams` ON.

Expected: decimate completes without errors. Face count approximately halves. No console errors referencing `seams_from_islands` or `vertex_groups`. The `Protect UV Seams` toggle being on should be a no-op on this mesh (helper returns `None` for no-UV meshes).

- [ ] **Step 7: Multi-pass regression test**

Barrel mesh. Settings:
- Decimate ratio: 0.1
- Passes: 3
- `Protect UV Seams`: ON
- `Planar Post-Pass`: OFF

Expected: completes without error. Final face count ~10% of original. Texture still clean at rim-to-lid boundary. No cracks. The vertex group's weight-propagation-across-passes behavior is working if this test passes.

- [ ] **Step 8: Toggle-off regression test**

Barrel mesh, same settings as Step 4 but `Protect UV Seams`: **OFF**.

Expected: identical behavior to unconstrained decimate — texture bleed returns on the rim/lid boundary (that's the opt-out choice), but mesh geometry is unaffected. Confirms the vertex-group logic gates cleanly on the toggle.

- [ ] **Step 9: Vertex-group cleanup check**

After any successful run with `Protect UV Seams` ON (Steps 4, 5, 7 above), select the decimated object and check Object Data Properties → Vertex Groups in Blender. Expected: `AIOPT_Seam_Protect` is **not present**. Confirms the cleanup logic in Task 2 Step 3 worked.

- [ ] **Step 10: Confirm overall success**

If Steps 4-9 all pass, the implementation is validated. If any step fails, capture the Blender system console output, note the exact step and expected-vs-observed behavior, and do NOT mark Task 4 complete.

---

## Task 5: Final verification and handoff

**Files:** none

- [ ] **Step 1: Run the linter on all modified files**

```bash
ruff check src/
ruff format src/ --check
```

Expected: no errors, no formatting diffs.

- [ ] **Step 2: Confirm the helper has the new signature**

```bash
grep -n "^def _protect_uv_seams\|return \"AIOPT_Seam_Protect\"\|return None" src/geometry.py
```

Expected: one `def _protect_uv_seams` match, plus at least one `return "AIOPT_Seam_Protect"` and at least one `return None` from inside the helper (for the early-exit paths).

- [ ] **Step 3: Confirm the call site captures the return value**

```bash
grep -n "seam_group_name" src/geometry.py
```

Expected: at least four matches — the declaration, the assignment inside the toggle block, the wiring inside the pass loop, and the cleanup guard.

- [ ] **Step 4: Confirm the vertex-group wiring is present on the modifier**

```bash
grep -n "invert_vertex_group\|mod.vertex_group = seam_group_name" src/geometry.py
```

Expected: one `mod.vertex_group = seam_group_name` match and one `mod.invert_vertex_group = True` match, both inside the pass loop.

- [ ] **Step 5: Confirm no Sharp-edge logic remains**

```bash
grep -n "edge.smooth = False\|edge.smooth=False" src/geometry.py
```

Expected: no matches. The old Sharp-edge code is gone.

- [ ] **Step 6: Verify staged changes**

```bash
git status
git diff --cached --stat
```

Expected staged files include `src/geometry.py` and `CHANGELOG.md`. Other files (properties.py, panels.py) should not appear as modified — they are untouched by this plan.

- [ ] **Step 7: Report completion**

Summarize to the user:
- What changed: `_protect_uv_seams` now builds a weighted vertex group instead of marking Sharp edges; `decimate_single` wires the group into each COLLAPSE pass with `invert_vertex_group=True`, then deletes the group afterward.
- Manual Blender validation steps (from Task 4) if not yet walked through.
- CHANGELOG updated in place under 1.8.0 (same feature entry, rewritten).

Remind the user (per CLAUDE.md) that Claude never commits — the user commits these changes themselves when ready.

---

## Notes for the implementer

**On `group.add(index=..., weight=..., type=...)`:**
The parameter name is `index` (singular), not `indices`, even though it accepts a list. Passing `indices=[...]` will raise `TypeError`. This is a Blender API quirk; `type="REPLACE"` is the standard value and overwrites any prior weight for the same vertex.

**On why non-seam vertices need explicit weight 0.1:**
A vertex with no group membership has effective weight 0. With `invert_vertex_group=True`, weight 0 means *maximum cost* (infinite resistance), not minimum. Conversely, a vertex with explicit weight 0.1 means low cost (easy to collapse). To give the solver a 10x contrast between protected and non-protected regions, both sides must be explicitly weighted — 1.0 for protected, 0.1 for everything else. Skipping the 0.1 assignment would make every non-seam vertex also fully protected and the feature would be a no-op.

**On why the group is rebuilt fresh each run (removing a prior one):**
Blender preserves vertex groups across operations. If `decimate_single` is called multiple times on the same object (e.g. iterative tweaking by the user), the second call's `vertex_groups.new(name="AIOPT_Seam_Protect")` would create a group named `AIOPT_Seam_Protect.001` because the name is taken. Removing the old one first keeps the name stable and guarantees the modifier's `vertex_group = "AIOPT_Seam_Protect"` always points at the current group.

**On weight propagation across passes:**
Blender's COLLAPSE decimator transfers vertex weights during collapse. When two vertices merge, the survivor inherits the max (or weighted average, depending on internal rules) of the two weights. This is why building the group once before the pass loop is sufficient — subsequent passes see weights that are correctly propagated through the prior pass's collapses, without the helper needing to rebuild anything.

**On the planar post-pass:**
DISSOLVE decimation does not use vertex groups. It runs with `delimit={"UV"}` which preserves UV island boundaries natively. The seam group is irrelevant to this pass, and the cleanup step after it correctly removes the group before the final remove-doubles / normals / delete-loose cleanup — which also doesn't care about vertex groups.
