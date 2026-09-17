"""
Tehtava 4 (osa 2): karttapoikkeamien laskenta jo kerätystä historical_maps
-datasta - shrinkage-estimaattori briiffin oman kaavan mukaan.

Kysymys jota tama vastaa: onko joukkue X poikkeuksellisen hyva/huono
JOLLAIN TIETYLLA kartalla suhteessa omaan yleistasoonsa (ei suhteessa
kentta keskiarvoon, joka on rakenteellisesti aina 0.5 - jokaisella kartalla
on tasan yksi voittaja ja yksi haviaja).

m_raaka(joukkue, kartta) = voitto-% kartalla - joukkueen voitto-% kaikilla
                           kartoilla yhteensa (= "poikkeama omasta tasosta")
m_hat = (n / (n + k)) * m_raaka     (n = pelatut kartat, k = shrinkage-vakio)

Pieni n -> m_hat lahella nollaa (ei luoteta satunnaiseen otokseen).
Suuri n -> m_hat lahella raakaa poikkeamaa.

k=5 on tietoinen alkuarvaus (ei viela kalibroitu markkinadataa vasten -
Tehtava 0 on tauolla). Tata kannattaa sailoa kun oikeita kertoimia on
saatavilla vertailukohdaksi.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection  # noqa: E402

K_SHRINKAGE = 5
MIN_MAPS_TO_REPORT = 4  # nayttoon vaadittu vahimmais-otoskoko (ei vaikuta laskentaan, vain raporttiin)


def load_map_results(conn) -> list:
    """Yksi rivi historical_maps:sta -> kaksi 'tapahtumaa' (team1 ja team2
    nakokulmasta), samaan tapaan kuin historical_matches tallentaa molemmat
    puolet. win=1/0."""
    rows = conn.execute(
        "SELECT team1, team2, map_name, team1_score, team2_score FROM historical_maps"
    ).fetchall()
    events = []
    for team1, team2, map_name, s1, s2 in rows:
        if s1 == s2:
            continue
        events.append((team1, map_name, 1 if s1 > s2 else 0))
        events.append((team2, map_name, 1 if s2 > s1 else 0))
    return events


def compute_deviations(events: list) -> list:
    overall: dict = {}   # team -> [wins, n]
    per_map: dict = {}   # (team, map) -> [wins, n]

    for team, map_name, win in events:
        overall.setdefault(team, [0, 0])
        overall[team][0] += win
        overall[team][1] += 1
        key = (team, map_name)
        per_map.setdefault(key, [0, 0])
        per_map[key][0] += win
        per_map[key][1] += 1

    out = []
    for (team, map_name), (wins, n) in per_map.items():
        baseline_wins, baseline_n = overall[team]
        if baseline_n == 0:
            continue
        baseline_rate = baseline_wins / baseline_n
        raw_rate = wins / n
        raw_deviation = raw_rate - baseline_rate
        shrunk = (n / (n + K_SHRINKAGE)) * raw_deviation
        out.append(
            {
                "team": team,
                "map": map_name,
                "n": n,
                "raw_win_rate": raw_rate,
                "baseline_win_rate": baseline_rate,
                "raw_deviation": raw_deviation,
                "shrunk_deviation": shrunk,
            }
        )
    return out


def main() -> int:
    conn = get_connection()
    events = load_map_results(conn)
    conn.close()

    n_teams = len({t for t, _, _ in events})
    n_maps = len({m for _, m, _ in events})
    print(f"Tapahtumia (joukkue x kartta -esiintymaa): {len(events)}")
    print(f"Eri joukkueita: {n_teams}, eri karttoja: {n_maps}")
    print(f"Shrinkage-vakio k={K_SHRINKAGE}\n")

    deviations = compute_deviations(events)
    reportable = [d for d in deviations if d["n"] >= MIN_MAPS_TO_REPORT]
    reportable.sort(key=lambda d: d["shrunk_deviation"], reverse=True)

    print(f"=== TOP 15 POSITIIVISTA POIKKEAMAA (n >= {MIN_MAPS_TO_REPORT}) ===")
    for d in reportable[:15]:
        print(
            f"  {d['team']:<22s} {d['map']:<10s} n={d['n']:3d}  "
            f"raaka_wr={d['raw_win_rate']:.2f}  oma_taso={d['baseline_win_rate']:.2f}  "
            f"poikkeama(shrunk)={d['shrunk_deviation']:+.3f}"
        )

    print(f"\n=== TOP 15 NEGATIIVISTA POIKKEAMAA (n >= {MIN_MAPS_TO_REPORT}) ===")
    for d in reportable[-15:][::-1]:
        print(
            f"  {d['team']:<22s} {d['map']:<10s} n={d['n']:3d}  "
            f"raaka_wr={d['raw_win_rate']:.2f}  oma_taso={d['baseline_win_rate']:.2f}  "
            f"poikkeama(shrunk)={d['shrunk_deviation']:+.3f}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
