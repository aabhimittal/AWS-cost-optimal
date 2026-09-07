"""End-to-end CLI behaviour: exit codes, JSON contracts, error messages.

A cost tool that gets wired into CI is only useful if a bad input fails loudly
(exit 2 with a message naming the problem) rather than printing a plausible
ranking of garbage.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "scripts")


def run(script, *args):
    proc = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, script), *args],
        capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


class CLITestCase(unittest.TestCase):
    def write(self, payload):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                             encoding="utf-8")
        handle.write(payload if isinstance(payload, str) else json.dumps(payload))
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name


class TestCostImpactMatrix(CLITestCase):
    def test_default_run_still_works(self):
        # The documented zero-argument invocation must not regress.
        code, out, _ = run("cost_impact_matrix.py")
        self.assertEqual(code, 0)
        self.assertIn("Savings Plans on steady baseline", out)
        self.assertIn("Confidence-weighted savings", out)

    def test_shipped_example_file(self):
        code, out, _ = run("cost_impact_matrix.py",
                           os.path.join(SCRIPTS, "candidates.example.json"))
        self.assertEqual(code, 0)
        self.assertIn("Spot for CI runners", out)

    def test_budget_plan_fits_the_budget(self):
        code, out, _ = run("cost_impact_matrix.py", "--budget", "3", "--json")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertLessEqual(payload["plan"]["effort"], 3 + 1e-9)
        self.assertTrue(payload["plan"]["exact"])

    def test_json_contract(self):
        code, out, _ = run("cost_impact_matrix.py", "--budget", "5",
                           "--simulate", "--trials", "500", "--json")
        payload = json.loads(out)
        self.assertEqual(code, 0)
        for key in ("ranked", "aggregate_risk", "net_expected_savings", "plan"):
            self.assertIn(key, payload)
        sim = payload["plan"]["simulation"]
        self.assertLessEqual(sim["p10"], sim["p90"])

    def test_simulation_is_reproducible(self):
        args = ("cost_impact_matrix.py", "--budget", "5", "--simulate",
                "--trials", "300", "--seed", "42", "--json")
        self.assertEqual(run(*args)[1], run(*args)[1])

    def test_explain_lists_rejections(self):
        code, out, _ = run("cost_impact_matrix.py", "--budget", "1", "--explain")
        self.assertEqual(code, 0)
        self.assertIn("Not in the plan:", out)

    def test_risk_budget_without_budget_warns(self):
        code, _, err = run("cost_impact_matrix.py", "--risk-budget", "0.2")
        self.assertEqual(code, 0)
        self.assertIn("only apply with --budget", err)

    def test_bad_candidate_file_exits_two(self):
        path = self.write([{"name": "a", "savings": 1, "confidence": 9,
                            "effort": 1, "risk": 0}])
        code, _, err = run("cost_impact_matrix.py", path)
        self.assertEqual(code, 2)
        self.assertIn("confidence", err)

    def test_missing_file_exits_two(self):
        code, _, err = run("cost_impact_matrix.py", "/nonexistent.json")
        self.assertEqual(code, 2)
        self.assertIn("error:", err)

    def test_empty_candidate_list_is_not_a_crash(self):
        code, out, _ = run("cost_impact_matrix.py", self.write([]))
        self.assertEqual(code, 0)
        self.assertIn("No candidates", out)

    def test_overlap_is_reported_when_groups_overlap(self):
        code, out, _ = run("cost_impact_matrix.py")
        self.assertIn("double-counted", out)


class TestCommitmentPlanner(CLITestCase):
    def test_demo_runs(self):
        code, out, _ = run("commitment_planner.py", "--demo")
        self.assertEqual(code, 0)
        self.assertIn("Recommended commit", out)

    def test_json_contract_and_closed_form_agreement(self):
        code, out, _ = run("commitment_planner.py", "--demo", "--json")
        payload = json.loads(out)
        self.assertEqual(code, 0)
        self.assertGreater(payload["savings"], 0)
        self.assertLessEqual(payload["effective_discount"], payload["discount"])
        self.assertAlmostEqual(payload["commit_per_hour"],
                               payload["closed_form_commit"], delta=1.0)

    def test_bare_list_input(self):
        code, out, _ = run("commitment_planner.py",
                           self.write([10.0] * 800), "--discount", "0.3", "--json")
        self.assertEqual(code, 0)
        self.assertAlmostEqual(json.loads(out)["commit_per_hour"], 10.0)

    def test_object_input_carries_its_own_discount(self):
        path = self.write({"usage": [10.0] * 800, "discount": 0.5})
        payload = json.loads(run("commitment_planner.py", path, "--json")[1])
        self.assertEqual(payload["discount"], 0.5)

    def test_cli_discount_overrides_the_file(self):
        path = self.write({"usage": [10.0] * 800, "discount": 0.5})
        payload = json.loads(
            run("commitment_planner.py", path, "--discount", "0.2", "--json")[1])
        self.assertEqual(payload["discount"], 0.2)

    def test_curve_prints_the_optimum(self):
        code, out, _ = run("commitment_planner.py", "--demo", "--curve")
        self.assertEqual(code, 0)
        self.assertIn("<- optimum", out)

    def test_warnings_surface_for_a_shrinking_workload(self):
        path = self.write([100.0] * 600 + [20.0] * 200)
        code, out, _ = run("commitment_planner.py", path)
        self.assertEqual(code, 0)
        self.assertIn("strand", out)

    def test_bad_usage_exits_two(self):
        code, _, err = run("commitment_planner.py", self.write([1.0, -2.0]))
        self.assertEqual(code, 2)
        self.assertIn("usage[1]", err)

    def test_no_arguments_prints_help(self):
        code, out, _ = run("commitment_planner.py")
        self.assertEqual(code, 2)
        self.assertIn("usage:", out.lower())


class TestRightsizingAdvisor(CLITestCase):
    def test_demo_runs_and_recommends(self):
        code, out, _ = run("rightsizing_advisor.py", "--demo")
        self.assertEqual(code, 0)
        self.assertIn("Recommendation:", out)
        self.assertIn("Bill impact:", out)

    def test_json_contract(self):
        payload = json.loads(run("rightsizing_advisor.py", "--demo", "--json")[1])
        for key in ("action", "recommended_capacity", "p99", "confidence", "reasons"):
            self.assertIn(key, payload)
        self.assertEqual(payload["action"], "downsize")

    def test_capacity_is_required(self):
        code, _, err = run("rightsizing_advisor.py", self.write([10.0] * 300))
        self.assertEqual(code, 2)
        self.assertIn("capacity", err)

    def test_saturated_series_never_downsizes(self):
        path = self.write({"samples": [100.0] * 600, "capacity": 8})
        payload = json.loads(run("rightsizing_advisor.py", path, "--json")[1])
        self.assertEqual(payload["action"], "upsize")

    def test_short_series_is_refused_then_forced(self):
        path = self.write({"samples": [10.0] * 30, "capacity": 8})
        refused = json.loads(run("rightsizing_advisor.py", path, "--json")[1])
        self.assertEqual(refused["action"], "insufficient-data")
        forced = json.loads(run("rightsizing_advisor.py", path,
                                "--allow-short-series", "--json")[1])
        self.assertEqual(forced["action"], "downsize")
        self.assertLess(forced["confidence"], 1.0)

    def test_custom_ladder_flag(self):
        path = self.write({"samples": [10.0] * 600, "capacity": 16})
        payload = json.loads(
            run("rightsizing_advisor.py", path, "--ladder", "6,16", "--json")[1])
        self.assertEqual(payload["recommended_capacity"], 6.0)

    def test_bad_ladder_flag_exits_two(self):
        path = self.write({"samples": [10.0] * 600, "capacity": 16})
        code, _, err = run("rightsizing_advisor.py", path, "--ladder", "small,big")
        self.assertEqual(code, 2)
        self.assertIn("ladder", err)

    def test_out_of_range_samples_exit_two(self):
        path = self.write({"samples": [140.0] * 600, "capacity": 8})
        code, _, err = run("rightsizing_advisor.py", path)
        self.assertEqual(code, 2)
        self.assertIn("normalise", err)

    def test_empty_file_exits_two(self):
        code, _, err = run("rightsizing_advisor.py", self.write("  "))
        self.assertEqual(code, 2)
        self.assertIn("empty", err)


if __name__ == "__main__":
    unittest.main()
