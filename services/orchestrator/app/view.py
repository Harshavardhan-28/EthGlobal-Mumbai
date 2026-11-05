"""Dashboard read model derived from the aggregate (decimal strings, explorer links, labels)."""

from __future__ import annotations

from typing import Any

from . import compliance, credit, verification
from .engine import available_credit, debt_outstanding, iso, lender_outstanding, money

STAGE = {"DRAFT": "verify", "VERIFIED": "verify", "TOKENIZED": "fund", "FUNDING": "fund", "ACTIVE": "spend", "OVERDUE": "spend", "DEFAULTED": "spend", "DISPUTED": "spend", "REPAID": "repay"}


def short(value: str | None, head: int = 6, tail: int = 4) -> str:
    if not value:
        return ""
    return value if len(value) <= head + tail + 1 else f"{value[:head]}…{value[-tail:]}"


def masked(tax_id: str | None) -> str:
    return f"{tax_id[:7]}…{tax_id[-3:]}" if tax_id and len(tax_id) > 10 else (tax_id or "None")


def credit_block(state: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    rec = state["receivable"]
    assessed = state.get("credit")
    terms = assessed or credit.terms(profile, rec["faceValueMinor"], rec["requestedAdvanceRateBps"], verification.TIERS["GST_E_INVOICE"]["advanceCapBps"])
    after = state.get("creditAfter")
    return {
        "score": terms["score"],
        "tier": terms["tier"],
        "limit": money(terms["limitMinor"]),
        "headroom": money(terms["headroomMinor"]),
        "advanceRateBps": terms["advanceRateBps"],
        "binding": terms["binding"],
        "nextStep": after["nextStep"] if after else terms["nextStep"],
        "breakdown": terms["breakdown"],
        "assessed": bool(assessed),
        "after": ({"score": after["after"], "before": after["before"], "tier": after["tier"], "outcome": after["outcome"], "limit": money(after["limitMinor"])} if after else None),
        "history": {k: profile[k] for k in ("onTimeRepayments", "lateRepayments", "defaults", "settledAgentPayments", "agentPolicyRejections")},
        "tiers": [{"name": n, "minScore": m, "limit": f"{lim:,}", "advance": f"{a / 100:.0f}%"} for n, m, lim, a in credit.TIERS],
    }


def build_view(state: dict[str, Any], events: list[dict[str, Any]], modes: dict[str, Any], now: int, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    rec = state["receivable"]
    profile = profile or credit.new_profile(state["borrower"]["id"])
    checked = rec.get("verification") or {}
    fac = state["facility"]
    token = rec.get("token") or {}
    selected = {a["lender"]: a for a in state["allocations"]}
    lender_due = fac["lenderDueMinor"] if fac["duesCrystallized"] else (lender_outstanding(state, now) if state["allocations"] else 0)
    fee_total = sum(a.get("feeMinor", 0) for a in state["allocations"]) if fac["duesCrystallized"] else max(0, lender_due - fac["fundedMinor"])
    funded = fac["fundedMinor"]
    assigned = sum(a["allocationMinor"] for a in state["agents"])
    agents = [
        {
            "name": a["name"],
            "role": a["role"],
            "identity": a["uaid"] or f"hcs14:{a['identity']} (pending registration)",
            "uaid": a["uaid"],
            "allocation": money(a["allocationMinor"] or a["plannedMinor"]),
            "assigned": a["allocationMinor"] > 0,
            "spent": money(a["spentMinor"]),
            "reserved": money(a["reservedMinor"]),
            "limit": money(a["perTxMinor"]) if a["perTxMinor"] else "-",
            "daily": money(a["dailyMinor"]) if a["dailyMinor"] else "-",
            "merchant": a["merchant"],
            "color": a["color"],
        }
        for a in state["agents"]
    ]
    agents.append({
        "name": "Reserve",
        "role": "Lead agent · unallocated credit",
        "identity": state["lead"]["uaid"] or "hcs14:lead (pending registration)",
        "uaid": state["lead"]["uaid"],
        "allocation": money(max(0, funded - assigned) if funded else 1_000_000_000),
        "assigned": funded > 0,
        "spent": money(state["lead"]["spentMinor"]),
        "reserved": money(state["lead"]["reservedMinor"]),
        "limit": "-",
        "daily": "-",
        "merchant": "Available to lead agent",
        "color": "amber",
    })
    spends = [
        {
            "id": i["id"],
            "agent": next((a["name"] for a in state["agents"] if a["identity"] == i["agent"]), "Lead"),
            "merchant": i["merchant"],
            "amount": money(i["amountMinor"]),
            "status": i["status"],
            "reason": i.get("reason"),
            "txId": i["evidence"].get("transactionId", ""),
            "explorerUrl": i["evidence"].get("explorerUrl"),
            "auditTxId": i["evidence"].get("auditTransactionId"),
            "commitTx": i["evidence"].get("commitTx"),
            "network": i["evidence"].get("network", "Hedera · testnet (simulated)" if modes["hedera"] == "simulated" else "Hedera · testnet"),
            "createdAt": iso(i["createdAt"]),
        }
        for i in state["intents"]
    ]
    chain = fac.get("chain") or {}
    return {
        "id": state["id"],
        "stage": STAGE[rec["status"]],
        "lifecycle": rec["status"],
        "mode": "live" if modes["hedera"] == "live" or modes["arc"] != "simulated" else "simulated",
        "modes": modes,
        "invoice": {
            "buyer": rec["buyer"],
            "operator": rec["operator"],
            "invoiceNumber": rec["invoiceNumber"],
            "faceValue": money(rec["faceValueMinor"]),
            "currency": rec["currency"],
            "terms": f"Net {rec['termsDays']}",
            "advanceRateBps": rec["advanceRateBps"],
            "status": rec["status"],
            "receivableId": short(rec["canonicalId"], 6, 4),
            "canonicalId": rec["canonicalId"],
            "tokenId": token.get("tokenId") or "Pending ATS issuance",
            "tokenSimulated": bool(token.get("simulated")),
            "tokenExplorerUrl": token.get("explorerUrl"),
            "dueDate": rec["dueDate"],
            "verification": checked.get("label") or "Awaiting GST verification",
            "verificationExpiresAt": iso(rec["verificationExpiresAt"]) if rec["verificationExpiresAt"] else None,
            "buyerScore": rec["buyerScore"],
            "country": rec["country"],
            "sellerTaxId": masked(rec.get("sellerTaxId")),
            "buyerTaxId": masked(rec.get("buyerTaxId")),
            "irn": short(rec.get("irn"), 8, 6) if rec.get("irn") else None,
            "irnSimulated": bool(checked.get("irnSimulated")),
            "evidenceTier": checked.get("tier"),
            "advanceCap": f"{checked['advanceCapBps'] / 100:.0f}%" if checked.get("advanceCapBps") else None,
            "checks": checked.get("checks", []),
            "closedAs": rec.get("closedAs"),
            "dispute": rec.get("dispute"),
        },
        "borrower": {"name": state["borrower"]["name"], "type": state["borrower"]["type"], "kycStatus": state["borrower"]["kycStatus"], "kycMethod": state["borrower"].get("kycMethod")},
        "credit": credit_block(state, profile),
        "compliance": {
            "paused": bool(modes.get("paused")),
            "kyc": state["borrower"]["kycStatus"],
            "screened": state["compliance"]["screenedAt"] is not None,
            "hits": len(state["compliance"]["hits"]),
            "blockedLenders": [o["id"] for o in state["offers"] if o.get("blocked")],
            "blockedMerchants": state["compliance"]["blockedMerchants"],
            "approvedLenders": sum(1 for o in state["offers"] if o.get("kycStatus") == compliance.KYC_OK and not o.get("blocked")),
            "policies": compliance.POLICIES,
        },
        "eligibility": verification.ELIGIBILITY_RULES,
        "facility": {
            "status": fac["status"] if fac["status"] != "NOT_OPEN" else "READY_TO_FUND",
            "borrowingBase": money(fac["borrowingBaseMinor"]),
            "funded": money(funded),
            "outstanding": money(debt_outstanding(state, now)),
            "spent": money(fac["spentMinor"]),
            "reserved": money(fac["reservedMinor"]),
            "remaining": money(available_credit(state) if fac["status"] == "ACTIVE" else 0),
            "feeTotal": money(fee_total),
            "lenderDue": money(lender_due),
            "lenderPaid": money(fac["lenderPaidMinor"]),
            "customerPaid": money(fac["customerPaidMinor"]),
            "operatorRemainder": money(fac["operatorPaidMinor"]),
            "operatorProjected": money(max(0, rec["faceValueMinor"] - lender_due) if state["allocations"] else max(0, rec["faceValueMinor"] - fac["requestedMinor"])),
            "undrawnReturned": money(fac["undrawnReturnedMinor"]),
            "repaymentAmount": money(fac["customerPaidMinor"] or rec["faceValueMinor"]),
            "chain": chain.get("label") or ("Arc · simulated" if modes["arc"] == "simulated" else "Arc"),
            "contract": chain.get("facility"),
        },
        "offers": [
            {
                "lender": o["id"],
                "aprBps": o["aprBps"],
                "apr": f"{o['aprBps'] / 100:.2f}%",
                "available": money(o["availableMinor"]),
                "selected": o["selected"],
                "eligible": o["eligible"],
                "rejection": o["rejection"],
                "score": o["riskTier"],
                "minBuyerScore": o["minBuyerScore"],
                "maxTenorDays": o["maxTenorDays"],
                "maxAdvanceRateBps": o["maxAdvanceRateBps"],
                "fill": ({
                    "principal": money(selected[o["id"]]["principalMinor"]),
                    "txHash": selected[o["id"]].get("txHash"),
                    "explorerUrl": selected[o["id"]].get("explorerUrl"),
                    "viaAqua": selected[o["id"]].get("viaAqua", False),
                    "strategyHash": selected[o["id"]].get("strategyHash"),
                    "notes": money(int(selected[o["id"]].get("notesMinor") or 0)),
                } if o["id"] in selected else None),
            }
            for o in state["offers"]
        ],
        "allocations": [
            {
                "lender": a["lender"],
                "principal": money(a["principalMinor"]),
                "aprBps": a["aprBps"],
                "fee": money(a.get("feeMinor", 0)),
                "repayment": money(a["dueMinor"]),
                "paid": money(a["paidMinor"]),
                "txHash": a.get("txHash"),
                "viaAqua": a.get("viaAqua", False),
            }
            for a in state["allocations"]
        ],
        "agents": agents,
        "spends": spends,
        "events": events,
        "updatedAt": iso(now),
    }
