"""Subscription dunning: decline triage, smart retry scheduling, account assessment.

Original implementation by FengForge. Zero dependencies (stdlib only).

Design rules:
  * Declines are classified soft (retry may succeed), hard (retrying the same
    instrument is pointless until the customer fixes something), or terminal
    (the instrument/account must never be retried: lost, stolen, pick-up,
    confirmed fraud).
  * Hard and terminal declines are NEVER placed on an automatic retry
    schedule. The schedule builder returns an empty schedule for them.
  * Automatic retries are capped: MAX_RETRIES attempts over MAX_WINDOW_DAYS.
  * Retries for insufficient-funds soft declines are aligned to the
    customer's pay cycle when the next payday falls inside the snap window:
    retrying the day before payday is how merchants burn network retry
    budgets and train issuers to hard-decline them.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any

MAX_RETRIES = 4
MAX_WINDOW_DAYS = 14
PAYDAY_SNAP_WINDOW_DAYS = 7
RETRY_TIME = time(9, 0, tzinfo=timezone.utc)  # morning UTC: after most payroll runs

# category: "soft" | "hard" | "terminal"
# base_delay_days: delay before the FIRST automatic retry for soft declines.
DECLINE_TABLE: dict[str, dict[str, Any]] = {}


def _add(code, source, category, action, message, delay=2, note=""):
    DECLINE_TABLE[code] = {
        "code": code, "source": source, "category": category,
        "retryable": category == "soft",
        "recommended_action": action,
        "customer_message": message,
        "base_delay_days": delay, "note": note,
    }


# --- Stripe-style decline codes -------------------------------------------------
_add("insufficient_funds", "stripe", "soft", "retry_on_payday_schedule",
     "Your payment failed because of insufficient funds. We'll retry automatically — please make sure funds are available.",
     delay=3, note="Classic pay-cycle decline: align retries to payday.")
_add("generic_decline", "stripe", "soft", "retry_limited_then_card_update",
     "Your bank declined the payment without giving a specific reason. We'll retry once; updating your card may help.",
     delay=2, note="Vague issuer decline. One or two retries are reasonable, then ask for a new card.")
_add("do_not_honor", "stripe", "soft", "retry_limited_then_card_update",
     "Your bank declined the payment (do not honor). We'll retry a limited number of times; you may need to approve the payment with your bank.",
     delay=2, note="ISO 05 equivalent. Limited retries only.")
_add("expired_card", "stripe", "hard", "send_card_update_request",
     "Your card has expired. Please update your payment method to keep your subscription active.")
_add("incorrect_cvc", "stripe", "hard", "send_card_update_request",
     "The security code on file doesn't match. Please re-enter your card details.")
_add("lost_card", "stripe", "terminal", "cancel_and_winback",
     "The card on file has been reported lost. Your subscription has been cancelled; you can restart any time with a new card.")
_add("stolen_card", "stripe", "terminal", "cancel_and_winback",
     "The card on file has been reported stolen. Your subscription has been cancelled for your protection.")
_add("card_velocity_exceeded", "stripe", "soft", "retry_with_longer_backoff",
     "Too many payment attempts were made in a short period. We'll retry later automatically.",
     delay=4, note="Back off properly — velocity flags punish impatient retry loops.")
_add("processing_error", "stripe", "soft", "retry_short_backoff",
     "A temporary processing error occurred. We'll retry automatically.", delay=1)
_add("authentication_required", "stripe", "hard", "send_authentication_request",
     "Your bank requires you to authenticate this payment. Please complete the verification to continue.")
_add("approve_with_id", "stripe", "hard", "send_authentication_request",
     "Your bank can approve this payment after verifying your identity. Please contact your bank or confirm in your banking app.")
_add("issuer_not_available", "stripe", "soft", "retry_short_backoff",
     "Your bank was temporarily unavailable. We'll retry automatically.", delay=1)
_add("reenter_transaction", "stripe", "soft", "retry_once_short_backoff",
     "The payment needs to be re-submitted. We'll retry automatically.", delay=1)
_add("try_again_later", "stripe", "soft", "retry_short_backoff",
     "The payment couldn't be processed right now. We'll retry automatically.", delay=1)
_add("withdrawal_count_limit_exceeded", "stripe", "soft", "retry_next_cycle",
     "A withdrawal limit on your account was reached. We'll retry in the next cycle.",
     delay=5, note="ISO 65 equivalent: frequency limit, wait for the counter to reset.")
_add("currency_not_supported", "stripe", "hard", "send_card_update_request",
     "Your card doesn't support this currency. Please use a different payment method.")
_add("duplicate_transaction", "stripe", "hard", "stop_and_reconcile",
     "This looks like a duplicate payment and was stopped. No further action is needed unless your subscription lapses.")
_add("fraudulent", "stripe", "terminal", "cancel_and_winback",
     "This payment was flagged as fraudulent and the subscription has been cancelled.")
_add("pickup_card", "stripe", "terminal", "cancel_and_winback",
     "Your bank has instructed that this card be retained. The subscription has been cancelled.")
_add("account_closed", "stripe", "hard", "send_card_update_request",
     "The account linked to this card has been closed. Please update your payment method.")

# --- ISO 8583 response codes ------------------------------------------------------
_add("51", "iso8583", "soft", "retry_on_payday_schedule",
     "Insufficient funds. We'll retry automatically around your payday.", delay=3)
_add("54", "iso8583", "hard", "send_card_update_request",
     "Your card has expired. Please update your payment method.")
_add("57", "iso8583", "hard", "send_card_update_request",
     "This transaction type isn't permitted on your card. Please use a different payment method.")
_add("58", "iso8583", "hard", "stop_and_fix_configuration",
     "This transaction isn't permitted for this merchant terminal/configuration. (Merchant-side issue — do not retry the customer.)")
_add("61", "iso8583", "soft", "retry_smaller_or_next_cycle",
     "The amount exceeds your account's withdrawal limit. We'll retry in the next cycle.", delay=4)
_add("65", "iso8583", "soft", "retry_next_cycle",
     "Your account's withdrawal frequency limit was reached. We'll retry in the next cycle.", delay=5)
_add("91", "iso8583", "soft", "retry_short_backoff",
     "Your bank was temporarily unavailable. We'll retry automatically.", delay=1)
_add("96", "iso8583", "soft", "retry_short_backoff",
     "A system error occurred while processing. We'll retry automatically.", delay=1)
_add("05", "iso8583", "soft", "retry_limited_then_card_update",
     "Your bank declined the payment (do not honor). We'll retry a limited number of times.", delay=2)
_add("14", "iso8583", "hard", "send_card_update_request",
     "The card number on file is invalid. Please update your payment method.")
_add("04", "iso8583", "terminal", "cancel_and_winback",
     "Your bank has instructed that this card be retained (pick-up). The subscription has been cancelled.")
_add("41", "iso8583", "terminal", "cancel_and_winback",
     "The card on file has been reported lost. The subscription has been cancelled.")
_add("43", "iso8583", "terminal", "cancel_and_winback",
     "The card on file has been reported stolen. The subscription has been cancelled.")


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def lookup_decline(code: Any) -> dict | None:
    """Look up a decline code. Numeric strings hit the ISO 8583 table."""
    if code is None:
        return None
    key = str(code).strip().lower()
    if key in DECLINE_TABLE:
        return DECLINE_TABLE[key]
    # Normalise ISO codes given as integers or without leading zero ("5" -> "05").
    if key.isdigit() and len(key) <= 2:
        padded = key.zfill(2)
        if padded in DECLINE_TABLE:
            return DECLINE_TABLE[padded]
    return None


def triage_decline(code: Any, source: str | None = None) -> dict:
    """Classify one decline code: category, retryability, action, message."""
    entry = lookup_decline(code)
    if entry is None:
        return {
            "code": str(code), "known": False, "category": "soft",
            "retryable": True, "max_retries": 1,
            "recommended_action": "retry_once_then_review",
            "customer_message": "Your payment failed. We'll retry once; if it fails again, please check your payment method.",
            "note": ("Unknown decline code: treated conservatively as soft with a "
                     "single retry, then human/agent review. Never loop on unknown codes."),
        }
    result = dict(entry)
    result["known"] = True
    result["max_retries"] = MAX_RETRIES if entry["retryable"] else 0
    result["never_auto_retry"] = entry["category"] in ("hard", "terminal")
    return result


def next_payday(after: date, pay_cycle: dict | None) -> date | None:
    """Next payday strictly on/after `after` for the given pay cycle.

    pay_cycle formats:
      {"type": "weekly",   "anchor_date": "2026-10-02"}   # anchor is a payday
      {"type": "biweekly", "anchor_date": "2026-10-02"}   # anchor is a payday
      {"type": "monthly"}                                  # 1st and 15th
    Weekly without an anchor defaults to Fridays.
    """
    if not pay_cycle:
        return None
    kind = str(pay_cycle.get("type") or "").lower()
    if kind == "monthly":
        candidates = []
        for month_offset in (0, 1):
            year = after.year + (after.month - 1 + month_offset) // 12
            month = (after.month - 1 + month_offset) % 12 + 1
            for day in (1, 15):
                cand = date(year, month, day)
                if cand >= after:
                    candidates.append(cand)
        return min(candidates) if candidates else None
    if kind in ("weekly", "biweekly"):
        period = 7 if kind == "weekly" else 14
        anchor_raw = pay_cycle.get("anchor_date") or pay_cycle.get("anchor")
        if anchor_raw:
            anchor = _parse_dt(anchor_raw).date()
            delta = (after - anchor).days
            steps = -(-delta // period)  # ceil division
            return anchor + timedelta(days=steps * period)
        if kind == "weekly":
            # Default: Friday (weekday 4).
            delta = (4 - after.weekday()) % 7
            return after + timedelta(days=delta)
        return None
    return None


def build_retry_schedule(code: Any, start: Any, pay_cycle: dict | None = None,
                         attempts_already_made: int = 0) -> dict:
    """Build an automatic retry schedule as ISO timestamps.

    Hard/terminal declines return an EMPTY schedule — never auto-retry them.
    Soft declines get an exponential-ish backoff (1x, 2x, 3x, 4x of the base
    delay), capped at MAX_RETRIES total attempts within MAX_WINDOW_DAYS.
    Insufficient-funds retries snap to the next payday when it falls within
    PAYDAY_SNAP_WINDOW_DAYS of the planned attempt.
    """
    triage = triage_decline(code)
    start_dt = _parse_dt(start)
    window_end = start_dt + timedelta(days=MAX_WINDOW_DAYS)

    if not triage["retryable"]:
        return {
            "code": triage["code"], "category": triage["category"],
            "retryable": False, "schedule": [], "attempts_planned": 0,
            "max_retries_cap": MAX_RETRIES,
            "rationale": (f"{triage['category']} decline — automatic retries are never "
                          f"scheduled. Required action instead: {triage['recommended_action']}."),
            "recommended_action": triage["recommended_action"],
            "customer_message": triage["customer_message"],
        }

    remaining = max(0, MAX_RETRIES - int(attempts_already_made))
    unknown_code = not triage["known"]
    if unknown_code:
        remaining = min(remaining, 1)
    base = int(triage.get("base_delay_days") or 2)
    is_funds = triage["code"] in ("insufficient_funds", "51")

    schedule: list[dict] = []
    cumulative = 0
    for i in range(remaining):
        cumulative += base * (i + 1) if i else base
        planned_date = (start_dt + timedelta(days=cumulative)).date()
        snapped = False
        payday = None
        if is_funds and pay_cycle:
            payday = next_payday(planned_date, pay_cycle)
            if payday and 0 <= (payday - planned_date).days <= PAYDAY_SNAP_WINDOW_DAYS:
                planned_date = payday
                snapped = True
        when = datetime.combine(planned_date, RETRY_TIME)
        if when > window_end:
            break
        schedule.append({
            "attempt": int(attempts_already_made) + i + 1,
            "at": when.isoformat(),
            "snapped_to_payday": snapped,
            "payday": payday.isoformat() if (snapped and payday) else None,
        })

    rationale = (f"Soft decline: up to {MAX_RETRIES} automatic retries within "
                 f"{MAX_WINDOW_DAYS} days (cap enforced).")
    if is_funds and pay_cycle:
        rationale += (" Insufficient-funds retries snap to the next payday when it "
                      f"falls within {PAYDAY_SNAP_WINDOW_DAYS} days of the planned attempt.")
    if unknown_code:
        rationale += " Unknown code: capped at a single retry pending review."
    return {
        "code": triage["code"], "category": triage["category"], "retryable": True,
        "schedule": schedule, "attempts_planned": len(schedule),
        "max_retries_cap": MAX_RETRIES, "window_days": MAX_WINDOW_DAYS,
        "rationale": rationale,
        "recommended_action": triage["recommended_action"],
        "customer_message": triage["customer_message"],
    }


def assess_account(attempt_history: list[dict]) -> dict:
    """Recommend the next account-level action from a payment attempt history.

    attempt_history items: {"code": ..., "date": ..., "succeeded": bool (optional)}.
    Recommendation is one of: "continue", "send_card_update_request",
    "cancel-and-winback".
    """
    history = attempt_history or []
    reasons: list[str] = []
    soft = hard = terminal = 0
    succeeded = False
    codes_seen: list[str] = []
    for item in history:
        if item.get("succeeded") or str(item.get("status", "")).lower() in ("succeeded", "success", "paid"):
            succeeded = True
            continue
        entry = lookup_decline(item.get("code"))
        codes_seen.append(str(item.get("code")))
        if entry is None:
            soft += 1  # unknown codes counted as soft failures, conservatively
            continue
        if entry["category"] == "terminal":
            terminal += 1
        elif entry["category"] == "hard":
            hard += 1
        else:
            soft += 1

    total_failures = soft + hard + terminal
    if succeeded and total_failures == 0:
        recommendation = "continue"
        reasons.append("latest attempt succeeded; no action needed")
    elif terminal:
        recommendation = "cancel-and-winback"
        reasons.append(f"{terminal} terminal decline(s) ({', '.join(sorted(set(codes_seen)))}) — "
                       "the instrument must never be retried; cancel now and run a winback "
                       "offer that requires a brand-new payment method")
    elif hard:
        recommendation = "send_card_update_request"
        reasons.append(f"{hard} hard decline(s) — retrying the same instrument cannot work; "
                       "send a card-update (dunning) request and pause retries until the "
                       "customer updates the payment method")
    elif total_failures >= MAX_RETRIES:
        recommendation = "cancel-and-winback"
        reasons.append(f"{total_failures} failed attempts reached the {MAX_RETRIES}-retry cap — "
                       "further retries damage issuer reputation; cancel and switch to winback")
    elif total_failures:
        recommendation = "continue"
        reasons.append(f"{soft} soft failure(s), {MAX_RETRIES - total_failures} retries remain "
                       "under the cap — continue the smart retry schedule")
    else:
        recommendation = "continue"
        reasons.append("no failed attempts on record")

    return {
        "recommendation": recommendation,
        "reasons": reasons,
        "counts": {"soft_failures": soft, "hard_failures": hard,
                   "terminal_failures": terminal, "total_failures": total_failures,
                   "succeeded": succeeded},
        "max_retries_cap": MAX_RETRIES,
    }
