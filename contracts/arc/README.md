# Arc contracts

| Contract | Purpose |
|---|---|
| `src/WorkingCapitalFacility.sol` | Receivables-backed USDC credit facility shared by an agent team |
| `src/LenderNote.sol` | Non-transferable lender credit note (RCN). It is minted when a lender funds and burned when that lender is repaid |
| `src/mocks/TestUSDC.sol` | Test-only 6-decimal USDC for the local Arc fork. Arc's native USDC needs an Arc precompile that Anvil lacks |

## Guarantees and the tests that check them

| Guarantee | Enforced by | Tests |
|---|---|---|
| One receivable is financed only once | `usedReceivables` lock in `openFacility` | `test_RevertWhen_ReceivableFinancedTwice` |
| Hedera facts arrive only through fresh, signed, single-use attestations | EIP-712 + `expiry` + `usedAttestations` | `…AttestationReplayed`, `…AttestationExpired`, `…SignerIsNotCoordinator` |
| Stale claims stop borrowing | `verificationExpiry` check on open, fund and reserve | `test_RevertWhen_VerificationIsStale` |
| Debt never exceeds the borrowing base | `_fundable` | `test_RevertWhen_FundingExceedsBorrowingBase`, `invariant_CreditCeiling` |
| Sub-budgets are reservations of the funded line | `assignBudget` | `test_BudgetsCannotExceedFundedPrincipal`, `invariant_AgentBudgets` |
| Global, agent, per-tx, daily and merchant limits are independent | `reserveSpend` (same order as the Python engine) | `test_ComputeOverspend…`, `test_LeadAgentIsBoundByFacilityCeiling`, `test_PerTransactionDailyAndMerchantLimits`, fuzz |
| A failed or stuck payment never consumes budget permanently | `releaseSpend` (coordinator, or anyone after the TTL) | `test_TimeoutReleaseRestoresBudget`, `…ConfirmedFailure` |
| Commit happens exactly once | Reservation state + attestation replay lock | `test_RevertWhen_CommitReplayed` |
| Lenders are paid principal plus fee before the operator, including partial payments | `repay` pro-rata waterfall | `test_DemoWaterfall…`, `test_PartialRepayment…`, `testFuzz_RepaymentConservesValue`, `invariant_LendersBeforeOperator` |
| The facility is always solvent for undrawn principal | Accounting | `invariant_FacilitySolvency` |
| Aqua fills come from the attested lender and deliver exactly the principal | Balance-delta checks in `fundViaAqua` | `npm run arc:fork:e2e` (fork integration) |
| Only KYC-approved lenders can fund | `approvedLender` in `_checkLender` | `test_RevertWhen_LenderNotApproved` |
| Sanctions-blocked addresses cannot open, fund, receive a budget, spend or repay | `blocked` checks | `test_RevertWhen_LenderIsBlocked`, `…OperatorIsBlockedAtOpen`, `…AgentIsBlocked`, `…BlockedPayerRepays` |
| Unpaid facilities become Overdue after 7 days and Defaulted after 30; anyone can trigger it, nobody can trigger it early | `markOverdue`, `markDefaulted` | `test_MarkOverdueOnlyAfterGraceAndItFreezesSpending`, `test_RevertWhen_DefaultMarkedEarly` |
| A defaulted facility still pays lenders first, with interest for the actual time | `repay` accepts Overdue, Defaulted and Disputed | `test_DefaultedFacilityStillPaysLendersBeforeOperator` |
| A dispute freezes spending; upheld means default, cleared means resume | `flagDispute`, `resolveDispute` | `test_DisputeFreezesSpendingAndClearsBackToActive`, `test_UpheldDisputeIsADefault` |
| Only the owner (compliance admin) changes allowlists and blocks | `onlyOwner` | `test_OnlyOwnerSetsCompliance` |

Status values: `None 0, Open 1, Active 2, Repaid 3, Overdue 4, Defaulted 5, Disputed 6` (new states were appended so `Repaid` keeps its number).

The lender fee is `principal × APR × max(elapsed, minTermDays) / 365 days`. The demo uses a 30-day minimum term, so an immediate repayment still pays the Net 30 fee.

## Commands

```sh
forge install foundry-rs/forge-std@v1.11.0 OpenZeppelin/openzeppelin-contracts@v5.6.1 --no-git
forge test -vv
ARC_APPROVED_LENDERS=0x…,0x… forge script script/Deploy.s.sol --rpc-url https://rpc.testnet.arc.io --broadcast --private-key $ARC_PRIVATE_KEY
```

## Arc Studio

The Arc prize requires the project to start in Arc Studio. Create a Studio project, bring in `WorkingCapitalFacility.sol`, `LenderNote.sol` and the tests, deploy to Arc testnet from Studio, and record the Studio project URL and deployed address in the root README. If Studio regenerates code, keep the reviewed source and these tests as the reference.

Not audited.
