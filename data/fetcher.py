"""
data/fetcher.py — pulls stats from Baseball Reference + Baseball Savant
and caches to SQLite.

Sources:
  batting_stats_bref()              → standard batting (OBP, SLG, HR, etc.) — all seasons
  pitching_stats_bref()             → standard pitching — all seasons
  statcast_batter_expected_stats()  → xwOBA, Barrel%, EV  — 2015+ only
  statcast_pitcher_expected_stats() → xERA — 2015+ only
  MLB Stats API                     → batter primaryPosition — all seasons

Statcast availability:
  2015+  : full (xwOBA, xERA, Barrel%, EV)
  pre-2015: BRef standard stats only
"""

import json
import sys
import os
import time
import threading
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import db
from datetime import datetime

import pybaseball
pybaseball.cache.enable()

from pybaseball import (
    batting_stats_bref,
    pitching_stats_bref,
    statcast_batter_expected_stats,
    statcast_pitcher_expected_stats,
    statcast_batter_exitvelo_barrels,
)

STATCAST_FIRST_YEAR = 2015   # Statcast installed in all parks
SEASONS_AVAILABLE   = list(range(1980, datetime.now().year + 1))

# ── Progress tracking (for background range fetch) ─────────────────────────────
_fetch_lock   = threading.Lock()
_fetch_status = {'running': False, 'current': '', 'done': 0, 'total': 0, 'errors': []}


def get_status():
    with _fetch_lock:
        return dict(_fetch_status)


def _set_status(**kwargs):
    with _fetch_lock:
        _fetch_status.update(kwargs)


# ── Batting ────────────────────────────────────────────────────────────────────

def fetch_batting(season: int, min_pa: int = 50):
    print(f"[fetcher] batting BRef {season}...")
    try:
        bref = batting_stats_bref(season)
        bref = bref[bref['Lev'].str.startswith('Maj', na=False)]
        bref = bref[bref['PA'] >= min_pa].copy()
    except Exception as e:
        print(f"[fetcher] bref batting {season} failed: {e}")
        return 0

    bref['mlbID'] = bref['mlbID'].astype(str)

    # Statcast enrichment — 2015+ only
    savant  = None
    barrels = None
    if season >= STATCAST_FIRST_YEAR:
        print(f"[fetcher] batting Savant {season}...")
        try:
            s = statcast_batter_expected_stats(season, minPA=min_pa)
            s = s.rename(columns={
                'player_id':                'mlbID',
                'woba':                     'wOBA',
                'est_woba':                 'xwOBA',
                'est_woba_minus_woba_diff': 'xwOBA_diff',
            })
            s['mlbID'] = s['mlbID'].astype(str)
            savant = s
        except Exception as e:
            print(f"[fetcher] savant batting {season} failed: {e}")

        try:
            b = statcast_batter_exitvelo_barrels(season, minBBE=20)
            b = b.rename(columns={
                'player_id':    'mlbID',
                'brl_percent':  'Barrel%',
                'avg_hit_speed':'EV',
                'ev95percent':  'HardHit%',
            })
            b['mlbID'] = b['mlbID'].astype(str)
            barrels = b
        except Exception as e:
            print(f"[fetcher] savant barrels {season} failed: {e}")

    df = bref
    if savant is not None:
        df = df.merge(savant[['mlbID','wOBA','xwOBA','xwOBA_diff']], on='mlbID', how='left')
    if barrels is not None:
        df = df.merge(barrels[['mlbID','Barrel%','EV','HardHit%']], on='mlbID', how='left')

    rows = []
    for _, row in df.iterrows():
        player_id = str(row.get('mlbID', ''))
        if not player_id or player_id == 'nan':
            continue
        name = str(row.get('Name', ''))
        team = str(row.get('Tm', ''))
        age  = _int(row.get('Age'))
        pa   = _num(row.get('PA'))
        so   = _num(row.get('SO'))
        bb   = _num(row.get('BB'))
        data = {
            'PA':        pa,
            'AB':        _num(row.get('AB')),
            'HR':        _num(row.get('HR')),
            'RBI':       _num(row.get('RBI')),
            'BB':        _num(row.get('BB')),
            'SO':        _num(row.get('SO')),
            'SB':        _num(row.get('SB')),
            'AVG':       _num(row.get('BA')),
            'OBP':       _num(row.get('OBP')),
            'SLG':       _num(row.get('SLG')),
            'OPS':       _num(row.get('OPS')),
            'wOBA':      _num(row.get('wOBA')),
            'xwOBA':     _num(row.get('xwOBA')),
            'xwOBA_diff':_num(row.get('xwOBA_diff')),
            'Barrel%':   _num(row.get('Barrel%')),
            'EV':        _num(row.get('EV')),
            'HardHit%':  _num(row.get('HardHit%')),
        }
        rows.append((
            player_id, name, team, 'BAT', age,
            json.dumps(data), 'savant_bat', season
        ))

    db.executemany(
        """INSERT OR REPLACE INTO player_cache
           (player_id, name, team, position, age, data_json, source, season, fetched_at)
           VALUES (?,?,?,?,?,?,?,?,datetime('now'))""",
        rows
    )
    print(f"[fetcher] cached {len(rows)} batters for {season}")
    return len(rows)


# ── Pitching ───────────────────────────────────────────────────────────────────

def fetch_pitching(season: int, min_ip: int = 20):
    print(f"[fetcher] pitching BRef {season}...")
    try:
        bref = pitching_stats_bref(season)
        bref = bref[bref['Lev'].str.startswith('Maj', na=False)]
        bref = bref[bref['IP'] >= min_ip].copy()
    except Exception as e:
        print(f"[fetcher] bref pitching {season} failed: {e}")
        return 0

    bref['mlbID'] = bref['mlbID'].astype(str)

    savant = None
    if season >= STATCAST_FIRST_YEAR:
        print(f"[fetcher] pitching Savant {season}...")
        try:
            s = statcast_pitcher_expected_stats(season, minPA=50)
            s = s.rename(columns={
                'player_id':            'mlbID',
                'era':                  'ERA_sv',
                'xera':                 'xERA',
                'era_minus_xera_diff':  'ERA_xERA_diff',
                'woba':                 'wOBA_against',
                'est_woba':             'xwOBA_against',
            })
            s['mlbID'] = s['mlbID'].astype(str)
            savant = s
        except Exception as e:
            print(f"[fetcher] savant pitching {season} failed: {e}")

    df = bref
    if savant is not None:
        df = df.merge(
            savant[['mlbID','xERA','ERA_xERA_diff','wOBA_against','xwOBA_against']],
            on='mlbID', how='left'
        )

    rows = []
    for _, row in df.iterrows():
        player_id = 'p_' + str(row.get('mlbID', ''))
        if player_id in ('p_', 'p_nan'):
            continue
        name = str(row.get('Name', ''))
        team = str(row.get('Tm', ''))
        age  = _int(row.get('Age'))
        so   = _num(row.get('SO'))
        bb   = _num(row.get('BB'))
        bf   = _num(row.get('BF')) or 1
        g    = _num(row.get('G'))  or 1
        gs   = _num(row.get('GS')) or 0
        role = 'SP' if (gs / g) >= 0.5 else 'RP'
        data = {
            'G':             _num(row.get('G')),
            'GS':            gs,
            'IP':            _num(row.get('IP')),
            'W':             _num(row.get('W')),
            'L':             _num(row.get('L')),
            'ERA':           _num(row.get('ERA')),
            'xERA':          _num(row.get('xERA')),
            'ERA_xERA_diff': _num(row.get('ERA_xERA_diff')),
            'WHIP':          _num(row.get('WHIP')),
            'SO':            so,
            'BB':            bb,
            'HR':            _num(row.get('HR')),
            'BABIP':         _num(row.get('BAbip')),
            'SO9':           _num(row.get('SO9')),
            'SO_W':          _num(row.get('SO/W')),
            'K%':            round(so / bf, 3) if so and bf else None,
            'BB%':           round(bb / bf, 3) if bb and bf else None,
            'wOBA_against':  _num(row.get('wOBA_against')),
            'xwOBA_against': _num(row.get('xwOBA_against')),
        }
        rows.append((
            player_id, name, team, role, age,
            json.dumps(data), 'savant_pit', season
        ))

    db.executemany(
        """INSERT OR REPLACE INTO player_cache
           (player_id, name, team, position, age, data_json, source, season, fetched_at)
           VALUES (?,?,?,?,?,?,?,?,datetime('now'))""",
        rows
    )
    print(f"[fetcher] cached {len(rows)} pitchers for {season}")
    return len(rows)


# ── Positions (MLB Stats API) ──────────────────────────────────────────────────

def fetch_positions(season: int):
    import requests as req
    print(f"[fetcher] positions MLB API {season}...")
    try:
        url  = f"https://statsapi.mlb.com/api/v1/sports/1/players?season={season}"
        data = req.get(url, timeout=15).json()
    except Exception as e:
        print(f"[fetcher] positions {season} failed: {e}")
        return 0

    updated = 0
    for person in data.get('people', []):
        pid = str(person.get('id', ''))
        pos = person.get('primaryPosition', {}).get('abbreviation', '')
        if pid and pos:
            db.execute(
                "UPDATE player_cache SET position=? WHERE player_id=? AND source='savant_bat' AND season=?",
                (pos, pid, season)
            )
            updated += 1
    print(f"[fetcher] positions updated {updated} batters for {season}")
    return updated


# ── Single season ──────────────────────────────────────────────────────────────

def fetch_season(season: int):
    b = fetch_batting(season)
    p = fetch_pitching(season)
    fetch_positions(season)
    db.set_meta(f'last_fetch_{season}', datetime.now().isoformat())
    return b, p


# ── Range fetch (background thread) ───────────────────────────────────────────

def _range_worker(seasons: list):
    total = len(seasons)
    errors = []
    for i, season in enumerate(seasons, 1):
        _set_status(running=True, current=str(season), done=i - 1, total=total)
        try:
            fetch_season(season)
        except Exception as e:
            errors.append(f"{season}: {e}")
            print(f"[fetcher] season {season} error: {e}")
        # Polite pause between seasons — BRef rate limits aggressively
        time.sleep(3)
    _set_status(running=False, current='', done=total, total=total, errors=errors)
    db.set_meta('last_range_fetch', datetime.now().isoformat())
    print(f"[fetcher] range fetch complete. Errors: {errors}")


def fetch_range_background(start: int, end: int):
    """Start a background thread fetching start..end (inclusive). Returns immediately."""
    with _fetch_lock:
        if _fetch_status['running']:
            return False   # already running
    seasons = list(range(start, end + 1))
    t = threading.Thread(target=_range_worker, args=(seasons,), daemon=True, name='moneyball-fetcher')
    t.start()
    return True


def cached_seasons():
    """Return dict of season → {bat, pit} counts already in DB."""
    rows = db.query("""
        SELECT season,
               SUM(CASE WHEN source='savant_bat' THEN 1 ELSE 0 END) as bat,
               SUM(CASE WHEN source='savant_pit' THEN 1 ELSE 0 END) as pit
        FROM player_cache GROUP BY season ORDER BY season DESC
    """)
    return {r['season']: {'bat': r['bat'], 'pit': r['pit']} for r in rows}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _num(v):
    try:
        f = float(v)
        return None if f != f else f   # NaN → None
    except (TypeError, ValueError):
        return None
