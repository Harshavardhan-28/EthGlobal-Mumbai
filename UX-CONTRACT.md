# UX Contract

## Product context

- Audience: operators and judges reviewing one receivable-backed agent credit flow.
- Primary jobs: confirm an invoice, fund a facility, observe controlled agent spending, reject unsafe spend, and settle repayment.
- Target market: global hackathon prototype; the source brief establishes Mumbai as the event location, not a localized financial product.
- Active locales: English.
- Timezone policy: store timestamps in UTC; display the viewer-local time with an explicit timezone.
- Accessibility target: WCAG 2.2 AA.

## Business-context sources

| Domain / scope | Authoritative source | Source type | Reviewed date |
|---|---|---|---|
| Product lifecycle and core flow | `docs/Autonomous_Working_Capital_System.docx` | Product brief | 2026-10-20 |
| Acceptance boundaries and implementation | `docs/PLAN.md` | Project plan | 2026-10-20 |
| Money and repayment behavior | `docs/PLAN.md` | Product decision | 2026-10-20 |

## Visual contract

- Project design: `DESIGN.md`.
- Token ownership: CSS variables in `apps/web/app/globals.css` are the runtime values; `DESIGN.md` records their intent.
- Supported theme: light.
- Design-context owner: project maintainers.

## Canonical UI Map

| Capability | Canonical owner | Source of truth | Allowed variants | Verification |
|---|---|---|---|---|
| Button | `Button` | global CSS variables and component variants | primary, secondary, danger, quiet | keyboard and visual state |
| Toast/status | `StatusMessage` | API/domain response | success, warning, error, pending | live region and retry |
| Lifecycle | `LifecycleRail` | receivable/facility domain state | current, complete, blocked | full demo flow |
| Data display | semantic table and `Amount` | API read model | compact rows, responsive overflow | keyboard and narrow viewport |

## Component behavior

| Component | Default | Focus | Disabled | Busy | Error |
|---|---|---|---|---|---|
| Button | action-specific label | visible blue ring | no handler | stable dimensions and status text | inline status explains recovery |
| Status message | persistent for material result | announced politely | n/a | pending copy | assertive only for blocking errors |
| Lifecycle rail | reflects server state | links/actions remain keyboard accessible | completed stage | active stage announced | blocked stage includes reason |

## Dataset navigation

- Tables are limited to the single seeded receivable and lender offers in v1.
- Search, pagination, and URL filters are not required until datasets grow.
- On small screens, keep each table in an independently scrollable region.

## Flow ledger

| Operation | Trigger | Pending | Success feedback | Failure recovery | Source ref |
|---|---|---|---|---|---|
| Verify GST invoice | `Verify GST invoice` | show confirmation in progress | token ID and next stage | retry only with same idempotency key | Product brief, sections 3 and 9 |
| Match lenders | `Match lenders` | show matching state | allocations and effective rates | explain ineligible offers | Product brief, sections 4 and 8 |
| Run research agent | `Run research agent` | reservation and payment pending | x402 receipt and audit event | release reservation after confirmed failure | Product brief, sections 3, 5, and 9 |
| Attempt overspend | `Attempt overspend` | policy evaluation | rejected with exact limit | preserve facility state | Product brief, sections 5 and 7 |
| Repay facility | `Repay facility` | settlement pending | waterfall and debt zero | preserve pending state and reconcile | Product brief, sections 3 and 7 |

## Navigation and responsive behavior

- One dashboard route for the v1 demo.
- The primary page uses document scrolling and has no hidden global scrollbar.
- Transaction IDs may be shortened visually only when copy/full-value access remains available.

## Overlays and feedback

- Use inline status panels for demo actions; do not use native browser dialogs.
- Any live on-chain action must state its network and consequence before submission.
- Status results remain visible until a subsequent action supersedes them.

## Async and resilience

- Mutations are pessimistic and idempotent.
- Pending state belongs to the action; the whole dashboard remains usable for independent reads.
- Failed or uncertain chain operations retain their operation ID and expose a reconciliation path.
- Stale read requests must not overwrite newer action results.

## Validation and sensitive values

- No private key entry or client-side key storage in the dashboard.
- Monetary fields use integer base units in services and formatted decimal strings in the UI.

## Verification

- Check keyboard use, visible focus, reduced motion, long identifiers, narrow viewport, and success/failure/pending states.
- Compare each flow stage against the product brief and label coordinator assumptions in the interface.
