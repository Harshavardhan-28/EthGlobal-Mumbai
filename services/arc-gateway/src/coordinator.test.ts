import assert from "node:assert/strict";
import { recoverTypedDataAddress } from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { Coordinator, agentAddress, id32 } from "./coordinator.js";

const account = privateKeyToAccount("0x00000000000000000000000000000000000000000000000000000000000c0ffe");
const facility = "0x18b7cbdffa52d1e7bb992fd50f394c5b59e20b72";
const coordinator = new Coordinator(account, 5042002, facility);
const message = {
  facilityId: id32("retailco-net30"),
  intentId: id32("research-1"),
  amount: 250_000n,
  hederaTxHash: id32("0.0.1001@1760905800.000000321"),
  hcsSequence: 9n,
  expiry: 1_900_000_000n,
};
const signature = await coordinator.signSpend(message);
const recovered = await recoverTypedDataAddress({
  domain: { name: "WorkingCapitalFacility", version: "1", chainId: 5042002, verifyingContract: facility },
  types: {
    SpendAttestation: [
      { name: "facilityId", type: "bytes32" },
      { name: "intentId", type: "bytes32" },
      { name: "amount", type: "uint128" },
      { name: "hederaTxHash", type: "bytes32" },
      { name: "hcsSequence", type: "uint64" },
      { name: "expiry", type: "uint64" },
    ],
  },
  primaryType: "SpendAttestation",
  message,
  signature,
});
assert.equal(recovered, account.address, "spend attestation recovers to the coordinator");
assert.notEqual(agentAddress("research"), agentAddress("compute"), "agent budget keys are distinct");
assert.match(agentAddress("research"), /^0x[0-9a-f]{40}$/);
console.log("Arc gateway coordinator checks passed");
