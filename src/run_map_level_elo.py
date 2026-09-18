"""
Karttatason Elo-malli (kayttajan pyynnosta 2026-09-18: "toteuta se" -
viimeinen kokeilematon idea 2026-09-17 aivoriihesta, mahdollistui vasta
nyt kun historical_maps taydentyi ensimmaista kertaa kattavasti top75-
laajennuksen yhteydessa, 1971 karttaa).

IDEA: nykyinen tuotantomalli paivittyy VAIN sarjan (ottelun) lopputuloksesta
(esim. Bo3 2-1 = YKSI Elo-paivitys). Jos paivitetaan JOKAISESTA yksittaisesta
KARTASTA erikseen (Bo3 2-1 = KOLME paivitysta oikeaan suuntaan), mallilla on
enemman, vahemman kohinaista signaalia per sarja - klassinen etu (esim.
FiveThirtyEight kayttaa vastaavaa periaatetta usealla urheilulajilla).

METODOLOGIA (ei kehapaatelmia): rakennetaan KAKSI Elo-mallia:
  (A) NYKYINEN tuotantomalli - paivitys sarjan lopputuloksesta
      (load_clean_matches, tuotannon (scale,k,half_life)).
  (B) UUSI - paivitys jokaisesta kartasta erikseen (historical_maps,
      nimet normalisoitu + dedupattu + SOS-suodatettu top75-vs-top75).
      OMA (scale,k) grid-haettu ERIKSEEN train-datalla (karttatason
      signaali on eri "yksikko" - reilu vertailu ei kayta (A):n valmiiksi
      viritettyja parametreja sellaisenaan).
Molempia ARVIOIDAAN TASMALLEEN SAMALLA nakemattomalla test-sarjajoukolla
(vain ne sarjat joille loytyy karttadataa - reilu, matchattu vertailu),
log loss SARJAN voittajasta (ei yksittaisen kartan)."""
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
    filter_top50_only,
    load_clean_matches,
    load_best_elo_params,
    log_loss,
)
from team_names import load_top50_names, resolve_to_canonical  # noqa: E402

SCALE_CANDIDATES = [100, 150, 200, 300, 400, 500]
K_CANDIDATES = [8, 16, 24, 32, 48, 64, 96]


def load_map_series(conn, canonical_names: set) -> list:
    """Ryhmittelee historical_maps-rivit sarjoiksi (pvm+joukkueet), nimet
    normalisoituna. Palauttaa listan: {date, team1, team2, maps: [(t1,t2)...],
    winner: team1|team2}, aikajarjestyksessa."""
    rows = conn.execute(
        """SELECT match_date_utc, team1, team2, team1_series_score, team2_series_score,
                  map_order, team1_score, team2_score
           FROM historical_maps
           WHERE team1_score IS NOT NULL AND team2_score IS NOT NULL
           ORDER BY match_date_utc, map_order"""
    ).fetchall()

    groups: dict = {}
    for date_s, t1, t2, ss1, ss2, map_order, s1, s2 in rows:
        t1c = resolve_to_canonical(t1, canonical_names)
        t2c = resolve_to_canonical(t2, canonical_names)
        key = (date_s, frozenset({t1c, t2c}))
        g = groups.setdefault(key, {
            "date_s": date_s, "team1": t1c, "team2": t2c,
            "series_score1": ss1, "series_score2": ss2, "maps": {},
        })
        # Dedup identtinen (date, team1, team2, map_order) - sama suoja
        # kuin backtest.deduplicate_matches, tassa map_order-tasolla.
        map_key = (map_order, t1c, t2c)
        if map_key not in g["maps"]:
            g["maps"][map_key] = (s1 > s2) if t1c == g["team1"] else (s2 > s1)

    out = []
    for key, g in groups.items():
        maps_won = list(g["maps"].values())
        if len(maps_won) < 1:
            continue
        team1_map_wins = sum(1 for w in maps_won if w)
        team2_map_wins = len(maps_won) - team1_map_wins
        if team1_map_wins == team2_map_wins:
            continue  # tasapeli/ratkaisematon - ei kaytetta voittajaa
        winner = g["team1"] if team1_map_wins > team2_map_wins else g["team2"]
        out.append({
            "date": datetime.fromisoformat(g["date_s"]),
            "team1": g["team1"], "team2": g["team2"],
            "winner": winner, "n_maps": len(maps_won),
            "map_results": maps_won,  # True = team1 voitti tama kartan
        })
    out.sort(key=lambda s: s["date"])
    return out


def ll(pairs):
    if not pairs:
        return float("nan")
    return log_loss([y for _, y in pairs], [p for p, _ in pairs])


def main() -> int:
    conn = get_connection()
    canonical_names = load_top50_names()
    series_list = load_map_series(conn, canonical_names)
    series_list = [s for s in series_list if s["team1"] in canonical_names and s["team2"] in canonical_names]

    match_list = deduplicate_matches(load_clean_matches(conn))
    conn.close()
    match_list = filter_top50_only(match_list, canonical_names)

    print(f"Karttadatasta johdettuja sarjoja (top75-vs-top75): {len(series_list)}")
    if len(series_list) < 100:
        print("HUOM: liian vahan sarjoja luotettavaan train/test-jakoon.")

    split_idx = int(len(series_list) * 0.8)
    split_date = series_list[split_idx]["date"]
    train_series = [s for s in series_list if s["date"] < split_date]
    test_series = [s for s in series_list if s["date"] >= split_date]
    print(f"Train/test-raja (sarjadatan oma): {split_date.date()}  "
          f"(train={len(train_series)}, test={len(test_series)})\n")

    # --- (A) NYKYINEN tuotantomalli: paivitys sarjan lopputuloksesta ---
    params_a = load_best_elo_params()
    eval_keys = {(s["date"], s["team1"], s["team2"]) for s in test_series}
    eval_keys |= {(s["date"], s["team2"], s["team1"]) for s in test_series}

    def build_model_a():
        elo = EloModel(scale=params_a["scale"], k_factor=params_a["k_factor"], half_life_days=params_a["half_life_days"])
        preds = {}
        for m in match_list:
            p = elo.predict(m.team, m.opponent, m.date)
            if (m.date, m.team, m.opponent) in eval_keys:
                preds[(m.date, frozenset({m.team, m.opponent}))] = (p, m.team, m.opponent)
            elo.update(m.team, m.opponent, m.team_won, m.date)
        return elo, preds

    elo_a, preds_a_raw = build_model_a()
    # preds_a_raw avaimena (date, {team1,team2}) -> (p_team_JOKA_OLI_"team"_TASSA_RIVISSA, team, opponent)
    # muunnetaan p(team1 voittaa) jotta vertailukelpoinen (B):n kanssa.
    pairs_a = []
    for s in test_series:
        key = (s["date"], frozenset({s["team1"], s["team2"]}))
        if key not in preds_a_raw:
            continue
        p_row, row_team, row_opp = preds_a_raw[key]
        p_team1 = p_row if row_team == s["team1"] else (1 - p_row)
        y = 1 if s["winner"] == s["team1"] else 0
        pairs_a.append((p_team1, y))

    print(f"(A) NYKYINEN malli: loytyi ennuste {len(pairs_a)}/{len(test_series)} test-sarjalle")
    print(f"    log_loss = {ll(pairs_a):.4f}\n")

    # --- (B) UUSI: paivitys jokaisesta kartasta ---
    def walkforward_b(scale, k, half_life):
        elo = EloModel(scale=scale, k_factor=k, half_life_days=half_life)
        train_pairs, test_pairs = [], []
        for s in series_list:
            p1 = elo.predict(s["team1"], s["team2"], s["date"])
            y = 1 if s["winner"] == s["team1"] else 0
            (test_pairs if s["date"] >= split_date else train_pairs).append((p1, y))
            for map_win_team1 in s["map_results"]:
                elo.update(s["team1"], s["team2"], 1 if map_win_team1 else 0, s["date"])
        return train_pairs, test_pairs

    print("=== (B) Grid-haku train-datalla (karttatason paivitys, oma scale/k) ===")
    grid_results = []
    for scale in SCALE_CANDIDATES:
        for k in K_CANDIDATES:
            train_pairs, test_pairs = walkforward_b(scale, k, params_a["half_life_days"])
            train_ll = ll(train_pairs)
            grid_results.append((scale, k, train_ll, test_pairs))
    grid_results.sort(key=lambda r: r[2])
    best_scale, best_k, best_train_ll, best_test_pairs = grid_results[0]
    print(f"Paras (B): scale={best_scale} k={best_k}  (train_log_loss={best_train_ll:.4f})")
    print("Top 5 grid-tulosta (train):")
    for scale, k, tll, _ in grid_results[:5]:
        print(f"  scale={scale:4d} k={k:3d}  train_log_loss={tll:.4f}")

    print(f"\n(B) UUSI malli (karttatason paivitys): test_log_loss = {ll(best_test_pairs):.4f}  (n={len(best_test_pairs)})\n")

    print("=== VERTAILU (sama test-sarjajoukko) ===")
    print(f"  (A) Nykyinen (sarjatason paivitys):  log_loss = {ll(pairs_a):.4f}  (n={len(pairs_a)})")
    print(f"  (B) Uusi (karttatason paivitys):     log_loss = {ll(best_test_pairs):.4f}  (n={len(best_test_pairs)})")

    if len(pairs_a) == len(best_test_pairs) and ll(best_test_pairs) < ll(pairs_a):
        print(f"\n-> (B) PARANTAA ennustetta nakemattomalla datalla. Kannattaa harkita kayttoonottoa.")
    elif len(pairs_a) != len(best_test_pairs):
        print(f"\nHUOM: eri n (A)={len(pairs_a)} vs (B)={len(best_test_pairs)} - vertailu EI ole taysin puhdas, tulkitse varovasti.")
    else:
        print(f"\n-> (B) EI paranna ennustetta nakemattomalla datalla - EI kannata ottaa kayttoon.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
