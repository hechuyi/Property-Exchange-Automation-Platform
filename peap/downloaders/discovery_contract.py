"""Registry-derived discovery query contracts for archive verification."""

from __future__ import annotations

from peap_core.source_business_contract import get_source_business_requirement


def expected_discovery_query_ids(
    *,
    source_id: str,
    record_family: str,
    business_id: str,
) -> tuple[str, ...]:
    source = str(source_id or "").strip()
    family = str(record_family or "").strip()
    business = str(business_id or "").strip()
    requirement = get_source_business_requirement(source, family, business)
    declared_query_ids = tuple(requirement.discovery_query_ids)
    if declared_query_ids:
        return declared_query_ids
    if family != "listing":
        raise ValueError(
            f"registry has no discovery query contract for {source}:{family}:{business}"
        )

    specs = tuple(requirement.list_query_specs)
    if source in {"guangdong", "shandong", "shenzhen"}:
        if specs:
            raise ValueError(f"regional listing source unexpectedly declares query specs: {source}")
        return ("listing",)
    if not specs:
        raise ValueError(f"registry has no discovery query specs for {source}:{family}:{business}")

    if source == "sse":
        return tuple(
            f"{str(spec.get('project_type') or '').strip()}-gplx-"
            f"{str(spec.get('gplx') or '').strip()}"
            for spec in specs
        )
    if source == "cbex":
        return tuple(
            f"{str(spec.get('label') or '').strip()}-"
            f"{str(spec.get('businessType') or '').strip()}-"
            f"{str(spec.get('assetType') or 'all').strip()}"
            for spec in specs
        )
    if source in {"tpre", "cquae"}:
        return tuple(
            f"{index:03d}-{str(spec.get('label') or 'listing').strip()}"
            for index, spec in enumerate(specs, start=1)
        )
    raise ValueError(f"unsupported discovery source contract: {source}")


__all__ = ["expected_discovery_query_ids"]
