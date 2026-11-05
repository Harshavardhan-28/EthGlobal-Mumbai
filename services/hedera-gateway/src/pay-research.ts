import { randomUUID } from "node:crypto";
import { createHederaIntegration, loadHederaConfig, RESEARCH_PAYMENT_MINOR } from "./index.js";

const config = loadHederaConfig();
const integration = createHederaIntegration(config);

try {
  const receipt = await integration.payX402({
    agentId: process.env.HEDERA_AGENT_ID || config.payerAccountId,
    merchantUrl: config.researchUrl,
    amountMinor: RESEARCH_PAYMENT_MINOR,
    token: "USDC",
    idempotencyKey: randomUUID(),
  });
  process.stdout.write(`${JSON.stringify(receipt, null, 2)}\n`);
} finally {
  integration.close();
}
