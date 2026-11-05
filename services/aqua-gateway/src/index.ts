import { createHash } from "node:crypto";
import { createRequire } from "node:module";

type Environment = Record<string, string | undefined>;

const require = createRequire(import.meta.url);
const aquaSdk = require("@1inch/aqua-sdk") as typeof import("@1inch/aqua-sdk");
const swapVmSdk = require("@1inch/swap-vm-sdk") as typeof import("@1inch/swap-vm-sdk");
const { Address, AquaProtocolContract, HexString, NetworkEnum } = aquaSdk;
const { Address: SvmAddress, HexString: SvmHex, AQUA_SWAP_VM_CONTRACT_ADDRESSES, AquaProgramBuilder, MakerTraits, Order, SwapVMContract, TakerTraits, instructions } = swapVmSdk;
const { AQUA_CONTRACT_ADDRESSES } = aquaSdk;

/**
 * Arc mainnet chain ID. The official Aqua registry and AquaSwapVMRouter are deployed on
 * Arc mainnet (5042) but not on Arc testnet (5042002), so the demo runs these contracts on
 * a local Anvil fork of Arc mainnet (`npm run arc:fork`). Never point this at mainnet with
 * real funds from the demo.
 */
export const ARC_CHAIN_ID = 5042;

export type AquaConfig = {
  chainId: typeof ARC_CHAIN_ID;
  rpcUrl: string;
  usdcAddress: string;
  aquaAddress: string;
  swapVmRouterAddress: string;
};

export type LenderOffer = {
  lenderId: string;
  makerAddress: string;
  strategy: string;
  usdcLiquidityMinor: bigint;
  aprBps: number;
  minBuyerScore: "A" | "A-" | "B";
  maxAdvanceRateBps: number;
  maxTenorDays: number;
  expiresAt: string;
};

export type FundingRequest = {
  receivableId: string;
  borrowerAddress: string;
  requestedMinor: bigint;
  buyerScore: "A" | "A-" | "B";
  advanceRateBps: number;
  tenorDays: number;
  asOf: string;
};

export type FundingAllocation = {
  lenderId: string;
  makerAddress: string;
  strategyHash: string;
  principalMinor: bigint;
  aprBps: number;
};

export type AquaFundingPlan = {
  planId: string;
  status: "QUOTED";
  chainId: typeof ARC_CHAIN_ID;
  requestedMinor: bigint;
  allocatedMinor: bigint;
  remainingMinor: bigint;
  allocations: FundingAllocation[];
  rejected: Array<{ lenderId: string; reason: string }>;
};

function required(env: Environment, name: string): string {
  const value = env[name]?.trim();
  if (!value) throw new Error(`Missing required Aqua configuration: ${name}`);
  return value;
}

function address(value: string, name: string): string {
  try {
    return new Address(value).toString();
  } catch {
    throw new Error(`${name} must be an EVM address.`);
  }
}

function positive(value: bigint, name: string): void {
  if (value <= 0n) throw new Error(`${name} must be positive.`);
}

const SCORE: Record<FundingRequest["buyerScore"], number> = { A: 3, "A-": 2, B: 1 };

export function loadAquaConfig(env: Environment = process.env): AquaConfig {
  const chainId = Number(env.AQUA_CHAIN_ID ?? ARC_CHAIN_ID);
  if (chainId !== ARC_CHAIN_ID) throw new Error("Aqua integration is restricted to Arc chain ID 5042.");

  const aquaAddress = AQUA_CONTRACT_ADDRESSES[NetworkEnum.ARC].toString();
  const swapVmRouterAddress = AQUA_SWAP_VM_CONTRACT_ADDRESSES[NetworkEnum.ARC].toString();
  return {
    chainId: ARC_CHAIN_ID,
    rpcUrl: required(env, "AQUA_RPC_URL"),
    usdcAddress: address(required(env, "AQUA_USDC_ADDRESS"), "AQUA_USDC_ADDRESS"),
    aquaAddress,
    swapVmRouterAddress,
  };
}

export function strategyHash(strategy: string): string {
  if (!/^0x(?:[0-9a-fA-F]{2})+$/.test(strategy)) {
    throw new Error("Aqua strategy must be non-empty, byte-aligned hex.");
  }
  return AquaProtocolContract.calculateStrategyHash(new HexString(strategy)).toString();
}

function isEligible(offer: LenderOffer, request: FundingRequest): string | undefined {
  if (SCORE[offer.minBuyerScore] > SCORE[request.buyerScore]) return "BUYER_SCORE_INELIGIBLE";
  if (offer.maxAdvanceRateBps < request.advanceRateBps) return "ADVANCE_RATE_INELIGIBLE";
  if (offer.maxTenorDays < request.tenorDays) return "TENOR_INELIGIBLE";
  if (Date.parse(offer.expiresAt) <= Date.parse(request.asOf)) return "OFFER_EXPIRED";
  if (offer.usdcLiquidityMinor <= 0n) return "NO_LIQUIDITY";
  return undefined;
}

export function selectAquaFunding(offers: LenderOffer[], request: FundingRequest): AquaFundingPlan {
  positive(request.requestedMinor, "requestedMinor");
  address(request.borrowerAddress, "borrowerAddress");
  if (!request.receivableId.trim()) throw new Error("receivableId is required.");
  if (Number.isNaN(Date.parse(request.asOf))) throw new Error("asOf must be an ISO-8601 timestamp.");

  let remaining = request.requestedMinor;
  const rejected: AquaFundingPlan["rejected"] = [];
  const allocations: FundingAllocation[] = [];
  for (const offer of [...offers].sort((left, right) => left.aprBps - right.aprBps || left.lenderId.localeCompare(right.lenderId))) {
    const rejection = isEligible(offer, request);
    if (rejection) {
      rejected.push({ lenderId: offer.lenderId, reason: rejection });
      continue;
    }
    const principalMinor = offer.usdcLiquidityMinor < remaining ? offer.usdcLiquidityMinor : remaining;
    if (principalMinor > 0n) {
      allocations.push({
        lenderId: offer.lenderId,
        makerAddress: address(offer.makerAddress, `${offer.lenderId}.makerAddress`),
        strategyHash: strategyHash(offer.strategy),
        principalMinor,
        aprBps: offer.aprBps,
      });
      remaining -= principalMinor;
    }
    if (remaining === 0n) break;
  }

  const allocatedMinor = request.requestedMinor - remaining;
  const planId = `aqua-${createHash("sha256")
    .update(`${request.receivableId}:${request.asOf}:${allocations.map((item) => `${item.lenderId}:${item.principalMinor}`).join(",")}`)
    .digest("hex")
    .slice(0, 16)}`;
  return {
    planId,
    status: "QUOTED",
    chainId: ARC_CHAIN_ID,
    requestedMinor: request.requestedMinor,
    allocatedMinor,
    remainingMinor: remaining,
    allocations,
    rejected,
  };
}

export function buildAquaShipTransaction(
  config: AquaConfig,
  makerAddress: string,
  strategy: string,
  amountMinor: bigint,
) {
  positive(amountMinor, "amountMinor");
  address(makerAddress, "makerAddress");
  const transaction = AquaProtocolContract.buildShipTx(new Address(config.aquaAddress), {
    app: new Address(config.swapVmRouterAddress),
    strategy: new HexString(strategy),
    amountsAndTokens: [{ amount: amountMinor, token: new Address(config.usdcAddress) }],
  });
  return { to: transaction.to.toString(), data: transaction.data.toString(), value: transaction.value.toString() };
}

/** Lender terms committed into the strategy salt so the strategy hash binds the offer. */
export type ReceivableCreditTerms = {
  aprBps: number;
  minBuyerScore: "A" | "A-" | "B";
  maxAdvanceRateBps: number;
  maxTenorDays: number;
};

export function termsSalt(terms: ReceivableCreditTerms): bigint {
  const digest = createHash("sha256")
    .update(`RECEIVABLE_CREDIT_V1:${terms.aprBps}:${terms.minBuyerScore}:${terms.maxAdvanceRateBps}:${terms.maxTenorDays}`)
    .digest();
  return digest.readBigUInt64BE(0);
}

/**
 * RECEIVABLE_CREDIT_V1 lender strategy, composed only from instructions the deployed
 * AquaSwapVMRouter supports:
 *   salt(terms) -> deadline(offer expiry) -> concentrate(1:1 band) -> xycSwap
 * The lender ships USDC with a zero lender-note balance. The facility (taker) pays lender
 * notes in and receives USDC out. The 1 bp price band only ever lets the lender receive at
 * least one note per USDC paid out. A dedicated receivable-credit opcode stays deferred
 * until its bytecode and adversarial tests are reviewed (docs/PLAN.md, Phase 4).
 */
export function buildReceivableCreditOrder(params: {
  maker: string;
  usdcAddress: string;
  noteAddress: string;
  expiresAt: number;
  terms: ReceivableCreditTerms;
}) {
  const maker = address(params.maker, "maker");
  const usdc = address(params.usdcAddress, "usdcAddress");
  const note = address(params.noteAddress, "noteAddress");
  const { ONE_E18 } = instructions.concentrate;
  const usdcIsLower = BigInt(usdc) < BigInt(note);
  // Price P = tokenGt / tokenLt. Keep notes-per-USDC >= 1 for the lender.
  const [rawPriceMin, rawPriceMax] = usdcIsLower ? [ONE_E18, (ONE_E18 * 10_001n) / 10_000n] : [(ONE_E18 * 9_999n) / 10_000n, ONE_E18];
  const program = new AquaProgramBuilder()
    .salt({ salt: termsSalt(params.terms) })
    .deadline({ deadline: BigInt(params.expiresAt) })
    .concentrateGrowLiquidity2D(instructions.concentrate.ConcentrateGrowLiquidity2DArgs.fromRawPrices(rawPriceMin, rawPriceMax))
    .xycSwapXD()
    .build();
  const order = Order.new({ maker: new SvmAddress(maker), program, traits: MakerTraits.default() });
  const encoded = order.encode().toString();
  return { order, encoded, strategyHash: strategyHash(encoded) };
}

/** Builds the Aqua `ship` transaction a lender signs to publish its USDC strategy. */
export function buildLenderShip(config: Pick<AquaConfig, "aquaAddress" | "swapVmRouterAddress">, encodedOrder: string, usdcAddress: string, noteAddress: string, usdcMinor: bigint) {
  positive(usdcMinor, "usdcMinor");
  const tx = AquaProtocolContract.buildShipTx(new Address(config.aquaAddress), {
    app: new Address(config.swapVmRouterAddress),
    strategy: new HexString(encodedOrder),
    amountsAndTokens: [
      { amount: usdcMinor, token: new Address(usdcAddress) },
      { amount: 0n, token: new Address(noteAddress) },
    ],
  });
  return { to: tx.to.toString(), data: tx.data.toString(), value: tx.value.toString() };
}

/**
 * Builds SwapVM calldata for the facility to fill exactly `principalMinor` USDC from a lender
 * strategy, paying at most `maxNotesMinor` lender notes. The facility contract executes this
 * calldata against the fixed router and verifies the balance changes on-chain.
 */
export function buildFacilityFill(params: {
  routerAddress: string;
  encodedOrder: string;
  usdcAddress: string;
  noteAddress: string;
  principalMinor: bigint;
  maxNotesMinor: bigint;
  deadline: number;
}) {
  positive(params.principalMinor, "principalMinor");
  if (params.maxNotesMinor < params.principalMinor) throw new Error("maxNotesMinor must cover the principal.");
  const vm = new SwapVMContract(new SvmAddress(params.routerAddress));
  const swap = {
    order: Order.decode(new SvmHex(params.encodedOrder)),
    amount: params.principalMinor,
    takerTraits: TakerTraits.new({ exactIn: false, threshold: params.maxNotesMinor, deadline: BigInt(params.deadline) }),
    tokenIn: new SvmAddress(params.noteAddress),
    tokenOut: new SvmAddress(params.usdcAddress),
  };
  const quote = vm.quote(swap);
  const tx = vm.swap(swap);
  return { to: tx.to.toString(), data: tx.data.toString(), quoteData: quote.data.toString() };
}
