"""Golden-output parity tests for GATPortfolioNet.

Fixture inputs (tests/fixtures/input/basket_N.npy) are synthetic float32 arrays
with the statistical properties of weekly OHLCV features produced by features.py.
Yahoo Finance was rate-limited during fixture generation (yfinance 0.2.40), so
synthetic tensors with fixed seeds were used instead of live market data.
Golden outputs (tests/fixtures/golden/basket_N.json) are real model inference on
those inputs, captured once and frozen.

The test verifies that predictor._forward() reproduces those exact outputs within
atol=1e-4, confirming model determinism and that no silent changes have been made
to the model or graph construction code.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pytest
import torch

sys.path.insert(0, str(Path(__file__).parents[1] / "app"))

from core import graph, predictor  # noqa: E402

FIXTURE_DIR = Path(__file__).parent / "fixtures"

BASKETS = {
    1: ["AAPL", "MSFT", "NVDA", "GOOGL", "META", "JPM", "XOM", "PG", "COST", "HD"],
    2: ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD", "INTC", "QCOM"],
    3: ["JPM", "BAC", "WFC", "V", "MA", "XOM", "CVX", "PG", "KO", "PEP"],
    4: ["AAPL", "JNJ", "WMT", "VZ", "IBM", "CAT", "GE", "UNP", "LIN", "NEE"],
    5: ["NVDA", "AMD", "AVGO", "QCOM", "TXN", "INTC", "CSCO", "IBM", "NOW", "CRM"],
}


@pytest.mark.parametrize("basket_id", [1, 2, 3, 4, 5])
def test_golden_parity(basket_id):
    input_path = FIXTURE_DIR / "input" / f"basket_{basket_id}.npy"
    golden_path = FIXTURE_DIR / "golden" / f"basket_{basket_id}.json"

    x_np = np.load(str(input_path))
    assert x_np.shape == (10, 52, 6), f"Unexpected input shape: {x_np.shape}"
    assert x_np.dtype == np.float32

    with open(golden_path) as f:
        golden = json.load(f)

    tickers = BASKETS[basket_id]
    assert golden["tickers"] == tickers
    assert len(golden["weights"]) == 10

    golden_weights = np.array([golden["weights"][t] for t in tickers], dtype=np.float64)
    assert abs(golden_weights.sum() - 1.0) < 1e-3, (
        f"Golden weights do not sum to 1: {golden_weights.sum()}"
    )

    x = torch.from_numpy(x_np)
    edge_index, edge_attr = graph.build_edge_index(x_np)

    with torch.no_grad():
        weights_tensor = predictor._forward(x, edge_index, edge_attr)

    actual = weights_tensor.detach().cpu().numpy().reshape(-1).astype(np.float64)
    actual = np.clip(actual, 0.0, None)
    actual = actual / actual.sum()

    np.testing.assert_allclose(
        actual,
        golden_weights,
        atol=1e-4,
        err_msg=f"Basket {basket_id} weights deviate from golden fixtures",
    )

    assert abs(actual.sum() - 1.0) < 1e-3, f"Actual weights do not sum to 1: {actual.sum()}"
