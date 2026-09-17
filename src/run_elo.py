"""
Tehtava 3: karttatason Elo - fittaa (scale, k_factor, half_life_days)
grid-haulla walk-forward log lossia minimoiden, ja vertaa tulosta
Tehtava 2:n tyhmiin perusmalleihin.

HUOM: Elo on TILALLINEN malli - vaatii deduplikoidun, aikajarjestetyn
ottelulistan (backtest.deduplicate_matches). Tama PUOLITTAA suunnilleen
naytemaaran verrattuna run_backtest.py:n tyhmien mallien ajoon (jotka
kayttavat kaikkia riveja, peilikuvat mukaan lukien) - log loss -luvut
EIVAT siis ole suoraan keskenaan vertailukelpoisia ilman varovaisuutta.
Tassa skriptissa tyhmat mallit ajetaan UUDELLEEN samalla deduplikoidulla
datalla, jotta vertailu on reilu.
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
    VrsRankings,
    deduplicate_matches,
    load_clean_matches,
    make_predict_vrs_soft,
    predict_5050,
    predict_higher_vrs_rank,
    run_elo_walkforward,
    run_walk_forward,
)

VRS_PATH = ROOT / "data" / "vrs_snapshots.json"
REPORT_PATH = ROOT / "data" / "elo_report.json"


def fmt(v):
    return "n/a" if v is None else f"{v:.4f}"


def main() -> int:
    conn = get_connection()
    all_matches = load_clean_matches(conn)
    matches = deduplicate_matches(all_matches)
    print(f"Ottelut: {len(all_matches)} kaikkiaan -> {len(matches)} deduplikoinnin jalkeen")

    vrs_raw = json.loads(VRS_PATH.read_text(encoding="utf-8"))
    vrs = VrsRankings(vrs_raw)

    # --- uudelleenaja tyhmat mallit SAMALLA deduplikoidulla datalla, reilu vertailu ---
    baseline_results = {}
    for name, fn in [("aina_5050", predict_5050), ("korkeampi_vrs_sija_deterministinen", predict_higher_vrs_rank)]:
        res = run_walk_forward(matches, fn, vrs)
        baseline_results[name] = res
        print(f"{name}: log_loss={fmt(res['log_loss'])} brier={fmt(res['brier'])}")

    # --- Tehtava 2 -korjaus: pehmea VRS-sijaeromalli, scale grid-haulla ---
    best_soft = None
    for soft_scale in [8, 16, 32, 48, 75, 100, 150, 200, 300, 500, 750, 1000]:
        res = run_walk_forward(matches, make_predict_vrs_soft(soft_scale), vrs)
        if best_soft is None or res["log_loss"] < best_soft["log_loss"]:
            best_soft = {"scale": soft_scale, **res}
    baseline_results["korkeampi_vrs_sija_pehmea"] = best_soft
    print(f"korkeampi_vrs_sija_pehmea (scale={best_soft['scale']}): log_loss={fmt(best_soft['log_loss'])} brier={fmt(best_soft['brier'])}")

    # --- grid search Elolle ---
    scales = [100, 150, 200, 300, 400, 500]
    ks = [8, 16, 24, 32, 48, 64, 96]
    half_lives = [30, 60, 90, 180, 99999]  # 99999 ~ ei vaimennusta

    best = None
    grid_results = []
    print("\nGrid search (scale, k, half_life_days) -> log_loss:")
    for scale in scales:
        for k in ks:
            for hl in half_lives:
                elo = EloModel(scale=scale, k_factor=k, half_life_days=hl)
                res = run_elo_walkforward(matches, elo)
                grid_results.append({"scale": scale, "k": k, "half_life_days": hl, "log_loss": res["log_loss"], "brier": res["brier"]})
                if best is None or res["log_loss"] < best["log_loss"]:
                    best = {"scale": scale, "k": k, "half_life_days": hl, "log_loss": res["log_loss"], "brier": res["brier"], "calibration": res["calibration"]}

    print(f"\nPARAS: scale={best['scale']} k={best['k']} half_life_days={best['half_life_days']}")
    print(f"  log_loss={fmt(best['log_loss'])}  brier={fmt(best['brier'])}")
    print("  Kalibrointi:")
    for b in best["calibration"]:
        if b["n"] == 0:
            continue
        print(f"    {b['range']}  n={b['n']:<4d}  ennustettu={b['avg_predicted']:.2f}  toteutunut={b['avg_actual']:.2f}")

    print("\n=== VERTAILU (sama deduplikoitu data) ===")
    print(f"  aina_5050:                    {fmt(baseline_results['aina_5050']['log_loss'])}")
    print(f"  korkeampi_vrs_sija (0/1):     {fmt(baseline_results['korkeampi_vrs_sija_deterministinen']['log_loss'])}")
    print(f"  korkeampi_vrs_sija (pehmea):  {fmt(baseline_results['korkeampi_vrs_sija_pehmea']['log_loss'])}")
    print(f"  Elo (paras):                  {fmt(best['log_loss'])}")

    ll_5050 = baseline_results["aina_5050"]["log_loss"]
    ll_vrs_soft = baseline_results["korkeampi_vrs_sija_pehmea"]["log_loss"]
    if best["log_loss"] < ll_5050:
        print("  -> Elo VOITTAA 50/50-perusmallin.")
    else:
        print("  -> Elo EI voita edes 50/50-perusmallia - jotain on vialla.")
    if best["log_loss"] < ll_vrs_soft:
        print("  -> Elo voittaa myos pehman VRS-sijamallin: kierrostason/karttatason ratingilla on lisaarvoa pelkan VRS-sijan paalla.")
    else:
        print("  -> Elo EI voita pehmeaa VRS-mallia - Elo ei viela tuo lisaarvoa VRS-sijoituksen paalle.")

    report = {
        "n_matches": len(matches),
        "best_params": {"scale": best["scale"], "k": best["k"], "half_life_days": best["half_life_days"]},
        "best_log_loss": best["log_loss"],
        "best_brier": best["brier"],
        "calibration": best["calibration"],
        "baseline_5050_log_loss": ll_5050,
        "baseline_vrs_deterministic_log_loss": baseline_results["korkeampi_vrs_sija_deterministinen"]["log_loss"],
        "baseline_vrs_soft_log_loss": ll_vrs_soft,
        "baseline_vrs_soft_scale": baseline_results["korkeampi_vrs_sija_pehmea"]["scale"],
        "grid_results": grid_results,
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nRaportti tallennettu: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
