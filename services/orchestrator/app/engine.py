"""Deterministic credit engine.

Pure functions over a JSON-serialisable aggregate. No I/O, no randomness, no AI:
the same inputs always give the same decision. Monetary values are integer USDC
base units (6 decimals), rates are basis points, timestamps are UTC epoch seconds.

The rules mirror ``contracts/arc/src/WorkingCapitalFacility.sol`` exactly
(check order, fee formula, pro-rata waterfall and rounding), so a decision made
here is the decision the Arc contract will make.
"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import compliance, verification

USDC = 1_000_000
BPS = 10_000
DAY = 86_400
YEAR = 365 * DAY
LIFECYCLE = ("DRAFT", "VERIFIED", "TOKENIZED", "FUNDING", "ACTIVE", "REPAID")
# Exception states a funded facility can enter. Spending stops; repayment still works.
EXCEPTIONS = ("OVERDUE", "DEFAULTED", "DISPUTED")
REPAYABLE = ("ACTIVE", *EXCEPTIONS)
GRACE_DAYS = 7      # unpaid this long after the due date -> OVERDUE (matches the contract)
DEFAULT_DAYS = 30   # unpaid this long after the due date -> DEFAULTED (matches the contract)
SCORE = {"A": 3, "A-": 2, "B": 1}
FIXTURE_PATH = Path(os.environ.get("WC_FIXTURE_PATH") or Path(__file__).resolve().parents[3] / "packages" / "shared" / "fixtures" / "retailco-demo.json")


class PolicyError(ValueError):
    """A deterministic rejection with a stable machine-readable code."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail or code


class TransitionError(ValueError):
    """An action that is not allowed from the aggregate's current lifecycle state."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.code = "INVALID_TRANSITION"
        self.detail = detail


def load_fixture(path: Path = FIXTURE_PATH) -> dict[str, Any]:
    return json.loads(path.read_text())


def iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def money(minor: int) -> str:
    """Two-decimal display string, truncated toward zero (never overstates a balance)."""
    sign = "-" if minor < 0 else ""
    value = abs(minor)
    return f"{sign}{value // USDC}.{(value % USDC) // 10_000:02d}"


def usd(minor: int) -> str:
    whole, cents = money(minor).split(".")
    return f"${int(whole):,}.{cents}"


# --------------------------------------------------------------------- receivable
canonical_receivable_id = verification.canonical_receivable_id


def borrowing_base(face_value_minor: int, advance_rate_bps: int) -> int:
    return face_value_minor * advance_rate_bps // BPS


def lender_fee(principal_minor: int, apr_bps: int, elapsed_seconds: int, min_term_days: int) -> int:
    """principal x APR x max(elapsed, minimum term) / 365 days, floored (matches the contract)."""
    term = max(elapsed_seconds, min_term_days * DAY)
    return principal_minor * apr_bps * term // (BPS * YEAR)


def invoice_of(rec: dict[str, Any]) -> dict[str, Any]:
    """The fields the verification rules read."""
    keys = ("country", "sellerTaxId", "buyerTaxId", "buyerType", "documentType", "invoiceNumber", "invoiceDate", "dueDate", "faceValueMinor", "irn")
    return {k: rec.get(k) for k in keys}


def new_aggregate(aggregate_id: str, fixture: dict[str, Any], now: int, run: int = 1) -> dict[str, Any]:
    r = fixture["receivable"]
    borrower = fixture["borrower"]
    due_ts = now + r["termsDays"] * DAY
    due_date = datetime.fromtimestamp(due_ts, tz=timezone.utc).date().isoformat()
    invoice_date = datetime.fromtimestamp(now, tz=timezone.utc).date().isoformat()
    invoice = {"country": r["country"], "sellerTaxId": r.get("sellerTaxId"), "invoiceDate": invoice_date}
    canonical = canonical_receivable_id(r["country"], verification.seller_key(invoice, borrower), verification.invoice_period(invoice), r["documentType"], r["invoiceNumber"])
    return {
        "id": aggregate_id,
        "run": run,
        "chainKey": f"{aggregate_id}#{run}",
        "createdAt": now,
        "borrower": dict(borrower),
        "receivable": {
            "invoiceNumber": r["invoiceNumber"],
            "documentType": r["documentType"],
            "country": r["country"],
            "sellerTaxId": r.get("sellerTaxId"),
            "buyerTaxId": r.get("buyerTaxId"),
            "buyerType": r.get("buyerType", "BUSINESS"),
            "irn": None,
            "buyer": r["buyer"],
            "buyerScore": r["buyerScore"],
            "operator": r["operator"],
            "faceValueMinor": r["faceValueMinor"],
            "currency": r["currency"],
            "termsDays": r["termsDays"],
            "requestedAdvanceRateBps": r["advanceRateBps"],
            "advanceRateBps": r["advanceRateBps"],
            "invoiceDate": invoice_date,
            "dueDate": due_date,
            "dueAt": due_ts,
            "canonicalId": canonical,
            "status": "DRAFT",
            "verification": None,
            "verificationExpiresAt": None,
            "token": None,
            "statusBefore": None,
            "closedAs": None,
        },
        "credit": None,
        "compliance": {"blockedMerchants": [], "screenedAt": None, "hits": []},
        "facility": {
            "status": "NOT_OPEN",
            "borrowingBaseMinor": borrowing_base(r["faceValueMinor"], r["advanceRateBps"]),
            "requestedMinor": fixture["fundingRequestMinor"],
            "fundedMinor": 0,
            "spentMinor": 0,
            "reservedMinor": 0,
            "lenderDueMinor": 0,
            "lenderPaidMinor": 0,
            "duesCrystallized": False,
            "customerPaidMinor": 0,
            "operatorPaidMinor": 0,
            "undrawnReturnedMinor": 0,
            "chain": None,
        },
        "offers": [
            {
                **lender,
                "blocked": False,
                "eligible": None,
                "rejection": None,
                "selected": False,
                "expiresAt": now + 7 * DAY,
            }
            for lender in fixture["lenders"]
        ],
        "allocations": [],
        "agents": [
            {**agent, "allocationMinor": 0, "plannedMinor": agent["allocationMinor"], "spentMinor": 0, "reservedMinor": 0, "day": 0, "dayUsedMinor": 0, "uaid": None, "merchants": [agent["merchant"]]}
            for agent in fixture["agents"]
        ],
        "lead": {**fixture["lead"], "spentMinor": 0, "reservedMinor": 0, "day": 0, "dayUsedMinor": 0, "perTxMinor": 0, "dailyMinor": 0, "merchants": [fixture["lead"]["merchant"]], "uaid": None},
        "intents": [],
        "scenarios": fixture["scenarios"],
        "intentSeq": 0,
    }


def _require(state: dict[str, Any], allowed: tuple[str, ...], action: str) -> None:
    status = state["receivable"]["status"]
    if status not in allowed:
        raise TransitionError(f"Cannot {action} while the receivable is {status}.")


def verify(state: dict[str, Any], now: int, result: dict[str, Any], terms: dict[str, Any], screening_hits: list[dict[str, str]] | None = None) -> list[dict[str, Any]]:
    """Accept a verified invoice. `result` comes from verification.verify_invoice and
    `terms` from credit.terms; both are pure, so this decision is reproducible."""
    _require(state, ("DRAFT",), "verify the invoice")
    error = compliance.kyc_error(state["borrower"])
    if error:
        raise PolicyError(error, "The borrower must pass KYC/KYB before an invoice can be financed.")
    hits = screening_hits or []
    party_hits = [h for h in hits if h["role"] in ("borrower", "seller", "buyer")]
    if party_hits:
        raise PolicyError("SANCTIONS_MATCH", f"Screening matched the {party_hits[0]['role']} ({party_hits[0]['list']}).")
    if not result["eligible"]:
        raise PolicyError(result["rejection"], result["detail"])
    if terms["tier"] == "BLOCKED":
        raise PolicyError("CREDIT_TIER_BLOCKED", terms["tierReason"] or "The credit score is below the minimum for new credit.")
    if terms["borrowingBaseMinor"] <= 0:
        raise PolicyError("CREDIT_LIMIT_REACHED", "Open facilities already use the whole credit limit.")
    rec = state["receivable"]
    fac = state["facility"]
    rec["status"] = "VERIFIED"
    rec["verification"] = {**result, "verifiedAt": now}
    rec["advanceRateBps"] = terms["advanceRateBps"]
    fac["borrowingBaseMinor"] = terms["borrowingBaseMinor"]
    fac["requestedMinor"] = min(fac["requestedMinor"], terms["borrowingBaseMinor"])
    state["credit"] = {**terms, "assessedAt": now}
    blocked_lenders = {h["ref"] for h in hits if h["role"] == "lender"}
    for offer in state["offers"]:
        offer["blocked"] = offer["id"] in blocked_lenders
    state["compliance"] = {"blockedMerchants": sorted({h["ref"] for h in hits if h["role"] == "merchant"}), "screenedAt": now, "hits": hits}
    # Verification goes stale at the due date; a stale claim cannot support new borrowing.
    rec["verificationExpiresAt"] = rec["dueAt"]
    return [{"type": "receivable.verified", "canonicalId": rec["canonicalId"], "faceValueMinor": rec["faceValueMinor"], "evidenceTier": result["tier"], "creditTier": terms["tier"], "score": terms["score"], "advanceRateBps": terms["advanceRateBps"], "borrowingBaseMinor": terms["borrowingBaseMinor"]}]


def tokenize(state: dict[str, Any], now: int, token: dict[str, Any]) -> list[dict[str, Any]]:
    _require(state, ("VERIFIED",), "tokenize the receivable")
    if not token.get("tokenId"):
        raise PolicyError("ATS_TOKEN_REQUIRED", "An ATS token reference is required.")
    state["receivable"]["status"] = "TOKENIZED"
    state["receivable"]["token"] = {**token, "tokenizedAt": now}
    return [{"type": "receivable.tokenized", "tokenId": token["tokenId"], "simulated": token.get("simulated", False)}]


def is_stale(state: dict[str, Any], now: int) -> bool:
    expires = state["receivable"]["verificationExpiresAt"]
    return expires is None or now >= expires


# --------------------------------------------------------------------- funding
def offer_rejection(offer: dict[str, Any], state: dict[str, Any], now: int) -> str | None:
    rec = state["receivable"]
    compliance_error = compliance.lender_error(offer, offer.get("blocked", False))
    if compliance_error:
        return compliance_error
    if SCORE[offer["minBuyerScore"]] > SCORE[rec["buyerScore"]]:
        return "BUYER_SCORE_INELIGIBLE"
    if offer["maxAdvanceRateBps"] < rec["advanceRateBps"]:
        return "ADVANCE_RATE_INELIGIBLE"
    if offer["maxTenorDays"] < rec["termsDays"]:
        return "TENOR_INELIGIBLE"
    if offer["expiresAt"] <= now:
        return "OFFER_EXPIRED"
    if offer["availableMinor"] <= 0:
        return "NO_LIQUIDITY"
    return None


def quote(state: dict[str, Any], now: int, requested_minor: int | None = None) -> dict[str, Any]:
    """Filter offers by buyer score, advance rate, tenor, expiry and liquidity, then fill cheapest APR first."""
    _require(state, ("TOKENIZED", "FUNDING"), "match lenders")
    if is_stale(state, now):
        raise PolicyError("STALE_RECEIVABLE", "Receivable verification has expired; re-verify before borrowing.")
    fac = state["facility"]
    requested = requested_minor if requested_minor is not None else fac["requestedMinor"]
    if requested <= 0:
        raise PolicyError("INVALID_AMOUNT", "The funding request must be positive.")
    if requested > fac["borrowingBaseMinor"]:
        raise PolicyError("BORROWING_BASE_EXCEEDED", f"Request {usd(requested)} exceeds the {usd(fac['borrowingBaseMinor'])} borrowing base.")
    remaining = requested
    plan: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    for offer in sorted(state["offers"], key=lambda o: (o["aprBps"], o["id"])):
        reason = offer_rejection(offer, state, now)
        offer["eligible"] = reason is None
        offer["rejection"] = reason
        if reason:
            rejected.append({"lender": offer["id"], "reason": reason})
            continue
        take = min(remaining, offer["availableMinor"])
        if take > 0:
            plan.append({"lender": offer["id"], "principalMinor": take, "aprBps": offer["aprBps"], "minTermDays": offer["minTermDays"]})
            remaining -= take
    if remaining > 0:
        raise PolicyError("INSUFFICIENT_ELIGIBLE_LIQUIDITY", f"Eligible offers leave {usd(remaining)} unfunded.")
    return {"requestedMinor": requested, "allocations": plan, "rejected": rejected}


def begin_funding(state: dict[str, Any], plan: dict[str, Any]) -> list[dict[str, Any]]:
    _require(state, ("TOKENIZED",), "start funding")
    state["receivable"]["status"] = "FUNDING"
    state["facility"]["status"] = "FUNDING"
    for offer in state["offers"]:
        offer["selected"] = any(a["lender"] == offer["id"] for a in plan["allocations"])
    return [{"type": "facility.quoted", "allocations": plan["allocations"], "rejected": plan["rejected"]}]


def record_fill(state: dict[str, Any], now: int, allocation: dict[str, Any], evidence: dict[str, Any]) -> list[dict[str, Any]]:
    _require(state, ("FUNDING",), "record a lender fill")
    fac = state["facility"]
    if any(a["lender"] == allocation["lender"] for a in state["allocations"]):
        raise PolicyError("DUPLICATE_FILL", f"{allocation['lender']} is already funded.")
    if fac["fundedMinor"] + allocation["principalMinor"] > fac["borrowingBaseMinor"]:
        raise PolicyError("BORROWING_BASE_EXCEEDED", "Fill would exceed the borrowing base.")
    state["allocations"].append({**allocation, "fundedAt": now, "dueMinor": 0, "paidMinor": 0, **evidence})
    fac["fundedMinor"] += allocation["principalMinor"]
    return [{"type": "facility.lender_filled", "lender": allocation["lender"], "principalMinor": allocation["principalMinor"], **{k: v for k, v in evidence.items() if k in ("txHash", "viaAqua", "notesMinor")}}]


def activate(state: dict[str, Any], chain: dict[str, Any] | None) -> list[dict[str, Any]]:
    _require(state, ("FUNDING",), "activate the facility")
    if state["facility"]["fundedMinor"] <= 0:
        raise PolicyError("NOT_FUNDED", "No lender fills were recorded.")
    state["receivable"]["status"] = "ACTIVE"
    state["facility"]["status"] = "ACTIVE"
    state["facility"]["chain"] = chain
    return [{"type": "facility.activated", "fundedMinor": state["facility"]["fundedMinor"]}]


def assign_budgets(state: dict[str, Any], budgets: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Budgets are reservations of the funded line: their sum can never exceed it."""
    _require(state, ("ACTIVE",), "assign budgets")
    by_identity = {b["identity"]: b for b in (budgets or [])}
    planned = []
    for agent in state["agents"]:
        b = by_identity.get(agent["identity"], {})
        allocation = int(b.get("allocationMinor", agent["plannedMinor"]))
        if allocation < agent["spentMinor"] + agent["reservedMinor"]:
            raise PolicyError("BUDGET_CEILING_EXCEEDED", f"{agent['name']} has already used more than {usd(allocation)}.")
        planned.append((agent, allocation, int(b.get("perTxMinor", agent["perTxMinor"])), int(b.get("dailyMinor", agent["dailyMinor"]))))
    total = sum(p[1] for p in planned)
    if total > state["facility"]["fundedMinor"]:
        raise PolicyError("BUDGET_CEILING_EXCEEDED", f"Budgets total {usd(total)} but only {usd(state['facility']['fundedMinor'])} is funded.")
    for agent, allocation, per_tx, daily in planned:
        agent["allocationMinor"], agent["perTxMinor"], agent["dailyMinor"] = allocation, per_tx, daily
    return [{"type": "facility.budgets_assigned", "budgets": {a["identity"]: a["allocationMinor"] for a in state["agents"]}}]


# --------------------------------------------------------------------- spend
def _spender(state: dict[str, Any], identity: str) -> dict[str, Any]:
    if identity == "lead":
        return state["lead"]
    for agent in state["agents"]:
        if agent["identity"] == identity:
            return agent
    raise PolicyError("UNKNOWN_AGENT", f"No agent {identity}.")


def available_credit(state: dict[str, Any]) -> int:
    fac = state["facility"]
    return fac["fundedMinor"] - fac["spentMinor"] - fac["reservedMinor"]


def check_spend(state: dict[str, Any], identity: str, merchant: str, amount: int, now: int) -> None:
    """Same order as WorkingCapitalFacility.reserveSpend: capacity first, then policy limits."""
    fac = state["facility"]
    status = state["receivable"]["status"]
    if status in EXCEPTIONS:
        detail = {"OVERDUE": "The invoice is overdue; spending is frozen until it is repaid.", "DEFAULTED": "The invoice defaulted; spending is closed.", "DISPUTED": "The invoice is disputed; spending is frozen until the dispute is resolved."}[status]
        raise PolicyError(f"FACILITY_{status}", detail)
    if status != "ACTIVE" or fac["status"] != "ACTIVE":
        raise PolicyError("FACILITY_NOT_ACTIVE", "Fund the facility before agents can spend.")
    if is_stale(state, now):
        raise PolicyError("STALE_RECEIVABLE", "Receivable verification has expired.")
    if amount <= 0:
        raise PolicyError("INVALID_AMOUNT", "Spend amount must be positive.")
    spender = _spender(state, identity)
    if amount > available_credit(state):
        raise PolicyError("GLOBAL_CREDIT_LIMIT_EXCEEDED", f"{spender['name']} requested {usd(amount)}; the facility has {usd(available_credit(state))} of remaining credit.")
    if identity != "lead" and spender["spentMinor"] + spender["reservedMinor"] + amount > spender["allocationMinor"]:
        raise PolicyError("AGENT_BUDGET_EXCEEDED", f"{spender['name']} requested {usd(amount)} against its {usd(spender['allocationMinor'])} allocation.")
    if spender["perTxMinor"] and amount > spender["perTxMinor"]:
        raise PolicyError("TRANSACTION_LIMIT_EXCEEDED", f"{usd(amount)} exceeds {spender['name']}'s {usd(spender['perTxMinor'])} per-transaction limit.")
    day = now // DAY
    used = spender["dayUsedMinor"] if spender["day"] == day else 0
    if spender["dailyMinor"] and used + amount > spender["dailyMinor"]:
        raise PolicyError("DAILY_LIMIT_EXCEEDED", f"{spender['name']} would exceed its {usd(spender['dailyMinor'])} daily limit.")
    if merchant not in spender["merchants"]:
        raise PolicyError("MERCHANT_NOT_ALLOWED", f"{merchant} is not on {spender['name']}'s merchant allowlist.")
    if merchant in state.get("compliance", {}).get("blockedMerchants", []):
        raise PolicyError("MERCHANT_BLOCKED", f"{merchant} matched sanctions screening.")


def create_intent(state: dict[str, Any], identity: str, merchant: str, amount: int, now: int, ttl: int = 300) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    state["intentSeq"] += 1
    intent = {
        "id": f"{state['id']}.si-{state['intentSeq']}",
        "chainKey": f"{state['chainKey']}:si-{state['intentSeq']}",
        "agent": identity,
        "merchant": merchant,
        "amountMinor": amount,
        "status": "PENDING",
        "reason": None,
        "createdAt": now,
        "expiresAt": now + ttl,
        "evidence": {},
    }
    try:
        check_spend(state, identity, merchant, amount, now)
    except PolicyError as exc:
        intent["status"] = "REJECTED"
        intent["reason"] = exc.code
        intent["detail"] = exc.detail
        state["intents"].insert(0, intent)
        return intent, [{"type": "spend.rejected", "intentId": intent["id"], "agent": identity, "amountMinor": amount, "code": exc.code}]
    state["intents"].insert(0, intent)
    return intent, [{"type": "spend.created", "intentId": intent["id"], "agent": identity, "amountMinor": amount}]


def find_intent(state: dict[str, Any], intent_id: str) -> dict[str, Any]:
    for intent in state["intents"]:
        if intent["id"] == intent_id:
            return intent
    raise PolicyError("UNKNOWN_INTENT", f"No spend intent {intent_id}.")


def reserve(state: dict[str, Any], intent_id: str, now: int, evidence: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    intent = find_intent(state, intent_id)
    if intent["status"] != "PENDING":
        raise TransitionError(f"Intent {intent_id} is {intent['status']}, not PENDING.")
    check_spend(state, intent["agent"], intent["merchant"], intent["amountMinor"], now)
    spender = _spender(state, intent["agent"])
    day = now // DAY
    if spender["day"] != day:
        spender["day"], spender["dayUsedMinor"] = day, 0
    spender["dayUsedMinor"] += intent["amountMinor"]
    spender["reservedMinor"] += intent["amountMinor"]
    state["facility"]["reservedMinor"] += intent["amountMinor"]
    intent["status"] = "RESERVED"
    intent["reservedAt"] = now
    intent["evidence"].update(evidence or {})
    return [{"type": "spend.reserved", "intentId": intent_id, "amountMinor": intent["amountMinor"]}]


def mark_paying(state: dict[str, Any], intent_id: str) -> list[dict[str, Any]]:
    intent = find_intent(state, intent_id)
    if intent["status"] != "RESERVED":
        raise TransitionError(f"Intent {intent_id} is {intent['status']}, not RESERVED.")
    intent["status"] = "PAYING"
    return [{"type": "spend.paying", "intentId": intent_id}]


def commit(state: dict[str, Any], intent_id: str, now: int, evidence: dict[str, Any]) -> list[dict[str, Any]]:
    """Commit exactly once; a finalized settlement receipt is required."""
    intent = find_intent(state, intent_id)
    if intent["status"] == "SETTLED":
        return []
    if intent["status"] not in ("RESERVED", "PAYING"):
        raise TransitionError(f"Intent {intent_id} is {intent['status']}; it cannot be committed.")
    if evidence.get("finalized") is not True or not evidence.get("transactionId"):
        raise PolicyError("UNVERIFIED_X402_RECEIPT", "A finalized Hedera settlement receipt is required before commit.")
    spender = _spender(state, intent["agent"])
    amount = intent["amountMinor"]
    spender["reservedMinor"] -= amount
    spender["spentMinor"] += amount
    state["facility"]["reservedMinor"] -= amount
    state["facility"]["spentMinor"] += amount
    intent["status"] = "SETTLED"
    intent["settledAt"] = now
    intent["evidence"].update(evidence)
    return [{"type": "spend.settled", "intentId": intent_id, "amountMinor": amount, "transactionId": evidence["transactionId"]}]


def release(state: dict[str, Any], intent_id: str, now: int, reason: str) -> list[dict[str, Any]]:
    intent = find_intent(state, intent_id)
    if intent["status"] == "RELEASED":
        return []
    if intent["status"] not in ("RESERVED", "PAYING"):
        raise TransitionError(f"Intent {intent_id} is {intent['status']}; it cannot be released.")
    spender = _spender(state, intent["agent"])
    amount = intent["amountMinor"]
    spender["reservedMinor"] -= amount
    if spender["day"] == now // DAY:
        spender["dayUsedMinor"] = max(0, spender["dayUsedMinor"] - amount)
    state["facility"]["reservedMinor"] -= amount
    intent["status"] = "RELEASED"
    intent["reason"] = reason
    intent["releasedAt"] = now
    return [{"type": "spend.released", "intentId": intent_id, "amountMinor": amount, "reason": reason}]


# --------------------------------------------------------------------- repay
def crystallize(state: dict[str, Any], now: int) -> None:
    fac = state["facility"]
    if fac["duesCrystallized"]:
        return
    total = 0
    for alloc in state["allocations"]:
        fee = lender_fee(alloc["principalMinor"], alloc["aprBps"], now - alloc["fundedAt"], alloc["minTermDays"])
        alloc["feeMinor"] = fee
        alloc["dueMinor"] = alloc["principalMinor"] + fee
        total += alloc["dueMinor"]
    fac["lenderDueMinor"] = total
    fac["duesCrystallized"] = True


def lender_outstanding(state: dict[str, Any], now: int) -> int:
    probe = deepcopy(state)
    crystallize(probe, now)
    return probe["facility"]["lenderDueMinor"] - probe["facility"]["lenderPaidMinor"]


def _set_status(state: dict[str, Any], status: str) -> None:
    state["receivable"]["status"] = status
    state["facility"]["status"] = status


def assess(state: dict[str, Any], now: int) -> list[dict[str, Any]]:
    """Move an unpaid facility to OVERDUE or DEFAULTED once enough time has passed.
    Anyone may trigger this; it only reads the clock, like the contract's markOverdue."""
    status = state["receivable"]["status"]
    if status not in ("ACTIVE", "OVERDUE"):
        return []
    due = state["receivable"]["dueAt"]
    if now >= due + DEFAULT_DAYS * DAY:
        _set_status(state, "DEFAULTED")
        return [{"type": "facility.defaulted", "daysLate": (now - due) // DAY, "lenderOutstandingMinor": lender_outstanding(state, now)}]
    if status == "ACTIVE" and now >= due + GRACE_DAYS * DAY:
        _set_status(state, "OVERDUE")
        return [{"type": "facility.overdue", "daysLate": (now - due) // DAY}]
    return []


def dispute(state: dict[str, Any], now: int, reason: str) -> list[dict[str, Any]]:
    _require(state, ("ACTIVE", "OVERDUE"), "dispute the invoice")
    if not reason.strip():
        raise PolicyError("DISPUTE_REASON_REQUIRED", "Say why the invoice is disputed.")
    state["receivable"]["statusBefore"] = state["receivable"]["status"]
    state["receivable"]["dispute"] = {"reason": reason.strip(), "openedAt": now}
    _set_status(state, "DISPUTED")
    return [{"type": "facility.disputed", "status": "DISPUTED"}]


def resolve_dispute(state: dict[str, Any], now: int, upheld: bool) -> list[dict[str, Any]]:
    """upheld=True: the buyer was right and the invoice is not owed, so the seller is in
    default (recourse). upheld=False: the invoice stands and the facility resumes."""
    _require(state, ("DISPUTED",), "resolve a dispute")
    rec = state["receivable"]
    rec["dispute"] = {**rec.get("dispute", {}), "resolvedAt": now, "upheld": upheld}
    if upheld:
        _set_status(state, "DEFAULTED")
        return [{"type": "facility.dispute_upheld", "status": "DEFAULTED"}]
    _set_status(state, rec.get("statusBefore") or "ACTIVE")
    return [{"type": "facility.dispute_cleared", "status": rec["status"]}, *assess(state, now)]


def close_outcome(state: dict[str, Any], status_before: str, now: int) -> str:
    """How a repaid facility counts for the borrower's credit history."""
    if status_before == "DEFAULTED":
        return "RECOVERED"
    if now <= state["receivable"]["dueAt"] + GRACE_DAYS * DAY and status_before == "ACTIVE":
        return "ON_TIME"
    return "LATE"


def repay(state: dict[str, Any], amount: int, now: int, evidence: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Lenders receive principal plus fee, pro rata, before any amount reaches the operator.
    Works while ACTIVE, OVERDUE, DEFAULTED or DISPUTED: lenders can always be paid."""
    _require(state, REPAYABLE, "repay the facility")
    status_before = state["receivable"]["status"]
    if amount <= 0:
        raise PolicyError("INVALID_AMOUNT", "Repayment must be positive.")
    fac = state["facility"]
    crystallize(state, now)
    owed = fac["lenderDueMinor"] - fac["lenderPaidMinor"]
    to_lenders = min(amount, owed)
    shares = []
    if to_lenders:
        for alloc in state["allocations"]:
            remaining = alloc["dueMinor"] - alloc["paidMinor"]
            shares.append(remaining if to_lenders == owed else to_lenders * remaining // owed)
        distributed = sum(shares)
        for i, alloc in enumerate(state["allocations"]):  # rounding dust, in order, to lenders with capacity
            if distributed >= to_lenders:
                break
            capacity = alloc["dueMinor"] - alloc["paidMinor"] - shares[i]
            extra = min(to_lenders - distributed, capacity)
            shares[i] += extra
            distributed += extra
        for alloc, share in zip(state["allocations"], shares):
            alloc["paidMinor"] += share
        fac["lenderPaidMinor"] += to_lenders
    to_operator = amount - to_lenders
    fac["customerPaidMinor"] += amount
    fac["operatorPaidMinor"] += to_operator
    events = [{"type": "repayment.received", "amountMinor": amount, "toLendersMinor": to_lenders, "toOperatorMinor": to_operator, **(evidence or {})}]
    if fac["lenderPaidMinor"] == fac["lenderDueMinor"] and fac["reservedMinor"] == 0:
        fac["undrawnReturnedMinor"] = fac["fundedMinor"] - fac["spentMinor"]
        outcome = close_outcome(state, status_before, now)
        state["receivable"]["closedAs"] = outcome
        _set_status(state, "REPAID")
        events.append({"type": "facility.repaid", "undrawnReturnedMinor": fac["undrawnReturnedMinor"], "outcome": outcome})
    return events


def debt_outstanding(state: dict[str, Any], now: int) -> int:
    if state["facility"]["status"] in ("NOT_OPEN", "FUNDING") and not state["allocations"]:
        return 0
    if state["facility"]["status"] == "REPAID":
        return 0
    return lender_outstanding(state, now)
