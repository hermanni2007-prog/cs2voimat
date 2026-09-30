"""
Karttatason Elo (tuotantomalli 2026-09-28 alkaen) samoja oikeita kertoimia
vastaan kuin backtest_vs_real_odds.py (vanha sarjatason Elo). Avoin tehtava
CLAUDE.md:sta, ajettu 2026-09-30.

Walk-forward: jokaiselle vedolle kaytetaan VAIN ottelun aikaleimaa edeltavia
sarjoja ja karttoja, ja kalibrointi (tau) sovitetaan VAIN niilla. Sarjan
pituus (Bo1/Bo3/Bo5) luetaan saman ottelun rivista datassa (vain formaatti,
ei tulosta), oletus Bo3 jos ottelua ei loydy.

Paatossaanto on sama kuin vanhassa skriptissa: panostetaan 1 yksikko jokaiseen
puoleen jonka EV > 0; jos vain yksi kerroin tunnetaan ja sen EV <= 0,
vastapuolen kerroin arvioidaan ASSUMED_MARGIN-oletuksella. Tarkan tuloksen
vedot eivat ole mukana. Molemmat mallit ajetaan rinnakkain samoilla vedoilla.
Ei korjauksia (online/ruostuminen/vasymys), kuten vanhassakaan testissa.

TULOS 2026-09-30 (75 kerroinriviä, 83 ottelua joissa molemmat kertoimet):
  +EV-vedot: vanha 77 vetoa +11.72 yks. (+15.2 %), kartta 83 vetoa +1.01 (+1.2 %).
  Bootstrap 95 % valit: vanha [-14, +40], kartta [-26, +31] -> ero on kohinaa.
  Log loss: vanha 0.6824, kartta 0.6888, markkina 0.6594.
  Ero vanha-kartta -0.0064, 95 % vali [-0.031, +0.019], P(kartta parempi)=0.31.
  -> Kertoimilla karttatason Elo EI nayta etua, mutta otos on pieni. Rolling
  origin (702 sarjaa) suosi karttamallia lievasti; molemmat nayttavat heikoilta.
  Markkina on selvasti kumpaakin mallia tarkempi.
"""
from __future__ import annotations

import math
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from backtest import (  # noqa: E402
    EloModel,
    deduplicate_matches,
    load_best_elo_params,
    load_clean_matches,
    production_k_override,
    remove_margin,
)
from backtest_vs_real_odds import ASSUMED_MARGIN, BETS, MIN_GAMES_FOR_RELIABLE  # noqa: E402
from db import get_connection  # noqa: E402
from run_format_map_model import (  # noqa: E402
    MATCH_WINDOW, PROD_MAP_K, SCALE, attach_maps, cal, fit_tau, series_shape, walk,
)
from team_names import load_top50_names  # noqa: E402
from xr_model import load_map_games, series_win_prob  # noqa: E402


def best_of_for(bet, matches):
    d = datetime.fromisoformat(bet["date"])
    pair = {bet["team"], bet["opponent"]}
    for m in matches:
        if {m.team, m.opponent} == pair and abs(m.date - d) <= MATCH_WINDOW:
            sh = series_shape(m)
            if sh:
                return sh[0], True
    return 3, False


def old_prob(bet, prior, top, params):
    elo = EloModel(scale=params["scale"], k_factor=params["k_factor"], half_life_days=params["half_life_days"])
    for m in prior:
        elo.update(m.team, m.opponent, m.team_won, m.date,
                   k_override=production_k_override(params["k_factor"], m.match_type, m.team, m.opponent, top))
    d = datetime.fromisoformat(bet["date"])
    n = min(elo.games_played.get(bet["team"], 0), elo.games_played.get(bet["opponent"], 0))
    return elo.predict(bet["team"], bet["opponent"], d), n


def map_prob(bet, prior, prior_games, top, bo):
    state = {}
    series, _ = walk(prior, attach_maps(prior, prior_games), top, PROD_MAP_K, 0.0, state=state)
    tau = fit_tau([(p, y) for p, _, y, _ in series])
    r = state["r"]
    p_map = 1 / (1 + math.exp(-(r[bet["team"]] - r[bet["opponent"]]) / SCALE))
    return cal(series_win_prob(p_map, bo), tau)


def value_bets(bet, p_team):
    """-> lista (kuvaus, ev, voitto, voittiko, arvio) samalla saannolla kuin vanha skripti."""
    out = []
    if bet.get("opponent_price") is not None:
        for side, price, p in ((bet["team"], bet["price"], p_team),
                               (bet["opponent"], bet["opponent_price"], 1 - p_team)):
            ev = p * price - 1
            if ev > 0:
                won = bet["actual_winner"] == side
                out.append((f"{side}@{price}", ev, (price - 1) if won else -1.0, won, False))
        return out
    if "oikea tulos" in bet["bet_type"]:
        return out
    side = bet["bet_on"]
    p = p_team if side == bet["team"] else 1 - p_team
    ev = p * bet["price"] - 1
    if ev > 0:
        won = bet["actual_winner"] == side
        out.append((f"{side}@{bet['price']}", ev, (bet["price"] - 1) if won else -1.0, won, False))
    else:
        other = bet["opponent"] if side == bet["team"] else bet["team"]
        price_o = 1 / max(ASSUMED_MARGIN - 1 / bet["price"], 0.01)
        ev_o = (1 - p) * price_o - 1
        if ev_o > 0:
            won = bet["actual_winner"] == other
            out.append((f"{other}@{price_o:.2f} (arvio)", ev_o, (price_o - 1) if won else -1.0, won, True))
    return out


def summary(name, rows):
    n = len(rows)
    if not n:
        print(f"{name}: ei yhtaan +EV-vetoa")
        return
    profit = sum(r[2] for r in rows)
    ev = sum(r[1] for r in rows)
    wins = sum(1 for r in rows if r[3])
    print(f"{name}: {n} vetoa, {wins} voittoa, odotusarvo {ev:+.2f} ({ev / n:+.1%}/veto), "
          f"toteutunut {profit:+.2f} ({profit / n:+.1%}/veto)")


def log_loss_rows(rows):
    return sum(-math.log(p if y else 1 - p) for p, y in rows) / len(rows)


def main() -> int:
    conn = get_connection()
    top = load_top50_names()
    matches = deduplicate_matches(load_clean_matches(conn))
    games = load_map_games(conn, top)
    conn.close()
    params = load_best_elo_params()
    print(f"Karttatason Elo K={PROD_MAP_K}, scale={SCALE:.0f}; vanha Elo k={params['k_factor']}\n")

    res = {"vanha": [], "kartta": []}
    ll = {"vanha": [], "kartta": [], "markkina": []}
    seen_matches = set()
    print(f"{'pvm':10} {'ottelu':32} {'bo':>3} {'vanha':>6} {'kartta':>6} {'mark.':>6}  voittaja")
    for bet in BETS:
        d = datetime.fromisoformat(bet["date"])
        prior = [m for m in matches if m.date < d]
        prior_games = [g for g in games if g.date < d]
        bo, found = best_of_for(bet, matches)
        p_old, n_min = old_prob(bet, prior, top, params)
        p_map = map_prob(bet, prior, prior_games, top, bo)
        p_mkt = remove_margin(bet["price"], bet["opponent_price"])[0] if bet.get("opponent_price") else None
        y = int(bet["actual_winner"] == bet["team"])
        mk = f"{p_mkt:6.3f}" if p_mkt is not None else "     -"
        print(f"{bet['date'][:10]} {bet['team'] + ' - ' + bet['opponent']:32.32} {bo:>2}{'' if found else '?'} "
              f"{p_old:6.3f} {p_map:6.3f} {mk}  {bet['actual_winner']}")
        for key, p in (("vanha", p_old), ("kartta", p_map)):
            res[key] += [row + (n_min,) for row in value_bets(bet, p)]
        # Log loss kerran per ottelu (sama ottelu voi olla usealla kirjanpitajalla).
        mkey = (frozenset((bet["team"], bet["opponent"])), bet["date"][:13])
        if p_mkt is not None and mkey not in seen_matches:
            seen_matches.add(mkey)
            ll["vanha"].append((p_old, y))
            ll["kartta"].append((p_map, y))
            ll["markkina"].append((p_mkt, y))

    print("\n=== 'Panosta kun EV > 0', 1 yksikko/veto ===")
    for key, label in (("vanha", "Vanha sarjatason Elo"), ("kartta", "Karttatason Elo    ")):
        summary(label, res[key])
    print(f"\n=== Sama ilman ohutdataisia (alle {MIN_GAMES_FOR_RELIABLE} ottelua) ===")
    for key, label in (("vanha", "Vanha sarjatason Elo"), ("kartta", "Karttatason Elo    ")):
        summary(label, [r for r in res[key] if r[5] >= MIN_GAMES_FOR_RELIABLE])
    print("\n=== Vain oikeat havaitut kertoimet (ei '(arvio)'-vastapuolia) ===")
    for key, label in (("vanha", "Vanha sarjatason Elo"), ("kartta", "Karttatason Elo    ")):
        summary(label, [r for r in res[key] if not r[4]])

    n = len(ll["markkina"])
    print(f"\n=== Log loss, ottelut joissa molemmat kertoimet tunnettu (n={n}, yksi rivi per ottelu) ===")
    for key in ("vanha", "kartta", "markkina"):
        print(f"  {key:9} {log_loss_rows(ll[key]):.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
