import express from "express";
import { HTTPFacilitatorClient } from "@x402/core/server";
import { paymentMiddleware, x402ResourceServer } from "@x402/express";
import { ExactHederaScheme } from "@x402/hedera/exact/server";
import { loadHederaResourceConfig, RESEARCH_PRICE_USD } from "./index.js";

const config = loadHederaResourceConfig();

const facilitator = new HTTPFacilitatorClient({ url: config.facilitatorUrl });
const server = new x402ResourceServer(facilitator)
  .register(config.network, new ExactHederaScheme({
    defaultAssets: {
      [config.network]: { asset: config.usdcTokenId, decimals: 6 },
    },
  }));

const app = express();
app.disable("x-powered-by");
app.get("/health", (_request, response) => response.json({ status: "ok", network: config.network }));
app.use(paymentMiddleware({
  "GET /research": {
    accepts: [{
      scheme: "exact",
      price: RESEARCH_PRICE_USD,
      network: config.network,
      payTo: config.merchantAccountId,
      maxTimeoutSeconds: 180,
    }],
    description: "Retail market research brief for the working-capital demo",
  },
}, server));
app.get("/research", (_request, response) => response.json({
  source: "Briefly Data",
  result: "RetailCo demand is forecast to rise 8% over the next 30 days.",
  paidService: true,
}));

const bindHost = process.env.HEDERA_BIND_HOST?.trim() || "127.0.0.1";

app.listen(config.researchPort, bindHost, () => {
  process.stdout.write(`Hedera x402 research service listening on http://${bindHost}:${config.researchPort}/research\n`);
});
