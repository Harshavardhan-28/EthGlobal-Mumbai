from __future__ import annotations

import os
from pathlib import Path
import json
from typing import Any
from urllib.request import Request, urlopen

from dotenv import load_dotenv


def load_project_env() -> None:
    for directory in Path(__file__).resolve().parents:
        candidate = directory / ".env"
        if candidate.is_file():
            load_dotenv(candidate)
            return


load_project_env()


def _configured(*names: str) -> bool:
    return all(os.environ.get(name, "").strip() for name in names)


def _arc_gateway() -> dict[str, Any] | None:
    if os.environ.get("ARC_EXECUTION_MODE", "simulated") != "gateway":
        return None
    try:
        request = Request(os.environ.get("ARC_GATEWAY_URL", "http://127.0.0.1:4031").rstrip("/") + "/v1/config", headers={"Authorization": f"Bearer {os.environ.get('ARC_GATEWAY_API_TOKEN', 'local-fork-token')}"})
        with urlopen(request, timeout=2) as response:
            return json.loads(response.read().decode())
    except Exception:  # noqa: BLE001
        return {"error": "unreachable"}


def _aqua_status() -> dict[str, Any]:
    cfg = _arc_gateway()
    if cfg is None:
        return {"label": "1inch Aqua", "mode": "simulated", "ready": False, "detail": "Run the Arc fork and gateway (ARC_EXECUTION_MODE=gateway) to fill lenders through the official Aqua router"}
    if "error" in cfg:
        return {"label": "1inch Aqua", "mode": "gateway-down", "ready": False, "detail": "Arc gateway is not reachable on ARC_GATEWAY_URL"}
    router = cfg.get("deployment", {}).get("aquaRouter")
    if cfg.get("mode") == "fork" and router:
        return {"label": "1inch Aqua", "mode": "arc-fork-5042", "ready": True, "detail": f"Official AquaSwapVMRouter {router[:10]}… on a local Arc mainnet fork · TestUSDC"}
    return {"label": "1inch Aqua", "mode": "direct-funding", "ready": False, "detail": "Arc testnet has no Aqua deployment; lenders fund the facility directly"}


def _arc_status() -> dict[str, Any]:
    cfg = _arc_gateway()
    if cfg is None:
        return {"label": "Arc facility", "mode": "simulated", "ready": False, "detail": "Contract calls simulated until ARC_EXECUTION_MODE=gateway"}
    if "error" in cfg:
        return {"label": "Arc facility", "mode": "gateway-down", "ready": False, "detail": "Arc gateway is not reachable"}
    facility = cfg.get("deployment", {}).get("facility", "")
    return {"label": "Arc facility", "mode": cfg.get("mode", "?"), "ready": True, "detail": f"{cfg.get('label')} · contract {facility[:10]}…"}


def integration_status() -> dict[str, Any]:
    payment_mode = os.environ.get("HEDERA_PAYMENT_MODE", "simulated")
    x402_ready = payment_mode == "live" and _configured(
        "HEDERA_AGENT_ACCOUNT_ID",
        "HEDERA_AGENT_PRIVATE_KEY",
        "HEDERA_RESEARCH_MERCHANT_ID",
        "HEDERA_AUDIT_OPERATOR_ID",
        "HEDERA_AUDIT_OPERATOR_KEY",
        "HEDERA_AUDIT_TOPIC_ID",
        "HEDERA_GATEWAY_API_TOKEN",
    )
    return {
        "arc": _arc_status(),
        "x402": {
            "label": "Hedera x402",
            "mode": payment_mode,
            "ready": x402_ready,
            "detail": "Live testnet payment can be submitted" if x402_ready else "Simulated until payer, merchant, audit, and gateway settings are configured",
        },
        "ats": {
            "label": "Hedera ATS",
            "mode": "wallet-required",
            "ready": _configured("HEDERA_ATS_CONFIG_ID", "HEDERA_ATS_CONFIG_VERSION", "HEDERA_ATS_ISSUER_ACCOUNT_ID"),
            "detail": "Issuer wallet review is ready" if _configured("HEDERA_ATS_CONFIG_ID", "HEDERA_ATS_CONFIG_VERSION", "HEDERA_ATS_ISSUER_ACCOUNT_ID") else "Issuer config and wallet connection required",
        },
        "aqua": _aqua_status(),
        "adk": {
            "label": "Google ADK",
            "mode": "bounded",
            "ready": _configured("GOOGLE_API_KEY"),
            "detail": "Coordinator can run with Gemini" if _configured("GOOGLE_API_KEY") else "Google API key required to run the coordinator",
        },
    }
