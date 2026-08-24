# Distribution Boundary

The repository root remains the development source tree. This profile is for
macOS source distribution and development only. A distributable source
staging tree is generated only when needed; it is not checked in and does not
replace the development tree.

Use the staging command from the repository root:

```bash
uv run python scripts/prepare_distribution.py --output release/PEAP-source
```

The command is deliberately limited to copying files and writing a manifest.
It does not install dependencies, run a frontend build, create an App bundle,
or copy runtime data. By default it refuses to stage a dirty Git worktree, so
an official distribution is tied to a reviewed commit. `--allow-dirty` is only
for a local preview while development is in progress. `--force` only replaces
an intact staging tree whose generated manifest still matches every file; it
refuses unrecognized directories, added files, edited files, and symlinks.
An existing staging tree is a point-in-time artifact and is not synchronized
when the development tree changes. Regenerate it explicitly after the release
commit; a staging manifest with `source_dirty: true` is a preview and must not
be distributed.

The manifest excludes tests, plans, worktrees, virtual environments,
`node_modules`, caches, logs, downloaded evidence, generated exports, and the
local `PEAP Launcher.app`. The checked-in `assets/runtime_config.json` is a
portable default baseline; it contains no machine-specific workspace path and
is kept structurally in sync with `assets/runtime_config.template.json`.

The runtime-source profile remains source-only. A new macOS machine using that
profile directly needs `uv`, Node.js 18+, and npm. Run
`bash scripts/bootstrap_desktop_env.sh` in the staged tree before
`bash start.sh`; the bootstrap uses `uv.lock`, `frontend/package-lock.json`,
and the Playwright browser version selected by the Python lock.

The Apple Silicon offline app is a separate generated artifact. It supports
Apple Silicon Macs running macOS 14.0 or newer. Build it only
after the development release gate passes:

```bash
uv run python scripts/build_offline_app.py --output "release/PEAP Launcher.app"
uv run python scripts/validate_offline_app.py --execute --require-arm64-host \
  "release/PEAP Launcher.app"
```

`packaging/offline-app.json` locks Python and the official Node arm64 archive,
including its SHA-256 digest. The builder stages Project source, installs the
Python runtime closure from `desktop_backend/requirements.lock.txt`, runs
`npm ci` from the frontend lock, installs the matching Playwright Chromium,
then validates and signs the complete bundle. It writes only under the chosen
output and build cache; runtimes, browser binaries, generated `node_modules`,
and user workspace data are never checked into the repository. `--offline`
requires all supplied/cache inputs to exist and fails instead of downloading.

The generated bundle is currently ad-hoc signed. On a machine with Gatekeeper
enabled, the first launch may require Finder's **Open** confirmation. A
no-prompt public release additionally needs a Developer ID signature and Apple
notarization; those credentials are intentionally not embedded in this
repository.

For end users, launch the `.app` itself. `Contents/Resources/run.sh` is an
internal implementation entry point and should not be kept as a desktop
shortcut. The graphical launcher starts it in a clean environment, so stale
developer/test `PEAP_LAUNCHER_*` variables cannot redirect the editable source
or leave the app in initialization-only mode. On first launch it copies the
editable source to `~/Documents/PEAP/source/<release-id>` and keeps later user
edits intact.

Run `scripts/check_release_gate.py` in the development tree before staging.
The runtime-source profile deliberately excludes `tests/` and
`frontend/tests/`, so the staged copy is not a replacement release-gate
workspace even though the gate script and release metadata remain available
for traceability.
