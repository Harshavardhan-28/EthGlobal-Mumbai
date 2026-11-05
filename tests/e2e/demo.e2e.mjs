// Browser rehearsal of the full demo (docs/PLAN.md acceptance criteria).
// Requires the API on API_URL and the dashboard on WEB_URL:  npm run e2e
import { createRequire } from "node:module";
const { chromium } = createRequire(import.meta.url)("playwright"); // npm install --no-save playwright@1.63.0
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";

const WEB = process.env.WEB_URL ?? "http://localhost:3000";
const API = process.env.API_URL ?? "http://localhost:8000";
const SHOTS = process.env.E2E_SCREENSHOTS ?? "tests/e2e/screenshots";
mkdirSync(SHOTS, { recursive: true });

const reset = await fetch(`${API}/api/demo/retailco-net30/reset`, { method: "POST" });
assert.equal(reset.status, 200, "API reset");

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const consoleErrors = [];
const failedRequests = [];
page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });
page.on("requestfailed", (r) => { if (!r.url().includes("/events/stream")) failedRequests.push(`${r.method()} ${r.url()} ${r.failure()?.errorText}`); });

await page.goto(WEB, { waitUntil: "networkidle" });
await page.getByRole("heading", { name: "Working capital desk" }).waitFor();

async function act(buttonName, expectText) {
  const button = page.getByRole("button", { name: buttonName });
  await button.waitFor();
  assert.equal(await button.isEnabled(), true, `${buttonName} should be enabled`);
  await button.click();
  await page.locator(".inline-alert").filter({ hasText: expectText }).waitFor({ timeout: 120_000 });
  console.log(`✓ ${buttonName.toString()} → ${expectText}`);
}

await act(/Verify GST invoice/, "GST invoice verified");
const receivable = await page.locator(".receivable-panel").innerText();
assert.match(receivable, /GST e-invoice verified/);
assert.match(receivable, /Buyer GSTIN/);
assert.match(receivable, /E-invoice IRN/);
assert.match(await page.locator(".credit-panel").innerText(), /740[\s\S]*Established/);
console.log("✓ GST checks, evidence tier and credit score shown");
await act(/Match lenders/, "Facility funded");
assert.match(await page.locator(".funding-panel").innerText(), /Not needed/);
await act(/Run research agent/, "x402 payment settled");
await page.getByText("x402 settled").waitFor();
await act(/Attempt overspend/, "AGENT_BUDGET_EXCEEDED");
await act(/Test facility ceiling/, "GLOBAL_CREDIT_LIMIT_EXCEEDED");
await page.screenshot({ path: `${SHOTS}/1-spend.png`, fullPage: true });
await act(/Partial repayment/, "Partial repayment applied");
await act(/Customer pays the rest/, "Repayment complete");
await page.getByText("All obligations settled").waitFor();
await page.locator(".credit-panel").filter({ hasText: "740 → 760" }).waitFor();
console.log("✓ credit score 740 → 760 after on-time repayment");
const final = await (await fetch(`${API}/api/demo/retailco-net30`)).json();
assert.equal(final.lifecycle, "REPAID");
assert.equal(final.facility.outstanding, "0.00");
assert.equal(final.facility.lenderPaid, final.facility.lenderDue);
assert.equal(final.credit.after.score, 760);
await page.screenshot({ path: `${SHOTS}/2-repaid.png`, fullPage: true });
console.log("✓ final state: debt $0, receivable REPAID");

// Keyboard: the reset control is reachable and operable without a mouse.
await page.getByRole("button", { name: /Reset demo/ }).focus();
await page.keyboard.press("Enter");
await page.getByRole("button", { name: /Verify GST invoice/ }).waitFor();
console.log("✓ keyboard reset returns to Verify");

// Narrow viewport: no horizontal page overflow.
await page.setViewportSize({ width: 390, height: 900 });
await page.waitForTimeout(300);
const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
assert.ok(overflow <= 1, `page overflows horizontally by ${overflow}px at 390px`);
await page.screenshot({ path: `${SHOTS}/3-mobile.png`, fullPage: true });
console.log("✓ no horizontal overflow at 390px");

assert.deepEqual(failedRequests, [], `failed requests: ${failedRequests.join("; ")}`);
assert.deepEqual(consoleErrors, [], `console errors: ${consoleErrors.join("; ")}`);
console.log("✓ no console errors or failed requests");
await browser.close();
