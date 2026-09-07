"""Commitment sizing edge cases: spiky, flat, shrinking and short series."""
import math
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

from awscost.commitment import (  # noqa: E402
    CommitmentError,
    cost_at,
    coverage_curve,
    optimal_commit_quantile,
    plan,
)


def diurnal(days=14, low=20.0, high=60.0):
    """A well-behaved workload with a daily cycle: the easy case."""
    out = []
    for hour in range(days * 24):
        phase = (hour % 24) / 24.0 * 2 * math.pi
        out.append(round(low + (high - low) * (0.5 + 0.5 * math.sin(phase)), 3))
    return out


class TestOptimality(unittest.TestCase):
    def test_optimum_beats_every_other_commit_level(self):
        usage = diurnal()
        discount = 0.28
        best = plan(usage, discount).commit
        best_cost = cost_at(usage, best, discount)
        for level in [i * 0.5 for i in range(0, 2 * int(max(usage)) + 4)]:
            self.assertLessEqual(best_cost, cost_at(usage, level, discount) + 1e-9)

    def test_closed_form_matches_the_search(self):
        # The optimal commit is the discount-th percentile of hourly usage.
        rng = random.Random(11)
        for discount in (0.05, 0.2, 0.28, 0.45, 0.72):
            usage = [round(rng.uniform(0, 100), 3) for _ in range(500)]
            searched = plan(usage, discount).commit
            closed = optimal_commit_quantile(usage, discount)
            self.assertAlmostEqual(cost_at(usage, searched, discount),
                                   cost_at(usage, closed, discount), places=6)

    def test_flat_usage_commits_to_the_flat_level(self):
        result = plan([50.0] * 200, 0.3)
        self.assertAlmostEqual(result.commit, 50.0)
        self.assertAlmostEqual(result.savings, 50.0 * 200 * 0.3)
        self.assertEqual(result.waste, 0.0)
        self.assertAlmostEqual(result.effective_discount, 0.3)

    def test_single_spike_is_not_worth_committing_to(self):
        # 1 busy hour in 200. Committing to it wastes 199 hours of commitment.
        usage = [0.0] * 199 + [1000.0]
        result = plan(usage, 0.3)
        self.assertEqual(result.commit, 0.0)
        self.assertEqual(result.savings, 0.0)
        self.assertTrue(any("too spiky" in w for w in result.warnings))

    def test_baseline_plus_spikes_commits_to_the_baseline(self):
        usage = [10.0] * 180 + [200.0] * 20
        result = plan(usage, 0.4)
        self.assertAlmostEqual(result.commit, 10.0)

    def test_higher_discount_justifies_a_higher_commit(self):
        usage = diurnal()
        commits = [plan(usage, d).commit for d in (0.1, 0.3, 0.6, 0.9)]
        self.assertEqual(commits, sorted(commits))

    def test_zero_discount_means_never_commit(self):
        self.assertEqual(plan(diurnal(), 0.0).commit, 0.0)

    def test_all_zero_usage(self):
        result = plan([0.0] * 300, 0.3)
        self.assertEqual(result.commit, 0.0)
        self.assertEqual(result.savings, 0.0)
        self.assertEqual(result.savings_pct, 0.0)
        self.assertEqual(result.coverage_pct, 0.0)
        self.assertEqual(result.effective_discount, 0.0)

    def test_single_hour_series(self):
        result = plan([42.0], 0.5)
        self.assertAlmostEqual(result.commit, 42.0)
        self.assertTrue(any("seasonality" in w for w in result.warnings))

    def test_savings_never_negative_at_the_optimum(self):
        rng = random.Random(3)
        for _ in range(25):
            usage = [rng.choice([0.0, rng.uniform(0, 80)]) for _ in range(300)]
            self.assertGreaterEqual(plan(usage, rng.uniform(0.01, 0.9)).savings, -1e-9)


class TestValidation(unittest.TestCase):
    def test_empty_series_rejected(self):
        with self.assertRaises(CommitmentError):
            plan([], 0.3)

    def test_negative_usage_rejected(self):
        with self.assertRaises(CommitmentError) as ctx:
            plan([10.0, -1.0], 0.3)
        self.assertIn("usage[1]", str(ctx.exception))

    def test_nan_is_not_a_zero_hour(self):
        # A metric gap read as zero usage silently lowers the recommended commit.
        with self.assertRaises(CommitmentError) as ctx:
            plan([10.0, float("nan")], 0.3)
        self.assertIn("missing hour", str(ctx.exception))

    def test_infinite_usage_rejected(self):
        with self.assertRaises(CommitmentError):
            plan([float("inf")], 0.3)

    def test_string_usage_rejected(self):
        with self.assertRaises(CommitmentError):
            plan(["10.0"], 0.3)

    def test_discount_as_percentage_rejected_with_hint(self):
        with self.assertRaises(CommitmentError) as ctx:
            plan([10.0] * 200, 28)
        self.assertIn("/100", str(ctx.exception))

    def test_full_discount_rejected(self):
        with self.assertRaises(CommitmentError):
            plan([10.0] * 200, 1.0)

    def test_negative_discount_rejected(self):
        with self.assertRaises(CommitmentError):
            plan([10.0] * 200, -0.1)

    def test_bad_term_rejected(self):
        with self.assertRaises(CommitmentError):
            plan([10.0] * 200, 0.3, term_months=0)


class TestWarnings(unittest.TestCase):
    def test_short_history_warns_about_seasonality(self):
        result = plan([30.0] * 100, 0.3)
        self.assertTrue(any("seasonality" in w for w in result.warnings))

    def test_scale_down_in_flight_is_flagged(self):
        # The classic trap: commit sized on a fleet that is being migrated away.
        usage = [100.0] * 600 + [20.0] * 200
        result = plan(usage, 0.3)
        self.assertTrue(any("below" in w and "strand" in w for w in result.warnings))

    def test_trailing_window_sizes_on_the_current_shape(self):
        usage = [100.0] * 600 + [20.0] * 200
        full = plan(usage, 0.3).commit
        recent = plan(usage, 0.3, trailing_window=200).commit
        self.assertGreater(full, recent)
        self.assertAlmostEqual(recent, 20.0)

    def test_trailing_window_longer_than_series_warns_not_crashes(self):
        result = plan([30.0] * 200, 0.3, trailing_window=10000)
        self.assertTrue(any("exceeds" in w for w in result.warnings))
        self.assertAlmostEqual(result.commit, 30.0)

    def test_bad_trailing_window_rejected(self):
        with self.assertRaises(CommitmentError):
            plan([30.0] * 200, 0.3, trailing_window=0)

    def test_growth_is_flagged(self):
        usage = [20.0] * 600 + [80.0] * 200
        self.assertTrue(any("trending up" in w for w in plan(usage, 0.3).warnings))

    def test_flat_series_warns_about_decommission_risk(self):
        result = plan([30.0] * 800, 0.3)
        self.assertTrue(any("decommission" in w for w in result.warnings))

    def test_long_history_of_a_healthy_workload_is_quiet(self):
        result = plan(diurnal(days=60), 0.28, term_months=12)
        self.assertEqual(result.warnings, [])


class TestReporting(unittest.TestCase):
    def test_waste_and_coverage_are_consistent(self):
        usage = [10.0] * 100 + [50.0] * 100
        result = plan(usage, 0.5)
        recomputed = sum(min(u, result.commit) for u in usage) / sum(usage) * 100
        self.assertAlmostEqual(result.coverage_pct, recomputed)
        self.assertAlmostEqual(
            result.waste,
            sum(max(result.commit - u, 0.0) for u in usage) * (1 - result.discount))

    def test_effective_discount_is_below_the_headline_when_waste_exists(self):
        usage = [0.0] * 40 + [100.0] * 160
        result = plan(usage, 0.6)
        self.assertLess(result.effective_discount, 0.6)

    def test_curve_peaks_at_the_optimum(self):
        usage = diurnal()
        result = plan(usage, 0.3)
        curve = coverage_curve(usage, 0.3)
        best_on_curve = max(savings for _, savings, _ in curve)
        self.assertGreaterEqual(result.savings, best_on_curve - 1e-9)

    def test_curve_rejects_negative_levels(self):
        with self.assertRaises(CommitmentError):
            coverage_curve([10.0] * 10, 0.3, levels=[-1.0])

    def test_curve_is_a_plateau_not_a_knife_edge(self):
        # Committing 10% under the optimum should cost very little; this is why
        # under-committing is the safe direction.
        usage = diurnal()
        result = plan(usage, 0.3)
        under = coverage_curve(usage, 0.3, levels=[result.commit * 0.9])[0][1]
        self.assertGreater(under, result.savings * 0.95)


if __name__ == "__main__":
    unittest.main()
