import os
import threading
import time
import requests
from datetime import datetime
from flask import Flask, request

app = Flask(__name__)

GROUPME_BOT_ID = os.environ.get("GROUPME_BOT_ID")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
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


def get_ai_reply(user_text):
    if not GROQ_API_KEY:
        return None  # AI replies disabled if no key set
    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "llama-3.3-70b-versatile",
                "max_tokens": 300,
                "messages": [
                    {
                        "role": "system",
                        "content": (
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


def match_team(text):
    """Return (sport, league, abbr) if any alias appears in the text. Longest alias wins."""
    matches = [(alias, val) for alias, val in TEAM_ALIASES.items() if alias in text]
    if not matches:
        return None
    matches.sort(key=lambda m: len(m[0]), reverse=True)
    return matches[0][1]


# ---- Live score tracking ----
watched_teams = {}  # abbr -> {"sport":.., "league":.., "last_line":..}
watch_lock = threading.Lock()


def start_watch(sport, league, abbr):
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
        watched_teams[abbr] = {"sport": sport, "league": league, "last_line": line}
    return line


def stop_watch(abbr):
    with watch_lock:
        watched_teams.pop(abbr, None)


def poll_watched_teams():
    while True:
        time.sleep(60)
        with watch_lock:
            items = list(watched_teams.items())
        for abbr, info in items:
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
                    post_to_groupme(f"UPDATE: {found_line}")
                    with watch_lock:
                        if abbr in watched_teams:
                            watched_teams[abbr]["last_line"] = found_line
            except Exception:
                continue


_poller_thread = threading.Thread(target=poll_watched_teams, daemon=True)
_poller_thread.start()


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
            stop_watch(abbr)
            post_to_groupme(f"Stopped tracking live updates for {abbr}.")
        else:
            line = start_watch(sport, league, abbr)
            if line:
                post_to_groupme(f"Tracking {abbr} live now, I'll post here whenever the score changes.\nCurrent: {line}")
            else:
                post_to_groupme(f"{abbr} isn't playing right now, but I'll keep watching and post as soon as they are.")
                # still watch, in case game starts soon
                with watch_lock:
                    watched_teams[abbr] = {"sport": sport, "league": league, "last_line": None}
        return "ok", 200

    if "standing" in text:  # catches "standing" and "standings"
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
