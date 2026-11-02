# Polypredict — analysis-flow review

Scope: `predict.py`, `bayes.py`, `../inbox/beliefs.json`, `detail_data.json`, `bayes_data.json`,
`predictions.jsonl`, `odds.json`, as of 2026-11-02. This is a list of findings only. No logic was changed.
Line numbers refer to `predict.py` unless noted. Items are ordered by severity within each section.

> **Path note:** the brief points to `beliefs.json` in this directory. `bayes.py` actually reads
> `../inbox/beliefs.json` (`BELIEFS = HERE/../inbox/beliefs.json`). No `beliefs.json` exists in `site/`.

---

## 0. Operational issue (fix first)

**`--render` pushes to GitHub.** The `--render` branch (l.476–510) ends with `push()`, which runs
`git add -A`, `git commit`, `git push`, then **`git push -f origin main:gh-pages`**. The redesign
brief describes `--render` as a safe local preview ("NEVER run without `--render` (the main run pushes)").
That is wrong: both modes push, and both force-push gh-pages. Anyone who follows the brief will publish
to the live site.
*Suggestion:* add a `--no-push` flag (or make `--render` local-only and add `--publish`). Also, drop the
`-f` on the gh-pages push, or guard it.

---

## 1. Odds → per-signal up-probabilities (`signals()`, l.107–150)

1. **Sign error in `wti_dip90`.** When dip-under-$90 odds are ≥55% the rule sets XLE/SCHD up-prob to
   `100 − 0.8p`. That gives 56% at p=55, 52% at p=60 and 50% at p=62.5. So in the 55–62.5% band, high
   odds of an oil *dip* make XLE **bullish**. The intended direction only appears above 62.5%.
   The likely intent was `50 − (p−50)·k`.
2. **Formulas aren't centred, so there are big jumps at the thresholds.** Most rules use `50 + p·k`
   instead of `50 + (p−threshold)·k`:
   - `cut_jul27`: 49% odds → no signal (50); 50% odds → **75%** up-prob. Today a 53% (coin-flip) market
     alone makes **ZROZ "very bullish, high confidence" at 76%**.
   - `rec27` at 45% → 68%. `iran_break` at 55% → 72%. `wti_spike120` at 15% → 59%. `cpi_hot` at 55% → 30.8%.
   - A one-point move in a single market can therefore swing an asset by about 25 points and flip two
     rating buckets. This also writes a new archive row (see §4).
3. **`wti_spike120` can exceed 100%.** `50 + 0.6p` goes above 100 once p > 83.3, e.g. 104 at p=90.
   No rule clamps its output.
4. **Mixed sensitivities.** `fed_hike` maps the event probability 1:1 into the asset probability
   (72% hike → SCHD 72% up, QQQ 28% up). Other rules use multipliers of 0.35–0.6. These multipliers aren't
   derived from anything (no historical betas or hit rates), so the composite's scale is arbitrary.
   *Suggestion:* use one documented transform per rule, `p_up = 50 + β·(p_event − 50)`, and clamp to
   [5, 95]. Fit β against the archive once enough rows resolve.
5. **The invalidation text doesn't match the rule.** Each signal says "revises if hike odds fall under 45%",
   but the code thresholds are 55/35 with nothing in between. Between 35% and 55% the signal simply
   disappears, so there's no hysteresis. The invalidation strings are never checked against
   anything. *Suggestion:* store the signal state and apply the stated hysteresis, or make the text
   match the real thresholds.
6. **Horizon mismatch.** Every view is 90 days, but the inputs have very different horizons:
   - `fed_hike` uses the *nearest* meeting, which could be days away, and then rolls to the next
     meeting, causing a jump.
   - The WTI ladder uses the *nearest month* (the "November" market), whose probability collapses
     toward 0 or 100 as the month ends.
   - `cut_jul27` is hard-coded to July 2027 (l.73) and will drift and then expire.
   *Suggestion:* choose markets by "expiry closest to today+90d". Record the market's horizon with
   each signal.
7. **Coverage gaps.** XLY and XLP appear in **no** rule. XLF, XLV and HYG only get a signal from `rec27`,
   and `rec27` is **missing from today's `odds.json`** (see §6.1). So 5 of 11 basket assets are
   "neutral 50%, low confidence" because they have no input, not because the inputs conflict. The site
   shows both cases the same way.
8. **Correlated signals are counted as independent.** `fed_hike`, `cut_jul27` and `cpi_hot` all
   describe the same rates path. For QQQ, hike (28%) and cut (76.5%) average to 52.2% "neutral". That
   number is an artefact of mixing a near-term and a medium-term market, not a real offset.
   *Suggestion:* collapse the markets into a few factors (rates, oil, growth, geopolitics) and map
   factors to assets with per-asset loadings. Each factor then counts once.

## 2. Composite view and confidence (`views()`, `rating_of`, `confidence_of`, l.153–206)

1. **Arithmetic mean of probabilities.** Averaging pulls opposing signals toward 50 and ignores how many
   signals agree. Averaging log-odds with weights is better: weights per signal or factor, plus a
   shrinkage toward 50 that depends on how many signals there are.
2. **One signal can produce "high confidence".** With one signal, `same_side` is always true and
   `spread` is 0. So any single rule with |p−50| ≥ 12 yields **high** (SPY, XLC and ZROZ today, each
   from a single market). Confidence currently measures dispersion, not evidence. *Suggestion:* require
   at least 2 independent factors for "high". Base confidence on effective evidence count and track
   record (per-asset Brier), not only agreement.
3. **Confidence isn't recomputed after the Bayes update.** `apply_bayes` (l.182) and the render
   path (l.486–490) replace `prob_up` and `rating` but keep `conf`. Result: **SPY shows
   "neutral · high confidence" at 49.8%**, which contradicts itself. Confidence should be
   recomputed from the posterior, or shown as "market-signal confidence".
4. **Thresholds are defined twice.** `rating_of` (42/58) and `trend_verdict` (l.289) each hard-code
   the same bands. Put them in one constant.

## 3. Bayesian layer (`bayes.py`, `../inbox/beliefs.json`)

1. **Correlated evidence multiplies as if independent.** SPY has 5 items from **one video**
   ("Adam Khoo — same video"). Their LRs multiply to 2.57, which moves SPY from **27.9% to 49.8%**
   (very bearish → neutral). One promotional source outweighs the whole odds leg.
   *Suggestion:* cap the combined log-LR per source (video or channel), e.g. |Σ log LR| ≤ log 1.5.
   Or combine each video into one LR. Add a global cap on how far evidence can move a posterior.
2. **The conflict-resolution text isn't connected to the math.** SPY's resolution says
   "*bearish, not very-bearish*", but the posterior lands at 49.8 = **neutral**. XLV's resolution
   cites "tight HY OAS + pricing power" and XLRE's cites "mortgage-rate 7.24%". **Neither driver exists
   in this engine**; the text looks copied from a different model. *Suggestion:* store the intended
   bucket in the resolution (e.g. `"target": "bearish"`). Have bayes.py warn when the posterior's bucket
   differs, or let the resolution set a clamp.
3. **`credibility` is declared but never used.** `channels[].credibility = 0.85` and the `lr_protocol`
   ("multiply by credibility discount at extraction time") depend on manual discipline. bayes.py never
   reads it, so whether an LR was discounted can't be checked. Apply it in code: `lr_eff = lr^(w·cred)`.
4. **Evidence is dated at extraction, not at the event.** Items from the Oct 30 video carry
   `date: 2026-11-02`. Facts like "Oct sector tape" describe October. So decay starts late.
   Add `event_date` and decay from that date.
5. **Some half-lives outlast the horizon.** Values of 120–180 days (nvda-2: 180, qqq-2: 150) keep
   more than half their weight beyond the 90-day horizon. The 0.05 cutoff then keeps them for about
   4.3 half-lives, which is up to 2 years. Decaying `log LR` toward 0 (`lr^w`) is a sound shape. The
   problem is that half-lives are hand-picked per item. *Suggestion:* use a default half-life for each
   evidence class (flow/positioning ≈ 30d, earnings/fundamentals ≈ 90d, structural/valuation ≈ 120d)
   and cap at about 1× the horizon.
6. **Momentum is counted twice.** Evidence like "Energy +10% in Oct" or "staples +11% in Oct"
   restates realised price action. That is already in the trend panel and partly in the odds.
   Price-action items should get LR ≈ 1 or be excluded.
7. **The prior isn't a calibrated probability.** The "market prior" comes from rules (§1), so treating
   it as P(up) for an odds update is a category error until the rules are calibrated. A transform
   that's off by 25 points (§1.2) makes any LR adjustment on top of it mostly noise.
8. **Evidence for assets outside the basket is dropped silently.** XLU, XLRE and XLK have evidence in
   beliefs.json but no prior in detail_data.json, so bayes.py skips them without a warning. Log a
   warning, or add the tickers to the basket or watchlist.
9. **The prior is clamped to [2, 98] before the update**, and the posterior is clamped again. That's
   fine, but the archived `prior` is the unclamped value, so a recomputation won't always reproduce it.

## 4. Archive (`predictions.jsonl`, l.523–536)

1. **Pre-Bayes and post-Bayes rows coexist for the same day.** The archive holds SPY
   "very bearish/high/27.7" (no `prior` field) **and** SPY "neutral/high/49.8, prior 27.9", both issued
   2026-11-02 with the same expiry. Both will resolve. Calibration then scores the very-bearish row
   (non-neutral) and skips the neutral one, so **calibration grades a view the site no longer shows.**
   *Suggestion:* when a new row replaces an active one, mark the old row `superseded: <date>` and score
   only the views that were live (or time-weight them).
2. **Rows are appended only when rating or confidence changes.** Probability changes aren't recorded,
   and the rule jumps in §1.2 cause rating flips that produce extra overlapping rows. Overlapping 90-day
   windows on the same asset are strongly autocorrelated, so `n` overstates the independent sample.
3. **Raw inputs aren't archived.** Signal `basis` strings in the archive have no numbers ("hike odds at
   Dec '26 meeting"), and neither raw odds nor market IDs/slugs are stored. The rules (§1.4) can't be
   re-fit later. Store `odds` (value + market slug + end date) and the rule version with each row.
4. **`--render` can change the displayed rating without archiving it.** Render re-runs bayes.py and
   re-rates (l.484–490) but never touches the archive. A same-day belief update can show a new rating
   that calibration never sees. Either archive in render mode, or show the archived view ID next to the
   displayed one.
5. **Writes aren't atomic.** The archive is rewritten in full with `open(ARCHIVE, "w")` (l.535). A crash
   or full disk mid-write destroys history. The same applies to `json.dump(..., open(...))` without close
   (l.519). Write to a temp file, then `os.replace`. Consider keeping it append-only.

## 5. Resolution and calibration (`resolve`, `calibration`, l.226–246)

1. **Price return, not total return.** Resolution compares closes, so dividends and coupons are ignored.
   For HYG (≈6–7% yield), SCHD and ZROZ that biases "up" outcomes down by ~0.5–1.7% per quarter.
   Use adjusted close (Yahoo `adjclose`) or add distributions back.
2. **Neutral views are left out of calibration.** Brier is a proper score for *every* probability.
   Dropping neutral rows throws away most of the data (14 of 23 current rows) and invites selection
   effects. Score all rows. Report hit rate separately for non-neutral rows if useful.
3. **The hit definition doesn't match the rating bands.** `hit = (prob_up ≥ 50) == up`. A
   "neutral" 50.0 counts as an *up* call, and a flat close (`b >= a`) counts as up.
4. **No baseline to compare against.** A mean Brier means little on its own. Show the skill score
   against (a) always-50% (Brier 0.25) and (b) the base rate of 90-day up moves for each asset. Also
   score **prior vs posterior** for the same rows; the archive already stores `prior` on Bayes rows. That
   comparison is the only direct test of whether the video layer helps.
5. **No reliability view.** With enough rows, bucket by forecast probability (e.g. 30/40/50/60/70) and
   compare against observed frequency. Per-asset n will stay small for a long time, so pool across assets.
6. **`close_on` falls back silently.** It uses `series[0]` when no close exists on or before the date
   (l.46). Resolution also uses `min(expires, last series date)`, so stale Yahoo data resolves a view on
   an old price. Require the series to reach the expiry date (allowing a weekend or holiday gap) before
   resolving.

## 6. Failure modes and robustness

1. **Missing markets are dropped silently.** The cut, Iran and recession fetchers each `break` after the
   *first* search hit (l.75, 90, 96). If Polymarket's search ranks a different event first, that signal
   disappears without a warning. **`rec27` is missing from today's `odds.json`.** Missing odds turn assets
   neutral, which then writes a "view change" to the archive.
   *Suggestion:* set a minimum coverage per run (e.g. abort archiving if fewer than N of the expected
   markets are found), log each missing key, and search across all events, not only the first.
2. **bayes.py failures are hidden and old data is reused.** `run_bayes` ignores the subprocess return
   code and stderr (l.174). If bayes.py crashes (bad JSON in beliefs, network), the **previous run's
   bayes_data.json** is loaded. `apply_bayes` then copies its *stale* `prior` and `post` over today's
   values. *Suggestion:* check the return code, add a `generated_at` and `prior_source_hash` to
   bayes_data.json, and reject it when `prior` ≠ today's `detail_data[a].prob_up`.
3. **Narratives can contradict the posterior.** `load_narrative` accepts any file under 3 days old,
   judged by mtime. The narratives are written against the *raw* prior. SPY's narrative says "composite
   very bearish, high confidence", while the page header now says neutral. *Suggestion:* stamp each
   narrative with the rating and prob it was written for. Fall back, or show a "written for: …" note,
   when they don't match.
4. **`poly_yes` assumes outcome 0 is "Yes".** Check `outcomes` before using index 0.
5. **Mixed timezones.** `today = date.today()` is local time, Yahoo dates are converted from UTC, and the
   page timestamp is UTC. Around midnight these can disagree by a day (bayes.py uses local `fromtimestamp`
   in a third way).
6. **Small inefficiencies.** `DD = build_detail_data(V, rows)` at l.522 is computed and then overwritten
   at l.537. The module-level `odds` global is mutated by `fetch()` and replaced in render mode.

## 7. Suggested order of work

1. Add `--no-push` / make render local-only (§0).
2. Fix the `wti_dip90` sign, centre all rule formulas, and clamp outputs (§1.1–1.3).
3. Guard against stale bayes data and missing markets (§6.1–6.2).
4. Supersede-aware archive and calibration over all rows with baselines (§4.1, §5.2–5.4).
5. Cap LR per source, apply credibility in code, add event dates (§3.1, 3.3, 3.4).
6. Factor-based composite with real confidence (§1.8, §2).
