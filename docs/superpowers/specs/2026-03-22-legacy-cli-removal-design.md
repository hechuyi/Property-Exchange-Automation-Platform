# Legacy CLI Removal Design

## Goal

Remove the legacy source-tree CLI entrypoints from the product surface so the repository has a single supported operator path: `desktop_app/` + `desktop_backend/`.

## Scope

Delete the legacy wrapper scripts under `bin/` that expose parser/downloader/postprocess workflows as user-facing commands.

Keep the engine modules under `peap/`, `peap_parsers/`, and `peap_postprocess/` unless they are proven unused by the desktop product path. These modules remain runtime dependencies of the desktop workflow and are not part of the removal target.

Update repository docs and tests so they no longer describe or enforce the removed CLI surface.

## Architecture

The repository will move from a dual-surface model to a single product surface:

- Supported product entry: Electron desktop shell plus local desktop backend.
- Internal engine dependencies: parser/downloader/postprocess modules consumed by the desktop backend.
- Removed entry surface: `bin/*.py` wrappers and the unified `bin/peap.py` dispatcher.

This keeps the engine code available for the desktop pipeline while removing the accidental product contract created by the old command-line wrappers.

## Behavioral Changes

The desktop product becomes the only documented and supported interactive workflow.

The backend must enforce task-start constraints on the server side rather than trusting renderer button state alone. If one mutating job is already running, attempts to start another must fail explicitly with a clear error.

## Deletion Boundary

Delete:

- `bin/daily_pipeline.py`
- `bin/download.py`
- `bin/download_oneclick.py`
- `bin/parse.py`
- `bin/parser_regression.py`
- `bin/peap.py`
- `bin/public_resource_deals.py`

Retain:

- `bin/bootstrap.py` if still needed for import-path setup by non-product internal tooling
- runtime engine modules and desktop packaging/runtime files

## Risks

The main risk is deleting a wrapper that is still referenced by docs, tests, or helper scripts. This is mitigated by a reference sweep before deletion and by running desktop tests plus targeted Python tests after each cleanup step.

The second risk is exposing missing server-side invariants once the old surface is removed. This is addressed by adding tests for backend task exclusivity before implementation.

## Validation

- `desktop_app` Node tests must stay green.
- Targeted Python tests for desktop backend/service and streaming pipeline must pass.
- Repository docs must no longer instruct users to run `python bin/peap.py ...` or related wrappers.
