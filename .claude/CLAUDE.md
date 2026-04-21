# CLAUDE.md

## Project Overview

Blender add-on ("AI 3D Model Optimizer") that optimizes AI-generated 3D models for web/real-time use. Multi-file Python package targeting Blender 4.0+, with a build step that produces a single installable `.py`.

## Structure

- `src/__init__.py` — bl_info, register/unregister
- `src/operators.py` — All AIOPT_OT_* operator classes
- `src/panels.py` — All AIOPT_PT_* panel classes
- `src/properties.py` — Property groups (AIOPT_Properties, AIOPT_PipelineState)
- `src/geometry.py` — Geometry fixing, decimation, interior removal, symmetry
- `src/textures.py` — Image cleanup, resizing, fingerprinting, vertex color baking
- `src/materials.py` — Material merging, mesh joining
- `src/utils.py` — Shared helpers (logging, config, mesh selection, size estimation, export)
- `build.py` — Build script to produce single-file addon

## Key Concepts

- All code uses Blender's Python API (`bpy`)
- The add-on registers a sidebar panel in the 3D Viewport under "AI Optimizer"
- Pipeline steps: Fix Geometry → Decimate → Clean Images → Clean Unused → Resize Textures → Export GLB
- Optional dependency: 3D Print Toolbox (for manifold fixes); falls back to manual hole-filling if unavailable
- Settings are persisted as JSON via `bpy.utils.user_resource('CONFIG')`
- Blender naming conventions: classes prefixed with `AIOPT_`, operators use `OT_`, panels use `PT_`

## Build

- Run `python build.py` to produce `build/model-optimizer-addon.py`
- The build reads version from `pyproject.toml` and injects it into `bl_info`
- Install the built file in Blender: Edit → Preferences → Add-ons → Install from Disk

## Releasing

- Update version in `pyproject.toml`
- Commit and tag: `git tag v1.5.0 && git push --tags`
- GitHub Action builds and creates a release with the addon `.py` attached

## Development

- No external dependencies beyond Blender's bundled Python
- **Linting**: `ruff check src/` — config in `pyproject.toml`
- **Formatting**: `ruff format src/`
- See [BEST_PRACTICES.md](BEST_PRACTICES.md) for coding conventions and guidelines

## Working with Claude

- **Never commit.** The user commits all changes themselves. Claude (and any dispatched subagents) must stage/edit files but never run `git commit`.
- **No worktrees.** Work directly in the main checkout on a feature branch. Do not create git worktrees.
- **Update `CHANGELOG.md` when a feature is complete.** Before reporting a feature as done, add an entry under a new version heading matching the version in `pyproject.toml` (format: `## [X.Y.Z] - YYYY-MM-DD`), using Keep a Changelog sections (`### Added` / `### Changed` / `### Fixed` / `### Removed`). If `pyproject.toml` still holds the last released version, bump the patch or minor number as appropriate and update both files in the same change. Bug fixes alone don't need a version bump if one is already pending.
- **Keep `README.md` in sync with `CHANGELOG.md`.** Whenever you add or change a CHANGELOG entry, re-read `README.md` and update it in the same change if the entry affects anything user-visible — Features list, Settings tables, Requirements, Installation, or Usage. Purely internal fixes (refactors, test-only changes, build-script tweaks) don't need a README update. Doing this at changelog-write time avoids shipping a release where the README lags behind the shipped behavior.
