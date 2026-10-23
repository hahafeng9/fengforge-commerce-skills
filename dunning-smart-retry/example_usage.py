#!/usr/bin/env python3
"""Runnable example for FengForge Dunning Smart Retry.

    python3 example_usage.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fengforge_dunning import (assess_account, build_retry_schedule,  # noqa: E402
                               triage_decline)

if __name__ == "__main__":
    print("== Triage: insufficient_funds ==")
    print(json.dumps(triage_decline("insufficient_funds"), indent=2))
    print("\n== Triage: ISO 8583 code 54 (expired card) ==")
    print(json.dumps(triage_decline("54"), indent=2))
    print("\n== Retry schedule: insufficient funds, monthly pay cycle (1st & 15th) ==")
    print(json.dumps(build_retry_schedule(
        "insufficient_funds", "2026-10-23T10:00:00Z", {"type": "monthly"}), indent=2))
    print("\n== Retry schedule: stolen card (must be empty) ==")
    print(json.dumps(build_retry_schedule("stolen_card", "2026-10-23T10:00:00Z"), indent=2))
    print("\n== Account assessment: 1 hard decline ==")
    print(json.dumps(assess_account([
        {"code": "insufficient_funds", "date": "2026-10-20"},
        {"code": "expired_card", "date": "2026-10-23"},
    ]), indent=2))
