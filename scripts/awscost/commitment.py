"""Optimal Savings Plan / Reserved Instance commit level from a usage series.

Commitment coverage is the single biggest lever on most bills, and it is almost
always set by feel ("cover 70%, seems safe"). It has a closed form.

Model, per hour, in normalised on-demand dollars:

    cost(C) = C * (1 - d) + max(u_h - C, 0)

You pay for the commit ``C`` whether you use it or not (that is the whole risk),
and anything above it lands at on-demand rates. Total cost is convex and
piecewise-linear in ``C``; its subgradient is ``N*(1-d) - #{h : u_h > C}``, which
turns sign exactly where the fraction of hours above ``C`` equals ``1 - d``:

    **the optimal commit is the d-th percentile of hourly usage.**

A 28% discount says commit to your 28th-percentile hour -- far below the "cover
70% of spend" folklore, and the gap is money. :func:`plan` finds the optimum by
evaluating the breakpoints directly (no distributional assumptions), and
:func:`optimal_commit_quantile` gives the closed form for cross-checking.

Everything here is *backward*-looking. The warnings exist because a commitment
is forward-looking and one to three years long.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple

HOURS_PER_WEEK = 168


class CommitmentError(ValueError):
    """Raised for a malformed usage series or discount."""


@dataclass
class CommitmentPlan:
    commit: float
    discount: float
    hours: int
    baseline_cost: float
    optimized_cost: float
    savings: float
    savings_pct: float
    coverage_pct: float
    waste: float
    unused_hours: int
    warnings: List[str] = field(default_factory=list)

    @property
    def effective_discount(self) -> float:
        """Realised discount after unused-commitment waste."""
        if self.baseline_cost <= 0:
            return 0.0
        return self.savings / self.baseline_cost


def _clean_usage(usage: Sequence[float]) -> List[float]:
    if usage is None:
        raise CommitmentError("usage series is required")
    cleaned: List[float] = []
    for i, value in enumerate(usage):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CommitmentError(
                f"usage[{i}]: expected a number, got {type(value).__name__}"
            )
        val = float(value)
        if math.isnan(val):
            raise CommitmentError(
                f"usage[{i}] is NaN - a missing hour is not a zero-usage hour; "
                "drop the gap or backfill it explicitly"
            )
        if not math.isfinite(val):
            raise CommitmentError(f"usage[{i}] must be finite, got {val}")
        if val < 0:
            raise CommitmentError(f"usage[{i}] cannot be negative, got {val:g}")
        cleaned.append(val)
    if not cleaned:
        raise CommitmentError("usage series is empty")
    return cleaned


def cost_at(usage: Sequence[float], commit: float, discount: float) -> float:
    """Total cost of the series at a given commit level."""
    hours = len(usage)
    return commit * (1.0 - discount) * hours + sum(
        max(u - commit, 0.0) for u in usage
    )


def optimal_commit_quantile(usage: Sequence[float], discount: float) -> float:
    """Closed form: the ``discount``-th percentile hour of the series."""
    series = sorted(_clean_usage(usage))
    _check_discount(discount)
    if discount <= 0:
        return 0.0
    index = min(int(math.ceil(discount * len(series))) - 1, len(series) - 1)
    return series[max(index, 0)]


def _check_discount(discount: float) -> None:
    if isinstance(discount, bool) or not isinstance(discount, (int, float)):
        raise CommitmentError("discount must be a number between 0 and 1")
    if not math.isfinite(discount):
        raise CommitmentError("discount must be finite")
    if not 0.0 <= discount < 1.0:
        raise CommitmentError(
            f"discount must be in [0, 1), got {discount:g}"
            + (" (percentages need /100)" if discount >= 1 else "")
        )


def plan(
    usage: Sequence[float],
    discount: float,
    *,
    trailing_window: Optional[int] = None,
    term_months: int = 12,
) -> CommitmentPlan:
    """Recommend a commit level for an hourly usage series.

    ``usage``            hourly on-demand-equivalent spend, oldest first
    ``discount``         commitment discount, e.g. 0.28 for a 1-year compute SP
    ``trailing_window``  size the commit on only the most recent N hours, which
                         is the honest choice when the estimate has just shrunk
    """
    series = _clean_usage(usage)
    _check_discount(discount)
    if term_months <= 0:
        raise CommitmentError(f"term_months must be positive, got {term_months}")

    warnings: List[str] = []
    sizing = series
    if trailing_window is not None:
        if trailing_window <= 0:
            raise CommitmentError("trailing_window must be positive")
        if trailing_window < len(series):
            sizing = series[-trailing_window:]
        else:
            warnings.append(
                f"trailing_window of {trailing_window}h exceeds the {len(series)}h "
                "series; using the whole series"
            )

    # Evaluate the exact optimum at the breakpoints. Ties resolve to the smallest
    # commit: identical savings, less lock-in.
    breakpoints = sorted({0.0} | set(sizing))
    best_commit = 0.0
    best_cost = cost_at(sizing, 0.0, discount)
    for point in breakpoints:
        cost = cost_at(sizing, point, discount)
        if cost < best_cost - 1e-9:
            best_cost, best_commit = cost, point

    baseline = sum(series)
    optimized = cost_at(series, best_commit, discount)
    savings = baseline - optimized
    covered = sum(min(u, best_commit) for u in series)
    unused_hours = sum(1 for u in series if u < best_commit)
    waste = sum(max(best_commit - u, 0.0) for u in series) * (1.0 - discount)

    warnings.extend(_series_warnings(series, best_commit, discount, term_months))

    return CommitmentPlan(
        commit=best_commit,
        discount=discount,
        hours=len(series),
        baseline_cost=baseline,
        optimized_cost=optimized,
        savings=savings,
        savings_pct=(savings / baseline * 100.0) if baseline > 0 else 0.0,
        coverage_pct=(covered / baseline * 100.0) if baseline > 0 else 0.0,
        waste=waste,
        unused_hours=unused_hours,
        warnings=warnings,
    )


def _series_warnings(
    series: Sequence[float], commit: float, discount: float, term_months: int
) -> List[str]:
    warnings: List[str] = []
    hours = len(series)
    term_hours = term_months * 730

    if hours < HOURS_PER_WEEK:
        warnings.append(
            f"only {hours}h of history: weekly seasonality is unobserved, so the "
            "recommended commit may be sized on an unrepresentative window"
        )
    elif hours < 4 * HOURS_PER_WEEK:
        warnings.append(
            f"{hours}h of history (<1 month): monthly batch peaks may be unobserved"
        )
    if hours < term_hours / 12:
        warnings.append(
            f"history covers {hours}h but the commitment runs {term_months} months; "
            "a commit is a forward-looking bet on a backward-looking series"
        )

    if hours >= 2 * HOURS_PER_WEEK:
        # Compare whole weeks, offset by exactly one week, so a weekend or a
        # nightly batch cycle cannot masquerade as a trend.
        tail = series[-HOURS_PER_WEEK:]
        head = series[-2 * HOURS_PER_WEEK:-HOURS_PER_WEEK]
        head_mean = sum(head) / len(head)
        tail_mean = sum(tail) / len(tail)
        if head_mean > 0 and tail_mean < head_mean * 0.9:
            drop = (1.0 - tail_mean / head_mean) * 100.0
            warnings.append(
                f"the most recent week is {drop:.0f}% below the week before it - a "
                "migration or scale-down in flight will strand this commit; re-run "
                "with --trailing-window"
            )
        if head_mean > 0 and tail_mean > head_mean * 1.25:
            warnings.append(
                "usage is trending up; the commit is sized on history and will "
                "under-cover - re-evaluate after the growth settles"
            )

    if commit <= 0 and sum(series) > 0:
        warnings.append(
            "optimal commit is zero: this workload is too spiky to commit against "
            f"at a {discount:.0%} discount - the unused hours cost more than the "
            "discount returns"
        )
    if commit > 0 and commit >= max(series) - 1e-12:
        warnings.append(
            "commit sits at the series maximum: usage is effectively flat, so "
            "verify there is no scheduled decommission inside the term"
        )
    return warnings


def coverage_curve(
    usage: Sequence[float], discount: float, levels: Optional[Iterable[float]] = None
) -> List[Tuple[float, float, float]]:
    """``(commit, savings, waste)`` at a spread of commit levels, for plotting.

    Shows the shape of the optimum: it is a broad plateau, not a knife edge, so
    committing slightly *under* the optimum costs almost nothing and protects
    against the workload shrinking.
    """
    series = _clean_usage(usage)
    _check_discount(discount)
    peak = max(series)
    if levels is None:
        levels = [i / 10.0 * peak for i in range(11)]
    baseline = sum(series)
    out = []
    for commit in levels:
        if commit < 0:
            raise CommitmentError(f"commit level cannot be negative, got {commit:g}")
        cost = cost_at(series, commit, discount)
        waste = sum(max(commit - u, 0.0) for u in series) * (1.0 - discount)
        out.append((commit, baseline - cost, waste))
    return out
