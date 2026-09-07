# Novel Approaches

The checklist optimizations (right-size, tier, commit, Spot) are table stakes. These
are the higher-leverage, less-obvious moves — the "novel approaches" that treat cost
as a first-class engineering dimension rather than a cleanup chore.

## 1. SLO-driven right-sizing (optimize to the budget, not to zero)

Instead of "how small can this go," ask "how much latency headroom does the SLO give
me, and how much of it can I spend on cost?" Encode the SLO, measure current headroom,
and size so the remaining headroom equals your risk tolerance. This turns right-sizing
from guesswork into a control loop with a setpoint.

See `scripts/cost_impact_matrix.py` for scoring candidates against risk explicitly.

## 2. FinOps-as-code — cost checks in CI

Waste is cheapest to prevent at the pull request, not the monthly review.

- **Infracost** in CI: every Terraform PR gets a cost diff comment. Reviewers see
  "+$1,400/mo" before merge, not after.
- **Policy-as-code** (OPA / Sentinel / cfn-guard): block untagged resources, gp2
  volumes, oversized instance families, public NAT-heavy patterns.
- **Budget alarms + anomaly detection** (AWS Budgets, Cost Anomaly Detection) wired
  to alerts so a runaway spend pages someone in hours, not at month-end.

Cost becomes a reviewable diff, like performance or security.

## 3. Carbon-aware = cost-aware scheduling

Deferring flexible batch work (ETL, ML training, report generation) to off-peak windows
and cheaper regions cuts both carbon *and* cost — Spot prices and grid carbon intensity
often bottom out together. Schedule interruptible work when capacity is cheap and green.

## 4. Tiered compute by request value

Not all requests deserve the same hardware. Route:
- Latency-critical, revenue-bearing paths → On-Demand, latest-gen, warm caches.
- Background/best-effort/free-tier traffic → Spot, Graviton, colder caches.

Segment the fleet by request value so premium spend follows premium requests, instead
of one uniform (over- or under-provisioned) tier for everything.

## 5. Cache the expensive, not the frequent

Standard advice caches hot keys. The cost-optimal move is caching by **cost-to-recompute**:
a rarely-hit query that triggers a huge cross-region join or a heavy Lambda fan-out may
be worth caching even at low hit-rate, because each miss is expensive. Weight cache
decisions by `hit_rate × cost_per_miss`, not hit-rate alone.

## 6. Delete as a feature

The cheapest resource is the one that doesn't exist. Institutionalize deletion:
- TTLs on everything ephemeral (logs, temp buckets, preview environments).
- Auto-expiring dev/preview stacks (spin up per-PR, destroy on merge).
- Quarterly "resource archaeology" — orphaned load balancers, idle endpoints,
  forgotten NAT gateways, zombie clusters. These are pure margin.

## 7. Commit at the discount percentile, not at a coverage target

Commitment coverage is usually set by feel — "cover 70%, that feels prudent". It has a
closed form. With an hourly usage series and a discount `d`, total cost is convex in the
commit level and minimised exactly where the fraction of hours above the commit equals
`1 − d`:

> **The optimal commit is the d-th percentile of hourly usage.** At a 28% discount,
> commit to your 28th-percentile hour.

That is dramatically lower than the folklore target, and the gap is money — stranded in
unused commitment on one side, left as undiscounted on-demand on the other. Track the
**effective discount** (headline discount less unused-commitment waste), because that is
the number that reaches the bill. `scripts/commitment_planner.py` computes both, and
refuses to recommend a commit for a workload too spiky or too clearly shrinking to
commit against.

## 8. Portfolio thinking — plan a quarter, not a ranking

A ranked list of optimizations is not a plan. A plan fits an engineering budget, does
not bank the same dollar twice, respects that Spot and a Savings Plan on the same
capacity are mutually exclusive, and keeps aggregate regression risk inside the error
budget — where "aggregate" means `1 − Π(1 − riskᵢ)`, so ten 2%-risk changes are an 18%
chance of an incident, not 2%.

Savings are also a *distribution*, not a number: confidence is a probability, so
simulate the plan and commit externally to the p10 while planning internally against
the p50. `scripts/cost_impact_matrix.py --budget --simulate` does all of this.

## 9. Refusal as a feature

The most valuable output of a right-sizing tool is often "not enough evidence". A CPU
pinned at 100% is a censored measurement, not an efficient one. A metric gap read as
zero utilisation halves an apparent p99. A workload growing all quarter will breach its
target inside the payback window. Tooling that always emits a number will eventually
emit a confident, wrong one — and cost-optimization programmes are killed by a single
self-inflicted incident far more often than they are killed by an under-optimised bill.
See [industrial edge cases](industrial-edge-cases.md) for the full catalogue.

## 10. Unit economics — cost per business metric

The most novel shift is *what you measure*. Track **$ per order / per active user /
per 1k requests**, not just total bill. A rising total bill with falling unit cost is
healthy growth; a flat bill with rising unit cost is rot. Unit economics tell you which
is which and make cost a product metric the whole org can reason about.

→ Next: [Decision framework](decision-framework.md)
