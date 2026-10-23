#!/usr/bin/env python3
"""Download the official OFAC SDN list (CSV) and convert it to this skill's JSON format.

    python3 tools/fetch_sdn.py [--out PATH]

Source: https://www.treasury.gov/ofac/downloads/sdn.csv
(the Specially Designated Nationals list published by the U.S. Department
of the Treasury, Office of Foreign Assets Control).

The SDN CSV has no header row. Columns used here:
    0 ent_num, 1 SDN_Name, 2 SDN_Type, 3 Program, ... 11 Remarks

Output format matches fengforge_ofac/data/sdn_sample.json:
    {"dataset": "...", "entries": [{"name", "type", "program", "list", ...}]}

This script is a build/maintenance tool. The MCP server never touches the
network. If the download fails (offline sandbox, blocked proxy, ...), the
script exits gracefully with manual-download instructions instead of a
traceback.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

SDN_CSV_URL = "https://www.treasury.gov/ofac/downloads/sdn.csv"
DEFAULT_OUT = (Path(__file__).resolve().parent.parent
               / "fengforge_ofac" / "data" / "sdn_full.json")

INSTRUCTIONS = """\
Could not download the official SDN list automatically.

Manual steps:
  1. On a machine with internet access, open:
         https://www.treasury.gov/ofac/downloads/sdn.csv
     (or the OFAC downloads page: https://ofac.treasury.gov/sanctions-list-service)
  2. Save the file as sdn.csv next to this script.
  3. Re-run with:  python3 tools/fetch_sdn.py --csv /path/to/sdn.csv
"""


def convert(csv_text: str) -> dict:
    entries: list[dict] = []
    seen: set[str] = set()
    reader = csv.reader(io.StringIO(csv_text))
    for row in reader:
        if len(row) < 4:
            continue
        name = row[1].strip()
        if not name or name in seen:
            continue
        seen.add(name)
        entries.append({
            "name": name,
            "type": (row[2].strip().lower() or "entity"),
            "program": row[3].strip(),
            "list": "SDN",
            "country": "",
            "aliases": [],
            "ent_num": row[0].strip(),
            "remarks": row[11].strip() if len(row) > 11 else "",
        })
    return {
        "dataset": ("Full OFAC SDN list converted from the official CSV at "
                    "treasury.gov. Verify currency against OFAC before use."),
        "source": SDN_CSV_URL,
        "entries": entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="output JSON path")
    parser.add_argument("--csv", default=None,
                        help="convert a locally downloaded sdn.csv instead of downloading")
    args = parser.parse_args()

    if args.csv:
        try:
            csv_text = Path(args.csv).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"error: cannot read {args.csv}: {exc}", file=sys.stderr)
            return 1
    else:
        request = urllib.request.Request(
            SDN_CSV_URL, headers={"User-Agent": "fengforge-ofac-fetch/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 (fixed official URL)
                csv_text = response.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, OSError) as exc:
            print(f"download failed: {exc}\n", file=sys.stderr)
            print(INSTRUCTIONS, file=sys.stderr)
            return 1

    payload = convert(csv_text)
    if not payload["entries"]:
        print("error: no entries parsed — the CSV format may have changed.", file=sys.stderr)
        print(INSTRUCTIONS, file=sys.stderr)
        return 1
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(payload['entries'])} SDN entries to {out_path}")
    print("note: point the screener at this file (load_dataset / your integration layer) "
          "to screen against the full list instead of the bundled sample.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
