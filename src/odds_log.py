"""
data/odds_log.json: kaikki kayttajan nayttamat kertoimet (kyselyt ja lyodyt
vedot) kerroin-backtestia varten. Lisatty 2026-09-30 kayttajan pyynnosta:
jokainen kerroinkysely kirjataan, jotta mallia voidaan testata oikeaa
markkinaa vastaan jatkuvasti (ks. CLAUDE.md kohta 6).

Rivin kentat: logged_utc, match_date_utc, tournament, team, opponent,
price (team-puolen Money Line), opponent_price (null jos ei tiedossa),
source (user_query / user_bet), note.

Tulos ja ottelun tarkka aika haetaan historical_matches-datasta: rivi tulee
testiin vasta, kun CI on kerannyt ottelun. Hakuehto: sama joukkuepari ja
aikaero enintaan 36 h (aikataulut siirtyvat). Kasin ei lisata mitaan tulosta.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from team_names import load_top50_names, resolve_to_canonical

ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = ROOT / "data" / "odds_log.json"
WINDOW = timedelta(hours=36)


def _dt(s: str) -> datetime:
    d = datetime.fromisoformat(s if "T" in s else s + "T12:00:00+00:00")
    return d


def load_logged_bets(matches, path: Path = LOG_PATH):
    """-> (bets, pending): bets samassa muodossa kuin backtest_vs_real_odds.BETS,
    pending = rivit joiden tulosta ei viela ole datassa."""
    if not path.exists():
        return [], []
    names = load_top50_names()
    bets, pending = [], []
    for e in json.loads(path.read_text(encoding="utf-8")):
        team = resolve_to_canonical(e["team"], names)
        opp = resolve_to_canonical(e["opponent"], names) if e.get("opponent") else None
        d = _dt(e["match_date_utc"])
        # Ottelu voi olla datassa vain vastustajan nakokulmasta (esim. jos
        # joukkueen oma sivu puuttuu), joten kelpuutetaan kumpikin suunta.
        # Ehdokkaat kummastakin nakokulmasta; peilirivien aika voi erota
        # minuutteja, joten alle 4 h valein olevat rivit ovat sama ottelu.
        rows = []
        for m in matches:
            if abs(m.date - d) > WINDOW:
                continue
            if m.team == team and (opp is None or m.opponent == opp):
                rows.append((m.date, m.opponent, m.team_won))
            elif m.opponent == team and (opp is None or m.team == opp):
                rows.append((m.date, m.team, not m.team_won))
        games = []
        for r in sorted(rows):
            if games and r[1] == games[-1][0][1] and r[0] - games[-1][-1][0] <= timedelta(hours=4):
                games[-1].append(r)
            else:
                games.append([r])
        # Yksi ottelu 36 h sisalla, tai lahin alle 6 h paassa ja muut yli 12 h paassa.
        games.sort(key=lambda g: abs(g[0][0] - d))
        ok = len(games) == 1 or (len(games) > 1 and abs(games[0][0][0] - d) <= timedelta(hours=6)
                                 and abs(games[1][0][0] - d) > timedelta(hours=12))
        if not ok or len({r[2] for r in games[0]}) > 1:
            pending.append(e)
            continue
        date, opp_found, team_won = games[0][0]
        bet = {"date": date.isoformat(), "team": team, "opponent": opp_found, "price": e["price"],
               "actual_winner": team if team_won else opp_found,
               "bet_type": f"voittaja (odds_log: {e['source']})"}
        if e.get("opponent_price"):
            bet.update(opponent_price=e["opponent_price"], bet_on=None)
        else:
            bet.update(bet_on=team)
        bets.append(bet)
    return bets, pending
