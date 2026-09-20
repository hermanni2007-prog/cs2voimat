"""
Karttojen "valttely"-indeksi (kayttajan pyynnosta 2026-09-20: "consider
insta bans and how they collide"). Verrataan joukkueen oman kartan
pelimaaraa KENTAN keskimaaraiseen osuuteen sillä kartalla - jos joukkue
pelaa karttaa paljon VAHEMMAN kuin kentta keskimaarin (indeksi << 1, tai
tarkalleen 0), se on epasuora jalki todennakoisesta insta-bannista (emme
nae itse veto-tapahtumaa, vain poissaolon pelidatasta).

LUOTETTAVUUSHUOMIO: signaali on VAHVIN kun kyseessa on UNIVERSAALI kartta
(korkea kentta-osuus, esim. Dust II 18.7%, Ancient 17.6%, Nuke 15.5%) -
silloin poissaolo ei voi selittya poolin rotaatiolla. Harvinaisemmilla
kartoilla (Cache 3.6%, Overpass 7.5%, Anubis 8.2%) poissaolo voi osittain
johtua siita etta kartta ei ollut aktiivisessa poolissa niin usein niiden
turnausten aikana - tulkitse varovaisemmin.

KAYTTO: qualitatiivinen esikatselutyokalu tietylle ottelulle (sama
kategoria kuin stand-in-tarkistus) - EI automaattinen mallipiirre. Kahden
muodollisen "karttataito EV-piirteena" -yrityksen (run_decider_strength.py,
run_map_pool_shrinkage.py) epaonnistuttua nakemattomalla testilla, tata EI
pidä liittaa Elo-putkeen ilman oikeaa veto-sekvenssidataa (ei viela
kerätty - Liquipedian yksittaisten ottelusivujen veto-osio pitaisi
tarkistaa kun paikallinen esto poistuu)."""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection  # noqa: E402
from team_names import load_top50_names, resolve_to_canonical  # noqa: E402

VALID_MAPS = {"Dust II", "Ancient", "Mirage", "Nuke", "Inferno", "Anubis", "Overpass", "Cache"}
MIN_TEAM_TOTAL = 15


def load_indexes():
    conn = get_connection()
    top50 = load_top50_names()
    rows = conn.execute(
        "SELECT team1, team2, map_name, team1_score, team2_score "
        "FROM historical_maps WHERE team1_score IS NOT NULL AND team2_score IS NOT NULL"
    ).fetchall()
    conn.close()

    team_map_n = defaultdict(lambda: defaultdict(int))
    team_map_wins = defaultdict(lambda: defaultdict(int))
    team_total = defaultdict(int)
    map_total = defaultdict(int)
    grand_total = 0

    for t1, t2, map_name, s1, s2 in rows:
        if map_name not in VALID_MAPS or s1 == s2:
            continue
        winner = resolve_to_canonical(t1 if s1 > s2 else t2, top50)
        for t in (t1, t2):
            tc = resolve_to_canonical(t, top50)
            if tc not in top50:
                continue
            team_map_n[tc][map_name] += 1
            team_total[tc] += 1
            map_total[map_name] += 1
            grand_total += 1
            if tc == winner:
                team_map_wins[tc][map_name] += 1

    map_share = {m: map_total[m] / grand_total for m in VALID_MAPS}
    return team_map_n, team_map_wins, team_total, map_share


def profile(team, team_map_n, team_map_wins, team_total, map_share):
    print(f"\n{team} (n={team_total[team]} kokonaiskarttaa):")
    for m in sorted(VALID_MAPS, key=lambda m: -map_share[m]):
        n = team_map_n[team][m]
        w = team_map_wins[team][m]
        actual = n / team_total[team] if team_total[team] else 0
        idx = actual / map_share[m] if map_share[m] else 0
        wr = f"{w}/{n}={w/n*100:.0f}%" if n else "-"
        flag = "  <- INSTA-BAN?" if n == 0 else ("  <- SUOSIKKI" if idx > 2 else "")
        print(f"  {m:10s} n={n:3d} ({actual*100:4.1f}% vs kentta {map_share[m]*100:4.1f}%, idx={idx:.2f})  voitto-%={wr}{flag}")


def main() -> int:
    team_map_n, team_map_wins, team_total, map_share = load_indexes()

    if len(sys.argv) > 1:
        teams = sys.argv[1:]
    else:
        teams = sorted(t for t, n in team_total.items() if n >= MIN_TEAM_TOTAL)

    for team in teams:
        if team not in team_total:
            print(f"\n{team}: ei dataa.")
            continue
        profile(team, team_map_n, team_map_wins, team_total, map_share)
    return 0


if __name__ == "__main__":
    sys.exit(main())
