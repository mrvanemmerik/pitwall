import json
import os

from f1board.season_view import SeasonIncomplete, season_view
from f1board.store import load_items

# The season changes only a few times a week, so browsers may reuse it for five minutes.
# Errors are never cached, so a reload can recover as soon as the data is whole again.
CACHE_OK = "public, max-age=300"
CACHE_ERROR = "no-store"


def handler(event, context):
    items = load_items(os.environ["TABLE_NAME"], os.environ["SEASON"])
    try:
        body = season_view(items)
    except SeasonIncomplete:
        return _response(500, {"error": "season incomplete"}, CACHE_ERROR)
    return _response(200, body, CACHE_OK)


def _response(status, body, cache):
    headers = {"content-type": "application/json", "cache-control": cache}
    return {"statusCode": status, "headers": headers, "body": json.dumps(body)}
