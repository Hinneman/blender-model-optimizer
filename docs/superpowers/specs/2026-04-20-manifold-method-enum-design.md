# Manifold Method as a Setting + Remove UV Gutter Dilation + Decimate Flow Rework

**Status:** Draft
**Date:** 2026-04-20
**Branch:** `feat/multiple-pass-decimate`

## Context

During 1.8.0 troubleshooting of a camo-cover-over-barrels AI mesh, repeated pipeline runs showed large "holes" in the drape of the cover. Investigation ruled out every mid-pipeline suspect in turn (Remove Interior, Remove Small Pieces, flipped normals, fragmented-UV texture bleed) and eventually traced the damage to **`mesh.print3d_clean_non_manifold`** running inside `fix_geometry_single`.

The 3D Print Toolbox's `clean_non_manifold` operator deletes geometry around non-manifold edges to produce a watertight solid. A draped cover is a thin single-layer shell — every free boundary edge is non-manifold by design, so the operator chews away ring after ring of cover faces. Pre-1.7.1, an earlier bug in `is_print3d_available()` (using `hasattr(bpy.ops.mesh, ...)` which always returned True) caused the operator to raise at call time and the `except` branch to silently fall through to `mesh.fill_holes(sides=32)`. That accidental fallback was the correct behavior for the add-on's target meshes. 1.7.1 fixed the availability check, which exposed the bug.

Confirmed by test: uninstalling the 3D Print Toolbox makes the cover come out clean with default settings, no dilation needed. UV gutter smears also vanish — they were downstream artifacts of the Toolbox tearing UV islands apart.

## Goals

1. Make the 3D Print Toolbox an **explicit opt-in** rather than the automatic preference, so it cannot silently damage thin-shell AI meshes.
2. Keep it available as a capability for users who know they want it (watertight CAD exports, 3D-printable solids).
3. Remove the ambient dependency-status labels that currently imply the Toolbox is recommended.
4. Remove the Dilate UV Gutters pipeline step — it was added earlier this session as a workaround for the texture-bleed symptom, which was itself a downstream effect of the Toolbox damage. With the root cause fixed, dilation is unnecessary and slow (43–48s per pipeline run on this test mesh).
5. Fix the multi-pass decimation face-count regression. Running with 3 passes at ratio 0.05 was expected to land near 57k faces (UI estimate); actual result was ~76k (33% over), driven by two interacting bugs: (a) the `dissolve_limited` pre-pass produced n-gons that the COLLAPSE modifier's `use_collapse_triangulate` re-triangulated, growing the face count on pass 1; (b) passes 2 and 3 became near-no-ops because Blender's quadric-edge-collapse solver runs out of cheap collapses on raw thin-shell triangles and stops before reaching the requested ratio.
6. Turn on `Protect UV Seams` by default. The pre-release 1.8.0 branch defaulted it off because it "didn't help" on fragmented-UV meshes. This session's evidence showed the visible damage attributed to seam protection was actually from the 3D Print Toolbox; seam protection itself is near-free and genuinely helps on clean-UV meshes. Default it on and migrate existing saved configs to match.

## Non-Goals

- No changes to Remove Interior, Remove Small Pieces, Decimate, or any other pipeline step.
- No changes to the `Recalculate Normals` toggle or its behavior.
- No changes to the multi-pass, planar post-pass, or Protect UV Seams features introduced in 1.8.0.

## Design

### New property: `manifold_method`

Replace the existing `fix_manifold: BoolProperty` with a single `EnumProperty`:

```python
manifold_method: EnumProperty(
    name="Manifold Fix",
    items=[
        ("OFF", "Off", "Don't attempt manifold repair"),
        ("FILL_HOLES", "Fill Holes", "Fill holes with n-gons (up to 32 sides). Safe on thin-shell meshes"),
        ("PRINT3D", "3D Print Toolbox",
         "Aggressive non-manifold cleanup. Best for watertight solid meshes. "
         "Warning: deletes geometry around non-manifold edges — NOT suitable for thin shells, "
         "draped covers, cloth, or any single-layer surface"),
    ],
    default="FILL_HOLES",
    description="Method used to repair non-manifold geometry",
)
```

### UI — radio-row layout with disabled state

Blender's `EnumProperty` dropdown does not support per-item disabled state. Use `layout.prop_enum(props, "manifold_method", value)` on one row per choice, and set `row.enabled = False` for the PRINT3D row when the plugin is not installed. The item remains visible but unclickable.

```python
# In AIOPT_PT_geometry_panel.draw:
layout.label(text="Manifold Fix:")
col = layout.column(align=True)
col.prop_enum(props, "manifold_method", "OFF")
col.prop_enum(props, "manifold_method", "FILL_HOLES")
row = col.row(align=True)
row.enabled = is_print3d_available()
row.prop_enum(props, "manifold_method", "PRINT3D")
```

`is_print3d_available()` stays in `utils.py` (it's used only by the panel now) — the helper keeps its current `addon_utils.check()` implementation. The function is the only place that knows how to detect the Toolbox.

### `fix_geometry_single` — dispatch on the enum

```python
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
# OFF: no-op, keep method_used = "none"
```

Extract the current fill-holes fallback block (`select_non_manifold` + `fill_holes(sides=32)` inside a try/except for `RuntimeError`) into a module-level helper `_fill_holes_manifold()` that operates on the currently-active object in Edit mode and returns `True` on success, `False` on RuntimeError. Both the `FILL_HOLES` branch and the emergency fallback from `PRINT3D` call it.

Signature change: `fix_geometry_single` continues to return `(fixed, method_used)` — no caller contract change.

### Remove: 3D Print Toolbox dependency status labels

Remove both panel-level labels that currently describe plugin availability:

- [src/panels.py:35-38](src/panels.py#L35-L38) — the always-visible status row in `AIOPT_PT_main_panel`
- [src/panels.py:267-281](src/panels.py#L267-L281) — the `if props.fix_manifold:` block in `AIOPT_PT_geometry_panel` that shows a warning box when the plugin is missing

After removal, the only place the plugin is named is inside the radio row itself. Users who don't know or care about it simply see it as a disabled option; users who want it install it and the option enables.

### Remove: Dilate UV Gutters

Delete every artifact of the feature introduced earlier this session:

- `dilate_image_gutters`, `dilate_gutters_all`, `_collect_uv_triangles_for_image`, `_rasterize_coverage` from [src/textures.py](src/textures.py)
- `AIOPT_OT_uv_dilate` operator from [src/operators.py](src/operators.py)
- `run_uv_dilate` and `uv_dilate_pixels` properties from [src/properties.py](src/properties.py)
- Pipeline step wiring in `AIOPT_OT_run_all.invoke` + `_setup_uv_dilate` / `_tick_uv_dilate` / `_teardown_uv_dilate`
- `dilate_gutters_all` import in [src/operators.py](src/operators.py)
- `AIOPT_OT_uv_dilate` in `classes` tuple and imports in [src/__init__.py](src/__init__.py)
- `run_uv_dilate` and `uv_dilate_pixels` from `SAVEABLE_PROPS` in [src/utils.py](src/utils.py)
- UI toggle and pixel slider in [src/panels.py](src/panels.py) textures panel
- CHANGELOG 1.8.0 entries that describe the feature

### Migration — one-shot translation on load

In `load_defaults()` in [src/utils.py](src/utils.py), after reading the JSON:

```python
# One-time migration: fix_manifold BoolProperty → manifold_method EnumProperty.
# Pre-1.8.0 configs have fix_manifold (True/False); translate to the safe default.
# Users who had Toolbox via fix_manifold=True get FILL_HOLES; they can opt back
# in to PRINT3D explicitly if they want it. Rationale: Toolbox was causing
# damage on thin-shell meshes even for users who had the plugin installed.
if "fix_manifold" in data:
    if "manifold_method" not in data:
        data["manifold_method"] = "FILL_HOLES" if data["fix_manifold"] else "OFF"
    del data["fix_manifold"]
```

`SAVEABLE_PROPS` loses `fix_manifold` and gains `manifold_method`.

### Decimate flow rework (B2)

Replace the current `decimate_single` flow. Old flow:

```
dissolve_limited(angle=dissolve_angle, delimit={UV})   # merge coplanar tris → n-gons
[optional] build AIOPT_Seam_Protect vertex group
for each pass:
    DECIMATE modifier (COLLAPSE, ratio=per_pass, use_collapse_triangulate=True)
[optional] DECIMATE modifier (DISSOLVE, angle=planar_angle)
remove_doubles + delete_loose
```

New flow:

```
[optional] build AIOPT_Seam_Protect vertex group      # now default on
triangulate entire mesh (bpy.ops.mesh.quads_convert_to_tris)
for each pass:
    DECIMATE modifier (COLLAPSE, ratio=per_pass, use_collapse_triangulate=False)
[optional] DECIMATE modifier (DISSOLVE, angle=planar_angle, delimit={UV})
remove_doubles + delete_loose
```

Rationale:

- **Triangulate up front** so every pass sees the same "1 polygon = 1 triangle" accounting. Per-pass ratio math becomes honest: pass 1 on N triangles targets `N × per_pass_ratio`, pass 2 on `N × per_pass_ratio` triangles targets `N × per_pass_ratio²`, etc.
- **`use_collapse_triangulate = False`** is now correct because the input is already triangulated — nothing for the flag to do, but leaving it True would conflict with some edge cases in Blender's solver. Explicitly off.
- **Drop `dissolve_limited` pre-pass** entirely. Its purpose (merging coplanar tris to reduce solver work) is still served by the planar post-pass at the end, which handles flat regions after the collapse solver has done the topology-preserving work on curved regions. Two angle-based dissolves in the pipeline (pre + post) was redundant.
- **Planar post-pass uses `delimit={"UV"}`** (unchanged) so it doesn't merge across UV island boundaries.

### Property change: remove `dissolve_angle`, repurpose `planar_angle`

Remove `dissolve_angle: FloatProperty` from `AIOPT_Properties`. The flat-surface merge is now governed entirely by `planar_angle` (the post-pass), which remains at its 5° default. Users who want more aggressive flat-region collapse can raise `planar_angle` to 10–15°.

Remove the `dissolve_angle` slider from the Decimate panel in `AIOPT_PT_decimate_panel`.

Remove `dissolve_angle` from `SAVEABLE_PROPS`.

### Property change: `protect_uv_seams` default True, forced migration

Change `protect_uv_seams: BoolProperty(default=False)` to `default=True` in `AIOPT_Properties`.

Unlike the `fix_manifold` migration (which respects the user's intent by translating to the safe default), this migration **overrides the saved value**: any saved config that has `protect_uv_seams: False` loads as `True`. Rationale: the reason users may have toggled it off was the texture-bleed symptom, which we now know was not caused by seam protection. The correct behavior is to default everyone to on.

### `load_defaults()` migrations, consolidated

In order, on JSON load:

1. `fix_manifold` key present → translate to `manifold_method` per the table above, then drop `fix_manifold`.
2. `dissolve_angle` key present → drop it. No translation.
3. `run_uv_dilate` and `uv_dilate_pixels` keys present → drop them. No translation.
4. `protect_uv_seams` key present with value False → force to True. (Value True already matches the new default, no change needed, but same path.)

### Debug logging: remove

The per-pass `print()` statements in `decimate_single` (dissolve pre-pass count, per-pass COLLAPSE counts, planar post-pass count) were added during investigation. Remove them before shipping — they're noise in the Blender console for normal users. If we want them back later they can live behind a debug toggle.

### CHANGELOG updates

Under the existing `## [1.8.0]` heading:

- **Remove** the Dilate UV Gutters entry from `### Added`.
- **Revise** the "Black texture smears" entry under `### Fixed` — the smears are fixed by defaulting the Manifold Fix method away from Toolbox, not by dilation. New wording focuses on the actual root cause.
- **Add** a `### Changed` entry explaining that `Fix Manifold` boolean became `Manifold Fix` enum, and that the default changed from "use Toolbox when available" to "Fill Holes" because the Toolbox was damaging thin-shell AI meshes.
- **Add** a `### Fixed` entry for the multi-pass face-count regression: explain the n-gon/re-triangulate interaction and that the fix is to triangulate up front, drop the `dissolve_limited` pre-pass, and set `use_collapse_triangulate=False`. Multi-pass at aggressive ratios (0.05, 0.1) now reaches face counts close to the UI estimate instead of overshooting by 30–50%.
- **Revise** the existing 1.8.0 `Passes` entry under `### Added` — it currently says "the dissolve pre-pass and UV seam protection run once up front; only the COLLAPSE modifier runs per iteration." The dissolve pre-pass no longer exists; rewrite to say the mesh is triangulated up front and each pass runs only the COLLAPSE modifier.
- **Remove** the `Dissolve Angle` references from any description that mentions it. (Currently mentioned only implicitly via the Decimate step defaults; no dedicated CHANGELOG entry exists to remove.)
- **Add** a `### Changed` entry: `Protect UV Seams` now defaults on. Existing saved configs that had it off are force-migrated to on (the previous "off" recommendation was based on a misdiagnosis that's since been corrected).
- **Revise** the existing 1.8.0 `Protect UV Seams` entry under `### Added` to reflect the new default.

## Data flow

No pipeline-order changes. The only affected step is Fix Geometry, and the change is internal dispatch:

```
Fix Geometry step
  └─ for each mesh: fix_geometry_single(obj, props)
       └─ dissolve_degenerate → remove_doubles → [optional] recalculate_normals
           └─ switch props.manifold_method:
                OFF          → skip
                FILL_HOLES   → _fill_holes_manifold()
                PRINT3D      → print3d_clean_non_manifold() (or fallback)
           └─ delete_loose
```

## Testing

Manual verification only (consistent with existing code — no test framework in the repo):

1. **Fresh install / fresh scene**: default `manifold_method = "FILL_HOLES"`, camo-cover mesh pipeline comes out clean. Verifies the default behavior is safe.
2. **Plugin not installed**: 3D Print Toolbox radio row is greyed in the Geometry panel. Cannot click to select. Verifies disabled-state UI.
3. **Plugin installed, user selects Toolbox on a solid mesh (e.g. a barrel alone)**: pipeline runs, step reports "3D Print Toolbox" as the method used. Verifies the advanced path still works for users who want it.
4. **Plugin installed, user selects Toolbox, then uninstalls plugin mid-session, runs pipeline**: step falls through to Fill Holes without failing the pipeline. Verifies the in-band try/except safety.
5. **Load a saved defaults JSON from 1.7.x that has `fix_manifold: true`**: opens with `manifold_method = "FILL_HOLES"` (not PRINT3D — we chose the safe default). Verifies migration.
6. **Load a saved defaults JSON with `fix_manifold: false`**: opens with `manifold_method = "OFF"`. Verifies the "Off" branch of migration.
7. **Full pipeline on camo-cover mesh with default settings, no Dilate UV Gutters step in the progress panel**: pipeline time drops by ~45s vs. the session's recent runs. Cover comes out clean. Verifies dilation removal.
8. **Decimate 3 passes at ratio 0.05 on camo-cover mesh**: UI estimate shows ~57k; actual result lands within 10% of that (target range roughly 55k–65k) rather than the current ~76k. Verifies the triangulate-up-front + no-pre-dissolve + use_collapse_triangulate=False rework.
9. **Decimate 1 pass at ratio 0.5 on a CAD barrel mesh**: rework must not regress the simple case. Actual face count lands close to 50% of input.
10. **Fresh install, `Protect UV Seams` toggle defaults to on**: open a fresh scene with no saved config, check the Decimate panel. Verifies the default change.
11. **Load a saved defaults JSON from 1.8.0-beta with `protect_uv_seams: false`**: opens with the toggle on. Verifies the forced migration.
12. **Decimate panel no longer shows a `Dissolve Angle` slider**; planar post-pass still shows `Planar Angle`. Verifies UI cleanup.
13. **System console shows no per-pass face-count `print()` spam** during a pipeline run. Verifies debug logging removal.

## Risks

- **Users who relied on the Toolbox on watertight meshes** will silently migrate to Fill Holes and notice a quality regression on those specific meshes. Mitigation: the option is still there, one click away, and the CHANGELOG explains what changed and how to restore the old behavior.
- **`fix_manifold` key lingering in old JSON configs** that someone merges into a 1.8.0+ install. The migration handles it on load, but if the user re-saves without changing anything we rely on `save_defaults` having already dropped the key (which it does, since `fix_manifold` is no longer in `SAVEABLE_PROPS`).
- **Enum name chosen as `manifold_method` rather than `fix_manifold`** is intentional — we don't want any historical code paths keyed on `fix_manifold` the boolean to accidentally still work and mask a migration bug. A rename surfaces incomplete migrations as AttributeErrors at register time.
- **Triangulating up front increases polygon count before decimation starts** — a pre-dissolved 130k-n-gon mesh will inflate to ~500k triangles going into pass 1. Pipeline memory use peaks higher for a brief window. Not expected to matter on the test mesh (~1.1M input, Blender handles multi-M meshes), but worth noting.
- **`Protect UV Seams` forced migration** ignores the user's previously-expressed preference. This is a deliberate override justified by the misdiagnosis that motivated the original off-default. If a user really wants it off, they re-toggle. No way to distinguish "saved off by habit" from "saved off intentionally" in the JSON, so we migrate all to on.
- **Passes 2/3 may still be near-no-ops even with triangulate-up-front.** The COLLAPSE solver hitting a wall on thin-shell geometry is a separate underlying limitation that this rework does not fully address. The rework fixes pass 1 (which was growing face count before) and makes the math honest for users who set single-pass with a low ratio. Aggressive multi-pass at 0.05 may still overshoot the estimate, but by far less. If test 8 fails (actual > 70k at 3 passes × 0.05 on the camo mesh), the acceptance bar drops to "at minimum, no worse than the pre-rework 76k result" — full multi-pass effectiveness on thin-shell becomes a follow-up investigation.
