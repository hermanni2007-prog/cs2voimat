"""
xR:n parannusyritykset (2026-09-29, kayttajan linjaus: xR halutaan kayttoon,
sita pitaa parantaa). Arvioidaan samalla rolling origin -asetelmalla joka
kaatoi alkuperaisen xR:n (run_xr_diagnostics.py):

  A) gamma: rating-eron kutistus kierrostodennakoisyydessa (yliluottamus
     tasavakisissa top75-otteluissa). K ja gamma valitaan train-sarjojen
     kalibroidulla log lossilla.
  B) yhdistelma: p = sigmoid(a*logit(p_xR) + b*logit(p_tuotanto)), a ja b
     sovitetaan train-sarjoilla.

Hyvaksyntakriteeri: parannus tuotantoon nahden USEAMMALLA testijaksolla,
ei vain syyskuussa (jolloin tuotantomalli oli poikkeuksellisen heikko).
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
from run_xr import TAU_GRID, load_series, production_elo_preds, walk  # noqa: E402
from run_xr_diagnostics import SPLITS, auc, cal  # noqa: E402
from team_names import load_top50_names  # noqa: E402
from xr_model import XRModel, field_ct_advantage, load_map_games, logit, sigmoid  # noqa: E402

K_GRID = [3, 4, 6]
GAMMA_GRID = [0.4, 0.7, 1.0]
BLEND_GRID = [round(0.1 * i, 1) for i in range(0, 16)]  # 0.0 .. 1.5


def make_model(gamma):
    class G(XRModel):
        def __init__(self, k, ct_adv, tau=1.0):
            super().__init__(k, ct_adv, tau, gamma=gamma)
    return G


def paired(preds, prod, series, split):
    rows = []
    for s in series:
        if s["key"] not in preds or s["key"] not in prod:
            continue
        p_prod, prod_team = prod[s["key"]]
        if prod_team != s["team"]:
            p_prod = 1 - p_prod
        rows.append((preds[s["key"]], p_prod, s["y"], s["date"] >= split))
    return [r for r in rows if not r[3]], [r for r in rows if r[3]]


def fit_tau(rows, i):
    return min(TAU_GRID, key=lambda t: log_loss([r[2] for r in rows], [cal(r[i], t) for r in rows]))


def main() -> int:
    conn = get_connection()
    top = load_top50_names()
    games = load_map_games(conn, top)
    series = load_series(conn, top)
    prod = production_elo_preds(conn, top, {s["key"] for s in series})
    conn.close()
    top_games = [g for g in games if g.team1 in top and g.team2 in top and g.halves]

    print(f"{'raja':>5} {'pvm':>10} {'n':>4} | {'tuotanto':>8} {'xR':>7} {'xR+gamma':>9} {'yhdist.':>8} | "
          f"{'AUC tuot.':>9} {'AUC yhd.':>8} | valinta")
    wins_gamma = wins_blend = 0
    for frac in SPLITS:
        split = top_games[int(len(top_games) * frac)].date
        ct = field_ct_advantage([g for g in games if g.date < split])

        configs = {}
        for gamma in GAMMA_GRID:
            run_xr.XRModel = make_model(gamma)
            for k in K_GRID:
                r = walk(games, k, ct, split, top, tau=1.0, series_events=series)
                tr, te = paired(r["series_preds"], prod, series, split)
                t = fit_tau(tr, 0)
                configs[(k, gamma)] = (log_loss([x[2] for x in tr], [cal(x[0], t) for x in tr]), t, tr, te)
        run_xr.XRModel = XRModel

        base_key = min((c for c in configs if c[1] == 1.0), key=lambda c: configs[c][0])
        best_key = min(configs, key=lambda c: configs[c][0])
        _, t_base, _, te_base = configs[base_key]
        _, t_best, tr, te = configs[best_key]
        y = [x[2] for x in te]
        t_prod = fit_tau(tr, 1)
        ll_prod = log_loss(y, [cal(x[1], t_prod) for x in te])
        ll_xr = log_loss([x[2] for x in te_base], [cal(x[0], t_base) for x in te_base])
        ll_gamma = log_loss(y, [cal(x[0], t_best) for x in te])

        def blend(x, a, b):
            return sigmoid(a * logit(x[0]) + b * logit(x[1]))

        a, b = min(((a, b) for a in BLEND_GRID for b in BLEND_GRID),
                   key=lambda ab: log_loss([x[2] for x in tr], [blend(x, *ab) for x in tr]))
        pb = [blend(x, a, b) for x in te]
        ll_blend = log_loss(y, pb)
        wins_gamma += ll_gamma < ll_prod
        wins_blend += ll_blend < ll_prod
        print(f"{frac:>5.0%} {str(split.date()):>10} {len(te):>4} | {ll_prod:>8.4f} {ll_xr:>7.4f} {ll_gamma:>9.4f} "
              f"{ll_blend:>8.4f} | {auc([x[1] for x in te], y):>9.3f} {auc(pb, y):>8.3f} | "
              f"K={best_key[0]} g={best_key[1]} a={a} b={b}")

    n = len(SPLITS)
    print(f"\nVoittaa tuotannon: xR+gamma {wins_gamma}/{n} jaksolla, yhdistelma {wins_blend}/{n} jaksolla")
    return 0


if __name__ == "__main__":
    sys.exit(main())
