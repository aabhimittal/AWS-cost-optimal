"""Portfolio selection edge cases: overlap, exclusions, prerequisites, risk."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

from awscost.model import Candidate  # noqa: E402
from awscost.portfolio import (  # noqa: E402
    PortfolioError,
    aggregate_risk,
    net_savings,
    optimize,
    requirement_closure,
    simulate,
)


def c(name, savings=1000, confidence=1.0, effort=1.0, risk=0.0, **kw):
    return Candidate(name, savings, confidence, effort, risk, **kw)


class TestOverlap(unittest.TestCase):
    def test_ungrouped_savings_add_up(self):
        self.assertAlmostEqual(net_savings([c("a", 100), c("b", 200)]), 300)

    def test_same_group_is_not_double_counted(self):
        # Right-sizing and Graviton harvest the same fleet spend. Naively adding
        # them promises money that does not exist.
        plan = [c("rightsize", 3000, group="fleet"), c("graviton", 4000, group="fleet")]
        self.assertAlmostEqual(net_savings(plan, 0.5), 4000 + 3000 * 0.5)

    def test_richest_member_is_credited_in_full(self):
        plan = [c("small", 100, group="g"), c("big", 900, group="g")]
        self.assertAlmostEqual(net_savings(plan, 0.0), 900)

    def test_decay_bounds_enforced(self):
        for bad in (-0.1, 1.1):
            with self.assertRaises(PortfolioError):
                net_savings([c("a")], bad)

    def test_group_of_one_is_undiscounted(self):
        self.assertAlmostEqual(net_savings([c("a", 500, group="g")], 0.0), 500)

    def test_three_members_decay_geometrically(self):
        plan = [c("a", 100, group="g"), c("b", 100, group="g"), c("d", 100, group="g")]
        self.assertAlmostEqual(net_savings(plan, 0.5), 100 + 50 + 25)


class TestAggregateRisk(unittest.TestCase):
    def test_small_risks_compound(self):
        # Ten 2% changes are an 18% chance of an incident, not 2%.
        self.assertAlmostEqual(aggregate_risk([c(str(i), risk=0.02) for i in range(10)]),
                               1 - 0.98 ** 10)

    def test_empty_plan_is_riskless(self):
        self.assertEqual(aggregate_risk([]), 0.0)

    def test_certain_regression_saturates(self):
        self.assertAlmostEqual(aggregate_risk([c("a", risk=1.0), c("b", risk=0.5)]), 1.0)


class TestBudget(unittest.TestCase):
    def test_floating_point_budget_boundary(self):
        # 0.1 + 0.2 > 0.3 in binary. A plan that drops a candidate for that is a
        # bug, not a constraint.
        plan = optimize([c("a", 100, effort=0.1), c("b", 100, effort=0.2)],
                        effort_budget=0.3)
        self.assertEqual(sorted(plan.names), ["a", "b"])

    def test_zero_budget_selects_nothing(self):
        plan = optimize([c("a", 1000)], effort_budget=0)
        self.assertEqual(plan.chosen, [])
        self.assertEqual(plan.net_savings, 0.0)

    def test_negative_budget_rejected(self):
        with self.assertRaises(PortfolioError):
            optimize([c("a")], effort_budget=-1)

    def test_unlimited_budget_takes_everything_profitable(self):
        plan = optimize([c("a", 100), c("b", 200), c("dud", -50)])
        self.assertEqual(sorted(plan.names), ["a", "b"])

    def test_greedy_ordering_is_beaten_by_exact_search(self):
        # Greedy takes the best ratio (a) and then cannot afford b or d.
        # The optimum is b + d.
        items = [c("a", 620, effort=6), c("b", 500, effort=5), c("d", 500, effort=5)]
        plan = optimize(items, effort_budget=10)
        self.assertTrue(plan.exact)
        self.assertEqual(sorted(plan.names), ["b", "d"])
        self.assertAlmostEqual(plan.net_savings, 1000)

    def test_empty_candidate_set(self):
        plan = optimize([], effort_budget=10)
        self.assertEqual(plan.chosen, [])
        self.assertEqual(plan.aggregate_risk, 0.0)

    def test_large_set_falls_back_to_greedy_and_still_fits(self):
        items = [c(f"c{i}", 100 + i, effort=1) for i in range(40)]
        plan = optimize(items, effort_budget=5, exact_limit=22)
        self.assertFalse(plan.exact)
        self.assertLessEqual(plan.effort, 5 + 1e-9)
        self.assertEqual(len(plan.chosen), 5)

    def test_effort_floor_applies_to_the_budget(self):
        # "Free" changes still consume the 0.1-day floor, so 20 of them do not
        # fit in a 1-day budget.
        items = [c(f"c{i}", 100, effort=0) for i in range(20)]
        plan = optimize(items, effort_budget=1.0)
        self.assertEqual(len(plan.chosen), 10)


class TestConstraints(unittest.TestCase):
    def test_mutually_exclusive_candidates_never_both_ship(self):
        items = [c("spot", 5000, effort=5, excludes=("ri",)), c("ri", 4000, effort=1)]
        plan = optimize(items, effort_budget=10)
        # Both fit on effort alone; the exclusion forces a choice, and the
        # planner takes the higher net saving rather than the higher score.
        self.assertEqual(plan.names, ["spot"])
        self.assertIn("excluded by", dict(plan.excluded)["ri"])

    def test_exclusion_is_symmetric_from_either_side(self):
        items = [c("a", 1000, effort=1), c("b", 900, effort=1, excludes=("a",))]
        plan = optimize(items, effort_budget=10)
        self.assertEqual(plan.names, ["a"])

    def test_prerequisite_is_pulled_in_with_its_effort(self):
        items = [c("tagging", 0, effort=2), c("showback", 3000, effort=1,
                                              requires=("tagging",))]
        plan = optimize(items, effort_budget=3)
        self.assertEqual(sorted(plan.names), ["showback", "tagging"])
        self.assertAlmostEqual(plan.effort, 3)

    def test_prerequisite_that_does_not_fit_blocks_the_candidate(self):
        items = [c("tagging", 0, effort=10), c("showback", 3000, effort=1,
                                               requires=("tagging",))]
        plan = optimize(items, effort_budget=5)
        self.assertEqual(plan.chosen, [])

    def test_transitive_prerequisites_resolved(self):
        items = [c("a", 0, effort=1), c("b", 0, effort=1, requires=("a",)),
                 c("d", 900, effort=1, requires=("b",))]
        closure = requirement_closure(items)
        self.assertEqual(closure["d"], {"a", "b", "d"})
        plan = optimize(items, effort_budget=3)
        self.assertEqual(sorted(plan.names), ["a", "b", "d"])

    def test_risk_budget_caps_the_plan(self):
        items = [c(f"c{i}", 1000, effort=1, risk=0.2) for i in range(5)]
        plan = optimize(items, effort_budget=5, risk_budget=0.5)
        self.assertLessEqual(plan.aggregate_risk, 0.5 + 1e-9)
        self.assertEqual(len(plan.chosen), 3)  # 1-0.8^3 = 0.488

    def test_max_risk_drops_individual_candidates(self):
        items = [c("safe", 1000, risk=0.1), c("scary", 9000, risk=0.8)]
        plan = optimize(items, effort_budget=10, max_risk=0.5)
        self.assertEqual(plan.names, ["safe"])
        self.assertIn("scary", [name for name, _ in plan.excluded])

    def test_risky_prerequisite_blocks_a_safe_candidate(self):
        items = [c("scary", 0, risk=0.9), c("safe", 1000, risk=0.0,
                                            requires=("scary",))]
        plan = optimize(items, effort_budget=10, max_risk=0.5)
        self.assertEqual(plan.chosen, [])

    def test_invalid_constraint_values_rejected(self):
        for kwargs in ({"risk_budget": 1.5}, {"max_risk": -0.1},
                       {"overlap_decay": 2.0}):
            with self.assertRaises(PortfolioError):
                optimize([c("a")], effort_budget=1, **kwargs)

    def test_duplicate_names_rejected(self):
        with self.assertRaises(PortfolioError):
            optimize([c("a"), c("a")], effort_budget=1)

    def test_overlap_changes_which_plan_wins(self):
        # Against a 2-day budget, two overlapping 1-day candidates look better
        # than one standalone until the double-count is removed.
        items = [c("solo", 1400, effort=2),
                 c("g1", 1000, effort=1, group="fleet"),
                 c("g2", 1000, effort=1, group="fleet")]
        self.assertEqual(sorted(optimize(items, 2, overlap_decay=1.0).names),
                         ["g1", "g2"])
        self.assertEqual(optimize(items, 2, overlap_decay=0.2).names, ["solo"])

    def test_rejection_reasons_are_specific(self):
        items = [c("a", 5000, effort=1), c("b", 100, effort=50),
                 c("dud", -10, effort=1)]
        plan = optimize(items, effort_budget=1)
        reasons = dict(plan.excluded)
        self.assertIn("effort budget", reasons["b"])
        self.assertIn("no expected saving", reasons["dud"])


class TestSimulate(unittest.TestCase):
    def test_certain_plan_has_no_spread(self):
        stats = simulate([c("a", 1000, confidence=1.0)], trials=200, seed=1)
        self.assertAlmostEqual(stats["p10"], 1000)
        self.assertAlmostEqual(stats["p90"], 1000)

    def test_uncertain_plan_has_a_downside(self):
        items = [c(str(i), 1000, confidence=0.5) for i in range(6)]
        stats = simulate(items, trials=4000, seed=7)
        self.assertLess(stats["p10"], stats["p50"])
        self.assertLess(stats["p50"], stats["p90"])
        self.assertAlmostEqual(stats["mean"], 3000, delta=200)

    def test_deterministic_for_a_seed(self):
        items = [c(str(i), 1000, confidence=0.6) for i in range(5)]
        self.assertEqual(simulate(items, 500, seed=3), simulate(items, 500, seed=3))

    def test_zero_confidence_plan_yields_nothing(self):
        stats = simulate([c("a", 1000, confidence=0.0)], trials=100, seed=0)
        self.assertEqual(stats["p90"], 0.0)

    def test_empty_plan(self):
        self.assertEqual(simulate([], trials=10)["p50"], 0.0)

    def test_non_positive_trials_rejected(self):
        with self.assertRaises(PortfolioError):
            simulate([c("a")], trials=0)

    def test_single_trial_does_not_crash_percentiles(self):
        self.assertIn("p50", simulate([c("a", 100, confidence=0.5)], trials=1))


if __name__ == "__main__":
    unittest.main()
