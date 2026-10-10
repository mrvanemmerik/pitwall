import json
from types import SimpleNamespace
from decimal import Decimal

import pytest

from f1board.http_client import fetch_json
from f1board.http_client import merge_race_pages
from f1board.ingest_handler import handler as ingest_handler
from f1board.ask_handler import CapReached, charge_question, handler as ask_handler, runtime_session
from f1board.read_handler import handler as read_handler
from f1board.season_tool import grounded_message
from f1board.web_fetch import WebFetchError, check_url, gather_web, html_to_text
from f1board.season_tool import handler as season_tool
from f1board.season_view import SeasonIncomplete
from f1board.store import _marshall
from tests.test_parse import DRIVERS, RACES, RESULTS, SPRINT, TEAMS


class FakeTable:
    def __init__(self):
        self.items = {}

    def write_items(self, items):
        self.items = items

    def load_items(self, season):
        return {key: value for key, value in self.items.items() if value["season"] == season}


def test_merge_race_pages_joins_a_round_split_across_pages():
    first = {
        "MRData": {
            "RaceTable": {
                "Races": [
                    {"round": "1", "raceName": "Australian Grand Prix", "Results": [{"position": "1"}]},
                ]
            }
        }
    }
    second = {
        "MRData": {
            "RaceTable": {
                "Races": [
                    {"round": "1", "raceName": "Australian Grand Prix", "Results": [{"position": "2"}]},
                    {"round": "2", "raceName": "Chinese Grand Prix", "Results": [{"position": "1"}]},
                ]
            }
        }
    }
    merged = merge_race_pages([first, second])
    races = merged["MRData"]["RaceTable"]["Races"]
    assert [race["round"] for race in races] == ["1", "2"]
    assert [row["position"] for row in races[0]["Results"]] == ["1", "2"]


def test_ingest_writes_only_after_all_fetches(monkeypatch):
    table = FakeTable()
    calls = []

    def fake_fetch(url):
        calls.append(url)
        name = url.rsplit("/", 1)[-1]
        return {
            "driverstandings.json": DRIVERS,
            "constructorstandings.json": TEAMS,
            "races.json": RACES,
        }[name]

    def fake_paged(url):
        calls.append(url)
        name = url.rsplit("/", 1)[-1]
        return {"results.json": RESULTS, "sprint.json": SPRINT}[name]

    monkeypatch.setenv("TABLE_NAME", "f1-whiteboard")
    monkeypatch.setenv("SEASON", "2026")
    monkeypatch.setenv("JOLPICA_BASE", "https://api.jolpi.ca/ergast/f1/2026/")
    monkeypatch.setattr("f1board.ingest_handler.fetch_json", fake_fetch)
    monkeypatch.setattr("f1board.ingest_handler.fetch_all_races", fake_paged)
    monkeypatch.setattr("f1board.ingest_handler.write_items", lambda table_name, items: table.write_items(items))
    monkeypatch.setattr("f1board.ingest_handler.utc_now", lambda: "2026-10-10T18:00:00Z")

    result = ingest_handler({}, None)

    assert result["ok"] is True
    assert result["updatedAt"] == "2026-10-10T18:00:00Z"
    assert len(calls) == 5
    assert set(table.items) == {"drivers", "teams", "schedule", "results", "meta"}


def test_ingest_writes_nothing_when_a_fetch_fails(monkeypatch):
    table = FakeTable()

    def fake_fetch(url):
        return DRIVERS

    def fake_paged(url):
        if url.endswith("sprint.json"):
            raise OSError("down")
        return SPRINT

    monkeypatch.setenv("TABLE_NAME", "f1-whiteboard")
    monkeypatch.setenv("SEASON", "2026")
    monkeypatch.setenv("JOLPICA_BASE", "https://api.jolpi.ca/ergast/f1/2026/")
    monkeypatch.setattr("f1board.ingest_handler.fetch_json", fake_fetch)
    monkeypatch.setattr("f1board.ingest_handler.fetch_all_races", fake_paged)
    monkeypatch.setattr("f1board.ingest_handler.write_items", lambda table_name, items: table.write_items(items))

    with pytest.raises(OSError):
        ingest_handler({}, None)
    assert table.items == {}


class FakeCap:
    def __init__(self, questions=0):
        self.questions = questions

    def update_item(self, **kwargs):
        from botocore.exceptions import ClientError

        limit = kwargs["ExpressionAttributeValues"][":limit"]
        if self.questions >= limit:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "no"}}, "UpdateItem")
        self.questions += 1


def test_charge_question_stops_after_the_limit():
    table = FakeCap(questions=30)
    with pytest.raises(CapReached):
        charge_question(table, "user", "2026-10-10", limit=30)
    assert table.questions == 30


def test_runtime_session_is_scoped_to_the_signed_in_user():
    first = runtime_session("abc-123", "browser-session")
    other = runtime_session("zzz-999", "browser-session")
    assert first != other
    assert 33 <= len(first) <= 100
    assert first.startswith("gabc123")


def test_ask_uses_only_the_question_and_the_stored_season(monkeypatch):
    seen = {}

    def invoke(message, session_id, actor_id):
        seen["message"] = message
        seen["session"] = session_id
        seen["actor"] = actor_id
        return "Andrea Kimi Antonelli leads with 320 points."

    monkeypatch.setenv("TABLE_NAME", "f1-whiteboard")
    monkeypatch.setenv("SEASON", "2026")
    monkeypatch.setenv("CAP_TABLE", "f1-whiteboard-ask")
    monkeypatch.setenv("HARNESS_ARN", "arn:aws:bedrock-agentcore:us-east-2:1:harness/example")
    monkeypatch.setattr("f1board.ask_handler.charge_question", lambda table, user, day, limit=30: None)
    monkeypatch.setattr("f1board.ask_handler._cap_table", lambda: object())
    monkeypatch.setattr("f1board.ask_handler.load_items", lambda table_name, season: {
        "drivers": {"rows": [{"name": "Andrea Kimi Antonelli", "points": 320}]},
        "teams": {"rows": []},
        "schedule": {"rows": []},
        "results": {"rows": []},
        "meta": {"updatedAt": "2026-10-10T14:35:38Z"},
    })
    monkeypatch.setattr("f1board.ask_handler.invoke_harness", invoke)
    event = {
        "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "user-sub-123456789"}}}},
        "body": json.dumps({"question": "Who leads?", "model": "other", "tools": ["shell"], "name": "Patched Name", "sessionId": "browser-session-0001"}),
    }
    response = ask_handler(event, None)
    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["answer"].startswith("Andrea Kimi Antonelli")
    assert "Who leads?" in seen["message"]
    assert "Andrea Kimi Antonelli" in seen["message"]
    assert "shell" not in seen["message"]
    assert "Patched Name" not in seen["message"]
    assert "Signed in as" not in seen["message"]
    assert seen["actor"] == "user-sub-123456789"


def test_ask_passes_the_verified_name_and_ignores_the_body(monkeypatch):
    seen = {}

    def invoke(message, session_id, actor_id):
        seen["message"] = message
        return "Hello Pat."

    monkeypatch.setenv("TABLE_NAME", "f1-whiteboard")
    monkeypatch.setenv("SEASON", "2026")
    monkeypatch.setenv("CAP_TABLE", "f1-whiteboard-ask")
    monkeypatch.setattr("f1board.ask_handler.charge_question", lambda table, user, day, limit=30: None)
    monkeypatch.setattr("f1board.ask_handler._cap_table", lambda: object())
    monkeypatch.setattr("f1board.ask_handler.load_items", lambda table_name, season: {
        "drivers": {"rows": []},
        "teams": {"rows": []},
        "schedule": {"rows": []},
        "results": {"rows": []},
        "meta": {"updatedAt": "2026-10-10T14:35:38Z"},
    })
    monkeypatch.setattr("f1board.ask_handler.invoke_harness", invoke)
    event = {
        "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "name-check-user", "name": "Pat\nTester"}}}},
        "body": json.dumps({"question": "Do you know my name?", "name": "Someone Else"}),
    }
    response = ask_handler(event, None)
    assert response["statusCode"] == 200
    assert seen["message"].startswith("Signed in as Pat Tester\n")
    assert "Someone Else" not in seen["message"]


def test_ask_rejects_a_missing_sign_in():
    response = ask_handler({"body": json.dumps({"question": "Who leads?"})}, None)
    assert response["statusCode"] == 401


def test_ask_accepts_a_verified_bearer(monkeypatch):
    seen = {}

    def invoke(message, session_id, actor_id):
        seen["actor"] = actor_id
        seen["message"] = message
        return "Andrea Kimi Antonelli leads with 320 points."

    monkeypatch.setenv("TABLE_NAME", "f1-whiteboard")
    monkeypatch.setenv("SEASON", "2026")
    monkeypatch.setenv("CAP_TABLE", "f1-whiteboard-ask")
    monkeypatch.setenv("COGNITO_ISSUER", "https://cognito-idp.us-east-2.amazonaws.com/pool")
    monkeypatch.setattr("f1board.ask_handler.charge_question", lambda table, user, day, limit=30: None)
    monkeypatch.setattr("f1board.ask_handler._cap_table", lambda: object())
    monkeypatch.setattr("f1board.ask_handler.load_items", lambda table_name, season: {
        "drivers": {"rows": []},
        "teams": {"rows": []},
        "schedule": {"rows": []},
        "results": {"rows": []},
        "meta": {"updatedAt": "2026-10-10T14:35:38Z"},
    })
    monkeypatch.setattr("f1board.ask_handler.invoke_harness", invoke)
    monkeypatch.setattr(
        "f1board.cognito_jwt.verify_id_token",
        lambda token: {"sub": "from-token", "email": "driver@example.com"} if token == "good" else (_ for _ in ()).throw(ValueError("bad")),
    )
    event = {"headers": {"authorization": "Bearer good"}, "body": json.dumps({"question": "Who leads?"})}
    response = ask_handler(event, None)
    assert response["statusCode"] == 200
    assert seen["actor"] == "from-token"
    assert seen["message"].startswith("Signed in as driver@example.com\n")


def test_ask_rejects_a_blank_question(monkeypatch):
    event = {
        "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "user"}}}},
        "body": json.dumps({"question": "  "}),
    }
    response = ask_handler(event, None)
    assert response["statusCode"] == 400


def test_read_returns_the_season_document(monkeypatch):
    table = FakeTable()
    table.items = {
        "drivers": {"season": "2026", "item": "drivers", "rows": [{"position": 1}]},
        "teams": {"season": "2026", "item": "teams", "rows": []},
        "schedule": {"season": "2026", "item": "schedule", "rows": []},
        "results": {"season": "2026", "item": "results", "rows": []},
        "meta": {"season": "2026", "item": "meta", "updatedAt": "2026-10-10T18:00:00Z", "source": "https://api.jolpi.ca/ergast/f1/2026/"},
    }
    monkeypatch.setenv("TABLE_NAME", "f1-whiteboard")
    monkeypatch.setenv("SEASON", "2026")
    monkeypatch.setattr("f1board.read_handler.load_items", lambda table_name, season: table.load_items(season))

    response = read_handler({}, None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert body["updatedAt"] == "2026-10-10T18:00:00Z"
    assert body["drivers"] == [{"position": 1}]
    assert "source" not in body


def test_season_tool_returns_the_same_document(monkeypatch):
    table = FakeTable()
    table.items = {
        "drivers": {"season": "2026", "item": "drivers", "rows": [{"position": 1, "points": Decimal("320")}]},
        "teams": {"season": "2026", "item": "teams", "rows": []},
        "schedule": {"season": "2026", "item": "schedule", "rows": []},
        "results": {"season": "2026", "item": "results", "rows": [{"round": 16, "grandPrix": [{"points": Decimal("25.5")}]}]},
        "meta": {"season": "2026", "item": "meta", "updatedAt": "2026-10-10T18:00:00Z", "source": "jolpica"},
    }
    monkeypatch.setenv("TABLE_NAME", "f1-whiteboard")
    monkeypatch.setenv("SEASON", "2026")
    monkeypatch.setattr("f1board.season_tool.load_items", lambda table_name, season: table.load_items(season))

    body = season_tool({}, None)

    assert set(body) == {"updatedAt", "drivers", "teams", "schedule", "results"}
    assert body["updatedAt"] == "2026-10-10T18:00:00Z"
    assert body["drivers"][0]["points"] == 320
    assert body["results"][0]["grandPrix"][0]["points"] == 25.5
    assert "source" not in body


def test_grounded_message_carries_the_stored_season():
    season = {"updatedAt": "2026-10-10T14:35:38Z", "drivers": [{"name": "Andrea Kimi Antonelli", "points": 320}], "teams": [], "schedule": [], "results": []}
    message = grounded_message("Who leads?", season)
    assert "Andrea Kimi Antonelli" in message
    assert "2026-10-10T14:35:38Z" in message
    assert message.endswith("Question: Who leads?")


def test_grounded_message_adds_a_verified_name():
    season = {"updatedAt": "2026-10-10T14:35:38Z", "drivers": [], "teams": [], "schedule": [], "results": []}
    message = grounded_message("Hello", season, "  Pat   Tester  ")
    assert message.startswith("Signed in as Pat Tester\n\nSeason JSON:\n")
    assert message.endswith("Question: Hello")
    unnamed = grounded_message("Hello", season)
    assert not unnamed.startswith("Signed in")


def test_ask_adds_web_text_for_a_news_question(monkeypatch):
    seen = {}

    def invoke(message, session_id, actor_id):
        seen["message"] = message
        return "The page mentions a practice note."

    monkeypatch.setattr("f1board.ask_handler.gather_web", lambda question, season=None: "Fetched https://example.com/news:\nPractice was dry.")
    monkeypatch.setattr("f1board.ask_handler.charge_question", lambda table, user, day, limit=30: None)
    monkeypatch.setattr("f1board.ask_handler._cap_table", lambda: object())
    monkeypatch.setattr("f1board.ask_handler.load_items", lambda table_name, season: {
        "drivers": {"rows": []},
        "teams": {"rows": []},
        "schedule": {"rows": []},
        "results": {"rows": []},
        "meta": {"updatedAt": "2026-10-10T14:35:38Z"},
    })
    monkeypatch.setattr("f1board.ask_handler.invoke_harness", invoke)
    monkeypatch.setenv("TABLE_NAME", "f1-whiteboard")
    monkeypatch.setenv("SEASON", "2026")
    monkeypatch.setenv("CAP_TABLE", "f1-whiteboard-ask")
    event = {
        "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "name-check-user"}}}},
        "body": json.dumps({"question": "What is the latest news?"}),
    }
    response = ask_handler(event, None)
    assert response["statusCode"] == 200
    assert "Web:" in seen["message"]
    assert "Practice was dry." in seen["message"]


def test_gather_web_skips_a_greeting_and_reads_a_pasted_page():
    def boom(question):
        raise AssertionError(question)

    assert gather_web("do you know my name?", fetch=boom, search=boom) == ""
    assert gather_web("Who leads?", fetch=boom, search=boom) == ""

    def fetch(url):
        assert url == "https://example.com/tires"
        return "Soft tires on Sunday."

    text = gather_web("See https://example.com/tires.", fetch=fetch, search=boom)
    assert "Soft tires on Sunday." in text
    assert "https://example.com/tires" in text


def test_gather_web_searches_when_the_board_cannot_answer():
    def fetch(url):
        raise AssertionError(url)

    def search(question):
        assert "latest" in question
        return [{"title": "Singapore sprint delayed by rain - Reuters", "url": "", "snippet": "Sat, 10 Oct 2026 12:53:17 GMT", "summary": ""}]

    text = gather_web("latest news from Singapore", fetch=fetch, search=search)
    assert "Reuters" in text
    assert "News:" in text


def test_a_sprint_winner_uses_the_official_results_page():
    seen = []

    def fetch(url):
        seen.append(url)
        return "1 Max Verstappen Red Bull Racing 8"

    def load_index():
        return '<a href="/en/results/2026/races/1296/singapore/race-result">Singapore</a>'

    season = {
        "schedule": [{"round": 17, "name": "Singapore Grand Prix", "date": "2026-10-11"}],
        "results": [{"round": 16, "name": "Bahrain Grand Prix"}],
    }
    text = gather_web(
        "who won the sprint race this morning?",
        fetch=fetch,
        search=lambda question: [],
        season=season,
        load_index=load_index,
    )
    assert seen == ["https://www.formula1.com/en/results/2026/races/1296/singapore/sprint-results"]
    assert "Max Verstappen" in text
    assert "Official results" in text


def test_fetch_page_keeps_the_results_table(monkeypatch):
    from f1board.web_fetch import fetch_page

    monkeypatch.setattr("f1board.web_fetch.check_url", lambda url: None)
    html = "<html><nav>" + ("Shop tickets " * 80) + "</nav><table><tr><td>1</td><td>Max Verstappen</td></tr></table></html>"

    class Response:
        headers = {"content-type": "text/html"}

        def read(self, limit):
            return html.encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    text = fetch_page("https://example.com/sprint", urlopen=lambda request, timeout=5: Response())
    assert text.startswith("1 Max Verstappen")
    assert "Shop tickets" not in text


def test_web_fetch_rejects_private_and_non_https_urls(monkeypatch):
    monkeypatch.setattr("f1board.web_fetch.socket.getaddrinfo", lambda host, port, type: [(None, None, None, None, ("127.0.0.1", port))])
    with pytest.raises(WebFetchError):
        check_url("https://metadata.example/latest/meta-data")
    with pytest.raises(WebFetchError):
        check_url("http://example.com/tires")
    with pytest.raises(WebFetchError):
        check_url("https://169.254.169.254/latest/meta-data")
    assert "Soft" in html_to_text("<style>x</style><p>Soft <b>tires</b></p>")


def test_grounded_message_refuses_an_incomplete_copy():
    with pytest.raises(SeasonIncomplete):
        grounded_message("Who leads?", {"error": "season incomplete"})


def test_season_tool_reports_an_incomplete_copy(monkeypatch):
    monkeypatch.setenv("TABLE_NAME", "f1-whiteboard")
    monkeypatch.setenv("SEASON", "2026")
    monkeypatch.setattr("f1board.season_tool.load_items", lambda table_name, season: {})

    assert season_tool({}, None) == {"error": "season incomplete"}


def test_read_errors_when_an_item_is_missing(monkeypatch):
    table = FakeTable()
    monkeypatch.setenv("TABLE_NAME", "f1-whiteboard")
    monkeypatch.setenv("SEASON", "2026")
    monkeypatch.setattr("f1board.read_handler.load_items", lambda table_name, season: table.load_items(season))

    response = read_handler({}, None)

    assert response["statusCode"] == 500
    assert json.loads(response["body"]) == {"error": "season incomplete"}


class _Body:
    def __init__(self, payload, status=200):
        self.status = status
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_fetch_json_invalid_body_raises_oserror(monkeypatch):
    monkeypatch.setattr(
        "f1board.http_client.urlopen",
        lambda url, timeout=20: _Body(b"not-json"),
    )

    with pytest.raises(OSError) as raised:
        fetch_json("https://api.jolpi.ca/ergast/f1/2026/driverstandings.json")

    assert not isinstance(raised.value, json.JSONDecodeError)


def test_marshall_stores_nested_half_points_as_decimal():
    marshalled = _marshall(
        {
            "season": "2026",
            "item": "drivers",
            "rows": [{"position": 1, "points": 25.5, "sprint": [12.5]}],
        }
    )

    row = marshalled["rows"]["L"][0]["M"]
    assert row["points"] == {"N": "25.5"}
    assert row["position"] == {"N": "1"}
    assert row["sprint"] == {"L": [{"N": "12.5"}]}


def test_read_encodes_dynamodb_decimals(monkeypatch):
    table = FakeTable()
    table.items = {
        "drivers": {
            "season": "2026",
            "item": "drivers",
            "rows": [{"position": Decimal("1"), "points": Decimal("25.5")}],
        },
        "teams": {"season": "2026", "item": "teams", "rows": [{"points": Decimal("10")}]},
        "schedule": {"season": "2026", "item": "schedule", "rows": []},
        "results": {"season": "2026", "item": "results", "rows": []},
        "meta": {
            "season": "2026",
            "item": "meta",
            "updatedAt": "2026-10-10T18:00:00Z",
            "source": "https://api.jolpi.ca/ergast/f1/2026/",
        },
    }
    monkeypatch.setenv("TABLE_NAME", "f1-whiteboard")
    monkeypatch.setenv("SEASON", "2026")
    monkeypatch.setattr("f1board.read_handler.load_items", lambda table_name, season: table.load_items(season))

    response = read_handler({}, None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert body["drivers"] == [{"position": 1, "points": 25.5}]
    assert body["teams"] == [{"points": 10}]
    assert '"points": 25.5' in response["body"]
    assert '"position": 1' in response["body"]
    assert '"points": 10' in response["body"]


def _ask_env(monkeypatch, charges, items=None):
    monkeypatch.setenv("TABLE_NAME", "f1-whiteboard")
    monkeypatch.setenv("SEASON", "2026")
    monkeypatch.setenv("CAP_TABLE", "f1-whiteboard-ask")
    monkeypatch.setattr("f1board.ask_handler.charge_question", lambda table, user, day, limit=30: charges.append("charge") or 4)
    monkeypatch.setattr("f1board.ask_handler.refund_question", lambda table, user, day: charges.append("refund"))
    monkeypatch.setattr("f1board.ask_handler._cap_table", lambda: object())
    full = {
        "drivers": {"rows": []},
        "teams": {"rows": []},
        "schedule": {"rows": []},
        "results": {"rows": []},
        "meta": {"updatedAt": "2026-10-10T14:35:38Z"},
    }
    monkeypatch.setattr("f1board.ask_handler.load_items", lambda table_name, season: full if items is None else items)


def _ask_event():
    return {
        "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "user-sub-123456789"}}}},
        "body": json.dumps({"question": "Who leads?"}),
    }


def test_ask_refunds_the_question_when_the_garage_fails(monkeypatch):
    charges = []
    _ask_env(monkeypatch, charges)
    monkeypatch.setattr("f1board.ask_handler.invoke_harness", lambda *args: (_ for _ in ()).throw(RuntimeError("down")))
    response = ask_handler(_ask_event(), None)
    assert response["statusCode"] == 502
    assert charges == ["charge", "refund"]


def test_ask_refunds_the_question_when_the_season_is_incomplete(monkeypatch):
    charges = []
    _ask_env(monkeypatch, charges, items={})
    response = ask_handler(_ask_event(), None)
    assert response["statusCode"] == 500
    assert charges == ["charge", "refund"]


def test_ask_reports_questions_left_on_success(monkeypatch):
    charges = []
    _ask_env(monkeypatch, charges)
    monkeypatch.setenv("DAILY_LIMIT", "30")
    monkeypatch.setattr("f1board.ask_handler.invoke_harness", lambda *args: "Kimi leads.")
    response = ask_handler(_ask_event(), None)
    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["remaining"] == 26
    assert charges == ["charge"]


def test_refund_question_never_goes_below_zero():
    from f1board.ask_handler import refund_question

    seen = {}

    class Table:
        def update_item(self, **kwargs):
            seen.update(kwargs)

    refund_question(Table(), "user", "2026-10-10")
    assert seen["Key"] == {"user": "user", "day": "2026-10-10"}
    assert "questions > :zero" in seen["ConditionExpression"]


def test_charge_question_returns_the_count_used():
    class Table:
        def update_item(self, **kwargs):
            assert kwargs["ReturnValues"] == "UPDATED_NEW"
            return {"Attributes": {"questions": 7}}

    assert charge_question(Table(), "user", "2026-10-10", limit=30) == 7


def test_grounded_message_asks_for_plain_answers_with_race_times():
    season = {"updatedAt": "2026-10-10T14:35:38Z", "drivers": [], "teams": [], "schedule": [], "results": []}
    message = grounded_message("When is the next race?", season, "Pat", "News:\n- a headline")
    guide, question = message.rsplit("\n\nQuestion: ", 1)
    assert question == "When is the next race?"
    assert "Write for a chat window" in guide
    assert "field names" in guide
    assert "UTC" in guide
    # the guide sits after the data it refers to
    assert guide.index("Season JSON:") < guide.index("Web:") < guide.index("Write for a chat window")


class ChatTable:
    def __init__(self, turns=None):
        self.item = {"turns": turns} if turns is not None else {}
        self.saved = None

    def get_item(self, Key):
        return {"Item": self.item} if self.item else {}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues):
        self.saved = (Key, ExpressionAttributeValues[":turns"])


class FakeClaude:
    def __init__(self, text="Kimi leads.", stop_reason="end_turn"):
        self.calls = []
        self.messages = self
        self._text = text
        self._stop = stop_reason

    def create(self, **kwargs):
        from types import SimpleNamespace

        self.calls.append(kwargs)
        block = SimpleNamespace(type="text", text=self._text)
        return SimpleNamespace(content=[block], stop_reason=self._stop)


def test_claude_gets_prior_turns_and_the_garage_prompt(monkeypatch):
    from f1board import ask_handler as module

    fake = FakeClaude()
    table = ChatTable([{"q": "Who leads?", "a": "Kimi."}])
    monkeypatch.setattr(module, "_claude_client", lambda: fake)
    answer = module.invoke_claude(table, "And second?", "GROUNDED And second?", "gsession", "user-1")
    call = fake.calls[0]
    assert answer == "Kimi leads."
    assert call["model"] == "claude-sonnet-5-5"
    assert call["system"].startswith("You are the 2026 Formula 1 garage expert")
    assert call["metadata"] == {"user_id": "user-1"}
    assert call["messages"] == [
        {"role": "user", "content": "Who leads?"},
        {"role": "assistant", "content": "Kimi."},
        {"role": "user", "content": "GROUNDED And second?"},
    ]
    key, turns = table.saved
    assert key == {"user": "user-1", "day": "chat#gsession"}
    assert turns[-1] == {"q": "And second?", "a": "Kimi leads."}
    assert len(turns) == 2


def test_claude_keeps_only_the_latest_turns(monkeypatch):
    from f1board import ask_handler as module

    monkeypatch.setattr(module, "_claude_client", lambda: FakeClaude())
    table = ChatTable([{"q": f"q{i}", "a": f"a{i}"} for i in range(10)])
    module.invoke_claude(table, "new", "new", "gsession", "user-1")
    assert len(table.saved[1]) == module.HISTORY_TURNS
    assert table.saved[1][-1]["q"] == "new"


def test_claude_refusal_or_empty_answer_is_an_error(monkeypatch):
    from f1board import ask_handler as module

    for fake in (FakeClaude(stop_reason="refusal"), FakeClaude(text="  ")):
        monkeypatch.setattr(module, "_claude_client", lambda fake=fake: fake)
        table = ChatTable()
        try:
            module.invoke_claude(table, "q", "q", "gsession", "user-1")
        except RuntimeError:
            pass
        else:
            raise AssertionError("expected RuntimeError")
        assert table.saved is None


def test_ask_model_uses_the_harness_until_a_secret_is_configured(monkeypatch):
    from f1board import ask_handler as module

    monkeypatch.setattr(module, "invoke_harness", lambda message, session, actor: "from harness")
    monkeypatch.setattr(module, "invoke_claude", lambda *args: "from claude")
    monkeypatch.delenv("ANTHROPIC_SECRET_ID", raising=False)
    assert module.ask_model(object(), "q", "m", "s", "a") == "from harness"
    monkeypatch.setenv("ANTHROPIC_SECRET_ID", "f1-whiteboard/anthropic-api-key")
    assert module.ask_model(object(), "q", "m", "s", "a") == "from claude"


class ScriptedClaude:
    """Returns the scripted responses in order; an Exception in the script is raised."""

    def __init__(self, *script):
        from types import SimpleNamespace

        self.ns = SimpleNamespace
        self.script = list(script)
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        text, stop = step
        return self.ns(content=[self.ns(type="text", text=text)], stop_reason=stop)


def test_claude_is_given_web_search_and_fetch(monkeypatch):
    from f1board import ask_handler as module

    fake = ScriptedClaude(("Answer.", "end_turn"))
    monkeypatch.setattr(module, "_claude_client", lambda: fake)
    module.invoke_claude(ChatTable(), "q", "q", "gsession", "user-1")
    names = [tool["name"] for tool in fake.calls[0]["tools"]]
    assert names == ["web_search", "web_fetch"]
    assert all(tool["max_uses"] == 3 for tool in fake.calls[0]["tools"])


def test_claude_resumes_a_paused_turn(monkeypatch):
    from f1board import ask_handler as module

    fake = ScriptedClaude(("partial", "pause_turn"), ("Final answer.", "end_turn"))
    monkeypatch.setattr(module, "_claude_client", lambda: fake)
    answer = module.invoke_claude(ChatTable(), "q", "q", "gsession", "user-1")
    assert answer == "Final answer."
    assert len(fake.calls) == 2
    assert fake.calls[1]["messages"][-1]["role"] == "assistant"


def test_claude_answers_without_the_web_when_the_tools_are_rejected(monkeypatch):
    import anthropic
    from f1board import ask_handler as module

    class Rejected(Exception):
        pass

    monkeypatch.setattr(anthropic, "BadRequestError", Rejected)
    fake = ScriptedClaude(Rejected("web search is not enabled"), ("From the board.", "end_turn"))
    monkeypatch.setattr(module, "_claude_client", lambda: fake)
    answer = module.invoke_claude(ChatTable(), "q", "q", "gsession", "user-1")
    assert answer == "From the board."
    assert "tools" in fake.calls[0] and "tools" not in fake.calls[1]


class FakeAgentCore:
    def __init__(self, records=None, fail=False):
        self.records = records or []
        self.fail = fail
        self.retrieved = None
        self.created = None

    def retrieve_memory_records(self, **kwargs):
        if self.fail:
            raise RuntimeError("down")
        self.retrieved = kwargs
        return {"memoryRecordSummaries": [{"content": {"text": text}} for text in self.records]}

    def create_event(self, **kwargs):
        if self.fail:
            raise RuntimeError("down")
        self.created = kwargs


def test_memory_is_off_until_a_memory_id_is_set(monkeypatch):
    from f1board import memory

    monkeypatch.delenv("AGENTCORE_MEMORY_ID", raising=False)
    monkeypatch.setattr(memory, "_agentcore", lambda: (_ for _ in ()).throw(AssertionError("must not be called")))
    assert memory.recall("user-1", "who leads?") == ""
    memory.record("user-1", "gsession", "q", "a")


def test_memory_recall_is_scoped_to_the_signed_in_person(monkeypatch):
    from f1board import memory

    fake = FakeAgentCore(["Favorite driver is Charles Leclerc", "Favorite driver is Charles Leclerc", "Asked about Ferrari last time"])
    monkeypatch.setenv("AGENTCORE_MEMORY_ID", "mem-123")
    monkeypatch.setattr(memory, "_agentcore", lambda: fake)
    block = memory.recall("user-1", "how is my driver doing?")
    assert fake.retrieved["namespacePath"] == "/actor/user-1/"
    assert fake.retrieved["memoryId"] == "mem-123"
    assert fake.retrieved["searchCriteria"]["searchQuery"] == "how is my driver doing?"
    assert block.count("Favorite driver is Charles Leclerc") == 1
    assert "- Asked about Ferrari last time" in block
    assert block.startswith("Memory (")


def test_memory_records_the_plain_question_and_answer(monkeypatch):
    from f1board import memory

    fake = FakeAgentCore()
    monkeypatch.setenv("AGENTCORE_MEMORY_ID", "mem-123")
    monkeypatch.setattr(memory, "_agentcore", lambda: fake)
    memory.record("user-1", "gsession", "My favorite driver is Leclerc", "Noted.")
    event = fake.created
    assert (event["actorId"], event["sessionId"]) == ("user-1", "gsession")
    assert [p["conversational"]["role"] for p in event["payload"]] == ["USER", "ASSISTANT"]
    assert event["payload"][0]["conversational"]["content"]["text"] == "My favorite driver is Leclerc"


def test_memory_failures_never_block_an_answer(monkeypatch):
    from f1board import memory
    from f1board import ask_handler as module

    monkeypatch.setenv("AGENTCORE_MEMORY_ID", "mem-123")
    monkeypatch.setattr(memory, "_agentcore", lambda: FakeAgentCore(fail=True))
    assert memory.recall("user-1", "q") == ""
    monkeypatch.setattr(module, "_claude_client", lambda: FakeClaude())
    assert module.invoke_claude(ChatTable(), "q", "q", "gsession", "user-1") == "Kimi leads."


def test_claude_sees_the_memory_block_before_the_question(monkeypatch):
    from f1board import ask_handler as module

    fake = FakeClaude()
    monkeypatch.setattr(module, "_claude_client", lambda: fake)
    monkeypatch.setattr(module.memory, "recall", lambda actor, question: "Memory (x):\n- Likes Ferrari\n\n")
    module.invoke_claude(ChatTable(), "How is my team?", "GROUNDED How is my team?", "gsession", "user-1")
    assert fake.calls[0]["messages"][-1]["content"].startswith("Memory (x):\n- Likes Ferrari\n\nGROUNDED")


def _invite_event(email, verified="true", question="Who leads?"):
    claims = {"sub": "invite-test-user"}
    if email is not None:
        claims["email"] = email
    if verified is not None:
        claims["email_verified"] = verified
    return {"requestContext": {"authorizer": {"jwt": {"claims": claims}}}, "body": json.dumps({"question": question})}


def _guard(monkeypatch, allowed):
    calls = []
    if allowed is None:
        monkeypatch.delenv("ALLOWED_EMAILS", raising=False)
    else:
        monkeypatch.setenv("ALLOWED_EMAILS", allowed)
    monkeypatch.setenv("TABLE_NAME", "f1-whiteboard")
    monkeypatch.setenv("SEASON", "2026")
    monkeypatch.setenv("CAP_TABLE", "f1-whiteboard-ask")
    monkeypatch.setattr("f1board.ask_handler._cap_table", lambda: SimpleNamespace(get_item=lambda **kwargs: {}))
    monkeypatch.setattr("f1board.ask_handler.charge_question", lambda *args, **kwargs: calls.append("charged"))
    monkeypatch.setattr("f1board.ask_handler.load_items", lambda *args: {
        "drivers": {"rows": []}, "teams": {"rows": []}, "schedule": {"rows": []}, "results": {"rows": []},
        "meta": {"updatedAt": "2026-10-10T14:35:38Z"},
    })
    monkeypatch.setattr("f1board.ask_handler.ask_model", lambda *args: calls.append("asked") or "Kimi.")
    monkeypatch.setattr("f1board.ask_handler.gather_web", lambda *args, **kwargs: "")
    return calls


def test_an_email_outside_the_allowlist_is_turned_away_before_any_charge(monkeypatch):
    calls = _guard(monkeypatch, "owner@example.com")
    response = ask_handler(_invite_event("someone@example.com"), None)
    assert response["statusCode"] == 403
    assert "invite-only" in json.loads(response["body"])["error"]
    assert calls == []


def test_an_unverified_or_missing_email_is_turned_away(monkeypatch):
    calls = _guard(monkeypatch, "owner@example.com")
    assert ask_handler(_invite_event("owner@example.com", verified="false"), None)["statusCode"] == 403
    assert ask_handler(_invite_event("owner@example.com", verified=None), None)["statusCode"] == 403
    assert ask_handler(_invite_event(None), None)["statusCode"] == 403
    assert calls == []


def test_the_listed_email_is_let_in_whatever_its_case(monkeypatch):
    calls = _guard(monkeypatch, " Owner@Example.com , other@example.com")
    response = ask_handler(_invite_event("owner@EXAMPLE.com"), None)
    assert response["statusCode"] == 200
    assert calls == ["charged", "asked"]


def test_without_an_allowlist_any_signed_in_user_may_ask(monkeypatch):
    calls = _guard(monkeypatch, None)
    assert ask_handler(_invite_event("anyone@example.com", verified=None), None)["statusCode"] == 200
    assert calls == ["charged", "asked"]


def test_a_missing_sign_in_is_still_a_401_with_an_allowlist(monkeypatch):
    _guard(monkeypatch, "owner@example.com")
    assert ask_handler({"body": json.dumps({"question": "Who leads?"})}, None)["statusCode"] == 401
