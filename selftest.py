#!/usr/bin/env python3
"""Offline self-test for the logic fixes (no network, no git, writes nothing).
Run: python3 selftest.py   -> exits nonzero on any failure."""
import datetime, io, contextlib, json, os, sys, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import predict as P   # pipeline entry points sit behind __main__: importing has no side effects
import bayes as B

GRID = [i / 10 for i in range(0, 1001)]          # 0.0 .. 100.0 step 0.1

def up(key, p, asset):
    """Per-signal up-prob for one asset from one market; 50 when no signal (dead band)."""
    v = [s["prob_up"] for s in P.signals({key: (p, "t")}) if s["asset"] == asset]
    assert len(v) <= 1, v
    return v[0] if v else 50.0


class OilRules(unittest.TestCase):
    def test_dip90_monotone_decreasing(self):
        for a in ("XLE", "SCHD"):
            ys = [up("wti_dip90", p, a) for p in GRID]
            for p, y0, y1 in zip(GRID, ys, ys[1:]):
                self.assertLessEqual(y1, y0, f"{a} up-prob rose with dip odds at {p}")
            self.assertLess(ys[-1], 50, a)

    def test_dip90_old_bug_band_not_bullish(self):
        for p in (55, 56, 58, 60, 62, 62.5):
            for a in ("XLE", "SCHD"):
                self.assertLessEqual(up("wti_dip90", p, a), 50, f"{a} bullish at dip odds {p}")

    def test_spike120_monotone_increasing(self):
        ys = [up("wti_spike120", p, "XLE") for p in GRID]
        for p, y0, y1 in zip(GRID, ys, ys[1:]):
            self.assertGreaterEqual(y1, y0, f"XLE fell with spike odds at {p}")

    def test_iran_direction(self):   # input is ceasefire-HOLD odds
        xle = [up("iran_peace_dec", p, "XLE") for p in GRID]
        spy = [up("iran_peace_dec", p, "SPY") for p in GRID]
        for y0, y1 in zip(xle, xle[1:]): self.assertLessEqual(y1, y0)
        for y0, y1 in zip(spy, spy[1:]): self.assertGreaterEqual(y1, y0)

    def test_all_rules_monotone(self):
        for sig, key, tf, maps, *_ in P.RULES:
            for assets, pts in maps:
                ys = [up(key, p, assets[0]) for p in GRID]
                d = [b - a for a, b in zip(ys, ys[1:])]
                self.assertTrue(all(x >= -1e-9 for x in d) or all(x <= 1e-9 for x in d),
                                f"{sig}->{assets} not monotone")


class Clamps(unittest.TestCase):
    def test_every_signal_in_1_99(self):
        for key in P.EXPECTED_MARKETS:
            for p in GRID:
                for s in P.signals({key: (p, "t")}):
                    self.assertTrue(1 <= s["prob_up"] <= 99, (key, p, s))

    def test_spike_rule_capped(self):
        self.assertEqual(up("wti_spike120", 90, "XLE"), 99.0)    # old rule: 104
        self.assertEqual(up("wti_spike120", 100, "XLE"), 99.0)   # old rule: 110


class Interpolation(unittest.TestCase):
    def test_interp_basic(self):
        pts = [(0, 80), (40, 50), (55, 50), (100, 20)]
        self.assertEqual(P.interp(-5, pts), 80)
        self.assertEqual(P.interp(120, pts), 20)
        self.assertAlmostEqual(P.interp(20, pts), 65)
        self.assertAlmostEqual(P.interp(77.5, pts), 35)

    def test_continuity_no_cliffs(self):
        # steepest rule slope is 50/35 per pt -> 0.143 per 0.1pt step, plus 0.1 display rounding
        for key in P.EXPECTED_MARKETS:
            for a in P.rule_assets(key):
                ys = [up(key, p, a) for p in GRID]
                jump = max(abs(y1 - y0) for y0, y1 in zip(ys, ys[1:]))
                self.assertLess(jump, 0.3, f"{key}->{a} jumps {jump}")

    def test_coin_flip_markets_neutral(self):
        self.assertAlmostEqual(up("cut_jul27", 53, "ZROZ"), 53, delta=3)
        v = P.views(P.signals({"cut_jul27": (53, "t")}))
        self.assertEqual(v["ZROZ"]["rating"], "neutral")
        self.assertNotEqual(v["ZROZ"]["conf"], "high")

    def test_saturation_extremes_kept(self):
        self.assertEqual(up("cut_jul27", 100, "ZROZ"), 99.0)      # old 50+0.5*100=100 -> clamp
        self.assertEqual(up("cut_jul27", 100, "SCHD"), 1.0)       # old 50-0.5*100=0 -> clamp
        self.assertEqual(up("wti_dip90", 100, "XLE"), 20.0)       # old 100-0.8*100
        self.assertEqual(up("wti_dip90", 0, "XLE"), 80.0)         # old 50+50*0.6
        self.assertEqual(up("iran_peace_dec", 0, "XLE"), 90.0)    # old 50+100*0.4
        self.assertEqual(up("iran_peace_dec", 0, "SPY"), 20.0)    # old 50-100*0.3
        self.assertEqual(up("rec27", 0, "HYG"), 70.0)             # old 50+50*0.4
        self.assertEqual(up("cpi_hot", 100, "QQQ"), 15.0)         # old 50-0.35*100

    def test_fed_hike_low_branch_mirrors_high(self):
        self.assertGreater(up("fed_hike", 80, "SCHD"), 50)
        self.assertLess(up("fed_hike", 80, "QQQ"), 50)
        self.assertLess(up("fed_hike", 10, "SCHD"), 50)
        self.assertGreater(up("fed_hike", 10, "QQQ"), 50)


class ConfidenceAfterBayes(unittest.TestCase):
    def _V(self, sig_probs, asset="SPY"):
        m = round(sum(sig_probs) / len(sig_probs), 1)
        ss = [dict(sig="x", basis="", prob_up=v, invalid="", asset=asset) for v in sig_probs]
        return {asset: dict(prob_up=m, rating=P.rating_of(m), conf=P.confidence_of(m, sig_probs), signals=ss)}

    def test_spy_case_neutral_posterior_not_high(self):
        V = self._V([27.9])
        self.assertEqual(V["SPY"]["conf"], "high")                 # raw very-bearish view
        P.apply_bayes(V, dict(SPY=dict(prior=27.9, post=49.8)))
        self.assertEqual(V["SPY"]["rating"], "neutral")
        self.assertEqual(V["SPY"]["conf"], "low")

    def test_neutral_never_high(self):
        sets = [[20], [80], [20, 25], [75, 80, 85], [30, 70], [10, 90, 40]]
        for post in [x / 2 for x in range(84, 117)]:               # 42..58 = neutral band
            for ms in sets:
                self.assertNotEqual(P.confidence_of(post, ms), "high", (post, ms))

    def test_posterior_flipped_against_signals_is_low(self):
        V = self._V([30, 32])
        P.apply_bayes(V, dict(SPY=dict(prior=31, post=66)))
        self.assertEqual(V["SPY"]["conf"], "low")

    def test_strong_agreeing_evidence_can_be_high(self):
        V = self._V([30, 34])
        P.apply_bayes(V, dict(SPY=dict(prior=32, post=25)))
        self.assertEqual(V["SPY"]["conf"], "high")

    def test_watchlist_low(self):
        V = {}
        P.apply_bayes(V, dict(NVDA=dict(prior=50, post=70, watch=True)))
        self.assertEqual(V["NVDA"]["conf"], "low")


class EvidenceGroups(unittest.TestCase):
    T = datetime.date(2026, 11, 2)

    def it(self, i, lr, d="up", g=None, date="2026-11-02", hl=60):
        x = dict(id=i, date=date, dir=d, lr=lr, half_life=hl)
        if g: x["group"] = g
        return x

    @staticmethod
    def post_of(prior, lr_product):
        o = prior / (100 - prior) * lr_product
        return 100 * o / (1 + o)

    def test_group_counts_once_max(self):
        items = [self.it(f"s{k}", lr, g="v1:SPY:up") for k, lr in enumerate([1.22, 1.22, 1.34, 1.17, 1.1])]
        p, used = B.posterior(30, items, self.T)
        self.assertAlmostEqual(p, self.post_of(30, 1.34), places=6)
        self.assertEqual([u["id"] for u in used if u["applied"]], ["s2"])
        self.assertTrue(all(u.get("absorbed_by") == "s2" for u in used if not u["applied"]))
        self.assertEqual(len(used), 5)                             # absorbed items kept for audit

    def test_ungrouped_stay_independent(self):
        p, _ = B.posterior(40, [self.it("a", 1.2), self.it("b", 1.3, d="down")], self.T)
        self.assertAlmostEqual(p, self.post_of(40, 1.2 / 1.3), places=6)

    def test_group_plus_independent(self):
        items = [self.it("a", 1.2, g="g"), self.it("b", 1.3, g="g"), self.it("c", 1.25)]
        p, _ = B.posterior(50, items, self.T)
        self.assertAlmostEqual(p, self.post_of(50, 1.3 * 1.25), places=6)

    def test_group_max_uses_decayed_lr(self):
        # LR 1.5 aged 120d with half-life 30d -> weight 1/16 -> lr_eff ~1.026; fresh 1.2 wins
        items = [self.it("old", 1.5, g="g", date="2026-07-05", hl=30), self.it("new", 1.2, g="g")]
        p, used = B.posterior(50, items, self.T)
        self.assertEqual([u["id"] for u in used if u["applied"]], ["new"])
        self.assertAlmostEqual(p, self.post_of(50, 1.2), places=6)

    def test_mixed_direction_group_rejected(self):
        with self.assertRaises(ValueError):
            B.posterior(50, [self.it("a", 1.2, g="g"), self.it("b", 1.2, d="down", g="g")], self.T)

    def test_beliefs_file_grouped(self):
        with open(B.BELIEFS) as f: bel = json.load(f)
        for a in ("SPY", "SCHD"):
            gs = {e.get("group") for e in bel["assets"][a]["evidence"]}
            self.assertEqual(len(gs), 1, a)
            self.assertIsNotNone(gs.pop(), a)
        for sec in ("assets", "watchlist"):
            for a, b in bel[sec].items():
                B.posterior(50, b.get("evidence", []), self.T)    # no mixed-direction group


class Calibration(unittest.TestCase):
    def r(self, asset, date, rating, p, hit, **kw):
        x = dict(date=date, asset=asset, rating=rating, conf="high", prob_up=p, expires="2027-01-31",
                 resolved=True, hit=hit, brier=0.1)
        x.update(kw)
        return x

    def test_legacy_raw_duplicate_skipped(self):
        rows = [self.r("SPY", "2026-11-02", "very bearish", 27.7, True),             # legacy pre-Bayes
                self.r("SPY", "2026-11-02", "neutral", 49.8, False, prior=27.9)]     # legacy posterior
        self.assertEqual(P.scored_rows(rows), [rows[1]])
        self.assertEqual(P.calibration(rows), {})          # the shown view was neutral -> unscored

    def test_raw_flag_excluded(self):
        rows = [self.r("XLE", "2026-11-02", "very bullish", 75, False, raw=True),
                self.r("XLE", "2026-11-02", "bullish", 62, True, raw=False, prior=59)]
        c = P.calibration(rows)
        self.assertEqual((c["XLE"]["n"], c["XLE"]["hit"]), (1, 100))

    def test_legacy_only_day_counts(self):
        rows = [self.r("ZROZ", "2026-08-01", "very bullish", 76, True)]
        self.assertEqual(P.calibration(rows)["ZROZ"]["n"], 1)

    def test_one_per_asset_day_last_posterior_wins(self):
        rows = [self.r("QQQ", "2026-11-02", "bearish", 38, True, raw=False, prior=40),
                self.r("QQQ", "2026-11-02", "very bearish", 30, False, raw=True),
                self.r("QQQ", "2026-11-02", "very bearish", 31, False, raw=False, prior=33),
                self.r("QQQ", "2026-11-03", "bearish", 39, True, raw=False, prior=39)]
        self.assertEqual(P.scored_rows(rows), [rows[2], rows[3]])
        c = P.calibration(rows)["QQQ"]
        self.assertEqual((c["n"], c["hit"]), (2, 50))

    def test_real_archive_one_scored_per_asset_day(self):
        s = P.scored_rows(P.load())
        keys = [(r["asset"], r["date"]) for r in s]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertFalse(any(P.is_raw(r) for r in s))

    def test_archive_views_writes_raw_then_posterior(self):
        V = dict(SPY=dict(prob_up=45.0, rating="neutral", conf="low", signals=[], prior=37.7, bayes=True),
                 XLY=dict(prob_up=50.0, rating="neutral", conf="low", signals=[]))
        raw = dict(SPY=dict(prob_up=37.7, rating="bearish", conf="high"), XLY=dict(V["XLY"]))
        new = P.archive_views([], V, raw)
        self.assertEqual([(r["asset"], r["raw"]) for r in new], [("SPY", True), ("SPY", False), ("XLY", False)])
        self.assertEqual(P.archive_views(new, V, raw), [])   # unchanged view -> nothing appended
        self.assertEqual(P.scored_rows(new), [new[1], new[2]])

    def test_scored_view_tracks_shown_posterior(self):
        t = P.today.isoformat()
        legacy = [dict(date=t, asset="QQQ", rating="neutral", conf="low", prob_up=52.0,
                       expires="2099-01-01", signals=[], resolved=False)]       # pre-Bayes, no prior key
        V = dict(QQQ=dict(prob_up=44.9, rating="neutral", conf="low", signals=[], prior=40.9, bayes=True))
        raw = dict(QQQ=dict(prob_up=40.9, rating="bearish", conf="medium"))
        new = P.archive_views(legacy, V, raw)                 # same rating/conf, but not a posterior
        self.assertEqual([r.get("raw") for r in new], [True, False])
        rows = legacy + new
        self.assertEqual(P.scored_rows(rows)[0]["prob_up"], 44.9)
        V["QQQ"]["prob_up"] = 46.0                            # same-day rerun, prob moved
        more = P.archive_views(rows, V, raw)
        self.assertEqual(P.scored_rows(rows + more)[0]["prob_up"], 46.0)
        self.assertEqual(len(P.scored_rows(rows + more)), 1)
        self.assertEqual(P.archive_views(rows + more, V, raw), [])

    def test_adjclose_total_return(self):
        ind = dict(quote=[dict(close=[100.0, None, 101.0])], adjclose=[dict(adjclose=[98.0, None, 101.0])])
        j = dict(chart=dict(result=[dict(timestamp=[1754006400, 1754092800, 1754179200], indicators=ind)]))
        s = P.parse_chart(j)
        self.assertEqual(s["total_basis"], "adjclose")
        self.assertEqual([v for _, v in s["total"]], [98.0, 101.0])
        self.assertEqual([v for _, v in s["close"]], [100.0, 101.0])
        del ind["adjclose"]
        s = P.parse_chart(j)
        self.assertEqual(s["total_basis"], "close")
        self.assertEqual(s["total"], s["close"])


class FailLoud(unittest.TestCase):
    def test_missing_markets_warn_and_note(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            m = P.missing_markets({k: (50, "") for k in P.EXPECTED_MARKETS if k != "rec27"})
        self.assertEqual(m, ["rec27"])
        self.assertIn("rec27", err.getvalue())
        html = P.index_html({}, [], {}, {}, m)
        self.assertIn("Missing Polymarket market", html)
        self.assertIn("rec27", html)
        self.assertNotIn("Missing Polymarket market", P.index_html({}, [], {}, {}, []))

    def test_stale_bayes_rejected(self):
        detail = dict(SPY=dict(prob_up=37.7))
        spy = dict(prior=37.7, post=45)
        self.assertTrue(P.check_bayes(dict(_meta=dict(generated_at=200.0), SPY=spy), 100.0, detail))
        with self.assertRaises(SystemExit):     # generated before this run started
            P.check_bayes(dict(_meta=dict(generated_at=50.0), SPY=spy), 100.0, detail)
        with self.assertRaises(SystemExit):     # old-format file without a freshness stamp
            P.check_bayes(dict(SPY=spy), 100.0, detail)
        with self.assertRaises(SystemExit):     # posterior computed from yesterday's prior
            P.check_bayes(dict(_meta=dict(generated_at=200.0), SPY=dict(prior=27.9, post=49.8)), 100.0, detail)

    def test_narrative_stale_banner(self):
        spy = "Evidence: ceasefire-break maps to a 27.7% SPY up-prob (composite very bearish, high confidence)."
        self.assertIn("Narrative stale", P.stale_banner(spy, "neutral", 45.0))
        self.assertEqual(P.stale_banner(spy, "very bearish", 28.0), "")
        qqq = ("**Neutral (low confidence) — the composite is a deliberate wash** (up-probs 28% vs 76% "
               "averaging to 52%). Hike odds under 45% turns QQQ bullish.")
        self.assertEqual(P.narrative_claims(qqq), ["neutral", "neutral"])
        self.assertEqual(P.stale_banner(qqq, "neutral", 46.0), "")
        xlc = "Odds **agree with the trend — bearish continuation** (composite up-prob 28%, high confidence)."
        self.assertEqual(P.narrative_claims(xlc), ["very bearish"])


import shutil, tempfile, time

GOOD_MD = "## Performance (6 months)\n\nup.\n\n## Drivers\n\nodds.\n\n## Continue or revert\n\nneutral.\n"


class NarrativePathMigration(unittest.TestCase):
    """assets/<A>/analysis.md is primary; narratives/<A>.md is a temporary fallback."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.saved = (P.ASSETS, P.NARR)
        P.ASSETS, P.NARR = os.path.join(self.tmp, "assets"), os.path.join(self.tmp, "narratives")
        os.makedirs(P.NARR)

    def tearDown(self):
        P.ASSETS, P.NARR = self.saved
        shutil.rmtree(self.tmp)

    def put(self, rel, text, age_s=0):
        p = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f: f.write(text)
        t = time.time() - age_s
        os.utime(p, (t, t))
        return p

    def load(self, a):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            txt = P.load_narrative(a)
        return txt, err.getvalue()

    def test_new_path_read(self):
        self.put("assets/SPY/analysis.md", GOOD_MD)
        txt, err = self.load("SPY")
        self.assertEqual(txt, GOOD_MD)
        self.assertEqual(err, "")

    def test_legacy_fallback_when_not_migrated(self):
        self.put("narratives/XLF.md", "legacy\n\n" + GOOD_MD)
        txt, err = self.load("XLF")
        self.assertTrue(txt.startswith("legacy"))
        self.assertIn("legacy path narratives/XLF.md", err)

    def test_placeholder_ignored_and_falls_back(self):
        import bayes
        self.put("assets/HYG/analysis.md", bayes.PLACEHOLDER)
        self.assertEqual(self.load("HYG")[0], None)                       # placeholder only -> fallback page
        self.put("narratives/HYG.md", "legacy\n\n" + GOOD_MD)
        self.assertTrue(self.load("HYG")[0].startswith("legacy"))         # placeholder + legacy -> legacy

    def test_new_wins_over_older_legacy(self):
        self.put("narratives/QQQ.md", "legacy\n\n" + GOOD_MD, age_s=3600)
        self.put("assets/QQQ/analysis.md", "new\n\n" + GOOD_MD)
        txt, err = self.load("QQQ")
        self.assertTrue(txt.startswith("new"))
        self.assertEqual(err, "")

    def test_newer_legacy_used_with_warning(self):    # a writer still targeting the old path
        self.put("assets/XLE/analysis.md", "new\n\n" + GOOD_MD, age_s=3600)
        self.put("narratives/XLE.md", "legacy\n\n" + GOOD_MD)
        txt, err = self.load("XLE")
        self.assertTrue(txt.startswith("legacy"))
        self.assertIn("write assets/XLE/analysis.md", err)
        self.assertNotIn("sections", err)

    def test_watchlist_ticker_path(self):
        self.put("assets/NVDA/analysis.md", GOOD_MD)
        self.assertEqual(P.narrative_path("NVDA"), os.path.join(P.ASSETS, "NVDA", "analysis.md"))
        self.assertEqual(self.load("NVDA")[0], GOOD_MD)

    def test_stale_and_bad_sections(self):
        self.put("assets/XLC/analysis.md", GOOD_MD, age_s=5 * 86400)
        self.assertIsNone(self.load("XLC")[0])                            # >3 days old -> fallback
        self.put("assets/XLC/analysis.md", "## Drivers\n\nonly one\n")
        txt, err = self.load("XLC")
        self.assertIsNotNone(txt)
        self.assertIn("sections", err)


class AssetFolderSync(unittest.TestCase):
    """evidence.json / views.jsonl regenerate from master files; repeated runs are no-ops."""
    BEL = {"assets": {"SPY": {"evidence": [dict(id="spy-1", dir="up", lr=1.2, date="2026-11-02")],
                              "resolution": "r"}},
           "watchlist": {"NVDA": {"prior": 50, "source": "video X", "added": "2026-11-02",
                                  "evidence": [dict(id="nvda-1", dir="up", lr=1.28, date="2026-11-02")]}}}

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ad, self.nd = os.path.join(self.tmp, "assets"), os.path.join(self.tmp, "narratives")
        self.arch = os.path.join(self.tmp, "predictions.jsonl")
        os.makedirs(self.nd)
        self.lines = [json.dumps(dict(date="2026-11-02", asset=a, rating="neutral", prob_up=p)) + "\n"
                      for a, p in (("SPY", 37.7), ("NVDA", 52.7), ("SPY", 44.8), ("XLY", 50.0))]
        with open(self.arch, "w") as f: f.writelines(self.lines)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def sync(self, basket=("SPY", "XLY")):
        return B.sync_asset_folders(self.BEL, list(basket), archive_path=self.arch,
                                    assets_dir=self.ad, narr_dir=self.nd)

    def read(self, a, name):
        with open(os.path.join(self.ad, a, name)) as f: return f.read()

    def snapshot(self):
        out = {}
        for root, _, files in os.walk(self.ad):
            for fn in files:
                p = os.path.join(root, fn)
                with open(p, "rb") as f: out[p] = (f.read(), os.path.getmtime(p))
        return out

    def test_generation_and_content(self):
        r = self.sync()
        self.assertEqual(sorted(os.listdir(self.ad)), ["NVDA", "SPY", "XLY"])
        self.assertEqual(self.read("SPY", "views.jsonl"), self.lines[0] + self.lines[2])   # verbatim, in order
        self.assertEqual(self.read("NVDA", "views.jsonl"), self.lines[1])
        spy = json.loads(self.read("SPY", "evidence.json"))
        self.assertEqual((spy["kind"], spy["resolution"], spy["evidence"]),
                         ("basket", "r", self.BEL["assets"]["SPY"]["evidence"]))
        nv = json.loads(self.read("NVDA", "evidence.json"))
        self.assertEqual((nv["kind"], nv["prior"], nv["found_in"]), ("watchlist", 50, "video X"))
        self.assertEqual(json.loads(self.read("XLY", "evidence.json"))["evidence"], [])
        self.assertEqual(sorted(r["placeholders"]), ["NVDA", "SPY", "XLY"])

    def test_idempotent_no_duplicates(self):
        self.sync()
        snap = self.snapshot()
        time.sleep(0.02)
        r = self.sync()
        self.assertEqual(r, {"written": [], "placeholders": []})
        self.assertEqual(self.snapshot(), snap)                           # same bytes, nothing rewritten
        for _ in range(3): self.sync()
        self.assertEqual(self.read("SPY", "views.jsonl").count("\n"), 2)

    def test_master_append_propagates_once(self):
        self.sync()
        extra = json.dumps(dict(date="2026-11-03", asset="SPY", rating="bearish", prob_up=40.0)) + "\n"
        with open(self.arch, "a") as f: f.write(extra)
        r = self.sync()
        self.assertEqual(r["written"], [os.path.join(self.ad, "SPY", "views.jsonl")])
        self.sync()
        self.assertEqual(self.read("SPY", "views.jsonl"), self.lines[0] + self.lines[2] + extra)

    def test_analysis_never_overwritten(self):
        os.makedirs(os.path.join(self.ad, "SPY"))
        with open(os.path.join(self.ad, "SPY", "analysis.md"), "w") as f: f.write(GOOD_MD)
        with open(os.path.join(self.nd, "XLY.md"), "w") as f: f.write(GOOD_MD)   # not yet migrated
        r = self.sync()
        self.assertEqual(self.read("SPY", "analysis.md"), GOOD_MD)
        self.assertNotIn("XLY", r["placeholders"])                        # leaves room for git mv
        self.assertFalse(os.path.exists(os.path.join(self.ad, "XLY", "analysis.md")))


class RepoLayout(unittest.TestCase):
    """The real repo: one folder per tracked asset, derived files in sync with the masters."""
    def test_every_tracked_asset_has_folder(self):
        with open(B.BELIEFS) as f: bel = json.load(f)
        for a in P.BASKET + list(bel["watchlist"]):
            for name in ("analysis.md", "evidence.json", "views.jsonl"):
                self.assertTrue(os.path.exists(os.path.join(P.ASSETS, a, name)), f"{a}/{name}")

    def test_migrated_narratives_have_three_sections(self):
        for a in sorted(os.listdir(P.ASSETS)):
            fp = os.path.join(P.ASSETS, a, "analysis.md")
            if not os.path.isfile(fp): continue
            with open(fp) as f: txt = f.read()
            heads = tuple(l.strip() for l in txt.splitlines() if l.startswith("## "))
            self.assertEqual(heads, P.NARR_SECTIONS, a)
        self.assertFalse([f for f in os.listdir(P.NARR) if f.endswith(".md")] if os.path.isdir(P.NARR) else [])

    def test_views_match_master_archive(self):
        with open(P.ARCHIVE) as f:
            master = [l if l.endswith("\n") else l + "\n" for l in f if l.strip()]
        for a in os.listdir(P.ASSETS):
            vp = os.path.join(P.ASSETS, a, "views.jsonl")
            if not os.path.isfile(vp): continue
            with open(vp) as f: got = f.read()
            self.assertEqual(got, "".join(l for l in master if json.loads(l)["asset"] == a), a)

    def test_publish_links_unchanged(self):
        V = {a: dict(prob_up=50.0, rating="neutral", conf="low", signals=[]) for a in ("SPY", "XLF")}
        V["NVDA"] = dict(prob_up=50.0, rating="neutral", conf="low", signals=[], watch=True)
        import re as _re
        hrefs = _re.findall(r"href='([^']+)'", P.index_html(V, [], {}, {}))
        self.assertEqual(hrefs, ["details/SPY.html", "details/XLF.html", "details/W_NVDA.html"])


if __name__ == "__main__":
    res = unittest.main(verbosity=2, exit=False).result
    sys.exit(0 if res.wasSuccessful() else 1)
