#!/usr/bin/env python3
"""Bayesian belief updater. beliefs.json = evidence items per asset;
posterior = market prior odds x product of decayed likelihood ratios.
Writes site/bayes_data.json consumed by predict.py. stdlib only.

Correlated-evidence rule (evidence groups):
  Items from the SAME video that support the SAME asset + directional claim are not
  independent draws, so they must not multiply. Such items carry a shared `group` id in
  beliefs.json. Each group contributes ONE likelihood ratio: the single strongest decayed LR
  among its active items (max of lr_eff = lr ** decay_weight). The other members are kept
  in the output for auditing (`applied: false`, `absorbed_by: <id>`) but contribute LR 1.
  Items without a `group` stay independent (each applies its own LR, as before).
  A group whose active items point in different directions is a data error -> ValueError,
  so bayes.py exits nonzero and predict.py refuses to publish."""
import json, math, os, sys, time, urllib.request, urllib.parse, datetime
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
    """Odds update with exponential half-life decay per item. Grouped items (same `group`
    id) count once: only the strongest decayed LR in the group is applied (module docstring)."""
    p = min(max(prior_p, 2), 98)
    odds = p / (100 - p)
    used, groups = [], {}
    for it in items:
        age = (today - datetime.date.fromisoformat(it["date"])).days
        hl = it.get("half_life", 60)
        w = 0.5 ** (age / hl) if hl else 0
        if w < 0.05 or age < 0:
            continue
        lr = math.exp(math.log(it["lr"]) * w)
        u = dict(it, lr_eff=round(lr, 2), age=age, _lr=lr)
        used.append(u)
        if it.get("group"):
            groups.setdefault(it["group"], []).append(u)
    for gid, g in groups.items():
        dirs = sorted({u["dir"] for u in g})
        if len(dirs) > 1:
            raise ValueError(f"evidence group {gid!r} mixes directions {dirs}: {[u.get('id') for u in g]}")
        best = max(g, key=lambda u: u["_lr"])          # first listed item wins ties
        for u in g:
            u["applied"] = u is best
            u["group_n"] = len(g)
            if u is not best:
                u["absorbed_by"] = best.get("id")
    for u in used:
        lr = u.pop("_lr")
        if u.get("applied", True):
            odds = odds * lr if u["dir"] == "up" else odds / lr
    p = 100 * odds / (1 + odds)
    return min(max(p, 2), 98), used

def main():
    t_start = time.time()
    with open(BELIEFS) as f: beliefs = json.load(f)
    detail = {}
    if os.path.exists(DETAIL):
        with open(DETAIL) as f: detail = json.load(f)
    today = datetime.date.today()
    out = {}
    for asset, b in beliefs.get("assets", {}).items():
        prior = detail.get(asset, {}).get("prob_up")
        if prior is None:
            print(f"warning: bayes: evidence for {asset} skipped (no market prior in detail_data.json)",
                  file=sys.stderr)
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
    for a, d in sorted(out.items()):
        print(f"{a:6s} prior {d['prior']:.0f}% -> posterior {d['post']:.0f}%  ({len(d['items'])} active evidence)")
    # freshness stamp: predict.py rejects output generated before its own run started
    out["_meta"] = {"generated_at": time.time(), "started_at": t_start, "run_date": today.isoformat()}
    tmp = OUT + ".tmp"
    with open(tmp, "w") as f: json.dump(out, f, indent=2)
    os.replace(tmp, OUT)

if __name__ == "__main__":
    main()
