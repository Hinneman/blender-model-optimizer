# AI 3D Model Optimizer — Blender Add-on

A Blender add-on that optimizes AI-generated 3D models for web and real-time use. Fixes geometry issues, reduces polygon count, cleans up textures, and exports compressed GLB files.

## Features

- **Fix Geometry** — Merge close vertices, recalculate normals, fix non-manifold geometry
- **Merge Materials** — Merge materials with identical shader setups to reduce draw calls
- **Join Meshes** — Join mesh objects sharing materials (by material or all into one)
- **Remove Interior** — Remove hidden interior geometry (Loose Parts or Ray Cast method)
- **Symmetry Mirror** *(Experimental)* — Detect near-symmetric meshes and apply mirror optimization
- **Decimate** — Reduce polygon count using collapse decimation with configurable ratio
- **Bake Normal Map** — Bake high-poly surface detail into a normal map before decimating (requires Cycles)
- **Clean Duplicate Images** — Pixel-content-based deduplication (safe for multi-import sessions)
- **Clean Unused Data** — Remove orphaned materials, textures, and meshes
- **Resize Textures** — Downsize or resize all textures to a maximum resolution
- **Export GLB** — Export with Draco mesh compression and configurable image format (WebP/JPEG/PNG)
- **LOD Generation** — Export multiple LOD levels as separate GLB files with configurable ratios
- **Full Pipeline** — Run all enabled steps in one click with toggleable stages
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
3. Adjust settings in the sub-panels (Clean & Prepare Geometry, Remove Interior, Symmetry Mirror, Decimate, Textures, Export)
4. Click **Run Full Pipeline** to run all enabled steps, or use individual step buttons
5. While the pipeline runs, a **Pipeline Progress** panel shows each step's status and timing
6. Press **ESC** or click **Cancel Pipeline** to abort — all changes will be rolled back
7. After completion, click **Dismiss** to close the results panel

## Settings

### Geometry

| Setting | Default | Description |
|---|---|---|
| Merge Distance | 0.0001 | Threshold for merging close vertices |
| Recalculate Normals | On | Fix flipped normals |
| Fix Manifold | On | Attempt to fix non-manifold geometry (holes, open edges) |
| Merge Materials | On | Merge materials with identical shader setups |
| Material Threshold | 0.01 | Color/value tolerance when comparing material properties |
| Join Meshes | On | Join mesh objects that share materials |
| Join Mode | By Material | Group by shared material, or join all into one mesh |

### Remove Interior

| Setting | Default | Description |
|---|---|---|
| Method | Ray Cast | Loose Parts (fast, disconnected geometry) or Ray Cast (slower, catches interior faces in connected meshes) |

### Symmetry Mirror *(Experimental)*

| Setting | Default | Description |
|---|---|---|
| Axis | X | Axis to test symmetry along |
| Threshold | 0.001 | Max distance between a vertex and its mirror to count as matched |
| Min Score | 0.85 | Minimum fraction of vertices that must have a mirror match |

### Decimate

| Setting | Default | Description |
|---|---|---|
| Dissolve Angle | 15° | Dissolve faces within this angle before decimation (0 = skip) |
| Decimate Ratio | 0.1 | Keep 10% of faces after dissolve |
| Bake Normal Map | On | Bake high-poly detail into a normal map before decimating |
| Normal Map Size | 1024 px | Resolution of the baked normal map |
| Cage Extrusion | 0.01 | Ray distance for baking from high-poly to low-poly surface |

### Textures

| Setting | Default | Description |
|---|---|---|
| Max Texture Size | 1024 px | Maximum texture dimension in pixels |
| Resize Mode | Downsize Only | Only shrink oversized textures, or resize all to exactly max size |

### Export

| Setting | Default | Description |
|---|---|---|
| Output Folder | ~/Downloads | Output folder |
| Filename | optimized_model.glb | Output filename |
| Selected Only | On | Export only selected objects |
| Draco Compression | On | Enable Draco mesh compression (recommended for web) |
| Draco Level | 6 | Compression level (0–10, higher = smaller file) |
| Position Bits | 14 | Draco position quantization bits (lower = smaller, less precision) |
| Normal Bits | 10 | Draco normal quantization bits |
| UV Bits | 12 | Draco UV quantization bits |
| Image Format | WebP | Texture format in exported GLB (WebP / JPEG / PNG) |
| Image Quality | 85 | JPEG/WebP quality (1–100) |
| LOD Generation | Off | Generate multiple LOD levels as separate GLB files |
| LOD Levels | 3 | Number of LOD levels (including full-detail LOD0) |
| LOD Suffix Pattern | _LOD{n} | Filename suffix pattern ({n} = LOD level number) |
| LOD Ratios | 1.0, 0.5, 0.25 | Comma-separated decimate ratios per LOD level |

## License

This project is licensed under the [MIT License](LICENSE).
