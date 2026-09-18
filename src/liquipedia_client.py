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

# 429-varautuminen (lisatty 2026-09-18): alkuperainen koodi ei reagoinut
# 429:aan mitenkaan - r.raise_for_status() heitti heti poikkeuksen, kutsuja
# (collect_*.py) nappasi sen, merkitsi rivin 'error':ksi ja siirtyi
# SEURAAVAAN tiimiin/turnaukseen VAIN normaalin 2.1s/30.5s cooldownin
# jalkeen. BUGI HAVAITTU: kun Liquipedia alkoi palauttaa 429:aa laajemmin
# (todennakoisesti aiemman samana paivana tapahtuneen rinnakkaisajon takia
# saatu pidempi IP-tason rajoitus, ei vain per-pyynto-cooldown), skripti
# jyrasi lapi kaikki 75/25/274 rivia muutamassa minuutissa - JOKAINEN
# epaonnistui, ja jatkuva pommitus todennakoisesti PAHENSI/PIDENSI estoa.
# Korjaus: eksponentiaalinen backoff 429:lla (Retry-After-otsikko jos
# annettu) + "circuit breaker" joka lopettaa turhan pommittamisen kokonaan
# hetkeksi kun esto on selvasti laaja-alainen, ei vain yksittainen huono tuuri.
MAX_RETRIES = 4
BACKOFF_START_SECONDS = 30.0
BACKOFF_CAP_SECONDS = 240.0
CIRCUIT_BREAK_AFTER = 3  # peräkkäistä täysin epäonnistunutta pyyntöä (kaikki uusintayritykset loppuun asti)
CIRCUIT_COOLDOWN_SECONDS = 600.0  # 10 min tauko kun esto todetaan laaja-alaiseksi


class LiquipediaClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
        self._last_query_at = 0.0
        self._last_parse_at = 0.0
        self._consecutive_exhaustions = 0
        self._circuit_open_until = 0.0

    def _wait(self, last_attr: str, cooldown: float):
        last = getattr(self, last_attr)
        elapsed = time.monotonic() - last
        if elapsed < cooldown:
            time.sleep(cooldown - elapsed)
        setattr(self, last_attr, time.monotonic())

    def _request(self, params: dict, cooldown_attr: str, cooldown_seconds: float, timeout: int):
        now = time.monotonic()
        if now < self._circuit_open_until:
            remaining = self._circuit_open_until - now
            raise RuntimeError(
                f"Liquipedia-esto todettu laaja-alaiseksi, odotetaan viela {remaining:.0f}s ennen uutta yritysta"
            )

        backoff = BACKOFF_START_SECONDS
        for attempt in range(MAX_RETRIES + 1):
            self._wait(cooldown_attr, cooldown_seconds)
            r = self.session.get(BASE_URL, params=params, timeout=timeout)
            if r.status_code == 429:
                if attempt == MAX_RETRIES:
                    self._consecutive_exhaustions += 1
                    if self._consecutive_exhaustions >= CIRCUIT_BREAK_AFTER:
                        self._circuit_open_until = time.monotonic() + CIRCUIT_COOLDOWN_SECONDS
                    r.raise_for_status()
                retry_after = r.headers.get("Retry-After")
                wait_s = float(retry_after) if retry_after and retry_after.strip().isdigit() else backoff
                time.sleep(wait_s)
                backoff = min(backoff * 2, BACKOFF_CAP_SECONDS)
                continue
            r.raise_for_status()
            self._consecutive_exhaustions = 0
            return r
        raise RuntimeError("_request: saavuttamaton tila")

    def page_exists(self, title: str) -> bool:
        r = self._request(
            {"action": "query", "format": "json", "titles": title},
            "_last_query_at", QUERY_COOLDOWN_SECONDS, 20,
        )
        pages = r.json().get("query", {}).get("pages", {})
        return not any("missing" in p for p in pages.values())

    def resolve_redirect(self, title: str) -> str:
        """Jos "title" on uudelleenohjaussivu, palauttaa kohdesivun otsikon - muuten "title" sellaisenaan.

        Tarpeellinen koska esim. "Spirit" ohjaa "Team Spirit" -sivulle, mutta
        "Spirit/Matches" ei ole olemassa vaikka "Team Spirit/Matches" on -
        alasivuja ei resolvoida automaattisesti perussivun uudelleenohjauksen kautta.
        """
        r = self._request(
            {"action": "query", "format": "json", "titles": title, "redirects": 1},
            "_last_query_at", QUERY_COOLDOWN_SECONDS, 20,
        )
        data = r.json().get("query", {})
        redirects = data.get("redirects")
        if redirects:
            return redirects[0]["to"]
        return title

    def search_best_title(self, query: str) -> Optional[str]:
        r = self._request(
            {"action": "query", "format": "json", "list": "search", "srsearch": query, "srlimit": 5},
            "_last_query_at", QUERY_COOLDOWN_SECONDS, 20,
        )
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

    def resolve_team_base_page(self, team_name: str) -> Optional[str]:
        """Sama resoluutiologiikka kuin resolve_team_page, mutta palauttaa
        joukkueen PAASIVUN otsikon (ei /Matches-alasivua) - kayttoon
        roolituksen ("Player Roster") lukemiseen.

        BUGI 2026-09-17 (loydetty ja korjattu): action=parse EI seuraa
        uudelleenohjauksia automaattisesti (toisin kuin action=query) -
        "G2" on uudelleenohjaussivu "G2 Esports":iin, joten alkuperainen
        "if page_exists(team_name): return team_name" palautti "G2":n
        (page_exists on totta, koska ohjaussivu ITSE on olemassa), ja
        fetch_rendered_html("G2") palautti vain tynkatekstin "Redirect to:
        G2 Esports" - parse_roster_page loysi tietysti 0 rivia. Vaikutti
        11/50 joukkueeseen (mm. FaZe, Vitality, Liquid, Falcons, G2).
        resolve_team_page (ottelut) valtti taman vahingossa, koska
        "{name}/Matches" (esim. "G2/Matches") ei ole itse ohjaussivu vaan
        EI OLE OLEMASSA ollenkaan, mika pakotti fallback-polun kayttoon.
        Korjaus: resolve_redirect() AINA ensin, ei vain fallbackina."""
        resolved = self.resolve_redirect(team_name)
        if self.page_exists(resolved):
            return resolved
        best = self.search_best_title(team_name)
        if best:
            resolved_best = self.resolve_redirect(best)
            if self.page_exists(resolved_best):
                return resolved_best
        return None

    def search_intitle(self, phrase: str) -> list:
        """CirrusSearchin "intitle:" -operaattori: tasmaa vain sivun OTSIKKOON,
        ei koko sivun tekstisisaltoon. Havaittu 2026-09-17 etta tavallinen
        list=search ilman tata palauttaa lahes satunnaisia osumia
        turnausnimille (esim. "Esports World Cup 2026" -> "FOKUS", pelaajan
        sivu jolla sana esiintyy usein) - intitle: on huomattavasti
        tarkempi kun etsitaan nimenomaan turnaussivua."""
        r = self._request(
            {
                "action": "query", "format": "json", "list": "search",
                "srsearch": f'intitle:"{phrase}"', "srlimit": 10,
            },
            "_last_query_at", QUERY_COOLDOWN_SECONDS, 20,
        )
        hits = r.json().get("query", {}).get("search", [])
        return [h["title"] for h in hits]

    # Turnaussarjat joiden nayttonimi ei sisalla Liquipedian oikeaa sarjan
    # nimea. Havaittu 2026-09-17: "IEM Cologne" loytaa intitle:lla useita
    # sivuja ("Intel Extreme Masters/Season XVII/Cologne",
    # ".../2026/Cologne", ...) - lyhenteen KAUTTA haku toimii silti (Liqui-
    # pedian search-indeksi tuntee sen), pidempi nayttonimi ("IEM Cologne
    # Major 2026 Stage 3") EI loyda mitaan koska "Major"/"Stage N" -sanat
    # eivat esiinny otsikossa.
    _SERIES_SHORT_FORM = {
        "IEM": 2,    # "IEM Cologne" (sarja + kaupunki)
        "BLAST": 2,  # "BLAST Bounty" (sarja + alasarja)
    }

    def resolve_tournament_page(self, tournament_string: str) -> Optional[str]:
        """Yrittaa loytaa turnaus(lava)-merkkijonoa (esim. "Esports World Cup
        2026 - Playoffs", historical_matches.tournament-kentasta) vastaavan
        Liquipedia-sivun otsikon bracket-datan hakua varten.

        Turnaussivujen otsikot ovat hierarkkisia (esim. "Esports World
        Cup/2026/Qualifier/Group Stage") eivatka tasmaa naytto-nimeen
        sanatarkasti, joten haetaan runkonimella (kaikki ennen ensimmaista
        " - "/": "/"#N" -erotinta, esim. "Esports World Cup 2026 - Group A"
        -> "Esports World Cup 2026") intitle:-operaattorilla, joka tasmaa
        vain otsikkoon (ei koko sivun sisaltoon - HAVAITTU BUGI: tavallinen
        full-text-haku palautti systemaattisesti vaaria sivuja, esim.
        pelaajien/joukkueiden sivuja joilla turnauksen nimi mainitaan usein).

        Osa sarjoista (esim. IEM) elaa Liquipediassa monena eri kautena
        saman kaupungin alla ("Season XIV/Beijing", "Season XVII/Cologne",
        "2026/Cologne", ...) - intitle-haku palauttaa TASSA tapauksessa
        useita osumia. Jotta ei vahingossa poimita vaaran vuoden dataa
        (esim. 2019 Beijing sekoittuisi 2026-datasettiimme), useamman
        osuman tapauksessa hyvaksytaan vain sellainen jossa "2026" esiintyy
        otsikossa; jos yksikaan ei tasmaa eika ainoa osuma sisalla "2026":ta,
        merkitaan not_found mieluummin kuin arvataan vaara kausi.

        Kattavuus on siis edelleen epatasaista, mutta tietoisesti
        varovainen - vaarat sivut ovat pahempi ongelma kuin puuttuvat."""
        import re as _re

        base = _re.split(r"\s*[:\-]\s*|\s*#\d+", tournament_string)[0].strip()
        num_match = _re.search(r"#(\d+)", tournament_string)
        num_suffix = num_match.group(1) if num_match else None

        words = base.split()
        phrases = [(base, False, [])]
        for prefix, n_words in self._SERIES_SHORT_FORM.items():
            if words and words[0].upper() == prefix and len(words) >= n_words:
                short = " ".join(words[:n_words])
                if short != base:
                    # lyhyempi muoto ensin, mutta merkitty "epavarmaksi" -
                    # sarjalla on usein monta vanhaa kautta ja monta
                    # rinnakkaista alasarjaa (Summer/Winter/Fall/Spring)
                    # saman kaupungin/vuoden alla, joten vaaditaan "2026"
                    # JA joku erottava lisasana ("Summer" jne.) otsikossa -
                    # ei hyvaksyta sokeasti mitaan yksittaista osumaa.
                    phrases.insert(0, (short, True, words[n_words:]))
                break

        for phrase, uncertain, extra_words in phrases:
            hits = self.search_intitle(phrase)
            if not hits:
                continue

            # Numeroitu turnaussarja ilman vuosilukua otsikossa (esim.
            # "BC.Game Masters Championship #2" -> ".../Championship/2") -
            # kokeillaan taman ensin, ennen vuosi-/lisasanaerottelua.
            if num_suffix and len(hits) > 1:
                num_hits = [h for h in hits if h.rstrip("/").rsplit("/", 1)[-1] == num_suffix]
                if len(num_hits) == 1:
                    resolved = self.resolve_redirect(num_hits[0])
                    if self.page_exists(resolved):
                        return resolved

            year_hits = [h for h in hits if "2026" in h]
            pick = None
            if len(year_hits) == 1:
                pick = year_hits[0]
            elif len(year_hits) > 1:
                # useampi 2026-osuma (esim. eri vuodenajan alasarjat) -
                # yritetaan erottaa lisasanalla (esim. "Fall" hakusanasta
                # "BLAST Open Fall 2026" osuu "BLAST/Open/2026/Fall":iin).
                for w in extra_words:
                    if len(w) < 4:
                        continue
                    match = next((h for h in year_hits if w.lower() in h.lower()), None)
                    if match:
                        pick = match
                        break
                if not pick and not uncertain:
                    pick = year_hits[0]
            elif len(hits) == 1 and not uncertain:
                pick = hits[0]
            if not pick:
                continue
            resolved = self.resolve_redirect(pick)
            if self.page_exists(resolved):
                return resolved
        return None

    def fetch_rendered_html(self, page_title: str) -> str:
        r = self._request(
            {"action": "parse", "format": "json", "page": page_title, "prop": "text"},
            "_last_parse_at", PARSE_COOLDOWN_SECONDS, 30,
        )
        data = r.json()
        if "error" in data:
            raise RuntimeError(f"Liquipedia API-virhe sivulla {page_title}: {data['error']}")
        return data["parse"]["text"]["*"]


def parse_matches_table(html: str, team_name: str = None) -> list:
    """Jasentaa joukkuesivun "/Matches"-taulukon riveiksi.

    Sarakkeet 0-5 (Date/Tier/Type/peli-ikoni/turnausikoni/Tournament) ovat
    vakioita, mutta BUGI (loydetty 2026-09-17, kayttajan pyytaessa lisaa
    puutteita - NRG:n data oli lahes tyhjaa, 21/27 rivista virheellisia):
    osa joukkueiden /Matches-sivuista kayttaa 12-sarakkeista "team1 | tulos
    | pisteet | tulos | team2" -asettelua JOSSA ON YLIMAARAINEN oman
    joukkueen ikonisarake ENNEN pistesaraketta (havaittu NRG:lla), toiset
    (esim. Natus Vincere, alkuperainen referenssi) kayttavat 10-sarakkeista
    asettelua ilman sita. Vanha koodi oletti AINA tds[7]=pisteet, tds[8]=
    vastustaja - NRG:n tapauksessa tds[7] oli tyhja tulosmerkki-ikoni ja
    tds[8] oikea pistesarake, joten "opponent"-kenttaan tallentui vahingossa
    PISTELUKEMA ("2:3") teksti, ja score_team/score_opponent jaivat None:ksi
    (koska tds[8]:n sisalto ei ollutkaan linkki vaan tekstia).

    KORJAUS: pistesarake etsitaan DYNAAMISESTI ensimmaisena tds:na (index
    >= 6) jossa on >=2 <span>-elementtia JOTKA molemmat parsiutuvat
    kokonaisluvuiksi - ei oleteta kiintea indeksia. Vastustaja etsitaan
    vastaavasti ensimmaisena pistesarakkeen JALKEISENA tds:na jossa on
    joukkuelinkki (<a title=...>), jonka otsikko EI ole oma joukkue
    (team_name-parametri, jos annettu - suojaa siltä etta oman joukkueen
    ikonisarake tulkittaisiin vastustajaksi)."""
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

        score_idx = None
        score_team = score_opponent = None
        raw_score = None
        for i in range(6, len(tds)):
            spans = tds[i].find_all("span")
            if len(spans) < 2:
                continue
            try:
                a = int(spans[0].get_text(strip=True))
                b = int(spans[-1].get_text(strip=True))
            except ValueError:
                continue
            score_idx = i
            score_team, score_opponent = a, b
            raw_score = tds[i].get_text(strip=True)
            break
        if score_idx is None:
            continue  # ei parsittavaa tulosta talla rivilla (esim. tuleva ottelu)

        opponent = None
        for i in range(score_idx + 1, len(tds)):
            opp_link = tds[i].find("a", title=True)
            if not opp_link:
                continue
            title = opp_link["title"]
            if team_name and title == team_name:
                continue
            opponent = title
            break

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


def parse_bracket_matches(html: str) -> list:
    """Jasentaa turnauksen bracket-sivun ottelupopupit (.brkts-match-popup-
    wrapper) kartta- ja puoliskotasolle asti.

    Havaittu rakenne 2026-09-17 (esim. PGL/2024/Copenhagen -sivulta):
      .brkts-opponent-entry[aria-label]           -> joukkueen nimi
      .brkts-opponent-score-inner                 -> ottelun (Bo3/Bo5) sarjatulos
      [data-timestamp]                             -> ottelun UTC-ajankohta
      .brkts-popup-body-grid > .brkts-popup-body-grid-row (yksi per kartta):
        .brkts-popup-body-grid-row-detail > 3x .brkts-popup-spaced:
          [0] joukkue1: .brkts-popup-body-detailed-scores-main-score +
              spanit (.brkts-cs-score-color-ct/-t) = puoliskotulokset
          [1] karttanimi (<a title="...">)
          [2] joukkue2: sama rakenne kuin [0]
    Kartta jatetaan pois jos jompikumpi paatulos puuttuu (ei viela pelattu,
    esim. GSL-sarjan tarpeeton paatoserapelilauta)."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    out = []

    for wrapper in soup.find_all("div", class_="brkts-match-popup-wrapper"):
        opponents = wrapper.find_all("div", class_="brkts-opponent-entry")
        if len(opponents) != 2:
            continue

        names, series_scores = [], []
        for o in opponents:
            names.append(o.get("aria-label"))
            score_el = o.find("div", class_="brkts-opponent-score-inner")
            txt = score_el.get_text(strip=True) if score_el else None
            try:
                series_scores.append(int(txt))
            except (TypeError, ValueError):
                series_scores.append(None)
        if not names[0] or not names[1]:
            continue

        ts_el = wrapper.find(attrs={"data-timestamp": True})
        match_date_utc = None
        if ts_el:
            try:
                ts = int(ts_el["data-timestamp"])
                if ts > 0:
                    match_date_utc = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
            except (ValueError, TypeError):
                pass

        maps_out = []
        grid = wrapper.find("div", class_="brkts-popup-body-grid")
        if grid:
            for row in grid.find_all("div", class_="brkts-popup-body-grid-row", recursive=False):
                detail = row.find("div", class_="brkts-popup-body-grid-row-detail")
                if not detail:
                    continue
                spaced = detail.find_all("div", class_="brkts-popup-spaced", recursive=False)
                if len(spaced) != 3:
                    continue
                map_link = spaced[1].find("a", title=True)
                map_name = map_link["title"] if map_link else None
                if not map_name:
                    continue

                def _read_score(container_div):
                    main = container_div.find("div", class_="brkts-popup-body-detailed-scores-main-score")
                    if not main:
                        return None, []
                    try:
                        val = int(main.get_text(strip=True))
                    except ValueError:
                        return None, []
                    halves = []
                    for span in container_div.find_all("span", class_="brkts-popup-body-detailed-score"):
                        classes = span.get("class", [])
                        side = "CT" if "brkts-cs-score-color-ct" in classes else (
                            "T" if "brkts-cs-score-color-t" in classes else None)
                        try:
                            hval = int(span.get_text(strip=True))
                        except ValueError:
                            continue
                        halves.append({"side": side, "score": hval})
                    return val, halves

                s1, h1 = _read_score(spaced[0])
                s2, h2 = _read_score(spaced[2])
                if s1 is None or s2 is None:
                    continue

                maps_out.append(
                    {
                        "map_name": map_name,
                        "team1_score": s1,
                        "team2_score": s2,
                        "team1_halves": h1,
                        "team2_halves": h2,
                    }
                )

        out.append(
            {
                "team1": names[0],
                "team2": names[1],
                "team1_series_score": series_scores[0],
                "team2_series_score": series_scores[1],
                "match_date_utc": match_date_utc,
                "maps": maps_out,
            }
        )
    return out


def parse_roster_page(html: str) -> list:
    """Jasentaa joukkuesivun "Player Roster" -osion (Active + kaikki
    Former-taulut) pelaajien liittymis-/lahtopaivamaariksi.

    Havaittu rakenne 2026-09-17 (esim. Natus Vincere -sivu):
      h3#Active            -> taulu: ID, Name, [], Join Date
      h3#Former, #Former_2 -> taulu: ID, [], Name, [], Join Date, Inactive Date, Leave Date, New Team

    YKSINKERTAISTUS (v1): Inactive Date / laina-abbr-tekstit (esim.
    "Was on loan to X") jatetaan huomiotta - kaytetaan vain ensimmaista
    ja viimeista YYYY-MM-DD-paivamaaraa rivilta (join / leave). Coach-
    ja muu organisaatiohenkilosto EI ole taalla (eri h2-osio "Organization").
    """
    import re

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    out = []

    for heading in soup.find_all("h3"):
        hid = heading.get("id", "")
        if hid != "Active" and not hid.startswith("Former"):
            continue
        is_active = hid == "Active"

        wrapper = heading.find_parent("div", class_="mw-heading")
        table_div = wrapper.find_next_sibling("div", class_="table2") if wrapper else None
        if table_div is None:
            continue
        table = table_div.find("table")
        if table is None:
            continue

        for row in table.find_all("tr", class_=lambda c: c and "row--body" in c):
            tds = row.find_all("td", recursive=False)
            if not tds:
                continue
            id_link = tds[0].find("a", title=True)
            if not id_link:
                continue
            player_id = id_link["title"].replace(" (page does not exist)", "")

            dates = []
            for td in tds:
                for sup in td.find_all("sup"):
                    sup.decompose()
                text = td.get_text(strip=True)
                m = re.match(r"(\d{4}-\d{2}-\d{2})", text)
                if m:
                    dates.append(m.group(1))

            join_date = dates[0] if dates else None
            leave_date = None if is_active else (dates[-1] if len(dates) >= 2 else None)

            if not join_date:
                continue
            out.append({"player_id": player_id, "join_date": join_date, "leave_date": leave_date})

    return out


def parse_roster_transitions(html: str) -> list:
    """Jasentaa joukkuesivun "Former"-osion SIIRTYMA-taulukon (ERI taulu
    kuin parse_roster_page:n Active/Former-roolilistaus) - rivit joissa on
    kulmaikoni <i class="fas fa-angle-right"> pelaaja_pois <-> pelaaja_tilalle
    -parin valissa, seka turnaus jonka yhteydessa siirtyma tapahtui.

    LOYDETTY 2026-09-18 (kayttajan pyynnosta "automatisoi" stand-in-
    havainnointi): tama taulu dokumentoi mm. tilapaiset stand-in-jaksot
    turnauskohtaisesti (esim. Team Vitalylla "jL -> apEX @ PGL Masters
    Bucharest 2026", "jL -> mezii @ BLAST Open Fall 2026" - jalkimmainen
    on juuri se hetki kun alkuperainen pelaaja palasi stand-inin jalkeen).
    HUOM: tama on JALKIKATEEN kirjoitettu/sitaatilla varustettu lahde -
    EI reaaliaikainen, ei sovi "onko stand-in TASSA ottelussa" -kysymykseen,
    vain historiallisen datan retrospektiiviseen tunnistukseen/korjaukseen.

    has_citation=True kun rivilla on <sup>-viite (yleensa stand-in/vaihto-
    syyn lahde) - kayttokelpoinen karkea suodatin, koska osa rivin
    "siirtymista" ovat oikeita pysyvia vaihtoja (ei-siteerattyja) eika
    tilapaisia stand-in-jaksoja; tama funktio EI erottele niita, se
    jattaa sen kutsujan paateltavaksi (has_citation-lippu + tekstin
    sisalto, esim. "stand-in" mainitseva sitaatti-ID, antavat viitteita)."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    out = []

    # Ei navigoida otsikosta (h2/h3 "Former"/"Former_2"/...) tauluun sisarus-
    # suhteen kautta - se osoittautui hauraaksi (siirtymataulu ei aina ole
    # otsikon VALITON seuraava div.table2, useita "Former"-variantteja voi
    # olla). Sen sijaan kaydaan KOKO sivun rivit lapi ja tunnistetaan
    # siirtyma-rivit rakenteen (kulmaikoni) perusteella - yksiselitteinen
    # merkki, ei esiinny tavallisessa roolilistaus- tai ottelutaulukossa.
    for row in soup.find_all("tr"):
        if not row.find("i", class_="fa-angle-right"):
            continue
        links = row.find_all("a")
        texts = [a.get_text(strip=True) for a in links if a.get_text(strip=True)]
        has_citation = bool(row.find("sup"))
        if has_citation and texts and texts[-1].startswith("["):
            texts = texts[:-1]  # viitenumero [54] pois nayttotekstista
        if len(texts) < 3:
            continue
        player_out, player_in, tournament = texts[0], texts[1], texts[2]
        out.append({
            "player_out": player_out, "player_in": player_in,
            "tournament": tournament, "has_citation": has_citation,
        })

    return out
