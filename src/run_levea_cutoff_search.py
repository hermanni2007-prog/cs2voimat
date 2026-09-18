"""
LEVEA-tier-rajan optimointi (kayttajan valinta - vaihtoehtolistalta
"ELIITTI/LEVEA-rajan optimointi"). Nykyinen LEVEA_RANK_CUTOFF=20 tuli
kayttajan omasta kehyksesta ("top 20 vs top 70"), ei empiirisesta
hausta. LEVEA_SHRINK on TALLA HETKELLA 1.0 (no-op) - SOS-pehmennys
mitatoi alkuperaisen shrink=0.5-loydoksen CUTOFF=20:lla mitattuna.

KYSYMYS: mitatoituiko efekti KOKONAAN, vai vain SILLA yhdella rajalla
(20)? Jos raja olisi eri (esim. 15 tai 30), loytyisiko silti aito,
validoitu shrink-tarve? 2D grid-haku (rank_cutoff x shrink) - molemmat
VALITAAN train-datalla, validoidaan NAKEMATTOMALLA test-osiolla vertaamalla
NYKYISEEN tilanteeseen (ei mitaan tier-korjausta, vastaa shrink=1.0
mika tahansa cutoffilla)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection  # noqa: E402
from backtest import (  # noqa: E402
    EloModel,
    current_elo_rank,
    deduplicate_matches,
    load_clean_matches,
    load_best_elo_params,
    log_loss,
    production_k_override,
)
from team_names import load_top50_names  # noqa: E402

CUTOFF_CANDIDATES = [10, 15, 20, 25, 30, 35, 40]
SHRINK_CANDIDATES = [round(0.0 + 0.1 * i, 1) for i in range(16)]  # 0.0 .. 1.5


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

    eval_matches = [m for m in all_matches if m.team in top50 and m.opponent in top50]
    split_idx = int(len(eval_matches) * 0.8)
    split_date = eval_matches[split_idx].date
    print(f"Data: arviointijoukko (top75-vs-top75) {len(eval_matches)}, train/test-raja {split_date.date()}\n")

    # Rakennetaan Elo KERRAN (production_k_override kiinteana - ei muutu
    # cutoffin/shrinkin mukana, koska ne vaikuttavat vain ENNUSTEESEEN),
    # tallennetaan (raw_p, rank_team, rank_opp, y, is_test) jokaiselle
    # arviointiottelulle. Rank lasketaan JUURI ENNEN ottelua (kausaalinen).
    elo = EloModel(scale=params["scale"], k_factor=params["k_factor"], half_life_days=params["half_life_days"])
    records = []
    for m in all_matches:
        p = elo.predict(m.team, m.opponent, m.date)
        if m.team in top50 and m.opponent in top50:
            rt = current_elo_rank(elo, m.team, m.date, candidate_names=top50)
            ro = current_elo_rank(elo, m.opponent, m.date, candidate_names=top50)
            records.append((p, rt, ro, m.team_won, m.date >= split_date))
        k_override = production_k_override(params["k_factor"], m.match_type, m.team, m.opponent, top50)
        elo.update(m.team, m.opponent, m.team_won, m.date, k_override=k_override)

    def shrunk(p, rt, ro, cutoff, shrink):
        if rt > cutoff and ro > cutoff:
            return 0.5 + (p - 0.5) * shrink
        return p

    print("=== 2D grid-haku train-datalla (rank_cutoff x shrink) ===")
    best = None
    for cutoff in CUTOFF_CANDIDATES:
        row = []
        for s in SHRINK_CANDIDATES:
            train_pairs = [(shrunk(p, rt, ro, cutoff, s), y) for p, rt, ro, y, is_test in records if not is_test]
            tll = ll(train_pairs)
            row.append(tll)
            if best is None or tll < best[2]:
                best = (cutoff, s, tll)
        print(f"  cutoff={cutoff:3d}  " + "  ".join(f"{v:.4f}" for v in row) +
              f"   (min tallä rivillä: {min(row):.4f})")
    print(f"\n(sarakkeet shrink={SHRINK_CANDIDATES[0]}..{SHRINK_CANDIDATES[-1]})")

    best_cutoff, best_shrink, best_train_ll = best
    print(f"\nParas (train): cutoff={best_cutoff}  shrink={best_shrink}  (train_log_loss={best_train_ll:.4f})")

    def test_ll_for(cutoff, shrink):
        pairs = [(shrunk(p, rt, ro, cutoff, shrink), y) for p, rt, ro, y, is_test in records if is_test]
        return ll(pairs), len(pairs)

    baseline_ll, n = test_ll_for(20, 1.0)  # nykyinen (ei tier-korjausta)
    best_test_ll, _ = test_ll_for(best_cutoff, best_shrink)

    print(f"\n=== Testataan NAKEMATTOMALLA test-osiolla (n={n}) ===")
    print(f"  NYKYINEN (ei tier-korjausta):        log_loss = {baseline_ll:.4f}")
    print(f"  PARAS (cutoff={best_cutoff}, shrink={best_shrink}):  log_loss = {best_test_ll:.4f}")

    if best_test_ll < baseline_ll:
        print(f"\n-> LOYTYI validoitu tier-efekti (cutoff={best_cutoff}, shrink={best_shrink}). Harkitse kayttoonottoa.")
    else:
        print(f"\n-> Ei loytynyt validoitua tier-efektia millaan cutoffilla - SOS-pehmennys nayttaa "
              f"aidosti kattaneen taman ongelman kokonaan, ei vain cutoff=20:lla. EI muutosta.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
