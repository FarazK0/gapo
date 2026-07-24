// k6 load test for the portfolio prediction endpoint.
//
//   SCENARIO=smoke     k6 run loadtest/predict.js
//   SCENARIO=burst     k6 run loadtest/predict.js
//   SCENARIO=sustained k6 run loadtest/predict.js
//   SCENARIO=spike     k6 run loadtest/predict.js
//   SCENARIO=soak      k6 run loadtest/predict.js
//
// Required: BASE_URL=https://dxxxx.cloudfront.net
//
// Read loadtest/README.md before running anything above `smoke`. There are
// two ways to make this expensive and both are avoidable.

import http from "k6/http";
import { check } from "k6";
import { Trend, Rate, Counter } from "k6/metrics";
import { randomSeed } from "k6";

const BASE = __ENV.BASE_URL;
if (!BASE) throw new Error("Set BASE_URL to your CloudFront domain.");

const SCENARIO = __ENV.SCENARIO || "smoke";

// -------------------------------------------------------------------
// Metrics
//
// The interesting number is not overall p95. It is the split between cold
// and warm, because they have different causes and different fixes. A
// single blended histogram hides a bimodal distribution and leads you to
// tune the wrong thing.
// -------------------------------------------------------------------
const coldLatency = new Trend("latency_cold", true);
const warmLatency = new Trend("latency_warm", true);
const coldRate = new Rate("cold_start_rate");
const throttled = new Counter("throttled_429");
const stale = new Counter("stale_data_503");
const badWeights = new Counter("weights_do_not_sum");

// -------------------------------------------------------------------
// Request shaping
//
// Uniform random tickers is not a realistic load profile and it is not a
// harmless simplification: real selections are heavily concentrated in a
// few mega caps. Skewing the draw keeps any future per-basket caching
// honest, because uniform traffic makes every cache look useless.
// -------------------------------------------------------------------
const UNIVERSE = [
  "AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","BRK-B","JPM","V",
  "UNH","XOM","JNJ","WMT","MA","PG","AVGO","HD","CVX","MRK",
  "ABBV","COST","PEP","KO","ADBE","CSCO","CRM","MCD","TMO","ACN",
  "BAC","NFLX","AMD","LIN","ABT","DIS","WFC","TXN","PM","DHR",
  "VZ","INTC","CAT","NEE","AMGN","IBM","GE","QCOM","NOW","UNP",
];

randomSeed(42); // reproducible runs, so two tests are comparable

function pickTickers() {
  // Zipf-ish: index chosen by squaring a uniform draw, which biases hard
  // toward the front of the list.
  const n = 8 + Math.floor(Math.random() * 5); // 8 to 12 holdings
  const chosen = new Set();
  while (chosen.size < n) {
    const u = Math.random();
    chosen.add(UNIVERSE[Math.floor(u * u * UNIVERSE.length)]);
  }
  return [...chosen];
}

// -------------------------------------------------------------------
// Scenarios
// -------------------------------------------------------------------
const scenarios = {
  // Does it work at all. Run this first, every time.
  smoke: {
    executor: "constant-vus",
    vus: 1,
    duration: "30s",
  },

  // The one that matters. Zero to 60 requests per second with no ramp,
  // against a cold or lightly warmed function. Every container that spins
  // up pays the full PyTorch import. This is what a launch, a link on
  // social media, or a demo to an interviewer actually looks like.
  burst: {
    executor: "constant-arrival-rate",
    rate: 60,
    timeUnit: "1s",
    duration: "2m",
    preAllocatedVUs: 150,
    maxVUs: 400,
  },

  // Steady state. Find the rate at which p95 stops being flat.
  sustained: {
    executor: "ramping-arrival-rate",
    startRate: 5,
    timeUnit: "1s",
    preAllocatedVUs: 100,
    maxVUs: 400,
    stages: [
      { target: 10, duration: "2m" },
      { target: 25, duration: "3m" },
      { target: 50, duration: "3m" },
      { target: 50, duration: "3m" },
      { target: 0, duration: "1m" },
    ],
  },

  // Warm, then hit it. Isolates scaling behaviour from cold start, because
  // the baseline containers already exist when the spike lands.
  spike: {
    executor: "ramping-arrival-rate",
    startRate: 5,
    timeUnit: "1s",
    preAllocatedVUs: 200,
    maxVUs: 600,
    stages: [
      { target: 5, duration: "3m" },
      { target: 120, duration: "10s" },
      { target: 120, duration: "1m" },
      { target: 5, duration: "2m" },
    ],
  },

  // Slow burn for an hour. Looking for memory growth in the torch process
  // and for the bundle refresh to behave across a snapshot boundary.
  soak: {
    executor: "constant-arrival-rate",
    rate: 3,
    timeUnit: "1s",
    duration: "60m",
    preAllocatedVUs: 20,
    maxVUs: 50,
  },
};

export const options = {
  scenarios: { [SCENARIO]: scenarios[SCENARIO] },
  thresholds: {
    // Warm path is pure CPU now: a slice, a corrcoef, and a forward pass
    // over at most 25 nodes. If this fails, something is doing I/O that
    // should not be.
    "latency_warm{expected_response:true}": ["p(95)<400"],
    // Cold path is dominated by the torch import.
    latency_cold: ["p(95)<9000"],
    http_req_failed: ["rate<0.01"],
    throttled_429: ["count<1"],
    weights_do_not_sum: ["count<1"],
  },
  summaryTrendStats: ["avg", "min", "med", "p(90)", "p(95)", "p(99)", "max"],
};

export default function () {
  const res = http.post(
    `${BASE}/api/`,
    JSON.stringify({ tickers: pickTickers(), session_id: `k6-${__VU}` }),
    {
      headers: { "content-type": "application/json" },
      tags: { name: "predict" },
      timeout: "70s",
    }
  );

  const cold = res.headers["X-Cold-Start"] === "1";
  coldRate.add(cold);
  (cold ? coldLatency : warmLatency).add(res.timings.duration);

  if (res.status === 429) throttled.add(1);
  if (res.status === 503) stale.add(1);

  check(res, {
    "status 200": (r) => r.status === 200,
    "has weights": (r) => {
      if (r.status !== 200) return false;
      try {
        return Object.keys(JSON.parse(r.body).weights || {}).length > 0;
      } catch {
        return false;
      }
    },
  });

  // Correctness under load, not just latency. A model that returns weights
  // summing to 0.87 when concurrency is high is a much worse finding than
  // a slow p99, and a latency-only test will never see it.
  if (res.status === 200) {
    try {
      const w = Object.values(JSON.parse(res.body).weights);
      const total = w.reduce((a, b) => a + b, 0);
      if (Math.abs(total - 1) > 1e-3) badWeights.add(1);
    } catch {
      badWeights.add(1);
    }
  }
}

export function handleSummary(data) {
  const m = data.metrics;
  const g = (k, s = "p(95)") => (m[k] && m[k].values[s] ? m[k].values[s].toFixed(0) : "n/a");

  const report = [
    ``,
    `  scenario          ${SCENARIO}`,
    `  requests          ${m.http_reqs ? m.http_reqs.values.count : 0}`,
    `  failed            ${m.http_req_failed ? (m.http_req_failed.values.rate * 100).toFixed(2) : "0"}%`,
    `  cold starts       ${m.cold_start_rate ? (m.cold_start_rate.values.rate * 100).toFixed(1) : "0"}%`,
    ``,
    `  warm  p50/p95/p99 ${g("latency_warm", "med")} / ${g("latency_warm")} / ${g("latency_warm", "p(99)")} ms`,
    `  cold  p50/p95/p99 ${g("latency_cold", "med")} / ${g("latency_cold")} / ${g("latency_cold", "p(99)")} ms`,
    ``,
    `  throttled (429)   ${m.throttled_429 ? m.throttled_429.values.count : 0}`,
    `  stale data (503)  ${m.stale_data_503 ? m.stale_data_503.values.count : 0}`,
    `  bad weight sums   ${m.weights_do_not_sum ? m.weights_do_not_sum.values.count : 0}`,
    ``,
  ].join("\n");

  return {
    stdout: report,
    [`loadtest/results/${SCENARIO}-${Date.now()}.json`]: JSON.stringify(data, null, 2),
  };
}
