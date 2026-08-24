from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_python_probe(script: str) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "probe failed\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    return json.loads(completed.stdout)


class DependencyIsolationRegressionTest(unittest.TestCase):
    def test_importing_former_stub_owner_before_parser_registry_keeps_real_bs4(self) -> None:
        probe = _run_python_probe(
            textwrap.dedent(
                f"""
                import importlib
                import json
                import sys

                sys.path.insert(0, {str(REPO_ROOT)!r})

                importlib.import_module("tests.test_app_backend")
                importlib.import_module("tests.test_parser_registry")

                from peap_parsers import base as parser_base
                from peap_core import snapshot_contracts
                from peap_parsers import snapshot_decoder, source_detection_rules

                print(json.dumps({{
                    "parser_base": parser_base.BeautifulSoup.__module__,
                    "snapshot_contracts": snapshot_contracts.BeautifulSoup.__module__,
                    "snapshot_decoder": snapshot_decoder.BeautifulSoup.__module__,
                    "source_detection_rules": source_detection_rules.BeautifulSoup.__module__,
                }}))
                """
            )
        )

        self.assertEqual(probe["parser_base"], "bs4")
        self.assertEqual(probe["snapshot_contracts"], "bs4")
        self.assertEqual(probe["snapshot_decoder"], "bs4")
        self.assertEqual(probe["source_detection_rules"], "bs4")

    def test_importing_parser_registry_before_former_stub_owner_stays_on_real_bs4(self) -> None:
        probe = _run_python_probe(
            textwrap.dedent(
                f"""
                import importlib
                import json
                import sys

                sys.path.insert(0, {str(REPO_ROOT)!r})

                importlib.import_module("tests.test_parser_registry")
                importlib.import_module("tests.test_app_backend")

                from peap_parsers import base as parser_base

                print(json.dumps({{
                    "parser_base": parser_base.BeautifulSoup.__module__,
                }}))
                """
            )
        )

        self.assertEqual(probe["parser_base"], "bs4")

    def test_importing_former_pandas_stub_owner_keeps_real_distribution_for_consumers(self) -> None:
        probe = _run_python_probe(
            textwrap.dedent(
                f"""
                import importlib
                import json
                import sys

                sys.path.insert(0, {str(REPO_ROOT)!r})

                importlib.import_module("tests.test_runner_request_adapters")
                importlib.import_module("tests.test_public_resource_deals_settings")
                importlib.import_module("tests.test_excel_schema_settings")

                import pandas
                from peap import excel_handler, public_resource_deals

                print(json.dumps({{
                    "pandas_origin": getattr(pandas, "__file__", ""),
                    "dataframe_module": pandas.DataFrame.__module__,
                    "public_resource_origin": getattr(public_resource_deals.pd, "__file__", ""),
                    "public_resource_dataframe_module": public_resource_deals.pd.DataFrame.__module__,
                    "excel_handler_origin": getattr(excel_handler.pd, "__file__", ""),
                    "excel_handler_dataframe_module": excel_handler.pd.DataFrame.__module__,
                }}))
                """
            )
        )

        self.assertIn("pandas", str(probe["pandas_origin"]))
        self.assertTrue(str(probe["dataframe_module"]).startswith("pandas"))
        self.assertIn("pandas", str(probe["public_resource_origin"]))
        self.assertTrue(str(probe["public_resource_dataframe_module"]).startswith("pandas"))
        self.assertIn("pandas", str(probe["excel_handler_origin"]))
        self.assertTrue(str(probe["excel_handler_dataframe_module"]).startswith("pandas"))


if __name__ == "__main__":
    unittest.main()
