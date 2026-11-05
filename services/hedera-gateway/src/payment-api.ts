/**
 * Localhost-only, token-authenticated Hedera gateway used by the orchestrator.
 *   POST /v1/payments/research   autonomous x402 payment (+ HCS audit), idempotent and durable
 *   GET  /v1/payments/:key        reconciliation lookup for an interrupted payment
 *   POST /v1/audit                publish a compact lifecycle event to the HCS audit topic
 *   POST /v1/ats/verify           verify an issuer-submitted ATS transaction on Mirror Node
 *   GET  /v1/agents               HCS-14 UAIDs for the agent team
 * Completed payments are persisted, so a restart replays receipts instead of paying twice.
 */
import express from "express";
import { join } from "node:path";
import { DurableStore, IdempotentExecutor, bearerMatches } from "@working-capital/shared";
import { createHederaIntegration, loadHederaConfig, publishAuditEvent, RESEARCH_PAYMENT_MINOR, type ChainReceipt } from "./index.js";
import { getMirrorTransaction } from "./mirror.js";
import { demoAgents, uaid } from "./uaid.js";

const config = loadHederaConfig();
const apiToken = process.env.HEDERA_GATEWAY_API_TOKEN?.trim();
const port = Number(process.env.HEDERA_GATEWAY_PORT ?? "4022");
if (!apiToken) throw new Error("Missing required Hedera configuration: HEDERA_GATEWAY_API_TOKEN");
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("HEDERA_GATEWAY_PORT must be a valid TCP port.");

const dataDir = process.env.HEDERA_GATEWAY_DATA_DIR?.trim() || join(process.cwd(), "../../.data");
const payments = new DurableStore<ChainReceipt>(join(dataDir, "hedera-payments.json"));
const paymentExecutor = new IdempotentExecutor(payments);
const auditExecutor = new IdempotentExecutor(new DurableStore<ChainReceipt>(join(dataDir, "hedera-audit.json")));

const app = express();
app.disable("x-powered-by");
app.use(express.json({ limit: "8kb", type: "application/json" }));

app.get("/health", (_request, response) => response.json({ status: "ok", network: config.network, mode: "live" }));

app.use((request, response, next) => {
  if (!bearerMatches(request.header("authorization"), apiToken)) {
    response.status(401).json({ error: "UNAUTHORIZED" });
    return;
  }
  next();
});

const validKey = (value: unknown): value is string => typeof value === "string" && value.length > 0 && value.length <= 255;

app.post("/v1/payments/research", async (request, response) => {
  const agentId = typeof request.body?.agentId === "string" ? request.body.agentId : "";
  const idempotencyKey = request.body?.idempotencyKey;
  if (!agentId || !validKey(idempotencyKey)) {
    response.status(400).json({ error: "INVALID_PAYMENT_REQUEST" });
    return;
  }
  try {
    const { result, replayed } = await paymentExecutor.run(idempotencyKey, async () => {
      const integration = createHederaIntegration(config);
      try {
        return await integration.payX402({ agentId, merchantUrl: config.researchUrl, amountMinor: RESEARCH_PAYMENT_MINOR, token: "USDC", idempotencyKey });
      } finally {
        integration.close();
      }
    });
    response.json({ ...result, replayed });
  } catch (error) {
    response.status(502).json({ error: "X402_PAYMENT_FAILED", detail: error instanceof Error ? error.message : String(error) });
  }
});

app.get("/v1/payments/:key", (request, response) => {
  const receipt = payments.get(request.params.key);
  if (!receipt) {
    response.status(404).json({ status: "UNKNOWN" });
    return;
  }
  response.json({ status: "SETTLED", ...receipt });
});

app.post("/v1/audit", async (request, response) => {
  const key = request.header("idempotency-key");
  const type = request.body?.type;
  if (!validKey(key) || typeof type !== "string") {
    response.status(400).json({ error: "INVALID_AUDIT_REQUEST" });
    return;
  }
  try {
    const { result, replayed } = await auditExecutor.run(key, () => publishAuditEvent({ ...request.body, network: config.network, at: new Date().toISOString() }));
    response.json({ ...result, replayed });
  } catch (error) {
    response.status(502).json({ error: "HCS_AUDIT_FAILED", detail: error instanceof Error ? error.message : String(error) });
  }
});

app.post("/v1/ats/verify", async (request, response) => {
  const transactionId = request.body?.transactionId;
  if (!validKey(transactionId)) {
    response.status(400).json({ error: "INVALID_TRANSACTION_ID" });
    return;
  }
  try {
    const tx = await getMirrorTransaction(transactionId);
    if (tx.result !== "SUCCESS") {
      response.status(409).json({ error: "ATS_TRANSACTION_NOT_SUCCESSFUL", result: tx.result });
      return;
    }
    response.json({ verified: true, transactionId, result: tx.result, name: tx.name, consensusTimestamp: tx.consensus_timestamp, entityId: tx.entity_id ?? null, explorerUrl: `https://hashscan.io/testnet/transaction/${encodeURIComponent(transactionId)}` });
  } catch (error) {
    response.status(502).json({ error: "MIRROR_NODE_UNAVAILABLE", detail: error instanceof Error ? error.message : String(error) });
  }
});

app.get("/v1/agents", (_request, response) => {
  response.json({ agents: demoAgents(process.env.HEDERA_OPERATOR_ACCOUNT_ID?.trim() || config.payerAccountId).map((agent) => ({ identity: agent.identity, name: agent.name, uaid: uaid(agent) })) });
});

const bindHost = process.env.HEDERA_BIND_HOST?.trim() || "127.0.0.1";
app.listen(port, bindHost, () => {
  process.stdout.write(`Hedera gateway listening on http://${bindHost}:${port}\n`);
});
