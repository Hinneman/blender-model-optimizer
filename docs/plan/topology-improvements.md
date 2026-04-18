Model optimizer improvements

## 1. Core Architectural Standards
* **BMesh over `bpy.ops`:** Operators should prioritize `bmesh` for geometry manipulation. `bpy.ops` is context-dependent and forces viewport refreshes, which slows down optimization on high-poly AI meshes.
* **Dependency Injection:** If the plugin relies on the *3D Print Toolbox*, the script must check for its presence and offer a graceful fallback or a "Enable Dependency" button in the UI.
* **Undo Grouping:** Wrap the "Run All" pipeline in a single undo push so users don't have to `Ctrl+Z` through 15 individual cleanup steps.
* **Progressive UI:** Use the `WindowManager.progress_begin()` during heavy tasks (like baking or decimation) to prevent the "Application Not Responding" state.

---

## 2. Feature Specification: The "AI-Proof" Pipeline

### Phase A: Geometric Hardening (`fix_geometry_single`)
The goal is to move from "standard cleanup" to "manifold-guaranteed" cleanup.
1.  **Degenerate Dissolve:** Run a pre-pass to dissolve zero-area faces and zero-length edges (threshold: $0.000001$).
2.  **Voxel-Wrap (Optional Toggle):** If the mesh is "soup" (thousands of disconnected parts), use a Voxel Remesh at high resolution to create a single shell, then re-project the surface.
3.  **Boundary Pinning:** Before fixing holes, identify UV boundaries. Ensure `fill_holes` does not create geometry that crosses these boundaries, which would break the texture map.

### Phase B: Texture-Aware Decimation
Standard decimation is the #1 cause of "broken" AI models.
1.  **UV Seam Protection:** Automatically mark all UV seams as "Sharp" or "Pinned" before decimating to prevent the modifier from collapsing the edges that define the texture layout.
2.  **Attribute Transfer:** If the model uses Vertex Colors, the script must automatically create a UV map and bake those colors to a texture *before* decimation, as decimation destroys vertex color fidelity.
3.  **Normal Map Baking:**
    * Implement **Auto-Ray Distance:** Calculate the bounding box of the mesh and set the bake ray distance to $1\%$ of the max dimension.
    * **Cage Generation:** Automatically create a temporary "Cage" object (a slightly inflated version of the low-poly) to ensure $100\%$ bake coverage.

### Phase C: Scene & Metadata Cleanup
1.  **PBR Standardization:** AI exports often have "unlinked" nodes or non-standard naming. The optimizer should rename textures to `{Object_Name}_Diff`, `{Object_Name}_Norm`, etc.
2.  **Origin Correction:** Calculate the "lowest point" of the mesh and snap it to the floor ($Z=0$) and world center ($X=0, Y=0$).

---

## 3. Recommended Operator Additions (The "Gaps")

| Operator | Implementation Logic |
| :--- | :--- |
| `AIOPT_OT_check_dependencies` | Scans for *3D Print Toolbox* and *KTX2* compressors; reports status in the sidebar. |
| `AIOPT_OT_atlas_textures` | If a model has 5 materials (common in Meshy), combine them into a single 0-1 UV sheet. |
| `AIOPT_OT_remove_internal` | Uses "Select Side: Inside" or ray-casting to delete faces that are $100\%$ occluded from the exterior. |
| `AIOPT_OT_apply_draco` | Integrates Google Draco compression into the GLB export for web-ready sizes. |

---

## 4. Technical Snippet for the Agent: Smart Hole Filling
This is a more robust logic for your fallback manifold repair:

```python
import bmesh

def smart_rebuild_mesh(obj):
    me = obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    
    # Remove hidden "ghost" geometry
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0001)
    
    # Find holes and fill only if they are small (prevents bridging across the model)
    # This targets the specific "crackling" seen in AI models
    for f in bm.faces:
        if not f.is_valid: continue
    
    # Calculate normals with high precision
    bm.normal_update()
    
    bm.to_mesh(me)
    bm.free()
```

---

## 5. UI/UX Best Practices
* **Tooltips:** Every button needs a tooltip explaining *why* it's needed (e.g., "Fixes black spots caused by overlapping triangles").
* **Visual Feedback:** After `AIOPT_OT_analyze_mesh`, highlight the "bad" areas in the viewport using a temporary Vertex Group or a red Material Overlay.