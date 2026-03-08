import os
from datetime import datetime, timezone
from flask import Flask, render_template_string, request
import httpx

app = Flask(__name__)

ATLAS_URL = os.environ.get("ATLAS_URL", "").rstrip("/")
ATLAS_KEY = os.environ.get("ATLAS_KEY", "atl_Bloom_mkt_reports_MKZaifOWZHoAlDSWYWBaGCtUfFxx5Fvd")

BONDS       = {"RNC-B", "VSP3"}
HIDDEN_TICKERS = {"RNHC", "RNC-B", "VSP3"}
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

def make_spark(prices, color, w=400, h=60):
    """Sleek smooth SVG sparkline — cubic bezier curves, subtle gradient fill, horizontal gridlines."""
    if len(prices) < 2:
        return ""
    mn, mx = min(prices), max(prices)
    rng = mx - mn if mx != mn else max(mn * 0.02, 1)
    pad = 6
    def px(i): return round(i / (len(prices)-1) * w, 1)
    def py(p): return round(h - pad - ((p - mn) / rng * (h - pad*2)), 1)
    coords = [(px(i), py(p)) for i, p in enumerate(prices)]
    # Build smooth cubic bezier path
    d = f"M {coords[0][0]},{coords[0][1]}"
    for i in range(1, len(coords)):
        x0,y0 = coords[i-1]; x1,y1 = coords[i]
        cx = round((x0+x1)/2, 1)
        d += f" C {cx},{y0} {cx},{y1} {x1},{y1}"
    # Fill path
    fd = d + f" L {coords[-1][0]},{h} L {coords[0][0]},{h} Z"
    uid = abs(hash(color + str(len(prices)))) % 100000
    # Subtle horizontal mid-line
    mid_y = round(h/2, 1)
    grid = f'<line x1="0" y1="{mid_y}" x2="{w}" y2="{mid_y}" stroke="#ffffff" stroke-opacity="0.03" stroke-width="1"/>'
    return (
        f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" style="display:block;width:100%;height:100%" xmlns="http://www.w3.org/2000/svg">'
        f'<defs><linearGradient id="g{uid}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{color}" stop-opacity="0.18"/>'
        f'<stop offset="100%" stop-color="{color}" stop-opacity="0.01"/>'
        f'</linearGradient></defs>'
        f'{grid}'
        f'<path d="{fd}" fill="url(#g{uid})"/>'
        f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/>'
        f'</svg>'
    )

def process_sec(s, history):
    t       = s["ticker"]
    price   = fmt(s.get("market_price"))
    derived = s.get("derived") or {}
    chg, chg_pct = price_change(history.get(t), price)
    cls = "up" if chg_pct and chg_pct > 0 else ("dn" if chg_pct and chg_pct < 0 else "fl")
    # Extract price series for sparkline (oldest first)
    hist = history.get(t, {})
    if isinstance(hist, dict): hist = hist.get("data", [])
    prices_raw = [float(e["price"]) for e in reversed(hist) if isinstance(e, dict) and e.get("price") is not None]
    if price is not None: prices_raw.append(price)
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
        "prices":  prices_raw,
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
*{box-sizing:border-box;margin:0;padding:0}
html,body{background:#111;color:#e5e5e5;font-family:'IBM Plex Sans',sans-serif}
.toolbar{background:#0a0a0a;border-bottom:1px solid #1e1e1e;padding:8px 28px;display:flex;gap:8px;align-items:center;position:fixed;top:0;left:0;right:0;z-index:100;height:36px}
.tbtn{padding:3px 12px;font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;border:1px solid #2a2a2a;background:transparent;color:#555;cursor:pointer;transition:all .15s}
.tbtn:hover,.tbtn.p{background:#fff;color:#000;border-color:#fff}
.tsp{flex:1}.th{font-family:'IBM Plex Mono',monospace;font-size:10px;color:#222}
.pages{margin-top:36px;display:flex;flex-direction:column;align-items:center;padding:12px 0 40px;gap:10px;background:#0a0a0a}
.page{width:1280px;height:720px;background:#111;border:1px solid #1e1e1e;position:relative;display:flex;flex-direction:column;overflow:hidden}
.pi{flex:1;padding:24px 32px 18px;display:flex;flex-direction:column;overflow:hidden}
.ph{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;padding-bottom:10px;border-bottom:1px solid #1e1e1e;flex-shrink:0}
.ph-l{display:flex;align-items:center;gap:10px}
.ph-logo{font-family:'IBM Plex Mono',monospace;font-size:12px;font-weight:600;color:#fff}
.ph-pipe{width:1px;height:10px;background:#222}
.ph-sub{font-family:'IBM Plex Mono',monospace;font-size:9px;letter-spacing:.12em;text-transform:uppercase;color:#333}
.ph-r{display:flex;gap:12px}
.ph-date{font-family:'IBM Plex Mono',monospace;font-size:11px;color:#888}
.ph-time{font-family:'IBM Plex Mono',monospace;font-size:10px;color:#333}
.pf{padding-top:8px;border-top:1px solid #171717;display:flex;justify-content:space-between;font-family:'IBM Plex Mono',monospace;font-size:9px;color:#1e1e1e;flex-shrink:0;margin-top:auto}
.pn{position:absolute;bottom:8px;right:12px;font-family:'IBM Plex Mono',monospace;font-size:9px;color:#1e1e1e}
.sh{font-family:'IBM Plex Mono',monospace;font-size:9px;letter-spacing:.16em;text-transform:uppercase;color:#555;padding-bottom:6px;border-bottom:1px solid #2a2a2a;margin-bottom:0}
/* security card */
.sc{padding:6px 0 4px;border-bottom:1px solid #1e1e1e}
.sc:last-child{border-bottom:none}
.sc-top{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:1px}
.sc-tk{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#666;letter-spacing:.05em}
.sc-px{font-family:'IBM Plex Mono',monospace;font-size:20px;font-weight:600;color:#fff;line-height:1}
.sc-nm{font-size:11px;font-weight:600;color:#aaa;margin-bottom:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sc-ch{font-family:'IBM Plex Mono',monospace;font-size:11px;font-weight:500;margin-bottom:2px}
.up{color:#16a34a}.dn{color:#dc2626}.fl{color:#444}
.sc-mt{display:flex;flex-wrap:wrap;gap:8px}
.sc-mi{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#444}
.sc-mi span{color:#777}
.frz{display:inline-block;font-family:'IBM Plex Mono',monospace;font-size:8px;letter-spacing:.05em;text-transform:uppercase;border:1px solid #dc2626;color:#dc2626;padding:0 3px;margin-left:3px;vertical-align:middle}
/* mini sparkline chart */
.spark{display:block;width:100%;height:100%;margin-top:0}
/* security tile = info card + chart panel (base, overridden per context) */
.sc-tile{border-bottom:1px solid #1e1e1e}
.sc-tile:last-child{border-bottom:none}
.sc-tile .sc{border-bottom:none;padding-bottom:3px}
.sc-chart{background:#0d0d0d;border-top:1px solid #1a1a1a;overflow:hidden}
/* orderbook */
.ob-card{background:#141414;border:1px solid #252525;padding:12px 14px}
.ob-tk{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#888;letter-spacing:.05em;margin-bottom:2px}
.ob-nm{font-size:11px;font-weight:600;color:#aaa;margin-bottom:8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ob-cols{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.ob-sl{font-family:'IBM Plex Mono',monospace;font-size:8px;letter-spacing:.1em;text-transform:uppercase;margin-bottom:3px}
.ob-sl.bid{color:#16a34a}.ob-sl.ask{color:#dc2626}
.ob-lv{font-family:'IBM Plex Mono',monospace;font-size:11px;display:flex;justify-content:space-between;padding:1px 0}
.ob-p{color:#ddd}.ob-q{color:#666}
.ob-em{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#333;font-style:italic}
.ob-sp{margin-top:7px;padding-top:6px;border-top:1px solid #222;font-family:'IBM Plex Mono',monospace;font-size:9px;display:flex;justify-content:space-between;color:#555}
.spv{color:#777}.spv.w{color:#d97706}.spv.d{color:#dc2626}
.ob-depth{margin-top:4px;font-family:'IBM Plex Mono',monospace;font-size:9px;color:#444;display:flex;justify-content:space-between}
@media print{.toolbar{display:none}.pages{margin-top:0;padding:0;gap:0}.page{border:none;page-break-after:always}}
"""

def ph(title, date_str, time_str):
    return f"""<div class="ph"><div class="ph-l"><span class="ph-logo">BLOOMBERG LABS</span><span class="ph-pipe"></span><span class="ph-sub">{title}</span></div><div class="ph-r"><span class="ph-date">{date_str}</span><span class="ph-time">{time_str} UTC</span></div></div>"""

def pf(n, t):
    return f"""<div class="pf"><span>BLOOMBERG LABS · DEMOCRACYCRAFT · NER EXCHANGE · ATLAS MARKET INFRASTRUCTURE</span><span>PAGE {n} OF {t} · CONFIDENTIAL</span></div>"""

def sc_html(s, meta=True, spark=True):
    frz = '<span class="frz">FRZ</span>' if s["frozen"] else ""
    if s["chg_pct"] is not None:
        sign = "+" if s["chg_pct"] > 0 else ""
        chg_str = f"{sign}{s['chg_pct']}%"
        if s["chg"] is not None:
            sign2 = "+" if s["chg"] > 0 else ""
            chg_str += f" ({sign2}{s['chg']})"
    else:
        chg_str = "—"
    m = ""
    if meta:
        m = f"""<div class="sc-mt">
      <span class="sc-mi">VWAP7 <span>{s['vwap7'] if s['vwap7'] is not None else '—'}</span></span>
      <span class="sc-mi">VOL <span>{s['vol7'] if s['vol7'] is not None else '—'}</span></span>
      <span class="sc-mi">LIQ <span>{s['liq'] if s['liq'] is not None else '—'}</span></span>
      <span class="sc-mi">SHRS <span>{s['shares']}</span></span>
    </div>"""
    spark_color = "#16a34a" if s["cls"] == "up" else ("#dc2626" if s["cls"] == "dn" else "#444")
    sp = ""
    if spark:
        svg = make_spark(s.get("prices", []), spark_color, w=200, h=32)
        if svg:
            sp = f'<div class="sc-chart">{svg}</div>'
    return f"""<div class="sc-tile">
  <div class="sc">
    <div class="sc-top"><span class="sc-tk">{s['ticker']}{frz}</span><span class="sc-px">{s['price']}</span></div>
    <div class="sc-nm">{s['name']}</div>
    <div class="sc-ch {s['cls']}">{chg_str}</div>
    {m}
  </div>{sp}
</div>"""

def ob_html(ob):
    bh = "".join(f'<div class="ob-lv"><span class="ob-p">{lv["price"]}</span><span class="ob-q">×{lv["qty"]}</span></div>' for lv in ob["bids"]) or '<div class="ob-em">no bids</div>'
    ah = "".join(f'<div class="ob-lv"><span class="ob-p">{lv["price"]}</span><span class="ob-q">×{lv["qty"]}</span></div>' for lv in ob["asks"]) or '<div class="ob-em">no asks</div>'
    sp = ""
    if ob["spread"] is not None:
        cls = "d" if ob["spread_pct"] and ob["spread_pct"] > 25 else ("w" if ob["spread_pct"] and ob["spread_pct"] > 10 else "")
        sp = f'<div class="ob-sp"><span>Spread</span><span class="spv {cls}">{ob["spread"]} ({ob["spread_pct"]}%)</span></div>'
    depth = f'<div class="ob-depth"><span>Bid depth: {ob["bid_depth"]:,}</span><span>Ask depth: {ob["ask_depth"]:,}</span></div>' if ob.get("bid_depth") is not None else ""
    return f"""<div class="ob-card">
  <div class="ob-tk">{ob['ticker']}</div>
  <div class="ob-nm">{ob['name']}</div>
  <div class="ob-cols">
    <div><div class="ob-sl bid">Bids</div>{bh}</div>
    <div><div class="ob-sl ask">Asks</div>{ah}</div>
  </div>
  {sp}{depth}
</div>"""

def toolbar(label):
    return f"""<div class="toolbar">
  <button class="tbtn p" onclick="window.print()">⎙ PDF</button>
  <button class="tbtn" onclick="window.close()">← Back</button>
  <span class="tsp"></span>
  <span class="th">{label}</span>
</div>"""

# ── PUBLIC REPORT ─────────────────────────────────────────────────────────────

def build_public(ctx):
    d = ctx
    all_secs = d["stocks"] + d["etfs"] + d["bonds"] + d["commodities"]
    # Top 3 movers by abs % change
    movers = sorted([s for s in all_secs if s["chg_pct"] is not None], key=lambda x: abs(x["chg_pct"]), reverse=True)[:3]

    stats_html = ""
    for label, val, sub, col in [
        ("NER Composite", d["comp_val"], "Equal-weighted avg", ""),
        ("NER Stocks",    d["stk_val"],  "Equity basket",      ""),
        ("NER Fixed Inc.",d["bond_val"], "Bond basket",        ""),
        ("Avg Liquidity", d["avg_liq"],  "Liquidity score",    ""),
        ("Avg Vol 7d",    d["avg_vol"],  "Market volatility",  ""),
        ("Frozen",        str(d["frozen_count"]), "Halted", "color:#dc2626" if d["frozen_count"] > 0 else "color:#16a34a"),
    ]:
        stats_html += f'<div class="sb"><div class="sb-l">{label}</div><div class="sb-v" style="{col}">{val}</div><div class="sb-s">{sub}</div></div>'

    movers_html = ""
    for s in movers:
        sign  = "+" if s["chg_pct"] > 0 else ""
        arrow = "▲" if s["chg_pct"] > 0 else "▼"
        col   = "#16a34a" if s["chg_pct"] > 0 else "#dc2626"
        spark_color = "#16a34a" if s["cls"] == "up" else "#dc2626"
        sp = make_spark(s.get("prices", []), spark_color, w=300, h=36)
        movers_html += f"""<div class="mv">
      <div class="mv-info">
        <div class="mv-row1">
          <span class="mv-tk">{s['ticker']}</span>
          <span class="mv-px">{s['price']}</span>
          <span class="mv-ch" style="color:{col}">{arrow} {sign}{s['chg_pct']}%</span>
        </div>
        <div class="mv-nm">{s['name']}</div>
        {sp}
      </div>
    </div>"""

    if not movers_html:
        movers_html = '<div style="font-family:IBM Plex Mono,monospace;font-size:10px;color:#444;padding:12px 0">No price history available</div>'

    idx_html = ""
    for i in d["indices"]:
        v = i["value"] if i["value"] is not None else "—"
        idx_html += f'<div class="ir"><div><div class="ir-tk">{i["ticker"]}</div><div class="ir-nm">{i["name"]}</div></div><div class="ir-v">{v}</div></div>'

    frozen_warn = ""
    if d["frozen_count"] > 0:
        tks = ", ".join(s["ticker"] for s in all_secs if s["frozen"])
        frozen_warn = f'<div class="warn">⚠ TRADING HALTED: {tks}</div>'

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<title>Bloomberg Labs — {d['date_str']}</title>{FONTS}
<style>
{BASE_CSS}
.pub-wrap{{width:1280px;margin:48px auto 24px;height:720px;background:#111;border:1px solid #1e1e1e;display:flex;flex-direction:column;overflow:hidden}}
.pub-inner{{flex:1;padding:22px 32px 16px;display:flex;flex-direction:column}}
.sbar{{display:flex;gap:1px;background:#1a1a1a;border:1px solid #1a1a1a;flex-shrink:0;margin-bottom:14px}}
.sb{{flex:1;background:#141414;padding:10px 14px}}
.sb-l{{font-family:'IBM Plex Mono',monospace;font-size:9px;letter-spacing:.1em;text-transform:uppercase;color:#444;margin-bottom:4px}}
.sb-v{{font-family:'IBM Plex Mono',monospace;font-size:18px;font-weight:600;color:#fff}}
.sb-s{{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#333;margin-top:2px}}
.pub-body{{flex:1;display:grid;grid-template-columns:260px 1fr 1fr;gap:24px;overflow:hidden}}
.hero-col{{display:flex;flex-direction:column;justify-content:flex-start;padding-top:4px}}
.hero-tag{{font-family:'IBM Plex Mono',monospace;font-size:9px;letter-spacing:.16em;text-transform:uppercase;color:#444;margin-bottom:10px}}
.hero-title{{font-size:80px;font-weight:700;letter-spacing:-.03em;line-height:.85;color:#fff;margin-bottom:14px}}
.hero-sub{{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#444;line-height:1.9}}
.mv-col{{display:flex;flex-direction:column;overflow:hidden;gap:2px}}
.mv{{padding:8px 0;border-bottom:1px solid #1e1e1e}}
.mv:last-child{{border-bottom:none}}
.mv-info{{display:flex;flex-direction:column;gap:2px}}
.mv-row1{{display:flex;align-items:baseline;gap:8px}}
.mv-tk{{font-family:'IBM Plex Mono',monospace;font-size:10px;color:#777;min-width:44px;flex-shrink:0}}
.mv-nm{{font-size:10px;font-weight:500;color:#888;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.mv-px{{font-family:'IBM Plex Mono',monospace;font-size:20px;font-weight:600;color:#fff;flex:1}}
.mv-ch{{font-family:'IBM Plex Mono',monospace;font-size:13px;font-weight:600;flex-shrink:0}}
.idx-col{{display:flex;flex-direction:column;overflow:hidden}}
.ir{{display:flex;justify-content:space-between;align-items:center;padding:9px 0;border-bottom:1px solid #1e1e1e}}
.ir:last-child{{border-bottom:none}}
.ir-tk{{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#555;letter-spacing:.08em;margin-bottom:2px}}
.ir-nm{{font-size:13px;font-weight:600;color:#999}}
.ir-v{{font-family:'IBM Plex Mono',monospace;font-size:24px;font-weight:600;color:#fff}}
.warn{{background:#1a0000;border:1px solid #3a0000;color:#dc2626;font-family:'IBM Plex Mono',monospace;font-size:10px;padding:5px 10px;flex-shrink:0;margin-top:8px}}
.pub-footer{{padding-top:7px;border-top:1px solid #1e1e1e;display:flex;justify-content:space-between;font-family:'IBM Plex Mono',monospace;font-size:9px;color:#333;flex-shrink:0}}
</style></head><body>
{toolbar("PUBLIC VERSION · DC #news · Bloomberg Labs")}
<div style="display:flex;flex-direction:column;align-items:center;padding:12px 0 32px;margin-top:36px;background:#0a0a0a">
<div class="pub-wrap"><div class="pub-inner">
  {ph("DemocracyCraft · NER Exchange · Daily Market Recap", d['date_str'], d['time_str'])}
  <div class="sbar">{stats_html}</div>
  <div class="pub-body">
    <div class="hero-col">
      <div class="hero-tag">NER Exchange · Bloomberg Labs</div>
      <div class="hero-title">Market<br>Recap</div>
      <div class="hero-sub">// {d['date_str']}<br>// {d['time_str']} UTC<br>// {d['active_count']} Active · {d['frozen_count']} Frozen<br>// {d['total_count']} Securities Listed</div>
    </div>
    <div class="mv-col">
      <div class="sh" style="margin-bottom:6px">// Top Movers</div>
      {movers_html}
    </div>
    <div class="idx-col">
      <div class="sh" style="margin-bottom:6px">// Bloomberg Indices</div>
      {idx_html}
    </div>
  </div>
  {frozen_warn}
  <div class="pub-footer">
    <span>BLOOMBERG LABS · DEMOCRACYCRAFT · ATLAS MARKET INFRASTRUCTURE</span>
    <span>{d['date_str']} · {d['time_str']} UTC · CONFIDENTIAL</span>
  </div>
</div></div>
</div></body></html>"""

# ── PRIVATE REPORT ────────────────────────────────────────────────────────────

def build_private(ctx):
    d = ctx
    ds, ts = d["date_str"], d["time_str"]

    # ── Page 1: Cover + Indices ──
    stats_html = ""
    for label, val, sub, col in [
        ("NER Composite", d["comp_val"], "Equal-weighted avg", ""),
        ("NER Stocks",    d["stk_val"],  "Equity basket",      ""),
        ("NER Fixed Inc.",d["bond_val"], "Bond basket",        ""),
        ("Avg Liquidity", d["avg_liq"],  "Liquidity score",    ""),
        ("Avg Vol 7d",    d["avg_vol"],  "σ across market",    ""),
        ("Frozen",        str(d["frozen_count"]), "Trading halted", "color:#dc2626" if d["frozen_count"] > 0 else "color:#16a34a"),
    ]:
        stats_html += f'<div class="hs"><div class="hs-l">{label}</div><div class="hs-v" style="{col}">{val}</div><div class="hs-s">{sub}</div></div>'

    idx_html = ""
    for i in d["indices"]:
        v = i["value"] if i["value"] is not None else "—"
        idx_html += f"""<div class="ir2">
      <div><div class="ir2-tk">{i['ticker']}</div><div class="ir2-nm">{i['name']}</div></div>
      <div class="ir2-r"><div class="ir2-v">{v}</div><div class="ir2-d">{i['desc']}</div></div>
    </div>"""

    # ── Page 2: All stocks (3 cols) ──
    def chunk(lst, n):
        k = max(1, (len(lst)+n-1)//n)
        return [lst[i*k:(i+1)*k] for i in range(n)]

    stocks_cols = chunk(d["stocks"], 3)
    p2_cols = ""
    for col in stocks_cols:
        p2_cols += f'<div class="p2-col">{"".join(sc_html(s) for s in col)}</div>'

    # ── Page 3: ETFs + Bonds + Commodities (3 cols) ──
    p3_col1 = "".join(sc_html(s) for s in d["etfs"])
    p3_col2 = "".join(sc_html(s, meta=False) for s in d["bonds"])
    p3_col3 = "".join(sc_html(s, meta=False) for s in d["commodities"])

    # ── Page 4: Orderbook (3 cols × 4 rows grid) ──
    ob_cards = "".join(ob_html(ob) for ob in d["orderbooks"])
    if not ob_cards:
        ob_cards = '<div style="font-family:IBM Plex Mono,monospace;font-size:11px;color:#2a2a2a;padding:20px">No orderbook data available</div>'

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<title>Bloomberg Labs Full — {ds}</title>{FONTS}
<style>
{BASE_CSS}
/* P1 */
.p1-g{{flex:1;display:grid;grid-template-columns:1fr 1fr;gap:28px;overflow:hidden}}
.hero-tag{{font-family:'IBM Plex Mono',monospace;font-size:9px;letter-spacing:.16em;text-transform:uppercase;color:#444;margin-bottom:8px}}
.hero-title{{font-size:72px;font-weight:700;letter-spacing:-.03em;line-height:.88;color:#fff;margin-bottom:12px}}
.hero-sub{{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#444;line-height:1.9}}
.hs-grid{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:1px;background:#1a1a1a;border:1px solid #1a1a1a;margin-top:auto}}
.hs{{background:#141414;padding:10px 14px}}
.hs-l{{font-family:'IBM Plex Mono',monospace;font-size:9px;letter-spacing:.1em;text-transform:uppercase;color:#444;margin-bottom:4px}}
.hs-v{{font-family:'IBM Plex Mono',monospace;font-size:18px;font-weight:600;color:#fff}}
.hs-s{{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#333;margin-top:2px}}
.ir2{{display:flex;justify-content:space-between;align-items:center;padding:11px 0;border-bottom:1px solid #161616}}
.ir2:last-child{{border-bottom:none}}
.ir2-tk{{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#555;letter-spacing:.08em;margin-bottom:2px}}
.ir2-nm{{font-size:14px;font-weight:600;color:#ccc}}
.ir2-r{{text-align:right}}
.ir2-v{{font-family:'IBM Plex Mono',monospace;font-size:26px;font-weight:600;color:#fff}}
.ir2-d{{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#444;margin-top:2px}}
/* P2/P3 */
.p2-g{{flex:1;display:grid;grid-template-columns:1fr 1fr 1fr;gap:0 16px;overflow:hidden}}
.p2-col{{display:flex;flex-direction:column;overflow:hidden}}
.sc-tile{{flex:1;display:flex;flex-direction:column;border-bottom:1px solid #1e1e1e;min-height:0}}
.sc-tile:last-child{{border-bottom:none}}
.sc-tile .sc{{flex-shrink:0;padding:5px 0 3px;border-bottom:none}}
.sc-chart{{flex:1;background:#0d0d0d;border-top:1px solid #1a1a1a;min-height:0;overflow:hidden}}
/* P4 */
.p4-g{{flex:1;display:grid;grid-template-columns:repeat(3,1fr);gap:12px;overflow:hidden;align-content:start}}
</style></head><body>
{toolbar("FULL REPORT · Bloomberg Discord · 4 pages")}
<div class="pages">

<div class="page"><div class="pi">
  {ph("Daily Market Recap · Cover", ds, ts)}
  <div class="p1-g">
    <div style="display:flex;flex-direction:column;justify-content:space-between">
      <div>
        <div class="hero-tag">NER Exchange · Bloomberg Labs</div>
        <div class="hero-title">Market<br>Recap</div>
        <div class="hero-sub">// {ds}<br>// {ts} UTC<br>// {d['active_count']} Active · {d['frozen_count']} Frozen · {d['total_count']} Listed</div>
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

<div class="page"><div class="pi">
  {ph("Securities · Stocks", ds, ts)}
  <div class="p2-g">{p2_cols}</div>
  {pf(2,4)}
</div><div class="pn">2/4</div></div>

<div class="page"><div class="pi">
  {ph("Securities · Funds · Fixed Income · Commodities", ds, ts)}
  <div class="p2-g">
    <div class="p2-col"><div class="sh" style="flex-shrink:0;margin-bottom:0">// ETFs &amp; Funds</div>{p3_col1}</div>
    <div class="p2-col"><div class="sh" style="flex-shrink:0;margin-bottom:0">// Fixed Income</div>{p3_col2}</div>
    <div class="p2-col"><div class="sh" style="flex-shrink:0;margin-bottom:0">// Commodities</div>{p3_col3}</div>
  </div>
  {pf(3,4)}
</div><div class="pn">3/4</div></div>

<div class="page"><div class="pi">
  {ph("Orderbook Snapshot", ds, ts)}
  <div class="p4-g">{ob_cards}</div>
  {pf(4,4)}
</div><div class="pn">4/4</div></div>

</div></body></html>"""

# ── Index page ────────────────────────────────────────────────────────────────

INDEX_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>Bloomberg Labs</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d0d0d;color:#e5e5e5;font-family:'IBM Plex Sans',sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:40px 20px}
.w{width:100%;max-width:480px}
.ey{font-family:'IBM Plex Mono',monospace;font-size:10px;letter-spacing:.2em;text-transform:uppercase;color:#333;margin-bottom:12px}
.lo{font-family:'IBM Plex Mono',monospace;font-size:24px;font-weight:600;color:#fff;margin-bottom:4px}
.tg{font-family:'IBM Plex Mono',monospace;font-size:11px;color:#333;margin-bottom:32px}
.card{background:#111;border:1px solid #1e1e1e;padding:28px;margin-bottom:12px}
.cl{font-family:'IBM Plex Mono',monospace;font-size:9px;letter-spacing:.15em;text-transform:uppercase;color:#2a2a2a;margin-bottom:14px}
.btns{display:flex;flex-direction:column;gap:8px}
.btn{display:block;width:100%;padding:13px;border:none;font-family:'IBM Plex Mono',monospace;font-size:12px;font-weight:600;letter-spacing:.05em;cursor:pointer;transition:all .15s;text-align:center}
.btn.pub{background:#fff;color:#000}.btn.pub:hover{background:#ccc}
.btn.prv{background:transparent;color:#fff;border:1px solid #2a2a2a}.btn.prv:hover{background:#1a1a1a}
.btn:disabled{background:#1a1a1a!important;color:#333!important;border-color:#1a1a1a!important;cursor:not-allowed}
.badge{display:inline-block;font-family:'IBM Plex Mono',monospace;font-size:8px;letter-spacing:.1em;text-transform:uppercase;padding:1px 5px;margin-left:6px;vertical-align:middle}
.badge.pub{background:#fff;color:#000}.badge.prv{border:1px solid #2a2a2a;color:#555}
.st{display:none;margin-top:16px}.st.on{display:block}
.bar{height:1px;background:#1a1a1a;margin-bottom:10px;overflow:hidden}
.barf{height:100%;background:#16a34a;width:0;transition:width .3s ease;box-shadow:0 0 5px #16a34a}
.lg{font-family:'IBM Plex Mono',monospace;font-size:10px;display:flex;gap:8px;padding:2px 0}
.lg .ts{color:#2a2a2a;min-width:52px}
.ok{color:#16a34a}.er{color:#dc2626}.hi{color:#d97706}
.ft{margin-top:20px;display:flex;justify-content:space-between;font-family:'IBM Plex Mono',monospace;font-size:10px;color:#222}
</style></head><body>
<div class="w">
  <div class="ey">DemocracyCraft · NER Exchange</div>
  <div class="lo">BLOOMBERG LABS</div>
  <div class="tg">// Market Report Generator · Atlas API</div>
  <div class="card">
    <div class="cl">// Select Report Type</div>
    <div class="btns">
      <button class="btn pub" id="btnPub" onclick="go('public')">▶ PUBLIC REPORT <span class="badge pub">DC #NEWS</span></button>
      <button class="btn prv" id="btnPrv" onclick="go('private')">▶ FULL REPORT <span class="badge prv">BLOOMBERG DISCORD</span></button>
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
function log(m,c=''){const d=document.createElement('div');d.className='lg';d.innerHTML=`<span class="ts">${new Date().toTimeString().slice(0,8)}</span><span class="${c}">${m}</span>`;document.getElementById('log').appendChild(d);}
function bar(p){document.getElementById('bf').style.width=p+'%'}
async function go(mode){
  document.getElementById('btnPub').disabled=true;document.getElementById('btnPrv').disabled=true;
  document.getElementById('st').classList.add('on');document.getElementById('log').innerHTML='';
  bar(5);log('Connecting to Atlas...','hi');
  try{
    const r=await fetch('/api/report?mode='+mode);bar(70);
    if(!r.ok)throw new Error(await r.text());
    log('Data fetched','ok');bar(90);
    const html=await r.text();bar(100);log('Report ready','ok');
    const w=window.open('','_blank');w.document.open();w.document.write(html);w.document.close();
  }catch(e){log('ERROR: '+e.message,'er');bar(0);}
  finally{document.getElementById('btnPub').disabled=false;document.getElementById('btnPrv').disabled=false;}
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
        return [p for s,p in zip(securities, processed) if classify(s["ticker"]) == cat and s["ticker"] not in HIDDEN_TICKERS]

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
