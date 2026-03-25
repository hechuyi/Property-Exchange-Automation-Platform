"""Path-based classification helpers."""

from typing import Tuple

from .constants import (
    STATUS_DEAL,
    STATUS_LISTED,
    TYPE_CAPITAL_INCREASE,
    TYPE_EQUITY_TRANSFER,
    TYPE_PHYSICAL_ASSET,
    TYPE_PRE_DISCLOSURE,
    TYPE_UNKNOWN,
)


def _normalize_path(file_path: str) -> str:
    return file_path.lower().replace(" ", "")


def detect_category_from_path(file_path: str) -> Tuple[str, str]:
    """Infer status and project type from folder path."""
    path_lower = file_path.lower()
    path_normalized = _normalize_path(file_path)

    if f"{STATUS_DEAL}_" in path_lower:
        status = STATUS_DEAL
    elif f"{STATUS_LISTED}_" in path_lower:
        status = STATUS_LISTED
    else:
        status = STATUS_LISTED

    if TYPE_EQUITY_TRANSFER in path_lower or TYPE_EQUITY_TRANSFER in path_normalized:
        project_type = TYPE_EQUITY_TRANSFER
    elif TYPE_PHYSICAL_ASSET in path_lower or TYPE_PHYSICAL_ASSET in path_normalized:
        project_type = TYPE_PHYSICAL_ASSET
    elif TYPE_CAPITAL_INCREASE in path_lower or TYPE_CAPITAL_INCREASE in path_normalized:
        project_type = TYPE_CAPITAL_INCREASE
    elif TYPE_PRE_DISCLOSURE in path_lower or TYPE_PRE_DISCLOSURE in path_normalized:
        project_type = TYPE_PRE_DISCLOSURE
    else:
        project_type = TYPE_UNKNOWN

    return status, project_type
