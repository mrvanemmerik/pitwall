import json
import time

from botocore.exceptions import ClientError

META = "access#meta"
SLOT = "last-refresh"
# Jolpica is a free API and a run pages it several times, so an admin may start one refresh per window.
COOLDOWN_SECONDS = 300


class TooSoon(Exception):
    def __init__(self, retry_after):
        super().__init__("refresh too soon")
        self.retry_after = retry_after


def start(table, client, function_name, now=None):
    """Claim the cooldown slot, then start ingest without waiting for it. A failed start hands the slot back."""
    now = int(time.time() if now is None else now)
    key = {"user": META, "day": SLOT}
    try:
        table.update_item(
            Key=key,
            UpdateExpression="SET #at = :now, expiresAt = :expires",
            ConditionExpression="attribute_not_exists(#at) OR #at < :cutoff",
            ExpressionAttributeNames={"#at": "at"},
            ExpressionAttributeValues={":now": now, ":cutoff": now - COOLDOWN_SECONDS + 1, ":expires": now + 86400},
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise
        last = int((table.get_item(Key=key).get("Item") or {}).get("at", now))
        raise TooSoon(max(last + COOLDOWN_SECONDS - now, 1)) from None
    try:
        client.invoke(FunctionName=function_name, InvocationType="Event", Payload=json.dumps({"source": "admin"}).encode())
    except Exception:
        table.put_item(Item={**key, "at": 0, "expiresAt": now + 86400})
        raise
