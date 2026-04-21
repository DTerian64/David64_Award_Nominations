"""
web_app.py
──────────
A lightweight FastAPI web UI for testing the Nominations SQL Agent
locally in Docker — no Claude Desktop required.

Exposes:
  GET  /           → browser chat UI
  POST /ask        → { question } → { sql, intent }
  POST /batch      → { questions: [] } → { results: [] }
  GET  /templates  → pre-built SQL templates
  POST /validate   → { question, sql } → { valid, issues, suggestions }
  GET  /health     → { status, deployment, endpoint }
"""

import os
import json
import logging
import time
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from dotenv import load_dotenv
from logging_config import setup_logging, get_logger

# ── startup ──────────────────────────────────
load_dotenv()
setup_logging()
log = get_logger("web_app")

# Import the AI helpers directly from server.py
from server import generate_sql, classify_query, validate_sql, TEMPLATE_QUERIES, DEPLOYMENT, AZURE_ENDPOINT

app = FastAPI(title="Nominations SQL Agent", version="1.0.0")
log.info("FastAPI app initialised")


# ── request / response models ─────────────────
class AskRequest(BaseModel):
    question: str

class BatchRequest(BaseModel):
    questions: list[str]

class ValidateRequest(BaseModel):
    question: str
    sql: str


# ── routes ────────────────────────────────────
@app.get("/health")
def health():
    return {
        "status": "ok",
        "deployment": DEPLOYMENT,
        "endpoint": AZURE_ENDPOINT,
    }


@app.post("/ask")
def ask(req: AskRequest):
    log.info("POST /ask — %s", req.question)
    t0 = time.perf_counter()
    try:
        sql    = generate_sql(req.question)
        intent = classify_query(req.question)
        elapsed = time.perf_counter() - t0
        log.info("POST /ask completed in %.2fs", elapsed)
        return {"success": True, "question": req.question, "sql": sql, "intent": intent, "elapsed": round(elapsed, 2)}
    except Exception as e:
        log.error("POST /ask failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/batch")
def batch(req: BatchRequest):
    log.info("POST /batch — %d questions", len(req.questions))
    results = []
    for question in req.questions:
        try:
            results.append({"question": question, "sql": generate_sql(question), "success": True})
        except Exception as e:
            log.error("Batch item failed: %s", e)
            results.append({"question": question, "error": str(e), "success": False})
    return {"results": results}


@app.get("/templates")
def templates():
    return {"templates": TEMPLATE_QUERIES}


@app.post("/validate")
def validate(req: ValidateRequest):
    log.info("POST /validate — %s", req.question)
    return validate_sql(req.question, req.sql)


@app.get("/", response_class=HTMLResponse)
def ui():
    """Serve a single-page chat UI."""
    return HTMLResponse(content=CHAT_UI)


# ── embedded single-file UI ───────────────────
CHAT_UI = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Nominations SQL Agent</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #0f1117; color: #e2e8f0; min-height: 100vh; display: flex; flex-direction: column; }
  header { background: #1a1d2e; border-bottom: 1px solid #2d3748; padding: 16px 24px;
           display: flex; align-items: center; gap: 12px; }
  header h1 { font-size: 18px; font-weight: 600; color: #fff; }
  header span { font-size: 13px; color: #718096; }
  .badge { background: #2d3748; border: 1px solid #4a5568; border-radius: 6px;
           padding: 3px 10px; font-size: 12px; color: #a0aec0; }
  main { flex: 1; display: flex; gap: 0; }

  /* ── left: chat ── */
  .chat-pane { flex: 1; display: flex; flex-direction: column; min-width: 0; }
  .messages { flex: 1; overflow-y: auto; padding: 24px; display: flex; flex-direction: column; gap: 16px; }
  .msg { max-width: 85%; }
  .msg.user { align-self: flex-end; }
  .msg.bot  { align-self: flex-start; }
  .bubble { border-radius: 12px; padding: 12px 16px; font-size: 14px; line-height: 1.5; }
  .msg.user .bubble { background: #3b5bdb; color: #fff; border-bottom-right-radius: 4px; }
  .msg.bot  .bubble { background: #1a1d2e; border: 1px solid #2d3748; border-bottom-left-radius: 4px; }
  .msg-meta { font-size: 11px; color: #4a5568; margin-top: 4px; padding: 0 4px; }
  .msg.user .msg-meta { text-align: right; }

  .input-bar { padding: 16px 24px; background: #1a1d2e; border-top: 1px solid #2d3748;
               display: flex; gap: 10px; }
  .input-bar input { flex: 1; background: #0f1117; border: 1px solid #2d3748; border-radius: 8px;
                     padding: 10px 14px; color: #e2e8f0; font-size: 14px; outline: none; }
  .input-bar input:focus { border-color: #3b5bdb; }
  .input-bar button { background: #3b5bdb; color: #fff; border: none; border-radius: 8px;
                      padding: 10px 20px; font-size: 14px; cursor: pointer; white-space: nowrap; }
  .input-bar button:hover { background: #2f4ac7; }
  .input-bar button:disabled { background: #2d3748; cursor: not-allowed; }

  /* ── right: sql panel ── */
  .sql-pane { width: 420px; background: #1a1d2e; border-left: 1px solid #2d3748;
              display: flex; flex-direction: column; }
  .sql-pane-header { padding: 14px 18px; border-bottom: 1px solid #2d3748;
                     font-size: 13px; font-weight: 600; color: #a0aec0; display: flex;
                     justify-content: space-between; align-items: center; }
  .copy-btn { background: #2d3748; border: none; color: #a0aec0; border-radius: 5px;
              padding: 4px 10px; font-size: 12px; cursor: pointer; }
  .copy-btn:hover { background: #4a5568; }
  pre { flex: 1; overflow: auto; padding: 18px; font-size: 13px; line-height: 1.6;
        color: #a8dadc; font-family: 'Fira Code', 'Cascadia Code', monospace; white-space: pre-wrap; }
  .intent-bar { padding: 10px 18px; border-top: 1px solid #2d3748; font-size: 12px;
                color: #718096; display: flex; gap: 16px; flex-wrap: wrap; }
  .intent-bar strong { color: #a0aec0; }

  /* ── samples ── */
  .samples { padding: 0 24px 16px; display: flex; flex-wrap: wrap; gap: 8px; }
  .sample-btn { background: #1a1d2e; border: 1px solid #2d3748; color: #a0aec0; border-radius: 20px;
                padding: 6px 14px; font-size: 12px; cursor: pointer; white-space: nowrap; }
  .sample-btn:hover { border-color: #3b5bdb; color: #e2e8f0; }

  .spinner { display: inline-block; width: 14px; height: 14px; border: 2px solid #4a5568;
             border-top-color: #a0aec0; border-radius: 50%; animation: spin .7s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>

<header>
  <div>🏆</div>
  <h1>Nominations SQL Agent</h1>
  <span class="badge">Azure OpenAI</span>
  <span class="badge" id="health-badge">checking...</span>
</header>

<main>
  <div class="chat-pane">
    <div class="messages" id="messages">
      <div class="msg bot">
        <div class="bubble">👋 Ask me anything about nominations in plain English and I'll generate the T-SQL for you.</div>
      </div>
    </div>

    <div class="samples" id="samples">
      <button class="sample-btn" onclick="fillQ('How many Pending approvals does Gary Lawsen have?')">Pending approvals for Gary Lawsen</button>
      <button class="sample-btn" onclick="fillQ('What are the top 5 nominators in the system?')">Top 5 nominators</button>
      <button class="sample-btn" onclick="fillQ('How much in dollar amounts is approved for the last 30 days?')">Approved dollars last 30 days</button>
      <button class="sample-btn" onclick="fillQ('Show me all Rejected nominations this week')">Rejected this week</button>
      <button class="sample-btn" onclick="fillQ('Who are the top 3 beneficiaries by total dollar amount?')">Top 3 beneficiaries</button>
    </div>

    <div class="input-bar">
      <input id="q" type="text" placeholder="e.g. How many pending approvals does Sarah have?" onkeydown="if(event.key==='Enter') send()" />
      <button id="send-btn" onclick="send()">Generate SQL</button>
    </div>
  </div>

  <div class="sql-pane">
    <div class="sql-pane-header">
      Generated T-SQL
      <button class="copy-btn" onclick="copySQL()">Copy</button>
    </div>
    <pre id="sql-output">-- Your SQL will appear here</pre>
    <div class="intent-bar" id="intent-bar">Ask a question to get started</div>
  </div>
</main>

<script>
  // Health check
  fetch('/health').then(r => r.json()).then(d => {
    const badge = document.getElementById('health-badge');
    badge.textContent = d.deployment;
    badge.style.color = '#68d391';
  }).catch(() => {
    document.getElementById('health-badge').textContent = 'offline';
  });

  function fillQ(text) {
    document.getElementById('q').value = text;
    document.getElementById('q').focus();
  }

  function addMsg(text, role) {
    const wrap = document.createElement('div');
    wrap.className = `msg ${role}`;
    wrap.innerHTML = `<div class="bubble">${text}</div>
      <div class="msg-meta">${new Date().toLocaleTimeString()}</div>`;
    document.getElementById('messages').appendChild(wrap);
    wrap.scrollIntoView({ behavior: 'smooth' });
    return wrap;
  }

  async function send() {
    const input = document.getElementById('q');
    const question = input.value.trim();
    if (!question) return;

    const btn = document.getElementById('send-btn');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span>';
    input.value = '';

    addMsg(question, 'user');
    const thinkingMsg = addMsg('<span class="spinner"></span> Generating SQL...', 'bot');

    try {
      const res = await fetch('/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question })
      });
      const data = await res.json();

      if (data.success) {
        thinkingMsg.querySelector('.bubble').textContent =
          `✅ SQL generated in ${data.elapsed}s — see the panel on the right.`;
        document.getElementById('sql-output').textContent = data.sql;
        document.getElementById('intent-bar').innerHTML =
          `<strong>Intent:</strong> ${data.intent?.category ?? '—'} &nbsp;|&nbsp;
           <strong>Entities:</strong> ${(data.intent?.entities ?? []).join(', ') || '—'} &nbsp;|&nbsp;
           <strong>Aggregation:</strong> ${data.intent?.aggregation ?? '—'}`;
      } else {
        thinkingMsg.querySelector('.bubble').textContent = `❌ ${data.detail || data.error}`;
      }
    } catch (e) {
      thinkingMsg.querySelector('.bubble').textContent = `❌ Network error: ${e.message}`;
    }

    btn.disabled = false;
    btn.textContent = 'Generate SQL';
    input.focus();
  }

  function copySQL() {
    const sql = document.getElementById('sql-output').textContent;
    navigator.clipboard.writeText(sql).then(() => {
      const btn = document.querySelector('.copy-btn');
      btn.textContent = 'Copied!';
      setTimeout(() => btn.textContent = 'Copy', 1500);
    });
  }
</script>
</body>
</html>
"""
