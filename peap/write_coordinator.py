"""Write-operation coordination and operation journal primitives."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict


def _error_payload_from_exception(exc: BaseException) -> dict[str, str]:
    return {
        "code": exc.__class__.__name__.lower(),
        "message": str(exc),
        "type": exc.__class__.__name__,
    }


def _required_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    text = value.strip()
    if not text:
        raise ValueError(f"{field} is empty")
    return text


def _object_payload(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return dict(value)


@dataclass
class OperationHandle:
    store: Any
    operation_id: str
    operation_type: str
    metadata: dict[str, Any]
    initial_snapshot: dict[str, Any]
    _manifest: dict[str, Any] = field(default_factory=dict)
    _finished: bool = False

    def set_manifest(self, manifest: Dict[str, Any]) -> None:
        self._manifest = _object_payload(manifest, field="manifest")

    def update_manifest(self, payload: Dict[str, Any]) -> None:
        self._manifest.update(_object_payload(payload, field="manifest"))

    @property
    def manifest(self) -> dict[str, Any]:
        return dict(self._manifest)

    @property
    def is_finished(self) -> bool:
        return bool(self._finished)

    def succeed(self, manifest: Dict[str, Any] | None = None) -> None:
        if self._finished:
            return
        if manifest is not None:
            self.set_manifest(manifest)
        final_manifest = {
            "before": dict(self.initial_snapshot),
            **dict(self._manifest),
            "after": dict(self.store.get_operation_snapshot()),
        }
        self.store.update_operation_journal(
            self.operation_id,
            status="succeeded",
            finished_at=self._now(),
            manifest=final_manifest,
            error={},
        )
        self._finished = True

    def fail(self, error: BaseException | Dict[str, Any] | None = None, *, manifest: Dict[str, Any] | None = None) -> None:
        if self._finished:
            return
        if manifest is not None:
            self.set_manifest(manifest)
        if isinstance(error, BaseException):
            error_payload: dict[str, Any] = _error_payload_from_exception(error)
        elif error is None:
            error_payload = {}
        else:
            error_payload = _object_payload(error, field="error")
        final_manifest = {
            "before": dict(self.initial_snapshot),
            **dict(self._manifest),
            "after": dict(self.store.get_operation_snapshot()),
        }
        self.store.update_operation_journal(
            self.operation_id,
            status="failed",
            finished_at=self._now(),
            manifest=final_manifest,
            error=error_payload,
        )
        self._finished = True

    def _now(self) -> str:
        current = self.store.get_operation_journal(self.operation_id)
        return str(current.get("finished_at") or "") or __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class WriteCoordinator:
    def __init__(self, *, store) -> None:
        self.store = store
        self._lock = threading.Lock()
        self._thread_state = threading.local()

    def start_operation(self, operation_type: str, metadata: Dict[str, Any] | None = None) -> OperationHandle:
        resolved_operation_type = _required_text(operation_type, field="operation_type")
        metadata_payload = {} if metadata is None else _object_payload(metadata, field="metadata")
        snapshot = dict(self.store.get_operation_snapshot())
        operation_id = self.store.create_operation_journal(
            resolved_operation_type,
            metadata=metadata_payload,
            manifest={"before": snapshot},
        )
        return OperationHandle(
            store=self.store,
            operation_id=operation_id,
            operation_type=resolved_operation_type,
            metadata=dict(metadata_payload),
            initial_snapshot=snapshot,
        )

    def write_operation(
        self,
        operation_type: str,
        metadata: Dict[str, Any] | None,
        fn: Callable[[OperationHandle], Any],
    ) -> Any:
        if getattr(self._thread_state, "active", False):
            raise RuntimeError("reentrant write operation is not allowed")
        with self._lock:
            operation = self.start_operation(operation_type, metadata)
            self._thread_state.active = True
            try:
                result = fn(operation)
            except Exception as exc:  # noqa: BLE001
                if not operation.is_finished:
                    operation.fail(exc)
                raise
            finally:
                self._thread_state.active = False
            if not operation.is_finished:
                operation.succeed()
            return result


__all__ = ["OperationHandle", "WriteCoordinator"]
