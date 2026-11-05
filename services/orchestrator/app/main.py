from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

import hmac

from .adapters import GatewayFailed, GatewayPolicyError, GatewayUncertain
from .compliance import POLICIES
from .engine import USDC, PolicyError, TransitionError
from .verification import ELIGIBILITY_RULES, TIERS
from .integrations import integration_status
from .service import DEMO_ID, Orchestrator, default_orchestrator

DEFAULT_CORS_ORIGINS = ("http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:3001", "http://127.0.0.1:3001")


def cors_origins() -> list[str]:
    configured = os.environ.get("CORS_ORIGINS", "")
    if not configured.strip():
        return list(DEFAULT_CORS_ORIGINS)
    return [origin.strip().rstrip("/") for origin in configured.split(",") if origin.strip()]


orchestrator: Orchestrator = default_orchestrator()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Resolve any payment interrupted by a previous shutdown before serving traffic.
    try:
        orchestrator.reconcile(DEMO_ID)
    except Exception:  # noqa: BLE001 - a down gateway must not stop the API from starting
        pass
    yield


app = FastAPI(title="Autonomous Working Capital API", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Idempotency-Key", "Authorization"],
)


def guarded(key: str | None, operation) -> dict[str, Any]:
    """Idempotent, typed-error wrapper for every mutation."""
    cached = orchestrator.store.idempotent(key)
    if cached:
        return cached
    try:
        result = operation()
    except PolicyError as exc:
        raise HTTPException(status_code=409, detail=f"{exc.code} · {exc.detail}" if exc.detail != exc.code else exc.code) from exc
    except TransitionError as exc:
        raise HTTPException(status_code=409, detail=exc.detail) from exc
    except GatewayPolicyError as exc:
        raise HTTPException(status_code=409, detail=f"Arc contract rejected: {exc.code}") from exc
    except (GatewayFailed, GatewayUncertain) as exc:
        raise HTTPException(status_code=502, detail=f"Chain gateway error: {exc}") from exc
    orchestrator.store.remember(key, result)
    return result


class ActionRequest(BaseModel):
    action: str = Field(min_length=1, max_length=64)


class VerifyRequest(BaseModel):
    irn: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$", description="E-invoice IRN from the IRP. Omit in the demo to use a simulated one.")


class DisputeRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class ResolveRequest(BaseModel):
    upheld: bool


class PauseRequest(BaseModel):
    paused: bool
    reason: str = Field(default="", max_length=200)


class TokenizeRequest(BaseModel):
    atsTransactionId: str | None = Field(default=None, max_length=128)
    tokenId: str | None = Field(default=None, max_length=128)


class SpendRequest(BaseModel):
    facilityId: str = DEMO_ID
    agent: str = Field(min_length=1, max_length=32)
    merchant: str = Field(min_length=1, max_length=64)
    amount: str = Field(pattern=r"^\d+(\.\d{1,6})?$", description="USDC decimal amount")


class RepayRequest(BaseModel):
    amount: str | None = Field(default=None, pattern=r"^\d+(\.\d{1,6})?$")


class CreateReceivableRequest(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9-]{3,64}$")


def to_minor(amount: str) -> int:
    whole, _, fraction = amount.partition(".")
    return int(whole) * USDC + int((fraction + "000000")[:6])


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "modes": orchestrator.modes()}


@app.get("/api/integrations")
def get_integrations() -> dict[str, Any]:
    return integration_status()


# ---------------------------------------------------------------- plan API surface
@app.post("/api/receivables")
def create_receivable(body: CreateReceivableRequest, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
    def run():
        orchestrator.create(body.id)
        return orchestrator.view(body.id)
    return guarded(idempotency_key, run)


@app.post("/api/receivables/{receivable_id}/verify")
def verify_receivable(receivable_id: str, body: VerifyRequest | None = None, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
    """GST verification + eligibility + screening + credit score. No buyer action needed."""
    return guarded(idempotency_key, lambda: {**orchestrator.verify(receivable_id, (body or VerifyRequest()).irn), "state": orchestrator.view(receivable_id)})


@app.post("/api/receivables/{receivable_id}/tokenize")
def tokenize_receivable(receivable_id: str, body: TokenizeRequest | None = None, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
    body = body or TokenizeRequest()
    return guarded(idempotency_key, lambda: {**orchestrator.tokenize(receivable_id, body.atsTransactionId, body.tokenId), "state": orchestrator.view(receivable_id)})


@app.post("/api/facilities/{receivable_id}/fund")
def fund_facility(receivable_id: str, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
    return guarded(idempotency_key, lambda: {**orchestrator.fund(receivable_id), "state": orchestrator.view(receivable_id)})


@app.post("/api/facilities/{facility_id}/budgets")
def facility_budgets(facility_id: str) -> dict[str, Any]:
    # Budgets are assigned atomically with activation during funding; this returns them.
    return {"agents": orchestrator.view(facility_id)["agents"]}


@app.post("/api/spend-intents")
def create_spend_intent(body: SpendRequest, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
    return guarded(idempotency_key, lambda: {"intent": orchestrator.create_intent(body.facilityId, body.agent, body.merchant, to_minor(body.amount)), "state": orchestrator.view(body.facilityId)})


@app.post("/api/spend-intents/{intent_id}/execute")
def execute_spend_intent(intent_id: str, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
    facility_id = intent_id.rsplit(".si-", 1)[0]
    return guarded(idempotency_key, lambda: {**orchestrator.execute_intent(facility_id, intent_id), "state": orchestrator.view(facility_id)})


@app.post("/api/facilities/{facility_id}/repay")
def repay_facility(facility_id: str, body: RepayRequest | None = None, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
    amount = to_minor(body.amount) if body and body.amount else None
    return guarded(idempotency_key, lambda: {**orchestrator.repay(facility_id, amount), "state": orchestrator.view(facility_id)})


@app.post("/api/facilities/{facility_id}/assess")
def assess_facility(facility_id: str) -> dict[str, Any]:
    """Overdue/default check. Safe for anyone to call; it only reads the clock."""
    return guarded(None, lambda: {**orchestrator.assess(facility_id), "state": orchestrator.view(facility_id)})


@app.post("/api/facilities/{facility_id}/dispute")
def dispute_facility(facility_id: str, body: DisputeRequest, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
    return guarded(idempotency_key, lambda: {**orchestrator.dispute(facility_id, body.reason), "state": orchestrator.view(facility_id)})


@app.post("/api/facilities/{facility_id}/dispute/resolve")
def resolve_dispute(facility_id: str, body: ResolveRequest, authorization: str | None = Header(default=None), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
    require_admin(authorization)
    return guarded(idempotency_key, lambda: {**orchestrator.resolve_dispute(facility_id, body.upheld), "state": orchestrator.view(facility_id)})


def require_admin(authorization: str | None) -> None:
    token = os.environ.get("ADMIN_API_TOKEN", "")
    if not token:
        raise HTTPException(status_code=403, detail="ADMIN_API_TOKEN is not configured, so admin actions are disabled.")
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(supplied, token):
        raise HTTPException(status_code=401, detail="UNAUTHORIZED")


@app.post("/api/admin/pause")
def admin_pause(body: PauseRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Compliance pause: stops verification, funding and spending. Repayment keeps working."""
    require_admin(authorization)
    return orchestrator.set_paused(body.paused, body.reason)


@app.get("/api/credit/{borrower_id}")
def get_credit(borrower_id: str) -> dict[str, Any]:
    return orchestrator.credit_view(borrower_id)


@app.get("/api/policies")
def get_policies() -> dict[str, Any]:
    return {"eligibility": ELIGIBILITY_RULES, "evidenceTiers": TIERS, "compliance": POLICIES, "paused": orchestrator.paused()}


@app.post("/api/reconcile")
def reconcile(facility_id: str = DEMO_ID) -> dict[str, Any]:
    results = orchestrator.reconcile(facility_id)
    return {"resolved": len(results), "state": orchestrator.view(facility_id)}


@app.get("/api/receivables/{receivable_id}/log")
def domain_log(receivable_id: str) -> dict[str, Any]:
    return {"items": orchestrator.store.log(receivable_id)}


# ---------------------------------------------------------------- dashboard
@app.get("/api/demo/{demo_id}")
def get_demo(demo_id: str) -> dict[str, Any]:
    try:
        return orchestrator.view(demo_id)
    except PolicyError as exc:
        raise HTTPException(status_code=404, detail="Demo not found") from exc


@app.post("/api/demo/{demo_id}/reset")
def reset_demo(demo_id: str) -> dict[str, Any]:
    if demo_id != DEMO_ID:
        raise HTTPException(status_code=404, detail="Demo not found")
    return orchestrator.reset(demo_id)


@app.post("/api/demo/{demo_id}/actions")
def run_demo_action(demo_id: str, request: ActionRequest, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
    if demo_id != DEMO_ID:
        raise HTTPException(status_code=404, detail="Demo not found")
    return guarded(idempotency_key, lambda: orchestrator.demo_action(request.action, demo_id))


@app.get("/api/events")
def get_events(facility_id: str = DEMO_ID) -> dict[str, Any]:
    return {"items": orchestrator.store.activity(facility_id)}


@app.get("/api/events/stream")
async def stream_events(request: Request, facility_id: str = DEMO_ID) -> StreamingResponse:
    async def events():
        last = 0
        while not await request.is_disconnected():
            for event in reversed(orchestrator.store.activity(facility_id, 50, last)):
                last = max(last, event["seq"])
                yield f"id: {event['seq']}\nevent: activity\ndata: {json.dumps(event, separators=(',', ':'))}\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
