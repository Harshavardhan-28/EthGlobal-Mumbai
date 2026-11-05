import assert from "node:assert/strict";
import { base58, canonicalAgentJson, demoAgents, uaid } from "./uaid.js";
import { mirrorTransactionId } from "./mirror.js";

assert.equal(base58(Uint8Array.from([0, 0, 1])), "112");
assert.equal(base58(Buffer.from("hello world")), "StV1DL6CwTryKyV");
const [lead, research] = demoAgents("0.0.5005");
assert.equal(canonicalAgentJson({ ...research, registry: " Working-Capital ", skills: [7, 0] }), canonicalAgentJson(research), "normalization is stable");
const id = uaid(research);
assert.match(id, /^uaid:aid:[1-9A-HJ-NP-Za-km-z]{60,70};uid=research;registry=working-capital;proto=x402;nativeId=hedera:testnet:0\.0\.5005$/);
assert.notEqual(uaid(lead), id);
assert.throws(() => uaid({ ...research, skills: [50] }), /Invalid HCS-14 skill/);
assert.equal(mirrorTransactionId("0.0.123@1700000000.000000001"), "0.0.123-1700000000-000000001");
console.log("Hedera gateway checks passed");
console.log(id);
