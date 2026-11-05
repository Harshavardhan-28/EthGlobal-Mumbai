"""Orchestration: engine decisions + chain execution + durable progress + reconciliation.

Each external step is persisted before and after it runs, so an interrupted flow
resumes from the last recorded step. Spending is two-phase:

    PENDING --reserve (Arc)--> RESERVED --pay (Hedera x402)--> PAYING
        --finalized receipt--> commit (Arc) --> SETTLED
        --confirmed failure or timeout--> release (Arc) --> RELEASED

A payment whose outcome is unknown is never retried blindly: reconciliation asks
the Hedera gateway (durable idempotency ledger) whether it settled, then commits
or releases exactly once.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from . import compliance, credit, engine, verification
from .adapters import ArcAdapter, GatewayFailed, GatewayPolicyError, GatewayUncertain, HederaAdapter
from .engine import PolicyError, TransitionError, usd
from .store import Store
from .view import build_view

DEMO_ID = "retailco-net30"
PAUSE_KEY = "compliance.paused"


def _now() -> int:
    return int(time.time())


class Orchestrator:
    def __init__(self, store: Store | None = None, hedera: HederaAdapter | None = None, arc: ArcAdapter | None = None, clock: Callable[[], int] = _now) -> None:
        self.store = store or Store()
        self.hedera = hedera or HederaAdapter()
        self.arc = arc or ArcAdapter()
        self.clock = clock
        self.fixture = engine.load_fixture()
        self.denylist = compliance.load_denylist()

    # ------------------------------------------------------------ compliance + credit
    def paused(self) -> bool:
        return self.store.setting(PAUSE_KEY, "false") == "true"

    def set_paused(self, paused: bool, reason: str = "") -> dict[str, Any]:
        self.store.set_setting(PAUSE_KEY, "true" if paused else "false")
        return {"paused": paused, "reason": reason}

    def _guard(self, action: str) -> None:
        """The compliance pause stops new risk. Repayment and reconciliation never stop."""
        if self.paused():
            raise PolicyError("SYSTEM_PAUSED", f"New {action} is paused by compliance. Repayments still work.")

    def profile(self, borrower_id: str) -> dict[str, Any]:
        stored = self.store.profile(borrower_id)
        if stored:
            return stored
        if borrower_id == self.fixture["borrower"]["id"]:
            return {"borrowerId": borrower_id, **self.fixture["creditHistory"]}
        return credit.new_profile(borrower_id)

    def credit_view(self, borrower_id: str) -> dict[str, Any]:
        profile = self.profile(borrower_id)
        return {"profile": profile, **credit.terms(profile, 0, 10_000, 10_000), "tiers": [{"name": n, "minScore": m, "limitUsd": lim, "advanceCapBps": a} for n, m, lim, a in credit.TIERS]}

    def _audit(self, event_type: str, payload: dict[str, Any], key: str) -> dict[str, Any]:
        """Only hashes, amounts and statuses go to HCS (data minimisation)."""
        return self.hedera.audit(event_type, compliance.public_payload(payload), key=key)

    # ------------------------------------------------------------ plumbing
    def modes(self) -> dict[str, Any]:
        arc_mode = "simulated"
        arc_label = "Arc · simulated"
        if self.arc.live:
            try:
                cfg = self.arc.config()
                arc_mode, arc_label = cfg["mode"], cfg["label"]
            except Exception:  # noqa: BLE001 - gateway down is shown in the UI, not raised
                arc_mode, arc_label = "unavailable", "Arc gateway unavailable"
        return {"hedera": self.hedera.mode, "hederaAudit": "live" if self.hedera.audit_live else "simulated", "arc": arc_mode, "arcLabel": arc_label}

    def _activity(self, title: str, detail: str, status: str, chain: str, tx_id: str | None = None, explorer: str | None = None) -> dict[str, Any]:
        return {"id": str(uuid4()), "title": title, "detail": detail, "status": status, "chain": chain, "txId": tx_id, "explorerUrl": explorer, "time": engine.iso(self.clock())}

    def load(self, aggregate_id: str = DEMO_ID) -> tuple[dict[str, Any], int]:
        loaded = self.store.load(aggregate_id)
        if loaded is None:
            if aggregate_id != DEMO_ID:
                raise PolicyError("NOT_FOUND", f"No receivable {aggregate_id}.")
            return self.create(DEMO_ID)
        return loaded

    def create(self, aggregate_id: str, run: int = 1) -> tuple[dict[str, Any], int]:
        state = engine.new_aggregate(aggregate_id, self.fixture, self.clock(), run)
        if not self.store.register_receivable(state["receivable"]["canonicalId"], aggregate_id):
            raise PolicyError("DUPLICATE_RECEIVABLE", "This invoice already backs another receivable.")
        ids = self.hedera.agent_ids()
        for agent in state["agents"]:
            agent["uaid"] = ids.get(agent["identity"])
        state["lead"]["uaid"] = ids.get("lead")
        activity = [self._activity("Invoice registered", f"{state['receivable']['buyer']} {state['receivable']['invoiceNumber']} · {usd(state['receivable']['faceValueMinor'])} · canonical ID locks it against double financing", "pending", "Working capital desk")]
        version = self.store.save(state, 0, [{"type": "receivable.drafted", "canonicalId": state["receivable"]["canonicalId"]}], activity)
        return state, version

    def view(self, aggregate_id: str = DEMO_ID) -> dict[str, Any]:
        with self.store.lock:
            state, _ = self.load(aggregate_id)
            profile = self.profile(state["borrower"]["id"])
            return build_view(state, self.store.activity(aggregate_id), {**self.modes(), "paused": self.paused()}, self.clock(), profile)

    def _commit(self, state: dict[str, Any], version: int, events: list[dict[str, Any]], activity: list[dict[str, Any]]) -> int:
        return self.store.save(state, version, events, activity)

    def reset(self, aggregate_id: str = DEMO_ID) -> dict[str, Any]:
        with self.store.lock:
            loaded = self.store.load(aggregate_id)
            run = (loaded[0]["run"] + 1) if loaded else 1
            self.store.delete_aggregate(aggregate_id)
            # The demo borrower's credit history returns to its starting point too.
            self.store.save_profile({"borrowerId": self.fixture["borrower"]["id"], **self.fixture["creditHistory"]})
            self.set_paused(False)
            self.create(aggregate_id, run)
            return self.view(aggregate_id)

    # ------------------------------------------------------------ verify
    def verify(self, aggregate_id: str, irn: str | None = None) -> dict[str, Any]:
        """GST verification, eligibility, screening and credit scoring, then accept.

        No buyer action is needed. Without a real IRN the demo stands in for the
        government IRP and issues one in the IRP's documented format, labelled simulated."""
        self._guard("verification")
        with self.store.lock:
            state, version = self.load(aggregate_id)
            now = self.clock()
            rec = state["receivable"]
            irn_simulated = False
            if irn:
                rec["irn"] = irn.strip().lower()
            elif rec["country"] == "IN" and rec.get("sellerTaxId") and not rec.get("irn"):
                rec["irn"] = verification.compute_irn(rec["sellerTaxId"], verification.financial_year(datetime.fromtimestamp(now, tz=timezone.utc).date()), rec["documentType"], rec["invoiceNumber"])
                irn_simulated = True
            result = verification.verify_invoice(engine.invoice_of(rec), state["borrower"], now)
            result["irnSimulated"] = irn_simulated
            hits = compliance.screen(compliance.subjects_for(state), self.denylist)
            profile = self.profile(state["borrower"]["id"])
            terms = credit.terms(profile, rec["faceValueMinor"], rec["requestedAdvanceRateBps"], result["advanceCapBps"] or 0)
            try:
                events = engine.verify(state, now, result, terms, hits)
            except PolicyError as exc:
                act = self._activity("Invoice not eligible", f"{exc.code} · {exc.detail}", "danger", "Deterministic verification")
                self._commit(state, version, [{"type": "receivable.rejected", "code": exc.code}], [act])
                raise
            audit = self._audit("receivable.verified", {"canonicalId": rec["canonicalId"], "faceValueMinor": rec["faceValueMinor"], "evidenceTier": result["tier"], "creditTier": terms["tier"], "score": terms["score"]}, key=f"{state['chainKey']}:verify")
            rec["verification"]["hcs"] = audit
            passed = sum(1 for c in result["checks"] if c["ok"])
            irn_note = "IRN simulated" if irn_simulated else "IRN supplied"
            detail = f"{result['label']} · {passed}/{len(result['checks'])} checks · {irn_note} · credit score {terms['score']} ({terms['tier'].title()}) · advance {terms['advanceRateBps'] / 100:.0f}% · borrowing base {usd(terms['borrowingBaseMinor'])}"
            chain = "Hedera HCS · testnet" if audit.get("transactionId") else "GST rules · HCS audit simulated"
            act = self._activity("GST invoice verified", detail, "success", chain, audit.get("transactionId"), audit.get("explorerUrl"))
            self._commit(state, version, events, [act])
            return {"event": act, "verification": result, "credit": terms}

    def tokenize(self, aggregate_id: str, ats_transaction_id: str | None = None, token_id: str | None = None) -> dict[str, Any]:
        """Live: the issuer submits the ATS bond in their wallet; we verify it on Mirror Node.
        Simulated: a clearly labelled placeholder - never presented as a real token."""
        with self.store.lock:
            state, version = self.load(aggregate_id)
            tx = ats_transaction_id or self.hedera.ats_tx
            if tx and self.hedera.mode == "live":
                verified = self.hedera.verify_ats(tx)
                token = {"tokenId": token_id or self.hedera.ats_token or verified.get("entityId") or tx, "transactionId": tx, "explorerUrl": verified.get("explorerUrl"), "simulated": False, "verifiedAt": verified.get("consensusTimestamp")}
                act = self._activity("Receivable tokenized on Hedera ATS", f"Single-unit USDC receivable bond {token['tokenId']} verified on Mirror Node", "success", "Hedera ATS · testnet", tx, verified.get("explorerUrl"))
            else:
                token = {"tokenId": "Simulated ATS token (unverified)", "simulated": True}
                act = self._activity("Tokenization simulated", "Submit the ATS bond from the issuer wallet and set HEDERA_ATS_ISSUANCE_TX_ID to verify a real token", "success", "Local demo · ATS simulated")
            events = engine.tokenize(state, self.clock(), token)
            self._commit(state, version, events, [act])
            return {"event": act}

    # ------------------------------------------------------------ fund
    def fund(self, aggregate_id: str) -> dict[str, Any]:
        self._guard("funding")
        with self.store.lock:
            state, version = self.load(aggregate_id)
            now = self.clock()
            if state["receivable"]["status"] == "TOKENIZED":
                plan = engine.quote(state, now)
                events = engine.begin_funding(state, plan)
                rejected = ", ".join(f"{r['lender']} ({r['reason']})" for r in plan["rejected"]) or "none"
                act = self._activity("Lender offers matched", f"Cheapest eligible first: {', '.join(a['lender'] for a in plan['allocations'])} · ineligible: {rejected}", "pending", "Deterministic solver")
                state["fundingPlan"] = plan
                version = self._commit(state, version, events, [act])
            elif state["receivable"]["status"] != "FUNDING":
                raise TransitionError(f"Cannot fund while the receivable is {state['receivable']['status']}.")

            rec = state["receivable"]
            if self.arc.live and not state["facility"].get("openTx"):
                opened = self.arc.open({
                    "facilityKey": state["chainKey"],
                    "receivableKey": f"{rec['canonicalId']}#{state['run']}",
                    "faceValueMinor": str(rec["faceValueMinor"]),
                    # The contract's base is face x rate; this rate makes it equal the engine's
                    # base, including when the credit limit (not the rate) is what binds.
                    "advanceRateBps": max(1, state["facility"]["borrowingBaseMinor"] * engine.BPS // rec["faceValueMinor"]),
                    "maturity": rec["dueAt"],
                    "verificationExpiry": rec["verificationExpiresAt"],
                    "hederaTxId": (rec.get("token") or {}).get("transactionId") or rec["canonicalId"],
                    "hcsSequence": ((rec["verification"] or {}).get("hcs") or {}).get("sequence") or 0,
                }, key=f"{state['chainKey']}:open")
                state["facility"]["openTx"] = opened.get("txHash")
                act = self._activity("Arc facility opened", f"Coordinator attestation accepted · borrowing base {usd(state['facility']['borrowingBaseMinor'])}", "success", self.arc.config()["label"], opened.get("txHash"), opened.get("explorerUrl"))
                version = self._commit(state, version, [{"type": "facility.opened", "txHash": opened.get("txHash")}], [act])

            for allocation in state["fundingPlan"]["allocations"]:
                if any(a["lender"] == allocation["lender"] for a in state["allocations"]):
                    continue  # already filled before an interruption
                fill = self.arc.fund({"facilityKey": state["chainKey"], "lenderId": allocation["lender"], "principalMinor": str(allocation["principalMinor"]), "aprBps": allocation["aprBps"], "minTermDays": allocation["minTermDays"]}, key=f"{state['chainKey']}:fund:{allocation['lender']}")
                evidence = {"txHash": fill.get("txHash"), "explorerUrl": fill.get("explorerUrl"), "viaAqua": bool(fill.get("viaAqua")), "strategyHash": fill.get("strategyHash"), "notesMinor": fill.get("notesMinor")}
                events = engine.record_fill(state, self.clock(), allocation, evidence)
                how = "official Aqua SwapVM fill" if evidence["viaAqua"] else ("direct USDC funding" if self.arc.live else "simulated fill")
                act = self._activity(f"{allocation['lender']} funded {usd(allocation['principalMinor'])}", f"{allocation['aprBps'] / 100:.2f}% APR · {how} · lender notes issued", "success", self.arc.config()["label"], fill.get("txHash"), fill.get("explorerUrl"))
                version = self._commit(state, version, events, [act])

            events = engine.activate(state, self.arc.config())
            events += engine.assign_budgets(state)
            if self.arc.live:
                self.arc.budgets({
                    "facilityKey": state["chainKey"],
                    "budgets": [{"agentIdentity": a["identity"], "allocationMinor": str(a["allocationMinor"]), "perTxMinor": str(a["perTxMinor"]), "dailyMinor": str(a["dailyMinor"]), "merchants": a["merchants"]} for a in state["agents"]],
                    "leadMerchant": state["lead"]["merchant"],
                }, key=f"{state['chainKey']}:budgets")
            unused = [o["id"] for o in state["offers"] if not o["selected"] and o["eligible"]]
            act = self._activity("Facility funded", f"{usd(state['facility']['fundedMinor'])} matched across {len(state['allocations'])} lenders · {', '.join(unused) + ' not needed' if unused else 'all eligible offers used'} · budgets reserved for {len(state['agents'])} agents", "success", self.arc.config()["label"])
            self._commit(state, version, events, [act])
            self.store.save_profile(credit.record_funding(self.profile(state["borrower"]["id"]), state["facility"]["fundedMinor"]))
            return {"event": act}

    # ------------------------------------------------------------ overdue, default, dispute
    def _arc_status(self, state: dict[str, Any], action: str, extra: dict[str, Any] | None = None) -> str | None:
        """Mirror a status change on Arc. A chain that disagrees (e.g. its clock says not
        overdue yet) is recorded, never allowed to undo the engine's decision."""
        if not self.arc.live:
            return None
        try:
            return self.arc.status({"facilityKey": state["chainKey"], "action": action, **(extra or {})}, key=f"{state['chainKey']}:status:{action}").get("txHash")
        except GatewayPolicyError as exc:
            return f"chain:{exc.code}"

    def assess(self, aggregate_id: str = DEMO_ID) -> dict[str, Any]:
        """Check the due date. Anyone may call it, like the contract's markOverdue."""
        with self.store.lock:
            state, version = self.load(aggregate_id)
            events = engine.assess(state, self.clock())
            if not events:
                return {"event": None, "status": state["receivable"]["status"]}
            kind = events[0]["type"]
            if kind == "facility.defaulted":
                self.store.save_profile(credit.record_default(self.profile(state["borrower"]["id"])))
                tx = self._arc_status(state, "default")
                act = self._activity("Facility defaulted", f"{events[0]['daysLate']} days past due · spending closed · borrower blocked from new credit · repayments still go to lenders first", "danger", self.arc.config()["label"], tx)
            else:
                tx = self._arc_status(state, "overdue")
                act = self._activity("Invoice overdue", f"{events[0]['daysLate']} days past due · agent spending frozen · lenders still paid first when money arrives", "danger", self.arc.config()["label"], tx)
            self._audit(kind, {"canonicalId": state["receivable"]["canonicalId"], "status": state["receivable"]["status"]}, key=f"{state['chainKey']}:{kind}")
            self._commit(state, version, events, [act])
            return {"event": act, "status": state["receivable"]["status"]}

    def dispute(self, aggregate_id: str, reason: str) -> dict[str, Any]:
        with self.store.lock:
            state, version = self.load(aggregate_id)
            events = engine.dispute(state, self.clock(), reason)
            tx = self._arc_status(state, "dispute")
            act = self._activity("Invoice disputed", f"Spending frozen until resolved · {reason.strip()[:120]}", "danger", self.arc.config()["label"], tx)
            self._commit(state, version, events, [act])
            return {"event": act}

    def resolve_dispute(self, aggregate_id: str, upheld: bool) -> dict[str, Any]:
        with self.store.lock:
            state, version = self.load(aggregate_id)
            events = engine.resolve_dispute(state, self.clock(), upheld)
            if upheld:
                self.store.save_profile(credit.record_default(self.profile(state["borrower"]["id"])))
            tx = self._arc_status(state, "resolve", {"upheld": upheld})
            title, detail = ("Dispute upheld", "The invoice is not owed · the seller is in default under recourse · lenders paid first from any recovery") if upheld else ("Dispute cleared", f"The invoice stands · facility is {state['receivable']['status'].lower()} again")
            act = self._activity(title, detail, "danger" if upheld else "success", self.arc.config()["label"], tx)
            self._commit(state, version, events, [act])
            return {"event": act}

    # ------------------------------------------------------------ spend
    def create_intent(self, aggregate_id: str, agent: str, merchant: str, amount_minor: int) -> dict[str, Any]:
        self._guard("spending")
        self.assess(aggregate_id)
        with self.store.lock:
            state, version = self.load(aggregate_id)
            intent, events = engine.create_intent(state, agent, merchant, amount_minor, self.clock())
            activity = []
            if intent["status"] == "REJECTED":
                activity.append(self._rejection(state, intent))
            self._commit(state, version, events, activity)
            return intent

    def _rejection(self, state: dict[str, Any], intent: dict[str, Any]) -> dict[str, Any]:
        """Policy rejections are decided by the engine; in gateway mode the Arc contract is asked too."""
        chain_note = "Arc policy check · simulated"
        if self.arc.live:
            try:
                # Dry run: eth_call simulation of reserveSpend, never a transaction.
                self.arc.reserve({"facilityKey": state["chainKey"], "intentKey": intent["chainKey"], "agentIdentity": intent["agent"], "merchant": intent["merchant"], "amountMinor": str(intent["amountMinor"]), "ttlSeconds": 300, "dryRun": True}, key=f"{intent['chainKey']}:probe")
                chain_note = "Arc contract accepted — engine and contract disagree"
            except GatewayPolicyError as exc:
                intent["evidence"]["contractCode"] = exc.code
                chain_note = f"Arc contract also rejects ({exc.code})" if exc.code == intent["reason"] else f"Arc contract rejects with {exc.code}"
        audit = self._audit("spend.rejected", {"intent": intent["id"], "agent": intent["agent"], "amountMinor": intent["amountMinor"], "code": intent["reason"]}, key=f"{intent['chainKey']}:rejected")
        return self._activity("Payment rejected by policy", f"{intent['reason']} · {intent['detail']}", "danger", chain_note, audit.get("transactionId"), audit.get("explorerUrl"))

    def execute_intent(self, aggregate_id: str, intent_id: str) -> dict[str, Any]:
        self._guard("spending")
        with self.store.lock:
            state, version = self.load(aggregate_id)
            intent = engine.find_intent(state, intent_id)
            if intent["status"] == "REJECTED":
                raise PolicyError(intent["reason"], intent.get("detail", ""))
            if intent["status"] == "PENDING":
                reserve_tx = None
                if self.arc.live:
                    try:
                        r = self.arc.reserve({"facilityKey": state["chainKey"], "intentKey": intent["chainKey"], "agentIdentity": intent["agent"], "merchant": intent["merchant"], "amountMinor": str(intent["amountMinor"]), "ttlSeconds": 300}, key=f"{intent['chainKey']}:reserve")
                        reserve_tx = r.get("txHash")
                    except GatewayPolicyError as exc:
                        intent["status"], intent["reason"], intent["detail"] = "REJECTED", exc.code, f"Arc contract rejected the reservation ({exc.code})."
                        self._commit(state, version, [{"type": "spend.rejected", "intentId": intent_id, "code": exc.code}], [self._activity("Payment rejected on Arc", intent["detail"], "danger", self.arc.config()["label"])])
                        raise PolicyError(exc.code, intent["detail"]) from exc
                events = engine.reserve(state, intent_id, self.clock(), {"reserveTx": reserve_tx})
                events += engine.mark_paying(state, intent_id)
                act = self._activity("Spend reserved", f"{usd(intent['amountMinor'])} held against the {intent['agent']} budget before payment", "pending", self.arc.config()["label"], reserve_tx)
                version = self._commit(state, version, events, [act])
            if intent["status"] in ("PAYING", "RESERVED"):
                return self._pay_and_settle(state, version, intent)
            return {"intent": intent, "event": None}

    def _pay_and_settle(self, state: dict[str, Any], version: int, intent: dict[str, Any]) -> dict[str, Any]:
        key = intent["chainKey"]
        try:
            receipt = self.hedera.pay_research(intent["agent"], key)
        except GatewayUncertain as exc:
            act = self._activity("Payment outcome unknown", f"{exc} · the reservation stays held until reconciliation confirms settlement or timeout", "pending", self.hedera.label())
            self._commit(state, version, [{"type": "spend.uncertain", "intentId": intent["id"]}], [act])
            return {"intent": intent, "event": act}
        except GatewayFailed as exc:
            return self._release(state, version, intent, f"PAYMENT_FAILED: {exc}")
        return self._settle(state, version, intent, receipt)

    def _settle(self, state: dict[str, Any], version: int, intent: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
        commit_tx = None
        if self.arc.live:
            c = self.arc.commit({"facilityKey": state["chainKey"], "intentKey": intent["chainKey"], "amountMinor": str(intent["amountMinor"]), "hederaTxId": receipt["transactionId"], "hcsSequence": receipt.get("auditSequence") or 0}, key=f"{intent['chainKey']}:commit")
            commit_tx = c.get("txHash")
        events = engine.commit(state, intent["id"], self.clock(), {**receipt, "commitTx": commit_tx})
        merchant = intent["merchant"]
        act = self._activity("x402 payment settled", f"{usd(intent['amountMinor'])} paid to {merchant} · HCS audit record {'added' if receipt.get('auditTransactionId') else 'pending'} · reservation committed", "success", receipt.get("network", self.hedera.label()), receipt["transactionId"], receipt.get("explorerUrl"))
        self._commit(state, version, events, [act])
        return {"intent": intent, "event": act}

    def _release(self, state: dict[str, Any], version: int, intent: dict[str, Any], reason: str) -> dict[str, Any]:
        release_tx = None
        if self.arc.live:
            r = self.arc.release({"facilityKey": state["chainKey"], "intentKey": intent["chainKey"]}, key=f"{intent['chainKey']}:release")
            release_tx = r.get("txHash")
        events = engine.release(state, intent["id"], self.clock(), reason)
        act = self._activity("Reservation released", f"{usd(intent['amountMinor'])} returned to the {intent['agent']} budget · {reason}", "pending", self.arc.config()["label"], release_tx)
        self._commit(state, version, events, [act])
        return {"intent": intent, "event": act}

    def reconcile(self, aggregate_id: str = DEMO_ID) -> list[dict[str, Any]]:
        """Resolve interrupted payments exactly once (run at startup and on demand)."""
        results = []
        with self.store.lock:
            loaded = self.store.load(aggregate_id)
            if not loaded:
                return []
            state, version = loaded
            for intent in [i for i in state["intents"] if i["status"] in ("RESERVED", "PAYING")]:
                receipt = self.hedera.lookup_payment(intent["chainKey"])
                if receipt and receipt.get("finalized"):
                    out = self._settle(state, version, intent, receipt)
                elif self.clock() >= intent["expiresAt"]:
                    out = self._release(state, version, intent, "TIMEOUT_NO_SETTLEMENT")
                else:
                    # Still inside the reservation window: retry with the same idempotency key.
                    # The gateway's durable ledger guarantees this cannot pay twice.
                    out = self._pay_and_settle(state, version, intent)
                results.append(out)
                state, version = self.store.load(aggregate_id)  # type: ignore[misc]
        return results

    # ------------------------------------------------------------ repay
    def repay(self, aggregate_id: str, amount_minor: int | None = None) -> dict[str, Any]:
        """Never paused: lenders must always be able to receive money."""
        self.assess(aggregate_id)
        with self.store.lock:
            state, version = self.load(aggregate_id)
            amount = amount_minor or state["receivable"]["faceValueMinor"]
            if any(i["status"] in ("RESERVED", "PAYING") for i in state["intents"]):
                self.reconcile(aggregate_id)
                state, version = self.load(aggregate_id)
            evidence = {}
            if self.arc.live:
                r = self.arc.repay({"facilityKey": state["chainKey"], "amountMinor": str(amount)}, key=f"{state['chainKey']}:repay:{state['facility']['customerPaidMinor']}:{amount}")
                evidence = {"txHash": r.get("txHash"), "explorerUrl": r.get("explorerUrl"), "chainToLendersMinor": r.get("toLendersMinor"), "chainToOperatorMinor": r.get("toOperatorMinor")}
            events = engine.repay(state, amount, self.clock(), evidence)
            received = events[0]
            if self.arc.live and evidence.get("chainToLendersMinor") is not None and int(evidence["chainToLendersMinor"]) != received["toLendersMinor"]:
                received["chainMismatch"] = True
            audit = self._audit("repayment.received", {"amountMinor": amount, "toLendersMinor": received["toLendersMinor"], "toOperatorMinor": received["toOperatorMinor"]}, key=f"{state['chainKey']}:repay-audit:{state['facility']['customerPaidMinor']}")
            done = state["facility"]["status"] == "REPAID"
            title = "Repayment complete" if done else "Partial repayment applied"
            detail = f"{usd(received['toLendersMinor'])} paid to lenders · {usd(received['toOperatorMinor'])} to operator"
            if done:
                detail += f" · {usd(state['facility']['undrawnReturnedMinor'])} undrawn credit returned · debt $0.00"
            else:
                detail += f" · {usd(state['facility']['lenderDueMinor'] - state['facility']['lenderPaidMinor'])} still owed to lenders"
            act = self._activity(title, detail, "success", "Arc repayment router" + ("" if self.arc.live else " · simulated"), evidence.get("txHash") or audit.get("transactionId"), evidence.get("explorerUrl"))
            activity = [act]
            if done:
                activity.append(self._close_credit(state))
            self._commit(state, version, events, activity)
            return {"event": act}

    def _close_credit(self, state: dict[str, Any]) -> dict[str, Any]:
        """Fold the closed facility into the borrower's credit history: repayment timing
        plus how the agents behaved (clean payments up, policy rejections down)."""
        before = self.profile(state["borrower"]["id"])
        old = credit.score(before)["score"]
        settled = sum(1 for i in state["intents"] if i["status"] == "SETTLED")
        rejected = sum(1 for i in state["intents"] if i["status"] == "REJECTED")
        outcome = state["receivable"]["closedAs"]
        after = credit.record_close(before, outcome, state["facility"]["fundedMinor"], settled, rejected)
        self.store.save_profile(after)
        new_terms = credit.terms(after, 0, 10_000, 10_000)
        state["creditAfter"] = {"before": old, "after": new_terms["score"], "tier": new_terms["tier"], "outcome": outcome, "nextStep": new_terms["nextStep"], "limitMinor": new_terms["limitMinor"]}
        label = {"ON_TIME": "on-time repayment", "LATE": "late repayment", "RECOVERED": "recovered after default"}[outcome]
        detail = f"{old} → {new_terms['score']} · {label} · {settled} clean agent payment{'s' if settled != 1 else ''}, {rejected} policy rejection{'s' if rejected != 1 else ''} · {new_terms['tier'].title()} tier, {usd(new_terms['limitMinor'])} limit · {new_terms['nextStep']}"
        return self._activity("Credit score updated", detail, "success" if new_terms["score"] >= old else "pending", "Deterministic credit score")

    # ------------------------------------------------------------ demo actions
    def demo_action(self, action: str, aggregate_id: str = DEMO_ID) -> dict[str, Any]:
        scenarios = self.fixture["scenarios"]
        if action == "verify_invoice":
            out = self.verify(aggregate_id)
            self.tokenize(aggregate_id)
        elif action == "match_lenders":
            out = self.fund(aggregate_id)
        elif action == "research_payment":
            intent = self.create_intent(aggregate_id, "research", "Briefly Data", scenarios["researchPaymentMinor"])
            if intent["status"] == "REJECTED":
                raise PolicyError(intent["reason"], intent.get("detail", ""))
            out = self.execute_intent(aggregate_id, intent["id"])
        elif action in ("compute_overspend", "global_overspend"):
            agent, merchant, amount = ("compute", "Northstar Cloud", scenarios["computeOverspendMinor"]) if action == "compute_overspend" else ("lead", "Northstar Cloud", scenarios["leadCeilingTestMinor"])
            state, _ = self.load(aggregate_id)
            if state["receivable"]["status"] != "ACTIVE":
                raise TransitionError("Fund the facility before evaluating spend requests.")
            intent = self.create_intent(aggregate_id, agent, merchant, amount)
            if intent["status"] != "REJECTED":
                raise RuntimeError("The policy test request was unexpectedly approved.")
            out = {"event": self.store.activity(aggregate_id, 1)[0]}
        elif action == "partial_repay":
            out = self.repay(aggregate_id, 3_000 * engine.USDC)
        elif action == "repay":
            state, _ = self.load(aggregate_id)
            fac = state["facility"]
            remaining = engine.lender_outstanding(state, self.clock()) if fac["customerPaidMinor"] else state["receivable"]["faceValueMinor"]
            out = self.repay(aggregate_id, state["receivable"]["faceValueMinor"] - fac["customerPaidMinor"] if fac["customerPaidMinor"] else remaining)
        else:
            raise PolicyError("UNKNOWN_ACTION", "Unknown demo action.")
        return {"state": self.view(aggregate_id), "event": out.get("event")}


def default_orchestrator() -> Orchestrator:
    return Orchestrator(Store(os.environ.get("DATABASE_URL")))
