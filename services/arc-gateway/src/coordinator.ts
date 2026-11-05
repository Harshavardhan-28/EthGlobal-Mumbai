import { keccak256, stringToHex, type Hex } from "viem";
import type { LocalAccount } from "viem/accounts";

/** Deterministic 32-byte identifiers shared by the orchestrator and the contract. */
export const id32 = (value: string): Hex => keccak256(stringToHex(value));

/** Agents act on Hedera; on Arc they are budget keys derived from their HCS-14 identity. */
export const agentAddress = (agentIdentity: string): Hex => `0x${id32(`agent:${agentIdentity}`).slice(26)}` as Hex;

export const ATTESTATION_TTL_SECONDS = 10 * 60;

export type ReceivableAttestation = {
  facilityId: Hex;
  receivableId: Hex;
  operator: Hex;
  treasury: Hex;
  faceValue: bigint;
  advanceRateBps: number;
  maturity: bigint;
  verificationExpiry: bigint;
  hederaTxHash: Hex;
  hcsSequence: bigint;
  expiry: bigint;
};

export type FundingAttestation = {
  facilityId: Hex;
  lender: Hex;
  principal: bigint;
  aprBps: number;
  minTermDays: number;
  strategyHash: Hex;
  expiry: bigint;
};

export type SpendAttestation = {
  facilityId: Hex;
  intentId: Hex;
  amount: bigint;
  hederaTxHash: Hex;
  hcsSequence: bigint;
  expiry: bigint;
};

const types = {
  ReceivableAttestation: [
    { name: "facilityId", type: "bytes32" },
    { name: "receivableId", type: "bytes32" },
    { name: "operator", type: "address" },
    { name: "treasury", type: "address" },
    { name: "faceValue", type: "uint128" },
    { name: "advanceRateBps", type: "uint16" },
    { name: "maturity", type: "uint64" },
    { name: "verificationExpiry", type: "uint64" },
    { name: "hederaTxHash", type: "bytes32" },
    { name: "hcsSequence", type: "uint64" },
    { name: "expiry", type: "uint64" },
  ],
  FundingAttestation: [
    { name: "facilityId", type: "bytes32" },
    { name: "lender", type: "address" },
    { name: "principal", type: "uint128" },
    { name: "aprBps", type: "uint16" },
    { name: "minTermDays", type: "uint32" },
    { name: "strategyHash", type: "bytes32" },
    { name: "expiry", type: "uint64" },
  ],
  SpendAttestation: [
    { name: "facilityId", type: "bytes32" },
    { name: "intentId", type: "bytes32" },
    { name: "amount", type: "uint128" },
    { name: "hederaTxHash", type: "bytes32" },
    { name: "hcsSequence", type: "uint64" },
    { name: "expiry", type: "uint64" },
  ],
} as const;

/**
 * Coordinator signer. It observes finalized Hedera facts (Mirror Node) and signs short-lived
 * EIP-712 attestations that the Arc facility verifies, with expiry and replay protection.
 * This signer is the prototype's cross-chain trust assumption.
 */
export class Coordinator {
  constructor(
    private readonly account: LocalAccount,
    private readonly chainId: number,
    private readonly verifyingContract: Hex,
  ) {}

  get address(): Hex {
    return this.account.address;
  }

  private domain() {
    return { name: "WorkingCapitalFacility", version: "1", chainId: this.chainId, verifyingContract: this.verifyingContract } as const;
  }

  signReceivable(message: ReceivableAttestation): Promise<Hex> {
    return this.account.signTypedData!({ domain: this.domain(), types, primaryType: "ReceivableAttestation", message });
  }

  signFunding(message: FundingAttestation): Promise<Hex> {
    return this.account.signTypedData!({ domain: this.domain(), types, primaryType: "FundingAttestation", message });
  }

  signSpend(message: SpendAttestation): Promise<Hex> {
    return this.account.signTypedData!({ domain: this.domain(), types, primaryType: "SpendAttestation", message });
  }
}

export const expiryFromNow = (seconds = ATTESTATION_TTL_SECONDS): bigint => BigInt(Math.floor(Date.now() / 1000) + seconds);
