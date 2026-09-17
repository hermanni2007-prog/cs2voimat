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
        # esilasketut lowercase-nakymat per snapshot-paiva (rakennetaan kerran)
        self._lower_snapshots = {
            d: {name.lower(): rank for name, rank in s.items()} for d, s in self.snapshots.items()
        }
        self._fuzzy_cache: dict = {}  # (snapshot_date, raw_name) -> resolved_name tai None

    def _fuzzy_resolve(self, snapshot: dict, name: str) -> Optional[str]:
        """KORJAUS 2026-09-17: tarkka nimi ei loydy usein, koska Liquipedian
        ottelusivun vastustaja-teksti ("Team Liquid") ei tasmaa VRS:n lyhyeen
        nimeen ("Liquid"). Vain 377/860 ottelusta loysi molemmat sijat ennen
        tata korjausta.

        KORJAUS #2 (samana paivana): ensimmainen versio kaytti raakaa
        "in"-osamerkkijonohakua (kuten fetch_market_spotcheck.py:ssa), mika
        tuotti vaaria osumia - esim. lyhyt VRS-nimi "am" tasmasi sanojen
        "te-AM" ja "g-AM-ing" SISALLA ilman sanarajoja, tuottaen 75 vaaraa
        yhdistysta (esim. "Team Liquid" -> "am"). Korjattu sanarajalliseen
        regexiin (\\b...\\b) - "liquid" tasmaa "team liquid":iin valilyonnin
        kohdalla, mutta "am" ei enaa tasmaa "team":iin, koska "am" ei ole
        oma sanansa siina. Lisaksi minimipituus 3 merkkia kandidaateille."""
        import re

        key = name.lower()
        if key in snapshot:
            return key
        candidates = []
        for full in snapshot:
            if len(full) < 3 or len(key) < 3:
                continue
            if re.search(r"\b" + re.escape(full) + r"\b", key):
                candidates.append(full)
            elif re.search(r"\b" + re.escape(key) + r"\b", full):
                candidates.append(full)
        if not candidates:
            return None
        candidates.sort(key=len)
        return candidates[0]

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
        lower_snapshot = self._lower_snapshots[chosen]

        cache_key = (chosen, team)
        if cache_key in self._fuzzy_cache:
            resolved = self._fuzzy_cache[cache_key]
        else:
            resolved = self._fuzzy_resolve(lower_snapshot, team)
            self._fuzzy_cache[cache_key] = resolved
        if resolved is None:
            return None
        return lower_snapshot.get(resolved)


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


def make_predict_vrs_soft(scale: float) -> Callable:
    """KORJAUS 2026-09-17: predict_higher_vrs_rank on deterministinen (0/1),
    minka vuoksi sen log loss (6+) on paatonta huonompi kuin 50/50 - se ei
    ole 'malli on rikki' vaan log lossin matematiikkaa (kova vaara ennuste
    rangaistaan asymptoottisesti). Tama versio muuntaa sijaeron pehmeaksi
    todennakoisyydeksi logistisella funktiolla, samaan tapaan kuin Elo -
    antaa mielekkaamman vertailukohdan. 'scale' fitataan run_elo.py:ssa
    grid-haulla."""
    def predict(match: MatchRow, vrs: "VrsRankings") -> float:
        r_team = vrs.rank_as_of(match.team, match.date)
        r_opp = vrs.rank_as_of(match.opponent, match.date)
        if r_team is None or r_opp is None:
            return 0.5
        diff = r_opp - r_team  # positiivinen jos 'team' on paremmin sijoitettu
        return 1.0 / (1.0 + math.exp(-diff / scale))
    return predict


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


# ---------------------------------------------------------------------------
# Deduplikointi (Tehtava 3): sama ottelu esiintyy kahdesti historical_matches
# -taulussa kun molemmat osapuolet ovat top-50 (kerran kummankin sivulta).
# Tilallisille malleille (Elo) tama pitaa poistaa ETUKATEEN, koska muuten
# rating paivittyisi kahdesti samasta ottelusta. Avain (pvm, turnaus) -
# yksinkertaistus, ks. tiedoston yla kommentti mahdollisesta harvinaisesta
# kollisiosta.
# ---------------------------------------------------------------------------

def deduplicate_matches(matches: list) -> list:
    seen = set()
    out = []
    for m in matches:
        key = (m.date.isoformat(), m.tournament)
        if key in seen:
            continue
        seen.add(key)
        out.append(m)
    return out


# ---------------------------------------------------------------------------
# Tehtava 3: karttatason Elo (round-taso pudotettu, ks. cs2-agenttibriiffi.md
# -laajuuspaatos 2026-09-17 - CT/T-dataa ei saatu ilmaiseksi lahteeksi).
#
# p = 1 / (1 + e^(-(R_team - R_opp) / scale))
# Paivitys: R += k * (actual - p), symmetrisesti molemmille joukkueille.
# Eksponentiaalinen aikavaimennus: jos joukkue ei ole pelannut pitkaan,
# sen rating "unohtuu" kohti keskiarvoa (1500) puoliintumisajalla
# half_life_days - mallintaa rosterimuutosten ja ruostumisen vaikutusta.
# ---------------------------------------------------------------------------

MEAN_RATING = 1500.0


class EloModel:
    def __init__(self, scale: float = 400.0, k_factor: float = 24.0, half_life_days: float = 60.0):
        self.scale = scale
        self.k = k_factor
        self.half_life_days = half_life_days
        self.ratings: dict = {}  # team -> (rating, last_date)

    def _decayed_rating(self, team: str, as_of: datetime) -> float:
        if team not in self.ratings:
            return MEAN_RATING
        rating, last_date = self.ratings[team]
        if self.half_life_days <= 0:
            return rating
        days = (as_of - last_date).total_seconds() / 86400.0
        if days <= 0:
            return rating
        decay = 0.5 ** (days / self.half_life_days)
        return MEAN_RATING + (rating - MEAN_RATING) * decay

    def predict(self, team: str, opponent: str, as_of: datetime) -> float:
        r_team = self._decayed_rating(team, as_of)
        r_opp = self._decayed_rating(opponent, as_of)
        return 1.0 / (1.0 + math.exp(-(r_team - r_opp) / self.scale))

    def update(self, team: str, opponent: str, team_won: int, as_of: datetime) -> None:
        r_team = self._decayed_rating(team, as_of)
        r_opp = self._decayed_rating(opponent, as_of)
        p = 1.0 / (1.0 + math.exp(-(r_team - r_opp) / self.scale))
        actual = float(team_won)
        self.ratings[team] = (r_team + self.k * (actual - p), as_of)
        self.ratings[opponent] = (r_opp + self.k * ((1 - actual) - (1 - p)), as_of)


def run_elo_walkforward(matches: list, elo: EloModel) -> dict:
    """Aidosti tilallinen walk-forward: jokainen ottelu ENSIN ennustetaan
    (vain aiempi data vaikuttaa), SITTEN paivitetaan rating. matches TAYTYY
    olla deduplicate_matches():n lapikaynyt ja aikajarjestyksessa (jo
    load_clean_matches():n ORDER BY ansiosta)."""
    outcomes = []
    probs = []
    for m in matches:
        p = elo.predict(m.team, m.opponent, m.date)
        outcomes.append(m.team_won)
        probs.append(p)
        elo.update(m.team, m.opponent, m.team_won, m.date)
    return {
        "n": len(outcomes),
        "log_loss": log_loss(outcomes, probs),
        "brier": brier_score(outcomes, probs),
        "calibration": calibration_curve(outcomes, probs),
    }
