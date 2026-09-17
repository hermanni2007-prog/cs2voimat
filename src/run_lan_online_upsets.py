"""
Kayttajan pyynto 2026-09-18: aiheuttaako LAN- (offline) turnaus enemman
"upset-potentiaalia" kuin online-turnaus - eli havisiiko suosikki
useammin LANeilla kuin sen oma voitto-tn ennustaisi?

historical_matches.match_type-kentassa on kolme arvoa: 'Offline' (2190),
'Online' (1368), 'LAN' (4, marginaalinen - niputettu Offlinen kanssa).

METODOLOGIA: sama walk-forward Elo (SOS-suodatettu oletusmalli) kuin
muuallakin. Jokaiselle ottelulle: mallin oma suosikki + sen ennustama P,
sitten katsotaan HAVISIKO suosikki useammin LAN- vs online-otteluissa.
TARKEA KONTROLLI: LAN- ja online-otteluiden suosikkien keskimaarainen
vahvuus (P) voi olla erilainen (esim. isot LAN-tapahtumat voivat vetaa
tasaisempia kenttia) - siksi verrataan MYOS P-vahvuuden mukaan
"bucketoituna" (esim. 70-80% suosikit erikseen LAN/online), jotta
lopputulos ei ole pelkkaa "LANilla sattuu olemaan enemman tasavaisia
otteluita" -artefaktia."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection  # noqa: E402
from backtest import (  # noqa: E402
    EloModel,
    deduplicate_matches,
    filter_top50_only,
    load_clean_matches,
    load_best_elo_params,
    log_loss,
)
from team_names import load_top50_names  # noqa: E402


def group_of(match_type: str) -> str:
    if match_type in ("Offline", "LAN"):
        return "LAN/Offline"
    if match_type == "Online":
        return "Online"
    return "Tuntematon"


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
    all_matches = deduplicate_matches(load_clean_matches(conn))
    conn.close()
    matches = filter_top50_only(all_matches, load_top50_names())
    params = load_best_elo_params()

    elo = EloModel(scale=params["scale"], k_factor=params["k_factor"], half_life_days=params["half_life_days"])
    rows = []
    for m in matches:
        p_team = elo.predict(m.team, m.opponent, m.date)
        p_fav = max(p_team, 1 - p_team)
        fav_won = (p_team >= 0.5 and m.team_won == 1) or (p_team < 0.5 and m.team_won == 0)
        rows.append({
            "group": group_of(m.match_type), "p_fav": p_fav, "fav_won": fav_won,
            "outcome": m.team_won, "p_model": p_team,
        })
        elo.update(m.team, m.opponent, m.team_won, m.date)

    print(f"Otteluita yhteensa: {len(rows)}\n")

    print("=== 1) Karkea vertailu: suosikin havioprosentti (upset rate) ryhmittain ===")
    for g in ("LAN/Offline", "Online", "Tuntematon"):
        grp = [r for r in rows if r["group"] == g]
        if not grp:
            continue
        n = len(grp)
        avg_p_fav = sum(r["p_fav"] for r in grp) / n
        upset_rate = 1 - sum(r["fav_won"] for r in grp) / n
        ll = log_loss([r["outcome"] for r in grp], [r["p_model"] for r in grp])
        print(f"  {g:12s}: n={n:4d}  suosikin ka. P={avg_p_fav:.3f}  "
              f"toteutunut upset-rate={upset_rate:.3f}  (odotettu={1-avg_p_fav:.3f})  log_loss={ll:.4f}")

    print("\n=== 2) Kontrolloitu vertailu: upset-rate P(suosikki)-vahvuuden mukaan bucketoituna ===")
    buckets_order = ["50-55%", "55-60%", "60-70%", "70-80%", "80-90%", "90-100%"]
    print(f"{'Bucket':>10}  {'LAN n':>6} {'LAN upset%':>11}  {'Online n':>9} {'Online upset%':>14}")
    for b in buckets_order:
        lan = [r for r in rows if r["group"] == "LAN/Offline" and p_bucket(r["p_fav"]) == b]
        onl = [r for r in rows if r["group"] == "Online" and p_bucket(r["p_fav"]) == b]
        lan_upset = (1 - sum(r["fav_won"] for r in lan) / len(lan)) if lan else float("nan")
        onl_upset = (1 - sum(r["fav_won"] for r in onl) / len(onl)) if onl else float("nan")
        print(f"{b:>10}  {len(lan):>6} {lan_upset:>10.1%}  {len(onl):>9} {onl_upset:>13.1%}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
