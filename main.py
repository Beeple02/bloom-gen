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

def is_tse(ticker):
    return str(ticker).startswith("TSE:")

def classify_ner(ticker):
    if ticker in NER_BONDS:       return "Bond"
    if ticker in NER_ETFS:        return "ETF"
    if ticker in NER_COMMODITIES: return "Commodity"
    return "Stock"

def classify_tse(security_type):
    if not security_type: return "Stock"
    st = security_type.lower()
    if st in ("bond", "fixed income", "debt"):          return "Bond"
    if st in ("etf", "fund", "index fund"):             return "ETF"
    if st in ("commodity", "resource", "commodities"):  return "Commodity"
    return "Stock"

def classify(s):
    t = s["ticker"]
    if is_tse(t): return classify_tse(s.get("security_type"))
    return classify_ner(t)

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

def fmt_str(v):
    if v is None: return "—"
    return str(v)

def price_change(hist, current):
    if not hist or current is None: return None, None
    if isinstance(hist, dict): hist = hist.get("data", [])
    if not isinstance(hist, list) or not hist: return None, None
    prices = [float(e["price"]) for e in hist if isinstance(e, dict) and e.get("price") is not None]
    if not prices: return None, None
    prev = prices[-1]
    if prev == 0: return None, None
    chg = round(current - prev, 4)
    chg_pct = round((chg / prev) * 100, 2)
    return chg, chg_pct

def compute_indices(securities):
    ner = {"Stock":[], "ETF":[], "Bond":[], "Commodity":[], "All":[]}
    tse = {"Stock":[], "ETF":[], "Bond":[], "Commodity":[], "All":[]}
    for s in securities:
        if s.get("hidden") or s["ticker"] in HIDDEN_TICKERS: continue
        p = s.get("market_price")
        if p is None: continue
        cat = classify(s)
        bucket = tse if is_tse(s["ticker"]) else ner
        bucket[cat].append(float(p))
        bucket["All"].append(float(p))
    def avg(lst): return round(sum(lst)/len(lst), 4) if lst else None
    ner_idx = [
        {"ticker":"B:COMP",  "name":"NER Composite",   "value":avg(ner["All"]),       "desc":"All NER securities"},
        {"ticker":"B:STK",   "name":"NER Stocks",       "value":avg(ner["Stock"]),     "desc":"NER equity basket"},
        {"ticker":"B:ETF",   "name":"NER Funds",        "value":avg(ner["ETF"]),       "desc":"NER ETF basket"},
        {"ticker":"B:BOND",  "name":"NER Fixed Income", "value":avg(ner["Bond"]),      "desc":"NER bond basket"},
        {"ticker":"B:CMDTY", "name":"NER Commodities",  "value":avg(ner["Commodity"]), "desc":"NER commodity basket"},
    ]
    tse_idx = [
        {"ticker":"T:COMP",  "name":"TSE Composite",   "value":avg(tse["All"]),       "desc":"All TSE securities"},
        {"ticker":"T:STK",   "name":"TSE Stocks",       "value":avg(tse["Stock"]),     "desc":"TSE equity basket"},
        {"ticker":"T:ETF",   "name":"TSE Funds",        "value":avg(tse["ETF"]),       "desc":"TSE ETF basket"},
        {"ticker":"T:BOND",  "name":"TSE Fixed Income", "value":avg(tse["Bond"]),      "desc":"TSE bond basket"},
        {"ticker":"T:CMDTY", "name":"TSE Commodities",  "value":avg(tse["Commodity"]), "desc":"TSE commodity basket"},
    ]
    return ner_idx, tse_idx

def make_spark(prices, color, w=200, h=28):
    if len(prices) < 2: return ""
    mn, mx = min(prices), max(prices)
    rng = mx - mn if mx != mn else mn * 0.1 if mn else 1
    pad_v = rng * 0.3
    y_min = mn - pad_v; y_max = mx + pad_v; y_rng = y_max - y_min
    def px(i): return round(i / (len(prices)-1) * w, 1)
    def py(p): return round(h - ((p - y_min) / y_rng * h), 1)
    pts = [(px(i), py(p)) for i, p in enumerate(prices)]
    line = " ".join(f"{x},{y}" for x,y in pts)
    fill = line + f" {pts[-1][0]},{h} {pts[0][0]},{h}"
    uid = abs(hash(f"{color}{round(mn,2)}{len(prices)}")) % 99999
    return (
        f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" style="display:block;width:100%;height:100%" xmlns="http://www.w3.org/2000/svg">'
        f'<defs><linearGradient id="g{uid}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{color}" stop-opacity="0.15"/>'
        f'<stop offset="100%" stop-color="{color}" stop-opacity="0.0"/>'
        f'</linearGradient></defs>'
        f'<polygon points="{fill}" fill="url(#g{uid})"/>'
        f'<polyline points="{line}" fill="none" stroke="{color}" stroke-width="1.2" stroke-linejoin="round" stroke-linecap="round"/>'
        f'</svg>'
    )

def process_sec(s, history):
    t       = s["ticker"]
    price   = fmt(s.get("market_price"))
    derived = s.get("derived") or {}
    chg, chg_pct = price_change(history.get(t), price)
    cls = "up" if chg_pct and chg_pct > 0 else ("dn" if chg_pct and chg_pct < 0 else "fl")
    hist = history.get(t, {})
    if isinstance(hist, dict): hist = hist.get("data", [])
    prices_raw = [float(e["price"]) for e in reversed(hist) if isinstance(e, dict) and e.get("price") is not None]
    if price is not None: prices_raw.append(price)
    display_ticker = t.replace("TSE:", "") if is_tse(t) else t
    liq  = fmt(derived.get("liquidity_score"))
    vol7 = fmt(derived.get("volatility_7d"))
    imb  = fmt(derived.get("orderbook_imbalance"), 4)
    spread     = fmt(derived.get("spread"))
    spread_pct = fmt(derived.get("spread_pct"))
    return {
        "ticker":         t,
        "display_ticker": display_ticker,
        "exchange":       "TSE" if is_tse(t) else "NER",
        "name":           s.get("full_name", t),
        "price":          price if price is not None else None,
        "price_str":      str(price) if price is not None else "—",
        "frozen":         bool(s.get("frozen")),
        "shares":         f"{int(s['total_shares']):,}" if s.get("total_shares") else "—",
        "market_cap":     fmt(s.get("market_cap")),
        "vwap7":          fmt(derived.get("vwap_7d")),
        "vwap24":         fmt(derived.get("vwap_24h")),
        "vol7":           vol7,
        "liq":            liq,
        "spread":         spread,
        "spread_pct":     spread_pct,
        "imbalance":      imb,
        "chg":            chg,
        "chg_pct":        chg_pct,
        "cls":            cls,
        "prices":         prices_raw,
        "sec_type":       s.get("security_type") or "",
    }

def process_ob(book, name_map):
    ticker = book.get("ticker", "?")
    bids, asks = [], []
    for e in (book.get("bids") or [])[:4]:
        p = e.get("price"); q = e.get("quantity") or e.get("qty")
        if p is not None: bids.append({"price": fmt(p), "qty": int(q) if q else "?"})
    for e in (book.get("asks") or [])[:4]:
        p = e.get("price"); q = e.get("quantity") or e.get("qty")
        if p is not None: asks.append({"price": fmt(p), "qty": int(q) if q else "?"})
    spread = fmt(book.get("spread"))
    spread_pct = fmt(book.get("spread_pct"))
    if spread is None and bids and asks:
        bb = bids[0]["price"]; ba = asks[0]["price"]
        if bb and ba:
            spread = fmt(ba - bb)
            spread_pct = fmt(((ba-bb)/bb)*100) if bb else None
    display = ticker.replace("TSE:", "") if is_tse(ticker) else ticker
    return {
        "ticker": ticker, "display_ticker": display,
        "exchange": "TSE" if is_tse(ticker) else "NER",
        "name": name_map.get(ticker, display),
        "bids": bids, "asks": asks,
        "spread": spread, "spread_pct": spread_pct,
        "best_bid": fmt(book.get("best_bid")),
        "best_ask": fmt(book.get("best_ask")),
        "bid_depth": book.get("bid_depth", 0),
        "ask_depth": book.get("ask_depth", 0),
        "imbalance": fmt(book.get("imbalance"), 4),
    }

# ─────────────────────────────────────────────────────────────────────────────
# SHARED HTML COMPONENTS
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
.pi{flex:1;padding:22px 30px 16px;display:flex;flex-direction:column;overflow:hidden}
/* page header */
.ph{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;padding-bottom:10px;border-bottom:1px solid #1e1e1e;flex-shrink:0}
.ph-l{display:flex;align-items:center;gap:10px}
.ph-logo{font-family:'IBM Plex Mono',monospace;font-size:12px;font-weight:600;color:#fff}
.ph-pipe{width:1px;height:10px;background:#222}
.ph-sub{font-family:'IBM Plex Mono',monospace;font-size:9px;letter-spacing:.12em;text-transform:uppercase;color:#333}
.ph-r{display:flex;gap:12px;align-items:center}
.ph-date{font-family:'IBM Plex Mono',monospace;font-size:11px;color:#888}
.ph-time{font-family:'IBM Plex Mono',monospace;font-size:10px;color:#333}
/* page footer */
.pf{padding-top:7px;border-top:1px solid #161616;display:flex;justify-content:space-between;font-family:'IBM Plex Mono',monospace;font-size:9px;color:#1e1e1e;flex-shrink:0;margin-top:auto}
.pn{position:absolute;bottom:8px;right:12px;font-family:'IBM Plex Mono',monospace;font-size:9px;color:#1e1e1e}
/* section header */
.sh{font-family:'IBM Plex Mono',monospace;font-size:9px;letter-spacing:.16em;text-transform:uppercase;color:#555;padding-bottom:5px;border-bottom:1px solid #222;margin-bottom:0;flex-shrink:0}
/* exchange badges */
.exb{display:inline-block;font-family:'IBM Plex Mono',monospace;font-size:7px;font-weight:700;letter-spacing:.04em;padding:1px 4px;vertical-align:middle;margin-left:3px;border-radius:1px}
.exb.NER{background:#0d1f15;color:#16a34a;border:1px solid #16a34a33}
.exb.TSE{background:#0d1525;color:#3b82f6;border:1px solid #3b82f633}
/* security card */
.sc{padding:5px 0 4px;border-bottom:1px solid #191919}
.sc:last-child{border-bottom:none}
.sc-top{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:1px}
.sc-tk{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#555;letter-spacing:.04em}
.sc-px{font-family:'IBM Plex Mono',monospace;font-size:19px;font-weight:600;color:#fff;line-height:1}
.sc-nm{font-size:11px;font-weight:600;color:#888;margin-bottom:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sc-ch{font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:500;margin-bottom:2px}
.up{color:#16a34a}.dn{color:#dc2626}.fl{color:#333}
.sc-mt{display:flex;flex-wrap:wrap;gap:6px;margin-top:1px}
.sc-mi{font-family:'IBM Plex Mono',monospace;font-size:8.5px;color:#333}
.sc-mi span{color:#666}
.frz{display:inline-block;font-family:'IBM Plex Mono',monospace;font-size:7px;letter-spacing:.05em;text-transform:uppercase;border:1px solid #dc2626;color:#dc2626;padding:0 3px;margin-left:3px;vertical-align:middle}
.sc-spark{height:20px;margin-top:3px;overflow:hidden}
/* orderbook */
.ob-card{background:#0f0f0f;border:1px solid #1e1e1e;padding:10px 12px}
.ob-tk{font-family:'IBM Plex Mono',monospace;font-size:8px;color:#777;letter-spacing:.04em;margin-bottom:1px}
.ob-nm{font-size:10px;font-weight:600;color:#999;margin-bottom:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ob-cols{display:grid;grid-template-columns:1fr 1fr;gap:6px}
.ob-sl{font-family:'IBM Plex Mono',monospace;font-size:7px;letter-spacing:.1em;text-transform:uppercase;margin-bottom:2px}
.ob-sl.bid{color:#16a34a}.ob-sl.ask{color:#dc2626}
.ob-lv{font-family:'IBM Plex Mono',monospace;font-size:10px;display:flex;justify-content:space-between;padding:1px 0}
.ob-p{color:#ccc}.ob-q{color:#555}
.ob-em{font-family:'IBM Plex Mono',monospace;font-size:8px;color:#2a2a2a;font-style:italic}
.ob-sp{margin-top:5px;padding-top:4px;border-top:1px solid #1a1a1a;font-family:'IBM Plex Mono',monospace;font-size:8px;display:flex;justify-content:space-between;color:#444}
.spv{color:#666}.spv.w{color:#d97706}.spv.d{color:#dc2626}
.ob-depth{margin-top:3px;font-family:'IBM Plex Mono',monospace;font-size:8px;color:#333;display:flex;justify-content:space-between}
@media print{.toolbar{display:none}.pages{margin-top:0;padding:0;gap:0}.page{border:none;page-break-after:always}}
"""

def ph(title, date_str, time_str):
    return (f'<div class="ph"><div class="ph-l"><span class="ph-logo">BLOOMBERG LABS</span>'
            f'<span class="ph-pipe"></span><span class="ph-sub">{title}</span></div>'
            f'<div class="ph-r"><span class="ph-date">{date_str}</span>'
            f'<span class="ph-time">{time_str} UTC</span></div></div>')

def pf(n, t):
    return (f'<div class="pf">'
            f'<span>BLOOMBERG LABS · DEMOCRACYCRAFT · NER &amp; TSE · ATLAS MARKET INFRASTRUCTURE</span>'
            f'<span>PAGE {n} OF {t} · CONFIDENTIAL · NOT FOR DISTRIBUTION</span></div>')

def exb(exchange):
    return f'<span class="exb {exchange}">{exchange}</span>'

def sc_html(s, meta=True, spark=True):
    frz = '<span class="frz">FRZ</span>' if s["frozen"] else ""
    badge = exb(s["exchange"])
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
        m = (f'<div class="sc-mt">'
             f'<span class="sc-mi">VWAP7 <span>{fmt_str(s["vwap7"])}</span></span>'
             f'<span class="sc-mi">VOL <span>{fmt_str(s["vol7"])}</span></span>'
             f'<span class="sc-mi">LIQ <span>{fmt_str(s["liq"])}</span></span>'
             f'<span class="sc-mi">SHRS <span>{s["shares"]}</span></span>'
             f'</div>')
    sp = ""
    if spark and s.get("prices"):
        spark_color = "#16a34a" if s["cls"] == "up" else ("#dc2626" if s["cls"] == "dn" else "#555")
        svg = make_spark(s["prices"], spark_color)
        if svg: sp = f'<div class="sc-spark">{svg}</div>'
    return (f'<div class="sc">'
            f'<div class="sc-top"><span class="sc-tk">{s["display_ticker"]}{badge}{frz}</span>'
            f'<span class="sc-px">{s["price_str"]}</span></div>'
            f'<div class="sc-nm">{s["name"]}</div>'
            f'<div class="sc-ch {s["cls"]}">{chg_str}</div>'
            f'{m}{sp}</div>')

def ob_html(ob):
    badge = exb(ob["exchange"])
    bh = "".join(f'<div class="ob-lv"><span class="ob-p">{lv["price"]}</span><span class="ob-q">×{lv["qty"]}</span></div>' for lv in ob["bids"]) or '<div class="ob-em">no bids</div>'
    ah = "".join(f'<div class="ob-lv"><span class="ob-p">{lv["price"]}</span><span class="ob-q">×{lv["qty"]}</span></div>' for lv in ob["asks"]) or '<div class="ob-em">no asks</div>'
    sp = ""
    if ob["spread"] is not None:
        cls = "d" if ob["spread_pct"] and ob["spread_pct"] > 25 else ("w" if ob["spread_pct"] and ob["spread_pct"] > 10 else "")
        sp = f'<div class="ob-sp"><span>Spread</span><span class="spv {cls}">{ob["spread"]} ({ob["spread_pct"]}%)</span></div>'
    depth = (f'<div class="ob-depth"><span>Bid {ob["bid_depth"]:,}</span>'
             f'<span>Ask {ob["ask_depth"]:,}</span></div>') if ob.get("bid_depth") is not None else ""
    return (f'<div class="ob-card">'
            f'<div class="ob-tk">{ob["display_ticker"]} {badge}</div>'
            f'<div class="ob-nm">{ob["name"]}</div>'
            f'<div class="ob-cols"><div><div class="ob-sl bid">Bids</div>{bh}</div>'
            f'<div><div class="ob-sl ask">Asks</div>{ah}</div></div>'
            f'{sp}{depth}</div>')

def toolbar(label):
    return (f'<div class="toolbar">'
            f'<button class="tbtn p" onclick="window.print()">⎙ PDF</button>'
            f'<button class="tbtn" onclick="window.close()">← Back</button>'
            f'<span class="tsp"></span><span class="th">{label}</span></div>')

# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC REPORT (1 page)
# ─────────────────────────────────────────────────────────────────────────────

def build_public(ctx):
    d = ctx
    all_secs = d["stocks"] + d["etfs"] + d["bonds"] + d["commodities"]
    movers = sorted([s for s in all_secs if s["chg_pct"] is not None],
                    key=lambda x: abs(x["chg_pct"]), reverse=True)[:3]

    stats_html = ""
    for label, val, sub, col in [
        ("NER Composite", d["ner_comp"],  "Equal-weighted avg",  ""),
        ("TSE Composite", d["tse_comp"],  "Equal-weighted avg",  "color:#3b82f6"),
        ("NER Stocks",    d["ner_stk"],   "NER equity basket",   ""),
        ("TSE Stocks",    d["tse_stk"],   "TSE equity basket",   "color:#3b82f6"),
        ("Avg Liquidity", d["avg_liq"],   "Market liquidity",    ""),
        ("Frozen",        str(d["frozen_count"]), "Halted",
         "color:#dc2626" if d["frozen_count"] > 0 else "color:#16a34a"),
    ]:
        stats_html += (f'<div class="sb"><div class="sb-l">{label}</div>'
                       f'<div class="sb-v" style="{col}">{val}</div>'
                       f'<div class="sb-s">{sub}</div></div>')

    movers_html = ""
    for s in movers:
        sign  = "+" if s["chg_pct"] > 0 else ""
        arrow = "▲" if s["chg_pct"] > 0 else "▼"
        col   = "#16a34a" if s["chg_pct"] > 0 else "#dc2626"
        sp    = make_spark(s.get("prices", []), col, w=300, h=34)
        badge_html = (f'<span style="font-family:IBM Plex Mono,monospace;font-size:7px;'
                      f'padding:1px 4px;border-radius:1px;margin-left:4px;'
                      f'background:{"#0d1525" if s["exchange"]=="TSE" else "#0d1f15"};'
                      f'color:{"#3b82f6" if s["exchange"]=="TSE" else "#16a34a"}">{s["exchange"]}</span>')
        movers_html += (f'<div class="mv"><div class="mv-row1">'
                        f'<span class="mv-tk">{s["display_ticker"]}{badge_html}</span>'
                        f'<span class="mv-px">{s["price_str"]}</span>'
                        f'<span class="mv-ch" style="color:{col}">{arrow} {sign}{s["chg_pct"]}%</span></div>'
                        f'<div class="mv-nm">{s["name"]}</div>{sp}</div>')

    if not movers_html:
        movers_html = '<div style="font-family:IBM Plex Mono,monospace;font-size:10px;color:#333;padding:10px 0">No price change data available</div>'

    # indices: NER left, TSE right, alternating
    idx_html = ""
    for ni, ti in zip(d["ner_indices"], d["tse_indices"]):
        nv = ni["value"] if ni["value"] is not None else "—"
        tv = ti["value"] if ti["value"] is not None else "—"
        idx_html += (f'<div class="ir">'
                     f'<span class="ir-tk">{ni["ticker"]}</span>'
                     f'<span class="ir-v">{nv}</span></div>'
                     f'<div class="ir" style="border-left:1px solid #1a2a3a">'
                     f'<span class="ir-tk" style="color:#3b82f6">{ti["ticker"]}</span>'
                     f'<span class="ir-v" style="color:#3b82f6">{tv}</span></div>')

    frozen_warn = ""
    frz_list = [s for s in all_secs if s["frozen"]]
    if frz_list:
        tickers_str = " · ".join(s["display_ticker"] for s in frz_list)
        frozen_warn = (f'<div style="background:#150000;border:1px solid #dc262633;padding:5px 12px;'
                       f'font-family:IBM Plex Mono,monospace;font-size:9px;color:#dc2626;'
                       f'letter-spacing:.05em;flex-shrink:0;margin-top:6px">'
                       f'⚠ TRADING HALTED: {tickers_str}</div>')

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<title>Bloomberg Labs — {d['date_str']}</title>{FONTS}
<style>
{BASE_CSS}
.pub-wrap{{display:flex;justify-content:center}}
.pub-inner{{width:1280px;height:720px;background:#111;display:flex;flex-direction:column;padding:18px 26px 14px;overflow:hidden;border:1px solid #1e1e1e}}
.sbar{{display:grid;grid-template-columns:repeat(6,1fr);gap:1px;background:#1a1a1a;border:1px solid #1a1a1a;margin-bottom:12px;flex-shrink:0}}
.sb{{background:#141414;padding:7px 11px}}
.sb-l{{font-family:'IBM Plex Mono',monospace;font-size:8px;letter-spacing:.1em;text-transform:uppercase;color:#3a3a3a;margin-bottom:3px}}
.sb-v{{font-family:'IBM Plex Mono',monospace;font-size:15px;font-weight:600;color:#fff}}
.sb-s{{font-family:'IBM Plex Mono',monospace;font-size:7px;color:#2a2a2a;margin-top:1px}}
.pub-body{{flex:1;display:grid;grid-template-columns:200px 1fr 210px;gap:18px;overflow:hidden;min-height:0}}
.hero-tag{{font-family:'IBM Plex Mono',monospace;font-size:8px;letter-spacing:.14em;text-transform:uppercase;color:#333;margin-bottom:6px}}
.hero-title{{font-size:52px;font-weight:700;letter-spacing:-.03em;line-height:.9;color:#fff;margin-bottom:10px}}
.hero-sub{{font-family:'IBM Plex Mono',monospace;font-size:8px;color:#333;line-height:2.0}}
.mv-col{{overflow:hidden;display:flex;flex-direction:column;gap:0}}
.mv{{padding:7px 0;border-bottom:1px solid #171717}}
.mv:last-child{{border-bottom:none}}
.mv-row1{{display:flex;align-items:baseline;gap:6px;margin-bottom:2px}}
.mv-tk{{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#777}}
.mv-px{{font-family:'IBM Plex Mono',monospace;font-size:17px;font-weight:600;color:#fff;margin-left:auto}}
.mv-ch{{font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600}}
.mv-nm{{font-size:10px;font-weight:600;color:#555;margin-bottom:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.idx-col{{overflow:hidden;display:flex;flex-direction:column}}
.ir{{display:flex;justify-content:space-between;align-items:baseline;padding:4px 6px;border-bottom:1px solid #141414}}
.ir:last-child{{border-bottom:none}}
.ir-tk{{font-family:'IBM Plex Mono',monospace;font-size:8px;color:#444;letter-spacing:.05em}}
.ir-v{{font-family:'IBM Plex Mono',monospace;font-size:13px;font-weight:600;color:#fff}}
.pub-footer{{padding-top:6px;border-top:1px solid #1a1a1a;display:flex;justify-content:space-between;font-family:'IBM Plex Mono',monospace;font-size:8px;color:#2a2a2a;flex-shrink:0}}
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
      <div class="hero-sub">// {d['date_str']}<br>// {d['time_str']} UTC<br>// {d['active_count']} Active · {d['frozen_count']} Frozen<br>// {d['total_count']} Securities Listed</div>
    </div>
    <div class="mv-col">
      <div class="sh" style="margin-bottom:6px">// Top Movers</div>
      {movers_html}
    </div>
    <div class="idx-col">
      <div class="sh" style="margin-bottom:4px">// Bloomberg Indices</div>
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
# PRIVATE REPORT (9 pages)
# ─────────────────────────────────────────────────────────────────────────────

def build_private(ctx):
    d   = ctx
    ds  = d["date_str"]
    ts  = d["time_str"]
    T   = 9  # total pages

    def chunk(lst, n):
        if not lst: return [[] for _ in range(n)]
        k = max(1, (len(lst)+n-1)//n)
        return [lst[i*k:(i+1)*k] for i in range(n)]

    # ── PAGE 1: Cover + Stats + Indices ──────────────────────────────────────
    stats_html = ""
    for label, val, sub, col in [
        ("NER Composite", d["ner_comp"],  "Equal-weighted avg",  ""),
        ("TSE Composite", d["tse_comp"],  "Equal-weighted avg",  "color:#3b82f6"),
        ("NER Stocks",    d["ner_stk"],   "NER equity basket",   ""),
        ("TSE Stocks",    d["tse_stk"],   "TSE equity basket",   "color:#3b82f6"),
        ("Avg Liquidity", d["avg_liq"],   "Market liquidity",    ""),
        ("Frozen",        str(d["frozen_count"]), "Trading halted",
         "color:#dc2626" if d["frozen_count"] > 0 else "color:#16a34a"),
    ]:
        stats_html += (f'<div class="hs"><div class="hs-l">{label}</div>'
                       f'<div class="hs-v" style="{col}">{val}</div>'
                       f'<div class="hs-s">{sub}</div></div>')

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
                         f'<div class="ir2-r">'
                         f'<div class="ir2-v" style="color:#3b82f6;font-size:18px">{v}</div>'
                         f'<div class="ir2-d">{i["desc"]}</div></div></div>')

    # ── PAGE 2: NER Stocks ────────────────────────────────────────────────────
    ner_stocks = [s for s in d["stocks"] if s["exchange"] == "NER"]
    ner_cols   = chunk(ner_stocks, 3)
    p2_html    = "".join(f'<div>{"".join(sc_html(s) for s in col)}</div>' for col in ner_cols)

    # ── PAGE 3: TSE Stocks (bigger cards, 3 cols) ─────────────────────────────
    tse_stocks = [s for s in d["stocks"] if s["exchange"] == "TSE"]
    tse_cols   = chunk(tse_stocks, 3)
    p3_html    = "".join(f'<div>{"".join(sc_html(s) for s in col)}</div>' for col in tse_cols)

    # ── PAGE 4: ETFs + Bonds + Commodities (NER + TSE mixed) ─────────────────
    ner_etfs  = [s for s in d["etfs"]        if s["exchange"] == "NER"]
    tse_etfs  = [s for s in d["etfs"]        if s["exchange"] == "TSE"]
    ner_bonds = [s for s in d["bonds"]       if s["exchange"] == "NER"]
    tse_bonds = [s for s in d["bonds"]       if s["exchange"] == "TSE"]
    ner_cmds  = [s for s in d["commodities"] if s["exchange"] == "NER"]
    tse_cmds  = [s for s in d["commodities"] if s["exchange"] == "TSE"]

    p4_etfs  = "".join(sc_html(s) for s in ner_etfs + tse_etfs)
    p4_bonds = "".join(sc_html(s) for s in ner_bonds + tse_bonds)
    p4_cmds  = "".join(sc_html(s, meta=False) for s in ner_cmds + tse_cmds)
    if not p4_etfs:  p4_etfs  = '<div class="sc-empty">No data</div>'
    if not p4_bonds: p4_bonds = '<div class="sc-empty">No data</div>'
    if not p4_cmds:  p4_cmds  = '<div class="sc-empty">No data</div>'

    # ── PAGE 5: Market Movers ─────────────────────────────────────────────────
    all_secs = d["stocks"] + d["etfs"] + d["bonds"] + d["commodities"]
    with_chg = [s for s in all_secs if s["chg_pct"] is not None]
    gainers  = sorted(with_chg, key=lambda x: x["chg_pct"], reverse=True)[:5]
    losers   = sorted(with_chg, key=lambda x: x["chg_pct"])[:5]

    def mover_row(s, rank):
        sign  = "+" if s["chg_pct"] > 0 else ""
        col   = "#16a34a" if s["chg_pct"] > 0 else "#dc2626"
        arrow = "▲" if s["chg_pct"] > 0 else "▼"
        sp    = make_spark(s.get("prices", []), col, w=160, h=24)
        sp_html = f'<div style="width:160px;height:24px;flex-shrink:0">{sp}</div>' if sp else '<div style="width:160px"></div>'
        return (f'<div class="mrow">'
                f'<div class="mrow-rank" style="color:{col}">#{rank}</div>'
                f'<div class="mrow-tk">{s["display_ticker"]}{exb(s["exchange"])}</div>'
                f'<div class="mrow-nm">{s["name"]}</div>'
                f'<div class="mrow-px">{s["price_str"]}</div>'
                f'<div class="mrow-ch" style="color:{col}">{arrow} {sign}{s["chg_pct"]}%</div>'
                f'<div class="mrow-abs" style="color:{col}">{sign}{s["chg"] if s["chg"] is not None else "—"}</div>'
                f'<div class="mrow-meta">LIQ {fmt_str(s["liq"])} · VOL {fmt_str(s["vol7"])}</div>'
                f'{sp_html}</div>')

    gainers_html = "".join(mover_row(s, i+1) for i, s in enumerate(gainers))
    losers_html  = "".join(mover_row(s, i+1) for i, s in enumerate(losers))
    if not gainers_html: gainers_html = '<div class="mrow-empty">No data</div>'
    if not losers_html:  losers_html  = '<div class="mrow-empty">No data</div>'

    # ── PAGE 6: Microstructure ────────────────────────────────────────────────
    # Spread ranking, liquidity ranking, imbalance
    has_spread = sorted([s for s in all_secs if s.get("spread_pct") is not None],
                        key=lambda x: x["spread_pct"])
    by_liq     = sorted([s for s in all_secs if s["liq"] is not None],
                        key=lambda x: x["liq"], reverse=True)
    by_vol     = sorted([s for s in all_secs if s["vol7"] is not None],
                        key=lambda x: x["vol7"], reverse=True)
    by_imb     = sorted([s for s in all_secs if s.get("imbalance") is not None],
                        key=lambda x: abs(x["imbalance"]), reverse=True)

    def micro_row(s, val, col=""):
        return (f'<div class="mr">'
                f'<span class="mr-tk">{s["display_ticker"]}{exb(s["exchange"])}</span>'
                f'<span class="mr-nm">{s["name"]}</span>'
                f'<span class="mr-v" style="{col}">{val}</span>'
                f'</div>')

    spread_html = "".join(micro_row(s, f'{s["spread_pct"]}%') for s in has_spread[:8]) or '<div class="mr-empty">—</div>'
    liq_html    = "".join(micro_row(s, fmt_str(s["liq"])) for s in by_liq[:8])         or '<div class="mr-empty">—</div>'
    vol_html    = "".join(micro_row(s, fmt_str(s["vol7"])) for s in by_vol[:8])         or '<div class="mr-empty">—</div>'
    imb_rows    = []
    for s in by_imb[:8]:
        imb_v = s["imbalance"]
        col   = "color:#16a34a" if imb_v > 0 else ("color:#dc2626" if imb_v < 0 else "")
        sign  = "+" if imb_v > 0 else ""
        imb_rows.append(micro_row(s, f'{sign}{imb_v}', col))
    imb_html = "".join(imb_rows) or '<div class="mr-empty">—</div>'

    # ── PAGE 7: Orderbook Snapshot ────────────────────────────────────────────
    # Fit all OB cards in a scrollable 4-col grid, capped cards
    ob_cards = "".join(ob_html(ob) for ob in d["orderbooks"])
    if not ob_cards:
        ob_cards = '<div style="font-family:IBM Plex Mono,monospace;font-size:11px;color:#2a2a2a;padding:20px">No orderbook data available</div>'

    # ── PAGE 8: Cross-exchange Summary ────────────────────────────────────────
    ner_all  = [s for s in all_secs if s["exchange"] == "NER"]
    tse_all  = [s for s in all_secs if s["exchange"] == "TSE"]

    def exch_stats(lst, label, color):
        if not lst: return ""
        liqs   = [s["liq"]  for s in lst if s["liq"]  is not None]
        vols   = [s["vol7"] for s in lst if s["vol7"] is not None]
        prices = [s["price"] for s in lst if s["price"] is not None]
        chgs   = [s["chg_pct"] for s in lst if s["chg_pct"] is not None]
        avg_l  = fmt(sum(liqs)/len(liqs))   if liqs   else "—"
        avg_v  = fmt(sum(vols)/len(vols))   if vols   else "—"
        avg_p  = fmt(sum(prices)/len(prices)) if prices else "—"
        avg_c  = fmt(sum(chgs)/len(chgs))   if chgs   else "—"
        top_g  = max(lst, key=lambda x: x["chg_pct"] if x["chg_pct"] is not None else -999)
        top_l  = min(lst, key=lambda x: x["chg_pct"] if x["chg_pct"] is not None else 999)
        frz    = sum(1 for s in lst if s["frozen"])
        rows = [
            ("Securities",    len(lst),  ""),
            ("Avg Price",     avg_p,     ""),
            ("Avg 7d Change", f'{("+" if avg_c != "—" and avg_c > 0 else "")}{avg_c}%' if avg_c != "—" else "—",
             f'color:{"#16a34a" if avg_c != "—" and avg_c > 0 else "#dc2626"}'),
            ("Avg Liquidity", avg_l,     ""),
            ("Avg Volatility",avg_v,     ""),
            ("Frozen",        frz,       "color:#dc2626" if frz > 0 else "color:#16a34a"),
            ("Top Gainer",    f'{top_g["display_ticker"]} +{top_g["chg_pct"]}%' if top_g["chg_pct"] else "—", "color:#16a34a"),
            ("Top Loser",     f'{top_l["display_ticker"]} {top_l["chg_pct"]}%'  if top_l["chg_pct"] else "—", "color:#dc2626"),
        ]
        html = (f'<div class="xs-head" style="color:{color}">{label}</div>')
        for rlab, rval, rcol in rows:
            html += (f'<div class="xs-row"><span class="xs-l">{rlab}</span>'
                     f'<span class="xs-v" style="{rcol}">{rval}</span></div>')
        return html

    xs_ner = exch_stats(ner_all, "NER Exchange", "#fff")
    xs_tse = exch_stats(tse_all, "TSE", "#3b82f6")

    # Combined market metrics
    all_liqs = [s["liq"]  for s in all_secs if s["liq"]  is not None]
    all_vols = [s["vol7"] for s in all_secs if s["vol7"] is not None]
    mkt_liq  = fmt(sum(all_liqs)/len(all_liqs)) if all_liqs else "—"
    mkt_vol  = fmt(sum(all_vols)/len(all_vols)) if all_vols else "—"
    mkt_frz  = sum(1 for s in all_secs if s["frozen"])
    mkt_secs = len(all_secs)
    combined_stats = [
        ("Total Securities", mkt_secs),
        ("NER Securities",   len(ner_all)),
        ("TSE Securities",   len(tse_all)),
        ("Market Avg Liq",   mkt_liq),
        ("Market Avg Vol",   mkt_vol),
        ("Total Frozen",     mkt_frz),
    ]
    comb_html = "".join(
        f'<div class="xs-row"><span class="xs-l">{l}</span><span class="xs-v">{v}</span></div>'
        for l, v in combined_stats
    )

    # price distribution sparkline across all securities
    all_prices = sorted([s["price"] for s in all_secs if s["price"] is not None])
    dist_spark = make_spark(all_prices, "#888", w=300, h=40) if len(all_prices) > 1 else ""

    # ── PAGE 9: Closing Slate ─────────────────────────────────────────────────
    frz_warn_p9 = ""
    frz_list = [s for s in all_secs if s["frozen"]]
    if frz_list:
        frz_warn_p9 = " · ".join(f'{s["display_ticker"]} ({s["exchange"]})' for s in frz_list)

    # ─────────────────────────────────────────────────────────────────────────
    # RENDER
    # ─────────────────────────────────────────────────────────────────────────
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<title>Bloomberg Labs Full Report — {ds}</title>{FONTS}
<style>
{BASE_CSS}
/* ── P1 ── */
.p1-g{{flex:1;display:grid;grid-template-columns:1fr 1fr 1fr;gap:22px;overflow:hidden}}
.hero-title{{font-size:62px;font-weight:700;letter-spacing:-.03em;line-height:.88;color:#fff;margin-bottom:10px}}
.hero-tag{{font-family:'IBM Plex Mono',monospace;font-size:8px;letter-spacing:.16em;text-transform:uppercase;color:#333;margin-bottom:7px}}
.hero-sub{{font-family:'IBM Plex Mono',monospace;font-size:8.5px;color:#333;line-height:2.0}}
.hs-grid{{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:#1a1a1a;border:1px solid #1a1a1a;margin-top:auto}}
.hs{{background:#0f0f0f;padding:9px 13px}}
.hs-l{{font-family:'IBM Plex Mono',monospace;font-size:8px;letter-spacing:.1em;text-transform:uppercase;color:#333;margin-bottom:3px}}
.hs-v{{font-family:'IBM Plex Mono',monospace;font-size:17px;font-weight:600;color:#fff}}
.hs-s{{font-family:'IBM Plex Mono',monospace;font-size:8px;color:#2a2a2a;margin-top:1px}}
.ir2{{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid #141414}}
.ir2:last-child{{border-bottom:none}}
.ir2-tk{{font-family:'IBM Plex Mono',monospace;font-size:8px;color:#444;letter-spacing:.07em;margin-bottom:1px}}
.ir2-nm{{font-size:12px;font-weight:600;color:#bbb}}
.ir2-r{{text-align:right}}
.ir2-v{{font-family:'IBM Plex Mono',monospace;font-size:20px;font-weight:600;color:#fff}}
.ir2-d{{font-family:'IBM Plex Mono',monospace;font-size:8px;color:#333;margin-top:1px}}
/* ── P2/P3 ── */
.p23-g{{flex:1;display:grid;grid-template-columns:1fr 1fr 1fr;gap:18px;overflow:hidden}}
.sc-empty{{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#2a2a2a;padding:10px 0}}
/* ── P4 ── */
.p4-g{{flex:1;display:grid;grid-template-columns:1fr 1fr 1fr;gap:18px;overflow:hidden}}
.p4-col{{display:flex;flex-direction:column;overflow:hidden}}
/* ── P5 movers ── */
.p5-g{{flex:1;display:grid;grid-template-columns:1fr 1fr;gap:20px;overflow:hidden}}
.mrow{{display:grid;grid-template-columns:24px 52px 1fr 60px 72px 50px 1fr 160px;align-items:center;gap:6px;padding:6px 0;border-bottom:1px solid #161616}}
.mrow:last-child{{border-bottom:none}}
.mrow-rank{{font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:700}}
.mrow-tk{{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#888}}
.mrow-nm{{font-size:10px;color:#666;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.mrow-px{{font-family:'IBM Plex Mono',monospace;font-size:12px;font-weight:600;color:#fff;text-align:right}}
.mrow-ch{{font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;text-align:right}}
.mrow-abs{{font-family:'IBM Plex Mono',monospace;font-size:10px;text-align:right}}
.mrow-meta{{font-family:'IBM Plex Mono',monospace;font-size:8px;color:#333;padding-left:4px}}
.mrow-empty{{font-family:'IBM Plex Mono',monospace;font-size:10px;color:#2a2a2a;padding:12px 0}}
/* ── P6 microstructure ── */
.p6-g{{flex:1;display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:16px;overflow:hidden}}
.micro-col{{display:flex;flex-direction:column;overflow:hidden}}
.mr{{display:flex;align-items:center;padding:4px 0;border-bottom:1px solid #141414;gap:6px}}
.mr:last-child{{border-bottom:none}}
.mr-tk{{font-family:'IBM Plex Mono',monospace;font-size:8.5px;color:#777;flex-shrink:0;width:36px}}
.mr-nm{{font-size:9px;color:#444;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.mr-v{{font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;color:#aaa;flex-shrink:0;text-align:right;min-width:50px}}
.mr-empty{{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#222;padding:8px 0}}
/* ── P7 orderbook ── */
.p7-g{{flex:1;display:grid;grid-template-columns:repeat(4,1fr);gap:8px;overflow:hidden;align-content:start}}
/* ── P8 cross-exchange ── */
.p8-g{{flex:1;display:grid;grid-template-columns:1fr 1fr 1fr;gap:20px;overflow:hidden}}
.xs-head{{font-family:'IBM Plex Mono',monospace;font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;padding-bottom:8px;border-bottom:1px solid #1e1e1e;margin-bottom:4px}}
.xs-row{{display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid #141414}}
.xs-row:last-child{{border-bottom:none}}
.xs-l{{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#444}}
.xs-v{{font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;color:#aaa}}
/* ── P9 closing ── */
.p9-inner{{flex:1;display:flex;flex-direction:column;justify-content:center;align-items:center;text-align:center;gap:0}}
</style></head><body>
{toolbar(f"FULL REPORT · Bloomberg Discord · {T} pages")}
<div class="pages">

<!-- ═══ PAGE 1: Cover ═══ -->
<div class="page"><div class="pi">
  {ph("Daily Market Recap · NER &amp; TSE Cover", ds, ts)}
  <div class="p1-g">
    <div style="display:flex;flex-direction:column;justify-content:space-between">
      <div>
        <div class="hero-tag">NER &amp; TSE · Bloomberg Labs</div>
        <div class="hero-title">Market<br>Recap</div>
        <div class="hero-sub">// {ds}<br>// {ts} UTC<br>// {d['active_count']} Active · {d['frozen_count']} Frozen · {d['total_count']} Listed<br>// NER Exchange + The Stock Exchange</div>
      </div>
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

<!-- ═══ PAGE 2: NER Stocks ═══ -->
<div class="page"><div class="pi">
  {ph("Securities · NER Stocks", ds, ts)}
  <div class="p23-g">{p2_html if p2_html.strip() else '<div class="sc-empty" style="grid-column:1/-1">No NER stocks available</div>'}</div>
  {pf(2,T)}
</div><div class="pn">2/{T}</div></div>

<!-- ═══ PAGE 3: TSE Stocks ═══ -->
<div class="page"><div class="pi">
  {ph("Securities · TSE Stocks", ds, ts)}
  <div class="p23-g">{p3_html if p3_html.strip() else '<div class="sc-empty" style="grid-column:1/-1">No TSE stocks available</div>'}</div>
  {pf(3,T)}
</div><div class="pn">3/{T}</div></div>

<!-- ═══ PAGE 4: ETFs / Bonds / Commodities ═══ -->
<div class="page"><div class="pi">
  {ph("Securities · Funds · Fixed Income · Commodities", ds, ts)}
  <div class="p4-g">
    <div class="p4-col"><div class="sh" style="margin-bottom:0">// ETFs &amp; Funds</div>{p4_etfs}</div>
    <div class="p4-col"><div class="sh" style="margin-bottom:0">// Fixed Income</div>{p4_bonds}</div>
    <div class="p4-col"><div class="sh" style="margin-bottom:0">// Commodities</div>{p4_cmds}</div>
  </div>
  {pf(4,T)}
</div><div class="pn">4/{T}</div></div>

<!-- ═══ PAGE 5: Market Movers ═══ -->
<div class="page"><div class="pi">
  {ph("Market Movers · Top Gainers &amp; Losers", ds, ts)}
  <div class="p5-g">
    <div style="overflow:hidden">
      <div class="sh" style="color:#16a34a;border-color:#0d2a1a;margin-bottom:6px">// Top Gainers</div>
      {gainers_html}
    </div>
    <div style="overflow:hidden">
      <div class="sh" style="color:#dc2626;border-color:#2a0d0d;margin-bottom:6px">// Top Losers</div>
      {losers_html}
    </div>
  </div>
  {pf(5,T)}
</div><div class="pn">5/{T}</div></div>

<!-- ═══ PAGE 6: Market Microstructure ═══ -->
<div class="page"><div class="pi">
  {ph("Market Microstructure · Spread · Liquidity · Volatility · Imbalance", ds, ts)}
  <div class="p6-g">
    <div class="micro-col">
      <div class="sh" style="margin-bottom:6px">// Tightest Spreads</div>
      {spread_html}
    </div>
    <div class="micro-col">
      <div class="sh" style="margin-bottom:6px">// Liquidity Score ↓</div>
      {liq_html}
    </div>
    <div class="micro-col">
      <div class="sh" style="margin-bottom:6px">// Volatility 7d ↓</div>
      {vol_html}
    </div>
    <div class="micro-col">
      <div class="sh" style="margin-bottom:6px">// OB Imbalance</div>
      {imb_html}
    </div>
  </div>
  {pf(6,T)}
</div><div class="pn">6/{T}</div></div>

<!-- ═══ PAGE 7: Orderbook Snapshot ═══ -->
<div class="page"><div class="pi">
  {ph("Orderbook Snapshot · NER &amp; TSE", ds, ts)}
  <div class="p7-g">{ob_cards}</div>
  {pf(7,T)}
</div><div class="pn">7/{T}</div></div>

<!-- ═══ PAGE 8: Cross-Exchange Summary ═══ -->
<div class="page"><div class="pi">
  {ph("Cross-Exchange Analytics · NER vs TSE", ds, ts)}
  <div class="p8-g">
    <div style="overflow:hidden">{xs_ner}</div>
    <div style="overflow:hidden">{xs_tse}</div>
    <div style="overflow:hidden">
      <div class="xs-head" style="color:#888">Combined Market</div>
      {comb_html}
      {"<div style='margin-top:12px'><div class='sh' style='margin-bottom:6px'>// Price Distribution</div><div style='height:40px'>" + dist_spark + "</div></div>" if dist_spark else ""}
    </div>
  </div>
  {pf(8,T)}
</div><div class="pn">8/{T}</div></div>

<!-- ═══ PAGE 9: End of Report ═══ -->
<div class="page"><div class="pi">
  {ph("End of Report", ds, ts)}
  <div class="p9-inner">
    <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;letter-spacing:.3em;text-transform:uppercase;color:#222;margin-bottom:32px">Bloomberg Labs · DemocracyCraft</div>
    <div style="font-size:72px;font-weight:700;letter-spacing:-.04em;color:#fff;line-height:1;margin-bottom:8px">End of<br>Report</div>
    <div style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:#333;margin-top:16px;letter-spacing:.06em">{ds} · {ts} UTC</div>
    <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;color:#1e1e1e;margin-top:8px">
      {d['total_count']} SECURITIES SURVEYED · NER EXCHANGE + THE STOCK EXCHANGE
    </div>
    {"<div style='font-family:IBM Plex Mono,monospace;font-size:9px;color:#dc262666;margin-top:16px;letter-spacing:.05em'>⚠ TRADING HALTED: " + frz_warn_p9 + "</div>" if frz_warn_p9 else ""}
    <div style="position:absolute;bottom:32px;left:50%;transform:translateX(-50%);font-family:'IBM Plex Mono',monospace;font-size:9px;color:#1e1e1e;white-space:nowrap;letter-spacing:.08em">
      BLOOMBERG LABS · CONFIDENTIAL · NOT FOR DISTRIBUTION · ATLAS MARKET INFRASTRUCTURE
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
.barf{height:100%;background:#16a34a;width:0;transition:width .3s ease;box-shadow:0 0 5px #16a34a}
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
        return [p for s, p in zip(securities, processed)
                if classify(s) == cat
                and s["ticker"] not in HIDDEN_TICKERS
                and not s.get("hidden")]

    stocks      = by_cat("Stock")
    etfs        = by_cat("ETF")
    bonds       = by_cat("Bond")
    commodities = by_cat("Commodity")

    ner_indices, tse_indices = compute_indices(securities)

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

    visible = [s for s in securities if not s.get("hidden") and s["ticker"] not in HIDDEN_TICKERS]
    total   = len(visible)
    frozen  = len([s for s in visible if s.get("frozen")])
    active  = total - frozen

    liqs = [p["liq"]  for p in processed if p["liq"]  is not None]
    vols = [p["vol7"] for p in processed if p["vol7"] is not None]
    avg_liq = fmt(sum(liqs)/len(liqs)) if liqs else "—"
    avg_vol = fmt(sum(vols)/len(vols)) if vols else "—"

    def idx_val(indices, ticker):
        i = next((x for x in indices if x["ticker"] == ticker), None)
        return i["value"] if i and i["value"] is not None else "—"

    now = datetime.now(timezone.utc)
    ctx = dict(
        date_str=now.strftime("%b. %d, %Y"),
        time_str=now.strftime("%H:%M:%S"),
        stocks=stocks, etfs=etfs, bonds=bonds, commodities=commodities,
        ner_indices=ner_indices, tse_indices=tse_indices,
        orderbooks=orderbooks,
        total_count=total, frozen_count=frozen, active_count=active,
        avg_liq=avg_liq, avg_vol=avg_vol,
        ner_comp=idx_val(ner_indices, "B:COMP"),
        ner_stk=idx_val(ner_indices,  "B:STK"),
        tse_comp=idx_val(tse_indices, "T:COMP"),
        tse_stk=idx_val(tse_indices,  "T:STK"),
    )
    html = build_public(ctx) if mode == "public" else build_private(ctx)
    return html, 200, {"Content-Type": "text/html"}

@app.route("/debug")
def debug():
    try:
        ob   = atlas("/orderbook")
        h    = atlas("/history/BB?days=7&limit=5")
        secs = atlas("/securities?include_derived=true")
        ner_sample = next((s for s in secs if not is_tse(s.get("ticker",""))), None)
        tse_sample = next((s for s in secs if is_tse(s.get("ticker",""))), None)
        return {
            "total_securities": len(secs) if isinstance(secs, list) else "?",
            "ner_count": len([s for s in secs if not is_tse(s.get("ticker",""))]),
            "tse_count": len([s for s in secs if is_tse(s.get("ticker",""))]),
            "ob_len": len(ob) if isinstance(ob, list) else "?",
            "ob_sample": ob[0] if isinstance(ob, list) and ob else ob,
            "hist_sample": h,
            "ner_sample": ner_sample,
            "tse_sample": tse_sample,
        }
    except Exception as e:
        import traceback
        return {"error": str(e), "trace": traceback.format_exc()}, 500

@app.route("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
