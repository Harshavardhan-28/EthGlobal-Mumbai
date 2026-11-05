# Eligibility, credit and compliance

The rules below are deterministic: the engine (`services/orchestrator/app`) decides, and the Arc contract enforces the on-chain parts again. AI can explain a decision but never makes one. This is a hackathon prototype, not legal advice. Check the rules with a lawyer before real use.

## 1. Who can borrow

| Rule | Code when it fails |
|---|---|
| The borrower is a company, partnership or sole proprietor (freelancer). Personal loans are not offered | `BORROWER_TYPE_INELIGIBLE` |
| The borrower has passed KYC (people) or KYB (businesses) | `KYC_REQUIRED` |
| Every agent belongs to that verified borrower. An agent cannot borrow on its own | (by design: agents only spend from budgets) |
| The buyer is a business with a tax ID. Invoices to consumers are not financed | `CONSUMER_INVOICE_NOT_SUPPORTED` |
| Seller and buyer are different legal entities. In India, a different PAN (GSTIN characters 3–12) | `SELF_INVOICE` |
| The document is a tax invoice (INV), not a credit or debit note | `DOCUMENT_TYPE_INELIGIBLE` |
| The invoice is not yet due and is due within 120 days | `INVOICE_TERM_INELIGIBLE` |
| The invoice has never backed another facility (canonical ID from country, seller, financial year, type and number, so changing the amount or date does not help) | `DUPLICATE_RECEIVABLE` |
| No party, lender, merchant or wallet matches sanctions screening | `SANCTIONS_MATCH`, `LENDER_BLOCKED`, `MERCHANT_BLOCKED` |
| The credit tier is not Blocked, and there is limit left | `CREDIT_TIER_BLOCKED`, `CREDIT_LIMIT_REACHED` |

## 2. How an invoice is verified (no buyer action)

**Indian invoices**

1. **Buyer GSTIN**: format, state code (01–38, 97, 99) and check digit. Invalid → `BUYER_GSTIN_INVALID`.
2. **Seller GSTIN**: the same checks. Invalid → `SELLER_GSTIN_INVALID`. A missing seller GSTIN is allowed only for sole proprietors below the GST threshold.
3. **E-invoice IRN**: NIC's e-invoice sandbox describes the IRN as a SHA-256 hash of supplier GSTIN + financial year + document type + document number. The engine recomputes it and compares.

The check digit follows GSTN's published algorithm and is tested against its worked example (`27AAPFU0939F1Z` → `V`).

**Foreign invoices**: the seller tax ID, buyer tax ID and invoice number are required. They cannot be checked against a government system here, so the advance cap is lower.

| Evidence tier | When | Advance cap |
|---|---|---|
| GST e-invoice verified | Valid GSTINs and a matching IRN | 80% |
| GSTIN verified | Valid GSTINs, no IRN or an IRN that does not match | 50% |
| Foreign invoice | Non-Indian invoice with both tax IDs and an invoice number | 50% |
| Declared | Freelancer without a GSTIN, buyer GSTIN valid | 30% |

**What this does and does not prove.** A matching IRN shows the seller reported the invoice to the GST system. It does not prove the buyer agreed or will pay. A non-matching IRN is downgraded rather than rejected, because the exact IRN concatenation has not been confirmed against a live IRN. Checking that an IRN is registered needs an IRP/GSP API account, which is future work. In the demo the IRN is generated in the IRP's format and labelled **simulated**.

## 3. Credit score and limits

Score = 550, then:

| Factor | Points |
|---|---|
| Each on-time repayment (up to 8) | +40 |
| Each late repayment | −60 |
| Each default | −250 |
| Clean agent payments | +2 each, up to +40 |
| Agent policy rejections (overspend attempts) | −10 each, down to −60 |

The score is bounded to 300–900.

| Tier | Score | Limit | Highest advance |
|---|---|---|---|
| Trusted | 800+ | $25,000 | 80% |
| Established | 700–799 | $10,000 | 75% |
| Building | 600–699 | $3,000 | 60% |
| Starter | 450–599 | $1,000 | 50% |
| Blocked | below 450, or any unresolved default | $0 | 0% |

- **Advance** = lowest of the requested rate, the tier cap and the evidence cap.
- **Borrowing base** = face value × advance, never more than the tier limit minus principal already open on other facilities.
- **A new borrower** starts at 550 (Starter, $1,000) and needs two on-time repayments to reach Building and four to reach Established.
- **The history persists per borrower.** In the demo, **Reset demo** restores the starting history.

## 4. When the customer does not pay

| Time after the due date | State | Effect |
|---|---|---|
| 0–6 days | Active | Spending is already stopped (the verification expires at the due date). Repayment works |
| 7 days | Overdue | Spending frozen. Anyone can trigger this on Arc (`markOverdue`) |
| 30 days | Defaulted | Score −250, borrower blocked from new credit. Anyone can trigger this on Arc (`markDefaulted`) |
| Paid later | Repaid (late or recovered) | Lenders are paid first, with interest for the actual time. A recovered default lifts the block; the default stays in the history |

**Disputes.** A disputed invoice freezes spending. If the dispute is upheld (the invoice is not owed), the seller is in default under recourse. If it is cleared, the facility resumes.

**Residual risk.** Lenders carry the risk that neither the buyer nor the seller pays. The score, small starting limits, evidence caps and lenders-first repayment limit that risk; they do not remove it.

## 5. Compliance policies

| Policy | Control | Enforced in |
|---|---|---|
| KYC / KYB | Borrowers and lenders must be verified before they can borrow or fund | Engine (verify, quote) and Arc (`approvedLender`) |
| Sanctions screening | Borrower, seller, buyer, lenders, merchants and wallets are screened at verification; a match blocks the action | Engine (demo list) and Arc (`blocked`) |
| Allowlists | Only approved lenders fund; agents pay only allowlisted merchants | Engine and Arc |
| Eligibility | Business and freelancer borrowers, business buyers, no personal loans | Engine |
| Credit limits | Score tiers cap the limit and advance | Engine; the Arc borrowing base is attested from it |
| Overdue and default | 7 and 30 days after the due date; spending stops, repayment continues | Engine and Arc (permissionless) |
| Disputes | Freeze spending until resolved | Engine and Arc |
| Emergency pause | Admin pause stops verification, funding and spending; never repayment | Engine (`ADMIN_API_TOKEN`) and Arc (owner `pause`) |
| Data minimisation | Tax IDs, IRNs and names stay private; only the invoice hash, amounts and statuses go to Hedera and Arc | `compliance.public_payload`, tested |
| Record keeping | Every decision is an append-only domain event, mirrored to an HCS audit topic in live mode | Store and HCS |
| USDC issuer controls | USDC can be frozen by its issuer on a lawful order (GENIUS Act). A frozen balance could stop a repayment; this is disclosed, not prevented | Disclosure |

**The screening list is fictional.** `packages/shared/fixtures/screening-demo.json` contains made-up entries for testing, not a real sanctions list. Production should call a screening provider and a KYC/KYB vendor behind the same functions (`compliance.screen`, `compliance.kyc_error`).

**Regulatory notes.**

- Lender notes may be treated as securities, so comparable platforms limit lending to verified or accredited investors.
- Invoice financing usually needs a legal assignment of the receivable.
- Get legal advice for each country before going live.
