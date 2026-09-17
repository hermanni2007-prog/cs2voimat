"""
Hakee Valven VRS-kuukausisnapshotit (global standings) viimeiselta 5
kuukaudelta ja tallentaa data/vrs_snapshots.json muotoon:
{"2026-05-04": {"Spirit": 1, "MOUZ": 2, ...}, "2026-06-01": {...}, ...}

Kaytetaan backtest.py:n VrsRankings-luokassa lookahead-vapaaseen
sijoitusarviointiin (Tehtava 2:n "korkeampi VRS-sija voittaa" -vertailumalli).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "data" / "vrs_snapshots.json"

RAW_BASE = "https://raw.githubusercontent.com/ValveSoftware/counter-strike_regional_standings/main/live/2026"

# Tunnetut viikonpaivat 2026: viimeisimmat kuukausittaiset global-snapshotit
SNAPSHOT_FILES = [
    "standings_global_2026_05_04.md",
    "standings_global_2026_06_01.md",
    "standings_global_2026_07_06.md",
    "standings_global_2026_08_03.md",
    "standings_global_2026_09_07.md",
]


def parse_md_table(text: str) -> dict:
    ranks = {}
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) < 3 or not cols[0].isdigit():
            continue
        ranks[cols[2]] = int(cols[0])
    return ranks


def main() -> int:
    result = {}
    for fname in SNAPSHOT_FILES:
        m = re.search(r"(\d{4})_(\d{2})_(\d{2})", fname)
        date_key = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        r = requests.get(f"{RAW_BASE}/{fname}", timeout=20)
        if r.status_code != 200:
            print(f"SKIP {fname}: HTTP {r.status_code}")
            continue
        ranks = parse_md_table(r.text)
        result[date_key] = ranks
        print(f"OK {date_key}: {len(ranks)} joukkuetta")

    OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Tallennettu: {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
