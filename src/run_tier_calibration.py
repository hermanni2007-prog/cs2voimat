"""
Kayttajan kysymys 2026-09-18: "top 70 tiimit voi vaatia erilaisia kaavoja
kuin top 20" - eli onko yhdella globaalilla K-kertoimella/scale-arvolla
huonompi kalibrointi eliittiotteluissa (molemmat top20) vs. leveamman
poolin otteluissa (joku/molemmat top21-75)?

METODOLOGIA (sama kuin projektin muut korjaukset - ei kehapaatelmia):
1) DIAGNOOSI ensin: mitataan NYKYISEN yhden globaalin mallin kalibrointi
   (log loss, Brier, kalibrointikayra) erikseen eliitti- vs leveampi-
   poolimatseille NAKEMATTOMALLA test-osiolla (viimeinen 20% kronologisesti).
   Tier maaritetaan MALLIN OMALLA senhetkisella Elo-rankingilla (rank
   kaikkien tahan mennessa pelanneiden joukkueiden joukossa juuri ENNEN
   kyseista ottelua) - taysin kausaalinen, ei lookahead-vuotoa.
2) Jos diagnoosi loytaa aidon eron, testataan korjaus: erillinen
   K-kerroin-multiplier leveamman poolin otteluiden RATING-PAIVITYKSELLE
   (sama mekanismi kuin online_update_weight). Grid-haku VAIN train-
   osiolla (ensimmainen 80%), validointi NAKEMATTOMALLA test-osiolla.
   Jos ei paranna test-log-lossia, HYLATAAN rehellisesti - ei etsita
   kiertotieta."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection  # noqa: E402
from backtest import (  # noqa: E402
    EloModel,
    brier_score,
    calibration_curve,
    deduplicate_matches,
    filter_top50_only,
    load_clean_matches,
    load_best_elo_params,
    log_loss,
)
from team_names import load_top50_names  # noqa: E402

ELITE_RANK_CUTOFF = 20
WEIGHT_CANDIDATES = [round(0.4 + 0.1 * i, 1) for i in range(17)]  # 0.4 .. 2.0


def current_rank(elo: EloModel, team: str, as_of) -> int:
    """1 = vahvin. Vain joukkueet joilla >=1 ottelu takana (muuten ranking
    epavakaa - kaikki oletusarvolla 1500)."""
    played = [(t, elo.rating_as_of(t, as_of)) for t in elo.ratings if elo.games_played.get(t, 0) >= 1]
    if not played:
        return 999
    played.sort(key=lambda x: -x[1])
    ranks = {t: i + 1 for i, (t, _) in enumerate(played)}
    return ranks.get(team, len(played) + 1)


def tier_of(rank_team: int, rank_opp: int) -> str:
    if rank_team <= ELITE_RANK_CUTOFF and rank_opp <= ELITE_RANK_CUTOFF:
        return "ELIITTI (molemmat top20)"
    if rank_team <= ELITE_RANK_CUTOFF or rank_opp <= ELITE_RANK_CUTOFF:
        return "SEKA (toinen top20)"
    return "LEVEA (molemmat top21-75)"


def ll(pairs):
    if not pairs:
        return float("nan")
    return log_loss([y for _, y in pairs], [p for p, _ in pairs])


def bs(pairs):
    if not pairs:
        return float("nan")
    return brier_score([y for _, y in pairs], [p for p, _ in pairs])


def main() -> int:
    conn = get_connection()
    all_matches = deduplicate_matches(load_clean_matches(conn))
    conn.close()
    top50 = load_top50_names()
    matches = filter_top50_only(all_matches, top50)
    params = load_best_elo_params()

    split_idx = int(len(matches) * 0.8)
    split_date = matches[split_idx].date
    print(f"Data (top75-vs-top75, SOS-suodatettu): {len(matches)} ottelua, "
          f"train/test-raja {split_date.date()}\n")

    # --- 1) DIAGNOOSI: nykyinen yksi globaali malli, tier NAKEMATTOMALLA test-osiolla ---
    elo = EloModel(scale=params["scale"], k_factor=params["k_factor"], half_life_days=params["half_life_days"])
    by_tier = {"ELIITTI (molemmat top20)": [], "SEKA (toinen top20)": [], "LEVEA (molemmat top21-75)": []}
    all_test = []
    for m in matches:
        p = elo.predict(m.team, m.opponent, m.date)
        if m.date >= split_date:
            rt = current_rank(elo, m.team, m.date)
            ro = current_rank(elo, m.opponent, m.date)
            tier = tier_of(rt, ro)
            by_tier[tier].append((p, m.team_won))
            all_test.append((p, m.team_won))
        elo.update(m.team, m.opponent, m.team_won, m.date)

    print("=== DIAGNOOSI: nykyisen (yhden globaalin K:n) mallin kalibrointi tiereittain, test-osio ===")
    print(f"{'Tier':32s}  {'n':>5s}  {'log_loss':>9s}  {'brier':>7s}")
    for tier, pairs in by_tier.items():
        print(f"{tier:32s}  {len(pairs):5d}  {ll(pairs):9.4f}  {bs(pairs):7.4f}")
    print(f"{'KAIKKI':32s}  {len(all_test):5d}  {ll(all_test):9.4f}  {bs(all_test):7.4f}")

    for tier, pairs in by_tier.items():
        if len(pairs) < 15:
            continue
        print(f"\nKalibrointikayra - {tier} (n={len(pairs)}):")
        outcomes = [y for _, y in pairs]
        probs = [p for p, _ in pairs]
        for b in calibration_curve(outcomes, probs, n_bins=5):
            if b["n"]:
                print(f"  {b['range']}: n={b['n']:3d}  ennustettu={b['avg_predicted']:.2f}  toteutunut={b['avg_actual']:.2f}")

    # --- 2) KORJAUSEHDOTUS: LEVEA/SEKA-otteluiden rating-paivitykselle oma K-multiplier ---
    # Grid-haku VAIN train-datalla (koko datasetin log loss), testataan nakemattomalla.
    def walkforward(multiplier: float, split_date):
        elo2 = EloModel(scale=params["scale"], k_factor=params["k_factor"], half_life_days=params["half_life_days"])
        train_pairs, test_pairs = [], []
        test_by_tier = {"ELIITTI (molemmat top20)": [], "SEKA (toinen top20)": [], "LEVEA (molemmat top21-75)": []}
        for m in matches:
            p = elo2.predict(m.team, m.opponent, m.date)
            rt = current_rank(elo2, m.team, m.date)
            ro = current_rank(elo2, m.opponent, m.date)
            tier = tier_of(rt, ro)
            is_test = m.date >= split_date
            (test_pairs if is_test else train_pairs).append((p, m.team_won))
            if is_test:
                test_by_tier[tier].append((p, m.team_won))
            k_override = params["k_factor"] * multiplier if tier == "LEVEA (molemmat top21-75)" else None
            elo2.update(m.team, m.opponent, m.team_won, m.date, k_override=k_override)
        return train_pairs, test_pairs, test_by_tier

    print("\n\n=== Grid-haku train-datalla (VAIN LEVEA-otteluiden K-multiplier, ELIITTI+SEKA aina 1.0) ===")
    train_results = []
    for w in WEIGHT_CANDIDATES:
        train_pairs, test_pairs, test_by_tier = walkforward(w, split_date)
        train_ll = ll(train_pairs)
        train_results.append((w, train_ll, test_pairs, test_by_tier))
        print(f"  multiplier={w:.1f}  train_log_loss={train_ll:.4f}")
    best_w, best_train_ll, best_test_pairs, best_test_by_tier = min(train_results, key=lambda r: r[1])
    print(f"\nParas multiplier train-datalla: {best_w}  (train_log_loss={best_train_ll:.4f})")

    _, baseline_test_pairs, baseline_test_by_tier = walkforward(1.0, split_date)

    print(f"\n=== Testataan NAKEMATTOMALLA test-osiolla ===")
    print(f"{'':32s}  {'Nykyinen (1.0)':>15}  {'Paras ('+str(best_w)+')':>15}")
    for tier in by_tier:
        n = len(baseline_test_by_tier[tier])
        print(f"{tier:32s}  {ll(baseline_test_by_tier[tier]):15.4f}  {ll(best_test_by_tier[tier]):15.4f}   (n={n})")
    ll_base_all = ll(baseline_test_pairs)
    ll_best_all = ll(best_test_pairs)
    print(f"{'KAIKKI':32s}  {ll_base_all:15.4f}  {ll_best_all:15.4f}   (n={len(baseline_test_pairs)})")

    if ll_best_all < ll_base_all:
        print(f"\n-> multiplier={best_w} PARANTAA koko test-joukon ennustetta ({ll_base_all:.4f} -> {ll_best_all:.4f}). Kannattaa harkita kayttoonottoa.")
    else:
        print(f"\n-> multiplier={best_w} EI paranna koko test-joukon ennustetta nakemattomalla datalla - EI kannata ottaa kayttoon.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
