"""Rule plugin interfaces."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict

from ..contracts import CanonicalRecord, RuleResult

RuleContext = Dict[str, Any]


class BaseRule(ABC):
    """Base class for postprocess rules."""

    def __init__(self, *, params: Dict[str, Any] | None = None) -> None:
        self.params = dict(params or {})

    @classmethod
    @abstractmethod
    def rule_id(cls) -> str:
        """Stable unique identifier for a rule."""

    @classmethod
    def rule_version(cls) -> str:
        return "0.1.0"

    def applies(self, record: CanonicalRecord, context: RuleContext) -> bool:  # noqa: ARG002
        return True

    @abstractmethod
    def apply(self, record: CanonicalRecord, context: RuleContext) -> RuleResult:
        """Evaluate one record and return planned patches/findings."""
