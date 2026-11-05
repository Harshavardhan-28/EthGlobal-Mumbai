# Demo script (about 4 minutes)

Before recording:

1. Start the stack.
2. Run `npm run demo:reset`.
3. Open the dashboard full screen.

Say "local Arc fork", "simulated" and "IRN simulated" wherever the UI shows those labels.

| Time | Click | Say |
|---|---|---|
| 0:00 | (dashboard at Verify) | "AI agents pay for data and compute every day, but customers pay in 30 to 60 days. We turn one GST invoice into a shared USDC credit line for a whole team of agents, with no action needed from the customer." |
| 0:20 | **Verify GST invoice** | "Northstar invoices RetailCo $10,000, Net 30. We check both GSTINs: format, state code and check digit. They are different legal entities, and the e-invoice IRN matches the seller, financial year and invoice number. That's the top evidence tier. The invoice gets one canonical ID and is tokenized as a single-unit ATS bond on Hedera, so it can never be financed twice." |
| 0:50 | (point at Credit and compliance) | "Northstar has passed KYB and sanctions screening. Its credit score is 740, the Established tier, from four on-time repayments and clean agent history. That allows a $10,000 limit and a 75% advance: $7,500. A brand-new borrower would start at $1,000." |
| 1:10 | **Match lenders** | "Three KYC-approved lenders posted strategies on 1inch Aqua. Our deterministic solver filters them and fills the cheapest first. Alice and Bob are filled through the official Aqua SwapVM router and receive lender notes. Charlie at 13% isn't needed. The Arc facility opened only after a signed, expiring coordinator attestation." |
| 1:45 | (point at the budgets) | "The lead agent splits $6,000 into reserved sub-budgets, with per-transaction, daily and merchant limits. More agents never means more credit." |
| 2:00 | **Run research agent · $0.25** | "The research agent pays for data through x402 with no human in the loop. The amount is reserved on Arc, paid on Hedera, and committed only after a finalized receipt." |
| 2:30 | **Attempt overspend** | "Compute asks for $5,500 against its $3,000 budget: AGENT_BUDGET_EXCEEDED. The engine rejects it, and so does the Arc contract." |
| 2:45 | **Test facility ceiling** | "The lead agent asks for more than the whole line: GLOBAL_CREDIT_LIMIT_EXCEEDED." |
| 3:00 | **Partial repayment** | "RetailCo pays $3,000 first. All of it goes to lenders. The business gets nothing yet." |
| 3:15 | **Customer pays the rest** | "Lenders are made whole: $6,036.98 including fees. The business gets the remainder plus the undrawn credit. Debt is zero." |
| 3:30 | (point at the score) | "The score moves from 740 to 760: up 40 for paying on time, down 20 for the two overspend attempts. One more on-time invoice unlocks the Trusted tier at $25,000. If RetailCo had paid 7 days late, spending would have frozen. At 30 days it's a default, and lenders are still paid first." |
| 3:50 | (point at the trust note) | "Credit decisions are deterministic: AI can explain them but never approve them. Hedera and Arc are not atomic, so a coordinator attestation with expiry and replay protection bridges them." |

After the table, show:

- the Arc Studio project for 10–15 seconds, for the Arc track;
- a HashScan link for the Hedera x402 transaction.
