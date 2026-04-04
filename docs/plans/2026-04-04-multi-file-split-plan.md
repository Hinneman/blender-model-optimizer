# Multi-file Addon Split — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Split the monolithic `src/model-optimizer-addon.py` into a multi-file Python package with a build script that concatenates it back into a single installable `.py`, plus a GitHub Actions release pipeline.

**Architecture:** Source lives as a standard Python package under `src/` with domain-based modules. `build.py` reads each module, deduplicates imports, injects the version from `pyproject.toml`, and emits a single `build/model-optimizer-addon.py`. A GitHub Action on tag push builds and creates a release.

**Tech Stack:** Python 3.11+, Blender Python API (`bpy`), GitHub Actions, ruff (linting/formatting)

---

## Dependency Graph

```
utils.py          (no internal deps)
textures.py       (imports from utils: get_image_users)
materials.py      (imports from textures: get_image_fingerprint)
geometry.py       (imports from utils: get_selected_meshes; uses bake_normal_map which is self-contained)
properties.py     (imports from utils: _tag_3d_redraw)
operators.py      (imports from utils, textures, materials, geometry, properties)
panels.py         (imports from utils, properties)
__init__.py       (imports all modules, provides register/unregister)
```

## Module Contents

### `utils.py`
- `get_image_users(image)` — also used by textures and estimate
- `get_selected_meshes()`
- `count_faces(meshes)`
- `log(context, message, level)`
- `get_config_path()`
- `is_print3d_available()`
- `_tag_3d_redraw(self, context)`
- `estimate_glb_size(meshes, props)`
- `SAVEABLE_PROPS` list
- `save_defaults(props)`
- `load_defaults(props)`
- `export_glb_all(context, props)`
- `generate_lods(context, props)`

### `textures.py`
- `get_image_fingerprint(img)`
- `images_are_identical(img_a, img_b)`
- `clean_images_all(context)` — uses `get_image_users` from utils
- `clean_unused_all(context)`
- `resize_texture_single(img, props)`
- `bake_vertex_colors_single(context, obj)`

### `materials.py`
- `_get_material_signature(mat, threshold)` — uses `get_image_fingerprint` from textures
- `merge_duplicate_materials(context, threshold)`
- `join_meshes_by_material(context, meshes, mode)`

### `geometry.py`
- `fix_geometry_single(context, obj, props)`
- `_bbox_contains(outer_obj, inner_obj)`
- `_remove_interior_loose_parts(context, obj)`
- `_remove_interior_raycast(context, obj)`
- `remove_interior_single(context, obj, props)`
- `detect_and_apply_symmetry(context, obj, ...)`
- `bake_normal_map_for_decimate(context, obj, highpoly, props)`
- `decimate_single(context, obj, props)`

### `properties.py`
- `AIOPT_Properties(PropertyGroup)`
- `AIOPT_PipelineState(PropertyGroup)`

### `operators.py`
- All `AIOPT_OT_*` classes (17 operators total)

### `panels.py`
- All `AIOPT_PT_*` classes (9 panels total)

### `__init__.py`
- `bl_info` dict (placeholder version)
- `classes` tuple
- `_load_defaults_on_file()`
- `register()` / `unregister()`
- `if __name__ == "__main__": register()`

---

## Task 1: Create `src/utils.py`

**Files:**
- Create: `src/utils.py`

**Step 1: Create utils.py with all shared helper functions**

Extract these functions/constants from `model-optimizer-addon.py` lines 55–181 and 1106–1180 and 946–1103:

```python
import json
import math
import os

import bpy
from mathutils import Vector


def get_image_users(image):
    # ... lines 55-63

def get_selected_meshes():
    # ... lines 66-71

def count_faces(meshes):
    # ... lines 74-76

def log(context, message, level="INFO"):
    # ... lines 79-83

def get_config_path():
    # ... lines 86-90

def is_print3d_available():
    # ... lines 93-96

def _tag_3d_redraw(self, context):
    # ... lines 99-103

def estimate_glb_size(meshes, props):
    # ... lines 106-180

# Properties to save/load
SAVEABLE_PROPS = [...]  # lines 1107-1149

def save_defaults(props):
    # ... lines 1152-1160

def load_defaults(props):
    # ... lines 1163-1179

def export_glb_all(context, props):
    # ... lines 946-990

def generate_lods(context, props):
    # ... lines 993-1103
```

Copy each function/constant verbatim from the original file. All external imports needed: `json`, `math`, `os`, `bpy`, `mathutils.Vector`.

Note: `generate_lods` calls `get_selected_meshes` which is in the same file — no import needed.

**Step 2: Run linting**

Run: `ruff check src/utils.py && ruff format src/utils.py`

---

## Task 2: Create `src/textures.py`

**Files:**
- Create: `src/textures.py`

**Step 1: Create textures.py with image/texture functions**

```python
import bpy

from .utils import get_image_users


def get_image_fingerprint(img):
    # ... lines 703-738

def images_are_identical(img_a, img_b):
    # ... lines 741-765

def clean_images_all(context):
    # ... lines 768-839 (calls get_image_users and get_image_fingerprint)

def clean_unused_all(context):
    # ... lines 842-854

def resize_texture_single(img, props):
    # ... lines 857-885 (needs `import math`)

def bake_vertex_colors_single(context, obj):
    # ... lines 888-943
```

Imports needed: `math`, `bpy`, and `from .utils import get_image_users`.

**Step 2: Run linting**

Run: `ruff check src/textures.py && ruff format src/textures.py`

---

## Task 3: Create `src/materials.py`

**Files:**
- Create: `src/materials.py`

**Step 1: Create materials.py with material merging functions**

```python
import bpy

from .textures import get_image_fingerprint


def _get_material_signature(mat, threshold=0.01):
    # ... lines 490-515

def merge_duplicate_materials(context, threshold=0.01):
    # ... lines 518-549

def join_meshes_by_material(context, meshes, mode="BY_MATERIAL"):
    # ... lines 552-597
```

**Step 2: Run linting**

Run: `ruff check src/materials.py && ruff format src/materials.py`

---

## Task 4: Create `src/geometry.py`

**Files:**
- Create: `src/geometry.py`

**Step 1: Create geometry.py with all geometry functions**

```python
import bpy
from mathutils import Vector


def fix_geometry_single(context, obj, props):
    # ... lines 188-243

def _bbox_contains(outer_obj, inner_obj):
    # ... lines 246-273

def _remove_interior_loose_parts(context, obj):
    # ... lines 276-336

def _remove_interior_raycast(context, obj):
    # ... lines 339-403

def remove_interior_single(context, obj, props):
    # ... lines 406-412

def detect_and_apply_symmetry(context, obj, axis="X", threshold=0.001, min_score=0.85):
    # ... lines 415-487

def bake_normal_map_for_decimate(context, obj, highpoly, props):
    # ... lines 600-680

def decimate_single(context, obj, props):
    # ... lines 683-700
```

Note: `_remove_interior_raycast` and `detect_and_apply_symmetry` have local `import bmesh` inside the function body — keep those as-is.

**Step 2: Run linting**

Run: `ruff check src/geometry.py && ruff format src/geometry.py`

---

## Task 5: Create `src/properties.py`

**Files:**
- Create: `src/properties.py`

**Step 1: Create properties.py with both PropertyGroup classes**

```python
import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    StringProperty,
)
from bpy.types import PropertyGroup

from .utils import _tag_3d_redraw


class AIOPT_Properties(PropertyGroup):
    # ... lines 1988-2261 (all property definitions, using _tag_3d_redraw as update callback)

class AIOPT_PipelineState(PropertyGroup):
    # ... lines 2264-2276
```

**Step 2: Run linting**

Run: `ruff check src/properties.py && ruff format src/properties.py`

---

## Task 6: Create `src/operators.py`

**Files:**
- Create: `src/operators.py`

**Step 1: Create operators.py with all operator classes**

```python
import json
import time

import bpy
from bpy.types import Operator

from .geometry import (
    bake_normal_map_for_decimate,
    decimate_single,
    detect_and_apply_symmetry,
    fix_geometry_single,
    remove_interior_single,
)
from .materials import join_meshes_by_material, merge_duplicate_materials
from .textures import (
    bake_vertex_colors_single,
    clean_images_all,
    clean_unused_all,
    resize_texture_single,
)
from .utils import (
    count_faces,
    export_glb_all,
    generate_lods,
    get_selected_meshes,
    load_defaults,
    save_defaults,
    SAVEABLE_PROPS,
)


class AIOPT_OT_fix_geometry(Operator):
    # ... lines 1187-1232

# ... all other operator classes through line 1981
```

Copy all 17 operator classes verbatim. The only change is replacing direct function calls with imported names (which have the same names, so no code changes needed inside the classes).

**Step 2: Run linting**

Run: `ruff check src/operators.py && ruff format src/operators.py`

---

## Task 7: Create `src/panels.py`

**Files:**
- Create: `src/panels.py`

**Step 1: Create panels.py with all panel classes**

```python
import json

import bpy
from bpy.types import Panel

from .utils import (
    count_faces,
    estimate_glb_size,
    get_config_path,
    get_selected_meshes,
    is_print3d_available,
)


class AIOPT_PT_main_panel(Panel):
    # ... lines 2284-2346

# ... all other panel classes through line 2791
```

Copy all 9 panel classes verbatim.

**Step 2: Run linting**

Run: `ruff check src/panels.py && ruff format src/panels.py`

---

## Task 8: Create `src/__init__.py`

**Files:**
- Create: `src/__init__.py`

**Step 1: Create __init__.py with bl_info and registration**

```python
"""
=============================================================
  AI 3D Model Optimizer — Blender Add-on
=============================================================
  HOW TO INSTALL:
    1. Open Blender
    2. Go to Edit → Preferences → Add-ons
    3. Click "Install from Disk" (Blender 4.2+) or "Install..." (older)
    4. Select this .py file
    5. Enable the add-on by checking the box next to it

  HOW TO USE:
    1. Open the sidebar in the 3D Viewport by pressing N
    2. Click the "AI Optimizer" tab
    3. Adjust settings
    4. Click buttons to run individual steps or the full pipeline
=============================================================
"""

bl_info = {
    "name": "AI 3D Model Optimizer",
    "author": "René Voigt, Claude",
    "version": (0, 0, 0),  # Placeholder — build.py injects from pyproject.toml
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > AI Optimizer",
    "description": "Optimize AI-generated 3D models: fix geometry, decimate, clean textures, export compressed GLB",
    "category": "Mesh",
}

import bpy

from .operators import (
    AIOPT_OT_cancel_pipeline,
    AIOPT_OT_clean_images,
    AIOPT_OT_clean_unused,
    AIOPT_OT_decimate,
    AIOPT_OT_dismiss_pipeline,
    AIOPT_OT_export_glb,
    AIOPT_OT_fix_geometry,
    AIOPT_OT_load_defaults,
    AIOPT_OT_remove_interior,
    AIOPT_OT_reset_defaults,
    AIOPT_OT_resize_textures,
    AIOPT_OT_run_all,
    AIOPT_OT_save_defaults,
    AIOPT_OT_show_stats,
    AIOPT_OT_symmetry_mirror,
)
from .panels import (
    AIOPT_PT_decimate_panel,
    AIOPT_PT_export_panel,
    AIOPT_PT_geometry_panel,
    AIOPT_PT_main_panel,
    AIOPT_PT_presets_panel,
    AIOPT_PT_progress_panel,
    AIOPT_PT_remove_interior_panel,
    AIOPT_PT_symmetry_panel,
    AIOPT_PT_textures_panel,
)
from .properties import AIOPT_PipelineState, AIOPT_Properties
from .utils import load_defaults

classes = (
    AIOPT_Properties,
    AIOPT_PipelineState,
    AIOPT_OT_fix_geometry,
    AIOPT_OT_remove_interior,
    AIOPT_OT_symmetry_mirror,
    AIOPT_OT_decimate,
    AIOPT_OT_clean_images,
    AIOPT_OT_clean_unused,
    AIOPT_OT_resize_textures,
    AIOPT_OT_export_glb,
    AIOPT_OT_run_all,
    AIOPT_OT_cancel_pipeline,
    AIOPT_OT_dismiss_pipeline,
    AIOPT_OT_show_stats,
    AIOPT_OT_save_defaults,
    AIOPT_OT_load_defaults,
    AIOPT_OT_reset_defaults,
    AIOPT_PT_main_panel,
    AIOPT_PT_progress_panel,
    AIOPT_PT_geometry_panel,
    AIOPT_PT_remove_interior_panel,
    AIOPT_PT_symmetry_panel,
    AIOPT_PT_decimate_panel,
    AIOPT_PT_textures_panel,
    AIOPT_PT_export_panel,
    AIOPT_PT_presets_panel,
)


def _load_defaults_on_file(dummy):
    """Auto-load saved defaults when a new file is opened."""
    if hasattr(bpy.context, "scene") and hasattr(bpy.context.scene, "ai_optimizer"):
        props = bpy.context.scene.ai_optimizer
        if load_defaults(props):
            print("[AI Model Optimizer] Loaded saved defaults")


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ai_optimizer = bpy.props.PointerProperty(type=AIOPT_Properties)
    bpy.types.WindowManager.ai_optimizer_pipeline = bpy.props.PointerProperty(type=AIOPT_PipelineState)

    if _load_defaults_on_file not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_load_defaults_on_file)
    if _load_defaults_on_file not in bpy.app.handlers.load_factory_startup_post:
        bpy.app.handlers.load_factory_startup_post.append(_load_defaults_on_file)

    if hasattr(bpy.context, "scene") and bpy.context.scene is not None:
        load_defaults(bpy.context.scene.ai_optimizer)

    print("[AI Model Optimizer] Add-on registered")


def unregister():
    if _load_defaults_on_file in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_defaults_on_file)
    if _load_defaults_on_file in bpy.app.handlers.load_factory_startup_post:
        bpy.app.handlers.load_factory_startup_post.remove(_load_defaults_on_file)

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.WindowManager.ai_optimizer_pipeline
    del bpy.types.Scene.ai_optimizer
    print("[AI Model Optimizer] Add-on unregistered")


if __name__ == "__main__":
    register()
```

**Step 2: Run linting**

Run: `ruff check src/__init__.py && ruff format src/__init__.py`

---

## Task 9: Delete old single file

**Files:**
- Delete: `src/model-optimizer-addon.py`

**Step 1: Remove the original monolithic file**

Run: `git rm src/model-optimizer-addon.py`

**Step 2: Run linting on entire src/**

Run: `ruff check src/ && ruff format src/`

Fix any import issues or linting errors.

---

## Task 10: Create `build.py`

**Files:**
- Create: `build.py`

**Step 1: Create the build script**

The build script must:
1. Read version from `pyproject.toml` (parse `version = "X.Y.Z"`)
2. Read each source module in dependency order
3. Extract and deduplicate all imports
4. Strip relative imports (`from .xxx import ...`)
5. Emit a single `.py` file to `build/model-optimizer-addon.py`

```python
#!/usr/bin/env python3
"""Build script: concatenates the multi-file addon into a single installable .py file."""

import os
import re
import sys

# --- Configuration ---

SRC_DIR = os.path.join(os.path.dirname(__file__), "src")
BUILD_DIR = os.path.join(os.path.dirname(__file__), "build")
OUTPUT_FILE = os.path.join(BUILD_DIR, "model-optimizer-addon.py")

# Module order: dependencies must come before dependents
MODULE_ORDER = [
    "utils",
    "textures",
    "materials",
    "geometry",
    "properties",
    "operators",
    "panels",
    "__init__",
]

# Header docstring for the built file
HEADER = '''\
"""
=============================================================
  AI 3D Model Optimizer — Blender Add-on
=============================================================
  HOW TO INSTALL:
    1. Open Blender
    2. Go to Edit → Preferences → Add-ons
    3. Click "Install from Disk" (Blender 4.2+) or "Install..." (older)
    4. Select this .py file
    5. Enable the add-on by checking the box next to it

  HOW TO USE:
    1. Open the sidebar in the 3D Viewport by pressing N
    2. Click the "AI Optimizer" tab
    3. Adjust settings
    4. Click buttons to run individual steps or the full pipeline
=============================================================
"""
'''


def read_version():
    """Read version string from pyproject.toml."""
    pyproject = os.path.join(os.path.dirname(__file__), "pyproject.toml")
    with open(pyproject) as f:
        for line in f:
            m = re.match(r'^version\s*=\s*"([^"]+)"', line)
            if m:
                return m.group(1)
    print("ERROR: Could not find version in pyproject.toml", file=sys.stderr)
    sys.exit(1)


def version_to_tuple_str(version_str):
    """Convert '1.5.0' to '(1, 5, 0)'."""
    parts = version_str.split(".")
    # Pad to 3 parts (Blender expects a 3-tuple)
    while len(parts) < 3:
        parts.append("0")
    return f"({', '.join(parts[:3])})"


def parse_module(filepath):
    """Parse a module file into imports and body lines."""
    with open(filepath) as f:
        lines = f.readlines()

    imports = []
    body = []
    in_docstring = False
    docstring_char = None

    for line in lines:
        stripped = line.strip()

        # Skip module-level docstrings
        if not in_docstring and (stripped.startswith('"""') or stripped.startswith("'''")):
            docstring_char = stripped[:3]
            if stripped.count(docstring_char) >= 2:
                # Single-line docstring
                continue
            in_docstring = True
            continue
        if in_docstring:
            if docstring_char in stripped:
                in_docstring = False
            continue

        # Skip relative imports (from .xxx import ...)
        if re.match(r"^from\s+\.", stripped):
            continue

        # Collect external imports
        if re.match(r"^(import |from (?!\.))", stripped):
            imports.append(line)
            continue

        body.append(line)

    return imports, body


def deduplicate_imports(all_imports):
    """Deduplicate and sort imports."""
    seen = set()
    unique = []
    for imp in all_imports:
        key = imp.strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(key)

    # Sort: stdlib first, then third-party (bpy, mathutils)
    stdlib = []
    thirdparty = []
    for imp in unique:
        if any(imp.startswith(f"import {m}") or imp.startswith(f"from {m}") for m in
               ["json", "math", "os", "time"]):
            stdlib.append(imp)
        else:
            thirdparty.append(imp)

    stdlib.sort()
    thirdparty.sort()

    result = []
    if stdlib:
        result.extend(stdlib)
    if stdlib and thirdparty:
        result.append("")
    if thirdparty:
        result.extend(thirdparty)

    return [line + "\n" for line in result]


def build():
    version = read_version()
    version_tuple = version_to_tuple_str(version)

    print(f"Building addon v{version} ...")

    os.makedirs(BUILD_DIR, exist_ok=True)

    all_imports = []
    all_body = []

    for module_name in MODULE_ORDER:
        filepath = os.path.join(SRC_DIR, f"{module_name}.py")
        if not os.path.exists(filepath):
            print(f"  ERROR: {filepath} not found", file=sys.stderr)
            sys.exit(1)

        imports, body = parse_module(filepath)
        all_imports.extend(imports)
        all_body.extend(body)
        print(f"  Processed: {module_name}.py")

    # Build the output
    merged_imports = deduplicate_imports(all_imports)

    with open(OUTPUT_FILE, "w", newline="\n") as f:
        # Header docstring
        f.write(HEADER)
        f.write("\n")

        # bl_info with correct version
        f.write("bl_info = {\n")
        f.write('    "name": "AI 3D Model Optimizer",\n')
        f.write('    "author": "René Voigt, Claude",\n')
        f.write(f'    "version": {version_tuple},\n')
        f.write('    "blender": (4, 0, 0),\n')
        f.write('    "location": "View3D > Sidebar > AI Optimizer",\n')
        f.write('    "description": "Optimize AI-generated 3D models: fix geometry, '
                'decimate, clean textures, export compressed GLB",\n')
        f.write('    "category": "Mesh",\n')
        f.write("}\n\n")

        # Imports
        f.writelines(merged_imports)
        f.write("\n")

        # Body (strip the bl_info block from __init__.py body)
        skip_bl_info = False
        for line in all_body:
            stripped = line.strip()
            if stripped.startswith("bl_info"):
                skip_bl_info = True
                continue
            if skip_bl_info:
                if stripped == "}":
                    skip_bl_info = False
                continue
            f.write(line)

    print(f"  Output: {OUTPUT_FILE}")
    print("Done.")


if __name__ == "__main__":
    build()
```

**Step 2: Test the build**

Run: `python build.py`
Expected: `build/model-optimizer-addon.py` is created with correct version.

**Step 3: Verify the built file passes linting**

Run: `ruff check build/model-optimizer-addon.py`

**Step 4: Verify the built file has correct structure**

Check that:
- The file starts with the docstring header
- `bl_info` has the version from `pyproject.toml`
- All imports are at the top (after bl_info)
- `register()` and `unregister()` are present
- No `from .xxx` relative imports remain

Run: `grep "from \." build/model-optimizer-addon.py` — should return nothing.
Run: `grep "bl_info" build/model-optimizer-addon.py` — should show version tuple.

---

## Task 11: Update `.gitignore`

**Files:**
- Modify: `.gitignore`

**Step 1: Add build directory to gitignore**

Add:
```
build/
```

---

## Task 12: Create GitHub Actions release workflow

**Files:**
- Create: `.github/workflows/release.yml`

**Step 1: Create the workflow file**

```yaml
name: Release

on:
  push:
    tags:
      - 'v*'

permissions:
  contents: write

jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Validate tag matches version
        run: |
          TAG_VERSION="${GITHUB_REF_NAME#v}"
          TOML_VERSION=$(python -c "
          import re
          with open('pyproject.toml') as f:
              for line in f:
                  m = re.match(r'^version\s*=\s*\"([^\"]+)\"', line)
                  if m:
                      print(m.group(1))
                      break
          ")
          if [ "$TAG_VERSION" != "$TOML_VERSION" ]; then
            echo "ERROR: Tag version ($TAG_VERSION) does not match pyproject.toml ($TOML_VERSION)"
            exit 1
          fi
          echo "Version: $TAG_VERSION"

      - name: Lint
        run: |
          pip install ruff
          ruff check src/

      - name: Build
        run: python build.py

      - name: Create Release
        uses: softprops/action-gh-release@v2
        with:
          generate_release_notes: true
          files: build/model-optimizer-addon.py
```

---

## Task 13: Update project docs

**Files:**
- Modify: `.claude/CLAUDE.md`

**Step 1: Update CLAUDE.md to reflect multi-file structure**

Update the Structure section to list the new modules, and add build instructions:

```markdown
## Structure

- `src/__init__.py` — bl_info, register/unregister
- `src/operators.py` — All AIOPT_OT_* operator classes
- `src/panels.py` — All AIOPT_PT_* panel classes
- `src/properties.py` — Property groups (AIOPT_Properties, AIOPT_PipelineState)
- `src/geometry.py` — Geometry fixing, decimation, interior removal, symmetry
- `src/textures.py` — Image cleanup, resizing, fingerprinting, vertex color baking
- `src/materials.py` — Material merging, mesh joining
- `src/utils.py` — Shared helpers (logging, config, mesh selection, size estimation, export)
- `build.py` — Build script to produce single-file addon

## Build

- Run `python build.py` to produce `build/model-optimizer-addon.py`
- The build reads version from `pyproject.toml` and injects it into `bl_info`
- Install the built file in Blender: Edit → Preferences → Add-ons → Install from Disk

## Releasing

- Update version in `pyproject.toml`
- Commit and tag: `git tag v1.5.0 && git push --tags`
- GitHub Action builds and creates a release with the addon `.py` attached
```

---

## Task 14: Final verification

**Step 1: Run full lint**

Run: `ruff check src/ && ruff format --check src/`

**Step 2: Run build and verify output**

Run: `python build.py && ruff check build/model-optimizer-addon.py`

**Step 3: Verify no relative imports leaked into build**

Run: `grep -n "from \." build/model-optimizer-addon.py`
Expected: no output

**Step 4: Verify version is correct**

Run: `grep "version" build/model-optimizer-addon.py | head -1`
Expected: shows version tuple matching pyproject.toml
