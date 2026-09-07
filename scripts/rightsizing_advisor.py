#!/usr/bin/env python3
"""Recommend a capacity from utilisation history, with the traps checked.

Right-sizing on the mean is how right-sizing causes incidents. This tool sizes
on p99 against an explicit headroom target, and refuses to recommend a downsize
when the evidence does not support one: a censored (pinned-at-100%) metric, a
p99 trending up, too few samples to have seen a daily cycle, or gaps big enough
to hide the peak.

Input JSON: a bare list of utilisation percentages (0..100, oldest first, null
for gaps), or an object:

  {"samples": [12.1, null, 14.0, ...], "capacity": 8,
   "cost_per_unit": 30, "interval_minutes": 5}

Usage:
  rightsizing_advisor.py fleet.json
  rightsizing_advisor.py fleet.json --target-peak 0.7 --cost-per-unit 30
  rightsizing_advisor.py fleet.json --ladder 2,4,8,16 --allow-short-series
  rightsizing_advisor.py --demo
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from awscost.rightsizing import SizingError, advise  # noqa: E402


def demo_samples():
    """Two weeks of 5-minute CPU on a comfortably over-provisioned fleet."""
    samples = []
    for i in range(2 * 7 * 24 * 12):
        minute_of_day = (i * 5) % (24 * 60)
        hour = minute_of_day / 60.0
        base = 9.0 + 6.0 * math.sin((hour - 7) / 24.0 * 2 * math.pi)
        if int(hour) == 2 and i % 12 == 0:  # nightly batch spike
            base += 22.0
        samples.append(round(max(base, 1.0), 2))
    return samples


def load(path):
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    if not text.strip():
        raise SizingError(f"{path}: file is empty")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SizingError(f"{path}: invalid JSON at line {exc.lineno}: {exc.msg}")
    if isinstance(raw, dict):
        if "samples" not in raw:
            raise SizingError(f"{path}: object input needs a 'samples' key")
        return raw
    if isinstance(raw, list):
        return {"samples": raw}
    raise SizingError(f"{path}: expected a list or an object with 'samples'")


def parse_ladder(text):
    if not text:
        return None
    try:
        return [float(part) for part in text.split(",") if part.strip()]
    except ValueError:
        raise SizingError(f"--ladder must be comma-separated numbers, got {text!r}")


def build_parser():
    p = argparse.ArgumentParser(
        prog="rightsizing_advisor.py",
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("samples", nargs="?", help="JSON file of utilisation samples")
    p.add_argument("--demo", action="store_true",
                   help="run against a synthetic over-provisioned fleet")
    p.add_argument("--capacity", type=float, default=None,
                   help="current capacity in vCPU or any linear unit")
    p.add_argument("--target-peak", type=float, default=0.60,
                   help="utilisation the p99 should land at (default: %(default)s)")
    p.add_argument("--ladder", default=None,
                   help="comma-separated allowed sizes (default: doubling ladder)")
    p.add_argument("--interval-minutes", type=float, default=None,
                   help="sampling interval (default: 5)")
    p.add_argument("--cost-per-unit", type=float, default=None,
                   help="$/month per capacity unit, to price the recommendation")
    p.add_argument("--min-samples", type=int, default=None,
                   help="samples required before advising (default: 288 = 1 day)")
    p.add_argument("--horizon-days", type=float, default=30.0,
                   help="growth projection horizon (default: %(default)s)")
    p.add_argument("--allow-short-series", action="store_true",
                   help="advise anyway on a short series (marks low confidence)")
    p.add_argument("--json", action="store_true", dest="as_json",
                   help="machine-readable output")
    return p


def main(argv):
    args = build_parser().parse_args(argv[1:])
    if not args.samples and not args.demo:
        build_parser().print_help()
        return 2
    try:
        data = {"samples": demo_samples(), "capacity": 16, "cost_per_unit": 30} \
            if args.demo else load(args.samples)
        capacity = args.capacity if args.capacity is not None else data.get("capacity")
        if capacity is None:
            raise SizingError(
                "current capacity is required: pass --capacity or a 'capacity' key")
        kwargs = {
            "target_peak_util": args.target_peak,
            "ladder": parse_ladder(args.ladder) or data.get("ladder"),
            "horizon_days": args.horizon_days,
            "allow_short_series": args.allow_short_series,
        }
        interval = args.interval_minutes or data.get("interval_minutes")
        if interval is not None:
            kwargs["sample_interval_minutes"] = interval
        if args.min_samples is not None:
            kwargs["min_samples"] = args.min_samples
        result = advise(data["samples"], capacity, **kwargs)
    except (SizingError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    cost_per_unit = args.cost_per_unit if args.cost_per_unit is not None \
        else data.get("cost_per_unit")
    saving = result.monthly_saving(cost_per_unit) if cost_per_unit else None

    if args.as_json:
        payload = {
            "action": result.action,
            "current_capacity": result.current_capacity,
            "recommended_capacity": result.recommended_capacity,
            "capacity_delta_pct": round(result.capacity_delta_pct, 1),
            "p50": round(result.p50, 2),
            "p99": round(result.p99, 2),
            "peak": round(result.peak, 2),
            "samples": result.samples,
            "missing_share": round(result.missing_share, 4),
            "confidence": round(result.confidence, 2),
            "reasons": result.reasons,
        }
        if saving is not None:
            payload["monthly_saving"] = round(saving, 2)
        print(json.dumps(payload, indent=2))
        return 0

    print(f"Recommendation:  {result.action.upper()}")
    print(f"Capacity:        {result.current_capacity:g} -> "
          f"{result.recommended_capacity:g} units "
          f"({result.capacity_delta_pct:+.0f}%)")
    print(f"Utilisation:     p50 {result.p50:.1f}%   p99 {result.p99:.1f}%   "
          f"peak {result.peak:.1f}%")
    print(f"Evidence:        {result.samples:,} samples, "
          f"{result.missing_share:.0%} missing, confidence {result.confidence:.0%}")
    if saving is not None:
        verb = "saves" if saving >= 0 else "costs"
        print(f"Bill impact:     {verb} ${abs(saving):,.0f}/month "
              f"at ${cost_per_unit:,.0f}/unit")
    if result.reasons:
        print("\nWhy:")
        for reason in result.reasons:
            print(f"  - {reason}")
    if result.confidence < 0.5 and result.action != "insufficient-data":
        print("\n  ! Low confidence: treat this as a hypothesis to test in "
              "non-prod, not a change to apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
