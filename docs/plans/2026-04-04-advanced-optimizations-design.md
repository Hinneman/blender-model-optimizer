# Advanced Optimizations Design

**Date:** 2026-04-04
**Target:** General purpose (web viewers + real-time apps)
**Model sources:** Meshy, Tripo, Rodin (typical AI mesh generators)

## Overview

Eight new optimizations integrated into the existing pipeline. Grouped with existing steps where logical, only 2 entirely new steps added (Symmetry Mirror, LOD Generation). All new features default off or extend existing behavior non-destructively.

## Pipeline Order

| Order | Step | Status | Changes |
|-------|------|--------|---------|
| 1 | Clean & Prepare Geometry | Renamed/Extended | + material merging, + mesh join |
| 2 | Remove Interior | Unchanged | — |
| 3 | Symmetry Mirror | **New** | Detect symmetry, delete half, add mirror modifier |
| 4 | Decimate | Extended | + optional normal map baking |
| 5 | Clean Images | Unchanged | — |
| 6 | Clean Unused | Unchanged | — |
| 7 | Optimize Textures | Renamed/Extended | + UV repacking (experimental) |
| 8 | LOD Generation | **New** | Export multiple LOD levels |
| 9 | Export GLB | Extended | + Draco quantization tuning, + vertex color baking |

### Estimated Impact (typical 50k face / 8 material / 4x 2048px texture / ~15MB model)

- Faces: 50,000 → ~3,000-5,000
- File size: ~15MB → ~0.5-2MB
- Draw calls: ~10-50 → 1-3

## Step Details

### Step 1 — Clean & Prepare Geometry (extended from Fix Geometry)

Rename from "Fix Geometry" to "Clean & Prepare Geometry".

**Existing settings (unchanged):**
- `merge_distance` — Float, default 0.0001
- `recalculate_normals` — Bool, default True
- `fix_manifold` — Bool, default True

**New settings:**

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `merge_materials` | Bool | True | Merge materials with identical shader setups |
| `merge_materials_threshold` | Float | 0.01 | Color/value tolerance when comparing material properties |
| `join_meshes` | Bool | True | Join separate mesh objects that share materials |
| `join_mode` | Enum | `BY_MATERIAL` | `BY_MATERIAL` = group by shared material, `ALL` = join everything |

**Material merging approach:** Compare each material's node tree — if two materials use the same textures (by image data, not name) and same shader values within threshold, remap all users to one and delete the duplicate. Runs before mesh join so there are fewer unique materials to group by.

**Mesh join approach:** After material merge, group objects by shared material and join each group. Reduces draw calls without breaking material assignments.

**Execution order within step:** merge doubles → fix normals → manifold fix → merge materials → join meshes → delete loose

### Step 2 — Remove Interior (unchanged)

No changes.

### Step 3 — Symmetry Mirror (new)

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `run_symmetry` | Bool | False | Detect and apply mirror optimization |
| `symmetry_axis` | Enum | `X` | `X`, `Y`, or `Z` |
| `symmetry_threshold` | Float | 0.001 | Max distance between vertex and its mirrored counterpart |
| `symmetry_min_score` | Float | 0.85 | Minimum % of vertices that must match to apply mirror |

**Approach:**
1. For each mesh, find the center of mass
2. For each vertex on the positive side of the chosen axis, look for a matching vertex on the negative side (within threshold)
3. Calculate a symmetry score (% of vertices matched)
4. If score >= `symmetry_min_score`: delete the negative-side half, add a Mirror modifier, apply immediately

**Default off.** Not all AI models are symmetric. Incorrect detection would destroy geometry. Users opt in when they know their model is symmetric (characters, vehicles).

**Pipeline position rationale:**
- After Remove Interior: interior removal may delete enclosed parts that confuse the center-of-mass calculation
- Before Decimate: decimation distorts vertex positions, making symmetry detection less reliable

### Step 4 — Decimate (extended)

**Existing settings (unchanged):**
- `dissolve_angle` — Float, default 0.0872665
- `decimate_ratio` — Float, default 0.1

**New settings:**

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `bake_normal_map` | Bool | False | Bake high-poly detail into a normal map before decimating |
| `normal_map_resolution` | Enum | `1024` | `512`, `1024`, `2048` |
| `normal_map_cage_extrusion` | Float | 0.01 | Ray distance for baking |

**Approach:**
1. Duplicate the mesh (high-poly copy)
2. Run normal decimate (dissolve + collapse) on the original
3. Bake normals from the high-poly copy onto the decimated mesh
4. Assign the baked normal map to the material's normal input
5. Delete the high-poly copy

**Default off.** Adds a texture (100-500KB), requires UV unwrap on the decimated mesh. Most valuable when decimating aggressively (ratio < 0.2).

### Steps 5 & 6 — Clean Images & Clean Unused (unchanged)

No changes.

### Step 7 — Optimize Textures (renamed from Resize Textures)

**Existing settings (unchanged):**
- `max_texture_size` — Int, default 1024
- `resize_mode` — Enum, default DOWNSIZE

**New settings:**

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `repack_uvs` | Bool | False | Repack UV islands for tighter layout before resizing |
| `repack_margin` | Float | 0.005 | Margin between UV islands after repacking |

**Approach:** For each mesh, enter edit mode, select all, run `bpy.ops.uv.pack_islands(margin=margin)`. Then resize as before.

**Default off. Marked as "Advanced / Experimental" in UI.** UV repacking changes layout, which breaks alignment with existing baked textures. Only safe when textures are procedural or user will re-bake. Future enhancement: texture re-baking after repack.

### Step 8 — LOD Generation (new)

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `run_lod` | Bool | False | Generate multiple LOD levels as separate GLB files |
| `lod_levels` | Int | 3 | Number of LOD levels (including full detail) |
| `lod_suffix_pattern` | String | `_LOD{n}` | Filename suffix, `{n}` replaced with 0, 1, 2... |
| `lod_ratios` | String | `1.0, 0.5, 0.25` | Comma-separated decimate ratios per level |

**Approach:**
1. Runs after all optimizations, before main Export step
2. LOD0 = already-optimized mesh (exported by normal Export step)
3. For each additional LOD level: duplicate scene state → apply additional collapse decimate at level ratio → export as `{filename}_LOD1.glb` → undo to restore scene
4. All LODs share the same (already optimized) textures

**Default off.** Most simple web viewers (model-viewer) don't use LODs. Useful for three.js / Babylon.js scenes.

### Step 9 — Export GLB (extended)

**Existing settings (unchanged):**
- `output_filename`, `output_folder`, `export_selected_only`
- `use_draco`, `draco_level`
- `image_format`, `image_quality`

**New settings:**

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `draco_position_quantization` | Int | 14 | Position quantization bits (8-16) |
| `draco_normal_quantization` | Int | 10 | Normal quantization bits (8-16) |
| `draco_texcoord_quantization` | Int | 12 | UV coordinate quantization bits (8-16) |
| `bake_vertex_colors` | Bool | False | Bake textures into vertex colors, remove texture images |
| `vertex_color_resolution` | Int | 1 | Samples per vertex for baking (1 = nearest) |

**Draco quantization:** Currently hardcoded at 14/10/12/10 in `export_glb_all`. Exposing as properties. Current values become defaults — no behavior change unless user tweaks.

**Vertex color baking approach:**
1. For each mesh, bake diffuse texture into a vertex color layer
2. Remove texture image references from materials
3. Rewire material to use vertex color output instead of image texture
4. Export — eliminates texture payload entirely

**Default off. Marked as "Advanced / Experimental".** Vertex colors are low-fidelity (one color per vertex). Only acceptable on dense meshes or stylized/flat-shaded models. Destructive to material setup.

## Implementation Priority

Ordered by impact/complexity ratio:

| Priority | Feature | Complexity | Impact | Batch |
|----------|---------|-----------|--------|-------|
| P1 | Material Merging (Step 1) | Low | High | 1 |
| P2 | Mesh Join (Step 1) | Low | High | 1 |
| P3 | Draco Quantization Exposure (Step 9) | Low | Low-Med | 1 |
| P4 | LOD Generation (Step 8) | Medium | Medium | 2 |
| P5 | UV Repacking (Step 7) | Medium | Medium | 2 |
| P6 | Normal Map Baking (Step 4) | High | Medium | 3 |
| P7 | Symmetry/Mirror (Step 3) | High | Medium | 3 |
| P8 | Vertex Color Baking (Step 9) | Medium | Low | 3 |

**Batch 1 (Quick wins):** Material merge, mesh join, Draco exposure
**Batch 2 (Medium effort):** LOD generation, UV repacking
**Batch 3 (Complex/experimental):** Normal baking, symmetry mirror, vertex colors
