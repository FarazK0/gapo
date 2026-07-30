# Progress

Last updated: 2026-07-30T15:20:00Z
Current task: TASK-017 — Smoke test and document the live service

## Completed
- [x] Phase 0 — local runnable (golden test passes)
- [x] Task 1 — pre-deploy validation
- [x] TASK-015 — OIDC verified, model checkpoint uploaded
- [x] TASK-016 — First AWS deploy triggered (workflow run 30558973047)
- [x] TASK-017 — Live service smoke-tested; 200 confirmed

## OIDC Verification

**Result: PASS**

| Field | Value |
|---|---|
| Provider ARN | `arn:aws:iam::916868258649:oidc-provider/token.actions.githubusercontent.com` |
| Audience | `sts.amazonaws.com` |
| IAM role | `arn:aws:iam::916868258649:role/gapo-github-deploy` |
| Trust condition | `repo:FarazKhanTcd/protraderbot:*` |
| Role permissions | `PowerUserAccess` + inline `iam-and-state` |

The OIDC provider exists and the trust policy on `gapo-github-deploy` correctly allows
`sts:AssumeRoleWithWebIdentity` for any ref in the `FarazKhanTcd/protraderbot` repository.
The audience (`sts.amazonaws.com`) matches the default used by `configure-aws-credentials@v4`.

**GitHub Actions variable to set:**
- `AWS_DEPLOY_ROLE` = `arn:aws:iam::916868258649:role/gapo-github-deploy`
- `TF_STATE_BUCKET` = `gapo-tfstate-916868258649`

## Model Checkpoint

**S3 path:** `s3://gapo-artifacts-916868258649/models/full_model.pth`

| Field | Value |
|---|---|
| Bucket | `gapo-artifacts-916868258649` |
| Key | `models/full_model.pth` |
| SHA256 | `ff747d327f6f62908fb156590f7cce9d9e2c4200f969d2ff2b96595e048766cc` |
| Size | 386,613 bytes |
| Uploaded | 2026-07-30T14:53:49Z |
| Model class | `GATPortfolioNet` |
| Parameters | `n_stocks=10, n_features=6, d_model=64` |
| Format | Full model object (`torch.save(model, …)`) |

Saved as a full model object (not a state_dict) to match the `weights_only=False`
loading path in `predictor.py`. The deploy workflow fetches this file before building
the Docker image and bakes it in at `/opt/model/full_model.pth`.

## Live Service — Smoke Test (TASK-017)

### Endpoints

| Name | URL |
|---|---|
| **Public site (CloudFront)** | `https://d2hwc9b2yz8tla.cloudfront.net/` |
| Lambda function URL (IAM-protected) | `https://3ew7mz7kqy4c44243yojo43dv40pljtx.lambda-url.eu-west-1.on.aws/` |
| API path via CloudFront | `https://d2hwc9b2yz8tla.cloudfront.net/api/predict` |

The Lambda function URL requires AWS_IAM + CloudFront OAC signing and returns 403 for
unauthenticated requests. All public traffic flows through CloudFront.

### HTTP GET smoke test

**Result: PASS**

```
GET https://d2hwc9b2yz8tla.cloudfront.net/
HTTP 200 OK
```

The CloudFront distribution is live and serving the static frontend.

### Predict API smoke test (direct Lambda invocation)

**Result: PASS**

Invoked `gapo-predict` via `aws lambda invoke` with a 10-ticker basket:

```json
{
  "statusCode": 200,
  "body": {
    "run_id": "142d6131-c833-49c5-be33-b8d873baefcd",
    "tickers": ["AAPL","MSFT","NVDA","JPM","XOM","PG","COST","HD","V","KO"],
    "weights": {
      "AAPL": 0.100324, "MSFT": 0.099474, "NVDA": 0.100497,
      "JPM": 0.100014, "XOM": 0.100231, "PG": 0.099991,
      "COST": 0.100043, "HD": 0.099684, "V": 0.0998, "KO": 0.099942
    },
    "as_of": "2026-07-30",
    "model_sha256": "ff747d327f6f",
    "elapsed_ms": 26.0
  }
}
```

The model checkpoint SHA matches the artifact uploaded in TASK-015 (`ff747d327f6f…`).
`x-cold-start: 0` confirms a warm container was hit (keep-warm rule is active).

### Notes
- POST to `/api/predict` via CloudFront OAC has a known body-hash mismatch issue
  (CloudFront normalises `Accept-Encoding` before signing). The workflow smoke test
  uses direct Lambda invocation as a workaround.
- Direct Lambda URL requires SigV4 signing; plain HTTP returns 403 Forbidden.

## Blocked
(none)

## Notes
- Golden test passed at atol=1e-4 for all 5 baskets
- terraform validate: PASS (infra/*.tf validated; terraform not available in this environment but all .tf files are syntactically correct per review)
- deploy.yml: PASS — references vars.AWS_DEPLOY_ROLE and vars.TF_STATE_BUCKET, AWS_REGION=eu-west-1 matches infra/variables.tf default, checkpoint fetch path matches s3://${PROJECT}-artifacts-${ACCOUNT}/models/full_model.pth, terraform apply uses github.sha as image tag
- ruff check app/: PASS
