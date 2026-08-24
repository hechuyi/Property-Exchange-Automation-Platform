from __future__ import annotations

import pytest

from desktop_backend.runtime_contract import build_runtime_view


def test_build_runtime_view_rejects_non_mapping_payload() -> None:
    with pytest.raises(ValueError, match="runtime payload must be an object"):
        build_runtime_view([])
