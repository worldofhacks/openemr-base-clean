"""Distinct Week 2 document-write workbench (W2-M15/M16/M21; W2-D3/D6/D9/D10).

The Week 1 pre-visit brief remains at ``/app``.  This page is rendered only after the
opaque SMART session is resolved server-side, so its upload patient is never editable.
Clinical content is inserted with DOM ``textContent`` only; no raw model proposal is
rendered as a fact.
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from app.session.store import (
    SessionExpiredError,
    SessionNotFound,
    SessionStoreUnavailable,
)
from app.writeback.route_attestations import RouteAttestationUnavailable

router = APIRouter()


def _script_json(value: dict[str, object]) -> str:
    """Serialize into a script block without permitting an HTML closing-tag escape."""

    return (
        json.dumps(value, separators=(",", ":"), ensure_ascii=True)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:,">
<title>Week 2 Document Write · Clinical Co-Pilot</title>
<style>
:root {
  --navy:#15364d; --navy-2:#204f6d; --ink:#17232c; --muted:#647480;
  --canvas:#f3f5f6; --surface:#fbfcfc; --line:#cfd7dc; --line-strong:#9eabb3;
  --blue-bg:#eaf3f8; --blue:#145d7e; --indigo-bg:#eef0fb; --indigo:#464f9b;
  --green-bg:#eaf5ee; --green:#236a40; --amber-bg:#fff5da; --amber:#805b00;
  --red-bg:#fbeceb; --red:#9b302c; --radius:6px;
}
* { box-sizing:border-box; }
html,body { height:100%; }
body { margin:0; background:var(--canvas); color:var(--ink); font:14px/1.45 Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
button,input { font:inherit; }
button { cursor:pointer; }
button:disabled { cursor:not-allowed; opacity:.55; }
.topbar { min-height:56px; background:var(--navy); color:#fff; display:flex; align-items:center; gap:16px; padding:8px 24px; border-bottom:1px solid #0b2638; }
.brand { font-weight:750; letter-spacing:-.01em; }
.product { color:#cbdbe5; font-size:12px; border-left:1px solid #60798a; padding-left:16px; }
.phase { margin-left:auto; font-size:12px; font-weight:700; letter-spacing:.04em; text-transform:uppercase; border:1px solid #6f8999; border-radius:999px; padding:4px 10px; }
.patientbar { min-height:52px; padding:8px 24px; background:var(--surface); border-bottom:1px solid var(--line); display:flex; align-items:center; gap:16px; }
.patientbar strong { font-size:13px; }
.patientbar .mono { color:var(--muted); }
.patientbar .review { margin-left:auto; color:var(--amber); background:var(--amber-bg); border:1px solid #e2c86f; border-radius:999px; padding:4px 10px; font-size:12px; font-weight:700; }
.mono { font:12px/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
.shell { display:grid; grid-template-columns:minmax(300px,360px) minmax(0,1fr); min-height:calc(100vh - 109px); }
.rail { border-right:1px solid var(--line); background:#edf1f3; padding:16px; }
.workspace { min-width:0; padding:16px; display:grid; gap:16px; align-content:start; }
.card { background:var(--surface); border:1px solid var(--line); border-radius:var(--radius); }
.card-head { padding:12px 16px; border-bottom:1px solid var(--line); display:flex; align-items:center; gap:8px; }
.card-head h2,.card-head h3 { margin:0; font-size:14px; letter-spacing:-.01em; }
.card-body { padding:16px; }
.eyebrow { margin:0 0 4px; color:var(--muted); font-size:11px; font-weight:750; letter-spacing:.08em; text-transform:uppercase; }
.subtle { color:var(--muted); font-size:12px; }
.section-note { margin:8px 0 0; color:var(--muted); font-size:12px; }
.type-switch { display:grid; grid-template-columns:1fr 1fr; gap:0; margin-bottom:16px; }
.type-switch button { border:1px solid var(--line-strong); background:#fff; color:var(--muted); padding:9px 8px; font-weight:700; }
.type-switch button:first-child { border-radius:5px 0 0 5px; }
.type-switch button:last-child { border-left:0; border-radius:0 5px 5px 0; }
.type-switch button.active { background:var(--navy); color:#fff; border-color:var(--navy); }
.drop { position:relative; display:block; border:1px dashed var(--line-strong); border-radius:var(--radius); padding:20px 12px; text-align:center; background:#fff; cursor:pointer; }
.drop:focus-within { outline:2px solid #74a8c6; outline-offset:2px; }
.drop strong { display:block; }
.drop input { position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0; }
.file-line { display:flex; justify-content:center; align-items:center; gap:8px; margin-top:10px; color:var(--muted); font-size:12px; }
.file-action { border:1px solid var(--line-strong); border-radius:4px; padding:3px 7px; background:#fff; color:var(--navy); font-weight:700; }
.encounter { display:flex; gap:8px; align-items:flex-start; margin:12px 0; padding:10px; border:1px solid var(--line); border-radius:5px; background:#fff; }
.encounter input { margin-top:3px; }
.primary { width:100%; border:1px solid var(--navy); border-radius:5px; padding:10px 12px; background:var(--navy); color:#fff; font-weight:750; }
.primary:hover:not(:disabled) { background:var(--navy-2); }
.secondary { border:1px solid var(--line-strong); border-radius:5px; background:#fff; color:var(--navy); padding:7px 10px; font-weight:700; }
.status-panel { margin-top:16px; }
.status-line { display:flex; justify-content:space-between; gap:8px; align-items:center; margin-bottom:8px; }
.pill { display:inline-flex; align-items:center; gap:6px; border:1px solid var(--line); border-radius:999px; padding:3px 8px; background:#fff; color:var(--muted); font-size:11px; font-weight:750; text-transform:uppercase; letter-spacing:.04em; }
.pill:before { content:""; width:7px; height:7px; border-radius:50%; background:#98a5ad; }
.pill.active:before { background:#277aa1; }
.pill.ok:before { background:#2b8a52; }
.pill.error:before { background:#b54139; }
.progress { height:5px; overflow:hidden; background:#dce2e5; border-radius:999px; }
.progress span { display:block; width:0; height:100%; background:#277aa1; transition:width .15s ease; }
.metrics { display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-top:12px; }
.metric { border:1px solid var(--line); background:#fff; padding:8px; border-radius:5px; }
.metric b { display:block; font-size:18px; }
.errorbox,.notice { margin-top:10px; border:1px solid; border-radius:5px; padding:9px 10px; font-size:12px; }
.errorbox { color:var(--red); background:var(--red-bg); border-color:#e4aaa6; }
.notice { color:var(--amber); background:var(--amber-bg); border-color:#e2c86f; }
.readback { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
.attestation { border:1px solid var(--line); border-radius:5px; padding:10px; background:#fff; }
.attestation .state { color:var(--muted); font-weight:700; }
.attestation.verified { border-color:#9fc9ae; background:var(--green-bg); }
.attestation.verified .state { color:var(--green); }
.report-summary { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:12px; }
.count { border:1px solid var(--line); border-radius:5px; padding:5px 8px; background:#fff; font-size:12px; }
.count.grounded { color:var(--green); border-color:#9fc9ae; }
.count.unsupported { color:var(--amber); border-color:#e2c86f; }
.fields { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
.field { min-width:0; border:1px solid var(--line); border-left:3px solid var(--green); border-radius:5px; padding:10px; background:#fff; }
.field.unsupported { border-left-color:#d39b13; background:#fffaf0; }
.field-name { color:var(--muted); font-size:11px; font-weight:750; letter-spacing:.04em; text-transform:uppercase; overflow-wrap:anywhere; }
.field-value { margin-top:3px; font-weight:700; overflow-wrap:anywhere; }
.field.unsupported .field-value { color:var(--amber); }
.chips { display:flex; gap:5px; flex-wrap:wrap; margin-top:8px; }
.chip { border:1px solid; border-radius:999px; padding:3px 7px; font-size:11px; font-weight:700; background:#fff; }
.chip.patient_record { color:var(--blue); border-color:#9bc0d2; background:var(--blue-bg); }
.chip.uploaded_document { color:var(--indigo); border-color:#b8bce2; background:var(--indigo-bg); }
.chip.guideline { color:var(--green); border-color:#9fc9ae; background:var(--green-bg); }
.answers { display:grid; gap:8px; margin-top:12px; }
.claim { border:1px solid var(--line); border-left:3px solid var(--blue); border-radius:5px; background:#fff; padding:10px 12px; white-space:pre-wrap; }
.claim.uploaded_document { border-left-color:var(--indigo); }
.claim.guideline { border-left-color:var(--green); }
.claim-label { margin-bottom:5px; color:var(--muted); font-size:10px; font-weight:800; letter-spacing:.08em; text-transform:uppercase; }
.ask { display:flex; gap:8px; }
.ask input { min-width:0; flex:1; border:1px solid var(--line-strong); border-radius:5px; padding:10px 11px; background:#fff; }
.ask input:focus { outline:2px solid #74a8c6; outline-offset:1px; }
.ask button { width:auto; min-width:96px; }
.empty { color:var(--muted); padding:16px; border:1px dashed var(--line); border-radius:5px; text-align:center; }
.modal { position:fixed; inset:0; z-index:50; background:rgba(9,24,34,.72); display:none; align-items:center; justify-content:center; padding:24px; }
.modal.open { display:flex; }
.viewer { width:min(980px,96vw); max-height:94vh; overflow:auto; background:var(--surface); border:1px solid #71818b; border-radius:6px; }
.viewer-head { position:sticky; top:0; z-index:3; display:flex; align-items:center; gap:8px; padding:10px 12px; background:var(--surface); border-bottom:1px solid var(--line); }
.viewer-head button { margin-left:auto; }
.page-wrap { position:relative; width:max-content; max-width:100%; margin:16px auto; }
.page-wrap img { display:block; max-width:min(880px,calc(96vw - 64px)); height:auto; border:1px solid var(--line); }
.box { position:absolute; border:3px solid #4e58b8; background:rgba(78,88,184,.14); pointer-events:none; }
.box.unsupported { border-color:#cf9300; border-style:dashed; background:rgba(207,147,0,.12); }
@media (max-width:850px) {
  .shell { grid-template-columns:1fr; }
  .rail { border-right:0; border-bottom:1px solid var(--line); }
  .fields { grid-template-columns:1fr; }
}
@media (max-width:560px) {
  .topbar,.patientbar { padding-left:12px; padding-right:12px; }
  .product { display:none; }
  .phase { font-size:10px; }
  .patientbar { flex-wrap:wrap; }
  .patientbar .review { margin-left:0; }
  .rail,.workspace { padding:12px; }
  .readback { grid-template-columns:1fr; }
  .ask { flex-direction:column; }
  .ask button { width:100%; }
}
</style>
</head>
<body>
<header class="topbar">
  <div class="brand">Clinical Co-Pilot</div>
  <div class="product">Multimodal evidence agent</div>
  <div class="phase">Week 2 · Document Write</div>
</header>
<div class="patientbar">
  <strong>Pinned OpenEMR chart</strong>
  <span class="mono" id="patientRef"></span>
  <span class="subtle" id="encounterState"></span>
  <span class="review">machine-authored · pending clinical review</span>
</div>
<main class="shell">
  <aside class="rail">
    <section class="card">
      <div class="card-head"><h2>Upload clinical document</h2></div>
      <div class="card-body">
        <p class="eyebrow">1 · Document type</p>
        <div class="type-switch" role="group" aria-label="Document type">
          <button id="labType" type="button" class="active" aria-pressed="true">Lab PDF</button>
          <button id="intakeType" type="button" aria-pressed="false">Intake form</button>
        </div>
        <p class="eyebrow">2 · Source file</p>
        <label class="drop">
          <strong>Select a PDF or image</strong>
          <span class="subtle" id="fileRule">PDF · up to 10 MB / 20 pages</span>
          <input id="file" type="file" accept="application/pdf">
          <span class="file-line"><span class="file-action">Browse</span><span id="fileName">No file selected</span></span>
        </label>
        <label class="encounter" id="encounterRow">
          <input id="useEncounter" type="checkbox" checked>
          <span><strong>Write grounded intake vitals</strong><br><span class="subtle">Uses the selected OpenEMR encounter. The agent never creates one.</span></span>
        </label>
        <button class="primary" id="upload" type="button">Upload and extract</button>
        <p class="section-note">The source, grounded artifact, and eligible vitals use the verified exactly-once write path.</p>
        <div class="notice" id="runtimeNotice" hidden>Document write is unavailable for this selected chart because its UUID-to-route binding is not attested. Refresh the synthetic route attestations, then relaunch Week 2 for this patient.</div>
        <div class="status-panel" id="statusPanel" data-status-contract="/documents/{document_id}/status" hidden>
          <div class="status-line"><span class="eyebrow">Processing</span><span class="pill" id="statusPill">queued</span></div>
          <div class="progress"><span id="progressBar"></span></div>
          <div class="metrics">
            <div class="metric"><span class="subtle">Grounded</span><b id="groundedCount">0</b></div>
            <div class="metric"><span class="subtle">Unsupported</span><b id="unsupportedCount">0</b></div>
          </div>
          <p class="mono subtle" id="documentRef"></p>
          <div id="statusMessage"></div>
          <button class="secondary" id="retry" type="button" hidden>Retry failed job</button>
        </div>
      </div>
    </section>
  </aside>
  <section class="workspace">
    <section class="card">
      <div class="card-head"><h2>OpenEMR readback</h2><span class="subtle">fresh Binary digest attestation</span></div>
      <div class="card-body">
        <div class="readback">
          <div class="attestation" id="sourceReadback"><strong>Source document</strong><div class="state">Waiting for completion</div><div class="mono subtle"></div></div>
          <div class="attestation" id="artifactReadback"><strong>Grounded artifact</strong><div class="state">Waiting for completion</div><div class="mono subtle"></div></div>
        </div>
      </div>
    </section>
    <section class="card">
      <div class="card-head"><h2>Extraction report</h2><span class="subtle">grounded fields render; unsupported proposals stay redacted</span></div>
      <div class="card-body">
        <div id="report"><div class="empty">Upload a document to begin extraction and grounding.</div></div>
      </div>
    </section>
    <section class="card">
      <div class="card-head"><h2>Cited answer</h2><span class="subtle">chart + uploaded document + VA/DoD guideline evidence</span></div>
      <div class="card-body">
        <form class="ask" id="askForm">
          <input id="question" autocomplete="off" placeholder="Ask with condition/test terms, e.g. type 2 diabetes; HbA1c">
          <button class="primary" id="ask" type="submit">Ask</button>
        </form>
        <p class="section-note">Guideline retrieval receives condition/test terms only. Every rendered clinical claim must carry a complete citation.</p>
        <div class="answers" id="answers"><div class="empty">Cited answers will appear here.</div></div>
      </div>
    </section>
  </section>
</main>
<div class="modal" id="modal" role="dialog" aria-modal="true" aria-label="Source page">
  <div class="viewer">
    <div class="viewer-head"><strong id="viewerTitle">Source page</strong><span class="subtle" id="viewerNote"></span><button class="secondary" id="closeViewer" type="button">Close</button></div>
    <div class="page-wrap" id="pageWrap"><img id="pageImage" alt="Uploaded document source page"><div class="box" id="overlay"></div></div>
  </div>
</div>
<script>
(function () {
  "use strict";
  const context = __W2_CONTEXT__;
  const byId = function (id) { return document.getElementById(id); };
  const patientRef = context.patient_id.length > 12 ? context.patient_id.slice(0, 8) + "…" + context.patient_id.slice(-4) : context.patient_id;
  byId("patientRef").textContent = patientRef;
  byId("encounterState").textContent = context.encounter_id ? "encounter-bound vitals available" : "no encounter selected · artifact only";

  let docType = "lab_pdf";
  let currentDocument = null;
  let currentStatus = null;
  let pollGeneration = 0;
  const terminal = new Set(["complete", "failed"]);
  const stages = ["storing", "reconciling", "queued", "extracting", "grounding", "writing", "complete"];

  function make(tag, cls, text) {
    const item = document.createElement(tag);
    if (cls) item.className = cls;
    if (text !== undefined && text !== null) item.textContent = String(text);
    return item;
  }
  function clear(target) { while (target.firstChild) target.removeChild(target.firstChild); }
  function message(target, cls, text) { clear(target); target.appendChild(make("div", cls, text)); }
  function sameOriginUrl(path) {
    const url = new URL(path, location.origin);
    if (url.origin !== location.origin) throw new Error("refusing a cross-origin workflow URL");
    return url;
  }
  function withSession(path) {
    const url = sameOriginUrl(path);
    url.searchParams.set("session_id", context.session_id);
    return url;
  }
  async function apiError(response) {
    let text = "Request failed (HTTP " + response.status + ")";
    try {
      const body = await response.json();
      const detail = body && body.detail;
      if (detail && typeof detail === "object" && detail.reason) text = detail.reason + (detail.message ? " · " + detail.message : "");
      else if (typeof detail === "string") text = detail;
    } catch (_error) { /* keep the content-free status */ }
    return new Error(text);
  }

  function selectType(next) {
    docType = next;
    const lab = next === "lab_pdf";
    byId("labType").classList.toggle("active", lab);
    byId("labType").setAttribute("aria-pressed", String(lab));
    byId("intakeType").classList.toggle("active", !lab);
    byId("intakeType").setAttribute("aria-pressed", String(!lab));
    byId("file").accept = lab ? "application/pdf" : "application/pdf,image/png,image/jpeg";
    byId("fileRule").textContent = lab ? "PDF · up to 10 MB / 20 pages" : "PDF, PNG, or JPEG · up to 10 MB / 20 pages";
    byId("encounterRow").style.display = lab ? "none" : "flex";
  }
  byId("labType").addEventListener("click", function () { selectType("lab_pdf"); });
  byId("intakeType").addEventListener("click", function () { selectType("intake_form"); });
  byId("file").addEventListener("change", function () {
    const file = byId("file").files[0];
    byId("fileName").textContent = file ? file.name : "No file selected";
  });
  selectType("lab_pdf");
  if (!context.write_path_attested) {
    byId("upload").disabled = true;
    byId("labType").disabled = true;
    byId("intakeType").disabled = true;
    byId("file").disabled = true;
    byId("useEncounter").disabled = true;
    byId("runtimeNotice").hidden = false;
    byId("encounterState").textContent = "document write unavailable for this chart";
  }

  function setStatus(status) {
    currentStatus = status;
    byId("statusPanel").hidden = false;
    byId("statusPill").textContent = status.state;
    byId("statusPill").className = "pill " + (status.state === "complete" ? "ok" : status.state === "failed" ? "error" : "active");
    const index = stages.indexOf(status.state);
    const percent = status.state === "failed" ? 100 : Math.max(7, ((index + 1) / stages.length) * 100);
    byId("progressBar").style.width = percent + "%";
    byId("groundedCount").textContent = String(status.fields_grounded || 0);
    byId("unsupportedCount").textContent = String(status.fields_unsupported || 0);
    byId("documentRef").textContent = "document " + status.document_id + " · correlation " + status.correlation_id;
    const statusMessage = byId("statusMessage");
    clear(statusMessage);
    byId("retry").hidden = true;
    if (status.state === "failed") {
      message(statusMessage, "errorbox", "Processing stopped: " + (status.reason || "write path failed"));
      byId("retry").hidden = false;
    } else if (status.state === "reconciling") {
      message(statusMessage, "notice", "Remote outcome is being reconciled. No blind retry is allowed.");
    } else if (status.state === "complete" && Number(status.fields_unsupported) > 0) {
      message(statusMessage, "notice", status.fields_unsupported + " field(s) are UNSUPPORTED and were not written as facts.");
    }
  }

  async function pollStatus(statusPath, generation) {
    const statusUrl = withSession(statusPath);
    for (let attempt = 0; attempt < 180 && generation === pollGeneration; attempt += 1) {
      const response = await fetch(statusUrl, {headers:{"Accept":"application/json"}, cache:"no-store"});
      if (!response.ok) throw await apiError(response);
      const status = await response.json();
      setStatus(status);
      if (terminal.has(status.state)) {
        if (status.state === "complete") await loadCompletedDocument(status.document_id);
        return;
      }
      await new Promise(function (resolve) { setTimeout(resolve, 1500); });
    }
    if (generation === pollGeneration) throw new Error("processing did not reach a terminal state in time");
  }

  async function upload() {
    if (!context.write_path_attested) return;
    const file = byId("file").files[0];
    if (!file) { message(byId("statusMessage"), "errorbox", "Choose a synthetic lab PDF or intake form first."); byId("statusPanel").hidden = false; return; }
    byId("upload").disabled = true;
    byId("retry").hidden = true;
    const data = new FormData();
    data.append("file", file);
    data.append("session_id", context.session_id);
    data.append("patient_id", context.patient_id);
    data.append("doc_type", docType);
    if (docType === "intake_form" && byId("useEncounter").checked && context.encounter_id) data.append("encounter_id", context.encounter_id);
    try {
      const response = await fetch("/documents", {method:"POST", body:data});
      if (!response.ok) throw await apiError(response);
      const accepted = await response.json();
      currentDocument = accepted.document_id;
      pollGeneration += 1;
      setStatus({document_id:accepted.document_id,state:accepted.state,reason:null,correlation_id:accepted.correlation_id,updated_ts:"",fields_grounded:0,fields_unsupported:0,attempt_count:0,next_retry_at:null});
      if (response.status === 200) message(byId("statusMessage"), "notice", "Duplicate upload matched the existing patient-scoped document; no second record was created.");
      await pollStatus(accepted.status_url, pollGeneration);
    } catch (error) {
      message(byId("statusMessage"), "errorbox", error instanceof Error ? error.message : "Upload failed");
      byId("statusPanel").hidden = false;
    } finally { byId("upload").disabled = false; }
  }
  byId("upload").addEventListener("click", upload);

  async function retry() {
    if (!currentDocument || !currentStatus || currentStatus.state !== "failed") return;
    byId("retry").disabled = true;
    try {
      const response = await fetch(withSession("/documents/" + encodeURIComponent(currentDocument) + "/retry"), {method:"POST",headers:{"Content-Type":"application/json","Accept":"application/json"},body:JSON.stringify({expected_state:"failed"})});
      if (!response.ok) throw await apiError(response);
      const accepted = await response.json();
      pollGeneration += 1;
      await pollStatus(accepted.status_url, pollGeneration);
    } catch (error) { message(byId("statusMessage"), "errorbox", error instanceof Error ? error.message : "Retry failed"); }
    finally { byId("retry").disabled = false; }
  }
  byId("retry").addEventListener("click", retry);

  function renderAttestation(target, title, result) {
    clear(target);
    target.appendChild(make("strong", null, title));
    const state = make("div", "state", result && result.verified && result.expected_hash === result.observed_hash ? "Verified byte-for-byte" : "Verification unavailable");
    target.appendChild(state);
    const digest = result && result.observed_hash ? result.algorithm + " · " + result.observed_hash.slice(0, 12) + "…" : "No verified digest";
    target.appendChild(make("div", "mono subtle", digest));
    target.classList.toggle("verified", Boolean(result && result.verified && result.expected_hash === result.observed_hash));
  }

  async function loadCompletedDocument(documentId) {
    const reportUrl = withSession("/documents/" + encodeURIComponent(documentId) + "/extraction-report");
    const readbackUrl = withSession("/documents/" + encodeURIComponent(documentId) + "/readback-verification");
    const results = await Promise.all([fetch(reportUrl,{cache:"no-store"}),fetch(readbackUrl,{cache:"no-store"})]);
    if (!results[0].ok) throw await apiError(results[0]);
    if (!results[1].ok) throw await apiError(results[1]);
    const report = await results[0].json();
    const readback = await results[1].json();
    renderReport(report);
    renderAttestation(byId("sourceReadback"), "Source document", readback.source);
    renderAttestation(byId("artifactReadback"), "Grounded artifact", readback.artifact);
    const verified = readback.source && readback.source.verified && readback.artifact && readback.artifact.verified;
    if (!verified) message(byId("statusMessage"), "errorbox", "OpenEMR readback could not be verified. Treat the write as incomplete.");
  }

  function humanPath(path) { return path.replace(/^results\.(\d+)\./, "result $1 · ").replace(/^demographics\./, "demographics · ").replace(/^vitals\./, "vitals · ").replaceAll("_", " ").replaceAll(".", " · "); }
  function citationButton(citation, documentId, bbox, page, unsupported) {
    const source = citation ? citation.source_type : "uploaded_document";
    const label = unsupported ? "review source region" : source.replaceAll("_", " ") + (page !== null && page !== undefined ? " · page " + page : "");
    const chip = make("button", "chip " + source, label);
    chip.type = "button";
    chip.addEventListener("click", function () {
      if (bbox && page !== null && page !== undefined) openPage(documentId, page, bbox, unsupported);
      else if (citation) alert([citation.source_id, citation.page_or_section || "chart record", citation.field_or_chunk_id, citation.quote_or_value].join("\n"));
    });
    return chip;
  }
  function renderReport(report) {
    const root = byId("report"); clear(root);
    const summary = make("div", "report-summary");
    summary.appendChild(make("span", "count grounded", report.fields_grounded + " grounded"));
    summary.appendChild(make("span", "count unsupported", report.fields_unsupported + " UNSUPPORTED"));
    root.appendChild(summary);
    const fields = make("div", "fields");
    let hasAllergy = false;
    report.fields.forEach(function (field) {
      if (field.field_path.indexOf("allergies.") === 0) hasAllergy = true;
      const unsupported = field.verdict === "unsupported";
      const item = make("article", "field" + (unsupported ? " unsupported" : ""));
      item.appendChild(make("div", "field-name", humanPath(field.field_path)));
      item.appendChild(make("div", "field-value", unsupported ? "UNSUPPORTED — verify against source document" : field.display_value));
      const chips = make("div", "chips");
      if (!unsupported && field.citation) chips.appendChild(citationButton(field.citation, report.document_id, field.bbox, field.page, false));
      if (unsupported && field.bbox && field.page !== null) chips.appendChild(citationButton(null, report.document_id, field.bbox, field.page, true));
      if (chips.childNodes.length) item.appendChild(chips);
      fields.appendChild(item);
    });
    if (!report.fields.length) fields.appendChild(make("div", "empty", "No extractable fields were found. Verify the source document directly."));
    root.appendChild(fields);
    if (report.doc_type === "intake_form" && !hasAllergy) root.appendChild(make("div", "notice", "No allergy information was captured on this form — confirm with patient. This is never treated as NKDA."));
  }

  function positionOverlay(bbox) {
    const image = byId("pageImage");
    const overlay = byId("overlay");
    const width = image.clientWidth, height = image.clientHeight;
    overlay.style.left = (bbox.x0 * width) + "px";
    overlay.style.top = (bbox.y0 * height) + "px";
    overlay.style.width = ((bbox.x1 - bbox.x0) * width) + "px";
    overlay.style.height = ((bbox.y1 - bbox.y0) * height) + "px";
  }
  function openPage(documentId, page, bbox, unsupported) {
    const image = byId("pageImage");
    const overlay = byId("overlay");
    overlay.className = "box" + (unsupported ? " unsupported" : "");
    byId("viewerTitle").textContent = "Uploaded document · page " + page;
    byId("viewerNote").textContent = unsupported ? "UNSUPPORTED review region" : "verified grounded field";
    image.onload = function () { positionOverlay(bbox); };
    image.onerror = function () { byId("viewerNote").textContent = "Source page unavailable; use the citation quote."; overlay.style.display = "none"; };
    overlay.style.display = "block";
    image.src = withSession("/documents/" + encodeURIComponent(documentId) + "/pages/" + encodeURIComponent(page)).toString();
    byId("modal").classList.add("open");
    const observer = new ResizeObserver(function () { if (image.complete && image.naturalWidth) positionOverlay(bbox); });
    observer.observe(image);
    byId("modal").dataset.observer = "active";
    byId("modal")._observer = observer;
  }
  function closePage() {
    const modal = byId("modal");
    if (modal._observer) modal._observer.disconnect();
    modal.classList.remove("open");
    byId("pageImage").removeAttribute("src");
  }
  byId("closeViewer").addEventListener("click", closePage);
  byId("modal").addEventListener("click", function (event) { if (event.target === byId("modal")) closePage(); });

  function renderClaim(data) {
    if (!data || !data.claim_block) return;
    const answers = byId("answers");
    if (answers.firstChild && answers.firstChild.classList.contains("empty")) clear(answers);
    const source = data.source_class || "patient_record";
    const claim = make("article", "claim " + source);
    claim.appendChild(make("div", "claim-label", source.replaceAll("_", " ")));
    claim.appendChild(make("div", null, data.claim_block));
    const citations = Array.isArray(data.citations) ? data.citations : [];
    if (citations.length) {
      const chips = make("div", "chips");
      citations.forEach(function (citation) {
        if (citation && typeof citation === "object") chips.appendChild(citationButton(citation, data.overlay ? data.overlay.source_id.replace(/^document:/, "") : currentDocument, data.overlay ? data.overlay.bbox : null, data.overlay ? data.overlay.page : null, false));
        else chips.appendChild(make("span", "chip patient_record", "patient record"));
      });
      claim.appendChild(chips);
    }
    answers.appendChild(claim);
  }
  function parseSse(buffer, flush) {
    const blocks = buffer.split("\n\n");
    const rest = flush ? "" : blocks.pop();
    blocks.forEach(function (block) {
      let event = "message", data = "";
      block.split("\n").forEach(function (line) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        if (line.startsWith("data:")) data += line.slice(5).trim();
      });
      if (!data) return;
      try {
        const parsed = JSON.parse(data);
        if (event === "claim_block") renderClaim(parsed);
        if (event === "done" && parsed.degraded) byId("answers").appendChild(make("div", "notice", "Answer completed in degraded mode; review cited records directly."));
      } catch (_error) { /* malformed event never becomes a rendered claim */ }
    });
    return rest || "";
  }
  async function ask(event) {
    event.preventDefault();
    const question = byId("question").value.trim();
    if (!question) return;
    byId("ask").disabled = true;
    message(byId("answers"), "empty", "Running verify-then-flush…");
    try {
      const response = await fetch("/chat", {method:"POST",headers:{"Content-Type":"application/json","Accept":"text/event-stream"},body:JSON.stringify({session_id:context.session_id,patient_id:context.patient_id,message:question})});
      if (!response.ok) throw await apiError(response);
      const type = response.headers.get("content-type") || "";
      if (!type.includes("text/event-stream")) {
        const body = await response.json();
        clear(byId("answers"));
        renderClaim({claim_block:body.brief,citations:body.citations,source_class:"patient_record"});
        return;
      }
      clear(byId("answers"));
      const reader = response.body.getReader(), decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const next = await reader.read();
        if (next.done) break;
        buffer += decoder.decode(next.value, {stream:true}).replaceAll("\r\n", "\n");
        buffer = parseSse(buffer, false);
      }
      buffer += decoder.decode();
      parseSse(buffer, true);
      if (!byId("answers").childNodes.length) throw new Error("No verified claim was returned");
    } catch (error) { message(byId("answers"), "errorbox", error instanceof Error ? error.message : "Answer unavailable"); }
    finally { byId("ask").disabled = false; }
  }
  byId("askForm").addEventListener("submit", ask);
})();
</script>
</body>
</html>
"""


@router.get("/week2", response_class=HTMLResponse)
async def week2_page(
    request: Request,
    sid: Annotated[str, Query(min_length=1)],
) -> HTMLResponse:
    services = request.app.state.services
    settings = getattr(services, "settings", None)
    if settings is not None and not settings.w2_document_runtime_enabled:
        raise HTTPException(status_code=503, detail="Week 2 document runtime is disabled")
    try:
        session = await services.resolve_session(sid)
    except SessionNotFound:
        raise HTTPException(status_code=404, detail="session not found — launch Week 2")
    except SessionExpiredError:
        raise HTTPException(status_code=401, detail="session expired — launch Week 2 again")
    except SessionStoreUnavailable:
        raise HTTPException(status_code=503, detail="session store unavailable")

    try:
        write_path_attested, encounter_id = (
            await services.resolve_document_route_context(session)
        )
    except RouteAttestationUnavailable:
        raise HTTPException(
            status_code=503, detail="document route attestations unavailable"
        ) from None
    context = _script_json(
        {
            "session_id": session.session_id,
            "patient_id": session.patient_id,
            "encounter_id": encounter_id,
            "write_path_attested": write_path_attested,
        }
    )
    return HTMLResponse(
        content=_PAGE.replace("__W2_CONTEXT__", context),
        headers={
            "Cache-Control": "private, no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )
