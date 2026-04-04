# Multi-file Addon Split with Build & Release Pipeline

**Date:** 2026-04-04

## Goal

Split the single-file addon (`src/model-optimizer-addon.py`) into a multi-file Python package for better code organization, while preserving single-file distribution via a build script. Add a GitHub Actions release pipeline with tag-based versioning.

## Source Structure

```
src/
├── __init__.py        # bl_info, register/unregister, imports from submodules
├── operators.py       # All AIOPT_OT_* classes
├── panels.py          # All AIOPT_PT_* classes
├── properties.py      # AIOPT_Properties, AIOPT_PipelineState
├── geometry.py        # fix_geometry_single, remove_interior_*, symmetry, bbox helpers
├── textures.py        # resize, clean_images, bake_vertex_colors, fingerprinting
├── materials.py       # merge_duplicate_materials, _get_material_signature, join_meshes
├── utils.py           # log, get_selected_meshes, count_faces, get_config_path,
                       #   is_print3d_available, estimate_glb_size, save/load_defaults
```

## Build Script (`build.py`)

A standalone Python script at the project root. No external dependencies.

- Reads each source module in dependency order
- Collects and deduplicates all imports (stdlib, bpy, mathutils)
- Reads version from `pyproject.toml` (single source of truth)
- Emits `build/model-optimizer-addon.py` containing:
  1. Original docstring header (install/usage instructions)
  2. `bl_info` dict with version injected from `pyproject.toml`
  3. Merged, deduplicated imports
  4. All function and class bodies in correct dependency order
  5. `register()` / `unregister()` and file-load handler at the bottom
- `build/` directory is gitignored

## Version Management

- **Single source of truth:** `pyproject.toml` `version` field (e.g. `"1.5.0"`)
- `build.py` parses it and injects into `bl_info["version"]` as a tuple (e.g. `(1, 5, 0)`)
- `src/__init__.py` keeps a placeholder `bl_info["version"]` for development
- Release workflow: update version in `pyproject.toml`, commit, tag `v1.5.0`, push

## GitHub Actions (`.github/workflows/release.yml`)

Triggered on tag push matching `v*`:

1. Checkout repository
2. Run `python build.py`
3. Validate that the tag version matches `pyproject.toml` version
4. Create a GitHub Release with:
   - Auto-generated release notes from commits since previous tag
   - Built `model-optimizer-addon.py` attached as a release asset

## Development Workflow Changes

- Linting: `ruff check src/` and `ruff format src/` work unchanged (now multiple files)
- Local testing: run `python build.py`, install `build/model-optimizer-addon.py` in Blender
- The original single `src/model-optimizer-addon.py` is removed after the split
