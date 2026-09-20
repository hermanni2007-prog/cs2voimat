"""
Joukkuekohtainen CT/T-puolisuus per kartta (kayttajan pyynnosta 2026-09-20,
idea #3: kayttamaton data - team1_halves_json/team2_halves_json ei ole
koskaan aiemmin hyodynnetty). Laskee kierrostason (ei karttatason) voitto-%
per puoli - paljon suurempi otoskoko per solu kuin karttatason win-rate-
taulukolla (map_winrates_top75.csv), koska yksi kartta = kymmenia kierroksia.

LOYDOS (2026-09-20): kentan tasolla useimmat kartat ovat lahella 50/50
(Dust II 48.6%, Ancient 51.5%, Anubis 46.2%, Cache 49.1% CT-voitto-%) -
vain Mirage/Nuke nayttavat lievaa kentan CT-etua (~57-58%). SIITA
HUOLIMATTA yksittaiset joukkueet nayttavat 30-50 prosenttiyksikon
epasymmetrioita (esim. SINNERS 79% CT vs 28% T Nukella) - tama EI siis
selity kartan ominaisuudella vaan on AIDOSTI joukkuekohtainen pelityyli-
identiteetti.

KAYTTO: qualitatiivinen esikatselutyokalu (sama kategoria kuin map_
avoidance.py) - EI viela muodollisesti testattu sarjaennusteen piirteena.
Rehellinen syy: aidon hyodyn realisointi vaatisi tietoa KUMPI joukkue
aloittaa kummalla puolella kussakin pelatussa kartassa (puolenvalinta/
kolikonheitto) - tata emme viela kerää, samoin kuin veto-sekvenssia."""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection  # noqa: E402
from team_names import load_top50_names, resolve_to_canonical  # noqa: E402

MIN_ROUNDS = 40


def side_scores(halves_json: str):
    d = {h["side"]: h["score"] for h in json.loads(halves_json)}
    return d.get("CT", 0), d.get("T", 0)


def load_profiles():
    conn = get_connection()
    top50 = load_top50_names()
    rows = conn.execute(
        "SELECT team1, team2, map_name, team1_halves_json, team2_halves_json "
        "FROM historical_maps WHERE team1_halves_json IS NOT NULL AND team2_halves_json IS NOT NULL"
    ).fetchall()
    conn.close()

    stats = defaultdict(lambda: defaultdict(lambda: [0, 0, 0, 0]))  # team -> map -> [ct_won, ct_played, t_won, t_played]
    for t1, t2, m, h1, h2 in rows:
        t1c, t2c = resolve_to_canonical(t1, top50), resolve_to_canonical(t2, top50)
        if t1c not in top50 or t2c not in top50:
            continue
        ct1, t1_ = side_scores(h1)
        ct2, t2_ = side_scores(h2)
        s = stats[t1c][m]
        s[0] += ct1; s[1] += ct1 + t2_
        s[2] += t1_; s[3] += t1_ + ct2
        s2 = stats[t2c][m]
        s2[0] += ct2; s2[1] += ct2 + t1_
        s2[2] += t2_; s2[3] += t2_ + ct1
    return stats


def profile(team, stats):
    print(f"\n{team}:")
    for m, (ctw, ctp, tw, tp) in sorted(stats[team].items(), key=lambda kv: -(kv[1][1] + kv[1][3])):
        if ctp + tp < MIN_ROUNDS:
            continue
        ct_rate = ctw / ctp if ctp else 0
        t_rate = tw / tp if tp else 0
        diff = ct_rate - t_rate
        flag = "  <- VAHVA CT-PUOLI" if diff > 0.2 else ("  <- VAHVA T-PUOLI" if diff < -0.2 else "")
        print(f"  {m:10s} CT={ct_rate*100:5.1f}%(n={ctp:3d})  T={t_rate*100:5.1f}%(n={tp:3d})  ero={diff*100:+5.1f}pp{flag}")


def main() -> int:
    stats = load_profiles()
    teams = sys.argv[1:] if len(sys.argv) > 1 else sorted(stats.keys())
    for team in teams:
        if team not in stats:
            print(f"\n{team}: ei dataa.")
            continue
        profile(team, stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
