import { check } from "k6";
import exec from "k6/execution";
import http from "k6/http";
import { Counter, Rate, Trend } from "k6/metrics";

const REQUIRED_PROFILE = "sonnet-4.6-200k-8192-single-call-retries2";
const MAX_CONTEXT_TOKENS = 200_000;
const MAX_OUTPUT_TOKENS = 8_192;
const INPUT_USD_PER_MILLION = 3;
const OUTPUT_USD_PER_MILLION = 15;
const CACHE_WRITE_MULTIPLIER = 1.25;
const MAX_PROVIDER_ATTEMPTS = 3;
const MIN_SAFE_CAP_USD = 2.62;
const MAX_ALLOWED_CAP_USD = 3.00;

function abort(message) {
    exec.test.abort(message);
    throw new Error(message);
}

function required(name) {
    const value = (__ENV[name] || "").trim();
    if (value === "") {
        abort(`${name} is required; no /chat request was sent`);
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

const target = required("CHAT_BASE_URL").replace(/\/+$/, "");
if (!/^https:\/\/[^/]+(?:\/.*)?$/.test(target)) {
    abort("CHAT_BASE_URL must be an https:// URL; no /chat request was sent");
}

const sessionId = required("CHAT_SESSION_ID");
const spendCapRaw = required("CHAT_SPEND_CAP_USD");
const spendCapUsd = Number(spendCapRaw);
if (!Number.isFinite(spendCapUsd)) {
    abort("CHAT_SPEND_CAP_USD must be a finite number; no /chat request was sent");
}
if (spendCapUsd < MIN_SAFE_CAP_USD) {
    abort(`CHAT_SPEND_CAP_USD is below the conservative $${MIN_SAFE_CAP_USD.toFixed(2)} request ceiling`);
}
if (spendCapUsd > MAX_ALLOWED_CAP_USD) {
    abort(`CHAT_SPEND_CAP_USD exceeds the hard $${MAX_ALLOWED_CAP_USD.toFixed(2)} test limit`);
}

const profile = required("CHAT_PROFILE_ACK");
if (profile !== REQUIRED_PROFILE) {
    abort(`CHAT_PROFILE_ACK must equal ${REQUIRED_PROFILE}; verify the deployed model limits first`);
}

// Conservative ceiling for the current serving contract. This intentionally double-counts
// the full context and max output, prices all input at the cache-write premium, and allows the
// SDK's initial attempt plus two retries. The k6 scenario itself is immutably one HTTP request.
const calculatedCeilingUsd = MAX_PROVIDER_ATTEMPTS * (
    (MAX_CONTEXT_TOKENS * INPUT_USD_PER_MILLION * CACHE_WRITE_MULTIPLIER) / 1_000_000
    + (MAX_OUTPUT_TOKENS * OUTPUT_USD_PER_MILLION) / 1_000_000
);
if (calculatedCeilingUsd > spendCapUsd) {
    abort(
        `configured spend cap $${spendCapUsd.toFixed(2)}`
        + ` is below calculated ceiling $${calculatedCeilingUsd.toFixed(5)}`,
    );
}

const chatDuration = new Trend("chat_duration", true);
const chatErrors = new Rate("chat_errors");
const chatRequests = new Counter("chat_requests");

export const options = {
    discardResponseBodies: false,
    maxRedirects: 0,
    scenarios: {
        capped_chat: {
            executor: "per-vu-iterations",
            vus: 1,
            iterations: 1,
            maxDuration: "150s",
            gracefulStop: "0s",
        },
    },
    summaryTrendStats: ["avg", "med", "p(90)", "p(95)", "p(99)", "max"],
    thresholds: {
        chat_errors: ["rate==0"],
        chat_duration: ["p(99)<130000"],
    },
};

export function setup() {
    const scenarios = exec.test.options.scenarios || {};
    const names = Object.keys(scenarios);
    const scenario = scenarios.capped_chat;
    if (names.length !== 1
        || scenario === undefined
        || scenario.executor !== "per-vu-iterations"
        || scenario.vus !== 1
        || scenario.iterations !== 1) {
        abort("runtime options changed the one-VU/one-iteration spend boundary; no /chat request was sent");
    }
    if ((__ENV.CHAT_VALIDATE_ONLY || "false").toLowerCase() === "true") {
        abort("CHAT_VALIDATE_ONLY: configuration and runtime boundary validated; no /chat request was sent");
    }
}

export default function () {
    const response = http.post(
        `${target}/chat`,
        JSON.stringify({
            session_id: sessionId,
            message: __ENV.CHAT_MESSAGE || "Give me the pre-visit brief.",
        }),
        {
            headers: {
                Accept: "application/json",
                "Content-Type": "application/json",
                "X-Copilot-Load-Test": "f6-chat-single-capped",
            },
            redirects: 0,
            tags: { endpoint: "chat", name: "POST /chat (single capped)" },
            timeout: "130s",
        },
    );

    chatRequests.add(1);
    chatDuration.add(response.timings.duration);
    chatErrors.add(response.status !== 200);

    const body = parseJson(response);
    check(response, {
        "chat returns 200": (result) => result.status === 200,
        "chat brief is non-empty": () => body !== null
            && typeof body.brief === "string"
            && body.brief.trim().length > 0,
        "chat source is declared": () => body !== null
            && ["llm", "deterministic_fallback", "deterministic_refusal"].includes(body.source),
        "chat includes correlation id": () => body !== null
            && typeof body.correlation_id === "string"
            && body.correlation_id.length > 0,
    });
}
