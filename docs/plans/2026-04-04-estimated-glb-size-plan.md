# Estimated GLB File Size Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Display an estimated GLB export file size in the Model Stats box, computed from a component-based heuristic.

**Architecture:** A single helper function `estimate_glb_size()` computes the estimate from mesh data and add-on properties. The main panel's `draw()` method calls it and displays the result. No new operators, properties, or classes needed.

**Tech Stack:** Blender Python API (`bpy`), existing add-on properties.

---

### Task 1: Add the estimation helper function

**Files:**
- Modify: `src/model-optimizer-addon.py` (helper functions section, after `is_print3d_available` ~line 96)

**Step 1: Write `estimate_glb_size` function**

Add after the `is_print3d_available` function (around line 96):

```python
def estimate_glb_size(meshes, props):
    """Estimate GLB export file size in bytes based on scene data and settings."""
    # -- Geometry --
    geo_bytes = 0
    for obj in meshes:
        mesh = obj.data
        verts = len(mesh.vertices)
        faces = len(mesh.polygons)
        # position (12B) + normal (12B) + UV (8B) per vertex + index (12B per tri)
        geo_bytes += verts * 32 + faces * 3 * 4

    if props.use_draco:
        # Draco compression factor: ~2x at level 0, ~6x at level 10
        draco_factor = 2.0 + (props.draco_level / 10.0) * 4.0
        geo_bytes /= draco_factor

    # -- Textures --
    tex_bytes = 0
    images = [i for i in bpy.data.images if i.type == "IMAGE" and i.name not in ("Render Result", "Viewer Node")]
    for img in images:
        w, h = img.size[0], img.size[1]
        if w == 0 or h == 0:
            continue

        # Cap to max_texture_size if resize step is enabled
        if props.run_resize_textures:
            max_s = props.max_texture_size
            if props.resize_mode == "ALL":
                w, h = max_s, max_s
            else:  # DOWNSIZE
                if w > max_s or h > max_s:
                    scale = max_s / max(w, h)
                    w = int(w * scale)
                    h = int(h * scale)

        raw = w * h * 4  # RGBA
        fmt = props.image_format
        if fmt == "WEBP":
            ratio = 15.0 * (props.image_quality / 100.0)
            tex_bytes += raw / max(ratio, 1.0)
        elif fmt == "JPEG":
            ratio = 10.0 * (props.image_quality / 100.0)
            tex_bytes += raw / max(ratio, 1.0)
        else:  # NONE (PNG)
            tex_bytes += raw / 2.0

    # -- Overhead (GLB container, materials, scene graph) --
    overhead = 10 * 1024

    return geo_bytes + tex_bytes + overhead
```

**Step 2: Run linting**

Run: `ruff check src/`
Expected: No new errors

**Step 3: Commit**

```
feat: add estimate_glb_size helper function
```

---

### Task 2: Display the estimate in the Model Stats box

**Files:**
- Modify: `src/model-optimizer-addon.py:1438` (in `AIOPT_PT_main_panel.draw`, after the Materials label)

**Step 1: Add estimate display**

After line 1438 (`col.label(text=f"Materials: {total_materials}")`), add:

```python
            est_bytes = estimate_glb_size(meshes, props)
            if est_bytes >= 1024 * 1024:
                est_label = f"~{est_bytes / (1024 * 1024):.1f} MB"
            else:
                est_label = f"~{est_bytes / 1024:.0f} KB"
            col.label(text=f"Est. Export Size: {est_label}")
```

Note: `props` is not currently fetched this early in `draw()`. Add `props = context.scene.ai_optimizer` before the stats box (before or after the `meshes` check). Check if `props` is already available — it may be fetched later in the method.

**Step 2: Run linting**

Run: `ruff check src/`
Expected: No new errors

**Step 3: Run formatting**

Run: `ruff format src/`

**Step 4: Commit**

```
feat: display estimated GLB export size in Model Stats
```
