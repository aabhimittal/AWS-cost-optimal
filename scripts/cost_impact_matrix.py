#!/usr/bin/env python3
"""Rank AWS cost-optimization candidates by impact vs. effort and performance risk.

Score = savings * confidence / (effort * (1 + risk))

  savings     estimated $/month saved
  confidence  0..1, how sure the saving materializes
  effort      engineer-days to implement + validate (>0)
  risk        0..1, probability x severity of an SLO regression

Higher score = do it sooner. See docs/decision-framework.md for the reasoning.

Usage:
  python3 scripts/cost_impact_matrix.py                 # score built-in examples
  python3 scripts/cost_impact_matrix.py candidates.json # score your own
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass

# Built-in example set, ordered roughly by the repo's default recommendation.
EXAMPLES = [
    # name, savings $/mo, confidence, effort days, risk
    ("Savings Plans on steady baseline", 8000, 0.95, 1, 0.02),
    ("Delete orphaned EBS + old snapshots", 1200, 0.99, 0.5, 0.0),
    ("gp2 -> gp3 migration", 900, 0.9, 1, 0.05),
    ("Graviton migration (interpreted stack)", 4000, 0.8, 4, 0.1),
    ("Right-size over-provisioned fleet", 3500, 0.75, 3, 0.25),
    ("S3 Intelligent-Tiering", 1500, 0.85, 1, 0.05),
    ("Stop non-prod nights/weekends", 2200, 0.9, 1.5, 0.05),
    ("Spot for stateless web + CI", 5000, 0.7, 5, 0.3),
    ("VPC Gateway Endpoint for S3 (kill NAT cost)", 1800, 0.85, 2, 0.05),
    ("Bespoke cross-region cache layer", 600, 0.5, 12, 0.45),
]


@dataclass
class Candidate:
    name: str
    savings: float
    confidence: float
    effort: float
    risk: float

    def score(self) -> float:
        effort = max(self.effort, 0.1)  # guard divide-by-zero
        return (self.savings * self.confidence) / (effort * (1 + self.risk))

    def payback_days(self) -> float:
        """Rough engineer-days to break even, assuming ~$800/day loaded cost."""
        monthly = self.savings * self.confidence
        if monthly <= 0:
            return float("inf")
        return (self.effort * 800) / (monthly / 30)


def load(path: str) -> list[Candidate]:
    with open(path) as f:
        raw = json.load(f)
    return [Candidate(**row) for row in raw]


def from_examples() -> list[Candidate]:
    return [Candidate(n, s, c, e, r) for n, s, c, e, r in EXAMPLES]


def report(candidates: list[Candidate]) -> None:
    ranked = sorted(candidates, key=lambda c: c.score(), reverse=True)
    name_w = max((len(c.name) for c in ranked), default=4)
    print(f"{'#':>2}  {'candidate':<{name_w}}  {'score':>8}  {'$/mo':>7}  "
          f"{'effort':>6}  {'risk':>4}  {'payback':>8}")
    print("-" * (name_w + 46))
    for i, c in enumerate(ranked, 1):
        pb = c.payback_days()
        pb_str = "never" if pb == float("inf") else f"{pb:5.0f}d"
        flag = "  <- steep region: verify SLO" if c.risk >= 0.3 else ""
        print(f"{i:>2}  {c.name:<{name_w}}  {c.score():>8.0f}  "
              f"{c.savings:>7.0f}  {c.effort:>5.1f}d  {c.risk:>4.2f}  {pb_str:>8}{flag}")
    total = sum(c.savings * c.confidence for c in ranked)
    print("-" * (name_w + 46))
    print(f"Confidence-weighted savings if all applied: ${total:,.0f}/mo")


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        candidates = load(argv[1])
        print(f"Scoring {len(candidates)} candidate(s) from {argv[1]}\n")
    else:
        candidates = from_examples()
        print("Scoring built-in example set "
              "(pass a JSON file to score your own)\n")
    report(candidates)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
