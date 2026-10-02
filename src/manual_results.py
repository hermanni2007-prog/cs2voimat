"""
data/manual_results.json: kayttajan antamat tuoreet tulokset (esim. HLTV-
kuvakaappauksista), joita CI ei ole viela kerannyt. Lisatty 2026-10-02
kayttajan pyynnosta: --extra-rivit pysyvasti koodiin, ilman tuplia.

EI KOSKAAN tietokantaan (CLAUDE.md kohta 4). predict_match.py lukee tiedoston
automaattisesti ja lisaa rivit vain muistissa olevaan ottelulistaan.

Tuplasuoja: rivi ohitetaan, jos datassa on jo SAMA JOUKKUEPARI enintaan
SKIP_WINDOW paassa (kummasta tahansa nakokulmasta). Ikkuna on leveampi kuin
backtest.deduplicate_matches:n 4 h, koska kasin arvioidut ajat voivat erota
CI:n keraamista. Kun CI kerää ottelun, rivi putoaa siis pois itsestaan.

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
    seen = {}
    for m in matches:
        seen.setdefault(frozenset((m.team, m.opponent)), []).append(m.date)
    added, skipped = [], 0
    for e in json.loads(path.read_text(encoding="utf-8")):
        a = resolve_to_canonical(e["team"], names)
        b = resolve_to_canonical(e["opponent"], names)
        d = datetime.fromisoformat(e["date_utc"])
        if any(abs(x - d) <= SKIP_WINDOW for x in seen.get(frozenset((a, b)), [])):
            skipped += 1
            continue
        sa, sb = int(e["score_team"]), int(e["score_opponent"])
        added.append(MatchRow(date=d, team=a, opponent=b, tier=None, match_type=e.get("match_type", "Offline"),
                              tournament=e.get("tournament", "manual"), team_won=int(sa > sb),
                              score_team=sa, score_opponent=sb))
        seen.setdefault(frozenset((a, b)), []).append(d)
    return added, skipped
