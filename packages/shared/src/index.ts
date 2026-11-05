import { mkdirSync, readFileSync, renameSync, writeFileSync, existsSync } from "node:fs";
import { dirname } from "node:path";
import { timingSafeEqual } from "node:crypto";

/**
 * Durable, file-backed idempotency ledger shared by the chain gateways.
 * Completed operations survive a gateway restart, so a retried request with the
 * same idempotency key returns the recorded receipt instead of re-submitting a
 * payment or transaction. Writes are atomic (write temp file, then rename).
 */
export class DurableStore<T> {
  private readonly records: Map<string, T>;

  constructor(private readonly path: string) {
    mkdirSync(dirname(path), { recursive: true });
    this.records = new Map(existsSync(path) ? Object.entries(JSON.parse(readFileSync(path, "utf8")) as Record<string, T>) : []);
  }

  get(key: string): T | undefined {
    return this.records.get(key);
  }

  set(key: string, value: T): void {
    this.records.set(key, value);
    const temp = `${this.path}.${process.pid}.tmp`;
    writeFileSync(temp, JSON.stringify(Object.fromEntries(this.records), (_k, v) => (typeof v === "bigint" ? v.toString() : v), 2));
    renameSync(temp, this.path);
  }

  values(): T[] {
    return [...this.records.values()];
  }
}

/** Runs `operation` at most once per key, even when concurrent retries arrive. */
export class IdempotentExecutor<T> {
  private readonly pending = new Map<string, Promise<T>>();

  constructor(private readonly store: DurableStore<T>) {}

  async run(key: string, operation: () => Promise<T>): Promise<{ result: T; replayed: boolean }> {
    const done = this.store.get(key);
    if (done !== undefined) return { result: done, replayed: true };
    let inflight = this.pending.get(key);
    if (!inflight) {
      inflight = operation().then((result) => {
        this.store.set(key, result);
        return result;
      });
      this.pending.set(key, inflight);
      inflight.finally(() => this.pending.delete(key)).catch(() => undefined);
    }
    return { result: await inflight, replayed: false };
  }
}

export function bearerMatches(header: string | undefined, token: string): boolean {
  const expected = Buffer.from(`Bearer ${token}`);
  const actual = Buffer.from(header ?? "");
  return actual.length === expected.length && timingSafeEqual(actual, expected);
}

export const explorers = {
  hederaTestnetTx: (transactionId: string) => `https://hashscan.io/testnet/transaction/${encodeURIComponent(transactionId)}`,
  hederaTestnetTopic: (topicId: string) => `https://hashscan.io/testnet/topic/${encodeURIComponent(topicId)}`,
  hederaTestnetToken: (tokenId: string) => `https://hashscan.io/testnet/token/${encodeURIComponent(tokenId)}`,
  arcTestnetTx: (hash: string) => `https://explorer.testnet.arc.io/tx/${hash}`,
  arcTestnetAddress: (address: string) => `https://explorer.testnet.arc.io/address/${address}`,
  arcMainnetAddress: (address: string) => `https://explorer.arc.io/address/${address}`,
};

/** Formats integer USDC base units (6 decimals) as a decimal string. */
export function formatUsdc(minor: bigint): string {
  const negative = minor < 0n;
  const value = negative ? -minor : minor;
  const whole = value / 1_000_000n;
  const fraction = (value % 1_000_000n).toString().padStart(6, "0");
  return `${negative ? "-" : ""}${whole}.${fraction}`;
}
