"""Shared library behind the AWS-cost-optimal CLIs.

Modules:
  model        Candidate parsing/validation (strict, industrial input hygiene)
  portfolio    Constrained selection: effort budget, overlap, exclusions, risk
  commitment   Optimal Savings Plan / RI commit level from a usage series
  rightsizing  SLO-headroom right-sizing advice from utilisation samples

Stdlib only, Python 3.9+.
"""

__all__ = ["model", "portfolio", "commitment", "rightsizing"]
__version__ = "0.2.0"
