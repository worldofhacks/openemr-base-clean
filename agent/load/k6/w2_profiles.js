import exec from "k6/execution";
import http from "k6/http";
import { check, fail, sleep } from "k6";
import { Rate, Trend } from "k6/metrics";

const profile = (__ENV.PROFILE || "retrieval").trim();
const base = (__ENV.AGENT_BASE_URL || "").replace(/\/$/, "");
const allowed = new Set(["retrieval", "ingestion", "extraction", "full_graph", "week1"]);
const costly = new Set(["ingestion", "extraction", "full_graph", "week1"]);
const documentProfiles = new Set(["ingestion", "extraction"]);
const authenticatedProfiles = new Set(["retrieval", "ingestion", "extraction", "full_graph", "week1"]);
const contextOffsets = { vu1: 0, vu10: 1, vu50: 11 };
const requiredContextCount = 61;

if (!/^https:\/\//.test(base)) fail("AGENT_BASE_URL must be HTTPS");
if (!allowed.has(profile)) fail("PROFILE is not one of the bounded W2 profiles");
if (authenticatedProfiles.has(profile) && __ENV.SYNTHETIC_ONLY_ACK !== "synthetic-sessions-and-documents") {
  fail("authenticated profiles require the synthetic-only acknowledgement");
}
if (costly.has(profile) && __ENV.ALLOW_PROVIDER_SPEND !== "true") {
  fail("cost-bearing profiles require ALLOW_PROVIDER_SPEND=true");
}
if (profile === "full_graph" && __ENV.GRAPH_ENABLED_ACK !== "true") {
  fail("full_graph requires an explicit graph-enabled deployment acknowledgement");
}

let contexts = [];
if (authenticatedProfiles.has(profile)) {
  if (!__ENV.SYNTHETIC_CONTEXTS_FILE) fail("SYNTHETIC_CONTEXTS_FILE is required");
  try {
    contexts = JSON.parse(open(__ENV.SYNTHETIC_CONTEXTS_FILE));
  } catch (_error) {
    fail("synthetic contexts are not valid JSON");
  }
  if (!Array.isArray(contexts) || contexts.length < requiredContextCount) {
    fail("the 1/10/50 ladder requires 61 non-reused synthetic contexts");
  }
  const sessionIds = new Set();
  const patientIds = new Set();
  for (const context of contexts.slice(0, requiredContextCount)) {
    if (!context || typeof context.session_id !== "string" || context.session_id.length === 0) {
      fail("every synthetic context requires an opaque session_id");
    }
    if (sessionIds.has(context.session_id)) fail("synthetic sessions must not be reused");
    sessionIds.add(context.session_id);
    if (documentProfiles.has(profile)) {
      if (typeof context.patient_id !== "string" || context.patient_id.length === 0) {
        fail("document profiles require a patient_id for each synthetic session");
      }
      if (patientIds.has(context.patient_id)) fail("document patients must not be reused");
      patientIds.add(context.patient_id);
    }
  }
}

let fixtureBytes = null;
if (documentProfiles.has(profile)) {
  if (!__ENV.SYNTHETIC_FIXTURE) fail("document profiles require SYNTHETIC_FIXTURE");
  fixtureBytes = open(__ENV.SYNTHETIC_FIXTURE, "b");
}

const failures = new Rate("w2_profile_failures");
const degraded = new Rate("w2_profile_degraded");
const latency = new Trend("w2_profile_latency_ms", true);

const latencyThreshold = profile === "retrieval"
  ? "p(95)<2000"
  : profile === "ingestion"
    ? "p(95)<30000"
    : "p(95)<120000";

export const options = {
  discardResponseBodies: false,
  scenarios: {
    vu1: { executor: "per-vu-iterations", vus: 1, iterations: 1, maxDuration: "3m" },
    vu10: { executor: "per-vu-iterations", vus: 10, iterations: 1, startTime: "3m", maxDuration: "5m" },
    vu50: { executor: "per-vu-iterations", vus: 50, iterations: 1, startTime: "8m", maxDuration: "10m" },
  },
  thresholds: {
    w2_profile_failures: ["rate<0.01"],
    w2_profile_latency_ms: [latencyThreshold],
  },
  summaryTrendStats: ["count", "avg", "med", "p(95)", "max"],
};

function activeContext() {
  const offset = contextOffsets[exec.scenario.name];
  const iteration = exec.scenario.iterationInTest;
  const context = contexts[offset + iteration];
  if (!context) fail("synthetic context allocation failed closed");
  return context;
}

function record(ok, started) {
  failures.add(!ok);
  latency.add(Date.now() - started);
  return ok;
}

function retrieval(context) {
  const started = Date.now();
  const response = http.post(
    `${base}/evidence/search`,
    JSON.stringify({ query: "hypertension", k: 5 }),
    {
      headers: {
        "Content-Type": "application/json",
        "X-Copilot-Session-Id": context.session_id,
      },
      tags: { name: "evidence_search" },
      timeout: "15s",
    }
  );
  const ok = check(response, {
    "retrieval returned grounded synthetic hits": (r) => {
      if (r.status !== 200) return false;
      const body = r.json();
      return body && Array.isArray(body.items) && body.items.length > 0;
    },
  });
  degraded.add(false);
  record(ok, started);
}

function chat(context, includeSharedChecks) {
  const started = Date.now();
  let sharedOk = true;
  if (includeSharedChecks) {
    const health = http.get(`${base}/health`, { tags: { name: "week1_health" }, timeout: "15s" });
    const ready = http.get(`${base}/ready`, { tags: { name: "week1_ready" }, timeout: "15s" });
    sharedOk = check(health, { "shared health is live": (r) => r.status === 200 })
      && check(ready, { "shared readiness serves": (r) => r.status === 200 });
  }
  const response = http.post(
    `${base}/chat`,
    JSON.stringify({ session_id: context.session_id, message: "hypertension" }),
    {
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      tags: { name: includeSharedChecks ? "week1_chat" : "full_graph_chat" },
      timeout: "120s",
    }
  );
  let body = null;
  if (response.status === 200) body = response.json();
  const chatOk = check(response, {
    "cited chat completed": () => Boolean(
      body
      && typeof body.brief === "string"
      && typeof body.correlation_id === "string"
      && Array.isArray(body.citations)
    ),
  });
  degraded.add(Boolean(body && body.degraded));
  record(sharedOk && chatOk, started);
}

function documentFlow(context, waitForExtraction) {
  const started = Date.now();
  let response = null;
  for (let attempt = 0; attempt < 30; attempt += 1) {
    response = http.post(
      `${base}/documents`,
      {
        session_id: context.session_id,
        patient_id: context.patient_id,
        doc_type: __ENV.DOC_TYPE || "lab_pdf",
        file: http.file(fixtureBytes, "synthetic.pdf", "application/pdf"),
      },
      { tags: { name: "documents_upload" }, timeout: "30s" }
    );
    if (response.status !== 503) break;
    sleep(0.5);
  }
  if (response === null) fail("bounded upload loop produced no response");
  const accepted = response.status === 202 ? response.json() : null;
  const uploadOk = check(response, {
    "new synthetic document was accepted": () => Boolean(
      accepted
      && typeof accepted.document_id === "string"
      && accepted.state !== "complete"
    ),
  });
  if (!uploadOk) {
    degraded.add(false);
    record(false, started);
    return;
  }
  if (!waitForExtraction) {
    degraded.add(false);
    record(true, started);
    return;
  }

  for (let attempt = 0; attempt < 90; attempt += 1) {
    const status = http.get(
      `${base}/documents/${encodeURIComponent(accepted.document_id)}/status?session_id=${encodeURIComponent(context.session_id)}`,
      { tags: { name: "document_status" }, timeout: "15s" }
    );
    if (status.status === 200) {
      const body = status.json();
      if (body.state === "complete") {
        degraded.add(false);
        record(true, started);
        return;
      }
      if (body.state === "failed") break;
    } else if (status.status !== 409) {
      break;
    }
    sleep(1);
  }
  degraded.add(true);
  record(false, started);
}

export default function () {
  const context = activeContext();
  if (profile === "retrieval") retrieval(context);
  else if (profile === "ingestion") documentFlow(context, false);
  else if (profile === "extraction") documentFlow(context, true);
  else if (profile === "week1") chat(context, true);
  else chat(context, false);
}
