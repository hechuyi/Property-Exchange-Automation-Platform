"""Unified error handling middleware for the HTTP layer.

Centralises the mapping from domain exceptions to HTTP status codes
and JSON error payloads so route handlers stay clean.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from ..app_service import AppUserFacingError
from ..error_codes import (
    ERROR_INTERNAL_ERROR,
    ERROR_INVALID_INPUT,
    ERROR_INVALID_REQUEST,
    ERROR_NOT_FOUND,
)
from ..http_contract import build_error_payload
from ..product_errors import ProductError, UserInputError


def handle_exception(exc: Exception) -> tuple[int, dict[str, Any]]:
    """Convert a domain exception to an (http_status, json_payload) tuple."""
    if isinstance(exc, AppUserFacingError):
        return exc.http_status, build_error_payload(
            error_code=exc.error_code,
            message=exc.message,
            details=exc.details,
        )

    if isinstance(exc, UserInputError):
        return HTTPStatus.BAD_REQUEST, build_error_payload(
            error_code=ERROR_INVALID_INPUT,
            message=str(exc),
        )

    if isinstance(exc, ValueError):
        return HTTPStatus.BAD_REQUEST, build_error_payload(
            error_code=ERROR_INVALID_REQUEST,
            message=str(exc),
        )

    if isinstance(exc, ProductError):
        return exc.status_code, exc.to_payload()

    if isinstance(exc, KeyError):
        return HTTPStatus.NOT_FOUND, build_error_payload(
            error_code=ERROR_NOT_FOUND,
            message=ERROR_NOT_FOUND,
            resource="unknown",
            resource_id="",
        )

    return HTTPStatus.INTERNAL_SERVER_ERROR, build_error_payload(
        error_code=ERROR_INTERNAL_ERROR,
        message="internal server error",
    )
