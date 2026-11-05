"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

type EventItem = { id: string; title: string; detail: string; time: string; status: string; chain: string; txId?: string | null; explorerUrl?: string | null };
type Agent = { name: string; role: string; identity: string; uaid?: string | null; allocation: string; assigned?: boolean; spent: string; reserved: string; limit: string; daily?: string; merchant: string; color: string };
type Fill = { principal: string; txHash?: string | null; explorerUrl?: string | null; viaAqua: boolean; strategyHash?: string | null; notes: string };
type Offer = { lender: string; aprBps: number; apr: string; available: string; selected: boolean; score: string; eligible?: boolean | null; rejection?: string | null; fill?: Fill | null };
type Spend = { id: string; agent: string; merchant: string; amount: string; status: string; reason?: string | null; txId: string; explorerUrl?: string | null; auditTxId?: string | null; commitTx?: string | null; network: string; createdAt: string };
type Check = { id: string; label: string; ok: boolean; detail: string };
type Credit = { score: number; tier: string; limit: string; headroom: string; advanceRateBps: number; binding: string; nextStep: string; assessed: boolean; after?: { score: number; before: number; tier: string; outcome: string; limit: string } | null; history: Record<string, number>; tiers: { name: string; minScore: number; limit: string; advance: string }[] };
type Compliance = { paused: boolean; kyc: string; screened: boolean; hits: number; blockedLenders: string[]; blockedMerchants: string[]; approvedLenders: number };
type Integration = { label: string; mode: string; ready: boolean; detail: string };
type IntegrationStatus = Record<string, Integration>;
type DemoState = {
  id: string;
  stage: string;
  lifecycle: string;
  mode: string;
  modes: { hedera: string; hederaAudit: string; arc: string; arcLabel: string };
  invoice: { buyer: string; operator: string; invoiceNumber: string; faceValue: string; currency: string; terms: string; advanceRateBps: number; status: string; receivableId: string; canonicalId: string; tokenId: string; tokenSimulated: boolean; tokenExplorerUrl?: string | null; dueDate: string; verification: string; buyerScore: string; country: string; sellerTaxId: string; buyerTaxId: string; irn?: string | null; irnSimulated: boolean; evidenceTier?: string | null; advanceCap?: string | null; checks: Check[]; closedAs?: string | null };
  borrower: { name: string; type: string; kycStatus: string; kycMethod?: string | null };
  credit: Credit;
  compliance: Compliance;
  facility: { status: string; borrowingBase: string; funded: string; outstanding: string; spent: string; reserved: string; remaining: string; feeTotal: string; lenderDue: string; lenderPaid: string; customerPaid: string; operatorRemainder: string; operatorProjected: string; undrawnReturned: string; repaymentAmount: string; chain: string; contract?: string | null };
  offers: Offer[];
  allocations: { lender: string; principal: string; aprBps: number; fee: string; repayment: string; paid: string; txHash?: string | null; viaAqua: boolean }[];
  agents: Agent[];
  events: EventItem[];
  spends: Spend[];
};

const shortId = (value?: string | null, head = 8, tail = 6) => !value ? "" : value.length <= head + tail + 1 ? value : `${value.slice(0, head)}…${value.slice(-tail)}`;

function TxRef({ id, url, label }: { id?: string | null; url?: string | null; label?: string }) {
  if (!id) return null;
  const text = label ?? shortId(id);
  return url ? <a className="tx-link" href={url} target="_blank" rel="noreferrer" title={id}>{text}<Icon name="external" size={11} /></a> : <code className="tx-code" title={id}>{text}</code>;
}

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const fmt = (value: string | number) => Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const remaining = (agent: Agent) => Math.max(0, Number(agent.allocation) - Number(agent.spent) - Number(agent.reserved));

function Icon({ name, size = 18 }: { name: string; size?: number }) {
  const paths: Record<string, React.ReactNode> = {
    grid: <><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></>,
    file: <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M8 13h8M8 17h8"/></>,
    layers: <><path d="m12 2 9 5-9 5-9-5 9-5Z"/><path d="m3 12 9 5 9-5M3 17l9 5 9-5"/></>,
    pulse: <><path d="M3 12h4l3-8 4 16 3-8h4"/></>,
    settings: <><circle cx="12" cy="12" r="3"/><path d="m19.4 15 .1.1a1.7 1.7 0 0 1-2.4 2.4l-.1-.1a1.7 1.7 0 0 0-2.9 1.2v.3a1.7 1.7 0 0 1-3.4 0v-.2A1.7 1.7 0 0 0 7.8 17l-.1.1a1.7 1.7 0 1 1-2.4-2.4l.1-.1a1.7 1.7 0 0 0-1.2-2.9H4a1.7 1.7 0 0 1 0-3.4h.2A1.7 1.7 0 0 0 5.4 5.8l-.1-.1a1.7 1.7 0 1 1 2.4-2.4l.1.1a1.7 1.7 0 0 0 2.9-1.2V2a1.7 1.7 0 0 1 3.4 0v.2a1.7 1.7 0 0 0 2.9 1.2l.1-.1a1.7 1.7 0 1 1 2.4 2.4l-.1.1a1.7 1.7 0 0 0 1.2 2.9h.3a1.7 1.7 0 0 1 0 3.4h-.2a1.7 1.7 0 0 0-1.3 2.9Z"/></>,
    arrow: <><path d="M5 12h14M13 6l6 6-6 6"/></>,
    check: <path d="m5 12 4 4L19 6"/>,
    clock: <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
    refresh: <><path d="M20 7v5h-5M4 17v-5h5"/><path d="M5.6 9A7 7 0 0 1 18 6l2 6M4 12l2 6a7 7 0 0 0 12.4-3"/></>,
    shield: <><path d="M12 22s8-4 8-11V5l-8-3-8 3v6c0 7 8 11 8 11Z"/><path d="m9 12 2 2 4-4"/></>,
    external: <><path d="M15 3h6v6M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/></>,
    warning: <><path d="m10.3 3.9-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.7-3.1l-8-14a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4M12 17h.01"/></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] ?? paths.grid}</svg>;
}

function StageRail({ stage }: { stage: string }) {
  const stages = ["Verify", "Fund", "Spend", "Repay"];
  const current = { verify: 0, fund: 1, spend: 2, repay: 3 }[stage] ?? 0;
  return <section className="rail" aria-label="Credit lifecycle">
    <div className="rail-line"><span style={{ width: `${(current / (stages.length - 1)) * 100}%` }} /></div>
    {stages.map((item, index) => <div className={`rail-stage ${index < current ? "complete" : ""} ${index === current ? "current" : ""}`} key={item}>
      <span className="stage-dot">{index < current ? <Icon name="check" size={13} /> : `0${index + 1}`}</span>
      <span className="stage-name">{item}</span>
      <span className="stage-detail">{["GST verified", "Lender matched", "Agent controls", "Debt cleared"][index]}</span>
    </div>)}
  </section>;
}

function Amount({ children, large = false }: { children: React.ReactNode; large?: boolean }) {
  return <span className={`amount ${large ? "amount-large" : ""}`}><span className="currency">$</span>{children}</span>;
}

const TIER_LABEL: Record<string, string> = { BLOCKED: "Blocked", STARTER: "Starter", BUILDING: "Building", ESTABLISHED: "Established", TRUSTED: "Trusted" };
const EXCEPTION_COPY: Record<string, string> = {
  OVERDUE: "This invoice is overdue. Agent spending is frozen; any payment still goes to lenders first.",
  DEFAULTED: "This invoice has defaulted. Spending is closed and the borrower cannot open new credit until it is recovered.",
  DISPUTED: "This invoice is disputed. Agent spending is frozen until the dispute is resolved.",
};

function CreditPanel({ credit, compliance, borrower }: { credit: Credit; compliance: Compliance; borrower: DemoState["borrower"] }) {
  const position = Math.min(100, Math.max(0, ((credit.score - 300) / 600) * 100));
  const after = credit.after;
  const rows: [string, boolean, string][] = [
    [borrower.type === "SOLE_PROPRIETOR" ? "KYC" : "KYB", borrower.kycStatus === "VERIFIED", borrower.kycStatus === "VERIFIED" ? `${borrower.name} verified` : "Verification required"],
    ["Sanctions screening", compliance.hits === 0, compliance.screened ? (compliance.hits === 0 ? "No matches · demo list" : `${compliance.hits} match${compliance.hits === 1 ? "" : "es"} blocked`) : "Runs at verification"],
    ["Approved lenders", compliance.blockedLenders.length === 0, `${compliance.approvedLenders} of 3 KYC-approved${compliance.blockedLenders.length ? ` · ${compliance.blockedLenders.join(", ")} blocked` : ""}`],
    ["Compliance pause", !compliance.paused, compliance.paused ? "Paused · repayments still work" : "Off"],
  ];
  return <section className="panel credit-panel" aria-label="Credit score and compliance">
    <div className="panel-heading compact-heading"><div><div className="panel-title-row"><span className="section-icon credit-icon"><Icon name="shield" size={15} /></span><h2>Credit and compliance</h2></div><p className="panel-subtitle">{credit.assessed ? "Score at verification sets the limit and advance rate." : "Preview from repayment and agent history."}</p></div></div>
    <div className="score-row"><div className="score-value"><strong>{after ? after.score : credit.score}</strong><span>of 900</span></div><div className="score-meta"><span className={`tag tier-tag tier-${(after?.tier ?? credit.tier).toLowerCase()}`}>{TIER_LABEL[after?.tier ?? credit.tier] ?? credit.tier}</span>{after && <span className={`tag ${after.score >= after.before ? "verified" : "pending"}`}>{after.before} → {after.score} · {after.outcome.replaceAll("_", " ").toLowerCase()}</span>}<small>Limit <Amount>{fmt(after ? after.limit : credit.limit)}</Amount> · advance up to {(credit.advanceRateBps / 100).toFixed(0)}%</small></div></div>
    <div className="score-track" aria-hidden="true"><span style={{ left: `${position}%` }} /></div>
    <div className="score-scale" aria-hidden="true"><span>300</span><span>Starter 450</span><span>Building 600</span><span>Established 700</span><span>Trusted 800</span></div>
    <p className="score-next"><Icon name="arrow" size={12} /> {credit.nextStep}</p>
    <div className="compliance-list">{rows.map(([label, ok, detail]) => <div className="compliance-row" key={label}><span className={`check-mark ${ok ? "ok" : "bad"}`}><Icon name={ok ? "check" : "warning"} size={11} /></span><span><b>{label}</b><small>{detail}</small></span></div>)}</div>
  </section>;
}

export default function Dashboard() {
  const [demo, setDemo] = useState<DemoState | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<EventItem | null>(null);
  const [apiError, setApiError] = useState("");
  const [integrations, setIntegrations] = useState<IntegrationStatus | null>(null);

  const load = useCallback(async () => {
    try {
      const [demoResponse, integrationResponse] = await Promise.all([
        fetch(`${API}/api/demo/retailco-net30`, { cache: "no-store" }),
        fetch(`${API}/api/integrations`, { cache: "no-store" }),
      ]);
      if (!demoResponse.ok) throw new Error(`API responded ${demoResponse.status}`);
      setDemo(await demoResponse.json());
      if (integrationResponse.ok) setIntegrations(await integrationResponse.json());
      setApiError("");
    } catch {
      setApiError("The demo API is unavailable. Start the orchestrator on port 8000, then retry.");
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  // Live activity: the orchestrator streams domain activity over server-sent events.
  useEffect(() => {
    if (typeof EventSource === "undefined") return;
    const source = new EventSource(`${API}/api/events/stream`);
    let timer: ReturnType<typeof setTimeout> | undefined;
    source.addEventListener("activity", () => {
      clearTimeout(timer);
      timer = setTimeout(() => void load(), 250);
    });
    return () => { clearTimeout(timer); source.close(); };
  }, [load]);

  const run = async (action: string, label: string) => {
    setBusy(action);
    setMessage(null);
    try {
      const response = await fetch(`${API}/api/demo/retailco-net30/actions`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() },
        body: JSON.stringify({ action }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail ?? `Unable to ${label.toLowerCase()}.`);
      setDemo(result.state);
      setMessage(result.event);
    } catch (error) {
      setMessage({ id: crypto.randomUUID(), title: `${label} failed`, detail: error instanceof Error ? error.message : "Please retry.", time: "Just now", status: "danger", chain: "Local demo" });
    } finally {
      setBusy(null);
    }
  };

  const reset = async () => {
    setBusy("reset");
    try {
      const response = await fetch(`${API}/api/demo/retailco-net30/reset`, { method: "POST" });
      if (!response.ok) throw new Error("Unable to reset the demo.");
      setDemo(await response.json());
      setMessage(null);
    } catch (error) {
      setApiError(error instanceof Error ? error.message : "Unable to reset the demo.");
    } finally { setBusy(null); }
  };

  const maxBudget = useMemo(() => demo?.agents.reduce((sum, agent) => sum + Number(agent.allocation), 0) ?? 0, [demo]);
  if (!demo) return <main className="loading-screen"><div className="loading-mark">WC</div><p>{apiError || "Connecting to working capital desk…"}</p>{apiError && <button className="button secondary" onClick={() => void load()}>Retry connection</button>}</main>;

  const currentIndex = { verify: 0, fund: 1, spend: 2, repay: 3 }[demo.stage] ?? 0;
  const primary = currentIndex === 0 ? ["verify_invoice", demo.modes.hedera === "live" ? "Verify GST invoice" : "Verify GST invoice · ATS simulated"] : currentIndex === 1 ? ["match_lenders", "Match lenders"] : currentIndex === 2 ? ["research_payment", "Run research agent · $0.25"] : ["verify_invoice", "Facility complete"];
  const primaryDisabled = currentIndex > 2;
  const settledDebt = demo.lifecycle === "REPAID";
  const lenderDue = Number(demo.facility.lenderDue);
  const operatorShare = Number(demo.facility.operatorProjected);
  const debtDue = Number(demo.facility.outstanding);
  const readyIntegrations = integrations ? Object.values(integrations).filter((integration) => integration.ready).length : 0;
  const integrationCount = integrations ? Object.keys(integrations).length : 0;
  const paymentIsLive = demo.modes.hedera === "live";
  const arcIsChain = demo.modes.arc === "fork" || demo.modes.arc === "testnet";
  const latestSpend = demo.spends.find((spend) => spend.status === "SETTLED");
  const partiallyRepaid = Number(demo.facility.customerPaid) > 0 && !settledDebt;
  const exception = EXCEPTION_COPY[demo.lifecycle];
  const verified = demo.lifecycle !== "DRAFT";
  const today = new Date().toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric", year: "numeric" }).toUpperCase();

  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand" href="#overview" aria-label="Working Capital Desk home"><span className="brand-mark">w</span><span className="brand-copy">working capital<span>DESK</span></span></a>
      <div className="workspace-label">WORKSPACE</div>
      <nav className="side-nav" aria-label="Main navigation">
        <a className="nav-item active" href="#overview"><Icon name="grid" /><span>Overview</span></a>
        <a className="nav-item" href="#receivable"><Icon name="file" /><span>Receivables</span><span className="nav-count">1</span></a>
        <a className="nav-item" href="#funding"><Icon name="layers" /><span>Credit facilities</span></a>
        <a className="nav-item" href="#activity"><Icon name="pulse" /><span>Activity</span></a>
      </nav>
      <div className="side-bottom">
        <div className="security-note"><span className="security-icon"><Icon name="shield" size={16} /></span><span><b>Policy engine active</b><small>Credit rules enforced</small></span><i className="green-dot" /></div>
        <div className="nav-item settings-button"><Icon name="settings" /><span>Settings</span></div>
        <div className="profile"><span className="avatar">NS</span><span className="profile-name"><b>Northstar Agents</b><small>Operator account</small></span><span className="profile-menu">···</span></div>
      </div>
    </aside>

    <main className="main-content" id="overview">
      <header className="topbar">
        <div className="breadcrumbs"><span>Workspace</span><b>/</b><strong>Overview</strong></div>
        <div className="top-actions"><span className={`network-pill ${paymentIsLive || arcIsChain ? "live" : ""}`}><i /> {paymentIsLive ? "Hedera testnet live" : "Hedera simulated"} · {arcIsChain ? (demo.modes.arc === "fork" ? "Arc local fork" : "Arc testnet") : "Arc simulated"}</span><button className="icon-button" aria-label="Refresh demo state" onClick={() => void load()}><Icon name="refresh" size={17} /></button><span className="top-avatar">NS</span></div>
      </header>
      <div className="page-wrap">
        <section className="page-heading">
          <div><p className="eyebrow">{today} <span className="eyebrow-divider" /> {demo.invoice.buyer.toUpperCase()} · {demo.invoice.terms.toUpperCase()}</p><h1>Working capital desk</h1><p className="subheading">{demo.lifecycle === "DRAFT" ? "Verify the GST invoice to open a shared credit line. No action needed from the buyer." : settledDebt ? "Debt cleared. The facility is ready for the next receivable." : "Verified future revenue, put to work by your agents."}</p></div>
          <button className="button quiet reset-button" onClick={reset} disabled={busy !== null}><Icon name="refresh" size={16} /> Reset demo</button>
        </section>

        <StageRail stage={demo.stage} />

        <section className="integration-board" aria-label="Integration readiness">
          <div className="integration-heading"><div><h2>Execution readiness</h2><p>Every chain action stays blocked until its own proof path is configured.</p></div><span className="readiness-count">{readyIntegrations} of {integrationCount} configured</span></div>
          <div className="integration-list">{integrations ? Object.entries(integrations).map(([key, integration]) => <article className={`integration-item ${integration.ready ? "ready" : "waiting"}`} key={key}><span className="integration-glyph">{key === "x402" ? "$" : key === "ats" ? "H" : key === "aqua" ? "A" : key === "arc" ? "C" : "G"}</span><div><div className="integration-title"><strong>{integration.label}</strong><span>{integration.ready ? "Ready" : integration.mode.replaceAll("-", " ")}</span></div><p>{integration.detail}</p></div><i className={integration.ready ? "green-dot" : "amber-dot"} /></article>) : <p className="integration-loading">Checking configured integrations…</p>}</div>
        </section>

        {apiError && <div className="inline-alert danger-alert" role="alert"><Icon name="warning" /><span>{apiError}</span><button onClick={() => void load()}>Retry</button></div>}
        {exception && <div className="inline-alert danger-alert" role="alert"><Icon name="warning" /><span><b>{demo.lifecycle.toLowerCase()}</b> · {exception}</span></div>}
        {message && <div className={`inline-alert ${message.status === "danger" ? "danger-alert" : "success-alert"}`} role="status"><span className="alert-icon"><Icon name={message.status === "danger" ? "warning" : "check"} size={17} /></span><div><strong>{message.title}</strong><p>{message.detail}</p></div><button className="alert-close" aria-label="Dismiss message" onClick={() => setMessage(null)}>×</button></div>}

        <section className="metric-grid" aria-label="Facility summary">
          <article className="metric-card featured-metric"><div className="metric-top"><span>Available credit</span><span className="metric-icon blue-icon"><Icon name="layers" size={17} /></span></div><div className="metric-value"><Amount large>{fmt(demo.facility.remaining)}</Amount><span className="metric-unit">USDC</span></div><div className="metric-foot"><span className="metric-status"><i className={demo.stage === "spend" ? "green-dot" : "blue-dot"} />{demo.facility.status.replaceAll("_", " ").toLowerCase()}</span><span className="metric-caption">of <Amount>{fmt(demo.facility.borrowingBase)}</Amount> borrowing base</span></div><div className="metric-meter"><span style={{ width: `${demo.facility.funded === "0.00" ? 0 : (Number(demo.facility.remaining) / Number(demo.facility.funded)) * 100}%` }} /></div></article>
          <article className="metric-card"><div className="metric-top"><span>Invoice value</span><span className="metric-icon"><Icon name="file" size={17} /></span></div><div className="metric-value"><Amount large>{fmt(demo.invoice.faceValue)}</Amount><span className="metric-unit">USDC</span></div><div className="metric-foot">{!verified ? <span className="tag pending">Awaiting GST check</span> : <span className="tag verified"><Icon name="check" size={12} /> GST verified</span>}<span className="metric-caption">Due {new Date(`${demo.invoice.dueDate}T00:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric" })}</span></div></article>
          <article className="metric-card"><div className="metric-top"><span>Outstanding debt</span><span className="metric-icon"><Icon name="pulse" size={17} /></span></div><div className="metric-value"><Amount large>{fmt(debtDue)}</Amount><span className="metric-unit">USDC</span></div><div className="metric-foot"><span className={`metric-status ${settledDebt ? "positive" : ""}`}><i className={settledDebt ? "green-dot" : "amber-dot"} />{settledDebt ? "Repaid in full" : partiallyRepaid ? "Partially repaid" : "Principal + accrued fee"}</span><span className="metric-caption">{demo.allocations.length || 0} lenders · fee ${fmt(demo.facility.feeTotal)}</span></div></article>
          <article className="metric-card"><div className="metric-top"><span>Agent spend</span><span className="metric-icon"><Icon name="pulse" size={17} /></span></div><div className="metric-value"><Amount large>{fmt(demo.facility.spent)}</Amount><span className="metric-unit">USDC</span></div><div className="metric-foot"><span className="metric-status"><i className="blue-dot" />Across 3 agents</span><span className="metric-caption">of <Amount>{fmt(maxBudget)}</Amount> allocated</span></div></article>
        </section>

        <div className="content-grid">
          <div className="primary-column">
            <section className="panel receivable-panel" id="receivable">
              <div className="panel-heading"><div><div className="panel-title-row"><span className="section-icon invoice-icon"><Icon name="file" size={16} /></span><h2>Verified receivable</h2>{!verified ? <span className="tag pending">Awaiting GST check</span> : <span className="tag verified"><Icon name="check" size={12} /> {demo.invoice.verification}</span>}{demo.invoice.irnSimulated && <span className="tag pending">IRN simulated</span>}{demo.invoice.tokenId.includes("Pending") && <span className="tag pending">ATS pending</span>}{demo.invoice.tokenSimulated && <span className="tag pending">ATS simulated</span>}<span className="tag not-needed">{demo.lifecycle.replaceAll("_", " ")}</span></div><p className="panel-subtitle">One GST-verified invoice backs this shared credit line. The buyer does not need to do anything.</p></div><span className="more-button" aria-hidden="true">···</span></div>
              <div className="invoice-summary"><div className="buyer-mark">R</div><div className="buyer-details"><strong>{demo.invoice.buyer}</strong><span>Invoice {demo.invoice.invoiceNumber} · {demo.invoice.terms} · buyer score {demo.invoice.buyerScore}</span></div><div className="invoice-total"><Amount>{fmt(demo.invoice.faceValue)}</Amount><span>Face value</span></div></div>
              <div className="invoice-facts"><div><span>Seller GSTIN</span><strong title="Masked">{demo.invoice.sellerTaxId}</strong></div><div><span>Buyer GSTIN</span><strong title="Masked">{demo.invoice.buyerTaxId}</strong></div><div><span>E-invoice IRN</span><strong>{demo.invoice.irn ?? "Added at verification"}</strong></div><div><span>Evidence cap</span><strong>{demo.invoice.advanceCap ?? "Up to 80%"}</strong></div><div><span>Operator</span><strong>{demo.invoice.operator}</strong></div><div><span>Advance rate</span><strong>{(demo.invoice.advanceRateBps / 100).toFixed(0)}%</strong></div><div><span>Borrowing base</span><strong><Amount>{fmt(demo.facility.borrowingBase)}</Amount></strong></div><div><span>Due date</span><strong>{new Date(`${demo.invoice.dueDate}T00:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}</strong></div></div>
              {demo.invoice.checks.length > 0 && <div className="check-list" aria-label="Verification checks">{demo.invoice.checks.map((check) => <div className="check-item" key={check.id} title={check.detail}><span className={`check-mark ${check.ok ? "ok" : "warn"}`}><Icon name={check.ok ? "check" : "warning"} size={11} /></span><span><b>{check.label}</b><small>{check.detail}</small></span></div>)}</div>}
              <div className="receivable-foot"><span className="chain-label"><span className="hedera-symbol">H</span> Hedera <span className="bullet">·</span> Asset Tokenization Studio</span><span className="id-label">{!demo.invoice.tokenSimulated && !demo.invoice.tokenId.includes("Pending") && <TxRef id={demo.invoice.tokenId} url={demo.invoice.tokenExplorerUrl} />} ID <code title={demo.invoice.canonicalId}>{demo.invoice.receivableId}</code><button className="copy-button" aria-label="Copy canonical receivable ID" onClick={() => void navigator.clipboard?.writeText(demo.invoice.canonicalId)}>Copy</button></span></div>
            </section>

            <section className="panel funding-panel" id="funding">
              <div className="panel-heading"><div><div className="panel-title-row"><span className="section-icon funding-icon"><Icon name="layers" size={16} /></span><h2>Lender matching</h2><span className="small-note">{demo.allocations.length ? `${demo.allocations.length} matched` : "3 offers"}</span></div><p className="panel-subtitle">Cheapest eligible capital funds first.</p></div><span className="aqua-mark">1inch <b>Aqua</b></span></div>
              <div className="table-wrap"><table><thead><tr><th>Lender</th><th>Risk tier</th><th>Available</th><th>APR</th><th>Decision</th></tr></thead><tbody>{demo.offers.map(offer => <tr key={offer.lender} className={offer.selected ? "selected-row" : ""}><td><span className={`lender-avatar ${offer.lender.toLowerCase()}`}>{offer.lender[0]}</span><strong>{offer.lender}</strong></td><td><span className={`risk-tier ${offer.score === "B" ? "tier-b" : ""}`}>{offer.score}</span></td><td><Amount>{fmt(offer.available)}</Amount></td><td className="apr-cell">{offer.apr}</td><td>{offer.fill ? <span className="fill-cell"><span className="tag selected"><Icon name="check" size={12} /> {offer.fill.viaAqua ? "Aqua fill" : arcIsChain ? "Funded" : "Matched"}</span><TxRef id={offer.fill.txHash} url={offer.fill.explorerUrl} /></span> : offer.selected ? <span className="tag pending">Filling…</span> : offer.rejection ? <span className="tag not-needed" title={offer.rejection}>{offer.rejection.replaceAll("_", " ").toLowerCase()}</span> : demo.allocations.length ? <span className="tag not-needed">Not needed</span> : <span className="offer-ready">Eligible</span>}</td></tr>)}</tbody></table></div>
              <div className="funding-foot"><span><Icon name="shield" size={14} /> KYC-approved lenders only, filtered by buyer score, advance rate, tenor, expiry and liquidity</span><span>Strategy <code>RECEIVABLE_CREDIT_V1</code> · {demo.modes.arc === "fork" ? "official Aqua router · local Arc fork · TestUSDC" : demo.modes.arc === "testnet" ? "direct funding on Arc testnet" : "simulated"}</span></div>
            </section>

            <section className="panel agents-panel">
              <div className="panel-heading"><div><div className="panel-title-row"><span className="section-icon agents-icon"><Icon name="grid" size={16} /></span><h2>Shared agent budgets</h2></div><p className="panel-subtitle">One facility ceiling, with spend reserved by agent.</p></div><span className="agent-count">{demo.agents.length - 1} agents <span className="green-dot" /></span></div>
              <div className="agent-list">{demo.agents.filter(agent => agent.name !== "Reserve").map(agent => <article className="agent-row" key={agent.name}><span className={`agent-avatar ${agent.color}`}>{agent.name === "Research" ? "R" : agent.name === "Compute" ? "C" : "D"}</span><div className="agent-ident"><strong>{agent.name}</strong><span title={agent.uaid ?? agent.identity}>{agent.role}</span></div><div className="agent-budget"><span><Amount>{fmt(agent.spent)}</Amount> <i>/</i> <Amount>{fmt(agent.allocation)}</Amount></span><div className="budget-track"><span className={agent.color} style={{ width: `${Math.min(100, Number(agent.spent) / Number(agent.allocation) * 100)}%` }} /></div></div><div className="agent-limit"><span>Per transaction</span><strong>{agent.limit === "-" ? "-" : <Amount>{fmt(agent.limit)}</Amount>}</strong></div><span className="agent-state"><i className={agent.assigned ? "green-dot" : "amber-dot"} /> {agent.assigned ? "Active" : "Planned"}</span></article>)}</div>
              {latestSpend && <div className="receipt-row" aria-label="Latest x402 receipt"><span className="tag verified"><Icon name="check" size={12} /> x402 settled</span><span>{latestSpend.agent} paid <b>${fmt(latestSpend.amount)}</b> to {latestSpend.merchant}</span><TxRef id={latestSpend.txId} url={latestSpend.explorerUrl} />{latestSpend.network.includes("simulated") && <span className="tag pending">Simulated</span>}</div>}
              <div className="agent-foot"><span><Icon name="shield" size={14} /> Per-transaction, daily and merchant limits enforced</span><span title={demo.agents[0]?.uaid ?? ""}>HCS-14 UAIDs · {shortId(demo.agents[0]?.uaid, 14, 4)}</span></div>
            </section>
          </div>

          <aside className="secondary-column">
            <section className="panel action-panel">
              <div className="action-overline"><span className="live-indicator"><i /> Workflow controls</span><span className={`simulated-label ${paymentIsLive || arcIsChain ? "live-label" : ""}`}>{paymentIsLive && arcIsChain ? "LIVE" : paymentIsLive || arcIsChain ? "PARTLY LIVE" : "SIMULATED"}</span></div>
              <h2>Move the workflow</h2><p className="action-description">Run each step to see how verified revenue becomes controlled agent spend.</p>
              <button className="button primary action-primary" disabled={primaryDisabled || busy !== null} onClick={() => void run(primary[0], primary[1])}>{busy === primary[0] ? <span className="spinner" /> : <Icon name={currentIndex === 2 ? "pulse" : "arrow"} size={16} />}{primary[1]}</button>
              <div className="action-divider"><span>POLICY TESTS</span></div>
              <button className="button policy-button" disabled={currentIndex !== 2 || busy !== null} onClick={() => void run("compute_overspend", "Compute overspend") }><span className="policy-icon"><Icon name="warning" size={16} /></span><span><b>Attempt overspend</b><small>Compute requests $5,500</small></span><Icon name="arrow" size={15} /></button>
              <button className="button policy-button" disabled={currentIndex !== 2 || busy !== null} onClick={() => void run("global_overspend", "Global credit limit") }><span className="policy-icon"><Icon name="shield" size={16} /></span><span><b>Test facility ceiling</b><small>Separate global limit check</small></span><Icon name="arrow" size={15} /></button>
              <button className="button policy-button" disabled={currentIndex !== 2 || partiallyRepaid || busy !== null} onClick={() => void run("partial_repay", "Partial repayment")}><span className="policy-icon"><Icon name="clock" size={16} /></span><span><b>Partial repayment</b><small>Customer pays $3,000 first</small></span><Icon name="arrow" size={15} /></button>
              <button className="button repay-button" disabled={currentIndex !== 2 || busy !== null} onClick={() => void run("repay", "Repay facility")}>{busy === "repay" ? <span className="spinner" /> : <Icon name="check" size={16} />}{partiallyRepaid ? "Customer pays the rest" : "Customer paid · repay lenders"}</button>
              <div className="action-trust"><Icon name="shield" size={15} /><span>Eligibility, the credit score and every limit are deterministic and enforced again by the Arc contract. 7 days late freezes spending, 30 days late is a default. Hedera facts reach Arc through a coordinator attestation, a prototype trust assumption; the chains are not atomic.</span></div>
            </section>

            <CreditPanel credit={demo.credit} compliance={demo.compliance} borrower={demo.borrower} />

            <section className="panel waterfall-panel">
              <div className="panel-heading compact-heading"><div><div className="panel-title-row"><span className="section-icon repay-icon"><Icon name="arrow" size={15} /></span><h2>Repayment waterfall</h2></div><p className="panel-subtitle">Lenders are paid before the operator.</p></div></div>
              <div className="waterfall-source"><span className="customer-mark">R</span><span><b>{demo.invoice.buyer}</b><small>{Number(demo.facility.customerPaid) > 0 ? "Customer paid" : "Customer payment due"}</small></span><Amount>{fmt(demo.facility.repaymentAmount)}</Amount></div>
              <div className="waterfall-connector"><i /></div>
              <div className="waterfall-destinations"><div className="waterfall-row"><span className="waterfall-dot lender-dot" /><span><b>Lenders</b><small>{Number(demo.facility.lenderPaid) > 0 ? `Paid ${fmt(demo.facility.lenderPaid)} of principal + fee` : "Principal + fee, paid first"}</small></span><Amount>{fmt(lenderDue)}</Amount></div><div className="waterfall-row"><span className="waterfall-dot operator-dot" /><span><b>Operator</b><small>{settledDebt ? `Remainder · plus $${fmt(demo.facility.undrawnReturned)} undrawn credit returned` : "Only after lenders are whole"}</small></span><Amount>{fmt(Number(demo.facility.customerPaid) > 0 ? demo.facility.operatorRemainder : operatorShare)}</Amount></div></div>
              <div className={`waterfall-status ${settledDebt ? "paid" : ""}`}><Icon name={settledDebt ? "check" : "clock"} size={14} />{settledDebt ? "All obligations settled · debt $0.00" : partiallyRepaid ? `Partially repaid · $${fmt(debtDue)} still owed to lenders` : "Awaiting customer payment"}</div>
            </section>

            <section className="panel activity-panel" id="activity">
              <div className="panel-heading compact-heading"><div><div className="panel-title-row"><span className="section-icon activity-icon"><Icon name="pulse" size={16} /></span><h2>Recent activity</h2></div></div><span className="small-note">Live</span></div>
              <div className="activity-list">{demo.events.slice(0, 6).map((event) => <article className="activity-row" key={event.id}><span className={`activity-pin ${event.status}`}><Icon name={event.status === "danger" ? "warning" : event.status === "success" ? "check" : "clock"} size={12} /></span><div className="activity-copy"><strong>{event.title}</strong><p>{event.detail}</p><span className="activity-meta">{event.chain} <i>·</i> {new Date(event.time).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", timeZoneName: "short" })}{event.txId && <> <i>·</i> <TxRef id={event.txId} url={event.explorerUrl} /></>}</span></div></article>)}</div>
            </section>
          </aside>
        </div>
        <footer className="page-footer"><span>Working Capital Desk <i>·</i> Prototype v0.2</span><span>{demo.modes.arcLabel} <i>·</i> {paymentIsLive ? "spends commit only after a finalized Hedera receipt" : "Hedera payments simulated until x402 credentials are configured"}</span></footer>
      </div>
    </main>
  </div>;
}
