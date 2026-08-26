"""Constrained portfolio selection.

Ranking by score answers "what is best?". It does not answer the question a team
actually faces: *"we have 15 engineer-days this quarter and one error budget --
which set do we ship?"* That is a knapsack with three complications real estates
always have:

1. **Overlap.** Right-sizing a fleet and moving it to Graviton both harvest the
   same underlying spend. Adding the two savings figures double-counts. Members
   of an overlap ``group`` are discounted geometrically after the first.
2. **Exclusions.** You cannot run the same fleet on Spot *and* on a 3-year RI.
3. **Prerequisites.** Tagging coverage before showback; a VPC endpoint before
   the NAT teardown. Selecting a candidate pulls its prerequisites in, with
   their effort.

Risk composes as independent failures: aggregate risk = ``1 - prod(1 - risk_i)``.
Ten "2% risk" changes are a 18% chance something regresses, not 2%.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .model import Candidate

#: Effort/budget comparisons in float days. 0.1 + 0.2 > 0.3 in binary, and a
#: plan that drops a candidate because of that is a bug, not a constraint.
EPS = 1e-9

#: Each additional candidate in an overlap group keeps this share of its savings.
DEFAULT_OVERLAP_DECAY = 0.5

#: Above this candidate count, exhaustive search is abandoned for a greedy pass.
DEFAULT_EXACT_LIMIT = 22


class PortfolioError(ValueError):
    """Raised when the constraints themselves are contradictory or malformed."""


@dataclass
class Plan:
    chosen: List[Candidate]
    effort: float
    gross_savings: float
    net_savings: float
    aggregate_risk: float
    exact: bool
    excluded: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def overlap_loss(self) -> float:
        return self.gross_savings - self.net_savings

    @property
    def names(self) -> List[str]:
        return [c.name for c in self.chosen]


def aggregate_risk(candidates: Iterable[Candidate]) -> float:
    """P(at least one regression), treating candidates as independent."""
    survival = 1.0
    for cand in candidates:
        survival *= 1.0 - cand.risk
    return 1.0 - survival


def net_savings(
    candidates: Sequence[Candidate], overlap_decay: float = DEFAULT_OVERLAP_DECAY
) -> float:
    """Confidence-weighted savings with overlap groups de-duplicated.

    Within a group the richest candidate is credited in full, the next at
    ``decay``, the next at ``decay**2``, and so on. Ungrouped candidates are
    always credited in full.
    """
    if not 0.0 <= overlap_decay <= 1.0:
        raise PortfolioError(f"overlap_decay must be in [0, 1], got {overlap_decay!r}")
    grouped: Dict[str, List[Candidate]] = {}
    total = 0.0
    for cand in candidates:
        if cand.group:
            grouped.setdefault(cand.group, []).append(cand)
        else:
            total += cand.expected_savings
    for members in grouped.values():
        members = sorted(members, key=lambda c: (-c.expected_savings, c.name))
        for rank, cand in enumerate(members):
            total += cand.expected_savings * (overlap_decay ** rank)
    return total


def requirement_closure(candidates: Sequence[Candidate]) -> Dict[str, Set[str]]:
    """Map each candidate to itself plus every transitive prerequisite."""
    by_name = {c.name: c for c in candidates}
    memo: Dict[str, Set[str]] = {}

    def resolve(name: str, seen: Set[str]) -> Set[str]:
        if name in memo:
            return memo[name]
        if name in seen:  # parse_candidates rejects cycles; belt and braces
            raise PortfolioError(f"requirement cycle through {name!r}")
        out = {name}
        for dep in by_name[name].requires:
            out |= resolve(dep, seen | {name})
        memo[name] = out
        return out

    return {name: resolve(name, set()) for name in by_name}


def _conflicts(names: Set[str], by_name: Dict[str, Candidate]) -> Optional[Tuple[str, str]]:
    for name in names:
        for other in by_name[name].excludes:
            if other in names:
                return (name, other)
    return None


def optimize(
    candidates: Sequence[Candidate],
    effort_budget: Optional[float] = None,
    *,
    risk_budget: Optional[float] = None,
    max_risk: Optional[float] = None,
    overlap_decay: float = DEFAULT_OVERLAP_DECAY,
    exact_limit: int = DEFAULT_EXACT_LIMIT,
) -> Plan:
    """Pick the highest-net-savings feasible subset.

    ``effort_budget``  engineer-days available (None = unlimited)
    ``risk_budget``    cap on P(at least one regression) across the plan
    ``max_risk``       drop any single candidate riskier than this
    """
    if effort_budget is not None and effort_budget < 0:
        raise PortfolioError(f"effort_budget cannot be negative, got {effort_budget}")
    if risk_budget is not None and not 0.0 <= risk_budget <= 1.0:
        raise PortfolioError(f"risk_budget must be in [0, 1], got {risk_budget}")
    if max_risk is not None and not 0.0 <= max_risk <= 1.0:
        raise PortfolioError(f"max_risk must be in [0, 1], got {max_risk}")
    if not 0.0 <= overlap_decay <= 1.0:
        raise PortfolioError(f"overlap_decay must be in [0, 1], got {overlap_decay}")

    by_name = {c.name: c for c in candidates}
    if len(by_name) != len(candidates):
        raise PortfolioError("candidate names must be unique")
    closure = requirement_closure(candidates)
    excluded: List[Tuple[str, str]] = []

    pool: List[Candidate] = []
    for cand in candidates:
        if max_risk is not None and cand.risk > max_risk + EPS:
            excluded.append((cand.name, f"risk {cand.risk:.2f} exceeds max-risk"))
            continue
        # A candidate whose prerequisites are themselves too risky is unbuildable.
        blocked = [
            dep
            for dep in closure[cand.name]
            if max_risk is not None and by_name[dep].risk > max_risk + EPS
        ]
        if blocked:
            excluded.append(
                (cand.name, f"prerequisite {sorted(blocked)[0]!r} exceeds max-risk")
            )
            continue
        pool.append(cand)

    budget = math.inf if effort_budget is None else effort_budget

    def cost_of(names: Set[str]) -> float:
        return sum(by_name[n].effective_effort for n in names)

    def feasible(names: Set[str]) -> bool:
        if cost_of(names) > budget + EPS:
            return False
        if _conflicts(names, by_name) is not None:
            return False
        if risk_budget is not None:
            if aggregate_risk(by_name[n] for n in names) > risk_budget + EPS:
                return False
        return True

    def value_of(names: Set[str]) -> float:
        return net_savings([by_name[n] for n in names], overlap_decay)

    order = sorted(pool, key=lambda c: (-c.score(), c.name))
    exact = len(pool) <= exact_limit

    if exact:
        best_names: Set[str] = set()
        best_value = 0.0

        def bound(index: int, names: Set[str]) -> float:
            """Optimistic completion: fractional knapsack on undiscounted savings."""
            remaining = budget - cost_of(names)
            total = value_of(names)
            for cand in order[index:]:
                if cand.name in names or cand.expected_savings <= 0:
                    continue
                effort = cand.effective_effort
                if effort <= remaining:
                    total += cand.expected_savings
                    remaining -= effort
                elif remaining > 0:
                    total += cand.expected_savings * (remaining / effort)
                    remaining = 0.0
                    break
                else:
                    break
            return total

        def search(index: int, names: Set[str]) -> None:
            nonlocal best_names, best_value
            value = value_of(names)
            if value > best_value + EPS:
                best_value, best_names = value, set(names)
            if index >= len(order):
                return
            if bound(index, names) <= best_value + EPS:
                return
            cand = order[index]
            if cand.name in names:  # already pulled in as someone's prerequisite
                search(index + 1, names)
                return
            candidate_set = names | closure[cand.name]
            if feasible(candidate_set):
                search(index + 1, candidate_set)
            search(index + 1, names)  # skip branch

        search(0, set())
        chosen_names = best_names
    else:
        chosen_names = set()
        for cand in order:
            if cand.name in chosen_names or cand.expected_savings <= 0:
                continue
            trial = chosen_names | closure[cand.name]
            if feasible(trial) and value_of(trial) > value_of(chosen_names) + EPS:
                chosen_names = trial

    chosen = sorted(
        (by_name[n] for n in chosen_names), key=lambda c: (-c.score(), c.name)
    )
    already = {name for name, _ in excluded}
    for cand in candidates:
        if cand.name in chosen_names or cand.name in already:
            continue
        excluded.append((cand.name, _rejection_reason(cand, chosen_names, by_name,
                                                      closure, budget, risk_budget)))

    return Plan(
        chosen=chosen,
        effort=sum(c.effective_effort for c in chosen),
        gross_savings=sum(c.expected_savings for c in chosen),
        net_savings=net_savings(chosen, overlap_decay),
        aggregate_risk=aggregate_risk(chosen),
        exact=exact,
        excluded=excluded,
    )


def _rejection_reason(
    cand: Candidate,
    chosen: Set[str],
    by_name: Dict[str, Candidate],
    closure: Dict[str, Set[str]],
    budget: float,
    risk_budget: Optional[float],
) -> str:
    """Why a candidate is not in the plan -- reviewers always ask."""
    if cand.expected_savings <= 0:
        return "no expected saving"
    conflict = _conflicts(chosen | closure[cand.name], by_name)
    if conflict is not None and cand.name in conflict:
        other = conflict[1] if conflict[0] == cand.name else conflict[0]
        return f"excluded by {other!r}"
    added = closure[cand.name] - chosen
    extra_effort = sum(by_name[n].effective_effort for n in added)
    used = sum(by_name[n].effective_effort for n in chosen)
    if used + extra_effort > budget + EPS:
        with_deps = " (with prerequisites)" if len(added) > 1 else ""
        return f"needs {extra_effort:g}d{with_deps}, over the effort budget"
    if risk_budget is not None:
        combined = aggregate_risk([by_name[n] for n in chosen | closure[cand.name]])
        if combined > risk_budget + EPS:
            return f"would push aggregate risk to {combined:.0%}"
    return "lower value than the selected set"


def simulate(
    candidates: Sequence[Candidate],
    trials: int = 10000,
    seed: int = 0,
    overlap_decay: float = DEFAULT_OVERLAP_DECAY,
) -> Dict[str, float]:
    """Monte-Carlo the realised savings of a plan.

    Confidence is a probability, so a plan's savings are a distribution, not a
    number. Reporting p10 alongside the mean is the difference between "we will
    save $18k/mo" and "we will save at least $11k/mo nine times out of ten" --
    only the second survives contact with a finance review.

    Seeded, so the same plan always reports the same interval.
    """
    if trials <= 0:
        raise PortfolioError(f"trials must be positive, got {trials}")
    if not candidates:
        return {"p10": 0.0, "p50": 0.0, "p90": 0.0, "mean": 0.0}
    rng = random.Random(seed)
    outcomes: List[float] = []
    for _ in range(trials):
        realised = [c for c in candidates if rng.random() < c.confidence]
        # Savings are already realised here, so credit them at full weight and
        # let the overlap groups do the de-duplication.
        outcomes.append(
            net_savings(
                [
                    Candidate(
                        name=c.name,
                        savings=c.savings,
                        confidence=1.0,
                        effort=c.effort,
                        risk=c.risk,
                        group=c.group,
                    )
                    for c in realised
                ],
                overlap_decay,
            )
        )
    outcomes.sort()

    def pct(p: float) -> float:
        if len(outcomes) == 1:
            return outcomes[0]
        pos = p * (len(outcomes) - 1)
        low = int(math.floor(pos))
        high = min(low + 1, len(outcomes) - 1)
        return outcomes[low] + (outcomes[high] - outcomes[low]) * (pos - low)

    return {
        "p10": pct(0.10),
        "p50": pct(0.50),
        "p90": pct(0.90),
        "mean": sum(outcomes) / len(outcomes),
    }
