"""
Jatkokoe run_tier_calibration.py:n jalkeen (kayttajan pyynnosta "kokeile
jotain muuta" sen jalkeen kun tier-kohtainen K-multiplier RATING-
PAIVITYKSESSA ei kestanyt rehellista testia - ks. run_tier_calibration.py).

TASSA kokeillaan eri mekanismia: sama periaate kuin apply_online_adjustment
(ONLINE_SHRINK) - ei kosketa Elo-ratingin PAIVITYSTA lainkaan, vain
kutistetaan lopullista ENNUSTETTA kohti 0.5:ta LEVEA-tason (molemmat
top21-75) otteluille. Tama on suoraviivaisempi/pienempi vapausasteinen
korjaus kuin K-multiplier (yksi kerroin, ei muuta rating-historiaa
tuleviin ennusteisiin), joten vahemman altis ylisovitukselle pienella
otoksella.

METODOLOGIA: sama kuin projektin muut - grid-haku shrink [0.0, 1.5]
VAIN train-datalla (koko datasetin log loss, EI vain LEVEA-otteluiden,
koska Elo-rating itse on sama molemmissa - vain lopullinen ennuste
LEVEA-otteluille muuttuu), validointi NAKEMATTOMALLA test-osiolla,
raportoitu tiereittain. Jos ei paranna, hylataan rehellisesti."""
from __future__ import annotations

import sys
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
)
from team_names import load_top50_names  # noqa: E402

ELITE_RANK_CUTOFF = 20
SHRINK_CANDIDATES = [round(0.0 + 0.1 * i, 1) for i in range(16)]  # 0.0 .. 1.5


def current_rank(elo: EloModel, team: str, as_of) -> int:
    played = [(t, elo.rating_as_of(t, as_of)) for t in elo.ratings if elo.games_played.get(t, 0) >= 1]
    if not played:
        return 999
    played.sort(key=lambda x: -x[1])
    ranks = {t: i + 1 for i, (t, _) in enumerate(played)}
    return ranks.get(team, len(played) + 1)


def is_levea(rank_team: int, rank_opp: int) -> bool:
    return rank_team > ELITE_RANK_CUTOFF and rank_opp > ELITE_RANK_CUTOFF


def ll(pairs):
    if not pairs:
        return float("nan")
    return log_loss([y for _, y in pairs], [p for p, _ in pairs])


def main() -> int:
    conn = get_connection()
    all_matches = deduplicate_matches(load_clean_matches(conn))
    conn.close()
    top50 = load_top50_names()
    matches = filter_top50_only(all_matches, top50)
    params = load_best_elo_params()

    split_idx = int(len(matches) * 0.8)
    split_date = matches[split_idx].date
    print(f"Data: {len(matches)} ottelua, train/test-raja {split_date.date()}\n")

    # Rakennetaan YKSI Elo-malli (nykyinen, ei muutettu update-mekaniikkaa),
    # tallennetaan JOKAISEN ottelun (raaka_p, tier, is_test, y) - shrink
    # sovelletaan tahan jalkikateen eri ehdokkailla, koskematta rating-
    # historiaan (sama trikki kuin online-shrink-kokeessa).
    elo = EloModel(scale=params["scale"], k_factor=params["k_factor"], half_life_days=params["half_life_days"])
    records = []  # (raw_p, is_levea, y, is_test)
    for m in matches:
        p = elo.predict(m.team, m.opponent, m.date)
        rt = current_rank(elo, m.team, m.date)
        ro = current_rank(elo, m.opponent, m.date)
        records.append((p, is_levea(rt, ro), m.team_won, m.date >= split_date))
        elo.update(m.team, m.opponent, m.team_won, m.date)

    def shrunk(p, levea, shrink):
        return 0.5 + (p - 0.5) * shrink if levea else p

    print("=== Grid-haku train-datalla (LEVEA-ennusteiden shrink kohti 0.5:ta) ===")
    train_results = []
    for s in SHRINK_CANDIDATES:
        train_pairs = [(shrunk(p, lv, s), y) for p, lv, y, is_test in records if not is_test]
        train_ll = ll(train_pairs)
        train_results.append((s, train_ll))
        print(f"  shrink={s:.1f}  train_log_loss={train_ll:.4f}")
    best_s, best_train_ll = min(train_results, key=lambda r: r[1])
    print(f"\nParas shrink train-datalla: {best_s}  (train_log_loss={best_train_ll:.4f})")

    print(f"\n=== Testataan NAKEMATTOMALLA test-osiolla ===")
    for label, filt in [
        ("KAIKKI (test)", lambda r: r[3]),
        ("...vain LEVEA (test)", lambda r: r[3] and r[1]),
        ("...vain ELIITTI+SEKA (test)", lambda r: r[3] and not r[1]),
    ]:
        base_pairs = [(p, y) for p, lv, y, is_test in records if filt((p, lv, y, is_test))]
        best_pairs = [(shrunk(p, lv, best_s), y) for p, lv, y, is_test in records if filt((p, lv, y, is_test))]
        print(f"{label:28s}  nykyinen={ll(base_pairs):.4f}  paras(shrink={best_s})={ll(best_pairs):.4f}  (n={len(base_pairs)})")

    base_all = [(p, y) for p, lv, y, is_test in records if is_test]
    best_all = [(shrunk(p, lv, best_s), y) for p, lv, y, is_test in records if is_test]
    ll_base, ll_best = ll(base_all), ll(best_all)
    if ll_best < ll_base:
        print(f"\n-> shrink={best_s} PARANTAA koko test-joukon ennustetta ({ll_base:.4f} -> {ll_best:.4f}). Kannattaa harkita kayttoonottoa.")
    else:
        print(f"\n-> shrink={best_s} EI paranna koko test-joukon ennustetta nakemattomalla datalla - EI kannata ottaa kayttoon.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
