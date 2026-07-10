"""GET /app — a minimal single-page chat UI for the pre-visit co-pilot (T-E9 demo UI).

Presentation layer ONLY. It is a self-contained HTML page (inline CSS/JS, no build step) that
calls the EXISTING POST /chat for the session pinned by the SMART launch and renders the
verified brief as a chat message — citation chips from the response's `citations`, a
"verified / dropped" badge from `verdicts`. It changes nothing about verification or serving:
it is a client of /chat like any other. The session id arrives as `?sid=` (the /callback
redirect); production would carry it in a cookie instead of the URL.
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
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
         background: #eef1f6; color: #1a2233; height: 100vh; display: flex; flex-direction: column; }
  header { background: #123a5e; color: #fff; padding: 12px 18px; font-weight: 600; font-size: 16px;
           display: flex; align-items: center; gap: 10px; box-shadow: 0 1px 4px rgba(0,0,0,.2); }
  header .dot { width: 9px; height: 9px; border-radius: 50%; background: #4ade80; }
  header small { font-weight: 400; opacity: .8; font-size: 12px; margin-left: auto; }
  #log { flex: 1; overflow-y: auto; padding: 18px; display: flex; flex-direction: column; gap: 14px; }
  .row { display: flex; }
  .row.user { justify-content: flex-end; }
  .bubble { max-width: 760px; padding: 12px 15px; border-radius: 14px; line-height: 1.5;
            box-shadow: 0 1px 2px rgba(0,0,0,.08); font-size: 14px; }
  .user .bubble { background: #123a5e; color: #fff; border-bottom-right-radius: 4px; }
  .assistant .bubble { background: #fff; color: #1a2233; border-bottom-left-radius: 4px; }
  .brief { white-space: pre-wrap; word-break: break-word; }
  .badges { margin-top: 10px; display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
  .badge { font-size: 12px; padding: 3px 9px; border-radius: 20px; font-weight: 600; }
  .badge.ok { background: #dcfce7; color: #166534; }
  .badge.drop { background: #fee2e2; color: #991b1b; }
  .badge.src { background: #e0e7ff; color: #3730a3; }
  .badge.warn { background: #fef9c3; color: #854d0e; }
  .cites { margin-top: 8px; display: flex; flex-wrap: wrap; gap: 5px; }
  .chip { font-size: 11px; padding: 2px 8px; border-radius: 6px; background: #f1f5f9; color: #334155;
          border: 1px solid #cbd5e1; font-family: ui-monospace, Menlo, monospace; cursor: default; }
  .meta { margin-top: 8px; font-size: 11px; color: #94a3b8; }
  form { display: flex; gap: 8px; padding: 12px; background: #fff; border-top: 1px solid #d7dce5; }
  #msg { flex: 1; padding: 11px 13px; border: 1px solid #c3ccd9; border-radius: 10px; font-size: 14px; }
  button { padding: 0 20px; border: 0; border-radius: 10px; background: #123a5e; color: #fff;
           font-weight: 600; cursor: pointer; font-size: 14px; }
  button:disabled { opacity: .5; cursor: default; }
  .typing { color: #64748b; font-style: italic; }
</style>
</head>
<body>
<header><span class="dot"></span> Clinical Co-Pilot <small id="sub">read-only · verify-then-flush</small></header>
<div id="log"></div>
<form id="f" autocomplete="off">
  <input id="msg" placeholder="Ask a follow-up about this patient…">
  <button id="send" type="submit">Send</button>
</form>
<script>
(function () {
  var log = document.getElementById('log');
  var form = document.getElementById('f');
  var input = document.getElementById('msg');
  var sendBtn = document.getElementById('send');
  var sid = new URLSearchParams(location.search).get('sid');

  function el(cls, text) { var d = document.createElement('div'); d.className = cls; if (text != null) d.textContent = text; return d; }

  function addUser(text) {
    var row = el('row user'); var b = el('bubble'); b.appendChild(el('brief', text));
    row.appendChild(b); log.appendChild(row); log.scrollTop = log.scrollHeight;
  }

  function addTyping() {
    var row = el('row assistant'); var b = el('bubble');
    b.appendChild(el('brief typing', 'Reviewing the chart, verifying against evidence…'));
    row.appendChild(b); log.appendChild(row); log.scrollTop = log.scrollHeight; return row;
  }

  function renderAssistant(row, data) {
    row.innerHTML = ''; var b = el('bubble');
    b.appendChild(el('brief', data.brief || '(empty)'));

    var verdicts = data.verdicts || [];
    var verified = verdicts.filter(function (v) { return v === 'pass' || v === 'flagged'; }).length;
    var dropped = verdicts.filter(function (v) { return v === 'blocked' || v === 'refused' || (v && v.indexOf('refused') === 0); }).length;
    var badges = el('badges');
    badges.appendChild(Object.assign(el('badge ok'), { textContent: '\\u2713 ' + verified + ' verified' }));
    badges.appendChild(Object.assign(el('badge drop'), { textContent: '\\u2715 ' + dropped + ' dropped' }));
    badges.appendChild(Object.assign(el('badge src'), { textContent: data.source === 'llm' ? 'LLM + verified' : (data.source || '') }));
    if (data.degraded) badges.appendChild(Object.assign(el('badge warn'), { textContent: 'degraded' }));
    b.appendChild(badges);

    var cites = data.citations || [];
    if (cites.length) {
      var wrap = el('cites');
      cites.slice(0, 24).forEach(function (c) {
        var parts = String(c).split(':'); var type = parts[0] || 'ref'; var short = parts[parts.length - 1] || c;
        var chip = el('chip', type + ' \\u00b7 ' + short); chip.title = c; wrap.appendChild(chip);
      });
      if (cites.length > 24) wrap.appendChild(el('chip', '+' + (cites.length - 24) + ' more'));
      b.appendChild(wrap);
    }

    if (data.correlation_id) b.appendChild(el('meta', 'trace ' + data.correlation_id));
    row.appendChild(b); log.scrollTop = log.scrollHeight;
  }

  function renderError(row, msg) {
    row.innerHTML = ''; var b = el('bubble');
    b.appendChild(el('brief', '\\u26a0 ' + msg));
    row.appendChild(b); log.scrollTop = log.scrollHeight;
  }

  async function ask(message) {
    if (!sid) { addUser(message); var r0 = addTyping(); renderError(r0, 'No session. Start a SMART launch at /launch.'); return; }
    sendBtn.disabled = true; input.disabled = true;
    var row = addTyping();
    try {
      var resp = await fetch('/chat', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sid, message: message })
      });
      if (!resp.ok) {
        var d = await resp.json().catch(function () { return {}; });
        renderError(row, 'HTTP ' + resp.status + ' — ' + (d.detail || 'request failed'));
      } else {
        renderAssistant(row, await resp.json());
      }
    } catch (e) {
      renderError(row, 'Network error: ' + e);
    } finally {
      sendBtn.disabled = false; input.disabled = false; input.focus();
    }
  }

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var m = input.value.trim(); if (!m) return;
    input.value = ''; addUser(m); ask(m);
  });

  // Auto-run the pre-visit brief on load (the demo essential).
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
