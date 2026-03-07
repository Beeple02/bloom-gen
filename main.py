import os
from datetime import datetime, timezone
from flask import Flask, render_template_string, request
import httpx

app = Flask(__name__)

ATLAS_URL = os.environ.get("ATLAS_URL", "").rstrip("/")
ATLAS_KEY = os.environ.get("ATLAS_KEY", "atl_Bloom_mkt_reports_MKZaifOWZHoAlDSWYWBaGCtUfFxx5Fvd")

BONDS       = {"RNC-B", "VSP3"}
ETFS        = {"CGF", "RNHC", "SRI"}
COMMODITIES = {"NTR"}

def classify(ticker):
    if ticker in BONDS:       return "Bond"
    if ticker in ETFS:        return "ETF"
    if ticker in COMMODITIES: return "Commodity"
    return "Stock"

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
        except: history[t] = {}
    data["history"] = history
    return data

def fmt(v, d=2):
    if v is None: return None
    try:    return round(float(v), d)
    except: return None

def price_change(hist, current):
    """hist = {ticker, count, data:[{price,timestamp,...}]} sorted desc (newest first)"""
    if not hist or current is None: return None, None
    if isinstance(hist, dict):
        hist = hist.get("data", [])
    if not isinstance(hist, list) or not hist: return None, None
    prices = [float(e["price"]) for e in hist if isinstance(e, dict) and e.get("price") is not None]
    if len(prices) < 1: return None, None
    prev = prices[-1]  # oldest entry
    if prev == 0: return None, None
    chg = round(current - prev, 4)
    chg_pct = round((chg / prev) * 100, 2)
    return chg, chg_pct

def compute_indices(securities):
    buckets = {"Stock":[], "ETF":[], "Bond":[], "Commodity":[], "All":[]}
    for s in securities:
        if s.get("hidden"): continue
        p = s.get("market_price")
        if p is None: continue
        cat = classify(s["ticker"])
        buckets[cat].append(float(p))
        buckets["All"].append(float(p))
    def avg(lst): return round(sum(lst)/len(lst), 4) if lst else None
    return [
        {"ticker":"B:COMP",  "name":"NER Composite",   "value":avg(buckets["All"]),       "desc":"All active securities"},
        {"ticker":"B:STK",   "name":"NER Stocks",       "value":avg(buckets["Stock"]),     "desc":"Equity basket"},
        {"ticker":"B:ETF",   "name":"NER Funds",        "value":avg(buckets["ETF"]),       "desc":"ETF & fund basket"},
        {"ticker":"B:BOND",  "name":"NER Fixed Income", "value":avg(buckets["Bond"]),      "desc":"Bond basket"},
        {"ticker":"B:CMDTY", "name":"NER Commodities",  "value":avg(buckets["Commodity"]), "desc":"Commodity basket"},
    ]

def process_sec(s, history):
    t       = s["ticker"]
    price   = fmt(s.get("market_price"))
    derived = s.get("derived") or {}
    chg, chg_pct = price_change(history.get(t), price)
    cls = "up" if chg_pct and chg_pct > 0 else ("dn" if chg_pct and chg_pct < 0 else "fl")
    return {
        "ticker":  t,
        "name":    s.get("full_name", t),
        "price":   price if price is not None else "—",
        "frozen":  bool(s.get("frozen")),
        "shares":  f"{int(s['total_shares']):,}" if s.get("total_shares") else "—",
        "vwap7":   fmt(derived.get("vwap_7d")),
        "vol7":    fmt(derived.get("volatility_7d")),
        "liq":     fmt(derived.get("liquidity_score")),
        "chg":     chg,
        "chg_pct": chg_pct,
        "cls":     cls,
    }

def process_ob(book, name_map):
    """book = {ticker, bids:[{price,quantity}], asks:[...], spread, spread_pct, ...}"""
    ticker = book.get("ticker", "?")
    bids, asks = [], []
    for e in (book.get("bids") or [])[:4]:
        p = e.get("price"); q = e.get("quantity") or e.get("qty")
        if p is not None: bids.append({"price": fmt(p), "qty": int(q) if q else "?"})
    for e in (book.get("asks") or [])[:4]:
        p = e.get("price"); q = e.get("quantity") or e.get("qty")
        if p is not None: asks.append({"price": fmt(p), "qty": int(q) if q else "?"})
    spread     = fmt(book.get("spread"))
    spread_pct = fmt(book.get("spread_pct"))
    if spread is None and bids and asks:
        bb = bids[0]["price"]; ba = asks[0]["price"]
        if bb and ba:
            spread = fmt(ba - bb)
            spread_pct = fmt(((ba-bb)/bb)*100) if bb else None
    return {
        "ticker": ticker, "name": name_map.get(ticker, ticker),
        "bids": bids, "asks": asks,
        "spread": spread, "spread_pct": spread_pct,
        "best_bid": fmt(book.get("best_bid")),
        "best_ask": fmt(book.get("best_ask")),
        "bid_depth": book.get("bid_depth", 0),
        "ask_depth": book.get("ask_depth", 0),
    }

# ── HTML helpers ──────────────────────────────────────────────────────────────

FONTS = '<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@300;400;500;600;700&display=swap" rel="stylesheet">'

BASE_CSS = """
/* ── Bloomberg Terminal Design System ── */
:root {
  --blm-bg:       #0a0a08;
  --blm-bg2:      #0d0d0b;
  --blm-bg3:      #111110;
  --blm-bg4:      #161614;
  --blm-border:   #1e1e1a;
  --blm-border2:  #252520;
  --blm-orange:   #ff6600;
  --blm-amber:    #ffaa00;
  --blm-green:    #00cc44;
  --blm-red:      #ff2233;
  --blm-cyan:     #00aacc;
  --blm-white:    #e8e8e0;
  --blm-grey1:    #888880;
  --blm-grey2:    #444440;
  --blm-grey3:    #222220;
  --blm-grey4:    #181816;
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{background:#050504;font-family:'IBM Plex Mono',monospace;color:var(--blm-white)}

/* ── Toolbar ── */
.toolbar{
  background:#050504;
  border-bottom:1px solid var(--blm-orange);
  padding:0 20px;
  display:flex;
  gap:0;
  align-items:stretch;
  position:fixed;top:0;left:0;right:0;z-index:100;
  height:32px;
}
.tb-brand{
  display:flex;align-items:center;gap:8px;
  padding-right:16px;border-right:1px solid var(--blm-border2);margin-right:8px;
}
.tb-logo{font-size:11px;font-weight:600;color:var(--blm-orange);letter-spacing:.08em}
.tb-sep{color:var(--blm-grey3);margin:0 4px}
.tbtn{
  padding:0 12px;font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;
  letter-spacing:.06em;text-transform:uppercase;border:none;border-right:1px solid var(--blm-border2);
  background:transparent;color:var(--blm-grey2);cursor:pointer;transition:all .1s;height:100%;
}
.tbtn:hover,.tbtn.act{background:var(--blm-orange);color:#000}
.tsp{flex:1}
.tb-time{
  font-size:10px;color:var(--blm-grey2);align-self:center;
  padding-left:12px;border-left:1px solid var(--blm-border2);
}

/* ── Page wrapper ── */
.pages{
  margin-top:32px;display:flex;flex-direction:column;align-items:center;
  padding:8px 0 32px;gap:6px;background:#030302;
}
.page{
  width:1280px;height:720px;
  background:var(--blm-bg);
  border:1px solid var(--blm-border);
  position:relative;display:flex;flex-direction:column;overflow:hidden;
}

/* ── Page header ── */
.ph{
  background:var(--blm-bg2);
  border-bottom:2px solid var(--blm-orange);
  padding:0 16px;
  display:flex;align-items:stretch;
  flex-shrink:0;height:28px;
}
.ph-logo{
  font-size:11px;font-weight:600;color:var(--blm-orange);
  display:flex;align-items:center;padding-right:12px;
  border-right:1px solid var(--blm-border2);margin-right:12px;letter-spacing:.06em;
}
.ph-crumbs{display:flex;align-items:center;gap:0;flex:1}
.ph-crumb{
  font-size:9px;letter-spacing:.1em;text-transform:uppercase;
  color:var(--blm-grey2);padding:0 10px;height:100%;display:flex;align-items:center;
  border-right:1px solid var(--blm-border2);
}
.ph-crumb.act{color:var(--blm-amber);background:rgba(255,170,0,.05)}
.ph-r{display:flex;align-items:center;gap:12px;margin-left:auto}
.ph-date{font-size:10px;color:var(--blm-grey1)}
.ph-time{font-size:10px;color:var(--blm-grey3)}

/* ── Page footer ── */
.pf{
  background:var(--blm-bg2);border-top:1px solid var(--blm-border);
  padding:0 16px;display:flex;justify-content:space-between;align-items:center;
  flex-shrink:0;height:20px;
}
.pf-l,.pf-r{font-size:8px;letter-spacing:.1em;color:var(--blm-grey3);text-transform:uppercase}
.pf-r{color:var(--blm-amber)}

/* ── Body area ── */
.pi{flex:1;overflow:hidden;display:flex;flex-direction:column}

/* ── Section headers ── */
.sh{
  font-size:8px;letter-spacing:.2em;text-transform:uppercase;
  color:var(--blm-orange);padding:4px 0 3px;
  border-bottom:1px solid var(--blm-orange);margin-bottom:0;
  flex-shrink:0;
}
.sh2{
  font-size:8px;letter-spacing:.2em;text-transform:uppercase;
  color:var(--blm-grey2);padding:3px 0 2px;
  border-bottom:1px solid var(--blm-border2);margin-bottom:0;
  flex-shrink:0;
}

/* ── Security card ── */
.sc{
  padding:5px 0 4px;
  border-bottom:1px solid var(--blm-border);
}
.sc:last-child{border-bottom:none}
.sc-row1{display:flex;justify-content:space-between;align-items:baseline;gap:4px}
.sc-tk{font-size:9px;color:var(--blm-orange);letter-spacing:.04em;flex-shrink:0}
.sc-px{font-size:17px;font-weight:600;color:var(--blm-white);line-height:1;flex-shrink:0}
.sc-row2{display:flex;justify-content:space-between;align-items:baseline;gap:4px;margin-top:1px}
.sc-nm{font-size:9px;color:var(--blm-grey2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1}
.sc-ch{font-size:10px;font-weight:600;flex-shrink:0}
.up{color:var(--blm-green)}.dn{color:var(--blm-red)}.fl{color:var(--blm-grey3)}
.sc-row3{display:flex;gap:10px;margin-top:2px;flex-wrap:wrap}
.sc-mi{font-size:8px;color:var(--blm-grey3)}
.sc-mi span{color:var(--blm-grey2)}
.frz{
  display:inline-block;font-size:7px;letter-spacing:.05em;text-transform:uppercase;
  border:1px solid var(--blm-red);color:var(--blm-red);padding:0 2px;margin-left:3px;vertical-align:middle;
}

/* ── Orderbook card ── */
.ob-card{
  background:var(--blm-bg4);border:1px solid var(--blm-border2);
  padding:8px 10px;
}
.ob-hdr{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px}
.ob-tk{font-size:9px;color:var(--blm-orange);letter-spacing:.04em}
.ob-mid{font-size:13px;font-weight:600;color:var(--blm-white)}
.ob-nm{font-size:9px;color:var(--blm-grey2);margin-bottom:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ob-cols{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:4px}
.ob-sl{font-size:7px;letter-spacing:.12em;text-transform:uppercase;margin-bottom:2px}
.ob-sl.bid{color:var(--blm-green)}.ob-sl.ask{color:var(--blm-red)}
.ob-lv{font-size:10px;display:flex;justify-content:space-between;padding:1px 0;border-bottom:1px solid var(--blm-border)}
.ob-lv:last-child{border-bottom:none}
.ob-p.bid{color:var(--blm-green)}.ob-p.ask{color:var(--blm-red)}
.ob-q{color:var(--blm-grey3);font-size:9px}
.ob-em{font-size:9px;color:var(--blm-grey3);padding:2px 0}
.ob-sp{
  display:flex;justify-content:space-between;
  font-size:8px;color:var(--blm-grey3);
  border-top:1px solid var(--blm-border);padding-top:3px;
}
.spv{color:var(--blm-amber)}.spv.w{color:var(--blm-orange)}.spv.d{color:var(--blm-red)}
.ob-depth{display:flex;justify-content:space-between;font-size:8px;color:var(--blm-grey3);margin-top:2px}
.ob-depth .bv{color:var(--blm-green)}.ob-depth .av{color:var(--blm-red)}

/* ── Index row ── */
.ir{
  display:flex;justify-content:space-between;align-items:center;
  padding:7px 0;border-bottom:1px solid var(--blm-border);
}
.ir:last-child{border-bottom:none}
.ir-tk{font-size:8px;color:var(--blm-orange);letter-spacing:.06em;margin-bottom:1px}
.ir-nm{font-size:12px;font-weight:500;color:var(--blm-grey1)}
.ir-v{font-size:22px;font-weight:600;color:var(--blm-white)}
.ir-bar{height:2px;background:var(--blm-border2);margin-top:3px;position:relative}
.ir-barf{height:100%;background:var(--blm-amber);position:absolute;top:0;left:0}

/* ── Stat box ── */
.sb{background:var(--blm-bg4);border:1px solid var(--blm-border2);padding:8px 10px}
.sb-l{font-size:8px;letter-spacing:.1em;text-transform:uppercase;color:var(--blm-grey3);margin-bottom:3px}
.sb-v{font-size:16px;font-weight:600;color:var(--blm-white)}
.sb-s{font-size:8px;color:var(--blm-grey3);margin-top:2px}

/* ── Mover ── */
.mv{display:flex;align-items:center;gap:8px;padding:5px 8px;border-bottom:1px solid var(--blm-border)}
.mv:last-child{border-bottom:none}
.mv-tk{font-size:9px;color:var(--blm-orange);min-width:40px;flex-shrink:0}
.mv-nm{font-size:9px;color:var(--blm-grey2);flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mv-px{font-size:14px;font-weight:600;color:var(--blm-white);min-width:46px;text-align:right;flex-shrink:0}
.mv-ch{font-size:10px;font-weight:600;min-width:60px;text-align:right;flex-shrink:0}
.mv-bar{height:2px;margin-top:1px}
.mv-bar.up{background:var(--blm-green)}.mv-bar.dn{background:var(--blm-red)}

/* ── Ticker tape ── */
.tape{
  background:#050504;border-bottom:1px solid var(--blm-border);
  height:22px;overflow:hidden;flex-shrink:0;display:flex;align-items:center;
  border-top:1px solid var(--blm-border);
}
.tape-inner{display:flex;gap:0;white-space:nowrap;animation:scroll-tape 40s linear infinite}
@keyframes scroll-tape{from{transform:translateX(0)}to{transform:translateX(-50%)}}
.tape-item{
  display:inline-flex;align-items:center;gap:6px;
  padding:0 16px;border-right:1px solid var(--blm-border);
  font-size:9px;height:22px;
}
.tape-tk{color:var(--blm-amber);font-weight:600;letter-spacing:.04em}
.tape-px{color:var(--blm-white)}
.tape-ch.up{color:var(--blm-green)}.tape-ch.dn{color:var(--blm-red)}.tape-ch.fl{color:var(--blm-grey3)}

/* ── Warn banner ── */
.warn{
  background:#1a0000;border-top:1px solid var(--blm-red);border-bottom:1px solid var(--blm-red);
  color:var(--blm-red);font-size:9px;padding:4px 12px;flex-shrink:0;letter-spacing:.06em;
  display:flex;align-items:center;gap:8px;
}
.warn::before{content:"⬛ HALT";background:var(--blm-red);color:#000;font-weight:700;font-size:8px;padding:1px 4px;letter-spacing:.08em}

@media print{
  .toolbar{display:none}
  .pages{margin-top:0;padding:0;gap:0;background:#000}
  .page{border:none;page-break-after:always}
}
"""

FONTS = '<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:ital,wght@0,400;0,500;0,600;0,700;1,400&display=swap" rel="stylesheet">'

def ph_html(crumbs, date_str, time_str):
    crumb_html = "".join(
        f'<span class="ph-crumb{" act" if i==len(crumbs)-1 else ""}">{c}</span>'
        for i,c in enumerate(crumbs)
    )
    return f"""<div class="ph">
  <span class="ph-logo">BLOOMBERG LABS</span>
  <div class="ph-crumbs">{crumb_html}</div>
  <div class="ph-r">
    <span class="ph-date">{date_str}</span>
    <span class="ph-time">{time_str} UTC</span>
  </div>
</div>"""

def pf_html(n, t, note=""):
    return f"""<div class="pf">
  <span class="pf-l">Bloomberg Labs · DemocracyCraft · NER Exchange · Atlas Market Infrastructure · Confidential</span>
  <span class="pf-r">{note + "  " if note else ""}pg {n}/{t}</span>
</div>"""

def toolbar_html(label):
    return f"""<div class="toolbar">
  <div class="tb-brand"><span class="tb-logo">BLOOMBERG LABS</span></div>
  <button class="tbtn act" onclick="window.print()">⎙ PDF / Print</button>
  <button class="tbtn" onclick="window.close()">← Close</button>
  <span class="tsp"></span>
  <span class="tb-time">{label}</span>
</div>"""

def sc_html(s, meta=True):
    frz = '<span class="frz">HLT</span>' if s["frozen"] else ""
    if s["chg_pct"] is not None:
        sign = "+" if s["chg_pct"] > 0 else ""
        chg_str = f"{sign}{s['chg_pct']}%"
        if s["chg"] is not None:
            sign2 = "+" if s["chg"] > 0 else ""
            chg_str += f"  {sign2}{s['chg']}"
    else:
        chg_str = "—"
    m = ""
    if meta:
        vwap  = s['vwap7']  if s['vwap7']  is not None else "—"
        vol   = s['vol7']   if s['vol7']   is not None else "—"
        liq   = s['liq']    if s['liq']    is not None else "—"
        m = f"""<div class="sc-row3">
      <span class="sc-mi">VWAP7&nbsp;<span>{vwap}</span></span>
      <span class="sc-mi">ΣVOL&nbsp;<span>{vol}</span></span>
      <span class="sc-mi">LIQ&nbsp;<span>{liq}</span></span>
      <span class="sc-mi">SHS&nbsp;<span>{s['shares']}</span></span>
    </div>"""
    return f"""<div class="sc">
  <div class="sc-row1"><span class="sc-tk">{s['ticker']}{frz}</span><span class="sc-px">{s['price']}</span></div>
  <div class="sc-row2"><span class="sc-nm">{s['name']}</span><span class="sc-ch {s['cls']}">{chg_str}</span></div>
  {m}
</div>"""

def ob_html(ob):
    bh = "".join(
        f'<div class="ob-lv"><span class="ob-p bid">{lv["price"]}</span><span class="ob-q">×{lv["qty"]}</span></div>'
        for lv in ob["bids"]
    ) or '<div class="ob-em">NO BIDS</div>'
    ah = "".join(
        f'<div class="ob-lv"><span class="ob-p ask">{lv["price"]}</span><span class="ob-q">×{lv["qty"]}</span></div>'
        for lv in ob["asks"]
    ) or '<div class="ob-em">NO ASKS</div>'
    sp = ""
    if ob["spread"] is not None:
        cls = "d" if ob["spread_pct"] and ob["spread_pct"] > 25 else ("w" if ob["spread_pct"] and ob["spread_pct"] > 10 else "")
        sp = f'<div class="ob-sp"><span>SPREAD</span><span class="spv {cls}">{ob["spread"]} ({ob["spread_pct"]}%)</span></div>'
    bid_dep = ob.get("bid_depth") or 0
    ask_dep = ob.get("ask_depth") or 0
    ba = ob.get("best_ask")
    bb = ob.get("best_bid")
    mid_str = f'{ob["best_ask"]}' if ba else (f'{ob["best_bid"]}' if bb else "—")
    depth = f'<div class="ob-depth"><span class="bv">B:{bid_dep:,}</span><span class="av">A:{ask_dep:,}</span></div>'
    return f"""<div class="ob-card">
  <div class="ob-hdr"><span class="ob-tk">{ob['ticker']}</span><span class="ob-mid">{mid_str}</span></div>
  <div class="ob-nm">{ob['name']}</div>
  <div class="ob-cols">
    <div><div class="ob-sl bid">▲ Bids</div>{bh}</div>
    <div><div class="ob-sl ask">▼ Asks</div>{ah}</div>
  </div>
  {sp}{depth}
</div>"""

def tape_html(all_secs):
    """Scrolling ticker tape from all securities"""
    items = ""
    for s in all_secs:
        if s["chg_pct"] is not None:
            sign = "+" if s["chg_pct"] > 0 else ""
            ch = f"{sign}{s['chg_pct']}%"
        else:
            ch = "—"
        items += f'<span class="tape-item"><span class="tape-tk">{s["ticker"]}</span><span class="tape-px">{s["price"]}</span><span class="tape-ch {s["cls"]}">{ch}</span></span>'
    return f'<div class="tape"><div class="tape-inner">{items}{items}</div></div>'

# ── PUBLIC REPORT ─────────────────────────────────────────────────────────────

def build_public(ctx):
    d = ctx
    all_secs = d["stocks"] + d["etfs"] + d["bonds"] + d["commodities"]
    movers   = sorted([s for s in all_secs if s["chg_pct"] is not None], key=lambda x: abs(x["chg_pct"]), reverse=True)[:8]

    stats_html = ""
    stat_items = [
        ("NER COMP",    d["comp_val"],          "Composite"),
        ("NER STK",     d["stk_val"],           "Equities"),
        ("NER BOND",    d["bond_val"],          "Fixed Inc."),
        ("AVG LIQ",     d["avg_liq"],           "Liquidity"),
        ("AVG VOL 7D",  d["avg_vol"],           "Volatility"),
        ("ACTIVE",      str(d["active_count"]), f"of {d['total_count']} listed"),
        ("FROZEN",      str(d["frozen_count"]), "halted", "var(--blm-red)" if d["frozen_count"] > 0 else "var(--blm-green)"),
    ]
    for item in stat_items:
        lbl, val, sub = item[0], item[1], item[2]
        col = item[3] if len(item) > 3 else ""
        col_style = f"color:{col}" if col else ""
        stats_html += f'<div class="sb"><div class="sb-l">{lbl}</div><div class="sb-v" style="{col_style}">{val}</div><div class="sb-s">{sub}</div></div>'

    movers_html = ""
    for s in movers:
        sign  = "+" if s["chg_pct"] > 0 else ""
        arrow = "▲" if s["chg_pct"] > 0 else "▼"
        w     = min(100, abs(s["chg_pct"]) * 5)
        movers_html += f"""<div class="mv">
      <span class="mv-tk">{s['ticker']}</span>
      <span class="mv-nm">{s['name']}</span>
      <span class="mv-px">{s['price']}</span>
      <span class="mv-ch {s['cls']}">{arrow} {sign}{s['chg_pct']}%</span>
    </div>
    <div class="mv-bar {s['cls']}" style="width:{w}%;height:1px"></div>"""

    if not movers_html:
        movers_html = '<div style="font-size:9px;color:var(--blm-grey3);padding:8px 0">NO HISTORY DATA AVAILABLE</div>'

    idx_html = ""
    for i in d["indices"]:
        v = i["value"] if i["value"] is not None else "—"
        idx_html += f"""<div class="ir">
      <div><div class="ir-tk">{i['ticker']}</div><div class="ir-nm">{i['name']}</div></div>
      <div class="ir-v">{v}</div>
    </div>"""

    frozen_warn = ""
    if d["frozen_count"] > 0:
        tks = "  ·  ".join(s["ticker"] for s in all_secs if s["frozen"])
        frozen_warn = f'<div class="warn">{tks}</div>'

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<title>Bloomberg Labs — {d['date_str']}</title>{FONTS}
<style>
{BASE_CSS}
.pub-wrap{{
  width:1280px;height:720px;background:var(--blm-bg);
  display:flex;flex-direction:column;overflow:hidden;
}}
.stats-bar{{
  display:grid;grid-template-columns:repeat(7,1fr);gap:2px;
  background:var(--blm-border);padding:2px;flex-shrink:0;
  margin:0 0 0 0;
}}
.pub-body{{
  flex:1;display:grid;grid-template-columns:220px 1fr 220px;
  gap:0;overflow:hidden;border-top:1px solid var(--blm-border);
}}
.pub-col{{padding:10px 14px;overflow:hidden;display:flex;flex-direction:column}}
.pub-col+.pub-col{{border-left:1px solid var(--blm-border)}}
.hero-tag{{font-size:8px;letter-spacing:.2em;text-transform:uppercase;color:var(--blm-orange);margin-bottom:6px}}
.hero-t{{font-size:64px;font-weight:700;letter-spacing:-.04em;line-height:.85;color:var(--blm-white);margin-bottom:10px}}
.hero-meta{{font-size:9px;color:var(--blm-grey3);line-height:2;margin-top:6px}}
.hero-meta b{{color:var(--blm-amber)}}
</style></head>
<body>
{toolbar_html(f"{d['date_str']} · {d['time_str']} UTC · PUBLIC REPORT")}
<div style="display:flex;flex-direction:column;align-items:center;padding:8px 0 28px;margin-top:32px;background:#030302">
<div class="pub-wrap">
  {ph_html(["NER Exchange", "DemocracyCraft", "Daily Market Recap"], d['date_str'], d['time_str'])}
  {tape_html(all_secs)}
  <div class="stats-bar">{stats_html}</div>
  <div class="pub-body">
    <div class="pub-col">
      <div class="hero-tag">NER Exchange · Bloomberg Labs</div>
      <div class="hero-t">Daily<br>Market<br>Recap</div>
      <div class="hero-meta">
        <b>{d['date_str']}</b><br>
        {d['time_str']} UTC<br>
        ──────────<br>
        {d['active_count']} Active<br>
        {d['frozen_count']} Frozen<br>
        {d['total_count']} Listed<br>
        ──────────<br>
        ATL Infrastructure
      </div>
    </div>
    <div class="pub-col" style="padding:0">
      <div class="sh" style="padding:6px 14px;margin:0">// TOP MOVERS — 7D PERFORMANCE</div>
      <div style="flex:1;overflow:hidden;padding:4px 14px 8px">{movers_html}</div>
    </div>
    <div class="pub-col" style="padding:0">
      <div class="sh" style="padding:6px 14px;margin:0">// BLOOMBERG INDICES</div>
      <div style="flex:1;overflow:hidden;padding:4px 14px 8px">{idx_html}</div>
    </div>
  </div>
  {frozen_warn}
  {pf_html(1, 1, "PUBLIC · DC #NEWS")}
</div>
</div>
</body></html>"""

# ── PRIVATE REPORT ────────────────────────────────────────────────────────────

def build_private(ctx):
    d = ctx
    ds, ts = d["date_str"], d["time_str"]
    all_secs = d["stocks"] + d["etfs"] + d["bonds"] + d["commodities"]

    # Tape shared
    tape = tape_html(all_secs)

    # ── Page 1: Cover + Stats + Indices ──
    stats_html = ""
    stat_items = [
        ("NER COMPOSITE",  d["comp_val"],          "All securities"),
        ("NER STOCKS",     d["stk_val"],           "Equity basket"),
        ("NER FIXED INC.", d["bond_val"],          "Bond basket"),
        ("AVG LIQUIDITY",  d["avg_liq"],           "Market liquidity"),
        ("AVG VOLATILITY", d["avg_vol"],           "7-day σ"),
        ("ACTIVE",         str(d["active_count"]), f"of {d['total_count']}"),
        ("FROZEN / HALTED",str(d["frozen_count"]), "trading halted",
         "var(--blm-red)" if d["frozen_count"] > 0 else "var(--blm-green)"),
    ]
    for item in stat_items:
        lbl, val, sub = item[0], item[1], item[2]
        col = item[3] if len(item) > 3 else ""
        stats_html += f'<div class="sb"><div class="sb-l">{lbl}</div><div class="sb-v" style="color:{col}">{val}</div><div class="sb-s">{sub}</div></div>'

    idx_html = ""
    for i in d["indices"]:
        v = i["value"] if i["value"] is not None else "—"
        idx_html += f"""<div class="ir">
      <div><div class="ir-tk">{i['ticker']}</div><div class="ir-nm">{i['name']}</div></div>
      <div style="text-align:right"><div class="ir-v">{v}</div><div style="font-size:8px;color:var(--blm-grey3)">{i['desc']}</div></div>
    </div>"""

    movers = sorted([s for s in all_secs if s["chg_pct"] is not None], key=lambda x: abs(x["chg_pct"]), reverse=True)[:6]
    movers_html = ""
    for s in movers:
        sign  = "+" if s["chg_pct"] > 0 else ""
        arrow = "▲" if s["chg_pct"] > 0 else "▼"
        movers_html += f"""<div class="mv" style="padding:4px 8px">
      <span class="mv-tk">{s['ticker']}</span>
      <span class="mv-nm">{s['name']}</span>
      <span class="mv-px" style="font-size:12px">{s['price']}</span>
      <span class="mv-ch {s['cls']}" style="font-size:9px">{arrow} {sign}{s['chg_pct']}%</span>
    </div>"""

    # ── Page 2: Stocks ──
    def chunk3(lst):
        n = len(lst); k = max(1,(n+2)//3)
        return [lst[:k], lst[k:2*k], lst[2*k:]]

    stocks_cols = chunk3(d["stocks"])
    p2_body = ""
    for col in stocks_cols:
        p2_body += f'<div style="overflow:hidden">{"".join(sc_html(s) for s in col)}</div>'

    # ── Page 3: ETFs + Bonds + Commodities ──
    p3_etf  = "".join(sc_html(s) for s in d["etfs"])  or '<div style="font-size:9px;color:var(--blm-grey3);padding:8px 0">NONE LISTED</div>'
    p3_bond = "".join(sc_html(s, meta=True) for s in d["bonds"]) or '<div style="font-size:9px;color:var(--blm-grey3);padding:8px 0">NONE LISTED</div>'
    p3_comm = "".join(sc_html(s, meta=True) for s in d["commodities"]) or '<div style="font-size:9px;color:var(--blm-grey3);padding:8px 0">NONE LISTED</div>'

    # ── Page 4: Orderbook 3-col grid ──
    ob_cards = "".join(ob_html(ob) for ob in d["orderbooks"])
    if not ob_cards:
        ob_cards = '<div style="font-size:9px;color:var(--blm-grey3);padding:12px">NO ORDERBOOK DATA AVAILABLE</div>'

    frozen_warn = ""
    if d["frozen_count"] > 0:
        tks = "  ·  ".join(s["ticker"] for s in all_secs if s["frozen"])
        frozen_warn = f'<div class="warn" style="flex-shrink:0">{tks}</div>'

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<title>Bloomberg Labs Full — {ds}</title>{FONTS}
<style>
{BASE_CSS}
/* P1 layout */
.p1-body{{
  flex:1;display:grid;grid-template-columns:1fr 260px;gap:0;overflow:hidden;
  border-top:1px solid var(--blm-border);
}}
.p1-left{{display:flex;flex-direction:column;border-right:1px solid var(--blm-border);overflow:hidden}}
.p1-hero{{
  display:grid;grid-template-columns:180px 1fr;gap:0;
  border-bottom:1px solid var(--blm-border);flex-shrink:0;
}}
.p1-hero-l{{padding:12px 14px;border-right:1px solid var(--blm-border)}}
.p1-hero-r{{padding:8px 14px;overflow:hidden}}
.stats-grid{{
  display:grid;grid-template-columns:repeat(7,1fr);gap:2px;
  background:var(--blm-border);padding:2px;flex-shrink:0;
}}
.p1-right{{
  display:flex;flex-direction:column;overflow:hidden;
}}
/* P2/P3 3-col */
.p-3col{{
  flex:1;display:grid;grid-template-columns:1fr 1fr 1fr;gap:0;
  overflow:hidden;border-top:1px solid var(--blm-border);
}}
.p-col{{padding:8px 12px;overflow:hidden;border-right:1px solid var(--blm-border)}}
.p-col:last-child{{border-right:none}}
/* P4 OB grid */
.ob-grid{{
  flex:1;display:grid;grid-template-columns:repeat(3,1fr);
  gap:6px;padding:8px;overflow:hidden;align-content:start;
  border-top:1px solid var(--blm-border);
}}
</style></head><body>
{toolbar_html(f"{ds} · {ts} UTC · FULL REPORT · 4 PAGES")}
<div class="pages">

<!-- PAGE 1: COVER -->
<div class="page"><div class="pi">
  {ph_html(["NER Exchange", "Daily Market Recap", "Cover"], ds, ts)}
  {tape}
  <div class="p1-body">
    <div class="p1-left">
      <div class="p1-hero">
        <div class="p1-hero-l">
          <div style="font-size:8px;letter-spacing:.2em;color:var(--blm-orange);margin-bottom:6px">NER EXCHANGE</div>
          <div style="font-size:52px;font-weight:700;letter-spacing:-.04em;line-height:.85;color:var(--blm-white)">Daily<br>Market<br>Recap</div>
          <div style="font-size:8px;color:var(--blm-grey3);margin-top:10px;line-height:2">
            <span style="color:var(--blm-amber)">{ds}</span><br>
            {ts} UTC<br>──────────<br>
            {d['active_count']} Active<br>{d['frozen_count']} Frozen<br>{d['total_count']} Listed
          </div>
        </div>
        <div class="p1-hero-r">
          <div class="sh2" style="margin-bottom:4px">// TOP MOVERS</div>
          {movers_html}
        </div>
      </div>
      <div class="stats-grid" style="flex-shrink:0">{stats_html}</div>
    </div>
    <div class="p1-right">
      <div class="sh" style="padding:6px 14px;flex-shrink:0">// BLOOMBERG INDICES</div>
      <div style="flex:1;overflow:hidden;padding:4px 14px 8px">{idx_html}</div>
    </div>
  </div>
  {frozen_warn}
  {pf_html(1, 4, "COVER · BLOOMBERG LABS PRIVATE")}
</div></div>

<!-- PAGE 2: STOCKS -->
<div class="page"><div class="pi">
  {ph_html(["NER Exchange", "Securities", "Stocks"], ds, ts)}
  {tape}
  <div class="p-3col">{p2_body}</div>
  {pf_html(2, 4, "EQUITIES")}
</div></div>

<!-- PAGE 3: ETFs + BONDS + COMMODITIES -->
<div class="page"><div class="pi">
  {ph_html(["NER Exchange", "Securities", "Funds · Fixed Income · Commodities"], ds, ts)}
  {tape}
  <div class="p-3col">
    <div class="p-col">
      <div class="sh" style="margin-bottom:4px">// ETFs &amp; FUNDS</div>
      {p3_etf}
    </div>
    <div class="p-col">
      <div class="sh" style="margin-bottom:4px">// FIXED INCOME</div>
      {p3_bond}
    </div>
    <div class="p-col">
      <div class="sh" style="margin-bottom:4px">// COMMODITIES</div>
      {p3_comm}
    </div>
  </div>
  {pf_html(3, 4, "FUNDS · BONDS · COMMODITIES")}
</div></div>

<!-- PAGE 4: ORDERBOOK -->
<div class="page"><div class="pi">
  {ph_html(["NER Exchange", "Orderbook Snapshot"], ds, ts)}
  {tape}
  <div class="ob-grid">{ob_cards}</div>
  {pf_html(4, 4, "ORDERBOOK SNAPSHOT · LIVE")}
</div></div>

</div></body></html>"""

# ── Index page ────────────────────────────────────────────────────────────────

INDEX_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>Bloomberg Labs</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--o:#ff6600;--a:#ffaa00;--g:#00cc44;--r:#ff2233;--b:#0a0a08;--b2:#111110;--b3:#1e1e1a;--w:#e8e8e0;--gr:#444440;--gr2:#222220}
body{background:var(--b);color:var(--w);font-family:'IBM Plex Mono',monospace;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:40px 20px}
.topbar{position:fixed;top:0;left:0;right:0;height:28px;background:#050504;border-bottom:1px solid var(--o);display:flex;align-items:center;padding:0 16px;gap:8px}
.tb-logo{font-size:11px;font-weight:700;color:var(--o);letter-spacing:.06em}
.tb-pipe{color:var(--gr2);margin:0 4px}
.tb-sub{font-size:9px;color:var(--gr);letter-spacing:.12em;text-transform:uppercase}
.tb-r{margin-left:auto;font-size:9px;color:var(--gr2)}
.w{width:100%;max-width:440px;margin-top:28px}
.hdr{margin-bottom:24px}
.hdr-tag{font-size:8px;letter-spacing:.22em;text-transform:uppercase;color:var(--o);margin-bottom:6px}
.hdr-logo{font-size:28px;font-weight:700;color:var(--w);letter-spacing:-.02em}
.hdr-sub{font-size:10px;color:var(--gr);margin-top:4px}
.card{background:var(--b2);border:1px solid var(--b3);padding:20px}
.cl{font-size:8px;letter-spacing:.18em;text-transform:uppercase;color:var(--gr2);margin-bottom:12px;padding-bottom:8px;border-bottom:1px solid var(--b3)}
.btns{display:flex;flex-direction:column;gap:6px}
.btn{
  display:block;width:100%;padding:11px 14px;border:none;
  font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:700;
  letter-spacing:.06em;cursor:pointer;transition:all .15s;text-align:left;
  display:flex;justify-content:space-between;align-items:center;
}
.btn.pub{background:var(--o);color:#000}.btn.pub:hover{background:#e55a00}
.btn.prv{background:transparent;color:var(--w);border:1px solid var(--gr2)}.btn.prv:hover{background:var(--b3);border-color:var(--a)}
.btn:disabled{background:var(--b3)!important;color:var(--gr2)!important;border-color:var(--b3)!important;cursor:not-allowed}
.badge{font-size:8px;letter-spacing:.06em;padding:1px 5px;border:1px solid currentColor;opacity:.7}
.st{display:none;margin-top:14px;border-top:1px solid var(--b3);padding-top:12px}.st.on{display:block}
.prog{height:2px;background:var(--b3);margin-bottom:8px}
.progf{height:100%;background:var(--o);width:0;transition:width .3s ease}
.lg{font-size:9px;display:flex;gap:8px;padding:2px 0}
.lg .ts{color:var(--gr2);min-width:50px}
.ok{color:var(--g)}.er{color:var(--r)}.hi{color:var(--a)}
.ft{margin-top:16px;display:flex;justify-content:space-between;font-size:9px;color:var(--gr2)}
.dot{display:inline-block;width:5px;height:5px;background:var(--g);border-radius:50%;margin-right:5px;animation:blink 2s infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.3}}
</style></head><body>
<div class="topbar">
  <span class="tb-logo">BLOOMBERG LABS</span>
  <span class="tb-pipe">|</span>
  <span class="tb-sub">Report Generator</span>
  <span class="tb-r" id="clk"></span>
</div>
<div class="w">
  <div class="hdr">
    <div class="hdr-tag">DemocracyCraft · NER Exchange</div>
    <div class="hdr-logo">BLOOMBERG LABS</div>
    <div class="hdr-sub">// Market Report Generator · <span class="dot"></span>Atlas API Live</div>
  </div>
  <div class="card">
    <div class="cl">// SELECT REPORT TYPE</div>
    <div class="btns">
      <button class="btn pub" id="btnPub" onclick="go('public')">
        ▶ PUBLIC MARKET RECAP <span class="badge">DC #NEWS</span>
      </button>
      <button class="btn prv" id="btnPrv" onclick="go('private')">
        ▶ FULL REPORT [4 PAGES] <span class="badge">BLOOMBERG DISCORD</span>
      </button>
    </div>
    <div class="st" id="st">
      <div class="prog"><div class="progf" id="pf"></div></div>
      <div id="log"></div>
    </div>
  </div>
  <div class="ft">
    <span>Bloomberg Labs · DemocracyCraft</span>
    <span id="clk2"></span>
  </div>
</div>
<script>
function tick(){const t=new Date().toUTCString().slice(0,25)+' UTC';document.getElementById('clk').textContent=t;document.getElementById('clk2').textContent=t}
setInterval(tick,1000);tick();
function log(m,c=''){const d=document.createElement('div');d.className='lg';d.innerHTML='<span class="ts">'+new Date().toTimeString().slice(0,8)+'</span><span class="'+c+'">'+m+'</span>';document.getElementById('log').appendChild(d)}
function prog(p){document.getElementById('pf').style.width=p+'%'}
async function go(mode){
  ['btnPub','btnPrv'].forEach(i=>document.getElementById(i).disabled=true);
  document.getElementById('st').classList.add('on');document.getElementById('log').innerHTML='';
  prog(5);log('Connecting to Atlas...','hi');
  try{
    prog(15);log('Fetching securities...','hi');
    const r=await fetch('/api/report?mode='+mode);prog(80);
    if(!r.ok)throw new Error(await r.text());
    log('Data received — building report...','ok');prog(92);
    const html=await r.text();prog(100);log('Report ready ✓','ok');
    const w=window.open('','_blank');w.document.open();w.document.write(html);w.document.close();
  }catch(e){log('ERROR: '+e.message,'er');prog(0)}
  finally{['btnPub','btnPrv'].forEach(i=>document.getElementById(i).disabled=false)}
}
</script></body></html>"""


# ── Routes ────────────────────────────────────────────────────────────────────

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
    if not isinstance(securities, list): securities = []
    history = data.get("history", {})
    ob_raw  = data.get("orderbook", [])

    processed = [process_sec(s, history) for s in securities]
    name_map  = {s["ticker"]: s.get("full_name", s["ticker"]) for s in securities}

    def by_cat(cat):
        return [p for s,p in zip(securities, processed) if classify(s["ticker"]) == cat]

    stocks      = by_cat("Stock")
    etfs        = by_cat("ETF")
    bonds       = by_cat("Bond")
    commodities = by_cat("Commodity")
    indices     = compute_indices(securities)

    orderbooks = []
    if isinstance(ob_raw, list):
        for book in ob_raw:
            orderbooks.append(process_ob(book, name_map))
    elif isinstance(ob_raw, dict):
        for ticker, book in ob_raw.items():
            if isinstance(book, dict):
                book["ticker"] = ticker
                orderbooks.append(process_ob(book, name_map))
    orderbooks.sort(key=lambda x: (not x["bids"] and not x["asks"], x["ticker"]))

    visible = [s for s in securities if not s.get("hidden")]
    total   = len(visible)
    frozen  = len([s for s in visible if s.get("frozen")])
    active  = total - frozen

    liqs = [p["liq"] for p in processed if p["liq"] is not None]
    vols = [p["vol7"] for p in processed if p["vol7"] is not None]
    avg_liq = fmt(sum(liqs)/len(liqs)) if liqs else "—"
    avg_vol = fmt(sum(vols)/len(vols)) if vols else "—"

    def idx_val(t):
        i = next((x for x in indices if x["ticker"] == t), None)
        return i["value"] if i and i["value"] is not None else "—"

    now = datetime.now(timezone.utc)
    ctx = dict(
        date_str=now.strftime("%b. %d, %Y"), time_str=now.strftime("%H:%M:%S"),
        stocks=stocks, etfs=etfs, bonds=bonds, commodities=commodities,
        indices=indices, orderbooks=orderbooks,
        total_count=total, frozen_count=frozen, active_count=active,
        avg_liq=avg_liq, avg_vol=avg_vol,
        comp_val=idx_val("B:COMP"), stk_val=idx_val("B:STK"), bond_val=idx_val("B:BOND"),
    )
    html = build_public(ctx) if mode == "public" else build_private(ctx)
    return html, 200, {"Content-Type": "text/html"}

@app.route("/debug")
def debug():
    try:
        ob  = atlas("/orderbook")
        h   = atlas("/history/BB?days=7&limit=5")
        return {"ob_type": str(type(ob)), "ob_len": len(ob) if isinstance(ob, list) else "?",
                "ob_sample": ob[0] if isinstance(ob, list) and ob else ob,
                "hist_type": str(type(h)), "hist_sample": h}
    except Exception as e:
        return {"error": str(e)}, 500

@app.route("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
