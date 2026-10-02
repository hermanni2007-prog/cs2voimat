"""
data/manual_results.json: kayttajan antamat tuoreet tulokset (esim. HLTV-
kuvakaappauksista), joita CI ei ole viela kerannyt. Lisatty 2026-10-02
kayttajan pyynnosta: --extra-rivit pysyvasti koodiin, ilman tuplia.

EI KOSKAAN tietokantaan (CLAUDE.md kohta 4). predict_match.py lukee tiedoston
automaattisesti ja lisaa rivit vain muistissa olevaan ottelulistaan.

Tuplasuoja: rivi ohitetaan vain, jos datassa on jo SAMA OTTELU: sama
joukkuepari, SAMA TULOS (nakokulma huomioiden) ja enintaan SKIP_WINDOW paassa.
Saman parin uusintaottelu (esim. Bo1-lohko 13-9 ja Bo3-pudotuspeli 2-1 samana
paivana) on eri tulos, joten se EI putoa pois. Ikkuna on leveampi kuin
deduplicate_matches:n 4 h, koska kasin arvioidut ajat voivat erota CI:n
keraamista. Kun CI kerää ottelun, rivi putoaa pois itsestaan.

Rivin kentat: date_utc, team, opponent, score_team, score_opponent,
tournament, match_type (Offline/Online), source.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from backtest import MatchRow
from team_names import resolve_to_canonical

ROOT = Path(__file__).resolve().parent.parent
PATH = ROOT / "data" / "manual_results.json"
SKIP_WINDOW = timedelta(hours=24)


def load_manual_results(matches, names, path: Path = PATH):
    """-> (lisattavat MatchRow:t, ohitettujen maara)."""
    if not path.exists():
        return [], 0
    def key(t, o, st, so):
        # Suunnasta riippumaton avain: pari + tulos samassa jarjestyksessa.
        return (t, o, st, so) if t <= o else (o, t, so, st)

    seen = {}
    for m in matches:
        if m.score_team is not None:
            seen.setdefault(key(m.team, m.opponent, m.score_team, m.score_opponent), []).append(m.date)
    added, skipped = [], 0
    for e in json.loads(path.read_text(encoding="utf-8")):
        a = resolve_to_canonical(e["team"], names)
        b = resolve_to_canonical(e["opponent"], names)
        d = datetime.fromisoformat(e["date_utc"])
        sa, sb = int(e["score_team"]), int(e["score_opponent"])
        k = key(a, b, sa, sb)
        if any(abs(x - d) <= SKIP_WINDOW for x in seen.get(k, [])):
            skipped += 1
            continue
        added.append(MatchRow(date=d, team=a, opponent=b, tier=None, match_type=e.get("match_type", "Offline"),
                              tournament=e.get("tournament", "manual"), team_won=int(sa > sb),
                              score_team=sa, score_opponent=sb))
        seen.setdefault(k, []).append(d)
    return added, skipped
