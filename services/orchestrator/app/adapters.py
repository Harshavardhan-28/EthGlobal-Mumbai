"""Chain adapters. Each has a simulated mode (deterministic, labelled) and a live mode
that calls the localhost TypeScript gateways. Decisions never happen here - the
engine decides; adapters only execute and return evidence."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .x402 import X402GatewayClient, X402PaymentError, load_project_env

load_project_env()
DATA_DIR = Path(os.environ.get("ORCHESTRATOR_DATA_DIR", Path(__file__).resolve().parents[3] / ".data"))


class GatewayPolicyError(RuntimeError):
    """The chain rejected the action by policy (e.g. a contract custom error)."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class GatewayUncertain(RuntimeError):
    """The outcome is unknown (timeout, connection loss). Must be reconciled, never retried blindly."""


class GatewayFailed(RuntimeError):
    """The gateway confirmed the operation did not happen."""


def _call(base: str, token: str, method: str, path: str, body: dict[str, Any] | None = None, key: str | None = None, timeout: int = 60) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if key:
        headers["Idempotency-Key"] = key
    request = Request(f"{base}{path}", data=json.dumps(body).encode() if body is not None else None, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode(errors="replace") or "{}")
        except json.JSONDecodeError:
            payload = {}
        if exc.code == 409:
            raise GatewayPolicyError(str(payload.get("error", "REJECTED")), json.dumps(payload)[:300]) from exc
        if exc.code == 404:
            raise GatewayFailed("NOT_FOUND") from exc
        raise GatewayFailed(f"HTTP {exc.code}: {json.dumps(payload)[:300]}") from exc
    except (URLError, TimeoutError, ConnectionError) as exc:
        raise GatewayUncertain(str(getattr(exc, "reason", exc))) from exc


# --------------------------------------------------------------------- HCS-14
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def base58(data: bytes) -> str:
    value = int.from_bytes(data, "big")
    out = ""
    while value:
        value, rem = divmod(value, 58)
        out = _B58[rem] + out
    return "1" * (len(data) - len(data.lstrip(b"\0"))) + out


def uaid(identity: str, name: str, skills: list[int], native_account: str) -> str:
    """HCS-14 UAID (AID method); identical to services/hedera-gateway/src/uaid.ts."""
    native_id = f"hedera:testnet:{native_account}"
    canonical = json.dumps(
        {"name": name.strip(), "nativeId": native_id, "protocol": "x402", "registry": "working-capital", "skills": sorted(skills), "version": "1.0.0"},
        separators=(",", ":"),
        sort_keys=True,
    )
    digest = base58(hashlib.sha384(canonical.encode()).digest())
    return f"uaid:aid:{digest};uid={identity};registry=working-capital;proto=x402;nativeId={native_id}"


DEMO_AGENTS = [("lead", "Northstar Lead Agent", [0, 1]), ("research", "Northstar Research Agent", [0, 7]), ("compute", "Northstar Compute Agent", [0, 9]), ("data", "Northstar Data Agent", [0, 7])]


# --------------------------------------------------------------------- Hedera
class HederaAdapter:
    def __init__(self) -> None:
        self.mode = os.environ.get("HEDERA_PAYMENT_MODE", "simulated")
        self.url = os.environ.get("HEDERA_GATEWAY_URL", "http://127.0.0.1:4022").rstrip("/")
        self.token = os.environ.get("HEDERA_GATEWAY_API_TOKEN", "")
        self.audit_live = self.mode == "live" and bool(os.environ.get("HEDERA_AUDIT_TOPIC_ID", "").strip())
        self.ats_tx = os.environ.get("HEDERA_ATS_ISSUANCE_TX_ID", "").strip()
        self.ats_token = os.environ.get("HEDERA_ATS_TOKEN_ID", "").strip()
        self.native_account = os.environ.get("HEDERA_OPERATOR_ACCOUNT_ID", "").strip() or os.environ.get("HEDERA_AGENT_ACCOUNT_ID", "").strip() or "0.0.0"
        self.fail_next_payment: str | None = None  # test hook: "failed" or "uncertain"
        self._ledger_path = DATA_DIR / "sim-hedera-payments.json"
        self._audit_seq = 0

    # simulated payment ledger survives restarts, like the live gateway's durable store
    def _ledger(self) -> dict[str, Any]:
        try:
            return json.loads(self._ledger_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _ledger_put(self, key: str, receipt: dict[str, Any]) -> None:
        ledger = self._ledger()
        ledger[key] = receipt
        self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._ledger_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(ledger, indent=2))
        tmp.replace(self._ledger_path)

    def label(self) -> str:
        return "Hedera · testnet" if self.mode == "live" else "Hedera · testnet (simulated)"

    def pay_research(self, agent: str, key: str) -> dict[str, Any]:
        if self.mode == "live":
            try:
                receipt = X402GatewayClient.from_environment().pay_research(agent, key)
            except X402PaymentError as exc:
                text = str(exc)
                if "unavailable" in text:
                    raise GatewayUncertain(text) from exc
                raise GatewayFailed(text) from exc
            assert receipt is not None
            return {**receipt, "network": "Hedera · testnet", "simulated": False}
        if self.fail_next_payment:
            mode, self.fail_next_payment = self.fail_next_payment, None
            if mode == "uncertain":
                # The payment "settles" at the facilitator but the response is lost.
                self._ledger_put(key, self._simulated_receipt(key))
                raise GatewayUncertain("Simulated timeout after settlement")
            raise GatewayFailed("Simulated facilitator rejection")
        existing = self._ledger().get(key)
        if existing:
            return existing
        receipt = self._simulated_receipt(key)
        self._ledger_put(key, receipt)
        return receipt

    def _simulated_receipt(self, key: str) -> dict[str, Any]:
        digest = hashlib.sha256(key.encode()).hexdigest()
        tx = f"0.0.0@sim.{int(digest[:12], 16) % 10**9:09d}"
        return {"transactionId": tx, "finalized": True, "network": "Hedera · testnet (simulated)", "explorerUrl": None, "auditTransactionId": f"{tx}-audit", "auditSequence": None, "simulated": True}

    def lookup_payment(self, key: str) -> dict[str, Any] | None:
        if self.mode == "live":
            try:
                from urllib.parse import quote

                result = _call(self.url, self.token, "GET", f"/v1/payments/{quote(key, safe='')}", timeout=15)
            except GatewayFailed:
                return None
            return {**result, "network": "Hedera · testnet", "simulated": False}
        return self._ledger().get(key)

    def audit(self, event_type: str, payload: dict[str, Any], key: str) -> dict[str, Any]:
        if not self.audit_live:
            self._audit_seq += 1
            return {"simulated": True, "transactionId": None, "sequence": None}
        try:
            return _call(self.url, self.token, "POST", "/v1/audit", {"type": event_type, **payload}, key=key, timeout=30)
        except (GatewayFailed, GatewayUncertain) as exc:
            return {"simulated": False, "error": str(exc), "transactionId": None, "sequence": None}

    def verify_ats(self, transaction_id: str) -> dict[str, Any]:
        return _call(self.url, self.token, "POST", "/v1/ats/verify", {"transactionId": transaction_id}, timeout=60)

    def agent_ids(self) -> dict[str, str]:
        return {identity: uaid(identity, name, skills, self.native_account) for identity, name, skills in DEMO_AGENTS}


# --------------------------------------------------------------------- Arc
class ArcAdapter:
    def __init__(self) -> None:
        self.mode = os.environ.get("ARC_EXECUTION_MODE", "simulated")
        self.url = os.environ.get("ARC_GATEWAY_URL", "http://127.0.0.1:4031").rstrip("/")
        self.token = os.environ.get("ARC_GATEWAY_API_TOKEN", "local-fork-token")
        self._config: dict[str, Any] | None = None

    @property
    def live(self) -> bool:
        return self.mode == "gateway"

    def config(self) -> dict[str, Any]:
        if not self.live:
            return {"mode": "simulated", "label": "Arc · simulated", "facility": None}
        if self._config is None:
            raw = _call(self.url, self.token, "GET", "/v1/config", timeout=10)
            self._config = {"mode": raw["mode"], "label": raw["label"], "facility": raw["deployment"]["facility"], "chainId": raw["chainId"], "aquaRouter": raw["deployment"].get("aquaRouter")}
        return self._config

    def _post(self, path: str, body: dict[str, Any], key: str) -> dict[str, Any]:
        if not self.live:
            return {"simulated": True, "txHash": None, "explorerUrl": None}
        return _call(self.url, self.token, "POST", path, body, key=key, timeout=120)

    def open(self, body: dict[str, Any], key: str) -> dict[str, Any]:
        return self._post("/v1/facilities/open", body, key)

    def fund(self, body: dict[str, Any], key: str) -> dict[str, Any]:
        result = self._post("/v1/facilities/fund", body, key)
        if not self.live:
            result.update({"viaAqua": False, "notesMinor": str(body["principalMinor"]), "strategyHash": None})
        return result

    def budgets(self, body: dict[str, Any], key: str) -> dict[str, Any]:
        return self._post("/v1/facilities/budgets", body, key)

    def reserve(self, body: dict[str, Any], key: str) -> dict[str, Any]:
        return self._post("/v1/spends/reserve", body, key)

    def commit(self, body: dict[str, Any], key: str) -> dict[str, Any]:
        return self._post("/v1/spends/commit", body, key)

    def release(self, body: dict[str, Any], key: str) -> dict[str, Any]:
        return self._post("/v1/spends/release", body, key)

    def repay(self, body: dict[str, Any], key: str) -> dict[str, Any]:
        return self._post("/v1/facilities/repay", body, key)

    def status(self, body: dict[str, Any], key: str) -> dict[str, Any]:
        """overdue | default | dispute | resolve (with upheld) on the Arc facility."""
        return self._post("/v1/facilities/status", body, key)

    def reservation(self, facility_key: str, intent_key: str) -> dict[str, Any] | None:
        if not self.live:
            return None
        from urllib.parse import quote

        return _call(self.url, self.token, "GET", f"/v1/spends/{quote(facility_key, safe='')}/{quote(intent_key, safe='')}", timeout=15)
