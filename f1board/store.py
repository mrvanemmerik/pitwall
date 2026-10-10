from decimal import Decimal

import boto3


def write_items(table_name, items):
    client = boto3.client("dynamodb")
    client.transact_write_items(
        TransactItems=[
            {
                "Put": {
                    "TableName": table_name,
                    "Item": _marshall(item),
                }
            }
            for item in items.values()
        ]
    )


def write_item(table_name, item):
    boto3.client("dynamodb").put_item(TableName=table_name, Item=_marshall(item))


def load_recaps(table_name, season):
    response = boto3.resource("dynamodb").Table(table_name).get_item(Key={"season": season, "item": "recaps"})
    rows = (response.get("Item") or {}).get("rows")
    return _plain(rows) if isinstance(rows, list) else []


def load_items(table_name, season):
    table = boto3.resource("dynamodb").Table(table_name)
    found = {}
    for key in ("drivers", "teams", "schedule", "results", "meta", "recaps"):
        response = table.get_item(Key={"season": season, "item": key})
        if "Item" in response:
            found[key] = response["Item"]
    return found


def _marshall(item):
    from boto3.dynamodb.types import TypeSerializer

    serializer = TypeSerializer()
    prepared = _decimals(item)
    return {key: serializer.serialize(value) for key, value in prepared.items()}


def _plain(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _decimals(value):
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {key: _decimals(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decimals(item) for item in value]
    return value
