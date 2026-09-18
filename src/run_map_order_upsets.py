"""
Kartan jarjestyksen vaikutus yllatysherkkyyteen (kayttajan valinta
vaihtoehtolistalta). Hypoteesi: Bo3:n ENSIMMAINEN kartta (usein
molempien "vahvin"/neutraalein veto, ei viela paine hävitä sarja)
saattaa olla yllatysherkempi kuin MYOHEMMAT kartat (esim. deciderissa
suosikki panostaa enemman / altavastaaja on jo hermostunut).

DIAGNOOSI, EI VIELA KORJAUS (sama tapa kuin run_lan_online_upsets.py /
run_bo1_bo3_upsets.py): lasketaan mallin ("suosikki" per sarjatason Elo-
ennuste, SAMA malli/parametrit kuin tuotannossa) yllatysprosentti
map_order-luokittain. TARKEA: AIDOSTI walk-forward - jokaisen sarjan
suosikki maaritetaan Elo-rating JUURI ENNEN sita sarjaa (ei lopullista/
tulevaisuuden ratingia), sama periaate kuin kaikkialla muualla tassa
projektissa - lookahead-vuoto olisi kehapaatelma."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection  # noqa: E402
from backtest import (  # noqa: E402
    EloModel,
    deduplicate_matches,
    load_clean_matches,
    load_best_elo_params,
    production_k_override,
)
from team_names import load_top50_names, resolve_to_canonical  # noqa: E402


def main() -> int:
    conn = get_connection()
    match_list = deduplicate_matches(load_clean_matches(conn))
    top50 = load_top50_names()

    map_rows = conn.execute(
        """SELECT match_date_utc, team1, team2, map_order, team1_score, team2_score
           FROM historical_maps
           WHERE team1_score IS NOT NULL AND team2_score IS NOT NULL"""
    ).fetchall()
    conn.close()

    # Ryhmittele kartat (date, {team1,team2}) -> lista (map_order, winner_team),
    # nimet normalisoitu, dedupattu (sama nimivariantti-suoja kuin muualla).
    dedup_seen = set()
    map_groups: dict = {}
    for date_s, t1, t2, map_order, s1, s2 in map_rows:
        t1c = resolve_to_canonical(t1, top50)
        t2c = resolve_to_canonical(t2, top50)
        if t1c not in top50 or t2c not in top50 or t1c == t2c or s1 == s2:
            continue
        map_key = (date_s, map_order, frozenset({t1c, t2c}))
        if map_key in dedup_seen:
            continue
        dedup_seen.add(map_key)
        winner = t1c if s1 > s2 else t2c
        date = datetime.fromisoformat(date_s)
        group_key = (date, frozenset({t1c, t2c}))
        map_groups.setdefault(group_key, []).append((map_order, winner))

    # AIDOSTI walk-forward: kayda match_list lapi kronologisesti, ennusta
    # ENNEN paivitysta, kirjaa yllatykset TALLA hetkella tunnetulla ratingilla.
    params = load_best_elo_params()
    elo = EloModel(scale=params["scale"], k_factor=params["k_factor"], half_life_days=params["half_life_days"])
    buckets: dict = {}  # map_order_bucket -> [n, n_upset]
    matched_series = 0

    for m in match_list:
        p_team = elo.predict(m.team, m.opponent, m.date)
        if m.team in top50 and m.opponent in top50:
            group_key = (m.date, frozenset({m.team, m.opponent}))
            maps_here = map_groups.get(group_key)
            if maps_here:
                matched_series += 1
                favorite = m.team if p_team >= 0.5 else m.opponent
                for map_order, winner in maps_here:
                    is_upset = winner != favorite
                    bucket = "1 (avaus)" if map_order == 1 else ("2" if map_order == 2 else "3+ (decider tms.)")
                    b = buckets.setdefault(bucket, [0, 0])
                    b[0] += 1
                    b[1] += int(is_upset)
        k_override = production_k_override(params["k_factor"], m.match_type, m.team, m.opponent, top50)
        elo.update(m.team, m.opponent, m.team_won, m.date, k_override=k_override)

    print(f"Loytyi karttadataa {matched_series} sarjalle (top75-vs-top75).\n")
    print(f"Yllatysprosentti (mallin suosikki havisi tuon KARTAN) map_order-luokittain:\n")
    print(f"{'map_order':18s}  {'n':>5s}  {'yllatyksia':>10s}  {'yllatys-%':>9s}")
    for bucket in sorted(buckets.keys()):
        n, n_upset = buckets[bucket]
        pct = n_upset / n * 100 if n else float("nan")
        print(f"{bucket:18s}  {n:5d}  {n_upset:10d}  {pct:8.1f}%")

    total_n = sum(v[0] for v in buckets.values())
    total_upset = sum(v[1] for v in buckets.values())
    if total_n:
        print(f"\nKaikki yhteensa: n={total_n}  yllatys-% = {total_upset/total_n*100:.1f}%")
    else:
        print("\nEi dataa.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
