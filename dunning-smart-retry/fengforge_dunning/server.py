#!/usr/bin/env python3
"""MCP (JSON-RPC 2.0, stdio) server for FengForge Dunning Smart Retry.

Zero dependencies. Run:  python3 server.py          (speaks MCP over stdio)
Self-test:               python3 server.py --test
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fengforge_dunning.core import (MAX_RETRIES, assess_account,  # noqa: E402
                                    build_retry_schedule, triage_decline)

SERVER_NAME = "fengforge-dunning-smart-retry"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {
        "name": "triage_decline",
        "description": ("Classify a payment decline code (Stripe-style string codes and "
                        "ISO 8583 numeric codes) as soft / hard / terminal, with "
                        "retryability, the recommended action and a customer-ready message. "
                        "Hard and terminal declines are never auto-retried."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "e.g. insufficient_funds, expired_card, 51, 54"},
                "source": {"type": "string", "description": "Optional hint: stripe | iso8583"},
            },
            "required": ["code"],
        },
    },
    {
        "name": "build_retry_schedule",
        "description": (f"Build an automatic retry schedule (ISO timestamps) for a decline. "
                        f"Hard/terminal declines return an empty schedule. Soft retries are "
                        f"capped at {MAX_RETRIES} attempts in 14 days; insufficient-funds "
                        "retries snap to the next payday (weekly / biweekly anchor / "
                        "monthly 1st & 15th) when it is within the snap window."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "start": {"type": "string", "description": "ISO date/datetime of the failed attempt"},
                "pay_cycle": {
                    "type": "object",
                    "description": "Optional: {type: weekly|biweekly|monthly, anchor_date: ISO date of a known payday}",
                    "additionalProperties": True,
                },
                "attempts_already_made": {"type": "integer", "minimum": 0},
            },
            "required": ["code", "start"],
        },
    },
    {
        "name": "assess_account",
        "description": ("Given a subscription account's payment attempt history, recommend "
                        "continue / send_card_update_request / cancel-and-winback, with reasons."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "attempt_history": {
                    "type": "array",
                    "items": {"type": "object",
                              "properties": {"code": {"type": "string"},
                                             "date": {"type": "string"},
                                             "succeeded": {"type": "boolean"}},
                              "additionalProperties": True},
                },
            },
            "required": ["attempt_history"],
        },
    },
]


def call_tool(name: str, args: dict):
    args = args or {}
    if name == "triage_decline":
        return triage_decline(args.get("code"), args.get("source"))
    if name == "build_retry_schedule":
        return build_retry_schedule(args.get("code"), args.get("start"),
                                    args.get("pay_cycle"),
                                    int(args.get("attempts_already_made") or 0))
    if name == "assess_account":
        return assess_account(args.get("attempt_history") or [])
    raise ValueError(f"unknown tool: {name}")


def _reply(msg_id, result=None, error=None):
    out = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        out["error"] = error
    else:
        out["result"] = result
    return out


def handle(msg: dict):
    method = msg.get("method")
    msg_id = msg.get("id")
    if method == "initialize":
        return _reply(msg_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return _reply(msg_id, {})
    if method == "tools/list":
        return _reply(msg_id, {"tools": TOOLS})
    if method == "tools/call":
        params = msg.get("params") or {}
        try:
            result = call_tool(params.get("name"), params.get("arguments"))
            return _reply(msg_id, {"content": [{"type": "text",
                                                "text": json.dumps(result, indent=2, ensure_ascii=False)}]})
        except Exception as exc:
            return _reply(msg_id, {"content": [{"type": "text", "text": f"Error: {exc}"}],
                                   "isError": True})
    return _reply(msg_id, error={"code": -32601, "message": f"method not found: {method}"})


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle(msg)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


def run_test() -> int:
    # Hard declines are never retried.
    for code in ("expired_card", "lost_card", "stolen_card", "54", "41", "43"):
        triage = triage_decline(code)
        assert triage["retryable"] is False, (code, triage)
        sched = build_retry_schedule(code, "2026-10-23T10:00:00Z")
        assert sched["schedule"] == [], (code, sched)
    # Soft decline: capped schedule of real ISO timestamps.
    sched = build_retry_schedule("insufficient_funds", "2026-10-23T10:00:00Z",
                                 {"type": "monthly"})
    assert sched["retryable"] is True and 1 <= len(sched["schedule"]) <= MAX_RETRIES, sched
    stamps = [a["at"] for a in sched["schedule"]]
    assert stamps == sorted(stamps), stamps
    from datetime import datetime
    for stamp in stamps:
        datetime.fromisoformat(stamp)  # must parse as ISO timestamp
    # Payday snap: first retry planned 2026-10-26, monthly payday 2026-11-01 is
    # 6 days later (inside the 7-day snap window), so it must snap.
    assert sched["schedule"][0]["snapped_to_payday"] is True, sched
    assert sched["schedule"][0]["at"].startswith("2026-11-01"), sched
    # ISO + Stripe equivalence for insufficient funds.
    assert triage_decline("51")["category"] == triage_decline("insufficient_funds")["category"] == "soft"
    # Account assessment.
    assert assess_account([{"code": "insufficient_funds"}])["recommendation"] == "continue"
    assert assess_account([{"code": "expired_card"}])["recommendation"] == "send_card_update_request"
    assert assess_account([{"code": "stolen_card"}])["recommendation"] == "cancel-and-winback"
    print(f"{SERVER_NAME} --test: PASS (hard never retried, capped ISO schedule, payday snap, assessment)")
    return 0


if __name__ == "__main__":
    if "--test" in sys.argv:
        raise SystemExit(run_test())
    main()
