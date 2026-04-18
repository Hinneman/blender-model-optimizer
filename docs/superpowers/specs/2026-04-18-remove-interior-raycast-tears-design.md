# Remove Interior Raycast — Reduce False Positives on Exterior Shell

## Problem

`_remove_interior_raycast` in [src/geometry.py:164-230](../../../src/geometry.py#L164-L230) casts 5 rays from each face along directions inside a very narrow cone around the face normal. The jitter offsets are:

```python
jitter_offsets = [
    Vector((0, 0, 0)),
    Vector((0.1, 0.1, 0)),
    Vector((-0.1, 0.1, 0)),
    Vector((0.1, -0.1, 0)),
    Vector((-0.1, -0.1, 0)),
]
```

Each offset is added to the unit face-normal and the result re-normalized. With offsets of magnitude ≈0.14, the resulting rays stay within ~6° of the normal. For exterior faces in concave regions of AI meshes (e.g., the spine near the canopy on an aircraft fuselage), all 5 rays exit in roughly the same direction and hit the opposite interior wall of the fuselage. The hit is a back-face from the ray's POV, so `hit_normal · local_dir > 0` and `all_blocked` stays True. The exterior face gets flagged as interior and deleted — producing visible tears on the exterior shell.

## Non-Goals

- **No change to the "all rays blocked" rule.** The rule itself is sound; only the ray-direction sampling is the problem.
- **No new user-facing properties or panel changes.** This is a fix, not a feature.
- **No overhaul of the Enclosed Parts method.** Out of scope.
- **Not addressing interior false negatives** (debris that survives because Enclosed Parts can't see it and Ray Cast doesn't catch it either). The user prioritizes fixing exterior false positives.

## Change

Replace the 5-ray tight-cone `jitter_offsets` list with a 13-ray wider cone. The new sample pattern covers outward directions roughly up to 55° from the face normal, so exterior faces sitting in concavities have several rays pointing into open space rather than all clustering on the opposite wall.

### Sampling pattern

Conceptually:
- 1 ray along pure normal (0° from normal)
- 6 rays at ~30° from normal, evenly spaced around a circle
- 6 rays at ~55° from normal, evenly spaced around a circle, rotated 30° from the inner ring

After each offset is added to the face normal and re-normalized, this yields 13 distinct outward directions distributed across a ~55° cone.

### Precomputed offsets

No runtime spherical math. The new `jitter_offsets` is a static list of 13 `Vector` offsets, hand-computed to produce the angles above. Using the identity `tan(θ) = |offset_xy| / 1` (where 1 is the normal-direction component), the required offset magnitudes are:

- 30° ring: `|offset_xy| = tan(30°) ≈ 0.577`
- 55° ring: `|offset_xy| = tan(55°) ≈ 1.428`

Six positions around a circle at radius r: `(r·cos(k·60°), r·sin(k·60°), 0)` for k = 0..5.

Outer ring rotated 30°: `(r·cos(k·60°+30°), r·sin(k·60°+30°), 0)`.

The comment above the list should explain the geometric intent so a future reader understands why the magnitudes are what they are.

### Rule unchanged

The outer loop and the decision logic stay exactly as-is:

```python
all_blocked = True
for jitter in jitter_offsets:
    ...
    if not hit:
        all_blocked = False
        break
    if hit_normal.dot(local_dir) < 0:
        all_blocked = False
        break

if all_blocked:
    interior_faces.append(face)
```

Only the iterated list gets longer (5 → 13) and wider in angular spread.

## Files Touched

- `src/geometry.py` — only the `jitter_offsets` list and its explanatory comment.
- `CHANGELOG.md` — new `### Fixed` bullet under the next version.

No other code, no UI, no properties, no registration changes.

## Version

This is a bug fix. Add to a new patch version (e.g. `1.7.1`) on top of the just-released `1.7.0`. Bump `pyproject.toml` and add a new `## [1.7.1] - 2026-04-18` heading in `CHANGELOG.md`.

## Testing

Manual, in Blender, on the airplane AI mesh used during Phase 6/7 review.

1. Rebuild and install.
2. Load the airplane mesh (Messerschmitt Bf 109 from the screenshots).
3. Run only **Fix Geometry** and **Remove Interior** (Ray Cast method), everything else off.
4. **Expected:** the exterior tear on the fuselage spine/canopy area from pre-fix runs is gone or dramatically reduced. Face count reduction will be smaller than before (fewer false positives = fewer faces removed) — expect well under the previous 12,244 removed on this mesh.
5. **Regression test:** add a default cube, enable only Remove Interior (Ray Cast). Run. Expected: no faces removed (all 6 faces are exterior).
6. **Regression test:** load or construct a mesh with clear interior geometry (two nested cubes connected by one bridge edge, so Enclosed Parts wouldn't separate them). Run Remove Interior Ray Cast. Expected: the inner cube's faces are removed.

## Rollback

Single-commit revert reverts the `jitter_offsets` change. No migration concerns — no property added, no panel change.

## Future Work (Explicitly Out of Scope)

- If false positives persist, consider approach B from brainstorming: majority voting (e.g., 12 of 13 rays must hit back-faces instead of all 13).
- If false negatives dominate later, revisit Enclosed Parts or add a hybrid mode.
- Exposing a "cone angle" or "vote threshold" as a user-tunable property — only if needed; default-only is simpler.
