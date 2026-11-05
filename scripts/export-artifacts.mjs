// Copies ABI + bytecode for the Arc contracts from Foundry's out/ into packages/shared/abi.
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const out = join(root, "contracts/arc/out");
const target = join(root, "packages/shared/abi");
mkdirSync(target, { recursive: true });
for (const [file, name] of [["WorkingCapitalFacility.sol", "WorkingCapitalFacility"], ["LenderNote.sol", "LenderNote"], ["TestUSDC.sol", "TestUSDC"]]) {
  const artifact = JSON.parse(readFileSync(join(out, file, `${name}.json`), "utf8"));
  writeFileSync(join(target, `${name}.json`), `${JSON.stringify({ contractName: name, abi: artifact.abi, bytecode: artifact.bytecode.object }, null, 2)}\n`);
  console.log(`exported ${name}`);
}
