"""
SOS-suodatuksen pehmennys (kayttajan pyynnosta "yrita keksia lisaa").
Nykyinen filter_top50_only() on TAYSI PAALLA/POIS-kytkin: ottelu top75-
listan ULKOPUOLista vastustajaa vastaan POISTETAAN kokonaan Elo-
paivityksesta. Tama hylkaa KAIKEN signaalin naista otteluista, vaikka
esim. selva 2-0-lakaisu heikkoa karsintajoukkuetta vastaan silti kertoo
JOTAIN (vain vahemman luotettavasti kuin top75-vs-top75-ottelu).

IDEA: sen sijaan etta poistetaan nama ottelut kokonaan, annetaan niille
PIENENNETTY K-kerroin (RATING-PAIVITYKSESSA, ei kosketa ennustetta) -
sailyttaa osan signaalista, vahentaa "irrallisen poolin" vaaristymaa
taysin poistamisen sijaan. multiplier=0.0 vastaa NYKYISTA hard-filtteria,
multiplier=1.0 vastaisi TAYSIN suodattamatonta (esi-SOS) mallia.

METODOLOGIA: sama kuin SOS-filtterin oma alkuperainen validointi
(run_sos_filter.py) - grid-haku multiplier [0.0, 1.0] VAIN train-datalla
(koko datasetin log loss), validointi NAKEMATTOMALLA top75-vs-top75
test-joukolla (sama eval_keys-tekniikka kuin run_sos_filter.py:ssa)."""
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
    online_k_override,
)
from team_names import load_top50_names  # noqa: E402

MULTIPLIER_CANDIDATES = [round(0.0 + 0.1 * i, 1) for i in range(11)]  # 0.0 .. 1.0


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

    top50_matches = [m for m in all_matches if m.team in top50 and m.opponent in top50]
    split_idx = int(len(top50_matches) * 0.8)
    split_date = top50_matches[split_idx].date
    print(f"Data (KAIKKI ottelut, ei viela SOS-suodatettu): {len(all_matches)} ottelua")
    print(f"Train/test-raja (top75-vs-top75-datan oma): {split_date.date()}\n")

    test_top50 = [m for m in top50_matches if m.date >= split_date]
    eval_keys = {(m.date, m.team, m.opponent) for m in test_top50}
    print(f"Testijoukko (top75-vs-top75, nakematon 20%): {len(test_top50)} ottelua\n")

    def walkforward(non_top50_multiplier: float):
        elo = EloModel(scale=params["scale"], k_factor=params["k_factor"], half_life_days=params["half_life_days"])
        train_pairs, test_pairs = [], []
        for m in all_matches:
            p = elo.predict(m.team, m.opponent, m.date)
            is_eval = (m.date, m.team, m.opponent) in eval_keys
            if is_eval:
                test_pairs.append((p, m.team_won))
            elif m.date < split_date and m.team in top50 and m.opponent in top50:
                train_pairs.append((p, m.team_won))
            base_k = online_k_override(params["k_factor"], m.match_type) or params["k_factor"]
            both_top50 = m.team in top50 and m.opponent in top50
            k_override = base_k if both_top50 else base_k * non_top50_multiplier
            elo.update(m.team, m.opponent, m.team_won, m.date, k_override=k_override)
        return train_pairs, test_pairs

    print("=== Grid-haku train-datalla (top75-ulkopuolisten vastustajien K-multiplier) ===")
    train_results = []
    for mult in MULTIPLIER_CANDIDATES:
        train_pairs, test_pairs = walkforward(mult)
        train_ll = ll(train_pairs)
        train_results.append((mult, train_ll, test_pairs))
        print(f"  multiplier={mult:.1f}  train_log_loss={train_ll:.4f}")
    best_mult, best_train_ll, best_test = min(train_results, key=lambda r: r[1])
    print(f"\nParas multiplier train-datalla: {best_mult}  (train_log_loss={best_train_ll:.4f})")

    _, hard_filter_test = walkforward(0.0)   # nykyinen tuotanto (hard filter)
    _, no_filter_test = walkforward(1.0)     # esi-SOS (ei suodatusta)

    print(f"\n=== Testataan NAKEMATTOMALLA top75-vs-top75-test-osiolla ===")
    print(f"  NYKYINEN (hard filter, multiplier=0.0):     log_loss = {ll(hard_filter_test):.4f}  (n={len(hard_filter_test)})")
    print(f"  EI SUODATUSTA (multiplier=1.0, esi-SOS):    log_loss = {ll(no_filter_test):.4f}  (n={len(no_filter_test)})")
    print(f"  PEHMEA PAINOTUS (paras, multiplier={best_mult}): log_loss = {ll(best_test):.4f}  (n={len(best_test)})")

    ll_hard = ll(hard_filter_test)
    ll_best = ll(best_test)
    if best_mult > 0.0 and ll_best < ll_hard:
        print(f"\n-> Pehmea painotus (multiplier={best_mult}) PARANTAA nykyista hard filtteria ({ll_hard:.4f} -> {ll_best:.4f}). Kannattaa harkita kayttoonottoa.")
    else:
        print(f"\n-> Pehmea painotus EI paranna nykyista hard filtteria nakemattomalla datalla - EI kannata muuttaa.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
