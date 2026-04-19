# Decimate Multi-Pass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users split decimation into N passes so that a given final ratio produces higher-quality geometry than a single aggressive collapse.

**Architecture:** Add a `decimate_passes` IntProperty (1–5, default 1) to `AIOPT_Properties`. `decimate_single` becomes a loop: per-pass ratio is computed as `decimate_ratio ** (1/decimate_passes)`, the `dissolve_limited` + `_protect_uv_seams` pre-passes run once per iteration, and the post-decimate cleanup (remove_doubles / normals / delete_loose) runs once at the end. Normal-map baking remains unchanged — the highpoly snapshot is taken once before the first pass, as today.

**Tech Stack:** Python 3.10+, Blender 4.0+ `bpy` API. No test framework in this project → verification is manual (run built add-on in Blender). Lint/format via `ruff`.

---

## File Structure

- Modify: [src/properties.py](../../../src/properties.py) — add `decimate_passes` IntProperty in the `-- Decimate settings --` block.
- Modify: [src/geometry.py](../../../src/geometry.py) — rewrite `decimate_single` to loop over N passes; split current body so pre-passes run per-iteration and post-cleanup runs once.
- Modify: [src/panels.py](../../../src/panels.py) — add the passes slider in the Decimate panel, next to the ratio slider; update the "Estimated after" preview to reflect the final ratio (unchanged math — preview already uses `decimate_ratio`, which stays as the *final* target).
- Modify: `CHANGELOG.md` — add entry under new version heading.
- Modify: `pyproject.toml` — bump version (1.7.1 → 1.8.0, minor bump since this is a user-visible feature).

Operators in [src/operators.py](../../../src/operators.py) do not need changes: they call `decimate_single(context, obj, props)` and `bake_normal_map_for_decimate(...)` — both signatures are unchanged. The multi-pass logic is fully internal to `decimate_single`.

---

## Task 1: Add the `decimate_passes` property

**Files:**
- Modify: `src/properties.py:135-155` (inside the `-- Decimate settings --` block, immediately after `decimate_ratio`)

- [ ] **Step 1: Add the property**

Open [src/properties.py](../../../src/properties.py) and insert the new property directly after the `decimate_ratio` definition (which ends at line 155 with `update=_tag_3d_redraw,` followed by `)`). The new block:

```python
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
```

`_tag_3d_redraw` is already imported at line 12.

- [ ] **Step 2: Lint**

Run from the project root:

```bash
ruff check src/properties.py
```

Expected: no issues.

- [ ] **Step 3: Format**

```bash
ruff format src/properties.py
```

Expected: `1 file left unchanged` or `1 file reformatted`.

- [ ] **Step 4: Manual smoke test**

Build and load the add-on:

```bash
python build.py
```

In Blender: Edit → Preferences → Add-ons → Install from Disk → `build/model-optimizer-addon.py` → enable. Open the 3D Viewport sidebar → AI Optimizer panel. The new property is not yet wired into the UI (next task), so there is nothing to see yet — the goal of this step is only to confirm the add-on still **registers** without error. Check the Blender console (Window → Toggle System Console on Windows) for tracebacks.

Expected: add-on enables cleanly, no registration errors.

- [ ] **Step 5: Stage**

```bash
git add src/properties.py
```

Do not commit. (Project CLAUDE.md: "The user commits all changes themselves.")

---

## Task 2: Wire the property into the Decimate panel

**Files:**
- Modify: `src/panels.py:429-443` (the Decimate panel's `draw` method)

- [ ] **Step 1: Add the passes slider and update the preview**

Open [src/panels.py](../../../src/panels.py). Locate the block at lines 429–443 (the `draw` method of the Decimate panel). Replace the section from `col.prop(props, "dissolve_angle", slider=True)` through the "Estimated after" label with the version below. The change: insert a `decimate_passes` slider between the dissolve-angle and ratio rows, and add a second preview line that shows the per-pass ratio when passes > 1.

Find this block (lines 433–443):

```python
        col = layout.column(align=True)
        col.prop(props, "dissolve_angle", slider=True)
        col.prop(props, "decimate_ratio", slider=True)

        # Show preview of what this ratio means
        meshes = get_selected_meshes()
        if meshes:
            current = count_faces(meshes)
            estimated = int(current * props.decimate_ratio)
            col.label(text=f"Current: {current:,} faces")
            col.label(text=f"Estimated after: ~{estimated:,} faces")
```

Replace with:

```python
        col = layout.column(align=True)
        col.prop(props, "dissolve_angle", slider=True)
        col.prop(props, "decimate_ratio", slider=True)
        col.prop(props, "decimate_passes", slider=True)

        # Show preview of what this ratio means
        meshes = get_selected_meshes()
        if meshes:
            current = count_faces(meshes)
            estimated = int(current * props.decimate_ratio)
            col.label(text=f"Current: {current:,} faces")
            col.label(text=f"Estimated after: ~{estimated:,} faces")
            if props.decimate_passes > 1:
                per_pass = props.decimate_ratio ** (1.0 / props.decimate_passes)
                col.label(text=f"Per-pass ratio: {per_pass:.3f} × {props.decimate_passes}")
```

- [ ] **Step 2: Lint and format**

```bash
ruff check src/panels.py && ruff format src/panels.py
```

Expected: no issues.

- [ ] **Step 3: Manual smoke test**

Rebuild and reload in Blender:

```bash
python build.py
```

In Blender: disable the add-on, re-install from disk, re-enable. Open the AI Optimizer panel → Decimate section.

Expected:
- A "Passes" slider appears below the Ratio slider, range 1–5, default 1.
- With a mesh selected and Passes = 1, the preview shows "Current" and "Estimated after" lines only (no per-pass line).
- With Passes = 3 and Ratio = 0.1, a new line shows `Per-pass ratio: 0.464 × 3` (value is `0.1 ** (1/3) ≈ 0.464`).
- Changing either slider updates the preview live.

- [ ] **Step 4: Stage**

```bash
git add src/panels.py
```

---

## Task 3: Rewrite `decimate_single` to support multiple passes

**Files:**
- Modify: `src/geometry.py:572-602` (the full body of `decimate_single`)

- [ ] **Step 1: Replace `decimate_single`**

Open [src/geometry.py](../../../src/geometry.py). Replace the entire function `decimate_single` (lines 572–602) with the version below. Key differences:

- A new loop runs the dissolve + seam-protection pre-passes and the collapse modifier `passes` times.
- Per-pass ratio is `props.decimate_ratio ** (1.0 / passes)`.
- The post-decimate cleanup (remove_doubles / normals_make_consistent / delete_loose) runs **once** after the final pass, not per iteration.
- `passes == 1` produces byte-identical operator calls to the previous implementation (same dissolve → seam protection → single COLLAPSE → cleanup), so default behavior is preserved.

Replacement:

```python
def decimate_single(context, obj, props):
    """Dissolve coplanar faces, then collapse-decimate *obj*.

    When ``props.decimate_passes > 1``, decimation is split into N passes
    targeting ``props.decimate_ratio`` overall. Per-pass ratio is
    ``decimate_ratio ** (1/passes)`` so the product equals the final ratio.
    The dissolve + seam-protection pre-passes run once per iteration on the
    current intermediate mesh so each collapse sees a clean error field.
    The remove-doubles / normals / delete-loose cleanup runs once at the end.
    """
    bpy.ops.object.select_all(action="DESELECT")
    context.view_layer.objects.active = obj
    obj.select_set(True)

    passes = max(1, int(getattr(props, "decimate_passes", 1)))
    per_pass_ratio = props.decimate_ratio ** (1.0 / passes)

    for _ in range(passes):
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
        mod.decimate_type = "COLLAPSE"
        mod.ratio = per_pass_ratio
        mod.use_collapse_triangulate = True
        bpy.ops.object.modifier_apply(modifier=mod.name)

    # Post-decimate cleanup: fix degenerate geometry without adding new faces
    # (hole-filling creates faces with bad UVs that cause texture artifacts)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.remove_doubles(threshold=props.merge_distance_mm / 1000.0)
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.mesh.delete_loose(use_verts=True, use_edges=True, use_faces=False)
    bpy.ops.object.mode_set(mode="OBJECT")
```

Note: `getattr(props, "decimate_passes", 1)` is defensive — once Task 1 has shipped, the property always exists, but the fallback lets old saved `.blend` files load without error if they were created before the property was added. `max(1, int(...))` clamps against zero or negative values even though the property definition already enforces `min=1`.

- [ ] **Step 2: Lint and format**

```bash
ruff check src/geometry.py && ruff format src/geometry.py
```

Expected: no issues.

- [ ] **Step 3: Verify backward compatibility — passes = 1**

Rebuild: `python build.py`. In Blender:

1. Open or import a moderately dense AI mesh (any mesh with ≥ 10k faces).
2. In the AI Optimizer panel, set: Ratio = 0.5, Passes = 1, Dissolve Angle = default.
3. Note the face count (`count_faces` is shown in the panel preview).
4. Select the mesh(es), enable only the Decimate step, disable everything else, run the pipeline.
5. Check the resulting face count.

Expected: face count is approximately `starting_faces × 0.5`. Visual inspection: silhouette and texture are not noticeably different from what you got with the pre-change code at the same settings. (This is the regression check — passes = 1 must behave exactly as before.)

- [ ] **Step 4: Verify multi-pass path — passes = 3**

On the same source mesh (reload or undo to get back to full detail):

1. Set Ratio = 0.1, Passes = 3.
2. Confirm the panel preview shows `Per-pass ratio: 0.464 × 3`.
3. Run the pipeline with only Decimate enabled.
4. Check the resulting face count.

Expected: face count is approximately `starting_faces × 0.1` (matches the single-pass final ratio), but the visual quality — especially on curved/organic regions — is visibly better than the same mesh decimated at Ratio = 0.1, Passes = 1. Compare the two side by side.

- [ ] **Step 5: Verify multi-pass with baking — passes = 2, bake on**

1. Set Ratio = 0.2, Passes = 2, Bake Normal Map = ON.
2. Run the pipeline with Decimate enabled (and any other steps needed for the bake).

Expected: normal map bakes successfully (no error in the console), the decimated mesh has a `*_normal_map` image in its material, face count is roughly `starting × 0.2`. The normal map should capture detail from the **original** highpoly, not from an intermediate pass — this is already guaranteed because the highpoly copy is snapshotted in `operators.py` **before** `decimate_single` is called (see [src/operators.py:155-170](../../../src/operators.py#L155-L170)).

- [ ] **Step 6: Stage**

```bash
git add src/geometry.py
```

---

## Task 4: Update changelog and version

**Files:**
- Modify: `pyproject.toml:3`
- Modify: `CHANGELOG.md` (prepend a new version section above the current top entry)

- [ ] **Step 1: Bump version**

Open `pyproject.toml` and change line 3 from:

```toml
version = "1.7.1"
```

to:

```toml
version = "1.8.0"
```

Minor bump because this is a user-visible additive feature with no breaking changes.

- [ ] **Step 2: Add changelog entry**

Open `CHANGELOG.md`. Immediately after the "All notable changes…" header block and before the existing `## [1.7.1] - 2026-04-18` heading, insert:

```markdown
## [1.8.0] - 2026-04-18

### Added

- `Passes` setting on the Decimate step (1–5, default 1). Splits decimation into N iterations targeting the same final ratio, with per-pass ratio computed as `ratio ** (1/passes)`. Each iteration reruns the dissolve + seam-protection pre-passes on the intermediate mesh so the collapse solver operates on a clean error field. Higher pass counts preserve silhouette and texture detail noticeably better at aggressive ratios (e.g. 3× at ratio 0.1 produces much smoother output than a single pass at 0.1). Default of 1 keeps existing behavior unchanged.
```

- [ ] **Step 3: Verify build still reads the new version**

```bash
python build.py
```

Expected: build succeeds; the last line of stdout reports the new version (1.8.0) or the built file's `bl_info` contains it. If the build script prints the version, confirm it says `1.8.0`. Otherwise grep the built file:

```bash
grep 'version' build/model-optimizer-addon.py | head -3
```

Expected: shows `"version": (1, 8, 0)` in the injected `bl_info`.

- [ ] **Step 4: Stage**

```bash
git add pyproject.toml CHANGELOG.md
```

---

## Final verification

- [ ] **Step 1: Lint the whole src/**

```bash
ruff check src/
```

Expected: no issues across all modified files.

- [ ] **Step 2: Rebuild once more and enable in Blender**

```bash
python build.py
```

Install and enable the built add-on in Blender. Confirm:
- Add-on enables without error.
- Decimate panel shows the new Passes slider.
- Preview label updates correctly when Passes changes.
- Running the Decimate step at Passes = 1 matches pre-change behavior.
- Running at Passes = 3 with Ratio = 0.1 produces a noticeably cleaner result than Passes = 1 with Ratio = 0.1 on the same source mesh.

- [ ] **Step 3: Leave changes staged for the user to commit**

```bash
git status
```

Expected: staged files are `src/properties.py`, `src/panels.py`, `src/geometry.py`, `pyproject.toml`, `CHANGELOG.md`. Do **not** run `git commit` — the user commits all changes themselves (project policy in [.claude/CLAUDE.md](../../../.claude/CLAUDE.md)).
