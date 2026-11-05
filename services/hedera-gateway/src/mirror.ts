/** Hedera Mirror Node reads with retries for ingestion lag. */
const DEFAULT_MIRROR = "https://testnet.mirrornode.hedera.com/api/v1";

export function mirrorTransactionId(transactionId: string): string {
  // SDK form 0.0.123@1700000000.000000001 -> Mirror form 0.0.123-1700000000-000000001
  const match = /^(\d+\.\d+\.\d+)@(\d+)\.(\d+)$/.exec(transactionId.trim());
  return match ? `${match[1]}-${match[2]}-${match[3]}` : transactionId.trim();
}

export type MirrorTransaction = {
  transaction_id: string;
  result: string;
  name: string;
  consensus_timestamp: string;
  entity_id?: string | null;
};

export async function getMirrorTransaction(transactionId: string, options: { baseUrl?: string; attempts?: number; delayMs?: number } = {}): Promise<MirrorTransaction> {
  const baseUrl = (options.baseUrl ?? process.env.HEDERA_MIRROR_NODE_URL ?? DEFAULT_MIRROR).replace(/\/+$/, "").replace(/\/api\/v1$/, "") + "/api/v1";
  const attempts = options.attempts ?? 8;
  let lastError = "not found";
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const response = await fetch(`${baseUrl}/transactions/${encodeURIComponent(mirrorTransactionId(transactionId))}`, { signal: AbortSignal.timeout(10_000) });
    if (response.ok) {
      const body = (await response.json()) as { transactions?: MirrorTransaction[] };
      const tx = body.transactions?.[0];
      if (tx) return tx;
    } else {
      lastError = `HTTP ${response.status}`;
    }
    await new Promise((resolve) => setTimeout(resolve, options.delayMs ?? 1_500));
  }
  throw new Error(`Mirror Node has no record of ${transactionId} (${lastError}).`);
}

export async function getTopicMessage(topicId: string, sequence: number, baseUrl = process.env.HEDERA_MIRROR_NODE_URL ?? DEFAULT_MIRROR) {
  const root = baseUrl.replace(/\/+$/, "").replace(/\/api\/v1$/, "") + "/api/v1";
  const response = await fetch(`${root}/topics/${encodeURIComponent(topicId)}/messages/${sequence}`, { signal: AbortSignal.timeout(10_000) });
  if (!response.ok) throw new Error(`Mirror Node topic message lookup failed with HTTP ${response.status}.`);
  return (await response.json()) as { consensus_timestamp: string; sequence_number: number; message: string };
}
