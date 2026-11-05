> **Update (Nov 2026):** buyer confirmation was replaced by GST verification (GSTIN check digits and e-invoice IRN match), a credit score with step-up limits, compliance gates and overdue/default/dispute handling. See `README.md` and `docs/COMPLIANCE.md`. The lifecycle is now `DRAFT → VERIFIED → TOKENIZED → FUNDING → ACTIVE → REPAID`.

# Autonomous Working Capital System - V1 Implementation Plan

## Summary

Build a demo-ready receivables-backed credit protocol where a buyer-confirmed receivable establishes one shared USDC credit ceiling for an AI-agent team. Agents receive reserved sub-budgets, pay for services autonomously through x402 on Hedera, and customer repayment is routed to lenders before the operator.

V1 targets the core P0 flow plus the 1inch Aqua lender matching slice (P1). P2 dynamic pricing and refinancing follow only after the core flow is dependable.

## Architecture and interfaces

### Workspace

- `apps/web`: Next.js and TypeScript operator dashboard.
- `services/orchestrator`: Python 3.12, FastAPI, Pydantic, SQLAlchemy, and PostgreSQL for deployed domain orchestration and read models. The local demo may use SQLite.
- `services/hedera-gateway`: TypeScript adapter for Hedera ATS, HCS, Mirror Node, HCS-14, and x402.
- `contracts/arc`: Foundry contracts created and first tested through Arc Studio.
- `contracts/aqua`: pinned official Aqua and SwapVM dependencies plus the receivable-credit app.
- `packages/shared`: API types, event schemas, ABIs, and demo fixtures.
- `tests/e2e`: browser-level demonstration and failure-path coverage.

Use append-only domain events and normalized PostgreSQL read models. Store monetary values as integer token base units, rates as basis points, and timestamps in UTC. Do not place signing keys in the browser or source control.

### Domain records

- `Receivable`: canonical ID, buyer, operator, face value, currency, issue/due dates, lifecycle status, ATS token, HCS sequence, and verification expiry.
- `LenderOffer`: lender, available principal, buyer-score threshold, maximum advance rate and tenor, APR, expiry, and Aqua strategy hash.
- `Facility`: receivable ID, borrowing base, funded principal, outstanding debt, status, Arc contract, and lender allocations.
- `AgentBudget`: facility, agent identity, allocation, spent and reserved amounts, transaction and daily limits, and merchant allowlist.
- `SpendIntent`: agent, merchant, amount, status, Arc reservation, Hedera payment, and HCS audit sequence.
- `FundingAllocation`: lender, principal, APR, fee, note balance, and repayment status.

### Chain responsibilities

1. **Hedera:** issue a receivable asset using Asset Tokenization Studio (ATS); enforce one canonical invoice ID and lifecycle; publish buyer verification, agent spend, rejection, and repayment events to HCS; associate agents with HCS-14 profiles; settle one x402-protected service request through a listed facilitator.
2. **Arc:** build from an Arc Studio project; hold USDC in a credit facility; enforce borrowing-base and receivable uniqueness; issue lender notes; manage budgets and spend reservations; route repayments to lender obligations before the operator remainder.
3. **Aqua / SwapVM:** use the official pinned protocols for lender positions and a custom receivable-credit instruction. The solver filters offers by buyer score, tenor, advance rate, liquidity, and expiry, then selects the lowest effective cost first.

### Cross-chain coordination

Hedera and Arc do not provide native atomic interoperability for this prototype. A coordinator observes finalized Hedera transactions through Mirror Node and submits a short-lived EIP-712 attestation to Arc containing the source transaction, receivable/spend ID, amount, HCS sequence, and expiry. Arc rejects expired and replayed attestations.

Spending is a two-phase flow: reserve the amount on Arc, make the x402 payment on Hedera, then commit the reservation on success or release it on a confirmed failure/timeout. The UI and README must identify the coordinator as a trust assumption.

### API surface

- `POST /api/receivables`
- `POST /api/receivables/{id}/confirm`
- `POST /api/facilities/{receivableId}/fund`
- `POST /api/facilities/{id}/budgets`
- `POST /api/spend-intents`
- `POST /api/spend-intents/{id}/execute`
- `POST /api/facilities/{id}/repay`
- `GET /api/demo/{id}`
- `GET /api/events/stream` (server-sent events)

Mutations accept an idempotency key and return updated domain state plus transaction/explorer links.

## Development phases

### Phase 0 - Foundation and feasibility

- Initialize the repository, CI, formatting, type checks, secret scanning, environment examples, and meaningful incremental commits.
- Create the Arc Studio project first and record its URL.
- Spike ATS issuance, one Hedera x402 settlement, and official Aqua/SwapVM execution on Arc testnet.
- Pin protocol versions and record chain IDs, token addresses, contract addresses, explorer links, and fallback conditions.
- Gate: ATS issuance, x402 payment, and Arc Studio deployment each work independently before feature work.

### Phase 1 - Deterministic domain core

- Implement the receivable lifecycle `DRAFT -> BUYER_CONFIRMED -> TOKENIZED -> FUNDING -> ACTIVE -> REPAID`.
- Add canonical IDs, stale-claim rules, borrowing-base calculation, offer eligibility, cheapest-first allocation, fee calculation, spend reservation, and repayment allocation.
- Keep credit decisions deterministic; AI can explain a decision but cannot approve it.
- Seed RetailCo, Alice, Bob, Charlie, and Research, Compute, and Data agents.
- Gate: domain behavior runs without a chain and reproduces the example funding, payment, rejection, and repayment.

### Phase 2 - Hedera verification and agent payment

- Create operator, buyer, merchant, lead-agent, and subagent testnet identities.
- Construct a single-unit, USDC-denominated ATS bond after buyer confirmation. Store the canonical receivable ID and buyer account in its metadata, require issuer-approved asset identification, and require an issuer wallet review before submission.
- Register HCS-14 identities and create the audit topic.
- Implement the paid research endpoint and autonomous x402 client.
- Route live x402 payment requests through a localhost-only, token-authenticated gateway. Persist finalized settlement receipts and HCS audit IDs before a spend is marked settled; use durable idempotency and reconciliation before production.
- Use Google ADK only for bounded orchestration and explanation. ADK may prepare an issuance request, but deterministic policy and an issuer wallet authorize every ATS transaction.
- Gate: an agent pays without human approval, receives the service response, and yields a Hedera transaction ID and HCS audit event.

### Phase 3 - Arc facility and settlement

- Generate the initial facility, budget, lender-note, and repayment contracts through Arc Studio, then keep their reviewed source and tests in the repository.
- Add signed Hedera attestations, expiry, replay protection, USDC facility funding, and the repayment waterfall.
- Gate: Arc prevents duplicate claims and over-borrowing, reserves agent budgets, and pays lenders before the operator.

### Phase 4 - Aqua lender market

- Use the official Aqua and SwapVM SDKs on Arc chain ID 5042. Filter lender strategies by buyer score, advance rate, tenor, expiry, and liquidity, then select the lowest APR first.
- Build a lender-reviewed Aqua `ship` transaction from a supplied strategy. Persist the strategy hash and keep the plan `QUOTED` until the chain receipt is reconciled.
- Add the custom receivable-credit SwapVM instruction only after its bytecode, invariants, and adversarial tests are reviewed.
- Match Alice and Bob to the funding request while leaving Charlie unused.
- Persist strategy hashes, lender fills, and token-transfer transaction IDs.
- Gate: official contracts execute token transfers on Arc testnet; if compatibility blocks this, use the same official contracts on a supported local fork and label that boundary in the demo and README.

### Phase 5 - Orchestration and reconciliation

- Connect the Python agents to the TypeScript Hedera gateway and Arc contracts.
- Implement reserve, pay, confirm, and audit with safe retries, receipt polling, timeout release, and reconciliation for interrupted runs.
- Gate: restarting a service during an in-flight payment cannot duplicate the payment or permanently consume budget.

### Phase 6 - Demo dashboard

- Show Verify, Fund, Spend, and Repay as the primary progress rail.
- Display receivable evidence, borrowing base, lender matching, facility utilization, agent budgets, x402 receipt, policy rejection, and repayment waterfall.
- Add deterministic actions: `Confirm invoice`, `Match lenders`, `Run research agent`, `Attempt overspend`, and `Repay facility`.
- Link every real transaction to its explorer and label any simulated/local-fork action.
- Gate: the complete seven-step demo can be reset and replayed through the dashboard.

### Phase 7 - Hardening and submission

- Add contract unit, fuzz, invariant, integration, and browser E2E coverage.
- Rehearse the complete flow with fresh test wallets and confirm explorer evidence.
- Document setup, architecture, trust assumptions, addresses, transaction IDs, and prize tracks in README.
- Include an architecture diagram, reset script, and concise presentation script; record a demo under five minutes.
- Preserve incremental commit history for the 1inch track.

## Acceptance and verification

- Duplicate receivable IDs, replayed/expired attestations, stale claims, excess advances, ineligible offers, and unauthorized transitions are rejected.
- Global credit, agent allocation, transaction, daily, and merchant policies are independently enforced.
- A failed x402 payment releases its reservation; a successful one commits exactly once.
- Repayment pays lender principal and the term-based fee before any operator remainder, including partial-repayment cases.
- API retries, Mirror Node lag, RPC errors, and service restarts do not corrupt the lifecycle.
- Browser rehearsal completes the full flow, shows the successful $0.25 research payment, rejects the $5,500 compute attempt with `AGENT_BUDGET_EXCEEDED`, demonstrates a separate global-limit rejection, and ends with zero debt and a repaid receivable.
- Inspect console/network errors, keyboard access, responsive layouts, overflow, and transaction links.

## Assumptions and fixed decisions

- The implementation uses Python for orchestration, TypeScript for the web and Hedera gateway, Solidity for contracts, PostgreSQL for off-chain state, and Foundry for contract work.
- Demo wallets are pre-funded and server managed; browser code never receives private keys.
- Arc Studio is a required starting point for Arc submissions. Its project URL and use in the demo are submission deliverables.
- ATS is used for Hedera receivable tokenization; x402 uses a listed Hedera facilitator and must settle on testnet for the prize path.
- Arc, Hedera, and 1inch operate across separate chain environments; cross-chain messages rely on a coordinator attestation in v1.
- The sample repayment fee is calculated from principal, APR, and actual term. The brief's flat $100 example is illustrative and is not the lender fee rule.
- A $5,500 request against the $3,000 Compute allocation fails as `AGENT_BUDGET_EXCEEDED`. A separate request is used to show `GLOBAL_CREDIT_LIMIT_EXCEEDED`.
- P2 dynamic pricing and flash refinancing are deferred until every P0 and P1 acceptance gate passes.
