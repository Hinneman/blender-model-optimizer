# Estimated GLB File Size Display

## Summary

Add an estimated GLB export file size to the Model Stats box in the main panel, calculated via a component-based heuristic and labeled as an estimate.

## Display

- Location: Model Stats box, below existing stats (Objects, Faces, Vertices, Images, Materials)
- Format: `Est. Export Size: ~X.X MB`
- Updates reactively on every panel redraw (settings changes immediately reflected)

## Estimation Formula

### 1. Geometry

For each mesh object:

- Base size = vertices × 32 bytes (position 12B + normal 12B + UV 8B)
- Index data = faces × 3 × 4 bytes (triangle indices as uint32)
- If Draco enabled: divide total by compression factor (~4× at default level, scaled with `draco_level`)
- If Draco disabled: use raw size

### 2. Textures

For each image in use (type == "IMAGE", excluding Render Result/Viewer Node):

- Raw pixels = width × height × 4 (RGBA)
- If resize step enabled: cap dimensions at `max_texture_size` before calculating
- Apply format compression ratio:
  - JPEG: ÷10
  - WebP: ÷15
  - PNG: ÷2
  - AUTO: ÷10
- Scale with `image_quality` for JPEG/WebP (higher quality = larger)

### 3. Overhead

Fixed 10 KB for GLB container, scene graph, and material definitions.

### 4. Total

`total = geometry + textures + overhead`

## Implementation

- No new operators or properties needed
- Pure calculation in `AIOPT_PT_main_panel.draw()` (or extracted to a helper function)
- Reads existing add-on properties (Draco, image format, quality, max texture size, resize enabled)
