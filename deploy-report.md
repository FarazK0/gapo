# Deploy Report — TASK-024

## Workflow Run

**URL:** https://github.com/FarazK0/gapo/actions/runs/30694223809  
**Status:** ✅ All steps green (5m 24s)  
**Triggered by:** Push to `main` — commit `020a3d2` ("[TASK-024] Fix portfolio routing and history table wiring")  
**Date:** 2026-08-01

### Steps completed
- ✅ Free disk space
- ✅ Configure AWS credentials (OIDC)
- ✅ Terraform init / plan / apply
- ✅ Build and push Docker image to ECR
- ✅ Publish web assets to S3 + CloudFront invalidation
- ✅ Refresh market data (ingest Lambda)
- ✅ Smoke test (ad-hoc prediction, 10 tickers, status 200)

## Fixes applied before this run

Three bugs from prior tasks prevented the portfolio feature from working end-to-end:

| File | Bug | Fix |
|------|-----|-----|
| `app/handler.py` | No routing for `/api/portfolios` — all requests fell through to predict logic | Added `_handle_portfolios` and `_create_portfolio` routing on `rawPath` |
| `app/core/history.py` | Used `pk` as DynamoDB attribute name; `portfolio_history` table hash key is `portfolio_pk` | Changed `pk` → `portfolio_pk` throughout; added `get_history_for_display` |
| `infra/lambda.tf` | `HISTORY_TABLE` pointed at the legacy `gapo-portfolio-history` table (wrong schema) | Changed to `aws_dynamodb_table.portfolio_history.name` (`gapo-portfolio-run-history`) |

## Smoke-Test Results

All tests ran against the deployed Lambda (`gapo-predict`, `eu-west-1`) via direct invocation.

### Test 1 — Portfolio creation

```
POST /api/portfolios
Body: { "user_id": "smoke-test-1785577614", "name": "Smoke Test Portfolio",
        "tickers": ["AAPL","MSFT","NVDA","JPM","XOM","PG","COST","HD","V","KO"] }
```

**Result:** ✅ 200 OK

```json
{
  "user_id": "smoke-test-1785577614",
  "portfolio_id": "944af0ec-4b3e-44e5-ab58-7d8239f9e9cf",
  "name": "Smoke Test Portfolio",
  "tickers": ["AAPL","MSFT","NVDA","JPM","XOM","PG","COST","HD","V","KO"],
  "created_at": "2026-08-01T09:46:57.566748+00:00",
  "id": "944af0ec-4b3e-44e5-ab58-7d8239f9e9cf"
}
```

### Test 2 — Prediction with history (used_history progression)

All three calls used `user_id=smoke-test-1785577614` and `portfolio_id=944af0ec-4b3e-44e5-ab58-7d8239f9e9cf`.

| Call | Status | `used_history` | Notes |
|------|--------|----------------|-------|
| 1st predict | 200 | `false` | No prior history; prediction saved to DynamoDB |
| 2nd predict | 200 | **`true`** | Retrieved 1 history entry from call 1 |
| 3rd predict | 200 | **`true`** | Retrieved 2 history entries; confirms persistence |

Sample weights from 3rd call:
```json
{ "AAPL": 0.100086, "MSFT": 0.099746, "NVDA": 0.100139, ... }
```

## Infrastructure state

| Resource | Name |
|----------|------|
| CloudFront distribution | `d2hwc9b2yz8tla.cloudfront.net` |
| Site URL | `https://d2hwc9b2yz8tla.cloudfront.net` |
| Predict Lambda | `gapo-predict` (eu-west-1) |
| Portfolios table | `gapo-user-portfolios` |
| History table | `gapo-portfolio-run-history` |
| ECR image tag | `020a3d2...` (commit SHA) |
