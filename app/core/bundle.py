"""In-memory snapshot bundle.

WHY THIS EXISTS

The first version of the predict path did, per request:

    10 x S3 GetObject   (one parquet per ticker)
    10 x parquet decode
    10 x pandas feature computation over 52 weeks

That is roughly 250 ms of network and CPU before the model is even called,
and it scales linearly with concurrency. Under a burst it is the whole
story: S3 latency dominates, Lambda duration triples, and you pay for
every millisecond of it across every concurrent container.

None of that work depends on the request. The universe is 50 tickers and
the snapshot changes once a day. So it moves to container init, once, for
the entire universe:

    ingest  -> computes every ticker's (52, 6) feature array
            -> writes one bundle.npz for the whole snapshot
    predict -> loads bundle.npz at init, holds it in memory
            -> a request becomes an array slice plus a forward pass

Per-request I/O drops to zero. Handler time drops from ~250 ms to ~15 ms.
The bundle for 50 tickers is about 60 KB, so this costs nothing in memory.

This is the single change that decides whether your load test looks good.
Make it before you run one, or you will spend a day tuning around a
bottleneck that should not exist.
"""

import io
import logging
import os
import time

import boto3
import numpy as np

from core import store

log = logging.getLogger("gapo.bundle")

BUCKET = os.environ["DATA_BUCKET"]
BUNDLE_NAME = "bundle.npz"

# How long a warm container may keep serving a bundle before rechecking
# the pointer. Long enough that a burst does not stampede S3, short enough
# that a new snapshot propagates within the hour.
REFRESH_SECONDS = int(os.environ.get("BUNDLE_REFRESH_SECONDS", "900"))

_s3 = boto3.client("s3")


class Bundle:
    __slots__ = ("snapshot", "tickers", "index", "matrix", "loaded_at")

    def __init__(self, snapshot, tickers, matrix):
        self.snapshot = snapshot
        self.tickers = tickers
        self.index = {t: i for i, t in enumerate(tickers)}
        self.matrix = matrix  # (n_universe, lookback, n_features) float32
        self.loaded_at = time.monotonic()

    def select(self, requested):
        """Return (len(requested), lookback, n_features) in the given order."""
        missing = [t for t in requested if t not in self.index]
        if missing:
            raise store.MissingDataError(
                f"Not in snapshot {self.snapshot}: {', '.join(missing)}"
            )
        rows = [self.index[t] for t in requested]
        # Fancy indexing copies, which is what we want. The model must not
        # be handed a view into shared state.
        return self.matrix[rows]

    @property
    def age_seconds(self):
        return time.monotonic() - self.loaded_at


def write(snapshot: str, tickers: list, matrix: np.ndarray) -> None:
    """Called by ingest. One object for the whole universe."""
    buf = io.BytesIO()
    np.savez_compressed(buf, tickers=np.array(tickers), matrix=matrix)
    _s3.put_object(
        Bucket=BUCKET,
        Key=f"snapshots/{snapshot}/{BUNDLE_NAME}",
        Body=buf.getvalue(),
    )
    log.info("bundle written: %s, %d tickers, %d bytes",
             snapshot, len(tickers), buf.tell())


def _read(snapshot: str) -> Bundle:
    obj = _s3.get_object(Bucket=BUCKET, Key=f"snapshots/{snapshot}/{BUNDLE_NAME}")
    with np.load(io.BytesIO(obj["Body"].read()), allow_pickle=False) as z:
        tickers = [str(t) for t in z["tickers"]]
        matrix = z["matrix"].astype(np.float32)
    return Bundle(snapshot, tickers, matrix)


_CACHE = None


def get() -> Bundle:
    """Return the current bundle, refreshing at most every REFRESH_SECONDS.

    Deliberately not thread safe in a clever way. A Lambda container serves
    one request at a time, so the worst case is two adjacent refreshes.
    """
    global _CACHE

    if _CACHE is not None and _CACHE.age_seconds < REFRESH_SECONDS:
        return _CACHE

    pointer = store.read_pointer()
    snapshot = store.assert_fresh(pointer)

    if _CACHE is not None and _CACHE.snapshot == snapshot:
        _CACHE.loaded_at = time.monotonic()  # still current, reset the clock
        return _CACHE

    _CACHE = _read(snapshot)
    log.info("bundle loaded: %s, %d tickers", _CACHE.snapshot, len(_CACHE.tickers))
    return _CACHE


def prime() -> None:
    """Load at container init so the first real request is not the one
    that pays for it. Failure here is not fatal, because a cold container
    that cannot reach S3 should still be able to try again per request."""
    try:
        get()
    except Exception:
        log.warning("bundle prime failed, will retry on first request", exc_info=True)
