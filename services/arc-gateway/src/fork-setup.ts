/**
 * Prepares a local Anvil fork of Arc mainnet (chain 5042) for the demo:
 *   1. checks the official 1inch Aqua registry and AquaSwapVMRouter exist on the fork,
 *   2. deploys the labelled TestUSDC stand-in and the WorkingCapitalFacility,
 *   3. funds the demo lenders and RetailCo with TestUSDC,
 *   4. approves Alice, Bob and Charlie as KYC-verified lenders on the facility,
 *   5. has them approve Aqua and `ship` RECEIVABLE_CREDIT_V1 strategies.
 * Writes deployments/arc-fork.json. Run `npm run arc:fork` first in another terminal.
 */
import { createRequire } from "node:module";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import { createPublicClient, createWalletClient, http, parseAbi, type Hex } from "viem";
import { buildLenderShip, buildReceivableCreditOrder, loadAquaConfig } from "@working-capital/aqua-gateway";
import { arcChain, loadArcConfig, type Deployment, type LenderId } from "./config.js";
import { facilityArtifact, usdcArtifact } from "./facility.js";

const require = createRequire(import.meta.url);
const fixture = require("@working-capital/shared/fixtures/retailco-demo.json") as {
  receivable: { faceValueMinor: number };
  lenders: Array<{ id: LenderId; aprBps: number; availableMinor: number; minBuyerScore: "A" | "A-" | "B"; maxAdvanceRateBps: number; maxTenorDays: number }>;
};

const config = loadArcConfig({ ...process.env, ARC_MODE: "fork" });
const chain = arcChain(config);
const client = createPublicClient({ chain, transport: http(config.rpcUrl) });
const wallet = (account: typeof config.owner) => createWalletClient({ account, chain, transport: http(config.rpcUrl) });
const erc20 = parseAbi(["function mint(address,uint256)", "function approve(address,uint256) returns (bool)"]);

async function confirm(hash: Hex) {
  const receipt = await client.waitForTransactionReceipt({ hash });
  if (receipt.status !== "success") throw new Error(`Transaction ${hash} reverted.`);
  return receipt;
}

async function main() {
  const chainId = await client.getChainId();
  if (chainId !== 5042) throw new Error(`Expected an Arc mainnet fork (5042), found chain ${chainId}. Start it with npm run arc:fork.`);
  const aqua = loadAquaConfig({ AQUA_RPC_URL: config.rpcUrl, AQUA_USDC_ADDRESS: "0x0000000000000000000000000000000000000001" });
  for (const [name, address] of [["Aqua registry", aqua.aquaAddress], ["AquaSwapVMRouter", aqua.swapVmRouterAddress]]) {
    const code = await client.getCode({ address: address as Hex });
    if (!code || code === "0x") throw new Error(`${name} ${address} has no code on this fork. Fork Arc mainnet, not Arc testnet.`);
    console.log(`✓ ${name} found at ${address}`);
  }

  // Gas on Arc is native USDC; give the demo accounts gas on the local fork only.
  const accounts = [config.owner, config.coordinator, config.operator, config.customer, ...Object.values(config.lenders)];
  for (const account of accounts) {
    await client.request({ method: "anvil_setBalance" as any, params: [account.address, "0x3635C9ADC5DEA00000"] as any });
  }

  const usdcHash = await wallet(config.owner).deployContract({ abi: usdcArtifact.abi as any, bytecode: usdcArtifact.bytecode, args: [config.owner.address] });
  const usdc = (await confirm(usdcHash)).contractAddress as Hex;
  const facilityHash = await wallet(config.owner).deployContract({ abi: facilityArtifact.abi as any, bytecode: facilityArtifact.bytecode, args: [usdc, config.coordinator.address, config.owner.address] });
  const facility = (await confirm(facilityHash)).contractAddress as Hex;
  await confirm(await wallet(config.owner).writeContract({ address: facility, abi: facilityArtifact.abi as any, functionName: "setAqua", args: [aqua.swapVmRouterAddress, aqua.aquaAddress] }));
  const note = (await client.readContract({ address: facility, abi: facilityArtifact.abi as any, functionName: "note" })) as Hex;
  console.log(`✓ TestUSDC ${usdc}\n✓ WorkingCapitalFacility ${facility}\n✓ LenderNote ${note}`);

  await confirm(await wallet(config.owner).writeContract({ address: usdc, abi: erc20, functionName: "mint", args: [config.customer.address, BigInt(fixture.receivable.faceValueMinor) * 2n] }));

  const expiresAt = Math.floor(Date.now() / 1000) + 7 * 24 * 3600;
  // Each rehearsal draws the offered amount; ship enough for repeated demo resets.
  const rehearsals = BigInt(process.env.ARC_FORK_REHEARSALS ?? "25");
  const lenders: Deployment["lenders"] = {};
  for (const offer of fixture.lenders) {
    const account = config.lenders[offer.id];
    // Compliance: only KYC-approved lenders can fund the facility.
    await confirm(await wallet(config.owner).writeContract({ address: facility, abi: facilityArtifact.abi as any, functionName: "setLenderApproval", args: [account.address, true] }));
    const amount = BigInt(offer.availableMinor) * rehearsals;
    await confirm(await wallet(config.owner).writeContract({ address: usdc, abi: erc20, functionName: "mint", args: [account.address, amount] }));
    await confirm(await wallet(account).writeContract({ address: usdc, abi: erc20, functionName: "approve", args: [aqua.aquaAddress as Hex, 2n ** 255n] }));
    const order = buildReceivableCreditOrder({
      maker: account.address,
      usdcAddress: usdc,
      noteAddress: note,
      expiresAt,
      terms: { aprBps: offer.aprBps, minBuyerScore: offer.minBuyerScore, maxAdvanceRateBps: offer.maxAdvanceRateBps, maxTenorDays: offer.maxTenorDays },
    });
    const ship = buildLenderShip(aqua, order.encoded, usdc, note, amount);
    const shipTx = await wallet(account).sendTransaction({ to: ship.to as Hex, data: ship.data as Hex });
    await confirm(shipTx);
    lenders[offer.id] = { address: account.address, encodedOrder: order.encoded, strategyHash: order.strategyHash, shipTx, shippedMinor: amount.toString(), expiresAt };
    console.log(`✓ ${offer.id} shipped ${Number(amount) / 1e6} tUSDC to Aqua (${rehearsals} rehearsals of ${offer.availableMinor / 1e6}) · strategy ${order.strategyHash.slice(0, 18)}…`);
  }

  const deployment: Deployment = {
    mode: "fork",
    chainId,
    facility,
    note,
    usdc,
    aquaRegistry: aqua.aquaAddress as Hex,
    aquaRouter: aqua.swapVmRouterAddress as Hex,
    deployedAt: new Date().toISOString(),
    lenders,
  };
  mkdirSync(dirname(config.deploymentPath), { recursive: true });
  writeFileSync(config.deploymentPath, `${JSON.stringify(deployment, null, 2)}\n`);
  console.log(`\nWrote ${config.deploymentPath}`);
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
});
