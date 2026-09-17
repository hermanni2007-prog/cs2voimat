"""
Jatkokysymys online-korjauksesta (2026-09-18): apply_online_adjustment
vaimentaa vain ENNUSTETTA online-ottelulle, mutta online-ottelun TULOS
paivittaa silti joukkueen ratingia taydella K-kertoimella - tama
vaikuttaa KAIKKIIN tuleviin ennusteisiin (myos LAN-otteluihin) koska
rating on yhteinen. Kayttajan kysymys: kannattaisiko online-otteluiden
paivityksille antaa PIENEMPI paino ITSE RATING-PAIVITYKSESSA, ei vain
ennusteessa?

METODOLOGIA: sama kuin online-korjauksessa. Grid-haku online_weight
[0.0, 1.0] VAIN train-osiolla (ensimmainen 80% kronologisesti, KOKO
datasetin log loss - ei vain online-otteluiden, koska tavoite on
parantaa YLEISTA rating-laatua kaikille tuleville ennusteille), testattu
NAKEMATTOMALLA test-osiolla - raportoitu erikseen koko test-joukolle,
pelkalle LAN-test-joukolle ja pelkalle online-test-joukolle."""
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

WEIGHT_CANDIDATES = [round(0.0 + 0.1 * i, 1) for i in range(11)]  # 0.0 .. 1.0


def walkforward(matches: list, params: dict, online_weight: float, split_date) -> dict:
    elo = EloModel(scale=params["scale"], k_factor=params["k_factor"], half_life_days=params["half_life_days"])
    train_pairs, test_pairs, test_online_pairs, test_lan_pairs = [], [], [], []
    for m in matches:
        p = elo.predict(m.team, m.opponent, m.date)
        is_online = m.match_type == "Online"
        is_test = m.date >= split_date
        (test_pairs if is_test else train_pairs).append((p, m.team_won))
        if is_test:
            (test_online_pairs if is_online else test_lan_pairs).append((p, m.team_won))
        k_override = params["k_factor"] * online_weight if is_online else None
        elo.update(m.team, m.opponent, m.team_won, m.date, k_override=k_override)
    return {"train": train_pairs, "test": test_pairs, "test_online": test_online_pairs, "test_lan": test_lan_pairs}


def ll(pairs: list) -> float:
    if not pairs:
        return float("nan")
    outcomes = [y for _, y in pairs]
    probs = [p for p, _ in pairs]
    return log_loss(outcomes, probs)


def main() -> int:
    conn = get_connection()
    all_matches = deduplicate_matches(load_clean_matches(conn))
    conn.close()
    matches = filter_top50_only(all_matches, load_top50_names())
    params = load_best_elo_params()

    split_idx = int(len(matches) * 0.8)
    split_date = matches[split_idx].date
    print(f"Train/test-raja: {split_date.date()} ({len(matches)} ottelua)\n")

    print("=== Grid-haku train-datalla (koko datasetin log loss, online_weight 0.0-1.0) ===")
    train_results = []
    for w in WEIGHT_CANDIDATES:
        res = walkforward(matches, params, w, split_date)
        train_ll = ll(res["train"])
        train_results.append((w, train_ll, res))
        print(f"  online_weight={w:.1f}  train_log_loss={train_ll:.4f}")
    best_w, best_train_ll, best_res = min(train_results, key=lambda r: r[1])
    print(f"\nParas online_weight train-datalla: {best_w}  (train_log_loss={best_train_ll:.4f})")

    baseline_res = walkforward(matches, params, 1.0, split_date)  # 1.0 = nykyinen (ei painotusta)

    print(f"\n=== Testataan NAKEMATTOMALLA test-osiolla ===")
    print(f"{'':20s}  {'Nykyinen (w=1.0)':>18}  {'Paras (w='+str(best_w)+')':>18}")
    for label, key in [("Koko test-joukko", "test"), ("...vain LAN-test", "test_lan"), ("...vain Online-test", "test_online")]:
        n = len(baseline_res[key])
        ll_base = ll(baseline_res[key])
        ll_best = ll(best_res[key])
        print(f"{label:20s}  {ll_base:>18.4f}  {ll_best:>18.4f}   (n={n})")

    ll_base_all = ll(baseline_res["test"])
    ll_best_all = ll(best_res["test"])
    if ll_best_all < ll_base_all:
        print(f"\n-> online_weight={best_w} PARANTAA koko test-joukon ennustetta ({ll_base_all:.4f} -> {ll_best_all:.4f}). Kannattaa ottaa kayttoon.")
    else:
        print(f"\n-> online_weight={best_w} EI paranna koko test-joukon ennustetta nakemattomalla datalla - EI kannata ottaa kayttoon.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
