import os
import threading
import time
import imaplib
import email
import re
import random
from email.header import decode_header
import xml.etree.ElementTree as ET
from urllib.parse import quote
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Flask, request

app = Flask(__name__)

BOT_VERSION = "2026-08-13-v1"

GROUPME_BOT_ID = os.environ.get("GROUPME_BOT_ID")  # fallback / single-group default
GOOGLE_SEARCH_API_KEY = os.environ.get("GOOGLE_SEARCH_API_KEY")
GOOGLE_SEARCH_CX = os.environ.get("GOOGLE_SEARCH_CX")
BRAVE_SEARCH_API_KEY = os.environ.get("BRAVE_SEARCH_API_KEY")
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY")
SERPER_API_KEY = os.environ.get("SERPER_API_KEY")
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
TRACK17_API_KEY = os.environ.get("TRACK17_API_KEY")
AVIATIONSTACK_API_KEY = os.environ.get("AVIATIONSTACK_API_KEY")
# Multi-group support: "groupId:botId|groupId:botId"
GROUPME_BOTS_RAW = os.environ.get("GROUPME_BOTS")


def parse_groupme_bots():
    mapping = {}
    if GROUPME_BOTS_RAW:
        # accept entries separated by |, newlines, or commas
        raw_entries = GROUPME_BOTS_RAW.replace(",", "\n").replace("|", "\n").splitlines()
        for entry in raw_entries:
            entry = entry.strip()
            if not entry:
                continue
            parts = entry.split(":")
            if len(parts) == 2:
                group_id, bot_id = parts[0].strip(), parts[1].strip()
                mapping[group_id] = bot_id
    return mapping


GROUPME_BOTS_MAP = parse_groupme_bots()
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
ESPN_SEASON = os.environ.get("ESPN_SEASON", "2026")
ESPN_S2 = os.environ.get("ESPN_S2")     # only needed for PRIVATE leagues
ESPN_SWID = os.environ.get("ESPN_SWID")  # only needed for PRIVATE leagues

# Multiple teams: "leagueId:teamId:label|leagueId:teamId:label"
# Falls back to single ESPN_LEAGUE_ID / ESPN_TEAM_ID if ESPN_TEAMS isn't set.
ESPN_TEAMS_RAW = os.environ.get("ESPN_TEAMS")
_legacy_league = os.environ.get("ESPN_LEAGUE_ID")
_legacy_team = os.environ.get("ESPN_TEAM_ID")


def parse_espn_teams():
    teams = []
    if ESPN_TEAMS_RAW:
        for entry in ESPN_TEAMS_RAW.split("|"):
            parts = entry.split(":")
            if len(parts) >= 2:
                league_id, team_id = parts[0].strip(), parts[1].strip()
                label = parts[2].strip() if len(parts) > 2 else f"League {league_id}"
                teams.append((league_id, team_id, label))
    elif _legacy_league and _legacy_team:
        teams.append((_legacy_league, _legacy_team, "My Team"))
    return teams

# alias -> (sport, league, ESPN team abbreviation)
TEAM_ALIASES = {
    # NFL
    "bears": ("football", "nfl", "CHI"), "chicago bears": ("football", "nfl", "CHI"),
    "bills": ("football", "nfl", "BUF"), "buffalo": ("football", "nfl", "BUF"),
    "dolphins": ("football", "nfl", "MIA"),
    "patriots": ("football", "nfl", "NE"), "pats": ("football", "nfl", "NE"),
    "jets": ("football", "nfl", "NYJ"),
    "ravens": ("football", "nfl", "BAL"),
    "bengals": ("football", "nfl", "CIN"),
    "browns": ("football", "nfl", "CLE"),
    "steelers": ("football", "nfl", "PIT"),
    "texans": ("football", "nfl", "HOU"),
    "colts": ("football", "nfl", "IND"),
    "jaguars": ("football", "nfl", "JAX"), "jags": ("football", "nfl", "JAX"),
    "titans": ("football", "nfl", "TEN"),
    "broncos": ("football", "nfl", "DEN"),
    "chiefs": ("football", "nfl", "KC"),
    "raiders": ("football", "nfl", "LV"),
    "chargers": ("football", "nfl", "LAC"),
    "cowboys": ("football", "nfl", "DAL"),
    "giants": ("football", "nfl", "NYG"),
    "eagles": ("football", "nfl", "PHI"), "philly": ("football", "nfl", "PHI"),
    "commanders": ("football", "nfl", "WSH"),
    "packers": ("football", "nfl", "GB"), "green bay": ("football", "nfl", "GB"),
    "lions": ("football", "nfl", "DET"),
    "vikings": ("football", "nfl", "MIN"),
    "49ers": ("football", "nfl", "SF"), "niners": ("football", "nfl", "SF"),
    "seahawks": ("football", "nfl", "SEA"),
    "rams": ("football", "nfl", "LAR"),
    "cardinals": ("football", "nfl", "ARI"),
    "buccaneers": ("football", "nfl", "TB"), "bucs": ("football", "nfl", "TB"),
    "falcons": ("football", "nfl", "ATL"),
    "panthers": ("football", "nfl", "CAR"),
    "saints": ("football", "nfl", "NO"),
    # MLB - all 30 teams
    "cubs": ("baseball", "mlb", "CHC"),
    "white sox": ("baseball", "mlb", "CHW"), "sox": ("baseball", "mlb", "CHW"),
    "yankees": ("baseball", "mlb", "NYY"),
    "mets": ("baseball", "mlb", "NYM"),
    "red sox": ("baseball", "mlb", "BOS"),
    "dodgers": ("baseball", "mlb", "LAD"),
    "giants baseball": ("baseball", "mlb", "SF"),
    "astros": ("baseball", "mlb", "HOU"),
    "braves": ("baseball", "mlb", "ATL"),
    "phillies": ("baseball", "mlb", "PHI"),
    "cardinals baseball": ("baseball", "mlb", "STL"),
    "brewers": ("baseball", "mlb", "MIL"),
    "guardians": ("baseball", "mlb", "CLE"),
    "twins": ("baseball", "mlb", "MIN"),
    "rangers": ("baseball", "mlb", "TEX"),
    "padres": ("baseball", "mlb", "SD"),
    "mariners": ("baseball", "mlb", "SEA"),
    "orioles": ("baseball", "mlb", "BAL"),
    "rays": ("baseball", "mlb", "TB"),
    "blue jays": ("baseball", "mlb", "TOR"),
    "diamondbacks": ("baseball", "mlb", "ARI"), "dbacks": ("baseball", "mlb", "ARI"),
    "royals": ("baseball", "mlb", "KC"),
    "tigers": ("baseball", "mlb", "DET"),
    "reds": ("baseball", "mlb", "CIN"),
    "pirates": ("baseball", "mlb", "PIT"),
    "marlins": ("baseball", "mlb", "MIA"),
    "angels": ("baseball", "mlb", "LAA"),
    "athletics": ("baseball", "mlb", "ATH"),
    "nationals": ("baseball", "mlb", "WSH"),
    "rockies": ("baseball", "mlb", "COL"),
}


def scoreboard_url(sport, league):
    return f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard"


def schedule_url(sport, league, abbr):
    return f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/teams/{abbr}/schedule"


def standings_url(sport, league):
    return f"https://site.api.espn.com/apis/v2/sports/{sport}/{league}/standings"


def format_game(event):
    competitions = event.get("competitions", [{}])[0]
    status = competitions.get("status", {}).get("type", {}).get("shortDetail", "")
    competitors = competitions.get("competitors", [])
    home = next((c for c in competitors if c.get("homeAway") == "home"), None)
    away = next((c for c in competitors if c.get("homeAway") == "away"), None)
    if not home or not away:
        return None
    home_name = home.get("team", {}).get("abbreviation", "HOME")
    away_name = away.get("team", {}).get("abbreviation", "AWAY")
    home_score = home.get("score", "0")
    away_score = away.get("score", "0")
    return f"{away_name} {away_score} @ {home_name} {home_score} ({status})", {home_name, away_name}


def get_league_scores(sport, league):
    try:
        resp = requests.get(scoreboard_url(sport, league), timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return f"Couldn't fetch scores right now ({e})"

    events = data.get("events", [])
    lines = []
    for event in events:
        result = format_game(event)
        if result:
            lines.append(result[0])
    return "\n".join(lines) if lines else "No games found right now."


def get_next_game(sport, league, abbr):
    try:
        resp = requests.get(schedule_url(sport, league, abbr), timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return f"Couldn't fetch schedule right now ({e})"

    events = data.get("events", [])
    now = datetime.utcnow()
    upcoming = []
    for event in events:
        date_str = event.get("date", "")
        try:
            game_date = datetime.strptime(date_str, "%Y-%m-%dT%H:%MZ")
        except Exception:
            continue
        if game_date >= now:
            upcoming.append((game_date, event))

    if not upcoming:
        return "Couldn't find their next game."

    upcoming.sort(key=lambda x: x[0])
    game_date, event = upcoming[0]
    competitions = event.get("competitions", [{}])[0]
    competitors = competitions.get("competitors", [])
    home = next((c for c in competitors if c.get("homeAway") == "home"), None)
    away = next((c for c in competitors if c.get("homeAway") == "away"), None)
    home_name = home.get("team", {}).get("abbreviation", "HOME") if home else "HOME"
    away_name = away.get("team", {}).get("abbreviation", "AWAY") if away else "AWAY"
    game_date_ct = game_date.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("America/Chicago"))
    when = game_date_ct.strftime("%a %b %-d, %-I:%M %p CT")
    return f"Not playing right now. Next game: {away_name} @ {home_name} on {when}"


def format_baseball_live(event, my_abbr):
    """Compact live line: score, inning half, outs, bases occupied."""
    competitions = event.get("competitions", [{}])[0]
    competitors = competitions.get("competitors", [])
    home = next((c for c in competitors if c.get("homeAway") == "home"), None)
    away = next((c for c in competitors if c.get("homeAway") == "away"), None)
    if not home or not away:
        return None

    home_abbr = home.get("team", {}).get("abbreviation", "HOME")
    away_abbr = away.get("team", {}).get("abbreviation", "AWAY")
    home_score = home.get("score", "0")
    away_score = away.get("score", "0")

    status = competitions.get("status", {})
    state = status.get("type", {}).get("state")  # 'pre', 'in', 'post'
    detail = status.get("type", {}).get("detail", "")

    line1 = f"{home_abbr} {home_score}, {away_abbr} {away_score}"

    if state != "in":
        return f"{line1}\n{detail}"

    period = status.get("period")
    detail_lower = detail.lower()
    half = "Top" if "top" in detail_lower else ("Bot" if "bottom" in detail_lower else "")
    lines = [line1, f"{half} {period}".strip()]

    situation = competitions.get("situation", {})
    balls = situation.get("balls")
    strikes = situation.get("strikes")
    if balls is not None and strikes is not None:
        lines.append(f"Count: {balls}-{strikes}")

    outs = situation.get("outs")
    if outs is not None:
        lines.append(f"{outs} out" + ("" if outs == 1 else "s"))

    bases = []
    if situation.get("onFirst"):
        bases.append("1")
    if situation.get("onSecond"):
        bases.append("2")
    if situation.get("onThird"):
        bases.append("3")
    if bases:
        lines.append("/".join(bases))

    batter = situation.get("batter", {}).get("athlete", {}).get("displayName")
    if batter:
        lines.append(f"At bat: {batter}")

    return "\n".join(lines)


# alias -> (sport, league, team abbr, name fragment to search for in box score)
PLAYER_ALIASES = {
    "pca": ("baseball", "mlb", "CHC", "Crow-Armstrong"),
    "crow-armstrong": ("baseball", "mlb", "CHC", "Crow-Armstrong"),
    "caleb williams": ("football", "nfl", "CHI", "Williams"),
    "williams": ("football", "nfl", "CHI", "Williams"),
    "ohtani": ("baseball", "mlb", "LAD", "Ohtani"),
}


def find_team_event_id(sport, league, team_abbr):
    try:
        resp = requests.get(scoreboard_url(sport, league), timeout=10)
        resp.raise_for_status()
        for event in resp.json().get("events", []):
            result = format_game(event)
            if result and team_abbr in result[1]:
                return event.get("id")
    except Exception:
        pass
    return None


def format_batting_line(labels, stats, summary_text=None):
    if summary_text:
        return summary_text

    stat_map = dict(zip(labels, stats))
    ab = stat_map.get("AB", "0")
    h = stat_map.get("H", "0")
    hr = stat_map.get("HR")
    rbi = stat_map.get("RBI", "0")
    r = stat_map.get("R", "0")

    parts = [f"{h}-{ab}"]
    if hr not in (None, "0"):
        parts.append("HR" if hr == "1" else f"{hr} HR")
    if rbi not in (None, "0"):
        parts.append(f"{rbi} RBI")
    if r not in (None, "0"):
        parts.append(f"{r} R")
    return ", ".join(parts)


def format_football_stat_line(labels, stats, summary_text=None):
    if summary_text:
        return summary_text
    stat_map = dict(zip(labels, stats))
    # common labels: C/ATT, YDS, AVG, TD, INT, SACKS, QBR, RTG (passing)
    # or CAR, YDS, AVG, TD, LONG (rushing) / REC, YDS, AVG, TD, LONG (receiving)
    parts = []
    for key in ("C/ATT", "CAR", "REC"):
        if key in stat_map:
            parts.append(f"{key} {stat_map[key]}")
            break
    if "YDS" in stat_map:
        parts.append(f"{stat_map['YDS']} YDS")
    if "TD" in stat_map:
        parts.append(f"{stat_map['TD']} TD")
    if "INT" in stat_map:
        parts.append(f"{stat_map['INT']} INT")
    return ", ".join(parts) if parts else ", ".join(f"{l} {v}" for l, v in zip(labels, stats))


def get_player_game_stats(alias):
    entry = PLAYER_ALIASES.get(alias)
    if not entry:
        return f"Don't have a player mapped to \"{alias}\" yet. Tell me the player's name and team and I can add them."

    sport, league, team_abbr, name_fragment = entry
    event_id = find_team_event_id(sport, league, team_abbr)
    if not event_id:
        return f"{team_abbr} isn't playing right now, so no stats to show."

    try:
        resp = requests.get(summary_url(sport, league, event_id), timeout=10)
        resp.raise_for_status()
        summary = resp.json()
    except Exception as e:
        return f"Couldn't fetch player stats right now ({e})"

    boxscore = summary.get("boxscore", {})
    for team_block in boxscore.get("players", []):
        for stat_group in team_block.get("statistics", []):
            labels = stat_group.get("labels", [])
            for athlete_entry in stat_group.get("athletes", []):
                name = athlete_entry.get("athlete", {}).get("displayName", "")
                if name_fragment.lower() not in name.lower():
                    continue
                stats = athlete_entry.get("stats", [])
                summary_text = athlete_entry.get("summary")
                if not stats and not summary_text:
                    continue
                if league == "mlb":
                    line = format_batting_line(labels, stats, summary_text)
                else:
                    line = format_football_stat_line(labels, stats, summary_text)
                return f"{name}: {line}"

    return "Couldn't find stats for that player in today's box score yet (game may not have started)."


def format_football_live(event, my_abbr):
    """Compact live line: score, quarter/clock, possession, down & distance, field position."""
    competitions = event.get("competitions", [{}])[0]
    competitors = competitions.get("competitors", [])
    home = next((c for c in competitors if c.get("homeAway") == "home"), None)
    away = next((c for c in competitors if c.get("homeAway") == "away"), None)
    if not home or not away:
        return None

    home_abbr = home.get("team", {}).get("abbreviation", "HOME")
    away_abbr = away.get("team", {}).get("abbreviation", "AWAY")
    home_score = home.get("score", "0")
    away_score = away.get("score", "0")

    status = competitions.get("status", {})
    state = status.get("type", {}).get("state")
    detail = status.get("type", {}).get("detail", "")

    line1 = f"{home_abbr} {home_score}, {away_abbr} {away_score}"

    if state != "in":
        return f"{line1}\n{detail}"

    lines = [line1, detail]  # detail already reads like "12:34 - 2nd"

    situation = competitions.get("situation", {})
    down_distance = situation.get("shortDownDistanceText")  # e.g. "2nd & 5"
    possession_id = situation.get("possession")
    possession_abbr = None
    if possession_id:
        if home.get("id") == possession_id:
            possession_abbr = home_abbr
        elif away.get("id") == possession_id:
            possession_abbr = away_abbr

    if down_distance:
        lines.append(down_distance)
    field_pos = situation.get("possessionText")  # e.g. "NYJ 35"
    if possession_abbr:
        pos_line = f"Ball: {possession_abbr}"
        if field_pos:
            pos_line += f" ({field_pos})"
        lines.append(pos_line)

    return "\n".join(lines)


def get_team_result(sport, league, abbr):
    try:
        resp = requests.get(scoreboard_url(sport, league), timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return f"Couldn't fetch scores right now ({e})"

    for event in data.get("events", []):
        result = format_game(event)
        if not result:
            continue
        line, abbrs = result
        if abbr in abbrs:
            if league == "mlb":
                rich = format_baseball_live(event, abbr)
                if rich:
                    return rich
            if league == "nfl":
                rich = format_football_live(event, abbr)
                if rich:
                    return rich
            return line

    return get_next_game(sport, league, abbr)


MLB_DIVISIONS = {
    "AL East": ["NYY", "BOS", "TB", "TOR", "BAL"],
    "AL Central": ["CLE", "MIN", "CHW", "DET", "KC"],
    "AL West": ["HOU", "SEA", "TEX", "LAA", "ATH"],
    "NL East": ["ATL", "PHI", "NYM", "MIA", "WSH"],
    "NL Central": ["CHC", "MIL", "STL", "CIN", "PIT"],
    "NL West": ["LAD", "SD", "SF", "ARI", "COL"],
}

NFL_DIVISIONS = {
    "AFC East": ["BUF", "MIA", "NE", "NYJ"],
    "AFC North": ["BAL", "CIN", "CLE", "PIT"],
    "AFC South": ["HOU", "IND", "JAX", "TEN"],
    "AFC West": ["DEN", "KC", "LV", "LAC"],
    "NFC East": ["DAL", "NYG", "PHI", "WSH"],
    "NFC North": ["CHI", "DET", "GB", "MIN"],
    "NFC South": ["ATL", "CAR", "NO", "TB"],
    "NFC West": ["ARI", "LAR", "SF", "SEA"],
}


def get_division_map(league):
    return MLB_DIVISIONS if league == "mlb" else NFL_DIVISIONS


def find_division_name(league, abbr):
    for div_name, abbrs in get_division_map(league).items():
        if abbr in abbrs:
            return div_name
    return None


def _stat_value(entry, stat_names, use_display=False):
    for stat in entry.get("stats", []):
        if stat.get("name") in stat_names or stat.get("shortDisplayName") in stat_names:
            return stat.get("displayValue") if use_display else stat.get("value")
    return None


def get_flat_standings_entries(sport, league):
    """Fetch standings and flatten every team entry into one list,
    regardless of how ESPN nests/groups them (league-only, division, etc)."""
    try:
        resp = requests.get(standings_url(sport, league), timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return None, f"Couldn't fetch standings right now ({e})"

    def walk(node, out):
        children = node.get("children", [])
        for child in children:
            walk(child, out)
        standings = node.get("standings")
        if standings and standings.get("entries"):
            out.extend(standings["entries"])

    entries = []
    for child in data.get("children", []):
        walk(child, entries)

    # de-dupe by team abbreviation, in case a team appears in both a
    # league-level group and a division-level group
    seen = {}
    for entry in entries:
        abbr = entry.get("team", {}).get("abbreviation")
        if abbr and abbr not in seen:
            seen[abbr] = entry
    return list(seen.values()), None


def build_division_block(div_name, division_abbrs, all_entries):
    by_abbr = {e.get("team", {}).get("abbreviation"): e for e in all_entries}
    rows = []
    for abbr in division_abbrs:
        entry = by_abbr.get(abbr)
        if not entry:
            continue
        wins = _stat_value(entry, {"wins"})
        losses = _stat_value(entry, {"losses"})
        gb = _stat_value(entry, {"gamesBehind"}, use_display=True) or "-"
        wins = int(wins) if wins is not None else 0
        losses = int(losses) if losses is not None else 0
        rows.append((wins, losses, gb, abbr))
    rows.sort(key=lambda r: r[0] / max(r[0] + r[1], 1), reverse=True)
    lines = [f"{abbr} {w}-{l} (GB {gb})" for w, l, gb, abbr in rows]
    return f"{div_name}:\n" + "\n".join(lines)


def get_standings(sport, league):
    all_entries, err = get_flat_standings_entries(sport, league)
    if err:
        return err
    if not all_entries:
        return "Couldn't find standings right now."

    sections = [
        build_division_block(div_name, abbrs, all_entries)
        for div_name, abbrs in get_division_map(league).items()
    ]
    return "\n\n".join(sections)


def get_mlb_standings():
    return get_standings("baseball", "mlb")


def get_nfl_standings():
    return get_standings("football", "nfl")


def get_ai_reply(user_text, system_prompt=None, model="meta-llama/llama-3.3-70b-instruct:free"):
    if not OPENROUTER_API_KEY:
        return None  # AI replies disabled if no key set
    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 400,
                "messages": [
                    {
                        "role": "system",
                        "content": system_prompt or (
                            "You are a friendly, concise group chat bot in GroupMe. "
                            "Keep replies short (a few sentences max) unless asked for more."
                        ),
                    },
                    {"role": "user", "content": user_text},
                ],
            },
            timeout=25,
        )
        if resp.status_code != 200:
            return f"(AI error {resp.status_code}: {resp.text[:300]})"
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip() or None
    except Exception as e:
        return f"(AI error: {e})"


def ai_search(query):
    reply = get_ai_reply(
        query,
        system_prompt=(
            "You are a precise search assistant with real-time web access. "
            "Give direct, accurate, up-to-date answers. Keep it short - a sentence or two "
            "for simple factual questions, more only if the question genuinely needs it. "
            "No filler, no unnecessary preamble."
        ),
        model="perplexity/sonar",
    )
    return reply or "Search needs OPENROUTER_API_KEY set up first."


def get_playoff_odds(sport, league, abbr):
    metric_name = "fpi" if league == "nfl" else "bpi"
    url = f"https://site.api.espn.com/apis/fitt/v3/sports/{sport}/{league}/{metric_name}"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return f"Couldn't fetch playoff odds right now ({e})"

    teams = data.get("teams", []) or data.get("fpi", {}).get("teams", [])
    entry = None
    for t in teams:
        team_info = t.get("team", t)
        if team_info.get("abbreviation") == abbr:
            entry = t
            break

    if not entry:
        return f"Couldn't find playoff odds data for {abbr} right now."

    stats = entry.get("stats", entry)

    def find_pct(*keys):
        for k in keys:
            val = stats.get(k)
            if val is not None:
                return val
        return None

    division_pct = find_pct("winDivisionPct", "divisionWinPercent", "winDivision")
    wildcard_pct = find_pct("makePlayoffsPct", "wildCardPct", "playoffPct")
    title_pct = find_pct(
        "winSuperBowlPct", "winWorldSeriesPct", "winChampionshipPct", "winLeaguePct"
    )

    if division_pct is None and wildcard_pct is None and title_pct is None:
        return f"Playoff odds data isn't available for {abbr} right now (may be off-season or ESPN hasn't published projections yet)."

    lines = [f"{abbr} playoff odds:"]
    if division_pct is not None:
        lines.append(f"Win division: {division_pct}%")
    if wildcard_pct is not None:
        lines.append(f"Make playoffs: {wildcard_pct}%")
    if title_pct is not None:
        title_label = "Win World Series" if league == "mlb" else "Win Super Bowl"
        lines.append(f"{title_label}: {title_pct}%")
    return "\n".join(lines)


def get_team_division_standings(sport, league, abbr):
    div_name = find_division_name(league, abbr)
    if not div_name:
        return f"Don't have division info for {abbr}."

    all_entries, err = get_flat_standings_entries(sport, league)
    if err:
        return err

    division_abbrs = get_division_map(league)[div_name]
    return build_division_block(div_name, division_abbrs, all_entries)


def debug_standings_groups(sport, league):
    all_entries, err = get_flat_standings_entries(sport, league)
    if err:
        return err
    abbrs = sorted(e.get("team", {}).get("abbreviation", "?") for e in all_entries)
    return f"Flat entries found ({len(abbrs)} teams): {', '.join(abbrs)}"


def summary_url(sport, league, event_id):
    return f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/summary?event={event_id}"


def get_league_odds(sport, league):
    try:
        resp = requests.get(scoreboard_url(sport, league), timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return f"Couldn't fetch odds right now ({e})"

    lines = []
    for event in data.get("events", []):
        competitions = event.get("competitions", [{}])[0]
        competitors = competitions.get("competitors", [])
        home = next((c for c in competitors if c.get("homeAway") == "home"), None)
        away = next((c for c in competitors if c.get("homeAway") == "away"), None)
        if not home or not away:
            continue
        home_abbr = home.get("team", {}).get("abbreviation", "HOME")
        away_abbr = away.get("team", {}).get("abbreviation", "AWAY")

        odds_list = competitions.get("odds", [])
        if not odds_list:
            continue
        odds = odds_list[0]
        spread = odds.get("details", "even")
        over_under = odds.get("overUnder")
        ou_text = f", O/U {over_under}" if over_under else ""
        lines.append(f"{away_abbr} @ {home_abbr}: {spread}{ou_text}")

    return "\n".join(lines) if lines else "No odds available right now (games may not be posted yet)."


def get_team_prediction(sport, league, abbr):
    try:
        resp = requests.get(scoreboard_url(sport, league), timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return f"Couldn't fetch predictions right now ({e})"

    event_id = None
    matchup = None
    for event in data.get("events", []):
        result = format_game(event)
        if not result:
            continue
        _, abbrs = result
        if abbr in abbrs:
            event_id = event.get("id")
            matchup = result[0]
            break

    if not event_id:
        return "That team isn't playing right now, so no prediction available."

    try:
        resp = requests.get(summary_url(sport, league, event_id), timeout=10)
        resp.raise_for_status()
        summary = resp.json()
    except Exception as e:
        return f"Couldn't fetch prediction right now ({e})"

    predictor = summary.get("predictor", {})
    home_pct = predictor.get("homeTeam", {}).get("gameProjection")
    away_pct = predictor.get("awayTeam", {}).get("gameProjection")
    home_abbr = predictor.get("homeTeam", {}).get("team", {}).get("abbreviation", "HOME")
    away_abbr = predictor.get("awayTeam", {}).get("team", {}).get("abbreviation", "AWAY")

    if home_pct is None or away_pct is None:
        return f"{matchup}\nNo win-probability prediction available for this game yet."

    return f"{matchup}\nWin probability: {away_abbr} {away_pct}% - {home_abbr} {home_pct}%"


def estimate_win_probability(my_score, opp_score):
    """Rough estimate based on score gap, not an official ESPN win probability."""
    diff = my_score - opp_score
    prob = 1 / (1 + 10 ** (-diff / 20))
    return round(prob * 100)


def verify_espn_team(league_id, team_id, label):
    """Lightweight check that works pre-season, before scoring data exists."""
    url = (
        f"https://fantasy.espn.com/apis/v3/games/ffl/seasons/{ESPN_SEASON}"
        f"/segments/0/leagues/{league_id}?view=mTeam&view=mSettings"
    )
    cookies = {}
    if ESPN_S2 and ESPN_SWID:
        cookies = {"espn_s2": ESPN_S2, "SWID": ESPN_SWID}
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    }

    try:
        resp = requests.get(url, cookies=cookies, headers=headers, timeout=10)
        if resp.status_code == 202:
            time.sleep(1.5)
            resp = requests.get(url, cookies=cookies, headers=headers, timeout=10)
    except Exception as e:
        return f"{label}: couldn't reach ESPN ({e})"

    if resp.status_code != 200:
        return f"{label}: ESPN returned status {resp.status_code} - league may be private or not accessible."

    try:
        data = resp.json()
    except Exception:
        return f"{label}: ESPN didn't return usable data yet (probably too early in the season)."

    league_name = data.get("settings", {}).get("name", "Unknown league")
    team_id_int = int(team_id)
    team = next((t for t in data.get("teams", []) if t.get("id") == team_id_int), None)

    if not team:
        return f"{label}: found league \"{league_name}\", but no team with ID {team_id} in it. Double check your Team ID."

    team_name = f"{team.get('location', '')} {team.get('nickname', '')}".strip() or f"Team {team_id}"
    return f"{label}: connected OK - league \"{league_name}\", your team is \"{team_name}\". Scores aren't live yet."


def get_one_fantasy_matchup(league_id, team_id, label):
    url = (
        f"https://fantasy.espn.com/apis/v3/games/ffl/seasons/{ESPN_SEASON}"
        f"/segments/0/leagues/{league_id}?view=mScoreboard&view=mTeam"
    )
    cookies = {}
    if ESPN_S2 and ESPN_SWID:
        cookies = {"espn_s2": ESPN_S2, "SWID": ESPN_SWID}
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    }

    try:
        resp = requests.get(url, cookies=cookies, headers=headers, timeout=10)
        if resp.status_code == 202:
            # ESPN sometimes returns 202 (no body) on first hit - retry once
            time.sleep(1.5)
            resp = requests.get(url, cookies=cookies, headers=headers, timeout=10)
    except Exception as e:
        return f"{label}: couldn't fetch matchup ({e})"

    if resp.status_code != 200:
        return f"{label}: couldn't fetch matchup (status {resp.status_code}: {resp.text[:200]})"

    try:
        data = resp.json()
    except Exception:
        return f"{label}: couldn't fetch matchup (unexpected response: {resp.text[:200]})"

    teams = {t["id"]: t for t in data.get("teams", [])}
    current_period = data.get("status", {}).get("currentMatchupPeriod")
    team_id_int = int(team_id)

    for matchup in data.get("schedule", []):
        if matchup.get("matchupPeriodId") != current_period:
            continue
        home = matchup.get("home", {})
        away = matchup.get("away", {})
        if home.get("teamId") == team_id_int:
            my_side, opp_side = home, away
        elif away.get("teamId") == team_id_int:
            my_side, opp_side = away, home
        else:
            continue

        my_team = teams.get(my_side.get("teamId"), {})
        opp_team = teams.get(opp_side.get("teamId"), {})
        my_name = f"{my_team.get('location', '')} {my_team.get('nickname', '')}".strip() or "Your Team"
        opp_name = f"{opp_team.get('location', '')} {opp_team.get('nickname', '')}".strip() or "Opponent"
        my_score = my_side.get("totalPoints", 0)
        opp_score = opp_side.get("totalPoints", 0)
        win_pct = estimate_win_probability(my_score, opp_score)
        return f"[{label}] {my_name} {my_score} - {opp_name} {opp_score} (~{win_pct}% win chance, estimate)"

    return f"{label}: couldn't find current matchup (season may not have started yet)"


def get_fantasy_matchup(filter_label=None):
    teams = parse_espn_teams()
    if not teams:
        return "Fantasy team isn't set up yet."

    if filter_label:
        teams = [t for t in teams if filter_label in t[2].lower()] or teams

    results = [get_one_fantasy_matchup(lid, tid, label) for lid, tid, label in teams]
    return "\n\n".join(results)


# ESPN's internal NFL team ID -> abbreviation
ESPN_PRO_TEAM_ABBR = {
    1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL", 7: "DEN", 8: "DET",
    9: "GB", 10: "TEN", 11: "IND", 12: "KC", 13: "LV", 14: "LAR", 15: "MIA", 16: "MIN",
    17: "NE", 18: "NO", 19: "NYG", 20: "NYJ", 21: "PHI", 22: "ARI", 23: "PIT", 24: "LAC",
    25: "SF", 26: "SEA", 27: "TB", 28: "WSH", 29: "CAR", 30: "JAX", 33: "BAL", 34: "HOU",
}

# ESPN raw stat category IDs used to hand-calculate standard PPR points
PPR_STAT_IDS = {
    "pass_yds": "3", "pass_td": "4", "pass_int": "20",
    "rush_yds": "25", "rush_td": "26",
    "rec": "53", "rec_yds": "42", "rec_td": "43",
    "fumbles_lost": "72",
}


def calc_ppr_points(raw_stats):
    """Standard ESPN PPR scoring, computed from raw stat categories."""
    if not raw_stats:
        return None
    g = lambda key: raw_stats.get(PPR_STAT_IDS[key], 0)
    points = 0.0
    points += g("pass_yds") * 0.04       # 1 pt / 25 yds
    points += g("pass_td") * 4
    points += g("pass_int") * -2
    points += g("rush_yds") * 0.1        # 1 pt / 10 yds
    points += g("rush_td") * 6
    points += g("rec") * 1               # PPR: 1 pt / reception
    points += g("rec_yds") * 0.1         # 1 pt / 10 yds
    points += g("rec_td") * 6
    points += g("fumbles_lost") * -2
    return round(points, 1)


def get_fantasy_player_info(last_name):
    season = ESPN_SEASON
    url = f"https://fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/players?view=kona_player_info"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "x-fantasy-filter": (
            '{"players":{"filterActive":{"value":true},'
            '"filterStatsForTopScoringPeriodIds":{"value":2,'
            f'"additionalValue":["00{season}","10{season}"]}},'
            '"sortPercOwned":{"sortPriority":1,"sortAsc":false},'
            '"limit":600}}'
        ),
    }

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        players = resp.json()
    except Exception as e:
        return f"Couldn't fetch player data right now ({e})"

    found_player = None
    for player in players:
        full_name = player.get("fullName", "")
        if last_name.lower() in full_name.lower():
            found_player = player
            break

    if not found_player:
        return f"Couldn't find a player matching \"{last_name}\" (may be too obscure, or check the spelling)."

    full_name = found_player.get("fullName", last_name)

    current_week_stats = None
    for stat_entry in found_player.get("stats", []):
        if stat_entry.get("statSourceId") == 0:  # 0 = actual (not projected)
            current_week_stats = stat_entry.get("stats")
            break
    if current_week_stats is None:
        for stat_entry in found_player.get("stats", []):
            if stat_entry.get("statSourceId") == 1:  # fall back to projected
                current_week_stats = stat_entry.get("stats")
                break

    points = calc_ppr_points(current_week_stats)
    points_text = f"{points} PPR pts this week" if points is not None else "no stats logged yet this week"

    pro_team_id = found_player.get("proTeamId")
    abbr = ESPN_PRO_TEAM_ABBR.get(pro_team_id)
    schedule_text = ""
    if abbr:
        event_id = find_team_event_id("football", "nfl", abbr)
        schedule_text = "Playing now." if event_id else get_next_game("football", "nfl", abbr)

    return f"{full_name}: {points_text}\n{schedule_text}".strip()


def track_package(tracking_number):
    if not TRACK17_API_KEY:
        return "Package tracking isn't set up yet."

    tracking_number = tracking_number.strip()
    headers = {"17token": TRACK17_API_KEY, "Content-Type": "application/json"}

    try:
        # Register the number (safe to call even if already registered)
        requests.post(
            "https://api.17track.net/track/v2.2/register",
            headers=headers,
            json=[{"number": tracking_number}],
            timeout=10,
        )
        resp = requests.post(
            "https://api.17track.net/track/v2.2/gettrackinfo",
            headers=headers,
            json=[{"number": tracking_number}],
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return f"Couldn't fetch tracking info right now ({e})"

    accepted = data.get("data", {}).get("accepted", [])
    if not accepted:
        return f"No tracking info found for {tracking_number} yet (may take a bit after a new package is registered)."

    track_info = accepted[0].get("track", {})
    events = track_info.get("z1", []) or track_info.get("z0", [])
    status_text = track_info.get("e", "")  # status code summary if present

    if not events:
        return f"{tracking_number}: no scan events yet. Status: {status_text or 'pending'}"

    latest = events[0]
    desc = latest.get("z", "") or latest.get("a", "")
    time_text = latest.get("t", "") or latest.get("z_time", "")
    return f"{tracking_number}:\n{desc}\n{time_text}".strip()


# ---- Reminders ----
pending_reminders = []  # list of {"due": datetime, "message": str, "bot_id": str}
reminders_lock = threading.Lock()


def parse_duration(token):
    match = re.match(r"^(\d+)(m|min|mins|h|hr|hrs|d|day|days)$", token.lower())
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2)
    if unit.startswith("m"):
        return timedelta(minutes=amount)
    if unit.startswith("h"):
        return timedelta(hours=amount)
    return timedelta(days=amount)


def add_reminder(duration_token, message, bot_id):
    delta = parse_duration(duration_token)
    if not delta:
        return None
    due = datetime.utcnow() + delta
    with reminders_lock:
        pending_reminders.append({"due": due, "message": message, "bot_id": bot_id})
    return due


def poll_reminders():
    while True:
        time.sleep(30)
        now = datetime.utcnow()
        with reminders_lock:
            due_now = [r for r in pending_reminders if r["due"] <= now]
            for r in due_now:
                pending_reminders.remove(r)
        for r in due_now:
            _post_to_groupme(f"Reminder: {r['message']}", bot_id=r["bot_id"])


_reminders_thread = threading.Thread(target=poll_reminders, daemon=True)
_reminders_thread.start()


# ---- Flight status ----
def get_flight_status(flight_number):
    if not AVIATIONSTACK_API_KEY:
        return "Flight tracking isn't set up yet."
    url = f"http://api.aviationstack.com/v1/flights?access_key={AVIATIONSTACK_API_KEY}&flight_iata={flight_number}"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return f"Couldn't fetch flight info right now ({e})"

    results = data.get("data", [])
    if not results:
        return f"Couldn't find flight {flight_number}."

    flight = results[0]
    status = flight.get("flight_status", "unknown")
    dep = flight.get("departure", {})
    arr = flight.get("arrival", {})
    dep_airport = dep.get("airport", "?")
    arr_airport = arr.get("airport", "?")
    dep_time = dep.get("scheduled", "?")
    delay = dep.get("delay")
    delay_text = f" (delayed {delay} min)" if delay else ""

    return f"{flight_number}: {status}{delay_text}\n{dep_airport} -> {arr_airport}\nDeparts: {dep_time}"


# ---- Translate ----
def translate_text(text_to_translate, target_lang="en"):
    url = "https://translate.googleapis.com/translate_a/single"
    params = {"client": "gtx", "sl": "auto", "tl": target_lang, "dt": "t", "q": text_to_translate}
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        translated = "".join(chunk[0] for chunk in data[0])
        return translated
    except Exception as e:
        return f"Couldn't translate right now ({e})"


LANGUAGE_CODES = {
    "spanish": "es", "french": "fr", "german": "de", "italian": "it",
    "portuguese": "pt", "chinese": "zh-CN", "japanese": "ja", "korean": "ko",
    "russian": "ru", "arabic": "ar", "hindi": "hi", "english": "en",
}


# Built-in Hebrew phrases, no AI needed - (English, Hebrew script, phonetic pronunciation)
HEBREW_PHRASES = [
    ("hello", "שלום", "shalom"),
    ("thank you", "תודה", "todah"),
    ("please / you're welcome", "בבקשה", "bevakasha"),
    ("yes", "כן", "ken"),
    ("no", "לא", "lo"),
    ("good morning", "בוקר טוב", "boker tov"),
    ("good night", "לילה טוב", "layla tov"),
    ("how are you?", "מה שלומך?", "mah shlomcha?"),
    ("I love you", "אני אוהב אותך", "ani ohev otach"),
    ("goodbye", "להתראות", "lehitraot"),
    ("water", "מים", "mayim"),
    ("food", "אוכל", "ochel"),
    ("friend", "חבר", "chaver"),
    ("family", "משפחה", "mishpacha"),
    ("congratulations", "מזל טוב", "mazel tov"),
]


def get_hebrew_lesson(word=None):
    if word:
        translated = translate_text(word, "he")
        return f'"{word}" in Hebrew: {translated}\n(Pronunciation not available for custom words - try a common word/phrase for a full lesson.)'

    english, hebrew, pronunciation = random.choice(HEBREW_PHRASES)
    return f'{english.capitalize()}: {hebrew}\nPronounced: "{pronunciation}"'


# ---- Injury reports ----
def get_team_injuries(team_abbr):
    url = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return f"Couldn't fetch injury info right now ({e})"

    for team_block in data.get("injuries", []):
        team = team_block.get("team", {})
        if team.get("abbreviation") == team_abbr:
            entries = team_block.get("injuries", [])
            if not entries:
                return f"{team_abbr}: no injuries reported."
            lines = []
            for inj in entries[:8]:
                athlete = inj.get("athlete", {}).get("displayName", "Unknown")
                status = inj.get("status", "")
                lines.append(f"{athlete} - {status}")
            return f"{team_abbr} injuries:\n" + "\n".join(lines)

    return f"No injury data found for {team_abbr}."


# ---- Game day countdown ----
def get_game_countdown(sport, league, abbr):
    event_id = find_team_event_id(sport, league, abbr)
    if event_id:
        return f"{abbr} is playing right now!"

    next_game_text = get_next_game(sport, league, abbr)
    try:
        resp = requests.get(schedule_url(sport, league, abbr), timeout=10)
        resp.raise_for_status()
        events = resp.json().get("events", [])
        now = datetime.utcnow()
        upcoming = []
        for event in events:
            try:
                game_date = datetime.strptime(event.get("date", ""), "%Y-%m-%dT%H:%MZ")
            except Exception:
                continue
            if game_date >= now:
                upcoming.append(game_date)
        if upcoming:
            upcoming.sort()
            delta = upcoming[0] - now
            days = delta.days
            hours = delta.seconds // 3600
            return f"{abbr}'s next game is in {days}d {hours}h."
    except Exception:
        pass

    return next_game_text


# ---- Joke ----
RIDDLES = [
    ("What has to be broken before you can use it?", "An egg."),
    ("I speak without a mouth and hear without ears. What am I?", "An echo."),
    ("The more you take, the more you leave behind. What am I?", "Footsteps."),
    ("What has keys but no locks, space but no room, and you can enter but not go in?", "A keyboard."),
    ("What gets wetter the more it dries?", "A towel."),
    ("What has a face and two hands but no arms or legs?", "A clock."),
    ("What comes once in a minute, twice in a moment, but never in a thousand years?", "The letter M."),
    ("What can travel around the world while staying in a corner?", "A stamp."),
    ("What has many teeth but cannot bite?", "A comb."),
    ("I'm tall when I'm young and short when I'm old. What am I?", "A candle."),
]

# per-group state so the answer reveal matches the right riddle
current_riddles = {}  # bot_id -> answer
riddles_lock = threading.Lock()


WOULD_YOU_RATHER = [
    "Would you rather have unlimited pizza for life or unlimited tacos for life?",
    "Would you rather be able to fly or be invisible?",
    "Would you rather always be 10 minutes late or 20 minutes early?",
    "Would you rather give up your phone for a month or give up TV for a year?",
    "Would you rather fight one horse-sized duck or 100 duck-sized horses?",
    "Would you rather have super strength or super speed?",
    "Would you rather always have to say everything on your mind or never speak again?",
    "Would you rather live without music or without TV/movies?",
    "Would you rather be the funniest person in the room or the smartest?",
    "Would you rather explore space or the ocean?",
]

TRIVIA_QUESTIONS = [
    ("What is the largest planet in our solar system?", "Jupiter"),
    ("What is the capital of Australia?", "Canberra"),
    ("How many bones are in the adult human body?", "206"),
    ("What year did the Titanic sink?", "1912"),
    ("What's the smallest country in the world?", "Vatican City"),
    ("What element does the symbol 'O' represent?", "Oxygen"),
    ("Who painted the Mona Lisa?", "Leonardo da Vinci"),
    ("What's the longest river in the world?", "The Nile"),
    ("How many continents are there?", "7"),
    ("What is the hardest natural substance on Earth?", "Diamond"),
]

EIGHT_BALL_ANSWERS = [
    "It is certain.", "Without a doubt.", "Yes, definitely.", "You may rely on it.",
    "As I see it, yes.", "Most likely.", "Outlook good.", "Signs point to yes.",
    "Reply hazy, try again.", "Ask again later.", "Better not tell you now.",
    "Cannot predict now.", "Concentrate and ask again.", "Don't count on it.",
    "My reply is no.", "My sources say no.", "Outlook not so good.", "Very doubtful.",
]

current_trivia = {}  # bot_id -> answer
trivia_lock = threading.Lock()


def get_would_you_rather():
    return random.choice(WOULD_YOU_RATHER)


def get_trivia(bot_id):
    question, answer = random.choice(TRIVIA_QUESTIONS)
    with trivia_lock:
        current_trivia[bot_id] = answer
    return f"{question}\n(text \"trivia answer\" to reveal)"


def reveal_trivia_answer(bot_id):
    with trivia_lock:
        answer = current_trivia.pop(bot_id, None)
    return answer or "No trivia pending - text \"trivia\" to get a question."


def get_8ball():
    return random.choice(EIGHT_BALL_ANSWERS)


def roll_dice():
    return f"You rolled a {random.randint(1, 6)}."


def flip_coin():
    return random.choice(["Heads.", "Tails."])


def get_roast(target=None):
    who = target.strip() if target else "me"
    prompt = f"Write a short, playful, PG-13 roast of {who} for a friendly group chat. Keep it under 3 sentences, funny not mean-spirited."
    reply = get_ai_reply(prompt, system_prompt="You write short, witty, good-natured roasts for a group chat. Never actually cruel, just funny.")
    return reply or "Roasting needs the AI key set up first - ask whoever runs the bot to add OPENROUTER_API_KEY."


SCRAMBLE_WORDS = [
    "basketball", "elephant", "mountain", "birthday", "chocolate", "umbrella",
    "dinosaur", "adventure", "keyboard", "sandwich", "backpack", "telescope",
]

EMOJI_QUIZZES = [
    ("🦁👑", "The Lion King"),
    ("🕷️🧑", "Spider-Man"),
    ("❄️👸", "Frozen"),
    ("🏠😱", "Home Alone"),
    ("🦈", "Jaws"),
    ("👻🚫", "Ghostbusters"),
    ("🐠🔍", "Finding Nemo"),
    ("🌪️🏠👧", "The Wizard of Oz"),
    ("🚢💔🧊", "Titanic"),
    ("🍫🏭", "Charlie and the Chocolate Factory"),
]

DRINKING_PROMPTS = [
    "Everyone drinks.",
    "Last person to text 'here' drinks.",
    "Whoever drank most recently, drink again.",
    "Make a rule - next person to break it drinks.",
    "Everyone who's ever been late to something drinks.",
    "Person to your right (or last texter besides you) picks who drinks.",
    "Skip a round - nobody drinks this time.",
    "Whoever has the most unread texts drinks.",
    "Group vote: most likely to fall asleep first drinks.",
    "Take two sips if you've ever texted an ex by accident.",
]

current_scrambles = {}  # bot_id -> answer
current_number_games = {}  # bot_id -> target number
game_lock = threading.Lock()


def get_scramble(bot_id):
    word = random.choice(SCRAMBLE_WORDS)
    letters = list(word)
    random.shuffle(letters)
    scrambled = "".join(letters)
    with game_lock:
        current_scrambles[bot_id] = word
    return f"Unscramble this: {scrambled}\n(text \"scramble answer\" to reveal)"


def reveal_scramble_answer(bot_id):
    with game_lock:
        answer = current_scrambles.pop(bot_id, None)
    return answer or "No scramble pending - text \"scramble\" to get one."


def get_emoji_quiz():
    emojis, answer = random.choice(EMOJI_QUIZZES)
    return f"{emojis}\nGuess the movie! (Answer: {answer})"


def start_number_game(bot_id):
    target = random.randint(1, 100)
    with game_lock:
        current_number_games[bot_id] = target
    return "I'm thinking of a number between 1 and 100. Text \"guess [number]\" to try."


def guess_number(bot_id, guess):
    with game_lock:
        target = current_number_games.get(bot_id)
    if target is None:
        return "No game running - text \"guess the number\" to start one."
    if guess == target:
        with game_lock:
            current_number_games.pop(bot_id, None)
        return f"Correct! It was {target}."
    elif guess < target:
        return "Higher."
    else:
        return "Lower."


def get_drinking_prompt():
    return random.choice(DRINKING_PROMPTS) + "\n(Drink responsibly - know your limits.)"


def get_riddle(bot_id):
    question, answer = random.choice(RIDDLES)
    with riddles_lock:
        current_riddles[bot_id] = answer
    return f"{question}\n(text \"answer\" to reveal)"


def reveal_riddle_answer(bot_id):
    with riddles_lock:
        answer = current_riddles.pop(bot_id, None)
    return answer or "No riddle pending - text \"riddle\" to get one."


def get_joke():
    url = "https://v2.jokeapi.dev/joke/Any?safe-mode&type=single"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("joke"):
            return data["joke"]
        setup = data.get("setup", "")
        delivery = data.get("delivery", "")
        return f"{setup}\n{delivery}"
    except Exception as e:
        return f"Couldn't fetch a joke right now ({e})"


# ---- Stock price ----
def get_stock_price(symbol):
    symbol = symbol.upper()
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return f"Couldn't fetch stock data right now ({e})"

    result = data.get("chart", {}).get("result")
    if not result:
        return f"Couldn't find a stock for \"{symbol}\"."

    meta = result[0].get("meta", {})
    price = meta.get("regularMarketPrice")
    prev_close = meta.get("previousClose") or meta.get("chartPreviousClose")
    currency = meta.get("currency", "")

    if price is None:
        return f"Couldn't find current price for {symbol}."

    line = f"{symbol}: {price} {currency}"
    if prev_close:
        change = price - prev_close
        pct = (change / prev_close) * 100 if prev_close else 0
        sign = "+" if change >= 0 else ""
        line += f" ({sign}{round(change, 2)}, {sign}{round(pct, 2)}%)"
    return line


# ---- Status / heartbeat ----
BOT_START_TIME = datetime.utcnow()


COMMAND_LIST_TEXT = (
    "SCORES\n"
    "[team] - score or next game\n"
    "score - all live games\n"
    "[team] update - live tracking\n"
    "turn on / stop - track everything\n\n"
    "STATS\n"
    "[team] standings - division\n"
    "[team] playoffs - playoff odds\n"
    "predict [team] - win probability\n"
    "odds - spread & O/U\n"
    "[team] injuries\n"
    "[team] countdown\n\n"
    "FANTASY\n"
    "fantasy - your matchup\n"
    "[name] points - fantasy pts\n\n"
    "UTILITIES\n"
    "weather / email / news [topic]\n"
    "remind 30m [msg]\n"
    "track [number] - package\n"
    "flight [number] / translate [text]\n"
    "stock [ticker] / status\n\n"
    "FUN\n"
    "joke / riddle / trivia\n"
    "would you rather / roast me\n"
    "8ball / roll dice / flip coin\n"
    "scramble / emoji quiz\n"
    "guess the number / drink\n\n"
    "AI\n"
    "hey bot [anything]"
)


def get_summary():
    parts = []

    weather = get_weather()
    parts.append(f"Weather: {weather}")

    if GMAIL_ADDRESS and GMAIL_APP_PASSWORD:
        try:
            mail = imaplib.IMAP4_SSL("imap.gmail.com")
            mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            mail.select("inbox")
            status, data = mail.search(None, "UNSEEN")
            mail.logout()
            count = len(data[0].split()) if status == "OK" else 0
            parts.append(f"Unread email: {count}")
        except Exception:
            parts.append("Unread email: couldn't check")

    with reminders_lock:
        pending_count = len(pending_reminders)
    if pending_count:
        parts.append(f"Pending reminders: {pending_count}")

    headlines = get_news(None).split("\n")
    if headlines:
        top = headlines[0].replace("- ", "")
        parts.append(f"Top news: {top}")

    return "Daily summary:\n" + "\n".join(parts)


def get_status():
    uptime = datetime.utcnow() - BOT_START_TIME
    hours = int(uptime.total_seconds() // 3600)
    minutes = int((uptime.total_seconds() % 3600) // 60)
    return f"I'm alive and running. Up for {hours}h {minutes}m."


def send_daily_heartbeat():
    while True:
        time.sleep(3600)  # check every hour for accuracy
        now_ct = datetime.now(ZoneInfo("America/Chicago"))
        if now_ct.hour == 8:  # 8am Central
            with watch_lock:
                bot_ids = set(GROUPME_BOTS_MAP.values())
                if GROUPME_BOT_ID:
                    bot_ids.add(GROUPME_BOT_ID)
            for bid in bot_ids:
                _post_to_groupme(get_summary(), bot_id=bid)
            time.sleep(3600)  # avoid firing twice within the same hour


_heartbeat_thread = threading.Thread(target=send_daily_heartbeat, daemon=True)
_heartbeat_thread.start()


def get_email_digest(limit=5):
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        return "Gmail isn't set up yet."

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        mail.select("inbox")
        status, data = mail.search(None, "UNSEEN")
        if status != "OK":
            return "Couldn't check your inbox right now."

        ids = data[0].split()
        if not ids:
            mail.logout()
            return "No new unread email."

        ids = ids[-limit:]  # most recent N
        lines = []
        for eid in reversed(ids):
            status, msg_data = mail.fetch(eid, "(RFC822)")
            if status != "OK":
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            subject, encoding = decode_header(msg.get("Subject", ""))[0]
            if isinstance(subject, bytes):
                subject = subject.decode(encoding or "utf-8", errors="ignore")
            sender = msg.get("From", "Unknown")
            if "<" in sender:
                sender = sender.split("<")[0].strip().strip('"')
            lines.append(f"{sender}: {subject}")

        mail.logout()
        total_unread = len(data[0].split())
        header = f"{total_unread} unread email" + ("s" if total_unread != 1 else "") + ":\n"
        return header + "\n".join(f"- {l}" for l in lines)
    except Exception as e:
        return f"Couldn't check email right now ({e})"


def get_weather(location=None):
    loc = location or os.environ.get("WEATHER_LOCATION", "")
    query = quote(loc) if loc else ""
    url = f"https://wttr.in/{query}?format=%l:+%C+%t+(feels+%f),+wind+%w"
    try:
        resp = requests.get(url, headers={"User-Agent": "curl"}, timeout=10)
        resp.raise_for_status()
        return resp.text.strip()
    except Exception as e:
        return f"Couldn't fetch weather right now ({e})"


def shorten_headline(title, max_words=8):
    if " - " in title:
        title = title.rsplit(" - ", 1)[0]  # drop trailing " - Source Name"
    words = title.split()
    if len(words) > max_words:
        title = " ".join(words[:max_words]) + "..."
    return title


def tavily_search(query):
    """Returns (result_text, quota_exhausted_bool)."""
    if not TAVILY_API_KEY:
        return None, False

    try:
        resp = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": TAVILY_API_KEY,
                "query": query,
                "search_depth": "basic",
                "include_answer": True,
                "max_results": 3,
            },
            timeout=15,
        )
        if resp.status_code in (401, 403, 429):
            # 401/403 can mean invalid key OR exhausted plan depending on Tavily's response;
            # 429 is a clear rate/quota limit - either way, fall back.
            return None, True
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None, True

    answer = data.get("answer")
    if answer:
        return answer.strip(), False

    results = data.get("results", [])
    if results:
        content = results[0].get("content", "").strip()
        if content:
            return content[:500] + ("..." if len(content) > 500 else ""), False

    return f"No results found for \"{query}\".", False


def serper_search(query):
    if not SERPER_API_KEY:
        return "Search isn't set up yet."

    try:
        resp = requests.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
            json={"q": query},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return f"Couldn't search right now ({e})"

    answer_box = data.get("answerBox")
    if answer_box:
        text = answer_box.get("answer") or answer_box.get("snippet")
        if text:
            return text.strip()

    organic = data.get("organic", [])
    if organic:
        snippet = organic[0].get("snippet", "").strip()
        if snippet:
            return snippet

    return f"No results found for \"{query}\"."


def do_search(query):
    result, exhausted = tavily_search(query)
    if result is not None:
        return result
    if exhausted and SERPER_API_KEY:
        return serper_search(query)
    if exhausted:
        return "Couldn't search right now (Tavily may be out of free credits this month, and no backup search is set up)."
    return "Search isn't set up yet."


def duckduckgo_search(query):
    """No signup, no API key needed at all - fully free and open."""
    url = "https://api.duckduckgo.com/"
    params = {"q": query, "format": "json", "no_html": 1, "skip_disambig": 1}
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return f"Couldn't search right now ({e})"

    answer = data.get("Answer") or data.get("AbstractText")
    if answer:
        return answer.strip()

    related = data.get("RelatedTopics", [])
    for item in related:
        text = item.get("Text")
        if text:
            return text.strip()

    return (
        f"No quick answer found for \"{query}\". "
        "This search works best for well-known facts and definitions, "
        "not obscure or very recent info."
    )


def brave_search(query):
    if not BRAVE_SEARCH_API_KEY:
        return "Search isn't set up yet."

    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {"Accept": "application/json", "X-Subscription-Token": BRAVE_SEARCH_API_KEY}
    params = {"q": query, "count": 3}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return f"Couldn't search right now ({e})"

    results = data.get("web", {}).get("results", [])
    if not results:
        return f"No results found for \"{query}\"."

    MAX_CHARS = 600
    combined = ""
    for item in results[:2]:
        desc = item.get("description", "")
        desc = re.sub(r"<[^>]+>", "", desc).strip()  # strip Brave's <strong> highlight tags
        if not desc:
            continue
        candidate = (combined + " " + desc).strip() if combined else desc
        if len(candidate) > MAX_CHARS:
            if MAX_CHARS - len(combined) > 40:
                combined = candidate[:MAX_CHARS].rsplit(" ", 1)[0] + "..."
            break
        combined = candidate

    return combined or results[0].get("title", "No answer found.")


def google_search(query):
    if not GOOGLE_SEARCH_API_KEY or not GOOGLE_SEARCH_CX:
        return "Google search isn't set up yet."

    url = "https://www.googleapis.com/customsearch/v1"
    params = {"key": GOOGLE_SEARCH_API_KEY, "cx": GOOGLE_SEARCH_CX, "q": query, "num": 3}
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return f"Couldn't search right now ({e})"

    items = data.get("items", [])
    if not items:
        return f"No results found for \"{query}\"."

    # Combine snippets from the top couple results for more complete coverage,
    # but cap the total length so it can't balloon into multiple paragraphs.
    MAX_CHARS = 600
    combined = ""
    for item in items[:2]:
        snippet = item.get("snippet", "").replace("\n", " ").strip()
        if not snippet:
            continue
        candidate = (combined + " " + snippet).strip() if combined else snippet
        if len(candidate) > MAX_CHARS:
            remaining = MAX_CHARS - len(combined)
            if remaining > 40:  # only add a partial if there's meaningfully more room
                combined = (candidate[:MAX_CHARS]).rsplit(" ", 1)[0] + "..."
            break
        combined = candidate

    return combined or items[0].get("title", "No answer found.")


def get_news(topic=None):
    query = topic if topic else "top stories"
    url = f"https://news.google.com/rss/search?q={quote(query)}&hl=en-US&gl=US&ceid=US:en"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as e:
        return f"Couldn't fetch news right now ({e})"

    headlines = []
    for item in root.findall(".//item")[:3]:
        title = item.findtext("title", "")
        if title:
            headlines.append(shorten_headline(title))

    if not headlines:
        return f"No news found for \"{query}\"."

    return "\n".join(f"- {h}" for h in headlines)


def match_team(text):
    """Return (sport, league, abbr) if any alias appears in the text. Longest alias wins."""
    matches = [(alias, val) for alias, val in TEAM_ALIASES.items() if alias in text]
    if not matches:
        return None
    matches.sort(key=lambda m: len(m[0]), reverse=True)
    return matches[0][1]


# ---- Live score tracking ----
watched_teams = {}  # (bot_id, abbr) -> {"sport":.., "league":.., "last_line":.., "bot_id":..}
watch_lock = threading.Lock()


def start_watch(sport, league, abbr, bot_id):
    line = None
    try:
        resp = requests.get(scoreboard_url(sport, league), timeout=10)
        resp.raise_for_status()
        for event in resp.json().get("events", []):
            result = format_game(event)
            if result and abbr in result[1]:
                line = result[0]
                break
    except Exception:
        pass
    with watch_lock:
        watched_teams[(bot_id, abbr)] = {"sport": sport, "league": league, "last_line": line, "bot_id": bot_id}
    return line


def stop_watch(abbr, bot_id):
    with watch_lock:
        watched_teams.pop((bot_id, abbr), None)


def poll_watched_teams():
    while True:
        time.sleep(60)
        with watch_lock:
            items = list(watched_teams.items())
        print(f"[poller] checking {len(items)} watched team(s): {list(watched_teams.keys())}", flush=True)
        for key, info in items:
            bot_id, abbr = key
            try:
                resp = requests.get(scoreboard_url(info["sport"], info["league"]), timeout=10)
                resp.raise_for_status()
                found_line = None
                for event in resp.json().get("events", []):
                    result = format_game(event)
                    if result and abbr in result[1]:
                        found_line = result[0]
                        break
                print(f"[poller] {abbr}: found_line={found_line!r} last_line={info['last_line']!r}", flush=True)
                if found_line and found_line != info["last_line"]:
                    _post_to_groupme(f"UPDATE: {found_line}", bot_id=bot_id)
                    with watch_lock:
                        if key in watched_teams:
                            watched_teams[key]["last_line"] = found_line
            except Exception as e:
                print(f"[poller] ERROR checking {abbr}: {e}", flush=True)
                continue


_poller_thread = threading.Thread(target=poll_watched_teams, daemon=True)
_poller_thread.start()

# ---- Track ALL live games (NFL + MLB) mode, per group ----
all_games_watch = {}  # bot_id -> {"active": bool, "known": {}}


def toggle_all_games(on, bot_id):
    with watch_lock:
        state = all_games_watch.setdefault(bot_id, {"active": False, "known": {}})
        state["active"] = on
        if on:
            state["known"] = {}


def poll_all_games():
    while True:
        time.sleep(60)
        with watch_lock:
            active_bots = [bid for bid, s in all_games_watch.items() if s["active"]]
        if not active_bots:
            continue
        for sport, league in (("football", "nfl"), ("baseball", "mlb")):
            try:
                resp = requests.get(scoreboard_url(sport, league), timeout=10)
                resp.raise_for_status()
                events = resp.json().get("events", [])
            except Exception:
                continue
            for event in events:
                result = format_game(event)
                if not result:
                    continue
                line, _ = result
                event_id = event.get("id")
                for bot_id in active_bots:
                    with watch_lock:
                        state = all_games_watch.get(bot_id)
                        if not state:
                            continue
                        prev = state["known"].get(event_id)
                        if prev is not None and prev != line:
                            _post_to_groupme(f"UPDATE: {line}", bot_id=bot_id)
                        state["known"][event_id] = line


_all_games_thread = threading.Thread(target=poll_all_games, daemon=True)
_all_games_thread.start()


def _post_to_groupme(text, bot_id=None):
    target_bot_id = bot_id or GROUPME_BOT_ID
    if not target_bot_id:
        print("No bot_id available, cannot post")
        return
    # GroupMe caps messages around 1000 chars; split if needed
    for i in range(0, len(text), 950):
        chunk = text[i:i + 950]
        requests.post(
            "https://api.groupme.com/v3/bots/post",
            json={"bot_id": target_bot_id, "text": chunk},
            timeout=10,
        )


@app.route("/callback", methods=["POST"])
def callback():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip().lower()
    sender_type = data.get("sender_type", "")
    group_id = data.get("group_id", "")
    bot_id = GROUPME_BOTS_MAP.get(group_id, GROUPME_BOT_ID)

    def post_to_groupme(msg):
        _post_to_groupme(msg, bot_id=bot_id)

    if sender_type == "bot":
        return "ok", 200

    stripped = text.strip()
    if stripped == "turn on":
        toggle_all_games(True, bot_id)
        post_to_groupme("Live tracking is ON for all NFL and MLB games - I'll post here whenever any score changes. Text \"stop\" to turn it off.")
        return "ok", 200
    if stripped == "stop":
        toggle_all_games(False, bot_id)
        post_to_groupme("Live tracking is OFF.")
        return "ok", 200

    if stripped.startswith("remind "):
        parts = stripped.split(" ", 2)
        if len(parts) >= 3:
            due = add_reminder(parts[1], parts[2], bot_id)
            if due:
                post_to_groupme(f"Got it, I'll remind you: \"{parts[2]}\"")
            else:
                post_to_groupme("Use a format like \"remind 30m call mom\" or \"remind 2h check oven\".")
        else:
            post_to_groupme("Use a format like \"remind 30m call mom\".")
        return "ok", 200

    if stripped.startswith("flight "):
        flight_number = stripped[len("flight "):].strip().upper()
        post_to_groupme(get_flight_status(flight_number))
        return "ok", 200

    if stripped.startswith("hebrew"):
        word = stripped[len("hebrew"):].strip()
        post_to_groupme(get_hebrew_lesson(word if word else None))
        return "ok", 200

    if stripped.startswith("translate "):
        rest = stripped[len("translate "):].strip()
        target = "en"
        for lang_word, code in LANGUAGE_CODES.items():
            if rest.endswith(f" to {lang_word}"):
                target = code
                rest = rest[: -(len(f" to {lang_word}"))].strip()
                break
        post_to_groupme(translate_text(rest, target))
        return "ok", 200

    if "injur" in text:  # catches injury / injuries
        team = match_team(text)
        if team:
            _, league, abbr = team
            if league == "nfl":
                post_to_groupme(get_team_injuries(abbr))
            else:
                post_to_groupme("Injury reports only available for NFL right now.")
        else:
            post_to_groupme("Tell me which team, e.g. \"bears injuries\".")
        return "ok", 200

    if "countdown" in text or ("next" in text and "game" in text):
        team = match_team(text)
        if team:
            sport, league, abbr = team
            post_to_groupme(get_game_countdown(sport, league, abbr))
        else:
            post_to_groupme("Tell me which team, e.g. \"bears countdown\" or \"next bears game\".")
        return "ok", 200

    if stripped == "summary":
        post_to_groupme(get_summary())
        return "ok", 200

    if stripped in ("list", "commands", "help"):
        post_to_groupme(COMMAND_LIST_TEXT)
        return "ok", 200

    if stripped == "standings debug":
        post_to_groupme(debug_standings_groups("baseball", "mlb"))
        return "ok", 200

    if stripped == "version":
        post_to_groupme(f"Running version: {BOT_VERSION}")
        return "ok", 200

    if stripped == "status":
        post_to_groupme(get_status())
        return "ok", 200

    if stripped.startswith("stock "):
        symbol = stripped[len("stock "):].strip()
        post_to_groupme(get_stock_price(symbol))
        return "ok", 200

    if "would you rather" in text or stripped == "wyr":
        post_to_groupme(get_would_you_rather())
        return "ok", 200

    if stripped.startswith("roast"):
        target = stripped[len("roast"):].strip()
        post_to_groupme(get_roast(target if target else None))
        return "ok", 200

    if stripped == "trivia":
        post_to_groupme(get_trivia(bot_id))
        return "ok", 200

    if stripped == "trivia answer":
        post_to_groupme(reveal_trivia_answer(bot_id))
        return "ok", 200

    if stripped.startswith("8ball") or stripped.startswith("magic 8ball"):
        post_to_groupme(get_8ball())
        return "ok", 200

    if stripped in ("roll dice", "roll", "dice"):
        post_to_groupme(roll_dice())
        return "ok", 200

    if stripped in ("flip coin", "coin flip", "flip"):
        post_to_groupme(flip_coin())
        return "ok", 200

    if stripped == "scramble":
        post_to_groupme(get_scramble(bot_id))
        return "ok", 200

    if stripped == "scramble answer":
        post_to_groupme(reveal_scramble_answer(bot_id))
        return "ok", 200

    if stripped == "emoji quiz":
        post_to_groupme(get_emoji_quiz())
        return "ok", 200

    if stripped == "guess the number":
        post_to_groupme(start_number_game(bot_id))
        return "ok", 200

    if stripped.startswith("guess "):
        guess_text = stripped[len("guess "):].strip()
        if guess_text.isdigit():
            post_to_groupme(guess_number(bot_id, int(guess_text)))
        else:
            post_to_groupme("Text \"guess [a number]\".")
        return "ok", 200

    if stripped in ("drink", "drinking game", "drinking"):
        post_to_groupme(get_drinking_prompt())
        return "ok", 200

    if stripped == "riddle":
        post_to_groupme(get_riddle(bot_id))
        return "ok", 200

    if stripped == "answer":
        post_to_groupme(reveal_riddle_answer(bot_id))
        return "ok", 200

    if stripped == "joke":
        post_to_groupme(get_joke())
        return "ok", 200

    if "weather" in text:
        location = text.replace("weather", "").strip()
        post_to_groupme(get_weather(location if location else None))
        return "ok", 200

    if "email" in text or "mail check" in text:
        post_to_groupme(get_email_digest())
        return "ok", 200

    if stripped.startswith("track "):
        tracking_number = stripped[len("track "):].strip()
        if tracking_number:
            post_to_groupme(track_package(tracking_number))
        else:
            post_to_groupme("Text \"track [number]\" with your tracking number.")
        return "ok", 200

    if stripped.startswith("search ") or stripped.startswith("google "):
        query = stripped.split(" ", 1)[1].strip()
        post_to_groupme(do_search(query))
        return "ok", 200

    if "news" in text:
        after = text.split("news", 1)[1].strip()
        post_to_groupme(get_news(after if after else None))
        return "ok", 200

    matched_player_alias = next((a for a in PLAYER_ALIASES if a in stripped), None)
    if matched_player_alias and (stripped == matched_player_alias or "stats" in stripped):
        post_to_groupme(get_player_game_stats(matched_player_alias))
        return "ok", 200

    if "fantasy points" in text or "how many points" in text or ("points" in text and "fantasy" not in text and match_team(text) is None):
        name = text
        for word in ("fantasy", "points", "how many", "does", "have", "?"):
            name = name.replace(word, "")
        name = name.strip()
        if name:
            post_to_groupme(get_fantasy_player_info(name))
        else:
            post_to_groupme("Tell me the player's last name, e.g. \"Williams points\".")
        return "ok", 200

    if "fantasy check" in text or "check fantasy" in text:
        teams = parse_espn_teams()
        if not teams:
            post_to_groupme("Fantasy team isn't set up yet.")
        else:
            results = [verify_espn_team(lid, tid, label) for lid, tid, label in teams]
            post_to_groupme("\n\n".join(results))
        return "ok", 200

    if "fantasy" in text:
        # allow "fantasy work" to filter to a team labeled "Work League" etc.
        after = text.split("fantasy", 1)[1].strip()
        post_to_groupme(get_fantasy_matchup(after if after else None))
        return "ok", 200

    if "update" in text:
        team = match_team(text)
        if not team:
            post_to_groupme("Tell me which team, e.g. \"cubs update\" or \"bears update\".")
            return "ok", 200
        sport, league, abbr = team
        if "stop" in text:
            stop_watch(abbr, bot_id)
            post_to_groupme(f"Stopped tracking live updates for {abbr}.")
        else:
            line = start_watch(sport, league, abbr, bot_id)
            if line:
                post_to_groupme(f"Tracking {abbr} live now, I'll post here whenever the score changes.\nCurrent: {line}")
            else:
                post_to_groupme(f"{abbr} isn't playing right now, but I'll keep watching and post as soon as they are.")
                # still watch, in case game starts soon
                with watch_lock:
                    watched_teams[(bot_id, abbr)] = {"sport": sport, "league": league, "last_line": None, "bot_id": bot_id}
        return "ok", 200

    if "standing" in text or "division" in text:
        team = match_team(text)
        if team:
            sport, league, abbr = team
            post_to_groupme(get_team_division_standings(sport, league, abbr))
            return "ok", 200

        wants_nfl = "nfl" in text
        wants_mlb = "mlb" in text
        if wants_nfl and not wants_mlb:
            post_to_groupme(get_nfl_standings())
        elif wants_mlb and not wants_nfl:
            post_to_groupme(get_mlb_standings())
        else:
            post_to_groupme("NFL STANDINGS\n\n" + get_nfl_standings())
            post_to_groupme("MLB STANDINGS\n\n" + get_mlb_standings())
        return "ok", 200

    if "playoff" in text:
        team = match_team(text)
        if team:
            sport, league, abbr = team
            post_to_groupme(get_playoff_odds(sport, league, abbr))
        else:
            post_to_groupme("Tell me which team, e.g. \"cubs playoffs\" or \"bears playoffs\".")
        return "ok", 200

    if "predict" in text:
        team = match_team(text)
        if team:
            sport, league, abbr = team
            post_to_groupme(get_team_prediction(sport, league, abbr))
        else:
            post_to_groupme("Tell me which team, e.g. \"predict bears\" or \"predict cubs\".")
        return "ok", 200

    if "odds" in text or "spread" in text or "line" in text:
        wants_nfl = "nfl" in text
        wants_mlb = "mlb" in text
        if wants_nfl and not wants_mlb:
            post_to_groupme("NFL ODDS\n\n" + get_league_odds("football", "nfl"))
        elif wants_mlb and not wants_nfl:
            post_to_groupme("MLB ODDS\n\n" + get_league_odds("baseball", "mlb"))
        else:
            post_to_groupme("NFL ODDS\n\n" + get_league_odds("football", "nfl"))
            post_to_groupme("MLB ODDS\n\n" + get_league_odds("baseball", "mlb"))
        return "ok", 200

    team = match_team(text)
    if team:
        sport, league, abbr = team
        post_to_groupme(get_team_result(sport, league, abbr))
        return "ok", 200

    if "score" in text:
        nfl = get_league_scores("football", "nfl")
        mlb = get_league_scores("baseball", "mlb")
        post_to_groupme(f"NFL:\n{nfl}\n\nMLB:\n{mlb}")
        return "ok", 200

    # Fallback: only respond to AI mentions to avoid replying to every group message
    original_text = (data.get("text") or "").strip()
    if original_text.lower().startswith(("hey bot", "bot,", "@bot", "ai,")):
        reply = get_ai_reply(original_text)
        if reply:
            post_to_groupme(reply)

    return "ok", 200


@app.route("/", methods=["GET"])
def health():
    return "Score bot is running", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
