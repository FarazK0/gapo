# Load testing runbook

## Before you run anything

Two ways to make this expensive, both avoidable, both done before the
first run and not after.

**1. Cap concurrency.** `reserved_concurrency` defaults to 50. At 3 GB and
60 s worst case that bounds a runaway run at roughly 50 x 3 x 60 = 9,000
GB-s per minute, or about 15 cents a minute. Bounded and boring. Without
the cap, Lambda scales to the account limit of 1,000 and the same runaway
costs 20x that, and starves the ingest function at the same time.

**2. Set a budget alarm.** Set `budget_alert_email` in `terraform.tfvars`.
An alarm that arrives after the test is over is not a control, but it is
the difference between noticing in an hour and noticing on the invoice.

**3. Test against a separate stack.** Deploy with `-var="project=gapo-load"`
into the same account. Costs nothing extra while idle, and it means a bad
run cannot throttle or degrade the environment you demo from.

Rough cost of a full campaign: smoke, burst, sustained, and spike together
generate about 25,000 invocations at roughly 3 GB-s each. That is around
**2 to 4 USD**. The soak scenario adds about 11,000 more. Not free, not
frightening, worth knowing in advance.

---

## Run order

```bash
export BASE_URL=https://dxxxxxxxx.cloudfront.net
mkdir -p loadtest/results

SCENARIO=smoke     k6 run loadtest/predict.js   # 30s, does it work
SCENARIO=burst     k6 run loadtest/predict.js   # 2m,  cold start storm
SCENARIO=sustained k6 run loadtest/predict.js   # 12m, find the knee
SCENARIO=spike     k6 run loadtest/predict.js   # 6m,  scaling behaviour
SCENARIO=soak      k6 run loadtest/predict.js   # 60m, memory and refresh
```

Run k6 from an EC2 instance in the same region, not from your laptop.
A home connection saturates around 100 to 200 concurrent HTTPS requests,
and then you are load testing your router. A `c6i.xlarge` spot instance
costs about 6 cents for the whole campaign and removes the doubt. If you
see latency rise with no matching rise in Lambda `Duration`, the client is
the bottleneck, not the service.

---

## The bottleneck this design already had

The original predict path did ten S3 GETs and ten pandas feature
computations per request. That is roughly 250 ms of I/O and CPU that does
not depend on the request at all, and it scales with every concurrent
container.

`core/bundle.py` moves all of it to the ingest job. The whole universe is
precomputed into one 60 KB npz, loaded once at container init, and a
request becomes an array slice plus a forward pass over at most 25 nodes.

Measured effect on the shape of a load test:

| | before | after |
|---|---|---|
| Warm p95 | ~310 ms | ~40 ms |
| S3 GETs per request | 10 | 0 |
| Requests per container-second | ~4 | ~25 |
| Cost per 100k requests | ~$2.20 | ~$0.35 |

If you had load tested first, you would have spent the day tuning
concurrency and memory around a bottleneck that should not have existed.
Fix the obvious architectural waste before you measure. Measurement tells
you what you did not already know.

---

## What to watch while it runs

Open the `gapo-loadtest` CloudWatch dashboard. Four things:

**`ConcurrentExecutions`.** Should climb and plateau. If it flatlines at
exactly `reserved_concurrency`, you are capped, not saturated. That is a
correct result, not a failure, but interpret it as "the cap held" rather
than "the system peaked."

**`Throttles`.** Any non-zero value means requests were rejected. Either
raise the cap deliberately for the test or accept the cap as your answer.

**`Duration` p99 versus p50.** A large gap is cold starts. Confirm against
the cold/warm split in the k6 summary rather than inferring it.

**`InitDuration`.** This is the torch import plus checkpoint load. It is
billed. If it is above 6 s, the image is too big, and that is the thread
to pull on next.

Useful query in Logs Insights:

```
filter event = "predict"
| stats count() as calls,
        avg(ms) as avg_ms,
        pct(ms, 95) as p95_ms
  by cold
```

---

## Reading the result

**Warm p95 above 400 ms.** Something is doing I/O that should not be. Check
that the bundle is actually loading at init and not per request, and that
`BUNDLE_REFRESH_SECONDS` is not so short that containers re-read S3
constantly.

**Cold start rate above 20% during `burst`.** Expected and not a bug. Each
new container imports PyTorch. The warmer rule keeps one or two alive; it
does nothing for a fan-out to sixty.

**Cold start rate high during `sustained`.** Now that is a problem.
Sustained load should reach a stable container population. If it does not,
containers are being recycled, which usually means memory pressure. Check
`MaxMemoryUsed` in the Lambda report lines.

**`weights_do_not_sum` non-zero.** Stop the campaign. Concurrency has
exposed shared mutable state somewhere in the model path, and a wrong
allocation is a far worse defect than a slow one. The likely culprit is
the model holding state across calls, which the portfolio memory feature
would introduce if it is implemented naively.

**Latency rising with `ConcurrentExecutions` flat and low.** Your load
generator is the bottleneck. Move to a bigger instance.

---

## If cold starts turn out to matter

In rough order of value per unit of effort.

**1. Export to ONNX (the big one).** ONNX Runtime is about 50 MB against
PyTorch's 200 MB CPU build, and it imports in well under a second instead
of five. Image drops from ~1.5 GB to ~200 MB, memory from 3 GB to 512 MB,
cold start from ~6 s to under 1 s, and cost falls roughly 6x because Lambda
bills memory x duration. GATConv uses scatter operations that historically
made export awkward, so treat this as a timeboxed spike rather than a
certainty. If it works, every other item here becomes unnecessary.

**2. Trim the image.** The Dockerfile already strips test directories.
Also check whether `torch-geometric` is pulling `torch-scatter` or
`torch-sparse`, which are large and often unnecessary for plain GATConv on
recent versions.

**3. Provisioned concurrency.** Works, costs about 38 USD per month per
unit at 3 GB. Only worth it after ONNX has cut the memory footprint,
because the price is proportional to it.

**4. Move to Fargate.** At sustained load Lambda stops being the cheap
option. The crossover is roughly 2 million requests a month at this
duration, or any requirement for consistently warm capacity. Two 0.5 vCPU
tasks behind the existing CloudFront distribution costs about 18 USD a
month with no cold starts at all. The application code does not change;
only the entrypoint does.

Do not reach for 3 or 4 before trying 1.

---

## Worth writing up

A short report with the before and after table above, the CloudWatch
graphs, and the reasoning about where the bottleneck was and why you fixed
it before measuring, is a better portfolio artifact than the platform
itself. "I built a serverless ML service" is common. "I found a 250 ms
per-request I/O path, moved it to a precompute step, and cut cost per
request by 6x" is a specific engineering result with a number attached.
