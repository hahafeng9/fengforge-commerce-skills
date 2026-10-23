# Chargeback CE3.0 Compiler — FengForge

An MCP server that does the actual work of a Visa Compelling Evidence 3.0
(CE3.0) representment: it checks whether a cardholder's prior transaction
history really qualifies, shows exactly why each transaction passes or fails,
and compiles a ready-to-review Markdown evidence packet.

Zero dependencies. Python 3.10+ standard library only. No network calls.

## The rules it implements

A prior transaction qualifies only if **all** of these hold:

- it was **not previously disputed**;
- it is aged **120–365 days** before the dispute date;
- it shares at least one **core data element** with the disputed transaction.

The case is eligible only if, in addition:

- at least **2** prior transactions qualify, and
- at least **2 distinct** core elements are matched across the qualifying set
  (device fingerprint, IP address, account / login ID).

A merchandise/service match on a qualifying transaction is reported as a
strengthening **bonus** — never as a substitute for the core-element rule.
Every failed check produces a human-readable reason (e.g. `tx too recent:
80d (< 120d minimum)`), and the result includes an explicit **gaps** list so
an agent (or a human) knows what evidence is missing.

## Tools

| Tool | What it returns |
|---|---|
| `check_ce30_eligibility` | Verdict, per-transaction pass/fail with reasons, elements matched across the set, gaps, merchandise bonus flag |
| `match_qualifying_transactions` | Only the qualifying transactions, with match detail |
| `generate_evidence_packet` | Full Markdown representment packet as text: case summary, qualifying-transaction table, element-match table, timeline, gaps, merchant declaration |

Transaction objects are plain JSON. Useful fields: `transaction_id`, `date`,
`amount`, `currency`, `device_fingerprint`, `ip_address`, `account_id`
(or `login_id`), `disputed`, `merchandise`; the disputed transaction also
takes `dispute_date` and `reason_code`.

## Run it

```bash
python3 fengforge_chargeback_ce30/server.py --test   # self-test
python3 example_usage.py                             # worked example
python3 fengforge_chargeback_ce30/server.py          # MCP over stdio
```

Claude Desktop / MCP client configuration:

```json
{
  "mcpServers": {
    "fengforge-chargeback-ce30": {
      "command": "python3",
      "args": ["/absolute/path/to/chargeback-ce30/fengforge_chargeback_ce30/server.py"]
    }
  }
}
```

## Honest limits

- CE3.0 applies to specific Visa fraud reason codes (notably 10.4). Other
  networks and reason codes have different compelling-evidence rules; this
  tool does not pretend otherwise.
- Eligibility here means "the history meets the published CE3.0 criteria".
  Issuers still decide individual cases; acceptance is never guaranteed.
- Element values are compared exactly. If your logs store hashed device IDs
  or truncated IPs, normalise them before calling — near-matches are not
  guessed at, on purpose.

Decision support only — not legal advice.

## License

MIT © FengForge. See the bundle LICENSE at the repository root.
