import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'moneyball.db')


def _conn():
    c = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    return c


def init_db():
    c = _conn()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS player_cache (
            player_id   TEXT PRIMARY KEY,
            name        TEXT,
            team        TEXT,
            position    TEXT,
            age         INTEGER,
            data_json   TEXT,           -- full stat blob as JSON
            source      TEXT,           -- 'fg_bat' | 'fg_pit' | 'savant'
            season      INTEGER,
            fetched_at  TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS salary_cache (
            player_id   TEXT PRIMARY KEY,
            name        TEXT,
            team        TEXT,
            salary      INTEGER,        -- annual value in dollars
            years_left  INTEGER,
            total_value INTEGER,
            contract_note TEXT,
            fetched_at  TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS team_cache (
            team_abbr   TEXT PRIMARY KEY,
            name        TEXT,
            payroll     INTEGER,
            wins        INTEGER,
            losses      INTEGER,
            war_total   REAL,
            fetched_at  TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    c.commit()
    c.close()


def execute(sql, params=()):
    c = _conn()
    c.execute(sql, params)
    c.commit()
    c.close()


def executemany(sql, rows):
    c = _conn()
    c.executemany(sql, rows)
    c.commit()
    c.close()


def query(sql, params=()):
    c = _conn()
    rows = c.execute(sql, params).fetchall()
    c.close()
    return rows


def get_one(sql, params=()):
    c = _conn()
    row = c.execute(sql, params).fetchone()
    c.close()
    return row


def get_meta(key, default=None):
    row = get_one("SELECT value FROM meta WHERE key=?", (key,))
    return row['value'] if row else default


def set_meta(key, value):
    execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)", (key, value))
