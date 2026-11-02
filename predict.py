#!/usr/bin/env python3
"""Polymarket -> ETF composite views + falsifiable 3-month predictions + calibration.
Stdlib-only. Odds basket -> per-signal asset up-probabilities -> ONE rating per asset
(very bearish..very bullish, high/med/low confidence, 90-day horizon) -> static mobile
site (index rows + per-asset details pages) pushed to GitHub Pages."""
import json, os, re, subprocess, sys, urllib.parse, urllib.request
from datetime import date, datetime, timedelta, timezone

HOME = os.path.expanduser("~")
SITE = os.path.join(HOME, "signals/polypredict/site")
ARCHIVE = os.path.join(SITE, "predictions.jsonl")
os.makedirs(os.path.join(SITE, "details"), exist_ok=True)
os.makedirs(os.path.join(SITE, "narratives"), exist_ok=True)
NARR = os.path.join(SITE, "narratives")
UA = {"User-Agent": "Mozilla/5.0"}
GAMMA = "https://gamma-api.polymarket.com"
BASKET = ["SPY","QQQ","SCHD","XLE","XLF","XLC","XLY","XLP","XLV","ZROZ","HYG"]
HORIZON = 90

# ---------- odds helpers ----------
def poly_search(q):
    req = urllib.request.Request(f"{GAMMA}/public-search?q={urllib.parse.quote(q)}", headers=UA)
    return json.load(urllib.request.urlopen(req, timeout=30)).get("events", [])

def poly_yes(m):
    p = m.get("outcomePrices")
    if not p: return None
    if isinstance(p, str):
        p = json.loads(p)
        if isinstance(p, str): p = json.loads(p)
    try: return float(p[0]) * 100
    except (TypeError, ValueError): return None

def poly_open(e):
    return [m for m in e.get("markets", []) if not m.get("closed")]

def yahoo_series(tk):
    req = urllib.request.Request(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{tk}?range=1y&interval=1d", headers=UA)
    r = json.load(urllib.request.urlopen(req, timeout=30))["chart"]["result"][0]
    ts = r.get("timestamp") or []; c = (r["indicators"]["quote"][0].get("close") or [])
    return [(datetime.fromtimestamp(t, timezone.utc).date(), v) for t, v in zip(ts, c) if v is not None]

def close_on(series, d):
    prior = [p for p in series if p[0] <= d]
    return prior[-1][1] if prior else series[0][1]

MON = {m: i for i, m in enumerate(["january","february","march","april","may","june","july","august","september","october","november","december"], 1)}
MON3 = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
def when_key(s):
    mm = re.search(r"(\w+) (\d{4})", s.lower())
    return int(mm.group(2))*12 + MON[mm.group(1)] if mm and mm.group(1) in MON else None

# ---------- odds basket ----------
odds = {}
def fed_when(e):
    return when_key(" ".join(m.get("question","") for m in poly_open(e)))

def fetch():
    fed = [e for e in poly_search("Fed Decision")
           if re.match(r"Fed [Dd]ecision in \w+\??$", e.get("title","")) and not e.get("closed")]
    fed = [e for e in fed if fed_when(e)]
    if fed:
        e = min(fed, key=fed_when)
        hike = sum(y for m in poly_open(e) if "increase" in m.get("question","") and (y:=poly_yes(m)) is not None)
        k = fed_when(e)
        odds["fed_hike"] = (hike, f"hike odds {hike:.0f}% at {MON3[(k-1)%12]} '{str((k-1)//12)[2:]} meeting")
    for e in poly_search("Fed rate cut by"):
        if e.get("closed"): continue
        for m in poly_open(e):
            mm = re.search(r"by (\w+) (\d{4})", m.get("question",""))
            if mm and mm.group(1).lower() in MON and (y:=poly_yes(m)) is not None:
                if (mm.group(2), mm.group(1).lower()) == ("2027","july"):
                    odds["cut_jul27"] = (y, f"{m.get('question','')} — market prob {y:.0f}%")
        break
    lads = [e for e in poly_search("WTI Crude Oil hit")
            if re.search(r"hit in \w+ \d{4}", e.get("title","")) and not e.get("closed")]
    if lads:
        e = min(lads, key=lambda x: when_key(x.get("title","")) or 99999)
        for m in poly_open(e):
            if re.search(r"\(LOW\) \$90\b", m.get("question","")) and (y:=poly_yes(m)) is not None:
                odds["wti_dip90"] = (y, f"{m.get('question','')} — market prob {y:.0f}%")
            if re.search(r"\(HIGH\) \$120\b", m.get("question","")) and (y:=poly_yes(m)) is not None:
                odds["wti_spike120"] = (y, f"{m.get('question','')} — market prob {y:.0f}%")
    for e in poly_search("US-Iran ceasefire"):
        if e.get("closed"): continue
        for m in poly_open(e):
            if "December 31" in m.get("question","") and (y:=poly_yes(m)) is not None:
                odds["iran_peace_dec"] = (y, f"{m.get('question','')} — market prob {y:.0f}%")
        break
    for e in poly_search("US recession by end"):
        if e.get("closed"): continue
        for m in poly_open(e):
            if e.get("title","").strip() == "US recession by end of 2027?" and (y:=poly_yes(m)) is not None:
                odds["rec27"] = (y, f"{e.get('title','')} — market prob {y:.0f}%")
        break
    cpi = [e for e in poly_search("Core CPI MoM")
           if re.match(r"Core CPI MoM - \w+ \d{4}", e.get("title","")) and not e.get("closed")]
    if cpi:
        e = min(cpi, key=lambda x: when_key(x.get("title","")) or 99999)
        tot = sum(y for m in poly_open(e) if (n:=re.search(r"(\d+\.\d+)%", m.get("question","")))
                  and float(n.group(1)) >= 0.3 and "or less" not in m.get("question","")
                  and (y:=poly_yes(m)) is not None)
        odds["cpi_hot"] = (tot, f"{e.get('title','')} — hot buckets sum {tot:.0f}%")

# ---------- signals: theme -> (asset, probability asset rises over next 3 months) ----------
def signals(o):
    S = []
    def add(sig, basis, prob_up, invalid, asset):
        S.append(dict(sig=sig, basis=basis, prob_up=round(prob_up,1), invalid=invalid, asset=asset))
    if "fed_hike" in o:
        p, b = o["fed_hike"]
        if p >= 55:
            add("fed_hike", b, p, "revises if hike odds fall under 45%", "SCHD")
            for a in ("QQQ","XLC"): add("fed_hike", b, 100-p, "revises if hike odds fall under 45%", a)
        elif p <= 35:
            add("fed_hike", b, 100-p, "revises if hike odds climb over 55%", "SCHD")
            for a in ("QQQ","XLC"): add("fed_hike", b, p, "revises if hike odds climb over 55%", a)
    if "cut_jul27" in o:
        p, b = o["cut_jul27"]
        if p >= 50:
            for a in ("ZROZ","QQQ"): add("cut_jul27", b, 50+p*0.5, "revises if cut-by-July-2027 odds fall under 40%", a)
            add("cut_jul27", b, 50-p*0.5, "revises if cut-by-July-2027 odds fall under 40%", "SCHD")
    if "wti_dip90" in o:
        p, b = o["wti_dip90"]
        if p >= 55:
            for a in ("XLE","SCHD"): add("wti_dip90", b, 100-p*0.8, "revises if dip-under-$90 odds fall under 45%", a)
        elif p <= 40:
            add("wti_dip90", b, 50+(50-p)*0.6, "revises if dip-under-$90 odds climb over 55%", "XLE")
    if "wti_spike120" in o:
        p, b = o["wti_spike120"]
        if p >= 15:
            add("wti_spike120", b, 50+p*0.6, "revises if spike-over-$120 odds fall under 10%", "XLE")
    if "iran_peace_dec" in o:
        p, b = o["iran_peace_dec"]; brk = 100-p
        if brk >= 55:
            add("iran_break", b, 50+brk*0.4, "revises if ceasefire-hold odds recover over 55%", "XLE")
            add("iran_break", b, 50-brk*0.3, "revises if ceasefire-hold odds recover over 55%", "SPY")
    if "rec27" in o:
        p, b = o["rec27"]
        if p >= 45:
            for a in ("ZROZ","XLV"): add("rec27", b, 50+p*0.4, "revises if rec-by-2027 odds fall under 35%", a)
            for a in ("XLF","HYG"): add("rec27", b, 50-p*0.4, "revises if rec-by-2027 odds fall under 35%", a)
        elif p <= 25:
            for a in ("HYG","XLF"): add("rec27", b, 50+(50-p)*0.4, "revises if rec-by-2027 odds climb over 35%", a)
    if "cpi_hot" in o:
        p, b = o["cpi_hot"]
        if p >= 55:
            for a in ("QQQ","XLC"): add("cpi_hot", b, 50-p*0.35, "revises if hot-CPI odds fall under 45%", a)
    return [s for s in S if s["asset"] in BASKET]

# ---------- composite view per asset ----------
def rating_of(m):
    if m < 32: return "very bearish"
    if m < 42: return "bearish"
    if m <= 58: return "neutral"
    if m <= 68: return "bullish"
    return "very bullish"

def confidence_of(ms):
    if not ms: return "low"
    mean = sum(ms)/len(ms)
    if abs(mean-50) < 8: return "low"
    same_side = all(v > 50 for v in ms) or all(v < 50 for v in ms)
    spread = max(ms)-min(ms)
    if same_side and spread <= 25 and abs(mean-50) >= 12: return "high"
    if same_side or len(ms) >= 2: return "medium"
    return "low"

def views(sigs):
    by = {}
    for s in sigs: by.setdefault(s["asset"], []).append(s)
    out = {}
    for a in BASKET:
        ss = by.get(a, [])
        ms = [s["prob_up"] for s in ss]
        m = round(sum(ms)/len(ms), 1) if ms else 50.0
        out[a] = dict(prob_up=m, rating=rating_of(m), conf=confidence_of(ms), signals=ss)
    return out

# ---------- archive ----------
def load():
    rows = []
    if os.path.exists(ARCHIVE):
        with open(ARCHIVE) as f:
            for line in f:
                try: rows.append(json.loads(line))
                except json.JSONDecodeError: pass
    return rows

today = date.today()
cache = {}
def series(tk):
    if tk not in cache:
        try: cache[tk] = yahoo_series(tk)
        except Exception: cache[tk] = None
    return cache[tk]

def resolve(rows):
    for r in rows:
        if r.get("resolved") or r["expires"] > today.isoformat(): continue
        s = series(r["asset"])
        if not s: continue
        a = close_on(s, date.fromisoformat(r["date"]))
        b = close_on(s, min(date.fromisoformat(r["expires"]), s[-1][0]))
        up = b >= a
        r["resolved"] = True; r["ret"] = round((b/a-1)*100, 2)
        r["outcome"] = 1 if up else 0
        r["hit"] = (r["prob_up"] >= 50) == up
        r["brier"] = round(((r["prob_up"]/100)-up)**2, 3)

def calibration(rows, asset=None):
    agg = {}
    for r in rows:
        if not r.get("resolved") or r.get("rating") == "neutral": continue
        if asset and r["asset"] != asset: continue
        a = agg.setdefault(r["asset"], {"n":0,"hits":0,"brier":0.0})
        a["n"] += 1; a["hits"] += 1 if r["hit"] else 0; a["brier"] += r["brier"]
    return {k: {"n":v["n"],"hit":round(100*v["hits"]/v["n"]),"brier":round(v["brier"]/v["n"],3)} for k,v in agg.items()}

def latest_active(rows):
    last = {}
    for r in rows:
        if not r.get("resolved") and r["expires"] >= today.isoformat():
            last[r["asset"]] = r
    return last

# ---------- per-asset performance + trend analysis ----------
ASSET_NOTE = {
 "SPY":"Broad US equity market; sensitive to Fed policy path, credit conditions, and geopolitical risk-off.",
 "QQQ":"Mega-cap growth; multiples squeezed by rising real yields/hike odds, relieved by easing.",
 "SCHD":"Dividend-value basket loaded on energy, healthcare, staples; wins while the Fed stays hawkish and crude stays bid.",
 "XLE":"Energy equities; earnings track crude directly - the oil supply shock is the dominant driver.",
 "XLF":"Financials; benefits from steep curve and no-recession credit, hurt by recession odds.",
 "XLC":"Telecom/media - long-duration content businesses with weak pricing power; rate-sensitive.",
 "XLY":"Consumer discretionary; vulnerable to rate persistence + labor softening.",
 "XLP":"Consumer staples; pricing-power shelter in cost-push inflation.",
 "XLV":"Healthcare; pricing power + defensive bid, but credit blowouts hit biotech/pharma.",
 "ZROZ":"Long-duration Treasuries; convex recession hedge, gains when easing odds rise.",
 "HYG":"High-yield credit; dies with recession/regime risk, thrives in soft landing.",
}
def perf_stats(tk):
    s = series(tk)
    if not s or len(s) < 10: return None
    w = [q for q in s if q[0] >= s[-1][0] - timedelta(days=182)]
    if len(w) < 5: w = s[-20:]
    ret6 = (w[-1][1]/w[0][1]-1)*100
    hi = max(w, key=lambda q:q[1]); lo = min(w, key=lambda q:q[1])
    peak = -1; dd = 0.0
    for _,c in w:
        peak = max(peak,c); dd = min(dd,(c/peak-1)*100)
    w1 = [q for q in s if q[0] >= s[-1][0] - timedelta(days=30)]
    ret1 = (w1[-1][1]/w1[0][1]-1)*100 if len(w1) > 1 else 0.0
    return dict(ret6=round(ret6,1), ret1=round(ret1,1), hi=[str(hi[0]),round(hi[1],2)],
                lo=[str(lo[0]),round(lo[1],2)], dd=round(dd,1), last=round(w[-1][1],2))

def trend_of(p):
    if p is None: return "unknown"
    return "up" if p["ret6"] >= 5 else ("down" if p["ret6"] <= -5 else "sideways")

def trend_verdict(trend, m):
    view = "bullish" if m > 58 else ("bearish" if m < 42 else "neutral")
    if view == "neutral":
        return "Odds imply no strong 3-month direction (neutral zone); the realized trend may drift either way.", view
    if view == trend:
        return f"Odds AGREE with the 6-month trend - the crowd expects continuation ({view}).", view
    return f"Odds CONTRADICT the 6-month trend - the crowd expects a REVERSAL ({view} view vs {trend} trend).", view

def build_detail_data(V, rows):
    out = {}
    for a in BASKET:
        d = V[a]; pf = perf_stats(a); tr = trend_of(pf)
        vtxt, vview = trend_verdict(tr, d["prob_up"])
        act = [r for r in rows if r["asset"]==a and not r.get("resolved")]
        out[a] = dict(asset=a, rating=d["rating"], conf=d["conf"], prob_up=d["prob_up"],
                      perf=pf, trend=tr, verdict=vtxt, view=vview,
                      signals=[{k:s[k] for k in ("sig","basis","prob_up","invalid")} for s in d["signals"]],
                      expires=act[-1]["expires"] if act else None, note=ASSET_NOTE.get(a,""))
    return out

# ---------- html ----------
def esc(x): return str(x).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
SNAME = {"fed_hike":"Fed hike","cut_jul27":"Fed cut by Jul 2027","wti_dip90":"WTI dips below $90",
         "wti_spike120":"WTI spikes above $120","iran_break":"Iran ceasefire breaks","rec27":"US recession by 2027",
         "cpi_hot":"Hot core CPI (>=0.3%)"}
RCLS = {"very bearish":"vbear","bearish":"bear","neutral":"neu","bullish":"bull","very bullish":"vbull"}
CSS = ("body{background:#0d1117;color:#c9d1d9;font-family:ui-monospace,Menlo,monospace;margin:14px;font-size:14px;max-width:720px}"
 "h1{font-size:17px;color:#58a6ff;margin-bottom:4px}h2{font-size:14px;color:#8b949e;margin:22px 0 6px}"
 ".sub{color:#8b949e;font-size:12px;margin-bottom:10px}"
 ".row{display:flex;align-items:center;gap:8px;padding:10px 8px;border-bottom:1px solid #21262d;flex-wrap:wrap}"
 ".asset{font-weight:bold;min-width:58px}.tag{padding:3px 8px;border-radius:10px;font-size:12px}"
 ".vbear{background:#5c1c1c;color:#ff9191}.bear{background:#3a2020;color:#f0a0a0}"
 ".neu{background:#222;color:#bbb}.bull{background:#1e3a24;color:#8fd69a}.vbull{background:#14502a;color:#7dff9e}"
 ".conf{font-size:11px;color:#8b949e;min-width:52px}.det{margin-left:auto}"
 ".det a{color:#58a6ff;border:1px solid #30363d;border-radius:8px;padding:4px 10px;text-decoration:none;font-size:12px}"
 ".bar{height:6px;background:#21262d;border-radius:3px;position:relative;flex:1 1 130px;min-width:110px;max-width:220px}"
 ".bar i{position:absolute;left:50%;top:-2px;width:2px;height:10px;background:#484f58}"
 ".bar b{position:absolute;top:0;height:6px;border-radius:3px}"
 "table{width:100%;border-collapse:collapse}td,th{padding:4px 6px;border-bottom:1px solid #21262d;text-align:left;font-size:12px;word-break:break-word}"
 ".up{color:#3fb950}.down{color:#f85149}.dim{color:#8b949e}"
 "card{border:1px solid #21262d;border-radius:10px;padding:10px 12px;margin:8px 0}"
 ".card .top{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:6px}"
 ".card .name{font-weight:bold;font-size:13px}"
 ".card .prob{font-size:12px;padding:2px 8px;border-radius:8px;background:#1c2a3a;color:#9ecbff}"
 ".card .basis{font-size:12.5px;color:#c9d1d9;line-height:1.55}"
 ".card .inv{font-size:11px;color:#8b949e;margin-top:6px}"
 ".comp{font-size:12px;color:#8b949e;margin:10px 0;line-height:1.5}"
 "@media(max-width:480px){.row{gap:6px;padding:9px 4px}.conf{min-width:0}.det{margin-left:0}}")

def prob_bar(m):
    w = abs(m-50)
    side = "left:50%" if m >= 50 else f"left:{50-w}%"
    col = "#f85149" if m < 42 else ("#8fd69a" if m > 58 else "#8b8b8b")
    return f"<div class='bar'><i></i><b style='{side};width:{w}%;background:{col}'></b></div>"

def page(title, body, back=False):
    h = [f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
         f"<title>{esc(title)}</title><style>{CSS}</style></head><body>"]
    if back: h.append("<a href='../' style='color:#58a6ff'>&larr; all assets</a>")
    h.append(body)
    h.append(f"<p class='sub'>generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} &mdash; Polymarket Gamma API odds; rules engine + calibration history in predictions.jsonl</p></body></html>")
    return "\n".join(h)

def md_lite(txt):
    out = []
    for block in [x.strip() for x in txt.split("\n\n") if x.strip()]:
        if block.startswith("## "):
            out.append(f"<h2>{esc(block[3:].strip())}</h2>")
        else:
            para = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc(block)).replace("\n", " ")
            out.append(f"<p style='line-height:1.6;margin:8px 0'>{para}</p>")
    return "\n".join(out)

def load_narrative(a):
    fp = os.path.join(NARR, f"{a}.md")
    if os.path.exists(fp) and (today - date.fromtimestamp(os.path.getmtime(fp))).days <= 3:
        with open(fp) as f: return f.read()
    return None

def fallback_narrative(a, dd):
    pf = dd["perf"]
    if pf:
        perf = (f"## Performance (6 months)\n\n"
                f"{a} returned **{pf['ret6']:+.1f}%** over the past 6 months (last close {pf['last']:,.2f}). "
                f"Peak {pf['hi'][1]:,.2f} on {pf['hi'][0]}, trough {pf['lo'][1]:,.2f} on {pf['lo'][0]}; "
                f"worst pullback inside the window was {pf['dd']:.1f}%. Last 30 days: {pf['ret1']:+.1f}%. Trend: {dd['trend']}.")
    else:
        perf = f"## Performance (6 months)\n\nNo price history available for {a}."
    sig_txt = "; ".join(f"{SNAME.get(s['sig'], s['sig'])}: {s['basis']}" for s in dd["signals"]) or "no odds crossed rule thresholds"
    drivers = (f"## Drivers\n\n{dd['note']} Active market odds: {sig_txt}.")
    ev = ("Active signal evidence: " + "; ".join(
          f"{SNAME.get(s['sig'], s['sig'])} up-prob {s['prob_up']:.0f}% (invalidation: {s['invalid']})" for s in dd["signals"])
          if dd["signals"] else "No active signals - the view rests only on the asset's baseline role in the basket.")
    ana = f"## Continue or revert\n\n{dd['verdict']} {ev}"
    return perf + "\n\n" + drivers + "\n\n" + ana

def index_html(V, rows, cal, o):
    b = [f"<h1>Polymarket &#8594; ETF views</h1><div class='sub'>{today.isoformat()} &middot; 3-month horizon &middot; crowd odds mapped through auditable rules</div>"]
    for a in BASKET:
        d = V[a]
        b.append(f"<div class='row'><span class='asset'>{a}</span><span class='tag {RCLS[d['rating']]}'>{d['rating']}</span>"
                 f"<span class='conf'>{d['conf']} conf</span>{prob_bar(d['prob_up'])}"
                 f"<span class='det'><a href='details/{a}.html'>details</a></span></div>")
    b.append("<h2>CALIBRATION (resolved views, non-neutral)</h2><table><tr><th>asset</th><th>n</th><th>hit%</th><th>mean Brier</th></tr>")
    for k, c in sorted(cal.items(), key=lambda kv: -kv[1]["n"]):
        b.append(f"<tr><td>{k}</td><td>{c['n']}</td><td>{c['hit']}%</td><td>{c['brier']}</td></tr>")
    if not cal: b.append("<tr><td colspan='4' class='dim'>nothing resolved yet &mdash; views resolve 90 days after issuance</td></tr>")
    b.append("</table>")
    b.append("<h2>TODAY'S ODDS BASKET</h2><table><tr><th>market</th><th>prob</th></tr>")
    for k, (val, lbl) in sorted(o.items()):
        b.append(f"<tr><td>{esc(lbl)}</td><td>{val:.1f}%</td></tr>")
    b.append("</table>")
    return page("Polymarket -> ETF views", "\n".join(b))

def detail_html(a, dd, rows):
    b = [f"<h1>{a} &mdash; <span class='tag {RCLS[dd['rating']]}'>{dd['rating']}</span></h1>"
         f"<div class='sub'>{dd['conf']} confidence &middot; implied 3-month up-probability {dd['prob_up']:.0f}% &middot; trend: {dd['trend']} &middot; {today.isoformat()}</div>{prob_bar(dd['prob_up'])}"]
    b.append(md_lite(load_narrative(a) or fallback_narrative(a, dd)))
    b.append("<h2>ODDS EVIDENCE</h2>")
    if dd["signals"]:
        for s in dd["signals"]:
            b.append(f"<div class='card'><div class='top'><span class='name'>{esc(SNAME.get(s['sig'], s['sig']))}</span>"
                     f"<span class='prob'>{a} 3mo up-prob {s['prob_up']:.0f}%</span></div>"
                     f"<div class='basis'>{esc(s['basis'])}</div>"
                     f"<div class='inv'>&#9888; invalidation: {esc(s['invalid'])}</div></div>")
        b.append(f"<div class='comp'>Composite: mean of {a} up-probs &rarr; {dd['prob_up']:.0f}% &rarr; <b>{dd['rating']}</b> ({dd['conf']} confidence)</div>")
    else:
        b.append("<div class='comp'>No odds crossed rule thresholds today &mdash; held neutral.</div>")
    acal = calibration(rows, asset=a)
    b.append("<h2>TRACK RECORD ON THIS ASSET</h2>")
    b.append(f"<div class='sub'>{acal[a]['n']} resolved views &middot; hit {acal[a]['hit']}% &middot; mean Brier {acal[a]['brier']}</div>"
             if a in acal else "<div class='sub'>no resolved views yet</div>")
    past = [r for r in rows if r["asset"] == a and r.get("resolved")][-10:]
    if past:
        b.append("<table><tr><th>date</th><th>rating</th><th>ret%</th><th>hit</th></tr>")
        for r in reversed(past):
            b.append(f"<tr><td>{r['date']}</td><td>{esc(r['rating'])}</td><td>{r['ret']:+.1f}</td>"
                     f"<td class='{'up' if r['hit'] else 'down'}'>{'HIT' if r['hit'] else 'MISS'}</td></tr>")
        b.append("</table>")
    cur = [r for r in rows if r["asset"] == a and not r.get("resolved")]
    if cur:
        c = cur[-1]
        b.append(f"<h2>CURRENT VIEW</h2><div class='sub'>issued {c['date']} &middot; expires {c['expires']} &middot; resolved by comparing close on expiry vs issue date</div>")
    return page(f"{a} details", "\n".join(b), back=True)

# ---------- main ----------
def push():
    subprocess.run(["git","add","-A"], cwd=SITE, capture_output=True)
    subprocess.run(["git","commit","-m",f"update {today.isoformat()}"], cwd=SITE, capture_output=True)
    r = subprocess.run(["git","push"], cwd=SITE, capture_output=True, text=True)
    r2 = subprocess.run(["git","push","-f","origin","main:gh-pages"], cwd=SITE, capture_output=True, text=True)
    return (r.returncode, r2.returncode, (r.stderr or r2.stderr or "")[-200:])

if "--render" in sys.argv:
    # narrative files were updated externally; rebuild detail pages from cached data, no fetching
    with open(os.path.join(SITE, "detail_data.json")) as f: DD = json.load(f)
    rows = load()
    for a in DD:
        with open(os.path.join(SITE, "details", f"{a}.html"), "w") as f:
            f.write(detail_html(a, DD[a], rows))
    rc1, rc2, err = push()
    print(f"render-only: re-rendered {len(DD)} detail pages; push rc={rc1}/{rc2}; {err if (rc1 or rc2) else 'pushed ok'}")
    sys.exit(0)

fetch()
rows = load()
resolve(rows)
V = views(signals(odds))
new = []
act = latest_active(rows)
for a in BASKET:
    d = V[a]
    prevr = act.get(a)
    if prevr and prevr["rating"] == d["rating"] and prevr["conf"] == d["conf"]:
        continue
    new.append(dict(date=today.isoformat(), asset=a, rating=d["rating"], conf=d["conf"],
                    prob_up=d["prob_up"], expires=(today+timedelta(days=HORIZON)).isoformat(),
                    signals=[{k:s[k] for k in ("sig","basis","prob_up","invalid")} for s in d["signals"]],
                    resolved=False))
with open(ARCHIVE, "w") as f:
    for r in rows + new: f.write(json.dumps(r)+"\n")
DD = build_detail_data(V, rows + new)
with open(os.path.join(SITE, "detail_data.json"), "w") as f:
    json.dump(DD, f)
with open(os.path.join(SITE, "index.html"), "w") as f:
    f.write(index_html(V, rows+new, calibration(rows+new), odds))
for a in BASKET:
    with open(os.path.join(SITE, "details", f"{a}.html"), "w") as f:
        f.write(detail_html(a, DD[a], rows+new))

rc1, rc2, err = push()
print("views: " + ", ".join(f"{a}={V[a]['rating']}({V[a]['conf']})" for a in BASKET))
print(f"odds: {len(odds)} markets; view changes: {len(new)}; archive rows: {len(rows)+len(new)}; push rc={rc1}/{rc2}")
print(err if (rc1 or rc2) else "pushed ok")
