# Decision Framework

How to prioritize the optimizations in this repo — and, just as important, when to stop.

## Prioritize by impact ÷ (effort × risk)

For each candidate, estimate:

- **Savings** — $/mo, from Cost Explorer, not vibes.
- **Confidence** — how sure are you the saving materializes (0–1)?
- **Effort** — engineer-days to implement and validate.
- **Performance risk** — probability × severity of an SLO regression (0–1).

Score = `savings × confidence ÷ (effort × (1 + risk))`. Attack highest score first.
`scripts/cost_impact_matrix.py` computes exactly this and sorts for you.

## The default order (usually correct)

1. **Commitment coverage** (Savings Plans / RIs) — biggest lever, zero code, days to apply.
2. **Waste elimination** — orphaned volumes, idle instances, unattached IPs, old
   snapshots, incomplete uploads. Pure flat-region cuts, no performance risk.
3. **Right-sizing + Graviton + gp3** — high savings, low risk, moderate effort.
4. **Tiering & scheduling** — S3 lifecycle, stop non-prod, autoscaling.
5. **Spot adoption** — high savings where the reliability tradeoff fits.
6. **Architectural** — caching layers, endpoint migrations, serverless refactors.
   Highest effort/risk; do last, only where the matrix justifies it.

## When to STOP

Over-optimization is its own waste. Stop when:

- The next candidate saves less than the **engineering time to implement it** costs.
  ($150/mo saving, 3 engineer-days at loaded cost → net loss for ~2 years.)
- You're entering the **steep region** — further cuts eat SLO headroom you need.
- The optimization adds **fragility** (bespoke, undocumented, one-person-understands-it)
  that raises operational risk above the dollars saved.
- Unit cost is already healthy and falling. Chasing the total bill during growth is
  optimizing the wrong number.

## The one-page checklist

```
[ ] Baseline measured (Cost Explorer + tags + CloudWatch)
[ ] SLOs defined per workload; headroom known
[ ] Savings Plans / RIs cover steady baseline
[ ] Waste swept: idle instances, orphaned EBS/EIPs, old snapshots, incomplete uploads
[ ] Right-sized against real CPU + memory
[ ] Graviton where the stack allows; gp2 → gp3
[ ] Storage tiered to access pattern (Intelligent-Tiering default)
[ ] Non-prod scheduled off out-of-hours
[ ] Spot for stateless/batch/CI
[ ] VPC endpoints for S3/DynamoDB; NAT usage reviewed
[ ] Cost checks in CI (Infracost / policy-as-code)
[ ] Budget + anomaly alerts wired to a human
[ ] Tracking $ per business unit, not just total
```

Work top to bottom; stop where impact ÷ effort drops below your bar.
