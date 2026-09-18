"""
Uudelleenvalidointi (jatkoa run_online_weight_recheck.py:lle, kayttajan
pyynnosta "jatka kehitysta"): ONLINE_SHRINK (validoitu 2026-09-18
AIEMMIN samana paivana, ennen SOS-pehmennysta ja ennen ONLINE_UPDATE_
WEIGHT-mekanismia) kutistaa lopullista ENNUSTETTA kohti 0.5:ta online-
otteluille. Onko vanha arvo 0.0 (=silla ei ole minkaanlaista ennuste-
arvoa) yha optimaalinen nyt kun sen ALLA oleva rating (SOS-pehmennys +
ONLINE_UPDATE_WEIGHT=0.2 rating-paivityksessa) on muuttunut?"""
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
    production_k_override,
)
from team_names import load_top50_names  # noqa: E402

SHRINK_CANDIDATES = [round(0.0 + 0.1 * i, 1) for i in range(11)]  # 0.0 .. 1.0


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
    print(f"Data: arviointijoukko (top75-vs-top75) {len(eval_matches)}, train/test-raja {split_date.date()}\n")

    # Rakennetaan tama YHDEN kerran (production_k_override sisaltaa jo
    # ONLINE_UPDATE_WEIGHT+SOS_NON_TOP50_K_WEIGHT nykyiset validoidut
    # arvot rating-paivityksessa) - shrink vaikuttaa VAIN ennusteeseen,
    # ei ratingiin, joten sen voi soveltaa jalkikateen samaan ajoon.
    elo = EloModel(scale=params["scale"], k_factor=params["k_factor"], half_life_days=params["half_life_days"])
    records = []  # (raw_p, is_online, y, is_test)
    for m in all_matches:
        p = elo.predict(m.team, m.opponent, m.date)
        if m.team in top50 and m.opponent in top50:
            records.append((p, m.match_type == "Online", m.team_won, m.date >= split_date))
        k_override = production_k_override(params["k_factor"], m.match_type, m.team, m.opponent, top50)
        elo.update(m.team, m.opponent, m.team_won, m.date, k_override=k_override)

    def shrunk(p, is_online, shrink):
        return 0.5 + (p - 0.5) * shrink if is_online else p

    print("=== Grid-haku train-datalla (ONLINE_SHRINK) ===")
    results = []
    for s in SHRINK_CANDIDATES:
        train_pairs = [(shrunk(p, o, s), y) for p, o, y, is_test in records if not is_test]
        tll = ll(train_pairs)
        results.append((s, tll))
        print(f"  shrink={s:.1f}  train_log_loss={tll:.4f}")
    best_s, best_tll = min(results, key=lambda r: r[1])
    print(f"\nParas shrink (train): {best_s}  (train_log_loss={best_tll:.4f})")

    print(f"\n=== Testataan NAKEMATTOMALLA test-osiolla ===")
    for label, filt in [("KAIKKI", lambda r: r[3]), ("...Online", lambda r: r[3] and r[1]),
                         ("...LAN/Offline", lambda r: r[3] and not r[1])]:
        base_pairs = [(p, y) for p, o, y, is_test in records if filt((p, o, y, is_test))]
        best_pairs = [(shrunk(p, o, best_s), y) for p, o, y, is_test in records if filt((p, o, y, is_test))]
        print(f"{label:18s}  nykyinen(0.0)={ll(base_pairs):.4f}  paras({best_s})={ll(best_pairs):.4f}  (n={len(base_pairs)})")

    base_all = [(p, y) for p, o, y, is_test in records if is_test]
    best_all = [(shrunk(p, o, best_s), y) for p, o, y, is_test in records if is_test]
    if ll(best_all) < ll(base_all):
        print(f"\n-> shrink={best_s} VOITTAA nykyisen (0.0) nakemattomalla datalla "
              f"({ll(base_all):.4f} -> {ll(best_all):.4f}). Harkitse paivitysta.")
    else:
        print(f"\n-> Nykyinen ONLINE_SHRINK=0.0 PYSYY parhaana (tai yhta hyvana) nakemattomalla datalla - EI muutosta.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
