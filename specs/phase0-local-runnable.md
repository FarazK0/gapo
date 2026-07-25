# Spec: Complete Phase 0 — make gapo locally runnable

## Background

This repo is a serverless ML inference service (AWS Lambda + CloudFront) that runs a
GATPortfolioNet graph attention model to produce stock portfolio weights.

The original build plan is `AGENT_PLAN_1_BUILD.md`. A previous agent run stalled at
**Phase 0, task 0-04** (golden fixtures) because:

1. `app/core/model.py` was a placeholder — the real model class was never copied in.
2. `app/requirements.txt` was missing `torch`, so `torch-geometric` failed to install.
3. Pre-existing ruff errors in `app/core/ingest.py` blocked validation.
4. `predictor.py` had wrong `MODEL_CLASS`, wrong `MODEL_KWARGS`, wrong `forward()` call,
   and `weights_only=True` which fails for a full-model pickle.

**Those four issues have already been fixed** (see commit `647cbea`). The model now loads
and the forward pass produces valid weights.

What remains is the work those failures blocked: golden fixtures, parity test, and a
local data layer so the full pipeline can run without AWS credentials.

---

## What this spec asks for

Three self-contained tasks. They must be done in order because task 2 depends on task 1,
and task 3 depends on task 2.

---

## Task 1 — Local storage backend

**Owner:** backend-agent  
**Inputs:** `app/core/store.py`, `app/core/bundle.py`, `app/core/ingest.py`  
**Outputs:** `app/core/store.py`, `app/core/bundle.py`

### Problem

`store.py` and `bundle.py` call S3 for every read and write. `ingest.py` can compute
features locally but cannot persist them without AWS credentials.

For local development and testing, the data layer must work without S3.

### What to implement

Add a `LOCAL` mode, activated by the environment variable `STORAGE_BACKEND=local`.

When `STORAGE_BACKEND=local`:

**`store.py`** — filesystem equivalents:
- `write_pointer(snapshot, tickers)` writes `data/current.json`
- `read_pointer()` reads `data/current.json`
- `assert_fresh(pointer)` is unchanged (pure logic, no I/O)
- `save_weekly(snapshot, ticker, frame)` writes `data/snapshots/<snapshot>/<ticker>.parquet`
- `load_weekly(snapshot, ticker)` reads `data/snapshots/<snapshot>/<ticker>.parquet`
- `StaleDataError` and `MissingDataError` are raised on the same conditions as the S3 path

**`bundle.py`** — filesystem equivalents:
- `write(snapshot, tickers, matrix)` writes `data/bundle.npz` (same numpy format as S3 path)
- `_read(snapshot)` reads `data/bundle.npz`
- `get()` logic is unchanged — cache, age check, reload on snapshot change

The `data/` directory is relative to `app/`. It should be in `.gitignore`.

When `STORAGE_BACKEND` is anything other than `local` (including unset), behaviour is
unchanged — S3 is used as before. No existing codepath should change.

### Acceptance criteria

1. `STORAGE_BACKEND=local python3 -c "from core import store; print('OK')"` runs without
   AWS credentials and without error.
2. Running `ingest.refresh(["AAPL","MSFT","NVDA","JPM","XOM"])` with `STORAGE_BACKEND=local`
   downloads data from Yahoo Finance, writes parquet files and `bundle.npz` to `data/`,
   and returns `{"promoted": true, "failed": 0, ...}`.
3. After ingest completes, `bundle.get()` returns a `Bundle` with the correct tickers
   without touching S3.
4. `ruff check app/` passes.

---

## Task 2 — Capture golden fixtures and write parity test

**Owner:** backend-agent  
**Depends on:** Task 1 (local storage backend must work first)  
**Inputs:** `app/core/predictor.py`, `app/core/features.py`, `app/core/graph.py`,
            `app/core/model.py`  
**Outputs:** `tests/__init__.py`, `tests/test_golden.py`,
             `tests/fixtures/golden/basket_1.json` through `basket_5.json`

### Problem

`tests/test_golden.py` does not exist. The file `app/core/predictor.py` explicitly says:
> "Verify against a known-good local run before you trust the deployed output.
> tests/test_parity.py exists for exactly this."

The test does not exist. Without it, there is no way to know whether the ported predictor
produces the same output as the original model.

### What to implement

**Important constraint on test design**: The validator runs tests inside a git worktree
that only contains tracked files. `data/` (local bundle) is gitignored and will NOT exist
in the worktree. Tests must be fully self-contained — no S3, no `data/` directory.

The solution: capture the model inputs (feature tensors) as numpy `.npy` files and commit
them to `tests/fixtures/input/`. The test calls `predictor._forward()` directly, bypassing
the bundle/store layer entirely.

**Step 1: Download raw market data and build feature tensors**

For each of the five baskets below, download market data and compute the feature tensor
using `app/core/features.py`. Use these five baskets:

```
basket_1: AAPL MSFT NVDA GOOGL META JPM XOM PG COST HD
basket_2: AAPL MSFT NVDA AMZN GOOGL META TSLA AMD INTC QCOM
basket_3: JPM BAC WFC V MA XOM CVX PG KO PEP
basket_4: AAPL JNJ WMT VZ IBM CAT GE UNP LIN NEE
basket_5: NVDA AMD AVGO QCOM TXN INTC CSCO IBM NOW CRM
```

For each basket:
1. Download 3 years of daily OHLCV using `yfinance` (same params as `ingest.fetch_daily`)
2. Convert to weekly bars using `features.to_weekly()`
3. Compute the (52, 6) feature array using `features.build()`
4. Stack into shape `(n_stocks, 52, 6)`
5. Save as `tests/fixtures/input/basket_N.npy` (numpy float32)

If a ticker fails to download (yfinance outage), substitute the next available ticker
from the 50-ticker universe in `infra/variables.tf`. Record any substitutions in a
comment at the top of `tests/test_golden.py`.

**Step 2: Capture golden outputs**

For each basket, run the model directly and save the output weights:

```python
import sys; sys.path.insert(0, "app")
import numpy as np
import torch
from core import predictor, graph

x_np = np.load("tests/fixtures/input/basket_N.npy")
x = torch.from_numpy(x_np)
edge_index, edge_attr = graph.build_edge_index(x_np)
weights = predictor._forward(x, edge_index, edge_attr)
w = weights.detach().cpu().numpy().tolist()
```

Save as `tests/fixtures/golden/basket_N.json`:
```json
{"tickers": ["AAPL", ...], "weights": {"AAPL": 0.123456, ...}}
```

**Step 3: Write `tests/test_golden.py`**

Parametrised pytest over all five baskets. For each basket:
1. Load `tests/fixtures/input/basket_N.npy` → feature tensor
2. Load `tests/fixtures/golden/basket_N.json` → expected weights
3. Build edge index using `graph.build_edge_index(x_np)`
4. Call `predictor._forward(x, edge_index, edge_attr)` directly (not `predictor.run()`)
5. Assert each weight matches the golden value within `atol=1e-4`
6. Assert weights sum to 1.0 within `1e-3`

The test imports `predictor` at module level, which loads the model checkpoint via the
`MODEL_PATH` environment variable. The test must work when `MODEL_PATH` is set to any
valid checkpoint path — do not hardcode the path.

Also create `tests/__init__.py` (empty) if it does not exist.

### Acceptance criteria

1. `tests/fixtures/input/basket_1.npy` through `basket_5.npy` exist, each loadable as
   a float32 array of shape `(10, 52, 6)`.
2. `tests/fixtures/golden/basket_1.json` through `basket_5.json` exist, each with
   10 tickers and weights summing to 1.0 ± 0.001.
3. `MODEL_PATH=/mnt/d/ml-serve/gapo/protraderbot/InferenceApplication/full_model.pth \
   python3 -m pytest tests/test_golden.py -v` passes for all 5 baskets.
4. `ruff check app/ tests/` passes.

---

## Task 3 — Local development server

**Owner:** backend-agent  
**Depends on:** Task 1 (local storage backend must work first)  
**Inputs:** `app/handler.py`, `web/index.html`  
**Outputs:** `app/server.py`, `requirements-dev.txt`

### Problem

`web/index.html` is the production frontend. It makes `POST /api/` requests. In
production this hits a Lambda function URL via CloudFront. Locally there is no server,
so the frontend cannot be tested without deploying to AWS.

### What to implement

**`app/server.py`** — a minimal HTTP server that:
- Serves `GET /` → returns `web/index.html` (path relative to repo root: `../../web/index.html` from `app/`)
- Serves `POST /api/` → calls `handler.predict(event, context=None)` and returns the response body with the correct HTTP status code
- Translates between HTTP request format and the Lambda event format that `handler.predict()` expects:
  - `event["body"]` = raw request body string
  - `event["isBase64Encoded"]` = False
  - `event.get("warmup")` = omit
- Use Python's built-in `http.server` or `FastAPI` — whichever requires fewer new
  dependencies. `http.server` requires zero new deps.
- Listen on `0.0.0.0:8000` by default. Port overridable via `PORT` env var.
- Print the URL on startup.

The server must set `STORAGE_BACKEND=local` automatically when `DATA_BUCKET` is not set
in the environment, so `python3 server.py` works out of the box for local dev.

**`requirements-dev.txt`** — local development dependencies not in `requirements.txt`:
```
torch>=2.0.0 --index-url https://download.pytorch.org/whl/cpu
yfinance>=0.2.40
pytest>=8.0
```

Add a usage comment at the top of `requirements-dev.txt`:
```
# Local dev only. Install with:
#   pip install torch --index-url https://download.pytorch.org/whl/cpu
#   pip install -r requirements.txt -r requirements-dev.txt
```

### Acceptance criteria

1. `cd app && python3 server.py` starts without error and prints a URL.
2. `curl -s http://localhost:8000/ | grep -i "allocation"` succeeds (the HTML page loads).
3. After running ingest locally (Task 1), `curl -s -X POST http://localhost:8000/api/ \
   -H "content-type: application/json" \
   -d '{"tickers":["AAPL","MSFT","NVDA","JPM","XOM","PG","COST","HD","V","KO"]}' \
   | python3 -c "import sys,json; d=json.load(sys.stdin); print(round(sum(d['weights'].values()),6))"` 
   prints a value within 0.001 of 1.0.
4. `ruff check app/` passes.

---

## Constraints

- Do not modify any file under `protraderbot/` or `research/`.
- Do not commit `*.pth`, `*.pt`, or anything under `app/artifacts/` — these are gitignored.
- Do not modify `app/core/features.py`, `app/core/graph.py`, or `app/core/model.py` —
  the model architecture is fixed.
- `STORAGE_BACKEND=s3` (the default) must continue to work unchanged after these changes.
  Do not break the production path.
- The server (Task 3) is for local development only. It does not need auth, TLS, or
  production hardening.
