import math

import bpy

from .utils import get_image_users


def get_image_fingerprint(img):
    """Create a fingerprint of an image's actual pixel content.

    Compares dimensions + sampled pixel values to detect true duplicates
    without needing to compare every single pixel (which would be slow
    on large textures).
    """
    if not img.has_data:
        return None

    w, h = img.size[0], img.size[1]
    if w == 0 or h == 0:
        return None

    channels = img.channels
    pixels = img.pixels  # flat RGBA array, read-only access

    # Start fingerprint with dimensions and channel count
    fp = (w, h, channels)

    # Sample pixels at fixed positions across the image to build
    # a content hash. 16 sample points is enough to distinguish
    # different textures while being very fast even on 8K images.
    total_pixels = w * h
    sample_count = min(16, total_pixels)

    sampled = []
    for i in range(sample_count):
        # Spread samples evenly across the pixel array
        pixel_index = int((i / sample_count) * total_pixels)
        offset = pixel_index * channels
        # Read RGBA values rounded to avoid float precision issues
        sample = tuple(round(pixels[offset + c], 4) for c in range(min(channels, 4)))
        sampled.append(sample)

    return fp + tuple(sampled)


def images_are_identical(img_a, img_b, token=None):
    """Full pixel comparison between two images.

    Only called when fingerprints match, so this is a rare slow path for
    confirmation. When *token* is supplied, checks it once per chunk so
    cancel takes effect within ~4k pixels.
    """
    if img_a.size[0] != img_b.size[0] or img_a.size[1] != img_b.size[1]:
        return False
    if img_a.channels != img_b.channels:
        return False

    px_a = img_a.pixels[:]
    px_b = img_b.pixels[:]

    if len(px_a) != len(px_b):
        return False

    chunk = 4096
    for start in range(0, len(px_a), chunk):
        if token is not None:
            token.check()
        end = min(start + chunk, len(px_a))
        for i in range(start, end):
            if abs(px_a[i] - px_b[i]) > 0.001:
                return False
    return True


def clean_images_all(context, token=None):
    """Remove truly identical images by comparing pixel content.

    Returns ``(removed_count, detail_string)``.
    """
    removed = 0
    images = []
    for img in bpy.data.images:
        try:
            if img.type == "IMAGE" and img.has_data and img.name not in ("Render Result", "Viewer Node"):
                images.append(img)
        except ReferenceError:
            continue

    if len(images) < 2:
        return (0, "Not enough images to compare")

    # Phase 1: Group images by fingerprint (fast)
    fingerprint_groups = {}
    for img in images:
        if token is not None:
            token.check()
        fp = get_image_fingerprint(img)
        if fp is None:
            continue
        fingerprint_groups.setdefault(fp, []).append(img)

    # Phase 2: For groups with matching fingerprints, do full comparison
    for _fp, group in fingerprint_groups.items():
        if len(group) < 2:
            continue

        # Find clusters of truly identical images within this group
        merged = set()
        for i, img_a in enumerate(group):
            if token is not None:
                token.check()
            try:
                if img_a.name in merged:
                    continue
            except ReferenceError:
                continue
            for j in range(i + 1, len(group)):
                img_b = group[j]
                try:
                    if img_b.name in merged:
                        continue
                except ReferenceError:
                    continue

                if images_are_identical(img_a, img_b, token=token):
                    # Keep whichever has more material users, or img_a as tiebreaker
                    users_a = get_image_users(img_a)
                    users_b = get_image_users(img_b)

                    if users_b > users_a:
                        keeper, duplicate = img_b, img_a
                    else:
                        keeper, duplicate = img_a, img_b

                    print(
                        f"  [AI Optimizer] Identical: '{duplicate.name}' == '{keeper.name}'"
                        f" → removing '{duplicate.name}'"
                    )
                    is_a = duplicate == img_a
                    duplicate.user_remap(keeper)
                    dup_name = duplicate.name
                    bpy.data.images.remove(duplicate)
                    removed += 1
                    merged.add(dup_name)

                    # If img_a was the duplicate, stop comparing it
                    if is_a:
                        break

    return (removed, f"Removed {removed} truly identical image(s)")


def clean_unused_all(context):
    """Remove all unused data blocks (orphaned materials, textures, meshes).

    Returns ``(removed_count, detail_string)``.
    """
    before = len(bpy.data.images) + len(bpy.data.materials) + len(bpy.data.meshes) + len(bpy.data.textures)

    bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)

    after = len(bpy.data.images) + len(bpy.data.materials) + len(bpy.data.meshes) + len(bpy.data.textures)

    removed = before - after
    return (removed, f"Removed {removed} unused data block(s)")


def _collect_uv_triangles_for_image(img, mesh_objects):
    """Return a list of (u0,v0, u1,v1, u2,v2) tuples for every face in
    *mesh_objects* whose material references *img*.

    Quads and n-gons are fan-triangulated around the first loop so one
    polygon contributes (n-2) triangles.
    """
    triangles = []
    for obj in mesh_objects:
        mesh = obj.data
        if not mesh.uv_layers:
            continue
        uv_layer = mesh.uv_layers.active.data

        # Per-material: does this slot reference img?
        slot_uses_img = []
        for slot in obj.material_slots:
            mat = slot.material
            uses = False
            if mat and mat.use_nodes:
                for node in mat.node_tree.nodes:
                    if node.type == "TEX_IMAGE" and node.image == img:
                        uses = True
                        break
            slot_uses_img.append(uses)

        if not any(slot_uses_img):
            continue

        for poly in mesh.polygons:
            if poly.material_index >= len(slot_uses_img) or not slot_uses_img[poly.material_index]:
                continue
            loop_indices = poly.loop_indices
            if len(loop_indices) < 3:
                continue
            uv0 = uv_layer[loop_indices[0]].uv
            for i in range(1, len(loop_indices) - 1):
                uv1 = uv_layer[loop_indices[i]].uv
                uv2 = uv_layer[loop_indices[i + 1]].uv
                triangles.append((uv0.x, uv0.y, uv1.x, uv1.y, uv2.x, uv2.y))
    return triangles


def _rasterize_coverage(triangles, width, height, token=None):
    """Rasterize UV triangles into a ``(height, width)`` boolean coverage mask.

    Uses per-triangle bounding boxes and numpy barycentric tests so the
    cost is proportional to island area, not triangle count times image area.
    """
    import numpy as np

    coverage = np.zeros((height, width), dtype=bool)
    if not triangles:
        return coverage

    # Build pixel grid index arrays once; we'll slice into them per-triangle.
    for tri_idx, (u0, v0, u1, v1, u2, v2) in enumerate(triangles):
        if token is not None and (tri_idx & 0x3FF) == 0:
            token.check()

        # Convert UV (0..1, with wrap ignored — we clip) to pixel coordinates.
        # v is flipped: v=0 is bottom of image, row 0 is top.
        px0, py0 = u0 * width, (1.0 - v0) * height
        px1, py1 = u1 * width, (1.0 - v1) * height
        px2, py2 = u2 * width, (1.0 - v2) * height

        min_x = max(0, math.floor(min(px0, px1, px2)))
        max_x = min(width - 1, math.ceil(max(px0, px1, px2)))
        min_y = max(0, math.floor(min(py0, py1, py2)))
        max_y = min(height - 1, math.ceil(max(py0, py1, py2)))
        if max_x < min_x or max_y < min_y:
            continue

        # Precompute barycentric denominator
        denom = (py1 - py2) * (px0 - px2) + (px2 - px1) * (py0 - py2)
        if abs(denom) < 1e-12:
            continue
        inv_denom = 1.0 / denom

        ys = np.arange(min_y, max_y + 1) + 0.5
        xs = np.arange(min_x, max_x + 1) + 0.5
        gx, gy = np.meshgrid(xs, ys)

        w0 = ((py1 - py2) * (gx - px2) + (px2 - px1) * (gy - py2)) * inv_denom
        w1 = ((py2 - py0) * (gx - px2) + (px0 - px2) * (gy - py2)) * inv_denom
        w2 = 1.0 - w0 - w1

        inside = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
        coverage[min_y : max_y + 1, min_x : max_x + 1] |= inside
    return coverage


def dilate_image_gutters(img, mesh_objects, pixels, token=None):
    """Dilate the colored UV-island regions of *img* into the surrounding
    gutter by *pixels* pixels.

    Fragmented UV layouts (typical of AI-generated meshes) leave narrow
    unpainted channels between islands. After decimation small UV drift
    causes faces to sample into those channels, showing up as black
    smears. Dilation bleeds each island's edge colors outward so drifted
    samples land on a sane color instead.

    Returns ``True`` if the image was modified.
    """
    import numpy as np

    if img.type != "IMAGE" or not img.has_data:
        return False
    if img.name in ("Render Result", "Viewer Node"):
        return False

    w, h = img.size[0], img.size[1]
    if w == 0 or h == 0 or pixels <= 0:
        return False

    channels = img.channels
    if channels < 3:
        return False

    triangles = _collect_uv_triangles_for_image(img, mesh_objects)
    if not triangles:
        return False

    if token is not None:
        token.check()

    coverage = _rasterize_coverage(triangles, w, h, token=token)
    if not coverage.any():
        return False

    # Read pixels into (h, w, channels) array. Blender stores pixels
    # bottom-up, left-right, but our coverage was built top-down above,
    # so flip the pixels array vertically to match.
    flat = np.empty(w * h * channels, dtype=np.float32)
    img.pixels.foreach_get(flat)
    px = flat.reshape(h, w, channels)
    px = np.flipud(px).copy()  # top-down orientation, writable

    for _ in range(pixels):
        if token is not None:
            token.check()
        # Shift coverage in 4 directions; any True neighbor makes this
        # pixel a candidate for filling. Then for each candidate we copy
        # from whichever neighbor is covered, preferring left/right/up/down
        # in that fixed order so the result is deterministic.
        up = np.zeros_like(coverage)
        up[:-1, :] = coverage[1:, :]
        down = np.zeros_like(coverage)
        down[1:, :] = coverage[:-1, :]
        left = np.zeros_like(coverage)
        left[:, :-1] = coverage[:, 1:]
        right = np.zeros_like(coverage)
        right[:, 1:] = coverage[:, :-1]

        candidates = (up | down | left | right) & ~coverage
        if not candidates.any():
            break

        # For each candidate, pick a neighbor in priority order.
        # Build per-direction masks: candidate AND that direction is covered.
        use_left = candidates & left
        use_right = candidates & right & ~use_left
        use_up = candidates & up & ~use_left & ~use_right
        use_down = candidates & down & ~use_left & ~use_right & ~use_up

        # Apply copies. A right-shifted coverage means the source pixel is
        # one column to the LEFT (we're filling from the left neighbor),
        # so to fill pixel (y,x) we read from (y, x-1) etc.
        if use_left.any():
            ys, xs = np.where(use_left)
            px[ys, xs, :] = px[ys, np.clip(xs + 1, 0, w - 1), :]
        if use_right.any():
            ys, xs = np.where(use_right)
            px[ys, xs, :] = px[ys, np.clip(xs - 1, 0, w - 1), :]
        if use_up.any():
            ys, xs = np.where(use_up)
            px[ys, xs, :] = px[np.clip(ys + 1, 0, h - 1), xs, :]
        if use_down.any():
            ys, xs = np.where(use_down)
            px[ys, xs, :] = px[np.clip(ys - 1, 0, h - 1), xs, :]

        coverage |= candidates

    # Flip back to Blender's bottom-up order and write.
    px = np.flipud(px)
    img.pixels.foreach_set(px.reshape(-1))
    img.update()
    img.pack()
    return True


def dilate_gutters_all(context, props, token=None):
    """Apply UV-gutter dilation to every texture that has UV coverage from
    the scene's meshes.

    Returns ``(modified_count, detail_string)``.
    """
    meshes = [obj for obj in context.scene.objects if obj.type == "MESH"]
    pixels = int(props.uv_dilate_pixels)

    modified = 0
    touched_names = []
    for img in list(bpy.data.images):
        if token is not None:
            token.check()
        try:
            if dilate_image_gutters(img, meshes, pixels, token=token):
                modified += 1
                touched_names.append(img.name)
        except ReferenceError:
            continue

    if modified:
        return (modified, f"Dilated {modified} texture(s) by {pixels}px")
    return (0, "No textures needed dilation")


def resize_texture_single(img, props):
    """Resize a single image according to *props* settings.

    Returns ``True`` if the image was resized.
    """
    max_size = props.max_texture_size

    if img.type != "IMAGE" or not img.has_data:
        return False
    if img.name in ("Render Result", "Viewer Node"):
        return False

    w, h = img.size[0], img.size[1]

    needs_resize = (w != max_size or h != max_size) if props.resize_mode == "ALL" else (w > max_size or h > max_size)

    if not needs_resize:
        return False

    if props.resize_mode == "ALL":
        new_w, new_h = max_size, max_size
    else:
        scale = max_size / max(w, h)
        new_w = max(1, 2 ** round(math.log2(max(1, int(w * scale)))))
        new_h = max(1, 2 ** round(math.log2(max(1, int(h * scale)))))

    img.scale(new_w, new_h)
    img.pack()
    return True
