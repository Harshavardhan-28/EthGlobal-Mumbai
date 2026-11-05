"""An ADK agent that prepares, but never executes, ATS issuance."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from google.adk.agents import Agent
from dotenv import load_dotenv


for directory in Path(__file__).resolve().parents:
    candidate = directory / ".env"
    if candidate.is_file():
        load_dotenv(candidate)
        break


def prepare_ats_issuance(
    receivable_id: str,
    issuer_account_id: str,
    buyer_account_id: str,
    face_value_usdc: str,
    due_date: str,
    isin: str,
) -> dict[str, object]:
    """Validate a GST-verified receivable and return a wallet-review payload.

    This tool deliberately does not connect a wallet, sign a Hedera transaction,
    or move funds. The issuer must review and submit the ATS transaction.
    """

    required = {
        "receivable_id": receivable_id,
        "issuer_account_id": issuer_account_id,
        "buyer_account_id": buyer_account_id,
        "face_value_usdc": face_value_usdc,
        "due_date": due_date,
        "isin": isin,
    }
    missing = [name for name, value in required.items() if not value or not value.strip()]
    if missing:
        return {"ok": False, "error": f"Missing required issuance fields: {', '.join(missing)}"}

    try:
        value = float(face_value_usdc)
        maturity = date.fromisoformat(due_date)
    except ValueError:
        return {"ok": False, "error": "face_value_usdc must be numeric and due_date must be YYYY-MM-DD."}
    if value <= 0:
        return {"ok": False, "error": "face_value_usdc must be positive."}
    if maturity <= date.today():
        return {"ok": False, "error": "due_date must be in the future."}

    return {
        "ok": True,
        "action": "ISSUER_WALLET_REVIEW_REQUIRED",
        "network": "hedera:testnet",
        "asset_type": "single-unit receivable bond",
        "receivable": {
            "id": receivable_id,
            "issuer": issuer_account_id,
            "buyer": buyer_account_id,
            "face_value_usdc": face_value_usdc,
            "due_date": due_date,
            "isin": isin,
        },
        "next_step": "Connect the issuer wallet and submit the ATS Bond.create request after reviewing its transaction.",
    }


POLICY_EXPLANATIONS = {
    "AGENT_BUDGET_EXCEEDED": "The request is larger than what remains of this agent's own sub-budget. Sub-budgets are reservations of the shared line, so one agent cannot consume another's allocation.",
    "GLOBAL_CREDIT_LIMIT_EXCEEDED": "The request is larger than the facility's remaining credit. The funded line is the hard ceiling for the whole agent team, however budgets are split.",
    "TRANSACTION_LIMIT_EXCEEDED": "The single payment exceeds this agent's per-transaction limit.",
    "DAILY_LIMIT_EXCEEDED": "This payment would take the agent past its daily spending limit.",
    "MERCHANT_NOT_ALLOWED": "The merchant is not on this agent's allowlist.",
    "STALE_RECEIVABLE": "The receivable's verification has expired, so it can no longer support new borrowing.",
    "BORROWING_BASE_EXCEEDED": "Debt would exceed the advance rate applied to the verified invoice.",
    "DUPLICATE_RECEIVABLE": "This invoice already backs another facility and cannot be financed twice.",
}


def explain_policy_decision(code: str) -> dict[str, object]:
    """Explain a deterministic credit-policy decision code in plain language.

    The explanation is read-only: it cannot change, approve, or override the decision.
    """
    key = code.strip().upper()
    return {"code": key, "explanation": POLICY_EXPLANATIONS.get(key, "Unknown policy code."), "can_override": False}


root_agent = Agent(
    name="working_capital_coordinator",
    model=os.getenv("GOOGLE_ADK_MODEL", "gemini-2.5-flash"),
    instruction=(
        "You coordinate the working-capital prototype. You may prepare a Hedera ATS "
        "receivable issuance payload only after GST verification. You must never claim "
        "that a token exists without a Hedera transaction ID, and you must never execute, "
        "sign, or request an autonomous ATS issuance. Use prepare_ats_issuance for validation. "
        "Credit decisions are made by the deterministic engine and the Arc contract; you may explain "
        "them with explain_policy_decision but never approve, change, or override a limit."
    ),
    tools=[prepare_ats_issuance, explain_policy_decision],
)
