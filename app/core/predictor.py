"""Model loading and inference.

This is the seam between your repository and the platform. Everything
above it is infrastructure. Everything below it is your research code.

INTEGRATION STEPS

1. Copy your model.py into this directory unchanged.
2. Set MODEL_CLASS below to the class name it defines.
3. Set MODEL_KWARGS to the constructor arguments used at training time.
4. Confirm forward() signature matches _forward() below and adjust if not.

Steps 3 and 4 are where this will break first, and the failure is silent
if the checkpoint happens to load. Verify against a known-good local run
before you trust the deployed output. tests/test_parity.py exists for
exactly this.
"""

import hashlib
import logging
import os
from pathlib import Path

import numpy as np
import torch

from core import bundle, features, graph, store
from core.store import MissingDataError, StaleDataError  # re-exported

log = logging.getLogger("gapo.predictor")

MODEL_PATH = os.environ.get("MODEL_PATH", "/opt/model/full_model.pth")
SHA_PATH = "/opt/model/model.sha256"

# ---- adjust these three to match your training run ------------------
MODEL_CLASS = "GATPortfolio"
MODEL_KWARGS = {
    "in_features": features.N_FEATURES,
    "lookback": features.LOOKBACK,
    "hidden_dim": 64,
    "heads": 4,
}
# ---------------------------------------------------------------------


def _verify_checkpoint(path: str) -> str:
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    expected_file = Path(SHA_PATH)
    if expected_file.exists():
        expected = expected_file.read_text().split()[0].strip()
        if digest != expected:
            raise RuntimeError(
                "Model checkpoint does not match its recorded SHA256. "
                "Refusing to load."
            )
    return digest


def _load_model():
    """Load once at container init.

    torch.load with weights_only=True refuses to execute arbitrary
    pickled code. Never relax this. A checkpoint is executable content
    and the whole reason the desktop app's 'Load Model' button cannot
    exist on a server.
    """
    from core.model import __dict__ as model_module  # your model.py

    cls = model_module[MODEL_CLASS]
    model = cls(**MODEL_KWARGS)

    state = torch.load(MODEL_PATH, map_location="cpu", weights_only=True)
    if isinstance(state, dict) and "state_dict" in state:
        state = state["state_dict"]

    missing, unexpected = model.load_state_dict(state, strict=False)
    if missing or unexpected:
        # Loud, because a partial load produces confident nonsense.
        log.error("state_dict mismatch: missing=%s unexpected=%s", missing, unexpected)
        raise RuntimeError(
            "Checkpoint does not match the model definition. "
            f"Missing keys: {list(missing)[:5]}. "
            f"Unexpected keys: {list(unexpected)[:5]}."
        )

    model.eval()
    torch.set_grad_enabled(False)
    torch.set_num_threads(2)
    return model


MODEL_SHA = _verify_checkpoint(MODEL_PATH)
_MODEL = _load_model()
log.info("model loaded, sha256=%s", MODEL_SHA[:12])


def _forward(x: torch.Tensor, edge_index: torch.Tensor, edge_weight: torch.Tensor):
    """Single place where the model's calling convention is expressed.

    If your forward() takes different arguments, change it here only.
    """
    return _MODEL(x, edge_index, edge_weight)


def run(tickers: list) -> dict:
    """Pure-ish prediction. Reads the in-memory bundle, returns weights.

    Zero network calls on the happy path. Everything this needs was
    computed once by the ingest job and loaded once at container init.
    """
    snap = bundle.get()
    x_np = snap.select(tickers)

    x = torch.from_numpy(x_np)

    edge_index, edge_weight = graph.build_edge_index(x_np)

    raw = _forward(x, edge_index, edge_weight)
    weights = raw.detach().cpu().numpy().reshape(-1).astype(np.float64)

    # Defensive normalisation. The model should already emit a simplex,
    # but a long-only portfolio that does not sum to one is a bug the
    # user should never see rendered as an allocation.
    if np.any(weights < -1e-6):
        log.warning("negative weights emitted, clipping: min=%.4f", weights.min())
    weights = np.clip(weights, 0.0, None)
    total = weights.sum()
    if total <= 0:
        raise RuntimeError("Model produced an all-zero allocation.")
    weights = weights / total

    return {
        "as_of": snap.snapshot,
        "weights": {t: round(float(w), 6) for t, w in zip(tickers, weights)},
        "concentration_hhi": round(float((weights ** 2).sum()), 4),
        "effective_positions": round(float(1.0 / (weights ** 2).sum()), 2),
    }
