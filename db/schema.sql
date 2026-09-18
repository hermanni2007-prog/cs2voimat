-- CS2-pelivoimat: kertoimien keruun tietokantaskeema (Tehtava 0)

CREATE TABLE IF NOT EXISTS matches (
    fixture_id          TEXT PRIMARY KEY,
    sport_slug          TEXT NOT NULL,
    tournament_id       TEXT,
    tournament_name     TEXT,
    team_home           TEXT,
    team_away           TEXT,
    format              TEXT,               -- Bo1 / Bo3 / Bo5 / NULL jos ei tiedossa
    scheduled_start_utc TEXT,                -- ISO8601
    first_seen_utc      TEXT NOT NULL,
    source              TEXT NOT NULL DEFAULT 'oddspapi',
    raw_json            TEXT                 -- ensimmaisen naon raakavastaus, skeeman tarkistusta varten
);

CREATE TABLE IF NOT EXISTS odds_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    fixture_id      TEXT NOT NULL REFERENCES matches(fixture_id),
    captured_utc    TEXT NOT NULL,
    bookmaker       TEXT NOT NULL,
    price_home      REAL,
    price_away      REAL,
    is_opening      INTEGER NOT NULL DEFAULT 0,
    is_closing      INTEGER NOT NULL DEFAULT 0,
    source          TEXT NOT NULL DEFAULT 'oddspapi',
    raw_json        TEXT
);

CREATE INDEX IF NOT EXISTS idx_odds_fixture ON odds_snapshots(fixture_id);
CREATE INDEX IF NOT EXISTS idx_odds_opening ON odds_snapshots(fixture_id, is_opening);
CREATE INDEX IF NOT EXISTS idx_odds_closing ON odds_snapshots(fixture_id, is_closing);

-- Ajolokitaulu: jokainen ajastettu ajo kirjaa tanne rivin.
-- Tama on se taulu josta nahdaan, onko keruu oikeasti pyorinyt vai kaatunut hiljaa.
CREATE TABLE IF NOT EXISTS collector_runs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_utc          TEXT NOT NULL,
    status           TEXT NOT NULL,          -- ok / error / skipped_no_key
    fixtures_seen    INTEGER DEFAULT 0,
    new_matches      INTEGER DEFAULT 0,
    new_opening      INTEGER DEFAULT 0,
    new_closing      INTEGER DEFAULT 0,
    error_message    TEXT
);

-- Tehtava 1: historialliset ottelutulokset Liquipedian joukkuekohtaisilta
-- "/Matches"-sivuilta. Rajattu viimeiseen 4 kuukauteen ja top-50-joukkueisiin
-- (data/top50_teams.json, lahde: Valve VRS). Yksi rivi = yksi ottelu/kartta
-- yhden joukkueen nakokulmasta - sama ottelu voi esiintya kahdesti (kerran
-- kummankin joukkueen sivulta luettuna), dedupe unique-indeksilla.
CREATE TABLE IF NOT EXISTS historical_matches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    match_date_utc  TEXT NOT NULL,
    team            TEXT NOT NULL,
    opponent        TEXT NOT NULL,
    tier            TEXT,               -- S-Tier / A-Tier / B-Tier / ...
    match_type      TEXT,               -- Offline (LAN) / Online
    tournament      TEXT,
    score_team      INTEGER,
    score_opponent  INTEGER,
    raw_score       TEXT,               -- alkuperainen tekstimuotoinen tulos
    source_page     TEXT NOT NULL,      -- esim. "Natus Vincere/Matches"
    collected_utc   TEXT NOT NULL,
    UNIQUE(match_date_utc, team, opponent, tournament)
);

CREATE INDEX IF NOT EXISTS idx_hist_team ON historical_matches(team);
CREATE INDEX IF NOT EXISTS idx_hist_date ON historical_matches(match_date_utc);

-- Etenemisen seuranta joukkuesivujen lapikaynnille - antaa ajon jatkua
-- siita mihin edellinen jai (Liquipedian 30s/pyynto -rajoite tekee taydesta
-- 50 joukkueen kierroksesta ~25 min operaation, ei mahdu yhteen GH Actions -ajoon
-- luotettavasti yhdessa kierroksessa aina).
CREATE TABLE IF NOT EXISTS history_team_progress (
    team            TEXT PRIMARY KEY,
    liquipedia_page TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending / ok / not_found / error
    matches_found   INTEGER DEFAULT 0,
    last_attempt_utc TEXT,
    error_message   TEXT
);

-- Kokoonpanot: pelaajan liittymis-/lahtopaiva per joukkue, Liquipedian
-- paasivun "Player Roster" -osiosta (Active + kaikki Former-taulut).
-- leave_date NULL = pelaaja on edelleen (sivun viimeisimman muokkauksen
-- hetkella) aktiivinen. Taulukko korvataan kokonaan joukkueen osalta
-- joka keruukerralla (ei UNIQUE-indeksia/dedupea - yksinkertaisin oikea
-- malli kun koko rooli-lista haetaan aina uudelleen kerralla).
CREATE TABLE IF NOT EXISTS team_rosters (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    team            TEXT NOT NULL,
    player_id       TEXT NOT NULL,
    join_date       TEXT,
    leave_date      TEXT,
    source_page     TEXT NOT NULL,
    collected_utc   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_roster_team ON team_rosters(team);

CREATE TABLE IF NOT EXISTS roster_team_progress (
    team            TEXT PRIMARY KEY,
    liquipedia_page TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending / ok / not_found / error
    entries_found   INTEGER DEFAULT 0,
    last_attempt_utc TEXT,
    error_message   TEXT
);

-- Tehtava 4: kartta- ja puoliskotason tulokset turnausten bracket-sivuilta
-- (".../<Turnaus>" -sivun .brkts-match-popup-wrapper -elementit). Tama on
-- eri lahde kuin historical_matches (joukkuekohtaiset /Matches-taulukot),
-- jotka eivat sisalla kartta- tai CT/T-puoliskotietoa - vain ottelun
-- kokonaistuloksen. Yksi rivi = yksi kartta yhdesta ottelusta.
CREATE TABLE IF NOT EXISTS historical_maps (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    match_date_utc      TEXT,
    tournament          TEXT NOT NULL,      -- historical_matches.tournament -kentan alkuperainen merkkijono
    liquipedia_page      TEXT NOT NULL,      -- resolvoitu bracket-sivun otsikko
    team1               TEXT NOT NULL,
    team2               TEXT NOT NULL,
    team1_series_score  INTEGER,
    team2_series_score  INTEGER,
    map_order           INTEGER NOT NULL,   -- 1., 2., 3. kartta ottelussa
    map_name            TEXT NOT NULL,
    team1_score         INTEGER NOT NULL,
    team2_score         INTEGER NOT NULL,
    team1_halves_json   TEXT,               -- [{"side":"CT","score":8}, ...] puoliskojarjestyksessa
    team2_halves_json   TEXT,
    collected_utc       TEXT NOT NULL,
    UNIQUE(liquipedia_page, team1, team2, map_order, match_date_utc)
);

CREATE INDEX IF NOT EXISTS idx_maps_tournament ON historical_maps(tournament);
CREATE INDEX IF NOT EXISTS idx_maps_mapname ON historical_maps(map_name);

-- Etenemisen seuranta turnaus-per-turnaus (sama resumable-malli kuin
-- history_team_progress). "tournament" = historical_matches.tournament
-- -kentan raakamerkkijono (181 kpl 2026-09-17), koska yksittaisia
-- turnauslavoja (Group A / Playoffs / ...) kasitellaan usein omina
-- Liquipedia-sivuinaan.
CREATE TABLE IF NOT EXISTS bracket_progress (
    tournament          TEXT PRIMARY KEY,
    liquipedia_page      TEXT,
    status              TEXT NOT NULL DEFAULT 'pending',  -- pending / ok / ok_no_brackets / not_found / error
    matches_found        INTEGER DEFAULT 0,
    maps_found            INTEGER DEFAULT 0,
    last_attempt_utc     TEXT,
    error_message         TEXT
);

-- Rosterin siirtymataulukko (lisatty 2026-09-18, kayttajan pyynnosta
-- "automatisoi tama" stand-in-havainnoinnin jalkeen): team_rosters (Active/
-- Former -listaus, join_date/leave_date) EI tallenna tilapaisia stand-in-
-- vaihtoja - Liquipedian paasivulla ("Former"-otsikon alla, ERI taulu kuin
-- Active/Former-roolilistaus) on kuitenkin erillinen "siirtyma"-taulu joka
-- listaa player_out -> player_in -parit TURNAUSKOHTAISESTI, usein sitaatilla
-- (esim. "jL -> apEX @ PGL Masters Bucharest 2026", "jL -> mezii @ BLAST
-- Open Fall 2026" Vitalylla - tasta nakyy stand-in-jakson alku ja loppu).
-- HUOM: TAMA ON JALKIKATEEN DOKUMENTOITU LAHDE (vaatii sitaatin/lahteen),
-- ei reaaliaikainen - ei sovellu "onko stand-in TASSA ottelussa juuri nyt"
-- -kysymykseen, vain historiallisen datan retrospektiiviseen korjaukseen.
CREATE TABLE IF NOT EXISTS roster_transitions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    team            TEXT NOT NULL,
    player_out      TEXT,
    player_in       TEXT,
    tournament      TEXT,               -- Liquipedian nayttonimi turnaukselle (linkin teksti)
    has_citation    INTEGER NOT NULL DEFAULT 0,  -- 1 jos rivilla oli lahdeviite (yleensa stand-in/vaihto-syy)
    source_page     TEXT NOT NULL,
    collected_utc   TEXT NOT NULL,
    UNIQUE(team, player_out, player_in, tournament)
);

CREATE INDEX IF NOT EXISTS idx_roster_trans_team ON roster_transitions(team);
CREATE INDEX IF NOT EXISTS idx_roster_trans_tournament ON roster_transitions(tournament);

CREATE TABLE IF NOT EXISTS roster_transition_progress (
    team            TEXT PRIMARY KEY,
    liquipedia_page TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending / ok / not_found / error
    entries_found   INTEGER DEFAULT 0,
    last_attempt_utc TEXT,
    error_message   TEXT
);

-- Tehtava 7 (valmisteltu etukateen, EI VIELA KAYTOSSA): CLV (closing line
-- value) -loki. Rakenne valmis nyt jotta Tehtava 0:n kaynnistyessa (Coolbet-
-- tilaus) sen voi ottaa suoraan kayttoon ilman skeemamuutosta. Tayttyy
-- vain jos/kun oikeaa markkinadataa (odds_snapshots) on riittavasti - katso
-- src/clv.py:n yla kommentti laskentakaavasta.
CREATE TABLE IF NOT EXISTS clv_log (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    fixture_id            TEXT NOT NULL REFERENCES matches(fixture_id),
    bookmaker             TEXT NOT NULL,
    model_prob_team       REAL NOT NULL,   -- p_malli hetkella jolloin "veto" olisi tehty
    market_prob_team_bet  REAL NOT NULL,   -- markkinan marginaalipoistettu tn samalla hetkella
    price_at_bet          REAL NOT NULL,
    price_closing         REAL,            -- taytetaan jalkikateen kun sulkeutuva kerroin tiedossa
    clv_pct               REAL,            -- (price_at_bet / price_closing - 1) - taytetaan samalla
    logged_utc            TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_clv_fixture ON clv_log(fixture_id);
