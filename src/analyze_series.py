"""
Konkreettinen esimerkki mallin kaytosta: laskee Bo3-sarjan voittotoden-
nakoisyyden yhdistamalla Tehtava 3:n Elo-pelivoiman (ottelutason yleistaso)
ja Tehtava 4:n karttapoikkeamat (joukkuekohtainen vahvuus/heikkous
YKSITTAISELLA kartalla), seka paivittaa ennusteen sarjatilanteen mukaan
(esim. "joukkue A johtaa 1-0, seuraava kartta B, mahdollinen decider C").

Yhdistamistapa (v1, ei viela kalibroitu markkinaa vastaan):
  p_kartta(joukkue, vastustaja, kartta) =
      p_elo(joukkue, vastustaja)                       [ottelutason yleistaso]
    + poikkeama_shrunk(joukkue, kartta)                [oma vahvuus talla kartalla]
    - poikkeama_shrunk(vastustaja, kartta)              [vastustajan vahvuus talla kartalla]
  (leikattu valille [0.03, 0.97])

Sarjan lasku (Bo3, jo 1-0 tilanteessa):
  P(voitto) = P(voita seuraava kartta)
            + P(havia seuraava kartta) * P(voita decider)

TARKEA VAROITUS: kartta-poikkeamat perustuvat usein hyvin pieneen otokseen
(n=2-5 karttaa/joukkue), jolloin shrinkage-estimaattori (k=5) vetaa
poikkeaman lahelle nollaa - tama on TARKOITUKSELLISTA (ei haluta luottaa
kohinaan), mutta tarkoittaa etta kartta-korjaus on usein pieni. Tata EI
ole viela testattu markkinaa vastaan (Tehtava 0 tauolla) - tama on
mallin OMA arvio, ei todiste vedonlyontiarvosta."""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection  # noqa: E402
from backtest import EloModel, deduplicate_matches, load_clean_matches  # noqa: E402
from run_map_deviations import K_SHRINKAGE, compute_deviations, load_map_results  # noqa: E402

SCALE, K_FACTOR, HALF_LIFE = 100.0, 32.0, 99999.0


def build_current_elo(matches: list) -> EloModel:
    elo = EloModel(scale=SCALE, k_factor=K_FACTOR, half_life_days=HALF_LIFE)
    for m in matches:
        elo.update(m.team, m.opponent, m.team_won, m.date)
    return elo


def get_deviation(deviations: list, team: str, map_name: str) -> tuple:
    for d in deviations:
        if d["team"] == team and d["map"] == map_name:
            return d["shrunk_deviation"], d["n"]
    return 0.0, 0


def map_win_prob(p_elo_base: float, dev_team: float, dev_opp: float) -> float:
    p = p_elo_base + dev_team - dev_opp
    return min(max(p, 0.03), 0.97)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--team", default="MIBR")
    parser.add_argument("--opponent", default="FURIA")
    parser.add_argument("--leader", default="MIBR", help="Kuka johtaa sarjaa 1-0")
    parser.add_argument("--next-map", default="Nuke")
    parser.add_argument("--decider-map", default="Cache")
    args = parser.parse_args()

    conn = get_connection()
    matches = deduplicate_matches(load_clean_matches(conn))
    map_events = load_map_results(conn)
    conn.close()

    elo = build_current_elo(matches)
    deviations = compute_deviations(map_events)

    r_team = elo.rating_as_of(args.team, matches[-1].date if matches else None)
    r_opp = elo.rating_as_of(args.opponent, matches[-1].date if matches else None)
    p_elo_team = 1.0 / (1.0 + math.exp(-(r_team - r_opp) / SCALE))

    print(f"=== Elo-pelivoima (ottelutason yleistaso, {len(matches)} ottelun historian jalkeen) ===")
    print(f"  {args.team:<10s} rating={r_team:7.1f}")
    print(f"  {args.opponent:<10s} rating={r_opp:7.1f}")
    print(f"  P({args.team} voittaa ottelun, ei kartta-korjausta) = {p_elo_team:.3f}\n")

    print(f"=== Sarjatilanne: {args.leader} johtaa 1-0, seuraava {args.next_map}, decider {args.decider_map} ===\n")

    leader_is_team = args.leader == args.team
    p_elo_leader = p_elo_team if leader_is_team else (1 - p_elo_team)

    for map_name in (args.next_map, args.decider_map):
        dev_team, n_team = get_deviation(deviations, args.team, map_name)
        dev_opp, n_opp = get_deviation(deviations, args.opponent, map_name)
        p_team_map = map_win_prob(p_elo_team, dev_team, dev_opp)
        print(f"--- {map_name} ---")
        print(f"  {args.team}: oma poikkeama {dev_team:+.3f} (n={n_team} karttaa)")
        print(f"  {args.opponent}: oma poikkeama {dev_opp:+.3f} (n={n_opp} karttaa)")
        print(f"  P({args.team} voittaa {map_name}) = {p_elo_team:.3f} {dev_team:+.3f} - ({dev_opp:+.3f}) = {p_team_map:.3f}\n")

    dev_team_next, n_team_next = get_deviation(deviations, args.team, args.next_map)
    dev_opp_next, n_opp_next = get_deviation(deviations, args.opponent, args.next_map)
    p_team_next = map_win_prob(p_elo_team, dev_team_next, dev_opp_next)

    dev_team_dec, n_team_dec = get_deviation(deviations, args.team, args.decider_map)
    dev_opp_dec, n_opp_dec = get_deviation(deviations, args.opponent, args.decider_map)
    p_team_dec = map_win_prob(p_elo_team, dev_team_dec, dev_opp_dec)

    if leader_is_team:
        p_leader_wins_next = p_team_next
        p_leader_wins_decider = p_team_dec
    else:
        p_leader_wins_next = 1 - p_team_next
        p_leader_wins_decider = 1 - p_team_dec

    p_leader_wins_series = p_leader_wins_next + (1 - p_leader_wins_next) * p_leader_wins_decider

    print("=== Sarjan kokonaistulos ===")
    print(f"  P({args.leader} voittaa {args.next_map} ja paattaa sarjan 2-0) = {p_leader_wins_next:.3f}")
    print(f"  P({args.leader} havioaa {args.next_map}, sarja jatkuu decideriin {args.decider_map}) = {1 - p_leader_wins_next:.3f}")
    print(f"  P({args.leader} voittaa decider-kartan jos siihen mennaan) = {p_leader_wins_decider:.3f}")
    print(f"  --> P({args.leader} voittaa koko sarjan, 1-0-tilanteesta) = {p_leader_wins_series:.3f}\n")

    print("HUOM: karttapoikkeamat perustuvat pieneen otokseen (n usein 2-5), shrinkage")
    print("vetaa niita tarkoituksella lahelle nollaa. Tama on mallin oma arvio - ei viela")
    print("testattu markkinaa vastaan (Tehtava 0 tauolla).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
