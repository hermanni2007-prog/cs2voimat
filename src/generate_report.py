"""
Tehtava 6: paivittainen raportti.

Briiffi pyytaa: (A) pelivoimataulukko karttapoikkeamineen, (B) tulevien
otteluiden nakyma p_malli/o_reilu/markkinakerroin/EV/Kelly, (C)
kumulatiivinen CLV-seurantaloki. Pakollinen: mallin ja markkinan rullaava
log loss rinnakkain.

REHELLINEN TILANNE 2026-09-17: B ja C vaativat Tehtava 0:n jatkuvaa
kerroinkeruuta (paussilla, odottaa Coolbet-tilausta) ja markkinan
rullaava log loss vaatii kertoimet historiallisille otteluille (ei viela
saatavilla, ks. Tehtava 2:n loydokset). Taman skriptin tuottama raportti
sisaltaa siis:
  - A: OIKEA nykyinen pelivoimataulukko (Elo, kaikki 50 joukkuetta).
      Karttapoikkeamat EIVAT ole mukana (Tehtava 4 kesken - ei karttadataa).
  - Mallin oma rullaava log loss (VIIMEISET 100 deduplikoitua ottelua) -
      OIKEA luku, walk-forward-ajosta.
  - Markkinan rullaava log loss: EI SAATAVILLA, merkitty selkeasti.
  - B: yksi OIKEA esimerkkilaskelma kahdelle ottelulle joille loytyi
      seka Elo-luokitus etta oikea Coolbet-kerroin (talta paivalta,
      Tehtava 0:n testiajosta - EI enaa voimassa olevia, esimerkkina).
  - C: EI VIELA ALOITETTU (vaatii Tehtava 7:n infran).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection  # noqa: E402
from backtest import (  # noqa: E402
    EloModel,
    deduplicate_matches,
    load_clean_matches,
    log_loss,
    remove_margin,
)

REPORT_PATH = ROOT / "data" / "daily_report.json"

# Tehtava 3:n paras loydetty parametristo (run_elo.py)
SCALE = 100.0
K_FACTOR = 32.0
HALF_LIFE = 99999.0  # ei vaimennusta - paras loydos 4 kk:n datalla


def build_elo_and_rolling_loss(matches: list) -> tuple:
    """Ajaa Elon koko historian lapi, palauttaa (lopulliset ratingit,
    rullaava log loss viimeiselle 100:lle otteluille)."""
    elo = EloModel(scale=SCALE, k_factor=K_FACTOR, half_life_days=HALF_LIFE)
    outcomes = []
    probs = []
    for m in matches:
        p = elo.predict(m.team, m.opponent, m.date)
        outcomes.append(m.team_won)
        probs.append(p)
        elo.update(m.team, m.opponent, m.team_won, m.date)

    window = 100
    if len(outcomes) >= window:
        rolling_ll = log_loss(outcomes[-window:], probs[-window:])
        rolling_n = window
    else:
        rolling_ll = log_loss(outcomes, probs) if outcomes else None
        rolling_n = len(outcomes)

    final_ratings = {team: rating for team, (rating, _date) in elo.ratings.items()}
    return final_ratings, rolling_ll, rolling_n, elo


def build_example_ev_table(conn, elo: EloModel) -> list:
    """Etsii ottelut joille loytyy SEKA Elo-luokitus etta oikea kerroin
    (Tehtava 0:n testidatasta) - esimerkkina EV/Kelly-laskennasta, EI
    aktiivisina suosituksina (kertoimet eivat ole enaa voimassa)."""
    rows = conn.execute(
        """SELECT m.team_home, m.team_away, m.scheduled_start_utc, m.tournament_name,
                  o.price_home, o.price_away
           FROM matches m
           JOIN odds_snapshots o ON o.fixture_id = m.fixture_id AND o.is_opening = 1"""
    ).fetchall()

    out = []
    now = datetime.now(timezone.utc)
    for team_home, team_away, start_utc, tournament, price_home, price_away in rows:
        if team_home not in elo.ratings or team_away not in elo.ratings:
            continue  # ei molemmille Elo-luokitusta - ohitetaan esimerkista
        if price_home is None or price_away is None:
            continue  # kerroin ei taltioitunut taydellisena
        p_model = elo.predict(team_home, team_away, now)
        p_fair_home, p_fair_away = remove_margin(price_home, price_away)
        ev_home = p_model * price_home - 1
        kelly_home = max(0.0, (p_model * price_home - 1) / (price_home - 1)) if price_home > 1 else 0.0
        out.append({
            "team_home": team_home, "team_away": team_away,
            "tournament": tournament, "start_utc": start_utc,
            "price_home": price_home, "price_away": price_away,
            "p_model_home": p_model, "p_fair_home": p_fair_home,
            "ev_home": ev_home, "kelly_home": kelly_home,
        })
    return out


def main() -> int:
    conn = get_connection()
    matches = deduplicate_matches(load_clean_matches(conn))
    print(f"Ottelut (deduplikoitu): {len(matches)}")

    ratings, rolling_ll, rolling_n, elo = build_elo_and_rolling_loss(matches)

    top50 = json.loads((ROOT / "data" / "top50_teams.json").read_text(encoding="utf-8"))
    table = []
    for t in top50:
        name = t["name"]
        rating = ratings.get(name, 1500.0)
        table.append({"vrs_rank": t["rank"], "team": name, "elo_rating": round(rating, 1)})
    table.sort(key=lambda r: -r["elo_rating"])
    for i, row in enumerate(table, start=1):
        row["elo_rank"] = i

    example_matches = build_example_ev_table(conn, elo)

    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "section_a_power_table": table,
        "section_a_note": "Karttapoikkeamat EIVAT viela mukana - Tehtava 4 odottaa karttatason dataa.",
        "rolling_log_loss": {
            "model": rolling_ll,
            "model_n": rolling_n,
            "market": None,
            "market_note": "Ei saatavilla - vaatii Tehtava 0:n jatkuvan kerroinkeruun ja pidemman aikahistorian.",
        },
        "section_b_examples": example_matches,
        "section_b_note": "Nama EIVAT ole aktiivisia suosituksia - kertoimet talta paivalta, ottelut jo menneet. Esimerkki EV/Kelly-laskentatavasta.",
        "section_c_clv_log": None,
        "section_c_note": "Ei viela aloitettu - vaatii Tehtava 7:n infran ja Tehtava 0:n jatkuvan datan.",
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nPelivoimataulukko (top 10):")
    for row in table[:10]:
        print(f"  #{row['elo_rank']:<3d} {row['team']:<22s} Elo={row['elo_rating']}")
    print(f"\nMallin rullaava log loss (viim. {rolling_n} ottelua): {rolling_ll:.4f}")
    print(f"Markkinan rullaava log loss: EI SAATAVILLA")
    print(f"\nEsimerkkiotteluita EV-laskennalla: {len(example_matches)}")
    for ex in example_matches:
        print(f"  {ex['team_home']} vs {ex['team_away']}: p_malli={ex['p_model_home']:.2f} "
              f"kerroin={ex['price_home']} EV={ex['ev_home']:+.2%} Kelly={ex['kelly_home']:.1%}")

    print(f"\nRaportti tallennettu: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
