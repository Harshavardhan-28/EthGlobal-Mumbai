import json
import os
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app import compliance, engine
from app.engine import DAY, USDC, PolicyError
from tests.helpers import TempDirCase, make_orchestrator
from tests.test_engine import NOW, active_state

DENYLIST = compliance.load_denylist()


class ScreeningTests(unittest.TestCase):
    def test_screen_matches_normalised_values_only(self) -> None:
        hits = compliance.screen([
            {"role": "buyer", "ref": "buyer", "type": "taxId", "value": " 07aaacb4321q1zm "},
            {"role": "merchant", "ref": "Briefly Data", "type": "name", "value": "Briefly Data"},
            {"role": "lender", "ref": "Eve", "type": "name", "value": "blocked   trading co"},
        ], DENYLIST)
        self.assertEqual([(h["role"], h["ref"]) for h in hits], [("buyer", "buyer"), ("lender", "Eve")])

    def test_public_payload_drops_identifying_fields(self) -> None:
        clean = compliance.public_payload({"canonicalId": "0xabc", "sellerTaxId": "27AABCN1234K1ZL", "buyer": "RetailCo", "amountMinor": 5})
        self.assertEqual(clean, {"canonicalId": "0xabc", "amountMinor": 5})
        with self.assertRaises(ValueError):
            compliance.public_payload({"code": "seller 27AABCN1234K1ZL"})


class EngineComplianceTests(unittest.TestCase):
    def test_kyc_is_required(self) -> None:
        from tests.test_engine import verified

        state = engine.new_aggregate("t", engine.load_fixture(), NOW)
        state["borrower"]["kycStatus"] = "PENDING"
        with self.assertRaises(PolicyError) as ctx:
            verified(state)
        self.assertEqual(ctx.exception.code, "KYC_REQUIRED")

    def test_unapproved_or_blocked_lenders_are_skipped(self) -> None:
        from tests.test_engine import verified

        state = engine.new_aggregate("t", engine.load_fixture(), NOW)
        verified(state)
        engine.tokenize(state, NOW, {"tokenId": "x"})
        state["offers"][0]["kycStatus"] = "PENDING"   # Alice
        state["offers"][1]["blocked"] = True          # Bob
        with self.assertRaises(PolicyError) as ctx:
            engine.quote(state, NOW)  # Charlie's $5,000 alone cannot cover $6,000
        self.assertEqual(ctx.exception.code, "INSUFFICIENT_ELIGIBLE_LIQUIDITY")
        plan = engine.quote(state, NOW, 5_000 * USDC)
        self.assertEqual([a["lender"] for a in plan["allocations"]], ["Charlie"])
        self.assertEqual({r["lender"]: r["reason"] for r in plan["rejected"]}, {"Alice": "LENDER_NOT_APPROVED", "Bob": "LENDER_BLOCKED"})

    def test_blocked_merchant_is_rejected_after_the_allowlist(self) -> None:
        state = active_state()
        state["compliance"]["blockedMerchants"] = ["Briefly Data"]
        with self.assertRaises(PolicyError) as ctx:
            engine.check_spend(state, "research", "Briefly Data", USDC // 4, NOW)
        self.assertEqual(ctx.exception.code, "MERCHANT_BLOCKED")


class OverdueDefaultDisputeTests(unittest.TestCase):
    def test_overdue_after_grace_then_default_after_thirty_days(self) -> None:
        state = active_state()
        due = state["receivable"]["dueAt"]
        self.assertEqual(engine.assess(state, due + 7 * DAY - 1), [])
        self.assertEqual(engine.assess(state, due + 7 * DAY)[0]["type"], "facility.overdue")
        self.assertEqual(state["receivable"]["status"], "OVERDUE")
        with self.assertRaises(PolicyError) as ctx:
            engine.check_spend(state, "research", "Briefly Data", USDC // 4, due + 8 * DAY)
        self.assertEqual(ctx.exception.code, "FACILITY_OVERDUE")
        self.assertEqual(engine.assess(state, due + 30 * DAY)[0]["type"], "facility.defaulted")
        self.assertEqual(engine.assess(state, due + 31 * DAY), [])  # idempotent

    def test_late_repayment_still_pays_lenders_first_and_counts_as_late(self) -> None:
        state = active_state()
        late = state["receivable"]["dueAt"] + 10 * DAY
        engine.assess(state, late)
        engine.repay(state, 3_000 * USDC, late)
        self.assertEqual(state["receivable"]["status"], "OVERDUE")  # partial payment keeps the flag
        self.assertEqual(state["facility"]["operatorPaidMinor"], 0)
        events = engine.repay(state, 7_000 * USDC, late)
        self.assertEqual(events[-1]["outcome"], "LATE")
        self.assertEqual(state["receivable"]["status"], "REPAID")
        # 40 days of interest instead of the 30-day minimum, still paid before the operator.
        self.assertGreater(state["facility"]["lenderPaidMinor"], 6_036_986_300)

    def test_defaulted_facility_recovers_when_paid(self) -> None:
        state = active_state()
        engine.assess(state, state["receivable"]["dueAt"] + 30 * DAY)
        events = engine.repay(state, 10_000 * USDC, state["receivable"]["dueAt"] + 45 * DAY)
        self.assertEqual(events[-1]["outcome"], "RECOVERED")

    def test_dispute_freezes_spending_until_resolved(self) -> None:
        state = active_state()
        with self.assertRaises(PolicyError):
            engine.dispute(state, NOW, "  ")
        engine.dispute(state, NOW, "Goods not delivered")
        with self.assertRaises(PolicyError) as ctx:
            engine.check_spend(state, "research", "Briefly Data", USDC // 4, NOW)
        self.assertEqual(ctx.exception.code, "FACILITY_DISPUTED")
        engine.resolve_dispute(state, NOW + DAY, upheld=False)
        self.assertEqual(state["receivable"]["status"], "ACTIVE")
        engine.check_spend(state, "research", "Briefly Data", USDC // 4, NOW + DAY)
        engine.dispute(state, NOW + DAY, "Invoice cancelled")
        engine.resolve_dispute(state, NOW + 2 * DAY, upheld=True)
        self.assertEqual(state["receivable"]["status"], "DEFAULTED")


class ServiceComplianceTests(TempDirCase, unittest.TestCase):
    def test_default_blocks_the_borrowers_next_invoice(self) -> None:
        self.orc.demo_action("verify_invoice")
        self.orc.demo_action("match_lenders")
        state, _ = self.orc.load()
        self.clock.now = state["receivable"]["dueAt"] + 30 * DAY
        self.assertEqual(self.orc.assess()["status"], "DEFAULTED")
        profile = self.orc.profile("northstar-agents")
        self.assertEqual((profile["defaults"], profile["openDefaults"]), (1, 1))
        self.assertEqual(self.orc.credit_view("northstar-agents")["tier"], "BLOCKED")
        # Recovery: paying in full clears the block but keeps the default in the history.
        self.orc.repay("retailco-net30", 10_000 * USDC)
        profile = self.orc.profile("northstar-agents")
        self.assertEqual((profile["defaults"], profile["openDefaults"]), (1, 0))

    def test_sanctioned_buyer_cannot_be_financed(self) -> None:
        state, version = self.orc.load()
        state["receivable"]["buyerTaxId"] = "07AAACB4321Q1ZM"  # on the demo screening list
        self.orc.store.save(state, version, [])
        with self.assertRaises(PolicyError) as ctx:
            self.orc.verify("retailco-net30")
        self.assertEqual(ctx.exception.code, "SANCTIONS_MATCH")
        self.assertEqual(self.orc.view()["events"][0]["title"], "Invoice not eligible")

    def test_pause_stops_new_risk_but_not_repayment(self) -> None:
        self.orc.demo_action("verify_invoice")
        self.orc.demo_action("match_lenders")
        self.orc.set_paused(True)
        with self.assertRaises(PolicyError) as ctx:
            self.orc.demo_action("research_payment")
        self.assertEqual(ctx.exception.code, "SYSTEM_PAUSED")
        view = self.orc.demo_action("repay")["state"]
        self.assertEqual(view["lifecycle"], "REPAID")
        self.assertTrue(view["compliance"]["paused"])

    def test_closing_on_time_raises_the_score(self) -> None:
        for action in ("verify_invoice", "match_lenders", "research_payment", "compute_overspend", "global_overspend", "repay"):
            view = self.orc.demo_action(action)["state"]
        self.assertEqual(view["credit"]["after"], {"score": 760, "before": 740, "tier": "ESTABLISHED", "outcome": "ON_TIME", "limit": "10000.00"})
        self.assertIn("1 more on-time repayment to reach TRUSTED", view["credit"]["nextStep"])
        self.assertEqual(view["events"][0]["title"], "Credit score updated")
        reset = self.orc.reset()
        self.assertEqual(reset["credit"]["score"], 740)  # demo history restored

    def test_hcs_audit_payloads_never_carry_tax_ids(self) -> None:
        published = []
        self.orc.hedera.audit = lambda event_type, payload, key: published.append(payload) or {"transactionId": None}
        for action in ("verify_invoice", "match_lenders", "compute_overspend", "repay"):
            self.orc.demo_action(action)
        blob = json.dumps(published)
        self.assertTrue(published)
        for secret in ("27AABCN1234K1ZL", "29AAECR5678M1ZI", "RetailCo", "AABCN1234K"):
            self.assertNotIn(secret, blob)


class AdminApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        from app import main

        self.main = main
        main.orchestrator = make_orchestrator(self.tempdir.name)
        self.client = TestClient(main.app)
        os.environ.pop("ADMIN_API_TOKEN", None)

    def tearDown(self) -> None:
        os.environ.pop("ADMIN_API_TOKEN", None)
        self.main.orchestrator.store.close()
        self.tempdir.cleanup()

    def test_admin_pause_requires_a_configured_token(self) -> None:
        self.assertEqual(self.client.post("/api/admin/pause", json={"paused": True}).status_code, 403)
        os.environ["ADMIN_API_TOKEN"] = "secret"
        self.assertEqual(self.client.post("/api/admin/pause", json={"paused": True}, headers={"Authorization": "Bearer wrong"}).status_code, 401)
        self.assertTrue(self.client.post("/api/admin/pause", json={"paused": True}, headers={"Authorization": "Bearer secret"}).json()["paused"])
        response = self.client.post("/api/demo/retailco-net30/actions", json={"action": "verify_invoice"}, headers={"Idempotency-Key": str(uuid4())})
        self.assertEqual(response.status_code, 409)
        self.assertIn("SYSTEM_PAUSED", response.json()["detail"])

    def test_policies_and_credit_endpoints(self) -> None:
        policies = self.client.get("/api/policies").json()
        self.assertIn("GST_E_INVOICE", policies["evidenceTiers"])
        self.assertTrue(any(rule["id"] == "b2b" for rule in policies["eligibility"]))
        self.assertEqual(self.client.get("/api/credit/someone-new").json()["tier"], "STARTER")

    def test_verify_accepts_a_real_irn_and_rejects_malformed_ones(self) -> None:
        self.assertEqual(self.client.post("/api/receivables/retailco-net30/verify", json={"irn": "xyz"}).status_code, 422)
        response = self.client.post("/api/receivables/retailco-net30/verify", json={"irn": "ab" * 32}, headers={"Idempotency-Key": str(uuid4())})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["verification"]["tier"], "GSTIN_VERIFIED")  # a non-matching IRN downgrades

    def test_dispute_and_resolve_api(self) -> None:
        for action in ("verify_invoice", "match_lenders"):
            self.client.post("/api/demo/retailco-net30/actions", json={"action": action}, headers={"Idempotency-Key": str(uuid4())})
        disputed = self.client.post("/api/facilities/retailco-net30/dispute", json={"reason": "Goods not delivered"}, headers={"Idempotency-Key": str(uuid4())}).json()
        self.assertEqual(disputed["state"]["lifecycle"], "DISPUTED")
        self.assertEqual(self.client.post("/api/facilities/retailco-net30/dispute/resolve", json={"upheld": False}).status_code, 403)
        os.environ["ADMIN_API_TOKEN"] = "secret"
        resolved = self.client.post("/api/facilities/retailco-net30/dispute/resolve", json={"upheld": False}, headers={"Authorization": "Bearer secret", "Idempotency-Key": str(uuid4())}).json()
        self.assertEqual(resolved["state"]["lifecycle"], "ACTIVE")


if __name__ == "__main__":
    unittest.main()
