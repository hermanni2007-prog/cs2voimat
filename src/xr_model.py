"""
xR (expected rounds) -malli: kierrostason luokitus, joka oppii puoliskojen
kierrosmaarista (historical_maps.team*_halves_json) eika pelkasta sarjan
voitto/tappiosta - ~50x enemman havaintoja kuin sarjatason Elossa.

Rajoite: datassa on vain puoliskojen kierrossummat (ei yksittaisia
kierroksia, ekonomiaa tai pistooleja), joten malli on puoliskotason.

Rakenne:
  p(joukkue voittaa kierroksen CT:na vastustajaa vastaan kartalla m)
      = sigmoid((r_joukkue - r_vastustaja) / SCALE + c_m)
  T-puolella sama, mutta -c_m. c_m = kentan CT-etu kartalla m (logit),
  estimoidaan kutsujan antamasta (train-)datasta - ei vuotoa testiin.
  Paivitys per puolisko: r += K * (voitetut - pelatut * p).

Kartan voitto kierrostodennakoisyyksista lasketaan tarkasti (MR12: 13
voittaa, 12-12 -> jatkoajat MR3 ensimmainen 4:aan, 3-3 -> uusi jatkoaika).
Kierrokset eivat ole oikeasti riippumattomia (ekonomia, momentum), joten
karttatodennakoisyys kalibroidaan: sigmoid(tau * logit(P_kartta)), tau
sovitetaan train-datalla (run_xr.py).
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache

SCALE = 400.0  # vain K/SCALE-suhde merkitsee - SCALE kiinnitetaan, K haetaan
REGULATION_HALF = 12
WIN_ROUNDS = 13


def sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def logit(p: float) -> float:
    p = min(max(p, 1e-9), 1 - 1e-9)
    return math.log(p / (1 - p))


@dataclass
class Half:
    """Yhden joukkueen yksi varsinaisen peliajan puolisko."""
    team: str
    opponent: str
    side: str  # "CT" / "T"
    map_name: str
    won: int
    played: int


@dataclass
class MapGame:
    date: datetime
    team1: str
    team2: str
    map_name: str
    map_order: int
    team1_won: int
    team1_rounds: int
    team2_rounds: int
    team1_start_side: str | None
    halves: list  # [Half, ...] molempien joukkueiden varsinaisen peliajan puoliskot


def load_map_games(conn, canonical_names: set) -> list:
    """historical_maps -> MapGame-lista aikajarjestyksessa, nimet
    normalisoitu, duplikaatit (sama pvm+joukkueet+kartan jarjestys) poistettu."""
    from team_names import resolve_to_canonical

    rows = conn.execute(
        """SELECT match_date_utc, team1, team2, map_order, map_name, team1_score, team2_score,
                  team1_halves_json, team2_halves_json
           FROM historical_maps
           WHERE team1_score IS NOT NULL AND team2_score IS NOT NULL AND match_date_utc IS NOT NULL"""
    ).fetchall()
    seen = set()
    out = []
    for d, t1, t2, order, m, s1, s2, h1j, h2j in rows:
        t1c = resolve_to_canonical(t1, canonical_names)
        t2c = resolve_to_canonical(t2, canonical_names)
        key = (d, frozenset((t1c, t2c)), order)
        if key in seen or s1 == s2:
            continue
        seen.add(key)
        try:
            h1 = json.loads(h1j) if h1j else []
            h2 = json.loads(h2j) if h2j else []
        except json.JSONDecodeError:
            h1, h2 = [], []
        halves = []
        # Puoliskot parittain indeksin mukaan: samassa puoliskossa joukkueet
        # ovat vastakkaisilla puolilla. Vain 2 ensimmaista = varsinainen peliaika.
        for i in range(min(2, len(h1), len(h2))):
            a, b = h1[i], h2[i]
            if a.get("side") not in ("CT", "T") or b.get("side") == a.get("side"):
                continue
            n = int(a.get("score", 0)) + int(b.get("score", 0))
            if n <= 0:
                continue
            halves.append(Half(t1c, t2c, a["side"], m, int(a["score"]), n))
            halves.append(Half(t2c, t1c, b["side"], m, int(b["score"]), n))
        out.append(MapGame(
            date=datetime.fromisoformat(d), team1=t1c, team2=t2c, map_name=m, map_order=order,
            team1_won=1 if s1 > s2 else 0, team1_rounds=s1, team2_rounds=s2,
            team1_start_side=(h1[0].get("side") if h1 else None), halves=halves,
        ))
    out.sort(key=lambda g: (g.date, g.map_order))
    return out


def field_ct_advantage(games: list) -> dict:
    """Kentan CT-etu logit-muodossa per kartta + '_all' (kaikki kartat)."""
    won = defaultdict(int)
    played = defaultdict(int)
    for g in games:
        for h in g.halves:
            if h.side == "CT":
                won[h.map_name] += h.won
                played[h.map_name] += h.played
                won["_all"] += h.won
                played["_all"] += h.played
    return {m: logit(won[m] / played[m]) for m in played if played[m] >= 200}


# ---------------------------------------------------------------------------
# Kartan voittotodennakoisyys kierrostodennakoisyyksista (tarkka DP)
# ---------------------------------------------------------------------------

def _overtime_win(p_a: float, p_b: float) -> float:
    """MR3-jatkoaika: 3 kierrosta puolella A (p_a), 3 puolella B (p_b),
    ensimmainen 4:aan voittaa, 3-3 -> uusi jatkoaika (sama rakenne)."""
    dist = {(0, 0): 1.0}
    win = lose = tie = 0.0
    for i in range(6):
        p = p_a if i < 3 else p_b
        nxt = defaultdict(float)
        for (x, y), pr in dist.items():
            for dx, q in ((1, p), (0, 1 - p)):
                nx, ny = x + dx, y + (1 - dx)
                if nx == 4:
                    win += pr * q
                elif ny == 4:
                    lose += pr * q
                else:
                    nxt[(nx, ny)] += pr * q
        dist = nxt
    tie = sum(dist.values())
    return win / (win + lose) if (win + lose) > 0 else 0.5


@lru_cache(maxsize=200_000)
def _map_win_cached(p1: float, p2: float) -> float:
    # Varsinainen peliaika: 12 kierrosta puolella 1 (p1), sitten puolella 2 (p2).
    dist = {(0, 0): 1.0}
    win = 0.0
    tie_mass = 0.0
    for i in range(2 * REGULATION_HALF):
        p = p1 if i < REGULATION_HALF else p2
        nxt = defaultdict(float)
        for (x, y), pr in dist.items():
            for dx, q in ((1, p), (0, 1 - p)):
                nx, ny = x + dx, y + (1 - dx)
                if nx == WIN_ROUNDS:
                    win += pr * q
                elif ny == WIN_ROUNDS:
                    pass
                else:
                    nxt[(nx, ny)] += pr * q
        dist = nxt
    tie_mass = sum(v for (x, y), v in dist.items() if x == y == REGULATION_HALF)
    # Jatkoajalla puolet vaihtuvat: ensin viimeisen puoliskon puoli.
    return win + tie_mass * _overtime_win(p2, p1)


def map_win_prob(p_first_half: float, p_second_half: float) -> float:
    return _map_win_cached(round(p_first_half, 4), round(p_second_half, 4))


def series_win_prob(p_map: float, best_of: int) -> float:
    need = best_of // 2 + 1
    # P(joukkue voittaa 'need' karttaa ennen kuin vastustaja voittaa 'need')
    total = 0.0
    for losses in range(need):
        total += math.comb(need - 1 + losses, losses) * (p_map ** need) * ((1 - p_map) ** losses)
    return total


# ---------------------------------------------------------------------------
# Malli
# ---------------------------------------------------------------------------

class XRModel:
    def __init__(self, k: float, ct_adv: dict, tau: float = 1.0):
        self.k = k
        self.ct_adv = ct_adv
        self.tau = tau
        self.r = defaultdict(float)
        self.n_maps = defaultdict(int)

    def _c(self, map_name: str | None) -> float:
        return self.ct_adv.get(map_name, self.ct_adv.get("_all", 0.0))

    def p_round(self, team: str, opp: str, side: str, map_name: str | None) -> float:
        c = self._c(map_name)
        return sigmoid((self.r[team] - self.r[opp]) / SCALE + (c if side == "CT" else -c))

    def expected_rounds(self, team: str, opp: str, map_name: str | None, played_per_side: int = REGULATION_HALF) -> float:
        """xR: odotettu kierrosmaara 12 CT- ja 12 T-kierroksesta (ei jatkoaikaa)."""
        return played_per_side * (self.p_round(team, opp, "CT", map_name) + self.p_round(team, opp, "T", map_name))

    def p_map(self, team: str, opp: str, map_name: str | None, start_side: str | None = None) -> float:
        p_ct = self.p_round(team, opp, "CT", map_name)
        p_t = self.p_round(team, opp, "T", map_name)
        if start_side == "CT":
            raw = map_win_prob(p_ct, p_t)
        elif start_side == "T":
            raw = map_win_prob(p_t, p_ct)
        else:
            raw = 0.5 * (map_win_prob(p_ct, p_t) + map_win_prob(p_t, p_ct))
        return sigmoid(self.tau * logit(raw))

    def p_series(self, team: str, opp: str, best_of: int) -> float:
        # Ennen ottelua karttoja ei tiedeta -> kentan keskimaarainen CT-etu.
        return series_win_prob(self.p_map(team, opp, None), best_of)

    def half_log_loss_terms(self, h: Half) -> tuple:
        """(binomi-log-loss summa, kierrosten maara) tälle puoliskolle ennen paivitysta."""
        p = min(max(self.p_round(h.team, h.opponent, h.side, h.map_name), 1e-9), 1 - 1e-9)
        return -(h.won * math.log(p) + (h.played - h.won) * math.log(1 - p)), h.played

    def update_game(self, g: MapGame) -> None:
        # Lasketaan kaikki puoliskojen odotukset ENNEN paivityksia (symmetrinen).
        deltas = defaultdict(float)
        for h in g.halves:
            p = self.p_round(h.team, h.opponent, h.side, h.map_name)
            deltas[h.team] += self.k * (h.won - h.played * p)
        for team, d in deltas.items():
            self.r[team] += d
            self.n_maps[team] += 1

    def rounds_above_expected(self, g: MapGame) -> dict:
        """Toteutunut - odotettu kierrosmaara varsinaisella peliajalla, per joukkue."""
        out = defaultdict(float)
        for h in g.halves:
            out[h.team] += h.won - h.played * self.p_round(h.team, h.opponent, h.side, h.map_name)
        return dict(out)
