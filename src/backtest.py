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
    """HUOM 2026-09-18 (kayttajan huomio: "ero on liian suuri selvasti" 3DMAX
    vs EYEBALLERS -ennusteessa, tutkittu ja loydetty aito bugi): opponent-
    sarake historical_matches:ssa tallentaa Liquipedian HTML:sta parsitun
    nayttonimen sellaisenaan, joka VAIHTELEE saman oikean joukkueen kohdalla
    eri lahdesivujen valilla (esim. "SINNERS" vs "SINNERS Esports", "Omega"
    vs "OMEGA", "WW TEAM" vs "WW", "Butterfly (Russian team)" vs "Butterfly").
    Koska deduplicate_matches() avain kayttaa opponent-nimea sellaisenaan,
    tama aiheutti SAMAN oikean ottelun tallentumisen KAHTEEN KERTAAN (112
    tuplaparia loydetty koko datasetista top75-laajennuksen jalkeen, 2026-
    09-18) - vaikutti kymmeniin joukkueisiin, mm. vaaristi 3DMAX:n ja
    EYEBALLERS:n Elo-ratingin (3DMAX:lla tuplahavio, EYEBALLERS:lla
    tuplavoitto, molemmat vaaraan suuntaan). Korjaus: normalisoidaan seka
    team etta opponent kanoniseen top75-nimeen (resolve_to_canonical, sama
    fuzzy-logiikka jota jo kaytettiin VrsRankingsissa ja historical_maps:n
    aiemmassa migraatiossa) ENNEN deduplikointia, jolloin molemmat nimivariantit
    osuvat samaan avaimeen ja tuplat poistuvat oikein."""
    from team_names import load_top50_names, resolve_to_canonical

    canonical_names = load_top50_names()
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
        team = resolve_to_canonical(r[1], canonical_names)
        opponent = resolve_to_canonical(r[2], canonical_names)
        out.append(MatchRow(date=date, team=team, opponent=opponent, tier=r[3], match_type=r[4], tournament=r[5], team_won=team_won))
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


class RosterHistory:
    """Lukee team_rosters-taulun ja vastaa 'ketka pelasivat joukkueessa X
    ajankohtana T' -kysymykseen. Perustuu src/collect_rosters.py:n
    kerailemiin liittymis-/lahtopaiviin (ks. README: Inactive Date ja
    laina-abbr-tekstit jatetty huomiotta v1:ssa - tama voi silloin tallon
    aina-aktiivisena pelaajan joka oikeasti oli hetkellisesti lainassa
    toisessa joukkueessa)."""

    def __init__(self, conn):
        rows = conn.execute(
            "SELECT team, player_id, join_date, leave_date FROM team_rosters"
        ).fetchall()
        self.by_team: dict = {}
        for team, player_id, join_date, leave_date in rows:
            self.by_team.setdefault(team, []).append((player_id, join_date, leave_date))

    def roster_as_of(self, team: str, as_of: datetime) -> list:
        """Palauttaa listan pelaajaId:ita joilla join_date <= as_of JA
        (leave_date IS NULL TAI leave_date > as_of). Ei takaa tasan 5:ta -
        katso luokan docstring tunnetuista yksinkertaistuksista."""
        as_of_str = as_of.date().isoformat()
        out = []
        for player_id, join_date, leave_date in self.by_team.get(team, []):
            if join_date is None or join_date > as_of_str:
                continue
            if leave_date is not None and leave_date <= as_of_str:
                continue
            out.append(player_id)
        return out

    def roster_stability(self, team: str, as_of: datetime, lookback_days: int = 30) -> int:
        """Kuinka moni NYKYISESTA roolista liittyi viimeisen lookback_days
        paivan aikana - karkea 'tuore rosterimuutos' -lippu, jota briiffi
        mainitsee Tehtava 7:n segmentoinnissa ('rosterimuutosliput paalla/pois')."""
        from datetime import timedelta

        as_of_str = as_of.date().isoformat()
        cutoff_str = (as_of - timedelta(days=lookback_days)).date().isoformat()
        recent_joins = 0
        for player_id, join_date, leave_date in self.by_team.get(team, []):
            if join_date is None or join_date > as_of_str:
                continue
            if leave_date is not None and leave_date <= as_of_str:
                continue
            if join_date >= cutoff_str:
                recent_joins += 1
        return recent_joins


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
# rating paivittyisi kahdesti samasta ottelusta.
#
# BUGI (loydetty 2026-09-17, kayttaja lisasi kasin kolme samana paivana
# samassa turnauksessa pelattua ottelua): alkuperainen avain (pvm, turnaus)
# EI sisaltanyt joukkueita - jos KAKSI ERI ottelua samassa turnauksessa
# jaettiin samaan aikaleimaan (esim. ryhmavaiheen ottelut jotka alkavat
# samaan kellonaikaan), toinen niista tulkittiin virheellisesti "saman
# ottelun toiseksi puoleksi" ja pudotettiin kokonaan - yhden joukkueen
# rating ei paivittynyt ollenkaan. Korjattu lisaamalla joukkuepari
# (frozenset, jarjestyksesta riippumaton) avaimeen.
# ---------------------------------------------------------------------------

def deduplicate_matches(matches: list) -> list:
    seen = set()
    out = []
    for m in matches:
        key = (m.date.isoformat(), m.tournament, frozenset({m.team, m.opponent}))
        if key in seen:
            continue
        seen.add(key)
        out.append(m)
    return out


# ---------------------------------------------------------------------------
# SOS-suodatus (strength of schedule) - kayttajan huomio 2026-09-18: joukkueet
# joilla suuri osuus otteluista top50-listan ULKOPUOLISIA vastustajia (esim.
# NRG 47%, karsintasarjojen fodder-joukkueita kuten NuTorious tai Iowa
# Stormboar) muodostavat Elo-verkossa irrallisen osa-altaan jonka rating-taso
# ei ole suoraan vertailukelpoinen aidon top50-vs-top50 -verkon kanssa.
# VALIDOITU OIKEIN (2026-09-18, ks. run_sos_filter.py): fitattu/rakennettu
# VAIN ensimmaisella 80%:lla kronologisesti, tarkistettu nakemattomalla
# 20%:lla top50-vs-top50 -otteluita. Tulos: log loss 0.668 -> 0.663, brier
# 0.237 -> 0.233 - aito parannus, ei ylisovitus. Otettu kayttoon oletukseksi
# ottelukohtaisissa live-ennusteissa (predict_match.py,
# backtest_vs_real_odds.py).
def filter_top50_only(matches: list, top50_names: set) -> list:
    return [m for m in matches if m.team in top50_names and m.opponent in top50_names]


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

# Oletusarvo jos data/elo_report.json ei ole viela olemassa (esim. taysin
# tuore checkout). Paivittyy automaattisesti kun run_elo.py ajetaan.
_FALLBACK_ELO_PARAMS = {"scale": 200.0, "k_factor": 32.0, "half_life_days": 99999.0}


def load_best_elo_params() -> dict:
    """Lukee Tehtava 3:n viimeisimman grid-haun tuloksen (run_elo.py:n
    kirjoittama data/elo_report.json) - EI ENAA hardcodattu joka skriptiin
    erikseen. LOYDETTY ONGELMA (2026-09-17): kun dedup-bugi ja
    nimenormalisointibugi korjattiin, "paras" (scale,k) muuttui KAHDESTI,
    ja jokainen skripti (analyze_series.py, predict_match.py, run_roster_
    signal.py, run_form_lambda.py, generate_report.py) piti paivittaa
    KASIN erikseen - unohdettiin osa niista ensimmaisella kierroksella,
    mika johti hetkelliseen VAARAAN johtopaatokseen (Tehtava 5:n lambda-
    testi, ks. README). Yksi yhteinen lataaja poistaa taman toistuvan
    virhelahteen kokonaan - skriptit eivat voi enaa "unohtua paivittaa"."""
    path = ROOT / "data" / "elo_report.json"
    if not path.exists():
        return dict(_FALLBACK_ELO_PARAMS)
    try:
        import json
        data = json.loads(path.read_text(encoding="utf-8"))
        p = data["best_params"]
        return {"scale": float(p["scale"]), "k_factor": float(p["k"]), "half_life_days": float(p["half_life_days"])}
    except (KeyError, ValueError, OSError):
        return dict(_FALLBACK_ELO_PARAMS)


class EloModel:
    def __init__(self, scale: float = 400.0, k_factor: float = 24.0, half_life_days: float = 60.0):
        self.scale = scale
        self.k = k_factor
        self.half_life_days = half_life_days
        self.ratings: dict = {}  # team -> (rating, last_date)
        self.games_played: dict = {}  # team -> int, ks. rating_deviation()

    def rating_as_of(self, team: str, as_of: datetime) -> float:
        """Julkinen versio _decayed_rating:sta - kayttoon Tehtava 5:n
        (formipaino) kahden rinnakkaisen mallin sekoitukseen."""
        return self._decayed_rating(team, as_of)

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

    def update(self, team: str, opponent: str, team_won: int, as_of: datetime,
               k_override: Optional[float] = None) -> None:
        """k_override: kaytossa esim. run_online_weight.py:ssa online-otteluiden
        painon skaalaamiseen rating-PAIVITYKSESSA (eri asia kuin apply_online_
        adjustment, joka vaikuttaa vain ENNUSTEESEEN, ei rating-historiaan)."""
        r_team = self._decayed_rating(team, as_of)
        r_opp = self._decayed_rating(opponent, as_of)
        p = 1.0 / (1.0 + math.exp(-(r_team - r_opp) / self.scale))
        actual = float(team_won)
        k = self.k if k_override is None else k_override
        self.ratings[team] = (r_team + k * (actual - p), as_of)
        self.ratings[opponent] = (r_opp + k * ((1 - actual) - (1 - p)), as_of)
        self.games_played[team] = self.games_played.get(team, 0) + 1
        self.games_played[opponent] = self.games_played.get(opponent, 0) + 1

    # -------------------------------------------------------------------
    # Epavarmuuden mallinnus (lisatty 2026-09-17, kayttajan pyynnosta -
    # loydettiin kayttamalla NRG:ta esimerkkina: malli antoi TARKAN 55.5%
    # -luvun vaikka pohjalla oli vain 6 kelvollista ottelua, ei mitaan
    # tapaa ilmaista etta talle luvulle EI PIDA luottaa yhta paljon kuin
    # esim. MOUZ:n 43 ottelun paalle lasketulle. Ei taydellinen Glicko
    # (ei seuraa volatiliteettia/aikahajontaa erikseen), mutta sama
    # perusidea: rating deviation (RD) pienenee pelattujen ottelujen
    # myota, alkaen suuresta (350, Glickon oma oletusarvo) ja lahestyen
    # lattiaa (RD_MIN) - RD_HALFLIFE_GAMES saatiin karkealla arviolla
    # (ei viela erikseen kalibroitu), ei tarkka tiede.
    # -------------------------------------------------------------------
    RD_MAX = 350.0
    RD_MIN = 40.0
    RD_HALFLIFE_GAMES = 12.0

    def rating_deviation(self, team: str) -> float:
        """Suurempi RD = epavarmempi rating. Uudelle/harvoin pelanneelle
        joukkueelle lahella RD_MAX:a, paljon pelanneelle lahella RD_MIN:a."""
        n = self.games_played.get(team, 0)
        return self.RD_MIN + (self.RD_MAX - self.RD_MIN) * (0.5 ** (n / self.RD_HALFLIFE_GAMES))

    def predict_with_confidence(self, team: str, opponent: str, as_of: datetime) -> dict:
        """Palauttaa piste-ennusteen LISAKSI karkean epavarmuushaarukan:
        p_low/p_high siirtavat kumpaakin ratingia yhden RD:n verran
        EPASUOTUISAAN suuntaan ennen ennustetta - ei tilastollisesti
        tasmallinen luottamusvali, mutta antaa karkean, rehellisen kuvan
        siita kuinka paljon arvio voisi liikkua jos rating on vaarassa
        suunnassa vaarin (esim. liian vahan dataa)."""
        r_team = self._decayed_rating(team, as_of)
        r_opp = self._decayed_rating(opponent, as_of)
        rd_team = self.rating_deviation(team)
        rd_opp = self.rating_deviation(opponent)

        p_mid = 1.0 / (1.0 + math.exp(-(r_team - r_opp) / self.scale))
        p_low = 1.0 / (1.0 + math.exp(-((r_team - rd_team) - (r_opp + rd_opp)) / self.scale))
        p_high = 1.0 / (1.0 + math.exp(-((r_team + rd_team) - (r_opp - rd_opp)) / self.scale))

        n_team = self.games_played.get(team, 0)
        n_opp = self.games_played.get(opponent, 0)
        n_min = min(n_team, n_opp)
        if n_min < 10:
            confidence = "MATALA"
        elif n_min < 25:
            confidence = "KOHTALAINEN"
        else:
            confidence = "HYVA"

        return {
            "p_mid": p_mid, "p_low": p_low, "p_high": p_high,
            "n_team": n_team, "n_opp": n_opp, "confidence": confidence,
            "rd_team": rd_team, "rd_opp": rd_opp,
        }


# ---------------------------------------------------------------------------
# "Ruostumis"-korjaus (lisatty 2026-09-17, kayttajan pyynnosta - alkuperainen
# hypoteesi oli "uupumus" (liikaa otteluita -> huonompi tulos), mutta
# run_tournament_effects.py:n mittaus (ks. README) osoitti PAINVASTAISEN
# ilmion: suosikki joka EI OLE PELANNUT VIIMEISEN 5 VRK:N AIKANA havisi
# ODOTETTUA USEAMMIN - ei uupumus vaan "ring rust"/kilpailurytmin puute.
#
# KAAVA (empiirisesti sovitettu, ei teoreettinen): OLS-kalibrointikulmakerroin
# per bucket (pakotettu leikkauspiste 0.5, ks. fit: slope = Sum((p-0.5)(y-0.5))
# / Sum((p-0.5)^2)):
#   suosikki EI pelannut viimeisen 5 vrk:n aikana (n=227): slope = 0.790
#     -> malli YLILUOTTAVAINEN, ennustetta pitaa vetaa kohti 0.5:ta
#   suosikki PELASI >=1 kertaa viimeisen 5 vrk:n aikana (n=810): slope = 1.117
#     -> lahella 1.0:aa, ei korjata (mahdollinen lievä aliluottamus jatetaan
#        korjaamatta - pienempi riski kuin yliluottamuksen jattaminen)
#
# p_korjattu = 0.5 + (p_raaka - 0.5) * RUST_SHRINK   (vain jos suosikilla n=0)
#
# HUOM: tama on toistaiseksi YKSI kertaluokka karkeampi kuin oikea Glicko-
# tyylinen ratkaisu (ei huomioi MONTAKO paivaa on kulunut, vain 0 vs >=1),
# ja perustuu 227 ottelun otokseen - ei kalibroitu markkinaa vastaan.
# ---------------------------------------------------------------------------
RUST_WINDOW_DAYS = 5
RUST_SHRINK_NO_RECENT_MATCH = 0.790


def count_recent_matches(team: str, as_of, matches_by_team: dict, window_days: float = RUST_WINDOW_DAYS) -> int:
    """matches_by_team: team -> LAJITELTU lista ottelupaivamaarista (esim.
    run_tournament_effects.build_recent_match_index:n tuottama). Laskee
    kuinka monta ottelua team on pelannut valilla [as_of - window_days, as_of)."""
    import bisect
    from datetime import timedelta

    dates = matches_by_team.get(team, [])
    lo = as_of - timedelta(days=window_days)
    left = bisect.bisect_left(dates, lo)
    right = bisect.bisect_left(dates, as_of)
    return right - left


def apply_rust_adjustment(p_team: float, n_recent_team: int, n_recent_opponent: int) -> float:
    """Kutistaa ennusteen kohti 0.5:ta JOS suosikilla (kumpi tahansa puoli
    p_team>=0.5 mukaan) ei ole yhtaan ottelua viimeisen RUST_WINDOW_DAYS:n
    aikana. Ei muuta ennustetta jos suosikki on pelannut äskettäin."""
    is_team_favorite = p_team >= 0.5
    favorite_n_recent = n_recent_team if is_team_favorite else n_recent_opponent
    if favorite_n_recent == 0:
        return 0.5 + (p_team - 0.5) * RUST_SHRINK_NO_RECENT_MATCH
    return p_team


# ---------------------------------------------------------------------------
# Vasymyskorjaus (lisatty 2026-09-18, kayttajan pyynnosta "think outside
# the box" -kehitysideat): VASTAKOHTA ruostumiskorjaukselle - jos suosikki
# on pelannut MONTA ottelua lyhyessa ajassa (esim. round-robin-turnauksen
# 3.+ ottelu samana paivana, kuten Logitech G Play Connect 2026 tanaan),
# ennuste saattaa olla epaluotettavampi kuin Elo-ero antaisi ymmartaa.
#
# VALIDOITU OIKEIN (run_fatigue_shrink.py): kiintea ikkuna (24h) ja kynnys
# (>=2 ottelua = tama on jo 3.+ ottelu), shrink grid-haettu VAIN train-
# datalla, paras arvo 0.4. NAKEMATTOMALLA test-osiolla: "vasynyt"-ryhma
# parani selvasti (log loss 0.7064 -> 0.6909, n=61), "tuore"-ryhma TAYSIN
# KOSKEMATON (0.6481 = 0.6481, koska shrink on no-op ei-vasyneille) - koko
# parannus tulee puhtaasti vasyneesta ryhmasta, ei muiden kustannuksella
# (sama puhdas kuvio kuin ONLINE_SHRINK/LEVEA_SHRINK aikanaan). HUOM: train-
# kayra on melko litea (0.6844-0.6853 koko [0,1]-valilla) - pieni otos
# (n=61), joten "toistaiseksi tuettu" samaan tapaan kuin ruostumiskorjaus,
# ei "todistettu".
# ---------------------------------------------------------------------------
FATIGUE_WINDOW_DAYS = 1.0  # 24h
FATIGUE_THRESHOLD = 2  # >=2 ottelua ikkunassa = tama on jo 3.+ ottelu
FATIGUE_SHRINK = 0.4  # validoitu grid-haulla, ei arvaus


def apply_fatigue_adjustment(p_team: float, n_fatigue_team: int, n_fatigue_opponent: int) -> float:
    """Kutistaa ennusteen kohti 0.5:ta JOS suosikilla on >=FATIGUE_THRESHOLD
    ottelua viimeisen FATIGUE_WINDOW_DAYS:n (24h) aikana - eri ikkuna kuin
    ruostumiskorjaus (5 vrk), lasketaan erikseen `count_recent_matches`-
    kutsulla FATIGUE_WINDOW_DAYS-parametrilla."""
    is_team_favorite = p_team >= 0.5
    favorite_n_fatigue = n_fatigue_team if is_team_favorite else n_fatigue_opponent
    if favorite_n_fatigue >= FATIGUE_THRESHOLD:
        return 0.5 + (p_team - 0.5) * FATIGUE_SHRINK
    return p_team


# ---------------------------------------------------------------------------
# Online-korjaus (lisatty 2026-09-18, kayttajan pyynnosta - run_lan_online_
# upsets.py:n loydos): online-otteluissa suosikki havisi selvasti useammin
# kuin malli ennustaa (upset-rate 47.1% vs odotettu 40.6%, n=933 SOS-
# suodatetulla datalla), kun taas LAN/Offline on lahes tasmalleen
# kalibroitu (41.8% vs 40.3%). Todennakoinen selitys: top-joukkueet
# kayttavat online-karsinnoissa useammin stand-ineja / eivat panosta
# taydella kokoonpanolla. (HUOM 2026-09-18: TAMA ON ERI ASIA kuin
# kayttajan tanaan huomaamat magic-valmentaja/Vitality-mezii-stand-init -
# ne olivat StarLadder StarSeries Fall 2026 -turnauksen LAN-otteluita,
# ei online. Vahvistin virheellisesti nama samaksi ilmioksi kayttajalle -
# ne ovat kaksi ERILLISTA, EI-liittyvaa puutetta: online-korjaus koskee
# koko online-kategoriaa yleisesti, kun taas yksittaiset stand-init
# voivat tapahtua seka LANilla etta onlinena eika kumpaakaan tallenneta
# erikseen dataamme.)
#
# VALIDOITU OIKEIN (run_online_adjustment.py): shrink-kerroin grid-haettu
# VALILTA [0.0, 1.0] VAIN train-online-otteluilla (n=179, ensimmainen 80%
# kronologisesti), tarkistettu nakemattomalla test-online-joukolla (n=61).
# Kayra on TAYSIN MONOTONINEN - log loss paranee jatkuvasti mita enemman
# mallia kutistetaan, paras arvo koko haetulla valilla on aarirajalla
# s=0.0 (= mallilla ei ole MINKAANLAISTA hyodyllista signaalia online-
# otteluissa - raaka malli on jopa HUONOMPI kuin pelkka 50/50-arvaus,
# train_log_loss 0.7253 vs 0.6931). Nakemattomalla testilla sama suunta
# (0.6995 -> 0.6931). HUOM: pieni otos (n=179/61, yksi train/test-jako) -
# vahva tulos mutta ei yhta laajasti testattu kuin ruostumiskorjaus.
# ---------------------------------------------------------------------------
ONLINE_SHRINK = 0.0  # "ei kaytannon ennustearvoa" - validoitu, ei arvaus


def apply_online_adjustment(p_team: float, match_type: Optional[str]) -> float:
    """Kutistaa ennusteen kohti 0.5:ta JOS ottelu on Online (ei LAN/Offline).
    match_type odottaa historical_matches.match_type-arvoja ('Online',
    'Offline', 'LAN', tai None jos tuntematon - tuntemattomalle ei
    korjata, koska emme tieda kummasta on kyse)."""
    if match_type == "Online":
        return 0.5 + (p_team - 0.5) * ONLINE_SHRINK
    return p_team


# ---------------------------------------------------------------------------
# Online-paino RATING-PAIVITYKSESSA (lisatty 2026-09-18, kayttajan
# jatkokysymys apply_online_adjustment:n jalkeen): edellinen korjaus
# vaimentaa vain ENNUSTETTA online-ottelulle, mutta online-ottelun TULOS
# paivitti silti joukkueen ratingia taydella K-kertoimella - vaikuttaen
# kaikkiin TULEVIIN ennusteisiin (myos LAN-otteluihin). Kysymys: pitaisiko
# online-tuloksille antaa pienempi paino ITSE rating-paivityksessa?
#
# VALIDOITU OIKEIN (run_online_weight.py): online_weight grid-haettu
# valilta [0.0, 1.0] VAIN train-osiolla (koko datasetin log loss, ei vain
# online-otteluiden - tavoite on parantaa yleista rating-laatua), paras
# arvo 0.2. Nakemattomalla test-osiolla (n=187): koko test-joukon log
# loss parani 0.6397 -> 0.6379, ja erityisesti online-test parani
# selvasti (0.6995 -> 0.6854); LAN-test heikkeni hieman (0.6107 ->
# 0.6149) mutta kokonaisvaikutus on positiivinen. HUOM: sama pieni-otos-
# varaus kuin apply_online_adjustment:lla.
# ---------------------------------------------------------------------------
ONLINE_UPDATE_WEIGHT = 0.2  # validoitu grid-haulla, ei arvaus


def online_k_override(k_factor: float, match_type: Optional[str]) -> Optional[float]:
    """Palauttaa skaalatun K-kertoimen EloModel.update()-kutsuun kun ottelu
    on Online, muuten None (=kaytä oletus-K:ta)."""
    if match_type == "Online":
        return k_factor * ONLINE_UPDATE_WEIGHT
    return None


# ---------------------------------------------------------------------------
# SOS-PEHMENNYS (lisatty 2026-09-18, kayttajan pyynnosta "yrita keksia
# lisaa"): filter_top50_only() on TAYSI PAALLA/POIS-kytkin - ottelu top75-
# listan ULKOPUOLISTA vastustajaa vastaan POISTETAAN kokonaan Elo-
# paivityksesta. Tama hylkaa KAIKEN signaalin naista otteluista (esim.
# selva lakaisu heikkoa karsintajoukkuetta vastaan kertoo silti JOTAIN,
# vain vahemman luotettavasti).
#
# VALIDOITU OIKEIN (run_sos_soft_weight.py): grid-haku non-top75-vastustaja
# -otteluiden K-multiplier [0.0, 1.0] VAIN train-datalla (multiplier=0.0
# vastaa nykyista hard filtteria, multiplier=1.0 taysin suodattamatonta
# esi-SOS-mallia). Paras arvo 0.5 (tasainen minimi valilla 0.4-0.7, ei
# ylisovitus-piikki). NAKEMATTOMALLA top75-vs-top75-test-osiolla (n=265):
# NYKYINEN hard filter log_loss=0.6654, EI SUODATUSTA=0.6580, PEHMEA
# PAINOTUS (0.5)=0.6584 - molemmat selvasti parempia kuin hard filter.
# HUOM MERKITTAVA: tama KUMOAA aiemman filter_top50_only-loydoksen (joka
# validoitiin ALKUPERAISELLA top50-datasetilla ennen top75-laajennusta,
# 2026-09-18 aiemmin samana paivana) - datasetin kasvaessa (top75, 8kk
# historia 25 uudelle joukkueelle) "irrallisen poolin" ongelma on
# lientynyt sen verran etta TAYSI poissuodatus on nyt liikaa - se heittaa
# pois hyodyllista signaalia jota pehmea painotus sailyttaa. Tama on
# esimerkki siita etta validointi pitaa TOISTAA kun data muuttuu
# merkittavasti, ei luottaa vanhaan tulokseen ikuisesti.
#
# filter_top50_only() JATETTY KOODIIN silla se on yha kaytossa monessa jo
# olemassa olevassa tutkimusskriptissa (run_tier_calibration.py, run_
# tier_shrink.py, run_roster_shrink.py, run_mov_elo.py, run_map_level_
# elo.py) niiden OMAN, jo raportoidun tuloksen toistettavuuden vuoksi -
# UUSI tuotantopolku (predict_match.py, backtest_vs_real_odds.py) kayttaa
# talta lahtien sos_k_override():a filter_top50_only():n SIJASTA.
# ---------------------------------------------------------------------------
SOS_NON_TOP50_K_WEIGHT = 0.5  # validoitu grid-haulla, ei arvaus


def sos_k_override(k_factor: float, team: str, opponent: str, top50_names: set) -> float:
    """Palauttaa RATING-PAIVITYKSEN K-kertoimen: taysi jos molemmat
    top75-listalla, muuten skaalattu SOS_NON_TOP50_K_WEIGHT:lla."""
    if team in top50_names and opponent in top50_names:
        return k_factor
    return k_factor * SOS_NON_TOP50_K_WEIGHT


def production_k_override(k_factor: float, match_type: Optional[str], team: str, opponent: str,
                           top50_names: set) -> float:
    """Yhdistaa online- ja SOS-pehmennyskertoimet YHDEKSI K:ksi tuotanto-
    ennusteille (predict_match.py, backtest_vs_real_odds.py). Molemmat ovat
    itsenaisesti validoituja K-multipliereita samalle EloModel.update()-
    kutsulle, joten ne kerrotaan yhteen (kumpikin skaalaa erikseen samaa
    perus-K:ta, ei toisiaan)."""
    k = k_factor
    if match_type == "Online":
        k *= ONLINE_UPDATE_WEIGHT
    if not (team in top50_names and opponent in top50_names):
        k *= SOS_NON_TOP50_K_WEIGHT
    return k


# ---------------------------------------------------------------------------
# TASO-korjaus - RAKENNETTIIN JA SITTEN POISTETTIIN (2026-09-17).
#
# Kayttaja huomautti perustellusti: kaikki tama session'in "validointi" on
# tehnyt saman virheen jos kalibrointikerroin FITATAAN koko datasetilla ja
# sen jalkeen "todennetaan" soveltamalla se TAKAISIN samaan dataan - se ei
# ole validointia, se on kehaa. Aiempi versio talla paikalla vaitti etta
# S/A-Tier-otteluissa malli on yliluottavainen (OLS-slope=0.563 koko
# datasetilla) ja "validoi" taman huonontamalla ennustetta koko datasetilla
# ja pienella "viimeinen 20%" -tarkistuksella JOKA OLI ITSEKIN SAASTUNUT
# (se 20% oli mukana slope:n fittauksessa alusta asti).
#
# OIKEA TESTI: fitattiin slope VAIN train-osalla (ensimmaiset 80%, n=311
# HIGH-tier-ottelua) -> slope=0.211, TAYSIN ERI kuin koko datasetin 0.563.
# Sovellettuna AIDOSTI nakemattomaan test-osaan (n=50 HIGH-tier ottelua)
# se HUONONSI log lossia (0.624 -> 0.675). Tama tarkoittaa etta "taso"-
# ilmio ei yleisty - se oli ylisovitus/kohinaa koko-datasetin fittauksessa,
# ei aito, toistuva efekti. POISTETTU KOKONAAN kayttajan huomion ansiosta.
#
# (Vertaa: ruostumiskorjaus LAPAISI saman testin - slope pysyi lahes
# samana train-only-fitatussa (0.792) ja koko-datasetin (0.790) versiossa,
# ja paransi log lossia myos aidosti nakemattomalla test-datalla (0.684 ->
# 0.683, n=28) - siksi se on yha kaytossa alla, tosin pienella n:lla
# varustettuna "toistaiseksi tuettu" -leimalla, ei "todistettu".)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Rank-tier-korjaus (lisatty 2026-09-18, kayttajan pyynnosta "top 70 tiimit
# voi vaatia erilaisia kaavoja kuin top 20"): diagnoosi (run_tier_
# calibration.py) loysi aidon kalibrointieron nykyisella yhdella globaalilla
# mallilla - LEVEA-tason (molemmat joukkueet SENHETKISESSA Elo-rankingissa
# sijalla >20) ottelut ennustettiin selvasti huonommin (test log loss 0.689)
# kuin ELIITTI-tason (molemmat top20, 0.643) tai SEKA-tason (0.605) ottelut.
#
# ENSIMMAINEN YRITYS (K-kerroin-multiplier RATING-PAIVITYKSESSA, ks. run_
# tier_calibration.py) EI KESTANYT rehellista testia - kun multiplier
# rajattiin koskemaan VAIN LEVEA-otteluita (ei ELIITTI/SEKA-otteluita),
# train-optimipiste litistyi kohtaan 1.0 (0.6940 vs 0.6942, kohinan
# sisalla) ja test-tulos oli TAYSIN IDENTTINEN nykyiseen (0.6525 = 0.6525).
# HYLATTY - ei aitoa signaalia, aiempi "parannus" johtui pelkastaan siita
# etta multiplier vaikutti myos SEKA-otteluihin (jotka sisaltavat top20-
# joukkueen), ei aidosta LEVEA-tier-efektista.
#
# TOINEN YRITYS (TASSA, run_tier_shrink.py) - eri mekanismi, sama periaate
# kuin online-korjaus: EI kosketa Elo-ratingin PAIVITYSTA lainkaan, vain
# kutistetaan lopullista ENNUSTETTA kohti 0.5:ta LEVEA-otteluille. Grid-
# haku shrink [0.0, 1.5] VAIN train-datalla, paras arvo 0.5 (tasainen
# minimi valilla 0.4-0.6, ei terava ylisovitus-piikki). NAKEMATTOMALLA
# test-osiolla: KAIKKI 0.6525 -> 0.6505, LEVEA-test 0.6887 -> 0.6845,
# ELIITTI+SEKA-test TAYSIN KOSKEMATON (0.6213 = 0.6213, koska shrink on
# no-op kun is_levea=False) - eli parannus tulee PUHTAASTI LEVEA-
# ottelusta, ei muiden tierien kustannuksella. Otettu kayttoon.
#
# HUOM: rank lasketaan SENHETKISESTA Elo-ratingista (elo.ratings, joukkue
# jolla >=1 ottelu takana) - taysin kausaalinen, ei lookahead-vuotoa.
# Peliaikaisesti epavakaa alussa (harvat joukkueet pelanneet), mutta sama
# koskee koko Elo-mallia muutenkin.
# ---------------------------------------------------------------------------
LEVEA_RANK_CUTOFF = 20
# HUOM 2026-09-18 (SOS-pehmennyksen jalkeen, ks. sos_k_override()-kommentti):
# LEVEA_SHRINK=0.5 validoitiin AIKAISEMMIN SAMANA PAIVANA hard filter_top50_
# only -pohjalla. Kun SOS-suodatus vaihdettiin pehmeaksi painotukseksi,
# LEVEA-tason kalibrointiero HAVIISI LAHES KOKONAAN itsestaan - uudelleen-
# validointi (recheck_levea_on_soft_sos.py-tyylinen grid-haku) loysi
# optimin siirtyneen ~1.0:aan (taysin litea kayra 1.0-1.4 valilla), ja
# VANHA arvo 0.5 on NYT test-datalla HUONOMPI kuin ei korjausta lainkaan
# (0.6618 vs 0.6584 log loss). Todennakoinen selitys: pehmea SOS-painotus
# JO korjasi sen alikalibroinnin jota LEVEA_SHRINK yritti paikata erikseen
# - kaksi korjausta samaan ongelmaan, jalkimmainen muuttui tarpeettomaksi/
# haitalliseksi kun ensimmainen parani. POISTETTU KAYTOSTA (1.0 = ei
# vaikutusta) mutta mekanismi jatetty koodiin dokumentoiduksi/uudelleen-
# aktivoitavaksi jos joskus tarpeen. Opetus: yhden korjauksen validointi
# EI ole pysyva jos jokin TOINEN, myohemmin lisatty korjaus muuttaa samaa
# alla olevaa dataa - interaktiot pitaa tarkistaa uudelleen.
LEVEA_SHRINK = 1.0  # POISTETTU KAYTOSTA - ks. yla kommentti


def current_elo_rank(elo: "EloModel", team: str, as_of, candidate_names: Optional[set] = None) -> int:
    """1 = vahvin senhetkinen Elo-rating. Joukkueet joilla ei viela yhtaan
    ottelua saavat sijan len(pelanneet)+1 (huonoin mahdollinen, koska
    default-rating 1500 ei kerro mitaan oikeasta tasosta).

    `candidate_names`: rajaa ranking-poolin (esim. top75-listaan) - TARKEA
    2026-09-18 SOS-pehmennyksen jalkeen, koska sos_k_override() EI enaa
    poista top75-ulkopuolisia vastustajia Elo-paivityksesta (ne saavat vain
    pienemman K:n) - ilman tata rajausta ranking-pooliin ilmestyisi satoja
    heikkoja karsintajoukkueita, mika siirtaisi LEVEA_RANK_CUTOFF:n
    merkitysta verrattuna siihen miten LEVEA_SHRINK aikanaan validoitiin
    (top75-vs-top75-datalla, filter_top50_only-rajattuna)."""
    played = [(t, elo.rating_as_of(t, as_of)) for t in elo.ratings if elo.games_played.get(t, 0) >= 1]
    if candidate_names is not None:
        played = [(t, r) for t, r in played if t in candidate_names]
    if not played:
        return 999
    played.sort(key=lambda x: -x[1])
    for i, (t, _) in enumerate(played):
        if t == team:
            return i + 1
    return len(played) + 1


def apply_tier_adjustment(p_team: float, rank_team: Optional[int], rank_opponent: Optional[int]) -> float:
    """Kutistaa ennusteen kohti 0.5:ta JOS molemmat joukkueet ovat
    senhetkisessa Elo-rankingissa sijalla > LEVEA_RANK_CUTOFF (ei kumpikaan
    "eliittia"). rank=None (esim. rankia ei laskettu/saatavilla) -> ei
    korjata, koska emme tieda kummasta on kyse."""
    if rank_team is None or rank_opponent is None:
        return p_team
    if rank_team > LEVEA_RANK_CUTOFF and rank_opponent > LEVEA_RANK_CUTOFF:
        return 0.5 + (p_team - 0.5) * LEVEA_SHRINK
    return p_team


def apply_all_adjustments(p_team: float, n_recent_team: int, n_recent_opponent: int,
                           tier: Optional[str] = None, match_type: Optional[str] = None,
                           rank_team: Optional[int] = None, rank_opponent: Optional[int] = None,
                           n_fatigue_team: Optional[int] = None, n_fatigue_opponent: Optional[int] = None) -> float:
    """Soveltaa ruostumis-, online-, rank-tier- ja vasymyskorjaukset
    peräkkäin (kaikki ovat kutistuksia kohti 0.5:ta, joten jarjestys ei
    muuta lopputulosta merkittavasti). Taso-korjaus (S/A-Tier) poistettiin,
    ks. yla kommentti. `tier`-parametri jatetty rajapintaan taaksepain
    yhteensopivuuden vuoksi, ei enaa kaytossa. `rank_team`/`rank_opponent`:
    ks. current_elo_rank() - jatetaan None:ksi jos ei saatavilla (ei
    korjata). `n_fatigue_team`/`n_fatigue_opponent`: ottelumaara 24h
    ikkunassa (ERI ikkuna kuin n_recent_team/n_recent_opponent, jotka
    ovat RUST_WINDOW_DAYS=5 vrk:n ikkunassa) - jatetaan None:ksi (=ei
    korjata) jos ei saatavilla."""
    p = apply_rust_adjustment(p_team, n_recent_team, n_recent_opponent)
    p = apply_online_adjustment(p, match_type)
    p = apply_tier_adjustment(p, rank_team, rank_opponent)
    if n_fatigue_team is not None and n_fatigue_opponent is not None:
        p = apply_fatigue_adjustment(p, n_fatigue_team, n_fatigue_opponent)
    return p


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


# ---------------------------------------------------------------------------
# Tehtava 5: formipaino lambda.
#
# PV = R_hidas + lambda * (R_nopea - R_hidas)
# R_hidas: puoliintumisaika ~180 vrk (pitka muisti, "todellinen taso")
# R_nopea: puoliintumisaika ~21 vrk (lyhyt muisti, "tuore forma")
# lambda haetaan ruudukosta 0..1 minimoiden walk-forward log loss - TAMA
# ON PROJEKTIN ALKUPERAINEN TEESI: painottaako tuore forma tuo edgen.
# Jos paras lambda ajautuu 0:aan, teesi EI pida paikkaansa tassa datassa -
# raportoidaan suoraan, ei etsita kiertotieta (kayttajan oma vaatimus).
# ---------------------------------------------------------------------------

def run_dual_elo_walkforward(matches: list, elo_slow: EloModel, elo_fast: EloModel, lam: float) -> dict:
    """Molemmat mallit paivittyvat JOKAISELLA ottelulla omalla
    puoliintumisajallaan riippumatta lambda:sta - lambda vaikuttaa vain
    ENNUSTEEN sekoitukseen, ei rating-paivitykseen. Sekoitus tehdaan
    RATING-avaruudessa (PV = R_hidas + lambda*(R_nopea-R_hidas)), kuten
    briiffi maarittelee, ei erillisina todennakoisyyksina."""
    outcomes = []
    probs = []
    for m in matches:
        rt_slow = elo_slow.rating_as_of(m.team, m.date)
        rt_fast = elo_fast.rating_as_of(m.team, m.date)
        pv_team = rt_slow + lam * (rt_fast - rt_slow)

        ro_slow = elo_slow.rating_as_of(m.opponent, m.date)
        ro_fast = elo_fast.rating_as_of(m.opponent, m.date)
        pv_opp = ro_slow + lam * (ro_fast - ro_slow)

        p = 1.0 / (1.0 + math.exp(-(pv_team - pv_opp) / elo_slow.scale))
        outcomes.append(m.team_won)
        probs.append(p)

        elo_slow.update(m.team, m.opponent, m.team_won, m.date)
        elo_fast.update(m.team, m.opponent, m.team_won, m.date)
    return {
        "n": len(outcomes),
        "log_loss": log_loss(outcomes, probs),
        "brier": brier_score(outcomes, probs),
        "calibration": calibration_curve(outcomes, probs),
    }
