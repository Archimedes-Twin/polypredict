# Per-asset folder reorganization brief (for Claude Code)

Follow-up to the logic fixes in this repo. Organize every tracked asset into its own folder with an analysis file. Machine-generated publish paths must NOT change.

## Target layout (repo root = site/)
```
assets/
  README.md            explains the layout and which files are source-of-truth vs derived
  SPY/
    analysis.md        human narrative — exactly the 3 sections (## Performance (6 months), ## Drivers, ## Continue or revert); migrated from narratives/SPY.md via `git mv`
    evidence.json      per-asset belief slice extracted from ../inbox/beliefs.json (evidence items + resolution; watchlist entries include prior/found_in). Written by bayes.py each run; derived, not source of truth
    views.jsonl        that asset's lines from predictions.jsonl (derived copy; master archive stays authoritative)
  ...                  one folder per basket asset AND per watchlist ticker (bare ticker names: SPY, QQQ, SCHD, XLE, ..., NVDA, MSFT, MU, ...)
```

## Rules
- `details/*.html` and `index.html` publish paths stay byte-identical in placement — GitHub Pages links must not break. Generated HTML stays in details/.
- `predict.py`: narrative loader must read `assets/<A>/analysis.md`; keep a temporary fallback to `narratives/<A>.md` for any file not yet migrated. Watchlist narrative path: `assets/<T>/analysis.md`.
- `bayes.py`: after computing, also write each asset's `evidence.json` and append-sync `views.jsonl` (idempotent: regenerate from master files each run, never append duplicates).
- `../inbox/beliefs.json` and `predictions.jsonl` remain the source of truth; folders are organization + human-readable views.
- Do NOT touch ../inbox/playbook/khoo_playbook.md.
- Keep stdlib-only; keep CLI flags working; remember BOTH main and `--render` push — verify with git neutered (you did this before). Never run `git push`. You MAY run `git mv` and `git status`; leave all changes uncommitted.
- Extend `selftest.py`: narrative-path migration read (new + fallback), evidence.json/views.jsonl generation idempotency. Keep all existing tests passing.

## Verify
- `python3 -m py_compile predict.py bayes.py selftest.py`
- `python3 selftest.py` passes
- No-push full render produces index + all detail pages with the same links as before.
Finish with a summary: folders created, files migrated, loader changes, and anything the cron agent must now write differently (e.g. narrative file path change).
