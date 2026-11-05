import unittest
from copy import deepcopy

from app import credit, engine, verification
from app.engine import DAY, USDC, PolicyError, TransitionError

NOW = 1_790_000_000
FIXTURE = engine.load_fixture()


def verified(state, now=NOW, irn=True):
    rec = state["receivable"]
    if irn:
        rec["irn"] = verification.compute_irn(rec["sellerTaxId"], verification.financial_year(engine.datetime.fromtimestamp(now, tz=engine.timezone.utc).date()), "INV", rec["invoiceNumber"])
    result = verification.verify_invoice(engine.invoice_of(rec), state["borrower"], now)
    terms = credit.terms({"borrowerId": "x", **FIXTURE["creditHistory"]}, rec["faceValueMinor"], rec["requestedAdvanceRateBps"], result["advanceCapBps"])
    return engine.verify(state, now, result, terms)


def active_state():
    state = engine.new_aggregate("t", engine.load_fixture(), NOW)
    verified(state)
    engine.tokenize(state, NOW, {"tokenId": "sim", "simulated": True})
    plan = engine.quote(state, NOW)
    engine.begin_funding(state, plan)
    for allocation in plan["allocations"]:
        engine.record_fill(state, NOW, allocation, {})
    engine.activate(state, None)
    engine.assign_budgets(state)
    return state


class EngineTests(unittest.TestCase):
    def test_lender_fee_matches_contract_formula(self) -> None:
        self.assertEqual(engine.lender_fee(4_000 * USDC, 700, 0, 30), 23_013_698)
        self.assertEqual(engine.lender_fee(2_000 * USDC, 850, 0, 30), 13_972_602)
        self.assertEqual(engine.lender_fee(4_000 * USDC, 700, 45 * DAY, 30), 34_520_547)

    def test_canonical_id_is_stable_and_normalised(self) -> None:
        a = engine.canonical_receivable_id("IN", "27AABCN1234K1ZL", "2026-27", "INV", "inv-2048")
        b = engine.canonical_receivable_id("in", " 27aabcn1234k1zl ", "2026-27", "inv", "INV-2048")
        self.assertEqual(a, b)
        self.assertNotEqual(a, engine.canonical_receivable_id("IN", "27AABCN1234K1ZL", "2026-27", "INV", "INV-2049"))
        # Amount and due date are not part of the ID, so editing them cannot refinance the invoice.
        state = engine.new_aggregate("t", engine.load_fixture(), NOW)
        fixture = engine.load_fixture()
        fixture["receivable"]["faceValueMinor"] += 1
        fixture["receivable"]["termsDays"] = 45
        self.assertEqual(engine.new_aggregate("u", fixture, NOW)["receivable"]["canonicalId"], state["receivable"]["canonicalId"])

    def test_lifecycle_rejects_unauthorized_transitions(self) -> None:
        state = engine.new_aggregate("t", engine.load_fixture(), NOW)
        with self.assertRaises(TransitionError):
            engine.quote(state, NOW)  # cannot fund a DRAFT
        verified(state)
        with self.assertRaises(TransitionError):
            verified(state)
        with self.assertRaises(PolicyError):
            engine.tokenize(state, NOW, {})

    def test_cheapest_eligible_offers_fill_first(self) -> None:
        state = engine.new_aggregate("t", engine.load_fixture(), NOW)
        verified(state)
        engine.tokenize(state, NOW, {"tokenId": "x"})
        plan = engine.quote(state, NOW)
        self.assertEqual([(a["lender"], a["principalMinor"]) for a in plan["allocations"]], [("Alice", 4_000 * USDC), ("Bob", 2_000 * USDC)])

    def test_ineligible_offers_are_skipped_with_reasons(self) -> None:
        state = engine.new_aggregate("t", engine.load_fixture(), NOW)
        verified(state)
        engine.tokenize(state, NOW, {"tokenId": "x"})
        state["receivable"]["buyerScore"] = "B"  # Alice and Bob require A- or better
        with self.assertRaises(PolicyError) as ctx:
            engine.quote(state, NOW)  # Charlie alone cannot cover $6,000
        self.assertEqual(ctx.exception.code, "INSUFFICIENT_ELIGIBLE_LIQUIDITY")
        plan = engine.quote(state, NOW, 5_000 * USDC)
        self.assertEqual([a["lender"] for a in plan["allocations"]], ["Charlie"])
        self.assertEqual({r["reason"] for r in plan["rejected"]}, {"BUYER_SCORE_INELIGIBLE"})
        state["offers"][2]["expiresAt"] = NOW
        with self.assertRaises(PolicyError) as ctx:
            engine.quote(state, NOW, 5_000 * USDC)
        self.assertEqual(ctx.exception.code, "INSUFFICIENT_ELIGIBLE_LIQUIDITY")

    def test_excess_advance_and_stale_claims_are_rejected(self) -> None:
        state = engine.new_aggregate("t", engine.load_fixture(), NOW)
        verified(state)
        engine.tokenize(state, NOW, {"tokenId": "x"})
        with self.assertRaises(PolicyError) as ctx:
            engine.quote(state, NOW, 7_500 * USDC + 1)
        self.assertEqual(ctx.exception.code, "BORROWING_BASE_EXCEEDED")
        with self.assertRaises(PolicyError) as ctx:
            engine.quote(state, NOW + 31 * DAY)
        self.assertEqual(ctx.exception.code, "STALE_RECEIVABLE")

    def test_policies_are_independent_and_ordered_like_the_contract(self) -> None:
        state = active_state()
        cases = [
            ("compute", "Northstar Cloud", 5_500 * USDC, "AGENT_BUDGET_EXCEEDED"),
            ("lead", "Northstar Cloud", 6_500 * USDC, "GLOBAL_CREDIT_LIMIT_EXCEEDED"),
            ("research", "Briefly Data", 1 * USDC, "TRANSACTION_LIMIT_EXCEEDED"),
            ("research", "Northstar Cloud", USDC // 4, "MERCHANT_NOT_ALLOWED"),
        ]
        for agent, merchant, amount, code in cases:
            with self.assertRaises(PolicyError) as ctx:
                engine.check_spend(state, agent, merchant, amount, NOW)
            self.assertEqual(ctx.exception.code, code, (agent, amount))
        for i in range(50):
            intent, _ = engine.create_intent(state, "research", "Briefly Data", USDC // 2, NOW)
            engine.reserve(state, intent["id"], NOW)
        with self.assertRaises(PolicyError) as ctx:
            engine.check_spend(state, "research", "Briefly Data", USDC // 2, NOW)
        self.assertEqual(ctx.exception.code, "DAILY_LIMIT_EXCEEDED")
        engine.check_spend(state, "research", "Briefly Data", USDC // 2, NOW + DAY)

    def test_budgets_cannot_exceed_funded_line(self) -> None:
        state = active_state()
        with self.assertRaises(PolicyError):
            engine.assign_budgets(state, [{"identity": "data", "allocationMinor": 2_000 * USDC + 1}])

    def test_commit_requires_finalized_receipt_and_happens_once(self) -> None:
        state = active_state()
        intent, _ = engine.create_intent(state, "research", "Briefly Data", USDC // 4, NOW)
        engine.reserve(state, intent["id"], NOW)
        with self.assertRaises(PolicyError):
            engine.commit(state, intent["id"], NOW, {"transactionId": "x", "finalized": False})
        engine.commit(state, intent["id"], NOW, {"transactionId": "0.0.1@1.1", "finalized": True})
        self.assertEqual(engine.commit(state, intent["id"], NOW, {"transactionId": "0.0.1@1.1", "finalized": True}), [])
        self.assertEqual(state["facility"]["spentMinor"], USDC // 4)
        with self.assertRaises(TransitionError):
            engine.release(state, intent["id"], NOW, "late")

    def test_failed_payment_release_restores_budget(self) -> None:
        state = active_state()
        intent, _ = engine.create_intent(state, "research", "Briefly Data", USDC // 4, NOW)
        engine.reserve(state, intent["id"], NOW)
        self.assertEqual(engine.available_credit(state), 6_000 * USDC - USDC // 4)
        engine.release(state, intent["id"], NOW, "PAYMENT_FAILED")
        self.assertEqual(engine.available_credit(state), 6_000 * USDC)
        self.assertEqual(state["agents"][0]["reservedMinor"], 0)

    def test_full_repayment_waterfall_matches_contract(self) -> None:
        state = active_state()
        intent, _ = engine.create_intent(state, "research", "Briefly Data", USDC // 4, NOW)
        engine.reserve(state, intent["id"], NOW)
        engine.commit(state, intent["id"], NOW, {"transactionId": "t", "finalized": True})
        events = engine.repay(state, 10_000 * USDC, NOW)
        self.assertEqual(events[0]["toLendersMinor"], 6_036_986_300)
        self.assertEqual(events[0]["toOperatorMinor"], 3_963_013_700)
        self.assertEqual([a["paidMinor"] for a in state["allocations"]], [4_023_013_698, 2_013_972_602])
        self.assertEqual(state["facility"]["undrawnReturnedMinor"], 6_000 * USDC - USDC // 4)
        self.assertEqual(state["receivable"]["status"], "REPAID")
        self.assertEqual(engine.debt_outstanding(state, NOW), 0)

    def test_partial_repayment_pays_lenders_pro_rata_before_operator(self) -> None:
        state = active_state()
        engine.repay(state, 3_000 * USDC, NOW)
        self.assertEqual(state["facility"]["operatorPaidMinor"], 0)
        self.assertEqual(state["allocations"][0]["paidMinor"], 1_999_183_118)  # matches the contract test
        self.assertEqual(sum(a["paidMinor"] for a in state["allocations"]), 3_000 * USDC)
        self.assertEqual(state["receivable"]["status"], "ACTIVE")
        engine.repay(state, 7_000 * USDC, NOW)
        self.assertEqual(state["facility"]["operatorPaidMinor"], 10_000 * USDC - 6_036_986_300)
        self.assertEqual(state["receivable"]["status"], "REPAID")

    def test_repayment_conserves_value_for_many_splits(self) -> None:
        base = active_state()
        for first in (1, 999_999, 2_500 * USDC, 6_036_986_299, 6_036_986_300, 9_999 * USDC):
            state = deepcopy(base)
            engine.repay(state, first, NOW)
            if state["receivable"]["status"] == "ACTIVE":
                engine.repay(state, 10_000 * USDC - first, NOW)
            fac = state["facility"]
            self.assertEqual(fac["lenderPaidMinor"] + fac["operatorPaidMinor"], fac["customerPaidMinor"])
            self.assertEqual(sum(a["paidMinor"] for a in state["allocations"]), fac["lenderPaidMinor"])
            self.assertLessEqual(fac["lenderPaidMinor"], fac["lenderDueMinor"])


if __name__ == "__main__":
    unittest.main()
