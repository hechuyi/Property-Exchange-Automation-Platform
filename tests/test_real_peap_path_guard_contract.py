from __future__ import annotations

from pathlib import Path


def test_real_peap_path_guard_avoids_resolve_based_filesystem_probes() -> None:
    source = Path("scripts/_paths.py").read_text(encoding="utf-8")

    assert "/Users/" not in source
    assert "C:\\Users\\" not in source
    assert ".resolve(strict=False)" not in source


def test_maintenance_scripts_do_not_embed_a_developer_home() -> None:
    for script_name in (
        "collect_public_resource_deal_supplements.py",
        "live_truth_audit.py",
        "ui_interaction_audit.py",
        "worktree_delivery_guard.py",
    ):
        source = Path("scripts", script_name).read_text(encoding="utf-8")
        assert "/Users/" not in source
        assert "C:\\Users\\" not in source
