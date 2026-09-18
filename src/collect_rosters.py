"""
Tehtava 1 -taydennys: kokoonpanot (kuka pelasi milloinkin).

Alkuperainen hyvaksymiskriteeri vaati kummankin joukkueen kokoonpanon
jokaiselle ottelu-riville. Tama skripti hakee top-50-joukkueiden
paasivuilta (EI /Matches-alasivulta) "Player Roster" -osion Active- ja
Former-taulut, jotka sisaltavat pelaajien liittymis-/lahtopaivat.

Ajetaan HARVEMMIN kuin ottelukeruu (kokoonpanot muuttuvat harvoin) -
GitHub Actions -ajastus paivittain, ei 15 min valein. Jokainen ajo
KORVAA koko joukkueen rivit tuoreilla (ei inkrementaalinen dedupe -
roolilista haetaan aina kokonaisuutena).

Kayttö jalkikateen: backtest.py:n kaltainen "roster_as_of(team, date)"
-haku, joka palauttaa listan pelaajaId:ita joilla join_date <= date JA
(leave_date IS NULL TAI leave_date > date).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection, init_db  # noqa: E402
from liquipedia_client import LiquipediaClient, parse_roster_page  # noqa: E402

LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "collect_rosters.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("collect_rosters")

TOP50_PATH = ROOT / "data" / "top50_teams.json"
PROGRESS_PATH = ROOT / "data" / "roster_progress.json"

# Sama nimitormaystaulukko kuin collect_history.py:ssa (Liquipedia lyhyt nimi
# osuu pelaajasivuun eika joukkuesivuun).
TEAM_ALIASES = {
    "Spirit": "Team Spirit",
    "Aurora": "Aurora Gaming",
    "THUNDERdOWNUNDER": "THUNDER dOWNUNDER",
}


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_top50() -> list:
    return json.loads(TOP50_PATH.read_text(encoding="utf-8"))


def ensure_progress_rows(conn, teams: list) -> None:
    for t in teams:
        conn.execute(
            "INSERT OR IGNORE INTO roster_team_progress (team, status) VALUES (?, 'pending')",
            (t["name"],),
        )
    conn.commit()


def write_progress_snapshot(conn) -> None:
    total = conn.execute("SELECT COUNT(*) FROM roster_team_progress").fetchone()[0]
    done = conn.execute(
        "SELECT COUNT(*) FROM roster_team_progress WHERE status IN ('ok','not_found')"
    ).fetchone()[0]
    ok = conn.execute("SELECT COUNT(*) FROM roster_team_progress WHERE status='ok'").fetchone()[0]
    total_entries = conn.execute("SELECT COUNT(*) FROM team_rosters").fetchone()[0]
    snapshot = {
        "updated_utc": now_utc_iso(),
        "teams_total": total,
        "teams_done": done,
        "teams_ok": ok,
        "roster_entries": total_entries,
    }
    PROGRESS_PATH.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Edistyminen: %d/%d joukkuetta, %d roolirivia yhteensa", done, total, total_entries)


def process_team(conn, client: LiquipediaClient, team_name: str) -> None:
    try:
        lookup_name = TEAM_ALIASES.get(team_name, team_name)
        page_title = client.resolve_team_base_page(lookup_name)
        if not page_title:
            conn.execute(
                "UPDATE roster_team_progress SET status='not_found', last_attempt_utc=? WHERE team=?",
                (now_utc_iso(), team_name),
            )
            conn.commit()
            log.warning("Paasivua ei loytynyt: %s", team_name)
            return

        html = client.fetch_rendered_html(page_title)
        entries = parse_roster_page(html)

        conn.execute("DELETE FROM team_rosters WHERE team = ?", (team_name,))
        for e in entries:
            conn.execute(
                """INSERT INTO team_rosters (team, player_id, join_date, leave_date, source_page, collected_utc)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (team_name, e["player_id"], e["join_date"], e["leave_date"], page_title, now_utc_iso()),
            )

        conn.execute(
            """UPDATE roster_team_progress
               SET status='ok', liquipedia_page=?, entries_found=?, last_attempt_utc=?, error_message=NULL
               WHERE team=?""",
            (page_title, len(entries), now_utc_iso(), team_name),
        )
        conn.commit()
        log.info("OK %s (%s): %d roolirivia", team_name, page_title, len(entries))

    except Exception as exc:
        conn.execute(
            "UPDATE roster_team_progress SET status='error', last_attempt_utc=?, error_message=? WHERE team=?",
            (now_utc_iso(), str(exc), team_name),
        )
        conn.commit()
        log.exception("Virhe joukkueella %s", team_name)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-teams", type=int, default=None)
    parser.add_argument("--reset", action="store_true", help="Merkitse kaikki uudelleen 'pending':ksi (taysi paivitys).")
    args = parser.parse_args()

    init_db()
    conn = get_connection()
    teams = load_top50()
    ensure_progress_rows(conn, teams)

    if args.reset:
        conn.execute("UPDATE roster_team_progress SET status='pending'")
        conn.commit()

    pending = [r[0] for r in conn.execute(
        "SELECT team FROM roster_team_progress WHERE status='pending' ORDER BY team"
    ).fetchall()]
    if args.max_teams:
        pending = pending[: args.max_teams]

    log.info("Kasitellaan %d joukkuetta tassa ajossa", len(pending))

    client = LiquipediaClient()
    for team_name in pending:
        process_team(conn, client, team_name)
        write_progress_snapshot(conn)

    write_progress_snapshot(conn)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
