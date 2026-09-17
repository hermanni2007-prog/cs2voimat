"""
Idea #1 kayttajan brainstorm-listalta (2026-09-18): sekoita mallin oma
ennuste markkinan omaan (marginaalipoistettuun) hintaan sen sijaan etta
kaytetaan pelkkaa mallin P:ta sellaisenaan. Standarditekniikka: markkina
on yleensa tehokas, joten malli tuo lisaarvoa vain siina maarin kuin se
NAKEE jotain markkinasta poikkeavaa - ei korvaamalla markkinaa kokonaan.

p_blend(team) = w * p_malli(team) + (1-w) * p_markkina_fair(team)

Data: VAIN ne 90/96 BETS-rivia joissa MOLEMMAT oikeat kertoimet tunnetaan
(opponent_price != None) - naille lasketaan p_markkina_fair
remove_margin()-funktiolla. Yksipuoliset (kayttajan omat Coolbet-vedot,
"tarkka tulos" -vedot) jatetty pois koska niille ei ole puhdasta
kaksipuolista markkinatodennakoisyytta.

METODOLOGIA (sama kuin koko projektissa): kronologinen 80/20-jako, w
haetaan minimoimalla log loss VAIN train-osiolla (80%), raportoidaan
test-osion (20%, NAKEMATON) log loss VALITULLA w:lla. Tama on eri asia
kuin aiemmin hylatty "hae paras arvo suoraan 78 vedon EV:sta" -tapa -
tassa validointi on alusta asti oikein jaettu, ei kehamainen."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection  # noqa: E402
from backtest import (  # noqa: E402
    EloModel,
    deduplicate_matches,
    filter_top50_only,
    load_clean_matches,
    load_best_elo_params,
    log_loss,
    remove_margin,
)
from team_names import load_top50_names  # noqa: E402
from backtest_vs_real_odds import BETS  # noqa: E402

W_CANDIDATES = [round(x * 0.1, 1) for x in range(0, 11)]  # 0.0, 0.1, ..., 1.0


def build_dataset(matches: list, params: dict) -> list:
    """Palauttaa listan {date, p_model, p_market, team_won} - vain
    kaksipuoliset oikeat kertoimet."""
    rows = []
    for bet in BETS:
        if bet.get("opponent_price") is None:
            continue
        bet_date = datetime.fromisoformat(bet["date"])
        prior = [m for m in matches if m.date < bet_date]
        elo = EloModel(scale=params["scale"], k_factor=params["k_factor"], half_life_days=params["half_life_days"])
        for m in prior:
            elo.update(m.team, m.opponent, m.team_won, m.date)
        p_model = elo.predict(bet["team"], bet["opponent"], bet_date)
        p_market, _ = remove_margin(bet["price"], bet["opponent_price"])
        team_won = 1 if bet["actual_winner"] == bet["team"] else 0
        rows.append({"date": bet_date, "p_model": p_model, "p_market": p_market, "team_won": team_won,
                      "team": bet["team"], "opponent": bet["opponent"],
                      "price": bet["price"], "opponent_price": bet["opponent_price"]})
    rows.sort(key=lambda r: r["date"])
    return rows


def ev_profit_for_w(rows: list, w: float) -> dict:
    """Illustratiivinen: paljonko 'panosta jos EV>0' -strategia olisi
    tuottanut TALLA w:lla, TALLA (test-)osiolla. Ei kaytetty w:n valintaan."""
    total_profit = 0.0
    n_bets = 0
    n_wins = 0
    for r in rows:
        p_blend = w * r["p_model"] + (1 - w) * r["p_market"]
        for side, price, p_side, won in (
            ("team", r["price"], p_blend, r["team_won"] == 1),
            ("opponent", r["opponent_price"], 1 - p_blend, r["team_won"] == 0),
        ):
            ev = p_side * price - 1
            if ev > 0:
                profit = (price - 1) if won else -1.0
                total_profit += profit
                n_bets += 1
                n_wins += int(won)
    return {"n_bets": n_bets, "n_wins": n_wins, "profit": total_profit}


def main() -> int:
    conn = get_connection()
    all_matches = deduplicate_matches(load_clean_matches(conn))
    conn.close()
    matches = filter_top50_only(all_matches, load_top50_names())
    params = load_best_elo_params()

    rows = build_dataset(matches, params)
    print(f"Kaksipuolisia real-odds -otteluita yhteensa: {len(rows)}\n")

    split_idx = int(len(rows) * 0.8)
    train = rows[:split_idx]
    test = rows[split_idx:]
    print(f"Train (fit w tassa): {len(train)} ottelua ({train[0]['date'].date()} - {train[-1]['date'].date()})")
    print(f"Test (NAKEMATON, w valittu ennen tata): {len(test)} ottelua "
          f"({test[0]['date'].date()} - {test[-1]['date'].date()})\n")

    print("=== Haetaan paras w TRAIN-osiolla (log loss) ===")
    train_results = []
    for w in W_CANDIDATES:
        outcomes = [r["team_won"] for r in train]
        probs = [w * r["p_model"] + (1 - w) * r["p_market"] for r in train]
        ll = log_loss(outcomes, probs)
        train_results.append((w, ll))
        print(f"  w={w:.1f}  (1.0=pelkka malli, 0.0=pelkka markkina)  train_log_loss={ll:.4f}")
    best_w, best_train_ll = min(train_results, key=lambda r: r[1])
    print(f"\nPARAS w train-datalla: {best_w} (log_loss={best_train_ll:.4f})")

    print(f"\n=== Testataan valittu w={best_w} NAKEMATTOMALLA test-osiolla ===")
    outcomes_test = [r["team_won"] for r in test]
    probs_blend_test = [best_w * r["p_model"] + (1 - best_w) * r["p_market"] for r in test]
    probs_model_test = [r["p_model"] for r in test]
    probs_market_test = [r["p_market"] for r in test]
    ll_blend = log_loss(outcomes_test, probs_blend_test)
    ll_model = log_loss(outcomes_test, probs_model_test)
    ll_market = log_loss(outcomes_test, probs_market_test)
    print(f"  Pelkka malli      (w=1.0): log_loss={ll_model:.4f}")
    print(f"  Pelkka markkina   (w=0.0): log_loss={ll_market:.4f}")
    print(f"  Sekoitus (w={best_w}):      log_loss={ll_blend:.4f}")

    if ll_blend < min(ll_model, ll_market):
        print("  -> Sekoitus VOITTAA seka pelkan mallin etta pelkan markkinan nakemattomalla datalla.")
    elif ll_blend < ll_model:
        print("  -> Sekoitus voittaa pelkan mallin, mutta ei markkinaa - markkina on jo tehokkaampi tallakin otoksella.")
    else:
        print("  -> Sekoitus EI voita edes pelkkaa mallia nakemattomalla datalla - ei aitoa parannusta.")

    print("\n=== Illustratiivinen EV>0-panostulos test-osiolla (EI kaytetty w:n valintaan) ===")
    for label, w in [("Pelkka malli (w=1.0)", 1.0), ("Pelkka markkina (w=0.0)", 0.0), (f"Sekoitus (w={best_w})", best_w)]:
        r = ev_profit_for_w(test, w)
        avg = r["profit"] / r["n_bets"] if r["n_bets"] else 0
        print(f"  {label}: {r['n_bets']} vetoa, {r['n_wins']} voitti, netto={r['profit']:+.2f} ({avg:+.1%} ka.)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
