#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Standalone entrypoint for PostProcess Engine."""

from __future__ import annotations

import os
import sys


def _add_project_root_to_syspath() -> str:
    system_dir = os.path.abspath(os.path.dirname(__file__))
    project_root = os.path.abspath(os.path.join(system_dir, ".."))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    return project_root


_add_project_root_to_syspath()

from postprocess_engine.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
