from __future__ import annotations

from typing import Any, Mapping

from peap.compat_payload import build_compat_payload
from peap.standard_model import hydrate_standard_project
from peap_core import CanonicalRecord


def _canonical_fields_from_payload(canonical: CanonicalRecord | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(canonical, CanonicalRecord):
        return canonical.canonical_fields
    if isinstance(canonical, Mapping):
        nested = canonical.get("canonical_fields")
        if isinstance(nested, Mapping):
            return nested
        return canonical
    return {}


def project_canonical_record_to_compat_payload(canonical: CanonicalRecord | Mapping[str, Any]) -> dict[str, object]:
    standard = hydrate_standard_project(dict(_canonical_fields_from_payload(canonical)))
    return build_compat_payload(standard)


__all__ = ["project_canonical_record_to_compat_payload"]
