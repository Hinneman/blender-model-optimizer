# CLAUDE.md

## Project Overview

Blender add-on ("AI 3D Model Optimizer") that optimizes AI-generated 3D models for web/real-time use. Single-file Python add-on targeting Blender 4.0+.

## Structure

- `src/model-optimizer-addon.py` — The entire add-on (operators, UI panels, properties, registration)

## Key Concepts

- All code uses Blender's Python API (`bpy`)
- The add-on registers a sidebar panel in the 3D Viewport under "AI Optimizer"
- Pipeline steps: Fix Geometry → Decimate → Clean Images → Clean Unused → Resize Textures → Export GLB
- Optional dependency: 3D Print Toolbox (for manifold fixes); falls back to manual hole-filling if unavailable
- Settings are persisted as JSON via `bpy.utils.user_resource('CONFIG')`
- Blender naming conventions: classes prefixed with `AIOPT_`, operators use `OT_`, panels use `PT_`

## Development

- No build step — the `.py` file is installed directly into Blender
- No external dependencies beyond Blender's bundled Python
- Test by installing in Blender: Edit → Preferences → Add-ons → Install from Disk
- **Linting**: `ruff check src/` — config in `pyproject.toml`
- **Formatting**: `ruff format src/`
- See [BEST_PRACTICES.md](BEST_PRACTICES.md) for coding conventions and guidelines
