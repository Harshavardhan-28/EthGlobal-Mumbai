import assert from "node:assert/strict";
import {
  ARC_CHAIN_ID,
  buildAquaShipTransaction,
  loadAquaConfig,
  selectAquaFunding,
  strategyHash,
  type LenderOffer,
} from "./index.js";

const env = {
  AQUA_CHAIN_ID: "5042",
  AQUA_RPC_URL: "https://rpc.example.test",
  AQUA_USDC_ADDRESS: "0x0000000000000000000000000000000000000001",
};
const config = loadAquaConfig(env);
const strategy = "0x5000";
const offers: LenderOffer[] = [
  { lenderId: "Charlie", makerAddress: "0x0000000000000000000000000000000000000003", strategy, usdcLiquidityMinor: 5_000_000_000n, aprBps: 1300, minBuyerScore: "A", maxAdvanceRateBps: 7500, maxTenorDays: 30, expiresAt: "2026-12-01T00:00:00Z" },
  { lenderId: "Bob", makerAddress: "0x0000000000000000000000000000000000000002", strategy, usdcLiquidityMinor: 2_000_000_000n, aprBps: 850, minBuyerScore: "A-", maxAdvanceRateBps: 7500, maxTenorDays: 30, expiresAt: "2026-12-01T00:00:00Z" },
  { lenderId: "Alice", makerAddress: "0x0000000000000000000000000000000000000001", strategy, usdcLiquidityMinor: 4_000_000_000n, aprBps: 700, minBuyerScore: "A", maxAdvanceRateBps: 7500, maxTenorDays: 30, expiresAt: "2026-12-01T00:00:00Z" },
];
const plan = selectAquaFunding(offers, {
  receivableId: "INV-2048",
  borrowerAddress: "0x0000000000000000000000000000000000000010",
  requestedMinor: 6_000_000_000n,
  buyerScore: "A",
  advanceRateBps: 7500,
  tenorDays: 30,
  asOf: "2026-10-20T00:00:00Z",
});
assert.equal(plan.chainId, ARC_CHAIN_ID);
assert.deepEqual(plan.allocations.map((item) => item.lenderId), ["Alice", "Bob"]);
assert.equal(plan.remainingMinor, 0n);
assert.match(strategyHash(strategy), /^0x[0-9a-f]{64}$/);
const ship = buildAquaShipTransaction(config, offers[0].makerAddress, strategy, 4_000_000_000n);
assert.equal(ship.to, config.aquaAddress);
assert.match(ship.data, /^0x[0-9a-f]+$/);
process.stdout.write("Aqua gateway checks passed\n");
