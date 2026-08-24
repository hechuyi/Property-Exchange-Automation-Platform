"""Product-level error model for desktop backend surfaces."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Dict

from .error_codes import (
    ERROR_DEPENDENCY_NOT_READY,
    ERROR_INVALID_INPUT,
    ERROR_NOT_FOUND,
    ERROR_PRODUCT_ERROR,
    ERROR_STATE_CONFLICT,
)
from .http_contract import build_error_payload


class ProductError(Exception):
    status_code = 500
    error_code = ERROR_PRODUCT_ERROR

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = str(message or "").strip()
        if details is None:
            self.details = {}
        elif not isinstance(details, Mapping):
            raise TypeError("details must be a dict")
        else:
            self.details = dict(details)

    def to_payload(self) -> Dict[str, Any]:
        return build_error_payload(
            error_code=self.error_code,
            message=self.message or self.error_code,
            details=self.details,
        )

    def __str__(self) -> str:
        return self.message


class UserInputError(ProductError, ValueError):
    status_code = 400
    error_code = ERROR_INVALID_INPUT


class StateConflictError(ProductError, RuntimeError):
    status_code = 409
    error_code = ERROR_STATE_CONFLICT


class DependencyNotReadyError(ProductError, RuntimeError):
    status_code = 503
    error_code = ERROR_DEPENDENCY_NOT_READY


class ResourceNotFoundError(ProductError, KeyError):
    status_code = 404
    error_code = ERROR_NOT_FOUND

    def __init__(self, *, resource: str, resource_id: str = "", message: str | None = None) -> None:
        normalized_resource = str(resource or "").strip() or "resource"
        normalized_resource_id = str(resource_id or "").strip()
        if message is None:
            message = (
                f"{normalized_resource} not found: {normalized_resource_id}"
                if normalized_resource_id
                else f"{normalized_resource} not found"
            )
        super().__init__(
            message,
            details={
                "resource": normalized_resource,
                "resource_id": normalized_resource_id,
            },
        )
