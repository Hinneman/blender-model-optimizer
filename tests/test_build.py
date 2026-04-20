"""Smoke tests for build.py.

Ensures the single-file add-on build still produces something Blender
can actually load: syntactically valid Python, bl_info present, version
injected from pyproject.toml, and no lingering relative imports (which
break when the addon is installed as a single file).
"""

import ast
import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def build_module():
    """Load build.py as a module without colliding with the build/ dir."""
    spec = importlib.util.spec_from_file_location("build_script", ROOT / "build.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["build_script"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def built_output(tmp_path, build_module, monkeypatch):
    """Run the build into tmp_path and return the generated source text."""
    output = tmp_path / "model-optimizer-addon.py"
    monkeypatch.setattr(build_module, "BUILD_DIR", str(tmp_path))
    monkeypatch.setattr(build_module, "OUTPUT_FILE", str(output))
    build_module.build()
    assert output.exists()
    return output.read_text(encoding="utf-8")


def _pyproject_version() -> tuple[int, int, int]:
    text = (ROOT / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"(\d+)\.(\d+)\.(\d+)"', text, re.MULTILINE)
    assert m, "could not find version in pyproject.toml"
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def test_output_is_syntactically_valid_python(built_output):
    ast.parse(built_output)


def test_output_contains_bl_info(built_output):
    assert "bl_info = {" in built_output


def test_version_injected_from_pyproject(built_output):
    major, minor, patch = _pyproject_version()
    assert f'"version": ({major}, {minor}, {patch})' in built_output


def test_no_relative_imports_in_output(built_output):
    # The single-file build must not contain any `from .foo import ...` —
    # those only work inside a package, and the installed addon is one file.
    tree = ast.parse(built_output)
    relative = [
        node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.level and node.level > 0
    ]
    assert not relative, f"relative imports leaked into build: {[n.module for n in relative]}"


def test_register_and_unregister_are_defined(built_output):
    # Blender calls these at addon enable/disable time.
    tree = ast.parse(built_output)
    funcs = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    assert "register" in funcs
    assert "unregister" in funcs
