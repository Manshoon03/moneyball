"""
data/fetcher.py — pulls stats via pybaseball and caches to SQLite.
Call fetch_season(year) to refresh all batting/pitching data.
"""

import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import db
from datetime import datetime

# Suppress pybaseball progress bars
import pybaseball
pybaseball.cache.enable()

from pybaseball import batting_stats, pitching_stats


# ── Batting ───────────────────────────────────────────────────────────────────

BATTING_COLS = [
    'IDfg', 'Name', 'Team', 'Age', 'G', 'PA', 'AB',
    'AVG', 'OBP', 'SLG', 'OPS', 'wOBA', 'xwOBA',
    'wRC+', 'BABIP', 'BB%', 'K%', 'HR', 'R', 'RBI', 'SB',
    'WAR', 'Barrel%', 'HardHit%', 'EV', 'LA',
    'Pull%', 'Cent%', 'Oppo%',
]


def fetch_batting(season: int, min_pa: int = 100):
    print(f"[fetcher] pulling FanGraphs batting {season}...")
    try:
        df = batting_stats(season, qual=min_pa)
    except Exception as e:
        print(f"[fetcher] batting fetch failed: {e}")
        return 0

    rows = []
    for _, row in df.iterrows():
        available = {c: row.get(c) for c in BATTING_COLS if c in df.columns}
        player_id = str(available.get('IDfg', ''))
        if not player_id:
            continue
        name     = str(available.get('Name', ''))
        team     = str(available.get('Team', ''))
        age      = _int(available.get('Age'))
        position = 'BAT'
        rows.append((
            player_id, name, team, position, age,
            json.dumps(_clean(available)), 'fg_bat', season
        ))

    db.executemany(
        """INSERT OR REPLACE INTO player_cache
           (player_id, name, team, position, age, data_json, source, season, fetched_at)
           VALUES (?,?,?,?,?,?,?,?,datetime('now'))""",
        rows
    )
    print(f"[fetcher] cached {len(rows)} batters")
    return len(rows)


# ── Pitching ──────────────────────────────────────────────────────────────────

PITCHING_COLS = [
    'IDfg', 'Name', 'Team', 'Age', 'G', 'GS', 'IP',
    'ERA', 'xERA', 'FIP', 'xFIP', 'WHIP',
    'K%', 'BB%', 'K-BB%', 'HR/9', 'BABIP',
    'LOB%', 'WAR', 'AVG', 'EV', 'Barrel%', 'HardHit%',
]


def fetch_pitching(season: int, min_ip: int = 20):
    print(f"[fetcher] pulling FanGraphs pitching {season}...")
    try:
        df = pitching_stats(season, qual=min_ip)
    except Exception as e:
        print(f"[fetcher] pitching fetch failed: {e}")
        return 0

    rows = []
    for _, row in df.iterrows():
        available = {c: row.get(c) for c in PITCHING_COLS if c in df.columns}
        player_id = 'p_' + str(available.get('IDfg', ''))
        if player_id == 'p_':
            continue
        name  = str(available.get('Name', ''))
        team  = str(available.get('Team', ''))
        age   = _int(available.get('Age'))
        rows.append((
            player_id, name, team, 'PIT', age,
            json.dumps(_clean(available)), 'fg_pit', season
        ))

    db.executemany(
        """INSERT OR REPLACE INTO player_cache
           (player_id, name, team, position, age, data_json, source, season, fetched_at)
           VALUES (?,?,?,?,?,?,?,?,datetime('now'))""",
        rows
    )
    print(f"[fetcher] cached {len(rows)} pitchers")
    return len(rows)


# ── Main entry ────────────────────────────────────────────────────────────────

def fetch_season(season: int):
    b = fetch_batting(season)
    p = fetch_pitching(season)
    db.set_meta(f'last_fetch_{season}', datetime.now().isoformat())
    return b, p


# ── Helpers ───────────────────────────────────────────────────────────────────

def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _clean(d):
    """Convert numpy types to plain Python for JSON serialization."""
    out = {}
    for k, v in d.items():
        if v is None or (isinstance(v, float) and v != v):  # NaN check
            out[k] = None
        else:
            try:
                out[k] = float(v) if '.' in str(v) else int(v)
            except (TypeError, ValueError):
                out[k] = str(v)
    return out
