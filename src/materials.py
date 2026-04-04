import bpy

from .textures import get_image_fingerprint


def _get_material_signature(mat, threshold=0.01):
    """Build a hashable signature from a material's node tree.

    Two materials with the same signature are visually identical and safe
    to merge.  Texture images are compared by pixel fingerprint (via
    ``get_image_fingerprint``), and shader input values are rounded to
    *threshold* so that near-identical values collapse to the same key.
    """
    if mat.node_tree is None:
        return None

    parts = []
    for node in mat.node_tree.nodes:
        if node.type == "TEX_IMAGE" and node.image:
            fp = get_image_fingerprint(node.image)
            parts.append(("tex", node.name, fp))
        elif node.type == "BSDF_PRINCIPLED":
            for inp in node.inputs:
                if not inp.is_linked:
                    if inp.type == "RGBA":
                        val = tuple(round(c / threshold) for c in inp.default_value)
                        parts.append(("input", inp.name, val))
                    elif inp.type == "VALUE":
                        val = round(inp.default_value / threshold)
                        parts.append(("input", inp.name, val))
    return tuple(sorted(parts))


def merge_duplicate_materials(context, threshold=0.01):
    """Merge materials with identical shader setups.

    Returns ``(merged_count, detail_string)`` where *detail_string*
    lists which materials were replaced and by what.
    """
    sig_to_keeper = {}
    merged_count = 0
    details = []

    # Build a list first so we don't mutate while iterating
    all_mats = list(bpy.data.materials)

    for mat in all_mats:
        sig = _get_material_signature(mat, threshold)
        if sig is None:
            continue

        if sig in sig_to_keeper:
            keeper = sig_to_keeper[sig]
            mat_name = mat.name
            keeper_name = keeper.name
            print(f"[AI Optimizer] Merging '{mat_name}' -> '{keeper_name}'")
            mat.user_remap(keeper)
            bpy.data.materials.remove(mat)
            merged_count += 1
            details.append(f"'{mat_name}' -> '{keeper_name}'")
        else:
            sig_to_keeper[sig] = mat

    detail_string = "; ".join(details) if details else "no duplicates found"
    return merged_count, detail_string


def join_meshes_by_material(context, meshes, mode="BY_MATERIAL"):
    """Join mesh objects to reduce draw calls.

    *meshes* should be a list of mesh objects.
    *mode* is either ``"ALL"`` (join everything) or ``"BY_MATERIAL"``
    (join objects that share the same material set).

    Returns ``(resulting_objects, detail_string)``.
    """
    if len(meshes) <= 1:
        return (list(meshes), "Only 1 object, nothing to join")

    original_count = len(meshes)

    if mode == "ALL":
        bpy.ops.object.select_all(action="DESELECT")
        for obj in meshes:
            obj.select_set(True)
        context.view_layer.objects.active = meshes[0]
        bpy.ops.object.join()
        result = [context.view_layer.objects.active]
        return (result, f"Joined {original_count} objects into 1 (all)")

    # -- BY_MATERIAL mode --
    # Group objects by their material set.
    groups = {}
    for obj in meshes:
        mat_names = frozenset(slot.material.name for slot in obj.material_slots if slot.material)
        if not mat_names:
            mat_names = frozenset(["__no_material__"])
        groups.setdefault(mat_names, []).append(obj)

    result_objects = []
    for _key, group in groups.items():
        if len(group) == 1:
            result_objects.append(group[0])
            continue
        bpy.ops.object.select_all(action="DESELECT")
        for obj in group:
            obj.select_set(True)
        context.view_layer.objects.active = group[0]
        bpy.ops.object.join()
        result_objects.append(context.view_layer.objects.active)

    detail = f"Joined {original_count} objects into {len(result_objects)} (by material)"
    return (result_objects, detail)
