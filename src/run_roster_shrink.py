"""
Jatkokoe run_roster_signal.py:n diagnoosin jalkeen (kayttajan pyynnosta
"kehita mallia lisaa" - roster-data on nyt ensi kertaa taydellinen kaikille
75 joukkueelle, 1808 riviä, kerattiin 2026-09-18). Diagnoosi (koko data,
ei train/test-jakoa) loysi ison eron: VAKAA kokoonpano log_loss=0.6417
(n=2195) vs TUORE rosterimuutos (>=1 uusi pelaaja 30 vrk sisalla) log_loss
=0.6613 (n=1200) - merkittava, hyvin populoitu ero.

METODOLOGIA (sama kuin projektin muut korjaukset - ei kehapaatelmia):
puhdas ENNUSTE-tason shrink kohti 0.5:ta (sama mekanismi kuin ONLINE_
SHRINK/LEVEA_SHRINK, EI kosketa Elo-ratingin paivitysta), grid-haku VAIN
train-datalla (ensimmainen 80% kronologisesti), validointi NAKEMATTOMALLA
test-osiolla, raportoitu erikseen VAKAA- ja TUORE-ryhmille jotta nahdaan
ettei parannus tule vain toisen ryhman kustannuksella (sama tarkistus
joka paljasti LEVEA-K-multiplierin ongelman aiemmin)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection  # noqa: E402
from backtest import (  # noqa: E402
    EloModel,
    RosterHistory,
    deduplicate_matches,
    filter_top50_only,
    load_clean_matches,
    load_best_elo_params,
    log_loss,
)
from team_names import load_top50_names  # noqa: E402

LOOKBACK_DAYS = 30
CHANGE_THRESHOLD = 1
SHRINK_CANDIDATES = [round(0.0 + 0.1 * i, 1) for i in range(16)]  # 0.0 .. 1.5


def ll(pairs):
    if not pairs:
        return float("nan")
    return log_loss([y for _, y in pairs], [p for p, _ in pairs])


def main() -> int:
    conn = get_connection()
    all_matches = deduplicate_matches(load_clean_matches(conn))
    roster = RosterHistory(conn)
    conn.close()
    top50 = load_top50_names()
    matches = filter_top50_only(all_matches, top50)
    params = load_best_elo_params()

    split_idx = int(len(matches) * 0.8)
    split_date = matches[split_idx].date
    print(f"Data (top75-vs-top75, SOS-suodatettu): {len(matches)} ottelua, "
          f"train/test-raja {split_date.date()}\n")

    elo = EloModel(scale=params["scale"], k_factor=params["k_factor"], half_life_days=params["half_life_days"])
    records = []  # (raw_p, changed, y, is_test)
    for m in matches:
        p = elo.predict(m.team, m.opponent, m.date)
        team_changed = roster.roster_stability(m.team, m.date, LOOKBACK_DAYS) >= CHANGE_THRESHOLD
        opp_changed = roster.roster_stability(m.opponent, m.date, LOOKBACK_DAYS) >= CHANGE_THRESHOLD
        changed = team_changed or opp_changed
        records.append((p, changed, m.team_won, m.date >= split_date))
        elo.update(m.team, m.opponent, m.team_won, m.date)

    def shrunk(p, changed, shrink):
        return 0.5 + (p - 0.5) * shrink if changed else p

    print("=== Grid-haku train-datalla (tuore rosterimuutos -ennusteiden shrink kohti 0.5:ta) ===")
    train_results = []
    for s in SHRINK_CANDIDATES:
        train_pairs = [(shrunk(p, c, s), y) for p, c, y, is_test in records if not is_test]
        train_ll = ll(train_pairs)
        train_results.append((s, train_ll))
        print(f"  shrink={s:.1f}  train_log_loss={train_ll:.4f}")
    best_s, best_train_ll = min(train_results, key=lambda r: r[1])
    print(f"\nParas shrink train-datalla: {best_s}  (train_log_loss={best_train_ll:.4f})")

    print(f"\n=== Testataan NAKEMATTOMALLA test-osiolla ===")
    for label, filt in [
        ("KAIKKI (test)", lambda r: r[3]),
        ("...vain TUORE muutos (test)", lambda r: r[3] and r[1]),
        ("...vain VAKAA (test)", lambda r: r[3] and not r[1]),
    ]:
        base_pairs = [(p, y) for p, c, y, is_test in records if filt((p, c, y, is_test))]
        best_pairs = [(shrunk(p, c, best_s), y) for p, c, y, is_test in records if filt((p, c, y, is_test))]
        print(f"{label:28s}  nykyinen={ll(base_pairs):.4f}  paras(shrink={best_s})={ll(best_pairs):.4f}  (n={len(base_pairs)})")

    base_all = [(p, y) for p, c, y, is_test in records if is_test]
    best_all = [(shrunk(p, c, best_s), y) for p, c, y, is_test in records if is_test]
    ll_base, ll_best = ll(base_all), ll(best_all)
    if ll_best < ll_base:
        print(f"\n-> shrink={best_s} PARANTAA koko test-joukon ennustetta ({ll_base:.4f} -> {ll_best:.4f}). Kannattaa harkita kayttoonottoa.")
    else:
        print(f"\n-> shrink={best_s} EI paranna koko test-joukon ennustetta nakemattomalla datalla - EI kannata ottaa kayttoon.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
