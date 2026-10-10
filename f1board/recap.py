import json
import os
from datetime import datetime, timezone

from f1board.store import load_recaps, write_item

SEASON = "2026"
MODEL = "claude-sonnet-5-5"
KEEP = 8
STANDINGS_ROWS = 6

RECAP_SYSTEM = (
    "You write short race recaps for the Pit Wall, a 2026 Formula 1 fan site. "
    "The user message is JSON with the finishing order of one round (grand prix, and sprint if there was one) and the championship standings after it. "
    "Write one paragraph of three to five sentences in plain text: no markdown, no headings, no bullets. "
    "Say who won and by what gap, who completed the podium, and the most notable result such as a team double, a big mover in the standings, or a tight points fight. "
    "If a sprint is present, mention its winner in one sentence. "
    "Use only the data given. Do not invent incidents, weather, tires, strategy, penalties, quotes, or lap details, and do not mention anything the data does not show. "
    "Gaps are in seconds behind the winner, as given. placesMoved is places gained (positive) or lost (negative) in the championship this round. Refer to drivers by surname after the first mention."
)

QUALIFYING_SYSTEM = (
    "You write short qualifying recaps for the Pit Wall, a 2026 Formula 1 fan site. "
    "The user message is JSON with the top ten of one qualifying session and each driver's current championship position. "
    "Write one paragraph of three to four sentences in plain text: no markdown, no headings, no bullets. "
    "Say who took pole and the gap to second, who else is on the front two rows, and the most notable thing such as a team lockout, a championship leader who missed pole, or a very tight top three. "
    "This is the qualifying classification, not the starting grid: grid penalties are not included, so do not claim anyone starts from a given position. "
    "Use only the data given. Do not invent incidents, weather, tires, strategy, penalties, quotes, or lap details. "
    "gapToPole is in seconds, as given, and is only present for drivers who set a time in the same final session as pole. Refer to drivers by surname after the first mention."
)


def _seconds(clock):
    try:
        text = str(clock or "")
        minutes, _, seconds = text.rpartition(":")
        return float(seconds) + (int(minutes) * 60 if minutes else 0)
    except ValueError:
        return None


def build_qualifying_facts(items, quali):
    schedule = {row["round"]: row for row in items["schedule"]["rows"]}
    race = schedule.get(quali["round"]) or {}
    standing = {row["name"]: row["position"] for row in items["drivers"]["rows"]}
    grid = quali["grid"]
    pole = grid[0]
    session = "q3" if pole.get("q3") else ("q2" if pole.get("q2") else "q1")
    pole_time = _seconds(pole.get(session))
    rows = []
    for row in grid:
        best = row.get("q3") or row.get("q2") or row.get("q1")
        entry = {"position": row["position"], "driver": row["driver"], "team": row["team"], "bestTime": best, "championshipPosition": standing.get(row["driver"])}
        time = _seconds(row.get(session))
        if time is not None and pole_time is not None and row["position"] > 1:
            entry["gapToPole"] = f"+{time - pole_time:.3f}"
        rows.append(entry)
    return {"round": quali["round"], "name": quali["name"], "circuit": race.get("circuit"), "date": race.get("date"), "finalSession": session.upper(), "top10": rows}


def _round_gains(latest):
    gains = {}
    for row in (latest.get("grandPrix") or []) + (latest.get("sprint") or []):
        gains[row["driver"]] = gains.get(row["driver"], 0) + float(row.get("points") or 0)
    return gains


def _places_moved(drivers, gains):
    before = sorted(
        ((float(row["points"]) - gains.get(row["name"], 0), row["position"], row["name"]) for row in drivers),
        key=lambda item: (-item[0], item[1]),
    )
    return {name: index + 1 - position for index, (_, position, name) in enumerate(before)}


def build_facts(items, latest):
    """The only things the model is allowed to talk about."""
    drivers = items["drivers"]["rows"]
    gains = _round_gains(latest)
    moved = _places_moved(drivers, gains)
    schedule = {row["round"]: row for row in items["schedule"]["rows"]}
    race = schedule.get(latest["round"]) or {}
    facts = {
        "round": latest["round"],
        "name": latest["name"],
        "circuit": race.get("circuit"),
        "date": race.get("date"),
        "grandPrix": latest.get("grandPrix") or [],
        "standingsAfter": [
            {
                "position": row["position"],
                "driver": row["name"],
                "team": row["team"],
                "points": row["points"],
                "pointsThisRound": gains.get(row["name"], 0),
                "placesMoved": moved.get(row["name"], 0),
            }
            for row in drivers[:STANDINGS_ROWS]
        ],
        "teamsTop3": [{"position": row["position"], "team": row["name"], "points": row["points"]} for row in items["teams"]["rows"][:3]],
    }
    if latest.get("hasSprint"):
        facts["sprint"] = latest.get("sprint") or []
    return facts


def _client():
    import anthropic
    import boto3

    secret = boto3.client("secretsmanager").get_secret_value(SecretId=os.environ["ANTHROPIC_SECRET_ID"])
    return anthropic.Anthropic(api_key=secret["SecretString"].strip(), timeout=60.0, max_retries=1)


def write_recap(facts, client=None, system=RECAP_SYSTEM):
    client = client or _client()
    response = client.messages.create(
        model=os.environ.get("CLAUDE_MODEL", MODEL),
        max_tokens=1200,
        system=system,
        output_config={"effort": "low"},
        messages=[{"role": "user", "content": json.dumps(facts, separators=(",", ":"))}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("declined")
    text = " ".join("".join(block.text for block in response.content if block.type == "text").split())
    if not text:
        raise RuntimeError("empty recap")
    return text


def _kind(row):
    return row.get("kind") or "race"


def _entry(kind, row, text, now):
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"round": row["round"], "kind": kind, "name": row["name"], "text": text, "generatedAt": stamp, "model": os.environ.get("CLAUDE_MODEL", MODEL)}


def refresh_recaps(table_name, items, qualifying=None, now=None, client=None, loader=None, writer=None):
    """At most one race recap and one qualifying recap per refresh, each only when it is new.

    The race recap covers the newest round with results. The qualifying recap covers a round whose
    qualifying is in but whose race is not, so it only exists between qualifying and the race.
    A failure in one never blocks the other, or the refresh that already wrote the season.
    """
    existing = (loader or load_recaps)(table_name, SEASON)
    have = {(row.get("round"), _kind(row)) for row in existing}
    results = items["results"]["rows"]
    newest = max((row["round"] for row in results), default=0)
    made = []
    if results:
        latest = max(results, key=lambda row: row["round"])
        if (latest["round"], "race") not in have:
            try:
                made.append(_entry("race", latest, write_recap(build_facts(items, latest), client), now))
            except Exception as exc:
                print(json.dumps({"recap_error": type(exc).__name__, "kind": "race", "detail": str(exc)[:200]}))
    if qualifying is not None:
        try:
            rows = qualifying() if callable(qualifying) else qualifying
            upcoming = max((row for row in rows if row["round"] > newest), key=lambda row: row["round"], default=None)
            if upcoming and (upcoming["round"], "qualifying") not in have:
                text = write_recap(build_qualifying_facts(items, upcoming), client, QUALIFYING_SYSTEM)
                made.append(_entry("qualifying", upcoming, text, now))
        except Exception as exc:
            print(json.dumps({"recap_error": type(exc).__name__, "kind": "qualifying", "detail": str(exc)[:200]}))
    if not made:
        return []
    fresh = {(row["round"], row["kind"]) for row in made}
    kept = [row for row in existing if (row.get("round"), _kind(row)) not in fresh]
    rows = sorted(kept + made, key=lambda row: (row["round"], _kind(row)))[-KEEP:]
    (writer or write_item)(table_name, {"season": SEASON, "item": "recaps", "rows": rows})
    return made
