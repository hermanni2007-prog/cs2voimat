"""
Putken jarkevyystarkistukset - ajetaan jokaisen keruuajon jalkeen (paikallisesti
tai GitHub Actionsissa). Ei korjaa mitaan automaattisesti, vain RAPORTOI
epailyttavat tilanteet - loydetty 2026-09-17 kayttajan omien havaintojen
kautta (dedup-bugi, sarja/kartta-datan sekoitus, collector joka lakkasi
paivittymasta) etta putki voi hiljaa rikkoutua tavoilla joita ei huomaa
ilman ulkopuolista tarkistusta.

Palauttaa exit code 1 jos loytyy jotain huolestuttavaa (jotta GitHub
Actions -workflow voi nayttaa sen epaonnistuneena ajona, ei vain hiljaa
onnistuneena)."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection  # noqa: E402
from backtest import deduplicate_matches, load_clean_matches  # noqa: E402

STALE_WARN_HOURS = 24  # kuinka vanha data on jo huolestuttavaa (ei viela virhe)
STALE_ERROR_HOURS = 72


def check_history_freshness(conn) -> list:
    problems = []
    rows = conn.execute(
        "SELECT team, status, last_attempt_utc FROM history_team_progress"
    ).fetchall()
    now = datetime.now(timezone.utc)
    stale_warn, stale_error, never = [], [], []
    for team, status, last_attempt in rows:
        if last_attempt is None:
            never.append(team)
            continue
        age_h = (now - datetime.fromisoformat(last_attempt)).total_seconds() / 3600
        if age_h > STALE_ERROR_HOURS:
            stale_error.append((team, age_h))
        elif age_h > STALE_WARN_HOURS:
            stale_warn.append((team, age_h))

    if never:
        problems.append(("VAROITUS", f"{len(never)} joukkuetta ei ole KOSKAAN haettu: {never[:10]}"))
    if stale_warn:
        problems.append(("HUOMIO", f"{len(stale_warn)} joukkuetta yli {STALE_WARN_HOURS}h vanhaa "
                                    f"(esim. {stale_warn[0][0]}, {stale_warn[0][1]:.0f}h)"))
    if stale_error:
        problems.append(("VIRHE", f"{len(stale_error)} joukkuetta yli {STALE_ERROR_HOURS}h vanhaa "
                                   f"- kerays on todennakoisesti jumissa: {[t for t, _ in stale_error[:10]]}"))
    return problems


def check_dedup_sanity(conn) -> list:
    """Varmistaa etta jokaisella top-50-joukkueella on JARKEVA maara
    otteluita dedupin jalkeen suhteessa historical_matches:in raakadataan -
    jos dedup pudottaa yli 60% jonkun joukkueen otteluista, se viittaa
    samaan dedup-avain-kollisioon kuin 2026-09-17 loydetty bugi."""
    import json

    problems = []
    top50 = json.loads((ROOT / "data" / "top50_teams.json").read_text(encoding="utf-8"))
    all_matches = load_clean_matches(conn)
    deduped = deduplicate_matches(all_matches)

    raw_count: dict = {}
    for m in all_matches:
        raw_count[m.team] = raw_count.get(m.team, 0) + 1
    dedup_count: dict = {}
    for m in deduped:
        dedup_count[m.team] = dedup_count.get(m.team, 0) + 1
        dedup_count[m.opponent] = dedup_count.get(m.opponent, 0) + 1

    for t in top50:
        name = t["name"]
        raw = raw_count.get(name, 0)
        if raw == 0:
            continue
        kept = dedup_count.get(name, 0)
        if kept < raw * 0.4:
            problems.append(("VIRHE", f"{name}: dedupin jalkeen vain {kept}/{raw} ottelua jaljella "
                                       f"(<40%) - mahdollinen dedup-avain-kollisio"))
    return problems


def check_bracket_progress_stuck(conn) -> list:
    problems = []
    row = conn.execute(
        "SELECT COUNT(*), MAX(last_attempt_utc) FROM bracket_progress WHERE status != 'pending'"
    ).fetchone()
    processed, last = row
    if last:
        age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() / 3600
        pending = conn.execute("SELECT COUNT(*) FROM bracket_progress WHERE status='pending'").fetchone()[0]
        if pending > 0 and age_h > STALE_ERROR_HOURS:
            problems.append(("VIRHE", f"bracket_progress: {pending} turnausta yha pending, "
                                       f"viimeisin ajo {age_h:.0f}h sitten - kerays nayttaa jumiutuneen"))
    return problems


def main() -> int:
    conn = get_connection()
    all_problems = []
    all_problems += check_history_freshness(conn)
    all_problems += check_dedup_sanity(conn)
    all_problems += check_bracket_progress_stuck(conn)
    conn.close()

    if not all_problems:
        print("OK - ei loytynyt huolestuttavia signaaleja.")
        return 0

    has_error = False
    for level, msg in all_problems:
        print(f"[{level}] {msg}")
        if level == "VIRHE":
            has_error = True

    return 1 if has_error else 0


if __name__ == "__main__":
    sys.exit(main())
