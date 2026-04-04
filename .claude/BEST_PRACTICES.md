# Python & Blender Add-on Best Practices

## Linting

This project uses **ruff** for linting and formatting. Configuration is in `pyproject.toml`.

```bash
ruff check src/          # lint
ruff check src/ --fix    # lint and auto-fix
ruff format src/         # format
```

## Blender Add-on Conventions

- **Class naming**: Use `AIOPT_` prefix. Operators use `OT_`, panels use `PT_`, properties use no suffix.
- **Operator design**: Every operator should be idempotent. Include `bl_options = {'REGISTER', 'UNDO'}` unless there's a reason not to.
- **Context checks**: Always validate context before operating — check mode, selection, and active object. Use `poll()` classmethods where appropriate.
- **User feedback**: Use `self.report()` for messages shown to the user. Use `print()` only for debug/console logging.
- **Registration symmetry**: Everything registered in `register()` must be cleaned up in `unregister()`.
- **No state on operators**: Store persistent state on scene properties (`AIOPT_Properties`) or window manager, never on operator instances.

## Python Style

- **Early returns**: Prefer early returns over deep nesting.
- **Narrow exceptions**: Catch specific exceptions (`TypeError`, `RuntimeError`), not bare `except`.
- **Guard clauses**: Check for empty/None collections before iterating.
- **f-strings**: Use f-strings for string formatting (not `%` or `.format()`).
- **Constants**: Use UPPER_SNAKE_CASE for module-level constants.

## Project Architecture

- **Multi-file package**: Source is split into domain modules under `src/`. A build step (`python build.py`) concatenates them into a single installable `.py` in `build/`.
- **Module layout**:
  - `utils.py` — shared helpers, config, export
  - `textures.py` — image cleanup, resizing, vertex color baking
  - `materials.py` — material merging, mesh joining
  - `geometry.py` — geometry fixing, decimation, interior removal, symmetry
  - `properties.py` — property groups
  - `operators.py` — all operator classes
  - `panels.py` — all panel classes
  - `__init__.py` — bl_info, registration
- **Version management**: Single source of truth is `pyproject.toml`. The build script injects the version into `bl_info`. Tag with `v*` to trigger a GitHub release.
- **Adding a pipeline step** requires:
  1. New `AIOPT_OT_<name>` operator class in `operators.py`
  2. `BoolProperty` toggle in `AIOPT_Properties` in `properties.py` (e.g., `run_<name>`)
  3. Add property name to `SAVEABLE_PROPS` list in `utils.py`
  4. UI panel entry in `panels.py` (new sub-panel or entry in existing panel)
  5. Step entry in `AIOPT_OT_run_all` (setup/tick/teardown methods in `operators.py`)
  6. Register the class in the `classes` tuple in `__init__.py`

## Things to Avoid

- Don't import modules at function scope unless they're optional/heavy — use module-level imports (after `bl_info`).
- Don't use `bpy.context` in module scope — it's not reliable during registration. Pass context from operators.
- Don't hardcode paths — use `bpy.utils.user_resource()` or `bpy.data.filepath`.
- Don't modify `bpy.data` outside of operators — changes won't be undoable.
