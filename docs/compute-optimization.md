# Compute Optimization

Compute (EC2, Lambda, Fargate, EKS) is usually the largest line item and the one with
the widest cost–performance curve. Highest leverage lives here.

## Right-sizing — the flat-region cut

Most instances run at 10–30% CPU. That's flat-region waste: you pay for cores that
never light up.

- Use **Compute Optimizer** for data-driven recommendations (it reads CloudWatch).
- Downsize by one instance size and watch p99 for a week; repeat until headroom
  approaches your SLO budget.
- **Watch memory, not just CPU.** The default CloudWatch agent doesn't report memory;
  install it or you'll downsize into OOM kills.

| Signal | Action |
|--------|--------|
| CPU <20%, mem <40% sustained | Downsize a size (or two) |
| Bursty CPU, low average | Move to burstable `T`-family (credits) |
| Steady high CPU | Already near knee — leave it |

## Graviton — free performance-per-dollar

ARM-based Graviton instances deliver ~20–40% better price/performance than x86 for
most workloads. This is the rare optimization that moves the whole curve, not a point on it.

- **Effort:** low for interpreted stacks (Python/Node/Java/Go recompile-or-nothing),
  higher for native deps with x86 assumptions.
- **Risk:** low — same OS, same services; validate with a canary.
- Do this early; it's usually top-left on the impact/effort matrix.

## Spot — latency/reliability traded for ~70% off

Spot instances are spare capacity at up to 90% discount, reclaimable with a 2-minute warning.

**Good fit:** stateless web tiers, batch/ETL, CI runners, fault-tolerant workers,
Kubernetes nodes with graceful drain.
**Bad fit:** stateful singletons, anything that can't checkpoint or drain in 2 minutes.

Pattern: mix Spot + On-Demand in one ASG/node group with a base of On-Demand for
floor capacity and Spot for the elastic top. Diversify across instance types so a
single-pool reclaim doesn't take everything.

## Autoscaling — pay for the load you have

Static fleets sized for peak are flat-region waste 20 hours a day.

- **Target-tracking** on a demand metric (CPU, request count, queue depth) beats
  step/scheduled scaling for variable load.
- **Scheduled** scaling for known patterns (scale dev to zero nights/weekends —
  often a 60%+ non-prod saving for free).
- Set scale-in cooldowns so you don't thrash and pay startup costs repeatedly.

## Serverless breakeven (Lambda / Fargate)

Serverless removes idle cost but has a higher per-unit-of-work price. There's a
**crossover point**:

```
$/mo
 │  provisioned ────────────  ← wins at steady, high utilization
 │        ╱
 │       ╱
 │  ────╱  serverless          ← wins at spiky, low-duty-cycle load
 └──────────────────────────► utilization
```

- Spiky/low-duty-cycle → serverless (you stop paying between requests).
- Steady/high-utilization → provisioned (reserved EC2/Fargate is cheaper per unit).
- For Lambda, right-size **memory** — it also scales CPU, so more memory can be
  *cheaper* by finishing faster. Tune with Lambda Power Tuning, don't guess.

## Purchase commitments — the biggest lever, zero code

Before any engineering: cover steady baseline usage with **Savings Plans** or
**Reserved Instances** (30–50%+ off). Compute Savings Plans are flexible across
family/region/serverless. This routinely beats weeks of right-sizing effort.

→ Next: [Storage optimization](storage-optimization.md)
