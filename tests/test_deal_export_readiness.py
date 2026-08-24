import unittest
from unittest.mock import patch

from peap.export_projection import ExportProjectionError, project_canonical_record_to_export_payload
from peap.streaming_export import _ensure_exportable_payload


def _deal_record(
    business_id: str,
    *,
    canonical_fields: dict[str, object] | None = None,
    export_extras: dict[str, object] | None = None,
) -> dict[str, object]:
    fields: dict[str, object] = {
        "project_code": "G32026TEST0001",
        "project_name": "成交测试项目",
        "status": "已成交",
        "deal_date": "2026-04-20",
    }
    fields.update(canonical_fields or {})
    return {
        "record_family": "deal",
        "business_identity": {"business_id": business_id},
        "source_identity": {"source_id": "sse"},
        "canonical_fields": fields,
        "export_extras": export_extras or {},
    }


class DealExportReadinessTest(unittest.TestCase):
    def test_equity_deal_requires_deal_price_but_not_valuation_or_reserve_price(self) -> None:
        record = _deal_record("deal_equity_transfer")

        with self.assertRaises(ExportProjectionError) as raised:
            project_canonical_record_to_export_payload(record, fail_on_missing=True)

        self.assertIn("deal_price", str(raised.exception))
        _payload, findings = project_canonical_record_to_export_payload(record, fail_on_missing=False)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "warn")
        self.assertEqual(findings[0].evidence["missing_fields"], ["deal_price"])

        ready_record = _deal_record(
            "deal_equity_transfer",
            canonical_fields={"deal_price": "1200", "deal_price_unit_basis": "raw_unit"},
        )
        _payload, findings = project_canonical_record_to_export_payload(ready_record, fail_on_missing=True)
        self.assertEqual(findings, ())

    def test_dict_canonical_record_derives_deal_family_from_business_identity(self) -> None:
        record = _deal_record("deal_equity_transfer")
        record.pop("record_family")
        record["business_identity"] = {
            "record_family": "deal",
            "business_id": "deal_equity_transfer",
        }

        with self.assertRaises(ExportProjectionError) as raised:
            project_canonical_record_to_export_payload(record, fail_on_missing=True)

        message = str(raised.exception)
        missing_fields = {item["canonical_field"] for item in raised.exception.missing_fields}
        self.assertIn("deal_price", message)
        self.assertNotIn("start_date", missing_fields)
        self.assertNotIn("price", missing_fields)
        self.assertNotIn("seller", missing_fields)

        ready_record = dict(record)
        ready_record["canonical_fields"] = {
            **dict(record["canonical_fields"]),
            "deal_price": "1200",
            "deal_price_unit_basis": "raw_unit",
        }
        _payload, findings = project_canonical_record_to_export_payload(ready_record, fail_on_missing=True)
        self.assertEqual(findings, ())

    def test_dict_canonical_record_derives_deal_family_from_source_identity_fallback(self) -> None:
        record = _deal_record(
            "deal_equity_transfer",
            canonical_fields={"deal_price": "1200", "deal_price_unit_basis": "raw_unit"},
        )
        record.pop("record_family")
        record["business_identity"] = {"business_id": "deal_equity_transfer"}
        record["source_identity"] = {"source_id": "sse", "record_family": "deal"}

        _payload, findings = project_canonical_record_to_export_payload(record, fail_on_missing=True)

        self.assertEqual(findings, ())

    def test_equity_deal_price_accepts_default_wan_unit_basis(self) -> None:
        record = _deal_record(
            "deal_equity_transfer",
            canonical_fields={"deal_price": "1200", "deal_price_unit_basis": "default_wan"},
        )

        _payload, findings = project_canonical_record_to_export_payload(record, fail_on_missing=False)

        self.assertEqual(findings, ())

    def test_equity_deal_price_accepts_field_and_page_unit_evidence(self) -> None:
        accepted_bases = (
            "field_unit_wan",
            "converted_from_field_yuan",
            "converted_from_field_yi_yuan",
        )

        for basis in accepted_bases:
            with self.subTest(basis=basis):
                record = _deal_record(
                    "deal_equity_transfer",
                    canonical_fields={"deal_price": "1200", "deal_price_unit_basis": basis},
                )

                _payload, findings = project_canonical_record_to_export_payload(record, fail_on_missing=False)

                self.assertEqual(findings, ())

    def test_deal_date_can_be_audited_collection_date_when_imputed(self) -> None:
        record = _deal_record(
            "deal_physical_asset",
            canonical_fields={
                "deal_date": "",
                "collection_date": "2026-04-20",
                "deal_date_is_imputed": True,
                "deal_price": "320",
                "deal_price_unit_basis": "raw_unit",
            },
        )

        _payload, findings = project_canonical_record_to_export_payload(record, fail_on_missing=True)
        self.assertEqual(findings, ())

    def test_imputed_collection_date_deal_date_requires_collection_date_audit_evidence(self) -> None:
        record = _deal_record(
            "deal_physical_asset",
            canonical_fields={
                "deal_date": "2026-04-20",
                "collection_date": "",
                "deal_date_basis": "collection_date",
                "deal_date_is_imputed": True,
                "deal_price": "320",
                "deal_price_unit_basis": "raw_unit",
            },
        )

        _payload, findings = project_canonical_record_to_export_payload(record, fail_on_missing=False)

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].evidence["missing_fields"], ["deal_date_or_collection_date_audit"])

    def test_real_non_imputed_deal_date_is_export_ready_without_collection_date(self) -> None:
        record = _deal_record(
            "deal_physical_asset",
            canonical_fields={
                "deal_date": "2026-04-20",
                "collection_date": "",
                "deal_date_basis": "deal_date",
                "deal_date_is_imputed": False,
                "deal_price": "320",
                "deal_price_unit_basis": "raw_unit",
            },
        )

        _payload, findings = project_canonical_record_to_export_payload(record, fail_on_missing=True)

        self.assertEqual(findings, ())

    def test_missing_deal_date_audit_is_reported_as_stable_missing_item(self) -> None:
        record = _deal_record(
            "deal_physical_asset",
            canonical_fields={
                "deal_date": "",
                "collection_date": "2026-04-20",
                "deal_date_is_imputed": False,
                "deal_date_basis": "",
                "deal_price": "320",
                "deal_price_unit_basis": "raw_unit",
            },
        )

        _payload, findings = project_canonical_record_to_export_payload(record, fail_on_missing=False)

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].evidence["missing_fields"], ["deal_date_or_collection_date_audit"])

    def test_capital_increase_requires_valid_investor_name_and_amount_not_deal_price(self) -> None:
        record = _deal_record(
            "deal_capital_increase",
            canonical_fields={"deal_price": ""},
            export_extras={
                "investors": [
                    {"name": "投资方甲", "amount": ""},
                    {"name": "", "amount": "800"},
                ]
            },
        )

        with self.assertRaises(ExportProjectionError) as raised:
            project_canonical_record_to_export_payload(record, fail_on_missing=True)

        message = str(raised.exception)
        self.assertIn("investors[0].投资金额（万元）", message)
        self.assertIn("investors[1].投资方名称", message)
        self.assertNotIn("deal_price", message)

        ready_record = _deal_record(
            "deal_capital_increase",
            canonical_fields={"deal_price": ""},
            export_extras={"investors": [{"name": "投资方甲", "amount": "800"}]},
        )
        _payload, findings = project_canonical_record_to_export_payload(ready_record, fail_on_missing=True)
        self.assertEqual(findings, ())

    def test_capital_increase_summary_only_investors_are_not_export_ready(self) -> None:
        record = _deal_record(
            "deal_capital_increase",
            canonical_fields={"deal_price": ""},
            export_extras={
                "investors": [
                    {"name": "小计", "amount": "800"},
                    {"name": "总计", "amount": "1200"},
                ]
            },
        )

        _payload, findings = project_canonical_record_to_export_payload(record, fail_on_missing=False)

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].evidence["missing_fields"], ["investors"])

    def test_capital_increase_rejects_explicit_non_object_export_extras(self) -> None:
        for invalid_export_extras in ([], False):
            with self.subTest(export_extras=invalid_export_extras):
                record = _deal_record(
                    "deal_capital_increase",
                    canonical_fields={"deal_price": ""},
                )
                record["export_extras"] = invalid_export_extras

                for fail_on_missing in (True, False):
                    with self.subTest(fail_on_missing=fail_on_missing):
                        with self.assertRaises(ExportProjectionError) as raised:
                            project_canonical_record_to_export_payload(
                                record,
                                fail_on_missing=fail_on_missing,
                            )

                        self.assertIn("export_extras", str(raised.exception))

    def test_streaming_export_readiness_uses_deal_specific_projection_blockers(self) -> None:
        canonical_record = _deal_record("deal_equity_transfer")
        record = {
            "record_family": "deal",
            "canonical_record": canonical_record,
        }

        with self.assertRaises(ExportProjectionError) as raised:
            _ensure_exportable_payload(record, {"项目编号": "G32026TEST0001"}, record_family="deal")

        self.assertIn("deal_price", str(raised.exception))

    def test_export_projection_does_not_read_artifact_files(self) -> None:
        record = _deal_record(
            "deal_equity_transfer",
            canonical_fields={"deal_price": "1200", "deal_price_unit_basis": "raw_unit"},
        )

        with patch("builtins.open", side_effect=AssertionError("export projection must not read files")):
            _payload, findings = project_canonical_record_to_export_payload(
                record,
                fail_on_missing=True,
            )

        self.assertEqual(findings, ())


if __name__ == "__main__":
    unittest.main()
