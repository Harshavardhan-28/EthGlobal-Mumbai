import { createHash } from "node:crypto";

/**
 * HCS-14 Universal Agent ID, AID (hash-based) method.
 * Canonical fields: registry, name, version, protocol, nativeId, skills.
 * Strings are trimmed; registry and protocol lowercased; skills sorted ascending;
 * JSON keys sorted; SHA-384 over UTF-8; Base58 encoded.
 * Format: uaid:aid:{hash};uid={uid};registry={registry};proto={proto};nativeId={nativeId}
 * Spec: https://hol.org/docs/standards/hcs-14/
 */
export type AgentIdentity = {
  registry: string;
  name: string;
  version: string;
  protocol: string;
  nativeId: string;
  skills: number[];
  uid?: string;
};

const ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";

export function base58(bytes: Uint8Array): string {
  let value = BigInt(`0x${Buffer.from(bytes).toString("hex") || "0"}`);
  let out = "";
  while (value > 0n) {
    out = ALPHABET[Number(value % 58n)] + out;
    value /= 58n;
  }
  for (const byte of bytes) {
    if (byte !== 0) break;
    out = `1${out}`;
  }
  return out;
}

export function canonicalAgentJson(agent: AgentIdentity): string {
  for (const skill of agent.skills) {
    if (!Number.isInteger(skill) || skill < 0 || (skill >= 40 && skill <= 99)) throw new Error(`Invalid HCS-14 skill ID ${skill}.`);
  }
  const canonical = {
    name: agent.name.trim(),
    nativeId: agent.nativeId.trim(),
    protocol: agent.protocol.trim().toLowerCase(),
    registry: agent.registry.trim().toLowerCase(),
    skills: [...agent.skills].sort((a, b) => a - b),
    version: agent.version.trim(),
  };
  for (const [key, value] of Object.entries(canonical)) {
    if (value === "" ) throw new Error(`HCS-14 field ${key} is required.`);
  }
  return JSON.stringify(canonical);
}

export function uaid(agent: AgentIdentity): string {
  const hash = base58(createHash("sha384").update(canonicalAgentJson(agent), "utf8").digest());
  const params = [`uid=${agent.uid?.trim() || "0"}`, `registry=${agent.registry.trim().toLowerCase()}`, `proto=${agent.protocol.trim().toLowerCase()}`, `nativeId=${agent.nativeId.trim()}`];
  return `uaid:aid:${hash};${params.join(";")}`;
}

/** The demo's agent team, anchored to the operator's Hedera testnet account. */
export function demoAgents(nativeAccountId: string): Array<AgentIdentity & { identity: string }> {
  const nativeId = `hedera:testnet:${nativeAccountId}`;
  return [
    { identity: "lead", name: "Northstar Lead Agent", skills: [0, 1] },
    { identity: "research", name: "Northstar Research Agent", skills: [0, 7] },
    { identity: "compute", name: "Northstar Compute Agent", skills: [0, 9] },
    { identity: "data", name: "Northstar Data Agent", skills: [0, 7] },
  ].map((agent) => ({ ...agent, registry: "working-capital", version: "1.0.0", protocol: "x402", nativeId, uid: agent.identity }));
}
