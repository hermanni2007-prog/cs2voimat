"""
Sarjan pituus (Bo1/Bo3/Bo5), kartat ja kierrokset malliin - rolling origin -testi
(2026-09-28, kayttajan pyynto: "nama kaksi pitaa hoitaa jotenkin").

Tuotantomalli paivittyy sarjan lopputuloksesta eika tieda formaattia: Bo1-voitto
ja 2-0 Bo3-voitto ovat sille sama asia, ja ennuste on sama Bo1:lle ja Bo3:lle.

Mallit (kaikki kalibroidaan sigmoid(t*logit p):lla, t train-osalta):
  P    tuotanto-Elo sarjoista (vertailukohta)
  PF   tuotanto-Elo, oma kalibrointi Bo1:lle ja Bo3+:lle
  M    KARTTATASON Elo: paivitys jokaisesta kartasta (Bo1 = 1 kartta,
       Bo3 2-1 = 3 karttaa sarjatuloksesta), sarjaennuste = series_win_prob(p_kartta, bo)
       -> formaatti on mallissa rakenteellisesti mukana
  MR   M + kierrosero: kartan paivityspaino (marginaali/6)^a
Karttatasolla (kartat tiedossa = veto-vaiheen jalkeen):
  D    M:n karttaennuste + joukkueen karttakohtainen poikkeama: kutistettu
       keskiarvo aiemmista residuaaleista (y - p) kyseisella kartalla,
       p = sigmoid(logit p_M + beta*(d_joukkue - d_vastustaja)), (kutistus, beta) train-osalta.
Arvio top75-vs-top75, testi-ikkuna [raja_i, raja_i+1), parametrit AINA vain ikkunaa
edeltavalla datalla.

TULOS 2026-09-28 (1403 sarjaa, 702 testissa, 871 testikarttaa):
  sarjat log loss: P 0.6857, PF 0.6861, M 0.6825, MF 0.6832, MR 0.6830
  M voitti P:n 4/5 ikkunassa, AUC 0.594 vs 0.585; bootstrap parannus +0.0032,
  95 % vali [-0.0032, +0.0096], P(parannus>0)=0.85 -> LUPAAVA, EI VIELA TODISTETTU.
  M on parempi Bo3+:ssa (0.6850 vs 0.6906) mutta heikompi Bo1:ssa (0.6718 vs
  0.6639, n=130 - pieni otos). K valitaan 64-96 (ei hakualueen reunalla).
  Kierrosero (MR) ja karttakohtaiset joukkuevahvuudet (D) EIVAT paranna.
"""
from __future__ import annotations

import math
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from backtest import EloModel, deduplicate_matches, load_best_elo_params, load_clean_matches, log_loss, production_k_override  # noqa: E402
from db import get_connection  # noqa: E402
from run_xr_diagnostics import auc  # noqa: E402
from team_names import load_top50_names  # noqa: E402
from xr_model import load_map_games, series_win_prob  # noqa: E402

SPLITS = [0.5, 0.6, 0.7, 0.8, 0.9]
TAU_GRID = [round(0.5 + 0.05 * i, 2) for i in range(21)]
MAP_K = [16, 32, 48, 64, 96, 128, 192, 256]
MOV_A = [0.0, 0.5, 1.0]
DEV_SHRINK = [5, 10, 20, 40]
DEV_BETA = [0.0, 0.5, 1.0, 1.5, 2.0, 3.0]
SCALE = 400.0
MATCH_WINDOW = timedelta(hours=6)


def cal(p, t):
    p = min(max(p, 1e-9), 1 - 1e-9)
    return 1 / (1 + math.exp(-t * math.log(p / (1 - p))))


def logit(p):
    p = min(max(p, 1e-9), 1 - 1e-9)
    return math.log(p / (1 - p))


def series_shape(m):
    """-> (best_of, team_map_wins, opp_map_wins) tai None."""
    hi = max(m.score_team, m.score_opponent)
    if hi >= 13 or hi == 1:
        return 1, int(m.team_won), 1 - int(m.team_won)
    if hi in (2, 3):
        return 2 * hi - 1, m.score_team, m.score_opponent
    return None


def attach_maps(matches, games):
    """Liittaa sarjaan sen karttarivit (nimi, kierrokset) jos ne loytyvat yksiselitteisesti."""
    by_pair = defaultdict(list)
    for g in games:
        by_pair[frozenset((g.team1, g.team2))].append(g)
    out = {}
    for i, m in enumerate(matches):
        sh = series_shape(m)
        if not sh:
            continue
        cand = [g for g in by_pair.get(frozenset((m.team, m.opponent)), []) if abs(g.date - m.date) <= MATCH_WINDOW]
        wins = sum(1 for g in cand if (g.team1 == m.team) == bool(g.team1_won))
        if cand and len(cand) == sh[1] + sh[2] and wins == sh[1]:
            out[i] = sorted(cand, key=lambda g: g.map_order)
    return out


def walk(matches, maps_of, top, k, a, collect_maps=False):
    """Karttatason Elo. Palauttaa (sarjat, kartat):
    sarjat: top-vs-top (p_sarja, bo, y, date); kartat: (p_kartta, {ks: dev_ero}, y, date)."""
    r = defaultdict(lambda: 1500.0)
    dev = {ks: defaultdict(float) for ks in DEV_SHRINK} if collect_maps else {}
    devn = defaultdict(int)
    series, maps = [], []
    for i, m in enumerate(matches):
        sh = series_shape(m)
        if not sh:
            continue
        bo, w_t, w_o = sh
        is_top = m.team in top and m.opponent in top
        p_map = 1 / (1 + math.exp(-(r[m.team] - r[m.opponent]) / SCALE))
        if is_top:
            series.append((series_win_prob(p_map, bo), bo, int(m.team_won), m.date))
        kk = production_k_override(k, m.match_type, m.team, m.opponent, top)
        glist = maps_of.get(i)
        if glist:
            seq = []
            for g in glist:
                y = int((g.team1 == m.team) == bool(g.team1_won))
                seq.append((y, abs(g.team1_rounds - g.team2_rounds), g.map_name))
        else:
            margin = abs(m.score_team - m.score_opponent) if bo == 1 else 6
            seq = [(1, margin, None)] * w_t + [(0, margin, None)] * w_o
        for y, margin, mname in seq:
            p = 1 / (1 + math.exp(-(r[m.team] - r[m.opponent]) / SCALE))
            if collect_maps and mname and is_top:
                diffs = {ks: dev[ks][(m.team, mname)] / (devn[(m.team, mname)] + ks)
                         - dev[ks][(m.opponent, mname)] / (devn[(m.opponent, mname)] + ks) for ks in DEV_SHRINK}
                maps.append((p, diffs, y, m.date))
            w = (max(margin, 2) / 6.0) ** a
            d = kk * w * (y - p)
            r[m.team] += d
            r[m.opponent] -= d
            if collect_maps and mname:
                for ks in DEV_SHRINK:
                    dev[ks][(m.team, mname)] += y - p
                    dev[ks][(m.opponent, mname)] -= y - p
                devn[(m.team, mname)] += 1
                devn[(m.opponent, mname)] += 1
    return series, maps


def prod_walk(matches, top):
    params = load_best_elo_params()
    elo = EloModel(scale=params["scale"], k_factor=params["k_factor"], half_life_days=params["half_life_days"])
    out = []
    for m in matches:
        sh = series_shape(m)
        if sh and m.team in top and m.opponent in top:
            out.append((elo.predict(m.team, m.opponent, m.date), sh[0], int(m.team_won), m.date))
        elo.update(m.team, m.opponent, m.team_won, m.date,
                   k_override=production_k_override(params["k_factor"], m.match_type, m.team, m.opponent, top))
    return out


def ll(rows):
    return log_loss([y for _, y in rows], [p for p, _ in rows])


def au(rows):
    return auc([p for p, _ in rows], [y for _, y in rows])


def fit_tau(rows):
    return min(TAU_GRID, key=lambda t: ll([(cal(p, t), y) for p, y in rows]))


def main() -> int:
    conn = get_connection()
    top = load_top50_names()
    matches = deduplicate_matches(load_clean_matches(conn))
    games = load_map_games(conn, top)
    conn.close()
    maps_of = attach_maps(matches, games)
    fmt = defaultdict(int)
    for m in matches:
        sh = series_shape(m)
        fmt[sh[0] if sh else None] += 1
    print(f"Sarjoja {len(matches)}: formaatit {dict(fmt)}; karttatiedot liitetty {len(maps_of)} sarjaan")

    prod = prod_walk(matches, top)
    walks = {}
    for k in MAP_K:
        for a in MOV_A:
            walks[(k, a)] = walk(matches, maps_of, top, k, a, collect_maps=(a == 0.0))
    n = len(prod)
    assert all(len(s) == n for s, _ in walks.values())

    dates = [x[3] for x in prod]
    bounds = [dates[int(n * f)] for f in SPLITS] + [dates[-1] + timedelta(days=1)]
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    print(f"top75-vs-top75-sarjoja {n}; rajat: " + ", ".join(str(b.date()) for b in bounds[:-1]) + "\n")

    def idx(lo, hi):
        return [i for i, d in enumerate(dates) if lo <= d < hi]

    def calibrated(recs, tr, te, by_fmt=False):
        if not by_fmt:
            t = fit_tau([(recs[i][0], recs[i][2]) for i in tr])
            return [(cal(recs[i][0], t), recs[i][2]) for i in te]
        out = {}
        for grp in (lambda b: b == 1, lambda b: b > 1):
            t = fit_tau([(recs[i][0], recs[i][2]) for i in tr if grp(recs[i][1])])
            out.update({i: (cal(recs[i][0], t), recs[i][2]) for i in te if grp(recs[i][1])})
        return [out[i] for i in te]

    def best_walk(tr, keys):
        def score(key):
            s = walks[key][0]
            rows = [(s[i][0], s[i][2]) for i in tr]
            t = fit_tau(rows)
            return ll([(cal(p, t), y) for p, y in rows])
        return min(keys, key=score)

    print(f"{'raja':>10} {'n':>4} | {'P ll':>7} {'PF ll':>7} {'M ll':>7} {'MR ll':>7} | {'P AUC':>6} {'M AUC':>6} {'MR AUC':>6} | valitut")
    pooled = defaultdict(list)
    pooled_fmt = defaultdict(list)
    map_rows = defaultdict(list)
    for s in range(len(SPLITS)):
        lo, hi = bounds[s], bounds[s + 1]
        tr, te = idx(epoch, lo), idx(lo, hi)
        res = {"P": calibrated(prod, tr, te), "PF": calibrated(prod, tr, te, by_fmt=True)}
        km = best_walk(tr, [(k, 0.0) for k in MAP_K])
        kr = best_walk(tr, list(walks))
        res["M"] = calibrated(walks[km][0], tr, te)
        res["MF"] = calibrated(walks[km][0], tr, te, by_fmt=True)
        res["MR"] = calibrated(walks[kr][0], tr, te)
        for key, rows in res.items():
            pooled[key].extend(rows)
            for i, row in zip(te, rows):
                pooled_fmt[(key, "Bo1" if prod[i][1] == 1 else "Bo3+")].append(row)
        print(f"{str(lo.date()):>10} {len(te):>4} | {ll(res['P']):>7.4f} {ll(res['PF']):>7.4f} {ll(res['M']):>7.4f} {ll(res['MR']):>7.4f} | "
              f"{au(res['P']):>6.3f} {au(res['M']):>6.3f} {au(res['MR']):>6.3f} | M K={km[0]}  MR K={kr[0]},a={kr[1]}")

        # Karttataso: D vs M (sama walk km, kartat joilla nimi)
        mp = walks[km][1]
        mtr = [x for x in mp if x[3] < lo]
        mte = [x for x in mp if lo <= x[3] < hi]
        tb = fit_tau([(p, y) for p, _, y, _ in mtr])
        base = [(cal(p, tb), y) for p, _, y, _ in mte]
        best = None
        for ks in DEV_SHRINK:
            for b in DEV_BETA:
                rows = [(1 / (1 + math.exp(-(logit(p) + b * d[ks]))), y) for p, d, y, _ in mtr]
                t = fit_tau(rows)
                v = ll([(cal(p, t), y) for p, y in rows])
                if best is None or v < best[0]:
                    best = (v, ks, b, t)
        _, ks, b, t = best
        devrows = [(cal(1 / (1 + math.exp(-(logit(p) + b * d[ks]))), t), y) for p, d, y, _ in mte]
        map_rows["M"].extend(base)
        map_rows["D"].extend(devrows)
        print(f"{'':>10} kartat n={len(mte):>4}: M ll {ll(base):.4f} AUC {au(base):.3f} | D ll {ll(devrows):.4f} AUC {au(devrows):.3f}  (kutistus {ks}, beta {b})")

    print("\nYhdistetty, sarjat:")
    for key, rows in pooled.items():
        print(f"  {key:3s} n={len(rows)}  log loss {ll(rows):.4f}  AUC {au(rows):.3f}   "
              + "  ".join(f"{f}: ll {ll(pooled_fmt[(key, f)]):.4f} (n={len(pooled_fmt[(key, f)])})" for f in ("Bo1", "Bo3+")))
    import random
    rng = random.Random(1)
    for key in ("M", "MF"):
        d = [(-math.log(max(1e-15, p if y else 1 - p))) - (-math.log(max(1e-15, q if y else 1 - q)))
             for (p, y), (q, _) in zip(pooled["P"], pooled[key])]
        boots = sorted(sum(rng.choice(d) for _ in d) / len(d) for _ in range(2000))
        better = sum(1 for b in boots if b > 0) / len(boots)
        print(f"  bootstrap {key} vs P: parannus {sum(d) / len(d):+.4f}, 95 % vali [{boots[50]:+.4f}, {boots[1949]:+.4f}], "
              f"P(parannus > 0) = {better:.2f}")
    print("Yhdistetty, kartat (kartat tiedossa):")
    for key, rows in map_rows.items():
        print(f"  {key:3s} n={len(rows)}  log loss {ll(rows):.4f}  AUC {au(rows):.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
