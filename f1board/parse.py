class PartialSeason(Exception):
    pass


def _number(value):
    number = float(value)
    if number.is_integer():
        return int(number)
    return number


def _driver_name(driver):
    return f"{driver['givenName']} {driver['familyName']}"


def _finishers(rows, driver_key="Driver", team_key="Constructor"):
    parsed = []
    for row in rows:
        if not str(row.get("position", "")).isdigit():
            continue
        position = int(row["position"])
        if position > 10:
            continue
        item = {
            "position": position,
            "driver": _driver_name(row[driver_key]),
            "team": row[team_key]["name"],
        }
        if row.get("points") not in (None, ""):
            item["points"] = _number(row["points"])
        time = (row.get("Time") or {}).get("time")
        if time:
            item["time"] = time
        parsed.append(item)
    return parsed


def parse_driver_standings(payload):
    lists = payload["MRData"]["StandingsTable"]["StandingsLists"]
    if not lists:
        raise PartialSeason("driver standings missing")
    rows = []
    for row in lists[0]["DriverStandings"]:
        rows.append(
            {
                "position": int(row["position"]),
                "name": _driver_name(row["Driver"]),
                "team": row["Constructors"][0]["name"],
                "points": _number(row["points"]),
                "wins": int(row["wins"]),
            }
        )
    return rows


def parse_team_standings(payload):
    lists = payload["MRData"]["StandingsTable"]["StandingsLists"]
    if not lists:
        raise PartialSeason("team standings missing")
    rows = []
    for row in lists[0]["ConstructorStandings"]:
        rows.append(
            {
                "position": int(row["position"]),
                "name": row["Constructor"]["name"],
                "points": _number(row["points"]),
                "wins": int(row["wins"]),
            }
        )
    return rows


def parse_schedule(payload):
    races = payload["MRData"]["RaceTable"]["Races"]
    if not races:
        raise PartialSeason("schedule missing")
    rows = []
    for race in races:
        row = {
            "round": int(race["round"]),
            "name": race["raceName"],
            "circuit": race["Circuit"]["circuitName"],
            "date": race["date"],
            "hasSprint": "Sprint" in race,
        }
        if race.get("time"):
            row["time"] = race["time"]
        sessions = _sessions(race)
        if sessions:
            row["sessions"] = sessions
        rows.append(row)
    return rows


# Jolpica key -> label shown on the board. Practice is left out on purpose.
SESSIONS = {"SprintQualifying": "Sprint qualifying", "SprintShootout": "Sprint qualifying", "Sprint": "Sprint", "Qualifying": "Qualifying"}


def _sessions(race):
    found = []
    for key, name in SESSIONS.items():
        session = race.get(key) or {}
        if session.get("date") and session.get("time"):
            found.append({"name": name, "date": session["date"], "time": session["time"]})
    return sorted(found, key=lambda row: (row["date"], row["time"]))


def parse_results(results_payload, sprint_payload):
    races = results_payload["MRData"]["RaceTable"]["Races"]
    if races is None:
        raise PartialSeason("results missing")
    sprints = {}
    for race in sprint_payload["MRData"]["RaceTable"]["Races"]:
        sprints[int(race["round"])] = race
    rows = []
    for race in races:
        if "Results" not in race:
            continue
        round_number = int(race["round"])
        sprint_race = sprints.get(round_number)
        sprint_rows = _finishers(sprint_race.get("SprintResults", [])) if sprint_race else []
        rows.append(
            {
                "round": round_number,
                "name": race["raceName"],
                "hasSprint": bool(sprint_rows),
                "grandPrix": _finishers(race["Results"]),
                "sprint": sprint_rows,
            }
        )
    return rows


def parse_qualifying(payload):
    rows = []
    for race in payload["MRData"]["RaceTable"]["Races"]:
        grid = []
        for row in race.get("QualifyingResults") or []:
            if not str(row.get("position", "")).isdigit() or int(row["position"]) > 10:
                continue
            grid.append(
                {
                    "position": int(row["position"]),
                    "driver": _driver_name(row["Driver"]),
                    "team": row["Constructor"]["name"],
                    "q1": row.get("Q1") or None,
                    "q2": row.get("Q2") or None,
                    "q3": row.get("Q3") or None,
                }
            )
        if grid:
            rows.append({"round": int(race["round"]), "name": race["raceName"], "grid": grid})
    return rows
