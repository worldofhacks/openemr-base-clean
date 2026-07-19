"""GET /app — the practitioner chat UI for the pre-visit co-pilot (T-E9 demo UI).

Presentation layer ONLY. A self-contained single-page app (inline CSS/JS, no build step) that
calls the EXISTING POST /chat for the SMART-pinned session and renders the verified brief as a
scannable clinical card: a patient header, a "Review before entering" attention panel, the brief
grouped into Problems / Medications / Labs / Allergies (parsed client-side from the served text),
a "N verified / M dropped" trust badge, clickable citation chips, amber caution on the
confirm-with-patient allergy line, and a multi-turn follow-up with example prompts. It changes
nothing about verification or serving — it is a client of /chat like any other. The session id
arrives as `?sid=` (the /callback redirect); production would carry it in a cookie.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Clinical Co-Pilot</title>
<style>
  :root {
    --ink:#0f2233; --muted:#5c7186; --line:#dce3ec; --bg:#eef2f7; --card:#ffffff;
    --brand:#0f5c8c; --brand-d:#0c4d76;
    --ok-bg:#e3f5ea; --ok-fg:#1a7a45; --drop-bg:#fde7e7; --drop-fg:#b3261e;
    --amber-bg:#fff5e0; --amber-fg:#8a5a00; --amber-line:#f0c469; --chip:#eef3f8;
  }
  * { box-sizing:border-box; }
  html,body { height:100%; }
  body { margin:0; font-family:-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
         background:var(--bg); color:var(--ink); display:flex; flex-direction:column;
         height:100vh; overflow:hidden; }
  header.app { background:linear-gradient(180deg,#0f5c8c,#0c4d76); color:#fff; padding:10px 18px;
    display:flex; align-items:center; gap:10px; box-shadow:0 1px 5px rgba(0,0,0,.25); z-index:5; }
  header.app .brand { font-weight:700; font-size:16px; letter-spacing:.2px; }
  header.app .dot { width:9px;height:9px;border-radius:50%;background:#5ee08a;box-shadow:0 0 0 3px rgba(94,224,138,.25); }
  header.app .tag { margin-left:auto; font-size:11px; font-weight:600; background:rgba(255,255,255,.16);
    padding:3px 9px; border-radius:20px; }

  /* patient header */
  .patient { background:var(--card); border-bottom:1px solid var(--line); padding:11px 18px;
    display:flex; align-items:center; gap:14px; }
  .patient .avatar { width:38px;height:38px;border-radius:50%;background:#dbe8f2;color:#0f5c8c;
    display:flex;align-items:center;justify-content:center;font-weight:700;font-size:15px; }
  .patient .who { font-weight:700; font-size:15px; }
  .patient .meta { color:var(--muted); font-size:12.5px; margin-top:1px; }
  .patient .reason { margin-left:auto; text-align:right; }
  .patient .reason .k { font-size:10.5px; text-transform:uppercase; letter-spacing:.6px; color:var(--muted); }
  .patient .reason .v { font-weight:600; font-size:13px; }
  .skeleton { color:transparent; background:linear-gradient(90deg,#e9eef4,#f4f7fa,#e9eef4);
    background-size:200% 100%; animation:sk 1.2s infinite; border-radius:5px; }
  @keyframes sk { 0%{background-position:200% 0} 100%{background-position:-200% 0} }

  #log { flex:1; min-height:0; overflow-y:auto; padding:18px; display:flex;
    flex-direction:column; gap:16px; overscroll-behavior:contain; }
  .row { display:flex; }
  .row.user { justify-content:flex-end; }
  .row.user .bubble { background:#0f5c8c; color:#fff; max-width:70%; padding:10px 14px;
    border-radius:14px 14px 4px 14px; font-size:14px; line-height:1.45; box-shadow:0 1px 2px rgba(0,0,0,.12); }

  .brief { flex:none; background:var(--card); border:1px solid var(--line); border-radius:14px;
    max-width:820px; width:100%; box-shadow:0 2px 8px rgba(15,40,60,.07); overflow:hidden; }
  .brief .head { display:flex; align-items:center; gap:8px; padding:11px 15px; border-bottom:1px solid var(--line);
    flex-wrap:wrap; background:#f7fafc; }
  .brief .head .title { font-weight:700; font-size:13.5px; }
  .badge { font-size:11.5px; padding:3px 9px; border-radius:20px; font-weight:700; white-space:nowrap; }
  .badge.ok { background:var(--ok-bg); color:var(--ok-fg); }
  .badge.drop { background:var(--drop-bg); color:var(--drop-fg); }
  .badge.src { background:#e5edf6; color:#0f5c8c; }
  .badge.warn { background:var(--amber-bg); color:var(--amber-fg); }
  .brief .body { padding:6px 15px 13px; }

  .attention { border:1px solid var(--amber-line); background:var(--amber-bg); border-radius:10px;
    padding:10px 12px; margin:11px 0; }
  .attention h4 { margin:0 0 6px; font-size:12.5px; color:var(--amber-fg); display:flex; align-items:center; gap:6px; }
  .attention ul { margin:0; padding-left:18px; }
  .attention li { font-size:13px; color:#5a4200; margin:3px 0; line-height:1.4; }
  .attention.clear { border-color:#bfe3cb; background:var(--ok-bg); }
  .attention.clear h4 { color:var(--ok-fg); }

  .section { margin:12px 0 4px; }
  .section > .sh { display:flex; align-items:center; gap:7px; font-size:11.5px; font-weight:700;
    text-transform:uppercase; letter-spacing:.5px; color:var(--muted); margin-bottom:5px; }
  .section > .sh .n { background:var(--chip); color:var(--muted); border-radius:20px; padding:0 7px; font-size:11px; }
  .item { padding:6px 10px; border:1px solid var(--line); border-left:3px solid #cfe0ee; border-radius:8px;
    margin:5px 0; font-size:13.5px; line-height:1.4; display:flex; gap:8px; align-items:flex-start; }
  .item .ic { color:#0f5c8c; flex:none; }
  .item.amber { border-left-color:var(--amber-line); background:#fffaf0; }
  .item.amber .ic { color:var(--amber-fg); }

  .cites { display:flex; flex-wrap:wrap; gap:5px; margin-top:12px; padding-top:10px; border-top:1px dashed var(--line); }
  .cites .lbl { font-size:11px; color:var(--muted); align-self:center; margin-right:2px; }
  .chip { font-size:11px; padding:2px 8px; border-radius:6px; background:var(--chip); color:#33506b;
    border:1px solid #cdddea; font-family:ui-monospace,Menlo,monospace; cursor:pointer; }
  .chip:hover { background:#e3eef7; }
  .meta { margin-top:9px; font-size:10.5px; color:#9fb0bf; }

  /* loading stepper */
  .steps { list-style:none; margin:6px 0; padding:0; }
  .steps li { display:flex; align-items:center; gap:9px; font-size:13px; color:var(--muted); padding:4px 0; }
  .steps li .b { width:16px;height:16px;border-radius:50%;border:2px solid #cdd8e2;flex:none;
    display:flex;align-items:center;justify-content:center;font-size:10px; }
  .steps li.active { color:var(--ink); font-weight:600; }
  .steps li.active .b { border-color:#0f5c8c; }
  .steps li.active .b:after { content:""; width:6px;height:6px;border-radius:50%;background:#0f5c8c; animation:pulse 1s infinite; }
  .steps li.done { color:#1a7a45; }
  .steps li.done .b { border-color:#1a7a45; background:#1a7a45; color:#fff; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.3} }

  /* popover */
  .pop { position:fixed; z-index:50; background:#0f2233; color:#fff; border-radius:8px; padding:9px 11px;
    font-size:12px; max-width:320px; box-shadow:0 6px 22px rgba(0,0,0,.3); line-height:1.5; }
  .pop .pk { font-family:ui-monospace,Menlo,monospace; color:#8fd0ff; word-break:break-all; }

  /* composer */
  .composer { flex:none; border-top:1px solid var(--line); background:var(--card);
    padding:9px 14px 12px; }
  .examples { display:flex; gap:7px; flex-wrap:wrap; margin-bottom:8px; }
  .ex { font-size:12px; padding:5px 11px; border-radius:20px; background:#eef3f8; color:#0f5c8c;
    border:1px solid #d5e2ee; cursor:pointer; }
  .ex:hover { background:#e2edf6; }
  form { display:flex; gap:8px; }
  #msg { flex:1; padding:11px 13px; border:1px solid #c3ccd9; border-radius:10px; font-size:14px; }
  button.send { padding:0 20px; border:0; border-radius:10px; background:#0f5c8c; color:#fff; font-weight:700;
    cursor:pointer; font-size:14px; }
  button.send:disabled { opacity:.5; cursor:default; }

  /* responsive: stack the patient header, tighten paddings, let badges/examples wrap on phones */
  @media (max-width: 620px) {
    header.app { padding:9px 12px; }
    header.app .tag { font-size:10px; padding:2px 7px; }
    .patient { padding:9px 12px; flex-wrap:wrap; }
    .patient .reason { margin-left:0; text-align:left; margin-top:3px; }
    #log { padding:12px; gap:12px; }
    .brief .head { padding:9px 12px; }
    .brief .body { padding:4px 12px 12px; }
    .composer { padding:8px 11px 11px; }
    .row.user .bubble { max-width:88%; }
    .pop { max-width:88vw; }
  }
</style>
</head>
<body>
<header class="app"><span class="dot"></span><span class="brand">Clinical Co-Pilot</span>
  <span class="tag">READ-ONLY · VERIFY-THEN-FLUSH</span></header>

<div class="patient" id="phead">
  <div class="avatar" id="pavatar">–</div>
  <div>
    <div class="who skeleton" id="pname">Patient Name</div>
    <div class="meta skeleton" id="pmeta">00 yrs · —</div>
  </div>
  <div class="reason"><div class="k">Today</div><div class="v">Pre-visit chart review</div></div>
</div>

<div id="log"></div>

<div class="composer">
  <div class="examples" id="examples"></div>
  <form id="f" autocomplete="off">
    <input id="msg" placeholder="Ask a follow-up about this patient…">
    <button class="send" id="send" type="submit">Send</button>
  </form>
</div>

<script>
(function () {
  var log = document.getElementById('log');
  var form = document.getElementById('f');
  var input = document.getElementById('msg');
  var sendBtn = document.getElementById('send');
  var sid = new URLSearchParams(location.search).get('sid');

  var EXAMPLES = [
    "What are the patient's active problems?",
    "What medications is the patient currently taking?",
    "What are the most recent lab results?"
  ];
  var exWrap = document.getElementById('examples');
  EXAMPLES.forEach(function (t) {
    var b = document.createElement('button'); b.type = 'button'; b.className = 'ex'; b.textContent = t;
    b.onclick = function () { input.value = t; input.focus(); };
    exWrap.appendChild(b);
  });

  function el(cls, text) { var d = document.createElement('div'); if (cls) d.className = cls; if (text != null) d.textContent = text; return d; }
  function elt(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }

  // ---- patient header ----
  function ageFrom(dob) {
    if (!dob) return null;
    var d = new Date(dob + "T00:00:00"); if (isNaN(d)) return null;
    var n = new Date(), a = n.getFullYear() - d.getFullYear();
    if (n.getMonth() < d.getMonth() || (n.getMonth() === d.getMonth() && n.getDate() < d.getDate())) a--;
    return a;
  }
  function setHeader(p) {
    if (!p || !p.name) return;
    document.getElementById('pname').className = 'who';
    document.getElementById('pname').textContent = p.name;
    var age = ageFrom(p.birth_date);
    var bits = [];
    if (age != null) bits.push(age + ' yrs');
    if (p.gender) bits.push(p.gender.charAt(0).toUpperCase() + p.gender.slice(1));
    if (p.birth_date) bits.push('DOB ' + p.birth_date);
    document.getElementById('pmeta').className = 'meta';
    document.getElementById('pmeta').textContent = bits.join(' · ');
    var initials = p.name.split(/\\s+/).filter(Boolean).map(function (w) { return w[0]; }).join('').slice(0, 2).toUpperCase();
    document.getElementById('pavatar').textContent = initials || '–';
  }

  // ---- parse the served brief text into sections ----
  var SECTIONS = [
    { key: 'problems',      title: 'Problems',    icon: '◉' },
    { key: 'medications',   title: 'Medications', icon: '℞' },
    { key: 'labs',          title: 'Labs',        icon: '⚕' },
    { key: 'allergies',     title: 'Allergies',   icon: '⚠' },
    { key: 'immunizations', title: 'Immunizations', icon: '✔' }
  ];
  function classify(line) {
    var l = line.toLowerCase();
    if (/confirm with patient|no allergy records|^allergy:|allergies:/.test(l)) return 'allergies';
    if (/^immunization:|vaccine/.test(l)) return 'immunizations';
    // Labs BEFORE meds: a lab unit like "mg/dL" contains "mg", which the medication heuristic
    // below would otherwise claim. A "Display: <number>" shape is the reliable lab signal.
    if (/:\\s*[-0-9.]+/.test(line)) return 'labs';
    if (/ — |\\bmg\\b|tablet|capsule|sublingual|oral gel|injection/.test(l)) return 'medications';
    return 'problems';
  }
  var HEADER_MAP = { 'problems':'problems','medications':'medications','labs':'labs',
    'labs / observations':'labs','observations':'labs','allergies':'allergies','immunizations':'immunizations' };
  function parseBrief(text) {
    var out = { problems:[], medications:[], labs:[], allergies:[], immunizations:[] };
    var cur = null;
    (text || '').split('\\n').forEach(function (raw) {
      var line = raw.trim();
      if (!line) return;
      if (/^generated without llm|^verified summary/i.test(line)) return;   // headers, not content
      var mh = line.match(/^#{1,3}\\s+(.*)$/);
      if (mh) { cur = HEADER_MAP[mh[1].trim().toLowerCase()] || null; return; }  // fallback section headers
      var body = line.replace(/^[-*\\u2022]\\s*/, '').trim();
      if (!body) return;
      var sec = cur || classify(body);
      if (!out[sec]) sec = 'problems';
      out[sec].push(body);
    });
    return out;
  }

  // ---- citation popover ----
  var CITATION_LABELS = Object.freeze({
    patient_record: 'Chart',
    uploaded_document: 'Uploaded document',
    guideline: 'Guideline'
  });
  function validCitation(citation) {
    if (!citation || typeof citation !== 'object' || Array.isArray(citation)) return false;
    if (!Object.prototype.hasOwnProperty.call(CITATION_LABELS, citation.source_type)) return false;
    if (typeof citation.source_id !== 'string' || !citation.source_id.trim()) return false;
    if (typeof citation.field_or_chunk_id !== 'string' || !citation.field_or_chunk_id.trim()) return false;
    if (typeof citation.quote_or_value !== 'string' || !citation.quote_or_value.trim()) return false;
    if (citation.source_type === 'patient_record') return citation.page_or_section === null;
    return typeof citation.page_or_section === 'string' && Boolean(citation.page_or_section.trim());
  }
  function citationLabel(citation) { return CITATION_LABELS[citation.source_type]; }
  // R01 (AF-P0-03): `claims[]` is the authoritative per-claim lane. Each entry must own
  // at least one CitationV2 of ITS declared source class; anything else is invalid and
  // never renders as fact (mirror of the server-side fail-closed contract).
  var CLAIM_VERDICTS = ['pass', 'flagged'];
  function validClaim(claim) {
    if (!claim || typeof claim !== 'object' || Array.isArray(claim)) return false;
    if (typeof claim.text !== 'string' || !claim.text.trim()) return false;
    if (!Object.prototype.hasOwnProperty.call(CITATION_LABELS, claim.source_class)) return false;
    if (CLAIM_VERDICTS.indexOf(claim.verdict) === -1) return false;
    if (!Array.isArray(claim.citations) || !claim.citations.length) return false;
    return claim.citations.every(function (citation) {
      return validCitation(citation) && citation.source_type === claim.source_class;
    });
  }
  function compactEvidenceKey(citation) {
    var key = citation.field_or_chunk_id;
    return key.length > 22 ? '…' + key.slice(-21) : key;
  }
  var pop = null, popChip = null, popOpenedAt = 0;
  function closePop() { if (pop) { pop.remove(); pop = null; popChip = null; } }
  document.addEventListener('click', function (e) { if (pop && !e.target.classList.contains('chip')) closePop(); });
  window.addEventListener('resize', closePop);
  // Close on a genuine log scroll (the popover is fixed-position and would detach), but ignore
  // the brief scroll-into-view that can accompany the opening click — else the click that opens
  // the popover immediately closes it when the chip was below the fold.
  log.addEventListener('scroll', function () { if (pop && Date.now() - popOpenedAt > 350) closePop(); }, true);
  function showPop(chip, citation) {
    if (!validCitation(citation)) return;
    if (popChip === chip) { closePop(); return; }   // clicking the same chip toggles it off
    closePop();
    // Build with DOM + textContent: every citation value is treated as untrusted data.
    pop = el('pop'); popChip = chip; popOpenedAt = Date.now();
    var suffix = citation.source_type === 'patient_record' ? ' — chart record' : ' — cited evidence';
    var title = el(null, citationLabel(citation) + suffix); title.style.cssText = 'font-weight:700;margin-bottom:4px';
    pop.appendChild(title);
    var r1 = el(null); r1.appendChild(document.createTextNode('source id: '));
    r1.appendChild(elt('span', 'pk', citation.source_id)); pop.appendChild(r1);
    if (citation.page_or_section !== null) {
      var location = el(null); location.appendChild(document.createTextNode('page / section: '));
      location.appendChild(elt('span', 'pk', citation.page_or_section)); pop.appendChild(location);
    }
    var r2 = el(null); r2.appendChild(document.createTextNode('field / chunk: '));
    r2.appendChild(elt('span', 'pk', citation.field_or_chunk_id)); pop.appendChild(r2);
    var r3 = el(null); r3.appendChild(document.createTextNode('verified value: '));
    r3.appendChild(elt('span', 'pk', citation.quote_or_value)); pop.appendChild(r3);
    var note = el(null, '✓ this line was verified field-by-field against this record');
    note.style.cssText = 'margin-top:5px;color:#9fd0a8'; pop.appendChild(note);
    document.body.appendChild(pop);
    var r = chip.getBoundingClientRect(), pw = pop.offsetWidth, ph = pop.offsetHeight;
    pop.style.left = Math.max(8, Math.min(r.left, window.innerWidth - pw - 8)) + 'px';
    var top = r.top - ph - 8;                                   // prefer above the chip…
    if (top < 8) top = Math.min(r.bottom + 8, window.innerHeight - ph - 8);  // …flip below if no room
    pop.style.top = Math.max(8, top) + 'px';
  }

  // ---- render one assistant brief ----
  function renderAssistant(card, data) {
    setHeader(data.patient);
    card.className = 'brief'; card.replaceChildren();

    var verdicts = data.verdicts || [];
    var verified = verdicts.filter(function (v) { return v === 'pass' || v === 'flagged'; }).length;
    var dropped = verdicts.filter(function (v) { return v === 'blocked' || (v && v.indexOf('refus') === 0); }).length;
    var isLLM = data.source === 'llm';

    var head = el('head');
    head.appendChild(el('title', 'Pre-visit brief'));
    head.appendChild(Object.assign(el('badge ok'), { textContent: '✓ ' + verified + ' verified' }));
    head.appendChild(Object.assign(el('badge drop'), { textContent: '✕ ' + dropped + ' dropped' }));
    head.appendChild(Object.assign(el('badge src'), { textContent: isLLM ? 'LLM + verified' : 'grounded fallback' }));
    if (data.degraded) head.appendChild(Object.assign(el('badge warn'), { textContent: 'degraded' }));
    card.appendChild(head);

    var body = el('body');
    var sections = parseBrief(data.brief);

    // "Review before entering" attention panel
    var flags = [];
    var allergyConfirm = sections.allergies.filter(function (a) { return /confirm with patient|no allergy records/i.test(a); });
    if (allergyConfirm.length) flags.push('Allergies not confirmed in the chart — verify directly with the patient (missing ≠ none known).');
    if (dropped > 0) flags.push(dropped + ' statement' + (dropped === 1 ? '' : 's') + ' could not be verified against the chart and ' + (dropped === 1 ? 'was' : 'were') + ' withheld — review the record directly.');
    if (!isLLM) flags.push('Automated grounded fallback — clinical synthesis was not performed; read the records below directly.');

    var att = el('attention');
    if (flags.length) {
      att.appendChild(elt('h4', null, '⚠ Review before entering'));
      var ul = elt('ul'); flags.forEach(function (f) { ul.appendChild(elt('li', null, f)); }); att.appendChild(ul);
    } else {
      att.className = 'attention clear';
      att.appendChild(elt('h4', null, '✓ Reviewed'));
      var ulc = elt('ul'); ulc.appendChild(elt('li', null, 'No blocking flags — every line below is verified against the chart.')); att.appendChild(ulc);
    }
    body.appendChild(att);

    // sections
    SECTIONS.forEach(function (s) {
      var items = sections[s.key]; if (!items || !items.length) return;
      var sec = el('section');
      var sh = el('sh'); sh.appendChild(el(null, s.icon + ' ' + s.title));
      sh.appendChild(Object.assign(el('n'), { textContent: items.length })); sec.appendChild(sh);
      items.forEach(function (line) {
        var amber = s.key === 'allergies' && /confirm with patient|no allergy records/i.test(line);
        var it = el('item' + (amber ? ' amber' : ''));
        it.appendChild(Object.assign(el('ic'), { textContent: amber ? '⚠' : s.icon }));
        it.appendChild(el(null, line.replace(/^⚠\\s*/, '')));
        sec.appendChild(it);
      });
      body.appendChild(sec);
    });

    // empty-state guard: a verified response that parsed no clinical lines still shows the
    // attention panel above; make the absence explicit rather than a blank card.
    var totalItems = SECTIONS.reduce(function (n, s) { return n + (sections[s.key] ? sections[s.key].length : 0); }, 0);
    if (!totalItems) body.appendChild(el('meta', 'No verified clinical lines to display — see the note above.'));

    function citationChip(citation) {
      var chip = el('chip', citationLabel(citation) + ' · ' + compactEvidenceKey(citation));
      chip.title = citation.source_id + ' · ' + citation.field_or_chunk_id;
      chip.tabIndex = 0; chip.setAttribute('role', 'button');
      chip.onclick = function (e) { e.stopPropagation(); showPop(chip, citation); };
      chip.onkeydown = function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); showPop(chip, citation); } };
      return chip;
    }

    // authoritative per-claim lane (R01/AF-P0-03): each served claim renders with ITS
    // OWN citation chips, so which citation supports which claim is never ambiguous.
    var claims = (Array.isArray(data.claims) ? data.claims : []).filter(validClaim);
    if (claims.length) {
      var csec = el('section');
      var csh = el('sh'); csh.appendChild(el(null, '❋ Cited claims'));
      csh.appendChild(Object.assign(el('n'), { textContent: claims.length })); csec.appendChild(csh);
      claims.forEach(function (claim) {
        var amber = claim.verdict === 'flagged';
        var it = el('item' + (amber ? ' amber' : ''));
        it.appendChild(Object.assign(el('ic'), { textContent: amber ? '⚠' : '✓' }));
        var wrap = el(null);
        wrap.appendChild(el(null, claim.text));
        var cw = el('cites');
        cw.appendChild(el('lbl', 'Cited from ' + CITATION_LABELS[claim.source_class] + ':'));
        claim.citations.forEach(function (citation) { cw.appendChild(citationChip(citation)); });
        wrap.appendChild(cw);
        it.appendChild(wrap);
        csec.appendChild(it);
      });
      body.appendChild(csec);
    }

    // legacy flat citation chips (derived, non-authoritative compatibility view) —
    // only when the authoritative per-claim lane is absent from the response.
    var cites = claims.length ? [] : (Array.isArray(data.citations) ? data.citations : []).filter(validCitation);
    if (cites.length) {
      var cw = el('cites'); cw.appendChild(el('lbl', 'Evidence:'));
      cites.slice(0, 30).forEach(function (citation) {
        var chip = el('chip', citationLabel(citation) + ' · ' + compactEvidenceKey(citation));
        chip.title = citation.source_id + ' · ' + citation.field_or_chunk_id;
        chip.tabIndex = 0; chip.setAttribute('role', 'button');
        chip.onclick = function (e) { e.stopPropagation(); showPop(chip, citation); };
        chip.onkeydown = function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); showPop(chip, citation); } };
        cw.appendChild(chip);
      });
      if (cites.length > 30) cw.appendChild(el('chip', '+' + (cites.length - 30) + ' more'));
      body.appendChild(cw);
    }
    if (data.correlation_id) body.appendChild(el('meta', 'trace ' + data.correlation_id));
    card.appendChild(body);
    log.scrollTop = log.scrollHeight;
  }

  // ---- progressive loading (shows the work during the ~35s call) ----
  var LOAD_STEPS = [
    'Authenticating the SMART session',
    'Reading the chart (6 FHIR resources)',
    'Verifying each claim against the evidence',
    'Rendering the verified brief'
  ];
  function renderLoading(card) {
    card.className = 'brief'; card.replaceChildren();
    var head = el('head'); head.appendChild(el('title', 'Preparing pre-visit brief…')); card.appendChild(head);
    var body = el('body'); var ul = elt('ul', 'steps');
    LOAD_STEPS.forEach(function (s, i) {
      var li = elt('li', i === 0 ? 'active' : ''); li.appendChild(el('b')); li.appendChild(el(null, s));
      ul.appendChild(li);
    });
    body.appendChild(ul); card.appendChild(body);
    var lis = ul.querySelectorAll('li'); var i = 0;
    // advance through the first three steps on a representative cadence; the last holds until the
    // real response returns (verification of a rich chart dominates the wall-clock).
    var timers = [];
    [1800, 5000, 12000].forEach(function (t, k) {
      timers.push(setTimeout(function () {
        if (lis[k]) { lis[k].className = 'done'; lis[k].querySelector('.b').textContent = '✓'; }
        if (lis[k + 1]) lis[k + 1].className = 'active';
      }, t));
    });
    return { finish: function () { timers.forEach(clearTimeout);
      lis.forEach(function (li) { li.className = 'done'; li.querySelector('.b').textContent = '✓'; }); } };
  }

  function addUser(text) {
    var row = el('row user'); row.appendChild(el('bubble', text)); log.appendChild(row);
    log.scrollTop = log.scrollHeight;
  }
  function renderError(card, msg) {
    card.className = 'brief'; card.replaceChildren();
    var b = el('body'); b.style.color = '#b3261e';
    b.appendChild(el(null, '⚠ ' + msg)); card.appendChild(b);
  }

  async function ask(message) {
    var card = el(''); log.appendChild(card);
    if (!sid) { renderError(card, 'No session — start a SMART launch at /launch.'); return; }
    sendBtn.disabled = true; input.disabled = true;
    var loader = renderLoading(card);
    try {
      var resp = await fetch('/chat', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sid, message: message }) });
      loader.finish();
      if (!resp.ok) {
        var d = await resp.json().catch(function () { return {}; });
        renderError(card, 'HTTP ' + resp.status + ' — ' + (d.detail || 'request failed'));
      } else {
        renderAssistant(card, await resp.json());
      }
    } catch (e) {
      loader.finish(); renderError(card, 'Network error: ' + e);
    } finally {
      sendBtn.disabled = false; input.disabled = false; input.focus();
    }
  }

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var m = input.value.trim(); if (!m) return;
    input.value = ''; addUser(m); ask(m);
  });

  // auto-run the pre-visit brief on load (the demo essential)
  var opening = 'Give me the pre-visit brief for this patient.';
  addUser(opening); ask(opening);
})();
</script>
</body>
</html>
"""


@router.get("/app", response_class=HTMLResponse)
async def app_page() -> HTMLResponse:
    return HTMLResponse(content=_PAGE)
