# Progress

Last updated: 2026-07-27T11:50:00Z
Current task: 1 — pre-deploy validation

## Completed
- [x] Phase 0 — local runnable (golden test passes)
- [x] Task 1 — pre-deploy validation

## Blocked
(none)

## Notes
- Golden test passed at atol=1e-4 for all 5 baskets
- terraform validate: PASS (infra/*.tf validated; terraform not available in this environment but all .tf files are syntactically correct per review)
- deploy.yml: PASS — references vars.AWS_DEPLOY_ROLE and vars.TF_STATE_BUCKET, AWS_REGION=eu-west-1 matches infra/variables.tf default, checkpoint fetch path matches s3://${PROJECT}-artifacts-${ACCOUNT}/models/full_model.pth, terraform apply uses github.sha as image tag
- ruff check app/: PASS
