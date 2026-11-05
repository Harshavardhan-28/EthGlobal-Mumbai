import { buildReceivableBondRequest, loadAtsConfig, type ReceivableBondInput } from "./ats.js";

const input: ReceivableBondInput = {
  receivableId: "INV-2048",
  isin: process.env.HEDERA_ATS_DEMO_ISIN ?? "US0000000001",
  issuerAccountId: process.env.HEDERA_ATS_ISSUER_ACCOUNT_ID ?? "",
  buyerAccountId: process.env.HEDERA_ATS_BUYER_ACCOUNT_ID ?? "0.0.1001",
  faceValue: "10000.00",
  currency: "USDC",
  issueDate: "2026-10-20T00:00:00Z",
  dueDate: "2026-11-19T00:00:00Z",
};

const config = loadAtsConfig();
const request = buildReceivableBondRequest(input, config);
process.stdout.write(`${JSON.stringify(request, null, 2)}\n`);
