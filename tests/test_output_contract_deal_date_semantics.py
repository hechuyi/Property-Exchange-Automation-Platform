from __future__ import annotations

from peap.output_contract import (
    KIND_DEAL_CAPITAL,
    KIND_DEAL_EQUITY,
    KIND_DEAL_PHYSICAL,
    KIND_PUBLIC_RESOURCE,
    get_output_columns_for_kind,
    list_deal_workbook_sheet_specs,
)


def test_all_deal_workbook_sheets_with_deal_date_have_visible_remark_column() -> None:
    for kind in (KIND_DEAL_EQUITY, KIND_DEAL_PHYSICAL, KIND_DEAL_CAPITAL):
        for spec in list_deal_workbook_sheet_specs(kind):
            if "成交日期" not in spec.headers:
                continue
            assert "备注" in spec.headers, f"{kind}:{spec.source_id}:{spec.sheet_name} lacks 备注"


def test_public_resource_deal_output_has_visible_remark_column_for_imputed_deal_dates() -> None:
    columns = get_output_columns_for_kind(KIND_PUBLIC_RESOURCE)

    assert "成交日期" in columns
    assert "备注" in columns
