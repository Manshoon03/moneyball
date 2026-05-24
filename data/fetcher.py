"""
data/fetcher.py — pulls stats from Baseball Reference + Baseball Savant
and caches to SQLite.

Sources:
  batting_stats_bref()              → standard batting (OBP, SLG, HR, etc.)
  statcast_batter_expected_stats()  → xwOBA, wOBA gap
  statcast_batter_exitvelo_barrels()→ Barrel%, EV
  pitching_stats_bref()             → standard pitching (ERA, WHIP, IP, K, BB)
  statcast_pitcher_expected_stats() → xERA, ERA gap

All sources share the same MLB player ID (mlbID / player_id).
"""

import json
import sys
import os
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


# ── Batting ───────────────────────────────────────────────────────────────────

def fetch_batting(season: int, min_pa: int = 50):
    print(f"[fetcher] batting: Baseball Reference {season}...")
    try:
        bref = batting_stats_bref(season)
        # MLB only, minimum PA
        bref = bref[bref['Lev'].str.startswith('Maj', na=False)]
        bref = bref[bref['PA'] >= min_pa]
    except Exception as e:
        print(f"[fetcher] bref batting failed: {e}")
        return 0

    print(f"[fetcher] batting: Savant expected stats {season}...")
    try:
        savant = statcast_batter_expected_stats(season, minPA=min_pa)
        savant = savant.rename(columns={
            'player_id':                    'mlbID',
            'woba':                         'wOBA',
            'est_woba':                     'xwOBA',
            'est_woba_minus_woba_diff':     'xwOBA_diff',
        })
        savant['mlbID'] = savant['mlbID'].astype(str)
    except Exception as e:
        print(f"[fetcher] savant batting expected failed: {e}")
        savant = None

    print(f"[fetcher] batting: Savant barrels {season}...")
    try:
        barrels = statcast_batter_exitvelo_barrels(season, minBBE=20)
        barrels = barrels.rename(columns={
            'player_id':    'mlbID',
            'brl_percent':  'Barrel%',
            'avg_hit_speed':'EV',
            'ev95percent':  'HardHit%',
        })
        barrels['mlbID'] = barrels['mlbID'].astype(str)
    except Exception as e:
        print(f"[fetcher] savant barrels failed: {e}")
        barrels = None

    bref['mlbID'] = bref['mlbID'].astype(str)

    # Merge expected stats and barrels onto BRef base
    df = bref
    if savant is not None:
        df = df.merge(
            savant[['mlbID', 'wOBA', 'xwOBA', 'xwOBA_diff']],
            on='mlbID', how='left'
        )
    if barrels is not None:
        df = df.merge(
            barrels[['mlbID', 'Barrel%', 'EV', 'HardHit%']],
            on='mlbID', how='left'
        )

    rows = []
    for _, row in df.iterrows():
        player_id = str(row.get('mlbID', ''))
        if not player_id or player_id == 'nan':
            continue
        name  = str(row.get('Name', ''))
        team  = str(row.get('Tm', ''))
        age   = _int(row.get('Age'))
        data  = {
            'PA':        _num(row.get('PA')),
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
    print(f"[fetcher] cached {len(rows)} batters")
    return len(rows)


# ── Pitching ──────────────────────────────────────────────────────────────────

def fetch_pitching(season: int, min_ip: int = 20):
    print(f"[fetcher] pitching: Baseball Reference {season}...")
    try:
        bref = pitching_stats_bref(season)
        bref = bref[bref['Lev'].str.startswith('Maj', na=False)]
        bref = bref[bref['IP'] >= min_ip]
    except Exception as e:
        print(f"[fetcher] bref pitching failed: {e}")
        return 0

    print(f"[fetcher] pitching: Savant expected stats {season}...")
    try:
        savant = statcast_pitcher_expected_stats(season, minPA=50)
        savant = savant.rename(columns={
            'player_id':                    'mlbID',
            'era':                          'ERA_sv',
            'xera':                         'xERA',
            'era_minus_xera_diff':          'ERA_xERA_diff',
            'woba':                         'wOBA_against',
            'est_woba':                     'xwOBA_against',
        })
        savant['mlbID'] = savant['mlbID'].astype(str)
    except Exception as e:
        print(f"[fetcher] savant pitching expected failed: {e}")
        savant = None

    bref['mlbID'] = bref['mlbID'].astype(str)

    df = bref
    if savant is not None:
        df = df.merge(
            savant[['mlbID', 'xERA', 'ERA_xERA_diff', 'wOBA_against', 'xwOBA_against']],
            on='mlbID', how='left'
        )

    rows = []
    for _, row in df.iterrows():
        player_id = 'p_' + str(row.get('mlbID', ''))
        if player_id == 'p_' or player_id == 'p_nan':
            continue
        name  = str(row.get('Name', ''))
        team  = str(row.get('Tm', ''))
        age   = _int(row.get('Age'))
        so    = _num(row.get('SO'))
        bb    = _num(row.get('BB'))
        ip    = _num(row.get('IP'))
        k_pct  = round(so / _num(row.get('BF', 1)), 3) if so and _num(row.get('BF')) else None
        bb_pct = round(bb / _num(row.get('BF', 1)), 3) if bb and _num(row.get('BF')) else None
        data  = {
            'G':              _num(row.get('G')),
            'GS':             _num(row.get('GS')),
            'IP':             ip,
            'W':              _num(row.get('W')),
            'L':              _num(row.get('L')),
            'ERA':            _num(row.get('ERA')),
            'xERA':           _num(row.get('xERA')),
            'ERA_xERA_diff':  _num(row.get('ERA_xERA_diff')),
            'WHIP':           _num(row.get('WHIP')),
            'SO':             so,
            'BB':             bb,
            'HR':             _num(row.get('HR')),
            'BABIP':          _num(row.get('BAbip')),
            'SO9':            _num(row.get('SO9')),
            'SO_W':           _num(row.get('SO/W')),
            'K%':             k_pct,
            'BB%':            bb_pct,
            'wOBA_against':   _num(row.get('wOBA_against')),
            'xwOBA_against':  _num(row.get('xwOBA_against')),
        }
        rows.append((
            player_id, name, team, 'PIT', age,
            json.dumps(data), 'savant_pit', season
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
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _num(v):
    try:
        f = float(v)
        return None if f != f else f   # NaN check
    except (TypeError, ValueError):
        return None
