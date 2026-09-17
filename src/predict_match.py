"""
Nopea ottelukohtainen ennustetyokalu, kayttoon ad-hoc kysymyksiin
("mitka kertoimet pitaisi olla X vs Y") ilman etukateen tallennettua
sarjatilannetta (ks. analyze_series.py Bo3-sarjatilanteille).

Kayttaa Tehtava 3:n ottelutason Elo-mallia (historical_matches, korjattu
dedup-bugi 2026-09-17) ja EloModel.predict_with_confidence() -metodia
(lisatty 2026-09-17) epavarmuushaarukan nayttamiseen - katso backtest.py:n
kommentti: karkea Glicko-tyylinen rating deviation, EI tilastollisesti
tasmallinen luottamusvali."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection  # noqa: E402
from backtest import EloModel, deduplicate_matches, load_clean_matches  # noqa: E402

SCALE, K_FACTOR, HALF_LIFE = 200.0, 48.0, 99999.0  # run_elo.py:n paras (2026-09-17, korjattu data)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("team")
    parser.add_argument("opponent")
    args = parser.parse_args()

    conn = get_connection()
    matches = deduplicate_matches(load_clean_matches(conn))
    conn.close()

    elo = EloModel(scale=SCALE, k_factor=K_FACTOR, half_life_days=HALF_LIFE)
    for m in matches:
        elo.update(m.team, m.opponent, m.team_won, m.date)
    last_date = matches[-1].date if matches else None

    r = elo.predict_with_confidence(args.team, args.opponent, last_date)

    print(f"{args.team} vs {args.opponent}")
    print(f"  n_ottelua: {args.team}={r['n_team']}  {args.opponent}={r['n_opp']}  -> luottamus: {r['confidence']}")
    print(f"  P({args.team}) = {r['p_mid']:.3f}  (haarukka [{r['p_low']:.3f}, {r['p_high']:.3f}])")
    print(f"  Reilu kerroin {args.team}: {1/r['p_mid']:.2f}  (haarukka [{1/r['p_high']:.2f}, {1/r['p_low']:.2f}])")
    print(f"  Reilu kerroin {args.opponent}: {1/(1-r['p_mid']):.2f}")
    if r["confidence"] == "MATALA":
        print("\n  HUOM: MATALA luottamus - jommallakummalla joukkueella alle 10 kelvollista ottelua."
              " Piste-ennustetta ei pida kayttaa yhta luottavaisesti kuin HYVA-luokan ennusteita.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
