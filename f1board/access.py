import json
import os
import re
from datetime import datetime, timezone

from botocore.exceptions import ClientError

INDEX = "access#index"
META = "access#meta"
MAX_PENDING = 25
NOTICE_WINDOW_SECONDS = 15 * 60
REQUEST_TTL_DAYS = 90
EMAIL = re.compile(r"^[^@\s,]{1,64}@[^@\s,]{1,200}\.[^@\s,]{2,}$")
DECISIONS = {"approve": "approved", "deny": "denied"}


class TooManyRequests(Exception):
    pass


def _listed(name):
    return {item.strip().lower() for item in os.environ.get(name, "").split(",") if item.strip()}


def gating_on():
    return bool(_listed("ALLOWED_EMAILS") or _listed("ADMIN_EMAILS"))


def verified_email(claims):
    """The Google email, only when Google says it is verified."""
    if str(claims.get("email_verified")).strip().lower() != "true":
        return ""
    email = str(claims.get("email") or "").strip().lower()
    return email if EMAIL.match(email) else ""


def is_admin(claims):
    email = verified_email(claims)
    return bool(email) and email in _listed("ADMIN_EMAILS")


def status_of(table, email):
    try:
        item = table.get_item(Key={"user": INDEX, "day": email}).get("Item") or {}
    except ClientError:
        return "none"
    status = item.get("status")
    return status if status in {"pending", "approved", "denied"} else "none"


def status_for(table, claims):
    """approved | pending | denied | none for the signed-in person. Fixed lists always win."""
    email = verified_email(claims)
    if not email:
        return "none"
    if email in _listed("ALLOWED_EMAILS") or email in _listed("ADMIN_EMAILS"):
        return "approved"
    return status_of(table, email)


def is_allowed(table, claims):
    """With no lists configured the chat is open. Otherwise only approved, verified emails may ask."""
    if not gating_on():
        return True
    return status_for(table, claims) == "approved"


def _now(now=None):
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch(now=None, days=0, seconds=0):
    moment = now or datetime.now(timezone.utc)
    return int(moment.timestamp()) + days * 86400 + seconds


def _notice_due(table, now=None):
    """At most one email per window, so a flood of requests cannot flood the owner's inbox."""
    stamp = _epoch(now)
    try:
        last = table.get_item(Key={"user": META, "day": "last-notice"}).get("Item") or {}
        if stamp - int(last.get("at", 0)) < NOTICE_WINDOW_SECONDS:
            return False
        table.put_item(Item={"user": META, "day": "last-notice", "at": stamp, "expiresAt": stamp + 86400})
    except ClientError:
        return False
    return True


def request_access(table, claims, notify=None, now=None):
    """Record one pending request per verified email and tell the owner once."""
    email = verified_email(claims)
    if not email:
        raise ValueError("no verified email")
    current = status_for(table, claims)
    if current != "none":
        return current
    name = " ".join(str(claims.get("name") or "").split())[:80]
    pending = sum(1 for row in list_requests(table) if row["status"] == "pending")
    if pending >= MAX_PENDING:
        raise TooManyRequests("too many pending requests")
    try:
        table.put_item(
            Item={"user": INDEX, "day": email, "status": "pending", "name": name, "requestedAt": _now(now), "expiresAt": _epoch(now, days=REQUEST_TTL_DAYS)},
            ConditionExpression="attribute_not_exists(#u)",
            ExpressionAttributeNames={"#u": "user"},
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return status_of(table, email)
        raise
    if notify and _notice_due(table, now):
        try:
            notify(email, name, pending + 1)
        except Exception as exc:
            print(json.dumps({"access_notify_error": type(exc).__name__}))
    return "pending"


def list_requests(table):
    from boto3.dynamodb.conditions import Key

    rows = []
    for item in table.query(KeyConditionExpression=Key("user").eq(INDEX)).get("Items", []):
        rows.append(
            {
                "email": item["day"],
                "status": item.get("status", "none"),
                "name": item.get("name", ""),
                "requestedAt": item.get("requestedAt", ""),
                "decidedAt": item.get("decidedAt", ""),
            }
        )
    order = {"pending": 0, "approved": 1, "denied": 2}
    rows.sort(key=lambda row: row["requestedAt"], reverse=True)
    rows.sort(key=lambda row: order.get(row["status"], 3))
    return rows


def decide(table, admin_claims, email, decision, now=None):
    """Approve or deny one email. Works for people who never asked, so the owner can add someone."""
    if decision not in DECISIONS:
        raise ValueError("decision must be approve or deny")
    email = str(email or "").strip().lower()
    if not EMAIL.match(email):
        raise ValueError("not an email address")
    try:
        existing = table.get_item(Key={"user": INDEX, "day": email}).get("Item") or {}
    except ClientError:
        existing = {}
    item = {
        "user": INDEX,
        "day": email,
        "status": DECISIONS[decision],
        "name": existing.get("name", ""),
        "requestedAt": existing.get("requestedAt") or _now(now),
        "decidedAt": _now(now),
        "decidedBy": verified_email(admin_claims),
    }
    if decision == "deny":
        item["expiresAt"] = _epoch(now, days=REQUEST_TTL_DAYS)  # approved entries never expire
    table.put_item(Item=item)
    return item


def sns_notifier(topic_arn, site_url):
    def notify(email, name, pending=None):
        import boto3

        who = f"{name} <{email}>" if name else email
        more = f" {pending} request(s) are waiting." if pending and pending > 1 else ""
        boto3.client("sns").publish(
            TopicArn=topic_arn,
            Subject="Pit Wall: new access request",
            Message=f"{who} asked for chat access to Pit Wall.{more}\n\n" + (f"Sign in at {site_url} and approve" if site_url else "Sign in and approve") + " or deny in the Access requests panel. You get at most one of these emails every 15 minutes.",
        )

    return notify
