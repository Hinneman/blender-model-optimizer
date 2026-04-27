# CLAUDE.md

## Project Overview

Blender add-on ("AI 3D Model Optimizer") that optimizes AI-generated 3D models for web/real-time use. Multi-file Python package targeting Blender 4.2+, distributed as a Blender extension `.zip` on [extensions.blender.org](https://extensions.blender.org).

## Structure

- `blender_model_optimizer/__init__.py` — register/unregister
- `blender_model_optimizer/operators.py` — All AIOPT_OT_* operator classes
- `blender_model_optimizer/panels.py` — All AIOPT_PT_* panel classes
- `blender_model_optimizer/properties.py` — Property groups (AIOPT_Properties, AIOPT_PipelineState)
- `blender_model_optimizer/geometry.py` — Geometry fixing, decimation, interior removal, symmetry
- `blender_model_optimizer/textures.py` — Image cleanup, resizing, fingerprinting, vertex color baking
- `blender_model_optimizer/materials.py` — Material merging, mesh joining
- `blender_model_optimizer/utils.py` — Shared helpers (logging, config, mesh selection, size estimation, export)
- `blender_manifest.toml` — Extension metadata (id, version, permissions, etc.)
- `build.py` — Build script to produce the extension `.zip`

## Key Concepts

- All code uses Blender's Python API (`bpy`)
- The add-on registers a sidebar panel in the 3D Viewport under "AI Optimizer"
- Pipeline steps: Fix Geometry → Decimate → Clean Images → Clean Unused → Resize Textures → Export GLB
- Optional dependency: 3D Print Toolbox (for manifold fixes); falls back to manual hole-filling if unavailable
- Settings are persisted as JSON via `bpy.utils.user_resource('CONFIG')`
- Blender naming conventions: classes prefixed with `AIOPT_`, operators use `OT_`, panels use `PT_`

## Build

- Run `python build.py` to produce `build/blender_model_optimizer-<version>.zip`
- The build reads version from `pyproject.toml` and injects it into `blender_manifest.toml` inside the zip
- Sideload in Blender: drag-and-drop the zip, or Edit → Preferences → Add-ons → Install from Disk
- Before tagging a release, run `scripts/validate.ps1` (Windows) or `scripts/validate.sh` (POSIX) to run `blender --command extension validate` against the built zip

## Releasing

- Update version in `pyproject.toml` and add a matching `CHANGELOG.md` entry
- Commit and tag: `git tag vX.Y.Z && git push --tags`
- GitHub Action builds and creates a release with the extension `.zip` attached
- Manually upload the zip to extensions.blender.org — see [RELEASING.md](../RELEASING.md)

## Development

- No external dependencies beyond Blender's bundled Python
- **Linting**: `ruff check blender_model_optimizer/` — config in `pyproject.toml`
- **Formatting**: `ruff format blender_model_optimizer/`
- See [BEST_PRACTICES.md](BEST_PRACTICES.md) for coding conventions and guidelines

## Working with Claude

- **Never commit.** The user commits all changes themselves. Claude (and any dispatched subagents) must stage/edit files but never run `git commit`.
- **No worktrees.** Work directly in the main checkout on a feature branch. Do not create git worktrees.
- **Update `CHANGELOG.md` when a feature is complete.** Before reporting a feature as done, add an entry under a new version heading matching the version in `pyproject.toml` (format: `## [X.Y.Z] - YYYY-MM-DD`), using Keep a Changelog sections (`### Added` / `### Changed` / `### Fixed` / `### Removed`). If `pyproject.toml` still holds the last released version, bump the patch or minor number as appropriate and update both files in the same change. Bug fixes alone don't need a version bump if one is already pending.
- **Keep `README.md` in sync with `CHANGELOG.md`.** Whenever you add or change a CHANGELOG entry, re-read `README.md` and update it in the same change if the entry affects anything user-visible — Features list, Settings tables, Requirements, Installation, or Usage. Purely internal fixes (refactors, test-only changes, build-script tweaks) don't need a README update. Doing this at changelog-write time avoids shipping a release where the README lags behind the shipped behavior.
