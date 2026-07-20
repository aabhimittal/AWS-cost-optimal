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
this basis so you attack them in the right order.

## Contents

| Guide | What it covers |
|-------|----------------|
| [Performance ↔ cost tradeoffs](docs/performance-cost-tradeoffs.md) | The framework: how to reason about the curve, SLO-anchored budgets |
| [Compute](docs/compute-optimization.md) | Right-sizing, Graviton, Spot, autoscaling, serverless breakeven |
| [Storage](docs/storage-optimization.md) | S3 tiers, EBS types, lifecycle, the retrieval-latency tax |
| [Database & networking](docs/database-and-networking.md) | RDS/Aurora, caching, data transfer, the NAT/egress traps |
| [Novel approaches](docs/novel-approaches.md) | Beyond the checklist: carbon-aware, FinOps-as-code, right-sizing by SLO |
| [Decision framework](docs/decision-framework.md) | How to prioritize and avoid over-optimizing |

## Quick start

```bash
python3 scripts/cost_impact_matrix.py            # scores the built-in example set
python3 scripts/cost_impact_matrix.py my.json    # score your own candidates
```

## Principle before tactics

1. **Measure before cutting.** Cost Explorer + tags tell you where the money is;
   never optimize an unmeasured workload.
2. **Anchor to an SLO.** "Cheaper" is only correct if the SLO still holds.
   A saving that breaches latency is a regression wearing a discount.
3. **Optimize the bill's shape, not just its size.** Reserved/Savings Plans,
   commitment coverage, and waste elimination usually beat clever engineering.
