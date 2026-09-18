"""
K-kertoimen grid-haun laajennus (kayttajan valinta vaihtoehtolistalta).
run_elo.py:n korjatussa haussa (2026-09-18) nykyinen tuotanto-k=96 VOITTI
koko testatun k-gridin (8,16,24,32,48,64,96) - eli paras loydetty arvo
osui gridin YLAREUNAAN. Ei tiedetty jatkuisiko trendi (isompi k parempi)
viela pidemmalle, koska sita ei testattu.

TASSA: sama train/test-metodologia kuin run_elo.py:ssa (production_
k_override mukana), mutta k-grid laajennettu ylospain (96 -> 128 -> 160
-> ... -> 500), scale ja half_life PIDETTY KIINTEINA nykyisessa parhaassa
(400, 99999) - jos suunta jatkuu, tama loytaa sen; jos kayra kaantyy
(mika olisi odotettua - aarettoman iso K tarkoittaisi etta JOKAINEN
ottelu mitatoi koko aiemman ratingin, mika ei voi olla optimaalista),
loydetaan oikea sisainen optimi."""
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

K_CANDIDATES = [16, 32, 48, 64, 96, 128, 160, 200, 250, 300, 400, 500, 650, 800]


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
    scale, half_life = params["scale"], params["half_life_days"]

    eval_matches = [m for m in all_matches if m.team in top50 and m.opponent in top50]
    split_idx = int(len(eval_matches) * 0.8)
    split_date = eval_matches[split_idx].date
    eval_keys = {(m.date, m.team, m.opponent) for m in eval_matches}
    print(f"Data: arviointijoukko (top75-vs-top75) {len(eval_matches)}, train/test-raja {split_date.date()}")
    print(f"Kiinteat: scale={scale}  half_life={half_life}\n")

    def walkforward(k):
        elo = EloModel(scale=scale, k_factor=k, half_life_days=half_life)
        train_pairs, test_pairs = [], []
        for m in all_matches:
            p = elo.predict(m.team, m.opponent, m.date)
            if (m.date, m.team, m.opponent) in eval_keys:
                pair = (p, m.team_won)
                (test_pairs if m.date >= split_date else train_pairs).append(pair)
            k_override = production_k_override(k, m.match_type, m.team, m.opponent, top50)
            elo.update(m.team, m.opponent, m.team_won, m.date, k_override=k_override)
        return train_pairs, test_pairs

    print("=== Laajennettu k-grid, TRAIN log loss ===")
    results = []
    for k in K_CANDIDATES:
        train_pairs, test_pairs = walkforward(k)
        tll = ll(train_pairs)
        results.append((k, tll, test_pairs))
        marker = "  <- nykyinen tuotanto" if k == params["k_factor"] else ""
        print(f"  k={k:4d}  train_log_loss={tll:.4f}{marker}")
    best_k, best_tll, best_test = min(results, key=lambda r: r[1])
    print(f"\nParas k (train): {best_k}  (train_log_loss={best_tll:.4f})")

    _, deployed_test = walkforward(params["k_factor"])
    print(f"\n=== Testataan NAKEMATTOMALLA test-osiolla ===")
    print(f"  NYKYINEN (k={params['k_factor']}):  test_log_loss = {ll(deployed_test):.4f}")
    print(f"  PARAS (k={best_k}):          test_log_loss = {ll(best_test):.4f}")

    if ll(best_test) < ll(deployed_test):
        print(f"\n-> k={best_k} VOITTAA nykyisen tuotannon nakemattomalla datalla. Harkitse paivitysta "
              f"(HUOM: paivita talloin data/elo_report.json TAI aja run_elo.py uudelleen taman jalkeen).")
    else:
        print(f"\n-> Nykyinen k={params['k_factor']} PYSYY parhaana (tai yhta hyvana) nakemattomalla datalla - EI muutosta.")

    if best_k == K_CANDIDATES[-1]:
        print(f"\nHUOM: paras arvo osui TAAS gridin ylareunaan ({K_CANDIDATES[-1]}) - suunta ei viela kaantynyt, "
              f"grida voisi laajentaa lisaa jos halutaan jatkaa.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
