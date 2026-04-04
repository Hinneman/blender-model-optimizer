# Advanced Optimizations Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add 8 new optimizations to the AI 3D Model Optimizer Blender addon, grouped into existing and new pipeline steps.

**Architecture:** Single-file addon (`src/model-optimizer-addon.py`). Each optimization adds: a helper function, properties on `AIOPT_Properties`, UI elements on the relevant panel, pipeline integration in `AIOPT_OT_run_all`, and an update to `SAVEABLE_PROPS`/`estimate_glb_size`. No external dependencies.

**Tech Stack:** Python 3.11+, Blender 4.0+ API (`bpy`, `bmesh`, `mathutils`)

**Validation:** `ruff check src/` and `ruff format --check src/` must pass after each task. Manual testing in Blender for functional verification.

---

## Batch 1: Quick Wins

### Task 1: Material Merging — Helper Function

**Files:**
- Modify: `src/model-optimizer-addon.py` (insert after `remove_interior_single` at ~line 398)

**Step 1: Write `materials_are_similar` comparison function**

Add after line 398, before `decimate_single`:

```python
def _get_material_signature(mat, threshold=0.01):
    """Build a hashable signature from a material's node tree.

    Compares texture images (by data pointer) and shader input values
    (rounded to *threshold*). Two materials with the same signature are
    visually identical and safe to merge.
    """
    if not mat.node_tree:
        return None

    sig_parts = []
    for node in mat.node_tree.nodes:
        if node.type == "TEX_IMAGE" and node.image:
            # Identify texture by pixel fingerprint (reuse existing helper)
            fp = get_image_fingerprint(node.image)
            sig_parts.append(("tex", node.name, fp))
        elif node.type == "BSDF_PRINCIPLED":
            for inp in node.inputs:
                if inp.type == "RGBA":
                    val = tuple(round(v / threshold) for v in inp.default_value)
                    sig_parts.append(("input", inp.name, val))
                elif inp.type == "VALUE":
                    val = round(inp.default_value / threshold)
                    sig_parts.append(("input", inp.name, val))
    return tuple(sorted(sig_parts))


def merge_duplicate_materials(context, threshold=0.01):
    """Merge materials with identical shader setups.

    Returns ``(merged_count, detail_string)``.
    """
    sig_map = {}  # signature → keeper material
    merge_count = 0

    for mat in list(bpy.data.materials):
        sig = _get_material_signature(mat, threshold)
        if sig is None:
            continue
        if sig in sig_map:
            keeper = sig_map[sig]
            print(f"  [AI Optimizer] Merging material '{mat.name}' → '{keeper.name}'")
            mat.user_remap(keeper)
            bpy.data.materials.remove(mat)
            merge_count += 1
        else:
            sig_map[sig] = mat

    return (merge_count, f"Merged {merge_count} duplicate material(s)")
```

**Step 2: Lint**

Run: `ruff check src/ && ruff format --check src/`
Expected: PASS (0 errors)

---

### Task 2: Mesh Join — Helper Function

**Files:**
- Modify: `src/model-optimizer-addon.py` (insert after `merge_duplicate_materials`)

**Step 1: Write `join_meshes_by_material` function**

```python
def join_meshes_by_material(context, meshes, mode="BY_MATERIAL"):
    """Join mesh objects to reduce draw calls.

    *mode* is ``"BY_MATERIAL"`` (group objects sharing materials, join each
    group) or ``"ALL"`` (join everything into one object).

    Returns ``(resulting_objects, detail_string)``.
    """
    if len(meshes) <= 1:
        return (meshes, "Only 1 object, nothing to join")

    if mode == "ALL":
        bpy.ops.object.select_all(action="DESELECT")
        for obj in meshes:
            obj.select_set(True)
        context.view_layer.objects.active = meshes[0]
        bpy.ops.object.join()
        result = [context.view_layer.objects.active]
        return (result, f"Joined {len(meshes)} objects into 1")

    # BY_MATERIAL: group objects by their material set
    groups = {}
    for obj in meshes:
        mat_key = frozenset(slot.material.name for slot in obj.material_slots if slot.material)
        if not mat_key:
            mat_key = frozenset(["__no_material__"])
        groups.setdefault(mat_key, []).append(obj)

    joined_count = 0
    result_objects = []
    for _key, group in groups.items():
        if len(group) <= 1:
            result_objects.extend(group)
            continue
        bpy.ops.object.select_all(action="DESELECT")
        for obj in group:
            obj.select_set(True)
        context.view_layer.objects.active = group[0]
        bpy.ops.object.join()
        result_objects.append(context.view_layer.objects.active)
        joined_count += len(group)

    before = len(meshes)
    after = len(result_objects)
    return (result_objects, f"Joined {before} objects into {after} (by material)")
```

**Step 2: Lint**

Run: `ruff check src/ && ruff format --check src/`

---

### Task 3: Material Merging & Mesh Join — Properties, UI, Pipeline

**Files:**
- Modify: `src/model-optimizer-addon.py`
  - `AIOPT_Properties` class (~line 1338): add 4 new properties
  - `SAVEABLE_PROPS` list (~line 653): add property names
  - `AIOPT_PT_geometry_panel.draw` (~line 1680): add UI controls
  - `AIOPT_OT_fix_geometry.execute` (~line 720): call new functions
  - `AIOPT_OT_run_all` pipeline setup (~line 896): integrate into fix_geometry step
  - `fix_geometry_single` or add new per-object wrappers as needed

**Step 1: Add properties to `AIOPT_Properties`** (after `fix_manifold` at ~line 1362)

```python
    # -- Material & Mesh cleanup --
    merge_materials: BoolProperty(
        name="Merge Materials",
        default=True,
        description="Merge materials with identical shader setups (same textures and values)",
    )
    merge_materials_threshold: FloatProperty(
        name="Material Threshold",
        default=0.01,
        min=0.001,
        max=0.1,
        precision=3,
        description="Color/value tolerance when comparing material properties",
    )
    join_meshes: BoolProperty(
        name="Join Meshes",
        default=True,
        description="Join separate mesh objects that share materials to reduce draw calls",
    )
    join_mode: EnumProperty(
        name="Join Mode",
        items=[
            ("BY_MATERIAL", "By Material", "Group objects by shared material, join each group"),
            ("ALL", "All", "Join all objects into a single mesh"),
        ],
        default="BY_MATERIAL",
        description="How to group objects when joining",
    )
```

**Step 2: Add to `SAVEABLE_PROPS`** (~line 653)

Add these strings to the list:
```python
    "merge_materials",
    "merge_materials_threshold",
    "join_meshes",
    "join_mode",
```

**Step 3: Update `AIOPT_PT_geometry_panel.draw`** (~line 1680)

After the existing manifold section and before the operator button, add:

```python
        layout.separator()
        col = layout.column(align=True)
        col.label(text="Cleanup:", icon="MATERIAL")
        col.prop(props, "merge_materials")
        if props.merge_materials:
            col.prop(props, "merge_materials_threshold")
        col.prop(props, "join_meshes")
        if props.join_meshes:
            col.prop(props, "join_mode", text="")
```

**Step 4: Update `AIOPT_OT_fix_geometry.execute`** (~line 720)

After the existing per-object loop and before `self.report`, add material merge and mesh join calls:

```python
        # Material merge (operates on all materials, not per-object)
        mat_merged = 0
        if props.merge_materials:
            mat_merged, _detail = merge_duplicate_materials(context, props.merge_materials_threshold)

        # Mesh join
        join_detail = ""
        if props.join_meshes:
            meshes = get_selected_meshes()  # refresh after geometry fixes
            _result, join_detail = join_meshes_by_material(context, meshes, props.join_mode)
```

Update the report message to include the new info.

**Step 5: Update pipeline step** — extend `_teardown_fix_geometry` and add material merge + join to the fix geometry pipeline tick sequence.

The simplest approach: after the per-object geometry fix loop completes (all sub-items processed), run material merge and mesh join in the teardown. Modify `_teardown_fix_geometry`:

```python
    def _teardown_fix_geometry(self, context):
        props = context.scene.ai_optimizer
        count = self._fix_count
        method = self._fix_method
        total = len(self._sub_items)
        detail = f"{count}/{total} fixed"
        if method != "none":
            detail += f" — method: {method}"

        if props.merge_materials:
            mat_count, _d = merge_duplicate_materials(context, props.merge_materials_threshold)
            detail += f", {mat_count} materials merged"

        if props.join_meshes:
            meshes = get_selected_meshes()
            _result, join_d = join_meshes_by_material(context, meshes, props.join_mode)
            detail += f", {join_d}"

        return detail
```

**Step 6: Rename panel label** — Change `AIOPT_PT_geometry_panel.bl_label` from `"Geometry Fix"` to `"Clean & Prepare Geometry"`.

**Step 7: Lint**

Run: `ruff check src/ && ruff format --check src/`

---

### Task 4: Draco Quantization Exposure — Properties, UI, Export

**Files:**
- Modify: `src/model-optimizer-addon.py`
  - `AIOPT_Properties` (~line 1443): add 3 quantization properties
  - `SAVEABLE_PROPS` (~line 653): add property names
  - `AIOPT_PT_export_panel.draw` (~line 1852): add UI sliders
  - `export_glb_all` (~line 605): use property values instead of hardcoded
  - `estimate_glb_size` (~line 106): no change needed (Draco ratio is already a heuristic)

**Step 1: Add properties to `AIOPT_Properties`** (after `draco_level`)

```python
    draco_position_quantization: IntProperty(
        name="Position Bits",
        default=14,
        min=8,
        max=16,
        description="Draco position quantization bits. Lower = smaller file, less precision",
        update=_tag_3d_redraw,
    )
    draco_normal_quantization: IntProperty(
        name="Normal Bits",
        default=10,
        min=8,
        max=16,
        description="Draco normal quantization bits",
        update=_tag_3d_redraw,
    )
    draco_texcoord_quantization: IntProperty(
        name="UV Bits",
        default=12,
        min=8,
        max=16,
        description="Draco texture coordinate quantization bits",
        update=_tag_3d_redraw,
    )
```

**Step 2: Add to `SAVEABLE_PROPS`**

```python
    "draco_position_quantization",
    "draco_normal_quantization",
    "draco_texcoord_quantization",
```

**Step 3: Update `export_glb_all`** (~line 623-626)

Replace hardcoded quantization values:
```python
        "export_draco_position_quantization": props.draco_position_quantization,
        "export_draco_normal_quantization": props.draco_normal_quantization,
        "export_draco_texcoord_quantization": props.draco_texcoord_quantization,
        "export_draco_color_quantization": 10,
```

**Step 4: Update `AIOPT_PT_export_panel.draw`** (~line 1866)

After `col.prop(props, "draco_level", slider=True)` add:

```python
            col.separator()
            col.label(text="Quantization (advanced):")
            col.prop(props, "draco_position_quantization", slider=True)
            col.prop(props, "draco_normal_quantization", slider=True)
            col.prop(props, "draco_texcoord_quantization", slider=True)
```

**Step 5: Lint**

Run: `ruff check src/ && ruff format --check src/`

---

## Batch 2: Medium Effort

### Task 5: LOD Generation — Helper Function

**Files:**
- Modify: `src/model-optimizer-addon.py` (insert after `export_glb_all`)

**Step 1: Write `generate_lods` function**

```python
def generate_lods(context, props):
    """Generate multiple LOD levels as separate GLB files.

    LOD0 is the current (already optimized) state — exported by the normal
    export step.  This function exports LOD1, LOD2, etc. with progressively
    more aggressive decimation.

    Returns a detail string.
    """
    ratios_str = props.lod_ratios.strip()
    try:
        ratios = [float(r.strip()) for r in ratios_str.split(",")]
    except ValueError:
        return "Invalid LOD ratios string"

    if len(ratios) < 2:
        return "Need at least 2 LOD levels"

    # Determine base output path
    if props.output_folder:
        output_dir = props.output_folder
    elif bpy.data.filepath:
        output_dir = os.path.dirname(bpy.data.filepath)
    else:
        output_dir = os.path.expanduser("~")

    base_name = os.path.splitext(props.output_filename)[0]
    suffix_pattern = props.lod_suffix_pattern

    exported = []
    # Skip LOD0 (ratio index 0) — that's the main export
    for i, ratio in enumerate(ratios):
        if i == 0:
            continue
        if ratio >= 1.0:
            continue

        # Push undo so we can revert decimation
        bpy.ops.ed.undo_push(message=f"Before LOD{i}")

        # Decimate all meshes by additional ratio
        meshes = get_selected_meshes()
        for obj in meshes:
            bpy.ops.object.select_all(action="DESELECT")
            context.view_layer.objects.active = obj
            obj.select_set(True)
            mod = obj.modifiers.new(name=f"LOD{i}_Decimate", type="DECIMATE")
            mod.decimate_type = "COLLAPSE"
            mod.ratio = ratio
            mod.use_collapse_triangulate = True
            bpy.ops.object.modifier_apply(modifier=mod.name)

        # Build filename
        suffix = suffix_pattern.replace("{n}", str(i))
        lod_filename = f"{base_name}{suffix}.glb"
        lod_path = os.path.join(output_dir, lod_filename)

        # Export
        export_settings = {
            "filepath": lod_path,
            "export_format": "GLB",
            "use_selection": props.export_selected_only,
            "export_apply": True,
            "export_yup": True,
            "export_draco_mesh_compression_enable": props.use_draco,
            "export_draco_mesh_compression_level": props.draco_level,
            "export_draco_position_quantization": props.draco_position_quantization,
            "export_draco_normal_quantization": props.draco_normal_quantization,
            "export_draco_texcoord_quantization": props.draco_texcoord_quantization,
            "export_draco_color_quantization": 10,
            "export_image_format": props.image_format,
        }
        if props.image_format in ("JPEG", "WEBP"):
            export_settings["export_image_quality"] = props.image_quality

        try:
            bpy.ops.export_scene.gltf(**export_settings)
            if os.path.exists(lod_path):
                size_mb = os.path.getsize(lod_path) / (1024 * 1024)
                exported.append(f"LOD{i}: {lod_filename} ({size_mb:.2f} MB)")
        except (TypeError, RuntimeError) as e:
            exported.append(f"LOD{i}: export failed ({e})")

        # Undo the decimation to restore original state
        bpy.ops.ed.undo()

    return f"Generated {len(exported)} LOD(s): " + "; ".join(exported)
```

**Step 2: Lint**

Run: `ruff check src/ && ruff format --check src/`

---

### Task 6: LOD Generation — Properties, UI, Pipeline

**Files:**
- Modify: `src/model-optimizer-addon.py`
  - `AIOPT_Properties`: add 4 LOD properties
  - `SAVEABLE_PROPS`: add property names
  - `AIOPT_PT_export_panel.draw`: add LOD UI section
  - `AIOPT_OT_run_all.invoke`: add LOD step to pipeline
  - `classes` tuple: add new operator if needed (or integrate into export step)

**Step 1: Add properties to `AIOPT_Properties`** (after export settings, before the class ends)

```python
    # -- LOD settings --
    run_lod: BoolProperty(
        name="LOD Generation",
        default=False,
        description="Generate multiple LOD levels as separate GLB files",
    )
    lod_levels: IntProperty(
        name="LOD Levels",
        default=3,
        min=2,
        max=5,
        description="Number of LOD levels (including full detail as LOD0)",
    )
    lod_suffix_pattern: StringProperty(
        name="Suffix Pattern",
        default="_LOD{n}",
        description="Filename suffix pattern. {n} is replaced with the LOD level number",
    )
    lod_ratios: StringProperty(
        name="LOD Ratios",
        default="1.0, 0.5, 0.25",
        description="Comma-separated decimate ratios per LOD level (LOD0 should be 1.0)",
    )
```

**Step 2: Add to `SAVEABLE_PROPS`**

```python
    "run_lod",
    "lod_levels",
    "lod_suffix_pattern",
    "lod_ratios",
```

**Step 3: Add LOD UI** to `AIOPT_PT_export_panel.draw`, before the export operator button:

```python
        layout.separator()
        col = layout.column(align=True)
        col.label(text="LOD Generation:", icon="MOD_DECIM")
        col.prop(props, "run_lod")
        if props.run_lod:
            col.prop(props, "lod_levels")
            col.prop(props, "lod_suffix_pattern")
            col.prop(props, "lod_ratios")
```

**Step 4: Add LOD step to pipeline** in `AIOPT_OT_run_all.invoke`, between resize_textures and export:

```python
        if props.run_lod:
            self._steps.append(("LOD Generation", self._setup_lod, self._tick_lod, self._teardown_noop))
```

Add setup/tick methods to `AIOPT_OT_run_all`:

```python
    # -- LOD Generation --

    def _setup_lod(self, context):
        self._sub_items = [None]
        return 1

    def _tick_lod(self, context, index):
        props = context.scene.ai_optimizer
        detail = generate_lods(context, props)
        self._lod_detail = detail
        return detail

    def _teardown_lod(self, context):
        return getattr(self, "_lod_detail", "")
```

Note: update the teardown reference from `self._teardown_noop` to `self._teardown_lod`.

**Step 5: Lint**

Run: `ruff check src/ && ruff format --check src/`

---

### Task 7: UV Repacking — Helper Function, Properties, UI, Pipeline

**Files:**
- Modify: `src/model-optimizer-addon.py`

**Step 1: Write `repack_uvs_single` helper** (insert near `resize_texture_single`)

```python
def repack_uvs_single(context, obj, margin=0.005):
    """Repack UV islands on *obj* for a tighter layout.

    Returns True if UV islands were repacked.
    """
    if not obj.data.uv_layers:
        return False

    bpy.ops.object.select_all(action="DESELECT")
    context.view_layer.objects.active = obj
    obj.select_set(True)

    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.pack_islands(margin=margin)
    bpy.ops.object.mode_set(mode="OBJECT")
    return True
```

**Step 2: Add properties to `AIOPT_Properties`** (near texture settings)

```python
    repack_uvs: BoolProperty(
        name="Repack UVs (Experimental)",
        default=False,
        description="Repack UV islands for tighter layout. WARNING: breaks pre-baked texture alignment",
    )
    repack_margin: FloatProperty(
        name="UV Margin",
        default=0.005,
        min=0.0,
        max=0.05,
        precision=4,
        description="Margin between UV islands after repacking",
    )
```

**Step 3: Add to `SAVEABLE_PROPS`**

```python
    "repack_uvs",
    "repack_margin",
```

**Step 4: Add UI** to `AIOPT_PT_textures_panel.draw`, before the resize section:

```python
        layout.separator()
        col = layout.column(align=True)
        col.label(text="UV Repacking (Experimental):", icon="UV")
        col.prop(props, "repack_uvs")
        if props.repack_uvs:
            col.prop(props, "repack_margin")
            box = layout.box()
            warn_col = box.column(align=True)
            warn_col.scale_y = 0.8
            warn_col.label(text="Warning: breaks pre-baked textures.", icon="ERROR")
            warn_col.label(text="Only use with procedural textures")
            warn_col.label(text="or if you plan to re-bake.")
```

**Step 5: Integrate into resize textures pipeline step**

In `_setup_resize_textures`, before building the image resize list, run UV repack:

```python
    def _setup_resize_textures(self, context):
        props = context.scene.ai_optimizer

        # UV repack pass (before resize)
        if props.repack_uvs:
            meshes = get_selected_meshes()
            for obj in meshes:
                repack_uvs_single(context, obj, props.repack_margin)

        # ... existing image resize setup ...
```

Also integrate into `AIOPT_OT_resize_textures.execute` for standalone use.

**Step 6: Lint**

Run: `ruff check src/ && ruff format --check src/`

---

## Batch 3: Complex / Experimental

### Task 8: Normal Map Baking — Helper Function

**Files:**
- Modify: `src/model-optimizer-addon.py` (insert near `decimate_single`)

**Step 1: Write `bake_normal_map` helper**

```python
def bake_normal_map_for_decimate(context, obj, props):
    """Bake a normal map from the high-poly mesh onto the decimated version.

    Must be called AFTER decimation. Expects *obj* to be the decimated mesh.
    *_highpoly_copy* must be set on the caller as the pre-decimation duplicate.

    Returns the baked image or None on failure.
    """
    highpoly = getattr(context, "_aiopt_highpoly_copy", None)
    if highpoly is None:
        return None

    resolution = int(props.normal_map_resolution)

    # Ensure decimated mesh has a UV map
    if not obj.data.uv_layers:
        bpy.ops.object.select_all(action="DESELECT")
        context.view_layer.objects.active = obj
        obj.select_set(True)
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.smart_project(angle_limit=1.15192)
        bpy.ops.object.mode_set(mode="OBJECT")

    # Create target image
    img_name = f"{obj.name}_normal_map"
    img = bpy.data.images.new(img_name, resolution, resolution, alpha=False)
    img.colorspace_settings.name = "Non-Color"

    # Set up material with image node for baking target
    mat = obj.data.materials[0] if obj.data.materials else None
    if mat is None:
        mat = bpy.data.materials.new(name=f"{obj.name}_material")
        obj.data.materials.append(mat)
        mat.use_nodes = True

    tree = mat.node_tree
    img_node = tree.nodes.new("ShaderNodeTexImage")
    img_node.image = img
    img_node.select = True
    tree.nodes.active = img_node

    # Set up bake: select highpoly, active = lowpoly (decimated)
    bpy.ops.object.select_all(action="DESELECT")
    highpoly.select_set(True)
    obj.select_set(True)
    context.view_layer.objects.active = obj

    # Bake
    bpy.context.scene.render.engine = "CYCLES"
    try:
        bpy.ops.object.bake(
            type="NORMAL",
            use_selected_to_active=True,
            cage_extrusion=props.normal_map_cage_extrusion,
        )
    except RuntimeError:
        # Bake failed — clean up and return
        tree.nodes.remove(img_node)
        bpy.data.images.remove(img)
        return None

    # Wire up the normal map in the material
    normal_map_node = tree.nodes.new("ShaderNodeNormalMap")
    normal_map_node.space = "TANGENT"
    img_node.select = False

    # Connect image → normal map → BSDF normal input
    tree.links.new(img_node.outputs["Color"], normal_map_node.inputs["Color"])
    for node in tree.nodes:
        if node.type == "BSDF_PRINCIPLED":
            tree.links.new(normal_map_node.outputs["Normal"], node.inputs["Normal"])
            break

    # Pack the image so it's embedded in the file
    img.pack()

    return img
```

**Step 2: Lint**

Run: `ruff check src/ && ruff format --check src/`

---

### Task 9: Normal Map Baking — Properties, UI, Pipeline Integration

**Files:**
- Modify: `src/model-optimizer-addon.py`

**Step 1: Add properties** (after `decimate_ratio` in `AIOPT_Properties`)

```python
    bake_normal_map: BoolProperty(
        name="Bake Normal Map",
        default=False,
        description="Bake high-poly detail into a normal map before decimating. Requires Cycles",
    )
    normal_map_resolution: EnumProperty(
        name="Normal Map Size",
        items=[
            ("512", "512px", ""),
            ("1024", "1024px", ""),
            ("2048", "2048px", ""),
        ],
        default="1024",
        description="Resolution of the baked normal map",
    )
    normal_map_cage_extrusion: FloatProperty(
        name="Cage Extrusion",
        default=0.01,
        min=0.001,
        max=1.0,
        precision=3,
        description="Ray distance for baking from high-poly to low-poly surface",
    )
```

**Step 2: Add to `SAVEABLE_PROPS`**

```python
    "bake_normal_map",
    "normal_map_resolution",
    "normal_map_cage_extrusion",
```

**Step 3: Add UI** to `AIOPT_PT_decimate_panel.draw`, after the decimate ratio slider:

```python
        layout.separator()
        col = layout.column(align=True)
        col.label(text="Normal Map Baking:", icon="IMAGE_DATA")
        col.prop(props, "bake_normal_map")
        if props.bake_normal_map:
            col.prop(props, "normal_map_resolution", text="")
            col.prop(props, "normal_map_cage_extrusion")
            box = layout.box()
            warn_col = box.column(align=True)
            warn_col.scale_y = 0.8
            warn_col.label(text="Requires Cycles render engine.", icon="INFO")
            warn_col.label(text="Best with aggressive decimation (ratio < 0.2).")
```

**Step 4: Integrate into decimate pipeline step**

Modify `_setup_decimate` to duplicate meshes if normal baking is enabled:
```python
    def _setup_decimate(self, context):
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        self._sub_items = get_selected_meshes()
        self._faces_before = count_faces(self._sub_items)
        props = context.scene.ai_optimizer
        self._highpoly_copies = {}
        if props.bake_normal_map:
            for obj in self._sub_items:
                copy = obj.copy()
                copy.data = obj.data.copy()
                context.collection.objects.link(copy)
                copy.hide_set(True)
                self._highpoly_copies[obj.name] = copy
        return len(self._sub_items)
```

Modify `_teardown_decimate` to bake normal maps and clean up:
```python
    def _teardown_decimate(self, context):
        props = context.scene.ai_optimizer
        meshes = get_selected_meshes()
        faces_after = count_faces(meshes)
        reduction = (1 - faces_after / max(self._faces_before, 1)) * 100
        detail = f"{self._faces_before:,} → {faces_after:,} faces ({reduction:.1f}% reduction)"

        if props.bake_normal_map and self._highpoly_copies:
            baked = 0
            for obj in meshes:
                highpoly = self._highpoly_copies.get(obj.name)
                if highpoly:
                    context._aiopt_highpoly_copy = highpoly
                    result = bake_normal_map_for_decimate(context, obj, props)
                    if result:
                        baked += 1
            # Clean up highpoly copies
            for copy in self._highpoly_copies.values():
                bpy.data.objects.remove(copy, do_unlink=True)
            self._highpoly_copies = {}
            detail += f", {baked} normal map(s) baked"

        return detail
```

**Step 5: Lint**

Run: `ruff check src/ && ruff format --check src/`

---

### Task 10: Symmetry/Mirror Detection — Helper Function

**Files:**
- Modify: `src/model-optimizer-addon.py` (insert after `remove_interior_single`)

**Step 1: Write symmetry detection and mirror helper**

```python
def detect_and_apply_symmetry(context, obj, axis="X", threshold=0.001, min_score=0.85):
    """Detect near-symmetric geometry and apply a mirror optimization.

    Tests if the mesh is approximately symmetric along *axis*. If the
    symmetry score (fraction of vertices with a mirror match) is at least
    *min_score*, deletes the negative-side half and adds a Mirror modifier.

    Returns ``(applied: bool, score: float)``.
    """
    import bmesh

    axis_index = {"X": 0, "Y": 1, "Z": 2}[axis]

    bpy.ops.object.select_all(action="DESELECT")
    context.view_layer.objects.active = obj
    obj.select_set(True)

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()

    # Find center of mass along the symmetry axis
    if len(bm.verts) == 0:
        bm.free()
        return (False, 0.0)

    center = sum((v.co[axis_index] for v in bm.verts), 0.0) / len(bm.verts)

    # Build KD-tree for vertex lookup
    from mathutils.kdtree import KDTree

    kd = KDTree(len(bm.verts))
    for i, v in enumerate(bm.verts):
        kd.insert(v.co, i)
    kd.balance()

    # Check symmetry: for each vertex on the positive side, look for mirror
    positive_verts = [v for v in bm.verts if v.co[axis_index] >= center]
    matched = 0

    for v in positive_verts:
        mirrored_co = v.co.copy()
        mirrored_co[axis_index] = 2 * center - mirrored_co[axis_index]
        _co, _idx, dist = kd.find(mirrored_co)
        if dist <= threshold:
            matched += 1

    score = matched / max(len(positive_verts), 1)

    if score < min_score:
        bm.free()
        return (False, score)

    # Apply: delete negative side verts
    to_delete = [v for v in bm.verts if v.co[axis_index] < center - threshold]
    bmesh.ops.delete(bm, geom=to_delete, context="VERTS")

    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()

    # Add mirror modifier
    mod = obj.modifiers.new(name="Symmetry_Mirror", type="MIRROR")
    mod.use_axis[0] = axis_index == 0
    mod.use_axis[1] = axis_index == 1
    mod.use_axis[2] = axis_index == 2
    mod.use_clip = True
    mod.merge_threshold = threshold

    # Apply the modifier immediately
    bpy.ops.object.modifier_apply(modifier=mod.name)

    return (True, score)
```

**Step 2: Lint**

Run: `ruff check src/ && ruff format --check src/`

---

### Task 11: Symmetry/Mirror — Properties, UI, Pipeline, Operator

**Files:**
- Modify: `src/model-optimizer-addon.py`

**Step 1: Add properties** (after `interior_method` in `AIOPT_Properties`)

```python
    # -- Symmetry settings --
    run_symmetry: BoolProperty(
        name="Symmetry Mirror",
        default=False,
        description="Detect near-symmetric meshes and apply mirror optimization",
    )
    symmetry_axis: EnumProperty(
        name="Axis",
        items=[
            ("X", "X", "Mirror along X axis"),
            ("Y", "Y", "Mirror along Y axis"),
            ("Z", "Z", "Mirror along Z axis"),
        ],
        default="X",
        description="Axis to test symmetry along",
    )
    symmetry_threshold: FloatProperty(
        name="Threshold",
        default=0.001,
        min=0.0001,
        max=0.1,
        precision=4,
        description="Max distance between a vertex and its mirror to count as matched",
    )
    symmetry_min_score: FloatProperty(
        name="Min Score",
        default=0.85,
        min=0.5,
        max=1.0,
        step=1,
        precision=2,
        description="Minimum fraction of vertices that must have a mirror match",
        subtype="FACTOR",
    )
```

**Step 2: Add to `SAVEABLE_PROPS`**

```python
    "run_symmetry",
    "symmetry_axis",
    "symmetry_threshold",
    "symmetry_min_score",
```

**Step 3: Add standalone operator** `AIOPT_OT_symmetry_mirror`

```python
class AIOPT_OT_symmetry_mirror(Operator):
    bl_idname = "ai_optimizer.symmetry_mirror"
    bl_label = "Symmetry Mirror"
    bl_description = "Detect symmetry and apply mirror optimization"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.ai_optimizer
        meshes = get_selected_meshes()

        if not meshes:
            self.report({"ERROR"}, "No mesh objects found")
            return {"CANCELLED"}

        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        applied = 0
        for obj in meshes:
            was_applied, score = detect_and_apply_symmetry(
                context, obj, props.symmetry_axis, props.symmetry_threshold, props.symmetry_min_score
            )
            if was_applied:
                applied += 1
            else:
                self.report({"INFO"}, f"{obj.name}: symmetry score {score:.0%} (below {props.symmetry_min_score:.0%})")

        self.report({"INFO"}, f"Applied mirror to {applied}/{len(meshes)} object(s)")
        return {"FINISHED"}
```

**Step 4: Add UI panel** `AIOPT_PT_symmetry_panel` (between remove_interior and decimate panels)

```python
class AIOPT_PT_symmetry_panel(Panel):
    bl_label = "Symmetry Mirror"
    bl_idname = "AIOPT_PT_symmetry_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AI Optimizer"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        state = context.window_manager.ai_optimizer_pipeline
        return not state.is_running and state.step_results == "[]"

    def draw_header(self, context):
        props = context.scene.ai_optimizer
        self.layout.prop(props, "run_symmetry", text="")

    def draw(self, context):
        layout = self.layout
        props = context.scene.ai_optimizer

        col = layout.column(align=True)
        col.prop(props, "symmetry_axis")
        col.prop(props, "symmetry_threshold")
        col.prop(props, "symmetry_min_score", slider=True)

        box = layout.box()
        help_col = box.column(align=True)
        help_col.scale_y = 0.8
        help_col.label(text="Detects near-symmetric geometry.", icon="INFO")
        help_col.label(text="Best for characters, vehicles, furniture.")
        help_col.label(text="Off by default — opt in when model is symmetric.")

        layout.separator()
        layout.operator("ai_optimizer.symmetry_mirror", icon="MOD_MIRROR")
```

**Step 5: Add to pipeline** in `AIOPT_OT_run_all.invoke`, between remove_interior and decimate:

```python
        if props.run_symmetry:
            self._steps.append(
                (
                    "Symmetry Mirror",
                    self._setup_symmetry,
                    self._tick_symmetry,
                    self._teardown_symmetry,
                )
            )
```

Add methods to `AIOPT_OT_run_all`:

```python
    # -- Symmetry Mirror --

    def _setup_symmetry(self, context):
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        self._sub_items = get_selected_meshes()
        self._symmetry_applied = 0
        self._symmetry_faces_before = count_faces(self._sub_items)
        return len(self._sub_items)

    def _tick_symmetry(self, context, index):
        props = context.scene.ai_optimizer
        obj = self._sub_items[index]
        applied, score = detect_and_apply_symmetry(
            context, obj, props.symmetry_axis, props.symmetry_threshold, props.symmetry_min_score
        )
        if applied:
            self._symmetry_applied += 1
        return f"{obj.name}: {'applied' if applied else f'skipped ({score:.0%})'}"

    def _teardown_symmetry(self, context):
        faces_after = count_faces(get_selected_meshes())
        removed = self._symmetry_faces_before - faces_after
        return f"Mirrored {self._symmetry_applied} object(s), {removed:,} faces removed"
```

**Step 6: Register** — add `AIOPT_OT_symmetry_mirror` and `AIOPT_PT_symmetry_panel` to the `classes` tuple in the correct position (operator with operators, panel between remove_interior and decimate panels).

**Step 7: Lint**

Run: `ruff check src/ && ruff format --check src/`

---

### Task 12: Vertex Color Baking — Helper Function, Properties, UI, Pipeline

**Files:**
- Modify: `src/model-optimizer-addon.py`

**Step 1: Write `bake_vertex_colors` helper**

```python
def bake_vertex_colors_single(context, obj):
    """Bake diffuse texture into vertex colors for *obj*.

    Creates a vertex color layer, bakes the diffuse color, then rewires
    the material to use vertex colors instead of image textures.

    Returns True if successful.
    """
    if not obj.data.materials:
        return False

    bpy.ops.object.select_all(action="DESELECT")
    context.view_layer.objects.active = obj
    obj.select_set(True)

    # Create vertex color layer
    if not obj.data.color_attributes:
        obj.data.color_attributes.new(name="BakedColor", type="BYTE_COLOR", domain="CORNER")

    # Bake diffuse to vertex colors
    original_engine = bpy.context.scene.render.engine
    bpy.context.scene.render.engine = "CYCLES"

    try:
        bpy.ops.object.bake(type="DIFFUSE", use_pass_direct=False, use_pass_indirect=False, target="VERTEX_COLORS")
    except RuntimeError:
        bpy.context.scene.render.engine = original_engine
        return False

    bpy.context.scene.render.engine = original_engine

    # Rewire materials: replace image texture with vertex color node
    for slot in obj.material_slots:
        mat = slot.material
        if not mat or not mat.node_tree:
            continue
        tree = mat.node_tree
        # Find BSDF and remove image texture connections to Base Color
        for node in tree.nodes:
            if node.type == "BSDF_PRINCIPLED":
                # Add vertex color node
                vc_node = tree.nodes.new("ShaderNodeVertexColor")
                vc_node.layer_name = "BakedColor"
                # Connect to base color
                tree.links.new(vc_node.outputs["Color"], node.inputs["Base Color"])
                # Remove image texture nodes
                for link in list(tree.links):
                    if link.to_socket == node.inputs["Base Color"] and link.from_node != vc_node:
                        tree.links.remove(link)
                break

    return True
```

**Step 2: Add properties** (at end of `AIOPT_Properties`)

```python
    # -- Vertex color baking --
    bake_vertex_colors: BoolProperty(
        name="Bake Vertex Colors (Experimental)",
        default=False,
        description="Bake textures into vertex colors and remove images. Low fidelity, eliminates texture payload",
    )
```

**Step 3: Add to `SAVEABLE_PROPS`**

```python
    "bake_vertex_colors",
```

**Step 4: Add UI** to `AIOPT_PT_export_panel.draw`, before the export button:

```python
        layout.separator()
        col = layout.column(align=True)
        col.label(text="Vertex Colors (Experimental):", icon="VPAINT_HLT")
        col.prop(props, "bake_vertex_colors")
        if props.bake_vertex_colors:
            box = layout.box()
            warn_col = box.column(align=True)
            warn_col.scale_y = 0.8
            warn_col.label(text="Bakes textures to vertex colors.", icon="ERROR")
            warn_col.label(text="Low fidelity — one color per vertex.")
            warn_col.label(text="Only for stylized or dense meshes.")
```

**Step 5: Integrate into export pipeline step**

In `_tick_export`, before calling `export_glb_all`, add vertex color baking:

```python
    def _tick_export(self, context, index):
        props = context.scene.ai_optimizer

        if props.bake_vertex_colors:
            meshes = get_selected_meshes()
            for obj in meshes:
                bake_vertex_colors_single(context, obj)

        detail = export_glb_all(context, props)
        return detail
```

**Step 6: Lint**

Run: `ruff check src/ && ruff format --check src/`

---

### Task 13: Update `estimate_glb_size` for New Features

**Files:**
- Modify: `src/model-optimizer-addon.py` — `estimate_glb_size` function (~line 106)

**Step 1:** Update the estimate function to account for:
- Material merge reducing overhead (fewer material entries)
- Symmetry mirror halving geometry (if enabled and assuming it applies)
- Normal map baking adding a texture
- Vertex color baking replacing textures with per-vertex data

The estimate is already a heuristic, so these are rough adjustments:

```python
    # After computing geo_bytes, before textures:

    # Symmetry mirror — assume ~40% geometry reduction if enabled
    # (conservative; actual depends on model)
    if props.run_symmetry:
        geo_bytes *= 0.6

    # ... after tex_bytes calculation:

    # Normal map bake adds a texture
    if props.bake_normal_map:
        nmap_res = int(props.normal_map_resolution)
        # Normal maps compress well in PNG (~4:1 from raw RGB)
        nmap_raw = nmap_res * nmap_res * 3
        tex_bytes += nmap_raw / 4.0

    # Vertex color baking replaces all textures with per-vertex data
    if props.bake_vertex_colors:
        verts_total = sum(len(obj.data.vertices) for obj in meshes)
        tex_bytes = verts_total * 4  # RGBA byte per vertex, replaces image textures
```

**Step 2: Lint**

Run: `ruff check src/ && ruff format --check src/`

---

### Task 14: Final Lint, Format, and Verify

**Step 1:** Run full lint and format

```bash
ruff check src/
ruff format src/
ruff check src/  # verify again after format
```

**Step 2:** Verify the `classes` tuple contains all new classes in correct order:
- `AIOPT_OT_symmetry_mirror` (with other operators)
- `AIOPT_PT_symmetry_panel` (between remove_interior and decimate panels)

**Step 3:** Verify `SAVEABLE_PROPS` contains all new property names.

**Step 4:** Verify pipeline step order in `AIOPT_OT_run_all.invoke` matches design:
1. Fix Geometry (with material merge + mesh join in teardown)
2. Remove Interior
3. Symmetry Mirror
4. Decimate (with normal map baking in teardown)
5. Clean Images
6. Clean Unused
7. Optimize Textures (with UV repack in setup)
8. LOD Generation
9. Export GLB (with vertex color baking in tick)
