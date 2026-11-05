---
version: alpha
name: "Working Capital Desk"
description: "A calm, high-trust financial operations desk for receivables-backed agent credit."
colors:
  primary: "#2457D6"
  secondary: "#122C3A"
  background: "#F3F6F8"
  surface: "#FFFFFF"
  text: "#17212B"
  muted: "#667481"
  border: "#DCE3E8"
  success: "#187A57"
  warning: "#A45A13"
  danger: "#B83C3C"
  info: "#286C91"
typography:
  sans:
    fontFamily: "Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
  mono:
    fontFamily: "'IBM Plex Mono', ui-monospace, SFMono-Regular, Menlo, monospace"
rounded:
  sm: "0.375rem"
  DEFAULT: "0.625rem"
  md: "0.875rem"
  lg: "1.125rem"
spacing:
  content-max: "90rem"
  page-gutter: "clamp(1rem, 2.5vw, 2.5rem)"
  section-gap: "1.5rem"
components:
  button:
    height: "2.5rem"
    radius: "0.625rem"
  panel:
    radius: "0.875rem"
    border: "1px solid var(--color-border)"
  table:
    row-height: "3.5rem"
    numeric-font: "var(--font-mono)"
---

# Working Capital Desk Design System

## Overview

### Creative North Star

This should feel like the calm operations desk of a small credit institution: crisp paper surfaces, deep ink, precise amounts, and a restrained blue signal for trusted financial actions. The audience is a hackathon judge or operator following a consequential multi-chain flow on a laptop, often under time pressure.

- **Audience and primary job:** Understand and operate one receivable-backed agent credit lifecycle.
- **Target market(s) and evidence:** Global developer and judge audience; ETHGlobal Mumbai is the setting. The product brief specifies USDC and testnets, not India-specific customer flows.
- **Locale(s) and language policy:** English UI, Indian Standard Time only for event context, UTC for stored timestamps, and locale-aware USD display for the demo.
- **Usage scene:** Desktop-first live presentation with mobile support for inspection.
- **Register:** Product dashboard.
- **Memorable signature:** One connected capital-flow rail links verification, funding, agent spend, and repayment, with its active segment reflecting current lifecycle state.
- **Restraint:** Keep financial figures, policy outcomes, and transaction evidence more prominent than chain branding or decoration.
- **Anti-references:** Generic crypto neon, dense terminal chrome, and undifferentiated grids of rounded cards obscure trust and task hierarchy.
- **Token ownership/runtime mapping:** This file is the design reference; `apps/web/app/globals.css` owns the runtime CSS variables. Components consume semantic variables only. Check values against this file during UI review.

## Colors

The palette is a cool paper-and-ink environment. Primary blue marks the active action and lifecycle position. Green means settled, amber means pending or caution, and red is reserved for rejected or unsafe actions. Neutral border and muted text preserve readable data hierarchy.

## Typography

Inter/system sans provides stable, familiar interface text; IBM Plex Mono is reserved for transaction IDs, addresses, and aligned financial quantities. Use sentence case, tabular numerals, and short labels. Keep full transaction values available when a display is truncated.

## Layout

Use a left navigation rail and a wide document-scrolling workspace capped at 90rem. At desktop, place the lifecycle rail above a two-column operations area; at narrow widths, stack panels and allow tables to scroll within their own region. Amounts align right. No fixed-height page shell or hidden page scrollbar.

## Elevation & Depth

Use white surfaces against a blue-gray canvas, with borders carrying most separation. Reserve soft shadow for overlays only. The active lifecycle stage may use a pale blue surface; don't tint every panel.

## Shapes

Controls use 6px corners, panels 14px. Prefer compact rounded rectangles and fine dividers over pills. Status labels may use a small radius with text and an icon.

## Components

### Foundational visual states

Controls have distinct hover, focus-visible, pressed, disabled, and busy states. Success, warning, and danger always pair color with a label/icon. Loading uses a small stable spinner; reduced-motion preferences stop the rail animation.

### Buttons and actions

Primary blue is reserved for the next lifecycle action. Secondary actions use an outline. Dangerous test actions remain visually separated and explicitly explain their policy result.

### Navigation and data display

The lifecycle rail is the sole large expressive element. Tables use semantic markup, fixed numeric alignment, concise status text, and a visible overflow affordance on narrow screens.

### Forms and overlays

Field labels remain visible. Validation is inline. Costly on-chain actions use a named confirmation surface. Status feedback uses a stable live region.

### Iconography

Use simple inline SVG line icons at 16px and 20px. Keep labels on primary navigation and meaningful actions.

### Motion

Use 140ms color and transform transitions for interaction feedback. The lifecycle rail draws its active connector once after a user action. Respect `prefers-reduced-motion` and never animate balances as if value were changing without a transaction.

### Content and data visualization

Use direct verbs and plain policy outcomes. Format money to two decimal places and show the chain/token near the amount. Data visualizations must include a textual value or table equivalent.

## Do's and Don'ts

- **Do:** Show whether each transaction is live, pending, failed, or simulated.
- **Do:** Keep the borrowing base and remaining credit ceiling visible during spending.
- **Don't:** Use decorative gradients, token-price tickers, or chain logos as hierarchy.
- **Don't:** imply that separate chain operations are atomic.
