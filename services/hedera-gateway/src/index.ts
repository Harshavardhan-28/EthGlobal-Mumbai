import { createHash } from "node:crypto";
import { AccountId, Client, PrivateKey, TopicId, TopicMessageSubmitTransaction } from "@hiero-ledger/sdk";
import { x402Client, x402HTTPClient } from "@x402/core/client";
import { wrapFetchWithPayment } from "@x402/fetch";
import { ExactHederaScheme, createClientHederaSigner } from "@x402/hedera";

export const RESEARCH_PAYMENT_MINOR = 250_000n;
export const RESEARCH_PRICE_USD = "$0.25";

export type ChainReceipt = {
  network: "hedera-testnet" | "arc-testnet";
  transactionId: string;
  explorerUrl: string;
  finalized: boolean;
  auditTransactionId?: string;
  /** HCS topic sequence number of the audit message, when one was published. */
  auditSequence?: number;
  sequence?: number;
};

export type X402Payment = {
  agentId: string;
  merchantUrl: string;
  amountMinor: bigint;
  token: "USDC";
  idempotencyKey: string;
};

export interface HederaPort {
  tokenizeReceivable(receivableId: string, verificationHash: string): Promise<ChainReceipt>;
  publishAudit(topicId: string, payload: Record<string, unknown>): Promise<ChainReceipt>;
  payX402(payment: X402Payment): Promise<ChainReceipt>;
}

export type HederaCommonConfig = {
  network: "hedera:testnet";
  usdcTokenId: string;
  merchantAccountId: string;
  maxPaymentMinor: bigint;
  facilitatorUrl: string;
};

export type HederaConfig = HederaCommonConfig & {
  payerAccountId: string;
  payerPrivateKey: string;
  researchUrl: string;
  auditOperatorId: string;
  auditOperatorKey: string;
  auditTopicId: string;
};

export type HederaResourceConfig = HederaCommonConfig & { researchPort: number };

type Environment = Record<string, string | undefined>;

function loadCommonConfig(env: Environment, required: (name: string) => string): HederaCommonConfig {
  const network = env.HEDERA_NETWORK?.trim() || "hedera:testnet";
  if (network !== "hedera:testnet") {
    throw new Error("Live payment signing is restricted to hedera:testnet in this prototype.");
  }

  const maxPaymentMinor = BigInt(env.HEDERA_X402_MAX_USDC_MINOR || RESEARCH_PAYMENT_MINOR.toString());
  if (maxPaymentMinor <= 0n) throw new Error("HEDERA_X402_MAX_USDC_MINOR must be positive.");

  return {
    network,
    usdcTokenId: env.HEDERA_USDC_TOKEN_ID?.trim() || "0.0.429274",
    merchantAccountId: required("HEDERA_RESEARCH_MERCHANT_ID"),
    maxPaymentMinor,
    facilitatorUrl: env.HEDERA_X402_FACILITATOR_URL?.trim() || "https://x402.org/facilitator",
  };
}

export function loadHederaConfig(env: Environment = process.env): HederaConfig {
  const required = (name: string): string => {
    const value = env[name]?.trim();
    if (!value) throw new Error(`Missing required Hedera configuration: ${name}`);
    return value;
  };
  const config: HederaConfig = {
    ...loadCommonConfig(env, required),
    payerAccountId: required("HEDERA_AGENT_ACCOUNT_ID"),
    payerPrivateKey: required("HEDERA_AGENT_PRIVATE_KEY"),
    researchUrl: required("HEDERA_RESEARCH_URL"),
    auditOperatorId: required("HEDERA_AUDIT_OPERATOR_ID"),
    auditOperatorKey: required("HEDERA_AUDIT_OPERATOR_KEY"),
    auditTopicId: required("HEDERA_AUDIT_TOPIC_ID"),
  };

  const researchUrl = new URL(config.researchUrl);
  const allowsInternalHttp = env.HEDERA_ALLOW_INTERNAL_HTTP === "true" && researchUrl.hostname === "research";
  if (researchUrl.protocol !== "https:" && researchUrl.hostname !== "localhost" && researchUrl.hostname !== "127.0.0.1" && !allowsInternalHttp) {
    throw new Error("HEDERA_RESEARCH_URL must use HTTPS outside localhost.");
  }
  return config;
}

export function loadHederaResourceConfig(env: Environment = process.env): HederaResourceConfig {
  const required = (name: string): string => {
    const value = env[name]?.trim();
    if (!value) throw new Error(`Missing required Hedera configuration: ${name}`);
    return value;
  };
  const port = Number(env.HEDERA_RESEARCH_PORT || "4021");
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("HEDERA_RESEARCH_PORT must be a valid TCP port.");
  return { ...loadCommonConfig(env, required), researchPort: port };
}

function hederaExplorerUrl(transactionId: string): string {
  return `https://hashscan.io/testnet/transaction/${encodeURIComponent(transactionId)}`;
}

function accountClient(accountId: string, privateKey: string): Client {
  const key = PrivateKey.fromStringECDSA(privateKey);
  return Client.forTestnet().setOperator(AccountId.fromString(accountId), key);
}

function hashIdempotencyKey(key: string): string {
  return createHash("sha256").update(key).digest("hex");
}

export class HederaIntegration implements HederaPort {
  private readonly auditClient: Client;
  private readonly config: HederaConfig;

  constructor(config: HederaConfig) {
    this.config = config;
    this.auditClient = accountClient(config.auditOperatorId, config.auditOperatorKey);
  }

  private createPaidFetch(expectedAmount: bigint): { fetch: typeof globalThis.fetch; http: x402HTTPClient } {
    const payerKey = PrivateKey.fromStringECDSA(this.config.payerPrivateKey);
    const signer = createClientHederaSigner(this.config.payerAccountId, payerKey, { network: this.config.network });
    const client = new x402Client()
      .register(this.config.network, new ExactHederaScheme(signer))
      .setSpendControls({
        allowedAssets: [{
          network: this.config.network,
          asset: this.config.usdcTokenId,
          maxAmountPerPayment: this.config.maxPaymentMinor.toString(),
        }],
      })
      .registerPolicy((_version, requirements) => requirements.filter((requirement) =>
        requirement.network === this.config.network
        && requirement.scheme === "exact"
        && requirement.asset === this.config.usdcTokenId
        && requirement.payTo === this.config.merchantAccountId
        && BigInt(requirement.amount) === expectedAmount,
      ));
    return { fetch: wrapFetchWithPayment(globalThis.fetch, client), http: new x402HTTPClient(client) };
  }

  async tokenizeReceivable(_receivableId: string, _verificationHash: string): Promise<ChainReceipt> {
    throw new Error("ATS receivable issuance is not configured. Use the ATS wallet-authorized flow before marking an invoice tokenized.");
  }

  async publishAudit(topicId: string, payload: Record<string, unknown>): Promise<ChainReceipt> {
    const message = JSON.stringify(payload);
    if (Buffer.byteLength(message, "utf8") > 1024) {
      throw new Error("HCS audit event exceeds the prototype's 1 KB payload cap; publish a compact event.");
    }

    try {
      const response = await new TopicMessageSubmitTransaction()
        .setTopicId(TopicId.fromString(topicId))
        .setMessage(message)
        .execute(this.auditClient);
      const receipt = await response.getReceipt(this.auditClient);
      if (receipt.status.toString() !== "SUCCESS") {
        throw new Error(`HCS submission failed with status ${receipt.status.toString()}.`);
      }
      const transactionId = response.transactionId.toString();
      return {
        network: "hedera-testnet",
        transactionId,
        explorerUrl: hederaExplorerUrl(transactionId),
        finalized: true,
        sequence: receipt.topicSequenceNumber ? Number(receipt.topicSequenceNumber.toString()) : undefined,
      };
    } catch (error) {
      throw new Error(`HCS audit submission failed: ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  async payX402(payment: X402Payment): Promise<ChainReceipt> {
    if (payment.token !== "USDC") throw new Error("Only USDC x402 payments are enabled.");
    if (payment.amountMinor <= 0n || payment.amountMinor > this.config.maxPaymentMinor) {
      throw new Error("Requested x402 amount exceeds the configured per-payment limit.");
    }
    if (new URL(payment.merchantUrl).href !== new URL(this.config.researchUrl).href) {
      throw new Error("Requested x402 URL is not the configured research endpoint.");
    }

    const { fetch: paidFetch, http } = this.createPaidFetch(payment.amountMinor);
    const response = await paidFetch(payment.merchantUrl, {
      method: "GET",
      headers: { "Idempotency-Key": payment.idempotencyKey },
      signal: AbortSignal.timeout(30_000),
    });
    if (!response.ok) {
      throw new Error(`Paid research endpoint returned HTTP ${response.status}.`);
    }

    const settlement = http.getPaymentSettleResponse((name) => response.headers.get(name)) as unknown as Record<string, unknown>;
    const transactionId = settlement.transactionId ?? settlement.transaction;
    if (settlement.success !== true || typeof transactionId !== "string" || transactionId.length === 0) {
      throw new Error("x402 resource completed without a successful Hedera settlement receipt.");
    }

    const audit = await this.publishAudit(this.config.auditTopicId, {
      type: "x402.payment.settled",
      network: this.config.network,
      transactionId,
      asset: this.config.usdcTokenId,
      amountMinor: payment.amountMinor.toString(),
      agentId: payment.agentId,
      intentRef: hashIdempotencyKey(payment.idempotencyKey),
    });

    return {
      network: "hedera-testnet",
      transactionId,
      explorerUrl: hederaExplorerUrl(transactionId),
      finalized: true,
      auditTransactionId: audit.transactionId,
      auditSequence: audit.sequence,
    };
  }

  close(): void {
    this.auditClient.close();
  }
}

/** Audit-only publisher for lifecycle events (confirmation, rejection, repayment). */
export function loadAuditConfig(env: Environment = process.env) {
  const required = (name: string): string => {
    const value = env[name]?.trim();
    if (!value) throw new Error(`Missing required Hedera configuration: ${name}`);
    return value;
  };
  return { operatorId: required("HEDERA_AUDIT_OPERATOR_ID"), operatorKey: required("HEDERA_AUDIT_OPERATOR_KEY"), topicId: required("HEDERA_AUDIT_TOPIC_ID") };
}

export async function publishAuditEvent(payload: Record<string, unknown>, env: Environment = process.env): Promise<ChainReceipt> {
  const audit = loadAuditConfig(env);
  const client = accountClient(audit.operatorId, audit.operatorKey);
  try {
    const message = JSON.stringify(payload);
    if (Buffer.byteLength(message, "utf8") > 1024) throw new Error("HCS audit event exceeds the 1 KB payload cap.");
    const response = await new TopicMessageSubmitTransaction().setTopicId(TopicId.fromString(audit.topicId)).setMessage(message).execute(client);
    const receipt = await response.getReceipt(client);
    if (receipt.status.toString() !== "SUCCESS") throw new Error(`HCS submission failed with status ${receipt.status.toString()}.`);
    const transactionId = response.transactionId.toString();
    return {
      network: "hedera-testnet",
      transactionId,
      explorerUrl: hederaExplorerUrl(transactionId),
      finalized: true,
      sequence: receipt.topicSequenceNumber ? Number(receipt.topicSequenceNumber.toString()) : undefined,
    };
  } finally {
    client.close();
  }
}

export function createHederaIntegration(config = loadHederaConfig()): HederaIntegration {
  return new HederaIntegration(config);
}
