# AI 3D Model Optimizer — Blender Add-on

[![Latest Release](https://img.shields.io/github/v/release/Hinneman/blender-model-optimizer)](https://github.com/Hinneman/blender-model-optimizer/releases/latest)
[![Blender](https://img.shields.io/badge/Blender-4.2%2B-orange?logo=blender&logoColor=white)](https://www.blender.org/)
[![License](https://img.shields.io/github/license/Hinneman/blender-model-optimizer)](LICENSE)

A Blender add-on that optimizes AI-generated 3D models for web and real-time use. Fixes geometry issues, reduces polygon count, cleans up textures, and exports compressed GLB files.

## Features

- **Fix Geometry** — Merge close vertices, recalculate normals, fix non-manifold geometry
- **Merge Materials** — Merge materials with identical shader setups to reduce draw calls
- **Join Meshes** — Join mesh objects sharing materials (by material or all into one)
- **Remove Interior** — Remove hidden interior geometry (Loose Parts or Ray Cast method)
- **Remove Small Pieces** — Delete disconnected mesh islands below a face count or size threshold
- **Symmetry Mirror** *(Experimental)* — Detect near-symmetric meshes and apply mirror optimization
- **Decimate** — Reduce polygon count using collapse decimation with configurable ratio, optional multi-pass refinement, UV-seam protection, and a planar pre-pass that merges flat regions before collapse
- **Bake Normal Map** — Bake high-poly surface detail into a normal map before decimating (requires Cycles)
- **Clean Duplicate Images** — Pixel-content-based deduplication (safe for multi-import sessions)
- **Clean Unused Data** — Remove orphaned materials, textures, and meshes
- **Resize Textures** — Downsize or resize all textures to a maximum resolution
- **Export GLB** — Export with Draco mesh compression and configurable image format (WebP/JPEG/PNG)
- **LOD Generation** — Export multiple LOD levels as separate GLB files with configurable ratios
- **Mesh Analysis** — Analyze mesh problems and get optimization recommendations for decimate ratio and merge distance
- **Full Pipeline** — Run all enabled steps in one click with toggleable stages
- **Pipeline Progress** — Live progress panel showing per-step status, sub-step progress, timing, and overall completion
- **Pipeline Summary** — Results panel shows total face reduction, export file size, and elapsed time at a glance
- **Cancellable Pipeline** — Cancel mid-pipeline with ESC or a Cancel button; all changes are automatically undone
- **Presets** — Save, load, and reset default settings across sessions
- **Verbose Logging** *(opt-in)* — Detailed per-step diagnostics (settings consumed, checkpoints, elapsed time) with an **Open Debug Log** button to write the buffered log to a file and open it in your OS text editor

## Requirements

- Blender 4.2 or newer
- Optional: [3D Print Toolbox](https://docs.blender.org/manual/en/latest/addons/mesh/3d_print_toolbox.html)

## Installation

**Preferred (Blender 4.2+):**

1. In Blender, go to **Edit → Preferences → Get Extensions**
2. Search for **AI 3D Model Optimizer**
3. Click **Install**

**Sideload (from GitHub release):**

1. Download `ai_model_optimizer-X.Y.Z.zip` from the [latest release](https://github.com/Hinneman/blender-model-optimizer/releases/latest)
2. Drag-and-drop the `.zip` into Blender, **or** go to **Edit → Preferences → Add-ons → Install from Disk** and select the file
3. Enable the add-on by checking the box next to "AI 3D Model Optimizer"

## Usage

1. Open the sidebar in the 3D Viewport by pressing **N**
2. Click the **AI Optimizer** tab
3. *(Optional)* Click **Run Analysis** to inspect mesh problems and get recommended settings
4. Adjust settings in the sub-panels (Clean & Prepare Geometry, Remove Interior, Remove Small Pieces, Symmetry Mirror, Decimate, Textures, Export)
5. Click **Run Full Pipeline** to run all enabled steps, or use individual step buttons
6. While the pipeline runs, a **Pipeline Progress** panel shows each step's status and timing
7. After completion, a **summary box** shows total face reduction and export file size
8. Press **ESC** or click **Cancel Pipeline** to abort — all changes will be rolled back

## Settings

### Geometry

| Setting | Default | Description |
|---|---|---|
| Merge Distance | 0.1 mm | Threshold for merging close vertices |
| Recalculate Normals | On | Fix flipped normals |
| Manifold Fix | Fill Holes | `Off` / `Fill Holes` (n-gon hole filling, safe on thin shells) / `3D Print Toolbox` (aggressive cleanup for watertight solids, requires the plugin — destroys thin-shell meshes) |
| Merge Materials | On | Merge materials with identical shader setups |
| Material Tolerance | 1% | Color/value tolerance when comparing material properties |
| Join Meshes | On | Join mesh objects that share materials |
| Join Mode | By Material | Group by shared material, or join all into one mesh |

### Remove Interior

| Setting | Default | Description |
|---|---|---|
| Method | Ray Cast | Loose Parts (fast, disconnected geometry) or Ray Cast (slower, catches interior faces in connected meshes) |

### Remove Small Pieces

| Setting | Default | Description |
|---|---|---|
| Min Faces | 50 | Delete loose parts with fewer than this many faces |
| Min Size | 1.0 cm | Delete loose parts smaller than this cube edge length |

### Mesh Analysis

| Setting | Default | Description |
|---|---|---|
| Target | Web | Target platform preset: Mobile (~5K faces), Web (~25K), Desktop (~75K), or Custom |
| Target Faces | 25,000 | Custom target face count (when Target = Custom) |

### Symmetry Mirror *(Experimental)*

| Setting | Default | Description |
|---|---|---|
| Axis | X | Axis to test symmetry along |
| Threshold | 1.0 mm | Max distance between a vertex and its mirror to count as matched |
| Min Score | 0.85 | Minimum fraction of vertices that must have a mirror match |

### Decimate

| Setting | Default | Description |
|---|---|---|
| Decimate Ratio | 0.1 | Keep ~10% of the original face count |
| Passes | 1 | Split COLLAPSE into N passes targeting the same final ratio. Higher values preserve silhouette and texture detail at aggressive ratios |
| Protect UV Seams | On | Bias COLLAPSE ~5× against collapsing vertices near UV island boundaries. Prevents texture smearing on fragmented-UV meshes |
| Planar Pre-Pass | On | Before COLLAPSE, merge near-coplanar faces into n-gons (UV-island-preserving). Frees COLLAPSE's budget for curved geometry |
| Planar Angle | 5° | Max angle between adjacent faces for the planar pre-pass to merge them. 10–15° reduces more faces but may flatten subtle curvature |
| Bake Normal Map | On | Bake high-poly detail into a normal map before decimating |
| Normal Map Size | 1024 px | Resolution of the baked normal map |
| Auto Cage Distance | On | Set bake ray distance to 1% of the mesh bounding-box diagonal (scale-independent) |
| Cage Extrusion | 10 mm | Manual bake ray distance (used when Auto Cage Distance is off) |

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
| Draco Level | 6 | Compression level (0-10, higher = smaller file) |
| Position Bits | 14 | Draco position quantization bits (lower = smaller, less precision) |
| Normal Bits | 10 | Draco normal quantization bits |
| UV Bits | 12 | Draco UV quantization bits |
| Image Format | WebP | Texture format in exported GLB (WebP / JPEG / PNG) |
| Image Quality | 85 | JPEG/WebP quality (1-100) |
| LOD Generation | Off | Generate multiple LOD levels as separate GLB files |
| LOD Levels | 3 | Number of LOD levels (including full-detail LOD0) |
| LOD Suffix Pattern | _LOD{n} | Filename suffix pattern ({n} = LOD level number) |
| LOD Ratios | 1.0, 0.5, 0.25 | Comma-separated decimate ratios per LOD level |

## License

This project is licensed under the [GNU General Public License v3.0 or later](LICENSE).
