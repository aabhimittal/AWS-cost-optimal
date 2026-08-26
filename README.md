# AWS Cost Optimal

A practical playbook for **AWS cost optimization framed as a performance ↔ cost tradeoff** —
not "cut the bill," but "spend where it buys performance that matters, starve where it doesn't."

> Mental model: cost is a *dial*, not a *floor*. Every AWS resource sits on a curve of
> `$ spent → performance delivered`. Optimization is finding the knee of that curve for
> each workload, then moving spend from the flat part (paying for headroom nobody uses)
> to the steep part (paying for latency users feel).

## The core idea: impact vs. effort

Optimizations are not equal. Sort them by **savings × confidence** against
**engineering effort × performance risk**. Do the top-left first.

```
 High savings │  Graviton migration      Spot for stateless
              │  S3 lifecycle tiering     Right-sizing
              │  ─────────────────────────────────────────
 Low savings  │  Idle resource cleanup    Bespoke caching layers
              │  (do anyway, trivial)     (high effort, fragile)
              └────────────────────────────────────────────
                 Low effort/risk            High effort/risk
```

`scripts/cost_impact_matrix.py` scores a list of candidate optimizations on exactly
this basis so you attack them in the right order — and with `--budget`, turns the
ranking into a plan that fits the engineer-days you have, de-duplicates overlapping
savings, honours prerequisites and exclusions, and caps aggregate SLO risk.

## Contents

| Guide | What it covers |
|-------|----------------|
| [Performance ↔ cost tradeoffs](docs/performance-cost-tradeoffs.md) | The framework: how to reason about the curve, SLO-anchored budgets |
| [Compute](docs/compute-optimization.md) | Right-sizing, Graviton, Spot, autoscaling, serverless breakeven |
| [Storage](docs/storage-optimization.md) | S3 tiers, EBS types, lifecycle, the retrieval-latency tax |
| [Database & networking](docs/database-and-networking.md) | RDS/Aurora, caching, data transfer, the NAT/egress traps |
| [Novel approaches](docs/novel-approaches.md) | Beyond the checklist: carbon-aware, FinOps-as-code, commit-at-the-percentile, portfolio thinking |
| [Decision framework](docs/decision-framework.md) | How to prioritize and avoid over-optimizing |
| [Tooling](docs/tooling.md) | The three scripts, what each one decides, and how to wire them into CI |
| [Industrial edge cases](docs/industrial-edge-cases.md) | What breaks these methods in a real estate, and the rule for each |

## Quick start

```bash
# 1. Which optimizations, in what order, with the days we actually have?
python3 scripts/cost_impact_matrix.py                      # built-in example set
python3 scripts/cost_impact_matrix.py my.json --budget 10 --simulate --explain

# 2. How much do we commit to -- and is this workload safe to commit against?
python3 scripts/commitment_planner.py --demo --curve
python3 scripts/commitment_planner.py usage.json --discount 0.28

# 3. Is this fleet over-provisioned, or does it only look that way?
python3 scripts/rightsizing_advisor.py --demo
python3 scripts/rightsizing_advisor.py fleet.json --cost-per-unit 30 --json

python3 -m unittest discover -s tests      # 164 tests, mostly edge cases
```

Stdlib-only Python 3.9+. No dependencies, no AWS credentials, no telemetry —
these read your numbers, not your account.

## Three results worth knowing even if you never run the scripts

1. **The optimal commitment is the discount-th percentile of hourly usage.** At a
   28% discount, commit to your 28th-percentile hour — far below the "cover 70%"
   folklore. ([why](docs/novel-approaches.md#7-commit-at-the-discount-percentile-not-at-a-coverage-target))
2. **Risk compounds multiplicatively.** Ten changes at "2% risk" carry an 18%
   chance the quarter contains an incident: `1 − Π(1 − riskᵢ)`, not a sum.
3. **100% CPU is a censored measurement, not an efficient one.** The metric is
   clipped at the ceiling; true demand is unknown and higher.
   ([the rest of the catalogue](docs/industrial-edge-cases.md))

## Principle before tactics

1. **Measure before cutting.** Cost Explorer + tags tell you where the money is;
   never optimize an unmeasured workload.
2. **Anchor to an SLO.** "Cheaper" is only correct if the SLO still holds.
   A saving that breaches latency is a regression wearing a discount.
3. **Optimize the bill's shape, not just its size.** Reserved/Savings Plans,
   commitment coverage, and waste elimination usually beat clever engineering.
