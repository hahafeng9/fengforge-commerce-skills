#!/usr/bin/env python3
"""Full test suite for the FengForge Commerce Skills bundle.

Runs every server's --test self-test (subprocess) plus direct unit
assertions against each package's core module.

    python3 tests/run_all_tests.py

Exits 0 only if everything passes.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "chargeback-ce30"))
sys.path.insert(0, str(ROOT / "dunning-smart-retry"))
sys.path.insert(0, str(ROOT / "ofac-screener"))

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    status = "ok" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if (detail and not condition) else ""))
    if not condition:
        FAILURES.append(f"{label}: {detail}")


def run_server_tests() -> None:
    print("== server --test (subprocess) ==")
    servers = [
        ROOT / "chargeback-ce30" / "fengforge_chargeback_ce30" / "server.py",
        ROOT / "dunning-smart-retry" / "fengforge_dunning" / "server.py",
        ROOT / "ofac-screener" / "fengforge_ofac" / "server.py",
    ]
    for server in servers:
        proc = subprocess.run([sys.executable, str(server), "--test"],
                              capture_output=True, text=True, timeout=60)
        check(f"{server.parent.name} --test exit 0", proc.returncode == 0,
              proc.stdout + proc.stderr)
        if proc.stdout.strip():
            print(f"      {proc.stdout.strip().splitlines()[-1]}")


def test_ce30() -> None:
    print("== chargeback CE3.0 unit assertions ==")
    from fengforge_chargeback_ce30.core import (check_ce30_eligibility,
                                                generate_evidence_packet,
                                                match_qualifying_transactions)

    disputed = {
        "transaction_id": "D1", "date": "2026-09-20", "dispute_date": "2026-10-15",
        "amount": 49.0, "currency": "USD", "reason_code": "10.4",
        "device_fingerprint": "dev-1", "ip_address": "203.0.113.9",
        "account_id": "user-7", "merchandise": "Cloud storage plan",
    }
    good = [
        {"transaction_id": "P1", "date": "2026-02-01", "amount": 49.0, "currency": "USD",
         "device_fingerprint": "dev-1", "ip_address": "198.51.100.2",
         "account_id": "user-7", "merchandise": "Cloud storage plan", "disputed": False},
        {"transaction_id": "P2", "date": "2025-11-20", "amount": 49.0, "currency": "USD",
         "device_fingerprint": "dev-2", "ip_address": "203.0.113.9",
         "account_id": "user-8", "merchandise": "Cloud storage plan", "disputed": False},
    ]
    result = check_ce30_eligibility(disputed, good)
    check("CE3.0 eligible case verdict", result["eligible"] is True, str(result))
    check("CE3.0 qualifying count == 2", result["qualifying_count"] == 2)
    check("CE3.0 >=2 distinct elements across set",
          len(result["elements_matched_across_set"]) >= 2, str(result))
    check("CE3.0 merchandise bonus flagged", result["merchandise_match_bonus"] is True)

    too_recent = dict(good[0], transaction_id="P3", date="2026-07-27")  # 80 days
    result_recent = check_ce30_eligibility(disputed, [too_recent, good[1]])
    p3 = [r for r in result_recent["per_transaction"] if r["transaction_id"] == "P3"][0]
    check("CE3.0 80-day tx rejected as too recent",
          p3["qualifies"] is False and any("too recent" in r for r in p3["fail_reasons"]), str(p3))
    check("CE3.0 one-qualifying case ineligible with gap note",
          result_recent["eligible"] is False
          and any("only 1 qualifying" in g for g in result_recent["gaps"]), str(result_recent))

    too_old = dict(good[0], transaction_id="P4", date="2025-09-01")  # >365 days
    result_old = check_ce30_eligibility(disputed, [too_old, good[1]])
    p4 = [r for r in result_old["per_transaction"] if r["transaction_id"] == "P4"][0]
    check("CE3.0 >365-day tx rejected as too old",
          p4["qualifies"] is False and any("too old" in r for r in p4["fail_reasons"]), str(p4))

    was_disputed = dict(good[0], transaction_id="P5", disputed=True)
    result_disp = check_ce30_eligibility(disputed, [was_disputed, good[1]])
    p5 = [r for r in result_disp["per_transaction"] if r["transaction_id"] == "P5"][0]
    check("CE3.0 previously-disputed tx rejected",
          p5["qualifies"] is False and any("disputed" in r for r in p5["fail_reasons"]), str(p5))

    # Only one distinct element across the set -> ineligible even with 2 qualifying tx.
    one_element = [
        dict(good[0], transaction_id="P6", ip_address="192.0.2.1", account_id="user-x"),
        dict(good[1], transaction_id="P7", device_fingerprint="dev-1",
             ip_address="192.0.2.2", account_id="user-y"),
    ]
    result_one_el = check_ce30_eligibility(disputed, one_element)
    check("CE3.0 single-element set ineligible",
          result_one_el["eligible"] is False
          and result_one_el["elements_matched_across_set"] == ["device_fingerprint"],
          str(result_one_el))

    matched = match_qualifying_transactions(disputed, good)
    check("match_qualifying returns both", matched["qualifying_count"] == 2)

    packet = generate_evidence_packet(disputed, good, merchant_name="Example Store Ltd")
    for section in ("Case summary", "Qualifying prior transactions",
                    "Core data element matches", "Timeline", "Merchant declaration"):
        check(f"packet contains '{section}'", section in packet)
    check("packet is substantial text", len(packet) > 800, f"len={len(packet)}")


def test_dunning() -> None:
    print("== dunning smart-retry unit assertions ==")
    from fengforge_dunning.core import (DECLINE_TABLE, MAX_RETRIES, assess_account,
                                        build_retry_schedule, triage_decline)

    stripe_codes = ["insufficient_funds", "generic_decline", "do_not_honor", "expired_card",
                    "incorrect_cvc", "lost_card", "stolen_card", "card_velocity_exceeded",
                    "processing_error", "authentication_required", "approve_with_id",
                    "issuer_not_available", "reenter_transaction", "try_again_later",
                    "withdrawal_count_limit_exceeded", "currency_not_supported",
                    "duplicate_transaction", "fraudulent", "pickup_card"]
    iso_codes = ["51", "54", "57", "58", "61", "65", "91", "96", "05", "14", "04", "41", "43"]
    for code in stripe_codes + iso_codes:
        check(f"decline table covers {code}", code in DECLINE_TABLE)
    check("decline table has >=33 codes", len(DECLINE_TABLE) >= 33, str(len(DECLINE_TABLE)))

    hard_or_terminal = ["expired_card", "incorrect_cvc", "lost_card", "stolen_card",
                        "authentication_required", "fraudulent", "pickup_card",
                        "54", "57", "58", "14", "04", "41", "43"]
    for code in hard_or_terminal:
        triage = triage_decline(code)
        sched = build_retry_schedule(code, "2026-10-23T10:00:00Z")
        check(f"hard/terminal {code} never auto-retried",
              triage["retryable"] is False and sched["schedule"] == [], str(triage))

    sched = build_retry_schedule("processing_error", "2026-10-23T10:00:00Z")
    check("soft schedule non-empty and capped",
          1 <= len(sched["schedule"]) <= MAX_RETRIES, str(sched))
    stamps = [datetime.fromisoformat(a["at"]) for a in sched["schedule"]]
    check("soft schedule timestamps are real, increasing ISO timestamps",
          stamps == sorted(stamps) and len(set(stamps)) == len(stamps), str(stamps))

    capped = build_retry_schedule("processing_error", "2026-10-23T10:00:00Z",
                                  attempts_already_made=MAX_RETRIES)
    check("cap honours attempts already made", capped["attempts_planned"] == 0, str(capped))

    monthly = build_retry_schedule("insufficient_funds", "2026-10-23T10:00:00Z",
                                   {"type": "monthly"})
    check("insufficient_funds snaps to payday (monthly 1st/15th)",
          monthly["schedule"] and monthly["schedule"][0]["snapped_to_payday"] is True
          and monthly["schedule"][0]["at"].startswith("2026-11-01"), str(monthly))

    biweekly = build_retry_schedule("51", "2026-10-23T10:00:00Z",
                                    {"type": "biweekly", "anchor_date": "2026-10-30"})
    check("ISO 51 snaps to biweekly anchor payday",
          biweekly["schedule"] and biweekly["schedule"][0]["snapped_to_payday"] is True
          and biweekly["schedule"][0]["at"].startswith("2026-10-30"), str(biweekly))

    weekly = build_retry_schedule("insufficient_funds", "2026-10-23T10:00:00Z",
                                  {"type": "weekly", "anchor_date": "2026-10-30"})
    check("weekly pay cycle supported",
          weekly["schedule"] and weekly["schedule"][0]["at"].startswith("2026-10-30"),
          str(weekly))

    check("assess: soft failure -> continue",
          assess_account([{"code": "insufficient_funds"}])["recommendation"] == "continue")
    check("assess: hard failure -> card update",
          assess_account([{"code": "expired_card"}])["recommendation"] == "send_card_update_request")
    check("assess: terminal failure -> cancel-and-winback",
          assess_account([{"code": "41"}])["recommendation"] == "cancel-and-winback")
    check("assess: exhausted cap -> cancel-and-winback",
          assess_account([{"code": "insufficient_funds"}] * MAX_RETRIES)["recommendation"]
          == "cancel-and-winback")
    check("assess: unknown code gets single retry only",
          build_retry_schedule("some_new_code", "2026-10-23T10:00:00Z")["attempts_planned"] == 1)


def test_ofac() -> None:
    print("== OFAC screener unit assertions ==")
    from fengforge_ofac.core import (DEFAULT_THRESHOLD, generate_hold_memo,
                                     levenshtein, load_dataset, name_similarity,
                                     screen_batch, screen_entity)

    check("levenshtein kitten/sitting == 3", levenshtein("kitten", "sitting") == 3)
    entries = load_dataset()
    check("sample dataset has >=30 entries", len(entries) >= 30, str(len(entries)))

    for query, expected_name in (("Sberbank of Russia", "Sberbank"),
                                 ("Rosneft Oil Company", "Rosneft"),
                                 ("Huawei Technologies Co Ltd", "Huawei Technologies")):
        result = screen_entity({"name": query})
        top = result["matches"][0] if result["matches"] else {}
        check(f"OFAC hit: {query}",
              result["match_count"] >= 1 and top.get("matched_name") == expected_name
              and top.get("score", 0) >= DEFAULT_THRESHOLD, str(result))

    score = name_similarity("Sberbnk", "Sberbank")
    check("fuzzy typo 'Sberbnk' scores above threshold", score >= DEFAULT_THRESHOLD, str(score))
    fuzzy = screen_entity({"name": "Sberbnk of Russia"})
    check("fuzzy entity screen hits", fuzzy["match_count"] >= 1, str(fuzzy))
    check("legal suffix normalisation (Ltd/Inc/GmbH stripped)",
          name_similarity("Aurelia Coffee Ltd", "Aurelia Coffee") == 1.0)

    clear = screen_entity({"name": "Aurelia Coffee Roasters", "country": "GB"})
    check("clear name passes with low risk",
          clear["match_count"] == 0 and clear["risk_level"] == "low", str(clear))

    for country in ("CU", "IR", "KP", "SY", "Cuba", "Iran", "North Korea", "Syria"):
        res = screen_entity({"name": "Aurelia Coffee Roasters", "country": country})
        check(f"embargoed jurisdiction {country} flagged",
              res["jurisdiction_check"]["embargoed"] is True and res["risk_level"] == "critical",
              str(res))
    for region in ("Crimea", "Donetsk", "Luhansk"):
        res = screen_entity({"name": "Harbour Coffee", "country": "UA", "region": region})
        check(f"embargoed region {region} flagged",
              res["jurisdiction_check"]["embargoed"] is True, str(res))

    batch = screen_batch([{"name": "Rosneft Oil Company", "country": "RU"},
                          {"name": "Aurelia Coffee Roasters", "country": "GB"}])
    check("batch summary counts",
          batch["summary"] == {"total": 2, "clear": 1, "review": 0, "blocked_or_held": 1},
          str(batch["summary"]))

    memo = generate_hold_memo({"name": "Sberbank of Russia", "country": "RU"},
                              {"amount": 5000, "currency": "EUR", "reference": "R-1"})
    for needle in ("SANCTIONS HOLD MEMO", "Transaction under review", "Screening result",
                   "Required actions", "10 business days", "Disclaimer"):
        check(f"memo contains '{needle}'", needle in memo)
    check("memo is substantial text", len(memo) > 900, f"len={len(memo)}")


def main() -> int:
    run_server_tests()
    test_ce30()
    test_dunning()
    test_ofac()
    print()
    if FAILURES:
        print(f"RESULT: FAIL — {len(FAILURES)} assertion(s) failed:")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print("RESULT: PASS — all server self-tests and unit assertions passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
