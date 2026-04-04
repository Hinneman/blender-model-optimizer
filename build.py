"""Build script: concatenates the multi-file src/ package into a single
installable Blender add-on .py file at build/model-optimizer-addon.py.

Usage:
    python build.py
"""

import os
import re
import sys

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

# Source modules in dependency order (everything except __init__.py first)
MODULE_ORDER = [
    "utils.py",
    "textures.py",
    "materials.py",
    "geometry.py",
    "properties.py",
    "operators.py",
    "panels.py",
]

INIT_MODULE = "__init__.py"

SRC_DIR = os.path.join(PROJECT_ROOT, "src")
BUILD_DIR = os.path.join(PROJECT_ROOT, "build")
OUTPUT_FILE = os.path.join(BUILD_DIR, "model-optimizer-addon.py")

# Imports that belong to the "third-party / Blender" group
BLENDER_MODULES = {"bpy", "bmesh", "mathutils", "gpu", "gpu_extras", "bl_math", "idprop", "aud", "bgl"}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def read_version():
    """Parse version = "X.Y.Z" from pyproject.toml and return as tuple string."""
    pyproject = os.path.join(PROJECT_ROOT, "pyproject.toml")
    with open(pyproject) as f:
        for line in f:
            m = re.match(r'^version\s*=\s*"(\d+)\.(\d+)\.(\d+)"', line)
            if m:
                return f"({m.group(1)}, {m.group(2)}, {m.group(3)})"
    print("ERROR: Could not find version in pyproject.toml", file=sys.stderr)
    sys.exit(1)


def read_file(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def strip_leading_docstring(source):
    """Remove a leading triple-quoted docstring and return the rest."""
    stripped = source.lstrip()
    for quote in ('"""', "'''"):
        if stripped.startswith(quote):
            end = stripped.find(quote, 3)
            if end != -1:
                return stripped[end + 3 :].lstrip("\n")
    return source


def extract_leading_docstring(source):
    """Return the leading triple-quoted docstring, or empty string."""
    stripped = source.lstrip()
    for quote in ('"""', "'''"):
        if stripped.startswith(quote):
            end = stripped.find(quote, 3)
            if end != -1:
                return stripped[: end + 3]
    return ""


def extract_bl_info(source):
    """Extract the bl_info = { ... } block from source."""
    pattern = re.compile(r"^(bl_info\s*=\s*\{.*?\})", re.MULTILINE | re.DOTALL)
    m = pattern.search(source)
    return m.group(1) if m else ""


def classify_import(top_module):
    """Return 'blender' or 'stdlib' for a top-level module name."""
    if top_module in BLENDER_MODULES:
        return "blender"
    return "stdlib"


def get_top_module(line):
    """Extract the top-level module name from an import line."""
    m = re.match(r"^\s*(?:from|import)\s+([\w.]+)", line)
    if m:
        return m.group(1).split(".")[0]
    return None


def process_module(source):
    """Process a single module's source.

    Returns (import_blocks, body) where import_blocks is a list of
    complete import statements (single or multi-line) and body is the
    remaining code with all imports removed.
    """
    # Strip leading docstring
    source = strip_leading_docstring(source)
    # Strip bl_info
    source = re.sub(r"^bl_info\s*=\s*\{.*?\}\s*\n", "", source, flags=re.MULTILINE | re.DOTALL)

    lines = source.split("\n")
    import_blocks = []
    body_lines = []
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Check if this is an import line
        if stripped.startswith("import ") or stripped.startswith("from "):
            # Check if relative import
            is_relative = bool(re.match(r"^from\s+\.", stripped))

            # Check if multi-line import (has open paren without close)
            if "(" in stripped and ")" not in stripped:
                # Collect all lines until closing paren
                block_lines = [line]
                i += 1
                while i < len(lines) and ")" not in lines[i]:
                    block_lines.append(lines[i])
                    i += 1
                if i < len(lines):
                    block_lines.append(lines[i])
                    i += 1

                if not is_relative:
                    import_blocks.append("\n".join(block_lines))
                continue
            else:
                if not is_relative:
                    import_blocks.append(line)
                i += 1
                continue

        body_lines.append(line)
        i += 1

    return import_blocks, "\n".join(body_lines)


def normalize_import_block(block):
    """Create a normalized key for deduplication of import blocks."""
    # For multi-line blocks, normalize whitespace for comparison
    lines = block.split("\n")
    return "\n".join(line.strip() for line in lines)


def merge_from_imports(blocks):
    """Merge `from X import A` and `from X import B` into `from X import A, B`.

    Plain `import X` statements are left as-is. Multi-line `from X import (...)`
    blocks are expanded so all names from the same source module are combined.
    """
    # Separate plain imports from from-imports
    plain = []  # `import X` lines
    from_map = {}  # module_path -> set of imported names

    for block in blocks:
        norm = normalize_import_block(block)
        # Match `from X.Y import (A, B, C)` or `from X import A`
        m = re.match(r"^from\s+([\w.]+)\s+import\s+(.+)$", norm, re.DOTALL)
        if m:
            module = m.group(1)
            names_str = m.group(2).strip()
            # Handle parenthesised multi-line form
            if names_str.startswith("("):
                names_str = names_str.strip("()")
            names = [n.strip().rstrip(",") for n in re.split(r"[,\n]", names_str) if n.strip().rstrip(",")]
            if module not in from_map:
                from_map[module] = []
            for n in names:
                if n and n not in from_map[module]:
                    from_map[module].append(n)
        elif norm.startswith("import "):
            if norm not in plain:
                plain.append(norm)

    # Rebuild from-import blocks
    from_blocks = []
    for module, names in sorted(from_map.items()):
        names_sorted = sorted(names)
        if len(names_sorted) <= 3:
            from_blocks.append(f"from {module} import {', '.join(names_sorted)}")
        else:
            inner = ",\n".join(f"    {n}" for n in names_sorted)
            from_blocks.append(f"from {module} import (\n{inner},\n)")

    return plain, from_blocks


def deduplicate_and_sort_imports(all_blocks):
    """Deduplicate import blocks and sort into stdlib + blender groups."""
    seen = set()
    stdlib_raw = []
    blender_raw = []

    for block in all_blocks:
        norm = normalize_import_block(block)
        if norm in seen:
            continue
        seen.add(norm)

        top = get_top_module(block)
        if top and classify_import(top) == "blender":
            blender_raw.append(block)
        else:
            stdlib_raw.append(block)

    # Merge from-imports within each group
    stdlib_plain, stdlib_from = merge_from_imports(stdlib_raw)
    blender_plain, blender_from = merge_from_imports(blender_raw)

    # Sort each sub-list
    stdlib_plain.sort()
    stdlib_from.sort()
    blender_plain.sort()
    blender_from.sort()

    # Combine: plain imports first, then from-imports
    stdlib = stdlib_plain + stdlib_from
    blender = blender_plain + blender_from

    return stdlib, blender


def format_import_block(block):
    """Ensure multi-line import blocks have consistent indentation."""
    lines = block.split("\n")
    if len(lines) == 1:
        return lines[0].strip()

    # First line: strip leading whitespace
    result = [lines[0].strip()]
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == ")":
            result.append(")")
        elif stripped:
            result.append(f"    {stripped}")
        else:
            result.append("")
    return "\n".join(result)


# ---------------------------------------------------------------------------
# Main build
# ---------------------------------------------------------------------------


def build():
    version_tuple = read_version()
    print(f"Building with version {version_tuple}")

    os.makedirs(BUILD_DIR, exist_ok=True)

    # Read __init__.py
    init_source = read_file(os.path.join(SRC_DIR, INIT_MODULE))
    docstring = extract_leading_docstring(init_source)
    bl_info_block = extract_bl_info(init_source)

    # Inject version
    bl_info_block = re.sub(
        r'"version":\s*\([^)]*\)',
        f'"version": {version_tuple}',
        bl_info_block,
    )

    # Process all modules
    all_imports = []
    all_bodies = []

    for module_file in MODULE_ORDER:
        path = os.path.join(SRC_DIR, module_file)
        source = read_file(path)
        imports, body = process_module(source)
        all_imports.extend(imports)

        body = body.strip()
        if body:
            header = f"# {'=' * 70}\n# {module_file}\n# {'=' * 70}"
            all_bodies.append(f"\n\n{header}\n\n{body}")

    # Process __init__.py
    imports, body = process_module(init_source)
    all_imports.extend(imports)

    body = body.strip()
    if body:
        header = f"# {'=' * 70}\n# __init__.py\n# {'=' * 70}"
        all_bodies.append(f"\n\n{header}\n\n{body}")

    # Deduplicate and sort imports
    stdlib, blender = deduplicate_and_sort_imports(all_imports)

    # Assemble output
    parts = []
    parts.append(docstring)
    parts.append("")
    parts.append(bl_info_block)
    parts.append("")

    if stdlib:
        parts.append("\n".join(stdlib))
    if stdlib and blender:
        parts.append("")
    if blender:
        parts.append("\n".join(blender))

    for body in all_bodies:
        parts.append(body)

    output = "\n".join(parts)
    # Collapse triple+ blank lines to double blank lines
    output = re.sub(r"\n{3,}", "\n\n", output)
    output = output.rstrip("\n") + "\n"

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(output)

    print(f"Built: {OUTPUT_FILE}")
    print(f"Size: {os.path.getsize(OUTPUT_FILE):,} bytes")


if __name__ == "__main__":
    build()
