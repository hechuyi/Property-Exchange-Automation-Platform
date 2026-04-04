#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLAN_PATH = ROOT / "docs/superpowers/plans/2026-04-01-downloader-3.0-hardening.md"
CLOSEOUT_GATE_PATH = ROOT / ".claude" / "downloader_closeout_gate.json"
ALLOW_STOP_TOKEN = "[HOOK_ALLOW_STOP]"
UNCHECKED_PATTERN = re.compile(r"^- \[ \] (.+)$")


def _unchecked_steps(plan_path: Path) -> list[str]:
    steps: list[str] = []
    for line in plan_path.read_text(encoding="utf-8").splitlines():
        match = UNCHECKED_PATTERN.match(line.strip())
        if match:
            steps.append(match.group(1).strip())
    return steps


def _pending_closeout_gates(gate_path: Path) -> list[str] | None:
    if not gate_path.exists():
        return None
    payload = json.loads(gate_path.read_text(encoding="utf-8"))
    raw_gates = payload.get("gates")
    if not isinstance(raw_gates, list):
        raise ValueError("closeout gate file must contain a gates list")

    pending: list[str] = []
    for item in raw_gates:
        if not isinstance(item, dict):
            continue
        if bool(item.get("done")):
            continue
        label = str(item.get("label") or item.get("id") or "").strip()
        if label:
            pending.append(label)
    return pending


def main() -> int:
    payload = json.load(sys.stdin) if not sys.stdin.isatty() else {}
    last_message = str(payload.get("last_assistant_message") or "")

    if ALLOW_STOP_TOKEN in last_message:
        return 0

    try:
        pending_gates = _pending_closeout_gates(CLOSEOUT_GATE_PATH)
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {
                    "decision": "block",
                    "reason": f"Closeout guard could not read {CLOSEOUT_GATE_PATH}: {exc}",
                }
            )
        )
        return 0

    if pending_gates is not None:
        if not pending_gates:
            return 0
        preview = "; ".join(pending_gates[:3])
        remainder = f" (+{len(pending_gates) - 3} more)" if len(pending_gates) > 3 else ""
        print(
            json.dumps(
                {
                    "decision": "block",
                    "reason": (
                        f"Closeout incomplete: {len(pending_gates)} gate(s) remain. "
                        f"Next: {preview}{remainder}. Continue execution instead of stopping. "
                        f"Use {ALLOW_STOP_TOKEN} only when user input is required."
                    ),
                }
            )
        )
        return 0

    try:
        unchecked = _unchecked_steps(PLAN_PATH)
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {
                    "decision": "block",
                    "reason": f"Plan guard could not read {PLAN_PATH}: {exc}",
                }
            )
        )
        return 0

    if not unchecked:
        return 0

    preview = "; ".join(unchecked[:3])
    remainder = f" (+{len(unchecked) - 3} more)" if len(unchecked) > 3 else ""
    print(
        json.dumps(
            {
                "decision": "block",
                "reason": (
                    f"Plan incomplete: {len(unchecked)} unchecked step(s) remain. "
                    f"Next: {preview}{remainder}. Continue execution instead of stopping. "
                    f"Use {ALLOW_STOP_TOKEN} only when user input is required."
                ),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
