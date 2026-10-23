#!/usr/bin/env python3
"""Runnable example for the FengForge OFAC Screener.

    python3 example_usage.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fengforge_ofac import generate_hold_memo, screen_batch, screen_entity  # noqa: E402

if __name__ == "__main__":
    print("== Screen: 'Sberbank of Russia' (alias hit expected) ==")
    print(json.dumps(screen_entity({"name": "Sberbank of Russia", "country": "RU"}), indent=2))
    print("\n== Screen: 'Aurelia Coffee Roasters Ltd' in GB (clear expected) ==")
    print(json.dumps(screen_entity({"name": "Aurelia Coffee Roasters Ltd", "country": "GB"}), indent=2))
    print("\n== Screen: clean name, embargoed jurisdiction (blocked expected) ==")
    print(json.dumps(screen_entity({"name": "Aurelia Coffee Roasters", "country": "IR"}), indent=2))
    print("\n== Batch ==")
    print(json.dumps(screen_batch([
        {"name": "Rosneft Oil Company", "country": "RU"},
        {"name": "Aurelia Coffee Roasters Ltd", "country": "GB"},
    ])["summary"], indent=2))
    print("\n== Hold memo ==")
    print(generate_hold_memo(
        {"name": "Sberbank of Russia", "country": "RU"},
        {"amount": 12000, "currency": "USD", "reference": "PAY-2026-0042",
         "date": "2026-10-22", "purpose": "Software licence payment"}))
