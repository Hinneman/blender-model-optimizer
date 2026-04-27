"""Tests for filename auto-extension swap and format dispatch."""

import types
from unittest.mock import MagicMock

import pytest

from blender_model_optimizer import utils
from blender_model_optimizer.utils import _export_format_update, swap_export_extension


def _full_props(**overrides):
    base = dict(
        export_format="GLB",
        output_filename="model.glb",
        output_folder="/tmp",
        export_selected_only=True,
        use_draco=False,
        draco_level=6,
        draco_position_quantization=14,
        draco_normal_quantization=10,
        draco_texcoord_quantization=12,
        image_format="WEBP",
        image_quality=85,
        fbx_axis_preset="UNREAL",
        fbx_embed_textures=True,
        fbx_smoothing="FACE",
        obj_export_materials=True,
        obj_forward_axis="NEGATIVE_Z",
        obj_up_axis="Y",
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


@pytest.mark.parametrize(
    "current,fmt,expected",
    [
        ("model.glb", "FBX", "model.fbx"),
        ("model.fbx", "GLB", "model.glb"),
        ("model.obj", "FBX", "model.fbx"),
        ("model.GLB", "FBX", "model.fbx"),  # case-insensitive on the strip
        ("model", "GLB", "model.glb"),       # no extension → append
        ("model.unknown", "GLB", "model.unknown.glb"),  # unknown ext → append, don't strip
        ("", "GLB", ".glb"),                 # empty stays empty + ext (Blender's behavior is fine)
        ("a.b.glb", "FBX", "a.b.fbx"),       # only the trailing known ext is swapped
    ],
)
def test_swap_export_extension(current, fmt, expected):
    assert swap_export_extension(current, fmt) == expected


def test_export_format_update_swaps_filename():
    props = types.SimpleNamespace(
        output_filename="model.glb",
        export_format="FBX",
    )
    # _tag_3d_redraw walks context.screen.areas; pass a stub.
    ctx = types.SimpleNamespace(screen=types.SimpleNamespace(areas=[]))
    _export_format_update(props, ctx)
    assert props.output_filename == "model.fbx"


def test_export_fbx_calls_blender_fbx_op(monkeypatch):
    fbx_op = MagicMock()
    monkeypatch.setattr(utils.bpy.ops.export_scene, "fbx", fbx_op)
    monkeypatch.setattr(utils.os.path, "exists", lambda p: True)
    monkeypatch.setattr(utils.os.path, "getsize", lambda p: 1024)

    props = _full_props(export_format="FBX", output_filename="m.fbx")
    detail = utils.export_model(context=MagicMock(), props=props)

    fbx_op.assert_called_once()
    kwargs = fbx_op.call_args.kwargs
    assert kwargs["filepath"].endswith(".fbx")
    assert kwargs["use_selection"] is True
    assert kwargs["use_mesh_modifiers"] is True
    assert kwargs["embed_textures"] is True
    assert kwargs["mesh_smooth_type"] == "FACE"
    assert "Exported" in detail


def test_export_fbx_unreal_axis_preset(monkeypatch):
    fbx_op = MagicMock()
    monkeypatch.setattr(utils.bpy.ops.export_scene, "fbx", fbx_op)
    monkeypatch.setattr(utils.os.path, "exists", lambda p: True)
    monkeypatch.setattr(utils.os.path, "getsize", lambda p: 1024)

    props = _full_props(export_format="FBX", fbx_axis_preset="UNREAL")
    utils.export_model(context=MagicMock(), props=props)

    kwargs = fbx_op.call_args.kwargs
    assert kwargs["axis_forward"] == "X"
    assert kwargs["axis_up"] == "Z"
    assert kwargs["bake_space_transform"] is True
    assert kwargs["global_scale"] == 1.0


def test_export_fbx_embed_disabled_changes_path_mode(monkeypatch):
    fbx_op = MagicMock()
    monkeypatch.setattr(utils.bpy.ops.export_scene, "fbx", fbx_op)
    monkeypatch.setattr(utils.os.path, "exists", lambda p: True)
    monkeypatch.setattr(utils.os.path, "getsize", lambda p: 1024)

    props = _full_props(export_format="FBX", fbx_embed_textures=False)
    utils.export_model(context=MagicMock(), props=props)

    assert fbx_op.call_args.kwargs["path_mode"] == "AUTO"
    assert fbx_op.call_args.kwargs["embed_textures"] is False


def test_export_obj_calls_blender_obj_op(monkeypatch):
    obj_op = MagicMock()
    monkeypatch.setattr(utils.bpy.ops.wm, "obj_export", obj_op)
    monkeypatch.setattr(utils.os.path, "exists", lambda p: True)
    monkeypatch.setattr(utils.os.path, "getsize", lambda p: 1024)

    props = _full_props(export_format="OBJ", output_filename="m.obj")
    detail = utils.export_model(context=MagicMock(), props=props)

    obj_op.assert_called_once()
    kwargs = obj_op.call_args.kwargs
    assert kwargs["filepath"].endswith(".obj")
    assert kwargs["export_selected_objects"] is True
    assert kwargs["apply_modifiers"] is True
    assert kwargs["export_materials"] is True
    assert kwargs["forward_axis"] == "NEGATIVE_Z"
    assert kwargs["up_axis"] == "Y"
    assert "Exported" in detail


def test_generate_lods_uses_chosen_format_extension(monkeypatch):
    fbx_op = MagicMock()
    monkeypatch.setattr(utils.bpy.ops.export_scene, "fbx", fbx_op)
    monkeypatch.setattr(utils.os.path, "exists", lambda p: True)
    monkeypatch.setattr(utils.os.path, "getsize", lambda p: 1024)
    monkeypatch.setattr(utils, "get_selected_meshes", lambda: [])

    props = _full_props(
        export_format="FBX",
        output_filename="model.fbx",
        run_lod=True,
        lod_levels=2,
        lod_suffix_pattern="_LOD{n}",
        lod_ratios="1.0, 0.5",
    )
    utils.generate_lods(context=MagicMock(), props=props)

    # No meshes → no exports get called, but we want the function not to crash
    # and not to assume .glb. Real per-call extension is exercised by the smoke test.
    assert fbx_op.call_count == 0
