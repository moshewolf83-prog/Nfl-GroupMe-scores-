import os
import requests
from flask import Flask, request

app = Flask(__name__)

# Set these as environment variables on your host (Render, etc.)
GROUPME_BOT_ID = os.environ.get("GROUPME_BOT_ID")
BOT_NAME_LOWER = os.environ.get("BOT_NAME", "").lower()  # optional, avoids replying to itself

ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"

# Maps things people might text -> ESPN team abbreviation
TEAM_ALIASES = {
    "bears": "CHI", "chicago": "CHI", "chi": "CHI",
    "bills": "BUF", "buffalo": "BUF", "buf": "BUF",
    "dolphins": "MIA", "miami": "MIA", "mia": "MIA",
    "patriots": "NE", "pats": "NE", "ne": "NE",
    "jets": "NYJ", "nyj": "NYJ",
    "ravens": "BAL", "baltimore": "BAL", "bal": "BAL",
    "bengals": "CIN", "cincinnati": "CIN", "cin": "CIN",
    "browns": "CLE", "cleveland": "CLE", "cle": "CLE",
    "steelers": "PIT", "pittsburgh": "PIT", "pit": "PIT",
    "texans": "HOU", "houston": "HOU", "hou": "HOU",
    "colts": "IND", "indianapolis": "IND", "ind": "IND",
    "jaguars": "JAX", "jags": "JAX", "jacksonville": "JAX", "jax": "JAX",
    "titans": "TEN", "tennessee": "TEN", "ten": "TEN",
    "broncos": "DEN", "denver": "DEN", "den": "DEN",
    "chiefs": "KC", "kansas city": "KC", "kc": "KC",
    "raiders": "LV", "las vegas": "LV", "lv": "LV",
    "chargers": "LAC", "lac": "LAC",
    "cowboys": "DAL", "dallas": "DAL", "dal": "DAL",
    "giants": "NYG", "nyg": "NYG",
    "eagles": "PHI", "philadelphia": "PHI", "philly": "PHI", "phi": "PHI",
    "commanders": "WSH", "washington": "WSH", "wsh": "WSH",
    "packers": "GB", "green bay": "GB", "gb": "GB",
    "lions": "DET", "detroit": "DET", "det": "DET",
    "vikings": "MIN", "minnesota": "MIN", "min": "MIN",
    "49ers": "SF", "niners": "SF", "san francisco": "SF", "sf": "SF",
    "seahawks": "SEA", "seattle": "SEA", "sea": "SEA",
    "rams": "LAR", "lar": "LAR",
    "cardinals": "ARI", "arizona": "ARI", "ari": "ARI",
    "buccaneers": "TB", "bucs": "TB", "tampa bay": "TB", "tb": "TB",
    "falcons": "ATL", "atlanta": "ATL", "atl": "ATL",
    "panthers": "CAR", "carolina": "CAR", "car": "CAR",
    "saints": "NO", "new orleans": "NO", "no": "NO",
}


def fetch_scoreboard():
    resp = requests.get(ESPN_SCOREBOARD_URL, timeout=10)
    resp.raise_for_status()
    return resp.json()


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


def get_nfl_scores(team_abbr=None):
    try:
        data = fetch_scoreboard()
    except Exception as e:
        return f"Couldn't fetch scores right now ({e})"

    events = data.get("events", [])
    if not events:
        return "No NFL games found right now."

    lines = []
    for event in events:
        result = format_game(event)
        if not result:
            continue
        line, abbrs = result
        if team_abbr and team_abbr not in abbrs:
            continue
        lines.append(line)

    if team_abbr and not lines:
        return "That team isn't playing right now."
    return "\n".join(lines) if lines else "No NFL games found right now."


def match_team(text):
    """Return an ESPN abbreviation if any alias appears in the text."""
    for alias, abbr in TEAM_ALIASES.items():
        if alias in text:
            return abbr
    return None


def post_to_groupme(text):
    if not GROUPME_BOT_ID:
        print("GROUPME_BOT_ID not set, cannot post")
        return
    requests.post(
        "https://api.groupme.com/v3/bots/post",
        json={"bot_id": GROUPME_BOT_ID, "text": text},
        timeout=10,
    )


@app.route("/callback", methods=["POST"])
def callback():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip().lower()
    sender_type = data.get("sender_type", "")

    # Ignore messages sent by bots (including this one) to avoid loops
    if sender_type == "bot":
        return "ok", 200

    team_abbr = match_team(text)
    if team_abbr:
        post_to_groupme(get_nfl_scores(team_abbr))
    elif "score" in text:
        post_to_groupme(get_nfl_scores())

    return "ok", 200


@app.route("/", methods=["GET"])
def health():
    return "NFL score bot is running", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
