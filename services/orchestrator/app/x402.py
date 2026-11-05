from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv


def load_project_env() -> None:
    for directory in Path(__file__).resolve().parents:
        candidate = directory / ".env"
        if candidate.is_file():
            load_dotenv(candidate)
            return


load_project_env()


class X402PaymentError(RuntimeError):
    pass


@dataclass(frozen=True)
class X402GatewayClient:
    mode: str
    url: str
    api_token: str

    @classmethod
    def from_environment(cls) -> "X402GatewayClient":
        return cls(
            mode=os.environ.get("HEDERA_PAYMENT_MODE", "simulated"),
            url=os.environ.get("HEDERA_GATEWAY_URL", "http://127.0.0.1:4022").rstrip("/"),
            api_token=os.environ.get("HEDERA_GATEWAY_API_TOKEN", ""),
        )

    def pay_research(self, agent_id: str, idempotency_key: str) -> dict[str, object] | None:
        if self.mode == "simulated":
            return None
        if self.mode != "live":
            raise X402PaymentError("HEDERA_PAYMENT_MODE must be simulated or live.")
        if not self.api_token:
            raise X402PaymentError("HEDERA_GATEWAY_API_TOKEN is required for live x402 payments.")

        body = json.dumps({"agentId": agent_id, "idempotencyKey": idempotency_key}).encode()
        request = Request(
            f"{self.url}/v1/payments/research",
            data=body,
            headers={"Authorization": f"Bearer {self.api_token}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=45) as response:
                payload = json.loads(response.read().decode())
        except HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:500]
            raise X402PaymentError(f"x402 gateway rejected payment with HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise X402PaymentError(f"x402 gateway is unavailable: {exc.reason}") from exc

        if not isinstance(payload, dict) or payload.get("network") != "hedera-testnet" or payload.get("finalized") is not True:
            raise X402PaymentError("x402 gateway returned an unverified settlement receipt.")
        transaction_id = payload.get("transactionId")
        if not isinstance(transaction_id, str) or not transaction_id:
            raise X402PaymentError("x402 gateway receipt has no transaction ID.")
        return payload
