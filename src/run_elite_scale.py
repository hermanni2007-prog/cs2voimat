"""
Eliittitason scale-korjaus (kayttajan huomio 2026-09-20: Bet365 antoi
Vitalylle 71.0% marginaalittomana kun mallimme antoi vain 57.3% - 13.7pp
ero sharp-kirjaan, paljon isompi kuin aiempi BBL-tapaus jossa Bet365
VAHVISTI mallin suunnan). Diagnoosi: Vitalyn SOS-suodatettu tilasto on
40/52 (76.9%) top50-vastustajia vastaan - aidosti dominoiva, rating jopa
MOUZ:n ylapuolella - kun taas FURIA on 32/57 (56.1%), selvasti heikompi.
119 pisteen Elo-ero (scale=400) tuottaa vain 57.3%, mutta Bet365:n 71.0%
vaatisi ~358 pisteen eron SAMALLA scale-arvolla - viittaa etta scale=400
(optimoitu KESKIARVONA koko top75-poolille) voi olla LIIAN ISO (liian
"tasainen") juuri ELIITTI-tason (molemmat top20) otteluille.

HYPOTEESI: pienempi scale ELIITTI-tason ennusteille (ei rating-
paivitykselle, PUHDAS ennuste-tason muunnos, sama periaate kuin muut
tama session'in korjaukset) parantaisi kalibrointia. Tama on ERI idea
kuin aiemmin hylatty LEVEA-tier-shrink (joka kutisti kohti 0.5:ta) -
tassa halutaan PAINVASTAINEN suunta ELIITILLE: LOITONTAA ennustetta
0.5:sta (suurempi erotteluteho), ei kutistaa.

METODOLOGIA: sama kuin muut - elite_scale grid-haettu VAIN train-
datalla (koko datasetin log loss), validointi NAKEMATTOMALLA test-
osiolla, tiereittain jotta nahdaan ettei parannus tule muiden
kustannuksella."""
from __future__ import annotations

import math
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

ELITE_RANK_CUTOFF = 20
SCALE_CANDIDATES = [50, 75, 100, 125, 150, 175, 200, 250, 300, 350, 400, 450, 500]


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

    # Rakennetaan Elo KERRAN standardi scale:lla (rating-paivitys
    # koskematon), tallennetaan seka standardiennuste etta joukkueiden
    # SENHETKISET ratingit + rank jotta voidaan jalkikateen laskea
    # elite_scale-ennuste UUDELLEEN samoista ratingeista.
    elo = EloModel(scale=params["scale"], k_factor=params["k_factor"], half_life_days=params["half_life_days"])
    records = []  # (rating_diff, is_elite, y, is_test)
    for m in all_matches:
        r_team = elo.rating_as_of(m.team, m.date)
        r_opp = elo.rating_as_of(m.opponent, m.date)
        if m.team in top50 and m.opponent in top50:
            rt = current_elo_rank(elo, m.team, m.date, candidate_names=top50)
            ro = current_elo_rank(elo, m.opponent, m.date, candidate_names=top50)
            is_elite = rt <= ELITE_RANK_CUTOFF and ro <= ELITE_RANK_CUTOFF
            records.append((r_team - r_opp, is_elite, m.team_won, m.date >= split_date))
        k_override = production_k_override(params["k_factor"], m.match_type, m.team, m.opponent, top50)
        elo.update(m.team, m.opponent, m.team_won, m.date, k_override=k_override)

    n_elite_test = sum(1 for r in records if r[3] and r[1])
    print(f"ELIITTI-tason (molemmat top20) test-otteluita: {n_elite_test}\n")

    def predict(diff, scale):
        return 1.0 / (1.0 + math.exp(-diff / scale))

    def p_with_elite_scale(diff, is_elite, scale, elite_scale):
        s = elite_scale if is_elite else scale
        return predict(diff, s)

    print("=== Grid-haku train-datalla (ELIITTI-tason oma scale) ===")
    results = []
    for es in SCALE_CANDIDATES:
        train_pairs = [(p_with_elite_scale(diff, is_elite, params["scale"], es), y)
                       for diff, is_elite, y, is_test in records if not is_test]
        tll = ll(train_pairs)
        results.append((es, tll))
        marker = "  <- nykyinen (sama kuin globaali)" if es == params["scale"] else ""
        print(f"  elite_scale={es:4d}  train_log_loss={tll:.4f}{marker}")
    best_es, best_tll = min(results, key=lambda r: r[1])
    print(f"\nParas elite_scale (train): {best_es}  (train_log_loss={best_tll:.4f})")

    def test_pairs_for(es):
        return [(p_with_elite_scale(diff, is_elite, params["scale"], es), y)
                for diff, is_elite, y, is_test in records if is_test]

    def test_pairs_for_bucket(es, want_elite):
        return [(p_with_elite_scale(diff, is_elite, params["scale"], es), y)
                for diff, is_elite, y, is_test in records if is_test and is_elite == want_elite]

    base_all = test_pairs_for(params["scale"])
    best_all = test_pairs_for(best_es)
    base_elite = test_pairs_for_bucket(params["scale"], True)
    best_elite = test_pairs_for_bucket(best_es, True)
    base_other = test_pairs_for_bucket(params["scale"], False)
    best_other = test_pairs_for_bucket(best_es, False)

    print(f"\n=== Testataan NAKEMATTOMALLA test-osiolla ===")
    print(f"{'':20s}  {'Nykyinen ('+str(int(params['scale']))+')':>16}  {'Paras ('+str(best_es)+')':>16}")
    print(f"{'KAIKKI':20s}  {ll(base_all):16.4f}  {ll(best_all):16.4f}   (n={len(base_all)})")
    print(f"{'...ELIITTI':20s}  {ll(base_elite):16.4f}  {ll(best_elite):16.4f}   (n={len(base_elite)})")
    print(f"{'...MUUT':20s}  {ll(base_other):16.4f}  {ll(best_other):16.4f}   (n={len(base_other)})")

    if ll(best_all) < ll(base_all) and ll(best_other) == ll(base_other):
        print(f"\n-> elite_scale={best_es} PARANTAA puhtaasti (muut tierit koskemattomia). Harkitse kayttoonottoa.")
    elif ll(best_all) < ll(base_all):
        print(f"\n-> elite_scale={best_es} parantaa kokonaisuutta, MUTTA vaikuttaa myos muihin tiereihin - tarkista ettei ole vahinkovaikutusta.")
    else:
        print(f"\n-> EI paranna nakemattomalla datalla - EI kannata ottaa kayttoon.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
