"""Stable v2 workflow for the property parser project."""

from __future__ import annotations

from typing import Iterable, Optional

__all__ = ["main"]


def main(argv: Optional[Iterable[str]] = None) -> int:
    from .cli import main as cli_main

    return cli_main(argv)
