#!/usr/bin/env python3
"""Runnable example for the FengForge Chargeback CE3.0 Compiler.

    python3 example_usage.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fengforge_chargeback_ce30 import (check_ce30_eligibility,  # noqa: E402
                                       generate_evidence_packet,
                                       match_qualifying_transactions)

DISPUTED = {
    "transaction_id": "txn_9001", "date": "2026-09-20", "dispute_date": "2026-10-15",
    "amount": 59.99, "currency": "USD", "reason_code": "10.4",
    "device_fingerprint": "dev-abc", "ip_address": "203.0.113.7",
    "account_id": "user-42", "merchandise": "Pro subscription",
}
PRIORS = [
    {"transaction_id": "txn_8001", "date": "2026-01-10", "amount": 59.99, "currency": "USD",
     "device_fingerprint": "dev-abc", "ip_address": "198.51.100.3",
     "account_id": "user-42", "merchandise": "Pro subscription", "disputed": False},
    {"transaction_id": "txn_8002", "date": "2025-12-01", "amount": 59.99, "currency": "USD",
     "device_fingerprint": "dev-other", "ip_address": "203.0.113.7",
     "account_id": "user-42", "merchandise": "Pro subscription", "disputed": False},
    {"transaction_id": "txn_8003", "date": "2026-08-01", "amount": 59.99, "currency": "USD",
     "device_fingerprint": "dev-abc", "ip_address": "203.0.113.7",
     "account_id": "user-42", "merchandise": "Pro subscription", "disputed": False},
]

if __name__ == "__main__":
    print("== Eligibility ==")
    print(json.dumps(check_ce30_eligibility(DISPUTED, PRIORS), indent=2))
    print("\n== Qualifying only ==")
    print(json.dumps(match_qualifying_transactions(DISPUTED, PRIORS), indent=2))
    print("\n== Evidence packet ==")
    print(generate_evidence_packet(DISPUTED, PRIORS, merchant_name="Example Store Ltd"))
