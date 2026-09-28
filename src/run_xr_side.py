"""
Puolikohtaiset xR-ratingit (SideXRModel) - 2026-09-29.

Vaihe 1 (kierrostaso, 80 %:n raja): K ja K_side valitaan train-jakson
top75-vs-top75 -kierrosten log lossilla; raportoidaan test-kierrokset vs
pelkka kentan CT/T-perustaso. Alkuperainen xR (K_side=0) HAVISI perustasolle
- puolikohtaisten ratingien pitaa ensin korjata tama.

Vaihe 2 (sarjat, rolling origin 5 jaksoa): K ja K_side valitaan train-sarjojen
kalibroidulla log lossilla; vertailu tuotanto-Eloon + yhdistelma.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from backtest import log_loss  # noqa: E402
from db import get_connection  # noqa: E402
import run_xr  # noqa: E402
from run_xr import load_series, production_elo_preds, walk  # noqa: E402
from run_xr_diagnostics import SPLITS, auc, cal  # noqa: E402
from run_xr_improve import BLEND_GRID, fit_tau, paired  # noqa: E402
from team_names import load_top50_names  # noqa: E402
from xr_model import SideXRModel, XRModel, field_ct_advantage, load_map_games, logit, sigmoid  # noqa: E402

K_GRID = [4, 6]
K_SIDE_GRID = [0, 1, 2, 4]


def factory(k_side):
    class M(SideXRModel):
        def __init__(self, k, ct_adv, tau=1.0):
            super().__init__(k, ct_adv, tau, k_side=k_side)
    return M


def round_level(games, top, split, ct, k, k_side):
    m = SideXRModel(k, ct, k_side=k_side)
    acc = {False: [0.0, 0.0, 0.0], True: [0.0, 0.0, 0.0]}
    for g in games:
        if g.team1 in top and g.team2 in top:
            a = acc[g.date >= split]
            for h in g.halves:
                p = min(max(m.p_round(h.team, h.opponent, h.side, h.map_name), 1e-9), 1 - 1e-9)
                c = ct.get(h.map_name, ct.get("_all", 0.0))
                p0 = sigmoid(c if h.side == "CT" else -c)
                a[0] += -(h.won * math.log(p) + (h.played - h.won) * math.log(1 - p))
                a[1] += -(h.won * math.log(p0) + (h.played - h.won) * math.log(1 - p0))
                a[2] += h.played
        m.update_game(g)
    return {t: (a[0] / a[2], a[1] / a[2]) for t, a in acc.items()}


def main() -> int:
    conn = get_connection()
    top = load_top50_names()
    games = load_map_games(conn, top)
    series = load_series(conn, top)
    prod = production_elo_preds(conn, top, {s["key"] for s in series})
    conn.close()
    top_games = [g for g in games if g.team1 in top and g.team2 in top and g.halves]

    print("=== Vaihe 1: kierrostaso top75-vs-top75 (80 %:n raja) ===")
    split = top_games[int(len(top_games) * 0.8)].date
    ct = field_ct_advantage([g for g in games if g.date < split])
    res = {(k, ks): round_level(games, top, split, ct, k, ks) for k in K_GRID for ks in K_SIDE_GRID}
    for (k, ks), r in sorted(res.items()):
        (trx, trb), (tex, teb) = r[False], r[True]
        print(f"  K={k} K_side={ks}:  train {trx:.5f} (perustaso {trb:.5f}, ero {trb - trx:+.5f})   "
              f"test {tex:.5f} (perustaso {teb:.5f}, ero {teb - tex:+.5f})")
    best = min(res, key=lambda c: res[c][False][0])
    tex, teb = res[best][True]
    print(f"  Valittu trainilla K={best[0]} K_side={best[1]}: test-ero perustasoon {teb - tex:+.5f} "
          f"({'VOITTAA' if tex < teb else 'havia'} perustason)")

    print("\n=== Vaihe 2: sarjat, rolling origin ===")
    print(f"{'raja':>5} {'pvm':>10} {'n':>4} | {'tuotanto':>8} {'sivu-xR':>8} {'yhdist.':>8} | "
          f"{'AUC tuot.':>9} {'AUC sivu':>8} | valinta")
    wins_x = wins_b = 0
    for frac in SPLITS:
        split = top_games[int(len(top_games) * frac)].date
        ct = field_ct_advantage([g for g in games if g.date < split])
        configs = {}
        for ks in K_SIDE_GRID:
            run_xr.XRModel = factory(ks)
            for k in K_GRID:
                r = walk(games, k, ct, split, top, tau=1.0, series_events=series)
                tr, te = paired(r["series_preds"], prod, series, split)
                t = fit_tau(tr, 0)
                configs[(k, ks)] = (log_loss([x[2] for x in tr], [cal(x[0], t) for x in tr]), t, tr, te)
        run_xr.XRModel = XRModel
        key = min(configs, key=lambda c: configs[c][0])
        _, t, tr, te = configs[key]
        y = [x[2] for x in te]
        ll_prod = log_loss(y, [cal(x[1], fit_tau(tr, 1)) for x in te])
        ll_x = log_loss(y, [cal(x[0], t) for x in te])

        def blend(x, a, b):
            return sigmoid(a * logit(x[0]) + b * logit(x[1]))

        a, b = min(((a, b) for a in BLEND_GRID for b in BLEND_GRID),
                   key=lambda ab: log_loss([x[2] for x in tr], [blend(x, *ab) for x in tr]))
        ll_b = log_loss(y, [blend(x, a, b) for x in te])
        wins_x += ll_x < ll_prod
        wins_b += ll_b < ll_prod
        print(f"{frac:>5.0%} {str(split.date()):>10} {len(te):>4} | {ll_prod:>8.4f} {ll_x:>8.4f} {ll_b:>8.4f} | "
              f"{auc([x[1] for x in te], y):>9.3f} {auc([x[0] for x in te], y):>8.3f} | "
              f"K={key[0]} K_side={key[1]} a={a} b={b}")
    n = len(SPLITS)
    print(f"\nVoittaa tuotannon: sivu-xR {wins_x}/{n}, yhdistelma {wins_b}/{n} jaksolla")
    return 0


if __name__ == "__main__":
    sys.exit(main())
