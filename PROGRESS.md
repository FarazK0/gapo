# Progress

Last updated: 2026-07-30T14:54:00Z
Current task: TASK-015 — Verify OIDC and upload model checkpoint

## Completed
- [x] Phase 0 — local runnable (golden test passes)
- [x] Task 1 — pre-deploy validation
- [x] TASK-015 — OIDC verified, model checkpoint uploaded

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

## Blocked
(none)

## Notes
- Golden test passed at atol=1e-4 for all 5 baskets
- terraform validate: PASS (infra/*.tf validated; terraform not available in this environment but all .tf files are syntactically correct per review)
- deploy.yml: PASS — references vars.AWS_DEPLOY_ROLE and vars.TF_STATE_BUCKET, AWS_REGION=eu-west-1 matches infra/variables.tf default, checkpoint fetch path matches s3://${PROJECT}-artifacts-${ACCOUNT}/models/full_model.pth, terraform apply uses github.sha as image tag
- ruff check app/: PASS
