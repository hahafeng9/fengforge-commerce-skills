#!/usr/bin/env python3
"""MCP (JSON-RPC 2.0, stdio) server for the FengForge Chargeback CE3.0 Compiler.

Zero dependencies. Run:  python3 server.py          (speaks MCP over stdio)
Self-test:               python3 server.py --test
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fengforge_chargeback_ce30.core import (check_ce30_eligibility,  # noqa: E402
                                             generate_evidence_packet,
                                             match_qualifying_transactions)

SERVER_NAME = "fengforge-chargeback-ce30"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2024-11-05"

_TX_SCHEMA = {
    "type": "object",
    "description": ("Transaction object. Useful fields: transaction_id, date (ISO), "
                    "amount, currency, device_fingerprint, ip_address, account_id "
                    "(or login_id), disputed (bool), merchandise, and for the disputed "
                    "transaction also dispute_date and reason_code."),
    "additionalProperties": True,
}

TOOLS = [
    {
        "name": "check_ce30_eligibility",
        "description": ("Assess Visa CE3.0 eligibility: >=2 undisputed prior transactions "
                        "aged 120-365 days before the dispute date, sharing >=2 distinct "
                        "core data elements with the disputed transaction. Returns "
                        "per-transaction pass/fail reasons and a gaps list."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "disputed_transaction": _TX_SCHEMA,
                "prior_transactions": {"type": "array", "items": _TX_SCHEMA},
                "dispute_date": {"type": "string", "description": "ISO date; overrides disputed_transaction.dispute_date"},
            },
            "required": ["disputed_transaction", "prior_transactions"],
        },
    },
    {
        "name": "match_qualifying_transactions",
        "description": "Return only the prior transactions that qualify under CE3.0, with element-match detail.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "disputed_transaction": _TX_SCHEMA,
                "prior_transactions": {"type": "array", "items": _TX_SCHEMA},
                "dispute_date": {"type": "string"},
            },
            "required": ["disputed_transaction", "prior_transactions"],
        },
    },
    {
        "name": "generate_evidence_packet",
        "description": "Generate a ready-to-submit Markdown CE3.0 representment packet as text.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "disputed_transaction": _TX_SCHEMA,
                "prior_transactions": {"type": "array", "items": _TX_SCHEMA},
                "dispute_date": {"type": "string"},
                "merchant_name": {"type": "string"},
            },
            "required": ["disputed_transaction", "prior_transactions"],
        },
    },
]


def call_tool(name: str, args: dict):
    args = args or {}
    disputed = args.get("disputed_transaction") or {}
    priors = args.get("prior_transactions") or []
    if name == "check_ce30_eligibility":
        return check_ce30_eligibility(disputed, priors, args.get("dispute_date")), False
    if name == "match_qualifying_transactions":
        return match_qualifying_transactions(disputed, priors, args.get("dispute_date")), False
    if name == "generate_evidence_packet":
        text = generate_evidence_packet(disputed, priors, args.get("dispute_date"),
                                        args.get("merchant_name") or "")
        return text, True
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
            result, is_text = call_tool(params.get("name"), params.get("arguments"))
            text = result if is_text else json.dumps(result, indent=2, ensure_ascii=False)
            return _reply(msg_id, {"content": [{"type": "text", "text": text}]})
        except Exception as exc:  # surface as MCP tool error, not a crash
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


def _sample():
    disputed = {
        "transaction_id": "txn_disputed", "date": "2026-10-01", "dispute_date": "2026-10-15",
        "amount": 59.99, "currency": "USD", "reason_code": "10.4",
        "device_fingerprint": "dev-abc", "ip_address": "203.0.113.7",
        "account_id": "user-42", "merchandise": "Pro subscription",
    }
    priors = [
        {"transaction_id": "txn_old1", "date": "2026-01-10", "amount": 59.99, "currency": "USD",
         "device_fingerprint": "dev-abc", "ip_address": "198.51.100.3",
         "account_id": "user-42", "merchandise": "Pro subscription", "disputed": False},
        {"transaction_id": "txn_old2", "date": "2025-12-01", "amount": 59.99, "currency": "USD",
         "device_fingerprint": "dev-xyz", "ip_address": "203.0.113.7",
         "account_id": "user-99", "merchandise": "Pro subscription", "disputed": False},
        {"transaction_id": "txn_recent", "date": "2026-07-27", "amount": 59.99, "currency": "USD",
         "device_fingerprint": "dev-abc", "ip_address": "203.0.113.7",
         "account_id": "user-42", "merchandise": "Pro subscription", "disputed": False},
    ]
    return disputed, priors


def run_test() -> int:
    disputed, priors = _sample()
    result = check_ce30_eligibility(disputed, priors)
    assert result["eligible"] is True, f"expected eligible, got {result}"
    assert result["qualifying_count"] == 2, result
    assert set(result["elements_matched_across_set"]) >= {"device_fingerprint", "account_id", "ip_address"} or len(result["elements_matched_across_set"]) >= 2
    assert result["merchandise_match_bonus"] is True
    recent = [r for r in result["per_transaction"] if r["transaction_id"] == "txn_recent"][0]
    assert recent["qualifies"] is False and any("too recent" in x for x in recent["fail_reasons"]), recent
    # Ineligible case: only one qualifying transaction.
    weak = check_ce30_eligibility(disputed, priors[:1])
    assert weak["eligible"] is False and any("only 1 qualifying" in g for g in weak["gaps"]), weak
    # MCP handshake + tool call smoke test.
    resp = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert [t["name"] for t in resp["result"]["tools"]] == [t["name"] for t in TOOLS]
    resp = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                   "params": {"name": "generate_evidence_packet",
                              "arguments": {"disputed_transaction": disputed,
                                            "prior_transactions": priors,
                                            "merchant_name": "Example Store"}}})
    text = resp["result"]["content"][0]["text"]
    for section in ("Case summary", "Qualifying prior transactions", "Merchant declaration"):
        assert section in text, section
    print(f"{SERVER_NAME} --test: PASS (eligible case, ineligible case, packet generation)")
    return 0


if __name__ == "__main__":
    if "--test" in sys.argv:
        raise SystemExit(run_test())
    main()
