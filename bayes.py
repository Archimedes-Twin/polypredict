#!/usr/bin/env python3
"""Bayesian belief updater. beliefs.json = evidence items per asset;
posterior = market prior odds x product of decayed likelihood ratios.
Writes site/bayes_data.json consumed by predict.py. stdlib only."""
import json, math, os, urllib.request, urllib.parse, datetime
HERE = os.path.dirname(os.path.abspath(__file__))
BELIEFS = os.path.join(HERE, "..", "inbox", "beliefs.json")
DETAIL = os.path.join(HERE, "detail_data.json")
OUT = os.path.join(HERE, "bayes_data.json")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"}

def yahoo6(sym):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?range=6mo&interval=1d"
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
        d = json.load(r)["chart"]["result"][0]
    q = d["indicators"]["quote"][0]; ts = d["timestamp"]
    closes = [(datetime.datetime.fromtimestamp(t).date(), c) for t, c in zip(ts, q["close"]) if c]
    return closes

def posterior(prior_p, items, today):
    """odds update with exponential half-life decay per item."""
    p = min(max(prior_p, 2), 98)
    odds = p / (100 - p)
    used = []
    for it in items:
        age = (today - datetime.date.fromisoformat(it["date"])).days
        hl = it.get("half_life", 60)
        w = 0.5 ** (age / hl) if hl else 0
        if w < 0.05 or age < 0:
            continue
        lr = math.exp(math.log(it["lr"]) * w)
        odds = odds * lr if it["dir"] == "up" else odds / lr
        used.append({**it, "lr_eff": round(lr, 2), "age": age})
    p = 100 * odds / (1 + odds)
    return min(max(p, 2), 98), used

def main():
    beliefs = json.load(open(BELIEFS))
    detail = json.load(open(DETAIL)) if os.path.exists(DETAIL) else {}
    today = datetime.date.today()
    out = {}
    for asset, b in beliefs.get("assets", {}).items():
        prior = detail.get(asset, {}).get("prob_up")
        if prior is None:
            continue
        post, used = posterior(prior, b.get("evidence", []), today)
        out[asset] = {"prior": prior, "post": round(post, 1), "items": used,
                      "resolution": b.get("resolution", ""), "source": "market prior + video evidence"}
    for t, w in beliefs.get("watchlist", {}).items():
        try:
            closes = yahoo6(t)
            last = closes[-1][1]; ret6 = round(100 * (last / closes[0][1] - 1), 1)
            peak = max(c for _, c in closes); peakd = max(d for d, c in closes if c == peak)
            dd = round(100 * (last / peak - 1), 1)
            prev21 = closes[max(0, len(closes) - 22)][1]
            ret1 = round(100 * (last / prev21 - 1), 1)
        except Exception:
            last = ret6 = dd = ret1 = None
        post, used = posterior(w.get("prior", 50), w.get("evidence", []), today)
        out[t] = {"prior": w.get("prior", 50), "post": round(post, 1), "items": used,
                  "resolution": w.get("note", ""), "source": "neutral prior + video evidence",
                  "watch": True, "last": round(last, 2) if last else None,
                  "ret6": ret6, "ret1": ret1, "dd": dd,
                  "added": w.get("added", ""), "found_in": w.get("source", "")}
    json.dump(out, open(OUT, "w"), indent=2)
    for a, d in sorted(out.items()):
        print(f"{a:6s} prior {d['prior']:.0f}% -> posterior {d['post']:.0f}%  ({len(d['items'])} active evidence)")

if __name__ == "__main__":
    main()
