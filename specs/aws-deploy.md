# Spec: Full AWS production deploy

## Prerequisites

Complete `specs/phase0-local-runnable.md` first.
The golden test (`tests/test_golden.py`) must pass before this spec begins.

---

## What already exists

The infrastructure and CI/CD code is fully written. Do not rewrite it.

| File | What it does |
|------|-------------|
| `bootstrap/bootstrap.yaml` | CloudFormation — creates Terraform state bucket, artifact bucket, GitHub OIDC provider, deploy role |
| `infra/main.tf` | S3 market data, DynamoDB history, ECR repo |
| `infra/lambda.tf` | Predict + ingest Lambda functions, IAM roles, EventBridge schedules, CloudWatch alarms |
| `infra/cdn.tf` | CloudFront distribution, S3 web bucket, Lambda function URL wired as `/api/*` origin |
| `infra/scaling.tf` | `reserved_concurrency`, `provisioned_concurrency`, budget alarm, load test dashboard |
| `infra/outputs.tf` | `site_url`, `web_bucket`, `distribution_id`, `predict_function`, `ingest_function` |
| `.github/workflows/deploy.yml` | Full CI/CD: creates ECR, fetches checkpoint, builds image, terraform apply, publishes web, ingest, smoke test |
| `.github/workflows/destroy.yml` | Typed-confirmation teardown |

---

## Overview of gates

This deploy has three human approval gates. Everything between gates is agent work.

```
[Agent: validate + fix code]
      ↓
[HUMAN GATE 1: bootstrap the AWS account]
      ↓
[Agent: verify OIDC, upload checkpoint, create tfvars.example]
      ↓
[HUMAN GATE 2: set GitHub repo variables, trigger first deploy]
      ↓
[Agent: verify deploy, smoke test, document live URL]
      ↓
[HUMAN GATE 3: review live service, approve or request changes]
```

---

## Task 1 — Pre-deploy validation

**Owner:** backend-agent
**Inputs:** `infra/*.tf`, `.github/workflows/deploy.yml`, `app/Dockerfile`,
            `app/requirements.txt`, `tests/test_golden.py`
**Outputs:** `infra/terraform.tfvars.example`, `PROGRESS.md`

### What to do

**1a. Run the golden test to confirm Phase 0 is complete.**

```bash
cd app
STORAGE_BACKEND=local python3 -m pytest tests/test_golden.py -v
```

If it fails, stop and escalate to human: Phase 0 is not done.

**1b. Validate the Terraform configuration.**

```bash
cd infra
terraform init -backend=false
terraform validate
```

Fix any `terraform validate` errors. Do not change resource names, IAM policies,
or the CloudFront configuration — those are intentional. Only fix syntax/type errors.

**1c. Check the deploy workflow.**

Read `.github/workflows/deploy.yml` end to end. Verify:
- It references `vars.AWS_DEPLOY_ROLE` and `vars.TF_STATE_BUCKET` (set in GitHub — not in the file)
- `env.AWS_REGION` matches `infra/variables.tf` default (`eu-west-1`)
- The checkpoint fetch path matches: `s3://${PROJECT}-artifacts-${ACCOUNT}/models/full_model.pth`
- The `terraform apply` uses the image tag from `github.sha`

If anything is wrong, fix it and note the fix in `PROGRESS.md`.

**1d. Create `infra/terraform.tfvars.example`.**

This file documents the variables a human needs to set before their first deploy.
It must not contain real values. Write it as:

```hcl
# Copy to terraform.tfvars (gitignored) and fill in real values.
# Only budget_alert_email and region typically need to change.

region             = "eu-west-1"       # AWS region for all resources
budget_alert_email = "you@example.com" # Set this or you will not know when costs spike
monthly_budget_usd = 25
```

**1e. Create `PROGRESS.md` at repo root.**

```markdown
# Progress

Last updated: <ISO timestamp>
Current task: 1 — pre-deploy validation

## Completed
- [x] Phase 0 — local runnable (golden test passes)
- [x] Task 1 — pre-deploy validation

## Blocked
(none)

## Notes
- Golden test passed at atol=1e-4 for all 5 baskets
- terraform validate: <PASS or list of fixes>
- deploy.yml: <PASS or list of fixes>
```

### Acceptance criteria

1. `cd infra && terraform init -backend=false && terraform validate` outputs `Success`.
2. `tests/test_golden.py` passes.
3. `infra/terraform.tfvars.example` exists with the three variables above.
4. `PROGRESS.md` exists with task 1 checked off.
5. `ruff check app/` passes.

---

## HUMAN GATE 1 — Bootstrap the AWS account

**This is a hard stop. The agent cannot proceed past this point.**

The agent must escalate to human with these exact instructions:

---

Before the first deploy you need to:

**Step 1 — Create the bootstrap stack.**

This creates the Terraform state bucket, the artifact bucket, the GitHub OIDC provider,
and the deploy role. It costs nothing to create (S3 buckets bill on storage/requests,
not existence).

```bash
aws cloudformation deploy \
  --template-file bootstrap/bootstrap.yaml \
  --stack-name gapo-bootstrap \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
      GitHubOrg=<your-github-org> \
      GitHubRepo=<your-github-repo> \
      ProjectName=gapo
```

Then get the stack outputs:
```bash
aws cloudformation describe-stacks \
  --stack-name gapo-bootstrap \
  --query 'Stacks[0].Outputs'
```

You will see three outputs: `DeployRoleArn`, `TFStateBucketName`, `ArtifactBucketName`.

**Step 2 — Set GitHub repository variables** (not secrets — these are not sensitive).

In GitHub: repo Settings → Secrets and variables → Actions → Variables tab:
- `AWS_DEPLOY_ROLE` = the `DeployRoleArn` output
- `TF_STATE_BUCKET` = the `TFStateBucketName` output

**Step 3 — Respond to this task** with:
- Your `ArtifactBucketName`
- Your AWS region (if different from `eu-west-1`)
- Confirm you have set both GitHub variables

---

## Task 2 — Verify OIDC and upload checkpoint

**Owner:** backend-agent
**Depends on:** Human Gate 1 response
**Inputs:** human response containing `ArtifactBucketName` and region confirmation
**Outputs:** `.github/workflows/oidc-check.yml` (temporary), `PROGRESS.md` update

### What to do

**2a. Create a temporary OIDC verification workflow.**

Create `.github/workflows/oidc-check.yml`:

```yaml
name: OIDC Check (delete after one green run)
on:
  workflow_dispatch:
permissions:
  id-token: write
  contents: read
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ vars.AWS_DEPLOY_ROLE }}
          aws-region: eu-west-1
      - run: aws sts get-caller-identity
```

Push this file. The human will run it manually from the Actions tab to confirm OIDC works.
Tell them: "Go to Actions → OIDC Check → Run workflow. It should print an ARN containing
`gapo-github-deploy`. Reply here once it passes, then I will delete the workflow."

Once the human confirms it passed, delete `.github/workflows/oidc-check.yml` and commit.

**2b. Upload the checkpoint to the artifact bucket.**

The deploy workflow fetches the checkpoint from S3 at build time. Upload it now so the
workflow has it:

```bash
ARTIFACT_BUCKET=<from human response>
aws s3 cp protraderbot/InferenceApplication/full_model.pth \
  "s3://${ARTIFACT_BUCKET}/models/full_model.pth"
aws s3 ls "s3://${ARTIFACT_BUCKET}/models/"
```

Do not commit the `.pth` file. It is gitignored. Verify with:
```bash
git status --porcelain | grep -i "\.pth" && echo "FAIL: checkpoint staged" || echo "OK"
```

**2c. Update PROGRESS.md.**

```markdown
## Completed
- [x] Phase 0 — local runnable
- [x] Task 1 — pre-deploy validation
- [x] Task 2 — OIDC verified, checkpoint uploaded

## Notes
- OIDC: green run confirmed
- Checkpoint uploaded to: s3://<bucket>/models/full_model.pth
```

### Acceptance criteria

1. OIDC workflow ran green (confirmed by human before deleting it).
2. `aws s3 ls s3://<ARTIFACT_BUCKET>/models/full_model.pth` succeeds.
3. `.github/workflows/oidc-check.yml` is deleted.
4. No `.pth` file is staged in git.

---

## HUMAN GATE 2 — Trigger the first deploy

**This is a hard stop.**

The agent escalates with:

---

Everything is ready for the first deploy. Before triggering it, be aware:

**What will be created (all in `eu-west-1`):**
- ECR repository (~1.5 GB image stored = ~$1.50/month)
- S3 market data bucket, S3 web bucket (under $0.10/month at this scale)
- DynamoDB table (on-demand, near zero cost until load tested)
- Two Lambda functions (predict + ingest), CloudFront distribution
- EventBridge rules (daily ingest at 22:30 UTC, keep-warm ping every 5 min)
- CloudWatch log groups, alarms, dashboard
- AWS Budget alert at $25/month

**Estimated monthly cost:** ~$6 at normal usage. See README.md for the full breakdown.

**To trigger the deploy:**

Option A — GitHub Actions (recommended):
```
GitHub repo → Actions → Deploy → Run workflow
Check "Refresh market data after deploying" ✓
```

Option B — from the command line (requires Terraform installed locally):
```bash
# Not recommended for first run — use the workflow
```

**After the workflow completes (~8-10 minutes):**
- Reply with the CloudFront URL from the workflow summary
- Or run: `cd infra && terraform output site_url`

---

## Task 3 — Verify the live service

**Owner:** backend-agent
**Depends on:** Human Gate 2 response (live URL)
**Inputs:** live CloudFront URL from human
**Outputs:** `PROGRESS.md` update with live URL and smoke test results

### What to do

**3a. Smoke test the live API.**

```bash
SITE_URL=<from human response>

# Site loads
curl -s -o /dev/null -w "%{http_code}\n" "$SITE_URL"
# expect: 200

# API returns valid allocation
curl -s -X POST "${SITE_URL}/api/" \
  -H "content-type: application/json" \
  -d '{"tickers":["AAPL","MSFT","NVDA","JPM","XOM","PG","COST","HD","V","KO"]}' \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('status: ok')
print('weights sum:', round(sum(d['weights'].values()), 6))
print('cold start:', d.get('x-cold-start', 'n/a'))
for t, w in sorted(d['weights'].items(), key=lambda x: -x[1]):
    print(f'  {t}: {w:.4f}')
"
```

**3b. Verify function URL is not publicly accessible.**

```bash
FURL=$(aws lambda get-function-url-config \
  --function-name gapo-predict \
  --query FunctionUrl --output text)
CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$FURL" -d '{}')
echo "Direct function URL returned: $CODE"
# Must be 403 — any other code means the function is publicly invokable without CloudFront
```

If this returns anything other than 403, escalate to human immediately. Do not attempt
to fix it yourself — a publicly accessible function URL is a security issue.

**3c. Verify reserved concurrency.**

```bash
aws lambda get-function-configuration \
  --function-name gapo-predict \
  --query ReservedConcurrentExecutions
# expect: 50
```

**3d. Check the market data ingest result.**

```bash
aws lambda invoke \
  --function-name gapo-ingest \
  --cli-read-timeout 660 \
  --payload '{}' \
  /tmp/ingest.json
cat /tmp/ingest.json
# expect: {"snapshot": "...", "written": N, "failed": 0, "promoted": true, ...}
```

If `failed > 0`, record which tickers failed in `PROGRESS.md` — this is not a deploy
blocker (Yahoo Finance occasionally fails for individual tickers) but worth noting.

**3e. Final PROGRESS.md update.**

```markdown
## Completed
- [x] Phase 0 — local runnable
- [x] Task 1 — pre-deploy validation
- [x] Task 2 — OIDC verified, checkpoint uploaded
- [x] Task 3 — live service verified

## Live service
- URL: https://<cloudfront-domain>
- Image tag: <git sha>
- Reserved concurrency: 50
- Function URL direct access: 403 (correct)
- Ingest: <N> tickers written, <M> failed

## Notes
<any surprises, failed tickers, tolerance needed for golden test, etc.>
```

### Acceptance criteria

1. `curl -s -o /dev/null -w "%{http_code}" "$SITE_URL"` returns 200.
2. POST to `/api/` with 10 tickers returns weights summing to 1.0 ± 0.001.
3. Direct function URL returns 403.
4. `ReservedConcurrentExecutions` is 50.
5. `PROGRESS.md` updated with live URL and all checks.

---

## Constraints for all tasks

- Never run `terraform apply -auto-approve` locally. Use the workflow for applies.
  The only exception is `terraform apply -auto-approve -target=aws_ecr_repository.app`
  which the deploy workflow does internally and is safe.
- Never commit `*.pth`, `*.pt`, or anything under `app/artifacts/`.
- Never set a Lambda function URL `authorization_type = "NONE"`.
- Never raise `reserved_concurrency` above 50 without an explicit human instruction.
- If `terraform validate` reveals issues in files not listed here, fix them in a separate
  commit with a clear message explaining what was wrong.
- If a deploy workflow run fails, read the workflow logs, diagnose the root cause, fix the
  code, and push. Do not re-trigger the workflow blindly.

---

## After this spec is complete

The service is live. The next steps (from `AGENT_PLAN_1_BUILD.md` Phase 5 onwards) are:
- CI/CD quality gates: `tflint`, `checkov`, `trivy`, `pip-audit` on pull requests
- Synthetic canary: scheduled Lambda calling `/api/` every 5 minutes
- SLO document: `docs/slo.md`
- Load testing: separate plan in `AGENT_PLAN_2_SCALE.md`

Do not open `AGENT_PLAN_2_SCALE.md` until this spec is fully complete and the live URL
is recorded in `PROGRESS.md`.
