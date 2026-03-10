import os
from datetime import datetime, timezone
from flask import Flask, render_template_string, request
import httpx

app = Flask(__name__)

ATLAS_URL = os.environ.get("ATLAS_URL", "").rstrip("/")
ATLAS_KEY = os.environ.get("ATLAS_KEY", "atl_Bloom_mkt_reports_MKZaifOWZHoAlDSWYWBaGCtUfFxx5Fvd")

NER_BONDS       = {"RNC-B", "VSP3"}
NER_ETFS        = {"CGF", "RNHC", "SRI"}
NER_COMMODITIES = {"NTR"}
HIDDEN_TICKERS  = {"RNHC", "RNC-B", "VSP3"}

def is_tse(ticker):   return str(ticker).startswith("TSE:")
def classify_ner(t):
    if t in NER_BONDS: return "Bond"
    if t in NER_ETFS:  return "ETF"
    if t in NER_COMMODITIES: return "Commodity"
    return "Stock"
def classify_tse(st):
    if not st: return "Stock"
    s = st.lower()
    if s in ("bond","fixed income","debt"):         return "Bond"
    if s in ("etf","fund","index fund"):            return "ETF"
    if s in ("commodity","resource","commodities"): return "Commodity"
    return "Stock"
def classify(s):
    t = s["ticker"]
    return classify_tse(s.get("security_type")) if is_tse(t) else classify_ner(t)

def atlas(path):
    r = httpx.get(f"{ATLAS_URL}{path}", headers={"X-Atlas-Key": ATLAS_KEY}, timeout=15)
    r.raise_for_status()
    return r.json()

def fetch_all():
    raw = {}
    raw["securities"] = atlas("/securities?include_derived=true")
    raw["orderbook"]  = atlas("/orderbook")
    tickers = [s["ticker"] for s in raw["securities"]] if isinstance(raw["securities"], list) else []
    history = {}
    for t in tickers:
        try:    history[t] = atlas(f"/history/{t}?days=7&limit=50")
        except: history[t] = {}
    raw["history"] = history
    return raw

def fmt(v, d=2):
    if v is None: return None
    try:    return round(float(v), d)
    except: return None
def fmts(v, d=2): return str(fmt(v,d)) if fmt(v,d) is not None else "—"
def fmtbig(v):
    """Format large numbers: 1,234,567 → 1.23M"""
    if v is None: return "—"
    try:
        v = float(v)
        if v >= 1_000_000: return f"{v/1_000_000:.2f}M"
        if v >= 1_000:     return f"{v/1_000:.1f}K"
        return str(round(v,2))
    except: return "—"

def price_change(hist, current):
    if not hist or current is None: return None, None
    if isinstance(hist, dict): hist = hist.get("data", [])
    if not isinstance(hist, list) or not hist: return None, None
    prices = [float(e["price"]) for e in hist if isinstance(e,dict) and e.get("price") is not None]
    if not prices: return None, None
    prev = prices[-1]
    if prev == 0: return None, None
    chg = round(current - prev, 4)
    return chg, round((chg/prev)*100, 2)

def compute_indices(securities):
    ner = {"Stock":[],"ETF":[],"Bond":[],"Commodity":[],"All":[]}
    tse = {"Stock":[],"ETF":[],"Bond":[],"Commodity":[],"All":[]}
    for s in securities:
        if s.get("hidden") or s["ticker"] in HIDDEN_TICKERS: continue
        p = s.get("market_price")
        if p is None: continue
        cat = classify(s)
        b = tse if is_tse(s["ticker"]) else ner
        b[cat].append(float(p)); b["All"].append(float(p))
    def avg(l): return round(sum(l)/len(l),4) if l else None
    ni = [
        {"ticker":"B:COMP",  "name":"NER Composite",   "value":avg(ner["All"]),       "desc":"All NER securities"},
        {"ticker":"B:STK",   "name":"NER Stocks",       "value":avg(ner["Stock"]),     "desc":"NER equity basket"},
        {"ticker":"B:ETF",   "name":"NER Funds",        "value":avg(ner["ETF"]),       "desc":"NER ETF basket"},
        {"ticker":"B:BOND",  "name":"NER Fixed Income", "value":avg(ner["Bond"]),      "desc":"NER bond basket"},
        {"ticker":"B:CMDTY", "name":"NER Commodities",  "value":avg(ner["Commodity"]), "desc":"NER commodity basket"},
    ]
    ti = [
        {"ticker":"T:COMP",  "name":"TSE Composite",   "value":avg(tse["All"]),       "desc":"All TSE securities"},
        {"ticker":"T:STK",   "name":"TSE Stocks",       "value":avg(tse["Stock"]),     "desc":"TSE equity basket"},
        {"ticker":"T:ETF",   "name":"TSE Funds",        "value":avg(tse["ETF"]),       "desc":"TSE ETF basket"},
        {"ticker":"T:BOND",  "name":"TSE Fixed Income", "value":avg(tse["Bond"]),      "desc":"TSE bond basket"},
        {"ticker":"T:CMDTY", "name":"TSE Commodities",  "value":avg(tse["Commodity"]), "desc":"TSE commodity basket"},
    ]
    return ni, ti

def make_spark(prices, color, w=200, h=28):
    if len(prices) < 2: return ""
    mn, mx = min(prices), max(prices)
    rng = mx-mn if mx!=mn else mn*0.1 if mn else 1
    pad = rng*0.3; y0=mn-pad; y1=mx+pad; yr=y1-y0
    def px(i): return round(i/(len(prices)-1)*w, 1)
    def py(p): return round(h-((p-y0)/yr*h), 1)
    pts = [(px(i),py(p)) for i,p in enumerate(prices)]
    line = " ".join(f"{x},{y}" for x,y in pts)
    fill = line+f" {pts[-1][0]},{h} {pts[0][0]},{h}"
    uid  = abs(hash(f"{color}{round(mn,2)}{len(prices)}"))%99999
    return (f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" style="display:block;width:100%;height:100%" xmlns="http://www.w3.org/2000/svg">'
            f'<defs><linearGradient id="g{uid}" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0%" stop-color="{color}" stop-opacity="0.15"/>'
            f'<stop offset="100%" stop-color="{color}" stop-opacity="0.0"/>'
            f'</linearGradient></defs>'
            f'<polygon points="{fill}" fill="url(#g{uid})"/>'
            f'<polyline points="{line}" fill="none" stroke="{color}" stroke-width="1.2" stroke-linejoin="round" stroke-linecap="round"/>'
            f'</svg>')

def process_sec(s, history, total_mcap_ner, total_mcap_tse):
    t       = s["ticker"]
    price   = fmt(s.get("market_price"))
    derived = s.get("derived") or {}
    chg, chg_pct = price_change(history.get(t), price)
    cls = "up" if chg_pct and chg_pct>0 else ("dn" if chg_pct and chg_pct<0 else "fl")

    # History analysis
    hist = history.get(t, {})
    if isinstance(hist, dict): hist = hist.get("data", [])
    valid_hist = [e for e in hist if isinstance(e,dict) and e.get("price") is not None]
    prices_raw = [float(e["price"]) for e in reversed(valid_hist)]
    if price is not None: prices_raw.append(price)

    all_prices   = [float(e["price"]) for e in valid_hist]
    all_volumes  = [float(e["volume"]) for e in valid_hist if e.get("volume") is not None]
    trade_count  = len(valid_hist)
    hi7  = round(max(all_prices), 4)  if all_prices else None
    lo7  = round(min(all_prices), 4)  if all_prices else None
    rng7 = round(hi7-lo7, 4)          if hi7 is not None and lo7 is not None else None
    avg_vol_trade = round(sum(all_volumes)/len(all_volumes), 2) if all_volumes else None
    total_vol7    = round(sum(all_volumes), 2) if all_volumes else None
    last_trade_ts = valid_hist[0].get("timestamp","") if valid_hist else ""
    last_trade_fmt = last_trade_ts[:16].replace("T"," ") if last_trade_ts else "—"

    # VWAP momentum: 24h vs 7d
    vwap7  = fmt(derived.get("vwap_7d"))
    vwap24 = fmt(derived.get("vwap_24h"))
    if vwap24 and vwap7 and vwap7 > 0:
        mom_pct = round((vwap24-vwap7)/vwap7*100, 2)
        mom_cls = "up" if mom_pct>0 else ("dn" if mom_pct<0 else "fl")
    else:
        mom_pct = None; mom_cls = "fl"

    # Market cap + dominance
    mcap   = fmt(s.get("market_cap"))
    shares = s.get("total_shares")
    # Compute market cap from price*shares if not provided
    if mcap is None and price is not None and shares:
        mcap = fmt(float(price)*float(shares))
    exch = "TSE" if is_tse(t) else "NER"
    total_mcap = total_mcap_tse if exch=="TSE" else total_mcap_ner
    dominance  = round(mcap/total_mcap*100, 2) if mcap and total_mcap else None

    display_ticker = t.replace("TSE:","") if is_tse(t) else t
    return {
        "ticker":         t,
        "display_ticker": display_ticker,
        "exchange":       exch,
        "name":           s.get("full_name", t),
        "price":          price,
        "price_str":      str(price) if price is not None else "—",
        "frozen":         bool(s.get("frozen")),
        "shares":         f"{int(shares):,}" if shares else "—",
        "market_cap":     mcap,
        "mcap_fmt":       fmtbig(mcap),
        "dominance":      dominance,
        "shareholders":   s.get("shareholder_count"),
        "vwap7":          vwap7,
        "vwap24":         vwap24,
        "vol7":           fmt(derived.get("volatility_7d")),
        "liq":            fmt(derived.get("liquidity_score")),
        "spread":         fmt(derived.get("spread")),
        "spread_pct":     fmt(derived.get("spread_pct")),
        "imbalance":      fmt(derived.get("orderbook_imbalance"),4),
        "chg":            chg,
        "chg_pct":        chg_pct,
        "cls":            cls,
        "prices":         prices_raw,
        "hi7":            hi7,
        "lo7":            lo7,
        "rng7":           rng7,
        "trade_count":    trade_count,
        "avg_vol_trade":  avg_vol_trade,
        "total_vol7":     total_vol7,
        "last_trade":     last_trade_fmt,
        "mom_pct":        mom_pct,
        "mom_cls":        mom_cls,
        "sec_type":       s.get("security_type") or "",
        "updated_at":     (s.get("updated_at","")[:16].replace("T"," ")) if s.get("updated_at") else "—",
    }

def process_ob(book, name_map):
    ticker = book.get("ticker","?")
    bids, asks = [], []
    all_bids_raw = book.get("bids") or []
    all_asks_raw = book.get("asks") or []
    for e in all_bids_raw[:4]:
        p = e.get("price"); q = e.get("quantity") or e.get("qty")
        if p is not None: bids.append({"price":fmt(p),"qty":int(q) if q else "?"})
    for e in all_asks_raw[:4]:
        p = e.get("price"); q = e.get("quantity") or e.get("qty")
        if p is not None: asks.append({"price":fmt(p),"qty":int(q) if q else "?"})
    # Largest wall detection
    def biggest(lst):
        best = None
        for e in lst:
            p = e.get("price"); q = e.get("quantity") or e.get("qty")
            if p is not None and q is not None:
                if best is None or float(q) > float(best["qty"]):
                    best = {"price":fmt(p),"qty":int(float(q))}
        return best
    bid_wall = biggest(all_bids_raw)
    ask_wall = biggest(all_asks_raw)

    spread = fmt(book.get("spread"))
    spread_pct = fmt(book.get("spread_pct"))
    if spread is None and bids and asks:
        bb=bids[0]["price"]; ba=asks[0]["price"]
        if bb and ba:
            spread=fmt(ba-bb); spread_pct=fmt(((ba-bb)/bb)*100) if bb else None
    display = ticker.replace("TSE:","") if is_tse(ticker) else ticker
    return {
        "ticker":ticker,"display_ticker":display,
        "exchange":"TSE" if is_tse(ticker) else "NER",
        "name":name_map.get(ticker,display),
        "bids":bids,"asks":asks,
        "bid_wall":bid_wall,"ask_wall":ask_wall,
        "spread":spread,"spread_pct":spread_pct,
        "best_bid":fmt(book.get("best_bid")),
        "best_ask":fmt(book.get("best_ask")),
        "bid_depth":book.get("bid_depth",0),
        "ask_depth":book.get("ask_depth",0),
        "imbalance":fmt(book.get("imbalance"),4),
        "mid":fmt(book.get("mid")),
    }

# ─────────────────────────────────────────────────────────────────────────────
# HTML HELPERS
# ─────────────────────────────────────────────────────────────────────────────

FONTS = '<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600;700&family=IBM+Plex+Sans:wght@300;400;500;600;700&display=swap" rel="stylesheet">'

BASE_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
html,body{background:#111;color:#e5e5e5;font-family:'IBM Plex Sans',sans-serif}
.toolbar{background:#0a0a0a;border-bottom:1px solid #1e1e1e;padding:8px 28px;display:flex;gap:8px;align-items:center;position:fixed;top:0;left:0;right:0;z-index:100;height:36px}
.tbtn{padding:3px 12px;font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;border:1px solid #2a2a2a;background:transparent;color:#555;cursor:pointer;transition:all .15s}
.tbtn:hover,.tbtn.p{background:#fff;color:#000;border-color:#fff}
.tsp{flex:1}.th{font-family:'IBM Plex Mono',monospace;font-size:10px;color:#333}
.pages{margin-top:36px;display:flex;flex-direction:column;align-items:center;padding:12px 0 40px;gap:10px;background:#0a0a0a}
.page{width:1280px;height:720px;background:#111;border:1px solid #1e1e1e;position:relative;display:flex;flex-direction:column;overflow:hidden}
.pi{flex:1;padding:20px 28px 14px;display:flex;flex-direction:column;overflow:hidden}
.ph{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;padding-bottom:9px;border-bottom:1px solid #1e1e1e;flex-shrink:0}
.ph-l{display:flex;align-items:center;gap:10px}
.ph-logo{font-family:'IBM Plex Mono',monospace;font-size:12px;font-weight:600;color:#fff}
.ph-pipe{width:1px;height:10px;background:#222}
.ph-sub{font-family:'IBM Plex Mono',monospace;font-size:9px;letter-spacing:.12em;text-transform:uppercase;color:#333}
.ph-r{display:flex;gap:12px;align-items:center}
.ph-date{font-family:'IBM Plex Mono',monospace;font-size:11px;color:#888}
.ph-time{font-family:'IBM Plex Mono',monospace;font-size:10px;color:#333}
.pf{padding-top:6px;border-top:1px solid #161616;display:flex;justify-content:space-between;font-family:'IBM Plex Mono',monospace;font-size:8px;color:#1e1e1e;flex-shrink:0;margin-top:auto}
.pn{position:absolute;bottom:7px;right:11px;font-family:'IBM Plex Mono',monospace;font-size:9px;color:#1e1e1e}
.sh{font-family:'IBM Plex Mono',monospace;font-size:8px;letter-spacing:.18em;text-transform:uppercase;color:#444;padding-bottom:5px;border-bottom:1px solid #1e1e1e;margin-bottom:0;flex-shrink:0}
.exb{display:inline-block;font-family:'IBM Plex Mono',monospace;font-size:7px;font-weight:700;letter-spacing:.04em;padding:1px 4px;vertical-align:middle;margin-left:3px;border-radius:1px}
.exb.NER{background:#0d1f15;color:#16a34a;border:1px solid #16a34a44}
.exb.TSE{background:#0d1525;color:#3b82f6;border:1px solid #3b82f644}
.up{color:#16a34a}.dn{color:#dc2626}.fl{color:#333}
/* security card */
.sc{padding:5px 0 3px;border-bottom:1px solid #171717}
.sc:last-child{border-bottom:none}
.sc-top{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:1px}
.sc-tk{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#555;letter-spacing:.04em}
.sc-px{font-family:'IBM Plex Mono',monospace;font-size:18px;font-weight:600;color:#fff;line-height:1}
.sc-nm{font-size:10px;font-weight:600;color:#777;margin-bottom:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sc-ch{font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:500;margin-bottom:1px}
.sc-mt{display:flex;flex-wrap:wrap;gap:5px;margin-top:1px}
.sc-mi{font-family:'IBM Plex Mono',monospace;font-size:8px;color:#2a2a2a}
.sc-mi span{color:#555}
.sc-row2{display:flex;gap:5px;flex-wrap:wrap;margin-top:1px}
.sc-tag{font-family:'IBM Plex Mono',monospace;font-size:7.5px;color:#2a2a2a}
.sc-tag span{color:#444}
.frz{display:inline-block;font-family:'IBM Plex Mono',monospace;font-size:7px;letter-spacing:.05em;text-transform:uppercase;border:1px solid #dc2626;color:#dc2626;padding:0 3px;margin-left:3px;vertical-align:middle}
.sc-spark{height:18px;margin-top:2px;overflow:hidden}
/* dominance bar */
.dom-bar-wrap{height:2px;background:#1a1a1a;margin-top:3px;overflow:hidden;border-radius:1px}
.dom-bar{height:100%;border-radius:1px;transition:width .3s}
/* orderbook */
.ob-card{background:#0f0f0f;border:1px solid #1a1a1a;padding:9px 11px}
.ob-tk{font-family:'IBM Plex Mono',monospace;font-size:8px;color:#666;letter-spacing:.04em;margin-bottom:1px}
.ob-nm{font-size:9px;font-weight:600;color:#888;margin-bottom:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ob-cols{display:grid;grid-template-columns:1fr 1fr;gap:5px}
.ob-sl{font-family:'IBM Plex Mono',monospace;font-size:7px;letter-spacing:.1em;text-transform:uppercase;margin-bottom:2px}
.ob-sl.bid{color:#16a34a}.ob-sl.ask{color:#dc2626}
.ob-lv{font-family:'IBM Plex Mono',monospace;font-size:9px;display:flex;justify-content:space-between;padding:1px 0}
.ob-p{color:#bbb}.ob-q{color:#444}
.ob-em{font-family:'IBM Plex Mono',monospace;font-size:8px;color:#222;font-style:italic}
.ob-sp{margin-top:4px;padding-top:3px;border-top:1px solid #161616;font-family:'IBM Plex Mono',monospace;font-size:8px;display:flex;justify-content:space-between;color:#333}
.spv{color:#555}.spv.w{color:#d97706}.spv.d{color:#dc2626}
.ob-depth{margin-top:2px;font-family:'IBM Plex Mono',monospace;font-size:7.5px;color:#2a2a2a;display:flex;justify-content:space-between}
@media print{.toolbar{display:none}.pages{margin-top:0;padding:0;gap:0}.page{border:none;page-break-after:always}}
"""

def ph(title, ds, ts):
    return (f'<div class="ph"><div class="ph-l"><span class="ph-logo">BLOOMBERG LABS</span>'
            f'<span class="ph-pipe"></span><span class="ph-sub">{title}</span></div>'
            f'<div class="ph-r"><span class="ph-date">{ds}</span>'
            f'<span class="ph-time">{ts} UTC</span></div></div>')

def pf(n, t):
    return (f'<div class="pf"><span>BLOOMBERG LABS · DEMOCRACYCRAFT · NER &amp; TSE · ATLAS MARKET INFRASTRUCTURE</span>'
            f'<span>PAGE {n} OF {t} · CONFIDENTIAL · NOT FOR DISTRIBUTION</span></div>')

def exb(exchange):
    return f'<span class="exb {exchange}">{exchange}</span>'

def sc_html(s, meta=True, spark=True, extended=False):
    """Security card. extended=True adds hi/lo/range/trade data/momentum/dominance."""
    frz   = '<span class="frz">FRZ</span>' if s["frozen"] else ""
    badge = exb(s["exchange"])
    if s["chg_pct"] is not None:
        sign  = "+" if s["chg_pct"]>0 else ""
        sign2 = "+" if s["chg"] and s["chg"]>0 else ""
        chg_str = f"{sign}{s['chg_pct']}% ({sign2}{s['chg']})" if s["chg"] is not None else f"{sign}{s['chg_pct']}%"
    else:
        chg_str = "—"

    meta_html = ""
    if meta:
        meta_html = (f'<div class="sc-mt">'
                     f'<span class="sc-mi">VWAP7 <span>{fmts(s["vwap7"])}</span></span>'
                     f'<span class="sc-mi">VOL <span>{fmts(s["vol7"])}</span></span>'
                     f'<span class="sc-mi">LIQ <span>{fmts(s["liq"])}</span></span>'
                     f'<span class="sc-mi">SHRS <span>{s["shares"]}</span></span>'
                     f'</div>')

    ext_html = ""
    if extended:
        # Row 1: Hi/Lo/Range
        hi  = fmts(s.get("hi7"))
        lo  = fmts(s.get("lo7"))
        rng = fmts(s.get("rng7"))
        # Row 2: Trade count, avg size, total volume
        tc  = s.get("trade_count",0)
        avt = fmts(s.get("avg_vol_trade"))
        tvol= fmtbig(s.get("total_vol7"))
        # Row 3: Momentum
        mom_pct = s.get("mom_pct")
        mom_cls = s.get("mom_cls","fl")
        mom_sign = "+" if mom_pct and mom_pct>0 else ""
        mom_str = f'{mom_sign}{mom_pct}%' if mom_pct is not None else "—"
        # Row 4: Market cap + dominance bar
        mcap_str  = s.get("mcap_fmt","—")
        dom       = s.get("dominance")
        dom_str   = f'{dom}%' if dom is not None else "—"
        dom_w     = min(100, dom) if dom else 0
        bar_color = "#3b82f6" if s["exchange"]=="TSE" else "#16a34a"
        # Shareholders + last trade
        holders = s.get("shareholders")
        holders_str = f"{int(holders):,}" if holders else "—"
        last = s.get("last_trade","—")
        updated = s.get("updated_at","—")

        ext_html = (
            f'<div class="sc-row2">'
            f'<span class="sc-tag">7d HI <span>{hi}</span></span>'
            f'<span class="sc-tag">LO <span>{lo}</span></span>'
            f'<span class="sc-tag">RNG <span>{rng}</span></span>'
            f'</div>'
            f'<div class="sc-row2">'
            f'<span class="sc-tag">TRADES <span>{tc}</span></span>'
            f'<span class="sc-tag">AVG SIZE <span>{avt}</span></span>'
            f'<span class="sc-tag">VOL7 <span>{tvol}</span></span>'
            f'</div>'
            f'<div class="sc-row2">'
            f'<span class="sc-tag">MCAP <span>{mcap_str}</span></span>'
            f'<span class="sc-tag">DOM <span style="color:{bar_color}">{dom_str}</span></span>'
            f'<span class="sc-tag">HOLDERS <span>{holders_str}</span></span>'
            f'</div>'
            f'<div class="sc-row2">'
            f'<span class="sc-tag">MOM24h <span class="{mom_cls}">{mom_str}</span></span>'
            f'<span class="sc-tag">LAST TRD <span>{last}</span></span>'
            f'</div>'
            f'<div class="dom-bar-wrap"><div class="dom-bar" style="width:{dom_w}%;background:{bar_color}22;border-right:1px solid {bar_color}"></div></div>'
        )

    sp = ""
    if spark and s.get("prices"):
        sc = "#16a34a" if s["cls"]=="up" else ("#dc2626" if s["cls"]=="dn" else "#555")
        svg = make_spark(s["prices"], sc)
        if svg: sp = f'<div class="sc-spark">{svg}</div>'

    return (f'<div class="sc">'
            f'<div class="sc-top"><span class="sc-tk">{s["display_ticker"]}{badge}{frz}</span>'
            f'<span class="sc-px">{s["price_str"]}</span></div>'
            f'<div class="sc-nm">{s["name"]}</div>'
            f'<div class="sc-ch {s["cls"]}">{chg_str}</div>'
            f'{meta_html}{ext_html}{sp}</div>')

def ob_html(ob):
    badge = exb(ob["exchange"])
    bh = "".join(f'<div class="ob-lv"><span class="ob-p">{lv["price"]}</span><span class="ob-q">×{lv["qty"]}</span></div>' for lv in ob["bids"]) or '<div class="ob-em">no bids</div>'
    ah = "".join(f'<div class="ob-lv"><span class="ob-p">{lv["price"]}</span><span class="ob-q">×{lv["qty"]}</span></div>' for lv in ob["asks"]) or '<div class="ob-em">no asks</div>'
    sp = ""
    if ob["spread"] is not None:
        cls = "d" if ob["spread_pct"] and ob["spread_pct"]>25 else ("w" if ob["spread_pct"] and ob["spread_pct"]>10 else "")
        sp = f'<div class="ob-sp"><span>Spread</span><span class="spv {cls}">{ob["spread"]} ({ob["spread_pct"]}%)</span></div>'
    depth = (f'<div class="ob-depth">'
             f'<span>Bid {fmtbig(ob["bid_depth"])}</span>'
             f'<span>Ask {fmtbig(ob["ask_depth"])}</span></div>') if ob.get("bid_depth") is not None else ""
    mid_html = f'<div class="ob-depth"><span>Mid {ob["mid"]}</span></div>' if ob.get("mid") else ""
    return (f'<div class="ob-card">'
            f'<div class="ob-tk">{ob["display_ticker"]} {badge}</div>'
            f'<div class="ob-nm">{ob["name"]}</div>'
            f'<div class="ob-cols"><div><div class="ob-sl bid">Bids</div>{bh}</div>'
            f'<div><div class="ob-sl ask">Asks</div>{ah}</div></div>'
            f'{sp}{depth}{mid_html}</div>')

def toolbar(label):
    return (f'<div class="toolbar">'
            f'<button class="tbtn p" onclick="window.print()">⎙ PDF</button>'
            f'<button class="tbtn" onclick="window.close()">← Back</button>'
            f'<span class="tsp"></span><span class="th">{label}</span></div>')

# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC REPORT
# ─────────────────────────────────────────────────────────────────────────────

def build_public(ctx):
    d = ctx
    all_secs = d["stocks"]+d["etfs"]+d["bonds"]+d["commodities"]
    movers   = sorted([s for s in all_secs if s["chg_pct"] is not None],
                      key=lambda x: abs(x["chg_pct"]), reverse=True)[:3]

    stats_html = ""
    for label, val, sub, col in [
        ("NER Composite", d["ner_comp"],  "Equal-weighted avg", ""),
        ("TSE Composite", d["tse_comp"],  "Equal-weighted avg", "color:#3b82f6"),
        ("NER Stocks",    d["ner_stk"],   "NER equity basket",  ""),
        ("TSE Stocks",    d["tse_stk"],   "TSE equity basket",  "color:#3b82f6"),
        ("Avg Liquidity", d["avg_liq"],   "Market liquidity",   ""),
        ("Frozen",        str(d["frozen_count"]), "Halted",
         "color:#dc2626" if d["frozen_count"]>0 else "color:#16a34a"),
    ]:
        stats_html += (f'<div class="sb"><div class="sb-l">{label}</div>'
                       f'<div class="sb-v" style="{col}">{val}</div>'
                       f'<div class="sb-s">{sub}</div></div>')

    movers_html = ""
    for s in movers:
        sign  = "+" if s["chg_pct"]>0 else ""
        arrow = "▲" if s["chg_pct"]>0 else "▼"
        col   = "#16a34a" if s["chg_pct"]>0 else "#dc2626"
        sp    = make_spark(s.get("prices",[]), col, w=300, h=32)
        bh    = (f'<span style="font-family:IBM Plex Mono,monospace;font-size:7px;padding:1px 4px;border-radius:1px;margin-left:4px;'
                 f'background:{"#0d1525" if s["exchange"]=="TSE" else "#0d1f15"};'
                 f'color:{"#3b82f6" if s["exchange"]=="TSE" else "#16a34a"}">{s["exchange"]}</span>')
        movers_html += (f'<div class="mv">'
                        f'<div class="mv-row1"><span class="mv-tk">{s["display_ticker"]}{bh}</span>'
                        f'<span class="mv-px">{s["price_str"]}</span>'
                        f'<span class="mv-ch" style="color:{col}">{arrow} {sign}{s["chg_pct"]}%</span></div>'
                        f'<div class="mv-nm">{s["name"]}</div>{sp}</div>')

    if not movers_html:
        movers_html = '<div style="font-family:IBM Plex Mono,monospace;font-size:10px;color:#333;padding:10px 0">No price change data</div>'

    idx_html = ""
    for ni, ti in zip(d["ner_indices"], d["tse_indices"]):
        nv = ni["value"] if ni["value"] is not None else "—"
        tv = ti["value"] if ti["value"] is not None else "—"
        idx_html += (f'<div class="ir"><span class="ir-tk">{ni["ticker"]}</span><span class="ir-v">{nv}</span></div>'
                     f'<div class="ir" style="border-left:1px solid #1a2a3a">'
                     f'<span class="ir-tk" style="color:#3b82f6">{ti["ticker"]}</span>'
                     f'<span class="ir-v" style="color:#3b82f6">{tv}</span></div>')

    frozen_warn = ""
    frz_list = [s for s in all_secs if s["frozen"]]
    if frz_list:
        tickers_str = " · ".join(s["display_ticker"] for s in frz_list)
        frozen_warn = (f'<div style="background:#150000;border:1px solid #dc262633;padding:4px 12px;'
                       f'font-family:IBM Plex Mono,monospace;font-size:8px;color:#dc2626;'
                       f'letter-spacing:.05em;flex-shrink:0;margin-top:5px">⚠ TRADING HALTED: {tickers_str}</div>')

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<title>Bloomberg Labs — {d['date_str']}</title>{FONTS}
<style>
{BASE_CSS}
.pub-wrap{{display:flex;justify-content:center}}
.pub-inner{{width:1280px;height:720px;background:#111;display:flex;flex-direction:column;padding:16px 24px 12px;overflow:hidden;border:1px solid #1e1e1e}}
.sbar{{display:grid;grid-template-columns:repeat(6,1fr);gap:1px;background:#1a1a1a;border:1px solid #1a1a1a;margin-bottom:10px;flex-shrink:0}}
.sb{{background:#0f0f0f;padding:6px 10px}}
.sb-l{{font-family:'IBM Plex Mono',monospace;font-size:7.5px;letter-spacing:.1em;text-transform:uppercase;color:#333;margin-bottom:2px}}
.sb-v{{font-family:'IBM Plex Mono',monospace;font-size:15px;font-weight:600;color:#fff}}
.sb-s{{font-family:'IBM Plex Mono',monospace;font-size:7px;color:#222;margin-top:1px}}
.pub-body{{flex:1;display:grid;grid-template-columns:190px 1fr 200px;gap:16px;overflow:hidden;min-height:0}}
.hero-tag{{font-family:'IBM Plex Mono',monospace;font-size:8px;letter-spacing:.14em;text-transform:uppercase;color:#2a2a2a;margin-bottom:5px}}
.hero-title{{font-size:50px;font-weight:700;letter-spacing:-.03em;line-height:.9;color:#fff;margin-bottom:8px}}
.hero-sub{{font-family:'IBM Plex Mono',monospace;font-size:8px;color:#2a2a2a;line-height:2.1}}
.mv-col{{overflow:hidden;display:flex;flex-direction:column}}
.mv{{padding:6px 0;border-bottom:1px solid #161616}}
.mv:last-child{{border-bottom:none}}
.mv-row1{{display:flex;align-items:baseline;gap:5px;margin-bottom:1px}}
.mv-tk{{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#666}}
.mv-px{{font-family:'IBM Plex Mono',monospace;font-size:16px;font-weight:600;color:#fff;margin-left:auto}}
.mv-ch{{font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600}}
.mv-nm{{font-size:9px;color:#444;margin-bottom:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.idx-col{{overflow:hidden;display:flex;flex-direction:column}}
.ir{{display:flex;justify-content:space-between;align-items:baseline;padding:3px 5px;border-bottom:1px solid #131313}}
.ir:last-child{{border-bottom:none}}
.ir-tk{{font-family:'IBM Plex Mono',monospace;font-size:8px;color:#333;letter-spacing:.05em}}
.ir-v{{font-family:'IBM Plex Mono',monospace;font-size:12px;font-weight:600;color:#fff}}
.pub-footer{{padding-top:5px;border-top:1px solid #171717;display:flex;justify-content:space-between;font-family:'IBM Plex Mono',monospace;font-size:8px;color:#222;flex-shrink:0}}
</style></head><body>
{toolbar("PUBLIC VERSION · DC #news · Bloomberg Labs")}
<div style="display:flex;flex-direction:column;align-items:center;padding:12px 0 32px;margin-top:36px;background:#0a0a0a">
<div class="pub-wrap"><div class="pub-inner">
  {ph("DemocracyCraft · NER &amp; TSE · Daily Market Recap", d['date_str'], d['time_str'])}
  <div class="sbar">{stats_html}</div>
  <div class="pub-body">
    <div>
      <div class="hero-tag">NER &amp; TSE · Bloomberg Labs</div>
      <div class="hero-title">Market<br>Recap</div>
      <div class="hero-sub">// {d['date_str']}<br>// {d['time_str']} UTC<br>// {d['active_count']} Active · {d['frozen_count']} Frozen<br>// {d['total_count']} Securities Listed<br>// NER + The Stock Exchange</div>
    </div>
    <div class="mv-col">
      <div class="sh" style="margin-bottom:5px">// Top Movers</div>
      {movers_html}
    </div>
    <div class="idx-col">
      <div class="sh" style="margin-bottom:3px">// Bloomberg Indices</div>
      {idx_html}
    </div>
  </div>
  {frozen_warn}
  <div class="pub-footer">
    <span>BLOOMBERG LABS · DEMOCRACYCRAFT · NER &amp; TSE · ATLAS MARKET INFRASTRUCTURE</span>
    <span>{d['date_str']} · {d['time_str']} UTC · CONFIDENTIAL</span>
  </div>
</div></div>
</div></body></html>"""

# ─────────────────────────────────────────────────────────────────────────────
# PRIVATE REPORT — 9 pages
# ─────────────────────────────────────────────────────────────────────────────

def build_private(ctx):
    d  = ctx
    ds = d["date_str"]; ts = d["time_str"]
    T  = 9

    def chunk(lst, n):
        if not lst: return [[] for _ in range(n)]
        k = max(1,(len(lst)+n-1)//n)
        return [lst[i*k:(i+1)*k] for i in range(n)]

    all_secs = d["stocks"]+d["etfs"]+d["bonds"]+d["commodities"]

    # ── P1: Cover ────────────────────────────────────────────────────────────
    stats_html = ""
    for label, val, sub, col in [
        ("NER Composite", d["ner_comp"],  "Equal-weighted avg", ""),
        ("TSE Composite", d["tse_comp"],  "Equal-weighted avg", "color:#3b82f6"),
        ("NER Stocks",    d["ner_stk"],   "NER equity basket",  ""),
        ("TSE Stocks",    d["tse_stk"],   "TSE equity basket",  "color:#3b82f6"),
        ("Avg Liquidity", d["avg_liq"],   "Market liquidity",   ""),
        ("Frozen",        str(d["frozen_count"]), "Trading halted",
         "color:#dc2626" if d["frozen_count"]>0 else "color:#16a34a"),
    ]:
        stats_html += (f'<div class="hs"><div class="hs-l">{label}</div>'
                       f'<div class="hs-v" style="{col}">{val}</div>'
                       f'<div class="hs-s">{sub}</div></div>')

    # Market cap totals for cover
    ner_mcap_total = d.get("ner_mcap_total")
    tse_mcap_total = d.get("tse_mcap_total")
    combined_mcap  = (ner_mcap_total + tse_mcap_total) if (ner_mcap_total and tse_mcap_total) else None
    ner_mcap_pct   = round(ner_mcap_total/combined_mcap*100,1) if combined_mcap else None
    tse_mcap_pct   = round(tse_mcap_total/combined_mcap*100,1) if combined_mcap else None

    mcap_bar_html = ""
    if ner_mcap_pct and tse_mcap_pct:
        mcap_bar_html = (
            f'<div style="margin-top:8px">'
            f'<div style="font-family:IBM Plex Mono,monospace;font-size:7.5px;color:#333;letter-spacing:.12em;text-transform:uppercase;margin-bottom:3px">Market Cap Distribution</div>'
            f'<div style="height:3px;display:flex;border-radius:2px;overflow:hidden">'
            f'<div style="width:{ner_mcap_pct}%;background:#16a34a"></div>'
            f'<div style="width:{tse_mcap_pct}%;background:#3b82f6"></div>'
            f'</div>'
            f'<div style="display:flex;justify-content:space-between;font-family:IBM Plex Mono,monospace;font-size:8px;margin-top:2px">'
            f'<span style="color:#16a34a">NER {fmtbig(ner_mcap_total)} ({ner_mcap_pct}%)</span>'
            f'<span style="color:#3b82f6">TSE {fmtbig(tse_mcap_total)} ({tse_mcap_pct}%)</span>'
            f'</div></div>'
        )

    ner_idx_html = ""
    for i in d["ner_indices"]:
        v = i["value"] if i["value"] is not None else "—"
        ner_idx_html += (f'<div class="ir2"><div><div class="ir2-tk">{i["ticker"]}</div>'
                         f'<div class="ir2-nm">{i["name"]}</div></div>'
                         f'<div class="ir2-r"><div class="ir2-v">{v}</div>'
                         f'<div class="ir2-d">{i["desc"]}</div></div></div>')

    tse_idx_html = ""
    for i in d["tse_indices"]:
        v = i["value"] if i["value"] is not None else "—"
        tse_idx_html += (f'<div class="ir2"><div>'
                         f'<div class="ir2-tk" style="color:#3b82f6">{i["ticker"]}</div>'
                         f'<div class="ir2-nm">{i["name"]}</div></div>'
                         f'<div class="ir2-r"><div class="ir2-v" style="color:#3b82f6;font-size:17px">{v}</div>'
                         f'<div class="ir2-d">{i["desc"]}</div></div></div>')

    # ── P2: NER Stocks (extended cards) ──────────────────────────────────────
    ner_stocks = [s for s in d["stocks"] if s["exchange"]=="NER"]
    ner_cols   = chunk(ner_stocks, 3)
    p2_html    = "".join(f'<div>{"".join(sc_html(s, extended=True) for s in col)}</div>' for col in ner_cols)

    # ── P3: TSE Stocks (extended cards) ──────────────────────────────────────
    tse_stocks = [s for s in d["stocks"] if s["exchange"]=="TSE"]
    tse_cols   = chunk(tse_stocks, 3)
    p3_html    = "".join(f'<div>{"".join(sc_html(s, extended=True) for s in col)}</div>' for col in tse_cols)

    # ── P4: ETFs / Bonds / Commodities ───────────────────────────────────────
    ner_etfs  = [s for s in d["etfs"]        if s["exchange"]=="NER"]
    tse_etfs  = [s for s in d["etfs"]        if s["exchange"]=="TSE"]
    ner_bonds = [s for s in d["bonds"]       if s["exchange"]=="NER"]
    tse_bonds = [s for s in d["bonds"]       if s["exchange"]=="TSE"]
    ner_cmds  = [s for s in d["commodities"] if s["exchange"]=="NER"]
    tse_cmds  = [s for s in d["commodities"] if s["exchange"]=="TSE"]
    p4_etfs   = "".join(sc_html(s, extended=True) for s in ner_etfs+tse_etfs)   or '<div class="sc-empty">No data</div>'
    p4_bonds  = "".join(sc_html(s, extended=True) for s in ner_bonds+tse_bonds) or '<div class="sc-empty">No data</div>'
    p4_cmds   = "".join(sc_html(s, extended=True) for s in ner_cmds+tse_cmds)   or '<div class="sc-empty">No data</div>'

    # ── P5: Market Movers ────────────────────────────────────────────────────
    with_chg = [s for s in all_secs if s["chg_pct"] is not None]
    gainers  = sorted(with_chg, key=lambda x: x["chg_pct"], reverse=True)[:5]
    losers   = sorted(with_chg, key=lambda x: x["chg_pct"])[:5]

    def mover_row(s, rank):
        sign  = "+" if s["chg_pct"]>0 else ""
        col   = "#16a34a" if s["chg_pct"]>0 else "#dc2626"
        arrow = "▲" if s["chg_pct"]>0 else "▼"
        sp    = make_spark(s.get("prices",[]), col, w=150, h=22)
        sp_html = f'<div style="width:150px;height:22px;flex-shrink:0">{sp}</div>' if sp else '<div style="width:150px"></div>'
        mc = s.get("mcap_fmt","—"); dom = f'{s["dominance"]}%' if s["dominance"] else "—"
        return (f'<div class="mrow">'
                f'<div class="mrow-rank" style="color:{col}">#{rank}</div>'
                f'<div class="mrow-tk">{s["display_ticker"]}{exb(s["exchange"])}</div>'
                f'<div class="mrow-nm">{s["name"]}</div>'
                f'<div class="mrow-px">{s["price_str"]}</div>'
                f'<div class="mrow-ch" style="color:{col}">{arrow} {sign}{s["chg_pct"]}%</div>'
                f'<div class="mrow-abs" style="color:{col}">{sign}{s["chg"] if s["chg"] is not None else "—"}</div>'
                f'<div class="mrow-meta">LIQ {fmts(s["liq"])} · VOL {fmts(s["vol7"])} · MCAP {mc}</div>'
                f'{sp_html}</div>')

    gainers_html = "".join(mover_row(s,i+1) for i,s in enumerate(gainers)) or '<div class="mrow-empty">No data</div>'
    losers_html  = "".join(mover_row(s,i+1) for i,s in enumerate(losers))  or '<div class="mrow-empty">No data</div>'

    # ── P6: Microstructure ───────────────────────────────────────────────────
    has_spread = sorted([s for s in all_secs if s.get("spread_pct") is not None], key=lambda x: x["spread_pct"])
    by_liq     = sorted([s for s in all_secs if s["liq"]  is not None], key=lambda x: x["liq"],  reverse=True)
    by_vol     = sorted([s for s in all_secs if s["vol7"] is not None], key=lambda x: x["vol7"],  reverse=True)
    by_imb     = sorted([s for s in all_secs if s.get("imbalance") is not None], key=lambda x: abs(x["imbalance"]), reverse=True)

    # OB walls: largest single bid/ask order across all books
    bid_walls = sorted([ob for ob in d["orderbooks"] if ob.get("bid_wall")],
                       key=lambda x: x["bid_wall"]["qty"], reverse=True)[:6]
    ask_walls = sorted([ob for ob in d["orderbooks"] if ob.get("ask_wall")],
                       key=lambda x: x["ask_wall"]["qty"], reverse=True)[:6]

    def micro_row(s, val, col=""):
        return (f'<div class="mr"><span class="mr-tk">{s["display_ticker"]}{exb(s["exchange"])}</span>'
                f'<span class="mr-nm">{s["name"]}</span>'
                f'<span class="mr-v" style="{col}">{val}</span></div>')

    def wall_row(ob, side):
        w = ob[f"{side}_wall"]
        col = "color:#16a34a" if side=="bid" else "color:#dc2626"
        val_str = f'{w["price"]} × {fmtbig(w["qty"])}'
        return (f'<div class="mr"><span class="mr-tk">{ob["display_ticker"]}{exb(ob["exchange"])}</span>'
                f'<span class="mr-nm">{ob["name"]}</span>'
                f'<span class="mr-v" style="{col}">{val_str}</span></div>')

    spread_html = "".join(micro_row(s, f'{s["spread_pct"]}%') for s in has_spread[:7]) or '<div class="mr-empty">—</div>'
    liq_html    = "".join(micro_row(s, fmts(s["liq"]))       for s in by_liq[:7])      or '<div class="mr-empty">—</div>'
    vol_html    = "".join(micro_row(s, fmts(s["vol7"]))       for s in by_vol[:7])      or '<div class="mr-empty">—</div>'
    imb_rows = []
    for s in by_imb[:7]:
        iv = s["imbalance"]
        col = "color:#16a34a" if iv>0 else ("color:#dc2626" if iv<0 else "")
        imb_rows.append(micro_row(s, f'{"+" if iv>0 else ""}{iv}', col))
    imb_html = "".join(imb_rows) or '<div class="mr-empty">—</div>'

    bid_wall_html = "".join(wall_row(ob,"bid") for ob in bid_walls) or '<div class="mr-empty">—</div>'
    ask_wall_html = "".join(wall_row(ob,"ask") for ob in ask_walls) or '<div class="mr-empty">—</div>'

    # ── P7: Volume Analysis ───────────────────────────────────────────────────
    by_vol7_total  = sorted([s for s in all_secs if s.get("total_vol7") is not None], key=lambda x: x["total_vol7"],  reverse=True)
    by_trade_count = sorted([s for s in all_secs if s.get("trade_count",0)>0],         key=lambda x: x["trade_count"], reverse=True)
    by_avg_size    = sorted([s for s in all_secs if s.get("avg_vol_trade") is not None],key=lambda x: x["avg_vol_trade"], reverse=True)

    def vol_row(s, val, bar_pct, bar_col):
        return (f'<div class="vrow">'
                f'<span class="vrow-tk">{s["display_ticker"]}{exb(s["exchange"])}</span>'
                f'<span class="vrow-nm">{s["name"]}</span>'
                f'<span class="vrow-v">{val}</span>'
                f'<div class="vrow-bar-wrap"><div class="vrow-bar" style="width:{bar_pct}%;background:{bar_col}"></div></div>'
                f'</div>')

    max_vol   = by_vol7_total[0]["total_vol7"]   if by_vol7_total   else 1
    max_tc    = by_trade_count[0]["trade_count"] if by_trade_count  else 1
    max_avg   = by_avg_size[0]["avg_vol_trade"]  if by_avg_size     else 1

    vol_total_html  = "".join(vol_row(s, fmtbig(s["total_vol7"]),   min(100,s["total_vol7"]/max_vol*100),     "#16a34a" if s["exchange"]=="NER" else "#3b82f6") for s in by_vol7_total[:8])  or "—"
    vol_count_html  = "".join(vol_row(s, str(s["trade_count"]),      min(100,s["trade_count"]/max_tc*100),     "#16a34a" if s["exchange"]=="NER" else "#3b82f6") for s in by_trade_count[:8]) or "—"
    vol_avg_html    = "".join(vol_row(s, fmts(s["avg_vol_trade"]),   min(100,s["avg_vol_trade"]/max_avg*100),  "#16a34a" if s["exchange"]=="NER" else "#3b82f6") for s in by_avg_size[:8])   or "—"

    # ── P8: Orderbook Snapshot ────────────────────────────────────────────────
    ob_cards = "".join(ob_html(ob) for ob in d["orderbooks"]) or '<div style="font-family:IBM Plex Mono,monospace;font-size:11px;color:#2a2a2a;padding:20px">No orderbook data</div>'

    # ── P9: Cross-Exchange + Closing ─────────────────────────────────────────
    ner_all = [s for s in all_secs if s["exchange"]=="NER"]
    tse_all = [s for s in all_secs if s["exchange"]=="TSE"]

    def xs_block(lst, label, color):
        if not lst: return f'<div class="xs-head" style="color:{color}">{label}</div><div class="mr-empty" style="padding:8px 0">No data</div>'
        liqs   = [s["liq"]   for s in lst if s["liq"]   is not None]
        vols   = [s["vol7"]  for s in lst if s["vol7"]  is not None]
        prices = [s["price"] for s in lst if s["price"] is not None]
        chgs   = [s["chg_pct"] for s in lst if s["chg_pct"] is not None]
        mcaps  = [s["market_cap"] for s in lst if s["market_cap"] is not None]
        avg_l  = fmts(sum(liqs)/len(liqs))     if liqs   else "—"
        avg_v  = fmts(sum(vols)/len(vols))     if vols   else "—"
        avg_p  = fmts(sum(prices)/len(prices)) if prices else "—"
        avg_c  = fmt(sum(chgs)/len(chgs))      if chgs   else None
        tot_mc = fmtbig(sum(mcaps))            if mcaps  else "—"
        top_g  = max(lst, key=lambda x: x["chg_pct"] if x["chg_pct"] is not None else -999)
        top_l  = min(lst, key=lambda x: x["chg_pct"] if x["chg_pct"] is not None else 999)
        frz    = sum(1 for s in lst if s["frozen"])
        avg_c_str = f'{"+" if avg_c and avg_c>0 else ""}{avg_c}%' if avg_c is not None else "—"
        avg_c_col = f'color:{"#16a34a" if avg_c and avg_c>0 else "#dc2626"}' if avg_c is not None else ""
        rows = [
            ("Securities",     len(lst),                  ""),
            ("Total Mkt Cap",  tot_mc,                    ""),
            ("Avg Price",      avg_p,                     ""),
            ("Avg 7d Δ",       avg_c_str,                 avg_c_col),
            ("Avg Liquidity",  avg_l,                     ""),
            ("Avg Volatility", avg_v,                     ""),
            ("Frozen",         frz,                       "color:#dc2626" if frz>0 else "color:#16a34a"),
            ("Top Gainer",     f'{top_g["display_ticker"]} +{top_g["chg_pct"]}%' if top_g["chg_pct"] else "—", "color:#16a34a"),
            ("Top Loser",      f'{top_l["display_ticker"]} {top_l["chg_pct"]}%'  if top_l["chg_pct"] else "—", "color:#dc2626"),
        ]
        html = f'<div class="xs-head" style="color:{color}">{label}</div>'
        for rl, rv, rc in rows:
            html += (f'<div class="xs-row"><span class="xs-l">{rl}</span>'
                     f'<span class="xs-v" style="{rc}">{rv}</span></div>')
        return html

    xs_ner = xs_block(ner_all, "NER Exchange", "#fff")
    xs_tse = xs_block(tse_all, "The Stock Exchange", "#3b82f6")

    # closing text stats
    all_liqs = [s["liq"] for s in all_secs if s["liq"] is not None]
    all_vols = [s["vol7"] for s in all_secs if s["vol7"] is not None]
    mkt_liq  = fmts(sum(all_liqs)/len(all_liqs)) if all_liqs else "—"
    mkt_vol  = fmts(sum(all_vols)/len(all_vols)) if all_vols else "—"

    frz_warn_p9 = " · ".join(f'{s["display_ticker"]} ({s["exchange"]})' for s in all_secs if s["frozen"])

    # ─────────────────────────────────────────────────────────────────────────
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<title>Bloomberg Labs Full Report — {ds}</title>{FONTS}
<style>
{BASE_CSS}
/* P1 */
.p1-g{{flex:1;display:grid;grid-template-columns:1fr 1fr 1fr;gap:20px;overflow:hidden}}
.hero-title{{font-size:60px;font-weight:700;letter-spacing:-.03em;line-height:.88;color:#fff;margin-bottom:10px}}
.hero-tag{{font-family:'IBM Plex Mono',monospace;font-size:8px;letter-spacing:.16em;text-transform:uppercase;color:#2a2a2a;margin-bottom:6px}}
.hero-sub{{font-family:'IBM Plex Mono',monospace;font-size:8px;color:#2a2a2a;line-height:2.0}}
.hs-grid{{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:#1a1a1a;border:1px solid #1a1a1a;margin-top:auto}}
.hs{{background:#0f0f0f;padding:8px 12px}}
.hs-l{{font-family:'IBM Plex Mono',monospace;font-size:8px;letter-spacing:.1em;text-transform:uppercase;color:#2a2a2a;margin-bottom:2px}}
.hs-v{{font-family:'IBM Plex Mono',monospace;font-size:16px;font-weight:600;color:#fff}}
.hs-s{{font-family:'IBM Plex Mono',monospace;font-size:7.5px;color:#222;margin-top:1px}}
.ir2{{display:flex;justify-content:space-between;align-items:center;padding:7px 0;border-bottom:1px solid #131313}}
.ir2:last-child{{border-bottom:none}}
.ir2-tk{{font-family:'IBM Plex Mono',monospace;font-size:8px;color:#333;letter-spacing:.07em;margin-bottom:1px}}
.ir2-nm{{font-size:11px;font-weight:600;color:#aaa}}
.ir2-r{{text-align:right}}
.ir2-v{{font-family:'IBM Plex Mono',monospace;font-size:19px;font-weight:600;color:#fff}}
.ir2-d{{font-family:'IBM Plex Mono',monospace;font-size:7.5px;color:#2a2a2a;margin-top:1px}}
/* P2/P3/P4 */
.p23-g{{flex:1;display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;overflow:hidden}}
.sc-empty{{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#222;padding:8px 0}}
.p4-g{{flex:1;display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;overflow:hidden}}
.p4-col{{display:flex;flex-direction:column;overflow:hidden}}
/* P5 movers */
.p5-g{{flex:1;display:grid;grid-template-columns:1fr 1fr;gap:18px;overflow:hidden}}
.mrow{{display:grid;grid-template-columns:22px 52px 1fr 56px 68px 48px 1fr 150px;align-items:center;gap:5px;padding:5px 0;border-bottom:1px solid #141414}}
.mrow:last-child{{border-bottom:none}}
.mrow-rank{{font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:700}}
.mrow-tk{{font-family:'IBM Plex Mono',monospace;font-size:8.5px;color:#777}}
.mrow-nm{{font-size:9px;color:#555;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.mrow-px{{font-family:'IBM Plex Mono',monospace;font-size:11px;font-weight:600;color:#fff;text-align:right}}
.mrow-ch{{font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;text-align:right}}
.mrow-abs{{font-family:'IBM Plex Mono',monospace;font-size:9px;text-align:right}}
.mrow-meta{{font-family:'IBM Plex Mono',monospace;font-size:7.5px;color:#2a2a2a;padding-left:4px;white-space:nowrap;overflow:hidden}}
.mrow-empty{{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#1e1e1e;padding:10px 0}}
/* P6 microstructure */
.p6-g{{flex:1;display:grid;grid-template-columns:1fr 1fr 1fr 1fr 1fr 1fr;gap:12px;overflow:hidden}}
.micro-col{{display:flex;flex-direction:column;overflow:hidden}}
.mr{{display:flex;align-items:center;padding:3px 0;border-bottom:1px solid #131313;gap:4px}}
.mr:last-child{{border-bottom:none}}
.mr-tk{{font-family:'IBM Plex Mono',monospace;font-size:8px;color:#666;flex-shrink:0;min-width:32px}}
.mr-nm{{font-size:8.5px;color:#333;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.mr-v{{font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:600;color:#888;flex-shrink:0;text-align:right;min-width:48px}}
.mr-empty{{font-family:'IBM Plex Mono',monospace;font-size:8px;color:#1e1e1e;padding:6px 0}}
/* P7 volume */
.p7-g{{flex:1;display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;overflow:hidden}}
.vrow{{display:grid;grid-template-columns:36px 1fr 50px;align-items:center;gap:5px;padding:4px 0;border-bottom:1px solid #131313}}
.vrow:last-child{{border-bottom:none}}
.vrow-tk{{font-family:'IBM Plex Mono',monospace;font-size:8px;color:#666}}
.vrow-nm{{font-size:8.5px;color:#333;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.vrow-v{{font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:600;color:#aaa;text-align:right}}
.vrow-bar-wrap{{grid-column:1/-1;height:2px;background:#161616;border-radius:1px;overflow:hidden}}
.vrow-bar{{height:100%;border-radius:1px}}
/* P8 orderbook */
.p8-g{{flex:1;display:grid;grid-template-columns:repeat(4,1fr);gap:8px;overflow:hidden;align-content:start}}
/* P9 cross-exchange */
.p9-g{{flex:1;display:grid;grid-template-columns:1fr 1fr 1fr;gap:18px;overflow:hidden}}
.xs-head{{font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;padding-bottom:7px;border-bottom:1px solid #1a1a1a;margin-bottom:3px}}
.xs-row{{display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #111}}
.xs-row:last-child{{border-bottom:none}}
.xs-l{{font-family:'IBM Plex Mono',monospace;font-size:8.5px;color:#333}}
.xs-v{{font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:600;color:#888}}
/* closing */
.close-inner{{flex:1;display:flex;flex-direction:column;justify-content:center;align-items:center;text-align:center}}
</style></head><body>
{toolbar(f"FULL REPORT · Bloomberg Discord · {T} pages")}
<div class="pages">

<!-- ═══ P1: Cover ═══ -->
<div class="page"><div class="pi">
  {ph("Daily Market Recap · NER &amp; TSE", ds, ts)}
  <div class="p1-g">
    <div style="display:flex;flex-direction:column;justify-content:space-between">
      <div>
        <div class="hero-tag">NER &amp; TSE · Bloomberg Labs</div>
        <div class="hero-title">Market<br>Recap</div>
        <div class="hero-sub">// {ds}<br>// {ts} UTC<br>// {d['active_count']} Active · {d['frozen_count']} Frozen · {d['total_count']} Listed<br>// NER Exchange · The Stock Exchange</div>
      </div>
      {mcap_bar_html}
      <div class="hs-grid">{stats_html}</div>
    </div>
    <div style="display:flex;flex-direction:column;">
      <div class="sh" style="margin-bottom:0">// NER Indices</div>
      {ner_idx_html}
    </div>
    <div style="display:flex;flex-direction:column;">
      <div class="sh" style="margin-bottom:0;color:#3b82f6;border-color:#0d1525">// TSE Indices</div>
      {tse_idx_html}
    </div>
  </div>
  {pf(1,T)}
</div><div class="pn">1/{T}</div></div>

<!-- ═══ P2: NER Stocks ═══ -->
<div class="page"><div class="pi">
  {ph("Securities · NER Stocks", ds, ts)}
  <div class="p23-g">{p2_html or '<div class="sc-empty" style="grid-column:1/-1">No NER stocks</div>'}</div>
  {pf(2,T)}
</div><div class="pn">2/{T}</div></div>

<!-- ═══ P3: TSE Stocks ═══ -->
<div class="page"><div class="pi">
  {ph("Securities · TSE Stocks", ds, ts)}
  <div class="p23-g">{p3_html or '<div class="sc-empty" style="grid-column:1/-1">No TSE stocks</div>'}</div>
  {pf(3,T)}
</div><div class="pn">3/{T}</div></div>

<!-- ═══ P4: ETFs / Bonds / Commodities ═══ -->
<div class="page"><div class="pi">
  {ph("Securities · Funds · Fixed Income · Commodities", ds, ts)}
  <div class="p4-g">
    <div class="p4-col"><div class="sh" style="margin-bottom:0">// ETFs &amp; Funds</div>{p4_etfs}</div>
    <div class="p4-col"><div class="sh" style="margin-bottom:0">// Fixed Income</div>{p4_bonds}</div>
    <div class="p4-col"><div class="sh" style="margin-bottom:0">// Commodities</div>{p4_cmds}</div>
  </div>
  {pf(4,T)}
</div><div class="pn">4/{T}</div></div>

<!-- ═══ P5: Market Movers ═══ -->
<div class="page"><div class="pi">
  {ph("Market Movers · Top 5 Gainers &amp; Top 5 Losers", ds, ts)}
  <div class="p5-g">
    <div style="overflow:hidden">
      <div class="sh" style="color:#16a34a;border-color:#0d2a1a;margin-bottom:5px">// Top Gainers</div>
      {gainers_html}
    </div>
    <div style="overflow:hidden">
      <div class="sh" style="color:#dc2626;border-color:#2a0d0d;margin-bottom:5px">// Top Losers</div>
      {losers_html}
    </div>
  </div>
  {pf(5,T)}
</div><div class="pn">5/{T}</div></div>

<!-- ═══ P6: Microstructure ═══ -->
<div class="page"><div class="pi">
  {ph("Market Microstructure · Spread · Liquidity · Volatility · Imbalance · OB Walls", ds, ts)}
  <div class="p6-g">
    <div class="micro-col"><div class="sh" style="margin-bottom:5px">// Tightest Spreads</div>{spread_html}</div>
    <div class="micro-col"><div class="sh" style="margin-bottom:5px">// Liquidity ↓</div>{liq_html}</div>
    <div class="micro-col"><div class="sh" style="margin-bottom:5px">// Volatility 7d ↓</div>{vol_html}</div>
    <div class="micro-col"><div class="sh" style="margin-bottom:5px">// OB Imbalance</div>{imb_html}</div>
    <div class="micro-col"><div class="sh" style="color:#16a34a;border-color:#0d2a1a;margin-bottom:5px">// Bid Walls</div>{bid_wall_html}</div>
    <div class="micro-col"><div class="sh" style="color:#dc2626;border-color:#2a0d0d;margin-bottom:5px">// Ask Walls</div>{ask_wall_html}</div>
  </div>
  {pf(6,T)}
</div><div class="pn">6/{T}</div></div>

<!-- ═══ P7: Volume Analysis ═══ -->
<div class="page"><div class="pi">
  {ph("Volume Analysis · 7-Day Activity", ds, ts)}
  <div class="p7-g">
    <div style="overflow:hidden">
      <div class="sh" style="margin-bottom:5px">// Total 7d Volume ↓</div>
      {vol_total_html}
    </div>
    <div style="overflow:hidden">
      <div class="sh" style="margin-bottom:5px">// Trade Count ↓</div>
      {vol_count_html}
    </div>
    <div style="overflow:hidden">
      <div class="sh" style="margin-bottom:5px">// Avg Trade Size ↓</div>
      {vol_avg_html}
    </div>
  </div>
  {pf(7,T)}
</div><div class="pn">7/{T}</div></div>

<!-- ═══ P8: Orderbook Snapshot ═══ -->
<div class="page"><div class="pi">
  {ph("Orderbook Snapshot · NER &amp; TSE", ds, ts)}
  <div class="p8-g">{ob_cards}</div>
  {pf(8,T)}
</div><div class="pn">8/{T}</div></div>

<!-- ═══ P9: Cross-Exchange + Closing ═══ -->
<div class="page"><div class="pi">
  {ph("Cross-Exchange Analytics · NER vs TSE · End of Report", ds, ts)}
  <div class="p9-g">
    <div style="overflow:hidden">{xs_ner}</div>
    <div style="overflow:hidden">{xs_tse}</div>
    <div style="display:flex;flex-direction:column;justify-content:space-between;overflow:hidden">
      <div>
        <div class="xs-head" style="color:#555">Combined Market</div>
        <div class="xs-row"><span class="xs-l">Total Securities</span><span class="xs-v">{len(all_secs)}</span></div>
        <div class="xs-row"><span class="xs-l">NER Securities</span><span class="xs-v">{len(ner_all)}</span></div>
        <div class="xs-row"><span class="xs-l">TSE Securities</span><span class="xs-v">{len(tse_all)}</span></div>
        <div class="xs-row"><span class="xs-l">Combined Mcap</span><span class="xs-v">{fmtbig(d.get("combined_mcap"))}</span></div>
        <div class="xs-row"><span class="xs-l">Market Avg Liq</span><span class="xs-v">{mkt_liq}</span></div>
        <div class="xs-row"><span class="xs-l">Market Avg Vol</span><span class="xs-v">{mkt_vol}</span></div>
        <div class="xs-row"><span class="xs-l">Total Frozen</span><span class="xs-v" style="{"color:#dc2626" if d["frozen_count"]>0 else ""}">{d["frozen_count"]}</span></div>
        {"<div class='xs-row'><span class='xs-l' style='color:#dc262699'>Halted</span><span class='xs-v' style='color:#dc262699;font-size:8px'>" + frz_warn_p9 + "</span></div>" if frz_warn_p9 else ""}
      </div>
      <div style="text-align:center;padding-bottom:8px">
        <div style="font-family:'IBM Plex Mono',monospace;font-size:8px;letter-spacing:.2em;text-transform:uppercase;color:#1a1a1a;margin-bottom:4px">End of Report</div>
        <div style="font-size:24px;font-weight:700;letter-spacing:-.02em;color:#222;line-height:1">Bloomberg<br>Labs</div>
        <div style="font-family:'IBM Plex Mono',monospace;font-size:8px;color:#1a1a1a;margin-top:6px">{ds} · CONFIDENTIAL</div>
      </div>
    </div>
  </div>
  {pf(9,T)}
</div><div class="pn">9/{T}</div></div>

</div></body></html>"""

# ─────────────────────────────────────────────────────────────────────────────
# INDEX PAGE
# ─────────────────────────────────────────────────────────────────────────────

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
.barf{height:100%;background:#16a34a;width:0;transition:width .3s ease}
.lg{font-family:'IBM Plex Mono',monospace;font-size:10px;display:flex;gap:8px;padding:2px 0}
.lg .ts{color:#2a2a2a;min-width:52px}
.ok{color:#16a34a}.er{color:#dc2626}.hi{color:#d97706}
.ft{margin-top:20px;display:flex;justify-content:space-between;font-family:'IBM Plex Mono',monospace;font-size:10px;color:#222}
</style></head><body>
<div class="w">
  <div class="ey">DemocracyCraft · NER &amp; TSE Exchange</div>
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
  <div class="ft"><span>Bloomberg Labs · DemocracyCraft · NER &amp; TSE</span><span id="clk"></span></div>
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

# ─────────────────────────────────────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(INDEX_HTML)

@app.route("/api/report")
def api_report():
    mode = request.args.get("mode","private")
    try:
        raw = fetch_all()
    except Exception as e:
        return f"Atlas error: {e}", 500

    securities = raw.get("securities",[])
    if not isinstance(securities,list): securities=[]
    history = raw.get("history",{})
    ob_raw  = raw.get("orderbook",[])

    # Pre-compute total market caps per exchange for dominance %
    def sum_mcap(exch):
        total = 0
        for s in securities:
            if s.get("hidden") or s["ticker"] in HIDDEN_TICKERS: continue
            if (is_tse(s["ticker"]) and exch=="TSE") or (not is_tse(s["ticker"]) and exch=="NER"):
                mc = s.get("market_cap")
                p  = s.get("market_price")
                sh = s.get("total_shares")
                if mc:   total += float(mc)
                elif p and sh: total += float(p)*float(sh)
        return total if total>0 else None

    ner_mcap_total = sum_mcap("NER")
    tse_mcap_total = sum_mcap("TSE")
    combined_mcap  = (ner_mcap_total+tse_mcap_total) if (ner_mcap_total and tse_mcap_total) else None

    processed = [process_sec(s, history, ner_mcap_total, tse_mcap_total) for s in securities]
    name_map  = {s["ticker"]: s.get("full_name", s["ticker"]) for s in securities}

    def by_cat(cat):
        return [p for s,p in zip(securities,processed)
                if classify(s)==cat and s["ticker"] not in HIDDEN_TICKERS and not s.get("hidden")]

    stocks      = by_cat("Stock")
    etfs        = by_cat("ETF")
    bonds       = by_cat("Bond")
    commodities = by_cat("Commodity")

    ner_indices, tse_indices = compute_indices(securities)

    orderbooks = []
    if isinstance(ob_raw,list):
        for book in ob_raw: orderbooks.append(process_ob(book,name_map))
    elif isinstance(ob_raw,dict):
        for ticker,book in ob_raw.items():
            if isinstance(book,dict):
                book["ticker"]=ticker; orderbooks.append(process_ob(book,name_map))
    orderbooks.sort(key=lambda x:(not x["bids"] and not x["asks"],x["ticker"]))

    visible = [s for s in securities if not s.get("hidden") and s["ticker"] not in HIDDEN_TICKERS]
    total   = len(visible)
    frozen  = len([s for s in visible if s.get("frozen")])

    liqs = [p["liq"]  for p in processed if p["liq"]  is not None]
    vols = [p["vol7"] for p in processed if p["vol7"] is not None]

    def idx_val(indices, ticker):
        i = next((x for x in indices if x["ticker"]==ticker),None)
        return i["value"] if i and i["value"] is not None else "—"

    now = datetime.now(timezone.utc)
    ctx = dict(
        date_str=now.strftime("%b. %d, %Y"), time_str=now.strftime("%H:%M:%S"),
        stocks=stocks, etfs=etfs, bonds=bonds, commodities=commodities,
        ner_indices=ner_indices, tse_indices=tse_indices,
        orderbooks=orderbooks,
        total_count=total, frozen_count=frozen, active_count=total-frozen,
        avg_liq=fmts(sum(liqs)/len(liqs)) if liqs else "—",
        avg_vol=fmts(sum(vols)/len(vols)) if vols else "—",
        ner_comp=idx_val(ner_indices,"B:COMP"), ner_stk=idx_val(ner_indices,"B:STK"),
        tse_comp=idx_val(tse_indices,"T:COMP"), tse_stk=idx_val(tse_indices,"T:STK"),
        ner_mcap_total=ner_mcap_total, tse_mcap_total=tse_mcap_total,
        combined_mcap=combined_mcap,
    )
    html = build_public(ctx) if mode=="public" else build_private(ctx)
    return html, 200, {"Content-Type":"text/html"}

@app.route("/debug")
def debug():
    try:
        ob   = atlas("/orderbook")
        h    = atlas("/history/BB?days=7&limit=5")
        secs = atlas("/securities?include_derived=true")
        ner_s = next((s for s in secs if not is_tse(s.get("ticker",""))),None)
        tse_s = next((s for s in secs if is_tse(s.get("ticker",""))),None)
        return {
            "total_securities": len(secs) if isinstance(secs,list) else "?",
            "ner_count": len([s for s in secs if not is_tse(s.get("ticker",""))]),
            "tse_count": len([s for s in secs if is_tse(s.get("ticker",""))]),
            "ob_len": len(ob) if isinstance(ob,list) else "?",
            "ob_sample": ob[0] if isinstance(ob,list) and ob else ob,
            "hist_sample": h, "ner_sample": ner_s, "tse_sample": tse_s,
        }
    except Exception as e:
        import traceback
        return {"error":str(e),"trace":traceback.format_exc()}, 500

@app.route("/health")
def health():
    return {"status":"ok"}

if __name__=="__main__":
    port = int(os.environ.get("PORT",5000))
    app.run(host="0.0.0.0",port=port,debug=False)
