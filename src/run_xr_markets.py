"""
xR:n kalibrointitesti karttakohtaisille kierrosmarkkinoille (2026-09-29).
Naissa markkinoissa tuotanto-Elo ei pysty ennustamaan mitaan, joten xR:n ei
tarvitse voittaa sita - riittaa etta se on parempi kuin "arvaa aina train-
jakson toteutunut osuus" ja hyvin kalibroitu.

Markkinat (team1:n nakokulmasta, jatkoajat mukana):
  handicap -3.5 / -1.5 / +1.5 / +3.5, kierrosmaara yli 20.5 / 21.5 / 22.5,
  jatkoaika (12-12).
Ennuste tehdaan ennen karttaa ilman tietoa aloituspuolesta (keskiarvo
molemmista jarjestyksista). Raaka DP olettaa kierrokset riippumattomiksi,
joten jokainen markkina kalibroidaan erikseen: sigmoid(a*logit(p)+b), a,b
train-kartoilla. K valitaan train-karttojen raa'alla kokonaislog lossilla.
Kaikki sovitus train-jaksolla (80 % top75-vs-top75 -kartoista), arvio test-jaksolla.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from backtest import log_loss  # noqa: E402
from db import get_connection  # noqa: E402
from team_names import load_top50_names  # noqa: E402
from xr_model import XRModel, field_ct_advantage, load_map_games, logit, map_score_distribution, sigmoid  # noqa: E402

K_GRID = [0.5, 1, 2, 4]
A_GRID = [round(0.1 * i, 1) for i in range(0, 21)]   # 0.0 .. 2.0
B_GRID = [round(-1.0 + 0.1 * i, 1) for i in range(21)]  # -1.0 .. 1.0

MARKETS = {
    "handicap -3.5": lambda a, b: a - b > 3.5,
    "handicap -1.5": lambda a, b: a - b > 1.5,
    "handicap +1.5": lambda a, b: a - b > -1.5,
    "handicap +3.5": lambda a, b: a - b > -3.5,
    "yli 20.5": lambda a, b: a + b > 20.5,
    "yli 21.5": lambda a, b: a + b > 21.5,
    "yli 22.5": lambda a, b: a + b > 22.5,
    "jatkoaika": lambda a, b: a + b > 24,
}


def market_probs(model, g):
    p_ct = model.p_round(g.team1, g.team2, "CT", g.map_name)
    p_t = model.p_round(g.team1, g.team2, "T", g.map_name)
    d1 = map_score_distribution(round(p_ct, 4), round(p_t, 4))
    d2 = map_score_distribution(round(p_t, 4), round(p_ct, 4))
    return {m: 0.5 * (sum(v for s, v in d1.items() if f(*s)) + sum(v for s, v in d2.items() if f(*s)))
            for m, f in MARKETS.items()}


def collect(games, top, split, ct, k):
    m = XRModel(k, ct)
    recs = []  # (probs, outcomes, is_test)
    for g in games:
        if (g.team1 in top and g.team2 in top and g.halves
                and max(g.team1_rounds, g.team2_rounds) >= 13):
            probs = market_probs(m, g)
            outs = {mk: int(f(g.team1_rounds, g.team2_rounds)) for mk, f in MARKETS.items()}
            recs.append((probs, outs, g.date >= split))
        m.update_game(g)
    return recs


def cal(p, a, b):
    return sigmoid(a * logit(p) + b)


def buckets(ps, ys, n=5):
    pairs = sorted(zip(ps, ys))
    size = max(1, len(pairs) // n)
    out = []
    for i in range(0, len(pairs), size):
        chunk = pairs[i:i + size]
        out.append((sum(p for p, _ in chunk) / len(chunk), sum(y for _, y in chunk) / len(chunk), len(chunk)))
    return out


def main() -> int:
    conn = get_connection()
    top = load_top50_names()
    games = load_map_games(conn, top)
    conn.close()
    top_games = [g for g in games if g.team1 in top and g.team2 in top and g.halves]
    split = top_games[int(len(top_games) * 0.8)].date
    ct = field_ct_advantage([g for g in games if g.date < split])

    best = None
    for k in K_GRID:
        recs = collect(games, top, split, ct, k)
        tr = [r for r in recs if not r[2]]
        total = sum(log_loss([r[1][mk] for r in tr], [r[0][mk] for r in tr]) for mk in MARKETS)
        print(f"K={k:<4} train raaka log loss (summa yli markkinoiden) {total:.4f}")
        if best is None or total < best[1]:
            best = (k, total, recs)
    k, _, recs = best
    tr = [r for r in recs if not r[2]]
    te = [r for r in recs if r[2]]
    print(f"\nValittu K={k}. Train {len(tr)} karttaa, test {len(te)} karttaa (raja {split.date()})\n")

    print(f"{'markkina':<14} {'toteutui':>8} | {'perustaso':>9} {'xR raaka':>9} {'xR kalibr.':>10} | {'parannus':>8}")
    for mk in MARKETS:
        ytr = [r[1][mk] for r in tr]
        yte = [r[1][mk] for r in te]
        base_p = min(max(sum(ytr) / len(ytr), 1e-6), 1 - 1e-6)
        ll_base = log_loss(yte, [base_p] * len(yte))
        ll_raw = log_loss(yte, [r[0][mk] for r in te])
        a, b = min(((a, b) for a in A_GRID for b in B_GRID),
                   key=lambda ab: log_loss(ytr, [cal(r[0][mk], *ab) for r in tr]))
        pc = [cal(r[0][mk], a, b) for r in te]
        ll_cal = log_loss(yte, pc)
        print(f"{mk:<14} {sum(yte) / len(yte):>8.1%} | {ll_base:>9.4f} {ll_raw:>9.4f} {ll_cal:>10.4f} | "
              f"{ll_base - ll_cal:>+8.4f}   (a={a}, b={b})")
        if mk in ("handicap -1.5", "yli 21.5", "jatkoaika"):
            print("      kalibrointi (ennuste -> toteutui, n): " +
                  "  ".join(f"{p:.2f}->{y:.2f} ({n})" for p, y, n in buckets(pc, yte)))
    print("\nparannus > 0 = kalibroitu xR parempi kuin train-jakson keskiarvo test-jaksolla.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
