# Industrial Edge Cases

The demo of a cost tool always works. What breaks it is a real estate: metric
gaps, censored counters, migrations in flight, spreadsheets with `$8,000` in a
numeric column, and workloads whose average is a lie.

This is the catalogue. Each entry is a failure mode, what it costs, the rule the
tooling applies, and the test that holds the rule in place. Use it as a review
checklist even if you never run the scripts.

---

## A. Measurement traps

### A1. The censored metric (100% CPU is not "efficient")

A fleet pinned at 100% CPU looks perfectly utilised. It is a measurement clipped
at the ceiling: true demand is unknown and *higher*, and the queue is already
forming behind it. Utilisation-based tooling that treats the ceiling as the
signal will happily leave a throttled fleet in place — or size the next one from
a number that was never real.

**Rule:** if more than 5% of samples sit at or above 95%, refuse to downsize,
recommend an upsize, and say the metric is censored.
*Test:* `test_saturated_metric_is_never_downsized`, `test_partial_saturation_still_blocks_a_downsize`.

### A2. A gap is not a zero

An agent outage, a scrape failure, a `null` in the export. Read as `0`, a
week-long gap halves the apparent p99 and produces a confident recommendation to
cut a fleet in half.

**Rule:** `null`/`NaN` samples are dropped, never zero-filled; the missing share
is reported; above 20% missing, confidence drops and the output says why. For
usage series, a `NaN` hour is a hard error — a commitment sized on phantom
zero-usage hours is a five-figure mistake.
*Test:* `test_gaps_are_not_zero_utilisation`, `test_nan_is_not_a_zero_hour`.

### A3. Too little history

Two hours of samples have never seen the nightly batch. Six days have never seen
the Monday peak. Four weeks have never seen the quarter-end close.

**Rule:** below one full day of samples the advisor returns
`insufficient-data` rather than a number; below a week it says the weekly peak
may be unobserved and lowers confidence. The commitment planner warns when the
history is short relative to the term — a 3-year commitment sized on 7 days of
data is a bet, not an analysis.
*Test:* `test_short_series_refuses_to_advise`, `test_short_history_warns_about_seasonality`.

### A4. Units

CPU as a fraction vs a percentage. Cents vs dollars. Annual vs monthly savings.
Multi-core counters normalised differently by different agents. Every one of
these produces a number that is plausible and wrong by 100×.

**Rule:** utilisation above 100 is rejected with "normalise multi-core counters
first"; a confidence of `90` is rejected with "percentages need /100"; savings
past $10¹² are rejected as a unit error.
*Test:* `test_utilisation_above_100_rejected`, `test_percentage_confidence_above_one_rejected_with_hint`, `test_unit_error_in_savings_rejected`.

---

## B. Distribution traps

### B1. The mean of a bursty workload is meaningless

A p99/p50 ratio of 10 says the workload is spiky. Sizing to the mean guarantees
the spikes clip; sizing to the p99 with normal headroom may still be tight.

**Rule:** above a 4× ratio, tighten the headroom target by 25%, flag it, and
note that a burstable family may fit better than a smaller fixed one.
*Test:* `test_bursty_workload_gets_extra_headroom`.

### B2. The single spike that poisons a commitment

One hour at 1000 units in a series of 200 otherwise-idle hours. Cover the spike
and you pay for 199 hours of unused commitment.

**Rule:** the optimum falls out of the maths — the commit goes to zero and the
tool says the workload is too spiky to commit against at this discount.
*Test:* `test_single_spike_is_not_worth_committing_to`.

### B3. Diurnal and weekly shape looks like a trend

A series ending on a weekend is 40% below its weekday mean. A naive
"last quarter vs the rest" trend check fires on every workload that has a
weekend.

**Rule:** trend checks compare whole weeks offset by exactly one week, so the
day-of-week composition matches on both sides. Below two weeks of history, no
trend claim is made at all.
*Test:* `test_long_history_of_a_healthy_workload_is_quiet`, `test_scale_down_in_flight_is_flagged`.

---

## C. Time traps

### C1. The migration in flight

The fleet you are sizing a 3-year commitment against is being migrated to
serverless next quarter. Historical usage says commit; the roadmap says do not.

**Rule:** when the most recent week is more than 10% below the week before,
warn that a scale-down will strand the commit and point at
`--trailing-window`, which sizes on the current shape rather than the historical
one.
*Test:* `test_scale_down_in_flight_is_flagged`, `test_trailing_window_sizes_on_the_current_shape`.

### C2. Growth eats the saving before it lands

A workload whose p99 has climbed all quarter will breach the target inside the
payback window. You will pay the engineering cost twice: once to shrink it, once
to grow it back.

**Rule:** project the p99 forward over `--horizon-days` using the observed
slope; if the projection breaches the target, hold and say so.
*Test:* `test_growing_workload_is_not_downsized`.

### C3. Stale peaks keep a shrunk workload over-provisioned

The mirror image of C2: a migration *completed* mid-window, so the history still
contains peaks from a workload that no longer exists. Sizing on the full-window
p99 pays for that ghost forever.

**Rule:** when the trend is down and the trailing quarter's p99 is below 80% of
the full-window p99, size on the trailing window (if it is long enough to be
credible), state that it happened, and lower confidence.
*Test:* `test_shrunk_workload_is_sized_on_its_recent_window`.

---

## D. Portfolio traps

### D1. The same dollar, counted twice

Right-sizing the fleet: $3,500/mo. Graviton on the same fleet: $4,000/mo. The
plan promises $7,500. Graviton on an already-right-sized fleet returns closer to
$5,500 — the second optimization is harvesting spend the first one already took.

**Rule:** overlap `group`s. Richest member at full credit, each subsequent
member at the decay factor. The report prints how much of the raw total was
double-counted.
*Test:* `test_same_group_is_not_double_counted`, `test_overlap_changes_which_plan_wins`.

### D2. Small risks compound

Ten independent changes at "2% risk each" are not 2%. They are `1 − 0.98¹⁰` =
18%: roughly a one-in-five chance the quarter contains an incident traceable to
the cost programme. That is the number that determines whether the programme
keeps its licence to operate.

**Rule:** aggregate risk is `1 − Π(1 − riskᵢ)`, reported on every plan and
cappable with `--risk-budget`.
*Test:* `test_small_risks_compound`, `test_risk_budget_caps_the_plan`.

### D3. Greedy ranking is not a plan

With a 10-day budget, greedy takes the best-scoring 6-day candidate and then
cannot afford either 5-day candidate — banking $620 where $1,000 was available.

**Rule:** exact branch-and-bound up to 22 candidates; greedy beyond that, and
the output labels which one ran.
*Test:* `test_greedy_ordering_is_beaten_by_exact_search`.

### D4. Prerequisites and mutual exclusion

Showback needs tag coverage; the effort of the prerequisite belongs to the
dependent candidate. Spot and a Savings Plan on the same capacity cannot both
be banked. Neither survives a flat ranked list.

**Rule:** `requires` closes transitively and carries its effort into the budget;
`excludes` is enforced symmetrically; requirement cycles are rejected at parse
time with the cycle printed.
*Test:* `test_prerequisite_is_pulled_in_with_its_effort`, `test_requirement_cycle_detected`, `test_risky_prerequisite_blocks_a_safe_candidate`.

### D5. Floating-point budgets

`0.1 + 0.2 > 0.3` in binary. A planner that compares effort to budget exactly
will drop a candidate that fits, and nobody will ever work out why.

**Rule:** every budget comparison carries an epsilon.
*Test:* `test_floating_point_budget_boundary`.

### D6. The marginal overshoot that doubles the bill

p99 lands at 60.5% against a 60% target. A naive ladder lookup jumps 8 → 16
units and doubles the spend to recover half a percent of headroom.

**Rule:** a 10% tolerance band above target holds instead of upsizing, and says
the next size up is not worth the spend.
*Test:* `test_marginal_overshoot_holds_instead_of_doubling_the_box`.

---

## E. Input traps

Cost data comes from spreadsheets and hand-edited JSON. All of these are
rejected loudly, with the row and field named, rather than silently coerced:

| Input | Why it must not be accepted quietly |
|-------|-------------------------------------|
| `null` in a numeric cell | A blank cell scored as "$0 saved" changes the ranking |
| `true` where a number belongs | JSON booleans coerce to `1.0` and look like data |
| `NaN` / `Infinity` | Propagates through every downstream total |
| Duplicate candidate names | Names are identities for `requires`/`excludes` |
| Dangling `requires` reference | Silently ignoring it ships a plan missing a prerequisite |
| Requirement cycle | Infinite recursion, or a plan that can never start |
| Unknown field (`saving` vs `savings`) | Typo means the real field is missing and defaulted |
| `"$8,000"`, `"95%"` | *Accepted* — spreadsheets emit these, and rejecting them just moves the error into a manual retype |
| Negative savings | *Accepted* — a cache bought for latency is a real candidate; it ranks last and never pays back |
| Zero effort | *Accepted*, floored at 0.1 days — nothing is free; there is always a review and a rollback plan |

*Tests:* `tests/test_model.py`, and `tests/test_cli.py` for the exit codes a CI
gate depends on.

---

## Using this list without the tools

Before you accept any right-sizing or commitment recommendation — from these
scripts, from a vendor, or from a spreadsheet:

1. Is the metric censored? (Check the max, not the mean.)
2. How much of the window is missing, and was it filled with zeros?
3. Does the window cover a full weekly *and* monthly cycle?
4. Is the workload growing, shrinking, or being migrated during the term?
5. Does this saving overlap another one already booked?
6. What is the aggregate regression risk across everything shipping this quarter?
7. If the number is wrong, which direction is it wrong in — and is that the
   direction that costs money, or the one that costs an incident?
