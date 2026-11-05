/**
 * Phase 3 + 4 gate on the local Arc fork: opens the RetailCo facility, fills Alice and Bob
 * through the official Aqua router, assigns budgets, reserves and commits the $0.25 research
 * spend, proves both policy rejections, and repays through the waterfall.
 * Usage: npm run arc:fork, npm run arc:fork:setup, then npm run arc:fork:e2e.
 */
import { createRequire } from "node:module";
import { loadArcConfig } from "./config.js";
import { ArcPolicyError, FacilityClient } from "./facility.js";

const require = createRequire(import.meta.url);
const fixture = require("@working-capital/shared/fixtures/retailco-demo.json");

const config = loadArcConfig({ ...process.env, ARC_MODE: "fork" });
const arc = new FacilityClient(config);
const run = `${fixture.facilityKey}-${Date.now()}`;
const now = Math.floor(Date.now() / 1000);
const usd = (minor: string | bigint) => (Number(minor) / 1e6).toLocaleString("en-US", { minimumFractionDigits: 2 });

async function expectPolicy(code: string, action: () => Promise<unknown>) {
  try {
    await action();
  } catch (error) {
    if (error instanceof ArcPolicyError && error.code === code) {
      console.log(`✓ rejected on-chain: ${code}`);
      return;
    }
    throw error;
  }
  throw new Error(`Expected ${code}`);
}

const opened = await arc.openFacility({
  facilityKey: run,
  receivableKey: `${run}:${fixture.receivable.invoiceNumber}`,
  faceValueMinor: BigInt(fixture.receivable.faceValueMinor),
  advanceRateBps: fixture.receivable.advanceRateBps,
  maturity: now + 30 * 86400,
  verificationExpiry: now + 30 * 86400,
  hederaTxId: "local-fork-demo",
  hcsSequence: 1,
});
console.log(`✓ facility opened ${opened.txHash}`);
await expectPolicy("DUPLICATE_RECEIVABLE", () =>
  arc.openFacility({ facilityKey: `${run}-dup`, receivableKey: `${run}:${fixture.receivable.invoiceNumber}`, faceValueMinor: 1n, advanceRateBps: 7500, maturity: now + 86400, verificationExpiry: now + 86400, hederaTxId: "dup", hcsSequence: 2 }),
);

for (const [lenderId, principal, apr] of [["Alice", 4_000_000_000n, 700], ["Bob", 2_000_000_000n, 850]] as const) {
  const fill = await arc.fund({ facilityKey: run, lenderId, principalMinor: principal, aprBps: apr, minTermDays: 30 });
  console.log(`✓ ${lenderId} filled ${usd(principal)} via ${fill.viaAqua ? "official Aqua router" : "direct funding"} · notes ${usd(fill.notesMinor ?? "0")} · ${fill.txHash}`);
}

for (const agent of fixture.agents) {
  await arc.assignBudget({ facilityKey: run, agentIdentity: agent.identity, allocationMinor: BigInt(agent.allocationMinor), perTxMinor: BigInt(agent.perTxMinor), dailyMinor: BigInt(agent.dailyMinor), merchants: [agent.merchant] });
}
await arc.allowLeadMerchant(run, fixture.lead.merchant);
console.log("✓ budgets assigned: Research $1,000 · Compute $3,000 · Data $1,000");

await arc.reserve({ facilityKey: run, intentKey: `${run}:research-1`, agentIdentity: "research", merchant: "Briefly Data", amountMinor: 250_000n, ttlSeconds: 300 });
const committed = await arc.commit({ facilityKey: run, intentKey: `${run}:research-1`, amountMinor: 250_000n, hederaTxId: "0.0.0@0.0-fork", hcsSequence: 3 });
console.log(`✓ research $0.25 reserved and committed ${committed.txHash}`);

await expectPolicy("AGENT_BUDGET_EXCEEDED", () => arc.reserve({ facilityKey: run, intentKey: `${run}:compute-5500`, agentIdentity: "compute", merchant: "Northstar Cloud", amountMinor: 5_500_000_000n, ttlSeconds: 300 }));
await expectPolicy("GLOBAL_CREDIT_LIMIT_EXCEEDED", () => arc.reserve({ facilityKey: run, intentKey: `${run}:lead-6500`, agentIdentity: "lead", merchant: "Northstar Cloud", amountMinor: 6_500_000_000n, ttlSeconds: 300 }));

const repaid = await arc.repay({ facilityKey: run, amountMinor: BigInt(fixture.receivable.faceValueMinor) });
console.log(`✓ repaid: $${usd(repaid.toLendersMinor)} to lenders, $${usd(repaid.toOperatorMinor)} to operator, $${usd(repaid.undrawnReturnedMinor)} undrawn returned`);
const state = await arc.facilityState(run);
if (state.facility.status !== 3) throw new Error(`Facility should be REPAID, found ${state.facility.status}`);
console.log("✓ facility status REPAID · debt 0");
