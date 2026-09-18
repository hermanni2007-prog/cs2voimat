"""
Kayttajan omien, oikeiden vetojen seuranta (kayttajan pyynnosta 2026-09-18:
"ala pitamaan kirjaa vedoistani algoritmimme pohjalta"). ERI ASIA kuin
backtest_vs_real_odds.py (joka on kiinteä, jo-ratkaistu historiallinen
validointiaineisto) - tama on elava, kasvava loki OIKEISTA rahapanoksista
joita kayttaja tekee eteenpain, mallin EV-arvion kera talteen otettuna
VEDON HETKELLA (ei jalkikateen keksittyna).

data/user_bets.json: lista vetoja, kentat ks. yksittaisen vedon rakenne
alla (lisatty kasin/Edit-tyokalulla toistaiseksi, ei omaa add-CLI:ta -
liian harva tarve viela oikeuttamaan sita).

Tama skripti: lukee lokin, yrittaa AUTOMAATTISESTI ratkaista 'pending'-
vedot hakemalla otteluiden oikean tuloksen historical_matches-taulusta
(sama data jota keraaja taydentaa), paivittaa statuksen ja profit_eur:n,
tallentaa takaisin, ja tulostaa yhteenvedon (pankki, ROI, per-veto-rivit)."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection  # noqa: E402

BETS_PATH = ROOT / "data" / "user_bets.json"


def load_bets() -> list:
    if not BETS_PATH.exists():
        return []
    return json.loads(BETS_PATH.read_text(encoding="utf-8"))


def save_bets(bets: list) -> None:
    BETS_PATH.write_text(json.dumps(bets, ensure_ascii=False, indent=2), encoding="utf-8")


def find_result(conn, team: str, opponent: str, match_date_utc: str):
    """Etsii otteluiden tuloksen +-2 vrk match_date_utc:sta (BO1-kortin
    kellonaika ja Liquipedian kirjaama ajankohta voivat poiketa hieman)."""
    target = datetime.fromisoformat(match_date_utc)
    lo = (target - timedelta(days=2)).isoformat()
    hi = (target + timedelta(days=2)).isoformat()
    row = conn.execute(
        """SELECT team, opponent, score_team, score_opponent FROM historical_matches
           WHERE ((team=? AND opponent=?) OR (team=? AND opponent=?))
             AND match_date_utc BETWEEN ? AND ?
             AND score_team IS NOT NULL AND score_opponent IS NOT NULL
           ORDER BY match_date_utc LIMIT 1""",
        (team, opponent, opponent, team, lo, hi),
    ).fetchone()
    if not row:
        return None
    r_team, r_opp, st, so = row
    if st == so:
        return None
    winner = r_team if st > so else r_opp
    return winner


def main() -> int:
    bets = load_bets()
    if not bets:
        print("Ei viela yhtaan vetoa lokissa (data/user_bets.json).")
        return 0

    conn = get_connection()
    changed = False
    for b in bets:
        if b["status"] != "pending":
            continue
        winner = find_result(conn, b["team"], b["opponent"], b["match_date_utc"])
        if winner is None:
            continue
        b["actual_winner"] = winner
        won = winner == b["bet_on"]
        b["status"] = "won" if won else "lost"
        b["profit_eur"] = round(b["stake_eur"] * (b["price"] - 1), 2) if won else -round(b["stake_eur"], 2)
        changed = True
    conn.close()
    if changed:
        save_bets(bets)

    print(f"{'Pvm':16s} {'Ottelu':32s} {'Veto':12s} {'Kerroin':>7s} {'Panos':>7s} {'EV':>7s} {'Tila':>8s} {'Tulos':>8s}")
    total_staked = total_profit = 0.0
    n_resolved = 0
    for b in bets:
        matchup = f"{b['team']} - {b['opponent']}"
        ev_str = f"{b['model_ev']*100:+.1f}%" if b.get("model_ev") is not None else "-"
        profit_str = f"{b['profit_eur']:+.2f}e" if b.get("profit_eur") is not None else "-"
        print(f"{b['match_date_utc'][:16]:16s} {matchup:32s} {b['bet_on']:12s} "
              f"{b['price']:>7.2f} {b['stake_eur']:>6.2f}e {ev_str:>7s} {b['status']:>8s} {profit_str:>8s}")
        total_staked += b["stake_eur"] if b["status"] != "pending" else 0.0
        if b.get("profit_eur") is not None:
            total_profit += b["profit_eur"]
            n_resolved += 1

    n_pending = sum(1 for b in bets if b["status"] == "pending")
    print(f"\nYhteensa: {len(bets)} vetoa ({n_pending} odottaa, {n_resolved} ratkaistu)")
    if n_resolved:
        roi = total_profit / total_staked * 100 if total_staked else 0.0
        print(f"Ratkaistut: panostettu {total_staked:.2f}e, nettotulos {total_profit:+.2f}e (ROI {roi:+.1f}%)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
