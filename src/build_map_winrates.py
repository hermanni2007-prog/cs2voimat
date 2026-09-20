"""
Joukkuekohtainen kartta-voitto-% -taulukko (kayttajan pyynnosta 2026-09-20:
"can you collect every teams winrate on x map") - EI vaadi Liquipediaa,
puhdas aggregointi jo kerätystä historical_maps-datasta.

SOS-yhdenmukaisuus: rajataan top75-vs-top75 -otteluihin (sama periaate
kuin muualla projektissa - raakavoitto-% heikkoja vastustajia vastaan
olisi harhaanjohtava). HUOM (rehellisyys, jo aiemmin todistettu tassa
projektissa): tata EI ole validoitu ennustavana MALLIPIIRTEENA - kaksi
erillista yritysta (run_decider_strength.py, run_map_pool_shrinkage.py)
epaonnistuivat nakemattomalla testilla. Tama on PUHDAS KUVAILEVA taulukko
selailuun (data/map_winrates_top75.csv + Artifact-taulukko), ei uusi
+EV-signaali. Aja uudelleen aina kun historical_maps paivittyy merkittavasti."""
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection  # noqa: E402
from team_names import load_top50_names, resolve_to_canonical  # noqa: E402

VALID_MAPS = {"Dust II", "Ancient", "Mirage", "Nuke", "Inferno", "Anubis", "Overpass", "Cache"}


def main() -> int:
    conn = get_connection()
    top50 = load_top50_names()

    rows = conn.execute(
        "SELECT team1, team2, map_name, team1_score, team2_score "
        "FROM historical_maps WHERE team1_score IS NOT NULL AND team2_score IS NOT NULL"
    ).fetchall()
    conn.close()

    stats = defaultdict(lambda: [0, 0])  # (team, map) -> [wins, n]
    for t1, t2, map_name, s1, s2 in rows:
        if map_name not in VALID_MAPS or s1 == s2:
            continue
        t1c = resolve_to_canonical(t1, top50)
        t2c = resolve_to_canonical(t2, top50)
        if t1c not in top50 or t2c not in top50:
            continue
        winner = t1c if s1 > s2 else t2c
        loser = t2c if s1 > s2 else t1c
        stats[(winner, map_name)][0] += 1
        stats[(winner, map_name)][1] += 1
        stats[(loser, map_name)][1] += 1

    records = []
    for (team, map_name), (wins, n) in stats.items():
        records.append({
            "team": team, "map": map_name, "n": n, "wins": wins,
            "win_pct": round(wins / n * 100, 1),
        })
    records.sort(key=lambda r: (r["team"], -r["n"]))

    out_csv = ROOT / "data" / "map_winrates_top75.csv"
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["team", "map", "n", "wins", "win_pct"])
        w.writeheader()
        w.writerows(records)

    out_json = ROOT / "data" / "map_winrates_top75.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    teams_covered = len(set(r["team"] for r in records))
    total_rows = len(records)
    n_ge5 = sum(1 for r in records if r["n"] >= 5)
    n_ge10 = sum(1 for r in records if r["n"] >= 10)
    print(f"Joukkue-kartta-riveja yhteensa: {total_rows} ({teams_covered} joukkuetta)")
    print(f"...joista n>=5: {n_ge5} ({n_ge5/total_rows*100:.0f}%)")
    print(f"...joista n>=10: {n_ge10} ({n_ge10/total_rows*100:.0f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
