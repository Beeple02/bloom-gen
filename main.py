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
body{background:#111;color:#e5e5e5;font-family:'IBM Plex Sans',sans-serif}

.toolbar{background:#0a0a0a;border-bottom:1px solid #1e1e1e;padding:10px 40px;display:flex;gap:8px;align-items:center;position:sticky;top:0;z-index:10}
.tbtn{padding:5px 12px;font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;letter-spacing:.05em;text-transform:uppercase;border:1px solid #2a2a2a;background:transparent;color:#555;cursor:pointer;transition:all .15s}
.tbtn:hover,.tbtn.p{background:#fff;color:#000;border-color:#fff}
.tsp{flex:1}
.th{font-family:'IBM Plex Mono',monospace;font-size:10px;color:#2a2a2a}

#R{max-width:1140px;margin:0 auto;padding:48px 48px 72px}

/* Masthead */
.mast{display:flex;justify-content:space-between;align-items:flex-end;padding-bottom:24px;border-bottom:2px solid #222;margin-bottom:36px}
.m-tag{font-family:'IBM Plex Mono',monospace;font-size:10px;letter-spacing:.18em;text-transform:uppercase;color:#444;margin-bottom:10px}
.m-title{font-size:56px;font-weight:700;letter-spacing:-.03em;line-height:.95;color:#fff}
.m-sub{font-family:'IBM Plex Mono',monospace;font-size:11px;color:#444;margin-top:8px}
.m-r{text-align:right}
.m-date{font-family:'IBM Plex Mono',monospace;font-size:20px;font-weight:600;color:#fff;margin-bottom:5px}
.m-time{font-family:'IBM Plex Mono',monospace;font-size:11px;color:#444;line-height:1.7}

/* Stats bar */
.sbar{display:flex;border:1px solid #1e1e1e;margin-bottom:40px}
.sb{flex:1;padding:14px 20px;border-right:1px solid #1e1e1e}
.sb:last-child{border-right:none}
.sb-l{font-family:'IBM Plex Mono',monospace;font-size:9px;letter-spacing:.12em;text-transform:uppercase;color:#444;margin-bottom:5px}
.sb-v{font-family:'IBM Plex Mono',monospace;font-size:18px;font-weight:600;color:#fff}
.sb-s{font-family:'IBM Plex Mono',monospace;font-size:10px;color:#3a3a3a;margin-top:3px}

/* Two col */
.g2{display:grid;grid-template-columns:1fr 1fr;gap:36px;margin-bottom:40px}

/* Section */
.sh{font-family:'IBM Plex Mono',monospace;font-size:9px;letter-spacing:.2em;text-transform:uppercase;color:#3a3a3a;border-bottom:1px solid #1e1e1e;padding-bottom:7px;margin-bottom:0}
.sec{margin-bottom:32px}

/* Security card */
.sc{padding:14px 0;border-bottom:1px solid #181818}
.sc:last-child{border-bottom:none}
.sc-top{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:1px}
.sc-tk{font-family:'IBM Plex Mono',monospace;font-size:10px;color:#444;letter-spacing:.06em}
.sc-px{font-family:'IBM Plex Mono',monospace;font-size:22px;font-weight:600;color:#fff}
.sc-nm{font-size:12px;font-weight:600;color:#aaa;margin-bottom:5px}
.sc-ch{font-family:'IBM Plex Mono',monospace;font-size:12px;font-weight:500}
.up{color:#16a34a}.dn{color:#dc2626}.fl{color:#444}
.sc-mt{display:flex;flex-wrap:wrap;gap:12px;margin-top:5px}
.sc-mi{font-family:'IBM Plex Mono',monospace;font-size:10px;color:#333}
.sc-mi span{color:#666}
.frz{display:inline-block;font-family:'IBM Plex Mono',monospace;font-size:9px;letter-spacing:.08em;text-transform:uppercase;border:1px solid #dc2626;color:#dc2626;padding:0 4px;margin-left:5px;vertical-align:middle}

/* Indices */
.ir{display:flex;justify-content:space-between;align-items:center;padding:13px 0;border-bottom:1px solid #181818}
.ir:last-child{border-bottom:none}
.ir-tk{font-family:'IBM Plex Mono',monospace;font-size:10px;color:#3a3a3a;letter-spacing:.08em;margin-bottom:3px}
.ir-nm{font-size:13px;font-weight:600;color:#bbb}
.ir-v{font-family:'IBM Plex Mono',monospace;font-size:20px;font-weight:600;color:#fff;text-align:right}
.ir-d{font-family:'IBM Plex Mono',monospace;font-size:10px;color:#3a3a3a;text-align:right;margin-top:2px}

/* Orderbook */
.ob-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px}
.ob-card{background:#141414;border:1px solid #1e1e1e;padding:14px}
.ob-tk{font-family:'IBM Plex Mono',monospace;font-size:10px;color:#444;letter-spacing:.06em;margin-bottom:3px}
.ob-nm{font-size:11px;font-weight:600;color:#888;margin-bottom:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ob-ss{display:grid;grid-template-columns:1fr 1fr;gap:6px}
.ob-sl{font-family:'IBM Plex Mono',monospace;font-size:9px;letter-spacing:.1em;text-transform:uppercase;margin-bottom:3px}
.ob-sl.bid{color:#16a34a}.ob-sl.ask{color:#dc2626}
.ob-lv{font-family:'IBM Plex Mono',monospace;font-size:10px;display:flex;justify-content:space-between;padding:1px 0}
.ob-p{color:#bbb}.ob-q{color:#333}
.ob-em{font-family:'IBM Plex Mono',monospace;font-size:10px;color:#2a2a2a;font-style:italic}
.ob-sp{margin-top:8px;padding-top:8px;border-top:1px solid #1a1a1a;font-family:'IBM Plex Mono',monospace;font-size:10px;color:#333;display:flex;justify-content:space-between}
.spv{color:#666}.spv.w{color:#d97706}.spv.d{color:#dc2626}

.div{height:1px;background:#1a1a1a;margin-bottom:36px}
.rfooter{margin-top:48px;padding-top:14px;border-top:1px solid #1a1a1a;display:flex;justify-content:space-between;font-family:'IBM Plex Mono',monospace;font-size:10px;color:#2a2a2a}

@media print{.toolbar{display:none}#R{padding:32px}}
</style>
</head>
<body>

<div class="toolbar">
  <button class="tbtn p" onclick="window.print()">⎙ Save PDF</button>
  <button class="tbtn" onclick="window.close()">← Back</button>
  <span class="tsp"></span>
  <span class="th">Print → Save as PDF for Discord · Bloomberg Labs</span>
</div>

<div id="R">

  <div class="mast">
    <div>
      <div class="m-tag">Bloomberg Labs · DemocracyCraft · NER Exchange</div>
      <div class="m-title">Market<br>Recap</div>
      <div class="m-sub">// Daily Summary · Atlas Market Infrastructure</div>
    </div>
    <div class="m-r">
      <div class="m-date">{{ date_str }}</div>
      <div class="m-time">{{ time_str }} UTC</div>
      <div class="m-time">{{ active_count }} Active · {{ frozen_count }} Frozen · {{ total_count }} Listed</div>
    </div>
  </div>

  <div class="sbar">
    <div class="sb">
      <div class="sb-l">NER Composite</div>
      <div class="sb-v">{{ comp_val }}</div>
      <div class="sb-s">Equal-weighted avg</div>
    </div>
    <div class="sb">
      <div class="sb-l">NER Stocks</div>
      <div class="sb-v">{{ stk_val }}</div>
      <div class="sb-s">Equity basket</div>
    </div>
    <div class="sb">
      <div class="sb-l">NER Fixed Income</div>
      <div class="sb-v">{{ bond_val }}</div>
      <div class="sb-s">Bond basket</div>
    </div>
    <div class="sb">
      <div class="sb-l">Avg Liquidity</div>
      <div class="sb-v">{{ avg_liq }}</div>
      <div class="sb-s">Liquidity score</div>
    </div>
    <div class="sb">
      <div class="sb-l">Avg Volatility</div>
      <div class="sb-v">{{ avg_vol }}</div>
      <div class="sb-s">7-day σ</div>
    </div>
    <div class="sb">
      <div class="sb-l">Frozen</div>
      <div class="sb-v" style="color:{% if frozen_count > 0 %}#dc2626{% else %}#16a34a{% endif %}">{{ frozen_count }}</div>
      <div class="sb-s">Trading halted</div>
    </div>
  </div>

  <div class="g2">
    <!-- LEFT -->
    <div>
      {% if stocks %}
      <div class="sec">
        <div class="sh">// Stocks</div>
        {% for s in stocks %}
        <div class="sc">
          <div class="sc-top">
            <span class="sc-tk">{{ s.ticker }}{% if s.frozen %}<span class="frz">FROZEN</span>{% endif %}</span>
            <span class="sc-px">{{ s.price }}</span>
          </div>
          <div class="sc-nm">{{ s.name }}</div>
          <div class="sc-ch {{ s.cls }}">
            {% if s.chg_pct is not none %}{{ '+' if s.chg_pct > 0 else '' }}{{ s.chg_pct }}%
            {% if s.chg is not none %}&nbsp;&nbsp;{{ '+' if s.chg > 0 else '' }}{{ s.chg }}{% endif %}
            {% else %}—{% endif %}
          </div>
          <div class="sc-mt">
            <span class="sc-mi">VWAP7 <span>{{ s.vwap7 if s.vwap7 is not none else '—' }}</span></span>
            <span class="sc-mi">VOL7 <span>{{ s.vol7 if s.vol7 is not none else '—' }}</span></span>
            <span class="sc-mi">LIQ <span>{{ s.liq if s.liq is not none else '—' }}</span></span>
            <span class="sc-mi">SHRS <span>{{ s.shares }}</span></span>
          </div>
        </div>
        {% endfor %}
      </div>
      {% endif %}

      {% if etfs %}
      <div class="sec">
        <div class="sh">// ETFs & Funds</div>
        {% for s in etfs %}
        <div class="sc">
          <div class="sc-top">
            <span class="sc-tk">{{ s.ticker }}{% if s.frozen %}<span class="frz">FROZEN</span>{% endif %}</span>
            <span class="sc-px">{{ s.price }}</span>
          </div>
          <div class="sc-nm">{{ s.name }}</div>
          <div class="sc-ch {{ s.cls }}">
            {% if s.chg_pct is not none %}{{ '+' if s.chg_pct > 0 else '' }}{{ s.chg_pct }}%{% else %}—{% endif %}
          </div>
          <div class="sc-mt">
            <span class="sc-mi">LIQ <span>{{ s.liq if s.liq is not none else '—' }}</span></span>
            <span class="sc-mi">SHRS <span>{{ s.shares }}</span></span>
          </div>
        </div>
        {% endfor %}
      </div>
      {% endif %}
    </div>

    <!-- RIGHT -->
    <div>
      {% if bonds %}
      <div class="sec">
        <div class="sh">// Fixed Income</div>
        {% for s in bonds %}
        <div class="sc">
          <div class="sc-top">
            <span class="sc-tk">{{ s.ticker }}{% if s.frozen %}<span class="frz">FROZEN</span>{% endif %}</span>
            <span class="sc-px">{{ s.price }}</span>
          </div>
          <div class="sc-nm">{{ s.name }}</div>
          <div class="sc-ch {{ s.cls }}">
            {% if s.chg_pct is not none %}{{ '+' if s.chg_pct > 0 else '' }}{{ s.chg_pct }}%{% else %}—{% endif %}
          </div>
          <div class="sc-mt">
            <span class="sc-mi">LIQ <span>{{ s.liq if s.liq is not none else '—' }}</span></span>
            <span class="sc-mi">SHRS <span>{{ s.shares }}</span></span>
          </div>
        </div>
        {% endfor %}
      </div>
      {% endif %}

      {% if commodities %}
      <div class="sec">
        <div class="sh">// Commodities</div>
        {% for s in commodities %}
        <div class="sc">
          <div class="sc-top">
            <span class="sc-tk">{{ s.ticker }}{% if s.frozen %}<span class="frz">FROZEN</span>{% endif %}</span>
            <span class="sc-px">{{ s.price }}</span>
          </div>
          <div class="sc-nm">{{ s.name }}</div>
          <div class="sc-ch {{ s.cls }}">
            {% if s.chg_pct is not none %}{{ '+' if s.chg_pct > 0 else '' }}{{ s.chg_pct }}%{% else %}—{% endif %}
          </div>
          <div class="sc-mt">
            <span class="sc-mi">LIQ <span>{{ s.liq if s.liq is not none else '—' }}</span></span>
            <span class="sc-mi">SHRS <span>{{ s.shares }}</span></span>
          </div>
        </div>
        {% endfor %}
      </div>
      {% endif %}

      <div class="sec">
        <div class="sh">// Bloomberg Indices</div>
        {% for i in indices %}
        <div class="ir">
          <div>
            <div class="ir-tk">{{ i.ticker }}</div>
            <div class="ir-nm">{{ i.name }}</div>
          </div>
          <div>
            <div class="ir-v">{{ i.value if i.value is not none else '—' }}</div>
            <div class="ir-d">{{ i.desc }}</div>
          </div>
        </div>
        {% endfor %}
      </div>
    </div>
  </div>

  <div class="div"></div>

  <div class="sec">
    <div class="sh">// Orderbook Snapshot</div>
    <div style="height:12px"></div>
    <div class="ob-grid">
      {% for ob in orderbooks %}
      <div class="ob-card">
        <div class="ob-tk">{{ ob.ticker }}</div>
        <div class="ob-nm">{{ ob.name }}</div>
        <div class="ob-ss">
          <div>
            <div class="ob-sl bid">Bids</div>
            {% if ob.bids %}{% for lv in ob.bids %}
            <div class="ob-lv"><span class="ob-p">{{ lv.price }}</span><span class="ob-q">×{{ lv.qty }}</span></div>
            {% endfor %}{% else %}<div class="ob-em">no bids</div>{% endif %}
          </div>
          <div>
            <div class="ob-sl ask">Asks</div>
            {% if ob.asks %}{% for lv in ob.asks %}
            <div class="ob-lv"><span class="ob-p">{{ lv.price }}</span><span class="ob-q">×{{ lv.qty }}</span></div>
            {% endfor %}{% else %}<div class="ob-em">no asks</div>{% endif %}
          </div>
        </div>
        {% if ob.spread is not none %}
        <div class="ob-sp">
          <span>Spread</span>
          <span class="spv {% if ob.spread_pct and ob.spread_pct > 25 %}d{% elif ob.spread_pct and ob.spread_pct > 10 %}w{% endif %}">
            {{ ob.spread }} ({{ ob.spread_pct }}%)
          </span>
        </div>
        {% endif %}
      </div>
      {% endfor %}
    </div>
  </div>

  <div class="rfooter">
    <span>BLOOMBERG LABS · DEMOCRACYCRAFT · NER EXCHANGE</span>
    <span>{{ time_str }} UTC · Atlas Market Infrastructure · CONFIDENTIAL</span>
  </div>

</div>
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
