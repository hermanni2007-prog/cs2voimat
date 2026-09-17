"""
Tehtava 5: formipaino lambda. Aja kaksi rinnakkaista Elo-ratingia
(puoliintumisajat ~180 vrk ja ~21 vrk), hae lambda ruudukosta 0..1 joka
minimoi walk-forward log lossin sekoitetulle PV = R_hidas + lambda*(R_nopea
- R_hidas) -ratingille.

TAMA ON PROJEKTIN ALKUPERAINEN TEESI (kayttajan oma, istunnon alusta):
"kun muutaman pelin formia hieman korostaa niin saa edgen". Jos paras
lambda tulee ulos 0:na tai lahella 0:aa, teesi ei pida paikkaansa tassa
datassa - raportoidaan suoraan.

Kayttaa Tehtava 3:sta parhaaksi loydettya (scale, k) = (200, 48, korjattu
2026-09-17 dedup-bugin jalkeen) - vain half_life vaihtelee kahden mallin
valilla, kuten briiffi maarittelee.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection  # noqa: E402
from backtest import (  # noqa: E402
    EloModel,
    deduplicate_matches,
    load_clean_matches,
    run_dual_elo_walkforward,
    run_elo_walkforward,
)

SCALE = 200.0
K_FACTOR = 48.0
SLOW_HALF_LIFE = 180.0
FAST_HALF_LIFE = 21.0

REPORT_PATH = ROOT / "data" / "form_lambda_report.json"


def fmt(v):
    return "n/a" if v is None else f"{v:.4f}"


def main() -> int:
    conn = get_connection()
    matches = deduplicate_matches(load_clean_matches(conn))
    print(f"Ottelut (deduplikoitu): {len(matches)}")

    # Vertailu: yksittainen Elo ilman formipainoa (Tehtava 3:n paras)
    single = run_elo_walkforward(matches, EloModel(scale=SCALE, k_factor=K_FACTOR, half_life_days=99999))
    print(f"Yksittainen Elo (ei vaimennusta): log_loss={fmt(single['log_loss'])}")

    lambdas = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    results = []
    best = None
    print("\nlambda -> log_loss (brier)")
    for lam in lambdas:
        elo_slow = EloModel(scale=SCALE, k_factor=K_FACTOR, half_life_days=SLOW_HALF_LIFE)
        elo_fast = EloModel(scale=SCALE, k_factor=K_FACTOR, half_life_days=FAST_HALF_LIFE)
        res = run_dual_elo_walkforward(matches, elo_slow, elo_fast, lam)
        results.append({"lambda": lam, "log_loss": res["log_loss"], "brier": res["brier"]})
        print(f"  {lam:.1f}  ->  {fmt(res['log_loss'])}  ({fmt(res['brier'])})")
        if best is None or res["log_loss"] < best["log_loss"]:
            best = {"lambda": lam, "log_loss": res["log_loss"], "brier": res["brier"], "calibration": res["calibration"]}

    print(f"\nPARAS LAMBDA: {best['lambda']}")
    print(f"  log_loss={fmt(best['log_loss'])}  (yksittainen Elo: {fmt(single['log_loss'])})")

    print("\n=== TEESIN TARKISTUS ===")
    if best["lambda"] <= 0.1:
        print(f"  TEESI EI PIDA PAIKKAANSA tassa datassa: paras lambda on {best['lambda']} (lahella 0).")
        print("  Tuoreen forman korostaminen ei tuo mitattavaa etua 4 kk:n ikkunassa.")
        print("  Tama raportoidaan suoraan, ei etsita kiertotieta.")
    elif best["log_loss"] < single["log_loss"]:
        improvement = single["log_loss"] - best["log_loss"]
        print(f"  Teesi saa TUKEA: lambda={best['lambda']} parantaa log lossia {improvement:.4f} verran")
        print(f"  yksittaiseen (ei-vaimenevaan) Elohon verrattuna.")
    else:
        print("  Sekava tulos: paras lambda > 0.1 mutta ei silti paranna yksittaista Eloa - katso lukuja tarkemmin.")

    report = {
        "n_matches": len(matches),
        "scale": SCALE, "k_factor": K_FACTOR,
        "slow_half_life_days": SLOW_HALF_LIFE, "fast_half_life_days": FAST_HALF_LIFE,
        "single_elo_log_loss": single["log_loss"],
        "best_lambda": best["lambda"],
        "best_log_loss": best["log_loss"],
        "best_brier": best["brier"],
        "lambda_grid": results,
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nRaportti tallennettu: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
