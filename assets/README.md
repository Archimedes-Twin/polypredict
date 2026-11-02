# assets/ — one folder per tracked asset

Every basket ETF (SPY, QQQ, SCHD, XLE, XLF, XLC, XLY, XLP, XLV, ZROZ, HYG) and every
video-evidence watchlist ticker (NVDA, MU, AMD, INTC, ARM, SNDK, GOOGL, META, AMZN, MSFT, ...)
has a folder named by its bare ticker:

```
assets/<TICKER>/
  analysis.md     human narrative                         SOURCE OF TRUTH (hand/agent-written)
  evidence.json   belief slice from ../inbox/beliefs.json  DERIVED - rewritten by bayes.py
  views.jsonl     this asset's lines of predictions.jsonl  DERIVED - rewritten by bayes.py/predict.py
```

## Source of truth vs derived

| file | authoritative source | who writes it |
|---|---|---|
| `analysis.md` | itself | the narrative writer (cron agent / human) |
| `evidence.json` | `../inbox/beliefs.json` (`assets.<A>` or `watchlist.<T>`) | `bayes.py` every run |
| `views.jsonl` | `predictions.jsonl` (master archive, repo root) | `bayes.py` + `predict.py` every run |

- **Never edit `evidence.json` or `views.jsonl`.** They are regenerated wholesale from the master
  files on every run (idempotent: same input gives the same bytes, and no line is ever duplicated). Edit
  `../inbox/beliefs.json` for evidence. The archive is append-only and owned by `predict.py`.
- Watchlist `evidence.json` also carries `prior`, `found_in` (source video), `added` and `note`.

## analysis.md format

Exactly three sections, in this order:

```
## Performance (6 months)

...

## Drivers

...

## Continue or revert

...
```

- `predict.py` renders it first on `details/<A>.html` (watchlist: `details/W_<T>.html`) when the
  file is at most 3 days old (by mtime); otherwise it shows an auto-generated fallback.
- A file starting with `<!-- analysis-placeholder` is a stub created for assets that have no
  narrative yet; it is ignored (fallback shown). Overwrite the whole file, marker included.
- If the text asserts a rating ("composite very bearish", "neutral (low confidence)",
  "composite up-prob 28%") that differs from the current posterior, the page shows a
  "narrative stale" banner.
- Legacy path `narratives/<A>.md` is still read as a temporary fallback (with a warning) when
  `analysis.md` is missing/placeholder or older than the legacy file. Do not write there any more.

## Publish paths (unchanged)

Generated HTML stays at `index.html` and `details/*.html`; nothing under `assets/` is linked
from the site.
