"""Feature engineering.

WARNING, read this before deploying.

This module must be byte-for-byte equivalent in behaviour to the feature
code inside training50.py. If the training script computes volatility over
a 4 week window and this computes it over 8, the model will still return
plausible looking weights and they will be wrong. Nothing will crash and
no test will fail.

The fix is not to be careful. The fix is to delete the duplicated logic
from training50.py and import this module there instead. One definition,
used by both paths. Do that before you deploy, not after.

Feature order, matching the README:
    0  weekly return
    1  4 week momentum
    2  rolling volatility
    3  high / low ratio
    4  volume z-score
    5  drawdown from peak
"""

import numpy as np
import pandas as pd

LOOKBACK = 52
N_FEATURES = 6
VOL_WINDOW = 12
VOLUME_WINDOW = 26


def to_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    """Resample daily OHLCV to weekly bars ending Friday."""
    return daily.resample("W-FRI").agg(
        {
            "Open": "first",
            "High": "max",
            "Low": "min",
            "Close": "last",
            "Volume": "sum",
        }
    ).dropna()


def build(weekly: pd.DataFrame) -> np.ndarray:
    """Return a (LOOKBACK, N_FEATURES) array for one ticker.

    Takes the most recent LOOKBACK complete weeks.
    """
    close = weekly["Close"]
    high = weekly["High"]
    low = weekly["Low"]
    volume = weekly["Volume"]

    weekly_return = close.pct_change()
    momentum_4w = close.pct_change(4)
    volatility = weekly_return.rolling(VOL_WINDOW).std()
    hl_ratio = (high / low.replace(0, np.nan)) - 1.0

    vol_mean = volume.rolling(VOLUME_WINDOW).mean()
    vol_std = volume.rolling(VOLUME_WINDOW).std().replace(0, np.nan)
    volume_z = (volume - vol_mean) / vol_std

    running_peak = close.cummax()
    drawdown = (close / running_peak) - 1.0

    frame = pd.concat(
        [weekly_return, momentum_4w, volatility, hl_ratio, volume_z, drawdown],
        axis=1,
    )
    frame.columns = [
        "ret",
        "mom4",
        "vol",
        "hl",
        "volz",
        "dd",
    ]

    frame = frame.replace([np.inf, -np.inf], np.nan).ffill().fillna(0.0)

    if len(frame) < LOOKBACK:
        raise ValueError(
            f"Need {LOOKBACK} weekly bars, have {len(frame)}."
        )

    window = frame.iloc[-LOOKBACK:].to_numpy(dtype=np.float32)
    assert window.shape == (LOOKBACK, N_FEATURES)
    return window


def stack(per_ticker: dict, order: list) -> np.ndarray:
    """Assemble (n_stocks, LOOKBACK, N_FEATURES) in the caller's order.

    Order matters. The graph edge indices are positional, so the feature
    tensor and the adjacency must use the same indexing.
    """
    return np.stack([per_ticker[t] for t in order], axis=0)
