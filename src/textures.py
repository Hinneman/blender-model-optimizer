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


def images_are_identical(img_a, img_b):
    """Full pixel comparison between two images.

    Only called when fingerprints match, so this is a rare slow path for
    confirmation.
    """
    if img_a.size[0] != img_b.size[0] or img_a.size[1] != img_b.size[1]:
        return False
    if img_a.channels != img_b.channels:
        return False

    px_a = img_a.pixels[:]
    px_b = img_b.pixels[:]

    if len(px_a) != len(px_b):
        return False

    # Compare in chunks for efficiency
    chunk = 4096
    for start in range(0, len(px_a), chunk):
        end = min(start + chunk, len(px_a))
        for i in range(start, end):
            if abs(px_a[i] - px_b[i]) > 0.001:
                return False
    return True


def clean_images_all(context):
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

                if images_are_identical(img_a, img_b):
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
