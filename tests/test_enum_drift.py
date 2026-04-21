"""Enum-value drift test.

Every ``EnumProperty`` on ``AIOPT_Properties`` declares a fixed set of
choice IDs. The rest of the codebase compares ``props.some_enum`` against
string literals to branch behavior. If a choice is renamed in
``properties.py`` but the operator/geometry code still compares against
the old string, the comparison silently never matches and that code path
goes dead — no crash, no warning.

This test:
  1. Extracts {enum_prop_name: {valid_choice_ids}} from ``properties.py``.
  2. Walks every ``ai_model_optimizer/*.py`` AST for comparisons where one side is
     ``<anything>.<enum_prop_name>`` and the other side is a string
     literal (directly, or inside a tuple/list/set for ``in`` checks).
  3. Asserts every such literal is a declared choice.
"""

import ast
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "ai_model_optimizer"
PROPERTIES_FILE = SRC_DIR / "properties.py"


def _enum_choices() -> dict[str, set[str]]:
    """Map each EnumProperty's attribute name to its set of choice IDs.

    Enums whose ``items=`` is not a simple literal list of string tuples
    are skipped — we can only validate statically-known choices.
    """
    tree = ast.parse(PROPERTIES_FILE.read_text())
    result: dict[str, set[str]] = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != "AIOPT_Properties":
            continue
        for stmt in node.body:
            if not (isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)):
                continue
            if not (
                isinstance(stmt.annotation, ast.Call)
                and isinstance(stmt.annotation.func, ast.Name)
                and stmt.annotation.func.id == "EnumProperty"
            ):
                continue
            items_kw = next((k for k in stmt.annotation.keywords if k.arg == "items"), None)
            if items_kw is None or not isinstance(items_kw.value, ast.List):
                continue
            choices: set[str] = set()
            for elt in items_kw.value.elts:
                if (
                    isinstance(elt, ast.Tuple)
                    and elt.elts
                    and isinstance(elt.elts[0], ast.Constant)
                    and isinstance(elt.elts[0].value, str)
                ):
                    choices.add(elt.elts[0].value)
            if choices:
                result[stmt.target.id] = choices
    return result


def _literals_on_other_side(compare: ast.Compare, attr_node: ast.Attribute) -> list[tuple[str, int]]:
    """Collect (literal_value, lineno) pairs from operands other than the enum attribute."""
    literals: list[tuple[str, int]] = []
    for operand in [compare.left, *compare.comparators]:
        if operand is attr_node:
            continue
        if isinstance(operand, ast.Constant) and isinstance(operand.value, str):
            literals.append((operand.value, operand.lineno))
        elif isinstance(operand, (ast.Tuple, ast.List, ast.Set)):
            for elt in operand.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    literals.append((elt.value, elt.lineno))
    return literals


def test_all_enum_comparisons_use_declared_choices():
    enums = _enum_choices()
    assert enums, "no EnumProperty declarations found — extractor is broken"

    errors: list[str] = []

    for path in sorted(SRC_DIR.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            operands = [node.left, *node.comparators]
            attr = next(
                (o for o in operands if isinstance(o, ast.Attribute) and o.attr in enums),
                None,
            )
            if attr is None:
                continue
            valid = enums[attr.attr]
            for literal, lineno in _literals_on_other_side(node, attr):
                if literal not in valid:
                    errors.append(
                        f"{path.name}:{lineno}: {attr.attr} == {literal!r}, "
                        f"but valid choices are {sorted(valid)}"
                    )

    assert not errors, "enum-value drift detected:\n  " + "\n  ".join(errors)


def test_detector_catches_synthetic_drift(tmp_path, monkeypatch):
    """Sanity check: if someone writes ``props.manifold_method == "BOGUS"``,
    the walker must flag it. Protects against a future refactor that
    silently disables the scan (e.g. by narrowing the Attribute check)."""
    fake = tmp_path / "bad.py"
    fake.write_text('def f(props):\n    if props.manifold_method == "BOGUS":\n        pass\n')

    enums = _enum_choices()
    tree = ast.parse(fake.read_text())
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        operands = [node.left, *node.comparators]
        attr = next((o for o in operands if isinstance(o, ast.Attribute) and o.attr in enums), None)
        if attr is None:
            continue
        for literal, _ in _literals_on_other_side(node, attr):
            if literal not in enums[attr.attr]:
                found.append(literal)
    assert found == ["BOGUS"]


def test_extractor_found_every_expected_enum():
    # Guard against the extractor silently returning {}. If properties.py
    # is restructured in a way that breaks the walker, we want to know.
    enums = _enum_choices()
    expected = {
        "manifold_method",
        "join_mode",
        "interior_method",
        "symmetry_axis",
        "normal_map_resolution",
        "resize_mode",
        "image_format",
        "analysis_target_preset",
    }
    missing = expected - enums.keys()
    assert not missing, f"extractor missed these enums: {sorted(missing)}"
