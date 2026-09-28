"""
Kokoonpanomuutoksia huomioiva tuotanto-Elo - rolling origin -testi (2026-09-28).

Tausta: run_roster_change_diagnostic.py nayttaa etta tuore muutos (30 pv)
heikentaa ennusteita koko 2026:n yli (log loss 0.690 vs 0.675). Aiempi
run_roster_shrink.py (yksi 80/20-jako, pienempi data) hylkasi idean, joten
nyt testataan tiukemmin: viisi rolling origin -ikkunaa, parametrit valitaan
AINA vain ikkunaa edeltavalla datalla.

Mekanismit (kukin erikseen):
  S  ennusteen kutistus kohti 0.5:ta jos jommallakummalla joukkueella
     >= 1 uusi (yha aktiivinen) jasen viim. W paivan aikana. Ei koske ratingia.
  R  ratingin regressio kohti keskiarvoa jokaisesta liittymisesta
     (r = 1500 + (r-1500)*(1-alpha)) - uusi kokoonpano = epavarmempi taso.
  K  suurempi K W paivaa liittymisen jalkeen -> rating mukautuu nopeammin.

Vertailukohta on REILU: tuotanto-Elo + globaali kalibrointi sigmoid(t*logit p),
t train-osalta. Jokaiselle mekanismille sovitetaan myos oma t. Nain parannus ei
voi tulla pelkasta yleisesta yliluottamuksen korjauksesta.
Arvio: top75-vs-top75-ottelut, testi-ikkuna [raja_i, raja_i+1).

TULOS 2026-09-28 (1403 ottelua, 5 ikkunaa, n=702 testissa): HYLATTY.
  yhdistetty log loss: perus 0.6857, S 0.6854, R 0.6860, K 0.6858
  S voitti 2/5 ikkunaa, K 3/5, R 0/5 - erot kohinan tasoa (~0.0003).
  Train-data valitsi johdonmukaisesti vahvan kutistuksen (s=0.3-0.6), mutta
  se ei yleisty seuraavaan ikkunaan -> diagnoosin ero (0.690 vs 0.675)
  selittyy muilla tekijoilla, jotka globaali kalibrointi jo kattaa.
"""
from __future__ import annotations

import math
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from backtest import MEAN_RATING, EloModel, deduplicate_matches, load_best_elo_params, load_clean_matches, log_loss, production_k_override  # noqa: E402
from db import get_connection  # noqa: E402
from run_xr_diagnostics import auc  # noqa: E402
from team_names import load_top50_names  # noqa: E402

SPLITS = [0.5, 0.6, 0.7, 0.8, 0.9]
TAU_GRID = [round(0.5 + 0.05 * i, 2) for i in range(21)]  # 0.5 .. 1.5
S_W = [14, 30, 60]
S_GRID = [round(0.1 * i, 1) for i in range(11)]  # 0 .. 1 (1 = ei kutistusta)
R_ALPHA = [0.0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4]
K_W = [14, 30]
K_MULT = [1.0, 1.5, 2.0, 3.0]


def cal(p, t):
    p = min(max(p, 1e-9), 1 - 1e-9)
    return 1 / (1 + math.exp(-t * math.log(p / (1 - p))))


def load_joins(conn):
    """team -> lajiteltu lista (join_dt, leave_str|None)."""
    out = defaultdict(list)
    for team, j, l in conn.execute("SELECT team, join_date, leave_date FROM team_rosters WHERE join_date IS NOT NULL"):
        try:
            out[team].append((datetime.fromisoformat(j).replace(tzinfo=timezone.utc), l))
        except ValueError:
            pass
    for t in out:
        out[t].sort(key=lambda x: x[0])
    return out


def recent_joins(joins, team, as_of, days):
    """Liittymiset valilla (as_of-days, as_of] joiden jasen on yha aktiivinen as_of-hetkella."""
    lst = joins.get(team, [])
    lo = as_of - timedelta(days=days)
    as_of_s = as_of.date().isoformat()
    return sum(1 for d, l in lst if lo < d <= as_of and (l is None or l > as_of_s))


class RosterElo(EloModel):
    def __init__(self, params, joins, alpha=0.0, k_w=0, k_mult=1.0):
        super().__init__(scale=params["scale"], k_factor=params["k_factor"], half_life_days=params["half_life_days"])
        self.alpha, self.k_w, self.k_mult, self.joins = alpha, k_w, k_mult, joins
        self.events = sorted((d, t) for t, lst in joins.items() for d, _ in lst)
        self.ei = 0

    def advance(self, as_of):
        while self.ei < len(self.events) and self.events[self.ei][0] <= as_of:
            d, t = self.events[self.ei]
            self.ei += 1
            if self.alpha > 0 and t in self.ratings:
                r = self._decayed_rating(t, d)
                self.ratings[t] = (MEAN_RATING + (r - MEAN_RATING) * (1 - self.alpha), d)

    def k_for(self, team, as_of, base_k):
        if self.k_mult != 1.0 and recent_joins(self.joins, team, as_of, self.k_w) > 0:
            return base_k * self.k_mult
        return base_k

    def update_match(self, m, top):
        """Kuten EloModel.update, mutta K voi olla eri joukkueille (K-mekanismi)."""
        base = production_k_override(self.k, m.match_type, m.team, m.opponent, top)
        kt, ko = self.k_for(m.team, m.date, base), self.k_for(m.opponent, m.date, base)
        r_t, r_o = self._decayed_rating(m.team, m.date), self._decayed_rating(m.opponent, m.date)
        p = 1.0 / (1.0 + math.exp(-(r_t - r_o) / self.scale))
        y = float(m.team_won)
        self.ratings[m.team] = (r_t + kt * (y - p), m.date)
        self.ratings[m.opponent] = (r_o - ko * (y - p), m.date)
        self.games_played[m.team] = self.games_played.get(m.team, 0) + 1
        self.games_played[m.opponent] = self.games_played.get(m.opponent, 0) + 1


def walk(matches, top, params, joins, **kw):
    """Palauttaa listan (p, y, date, {W: muutos?}) top75-vs-top75-otteluille."""
    elo = RosterElo(params, joins, **kw)
    recs = []
    for m in matches:
        elo.advance(m.date)
        if m.team in top and m.opponent in top:
            p = elo.predict(m.team, m.opponent, m.date)
            flags = {w: recent_joins(joins, m.team, m.date, w) + recent_joins(joins, m.opponent, m.date, w) > 0 for w in S_W}
            recs.append((p, m.team_won, m.date, flags))
        elo.update_match(m, top)
    return recs


def shrink(p, s):
    return 0.5 + (p - 0.5) * s


def fit_tau(rows):
    return min(TAU_GRID, key=lambda t: log_loss([y for _, y in rows], [cal(p, t) for p, _ in rows]))


def main() -> int:
    conn = get_connection()
    top = load_top50_names()
    joins = load_joins(conn)
    matches = deduplicate_matches(load_clean_matches(conn))
    conn.close()
    params = load_best_elo_params()
    print(f"Joukkueita joilla kokoonpanodataa: {sum(1 for t in top if t in joins)}/{len(top)}")

    base = walk(matches, top, params, joins)
    r_walks = {a: walk(matches, top, params, joins, alpha=a) for a in R_ALPHA if a > 0}
    r_walks[0.0] = base
    k_walks = {(w, km): walk(matches, top, params, joins, k_w=w, k_mult=km) for w in K_W for km in K_MULT if km != 1.0}
    for w in K_W:
        k_walks[(w, 1.0)] = base

    dates = [r[2] for r in base]
    bounds = [dates[int(len(dates) * f)] for f in SPLITS] + [dates[-1] + timedelta(days=1)]
    print(f"top75-vs-top75-otteluita {len(base)}; rajat: " + ", ".join(str(b.date()) for b in bounds[:-1]) + "\n")
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)

    def part(recs, lo, hi):
        return [r for r in recs if lo <= r[2] < hi]

    def s_rows(recs, w, s):
        return [(shrink(p, s) if f[w] else p, y) for p, y, _, f in recs]

    def ll(rows):
        return log_loss([y for _, y in rows], [p for p, _ in rows])

    def au(rows):
        return auc([p for p, _ in rows], [y for _, y in rows])

    def pick(walks, lo, hi):
        best = None
        for key, recs in walks.items():
            rows = [(p, y) for p, y, *_ in part(recs, epoch, lo)]
            t = fit_tau(rows)
            v = ll([(cal(p, t), y) for p, y in rows])
            if best is None or v < best[0]:
                best = (v, key, t)
        _, key, t = best
        return key, [(cal(p, t), y) for p, y, *_ in part(walks[key], lo, hi)]

    print(f"{'raja':>10} {'n':>4} | {'perus ll':>8} {'S ll':>7} {'R ll':>7} {'K ll':>7} | "
          f"{'perus AUC':>9} {'S AUC':>6} {'R AUC':>6} {'K AUC':>6} | valitut (train)")
    pooled = defaultdict(list)
    for i in range(len(SPLITS)):
        lo, hi = bounds[i], bounds[i + 1]
        trb, te_b = part(base, epoch, lo), part(base, lo, hi)

        tb = fit_tau([(p, y) for p, y, *_ in trb])
        out = {"perus": [(cal(p, tb), y) for p, y, *_ in te_b]}

        best = None
        for w in S_W:
            for s in S_GRID:
                rows = s_rows(trb, w, s)
                t = fit_tau(rows)
                v = ll([(cal(p, t), y) for p, y in rows])
                if best is None or v < best[0]:
                    best = (v, w, s, t)
        _, sw, ss, st = best
        out["S"] = [(cal(p, st), y) for p, y in s_rows(te_b, sw, ss)]
        ra, out["R"] = pick(r_walks, lo, hi)
        kk, out["K"] = pick(k_walks, lo, hi)

        for k, v in out.items():
            pooled[k].extend(v)
        print(f"{str(lo.date()):>10} {len(te_b):>4} | {ll(out['perus']):>8.4f} {ll(out['S']):>7.4f} {ll(out['R']):>7.4f} {ll(out['K']):>7.4f} | "
              f"{au(out['perus']):>9.3f} {au(out['S']):>6.3f} {au(out['R']):>6.3f} {au(out['K']):>6.3f} | "
              f"S(W={sw},s={ss}) R(a={ra}) K(W={kk[0]},x{kk[1]})")

    print("\nYhdistetty (kaikki testi-ikkunat):")
    for k, v in pooled.items():
        print(f"  {k:5s} n={len(v)}  log loss {ll(v):.4f}  AUC {au(v):.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
