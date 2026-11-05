import unittest

from app import credit

USDC = credit.USDC
FACE = 10_000 * USDC


class CreditScoreTests(unittest.TestCase):
    def test_new_borrower_starts_small(self) -> None:
        profile = credit.new_profile("new")
        terms = credit.terms(profile, FACE, 7500, 8000)
        self.assertEqual(terms["score"], 550)
        self.assertEqual(terms["tier"], "STARTER")
        self.assertEqual(terms["advanceRateBps"], 5000)
        self.assertEqual(terms["borrowingBaseMinor"], 1_000 * USDC)  # the $1,000 limit binds, not 50% of $10,000
        self.assertEqual(terms["binding"], "limit")
        self.assertIn("2 more on-time repayments to reach BUILDING", terms["nextStep"])

    def test_limit_steps_up_with_each_on_time_repayment(self) -> None:
        profile = credit.new_profile("new")
        seen = []
        for _ in range(6):
            profile = credit.record_close(profile, "ON_TIME", 0, 0, 0)
            seen.append(credit.terms(profile, FACE, 7500, 8000)["tier"])
        # Scores 590, 630, 670, 710, 750, 790.
        self.assertEqual(seen, ["STARTER", "BUILDING", "BUILDING", "ESTABLISHED", "ESTABLISHED", "ESTABLISHED"])

    def test_demo_borrower_is_established_and_keeps_the_demo_numbers(self) -> None:
        profile = {"borrowerId": "northstar", "onTimeRepayments": 4, "lateRepayments": 0, "defaults": 0, "openDefaults": 0, "settledAgentPayments": 30, "agentPolicyRejections": 1, "openPrincipalMinor": 0}
        terms = credit.terms(profile, FACE, 7500, 8000)
        self.assertEqual((terms["score"], terms["tier"], terms["advanceRateBps"], terms["borrowingBaseMinor"]), (740, "ESTABLISHED", 7500, 7_500 * USDC))

    def test_agent_history_counts_both_ways(self) -> None:
        clean = credit.score({**credit.new_profile("a"), "settledAgentPayments": 100})["score"]
        noisy = credit.score({**credit.new_profile("a"), "agentPolicyRejections": 100})["score"]
        self.assertEqual(clean, 550 + 40)   # capped bonus
        self.assertEqual(noisy, 550 - 60)   # capped penalty

    def test_late_and_default_lower_the_score(self) -> None:
        profile = credit.record_close(credit.new_profile("a"), "LATE", 0, 0, 0)
        self.assertEqual(credit.score(profile)["score"], 490)
        defaulted = credit.record_default(profile)
        terms = credit.terms(defaulted, FACE, 7500, 8000)
        self.assertEqual(terms["tier"], "BLOCKED")
        self.assertEqual(terms["borrowingBaseMinor"], 0)
        self.assertEqual(credit.next_step(defaulted), "Repay the defaulted invoice to lift the block.")
        recovered = credit.record_close(defaulted, "RECOVERED", 0, 0, 0)
        self.assertEqual(recovered["openDefaults"], 0)
        self.assertEqual(recovered["defaults"], 1)  # the history remembers it

    def test_evidence_caps_the_advance(self) -> None:
        profile = {**credit.new_profile("a"), "onTimeRepayments": 8}
        self.assertEqual(credit.terms(profile, FACE, 7500, 3000)["advanceRateBps"], 3000)
        self.assertEqual(credit.terms(profile, FACE, 7500, 3000)["binding"], "evidence")

    def test_open_facilities_use_up_the_limit(self) -> None:
        profile = {**credit.new_profile("a"), "onTimeRepayments": 4, "openPrincipalMinor": 8_000 * USDC}
        terms = credit.terms(profile, FACE, 7500, 8000)
        self.assertEqual(terms["limitMinor"], 10_000 * USDC)
        self.assertEqual(terms["borrowingBaseMinor"], 2_000 * USDC)
        profile = credit.record_close(profile, "ON_TIME", 8_000 * USDC, 0, 0)
        self.assertEqual(profile["openPrincipalMinor"], 0)

    def test_score_is_bounded(self) -> None:
        self.assertEqual(credit.score({**credit.new_profile("a"), "defaults": 10})["score"], 300)
        self.assertEqual(credit.score({**credit.new_profile("a"), "onTimeRepayments": 99, "settledAgentPayments": 99})["score"], 900)


if __name__ == "__main__":
    unittest.main()
