"""
Ohut client OddsPapi (https://oddspapi.io) REST-rajapinnalle.

Skeema varmistettu oikeaa API-avainta vasten 2026-09-17:
  - /v4/sports: CS2:n sportId = 17 (haetaan silti dynaamisesti, ei kovakoodata).
  - /v4/tournaments?sportId=17: 352 turnausta, joista vain pieni osa on
    kerrallaan "aktiivisia" (futureFixtures/upcomingFixtures/liveFixtures > 0).
    Ei-aktiivisilla turnauksilla /v4/odds-by-tournaments palauttaa 404
    FIXTURE_NOT_FOUND - siksi haetaan VAIN aktiiviset turnaukset.
  - /v4/odds-by-tournaments: fixture sisaltaa participant1Id/participant2Id
    (EI joukkuenimia suoraan) ja bookmakerOdds[bookmaker].markets, jossa
    jokaisella marketilla on bookmakerMarketId muotoa
    ".../<mapIndex>/moneyline" (mapIndex "0" = koko ottelun voittaja,
    "1"/"2"/"3" = yksittaisen kartan voittaja). Outcome-dictin avain (esim.
    "171"/"172") EI ole luotettava puoli - se vaihtelee fixturesta toiseen.
    Luotettava kentta on outcome.players["0"].bookmakerOutcomeId ("home"/"away").
  - /v4/participants?sportId=17: dict {participantId(str): teamName(str)}.
    Tasta resolvoidaan joukkuenimet.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Iterable, Optional

import requests

BASE_URL = "https://api.oddspapi.io"
REQUEST_COOLDOWN_SECONDS = 2.0  # kohtelias viive pyyntojen valissa


class OddsPapiError(RuntimeError):
    pass


@dataclass
class FixtureOdds:
    fixture_id: str
    tournament_id: Optional[str]
    tournament_name: Optional[str]
    team_home: Optional[str]
    team_away: Optional[str]
    scheduled_start_utc: Optional[str]
    bookmaker: str
    price_home: Optional[float]
    price_away: Optional[float]
    raw: dict


class OddsPapiClient:
    def __init__(self, api_key: str, bookmakers: str = "pinnacle", session: Optional[requests.Session] = None):
        if not api_key or api_key == "REPLACE_ME":
            raise OddsPapiError("ODDSPAPI_API_KEY puuttuu tai on asettamaton (.env)")
        self.api_key = api_key
        self.bookmakers = bookmakers
        self.session = session or requests.Session()

    def _get(self, path: str, params: dict, allow_not_found: bool = False) -> Any:
        params = dict(params)
        params["apiKey"] = self.api_key
        url = f"{BASE_URL}{path}"
        resp = self.session.get(url, params=params, timeout=20)
        time.sleep(REQUEST_COOLDOWN_SECONDS)
        if resp.status_code == 429:
            raise OddsPapiError(f"Rate limit (429) polulla {path}")
        if allow_not_found and resp.status_code == 404:
            return None
        resp.raise_for_status()
        try:
            return resp.json()
        except ValueError as exc:
            raise OddsPapiError(f"Vastaus ei ollut JSONia polulla {path}: {resp.text[:200]}") from exc

    def list_sports(self) -> list:
        data = self._get("/v4/sports", {})
        if isinstance(data, dict) and "sports" in data:
            data = data["sports"]
        if not isinstance(data, list):
            raise OddsPapiError(f"Odottamaton /v4/sports-muoto: {type(data)}")
        return data

    def find_cs2_sport_id(self) -> str:
        sports = self.list_sports()
        candidates = ["cs2", "counter-strike 2", "counter-strike", "csgo", "cs:go"]
        for sport in sports:
            name = str(sport.get("sportName") or sport.get("name") or "").lower()
            slug = str(sport.get("sportSlug") or sport.get("slug") or "").lower()
            if any(c in name or c in slug for c in candidates):
                sport_id = sport.get("sportId") or sport.get("id")
                if sport_id is not None:
                    return str(sport_id)
        raise OddsPapiError(
            "CS2-lajia ei loydetty /v4/sports-vastauksesta. "
            "Tarkista data/debug_last_response.json ja paivita find_cs2_sport_id()."
        )

    def list_tournaments(self, sport_id: str) -> list:
        data = self._get("/v4/tournaments", {"sportId": sport_id})
        if isinstance(data, dict) and "tournaments" in data:
            data = data["tournaments"]
        if not isinstance(data, list):
            raise OddsPapiError(f"Odottamaton /v4/tournaments-muoto: {type(data)}")
        return data

    @staticmethod
    def filter_active_tournaments(tournaments: list) -> list:
        """Vain turnaukset joissa on juuri nyt tulevia/live-otteluita.

        Ei-aktiivisille turnauksille odds-by-tournaments palauttaa 404:n, joten
        niiden kysyminen olisi vain hukattuja pyyntoja.
        """
        active = []
        for t in tournaments:
            count = (t.get("futureFixtures") or 0) + (t.get("upcomingFixtures") or 0) + (t.get("liveFixtures") or 0)
            if count > 0:
                active.append(t)
        return active

    def get_participants(self, sport_id: str) -> dict:
        """Palauttaa dictin {participantId(str): joukkueen nimi}."""
        data = self._get("/v4/participants", {"sportId": sport_id})
        if not isinstance(data, dict):
            raise OddsPapiError(f"Odottamaton /v4/participants-muoto: {type(data)}")
        return {str(k): v for k, v in data.items()}

    def get_odds_by_tournaments(self, tournament_ids: Iterable[str]) -> list:
        ids = ",".join(str(t) for t in tournament_ids)
        if not ids:
            return []
        data = self._get(
            "/v4/odds-by-tournaments",
            {"bookmaker": self.bookmakers, "tournamentIds": ids, "oddsFormat": "decimal"},
            allow_not_found=True,
        )
        if data is None:
            return []  # ei fixtureja tassa batchissa juuri nyt
        if isinstance(data, dict) and "fixtures" in data:
            data = data["fixtures"]
        if not isinstance(data, list):
            raise OddsPapiError(f"Odottamaton odds-by-tournaments-muoto: {type(data)}")
        return data

    @staticmethod
    def iter_fixture_odds(fixtures: list, participants: dict) -> Iterable[FixtureOdds]:
        for fx in fixtures:
            if not isinstance(fx, dict) or not fx.get("hasOdds"):
                continue
            fixture_id = str(fx.get("fixtureId") or "")
            if not fixture_id:
                continue
            tournament_id = fx.get("tournamentId")
            team_home = participants.get(str(fx.get("participant1Id")), str(fx.get("participant1Id")))
            team_away = participants.get(str(fx.get("participant2Id")), str(fx.get("participant2Id")))
            start = fx.get("startTime")

            bookmaker_odds = fx.get("bookmakerOdds") or {}
            for bookmaker_name, bm_data in bookmaker_odds.items():
                if not isinstance(bm_data, dict):
                    continue
                market = _find_match_winner_market(bm_data.get("markets") or {})
                price_home, price_away = _extract_home_away_prices(market) if market else (None, None)
                yield FixtureOdds(
                    fixture_id=fixture_id,
                    tournament_id=str(tournament_id) if tournament_id is not None else None,
                    tournament_name=None,
                    team_home=team_home,
                    team_away=team_away,
                    scheduled_start_utc=start,
                    bookmaker=bookmaker_name,
                    price_home=price_home,
                    price_away=price_away,
                    raw=fx,
                )


def _find_match_winner_market(markets: dict) -> Optional[dict]:
    """Etsii koko ottelun voittajan markkinan: bookmakerMarketId paattyy ".../0/moneyline".

    MapIndex "0" = koko ottelu, "1"/"2"/"3" = yksittaisen kartan voittaja - niita
    EI oteta tahan, koska Tehtava 0 kerraa vain ottelun paakerrointa.
    """
    for market in markets.values():
        if not isinstance(market, dict):
            continue
        bmid = str(market.get("bookmakerMarketId") or "")
        parts = bmid.split("/")
        if len(parts) >= 2 and parts[-1] == "moneyline" and parts[-2] == "0":
            return market
    return None


def _extract_home_away_prices(market: dict) -> tuple[Optional[float], Optional[float]]:
    """Poimii home/away-hinnat outcome.players['0'].bookmakerOutcomeId-kentan avulla.

    Outcome-dictin oma avain (esim. "171") EI ole luotettava tunniste puolelle -
    se vaihtelee fixturesta toiseen. bookmakerOutcomeId ("home"/"away") on.
    """
    price_home = price_away = None
    for outcome in (market.get("outcomes") or {}).values():
        if not isinstance(outcome, dict):
            continue
        entry = (outcome.get("players") or {}).get("0")
        if isinstance(entry, list) and entry:
            entry = entry[-1]
        if not isinstance(entry, dict):
            continue
        side = entry.get("bookmakerOutcomeId")
        price = entry.get("price")
        if side == "home":
            price_home = price
        elif side == "away":
            price_away = price
    return price_home, price_away
