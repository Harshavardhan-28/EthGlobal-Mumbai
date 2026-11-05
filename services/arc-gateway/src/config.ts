import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { defineChain, type Hex } from "viem";
import { mnemonicToAccount, privateKeyToAccount, type LocalAccount } from "viem/accounts";

type Environment = Record<string, string | undefined>;

export const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), "../../..");
export const ARC_TESTNET_CHAIN_ID = 5042002;
export const ARC_MAINNET_CHAIN_ID = 5042;
export const ARC_TESTNET_USDC = "0x3600000000000000000000000000000000000000";
/** Anvil's public development mnemonic. Only ever used against a local fork. */
const ANVIL_MNEMONIC = "test test test test test test test test test test test junk";

export type ArcMode = "fork" | "testnet";
export type LenderId = "Alice" | "Bob" | "Charlie";

export type Deployment = {
  mode: ArcMode;
  chainId: number;
  facility: Hex;
  note: Hex;
  usdc: Hex;
  aquaRegistry?: Hex;
  aquaRouter?: Hex;
  deployedAt: string;
  lenders: Partial<Record<LenderId, { address: Hex; encodedOrder?: string; strategyHash?: string; shipTx?: Hex; shippedMinor?: string; expiresAt?: number }>>;
};

export type ArcConfig = {
  mode: ArcMode;
  rpcUrl: string;
  chainId: number;
  deploymentPath: string;
  owner: LocalAccount;
  coordinator: LocalAccount;
  operator: LocalAccount;
  customer: LocalAccount;
  treasury: Hex;
  lenders: Record<LenderId, LocalAccount>;
  apiToken: string;
  port: number;
  dataDir: string;
};

function key(env: Environment, name: string, fallback?: string): LocalAccount {
  const value = env[name]?.trim() || fallback;
  if (!value) throw new Error(`Missing required Arc configuration: ${name}`);
  if (!/^0x[0-9a-fA-F]{64}$/.test(value)) throw new Error(`${name} must be a 32-byte hex private key.`);
  return privateKeyToAccount(value as Hex);
}

export function loadArcConfig(env: Environment = process.env): ArcConfig {
  const mode = (env.ARC_MODE?.trim() || "fork") as ArcMode;
  if (mode !== "fork" && mode !== "testnet") throw new Error("ARC_MODE must be fork or testnet.");
  const port = Number(env.ARC_GATEWAY_PORT || "4031");
  const common = {
    mode,
    apiToken: env.ARC_GATEWAY_API_TOKEN?.trim() || (mode === "fork" ? "local-fork-token" : ""),
    port,
    dataDir: env.ARC_GATEWAY_DATA_DIR?.trim() || join(REPO_ROOT, ".data"),
  };
  if (!common.apiToken) throw new Error("Missing required Arc configuration: ARC_GATEWAY_API_TOKEN");

  if (mode === "fork") {
    const dev = (index: number) => mnemonicToAccount(ANVIL_MNEMONIC, { addressIndex: index });
    return {
      ...common,
      rpcUrl: env.ARC_FORK_RPC_URL?.trim() || "http://127.0.0.1:8545",
      chainId: ARC_MAINNET_CHAIN_ID,
      deploymentPath: join(REPO_ROOT, "deployments/arc-fork.json"),
      owner: dev(0),
      coordinator: dev(1),
      operator: dev(2),
      lenders: { Alice: dev(3), Bob: dev(4), Charlie: dev(5) },
      customer: dev(6),
      treasury: dev(7).address,
    };
  }

  const owner = key(env, "ARC_PRIVATE_KEY");
  const ownerKey = env.ARC_PRIVATE_KEY!.trim();
  return {
    ...common,
    rpcUrl: env.ARC_RPC_URL?.trim() || "https://rpc.testnet.arc.io",
    chainId: Number(env.ARC_CHAIN_ID || ARC_TESTNET_CHAIN_ID),
    deploymentPath: join(REPO_ROOT, "deployments/arc-testnet.json"),
    owner,
    coordinator: key(env, "ARC_COORDINATOR_PRIVATE_KEY", ownerKey),
    operator: key(env, "ARC_OPERATOR_PRIVATE_KEY", ownerKey),
    lenders: {
      Alice: key(env, "ARC_LENDER_ALICE_PRIVATE_KEY", ownerKey),
      Bob: key(env, "ARC_LENDER_BOB_PRIVATE_KEY", ownerKey),
      Charlie: key(env, "ARC_LENDER_CHARLIE_PRIVATE_KEY", ownerKey),
    },
    customer: key(env, "ARC_CUSTOMER_PRIVATE_KEY", ownerKey),
    treasury: (env.ARC_TREASURY_ADDRESS?.trim() || owner.address) as Hex,
  };
}

export function arcChain(config: Pick<ArcConfig, "chainId" | "rpcUrl" | "mode">) {
  return defineChain({
    id: config.chainId,
    name: config.mode === "fork" ? "Arc mainnet (local Anvil fork)" : "Arc testnet",
    nativeCurrency: { name: "USDC", symbol: "USDC", decimals: 18 },
    rpcUrls: { default: { http: [config.rpcUrl] } },
  });
}

export function loadDeployment(config: Pick<ArcConfig, "deploymentPath">): Deployment {
  if (!existsSync(config.deploymentPath)) {
    throw new Error(`No Arc deployment at ${config.deploymentPath}. Run npm run arc:fork:setup (fork) or record the testnet deployment.`);
  }
  return JSON.parse(readFileSync(config.deploymentPath, "utf8")) as Deployment;
}

export function explorerTx(config: Pick<ArcConfig, "mode">, hash: string): string | null {
  return config.mode === "testnet" ? `https://explorer.testnet.arc.io/tx/${hash}` : null;
}
