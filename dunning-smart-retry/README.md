# Dunning Smart Retry — FengForge

An MCP server that turns a failed subscription payment into a decision:
what kind of decline is this, when (if ever) should you retry, and when do
you stop retrying and ask for a new card — or cancel and win back?

Zero dependencies. Python 3.10+ standard library only. No network calls.

## What it knows

- **33 decline codes**: 20 Stripe-style codes (`insufficient_funds`,
  `generic_decline`, `do_not_honor`, `expired_card`, `incorrect_cvc`,
  `lost_card`, `stolen_card`, `card_velocity_exceeded`, `processing_error`,
  `authentication_required`, `approve_with_id`, `issuer_not_available`,
  `reenter_transaction`, `try_again_later`,
  `withdrawal_count_limit_exceeded`, `currency_not_supported`,
  `duplicate_transaction`, `fraudulent`, `pickup_card`, …) and 13 ISO 8583
  response codes (`51, 54, 57, 58, 61, 65, 91, 96, 05, 14, 04, 41, 43`).
- Each code is classified **soft** (a retry can succeed), **hard** (the same
  instrument will never work until the customer acts), or **terminal**
  (lost / stolen / pick-up / fraud — never touch the instrument again).

## Non-negotiable rules built in

1. **Hard and terminal declines are never auto-retried.** The schedule
   builder returns an empty schedule for them, with the required alternative
   action instead.
2. **Retries are capped**: at most **4** automatic attempts within **14
   days**, and attempts already made count against the cap.
3. **Insufficient-funds retries align to payday.** For weekly, biweekly
   (anchored on a known payday), or monthly (1st & 15th) pay cycles, a retry
   that would land within 7 days before a payday is snapped to payday
   morning instead of burning an attempt the day before salary lands.
4. **Unknown codes get exactly one retry**, then review — never a loop.

## Tools

| Tool | What it returns |
|---|---|
| `triage_decline` | Category, retryability, max retries, recommended action, customer-ready message |
| `build_retry_schedule` | Actual ISO timestamps for each planned attempt, payday-snap flags, rationale, cap |
| `assess_account` | `continue` / `send_card_update_request` / `cancel-and-winback` with reasons and failure counts, from the attempt history |

## Run it

```bash
python3 fengforge_dunning/server.py --test   # self-test
python3 example_usage.py                     # worked example
python3 fengforge_dunning/server.py          # MCP over stdio
```

Claude Desktop / MCP client configuration:

```json
{
  "mcpServers": {
    "fengforge-dunning-smart-retry": {
      "command": "python3",
      "args": ["/absolute/path/to/dunning-smart-retry/fengforge_dunning/server.py"]
    }
  }
}
```

## Honest limits

- This is a policy engine, not a payment processor: it decides and
  schedules; your billing system still executes the charges.
- Pay-cycle alignment needs a pay cycle. Without one, schedules use plain
  backoff — the tool says so in its rationale rather than guessing a payday.
- Issuer behaviour varies; the classifications follow mainstream network
  guidance, but a code your processor maps differently should be overridden
  in your integration layer.

Decision support only — not financial or legal advice.

## License

MIT © FengForge. See the bundle LICENSE at the repository root.
