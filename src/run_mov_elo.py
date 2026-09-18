"""
Margin-of-victory (MOV) -kerroin Elo-paivitykseen (kayttajan pyynnosta
"kehita mallia lisaa" - yksi 2026-09-17 aivoriihen nelja ideaa, ei viela
kokeiltu). Ajatus: nykyinen Elo kohtelee 2-0-lakaisua ja 2-1-ratkaisijaa
TAYSIN samalla tavalla (vain voitto/havio, K vakio) - klassinen Elo-
laajennus (esim. FiveThirtyEight NFL Elo) antaa suuremman rating-
paivityksen selvemmalle voitolle.

HUOM SKOOPISTA: historical_matches.score_team/score_opponent sisaltaa
KAHTA eri mittakaavaa sekaisin - Bo3-sarjatulos (2-0 tai 2-1, n=3834,
83% datasta) JA Bo1-kierrostulos (esim. 13-6, n=726, 16%) - naita ei voi
verrata suoraan (2-0:n "marginaali" 2 ei ole sama asia kuin 13-6:n
"marginaali" 7). Tassa kokeilussa rajoitutaan PUHTAASTI Bo3-sarja-
tuloksiin (total=2 tai 3) - selkein, yksiselitteisin MOV-signaali
(lakaisu vs ratkaisija), suurin osa datasta. Bo1/Bo5 jatetaan normaalille
K:lle koskemattomina.

METODOLOGIA: sama kuin muut - grid-haku SWEEP_K_MULTIPLIER (2-0-otteluiden
K-kerroin RATING-PAIVITYKSESSA, 2-1 pysyy aina 1.0x-kertoimena) VAIN
train-datalla (koko datasetin log loss), NAKEMATTOMALLA test-osiolla
validointi, ei kehapaatelmia."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection  # noqa: E402
from backtest import (  # noqa: E402
    EloModel,
    deduplicate_matches,
    filter_top50_only,
    load_best_elo_params,
    log_loss,
    online_k_override,
)
from team_names import load_top50_names  # noqa: E402

MULTIPLIER_CANDIDATES = [round(0.5 + 0.1 * i, 1) for i in range(21)]  # 0.5 .. 2.5


def load_matches_with_score(conn):
    """Sama kuin backtest.load_clean_matches, mutta pitaa score_team/
    score_opponent mukana MOV-luokittelua varten (normaali MatchRow ei
    sisalla niita)."""
    from datetime import datetime
    from team_names import load_top50_names as _load, resolve_to_canonical

    canonical_names = _load()
    rows = conn.execute(
        """SELECT match_date_utc, team, opponent, tier, match_type, tournament,
                  score_team, score_opponent
           FROM historical_matches
           WHERE score_team IS NOT NULL AND score_opponent IS NOT NULL
             AND score_team != score_opponent
           ORDER BY match_date_utc ASC"""
    ).fetchall()
    out = []
    for r in rows:
        date = datetime.fromisoformat(r[0])
        team_won = 1 if r[6] > r[7] else 0
        team = resolve_to_canonical(r[1], canonical_names)
        opponent = resolve_to_canonical(r[2], canonical_names)
        total = r[6] + r[7]
        is_sweep = total == 2
        is_decider = total == 3
        out.append({
            "date": date, "team": team, "opponent": opponent, "match_type": r[4],
            "team_won": team_won, "is_sweep": is_sweep, "is_decider": is_decider,
        })
    return out


def dedup(matches):
    seen = set()
    out = []
    for m in matches:
        key = (m["date"].isoformat(), None, frozenset({m["team"], m["opponent"]}))
        # HUOM: ei kayteta tournament-kenttaa avaimessa taalla (yksinkertaisuuden
        # vuoksi, sama riski kuin backtest.deduplicate_matches ilman sita ei ole -
        # tama skripti on vain MOV-diagnoosi, ei tuotantopolku).
        if key in seen:
            continue
        seen.add(key)
        out.append(m)
    return out


def ll(pairs):
    if not pairs:
        return float("nan")
    return log_loss([y for _, y in pairs], [p for p, _ in pairs])


def main() -> int:
    conn = get_connection()
    all_matches = load_matches_with_score(conn)
    conn.close()

    top50 = load_top50_names()
    matches = [m for m in all_matches if m["team"] in top50 and m["opponent"] in top50]
    matches = dedup(matches)
    matches.sort(key=lambda m: m["date"])
    params = load_best_elo_params()

    split_idx = int(len(matches) * 0.8)
    split_date = matches[split_idx]["date"]
    n_sweep = sum(1 for m in matches if m["is_sweep"])
    n_decider = sum(1 for m in matches if m["is_decider"])
    print(f"Data: {len(matches)} ottelua (2-0 lakaisuja: {n_sweep}, 2-1 ratkaisijoita: {n_decider}), "
          f"train/test-raja {split_date.date()}\n")

    def walkforward(sweep_multiplier: float):
        elo = EloModel(scale=params["scale"], k_factor=params["k_factor"], half_life_days=params["half_life_days"])
        train_pairs, test_pairs = [], []
        test_sweep, test_decider = [], []
        for m in matches:
            p = elo.predict(m["team"], m["opponent"], m["date"])
            is_test = m["date"] >= split_date
            (test_pairs if is_test else train_pairs).append((p, m["team_won"]))
            if is_test:
                if m["is_sweep"]:
                    test_sweep.append((p, m["team_won"]))
                elif m["is_decider"]:
                    test_decider.append((p, m["team_won"]))
            base_k = online_k_override(params["k_factor"], m["match_type"]) or params["k_factor"]
            k_override = base_k * sweep_multiplier if m["is_sweep"] else base_k
            elo.update(m["team"], m["opponent"], m["team_won"], m["date"], k_override=k_override)
        return train_pairs, test_pairs, test_sweep, test_decider

    print("=== Grid-haku train-datalla (2-0-lakaisujen K-multiplier, 2-1 aina 1.0x) ===")
    train_results = []
    for mult in MULTIPLIER_CANDIDATES:
        train_pairs, test_pairs, test_sweep, test_decider = walkforward(mult)
        train_ll = ll(train_pairs)
        train_results.append((mult, train_ll, test_pairs, test_sweep, test_decider))
        print(f"  sweep_multiplier={mult:.1f}  train_log_loss={train_ll:.4f}")
    best_mult, best_train_ll, best_test, best_sweep, best_decider = min(train_results, key=lambda r: r[1])
    print(f"\nParas multiplier train-datalla: {best_mult}  (train_log_loss={best_train_ll:.4f})")

    _, base_test, base_sweep, base_decider = walkforward(1.0)

    print(f"\n=== Testataan NAKEMATTOMALLA test-osiolla ===")
    print(f"{'':24s}  {'Nykyinen (1.0x)':>16}  {'Paras ('+str(best_mult)+'x)':>16}")
    for label, base, best in [
        ("KAIKKI", base_test, best_test),
        ("...vain 2-0-lakaisut", base_sweep, best_sweep),
        ("...vain 2-1-ratkaisijat", base_decider, best_decider),
    ]:
        print(f"{label:24s}  {ll(base):16.4f}  {ll(best):16.4f}   (n={len(base)})")

    ll_base_all, ll_best_all = ll(base_test), ll(best_test)
    if ll_best_all < ll_base_all:
        print(f"\n-> sweep_multiplier={best_mult} PARANTAA koko test-joukon ennustetta ({ll_base_all:.4f} -> {ll_best_all:.4f}). Kannattaa harkita kayttoonottoa.")
    else:
        print(f"\n-> sweep_multiplier={best_mult} EI paranna koko test-joukon ennustetta nakemattomalla datalla - EI kannata ottaa kayttoon.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
