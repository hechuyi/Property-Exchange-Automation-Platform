from __future__ import annotations

from pathlib import Path

from scripts import cleanup_archive_conflicts as cleanup


def test_plan_reports_uninspectable_archive_subtree(monkeypatch, tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    readable_snapshot = archive_root / "readable.html"
    readable_snapshot.write_text("<html>readable</html>", encoding="utf-8")
    unreadable_subtree = archive_root / "locked"

    def fake_walk(path: Path, onerror=None):
        assert Path(path) == archive_root
        yield str(archive_root), ["locked"], ["readable.html"]
        if onerror is not None:
            error = PermissionError("permission denied")
            error.filename = str(unreadable_subtree)
            onerror(error)

    monkeypatch.setattr(cleanup.os, "walk", fake_walk)

    manifest = cleanup.plan_archive_conflicts(archive_root)

    assert manifest["mode"] == "report_only"
    assert manifest["destructive"] is False
    assert manifest["conflict_group_count"] == 0
    assert manifest["actions"] == []
    assert manifest["uninspectable_path_count"] == 1
    assert manifest["uninspectable_paths"] == [
        {
            "path": str(unreadable_subtree),
            "error": {
                "type": "PermissionError",
                "message": "permission denied",
            },
        }
    ]
