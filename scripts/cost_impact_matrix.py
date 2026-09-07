#!/usr/bin/env python3
"""Rank AWS cost-optimization candidates by impact vs. effort and performance risk.

Score = savings * confidence / (effort * (1 + risk))

  savings     estimated $/month saved
  confidence  0..1, how sure the saving materializes
  effort      engineer-days to implement + validate (>0)
  risk        0..1, probability x severity of an SLO regression

Higher score = do it sooner. See docs/decision-framework.md for the reasoning.

Ranking tells you the order. `--budget` answers the question a team actually
has -- "which of these do we ship with the days we have?" -- as a constrained
selection that de-duplicates overlapping savings, honours prerequisites and
exclusions, and caps aggregate SLO risk. See docs/tooling.md.

Usage:
  cost_impact_matrix.py                          # score built-in examples
  cost_impact_matrix.py candidates.json          # score your own
  cost_impact_matrix.py c.json --budget 10       # plan a quarter's 10 days
  cost_impact_matrix.py c.json --budget 10 --risk-budget 0.25 --simulate
  cost_impact_matrix.py c.json --json            # machine-readable, for CI
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from awscost import portfolio  # noqa: E402
from awscost.model import (  # noqa: E402
    Candidate,
    CandidateError,
    load_candidates,
    parse_candidates,
)

# Built-in example set, ordered roughly by the repo's default recommendation.
# `group` marks candidates that harvest the same underlying spend, so the planner
# does not bank the same dollar twice.
EXAMPLES = [
    {"name": "Savings Plans on steady baseline", "savings": 8000,
     "confidence": 0.95, "effort": 1, "risk": 0.02, "group": "compute-baseline"},
    {"name": "Delete orphaned EBS + old snapshots", "savings": 1200,
     "confidence": 0.99, "effort": 0.5, "risk": 0.0},
    {"name": "gp2 -> gp3 migration", "savings": 900,
     "confidence": 0.9, "effort": 1, "risk": 0.05},
    {"name": "Graviton migration (interpreted stack)", "savings": 4000,
     "confidence": 0.8, "effort": 4, "risk": 0.1, "group": "compute-baseline"},
    {"name": "Right-size over-provisioned fleet", "savings": 3500,
     "confidence": 0.75, "effort": 3, "risk": 0.25, "group": "compute-baseline"},
    {"name": "S3 Intelligent-Tiering", "savings": 1500,
     "confidence": 0.85, "effort": 1, "risk": 0.05},
    {"name": "Stop non-prod nights/weekends", "savings": 2200,
     "confidence": 0.9, "effort": 1.5, "risk": 0.05},
    {"name": "Spot for stateless web + CI", "savings": 5000,
     "confidence": 0.7, "effort": 5, "risk": 0.3,
     "excludes": ["Savings Plans on steady baseline"]},
    {"name": "VPC Gateway Endpoint for S3 (kill NAT cost)", "savings": 1800,
     "confidence": 0.85, "effort": 2, "risk": 0.05},
    {"name": "Bespoke cross-region cache layer", "savings": 600,
     "confidence": 0.5, "effort": 12, "risk": 0.45},
]


def from_examples():
    return parse_candidates(EXAMPLES)


def report(candidates, overlap_decay=portfolio.DEFAULT_OVERLAP_DECAY):
    if not candidates:
        print("No candidates to score.")
        return
    ranked = sorted(candidates, key=lambda c: (-c.score(), c.name))
    name_w = max(len(c.name) for c in ranked)
    print(f"{'#':>2}  {'candidate':<{name_w}}  {'score':>8}  {'$/mo':>7}  "
          f"{'effort':>6}  {'risk':>4}  {'payback':>8}")
    print("-" * (name_w + 46))
    for i, c in enumerate(ranked, 1):
        pb = c.payback_days()
        pb_str = "never" if pb == float("inf") else f"{pb:5.0f}d"
        flag = "  <- steep region: verify SLO" if c.risk >= 0.3 else ""
        print(f"{i:>2}  {c.name:<{name_w}}  {c.score():>8.0f}  "
              f"{c.savings:>7.0f}  {c.effort:>5.1f}d  {c.risk:>4.2f}  {pb_str:>8}{flag}")
    print("-" * (name_w + 46))
    gross = sum(c.expected_savings for c in ranked)
    net = portfolio.net_savings(ranked, overlap_decay)
    print(f"Confidence-weighted savings if all applied: ${gross:,.0f}/mo")
    if net < gross - 1e-9:
        print(f"  net of overlapping candidates:            ${net:,.0f}/mo "
              f"(${gross - net:,.0f} would be double-counted)")
    risk = portfolio.aggregate_risk(ranked)
    print(f"P(at least one SLO regression) if all applied: {risk:.0%}")


def report_plan(plan, args):
    if not plan.chosen:
        print(f"No candidate fits a {args.budget:g}-day budget.")
    else:
        name_w = max(len(c.name) for c in plan.chosen)
        print(f"Plan for {args.budget:g} engineer-days"
              f"{'' if plan.exact else '  (greedy: too many candidates for exact search)'}")
        print(f"{'#':>2}  {'candidate':<{name_w}}  {'$/mo':>8}  {'effort':>6}  {'risk':>5}")
        print("-" * (name_w + 30))
        for i, c in enumerate(plan.chosen, 1):
            print(f"{i:>2}  {c.name:<{name_w}}  {c.expected_savings:>8,.0f}  "
                  f"{c.effective_effort:>5.1f}d  {c.risk:>5.2f}")
        print("-" * (name_w + 30))
        print(f"Net savings:  ${plan.net_savings:,.0f}/mo "
              f"({plan.effort:g} days, {plan.aggregate_risk:.0%} aggregate risk)")
        if plan.overlap_loss > 1e-9:
            print(f"Overlap:      ${plan.overlap_loss:,.0f}/mo of the raw total is "
                  "the same spend counted twice")
    if args.explain and plan.excluded:
        print("\nNot in the plan:")
        for name, reason in plan.excluded:
            print(f"  - {name}: {reason}")
    if args.simulate:
        stats = portfolio.simulate(
            plan.chosen, trials=args.trials, seed=args.seed,
            overlap_decay=args.overlap_decay,
        )
        print(f"\nRealised savings across {args.trials:,} trials: "
              f"p10 ${stats['p10']:,.0f}  p50 ${stats['p50']:,.0f}  "
              f"p90 ${stats['p90']:,.0f}")
        print("Commit to the p10 externally; plan against the p50 internally.")


def as_json(candidates, plan, args):
    payload = {
        "ranked": [
            {
                "name": c.name,
                "score": round(c.score(), 2),
                "savings": c.savings,
                "expected_savings": round(c.expected_savings, 2),
                "effort": c.effective_effort,
                "risk": c.risk,
                "payback_days": None if c.payback_days() == float("inf")
                else round(c.payback_days(), 1),
            }
            for c in sorted(candidates, key=lambda c: (-c.score(), c.name))
        ],
        "aggregate_risk": round(portfolio.aggregate_risk(candidates), 4),
        "gross_expected_savings": round(
            sum(c.expected_savings for c in candidates), 2),
        "net_expected_savings": round(
            portfolio.net_savings(candidates, args.overlap_decay), 2),
    }
    if plan is not None:
        payload["plan"] = {
            "budget_days": args.budget,
            "exact": plan.exact,
            "chosen": plan.names,
            "effort": round(plan.effort, 2),
            "net_savings": round(plan.net_savings, 2),
            "aggregate_risk": round(plan.aggregate_risk, 4),
            "excluded": [{"name": n, "reason": r} for n, r in plan.excluded],
        }
        if args.simulate:
            payload["plan"]["simulation"] = {
                k: round(v, 2)
                for k, v in portfolio.simulate(
                    plan.chosen, trials=args.trials, seed=args.seed,
                    overlap_decay=args.overlap_decay,
                ).items()
            }
    print(json.dumps(payload, indent=2))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cost_impact_matrix.py",
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("candidates", nargs="?",
                   help="JSON file of candidates (default: built-in examples)")
    p.add_argument("--budget", type=float, metavar="DAYS",
                   help="engineer-days available; plans a constrained portfolio")
    p.add_argument("--risk-budget", type=float, metavar="P",
                   help="cap on P(at least one SLO regression), 0..1")
    p.add_argument("--max-risk", type=float, metavar="P",
                   help="drop any single candidate riskier than this, 0..1")
    p.add_argument("--overlap-decay", type=float,
                   default=portfolio.DEFAULT_OVERLAP_DECAY, metavar="D",
                   help="credit for each extra candidate in an overlap group "
                        "(default: %(default)s)")
    p.add_argument("--simulate", action="store_true",
                   help="Monte-Carlo the plan's realised savings")
    p.add_argument("--trials", type=int, default=10000,
                   help="simulation trials (default: %(default)s)")
    p.add_argument("--seed", type=int, default=0,
                   help="simulation seed, for reproducible intervals")
    p.add_argument("--explain", action="store_true",
                   help="say why each candidate missed the plan")
    p.add_argument("--json", action="store_true", dest="as_json",
                   help="machine-readable output, for CI gates")
    return p


def main(argv):
    args = build_parser().parse_args(argv[1:])
    try:
        if args.candidates:
            candidates = load_candidates(args.candidates)
            source = f"{len(candidates)} candidate(s) from {args.candidates}"
        else:
            candidates = from_examples()
            source = "built-in example set (pass a JSON file to score your own)"

        plan = None
        if args.budget is not None:
            plan = portfolio.optimize(
                candidates,
                effort_budget=args.budget,
                risk_budget=args.risk_budget,
                max_risk=args.max_risk,
                overlap_decay=args.overlap_decay,
            )
        elif args.risk_budget is not None or args.max_risk is not None:
            print("note: --risk-budget/--max-risk only apply with --budget",
                  file=sys.stderr)
    except (CandidateError, portfolio.PortfolioError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.as_json:
        as_json(candidates, plan, args)
        return 0

    print(f"Scoring {source}\n")
    report(candidates, args.overlap_decay)
    if plan is not None:
        print()
        report_plan(plan, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
