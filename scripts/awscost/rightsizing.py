"""SLO-headroom right-sizing from utilisation samples.

The naive rule -- "average CPU is 12%, so shrink it 8x" -- is how right-sizing
earns its reputation for causing incidents. This module encodes the failure
modes that make a downsize wrong even when the average says otherwise:

* **Censored metrics.** A CPU pinned at 100% is not "perfectly utilised", it is
  a measurement clipped at the ceiling. The true demand is unknown and larger.
  Never downsize on a saturated signal; upsize and re-measure.
* **Growth.** A workload whose p99 has climbed all quarter will breach the
  target inside the payback window. Sizing on the past is sizing on the wrong
  distribution.
* **Burstiness.** A p99/p50 ratio of 10 means the mean is meaningless. Bursty
  workloads need headroom (or a burstable family), not a smaller box.
* **Thin data.** A 2-hour window has never seen the weekly batch job.
* **Gaps.** A missing sample is not a zero-utilisation sample. Dropping gaps
  silently turns an agent outage into a downsize recommendation.

Samples are percentages (0..100), as CloudWatch reports them.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

#: Below this p99, the instance is doing nothing worth paying for.
IDLE_P99 = 3.0

#: At or above this, treat the metric as censored by the ceiling.
SATURATION_FLOOR = 95.0

#: Share of samples at the ceiling that makes censoring the likely explanation.
SATURATION_SHARE = 0.05

#: p99/p50 above this is a bursty workload, not a steady one.
BURST_RATIO = 4.0

#: One day of 5-minute samples. Less than this cannot see a daily cycle.
MIN_SAMPLES = 288

#: Share of missing samples above which the recommendation is not trustworthy.
MAX_MISSING_SHARE = 0.2

#: A p99 this far over target does not justify jumping to the next size up.
HOLD_TOLERANCE = 0.10

DEFAULT_TARGET_PEAK = 0.60
DEFAULT_LADDER = (1, 2, 4, 8, 16, 32, 48, 64, 96, 128, 192, 256, 384, 512)


class SizingError(ValueError):
    """Raised for malformed samples or parameters."""


@dataclass
class SizingAdvice:
    action: str  # downsize | upsize | hold | terminate | insufficient-data
    current_capacity: float
    recommended_capacity: float
    p50: float
    p99: float
    peak: float
    missing_share: float
    samples: int
    confidence: float
    reasons: List[str] = field(default_factory=list)

    @property
    def capacity_delta_pct(self) -> float:
        if self.current_capacity <= 0:
            return 0.0
        return (self.recommended_capacity / self.current_capacity - 1.0) * 100.0

    def monthly_saving(self, cost_per_capacity_unit: float) -> float:
        """Savings at a given $/month per capacity unit. Negative = an upsize."""
        return (self.current_capacity - self.recommended_capacity) * cost_per_capacity_unit


def percentile(values: Sequence[float], p: float) -> float:
    """Linear-interpolated percentile. ``p`` in 0..1."""
    if not values:
        raise SizingError("cannot take a percentile of an empty series")
    if not 0.0 <= p <= 1.0:
        raise SizingError(f"percentile must be in [0, 1], got {p}")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = p * (len(ordered) - 1)
    low = int(math.floor(pos))
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def _slope_per_sample(values: Sequence[float]) -> float:
    """Least-squares slope of utilisation against sample index."""
    n = len(values)
    if n < 2:
        return 0.0
    mean_x = (n - 1) / 2.0
    mean_y = sum(values) / n
    num = sum((i - mean_x) * (v - mean_y) for i, v in enumerate(values))
    den = sum((i - mean_x) ** 2 for i in range(n))
    return num / den if den else 0.0


def _round_to_ladder(required: float, ladder: Sequence[float]) -> float:
    for size in ladder:
        if size >= required - 1e-9:
            return float(size)
    return float(ladder[-1])


def advise(
    samples: Sequence[Optional[float]],
    current_capacity: float,
    *,
    target_peak_util: float = DEFAULT_TARGET_PEAK,
    ladder: Optional[Sequence[float]] = None,
    sample_interval_minutes: float = 5.0,
    min_samples: int = MIN_SAMPLES,
    horizon_days: float = 30.0,
    allow_short_series: bool = False,
) -> SizingAdvice:
    """Recommend a capacity for a workload from its utilisation history.

    ``samples``            utilisation percentages, oldest first; ``None`` marks
                           a gap (an agent outage, a scrape failure)
    ``current_capacity``   vCPU count, or any linear capacity unit
    ``target_peak_util``   utilisation the p99 should land at after resizing;
                           0.6 leaves 40% headroom for the spike you have not
                           seen yet
    ``horizon_days``       how far ahead the growth check projects
    """
    if isinstance(current_capacity, bool) or not isinstance(
        current_capacity, (int, float)
    ):
        raise SizingError("current_capacity must be a number")
    if not math.isfinite(current_capacity) or current_capacity <= 0:
        raise SizingError(f"current_capacity must be positive, got {current_capacity}")
    if not 0.0 < target_peak_util <= 1.0:
        raise SizingError(
            f"target_peak_util must be in (0, 1], got {target_peak_util}"
        )
    if sample_interval_minutes <= 0:
        raise SizingError("sample_interval_minutes must be positive")
    if min_samples < 1:
        raise SizingError("min_samples must be at least 1")

    sizes = tuple(ladder) if ladder is not None else DEFAULT_LADDER
    if not sizes:
        raise SizingError("ladder cannot be empty")
    if any(s <= 0 for s in sizes):
        raise SizingError("ladder sizes must be positive")
    sizes = tuple(sorted(sizes))

    clean: List[float] = []
    missing = 0
    for i, raw in enumerate(samples or []):
        if raw is None:
            missing += 1
            continue
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise SizingError(f"samples[{i}]: expected a number or null")
        value = float(raw)
        if math.isnan(value):
            missing += 1
            continue
        if not math.isfinite(value):
            raise SizingError(f"samples[{i}] must be finite, got {value}")
        if value < 0:
            raise SizingError(f"samples[{i}] cannot be negative, got {value:g}")
        if value > 100:
            raise SizingError(
                f"samples[{i}] is {value:g}: utilisation is a percentage of the "
                "instance, normalise multi-core counters before scoring"
            )
        clean.append(value)

    total = len(clean) + missing
    missing_share = (missing / total) if total else 1.0
    reasons: List[str] = []

    if not clean:
        return SizingAdvice(
            action="insufficient-data",
            current_capacity=float(current_capacity),
            recommended_capacity=float(current_capacity),
            p50=0.0, p99=0.0, peak=0.0,
            missing_share=missing_share,
            samples=0,
            confidence=0.0,
            reasons=["no usable samples: fix metric collection before resizing"],
        )

    p50 = percentile(clean, 0.50)
    p99 = percentile(clean, 0.99)
    peak = max(clean)
    span_hours = total * sample_interval_minutes / 60.0

    def build(action: str, recommended: float, confidence: float) -> SizingAdvice:
        return SizingAdvice(
            action=action,
            current_capacity=float(current_capacity),
            recommended_capacity=float(recommended),
            p50=p50, p99=p99, peak=peak,
            missing_share=missing_share,
            samples=len(clean),
            confidence=max(0.0, min(1.0, confidence)),
            reasons=reasons,
        )

    if len(clean) < min_samples and not allow_short_series:
        reasons.append(
            f"only {len(clean)} usable samples ({span_hours:.1f}h); need "
            f"{min_samples} to see a full daily cycle"
        )
        return build("insufficient-data", current_capacity, 0.0)

    confidence = 1.0
    if missing_share > MAX_MISSING_SHARE:
        reasons.append(
            f"{missing_share:.0%} of samples are missing: the gaps may hide the peak"
        )
        confidence -= 0.4
    elif missing > 0:
        confidence -= missing_share
    if span_hours < 7 * 24:
        reasons.append(
            f"history spans {span_hours / 24:.1f} days: a weekly peak may be unobserved"
        )
        confidence -= 0.2

    saturated_share = sum(1 for v in clean if v >= SATURATION_FLOOR) / len(clean)
    if saturated_share > SATURATION_SHARE:
        reasons.append(
            f"{saturated_share:.0%} of samples sit at or above {SATURATION_FLOOR:g}%: "
            "the metric is censored, true demand is unknown and higher"
        )
        target = _round_to_ladder(current_capacity * 1.5, sizes)
        if target <= current_capacity:
            reasons.append("already at the largest size on the ladder")
            return build("hold", current_capacity, confidence)
        return build("upsize", target, confidence)

    if p99 < IDLE_P99:
        reasons.append(
            f"p99 utilisation is {p99:.1f}%: idle. Terminating beats resizing - "
            "but confirm it is not a warm standby or a licence host first"
        )
        return build("terminate", sizes[0], confidence)

    burst_ratio = (p99 / p50) if p50 > 0 else float("inf")
    effective_target = target_peak_util
    if burst_ratio > BURST_RATIO:
        reasons.append(
            f"bursty: p99/p50 is {burst_ratio:.1f}x, so headroom is tightened "
            "and a burstable family may fit better than a smaller fixed one"
        )
        effective_target = target_peak_util * 0.75
        confidence -= 0.1

    slope = _slope_per_sample(clean)
    samples_per_day = 24 * 60 / sample_interval_minutes
    projected_p99 = p99 + slope * samples_per_day * horizon_days
    if projected_p99 > p99 * 1.05 and projected_p99 > effective_target * 100:
        reasons.append(
            f"p99 is trending up ({p99:.0f}% -> {projected_p99:.0f}% projected in "
            f"{horizon_days:.0f} days): sizing down now buys a re-size later"
        )
        return build("hold", current_capacity, confidence)

    sizing_p99 = p99
    trailing = clean[-max(len(clean) // 4, 1):]
    if (
        slope < 0
        and len(trailing) >= min_samples
        and percentile(trailing, 0.99) < p99 * 0.8
    ):
        # A workload that has genuinely shrunk (a migration completed, a caller
        # retired) is over-sized forever if we keep sizing on its stale peaks.
        sizing_p99 = percentile(trailing, 0.99)
        reasons.append(
            f"sized on the most recent {len(trailing)} samples (p99 {sizing_p99:.0f}%): "
            f"utilisation has fallen from a p99 of {p99:.0f}% across the full window"
        )
        confidence -= 0.1

    demand = current_capacity * (max(sizing_p99, projected_p99) / 100.0)
    required = demand / effective_target
    recommended = _round_to_ladder(required, sizes)

    if recommended > current_capacity and required <= current_capacity * (
        1.0 + HOLD_TOLERANCE
    ):
        # Do not double an instance because the p99 overshot the target by 1%.
        reasons.append(
            f"p99 of {p99:.0f}% needs {required:.2f} units, within {HOLD_TOLERANCE:.0%} "
            "of the current size; the next size up is not worth the spend"
        )
        return build("hold", current_capacity, confidence)

    if recommended < current_capacity:
        action = "downsize"
        reasons.append(
            f"p99 of {p99:.0f}% on {current_capacity:g} units needs {required:.2f} "
            f"units at a {effective_target:.0%} target; nearest size is {recommended:g}"
        )
    elif recommended > current_capacity:
        action = "upsize"
        reasons.append(
            f"p99 of {p99:.0f}% leaves less than the target headroom; "
            f"{recommended:g} units restores it"
        )
    else:
        action = "hold"
        reasons.append("already at the right size for the target headroom")
    return build(action, recommended, confidence)
