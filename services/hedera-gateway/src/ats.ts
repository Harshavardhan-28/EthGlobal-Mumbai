import { createRequire } from "node:module";

// ATS 8.0.0's ESM build imports the CommonJS-only js-sha3 package by named
// export, which Node 26 rejects. The package's supported CommonJS export keeps
// that dependency interoperable in this server-side gateway.
const require = createRequire(import.meta.url);
const ats = require("@hashgraph/asset-tokenization-sdk") as typeof import("@hashgraph/asset-tokenization-sdk");
const { Bond, CreateBondRequest, InitializationRequest, Network } = ats;

type Environment = Record<string, string | undefined>;

export type AtsConfig = {
  network: "testnet";
  mirrorNodeUrl: string;
  rpcUrl: string;
  resolverId: string;
  factoryId: string;
  configId: string;
  configVersion: number;
  issuerAccountId: string;
};

export type ReceivableBondInput = {
  receivableId: string;
  isin: string;
  issuerAccountId: string;
  buyerAccountId: string;
  faceValue: string;
  currency: "USDC";
  issueDate: string;
  dueDate: string;
};

const HEDERA_ID = /^\d+\.\d+\.\d+$/;

function required(env: Environment, name: string): string {
  const value = env[name]?.trim();
  if (!value) throw new Error(`Missing required ATS configuration: ${name}`);
  return value;
}

function hederaId(value: string, name: string): string {
  if (!HEDERA_ID.test(value)) throw new Error(`${name} must be a Hedera account or contract ID.`);
  return value;
}

function bytes32(value: string, name: string): string {
  if (!/^0x[0-9a-fA-F]{64}$/.test(value)) throw new Error(`${name} must be a 32-byte hex value (the ATS bond configuration ID).`);
  return value;
}

function timestamp(date: string, name: string): string {
  const parsed = Date.parse(date);
  if (Number.isNaN(parsed)) throw new Error(`${name} must be an ISO-8601 date.`);
  return String(Math.floor(parsed / 1_000));
}

export function loadAtsConfig(env: Environment = process.env): AtsConfig {
  const network = env.HEDERA_ATS_NETWORK ?? "testnet";
  if (network !== "testnet") throw new Error("ATS issuance is intentionally restricted to Hedera testnet.");

  const configVersion = Number(required(env, "HEDERA_ATS_CONFIG_VERSION"));
  if (!Number.isInteger(configVersion) || configVersion < 0) {
    throw new Error("HEDERA_ATS_CONFIG_VERSION must be a non-negative integer.");
  }

  return {
    network,
    mirrorNodeUrl: required(env, "HEDERA_ATS_MIRROR_NODE_URL"),
    rpcUrl: required(env, "HEDERA_ATS_RPC_URL"),
    resolverId: hederaId(required(env, "HEDERA_ATS_RESOLVER_ID"), "HEDERA_ATS_RESOLVER_ID"),
    factoryId: hederaId(required(env, "HEDERA_ATS_FACTORY_ID"), "HEDERA_ATS_FACTORY_ID"),
    configId: bytes32(required(env, "HEDERA_ATS_CONFIG_ID"), "HEDERA_ATS_CONFIG_ID"),
    configVersion,
    issuerAccountId: hederaId(required(env, "HEDERA_ATS_ISSUER_ACCOUNT_ID"), "HEDERA_ATS_ISSUER_ACCOUNT_ID"),
  };
}

export function buildReceivableBondRequest(input: ReceivableBondInput, config: AtsConfig): InstanceType<typeof CreateBondRequest> {
  if (!input.receivableId.trim()) throw new Error("A canonical receivable ID is required.");
  if (!input.isin.trim()) throw new Error("An issuer-approved ISIN or asset identifier is required.");
  if (!/^\d+(\.\d{1,6})?$/.test(input.faceValue) || Number(input.faceValue) <= 0) {
    throw new Error("faceValue must be a positive USDC amount with at most six decimal places.");
  }
  if (input.currency !== "USDC") throw new Error("The prototype issues USDC-denominated receivables only.");
  hederaId(input.issuerAccountId, "issuerAccountId");
  hederaId(input.buyerAccountId, "buyerAccountId");

  const start = timestamp(input.issueDate, "issueDate");
  const maturity = timestamp(input.dueDate, "dueDate");
  if (Number(maturity) <= Number(start)) throw new Error("dueDate must be after issueDate.");

  const symbolSuffix = input.receivableId.replace(/[^a-zA-Z0-9]/g, "").toUpperCase().slice(-10) || "DEMO";
  return new CreateBondRequest({
    name: `Receivable ${input.receivableId}`.slice(0, 64),
    symbol: `RCV${symbolSuffix}`.slice(0, 32),
    isin: input.isin,
    decimals: 0,
    isWhiteList: true,
    erc20VotesActivated: false,
    isControllable: true,
    arePartitionsProtected: false,
    isMultiPartition: false,
    clearingActive: false,
    internalKycActivated: false,
    diamondOwnerAccount: input.issuerAccountId,
    currency: input.currency,
    numberOfUnits: "1",
    nominalValue: input.faceValue,
    nominalValueDecimals: 6,
    startingDate: start,
    maturityDate: maturity,
    info: JSON.stringify({ receivableId: input.receivableId, buyerAccountId: input.buyerAccountId }),
    configId: config.configId,
    configVersion: config.configVersion,
  });
}

export async function initializeAts(config: AtsConfig): Promise<void> {
  await Network.init(new InitializationRequest({
    network: config.network,
    mirrorNode: { name: "Hedera testnet Mirror Node", baseUrl: config.mirrorNodeUrl },
    rpcNode: { name: "Hedera testnet JSON-RPC", baseUrl: config.rpcUrl },
    configuration: { resolverAddress: config.resolverId, factoryAddress: config.factoryId },
  }));
}

export async function issueReceivableBond(input: ReceivableBondInput, config = loadAtsConfig()) {
  if (input.issuerAccountId !== config.issuerAccountId) {
    throw new Error("issuerAccountId must match HEDERA_ATS_ISSUER_ACCOUNT_ID.");
  }
  await initializeAts(config);
  // ATS signs through the wallet previously connected by the issuer-facing UI.
  // This method must never be invoked from an autonomous agent or server job.
  return Bond.create(buildReceivableBondRequest(input, config));
}
