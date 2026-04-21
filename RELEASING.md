# Releasing a new version

## Overview

Each release does two things:

1. **Tags the repo + publishes a GitHub release** with the extension ZIP as an asset (automated — GitHub Action runs on `v*` tag push).
2. **Uploads the ZIP to extensions.blender.org** for distribution via Blender's `Get Extensions` UI (manual — the platform has a review queue).

## Checklist

### 1. Local prep

- [ ] Bump `version` in `pyproject.toml`.
- [ ] Add a `## [X.Y.Z] - YYYY-MM-DD` section to `CHANGELOG.md`.
- [ ] Update `README.md` if the new version changes anything user-visible (Features, Settings, Requirements, Installation, Usage).
- [ ] Run `scripts/validate.ps1` (Windows) or `scripts/validate.sh` (POSIX). This builds the zip and runs `blender --command extension validate` on it. Must exit 0.

### 2. Tag and push

- [ ] Commit the version bump + changelog (user commits — Claude never does).
- [ ] `git tag vX.Y.Z`
- [ ] `git push && git push --tags`
- [ ] Wait for the GitHub Action to finish. Verify the release at <https://github.com/Hinneman/blender-model-optimizer/releases> has `ai_model_optimizer-X.Y.Z.zip` attached.

### 3. Publish on extensions.blender.org

**First release only — create the listing:**

- [ ] Sign in at <https://extensions.blender.org>.
- [ ] Click **Add new extension** → upload `ai_model_optimizer-X.Y.Z.zip`.
- [ ] Fill in description, screenshots, and tags on the listing page.
- [ ] Click **Submit for review**.

**Subsequent releases:**

- [ ] Open the existing extension listing on extensions.blender.org.
- [ ] Click **Add version** → upload the new `ai_model_optimizer-X.Y.Z.zip`.
- [ ] Paste the changelog entry into the version notes.
- [ ] Submit for review.

### 4. While waiting for review

The Blender review queue typically takes a few days. During that time, sideload users can already install via the GitHub release ZIP. No action needed on our side unless reviewers request changes — check email.

## Troubleshooting

**`blender` is not on PATH (Windows):** Add `C:\Program Files\Blender Foundation\Blender 4.2\` (adjust version) to your PATH, or edit `scripts/validate.ps1` to hard-code the path.

**Reviewer objects to the "Open Debug Log" subprocess call:** The fallback plan is to replace the `os.startfile`/`subprocess` call in `AIOPT_OT_open_debug_log.execute` with a variant that only prints the log path to the UI. Document the change in the CHANGELOG and resubmit.

**Tagline rejected as too long:** Trim to ≤64 characters in `blender_manifest.toml` and rebuild.

**"module loaded with no associated file" error when installing a rebuilt zip during local testing:** A prior failed install (or earlier buggy zip) left the extension's module cached in Blender's Python `sys.modules`. Folder deletion alone does not clear it. Fully quit Blender (verify no `blender.exe` in Task Manager), then start fresh and drag the zip in again.
