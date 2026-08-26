"""Candidate model and strict input validation.

Real cost data arrives from spreadsheets, Cost Explorer exports and hand-edited
JSON. It contains ``"$8,000"`` strings, ``null`` cells, duplicated rows, typos in
key names, and occasionally ``NaN``. Silently coercing that into a ranking is how
a team ends up committing six figures against a number nobody checked, so every
malformed row is rejected with a message that names the row and the field.
"""
from __future__ import annotations

import difflib
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

REQUIRED_FIELDS = ("name", "savings", "confidence", "effort", "risk")
OPTIONAL_FIELDS = ("group", "requires", "excludes", "notes")
KNOWN_FIELDS = REQUIRED_FIELDS + OPTIONAL_FIELDS

#: Effort floor, in engineer-days. Nothing is truly free: a "zero effort" change
#: still costs a review, a deploy and a rollback plan.
MIN_EFFORT = 0.1

#: Loaded cost of an engineer-day, used for payback. Override per organisation.
ENGINEER_DAY_COST = 800.0

#: Anything past this is a unit error (cents vs dollars, annual vs monthly),
#: not a real monthly saving.
MAX_PLAUSIBLE_SAVINGS = 1e12


class CandidateError(ValueError):
    """Raised for a malformed candidate. The message names row and field."""


def _where(index: Optional[int], name: Optional[str]) -> str:
    if name:
        return f"candidate {name!r}"
    if index is not None:
        return f"row {index}"
    return "candidate"


def _coerce_number(value: Any, field_name: str, where: str) -> float:
    """Accept numbers and the number-shaped strings spreadsheets emit."""
    if isinstance(value, bool):
        # JSON ``true`` would otherwise become 1.0 and look like a real figure.
        raise CandidateError(f"{where}: {field_name} must be a number, got a boolean")
    if isinstance(value, (int, float)):
        num = float(value)
    elif isinstance(value, str):
        cleaned = value.strip().replace(",", "").replace("$", "").replace("_", "")
        if cleaned.endswith("%"):
            try:
                return float(cleaned[:-1]) / 100.0
            except ValueError:
                raise CandidateError(
                    f"{where}: {field_name} is not a number: {value!r}"
                ) from None
        if not cleaned:
            raise CandidateError(f"{where}: {field_name} is empty")
        try:
            num = float(cleaned)
        except ValueError:
            raise CandidateError(
                f"{where}: {field_name} is not a number: {value!r}"
            ) from None
    elif value is None:
        raise CandidateError(f"{where}: {field_name} is null (missing measurement)")
    else:
        raise CandidateError(
            f"{where}: {field_name} must be a number, got {type(value).__name__}"
        )
    if not math.isfinite(num):
        raise CandidateError(f"{where}: {field_name} must be finite, got {num}")
    return num


def _coerce_unit(value: Any, field_name: str, where: str) -> float:
    num = _coerce_number(value, field_name, where)
    if not 0.0 <= num <= 1.0:
        raise CandidateError(
            f"{where}: {field_name} must be between 0 and 1, got {num:g}"
            + (" (percentages need /100)" if num > 1 else "")
        )
    return num


def _coerce_names(value: Any, field_name: str, where: str) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise CandidateError(f"{where}: {field_name} must be a list of names")
    out: List[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise CandidateError(f"{where}: {field_name} contains a non-name entry")
        out.append(item.strip())
    return tuple(dict.fromkeys(out))  # de-duplicate, keep order


@dataclass(frozen=True)
class Candidate:
    """One cost-optimization candidate.

    savings     estimated $/month saved (negative = it costs money, e.g. a cache
                bought for latency rather than for the bill)
    confidence  0..1, probability the saving actually materialises
    effort      engineer-days to implement *and validate*
    risk        0..1, probability x severity of an SLO regression
    group       overlap group: candidates harvesting the same underlying spend
    requires    names that must ship first
    excludes    names that cannot ship alongside this one
    """

    name: str
    savings: float
    confidence: float
    effort: float
    risk: float
    group: Optional[str] = None
    requires: Tuple[str, ...] = ()
    excludes: Tuple[str, ...] = ()
    notes: Optional[str] = None

    @property
    def effective_effort(self) -> float:
        """Effort with the floor applied, so scoring can never divide by zero."""
        return max(self.effort, MIN_EFFORT)

    @property
    def expected_savings(self) -> float:
        """Confidence-weighted $/month."""
        return self.savings * self.confidence

    def score(self) -> float:
        """savings x confidence / (effort x (1 + risk)). Higher = do it sooner."""
        return self.expected_savings / (self.effective_effort * (1.0 + self.risk))

    def payback_days(self) -> float:
        """Calendar days to repay the engineering cost. inf if it never repays."""
        monthly = self.expected_savings
        if monthly <= 0:
            return float("inf")
        return (self.effective_effort * ENGINEER_DAY_COST) / (monthly / 30.0)


def parse_candidate(row: Any, index: Optional[int] = None) -> Candidate:
    """Validate one row into a :class:`Candidate`."""
    if not isinstance(row, Mapping):
        raise CandidateError(
            f"{_where(index, None)}: expected an object, got {type(row).__name__}"
        )

    raw_name = row.get("name")
    if not isinstance(raw_name, str) or not raw_name.strip():
        raise CandidateError(f"{_where(index, None)}: name is missing or empty")
    name = raw_name.strip()
    where = _where(index, name)

    for key in row:
        if key not in KNOWN_FIELDS:
            hint = difflib.get_close_matches(str(key), KNOWN_FIELDS, n=1)
            suffix = f" (did you mean {hint[0]!r}?)" if hint else ""
            raise CandidateError(f"{where}: unknown field {key!r}{suffix}")
    for key in REQUIRED_FIELDS:
        if key not in row:
            raise CandidateError(f"{where}: missing required field {key!r}")

    savings = _coerce_number(row["savings"], "savings", where)
    if abs(savings) > MAX_PLAUSIBLE_SAVINGS:
        raise CandidateError(
            f"{where}: savings of {savings:g}/mo is implausible - check the units"
        )
    effort = _coerce_number(row["effort"], "effort", where)
    if effort < 0:
        raise CandidateError(f"{where}: effort cannot be negative, got {effort:g}")

    group = row.get("group")
    if group is not None and (not isinstance(group, str) or not group.strip()):
        raise CandidateError(f"{where}: group must be a non-empty string")
    notes = row.get("notes")
    if notes is not None and not isinstance(notes, str):
        raise CandidateError(f"{where}: notes must be a string")

    candidate = Candidate(
        name=name,
        savings=savings,
        confidence=_coerce_unit(row["confidence"], "confidence", where),
        effort=effort,
        risk=_coerce_unit(row["risk"], "risk", where),
        group=group.strip() if isinstance(group, str) else None,
        requires=_coerce_names(row.get("requires"), "requires", where),
        excludes=_coerce_names(row.get("excludes"), "excludes", where),
        notes=notes,
    )
    if candidate.name in candidate.requires:
        raise CandidateError(f"{where}: cannot require itself")
    if candidate.name in candidate.excludes:
        raise CandidateError(f"{where}: cannot exclude itself")
    return candidate


def parse_candidates(rows: Any) -> List[Candidate]:
    """Validate a whole candidate set, including cross-row references.

    Accepts a bare list or a ``{"candidates": [...]}`` wrapper, since both shapes
    turn up in the wild.
    """
    if isinstance(rows, Mapping):
        if "candidates" not in rows:
            raise CandidateError(
                "expected a list of candidates or an object with a 'candidates' key"
            )
        rows = rows["candidates"]
    if not isinstance(rows, (list, tuple)):
        raise CandidateError(
            f"expected a list of candidates, got {type(rows).__name__}"
        )

    candidates = [parse_candidate(row, i) for i, row in enumerate(rows)]

    seen: Dict[str, int] = {}
    for i, cand in enumerate(candidates):
        key = cand.name.casefold()
        if key in seen:
            raise CandidateError(
                f"duplicate candidate name {cand.name!r} (rows {seen[key]} and {i}); "
                "names are identities for requires/excludes"
            )
        seen[key] = i

    by_name = {c.name: c for c in candidates}
    for cand in candidates:
        for ref_field in ("requires", "excludes"):
            for ref in getattr(cand, ref_field):
                if ref not in by_name:
                    hint = difflib.get_close_matches(ref, list(by_name), n=1)
                    suffix = f" (did you mean {hint[0]!r}?)" if hint else ""
                    raise CandidateError(
                        f"candidate {cand.name!r}: {ref_field} references unknown "
                        f"candidate {ref!r}{suffix}"
                    )
    _assert_no_requirement_cycle(candidates)
    return candidates


def _assert_no_requirement_cycle(candidates: Sequence[Candidate]) -> None:
    by_name = {c.name: c for c in candidates}
    state: Dict[str, int] = {}  # 0 = visiting, 1 = done

    def visit(name: str, path: List[str]) -> None:
        mark = state.get(name)
        if mark == 1:
            return
        if mark == 0:
            cycle = " -> ".join(path[path.index(name):] + [name])
            raise CandidateError(f"requirement cycle: {cycle}")
        state[name] = 0
        for dep in by_name[name].requires:
            visit(dep, path + [name])
        state[name] = 1

    for cand in candidates:
        visit(cand.name, [])


def load_candidates(path: str) -> List[Candidate]:
    """Read and validate a candidate JSON file."""
    import json

    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    if not text.strip():
        raise CandidateError(f"{path}: file is empty")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CandidateError(f"{path}: invalid JSON at line {exc.lineno}: {exc.msg}")
    return parse_candidates(raw)
