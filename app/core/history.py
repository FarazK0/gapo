"""Prediction history backed by DynamoDB.

Table schema (HISTORY_TABLE):
  PK  pk      "{user_id}#{portfolio_id}"  (string)
  SK  as_of   ISO date string             (string, for chronological ordering)

Gracefully no-ops when HISTORY_TABLE is unset so local dev and unit
tests that don't configure DynamoDB still work.
"""

import logging
import os
from datetime import datetime, timedelta, timezone

import torch

log = logging.getLogger("gapo.history")


def _table():
    table_name = os.environ.get("HISTORY_TABLE")
    if not table_name:
        return None
    import boto3
    return boto3.resource("dynamodb").Table(table_name)


def save_prediction(
    user_id: str,
    portfolio_id: str,
    as_of: str,
    weights,
    returns,
) -> None:
    """Persist a prediction outcome. Best-effort — never raises."""
    table = _table()
    if table is None:
        return
    try:
        if isinstance(weights, torch.Tensor):
            weights_list = [str(float(w)) for w in weights.reshape(-1)]
        else:
            weights_list = [str(float(w)) for w in weights]

        if isinstance(returns, torch.Tensor):
            returns_list = [str(float(r)) for r in returns.reshape(-1)]
        else:
            returns_list = [str(float(r)) for r in returns]

        expires = datetime.now(timezone.utc) + timedelta(days=180)
        table.put_item(
            Item={
                "pk": f"{user_id}#{portfolio_id}",
                "as_of": as_of,
                "weights": weights_list,
                "returns": returns_list,
                "expires_at": int(expires.timestamp()),
            }
        )
    except Exception:
        log.warning("history save_prediction failed", exc_info=True)


def get_history(user_id: str, portfolio_id: str, n: int = 8) -> list:
    """Return the n most-recent (weights_tensor, returns_tensor) pairs, oldest first."""
    table = _table()
    if table is None:
        return []
    try:
        from boto3.dynamodb.conditions import Key

        resp = table.query(
            KeyConditionExpression=Key("pk").eq(f"{user_id}#{portfolio_id}"),
            ScanIndexForward=False,  # newest first
            Limit=n,
        )
        items = list(reversed(resp.get("Items", [])))
        result = []
        for item in items:
            w = torch.tensor([float(v) for v in item["weights"]], dtype=torch.float32)
            r = torch.tensor([float(v) for v in item["returns"]], dtype=torch.float32)
            result.append((w, r))
        return result
    except Exception:
        log.warning("history get_history failed", exc_info=True)
        return []
