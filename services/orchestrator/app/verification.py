"""Invoice verification and eligibility.

Pure, deterministic checks - no network calls, no AI. They answer two questions:

1. Is this invoice one we are allowed to finance? (eligibility rules)
2. How much proof backs it? (evidence tier, which caps the advance rate)

Indian invoices
    GSTIN format, state code and check digit for the seller and the buyer, then an
    offline match of the e-invoice IRN. NIC's e-invoice sandbox describes the IRN as a
    SHA-256 hash of supplier GSTIN + financial year + document type + document number.
    A real IRN that does not match our recomputation is *downgraded*, not rejected,
    because the exact concatenation used by the IRP has not been confirmed against a
    live IRN. Confirming that an IRN is registered needs an IRP/GSP API account and is
    outside this prototype.

Foreign invoices
    The seller tax ID, buyer tax ID and invoice number must be present. We cannot check
    foreign tax IDs or invoice numbers against a government system, so these get a
    lower advance cap. The canonical ID still locks the invoice against double financing.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timezone
from typing import Any

GSTIN_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
# 01-38 are states and union territories, 97 is "other territory", 99 is centre jurisdiction.
GSTIN_STATE_CODES = {f"{n:02d}" for n in range(1, 39)} | {"97", "99"}
IRN_RE = re.compile(r"^[0-9a-f]{64}$")

ELIGIBLE_BORROWERS = ("COMPANY", "PARTNERSHIP", "SOLE_PROPRIETOR")
MAX_TERM_DAYS = 120

# Evidence tier -> highest advance rate it can support (basis points).
TIERS: dict[str, dict[str, Any]] = {
    "GST_E_INVOICE": {"label": "GST e-invoice verified", "advanceCapBps": 8000, "rank": 4},
    "GSTIN_VERIFIED": {"label": "GSTIN verified · no matching e-invoice", "advanceCapBps": 5000, "rank": 3},
    "FOREIGN_INVOICE": {"label": "Foreign invoice · tax IDs and invoice number", "advanceCapBps": 5000, "rank": 2},
    "DECLARED": {"label": "Declared · freelancer without GSTIN", "advanceCapBps": 3000, "rank": 1},
}

ELIGIBILITY_RULES = [
    {"id": "borrower", "rule": "The borrower is a company, partnership or sole proprietor (freelancer). Personal loans are not offered."},
    {"id": "kyc", "rule": "The borrower has passed KYC (people) or KYB (businesses)."},
    {"id": "owner", "rule": "Every agent belongs to that verified borrower. An agent cannot borrow on its own."},
    {"id": "b2b", "rule": "The buyer is a business with a tax ID. Invoices to consumers are not financed."},
    {"id": "self", "rule": "Seller and buyer are different legal entities (a different PAN in India)."},
    {"id": "document", "rule": "The document is a tax invoice (INV). Credit and debit notes are not financed."},
    {"id": "term", "rule": f"The invoice is not yet due and is due within {MAX_TERM_DAYS} days."},
    {"id": "india", "rule": "Indian invoices need a valid buyer GSTIN. A seller without a GSTIN is allowed only for sole proprietors, at the lowest tier."},
    {"id": "foreign", "rule": "Foreign invoices need the seller tax ID, buyer tax ID and invoice number."},
    {"id": "sanctions", "rule": "No party, lender, merchant or wallet matches the sanctions screening list."},
    {"id": "credit", "rule": "The borrower's credit tier is not BLOCKED. New borrowers start with a small limit."},
    {"id": "unique", "rule": "The invoice has never backed another facility."},
]


# --------------------------------------------------------------------- GSTIN
def gstin_check_digit(first14: str) -> str:
    """Alternate 1/2 weights over base-36 values, sum quotient+remainder of /36, then (36 - sum % 36) % 36."""
    total = 0
    for i, char in enumerate(first14):
        product = GSTIN_CHARS.index(char) * (1 if i % 2 == 0 else 2)
        total += product // 36 + product % 36
    return GSTIN_CHARS[(36 - total % 36) % 36]


def gstin_error(value: str | None) -> str | None:
    """None when valid, else a stable error code."""
    gstin = (value or "").strip().upper()
    if not GSTIN_RE.match(gstin):
        return "GSTIN_FORMAT_INVALID"
    if gstin[:2] not in GSTIN_STATE_CODES:
        return "GSTIN_STATE_INVALID"
    if gstin_check_digit(gstin[:14]) != gstin[14]:
        return "GSTIN_CHECKSUM_INVALID"
    return None


def pan_of(gstin: str) -> str:
    """Characters 3-12 of a GSTIN are the holder's PAN: the same PAN is the same legal entity."""
    return gstin.strip().upper()[2:12]


# --------------------------------------------------------------------- IRN
def financial_year(day: date) -> str:
    """Indian financial year (April to March), e.g. 2026-27."""
    start = day.year if day.month >= 4 else day.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def compute_irn(supplier_gstin: str, fy: str, document_type: str, document_number: str) -> str:
    material = f"{supplier_gstin.strip().upper()}{fy}{document_type.strip().upper()}{document_number.strip()}"
    return hashlib.sha256(material.encode()).hexdigest()


def canonical_receivable_id(country: str, seller_key: str, period: str, document_type: str, invoice_number: str) -> str:
    """One ID per invoice. It does not include the amount or due date, so editing either
    cannot make the same invoice look new. For India this mirrors IRN uniqueness."""
    material = "|".join([country.strip().upper(), seller_key.strip().upper(), period, document_type.strip().upper(), invoice_number.strip().upper()])
    return "0x" + hashlib.sha256(material.encode()).hexdigest()


def invoice_period(invoice: dict[str, Any]) -> str:
    day = date.fromisoformat(invoice["invoiceDate"])
    return financial_year(day) if invoice["country"].upper() == "IN" else str(day.year)


def seller_key(invoice: dict[str, Any], borrower: dict[str, Any]) -> str:
    return (invoice.get("sellerTaxId") or "").strip() or f"borrower:{borrower['id']}"


# --------------------------------------------------------------------- verify
def _check(checks: list[dict[str, Any]], check_id: str, label: str, ok: bool, detail: str) -> bool:
    checks.append({"id": check_id, "label": label, "ok": ok, "detail": detail})
    return ok


def verify_invoice(invoice: dict[str, Any], borrower: dict[str, Any], now: int) -> dict[str, Any]:
    """Run the eligibility rules, then work out the evidence tier.

    Returns {eligible, rejection, detail, tier, label, advanceCapBps, checks, fy}.
    """
    checks: list[dict[str, Any]] = []
    today = datetime.fromtimestamp(now, tz=timezone.utc).date()

    def reject(code: str, detail: str) -> dict[str, Any]:
        return {"eligible": False, "rejection": code, "detail": detail, "tier": None, "label": "Not eligible", "advanceCapBps": 0, "checks": checks}

    country = (invoice.get("country") or "").strip().upper()
    seller = (invoice.get("sellerTaxId") or "").strip().upper()
    buyer = (invoice.get("buyerTaxId") or "").strip().upper()

    # Eligibility rules first: these reject outright.
    if not _check(checks, "borrower", "Business borrower", borrower.get("type") in ELIGIBLE_BORROWERS, f"Borrower type {borrower.get('type')}"):
        return reject("BORROWER_TYPE_INELIGIBLE", "Only companies, partnerships and sole proprietors (freelancers) can borrow. Personal loans are not offered.")
    if not _check(checks, "b2b", "Business buyer", invoice.get("buyerType") == "BUSINESS" and bool(buyer), "Buyer has a business tax ID" if buyer else "No buyer tax ID"):
        return reject("CONSUMER_INVOICE_NOT_SUPPORTED", "Only invoices to businesses with a tax ID can be financed.")
    if not _check(checks, "document", "Tax invoice", (invoice.get("documentType") or "").upper() == "INV", f"Document type {invoice.get('documentType')}"):
        return reject("DOCUMENT_TYPE_INELIGIBLE", "Only tax invoices (INV) can be financed, not credit or debit notes.")
    if not _check(checks, "amount", "Positive amount", int(invoice.get("faceValueMinor") or 0) > 0, "Face value is positive"):
        return reject("INVOICE_AMOUNT_INVALID", "The invoice amount must be positive.")
    due = date.fromisoformat(invoice["dueDate"])
    term_days = (due - today).days
    if not _check(checks, "term", "Due within limits", 0 < term_days <= MAX_TERM_DAYS, f"Due in {term_days} days"):
        return reject("INVOICE_TERM_INELIGIBLE", f"The invoice must be unpaid, not yet due and due within {MAX_TERM_DAYS} days.")
    if not (invoice.get("invoiceNumber") or "").strip():
        _check(checks, "number", "Invoice number", False, "Missing")
        return reject("INVOICE_NUMBER_REQUIRED", "The invoice number is required.")

    if country != "IN":
        if not _check(checks, "foreign_ids", "Seller and buyer tax IDs", bool(seller) and bool(buyer), f"{country or 'Unknown country'} invoice {invoice['invoiceNumber']}"):
            return reject("FOREIGN_ID_REQUIRED", "Foreign invoices need the seller tax ID, buyer tax ID and invoice number.")
        if not _check(checks, "self", "Different legal entities", seller != buyer, "Seller and buyer tax IDs differ"):
            return reject("SELF_INVOICE", "Seller and buyer are the same entity.")
        tier = "FOREIGN_INVOICE"
        _check(checks, "official", "Government lookup", False, "Not available for foreign invoices; lower advance cap applies")
        return {"eligible": True, "rejection": None, "detail": "", "tier": tier, **_tier_fields(tier), "checks": checks, "fy": str(date.fromisoformat(invoice["invoiceDate"]).year)}

    # India: GSTIN checks.
    buyer_error = gstin_error(buyer)
    if not _check(checks, "buyer_gstin", "Buyer GSTIN", buyer_error is None, buyer_error or f"{buyer[:2]}… valid format, state code and check digit"):
        return reject("BUYER_GSTIN_INVALID", f"Buyer GSTIN failed: {buyer_error}.")
    if not seller:
        if not _check(checks, "seller_gstin", "Seller GSTIN", borrower.get("type") == "SOLE_PROPRIETOR", "No seller GSTIN (allowed for freelancers below the GST threshold)"):
            return reject("SELLER_GSTIN_REQUIRED", "Companies and partnerships need a seller GSTIN.")
        tier = "DECLARED"
        return {"eligible": True, "rejection": None, "detail": "", "tier": tier, **_tier_fields(tier), "checks": checks, "fy": financial_year(date.fromisoformat(invoice["invoiceDate"]))}
    seller_error = gstin_error(seller)
    if not _check(checks, "seller_gstin", "Seller GSTIN", seller_error is None, seller_error or f"{seller[:2]}… valid format, state code and check digit"):
        return reject("SELLER_GSTIN_INVALID", f"Seller GSTIN failed: {seller_error}.")
    if not _check(checks, "self", "Different legal entities", pan_of(seller) != pan_of(buyer), "Seller and buyer have different PANs"):
        return reject("SELF_INVOICE", "Seller and buyer share a PAN, so they are the same legal entity.")

    fy = financial_year(date.fromisoformat(invoice["invoiceDate"]))
    irn = (invoice.get("irn") or "").strip().lower()
    expected = compute_irn(seller, fy, invoice["documentType"], invoice["invoiceNumber"])
    if not irn:
        _check(checks, "irn", "E-invoice IRN", False, "No IRN supplied (seller may be below the e-invoice threshold)")
        tier = "GSTIN_VERIFIED"
    elif not IRN_RE.match(irn):
        _check(checks, "irn", "E-invoice IRN", False, "IRN is not a 64-character hash")
        tier = "GSTIN_VERIFIED"
    elif irn != expected:
        _check(checks, "irn", "E-invoice IRN", False, "IRN does not match seller GSTIN, financial year, type and number; verify on the IRP")
        tier = "GSTIN_VERIFIED"
    else:
        _check(checks, "irn", "E-invoice IRN", True, f"Matches seller GSTIN + {fy} + INV + {invoice['invoiceNumber']}")
        tier = "GST_E_INVOICE"
    return {"eligible": True, "rejection": None, "detail": "", "tier": tier, **_tier_fields(tier), "checks": checks, "fy": fy}


def _tier_fields(tier: str) -> dict[str, Any]:
    return {"label": TIERS[tier]["label"], "advanceCapBps": TIERS[tier]["advanceCapBps"]}
