# AGENT_PLAN_1_BUILD.md
## Phases 0 to 6 — build and operate the service

Execution plan for an autonomous coding agent, part 1 of 2. Read this file
in full before taking any action.

**Scope of this file.** Port the model, stand up the infrastructure, ship a
working public URL, wrap it in CI/CD, and instrument it. At the end of
Phase 6 the service is live, tested, deployed by pipeline, and monitored.

**Do not read part 2 yet.** `AGENT_PLAN_2_SCALE.md` covers load testing,
optimisation, hardening, and documentation. It has an entry gate that checks
this file is genuinely finished. Attempting phase 7 early spends money
measuring a system that is still changing.

Companion document: `gapo-implementation-plan.md` contains the reasoning
behind these tasks. This file contains only what to do and how to verify it.
Where the two disagree, this file wins.

---

## 0. Your role

You are implementing a serverless ML inference platform on AWS. The machine
learning model already exists and is finished. You are not improving it.

**This is an infrastructure project.** The model is a fixed binary artifact
you deploy. Its quality is out of scope.

Work one task at a time, in order. After each task, run its `VERIFY` block.
If verification fails, fix it before moving on. Do not batch tasks. Do not
skip ahead.

---

## 1. Hard stops

These require an explicit human decision. Stop, state the situation
concisely, and wait. Do not proceed on an assumption.

| Condition | Why |
| --- | --- |
| Any `terraform apply` that creates billable resources, for the first time in a phase | Cost is irreversible in a way code is not |
| Any `terraform destroy` | Data loss |
| Any load test scenario other than `smoke` | Generates real AWS spend |
| Raising `reserved_concurrency` above 50 | Removes the cost ceiling |
| Enabling `provisioned_concurrency` | Roughly 38 USD per month per unit |
| An action you estimate will cost more than 5 USD | Ask first, with your estimate |
| `tests/test_golden.py` fails after step 4 of the failure ladder | Human judgement needed on tolerance |
| A source file the plan tells you to read does not exist | Do not invent its contents |
| Any AWS API call returning `AccessDenied` twice for the same operation | Broadening IAM unprompted is dangerous |
| A task's `VERIFY` fails three times | You are stuck. Report rather than thrash |

---

## 2. Prohibitions

Never do any of these, regardless of what would be convenient.

1. **Never retrain, fine-tune, or modify the model architecture.** If the
   model appears wrong, that is a finding to report, not a task to do.
2. **Never reimplement feature engineering or graph construction from a
   description.** Read the user's actual source and port it verbatim. This
   is the single most likely failure mode for an agent on this project. A
   README is a summary, not a specification.
3. **Never commit `*.pth`, `*.pt`, `*.onnx`, or anything in `app/artifacts/`.**
4. **Never write AWS access keys, secrets, or tokens to any file.**
   Authentication is OIDC only.
5. **Never use `terraform apply -auto-approve` outside `.github/workflows/`.**
   Interactive applies show the plan and wait.
6. **Never edit files under `research/`** except the one import change in
   task 0-09, and only if the user approves it.
7. **Never weaken a test to make it pass.** Loosening the golden tolerance
   is a human decision (hard stop above).
8. **Never `torch.load` without `weights_only=True`.**
9. **Never set a Lambda function URL to `authorization_type = "NONE"`.**
10. **Never disable a CloudWatch alarm to silence a failing check.**

---

## 3. State protocol

Maintain `PROGRESS.md` in the repository root. Create it on first run.
Update it immediately after each task, before starting the next.

```markdown
# Progress

Last updated: <ISO timestamp>
Current task: <ID or "blocked" or "awaiting approval">

## Completed
- [x] 0-01 <one line: what you actually did>

## Blocked
- [ ] 0-06 <what failed, what you tried, what you need>

## Notes
<Anything a human needs to know. Discrepancies, surprises, decisions taken.>
```

If `PROGRESS.md` already exists when you start, read it and resume from the
first incomplete task. Every task below is idempotent or has a guard that
makes re-running safe.

---

## 4. Environment preflight

Run before task 0-01. If any check fails, stop and report.

```bash
python3 --version          # expect 3.11.x
docker --version
terraform version          # expect >= 1.6
aws --version
aws sts get-caller-identity
git rev-parse --show-toplevel
```

Record the AWS account ID and region. Every bucket name in this plan
includes the account ID.

---

# PHASE 0 — Port the model

**Goal: prediction becomes a pure function whose output is byte-identical to
the existing desktop application.**

Read this before starting. The tests in this phase do not check whether the
model is correct. They check whether you changed its behaviour while moving
it. The desktop app is the reference implementation. If it contains a bug,
the ported code must reproduce that bug exactly.

---

### TASK 0-00 — Inventory
**DEPENDS:** preflight

**DO:**
Read these files completely. Do not skim. Do not proceed without them.

```
InferenceApplication/portfolio_rebalancer.py
InferenceApplication/model.py
research/50stocks/training50.py          (or "training and backtesting/50stocks/")
research/50stocks/downloadstocks50.py
```

Write to `PROGRESS.md` under Notes:
- the exact class name and constructor signature in `model.py`
- every constructor argument used in `training50.py`, with its value
- the line ranges in `portfolio_rebalancer.py` that compute features
- the line ranges that build the correlation graph
- the value of `top_k` (or equivalent) and whether ranking uses signed or
  absolute correlation
- the `auto_adjust` argument passed to `yf.download` in `downloadstocks50.py`

**VERIFY:** All six items recorded with specific line numbers or values.

**STOP_IF:** Any file is missing. Report which and wait.

---

### TASK 0-01 — Restructure
**DEPENDS:** 0-00

**DO:**
```bash
git checkout -b feat/platform
mkdir -p app/core web infra loadtest/results tests/fixtures docs/adr
git mv "training and backtesting" research 2>/dev/null || true
touch app/core/__init__.py tests/__init__.py
```
Create `.gitignore` containing at minimum: `app/artifacts/`, `*.pth`,
`.terraform/`, `*.tfstate*`, `tfplan`, `__pycache__/`, `.venv/`.

**VERIFY:**
```bash
test -d app/core && test -d infra && test -d tests/fixtures && echo OK
git check-ignore app/artifacts/x.pth && echo IGNORED
```

---

### TASK 0-02 — Port the prediction path
**DEPENDS:** 0-01

**DO:**
Create `app/core/features.py`, `app/core/graph.py`, and `app/core/model.py`.

**Port, do not reimplement.** Copy the exact expressions from
`portfolio_rebalancer.py` identified in 0-00. Preserve window sizes,
operation order, fill and dropna behaviour, and dtype. If the original does
something that looks wrong or inelegant, keep it. You are not refactoring.

Copy `InferenceApplication/model.py` to `app/core/model.py` unchanged.

Create `app/core/predictor.py` exposing `run(tickers, feature_matrix)` with
no file I/O, no network calls, and no UI code.

**VERIFY:**
```bash
python3 -c "from app.core import features, graph, model, predictor; print('OK')"
grep -rn "tkinter\|Tk()\|messagebox" app/ && echo "FAIL: UI code leaked" || echo OK
```

**STOP_IF:** You cannot locate the feature computation in the original
source. Do not write your own from the README.

---

### TASK 0-03 — Freeze a data snapshot
**DEPENDS:** 0-02

**DO:**
Download daily OHLCV for the full universe once, using the exact
`auto_adjust` value recorded in 0-00. Save raw to `tests/fixtures/raw/`.

This snapshot is frozen. Every later test uses it. Never refresh it, or the
golden fixtures stop being reproducible.

**VERIFY:**
```bash
ls tests/fixtures/raw/*.csv | wc -l    # expect the full universe count
```

---

### TASK 0-04 — Capture golden fixtures
**DEPENDS:** 0-03

**DO:**
Run the **original** `portfolio_rebalancer.py` logic against the frozen
snapshot for these five baskets, and save each result to
`tests/fixtures/golden/basket_N.json` as `{"tickers": [...], "weights": {...}}`.

```
1. AAPL MSFT NVDA GOOGL META JPM XOM PG COST HD
2. AAPL MSFT NVDA AMZN GOOGL META TSLA AMD INTC QCOM
3. JPM BAC WFC V MA XOM CVX PG KO PEP
4. AAPL JNJ WMT VZ IBM CAT GE UNP LIN NEE
5. NVDA AMD AVGO QCOM TXN INTC CSCO IBM NOW CRM
```

If invoking the original requires a GUI, extract the callback body into a
short script under `tests/` rather than modifying `portfolio_rebalancer.py`.

**VERIFY:**
```bash
ls tests/fixtures/golden/*.json | wc -l   # expect 5
python3 -c "
import json,glob
for f in sorted(glob.glob('tests/fixtures/golden/*.json')):
    d=json.load(open(f)); s=sum(d['weights'].values())
    print(f, len(d['weights']), round(s,6))
    assert abs(s-1)<1e-3, 'weights do not sum to 1'
print('OK')"
```

---

### TASK 0-05 — Pin dependencies
**DEPENDS:** 0-04

**DO:** `pip freeze` from the environment that produced the fixtures. Write
exact pins for torch, torch-geometric, numpy, pandas to
`app/requirements.txt`. Record the torch version in `PROGRESS.md`.

**VERIFY:**
```bash
grep -E "^(numpy|pandas|torch-geometric)==" app/requirements.txt && echo OK
```

---

### TASK 0-06 — Force determinism
**DEPENDS:** 0-02

**DO:** In `predictor.py` model loading: call `model.eval()`, set
`torch.set_grad_enabled(False)`, `torch.manual_seed(0)`,
`torch.set_num_threads(2)`. Load with
`torch.load(path, map_location="cpu", weights_only=True)`.

**VERIFY:**
```bash
python3 -c "
from app.core import predictor
import json
b=json.load(open('tests/fixtures/golden/basket_1.json'))
out=[tuple(predictor.run(b['tickers'])['weights'].values()) for _ in range(10)]
assert len(set(out))==1, 'non-deterministic output'
print('OK deterministic')"
```

---

### TASK 0-07 — Golden test
**DEPENDS:** 0-04, 0-05, 0-06

**DO:** Write `tests/test_golden.py` asserting `predictor.run()` matches
each fixture within `atol=1e-4`. Parametrise over all five baskets.

**VERIFY:**
```bash
python3 -m pytest tests/test_golden.py -v
```

**IF IT FAILS:** work down this ladder and stop at the first fix that works.
Record in `PROGRESS.md` which rung resolved it.

1. **Non-determinism.** Re-check 0-06 was applied. Confirm `model.eval()`
   actually runs before the first forward pass.
2. **Version skew.** Confirm the running interpreter uses the pins from
   0-05. `pip list | grep -E "torch|numpy|pandas"`.
3. **You reimplemented something.** Most likely cause. Diff your
   `features.py` against the original source line by line. Window sizes,
   `min_periods`, `ffill` versus `fillna`, and `pct_change` periods are the
   usual culprits. Replace with a verbatim copy.
4. **Checkpoint will not load.** Read the constructor arguments back out of
   the weights:
   ```bash
   python3 -c "
   import torch
   sd = torch.load('InferenceApplication/full_model.pth', map_location='cpu', weights_only=True)
   sd = sd.get('state_dict', sd)
   for k,v in sd.items(): print(f'{k:55s} {tuple(v.shape)}')"
   ```
   Infer hidden dimensions, head counts, and depth from the shapes. Update
   `MODEL_KWARGS`.
5. **Still failing.** HARD STOP. Report the maximum absolute difference per
   basket and which rungs you tried. Do not loosen the tolerance yourself.
   Do not retrain.

---

### TASK 0-08 — Universe size check
**DEPENDS:** 0-07

**VERIFY:**
```bash
python3 -c "
from app.core import predictor
for n in (5,10,25):
    t=['AAPL','MSFT','NVDA','GOOGL','META','JPM','XOM','PG','COST','HD',
       'V','KO','PEP','CVX','MRK','ABBV','WMT','MA','UNH','JNJ',
       'BAC','DIS','CSCO','ADBE','CRM'][:n]
    w=predictor.run(t)['weights']
    assert len(w)==n and abs(sum(w.values())-1)<1e-3
    print(n,'OK')"
```

**IF IT FAILS:** find the hard-coded 10 and record its location in
`PROGRESS.md`. Do not change the model to fix it. Set `MAX_ASSETS` and
`MIN_ASSETS` in the handler to the range that works.

---

### TASK 0-09 — Single definition (OPTIONAL)
**DEPENDS:** 0-07
**REQUIRES HUMAN APPROVAL.** Editing `research/` is otherwise prohibited.

**DO:** Ask the user whether to make `training50.py` import
`app/core/features.py` rather than keep its own copy. If declined, skip and
add a note to `docs/adr/` recording the duplication as accepted.

---

# PHASE 1 — AWS foundations

### TASK 1-01 — Bootstrap stack
**DEPENDS:** 0-07
**HARD STOP: creates billable resources. Show the template and wait.**

**DO:** Deploy `bootstrap/bootstrap.yaml`, parameters `GitHubOrg`,
`GitHubRepo`, `ProjectName=gapo`.

```bash
aws cloudformation deploy \
  --template-file bootstrap/bootstrap.yaml \
  --stack-name gapo-bootstrap \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides GitHubOrg=<org> GitHubRepo=<repo>
```

**VERIFY:**
```bash
aws cloudformation describe-stacks --stack-name gapo-bootstrap \
  --query 'Stacks[0].[StackStatus,Outputs]' --output json
```
Expect `CREATE_COMPLETE` and three outputs.

---

### TASK 1-02 — Upload checkpoint
**DEPENDS:** 1-01

```bash
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
aws s3 cp InferenceApplication/full_model.pth \
  "s3://gapo-artifacts-${ACCOUNT}/models/full_model.pth"
```

**VERIFY:**
```bash
aws s3 ls "s3://gapo-artifacts-${ACCOUNT}/models/"
git status --porcelain | grep -i "\.pth" && echo "FAIL: checkpoint staged" || echo OK
```

---

### TASK 1-03 — Repository variables
**DEPENDS:** 1-01

**DO:** Instruct the user to set repository **variables** (not secrets)
`AWS_DEPLOY_ROLE` and `TF_STATE_BUCKET` from the stack outputs. You cannot
do this yourself. Wait for confirmation.

---

### TASK 1-04 — Verify OIDC
**DEPENDS:** 1-03

**DO:** Add a temporary workflow that assumes the role and runs
`aws sts get-caller-identity`. Push, observe, then delete the workflow.

**VERIFY:** The run prints an ARN containing `gapo-github-deploy`.

**Rationale:** debugging OIDC inside a 12-minute deploy is slow. Debugging
it in a 20-second workflow takes one attempt.

---

### TASK 1-05 — Budget guard
**DEPENDS:** 1-01

**DO:** Ask the user for an alert email. Set `budget_alert_email` and
`monthly_budget_usd = 25` in `infra/terraform.tfvars`. Confirm
`terraform.tfvars` is gitignored if it will contain the address.

**VERIFY:** `grep budget_alert_email infra/terraform.tfvars`

**Do not proceed to Phase 2 without this.**

---

# PHASE 2 — Data plane

### TASK 2-01 — Core resources
**DEPENDS:** 1-05
**HARD STOP: first billable Terraform apply.**

```bash
cd infra
terraform init -backend-config="bucket=<TF_STATE_BUCKET>" \
  -backend-config="region=<REGION>" \
  -backend-config="dynamodb_table=gapo-tfstate-lock"
terraform plan -var="image_tag=bootstrap" \
  -target=aws_s3_bucket.data -target=aws_dynamodb_table.history -out=tfplan
```
Show the plan. Wait for approval. Then `terraform apply tfplan`.

**VERIFY:** `terraform state list | grep -E "aws_s3_bucket.data|aws_dynamodb_table.history"`

---

### TASK 2-02 — Ingest and bundle
**DEPENDS:** 2-01

**DO:** Implement `app/core/ingest.py` and `app/core/bundle.py`. Ingest
computes every ticker's feature array **using `app/core/features.py`**, the
same module the request path uses. Do not duplicate the logic.

Order of operations, which matters: write all parquet, write `bundle.npz`,
then write `current.json`. The pointer is written last so a partial run
never becomes the live snapshot.

**VERIFY (run locally against the frozen snapshot):**
```bash
python3 -c "
from app.core import ingest
r = ingest.refresh(['AAPL','MSFT','NVDA','JPM','XOM'])
print(r); assert r['promoted'] and r['failed']==0"
```

---

### TASK 2-03 — Staleness guard
**DEPENDS:** 2-02

**VERIFY:** Hand-edit `current.json` to a date 30 days in the past, then:
```bash
python3 -c "
from app.core import predictor, store
try:
    predictor.run(['AAPL','MSFT','NVDA','JPM','XOM'])
    raise SystemExit('FAIL: served stale data')
except store.StaleDataError as e:
    print('OK refused:', e)"
```
Restore the pointer afterwards.

---

### TASK 2-04 — Deploy ingest
**DEPENDS:** 2-03
**HARD STOP: billable apply.**

Apply the ingest Lambda, EventBridge rule, and failure alarm. Invoke once
manually.

**VERIFY:**
```bash
aws lambda invoke --function-name gapo-ingest --cli-read-timeout 660 \
  --payload '{}' /tmp/ingest.json && cat /tmp/ingest.json
aws s3 ls "s3://gapo-marketdata-${ACCOUNT}/" --recursive | grep bundle.npz
```

---

# PHASE 3 — Inference

### TASK 3-01 — Wire predictor to bundle
**DEPENDS:** 2-04

**DO:** `predictor.run()` reads from `bundle.get()`. Zero S3 calls per
request on the warm path.

**VERIFY:**
```bash
python3 -m pytest tests/test_golden.py -v   # must still pass
```

---

### TASK 3-02 — Build image
**DEPENDS:** 3-01

```bash
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
mkdir -p app/artifacts
aws s3 cp "s3://gapo-artifacts-${ACCOUNT}/models/full_model.pth" app/artifacts/full_model.pth
sha256sum app/artifacts/full_model.pth | awk '{print $1}' > app/artifacts/model.sha256
cd app && docker build -t gapo:local .
```

**VERIFY:**
```bash
docker images gapo:local --format '{{.Size}}'   # under 2GB
```

---

### TASK 3-03 — Local container test
**DEPENDS:** 3-02

**Do this before pushing anything.** The push-deploy-debug loop is eight
minutes. The local loop is thirty seconds.

```bash
docker run -d --name gapo-test -p 9000:8080 \
  -e DATA_BUCKET="gapo-marketdata-${ACCOUNT}" \
  -e HISTORY_TABLE=gapo-portfolio-history \
  -e AWS_REGION="${REGION}" \
  -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_SESSION_TOKEN \
  gapo:local
sleep 15
curl -s "http://localhost:9000/2015-03-31/functions/function/invocations" \
  -d '{"body":"{\"tickers\":[\"AAPL\",\"MSFT\",\"NVDA\",\"JPM\",\"XOM\",\"PG\",\"COST\",\"HD\",\"V\",\"KO\"]}"}'
```

**VERIFY:** Response is 200 with a `weights` object summing to 1.
Then `docker rm -f gapo-test`.

---

### TASK 3-04 — Containerised golden test
**DEPENDS:** 3-03

**DO:** Run basket 1 through the container and diff against
`tests/fixtures/golden/basket_1.json` at `atol=1e-4`.

**IF IT FAILS but 0-07 passed:** the cause is version skew inside the image.
Align `app/requirements.txt` with the pins from 0-05. This is a real and
common failure. Do not dismiss it as floating point noise without
measuring the actual delta.

---

### TASK 3-05 — Deploy predict
**DEPENDS:** 3-04
**HARD STOP: billable apply.**

Push to ECR, apply the predict Lambda with `reserved_concurrency = 50` and a
function URL with `authorization_type = "AWS_IAM"`.

**VERIFY:**
```bash
aws lambda get-function-configuration --function-name gapo-predict \
  --query '[MemorySize,Timeout,ReservedConcurrentExecutions]'
aws lambda get-function-url-config --function-name gapo-predict \
  --query AuthType   # must be AWS_IAM, never NONE
```

---

# PHASE 4 — Edge and interface

### TASK 4-01 — CloudFront
**DEPENDS:** 3-05
**HARD STOP: billable apply.**

Apply the distribution with both origins and both origin access controls.

**VERIFY:**
```bash
FURL=$(aws lambda get-function-url-config --function-name gapo-predict --query FunctionUrl --output text)
curl -s -o /dev/null -w "%{http_code}\n" -X POST "$FURL" -d '{}'
# MUST be 403. Any other code means the function is publicly invokable.
```

**STOP_IF:** that returns anything other than 403.

---

### TASK 4-02 — Publish the site
**DEPENDS:** 4-01

```bash
aws s3 sync web/ "s3://gapo-web-${ACCOUNT}/" --delete
aws cloudfront create-invalidation --distribution-id <ID> --paths "/*"
```

**VERIFY:**
```bash
DOMAIN=$(cd infra && terraform output -raw site_url)
curl -s -o /dev/null -w "%{http_code}\n" "$DOMAIN"           # 200
curl -s -X POST "$DOMAIN/api/" -H 'content-type: application/json' \
  -d '{"tickers":["AAPL","MSFT","NVDA","JPM","XOM","PG","COST","HD","V","KO"]}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(round(sum(d['weights'].values()),6))"
```

**CHECKPOINT.** The project is now demonstrable. Update `PROGRESS.md` with
the live URL and tell the user. Phases 5 onward are improvements to
something that already works.

---

# PHASE 5 — CI/CD

### TASK 5-01 — Deploy workflow
**DEPENDS:** 4-02

**DO:** Implement `.github/workflows/deploy.yml`. It must, in order: run the
golden test, create the ECR repository via `-target`, fetch the checkpoint,
build and push, `terraform plan` then `apply`, sync web assets, invalidate,
smoke test.

The `-target` step is not optional. Lambda cannot reference an image that
does not exist, and the image cannot be pushed to a registry that does not
exist.

**VERIFY:** One green run from a clean checkout.

---

### TASK 5-02 — Quality gates
**DEPENDS:** 5-01

Add to the pull request path: `pytest`, `tflint`, `checkov`, `trivy image`
failing on HIGH, `pip-audit`.

**VERIFY:** Introduce a deliberate error in `features.py` on a branch.
Confirm the pipeline goes red. Revert.

---

### TASK 5-03 — Destroy workflow
**DEPENDS:** 5-01

**DO:** Implement `destroy.yml` with typed confirmation. Do not run it.

---

# PHASE 6 — Observability

### TASK 6-01 — Structured logs
Emit one JSON line per request with `event`, `n`, `ms`, `cold`.
**VERIFY:** A Logs Insights `stats ... by cold` query returns rows.

### TASK 6-02 — Dashboard and alarms
Concurrency, duration percentiles, cold/warm split. Alarms for throttles,
predict errors, ingest failure. Route to SNS then email.
**VERIFY:** Deliberately break ingest. Confirm an alert arrives. Restore.

### TASK 6-03 — SLO document
**DO:** Write `docs/slo.md`. Include a specific availability target, a
latency target, the measurement window, the error budget, and what happens
when it is exhausted. Leave the numbers as `TBD` until Phase 7 supplies
them, then fill them in.
**VERIFY:** File exists with named metrics and a stated budget policy.

### TASK 6-04 — Synthetic canary
Scheduled Lambda calling `/api/` with a fixed basket every 5 minutes,
asserting on the response body and not only the status code.
**VERIFY:** Break the model path. Alert fires within 10 minutes. Restore.

---


---

# END OF PART 1

## Exit criteria

All of these must pass before opening `AGENT_PLAN_2_SCALE.md`.

```bash
# 1. Golden test passes
python3 -m pytest tests/test_golden.py -v

# 2. Site responds
DOMAIN=$(cd infra && terraform output -raw site_url)
curl -s -o /dev/null -w "%{http_code}\n" "$DOMAIN"                 # 200

# 3. API returns a valid allocation
curl -s -X POST "$DOMAIN/api/" -H 'content-type: application/json' \
  -d '{"tickers":["AAPL","MSFT","NVDA","JPM","XOM","PG","COST","HD","V","KO"]}' \
  | python3 -c "import sys,json;d=json.load(sys.stdin);assert abs(sum(d['weights'].values())-1)<1e-3;print('OK')"

# 4. Function URL is not publicly invokable
FURL=$(aws lambda get-function-url-config --function-name gapo-predict --query FunctionUrl --output text)
curl -s -o /dev/null -w "%{http_code}\n" -X POST "$FURL" -d '{}'   # must be 403

# 5. Concurrency ceiling is in place
aws lambda get-function-configuration --function-name gapo-predict \
  --query ReservedConcurrentExecutions                              # 50

# 6. Budget alarm exists
aws budgets describe-budgets --account-id "$(aws sts get-caller-identity --query Account --output text)" \
  --query 'Budgets[?BudgetName==`gapo-monthly`].BudgetLimit'

# 7. Pipeline is green
gh run list --workflow=deploy.yml --limit 1

# 8. Alarms exist
aws cloudwatch describe-alarms --alarm-name-prefix gapo- \
  --query 'MetricAlarms[].AlarmName'
```

## Handoff report

Write this to `PROGRESS.md` and tell the user before stopping.

- Live URL
- Whether the golden test passed at 1e-4, or which rung of the ladder was
  needed, or that a rung-5 exception was granted
- Any hard-coded universe size found in 0-08
- AWS spend to date
- Anything in `docs/adr/` recorded as an accepted limitation

**Stop here.** Part 2 begins with load testing, which costs real money.
Wait for the user to hand you `AGENT_PLAN_2_SCALE.md`.
