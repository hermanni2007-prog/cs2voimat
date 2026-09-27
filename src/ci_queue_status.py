"""CI-apuri: tulostaa 'attempted remaining' karttakeruun jonosta.

attempted = turnauksia yritetty taman ajon aikana (last_attempt_utc >= argv[1])
remaining = turnauksia yha 'pending'-tilassa

collect_core.yml kaynnistaa seuraavan ajon itse vain jos remaining > 0 JA
attempted > 0 - jos ajo ei edennyt lainkaan (esim. IP-esto), ketju pysahtyy
eika synny loputonta silmukkaa. Virhetilaiset eivat ole 'pending', joten
pysyvasti virheelliset turnaukset eivat myoskaan pida ketjua kaynnissa."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import get_connection  # noqa: E402

run_start = sys.argv[1]
c = get_connection()
attempted = c.execute("SELECT COUNT(*) FROM bracket_progress WHERE last_attempt_utc >= ?", (run_start,)).fetchone()[0]
remaining = c.execute("SELECT COUNT(*) FROM bracket_progress WHERE status='pending'").fetchone()[0]
print(attempted, remaining)
