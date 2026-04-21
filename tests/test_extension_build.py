"""Smoke tests for the extension-format build.py.

Ensures the produced .zip has the correct structure, valid manifest,
version injected from pyproject.toml, and excludes __pycache__/*.pyc.
"""

import ast
import importlib.util
import sys
import tomllib
import zipfile
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
def built_zip(tmp_path, build_module, monkeypatch):
    """Run build() into tmp_path and return the generated ZIP path."""
    monkeypatch.setattr(build_module, "BUILD_DIR", tmp_path)
    path = build_module.build()
    assert path.exists()
    return path


def _pyproject_version() -> str:
    with open(ROOT / "pyproject.toml", "rb") as f:
        return tomllib.load(f)["project"]["version"]


def test_zip_filename_includes_version(built_zip):
    version = _pyproject_version()
    assert built_zip.name == f"ai_model_optimizer-{version}.zip"


def test_zip_contains_manifest(built_zip):
    with zipfile.ZipFile(built_zip) as z:
        assert "blender_manifest.toml" in z.namelist()


def test_zip_contains_license(built_zip):
    with zipfile.ZipFile(built_zip) as z:
        assert "LICENSE" in z.namelist()


def test_zip_contains_package_init(built_zip):
    with zipfile.ZipFile(built_zip) as z:
        assert "__init__.py" in z.namelist()


def test_zip_has_all_source_modules_at_root(built_zip):
    """Blender unpacks the zip into `extensions/<repo>/<id>/`, so source files
    must live at the zip root. A nested `ai_model_optimizer/...` path would
    install as `<id>/<id>/...`, making the outer dir a namespace package that
    Blender can't import ("module loaded with no associated file")."""
    expected = {
        "__init__.py",
        "geometry.py",
        "materials.py",
        "operators.py",
        "panels.py",
        "properties.py",
        "textures.py",
        "utils.py",
    }
    with zipfile.ZipFile(built_zip) as z:
        names = set(z.namelist())
    missing = expected - names
    assert not missing, f"missing at zip root: {missing}"
    nested = {n for n in names if n.startswith("ai_model_optimizer/")}
    assert not nested, f"source files should be at zip root, not nested: {nested}"


def test_manifest_version_matches_pyproject(built_zip):
    version = _pyproject_version()
    with zipfile.ZipFile(built_zip) as z:
        manifest_bytes = z.read("blender_manifest.toml")
    data = tomllib.loads(manifest_bytes.decode("utf-8"))
    assert data["version"] == version


def test_manifest_required_fields(built_zip):
    with zipfile.ZipFile(built_zip) as z:
        manifest_bytes = z.read("blender_manifest.toml")
    data = tomllib.loads(manifest_bytes.decode("utf-8"))
    assert data["schema_version"] == "1.0.0"
    assert data["id"] == "ai_model_optimizer"
    assert data["type"] == "add-on"
    assert data["blender_version_min"] == "4.2.0"
    assert data["license"] == ["SPDX:MIT"]
    assert "files" in data["permissions"]


def test_zip_excludes_pycache(built_zip):
    with zipfile.ZipFile(built_zip) as z:
        names = z.namelist()
    assert not any("__pycache__" in n for n in names), f"__pycache__ leaked into zip: {names}"
    assert not any(n.endswith(".pyc") for n in names), f".pyc leaked into zip: {names}"


def test_init_py_has_no_bl_info(built_zip):
    with zipfile.ZipFile(built_zip) as z:
        init_src = z.read("__init__.py").decode("utf-8")
    assert "bl_info" not in init_src, "bl_info dict must be removed in favor of blender_manifest.toml"


def test_init_py_is_valid_python(built_zip):
    with zipfile.ZipFile(built_zip) as z:
        init_src = z.read("__init__.py").decode("utf-8")
    ast.parse(init_src)


def test_init_py_defines_register_and_unregister(built_zip):
    with zipfile.ZipFile(built_zip) as z:
        init_src = z.read("__init__.py").decode("utf-8")
    tree = ast.parse(init_src)
    funcs = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "register" in funcs
    assert "unregister" in funcs


def test_tagline_within_platform_limit():
    """extensions.blender.org rejects taglines longer than 64 characters."""
    with open(ROOT / "blender_manifest.toml", "rb") as f:
        data = tomllib.load(f)
    assert len(data["tagline"]) <= 64
