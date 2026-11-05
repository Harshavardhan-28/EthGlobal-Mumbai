from datetime import date, timedelta
import unittest

from working_capital_agent.agent import explain_policy_decision, prepare_ats_issuance, root_agent


class AdkAgentTests(unittest.TestCase):
    def test_agent_exposes_wallet_review_only_issuance_tool(self) -> None:
        result = prepare_ats_issuance(
            "INV-2048",
            "0.0.1001",
            "0.0.1002",
            "10000.00",
            (date.today() + timedelta(days=30)).isoformat(),
            "US0000000001",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "ISSUER_WALLET_REVIEW_REQUIRED")
        self.assertEqual(root_agent.name, "working_capital_coordinator")

    def test_agent_rejects_past_due_date(self) -> None:
        result = prepare_ats_issuance("INV-2048", "0.0.1001", "0.0.1002", "1", "2020-01-01", "US0000000001")
        self.assertFalse(result["ok"])


    def test_agent_explains_but_cannot_override_decisions(self) -> None:
        result = explain_policy_decision("agent_budget_exceeded")
        self.assertFalse(result["can_override"])
        self.assertIn("sub-budget", result["explanation"])


if __name__ == "__main__":
    unittest.main()
