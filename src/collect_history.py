"""
Tehtava 1 (rajattu): historiallisten ottelutulosten keruu Liquipediasta.

Laajuus: viimeiset 8 kuukautta (kayttajan pyynnosta 2026-09-18 laajennettu
alkuperaisesta 4 kk:n rajauksesta), vain top-50 joukkuetta
(data/top50_teams.json, lahde: Valve VRS).

Lahde: Liquipedian MediaWiki-API, "<Joukkue>/Matches"-alasivut. Jokainen
sivu sisaltaa joukkueen koko ottelutulokset (pvm, taso, LAN/online,
turnaus, tulos, vastustaja) - ei tarvitse skannata satoja turnaussivuja.

Liquipedian oma nopeusrajoitus (ks. liquipedia_client.py) sallii
action=parse-kutsun kerran 30 sekunnissa. 50 joukkuetta = n. 25 min
minimissaan. Skripti on siksi jatkettava: history_team_progress-taulu
muistaa mitka joukkueet on jo kasitelty, joten ajon voi katkaista ja
jatkaa milloin vain (--max-teams rajaa yhden ajon pituutta esim. GitHub
Actionsin 15 min ajastusvaliin sopivaksi).

Jokaisen joukkueen jalkeen kirjoitetaan data/history_progress.json, josta
status-sivu lukee etenemisen.
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
from liquipedia_client import LiquipediaClient, parse_matches_table  # noqa: E402
from team_names import load_top50_names, resolve_to_canonical  # noqa: E402

LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "collect_history.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("collect_history")

HISTORY_WINDOW_DAYS = 240  # "viimeiset 8 kk" (laajennettu 4 kk:sta 2026-09-18)
TOP50_PATH = ROOT / "data" / "top50_teams.json"
PROGRESS_PATH = ROOT / "data" / "history_progress.json"

# BUGI (loydetty 2026-09-17): status pysyi ikuisesti 'ok':na ensimmaisen
# onnistuneen keruun jalkeen, eika mikaan koskaan palauttanut sita takaisin
# 'pending':ksi - WHERE status='pending' -kysely nayttaytyi siis tyhjana
# JOKA ikinen ajo sen jalkeen kun kaikki 50 joukkuetta oli kertaalleen
# kasitelty (havaittiin: kaikkien 50 last_attempt_utc oli sama 27 min
# ikkuna 2026-09-17, GH Actions oli ajanut tyhjaa siita lahtien vaikka
# ajastus oli paalla). Kayttaja huomasi tama epasuorasti kun Auroran data
# oli 17 vrk vanhaa ja aiheutti ison virheen ennusteessa. Korjaus: nollaa
# 'ok'-joukkueet takaisin 'pending':ksi kun niiden viimeisin haku on yli
# STALE_HOURS vanha, jotta keraus OIKEASTI jatkuu taustalla kuten status-
# sivu jo vaitti sen tekevan.
STALE_HOURS = 6


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_top50() -> list:
    return json.loads(TOP50_PATH.read_text(encoding="utf-8"))


def ensure_progress_rows(conn, teams: list) -> None:
    for t in teams:
        conn.execute(
            "INSERT OR IGNORE INTO history_team_progress (team, status) VALUES (?, 'pending')",
            (t["name"],),
        )
    conn.commit()


def write_progress_snapshot(conn) -> None:
    total_teams = conn.execute("SELECT COUNT(*) FROM history_team_progress").fetchone()[0]
    done = conn.execute(
        "SELECT COUNT(*) FROM history_team_progress WHERE status IN ('ok','not_found')"
    ).fetchone()[0]
    ok = conn.execute("SELECT COUNT(*) FROM history_team_progress WHERE status='ok'").fetchone()[0]
    not_found = conn.execute("SELECT COUNT(*) FROM history_team_progress WHERE status='not_found'").fetchone()[0]
    errors = conn.execute("SELECT COUNT(*) FROM history_team_progress WHERE status='error'").fetchone()[0]
    total_matches = conn.execute("SELECT COUNT(*) FROM historical_matches").fetchone()[0]
    last_team = conn.execute(
        "SELECT team, status, matches_found, last_attempt_utc FROM history_team_progress "
        "WHERE last_attempt_utc IS NOT NULL ORDER BY last_attempt_utc DESC LIMIT 1"
    ).fetchone()

    snapshot = {
        "updated_utc": now_utc_iso(),
        "window_days": HISTORY_WINDOW_DAYS,
        "teams_total": total_teams,
        "teams_done": done,
        "teams_ok": ok,
        "teams_not_found": not_found,
        "teams_error": errors,
        "matches_collected": total_matches,
        "last_team": (
            {"team": last_team[0], "status": last_team[1], "matches_found": last_team[2], "at": last_team[3]}
            if last_team
            else None
        ),
    }
    PROGRESS_PATH.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(
        "Edistyminen: %d/%d joukkuetta kasitelty, %d ottelua kerattu (ok=%d not_found=%d error=%d)",
        done, total_teams, total_matches, ok, not_found, errors,
    )


# VRS:n lyhyt nimi tormaa Liquipediassa toiseen sivuun (yleensa pelaajan
# nimimerkki) eika kyseessa ole uudelleenohjaus vaan kaksi eri sivua, joten
# automaattinen resolveri ei loyda oikeaa. Havaittu kasin 2026-09-17.
TEAM_ALIASES = {
    "Spirit": "Team Spirit",
    "Aurora": "Aurora Gaming",
    # top50->top75-laajennus (2026-09-18): kanoninen nimi ilman valilyontia
    # (jo havaittu DB-kaytanto, ks. IEM Cologne Stage 1 -kertoimet), mutta
    # Liquipedian oma sivunimi sisaltaa valilyonnin - haku epaonnistuisi
    # ilman tata.
    "THUNDERdOWNUNDER": "THUNDER dOWNUNDER",
}


def process_team(conn, client: LiquipediaClient, team_name: str, cutoff_utc: datetime) -> None:
    try:
        lookup_name = TEAM_ALIASES.get(team_name, team_name)
        page_title = client.resolve_team_page(lookup_name)
        if not page_title:
            conn.execute(
                "UPDATE history_team_progress SET status='not_found', last_attempt_utc=? WHERE team=?",
                (now_utc_iso(), team_name),
            )
            conn.commit()
            log.warning("Joukkuetta ei loytynyt Liquipediasta: %s", team_name)
            return

        html = client.fetch_rendered_html(page_title)
        rows = parse_matches_table(html, team_name=team_name)
        top50_names = load_top50_names()

        inserted = 0
        for row in rows:
            match_dt = datetime.fromisoformat(row["match_date_utc"])
            if match_dt < cutoff_utc:
                continue
            # BUGI (loydetty 2026-09-17): Liquipedia nayttaa vastustajan
            # TAYDELLA nimella ("G2 Esports"), mutta oma joukkue tallentuu
            # top50:n LYHYELLA nimella ("G2") - ilman normalisointia sama
            # oikea joukkue halkeaa kahdeksi eri Elo-entiteetiksi. Ks.
            # src/team_names.py:n docstring. Vaikutti 24/50 joukkueeseen.
            opponent = resolve_to_canonical(row["opponent"], top50_names)
            cur = conn.execute(
                """INSERT OR IGNORE INTO historical_matches
                   (match_date_utc, team, opponent, tier, match_type, tournament,
                    score_team, score_opponent, raw_score, source_page, collected_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row["match_date_utc"], team_name, opponent, row["tier"], row["match_type"],
                    row["tournament"], row["score_team"], row["score_opponent"], row["raw_score"],
                    page_title, now_utc_iso(),
                ),
            )
            if cur.rowcount:
                inserted += 1

        conn.execute(
            """UPDATE history_team_progress
               SET status='ok', liquipedia_page=?, matches_found=?, last_attempt_utc=?, error_message=NULL
               WHERE team=?""",
            (page_title, inserted, now_utc_iso(), team_name),
        )
        conn.commit()
        log.info("OK %s (%s): %d uutta ottelua (8 kk ikkunassa)", team_name, page_title, inserted)

    except Exception as exc:
        conn.execute(
            "UPDATE history_team_progress SET status='error', last_attempt_utc=?, error_message=? WHERE team=?",
            (now_utc_iso(), str(exc), team_name),
        )
        conn.commit()
        log.exception("Virhe joukkueella %s", team_name)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-teams", type=int, default=None,
                         help="Kasittele korkeintaan N kasittelematonta joukkuetta tassa ajossa.")
    parser.add_argument("--teams", nargs="+", default=None,
                         help="Pakota TASMALLEEN nama joukkueet valittomasti (ohittaa pending-jonon "
                              "JA staleness-tarkistuksen) - kayttoon kun tiedetaan etta tietty joukkue "
                              "on juuri pelannut (esim. saman paivan pudotuspelibracket) eika voida "
                              "odottaa sen normaalia 6h-uudelleenhakuvuoroa.")
    args = parser.parse_args()

    init_db()
    conn = get_connection()

    teams = load_top50()
    ensure_progress_rows(conn, teams)

    cutoff_utc = datetime.now(timezone.utc) - timedelta(days=HISTORY_WINDOW_DAYS)

    if args.teams:
        known = {r[0] for r in conn.execute("SELECT team FROM history_team_progress").fetchall()}
        pending = [t for t in args.teams if t in known]
        unknown = [t for t in args.teams if t not in known]
        if unknown:
            log.warning("Tuntemattomia joukkuenimia (ei top75-listalla): %s", unknown)
        log.info("Pakotettu kohdennettu haku %d joukkueelle (ohittaa jonon/staleness): %s", len(pending), pending)
    else:
        stale_cutoff = (datetime.now(timezone.utc) - timedelta(hours=STALE_HOURS)).isoformat()
        reset = conn.execute(
            "UPDATE history_team_progress SET status='pending' "
            "WHERE status='ok' AND last_attempt_utc < ?",
            (stale_cutoff,),
        )
        conn.commit()
        if reset.rowcount:
            log.info("Nollattu %d vanhentunutta (>%dh) joukkuetta takaisin pendingiksi", reset.rowcount, STALE_HOURS)

        # Vanhin last_attempt_utc ensin (NULL = ei koskaan haettu = kiireisin) -
        # varmistaa etta AINA vanhentunein data paivittyy ensin, ei aakkosjarjestys.
        pending = conn.execute(
            "SELECT team FROM history_team_progress WHERE status='pending' "
            "ORDER BY last_attempt_utc IS NOT NULL, last_attempt_utc ASC"
        ).fetchall()
        pending = [r[0] for r in pending]
        if args.max_teams:
            pending = pending[: args.max_teams]

    log.info("Kasitellaan %d joukkuetta tassa ajossa (8 kk ikkuna, alkaen %s)", len(pending), cutoff_utc.date())

    client = LiquipediaClient()
    for team_name in pending:
        process_team(conn, client, team_name, cutoff_utc)
        write_progress_snapshot(conn)

    write_progress_snapshot(conn)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
