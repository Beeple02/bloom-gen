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
.w{width:100%;max-width:480px}
.ey{font-family:'IBM Plex Mono',monospace;font-size:10px;letter-spacing:.2em;text-transform:uppercase;color:#333;margin-bottom:12px}
.lo{font-family:'IBM Plex Mono',monospace;font-size:24px;font-weight:600;color:#fff;margin-bottom:4px}
.tg{font-family:'IBM Plex Mono',monospace;font-size:11px;color:#333;margin-bottom:32px}
.card{background:#111;border:1px solid #1e1e1e;padding:28px;margin-bottom:12px}
.cl{font-family:'IBM Plex Mono',monospace;font-size:9px;letter-spacing:.15em;text-transform:uppercase;color:#333;margin-bottom:14px}
.btns{display:flex;flex-direction:column;gap:8px}
.btn{display:block;width:100%;padding:13px;border:none;font-family:'IBM Plex Mono',monospace;font-size:12px;font-weight:600;letter-spacing:.05em;cursor:pointer;transition:all .15s;text-align:center}
.btn.pub{background:#fff;color:#000}
.btn.pub:hover{background:#ccc}
.btn.prv{background:transparent;color:#fff;border:1px solid #2a2a2a}
.btn.prv:hover{background:#1a1a1a}
.btn:disabled{background:#1a1a1a!important;color:#333!important;border-color:#1a1a1a!important;cursor:not-allowed}
.badge{display:inline-block;font-family:'IBM Plex Mono',monospace;font-size:8px;letter-spacing:.1em;text-transform:uppercase;padding:1px 5px;margin-left:6px;vertical-align:middle}
.badge.pub{background:#fff;color:#000}
.badge.prv{border:1px solid #2a2a2a;color:#555}
.st{display:none;margin-top:16px}
.st.on{display:block}
.bar{height:1px;background:#1a1a1a;margin-bottom:10px;overflow:hidden}
.barf{height:100%;background:#16a34a;width:0;transition:width .3s ease;box-shadow:0 0 5px #16a34a}
.lg{font-family:'IBM Plex Mono',monospace;font-size:10px;display:flex;gap:8px;padding:2px 0}
.lg .ts{color:#2a2a2a;min-width:52px}
.ok{color:#16a34a}.er{color:#dc2626}.hi{color:#d97706}
.ft{margin-top:20px;display:flex;justify-content:space-between;font-family:'IBM Plex Mono',monospace;font-size:10px;color:#222}
</style>
</head>
<body>
<div class="w">
  <div class="ey">DemocracyCraft · NER Exchange</div>
  <div class="lo">BLOOMBERG LABS</div>
  <div class="tg">// Market Report Generator · Atlas API</div>
  <div class="card">
    <div class="cl">// Select Report Type</div>
    <div class="btns">
      <button class="btn pub" id="btnPub" onclick="go('public')">
        ▶ PUBLIC REPORT <span class="badge pub">DC #NEWS</span>
      </button>
      <button class="btn prv" id="btnPrv" onclick="go('private')">
        ▶ FULL REPORT <span class="badge prv">BLOOMBERG DISCORD</span>
      </button>
    </div>
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
function log(m,c=''){ const d=document.createElement('div');d.className='lg';d.innerHTML=`<span class="ts">${new Date().toTimeString().slice(0,8)}</span><span class="${c}">${m}</span>`;document.getElementById('log').appendChild(d); }
function bar(p){document.getElementById('bf').style.width=p+'%'}
async function go(mode){
  document.getElementById('btnPub').disabled=true;
  document.getElementById('btnPrv').disabled=true;
  document.getElementById('st').classList.add('on');
  document.getElementById('log').innerHTML='';
  bar(5);log('Connecting to Atlas...','hi');
  try{
    const r=await fetch('/api/report?mode='+mode);
    bar(70);
    if(!r.ok)throw new Error(await r.text());
    log('Data fetched','ok');bar(90);
    const html=await r.text();
    bar(100);log('Report ready · opening...','ok');
    const w=window.open('','_blank');
    w.document.open();w.document.write(html);w.document.close();
  }catch(e){log('ERROR: '+e.message,'er');bar(0)}
  finally{
    document.getElementById('btnPub').disabled=false;
    document.getElementById('btnPrv').disabled=false;
  }
}
</script>
</body>
</html>"""

# ── Shared style fragments ──────────────────────────────────────────────────

SHARED_FONTS = '<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@300;400;500;600;700&display=swap" rel="stylesheet">'

PAGE_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
html,body{background:#111;color:#e5e5e5;font-family:'IBM Plex Sans',sans-serif}
.toolbar{background:#0a0a0a;border-bottom:1px solid #1e1e1e;padding:9px 28px;display:flex;gap:8px;align-items:center;position:fixed;top:0;left:0;right:0;z-index:100;height:38px}
.tbtn{padding:4px 12px;font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;border:1px solid #2a2a2a;background:transparent;color:#555;cursor:pointer;transition:all .15s}
.tbtn:hover,.tbtn.p{background:#fff;color:#000;border-color:#fff}
.tsp{flex:1}
.th{font-family:'IBM Plex Mono',monospace;font-size:10px;color:#222}
.pages{margin-top:38px;display:flex;flex-direction:column;align-items:center;padding:16px 0 48px;gap:12px;background:#0a0a0a}
.page{width:1280px;height:720px;background:#111;border:1px solid #1e1e1e;position:relative;display:flex;flex-direction:column;overflow:hidden}
.pi{flex:1;padding:28px 36px 22px;display:flex;flex-direction:column;overflow:hidden}
.ph{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;padding-bottom:12px;border-bottom:1px solid #1e1e1e;flex-shrink:0}
.ph-l{display:flex;align-items:center;gap:12px}
.ph-logo{font-family:'IBM Plex Mono',monospace;font-size:12px;font-weight:600;color:#fff;letter-spacing:.02em}
.ph-pipe{width:1px;height:11px;background:#222}
.ph-sub{font-family:'IBM Plex Mono',monospace;font-size:9px;letter-spacing:.14em;text-transform:uppercase;color:#333}
.ph-r{display:flex;gap:14px;align-items:center}
.ph-date{font-family:'IBM Plex Mono',monospace;font-size:11px;color:#888}
.ph-time{font-family:'IBM Plex Mono',monospace;font-size:10px;color:#333}
.pf{margin-top:auto;padding-top:9px;border-top:1px solid #181818;display:flex;justify-content:space-between;font-family:'IBM Plex Mono',monospace;font-size:9px;color:#1e1e1e;flex-shrink:0}
.pn{position:absolute;bottom:10px;right:14px;font-family:'IBM Plex Mono',monospace;font-size:9px;color:#222}
.sh{font-family:'IBM Plex Mono',monospace;font-size:9px;letter-spacing:.18em;text-transform:uppercase;color:#333;padding-bottom:7px;border-bottom:1px solid #1a1a1a;margin-bottom:0}
.sc{padding:9px 0;border-bottom:1px solid #151515}
.sc:last-child{border-bottom:none}
.sc-top{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:1px}
.sc-tk{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#333;letter-spacing:.06em}
.sc-px{font-family:'IBM Plex Mono',monospace;font-size:17px;font-weight:600;color:#fff;line-height:1}
.sc-nm{font-size:10px;font-weight:600;color:#777;margin-bottom:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sc-ch{font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:500;margin-bottom:3px}
.up{color:#16a34a}.dn{color:#dc2626}.fl{color:#2a2a2a}
.sc-mt{display:flex;flex-wrap:wrap;gap:8px}
.sc-mi{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#222}
.sc-mi span{color:#444}
.frz{display:inline-block;font-family:'IBM Plex Mono',monospace;font-size:8px;letter-spacing:.06em;text-transform:uppercase;border:1px solid #dc2626;color:#dc2626;padding:0 3px;margin-left:3px;vertical-align:middle}
.ob-card{background:#141414;border:1px solid #1a1a1a;padding:12px}
.ob-tk{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#333;letter-spacing:.06em;margin-bottom:2px}
.ob-nm{font-size:10px;font-weight:600;color:#666;margin-bottom:8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ob-cols{display:grid;grid-template-columns:1fr 1fr;gap:6px}
.ob-sl{font-family:'IBM Plex Mono',monospace;font-size:8px;letter-spacing:.1em;text-transform:uppercase;margin-bottom:3px}
.ob-sl.bid{color:#16a34a}.ob-sl.ask{color:#dc2626}
.ob-lv{font-family:'IBM Plex Mono',monospace;font-size:10px;display:flex;justify-content:space-between;padding:1px 0}
.ob-p{color:#aaa}.ob-q{color:#2a2a2a}
.ob-em{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#1e1e1e;font-style:italic}
.ob-sp{margin-top:7px;padding-top:6px;border-top:1px solid #181818;font-family:'IBM Plex Mono',monospace;font-size:9px;color:#2a2a2a;display:flex;justify-content:space-between}
.spv{color:#444}.spv.w{color:#d97706}.spv.d{color:#dc2626}
@media print{.toolbar{display:none}.pages{margin-top:0;padding:0;gap:0}.page{border:none;page-break-after:always}}
"""

def page_header(title, date_str, time_str):
    return f"""<div class="ph">
  <div class="ph-l">
    <span class="ph-logo">BLOOMBERG LABS</span>
    <span class="ph-pipe"></span>
    <span class="ph-sub">{title}</span>
  </div>
  <div class="ph-r">
    <span class="ph-date">{date_str}</span>
    <span class="ph-time">{time_str} UTC</span>
  </div>
</div>"""

def page_footer(page, total):
    return f"""<div class="pf">
  <span>BLOOMBERG LABS · DEMOCRACYCRAFT · NER EXCHANGE · ATLAS MARKET INFRASTRUCTURE</span>
  <span>PAGE {page} OF {total} · CONFIDENTIAL</span>
</div>"""

def sec_card(s, show_meta=True):
    frz = '<span class="frz">FRZ</span>' if s['frozen'] else ''
    chg_html = '—'
    if s['chg_pct'] is not None:
        sign = '+' if s['chg_pct'] > 0 else ''
        chg_html = f"{sign}{s['chg_pct']}%"
        if s['chg'] is not None:
            sign2 = '+' if s['chg'] > 0 else ''
            chg_html += f" ({sign2}{s['chg']})"
    meta = ''
    if show_meta:
        meta = f"""<div class="sc-mt">
      <span class="sc-mi">VWAP7 <span>{s['vwap7'] if s['vwap7'] is not None else '—'}</span></span>
      <span class="sc-mi">VOL <span>{s['vol7'] if s['vol7'] is not None else '—'}</span></span>
      <span class="sc-mi">LIQ <span>{s['liq'] if s['liq'] is not None else '—'}</span></span>
    </div>"""
    return f"""<div class="sc">
  <div class="sc-top"><span class="sc-tk">{s['ticker']}{frz}</span><span class="sc-px">{s['price']}</span></div>
  <div class="sc-nm">{s['name']}</div>
  <div class="sc-ch {s['cls']}">{chg_html}</div>
  {meta}
</div>"""

def ob_card(ob):
    bids_html = ''.join(f'<div class="ob-lv"><span class="ob-p">{lv["price"]}</span><span class="ob-q">×{lv["qty"]}</span></div>' for lv in ob['bids']) or '<div class="ob-em">no bids</div>'
    asks_html = ''.join(f'<div class="ob-lv"><span class="ob-p">{lv["price"]}</span><span class="ob-q">×{lv["qty"]}</span></div>' for lv in ob['asks']) or '<div class="ob-em">no asks</div>'
    spread_html = ''
    if ob['spread'] is not None:
        cls = 'd' if ob['spread_pct'] and ob['spread_pct'] > 25 else ('w' if ob['spread_pct'] and ob['spread_pct'] > 10 else '')
        spread_html = f'<div class="ob-sp"><span>Spread</span><span class="spv {cls}">{ob["spread"]} ({ob["spread_pct"]}%)</span></div>'
    return f"""<div class="ob-card">
  <div class="ob-tk">{ob['ticker']}</div>
  <div class="ob-nm">{ob['name']}</div>
  <div class="ob-cols">
    <div><div class="ob-sl bid">Bids</div>{bids_html}</div>
    <div><div class="ob-sl ask">Asks</div>{asks_html}</div>
  </div>
  {spread_html}
</div>"""


def build_public(ctx):
    d = ctx
    # Top 4 movers by abs change pct
    all_secs = d["stocks"] + d["etfs"] + d["bonds"] + d["commodities"]
    movers = sorted([s for s in all_secs if s["chg_pct"] is not None], key=lambda x: abs(x["chg_pct"]), reverse=True)[:6]

    # Stats bar
    stats = [
        ("NER Composite", d["comp_val"], "Equal-weighted"),
        ("NER Stocks",    d["stk_val"],  "Equity basket"),
        ("NER Fixed Inc.",d["bond_val"], "Bond basket"),
        ("Avg Liquidity", d["avg_liq"],  "Liquidity score"),
        ("Avg Vol 7d",    d["avg_vol"],  "Market volatility"),
        ("Frozen",        str(d["frozen_count"]), "Trading halted"),
    ]
    stats_html = ""
    for label, val, sub in stats:
        color = 'color:#dc2626' if label == "Frozen" and d["frozen_count"] > 0 else ('color:#16a34a' if label == "Frozen" else "")
        stats_html += f'''<div class="sb">
      <div class="sb-l">{label}</div>
      <div class="sb-v" style="{color}">{val}</div>
      <div class="sb-s">{sub}</div>
    </div>'''

    # Movers
    movers_html = ""
    for s in movers:
        sign = "+" if s["chg_pct"] > 0 else ""
        arrow = "▲" if s["chg_pct"] > 0 else "▼"
        col = "#16a34a" if s["chg_pct"] > 0 else "#dc2626"
        movers_html += f'''<div class="mv">
      <div class="mv-tk">{s["ticker"]}</div>
      <div class="mv-nm">{s["name"]}</div>
      <div class="mv-px">{s["price"]}</div>
      <div class="mv-ch" style="color:{col}">{arrow} {sign}{s["chg_pct"]}%</div>
    </div>'''

    # Indices
    idx_html = ""
    for i in d["indices"]:
        val = i["value"] if i["value"] is not None else "—"
        idx_html += f'''<div class="ir">
      <div><div class="ir-tk">{i["ticker"]}</div><div class="ir-nm">{i["name"]}</div></div>
      <div class="ir-v">{val}</div>
    </div>'''

    frozen_warn = ""
    if d["frozen_count"] > 0:
        tickers = ", ".join(s["ticker"] for s in all_secs if s["frozen"])
        frozen_warn = f'<div class="warn">⚠ TRADING HALTED: {tickers}</div>'

    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><title>Bloomberg Labs — {d["date_str"]}</title>
{SHARED_FONTS}
<style>
{PAGE_CSS}
/* PUBLIC EXTRAS */
.pub-wrap{{width:1280px;margin:54px auto 32px;height:720px;background:#111;border:1px solid #1e1e1e;display:flex;flex-direction:column;overflow:hidden}}
.pub-inner{{flex:1;padding:28px 36px 20px;display:flex;flex-direction:column}}
.pub-body{{flex:1;display:grid;grid-template-columns:1fr 1fr 1fr;gap:28px;margin-top:16px;overflow:hidden}}

/* stats bar */
.sbar{{display:flex;gap:1px;background:#1a1a1a;border:1px solid #1a1a1a;margin-bottom:0;flex-shrink:0}}
.sb{{flex:1;background:#141414;padding:10px 14px}}
.sb-l{{font-family:'IBM Plex Mono',monospace;font-size:9px;letter-spacing:.1em;text-transform:uppercase;color:#333;margin-bottom:4px}}
.sb-v{{font-family:'IBM Plex Mono',monospace;font-size:16px;font-weight:600;color:#fff}}
.sb-s{{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#222;margin-top:2px}}

/* movers */
.mv-col{{display:flex;flex-direction:column}}
.mv{{display:flex;align-items:center;gap:0;padding:9px 0;border-bottom:1px solid #151515}}
.mv:last-child{{border-bottom:none}}
.mv-tk{{font-family:'IBM Plex Mono',monospace;font-size:10px;color:#444;width:52px;flex-shrink:0}}
.mv-nm{{font-size:10px;font-weight:600;color:#666;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.mv-px{{font-family:'IBM Plex Mono',monospace;font-size:14px;font-weight:600;color:#fff;margin-left:8px;flex-shrink:0;min-width:52px;text-align:right}}
.mv-ch{{font-family:'IBM Plex Mono',monospace;font-size:11px;font-weight:600;margin-left:10px;min-width:70px;text-align:right;flex-shrink:0}}

/* indices */
.idx-col{{display:flex;flex-direction:column}}
.ir{{display:flex;justify-content:space-between;align-items:center;padding:10px 0;border-bottom:1px solid #151515}}
.ir:last-child{{border-bottom:none}}
.ir-tk{{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#333;letter-spacing:.08em;margin-bottom:2px}}
.ir-nm{{font-size:12px;font-weight:600;color:#999}}
.ir-v{{font-family:'IBM Plex Mono',monospace;font-size:20px;font-weight:600;color:#fff}}

/* hero left */
.hero-col{{display:flex;flex-direction:column;justify-content:space-between}}
.hero-tag{{font-family:'IBM Plex Mono',monospace;font-size:9px;letter-spacing:.18em;text-transform:uppercase;color:#2a2a2a;margin-bottom:8px}}
.hero-title{{font-size:60px;font-weight:700;letter-spacing:-.03em;line-height:.9;color:#fff;margin-bottom:12px}}
.hero-sub{{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#2a2a2a;line-height:1.8}}

.warn{{background:#1a0000;border:1px solid #3a0000;color:#dc2626;font-family:'IBM Plex Mono',monospace;font-size:10px;padding:6px 10px;margin-top:8px;flex-shrink:0}}
.pub-footer{{padding-top:8px;border-top:1px solid #181818;display:flex;justify-content:space-between;font-family:'IBM Plex Mono',monospace;font-size:9px;color:#1e1e1e;flex-shrink:0;margin-top:8px}}
</style></head><body>
<div class="toolbar">
  <button class="tbtn p" onclick="window.print()">⎙ Print / PDF</button>
  <button class="tbtn" onclick="window.close()">← Back</button>
  <span class="tsp"></span>
  <span class="th">PUBLIC VERSION · DC #news · Bloomberg Labs</span>
</div>
<div style="display:flex;flex-direction:column;align-items:center;padding:16px 0 32px;margin-top:38px;background:#0a0a0a">
<div class="pub-wrap">
<div class="pub-inner">
  {page_header("DemocracyCraft · NER Exchange · Daily Market Recap", d["date_str"], d["time_str"])}
  <div class="sbar">{stats_html}</div>
  <div class="pub-body">
    <div class="hero-col">
      <div>
        <div class="hero-tag">NER Exchange · Bloomberg Labs</div>
        <div class="hero-title">Market<br>Recap</div>
        <div class="hero-sub">// {d["date_str"]}<br>// {d["time_str"]} UTC<br>// {d["active_count"]} Active · {d["frozen_count"]} Frozen · {d["total_count"]} Listed</div>
      </div>
    </div>
    <div class="mv-col">
      <div class="sh" style="margin-bottom:0">// Top Movers</div>
      {movers_html}
    </div>
    <div class="idx-col">
      <div class="sh" style="margin-bottom:0">// Bloomberg Indices</div>
      {idx_html}
    </div>
  </div>
  {frozen_warn}
  <div class="pub-footer">
    <span>BLOOMBERG LABS · DEMOCRACYCRAFT · ATLAS MARKET INFRASTRUCTURE</span>
    <span>{d["date_str"]} · {d["time_str"]} UTC · CONFIDENTIAL</span>
  </div>
</div>
</div>
</div>
</body></html>"""


def build_private(ctx):
    d = ctx
    date_str, time_str = d["date_str"], d["time_str"]

    def ph(title): return page_header(title, date_str, time_str)
    def pf(n, t): return page_footer(n, t)

    # ── PAGE 1: Cover + Indices ──────────────────────────────────────────────
    idx_html = ""
    for i in d["indices"]:
        val = i["value"] if i["value"] is not None else "—"
        idx_html += f'''<div class="ir2">
      <div><div class="ir2-tk">{i["ticker"]}</div><div class="ir2-nm">{i["name"]}</div></div>
      <div class="ir2-r"><div class="ir2-v">{val}</div><div class="ir2-d">{i["desc"]}</div></div>
    </div>'''

    stat_items = [
        ("NER Composite", d["comp_val"], "Equal-weighted avg"),
        ("NER Stocks",    d["stk_val"],  "Equity basket"),
        ("NER Fixed Inc.",d["bond_val"], "Bond basket"),
        ("Avg Liquidity", d["avg_liq"],  "Liquidity score"),
        ("Avg Vol 7d",    d["avg_vol"],  "σ across market"),
        ("Frozen",        str(d["frozen_count"]), "Trading halted"),
    ]
    stats_html = ""
    for label, val, sub in stat_items:
        color = 'color:#dc2626' if label == "Frozen" and d["frozen_count"] > 0 else ""
        stats_html += f'''<div class="hs"><div class="hs-l">{label}</div><div class="hs-v" style="{color}">{val}</div><div class="hs-s">{sub}</div></div>'''

    # ── PAGE 2: Stocks ───────────────────────────────────────────────────────
    def cols(items, n):
        k = (len(items)+n-1)//n
        return [items[i*k:(i+1)*k] for i in range(n)]

    stocks_cols = cols(d["stocks"], 3)
    stocks_html = ""
    for col in stocks_cols:
        cards = "".join(sec_card(s) for s in col)
        stocks_html += f'<div>{cards}</div>'

    # ── PAGE 3: ETFs + Bonds + Commodities ───────────────────────────────────
    etf_html  = "".join(sec_card(s) for s in d["etfs"])
    bond_html = "".join(sec_card(s, show_meta=False) for s in d["bonds"])
    cmdty_html= "".join(sec_card(s, show_meta=False) for s in d["commodities"])

    # ── PAGE 4: Orderbook ────────────────────────────────────────────────────
    ob_html = "".join(ob_card(ob) for ob in d["orderbooks"])

    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><title>Bloomberg Labs Full — {date_str}</title>
{SHARED_FONTS}
<style>
{PAGE_CSS}
/* PRIVATE EXTRAS */
/* P1 */
.p1-g{{flex:1;display:grid;grid-template-columns:1fr 1fr;gap:28px;overflow:hidden}}
.hero-tag{{font-family:'IBM Plex Mono',monospace;font-size:9px;letter-spacing:.18em;text-transform:uppercase;color:#2a2a2a;margin-bottom:10px}}
.hero-title{{font-size:68px;font-weight:700;letter-spacing:-.03em;line-height:.9;color:#fff;margin-bottom:14px}}
.hero-sub{{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#2a2a2a;line-height:1.8}}
.hs-grid{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:1px;background:#1a1a1a;border:1px solid #1a1a1a;margin-top:auto}}
.hs{{background:#141414;padding:11px 14px}}
.hs-l{{font-family:'IBM Plex Mono',monospace;font-size:9px;letter-spacing:.1em;text-transform:uppercase;color:#333;margin-bottom:4px}}
.hs-v{{font-family:'IBM Plex Mono',monospace;font-size:17px;font-weight:600;color:#fff}}
.hs-s{{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#222;margin-top:2px}}
.ir2{{display:flex;justify-content:space-between;align-items:center;padding:11px 0;border-bottom:1px solid #161616}}
.ir2:last-child{{border-bottom:none}}
.ir2-tk{{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#2a2a2a;letter-spacing:.1em;margin-bottom:3px}}
.ir2-nm{{font-size:13px;font-weight:600;color:#bbb}}
.ir2-r{{text-align:right}}
.ir2-v{{font-family:'IBM Plex Mono',monospace;font-size:22px;font-weight:600;color:#fff}}
.ir2-d{{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#2a2a2a;margin-top:2px}}
/* P2 */
.p2-g{{flex:1;display:grid;grid-template-columns:1fr 1fr 1fr;gap:20px;overflow:hidden}}
/* P3 */
.p3-g{{flex:1;display:grid;grid-template-columns:1fr 1fr 1fr;gap:20px;overflow:hidden}}
/* P4 */
.p4-g{{flex:1;display:grid;grid-template-columns:repeat(4,1fr);gap:12px;overflow:hidden}}
</style></head><body>
<div class="toolbar">
  <button class="tbtn p" onclick="window.print()">⎙ Print / PDF</button>
  <button class="tbtn" onclick="window.close()">← Back</button>
  <span class="tsp"></span>
  <span class="th">FULL REPORT · Bloomberg Discord · 4 pages</span>
</div>
<div class="pages">

<!-- PAGE 1: COVER + INDICES -->
<div class="page"><div class="pi">
  {ph("Daily Market Recap · Cover")}
  <div class="p1-g">
    <div style="display:flex;flex-direction:column;justify-content:space-between">
      <div>
        <div class="hero-tag">NER Exchange · Bloomberg Labs</div>
        <div class="hero-title">Market<br>Recap</div>
        <div class="hero-sub">// {date_str}<br>// {time_str} UTC<br>// {d["active_count"]} Active · {d["frozen_count"]} Frozen · {d["total_count"]} Listed</div>
      </div>
      <div class="hs-grid">{stats_html}</div>
    </div>
    <div style="display:flex;flex-direction:column;">
      <div class="sh" style="margin-bottom:0">// Bloomberg Indices</div>
      {idx_html}
    </div>
  </div>
  {pf(1,4)}
</div><div class="pn">1/4</div></div>

<!-- PAGE 2: STOCKS -->
<div class="page"><div class="pi">
  {ph("Securities · Stocks")}
  <div class="p2-g">{stocks_html}</div>
  {pf(2,4)}
</div><div class="pn">2/4</div></div>

<!-- PAGE 3: ETFs + FIXED INCOME + COMMODITIES -->
<div class="page"><div class="pi">
  {ph("Securities · Funds · Fixed Income · Commodities")}
  <div class="p3-g">
    <div><div class="sh">// ETFs & Funds</div>{etf_html}</div>
    <div><div class="sh">// Fixed Income</div>{bond_html}</div>
    <div><div class="sh">// Commodities</div>{cmdty_html}</div>
  </div>
  {pf(3,4)}
</div><div class="pn">3/4</div></div>

<!-- PAGE 4: ORDERBOOK -->
<div class="page"><div class="pi">
  {ph("Orderbook Snapshot")}
  <div class="p4-g">{ob_html}</div>
  {pf(4,4)}
</div><div class="pn">4/4</div></div>

</div></body></html>"""

# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(INDEX_HTML)

@app.route("/api/report")
def api_report():
    mode = request.args.get("mode", "private")
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

    now = datetime.now(timezone.utc)
    ctx = dict(
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
    html = build_public(ctx) if mode == "public" else build_private(ctx)
    return html, 200, {"Content-Type": "text/html"}

@app.route("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
