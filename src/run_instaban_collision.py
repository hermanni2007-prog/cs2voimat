"""
Insta-ban-kollisio: testataan kayttajan hypoteesia 2026-09-20 ("what edge
does a team gain if its instaban is the opponents best map") suoraan
sarjan lopputuloksen ennustamisessa.

IDEA: joukkueen X karttakohtainen "valttely-indeksi" (ks. map_avoidance.py,
= oman pelimaaran suhde kentan keskiarvoon) kertoo epasuorasti mika kartta
on todennakoisesti insta-bannattu (idx≈0) ja mika on suosikki (idx>>1).
Jos joukkue A:n insta-ban SATTUU olemaan joukkue B:n suosikkikartta, A saa
"ilmaisen" edun - A olisi bannannut sen joka tapauksessa, mutta se sattuu
viemaan B:lta parhaan aseen. Testataan tama TASMALLEEN samalla ei-
kehamaisella metodologialla kuin muut tama session'in kokeilut: piirre
lasketaan VAIN kronologisesti AIEMMASTA datasta (walk-forward), kerroin
(beta) grid-haetaan VAIN train-datalla, validointi NAKEMATTOMALLA test-
osiolla.

PIIRRE (per sarja, team1 vs team2):
  bestmap_2 = team2:n suosikkikartta (max idx, vaatii idx>1.3 ja n>=15)
  bestmap_1 = team1:n suosikkikartta (samat ehdot)
  denial_of_2 = 1 - min(team1_idx[bestmap_2], 1)   # 1.0 = team1 EI KOSKAAN pelaa team2:n suosikkia (taydellinen "ilmainen" kollisio)
  denial_of_1 = 1 - min(team2_idx[bestmap_1], 1)
  edge_team1 = denial_of_2 - denial_of_1   (~-1..1, positiivinen = etu team1:lle)
Jos jommankumman suosikkikartta ei ole tunnistettavissa (ei riittavasti
dataa), piirre jaa None ja sarja ohitetaan sovituksessa (mutta nakyy
kattavuusraportissa)."""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime
from math import exp, log
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection  # noqa: E402
from backtest import (  # noqa: E402
    EloModel,
    deduplicate_matches,
    load_clean_matches,
    load_best_elo_params,
    log_loss,
    production_k_override,
)
from team_names import load_top50_names, resolve_to_canonical  # noqa: E402

VALID_MAPS = {"Dust II", "Ancient", "Mirage", "Nuke", "Inferno", "Anubis", "Overpass", "Cache"}
MIN_TEAM_TOTAL = 15
FAVORITE_IDX_THRESHOLD = 1.3
BETA_CANDIDATES = [round(-3.0 + 0.25 * i, 2) for i in range(25)]  # -3.0 .. 3.0


def ll(pairs):
    if not pairs:
        return float("nan")
    return log_loss([y for _, y in pairs], [p for p, _ in pairs])


def logit(p):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return log(p / (1 - p))


def sigmoid(x):
    if x >= 0:
        return 1.0 / (1.0 + exp(-x))
    ez = exp(x)
    return ez / (1.0 + ez)


def load_map_series_detailed(conn, canonical_names: set) -> list:
    rows = conn.execute(
        """SELECT match_date_utc, team1, team2, map_order, map_name, team1_score, team2_score
           FROM historical_maps
           WHERE team1_score IS NOT NULL AND team2_score IS NOT NULL
           ORDER BY match_date_utc, map_order"""
    ).fetchall()
    groups: dict = {}
    for date_s, t1, t2, map_order, map_name, s1, s2 in rows:
        t1c = resolve_to_canonical(t1, canonical_names)
        t2c = resolve_to_canonical(t2, canonical_names)
        key = (date_s, frozenset({t1c, t2c}))
        g = groups.setdefault(key, {"date_s": date_s, "team1": t1c, "team2": t2c, "maps": {}})
        map_key = (map_order, t1c, t2c)
        if map_key not in g["maps"] and s1 != s2:
            g["maps"][map_key] = (map_name, s1 > s2)
    out = []
    for key, g in groups.items():
        entries = list(g["maps"].values())
        if not entries:
            continue
        team1_wins = sum(1 for _, w in entries if w)
        if team1_wins == len(entries) - team1_wins:
            continue
        winner = g["team1"] if team1_wins > len(entries) - team1_wins else g["team2"]
        out.append({
            "date": datetime.fromisoformat(g["date_s"]),
            "team1": g["team1"], "team2": g["team2"], "winner": winner,
            "map_entries": entries,
        })
    out.sort(key=lambda s: s["date"])
    return out


def main() -> int:
    conn = get_connection()
    top50 = load_top50_names()
    series_list = load_map_series_detailed(conn, top50)
    series_list = [s for s in series_list if s["team1"] in top50 and s["team2"] in top50]

    match_list = deduplicate_matches(load_clean_matches(conn))
    conn.close()
    params = load_best_elo_params()

    split_idx = int(len(series_list) * 0.8)
    split_date = series_list[split_idx]["date"]
    print(f"Karttadatasta johdettuja sarjoja (top75-vs-top75): {len(series_list)}")
    print(f"Train/test-raja: {split_date.date()}  (train={split_idx}, test={len(series_list)-split_idx})\n")

    eval_keys = {(s["date"], s["team1"], s["team2"]) for s in series_list}
    eval_keys |= {(s["date"], s["team2"], s["team1"]) for s in series_list}
    elo = EloModel(scale=params["scale"], k_factor=params["k_factor"], half_life_days=params["half_life_days"])
    base_p_lookup = {}
    for m in match_list:
        p = elo.predict(m.team, m.opponent, m.date)
        if (m.date, m.team, m.opponent) in eval_keys:
            base_p_lookup[(m.date, frozenset({m.team, m.opponent}))] = (p, m.team)
        k_override = production_k_override(params["k_factor"], m.match_type, m.team, m.opponent, top50)
        elo.update(m.team, m.opponent, m.team_won, m.date, k_override=k_override)

    team_map_n = defaultdict(lambda: defaultdict(int))
    team_total = defaultdict(int)
    map_total = defaultdict(int)
    grand_total = 0

    def team_idx(team, m):
        if team_total[team] < MIN_TEAM_TOTAL or map_total[m] == 0:
            return None
        expected_share = map_total[m] / grand_total
        actual_share = team_map_n[team][m] / team_total[team]
        return actual_share / expected_share if expected_share else None

    def favorite_map(team):
        best_m, best_idx = None, FAVORITE_IDX_THRESHOLD
        for m in VALID_MAPS:
            idx = team_idx(team, m)
            if idx is not None and idx > best_idx:
                best_m, best_idx = m, idx
        return best_m

    records = []  # (base_p_team1, feature_or_None, y, is_test)
    for s in series_list:
        key = (s["date"], frozenset({s["team1"], s["team2"]}))
        feature = None
        if key in base_p_lookup:
            p_row, row_team = base_p_lookup[key]
            base_p_team1 = p_row if row_team == s["team1"] else (1 - p_row)
            y = 1 if s["winner"] == s["team1"] else 0
            is_test = s["date"] >= split_date

            bestmap_2 = favorite_map(s["team2"])
            bestmap_1 = favorite_map(s["team1"])
            if bestmap_2 is not None and bestmap_1 is not None:
                idx_1_on_2s_fave = team_idx(s["team1"], bestmap_2)
                idx_2_on_1s_fave = team_idx(s["team2"], bestmap_1)
                if idx_1_on_2s_fave is not None and idx_2_on_1s_fave is not None:
                    denial_of_2 = 1 - min(idx_1_on_2s_fave, 1)
                    denial_of_1 = 1 - min(idx_2_on_1s_fave, 1)
                    feature = denial_of_2 - denial_of_1

            records.append((base_p_team1, feature, y, is_test))

        # Walk-forward: paivitetaan kirjanpito VASTA TAMAN sarjan jalkeen.
        for map_name, team1_won in s["map_entries"]:
            for t in (s["team1"], s["team2"]):
                team_map_n[t][map_name] += 1
                team_total[t] += 1
                map_total[map_name] += 1
                grand_total += 1

    n_test_total = sum(1 for r in records if r[3])
    n_test_covered = sum(1 for r in records if r[3] and r[1] is not None)
    print(f"Test-sarjoja: {n_test_total}, joista piirre kaytettavissa: {n_test_covered} "
          f"({n_test_covered/n_test_total*100:.1f}%)\n")

    def adjusted(p, feature, beta):
        if feature is None:
            return p
        return sigmoid(logit(p) + beta * feature)

    print("=== Grid-haku train-datalla ===")
    results = []
    for beta in BETA_CANDIDATES:
        train_pairs = [(adjusted(p, f, beta), y) for p, f, y, is_test in records if not is_test and f is not None]
        tll = ll(train_pairs)
        results.append((beta, tll))
    best_beta, best_tll = min(results, key=lambda r: r[1])
    for beta, tll in results:
        marker = "  <- paras" if beta == best_beta else ("  <- beta=0" if beta == 0.0 else "")
        print(f"  beta={beta:5.2f}  train_log_loss={tll:.4f}{marker}")
    print(f"\nParas beta (train): {best_beta}  (train_log_loss={best_tll:.4f})")

    print("\n=== Testataan NAKEMATTOMALLA test-osiolla ===")
    base_covered = [(p, y) for p, f, y, is_test in records if is_test and f is not None]
    best_covered = [(adjusted(p, f, best_beta), y) for p, f, y, is_test in records if is_test and f is not None]
    print(f"KATTAVUUSJOUKKO  nykyinen={ll(base_covered):.4f}  paras(beta={best_beta})={ll(best_covered):.4f}  (n={len(base_covered)})")

    if ll(best_covered) < ll(base_covered):
        print(f"\n-> Insta-ban-kollisiopiirre (beta={best_beta}) PARANTAA nakemattomalla datalla "
              f"({ll(base_covered):.4f} -> {ll(best_covered):.4f}). Harkitse kayttoonottoa.")
    else:
        print(f"\n-> EI paranna nakemattomalla datalla ({ll(base_covered):.4f} -> {ll(best_covered):.4f}) - EI oteta kayttoon.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
