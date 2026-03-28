# Progress Indicator Design

**Date:** 2026-03-28
**Status:** Approved

## Summary

Add a sidebar progress indicator to the AI Model Optimizer that shows per-step status, sub-step progress, timing, and overall pipeline completion. The pipeline becomes cancellable with full undo on cancel.

## Approach

**Modal operator with timer** (Approach A). Convert `AIOPT_OT_run_all` into a modal operator that advances one sub-step per timer tick, yielding control to Blender between ticks for UI redraws and cancel detection.

## Pipeline State Machine

State: `IDLE -> RUNNING -> COMPLETED | CANCELLED`

Pipeline state stored in `AIOPT_PipelineState` PropertyGroup on `WindowManager`:

| Property           | Type         | Purpose                                                  |
|--------------------|--------------|----------------------------------------------------------|
| `is_running`       | Bool         | Guards UI drawing and prevents re-entry                  |
| `was_cancelled`    | Bool         | True if user cancelled                                   |
| `current_step_index` | Int        | Which pipeline step we're on (0-based)                   |
| `current_step_name`  | String     | Display name of current step                             |
| `current_sub_step`   | Int        | Current object/image within a step                       |
| `total_sub_steps`    | Int        | Total objects/images in current step                     |
| `step_results`       | String (JSON) | Per-step results: name, status, duration, detail message |
| `total_elapsed`      | Float      | Total pipeline time                                      |

### Step execution flow (one timer tick = one sub-step)

1. Timer fires -> operator's `modal()` is called
2. If first sub-step of a new step: record start time, count objects/images, update `current_step_name`
3. Execute one sub-step (e.g., decimate one mesh, resize one image)
4. Increment `current_sub_step`, force area redraw
5. If step complete: record duration and result message, advance `current_step_index`
6. If all steps done: set state to COMPLETED, remove timer
7. If ESC or cancel button: set `was_cancelled`, call `bpy.ops.ed.undo()`, remove timer

Steps that are single operations (clean_unused, clean_images) execute in one tick with `sub_step = 1/1`.

## Refactoring Step Logic

Extract core logic into standalone functions for sub-step granularity. Existing operators become thin wrappers.

| Step             | Function                              | Granularity              |
|------------------|---------------------------------------|--------------------------|
| Fix Geometry     | `fix_geometry_single(context, obj, props)` | Per mesh object      |
| Decimate         | `decimate_single(context, obj, props)`     | Per mesh object      |
| Clean Images     | `clean_images_all(context)`                | Single operation (1/1) |
| Clean Unused     | `clean_unused_all(context)`                | Single operation (1/1) |
| Resize Textures  | `resize_texture_single(context, img, props)` | Per image          |
| Export GLB       | `export_glb_all(context, props)`           | Single operation (1/1) |

Each function returns a result detail string for the progress panel.

### Setup/teardown per step

- Fix Geometry: edit mode entry before sub-steps, object mode restore after
- Decimate: record `faces_before` at setup, `faces_after` at teardown
- Resize Textures: filter images needing resize at setup, iterate filtered list

## UI: Progress Panel

`AIOPT_PT_progress_panel` appears conditionally — only when the pipeline is running or has results. Sits between "Full Pipeline" box and sub-panels.

### During execution

```
+-- Pipeline Progress ---------------------+
| * Fix Geometry         check  0.8s       |
| * Decimate             check  1.2s       |
| > Clean Images         3/5 images        |
| o Clean Unused                           |
| o Resize Textures                        |
| o Export GLB                             |
|                                          |
| ============-------  Step 3/6 (52%)      |
|                                          |
| [Cancel Pipeline]                        |
+------------------------------------------+
```

- Checkmark = completed step
- Arrow = active step (with sub-step count where applicable)
- Circle = pending step
- Skipped steps (toggled off) not listed
- Progress: `(completed_steps + current_sub_step/total_sub_steps) / total_steps`

### After completion

Shows each step with checkmark, detail message, timing, and total elapsed.
Dismiss button clears state and hides panel.

### After cancellation

Shows completed steps, cancelled step marked with X, remaining steps as skipped.
Message: "Changes have been undone."
Dismiss button clears state.

## Undo / Cancel Mechanics

### Before pipeline starts
- Push undo snapshot via `bpy.ops.ed.undo_push(message="Before AI Optimizer Pipeline")`

### On cancel (ESC key or Cancel button)
1. Modal detects cancel -> sets `was_cancelled = True`
2. Calls `bpy.ops.ed.undo()` to roll back to snapshot
3. Removes timer, returns `{'CANCELLED'}`
4. Pipeline state preserved (on `WindowManager`, not undo stack)

### On completion
- Pipeline finishes normally, entire run is one undo step
- User can Ctrl+Z to undo the whole pipeline

### Re-entry guard
- "Run Full Pipeline" button disabled while `pipeline_state.is_running` is True

## Timer & Redraw

- **Timer interval:** 0.01s (10ms)
- **Redraw:** Tag VIEW_3D areas after each sub-step
- **Modal events:** TIMER -> next sub-step, ESC -> cancel, all others -> PASS_THROUGH
- User can still interact with Blender (orbit, hover) during execution

## New Classes

| Class                      | Type          | Purpose                                      |
|----------------------------|---------------|----------------------------------------------|
| `AIOPT_PipelineState`      | PropertyGroup | Runtime progress state on WindowManager      |
| `AIOPT_OT_run_all`         | Operator (mod)| Modal with timer, drives state machine       |
| `AIOPT_OT_cancel_pipeline` | Operator      | Sets `was_cancelled` flag                    |
| `AIOPT_OT_dismiss_pipeline`| Operator      | Clears state, hides progress panel           |
| `AIOPT_PT_progress_panel`  | Panel         | Conditional progress/results/cancelled panel |

## No Changes To

- `AIOPT_Properties` (no new user-facing settings)
- `AIOPT_PT_main_panel` (except disabling Run All during execution)
- Sub-panels (geometry, decimate, textures, export, presets)
- Individual step operator buttons (still work standalone)
