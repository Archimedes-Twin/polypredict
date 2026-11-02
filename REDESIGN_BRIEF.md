# Polypredict redesign brief (for Claude Code)

Repo: ~/signals/polypredict/site — publishes GitHub Pages at https://archimedes-twin.github.io/polypredict/
Engine: `predict.py` (Python, stdlib-ONLY — no pip deps allowed), belief updater `bayes.py`.
Pipeline each day: fetch Polymarket odds -> rules-based per-signal up-probs -> composite per-asset view -> `detail_data.json` (RAW market prior; bayes.py reads it and must see raw values) -> bayes.py (video evidence posteriors) -> apply_bayes -> predictions.jsonl archive + calibration -> build_index/build_detail -> git push main + gh-pages.

## Task 1 — review the analysis flow (write findings, change no logic)
Read predict.py, bayes.py, beliefs.json, detail_data.json, predictions.jsonl, odds.json.
Write `REVIEW.md` in this directory: weaknesses and concrete improvement suggestions for the odds->views->bayes->archive->calibration flow (e.g. signal mapping quality, LR decay choices, calibration stats design, conflict handling, failure modes). Findings only — do NOT edit logic.

## Task 2 — redesign the UI
Edit ONLY the HTML/CSS template code in predict.py (`build_index`, `build_detail`, CSS strings). Make it a modern, polished, mobile-first dark design: clear hierarchy, rating color coding, restrained typography (system fonts only).

### Hard constraints — these are the user's explicit requirements; do not regress them
- Index: exactly ONE asset per row. Direction words ONLY: VERY BULLISH / BULLISH / NEARLY NEUTRAL or NEUTRAL / BEARISH / VERY BEARISH. Confidence: only high / medium / low. Each row has a "Details" link to details/<A>.html.
- Rows that were bayes-adjusted show "market prior <raw>% → <posterior>%". Keep the VIDEO-EVIDENCE WATCHLIST section with links to details/W_<TICKER>.html. Keep footer "last updated".
- Detail page order: narrative FIRST (## PERFORMANCE (6 MONTHS) / ## DRIVERS / ## CONTINUE OR REVERT), then BAYESIAN UPDATE card (prior->posterior + evidence cards with source/title/date/LR/age/decay), then ODDS EVIDENCE as stacked CARDS (never a wide table; wide tables are unreadable on phone — this was a user complaint), then track record table, then "current view" footer card.
- Mobile: @media (max-width: 720px) must make everything stack cleanly with NO horizontal scrolling. The page is viewed mostly on phones.
- No external frameworks, fonts, CDNs, images, or JS beyond what exists. Inline CSS only.
- Do NOT change any data logic or schemas: prob computation, apply_bayes, detail_data.json raw-prior semantics, predictions.jsonl fields, CLI flags (--render), the git push flow.

### Safety rules
- NEVER run `python3 predict.py` without `--render` (the main run pushes to GitHub).
- NEVER run git commit/push.
- Verify with: `python3 -m py_compile predict.py` and `python3 predict.py --render`, then inspect generated index.html and details/*.html.
