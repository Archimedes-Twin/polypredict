# Logic-fix brief (for Claude Code)

Same repo. `REVIEW.md` has the findings; fix findings 1-6 now. Engine: `predict.py` + `bayes.py`, stdlib-only Python (no pip deps). Belief state lives at `../inbox/beliefs.json` (note: bayes.py reads ../inbox/beliefs.json, NOT beliefs.json in this folder).

## Fixes to implement

1. **Oil-rule sign bug + clamp** (`signals()`): `wti_dip90` currently makes XLE *bullish* when dip-below-$90 odds are in the mid band — the up-probability must DECREASE monotonically as dip odds increase. Same direction logic for all oil rules. Clamp every per-signal up-prob to [1,99] (the $120-spike rule can exceed 100).
2. **Threshold cliffs**: replace step-jump mappings with piecewise-linear interpolation over each rule's defined breakpoints so a 53% coin-flip market produces ~50%, not 76% "very bullish". Keep each rule's intent (direction, saturation extremes) — continuity only.
3. **Confidence after Bayes**: recompute `confidence_of` after `apply_bayes()` from the posterior + signal agreement; "neutral" posteriors must not carry "high confidence" unless evidence is genuinely strong and agreeing.
4. **Correlated evidence merging** (`bayes.py`): evidence items from the SAME video supporting the SAME asset+claim must contribute ONE likelihood ratio, not a product of N. Group items (add explicit `group` id in beliefs.json entries; items without a group stay independent as today). For a group, apply the single strongest LR (max), document the rule in the docstring. Update `../inbox/beliefs.json` to group the known SPY items from one video (there are other multi-item groups — apply consistently).
5. **Calibration scoring**: archive must contain ONE scored view per asset per day — the POSTERIOR that the site shows. Add a `raw: true` flag to pre-Bayes archive rows and exclude raw rows from hit-rate/Brier calibration (keep them for auditing). Resolution/calibration must use TOTAL return: Yahoo v8 chart also returns `adjclose` — use it where available, fall back to `close`.
6. **Fail loud**: (a) missing Polymarket markets (e.g. rec27) must print a warning AND add a visible note on the index page; (b) if `bayes.py` fails or its output is older than today's run, the pipeline must raise/exit nonzero — never silently reuse yesterday's posteriors; (c) when a narrative file's wording contradicts the current posterior rating (e.g. body says "very bearish" while posterior is neutral), the rendered detail page must show a visible "narrative stale — view changed since it was written" banner.

## Constraints
- stdlib only; no new files needed except tests.
- Do NOT redesign UI (already done); banners/notes may be added minimally using existing CSS classes.
- Preserve CLI: `--render` must keep working; remember `--render` and main BOTH push — never let your verification push: run python with GIT_DIR pointed at a nonexistent dir or wrap subprocess git calls (you did this before).
- Do NOT run `git commit` or `git push`. Leave changes uncommitted.
- `predictions.jsonl` history rows: do not rewrite existing lines; only new writes get the `raw` flag (calibration must skip legacy lines lacking the flag that duplicate a posterior on the same day — dedupe by taking the posterior line per asset+date when both exist).
- Add a self-test block runnable via `python3 selftest.py` (no network): oil-rule monotonicity, clamps, interpolation continuity, confidence-after-bayes, evidence-group merging math, calibration dedupe. Make it pass.

## Verification before you finish
- `python3 -m py_compile predict.py bayes.py selftest.py`
- `python3 selftest.py` passes
- One no-push full-pipeline run (git neutered) shows: sane ratings, one archive row scored per asset, missing-market note present or absent honestly, and SPY confidence consistent with its posterior.
