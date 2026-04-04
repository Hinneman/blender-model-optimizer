# AI 3D Model Optimizer — Blender Add-on

A Blender add-on that optimizes AI-generated 3D models for web and real-time use. Fixes geometry issues, reduces polygon count, cleans up textures, and exports compressed GLB files.

## Features

- **Fix Geometry** — Merge close vertices, recalculate normals, fix non-manifold geometry
- **Decimate** — Reduce polygon count using collapse decimation with configurable ratio
- **Clean Duplicate Images** — Pixel-content-based deduplication (safe for multi-import sessions)
- **Clean Unused Data** — Remove orphaned materials, textures, and meshes
- **Resize Textures** — Downsize or resize all textures to a maximum resolution
- **Export GLB** — Export with Draco mesh compression and configurable image format (WebP/JPEG/PNG)
- **Full Pipeline** — Run all steps in one click with toggleable stages
- **Pipeline Progress** — Live progress panel showing per-step status, sub-step progress, timing, and overall completion
- **Cancellable Pipeline** — Cancel mid-pipeline with ESC or a Cancel button; all changes are automatically undone
- **Presets** — Save, load, and reset default settings across sessions

## Requirements

- Blender 4.0 or newer
- Optional: [3D Print Toolbox](https://docs.blender.org/manual/en/latest/addons/mesh/3d_print_toolbox.html) add-on for improved manifold fixes

## Installation

1. Download `model-optimizer-addon.py` from the [latest release](../../releases/latest)
2. Open Blender
3. Go to **Edit → Preferences → Add-ons**
4. Click **Install from Disk** (Blender 4.2+) or **Install...** (older versions)
5. Select the downloaded `.py` file
6. Enable the add-on by checking the box next to "AI 3D Model Optimizer"

## Usage

1. Open the sidebar in the 3D Viewport by pressing **N**
2. Click the **AI Optimizer** tab
3. Adjust settings in the sub-panels (Geometry Fix, Decimate, Textures, Export)
4. Click **Run Full Pipeline** to run all enabled steps, or use individual step buttons
5. While the pipeline runs, a **Pipeline Progress** panel shows each step's status and timing
6. Press **ESC** or click **Cancel Pipeline** to abort — all changes will be rolled back
7. After completion, click **Dismiss** to close the results panel

## Settings

| Setting | Default | Description |
|---|---|---|
| Merge Distance | 0.0001 | Threshold for merging close vertices |
| Decimate Ratio | 0.1 | Keep 10% of faces (lower = more reduction) |
| Max Texture Size | 1024 px | Maximum texture dimension |
| Resize Mode | Downsize Only | Only shrink oversized textures |
| Output Folder | *(blank)* | Output folder (blank = same as .blend file) |
| Filename | optimized_model.glb | Output filename |
| Selected Only | Off | Export only selected objects |
| Draco Compression | On | Enable Draco mesh compression in GLB |
| Draco Level | 6 | Compression level (0–10) |
| Image Format | WebP | Texture format in exported GLB |
| Image Quality | 85 | JPEG/WebP quality (1–100) |

## License

This project is licensed under the [MIT License](LICENSE).
