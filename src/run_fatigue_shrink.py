"""
Vasymysvaikutus (kayttajan pyynnosta "think outside the box" - uusi idea,
ei aiemmin kokeiltu). Vastakohta "ruostumis"-korjaukselle (joka kutistaa
ennustetta jos suosikki EI ole pelannut viimeisen 5 vrk:n aikana): tassa
testataan LIIKAA otteluita lyhyessa ajassa - esim. tama session'in oma
Logitech G Play Connect -round-robin (sama joukkue pelasi 3+ ottelua
samana paivana) saattaa vasyttaa suosikkia ja tehda tuloksesta
epavarmemman kuin Elo-ero antaisi ymmartaa.

Kayttaa samaa run_tournament_effects.build_recent_match_index +
backtest.count_recent_matches -infraa kuin ruostumiskorjaus, vain
KAANTEISELLA ehdolla (MONTA ottelua, ei nolla).

METODOLOGIA: sama kuin muut - kiintea ikkuna (24h) ja kynnys (>=2 ottelua
= tama on jo 3.+ ottelu samassa ikkunassa), VAIN shrink-kerroin grid-
haetaan train-datalla, validointi nakemattomalla test-osiolla."""
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
    records = []  # (raw_p, favorite_is_fatigued, y, is_test)
    for m in matches:
        p = elo.predict(m.team, m.opponent, m.date)
        if m.team in top50 and m.opponent in top50:
            n_team = count_recent_matches(m.team, m.date, recent_idx, FATIGUE_WINDOW_DAYS)
            n_opp = count_recent_matches(m.opponent, m.date, recent_idx, FATIGUE_WINDOW_DAYS)
            is_team_fav = p >= 0.5
            fav_n_recent = n_team if is_team_fav else n_opp
            fatigued = fav_n_recent >= FATIGUE_THRESHOLD
            records.append((p, fatigued, m.team_won, m.date >= split_date))
        k_override = production_k_override(params["k_factor"], m.match_type, m.team, m.opponent, top50)
        elo.update(m.team, m.opponent, m.team_won, m.date, k_override=k_override)

    n_fatigued_test = sum(1 for _, f, _, is_test in records if is_test and f)
    print(f"Vasyneita (suosikilla >={FATIGUE_THRESHOLD} ottelua {FATIGUE_WINDOW_DAYS*24:.0f}h sisalla) test-otteluita: {n_fatigued_test}\n")

    def shrunk(p, fatigued, shrink):
        return 0.5 + (p - 0.5) * shrink if fatigued else p

    print("=== Grid-haku train-datalla (vasymysshrink) ===")
    results = []
    for s in SHRINK_CANDIDATES:
        train_pairs = [(shrunk(p, f, s), y) for p, f, y, is_test in records if not is_test]
        tll = ll(train_pairs)
        results.append((s, tll))
        print(f"  shrink={s:.1f}  train_log_loss={tll:.4f}")
    best_s, best_tll = min(results, key=lambda r: r[1])
    print(f"\nParas shrink (train): {best_s}  (train_log_loss={best_tll:.4f})")

    print(f"\n=== Testataan NAKEMATTOMALLA test-osiolla ===")
    for label, filt in [("KAIKKI", lambda r: r[3]), ("...vasynyt", lambda r: r[3] and r[1]),
                         ("...tuore", lambda r: r[3] and not r[1])]:
        base_pairs = [(p, y) for p, f, y, is_test in records if filt((p, f, y, is_test))]
        best_pairs = [(shrunk(p, f, best_s), y) for p, f, y, is_test in records if filt((p, f, y, is_test))]
        print(f"{label:14s}  nykyinen={ll(base_pairs):.4f}  paras({best_s})={ll(best_pairs):.4f}  (n={len(base_pairs)})")

    base_all = [(p, y) for p, f, y, is_test in records if is_test]
    best_all = [(shrunk(p, f, best_s), y) for p, f, y, is_test in records if is_test]
    if ll(best_all) < ll(base_all):
        print(f"\n-> shrink={best_s} PARANTAA nakemattomalla datalla ({ll(base_all):.4f} -> {ll(best_all):.4f}). Harkitse kayttoonottoa.")
    else:
        print(f"\n-> EI paranna nakemattomalla datalla - EI kannata ottaa kayttoon.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
