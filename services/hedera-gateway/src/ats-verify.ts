/** Verifies an issuer-submitted ATS bond transaction on Mirror Node: npm run hedera:ats:verify -- <transactionId> */
import { getMirrorTransaction } from "./mirror.js";

const transactionId = process.argv[2];
if (!transactionId) throw new Error("Usage: npm run hedera:ats:verify -- <transactionId from the ATS web app>");
const tx = await getMirrorTransaction(transactionId);
console.log(JSON.stringify({ transactionId, result: tx.result, name: tx.name, consensusTimestamp: tx.consensus_timestamp, entityId: tx.entity_id, hashscan: `https://hashscan.io/testnet/transaction/${encodeURIComponent(transactionId)}` }, null, 2));
if (tx.result !== "SUCCESS") process.exit(1);
