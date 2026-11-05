import { createRequire } from "node:module";
import {
  BaseError,
  ContractFunctionRevertedError,
  createPublicClient,
  createWalletClient,
  decodeEventLog,
  http,
  type Hex,
  type PublicClient,
  type TransactionReceipt,
} from "viem";
import type { LocalAccount } from "viem/accounts";
import { buildFacilityFill } from "@working-capital/aqua-gateway";
import { arcChain, explorerTx, loadDeployment, type ArcConfig, type Deployment, type LenderId } from "./config.js";
import { Coordinator, agentAddress, expiryFromNow, id32 } from "./coordinator.js";

const require = createRequire(import.meta.url);
export const facilityArtifact = require("@working-capital/shared/abi/WorkingCapitalFacility.json") as { abi: readonly unknown[]; bytecode: Hex };
export const usdcArtifact = require("@working-capital/shared/abi/TestUSDC.json") as { abi: readonly unknown[]; bytecode: Hex };
export const noteArtifact = require("@working-capital/shared/abi/LenderNote.json") as { abi: readonly unknown[]; bytecode: Hex };
const abi = facilityArtifact.abi as any;

/** Contract custom errors mapped to the domain's policy codes. */
const POLICY_CODES: Record<string, string> = {
  AgentBudgetExceeded: "AGENT_BUDGET_EXCEEDED",
  GlobalCreditLimitExceeded: "GLOBAL_CREDIT_LIMIT_EXCEEDED",
  TransactionLimitExceeded: "TRANSACTION_LIMIT_EXCEEDED",
  DailyLimitExceeded: "DAILY_LIMIT_EXCEEDED",
  MerchantNotAllowed: "MERCHANT_NOT_ALLOWED",
  BorrowingBaseExceeded: "BORROWING_BASE_EXCEEDED",
  DuplicateReceivable: "DUPLICATE_RECEIVABLE",
  StaleReceivable: "STALE_RECEIVABLE",
  AttestationExpired: "ATTESTATION_EXPIRED",
  AttestationReplayed: "ATTESTATION_REPLAYED",
  FacilityNotActive: "FACILITY_NOT_ACTIVE",
  DuplicateIntent: "DUPLICATE_INTENT",
  ReservationNotPending: "RESERVATION_NOT_PENDING",
  LenderNotApproved: "LENDER_NOT_APPROVED",
  AddressBlocked: "ADDRESS_BLOCKED",
  InvalidStatus: "INVALID_STATUS",
  NotYetOverdue: "NOT_YET_OVERDUE",
};

/** Facility.status enum on the contract. New states were appended, so Repaid stays 3. */
export const FACILITY_STATUS = ["NONE", "OPEN", "ACTIVE", "REPAID", "OVERDUE", "DEFAULTED", "DISPUTED"] as const;
export type StatusAction = "overdue" | "default" | "dispute" | "resolve";

export class ArcPolicyError extends Error {
  constructor(public readonly code: string, public readonly contractError: string) {
    super(code);
  }
}

export type ArcReceipt = { network: string; txHash: Hex; explorerUrl: string | null; blockNumber: string; simulatedChain: boolean };

export const merchantKey = (merchantId: string): Hex => id32(`merchant:${merchantId}`);

export class FacilityClient {
  readonly publicClient: PublicClient;
  readonly deployment: Deployment;
  readonly coordinator: Coordinator;
  private readonly chain;

  constructor(readonly config: ArcConfig, deployment?: Deployment) {
    this.chain = arcChain(config);
    this.publicClient = createPublicClient({ chain: this.chain, transport: http(config.rpcUrl) }) as PublicClient;
    this.deployment = deployment ?? loadDeployment(config);
    this.coordinator = new Coordinator(config.coordinator, config.chainId, this.deployment.facility);
  }

  private wallet(account: LocalAccount) {
    return createWalletClient({ account, chain: this.chain, transport: http(this.config.rpcUrl) });
  }

  private receipt(r: TransactionReceipt): ArcReceipt {
    return {
      network: this.config.mode === "fork" ? "arc-mainnet-fork (local)" : "arc-testnet",
      txHash: r.transactionHash,
      explorerUrl: explorerTx(this.config, r.transactionHash),
      blockNumber: r.blockNumber.toString(),
      simulatedChain: this.config.mode === "fork",
    };
  }

  /** Simulate first so policy reverts surface as typed errors, then send and await the receipt. */
  async write(account: LocalAccount, functionName: string, args: readonly unknown[], address: Hex = this.deployment.facility, contractAbi: any = abi) {
    const request = await this.simulate(account, functionName, args, address, contractAbi);
    const hash = await this.wallet(account).writeContract(request as any);
    const r = await this.publicClient.waitForTransactionReceipt({ hash });
    if (r.status !== "success") throw new Error(`${functionName} reverted in ${hash}`);
    return r;
  }

  /** eth_call simulation; policy reverts become typed ArcPolicyErrors. */
  async simulate(account: LocalAccount, functionName: string, args: readonly unknown[], address: Hex = this.deployment.facility, contractAbi: any = abi) {
    try {
      const { request } = await this.publicClient.simulateContract({ account, address, abi: contractAbi, functionName, args } as any);
      return request;
    } catch (error) {
      if (error instanceof BaseError) {
        const revert = error.walk((e) => e instanceof ContractFunctionRevertedError);
        if (revert instanceof ContractFunctionRevertedError && revert.data?.errorName) {
          const name = revert.data.errorName;
          throw new ArcPolicyError(POLICY_CODES[name] ?? name, name);
        }
      }
      throw error;
    }
  }

  read<T>(functionName: string, args: readonly unknown[] = []): Promise<T> {
    return this.publicClient.readContract({ address: this.deployment.facility, abi, functionName, args } as any) as Promise<T>;
  }

  // ------------------------------------------------------------------ verify
  async openFacility(input: { facilityKey: string; receivableKey: string; faceValueMinor: bigint; advanceRateBps: number; maturity: number; verificationExpiry: number; hederaTxId: string; hcsSequence: number }) {
    const attestation = {
      facilityId: id32(input.facilityKey),
      receivableId: id32(input.receivableKey),
      operator: this.config.operator.address,
      treasury: this.config.treasury,
      faceValue: input.faceValueMinor,
      advanceRateBps: input.advanceRateBps,
      maturity: BigInt(input.maturity),
      verificationExpiry: BigInt(input.verificationExpiry),
      hederaTxHash: id32(input.hederaTxId),
      hcsSequence: BigInt(input.hcsSequence),
      expiry: expiryFromNow(),
    };
    const signature = await this.coordinator.signReceivable(attestation);
    return this.receipt(await this.write(this.config.owner, "openFacility", [attestation, signature]));
  }

  // ------------------------------------------------------------------ fund
  async fund(input: { facilityKey: string; lenderId: LenderId; principalMinor: bigint; aprBps: number; minTermDays: number }) {
    const lender = this.deployment.lenders[input.lenderId];
    const lenderAccount = this.config.lenders[input.lenderId];
    const strategyHash = (lender?.strategyHash ?? id32(`direct:${input.lenderId}:${input.aprBps}`)) as Hex;
    const attestation = {
      facilityId: id32(input.facilityKey),
      lender: lenderAccount.address,
      principal: input.principalMinor,
      aprBps: input.aprBps,
      minTermDays: input.minTermDays,
      strategyHash,
      expiry: expiryFromNow(),
    };
    const signature = await this.coordinator.signFunding(attestation);

    if (this.deployment.aquaRouter && lender?.encodedOrder) {
      // 1 bp band: the lender receives at most 0.01% more notes than USDC paid out.
      const maxNotes = input.principalMinor + input.principalMinor / 10_000n + 1n;
      const fill = buildFacilityFill({
        routerAddress: this.deployment.aquaRouter,
        encodedOrder: lender.encodedOrder,
        usdcAddress: this.deployment.usdc,
        noteAddress: this.deployment.note,
        principalMinor: input.principalMinor,
        maxNotesMinor: maxNotes,
        deadline: Math.floor(Date.now() / 1000) + 600,
      });
      const r = await this.write(this.config.coordinator, "fundViaAqua", [attestation, signature, fill.data, maxNotes]);
      return { ...this.receipt(r), viaAqua: true, strategyHash, notesMinor: this.notesFromLogs(r)?.toString() ?? null, lender: lenderAccount.address };
    }

    await this.ensureAllowance(lenderAccount, input.principalMinor);
    const r = await this.write(lenderAccount, "fundDirect", [attestation, signature]);
    return { ...this.receipt(r), viaAqua: false, strategyHash, notesMinor: input.principalMinor.toString(), lender: lenderAccount.address };
  }

  private notesFromLogs(r: TransactionReceipt): bigint | undefined {
    for (const log of r.logs) {
      try {
        const event = decodeEventLog({ abi, data: log.data, topics: log.topics }) as { eventName: string; args: Record<string, unknown> };
        if (event.eventName === "LenderFunded") return event.args.notes as bigint;
      } catch {
        // not a facility event
      }
    }
    return undefined;
  }

  private async ensureAllowance(owner: LocalAccount, amount: bigint) {
    const allowance = (await this.publicClient.readContract({ address: this.deployment.usdc, abi: usdcArtifact.abi as any, functionName: "allowance", args: [owner.address, this.deployment.facility] })) as bigint;
    if (allowance < amount) await this.write(owner, "approve", [this.deployment.facility, 2n ** 255n], this.deployment.usdc, usdcArtifact.abi);
  }

  // ------------------------------------------------------------------ budgets
  async assignBudget(input: { facilityKey: string; agentIdentity: string; allocationMinor: bigint; perTxMinor: bigint; dailyMinor: bigint; merchants: string[] }) {
    const facilityId = id32(input.facilityKey);
    const agent = agentAddress(input.agentIdentity);
    const receipts = [this.receipt(await this.write(this.config.operator, "assignBudget", [facilityId, agent, input.allocationMinor, input.perTxMinor, input.dailyMinor]))];
    for (const merchant of input.merchants) {
      receipts.push(this.receipt(await this.write(this.config.operator, "setMerchant", [facilityId, agent, merchantKey(merchant), true])));
    }
    return { agent, receipts };
  }

  /** The operator is the lead agent; it only needs merchants allowlisted. */
  async allowLeadMerchant(facilityKey: string, merchant: string) {
    return this.receipt(await this.write(this.config.operator, "setMerchant", [id32(facilityKey), this.config.operator.address, merchantKey(merchant), true]));
  }

  // ------------------------------------------------------------------ spend
  agentFor(agentIdentity: string): Hex {
    return agentIdentity === "lead" ? this.config.operator.address : agentAddress(agentIdentity);
  }

  /** With dryRun the reservation is only simulated against current chain state (no transaction). */
  async reserve(input: { facilityKey: string; intentKey: string; agentIdentity: string; merchant: string; amountMinor: bigint; ttlSeconds: number }, dryRun = false) {
    const args = [
      id32(input.facilityKey),
      id32(input.intentKey),
      this.agentFor(input.agentIdentity),
      merchantKey(input.merchant),
      input.amountMinor,
      BigInt(input.ttlSeconds),
    ] as const;
    if (dryRun) {
      await this.simulate(this.config.coordinator, "reserveSpend", args);
      return { dryRun: true, accepted: true };
    }
    return this.receipt(await this.write(this.config.coordinator, "reserveSpend", args));
  }

  async commit(input: { facilityKey: string; intentKey: string; amountMinor: bigint; hederaTxId: string; hcsSequence: number }) {
    const attestation = {
      facilityId: id32(input.facilityKey),
      intentId: id32(input.intentKey),
      amount: input.amountMinor,
      hederaTxHash: id32(input.hederaTxId),
      hcsSequence: BigInt(input.hcsSequence),
      expiry: expiryFromNow(),
    };
    const signature = await this.coordinator.signSpend(attestation);
    return this.receipt(await this.write(this.config.owner, "commitSpend", [attestation, signature]));
  }

  async release(input: { facilityKey: string; intentKey: string }) {
    return this.receipt(await this.write(this.config.coordinator, "releaseSpend", [id32(input.facilityKey), id32(input.intentKey)]));
  }

  async reservation(facilityKey: string, intentKey: string) {
    const r = await this.read<{ agent: Hex; amount: bigint; expiresAt: bigint; state: number }>("reservation", [id32(facilityKey), id32(intentKey)]);
    return { agent: r.agent, amountMinor: r.amount.toString(), expiresAt: Number(r.expiresAt), state: ["NONE", "RESERVED", "COMMITTED", "RELEASED"][r.state] };
  }

  // ------------------------------------------------------------------ repay
  async repay(input: { facilityKey: string; amountMinor: bigint }) {
    await this.ensureAllowance(this.config.customer, input.amountMinor);
    const r = await this.write(this.config.customer, "repay", [id32(input.facilityKey), input.amountMinor]);
    let toLenders = 0n;
    let toOperator = 0n;
    let undrawnReturned = 0n;
    for (const log of r.logs) {
      try {
        const event = decodeEventLog({ abi, data: log.data, topics: log.topics }) as { eventName: string; args: Record<string, bigint> };
        if (event.eventName === "RepaymentReceived") {
          toLenders = event.args.toLenders;
          toOperator = event.args.toOperator;
        }
        if (event.eventName === "FacilityClosed") undrawnReturned = event.args.undrawnReturned;
      } catch {
        // token transfer logs
      }
    }
    return { ...this.receipt(r), toLendersMinor: toLenders.toString(), toOperatorMinor: toOperator.toString(), undrawnReturnedMinor: undrawnReturned.toString() };
  }

  // ------------------------------------------------------------------ status
  /** overdue/default are permissionless once the time has passed; dispute/resolve are coordinator calls. */
  async status(input: { facilityKey: string; action: StatusAction; upheld?: boolean }) {
    const facilityId = id32(input.facilityKey);
    const calls: Record<StatusAction, [string, readonly unknown[]]> = {
      overdue: ["markOverdue", [facilityId]],
      default: ["markDefaulted", [facilityId]],
      dispute: ["flagDispute", [facilityId]],
      resolve: ["resolveDispute", [facilityId, input.upheld === true]],
    };
    const [functionName, args] = calls[input.action];
    const r = await this.write(this.config.coordinator, functionName, args);
    const f = await this.read<{ status: number }>("facility", [facilityId]);
    return { ...this.receipt(r), status: FACILITY_STATUS[f.status] };
  }

  async facilityState(facilityKey: string) {
    const f = await this.read<Record<string, unknown>>("facility", [id32(facilityKey)]);
    const allocations = await this.read<Array<Record<string, unknown>>>("allocations", [id32(facilityKey)]);
    const stringify = (value: unknown) => (typeof value === "bigint" ? value.toString() : value);
    return {
      facility: Object.fromEntries(Object.entries(f).map(([k, v]) => [k, stringify(v)])),
      allocations: allocations.map((a) => Object.fromEntries(Object.entries(a).map(([k, v]) => [k, stringify(v)]))),
      availableCreditMinor: (await this.read<bigint>("availableCredit", [id32(facilityKey)])).toString(),
    };
  }
}
