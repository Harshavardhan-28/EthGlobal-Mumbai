"""Borrower credit score and step-up limits.

Deterministic and explainable: a score from 300 to 900 built only from what happened
on this platform. New borrowers start at 550 with a small limit; every on-time
repayment raises the score, and late payments, defaults and agents that keep hitting
policy limits lower it. The score picks a tier; the tier sets the credit limit and the
highest advance rate. AI never touches it.
"""

from __future__ import annotations

from math import ceil
from typing import Any

USDC = 1_000_000
BASE_SCORE = 550
MIN_SCORE, MAX_SCORE = 300, 900

ON_TIME_POINTS, ON_TIME_CAP = 40, 8           # up to +320
LATE_POINTS = -60
DEFAULT_POINTS = -250
SETTLED_SPEND_POINTS, SETTLED_SPEND_CAP = 2, 40   # agents paying cleanly, up to +40
REJECTION_POINTS, REJECTION_CAP = -10, -60        # agents hitting policy limits, down to -60

# (name, minimum score, credit limit in USDC, highest advance rate in bps), best first.
TIERS = (
    ("TRUSTED", 800, 25_000, 8000),
    ("ESTABLISHED", 700, 10_000, 7500),
    ("BUILDING", 600, 3_000, 6000),
    ("STARTER", 450, 1_000, 5000),
    ("BLOCKED", MIN_SCORE, 0, 0),
)

NEW_PROFILE = {
    "onTimeRepayments": 0,
    "lateRepayments": 0,
    "defaults": 0,
    "openDefaults": 0,
    "settledAgentPayments": 0,
    "agentPolicyRejections": 0,
    "openPrincipalMinor": 0,
}


def new_profile(borrower_id: str) -> dict[str, Any]:
    return {"borrowerId": borrower_id, **NEW_PROFILE}


def score(profile: dict[str, Any]) -> dict[str, Any]:
    on_time = min(profile["onTimeRepayments"], ON_TIME_CAP) * ON_TIME_POINTS
    late = profile["lateRepayments"] * LATE_POINTS
    defaults = profile["defaults"] * DEFAULT_POINTS
    agents_good = min(profile["settledAgentPayments"] * SETTLED_SPEND_POINTS, SETTLED_SPEND_CAP)
    agents_bad = max(profile["agentPolicyRejections"] * REJECTION_POINTS, REJECTION_CAP)
    value = max(MIN_SCORE, min(MAX_SCORE, BASE_SCORE + on_time + late + defaults + agents_good + agents_bad))
    return {
        "score": value,
        "breakdown": [
            {"factor": "Starting score", "points": BASE_SCORE},
            {"factor": f"{profile['onTimeRepayments']} on-time repayments", "points": on_time},
            {"factor": f"{profile['lateRepayments']} late repayments", "points": late},
            {"factor": f"{profile['defaults']} defaults", "points": defaults},
            {"factor": f"{profile['settledAgentPayments']} clean agent payments", "points": agents_good},
            {"factor": f"{profile['agentPolicyRejections']} agent policy rejections", "points": agents_bad},
        ],
    }


def tier_for(profile: dict[str, Any]) -> dict[str, Any]:
    value = score(profile)["score"]
    if profile["openDefaults"] > 0:
        name, minimum, limit, advance = TIERS[-1]
        return {"name": name, "minScore": minimum, "limitMinor": 0, "advanceCapBps": 0, "reason": "An unresolved default blocks new credit."}
    for name, minimum, limit, advance in TIERS:
        if value >= minimum:
            return {"name": name, "minScore": minimum, "limitMinor": limit * USDC, "advanceCapBps": advance, "reason": ""}
    raise AssertionError("unreachable: BLOCKED has the minimum score")


def next_step(profile: dict[str, Any]) -> str:
    current = tier_for(profile)
    if current["name"] == "BLOCKED" and profile["openDefaults"]:
        return "Repay the defaulted invoice to lift the block."
    value = score(profile)["score"]
    better = [t for t in TIERS if t[1] > value]
    if not better:
        return "Top tier reached."
    name, minimum, limit, _ = better[-1]
    if profile["onTimeRepayments"] >= ON_TIME_CAP:
        return f"{name} needs {minimum - value} more points."
    needed = ceil((minimum - value) / ON_TIME_POINTS)
    return f"{needed} more on-time repayment{'s' if needed != 1 else ''} to reach {name} (${limit:,} limit)."


def terms(profile: dict[str, Any], face_value_minor: int, requested_advance_bps: int, evidence_cap_bps: int) -> dict[str, Any]:
    """Advance = the lowest of what was asked, what the tier allows and what the evidence supports.
    Borrowing base = face x advance, never above the limit left after other open facilities."""
    tier = tier_for(profile)
    advance = min(requested_advance_bps, tier["advanceCapBps"], evidence_cap_bps)
    headroom = max(0, tier["limitMinor"] - profile["openPrincipalMinor"])
    uncapped = face_value_minor * advance // 10_000
    base = min(uncapped, headroom)
    # Which rule set the borrowing base, so the dashboard can explain it.
    if base < uncapped:
        binding = "limit"
    elif advance < requested_advance_bps and advance == evidence_cap_bps:
        binding = "evidence"
    elif advance < requested_advance_bps:
        binding = "tier"
    else:
        binding = "request"
    return {
        **score(profile),
        "tier": tier["name"],
        "tierReason": tier["reason"],
        "limitMinor": tier["limitMinor"],
        "headroomMinor": headroom,
        "advanceRateBps": advance,
        "borrowingBaseMinor": base,
        "binding": binding,
        "nextStep": next_step(profile),
    }


def record_close(profile: dict[str, Any], outcome: str, funded_minor: int, settled_payments: int, policy_rejections: int) -> dict[str, Any]:
    """Fold one closed facility into the profile. outcome: ON_TIME, LATE or RECOVERED."""
    updated = dict(profile)
    if outcome == "ON_TIME":
        updated["onTimeRepayments"] += 1
    elif outcome == "LATE":
        updated["lateRepayments"] += 1
    elif outcome == "RECOVERED":
        updated["openDefaults"] = max(0, updated["openDefaults"] - 1)
    else:
        raise ValueError(f"Unknown outcome {outcome}")
    updated["settledAgentPayments"] += settled_payments
    updated["agentPolicyRejections"] += policy_rejections
    updated["openPrincipalMinor"] = max(0, updated["openPrincipalMinor"] - funded_minor)
    return updated


def record_default(profile: dict[str, Any]) -> dict[str, Any]:
    updated = dict(profile)
    updated["defaults"] += 1
    updated["openDefaults"] += 1
    return updated


def record_funding(profile: dict[str, Any], funded_minor: int) -> dict[str, Any]:
    return {**profile, "openPrincipalMinor": profile["openPrincipalMinor"] + funded_minor}
