import os
import threading
import time
import imaplib
import email
import re
from email.header import decode_header
import xml.etree.ElementTree as ET
from urllib.parse import quote
import requests
from datetime import datetime
from flask import Flask, request

app = Flask(__name__)

GROUPME_BOT_ID = os.environ.get("GROUPME_BOT_ID")  # fallback / single-group default
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
    when = game_date.strftime("%a %b %-d, %-I:%M %p UTC")
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


def _collect_standing_groups(node, groups):
    """Recursively walk ESPN's standings tree and collect (division_name, entries)."""
    name = node.get("name") or node.get("abbreviation") or ""
    standings = node.get("standings")
    if standings and standings.get("entries"):
        groups.append((name, standings["entries"]))
    for child in node.get("children", []):
        _collect_standing_groups(child, groups)


def _stat_value(entry, stat_names, use_display=False):
    for stat in entry.get("stats", []):
        if stat.get("name") in stat_names or stat.get("shortDisplayName") in stat_names:
            return stat.get("displayValue") if use_display else stat.get("value")
    return None


def get_standings(sport, league):
    try:
        resp = requests.get(standings_url(sport, league), timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return f"Couldn't fetch standings right now ({e})"

    groups = []
    for child in data.get("children", []):
        _collect_standing_groups(child, groups)

    if not groups:
        return "Couldn't find standings right now."

    sections = []
    for div_name, entries in groups:
        rows = []
        for entry in entries:
            team = entry.get("team", {})
            abbr = team.get("abbreviation", "???")
            wins = _stat_value(entry, {"wins"})
            losses = _stat_value(entry, {"losses"})
            gb = _stat_value(entry, {"gamesBehind"}, use_display=True) or "-"
            wins = int(wins) if wins is not None else 0
            losses = int(losses) if losses is not None else 0
            rows.append((wins, losses, gb, abbr))
        rows.sort(key=lambda r: r[0] / max(r[0] + r[1], 1), reverse=True)
        lines = [f"{abbr} {w}-{l} (GB {gb})" for w, l, gb, abbr in rows]
        sections.append(f"{div_name}:\n" + "\n".join(lines))

    return "\n\n".join(sections)


def get_mlb_standings():
    return get_standings("baseball", "mlb")


def get_nfl_standings():
    return get_standings("football", "nfl")


def get_ai_reply(user_text, system_prompt=None):
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
                "model": "meta-llama/llama-3.3-70b-instruct:free",
                "max_tokens": 300,
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
            timeout=20,
        )
        if resp.status_code != 200:
            return f"(AI error {resp.status_code}: {resp.text[:300]})"
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip() or None
    except Exception as e:
        return f"(AI error: {e})"


def get_team_division_standings(sport, league, abbr):
    try:
        resp = requests.get(standings_url(sport, league), timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return f"Couldn't fetch standings right now ({e})"

    groups = []
    for child in data.get("children", []):
        _collect_standing_groups(child, groups)

    for div_name, entries in groups:
        abbrs_in_group = {e.get("team", {}).get("abbreviation", "") for e in entries}
        if abbr not in abbrs_in_group:
            continue
        rows = []
        for entry in entries:
            team = entry.get("team", {})
            team_abbr = team.get("abbreviation", "???")
            wins = _stat_value(entry, {"wins"})
            losses = _stat_value(entry, {"losses"})
            gb = _stat_value(entry, {"gamesBehind"}, use_display=True) or "-"
            wins = int(wins) if wins is not None else 0
            losses = int(losses) if losses is not None else 0
            rows.append((wins, losses, gb, team_abbr))
        rows.sort(key=lambda r: r[0] / max(r[0] + r[1], 1), reverse=True)
        lines = [f"{team_abbr} {w}-{l} (GB {gb})" for w, l, gb, team_abbr in rows]
        return f"{div_name}:\n" + "\n".join(lines)

    return "Couldn't find that team's division."


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
    match = re.search(r"on (.+ \d+, \d+, .+? UTC)", next_game_text)
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
                if found_line and found_line != info["last_line"]:
                    _post_to_groupme(f"UPDATE: {found_line}", bot_id=bot_id)
                    with watch_lock:
                        if key in watched_teams:
                            watched_teams[key]["last_line"] = found_line
            except Exception:
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
