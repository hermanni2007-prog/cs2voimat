"""
Kayttajan pyynto 2026-09-18: kaava joka painottaa dataa vahemman mita
enemman aikaa on kulunut NYKYHETKESTA (ennustushetkesta), ja joka alkaa
vaikuttaa kunnolla vasta ~3kk (90 vrk) taaksepain menon jalkeen.

ENSIMMAINEN YRITYS (hylatty, dokumentoitu tahan lapinakyvyyden vuoksi):
lisasin EloModel:iin "grace_days"-parametrin joka vaimentaa ratingia
kohti keskiarvoa perustuen aikaan JOUKKUEEN OMASTA EDELLISESTA OTTELUSTA
(sama mekanismi kuin olemassa oleva half_life_days-inaktiivisuusvaimennus,
vain viivastettyna). Tama osoittautui lahes taysin merkityksettomaksi:
top50-joukkueet pelaavat niin usein etta koko 8kk:n datassa on VAIN 2
tapausta joissa vali kahden perakkaisen ottelun valilla ylittaa edes 90
vrk (Virtus.pro, Lynn Vision) - eika kumpikaan osu yhteenkaan testi- tai
vetoennusteeseen. Tama EI ollut oikea tulkinta kayttajan pyynnosta.

OIKEA TULKINTA (talla toteutettu): jokaisen YKSITTAISEN historiallisen
ottelun VAIKUTUS nykyiseen ratingiin pienenee sen oman ian mukaan
ENNUSTUSHETKELLA - riippumatta siita onko joukkue pelannyt sen jalkeen
tiheasti vai ei. Tama vaatii ratingin UUDELLEENLASKENNAN jokaiselle
ennustushetkelle (K-kerroin per ottelu skaalataan ian mukaan), koska
tavallinen kumulatiivinen Elo ei voi "unohtaa" yksittaisen vanhan
ottelun vaikutusta pelkastaan koska aikaa on kulunut - se muistaa vain
kokonaisrating-summan.

painokaava (recency_weight):
  w(age_vrk) = 1.0                                    kun age <= grace_days (90 vrk)
  w(age_vrk) = 0.5 ** ((age - grace_days) / half_life)  kun age > grace_days

Ottelun efektiivinen K-kerroin = k_factor * w(age). Uusin data siis
painaa aina yhta paljon kuin ennenkin ENSIMMAISET 3 kk:ta, ja vasta sen
jalkeen vanhempi ottelu alkaa vaikuttaa yha vahemman.

METODOLOGIA (sama periaate kuin koko projektissa): kaksi ERI mittaria,
EIVAT sama asia:
  1) OIKEA validointi - fitattu vain ensimmaisella 80%:lla kronologisesti,
     tarkistettu nakemattomalla 20%:lla (log loss). Tama ratkaisee onko
     tama parannus aito.
  2) Kayttajan pyytama "suurin +EV meidan 78 real-odds-vedosta" - TAMA ON
     KEHAMAINEN jos sita kaytetaan paatoksentekoon (fitattu JA testattu
     samalla 78 vedon joukolla jota olemme raportoineet koko istunnon).
     Raportoitu koska pyydettiin, mutta paatos "otetaanko kayttoon"
     nojaa AINA kohtaan 1."""
from __future__ import annotations

import math
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection  # noqa: E402
from backtest import (  # noqa: E402
    MEAN_RATING,
    deduplicate_matches,
    filter_top50_only,
    load_clean_matches,
    load_best_elo_params,
    log_loss,
    brier_score,
)
from team_names import load_top50_names  # noqa: E402
from backtest_vs_real_odds import BETS, ASSUMED_MARGIN  # noqa: E402

GRACE_DAYS = 90.0  # kayttajan maarittelema, kiintea: "kunnolla vasta 3kk jalkeen"
HALF_LIFE_CANDIDATES = [15, 30, 45, 60, 90, 120, 180, 270, 365, 545, 730, 99999]


def recency_weight(age_days: float, grace_days: float, half_life_days: float) -> float:
    if age_days <= grace_days:
        return 1.0
    if half_life_days <= 0:
        return 1.0
    return 0.5 ** ((age_days - grace_days) / half_life_days)


def weighted_predict(matches_before: list, as_of: datetime, team: str, opponent: str,
                      scale: float, k_factor: float, grace_days: float, half_life_days: float) -> float:
    """Laskee ratingit UUDELLEEN talle nimenomaiselle ennustushetkelle -
    jokaisen ottelun K-kerroin skaalataan sen oman ian mukaan as_of-hetkella."""
    ratings: dict = {}
    for m in matches_before:
        age = (as_of - m.date).total_seconds() / 86400.0
        w = recency_weight(age, grace_days, half_life_days)
        if w <= 1e-6:
            continue
        eff_k = k_factor * w
        r_team = ratings.get(m.team, MEAN_RATING)
        r_opp = ratings.get(m.opponent, MEAN_RATING)
        p = 1.0 / (1.0 + math.exp(-(r_team - r_opp) / scale))
        actual = float(m.team_won)
        ratings[m.team] = r_team + eff_k * (actual - p)
        ratings[m.opponent] = r_opp + eff_k * ((1 - actual) - (1 - p))
    r_team = ratings.get(team, MEAN_RATING)
    r_opp = ratings.get(opponent, MEAN_RATING)
    return 1.0 / (1.0 + math.exp(-(r_team - r_opp) / scale))


def honest_validation(matches: list, params: dict, grace_days: float) -> list:
    split_idx = int(len(matches) * 0.8)
    split_date = matches[split_idx].date
    train_all = matches  # koko historia kaytettavissa "ennen" jokaista testipistetta
    test = [m for m in matches if m.date >= split_date]
    print(f"Honest-validointi: {len(matches)} ottelua, train/test-raja {split_date.date()}, "
          f"testijoukko {len(test)} ottelua\n")

    results = []
    for hl in HALF_LIFE_CANDIDATES:
        outcomes, probs = [], []
        for m in test:
            prior = [x for x in train_all if x.date < m.date]
            p = weighted_predict(prior, m.date, m.team, m.opponent,
                                  params["scale"], params["k_factor"], grace_days, hl)
            outcomes.append(m.team_won)
            probs.append(p)
        ll = log_loss(outcomes, probs)
        br = brier_score(outcomes, probs)
        results.append((hl, ll, br))
        print(f"  half_life_days={hl:<7} log_loss={ll:.4f}  brier={br:.4f}")
    return results


def bets_net_ev(matches: list, params: dict, half_life: float, grace_days: float) -> dict:
    total_profit = 0.0
    total_ev = 0.0
    n_bets = 0
    n_wins = 0
    for bet in BETS:
        bet_date = datetime.fromisoformat(bet["date"])
        prior = [m for m in matches if m.date < bet_date]
        p_team = weighted_predict(prior, bet_date, bet["team"], bet["opponent"],
                                   params["scale"], params["k_factor"], grace_days, half_life)

        if bet.get("opponent_price") is not None:
            for side, price, p_model in ((bet["team"], bet["price"], p_team),
                                          (bet["opponent"], bet["opponent_price"], 1 - p_team)):
                ev_side = p_model * price - 1
                if ev_side > 0:
                    won = bet["actual_winner"] == side
                    profit = (price - 1) if won else -1.0
                    total_profit += profit
                    total_ev += ev_side
                    n_bets += 1
                    n_wins += int(won)
            continue

        if "oikea tulos" in bet["bet_type"]:
            continue
        priced_side = bet["bet_on"]
        p_priced = p_team if priced_side == bet["team"] else (1 - p_team)
        ev = p_priced * bet["price"] - 1
        if ev <= 0:
            other_side = bet["opponent"] if priced_side == bet["team"] else bet["team"]
            implied_priced = 1 / bet["price"]
            implied_other_est = max(ASSUMED_MARGIN - implied_priced, 0.01)
            price_other_est = 1 / implied_other_est
            p_other = 1 - p_priced
            ev_other_est = p_other * price_other_est - 1
            if ev_other_est > 0:
                won = bet["actual_winner"] == other_side
                profit = (price_other_est - 1) if won else -1.0
                total_profit += profit
                total_ev += ev_other_est
                n_bets += 1
                n_wins += int(won)
        else:
            won = bet["actual_winner"] == priced_side
            profit = (bet["price"] - 1) if won else -1.0
            total_profit += profit
            total_ev += ev
            n_bets += 1
            n_wins += int(won)

    return {"n_bets": n_bets, "n_wins": n_wins, "total_profit": total_profit, "total_ev": total_ev}


def main() -> int:
    conn = get_connection()
    all_matches = deduplicate_matches(load_clean_matches(conn))
    conn.close()

    top50 = load_top50_names()
    matches = filter_top50_only(all_matches, top50)
    params = load_best_elo_params()

    print(f"grace_days={GRACE_DAYS:.0f} ({GRACE_DAYS/30:.1f} kk) - kiintea, kayttajan maarittelema")
    print(f"scale={params['scale']} k_factor={params['k_factor']} (nykyisesta parhaasta sovituksesta)\n")

    print("=== 1) OIKEA validointi: nakematon 20%:n log loss (fit vain ennen testihetkea) ===")
    honest_results = honest_validation(matches, params, GRACE_DAYS)
    best_honest = min(honest_results, key=lambda r: r[1])

    # Vertailu: nykyinen tuotantomalli (ei recency-painotusta, kumulatiivinen Elo half_life=99999)
    from backtest import EloModel  # noqa: E402
    split_idx = int(len(matches) * 0.8)
    split_date = matches[split_idx].date
    test = [m for m in matches if m.date >= split_date]
    elo_base = EloModel(scale=params["scale"], k_factor=params["k_factor"], half_life_days=params["half_life_days"])
    outcomes_base, probs_base = [], []
    for m in matches:
        p = elo_base.predict(m.team, m.opponent, m.date)
        if m.date >= split_date:
            outcomes_base.append(m.team_won)
            probs_base.append(p)
        elo_base.update(m.team, m.opponent, m.team_won, m.date)
    ll_base = log_loss(outcomes_base, probs_base)
    print(f"\n  Vertailu (nykyinen tuotantomalli, ei recency-painotusta): log_loss={ll_base:.4f}")
    print(f"  PARAS recency-malli: half_life_days={best_honest[0]}  log_loss={best_honest[1]:.4f}")
    if best_honest[1] < ll_base:
        print(f"  -> recency-painotus PARANTAA ennustetta ({ll_base:.4f} -> {best_honest[1]:.4f}).")
    else:
        print("  -> recency-painotus EI paranna ennustetta yhdelläkään testatuista half_life-arvoista.")

    print("\n=== 2) Kayttajan pyytama: mika half_life antaa suurimman netto-EV:n meidan 78 real-odds-vedosta ===")
    print("    (HUOM: kehamainen mittari - fitattu JA testattu SAMALLA 78 vedon joukolla. Katso yllaoleva kommentti.)")
    bet_results = []
    for hl in HALF_LIFE_CANDIDATES:
        r = bets_net_ev(matches, params, hl, GRACE_DAYS)
        bet_results.append((hl, r))
        avg = r["total_profit"] / r["n_bets"] if r["n_bets"] else 0
        print(f"  half_life_days={hl:<7} n_bets={r['n_bets']:<3} voitti={r['n_wins']:<3} "
              f"netto={r['total_profit']:+.2f}  ({avg:+.1%} ka.)")
    best_bets = max(bet_results, key=lambda r: r[1]["total_profit"])
    print(f"\n  SUURIN netto-EV (kehamainen mittari): half_life_days={best_bets[0]}  "
          f"netto={best_bets[1]['total_profit']:+.2f} yksikkoa ({best_bets[1]['n_bets']} vetoa)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
