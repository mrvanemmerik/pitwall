from f1board.parse import PartialSeason, parse_driver_standings, parse_results, parse_schedule, parse_team_standings

SEASON = "2026"
REQUIRED = ("drivers", "teams", "races", "results", "sprint")


class IncompleteSeason(Exception):
    pass


def assemble_items(payloads, updated_at, source):
    if any(name not in payloads or payloads[name] is None for name in REQUIRED):
        raise IncompleteSeason("missing jolpica payload")
    try:
        rows = {
            "drivers": parse_driver_standings(payloads["drivers"]),
            "teams": parse_team_standings(payloads["teams"]),
            "schedule": parse_schedule(payloads["races"]),
            "results": parse_results(payloads["results"], payloads["sprint"]),
        }
    except (PartialSeason, KeyError, TypeError) as exc:
        raise IncompleteSeason(str(exc)) from exc
    items = {}
    for name, parsed in rows.items():
        items[name] = {"season": SEASON, "item": name, "rows": parsed}
    items["meta"] = {
        "season": SEASON,
        "item": "meta",
        "updatedAt": updated_at,
        "source": source,
    }
    return items
