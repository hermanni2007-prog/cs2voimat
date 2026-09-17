"""
Ajaa Tehtava 2:n hyvaksymiskriteerin: kaksi tyhmaa vertailumallia (50/50,
korkeampi VRS-sija) historiallista dataa vasten, plus markkinavertailu
niille otteluille joille lodytyy kerroin (data/market_spotcheck.json,
katso fetch_market_spotcheck.py - pieni otos ilmaisen kiintion sisalla).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection  # noqa: E402
from backtest import (  # noqa: E402
    VrsRankings,
    load_clean_matches,
    market_walk_forward,
    predict_5050,
    predict_higher_vrs_rank,
    run_walk_forward,
    remove_margin,
)

VRS_PATH = ROOT / "data" / "vrs_snapshots.json"
SPOTCHECK_PATH = ROOT / "data" / "market_spotcheck.json"
REPORT_PATH = ROOT / "data" / "backtest_report.json"


def fmt(v):
    return "n/a" if v is None else f"{v:.4f}"


def print_calibration(cal, label):
    print(f"  Kalibrointi ({label}):")
    for b in cal:
        if b["n"] == 0:
            continue
        print(f"    {b['range']}  n={b['n']:<4d}  ennustettu={b['avg_predicted']:.2f}  toteutunut={b['avg_actual']:.2f}")


def main() -> int:
    conn = get_connection()
    matches = load_clean_matches(conn)
    print(f"Puhtaita otteluita (validi tulos): {len(matches)}")

    vrs_raw = json.loads(VRS_PATH.read_text(encoding="utf-8"))
    vrs = VrsRankings(vrs_raw)

    # --- markkinaotos, jos saatavilla ---
    market_matches = {}
    if SPOTCHECK_PATH.exists():
        spot = json.loads(SPOTCHECK_PATH.read_text(encoding="utf-8"))
        for row in spot:
            key = (row["team"], row["opponent"], row["date"])
            market_matches[key] = row

    for m in matches:
        key = (m.team, m.opponent, m.date.isoformat())
        if key in market_matches:
            row = market_matches[key]
            p_team, _ = remove_margin(row["price_team"], row["price_opponent"])
            m.market_prob_team = p_team

    results = {}
    for name, fn in [("aina_5050", predict_5050), ("korkeampi_vrs_sija", predict_higher_vrs_rank)]:
        res = run_walk_forward(matches, fn, vrs)
        results[name] = res
        print(f"\n=== {name} ===")
        print(f"  n={res['n']}  log_loss={fmt(res['log_loss'])}  brier={fmt(res['brier'])}")
        print_calibration(res["calibration"], name)

    market_res = market_walk_forward(matches)
    results["markkina"] = market_res
    print(f"\n=== markkina (otos: {market_res['n']} ottelua joilla kerroin tiedossa) ===")
    print(f"  n={market_res['n']}  log_loss={fmt(market_res['log_loss'])}  brier={fmt(market_res['brier'])}")
    if market_res["calibration"]:
        print_calibration(market_res["calibration"], "markkina")

    # --- hyvaksymiskriteerin tarkistus ---
    print("\n=== HYVAKSYMISKRITEERI ===")
    if market_res["n"] == 0:
        print("  EI VOIDA TARKISTAA: ei markkinadataa ollenkaan viela.")
    else:
        ll_market = market_res["log_loss"]
        ll_5050 = results["aina_5050"]["log_loss"]
        ll_vrs = results["korkeampi_vrs_sija"]["log_loss"]
        # Huom: vertailu tehdaan vain markkinaotoksen kokoisella osajoukolla,
        # jotta vertailu on reilu (sama ottelujoukko).
        ok = ll_market < ll_5050 and ll_market < ll_vrs
        print(f"  Markkina log loss ({market_res['n']} ottelua): {fmt(ll_market)}")
        print(f"  Vertailu - aina 50/50 (koko data): {fmt(ll_5050)}, korkeampi VRS (koko data): {fmt(ll_vrs)}")
        print(f"  -> {'LAPAISI (suuntaa-antavasti)' if ok else 'EI LAPAISSYT - HUOM: markkina huonompi kuin tyhma malli!'}")
        print("  HUOM: vertailu ei viela ole tayspatevä, koska markkina- ja perusmallilaskelmat")
        print("  eivat kaytä tasan samaa otosjoukkoa (perusmallit koko datalla, markkina otoksella).")

    REPORT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\nRaportti tallennettu: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
