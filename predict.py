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

# ---------- bayesian belief layer ----------
BAY = {}
def run_bayes():
    try:
        subprocess.run([sys.executable, "bayes.py"], cwd=SITE, capture_output=True, timeout=180)
    except Exception as e:
        print("bayes.py failed:", e)
    try:
        with open(os.path.join(SITE, "bayes_data.json")) as f: return json.load(f)
    except FileNotFoundError:
        return {}

def apply_bayes(V, B):
    """Market prior -> evidence posterior. Basket assets keep signal confidence;
    watchlist stocks (video-named) enter V with neutral prior + evidence only."""
    for a, e in B.items():
        if e.get("watch"):
            pf = {"ret6": e.get("ret6"), "ret1": e.get("ret1"), "dd": e.get("dd"), "last": e.get("last")} \
                 if e.get("ret6") is not None else None
            V[t := a] = dict(prob_up=e["post"], rating=rating_of(e["post"]), conf="low", signals=[],
                             watch=True, prior=e["prior"], bayes=True, perf=pf)
        elif a in V:
            d = V[a]
            d["prior"] = e["prior"]; d["prob_up"] = e["post"]
            d["rating"] = rating_of(e["post"]); d["bayes"] = True
    return V

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
    agree = (view == "bullish" and trend == "up") or (view == "bearish" and trend == "down")
    if agree:
        return f"Odds AGREE with the 6-month trend - the crowd expects continuation ({view}).", view
    if trend == "sideways":
        return f"Price has been sideways but odds point {view} - a new 3-month trend may be forming.", view
    return f"Odds CONTRADICT the 6-month trend - the crowd expects a REVERSAL ({view} view vs {trend} trend).", view

def build_detail_data(V, rows):
    out = {}
    for a in V:
        d = V[a]; pf = perf_stats(a) or (d.get("perf") if d.get("watch") else None)
        tr = trend_of(pf)
        vtxt, vview = trend_verdict(tr, d["prob_up"])
        act = [r for r in rows if r["asset"]==a and not r.get("resolved")]
        note = ASSET_NOTE.get(a) or "Watched stock named in a tracked macro YouTube video; scored against a neutral prior by video evidence only (no Polymarket leg)."
        out[a] = dict(asset=a, rating=d["rating"], conf=d["conf"], prob_up=d["prob_up"],
                      prior=d.get("prior"), bayes=bool(d.get("bayes")), watch=bool(d.get("watch")),
                      perf=pf, trend=tr, verdict=vtxt, view=vview,
                      signals=[{k:s[k] for k in ("sig","basis","prob_up","invalid")} for s in d["signals"]],
                      expires=act[-1]["expires"] if act else None, note=note)
    return out

# ---------- html ----------
def esc(x): return str(x).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
SNAME = {"fed_hike":"Fed hike","cut_jul27":"Fed cut by Jul 2027","wti_dip90":"WTI dips below $90",
         "wti_spike120":"WTI spikes above $120","iran_break":"Iran ceasefire breaks","rec27":"US recession by 2027",
         "cpi_hot":"Hot core CPI (>=0.3%)"}
RCLS = {"very bearish":"vbear","bearish":"bear","neutral":"neu","bullish":"bull","very bullish":"vbull"}
# Template layer: dark, mobile-first, system fonts only, inline CSS, no JS / external assets.
CSS = """
:root{--bg:#0a0d13;--sf:#11161f;--sf2:#171d29;--bd:#222a37;--bd2:#1a212c;--tx:#e7eaf0;--tx2:#c9cfda;--mu:#8a93a6;
--ac:#7aa2f7;--vbull:#34d17a;--bull:#86cf9c;--neu:#97a0b0;--bear:#eea0a0;--vbear:#ff6464;--rk:var(--neu)}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;overflow-wrap:anywhere;-webkit-font-smoothing:antialiased}
.wrap{max-width:780px;margin:0 auto;padding:24px 18px 40px}
a{color:var(--ac)}
h1{font-size:23px;font-weight:700;letter-spacing:-.015em;margin:0 0 4px;line-height:1.25}
h2{font-size:12px;font-weight:650;letter-spacing:.09em;color:var(--mu);margin:32px 0 10px;text-transform:uppercase}
.sub{color:var(--mu);font-size:13px;line-height:1.5}
.num,.pct,.tbl td{font-variant-numeric:tabular-nums}
.up{color:var(--vbull)}.down{color:var(--vbear)}.dim{color:var(--mu)}
.rk-vbull{--rk:var(--vbull)}.rk-bull{--rk:var(--bull)}.rk-neu{--rk:var(--neu)}.rk-bear{--rk:var(--bear)}.rk-vbear{--rk:var(--vbear)}
.pill{display:inline-block;padding:3px 10px;border-radius:999px;font-size:11px;font-weight:750;letter-spacing:.07em;white-space:nowrap;border:1px solid;line-height:1.4}
.pill.vbull{color:#03200e;background:var(--vbull);border-color:var(--vbull)}
.pill.bull{color:var(--bull);background:rgba(134,207,156,.11);border-color:rgba(134,207,156,.38)}
.pill.neu{color:var(--neu);background:rgba(151,160,176,.09);border-color:rgba(151,160,176,.3)}
.pill.bear{color:var(--bear);background:rgba(238,160,160,.11);border-color:rgba(238,160,160,.38)}
.pill.vbear{color:#2b0505;background:var(--vbear);border-color:var(--vbear)}
.top{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:6px}
.back{display:inline-block;font-size:13px;text-decoration:none;margin-bottom:16px;color:var(--mu)}
.back:hover{color:var(--ac)}
.list{display:flex;flex-direction:column;gap:8px}
.row{background:var(--sf);border:1px solid var(--bd);border-left:3px solid var(--rk);border-radius:12px;padding:12px 14px}
.rh{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.tk{font-weight:750;font-size:16px;letter-spacing:.01em;min-width:58px}
.conf{font-size:12.5px;color:var(--mu)}
.det{margin-left:auto;font-size:13px;font-weight:600;text-decoration:none;padding:6px 12px;border:1px solid var(--bd);border-radius:8px;background:var(--sf2);white-space:nowrap;color:var(--ac)}
.det:hover{border-color:var(--ac)}
.rb{display:flex;align-items:center;gap:12px;margin-top:10px}
.bar{position:relative;flex:1 1 auto;height:6px;background:#1b2230;border-radius:3px;min-width:0}
.bar i{position:absolute;left:50%;top:-3px;width:1px;height:12px;background:#3b4558}
.bar b{position:absolute;top:0;height:6px;border-radius:3px;background:var(--rk)}
.pct{font-size:13px;font-weight:650;white-space:nowrap;min-width:64px;text-align:right}
.prior{margin-top:7px;font-size:12.5px;color:var(--mu)}
.prior b{color:var(--tx);font-weight:650}
.card{background:var(--sf);border:1px solid var(--bd);border-radius:12px;padding:14px 16px;margin:10px 0}
.hero{border-left:3px solid var(--rk);padding:16px 18px}
.hero h1{font-size:26px;margin:0}
.big{font-size:30px;font-weight:750;letter-spacing:-.02em;line-height:1.1}
.kv{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin:10px 0 10px}
.kv .lbl{font-size:12.5px;color:var(--mu)}
.hero .bar{margin:4px 0 12px}
.narr p{margin:6px 0 14px;line-height:1.68;color:var(--tx2)}
.narr b{color:var(--tx)}
.narr h2:first-child{margin-top:26px}
.pp{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin:2px 0 10px}
.pp .side{display:flex;flex-direction:column}
.pp .side span{font-size:11px;letter-spacing:.07em;text-transform:uppercase;color:var(--mu)}
.pp .arr{font-size:22px;color:var(--mu)}
.note{font-size:13px;color:var(--mu);line-height:1.6;margin:8px 0}
.note b{color:var(--tx2)}
.ev{border-left:3px solid var(--bd)}
.ev.up{border-left-color:var(--vbull)}.ev.down{border-left-color:var(--vbear)}
.ct{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:8px}
.badge{font-size:11.5px;font-weight:650;padding:2px 9px;border-radius:6px;background:var(--sf2);color:var(--tx2);border:1px solid var(--bd);white-space:nowrap}
.badge.up{color:var(--vbull)}.badge.down{color:var(--vbear)}
.ctitle{font-weight:650;font-size:14.5px}
.claim{font-size:14px;line-height:1.58;color:var(--tx)}
.rel{font-size:12.5px;color:var(--mu);margin-top:8px;line-height:1.5}
.inv{font-size:12.5px;color:var(--mu);margin-top:10px;padding-top:9px;border-top:1px solid var(--bd2);line-height:1.5}
.meta{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px 14px;margin-top:12px;padding-top:11px;border-top:1px solid var(--bd2)}
.meta div{min-width:0}
.meta span{display:block;font-size:10.5px;letter-spacing:.07em;text-transform:uppercase;color:var(--mu);margin-bottom:1px}
.meta b{font-weight:550;font-size:13px;color:var(--tx2)}
.meta .w2{grid-column:span 2}.meta .w3{grid-column:1/-1}
.tblc{padding:4px 6px}
.tbl{width:100%;border-collapse:collapse;table-layout:fixed;font-size:13.5px}
.tbl th{text-align:left;color:var(--mu);font-weight:650;font-size:11px;text-transform:uppercase;letter-spacing:.07em;padding:9px 10px;border-bottom:1px solid var(--bd)}
.tbl td{padding:9px 10px;border-bottom:1px solid var(--bd2)}
.tbl tr:last-child td{border-bottom:0}
.odds{list-style:none;margin:0;padding:4px 0}
.odds li{display:flex;align-items:center;gap:12px;padding:10px 16px;border-bottom:1px solid var(--bd2)}
.odds li:last-child{border-bottom:0}
.odds .q{flex:1 1 auto;min-width:0;font-size:13.5px;color:var(--tx2);line-height:1.45}
.odds .v{font-weight:650;font-size:13.5px;white-space:nowrap}
.cv{border-left:3px solid var(--rk)}
.foot{margin-top:40px;padding-top:14px;border-top:1px solid var(--bd);font-size:12px;color:var(--mu);line-height:1.6}
@media (max-width:720px){
.wrap{padding:16px 12px 32px}
h1{font-size:20px}.hero h1{font-size:22px}
h2{margin:26px 0 8px}
.row{padding:12px}
.rh{display:grid;grid-template-columns:auto minmax(0,1fr) auto;grid-template-areas:"tk pl dt" "cf cf cf";gap:6px 10px}
.rh .tk{grid-area:tk;min-width:0}.rh .pill{grid-area:pl;justify-self:start}.rh .det{grid-area:dt;margin-left:0}.rh .conf{grid-area:cf}
.pct{min-width:0}
.card{padding:12px 13px}.hero{padding:14px}
.big{font-size:26px}
.meta{grid-template-columns:repeat(2,minmax(0,1fr))}.meta .w2,.meta .w3{grid-column:1/-1}
.tbl{font-size:12.5px}.tbl th,.tbl td{padding:8px 6px}
.odds li{padding:10px 12px}
}
@media (max-width:370px){
.rh{grid-template-columns:minmax(0,1fr) auto;grid-template-areas:"tk dt" "pl cf"}
.rh .conf{justify-self:end;text-align:right}
.tk{font-size:15px}
}
"""
LEAN_W = {"very bearish":"VERY BEARISH","bearish":"BEARISH","bullish":"BULLISH","very bullish":"VERY BULLISH"}
def dword(rating, p):
    """Display-only direction word; the neutral band reads NEUTRAL at ~50% else NEARLY NEUTRAL."""
    if rating in LEAN_W: return LEAN_W[rating]
    return "NEUTRAL" if p is None or round(p) == 50 else "NEARLY NEUTRAL"

def pill(rating, p):
    return f"<span class='pill {RCLS.get(rating, 'neu')}'>{dword(rating, p)}</span>"

def pct(x): return f"{x:.0f}%"

def prior_line(d):
    """'market prior raw% -> posterior%' for bayes-adjusted basket rows; watchlist uses a neutral prior."""
    if not d.get("bayes") or d.get("prior") is None: return ""
    lab = "neutral prior" if d.get("watch") else "market prior"
    return f"<div class='prior num'>{lab} {pct(d['prior'])} &rarr; <b>{pct(d['prob_up'])}</b></div>"

def prob_bar(m):
    w = round(abs(m-50), 1)
    side = "left:50%" if m >= 50 else f"left:{round(50-w, 1)}%"
    return f"<div class='bar' title='up-probability {m:.0f}%'><i></i><b style='{side};width:{w}%'></b></div>"

def page(title, body, back=False):
    h = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
         f"<meta name='color-scheme' content='dark'><meta name='theme-color' content='#0a0d13'>"
         f"<title>{esc(title)}</title><style>{CSS}</style></head><body><main class='wrap'>"]
    if back: h.append("<a class='back' href='../'>&larr; All assets</a>")
    h.append(body)
    h.append(f"<footer class='foot'>last updated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} &middot; Polymarket Gamma API odds &rarr; rules engine &rarr; Bayesian video-evidence update &middot; calibration history in predictions.jsonl</footer></main></body></html>")
    return "\n".join(h)

def md_lite(txt):
    out = []
    for block in [x.strip() for x in txt.split("\n\n") if x.strip()]:
        if block.startswith("## "):
            out.append(f"<h2>{esc(block[3:].strip().upper())}</h2>")
        else:
            para = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc(block)).replace("\n", " ")
            out.append(f"<p>{para}</p>")
    return "<section class='narr'>" + "\n".join(out) + "</section>"

def load_narrative(a):
    fp = os.path.join(NARR, f"{a}.md")
    if os.path.exists(fp) and (today - date.fromtimestamp(os.path.getmtime(fp))).days <= 3:
        with open(fp) as f: return f.read()
    return None

def fallback_narrative(a, dd):
    pf = dd["perf"]
    if pf and "hi" in pf:
        perf = (f"## Performance (6 months)\n\n"
                f"{a} returned **{pf['ret6']:+.1f}%** over the past 6 months (last close {pf['last']:,.2f}). "
                f"Peak {pf['hi'][1]:,.2f} on {pf['hi'][0]}, trough {pf['lo'][1]:,.2f} on {pf['lo'][0]}; "
                f"worst pullback inside the window was {pf['dd']:.1f}%. Last 30 days: {pf['ret1']:+.1f}%. Trend: {dd['trend']}.")
    elif pf:
        perf = (f"## Performance (6 months)\n\n"
                f"{a} returned **{pf['ret6']:+.1f}%** over the past 6 months (last close {pf['last']:,.2f}); "
                f"worst pullback inside the window {pf['dd']:.1f}%. Last 30 days: {pf['ret1']:+.1f}%. Trend: {dd['trend']}.")
    else:
        perf = f"## Performance (6 months)\n\nNo price history available for {a}."
    sig_txt = "; ".join(f"{SNAME.get(s['sig'], s['sig'])}: {s['basis']}" for s in dd["signals"]) or "no odds crossed rule thresholds"
    drivers = (f"## Drivers\n\n{dd['note']} Active market odds: {sig_txt}.")
    ev = ("Active signal evidence: " + "; ".join(
          f"{SNAME.get(s['sig'], s['sig'])} up-prob {s['prob_up']:.0f}% (invalidation: {s['invalid']})" for s in dd["signals"])
          if dd["signals"] else "No active signals - the view rests only on the asset's baseline role in the basket.")
    ana = f"## Continue or revert\n\n{dd['verdict']} {ev}"
    return perf + "\n\n" + drivers + "\n\n" + ana

def asset_row(t, d, href):
    """One asset per row: ticker, direction word, confidence, Details link; bar + up-prob; prior->posterior."""
    rc = RCLS.get(d["rating"], "neu")
    return (f"<div class='row rk-{rc}'><div class='rh'><span class='tk'>{esc(t)}</span>{pill(d['rating'], d['prob_up'])}"
            f"<span class='conf'>{esc(d['conf'])} confidence</span><a class='det' href='{href}'>Details &rarr;</a></div>"
            f"<div class='rb'>{prob_bar(d['prob_up'])}<span class='pct'>up {pct(d['prob_up'])}</span></div>"
            f"{prior_line(d)}</div>")

def index_html(V, rows, cal, o):
    b = [f"<header><h1>Polymarket &rarr; ETF views</h1><div class='sub'>{today.isoformat()} &middot; 3-month horizon &middot; "
         f"crowd odds mapped through auditable rules, then updated by macro-video evidence (Bayesian)</div></header>"]
    b.append("<h2>ETF BASKET</h2><div class='list'>")
    for a in [x for x in BASKET if x in V]:
        b.append(asset_row(a, V[a], f"details/{a}.html"))
    b.append("</div>")
    W = [t for t in V if V[t].get("watch")]
    if W:
        b.append("<h2>VIDEO-EVIDENCE WATCHLIST</h2><div class='sub' style='margin:-4px 0 10px'>stocks named in tracked macro videos "
                 "&mdash; neutral prior updated by video evidence only</div><div class='list'>")
        for t in sorted(W):
            b.append(asset_row(t, V[t], f"details/W_{t}.html"))
        b.append("</div>")
    b.append("<h2>CALIBRATION &middot; RESOLVED NON-NEUTRAL VIEWS</h2><div class='card tblc'><table class='tbl'>"
             "<tr><th>asset</th><th>n</th><th>hit</th><th>Brier</th></tr>")
    for k, c in sorted(cal.items(), key=lambda kv: -kv[1]["n"]):
        b.append(f"<tr><td><b>{esc(k)}</b></td><td>{c['n']}</td><td>{c['hit']}%</td><td>{c['brier']}</td></tr>")
    if not cal: b.append("<tr><td colspan='4' class='dim'>nothing resolved yet &mdash; views resolve 90 days after issuance</td></tr>")
    b.append("</table></div>")
    b.append("<h2>TODAY'S ODDS BASKET</h2><div class='card' style='padding:0'><ul class='odds'>")
    for k, (val, lbl) in sorted(o.items()):
        b.append(f"<li><span class='q'>{esc(lbl)}</span><span class='v num'>{val:.1f}%</span></li>")
    if not o: b.append("<li><span class='q dim'>no odds cached</span></li>")
    b.append("</ul></div>")
    return page("Polymarket -> ETF views", "\n".join(b))

def ev_card(it, be):
    """Evidence card: direction, claim, source / title / date / LR / age / decay."""
    up = it.get("dir") == "up"
    src, _, title = str(it.get("source", "")).partition("—")
    src, title = src.strip() or "unknown", title.strip()
    title = esc(title or str(be.get("found_in") or "").strip()) or "&mdash;"
    hl = it.get("half_life", 60); age = it.get("age", 0)
    w = 0.5 ** (age / hl) if hl else 0
    return (f"<div class='card ev {'up' if up else 'down'}'><div class='ct'>"
            f"<span class='badge {'up' if up else 'down'}'>{'&#9650; supports rise' if up else '&#9660; supports fall'}</span>"
            f"<span class='badge num'>LR {it.get('lr_eff', it.get('lr'))}</span></div>"
            f"<div class='claim'>{esc(it.get('claim',''))}</div>"
            + (f"<div class='rel'>causal link: {esc(it['relation'])}</div>" if it.get("relation") else "") +
            f"<div class='meta'><div><span>source</span><b>{esc(src)}</b></div><div class='w2'><span>title</span><b>{title}</b></div>"
            f"<div><span>date</span><b class='num'>{esc(it.get('date','?'))}</b></div>"
            f"<div><span>LR raw &rarr; effective</span><b class='num'>{it.get('lr')} &rarr; {it.get('lr_eff', it.get('lr'))}</b></div>"
            f"<div><span>age</span><b class='num'>{age}d</b></div>"
            f"<div class='w3'><span>decay</span><b class='num'>half-life {hl}d &middot; weight {w*100:.0f}%</b></div></div></div>")

def detail_html(a, dd, rows):
    rc = RCLS.get(dd["rating"], "neu")
    pf = dd.get("perf") or {}
    perf_bits = (f" &middot; 6m {pf['ret6']:+.1f}%" if pf.get("ret6") is not None else "") + \
                (f" &middot; 30d {pf['ret1']:+.1f}%" if pf.get("ret1") is not None else "")
    b = [f"<header class='card hero rk-{rc}'><div class='top'><h1>{esc(a)}</h1>{pill(dd['rating'], dd['prob_up'])}"
         f"<span class='conf'>{esc(dd['conf'])} confidence</span></div>"
         f"<div class='kv'><span class='big num'>{pct(dd['prob_up'])}</span><span class='lbl'>probability of rising over 3 months</span></div>"
         f"{prob_bar(dd['prob_up'])}{prior_line(dd)}"
         f"<div class='sub' style='margin-top:6px'>trend: {esc(dd['trend'])}{perf_bits} &middot; {today.isoformat()}"
         f"{' &middot; video-evidence watchlist' if dd.get('watch') else ''}</div></header>"]
    # 1) narrative first
    b.append(md_lite(load_narrative(a) or fallback_narrative(a, dd)))
    # 2) Bayesian update
    be = BAY.get(a)
    lab = "NEUTRAL PRIOR" if dd.get("watch") else "MARKET PRIOR"
    b.append(f"<h2>BAYESIAN UPDATE &middot; {lab} &rarr; POSTERIOR</h2>")
    if be:
        srcs = sorted({str(it.get('source','')).split('—')[0].strip() for it in be['items']} - {""})
        b.append(f"<div class='card'><div class='pp'><div class='side'><span>{lab.lower()}</span><b class='big num dim'>{pct(be['prior'])}</b></div>"
                 f"<div class='arr'>&rarr;</div><div class='side'><span>posterior</span><b class='big num'>{pct(be['post'])}</b></div>"
                 f"{pill(rating_of(be['post']), be['post'])}</div>"
                 f"<div class='note'>Posterior odds = prior odds &times; likelihood ratios of video facts, each decayed by its half-life. "
                 f"{len(be['items'])} active evidence item{'s' if len(be['items']) != 1 else ''}"
                 f" &middot; sources: {esc(', '.join(srcs)) or 'no active evidence'}.</div>"
                 + (f"<div class='note' style='border-top:1px solid var(--bd2);padding-top:10px'><b>Conflict resolution:</b> {esc(be['resolution'])}</div>"
                    if be.get("resolution") else "") + "</div>")
        for it in be["items"]:
            b.append(ev_card(it, be))
    else:
        b.append("<div class='card note' style='margin:10px 0'>No video evidence on file for this asset &mdash; the view rests on market odds only.</div>")
    # 3) odds evidence as stacked cards
    b.append("<h2>ODDS EVIDENCE</h2>")
    if dd["signals"]:
        for s in dd["signals"]:
            b.append(f"<div class='card'><div class='ct'><span class='ctitle'>{esc(SNAME.get(s['sig'], s['sig']))}</span>"
                     f"<span class='badge num'>{esc(a)} up-prob {s['prob_up']:.0f}%</span></div>"
                     f"<div class='claim'>{esc(s['basis'])}</div>"
                     f"<div class='inv'>&#9888; invalidation: {esc(s['invalid'])}</div></div>")
        if dd.get("bayes") and dd.get("prior") is not None:
            b.append(f"<div class='note'>Composite: mean of {esc(a)} up-probs &rarr; market prior {pct(dd['prior'])} &rarr; "
                     f"Bayesian posterior {pct(dd['prob_up'])} &rarr; <b>{dword(dd['rating'], dd['prob_up'])}</b> ({esc(dd['conf'])} confidence)</div>")
        else:
            b.append(f"<div class='note'>Composite: mean of {esc(a)} up-probs &rarr; {pct(dd['prob_up'])} &rarr; "
                     f"<b>{dword(dd['rating'], dd['prob_up'])}</b> ({esc(dd['conf'])} confidence)</div>")
    else:
        b.append("<div class='card note' style='margin:10px 0'>" +
                 ("No Polymarket leg for this name &mdash; the score comes entirely from video evidence (see Bayesian update above)."
                  if dd.get("watch") else "No odds crossed rule thresholds today &mdash; market leg held neutral.") + "</div>")
    # 4) track record
    acal = calibration(rows, asset=a)
    b.append("<h2>TRACK RECORD ON THIS ASSET</h2>")
    b.append(f"<div class='sub'>{acal[a]['n']} resolved views &middot; hit {acal[a]['hit']}% &middot; mean Brier {acal[a]['brier']}</div>"
             if a in acal else "<div class='sub'>no resolved views yet</div>")
    past = [r for r in rows if r["asset"] == a and r.get("resolved")][-10:]
    if past:
        b.append("<div class='card tblc'><table class='tbl'><colgroup><col style='width:32%'><col style='width:34%'>"
                 "<col style='width:17%'><col style='width:17%'></colgroup><tr><th>date</th><th>view</th><th>ret</th><th>hit</th></tr>")
        for r in reversed(past):
            b.append(f"<tr><td>{r['date']}</td><td>{dword(r['rating'], r.get('prob_up'))}</td><td>{r['ret']:+.1f}%</td>"
                     f"<td class='{'up' if r['hit'] else 'down'}'><b>{'HIT' if r['hit'] else 'MISS'}</b></td></tr>")
        b.append("</table></div>")
    # 5) current view footer card
    cur = [r for r in rows if r["asset"] == a and not r.get("resolved")]
    c = cur[-1] if cur else None
    b.append(f"<h2>CURRENT VIEW</h2><div class='card cv rk-{rc}'><div class='top'><b>{esc(a)}</b>{pill(dd['rating'], dd['prob_up'])}"
             f"<span class='conf'>{esc(dd['conf'])} confidence &middot; up {pct(dd['prob_up'])}</span></div>"
             + (f"<div class='sub'>issued {c['date']} &middot; expires {c['expires']} &middot; resolved by comparing close on expiry vs issue date</div>"
                if c else "<div class='sub'>not yet archived as a dated view &middot; views resolve 90 days after issuance by comparing closes</div>")
             + "</div>")
    return page(f"{a} details", "\n".join(b), back=True)

# ---------- main ----------
def push():
    subprocess.run(["git","add","-A"], cwd=SITE, capture_output=True)
    subprocess.run(["git","commit","-m",f"update {today.isoformat()}"], cwd=SITE, capture_output=True)
    r = subprocess.run(["git","push"], cwd=SITE, capture_output=True, text=True)
    r2 = subprocess.run(["git","push","-f","origin","main:gh-pages"], cwd=SITE, capture_output=True, text=True)
    return (r.returncode, r2.returncode, (r.stderr or r2.stderr or "")[-200:])

if "--render" in sys.argv:
    # narrative/beliefs updated externally; rebuild pages from cached data (watch prices only)
    with open(os.path.join(SITE, "detail_data.json")) as f: DD = json.load(f)
    try:
        with open(os.path.join(SITE, "odds.json")) as f: odds = json.load(f)
    except FileNotFoundError:
        pass
    rows = load()
    BAY.update(run_bayes())
    for a, d in DD.items():
        e = BAY.get(a)
        if e and not e.get("watch"):
            d["prior"] = e["prior"]; d["prob_up"] = e["post"]
            d["rating"] = rating_of(e["post"]); d["bayes"] = True
            d["verdict"], d["view"] = trend_verdict(d["trend"], d["prob_up"])
    WNOTE = "Watched stock named in a tracked macro YouTube video; scored against a neutral prior by video evidence only (no Polymarket leg)."
    for t, e in BAY.items():
        if e.get("watch"):
            pf = {"ret6": e.get("ret6"), "ret1": e.get("ret1"), "dd": e.get("dd"), "last": e.get("last")} \
                 if e.get("ret6") is not None else None
            tr = trend_of(pf); vtxt, vview = trend_verdict(tr, e["post"])
            DD[t] = dict(asset=t, rating=rating_of(e["post"]), conf="low", prob_up=e["post"],
                         prior=e["prior"], bayes=True, watch=True, perf=pf, trend=tr,
                         verdict=vtxt, view=vview, signals=[], expires=None, note=WNOTE)
    for a, d in DD.items():
        fn = f"W_{a}.html" if d.get("watch") else f"{a}.html"
        with open(os.path.join(SITE, "details", fn), "w") as f:
            f.write(detail_html(a, d, rows))
    V = {a: dict(prob_up=d["prob_up"], rating=d["rating"], conf=d["conf"], signals=d["signals"],
                 prior=d.get("prior"), bayes=d.get("bayes"), watch=d.get("watch")) for a, d in DD.items()}
    with open(os.path.join(SITE, "index.html"), "w") as f:
        f.write(index_html(V, rows, calibration(rows), odds))
    rc1, rc2, err = push()
    print(f"render-only: re-rendered {len(DD)} pages; push rc={rc1}/{rc2}; {err if (rc1 or rc2) else 'pushed ok'}")
    sys.exit(0)

fetch()
rows = load()
resolve(rows)
V = views(signals(odds))
DD_raw = build_detail_data(V, rows)          # market priors (un-adjusted) for bayes.py
with open(os.path.join(SITE, "detail_data.json"), "w") as f:
    json.dump(DD_raw, f)
json.dump(odds, open(os.path.join(SITE, "odds.json"), "w"))
BAY.update(run_bayes())                    # posterior = prior odds x decayed evidence LRs
V = apply_bayes(V, BAY)
DD = build_detail_data(V, rows)
new = []
act = latest_active(rows)
for a in V:
    d = V[a]
    prevr = act.get(a)
    if prevr and prevr["rating"] == d["rating"] and prevr["conf"] == d["conf"]:
        continue
    new.append(dict(date=today.isoformat(), asset=a, rating=d["rating"], conf=d["conf"],
                    prob_up=d["prob_up"], prior=d.get("prior"),
                    expires=(today+timedelta(days=HORIZON)).isoformat(),
                    signals=[{k:s[k] for k in ("sig","basis","prob_up","invalid")} for s in d["signals"]],
                    resolved=False))
with open(ARCHIVE, "w") as f:
    for r in rows + new: f.write(json.dumps(r)+"\n")
DD = build_detail_data(V, rows + new)
with open(os.path.join(SITE, "index.html"), "w") as f:
    f.write(index_html(V, rows+new, calibration(rows+new), odds))
for a in V:
    fn = f"W_{a}.html" if V[a].get("watch") else f"{a}.html"
    with open(os.path.join(SITE, "details", fn), "w") as f:
        f.write(detail_html(a, DD[a], rows+new))

rc1, rc2, err = push()
print("views: " + ", ".join(f"{a}={V[a]['rating']}({V[a]['conf']})" + ("*" if V[a].get("watch") else "") for a in V))
print(f"odds: {len(odds)} markets; evidence-adjusted: {len([a for a in V if V[a].get('bayes')])}; view changes: {len(new)}; archive rows: {len(rows)+len(new)}; push rc={rc1}/{rc2}")
print(err if (rc1 or rc2) else "pushed ok")
