from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from peap.constants import STATUS_LISTED, TYPE_EQUITY_TRANSFER
from peap.parser_subsystem import (
    ParserSubsystemError,
    _has_cbex_otc_identity,
    _resolved_business_type,
    run_parser_subsystem,
)
from peap_parsers import ParserOutput
from peap_parsers.base import WebPageParser


class FalsyMapping(dict[str, object]):
    def __bool__(self) -> bool:
        return False


@pytest.mark.parametrize("standard_payload", [False, [], "not-a-mapping"])
def test_business_type_fallback_rejects_explicit_non_mapping_standard_payload(standard_payload: object) -> None:
    with pytest.raises(ParserSubsystemError, match="standard_payload"):
        _resolved_business_type(data={}, standard_payload=standard_payload)


def test_business_type_fallback_reads_valid_falsy_mapping_standard_payload() -> None:
    standard_payload = FalsyMapping({"business_type": TYPE_EQUITY_TRANSFER})

    assert _resolved_business_type(data={}, standard_payload=standard_payload) == TYPE_EQUITY_TRANSFER


@pytest.mark.parametrize("standard_payload", [False, [], "not-a-mapping"])
def test_cbex_identity_fallback_rejects_explicit_non_mapping_standard_payload(standard_payload: object) -> None:
    with pytest.raises(ParserSubsystemError, match="standard_payload"):
        _has_cbex_otc_identity(data={}, standard_payload=standard_payload)


def test_cbex_identity_fallback_reads_valid_falsy_mapping_standard_payload() -> None:
    standard_payload = FalsyMapping({"project_code": "P-001"})

    assert _has_cbex_otc_identity(data={}, standard_payload=standard_payload) is True


def test_parser_subsystem_rejects_non_mapping_parser_output_standard_payload_with_clear_error() -> None:
    class FakeParser(WebPageParser):
        def parse(self) -> ParserOutput:
            return ParserOutput(standard_payload=[])

    with (
        patch(
            "peap.parser_subsystem.read_text_with_fallback",
            return_value=SimpleNamespace(content="<html></html>", encoding="utf-8"),
        ),
        patch("peap.parser_subsystem.detect_exchange", return_value="beijing"),
        patch("peap.parser_subsystem.PARSER_MAP", {"beijing": FakeParser}),
        patch(
            "peap.parser_subsystem.detect_category_from_path",
            return_value=(STATUS_LISTED, TYPE_EQUITY_TRANSFER),
        ),
        patch("peap.parser_subsystem.apply_pre_disclosure_fallback"),
        patch("peap.parser_subsystem.apply_finance_fallback"),
        patch("peap.parser_subsystem.apply_group_fallback"),
    ):
        with pytest.raises(ParserSubsystemError, match="standard_payload"):
            run_parser_subsystem("bad-standard-payload.html")


def test_parser_subsystem_allows_none_standard_payload_as_absent_payload() -> None:
    class FakeParser(WebPageParser):
        def parse(self) -> ParserOutput:
            return ParserOutput(standard_payload=None)

        def is_pre_disclosure(self, project_code: object) -> bool:
            return False

    with (
        patch(
            "peap.parser_subsystem.read_text_with_fallback",
            return_value=SimpleNamespace(content="<html></html>", encoding="utf-8"),
        ),
        patch("peap.parser_subsystem.detect_exchange", return_value="beijing"),
        patch("peap.parser_subsystem.PARSER_MAP", {"beijing": FakeParser}),
        patch(
            "peap.parser_subsystem.detect_category_from_path",
            return_value=(STATUS_LISTED, TYPE_EQUITY_TRANSFER),
        ),
        patch("peap.parser_subsystem.apply_pre_disclosure_fallback"),
        patch("peap.parser_subsystem.apply_finance_fallback"),
        patch("peap.parser_subsystem.apply_group_fallback"),
    ):
        result = run_parser_subsystem("absent-standard-payload.html")

    assert result.standard_payload is None


def test_parser_subsystem_surfaces_pre_disclosure_contract_errors() -> None:
    class FakeParser(WebPageParser):
        def parse(self) -> dict[str, object]:
            return {"项目编号": "P-RAISE"}

        def is_pre_disclosure(self, project_code: object) -> bool:
            raise RuntimeError(f"bad project code contract: {project_code}")

    with (
        patch(
            "peap.parser_subsystem.read_text_with_fallback",
            return_value=SimpleNamespace(content="<html></html>", encoding="utf-8"),
        ),
        patch("peap.parser_subsystem.detect_exchange", return_value="beijing"),
        patch("peap.parser_subsystem.PARSER_MAP", {"beijing": FakeParser}),
        patch(
            "peap.parser_subsystem.detect_category_from_path",
            return_value=(STATUS_LISTED, TYPE_EQUITY_TRANSFER),
        ),
        patch("peap.parser_subsystem.apply_pre_disclosure_fallback"),
        patch("peap.parser_subsystem.apply_finance_fallback"),
        patch("peap.parser_subsystem.apply_group_fallback"),
    ):
        with pytest.raises(ParserSubsystemError, match="pre-disclosure-detect-failed"):
            run_parser_subsystem("bad-pre-disclosure.html")
