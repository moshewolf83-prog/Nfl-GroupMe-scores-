import os
import requests
from datetime import datetime
from flask import Flask, request

app = Flask(__name__)

GROUPME_BOT_ID = os.environ.get("GROUPME_BOT_ID")

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


def match_team(text):
    """Return (sport, league, abbr) if any alias appears in the text. Longest alias wins."""
    matches = [(alias, val) for alias, val in TEAM_ALIASES.items() if alias in text]
    if not matches:
        return None
    matches.sort(key=lambda m: len(m[0]), reverse=True)
    return matches[0][1]


def post_to_groupme(text):
    if not GROUPME_BOT_ID:
        print("GROUPME_BOT_ID not set, cannot post")
        return
    # GroupMe caps messages around 1000 chars; split if needed
    for i in range(0, len(text), 950):
        chunk = text[i:i + 950]
        requests.post(
            "https://api.groupme.com/v3/bots/post",
            json={"bot_id": GROUPME_BOT_ID, "text": chunk},
            timeout=10,
        )


@app.route("/callback", methods=["POST"])
def callback():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip().lower()
    sender_type = data.get("sender_type", "")

    if sender_type == "bot":
        return "ok", 200

    if "standings" in text:
        wants_nfl = "nfl" in text
        wants_mlb = "mlb" in text
        if wants_nfl and not wants_mlb:
            post_to_groupme(get_nfl_standings())
        elif wants_mlb and not wants_nfl:
            post_to_groupme(get_mlb_standings())
        else:
            # no sport specified, or both mentioned -> send both
            post_to_groupme("NFL STANDINGS\n\n" + get_nfl_standings())
            post_to_groupme("MLB STANDINGS\n\n" + get_mlb_standings())
        return "ok", 200

    team = match_team(text)
    if team:
        sport, league, abbr = team
        post_to_groupme(get_team_result(sport, league, abbr))
    elif "score" in text:
        nfl = get_league_scores("football", "nfl")
        mlb = get_league_scores("baseball", "mlb")
        post_to_groupme(f"NFL:\n{nfl}\n\nMLB:\n{mlb}")

    return "ok", 200


@app.route("/", methods=["GET"])
def health():
    return "Score bot is running", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
