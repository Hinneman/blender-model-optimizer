"""Tests for estimate_glb_size — the logic that feeds the size estimate
shown in the sidebar. Regressions here are user-visible but silent
(numbers just drift), so pin the magic constants with assertions."""

import types

import pytest

from src import utils
from src.utils import estimate_glb_size

OVERHEAD = 10 * 1024  # matches src/utils.py


def _props(**overrides):
    """Baseline props: nothing enabled, PNG output, no resize. Overrides win."""
    base = {
        "run_decimate": False,
        "decimate_ratio": 1.0,
        "run_symmetry": False,
        "use_draco": False,
        "draco_level": 0,
        "run_resize_textures": False,
        "max_texture_size": 1024,
        "resize_mode": "DOWNSIZE",
        "image_format": "NONE",
        "image_quality": 100,
        "bake_normal_map": False,
        "normal_map_resolution": 1024,
    }
    base.update(overrides)
    return types.SimpleNamespace(**base)


def _mesh(verts: int, faces: int):
    return types.SimpleNamespace(data=types.SimpleNamespace(vertices=[None] * verts, polygons=[None] * faces))


class _Img:
    def __init__(self, *, name: str = "tex", type: str = "IMAGE", size: tuple[int, int] = (512, 512)):
        self.name = name
        self.type = type
        self.size = size


@pytest.fixture
def stub_images(monkeypatch):
    """Set the image list returned by bpy.data.images and give every image ≥1 user."""

    def _apply(images: list, users: int = 1):
        monkeypatch.setattr(utils.bpy.data, "images", images)
        monkeypatch.setattr(utils, "get_image_users", lambda img: users)

    return _apply


# ------------------------- overhead / empty scene -------------------------


def test_empty_scene_returns_overhead_only(stub_images):
    stub_images([])
    assert estimate_glb_size([], _props()) == OVERHEAD


# ------------------------- geometry ---------------------------------------


def test_geometry_bytes_per_vertex_and_face(stub_images):
    stub_images([])
    # 10 verts * 32B + 20 faces * 12B = 320 + 240 = 560
    size = estimate_glb_size([_mesh(verts=10, faces=20)], _props())
    assert size == pytest.approx(560 + OVERHEAD)


def test_decimate_ratio_applied_only_when_enabled(stub_images):
    stub_images([])
    mesh = _mesh(verts=100, faces=100)
    off = estimate_glb_size([mesh], _props(run_decimate=False, decimate_ratio=0.5))
    on = estimate_glb_size([mesh], _props(run_decimate=True, decimate_ratio=0.5))
    geo = 100 * 32 + 100 * 12
    assert off == pytest.approx(geo + OVERHEAD)
    assert on == pytest.approx(geo * 0.5 + OVERHEAD)


def test_symmetry_reduces_geometry_by_40_percent(stub_images):
    stub_images([])
    mesh = _mesh(verts=100, faces=100)
    size = estimate_glb_size([mesh], _props(run_symmetry=True))
    geo = (100 * 32 + 100 * 12) * 0.6
    assert size == pytest.approx(geo + OVERHEAD)


def test_draco_factor_at_level_0_and_10(stub_images):
    stub_images([])
    mesh = _mesh(verts=100, faces=100)
    raw = 100 * 32 + 100 * 12
    lvl0 = estimate_glb_size([mesh], _props(use_draco=True, draco_level=0))
    lvl10 = estimate_glb_size([mesh], _props(use_draco=True, draco_level=10))
    assert lvl0 == pytest.approx(raw / 6.0 + OVERHEAD)
    assert lvl10 == pytest.approx(raw / 30.0 + OVERHEAD)


# ------------------------- texture filtering ------------------------------


def test_non_image_type_is_skipped(stub_images):
    stub_images([_Img(type="RENDER_RESULT", size=(1024, 1024))])
    assert estimate_glb_size([], _props()) == OVERHEAD


def test_render_result_and_viewer_node_are_skipped(stub_images):
    stub_images([_Img(name="Render Result"), _Img(name="Viewer Node")])
    assert estimate_glb_size([], _props()) == OVERHEAD


def test_image_with_zero_users_is_skipped(stub_images):
    stub_images([_Img(size=(1024, 1024))], users=0)
    assert estimate_glb_size([], _props()) == OVERHEAD


def test_zero_size_image_is_skipped(stub_images):
    stub_images([_Img(size=(0, 0))])
    assert estimate_glb_size([], _props()) == OVERHEAD


# ------------------------- texture compression ratios ---------------------


def test_png_ratio_is_5(stub_images):
    stub_images([_Img(size=(64, 64))])
    raw = 64 * 64 * 4
    size = estimate_glb_size([], _props(image_format="NONE"))
    assert size == pytest.approx(raw / 5.0 + OVERHEAD)


def test_webp_ratio_spans_15_to_80(stub_images):
    stub_images([_Img(size=(64, 64))])
    raw = 64 * 64 * 4
    q100 = estimate_glb_size([], _props(image_format="WEBP", image_quality=100))
    q1 = estimate_glb_size([], _props(image_format="WEBP", image_quality=1))
    assert q100 == pytest.approx(raw / 15.0 + OVERHEAD)
    # q=1 → ratio 15 + 0.99 * 65 ≈ 79.35
    assert q1 == pytest.approx(raw / (15.0 + 0.99 * 65.0) + OVERHEAD)


def test_jpeg_ratio_spans_10_to_50(stub_images):
    stub_images([_Img(size=(64, 64))])
    raw = 64 * 64 * 4
    q100 = estimate_glb_size([], _props(image_format="JPEG", image_quality=100))
    q1 = estimate_glb_size([], _props(image_format="JPEG", image_quality=1))
    assert q100 == pytest.approx(raw / 10.0 + OVERHEAD)
    assert q1 == pytest.approx(raw / (10.0 + 0.99 * 40.0) + OVERHEAD)


# ------------------------- resize modes -----------------------------------


def test_resize_mode_all_forces_max_size(stub_images):
    stub_images([_Img(size=(4096, 2048))])
    raw = 512 * 512 * 4
    size = estimate_glb_size(
        [], _props(run_resize_textures=True, resize_mode="ALL", max_texture_size=512, image_format="NONE")
    )
    assert size == pytest.approx(raw / 5.0 + OVERHEAD)


def test_resize_mode_downsize_leaves_smaller_images_alone(stub_images):
    stub_images([_Img(size=(256, 256))])
    raw = 256 * 256 * 4
    size = estimate_glb_size(
        [], _props(run_resize_textures=True, resize_mode="DOWNSIZE", max_texture_size=1024, image_format="NONE")
    )
    assert size == pytest.approx(raw / 5.0 + OVERHEAD)


def test_resize_mode_downsize_scales_larger_images(stub_images):
    # 2048x1024 with max 1024 → scale 0.5 → 1024x512 (rounded to nearest power of two)
    stub_images([_Img(size=(2048, 1024))])
    raw = 1024 * 512 * 4
    size = estimate_glb_size(
        [], _props(run_resize_textures=True, resize_mode="DOWNSIZE", max_texture_size=1024, image_format="NONE")
    )
    assert size == pytest.approx(raw / 5.0 + OVERHEAD)


# ------------------------- normal map bake --------------------------------


def test_normal_map_bake_adds_texture(stub_images):
    stub_images([])
    baseline = estimate_glb_size([], _props(bake_normal_map=False))
    baked = estimate_glb_size([], _props(bake_normal_map=True, normal_map_resolution=256, image_format="NONE"))
    nmap_raw = 256 * 256 * 3
    assert baked - baseline == pytest.approx(nmap_raw / 5.0)
