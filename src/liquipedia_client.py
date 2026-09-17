"""
Ohut client Liquipedian MediaWiki-API:lle (VAPAASTI kaytettavissa, ei vaadi
LPDB-hyvaksyntaa - ks. https://liquipedia.net/api-terms-of-use).

Kayttoehdot joita tama moduuli noudattaa:
  - Kustomoitu User-Agent yhteystiedoilla (vaaditaan).
  - action=parse: max 1 pyynto / 30s.
  - Muut kutsut (action=query): max 1 pyynto / 2s.
  - HTTP-yhteys uudelleenkaytetaan (requests.Session).
  - gzip hyvaksytaan (requests hoitaa oletuksena).
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

import requests

BASE_URL = "https://liquipedia.net/counterstrike/api.php"
USER_AGENT = "CS2PelivoimatBot/0.1 (https://github.com/hermanni2007-prog/cs2voimat; hermanni2007@gmail.com)"

QUERY_COOLDOWN_SECONDS = 2.1
PARSE_COOLDOWN_SECONDS = 30.5


class LiquipediaClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
        self._last_query_at = 0.0
        self._last_parse_at = 0.0

    def _wait(self, last_attr: str, cooldown: float):
        last = getattr(self, last_attr)
        elapsed = time.monotonic() - last
        if elapsed < cooldown:
            time.sleep(cooldown - elapsed)
        setattr(self, last_attr, time.monotonic())

    def page_exists(self, title: str) -> bool:
        self._wait("_last_query_at", QUERY_COOLDOWN_SECONDS)
        r = self.session.get(BASE_URL, params={"action": "query", "format": "json", "titles": title}, timeout=20)
        r.raise_for_status()
        pages = r.json().get("query", {}).get("pages", {})
        return not any("missing" in p for p in pages.values())

    def resolve_redirect(self, title: str) -> str:
        """Jos "title" on uudelleenohjaussivu, palauttaa kohdesivun otsikon - muuten "title" sellaisenaan.

        Tarpeellinen koska esim. "Spirit" ohjaa "Team Spirit" -sivulle, mutta
        "Spirit/Matches" ei ole olemassa vaikka "Team Spirit/Matches" on -
        alasivuja ei resolvoida automaattisesti perussivun uudelleenohjauksen kautta.
        """
        self._wait("_last_query_at", QUERY_COOLDOWN_SECONDS)
        r = self.session.get(
            BASE_URL,
            params={"action": "query", "format": "json", "titles": title, "redirects": 1},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json().get("query", {})
        redirects = data.get("redirects")
        if redirects:
            return redirects[0]["to"]
        return title

    def search_best_title(self, query: str) -> Optional[str]:
        self._wait("_last_query_at", QUERY_COOLDOWN_SECONDS)
        r = self.session.get(
            BASE_URL,
            params={"action": "query", "format": "json", "list": "search", "srsearch": query, "srlimit": 5},
            timeout=20,
        )
        r.raise_for_status()
        hits = r.json().get("query", {}).get("search", [])
        return hits[0]["title"] if hits else None

    def resolve_team_page(self, team_name: str) -> Optional[str]:
        """Palauttaa joukkueen "<Sivu>/Matches"-alasivun otsikon, tai None jos ei loydy."""
        direct = f"{team_name}/Matches"
        if self.page_exists(direct):
            return direct

        # Kokeile perussivun uudelleenohjausta (esim. "Spirit" -> "Team Spirit").
        resolved = self.resolve_redirect(team_name)
        if resolved != team_name:
            candidate = f"{resolved}/Matches"
            if self.page_exists(candidate):
                return candidate

        best = self.search_best_title(team_name)
        if best:
            candidate = f"{best}/Matches"
            if self.page_exists(candidate):
                return candidate
            resolved_best = self.resolve_redirect(best)
            if resolved_best != best:
                candidate2 = f"{resolved_best}/Matches"
                if self.page_exists(candidate2):
                    return candidate2
        return None

    def fetch_rendered_html(self, page_title: str) -> str:
        self._wait("_last_parse_at", PARSE_COOLDOWN_SECONDS)
        r = self.session.get(
            BASE_URL,
            params={"action": "parse", "format": "json", "page": page_title, "prop": "text"},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise RuntimeError(f"Liquipedia API-virhe sivulla {page_title}: {data['error']}")
        return data["parse"]["text"]["*"]


def parse_matches_table(html: str) -> list:
    """Jasentaa joukkuesivun "/Matches"-taulukon riveiksi.

    Sarakejarjestys (havaittu 2026-09-17, Natus Vincere/Matches -sivulta):
    0=Date(timer-object data-timestamp), 1=Tier, 2=Type(Offline/Online),
    3=peli-ikoni, 4=turnausikoni, 5=Tournament, 6=tulosmerkki, 7=Score,
    8=vs. Opponent, 9=VOD(s).
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="table2__table")
    if table is None:
        return []

    rows = table.find_all("tr", class_=lambda c: c and "row--body" in c)
    out = []
    for row in rows:
        tds = row.find_all("td", recursive=False)
        if len(tds) < 9:
            continue

        timer = tds[0].find("span", class_="timer-object")
        if not timer or not timer.get("data-timestamp"):
            continue
        try:
            ts = int(timer["data-timestamp"])
        except (ValueError, TypeError):
            continue
        match_date_utc = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

        tier_link = tds[1].find("a")
        tier = (tier_link.get_text(strip=True) if tier_link else tds[1].get_text(strip=True)) or None

        match_type = tds[2].get_text(strip=True) or None

        tourn_link = tds[5].find("a")
        tournament = (tourn_link.get_text(strip=True) if tourn_link else tds[5].get_text(strip=True)) or None

        score_cell = tds[7]
        score_spans = score_cell.find_all("span")
        score_team = score_opponent = None
        if len(score_spans) >= 2:
            try:
                score_team = int(score_spans[0].get_text(strip=True))
                score_opponent = int(score_spans[1].get_text(strip=True))
            except ValueError:
                pass
        raw_score = score_cell.get_text(strip=True)

        opp_cell = tds[8]
        opp_link = opp_cell.find("a", title=True)
        opponent = opp_link["title"] if opp_link else opp_cell.get_text(strip=True)

        if not tournament or not opponent:
            continue

        out.append(
            {
                "match_date_utc": match_date_utc,
                "tier": tier,
                "match_type": match_type,
                "tournament": tournament,
                "score_team": score_team,
                "score_opponent": score_opponent,
                "raw_score": raw_score,
                "opponent": opponent,
            }
        )
    return out
