from __future__ import annotations

import pytest

from peap.export_projection import (
    EXPORT_EMPTY_PLACEHOLDERS,
    ExportProjectionError,
    is_export_empty_value,
    project_canonical_record_to_export_payload,
)


def _complete_listing_record() -> dict[str, object]:
    return {
        "canonical_fields": {
            "project_code": "G32026SH1000001",
            "project_name": "规范项目",
            "status": "挂牌中",
            "start_date": "2026-03-21",
            "price": "108.00",
            "seller": "上海测试公司",
        },
    }


def test_missing_identity_and_family_defaults_to_listing_for_compatibility() -> None:
    payload, findings = project_canonical_record_to_export_payload(
        _complete_listing_record(),
        fail_on_missing=True,
    )

    assert findings == ()
    assert payload["项目编号"] == "G32026SH1000001"
    assert payload["挂牌开始日期"] == "2026-03-21"


@pytest.mark.parametrize("identity_field", ["business_identity", "source_identity"])
def test_explicit_non_object_identity_is_rejected_instead_of_empty_identity(
    identity_field: str,
) -> None:
    canonical_record = {
        **_complete_listing_record(),
        "record_family": "listing",
        identity_field: "oops",
    }

    with pytest.raises(ExportProjectionError) as exc_info:
        project_canonical_record_to_export_payload(canonical_record, fail_on_missing=False)

    assert exc_info.value.failure_code == "invalid_identity_shape"
    assert f"{identity_field} must be an object" in str(exc_info.value)


def test_missing_required_canonical_field_is_not_filled_from_export_extras() -> None:
    canonical_record = {
        "record_family": "listing",
        "canonical_fields": {
            "project_code": "G32026SH1000001",
            "project_name": "规范项目",
            "status": "挂牌中",
            "start_date": "2026-03-21",
            "price": "108.00",
            "seller": "",
        },
        "export_extras": {
            "转让方": "不应回填的旧转让方",
        },
    }

    payload, findings = project_canonical_record_to_export_payload(
        canonical_record,
        fail_on_missing=False,
    )

    assert "转让方" not in payload
    assert len(findings) == 1
    assert findings[0].type == "canonical_field_missing"
    assert findings[0].evidence["missing_fields"] == ["seller"]

    with pytest.raises(ExportProjectionError) as exc_info:
        project_canonical_record_to_export_payload(canonical_record, fail_on_missing=True)

    assert exc_info.value.failure_code == "canonical_field_missing"
    assert exc_info.value.missing_fields == (
        {
            "kind": "canonical",
            "field": "seller",
            "canonical_field": "seller",
            "export_field": "",
            "message": "canonical field seller is required",
        },
    )


@pytest.mark.parametrize("placeholder", sorted(EXPORT_EMPTY_PLACEHOLDERS))
def test_export_placeholders_are_missing_at_projection_readiness_boundary(
    placeholder: str,
) -> None:
    canonical_record = _complete_listing_record()
    canonical_record["canonical_fields"]["price"] = placeholder

    payload, findings = project_canonical_record_to_export_payload(
        canonical_record,
        fail_on_missing=False,
    )

    assert is_export_empty_value(placeholder)
    assert "挂牌价格" not in payload
    assert [item.type for item in findings] == ["canonical_field_missing"]
    assert findings[0].evidence["missing_fields"] == ["price"]
