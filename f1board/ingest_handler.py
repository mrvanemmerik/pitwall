import json
import os
from datetime import datetime, timezone

from f1board.http_client import fetch_all_races, fetch_json
from f1board.season import assemble_items
from f1board.store import write_items

PATHS = {
    "drivers": "driverstandings.json",
    "teams": "constructorstandings.json",
    "races": "races.json",
    "results": "results.json",
    "sprint": "sprint.json",
}


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


PAGED = {"results", "sprint"}


def handler(event, context):
    base = os.environ["JOLPICA_BASE"].rstrip("/") + "/"
    payloads = {}
    for name, path in PATHS.items():
        url = base + path
        payloads[name] = fetch_all_races(url) if name in PAGED else fetch_json(url)
    items = assemble_items(payloads, utc_now(), base)
    write_items(os.environ["TABLE_NAME"], items)
    recap = _recap(items, base)
    return {"ok": True, "updatedAt": items["meta"]["updatedAt"], "recap": recap}


def _recap(items, base=None):
    """A recap is a nicety: it must never fail the refresh that already wrote the season."""
    if not os.environ.get("ANTHROPIC_SECRET_ID"):
        return None
    try:
        from f1board.parse import parse_qualifying
        from f1board.recap import refresh_recaps

        def qualifying():
            return parse_qualifying(fetch_all_races(base + "qualifying.json"))

        made = refresh_recaps(os.environ["TABLE_NAME"], items, qualifying=qualifying if base else None)
        return [{"round": row["round"], "kind": row["kind"]} for row in made]
    except Exception as exc:
        print(json.dumps({"recap_error": type(exc).__name__, "detail": str(exc)[:200]}))
        return None
