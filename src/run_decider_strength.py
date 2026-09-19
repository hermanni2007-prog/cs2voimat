"""
Deciderikartan idea (kayttajan pyynnosta, 2026-09-20: "do we have map-
specific data... also consider how the veto affects the game").

TARKEA EROTUS jotta talla EI tehda kehapaatelmaa: emme voi kayttaa sita,
"menikö TAMA sarja deciserille", koska se tiedetaan vasta ottelun aikana/
jalkeen - sita EI voi kayttaa ennuste-piirteena ennen ottelua (sama virhe-
tyyppi kuin ajallinen tiedon vuoto). Sen sijaan testataan PYSYVAA
joukkuekohtaista piirretta: "kuinka hyva joukkue X on ollut historiallisesti
paatoserapeleissa (Bo3:n 3. kartta)" - tama TIEDETAAN jo ennen ottelua,
koska se lasketaan VAIN kronologisesti AIEMMISTA otteluista (sama walk-
forward-periaate kuin EloModel.rating_as_of).

METODOLOGIA: sama kuin muut tama session'in korjaukset - piirteen
kerroin (beta) grid-haetaan VAIN train-datalla, validointi NAKEMATTOMALLA
test-osiolla. Esitutkimus (check_decider_feature_coverage.py, ei committoitu
- kertakayttoinen tarkistus) nayti etta n. 32% arviointiotteluista molemmilla
joukkueilla on >=3 kirjattua aiempaa deciseria - piirre siis kattaa vain
osan otteluista, loput jaavat perusennusteeseen koskemattomina.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime
from math import log
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
from team_names import load_top50_names, resolve_to_canonical  # noqa: E402

MIN_PRIOR_DECIDERS = 3
BETA_CANDIDATES = [round(-4.0 + 0.25 * i, 2) for i in range(33)]  # -4.0 .. 4.0


def ll(pairs):
    if not pairs:
        return float("nan")
    return log_loss([y for _, y in pairs], [p for p, _ in pairs])


def logit(p):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return log(p / (1 - p))


def sigmoid(x):
    if x >= 0:
        z = 1.0 / (1.0 + pow(2.718281828459045, -x))
    else:
        ez = pow(2.718281828459045, x)
        z = ez / (1.0 + ez)
    return z


def build_decider_events(conn, top50):
    """Palauttaa kronologisesti jarjestetyn listan (pvm, voittaja, haviaja)
    Bo3-sarjojen 3. kartalle (decider), vain jos kartat 1 JA 2 on loydetty."""
    map_rows = conn.execute(
        "SELECT liquipedia_page, team1, team2, match_date_utc, map_order, "
        "team1_score, team2_score FROM historical_maps WHERE team1_score IS NOT NULL"
    ).fetchall()
    series = defaultdict(list)
    for page, t1, t2, date, order, s1, s2 in map_rows:
        series[(page, t1, t2, date)].append((order, t1, t2, s1, s2))

    events = []
    for (page, t1, t2, date), maps in series.items():
        if len(maps) != 3:
            continue
        maps_sorted = sorted(maps, key=lambda x: x[0])
        order, mt1, mt2, s1, s2 = maps_sorted[2]
        if s1 == s2:
            continue
        winner_raw = mt1 if s1 > s2 else mt2
        loser_raw = mt2 if s1 > s2 else mt1
        w = resolve_to_canonical(winner_raw, top50)
        l = resolve_to_canonical(loser_raw, top50)
        events.append((datetime.fromisoformat(date), w, l))
    events.sort(key=lambda x: x[0])
    return events


def decider_rate(record, team, min_n=MIN_PRIOR_DECIDERS):
    w, l = record[team]
    n = w + l
    if n < min_n:
        return None
    return (w + 2) / (n + 4)  # Beta(2,2)-kutistus


def main() -> int:
    conn = get_connection()
    all_matches = deduplicate_matches(load_clean_matches(conn))
    top50 = load_top50_names()
    decider_events = build_decider_events(conn, top50)
    conn.close()
    params = load_best_elo_params()

    print(f"Deciderien kokonaismaara (Bo3, molemmat kartta1/2 loi): {len(decider_events)}")

    eval_matches = [m for m in all_matches if m.team in top50 and m.opponent in top50]
    split_idx = int(len(eval_matches) * 0.8)
    split_date = eval_matches[split_idx].date
    print(f"Data: arviointijoukko (top75-vs-top75) {len(eval_matches)}, train/test-raja {split_date.date()}\n")

    decider_record = defaultdict(lambda: [0, 0])
    decider_idx = 0

    elo = EloModel(scale=params["scale"], k_factor=params["k_factor"], half_life_days=params["half_life_days"])
    records = []  # (raw_p, diff_or_None, y, is_test)
    for m in all_matches:
        while decider_idx < len(decider_events) and decider_events[decider_idx][0] < m.date:
            _, w, l = decider_events[decider_idx]
            decider_record[w][0] += 1
            decider_record[l][1] += 1
            decider_idx += 1

        if m.team in top50 and m.opponent in top50:
            p = elo.predict(m.team, m.opponent, m.date)
            rt = decider_rate(decider_record, m.team)
            ro = decider_rate(decider_record, m.opponent)
            diff = (rt - ro) if (rt is not None and ro is not None) else None
            records.append((p, diff, m.team_won, m.date >= split_date))

        k_override = production_k_override(params["k_factor"], m.match_type, m.team, m.opponent, top50)
        elo.update(m.team, m.opponent, m.team_won, m.date, k_override=k_override)

    n_test_covered = sum(1 for r in records if r[3] and r[1] is not None)
    n_test_total = sum(1 for r in records if r[3])
    print(f"Test-otteluita: {n_test_total}, joista piirre kaytettavissa: {n_test_covered} "
          f"({n_test_covered/n_test_total*100:.1f}%)\n")

    def adjusted(p, diff, beta):
        if diff is None:
            return p
        return sigmoid(logit(p) + beta * diff)

    print("=== Grid-haku train-datalla (vain rivit joissa piirre kaytettavissa) ===")
    results = []
    for beta in BETA_CANDIDATES:
        train_pairs = [(adjusted(p, diff, beta), y) for p, diff, y, is_test in records
                       if not is_test and diff is not None]
        tll = ll(train_pairs)
        results.append((beta, tll))
    best_beta, best_tll = min(results, key=lambda r: r[1])
    for beta, tll in results:
        marker = "  <- paras" if beta == best_beta else ("  <- beta=0 (ei muutosta)" if beta == 0.0 else "")
        print(f"  beta={beta:5.2f}  train_log_loss={tll:.4f}{marker}")
    print(f"\nParas beta (train): {best_beta}  (train_log_loss={best_tll:.4f})")

    print("\n=== Testataan NAKEMATTOMALLA test-osiolla ===")
    for label, filt in [("KAIKKI (koko test)", lambda r: True),
                         ("...piirre kaytett.", lambda r: r[1] is not None),
                         ("...ei kaytett.", lambda r: r[1] is None)]:
        base_pairs = [(r[0], r[2]) for r in records if r[3] and filt(r)]
        best_pairs = [(adjusted(r[0], r[1], best_beta), r[2]) for r in records if r[3] and filt(r)]
        print(f"{label:20s}  nykyinen={ll(base_pairs):.4f}  paras(beta={best_beta})={ll(best_pairs):.4f}  (n={len(base_pairs)})")

    base_covered = [(r[0], r[2]) for r in records if r[3] and r[1] is not None]
    best_covered = [(adjusted(r[0], r[1], best_beta), r[2]) for r in records if r[3] and r[1] is not None]
    if ll(best_covered) < ll(base_covered):
        print(f"\n-> decider-vahvuus-piirre (beta={best_beta}) PARANTAA kattavuusjoukossa "
              f"nakemattomalla datalla ({ll(base_covered):.4f} -> {ll(best_covered):.4f}). Harkitse kayttoonottoa.")
    else:
        print(f"\n-> EI paranna nakemattomalla datalla ({ll(base_covered):.4f} -> {ll(best_covered):.4f}) - EI oteta kayttoon.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
