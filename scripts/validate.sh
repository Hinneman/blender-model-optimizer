#!/usr/bin/env bash
# Pre-tag validation: build the extension zip and run Blender's manifest validator.
# Requires `blender` on PATH.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "Building extension zip..."
python build.py

ZIP="$(ls -t build/blender_model_optimizer-*.zip 2>/dev/null | head -n1 || true)"
if [[ -z "$ZIP" ]]; then
  echo "ERROR: no blender_model_optimizer-*.zip found in build/" >&2
  exit 1
fi

echo "Validating $ZIP with Blender..."

if ! command -v blender >/dev/null 2>&1; then
  echo "ERROR: blender not found on PATH. Install Blender 4.2+ and add it to PATH." >&2
  exit 2
fi

blender --command extension validate "$ZIP"
echo "OK: $ZIP validated."
