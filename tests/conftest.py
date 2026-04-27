"""Shared test setup.

The add-on source imports ``bpy`` and ``mathutils`` at module scope, and
``blender_model_optimizer/__init__.py`` registers operators/panels at import time. These tests
target pure Python logic (JSON round-trip, migrations, config drift) and
should not require Blender — so we stub the Blender modules and load
``utils.py`` directly, bypassing the package's ``__init__``.
"""

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

# Stub Blender modules that blender_model_optimizer/utils.py imports at module scope.
sys.modules.setdefault("bpy", MagicMock())
sys.modules.setdefault("bpy.props", MagicMock())
sys.modules.setdefault("bpy.types", MagicMock())
sys.modules.setdefault("mathutils", MagicMock())

ROOT = Path(__file__).resolve().parent.parent

# Register a synthetic "blender_model_optimizer" package without running blender_model_optimizer/__init__.py (which
# would pull in operators, panels, geometry — none of which these tests need).
_src_pkg = types.ModuleType("blender_model_optimizer")
_src_pkg.__path__ = [str(ROOT / "blender_model_optimizer")]
sys.modules["blender_model_optimizer"] = _src_pkg

# Load blender_model_optimizer/utils.py as blender_model_optimizer.utils.
_spec = importlib.util.spec_from_file_location("blender_model_optimizer.utils", ROOT / "blender_model_optimizer" / "utils.py")
assert _spec is not None and _spec.loader is not None
_utils = importlib.util.module_from_spec(_spec)
sys.modules["blender_model_optimizer.utils"] = _utils
_spec.loader.exec_module(_utils)
