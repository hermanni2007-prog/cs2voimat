"""
Turnausvaiheen vaikutus (kayttajan pyynnosta "think outside the box" -
uusi idea, ei aiemmin kokeiltu tassa muodossa). Hypoteesi: sama logiikka
kuin validoitu ONLINE_SHRINK - joukkueet ottavat karsinnat/ryhmavaiheen
kevyemmin (vahemman valmistautumista, enemman kokeiluja/stand-ineja) kuin
pudotuspelit/finaalit, joten mallin ennuste voi olla epaluotettavampi
Qualifier/Group-vaiheissa kuin Playoffs-vaiheissa.

Data jo olemassa: historical_matches.tournament sisaltaa tekstimuotoisen
vaiheen (esim. "... Qual", "... Group A", "... Playoffs", "... Group
Stage") - pelkkaa tekstinluokittelua, ei uutta keraysta.

METODOLOGIA: sama kuin kaikki muut tama session'in korjaukset - puhdas
ENNUSTE-tason shrink (ei kosketa Elo-paivitysta), grid-haku VAIN train-
datalla, validointi NAKEMATTOMALLA test-osiolla, tiereittain (Qualifier/
Group vs Playoffs/muu) jotta nahdaan tuleeko parannus yhden ryhman
kustannuksella."""
from __future__ import annotations

import re
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
    production_k_override,
)
from team_names import load_top50_names  # noqa: E402

SHRINK_CANDIDATES = [round(0.0 + 0.1 * i, 1) for i in range(16)]  # 0.0 .. 1.5


def classify_stage(tournament: str) -> str:
    if not tournament:
        return "MUU"
    t = tournament.lower()
    if re.search(r"\bqual", t):
        return "QUALIFIER"
    if re.search(r"\bgroup\b", t):
        return "GROUP"
    if re.search(r"\bplayoffs?\b|\bfinals?\b|\bgrand final", t):
        return "PLAYOFFS"
    return "MUU"


def ll(pairs):
    if not pairs:
        return float("nan")
    return log_loss([y for _, y in pairs], [p for p, _ in pairs])


def main() -> int:
    conn = get_connection()
    all_matches_raw = conn.execute(
        """SELECT match_date_utc, team, opponent, tournament, score_team, score_opponent
           FROM historical_matches
           WHERE score_team IS NOT NULL AND score_opponent IS NOT NULL AND score_team != score_opponent
           ORDER BY match_date_utc ASC"""
    ).fetchall()
    matches = deduplicate_matches(load_clean_matches(conn))
    conn.close()
    top50 = load_top50_names()
    params = load_best_elo_params()

    # tournament-kentan haku match-oliolle (MatchRow ei sailyta sita)
    tourn_lookup = {}
    from datetime import datetime
    for date_s, team, opp, tournament, st, so in all_matches_raw:
        tourn_lookup[(datetime.fromisoformat(date_s), team, opp)] = tournament

    eval_matches = [m for m in matches if m.team in top50 and m.opponent in top50]
    split_idx = int(len(eval_matches) * 0.8)
    split_date = eval_matches[split_idx].date
    print(f"Data: arviointijoukko (top75-vs-top75) {len(eval_matches)}, train/test-raja {split_date.date()}\n")

    elo = EloModel(scale=params["scale"], k_factor=params["k_factor"], half_life_days=params["half_life_days"])
    records = []  # (raw_p, stage, y, is_test)
    for m in matches:
        p = elo.predict(m.team, m.opponent, m.date)
        if m.team in top50 and m.opponent in top50:
            tournament = tourn_lookup.get((m.date, m.team, m.opponent), "")
            stage = classify_stage(tournament)
            records.append((p, stage, m.team_won, m.date >= split_date))
        k_override = production_k_override(params["k_factor"], m.match_type, m.team, m.opponent, top50)
        elo.update(m.team, m.opponent, m.team_won, m.date, k_override=k_override)

    print("Jakauma (koko arviointijoukko):")
    from collections import Counter
    for stage, n in Counter(s for _, s, _, _ in records).items():
        print(f"  {stage}: {n}")

    print("\n=== DIAGNOOSI: nykyisen mallin kalibrointi vaiheittain (test-osio) ===")
    for stage in ("QUALIFIER", "GROUP", "PLAYOFFS", "MUU"):
        pairs = [(p, y) for p, s, y, is_test in records if is_test and s == stage]
        if pairs:
            print(f"  {stage:12s}  n={len(pairs):4d}  log_loss={ll(pairs):.4f}")

    def shrunk(p, stage, shrink):
        return 0.5 + (p - 0.5) * shrink if stage in ("QUALIFIER", "GROUP") else p

    print("\n=== Grid-haku train-datalla (QUALIFIER+GROUP-otteluiden shrink) ===")
    results = []
    for s in SHRINK_CANDIDATES:
        train_pairs = [(shrunk(p, stage, s), y) for p, stage, y, is_test in records if not is_test]
        tll = ll(train_pairs)
        results.append((s, tll))
        print(f"  shrink={s:.1f}  train_log_loss={tll:.4f}")
    best_s, best_tll = min(results, key=lambda r: r[1])
    print(f"\nParas shrink (train): {best_s}  (train_log_loss={best_tll:.4f})")

    print(f"\n=== Testataan NAKEMATTOMALLA test-osiolla ===")
    for label, filt_stages in [("KAIKKI", None), ("...QUALIFIER+GROUP", ("QUALIFIER", "GROUP")),
                                ("...PLAYOFFS+MUU", ("PLAYOFFS", "MUU"))]:
        if filt_stages is None:
            base_pairs = [(p, y) for p, s, y, is_test in records if is_test]
            best_pairs = [(shrunk(p, s, best_s), y) for p, s, y, is_test in records if is_test]
        else:
            base_pairs = [(p, y) for p, s, y, is_test in records if is_test and s in filt_stages]
            best_pairs = [(shrunk(p, s, best_s), y) for p, s, y, is_test in records if is_test and s in filt_stages]
        print(f"{label:22s}  nykyinen={ll(base_pairs):.4f}  paras({best_s})={ll(best_pairs):.4f}  (n={len(base_pairs)})")

    base_all = [(p, y) for p, s, y, is_test in records if is_test]
    best_all = [(shrunk(p, s, best_s), y) for p, s, y, is_test in records if is_test]
    if ll(best_all) < ll(base_all):
        print(f"\n-> shrink={best_s} PARANTAA nakemattomalla datalla ({ll(base_all):.4f} -> {ll(best_all):.4f}). Harkitse kayttoonottoa.")
    else:
        print(f"\n-> EI paranna nakemattomalla datalla - EI kannata ottaa kayttoon.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
