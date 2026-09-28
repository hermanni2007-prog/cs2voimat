"""
xR:n sarjatason edun alkupera (2026-09-29). run_xr.py nayttaa xR:lle paremman
log lossin kuin tuotanto-Elolle, mutta kierrostasolla xR ei voita pelkkaa
CT/T-perustasoa eika osumatarkkuus parane. Tama skripti erottelee:

  1) Kestaako etu useilla testijaksoilla (rolling origin 50..90 %)?
  2) AUC (jarjestyskyky) - kalibrointi/varovaisuus ei vaikuta siihen. Jos xR:n
     etu on vain kutistusta kohti 50 %:ia, AUC ei parane.
  3) Ablaatio: sama rating, mutta paivitys VAIN karttavoitoista (ei kierros-
     marginaalia). Jos yhta hyva -> kierrosdata ei tuo mitaan, etu tulee
     pelkasta nopeasti paivittyvasta ratingista.
  4) Kierrostason signaali top75-vs-top75 train- ja test-jaksolla erikseen.

Kaikki parametrit (K, tau) sovitetaan kunkin jakson train-osalla.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from backtest import log_loss  # noqa: E402
from db import get_connection  # noqa: E402
from run_xr import TAU_GRID, load_series, production_elo_preds, walk  # noqa: E402
from team_names import load_top50_names  # noqa: E402
from xr_model import SCALE, XRModel, field_ct_advantage, load_map_games, series_win_prob, sigmoid  # noqa: E402

XR_K = [2, 3, 4, 6]
MAP_K = [8, 16, 32, 48, 64]
SPLITS = [0.5, 0.6, 0.7, 0.8, 0.9]


def cal(p, t):
    p = min(max(p, 1e-9), 1 - 1e-9)
    return sigmoid(t * math.log(p / (1 - p)))


def auc(ps, ys):
    pos = [p for p, y in zip(ps, ys) if y == 1]
    neg = [p for p, y in zip(ps, ys) if y == 0]
    if not pos or not neg:
        return float("nan")
    wins = 0.0
    for a in pos:
        for b in neg:
            wins += 1.0 if a > b else 0.5 if a == b else 0.0
    return wins / (len(pos) * len(neg))


class MapOnlyModel(XRModel):
    """Ablaatio: rating paivittyy vain karttavoitosta, ennuste suoraan ratingerosta."""

    def p_map(self, team, opp, map_name, start_side=None):
        return sigmoid((self.r[team] - self.r[opp]) / SCALE)

    def p_series(self, team, opp, best_of):
        return series_win_prob(self.p_map(team, opp, None), best_of)

    def update_game(self, g):
        p = self.p_map(g.team1, g.team2, g.map_name)
        d = self.k * (g.team1_won - p)
        self.r[g.team1] += d
        self.r[g.team2] -= d
        self.n_maps[g.team1] += 1
        self.n_maps[g.team2] += 1


def run_walk(model_cls, games, k, ct_adv, split_date, top, series):
    import run_xr
    orig = run_xr.XRModel
    run_xr.XRModel = model_cls
    try:
        return walk(games, k, ct_adv, split_date, top, tau=1.0, series_events=series)
    finally:
        run_xr.XRModel = orig


def series_eval(preds, prod, series, split_date):
    rows = []
    for s in series:
        if s["key"] not in preds or s["key"] not in prod:
            continue
        p_prod, prod_team = prod[s["key"]]
        if prod_team != s["team"]:
            p_prod = 1 - p_prod
        rows.append((preds[s["key"]], p_prod, s["y"], s["date"] >= split_date))
    tr = [r for r in rows if not r[3]]
    te = [r for r in rows if r[3]]
    if not te or not tr:
        return None

    def fit(i):
        return min(TAU_GRID, key=lambda t: log_loss([r[2] for r in tr], [cal(r[i], t) for r in tr]))

    tx, tp = fit(0), fit(1)
    y = [r[2] for r in te]
    return {
        "n": len(te),
        "ll_model": log_loss(y, [cal(r[0], tx) for r in te]),
        "ll_prod": log_loss(y, [cal(r[1], tp) for r in te]),
        "auc_model": auc([r[0] for r in te], y),
        "auc_prod": auc([r[1] for r in te], y),
    }


def round_level_top(games, k, ct_adv, split_date, top):
    """Kierros-log-loss top75-vs-top75: xR vs pelkka kentan CT/T-osuus, train/test erikseen."""
    m = XRModel(k, ct_adv)
    acc = {True: [0.0, 0.0, 0.0], False: [0.0, 0.0, 0.0]}  # [ll_xr, ll_base, n]
    for g in games:
        if g.team1 in top and g.team2 in top:
            is_test = g.date >= split_date
            for h in g.halves:
                p = min(max(m.p_round(h.team, h.opponent, h.side, h.map_name), 1e-9), 1 - 1e-9)
                c = ct_adv.get(h.map_name, ct_adv.get("_all", 0.0))
                p0 = sigmoid(c if h.side == "CT" else -c)
                a = acc[is_test]
                a[0] += -(h.won * math.log(p) + (h.played - h.won) * math.log(1 - p))
                a[1] += -(h.won * math.log(p0) + (h.played - h.won) * math.log(1 - p0))
                a[2] += h.played
        m.update_game(g)
    return {("test" if t else "train"): (a[0] / a[2], a[1] / a[2]) for t, a in acc.items() if a[2]}


def main() -> int:
    conn = get_connection()
    top = load_top50_names()
    games = load_map_games(conn, top)
    series = load_series(conn, top)
    prod = production_elo_preds(conn, top, {s["key"] for s in series})
    conn.close()
    top_games = [g for g in games if g.team1 in top and g.team2 in top and g.halves]

    print("=== 1-3) Rolling origin: sarjat, kalibroitu log loss + AUC (K ja tau train-osalta) ===")
    print(f"{'raja':>5} {'pvm':>10} {'n':>4} | {'xR ll':>7} {'kartta ll':>9} {'tuot. ll':>8} | {'xR AUC':>7} {'kartta AUC':>10} {'tuot. AUC':>9}")
    for frac in SPLITS:
        split = top_games[int(len(top_games) * frac)].date
        ct = field_ct_advantage([g for g in games if g.date < split])
        best_xr = min((run_walk(XRModel, games, k, ct, split, top, series) for k in XR_K),
                      key=lambda r: r["train_round_ll"])
        best_map = min((run_walk(MapOnlyModel, games, k, ct, split, top, series) for k in MAP_K),
                       key=lambda r: log_loss([y for _, y in r["train_maps"]], [p for p, _ in r["train_maps"]]))
        a = series_eval(best_xr["series_preds"], prod, series, split)
        b = series_eval(best_map["series_preds"], prod, series, split)
        if not a or not b:
            continue
        print(f"{frac:>5.0%} {str(split.date()):>10} {a['n']:>4} | {a['ll_model']:>7.4f} {b['ll_model']:>9.4f} "
              f"{a['ll_prod']:>8.4f} | {a['auc_model']:>7.3f} {b['auc_model']:>10.3f} {a['auc_prod']:>9.3f}")

    print("\n=== 4) Kierrostaso top75-vs-top75 (K=4): xR vs pelkka CT/T-perustaso ===")
    split = top_games[int(len(top_games) * 0.8)].date
    ct = field_ct_advantage([g for g in games if g.date < split])
    for part, (lx, lb) in round_level_top(games, 4, ct, split, top).items():
        print(f"  {part:5s}: xR {lx:.5f}  perustaso {lb:.5f}  ero {lb - lx:+.5f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
