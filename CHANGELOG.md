# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.8.0] - 2026-04-19

### Added

- `Passes` setting on the Decimate step (1–5, default 1). Splits decimation into N iterations targeting the same final ratio, with per-pass ratio computed as `ratio ** (1/passes)` — so 3 passes at final ratio 0.1 collapses at ~0.464 per pass and still lands at ~10% of the original face count. The dissolve pre-pass and seam split run once up front; only the COLLAPSE modifier runs per iteration, so the quadric solver recomputes its error field between passes. For the same final face count, higher pass counts preserve silhouette and texture detail noticeably better at aggressive ratios (e.g. 3 passes reaching 0.1 produces a much smoother result than a single pass at 0.1). Default of 1 keeps existing behavior unchanged.
- `Planar Post-Pass` toggle on the Decimate step, **default on**, with a `Planar Angle` slider (default 5 deg). Runs a second DECIMATE modifier in `DISSOLVE` (planar) mode after the collapse pass, merging adjacent near-coplanar faces into n-gons. Dramatically reduces triangle count in flat regions — cylinder tops, flat panels, ground planes — without touching curved surfaces. UV island boundaries are preserved natively via the modifier's `delimit={'UV'}` setting. Fixes the radial fan artifact where the COLLAPSE solver left dozens of triangles meeting at a central vertex on flat discs. Disable if your mesh has subtle curvature that should not be flattened.
- **Topological UV seam constraint in the Decimate step.** When the mesh has UV layers, edges on UV island boundaries are now physically split before decimation so the COLLAPSE solver cannot collapse across them, then re-welded with a scoped merge-by-distance after the collapse passes. Replaces the 1.7.0 soft-hint seam protection (Sharp edges), which the collapse solver could still violate at aggressive ratios, causing texture smearing where rims bled into lids and neighboring UV islands. Always on when UVs are present; no configuration. Tradeoff: on meshes with highly fragmented UVs the final face count may exceed the target ratio because boundary edges become uncollapsible — the `Planar Post-Pass` default partially compensates by flattening coplanar regions.

### Changed

- Decimate step now runs a planar post-pass by default (see above). On AI-generated meshes with large flat regions this produces noticeably lower final face counts and eliminates the radial fan artifact; on meshes with subtle curvature you may want to disable `Planar Post-Pass` to preserve it.

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
