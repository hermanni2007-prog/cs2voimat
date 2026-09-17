"""
Konkreettinen esimerkki mallin kaytosta: laskee Bo3-sarjan voittotoden-
nakoisyyden 1-0-johtotilanteesta.

BUGIHISTORIA (2026-09-17, loydetty kayttajan vertaillessa oikeaan Coolbet-
kertoimeen - MIBR voitti kartan 1 FURIAa vastaan; Coolbetin live-Money
Line hinnoitteli SARJAN lahes tasapeliksi, 1.80/1.90 eli ~51%/49%
marginaali poistettuna):

  v1: kaytti Tehtava 3:n OTTELUTASON EloMallia (koulutettu
      `historical_matches`-taululla) per-kartta-todennakoisyytena. VAARIN:
      `historical_matches` on SEKOITUS - 998/1223 rivista (82%) on SARJAN
      kokonaistulos ("2-0" jne.), vain 219 aidosti yksittaisen kartan
      round-tulos. -> antoi 87.7%.

  v2: rakensi PUHTAAN per-kartta-Elon Tehtava 4:n `historical_maps`-
      datasta (783 aidosti yksittaisen kartan tulosta) ja syotti sen
      teoreettiseen Bo3-kombinatoriikkaan P=q+(1-q)*q. Yha VAARIN -> 85.8%,
      tuskin muuttunut. SYY: kaava OLETTAA jaljella olevat kartat
      RIIPPUMATTOMIKSI joukkueen yleistasosta. Todellisuudessa Bo3:n
      veto-jarjestys (molemmat joukkueet kieltavat huonoimpansa ennen
      pelia) tekee jaljella olevista kartoista tasaisempia kuin raaka
      joukkuetaso antaisi olettaa - MUTTA emme voi mallintaa tata
      SUORAAN, koska veto-jarjestysdataa ei ole Liquipedian bracket-
      sivuilla (tarkistettu: haettiin Esports World Cup/2026 tuoreena,
      ei "veto"/"pick"/"ban"-jalkea rakenteessa) - se vaatisi yhden
      pyynnon PER YKSITTAINEN OTTELU (satoja/tuhansia), ei toteutettavissa
      30.5s/pyynto -rajoitteella.

  v3 (TAMA VERSIO): sen sijaan etta yritetaan mallintaa VETO-mekanismia
      teoreettisesti jota emme voi havaita, mitataan suoraan OIKEA
      TOTEUTUNUT taajuus omasta datastamme: run_map_deviations.
      compute_empirical_leader_win_rate() laskee historical_maps:n
      map_order + lopullisen sarjatuloksen avulla, kuinka usein kartan 1
      voittaja OIKEASTI voitti koko sarjan (Bo3: 193/244 = 79.1%, Bo5:
      6/11 = 54.5% - liian pieni otos luotettavaksi). Tama ei oleta
      mitaan riippumattomuudesta - se ON se toteutunut taajuus, veto-
      vaikutus jo sisaanrakennettuna koska se on OIKEASTI tapahtunut.

      79.1% on silti korkeampi kuin markkinan ~51% talle YKSITTAISELLE
      ottelulle - se ei ole ristiriita: 79.1% on KESKIARVO KAIKEN
      TASOISTEN otteluiden yli, kun taas Coolbetin hinta sisaltaa tietoa
      juuri TASTA ottelusta (esim. FURIAn koettu vahvuus juuri nyt) jota
      meidan mallimme ei nae. Tama raportoidaan avoimesti alla."""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection  # noqa: E402
from backtest import EloModel  # noqa: E402
from run_map_deviations import (  # noqa: E402
    compute_deviations,
    compute_empirical_leader_win_rate,
    load_map_results,
    load_map_results_with_dates,
)

SCALE, K_FACTOR, HALF_LIFE = 100.0, 32.0, 99999.0


def build_map_elo(map_events_with_dates: list) -> EloModel:
    elo = EloModel(scale=SCALE, k_factor=K_FACTOR, half_life_days=HALF_LIFE)
    for date, team1, team2, map_name, team1_won in map_events_with_dates:
        elo.update(team1, team2, team1_won, date)
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
    parser.add_argument("--format", type=int, default=2, choices=[2, 3],
                         help="Sarjan max-score: 2=Bo3, 3=Bo5")
    args = parser.parse_args()

    conn = get_connection()
    map_events_dated = load_map_results_with_dates(conn)
    map_events = load_map_results(conn)
    empirical_rates = compute_empirical_leader_win_rate(conn)
    conn.close()

    elo = build_map_elo(map_events_dated)
    deviations = compute_deviations(map_events)

    last_date = map_events_dated[-1][0] if map_events_dated else None
    r_team = elo.rating_as_of(args.team, last_date)
    r_opp = elo.rating_as_of(args.opponent, last_date)
    p_elo_team = 1.0 / (1.0 + math.exp(-(r_team - r_opp) / SCALE))

    print(f"=== PUHDAS per-kartta-Elo ({len(map_events_dated)} kartan historian jalkeen, historical_maps) ===")
    print(f"  {args.team:<10s} rating={r_team:7.1f}")
    print(f"  {args.opponent:<10s} rating={r_opp:7.1f}")
    print(f"  P({args.team} voittaa YHDEN kartan, pre-match) = {p_elo_team:.3f}\n")

    print("=== Karttakohtaiset poikkeamat (informatiivista - EI kayteta enaa sarjaennusteessa, ks. yla kommentti) ===")
    for map_name in (args.next_map, args.decider_map):
        dev_team, n_team = get_deviation(deviations, args.team, map_name)
        dev_opp, n_opp = get_deviation(deviations, args.opponent, map_name)
        p_team_map = map_win_prob(p_elo_team, dev_team, dev_opp)
        print(f"  {map_name}: {args.team} poikkeama {dev_team:+.3f} (n={n_team}), "
              f"{args.opponent} poikkeama {dev_opp:+.3f} (n={n_opp}) "
              f"-> raaka P({args.team}) = {p_team_map:.3f} (EPALUOTETTAVA, ks. alla)")

    print(f"\n=== Sarjatilanne: {args.leader} johtaa 1-0 (formaatti: Bo{2*args.format-1}) ===\n")

    rate_info = empirical_rates.get(args.format)
    if rate_info is None or rate_info["n"] < 20:
        print(f"EI RIITTAVASTI DATAA (n={rate_info['n'] if rate_info else 0}) talle formaatille - "
              f"ei anneta lukua.")
        return 0

    p_leader_wins_series = rate_info["win_rate"]
    n = rate_info["n"]

    print(f"EMPIIRINEN P(kartta 1:n voittaja voittaa sarjan), Bo{2*args.format-1}, n={n} oikeaa ottelua:")
    print(f"  --> P({args.leader} voittaa koko sarjan, 1-0-tilanteesta) = {p_leader_wins_series:.3f}\n")

    leader_is_team = args.leader == args.team
    p_elo_leader = p_elo_team if leader_is_team else (1 - p_elo_team)
    print(f"Vertailu: {args.leader}:n pre-match Elo-todennakoisyys oli {p_elo_leader:.3f} "
          f"(vain hieman suosikki/altavastaaja) - siis kartan 1 voitto ei ollut suuri yllatys,")
    print("eika taman pitaisi antaa erityisen suurta lisaboostia empiirisen keskiarvon paalle.")
    print(f"\nHUOM: {p_leader_wins_series*100:.1f}% on KESKIARVO kaiken tasoisten otteluiden yli - ei tieda mitaan")
    print("TASTA nimenomaisesta ottelusta (esim. miten FURIA nayttaa juuri nyt). Jos oikea markkina")
    print("hinnoittelee taman paljon lahemmas tasapelia, se sisaltaa tietoa jota mallillamme ei ole -")
    print("EI todiste virheesta markkinassa, pikemminkin merkki siita etta oma tietomme on suppeampi.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
