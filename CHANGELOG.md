# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.6.2] - 2026-04-18

### Changed

- **Pipeline cancellation is now responsive mid-step** — Clicking Cancel (or pressing ESC) during a running pipeline now takes effect inside long Python loops (interior ray-cast, symmetry detection, small-pieces, image comparison), not only between steps. A hint under the Cancel button explains that an in-flight Blender op (decimate apply, export, bake) will still finish first.

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
