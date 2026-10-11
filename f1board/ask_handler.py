import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.eventstream import EventStreamBuffer
from botocore.exceptions import ClientError
from botocore.session import Session

from f1board import access, memory, refresh
from f1board.season_tool import GARAGE_SYSTEM, grounded_message
from f1board.web_fetch import gather_web
from f1board.season_view import SeasonIncomplete, season_view
from f1board.store import load_items

HEADERS = {"content-type": "application/json"}
DAILY_LIMIT = 30
CLAUDE_MODEL = "claude-sonnet-5-5"
HISTORY_TURNS = 6
CHAT_TTL_DAYS = 30
WEB_TOOLS = [
    {"type": "web_search_20250305", "name": "web_search", "max_uses": 3},
    {"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": 3},
]
MAX_RESUMES = 1
RESUME_BEFORE = 45.0


class CapReached(Exception):
    pass


class AnswerFailed(RuntimeError):
    """The model was called but gave no usable answer. The question is not handed back."""


def charge_question(table, user, day, limit=DAILY_LIMIT):
    try:
        response = table.update_item(
            Key={"user": user, "day": day},
            UpdateExpression="SET expiresAt = :exp ADD questions :one",
            ConditionExpression="attribute_not_exists(questions) OR questions < :limit",
            ExpressionAttributeValues={":one": 1, ":limit": limit, ":exp": int(time.time()) + 3 * 86400},
            ReturnValues="UPDATED_NEW",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise CapReached() from exc
        raise
    used = ((response or {}).get("Attributes") or {}).get("questions")
    return int(used) if used is not None else None


def refund_question(table, user, day):
    """Give a question back when the garage could not answer it."""
    try:
        table.update_item(
            Key={"user": user, "day": day},
            UpdateExpression="ADD questions :minus",
            ConditionExpression="questions > :zero",
            ExpressionAttributeValues={":minus": -1, ":zero": 0},
        )
    except ClientError:
        pass


def runtime_session(sub, requested):
    suffix = re.sub(r"[^A-Za-z0-9_-]", "", str(requested or ""))
    base = "g" + re.sub(r"[^A-Za-z0-9]", "", sub)[:16] + suffix
    if len(base) < 33:
        base = (base + ("0" * 33))[:33]
    return base[:100]


def question_from(event):
    raw = event.get("body") or "{}"
    body = json.loads(raw)
    if not isinstance(body, dict):
        raise ValueError("bad question")
    question = str(body.get("question") or "").strip()
    if not question or len(question) > 400:
        raise ValueError("bad question")
    return question, body.get("sessionId")


def invoke_harness(message, session_id, actor_id):
    arn = os.environ["HARNESS_ARN"]
    region = os.environ.get("AWS_REGION", "us-east-2")
    url = f"https://bedrock-agentcore.{region}.amazonaws.com/harnesses/invoke?" + urllib.parse.urlencode({"harnessArn": arn})
    data = json.dumps({
        "actorId": actor_id,
        "messages": [{"role": "user", "content": [{"text": message}]}],
    }).encode()
    creds = Session().get_credentials().get_frozen_credentials()
    req = AWSRequest(method="POST", url=url, data=data, headers={
        "Content-Type": "application/json",
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
    })
    SigV4Auth(creds, "bedrock-agentcore", region).add_auth(req)
    prepared = req.prepare()
    http_req = urllib.request.Request(url, data=data, method="POST")
    for key, value in prepared.headers.items():
        http_req.add_header(key, value)
    with urllib.request.urlopen(http_req, timeout=50) as resp:
        raw = resp.read()
    buf = EventStreamBuffer()
    buf.add_data(raw)
    parts = []
    for event in buf:
        headers = {key: value.decode() if isinstance(value, bytes) else value for key, value in event.headers.items()}
        payload = json.loads(event.payload.decode() or "{}")
        if headers.get(":event-type") == "contentBlockDelta" and "text" in payload.get("delta", {}):
            parts.append(payload["delta"]["text"])
    answer = "".join(parts).strip()
    if not answer:
        raise RuntimeError("empty answer")
    return answer


_client = None


def _claude_client():
    """One client per warm Lambda; the key is read from Secrets Manager once."""
    global _client
    if _client is None:
        import anthropic
        import boto3

        secret = boto3.client("secretsmanager").get_secret_value(SecretId=os.environ["ANTHROPIC_SECRET_ID"])
        _client = anthropic.Anthropic(api_key=secret["SecretString"].strip(), timeout=40.0, max_retries=0)
    return _client


def load_turns(table, key):
    try:
        item = table.get_item(Key=key).get("Item") or {}
    except ClientError:
        return []
    turns = item.get("turns")
    if not isinstance(turns, list):
        return []
    return [t for t in turns if isinstance(t, dict) and isinstance(t.get("q"), str) and isinstance(t.get("a"), str)]


def save_turns(table, key, turns):
    try:
        table.update_item(
            Key=key,
            UpdateExpression="SET turns = :turns, expiresAt = :exp",
            ExpressionAttributeValues={":turns": turns, ":exp": int(time.time()) + CHAT_TTL_DAYS * 86400},
        )
    except ClientError:
        pass


def _ask_with_web(messages, actor_id):
    """Claude may search and fetch pages itself. Without web access it still answers from the board."""
    import anthropic

    client = _claude_client()
    options = {
        "model": os.environ.get("CLAUDE_MODEL", CLAUDE_MODEL),
        "max_tokens": 4000,
        "system": GARAGE_SYSTEM,
        "output_config": {"effort": "low"},
        "metadata": {"user_id": actor_id},
    }
    started = time.monotonic()
    tools = WEB_TOOLS
    messages = list(messages)
    for attempt in range(MAX_RESUMES + 2):
        try:
            response = client.messages.create(messages=messages, tools=tools, **options) if tools else client.messages.create(messages=messages, **options)
        except anthropic.BadRequestError:
            if not tools:
                raise
            tools = None
            continue
        if response.stop_reason != "pause_turn" or time.monotonic() - started > RESUME_BEFORE:
            return response, bool(tools)
        messages = messages + [{"role": "assistant", "content": response.content}]
    return response, bool(tools)


def _tool_errors(response):
    codes = []
    for block in response.content:
        if not str(getattr(block, "type", "")).endswith("_tool_result"):
            continue
        content = getattr(block, "content", None)
        code = content.get("error_code") if isinstance(content, dict) else getattr(content, "error_code", None)
        if code:
            codes.append(str(code))
    return codes


def invoke_claude(table, question, message, session_id, actor_id):
    """Ask Claude directly. Prior turns are only the plain questions and answers, so the season JSON is sent once."""
    key = {"user": actor_id, "day": "chat#" + session_id}
    turns = load_turns(table, key)
    message = memory.recall(actor_id, question) + message
    messages = []
    for turn in turns:
        messages.append({"role": "user", "content": turn["q"]})
        messages.append({"role": "assistant", "content": turn["a"]})
    messages.append({"role": "user", "content": message})
    response, web = _ask_with_web(messages, actor_id)
    used = sum(1 for block in response.content if getattr(block, "type", "") == "server_tool_use")
    print(json.dumps({"model": getattr(response, "model", None), "web_tools": web, "web_calls": used, "web_errors": _tool_errors(response), "stop": response.stop_reason}))
    if response.stop_reason == "refusal":
        raise AnswerFailed("declined")
    answer = "".join(block.text for block in response.content if block.type == "text").strip()
    if not answer:
        raise AnswerFailed("empty answer")
    save_turns(table, key, (turns + [{"q": question, "a": answer[:1500]}])[-HISTORY_TURNS:])
    memory.record(actor_id, session_id, question, answer)
    return answer


def ask_model(table, question, message, session_id, actor_id):
    """Claude when a key is configured; otherwise the AgentCore harness."""
    if os.environ.get("ANTHROPIC_SECRET_ID"):
        return invoke_claude(table, question, message, session_id, actor_id)
    return invoke_harness(message, session_id, actor_id)


def caller(event):
    claims = _claims(event)
    sub = claims.get("sub") if isinstance(claims, dict) else None
    if not isinstance(sub, str) or not sub:
        return None, None, {}
    return sub, _signed_in_name(claims), claims


def _claims(event):
    claims = ((event.get("requestContext") or {}).get("authorizer") or {}).get("jwt", {}).get("claims") or {}
    if isinstance(claims, dict) and claims.get("sub"):
        return claims
    token = _bearer(event)
    if not token or not os.environ.get("COGNITO_ISSUER"):
        return {}
    try:
        from f1board.cognito_jwt import verify_id_token

        verified = verify_id_token(token)
    except Exception:
        return {}
    return verified if isinstance(verified, dict) else {}


def _signed_in_name(claims):
    for key in ("name", "email"):
        value = claims.get(key)
        if not isinstance(value, str):
            continue
        cleaned = " ".join(value.split())
        if cleaned:
            return cleaned[:80]
    return None


def _bearer(event):
    headers = {str(key).lower(): value for key, value in (event.get("headers") or {}).items()}
    auth = str(headers.get("authorization") or "")
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    return ""


def _path(event):
    path = event.get("rawPath") or ((event.get("requestContext") or {}).get("http") or {}).get("path") or "/"
    return "/" + str(path).strip("/")


def _json_body(event):
    try:
        body = json.loads(event.get("body") or "{}")
    except (TypeError, ValueError):
        return {}
    return body if isinstance(body, dict) else {}


def _notifier():
    topic = os.environ.get("ACCESS_TOPIC_ARN")
    if not topic:
        return None
    return access.sns_notifier(topic, os.environ.get("SITE_URL", ""))


def _access_route(path, event, claims, table):
    if path == "/access/status":
        return _response(200, {"status": access.status_for(table, claims), "admin": access.is_admin(claims), "email": access.verified_email(claims)})
    if path == "/access/request":
        try:
            return _response(200, {"status": access.request_access(table, claims, _notifier())})
        except ValueError:
            return _response(400, {"error": "Google has not verified your email address."})
        except access.TooManyRequests:
            return _response(429, {"error": "Too many access requests are waiting right now. Try again later."})
    if not access.is_admin(claims):
        return _response(403, {"error": "admins only"})
    if path == "/admin/refresh":
        return _refresh_route(table)
    if path == "/admin/decide":
        body = _json_body(event)
        try:
            access.decide(table, claims, body.get("email"), body.get("decision"))
        except ValueError as exc:
            return _response(400, {"error": str(exc)})
    return _response(200, {"requests": access.list_requests(table)})


def _refresh_route(table):
    function_name = os.environ.get("INGEST_FUNCTION")
    if not function_name:
        return _response(503, {"error": "refresh is not set up"})
    try:
        refresh.start(table, _lambda_client(), function_name)
    except refresh.TooSoon as exc:
        return _response(429, {"error": "a refresh ran a moment ago", "retryAfter": exc.retry_after})
    except Exception as exc:
        print(json.dumps({"refresh_error": type(exc).__name__, "detail": str(exc)[:200]}))
        return _response(502, {"error": "could not start the refresh"})
    return _response(202, {"started": True})


def handler(event, context):
    sub, name, claims = caller(event)
    if not sub:
        return _response(401, {"error": "sign in again"})
    path = _path(event)
    if path in {"/access/status", "/access/request", "/admin/requests", "/admin/decide", "/admin/refresh"}:
        return _access_route(path, event, claims, _cap_table())
    try:
        question, session_id = question_from(event)
    except (ValueError, json.JSONDecodeError):
        return _response(400, {"error": "ask a shorter question"})
    table = _cap_table()
    if not access.is_allowed(table, claims):
        return _response(403, {"error": "Pit Wall chat is invite-only for now.", "access": access.status_for(table, claims)})
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    limit = int(os.environ.get("DAILY_LIMIT", DAILY_LIMIT))
    try:
        used = charge_question(table, sub, day, limit)
    except CapReached:
        return _response(429, {"error": "come back tomorrow", "remaining": 0})
    try:
        season = season_view(load_items(os.environ["TABLE_NAME"], os.environ["SEASON"]))
    except SeasonIncomplete:
        refund_question(table, sub, day)
        return _response(500, {"error": "season incomplete"})
    try:
        answer = ask_model(
            table,
            question,
            grounded_message(question, season, name, gather_web(question, season=season)),
            runtime_session(sub, session_id),
            sub,
        )
    except AnswerFailed:
        return _response(502, {"error": "could not ask the garage"})
    except Exception:
        refund_question(table, sub, day)
        return _response(502, {"error": "could not ask the garage"})
    body = {"answer": answer}
    if isinstance(used, int):
        body["remaining"] = max(limit - used, 0)
    return _response(200, body)


def _lambda_client():
    import boto3

    return boto3.client("lambda")


def _cap_table():
    import boto3

    return boto3.resource("dynamodb").Table(os.environ["CAP_TABLE"])


def _response(status, body):
    return {"statusCode": status, "headers": HEADERS, "body": json.dumps(body)}
