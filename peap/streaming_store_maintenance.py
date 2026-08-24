"""Explicit maintenance orchestration for legacy store normalization."""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .streaming_store import StreamingStore


@dataclass(frozen=True)
class StreamingStoreMaintenanceSummary:
    skip_parse: dict[str, int]
    invalid_source_pages: dict[str, int]
    purged_invalid_source_pages: dict[str, int]
    quarantined_synthetic_failures: dict[str, int]
    superseded_record_shells: dict[str, int]
    deal_source_artifacts: dict[str, int]
    listing_dates: int
    business_kernel: dict[str, int]
    canonical_contracts: dict[str, int]
    deal_export_readiness: dict[str, int]
    required_mapping: dict[str, int]
    optional_rules: dict[str, int]
    export_projection_readiness: dict[str, int] = field(default_factory=dict)
    artifact_evidence: dict[str, int] = field(default_factory=dict)
    source_evidence_missing: dict[str, int] = field(default_factory=dict)
    required_field_missing: dict[str, int] = field(default_factory=dict)
    manifest: dict[str, Any] = field(default_factory=dict)
    mode: str = "dry_run"
    mutation_applied: bool = False


def _has_changes(summary: dict[str, Any]) -> bool:
    return any(int(summary.get(key, 0)) > 0 for key in summary)


def _manifest_counter_object(manifest: Mapping[str, Any], field: str) -> dict[str, Any]:
    value = manifest.get(field)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"maintenance manifest.{field} must be an object")
    return dict(value)


def run_streaming_store_maintenance(
    store: StreamingStore,
    *,
    rules_config: dict[str, Any] | None = None,
    mutate: bool = False,
) -> StreamingStoreMaintenanceSummary:
    manifest = store.build_maintenance_artifact_evidence_manifest()
    if not mutate:
        with tempfile.TemporaryDirectory() as temp_dir:
            dry_store = StreamingStore(f"{temp_dir}/streaming-maintenance-dry-run.sqlite3")
            with store._connect() as source_conn, dry_store._connect() as target_conn:
                source_conn.backup(target_conn)
            return _run_streaming_store_maintenance_steps(
                dry_store,
                rules_config=rules_config,
                manifest=manifest,
                mode="dry_run",
                mutation_applied=False,
                write_audit=False,
            )

    return _run_streaming_store_maintenance_steps(
        store,
        rules_config=rules_config,
        manifest=manifest,
        mode="mutate",
        mutation_applied=True,
        write_audit=True,
    )


def _run_streaming_store_maintenance_steps(
    store: StreamingStore,
    *,
    rules_config: dict[str, Any] | None,
    manifest: dict[str, Any],
    mode: str,
    mutation_applied: bool,
    write_audit: bool,
) -> StreamingStoreMaintenanceSummary:
    skip_parse = store.normalize_legacy_skip_parse_entries()
    if write_audit and _has_changes(skip_parse):
        store.add_audit_entry("legacy_skip_parse_normalized", skip_parse)

    invalid_source_pages = store.normalize_invalid_source_pages()
    if write_audit and _has_changes(invalid_source_pages):
        store.add_audit_entry("legacy_invalid_source_pages_normalized", invalid_source_pages)

    purged_invalid_source_pages = store.purge_invalid_source_page_records()
    if write_audit and _has_changes(purged_invalid_source_pages):
        store.add_audit_entry("legacy_invalid_source_pages_purged", purged_invalid_source_pages)

    quarantined_synthetic_failures = store.purge_quarantined_synthetic_failed_records()
    if write_audit and _has_changes(quarantined_synthetic_failures):
        store.add_audit_entry(
            "legacy_quarantined_synthetic_failures_purged",
            quarantined_synthetic_failures,
        )

    superseded_record_shells = store.normalize_superseded_record_shells()
    if write_audit and _has_changes(superseded_record_shells):
        store.add_audit_entry("legacy_superseded_record_shells_normalized", superseded_record_shells)

    deal_source_artifacts = store.normalize_deal_source_artifacts()
    if write_audit and _has_changes(deal_source_artifacts):
        store.add_audit_entry("legacy_deal_source_artifacts_normalized", deal_source_artifacts)

    listing_dates = int(store.normalize_listing_dates() or 0)
    if write_audit and listing_dates > 0:
        store.add_audit_entry("legacy_listing_dates_normalized", {"records": listing_dates})

    business_kernel = store.normalize_business_kernel_fields()
    if write_audit and _has_changes(business_kernel):
        store.add_audit_entry("legacy_business_kernel_normalized", business_kernel)

    deal_export_readiness = store.normalize_deal_export_readiness()
    if write_audit and _has_changes(deal_export_readiness):
        store.add_audit_entry("legacy_deal_export_readiness_normalized", deal_export_readiness)

    optional_rules = store.normalize_optional_rule_findings(rules_config=rules_config)
    if write_audit and _has_changes(optional_rules):
        store.add_audit_entry("legacy_optional_rules_normalized", optional_rules)

    # Optional rules can intentionally keep a record skipped. Run them before
    # generic normalization so projection requirements never override a
    # rule-filtered terminal decision.
    required_mapping = store.normalize_required_mapping_states()
    if write_audit and _has_changes(required_mapping):
        store.add_audit_entry("legacy_required_mapping_normalized", required_mapping)

    canonical_contracts = store.normalize_canonical_contracts()
    if write_audit and _has_changes(canonical_contracts):
        store.add_audit_entry("legacy_canonical_contract_normalized", canonical_contracts)

    # Canonical repair can fill fields that were previously reported as
    # FIELD_MISSING. Recompute only export projection blockers here instead of
    # feeding field-missing records through mapping normalization a second time.
    export_projection_readiness = store.normalize_export_projection_readiness()
    if write_audit and _has_changes(export_projection_readiness):
        store.add_audit_entry(
            "legacy_export_projection_readiness_normalized",
            export_projection_readiness,
        )

    post_manifest = store.build_maintenance_artifact_evidence_manifest()
    summary_manifest = post_manifest if mutation_applied else manifest
    return StreamingStoreMaintenanceSummary(
        skip_parse={key: int(value or 0) for key, value in skip_parse.items()},
        invalid_source_pages={key: int(value or 0) for key, value in invalid_source_pages.items()},
        purged_invalid_source_pages={key: int(value or 0) for key, value in purged_invalid_source_pages.items()},
        quarantined_synthetic_failures={
            key: int(value or 0) for key, value in quarantined_synthetic_failures.items()
        },
        superseded_record_shells={key: int(value or 0) for key, value in superseded_record_shells.items()},
        deal_source_artifacts={key: int(value or 0) for key, value in deal_source_artifacts.items()},
        listing_dates=listing_dates,
        business_kernel={key: int(value or 0) for key, value in business_kernel.items()},
        canonical_contracts={key: int(value or 0) for key, value in canonical_contracts.items()},
        deal_export_readiness={key: int(value or 0) for key, value in deal_export_readiness.items()},
        required_mapping={key: int(value or 0) for key, value in required_mapping.items()},
        optional_rules={key: int(value or 0) for key, value in optional_rules.items()},
        export_projection_readiness={
            key: int(value or 0)
            for key, value in export_projection_readiness.items()
        },
        artifact_evidence={
            key: int(value or 0) for key, value in _manifest_counter_object(manifest, "artifact_evidence").items()
        },
        source_evidence_missing={
            key: int(value or 0)
            for key, value in _manifest_counter_object(post_manifest, "source_evidence_missing").items()
        },
        required_field_missing={
            key: int(value or 0)
            for key, value in _manifest_counter_object(post_manifest, "required_field_missing").items()
        },
        manifest=dict(summary_manifest),
        mode=mode,
        mutation_applied=mutation_applied,
    )


__all__ = ["StreamingStoreMaintenanceSummary", "run_streaming_store_maintenance"]
