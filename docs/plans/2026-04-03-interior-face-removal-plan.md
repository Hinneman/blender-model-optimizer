# Interior Face Removal — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a pipeline step that detects and removes geometry hidden inside a model's outer shell, placed between Fix Geometry and Decimate.

**Architecture:** New helper functions (`remove_interior_loose_parts` and `remove_interior_raycast`) dispatched by `remove_interior_single`. New operator, pipeline modal methods, properties, and UI panel following existing patterns.

**Tech Stack:** Blender Python API (`bpy`), `mathutils` for vector math and BVH ray casting.

---

### Task 1: Add properties

**Files:**
- Modify: `src/model-optimizer-addon.py` — `AIOPT_Properties` class and `SAVEABLE_PROPS`

**Step 1: Add the enum and toggle properties**

In `AIOPT_Properties`, after the `fix_manifold` property (~line 1064) and before `# -- Decimate settings --` (~line 1067), add:

```python
    # -- Remove Interior settings --
    run_remove_interior: BoolProperty(
        name="Remove Interior", default=True, description="Remove hidden interior geometry"
    )
    interior_method: EnumProperty(
        name="Method",
        items=[
            ("LOOSE_PARTS", "Enclosed Parts", "Remove disconnected mesh parts fully inside other geometry. Fast, best for AI-generated models"),
            ("RAY_CAST", "Ray Cast", "Cast rays from each face to detect occlusion. Slower but catches interior faces within connected geometry"),
        ],
        default="LOOSE_PARTS",
        description="Method used to detect interior faces",
    )
```

**Step 2: Add to SAVEABLE_PROPS**

In `SAVEABLE_PROPS` list (~line 414), after `"run_fix_geometry",` add:

```python
    "run_remove_interior",
    "interior_method",
```

**Step 3: Lint**

Run: `ruff check src/ && ruff format src/`

---

### Task 2: Add helper functions — LOOSE_PARTS method

**Files:**
- Modify: `src/model-optimizer-addon.py` — add after `fix_geometry_single` function (after line 158)

**Step 1: Add imports**

At the top of the file, add `mathutils` to the imports (after the `bpy` imports):

```python
from mathutils import Vector
```

**Step 2: Add bounding box helper**

```python
def _bbox_contains(outer_obj, inner_obj):
    """Check if inner_obj's bounding box is fully inside outer_obj's bounding box."""
    outer_corners = [outer_obj.matrix_world @ Vector(c) for c in outer_obj.bound_box]
    inner_corners = [inner_obj.matrix_world @ Vector(c) for c in inner_obj.bound_box]

    outer_min = Vector((min(c.x for c in outer_corners), min(c.y for c in outer_corners), min(c.z for c in outer_corners)))
    outer_max = Vector((max(c.x for c in outer_corners), max(c.y for c in outer_corners), max(c.z for c in outer_corners)))

    for c in inner_corners:
        if c.x <= outer_min.x or c.x >= outer_max.x:
            return False
        if c.y <= outer_min.y or c.y >= outer_max.y:
            return False
        if c.z <= outer_min.z or c.z >= outer_max.z:
            return False
    return True
```

**Step 3: Add LOOSE_PARTS removal function**

```python
def _remove_interior_loose_parts(context, obj):
    """Remove disconnected mesh parts that are fully enclosed inside other parts.

    Separates mesh into loose parts, checks bounding-box containment,
    deletes enclosed parts, and re-joins the remainder.
    Returns the number of faces removed.
    """
    faces_before = len(obj.data.polygons)
    original_name = obj.name

    bpy.ops.object.select_all(action="DESELECT")
    context.view_layer.objects.active = obj
    obj.select_set(True)

    # Separate into loose parts
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.separate(type="LOOSE")
    bpy.ops.object.mode_set(mode="OBJECT")

    # Collect all parts (original + newly separated)
    parts = [o for o in context.selected_objects if o.type == "MESH"]
    if len(parts) <= 1:
        # Nothing was separated — single connected mesh
        return 0

    # Sort by face count descending — largest is most likely the outer shell
    parts.sort(key=lambda o: len(o.data.polygons), reverse=True)

    to_delete = []
    for i, inner in enumerate(parts):
        for outer in parts:
            if inner == outer:
                continue
            if _bbox_contains(outer, inner):
                to_delete.append(inner)
                break

    # Delete enclosed parts
    bpy.ops.object.select_all(action="DESELECT")
    for obj_del in to_delete:
        obj_del.select_set(True)

    if to_delete:
        bpy.ops.object.delete()

    # Re-join remaining parts into one object
    remaining = [o for o in context.scene.objects if o.type == "MESH" and o not in to_delete]
    if remaining:
        bpy.ops.object.select_all(action="DESELECT")
        for o in remaining:
            o.select_set(True)
        context.view_layer.objects.active = remaining[0]
        if len(remaining) > 1:
            bpy.ops.object.join()
        remaining[0].name = original_name

    faces_after = len(context.view_layer.objects.active.data.polygons) if context.view_layer.objects.active else 0
    return faces_before - faces_after
```

**Step 4: Lint**

Run: `ruff check src/ && ruff format src/`

---

### Task 3: Add helper functions — RAY_CAST method

**Files:**
- Modify: `src/model-optimizer-addon.py` — add after the loose parts function

**Step 1: Add RAY_CAST removal function**

```python
def _remove_interior_raycast(context, obj):
    """Remove interior faces by casting rays outward from each face center.

    For each face, casts rays along the face normal (and jittered directions).
    If all rays hit back-faces of the same object, the face is considered interior.
    Returns the number of faces removed.
    """
    import bmesh
    from mathutils import Vector

    faces_before = len(obj.data.polygons)

    bpy.ops.object.select_all(action="DESELECT")
    context.view_layer.objects.active = obj
    obj.select_set(True)

    # Ensure up-to-date mesh data
    depsgraph = context.evaluated_depsgraph_get()
    eval_obj = obj.evaluated_get(depsgraph)

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.faces.ensure_lookup_table()

    # Small offset to avoid self-intersection
    OFFSET = 0.001
    # Jitter directions around the normal
    jitter_offsets = [
        Vector((0, 0, 0)),
        Vector((0.1, 0.1, 0)),
        Vector((-0.1, 0.1, 0)),
        Vector((0.1, -0.1, 0)),
        Vector((-0.1, -0.1, 0)),
    ]

    interior_faces = []
    for face in bm.faces:
        center = obj.matrix_world @ face.calc_center_median()
        normal = (obj.matrix_world.to_3x3() @ face.normal).normalized()

        all_blocked = True
        for jitter in jitter_offsets:
            direction = (normal + jitter).normalized()
            origin = center + normal * OFFSET

            # Cast in object local space
            local_origin = obj.matrix_world.inverted() @ origin
            local_dir = (obj.matrix_world.inverted().to_3x3() @ direction).normalized()

            hit, _loc, hit_normal, _idx = obj.ray_cast(local_origin, local_dir)
            if not hit:
                all_blocked = False
                break
            # Check if we hit a back-face (normal pointing same direction as ray)
            if hit_normal.dot(local_dir) < 0:
                all_blocked = False
                break

        if all_blocked:
            interior_faces.append(face)

    # Delete interior faces
    bmesh.ops.delete(bm, geom=interior_faces, context="FACES")

    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()

    faces_after = len(obj.data.polygons)
    return faces_before - faces_after
```

**Step 2: Add dispatcher function**

```python
def remove_interior_single(context, obj, props):
    """Remove interior faces from *obj* using the configured method.
    Returns the number of faces removed.
    """
    if props.interior_method == "RAY_CAST":
        return _remove_interior_raycast(context, obj)
    return _remove_interior_loose_parts(context, obj)
```

**Step 3: Lint**

Run: `ruff check src/ && ruff format src/`

---

### Task 4: Add standalone operator

**Files:**
- Modify: `src/model-optimizer-addon.py` — add after `AIOPT_OT_fix_geometry` class (after line 503)

**Step 1: Add operator class**

```python
class AIOPT_OT_remove_interior(Operator):
    bl_idname = "ai_optimizer.remove_interior"
    bl_label = "Remove Interior"
    bl_description = "Remove hidden interior geometry"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.ai_optimizer
        meshes = get_selected_meshes()

        if not meshes:
            self.report({"ERROR"}, "No mesh objects found")
            return {"CANCELLED"}

        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        total_removed = 0
        for obj in meshes:
            total_removed += remove_interior_single(context, obj, props)

        method_label = "enclosed parts" if props.interior_method == "LOOSE_PARTS" else "ray cast"
        self.report({"INFO"}, f"Removed {total_removed:,} interior faces ({method_label})")
        return {"FINISHED"}
```

**Step 2: Add to registration tuple**

In the `classes` tuple (~line 1531), after `AIOPT_OT_fix_geometry,` add:

```python
    AIOPT_OT_remove_interior,
```

**Step 3: Lint**

Run: `ruff check src/ && ruff format src/`

---

### Task 5: Add modal pipeline integration

**Files:**
- Modify: `src/model-optimizer-addon.py` — `AIOPT_OT_run_all` class

**Step 1: Add step to pipeline build**

In `invoke()` (~line 637), after the `run_fix_geometry` block and before the `run_decimate` block, add:

```python
        if props.run_remove_interior:
            self._steps.append(
                (
                    "Remove Interior",
                    self._setup_remove_interior,
                    self._tick_remove_interior,
                    self._teardown_remove_interior,
                )
            )
```

**Step 2: Add class attribute**

Add to class attributes (near `_faces_before`):

```python
    _interior_removed: int
```

**Step 3: Add modal methods**

After `_teardown_fix_geometry` and before `# -- Decimate --`, add:

```python
    # -- Remove Interior --

    def _setup_remove_interior(self, context):
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        self._sub_items = get_selected_meshes()
        self._interior_removed = 0
        return len(self._sub_items)

    def _tick_remove_interior(self, context, index):
        props = context.scene.ai_optimizer
        obj = self._sub_items[index]
        removed = remove_interior_single(context, obj, props)
        self._interior_removed += removed
        return obj.name

    def _teardown_remove_interior(self, context):
        return f"Removed {self._interior_removed:,} interior faces"
```

**Step 4: Lint**

Run: `ruff check src/ && ruff format src/`

---

### Task 6: Add UI panel

**Files:**
- Modify: `src/model-optimizer-addon.py` — add panel class and update pipeline toggles

**Step 1: Add panel class**

After `AIOPT_PT_geometry_panel` class (after its `draw` method) and before `AIOPT_PT_decimate_panel`, add:

```python
class AIOPT_PT_remove_interior_panel(Panel):
    bl_label = "Remove Interior"
    bl_idname = "AIOPT_PT_remove_interior_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AI Optimizer"
    bl_parent_id = "AIOPT_PT_main_panel"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        state = context.window_manager.ai_optimizer_pipeline
        return not state.is_running and state.step_results == "[]"

    def draw(self, context):
        layout = self.layout
        props = context.scene.ai_optimizer

        col = layout.column(align=True)
        col.prop(props, "interior_method")

        # Dynamic help text based on selected method
        box = layout.box()
        help_col = box.column(align=True)
        help_col.scale_y = 0.8
        if props.interior_method == "LOOSE_PARTS":
            help_col.label(text="Removes disconnected mesh parts", icon="INFO")
            help_col.label(text="fully inside other geometry.")
            help_col.label(text="Fast, best for AI-generated models.")
        else:
            help_col.label(text="Casts rays from each face to detect", icon="INFO")
            help_col.label(text="occlusion. Slower but catches interior")
            help_col.label(text="faces within connected geometry.")

        layout.separator()
        layout.operator("ai_optimizer.remove_interior", icon="MESH_DATA")
```

**Step 2: Add toggle to main panel**

In the main panel's "Steps to include" section (~line 1213), change from:

```python
        row = col.row(align=True)
        row.prop(props, "run_fix_geometry", toggle=True, text="Geometry")
        row.prop(props, "run_decimate", toggle=True, text="Decimate")
```

to:

```python
        row = col.row(align=True)
        row.prop(props, "run_fix_geometry", toggle=True, text="Geometry")
        row.prop(props, "run_remove_interior", toggle=True, text="Interior")
        row.prop(props, "run_decimate", toggle=True, text="Decimate")
```

**Step 3: Add to registration tuple**

In the `classes` tuple, after `AIOPT_PT_geometry_panel,` add:

```python
    AIOPT_PT_remove_interior_panel,
```

**Step 4: Lint and format**

Run: `ruff check src/ && ruff format src/`

---

### Task 7: Final verification

**Step 1: Full lint and format pass**

Run: `ruff check src/ && ruff format src/`

**Step 2: Verify all references are consistent**

- `remove_interior_single` is called by both the operator and the modal tick
- `AIOPT_OT_remove_interior` is in the `classes` tuple
- `AIOPT_PT_remove_interior_panel` is in the `classes` tuple
- `run_remove_interior` and `interior_method` are in `SAVEABLE_PROPS`
- Pipeline order: Fix Geometry → Remove Interior → Decimate
