"""
Tehtava 3: Elo-mallin perusparametrien (scale, k_factor, half_life_days)
grid-haku - PAIVITETTY 2026-09-18 (kayttajan pyynnosta, huomautuksen
jalkeen "huomioithan aina etta ei saa olla kehapaatelmia?").

LOYDETTY METODOLOGINEN AUKKO tassa TASMALLEEN TASSA skriptissa: vanha
versio valitsi (scale,k,half_life) minimoimalla walk-forward-log lossin
KOKO datasetin ylitse, ja RAPORTOI saman koko-datasetin log lossin
"tuloksena" - eli hyperparametrit fitattiin JA "validoitiin" samalla
datalla. Tama on tasan se kehapaatelma jota projektin muu metodologia
(SOS-filtteri, online-korjaus, LEVEA-shrink, jne.) on huolellisesti
valttanyt kronologisella train/test-jaolla - vain tama, mallin KAIKKEIN
perustavin viritys, oli jaanyt vanhaksi/tarkistamatta kun muu koodi
kehittyi tiukemmaksi. Sama aukko koski myos pehmean VRS-sijamallin
soft_scale-hyperparametria.

KORJATTU: KAIKKI hyperparametrien haku (Elo: scale/k/half_life, VRS-
pehmea: soft_scale) valitaan nyt VAIN train-osiolla (ensimmainen 80%
kronologisesti, top75-vs-top75 -otteluista), raportoitu tulos on AINA
NAKEMATTOMAN test-osion (viimeinen 20%) log loss - ei koskaan sama data
jolla valittiin.

MYOS: walk-forward kayttaa nyt production_k_override():a (online- +
SOS-painotus), koska nama mekanismit ovat nyt osa tuotantopolkua - vanha
versio kaytti paljasta EloModel.update():a ilman kumpaakaan, joten
(scale,k,half_life) oli viritetty ERI dynamiikalle kuin mita mallissa
oikeasti ajetaan."""
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
    brier_score,
    calibration_curve,
    deduplicate_matches,
    load_clean_matches,
    log_loss,
    make_predict_vrs_soft,
    predict_5050,
    predict_higher_vrs_rank,
    production_k_override,
    run_walk_forward,
)
from team_names import load_top50_names  # noqa: E402

VRS_PATH = ROOT / "data" / "vrs_snapshots.json"
REPORT_PATH = ROOT / "data" / "elo_report.json"


def fmt(v):
    return "n/a" if v is None else f"{v:.4f}"


def ll(pairs):
    if not pairs:
        return float("nan")
    return log_loss([y for _, y in pairs], [p for p, _ in pairs])


def elo_walkforward_production(matches: list, elo: EloModel, top50_names: set,
                                split_date, eval_keys: set):
    """Kayy KAIKKI ottelut lapi (ei vain top75-vs-top75, koska production_
    k_override huolehtii painotuksesta - sama kuin predict_match.py:ssa),
    mutta kirjaa (p, y) -parit VAIN eval_keys-joukon otteluille (top75-vs-
    top75), jaettuna train/test-osiin split_date:n mukaan."""
    train_pairs, test_pairs = [], []
    for m in matches:
        p = elo.predict(m.team, m.opponent, m.date)
        if (m.date, m.team, m.opponent) in eval_keys:
            pair = (p, m.team_won)
            (test_pairs if m.date >= split_date else train_pairs).append(pair)
        k_override = production_k_override(elo.k, m.match_type, m.team, m.opponent, top50_names)
        elo.update(m.team, m.opponent, m.team_won, m.date, k_override=k_override)
    return train_pairs, test_pairs


def main() -> int:
    conn = get_connection()
    all_matches = load_clean_matches(conn)
    matches = deduplicate_matches(all_matches)
    conn.close()
    print(f"Ottelut: {len(all_matches)} kaikkiaan -> {len(matches)} deduplikoinnin jalkeen")

    top50_names = load_top50_names()
    eval_matches = [m for m in matches if m.team in top50_names and m.opponent in top50_names]
    split_idx = int(len(eval_matches) * 0.8)
    split_date = eval_matches[split_idx].date
    eval_keys = {(m.date, m.team, m.opponent) for m in eval_matches}
    train_eval = [m for m in eval_matches if m.date < split_date]
    test_eval = [m for m in eval_matches if m.date >= split_date]
    print(f"Arviointijoukko (top75-vs-top75): {len(eval_matches)} ottelua, "
          f"train/test-raja {split_date.date()} (train={len(train_eval)}, test={len(test_eval)})\n")

    vrs_raw = json.loads(VRS_PATH.read_text(encoding="utf-8"))
    vrs = VrsRankings(vrs_raw)

    # --- tyhmat, parametrittomat perusmallit: ei fitattavaa, raportoidaan
    # silti train/test erikseen lapinakyvyyden vuoksi (ei kehapaatelmariskia
    # koska naissa ei valita mitaan datan perusteella) ---
    baseline_results = {}
    for name, fn in [("aina_5050", predict_5050), ("korkeampi_vrs_sija_deterministinen", predict_higher_vrs_rank)]:
        train_res = run_walk_forward(train_eval, fn, vrs)
        test_res = run_walk_forward(test_eval, fn, vrs)
        baseline_results[name] = {"train": train_res, "test": test_res}
        print(f"{name}: train_log_loss={fmt(train_res['log_loss'])}  test_log_loss={fmt(test_res['log_loss'])}")

    # --- VRS-pehmea: soft_scale ON hyperparametri - valitaan VAIN train-datalla ---
    best_soft = None
    for soft_scale in [8, 16, 32, 48, 75, 100, 150, 200, 300, 500, 750, 1000]:
        res = run_walk_forward(train_eval, make_predict_vrs_soft(soft_scale), vrs)
        if best_soft is None or res["log_loss"] < best_soft["train_log_loss"]:
            best_soft = {"scale": soft_scale, "train_log_loss": res["log_loss"]}
    soft_test_res = run_walk_forward(test_eval, make_predict_vrs_soft(best_soft["scale"]), vrs)
    baseline_results["korkeampi_vrs_sija_pehmea"] = {
        "scale": best_soft["scale"], "train_log_loss": best_soft["train_log_loss"],
        "test": soft_test_res,
    }
    print(f"korkeampi_vrs_sija_pehmea (scale={best_soft['scale']}, valittu train-datalla): "
          f"test_log_loss={fmt(soft_test_res['log_loss'])}")

    # --- Elo-grid: (scale, k, half_life) VALITAAN VAIN train-datalla ---
    scales = [100, 150, 200, 300, 400, 500]
    ks = [8, 16, 24, 32, 48, 64, 96]
    half_lives = [30, 60, 90, 180, 99999]  # 99999 ~ ei vaimennusta

    best = None
    grid_results = []
    print("\nGrid search (scale, k, half_life_days) -> TRAIN log_loss (valintakriteeri):")
    for scale in scales:
        for k in ks:
            for hl in half_lives:
                elo = EloModel(scale=scale, k_factor=k, half_life_days=hl)
                train_pairs, test_pairs = elo_walkforward_production(matches, elo, top50_names, split_date, eval_keys)
                train_ll = ll(train_pairs)
                grid_results.append({"scale": scale, "k": k, "half_life_days": hl, "train_log_loss": train_ll})
                if best is None or train_ll < best["train_log_loss"]:
                    best = {"scale": scale, "k": k, "half_life_days": hl,
                            "train_log_loss": train_ll, "test_pairs": test_pairs, "train_pairs": train_pairs}

    test_outcomes = [y for _, y in best["test_pairs"]]
    test_probs = [p for p, _ in best["test_pairs"]]
    test_ll = log_loss(test_outcomes, test_probs)
    test_brier = brier_score(test_outcomes, test_probs)
    test_calibration = calibration_curve(test_outcomes, test_probs)

    print(f"\nGRID-EHDOKAS (train-datalla valittu): scale={best['scale']} k={best['k']} half_life_days={best['half_life_days']}")
    print(f"  train_log_loss={fmt(best['train_log_loss'])}")
    print(f"  NAKEMATON TEST: log_loss={fmt(test_ll)}  brier={fmt(test_brier)}  (n={len(best['test_pairs'])})")

    # --- TARKEA: verrataan grid-ehdokasta NYKYISEEN tuotantoon SAMALLA
    # nakemattomalla test-osiolla ENNEN kuin mitaan vaihdetaan - sama
    # "ehdota, validoi rehellisesti, ota kayttoon VAIN jos voittaa" -periaate
    # kuin kaikissa muissa tama session'in korjauksissa (ks. tiedoston
    # docstring). EI riita etta grid loysi train-optimin - jos se HAVIAA
    # nykyiselle tuotannolle nakemattomalla datalla, tuotantoa EI vaihdeta. ---
    deployed = None
    if REPORT_PATH.exists():
        try:
            deployed = json.loads(REPORT_PATH.read_text(encoding="utf-8"))["best_params"]
        except (KeyError, ValueError, OSError):
            deployed = None
    if deployed is None:
        deployed = {"scale": 400.0, "k": 96.0, "half_life_days": 99999.0}  # nykyinen tunnettu tuotanto

    elo_deployed = EloModel(scale=deployed["scale"], k_factor=deployed["k"], half_life_days=deployed["half_life_days"])
    _, deployed_test_pairs = elo_walkforward_production(matches, elo_deployed, top50_names, split_date, eval_keys)
    deployed_test_ll = ll(deployed_test_pairs)

    print(f"\nNYKYINEN TUOTANTO (scale={deployed['scale']} k={deployed['k']} half_life={deployed['half_life_days']}): "
          f"test_log_loss={fmt(deployed_test_ll)}  (SAMA {len(deployed_test_pairs)} ottelua)")

    if test_ll < deployed_test_ll:
        print(f"\n-> GRID-EHDOKAS VOITTAA nykyisen tuotannon nakemattomalla datalla "
              f"({fmt(deployed_test_ll)} -> {fmt(test_ll)}). OTETAAN KAYTTOON.")
        final_params = {"scale": best["scale"], "k": best["k"], "half_life_days": best["half_life_days"]}
        final_test_ll, final_brier, final_calibration = test_ll, test_brier, test_calibration
        chosen = "grid_candidate"
    else:
        print(f"\n-> Grid-ehdokas EI voita nykyista tuotantoa nakemattomalla datalla "
              f"(tuotanto {fmt(deployed_test_ll)} vs ehdokas {fmt(test_ll)}) - SAILYTETAAN nykyiset "
              f"parametrit. Tama on odotettavissa oleva, terve tulos: train-optimi pienella (n={len(best['train_pairs'])}) "
              f"train-joukolla EI aina yleisty paremmin kuin jo aiemmin toimivaksi todettu piste - juuri tasta "
              f"syysta grid-ehdokasta EI oteta kayttoon automaattisesti ilman tata tarkistusta.")
        final_params = {"scale": deployed["scale"], "k": deployed["k"], "half_life_days": deployed["half_life_days"]}
        deployed_outcomes = [y for _, y in deployed_test_pairs]
        deployed_probs = [p for p, _ in deployed_test_pairs]
        final_test_ll = deployed_test_ll
        final_brier = brier_score(deployed_outcomes, deployed_probs)
        final_calibration = calibration_curve(deployed_outcomes, deployed_probs)
        chosen = "deployed_unchanged"

    print("  Kalibrointi (test-osio, KAYTOSSA OLEVAT parametrit):")
    for b in final_calibration:
        if b["n"] == 0:
            continue
        print(f"    {b['range']}  n={b['n']:<4d}  ennustettu={b['avg_predicted']:.2f}  toteutunut={b['avg_actual']:.2f}")

    print("\n=== VERTAILU (kaikki NAKEMATTOMALLA test-osiolla, sama {} ottelua) ===".format(len(test_eval)))
    print(f"  aina_5050:                    {fmt(baseline_results['aina_5050']['test']['log_loss'])}")
    print(f"  korkeampi_vrs_sija (0/1):     {fmt(baseline_results['korkeampi_vrs_sija_deterministinen']['test']['log_loss'])}")
    print(f"  korkeampi_vrs_sija (pehmea):  {fmt(baseline_results['korkeampi_vrs_sija_pehmea']['test']['log_loss'])}")
    print(f"  Elo (KAYTOSSA OLEVAT parametrit): {fmt(final_test_ll)}")

    ll_5050 = baseline_results["aina_5050"]["test"]["log_loss"]
    ll_vrs_soft = baseline_results["korkeampi_vrs_sija_pehmea"]["test"]["log_loss"]
    if final_test_ll < ll_5050:
        print("  -> Elo VOITTAA 50/50-perusmallin (nakemattomalla datalla).")
    else:
        print("  -> Elo EI voita edes 50/50-perusmallia nakemattomalla datalla - jotain on vialla.")
    if final_test_ll < ll_vrs_soft:
        print("  -> Elo voittaa myos pehman VRS-sijamallin (nakemattomalla datalla).")
    else:
        print("  -> Elo EI voita pehmeaa VRS-mallia nakemattomalla datalla.")

    report = {
        "methodology": "scale/k/half_life ja VRS-soft_scale VALITTU vain train-datalla (ensimm. 80% "
                        "kronologisesti top75-vs-top75-otteluista). Grid-ehdokas OTETAAN KAYTTOON VAIN jos se "
                        "voittaa NYKYISEN tuotannon samalla nakemattomalla test-osiolla (viimeinen 20%) - jos "
                        "ei voita, best_params SAILYY ennallaan eika grid-ehdokasta kirjoiteta tuotantoon (ks. "
                        "tiedoston docstring ja 'chosen'-kentta alla). Korjattu 2026-09-18.",
        "chosen": chosen,
        "n_matches": len(matches),
        "n_eval_matches_top75": len(eval_matches),
        "split_date": split_date.isoformat(),
        "best_params": final_params,
        "test_log_loss": final_test_ll,
        "test_brier": final_brier,
        "test_n": len(best["test_pairs"]),
        "test_calibration": final_calibration,
        "grid_candidate": {"scale": best["scale"], "k": best["k"], "half_life_days": best["half_life_days"],
                            "train_log_loss": best["train_log_loss"], "test_log_loss": test_ll},
        "deployed_before_this_run": deployed,
        "deployed_test_log_loss": deployed_test_ll,
        "baseline_5050_test_log_loss": ll_5050,
        "baseline_vrs_deterministic_test_log_loss": baseline_results["korkeampi_vrs_sija_deterministinen"]["test"]["log_loss"],
        "baseline_vrs_soft_test_log_loss": ll_vrs_soft,
        "baseline_vrs_soft_scale": baseline_results["korkeampi_vrs_sija_pehmea"]["scale"],
        "grid_results": grid_results,
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nRaportti tallennettu: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
