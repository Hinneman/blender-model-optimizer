# Planar Post-Pass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional angle-based (planar) decimation pass after the collapse pass so flat regions that the collapse solver left heavily triangulated — like the tops of cylindrical objects, flat panels, and large ground planes — get reduced to a small number of large triangles without touching curved regions.

**Architecture:** Add a second DECIMATE modifier call inside `decimate_single`, after the collapse loop and before the post-cleanup, using `decimate_type="DISSOLVE"` (Blender's name for the planar mode) and `delimit={'UV'}` so UV islands are preserved natively by the modifier. Gated by a new `run_planar_postpass: BoolProperty` and tuned by a `planar_angle: FloatProperty` (radians). Default **on** because the primary use case (AI-generated meshes) exhibits large flat regions that the collapse solver triangulates into radial fan artifacts — testing on the barrel-top mesh confirmed this is the dominant failure mode for this add-on's target users.

**Tech Stack:** Python 3.10+, Blender 4.0+ `bpy` API. No test framework → manual verification in Blender. Lint/format via `ruff`.

**Prerequisite:** This plan assumes [2026-04-18-decimate-multi-pass.md](2026-04-18-decimate-multi-pass.md) and [2026-04-19-uv-seam-protection-toggle.md](2026-04-19-uv-seam-protection-toggle.md) are committed (or at minimum staged and tested). The planar post-pass extends `decimate_single` further — it is additive to those changes, not a replacement.

**Background — why planar mode and not COLLAPSE again:** COLLAPSE refuses to cross Sharp/material/seam boundaries but otherwise treats all edges by quadric error. In a flat disc made of hundreds of near-coplanar triangles, the quadric error of collapsing any edge is nearly zero, so COLLAPSE still produces many small triangles rather than a few large ones. Blender's planar DISSOLVE mode (`decimate_type="DISSOLVE"`) uses a different algorithm: it merges adjacent faces whose normals differ by less than `angle_limit`, producing n-gons that are then triangulated. This is exactly what we want for flat regions. Research (see plan [2026-04-19-uv-seam-protection-toggle.md](2026-04-19-uv-seam-protection-toggle.md) Task 3 notes) confirmed DISSOLVE supports the `delimit` property natively — we do not need Sharp-edge markup.

---

## File Structure

- Modify: [src/properties.py](../../../src/properties.py) — add `run_planar_postpass: BoolProperty` and `planar_angle: FloatProperty` in the `-- Decimate settings --` block.
- Modify: [src/panels.py](../../../src/panels.py) — add a new UI subsection in the Decimate panel for the planar pass toggle and its angle slider.
- Modify: [src/geometry.py](../../../src/geometry.py) — add a planar DISSOLVE modifier call in `decimate_single` after the COLLAPSE loop, gated by `props.run_planar_postpass`.
- Modify: `pyproject.toml` — bump version from `1.8.0` to `1.9.0` (additive feature, minor bump).
- Modify: `CHANGELOG.md` — add a new `## [1.9.0]` section above `## [1.8.0]`.

No operator changes — `decimate_single` signature is unchanged. No new files.

---

## Task 1: Add the planar post-pass properties

**Files:**
- Modify: `src/properties.py` — inside the `-- Decimate settings --` block, after the `protect_uv_seams` BoolProperty added by the previous plan.

- [ ] **Step 1: Add the two properties**

Open [src/properties.py](../../../src/properties.py). Locate the `protect_uv_seams` BoolProperty in the `-- Decimate settings --` section (added by plan [2026-04-19-uv-seam-protection-toggle.md](2026-04-19-uv-seam-protection-toggle.md)). Insert these two properties directly after its closing `)`:

```python
    run_planar_postpass: BoolProperty(
        name="Planar Post-Pass",
        default=True,
        description=(
            "After collapse decimation, run a second planar-dissolve pass that merges "
            "adjacent near-coplanar faces into n-gons. Dramatically reduces triangle count "
            "in flat regions (tops of cylinders, panels, ground planes) without changing "
            "curved surfaces. UV islands are preserved natively by the modifier. Disable "
            "if your mesh has subtle curvature that should not be flattened"
        ),
    )
    planar_angle: FloatProperty(
        name="Planar Angle",
        default=0.0872665,
        min=0.0,
        max=0.523599,
        step=1,
        precision=3,
        description=(
            "Max angle between adjacent faces for planar-dissolve to merge them. "
            "5 deg (default) is conservative; 10-15 deg reduces more faces but may "
            "flatten subtle curvature"
        ),
        subtype="ANGLE",
    )
```

Default angle is `0.0872665` radians ≈ 5 degrees (π/36). Max is `0.523599` ≈ 30 degrees. `FloatProperty` is already imported at the top of the file (line 5).

- [ ] **Step 2: Lint and format**

```bash
ruff check src/properties.py && ruff format src/properties.py
```

Expected: no issues.

- [ ] **Step 3: Stage (do NOT commit)**

```bash
git add src/properties.py
```

---

## Task 2: Wire the new controls into the Decimate panel

**Files:**
- Modify: `src/panels.py` — Decimate panel's `draw` method. The `protect_uv_seams` toggle (from the previous plan) sits between the preview block and the Normal Map Baking separator.

- [ ] **Step 1: Insert a new subsection**

Open [src/panels.py](../../../src/panels.py). Find this block (added by the seam-toggle plan):

```python
        layout.separator()
        layout.prop(props, "protect_uv_seams")

        layout.separator()
        col = layout.column(align=True)
        col.label(text="Normal Map Baking:", icon="IMAGE_DATA")
```

Replace with:

```python
        layout.separator()
        layout.prop(props, "protect_uv_seams")

        layout.separator()
        col = layout.column(align=True)
        col.prop(props, "run_planar_postpass")
        if props.run_planar_postpass:
            col.prop(props, "planar_angle", slider=True)

        layout.separator()
        col = layout.column(align=True)
        col.label(text="Normal Map Baking:", icon="IMAGE_DATA")
```

The change is 5 new lines (the separator + aligned column + prop row + conditional angle slider + blank line before the next separator).

- [ ] **Step 2: Lint and format**

```bash
ruff check src/panels.py && ruff format src/panels.py
```

Expected: no issues.

- [ ] **Step 3: Stage**

```bash
git add src/panels.py
```

---

## Task 3: Add the planar pass to `decimate_single`

**Files:**
- Modify: `src/geometry.py` — `decimate_single` function. After all prior plans, the function structure is: setup → pre-dissolve → optional seam protect → COLLAPSE loop → post-cleanup. We insert the planar DISSOLVE modifier between the COLLAPSE loop and the post-cleanup.

- [ ] **Step 1: Insert the planar pass**

Open [src/geometry.py](../../../src/geometry.py). Find the `decimate_single` function. After the prior plans, it contains a `for _ in range(passes):` block followed by a `# Post-decimate cleanup:` comment. Locate the exact line where the COLLAPSE loop ends and the cleanup begins. It looks like this (with surrounding context):

```python
    for _ in range(passes):
        mod = obj.modifiers.new(name="Decimate_Optimize", type="DECIMATE")
        mod.decimate_type = "COLLAPSE"
        mod.ratio = per_pass_ratio
        mod.use_collapse_triangulate = True
        bpy.ops.object.modifier_apply(modifier=mod.name)

    # Post-decimate cleanup: fix degenerate geometry without adding new faces
    # (hole-filling creates faces with bad UVs that cause texture artifacts)
    bpy.ops.object.mode_set(mode="EDIT")
```

Insert a new block **between the closing of the `for` loop and the `# Post-decimate cleanup:` comment**:

```python
    for _ in range(passes):
        mod = obj.modifiers.new(name="Decimate_Optimize", type="DECIMATE")
        mod.decimate_type = "COLLAPSE"
        mod.ratio = per_pass_ratio
        mod.use_collapse_triangulate = True
        bpy.ops.object.modifier_apply(modifier=mod.name)

    # Optional planar post-pass: merge adjacent near-coplanar faces into
    # n-gons. Reduces triangle count in flat regions without touching curved
    # surfaces. delimit={"UV"} preserves UV island boundaries natively.
    if getattr(props, "run_planar_postpass", False):
        mod = obj.modifiers.new(name="Decimate_Planar", type="DECIMATE")
        mod.decimate_type = "DISSOLVE"
        mod.angle_limit = props.planar_angle
        mod.delimit = {"UV"}
        bpy.ops.object.modifier_apply(modifier=mod.name)

    # Post-decimate cleanup: fix degenerate geometry without adding new faces
    # (hole-filling creates faces with bad UVs that cause texture artifacts)
    bpy.ops.object.mode_set(mode="EDIT")
```

The `getattr` fallback is for old `.blend` files saved before this property existed. Setting `delimit` on a DISSOLVE-mode DECIMATE modifier is the idiomatic Blender way to preserve UV boundaries — confirmed by the API research summarized in `2026-04-19-uv-seam-protection-toggle.md`.

- [ ] **Step 2: Update the function docstring**

In the same function, the current docstring (from the multi-pass plan) describes collapse passes and cleanup. Update the first paragraph to mention the planar pass. Find:

```python
    """Dissolve coplanar faces, then collapse-decimate *obj*.

    When ``props.decimate_passes > 1``, decimation is split into N passes
    targeting ``props.decimate_ratio`` overall. Per-pass ratio is
    ``decimate_ratio ** (1/passes)`` so the cumulative ratio closely approximates
    the final ratio. The dissolve pre-pass and UV seam protection run once
    up front; only the COLLAPSE modifier runs per iteration, so the quadric
    solver recomputes its error field between passes without re-constraining
    already-protected seams.
    The remove-doubles / normals / delete-loose cleanup runs once at the end.
    """
```

Replace with:

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

- [ ] **Step 3: Lint and format**

```bash
ruff check src/geometry.py && ruff format src/geometry.py
```

Expected: no issues.

- [ ] **Step 4: Stage**

```bash
git add src/geometry.py
```

---

## Task 4: Bump version and update changelog

**Files:**
- Modify: `pyproject.toml:3` — bump `1.8.0` to `1.9.0`.
- Modify: `CHANGELOG.md` — new `## [1.9.0]` section above the existing `## [1.8.0]`.

- [ ] **Step 1: Bump version**

Open `pyproject.toml`. Change:

```toml
version = "1.8.0"
```

to:

```toml
version = "1.9.0"
```

- [ ] **Step 2: Add the changelog entry**

Open `CHANGELOG.md`. Immediately after the header block and **before** the existing `## [1.8.0] - 2026-04-18` heading, insert:

```markdown
## [1.9.0] - 2026-04-19

### Added

- `Planar Post-Pass` toggle on the Decimate step, **default on**, with a `Planar Angle` slider (default 5 deg). Runs a second DECIMATE modifier in `DISSOLVE` (planar) mode after the collapse pass, merging adjacent near-coplanar faces into n-gons. Dramatically reduces triangle count in flat regions — cylinder tops, flat panels, ground planes — without touching curved surfaces. UV island boundaries are preserved natively via the modifier's `delimit={'UV'}` setting, so no Sharp-edge markup is needed. Fixes the radial fan artifact where the COLLAPSE solver left dozens of triangles meeting at a central vertex on flat discs. Disable if your mesh has subtle curvature that should not be flattened.

### Changed

- Decimate step now runs a planar post-pass by default (see above). Existing users will see lower face counts and fewer fan artifacts on flat regions after upgrading; the final face count from a given ratio may be lower than in 1.8.0. Disable `Planar Post-Pass` in the Decimate panel to restore 1.8.0 behavior.
```

Verify the resulting file has this order at the top:

```markdown
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.9.0] - 2026-04-19

### Added

- `Planar Post-Pass` toggle ...

## [1.8.0] - 2026-04-18

### Added

- `Passes` setting ...
- `Protect UV Seams` toggle ...
```

- [ ] **Step 3: Rebuild and verify version injection**

```bash
python build.py
```

Expected: `Building with version (1, 9, 0)`. Then:

```bash
grep '"version"' build/model-optimizer-addon.py | head -3
```

Expected: shows `"version": (1, 9, 0)`.

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

Expected: no issues.

- [ ] **Step 2: Rebuild and install in Blender**

```bash
python build.py
```

Install `build/model-optimizer-addon.py` in Blender.

- [ ] **Step 3: Manual verification**

1. **Default off — regression:**
   - Open a mesh, leave "Planar Post-Pass" unchecked. Run Decimate. Expected: result matches the 1.8.0 behavior with no planar pass applied.

2. **On — flat regions:**
   - On the same mesh, enable "Planar Post-Pass" with the default 5 deg angle. Run Decimate. Expected: face count drops further (compared with planar-off), with the reduction concentrated in flat regions. For the barrel test mesh, the cylinder top should be noticeably cleaner — few large triangles instead of many small ones — while curved parts (rim, bolts) are unchanged.

3. **Angle sweep:**
   - Try 10 deg and 15 deg. Expected: larger angles reduce more faces but start to flatten subtle curvature. 5 deg (default) should be the safest.

4. **With seam protection on:**
   - Enable both "Protect UV Seams" and "Planar Post-Pass". Run. Expected: UVs preserved, flat regions still dissolved (the planar pass uses `delimit={'UV'}`, not Sharp, so seam protection and planar dissolve don't conflict).

5. **With multi-pass:**
   - Set Passes = 3, enable Planar Post-Pass. Expected: 3 collapse passes run first, then the single planar pass, then the cleanup.

6. **Old `.blend`:**
   - Load a `.blend` saved before this feature. Expected: add-on loads, both new properties default to their defaults (False / 5 deg).

- [ ] **Step 4: Leave staged for user to commit**

```bash
git status
```

Expected staged files: `src/properties.py`, `src/panels.py`, `src/geometry.py`, `pyproject.toml`, `CHANGELOG.md`.

Do **not** run `git commit` — user commits all changes themselves.
