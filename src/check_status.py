"""Tulostaa nopean yhteenvedon keruun tilasta - tata varten ei tarvitse osata SQL:aa.

Kaytto:
    py -3 src/check_status.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection, DB_PATH  # noqa: E402


def main() -> None:
    if not DB_PATH.exists():
        print(f"Tietokantaa ei loydy viela: {DB_PATH}")
        print("Aja ensin: py -3 src/collect_odds.py")
        return

    conn = get_connection()

    total_matches = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    with_opening = conn.execute(
        "SELECT COUNT(DISTINCT fixture_id) FROM odds_snapshots WHERE is_opening = 1"
    ).fetchone()[0]
    with_closing = conn.execute(
        "SELECT COUNT(DISTINCT fixture_id) FROM odds_snapshots WHERE is_closing = 1"
    ).fetchone()[0]
    with_both = conn.execute(
        """SELECT COUNT(*) FROM (
             SELECT fixture_id FROM odds_snapshots WHERE is_opening = 1
             INTERSECT
             SELECT fixture_id FROM odds_snapshots WHERE is_closing = 1
           )"""
    ).fetchone()[0]

    print(f"Otteluita tietokannassa yhteensa: {total_matches}")
    print(f"  - joilla avauskerroin tallennettu:   {with_opening}")
    print(f"  - joilla sulkeutuva kerroin tallennettu: {with_closing}")
    print(f"  - joilla MOLEMMAT (hyvaksymiskriteeri): {with_both}")
    print()

    print("Viimeisimmat 10 ajoa (collector_runs):")
    rows = conn.execute(
        """SELECT run_utc, status, fixtures_seen, new_matches, new_opening, new_closing, error_message
           FROM collector_runs ORDER BY id DESC LIMIT 10"""
    ).fetchall()
    if not rows:
        print("  (ei yhtaan ajoa viela)")
    for r in rows:
        run_utc, status, seen, nm, no, nc, err = r
        line = f"  {run_utc}  status={status}  seen={seen} new_matches={nm} new_opening={no} new_closing={nc}"
        if err:
            line += f"  ERROR: {err[:120]}"
        print(line)

    conn.close()


if __name__ == "__main__":
    main()
