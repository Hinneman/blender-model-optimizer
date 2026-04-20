# Texture-bleed fix options (parking lot)

**Context:** After decimation, UV island boundaries on AI-generated meshes can smear colors across adjacent islands. The 1.7.0 soft-hint `Protect UV Seams` (Sharp edges) helps but doesn't eliminate the problem, and the 1.8.0 hard topological-split attempt (see [specs/2026-04-19-hard-uv-seam-constraint-design.md](specs/2026-04-19-hard-uv-seam-constraint-design.md)) was abandoned — it broke fragmented-UV meshes.

This file records approaches we considered but did not pursue on 2026-04-20. Option 4 (vertex-group weighting) is the one we are designing now; see its own spec under `specs/`.

## Option 1 — Per-island decimation

Detect UV islands, separate the mesh into parts by island, decimate each part independently (scaled ratio per part based on its face count), rejoin. Guarantees no cross-island collapses without the fragility of the topological-split restitch — parts were never shared, so there's no weld tolerance to tune.

**Concerns:**
- Cost scales with island count. A camo-style mesh with hundreds of islands would run decimate hundreds of times.
- Rejoining needs UV-aware merging so we don't weld physical seams that happen to be close in 3D space.
- Small islands may decimate poorly in isolation (a 20-face island at ratio 0.1 is just 2 faces).

## Option 2 — Post-decimate UV snapping

After decimation, walk every vertex whose UV drifted outside its original island boundary and snap it to the nearest valid point inside the island. Correct the drift instead of preventing it.

**Concerns:**
- Needs UV-island rasterization or polygon-inside tests.
- Doesn't fix texture stretching where UVs drifted but stayed inside the island.
- "Nearest valid point" may pick a point that doesn't visually match the adjacent geometry.

## Option 3 — Per-island UV padding (dilation)

Originally dismissed because unconstrained decimation's drift was larger than any reasonable dilation width. Worth revisiting *as a complement* to `Protect UV Seams` (soft hint) — with seam protection on, drift is smaller and dilation may cover it.

**Concerns:**
- Only helps if there is empty atlas space between islands. Fragmented camo atlases are nearly edge-to-edge.
- Mask-generation (UV island rasterization) is the hard part of the implementation.

## Option 5 — Analysis-stage warning

Extend the existing Analysis step to detect severely fragmented UVs and warn the user that aggressive decimation will cause bleed, suggesting `Protect UV Seams` or a less-aggressive ratio. Doesn't fix anything, just surfaces the tradeoff at the right moment.

**Concerns:**
- Purely informational; doesn't improve output quality for users who ignore the warning.
- Worth pairing with an actual fix, not as a standalone.

## Option 6 — Accept the status quo

The 1.8.0 release already ships `Protect UV Seams` as an opt-in toggle. Document when to enable it and call that the answer. No code change.

**Concerns:**
- Leaves the visible bleed artifact as a known limitation for users who don't know to flip the toggle.
- Fair to revisit if option 4 (or any other attempt) proves insufficient.
