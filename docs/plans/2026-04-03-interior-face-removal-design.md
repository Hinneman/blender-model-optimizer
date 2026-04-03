# Interior Face Removal — Design

## Overview

New pipeline step that detects and removes geometry hidden inside a model's outer shell. AI-generated models frequently contain disconnected interior parts or occluded faces that waste polygon budget.

## Pipeline Placement

Fix Geometry → **Remove Interior** → Decimate → Clean Images → Clean Unused → Resize Textures → Export GLB

## Settings

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `run_remove_interior` | BoolProperty | True | Pipeline toggle |
| `interior_method` | EnumProperty | `LOOSE_PARTS` | Detection method |

### Detection Methods

**LOOSE_PARTS — Enclosed Parts (default)**
Separate mesh into loose parts, check if each part's bounding box is fully contained inside another part's bounds. Delete enclosed parts. Fast, best for AI-generated models.

**RAY_CAST — Ray Cast**
For each face, cast rays outward along its normal. If all rays hit back-faces of the same object, the face is interior. Delete it. Slower but catches interior faces within connected geometry.

## Implementation

### Helper function: `remove_interior_single(context, obj, props)`

- Called per mesh object
- Dispatches to the appropriate method based on `props.interior_method`
- Returns count of removed faces

### LOOSE_PARTS method

1. Enter edit mode, separate by loose parts (`bpy.ops.mesh.separate(type='LOOSE')`)
2. Back to object mode — now we have multiple objects
3. For each pair, check if one's bounding box is fully inside another's
4. Delete the enclosed objects
5. Join remaining parts back into one object

### RAY_CAST method

1. Use `obj.ray_cast()` from each face center along its outward normal
2. If the ray hits another face of the same object (and hits a back-face), the face is likely interior
3. Cast multiple rays (e.g. normal + jittered directions) to reduce false positives
4. Delete faces flagged as interior
5. Clean up loose vertices/edges

### Operator: `AIOPT_OT_remove_interior`

- bl_idname: `ai_optimizer.remove_interior`
- Standalone button in the UI panel
- Reports removed face/part count

### Modal pipeline integration

- New step tuple: `("Remove Interior", _setup_remove_interior, _tick_remove_interior, _teardown_remove_interior)`
- Iterates over mesh objects like the existing decimate step

## UI Panel: `AIOPT_PT_remove_interior_panel`

- Parent: `AIOPT_PT_main_panel`
- Collapsible (`DEFAULT_CLOSED`)
- Contents:
  - Method enum dropdown
  - Dynamic help label that changes based on selected method:
    - Enclosed Parts: "Removes disconnected mesh parts fully inside other geometry. Fast, best for AI-generated models."
    - Ray Cast: "Casts rays from each face to detect occlusion. Slower but catches interior faces within connected geometry."
  - Operator button with `MESH_DATA` icon

## Pipeline toggle UI

Add `run_remove_interior` toggle to the main panel's "Steps to include" row, between Geometry and Decimate.
