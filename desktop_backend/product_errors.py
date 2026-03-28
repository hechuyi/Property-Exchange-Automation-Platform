"""Product-level error model for desktop backend surfaces."""

from __future__ import annotations

from typing import Any, Dict


class ProductError(Exception):
    status_code = 500
    error_code = "product_error"

    def __init__(self, message: str, *, details: Dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = str(message or "").strip()
        self.details = dict(details or {})

    def to_payload(self) -> Dict[str, Any]:
        return {
            "error": self.error_code,
            "message": self.message,
            "details": dict(self.details),
        }

    def __str__(self) -> str:
        return self.message


class UserInputError(ProductError, ValueError):
    status_code = 400
    error_code = "invalid_input"


class StateConflictError(ProductError, RuntimeError):
    status_code = 409
    error_code = "state_conflict"


class DependencyNotReadyError(ProductError, RuntimeError):
    status_code = 503
    error_code = "dependency_not_ready"


class ResourceNotFoundError(ProductError, KeyError):
    status_code = 404
    error_code = "not_found"

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
