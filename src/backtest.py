"""
Tehtava 2: Backtest-harness.

Ottaa sisaan minka tahansa ennustefunktion ja palauttaa log lossin, Brierin,
kalibrointikayran (10 koria) - seka mallille etta markkinan
marginaalipoistetulle todennakoisyydelle. Walk-forward: jokainen ottelu
ennustetaan kayttaen vain sita ennen tapahtunutta dataa.

HYVAKSYMISKRITEERI (briiffin oma): aja harnessi kahdella tyhmalla mallilla
- (a) aina 50/50, (b) korkeampi VRS-sija voittaa - ja tarkista etta
markkinan log loss on SELVASTI molempia parempi. Jos ei ole, harnessi
on rikki (ei markkina).

HUOM tunnetuista yksinkertaistuksista (v1, 2026-09-17):
  - historical_matches sisaltaa saman ottelun molemmilta joukkueilta
    (peilikuvarivit). Symmetrisille ennustefunktioille (mm. molemmat
    tassa tiedostossa) tama ei vaaronna log loss / Brier -keskiarvoja,
    mutta tuplaa naytekoon - ei viela korjattu.
  - Rivit joilta puuttuu validi numeerinen tulos (esim. walkoverit)
    jatetaan pois - noin 14 % datasta 2026-09-17.
  - VRS-sijoitus haetaan kuukausittaisesta snapshotista, ei paivatasosta -
    "ajankohtana T" tarkoittaa viimeisinta snapshotia joka on <= T.
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection  # noqa: E402

EPS = 1e-15  # log lossin nollasuojaus


# ---------------------------------------------------------------------------
# Metriikat
# ---------------------------------------------------------------------------

def log_loss(outcomes: list, probs: list) -> float:
    """outcomes: 1 jos 'team' voitti, 0 jos havisi. probs: mallin P(team voittaa)."""
    n = len(outcomes)
    if n == 0:
        return float("nan")
    total = 0.0
    for y, p in zip(outcomes, probs):
        p = min(max(p, EPS), 1 - EPS)
        total += -(y * math.log(p) + (1 - y) * math.log(1 - p))
    return total / n


def brier_score(outcomes: list, probs: list) -> float:
    n = len(outcomes)
    if n == 0:
        return float("nan")
    return sum((p - y) ** 2 for y, p in zip(outcomes, probs)) / n


def calibration_curve(outcomes: list, probs: list, n_bins: int = 10) -> list:
    """Palauttaa listan koreista: {bin, n, avg_predicted, avg_actual}."""
    bins = [[] for _ in range(n_bins)]
    for y, p in zip(outcomes, probs):
        idx = min(int(p * n_bins), n_bins - 1)
        bins[idx].append((p, y))
    out = []
    for i, bucket in enumerate(bins):
        if not bucket:
            out.append({"bin": i, "range": f"{i/n_bins:.1f}-{(i+1)/n_bins:.1f}", "n": 0, "avg_predicted": None, "avg_actual": None})
            continue
        avg_p = sum(p for p, _ in bucket) / len(bucket)
        avg_y = sum(y for _, y in bucket) / len(bucket)
        out.append({"bin": i, "range": f"{i/n_bins:.1f}-{(i+1)/n_bins:.1f}", "n": len(bucket), "avg_predicted": avg_p, "avg_actual": avg_y})
    return out


def remove_margin(price_team: float, price_opponent: float) -> tuple:
    """Desimaalikertoimet -> marginaalipoistettu todennakoisyyspari (summa=1)."""
    imp_team = 1.0 / price_team
    imp_opp = 1.0 / price_opponent
    overround = imp_team + imp_opp
    return imp_team / overround, imp_opp / overround


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass
class MatchRow:
    date: datetime
    team: str
    opponent: str
    tier: Optional[str]
    match_type: Optional[str]
    tournament: Optional[str]
    team_won: int  # 1/0
    market_prob_team: Optional[float] = None  # taytetaan jos kerroin loytyy


def load_clean_matches(conn) -> list:
    rows = conn.execute(
        """SELECT match_date_utc, team, opponent, tier, match_type, tournament,
                  score_team, score_opponent
           FROM historical_matches
           WHERE score_team IS NOT NULL AND score_opponent IS NOT NULL
             AND score_team != score_opponent
           ORDER BY match_date_utc ASC"""
    ).fetchall()
    out = []
    for r in rows:
        date = datetime.fromisoformat(r[0])
        team_won = 1 if r[6] > r[7] else 0
        out.append(MatchRow(date=date, team=r[1], opponent=r[2], tier=r[3], match_type=r[4], tournament=r[5], team_won=team_won))
    return out


# ---------------------------------------------------------------------------
# VRS-sijoitus ajankohtana T (pistetason lookahead-suoja)
# ---------------------------------------------------------------------------

class VrsRankings:
    """Lataa VRS-kuukausisnapshotit ja antaa sijoituksen 'sellaisena kuin se
    oli tunnettu ajankohtana T' - ei koskaan tulevaisuuden snapshotia."""

    def __init__(self, snapshots: dict):
        # snapshots: {"YYYY-MM-DD": {team_name: rank}} -> avaimet date-olioiksi
        from datetime import date as _date

        self.snapshots = {
            _date.fromisoformat(k): v for k, v in snapshots.items()
        }
        self.dates = sorted(self.snapshots.keys())

    def rank_as_of(self, team: str, as_of: datetime) -> Optional[int]:
        as_of_naive = as_of.date()
        chosen = None
        for d in self.dates:
            if d <= as_of_naive:
                chosen = d
            else:
                break
        if chosen is None:
            return None
        return self.snapshots[chosen].get(team)


# ---------------------------------------------------------------------------
# Tyhmat vertailumallit (hyvaksymiskriteeria varten)
# ---------------------------------------------------------------------------

def predict_5050(match: MatchRow, vrs: VrsRankings) -> float:
    return 0.5


def predict_higher_vrs_rank(match: MatchRow, vrs: VrsRankings) -> float:
    """Parempi (pienempi) VRS-sija voittaa varmasti. Jos jompikumpi puuttuu
    top-50:sta, palautetaan 0.5 (ei tietoa)."""
    r_team = vrs.rank_as_of(match.team, match.date)
    r_opp = vrs.rank_as_of(match.opponent, match.date)
    if r_team is None or r_opp is None:
        return 0.5
    if r_team == r_opp:
        return 0.5
    return 1.0 if r_team < r_opp else 0.0


# ---------------------------------------------------------------------------
# Walk-forward-ajuri
# ---------------------------------------------------------------------------

def run_walk_forward(matches: list, predict_fn: Callable, vrs: VrsRankings) -> dict:
    """Kayttaa vain predict_fn:aa - talle v1:lle 'walk-forward' tarkoittaa
    etta jokainen ennuste kayttaa VAIN ajankohtana T tunnettua VRS-dataa
    (ei koko historiaa/tulevaisuutta). Malleille jotka oikeasti fittaavat
    parametreja (Tehtavat 3-5) predict_fn saa myohemmin myos 'matches ennen
    T' -listan sisaan."""
    outcomes = []
    probs = []
    for m in matches:
        p = predict_fn(m, vrs)
        outcomes.append(m.team_won)
        probs.append(p)
    return {
        "n": len(outcomes),
        "log_loss": log_loss(outcomes, probs),
        "brier": brier_score(outcomes, probs),
        "calibration": calibration_curve(outcomes, probs),
    }


def market_walk_forward(matches: list) -> dict:
    """Sama markkinan margin-poistetulle todennakoisyydelle - vain riveille
    joille kerroin on tiedossa (match.market_prob_team ei None)."""
    outcomes = []
    probs = []
    for m in matches:
        if m.market_prob_team is None:
            continue
        outcomes.append(m.team_won)
        probs.append(m.market_prob_team)
    return {
        "n": len(outcomes),
        "log_loss": log_loss(outcomes, probs) if outcomes else None,
        "brier": brier_score(outcomes, probs) if outcomes else None,
        "calibration": calibration_curve(outcomes, probs) if outcomes else None,
    }
