/**
 * Localhost-only, token-authenticated Arc gateway used by the Python orchestrator.
 * Every mutation requires an Idempotency-Key; completed results are persisted so a
 * restarted gateway replays the receipt instead of re-submitting a transaction.
 */
import express from "express";
import { join } from "node:path";
import { createRequire } from "node:module";
import { DurableStore, IdempotentExecutor, bearerMatches } from "@working-capital/shared";
import { selectAquaFunding, type LenderOffer } from "@working-capital/aqua-gateway";
import { loadArcConfig, type LenderId } from "./config.js";
import { ArcPolicyError, FacilityClient, type StatusAction } from "./facility.js";

const require = createRequire(import.meta.url);
const fixture = require("@working-capital/shared/fixtures/retailco-demo.json");
const config = loadArcConfig();
const arc = new FacilityClient(config);
// One ledger per deployed facility: after an Anvil restart and a fresh fork:setup the
// contract address changes, so receipts from the old chain can never be replayed.
const executor = new IdempotentExecutor<Record<string, unknown>>(new DurableStore(join(config.dataDir, `arc-gateway-${config.mode}-${arc.deployment.facility.slice(2, 10).toLowerCase()}.json`)));

const app = express();
app.disable("x-powered-by");
app.use(express.json({ limit: "16kb" }));

const big = (value: unknown, name: string): bigint => {
  if (typeof value !== "string" && typeof value !== "number") throw new Error(`${name} is required`);
  const parsed = BigInt(value);
  if (parsed < 0n) throw new Error(`${name} must be non-negative`);
  return parsed;
};
const str = (value: unknown, name: string): string => {
  if (typeof value !== "string" || !value.trim() || value.length > 200) throw new Error(`${name} is required`);
  return value;
};

app.get("/health", (_req, res) => res.json({ status: "ok", mode: config.mode, chainId: config.chainId, facility: arc.deployment.facility }));

app.use((req, res, next) => {
  if (!bearerMatches(req.header("authorization"), config.apiToken)) {
    res.status(401).json({ error: "UNAUTHORIZED" });
    return;
  }
  next();
});

app.get("/v1/config", (_req, res) => {
  res.json({
    mode: config.mode,
    chainId: config.chainId,
    deployment: arc.deployment,
    operator: config.operator.address,
    coordinator: config.coordinator.address,
    treasury: config.treasury,
    label: config.mode === "fork" ? "Arc mainnet fork (local) · official Aqua contracts · TestUSDC" : "Arc testnet",
  });
});

/** Cheapest-eligible quote over the lender strategies shipped for this deployment. */
app.post("/v1/aqua/quote", (req, res) => {
  try {
    const offers: LenderOffer[] = fixture.lenders.map((lender: any) => ({
      lenderId: lender.id,
      makerAddress: config.lenders[lender.id as LenderId].address,
      strategy: arc.deployment.lenders[lender.id as LenderId]?.encodedOrder ?? "0x00",
      usdcLiquidityMinor: BigInt(lender.availableMinor),
      aprBps: lender.aprBps,
      minBuyerScore: lender.minBuyerScore,
      maxAdvanceRateBps: lender.maxAdvanceRateBps,
      maxTenorDays: lender.maxTenorDays,
      expiresAt: new Date((arc.deployment.lenders[lender.id as LenderId]?.expiresAt ?? Date.now() / 1000 + 86400) * 1000).toISOString(),
    }));
    const plan = selectAquaFunding(offers, {
      receivableId: str(req.body.receivableKey, "receivableKey"),
      borrowerAddress: arc.deployment.facility,
      requestedMinor: big(req.body.requestedMinor, "requestedMinor"),
      buyerScore: req.body.buyerScore,
      advanceRateBps: Number(req.body.advanceRateBps),
      tenorDays: Number(req.body.tenorDays),
      asOf: new Date().toISOString(),
    });
    res.json(JSON.parse(JSON.stringify(plan, (_k, v) => (typeof v === "bigint" ? v.toString() : v))));
  } catch (error) {
    res.status(400).json({ error: error instanceof Error ? error.message : String(error) });
  }
});

function mutation(path: string, handler: (body: any) => Promise<Record<string, unknown>>) {
  app.post(path, async (req, res) => {
    const key = req.header("idempotency-key");
    if (!key || key.length > 255) {
      res.status(400).json({ error: "IDEMPOTENCY_KEY_REQUIRED" });
      return;
    }
    try {
      const { result, replayed } = await executor.run(`${path}:${key}`, () => handler(req.body ?? {}));
      res.json({ ...result, replayed });
    } catch (error) {
      if (error instanceof ArcPolicyError) {
        res.status(409).json({ error: error.code, contractError: error.contractError });
        return;
      }
      res.status(502).json({ error: error instanceof Error ? error.message.split("\n")[0] : String(error) });
    }
  });
}

mutation("/v1/facilities/open", async (b) => arc.openFacility({
  facilityKey: str(b.facilityKey, "facilityKey"),
  receivableKey: str(b.receivableKey, "receivableKey"),
  faceValueMinor: big(b.faceValueMinor, "faceValueMinor"),
  advanceRateBps: Number(b.advanceRateBps),
  maturity: Number(b.maturity),
  verificationExpiry: Number(b.verificationExpiry),
  hederaTxId: str(b.hederaTxId, "hederaTxId"),
  hcsSequence: Number(b.hcsSequence ?? 0),
}));

mutation("/v1/facilities/fund", async (b) => arc.fund({
  facilityKey: str(b.facilityKey, "facilityKey"),
  lenderId: str(b.lenderId, "lenderId") as LenderId,
  principalMinor: big(b.principalMinor, "principalMinor"),
  aprBps: Number(b.aprBps),
  minTermDays: Number(b.minTermDays ?? 30),
}));

mutation("/v1/facilities/budgets", async (b) => {
  const facilityKey = str(b.facilityKey, "facilityKey");
  const results = [];
  for (const item of b.budgets ?? []) {
    results.push(await arc.assignBudget({
      facilityKey,
      agentIdentity: str(item.agentIdentity, "agentIdentity"),
      allocationMinor: big(item.allocationMinor, "allocationMinor"),
      perTxMinor: big(item.perTxMinor ?? 0, "perTxMinor"),
      dailyMinor: big(item.dailyMinor ?? 0, "dailyMinor"),
      merchants: item.merchants ?? [],
    }));
  }
  if (b.leadMerchant) await arc.allowLeadMerchant(facilityKey, str(b.leadMerchant, "leadMerchant"));
  return { budgets: results };
});

mutation("/v1/spends/reserve", async (b) => arc.reserve({
  facilityKey: str(b.facilityKey, "facilityKey"),
  intentKey: str(b.intentKey, "intentKey"),
  agentIdentity: str(b.agentIdentity, "agentIdentity"),
  merchant: str(b.merchant, "merchant"),
  amountMinor: big(b.amountMinor, "amountMinor"),
  ttlSeconds: Number(b.ttlSeconds ?? 300),
}, b.dryRun === true));

mutation("/v1/spends/commit", async (b) => arc.commit({
  facilityKey: str(b.facilityKey, "facilityKey"),
  intentKey: str(b.intentKey, "intentKey"),
  amountMinor: big(b.amountMinor, "amountMinor"),
  hederaTxId: str(b.hederaTxId, "hederaTxId"),
  hcsSequence: Number(b.hcsSequence ?? 0),
}));

mutation("/v1/spends/release", async (b) => arc.release({ facilityKey: str(b.facilityKey, "facilityKey"), intentKey: str(b.intentKey, "intentKey") }));

mutation("/v1/facilities/repay", async (b) => arc.repay({ facilityKey: str(b.facilityKey, "facilityKey"), amountMinor: big(b.amountMinor, "amountMinor") }));

mutation("/v1/facilities/status", async (b) => {
  const action = str(b.action, "action") as StatusAction;
  if (!["overdue", "default", "dispute", "resolve"].includes(action)) throw new Error("action must be overdue, default, dispute or resolve");
  return arc.status({ facilityKey: str(b.facilityKey, "facilityKey"), action, upheld: b.upheld === true });
});

app.get("/v1/spends/:facilityKey/:intentKey", async (req, res) => {
  try {
    res.json(await arc.reservation(req.params.facilityKey, req.params.intentKey));
  } catch (error) {
    res.status(502).json({ error: error instanceof Error ? error.message : String(error) });
  }
});

app.get("/v1/facilities/:facilityKey", async (req, res) => {
  try {
    res.json(await arc.facilityState(req.params.facilityKey));
  } catch (error) {
    res.status(502).json({ error: error instanceof Error ? error.message : String(error) });
  }
});

const host = process.env.ARC_GATEWAY_BIND_HOST?.trim() || "127.0.0.1";
app.listen(config.port, host, () => {
  process.stdout.write(`Arc gateway (${config.mode}, chain ${config.chainId}) listening on http://${host}:${config.port}\n`);
});
