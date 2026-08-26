"""Right-sizing edge cases: the signals that make a downsize wrong."""
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

from awscost.rightsizing import SizingError, advise, percentile  # noqa: E402

DAY = 288  # 5-minute samples in 24 hours


def steady(value, n=DAY * 14, jitter=0.0, seed=5):
    rng = random.Random(seed)
    return [max(0.0, min(100.0, value + rng.uniform(-jitter, jitter)))
            for _ in range(n)]


class TestPercentile(unittest.TestCase):
    def test_known_values(self):
        self.assertEqual(percentile([1, 2, 3, 4, 5], 0.0), 1)
        self.assertEqual(percentile([1, 2, 3, 4, 5], 1.0), 5)
        self.assertEqual(percentile([1, 2, 3, 4, 5], 0.5), 3)

    def test_single_sample(self):
        self.assertEqual(percentile([7], 0.99), 7)

    def test_empty_and_out_of_range(self):
        with self.assertRaises(SizingError):
            percentile([], 0.5)
        with self.assertRaises(SizingError):
            percentile([1, 2], 1.5)


class TestCoreRecommendation(unittest.TestCase):
    def test_over_provisioned_fleet_is_downsized(self):
        result = advise(steady(10, jitter=2), current_capacity=16)
        self.assertEqual(result.action, "downsize")
        self.assertLess(result.recommended_capacity, 16)
        self.assertGreater(result.confidence, 0.5)

    def test_downsize_leaves_the_target_headroom(self):
        result = advise(steady(30, jitter=1), current_capacity=16,
                        target_peak_util=0.6)
        needed = 16 * (result.p99 / 100) / 0.6
        self.assertGreaterEqual(result.recommended_capacity, needed - 1e-9)

    def test_well_sized_fleet_is_held(self):
        # p99 ~ 60% of a 8-unit box at a 60% target is exactly right.
        self.assertEqual(advise(steady(60, jitter=0.5), 8).action, "hold")

    def test_hot_fleet_is_upsized(self):
        result = advise(steady(85, jitter=1), current_capacity=8)
        self.assertEqual(result.action, "upsize")
        self.assertGreater(result.recommended_capacity, 8)

    def test_priced_recommendation(self):
        result = advise(steady(10, jitter=1), current_capacity=16)
        self.assertGreater(result.monthly_saving(30), 0)

    def test_upsize_is_priced_as_a_cost(self):
        result = advise(steady(85, jitter=1), current_capacity=8)
        self.assertLess(result.monthly_saving(30), 0)

    def test_custom_ladder_is_respected(self):
        result = advise(steady(10, jitter=1), current_capacity=16,
                        ladder=[4, 12, 16])
        self.assertIn(result.recommended_capacity, (4.0, 12.0, 16.0))

    def test_ladder_floor_is_never_breached(self):
        result = advise(steady(4, jitter=0.5), current_capacity=16, ladder=[8, 16])
        self.assertEqual(result.recommended_capacity, 8.0)


class TestIndustrialTraps(unittest.TestCase):
    def test_saturated_metric_is_never_downsized(self):
        # 100% CPU is a censored measurement, not perfect efficiency. The mean
        # says "fully utilised"; the truth is "throttled and under-provisioned".
        result = advise(steady(100, jitter=0), current_capacity=8)
        self.assertEqual(result.action, "upsize")
        self.assertTrue(any("censored" in r for r in result.reasons))

    def test_partial_saturation_still_blocks_a_downsize(self):
        samples = steady(20, n=DAY * 10) + steady(99, n=DAY * 2, seed=6)
        self.assertEqual(advise(samples, 8).action, "upsize")

    def test_idle_instance_is_a_termination_candidate(self):
        result = advise(steady(0.5, jitter=0.2), current_capacity=4)
        self.assertEqual(result.action, "terminate")
        self.assertTrue(any("standby" in r for r in result.reasons))

    def test_growing_workload_is_not_downsized(self):
        # p99 climbing all fortnight: sizing on history buys a re-size later.
        samples = [5 + 40 * (i / (DAY * 14)) for i in range(DAY * 14)]
        result = advise(samples, current_capacity=8)
        self.assertEqual(result.action, "hold")
        self.assertTrue(any("trending up" in r for r in result.reasons))

    def test_shrunk_workload_is_sized_on_its_recent_window(self):
        # A completed migration leaves stale peaks in the history. Sizing on
        # them keeps paying for a fleet that no longer exists.
        samples = [45 - 40 * (i / (DAY * 14)) for i in range(DAY * 14)]
        result = advise(samples, current_capacity=16)
        self.assertEqual(result.action, "downsize")
        self.assertTrue(any("most recent" in r for r in result.reasons))

    def test_marginal_overshoot_holds_instead_of_doubling_the_box(self):
        # p99 a hair over target must not trigger an 8 -> 16 jump.
        result = advise(steady(60.5, jitter=0.2), 8)
        self.assertEqual(result.action, "hold")
        self.assertTrue(any("not worth the spend" in r for r in result.reasons))

    def test_bursty_workload_gets_extra_headroom(self):
        base = steady(4, n=DAY * 14, jitter=1)
        for i in range(0, len(base), 100):  # sharp spikes
            base[i] = 70.0
        result = advise(base, current_capacity=16)
        self.assertTrue(any("bursty" in r for r in result.reasons))
        conservative = result.recommended_capacity
        steadier = advise(steady(result.p99, jitter=1), 16).recommended_capacity
        self.assertGreaterEqual(conservative, steadier)

    def test_short_series_refuses_to_advise(self):
        result = advise(steady(10, n=12), current_capacity=16)
        self.assertEqual(result.action, "insufficient-data")
        self.assertEqual(result.recommended_capacity, 16)
        self.assertEqual(result.confidence, 0.0)

    def test_short_series_can_be_forced_but_is_low_confidence(self):
        result = advise(steady(10, n=12), 16, allow_short_series=True)
        self.assertEqual(result.action, "downsize")
        self.assertLess(result.confidence, 0.9)
        self.assertTrue(any("weekly peak" in r for r in result.reasons))

    def test_gaps_are_not_zero_utilisation(self):
        # An agent outage read as 0% would halve the recommendation.
        live = steady(50, n=DAY * 7, jitter=1)
        gappy = live + [None] * (DAY * 7)
        self.assertAlmostEqual(advise(live, 8).p99, advise(gappy, 8).p99, places=6)

    def test_heavy_gaps_lower_confidence_and_say_so(self):
        samples = steady(20, n=DAY * 7) + [None] * (DAY * 7)
        result = advise(samples, 8)
        self.assertGreater(result.missing_share, 0.2)
        self.assertTrue(any("missing" in r for r in result.reasons))
        self.assertLess(result.confidence, 0.7)

    def test_all_samples_missing_is_insufficient_data(self):
        result = advise([None] * DAY * 2, 8)
        self.assertEqual(result.action, "insufficient-data")
        self.assertEqual(result.missing_share, 1.0)

    def test_no_samples_at_all(self):
        self.assertEqual(advise([], 8).action, "insufficient-data")

    def test_nan_counts_as_a_gap_not_a_reading(self):
        samples = steady(50, n=DAY * 2) + [float("nan")] * 10
        self.assertGreater(advise(samples, 8).missing_share, 0)

    def test_zero_variance_series_does_not_divide_by_zero(self):
        self.assertEqual(advise([50.0] * DAY * 2, 8).action, "hold")

    def test_already_largest_size_holds_instead_of_upsizing(self):
        result = advise(steady(100, jitter=0), current_capacity=8, ladder=[4, 8])
        self.assertEqual(result.action, "hold")
        self.assertTrue(any("largest size" in r for r in result.reasons))


class TestValidation(unittest.TestCase):
    def test_utilisation_above_100_rejected(self):
        with self.assertRaises(SizingError) as ctx:
            advise([150.0] * DAY * 2, 8)
        self.assertIn("normalise", str(ctx.exception))

    def test_negative_utilisation_rejected(self):
        with self.assertRaises(SizingError):
            advise([-1.0] * DAY * 2, 8)

    def test_infinite_sample_rejected(self):
        with self.assertRaises(SizingError):
            advise([float("inf")] * DAY * 2, 8)

    def test_string_sample_rejected(self):
        with self.assertRaises(SizingError):
            advise(["50"] * DAY * 2, 8)

    def test_bad_capacity_rejected(self):
        for bad in (0, -4, "8", True, float("nan")):
            with self.assertRaises(SizingError):
                advise(steady(10, n=DAY * 2), bad)

    def test_bad_target_rejected(self):
        for bad in (0, -0.5, 1.5):
            with self.assertRaises(SizingError):
                advise(steady(10, n=DAY * 2), 8, target_peak_util=bad)

    def test_bad_ladder_rejected(self):
        with self.assertRaises(SizingError):
            advise(steady(10, n=DAY * 2), 8, ladder=[])
        with self.assertRaises(SizingError):
            advise(steady(10, n=DAY * 2), 8, ladder=[0, 4])

    def test_bad_interval_rejected(self):
        with self.assertRaises(SizingError):
            advise(steady(10, n=DAY * 2), 8, sample_interval_minutes=0)

    def test_unsorted_ladder_is_sorted(self):
        result = advise(steady(10, jitter=1), 16, ladder=[16, 4, 8])
        self.assertEqual(result.recommended_capacity, 4.0)


if __name__ == "__main__":
    unittest.main()
