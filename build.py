"""Build script: packages blender_model_optimizer/ into a Blender extension .zip.

Output: build/blender_model_optimizer-<version>.zip

The ZIP contains, at its root:
  - blender_manifest.toml (with version rewritten from pyproject.toml)
  - LICENSE
  - the source files from blender_model_optimizer/ (__init__.py, operators.py, etc.),
    flat at the zip root. Blender extracts into extensions/<repo>/<id>/, so the
    package is installed as <id>/__init__.py. __pycache__ and *.pyc are excluded.

Pure stdlib — no Blender install required. The result is byte-compatible with
`blender --command extension build`. Use `scripts/validate.ps1` (or .sh) to
run `blender --command extension validate` locally before tagging a release.

Usage:
    python build.py
"""

import importlib.util
import re
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PKG_DIR = ROOT / "blender_model_optimizer"
MANIFEST_SRC = ROOT / "blender_manifest.toml"
LICENSE_SRC = ROOT / "LICENSE"
BUILD_DIR = ROOT / "build"

EXCLUDE_DIR_NAMES = {"__pycache__"}
EXCLUDE_SUFFIXES = {".pyc"}


def read_version() -> str:
    """Return the project version from pyproject.toml."""
    with open(ROOT / "pyproject.toml", "rb") as f:
        return tomllib.load(f)["project"]["version"]


def inject_version(manifest_text: str, version: str) -> str:
    """Rewrite the first `version = "..."` line in the manifest."""
    new_text, n = re.subn(
        r'^version\s*=\s*"[^"]*"',
        f'version = "{version}"',
        manifest_text,
        count=1,
        flags=re.MULTILINE,
    )
    if n != 1:
        raise RuntimeError('blender_manifest.toml is missing a top-level `version = "..."` line')
    return new_text


def _iter_package_files(pkg_dir: Path):
    """Yield files under pkg_dir, skipping __pycache__ and *.pyc."""
    for p in sorted(pkg_dir.rglob("*")):
        if not p.is_file():
            continue
        if any(part in EXCLUDE_DIR_NAMES for part in p.relative_to(pkg_dir).parts):
            continue
        if p.suffix in EXCLUDE_SUFFIXES:
            continue
        yield p


def build() -> Path:
    """Build the extension ZIP and return its path."""
    if not PKG_DIR.is_dir():
        raise RuntimeError(f"package directory not found: {PKG_DIR}")
    if not MANIFEST_SRC.is_file():
        raise RuntimeError(f"manifest not found: {MANIFEST_SRC}")
    if not LICENSE_SRC.is_file():
        raise RuntimeError(f"LICENSE not found: {LICENSE_SRC}")

    version = read_version()
    BUILD_DIR.mkdir(exist_ok=True)
    out = BUILD_DIR / f"blender_model_optimizer-{version}.zip"
    if out.exists():
        out.unlink()

    manifest_text = MANIFEST_SRC.read_text(encoding="utf-8")
    manifest_text = inject_version(manifest_text, version)

    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("blender_manifest.toml", manifest_text)
        z.write(LICENSE_SRC, "LICENSE")
        for p in _iter_package_files(PKG_DIR):
            # Package files go at the zip root (not nested under blender_model_optimizer/).
            # Blender extracts the zip into extensions/<repo>/<id>/, so the install
            # path becomes .../<id>/__init__.py. Nesting inside the zip would produce
            # .../<id>/<id>/__init__.py and the outer dir would be a namespace package
            # that Blender can't import.
            arcname = p.relative_to(PKG_DIR).as_posix()
            z.write(p, arcname)

    return out


def run_tests() -> None:
    """Run the test suite if pytest is available. Aborts the build on failure.

    Tests are dev-only — a missing pytest is a warning, not an error. CI always
    has pytest; local devs who install it get early feedback.
    """
    tests_dir = ROOT / "tests"
    if not tests_dir.is_dir():
        return
    if importlib.util.find_spec("pytest") is None:
        print("  [warn] pytest not installed — skipping tests. Install with: pip install pytest")
        return
    print("Running tests...", flush=True)
    result = subprocess.run([sys.executable, "-m", "pytest", str(tests_dir), "-q"], cwd=ROOT)
    if result.returncode != 0:
        print("ERROR: tests failed — build aborted", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    run_tests()
    path = build()
    size = path.stat().st_size
    print(f"Built: {path} ({size:,} bytes)")
