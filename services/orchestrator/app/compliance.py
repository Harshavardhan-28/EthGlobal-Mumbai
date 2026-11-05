"""Compliance gates: KYC/KYB, sanctions screening, lender allowlist, system pause.

The screening list here is a labelled demo list, not a real sanctions list. In
production `screen` would call a screening provider; the gates and the codes stay
the same. Only hashes and amounts ever leave this service for a public ledger:
`public_payload` strips anything that identifies a party.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

DENYLIST_PATH = Path(os.environ.get("WC_DENYLIST_PATH") or Path(__file__).resolve().parents[3] / "packages" / "shared" / "fixtures" / "screening-demo.json")
KYC_OK = "VERIFIED"
# Anything that looks like a GSTIN, PAN or email must never reach HCS or Arc.
_IDENTIFYING = re.compile(r"[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]|[A-Z]{5}[0-9]{4}[A-Z]|[^@\s]+@[^@\s]+")
PUBLIC_KEYS = {"canonicalId", "faceValueMinor", "evidenceTier", "creditTier", "amountMinor", "toLendersMinor", "toOperatorMinor", "intent", "agent", "code", "status", "outcome", "score"}


def load_denylist(path: Path = DENYLIST_PATH) -> dict[str, Any]:
    return json.loads(path.read_text())


def _norm(value: str) -> str:
    return " ".join(str(value).strip().upper().split())


def screen(subjects: list[dict[str, str]], denylist: dict[str, Any]) -> list[dict[str, str]]:
    """Exact match on normalised tax IDs, wallets and names.

    Each subject is {role, ref, type, value}: `ref` is our own handle (a lender ID, a
    merchant name) and `value` is what gets screened. Returns one hit per match."""
    listed = {(entry["type"], _norm(entry["value"])): entry for entry in denylist["entries"]}
    hits = []
    for subject in subjects:
        if not subject.get("value"):
            continue
        entry = listed.get((subject["type"], _norm(subject["value"])))
        if entry:
            hits.append({"role": subject["role"], "ref": subject.get("ref", subject["role"]), "type": subject["type"], "list": entry["list"]})
    return hits


def subjects_for(state: dict[str, Any]) -> list[dict[str, str]]:
    """Everyone a facility touches: borrower, seller, buyer, lenders, merchants."""
    rec = state["receivable"]
    out = [
        {"role": "borrower", "ref": "borrower", "type": "name", "value": state["borrower"]["name"]},
        {"role": "seller", "ref": "seller", "type": "taxId", "value": rec.get("sellerTaxId") or ""},
        {"role": "buyer", "ref": "buyer", "type": "taxId", "value": rec.get("buyerTaxId") or ""},
        {"role": "buyer", "ref": "buyer", "type": "name", "value": rec["buyer"]},
    ]
    for offer in state["offers"]:
        out.append({"role": "lender", "ref": offer["id"], "type": "name", "value": offer.get("legalName", offer["id"])})
        if offer.get("wallet"):
            out.append({"role": "lender", "ref": offer["id"], "type": "wallet", "value": offer["wallet"]})
    merchants = {m for agent in state["agents"] for m in agent["merchants"]} | set(state["lead"]["merchants"])
    for merchant in sorted(merchants):
        out.append({"role": "merchant", "ref": merchant, "type": "name", "value": merchant})
    return out


def kyc_error(borrower: dict[str, Any]) -> str | None:
    if borrower.get("kycStatus") != KYC_OK:
        return "KYC_REQUIRED"
    return None


def lender_error(lender: dict[str, Any], blocked: bool) -> str | None:
    if blocked:
        return "LENDER_BLOCKED"
    if lender.get("kycStatus") != KYC_OK:
        return "LENDER_NOT_APPROVED"
    return None


def public_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep only allowlisted, non-identifying fields for HCS and other public records."""
    clean = {k: v for k, v in payload.items() if k in PUBLIC_KEYS}
    for key, value in clean.items():
        if isinstance(value, str) and key != "canonicalId" and _IDENTIFYING.search(value):
            raise ValueError(f"Refusing to publish identifying data in {key}.")
    return clean


POLICIES = [
    {"id": "kyc", "name": "KYC / KYB", "control": "Borrowers and lenders must be verified before they can borrow or fund.", "enforced": "Engine (verify, quote) and Arc (approved lenders only)"},
    {"id": "sanctions", "name": "Sanctions screening", "control": "Borrower, buyer, lenders, merchants and wallets are screened before verify and funding. A match blocks the action.", "enforced": "Engine (demo list) and Arc (blocked addresses)"},
    {"id": "allowlist", "name": "Lender and merchant allowlists", "control": "Only approved lenders can fund; agents can only pay allowlisted merchants.", "enforced": "Engine and Arc"},
    {"id": "eligibility", "name": "Eligibility", "control": "Business borrowers and freelancers only, business buyers only, no personal loans.", "enforced": "Engine"},
    {"id": "limits", "name": "Credit limits", "control": "Credit score tiers cap the limit and advance rate; new borrowers start small.", "enforced": "Engine; the Arc borrowing base is attested from it"},
    {"id": "overdue", "name": "Overdue and default", "control": "7 days after the due date the facility is overdue; 30 days after, defaulted. Spending stops; repayments still pay lenders first.", "enforced": "Engine and Arc (anyone can trigger once the time has passed)"},
    {"id": "dispute", "name": "Disputes", "control": "A disputed invoice freezes new spending until it is resolved.", "enforced": "Engine and Arc"},
    {"id": "pause", "name": "Emergency pause", "control": "An admin can pause new verification, funding and spending. Repayments keep working.", "enforced": "Engine (admin token) and Arc (owner)"},
    {"id": "privacy", "name": "Data minimisation", "control": "Tax IDs, IRNs and names stay in the private database. Only the invoice hash, amounts and statuses go to Hedera and Arc.", "enforced": "Engine (public payload filter)"},
    {"id": "records", "name": "Record keeping", "control": "Every decision is an append-only domain event, mirrored to an HCS audit topic in live mode.", "enforced": "Store and HCS"},
]
