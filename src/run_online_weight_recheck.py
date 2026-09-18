"""
Uudelleenvalidointi (kayttajan pyynnosta "jatka kehitysta" - LEVEA_SHRINK-
tapauksen jalkeen loydetyn "korjaukset voivat mitatoida toisiaan" -riskin
vuoksi): ONLINE_UPDATE_WEIGHT (validoitu 2026-09-18 AIEMMIN samana
paivana, ennen SOS-pehmennysta) on RATING-PAIVITYKSEN K-multiplier
online-otteluille - sama mekanismityyppi kuin SOS_NON_TOP50_K_WEIGHT,
molemmat kerrotaan yhteen production_k_override():ssa. Onko vanha arvo
0.2 yha optimaalinen nyt kun SOS-mekanismi ON MUUTTUNUT (hard filter ->
pehmea painotus) sen jalkeen kun ONLINE_UPDATE_WEIGHT alun perin
viritettiin?

METODOLOGIA: sama kuin run_online_weight.py, mutta walk-forward kayttaa
NYT production_k_override():a (SOS-pehmennys mukana, SOS_NON_TOP50_K_
WEIGHT=0.5 pidetaan kiinteana - vain online_weight vaihtelee gridissa)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection  # noqa: E402
from backtest import (  # noqa: E402
    EloModel,
    deduplicate_matches,
    load_clean_matches,
    load_best_elo_params,
    log_loss,
)
from team_names import load_top50_names  # noqa: E402

WEIGHT_CANDIDATES = [round(0.0 + 0.1 * i, 1) for i in range(11)]  # 0.0 .. 1.0


def ll(pairs):
    if not pairs:
        return float("nan")
    return log_loss([y for _, y in pairs], [p for p, _ in pairs])


def main() -> int:
    conn = get_connection()
    all_matches = deduplicate_matches(load_clean_matches(conn))
    conn.close()
    top50 = load_top50_names()
    params = load_best_elo_params()

    eval_matches = [m for m in all_matches if m.team in top50 and m.opponent in top50]
    split_idx = int(len(eval_matches) * 0.8)
    split_date = eval_matches[split_idx].date
    eval_keys = {(m.date, m.team, m.opponent) for m in eval_matches}
    print(f"Data: {len(all_matches)} ottelua, arviointijoukko (top75-vs-top75) {len(eval_matches)}, "
          f"train/test-raja {split_date.date()}\n")

    def walkforward(online_weight: float):
        elo = EloModel(scale=params["scale"], k_factor=params["k_factor"], half_life_days=params["half_life_days"])
        train_pairs, test_pairs = [], []
        test_online, test_lan = [], []
        for m in all_matches:
            p = elo.predict(m.team, m.opponent, m.date)
            is_eval = (m.date, m.team, m.opponent) in eval_keys
            if is_eval:
                pair = (p, m.team_won)
                (test_pairs if m.date >= split_date else train_pairs).append(pair)
                if m.date >= split_date:
                    (test_online if m.match_type == "Online" else test_lan).append(pair)
            k = params["k_factor"]
            if m.match_type == "Online":
                k *= online_weight
            if not (m.team in top50 and m.opponent in top50):
                k *= 0.5  # SOS_NON_TOP50_K_WEIGHT, pidetaan kiinteana tassa kokeessa
            elo.update(m.team, m.opponent, m.team_won, m.date, k_override=k)
        return train_pairs, test_pairs, test_online, test_lan

    print("=== Grid-haku train-datalla (online_weight, SOS-pehmennys mukana) ===")
    results = []
    for w in WEIGHT_CANDIDATES:
        train_pairs, test_pairs, test_online, test_lan = walkforward(w)
        tll = ll(train_pairs)
        results.append((w, tll, test_pairs, test_online, test_lan))
        print(f"  online_weight={w:.1f}  train_log_loss={tll:.4f}")
    best_w, best_tll, best_test, best_online, best_lan = min(results, key=lambda r: r[1])
    print(f"\nParas online_weight (train): {best_w}  (train_log_loss={best_tll:.4f})")

    _, base_test, base_online, base_lan = walkforward(0.2)  # nykyinen tuotanto

    print(f"\n=== Testataan NAKEMATTOMALLA test-osiolla ===")
    print(f"{'':24s}  {'Nykyinen (0.2)':>16}  {'Paras ('+str(best_w)+')':>16}")
    for label, base, best in [("KAIKKI", base_test, best_test), ("...Online", base_online, best_online),
                               ("...LAN/Offline", base_lan, best_lan)]:
        print(f"{label:24s}  {ll(base):16.4f}  {ll(best):16.4f}   (n={len(base)})")

    if ll(best_test) < ll(base_test):
        print(f"\n-> online_weight={best_w} VOITTAA nykyisen (0.2) nakemattomalla datalla "
              f"({ll(base_test):.4f} -> {ll(best_test):.4f}). Harkitse paivitysta.")
    else:
        print(f"\n-> Nykyinen online_weight=0.2 PYSYY parhaana (tai yhta hyvana) nakemattomalla datalla "
              f"- EI muutosta.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
