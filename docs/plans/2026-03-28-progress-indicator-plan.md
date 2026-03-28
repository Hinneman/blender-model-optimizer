# Progress Indicator Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a sidebar progress indicator showing per-step status, sub-step progress, timing, and cancel-with-undo to the optimization pipeline.

**Architecture:** Convert `AIOPT_OT_run_all` from a synchronous `execute()` operator to a modal operator with a timer. Each timer tick executes one sub-step, yielding to Blender for UI redraws and cancel detection. A new `AIOPT_PipelineState` PropertyGroup on `WindowManager` holds runtime state. A conditional `AIOPT_PT_progress_panel` renders progress/results.

**Tech Stack:** Blender Python API (`bpy`), modal operators, timers, JSON for step results serialization.

**Important:** This is a single-file Blender add-on (`src/model-optimizer-addon.py`). All changes go in that file. No automated tests — verify with `ruff check src/` and `ruff format src/` after each task, then manual testing in Blender at checkpoints.

---

### Task 1: Extract helper functions from existing operators

Extract the core logic from each operator into standalone functions so the modal pipeline can call them per-object/per-image.

**Files:**
- Modify: `src/model-optimizer-addon.py` (helper functions section, lines ~50-135)
- Modify: `src/model-optimizer-addon.py` (operator classes, lines ~141-496)

**Step 1: Add `fix_geometry_single` function**

Add after the existing helper functions (after `is_print3d_available`, around line 93):

```python
def fix_geometry_single(context, obj, props):
    """Fix geometry on a single mesh object. Returns a result detail string."""
    bpy.ops.object.select_all(action='DESELECT')
    context.view_layer.objects.active = obj
    obj.select_set(True)

    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')

    bpy.ops.mesh.remove_doubles(threshold=props.merge_distance)

    if props.recalculate_normals:
        bpy.ops.mesh.normals_make_consistent(inside=False)

    method_used = "none"
    fixed = False
    if props.fix_manifold:
        try:
            bpy.ops.mesh.print3d_clean_non_manifold()
            fixed = True
            method_used = "3D Print Toolbox"
        except (AttributeError, RuntimeError):
            method_used = "manual fill holes"
            bpy.ops.mesh.select_all(action='DESELECT')
            bpy.ops.mesh.select_non_manifold(
                extend=False, use_wire=True, use_boundary=True,
                use_multi_face=True, use_non_contiguous=True, use_verts=True
            )
            try:
                bpy.ops.mesh.fill_holes(sides=32)
                fixed = True
            except RuntimeError:
                pass

    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.delete_loose(use_verts=True, use_edges=True, use_faces=False)

    bpy.ops.object.mode_set(mode='OBJECT')

    return fixed, method_used
```

**Step 2: Add `decimate_single` function**

```python
def decimate_single(context, obj, props):
    """Apply decimate modifier to a single mesh object."""
    bpy.ops.object.select_all(action='DESELECT')
    context.view_layer.objects.active = obj
    obj.select_set(True)

    mod = obj.modifiers.new(name="Decimate_Optimize", type='DECIMATE')
    mod.decimate_type = 'COLLAPSE'
    mod.ratio = props.decimate_ratio
    mod.use_collapse_triangulate = True
    bpy.ops.object.modifier_apply(modifier=mod.name)
```

**Step 3: Add `clean_images_all` function**

Move the `execute` logic from `AIOPT_OT_clean_images` into a standalone function. Keep the `get_image_fingerprint` and `images_are_identical` methods as module-level functions too.

```python
def get_image_fingerprint(img):
    """Create a fingerprint of an image's actual pixel content."""
    if not img.has_data:
        return None

    w, h = img.size[0], img.size[1]
    if w == 0 or h == 0:
        return None

    channels = img.channels
    pixels = img.pixels

    fp = (w, h, channels)

    total_pixels = w * h
    sample_count = min(16, total_pixels)

    sampled = []
    for i in range(sample_count):
        pixel_index = int((i / sample_count) * total_pixels)
        offset = pixel_index * channels
        sample = tuple(round(pixels[offset + c], 4) for c in range(min(channels, 4)))
        sampled.append(sample)

    return fp + tuple(sampled)


def images_are_identical(img_a, img_b):
    """Full pixel comparison between two images."""
    if img_a.size[0] != img_b.size[0] or img_a.size[1] != img_b.size[1]:
        return False
    if img_a.channels != img_b.channels:
        return False

    px_a = img_a.pixels[:]
    px_b = img_b.pixels[:]

    if len(px_a) != len(px_b):
        return False

    chunk = 4096
    for start in range(0, len(px_a), chunk):
        end = min(start + chunk, len(px_a))
        for i in range(start, end):
            if abs(px_a[i] - px_b[i]) > 0.001:
                return False
    return True


def clean_images_all(context):
    """Remove truly identical images. Returns (removed_count, detail_string)."""
    removed = 0
    images = [img for img in bpy.data.images
              if img.type == 'IMAGE'
              and img.has_data
              and img.name not in ('Render Result', 'Viewer Node')]

    if len(images) < 2:
        return 0, "Not enough images to compare"

    fingerprint_groups = {}
    for img in images:
        fp = get_image_fingerprint(img)
        if fp is None:
            continue
        fingerprint_groups.setdefault(fp, []).append(img)

    for _fp, group in fingerprint_groups.items():
        if len(group) < 2:
            continue

        merged = set()
        for i, img_a in enumerate(group):
            if img_a.name in merged:
                continue
            for j in range(i + 1, len(group)):
                img_b = group[j]
                if img_b.name in merged:
                    continue

                if images_are_identical(img_a, img_b):
                    users_a = get_image_users(img_a)
                    users_b = get_image_users(img_b)

                    if users_b > users_a:
                        keeper, duplicate = img_b, img_a
                    else:
                        keeper, duplicate = img_a, img_b

                    print(
                        f"  [AI Optimizer] Identical: '{duplicate.name}' == '{keeper.name}'"
                        f" → removing '{duplicate.name}'"
                    )
                    duplicate.user_remap(keeper)
                    bpy.data.images.remove(duplicate)
                    removed += 1
                    merged.add(duplicate.name)

                    if duplicate == img_a:
                        break

    return removed, f"Removed {removed} truly identical image(s)"
```

**Step 4: Add `clean_unused_all` function**

```python
def clean_unused_all(context):
    """Remove all unused data blocks. Returns (removed_count, detail_string)."""
    before = (len(bpy.data.images) + len(bpy.data.materials)
              + len(bpy.data.meshes) + len(bpy.data.textures))

    bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)

    after = (len(bpy.data.images) + len(bpy.data.materials)
             + len(bpy.data.meshes) + len(bpy.data.textures))

    removed = before - after
    return removed, f"Removed {removed} unused data block(s)"
```

**Step 5: Add `resize_texture_single` function**

```python
def resize_texture_single(img, props):
    """Resize a single image if needed. Returns True if resized."""
    max_size = props.max_texture_size
    w, h = img.size[0], img.size[1]

    if props.resize_mode == 'ALL':
        needs_resize = w != max_size or h != max_size
    else:
        needs_resize = w > max_size or h > max_size

    if not needs_resize:
        return False

    if props.resize_mode == 'ALL':
        new_w, new_h = max_size, max_size
    else:
        scale = max_size / max(w, h)
        new_w = max(1, 2 ** round(math.log2(max(1, int(w * scale)))))
        new_h = max(1, 2 ** round(math.log2(max(1, int(h * scale)))))

    img.scale(new_w, new_h)
    img.pack()
    return True
```

**Step 6: Add `export_glb_all` function**

```python
def export_glb_all(context, props):
    """Export GLB. Returns detail string."""
    if props.output_folder:
        output_dir = props.output_folder
    elif bpy.data.filepath:
        output_dir = os.path.dirname(bpy.data.filepath)
    else:
        output_dir = os.path.expanduser("~")

    output_path = os.path.join(output_dir, props.output_filename)

    export_settings = {
        "filepath": output_path,
        "export_format": "GLB",
        "use_selection": props.export_selected_only,
        "export_apply": True,
        "export_yup": True,
        "export_draco_mesh_compression_enable": props.use_draco,
        "export_draco_mesh_compression_level": props.draco_level,
        "export_draco_position_quantization": 14,
        "export_draco_normal_quantization": 10,
        "export_draco_texcoord_quantization": 12,
        "export_draco_color_quantization": 10,
        "export_image_format": props.image_format,
    }

    if props.image_format in ('JPEG', 'WEBP'):
        export_settings["export_image_quality"] = props.image_quality

    try:
        bpy.ops.export_scene.gltf(**export_settings)
    except TypeError:
        bpy.ops.export_scene.gltf(
            filepath=output_path,
            export_format="GLB",
            export_apply=True,
            export_draco_mesh_compression_enable=props.use_draco,
        )

    if os.path.exists(output_path):
        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        return f"{props.output_filename} ({size_mb:.2f} MB)"
    return "Export may have failed — file not found"
```

**Step 7: Refactor existing operators to use the new helper functions**

Update each operator's `execute()` to call the extracted functions. The operators remain as standalone buttons with the same behavior. For example, `AIOPT_OT_fix_geometry.execute()` becomes:

```python
def execute(self, context):
    props = context.scene.ai_optimizer
    meshes = get_selected_meshes()

    if not meshes:
        self.report({'ERROR'}, "No mesh objects found")
        return {'CANCELLED'}

    if context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')

    fixed = 0
    method_used = "none"
    for obj in meshes:
        obj_fixed, method_used = fix_geometry_single(context, obj, props)
        if obj_fixed:
            fixed += 1

    msg = f"Fixed geometry on {len(meshes)} object(s), {fixed} manifold fix(es)"
    if props.fix_manifold:
        msg += f" — method: {method_used}"
    self.report({'INFO'}, msg)
    return {'FINISHED'}
```

Apply the same pattern for `AIOPT_OT_decimate`, `AIOPT_OT_clean_images` (remove the methods, call module-level functions), `AIOPT_OT_clean_unused`, `AIOPT_OT_resize_textures`, and `AIOPT_OT_export_glb`.

**Step 8: Lint check**

Run: `ruff check src/ && ruff format --check src/`
Expected: No errors.

**Step 9: Commit**

```bash
git add src/model-optimizer-addon.py
git commit -m "refactor: extract step logic into standalone helper functions"
```

---

### Task 2: Add AIOPT_PipelineState PropertyGroup

Add the runtime state that tracks pipeline progress.

**Files:**
- Modify: `src/model-optimizer-addon.py` (after `AIOPT_Properties` class, before UI panels)

**Step 1: Add the PropertyGroup**

Add after the `AIOPT_Properties` class:

```python
class AIOPT_PipelineState(PropertyGroup):
    """Runtime state for pipeline progress tracking. Stored on WindowManager."""

    is_running: BoolProperty(default=False)
    was_cancelled: BoolProperty(default=False)
    current_step_index: IntProperty(default=0)
    current_step_name: StringProperty(default="")
    current_sub_step: IntProperty(default=0)
    total_sub_steps: IntProperty(default=0)
    step_results: StringProperty(default="[]")  # JSON array
    total_elapsed: FloatProperty(default=0.0)
    total_steps: IntProperty(default=0)
```

**Step 2: Register/unregister**

In `register()`, add after the `Scene.ai_optimizer` line:
```python
bpy.types.WindowManager.ai_optimizer_pipeline = bpy.props.PointerProperty(type=AIOPT_PipelineState)
```

In `unregister()`, add before the class unregistration loop:
```python
del bpy.types.WindowManager.ai_optimizer_pipeline
```

Add `AIOPT_PipelineState` to the `classes` tuple (before the operators that reference it).

**Step 3: Lint check**

Run: `ruff check src/ && ruff format --check src/`
Expected: No errors.

**Step 4: Commit**

```bash
git add src/model-optimizer-addon.py
git commit -m "feat: add AIOPT_PipelineState PropertyGroup for pipeline progress tracking"
```

---

### Task 3: Convert AIOPT_OT_run_all to modal operator

Replace the synchronous execute with a modal timer-driven state machine.

**Files:**
- Modify: `src/model-optimizer-addon.py` (`AIOPT_OT_run_all` class)

**Step 1: Add time import**

Add `import time` to the imports at the top of the file (after `import os`).

**Step 2: Rewrite AIOPT_OT_run_all**

Replace the entire class with:

```python
class AIOPT_OT_run_all(Operator):
    bl_idname = "ai_optimizer.run_all"
    bl_label = "Run Full Pipeline"
    bl_description = "Run all optimization steps in order"
    bl_options = {'REGISTER', 'UNDO'}

    _timer = None

    # Runtime state (not persisted — lives only during modal execution)
    _steps = []           # List of (name, setup_fn, tick_fn, teardown_fn) tuples
    _step_idx = 0
    _sub_items = []       # Items to iterate for current step
    _sub_idx = 0
    _step_start_time = 0.0
    _pipeline_start_time = 0.0
    _faces_before = 0     # For decimate stats

    @classmethod
    def poll(cls, context):
        state = context.window_manager.ai_optimizer_pipeline
        if state.is_running:
            return False
        meshes = get_selected_meshes()
        return len(meshes) > 0

    def _build_steps(self, context):
        """Build the list of enabled pipeline steps."""
        props = context.scene.ai_optimizer
        steps = []

        if props.run_fix_geometry:
            steps.append(("Fix Geometry", self._setup_geometry, self._tick_geometry, self._teardown_geometry))
        if props.run_decimate:
            steps.append(("Decimate", self._setup_decimate, self._tick_decimate, self._teardown_decimate))
        if props.run_clean_images:
            steps.append(("Clean Images", self._setup_single, self._tick_clean_images, self._teardown_noop))
        if props.run_clean_unused:
            steps.append(("Clean Unused", self._setup_single, self._tick_clean_unused, self._teardown_noop))
        if props.run_resize_textures:
            steps.append(("Resize Textures", self._setup_resize, self._tick_resize, self._teardown_resize))
        if props.run_export:
            steps.append(("Export GLB", self._setup_single, self._tick_export, self._teardown_noop))

        return steps

    # -- Setup functions (return list of sub-items) --

    def _setup_geometry(self, context):
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        meshes = get_selected_meshes()
        self._sub_items = list(meshes)
        self._geometry_fixed = 0
        self._geometry_method = "none"
        return len(meshes)

    def _setup_decimate(self, context):
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        meshes = get_selected_meshes()
        self._sub_items = list(meshes)
        self._faces_before = count_faces(meshes)
        return len(meshes)

    def _setup_resize(self, context):
        props = context.scene.ai_optimizer
        max_size = props.max_texture_size
        images = []
        for img in bpy.data.images:
            if img.type != 'IMAGE' or not img.has_data:
                continue
            if img.name in ('Render Result', 'Viewer Node'):
                continue
            w, h = img.size[0], img.size[1]
            if props.resize_mode == 'ALL':
                needs_resize = w != max_size or h != max_size
            else:
                needs_resize = w > max_size or h > max_size
            if needs_resize:
                images.append(img)
        self._sub_items = images
        self._resized_count = 0
        return max(len(images), 1)

    def _setup_single(self, context):
        self._sub_items = [None]
        return 1

    # -- Tick functions (process one sub-item, return detail string or None) --

    def _tick_geometry(self, context, item):
        props = context.scene.ai_optimizer
        obj_fixed, method = fix_geometry_single(context, item, props)
        if obj_fixed:
            self._geometry_fixed += 1
        self._geometry_method = method
        return None  # Detail reported at teardown

    def _tick_decimate(self, context, item):
        props = context.scene.ai_optimizer
        decimate_single(context, item, props)
        return None  # Detail reported at teardown

    def _tick_clean_images(self, context, _item):
        removed, detail = clean_images_all(context)
        return detail

    def _tick_clean_unused(self, context, _item):
        removed, detail = clean_unused_all(context)
        return detail

    def _tick_resize(self, context, item):
        props = context.scene.ai_optimizer
        if resize_texture_single(item, props):
            self._resized_count += 1
        return None  # Detail reported at teardown

    def _tick_export(self, context, _item):
        props = context.scene.ai_optimizer
        return export_glb_all(context, props)

    # -- Teardown functions (return detail string) --

    def _teardown_geometry(self, context):
        meshes = get_selected_meshes()
        msg = f"Fixed {len(meshes)} object(s), {self._geometry_fixed} manifold fix(es)"
        props = context.scene.ai_optimizer
        if props.fix_manifold:
            msg += f" — {self._geometry_method}"
        return msg

    def _teardown_decimate(self, context):
        meshes = get_selected_meshes()
        faces_after = count_faces(meshes)
        reduction = (1 - faces_after / max(self._faces_before, 1)) * 100
        return f"{self._faces_before:,} → {faces_after:,} faces ({reduction:.1f}% reduction)"

    def _teardown_resize(self, context):
        props = context.scene.ai_optimizer
        return f"Resized {self._resized_count} texture(s) to max {props.max_texture_size}px"

    def _teardown_noop(self, context):
        return None

    # -- Modal lifecycle --

    def invoke(self, context, event):
        self._steps = self._build_steps(context)
        if not self._steps:
            self.report({'WARNING'}, "No pipeline steps enabled")
            return {'CANCELLED'}

        # Push undo snapshot for cancel rollback
        bpy.ops.ed.undo_push(message="Before AI Optimizer Pipeline")

        # Initialize state
        state = context.window_manager.ai_optimizer_pipeline
        state.is_running = True
        state.was_cancelled = False
        state.current_step_index = 0
        state.total_steps = len(self._steps)
        state.step_results = "[]"
        state.total_elapsed = 0.0

        self._step_idx = 0
        self._sub_idx = 0
        self._pipeline_start_time = time.monotonic()
        self._results = []

        # Setup first step
        name, setup_fn, _, _ = self._steps[0]
        state.current_step_name = name
        total_subs = setup_fn(context)
        state.current_sub_step = 0
        state.total_sub_steps = total_subs

        self._step_start_time = time.monotonic()

        # Start timer
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.01, window=context.window)
        wm.modal_handler_add(self)

        # Force initial redraw
        self._redraw(context)

        return {'RUNNING_MODAL'}

    def _redraw(self, context):
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()

    def _finish(self, context, cancelled=False):
        """Clean up timer and finalize state."""
        wm = context.window_manager
        if self._timer:
            wm.event_timer_remove(self._timer)
            self._timer = None

        state = wm.ai_optimizer_pipeline
        state.is_running = False
        state.total_elapsed = time.monotonic() - self._pipeline_start_time
        state.step_results = json.dumps(self._results)

        if cancelled:
            state.was_cancelled = True
            bpy.ops.ed.undo()

        self._redraw(context)

    def modal(self, context, event):
        state = context.window_manager.ai_optimizer_pipeline

        # Check for cancel
        if event.type in {'ESC'} or state.was_cancelled:
            # Mark current step as cancelled in results
            if self._step_idx < len(self._steps):
                name = self._steps[self._step_idx][0]
                elapsed = time.monotonic() - self._step_start_time
                self._results.append({
                    "name": name, "status": "cancelled",
                    "duration": round(elapsed, 2), "detail": ""
                })
            # Mark remaining steps as skipped
            for i in range(self._step_idx + 1, len(self._steps)):
                self._results.append({
                    "name": self._steps[i][0], "status": "skipped",
                    "duration": 0, "detail": ""
                })
            self._finish(context, cancelled=True)
            self.report({'WARNING'}, "Pipeline cancelled — changes undone")
            return {'CANCELLED'}

        if event.type != 'TIMER':
            return {'PASS_THROUGH'}

        # Execute one sub-step
        if self._step_idx >= len(self._steps):
            self._finish(context)
            self.report({'INFO'}, f"Pipeline complete in {state.total_elapsed:.1f}s")
            return {'FINISHED'}

        name, setup_fn, tick_fn, teardown_fn = self._steps[self._step_idx]

        # Process current sub-item
        if self._sub_idx < len(self._sub_items):
            item = self._sub_items[self._sub_idx]
            detail = tick_fn(context, item)
            self._sub_idx += 1
            state.current_sub_step = self._sub_idx

            # Check if step is complete
            if self._sub_idx >= len(self._sub_items):
                teardown_detail = teardown_fn(context)
                elapsed = time.monotonic() - self._step_start_time
                final_detail = detail or teardown_detail or ""
                self._results.append({
                    "name": name, "status": "completed",
                    "duration": round(elapsed, 2), "detail": final_detail
                })

                # Advance to next step
                self._step_idx += 1
                state.current_step_index = self._step_idx

                if self._step_idx < len(self._steps):
                    next_name, next_setup, _, _ = self._steps[self._step_idx]
                    state.current_step_name = next_name
                    total_subs = next_setup(context)
                    state.current_sub_step = 0
                    state.total_sub_steps = total_subs
                    self._sub_idx = 0
                    self._step_start_time = time.monotonic()
                else:
                    # All done
                    self._finish(context)
                    self.report({'INFO'}, f"Pipeline complete in {state.total_elapsed:.1f}s")
                    self._redraw(context)
                    return {'FINISHED'}

        self._redraw(context)
        return {'RUNNING_MODAL'}
```

**Step 3: Lint check**

Run: `ruff check src/ && ruff format --check src/`
Expected: No errors.

**Step 4: Commit**

```bash
git add src/model-optimizer-addon.py
git commit -m "feat: convert run_all to modal operator with timer-driven pipeline"
```

---

### Task 4: Add cancel and dismiss operators

Small operators for the UI buttons.

**Files:**
- Modify: `src/model-optimizer-addon.py` (after `AIOPT_OT_run_all`)

**Step 1: Add AIOPT_OT_cancel_pipeline**

```python
class AIOPT_OT_cancel_pipeline(Operator):
    bl_idname = "ai_optimizer.cancel_pipeline"
    bl_label = "Cancel Pipeline"
    bl_description = "Cancel the running pipeline and undo all changes"

    @classmethod
    def poll(cls, context):
        state = context.window_manager.ai_optimizer_pipeline
        return state.is_running

    def execute(self, context):
        state = context.window_manager.ai_optimizer_pipeline
        state.was_cancelled = True
        return {'FINISHED'}
```

**Step 2: Add AIOPT_OT_dismiss_pipeline**

```python
class AIOPT_OT_dismiss_pipeline(Operator):
    bl_idname = "ai_optimizer.dismiss_pipeline"
    bl_label = "Dismiss"
    bl_description = "Dismiss the pipeline results"

    @classmethod
    def poll(cls, context):
        state = context.window_manager.ai_optimizer_pipeline
        return not state.is_running and state.step_results != "[]"

    def execute(self, context):
        state = context.window_manager.ai_optimizer_pipeline
        state.step_results = "[]"
        state.was_cancelled = False
        state.total_elapsed = 0.0
        state.current_step_index = 0
        state.total_steps = 0
        return {'FINISHED'}
```

**Step 3: Add both to the `classes` tuple**

Add `AIOPT_OT_cancel_pipeline` and `AIOPT_OT_dismiss_pipeline` after `AIOPT_OT_run_all` in the tuple.

**Step 4: Lint check**

Run: `ruff check src/ && ruff format --check src/`
Expected: No errors.

**Step 5: Commit**

```bash
git add src/model-optimizer-addon.py
git commit -m "feat: add cancel and dismiss pipeline operators"
```

---

### Task 5: Add progress panel UI

The conditional panel that shows during/after pipeline execution.

**Files:**
- Modify: `src/model-optimizer-addon.py` (after `AIOPT_PT_main_panel`, before `AIOPT_PT_geometry_panel`)

**Step 1: Add AIOPT_PT_progress_panel**

```python
class AIOPT_PT_progress_panel(Panel):
    bl_label = "Pipeline Progress"
    bl_idname = "AIOPT_PT_progress_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "AI Optimizer"
    bl_parent_id = "AIOPT_PT_main_panel"
    bl_options = set()  # Not closeable

    @classmethod
    def poll(cls, context):
        state = context.window_manager.ai_optimizer_pipeline
        return state.is_running or state.step_results != "[]"

    def draw(self, context):
        layout = self.layout
        state = context.window_manager.ai_optimizer_pipeline

        results = json.loads(state.step_results) if state.step_results != "[]" else []

        if state.is_running:
            self._draw_running(layout, state, results)
        elif state.was_cancelled:
            self._draw_cancelled(layout, state, results)
        else:
            self._draw_completed(layout, state, results)

    def _draw_running(self, layout, state, results):
        col = layout.column(align=True)

        # Completed steps
        for result in results:
            row = col.row()
            row.label(text=f"{result['name']}", icon='CHECKMARK')
            row.label(text=f"{result['duration']:.1f}s")

        # Current step
        row = col.row()
        row.label(text=f"{state.current_step_name}", icon='PLAY')
        if state.total_sub_steps > 1:
            row.label(text=f"{state.current_sub_step}/{state.total_sub_steps}")

        # Pending steps — compute from total_steps minus completed minus current
        pending_count = state.total_steps - len(results) - 1
        # We don't have pending step names in state, so we skip drawing them
        # (they'll appear as the pipeline advances)

        layout.separator()

        # Progress bar
        completed = len(results)
        if state.total_sub_steps > 0:
            step_fraction = state.current_sub_step / state.total_sub_steps
        else:
            step_fraction = 0
        overall = (completed + step_fraction) / max(state.total_steps, 1)

        col = layout.column(align=True)
        col.prop(
            state, "current_sub_step",
            text=f"Step {completed + 1}/{state.total_steps} ({overall:.0%})",
            slider=True,
        )

        layout.separator()
        layout.operator("ai_optimizer.cancel_pipeline", icon='CANCEL')

    def _draw_cancelled(self, layout, state, results):
        col = layout.column(align=True)
        col.label(text="Pipeline Cancelled", icon='CANCEL')
        col.separator()

        for result in results:
            row = col.row()
            if result["status"] == "completed":
                row.label(text=result["name"], icon='CHECKMARK')
                row.label(text=f"{result['duration']:.1f}s")
            elif result["status"] == "cancelled":
                row.label(text=result["name"], icon='X')
                row.label(text="(cancelled)")
            elif result["status"] == "skipped":
                row.label(text=result["name"], icon='RADIOBUT_OFF')
                row.label(text="(skipped)")

        col.separator()
        col.label(text="Changes have been undone.", icon='LOOP_BACK')

        layout.separator()
        layout.operator("ai_optimizer.dismiss_pipeline", icon='X')

    def _draw_completed(self, layout, state, results):
        col = layout.column(align=True)
        col.label(text="Pipeline Complete", icon='CHECKMARK')
        col.separator()

        for result in results:
            row = col.row()
            row.label(text=result["name"], icon='CHECKMARK')
            row.label(text=f"{result['duration']:.1f}s")
            if result.get("detail"):
                col.label(text=f"  {result['detail']}")

        col.separator()
        col.label(text=f"Total: {state.total_elapsed:.1f}s")

        layout.separator()
        layout.operator("ai_optimizer.dismiss_pipeline", icon='X')
```

**Step 2: Add to `classes` tuple**

Add `AIOPT_PT_progress_panel` after `AIOPT_PT_main_panel` in the tuple.

**Step 3: Disable Run All button during execution**

In `AIOPT_PT_main_panel.draw()`, the `poll()` on `AIOPT_OT_run_all` already handles disabling the button (it checks `state.is_running`). No additional change needed.

**Step 4: Lint check**

Run: `ruff check src/ && ruff format --check src/`
Expected: No errors.

**Step 5: Commit**

```bash
git add src/model-optimizer-addon.py
git commit -m "feat: add progress panel UI with running/completed/cancelled states"
```

---

### Task 6: Add pending step names to running view

The running view currently can't show pending step names because the state only tracks `total_steps`. Add a `step_names` JSON property so the panel can show all step names.

**Files:**
- Modify: `src/model-optimizer-addon.py`

**Step 1: Add `step_names` property to `AIOPT_PipelineState`**

```python
step_names: StringProperty(default="[]")  # JSON array of step names
```

**Step 2: Set `step_names` in `AIOPT_OT_run_all.invoke()`**

After building steps, add:
```python
state.step_names = json.dumps([s[0] for s in self._steps])
```

**Step 3: Update `_draw_running` to show pending steps**

Replace the pending steps section with:
```python
# Pending steps
all_names = json.loads(state.step_names) if state.step_names != "[]" else []
completed_count = len(results)
for i in range(completed_count + 1, len(all_names)):
    row = col.row()
    row.label(text=all_names[i], icon='RADIOBUT_OFF')
```

**Step 4: Clear `step_names` in `AIOPT_OT_dismiss_pipeline.execute()`**

Add: `state.step_names = "[]"`

**Step 5: Lint check**

Run: `ruff check src/ && ruff format --check src/`
Expected: No errors.

**Step 6: Commit**

```bash
git add src/model-optimizer-addon.py
git commit -m "feat: show pending step names in running progress view"
```

---

### Task 7: Replace progress bar with custom draw

The slider-based progress bar from Task 5 is a workaround. Replace with a cleaner text-based progress display since Blender's layout doesn't support true progress bars in panels.

**Files:**
- Modify: `src/model-optimizer-addon.py` (progress panel)

**Step 1: Replace the slider progress with text**

In `_draw_running`, replace the progress bar section with:

```python
# Overall progress text
completed = len(results)
if state.total_sub_steps > 0:
    step_fraction = state.current_sub_step / state.total_sub_steps
else:
    step_fraction = 0
overall = (completed + step_fraction) / max(state.total_steps, 1)

box = layout.box()
row = box.row()
row.label(text=f"Step {completed + 1}/{state.total_steps}")
row.label(text=f"{overall:.0%}")
```

**Step 2: Lint check**

Run: `ruff check src/ && ruff format --check src/`
Expected: No errors.

**Step 3: Commit**

```bash
git add src/model-optimizer-addon.py
git commit -m "fix: use text-based progress display instead of slider workaround"
```

---

### Task 8: Final lint, format, and version bump

**Files:**
- Modify: `src/model-optimizer-addon.py` (bl_info version)
- Modify: `pyproject.toml` (project version)

**Step 1: Run full lint and format**

Run: `ruff check src/ --fix && ruff format src/`
Expected: Clean output.

**Step 2: Bump version to 1.3.0**

In `bl_info`, change `"version": (1, 2, 0)` to `"version": (1, 3, 0)`.
In `pyproject.toml`, change `version = "1.2.0"` to `version = "1.3.0"`.

**Step 3: Lint check**

Run: `ruff check src/ && ruff format --check src/`
Expected: No errors.

**Step 4: Commit**

```bash
git add src/model-optimizer-addon.py pyproject.toml
git commit -m "chore: bump version to 1.3.0 for progress indicator feature"
```

---

### Manual Testing Checkpoint

After all tasks, install the add-on in Blender and verify:

1. **Run Full Pipeline** — progress panel appears, shows each step advancing with sub-step counts
2. **Timing** — each step shows duration, total elapsed shown at end
3. **Cancel (ESC)** — pipeline stops, changes are undone, cancelled panel appears
4. **Cancel (button)** — same as ESC
5. **Dismiss** — clears the progress panel
6. **Re-run** — can run pipeline again after dismiss
7. **Individual step buttons** — still work independently (no regression)
8. **Undo after completion** — Ctrl+Z undoes the entire pipeline
