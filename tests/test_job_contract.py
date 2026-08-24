from desktop_backend.job_contract import build_job_view, job_actions


def test_job_actions_fail_closed_for_unknown_or_missing_fields() -> None:
    assert job_actions({"job_type": "one_click", "status": "failed"}) == {"retry": False}
    assert job_actions({"job_type": "unknown", "status": "failed"}) == {"retry": False}
    assert job_actions({"job_type": "one_click"}) == {"retry": False}


def test_job_actions_requires_replayable_request_metadata() -> None:
    streaming = {
        "job_type": "one_click",
        "status": "failed",
        "metadata": {
            "start_date": "2026-01-01",
            "end_date": "2026-01-02",
            "exchange": "sse",
            "record_family": "listing",
        },
    }
    assert job_actions(streaming) == {"retry": True}
    assert job_actions({**streaming, "metadata": {"record_family": "listing"}}) == {"retry": False}
    assert job_actions(
        {
            "job_type": "manual_import",
            "status": "failed",
            "metadata": {"input_dir": "/tmp/import"},
        }
    ) == {"retry": True}
    assert job_actions(
        {
            "job_type": "archive_reprocess",
            "status": "failed",
            "metadata": {"input_dir": ""},
        }
    ) == {"retry": False}


def test_build_job_view_publishes_server_owned_retry_capability() -> None:
    view = build_job_view(
        {
            "job_id": "job-1",
            "job_type": "manual_import",
            "status": "cancelled",
            "metadata": {"input_dir": "/tmp/import"},
        }
    )
    assert view["actions"] == {"retry": True}


def test_build_job_view_publishes_filtered_public_resource_result() -> None:
    view = build_job_view(
        {
            "job_id": "job-public-resource",
            "job_type": "one_click",
            "status": "success_with_warnings",
            "summary": {
                "public_resource": {
                    "status": "success",
                    "record_count": 2,
                    "workbook": "/tmp/public-resource.xlsx",
                    "evidence_root": "/tmp/evidence",
                    "archive_root": "/tmp/archive",
                    "error_message": "",
                    "private_worker_field": "must not leak",
                }
            },
        }
    )

    assert view["result"]["public_resource"] == {
        "status": "success",
        "record_count": 2,
        "workbook": "/tmp/public-resource.xlsx",
        "evidence_root": "/tmp/evidence",
        "archive_root": "/tmp/archive",
    }


def test_build_job_view_publishes_aggregate_scope_for_multi_family_parent_job() -> None:
    view = build_job_view(
        {
            "job_id": "job-multi-family",
            "job_type": "one_click",
            "status": "running",
            "metadata": {
                "record_family": "",
                "record_families": ["listing", "deal"],
                "family_scopes": [
                    {
                        "record_family": "listing",
                        "business_id": "equity_transfer",
                        "exchange": "sse",
                    },
                    {
                        "record_family": "deal",
                        "business_id": "deal_equity_transfer",
                        "exchange": "sse",
                    },
                ],
            },
        }
    )

    assert view["record_family"] == ""
    assert view["business_id"] == ""
    assert view["scope"] == {
        "record_families": ["listing", "deal"],
        "family_scopes": [
            {
                "record_family": "listing",
                "business_id": "equity_transfer",
                "exchange": "sse",
            },
            {
                "record_family": "deal",
                "business_id": "deal_equity_transfer",
                "exchange": "sse",
            },
        ],
    }
