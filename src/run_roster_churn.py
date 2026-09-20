"""
Rosterin "myllerrys"-piirre (idea #4, kayttajan pyynnosta 2026-09-20).

ALKUPERAINEN IDEA oli tarkka "nykyisen aloitusviisikon yhtenaisyysaika",
mutta team_rosters-taulussa EI ole rooli-/asemakenttaa (pelaaja vs.
valmentaja/analyytikko erottamaton) - monella joukkueella "aktiivisia"
rivejä on 10-15 (ks. tarkistus 2026-09-20), joten "5 pelaajaa juuri nyt"
ei ole luotettavasti poimittavissa. KARKEAMPI, VANKEMPI vaihtoehto joka
EI vaadi roolierottelua: lasketaan KAIKKI liittymis-/lahtotapahtumat
(mukaan lukien valmentajat - jos organisaatiotason myllerrys sinänsä on
signaali, tama nakee sen silti).

PIIRRE: churn_count(team, D) = liittymis- TAI lahtotapahtumien lukumaara
edeltavan WINDOW_DAYS:n aikana (join_date TAI leave_date osuu ikkunaan).
HYPOTEESI: suosikin korkea tuore myllerrys ennustaa alisuoriutumista
(organisaationa "levoton" joukkue) - kutistetaan ennustetta kohti 0.5:ta,
sama rakenne kuin muut tama session'in kutistuskokeilut.

METODOLOGIA: sama kuin muut - shrink/kynnys grid-haetaan VAIN train-
datalla, validointi NAKEMATTOMALLA test-osiolla."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
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
from team_names import load_top50_names  # noqa: E402

WINDOW_DAYS = 60
THRESHOLD_CANDIDATES = [1, 2, 3, 4, 5]
SHRINK_CANDIDATES = [round(0.0 + 0.1 * i, 1) for i in range(11)]  # 0.0 .. 1.0


def ll(pairs):
    if not pairs:
        return float("nan")
    return log_loss([y for _, y in pairs], [p for p, _ in pairs])


def load_roster_events(conn):
    """Palauttaa team -> jarjestetty lista pvm-olioita (join- ja lahtotapahtumat)."""
    rows = conn.execute("SELECT team, join_date, leave_date FROM team_rosters").fetchall()
    events = {}
    for team, join_date, leave_date in rows:
        lst = events.setdefault(team, [])
        for d in (join_date, leave_date):
            if d:
                try:
                    lst.append(datetime.fromisoformat(d))
                except ValueError:
                    continue
    for team in events:
        events[team].sort()
    return events


def churn_count(events_for_team, as_of, window_days):
    if not events_for_team:
        return 0
    start = as_of - timedelta(days=window_days)
    return sum(1 for d in events_for_team if start <= d < as_of)


def shrunk(p, churned, shrink):
    return 0.5 + (p - 0.5) * shrink if churned else p


def main() -> int:
    conn = get_connection()
    matches = deduplicate_matches(load_clean_matches(conn))
    top50 = load_top50_names()
    roster_events = load_roster_events(conn)
    conn.close()
    params = load_best_elo_params()

    eval_matches = [m for m in matches if m.team in top50 and m.opponent in top50]
    split_idx = int(len(eval_matches) * 0.8)
    split_date = eval_matches[split_idx].date
    print(f"Data: arviointijoukko (top75-vs-top75) {len(eval_matches)}, train/test-raja {split_date.date()}\n")

    elo = EloModel(scale=params["scale"], k_factor=params["k_factor"], half_life_days=params["half_life_days"])
    records = []  # (raw_p, fav_churn, y, is_test)
    for m in matches:
        p = elo.predict(m.team, m.opponent, m.date)
        if m.team in top50 and m.opponent in top50:
            as_of = m.date.replace(tzinfo=None)
            n_team = churn_count(roster_events.get(m.team, []), as_of, WINDOW_DAYS)
            n_opp = churn_count(roster_events.get(m.opponent, []), as_of, WINDOW_DAYS)
            is_team_fav = p >= 0.5
            fav_n = n_team if is_team_fav else n_opp
            records.append((p, fav_n, m.team_won, m.date >= split_date))
        k_override = production_k_override(params["k_factor"], m.match_type, m.team, m.opponent, top50)
        elo.update(m.team, m.opponent, m.team_won, m.date, k_override=k_override)

    best = None
    print("=== Grid-haku train-datalla (kynnys x shrink) ===")
    for threshold in THRESHOLD_CANDIDATES:
        for shrink in SHRINK_CANDIDATES:
            train_pairs = [(shrunk(p, fav_n >= threshold, shrink), y) for p, fav_n, y, is_test in records if not is_test]
            tll = ll(train_pairs)
            if best is None or tll < best[2]:
                best = (threshold, shrink, tll)
    best_threshold, best_shrink, best_tll = best
    print(f"Paras (train): kynnys={best_threshold}  shrink={best_shrink}  train_log_loss={best_tll:.4f}")

    n_test_flagged = sum(1 for p, fav_n, y, is_test in records if is_test and fav_n >= best_threshold)
    n_test_total = sum(1 for p, fav_n, y, is_test in records if is_test)
    print(f"Test-otteluita: {n_test_total}, joista suosikilla myllerrys>=kynnys: {n_test_flagged}\n")

    print("=== Testataan NAKEMATTOMALLA test-osiolla ===")
    base_all = [(p, y) for p, fav_n, y, is_test in records if is_test]
    best_all = [(shrunk(p, fav_n >= best_threshold, best_shrink), y) for p, fav_n, y, is_test in records if is_test]
    base_flagged = [(p, y) for p, fav_n, y, is_test in records if is_test and fav_n >= best_threshold]
    best_flagged = [(shrunk(p, True, best_shrink), y) for p, fav_n, y, is_test in records if is_test and fav_n >= best_threshold]

    print(f"KAIKKI          nykyinen={ll(base_all):.4f}  paras={ll(best_all):.4f}  (n={len(base_all)})")
    print(f"...myllerrys    nykyinen={ll(base_flagged):.4f}  paras={ll(best_flagged):.4f}  (n={len(base_flagged)})")

    if ll(best_all) < ll(base_all):
        print(f"\n-> Rosterin myllerrys-piirre (kynnys={best_threshold}, shrink={best_shrink}) PARANTAA "
              f"nakemattomalla datalla ({ll(base_all):.4f} -> {ll(best_all):.4f}). Harkitse kayttoonottoa.")
    else:
        print(f"\n-> EI paranna nakemattomalla datalla ({ll(base_all):.4f} -> {ll(best_all):.4f}) - EI oteta kayttoon.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
