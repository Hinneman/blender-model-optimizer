# Hard UV Seam Constraint for Decimate

**Status:** Design approved, awaiting implementation plan
**Target version:** 1.8.0 (unreleased)
**Date:** 2026-04-19

## Problem

After the Decimate step, textures on AI-generated meshes show severe color bleed across UV island boundaries. A barrel mesh's brown rim smears in triangular wedges across its teal lid (and vice versa). The current soft-hint seam protection (`Protect UV Seams`, added 1.7.0, toggled off by default in 1.8.0) reduces the artifact but does not eliminate it: the COLLAPSE decimator treats Sharp edges as a cost preference, not a hard constraint, and will still collapse across seams at aggressive ratios.

Two failure modes currently pull in opposite directions:

- **Seam protection ON** → fan artifacts on meshes with fragmented UVs (e.g. AI camo textures with hundreds of tiny islands).
- **Seam protection OFF** → texture smearing on meshes with clean UVs (e.g. the barrel).

## Goal

Eliminate texture smearing at UV island boundaries by making seams a **topological constraint** the decimator physically cannot violate, replacing the existing soft-hint mechanism. Accept a slightly higher post-decimate face count on fragmented-UV meshes as the tradeoff for visually correct textures.

## Non-Goals

- Texture dilation / UV edge padding. Considered and rejected: the observed UV drift spans many atlas pixels, far beyond what any reasonable dilation width (4–16 px) can cover.
- Rebaking to a clean atlas. Considered and rejected: too invasive, expensive (Cycles bake), and loses per-island detail.
- Exposing weld tolerance as a user property. Deferred until a mesh exhibits micro-gaps in practice.

## Architecture

Modify `decimate_single` in [src/geometry.py](src/geometry.py) to split the mesh topologically along UV seams before decimation and re-weld the split vertices after. The decimator cannot collapse an edge that does not exist as a shared edge.

**Flow inside `decimate_single`:**

1. (existing) dissolve-limited pre-pass
2. **NEW — Seam split**: auto-generate seams from UV islands if absent, then edge-split along seams. Vertices on seams duplicate into disconnected copies (one per incident island). Record the world-space positions of split vertices.
3. (existing) N passes of COLLAPSE DECIMATE. Seam crossings are now physically impossible.
4. **NEW — Seam restitch**: merge-by-distance scoped to the recorded seam positions only, with a tight tolerance. Re-welds topology without touching unrelated vertices.
5. (existing) planar post-pass
6. (existing) final cleanup (remove doubles, normals, delete loose)

## Components

### `_split_uv_seams(obj) -> list[tuple[float, float, float]]`

New helper in [src/geometry.py](src/geometry.py). Replaces `_protect_uv_seams`.

- If the mesh has no UV layer, returns an empty list (no-op).
- If the mesh has UVs but no explicit seams, calls `bpy.ops.uv.seams_from_islands()` (same behavior as today's `_protect_uv_seams`).
- Collects the world-space positions of all vertices incident to seam edges, quantized to the restitch tolerance.
- Calls `bpy.ops.mesh.edge_split(type='EDGE')` on seam edges, duplicating shared vertices into per-island copies.
- Returns the list of recorded positions for the restitch phase.

**Why positions, not indices:** the DECIMATE modifier rewrites the vertex array completely. Only spatial coordinates survive the collapse passes reliably.

### `_restitch_seams(obj, seam_positions, threshold_m)`

New helper in [src/geometry.py](src/geometry.py). Performs a scoped merge-by-distance:

- Builds a spatial hash of `seam_positions` at cell size `threshold_m`.
- Iterates current mesh vertices, selecting only those within `threshold_m` of any recorded seam position.
- Uses `bmesh.ops.remove_doubles` on the selected subset so unrelated nearby vertices are not touched.

**Tolerance:** `min(props.merge_distance_mm, 0.01) / 1000` meters. Must be tighter than the user's global merge distance — seam pairs may drift slightly apart during decimate, but if the user's merge distance is large (say 1 mm), we do not want to weld unrelated geometry along former seams.

### Modified `decimate_single`

Replace the existing conditional `_protect_uv_seams` call (currently gated on `props.protect_uv_seams`) with the unconditional split-and-restitch flow. Behavior is always-on when UVs are present; no toggle.

### Property removal in `AIOPT_Properties`

- Remove `protect_uv_seams` BoolProperty from [src/properties.py](src/properties.py). It was added in 1.8.0 and 1.8.0 is unreleased, so no deprecation path is needed.

### Panel removal in `panels.py`

- Remove the `Protect UV Seams` checkbox from the Decimate section in [src/panels.py](src/panels.py).

## Data Flow & Edge Cases

**Clean UVs (barrel case):** rim and lid become topologically disconnected along the rim-to-lid boundary. Collapse passes operate independently on each island's interior. Restitch re-welds the originally-coincident pairs. Color bleed is eliminated by construction.

**Fragmented UVs (camo case):** hundreds of tiny islands become isolated flaps with no shared edges to neighbors. Boundary triangles cannot collapse (no edge to collapse into a neighbor). Replaces the old fan-artifact failure mode with preserved boundary rings — visually acceptable; higher face count than unconstrained decimate.

**Face count impact on fragmented UVs:** decimate may undershoot the target ratio (e.g. 0.1 → 0.15–0.20) because many edges are unavailable for collapse. Accepted tradeoff. Users targeting maximum reduction on fragmented meshes already have `Planar Post-Pass` enabled by default (1.8.0), which aggressively flattens coplanar regions.

**Mesh with no UVs:** `_split_uv_seams` returns empty, restitch no-ops. Current code path unchanged.

**Mesh with UVs but a single island (no seams):** `seams_from_islands` produces no seams, split no-ops, decimate proceeds as today.

**Multi-pass decimate:** split runs once before the pass loop. Each pass cannot cross seam boundaries. Restitch runs once after all passes.

**Planar post-pass:** runs after restitch. Because restitch re-welds seam pairs into shared topology, the planar dissolver can legitimately merge coplanar faces across welded seams — desired behavior for flat regions. Keep the existing `delimit={'UV'}` on the planar modifier as cheap insurance.

**Overlapping UV islands:** `seams_from_islands` only detects UV-space boundaries. Two different 3D regions sharing the same UV area have no detected seam between them — no change in behavior vs. today.

## Error Handling

- **`edge_split` fails on degenerate mesh:** wrap in try/except, log a warning, skip the split. Decimate proceeds as it does today (no worse than current behavior).
- **Restitch leaves unwelded seam pairs** (drift greater than tolerance): log a warning with the count. User-visible symptom would be hairline gaps along former seams. Mitigation if observed in practice: expose tolerance as a property. Deferred.
- **Weld tolerance too broad:** mitigated structurally by scoping the weld to recorded seam positions only — unrecorded vertices cannot be merged.

## Testing

Manual validation in Blender against three meshes. No automated tests (consistent with existing add-on conventions).

1. **Barrel mesh (clean UVs, motivating case).** Decimate at 0.1 ratio. Expected: rim-to-lid boundary shows no color bleed; triangular brown wedges across the teal lid are gone.
2. **Camo mesh (fragmented UVs, regression case).** Decimate at 0.1 ratio. Expected: no radial fan artifacts; final face count is higher than unconstrained decimate but texture is visually preserved.
3. **Cube with no UVs (regression case).** Decimate at 0.5 ratio. Expected: unchanged behavior; no errors; no warnings.

## Release

- Version stays at **1.8.0** (unreleased).
- CHANGELOG edits under the existing `## [1.8.0] - 2026-04-19` heading:
  - Remove the `Protect UV Seams` toggle line from **Added**.
  - Remove the paragraph in **Changed** that explains the default-off rationale.
  - Add a new **Added** entry describing the topological seam-split-and-restitch mechanism and why it replaces the soft hint.
