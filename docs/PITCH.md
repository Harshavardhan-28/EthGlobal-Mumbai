# Pitch

## One line

**Your GST invoice becomes your AI agents' budget.**

## The story (60 seconds)

Meera runs a small analytics company in Mumbai with five AI agents. They buy data and compute all day, paying per request. Her biggest customer, RetailCo, owes her $10,000 (roughly ₹8–9 lakh) on a GST e-invoice, payable in 30 days. Banks take weeks and crypto lenders want collateral she doesn't have, so her agents sit idle.

With us, she uploads the invoice and nobody calls RetailCo:

- We check both GST numbers and the e-invoice reference (IRN) in seconds, and tokenize the invoice on Hedera so it can never be financed twice.
- Her credit score of 740 from past repayments unlocks 75% of the invoice.
- Lenders on 1inch Aqua compete to fund it, cheapest first.
- The money sits in a contract on Arc, and each agent gets its own budget with hard limits.
- Her research agent pays 25 cents for data through x402, with no human involved. When the compute agent tries to overspend, it is blocked twice: by our rules engine and by the contract.
- When RetailCo pays, lenders are repaid first, automatically, and Meera keeps the rest. Her score goes up, so next time she can borrow more.

> **The invoice is the proof. Repayment is automatic. Agents can't overspend. Good behaviour unlocks more credit.**

## Who it is for

Businesses and freelancers whose AI agents spend money before their customers pay. The rules:

- **Who can borrow:** a company, partnership or freelancer (sole proprietor) with a business customer. No personal loans and no consumer invoices.
- **Every borrower is KYC/KYB-checked and sanctions-screened.** Agents never borrow on their own; they spend from their owner's budget.
- **Proof sets the advance.** A GST e-invoice with a matching IRN supports up to 80%, a GSTIN-only or foreign invoice 50%, and a freelancer without a GSTIN 30%.
- **New borrowers start at $1,000.** Every on-time repayment raises the score and the limit, up to $25,000.

## Why it is safe

| Risk | Answer |
|---|---|
| Fake or duplicate invoice | GSTIN check digits, same-PAN block, IRN match, and one canonical ID per invoice that is locked on Hedera and Arc |
| Agents overspend | Per-agent, per-transaction, daily, merchant and facility limits, enforced twice |
| Customer pays late | 7 days late freezes spending; 30 days late is a default; lenders are still paid first |
| New or risky borrower | Small starting limit; the score drops on late payments, defaults and overspend attempts |
| Bad actors | KYC/KYB, sanctions screening, approved lenders only, admin pause (repayments never pause) |
| Privacy | Tax IDs stay private; only hashes and amounts go on-chain |

## Sponsor fit

| Sponsor and track | What we use |
|---|---|
| Hedera: AI Agents That Pay (x402) | Autonomous x402 payments in USDC, agent budgets and merchant limits, HCS-14 identities, HCS audit log |
| Hedera: Tokenize Anything (ATS) | Each invoice is a single-unit receivable bond with compliance controls |
| Arc: DeFi / onchain finance (Arc Studio) | USDC credit facility with attested limits, lenders-first waterfall, overdue/default/dispute states |
| 1inch: Build an Aqua App | Custom `RECEIVABLE_CREDIT_V1` SwapVM strategy; lenders keep their USDC in their own wallets and are filled through the official router |

## Being upfront

- **The demo runs on test money.** It uses test lenders and TestUSDC on a local Arc mainnet fork with the real Aqua contracts. Hedera steps are simulated until testnet keys are added.
- **The demo IRN is simulated.** A real IRN can be passed in. Live IRP lookup needs a GSP account.
- **A GST e-invoice proves the invoice was reported, not that it will be paid.** That residual risk is why limits start small and lenders are paid first.
- **The screening list is fictional.** Production would use a real screening provider.

## WhatsApp version

```
*💸 Autonomous Working Capital*
_Your GST invoice becomes your AI agents' budget._

*Problem:* AI agents pay for data and compute every day, but customers pay invoices in 30–60 days.

*How it works*
1️⃣ *Verify:* We check both GSTINs and the e-invoice IRN. No call to the customer. The invoice is tokenized on *Hedera* so it can't be financed twice.
2️⃣ *Score:* A credit score from past repayments and agent behaviour sets the limit. New users start at $1,000 and grow with every on-time repayment.
3️⃣ *Fund:* Lenders on *1inch Aqua* fund the cheapest-first match.
4️⃣ *Spend:* USDC sits in a contract on *Arc*. Agents pay per request via *x402*, within hard limits.
5️⃣ *Repay:* The customer pays, lenders are repaid first, the business keeps the rest, and the score goes up.

*Safe by design* 🔒 KYC/KYB + sanctions screening · approved lenders only · 7 days late freezes spending, 30 days = default · AI never approves credit

*Who:* businesses and freelancers with business customers. No personal loans.

_Demo: test USDC on a local Arc fork with the real Aqua contracts._
```
