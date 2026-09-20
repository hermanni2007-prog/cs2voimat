"""
Markkinasnapshot-keruu oddsportal.com:sta (kayttajan pyynnosta 2026-09-20,
idea #1: "find out how we can benefit... automate the multi-book cross-
check"). Tarkoitus: CLV (closing line value) -seuranta ilman maksullista
oddspapi.io-tilausta - odotetaan sen sijaan periodisesti sivun oikeaa
RENDEROITUA tilaa (kuten oikea selain nakisi), EI oddsportalin sisaista
/proxy/api/home-data -paatepistetta (jonka payload on tarkoituksella
salattu/hamartetty automaatiota vastaan - sen purkaminen olisi bot-
suojauksen kiertamista, mita ei tehda).

TOTEUTUS: Playwright renderoi sivun (headless Chromium), BeautifulSoup
jasentaa tuloksen. Jasennys EI nojaa oddsportalin hashattuihin Tailwind-
luokkiin (ne voivat vaihtua build-per-build) vaan rakenteellisiin
ankkureihin: <a href="/esports/h2h/...">-linkit ovat semanttisesti
vakaita, joukkueiden nimet tulevat <img alt="..."> -attribuuteista, ja
kertoimet luetaan linkin VANHEMMAN tekstista linkin oman tekstin JALKEEN
(rivi = linkin teksti + kaksi desimaalilukua).

HUOM (rehellisyys/laajuus): turnausnimen poiminta osoittautui epaluotet-
tavaksi taman DOM-syvyyden esivanhempihakulla (osui aina ENSIMMAISEEN
sivulla nakyvaan turnaukseen, ei rivin omaan) - jatettiin pois v1:sta.
Ydinarvokas data (joukkueet, kertoimet, aikaleima) on silti taydellinen.
tournament-sarake jaa NULL:ksi kunnes tama parannetaan."""
from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection, init_db  # noqa: E402

LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "collect_market_snapshot.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("collect_market_snapshot")

ODDS_RE = re.compile(r"^\d{1,3}\.\d{1,2}$")
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_rendered_html(url: str) -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=USER_AGENT)
            page.goto(url, wait_until="load", timeout=30000)
            page.wait_for_timeout(2000)
            for sel in ["button:has-text('Reject All')", "button:has-text('I Accept')",
                        "#onetrust-reject-all-handler"]:
                try:
                    page.click(sel, timeout=3000)
                    break
                except Exception:
                    continue
            page.wait_for_timeout(2000)
            return page.content()
        finally:
            browser.close()


def parse_matches(html: str) -> list:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    out = []
    links = soup.find_all("a", href=lambda h: h and "/esports/h2h/" in h)
    for a in links:
        imgs = a.find_all("img", alt=True)
        if len(imgs) != 2:
            continue
        team1_raw, team2_raw = imgs[0]["alt"], imgs[1]["alt"]

        row = a.parent
        row_text = row.get_text(" | ", strip=True)
        a_text = a.get_text(" | ", strip=True)
        if not row_text.startswith(a_text):
            continue
        remainder = row_text[len(a_text):].strip(" |")
        odds_tokens = [t.strip() for t in remainder.split("|") if ODDS_RE.match(t.strip())]
        if len(odds_tokens) < 2:
            continue
        price1, price2 = float(odds_tokens[0]), float(odds_tokens[1])
        status = a_text.split("|")[0].strip()

        out.append({
            "team1_raw": team1_raw, "team2_raw": team2_raw,
            "match_time_raw": status, "price1": price1, "price2": price2,
        })
    return out


def save_snapshot(conn, matches: list, captured_utc: str) -> int:
    n = 0
    for m in matches:
        conn.execute(
            """INSERT INTO market_snapshots
               (captured_utc, sport, tournament, team1_raw, team2_raw, match_time_raw,
                bookmaker, price_team1, price_team2, source)
               VALUES (?, 'counterstrike', NULL, ?, ?, ?, 'oddsportal_best', ?, ?, 'oddsportal')""",
            (captured_utc, m["team1_raw"], m["team2_raw"], m["match_time_raw"],
             m["price1"], m["price2"]),
        )
        n += 1
    conn.commit()
    return n


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="https://www.oddsportal.com/esports/")
    args = parser.parse_args()

    init_db()
    conn = get_connection()
    captured_utc = now_utc_iso()

    try:
        html = fetch_rendered_html(args.url)
        matches = parse_matches(html)
    except Exception:
        log.exception("Keruu epaonnistui")
        conn.close()
        return 1

    n = save_snapshot(conn, matches, captured_utc)
    conn.close()
    log.info("Tallennettiin %d ottelun snapshot (%s)", n, captured_utc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
