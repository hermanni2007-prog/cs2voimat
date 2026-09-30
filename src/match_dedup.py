"""
Tuplaotteluiden esto historical_matches-taulussa (2026-09-28, kayttajan
vaatimus: "duplikaatteja ei saa enaa tulla").

Loydetyt tuplien lahteet (tarkistettu turnauskaavioiden karttadataa vasten):
  1) Liquipedia SIIRTAA alkamisajan (GL-magic 18:00 -> 18:10). UNIQUE-avain
     sisaltaa ajan, joten uusi aika lisataan uutena rivina ja vanha jaa.
  2) Kasin syotetty rivi (source_page ei paaty '/Matches') jaa kaavitun
     rinnalle kun sama ottelu myohemmin kaavitaan.

Saanto "sama ottelu": sama joukkuepari (kanoniset nimet), sama voittaja ja
sama tulos, alkamisajat <= DUPLICATE_WINDOW_HOURS paassa toisistaan. Bo3 kestaa
~2-3 h, joten sama pari ei pelaa kahta erillista ottelua 4 h sisalla; kaikki
5-72 h paassa olevat samanlaiset parit olivat kaavion mukaan aitoja uusintoja.

Poisto on konservatiivinen:
  - Siirretty aika: rivi poistetaan VAIN jos joukkueen oma sivu EI enaa
    listaa sita aikaa mutta listaa saman ottelun <= 4 h paassa. Jos sivu
    listaa molemmat, ne ovat eri otteluita eika kumpaakaan kosketa.
  - Kasin syotetty rivi poistetaan vain kun kaavittu vastine on olemassa.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from backtest import DUPLICATE_WINDOW_HOURS
from team_names import load_top50_names, resolve_to_canonical

WINDOW_S = DUPLICATE_WINDOW_HOURS * 3600


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def remove_rescheduled_rows(conn, team: str, source_page: str, current_rows: list, since_utc: str) -> int:
    """current_rows: joukkueen sivulta JUURI parsitut rivit muodossa
    (match_date_utc, opponent, tournament, score_team, score_opponent) - samat
    arvot jotka INSERT kayttaa. Rivi on VANHENTUNUT jos sivu ei enaa listaa
    sita (aika, vastustaja, turnaus) -yhdistelmana. Se poistetaan vain jos
    sivu listaa KORVAAVAN ottelun: sama tulos, <= 4 h paassa (kattaa siirretyn
    ajan, muuttuneen turnausnimen ja muuttuneen vastustajan nayttonimen).
    Vanhentunut rivi ilman korvaajaa jatetaan rauhaan."""
    listed = {(d, o, tn) for d, o, tn, _, _ in current_rows}
    by_score = defaultdict(list)
    for d, _, _, st, so in current_rows:
        if st is not None and so is not None and st != so:
            by_score[(st, so)].append(_dt(d))
    removed = 0
    for rid, d, o, tn, st, so in conn.execute(
        """SELECT id, match_date_utc, opponent, tournament, score_team, score_opponent
           FROM historical_matches
           WHERE team=? AND source_page=? AND match_date_utc >= ?
             AND score_team IS NOT NULL AND score_opponent IS NOT NULL AND score_team != score_opponent""",
        (team, source_page, since_utc),
    ).fetchall():
        if (d, o, tn) in listed:
            continue
        if any(abs((_dt(d) - nd).total_seconds()) <= WINDOW_S for nd in by_score.get((st, so), [])):
            conn.execute("DELETE FROM historical_matches WHERE id=?", (rid,))
            removed += 1
    return removed


def remove_exact_variant_rows(conn) -> int:
    """Poistaa rivit jotka ovat TASMALLEEN sama ottelu samalta sivulta eri
    vastustajan nimimuodolla ("SINNERS Esports" vs "SINNERS", ennen 18.9.
    nimien normalisointia tallentuneet). Sama joukkue + sama lahdesivu + sama
    aika + sama tulos + sama kanoninen vastustaja = sama ottelu varmasti.
    Toimii myos hakuikkunan (8 kk) ulkopuolella, jota remove_rescheduled_rows
    ei kasittele. Sailyttaa kanonisella nimella tallennetun (tai uusimman) rivin."""
    top = load_top50_names()
    groups = defaultdict(list)
    for rid, d, t, o, st, so, src, tn in conn.execute(
        """SELECT id, match_date_utc, team, opponent, score_team, score_opponent, source_page, tournament
           FROM historical_matches WHERE score_team IS NOT NULL AND score_opponent IS NOT NULL"""
    ):
        groups[(t, src, d, st, so, tn, resolve_to_canonical(o, top))].append((o == resolve_to_canonical(o, top), rid))
    ids = []
    for rows in groups.values():
        if len(rows) > 1:
            rows.sort(reverse=True)  # kanoninen nimi ensin, sitten suurin id
            ids.extend(rid for _, rid in rows[1:])
    for rid in ids:
        conn.execute("DELETE FROM historical_matches WHERE id=?", (rid,))
    return len(ids)


def _match_groups(conn):
    """Ryhmittelee kelvolliset rivit oikeiksi otteluiksi: palauttaa listan ryhmista,
    jokainen ryhma = lista (id, match_date_utc, team, source_page)."""
    top = load_top50_names()
    by_pair = defaultdict(list)
    for rid, d, t, o, st, so, src in conn.execute(
        """SELECT id, match_date_utc, team, opponent, score_team, score_opponent, source_page
           FROM historical_matches
           WHERE score_team IS NOT NULL AND score_opponent IS NOT NULL AND score_team != score_opponent"""
    ):
        t_c, o_c = resolve_to_canonical(t, top), resolve_to_canonical(o, top)
        winner = t_c if st > so else o_c
        key = (frozenset((t_c, o_c)), winner, (max(st, so), min(st, so)))
        by_pair[key].append((_dt(d), rid, d, t_c, src))
    groups = []
    for rows in by_pair.values():
        rows.sort()
        cur = [rows[0]]
        for r in rows[1:]:
            if (r[0] - cur[-1][0]).total_seconds() <= WINDOW_S:
                cur.append(r)
            else:
                groups.append(cur)
                cur = [r]
        groups.append(cur)
    return [[(rid, d, t, src) for _, rid, d, t, src in g] for g in groups if len(g) > 1]


def is_manual(source_page: str) -> bool:
    return not (source_page or "").endswith("/Matches")


def remove_superseded_manual_rows(conn) -> int:
    """Poistaa kasin syotetyt rivit joille on kaavittu vastine (sama ottelu)."""
    ids = [rid for g in _match_groups(conn) if any(not is_manual(s) for *_, s in g)
           for rid, _, _, s in g if is_manual(s)]
    for rid in ids:
        conn.execute("DELETE FROM historical_matches WHERE id=?", (rid,))
    return len(ids)


def find_duplicates(conn) -> list:
    """Terveystarkistukselle: ryhmat joissa SAMAN joukkueen nakokulmasta on
    useampi rivi samasta ottelusta, tai kasin syotetty rivi kaavitun rinnalla.
    (Kummankin joukkueen oma rivi = normaali peilipari, ei tupla.)"""
    bad = []
    for g in _match_groups(conn):
        per_team = defaultdict(int)
        for _, _, t, _ in g:
            per_team[t] += 1
        manual_with_scraped = any(is_manual(s) for *_, s in g) and any(not is_manual(s) for *_, s in g)
        if manual_with_scraped or any(n > 1 for n in per_team.values()):
            bad.append(g)
    return bad
