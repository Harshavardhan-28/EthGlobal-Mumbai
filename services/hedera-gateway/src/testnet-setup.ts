/**
 * One-time Hedera testnet setup. Safe to re-run: it skips steps that are already done.
 *   - associates testnet USDC with the agent payer and (when its key is given) the merchant
 *   - creates the HCS audit topic (submit key = audit operator) if HEDERA_AUDIT_TOPIC_ID is empty
 *   - publishes HCS-14 agent profiles (UAIDs) for the agent team to the audit topic
 * Prints the .env lines to add. Uses only testnet; never run with mainnet keys.
 */
import {
  AccountBalanceQuery,
  AccountId,
  Client,
  PrivateKey,
  TokenAssociateTransaction,
  TokenId,
  TopicCreateTransaction,
  TopicMessageSubmitTransaction,
  TopicId,
} from "@hiero-ledger/sdk";
import { demoAgents, uaid } from "./uaid.js";

const env = process.env;
const need = (name: string) => {
  const value = env[name]?.trim();
  if (!value) throw new Error(`Set ${name} in .env first (see docs/SETUP_GUIDE.docx).`);
  return value;
};
const usdc = TokenId.fromString(env.HEDERA_USDC_TOKEN_ID?.trim() || "0.0.429274");
const clientFor = (id: string, key: string) => Client.forTestnet().setOperator(AccountId.fromString(id), PrivateKey.fromStringECDSA(key));

async function associate(label: string, id: string, key: string) {
  const client = clientFor(id, key);
  try {
    const balance = await new AccountBalanceQuery().setAccountId(id).execute(client);
    if (balance.tokens?.get(usdc) !== undefined && balance.tokens?.get(usdc) !== null) {
      console.log(`✓ ${label} ${id} already associated with USDC ${usdc} (balance ${balance.tokens.get(usdc)?.toString()} base units)`);
      return;
    }
    const tx = await new TokenAssociateTransaction().setAccountId(id).setTokenIds([usdc]).freezeWith(client).sign(PrivateKey.fromStringECDSA(key));
    const receipt = await (await tx.execute(client)).getReceipt(client);
    console.log(`✓ ${label} ${id} associated with USDC: ${receipt.status.toString()}`);
  } catch (error) {
    const text = error instanceof Error ? error.message : String(error);
    if (text.includes("TOKEN_ALREADY_ASSOCIATED")) console.log(`✓ ${label} ${id} already associated with USDC`);
    else throw error;
  } finally {
    client.close();
  }
}

const payerId = need("HEDERA_AGENT_ACCOUNT_ID");
const payerKey = need("HEDERA_AGENT_PRIVATE_KEY");
const auditId = env.HEDERA_AUDIT_OPERATOR_ID?.trim() || payerId;
const auditKey = env.HEDERA_AUDIT_OPERATOR_KEY?.trim() || payerKey;
const merchantId = need("HEDERA_RESEARCH_MERCHANT_ID");

await associate("Agent payer", payerId, payerKey);
if (env.HEDERA_RESEARCH_MERCHANT_KEY?.trim()) await associate("Merchant", merchantId, env.HEDERA_RESEARCH_MERCHANT_KEY.trim());
else console.log(`! Merchant ${merchantId}: set HEDERA_RESEARCH_MERCHANT_KEY to associate USDC automatically, or associate it in your wallet.`);

let topicId = env.HEDERA_AUDIT_TOPIC_ID?.trim();
const client = clientFor(auditId, auditKey);
try {
  if (!topicId) {
    const receipt = await (await new TopicCreateTransaction()
      .setTopicMemo("Autonomous Working Capital · audit trail")
      .setSubmitKey(PrivateKey.fromStringECDSA(auditKey).publicKey)
      .execute(client)).getReceipt(client);
    topicId = receipt.topicId!.toString();
    console.log(`✓ Created HCS audit topic ${topicId} · https://hashscan.io/testnet/topic/${topicId}`);
  } else {
    console.log(`✓ Using HCS audit topic ${topicId}`);
  }

  const operatorAccount = env.HEDERA_OPERATOR_ACCOUNT_ID?.trim() || payerId;
  for (const agent of demoAgents(operatorAccount)) {
    const id = uaid(agent);
    const message = JSON.stringify({ type: "hcs14.agent.profile", uaid: id, name: agent.name, version: agent.version, protocol: agent.protocol, nativeId: agent.nativeId, skills: agent.skills });
    const receipt = await (await new TopicMessageSubmitTransaction().setTopicId(TopicId.fromString(topicId)).setMessage(message).execute(client)).getReceipt(client);
    console.log(`✓ HCS-14 profile ${agent.identity}: ${id.slice(0, 40)}… (seq ${receipt.topicSequenceNumber?.toString()})`);
  }
} finally {
  client.close();
}

console.log(`\nAdd to .env:\nHEDERA_AUDIT_OPERATOR_ID=${auditId}\nHEDERA_AUDIT_TOPIC_ID=${topicId}`);
