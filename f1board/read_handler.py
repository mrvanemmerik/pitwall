import json
import os

from f1board.season_view import SeasonIncomplete, season_view
from f1board.store import load_items

HEADERS = {
    "content-type": "application/json",
}


def handler(event, context):
    items = load_items(os.environ["TABLE_NAME"], os.environ["SEASON"])
    try:
        body = season_view(items)
    except SeasonIncomplete:
        return _response(500, {"error": "season incomplete"})
    return _response(200, body)


def _response(status, body):
    return {"statusCode": status, "headers": HEADERS, "body": json.dumps(body)}
