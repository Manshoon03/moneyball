"""
Moneyball App — find market inefficiencies in MLB player valuation.
"""

from flask import Flask, render_template, request, jsonify, redirect
from datetime import date
from dotenv import load_dotenv
import json
import os
import db

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'moneyball-dev')

VERSION        = '0.2.1'
FIRST_SEASON   = 1980
CURRENT_SEASON = date.today().year
ALL_SEASONS    = list(range(CURRENT_SEASON, FIRST_SEASON - 1, -1))


@app.context_processor
def inject_globals():
    from data.fetcher import cached_seasons
    return {
        'VERSION':        VERSION,
        'CURRENT_SEASON': CURRENT_SEASON,
        'ALL_SEASONS':    ALL_SEASONS,
        'cached_set':     set(cached_seasons().keys()),
    }


db.init_db()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_players(source, season=CURRENT_SEASON, min_pa=None, min_ip=None):
    """Return list of player dicts with data_json parsed and derived fields added."""
    rows = db.query(
        "SELECT * FROM player_cache WHERE source=? AND season=? ORDER BY name",
        (source, season)
    )
    players = []
    for r in rows:
        p = dict(r)
        d = json.loads(p['data_json'] or '{}')
        # Compute batter K% and BB% from counting stats
        if source == 'savant_bat':
            pa = d.get('PA') or 0
            if pa > 0:
                d['K%']  = round((d.get('SO') or 0) / pa, 3)
                d['BB%'] = round((d.get('BB') or 0) / pa, 3)
        p['data'] = d
        if min_pa and (d.get('PA') or 0) < min_pa:
            continue
        if min_ip and (d.get('IP') or 0) < min_ip:
            continue
        players.append(p)
    return players


# Sort key maps — used by both batters and pitchers routes
_BAT_SORT = {
    'score':  lambda p: p['score'],
    'age':    lambda p: p.get('age') or 99,
    'pa':     lambda p: p['data'].get('PA')        or 0,
    'obp':    lambda p: p['data'].get('OBP')       or 0,
    'slg':    lambda p: p['data'].get('SLG')       or 0,
    'ops':    lambda p: p['data'].get('OPS')       or 0,
    'woba':   lambda p: p['data'].get('wOBA')      or 0,
    'xwoba':  lambda p: p['data'].get('xwOBA')     or 0,
    'gap':    lambda p: p['data'].get('xwOBA_diff')or 0,
    'barrel': lambda p: p['data'].get('Barrel%')   or 0,
    'ev':     lambda p: p['data'].get('EV')        or 0,
    'hr':     lambda p: p['data'].get('HR')        or 0,
    'kpct':   lambda p: p['data'].get('K%')        or 0,
    'bbpct':  lambda p: p['data'].get('BB%')       or 0,
}

_PIT_SORT = {
    'score':  lambda p: p['score'],
    'age':    lambda p: p.get('age') or 99,
    'ip':     lambda p: p['data'].get('IP')             or 0,
    'era':    lambda p: p['data'].get('ERA')             or 99,
    'xera':   lambda p: p['data'].get('xERA')            or 99,
    'gap':    lambda p: p['data'].get('ERA_xERA_diff')   or 0,
    'kpct':   lambda p: p['data'].get('K%')              or 0,
    'bbpct':  lambda p: p['data'].get('BB%')             or 0,
    'whip':   lambda p: p['data'].get('WHIP')            or 99,
    'so9':    lambda p: p['data'].get('SO9')             or 0,
    'babip':  lambda p: p['data'].get('BABIP')           or 0,
}


def _moneyball_score(player, source):
    """
    Score = how undervalued is this player right now?
    Positive = buy low (market sleeping on them)
    Negative = sell high (overperforming, due to regress)
    """
    d = player['data']
    score = 0.0

    age = player.get('age') or 30

    if source == 'savant_bat':
        has_statcast = bool(d.get('xwOBA'))

        if has_statcast:
            # Statcast era (2015+): xwOBA gap is the primary signal
            diff = d.get('xwOBA_diff') or 0
            score += diff * 200
            barrel = d.get('Barrel%') or 0
            if barrel > 10 and (d.get('AVG') or 0) < 0.250:
                score += (barrel - 10) * 0.4
        else:
            # Pre-Statcast: use original Moneyball metrics
            # OBP is the classic undervalued stat — high OBP, low AVG = market inefficiency
            obp = d.get('OBP') or 0
            avg = d.get('AVG') or 0
            if obp and avg:
                score += (obp - avg) * 100   # large OBP-AVG gap = walks, undervalued
            # High BB%, low K% = good plate discipline
            k_pct  = d.get('K%') or 0
            bb_pct = d.get('BB%') or 0
            if bb_pct > 0.12:
                score += (bb_pct - 0.12) * 80
            if k_pct > 0.25:
                score -= (k_pct - 0.25) * 40

        # Age factor applies in all eras
        if age <= 26:
            score += 5
        elif age >= 33:
            score -= 3

    elif source == 'savant_pit':
        has_statcast = bool(d.get('xERA'))

        if has_statcast:
            era_diff = d.get('ERA_xERA_diff') or 0
            score += era_diff * 4
        else:
            # Pre-Statcast: BABIP + K/BB ratio as luck indicators
            babip = d.get('BABIP') or 0
            if babip > 0.320:
                score += (babip - 0.320) * 60   # unlucky, should improve
            elif babip < 0.260:
                score -= (0.260 - babip) * 40   # lucky, may regress

        # K% and BB% process metrics apply in all eras
        k_pct  = d.get('K%') or 0
        bb_pct = d.get('BB%') or 0
        if k_pct > 0.25:
            score += (k_pct - 0.25) * 20
        if bb_pct > 0.10:
            score -= (bb_pct - 0.10) * 15

        if age <= 26:
            score += 3
        elif age >= 35:
            score -= 3

    return round(score, 1)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    season  = request.args.get('season', CURRENT_SEASON, type=int)
    batters = _load_players('savant_bat', season, min_pa=150)
    pitchers = _load_players('savant_pit', season, min_ip=30)

    has_data = bool(batters or pitchers)

    # Top buy-low and sell-high teaser for dashboard
    scored_bat = sorted(
        [dict(p, score=_moneyball_score(p, 'savant_bat')) for p in batters],
        key=lambda x: x['score'], reverse=True
    )
    buy_low  = scored_bat[:5]
    sell_high = list(reversed(scored_bat))[:5]

    return render_template('index.html',
                           has_data=has_data,
                           buy_low=buy_low,
                           sell_high=sell_high,
                           season=season,
                           batter_count=len(batters),
                           pitcher_count=len(pitchers))


@app.route('/batters')
def batters():
    season  = request.args.get('season', CURRENT_SEASON, type=int)
    sort    = request.args.get('sort', 'score')
    order   = request.args.get('order', 'desc')
    min_pa  = request.args.get('min_pa', 150, type=int)

    players = _load_players('savant_bat', season, min_pa=min_pa)
    players = [dict(p, score=_moneyball_score(p, 'savant_bat')) for p in players]

    key_fn  = _BAT_SORT.get(sort, _BAT_SORT['score'])
    players.sort(key=key_fn, reverse=(order != 'asc'))

    return render_template('batters.html', players=players, season=season,
                           sort=sort, order=order, min_pa=min_pa)


@app.route('/pitchers')
def pitchers():
    season  = request.args.get('season', CURRENT_SEASON, type=int)
    sort    = request.args.get('sort', 'score')
    order   = request.args.get('order', 'desc')
    min_ip  = request.args.get('min_ip', 30, type=int)

    players = _load_players('savant_pit', season, min_ip=min_ip)
    players = [dict(p, score=_moneyball_score(p, 'savant_pit')) for p in players]

    key_fn  = _PIT_SORT.get(sort, _PIT_SORT['score'])
    players.sort(key=key_fn, reverse=(order != 'asc'))

    return render_template('pitchers.html', players=players, season=season,
                           sort=sort, order=order, min_ip=min_ip)


@app.route('/admin')
def admin():
    from data.fetcher import cached_seasons, get_status, SEASONS_AVAILABLE
    seasons  = cached_seasons()
    status   = get_status()
    return render_template('admin.html',
                           seasons=seasons,
                           all_seasons=SEASONS_AVAILABLE[::-1],  # newest first
                           status=status)


@app.route('/admin/fetch')
def admin_fetch():
    """Fetch a single season synchronously. Redirects to admin page when done."""
    season = request.args.get('season', CURRENT_SEASON, type=int)
    from data.fetcher import fetch_season
    fetch_season(season)
    return redirect('/admin')


@app.route('/admin/fetch-range')
def admin_fetch_range():
    """Start a background fetch for a range of seasons."""
    start = request.args.get('start', FIRST_SEASON, type=int)
    end   = request.args.get('end',   CURRENT_SEASON, type=int)
    from data.fetcher import fetch_range_background
    started = fetch_range_background(start, end)
    return jsonify({'ok': started, 'start': start, 'end': end,
                    'message': 'Already running' if not started else 'Fetch started'})


@app.route('/admin/progress')
def admin_progress():
    from data.fetcher import get_status
    return jsonify(get_status())


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5003))
    app.run(debug=True, port=port)
