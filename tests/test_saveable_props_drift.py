"""Drift test: every annotated field on AIOPT_Properties must be accounted for.

Catches the bug where someone adds a property but forgets to register it
in ``SAVEABLE_PROPS``. The alternative — discovering the omission only
after a user reports that their saved default isn't being loaded — is
exactly the failure mode this project has seen before.

Uses ``ast`` so the test runs without importing Blender.
"""

import ast
from pathlib import Path

from src.utils import SAVEABLE_PROPS

PROPERTIES_FILE = Path(__file__).resolve().parent.parent / "src" / "properties.py"

# Explicitly not persisted. If you add something here, say why.
NON_SAVEABLE = set()


def _annotated_names(class_name: str) -> set[str]:
    tree = ast.parse(PROPERTIES_FILE.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                stmt.target.id
                for stmt in node.body
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
            }
    raise AssertionError(f"class {class_name} not found in {PROPERTIES_FILE}")


def test_every_aiopt_property_is_saveable_or_explicitly_excluded():
    declared = _annotated_names("AIOPT_Properties")
    accounted_for = set(SAVEABLE_PROPS) | NON_SAVEABLE
    missing = declared - accounted_for
    assert not missing, (
        f"Properties declared on AIOPT_Properties but not in SAVEABLE_PROPS: {sorted(missing)}. "
        "Add them to SAVEABLE_PROPS in src/utils.py, or add them to NON_SAVEABLE in this test "
        "with a comment explaining why they're transient."
    )


def test_saveable_props_list_has_no_stale_entries():
    declared = _annotated_names("AIOPT_Properties")
    stale = set(SAVEABLE_PROPS) - declared
    assert not stale, (
        f"SAVEABLE_PROPS references properties that no longer exist on AIOPT_Properties: {sorted(stale)}. "
        "Remove them from src/utils.py."
    )


def test_saveable_props_has_no_duplicates():
    assert len(SAVEABLE_PROPS) == len(set(SAVEABLE_PROPS)), "SAVEABLE_PROPS contains duplicates"


def test_pipeline_state_is_not_persisted():
    # AIOPT_PipelineState holds transient UI state (progress, last-run results).
    # Nothing from it should leak into SAVEABLE_PROPS.
    transient = _annotated_names("AIOPT_PipelineState")
    leaked = transient & set(SAVEABLE_PROPS)
    assert not leaked, f"Transient pipeline state found in SAVEABLE_PROPS: {sorted(leaked)}"
