# UV Seam Protection Toggle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make UV seam protection in the Decimate step user-toggleable, defaulting **off**, so AI-generated meshes with fragmented UVs no longer get fan artifacts from overzealous Sharp-edge constraints.

**Architecture:** Add a `protect_uv_seams: BoolProperty(default=False)` to `AIOPT_Properties`. Wire it into the Decimate panel UI. Gate the existing `_protect_uv_seams(obj)` call in `decimate_single` behind the new property. No changes to the helper function itself — only its invocation becomes conditional. This is a behavior change from the previously-staged 1.8.0, so update the existing (unreleased) changelog entry to call it out.

**Tech Stack:** Python 3.10+, Blender 4.0+ `bpy` API. No test framework in this project → verification is manual (run built add-on in Blender). Lint/format via `ruff`.

**Context:** Built on top of the `feat/multiple-pass-decimate` branch, where 1.8.0 multi-pass decimation is already staged (not committed). That branch added `decimate_passes` and moved `_protect_uv_seams` out of the per-iteration loop in `decimate_single`. This plan adds the toggle on top of that existing change and updates the same 1.8.0 changelog entry to describe both changes.

---

## File Structure

- Modify: [src/properties.py](../../../src/properties.py) — add `protect_uv_seams` BoolProperty after `decimate_passes` in the Decimate settings block.
- Modify: [src/panels.py](../../../src/panels.py) — add the toggle in the Decimate panel, after the `decimate_passes` slider and before the Normal Map Baking separator.
- Modify: [src/geometry.py](../../../src/geometry.py) — gate the single `_protect_uv_seams(obj)` call in `decimate_single` behind `props.protect_uv_seams`.
- Modify: `CHANGELOG.md` — update the existing (unreleased) `## [1.8.0]` entry: add a new bullet documenting the toggle and the default-off behavior change.

No operator changes (`src/operators.py` is untouched). No version bump — still 1.8.0, just not yet committed. No new files.

---

## Task 1: Add the `protect_uv_seams` property

**Files:**
- Modify: `src/properties.py` — inside the `-- Decimate settings --` block, immediately after the `decimate_passes` IntProperty that was added in the previous (staged) change. That property currently sits around lines 156–167.

- [ ] **Step 1: Add the property**

Open [src/properties.py](../../../src/properties.py). Locate the `decimate_passes` IntProperty block in the `-- Decimate settings --` section. Insert the new property block directly after its closing `)`:

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

No `update=` callback is needed — this property does not affect the viewport preview.

`BoolProperty` is already imported at the top of the file (line 4).

- [ ] **Step 2: Lint and format**

Run from the project root:

```bash
ruff check src/properties.py && ruff format src/properties.py
```

Expected: no issues.

- [ ] **Step 3: Stage (do NOT commit)**

```bash
git add src/properties.py
```

Project CLAUDE.md: "The user commits all changes themselves." Do not run `git commit`.

---

## Task 2: Wire the toggle into the Decimate panel

**Files:**
- Modify: `src/panels.py` — the Decimate panel's `draw` method. The `decimate_passes` prop was added to this method in the previously-staged change around line 436, and the "Normal Map Baking:" separator sits around line 447 (after the preview block).

- [ ] **Step 1: Insert the toggle**

Open [src/panels.py](../../../src/panels.py). Find the block containing `col.prop(props, "decimate_passes", slider=True)` and the preview labels below it. After the entire preview block (the `if meshes:` block with the "Current" / "Estimated after" / "Per-pass ratio" labels) and **before** the `layout.separator()` line that precedes the "Normal Map Baking:" section, insert a new prop row. Specifically:

Find the end of the preview block — it looks like:

```python
            col.label(text=f"Current: {current:,} faces")
            col.label(text=f"Estimated after: ~{estimated:,} faces")
            if props.decimate_passes > 1:
                per_pass = props.decimate_ratio ** (1.0 / props.decimate_passes)
                col.label(text=f"Per-pass ratio: {per_pass:.3f} \u00d7 {props.decimate_passes}")

        layout.separator()
        col = layout.column(align=True)
        col.label(text="Normal Map Baking:", icon="IMAGE_DATA")
```

Replace it with:

```python
            col.label(text=f"Current: {current:,} faces")
            col.label(text=f"Estimated after: ~{estimated:,} faces")
            if props.decimate_passes > 1:
                per_pass = props.decimate_ratio ** (1.0 / props.decimate_passes)
                col.label(text=f"Per-pass ratio: {per_pass:.3f} \u00d7 {props.decimate_passes}")

        layout.separator()
        layout.prop(props, "protect_uv_seams")

        layout.separator()
        col = layout.column(align=True)
        col.label(text="Normal Map Baking:", icon="IMAGE_DATA")
```

The change is two new lines:

```python
        layout.separator()
        layout.prop(props, "protect_uv_seams")
```

inserted between the existing `layout.separator()` and the `col = layout.column(align=True)` that starts the Normal Map Baking section. Use `layout.prop` (not `col.prop`) so the toggle is a full-width row separated visually from both the decimate sliders above and the normal-map section below.

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

## Task 3: Gate the seam-protection call in `decimate_single`

**Files:**
- Modify: `src/geometry.py` — `decimate_single` function. After the previously-staged multi-pass rewrite, the function calls `_protect_uv_seams(obj)` unconditionally before the pass loop. We need to wrap that call in `if props.protect_uv_seams:`.

- [ ] **Step 1: Gate the call**

Open [src/geometry.py](../../../src/geometry.py). Find this exact block in `decimate_single` (it was staged in the prior change):

```python
    # Protect UV seams: mark island boundaries Sharp so DECIMATE doesn't
    # collapse edges that define the texture layout. Run once — seam and
    # sharp flags are preserved by subsequent collapses.
    _protect_uv_seams(obj)
```

Replace with:

```python
    # Protect UV seams: mark island boundaries Sharp so DECIMATE doesn't
    # collapse edges that define the texture layout. Run once — seam and
    # sharp flags are preserved by subsequent collapses. Off by default:
    # AI-generated meshes typically have fragmented UVs that create fan
    # artifacts when seams are protected.
    if getattr(props, "protect_uv_seams", False):
        _protect_uv_seams(obj)
```

Notes:
- `getattr(props, "protect_uv_seams", False)` is defensive for old `.blend` files saved before this property existed — same pattern used for `decimate_passes` two lines above.
- Default fallback is `False` to match the property's default. This means old `.blend` files migrate to the new default rather than preserving the old behavior. This is intentional — see the CHANGELOG entry in Task 4 for the user-facing rationale.

- [ ] **Step 2: Lint and format**

```bash
ruff check src/geometry.py && ruff format src/geometry.py
```

Expected: no issues.

- [ ] **Step 3: Stage**

```bash
git add src/geometry.py
```

---

## Task 4: Update the 1.8.0 changelog entry

**Files:**
- Modify: `CHANGELOG.md` — the existing (unreleased, staged) `## [1.8.0] - 2026-04-18` entry.

- [ ] **Step 1: Add a new bullet under the existing `### Added` section**

Open `CHANGELOG.md`. Locate the `## [1.8.0] - 2026-04-18` heading and its `### Added` section. The existing single bullet describes the `Passes` setting. Append a second bullet directly below it (before the next `##` heading):

```markdown
- `Protect UV Seams` toggle on the Decimate step, **default off**. When on, UV island boundaries are marked as Sharp edges before decimation so the collapse solver won't collapse across them — useful for CAD-style meshes with clean UV layouts. **Default changed from on to off**: the previous unconditional seam protection was causing visible fan artifacts in flat regions of AI-generated meshes (radial triangle stars on barrel tops, flat panels, etc.) because fragmented AI-UV layouts produce hundreds of arbitrary seam edges that over-constrain the quadric solver. Users with clean-UV CAD models should flip this on.
```

The final `### Added` section should contain two bullets: the Passes bullet (already present) and the new Protect UV Seams bullet.

- [ ] **Step 2: Stage**

```bash
git add CHANGELOG.md
```

---

## Final verification

- [ ] **Step 1: Lint the whole src/**

```bash
ruff check src/
```

Expected: no issues across all files.

- [ ] **Step 2: Rebuild the add-on**

```bash
python build.py
```

Expected: build succeeds with version `(1, 8, 0)`.

- [ ] **Step 3: Manual verification in Blender**

Install and enable the built `build/model-optimizer-addon.py`:

1. **Regression — toggle off (default):**
   - Open a mesh. Confirm the new "Protect UV Seams" toggle appears in the Decimate panel, below the Passes slider, unchecked by default.
   - Run the pipeline with Decimate only. Expected: no visible fan artifacts in flat regions. For the barrel test mesh specifically, the cylinder top should be a smooth disc with clean radial triangulation — not a star pattern.

2. **CAD-style — toggle on:**
   - On a mesh with meaningful UV islands (any non-AI CAD-style mesh), enable the toggle and run Decimate. Expected: same or better texture fidelity compared with toggle off, with no catastrophic silhouette loss.

3. **Backward compat — old `.blend`:**
   - Open a `.blend` saved before this change (if one exists). Expected: add-on loads without error, toggle defaults to off.

- [ ] **Step 4: Leave everything staged**

```bash
git status
```

Expected: the staged set now contains (stacking on top of the prior 1.8.0 work):
- `src/properties.py` (now with both `decimate_passes` and `protect_uv_seams`)
- `src/panels.py` (now with the passes slider + preview + the new toggle)
- `src/geometry.py` (multi-pass `decimate_single` with the gated seam call)
- `pyproject.toml` (version bumped to 1.8.0 — unchanged in this plan)
- `CHANGELOG.md` (1.8.0 entry with two `### Added` bullets)

Do **not** commit — user commits all changes themselves.

---

## Follow-up

Plan [2026-04-19-planar-post-pass.md](2026-04-19-planar-post-pass.md) adds an optional planar (angle-based) decimation pass after the collapse step. This is a separate feature that specifically targets flat regions where COLLAPSE leaves too many triangles. Do not bundle it with this plan — ship the toggle first, then evaluate whether the planar pass is still needed.
