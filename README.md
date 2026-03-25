# Property Exchange Automation Platform

Parser V2 + Downloader + PostProcess (rebuilt branch)

## Current Status

This repo is now split into two layers:

- `desktop_app/` + `desktop_backend/`: the product-facing desktop application under active development
- `peap/` + `peap_parsers/` + `peap_postprocess/`: the engine/core still used by the desktop app

Important boundary:

- `desktop_backend/` is allowed to orchestrate product behavior and app-local storage
- `peap/` remains the engine/core and is still required by the desktop app
- the old source-tree CLI is now legacy and should not be treated as the main product path

Do not delete yet:

- `peap/streaming_*`
- `peap/download_*`
- `peap/parsing.py`
- `peap_postprocess/`
- `peap_parsers/`

Those modules are still on the live path for the desktop app.

## Desktop Product

The current product-facing path is the standalone desktop app under `desktop_app/`. It no longer requires the legacy runtime JSON config for local startup.

```bash
# First, install pyenv on macOS
brew install pyenv

# Then build the isolated desktop backend environment
bash scripts/bootstrap_desktop_env.sh

# Electron shell
cd desktop_app
npm install
npm start
```

The desktop backend now uses one operator-visible workspace root by default:

- Workspace root: `PEAP_WORKSPACE_ROOT` / `PEAP_APP_HOME` / `PEAP_DOCUMENTS_HOME`, otherwise the platform documents folder under `PEAP`
- SQLite store: `<workspace_root>/data/streaming_ingest.sqlite3`
- Auto-saved web archives: `<workspace_root>/submission/`
- Manual import staging: `<workspace_root>/data/raw/manual/`
- Export root: `<workspace_root>/exports/`
- Logs: `<workspace_root>/logs/`
- Cache and browser runtime: `<workspace_root>/cache/` and `<workspace_root>/cache/ms-playwright/`

If an older desktop run left data under the legacy app-data root or repo-local `.cache/ms-playwright`, startup now merges that content into the unified workspace automatically when no per-path override is set.

For backend-only local debugging:

```bash
.venv-desktop/bin/python -m desktop_backend.app_backend --host 127.0.0.1 --port 42679
```

Storage layout and override variables are documented in `docs/desktop_storage_layout.md`.

Release packaging now uses a bundled backend sidecar instead of assuming a system Python at runtime:

```bash
cd desktop_app
npm install

# macOS runner: builds backend sidecar, then packages an unsigned .pkg
npm run dist:mac

# Windows runner: builds backend sidecar, then packages an unsigned NSIS .exe
npm run dist:win
```

The package scripts build `desktop_backend/app_backend.py` into `desktop_app/build/desktop_backend/peap-desktop-backend*`
with PyInstaller, then copy that binary into the Electron app as an extra resource. By default:

- dev launch uses repo-local `.venv-desktop`
- packaged launch uses `process.resourcesPath/desktop_backend/peap-desktop-backend*`
- Playwright browser cache is mirrored into both `PLAYWRIGHT_BROWSERS_PATH` and `PEAP_PLAYWRIGHT_BROWSERS_PATH` so backend config and runtime stay aligned

Native CI packaging is wired in `.github/workflows/desktop-package.yml` and uploads unsigned `.pkg` / `.exe` artifacts.

If Chromium is not present in the configured browser cache, the desktop app now exposes:

- settings page buttons to detect / install Chromium
- backend CLI fallback: `.venv-desktop/bin/python -m desktop_backend.app_backend --install-browser`
- startup readiness gate that disables download actions until Chromium is ready
- automatic first-run background install attempt when Chromium is missing

The backend install path uses Playwright's bundled driver directly and installs into the same `PLAYWRIGHT_BROWSERS_PATH` / `PEAP_PLAYWRIGHT_BROWSERS_PATH` cache directory used by the app runtime.

The desktop shell is now aligned to a business-facing operator UI:

- app branding is `产权交易所自动录入`
- one-click execution is the only primary action and always respects the selected date range
- one-click jobs save pages directly into the canonical archive tree instead of writing raw downloads and copying them later
- saved mapping rules now trigger background `mapping_refresh` jobs for all affected latest records, not only the row currently visible in the browser
- the desktop app now exposes a real `手动导入解析` job that recursively ingests local `html` / `htm` / `mhtml` files through the same parser and postprocess path used by downloader-produced pages
- `SkipParse` pages such as `skip-cbex-otc-page` are surfaced as skipped items instead of hard failures
- the record list and manual export both render fields through the same output contract used by the CLI/export pipeline
- the task sidebar is now separate from the records table instead of being buried inside the records panel

## Resume Here

If starting a new conversation, the next high-level tasks are:

1. Install `pyenv` on macOS and run `bash scripts/bootstrap_desktop_env.sh` to create `.venv-desktop/`.
2. Run the new `desktop_app` package flow on native macOS / Windows runners and verify the produced installers end-to-end.
3. Validate the real first-run UX on packaged builds:
   - does auto-install start successfully on a clean machine
   - does the startup gate recover to ready after Chromium finishes downloading
   - are failure messages actionable under network/proxy restrictions
4. Only after the desktop app no longer depends on legacy CLI wrappers, delete obsolete CLI shells carefully.

The current storage model is one workspace root:

- workspace root: `~/Documents/PEAP/` by default
- database: `<workspace_root>/data/streaming_ingest.sqlite3`
- auto-saved archive html: `<workspace_root>/submission/`
- manual import staging: `<workspace_root>/data/raw/manual/`
- logs: `<workspace_root>/logs/`
- cache: `<workspace_root>/cache/`
- browser cache: `<workspace_root>/cache/ms-playwright/`
- excel export: `<workspace_root>/exports/`

Every category above supports an explicit env override. See `docs/desktop_storage_layout.md`.

## Product Boundary

The desktop product is now the only supported operator workflow.

- Supported product entry: `desktop_app/` + `desktop_backend/`
- Internal engine modules retained for desktop runtime: `peap/`, `peap_parsers/`, `peap_postprocess/`
- Removed product surface: source-tree legacy CLI wrappers under `bin/`

If you need archive handoff or engine-level debugging, use the remaining internal modules and scripts deliberately. They are no longer documented as the primary way to run the product.

## Project Layout

```text
.
├─ desktop_app/                 # Electron desktop shell
├─ desktop_backend/             # local backend for desktop product
├─ peap/                        # parser/downloader core
├─ peap_parsers/                # exchange-specific parsers
├─ peap_postprocess/            # postprocess engine
├─ scripts/                     # internal maintenance scripts
├─ assets/                      # static templates (excel schema, etc.)
├─ docs/                        # design and planning docs
└─ requirements.txt
```

## File Submission Workflow

### Prepare Files for Submission/Archive

After downloading HTML pages, you can use the submission preparation script to create a clean submission package:

```powershell
# Prepare submission package
python scripts/prepare_submission.py
```

**What it does:**
- Scans all HTML files in `<data_root>/raw/auto`
- Extracts project code and name mapping from:
  1. Parser Excel outputs (if available)
  2. HTML metadata JSON files
  3. HTML filenames (as fallback)
- Copies files to `<data_root>/outputs/submission`
- Renames files to format: `[ProjectCode]-[ProjectName].html`
- Copies associated `_files` folders (containing resources)
- Generates `_manifest.json` with submission details

**Output structure:**
```
<data_root>/outputs/submission/
├─ G32025SH1000194-上海电气集团恒联企业发展有限公司35%股权.html
├─ G32025SH1000194-上海电气集团恒联企业发展有限公司35%股权_files/
├─ [other projects...]
└─ _manifest.json
```

For more details, see [docs/project_layout.md](docs/project_layout.md#7-提交流程).

## Key Docs

- Structure and data-root rules: `docs/project_layout.md`
- Development plan: `docs/development_plan.md`
- Parser rule risk report: `docs/parser_rule_risk_report.md`
- PPE user guide: `peap_postprocess/ppe_business_user_guide.md`

## Notes

- In rebuilt mode, downloader output under project root is blocked.
- Runtime config template: `assets/runtime_config.template.json`.
- Parser output layout is driven by `assets/excel_output_schema.json`.
- Parser excel outputs path is configured by `paths.output_excel_dir` in the active runtime config file.
- Excel output schema path is configured by `paths.excel_schema_file` in the active runtime config file.
- Parser/downloader/daily CLI log level and file logging are controlled by `logging.level` and `logging.to_file` in the active runtime config file.
- Runtime config file can be switched by env `PEAP_RUNTIME_CONFIG_FILE`.
- Streaming mode writes a sqlite store at `<log_dir>/streaming_ingest.sqlite3` by default (override with `--streaming-db`).
- In streaming mode, downloaded snapshots are copied into `<data_root>/outputs/submission/<YYYY年M月>/`.
