#!/usr/bin/env python3
"""Polymarket -> ETF prediction engine with falsifiable predictions + calibration.
Stdlib-only. Reads Polymarket odds (Gamma API), emits directional predictions on the
brief basket, resolves past predictions against Yahoo closes, scores hit-rate/Brier per
signal, and regenerates a static site at site/index.html (+ site/archive.jsonl)."""
import json, os, re, subprocess, time, urllib.parse, urllib.request
from datetime import date, datetime, timedelta, timezone

HOME = os.path.expanduser("~")
BASE = os.path.join(HOME, "signals/polypredict")
SITE = os.path.join(BASE, "site")
ARCHIVE = os.path.join(SITE, "archive.jsonl")
os.makedirs(SITE, exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0"}
GAMMA = "https://gamma-api.polymarket.com"

# ---------- helpers (shared shape with daily_macro_brief) ----------
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

def when_key(question):
    mm = re.search(r"(\w+) (\d{4})", question.lower())
    if mm and mm.group(1) in MON: return int(mm.group(2)) * 12 + MON[mm.group(1)]
    return None

# ---------- collect today's odds ----------
odds = {}
def fetch():
    # next Fed meeting: hike/cut
    fed = [e for e in poly_search("Fed Decision")
           if re.match(r"Fed [Dd]ecision in \w+\??$", e.get("title","")) and not e.get("closed")]
    def fed_when(e):
        for m in poly_open(e):
            k = when_key(m.get("question",""))
            if k: return k
    fed = [e for e in fed if fed_when(e)]
    if fed:
        e = min(fed, key=fed_when)
        hike = sum(y for m in poly_open(e) if "increase" in m.get("question","") and (y:=poly_yes(m)) is not None)
        cut  = sum(y for m in poly_open(e) if "decrease" in m.get("question","") and (y:=poly_yes(m)) is not None)
        k = fed_when(e)
        odds["fed_hike"] = (hike, f"hike at {MON3[(k-1)%12]} '{str((k-1)//12)[2:]} meeting")
    # Fed rate cut by <date> chain
    for e in poly_search("Fed rate cut by"):
        if e.get("closed"): continue
        for m in poly_open(e):
            mm = re.search(r"by (\w+) (\d{4})", m.get("question",""))
            if mm and mm.group(1).lower() in MON and (y:=poly_yes(m)) is not None:
                k = int(mm.group(2))*12 + MON[mm.group(1).lower()]
                if k == 2027*12+7: odds["cut_jul27"] = (y, "Fed cut by July 2027 meeting")
                elif k == 2027*12+3: odds["cut_mar27"] = (y, "Fed cut by March 2027 meeting")
        break
    # WTI monthly ladder: dip under 90 / spike over 120 (earliest open month)
    lads = [e for e in poly_search("WTI Crude Oil hit")
            if re.search(r"hit in \w+ \d{4}", e.get("title","")) and not e.get("closed")]
    if lads:
        e = min(lads, key=lambda x: when_key(x.get("title","")) or 99999)
        for m in poly_open(e):
            if re.search(r"\(LOW\) \$90\b", m.get("question","")) and (y:=poly_yes(m)) is not None:
                odds["wti_dip90"] = (y, f"{m.get('question','')}")
            if re.search(r"\(HIGH\) \$120\b", m.get("question","")) and (y:=poly_yes(m)) is not None:
                odds["wti_spike120"] = (y, f"{m.get('question','')}")
    # US-Iran ceasefire through Dec 31 (peace probability; break = 100-p)
    for e in poly_search("US-Iran ceasefire"):
        if e.get("closed"): continue
        for m in poly_open(e):
            if "December 31" in m.get("question","") and (y:=poly_yes(m)) is not None:
                odds["iran_peace_dec"] = (y, m.get("question",""))
        break
    # recession by end of 2026 / 2027
    for e in poly_search("US recession by end"):
        if e.get("closed"): continue
        t = e.get("title","")
        for m in poly_open(e):
            if (y:=poly_yes(m)) is None: continue
            if t.strip() == "US recession by end of 2026?": odds["rec26"] = (y, t)
            if t.strip() == "US recession by end of 2027?": odds["rec27"] = (y, t)
    # core CPI hot (earliest open month, buckets >=0.3 exclusive)
    cpi = [e for e in poly_search("Core CPI MoM")
           if re.match(r"Core CPI MoM - \w+ \d{4}", e.get("title","")) and not e.get("closed")]
    if cpi:
        e = min(cpi, key=lambda x: when_key(x.get("title","")) or 99999)
        tot = 0.0
        for m in poly_open(e):
            n = re.search(r"(\d+\.\d+)%", m.get("question",""))
            if n and float(n.group(1)) >= 0.3 and "or less" not in m.get("question","") and (y:=poly_yes(m)) is not None:
                tot += y
        odds["cpi_hot"] = (tot, e.get("title",""))

# ---------- rules: odds -> directional predictions ----------
BASKET = {"SPY","QQQ","SCHD","XLE","XLF","XLC","XLY","XLP","XLV","ZROZ","HYG","SCHD"}
RULES = []  # (signal, prob, label, [(asset, dir)], horizon_days, invalidation)
def build_rules(o):
    R = []
    if "fed_hike" in o:
        p, lbl = o["fed_hike"]
        if p >= 55:
            R.append(("fed_hike", p, lbl, [("QQQ","down"),("XLC","down"),("SCHD","up")], 30,
                      f"fed_hike falls under 45% before meeting"))
        elif p <= 35:
            R.append(("fed_hike", 100-p, lbl, [("QQQ","up"),("XLC","up"),("SCHD","down")], 30,
                      f"fed_hike climbs back over 55%"))
    if "cut_jul27" in o:
        p, lbl = o["cut_jul27"]
        if p >= 50:
            R.append(("cut_jul27", p, lbl, [("ZROZ","up"),("QQQ","up"),("SCHD","down")], 90,
                      f"cut_jul27 drops under 40%"))
    if "wti_dip90" in o:
        p, lbl = o["wti_dip90"]
        if p >= 55:
            R.append(("wti_dip90", p, lbl, [("XLE","down"),("SCHD","down")], 30,
                      f"dip90 odds fall under 45% (oil shock re-ignites)"))
        elif p <= 40:
            R.append(("wti_dip90", 100-p, lbl, [("XLE","up")], 30,
                      f"dip90 odds climb over 55%"))
    if "wti_spike120" in o:
        p, lbl = o["wti_spike120"]
        if p >= 15:
            R.append(("wti_spike120", p, lbl, [("XLE","up")], 30,
                      f"spike120 odds fall under 10%"))
    if "iran_peace_dec" in o:
        p, lbl = o["iran_peace_dec"]
        if 100-p >= 55:
            R.append(("iran_break", 100-p, lbl, [("XLE","up"),("SPY","down")], 45,
                      f"ceasefire-hold odds recover above 55%"))
    if "rec27" in o:
        p, lbl = o["rec27"]
        if p >= 45:
            R.append(("rec27", p, lbl, [("ZROZ","up"),("XLV","up"),("XLF","down"),("HYG","down")], 90,
                      f"rec27 falls under 35%"))
        elif p <= 25:
            R.append(("rec27", 100-p, lbl, [("HYG","up"),("XLF","up")], 90,
                      f"rec27 climbs over 35%"))
    if "cpi_hot" in o:
        p, lbl = o["cpi_hot"]
        if p >= 55:
            R.append(("cpi_hot", p, lbl, [("QQQ","down"),("XLC","down")], 30,
                      f"cpi_hot falls under 45%"))
    return R

# ---------- archive: append new, resolve expired ----------
def load_archive():
    rows = []
    if os.path.exists(ARCHIVE):
        with open(ARCHIVE) as f:
            for line in f:
                try: rows.append(json.loads(line))
                except json.JSONDecodeError: pass
    return rows

today = date.today()
series_cache = {}
def series(tk):
    if tk not in series_cache:
        try: series_cache[tk] = yahoo_series(tk)
        except Exception: series_cache[tk] = None
    return series_cache[tk]

def resolve(rows):
    changed = False
    for r in rows:
        if r.get("resolved") or r["expires"] > today.isoformat(): continue
        s = series(r["asset"])
        if not s: continue
        a = close_on(s, date.fromisoformat(r["date"]))
        b = close_on(s, min(date.fromisoformat(r["expires"]), s[-1][0]))
        up = b >= a
        outcome = 1 if up else 0
        prob_up = r["prob"] if r["dir"] == "up" else 100 - r["prob"]
        r["resolved"] = True
        r["outcome"] = outcome
        r["hit"] = (r["dir"] == "up") == up
        r["ret"] = round((b/a-1)*100, 2)
        r["brier"] = round(((prob_up/100)-outcome)**2, 3)
        changed = True
    return changed

def active_keys(rows):
    ks = set()
    for r in rows:
        if not r.get("resolved") and r["expires"] >= today.isoformat():
            ks.add((r["signal"], r["asset"], r["dir"]))
    return ks

def calibration(rows):
    agg = {}
    for r in rows:
        if not r.get("resolved"): continue
        a = agg.setdefault(r["signal"], {"n":0,"hits":0,"brier":0.0})
        a["n"] += 1; a["hits"] += 1 if r["hit"] else 0; a["brier"] += r["brier"]
    return {k: {"n":v["n"], "hit":round(100*v["hits"]/v["n"]), "brier":round(v["brier"]/v["n"],3)} for k,v in agg.items()}

# ---------- html ----------
def esc(x): return str(x).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def html(active, resolved_rows, cal, o):
    H = [f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
         f"<title>Polymarket -> ETF signals</title><style>"
         "body{background:#0d1117;color:#c9d1d9;font-family:ui-monospace,Menlo,monospace;margin:18px;font-size:14px}"
         "h1{font-size:18px;color:#58a6ff}h2{font-size:15px;color:#8b949e;margin-top:26px}"
         "table{border-collapse:collapse}td,th{padding:4px 10px;border-bottom:1px solid #21262d;text-align:left}"
         ".up{color:#3fb950}.down{color:#f85149}.dim{color:#8b949e}"
         "</style></head><body><h1>Polymarket &#8594; ETF signals &mdash; " + today.isoformat() + "</h1>"]
    H.append("<h2>ACTIVE PREDICTIONS</h2><table><tr><th>asset</th><th>dir</th><th>prob</th><th>signal</th><th>basis</th><th>expires</th><th>invalidation</th></tr>")
    for r in sorted(active, key=lambda x: x["asset"]):
        cls = "up" if r["dir"]=="up" else "down"
        H.append(f"<tr><td>{esc(r['asset'])}</td><td class='{cls}'>{r['dir']}</td><td>{r['prob']:.0f}%</td>"
                 f"<td>{esc(r['signal'])}</td><td class='dim'>{esc(r['basis'])}</td><td>{r['expires']}</td><td class='dim'>{esc(r.get('invalid',''))}</td></tr>")
    if not active: H.append("<tr><td colspan='7' class='dim'>no active predictions (no odds crossed thresholds)</td></tr>")
    H.append("</table>")
    H.append("<h2>CALIBRATION (resolved)</h2><table><tr><th>signal</th><th>n</th><th>hit%</th><th>mean Brier</th></tr>")
    for k, v in sorted(cal.items(), key=lambda kv: -kv[1]["n"]):
        H.append(f"<tr><td>{esc(k)}</td><td>{v['n']}</td><td>{v['hit']}%</td><td>{v['brier']}</td></tr>")
    if not cal: H.append("<tr><td colspan='4' class='dim'>nothing resolved yet</td></tr>")
    H.append("</table>")
    H.append("<h2>RESOLVED (last 30)</h2><table><tr><th>expires</th><th>asset</th><th>dir</th><th>prob</th><th>ret%</th><th>hit</th><th>signal</th></tr>")
    res = [r for r in resolved_rows if r.get("resolved")][-30:]
    for r in reversed(res):
        H.append(f"<tr><td>{r['expires']}</td><td>{esc(r['asset'])}</td><td>{r['dir']}</td><td>{r['prob']:.0f}%</td>"
                 f"<td>{r['ret']:+.1f}</td><td class='{'up' if r['hit'] else 'down'}'>{'HIT' if r['hit'] else 'MISS'}</td><td class='dim'>{esc(r['signal'])}</td></tr>")
    H.append("</table>")
    H.append("<h2>ODDS SNAPSHOT</h2><table><tr><th>market</th><th>prob</th></tr>")
    for k, (v, lbl) in sorted(o.items()):
        H.append(f"<tr><td>{esc(lbl)}</td><td>{v:.1f}%</td></tr>")
    H.append("</table>")
    H.append(f"<p class='dim'>generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} by ~/signals/polypredict/predict.py &mdash; crowd odds from Polymarket Gamma API, prices from Yahoo. Rules-based, falsifiable, self-scoring.</p>")
    H.append("</body></html>")
    return "\n".join(H)

# ---------- main ----------
fetch()
rows = load_archive()
resolve(rows)
new = []
if rules := build_rules(odds):
    act = active_keys(rows)
    for sig, p, basis, targets, horizon, invalid in rules:
        for asset, d in targets:
            if asset not in BASKET: continue
            if (sig, asset, d) in act: continue
            new.append(dict(date=today.isoformat(), signal=sig, asset=asset, dir=d,
                            prob=round(p,1), basis=basis, expires=(today+timedelta(days=horizon)).isoformat(),
                            invalid=invalid, resolved=False))
with open(ARCHIVE, "w") as f:
    for r in rows + new: f.write(json.dumps(r)+"\n")
with open(os.path.join(SITE, "index.html"), "w") as f:
    f.write(html([r for r in rows+new if not r.get("resolved")], rows, calibration(rows+new), odds))

# publish
def sh(cmd):
    subprocess.run(cmd, cwd=SITE, capture_output=True)
sh(["git","add","-A"])
subprocess.run(["git","commit","-m",f"update {today.isoformat()}"], cwd=SITE, capture_output=True)
r = subprocess.run(["git","push"], cwd=SITE, capture_output=True, text=True)
r2 = subprocess.run(["git","push","-f","origin","main:gh-pages"], cwd=SITE, capture_output=True, text=True)
print(f"odds: {len(odds)} markets; new preds: {len(new)}; archive rows: {len(rows)+len(new)}; push rc={r.returncode}/{r2.returncode}")
print((r.stderr or r2.stderr)[-300:] if (r.returncode or r2.returncode) else "pushed ok (main + gh-pages)")
