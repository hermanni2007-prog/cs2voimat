"""
Vasymyskorjauksen uudelleentestaus rolling originilla (2026-09-28, kayttajan
pyynto "voitko testata sita paremmin").

Alkuperainen validointi (run_fatigue_shrink.py): yksi 80/20-jako, vanha
sarjatason Elo, testissa vain 32 "vasynytta" ottelua. Nyt: tuotannon
karttatason Elo (K=96, sarjan pituus mukana, tau-kalibroitu train-osalla),
viisi testi-ikkunaa, top75-vs-top75.

Variantit (kaikki sovelletaan kalibroidun ennusteen paalle):
  BASE  ei korjausta
  FIX   tuotannon saanto sellaisenaan: suosikilla >= 2 ottelua 24 h sisalla JA
        enemman kuin altavastaajalla -> p = 0.5 + (p-0.5)*0.6
        (HUOM: 0.6 sovitettiin aiemmin datalla joka osin paallekkain naiden
        testi-ikkunoiden kanssa -> FIX on jos jotain, liian optimistinen)
  TUNED sama saanto, kerroin s valitaan train-osalta (s > 1 = suosikkia vahvistetaan)
  SYM   logit-siirto beta*(n_joukkue - n_vastustaja), n = ottelut W tunnin sisalla,
        (W, beta) train-osalta; beta < 0 = vasymys, beta > 0 = "vireessa"

TULOS 2026-09-28 (702 testisarjaa, joista 57 "vasynyt suosikki"):
  vasynyt suosikki voitti 49.1 %, malli odotti 56.8 % -> suunta tukee vasymysta,
  mutta n=57 (ero ~1.2 keskivirhetta, ei merkitseva).
  FIX +0.0010 (P>0 = 0.89, 3/5 ikkunaa, mutta 0.6 osin samalla datalla sovitettu),
  TUNED +0.0006 (P>0 = 0.66, train-valinta heiluu s=0.8...0.0 -> epavakaa),
  SYM (yleinen vasymys/vire) -0.0012 -> ei.
  Syyskuun ikkunassa vasyneet suosikit parjasivat HYVIN (korjaus haittasi).
  Johtopaatos: korjaus pidetaan (nojaa positiiviseen suuntaan), mutta sen
  suuruus on hyvin epavarma - vetoon jonka etu syntyy vain taman korjauksen
  ansiosta ei pideta arvovetona.
"""
from __future__ import annotations

import math
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from backtest import count_recent_matches, deduplicate_matches, load_clean_matches, log_loss  # noqa: E402
from db import get_connection  # noqa: E402
from run_format_map_model import PROD_MAP_K, attach_maps, cal, fit_tau, series_shape, walk  # noqa: E402
from run_tournament_effects import build_recent_match_index  # noqa: E402
from run_xr_diagnostics import auc  # noqa: E402
from team_names import load_top50_names  # noqa: E402
from xr_model import load_map_games  # noqa: E402

SPLITS = [0.5, 0.6, 0.7, 0.8, 0.9]
S_GRID = [round(0.1 * i, 1) for i in range(16)]  # 0 .. 1.5
BETA_GRID = [round(-1.0 + 0.1 * i, 1) for i in range(21)]  # -1 .. 1
W_HOURS = [24, 48]


def logit(p):
    p = min(max(p, 1e-9), 1 - 1e-9)
    return math.log(p / (1 - p))


def sig(x):
    return 1 / (1 + math.exp(-x))


def ll(rows):
    return log_loss([y for _, y in rows], [p for p, _ in rows])


def rule(p, nt, no, s):
    fav, dog = (nt, no) if p >= 0.5 else (no, nt)
    return 0.5 + (p - 0.5) * s if fav >= 2 and fav > dog else p


def main() -> int:
    conn = get_connection()
    top = load_top50_names()
    matches = deduplicate_matches(load_clean_matches(conn))
    games = load_map_games(conn, top)
    conn.close()
    series, _ = walk(matches, attach_maps(matches, games), top, PROD_MAP_K, 0.0)
    idx = build_recent_match_index(matches)
    meta = [(m.team, m.opponent) for m in matches
            if series_shape(m) and m.team in top and m.opponent in top]
    assert len(meta) == len(series)
    recs = []  # (p_raw, y, date, {W: (n_t, n_o)})
    for (t, o), (p, _, y, d) in zip(meta, series):
        n = {w: (count_recent_matches(t, d, idx, w / 24), count_recent_matches(o, d, idx, w / 24)) for w in W_HOURS}
        recs.append((p, y, d, n))

    dates = [r[2] for r in recs]
    bounds = [dates[int(len(recs) * f)] for f in SPLITS] + [dates[-1] + timedelta(days=1)]
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    tired_all = sum(1 for p, _, _, n in recs if rule(p, *n[24], 0.0) != p)
    print(f"top75-vs-top75-sarjoja {len(recs)}, joista saannon 'vasynyt suosikki' -tapauksia {tired_all}\n")

    print(f"{'raja':>10} {'n':>4} {'vasyn.':>6} | {'BASE':>7} {'FIX':>7} {'TUNED':>7} {'SYM':>7} | vasyneet: BASE / FIX / TUNED | valitut")
    pooled = {k: [] for k in ("BASE", "FIX", "TUNED", "SYM")}
    tired_pool = {k: [] for k in ("BASE", "FIX", "TUNED")}
    for i in range(len(SPLITS)):
        lo, hi = bounds[i], bounds[i + 1]
        tr = [r for r in recs if r[2] < lo]
        te = [r for r in recs if lo <= r[2] < hi]
        t = fit_tau([(p, y) for p, y, _, _ in tr])
        trc = [(cal(p, t), y, n) for p, y, _, n in tr]
        tec = [(cal(p, t), y, n) for p, y, _, n in te]

        s_best = min(S_GRID, key=lambda s: ll([(rule(p, *n[24], s), y) for p, y, n in trc]))
        w_best, b_best = min(((w, b) for w in W_HOURS for b in BETA_GRID),
                             key=lambda wb: ll([(sig(logit(p) + wb[1] * (n[wb[0]][0] - n[wb[0]][1])), y) for p, y, n in trc]))
        out = {
            "BASE": [(p, y) for p, y, _ in tec],
            "FIX": [(rule(p, *n[24], 0.6), y) for p, y, n in tec],
            "TUNED": [(rule(p, *n[24], s_best), y) for p, y, n in tec],
            "SYM": [(sig(logit(p) + b_best * (n[w_best][0] - n[w_best][1])), y) for p, y, n in tec],
        }
        tired_idx = [j for j, (p, y, n) in enumerate(tec) if rule(p, *n[24], 0.0) != p]
        for k, v in out.items():
            pooled[k].extend(v)
            if k in tired_pool:
                tired_pool[k].extend(v[j] for j in tired_idx)
        tl = {k: ll([out[k][j] for j in tired_idx]) if tired_idx else float("nan") for k in tired_pool}
        print(f"{str(lo.date()):>10} {len(te):>4} {len(tired_idx):>6} | {ll(out['BASE']):>7.4f} {ll(out['FIX']):>7.4f} "
              f"{ll(out['TUNED']):>7.4f} {ll(out['SYM']):>7.4f} | {tl['BASE']:.4f} / {tl['FIX']:.4f} / {tl['TUNED']:.4f} | "
              f"s={s_best}  SYM W={w_best}h beta={b_best}")

    print("\nYhdistetty (kaikki testi-ikkunat):")
    for k, v in pooled.items():
        print(f"  {k:5s} n={len(v)}  log loss {ll(v):.4f}  AUC {auc([p for p, _ in v], [y for _, y in v]):.3f}")
    print("Vain 'vasynyt suosikki' -ottelut:")
    for k, v in tired_pool.items():
        wins = sum(1 for p, y in v if (p >= 0.5) == bool(y))
        print(f"  {k:5s} n={len(v)}  log loss {ll(v):.4f}")
    base = tired_pool["BASE"]
    fav_won = sum(1 for p, y in base if (p >= 0.5) == bool(y))
    exp_fav = sum(max(p, 1 - p) for p, _ in base)
    print(f"  vasynyt suosikki voitti {fav_won}/{len(base)} = {fav_won / len(base):.1%}, malli odotti {exp_fav / len(base):.1%}")

    rng = random.Random(1)
    for k in ("FIX", "TUNED", "SYM"):
        d = [(-math.log(max(1e-15, p if y else 1 - p))) - (-math.log(max(1e-15, q if y else 1 - q)))
             for (p, y), (q, _) in zip(pooled["BASE"], pooled[k])]
        boots = sorted(sum(rng.choice(d) for _ in d) / len(d) for _ in range(2000))
        print(f"  bootstrap {k} vs BASE: parannus {sum(d) / len(d):+.4f}, 95 % vali [{boots[50]:+.4f}, {boots[1949]:+.4f}], "
              f"P(parannus > 0) = {sum(1 for b in boots if b > 0) / len(boots):.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
