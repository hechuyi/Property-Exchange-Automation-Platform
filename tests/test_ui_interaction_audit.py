from __future__ import annotations

import ast
from pathlib import Path


def test_ui_interaction_audit_uses_shared_browser_runtime_fallback() -> None:
    source = Path("scripts/ui_interaction_audit.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported_helper = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "peap.browser_runtime"
        and any(alias.name == "launch_chromium_browser_sync" for alias in node.names)
        for node in ast.walk(tree)
    )
    helper_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "launch_chromium_browser_sync"
    ]

    assert imported_helper
    assert helper_calls
    assert "p.chromium.launch(" not in source
