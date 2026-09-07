"""Input-hygiene edge cases: the malformed rows real cost data arrives as."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

from awscost.model import (  # noqa: E402
    Candidate,
    CandidateError,
    load_candidates,
    parse_candidate,
    parse_candidates,
)

GOOD = {"name": "gp2 -> gp3", "savings": 900, "confidence": 0.9,
        "effort": 1, "risk": 0.05}


def row(**overrides):
    merged = dict(GOOD)
    merged.update(overrides)
    return merged


class TestFieldValidation(unittest.TestCase):
    def test_happy_path(self):
        cand = parse_candidate(GOOD)
        self.assertEqual(cand.name, "gp2 -> gp3")
        self.assertAlmostEqual(cand.expected_savings, 810.0)

    def test_missing_field_names_the_field(self):
        broken = row()
        del broken["risk"]
        with self.assertRaises(CandidateError) as ctx:
            parse_candidate(broken)
        self.assertIn("risk", str(ctx.exception))
        self.assertIn("gp2 -> gp3", str(ctx.exception))

    def test_typo_in_key_suggests_the_right_one(self):
        broken = row()
        broken["saving"] = broken.pop("savings")
        with self.assertRaises(CandidateError) as ctx:
            parse_candidate(broken)
        self.assertIn("savings", str(ctx.exception))

    def test_null_is_not_zero(self):
        # A blank spreadsheet cell must not be scored as "saves $0".
        with self.assertRaises(CandidateError):
            parse_candidate(row(savings=None))

    def test_boolean_is_not_a_number(self):
        # JSON `true` would otherwise coerce to 1.0 and look like real data.
        with self.assertRaises(CandidateError):
            parse_candidate(row(savings=True))

    def test_nan_and_infinity_rejected(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(CandidateError):
                parse_candidate(row(savings=bad))

    def test_spreadsheet_number_strings_accepted(self):
        cand = parse_candidate(row(savings="$8,000", confidence="95%",
                                   effort="1.5", risk="0"))
        self.assertEqual(cand.savings, 8000.0)
        self.assertAlmostEqual(cand.confidence, 0.95)
        self.assertEqual(cand.effort, 1.5)

    def test_percentage_confidence_above_one_rejected_with_hint(self):
        with self.assertRaises(CandidateError) as ctx:
            parse_candidate(row(confidence=90))
        self.assertIn("/100", str(ctx.exception))

    def test_negative_effort_rejected_zero_effort_floored(self):
        with self.assertRaises(CandidateError):
            parse_candidate(row(effort=-1))
        cand = parse_candidate(row(effort=0))
        self.assertEqual(cand.effective_effort, 0.1)
        self.assertTrue(cand.score() > 0)  # no divide-by-zero

    def test_unit_error_in_savings_rejected(self):
        with self.assertRaises(CandidateError) as ctx:
            parse_candidate(row(savings=1e15))
        self.assertIn("units", str(ctx.exception))

    def test_negative_savings_allowed_and_never_pays_back(self):
        # A cache bought for latency, not for the bill: a real candidate that
        # costs money. It must rank last, not crash the tool.
        cand = parse_candidate(row(savings=-500))
        self.assertLess(cand.score(), 0)
        self.assertEqual(cand.payback_days(), float("inf"))

    def test_zero_confidence_never_pays_back(self):
        self.assertEqual(parse_candidate(row(confidence=0)).payback_days(),
                         float("inf"))

    def test_unicode_and_whitespace_names(self):
        cand = parse_candidate(row(name="  S3 Intelligent-Tiering (eu-west-1) ✓  "))
        self.assertEqual(cand.name, "S3 Intelligent-Tiering (eu-west-1) ✓")
        with self.assertRaises(CandidateError):
            parse_candidate(row(name="   "))

    def test_row_must_be_an_object(self):
        with self.assertRaises(CandidateError):
            parse_candidate(["gp2 -> gp3", 900, 0.9, 1, 0.05], index=3)


class TestCandidateSet(unittest.TestCase):
    def test_empty_set_is_valid(self):
        self.assertEqual(parse_candidates([]), [])

    def test_wrapper_object_accepted(self):
        self.assertEqual(len(parse_candidates({"candidates": [GOOD]})), 1)

    def test_top_level_scalar_rejected(self):
        with self.assertRaises(CandidateError):
            parse_candidates("gp2 -> gp3")

    def test_duplicate_names_rejected_case_insensitively(self):
        with self.assertRaises(CandidateError) as ctx:
            parse_candidates([GOOD, row(name="GP2 -> GP3")])
        self.assertIn("duplicate", str(ctx.exception))

    def test_dangling_reference_rejected_with_suggestion(self):
        with self.assertRaises(CandidateError) as ctx:
            parse_candidates([row(requires=["gp3 -> gp2"])])
        self.assertIn("unknown candidate", str(ctx.exception))

    def test_requirement_cycle_detected(self):
        rows = [
            row(name="a", requires=["b"]),
            row(name="b", requires=["c"]),
            row(name="c", requires=["a"]),
        ]
        with self.assertRaises(CandidateError) as ctx:
            parse_candidates(rows)
        self.assertIn("cycle", str(ctx.exception))

    def test_self_reference_rejected(self):
        with self.assertRaises(CandidateError):
            parse_candidates([row(requires=["gp2 -> gp3"])])
        with self.assertRaises(CandidateError):
            parse_candidates([row(excludes=["gp2 -> gp3"])])

    def test_diamond_dependencies_are_fine(self):
        rows = [
            row(name="tagging", requires=[]),
            row(name="showback", requires=["tagging"]),
            row(name="chargeback", requires=["tagging"]),
            row(name="unit-economics", requires=["showback", "chargeback"]),
        ]
        self.assertEqual(len(parse_candidates(rows)), 4)

    def test_requires_accepts_a_bare_string(self):
        rows = [row(name="tagging"), row(name="showback", requires="tagging")]
        self.assertEqual(parse_candidates(rows)[1].requires, ("tagging",))


class TestFileLoading(unittest.TestCase):
    def _write(self, text):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                             encoding="utf-8")
        handle.write(text)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_empty_file(self):
        with self.assertRaises(CandidateError) as ctx:
            load_candidates(self._write("   \n"))
        self.assertIn("empty", str(ctx.exception))

    def test_malformed_json_reports_the_line(self):
        with self.assertRaises(CandidateError) as ctx:
            load_candidates(self._write('[\n  {"name": "a",}\n]'))
        self.assertIn("line", str(ctx.exception))

    def test_missing_file_raises_oserror(self):
        with self.assertRaises(OSError):
            load_candidates("/nonexistent/candidates.json")

    def test_round_trip(self):
        path = self._write(json.dumps([GOOD]))
        self.assertEqual(load_candidates(path)[0].name, GOOD["name"])

    def test_shipped_example_file_is_valid(self):
        example = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "scripts", "candidates.example.json")
        self.assertTrue(load_candidates(example))


class TestScoring(unittest.TestCase):
    def test_score_formula(self):
        cand = Candidate("x", savings=1000, confidence=0.5, effort=2, risk=0.25)
        self.assertAlmostEqual(cand.score(), 1000 * 0.5 / (2 * 1.25))

    def test_ordering_prefers_cheap_certain_low_risk(self):
        risky = Candidate("risky", 1000, 0.5, 2, 0.5)
        safe = Candidate("safe", 1000, 0.9, 2, 0.05)
        self.assertGreater(safe.score(), risky.score())

    def test_payback_days(self):
        cand = Candidate("x", savings=3000, confidence=1.0, effort=1, risk=0)
        self.assertAlmostEqual(cand.payback_days(), 800 / (3000 / 30))


if __name__ == "__main__":
    unittest.main()
