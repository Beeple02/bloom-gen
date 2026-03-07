import os
from datetime import datetime, timezone
from flask import Flask, render_template_string
import httpx

app = Flask(__name__)

ATLAS_URL = os.environ.get("ATLAS_URL", "").rstrip("/")
ATLAS_KEY = os.environ.get("ATLAS_KEY", "atl_Bloom_mkt_reports_MKZaifOWZHoAlDSWYWBaGCtUfFxx5Fvd")

# ─── Security Classification ──────────────────────────────────────────────────

BONDS       = {"RNC-B", "VSP3"}
ETFS        = {"CGF", "RNHC", "SRI"}
COMMODITIES = {"NTR"}

def classify(ticker):
    if ticker in BONDS:        return "Bond"
    if ticker in ETFS:         return "ETF"
    if ticker in COMMODITIES:  return "Commodity"
    return "Stock"

# ─── Atlas Fetcher ────────────────────────────────────────────────────────────

def atlas(path):
    r = httpx.get(f"{ATLAS_URL}{path}", headers={"X-Atlas-Key": ATLAS_KEY}, timeout=15)
    r.raise_for_status()
    return r.json()

def fetch_all():
    data = {}
    data["securities"] = atlas("/securities?include_derived=true")
    data["orderbook"]  = atlas("/orderbook")

    tickers = [s["ticker"] for s in data["securities"]] if isinstance(data["securities"], list) else []

    history = {}
    for t in tickers:
        try:    history[t] = atlas(f"/history/{t}?days=7&limit=50")
        except: history[t] = []
    data["history"] = history
    return data

# ─── Helpers ──────────────────────────────────────────────────────────────────

def fmt(v, d=2):
    if v is None: return None
    try:    return round(float(v), d)
    except: return None

def price_change(hist, current):
    if not hist or not isinstance(hist, list) or current is None:
        return None, None
    prices = []
    for e in hist:
        if isinstance(e, dict):
            p = e.get("price") or e.get("close") or e.get("market_price")
            if p is not None: prices.append(float(p))
    if not prices: return None, None
    prev = prices[0]
    if prev == 0: return None, None
    chg_pct = round(((current - prev) / prev) * 100, 2)
    return round(current - prev, 4), chg_pct

def compute_indices(securities):
    buckets = {"Stock": [], "ETF": [], "Bond": [], "Commodity": [], "All": []}
    for s in securities:
        if s.get("hidden"): continue
        p = s.get("market_price")
        if p is None: continue
        cat = classify(s["ticker"])
        buckets[cat].append(float(p))
        buckets["All"].append(float(p))
    def avg(lst): return round(sum(lst)/len(lst), 4) if lst else None
    return [
        {"ticker": "B:COMP",  "name": "NER Composite",   "value": avg(buckets["All"]),       "desc": "All active securities"},
        {"ticker": "B:STK",   "name": "NER Stocks",       "value": avg(buckets["Stock"]),     "desc": "Equity basket"},
        {"ticker": "B:ETF",   "name": "NER Funds",        "value": avg(buckets["ETF"]),       "desc": "ETF & fund basket"},
        {"ticker": "B:BOND",  "name": "NER Fixed Income", "value": avg(buckets["Bond"]),      "desc": "Bond basket"},
        {"ticker": "B:CMDTY", "name": "NER Commodities",  "value": avg(buckets["Commodity"]), "desc": "Commodity basket"},
    ]

def process_sec(s, history):
    t       = s["ticker"]
    price   = fmt(s.get("market_price"))
    derived = s.get("derived") or {}
    chg, chg_pct = price_change(history.get(t, []), price)
    cls = "up" if chg_pct and chg_pct > 0 else ("dn" if chg_pct and chg_pct < 0 else "flat")
    return {
        "ticker":   t,
        "name":     s.get("full_name", t),
        "price":    price if price is not None else "—",
        "frozen":   bool(s.get("frozen")),
        "shares":   f"{int(s['total_shares']):,}" if s.get("total_shares") else "—",
        "vwap7":    fmt(derived.get("vwap_7d")),
        "vol7":     fmt(derived.get("volatility_7d")),
        "liq":      fmt(derived.get("liquidity_score")),
        "imb":      fmt(derived.get("orderbook_imbalance"), 3),
        "chg":      chg,
        "chg_pct":  chg_pct,
        "cls":      cls,
    }

def process_ob(ticker, book, name_map):
    bids, asks = [], []
    if isinstance(book, dict):
        for side, out in [(book.get("bids",[]), bids), (book.get("asks",[]), asks)]:
            for entry in (side or [])[:4]:
                if isinstance(entry, dict):
                    p = entry.get("price") or entry.get("p")
                    q = entry.get("quantity") or entry.get("qty") or entry.get("q") or entry.get("size")
                    if p: out.append({"price": fmt(p), "qty": fmt(q, 0) or "?"})
                elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
                    out.append({"price": fmt(entry[0]), "qty": fmt(entry[1], 0)})
    spread = spread_pct = None
    if bids and asks:
        bb, ba = bids[0]["price"], asks[0]["price"]
        if bb and ba:
            spread     = fmt(ba - bb)
            spread_pct = fmt(((ba - bb) / bb) * 100) if bb else None
    return {"ticker": ticker, "name": name_map.get(ticker, ticker),
            "bids": bids, "asks": asks, "spread": spread, "spread_pct": spread_pct}

# ─── Templates ────────────────────────────────────────────────────────────────

INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Bloomberg Labs</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d0d0d;color:#e5e5e5;font-family:'IBM Plex Sans',sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:40px 20px}
.w{width:100%;max-width:520px}
.ey{font-family:'IBM Plex Mono',monospace;font-size:10px;letter-spacing:.2em;text-transform:uppercase;color:#404040;margin-bottom:12px}
.lo{font-family:'IBM Plex Mono',monospace;font-size:26px;font-weight:600;color:#fff;margin-bottom:4px}
.tg{font-family:'IBM Plex Mono',monospace;font-size:11px;color:#444;margin-bottom:36px}
.card{background:#141414;border:1px solid #1e1e1e;padding:28px}
.cl{font-family:'IBM Plex Mono',monospace;font-size:10px;letter-spacing:.15em;text-transform:uppercase;color:#444;margin-bottom:18px}
.btn{display:block;width:100%;padding:13px;background:#fff;color:#000;border:none;font-family:'IBM Plex Mono',monospace;font-size:13px;font-weight:600;letter-spacing:.05em;cursor:pointer;transition:background .15s}
.btn:hover{background:#ccc}
.btn:disabled{background:#1e1e1e;color:#444;cursor:not-allowed}
.st{display:none;margin-top:20px}
.st.on{display:block}
.bar{height:1px;background:#1e1e1e;margin-bottom:12px;overflow:hidden}
.barf{height:100%;background:#16a34a;width:0;transition:width .3s ease;box-shadow:0 0 6px #16a34a}
.lg{font-family:'IBM Plex Mono',monospace;font-size:10px;color:#555;display:flex;gap:8px;padding:2px 0}
.lg .ts{color:#333;min-width:52px}
.lg .ok{color:#16a34a}
.lg .er{color:#dc2626}
.lg .hi{color:#d97706}
.ft{margin-top:24px;display:flex;justify-content:space-between;font-family:'IBM Plex Mono',monospace;font-size:10px;color:#2a2a2a}
</style>
</head>
<body>
<div class="w">
  <div class="ey">DemocracyCraft · NER Exchange</div>
  <div class="lo">BLOOMBERG LABS</div>
  <div class="tg">// Daily Market Report Generator</div>
  <div class="card">
    <div class="cl">// Generate Report</div>
    <button class="btn" id="btn" onclick="go()">▶ GENERATE REPORT</button>
    <div class="st" id="st">
      <div class="bar"><div class="barf" id="bf"></div></div>
      <div id="log"></div>
    </div>
  </div>
  <div class="ft"><span>Bloomberg Labs · DemocracyCraft</span><span id="clk"></span></div>
</div>
<script>
function tick(){document.getElementById('clk').textContent=new Date().toUTCString().slice(0,25)+' UTC'}
setInterval(tick,1000);tick();
function log(m,c=''){
  const d=document.createElement('div');d.className='lg';
  d.innerHTML=`<span class="ts">${new Date().toTimeString().slice(0,8)}</span><span class="${c}">${m}</span>`;
  document.getElementById('log').appendChild(d);
}
function bar(p){document.getElementById('bf').style.width=p+'%'}
async function go(){
  const btn=document.getElementById('btn');
  btn.disabled=true;btn.textContent='⏳ FETCHING...';
  document.getElementById('st').classList.add('on');
  document.getElementById('log').innerHTML='';
  bar(5);log('Connecting to Atlas...','hi');
  try{
    const r=await fetch('/api/report');
    bar(70);
    if(!r.ok)throw new Error(await r.text());
    log('Data fetched','ok');bar(90);
    log('Rendering...','hi');
    const html=await r.text();
    bar(100);log('Done','ok');
    const w=window.open('','_blank');
    w.document.open();w.document.write(html);w.document.close();
    btn.disabled=false;btn.textContent='▶ GENERATE NEW REPORT';
  }catch(e){
    log('ERROR: '+e.message,'er');bar(0);
    btn.disabled=false;btn.textContent='▶ RETRY';
  }
}
</script>
</body>
</html>"""

REPORT = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Bloomberg Labs — {{ date_str }}</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{background:#111;color:#e5e5e5;font-family:'IBM Plex Sans',sans-serif}

/* ── TOOLBAR ── */
.toolbar{background:#0a0a0a;border-bottom:1px solid #1e1e1e;padding:10px 32px;display:flex;gap:8px;align-items:center;position:fixed;top:0;left:0;right:0;z-index:100}
.tbtn{padding:5px 14px;font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;border:1px solid #2a2a2a;background:transparent;color:#555;cursor:pointer;transition:all .15s}
.tbtn:hover,.tbtn.p{background:#fff;color:#000;border-color:#fff}
.tsp{flex:1}
.th{font-family:'IBM Plex Mono',monospace;font-size:10px;color:#2a2a2a}

/* ── PAGE SYSTEM ── */
/* Each .page is exactly 1280×720px landscape — screenshot-ready */
.pages{margin-top:42px;display:flex;flex-direction:column;gap:0;align-items:center;padding:24px 0 48px}
.page{
  width:1280px;
  min-height:720px;
  background:#111;
  position:relative;
  overflow:hidden;
  border:1px solid #1e1e1e;
  margin-bottom:16px;
  display:flex;
  flex-direction:column;
}
.page-inner{flex:1;padding:32px 40px 28px;display:flex;flex-direction:column}

/* Page nav pills */
.page-num{position:absolute;bottom:14px;right:18px;font-family:'IBM Plex Mono',monospace;font-size:9px;color:#2a2a2a;letter-spacing:.1em}

/* ── SHARED HEADER STRIP ── */
.page-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:22px;padding-bottom:14px;border-bottom:1px solid #1e1e1e}
.ph-left{display:flex;align-items:baseline;gap:16px}
.ph-logo{font-family:'IBM Plex Mono',monospace;font-size:13px;font-weight:600;color:#fff;letter-spacing:.02em}
.ph-sep{width:1px;height:12px;background:#2a2a2a}
.ph-title{font-family:'IBM Plex Mono',monospace;font-size:10px;letter-spacing:.15em;text-transform:uppercase;color:#444}
.ph-right{display:flex;align-items:center;gap:16px}
.ph-date{font-family:'IBM Plex Mono',monospace;font-size:11px;color:#888}
.ph-time{font-family:'IBM Plex Mono',monospace;font-size:10px;color:#3a3a3a}

/* ── PAGE 1: HERO ── */
.p1-layout{display:grid;grid-template-columns:1fr 1fr;gap:32px;flex:1}
.p1-left{display:flex;flex-direction:column;justify-content:space-between}
.hero-tag{font-family:'IBM Plex Mono',monospace;font-size:9px;letter-spacing:.2em;text-transform:uppercase;color:#3a3a3a;margin-bottom:10px}
.hero-title{font-size:72px;font-weight:700;letter-spacing:-.03em;line-height:.92;color:#fff;margin-bottom:16px}
.hero-sub{font-family:'IBM Plex Mono',monospace;font-size:10px;color:#3a3a3a}
.hero-stats{display:grid;grid-template-columns:1fr 1fr 1fr;gap:1px;background:#1e1e1e;border:1px solid #1e1e1e;margin-top:auto}
.hs{background:#141414;padding:14px 16px}
.hs-l{font-family:'IBM Plex Mono',monospace;font-size:9px;letter-spacing:.12em;text-transform:uppercase;color:#3a3a3a;margin-bottom:5px}
.hs-v{font-family:'IBM Plex Mono',monospace;font-size:20px;font-weight:600;color:#fff}
.hs-s{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#2a2a2a;margin-top:3px}
.hs-v.red{color:#dc2626}
.hs-v.grn{color:#16a34a}

.p1-right{display:flex;flex-direction:column;gap:0}
.idx-header{font-family:'IBM Plex Mono',monospace;font-size:9px;letter-spacing:.18em;text-transform:uppercase;color:#3a3a3a;margin-bottom:12px;padding-bottom:8px;border-bottom:1px solid #1e1e1e}
.idx-row{display:flex;justify-content:space-between;align-items:center;padding:13px 0;border-bottom:1px solid #181818}
.idx-row:last-child{border-bottom:none}
.idx-tk{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#3a3a3a;letter-spacing:.1em;margin-bottom:3px}
.idx-nm{font-size:14px;font-weight:600;color:#ccc}
.idx-r{text-align:right}
.idx-val{font-family:'IBM Plex Mono',monospace;font-size:24px;font-weight:600;color:#fff}
.idx-d{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#333;margin-top:2px}

/* ── PAGE 2: SECURITIES ── */
.p2-layout{display:grid;grid-template-columns:repeat(4,1fr);gap:20px;flex:1}
.sec-col{}
.col-header{font-family:'IBM Plex Mono',monospace;font-size:9px;letter-spacing:.18em;text-transform:uppercase;color:#3a3a3a;padding-bottom:8px;border-bottom:1px solid #1e1e1e;margin-bottom:0}
.sc{padding:11px 0;border-bottom:1px solid #161616}
.sc:last-child{border-bottom:none}
.sc-top{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:1px}
.sc-tk{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#3a3a3a;letter-spacing:.06em}
.sc-px{font-family:'IBM Plex Mono',monospace;font-size:18px;font-weight:600;color:#fff;line-height:1}
.sc-nm{font-size:11px;font-weight:600;color:#888;margin-bottom:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sc-ch{font-family:'IBM Plex Mono',monospace;font-size:11px;font-weight:500;margin-bottom:4px}
.up{color:#16a34a}.dn{color:#dc2626}.fl{color:#333}
.sc-mt{display:flex;flex-wrap:wrap;gap:8px}
.sc-mi{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#2a2a2a}
.sc-mi span{color:#555}
.frz{display:inline-block;font-family:'IBM Plex Mono',monospace;font-size:8px;letter-spacing:.06em;text-transform:uppercase;border:1px solid #dc2626;color:#dc2626;padding:0 3px;margin-left:4px;vertical-align:middle}

/* ── PAGE 3: ORDERBOOK ── */
.p3-layout{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;flex:1}
.ob-card{background:#141414;border:1px solid #1a1a1a;padding:13px}
.ob-tk{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#3a3a3a;letter-spacing:.08em;margin-bottom:2px}
.ob-nm{font-size:11px;font-weight:600;color:#777;margin-bottom:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ob-cols{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.ob-sl{font-family:'IBM Plex Mono',monospace;font-size:8px;letter-spacing:.12em;text-transform:uppercase;margin-bottom:4px}
.ob-sl.bid{color:#16a34a}.ob-sl.ask{color:#dc2626}
.ob-lv{font-family:'IBM Plex Mono',monospace;font-size:10px;display:flex;justify-content:space-between;padding:1px 0}
.ob-p{color:#bbb}.ob-q{color:#2a2a2a}
.ob-em{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#222;font-style:italic}
.ob-sp{margin-top:8px;padding-top:7px;border-top:1px solid #181818;font-family:'IBM Plex Mono',monospace;font-size:9px;color:#2a2a2a;display:flex;justify-content:space-between}
.spv{color:#555}.spv.w{color:#d97706}.spv.d{color:#dc2626}

.page-footer{margin-top:auto;padding-top:10px;border-top:1px solid #181818;display:flex;justify-content:space-between;font-family:'IBM Plex Mono',monospace;font-size:9px;color:#222}

@media print{
  .toolbar{display:none}
  .pages{margin-top:0;padding:0}
  .page{margin-bottom:0;page-break-after:always;border:none}
}
</style>
</head>
<body>

<div class="toolbar">
  <button class="tbtn p" onclick="window.print()">⎙ Print / PDF</button>
  <button class="tbtn" onclick="window.close()">← Back</button>
  <span class="tsp"></span>
  <span class="th">Screenshot each page · 1280×720 · Bloomberg Labs</span>
</div>

<div class="pages">

<!-- ═══════════════════════════════════════════════════════════
     PAGE 1 — COVER + INDICES
══════════════════════════════════════════════════════════════ -->
<div class="page">
  <div class="page-inner">
    <div class="page-header">
      <div class="ph-left">
        <span class="ph-logo">BLOOMBERG LABS</span>
        <span class="ph-sep"></span>
        <span class="ph-title">DemocracyCraft · NER Exchange · Daily Market Recap</span>
      </div>
      <div class="ph-right">
        <span class="ph-date">{{ date_str }}</span>
        <span class="ph-time">{{ time_str }} UTC</span>
      </div>
    </div>

    <div class="p1-layout">
      <div class="p1-left">
        <div>
          <div class="hero-tag">NER Exchange · Bloomberg Labs</div>
          <div class="hero-title">Market<br>Recap</div>
          <div class="hero-sub">// {{ date_str }} · {{ time_str }} UTC<br>// {{ active_count }} Active · {{ frozen_count }} Frozen · {{ total_count }} Listed</div>
        </div>
        <div class="hero-stats">
          <div class="hs">
            <div class="hs-l">NER Composite</div>
            <div class="hs-v">{{ comp_val }}</div>
            <div class="hs-s">Equal-weighted avg</div>
          </div>
          <div class="hs">
            <div class="hs-l">NER Stocks</div>
            <div class="hs-v">{{ stk_val }}</div>
            <div class="hs-s">Equity basket</div>
          </div>
          <div class="hs">
            <div class="hs-l">NER Fixed Income</div>
            <div class="hs-v">{{ bond_val }}</div>
            <div class="hs-s">Bond basket</div>
          </div>
          <div class="hs">
            <div class="hs-l">Avg Liquidity</div>
            <div class="hs-v">{{ avg_liq }}</div>
            <div class="hs-s">Liquidity score</div>
          </div>
          <div class="hs">
            <div class="hs-l">Avg Volatility 7d</div>
            <div class="hs-v">{{ avg_vol }}</div>
            <div class="hs-s">σ across market</div>
          </div>
          <div class="hs">
            <div class="hs-l">Frozen</div>
            <div class="hs-v {% if frozen_count > 0 %}red{% else %}grn{% endif %}">{{ frozen_count }}</div>
            <div class="hs-s">Trading halted</div>
          </div>
        </div>
      </div>

      <div class="p1-right">
        <div class="idx-header">// Bloomberg Indices</div>
        {% for i in indices %}
        <div class="idx-row">
          <div>
            <div class="idx-tk">{{ i.ticker }}</div>
            <div class="idx-nm">{{ i.name }}</div>
          </div>
          <div class="idx-r">
            <div class="idx-val">{{ i.value if i.value is not none else "—" }}</div>
            <div class="idx-d">{{ i.desc }}</div>
          </div>
        </div>
        {% endfor %}
      </div>
    </div>

    <div class="page-footer">
      <span>BLOOMBERG LABS · DEMOCRACYCRAFT · ATLAS MARKET INFRASTRUCTURE</span>
      <span>PAGE 1 OF 3 · CONFIDENTIAL · INTERNAL USE ONLY</span>
    </div>
  </div>
  <div class="page-num">1 / 3</div>
</div>

<!-- ═══════════════════════════════════════════════════════════
     PAGE 2 — SECURITIES
══════════════════════════════════════════════════════════════ -->
<div class="page">
  <div class="page-inner">
    <div class="page-header">
      <div class="ph-left">
        <span class="ph-logo">BLOOMBERG LABS</span>
        <span class="ph-sep"></span>
        <span class="ph-title">Securities Overview</span>
      </div>
      <div class="ph-right">
        <span class="ph-date">{{ date_str }}</span>
        <span class="ph-time">{{ time_str }} UTC</span>
      </div>
    </div>

    <div class="p2-layout">
      <!-- STOCKS col 1 -->
      <div class="sec-col">
        <div class="col-header">// Stocks (1/2)</div>
        {% for s in stocks[:4] %}
        <div class="sc">
          <div class="sc-top">
            <span class="sc-tk">{{ s.ticker }}{% if s.frozen %}<span class="frz">FRZ</span>{% endif %}</span>
            <span class="sc-px">{{ s.price }}</span>
          </div>
          <div class="sc-nm">{{ s.name }}</div>
          <div class="sc-ch {{ s.cls }}">{% if s.chg_pct is not none %}{{ '+' if s.chg_pct > 0 else '' }}{{ s.chg_pct }}%{% if s.chg is not none %} ({{ '+' if s.chg > 0 else '' }}{{ s.chg }}){% endif %}{% else %}—{% endif %}</div>
          <div class="sc-mt">
            <span class="sc-mi">VWAP7 <span>{{ s.vwap7 if s.vwap7 is not none else "—" }}</span></span>
            <span class="sc-mi">VOL <span>{{ s.vol7 if s.vol7 is not none else "—" }}</span></span>
            <span class="sc-mi">LIQ <span>{{ s.liq if s.liq is not none else "—" }}</span></span>
          </div>
        </div>
        {% endfor %}
      </div>

      <!-- STOCKS col 2 -->
      <div class="sec-col">
        <div class="col-header">// Stocks (2/2)</div>
        {% for s in stocks[4:] %}
        <div class="sc">
          <div class="sc-top">
            <span class="sc-tk">{{ s.ticker }}{% if s.frozen %}<span class="frz">FRZ</span>{% endif %}</span>
            <span class="sc-px">{{ s.price }}</span>
          </div>
          <div class="sc-nm">{{ s.name }}</div>
          <div class="sc-ch {{ s.cls }}">{% if s.chg_pct is not none %}{{ '+' if s.chg_pct > 0 else '' }}{{ s.chg_pct }}%{% if s.chg is not none %} ({{ '+' if s.chg > 0 else '' }}{{ s.chg }}){% endif %}{% else %}—{% endif %}</div>
          <div class="sc-mt">
            <span class="sc-mi">VWAP7 <span>{{ s.vwap7 if s.vwap7 is not none else "—" }}</span></span>
            <span class="sc-mi">VOL <span>{{ s.vol7 if s.vol7 is not none else "—" }}</span></span>
            <span class="sc-mi">LIQ <span>{{ s.liq if s.liq is not none else "—" }}</span></span>
          </div>
        </div>
        {% endfor %}
      </div>

      <!-- FIXED INCOME + COMMODITIES -->
      <div class="sec-col">
        <div class="col-header">// Fixed Income</div>
        {% for s in bonds %}
        <div class="sc">
          <div class="sc-top">
            <span class="sc-tk">{{ s.ticker }}{% if s.frozen %}<span class="frz">FRZ</span>{% endif %}</span>
            <span class="sc-px">{{ s.price }}</span>
          </div>
          <div class="sc-nm">{{ s.name }}</div>
          <div class="sc-ch {{ s.cls }}">{% if s.chg_pct is not none %}{{ '+' if s.chg_pct > 0 else '' }}{{ s.chg_pct }}%{% else %}—{% endif %}</div>
          <div class="sc-mt">
            <span class="sc-mi">LIQ <span>{{ s.liq if s.liq is not none else "—" }}</span></span>
            <span class="sc-mi">SHRS <span>{{ s.shares }}</span></span>
          </div>
        </div>
        {% endfor %}

        {% if commodities %}
        <div class="col-header" style="margin-top:16px">// Commodities</div>
        {% for s in commodities %}
        <div class="sc">
          <div class="sc-top">
            <span class="sc-tk">{{ s.ticker }}</span>
            <span class="sc-px">{{ s.price }}</span>
          </div>
          <div class="sc-nm">{{ s.name }}</div>
          <div class="sc-ch {{ s.cls }}">{% if s.chg_pct is not none %}{{ '+' if s.chg_pct > 0 else '' }}{{ s.chg_pct }}%{% else %}—{% endif %}</div>
          <div class="sc-mt">
            <span class="sc-mi">LIQ <span>{{ s.liq if s.liq is not none else "—" }}</span></span>
            <span class="sc-mi">SHRS <span>{{ s.shares }}</span></span>
          </div>
        </div>
        {% endfor %}
        {% endif %}
      </div>

      <!-- ETFs -->
      <div class="sec-col">
        <div class="col-header">// ETFs & Funds</div>
        {% for s in etfs %}
        <div class="sc">
          <div class="sc-top">
            <span class="sc-tk">{{ s.ticker }}{% if s.frozen %}<span class="frz">FRZ</span>{% endif %}</span>
            <span class="sc-px">{{ s.price }}</span>
          </div>
          <div class="sc-nm">{{ s.name }}</div>
          <div class="sc-ch {{ s.cls }}">{% if s.chg_pct is not none %}{{ '+' if s.chg_pct > 0 else '' }}{{ s.chg_pct }}%{% else %}—{% endif %}</div>
          <div class="sc-mt">
            <span class="sc-mi">LIQ <span>{{ s.liq if s.liq is not none else "—" }}</span></span>
            <span class="sc-mi">SHRS <span>{{ s.shares }}</span></span>
          </div>
        </div>
        {% endfor %}
      </div>
    </div>

    <div class="page-footer">
      <span>BLOOMBERG LABS · DEMOCRACYCRAFT · ATLAS MARKET INFRASTRUCTURE</span>
      <span>PAGE 2 OF 3 · CONFIDENTIAL · INTERNAL USE ONLY</span>
    </div>
  </div>
  <div class="page-num">2 / 3</div>
</div>

<!-- ═══════════════════════════════════════════════════════════
     PAGE 3 — ORDERBOOK
══════════════════════════════════════════════════════════════ -->
<div class="page">
  <div class="page-inner">
    <div class="page-header">
      <div class="ph-left">
        <span class="ph-logo">BLOOMBERG LABS</span>
        <span class="ph-sep"></span>
        <span class="ph-title">Orderbook Snapshot</span>
      </div>
      <div class="ph-right">
        <span class="ph-date">{{ date_str }}</span>
        <span class="ph-time">{{ time_str }} UTC</span>
      </div>
    </div>

    <div class="p3-layout">
      {% for ob in orderbooks %}
      <div class="ob-card">
        <div class="ob-tk">{{ ob.ticker }}</div>
        <div class="ob-nm">{{ ob.name }}</div>
        <div class="ob-cols">
          <div>
            <div class="ob-sl bid">Bids</div>
            {% if ob.bids %}{% for lv in ob.bids %}<div class="ob-lv"><span class="ob-p">{{ lv.price }}</span><span class="ob-q">×{{ lv.qty }}</span></div>{% endfor %}
            {% else %}<div class="ob-em">no bids</div>{% endif %}
          </div>
          <div>
            <div class="ob-sl ask">Asks</div>
            {% if ob.asks %}{% for lv in ob.asks %}<div class="ob-lv"><span class="ob-p">{{ lv.price }}</span><span class="ob-q">×{{ lv.qty }}</span></div>{% endfor %}
            {% else %}<div class="ob-em">no asks</div>{% endif %}
          </div>
        </div>
        {% if ob.spread is not none %}
        <div class="ob-sp">
          <span>Spread</span>
          <span class="spv {% if ob.spread_pct and ob.spread_pct > 25 %}d{% elif ob.spread_pct and ob.spread_pct > 10 %}w{% endif %}">{{ ob.spread }} ({{ ob.spread_pct }}%)</span>
        </div>
        {% endif %}
      </div>
      {% endfor %}
    </div>

    <div class="page-footer">
      <span>BLOOMBERG LABS · DEMOCRACYCRAFT · ATLAS MARKET INFRASTRUCTURE</span>
      <span>PAGE 3 OF 3 · CONFIDENTIAL · INTERNAL USE ONLY</span>
    </div>
  </div>
  <div class="page-num">3 / 3</div>
</div>

</div><!-- /pages -->
</body>
</html>"""


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(INDEX_HTML)

@app.route("/api/report")
def api_report():
    try:
        data = fetch_all()
    except Exception as e:
        return f"Atlas error: {e}", 500

    securities = data.get("securities", [])
    if not isinstance(securities, list):
        securities = []

    history = data.get("history", {})
    ob_raw  = data.get("orderbook", {})

    processed = [process_sec(s, history) for s in securities]
    name_map  = {s["ticker"]: s.get("full_name", s["ticker"]) for s in securities}

    def by_cat(cat):
        return [p for s, p in zip(securities, processed) if classify(s["ticker"]) == cat]

    stocks      = by_cat("Stock")
    etfs        = by_cat("ETF")
    bonds       = by_cat("Bond")
    commodities = by_cat("Commodity")
    indices     = compute_indices(securities)

    orderbooks = []
    if isinstance(ob_raw, dict):
        for ticker, book in ob_raw.items():
            orderbooks.append(process_ob(ticker, book, name_map))
    orderbooks.sort(key=lambda x: (not x["bids"] and not x["asks"], x["ticker"]))

    visible   = [s for s in securities if not s.get("hidden")]
    total     = len(visible)
    frozen    = len([s for s in visible if s.get("frozen")])
    active    = total - frozen

    liqs = [p["liq"] for p in processed if p["liq"] is not None]
    vols = [p["vol7"] for p in processed if p["vol7"] is not None]
    avg_liq = fmt(sum(liqs)/len(liqs)) if liqs else "—"
    avg_vol = fmt(sum(vols)/len(vols)) if vols else "—"

    def idx_val(ticker):
        i = next((x for x in indices if x["ticker"] == ticker), None)
        return i["value"] if i and i["value"] is not None else "—"

    now = datetime.now(timezone.utc)

    html = render_template_string(
        REPORT,
        date_str     = now.strftime("%b. %d, %Y"),
        time_str     = now.strftime("%H:%M:%S"),
        stocks       = stocks,
        etfs         = etfs,
        bonds        = bonds,
        commodities  = commodities,
        indices      = indices,
        orderbooks   = orderbooks,
        total_count  = total,
        frozen_count = frozen,
        active_count = active,
        avg_liq      = avg_liq,
        avg_vol      = avg_vol,
        comp_val     = idx_val("B:COMP"),
        stk_val      = idx_val("B:STK"),
        bond_val     = idx_val("B:BOND"),
    )
    return html, 200, {"Content-Type": "text/html"}

@app.route("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
