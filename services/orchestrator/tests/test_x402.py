import unittest

from app.x402 import X402GatewayClient


class X402GatewayTests(unittest.TestCase):
    def test_simulated_mode_never_calls_gateway(self) -> None:
        client = X402GatewayClient(mode="simulated", url="http://127.0.0.1:4022", api_token="")
        self.assertIsNone(client.pay_research("Research", "demo-key"))

    def test_live_mode_requires_an_api_token(self) -> None:
        client = X402GatewayClient(mode="live", url="http://127.0.0.1:4022", api_token="")
        with self.assertRaisesRegex(RuntimeError, "HEDERA_GATEWAY_API_TOKEN"):
            client.pay_research("Research", "demo-key")


if __name__ == "__main__":
    unittest.main()
