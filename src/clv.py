"""
Tehtava 7 (valmisteltu etukateen, EI VIELA KAYTOSSA): CLV (closing line
value) -laskenta.

CLV on projektin lopullinen validointimittari (ks. cs2-agenttibriiffi.md) -
parempi kuin "loysinko +EV-vedon tanaan", koska se ei vaadi satoja oikeita
vetotuloksia paatelläkseen onko mallissa oikeasti jotain arvoa. Ajatus:
jos malli EROAA markkinasta systemaattisesti OIKEAAN suuntaan, markkinan
oma hinta liikkuu ajan mittaan kohti mallin ennustetta - eli saamamme
kerroin oli parempi kuin sulkeutuva kerroin.

CLV% = price_at_bet / price_closing - 1
  > 0  -> saimme paremman hinnan kuin sulkeutuva kerroin (hyva merkki)
  < 0  -> markkina liikkui meita vastaan (huono merkki)

Tama EI viela toimi kaytannossa: vaatii oikeaa jatkuvaa kerroinkeruuta
(Tehtava 0, tauolla Coolbet-tilausta odottaen). clv_log-taulu (db/schema.sql)
on valmis, ja log_bet()/settle_closing() alla ovat valmis rajapinta jota
src/collect_odds.py voi kutsua kun Tehtava 0 kaynnistyy uudelleen - EI
KUTSUTA VIELA MISTAAN."""
from __future__ import annotations

from datetime import datetime, timezone


def compute_clv_pct(price_at_bet: float, price_closing: float) -> float:
    if not price_closing:
        raise ValueError("price_closing puuttuu - CLV ei laskettavissa")
    return price_at_bet / price_closing - 1.0


def log_bet(conn, fixture_id: str, bookmaker: str, model_prob_team: float,
            market_prob_team_bet: float, price_at_bet: float) -> int:
    """Kirjaa hypoteettisen/oikean vedon CLV-seurantaa varten. price_closing
    ja clv_pct jaavat NULL:iksi kunnes settle_closing() kutsutaan samalle
    fixture_id:lle kun sulkeutuva kerroin on tiedossa (is_closing=1 rivi
    odds_snapshots-taulussa)."""
    cur = conn.execute(
        """INSERT INTO clv_log
           (fixture_id, bookmaker, model_prob_team, market_prob_team_bet, price_at_bet, logged_utc)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (fixture_id, bookmaker, model_prob_team, market_prob_team_bet, price_at_bet,
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return cur.lastrowid


def settle_closing(conn, fixture_id: str, price_closing: float) -> int:
    """Taydentaa kaikki kyseisen fixturen avoimet clv_log-rivit sulkeutuvalla
    kertoimella ja laskee CLV%:n. Palauttaa paivitettyjen rivien maaran."""
    rows = conn.execute(
        "SELECT id, price_at_bet FROM clv_log WHERE fixture_id = ? AND price_closing IS NULL",
        (fixture_id,),
    ).fetchall()
    for row_id, price_at_bet in rows:
        clv_pct = compute_clv_pct(price_at_bet, price_closing)
        conn.execute(
            "UPDATE clv_log SET price_closing = ?, clv_pct = ? WHERE id = ?",
            (price_closing, clv_pct, row_id),
        )
    conn.commit()
    return len(rows)


def summary(conn) -> dict:
    """Briiffin pysaytysanto: jos CLV on <= 0 300 vedon jalkeen, tama pitaa
    raportoida otsikkotasolla (ei viela ajankohtaista - clv_log on tyhja)."""
    rows = conn.execute("SELECT clv_pct FROM clv_log WHERE clv_pct IS NOT NULL").fetchall()
    n = len(rows)
    if n == 0:
        return {"n": 0, "avg_clv_pct": None, "stop_rule_triggered": False}
    avg = sum(r[0] for r in rows) / n
    return {"n": n, "avg_clv_pct": avg, "stop_rule_triggered": (n >= 300 and avg <= 0)}
