"""Market data cache.

Layout in S3:

    snapshots/<YYYY-MM-DD>/<TICKER>.parquet    weekly bars per ticker
    current.json                                pointer to the newest good snapshot

Layout on disk (STORAGE_BACKEND=local):

    data/snapshots/<YYYY-MM-DD>/<TICKER>.parquet
    data/current.json

The pointer indirection is what makes the daily job safe to fail. Ingest
writes a whole snapshot before flipping the pointer, so a partial or
broken run never becomes the snapshot that inference reads. This is the
same promote-on-success idea as the model version table, applied to data.
"""

import io
import json
import os
import pathlib
from datetime import date, datetime

import pandas as pd

POINTER_KEY = "current.json"
MAX_STALENESS_DAYS = 10  # covers a long weekend plus a failed run or two

_s3 = None


def _is_local() -> bool:
    return os.environ.get("STORAGE_BACKEND") == "local"


def _data_dir() -> pathlib.Path:
    # app/core/store.py -> parent.parent = app/ -> data/
    return pathlib.Path(__file__).parent.parent / "data"


def _get_s3():
    global _s3
    if _s3 is None:
        import boto3
        _s3 = boto3.client("s3")
    return _s3


def _bucket() -> str:
    return os.environ["DATA_BUCKET"]


class StaleDataError(RuntimeError):
    pass


class MissingDataError(RuntimeError):
    pass


def read_pointer() -> dict:
    if _is_local():
        path = _data_dir() / POINTER_KEY
        if not path.exists():
            raise StaleDataError(
                "No market data has been ingested yet. Run the ingest function once."
            )
        return json.loads(path.read_text())

    s3 = _get_s3()
    try:
        obj = s3.get_object(Bucket=_bucket(), Key=POINTER_KEY)
    except s3.exceptions.NoSuchKey:
        raise StaleDataError(
            "No market data has been ingested yet. Run the ingest function once."
        )
    return json.loads(obj["Body"].read())


def write_pointer(snapshot_date: str, tickers: list) -> None:
    data = json.dumps(
        {
            "snapshot": snapshot_date,
            "tickers": sorted(tickers),
            "written_at": datetime.utcnow().isoformat() + "Z",
        }
    )

    if _is_local():
        path = _data_dir() / POINTER_KEY
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data)
        return

    _get_s3().put_object(
        Bucket=_bucket(),
        Key=POINTER_KEY,
        Body=data.encode(),
        ContentType="application/json",
    )


def assert_fresh(pointer: dict) -> str:
    snapshot = pointer["snapshot"]
    age = (date.today() - date.fromisoformat(snapshot)).days
    if age > MAX_STALENESS_DAYS:
        raise StaleDataError(
            f"Market data is {age} days old (snapshot {snapshot}). "
            "Refusing to produce allocations from stale prices."
        )
    return snapshot


def load_weekly(snapshot: str, ticker: str) -> pd.DataFrame:
    if _is_local():
        path = _data_dir() / "snapshots" / snapshot / f"{ticker}.parquet"
        if not path.exists():
            raise MissingDataError(f"No cached data for {ticker} in snapshot {snapshot}.")
        return pd.read_parquet(path)

    s3 = _get_s3()
    key = f"snapshots/{snapshot}/{ticker}.parquet"
    try:
        obj = s3.get_object(Bucket=_bucket(), Key=key)
    except s3.exceptions.NoSuchKey:
        raise MissingDataError(f"No cached data for {ticker} in snapshot {snapshot}.")
    return pd.read_parquet(io.BytesIO(obj["Body"].read()))


def save_weekly(snapshot: str, ticker: str, frame: pd.DataFrame) -> None:
    buf = io.BytesIO()
    frame.to_parquet(buf, compression="snappy")
    _get_s3().put_object(
        Bucket=_bucket(),
        Key=f"snapshots/{snapshot}/{ticker}.parquet",
        Body=buf.getvalue(),
    )
