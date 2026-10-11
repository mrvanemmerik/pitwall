import json

from botocore.exceptions import ClientError

from f1board import refresh
from f1board.ask_handler import handler as ask_handler
from tests.test_access import AccessTable, claims, event, gate, wire


class RefreshTable(AccessTable):
    """AccessTable plus the conditional update the cooldown uses."""

    def update_item(self, Key, UpdateExpression, ConditionExpression, ExpressionAttributeValues, ExpressionAttributeNames=None):
        key = (Key["user"], Key["day"])
        last = (self.items.get(key) or {}).get("at")
        if last is not None and not last < ExpressionAttributeValues[":cutoff"]:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "too soon"}}, "UpdateItem")
        self.items[key] = {"user": Key["user"], "day": Key["day"], "at": ExpressionAttributeValues[":now"], "expiresAt": ExpressionAttributeValues[":expires"]}


class FakeLambda:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def invoke(self, **kwargs):
        if self.fail:
            raise ClientError({"Error": {"Code": "ServiceException", "Message": "down"}}, "Invoke")
        self.calls.append(kwargs)
        return {"StatusCode": 202}


def test_a_refresh_starts_ingest_in_the_background():
    table, client = RefreshTable(), FakeLambda()
    refresh.start(table, client, "f1-whiteboard-ingest", now=1000)
    assert client.calls == [{"FunctionName": "f1-whiteboard-ingest", "InvocationType": "Event", "Payload": b'{"source": "admin"}'}]
    assert table.items[(refresh.META, refresh.SLOT)]["at"] == 1000


def test_a_second_refresh_inside_the_cooldown_is_refused_with_the_wait():
    table, client = RefreshTable(), FakeLambda()
    refresh.start(table, client, "fn", now=1000)
    try:
        refresh.start(table, client, "fn", now=1000 + 120)
        raise AssertionError("expected TooSoon")
    except refresh.TooSoon as exc:
        assert exc.retry_after == refresh.COOLDOWN_SECONDS - 120
    assert len(client.calls) == 1
    refresh.start(table, client, "fn", now=1000 + refresh.COOLDOWN_SECONDS)
    assert len(client.calls) == 2


def test_a_failed_start_gives_the_slot_back():
    table = RefreshTable()
    try:
        refresh.start(table, FakeLambda(fail=True), "fn", now=1000)
        raise AssertionError("expected the invoke error")
    except ClientError:
        pass
    assert table.items[(refresh.META, refresh.SLOT)]["at"] == 0
    refresh.start(table, FakeLambda(), "fn", now=1001)


def _wire_refresh(monkeypatch, table, client, function="f1-whiteboard-ingest"):
    gate(monkeypatch)
    wire(monkeypatch, table)
    if function:
        monkeypatch.setenv("INGEST_FUNCTION", function)
    else:
        monkeypatch.delenv("INGEST_FUNCTION", raising=False)
    monkeypatch.setattr("f1board.ask_handler._lambda_client", lambda: client)


def test_the_admin_route_starts_a_refresh_and_then_asks_to_wait(monkeypatch):
    table, client = RefreshTable(), FakeLambda()
    _wire_refresh(monkeypatch, table, client)
    owner = claims("owner@example.com")
    first = ask_handler(event("/admin/refresh", owner), None)
    assert first["statusCode"] == 202 and json.loads(first["body"]) == {"started": True}
    second = ask_handler(event("/admin/refresh", owner), None)
    body = json.loads(second["body"])
    assert second["statusCode"] == 429 and 0 < body["retryAfter"] <= refresh.COOLDOWN_SECONDS
    assert len(client.calls) == 1


def test_only_an_admin_may_refresh(monkeypatch):
    table, client = RefreshTable(), FakeLambda()
    _wire_refresh(monkeypatch, table, client)
    for who in (claims("pat@example.com"), claims("owner@example.com", verified="false")):
        assert ask_handler(event("/admin/refresh", who), None)["statusCode"] == 403
    assert ask_handler({"rawPath": "/admin/refresh", "body": "{}"}, None)["statusCode"] == 401
    assert client.calls == [] and table.items == {}


def test_refresh_reports_when_it_is_not_set_up_or_the_start_fails(monkeypatch):
    owner = claims("owner@example.com")
    _wire_refresh(monkeypatch, RefreshTable(), FakeLambda(), function=None)
    assert ask_handler(event("/admin/refresh", owner), None)["statusCode"] == 503
    _wire_refresh(monkeypatch, RefreshTable(), FakeLambda(fail=True))
    assert ask_handler(event("/admin/refresh", owner), None)["statusCode"] == 502
