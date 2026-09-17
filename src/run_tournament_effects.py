"""
Kaksi kayttajan ehdottamaa hypoteesia testattu OLEMASSA OLEVALLA datalla
(ei vaadi uutta keruuta):

1) UUPUMUSFAKTORI: vaikuttaako joukkueen tiheä ottelutahti (montako
   ottelua pelattu viime paivina) upset-todennakoisyyteen? Jos vaikuttaa,
   suosikin (korkeamman Elo-luvun) pitaisi havita ODOTETTUA USEAMMIN kun
   sen oma äskettainen ottelutiheys on korkea.

2) TURNAUKSEN ALKUVAIHEEN FAKTORI: onko joukkueen ENSIMMAISESSA
   ottelussa turnauksessa (tallä raakadatan tournament-merkkijonolla)
   enemman upsetteja kuin myohemmissa otteluissa samassa turnauksessa?

Molemmat mitataan kahdella tavalla: (a) raaka upset-% (suosikki havisi),
(b) Elon oma log loss bucket:eittain - jos malli on jo hyvin kalibroitu
näissä tilanteissa, log loss ei poikkea muista bucket:eista paljon
enemman kuin kohina selittaisi.

BRACKET-HIERARKIA (lisatty 2026-09-17, kayttajan pyynnosta): alkuperainen
versio kaytti raakaa "tournament"-merkkijonoa (esim. "IEM Cologne Major
2026 Stage 1 - Round 1") tapahtuman tunnisteena - saman ISON tapahtuman
eri kierrokset/lohkot nakyvat ERI merkkijonoina, joten "turnauksen
ensimmainen ottelu" tarkoitti vain "tama NIMETYN LAVAN ensimmainen
ottelu" (esim. Playoffs-lohkon oma "ensimmainen ottelu" on usein
joukkueen viides ottelu koko tapahtumassa, koska se on jo selvinnyt
ryhmavaiheesta - vaarentaa mittausta valikoitumalla vain jo-todistaneisiin
joukkueisiin).

KORJAUS: Tehtava 4:n `bracket_progress`-taulu sisaltaa jo tarvittavan
tiedon - se resolvoi 88 raakaa lava-merkkijonoa OIKEIKSI Liquipedia-
sivuiksi, ja SAMA sivu (`liquipedia_page`) osuu usein MONELLE eri
lava-merkkijonolle (esim. 19 eri "Esports World Cup 2026 - Group X/LCQ/
Playoffs" -merkkijonoa resolvoituvat kaikki samaksi "Esports World
Cup/2026" -sivuksi). Kayttamalla `liquipedia_page`:a TAPAHTUMAN
tunnisteena raakan tournament-merkkijonon sijaan (silloin kun resolvointi
on onnistunut - 88/181, loput jaavat raa'an merkkijonon varaan
fallbackina) saadaan huomattavasti oikeampi "koko tapahtuman
ensimmainen ottelu" -tunnistus ilman uutta datankeruuta."""
from __future__ import annotations

import bisect
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection  # noqa: E402
from backtest import (  # noqa: E402
    EloModel,
    deduplicate_matches,
    load_best_elo_params,
    load_clean_matches,
    log_loss,
    brier_score,
)

FATIGUE_WINDOW_DAYS = 5


def build_tournament_to_event_map(conn) -> dict:
    """raaka tournament-merkkijono -> tapahtuman tunniste. Kayttaa
    bracket_progress.liquipedia_page:a kun resolvointi onnistui (sama
    sivu = sama oikea tapahtuma vaikka lava-merkkijono eroaa), muuten
    tournament-merkkijono itse fallbackina (ei pahempi kuin ennen)."""
    rows = conn.execute(
        "SELECT tournament, liquipedia_page FROM bracket_progress "
        "WHERE status IN ('ok','ok_no_brackets') AND liquipedia_page IS NOT NULL"
    ).fetchall()
    return {t: page for t, page in rows}


def event_of(tournament: str, tournament_to_event: dict) -> str:
    return tournament_to_event.get(tournament, tournament)


def build_recent_match_index(matches: list) -> dict:
    idx: dict = {}
    for m in matches:
        idx.setdefault(m.team, []).append(m.date)
        idx.setdefault(m.opponent, []).append(m.date)
    for k in idx:
        idx[k].sort()
    return idx


def count_recent(idx: dict, team: str, as_of, window_days: int) -> int:
    dates = idx.get(team, [])
    lo = as_of - timedelta(days=window_days)
    left = bisect.bisect_left(dates, lo)
    right = bisect.bisect_left(dates, as_of)
    return right - left


def build_event_first_date_index(matches: list, tournament_to_event: dict) -> dict:
    idx: dict = {}
    for m in matches:
        event = event_of(m.tournament, tournament_to_event)
        for team in (m.team, m.opponent):
            key = (team, event)
            if key not in idx or m.date < idx[key]:
                idx[key] = m.date
    return idx


def bucket_stats(label_to_events: dict) -> None:
    for label, events in label_to_events.items():
        outcomes = [e[0] for e in events]
        probs_fav = [e[1] for e in events]  # P(suosikki voittaa) mallin mukaan
        fav_won = [e[2] for e in events]  # 1 jos suosikki oikeasti voitti
        n = len(events)
        if n == 0:
            continue
        ll = log_loss(outcomes, probs_fav)
        br = brier_score(outcomes, probs_fav)
        upset_rate = 1 - (sum(fav_won) / n)
        print(f"  {label:<28s} n={n:4d}  upset%={upset_rate*100:5.1f}  log_loss={ll:.4f}  brier={br:.4f}")


def main() -> int:
    conn = get_connection()
    matches = deduplicate_matches(load_clean_matches(conn))
    tournament_to_event = build_tournament_to_event_map(conn)
    conn.close()
    print(f"Ottelut (deduplikoitu): {len(matches)}")
    n_raw = len({m.tournament for m in matches if m.tournament})
    n_events = len({event_of(m.tournament, tournament_to_event) for m in matches if m.tournament})
    print(f"Raakoja tournament-merkkijonoja: {n_raw} -> tapahtumia bracket_progress-resolvoinnin jalkeen: {n_events}\n")

    recent_idx = build_recent_match_index(matches)
    event_first_idx = build_event_first_date_index(matches, tournament_to_event)

    params = load_best_elo_params()
    elo = EloModel(scale=params["scale"], k_factor=params["k_factor"], half_life_days=params["half_life_days"])

    fatigue_buckets: dict = {"0 ottelua/5vrk": [], "1 ottelu/5vrk": [], "2 ottelua/5vrk": [], "3+ ottelua/5vrk": []}
    first_match_buckets: dict = {"suosikin 1. ottelu tapahtumassa": [], "ei ensimmainen": []}
    both_first_buckets: dict = {"molempien 1. ottelu tapahtumassa": [], "ei molempien 1.": []}

    for m in matches:
        p_team = elo.predict(m.team, m.opponent, m.date)
        # maaritetaan suosikki mallin mukaan (ennen ottelua)
        if p_team >= 0.5:
            p_fav = p_team
            fav_is_team = True
        else:
            p_fav = 1 - p_team
            fav_is_team = False
        fav_won = m.team_won if fav_is_team else (1 - m.team_won)
        # "outcome" log_loss-funktiolle: 1 jos ennuste (suosikki voittaa) toteutui
        outcome_for_fav_prob = fav_won

        fav_team_name = m.team if fav_is_team else m.opponent

        # --- uupumusfaktori: suosikin äskettäinen ottelutiheys ---
        n_recent = count_recent(recent_idx, fav_team_name, m.date, FATIGUE_WINDOW_DAYS)
        if n_recent == 0:
            fkey = "0 ottelua/5vrk"
        elif n_recent == 1:
            fkey = "1 ottelu/5vrk"
        elif n_recent == 2:
            fkey = "2 ottelua/5vrk"
        else:
            fkey = "3+ ottelua/5vrk"
        fatigue_buckets[fkey].append((outcome_for_fav_prob, p_fav, fav_won))

        # --- turnauksen alkuvaihe: onko tama suosikin ensimmainen ottelu KOKO
        # tapahtumassa (bracket_progress-resolvoitu event, ei raaka lava) ---
        event = event_of(m.tournament, tournament_to_event)
        is_fav_first = event_first_idx.get((fav_team_name, event)) == m.date
        fkey2 = "suosikin 1. ottelu tapahtumassa" if is_fav_first else "ei ensimmainen"
        first_match_buckets[fkey2].append((outcome_for_fav_prob, p_fav, fav_won))

        underdog_name = m.opponent if fav_is_team else m.team
        is_dog_first = event_first_idx.get((underdog_name, event)) == m.date
        if is_fav_first and is_dog_first:
            both_first_buckets["molempien 1. ottelu tapahtumassa"].append((outcome_for_fav_prob, p_fav, fav_won))
        else:
            both_first_buckets["ei molempien 1."].append((outcome_for_fav_prob, p_fav, fav_won))

        # AIDOSTI tilallinen walk-forward - paivitys VASTA ennusteen jalkeen,
        # muuten ratingit eivat koskaan liiku pois MEAN_RATINGista (1500) ja
        # koko "suosikki" -maaritys olisi merkityksetonta kohinaa.
        elo.update(m.team, m.opponent, m.team_won, m.date)

    print(f"=== UUPUMUSFAKTORI (suosikin ottelut viimeisen {FATIGUE_WINDOW_DAYS} vrk aikana ENNEN tata ottelua) ===")
    bucket_stats(fatigue_buckets)

    print("\n=== TURNAUKSEN ALKUVAIHE (suosikin nakokulmasta - onko tama hanen ensimmainen ottelunsa KOKO tapahtumassa) ===")
    bucket_stats(first_match_buckets)

    print("\n=== TURNAUKSEN ALKUVAIHE (molemmat joukkueet pelaavat tapahtuman ensimmaista ottelua) ===")
    bucket_stats(both_first_buckets)

    print("\nHUOM: 'tapahtuma' = bracket_progress.liquipedia_page kun resolvointi onnistui (88/181")
    print("raakaa lava-merkkijonoa), muuten raaka tournament-merkkijono fallbackina. Upset% = suosikki havisi.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
