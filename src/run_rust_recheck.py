"""
Uudelleenvalidointi (jatkoa run_online_*_recheck.py:lle, kayttajan
pyynnosta "jatka kehitysta"): RUST_SHRINK_NO_RECENT_MATCH=0.790
(RUST_WINDOW_DAYS=5) validoitiin ALUN PERIN erittain pienella otoksella
(n=28 test-ottelua, ks. backtest.py:n kommentti) ennen top75-laajennusta,
dedup-korjausta ja SOS-pehmennysta - todennakoisin ehdokas "vanhentuneeksi
muuttuneeksi" korjaukseksi taman koko session'in korjausten joukossa.

Kayttaa run_tournament_effects.build_recent_match_index + backtest.
count_recent_matches - samat funktiot joita apply_rust_adjustment()
oikeasti kayttaa tuotannossa."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection  # noqa: E402
from backtest import (  # noqa: E402
    EloModel,
    RUST_WINDOW_DAYS,
    count_recent_matches,
    deduplicate_matches,
    load_clean_matches,
    load_best_elo_params,
    log_loss,
    production_k_override,
)
from run_tournament_effects import build_recent_match_index  # noqa: E402
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
    recent_idx = build_recent_match_index(all_matches)

    eval_matches = [m for m in all_matches if m.team in top50 and m.opponent in top50]
    split_idx = int(len(eval_matches) * 0.8)
    split_date = eval_matches[split_idx].date
    print(f"Data: arviointijoukko (top75-vs-top75) {len(eval_matches)}, train/test-raja {split_date.date()}\n")

    elo = EloModel(scale=params["scale"], k_factor=params["k_factor"], half_life_days=params["half_life_days"])
    records = []  # (raw_p, favorite_n_recent, y, is_test)
    for m in all_matches:
        p = elo.predict(m.team, m.opponent, m.date)
        if m.team in top50 and m.opponent in top50:
            n_team = count_recent_matches(m.team, m.date, recent_idx, RUST_WINDOW_DAYS)
            n_opp = count_recent_matches(m.opponent, m.date, recent_idx, RUST_WINDOW_DAYS)
            is_team_fav = p >= 0.5
            fav_n_recent = n_team if is_team_fav else n_opp
            records.append((p, fav_n_recent, m.team_won, m.date >= split_date))
        k_override = production_k_override(params["k_factor"], m.match_type, m.team, m.opponent, top50)
        elo.update(m.team, m.opponent, m.team_won, m.date, k_override=k_override)

    n_rusty_test = sum(1 for _, n, _, is_test in records if is_test and n == 0)
    print(f"Ruostuneita (suosikilla 0 ottelua {RUST_WINDOW_DAYS} vrk sisalla) test-otteluita: {n_rusty_test}\n")

    def shrunk(p, fav_n_recent, shrink):
        return 0.5 + (p - 0.5) * shrink if fav_n_recent == 0 else p

    print("=== Grid-haku train-datalla (RUST_SHRINK_NO_RECENT_MATCH) ===")
    results = []
    for s in SHRINK_CANDIDATES:
        train_pairs = [(shrunk(p, n, s), y) for p, n, y, is_test in records if not is_test]
        tll = ll(train_pairs)
        results.append((s, tll))
        print(f"  shrink={s:.1f}  train_log_loss={tll:.4f}")
    best_s, best_tll = min(results, key=lambda r: r[1])
    print(f"\nParas shrink (train): {best_s}  (train_log_loss={best_tll:.4f})")

    print(f"\n=== Testataan NAKEMATTOMALLA test-osiolla ===")
    for label, filt in [("KAIKKI", lambda r: r[3]), ("...ruostunut (n=0)", lambda r: r[3] and r[1] == 0),
                         ("...tuore (n>0)", lambda r: r[3] and r[1] > 0)]:
        curr_pairs = [(shrunk(p, n, 0.790), y) for p, n, y, is_test in records if filt((p, n, y, is_test))]
        best_pairs = [(shrunk(p, n, best_s), y) for p, n, y, is_test in records if filt((p, n, y, is_test))]
        print(f"{label:20s}  nykyinen(0.79)={ll(curr_pairs):.4f}  paras({best_s})={ll(best_pairs):.4f}  (n={len(curr_pairs)})")

    base_all = [(p, y) for p, n, y, is_test in records if is_test]
    curr_all = [(shrunk(p, n, 0.790), y) for p, n, y, is_test in records if is_test]
    best_all = [(shrunk(p, n, best_s), y) for p, n, y, is_test in records if is_test]
    print(f"\nNykyinen (0.790): test_log_loss={ll(curr_all):.4f}   Paras ({best_s}): test_log_loss={ll(best_all):.4f}   Ei korjausta (1.0, vertailu): {ll(base_all):.4f}")
    if ll(best_all) < ll(curr_all):
        print(f"-> shrink={best_s} VOITTAA nykyisen (0.790) nakemattomalla datalla. Harkitse paivitysta.")
    else:
        print(f"-> Nykyinen RUST_SHRINK_NO_RECENT_MATCH=0.790 PYSYY parhaana (tai yhta hyvana) - EI muutosta.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
