# Tooling

Three stdlib-only scripts, no dependencies, no AWS credentials. They take your
numbers and answer the three questions that actually decide a cost programme.

| Script | Question it answers |
|--------|--------------------|
| `scripts/cost_impact_matrix.py` | Which optimizations do we ship, in what order, with the days we have? |
| `scripts/commitment_planner.py` | How much do we commit to, and is this workload safe to commit against at all? |
| `scripts/rightsizing_advisor.py` | Is this fleet actually over-provisioned, or does it only look that way? |

Everything supports `--json` so it can gate a pipeline instead of a meeting.

---

## 1. Impact matrix — ranking, and then planning

Ranking by `savings × confidence ÷ (effort × (1 + risk))` gives an order. A team
does not execute an order; it executes a **quarter**. `--budget` turns the
ranking into a constrained portfolio.

```bash
python3 scripts/cost_impact_matrix.py candidates.json --budget 10 \
        --risk-budget 0.3 --simulate --explain
```

Four things the planner does that a sorted list cannot:

**Overlap groups — stop double-counting the same dollar.** Right-sizing a fleet
and moving it to Graviton both harvest the same spend. Summing them promises
money that does not exist and the variance shows up at the quarterly review. Tag
both with `"group": "compute-baseline"` and each additional member of the group
is credited at `--overlap-decay` (default 0.5) of its face value.

**Exclusions and prerequisites.** `"excludes": ["Savings Plans on baseline"]` on
a Spot candidate stops the plan from committing and interrupting the same
capacity. `"requires": ["Tag coverage to 95%"]` pulls the prerequisite into the
plan *with its effort*, or drops the dependent candidate if it will not fit.

**Risk that composes.** Aggregate risk is `1 − Π(1 − riskᵢ)`, not a sum. Ten
"2% risk" changes carry an 18% chance that something regresses. `--risk-budget`
caps that; `--max-risk` drops individual candidates that are too hot to touch
this quarter.

**Exact selection, not greedy.** Up to 22 candidates the planner is a
branch-and-bound search over feasible subsets, so it finds combinations greedy
ranking misses (three 5-day candidates beat one 6-day candidate in a 10-day
budget). Past that it falls back to greedy and says so in the output.

`--simulate` Monte-Carlos the plan: confidence is a probability, so savings are
a distribution. Commit to the p10 externally, plan against the p50 internally.
Seeded, so the interval is reproducible in CI.

## 2. Commitment planner — the percentile result

Cost per hour at commit level `C`, in normalised on-demand dollars:

```
cost(C) = C × (1 − d)  +  max(uₕ − C, 0)
```

This is convex and piecewise-linear, and its optimum falls exactly where the
fraction of hours above `C` equals `1 − d`:

> **The optimal commit is the d-th percentile of hourly usage.**
> At a 28% discount, commit to your 28th-percentile hour.

That is far below the "cover 70% of spend" folklore, and the difference is real
money either stranded in unused commitment or left on the table. The tool finds
the optimum from your own series by evaluating the breakpoints (no distribution
assumed) and prints the closed form alongside as a cross-check.

```bash
python3 scripts/commitment_planner.py usage.json --discount 0.28 --curve
python3 scripts/commitment_planner.py usage.json --trailing-window 720
```

It also reports **effective discount** — the headline discount less
unused-commitment waste — which is the number that ends up on the bill, and
warns when the history cannot support a commitment at all (see
[industrial edge cases](industrial-edge-cases.md)).

`--curve` shows the shape of the optimum: a broad plateau, not a knife edge.
Committing 10% *under* the optimum costs almost nothing and buys protection
against the workload shrinking — which is why under-committing is the safe
direction to be wrong in.

## 3. Right-sizing advisor — the refusals matter more than the recommendation

Sizing on the mean is how right-sizing earns its reputation for causing
incidents. This tool sizes on p99 against an explicit headroom target
(`--target-peak`, default 0.6 = 40% headroom) and **refuses to recommend a
downsize** when the evidence does not support one:

- CPU pinned at 100% is a censored measurement, not perfect efficiency → upsize.
- p99 trending up will breach the target inside the payback window → hold.
- Fewer than a day of samples has never seen the daily cycle → insufficient data.
- Gaps big enough to hide the peak → low confidence, stated in the output.

```bash
python3 scripts/rightsizing_advisor.py fleet.json --cost-per-unit 30
python3 scripts/rightsizing_advisor.py fleet.json --ladder 2,4,8,16 --json
```

Every recommendation carries a `confidence` and a list of `reasons`. A
recommendation you cannot explain to the service owner is a recommendation that
will not ship.

## Wiring into CI

All three exit `2` on bad input with a message naming the offending field, and
exit `0` with JSON on success — so a pipeline can fail on a malformed candidate
file rather than publishing a plausible ranking of garbage:

```bash
python3 scripts/cost_impact_matrix.py candidates.json --budget 10 --json \
  | python3 -c 'import json,sys; p=json.load(sys.stdin)["plan"]; \
                sys.exit(p["aggregate_risk"] > 0.3)'
```

## Tests

```bash
python3 -m unittest discover -s tests -v
```

The suite is mostly edge cases rather than happy paths — see
[industrial edge cases](industrial-edge-cases.md) for what each one is
defending against.
