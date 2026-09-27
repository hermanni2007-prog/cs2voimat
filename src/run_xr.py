"""
xR-mallin (src/xr_model.py) ei-kehamainen validointi.

Kaikki vapaat parametrit sovitetaan VAIN train-jaksolla (ensimmaiset 80 %
top75-vs-top75 -kartoista aikajarjestyksessa):
  - c_m (kentan CT-etu per kartta): train-kartoista
  - K (kierrosratingin paivitysaskel): grid, train-puoliskojen binomi-log-loss
  - tau (karttatodennakoisyyden kalibrointi kierrosriippuvuuden vuoksi):
    grid, train-karttojen log loss
Testijaksolla raportoidaan kolme tasoa:
  1) kierrokset: log loss / kierros vs perustaso (pelkka kentan CT/T-osuus)
  2) kartat: log loss vs 50/50
  3) SARJAT: log loss vs NYKYINEN TUOTANTO-Elo, tasmalleen samat sarjat -
     tama ratkaisee, kannattaako xR ottaa kayttoon.

HUOM: aja uudelleen kun karttakeruun jono on purkautunut (2026-09-28 viela
~447 turnausta jonossa) - osittainen kattavuus painottaa jo haettuja turnauksia.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from datetime import datetime, timedelta  # noqa: E402

from backtest import (  # noqa: E402
    EloModel,
    deduplicate_matches,
    load_best_elo_params,
    load_clean_matches,
    log_loss,
    production_k_override,
)
from db import get_connection  # noqa: E402
from team_names import load_top50_names  # noqa: E402
from xr_model import XRModel, field_ct_advantage, load_map_games  # noqa: E402

K_GRID = [0.5, 1, 2, 3, 4, 6, 8, 12, 16]
TAU_GRID = [round(0.1 + 0.05 * i, 2) for i in range(29)]  # 0.1 .. 1.5
# Sarja ennustetaan ennen KAIKKIA karttoja jotka ovat alle 6 h ennen sen
# aikaleimaa - bracket-sivun ja /Matches-taulukon aikaleimat voivat erota
# minuutteja, eika sarjan omien karttojen saa antaa vuotaa ennusteeseen.
LEAK_BUFFER_HOURS = 6
MIN_MAPS_HISTORY = 10  # sarjavertailuun vain joukkueet joilla riittavasti xR-historiaa


def walk(games, k, ct_adv, split_date, top, tau=1.0, series_events=None):
    """Kay kartat aikajarjestyksessa: ennusta ensin, paivita sitten.
    Palauttaa train/test-metriikat ja sarjaennusteet (jos series_events annettu)."""
    m = XRModel(k, ct_adv, tau)
    tr_ll = tr_n = te_ll = te_n = te_base_ll = 0.0
    tr_maps, te_maps = [], []
    series_preds = {}
    ev = sorted(series_events or [], key=lambda e: e["date"])
    ei = 0
    base_c = ct_adv
    import math
    for g in games:
        # Sarjaennusteet tehdaan ennen saman hetken karttojen paivitysta.
        while ei < len(ev) and ev[ei]["date"] <= g.date + timedelta(hours=LEAK_BUFFER_HOURS):
            e = ev[ei]
            if m.n_maps[e["team"]] >= MIN_MAPS_HISTORY and m.n_maps[e["opp"]] >= MIN_MAPS_HISTORY:
                series_preds[e["key"]] = m.p_series(e["team"], e["opp"], e["best_of"])
            ei += 1
        both_top = g.team1 in top and g.team2 in top
        is_test = g.date >= split_date
        for h in g.halves:
            ll, n = m.half_log_loss_terms(h)
            if not is_test:
                tr_ll += ll
                tr_n += n
            elif both_top:
                te_ll += ll
                te_n += n
                c = base_c.get(h.map_name, base_c.get("_all", 0.0))
                p0 = 1 / (1 + math.exp(-(c if h.side == "CT" else -c)))
                te_base_ll += -(h.won * math.log(p0) + (h.played - h.won) * math.log(1 - p0))
        if both_top and g.halves:
            rec = (m.p_map(g.team1, g.team2, g.map_name, g.team1_start_side), g.team1_won)
            (te_maps if is_test else tr_maps).append(rec)
        m.update_game(g)
    return {
        "train_round_ll": tr_ll / tr_n if tr_n else float("nan"),
        "test_round_ll": te_ll / te_n if te_n else float("nan"),
        "test_round_base_ll": te_base_ll / te_n if te_n else float("nan"),
        "test_rounds": te_n,
        "train_maps": tr_maps,
        "test_maps": te_maps,
        "series_preds": series_preds,
        "model": m,
    }


def load_series(conn, top):
    """Kaikki top75-vs-top75 -sarjat (train + test), formaatti johdettuna tuloksesta."""
    rows = conn.execute(
        "SELECT match_date_utc, team, opponent, score_team, score_opponent FROM historical_matches "
        "WHERE score_team IS NOT NULL AND score_opponent IS NOT NULL AND score_team != score_opponent"
    ).fetchall()
    seen, out = set(), []
    for d, t, o, st, so in rows:
        date = datetime.fromisoformat(d)
        key = (d, frozenset((t, o)))
        if key in seen or t not in top or o not in top:
            continue
        mx = max(st, so)
        best_of = 3 if mx == 2 else 5 if mx == 3 else 1 if mx >= 13 else None
        if best_of is None:
            continue
        seen.add(key)
        out.append({"date": date, "team": t, "opp": o, "best_of": best_of, "y": 1 if st > so else 0, "key": key})
    return out


def production_elo_preds(conn, top, keys):
    params = load_best_elo_params()
    elo = EloModel(scale=params["scale"], k_factor=params["k_factor"], half_life_days=params["half_life_days"])
    out = {}
    for mr in deduplicate_matches(load_clean_matches(conn)):
        key = (mr.date.isoformat(), frozenset((mr.team, mr.opponent)))
        if key in keys:
            out[key] = (elo.predict(mr.team, mr.opponent, mr.date), mr.team)
        elo.update(mr.team, mr.opponent, mr.team_won, mr.date,
                   k_override=production_k_override(params["k_factor"], mr.match_type, mr.team, mr.opponent, top))
    return out


def main() -> int:
    conn = get_connection()
    top = load_top50_names()
    games = load_map_games(conn, top)
    top_games = [g for g in games if g.team1 in top and g.team2 in top and g.halves]
    split_date = top_games[int(len(top_games) * 0.8)].date
    train_games = [g for g in games if g.date < split_date]
    ct_adv = field_ct_advantage(train_games)
    print(f"Kartat: {len(games)} (top75-vs-top75 {len(top_games)}), train/test-raja {split_date.date()}")
    print("Kentan CT-etu (train):", {k: round(1 / (1 + 2.718281828 ** -v), 3) for k, v in sorted(ct_adv.items())})

    # 1) K train-kierroksilla
    print("\n=== K-haku (train, log loss / kierros) ===")
    best_k = None
    for k in K_GRID:
        r = walk(games, k, ct_adv, split_date, top)
        print(f"  K={k:5}  train_round_ll={r['train_round_ll']:.5f}")
        if best_k is None or r["train_round_ll"] < best_k[1]:
            best_k = (k, r["train_round_ll"])
    k = best_k[0]

    # 2) tau train-kartoilla
    base = walk(games, k, ct_adv, split_date, top, tau=1.0)
    raw_train = base["train_maps"]
    import math

    def cal(p, t):
        p = min(max(p, 1e-9), 1 - 1e-9)
        return 1 / (1 + math.exp(-t * math.log(p / (1 - p))))

    best_tau = min(TAU_GRID, key=lambda t: log_loss([y for _, y in raw_train], [cal(p, t) for p, _ in raw_train]))
    print(f"\nValittu (train): K={k}, tau={best_tau}")

    # 3) Sarjat: molemmille malleille ennusteet koko aikajaksolta
    series = load_series(conn, top)
    prod = production_elo_preds(conn, top, {s["key"] for s in series})
    conn.close()
    final = walk(games, k, ct_adv, split_date, top, tau=best_tau, series_events=series)

    rows = []  # (p_xr, p_prod, y, is_test) vain sarjoille joilla molemmat ennusteet
    for s in series:
        if s["key"] not in final["series_preds"] or s["key"] not in prod:
            continue
        p_prod, prod_team = prod[s["key"]]
        if prod_team != s["team"]:
            p_prod = 1 - p_prod
        rows.append((final["series_preds"][s["key"]], p_prod, s["y"], s["date"] >= split_date))

    # Reilu vertailu: KUMPIKIN malli saa oman kalibrointikertoimen train-sarjoilla,
    # jottei xR voita pelkalla varovaisuudella (kutistuksella kohti 50 %).
    train_rows = [r for r in rows if not r[3]]
    test_rows = [r for r in rows if r[3]]

    def fit_tau(idx):
        return min(TAU_GRID, key=lambda t: log_loss([r[2] for r in train_rows], [cal(r[idx], t) for r in train_rows]))

    tau_xr_s, tau_prod_s = fit_tau(0), fit_tau(1)

    print("\n=== TESTI (nakematon jakso, top75-vs-top75) ===")
    print(f"1) Kierrokset: xR {final['test_round_ll']:.5f} vs pelkka CT/T-perustaso "
          f"{final['test_round_base_ll']:.5f}  (n={final['test_rounds']:.0f} kierrosta)")
    tm = final["test_maps"]
    print(f"2) Kartat:     xR {log_loss([y for _, y in tm], [p for p, _ in tm]):.4f} vs 50/50 0.6931  (n={len(tm)})")
    if not test_rows:
        print("3) SARJAT: ei vertailukelpoisia sarjoja (liian vahan xR-historiaa).")
        return 0
    y = [r[2] for r in test_rows]
    ll_xr = log_loss(y, [r[0] for r in test_rows])
    ll_prod = log_loss(y, [r[1] for r in test_rows])
    ll_xr_c = log_loss(y, [cal(r[0], tau_xr_s) for r in test_rows])
    ll_prod_c = log_loss(y, [cal(r[1], tau_prod_s) for r in test_rows])
    acc = lambda i: sum((r[i] >= 0.5) == (r[2] == 1) for r in test_rows) / len(test_rows)  # noqa: E731
    print(f"3) SARJAT (samat {len(test_rows)} sarjaa, train-sarjoja kalibrointiin {len(train_rows)}):")
    print(f"     raaka:        xR {ll_xr:.4f}  vs tuotanto-Elo {ll_prod:.4f}")
    print(f"     kalibroitu:   xR {ll_xr_c:.4f} (tau={tau_xr_s})  vs tuotanto-Elo {ll_prod_c:.4f} (tau={tau_prod_s})")
    print(f"     osumatarkkuus (kalibrointi ei vaikuta): xR {acc(0):.3f}  vs tuotanto-Elo {acc(1):.3f}")
    verdict = "xR PARANTAA" if ll_xr_c < ll_prod_c else "xR EI paranna"
    print(f"\n-> {verdict} sarjaennustetta, kun molemmat on kalibroitu reilusti train-datalla.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
