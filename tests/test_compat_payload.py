"""Anti-compat tests: verify legacy compatibility surfaces are deleted."""

from __future__ import annotations

import unittest


class CompatPayloadAntiCompatTest(unittest.TestCase):
    """Tests that verify compat_payload module is not used in production runtime."""

    def test_peap_compat_payload_module_does_not_exist(self) -> None:
        """The peap.compat_payload module must be deleted."""
        with self.assertRaises(ImportError):
            from peap import compat_payload  # type: ignore

    def test_standard_model_does_not_import_compat_payload(self) -> None:
        """standard_model.py must not import from peap.compat_payload."""
        import ast
        from pathlib import Path

        source_path = Path(__file__).resolve().parents[1] / "peap" / "standard_model.py"
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "compat_payload" in node.module:
                    self.fail(f"standard_model.py imports from compat_payload: {node.module}")

    def test_output_mapping_does_not_import_compat_payload(self) -> None:
        """output_mapping.py must not import from peap.compat_payload."""
        import ast
        from pathlib import Path

        source_path = Path(__file__).resolve().parents[1] / "peap" / "output_mapping.py"
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "compat_payload" in node.module:
                    self.fail(f"output_mapping.py imports from compat_payload: {node.module}")

    def test_checks_does_not_import_compat_payload(self) -> None:
        """checks.py must not import from peap.compat_payload."""
        import ast
        from pathlib import Path

        source_path = Path(__file__).resolve().parents[1] / "peap" / "checks.py"
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "compat_payload" in node.module:
                    self.fail(f"checks.py imports from compat_payload: {node.module}")

    def test_streaming_export_does_not_import_compat_payload(self) -> None:
        """streaming_export.py must not import from peap.compat_payload."""
        import ast
        from pathlib import Path

        source_path = Path(__file__).resolve().parents[1] / "peap" / "streaming_export.py"
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "compat_payload" in node.module:
                    self.fail(f"streaming_export.py imports from compat_payload: {node.module}")

    def test_parsing_does_not_have_to_compat_payload_method(self) -> None:
        """ParsedProject must not expose to_compat_payload() for runtime use."""
        from peap.parsing import ParsedProject

        self.assertFalse(
            hasattr(ParsedProject, "to_compat_payload"),
            "ParsedProject must NOT expose to_compat_payload() method",
        )

    def test_no_runtime_imports_peap_record_projection(self) -> None:
        """Production runtime must not import peap.record_projection."""
        import ast
        from pathlib import Path

        peap_dir = Path(__file__).resolve().parents[1] / "peap"
        for source_path in peap_dir.glob("*.py"):
            try:
                source = source_path.read_text(encoding="utf-8")
                # Remove BOM if present
                if source.startswith("\ufeff"):
                    source = source[1:]
                tree = ast.parse(source)
            except SyntaxError:
                # Skip files with syntax errors (e.g., BOM issues)
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    if node.module and "record_projection" in node.module:
                        self.fail(f"{source_path.name} imports from record_projection: {node.module}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if "record_projection" in alias.name:
                            self.fail(f"{source_path.name} imports record_projection: {alias.name}")


if __name__ == "__main__":
    unittest.main()
