"""Lambda entrypoints.

Two handlers share one image:

    handler.predict   invoked through CloudFront, returns portfolio weights
    handler.ingest    invoked on a schedule, refreshes the market data cache

Neither contains business logic. Both are thin adapters over core/, so the
same code can be exercised from a test or a notebook without Lambda.
"""

import json
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

logging.basicConfig()
log = logging.getLogger("gapo")
log.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

UNIVERSE = [t for t in os.environ.get("UNIVERSE", "").split(",") if t]
MIN_ASSETS = 5
MAX_ASSETS = 25

# Imported at module scope on purpose. Lambda gives the init phase full
# CPU regardless of the configured memory, so importing torch and loading
# the checkpoint here is meaningfully cheaper than doing it in the handler.
from core import bundle, history as history_mod, predictor, profiles  # noqa: E402

bundle.prime()

# Flipped to False after the first invocation. Lets a load test
# separate cold-start latency from steady-state latency without
# guessing from the shape of the histogram.
_COLD = True
_INIT_COMPLETE = time.perf_counter()


def _response(status, body, cold=False):
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "cache-control": "no-store",
            "x-content-type-options": "nosniff",
            "x-cold-start": "1" if cold else "0",
        },
        "body": json.dumps(body),
    }


def _parse_tickers(payload):
    """Validate the requested basket. Reject early and specifically."""
    tickers = payload.get("tickers")
    if not isinstance(tickers, list):
        raise ValueError("Field 'tickers' must be a list.")

    cleaned = []
    for t in tickers:
        if not isinstance(t, str):
            raise ValueError("Every ticker must be a string.")
        t = t.strip().upper()
        if not t or len(t) > 8 or not all(c.isalnum() or c in "-." for c in t):
            raise ValueError(f"Ticker '{t}' is not a valid symbol.")
        cleaned.append(t)

    if len(set(cleaned)) != len(cleaned):
        raise ValueError("Duplicate tickers are not allowed.")
    if not MIN_ASSETS <= len(cleaned) <= MAX_ASSETS:
        raise ValueError(f"Select between {MIN_ASSETS} and {MAX_ASSETS} tickers.")

    unknown = [t for t in cleaned if UNIVERSE and t not in UNIVERSE]
    if unknown:
        raise ValueError(
            f"Not in the supported universe: {', '.join(unknown)}. "
            "Only cached tickers can be priced."
        )
    return cleaned


def predict(event, context):
    # The warmer rule invokes with this payload. Returning immediately
    # keeps the container alive without touching S3 or DynamoDB.
    global _COLD
    was_cold, _COLD = _COLD, False

    if event.get("warmup"):
        return {"warm": True, "was_cold": was_cold}

    started = time.perf_counter()

    try:
        body = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            import base64

            body = base64.b64decode(body).decode()
        payload = json.loads(body)
    except (ValueError, TypeError):
        return _response(400, {"error": "Request body is not valid JSON."}, was_cold)

    portfolio_id = payload.get("portfolio_id")
    user_id = payload.get("user_id")

    if portfolio_id and user_id:
        portfolio = profiles.get_portfolio(str(user_id), str(portfolio_id))
        if portfolio is None:
            return _response(404, {"error": "Portfolio not found."}, was_cold)
        tickers = portfolio["tickers"]
        hist = history_mod.get_history(str(user_id), str(portfolio_id))
        used_history = bool(hist)
    else:
        try:
            tickers = _parse_tickers(payload)
        except ValueError as exc:
            return _response(400, {"error": str(exc)}, was_cold)
        hist = None
        used_history = False

    try:
        result = predictor.run(tickers, history=hist)
    except predictor.StaleDataError as exc:
        # Serve nothing rather than something wrong. A portfolio weight
        # computed from a three week old snapshot is worse than an error.
        return _response(503, {"error": str(exc)}, was_cold)
    except predictor.MissingDataError as exc:
        return _response(422, {"error": str(exc)}, was_cold)
    except Exception:
        log.exception("prediction failed")
        return _response(
            500, {"error": "Prediction failed. The error has been logged."}, was_cold
        )

    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)

    record = {
        "run_id": str(uuid.uuid4()),
        "tickers": tickers,
        "weights": result["weights"],
        "as_of": result["as_of"],
        "model_sha256": predictor.MODEL_SHA[:12],
        "elapsed_ms": elapsed_ms,
        "used_history": used_history,
    }

    if portfolio_id and user_id:
        weights_list = [result["weights"][t] for t in tickers]
        returns_list = [0.0] * len(tickers)
        history_mod.save_prediction(
            str(user_id), str(portfolio_id), result["as_of"],
            weights_list, returns_list,
        )
    else:
        _save_history(payload.get("session_id"), record)

    # Structured so CloudWatch Logs Insights can aggregate it directly.
    log.info(json.dumps({
        "event": "predict", "n": len(tickers),
        "ms": elapsed_ms, "cold": was_cold,
        "portfolio": bool(portfolio_id),
    }))
    return _response(200, record, was_cold)


def _save_history(session_id, record):
    """Best effort. A history write failure must not fail the prediction."""
    table_name = os.environ.get("HISTORY_TABLE")
    if not table_name or not session_id:
        return
    try:
        import boto3

        table = boto3.resource("dynamodb").Table(table_name)
        expires = datetime.now(timezone.utc) + timedelta(days=180)
        table.put_item(
            Item={
                "user_id": str(session_id)[:128],
                "created_at": datetime.now(timezone.utc).isoformat(),
                "run_id": record["run_id"],
                "tickers": record["tickers"],
                "weights": {k: str(v) for k, v in record["weights"].items()},
                "as_of": record["as_of"],
                "expires_at": int(expires.timestamp()),
            }
        )
    except Exception:
        log.warning("history write failed", exc_info=True)


def ingest(event, context):
    """Refresh the market data cache. Never called by a user."""
    from core import ingest as ingest_mod

    summary = ingest_mod.refresh(UNIVERSE)
    log.info(json.dumps({"event": "ingest", **summary}))

    # Raising makes the Lambda Errors metric fire, which drives the alarm.
    if summary["failed"] > len(UNIVERSE) * 0.2:
        raise RuntimeError(
            f"Ingest degraded: {summary['failed']} of {len(UNIVERSE)} tickers failed."
        )
    return summary
