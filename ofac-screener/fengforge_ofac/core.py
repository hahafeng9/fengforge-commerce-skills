"""OFAC / denied-party name screening with real fuzzy matching.

Original implementation by FengForge. Zero dependencies (stdlib only).
No network access at runtime: screening runs against a local dataset.

Matching pipeline (deliberately explainable, unlike a bare substring test):
  1. Normalisation — Unicode accent folding, lower-casing, punctuation
     removal, legal-suffix stripping (Ltd, Inc, LLC, GmbH, PJSC, ...), and a
     small transliteration-variant canonicalisation (ph->f, muhammad
     variants, doubled-consonant collapse, ...).
  2. Scoring — a blend of character-level Levenshtein ratio, token Dice
     coefficient, and token containment, computed against each entry's name
     AND its aliases; the best score wins.
  3. Jurisdiction check — separately from name hits, counterparties in
     comprehensively sanctioned jurisdictions (CU, IR, KP, SY) or in the
     Crimea / Donetsk (DNR) / Luhansk (LNR) regions are flagged as
     embargoed. A name can be clean and the transaction still blocked.

The bundled dataset is a SAMPLE (see data/sdn_sample.json). Production use
requires the full official list — see tools/fetch_sdn.py.
"""
from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Any

DEFAULT_THRESHOLD = 0.85
_DATA_PATH = Path(__file__).resolve().parent / "data" / "sdn_sample.json"

LEGAL_SUFFIXES = {
    "ltd", "limited", "inc", "incorporated", "llc", "llp", "lp", "corp",
    "corporation", "co", "company", "gmbh", "ag", "sa", "sas", "sarl", "bv",
    "nv", "vba", "plc", "pjsc", "pao", "oao", "zao", "ao", "jsc", "ooo",
    "sro", "sdn", "bhd", "kk", "gk", "pty", "sdn bhd", "as", "oy", "ab",
    "spa", "srl", "bvba", "kg", "kgaa", "ug",
}
STOPWORDS = {"the", "of", "and", "&"}
# Transliteration / spelling variant canonicalisation, applied per token.
_VARIANT_MAP = {
    "muhammad": "muhammad", "mohammed": "muhammad", "mohamed": "muhammad",
    "muhammed": "muhammad", "mohammad": "muhammad",
    "ahmad": "ahmad", "ahmed": "ahmad",
    "hussein": "husayn", "hussain": "husayn", "husain": "husayn",
    "osama": "usama", "ussama": "usama",
    "kaddafi": "gaddafi", "qaddafi": "gaddafi", "gadaffi": "gaddafi",
}

EMBARGOED_COUNTRIES = {
    "CU": "Cuba", "IR": "Iran", "KP": "North Korea (DPRK)", "SY": "Syria",
}
COUNTRY_NAME_TO_CODE = {
    "cuba": "CU", "iran": "IR", "islamic republic of iran": "IR",
    "north korea": "KP", "dprk": "KP", "democratic people's republic of korea": "KP",
    "syria": "SY", "syrian arab republic": "SY",
}
EMBARGOED_REGIONS = {
    "crimea": "Crimea region of Ukraine",
    "donetsk": "Donetsk (DNR) region of Ukraine",
    "dnr": "Donetsk (DNR) region of Ukraine",
    "donetsk people's republic": "Donetsk (DNR) region of Ukraine",
    "donetsk peoples republic": "Donetsk (DNR) region of Ukraine",
    "luhansk": "Luhansk (LNR) region of Ukraine",
    "lugansk": "Luhansk (LNR) region of Ukraine",
    "lnr": "Luhansk (LNR) region of Ukraine",
    "luhansk people's republic": "Luhansk (LNR) region of Ukraine",
}


# --- Normalisation & similarity ---------------------------------------------------

def _fold_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFD", text)
                   if unicodedata.category(ch) != "Mn")


def _canonical_token(token: str) -> str:
    token = _VARIANT_MAP.get(token, token)
    token = token.replace("ph", "f").replace("sch", "sh").replace("tch", "ch")
    token = token.replace("kh", "h").replace("zh", "z")
    # Collapse doubled consonants (common transliteration noise: ll->l etc.)
    out: list[str] = []
    for ch in token:
        if out and out[-1] == ch and ch not in "aeiou":
            continue
        out.append(ch)
    return "".join(out)


def normalize_name(name: Any) -> list[str]:
    """Return the normalised token list for a name."""
    text = _fold_accents(str(name or "")).lower()
    text = "".join(ch if ch.isalnum() else " " for ch in text)
    tokens = []
    for token in text.split():
        if token in LEGAL_SUFFIXES or token in STOPWORDS:
            continue
        tokens.append(_canonical_token(token))
    return tokens


def levenshtein(a: str, b: str) -> int:
    """Classic dynamic-programming Levenshtein distance."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(
                previous[j] + 1,                 # deletion
                current[j - 1] + 1,              # insertion
                previous[j - 1] + (ca != cb),    # substitution
            ))
        previous = current
    return previous[len(b)]


def _lev_ratio(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    return 1.0 - levenshtein(a, b) / max(len(a), len(b))


def _soft_token_score(tokens_a: list[str], tokens_b: list[str]) -> float:
    """Token alignment score: average best per-token Levenshtein ratio, both ways.

    A strict token Dice punishes a one-character typo in a single token as
    harshly as a completely different word; aligning tokens fuzzily first is
    what makes this a fuzzy matcher rather than a dressed-up exact matcher.
    """
    def _direction(src: list[str], dst: list[str]) -> float:
        if not src:
            return 0.0
        return sum(max(_lev_ratio(tok, other) for other in dst) for tok in src) / len(src)
    return (_direction(tokens_a, tokens_b) + _direction(tokens_b, tokens_a)) / 2


def name_similarity(name_a: Any, name_b: Any) -> float:
    """Similarity in [0, 1]: joined Levenshtein + fuzzy token alignment + containment."""
    tokens_a, tokens_b = normalize_name(name_a), normalize_name(name_b)
    if not tokens_a or not tokens_b:
        return 0.0
    joined_a, joined_b = " ".join(tokens_a), " ".join(tokens_b)
    if joined_a == joined_b:
        return 1.0
    lev = _lev_ratio(joined_a, joined_b)
    soft_tokens = _soft_token_score(tokens_a, tokens_b)
    set_a, set_b = set(tokens_a), set(tokens_b)
    # Fuzzy containment: fraction of the smaller token set that has a close
    # (ratio >= 0.8) partner in the larger set.
    smaller, larger = (set_a, set_b) if len(set_a) <= len(set_b) else (set_b, set_a)
    matched = sum(1 for tok in smaller
                  if max((_lev_ratio(tok, other) for other in larger), default=0.0) >= 0.8)
    containment = matched / len(smaller) if smaller else 0.0
    score = 0.40 * lev + 0.35 * soft_tokens + 0.25 * containment
    # A full exact-token subset (query carries an extra descriptor word, or
    # an entry is known by a shorter form) should stay high.
    if set_a <= set_b or set_b <= set_a:
        score = max(score, 0.88 + 0.12 * lev)
    return round(min(1.0, score), 4)


# --- Dataset ------------------------------------------------------------------------

def load_dataset(path: str | None = None) -> list[dict]:
    data_file = Path(path) if path else _DATA_PATH
    with open(data_file, encoding="utf-8") as fh:
        payload = json.load(fh)
    return payload["entries"] if isinstance(payload, dict) else payload


_DATASET_CACHE: list[dict] | None = None


def _dataset() -> list[dict]:
    global _DATASET_CACHE
    if _DATASET_CACHE is None:
        _DATASET_CACHE = load_dataset()
    return _DATASET_CACHE


# --- Jurisdiction check ---------------------------------------------------------------

def check_jurisdiction(entity: dict) -> dict:
    """Comprehensively-sanctioned jurisdiction check (separate from name hits)."""
    raw_country = str(entity.get("country") or entity.get("country_code")
                      or entity.get("jurisdiction") or "").strip()
    region = str(entity.get("region") or "").strip().lower()
    address = str(entity.get("address") or "").lower()

    code = raw_country.upper()
    if code not in EMBARGOED_COUNTRIES:
        code = COUNTRY_NAME_TO_CODE.get(raw_country.lower(), code)
    if code in EMBARGOED_COUNTRIES:
        return {"embargoed": True, "matched_on": "country",
                "jurisdiction": EMBARGOED_COUNTRIES[code], "code": code,
                "reason": f"counterparty jurisdiction {EMBARGOED_COUNTRIES[code]} ({code}) "
                          "is comprehensively sanctioned"}

    for key, label in EMBARGOED_REGIONS.items():
        if region == key or key in address or (region and key in region):
            return {"embargoed": True, "matched_on": "region",
                    "jurisdiction": label, "code": key.upper(),
                    "reason": f"counterparty is located in the {label}, which is "
                              "subject to comprehensive sanctions"}
    return {"embargoed": False, "matched_on": None,
            "jurisdiction": raw_country or None, "code": code or None,
            "reason": "jurisdiction is not on the comprehensively-sanctioned list "
                      "(country/region level)"}


# --- Screening -------------------------------------------------------------------------

def screen_entity(entity: dict, threshold: float = DEFAULT_THRESHOLD,
                  dataset: list[dict] | None = None) -> dict:
    """Screen one entity: name fuzzy match + jurisdiction check + risk action."""
    name = entity.get("name") or ""
    entries = dataset if dataset is not None else _dataset()
    matches: list[dict] = []
    for entry in entries:
        candidates = [entry["name"]] + list(entry.get("aliases") or [])
        best_score, best_field = 0.0, entry["name"]
        for candidate in candidates:
            score = name_similarity(name, candidate)
            if score > best_score:
                best_score, best_field = score, candidate
        if best_score >= threshold:
            matches.append({
                "matched_name": entry["name"],
                "matched_on": best_field,
                "score": best_score,
                "list": entry.get("list"),
                "country": entry.get("country"),
                "program": entry.get("program"),
                "type": entry.get("type"),
                "matched_fields": ["name"] + (["alias"] if best_field != entry["name"] else []),
            })
    matches.sort(key=lambda m: m["score"], reverse=True)
    jurisdiction = check_jurisdiction(entity)

    if jurisdiction["embargoed"]:
        risk, action = "critical", ("BLOCK AND HOLD — comprehensively sanctioned "
                                    "jurisdiction. Do not process, do not return funds "
                                    "without a licence decision; escalate to compliance "
                                    "and prepare an OFAC report.")
    elif matches and matches[0]["score"] >= 0.95:
        risk, action = "high", ("HOLD — near-exact match to a listed party. Freeze the "
                                "transaction, escalate to compliance, and do not release "
                                "without documented clearance.")
    elif matches:
        risk, action = "medium", ("HOLD for review — possible match above threshold. "
                                  "Compare identifiers (aliases, country, entity type); "
                                  "release only with a documented false-positive rationale.")
    else:
        risk, action = "low", ("CLEAR on the screened dataset — no name match at or above "
                               f"threshold {threshold} and no embargoed jurisdiction. "
                               "Proceed with routine monitoring.")

    return {
        "input": {"name": name, "country": entity.get("country"),
                  "region": entity.get("region"), "type": entity.get("type")},
        "matches": matches,
        "match_count": len(matches),
        "threshold": threshold,
        "jurisdiction_check": jurisdiction,
        "risk_level": risk,
        "recommended_action": action,
        "dataset": "bundled SAMPLE subset (not the full official SDN list)",
    }


def screen_batch(entities: list[dict], threshold: float = DEFAULT_THRESHOLD) -> dict:
    """Screen a batch of entities; returns per-entity results plus a summary."""
    results = [screen_entity(e, threshold=threshold) for e in (entities or [])]
    summary = {"total": len(results), "clear": 0, "review": 0, "blocked_or_held": 0}
    for r in results:
        if r["risk_level"] == "low":
            summary["clear"] += 1
        elif r["risk_level"] == "medium":
            summary["review"] += 1
        else:
            summary["blocked_or_held"] += 1
    return {"results": results, "summary": summary, "threshold": threshold}


def generate_hold_memo(entity: dict, transaction: dict | None = None,
                       threshold: float = DEFAULT_THRESHOLD) -> str:
    """Generate a full sanctions hold memo as text for a screened entity."""
    result = screen_entity(entity, threshold=threshold)
    tx = transaction or {}
    lines: list[str] = []
    lines.append("SANCTIONS HOLD MEMO — CONFIDENTIAL / COMPLIANCE USE ONLY")
    lines.append("=" * 62)
    lines.append("")
    lines.append("## 1. Transaction under review")
    lines.append("")
    lines.append(f"- Counterparty name: {entity.get('name') or '(not supplied)'}")
    lines.append(f"- Counterparty country/region: {entity.get('country') or ''} "
                 f"{entity.get('region') or ''}".rstrip())
    lines.append(f"- Amount: {tx.get('amount', '(not supplied)')} {tx.get('currency', '')}".rstrip())
    lines.append(f"- Transaction reference: {tx.get('reference') or tx.get('id') or '(not supplied)'}")
    lines.append(f"- Value date: {tx.get('date') or '(not supplied)'}")
    lines.append(f"- Product / purpose: {tx.get('purpose') or '(not supplied)'}")
    lines.append("")
    lines.append("## 2. Screening result")
    lines.append("")
    lines.append(f"- Risk level: {result['risk_level'].upper()}")
    lines.append(f"- Recommended action: {result['recommended_action']}")
    lines.append(f"- Match threshold used: {result['threshold']}")
    lines.append(f"- Dataset: {result['dataset']}")
    lines.append("")
    lines.append("### Name matches")
    lines.append("")
    if result["matches"]:
        for m in result["matches"]:
            lines.append(f"- {m['matched_name']} — score {m['score']:.2f}, matched on "
                         f"'{m['matched_on']}' ({', '.join(m['matched_fields'])}); "
                         f"list: {m['list']}; programme: {m['program']}; "
                         f"country: {m['country']}; type: {m['type']}")
    else:
        lines.append("- No name matches at or above threshold.")
    lines.append("")
    lines.append("### Jurisdiction check")
    lines.append("")
    jc = result["jurisdiction_check"]
    lines.append(f"- Embargoed jurisdiction: {'YES' if jc['embargoed'] else 'no'}")
    lines.append(f"- Detail: {jc['reason']}")
    lines.append("")
    lines.append("## 3. Required actions")
    lines.append("")
    lines.append("1. HOLD the transaction. Do not process, release, or return the funds "
                 "while this memo is open, unless compliance documents a false-positive "
                 "determination or a licence applies.")
    lines.append("2. Escalate to the sanctions compliance officer with this memo and the "
                 "full transaction record; preserve all logs and correspondence.")
    lines.append("3. If the property is blocked (not merely rejected): an initial blocked "
                 "property report must be filed with OFAC within 10 business days of the "
                 "blocking, and blocked property must also be reported annually by "
                 "30 September. Rejected transactions must likewise be reported to OFAC "
                 "within 10 business days. (Deadlines under 31 CFR 501.603/501.604 — "
                 "confirm the current rule before filing.)")
    lines.append("4. Record the disposition (false positive / blocked / rejected / "
                 "licensed) and the approver's name in the case file.")
    lines.append("")
    lines.append("## 4. Disclaimer")
    lines.append("")
    lines.append("Generated by FengForge OFAC Screener from a SAMPLE dataset for triage "
                 "and documentation support. It is not a substitute for screening "
                 "against the current official OFAC SDN and Consolidated Lists or for "
                 "legal advice. Verify every hit against the official lists before "
                 "acting.")
    return "\n".join(lines) + "\n"
