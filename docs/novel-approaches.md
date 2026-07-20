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

## 7. Unit economics — cost per business metric

The most novel shift is *what you measure*. Track **$ per order / per active user /
per 1k requests**, not just total bill. A rising total bill with falling unit cost is
healthy growth; a flat bill with rising unit cost is rot. Unit economics tell you which
is which and make cost a product metric the whole org can reason about.

→ Next: [Decision framework](decision-framework.md)
