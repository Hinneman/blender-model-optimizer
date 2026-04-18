# Remove Interior Raycast Tears — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 5-ray narrow-cone jitter in `_remove_interior_raycast` with a 13-ray wider-cone sample pattern to reduce exterior false positives on thin-walled AI meshes.

**Architecture:** Single-file change in `src/geometry.py`. The decision rule ("all rays blocked → interior") is unchanged; only the list of ray-direction offsets gets wider and denser. Ship under the pending `1.7.1` release, which already contains a fix to `is_print3d_available` — both fixes land together.

**Tech Stack:** Blender Python API (`bpy`, `mathutils.Vector`). Manual validation in Blender (no automated tests).

**Reference spec:** [docs/superpowers/specs/2026-04-18-remove-interior-raycast-tears-design.md](../specs/2026-04-18-remove-interior-raycast-tears-design.md).

**Conventions:**
- **Never commit.** The user commits. Stage/edit only.
- **No worktrees.** Work in the main checkout on a branch like `fix/remove-interior-tears`.
- **Update `CHANGELOG.md`.** New patch version `1.7.1` under a new heading.
- **Build:** `python build.py` → `build/model-optimizer-addon.py`.
- **Lint:** `ruff check src/`, format: `ruff format src/`.

---

## Phase 1 — Widen the Raycast Sample Cone

**Version:** patch bump `1.7.0` → `1.7.1`.

**Files:**
- Modify: `src/geometry.py` — only the `jitter_offsets` list and its comment inside `_remove_interior_raycast` (lines ~183-192).
- Modify: `pyproject.toml` — version.
- Modify: `CHANGELOG.md` — new `[1.7.1]` heading with `### Fixed` entry.

### Task 1.1: Replace `jitter_offsets` with the 13-ray wider pattern

- [ ] **Step 1: Edit `src/geometry.py`.**

Locate the `_remove_interior_raycast` function (search for `def _remove_interior_raycast`). Inside it, find these lines (currently around lines 183-192):

```python
    # Small offset to avoid self-intersection
    OFFSET = 0.001
    # Jitter directions around the normal
    jitter_offsets = [
        Vector((0, 0, 0)),
        Vector((0.1, 0.1, 0)),
        Vector((-0.1, 0.1, 0)),
        Vector((0.1, -0.1, 0)),
        Vector((-0.1, -0.1, 0)),
    ]
```

Replace the `jitter_offsets` block (keep `OFFSET = 0.001` line unchanged) with:

```python
    # Small offset to avoid self-intersection
    OFFSET = 0.001
    # Sample 13 outward directions across a ~55° cone around the face normal.
    # Each offset is added to the unit normal and re-normalized before casting.
    # Using a wider cone (vs. the previous ~6° cluster) lets exterior faces in
    # concave regions have at least one ray exit to open space, which breaks
    # the "all blocked" rule and prevents them from being flagged as interior.
    #
    # Layout:
    #   - 1 ray along pure normal           (0° from normal)
    #   - 6 rays at ~30° from normal        (inner ring, r = tan(30°) ≈ 0.577)
    #   - 6 rays at ~55° from normal        (outer ring, r = tan(55°) ≈ 1.428),
    #     rotated 30° from the inner ring so the two rings don't line up.
    jitter_offsets = [
        # Pure normal
        Vector((0.0, 0.0, 0.0)),
        # Inner ring at ~30°, 6 rays spaced 60° apart
        Vector((0.577, 0.0, 0.0)),
        Vector((0.289, 0.500, 0.0)),
        Vector((-0.289, 0.500, 0.0)),
        Vector((-0.577, 0.0, 0.0)),
        Vector((-0.289, -0.500, 0.0)),
        Vector((0.289, -0.500, 0.0)),
        # Outer ring at ~55°, 6 rays rotated 30° from the inner ring
        Vector((1.237, 0.714, 0.0)),
        Vector((0.0, 1.428, 0.0)),
        Vector((-1.237, 0.714, 0.0)),
        Vector((-1.237, -0.714, 0.0)),
        Vector((0.0, -1.428, 0.0)),
        Vector((1.237, -0.714, 0.0)),
    ]
```

**Verify the geometry:**
- Inner ring: `(r·cos(k·60°), r·sin(k·60°), 0)` for k = 0..5 with `r = tan(30°) ≈ 0.5774`.
- Outer ring: `(r·cos(k·60° + 30°), r·sin(k·60° + 30°), 0)` for k = 0..5 with `r = tan(55°) ≈ 1.4281`.
  - `cos(30°) ≈ 0.866`, `sin(30°) ≈ 0.5`, so outer-ring offsets are `(1.4281·0.866, 1.4281·0.5, 0) = (1.237, 0.714, 0)` and rotations thereof.

The values in the block above round to 3 decimals and match these formulas.

The decision loop below the list stays completely unchanged.

- [ ] **Step 2: Lint.**

Run: `ruff check src/`. Expect: all checks passed.

Run: `ruff format --check src/`. Expect: 8 files already formatted (or auto-apply if not).

- [ ] **Step 3: Build.**

Run: `python build.py`. Expect: `Built: .../build/model-optimizer-addon.py` at the new `(1, 7, 1)` version (after Task 1.2 bumps it). For this task alone, the build just needs to succeed without errors.

### Task 1.2: Changelog entry

**Context:** `pyproject.toml` is already at `1.7.1` and `CHANGELOG.md` already has a `## [1.7.1] - 2026-04-18` heading (from a separate pending fix to `is_print3d_available`). This task appends the Remove Interior fix to that existing heading — no version bump.

- [ ] **Step 1: Append to existing `## [1.7.1]` section in `CHANGELOG.md`.**

Under the existing `## [1.7.1] - 2026-04-18` heading, inside its `### Fixed` subsection, add a second bullet ALONGSIDE the existing `is_print3d_available` bullet:

```markdown
- **Remove Interior (Ray Cast) no longer tears the exterior shell.** The raycast sampler now covers a wider ~55° cone with 13 rays instead of a narrow ~6° cone with 5 rays. Exterior faces in concave regions (fuselage spine, canopy fairing on AI aircraft meshes) previously had all 5 narrow-cone rays land on the opposite interior wall and got deleted as false positives. The wider cone lets at least one ray escape to open space, correctly classifying such faces as exterior.
```

Do NOT create a new `### Fixed` section. Do NOT change the version number. Leave everything else untouched.

- [ ] **Step 2: Rebuild and verify version.**

Run: `python build.py`.

Expect the build output to include `Building with version (1, 7, 1)`.

### Task 1.3: Manual validation in Blender

- [ ] **Step 1: Install the rebuilt add-on.**

In Blender: Edit → Preferences → Add-ons → AI 3D Model Optimizer → Remove. Then Install from Disk → pick the freshly built `build/model-optimizer-addon.py`.

- [ ] **Step 2: Airplane mesh test (primary regression).**

1. Load the Messerschmitt Bf 109 AI mesh that showed the tear during Phase 7 review.
2. In the AI Optimizer panel, enable only **Clean & Prepare Geometry** and **Remove Interior** (Ray Cast method). Disable everything else.
3. Run the full pipeline.
4. **Expected:** the exterior tear on the fuselage spine/canopy area is gone or dramatically smaller compared to the pre-fix screenshot. Face count reduction from Remove Interior is smaller than the previous 12,244 (fewer false positives → fewer faces removed). Overall airplane silhouette is intact.

- [ ] **Step 3: Default cube regression.**

1. File → New → General.
2. Enable only Remove Interior (Ray Cast) in the AI Optimizer panel.
3. Run the standalone Remove Interior operator.
4. **Expected:** no faces removed (the cube has 6 exterior faces, no interior). Pipeline report reads `Removed 0 interior faces` (or equivalent zero-result text).

- [ ] **Step 4: Known-interior mesh regression.**

1. File → New → General. Delete default cube. Add → Mesh → Cube. Scale to 2m. While it's selected, Add → Mesh → Cube again (creates a second 2m cube at the cursor, overlapping the first visually but a separate mesh object). Move the second cube to be INSIDE the first by scaling it to 0.5m and centering. Select both and Ctrl-J to join into one mesh object. Enter Edit Mode, select two vertices (one from the inner cube, one from the outer cube) and press F to bridge — this makes the inner cube faces no longer a separated loose part, so Enclosed Parts can't find them.
2. Exit Edit Mode. Select the joined mesh.
3. Enable only Remove Interior (Ray Cast). Run.
4. **Expected:** the 6 inner-cube faces are removed (or most of them — even if 1-2 survive due to the bridge, the bulk should go). Total face reduction ≥ 4.

- [ ] **Step 5: Report status.**

Report `DONE`, `DONE_WITH_CONCERNS` (noting any visible remaining tears), or `BLOCKED`. Do not commit.

---

## Final Wrap-Up

After Task 1.3 passes, the fix is complete. Hand back to the user for commit + tag.
