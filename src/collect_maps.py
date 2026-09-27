"""
Tehtava 4: kartta- ja CT/T-puoliskotason tulosten keruu Liquipedian
turnausten bracket-sivuilta.

Tausta: joukkuekohtaiset "/Matches"-taulukot (Tehtava 1, collect_history.py)
sisaltavat vain ottelun kokonaistuloksen (esim. "2-1"), ei yksittaisia
karttoja eika puoliskotietoa. Turnauksen omalla bracket-sivulla
(".brkts-match-popup-wrapper" -elementit, ks. liquipedia_client.
parse_bracket_matches) on sen sijaan jokainen kartta erikseen CT/T-jaolla.

Turnaussivujen otsikot eivat ole ennalta tiedossa (naytto-nimi != Liquipedia-
sivun otsikko), joten jokainen historical_matches.tournament-merkkijono
resolvoidaan ensin hakemalla (LiquipediaClient.resolve_tournament_page).
Sama 30.5s/pyynto -rajoite kuin muualla -> skripti on resumable
(bracket_progress-taulu) samaan tapaan kuin collect_history.py.

Rajaus: kasitellaan turnaukset otteluiden lukumaaran mukaan suurimmasta
pienimpaan (eniten dataa per rate-limitoitu pyynto), --max-tournaments
rajaa yhden ajon pituutta.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection, init_db  # noqa: E402
from liquipedia_client import LiquipediaBlocked, LiquipediaClient, parse_bracket_matches  # noqa: E402
from team_names import load_top50_names, resolve_to_canonical  # noqa: E402

LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "collect_maps.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("collect_maps")

PROGRESS_PATH = ROOT / "data" / "maps_progress.json"


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_progress_rows(conn) -> None:
    rows = conn.execute(
        "SELECT tournament, COUNT(*) c FROM historical_matches "
        "WHERE tournament IS NOT NULL GROUP BY tournament ORDER BY c DESC"
    ).fetchall()
    for tournament, _ in rows:
        conn.execute(
            "INSERT OR IGNORE INTO bracket_progress (tournament, status) VALUES (?, 'pending')",
            (tournament,),
        )
    conn.commit()


def write_progress_snapshot(conn) -> None:
    total = conn.execute("SELECT COUNT(*) FROM bracket_progress").fetchone()[0]
    done = conn.execute(
        "SELECT COUNT(*) FROM bracket_progress WHERE status IN ('ok','ok_no_brackets','not_found')"
    ).fetchone()[0]
    ok = conn.execute("SELECT COUNT(*) FROM bracket_progress WHERE status='ok'").fetchone()[0]
    no_brackets = conn.execute("SELECT COUNT(*) FROM bracket_progress WHERE status='ok_no_brackets'").fetchone()[0]
    not_found = conn.execute("SELECT COUNT(*) FROM bracket_progress WHERE status='not_found'").fetchone()[0]
    errors = conn.execute("SELECT COUNT(*) FROM bracket_progress WHERE status='error'").fetchone()[0]
    total_maps = conn.execute("SELECT COUNT(*) FROM historical_maps").fetchone()[0]
    last = conn.execute(
        "SELECT tournament, status, maps_found, last_attempt_utc FROM bracket_progress "
        "WHERE last_attempt_utc IS NOT NULL ORDER BY last_attempt_utc DESC LIMIT 1"
    ).fetchone()

    snapshot = {
        "updated_utc": now_utc_iso(),
        "tournaments_total": total,
        "tournaments_done": done,
        "tournaments_ok": ok,
        "tournaments_ok_no_brackets": no_brackets,
        "tournaments_not_found": not_found,
        "tournaments_error": errors,
        "maps_collected": total_maps,
        "last_tournament": (
            {"tournament": last[0], "status": last[1], "maps_found": last[2], "at": last[3]} if last else None
        ),
    }
    PROGRESS_PATH.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(
        "Edistyminen: %d/%d turnausta kasitelty, %d karttaa kerätty (ok=%d ei_bracketteja=%d not_found=%d error=%d)",
        done, total, total_maps, ok, no_brackets, not_found, errors,
    )


def known_tournament_page(conn, tournament: str):
    """Joukkueiden /Matches-taulukon turnauslinkista talteen otettu tarkka
    sivunimi (yleisin, jos saman naytto-nimen alla on useampi)."""
    row = conn.execute(
        """SELECT tournament_page FROM historical_matches
           WHERE tournament=? AND tournament_page IS NOT NULL
           GROUP BY tournament_page ORDER BY COUNT(*) DESC LIMIT 1""",
        (tournament,),
    ).fetchone()
    return row[0] if row else None


def process_tournament(conn, client: LiquipediaClient, tournament: str, html_cache: dict) -> None:
    try:
        page_title = known_tournament_page(conn, tournament) or client.resolve_tournament_page(tournament)
        if not page_title:
            conn.execute(
                "UPDATE bracket_progress SET status='not_found', last_attempt_utc=? WHERE tournament=?",
                (now_utc_iso(), tournament),
            )
            conn.commit()
            log.warning("Turnaussivua ei loytynyt: %s", tournament)
            return

        # Usea naytto-nimi (esim. "... - Group A" ja "... - Group B") voi
        # osoittaa samalle sivulle - ei haeta samaa sivua kahdesti yhdessa ajossa.
        if page_title not in html_cache:
            html_cache[page_title] = client.fetch_rendered_html(page_title)
        html = html_cache[page_title]
        matches = parse_bracket_matches(html)
        top50_names = load_top50_names()

        maps_inserted = 0
        for match in matches:
            # BUGI (loydetty 2026-09-17): bracket-popupin aria-label antaa
            # Liquipedian TAYDEN nimen ("G2 Esports", "Team Spirit") - ei
            # top50:n lyhytta nimea. Ilman normalisointia sama joukkue
            # halkeaa kahdeksi entiteetiksi Elo/deviaatio-laskennassa.
            # Vaikutti VAHINTAAN 24/50 joukkueeseen. Ks. src/team_names.py.
            team1 = resolve_to_canonical(match["team1"], top50_names)
            team2 = resolve_to_canonical(match["team2"], top50_names)
            for i, mp in enumerate(match["maps"], start=1):
                cur = conn.execute(
                    """INSERT OR IGNORE INTO historical_maps
                       (match_date_utc, tournament, liquipedia_page, team1, team2,
                        team1_series_score, team2_series_score, map_order, map_name,
                        team1_score, team2_score, team1_halves_json, team2_halves_json, collected_utc)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        match["match_date_utc"], tournament, page_title, team1, team2,
                        match["team1_series_score"], match["team2_series_score"], i, mp["map_name"],
                        mp["team1_score"], mp["team2_score"],
                        json.dumps(mp["team1_halves"], ensure_ascii=False),
                        json.dumps(mp["team2_halves"], ensure_ascii=False),
                        now_utc_iso(),
                    ),
                )
                if cur.rowcount:
                    maps_inserted += 1

        status = "ok" if matches else "ok_no_brackets"
        conn.execute(
            """UPDATE bracket_progress
               SET status=?, liquipedia_page=?, matches_found=?, maps_found=?, last_attempt_utc=?, error_message=NULL
               WHERE tournament=?""",
            (status, page_title, len(matches), maps_inserted, now_utc_iso(), tournament),
        )
        conn.commit()
        log.info(
            "%s %s (%s): %d ottelua, %d uutta karttaa",
            status.upper(), tournament, page_title, len(matches), maps_inserted,
        )

    except LiquipediaBlocked:
        raise
    except Exception as exc:
        conn.execute(
            "UPDATE bracket_progress SET status='error', last_attempt_utc=?, error_message=? WHERE tournament=?",
            (now_utc_iso(), str(exc), tournament),
        )
        conn.commit()
        log.exception("Virhe turnauksella %s", tournament)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-tournaments", type=int, default=None,
                         help="Kasittele korkeintaan N kasittelematonta turnausta tassa ajossa.")
    parser.add_argument("--max-minutes", type=float, default=None,
                         help="Lopeta siististi kun aikaa on kulunut N min (jo haetut on tallennettu).")
    args = parser.parse_args()

    init_db()
    conn = get_connection()

    ensure_progress_rows(conn)

    # BUGI (loydetty 2026-09-28): 'error' ei palannut koskaan jonoon, JA
    # kerran 'ok':ksi haettu turnaus jai lopullisesti kasittelyksi - jos haku
    # osui kesken turnausta (esim. SL StarSeries Fall 2026), myohempien
    # otteluiden karttoja ei kerätty koskaan (karttadata loppui 19.9:aan).
    # Nyt: turnaus palaa jonoon kun historiassa on sille UUDEMPI ottelu kuin
    # viimeisin bracket-haku - ei turhia uudelleenhakuja valmiille turnauksille.
    retry_cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    retried = conn.execute(
        "UPDATE bracket_progress SET status='pending' WHERE status='error' AND last_attempt_utc < ?",
        (retry_cutoff,),
    ).rowcount
    requeued = conn.execute(
        """UPDATE bracket_progress SET status='pending'
           WHERE status IN ('ok','ok_no_brackets') AND last_attempt_utc < (
               SELECT MAX(hm.match_date_utc) FROM historical_matches hm
               WHERE hm.tournament = bracket_progress.tournament)"""
    ).rowcount
    found_now = conn.execute(
        """UPDATE bracket_progress SET status='pending'
           WHERE status='not_found' AND EXISTS (
               SELECT 1 FROM historical_matches hm
               WHERE hm.tournament = bracket_progress.tournament AND hm.tournament_page IS NOT NULL)"""
    ).rowcount
    conn.commit()
    if retried or requeued or found_now:
        log.info("Jonoon palautettu: %d virhetilaista, %d turnausta joissa uusia otteluita, "
                 "%d aiemmin not_found joille tarkka sivunimi nyt tiedossa", retried, requeued, found_now)

    pending = conn.execute(
        """SELECT bp.tournament FROM bracket_progress bp
           JOIN (SELECT tournament, COUNT(*) c FROM historical_matches
                 WHERE tournament IS NOT NULL GROUP BY tournament) hm
           ON hm.tournament = bp.tournament
           WHERE bp.status='pending' ORDER BY hm.c DESC"""
    ).fetchall()
    pending = [r[0] for r in pending]
    if args.max_tournaments:
        pending = pending[: args.max_tournaments]

    log.info("Kasitellaan %d turnausta tassa ajossa", len(pending))

    client = LiquipediaClient()
    html_cache: dict = {}
    started = datetime.now(timezone.utc)
    for tournament in pending:
        if args.max_minutes and (datetime.now(timezone.utc) - started).total_seconds() > args.max_minutes * 60:
            log.info("Aikaraja %.0f min taynna - lopetetaan, loput jaavat jonoon", args.max_minutes)
            break
        try:
            process_tournament(conn, client, tournament, html_cache)
        except LiquipediaBlocked as exc:
            log.warning("Liquipedia estaa taman IP:n (%s) - lopetetaan ajo, jaljella olevat jaavat jonoon", exc)
            break
        write_progress_snapshot(conn)

    write_progress_snapshot(conn)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
