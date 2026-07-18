"""
Devig engine: strip the vig from a sharp book's two-sided market to
recover the fair (no-vig) probability of each outcome.

Methods:
  multiplicative — divide each implied prob by the overround. Simple,
                   slightly biased on longshots.
  power          — solve for k such that sum(p_i^k) = 1. Handles
                   favorite-longshot bias better; industry default.
  worst_case     — take the max implied prob across multiplicative and
                   power. Conservative: EV you see is EV you get.
"""

from typing import List


def implied(decimal_odds: float) -> float:
    return 1.0 / decimal_odds


def overround(decimals: List[float]) -> float:
    """Total implied prob of the market. 1.05 = 5% vig (two-way)."""
    return sum(implied(d) for d in decimals)


def devig_multiplicative(decimals: List[float]) -> List[float]:
    total = overround(decimals)
    return [implied(d) / total for d in decimals]


def devig_power(decimals: List[float], tol: float = 1e-10, max_iter: int = 100) -> List[float]:
    """Find k via bisection so that sum((1/d)^k) == 1."""
    probs = [implied(d) for d in decimals]
    lo, hi = 0.5, 3.0
    for _ in range(max_iter):
        k = (lo + hi) / 2.0
        s = sum(p ** k for p in probs)
        if abs(s - 1.0) < tol:
            break
        if s > 1.0:
            lo = k          # probs too big -> raise exponent
        else:
            hi = k
    raw = [p ** k for p in probs]
    total = sum(raw)
    return [r / total for r in raw]


def devig(decimals: List[float], method: str = "power") -> List[float]:
    if len(decimals) < 2:
        raise ValueError("Need a full two-plus-sided market to devig.")
    if method == "multiplicative":
        return devig_multiplicative(decimals)
    if method == "power":
        return devig_power(decimals)
    if method == "worst_case":
        m = devig_multiplicative(decimals)
        p = devig_power(decimals)
        raw = [max(a, b) for a, b in zip(m, p)]
        total = sum(raw)
        return [r / total for r in raw]
    raise ValueError(f"Unknown devig method: {method}")


def ev_pct(fair_prob: float, decimal_odds: float) -> float:
    """Expected value as % of stake: p*dec - 1."""
    return (fair_prob * decimal_odds - 1.0) * 100.0


def kelly_units(fair_prob: float, decimal_odds: float,
                fraction: float, bankroll_units: float) -> float:
    """Fractional Kelly stake in units. Never negative."""
    b = decimal_odds - 1.0
    if b <= 0:
        return 0.0
    q = 1.0 - fair_prob
    full = (b * fair_prob - q) / b
    return max(0.0, full * fraction * bankroll_units)
