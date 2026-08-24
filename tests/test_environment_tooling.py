from __future__ import annotations

import importlib
import json
import os
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


class EnvironmentToolingTest(unittest.TestCase):
    def test_pyproject_uses_uv_dependency_groups(self) -> None:
        pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

        self.assertEqual(pyproject["tool"]["uv"]["default-groups"], ["dev"])
        self.assertIn("dev", pyproject["dependency-groups"])
        self.assertTrue(
            any(str(dep).startswith("pytest") for dep in pyproject["dependency-groups"]["dev"])
        )
        self.assertTrue(
            any(str(dep).startswith("ruff") for dep in pyproject["dependency-groups"]["dev"])
        )
        self.assertNotIn("build", pyproject["dependency-groups"])
        self.assertNotIn("optional-dependencies", pyproject["project"])

    def test_pyproject_exports_shared_runtime_packages_for_non_parser_contracts(self) -> None:
        pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        package_includes = pyproject["tool"]["setuptools"]["packages"]["find"]["include"]
        streaming_store = (REPO_ROOT / "peap" / "streaming_store.py").read_text(encoding="utf-8")

        self.assertIn("from peap_core.record_identity import", streaming_store)
        self.assertIn("peap_core*", package_includes)
        self.assertIn("desktop_backend*", package_includes)

    def test_declared_third_party_runtime_dependencies_are_importable(self) -> None:
        bs4 = importlib.import_module("bs4")
        pandas = importlib.import_module("pandas")

        self.assertEqual(bs4.BeautifulSoup.__module__, "bs4")
        self.assertTrue(pandas.DataFrame.__module__.startswith("pandas"))
        self.assertIn("site-packages", str(getattr(bs4, "__file__", "")))
        self.assertIn("site-packages", str(getattr(pandas, "__file__", "")))

    def test_tests_do_not_need_to_fabricate_declared_runtime_dependencies(self) -> None:
        for relative_path in (
            "tests/test_app_backend.py",
            "tests/test_app_service.py",
            "tests/test_app_service_architecture.py",
            "tests/test_execution_service.py",
            "tests/test_export_service_scope.py",
            "tests/test_exchange_downloader_fixes.py",
            "tests/test_runner_request_adapters.py",
        ):
            source = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
            for dependency in ("bs4", "pandas"):
                write_pattern = 'sys.modules["' + dependency + '"]'
                guard_pattern = f'if "{dependency}" not in ' + "sys.modules"
                self.assertNotIn(write_pattern, source, msg=relative_path)
                self.assertNotIn(guard_pattern, source, msg=relative_path)

    def test_parser_subsystem_contract_modules_stay_runtime_free(self) -> None:
        for relative_path in (
            "peap_core/snapshot_contracts.py",
            "peap_core/page_parse_contracts.py",
            "peap_core/record_contracts.py",
        ):
            contract_file = REPO_ROOT / relative_path
            self.assertTrue(contract_file.exists(), msg=f"missing contract file: {relative_path}")
            source = contract_file.read_text(encoding="utf-8")
            self.assertNotIn("from peap import", source, msg=relative_path)
            self.assertNotIn("import peap", source, msg=relative_path)
            self.assertNotIn("from peap_parsers import", source, msg=relative_path)
            self.assertNotIn("import peap_parsers", source, msg=relative_path)
            self.assertNotIn("from desktop_backend", source, msg=relative_path)
            self.assertNotIn("import desktop_backend", source, msg=relative_path)
            self.assertNotIn("download_runner", source, msg=relative_path)
            self.assertNotIn("download_tasks", source, msg=relative_path)
            self.assertNotIn("download_oneclick", source, msg=relative_path)

        workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

        self.assertRegex(workflow, r"astral-sh/setup-uv@[0-9a-f]{40}\s+#\s*v7")
        self.assertIn("uv python install", workflow)
        self.assertIn("uv lock --check", workflow)
        self.assertIn("uv sync --locked", workflow)
        self.assertIn("uv run ruff check", workflow)
        self.assertIn("uv run python -m pytest tests", workflow)
        self.assertNotIn("pip install -r requirements-dev.lock", workflow)

    def test_desktop_package_workflow_is_removed(self) -> None:
        self.assertFalse((REPO_ROOT / ".github" / "workflows" / "desktop-package.yml").exists())

    def test_bootstrap_script_uses_uv_managed_project_environment(self) -> None:
        script = (REPO_ROOT / "scripts" / "bootstrap_desktop_env.sh").read_text(encoding="utf-8")

        self.assertIn("uv sync --locked", script)
        self.assertIn("MIN_NODE_MAJOR=18", script)
        self.assertIn("command -v node", script)
        self.assertIn("command -v npm", script)
        self.assertIn('(cd "$FRONTEND_DIR" && npm ci)', script)
        self.assertIn("uv run python -m playwright install chromium", script)
        self.assertIn('echo "  python: $VENV_DIR/bin/python"', script)
        self.assertNotIn("pyenv", script)
        self.assertNotIn(".venv-desktop", script)
        self.assertNotIn("pip install", script)

    def test_bootstrap_script_runs_playwright_install_with_cache_environment(self) -> None:
        script_path = REPO_ROOT / "scripts" / "bootstrap_desktop_env.sh"
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            fake_bin = temp_root / "bin"
            fake_bin.mkdir()
            command_log = temp_root / "commands.log"
            workspace_root = temp_root / "workspace"

            fake_uv = fake_bin / "uv"
            fake_uv.write_text(
                "#!/bin/sh\n"
                "printf 'uv|cwd=%s|args=%s|pw=%s|peap=%s\\n' "
                '"$PWD" "$*" "${PLAYWRIGHT_BROWSERS_PATH:-}" '
                '"${PEAP_PLAYWRIGHT_BROWSERS_PATH:-}" >> "$BOOTSTRAP_TEST_LOG"\n',
                encoding="utf-8",
            )
            fake_uv.chmod(0o755)

            fake_node = fake_bin / "node"
            fake_node.write_text(
                "#!/bin/sh\n"
                "case \"${1:-}\" in\n"
                "  -p) printf '20\\n' ;;\n"
                "  --version) printf 'v20.0.0\\n' ;;\n"
                "  *) exit 2 ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            fake_node.chmod(0o755)

            fake_npm = fake_bin / "npm"
            fake_npm.write_text(
                "#!/bin/sh\n"
                "printf 'npm|cwd=%s|args=%s\\n' \"$PWD\" \"$*\" "
                '>> "$BOOTSTRAP_TEST_LOG"\n',
                encoding="utf-8",
            )
            fake_npm.chmod(0o755)

            env = os.environ.copy()
            for name in (
                "PEAP_APP_HOME",
                "PEAP_DOCUMENTS_HOME",
                "PEAP_PLAYWRIGHT_BROWSERS_PATH",
                "PLAYWRIGHT_BROWSERS_PATH",
            ):
                env.pop(name, None)
            env.update(
                {
                    "BOOTSTRAP_TEST_LOG": str(command_log),
                    "HOME": str(temp_root),
                    "PATH": f"{fake_bin}:/usr/bin:/bin",
                    "PEAP_WORKSPACE_ROOT": str(workspace_root),
                }
            )

            completed = subprocess.run(
                ["/bin/bash", str(script_path)],
                cwd=temp_root,
                env=env,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(completed.returncode, 0, msg=completed.stderr)
            calls = command_log.read_text(encoding="utf-8").splitlines()
            browser_cache = workspace_root / "cache" / "ms-playwright"
            self.assertIn(f"uv|cwd={REPO_ROOT}|args=sync --locked|pw=|peap=", calls)
            self.assertIn(f"npm|cwd={REPO_ROOT / 'frontend'}|args=ci", calls)
            self.assertIn(
                f"uv|cwd={REPO_ROOT}|args=run python -m playwright install chromium|"
                f"pw={browser_cache}|peap={browser_cache}",
                calls,
            )

    def test_distribution_manifest_keeps_documented_runtime_inputs(self) -> None:
        manifest = json.loads(
            (REPO_ROOT / "packaging" / "distribution-manifest.json").read_text(
                encoding="utf-8"
            )
        )

        includes = set(manifest["include"])
        self.assertIn("scripts/check_release_gate.py", includes)
        self.assertIn("scripts/bootstrap_desktop_env.sh", includes)
        self.assertIn("scripts/build_offline_app.py", includes)
        self.assertIn("scripts/validate_offline_app.py", includes)
        self.assertIn("packaging/offline-app.json", includes)
        self.assertIn("packaging/launcher-template/**", includes)
        self.assertIn("desktop_backend/requirements.lock.txt", includes)
        self.assertNotIn("assets/**", includes)
        self.assertIn("assets/excel_output_schema.json", includes)
        self.assertIn("assets/runtime_config.json", includes)
        self.assertIn("assets/runtime_config.template.json", includes)
        self.assertIn("uv.lock", includes)
        self.assertIn("frontend/package-lock.json", includes)
        self.assertTrue(
            {
                "scripts/cleanup_archive_conflicts.py",
                "scripts/cleanup_duplicate_source_records.py",
                "scripts/cleanup_missing_source_records.py",
                "scripts/cleanup_sse_bad_snapshots.py",
                "scripts/recover_missing_archive_files.py",
                "scripts/refetch_sse_from_cleanup_manifest.py",
            }.issubset(includes)
        )

    def test_portable_runtime_config_matches_its_template(self) -> None:
        runtime_config = json.loads(
            (REPO_ROOT / "assets" / "runtime_config.json").read_text(encoding="utf-8")
        )
        template = json.loads(
            (REPO_ROOT / "assets" / "runtime_config.template.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(runtime_config, template)
        self.assertEqual(
            runtime_config["paths"]["streaming_db_path"],
            "streaming_ingest.sqlite3",
        )
        self.assertEqual(runtime_config["paths"]["data_root"], "~/Documents/PEAP/data")
        self.assertEqual(runtime_config["paths"]["html_folder"], "../manual")
        self.assertEqual(runtime_config["paths"]["auto_html_folder"], "../archive")
        self.assertEqual(runtime_config["paths"]["output_excel_dir"], "../exports")
        for name, path_value in runtime_config["paths"].items():
            self.assertFalse(Path(path_value).is_absolute(), msg=name)
            self.assertFalse(
                len(path_value) >= 3
                and path_value[0].isalpha()
                and path_value[1] == ":"
                and path_value[2] in {"/", "\\"},
                msg=name,
            )

    def test_plain_runtime_requirements_match_frozen_uv_export(self) -> None:
        completed = subprocess.run(
            ("uv", "export", "--frozen", "--no-dev", "--no-hashes", "--no-emit-project"),
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, msg=completed.stderr)
        actual = (REPO_ROOT / "desktop_backend" / "requirements.lock.txt").read_text(
            encoding="utf-8"
        )
        self.assertEqual(actual, completed.stdout)

    def test_readme_uses_uv_as_the_only_python_workflow(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("uv sync", readme)
        self.assertIn("uv run python -m desktop_backend.app_backend", readme)
        self.assertNotIn("brew install pyenv", readme)
        self.assertNotIn(".venv-desktop", readme)
        self.assertNotIn("requirements.txt", readme)

    def test_active_docs_avoid_retired_packaged_runtime_narrative(self) -> None:
        banned_phrases = ("packaged runtime", "backend sidecar")
        active_docs = (
            REPO_ROOT / "README.md",
            REPO_ROOT / "docs" / "operations.md",
            REPO_ROOT / "docs" / "release-gate.md",
        )

        for doc_path in active_docs:
            text = doc_path.read_text(encoding="utf-8").lower()
            for phrase in banned_phrases:
                self.assertNotIn(phrase, text, msg=f"{doc_path} still contains {phrase!r}")

    def test_frontend_package_exposes_vite_development_workflow(self) -> None:
        package_json = json.loads((REPO_ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
        scripts = package_json["scripts"]

        self.assertEqual(package_json["name"], "peap-frontend")
        self.assertTrue(package_json["private"])
        self.assertEqual(scripts["dev"], "vite")
        self.assertEqual(scripts["build"], "vite build")
        self.assertEqual(scripts["preview"], "vite preview")
        self.assertNotIn("start", scripts)
        self.assertNotIn("build:backend", scripts)
        self.assertNotIn("package:desktop", scripts)
        self.assertNotIn("pack", scripts)
        self.assertNotIn("dist:mac", scripts)
        self.assertNotIn("dist:win", scripts)

    def test_frontend_package_omits_legacy_desktop_dependencies_and_uses_local_backend_proxy(self) -> None:
        package_json = json.loads((REPO_ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
        dev_dependencies = package_json["devDependencies"]
        vite_config = (REPO_ROOT / "frontend" / "vite.config.js").read_text(encoding="utf-8")

        self.assertNotIn("electron-builder", dev_dependencies)
        self.assertNotIn("electron", dev_dependencies)
        self.assertIn('process.env.PEAP_FRONTEND_BACKEND_TARGET', vite_config)
        self.assertIn('"http://127.0.0.1:42679"', vite_config)
        self.assertIn('"/api/"', vite_config)
        self.assertIn('outDir: "../dist"', vite_config)

    def test_start_script_supports_bundled_python_and_isolated_proxy_target(self) -> None:
        script = (REPO_ROOT / "start.sh").read_text(encoding="utf-8")

        self.assertIn('PEAP_BACKEND_HOST="${PEAP_BACKEND_HOST:-127.0.0.1}"', script)
        self.assertIn('PEAP_BACKEND_PORT="${PEAP_BACKEND_PORT:-42679}"', script)
        self.assertIn('PEAP_FRONTEND_PORT="${PEAP_FRONTEND_PORT:-5173}"', script)
        self.assertIn('export PEAP_FRONTEND_BACKEND_TARGET="http://${PEAP_BACKEND_HOST}:${PEAP_BACKEND_PORT}"', script)
        self.assertIn('requested_python="${PEAP_PYTHON:-}"', script)
        self.assertIn('PYTHON_COMMAND=("$requested_python")', script)
        self.assertIn("PYTHON_COMMAND=(uv run python)", script)
        self.assertIn(
            '"${PYTHON_COMMAND[@]}" -m desktop_backend.app_backend '
            '--host "$PEAP_BACKEND_HOST" --port "$PEAP_BACKEND_PORT"',
            script,
        )
        self.assertIn(
            'printf \'%s\' "$payload" | "${PYTHON_COMMAND[@]}" -c',
            script,
        )
        self.assertIn('npm run dev -- --host 127.0.0.1 --port "$PEAP_FRONTEND_PORT"', script)
        self.assertIn("--strictPort", script)
        self.assertIn("cleanup()", script)
        self.assertNotIn("replace_existing_backend_if_owned()", script)
        self.assertNotIn("replace_existing_frontend_if_owned()", script)
        self.assertNotIn("stop_owned_port_processes()", script)
        self.assertIn("acquire_launcher_lock()", script)
        self.assertIn("release_launcher_lock()", script)
        self.assertIn('LAUNCHER_LOCK_DIR="${PEAP_RUNTIME_LOCK_ROOT}/launcher.lock"', script)
        self.assertLess(script.index("acquire_launcher_lock\n"), script.index('require_free_port "$PEAP_BACKEND_PORT"'))
        self.assertIn('require_free_port "$PEAP_FRONTEND_PORT" "Frontend"', script)
        self.assertIn('kill_tree "$FRONTEND_PID" TERM "$FRONTEND_START_IDENTITY"', script)
        self.assertIn('kill_tree "$BACKEND_PID" TERM "$BACKEND_START_IDENTITY"', script)
        self.assertIn("trap cleanup EXIT INT TERM", script)
        self.assertIn("wait_for_ready", script)
        self.assertIn("ready_payload_ok", script)
        self.assertIn('bool(data.get("ok"))', script)
        self.assertIn('bool(schema.get("ready"))', script)
        self.assertIn("/api/ready", script)
        self.assertNotIn("\nwait\n", script)
        self.assertNotIn("source .venv/bin/activate", script)


if __name__ == "__main__":
    unittest.main()
