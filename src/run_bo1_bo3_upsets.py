"""
Kayttajan pyynto 2026-09-18 (jatkokysymys LAN/online-tutkimuksen jalkeen):
aiheuttaako Bo1 enemman upset-potentiaalia kuin Bo3(+)?

historical_matches ei tallenna sarjaformaattia suoraan, mutta se on
johdettavissa TASMALLISESTI tuloksesta: CS2:ssa (MR12) yhden kartan
voittajan kierrospisteet ovat AINA >= 13 (13-X, tai jatkoajalla 16-14,
19-17, 22-20 - havaittu jakauma: 13, 16, 19, 22, ei muita), kun taas
Bo3/Bo5-sarjan karttavoittojen maara on AINA 2 tai 3. Jakaumassa ei ole
YHTAAN riviä valilla 4-12 - eli erottelu on 100% yksiselitteinen, ei
arvaus.

sama metodologia kuin LAN/online-tutkimuksessa: walk-forward Elo
(SOS-suodatettu oletusmalli), suosikin havioprosentti Bo1- vs Bo3+-
otteluissa, myos P(suosikki)-vahvuudella kontrolloituna."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection  # noqa: E402
from backtest import (  # noqa: E402
    EloModel,
    MatchRow,
    deduplicate_matches,
    filter_top50_only,
    load_clean_matches,
    load_best_elo_params,
    log_loss,
)
from team_names import load_top50_names  # noqa: E402
from datetime import datetime  # noqa: E402


def load_matches_with_format(conn) -> list:
    """Sama kuin backtest.load_clean_matches, mutta pitaa raa'an
    score_team/score_opponent mukana formaatin johtamiseksi."""
    rows = conn.execute(
        """SELECT match_date_utc, team, opponent, tier, match_type, tournament,
                  score_team, score_opponent
           FROM historical_matches
           WHERE score_team IS NOT NULL AND score_opponent IS NOT NULL
             AND score_team != score_opponent
           ORDER BY match_date_utc ASC"""
    ).fetchall()
    out = []
    for r in rows:
        date = datetime.fromisoformat(r[0])
        team_won = 1 if r[6] > r[7] else 0
        mx = max(r[6], r[7])
        fmt = "Bo1" if mx >= 13 else ("Bo3+" if mx <= 3 else "?")
        m = MatchRow(date=date, team=r[1], opponent=r[2], tier=r[3], match_type=r[4],
                     tournament=r[5], team_won=team_won)
        m.format = fmt  # type: ignore[attr-defined]
        out.append(m)
    return out


def p_bucket(p_fav: float) -> str:
    if p_fav < 0.55:
        return "50-55%"
    if p_fav < 0.60:
        return "55-60%"
    if p_fav < 0.70:
        return "60-70%"
    if p_fav < 0.80:
        return "70-80%"
    if p_fav < 0.90:
        return "80-90%"
    return "90-100%"


def main() -> int:
    conn = get_connection()
    all_matches = deduplicate_matches(load_matches_with_format(conn))
    conn.close()
    matches = filter_top50_only(all_matches, load_top50_names())
    params = load_best_elo_params()

    fmt_counts = {}
    for m in matches:
        fmt_counts[m.format] = fmt_counts.get(m.format, 0) + 1
    print("Formaattijakauma (SOS-suodatettu data):", fmt_counts, "\n")

    elo = EloModel(scale=params["scale"], k_factor=params["k_factor"], half_life_days=params["half_life_days"])
    rows = []
    for m in matches:
        p_team = elo.predict(m.team, m.opponent, m.date)
        p_fav = max(p_team, 1 - p_team)
        fav_won = (p_team >= 0.5 and m.team_won == 1) or (p_team < 0.5 and m.team_won == 0)
        rows.append({"format": m.format, "p_fav": p_fav, "fav_won": fav_won,
                      "outcome": m.team_won, "p_model": p_team})
        elo.update(m.team, m.opponent, m.team_won, m.date)

    print("=== 1) Karkea vertailu: suosikin havioprosentti (upset rate) formaatin mukaan ===")
    for f in ("Bo1", "Bo3+"):
        grp = [r for r in rows if r["format"] == f]
        if not grp:
            continue
        n = len(grp)
        avg_p_fav = sum(r["p_fav"] for r in grp) / n
        upset_rate = 1 - sum(r["fav_won"] for r in grp) / n
        ll = log_loss([r["outcome"] for r in grp], [r["p_model"] for r in grp])
        print(f"  {f:6s}: n={n:4d}  suosikin ka. P={avg_p_fav:.3f}  "
              f"toteutunut upset-rate={upset_rate:.3f}  (odotettu={1-avg_p_fav:.3f})  log_loss={ll:.4f}")

    print("\n=== 2) Kontrolloitu vertailu: upset-rate P(suosikki)-vahvuuden mukaan bucketoituna ===")
    buckets_order = ["50-55%", "55-60%", "60-70%", "70-80%", "80-90%", "90-100%"]
    print(f"{'Bucket':>10}  {'Bo1 n':>6} {'Bo1 upset%':>11}  {'Bo3+ n':>7} {'Bo3+ upset%':>12}")
    for b in buckets_order:
        bo1 = [r for r in rows if r["format"] == "Bo1" and p_bucket(r["p_fav"]) == b]
        bo3 = [r for r in rows if r["format"] == "Bo3+" and p_bucket(r["p_fav"]) == b]
        bo1_upset = (1 - sum(r["fav_won"] for r in bo1) / len(bo1)) if bo1 else float("nan")
        bo3_upset = (1 - sum(r["fav_won"] for r in bo3) / len(bo3)) if bo3 else float("nan")
        print(f"{b:>10}  {len(bo1):>6} {bo1_upset:>10.1%}  {len(bo3):>7} {bo3_upset:>11.1%}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
