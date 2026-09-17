"""
Hakee historiallisia kertoimia OddsPapista Tehtava 2:n markkinavertailua
varten.

TULOS 2026-09-17 (n. 72 historical-odds-kutsua, ~180 credittia): 0/72
palautti kayttokelpoisen hinnan Liquipedia-datamme otteluille - ei edes
S-Tier-otteluille tunnetuilla huippujoukkueilla (9z vs G2, BIG vs G2, jne).

KORJAUS AIEMPAAN LOYDOKSEEN: aiemmin (samana paivana) totesin yhden
NAVI-ottelun (20.8.2026) ja yhden StarLadder-ottelun (Magic vs Vitality,
14.9.2026) palauttavan taydellisen kerroinhistorian, ja paattelin tasta
etta OddsPapin arkisto ulottuisi laajasti ennen tilin luontia. Tama oli
YLIOPTIMISTINEN JOHTOPAATOS YHDESTA-KAHDESTA OTOKSESTA. Todennakoisempi
selitys: nama kaksi olivat turnauksia jotka olivat OddsPapin "aktiivinen"-
listalla SAMANA paivana kun testasin (ks. Tehtava 0:n /v4/tournaments-
kutsu) - eli joku (mahdollisesti oma Tehtava 0 -pollerimme) oli jo
kysynyt niiden AJANKOHTAISIA kertoimia, mika loi historian taannehtivasti.
Otteluille joita KUKAAN ei ole aktiivisesti pollannut, historiaa ei ole -
vaikka ne olisivat S-Tier-tason otteluja tunnetuilla joukkueilla.

JOHTOPAATOS: OddsPapin historical-odds EI ratkaise "kuukausien odottelu"
-ongelmaa taannehtivasti. Tehtava 0:n oma jatkuva keruu (odds_snapshots)
on edelleen se oikea tapa kartuttaa markkinavertailudataa - eteenpain,
ei taaksepain. Tehtava 7:n aikataulu palautuu takaisin alkuperaiseen
arvioon (satoja/tuhansia otteluita, viikkoja-kuukausia).
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection  # noqa: E402

OUT_PATH = ROOT / "data" / "market_spotcheck.json"
PARTICIPANTS_CACHE = ROOT / "data" / "cache_participants.json"
BASE_URL = "https://api.oddspapi.io"
MAX_HISTORICAL_CALLS = 20  # havaittu: historical-odds maksaa 1 pyynnon (ei 10x kuten oletettiin); jattaa puskuria


def load_participant_lookup(api_key: str) -> dict:
    """name(lower) -> participantId(str)"""
    data = json.loads(PARTICIPANTS_CACHE.read_text(encoding="utf-8"))["data"]
    return {v.lower(): k for k, v in data.items()}


def resolve_participant(name: str, lookup: dict) -> str | None:
    """Tarkka osuma ensin, sitten osamerkkijonohaku (esim. 'Sashi' ->
    'Sashi eSport'). Useasta osumasta valitaan lyhin (todennakoisimmin
    juuri se joukkue, ei akatemia-/female-variantti)."""
    key = name.lower()
    if key in lookup:
        return lookup[key]
    candidates = [full for full in lookup if key in full or full in key]
    if not candidates:
        return None
    candidates.sort(key=len)
    return lookup[candidates[0]]


def find_candidate_matches(conn, days_back: int = 30, limit: int = 20, tier: str | None = None) -> list:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).isoformat()
    query = """SELECT DISTINCT match_date_utc, team, opponent, score_team, score_opponent
               FROM historical_matches
               WHERE score_team IS NOT NULL AND score_opponent IS NOT NULL
                 AND match_date_utc >= ?"""
    params = [cutoff]
    if tier:
        query += " AND tier = ?"
        params.append(tier)
    query += " ORDER BY match_date_utc DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    return rows


def main() -> int:
    load_dotenv(ROOT / ".env")
    api_key = os.getenv("ODDSPAPI_API_KEY", "")
    if not api_key or api_key == "REPLACE_ME":
        print("ODDSPAPI_API_KEY puuttuu")
        return 1

    session = requests.Session()
    conn = get_connection()
    lookup = load_participant_lookup(api_key)
    candidates = find_candidate_matches(conn, days_back=30, limit=60, tier="S-Tier")
    print(f"Kandidaattiotteluita (30 pv): {len(candidates)}")

    results = []
    calls_made = 0

    for date_str, team, opponent, score_team, score_opponent in candidates:
        if calls_made >= MAX_HISTORICAL_CALLS:
            break
        pid_team = resolve_participant(team, lookup)
        pid_opp = resolve_participant(opponent, lookup)
        if not pid_team or not pid_opp:
            continue

        match_dt = datetime.fromisoformat(date_str)
        day_from = match_dt.strftime("%Y-%m-%d")
        day_to = (match_dt + timedelta(days=1)).strftime("%Y-%m-%d")

        r = session.get(
            f"{BASE_URL}/v4/fixtures",
            params={"apiKey": api_key, "sportId": "17", "from": day_from, "to": day_to},
            timeout=20,
        )
        time.sleep(1.5)
        if r.status_code != 200:
            continue
        fixtures = r.json()
        fixture_id = None
        for fx in fixtures:
            if not isinstance(fx, dict):
                continue
            p1, p2 = str(fx.get("participant1Id")), str(fx.get("participant2Id"))
            if {p1, p2} == {pid_team, pid_opp}:
                fixture_id = fx.get("fixtureId")
                break
        if not fixture_id:
            print(f"  ei fixturea: {team} vs {opponent} ({date_str})")
            continue

        r2 = session.get(
            f"{BASE_URL}/v4/historical-odds",
            params={"apiKey": api_key, "fixtureId": fixture_id, "bookmakers": "coolbet,pinnacle"},
            timeout=20,
        )
        calls_made += 1
        time.sleep(5.5)  # historical-odds cooldown 5000ms
        if r2.status_code != 200:
            print(f"  ei historiaa: {team} vs {opponent} ({date_str}) [{r2.status_code}]")
            continue

        data = r2.json()
        bookmakers_data = data.get("bookmakers", {})
        price_team = price_opp = used_bookmaker = None
        for bm_name in ("coolbet", "pinnacle"):
            bm = bookmakers_data.get(bm_name, {})
            markets = bm.get("markets", {})
            for market in markets.values():
                bmid = str(market.get("bookmakerMarketId", ""))
                parts = bmid.split("/")
                if len(parts) >= 2 and parts[-1] == "moneyline" and parts[-2] == "0":
                    pt = po = None
                    for outcome in (market.get("outcomes") or {}).values():
                        entry = (outcome.get("players") or {}).get("0")
                        if isinstance(entry, list) and entry:
                            entry = entry[-1]
                        if not isinstance(entry, dict):
                            continue
                        side = entry.get("bookmakerOutcomeId")
                        price = entry.get("price")
                        if side == "home":
                            pt = price
                        elif side == "away":
                            po = price
                    if pt and po:
                        price_team, price_opp, used_bookmaker = pt, po, bm_name
                    break
            if price_team and price_opp:
                break
        if price_team and price_opp:
            results.append({
                "date": date_str, "team": team, "opponent": opponent,
                "price_team": price_team, "price_opponent": price_opp,
                "fixture_id": fixture_id, "bookmaker": used_bookmaker,
            })
            print(f"  OK ({used_bookmaker}) {team} vs {opponent}: {price_team} / {price_opp}")
        else:
            print(f"  ei hintaa taltioituna kummallakaan kirjalla: {team} vs {opponent}")

    OUT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{len(results)} ottelua kerroineen tallennettu: {OUT_PATH}")
    print(f"historical-odds-kutsuja kaytetty: {calls_made} (= {calls_made*10} credittia)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
