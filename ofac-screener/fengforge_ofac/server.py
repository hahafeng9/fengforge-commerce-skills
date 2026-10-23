#!/usr/bin/env python3
"""MCP (JSON-RPC 2.0, stdio) server for the FengForge OFAC Screener.

Zero dependencies, no network access at runtime.
Run:  python3 server.py          (speaks MCP over stdio)
Self-test:               python3 server.py --test
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fengforge_ofac.core import (DEFAULT_THRESHOLD, generate_hold_memo,  # noqa: E402
                                 screen_batch, screen_entity)

SERVER_NAME = "fengforge-ofac-screener"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2024-11-05"

_ENTITY_SCHEMA = {
    "type": "object",
    "description": ("Entity to screen. Fields: name (required in practice), country "
                    "(ISO code or name), region (e.g. Crimea, Donetsk), type, address."),
    "properties": {
        "name": {"type": "string"},
        "country": {"type": "string"},
        "region": {"type": "string"},
        "type": {"type": "string"},
        "address": {"type": "string"},
    },
    "additionalProperties": True,
}

TOOLS = [
    {
        "name": "screen_entity",
        "description": ("Screen one counterparty against the denied-party dataset with real "
                        "fuzzy name matching (Levenshtein + token alignment, aliases, legal-"
                        "suffix and transliteration normalisation) plus a separate embargoed-"
                        "jurisdiction check (CU/IR/KP/SY, Crimea, DNR, LNR). Returns matches "
                        "with scores, risk level and a recommended action."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity": _ENTITY_SCHEMA,
                "threshold": {"type": "number", "minimum": 0, "maximum": 1,
                              "description": f"Match threshold (default {DEFAULT_THRESHOLD})"},
            },
            "required": ["entity"],
        },
    },
    {
        "name": "screen_batch",
        "description": "Screen a list of entities; returns per-entity results and clear/review/blocked counts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entities": {"type": "array", "items": _ENTITY_SCHEMA},
                "threshold": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["entities"],
        },
    },
    {
        "name": "generate_hold_memo",
        "description": ("Generate a full sanctions hold memo as text for an entity (with "
                        "optional transaction details): memo header, transaction, match "
                        "detail, jurisdiction check, required actions including the OFAC "
                        "reporting-deadline note, and disclaimer."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity": _ENTITY_SCHEMA,
                "transaction": {"type": "object", "additionalProperties": True,
                                "description": "Optional: amount, currency, reference, date, purpose"},
                "threshold": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["entity"],
        },
    },
]


def call_tool(name: str, args: dict):
    args = args or {}
    threshold = float(args.get("threshold") or DEFAULT_THRESHOLD)
    if name == "screen_entity":
        return screen_entity(args.get("entity") or {}, threshold=threshold), False
    if name == "screen_batch":
        return screen_batch(args.get("entities") or [], threshold=threshold), False
    if name == "generate_hold_memo":
        return generate_hold_memo(args.get("entity") or {}, args.get("transaction"),
                                  threshold=threshold), True
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
    # Exact-alias hits above threshold.
    for query in ("Sberbank of Russia", "Rosneft Oil Company"):
        result = screen_entity({"name": query, "country": "RU"})
        assert result["match_count"] >= 1 and result["matches"][0]["score"] >= DEFAULT_THRESHOLD, (query, result)
        assert result["risk_level"] in ("medium", "high"), result
    # Fuzzy hit with a typo.
    fuzzy = screen_entity({"name": "Sberbnk of Russia"})
    assert fuzzy["match_count"] >= 1, fuzzy
    # Clear name passes.
    clear = screen_entity({"name": "John Smith Plumbing Services", "country": "GB"})
    assert clear["match_count"] == 0 and clear["risk_level"] == "low", clear
    # Embargoed jurisdiction blocks even with a clean name.
    embargo = screen_entity({"name": "John Smith Plumbing Services", "country": "IR"})
    assert embargo["jurisdiction_check"]["embargoed"] is True and embargo["risk_level"] == "critical", embargo
    crimea = screen_entity({"name": "Harbour Coffee Roasters", "country": "UA", "region": "Crimea"})
    assert crimea["jurisdiction_check"]["embargoed"] is True, crimea
    # Memo contains the required sections.
    memo = generate_hold_memo({"name": "Sberbank of Russia", "country": "RU"},
                              {"amount": 12000, "currency": "USD", "reference": "TX-1"})
    for needle in ("SANCTIONS HOLD MEMO", "Required actions", "10 business days", "Disclaimer"):
        assert needle in memo, needle
    print(f"{SERVER_NAME} --test: PASS (exact/fuzzy hits, clear pass, embargo, memo)")
    return 0


if __name__ == "__main__":
    if "--test" in sys.argv:
        raise SystemExit(run_test())
    main()
