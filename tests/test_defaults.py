"""Round-trip and migration tests for save_defaults / load_defaults."""

from src import utils
from src.utils import SAVEABLE_PROPS, load_defaults, save_defaults


class FakeProps:
    """Stand-in for AIOPT_Properties.

    Mirrors the one behavior save/load depend on: getattr/setattr access.
    Unknown attributes raise AttributeError so the ``setattr`` try/except
    in ``load_defaults`` gets exercised realistically.
    """

    def __init__(self, **values):
        self.__dict__.update(values)

    def __getattr__(self, name):
        # Only reached when the attribute is missing.
        raise AttributeError(name)


def _sample_props():
    """Build a FakeProps with a plausible value for every saveable prop."""
    # One value per key is enough — we just need round-trip equality.
    sample: dict[str, object] = {key: 0 for key in SAVEABLE_PROPS}
    sample.update(
        {
            "run_fix_geometry": True,
            "manifold_method": "FILL_HOLES",
            "decimate_ratio": 0.5,
            "output_filename": "test.glb",
            "output_folder": "/tmp/out",
            "resize_mode": "DOWNSIZE",
            "image_format": "WEBP",
            "protect_uv_seams": True,
            "lod_suffix_pattern": "_LOD{n}",
            "lod_ratios": "0.5,0.25",
            "symmetry_axis": "X",
            "analysis_target_preset": "MEDIUM",
        }
    )
    return FakeProps(**sample)


def test_round_trip_preserves_all_saveable_props(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "get_config_path", lambda: str(tmp_path / "defaults.json"))

    source = _sample_props()
    save_defaults(source)

    target = FakeProps(**{k: None for k in SAVEABLE_PROPS})
    assert load_defaults(target) is True

    for key in SAVEABLE_PROPS:
        assert getattr(target, key) == getattr(source, key), f"mismatch on {key}"


def test_load_returns_false_when_no_config(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "get_config_path", lambda: str(tmp_path / "missing.json"))
    assert load_defaults(FakeProps()) is False


def test_load_returns_false_on_corrupt_json(tmp_path, monkeypatch):
    path = tmp_path / "defaults.json"
    path.write_text("{ not json")
    monkeypatch.setattr(utils, "get_config_path", lambda: str(path))
    assert load_defaults(FakeProps()) is False


def test_migration_fix_manifold_true_becomes_fill_holes(tmp_path, monkeypatch):
    path = tmp_path / "defaults.json"
    path.write_text('{"fix_manifold": true}')
    monkeypatch.setattr(utils, "get_config_path", lambda: str(path))

    target = FakeProps(manifold_method="OFF")
    assert load_defaults(target) is True
    assert target.manifold_method == "FILL_HOLES"


def test_migration_fix_manifold_false_becomes_off(tmp_path, monkeypatch):
    path = tmp_path / "defaults.json"
    path.write_text('{"fix_manifold": false}')
    monkeypatch.setattr(utils, "get_config_path", lambda: str(path))

    target = FakeProps(manifold_method="FILL_HOLES")
    assert load_defaults(target) is True
    assert target.manifold_method == "OFF"


def test_migration_explicit_manifold_method_wins_over_legacy(tmp_path, monkeypatch):
    # If both keys are present, the new one must not be overwritten.
    path = tmp_path / "defaults.json"
    path.write_text('{"fix_manifold": true, "manifold_method": "PRINT3D"}')
    monkeypatch.setattr(utils, "get_config_path", lambda: str(path))

    target = FakeProps(manifold_method="OFF")
    assert load_defaults(target) is True
    assert target.manifold_method == "PRINT3D"


def test_migration_drops_removed_keys(tmp_path, monkeypatch):
    path = tmp_path / "defaults.json"
    path.write_text('{"dissolve_angle": 5.0, "run_uv_dilate": true, "uv_dilate_pixels": 4}')
    monkeypatch.setattr(utils, "get_config_path", lambda: str(path))

    target = FakeProps()
    # Should not raise even though none of these keys exist on the target.
    assert load_defaults(target) is True
    assert not hasattr(target, "dissolve_angle")
    assert not hasattr(target, "run_uv_dilate")
    assert not hasattr(target, "uv_dilate_pixels")


def test_migration_forces_protect_uv_seams_on(tmp_path, monkeypatch):
    path = tmp_path / "defaults.json"
    path.write_text('{"protect_uv_seams": false}')
    monkeypatch.setattr(utils, "get_config_path", lambda: str(path))

    target = FakeProps(protect_uv_seams=False)
    assert load_defaults(target) is True
    assert target.protect_uv_seams is True


def test_unknown_keys_are_ignored(tmp_path, monkeypatch):
    path = tmp_path / "defaults.json"
    path.write_text('{"some_future_key": 42, "decimate_ratio": 0.3}')
    monkeypatch.setattr(utils, "get_config_path", lambda: str(path))

    target = FakeProps(decimate_ratio=1.0)
    assert load_defaults(target) is True
    assert target.decimate_ratio == 0.3
    assert not hasattr(target, "some_future_key")
