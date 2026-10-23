# OFAC Screener — FengForge

An MCP server for sanctions / denied-party screening that actually does
fuzzy matching — not substring search dressed up as screening.

Zero dependencies. Python 3.10+ standard library only. **No network access
at runtime**: screening runs entirely against a local dataset.

## How the matching works

1. **Normalisation** — accent folding, lower-casing, punctuation removal,
   legal-suffix stripping (`Ltd`, `Inc`, `LLC`, `GmbH`, `PJSC`, `OOO`, …),
   and transliteration-variant canonicalisation (common name variants,
   `ph→f`, doubled-consonant collapse, …).
2. **Scoring** — each candidate (entry name **and every alias**) is scored by
   a blend of character-level **Levenshtein** ratio over the joined name,
   **fuzzy token alignment** (per-token best Levenshtein, so a one-letter
   typo in `Sberbnk` still aligns with `Sberbank`), and fuzzy token
   containment. The threshold is configurable (default **0.85**).
3. **Jurisdiction check — separate from name hits.** Counterparties in
   comprehensively sanctioned jurisdictions (**CU, IR, KP, SY**) or in the
   **Crimea / Donetsk (DNR) / Luhansk (LNR)** regions are flagged embargoed
   even when the name itself is completely clean.

## Tools

| Tool | What it returns |
|---|---|
| `screen_entity` | Matches with scores and matched fields/aliases, jurisdiction result, risk level (`low`/`medium`/`high`/`critical`), recommended action |
| `screen_batch` | Per-entity results plus clear / review / blocked counts |
| `generate_hold_memo` | Full hold memo as text: transaction, match detail, required actions (including the OFAC 10-business-day reporting note), disclaimer |

## The dataset — read this

The bundled `fengforge_ofac/data/sdn_sample.json` contains **34 well-known
sanctioned parties** (Rosneft, Sberbank, VTB, Wagner Group, IRGC, Huawei,
ZTE, …) and is labelled, in the file and in every result, as a **SAMPLE for
development and testing**. It is not, and must never be presented as, the
official list.

For production, build the full dataset from the official source:

```bash
python3 tools/fetch_sdn.py            # downloads SDN CSV from treasury.gov
python3 tools/fetch_sdn.py --csv /path/to/sdn.csv   # or convert a local copy
```

The script fails gracefully (instructions, no traceback) if the download is
unavailable, and the server itself never needs the network.

## Run it

```bash
python3 fengforge_ofac/server.py --test   # self-test
python3 example_usage.py                  # worked example
python3 fengforge_ofac/server.py          # MCP over stdio
```

Claude Desktop / MCP client configuration:

```json
{
  "mcpServers": {
    "fengforge-ofac-screener": {
      "command": "python3",
      "args": ["/absolute/path/to/ofac-screener/fengforge_ofac/server.py"]
    }
  }
}
```

## Honest limits

- Fuzzy name matching produces candidates for human/compliance review; a
  score is not proof of identity. Always compare additional identifiers.
- Sanctions lists change constantly. A screener is only as current as its
  dataset — automate the fetch, and re-screen on material list updates.
- This tool covers OFAC-style list screening and embargoed jurisdictions.
  It does not perform adverse-media, PEP, or beneficial-ownership checks.

Decision support only — not legal advice.

## License

MIT © FengForge. See the bundle LICENSE at the repository root.
