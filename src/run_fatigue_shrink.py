"""
Vasymysvaikutus (kayttajan pyynnosta "think outside the box" - uusi idea).
Vastakohta "ruostumis"-korjaukselle (joka kutistaa ennustetta jos suosikki
EI ole pelannut viimeisen 5 vrk:n aikana): tassa testataan LIIKAA otteluita
lyhyessa ajassa.

BUGI LOYDETTY JA KORJATTU TASSA (2026-09-20, kayttajan huomio Vitaly-FURIA-
ottelusta): alkuperainen versio tarkisti VAIN suosikin vasymyksen absoluut-
tisesti (>=2 ottelua 24h sisalla), EI VERRANNUT sita altavastaajan
vasymykseen. Kayttaja huomasi ettei Vitaly-FURIA-otteluun sopinut tama -
MOLEMMAT olivat pelanneet tasan 2 ottelua 24h sisalla, silti korjaus
kutisti VAIN suosikkia (Vitalya) kohti 0.5:ta, mika ei ole perusteltua
kun altavastaaja on YHTA vasynyt. Testataan nyt KAKSI varianttia:
  (A) VANHA (absoluuttinen): suosikki vasynyt >= kynnys, altavastajaa ei
      huomioida - TAMA ON NYKYINEN TUOTANTO, osoittautui puutteelliseksi.
  (B) UUSI (differentiaalinen): suosikki vasynyt >= kynnys JA suosikki on
      AIDOSTI vasyneempi kuin altavastaaja (fav_n > opp_n) - korjaa
      havaitun ongelman, koska symmetrinen vasymys (molemmat yhta
      vasyneita) ei enaa laukaise korjausta.

METODOLOGIA: sama kuin muut - kiintea ikkuna (24h) ja kynnys (>=2 ottelua
= tama on jo 3.+ ottelu samassa ikkunassa), VAIN shrink-kerroin grid-
haetaan train-datalla, validointi nakemattomalla test-osiolla, molemmat
variantit rinnakkain."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection  # noqa: E402
from backtest import (  # noqa: E402
    EloModel,
    count_recent_matches,
    deduplicate_matches,
    load_clean_matches,
    load_best_elo_params,
    log_loss,
    production_k_override,
)
from run_tournament_effects import build_recent_match_index  # noqa: E402
from team_names import load_top50_names  # noqa: E402

FATIGUE_WINDOW_DAYS = 1.0  # 24h
FATIGUE_THRESHOLD = 2  # >=2 ottelua edeltavan 24h aikana = tama on jo 3.+
SHRINK_CANDIDATES = [round(0.0 + 0.1 * i, 1) for i in range(16)]  # 0.0 .. 1.5


def ll(pairs):
    if not pairs:
        return float("nan")
    return log_loss([y for _, y in pairs], [p for p, _ in pairs])


def shrunk(p, fatigued, shrink):
    return 0.5 + (p - 0.5) * shrink if fatigued else p


def run_variant(name, records, flag_idx):
    """records: list of (raw_p, fatigued_A, fatigued_B, y, is_test).
    flag_idx: 1 for variantti A, 2 for variantti B."""
    print(f"\n########## VARIANTTI {name} ##########")
    n_test_fatigued = sum(1 for r in records if r[4] and r[flag_idx])
    print(f"Vasyneita test-otteluita: {n_test_fatigued}")

    print("=== Grid-haku train-datalla ===")
    results = []
    for s in SHRINK_CANDIDATES:
        train_pairs = [(shrunk(r[0], r[flag_idx], s), r[3]) for r in records if not r[4]]
        tll = ll(train_pairs)
        results.append((s, tll))
        print(f"  shrink={s:.1f}  train_log_loss={tll:.4f}")
    best_s, best_tll = min(results, key=lambda r: r[1])
    print(f"\nParas shrink (train): {best_s}  (train_log_loss={best_tll:.4f})")

    print("\n=== Testataan NAKEMATTOMALLA test-osiolla ===")
    for label, filt in [("KAIKKI", lambda r: True), ("...vasynyt", lambda r: r[flag_idx]),
                         ("...tuore", lambda r: not r[flag_idx])]:
        base_pairs = [(r[0], r[3]) for r in records if r[4] and filt(r)]
        best_pairs = [(shrunk(r[0], r[flag_idx], best_s), r[3]) for r in records if r[4] and filt(r)]
        print(f"{label:14s}  nykyinen={ll(base_pairs):.4f}  paras({best_s})={ll(best_pairs):.4f}  (n={len(base_pairs)})")

    base_all = [(r[0], r[3]) for r in records if r[4]]
    best_all = [(shrunk(r[0], r[flag_idx], best_s), r[3]) for r in records if r[4]]
    if ll(best_all) < ll(base_all):
        print(f"\n-> Variantti {name}: shrink={best_s} PARANTAA nakemattomalla datalla "
              f"({ll(base_all):.4f} -> {ll(best_all):.4f}).")
    else:
        print(f"\n-> Variantti {name}: EI paranna nakemattomalla datalla.")
    return best_s, ll(best_all), ll(base_all)


def main() -> int:
    conn = get_connection()
    matches = deduplicate_matches(load_clean_matches(conn))
    conn.close()
    top50 = load_top50_names()
    params = load_best_elo_params()
    recent_idx = build_recent_match_index(matches)

    eval_matches = [m for m in matches if m.team in top50 and m.opponent in top50]
    split_idx = int(len(eval_matches) * 0.8)
    split_date = eval_matches[split_idx].date
    print(f"Data: arviointijoukko (top75-vs-top75) {len(eval_matches)}, train/test-raja {split_date.date()}\n")

    elo = EloModel(scale=params["scale"], k_factor=params["k_factor"], half_life_days=params["half_life_days"])
    records = []  # (raw_p, fatigued_A, fatigued_B, y, is_test)
    for m in matches:
        p = elo.predict(m.team, m.opponent, m.date)
        if m.team in top50 and m.opponent in top50:
            n_team = count_recent_matches(m.team, m.date, recent_idx, FATIGUE_WINDOW_DAYS)
            n_opp = count_recent_matches(m.opponent, m.date, recent_idx, FATIGUE_WINDOW_DAYS)
            is_team_fav = p >= 0.5
            fav_n, opp_n = (n_team, n_opp) if is_team_fav else (n_opp, n_team)
            fatigued_A = fav_n >= FATIGUE_THRESHOLD  # VANHA: absoluuttinen
            fatigued_B = fav_n >= FATIGUE_THRESHOLD and fav_n > opp_n  # UUSI: differentiaalinen
            records.append((p, fatigued_A, fatigued_B, m.team_won, m.date >= split_date))
        k_override = production_k_override(params["k_factor"], m.match_type, m.team, m.opponent, top50)
        elo.update(m.team, m.opponent, m.team_won, m.date, k_override=k_override)

    run_variant("A (VANHA, absoluuttinen)", records, 1)
    run_variant("B (UUSI, differentiaalinen)", records, 2)

    return 0


if __name__ == "__main__":
    sys.exit(main())
