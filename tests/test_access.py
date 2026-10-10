import json

from botocore.exceptions import ClientError

from f1board import access
from f1board.ask_handler import handler as ask_handler


class AccessTable:
    """Just enough of a DynamoDB table for the access items."""

    def __init__(self):
        self.items = {}

    def get_item(self, Key):
        item = self.items.get((Key["user"], Key["day"]))
        return {"Item": dict(item)} if item else {}

    def put_item(self, Item, ConditionExpression=None, ExpressionAttributeNames=None):
        key = (Item["user"], Item["day"])
        if ConditionExpression and key in self.items:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "exists"}}, "PutItem")
        self.items[key] = dict(Item)

    def query(self, **kwargs):
        return {"Items": [dict(v) for (user, _), v in self.items.items() if user == access.INDEX]}


def claims(email, verified="true", name="Pat Tester", sub=None):
    data = {"sub": sub or "sub-" + str(email)}
    if email is not None:
        data["email"] = email
    if verified is not None:
        data["email_verified"] = verified
    if name:
        data["name"] = name
    return data


def gate(monkeypatch, allowed="owner@example.com", admins="owner@example.com"):
    for key, value in (("ALLOWED_EMAILS", allowed), ("ADMIN_EMAILS", admins)):
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


def test_fixed_lists_always_win_and_others_start_with_none(monkeypatch):
    gate(monkeypatch, allowed="a@example.com", admins="owner@example.com")
    table = AccessTable()
    assert access.status_for(table, claims("A@Example.com")) == "approved"
    assert access.status_for(table, claims("owner@example.com")) == "approved"
    assert access.status_for(table, claims("stranger@example.com")) == "none"
    assert access.status_for(table, claims("owner@example.com", verified="false")) == "none"
    assert access.status_for(table, claims(None)) == "none"


def test_a_request_is_stored_once_and_the_owner_is_told_once(monkeypatch):
    gate(monkeypatch)
    table, sent = AccessTable(), []
    notify = lambda email, name: sent.append((email, name))
    assert access.request_access(table, claims("Pat@Example.com"), notify) == "pending"
    assert access.request_access(table, claims("pat@example.com"), notify) == "pending"
    assert sent == [("pat@example.com", "Pat Tester")]
    assert len(table.items) == 1


def test_an_unverified_email_cannot_request_access(monkeypatch):
    gate(monkeypatch)
    try:
        access.request_access(AccessTable(), claims("pat@example.com", verified="false"))
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_a_failed_notification_does_not_lose_the_request(monkeypatch, capsys):
    gate(monkeypatch)
    table = AccessTable()

    def broken(email, name):
        raise RuntimeError("sns down")

    assert access.request_access(table, claims("pat@example.com"), broken) == "pending"
    assert access.status_of(table, "pat@example.com") == "pending"
    assert "access_notify_error" in capsys.readouterr().out


def test_approve_and_deny_change_what_the_person_may_do(monkeypatch):
    gate(monkeypatch)
    table = AccessTable()
    pat = claims("pat@example.com")
    access.request_access(table, pat)
    assert not access.is_allowed(table, pat)
    access.decide(table, claims("owner@example.com"), "pat@example.com", "approve")
    assert access.is_allowed(table, pat)
    access.decide(table, claims("owner@example.com"), "pat@example.com", "deny")
    assert not access.is_allowed(table, pat)
    assert access.status_for(table, pat) == "denied"
    assert access.request_access(table, pat) == "denied"  # a denial is not reopened by asking again


def test_the_owner_can_add_someone_who_never_asked(monkeypatch):
    gate(monkeypatch)
    table = AccessTable()
    access.decide(table, claims("owner@example.com"), "  Friend@Example.com ", "approve")
    assert access.is_allowed(table, claims("friend@example.com"))


def test_decisions_reject_junk(monkeypatch):
    gate(monkeypatch)
    for email, decision in (("not-an-email", "approve"), ("a@b.co", "delete"), ("a@b.co,c@d.co", "approve")):
        try:
            access.decide(AccessTable(), claims("owner@example.com"), email, decision)
        except ValueError:
            continue
        raise AssertionError((email, decision))


def test_requests_list_pending_first_then_newest(monkeypatch):
    gate(monkeypatch)
    table = AccessTable()
    for email, stamp, status in (("old@x.co", "2026-10-01T00:00:00Z", "pending"), ("new@x.co", "2026-10-09T00:00:00Z", "pending"), ("ok@x.co", "2026-10-10T00:00:00Z", "approved")):
        table.items[(access.INDEX, email)] = {"user": access.INDEX, "day": email, "status": status, "requestedAt": stamp}
    assert [row["email"] for row in access.list_requests(table)] == ["new@x.co", "old@x.co", "ok@x.co"]


def event(path, claim, body=None):
    return {"rawPath": path, "requestContext": {"authorizer": {"jwt": {"claims": claim}}}, "body": json.dumps(body or {})}


def wire(monkeypatch, table):
    monkeypatch.setattr("f1board.ask_handler._cap_table", lambda: table)
    monkeypatch.setattr("f1board.ask_handler._notifier", lambda: None)
    monkeypatch.setenv("TABLE_NAME", "season")
    monkeypatch.setenv("SEASON", "2026")
    monkeypatch.setattr("f1board.ask_handler.charge_question", lambda *a, **k: 1)
    monkeypatch.setattr("f1board.ask_handler.load_items", lambda *a: {
        "drivers": {"rows": []}, "teams": {"rows": []}, "schedule": {"rows": []}, "results": {"rows": []}, "meta": {"updatedAt": "2026-10-10T14:35:38Z"},
    })
    monkeypatch.setattr("f1board.ask_handler.gather_web", lambda *a, **k: "")
    monkeypatch.setattr("f1board.ask_handler.ask_model", lambda *a: "Kimi leads.")


def test_the_whole_flow_through_the_handler(monkeypatch):
    gate(monkeypatch)
    table = AccessTable()
    wire(monkeypatch, table)
    pat, owner = claims("pat@example.com"), claims("owner@example.com", name="Owner")
    ask = lambda: ask_handler(event("/", pat, {"question": "Who leads?"}), None)

    blocked = ask()
    assert blocked["statusCode"] == 403 and json.loads(blocked["body"])["access"] == "none"
    status = json.loads(ask_handler(event("/access/status", pat), None)["body"])
    assert status == {"status": "none", "admin": False, "email": "pat@example.com"}

    assert json.loads(ask_handler(event("/access/request", pat), None)["body"])["status"] == "pending"
    assert json.loads(ask().get("body"))["access"] == "pending"

    queue = json.loads(ask_handler(event("/admin/requests", owner), None)["body"])["requests"]
    assert [(row["email"], row["status"]) for row in queue] == [("pat@example.com", "pending")]

    done = ask_handler(event("/admin/decide", owner, {"email": "pat@example.com", "decision": "approve"}), None)
    assert json.loads(done["body"])["requests"][0]["status"] == "approved"
    answered = ask()
    assert answered["statusCode"] == 200 and json.loads(answered["body"])["answer"] == "Kimi leads."


def test_only_a_verified_admin_may_use_the_admin_routes(monkeypatch):
    gate(monkeypatch)
    table = AccessTable()
    wire(monkeypatch, table)
    for who in (claims("pat@example.com"), claims("owner@example.com", verified="false"), claims("owner@example.com", verified=None)):
        assert ask_handler(event("/admin/requests", who), None)["statusCode"] == 403
        assert ask_handler(event("/admin/decide", who, {"email": "x@y.co", "decision": "approve"}), None)["statusCode"] == 403
    assert table.items == {}


def test_a_bad_decision_is_a_400_and_a_signed_out_caller_a_401(monkeypatch):
    gate(monkeypatch)
    wire(monkeypatch, AccessTable())
    owner = claims("owner@example.com")
    assert ask_handler(event("/admin/decide", owner, {"email": "nope", "decision": "approve"}), None)["statusCode"] == 400
    assert ask_handler({"rawPath": "/admin/requests", "body": "{}"}, None)["statusCode"] == 401
    assert ask_handler({"rawPath": "/access/request", "body": "{}"}, None)["statusCode"] == 401


def test_requesting_with_an_unverified_email_is_a_400(monkeypatch):
    gate(monkeypatch)
    wire(monkeypatch, AccessTable())
    assert ask_handler(event("/access/request", claims("pat@example.com", verified="false")), None)["statusCode"] == 400


def test_without_any_lists_the_chat_is_open_as_before(monkeypatch):
    gate(monkeypatch, allowed=None, admins=None)
    wire(monkeypatch, AccessTable())
    assert ask_handler(event("/", claims("anyone@example.com", verified=None), {"question": "Who leads?"}), None)["statusCode"] == 200
