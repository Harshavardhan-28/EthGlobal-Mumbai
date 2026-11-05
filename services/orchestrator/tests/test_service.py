import unittest

from app.engine import USDC, PolicyError
from tests.helpers import TempDirCase, make_orchestrator


class OrchestratorTests(TempDirCase, unittest.TestCase):
    def run_to_active(self) -> None:
        self.orc.demo_action("verify_invoice")
        self.orc.demo_action("match_lenders")

    def test_seven_step_demo_flow(self) -> None:
        view = self.orc.view()
        self.assertEqual(view["lifecycle"], "DRAFT")
        view = self.orc.demo_action("verify_invoice")["state"]
        self.assertEqual(view["lifecycle"], "TOKENIZED")
        self.assertTrue(view["invoice"]["tokenSimulated"])
        view = self.orc.demo_action("match_lenders")["state"]
        self.assertEqual(view["facility"]["funded"], "6000.00")
        self.assertEqual([o["selected"] for o in view["offers"]], [True, True, False])
        self.assertEqual(view["agents"][-1]["allocation"], "1000.00")  # unallocated reserve for the lead agent
        view = self.orc.demo_action("research_payment")["state"]
        self.assertEqual(view["facility"]["spent"], "0.25")
        self.assertEqual(view["spends"][0]["status"], "SETTLED")
        rejected = self.orc.demo_action("compute_overspend")
        self.assertIn("AGENT_BUDGET_EXCEEDED", rejected["event"]["detail"])
        rejected = self.orc.demo_action("global_overspend")
        self.assertIn("GLOBAL_CREDIT_LIMIT_EXCEEDED", rejected["event"]["detail"])
        self.assertEqual(rejected["state"]["facility"]["spent"], "0.25")
        view = self.orc.demo_action("repay")["state"]
        self.assertEqual(view["lifecycle"], "REPAID")
        self.assertEqual(view["facility"]["outstanding"], "0.00")
        self.assertEqual(view["facility"]["lenderPaid"], "6036.98")
        self.assertEqual(view["facility"]["operatorRemainder"], "3963.01")

    def test_policy_test_before_funding_is_a_transition_error_not_a_crash(self) -> None:
        with self.assertRaises(Exception) as ctx:
            self.orc.demo_action("global_overspend")
        self.assertIn("Fund the facility", str(ctx.exception))

    def test_ceiling_test_works_before_any_spend(self) -> None:
        self.run_to_active()  # regression: the old prototype crashed here with HTTP 500
        result = self.orc.demo_action("global_overspend")
        self.assertIn("GLOBAL_CREDIT_LIMIT_EXCEEDED", result["event"]["detail"])

    def test_duplicate_receivable_is_rejected(self) -> None:
        self.orc.view()
        with self.assertRaises(PolicyError) as ctx:
            self.orc.create("someone-else-financing-the-same-invoice")
        self.assertEqual(ctx.exception.code, "DUPLICATE_RECEIVABLE")

    def test_failed_payment_releases_reservation(self) -> None:
        self.run_to_active()
        self.orc.hedera.fail_next_payment = "failed"
        view = self.orc.demo_action("research_payment")["state"]
        self.assertEqual(view["spends"][0]["status"], "RELEASED")
        self.assertEqual(view["facility"]["reserved"], "0.00")
        self.assertEqual(view["facility"]["spent"], "0.00")
        self.assertEqual(view["facility"]["remaining"], "6000.00")

    def test_uncertain_payment_survives_restart_and_commits_exactly_once(self) -> None:
        self.run_to_active()
        self.orc.hedera.fail_next_payment = "uncertain"  # settles, but the response is lost
        view = self.orc.demo_action("research_payment")["state"]
        self.assertEqual(view["spends"][0]["status"], "PAYING")
        self.assertEqual(view["facility"]["reserved"], "0.25")
        # Simulate a service restart: new orchestrator on the same database and payment ledger.
        self.orc.store.close()
        restarted = make_orchestrator(self.tempdir.name, self.clock)
        restarted.reconcile()
        restarted.reconcile()  # idempotent
        view = restarted.view()
        self.assertEqual(view["spends"][0]["status"], "SETTLED")
        self.assertEqual(view["facility"]["spent"], "0.25")
        self.assertEqual(view["facility"]["reserved"], "0.00")
        self.assertEqual(len([s for s in view["spends"] if s["status"] == "SETTLED"]), 1)
        self.orc = restarted

    def test_reservation_times_out_when_no_settlement_exists(self) -> None:
        self.run_to_active()
        intent = self.orc.create_intent("retailco-net30", "research", "Briefly Data", USDC // 4)
        state, version = self.orc.load()
        from app import engine

        engine.reserve(state, intent["id"], self.clock())
        engine.mark_paying(state, intent["id"])
        self.orc.store.save(state, version, [])
        self.clock.now += 301
        self.orc.reconcile()
        view = self.orc.view()
        self.assertEqual(view["spends"][0]["status"], "RELEASED")
        self.assertEqual(view["spends"][0]["reason"], "TIMEOUT_NO_SETTLEMENT")
        self.assertEqual(view["facility"]["remaining"], "6000.00")

    def test_partial_then_final_repayment(self) -> None:
        self.run_to_active()
        view = self.orc.demo_action("partial_repay")["state"]
        self.assertEqual(view["lifecycle"], "ACTIVE")
        self.assertEqual(view["facility"]["operatorRemainder"], "0.00")
        self.assertEqual(view["facility"]["lenderPaid"], "3000.00")
        view = self.orc.demo_action("repay")["state"]
        self.assertEqual(view["lifecycle"], "REPAID")
        self.assertEqual(view["facility"]["customerPaid"], "10000.00")

    def test_reset_starts_a_fresh_run_and_logs_it(self) -> None:
        self.run_to_active()
        view = self.orc.reset()
        self.assertEqual(view["lifecycle"], "DRAFT")
        state, _ = self.orc.load()
        self.assertEqual(state["run"], 2)
        self.assertIn("demo.reset", [e["type"] for e in self.orc.store.log("retailco-net30")])
        self.assertEqual([e["title"] for e in view["events"]], ["Invoice registered"])  # previous run hidden

    def test_domain_event_log_is_append_only_and_ordered(self) -> None:
        self.run_to_active()
        types = [e["type"] for e in self.orc.store.log("retailco-net30")]
        self.assertEqual(types[:4], ["receivable.drafted", "receivable.verified", "receivable.tokenized", "facility.quoted"])
        self.assertIn("facility.activated", types)

    def test_agents_have_hcs14_uaids(self) -> None:
        view = self.orc.view()
        self.assertTrue(all(a["uaid"].startswith("uaid:aid:") for a in view["agents"]))


if __name__ == "__main__":
    unittest.main()
