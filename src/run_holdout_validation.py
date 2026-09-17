"""
Mallin paikkansapitavyyden testaus ILMAN markkinakerrointa - aidolla
train/test-jaolla, ei vain walk-forwardilla.

TAUSTA / MIKSI TAMA TARVITAAN: run_elo.py:n ja run_backtest.py:n grid-haku
valitsi parhaat hyperparametrit (scale, k_factor, half_life_days) SAMALLA
koko datasetilla jolla tulos sitten raportoitiin (0.6646). Vaikka itse
walk-forward on aidosti lookahead-suojattu OTTELUKOHTAISESTI (jokainen
ennuste kayttaa vain sita aiempaa dataa), PARAMETRIVALINTA nakee koko
datasetin - eli 0.6646 saattaa olla lievasti liian optimistinen arvio
siita miten malli toimisi AIDOSTI uudella datalla.

Tama skripti korjaa taman: jakaa datan kronologisesti (esim. 80% train /
20% test PAIVAMAARAN mukaan), valitsee parametrit VAIN train-osalla, ja
raportoi lopullisen log lossin VAIN test-osalla - jota parametrivalinta
ei ole koskaan nahnyt. Elo-rating jatkuu kuitenkin yhtenaisena koko
aikajanan yli (train+test), koska aidossa kaytossa malli EI nollaudu
train/test-rajalla - se vain jatkaa oppimista.

Tama ei vaadi yhtaan markkinakerrointa - se on juuri se "testaa mallin
paikkansapitavyys ilman kertoimia" -kysymys konkreettisena koodina."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection  # noqa: E402
from backtest import (  # noqa: E402
    EloModel,
    VrsRankings,
    deduplicate_matches,
    load_clean_matches,
    log_loss,
    brier_score,
    calibration_curve,
)

SPLIT_FRAC = 0.8  # 80% vanhinta = train, 20% tuoreinta = test

SCALE_GRID = [100, 150, 200, 300, 400, 500]
K_GRID = [8, 16, 24, 32, 48, 64, 96]
HALF_LIFE_GRID = [30, 60, 90, 180, 99999]

VRS_SCALE_GRID = [8, 16, 32, 48, 75, 100, 150, 200, 300, 500, 750, 1000]


def evaluate_elo(matches: list, split_idx: int, scale: float, k: float, half_life: float) -> dict:
    elo = EloModel(scale=scale, k_factor=k, half_life_days=half_life)
    train = {"outcomes": [], "probs": []}
    test = {"outcomes": [], "probs": []}
    for i, m in enumerate(matches):
        p = elo.predict(m.team, m.opponent, m.date)
        bucket = train if i < split_idx else test
        bucket["outcomes"].append(m.team_won)
        bucket["probs"].append(p)
        elo.update(m.team, m.opponent, m.team_won, m.date)
    return {
        "train_ll": log_loss(train["outcomes"], train["probs"]),
        "test_ll": log_loss(test["outcomes"], test["probs"]),
        "test_brier": brier_score(test["outcomes"], test["probs"]),
        "test_outcomes": test["outcomes"],
        "test_probs": test["probs"],
        "n_train": len(train["outcomes"]),
        "n_test": len(test["outcomes"]),
    }


def evaluate_vrs_soft(matches: list, split_idx: int, vrs: VrsRankings, scale: float) -> dict:
    import math

    train = {"outcomes": [], "probs": []}
    test = {"outcomes": [], "probs": []}
    for i, m in enumerate(matches):
        r_team = vrs.rank_as_of(m.team, m.date)
        r_opp = vrs.rank_as_of(m.opponent, m.date)
        p = 0.5
        if r_team is not None and r_opp is not None:
            diff = r_opp - r_team
            p = 1.0 / (1.0 + math.exp(-diff / scale))
        bucket = train if i < split_idx else test
        bucket["outcomes"].append(m.team_won)
        bucket["probs"].append(p)
    return {
        "train_ll": log_loss(train["outcomes"], train["probs"]),
        "test_ll": log_loss(test["outcomes"], test["probs"]),
        "n_train": len(train["outcomes"]),
        "n_test": len(test["outcomes"]),
    }


def main() -> int:
    import json

    conn = get_connection()
    matches = deduplicate_matches(load_clean_matches(conn))
    snapshots = json.loads((ROOT / "data" / "vrs_snapshots.json").read_text(encoding="utf-8"))
    vrs = VrsRankings(snapshots)
    conn.close()

    n = len(matches)
    split_idx = int(n * SPLIT_FRAC)
    split_date = matches[split_idx].date
    print(f"Otteluita yhteensa: {n}")
    print(f"Train: {split_idx} ottelua (ennen {split_date.date()})")
    print(f"Test:  {n - split_idx} ottelua ({split_date.date()} jalkeen) - talla EI viela optimoitu mitaan\n")

    # --- Aina-5050 (ei parametreja, referenssi) ---
    outcomes_test = [m.team_won for m in matches[split_idx:]]
    ll_5050 = log_loss(outcomes_test, [0.5] * len(outcomes_test))
    print(f"aina_5050                    test_log_loss={ll_5050:.4f}  (n_test={len(outcomes_test)})")

    # --- Pehmea VRS-sija: valitaan scale VAIN train-osalla ---
    best_vrs = None
    for scale in VRS_SCALE_GRID:
        res = evaluate_vrs_soft(matches, split_idx, vrs, scale)
        if best_vrs is None or res["train_ll"] < best_vrs[1]["train_ll"]:
            best_vrs = (scale, res)
    scale, res = best_vrs
    print(f"pehmea_vrs (scale={scale}, valittu train:lla)  train_ll={res['train_ll']:.4f}  "
          f"TEST_LL={res['test_ll']:.4f}  (n_test={res['n_test']})")

    # --- Elo: valitaan (scale, k, half_life) VAIN train-osalla ---
    best_elo = None
    for scale in SCALE_GRID:
        for k in K_GRID:
            for hl in HALF_LIFE_GRID:
                res = evaluate_elo(matches, split_idx, scale, k, hl)
                if best_elo is None or res["train_ll"] < best_elo[1]["train_ll"]:
                    best_elo = ((scale, k, hl), res)
    (scale, k, hl), res = best_elo
    print(f"Elo (scale={scale}, k={k}, half_life={hl}, valittu train:lla)")
    print(f"  train_ll={res['train_ll']:.4f}  TEST_LL={res['test_ll']:.4f}  test_brier={res['test_brier']:.4f}"
          f"  (n_test={res['n_test']})")

    print("\n=== Kalibrointi TEST-osalla (nakematonta dataa parametrivalinnalle) ===")
    for bucket in calibration_curve(res["test_outcomes"], res["test_probs"], n_bins=5):
        if bucket["n"] == 0:
            continue
        print(f"  ennustettu {bucket['range']}: n={bucket['n']:3d}  "
              f"keskim.ennuste={bucket['avg_predicted']:.2f}  toteutunut={bucket['avg_actual']:.2f}")

    print("\nJOHTOPAATOS: jos Elon TEST_LL on selvasti parempi kuin aina_5050 JA pehmea_vrs:n"
          " TEST_LL, malli yleistyy oikeasti aidosti nakemattomaan dataan - ei vain sovi"
          " hyvin siihen dataan jolla se viritettiin.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
