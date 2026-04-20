# Vertex-Group Seam Weighting for Decimate

**Status:** Design approved, awaiting implementation plan
**Target version:** 1.8.0 (unreleased)
**Date:** 2026-04-20

## Problem

The 1.7.0 `Protect UV Seams` mechanism marks UV-island seam edges as Sharp. Blender's COLLAPSE decimator treats Sharp edges as a cost preference, not a hard constraint, and still collapses across them at aggressive ratios. The resulting texture bleed (rim color smearing across lid, triangular wedges of adjacent islands) is the visible symptom.

A previous attempt (see [2026-04-19-hard-uv-seam-constraint-design.md](2026-04-19-hard-uv-seam-constraint-design.md)) tried a hard topological constraint — splitting the mesh along seams and restitching. On clean-UV meshes it eliminated bleed, but on AI-generated meshes with fragmented UVs (the add-on's primary target) the mesh broke into hundreds of disconnected flaps whose boundaries drifted too far apart during aggressive decimation for the restitch to re-weld, producing visible cracks and torn faces. That approach was reverted.

## Goal

Strengthen the existing soft-hint seam protection by replacing Sharp-edge marking with a vertex-group weight bias on the COLLAPSE modifier. The solver's collapse-cost landscape gains a tunable numerical preference against collapsing seam-adjacent vertices, rather than a binary edge flag that the solver can override. Mesh topology stays fully connected, so the failure mode of the hard-split approach (disconnected flaps) is impossible by construction.

## Non-Goals

- Eliminating seam bleed on highly fragmented UV meshes. The design intentionally degrades gracefully in that case: when most vertices are seam-adjacent, the bias cancels out and decimation proceeds ~normally. Users with fragmented meshes already benefit more from the 1.8.0 planar post-pass than from seam protection.
- Changing the `Protect UV Seams` toggle, its default (off), or its UI. The property and panel row stay exactly as shipped in the current 1.8.0 branch.
- Introducing a tunable weight slider. The 10× bias ratio (seam vs. non-seam) is hardcoded.

## Architecture

Replace the internals of `_protect_uv_seams` in [src/geometry.py](src/geometry.py). The function still detects UV islands, still auto-generates seams if absent. Instead of setting `edge.smooth = False` on seam edges, it creates a vertex group named `AIOPT_Seam_Protect` and assigns weights:

- **Seam-endpoint vertices + their one-ring neighbors:** weight 1.0
- **All other vertices:** weight 0.1

The function returns the group name (`"AIOPT_Seam_Protect"`) on success, or `None` if the mesh has no UVs or seam detection failed.

In `decimate_single`, when the caller has already checked `props.protect_uv_seams`, the helper runs once before the pass loop. Each COLLAPSE modifier inside the loop gains:

```python
mod.vertex_group = seam_group_name
mod.invert_vertex_group = True
```

Blender propagates vertex-group weights through collapse automatically. A single group created once before pass 1 survives all N passes correctly — surviving vertices inherit their parent's weight, so seam-adjacent vertices stay weighted across iterations.

After the collapse loop (and the planar post-pass, which ignores vertex groups), `decimate_single` removes the `AIOPT_Seam_Protect` group so the exported mesh doesn't ship a leftover diagnostic group.

## Components

### `_protect_uv_seams(obj)` — rewritten

Returns `Optional[str]`: the vertex-group name on success, or `None` on no-op / failure.

Flow:

1. If `obj.data.uv_layers` is empty, return `None`.
2. Auto-mark seams from islands if none exist (`bpy.ops.uv.seams_from_islands()`), inside a try/except for degenerate UV maps. On failure, log and return `None`.
3. Collect seam-endpoint vertices: iterate `bm.edges`, for each `edge.seam == True` add both endpoint `BMVert` objects to a set.
4. Expand to one-ring neighborhood: for each vertex in the seam-endpoint set, add every vertex reachable through any incident edge. Union with the original set.
5. Remove any existing `AIOPT_Seam_Protect` vertex group (prevents stale weights from a prior run).
6. Create a new `AIOPT_Seam_Protect` vertex group.
7. For each vertex in the expanded set, assign weight 1.0. For every other vertex, assign weight 0.1.
8. Return `"AIOPT_Seam_Protect"`.

No more `edge.smooth = False` mutations.

### Modified `decimate_single`

Replace the current call site:

```python
if getattr(props, "protect_uv_seams", False):
    _protect_uv_seams(obj)
```

with:

```python
seam_group_name = None
if getattr(props, "protect_uv_seams", False):
    seam_group_name = _protect_uv_seams(obj)
```

Inside the pass loop, after `mod = obj.modifiers.new(...)` and `mod.decimate_type = "COLLAPSE"`, add:

```python
if seam_group_name:
    mod.vertex_group = seam_group_name
    mod.invert_vertex_group = True
```

After the planar post-pass, but before the final cleanup (remove-doubles / normals / delete-loose), add:

```python
if seam_group_name:
    group = obj.vertex_groups.get(seam_group_name)
    if group is not None:
        obj.vertex_groups.remove(group)
```

### No property, panel, or CHANGELOG-entry changes

`src/properties.py` and `src/panels.py` are not touched. The existing `Protect UV Seams` toggle and its default-off behavior are preserved.

`CHANGELOG.md` gets a targeted edit: the `Added` entry for `Protect UV Seams` under 1.8.0 is updated to describe the weighted-vertex-group mechanism instead of Sharp edges. No new entry — same feature, stronger implementation.

## Data Flow & Edge Cases

**Clean UVs (barrel case):** Seam endpoints form the rim-to-lid boundary ring. One-ring expansion adds the row just inside each island. These get weight 1.0; the rest of the mesh gets 0.1. The solver collapses interior geometry freely and resists the protected band. Mesh stays fully connected — no cracks possible because no topology change occurs.

**Fragmented UVs (camo case):** Many seam endpoints, so one-ring expansion covers a large fraction of the mesh (often 30-50%). The weight ratio still biases the solver, but when most vertices are weighted the bias partially cancels out and decimation proceeds close to unconstrained. This is the intended graceful degradation — the old Sharp-edge approach over-constrained the solver and caused fan artifacts; the old topological approach fragmented the mesh. Weighted bias naturally softens on fragmented meshes.

**No-UV mesh:** Helper returns `None`. `seam_group_name` is `None`. The `if seam_group_name:` guards inside the loop and at cleanup skip the vertex-group wiring. Identical to today's no-UV behavior.

**Mesh with UVs but a single island (no seams):** `seams_from_islands` produces no seam edges. Seam-endpoint set is empty. One-ring expansion is empty. Every vertex gets weight 0.1. With `invert_vertex_group=True`, uniform weights uniformly scale collapse cost — no *relative* preference. Collapse proceeds identically to unweighted. Wasteful but harmless.

**Multi-pass (`Passes` > 1):** Group is created once before the loop. Each COLLAPSE modifier references the same group by name. Blender propagates weights through collapse (documented behavior). After N passes, surviving seam-adjacent vertices remain weighted. Cleanup removes the group once at the end.

**Planar post-pass (DISSOLVE):** Ignores vertex groups entirely. Runs with `delimit={'UV'}` for native UV-island preservation. Order doesn't matter — can run before or after cleanup of the seam group. We place cleanup after the post-pass for a single well-defined cleanup location.

**Existing group named `AIOPT_Seam_Protect` from a prior run:** Step 5 of the helper removes it before creating a fresh one. Prevents stale weights from leaking between invocations.

**User-defined group with the same name:** Overwritten. The `AIOPT_` prefix is a reserved-namespace convention for this add-on; collisions are user error.

## Error Handling

- **`bpy.ops.uv.seams_from_islands()` raises RuntimeError:** caught, logged via `print("  [AI Optimizer] Seam detection failed: <exc>")`, helper returns `None`. Caller skips vertex-group wiring. Same fallback as today's no-seam behavior.
- **Vertex-group creation raises RuntimeError:** caught, logged, return `None`.
- **Cleanup at end of `decimate_single`:** guarded by `if group is not None`. Idempotent.

## Testing

Manual validation in Blender. No automated tests (consistent with existing add-on conventions).

1. **Barrel mesh (clean UVs, motivating case).** Enable `Protect UV Seams`. Decimate ratio 0.1, Passes=1, Planar Post-Pass off. Expected: no texture bleed at rim-to-lid boundary; mesh fully watertight (no cracks, no torn faces); face count near target.
2. **Camo mesh (fragmented UVs, regression case).** Enable `Protect UV Seams`. Decimate ratio 0.1, Passes=1, Planar Post-Pass on. Expected: no catastrophic geometry damage; face count lands near target ratio; texture quality at least as good as running with the toggle off.
3. **Cube with no UVs.** Decimate ratio 0.5. Expected: no errors; no warnings about missing UV layers; unchanged behavior from today.
4. **Multi-pass regression.** Barrel mesh, Passes=3, ratio=0.1, `Protect UV Seams` on. Expected: clean rim-to-lid transition; face count ~10% of original; no cracks. Confirms vertex group survives across passes.
5. **Toggle-off regression.** Barrel mesh, `Protect UV Seams` off. Expected: identical to unconstrained 1.7.1 decimate — bleed returns, but that's the opt-out choice.
6. **Vertex-group cleanup check.** After any successful run with `Protect UV Seams` on, verify via Object Data Properties → Vertex Groups that `AIOPT_Seam_Protect` is absent.

## Release

- Version stays at **1.8.0** (unreleased).
- Only [src/geometry.py](src/geometry.py) changes functionally. [CHANGELOG.md](CHANGELOG.md) gets a targeted edit to the existing 1.8.0 `Added` entry for `Protect UV Seams`, rewording it to describe the vertex-group mechanism.
- No property, panel, or version changes.
