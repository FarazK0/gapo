"""Portfolio profiles backed by DynamoDB.

Table schema (PORTFOLIOS_TABLE):
  PK  user_id       (string)
  SK  portfolio_id  (string, UUID)

Gracefully no-ops when PORTFOLIOS_TABLE is unset so local dev and unit
tests that don't configure DynamoDB still work.
"""

import logging
import os
import uuid
from datetime import datetime, timezone

log = logging.getLogger("gapo.profiles")


def _table():
    table_name = os.environ.get("PORTFOLIOS_TABLE")
    if not table_name:
        return None
    import boto3
    return boto3.resource("dynamodb").Table(table_name)


def create_portfolio(user_id: str, name: str, tickers: list) -> dict:
    table = _table()
    if table is None:
        return {}
    portfolio_id = str(uuid.uuid4())
    item = {
        "user_id": user_id,
        "portfolio_id": portfolio_id,
        "name": name,
        "tickers": tickers,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    table.put_item(Item=item)
    return item


def get_portfolio(user_id: str, portfolio_id: str):
    table = _table()
    if table is None:
        return None
    resp = table.get_item(Key={"user_id": user_id, "portfolio_id": portfolio_id})
    return resp.get("Item")


def list_portfolios(user_id: str) -> list:
    table = _table()
    if table is None:
        return []
    from boto3.dynamodb.conditions import Key
    resp = table.query(KeyConditionExpression=Key("user_id").eq(user_id))
    return resp.get("Items", [])


def delete_portfolio(user_id: str, portfolio_id: str) -> None:
    table = _table()
    if table is None:
        return
    table.delete_item(Key={"user_id": user_id, "portfolio_id": portfolio_id})
