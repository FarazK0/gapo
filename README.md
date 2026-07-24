# Graph Attention Portfolio Optimizer, hosted

Inference-only deployment of the GAT portfolio model. Fully serverless,
entirely on AWS, one click to deploy.

The desktop Tkinter app becomes a static page on CloudFront. The model runs
in a Lambda container. Yahoo Finance is called once a day by a scheduled job
rather than once per user request.

```
browser
   │  https
   ▼
CloudFront ──────► S3 (static site, private, OAC)
   │
   └── /api/* ────► Lambda function URL (predict)
                      │ SigV4, IAM auth, reachable only via CloudFront
                      ├─► S3 market data cache   (read)
                      └─► DynamoDB history       (write)

EventBridge 22:30 UTC Mon-Fri ──► Lambda (ingest) ──► Yahoo ──► S3 cache
EventBridge every 5 min       ──► Lambda (predict) warm ping
```

There is no VPC, no NAT gateway, no load balancer, and no Kubernetes.
Each of those would have added more cost than the entire rest of the stack.

---

## Cost

| Item | Monthly (USD) |
|---|---|
| Lambda predict, 3 GB, ~2 s per call, 5k calls | 1.50 |
| Lambda warmer, 8.6k pings a month | 0.40 |
| Lambda ingest, 22 runs at ~4 min | 0.90 |
| S3, market data plus site, under 5 GB | 0.20 |
| DynamoDB on demand, low volume | 0.30 |
| CloudFront, PriceClass_100, a few GB | 1.00 |
| ECR storage, 10 images at 1.5 GB | 1.50 |
| CloudWatch logs, 14 day retention | 0.50 |
| **Total** | **~6** |

Set `keep_warm = false` and it drops to about 5 with 6 to 10 second cold
starts. The largest single line is ECR, which is why the lifecycle policy
caps it at 10 images.

---

## Deploy

### Once, ever

1. **Bootstrap the account.** In CloudFormation, create a stack from
   `bootstrap/bootstrap.yaml`. It creates the Terraform state bucket, the
   artifact bucket, the GitHub OIDC provider, and the deploy role. Set
   `GitHubOrg` and `GitHubRepo` to yours.

2. **Upload the checkpoint.**
   ```
   aws s3 cp full_model.pth s3://gapo-artifacts-<ACCOUNT_ID>/models/full_model.pth
   ```
   The checkpoint stays out of git deliberately. It is fetched at build time
   and baked into the image, so the image digest pins code and model together.

3. **Set two repository variables** under Settings, Secrets and variables,
   Actions, Variables. Both values are printed in the CloudFormation outputs.
   - `AWS_DEPLOY_ROLE`
   - `TF_STATE_BUCKET`

   There are no secrets to set. Authentication is OIDC, so no AWS access key
   ever exists.

4. **Drop in your model code.** Copy `model.py` from your repository over
   `app/core/model.py`. Then open `app/core/predictor.py` and set
   `MODEL_CLASS` and `MODEL_KWARGS` to match how you construct the model in
   `training50.py`.

### Every time after

Actions tab, **Deploy**, **Run workflow**. That is the one click.

The workflow creates the registry, fetches the checkpoint, builds and pushes
the image, applies Terraform, publishes the site, refreshes market data, and
runs a smoke test against the live endpoint. It prints the URL in the job
summary. First run takes about 12 minutes, mostly CloudFront propagation.
Subsequent runs are 4 to 6.

**Destroy** tears it all down. Type `destroy` to confirm. The state bucket,
artifact bucket, and deploy role survive, so redeploying is one click again.

---

## What you must verify before trusting the output

The infrastructure is the easy half. The failure mode that matters is
train/serve skew, and it is silent. A model fed features computed slightly
differently from training will still return a clean, plausible, wrong
allocation. Nothing crashes.

Three specific risks:

**1. Feature parity.** `app/core/features.py` reimplements the six features.
It is not enough to review it. Delete the duplicated logic from
`training50.py` and import this module there. One definition, both paths.

**2. Graph parity.** `TOP_K` in `app/core/graph.py` must match training, as
must the decision to rank on absolute rather than signed correlation.

**3. Numerical parity.** Before your first real deploy, run the same basket
through your local `portfolio_rebalancer.py` and through
`predictor.run()`, and diff the weights. They should agree to about 1e-4.
If they do not, one of the two above is wrong. Write this as a test and keep
it in CI.

The other thing to check: `auto_adjust=True` in `core/ingest.py`. If
`downloadstocks50.py` used unadjusted prices, every split in the history
becomes a fake 50 percent single-week loss in your serving features.

---

## Design notes

**Why the model is not retrained per user.** The BiGRU encoder shares weights
across stocks and GATConv is permutation-equivariant over nodes. That is why
one checkpoint trained on 50 tickers already runs on an arbitrary subset.
Per-user fine-tuning is a real future feature and it changes the architecture
substantially: it needs GPU compute, per-tenant weight storage, and a warm
model cache. Do not build for it until users ask.

**Why a function URL instead of API Gateway.** API Gateway adds a hop, a
price, and a 29 second timeout for no benefit here. CloudFront signing its
requests to the function URL with an origin access control gives the same
protection at zero marginal cost, and the single origin removes CORS from the
picture entirely.

**Why the snapshot pointer.** Ingest writes every ticker under a dated prefix
and only then flips `current.json`. A half-finished or broken refresh never
becomes the data that inference reads. `store.assert_fresh` refuses anything
older than ten days, so the system degrades to an honest error rather than
quietly pricing a portfolio off month-old data.

**Why the checkpoint is baked into the image.** No cold start download, and
a Lambda rollback restores the exact model that was serving before. Model
version and code version become one thing you cannot desynchronise.

**Why `weights_only=True`.** `torch.load` on an untrusted file is arbitrary
code execution. The desktop app's "Load Model (.pth)" button cannot exist on
a server, and this flag is the guardrail that stops it reappearing by
accident.

---

## Tightening IAM

The bootstrap deploy role uses `PowerUserAccess`, which is fine for a solo
project in your own account and not fine for anything else. When you are past
initial setup, replace it with a policy scoped to the services actually used
(`ecr:*`, `lambda:*`, `s3:*`, `dynamodb:*`, `cloudfront:*`, `events:*`,
`logs:*`, `iam:*Role*` on `gapo-*`) and narrow the OIDC trust condition from
`repo:owner/repo:*` to `repo:owner/repo:ref:refs/heads/main`.

---

## Scope and legal

The output is a concrete allocation across named securities. In Ireland and
the EU, providing investment advice or portfolio management is a regulated
activity under MiFID II, supervised by the Central Bank of Ireland. This is
not legal advice. If this ever moves beyond a research demonstration and
personal use, get a real opinion before it does. The disclaimer in the footer
of `web/index.html` is a starting point and not a substitute.
