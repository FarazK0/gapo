"""Daily market data refresh.

Runs on a schedule, never in a request path. Writes a complete snapshot
then flips the pointer, so inference never observes a partial refresh.

On yfinance: it scrapes an undocumented endpoint and it will break without
notice. That is tolerable here only because failure is contained. If this
job dies, inference keeps serving yesterday's snapshot and the staleness
guard in store.py eventually refuses rather than lying. Swapping in a
paid provider means implementing fetch_daily() and nothing else.
"""

import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd

from core import bundle, features, store

log = logging.getLogger("gapo.ingest")

# 52 weekly bars plus warmup for the rolling windows, with slack.
HISTORY_DAYS = 365 * 3


def fetch_daily(ticker: str) -> pd.DataFrame:
    """The only provider-specific function. Replace to change data source."""
    import yfinance as yf

    # /tmp is the only writable directory in Lambda. Without this, yfinance
    # tries to write timezone cache to ~/.cache and may silently fail.
    yf.set_tz_cache_location("/tmp/yfinance_tz_cache")

    end = date.today()
    start = end - timedelta(days=HISTORY_DAYS)

    frame = yf.download(
        ticker,
        start=start.isoformat(),
        end=end.isoformat(),
        interval="1d",
        auto_adjust=True,
        progress=False,
        threads=False,
    )
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(0)
    return frame


def refresh(universe: list) -> dict:
    snapshot = date.today().isoformat()
    written, failed, errors = [], [], {}
    arrays = {}

    for ticker in universe:
        try:
            daily = fetch_daily(ticker)
            if daily.empty:
                raise ValueError("empty response")

            weekly = features.to_weekly(daily)
            if len(weekly) < features.LOOKBACK + 30:
                raise ValueError(f"only {len(weekly)} weekly bars")

            store.save_weekly(snapshot, ticker, weekly)
            # Precompute here so the request path never has to.
            arrays[ticker] = features.build(weekly)
            written.append(ticker)
        except Exception as exc:
            log.warning("ingest failed for %s: %s", ticker, exc)
            failed.append(ticker)
            errors[ticker] = str(exc)[:200]

    # Only promote a snapshot that is actually usable.
    if len(written) >= max(10, len(universe) * 0.8):
        matrix = np.stack([arrays[t] for t in written], axis=0)
        bundle.write(snapshot, written, matrix)
        store.write_pointer(snapshot, written)
        promoted = True
    else:
        log.error("snapshot not promoted: only %d tickers written", len(written))
        promoted = False

    return {
        "snapshot": snapshot,
        "written": len(written),
        "failed": len(failed),
        "promoted": promoted,
        "errors": errors,
    }
