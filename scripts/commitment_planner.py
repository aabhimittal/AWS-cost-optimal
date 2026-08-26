#!/usr/bin/env python3
"""Size a Savings Plan / RI commitment from an hourly usage series.

The optimal hourly commit is the *discount-th percentile* of hourly usage: at a
28% discount, commit to your 28th-percentile hour. Committing to "70% coverage"
because it sounds prudent either strands money in unused commitment or leaves
discount on the table. This tool finds the exact optimum from your own series
and, just as importantly, tells you when your history is too short, too spiky or
trending the wrong way to commit against at all.

Input JSON: a bare list of hourly on-demand-equivalent dollars, oldest first,
or an object:

  {"usage": [12.4, 11.9, ...], "discount": 0.28, "term_months": 12}

Usage:
  commitment_planner.py usage.json
  commitment_planner.py usage.json --discount 0.4 --term-months 36
  commitment_planner.py usage.json --trailing-window 720   # size on last 30d
  commitment_planner.py usage.json --curve                 # sensitivity table
  commitment_planner.py --demo
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from awscost.commitment import (  # noqa: E402
    CommitmentError,
    coverage_curve,
    optimal_commit_quantile,
    plan,
)


def demo_series():
    """A week of a plausible diurnal workload: 24h cycle plus a weekend dip."""
    series = []
    for hour in range(24 * 7):
        hour_of_day = hour % 24
        day = hour // 24
        base = 30.0 + 22.0 * math.sin((hour_of_day - 6) / 24.0 * 2 * math.pi)
        if day >= 5:  # weekend
            base *= 0.55
        if hour_of_day == 3:  # nightly batch
            base += 25.0
        series.append(round(max(base, 4.0), 2))
    return series


def load(path):
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    if not text.strip():
        raise CommitmentError(f"{path}: file is empty")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CommitmentError(f"{path}: invalid JSON at line {exc.lineno}: {exc.msg}")
    if isinstance(raw, dict):
        if "usage" not in raw:
            raise CommitmentError(f"{path}: object input needs a 'usage' key")
        return raw["usage"], raw.get("discount"), raw.get("term_months")
    if isinstance(raw, list):
        return raw, None, None
    raise CommitmentError(f"{path}: expected a list or an object with 'usage'")


def build_parser():
    p = argparse.ArgumentParser(
        prog="commitment_planner.py",
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("usage", nargs="?", help="JSON file of hourly usage")
    p.add_argument("--demo", action="store_true",
                   help="run against a synthetic week of diurnal usage")
    p.add_argument("--discount", type=float, default=None,
                   help="commitment discount 0..1 (default: 0.28, 1yr compute SP)")
    p.add_argument("--term-months", type=int, default=None,
                   help="commitment term, used for lock-in warnings (default: 12)")
    p.add_argument("--trailing-window", type=int, metavar="HOURS",
                   help="size the commit on only the most recent N hours")
    p.add_argument("--curve", action="store_true",
                   help="print savings and waste across commit levels")
    p.add_argument("--json", action="store_true", dest="as_json",
                   help="machine-readable output")
    return p


def main(argv):
    args = build_parser().parse_args(argv[1:])
    if not args.usage and not args.demo:
        build_parser().print_help()
        return 2
    try:
        if args.demo:
            usage, file_discount, file_term = demo_series(), None, None
        else:
            usage, file_discount, file_term = load(args.usage)
        discount = args.discount if args.discount is not None else (
            file_discount if file_discount is not None else 0.28)
        term = args.term_months if args.term_months is not None else (
            file_term if file_term is not None else 12)
        result = plan(usage, discount,
                      trailing_window=args.trailing_window, term_months=term)
        quantile_commit = optimal_commit_quantile(
            usage[-args.trailing_window:] if args.trailing_window else usage, discount)
        curve = None
        if args.curve:
            peak = max(usage) if usage else 0.0
            levels = sorted({round(i / 10.0 * peak, 6) for i in range(11)}
                            | {round(result.commit, 6)})
            curve = coverage_curve(usage, discount, levels)
    except (CommitmentError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.as_json:
        payload = {
            "commit_per_hour": round(result.commit, 4),
            "discount": result.discount,
            "hours": result.hours,
            "baseline_cost": round(result.baseline_cost, 2),
            "optimized_cost": round(result.optimized_cost, 2),
            "savings": round(result.savings, 2),
            "savings_pct": round(result.savings_pct, 2),
            "coverage_pct": round(result.coverage_pct, 2),
            "effective_discount": round(result.effective_discount, 4),
            "waste": round(result.waste, 2),
            "unused_hours": result.unused_hours,
            "closed_form_commit": round(quantile_commit, 4),
            "warnings": result.warnings,
        }
        if curve:
            payload["curve"] = [
                {"commit": round(c, 4), "savings": round(s, 2), "waste": round(w, 2)}
                for c, s, w in curve
            ]
        print(json.dumps(payload, indent=2))
        return 0

    print(f"Hours analysed:      {result.hours:,}")
    print(f"Discount assumed:    {result.discount:.0%}  ({term}-month term)")
    print(f"Recommended commit:  ${result.commit:,.2f}/hour "
          f"(${result.commit * 730:,.0f}/month)")
    print(f"  closed form (p{result.discount * 100:.0f} of hourly usage): "
          f"${quantile_commit:,.2f}/hour")
    print()
    print(f"Baseline (all on-demand):  ${result.baseline_cost:,.2f}")
    print(f"With this commitment:      ${result.optimized_cost:,.2f}")
    print(f"Savings:                   ${result.savings:,.2f} "
          f"({result.savings_pct:.1f}% of the bill)")
    print(f"Usage covered:             {result.coverage_pct:.1f}%")
    print(f"Effective discount:        {result.effective_discount:.1%} "
          f"(headline {result.discount:.0%}, less unused commitment)")
    print(f"Unused commitment:         ${result.waste:,.2f} across "
          f"{result.unused_hours:,} hour(s)")
    if curve:
        print("\n  commit/hr     savings       waste")
        print("  " + "-" * 34)
        for commit, savings, waste in curve:
            marker = "  <- optimum" if abs(commit - result.commit) < 1e-6 else ""
            print(f"  {commit:9,.2f}  {savings:10,.2f}  {waste:10,.2f}{marker}")
    if result.warnings:
        print("\nWarnings:")
        for warning in result.warnings:
            print(f"  ! {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
