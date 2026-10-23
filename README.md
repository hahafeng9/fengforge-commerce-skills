# FengForge Commerce Skills — Agentic Commerce MCP Bundle

Three production-shaped MCP servers for e-commerce agents: chargeback
evidence, subscription dunning, and sanctions screening. Each one is an
original, zero-dependency Python implementation that does the real work —
rule engines, fuzzy matching, and document generation — rather than
returning canned answers.

| Skill | Package | Tools |
|---|---|---|
| Chargeback CE3.0 Compiler | `chargeback-ce30/` | `check_ce30_eligibility`, `match_qualifying_transactions`, `generate_evidence_packet` |
| Dunning Smart Retry | `dunning-smart-retry/` | `triage_decline`, `build_retry_schedule`, `assess_account` |
| OFAC Screener | `ofac-screener/` | `screen_entity`, `screen_batch`, `generate_hold_memo` |

All servers speak **MCP JSON-RPC 2.0 over stdio**, use only the Python 3.10+
standard library, and make **no network calls at runtime**.

## Quick start

```bash
python3 tests/run_all_tests.py        # full test suite (all three servers)
cd chargeback-ce30 && python3 fengforge_chargeback_ce30/server.py --test
cd dunning-smart-retry && python3 example_usage.py
cd ofac-screener && python3 fengforge_ofac/server.py --test
```

## Install in Claude Desktop / any MCP client

Add one block per server (adjust the absolute paths):

```json
{
  "mcpServers": {
    "fengforge-chargeback-ce30": {
      "command": "python3",
      "args": ["/path/to/commerce-skills/chargeback-ce30/fengforge_chargeback_ce30/server.py"]
    },
    "fengforge-dunning-smart-retry": {
      "command": "python3",
      "args": ["/path/to/commerce-skills/dunning-smart-retry/fengforge_dunning/server.py"]
    },
    "fengforge-ofac-screener": {
      "command": "python3",
      "args": ["/path/to/commerce-skills/ofac-screener/fengforge_ofac/server.py"]
    }
  }
}
```

Each package also ships a `skill.json` manifest and a runnable
`example_usage.py` for non-MCP integrations — the core modules are plain
Python and can be imported directly.

## Why this bundle exists — an honest comparison

Shallow "skill" repos in this niche typically ship a 40-line core that:

- checks CE3.0 by counting transactions, ignoring the age window, the
  undisputed requirement, and which core elements actually matched;
- "triages" declines with a five-row dictionary and happily retries stolen
  cards;
- "screens" sanctions with substring matching against half a dozen hardcoded
  names while claiming fuzzy/Levenshtein matching in the README.

This bundle implements the claims instead:

- **CE3.0**: per-transaction pass/fail with reasons, the real 120–365-day
  window, undisputed-only history, ≥2 qualifying transactions and ≥2
  distinct matched core elements, merchandise-match bonus, an explicit gaps
  list, and a full Markdown representment packet as output.
- **Dunning**: 33 decline codes (Stripe-style + ISO 8583), soft/hard/terminal
  classification, a hard rule that hard/terminal declines are never
  auto-retried, a stated retry cap (4 attempts / 14 days), real ISO
  timestamps, and payday alignment for weekly / biweekly / monthly
  (1st & 15th) pay cycles.
- **OFAC**: actual Levenshtein + fuzzy token alignment, legal-suffix and
  transliteration normalisation, alias matching, a configurable threshold, a
  separate embargoed-jurisdiction check (CU / IR / KP / SY + Crimea / DNR /
  LNR), batch screening, and a full hold memo — against a clearly labelled
  **sample** dataset, with a fetcher script for the full official SDN list.

## Layout

```
commerce-skills/
├── README.md            (this file)
├── LICENSE              (MIT, © FengForge)
├── chargeback-ce30/
│   ├── skill.json
│   ├── README.md
│   ├── example_usage.py
│   └── fengforge_chargeback_ce30/{__init__,core,server}.py
├── dunning-smart-retry/
│   ├── skill.json
│   ├── README.md
│   ├── example_usage.py
│   └── fengforge_dunning/{__init__,core,server}.py
├── ofac-screener/
│   ├── skill.json
│   ├── README.md
│   ├── example_usage.py
│   ├── tools/fetch_sdn.py
│   └── fengforge_ofac/{__init__,core,server}.py + data/sdn_sample.json
└── tests/run_all_tests.py
```

## Pro version

The free bundle above is complete and MIT-licensed. **Pro** adds the data
and scale pieces you need in production:

- **Full OFAC SDN dataset** (19,445 entries, generated from the official
  U.S. Treasury list) as the default screening data, instead of the
  34-entry sample
- **Batch CE3.0 compiler** — many dispute cases in, one evidence packet
  per case plus a summary index, and CSV export for acquirer spreadsheets
- **Processor decline maps** for Adyen, PayPal and Checkout.com, plus
  **recovery analytics** (recovery rate, recovered revenue, per-code
  breakdown) from your dunning attempt log

**Price: $29 per skill, or $59 for the full bundle** (launch; regular $89).
One-time payment, no subscription.

**How to buy:** email **whaifeng581@gmail.com** with which skill(s) you
want, and pay in USDT (TRON / TRC20 network) to:

```
TY1mFwQmEovq3wQaXxw7STPDjGzntq7Tnq
```

Include your transaction hash in the email. The Pro zip is delivered to
your email after the deposit confirms. Card payment is coming soon.

## Disclaimer

These tools provide **decision support, not legal, financial, or compliance
advice**. Outputs (eligibility verdicts, retry policies, screening scores,
generated documents) must be reviewed by qualified humans and checked
against current network rules, acquirer formats, and the official sanctions
lists before being acted on. The bundled sanctions dataset is a small sample
for development and testing — never a production screening source.

## License

MIT © 2026 FengForge. See `LICENSE`.
