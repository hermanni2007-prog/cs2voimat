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
