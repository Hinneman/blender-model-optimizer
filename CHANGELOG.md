# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.8.0] - 2026-04-19

### Fixed

- **Thin-shell meshes (draped covers, cloth, single-layer surfaces) no longer render with black "holes" after decimation.** The post-decimate cleanup in `decimate_single` was calling `normals_make_consistent(inside=False)`, which flood-fills face winding from a seed face. On thin-shell geometry the concept of "outside" is undefined, so the algorithm flipped whole face islands inside-out; those islands then rendered as back-faces (black under the GLB exporter's default material). The call was added in 1.7.0 as a defensive cleanup, but COLLAPSE decimation preserves input winding, so there was nothing for it to fix. Removed. Existing per-object `Fix Geometry` normal recalculation (gated behind the `Recalculate Normals` toggle) is unchanged and still runs before decimation on whole input where the flood-fill has a much better chance of seeding correctly.
- **Black texture smears on fragmented-UV meshes after decimation** are eliminated by defaulting `Manifold Fix` away from the 3D Print Toolbox (see Changed). The smears were a downstream artifact of the Toolbox's `clean_non_manifold` operator tearing UV islands apart on thin-shell meshes; with the default changed to `Fill Holes`, they no longer appear.
- **Multi-pass decimate at aggressive ratios no longer overshoots the face-count estimate.** Running 3 passes at ratio 0.05 previously produced ~76k faces against a UI estimate of ~57k because the `dissolve_limited` pre-pass created n-gons that the COLLAPSE modifier re-triangulated, growing the face count on pass 1. The new flow triangulates up front, runs the planar DISSOLVE as a pre-pass (unweighted, so it can collapse flat regions that the seam-protection bias would otherwise freeze), then runs COLLAPSE with `use_collapse_triangulate=False`. The per-pass ratio is computed from the post-pre-pass face count so the planar reduction doesn't compound with the requested ratio. Multi-pass COLLAPSE no longer stalls after pass 1 on fragmented-UV meshes because seam-protected vertices are now strongly biased (weight 0.5) rather than hard-immune (which weight 1.0 effectively produced in Blender's solver).

### Added

- `Passes` setting on the Decimate step (1–5, default 1). Splits COLLAPSE decimation into N iterations so the quadric solver recomputes its error field between passes. Per-pass ratio is computed *after* the planar pre-pass to solve for the reduction needed to hit `start_faces * ratio`, so the planar dissolve and the requested ratio don't compound. For the same final face count, higher pass counts preserve silhouette and texture detail noticeably better at aggressive ratios (e.g. 3 passes reaching 0.1 produces a much smoother result than a single pass at 0.1). Default of 1 keeps existing behavior unchanged.
- `Protect UV Seams` toggle on the Decimate step, **default on**. A temporary `AIOPT_Seam_Protect` vertex group weights UV island boundary vertices (and their one-ring neighbors) at 0.5 and all other vertices at 0.1; each COLLAPSE decimate modifier references the group with `invert_vertex_group=True`, biasing the quadric solver ~5x against collapsing seam-adjacent vertices. Mesh topology stays fully connected — this is a numerical cost bias, not a hard constraint. Weight 0.5 (rather than 1.0) is deliberate: weight 1.0 causes Blender's solver to treat the vertex as uncollapsible, stalling multi-pass COLLAPSE on fragmented-UV meshes where most vertices end up protected. The group is removed after decimation so the exported mesh stays clean.
- `Planar Pre-Pass` toggle on the Decimate step, **default on**, with a `Planar Angle` slider (default 5 deg). Runs a DECIMATE modifier in `DISSOLVE` (planar) mode *before* the COLLAPSE loop, merging adjacent near-coplanar faces into n-gons. Dramatically reduces triangle count in flat regions — cylinder tops, flat panels, ground planes — without touching curved surfaces. UV island boundaries are preserved natively via the modifier's `delimit={'UV'}` setting. Running DISSOLVE before COLLAPSE is load-bearing: it's unweighted, so it eats flat regions that the seam-protection bias would otherwise lock COLLAPSE out of. Disable if your mesh has subtle curvature that should not be flattened.

### Changed

- Decimate step now runs a planar pre-pass by default (see above). On AI-generated meshes with large flat regions this produces noticeably lower final face counts and eliminates the radial fan artifact; on meshes with subtle curvature you may want to disable `Planar Pre-Pass` to preserve it.
- `Fix Manifold` boolean replaced with `Manifold Fix` enum (`Off` / `Fill Holes` / `3D Print Toolbox`, default `Fill Holes`). The 3D Print Toolbox's `clean_non_manifold` operator deletes geometry around non-manifold edges to produce a watertight solid, which damages thin-shell AI meshes (draped covers, cloth, single-layer surfaces) — every boundary edge on a thin shell is non-manifold by design, so the operator eats ring after ring of faces. Toolbox is now an explicit opt-in rather than the automatic preference. Saved configs that had `Fix Manifold` on migrate to `Fill Holes` (not `3D Print Toolbox`); users who want the Toolbox can re-select it.
- `Protect UV Seams` now defaults on. The earlier "off" default was based on a misdiagnosis (seam protection was blamed for texture damage that actually came from the 3D Print Toolbox). Saved configs that had it off are force-migrated to on.
- Dependency status label for the 3D Print Toolbox removed from the main panel. The plugin is now named only inside the Manifold Fix radio row — users who don't care about it see it simply as a disabled option.
- Decimate step now triangulates the input up front and runs COLLAPSE with `use_collapse_triangulate=False`. The previous flow applied `dissolve_limited` first (which produced n-gons) and then let each COLLAPSE pass re-triangulate, inflating the face count on pass 1 and making multi-pass at aggressive ratios overshoot the UI estimate (see Fixed). The `Dissolve Angle` setting is removed; the `Planar Pre-Pass` (governed by `Planar Angle`) covers flat-region merging.

## [1.7.1] - 2026-04-18

### Fixed

- 3D Print Toolbox availability is now detected via `addon_utils.check()` instead of `hasattr(bpy.ops.mesh, ...)`. The previous check always returned True because `bpy.ops` uses dynamic attribute lookup, so the sidebar could show "available" while the fix-geometry step silently fell back to manual hole-filling.
- **Remove Interior (Ray Cast) no longer tears the exterior shell.** The raycast sampler now covers a wider ~55° cone with 13 rays instead of a narrow ~6° cone with 5 rays. Exterior faces in concave regions (fuselage spine, canopy fairing on AI aircraft meshes) previously had all 5 narrow-cone rays land on the opposite interior wall and got deleted as false positives. The wider cone lets at least one ray escape to open space, correctly classifying such faces as exterior.

## [1.7.0] - 2026-04-18

### Added

- `Auto Cage Distance` option for the normal-map bake: ray distance is automatically set to 1% of the mesh bounding-box max dimension. Works correctly regardless of model scale. Disable to fall back to the manual `Cage Extrusion (mm)` field.
- `Fix Geometry` now includes a degenerate-dissolve pre-pass (threshold 1e-6) that removes zero-area faces and zero-length edges common in AI-generated meshes before the existing merge-by-distance step.
- New pipeline step `Floor Snap`: translates the selected meshes so the lowest world-space vertex sits at Z=0, leaving XY unchanged. Useful for AI exports that arrive centered on origin. Runs between Decimate and Clean Images. Available as a standalone operator (`ai_optimizer.floor_snap`) and as a toggleable pipeline step, default ON.

### Changed

- Dependency status ("3D Print Toolbox installed / not installed") is now always visible at the top of the panel, not only when a mesh is selected.
- Decimate step now protects UV seams by marking island boundaries as Sharp before the DECIMATE modifier runs. Prevents texture smearing on AI meshes whose UV layout would otherwise be destroyed by edge collapse. Meshes without UVs are unaffected.

### Fixed

- Removed a leftover `progress_update(0)` call in `log()` that was a silent no-op (no matching `progress_begin`).
- Symmetry Mirror step now snaps the object origin onto the detected symmetry plane before applying the Mirror modifier. Prevents gaps or overlaps at the seam when the source AI mesh's origin is not already on the symmetry axis.

## [1.6.2] - 2026-04-18

### Changed

- **Pipeline cancellation is now responsive mid-step** — Clicking Cancel (or pressing ESC) during a running pipeline now takes effect inside long Python loops (interior ray-cast, symmetry detection, small-pieces, image comparison), not only between steps. A hint under the Cancel button explains that an in-flight Blender op (decimate apply, export, bake) will still finish first.

### Fixed

- **EEVEE crash on weak iGPUs during pipeline** — Viewport shading is now temporarily switched from `RENDERED`/`MATERIAL` to `SOLID` for the duration of the pipeline and restored afterwards. EEVEE's constant material re-sync against mutating meshes could dereference freed image/material pointers on Intel integrated graphics and crash Blender mid-decimate.
- **Depsgraph crash on cancel rollback** — The undo loop that rolls back pipeline changes is now deferred to a one-shot app timer instead of running inside the modal callback. Calling `bpy.ops.ed.undo()` while the modal operator was still on Blender's call stack caused `DepsgraphNodeBuilder::build_materials` to null-deref.
- **Cancel rollback no longer walks into pre-pipeline work** — Cancel now snapshots the operator-stack length at pipeline start and undoes only back to that baseline, instead of a fixed step count that could step past our snapshot into the user's prior edits.

## [1.6.1] - 2026-04-14

### Fixed

- **Normal map baking** — Baking silently failed because highpoly copies were hidden, preventing Blender from selecting them. The bake now works correctly.
- **Post-decimate cleanup** — Decimation could leave degenerate geometry (duplicate vertices, flipped normals, loose edges). A cleanup pass now runs automatically after the collapse modifier is applied.
- **Bake error reporting** — Normal map bake failures now log the actual error message to the system console instead of failing silently.

## [1.6.0] - 2026-04-13

### Added

- **Mesh Analysis** — Analyze mesh problems (non-manifold edges, zero/thin faces) and get optimization recommendations for decimate ratio and merge distance
- **Remove Small Pieces** — Delete disconnected mesh islands below a face count or size threshold, targeting floating debris in AI-generated models
- **Pipeline Summary** — Results panel now shows a summary box with total face reduction, export file size, and elapsed time at a glance

### Changed

- **Human-friendly units** — All settings now use intuitive units: millimeters for distances, centimeters for sizes, percent for tolerances (instead of raw meters and decimal fractions)
- **Improved size estimate** — Export size estimate is now much more accurate with realistic Draco and image compression ratios
- **Better pipeline report** — Step details no longer truncate in the sidebar; each detail displays on its own line. Clean Images, Clean Unused, and Export steps now show their results in the report.

## [1.5.1] - 2026-04-13

### Fixed

- Textures were not exported when PNG was selected as the image format

### Documentation

- README updated to document Merge Materials, Join Meshes, Remove Interior, Symmetry Mirror, Bake Normal Map, and LOD Generation features

## [1.5.0] - 2026-04-04

### Added

- **Merge Materials** — Merge materials with identical shader setups to reduce draw calls
- **Join Meshes** — Join mesh objects sharing the same material (by material, or all into one)
- **Remove Interior** — Remove hidden interior geometry using Loose Parts or Ray Cast method
- **Symmetry Mirror** *(Experimental)* — Detect near-symmetric meshes and apply mirror optimization
- **Bake Normal Map** — Bake high-poly surface detail into a normal map before decimating (requires Cycles)
- **LOD Generation** — Export multiple LOD levels as separate GLB files with configurable ratios
- **Pipeline Progress** — Live progress panel with per-step status, sub-step progress, timing, and overall completion
- **Cancellable Pipeline** — Cancel mid-pipeline with ESC or Cancel button; all changes are automatically undone
- **Presets** — Save, load, and delete named settings presets with a default preset
- File size estimate displayed in the export settings panel
- Multi-file package structure with a build pipeline (`build.py`)

### Changed

- Improved pipeline resilience and updated default settings

### Removed

- Vertex color baking
