import { check } from "k6";
import exec from "k6/execution";
import http from "k6/http";
import { Counter, Rate, Trend } from "k6/metrics";

const DEFAULT_BASE_URL = "https://agent-production-9f62.up.railway.app";
const allowedVus = new Set([10, 50]);

function abort(message) {
    exec.test.abort(message);
    throw new Error(message);
}

function baseUrl(raw) {
    const value = (raw || DEFAULT_BASE_URL).trim().replace(/\/+$/, "");
    if (!/^https:\/\/[^/]+(?:\/.*)?$/.test(value)) {
        abort("BASE_URL must be an https:// URL");
    }
    return value;
}

function parseJson(response) {
    try {
        return response.json();
    } catch (_error) {
        return null;
    }
}

const vus = Number.parseInt(__ENV.BASELINE_VUS || "", 10);
if (!allowedVus.has(vus)) {
    abort("BASELINE_VUS is required and must be exactly 10 or 50");
}

const target = baseUrl(__ENV.BASE_URL);
const allowNotReady = (__ENV.ALLOW_NOT_READY || "false").toLowerCase() === "true";

const healthDuration = new Trend("health_duration", true);
const readyDuration = new Trend("ready_duration", true);
const healthErrors = new Rate("health_errors");
const readyErrors = new Rate("ready_errors");
const healthRequests = new Counter("health_requests");
const readyRequests = new Counter("ready_requests");

const thresholds = {
    health_errors: ["rate==0"],
    health_duration: ["p(95)<1000", "p(99)<2000"],
    ready_duration: ["p(95)<10000", "p(99)<15000"],
};
if (!allowNotReady) {
    thresholds.ready_errors = ["rate==0"];
}

export const options = {
    discardResponseBodies: false,
    maxRedirects: 0,
    scenarios: {
        public_baseline: {
            executor: "per-vu-iterations",
            vus,
            iterations: 1,
            maxDuration: "30s",
            gracefulStop: "0s",
        },
    },
    summaryTrendStats: ["avg", "med", "p(90)", "p(95)", "p(99)", "max"],
    thresholds,
};

const requestParams = {
    headers: {
        Accept: "application/json",
        "X-Copilot-Load-Test": "f6-public-baseline",
    },
    timeout: "15s",
};

export default function () {
    const health = http.get(`${target}/health`, {
        ...requestParams,
        tags: { endpoint: "health", name: "GET /health" },
    });
    healthRequests.add(1);
    healthDuration.add(health.timings.duration);
    healthErrors.add(health.status !== 200);

    const healthBody = parseJson(health);
    check(health, {
        "health returns 200": (response) => response.status === 200,
        "health body reports alive": () => healthBody !== null && healthBody.status === "alive",
    });

    const ready = http.get(`${target}/ready`, {
        ...requestParams,
        tags: { endpoint: "ready", name: "GET /ready" },
    });
    readyRequests.add(1);
    readyDuration.add(ready.timings.duration);
    readyErrors.add(ready.status !== 200);

    const readyBody = parseJson(ready);
    check(ready, {
        "ready status matches run mode": (response) => response.status === 200
            || (allowNotReady && response.status === 503),
        "ready body has known status": () => readyBody !== null
            && ["ready", "degraded", "not_ready"].includes(readyBody.status),
        "ready body includes dependency checks": () => readyBody !== null
            && Array.isArray(readyBody.checks)
            && readyBody.checks.length > 0,
    });
}
