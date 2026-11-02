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
  so bayes.py exits nonzero and predict.py refuses to publish.

Per-asset folders (assets/<A>/, see assets/README.md) are synced by sync_asset_folders():
  evidence.json  belief slice for <A> from ../inbox/beliefs.json   (derived, rewritten each run)
  views.jsonl    <A>'s lines from predictions.jsonl, verbatim       (derived, rewritten each run)
  analysis.md    human narrative; only a placeholder is created when missing, never overwritten
Regeneration is from the master files every time, so repeated runs never duplicate lines."""
import json, math, os, sys, time, urllib.request, urllib.parse, datetime
HERE = os.path.dirname(os.path.abspath(__file__))
BELIEFS = os.path.join(HERE, "..", "inbox", "beliefs.json")
DETAIL = os.path.join(HERE, "detail_data.json")
OUT = os.path.join(HERE, "bayes_data.json")
ARCHIVE = os.path.join(HERE, "predictions.jsonl")
ASSETS = os.path.join(HERE, "assets")
NARR_LEGACY = os.path.join(HERE, "narratives")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"}

# ---------- per-asset folders ----------
ANALYSIS_SECTIONS = ("## Performance (6 months)", "## Drivers", "## Continue or revert")
PLACEHOLDER_MARK = "<!-- analysis-placeholder"
PLACEHOLDER = (PLACEHOLDER_MARK + ": not written yet. Replace this WHOLE file (marker line included) with "
               "the three sections below; until then the site shows an auto-generated fallback. -->\n\n"
               + "\n\n".join(ANALYSIS_SECTIONS) + "\n")

def evidence_slice(asset, beliefs):
    """Per-asset belief slice (derived view of beliefs.json; edit beliefs.json, not this)."""
    w = beliefs.get("watchlist", {}).get(asset)
    if w is not None:
        return {"asset": asset, "kind": "watchlist", "derived": True, "generated_by": "bayes.py",
                "source_of_truth": f"../inbox/beliefs.json -> watchlist.{asset}",
                "prior": w.get("prior", 50), "found_in": w.get("source", ""), "added": w.get("added", ""),
                "note": w.get("note", ""), "evidence": w.get("evidence", [])}
    b = beliefs.get("assets", {}).get(asset, {})
    return {"asset": asset, "kind": "basket", "derived": True, "generated_by": "bayes.py",
            "source_of_truth": f"../inbox/beliefs.json -> assets.{asset}",
            "evidence": b.get("evidence", []), "resolution": b.get("resolution", "")}

def _write_if_changed(path, text):
    try:
        with open(path) as f:
            if f.read() == text: return False
    except FileNotFoundError:
        pass
    tmp = path + ".tmp"
    with open(tmp, "w") as f: f.write(text)
    os.replace(tmp, path)
    return True

def sync_asset_folders(beliefs, basket, archive_path=ARCHIVE, assets_dir=ASSETS, narr_dir=NARR_LEGACY):
    """Create/refresh assets/<A>/ for every basket asset and watchlist ticker. evidence.json and
    views.jsonl are regenerated wholesale from the master files (idempotent: identical input ->
    identical bytes, no file rewritten). analysis.md is never overwritten; a placeholder is created
    only if neither assets/<A>/analysis.md nor a not-yet-migrated narratives/<A>.md exists.
    Returns {"written": [...paths], "placeholders": [...assets]}."""
    tracked = list(dict.fromkeys(list(basket) + list(beliefs.get("watchlist", {}))))
    lines = {}
    if os.path.exists(archive_path):
        with open(archive_path) as f:
            for line in f:
                s = line.rstrip("\n")
                if not s.strip(): continue
                try: a = json.loads(s).get("asset")
                except json.JSONDecodeError: continue
                lines.setdefault(a, []).append(s + "\n")
    written, placeholders = [], []
    for a in tracked:
        d = os.path.join(assets_dir, a)
        os.makedirs(d, exist_ok=True)
        ev = json.dumps(evidence_slice(a, beliefs), indent=2, ensure_ascii=False) + "\n"
        for name, text in (("evidence.json", ev), ("views.jsonl", "".join(lines.get(a, [])))):
            if _write_if_changed(os.path.join(d, name), text):
                written.append(os.path.join(d, name))
        an = os.path.join(d, "analysis.md")
        if not os.path.exists(an) and not os.path.exists(os.path.join(narr_dir, f"{a}.md")):
            with open(an, "w") as f: f.write(PLACEHOLDER)
            placeholders.append(a)
    return {"written": written, "placeholders": placeholders}

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
    # per-asset folders (derived views); predict.py re-syncs after it appends to the archive
    basket = [a for a, d in detail.items() if not d.get("watch")]
    sync_asset_folders(beliefs, basket)

if __name__ == "__main__":
    main()
