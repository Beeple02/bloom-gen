import os
import json

import httpx
from flask import Flask, Response, render_template_string, stream_with_context
import google.generativeai as genai

app = Flask(__name__)

ATLAS_URL = os.environ.get("ATLAS_URL", "").rstrip("/")
ATLAS_KEY = os.environ.get("ATLAS_KEY", "atl_Bloom_mkt_reports_MKZaifOWZHoAlDSWYWBaGCtUfFxx5Fvd")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# ─── Atlas Data Fetcher ────────────────────────────────────────────────────────

def atlas_headers():
    return {"X-Atlas-Key": ATLAS_KEY}

def fetch_atlas_data():
    """Fetch all required Atlas endpoints and bundle into a dict."""
    headers = atlas_headers()
    data = {}
    tickers = []

    with httpx.Client(timeout=15) as client:
        # Core endpoints
        endpoints = {
            "securities": f"{ATLAS_URL}/securities?include_derived=true",
            "orderbook":  f"{ATLAS_URL}/orderbook",
            "summary":    f"{ATLAS_URL}/market/summary",
            "derived":    f"{ATLAS_URL}/derived",
        }
        for key, url in endpoints.items():
            try:
                r = client.get(url, headers=headers)
                r.raise_for_status()
                data[key] = r.json()
            except Exception as e:
                data[key] = {"error": str(e)}

        # Extract tickers for per-security calls
        secs = data.get("securities", [])
        if isinstance(secs, list):
            tickers = [s.get("ticker") or s.get("symbol") for s in secs if s.get("ticker") or s.get("symbol")]
        elif isinstance(secs, dict) and "securities" in secs:
            tickers = [s.get("ticker") or s.get("symbol") for s in secs["securities"] if s.get("ticker") or s.get("symbol")]

        # History per ticker (limit to first 10 to stay snappy)
        history = {}
        shareholders = {}
        for ticker in tickers[:10]:
            try:
                r = client.get(f"{ATLAS_URL}/history/{ticker}?days=7&limit=50", headers=headers)
                r.raise_for_status()
                history[ticker] = r.json()
            except Exception as e:
                history[ticker] = {"error": str(e)}
            try:
                r = client.get(f"{ATLAS_URL}/shareholders/{ticker}", headers=headers)
                r.raise_for_status()
                shareholders[ticker] = r.json()
            except Exception as e:
                shareholders[ticker] = {"error": str(e)}

        data["history"] = history
        data["shareholders"] = shareholders

    return data

# ─── Claude Prompt ────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are the Bloomberg Labs Market Analyst for the DemocracyCraft Minecraft server economy.
Bloomberg Labs is a prestigious fictional financial institution tracking the DemocracyCraft in-game market.

You receive live market JSON data from the Atlas Market API and produce a polished, self-contained HTML report snippet.

OUTPUT RULES — READ CAREFULLY:
- Output ONLY valid HTML. No markdown. No code fences. No explanation before or after.
- No <html>, <head>, or <body> tags. No <!DOCTYPE>. Just the inner content.
- All styles must be INLINE (style="..."). No <style> tags. No CSS classes.
- Use IBM Plex Mono (font-family: 'IBM Plex Mono', monospace) for all numeric/data cells.
- Use IBM Plex Sans (font-family: 'IBM Plex Sans', sans-serif) for prose/headings.
- Color conventions: green #16a34a for positive/gains, red #dc2626 for negative/losses, #737373 for neutral/unchanged.
- Background: white #ffffff. Section dividers: 1px solid #e5e7eb.
- Tables: clean, dense, no outer border-radius. Header row bg #f9fafb, text #111827 bold.

REPORT STRUCTURE — produce exactly these 5 sections in order:

1. MARKET SUMMARY
   - One paragraph narrative (2-3 sentences) summarizing overall market conditions.
   - Table: metric | value — key stats from /market/summary and /derived (total volume, active securities, market cap if available, avg spread, avg liquidity score, etc.)
   - Flag if market appears frozen (zero volume, no recent trades).

2. TOP MOVERS
   - One paragraph narrative (2-3 sentences) about standout securities.
   - Table: Ticker | Price | Change % | Volume | VWAP | Volatility — sorted by absolute % change, top 8.
   - Color the Change % cell green or red accordingly.
   - Flag any security with spread > 20% or showing anomalous metrics.

3. PRICE EVOLUTION TABLE
   - One paragraph narrative about price trends over the past 7 days.
   - Table: Ticker | Day-1 | Day-2 | Day-3 | Day-4 | Day-5 | Day-6 | Day-7 (most recent rightmost) — use closing prices from /history data.
   - Use green/red cell background (#dcfce7 / #fee2e2) to show direction vs prior day.
   - If history is missing for a ticker, show "—".

4. ORDERBOOK SNAPSHOT
   - One paragraph narrative about liquidity and order depth.
   - Table: Ticker | Best Bid | Best Ask | Spread | Bid Depth | Ask Depth | Imbalance.
   - Flag empty orderbooks or extreme spreads (>25%).
   - Imbalance = (bid depth - ask depth) / (bid depth + ask depth), show as percentage, color accordingly.

5. SHAREHOLDER ACTIVITY
   - One paragraph narrative about ownership concentration.
   - Table: Ticker | Top Holder | % Owned | #Shareholders | HHI (if computable) — summarize from /shareholders data.
   - Note any single holder with >50% (dominant position) in red.

ANALYST NOTE (after all sections):
- A grey-background box (#f3f4f6), padding 12px, with bold header "ANALYST NOTE".
- 2-3 bullet observations about risks, opportunities, or anomalies you noticed.
- Keep it sharp, professional, and specific to the data you received.

STYLE GUIDANCE:
- Section headers: font-size 11px, letter-spacing 0.1em, text-transform uppercase, color #6b7280, border-bottom 2px solid #111827, margin-bottom 8px.
- Each section wrapped in a div with margin-bottom 32px.
- Tables: width 100%, border-collapse collapse. TD/TH padding: 6px 10px. Alternating row bg: #ffffff / #f9fafb.
- Narrative paragraphs: font-size 13px, line-height 1.6, color #374151, margin-bottom 12px.
- Numbers in table cells: font-family IBM Plex Mono, font-size 12px.
- Be concise but precise. This report will be screenshotted and posted to Discord.
"""

def build_user_prompt(atlas_data: dict) -> str:
    return (
        "Here is the live Atlas market data. Generate the full Bloomberg Labs Daily Report now.\n\n"
        + json.dumps(atlas_data, indent=2, default=str)
    )

# ─── HTML Templates ───────────────────────────────────────────────────────────

INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Bloomberg Labs — Report Generator</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg: #0a0a0a;
    --surface: #111111;
    --border: #1e1e1e;
    --border-bright: #2a2a2a;
    --text: #e5e5e5;
    --text-muted: #737373;
    --text-dim: #404040;
    --green: #16a34a;
    --green-dim: #14532d;
    --red: #dc2626;
    --amber: #d97706;
    --accent: #e5e5e5;
  }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'IBM Plex Sans', sans-serif;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 40px 20px;
  }

  .terminal-frame {
    width: 100%;
    max-width: 680px;
  }

  /* Header */
  .header {
    margin-bottom: 48px;
  }
  .header-eyebrow {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 10px;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    color: var(--text-dim);
    margin-bottom: 12px;
  }
  .header-logo {
    display: flex;
    align-items: baseline;
    gap: 10px;
    margin-bottom: 8px;
  }
  .header-logo .b {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 32px;
    font-weight: 600;
    color: var(--accent);
    letter-spacing: -0.02em;
  }
  .header-logo .sep {
    width: 2px;
    height: 28px;
    background: var(--border-bright);
    display: inline-block;
    margin: 0 4px;
    vertical-align: middle;
  }
  .header-logo .sub {
    font-size: 13px;
    font-weight: 300;
    color: var(--text-muted);
    letter-spacing: 0.05em;
  }
  .header-desc {
    font-size: 12px;
    color: var(--text-dim);
    font-family: 'IBM Plex Mono', monospace;
    margin-top: 6px;
  }

  /* Status bar */
  .status-bar {
    display: flex;
    gap: 24px;
    padding: 12px 0;
    border-top: 1px solid var(--border);
    border-bottom: 1px solid var(--border);
    margin-bottom: 40px;
  }
  .status-item {
    display: flex;
    align-items: center;
    gap: 6px;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 11px;
    color: var(--text-dim);
  }
  .status-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--green);
    box-shadow: 0 0 6px var(--green);
    animation: pulse 2s infinite;
  }
  .status-dot.amber { background: var(--amber); box-shadow: 0 0 6px var(--amber); }
  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
  }

  /* Main action area */
  .action-area {
    padding: 32px;
    border: 1px solid var(--border);
    background: var(--surface);
    position: relative;
    overflow: hidden;
  }
  .action-area::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 1px;
    background: linear-gradient(90deg, transparent, #333, transparent);
  }

  .action-label {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 10px;
    letter-spacing: 0.15em;
    text-transform: uppercase;
    color: var(--text-dim);
    margin-bottom: 20px;
  }

  .endpoints-list {
    margin-bottom: 28px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .endpoint {
    display: flex;
    align-items: center;
    gap: 10px;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 11px;
    color: var(--text-dim);
  }
  .endpoint-method {
    color: var(--green);
    font-weight: 600;
    min-width: 28px;
  }
  .endpoint-path { color: var(--text-muted); }

  .generate-btn {
    width: 100%;
    padding: 14px 24px;
    background: var(--accent);
    color: #0a0a0a;
    border: none;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 0.05em;
    cursor: pointer;
    transition: all 0.15s ease;
    position: relative;
    overflow: hidden;
  }
  .generate-btn:hover:not(:disabled) {
    background: #ffffff;
    transform: translateY(-1px);
    box-shadow: 0 4px 20px rgba(229,229,229,0.15);
  }
  .generate-btn:active:not(:disabled) { transform: translateY(0); }
  .generate-btn:disabled {
    background: var(--border-bright);
    color: var(--text-dim);
    cursor: not-allowed;
  }

  /* Loading state */
  .loading-area {
    display: none;
    margin-top: 24px;
  }
  .loading-area.visible { display: block; }

  .progress-label {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 11px;
    color: var(--text-muted);
    margin-bottom: 8px;
    display: flex;
    justify-content: space-between;
  }
  .progress-bar-track {
    height: 2px;
    background: var(--border);
    width: 100%;
    overflow: hidden;
  }
  .progress-bar-fill {
    height: 100%;
    background: var(--green);
    width: 0%;
    transition: width 0.3s ease;
    box-shadow: 0 0 8px var(--green);
  }

  .log-area {
    margin-top: 16px;
    max-height: 120px;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 3px;
  }
  .log-line {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 10px;
    color: var(--text-dim);
    display: flex;
    gap: 10px;
  }
  .log-ts { color: var(--text-dim); min-width: 60px; }
  .log-msg { color: var(--text-muted); }
  .log-msg.ok { color: var(--green); }
  .log-msg.err { color: var(--red); }
  .log-msg.info { color: var(--amber); }

  /* Footer */
  .footer {
    margin-top: 32px;
    display: flex;
    justify-content: space-between;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 10px;
    color: var(--text-dim);
    letter-spacing: 0.05em;
  }
</style>
</head>
<body>
<div class="terminal-frame">

  <div class="header">
    <div class="header-eyebrow">DemocracyCraft · Financial Intelligence Platform</div>
    <div class="header-logo">
      <span class="b">BLOOMBERG LABS</span>
      <span class="sep"></span>
      <span class="sub">Market Report Generator</span>
    </div>
    <div class="header-desc">// Atlas API → Claude Sonnet 4 → Styled Report</div>
  </div>

  <div class="status-bar">
    <div class="status-item">
      <span class="status-dot"></span>
      <span>ATLAS CONNECTED</span>
    </div>
    <div class="status-item">
      <span class="status-dot"></span>
      <span>CLAUDE ONLINE</span>
    </div>
    <div class="status-item" style="margin-left:auto">
      <span id="clock" style="color:#404040"></span>
    </div>
  </div>

  <div class="action-area">
    <div class="action-label">// Data Sources · 6 endpoints</div>
    <div class="endpoints-list">
      <div class="endpoint"><span class="endpoint-method">GET</span><span class="endpoint-path">/securities?include_derived=true</span></div>
      <div class="endpoint"><span class="endpoint-method">GET</span><span class="endpoint-path">/orderbook</span></div>
      <div class="endpoint"><span class="endpoint-method">GET</span><span class="endpoint-path">/market/summary</span></div>
      <div class="endpoint"><span class="endpoint-method">GET</span><span class="endpoint-path">/derived</span></div>
      <div class="endpoint"><span class="endpoint-method">GET</span><span class="endpoint-path">/history/{ticker}?days=7&limit=50</span></div>
      <div class="endpoint"><span class="endpoint-method">GET</span><span class="endpoint-path">/shareholders/{ticker}</span></div>
    </div>

    <button class="generate-btn" id="generateBtn" onclick="startGeneration()">
      ▶ GENERATE DAILY REPORT
    </button>

    <div class="loading-area" id="loadingArea">
      <div class="progress-label">
        <span id="progressLabel">Fetching market data...</span>
        <span id="progressPct">0%</span>
      </div>
      <div class="progress-bar-track">
        <div class="progress-bar-fill" id="progressFill"></div>
      </div>
      <div class="log-area" id="logArea"></div>
    </div>
  </div>

  <div class="footer">
    <span>Bloomberg Labs · DemocracyCraft</span>
    <span id="build">v1.0 · CONFIDENTIAL</span>
  </div>

</div>

<script>
  // Clock
  function updateClock() {
    const now = new Date();
    document.getElementById('clock').textContent = now.toUTCString().replace(' GMT','') + ' UTC';
  }
  setInterval(updateClock, 1000);
  updateClock();

  function log(msg, cls='') {
    const area = document.getElementById('logArea');
    const now = new Date();
    const ts = now.toTimeString().slice(0,8);
    const line = document.createElement('div');
    line.className = 'log-line';
    line.innerHTML = `<span class="log-ts">${ts}</span><span class="log-msg ${cls}">${msg}</span>`;
    area.appendChild(line);
    area.scrollTop = area.scrollHeight;
  }

  function setProgress(pct, label) {
    document.getElementById('progressFill').style.width = pct + '%';
    document.getElementById('progressPct').textContent = pct + '%';
    if (label) document.getElementById('progressLabel').textContent = label;
  }

  async function startGeneration() {
    const btn = document.getElementById('generateBtn');
    btn.disabled = true;
    btn.textContent = '⏳ GENERATING...';
    document.getElementById('loadingArea').classList.add('visible');
    document.getElementById('logArea').innerHTML = '';

    log('Initiating report generation sequence...', 'info');
    setProgress(5, 'Connecting to Atlas API...');

    try {
      log('Fetching market data from Atlas...', 'info');
      setProgress(10, 'Fetching market data...');

      // Open a new window for the report early
      const reportWin = window.open('/report', '_blank');

      const response = await fetch('/api/generate', { method: 'POST' });

      if (!response.ok) {
        const err = await response.text();
        throw new Error('Server error: ' + err);
      }

      log('Atlas data fetched successfully', 'ok');
      setProgress(30, 'Sending to Claude Sonnet 4...');
      log('Streaming report from Claude...', 'info');

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let chunkCount = 0;

      // We'll write to the report window
      if (reportWin) {
        reportWin.document.open();
        reportWin.document.write(getReportShell(''));
        reportWin.document.close();
      }

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value, { stream: true });
        buffer += chunk;
        chunkCount++;

        // Update progress
        const pct = Math.min(30 + chunkCount * 3, 95);
        setProgress(pct, 'Streaming report...');

        if (chunkCount % 5 === 0) log(`Received ${buffer.length} chars...`);

        // Update report window with current buffer
        if (reportWin && !reportWin.closed) {
          try {
            const contentDiv = reportWin.document.getElementById('report-content');
            if (contentDiv) contentDiv.innerHTML = buffer;
          } catch(e) {}
        }
      }

      setProgress(100, 'Report complete');
      log('Report generation complete ✓', 'ok');

      // Store in sessionStorage for report page
      sessionStorage.setItem('reportHTML', buffer);

      // Final update to report window
      if (reportWin && !reportWin.closed) {
        try {
          const contentDiv = reportWin.document.getElementById('report-content');
          if (contentDiv) {
            contentDiv.innerHTML = buffer;
            const spinner = reportWin.document.getElementById('report-spinner');
            if (spinner) spinner.style.display = 'none';
          }
        } catch(e) {}
      }

      btn.disabled = false;
      btn.textContent = '▶ GENERATE NEW REPORT';

    } catch(err) {
      log('ERROR: ' + err.message, 'err');
      setProgress(0, 'Error — see log');
      btn.disabled = false;
      btn.textContent = '▶ RETRY GENERATION';
    }
  }

  function getReportShell(content) {
    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Bloomberg Labs — Daily Market Report</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
body { font-family: 'IBM Plex Sans', sans-serif; background: #fff; margin: 0; padding: 0; }
#report-spinner {
  display: flex; align-items: center; justify-content: center;
  min-height: 300px; font-family: 'IBM Plex Mono', monospace;
  font-size: 13px; color: #737373; letter-spacing: 0.1em;
}
#report-wrapper { max-width: 900px; margin: 0 auto; padding: 40px 48px; }
</style>
</head>
<body>
<div id="report-wrapper">
  <div id="report-spinner">⏳ GENERATING REPORT — PLEASE WAIT...</div>
  <div id="report-content">${content}</div>
</div>
</body>
</html>`;
  }
</script>
</body>
</html>
"""

REPORT_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Bloomberg Labs — Daily Market Report</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'IBM Plex Sans', sans-serif;
    background: #ffffff;
    color: #111827;
    padding: 0;
    min-height: 100vh;
  }

  /* Masthead — outside screenshot area if needed */
  .masthead {
    background: #111827;
    color: #fff;
    padding: 16px 48px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-bottom: 3px solid #e5e7eb;
  }
  .masthead-left {
    display: flex;
    flex-direction: column;
  }
  .masthead-name {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 22px;
    font-weight: 600;
    letter-spacing: -0.01em;
    color: #fff;
  }
  .masthead-sub {
    font-size: 11px;
    color: #9ca3af;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    margin-top: 2px;
  }
  .masthead-right {
    text-align: right;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 11px;
    color: #6b7280;
  }
  .masthead-date {
    font-size: 13px;
    color: #d1d5db;
    font-weight: 500;
  }

  /* Report body */
  #report-wrapper {
    max-width: 960px;
    margin: 0 auto;
    padding: 40px 48px 60px;
  }

  /* Loading state */
  #report-spinner {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    min-height: 400px;
    gap: 16px;
  }
  .spinner-text {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 12px;
    color: #737373;
    letter-spacing: 0.15em;
    text-transform: uppercase;
  }
  .spinner-bar {
    width: 200px;
    height: 2px;
    background: #f3f4f6;
    position: relative;
    overflow: hidden;
  }
  .spinner-bar::after {
    content: '';
    position: absolute;
    left: -40%;
    width: 40%;
    height: 100%;
    background: #16a34a;
    animation: sweep 1.2s ease-in-out infinite;
  }
  @keyframes sweep {
    0% { left: -40%; }
    100% { left: 100%; }
  }

  /* Toolbar (only on report page) */
  .toolbar {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 12px 48px;
    border-bottom: 1px solid #e5e7eb;
    background: #f9fafb;
  }
  .toolbar-btn {
    padding: 6px 14px;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.05em;
    cursor: pointer;
    border: 1px solid #d1d5db;
    background: #fff;
    color: #374151;
    text-transform: uppercase;
  }
  .toolbar-btn:hover { background: #111827; color: #fff; border-color: #111827; }
  .toolbar-btn.primary { background: #111827; color: #fff; border-color: #111827; }
  .toolbar-btn.primary:hover { background: #374151; }
  .toolbar-sep { flex: 1; }
  .toolbar-hint {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 10px;
    color: #9ca3af;
  }

  @media print {
    .masthead, .toolbar { display: none; }
    body { padding: 0; }
    #report-wrapper { padding: 20px; }
  }
</style>
</head>
<body>

<div class="masthead">
  <div class="masthead-left">
    <div class="masthead-name">BLOOMBERG LABS</div>
    <div class="masthead-sub">DemocracyCraft Financial Intelligence · Daily Market Report</div>
  </div>
  <div class="masthead-right">
    <div class="masthead-date" id="report-date"></div>
    <div>Atlas Market API · Claude Sonnet 4</div>
    <div>CONFIDENTIAL · INTERNAL USE ONLY</div>
  </div>
</div>

<div class="toolbar">
  <button class="toolbar-btn primary" onclick="window.print()">⎙ Print / Save PDF</button>
  <button class="toolbar-btn" onclick="window.location='/'">← New Report</button>
  <button class="toolbar-btn" onclick="copyHTML()">⧉ Copy HTML</button>
  <span class="toolbar-sep"></span>
  <span class="toolbar-hint">TIP: Use browser Print → Save as PDF for Discord screenshot</span>
</div>

<div id="report-wrapper">
  <div id="report-spinner">
    <div class="spinner-text">Awaiting report data...</div>
    <div class="spinner-bar"></div>
  </div>
  <div id="report-content"></div>
</div>

<script>
  // Set date
  document.getElementById('report-date').textContent = new Date().toLocaleDateString('en-US', {
    weekday: 'long', year: 'numeric', month: 'long', day: 'numeric'
  });

  // Try to load from sessionStorage
  function tryLoad() {
    const html = sessionStorage.getItem('reportHTML');
    if (html) {
      document.getElementById('report-spinner').style.display = 'none';
      document.getElementById('report-content').innerHTML = html;
      return true;
    }
    return false;
  }

  if (!tryLoad()) {
    // Poll for a few seconds in case generator is still running
    let attempts = 0;
    const interval = setInterval(() => {
      attempts++;
      if (tryLoad() || attempts > 60) clearInterval(interval);
    }, 500);
  }

  function copyHTML() {
    const html = document.getElementById('report-content').innerHTML;
    navigator.clipboard.writeText(html).then(() => alert('HTML copied to clipboard!'));
  }
</script>
</body>
</html>
"""

# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(INDEX_HTML)

@app.route("/report")
def report():
    return render_template_string(REPORT_HTML)

@app.route("/api/generate", methods=["POST"])
def generate():
    """Fetch Atlas data, stream Claude response."""
    def stream():
        # 1. Fetch Atlas data
        try:
            atlas_data = fetch_atlas_data()
        except Exception as e:
            yield f"<p style='color:#dc2626;font-family:IBM Plex Mono,monospace'>ERROR fetching Atlas data: {e}</p>"
            return

        # 2. Stream from Gemini
        try:
            genai.configure(api_key=GEMINI_API_KEY)
            model = genai.GenerativeModel(
                model_name="gemini-2.0-flash",
                system_instruction=SYSTEM_PROMPT,
            )
            user_prompt = build_user_prompt(atlas_data)
            response = model.generate_content(user_prompt, stream=True)
            for chunk in response:
                if chunk.text:
                    yield chunk.text

        except Exception as e:
            yield f"<p style='color:#dc2626;font-family:IBM Plex Mono,monospace'>ERROR from Gemini: {e}</p>"

    return Response(stream_with_context(stream()), mimetype="text/plain")

@app.route("/health")
def health():
    return {"status": "ok", "atlas_url": ATLAS_URL[:30] + "..." if len(ATLAS_URL) > 30 else ATLAS_URL}

# ─── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
