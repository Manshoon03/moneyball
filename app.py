"""
Moneyball App — find market inefficiencies in MLB player valuation.
"""

from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
import json
import os
import db

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'moneyball-dev')

VERSION = '0.1.0'
CURRENT_SEASON = 2025


@app.context_processor
def inject_globals():
    return {'VERSION': VERSION, 'CURRENT_SEASON': CURRENT_SEASON}


db.init_db()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_players(source, season=CURRENT_SEASON, min_pa=None, min_ip=None):
    """Return list of player dicts with data_json parsed."""
    rows = db.query(
        "SELECT * FROM player_cache WHERE source=? AND season=? ORDER BY name",
        (source, season)
    )
    players = []
    for r in rows:
        p = dict(r)
        p['data'] = json.loads(p['data_json'] or '{}')
        if min_pa and (p['data'].get('PA') or 0) < min_pa:
            continue
        if min_ip and (p['data'].get('IP') or 0) < min_ip:
            continue
        players.append(p)
    return players


def _moneyball_score(player, source):
    """
    Score = how undervalued is this player right now?
    Positive = buy low (market sleeping on them)
    Negative = sell high (overperforming, due to regress)
    """
    d = player['data']
    score = 0.0

    if source == 'savant_bat':
        # xwOBA vs wOBA gap — core luck indicator
        diff = d.get('xwOBA_diff') or 0   # est_woba - woba (positive = unlucky)
        score += diff * 200                 # scale to roughly -30..+30

        # High Barrel%, low AVG = contact luck, should improve
        barrel = d.get('Barrel%') or 0
        avg    = d.get('AVG') or 0
        if barrel > 10 and avg < 0.250:
            score += (barrel - 10) * 0.4

        # Age bonus — ascending players undervalued vs declining
        age = player.get('age') or 30
        if age <= 26:
            score += 5
        elif age >= 33:
            score -= 3

    elif source == 'savant_pit':
        # ERA vs xERA gap — positive diff = ERA higher than deserved = unlucky
        era_diff = d.get('ERA_xERA_diff') or 0   # era - xera (positive = unlucky)
        score += era_diff * 4

        # High K%, low BB% = good process regardless of ERA
        k_pct  = d.get('K%') or 0
        bb_pct = d.get('BB%') or 0
        if k_pct > 0.25:
            score += (k_pct - 0.25) * 20
        if bb_pct > 0.10:
            score -= (bb_pct - 0.10) * 15

        age = player.get('age') or 30
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
    min_pa  = request.args.get('min_pa', 150, type=int)

    players = _load_players('savant_bat', season, min_pa=min_pa)
    players = [dict(p, score=_moneyball_score(p, 'savant_bat')) for p in players]

    if sort == 'score':
        players.sort(key=lambda x: x['score'], reverse=True)
    elif sort == 'war':
        players.sort(key=lambda x: x['data'].get('WAR') or 0, reverse=True)
    elif sort == 'wrc':
        players.sort(key=lambda x: x['data'].get('wRC+') or 0, reverse=True)
    elif sort == 'age':
        players.sort(key=lambda x: x.get('age') or 99)

    return render_template('batters.html', players=players, season=season,
                           sort=sort, min_pa=min_pa)


@app.route('/pitchers')
def pitchers():
    season  = request.args.get('season', CURRENT_SEASON, type=int)
    sort    = request.args.get('sort', 'score')
    min_ip  = request.args.get('min_ip', 30, type=int)

    players = _load_players('savant_pit', season, min_ip=min_ip)
    players = [dict(p, score=_moneyball_score(p, 'savant_pit')) for p in players]

    if sort == 'score':
        players.sort(key=lambda x: x['score'], reverse=True)
    elif sort == 'war':
        players.sort(key=lambda x: x['data'].get('WAR') or 0, reverse=True)
    elif sort == 'fip':
        players.sort(key=lambda x: x['data'].get('FIP') or 99)
    elif sort == 'age':
        players.sort(key=lambda x: x.get('age') or 99)

    return render_template('pitchers.html', players=players, season=season,
                           sort=sort, min_ip=min_ip)


@app.route('/admin/fetch')
def admin_fetch():
    """Trigger a data refresh manually."""
    season = request.args.get('season', CURRENT_SEASON, type=int)
    from data.fetcher import fetch_season
    b, p = fetch_season(season)
    return jsonify({'ok': True, 'batters': b, 'pitchers': p, 'season': season})


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5003))
    app.run(debug=True, port=port)
