import json
from decimal import Decimal


class SeasonIncomplete(Exception):
    pass


def season_view(items):
    """The five stored notes, in the same shape the public board reads."""
    required = {"drivers", "teams", "schedule", "results", "meta"}
    if set(items) - {"recaps"} != required:
        raise SeasonIncomplete("season incomplete")
    body = {
        "updatedAt": items["meta"]["updatedAt"],
        "drivers": items["drivers"]["rows"],
        "teams": items["teams"]["rows"],
        "schedule": items["schedule"]["rows"],
        "results": items["results"]["rows"],
    }
    recaps = (items.get("recaps") or {}).get("rows")
    if recaps:
        body["recaps"] = recaps
    return json.loads(json.dumps(body, default=_json_number))


def _json_number(value):
    if isinstance(value, Decimal):
        integral = value.to_integral_value()
        if value == integral:
            return int(integral)
        return float(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
