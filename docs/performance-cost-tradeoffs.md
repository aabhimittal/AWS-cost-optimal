# Performance ↔ Cost Tradeoffs

Cost optimization done blindly is just performance regression with better PR.
This guide is the reasoning framework the rest of the repo hangs off.

## The cost–performance curve

Every resource choice sits on a curve:

```
performance
   ▲
   │            ●───────●  ← flat: extra $ buys headroom nobody uses
   │        ●            (over-provisioned — cut here)
   │     ●
   │   ● ← the knee: best $/performance
   │  ●
   │ ●  ← steep: cutting $ here directly costs latency (protect this)
   └─────────────────────────► $ spent
```

- **Flat region** = waste. Idle capacity, oversized instances, retained snapshots.
  Cut freely; performance is unaffected.
- **The knee** = target. Right-sized, right-tiered.
- **Steep region** = load-bearing spend. Cutting here is where "cost savings"
  turns into an incident. Touch only with an SLO measurement in hand.

**The whole game is knowing which region a given line item is in.** Most bills are
30–45% flat-region waste — attack that before touching anything steep.

## Anchor every decision to an SLO

A cost change is only valid if the Service Level Objective still holds afterward.

| Without SLO | With SLO |
|-------------|----------|
| "Downsize to save 40%" | "p99 latency budget is 200 ms; current is 60 ms; downsizing lands at 140 ms → still within budget → do it" |
| Optimize by gut feel | Optimize against a measured headroom number |

Define, per workload: the metric (p50/p99 latency, throughput, error rate),
the target, and the current headroom. Headroom *is* your optimization budget.

## Three tradeoff axes

1. **Latency vs. cost** — Spot instances, colder storage tiers, smaller caches all
   trade responsiveness for price. Acceptable when headroom exists or the path is async.
2. **Reliability vs. cost** — Single-AZ, fewer replicas, Spot interruption risk.
   Acceptable for stateless/reproducible/batch work; dangerous for stateful primaries.
3. **Engineering time vs. cost** — A bespoke optimization saving $200/mo but costing
   two engineer-weeks and ongoing fragility is a *loss*. Value your own time in the math.

## The analogy: cost as a thermostat

Don't think of the bill as a wall to knock down; think of it as a **thermostat**.
You set it to the comfort level (the SLO) and pay exactly for that — no more (freezing
the office to save money = breaching SLO), no less (heating an empty building = waste).
Optimization is *calibrating the thermostat per room*, not turning off the furnace.

## Anti-patterns

- **Optimizing the unmeasured.** No baseline → you can't prove you didn't regress.
- **Chasing the long tail.** The 15th micro-optimization saving $12/mo isn't worth
  the review time. Stop at the knee of the *effort* curve too.
- **Global settings for local problems.** One instance family for all workloads
  guarantees most are in a flat or steep region, never the knee.
- **Ignoring commitment discounts.** Engineering heroics to save 20% while leaving
  Savings Plans on the table (30–50%+) is optimizing the wrong variable.

→ Next: [Compute optimization](compute-optimization.md)
