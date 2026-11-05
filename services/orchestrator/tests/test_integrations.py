import os
import unittest
from unittest.mock import patch

from app.integrations import integration_status


class IntegrationStatusTests(unittest.TestCase):
    def test_default_status_keeps_live_integrations_unavailable(self) -> None:
        with patch.dict(os.environ, {"HEDERA_PAYMENT_MODE": "simulated"}, clear=True):
            status = integration_status()
        self.assertFalse(status["x402"]["ready"])
        self.assertFalse(status["ats"]["ready"])
        self.assertEqual(status["aqua"]["mode"], "simulated")
        self.assertFalse(status["arc"]["ready"])

    def test_x402_is_ready_only_with_live_mode_and_all_required_config(self) -> None:
        settings = {
            "HEDERA_PAYMENT_MODE": "live",
            "HEDERA_AGENT_ACCOUNT_ID": "0.0.1001",
            "HEDERA_AGENT_PRIVATE_KEY": "private-key",
            "HEDERA_RESEARCH_MERCHANT_ID": "0.0.1002",
            "HEDERA_AUDIT_OPERATOR_ID": "0.0.1003",
            "HEDERA_AUDIT_OPERATOR_KEY": "audit-key",
            "HEDERA_AUDIT_TOPIC_ID": "0.0.1004",
            "HEDERA_GATEWAY_API_TOKEN": "local-token",
        }
        with patch.dict(os.environ, settings, clear=True):
            self.assertTrue(integration_status()["x402"]["ready"])


if __name__ == "__main__":
    unittest.main()
