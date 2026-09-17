"""
Tehtava 0: CS2-kertoimien keruu.

Ajetaan ajastettuna (GitHub Actions) 5 min valein - tama tarvitaan jotta
jokaiselle ottelulle osuu vahintaan yksi pollaus CLOSING_WINDOW_MINUTES
(oletus 10 min) -ikkunaan ennen alkua. Jokaisella ajolla skripti:
  1. Hakee kaikki tulevat CS2-ottelut kertoimineen.
  2. Jos ottelu nahdaan ensi kertaa -> tallentaa "avauskertoimen" (is_opening=1).
  3. Jos ottelun alkuun on alle CLOSING_WINDOW_MINUTES eika sulkeutuvaa
     kerrointa ole viela tallennettu -> tallentaa "sulkeutuvan kertoimen"
     (is_closing=1).
  4. Tallentaa myos valiin jaavat havainnot tavallisina snapshotteina - tama
     on kaytannossa ilmaista koska poller kay joka tapauksessa, ja antaa
     myohemmin tarkemman CLV-aikasarjan.

Jokainen ajo kirjaa yhden rivin collector_runs-tauluun JA logs/collector.log
-tiedostoon. Jos avain puuttuu tai API-kutsu epaonnistuu, ajo paattyy
statukseen 'error'/'skipped_no_key' EIKA kaadu hiljaa - taman huomaa
tarkistamalla collector_runs-taulun tai lokin.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection, init_db  # noqa: E402
from oddspapi_client import OddsPapiClient, OddsPapiError  # noqa: E402

LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "collector.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("collect_odds")


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_start_time(value) -> "datetime | None":
    if not value:
        return None
    try:
        v = value.replace("Z", "+00:00") if isinstance(value, str) else value
        dt = datetime.fromisoformat(v)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


CACHE_DIR = ROOT / "data"
TOURNAMENTS_CACHE_TTL_SECONDS = 2 * 3600   # turnauslista: uudet aktiiviset turnaukset huomataan max 2h viiveella
PARTICIPANTS_CACHE_TTL_SECONDS = 24 * 3600  # joukkuenimet muuttuvat harvoin


def cached_json(cache_name: str, ttl_seconds: int, fetch_fn):
    """Lukee JSON-valimuistin jos se on tarpeeksi tuore, muuten hakee ja tallentaa.

    Vahentaa /v4/tournaments ja /v4/participants -kutsujen maaraa - nama eivat
    muutu 15-20 minuutissa, mutta itse kertoimet (odds-by-tournaments) haetaan
    AINA tuoreena, koska juuri se on se data jonka pitaa olla ajantasainen.
    """
    path = CACHE_DIR / f"cache_{cache_name}.json"
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            fetched_at = datetime.fromisoformat(payload["fetched_utc"])
            age = (datetime.now(timezone.utc) - fetched_at).total_seconds()
            if age < ttl_seconds:
                return payload["data"]
        except Exception:
            pass  # korruptoitunut/vanha cache -> haetaan uudelleen
    data = fetch_fn()
    CACHE_DIR.mkdir(exist_ok=True)
    path.write_text(
        json.dumps({"fetched_utc": now_utc_iso(), "data": data}, ensure_ascii=False),
        encoding="utf-8",
    )
    return data


def record_run(conn: sqlite3.Connection, status: str, fixtures_seen=0, new_matches=0,
                new_opening=0, new_closing=0, error_message=None) -> None:
    conn.execute(
        """INSERT INTO collector_runs
           (run_utc, status, fixtures_seen, new_matches, new_opening, new_closing, error_message)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (now_utc_iso(), status, fixtures_seen, new_matches, new_opening, new_closing, error_message),
    )
    conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug-dump", action="store_true",
                         help="Tallentaa raakavastauksen data/debug_last_response.json ja lopettaa.")
    parser.add_argument("--closing-window-minutes", type=int, default=None)
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    init_db()
    conn = get_connection()

    api_key = os.getenv("ODDSPAPI_API_KEY", "")
    bookmakers = os.getenv("ODDSPAPI_BOOKMAKERS", "pinnacle")
    closing_window = args.closing_window_minutes or int(os.getenv("CLOSING_WINDOW_MINUTES", "20"))

    if not api_key or api_key == "REPLACE_ME":
        msg = "ODDSPAPI_API_KEY puuttuu .env-tiedostosta. Katso .env.example."
        log.error(msg)
        record_run(conn, "skipped_no_key", error_message=msg)
        conn.close()
        return 1

    try:
        client = OddsPapiClient(api_key=api_key, bookmakers=bookmakers)
        sport_id = cached_json("sport_id", TOURNAMENTS_CACHE_TTL_SECONDS, client.find_cs2_sport_id)
        tournaments = cached_json(
            "tournaments", TOURNAMENTS_CACHE_TTL_SECONDS, lambda: client.list_tournaments(sport_id)
        )
        participants = cached_json(
            "participants", PARTICIPANTS_CACHE_TTL_SECONDS, lambda: client.get_participants(sport_id)
        )
        active_tournaments = client.filter_active_tournaments(tournaments)
        tournament_ids = [str(t.get("tournamentId")) for t in active_tournaments]
        tournament_names = {str(t.get("tournamentId")): t.get("tournamentName") for t in active_tournaments}
        log.info(
            "CS2 sport_id=%s, %d turnausta yhteensa, %d aktiivista, %d joukkuetta",
            sport_id, len(tournaments), len(tournament_ids), len(participants),
        )

        all_raw = []
        fixtures_seen = 0
        new_matches = 0
        new_opening = 0
        new_closing = 0

        # OddsPapi sallii max 5 tournamentIds per pyynto (API palauttaa 400:n jos enemman).
        batch_size = 5
        for i in range(0, len(tournament_ids), batch_size):
            batch = tournament_ids[i:i + batch_size]
            raw = client.get_odds_by_tournaments(batch)
            all_raw.append(raw)

            if args.debug_dump:
                continue

            for fo in client.iter_fixture_odds(raw, participants):
                fo.tournament_name = tournament_names.get(fo.tournament_id)
                fixtures_seen += 1
                cur = conn.execute(
                    "SELECT scheduled_start_utc FROM matches WHERE fixture_id = ?",
                    (fo.fixture_id,),
                )
                row = cur.fetchone()
                is_new_match = row is None

                if is_new_match:
                    conn.execute(
                        """INSERT INTO matches
                           (fixture_id, sport_slug, tournament_id, tournament_name,
                            team_home, team_away, format, scheduled_start_utc,
                            first_seen_utc, source, raw_json)
                           VALUES (?, 'cs2', ?, ?, ?, ?, NULL, ?, ?, 'oddspapi', ?)""",
                        (fo.fixture_id, fo.tournament_id, fo.tournament_name,
                         fo.team_home, fo.team_away, fo.scheduled_start_utc,
                         now_utc_iso(), json.dumps(fo.raw)[:5000]),
                    )
                    new_matches += 1

                is_opening = 1 if is_new_match else 0

                is_closing = 0
                start_dt = parse_start_time(fo.scheduled_start_utc)
                if start_dt is not None:
                    minutes_to_start = (start_dt - datetime.now(timezone.utc)).total_seconds() / 60
                    if 0 <= minutes_to_start <= closing_window:
                        already_closed = conn.execute(
                            "SELECT 1 FROM odds_snapshots WHERE fixture_id = ? AND is_closing = 1 LIMIT 1",
                            (fo.fixture_id,),
                        ).fetchone()
                        if already_closed is None:
                            is_closing = 1

                if fo.bookmaker:
                    conn.execute(
                        """INSERT INTO odds_snapshots
                           (fixture_id, captured_utc, bookmaker, price_home, price_away,
                            is_opening, is_closing, source, raw_json)
                           VALUES (?, ?, ?, ?, ?, ?, ?, 'oddspapi', ?)""",
                        (fo.fixture_id, now_utc_iso(), fo.bookmaker, fo.price_home, fo.price_away,
                         is_opening, is_closing, json.dumps(fo.raw)[:5000]),
                    )
                    if is_opening:
                        new_opening += 1
                    if is_closing:
                        new_closing += 1

            conn.commit()

        if args.debug_dump:
            debug_path = ROOT / "data" / "debug_last_response.json"
            debug_path.parent.mkdir(exist_ok=True)
            debug_path.write_text(json.dumps(all_raw, indent=2, ensure_ascii=False), encoding="utf-8")
            log.info("Debug-dump kirjoitettu: %s", debug_path)
            conn.close()
            return 0

        log.info(
            "Ajo OK: fixtures_seen=%d new_matches=%d new_opening=%d new_closing=%d",
            fixtures_seen, new_matches, new_opening, new_closing,
        )
        record_run(conn, "ok", fixtures_seen, new_matches, new_opening, new_closing)
        conn.close()
        return 0

    except OddsPapiError as exc:
        log.exception("OddsPapi-virhe")
        record_run(conn, "error", error_message=str(exc))
        conn.close()
        return 1
    except Exception as exc:  # varmistetaan etta ajo EI kaadu hiljaa
        log.exception("Odottamaton virhe")
        record_run(conn, "error", error_message=str(exc))
        conn.close()
        return 1


if __name__ == "__main__":
    sys.exit(main())
