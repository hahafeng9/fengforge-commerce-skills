"""Visa Compelling Evidence 3.0 (CE3.0) eligibility and evidence compiler.

Original implementation by FengForge. Zero dependencies (stdlib only).

Rules implemented (Visa CE3.0, reason code 10.4 / fraud disputes):
  * At least TWO prior transactions qualify.
  * Each qualifying prior transaction must be UNDISPUTED.
  * Each qualifying prior transaction must be aged 120-365 days before the
    dispute date (measured against the disputed transaction's dispute date).
  * Across the qualifying set, at least TWO distinct core data elements must
    match the disputed transaction. Core elements implemented here:
    device fingerprint, IP address, and account / login ID.
  * A merchandise/service match between a qualifying transaction and the
    disputed transaction is flagged as a strengthening bonus (it is not a
    substitute for the core-element requirement).

This module is decision support only. Issuer acceptance of a CE3.0
representment is never guaranteed.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

CORE_ELEMENTS = ("device_fingerprint", "ip_address", "account_id")
MIN_AGE_DAYS = 120
MAX_AGE_DAYS = 365
MIN_QUALIFYING = 2
MIN_DISTINCT_ELEMENTS = 2


def parse_date(value: Any) -> date | None:
    """Parse an ISO date/datetime string (or date object) into a date."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def _account_id(tx: dict) -> str:
    for key in ("account_id", "login_id", "account_login_id", "customer_id", "user_id"):
        val = tx.get(key)
        if val:
            return str(val).strip()
    return ""


def _element_value(tx: dict, element: str) -> str:
    if element == "account_id":
        return _account_id(tx)
    val = tx.get(element)
    return str(val).strip() if val else ""


def _merchandise(tx: dict) -> str:
    for key in ("merchandise", "product", "service", "item", "sku_category", "category"):
        val = tx.get(key)
        if val:
            return str(val).strip().lower()
    return ""


def evaluate_transaction(disputed: dict, prior: dict, dispute_date: date) -> dict:
    """Evaluate one prior transaction against the CE3.0 qualifying rules."""
    reasons: list[str] = []
    passed_checks: list[str] = []

    tx_date = parse_date(prior.get("date") or prior.get("transaction_date"))
    age_days = (dispute_date - tx_date).days if tx_date else None

    is_disputed = bool(prior.get("disputed") or prior.get("was_disputed")
                        or prior.get("chargeback") or prior.get("has_chargeback"))
    if is_disputed:
        reasons.append("transaction was previously disputed (CE3.0 requires undisputed history)")
    else:
        passed_checks.append("undisputed")

    if age_days is None:
        reasons.append("missing or unparseable transaction date")
    elif age_days < MIN_AGE_DAYS:
        reasons.append(f"tx too recent: {age_days}d (< {MIN_AGE_DAYS}d minimum)")
    elif age_days > MAX_AGE_DAYS:
        reasons.append(f"tx too old: {age_days}d (> {MAX_AGE_DAYS}d maximum)")
    else:
        passed_checks.append(f"age {age_days}d within {MIN_AGE_DAYS}-{MAX_AGE_DAYS}d")

    elements_matched: list[str] = []
    for element in CORE_ELEMENTS:
        disputed_val = _element_value(disputed, element)
        prior_val = _element_value(prior, element)
        if disputed_val and prior_val and disputed_val == prior_val:
            elements_matched.append(element)
    if elements_matched:
        passed_checks.append("core elements matched: " + ", ".join(elements_matched))
    else:
        reasons.append("no core data elements matched the disputed transaction")

    merchandise_match = bool(
        _merchandise(disputed) and _merchandise(disputed) == _merchandise(prior)
    )
    if merchandise_match:
        passed_checks.append("merchandise/service matches the disputed transaction")

    qualifies = (not is_disputed and age_days is not None
                 and MIN_AGE_DAYS <= age_days <= MAX_AGE_DAYS and bool(elements_matched))

    return {
        "transaction_id": prior.get("transaction_id") or prior.get("id") or "(no id)",
        "date": prior.get("date") or prior.get("transaction_date"),
        "amount": prior.get("amount"),
        "currency": prior.get("currency"),
        "age_days": age_days,
        "qualifies": qualifies,
        "passed_checks": passed_checks,
        "fail_reasons": reasons,
        "elements_matched": elements_matched,
        "merchandise_match": merchandise_match,
    }


def _dispute_date(disputed: dict, explicit: Any = None) -> date | None:
    for candidate in (explicit, disputed.get("dispute_date"),
                      disputed.get("chargeback_date")):
        parsed = parse_date(candidate)
        if parsed:
            return parsed
    # Fall back to the disputed transaction date itself (documented behaviour).
    return parse_date(disputed.get("date") or disputed.get("transaction_date"))


def check_ce30_eligibility(disputed: dict, prior_transactions: list[dict],
                           dispute_date: Any = None) -> dict:
    """Full CE3.0 eligibility assessment for one disputed transaction."""
    d_date = _dispute_date(disputed, dispute_date)
    if d_date is None:
        return {
            "eligible": False,
            "verdict": "INDETERMINATE",
            "gaps": ["no dispute date available (pass dispute_date or set disputed.dispute_date)"],
            "per_transaction": [],
            "qualifying_transactions": [],
            "elements_matched_across_set": [],
            "merchandise_match_bonus": False,
        }

    per_tx = [evaluate_transaction(disputed, tx, d_date) for tx in (prior_transactions or [])]
    qualifying = [r for r in per_tx if r["qualifies"]]
    elements_across: list[str] = []
    for r in qualifying:
        for el in r["elements_matched"]:
            if el not in elements_across:
                elements_across.append(el)
    merchandise_bonus = any(r["merchandise_match"] for r in qualifying)

    gaps: list[str] = []
    if len(qualifying) < MIN_QUALIFYING:
        gaps.append(f"only {len(qualifying)} qualifying tx (need at least {MIN_QUALIFYING})")
    if len(elements_across) < MIN_DISTINCT_ELEMENTS:
        gaps.append(
            f"only {len(elements_across)} distinct core element(s) matched across the "
            f"qualifying set (need at least {MIN_DISTINCT_ELEMENTS}): "
            + (", ".join(elements_across) if elements_across else "none"))
    for r in per_tx:
        if not r["qualifies"]:
            for reason in r["fail_reasons"]:
                gaps.append(f"tx {r['transaction_id']}: {reason}")

    eligible = len(qualifying) >= MIN_QUALIFYING and len(elements_across) >= MIN_DISTINCT_ELEMENTS
    if eligible:
        verdict = "ELIGIBLE — CE3.0 criteria met; compile the evidence packet and submit representment."
    elif qualifying:
        verdict = "NOT YET ELIGIBLE — some history qualifies, but set-level criteria are unmet (see gaps)."
    else:
        verdict = "NOT ELIGIBLE — no qualifying prior transactions under CE3.0 rules."

    return {
        "eligible": eligible,
        "verdict": verdict,
        "dispute_date": d_date.isoformat(),
        "disputed_transaction_id": disputed.get("transaction_id") or disputed.get("id"),
        "qualifying_count": len(qualifying),
        "qualifying_transactions": [r["transaction_id"] for r in qualifying],
        "per_transaction": per_tx,
        "elements_matched_across_set": elements_across,
        "merchandise_match_bonus": merchandise_bonus,
        "gaps": gaps,
        "rules_applied": {
            "min_qualifying_transactions": MIN_QUALIFYING,
            "age_window_days": [MIN_AGE_DAYS, MAX_AGE_DAYS],
            "min_distinct_core_elements": MIN_DISTINCT_ELEMENTS,
            "core_elements": list(CORE_ELEMENTS),
            "prior_transactions_must_be_undisputed": True,
        },
    }


def match_qualifying_transactions(disputed: dict, prior_transactions: list[dict],
                                  dispute_date: Any = None) -> dict:
    """Return only the qualifying prior transactions, with match detail."""
    result = check_ce30_eligibility(disputed, prior_transactions, dispute_date)
    qualifying = [r for r in result["per_transaction"] if r["qualifies"]]
    return {
        "disputed_transaction_id": result["disputed_transaction_id"],
        "dispute_date": result.get("dispute_date"),
        "qualifying": qualifying,
        "qualifying_count": len(qualifying),
        "elements_matched_across_set": result["elements_matched_across_set"],
        "merchandise_match_bonus": result["merchandise_match_bonus"],
        "meets_minimum_count": len(qualifying) >= MIN_QUALIFYING,
    }


def generate_evidence_packet(disputed: dict, prior_transactions: list[dict],
                             dispute_date: Any = None, merchant_name: str = "") -> str:
    """Build a ready-to-submit Markdown representment packet (plain text)."""
    result = check_ce30_eligibility(disputed, prior_transactions, dispute_date)
    qualifying = [r for r in result["per_transaction"] if r["qualifies"]]
    merchant = merchant_name or disputed.get("merchant_name") or "(merchant name)"
    amount = disputed.get("amount")
    currency = disputed.get("currency") or ""
    amount_text = f"{amount} {currency}".strip() if amount is not None else "(amount not supplied)"

    lines: list[str] = []
    lines.append("# Chargeback Representment — Visa Compelling Evidence 3.0")
    lines.append("")
    lines.append(f"**Merchant:** {merchant}")
    lines.append(f"**Disputed transaction:** {result['disputed_transaction_id'] or '(id not supplied)'}")
    lines.append(f"**Dispute date:** {result.get('dispute_date') or '(unknown)'}")
    lines.append(f"**Disputed amount:** {amount_text}")
    if disputed.get("reason_code"):
        lines.append(f"**Reason code:** {disputed['reason_code']}")
    if disputed.get("customer_name"):
        lines.append(f"**Cardholder:** {disputed['customer_name']}")
    lines.append("")
    lines.append("## 1. Case summary")
    lines.append("")
    lines.append(
        "The cardholder disputes this transaction as fraud / not recognised. The merchant "
        "contends the transaction was made by the legitimate cardholder and submits this "
        "packet under Visa Compelling Evidence 3.0: at least two prior, undisputed "
        "transactions from the same cardholder, aged 120-365 days before the dispute date, "
        "share core data elements with the disputed transaction.")
    lines.append("")
    lines.append(f"**Eligibility verdict:** {result['verdict']}")
    lines.append(f"**Qualifying prior transactions:** {result['qualifying_count']}")
    lines.append("**Core elements matched across the set:** "
                 + (", ".join(result["elements_matched_across_set"]) or "none"))
    lines.append("**Merchandise/service match bonus:** "
                 + ("yes — at least one qualifying transaction is for the same "
                    "merchandise/service as the disputed transaction."
                    if result["merchandise_match_bonus"] else "no"))
    lines.append("")
    lines.append("## 2. Qualifying prior transactions")
    lines.append("")
    if qualifying:
        lines.append("| Transaction ID | Date | Amount | Age at dispute (days) | Merchandise match |")
        lines.append("|---|---|---|---|---|")
        for r in qualifying:
            lines.append(f"| {r['transaction_id']} | {r['date']} | "
                         f"{r['amount']} {r['currency'] or ''} | {r['age_days']} | "
                         f"{'yes' if r['merchandise_match'] else 'no'} |")
    else:
        lines.append("_No qualifying prior transactions were identified. Do not submit a "
                     "CE3.0 representment on this evidence; consider other compelling "
                     "evidence categories instead._")
    lines.append("")
    lines.append("## 3. Core data element matches")
    lines.append("")
    lines.append("| Transaction ID | Device fingerprint | IP address | Account / login ID |")
    lines.append("|---|---|---|---|")
    for r in qualifying:
        marks = {el: ("MATCH" if el in r["elements_matched"] else "—") for el in CORE_ELEMENTS}
        lines.append(f"| {r['transaction_id']} | {marks['device_fingerprint']} | "
                     f"{marks['ip_address']} | {marks['account_id']} |")
    lines.append("")
    lines.append("Element values are held in the merchant's transaction logs and can be "
                 "supplied in full to the issuer / acquirer on request. (Values are not "
                 "printed in this packet template to avoid spreading device/IP data into "
                 "documents that may be forwarded.)")
    lines.append("")
    lines.append("## 4. Timeline")
    lines.append("")
    timeline = sorted(result["per_transaction"], key=lambda r: str(r["date"]))
    for r in timeline:
        status = "qualifying" if r["qualifies"] else "not qualifying"
        lines.append(f"- {r['date']} — prior transaction {r['transaction_id']} "
                     f"({status}, age {r['age_days']}d at dispute)")
    lines.append(f"- {result.get('dispute_date')} — dispute raised on transaction "
                 f"{result['disputed_transaction_id'] or '(id not supplied)'}")
    lines.append("")
    lines.append("## 5. Gaps and weaknesses (merchant's own assessment)")
    lines.append("")
    if result["gaps"]:
        for gap in result["gaps"]:
            lines.append(f"- {gap}")
    else:
        lines.append("- None identified by the automated check.")
    lines.append("")
    lines.append("## 6. Merchant declaration")
    lines.append("")
    lines.append(
        f"I, on behalf of {merchant}, declare that the information in this packet is true "
        "and complete to the best of my knowledge, that the prior transactions listed were "
        "not previously disputed, and that the core data elements recorded above are taken "
        "from the merchant's own transaction logs for the same cardholder account.")
    lines.append("")
    lines.append("Name / title: ______________________    Date: ______________")
    lines.append("")
    lines.append("---")
    lines.append("_Generated with FengForge Chargeback CE3.0 Compiler. Decision support "
                 "only — not legal advice. Issuer acceptance is never guaranteed; review "
                 "the packet against your acquirer's current CE3.0 submission format "
                 "before filing._")
    return "\n".join(lines) + "\n"
