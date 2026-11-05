import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DurableStore, IdempotentExecutor, bearerMatches, formatUsdc } from "./index.js";

const dir = mkdtempSync(join(tmpdir(), "wc-shared-"));
const path = join(dir, "ledger.json");
let calls = 0;
const executor = new IdempotentExecutor(new DurableStore<{ tx: string }>(path));
const [a, b] = await Promise.all([
  executor.run("pay-1", async () => ({ tx: `tx-${++calls}` })),
  executor.run("pay-1", async () => ({ tx: `tx-${++calls}` })),
]);
assert.equal(calls, 1, "concurrent retries execute once");
assert.equal(a.result.tx, b.result.tx);

const restarted = new IdempotentExecutor(new DurableStore<{ tx: string }>(path));
const replay = await restarted.run("pay-1", async () => ({ tx: `tx-${++calls}` }));
assert.equal(replay.replayed, true, "a restarted gateway replays the stored receipt");
assert.equal(calls, 1);

assert.equal(bearerMatches("Bearer secret", "secret"), true);
assert.equal(bearerMatches("Bearer wrong!", "secret"), false);
assert.equal(formatUsdc(250_000n), "0.250000");
assert.equal(formatUsdc(6_036_986_300n), "6036.986300");
console.log("Shared package checks passed");
