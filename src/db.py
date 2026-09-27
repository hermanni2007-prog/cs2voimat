"""SQLite-yhteys ja skeeman alustus."""
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "odds.db"
SCHEMA_PATH = ROOT / "db" / "schema.sql"


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Ei WAL-tilaa: skripti ajetaan yhtena kertakayttoisena prosessina (paikallisesti
    # tai GitHub Actionsissa), ja lopputiedosto committoidaan sellaisenaan gittiin -
    # oletus rollback-journal ei jata ylimaaraisia -wal/-shm-tiedostoja.
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    conn = get_connection()
    try:
        schema = SCHEMA_PATH.read_text(encoding="utf-8")
        conn.executescript(schema)
        # CREATE TABLE IF NOT EXISTS ei lisaa sarakkeita olemassa olevaan tauluun.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(historical_matches)")}
        if "tournament_page" not in cols:
            conn.execute("ALTER TABLE historical_matches ADD COLUMN tournament_page TEXT")
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
    print(f"Tietokanta alustettu: {DB_PATH}")
