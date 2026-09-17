"""
Vastustajien vahvuuskorjaus (SOS, strength of schedule) - kayttajan
huomio 2026-09-18: NRG:n Elo-rating nojaa 47%:sti top50-listan
ULKOPUOLISIIN vastustajiin (esim. NuTorious, Iowa Stormboar,
SportsBetExpert), kun taas esim. Aurroralla vastaava osuus on vain 7%.
Nama karsinta-/alempien sarjojen joukkueet eivat koskaan pelaa muita
top50-joukkueita keskenaan (paitsi sita yhta top50-vastustajaa jonka
kautta ne paatyvat dataamme), joten ne muodostavat Elo-verkossa
"irrallisen" osa-altaan jonka rating-taso ei ole suoraan vertailukelpoinen
paaverkon (aidosti top50-vs-top50) kanssa. Talla on pitkalti sama
mekanismi kuin klassisessa "rating pool disconnection" -ongelmassa.

HYPOTEESI: jos Elo lasketaan VAIN top50-vs-top50 -otteluista (top50-
ulkopuoliset vastustajat kokonaan pois), ennuste on tarkempi nakemattomilla
top50-vs-top50 -otteluilla kuin nykyinen malli (joka sisallyttaa kaikki
top50-joukkueen ottelut, myos ne heikkoja karsintavastustajia vastaan).

METODOLOGIA (sama kuin projektin muut korjaukset - EI kehapaatelmaa):
fitataan/rakennetaan molemmat Elo-variantit VAIN kronologisen datan
ensimmaisella 80%:lla, arvioidaan MOLEMMAT samalla nakemattomalla 20%:n
top50-vs-top50 -testijoukolla. Jos SOS-suodatettu versio ei paranna
log lossia / kalibrointia, se hylataan taysin samoin kuin taso-korjaus
aiemmin - ei etsita kiertotieta."""
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
    load_best_elo_params,
    log_loss,
    brier_score,
    calibration_curve,
)

TOP50_PATH = ROOT / "data" / "top50_teams.json"


def load_top50_names() -> set:
    return {t["name"] for t in json.loads(TOP50_PATH.read_text(encoding="utf-8"))}


def walkforward_eval(update_matches: list, eval_keys: set, elo: EloModel) -> tuple:
    """Kayy update_matches:n lapi kronologisesti (predict+update JOKAISELLE),
    mutta kerää outcome/prob vain niille riveille jotka ovat myos
    eval_keys-joukossa. eval_keys = set of (date, team, opponent)."""
    outcomes, probs = [], []
    for m in update_matches:
        p = elo.predict(m.team, m.opponent, m.date)
        if (m.date, m.team, m.opponent) in eval_keys:
            outcomes.append(m.team_won)
            probs.append(p)
        elo.update(m.team, m.opponent, m.team_won, m.date)
    return outcomes, probs


def sos_share(team: str, matches: list, top50: set) -> tuple:
    games = [m for m in matches if m.team == team or m.opponent == team]
    if not games:
        return 0, 0
    non_top50 = sum(1 for m in games if (m.opponent if m.team == team else m.team) not in top50)
    return non_top50, len(games)


def main() -> int:
    conn = get_connection()
    all_matches = deduplicate_matches(load_clean_matches(conn))
    conn.close()

    top50 = load_top50_names()
    params = load_best_elo_params()

    split_idx = int(len(all_matches) * 0.8)
    split_date = all_matches[split_idx].date
    print(f"Data: {len(all_matches)} ottelua, train/test-raja {split_date.date()} "
          f"(ensimmainen 80% fit/rakennus, viimeinen 20% NAKEMATON testi)\n")

    test_top50 = [
        m for m in all_matches
        if m.date >= split_date and m.team in top50 and m.opponent in top50
    ]
    eval_keys = {(m.date, m.team, m.opponent) for m in test_top50}
    print(f"Testijoukko (top50-vs-top50, nakematon 20%): {len(test_top50)} ottelua\n")

    # Variantti A: NYKYINEN malli - kaikki ottelut mukana Elo-paivityksessa
    elo_a = EloModel(scale=params["scale"], k_factor=params["k_factor"], half_life_days=params["half_life_days"])
    outcomes_a, probs_a = walkforward_eval(all_matches, eval_keys, elo_a)

    # Variantti B: SOS-suodatettu - VAIN top50-vs-top50 -ottelut Elo-paivityksessa
    filtered_matches = [m for m in all_matches if m.team in top50 and m.opponent in top50]
    elo_b = EloModel(scale=params["scale"], k_factor=params["k_factor"], half_life_days=params["half_life_days"])
    outcomes_b, probs_b = walkforward_eval(filtered_matches, eval_keys, elo_b)

    print("=== VARIANTTI A: nykyinen malli (kaikki ottelut Elo-paivityksessa) ===")
    print(f"  log_loss = {log_loss(outcomes_a, probs_a):.4f}   brier = {brier_score(outcomes_a, probs_a):.4f}")

    print("\n=== VARIANTTI B: SOS-suodatettu (vain top50-vs-top50 Elo-paivityksessa) ===")
    print(f"  log_loss = {log_loss(outcomes_b, probs_b):.4f}   brier = {brier_score(outcomes_b, probs_b):.4f}")

    delta = log_loss(outcomes_a, probs_a) - log_loss(outcomes_b, probs_b)
    print(f"\nEro (A - B): {delta:+.4f} log loss  ->  "
          f"{'B (SOS-suodatettu) PARANTAA ennustetta' if delta > 0 else 'B EI paranna - A on yhta hyva tai parempi'}")

    print("\n=== Kalibrointi, variantti A ===")
    for b in calibration_curve(outcomes_a, probs_a):
        if b["n"]:
            print(f"  {b['range']}: n={b['n']:3d}  ennustettu={b['avg_predicted']:.2f}  toteutunut={b['avg_actual']:.2f}")
    print("\n=== Kalibrointi, variantti B ===")
    for b in calibration_curve(outcomes_b, probs_b):
        if b["n"]:
            print(f"  {b['range']}: n={b['n']:3d}  ennustettu={b['avg_predicted']:.2f}  toteutunut={b['avg_actual']:.2f}")

    print("\n=== SOS-osuus (top50-ulkopuolisten vastustajien osuus kaikista otteluista) ===")
    for t in ["NRG", "Aurora", "MOUZ", "FURIA", "Vitality"]:
        non50, total = sos_share(t, all_matches, top50)
        print(f"  {t:12s}: {non50}/{total} ({non50/total*100:.0f}%) top50-ulkopuolisia vastustajia")

    return 0


if __name__ == "__main__":
    sys.exit(main())
