# Topology Improvements — Design

Derived from [docs/plan/topology-improvements.md](../../plan/topology-improvements.md) (Gemini suggestions). Only the high/medium-value subset is in scope; the rest are explicitly out of scope (see end of document).

## Goals

- Improve decimation quality on AI meshes by protecting UV layout.
- Make the Symmetry step reliable regardless of source-model origin.
- Automate floor placement so AI exports can go straight to downstream use.
- Remove small cleanup papercuts (no-op progress call, silent dependency state, zero-area faces surviving cleanup, hand-tuned cage distance).

## Non-Goals

- No bmesh rewrite of `bpy.ops` calls. The existing modal + shading workaround already addresses the viewport-refresh concern Gemini raised.
- No Voxel-Wrap remesh, texture atlasing, PBR texture renaming, cage-object generation, or visual analyze overlay. Punted to a future spec if demand appears.

## Phased Plan

Each phase is independently shippable, testable, and reversible. Each phase ends with a `CHANGELOG.md` entry per the project convention.

---

### Phase 1 — Fix `progress_update(0)` No-Op

**Problem.** [src/utils.py:64-68](../../../src/utils.py#L64-L68) calls `context.window_manager.progress_update(0)` inside `log()`. No `progress_begin` ever ran, so this is a silent no-op left over from an earlier design.

**Change.** Delete the `progress_update` call. Keep the console `print` and the `log()` signature.

**Files.** `src/utils.py` only.

**Testing.**
1. Run the full pipeline on any test mesh.
2. **Expected:** identical behavior to before. Console log messages still appear. No visible change — this is dead-code removal.
3. Confirm no regressions in the modal progress panel (it uses the modal operator's own state, not `progress_update`).

---

### Phase 2 — Dependency Status Line

**Problem.** 3D Print Toolbox availability is checked silently. Users see a warning only after they enable `fix_manifold`. They don't know the fallback is active.

**Change.** Add a passive status line at the top of the main panel in [src/panels.py](../../../src/panels.py):

- ✓ icon + "3D Print Toolbox available" when `is_print3d_available()` is True
- ⚠ icon + "3D Print Toolbox not installed — manifold fallback active" when False

Use existing Blender UI icons (`CHECKMARK`, `ERROR`). Always visible, compact (one row).

**Files.** `src/panels.py` only.

**Testing.**
1. With 3D Print Toolbox enabled in Blender: open the sidebar. **Expected:** green checkmark row stating availability.
2. Disable 3D Print Toolbox in Edit → Preferences → Add-ons. Reopen the sidebar. **Expected:** warning row stating fallback active.
3. Re-enable and confirm the line switches back without restarting Blender.

---

### Phase 3 — Degenerate Dissolve Pre-Pass

**Problem.** AI meshes often contain zero-area faces and zero-length edges. `remove_doubles` handles coincident verts but not thin slivers with non-zero vertex spacing. These survive into decimation and can cause normal artifacts.

**Change.** In [src/geometry.py](../../../src/geometry.py) `fix_geometry_single`, add a pre-pass *before* `remove_doubles`:

```python
bpy.ops.mesh.dissolve_degenerate(threshold=1e-6)
```

Threshold `1e-6` per Gemini's suggestion. No new property — it's an always-on cleanup step with a near-zero threshold that only affects truly degenerate geometry.

**Files.** `src/geometry.py` only.

**Testing.**
1. Load a test AI mesh (one with known thin slivers — a Meshy or Tripo export is ideal).
2. In Edit Mode, note the polygon count via `Mesh → Statistics`.
3. Run **Fix Geometry** only (disable other steps).
4. **Expected:** face count drops slightly (the degenerate faces). Visual inspection shows no surface-detail loss — only slivers vanish.
5. On a known-clean mesh, confirm face count is unchanged (the pre-pass is a no-op when there's nothing degenerate).

---

### Phase 4 — Auto-Ray Distance for Normal Bake

**Problem.** `bake_normal_map_for_decimate` uses a fixed `cage_extrusion_mm` from props ([geometry.py:442](../../../src/geometry.py#L442)). Correct value depends on model size — a 1mm extrusion on a 100m building bakes nothing; a 1m extrusion on a 5cm figurine misses detail.

**Change.** Add an auto mode to the property:

- Add `auto_cage_extrusion: BoolProperty` (default `True`) in [src/properties.py](../../../src/properties.py).
- In `bake_normal_map_for_decimate`, when `auto_cage_extrusion` is True, compute the mesh bounding-box diagonal and use 1% of the max dimension (Gemini's formula). Otherwise use the existing `cage_extrusion_mm`.
- In [src/panels.py](../../../src/panels.py), show `cage_extrusion_mm` only when `auto_cage_extrusion` is False.

**Files.** `src/properties.py`, `src/geometry.py`, `src/panels.py`. Add `auto_cage_extrusion` to `SAVEABLE_PROPS` in `utils.py`.

**Testing.**
1. Load a small test mesh (< 1m) and run Decimate with **Bake Normal Map** enabled, `auto_cage_extrusion = True`.
2. **Expected:** clean normal map, no black patches (coverage gaps).
3. Scale the mesh up 100x and rerun. **Expected:** still clean — the auto distance scales with the model.
4. Disable `auto_cage_extrusion`. **Expected:** the `cage_extrusion_mm` field appears and behaves as today.

---

### Phase 5 — UV Seam Protection Before Decimate

**Problem.** `DECIMATE` collapse mode can merge edges that sit on UV seams, corrupting the texture layout. The `delimit={"UV"}` on `dissolve_limited` ([geometry.py:483](../../../src/geometry.py#L483)) helps for the dissolve pre-pass but not for the main collapse.

**Change.** In [src/geometry.py](../../../src/geometry.py) `decimate_single`, before adding the `DECIMATE` modifier:

1. If the mesh has a UV map but no marked seams, auto-mark seams from UV islands:
   ```python
   bpy.ops.object.mode_set(mode="EDIT")
   bpy.ops.mesh.select_all(action="SELECT")
   bpy.ops.uv.seams_from_islands()
   ```
2. Mark all seam edges as Sharp:
   ```python
   bpy.ops.mesh.select_all(action="DESELECT")
   # select seams via bmesh then set sharp
   ```
3. Then proceed with the existing decimation.

The `DECIMATE` modifier's `use_symmetry` / collapse behavior respects Sharp edges as boundaries, preventing seam collapse.

Skip the seams_from_islands step entirely if the mesh has no UV map (falls back to current behavior).

**Files.** `src/geometry.py` only.

**Testing.**
1. Load an AI mesh with a visible textured surface (Meshy export works well).
2. Note the seam visibility in the viewport with Edit Mode seam display on.
3. Run Decimate at a low ratio (0.3).
4. **Expected:** texture remains aligned on the decimated mesh — no visible seam-crossing artifacts. Compare against the current behavior (check out the prior commit) on the same mesh at the same ratio; the new version should show fewer/no texture smears at UV boundaries.
5. Test on a mesh without UVs (e.g., a plain cube). **Expected:** decimate still runs, no errors, no seam marking attempted.

---

### Phase 6 — Symmetry Origin Snap

**Problem.** `detect_and_apply_symmetry` applies a Mirror modifier, which pivots around the object origin. If the origin isn't on the symmetry plane, the mirrored half is offset — producing a gap or overlap.

**Change.** In [src/geometry.py](../../../src/geometry.py) `detect_and_apply_symmetry`, after computing `center` along the symmetry axis (already exists at line 340), before adding the Mirror modifier:

1. Move the object origin so that the symmetry-axis coordinate matches `center` in world space.
2. This requires translating `obj.location` and counter-translating mesh vertices so the mesh doesn't visibly move.

Implementation sketch: use `bpy.ops.object.origin_set(type='ORIGIN_CURSOR')` after placing the 3D cursor at the computed plane, or translate via direct matrix manipulation. Prefer the matrix path to avoid touching the 3D cursor.

No UI change. Always-on inside symmetry.

**Files.** `src/geometry.py` only.

**Testing.**
1. Load an asymmetric-origin test case: a humanoid mesh whose origin is at the feet (not the centerline). Confirm Symmetry currently produces a gap/overlap when applied.
2. Enable Symmetry, run the pipeline.
3. **Expected:** mirror is clean, no gap or overlap at the symmetry plane, regardless of where the origin started.
4. Test on a mesh whose origin is already on the plane. **Expected:** no visible change — the snap is a no-op in that case.

---

### Phase 7 — Floor Snap Pipeline Step

**Problem.** AI models always arrive centered on origin (mid-height at Z=0). Users manually drop them to the floor every time.

**Change.**

- Add new step toggle `run_floor_snap: BoolProperty` (default `True`) in [src/properties.py](../../../src/properties.py).
- Add a new operator `AIOPT_OT_floor_snap` in [src/operators.py](../../../src/operators.py) and function in `geometry.py` that: computes the minimum Z of all selected mesh vertices in world space, then translates each mesh up by that amount. XY is left alone (user hasn't asked for re-centering, and horizontal offsets often carry intent — pose, multi-object scenes).
- Register the step in the modal pipeline in `operators.py` with a setup/tick/teardown trio.
- Add to `SAVEABLE_PROPS`.
- Add a panel row under the existing pipeline-step toggles.
- Order: runs after Decimate, before Export (so the exported GLB reflects the snap).

**Files.** `src/properties.py`, `src/operators.py`, `src/geometry.py`, `src/panels.py`, `src/__init__.py` (register operator), `src/utils.py` (SAVEABLE_PROPS), `CHANGELOG.md`.

**Testing.**
1. Load any AI mesh centered on origin (bottom below Z=0, top above).
2. Enable **Floor Snap** step and run the pipeline (or just the standalone operator).
3. **Expected:** lowest vertex of the mesh ends up at Z=0. XY position unchanged.
4. With multiple mesh objects selected, run again. **Expected:** all objects shift by the same amount (the group's lowest point lands at Z=0), preserving their relative vertical relationships. (If simpler per-object logic is chosen, document that clearly — decision point: confirm at implementation time.)
5. Disable the toggle: pipeline behaves as today.
6. Load and save defaults: toggle state persists.

---

## Cross-Cutting Concerns

- **Version bump.** Each phase adds a `CHANGELOG.md` entry. Phase 4 and Phase 7 add new properties, so they need a minor version bump in `pyproject.toml`. Phases 1-3, 5, 6 can share a patch bump.
- **Undo.** All phases run inside the existing pipeline and inherit the single undo snapshot pushed at pipeline start ([operators.py:372](../../../src/operators.py#L372)).
- **Cancellation.** Phase 7 is a simple loop — respect `token.check()` between objects. Phases 3-6 modify single-object functions already called inside cancellable loops; no new cancel points needed.
- **Tests.** No automated test suite exists; all validation is manual per the Testing block of each phase.

## Explicitly Out of Scope (Gemini Suggestions Dropped)

- Blanket bmesh rewrite of `bpy.ops`
- Voxel-Wrap remesh
- UV-boundary-aware hole filling
- Cage-object generation (Phase 4 uses the auto-distance approach instead)
- PBR texture renaming
- Texture atlasing (`atlas_textures`)
- Visual viewport feedback after analyze
- Standalone `check_dependencies` operator (Phase 2 covers this passively)
