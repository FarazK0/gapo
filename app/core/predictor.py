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

from core import bundle, features, graph
from core.store import MissingDataError, StaleDataError  # noqa: F401  re-exported for callers

log = logging.getLogger("gapo.predictor")

# Lambda path in production; local repo path for development.
_LOCAL_DEFAULT = str(
    Path(__file__).parents[2]
    / "protraderbot"
    / "InferenceApplication"
    / "full_model.pth"
)
MODEL_PATH = os.environ.get("MODEL_PATH", _LOCAL_DEFAULT)
SHA_PATH = os.environ.get("SHA_PATH", "/opt/model/model.sha256")

# Checkpoint was trained with these exact dimensions (verified from state_dict shapes).
MODEL_CLASS = "GATPortfolioNet"
MODEL_KWARGS = {
    "n_stocks": 10,
    "n_features": features.N_FEATURES,  # 6
    "d_model": 64,
}


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

    The checkpoint was saved as a full model object (torch.save(model)), not as a
    state_dict. weights_only=False is required. The checkpoint comes from a controlled
    training pipeline — never load untrusted .pth files with weights_only=False.
    """
    import __main__
    import torch.nn as nn
    from core.model import GATPortfolioNet, PortfolioMemory, StockTemporalEncoder

    # Checkpoint was saved with torch.save(model) from a __main__ script.
    # Register classes there so pickle's find_class resolves them correctly.
    for _cls in (GATPortfolioNet, PortfolioMemory, StockTemporalEncoder):
        if not hasattr(__main__, _cls.__name__):
            setattr(__main__, _cls.__name__, _cls)

    loaded = torch.load(MODEL_PATH, map_location="cpu", weights_only=False)

    if isinstance(loaded, nn.Module):
        # Checkpoint is a full model object — use directly.
        model = loaded
    else:
        # Checkpoint is a state_dict (dict with "state_dict" key or bare dict).
        from core.model import __dict__ as model_module

        cls = model_module[MODEL_CLASS]
        model = cls(**MODEL_KWARGS)
        state = loaded.get("state_dict", loaded) if isinstance(loaded, dict) else loaded
        missing, unexpected = model.load_state_dict(state, strict=False)
        if missing or unexpected:
            log.error(
                "state_dict mismatch: missing=%s unexpected=%s", missing, unexpected
            )
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


def _forward(
    x: torch.Tensor,
    edge_index: torch.Tensor,
    edge_attr: torch.Tensor,
    history=None,
):
    """Single place where the model's calling convention is expressed."""
    hist = history if history is not None else []
    return _MODEL(x, edge_index, edge_attr, history=hist)


def run(tickers: list, history=None) -> dict:
    """Pure-ish prediction. Reads the in-memory bundle, returns weights.

    Zero network calls on the happy path. Everything this needs was
    computed once by the ingest job and loaded once at container init.

    history: optional list of (weights_tensor, returns_tensor) pairs from
    prior predictions. Passed to the model's PortfolioMemory for regret
    conditioning. Defaults to [] when not provided.
    """
    snap = bundle.get()
    x_np = snap.select(tickers)

    x = torch.from_numpy(x_np)

    edge_index, edge_attr = graph.build_edge_index(x_np)

    raw = _forward(x, edge_index, edge_attr, history=history)
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
