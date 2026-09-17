"""
"Onko tuore rosterimuutos hinnoitteluvirheen lahde?" - testi olemassa
olevalla datalla, ilman markkinakertoimia.

Ajatus: jos Elo-malli (joka ei tieda mitaan rostereista) ennustaa
SELVASTI huonommin otteluita joissa jompikumpi joukkue on vaihtanut
kokoonpanoaan viimeisen 30 paivan aikana, se on merkki etta nailla
otteluilla on enemman "uutta informaatiota" jota staattinen
Elo-luku ei viela ole ehtinyt sulattaa - ja todennakoisesti markkinakaan
ei ole talta osin yhta tarkka kuin vakiintuneissa kokoonpanoissa. Tama EI
viela todista edgea (se vaatii oikeita kertoimia, Tehtava 0), mutta
kertoo MISSA tilanteissa mallin epavarmuus on suurin - siis mihin
kannattaa kohdistaa huomio kun kerroinvertailu joskus kaynnistyy.

Kayttaa valmiita src/backtest.py-luokkia (EloModel parametreilla jotka
run_elo.py loysi parhaaksi: scale=100, k=32, ei vaimennusta) ja
RosterHistory.roster_stability():aa (deklaroitu jo Tehtava 2:ssa, ei
aiemmin kaytetty mihinkaan)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection  # noqa: E402
from backtest import (  # noqa: E402
    EloModel,
    RosterHistory,
    deduplicate_matches,
    load_clean_matches,
    log_loss,
    brier_score,
)

SCALE, K_FACTOR, HALF_LIFE = 100.0, 32.0, 99999.0
LOOKBACK_DAYS = 30
CHANGE_THRESHOLD = 1  # kuinka monta UUTTA pelaajaa lookback-ikkunassa lasketaan "muutokseksi"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--threshold", type=int, default=CHANGE_THRESHOLD,
                         help="Monta uutta pelaajaa lookback-ikkunassa = 'muutos'")
    args = parser.parse_args()
    threshold = args.threshold

    conn = get_connection()
    matches = deduplicate_matches(load_clean_matches(conn))
    roster = RosterHistory(conn)
    conn.close()

    elo = EloModel(scale=SCALE, k_factor=K_FACTOR, half_life_days=HALF_LIFE)

    bucket_stable = {"outcomes": [], "probs": []}
    bucket_recent = {"outcomes": [], "probs": []}

    for m in matches:
        p = elo.predict(m.team, m.opponent, m.date)

        team_changed = roster.roster_stability(m.team, m.date, LOOKBACK_DAYS) >= threshold
        opp_changed = roster.roster_stability(m.opponent, m.date, LOOKBACK_DAYS) >= threshold
        bucket = bucket_recent if (team_changed or opp_changed) else bucket_stable

        bucket["outcomes"].append(m.team_won)
        bucket["probs"].append(p)

        elo.update(m.team, m.opponent, m.team_won, m.date)

    print(f"Rosterimuutos-ikkuna: {LOOKBACK_DAYS} vrk, kynnys: >= {threshold} uutta pelaajaa\n")
    for name, b in (("VAKAA KOKOONPANO (ei muutosta 30 vrk sisalla)", bucket_stable),
                    ("TUORE ROSTERIMUUTOS (jommallakummalla, 30 vrk sisalla)", bucket_recent)):
        n = len(b["outcomes"])
        ll = log_loss(b["outcomes"], b["probs"]) if n else float("nan")
        br = brier_score(b["outcomes"], b["probs"]) if n else float("nan")
        print(f"{name}")
        print(f"  n={n}  log_loss={ll:.4f}  brier={br:.4f}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
