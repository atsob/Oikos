import { useState } from 'react'
import { PageHeader } from '@/components/ui'

// ── Shared prose primitives (no typography plugin — keep it minimal and consistent
// with the rest of the app's Tailwind styling) ────────────────────────────────
function H2({ children }: { children: React.ReactNode }) {
  return <h2 className="text-lg font-semibold text-slate-800 mt-8 mb-2 first:mt-0">{children}</h2>
}
function H3({ children }: { children: React.ReactNode }) {
  return <h3 className="text-sm font-semibold text-slate-700 mt-5 mb-1.5">{children}</h3>
}
function P({ children }: { children: React.ReactNode }) {
  return <p className="text-sm text-slate-600 leading-relaxed mb-2">{children}</p>
}
function Ul({ children }: { children: React.ReactNode }) {
  return <ul className="list-disc pl-5 text-sm text-slate-600 leading-relaxed space-y-1 mb-3">{children}</ul>
}
function Note({ children }: { children: React.ReactNode }) {
  return <div className="bg-blue-50 border border-blue-200 text-blue-800 text-sm rounded-lg px-3 py-2 mb-3">💡 {children}</div>
}

// ── Section content ─────────────────────────────────────────────────────────────
const SECTIONS: { id: string; label: string; body: React.ReactNode }[] = [
  {
    id: 'overview',
    label: 'Overview',
    body: (
      <>
        <H2>What is Oikos?</H2>
        <P>
          Oikos is a personal finance manager that tracks cash accounts, credit cards, loans, real assets
          (property, vehicles), investments (stocks, ETFs, bonds, crypto, options), and pensions in one place,
          converts everything to a common reporting currency, and gives you reports on net worth, income and
          expenses, investment performance, and tax.
        </P>
        <P>It's built as a React frontend talking to a FastAPI backend backed by PostgreSQL.</P>

        <H2>Core concepts</H2>
        <H3>Accounts</H3>
        <P>
          Every balance you track lives in an <b>Account</b> — a checking account, a credit card, a brokerage,
          a pension plan, a property, even a "Cash" wallet per currency. Each account has a type (Checking,
          Savings, Credit Card, Brokerage, Pension, Real Estate, …), a currency, and can be marked active or
          inactive (closed) without deleting its history.
        </P>
        <H3>Transactions &amp; Splits</H3>
        <P>
          A <b>Transaction</b> is a dated entry against an account (e.g. a grocery purchase, a salary deposit,
          a transfer between accounts). Each transaction has one or more <b>Splits</b> — the category/amount
          breakdown — so a single supermarket receipt can be split across "Groceries" and "Household" in one
          transaction.
        </P>
        <H3>Categories</H3>
        <P>
          Categories classify splits as Income, Expense, Transfer, Trading, Investment, Dividend, Interest, Tax,
          or Fee. This drives the Income &amp; Expense report and the AI monthly/weekly summaries.
        </P>
        <H3>Securities &amp; Investments</H3>
        <P>
          A <b>Security</b> is a tradable instrument (stock, ETF, bond, crypto, …) with its own price history.
          An <b>Investment</b> row records an action against a security in a brokerage-type account — Buy,
          Sell, Dividend, Reinvest, Split, ShrIn/ShrOut (transfers), and more. Holdings, cost basis, and P&amp;L
          are derived from this history, not entered directly.
        </P>
      </>
    ),
  },
  {
    id: 'dashboard',
    label: 'Dashboard',
    body: (
      <>
        <H2>Dashboard</H2>
        <P>The landing page — a snapshot of where things stand right now.</P>
        <H3>KPI cards</H3>
        <P>
          Net Worth, Cash &amp; Savings, Investments, Pension, and Assets, each with a "vs prev month" and
          "YTD" (or "daily" for Investments) change. Net Worth is the sum of the other four.
        </P>
        <Note>
          The <b>Options &amp; Account Selection</b> panel lets you include/exclude specific accounts and
          toggle "Show Disabled" for closed accounts — both the current totals and the historical baseline used
          for the deltas respect this same selection, so what you see stays internally consistent.
        </Note>
        <H3>Insights &amp; Alerts</H3>
        <P>
          Auto-generated observations (unusual spending, upcoming bills, low balances, etc.) and any alert
          rules you've triggered, both collapsible.
        </P>
        <H3>Pending Drafts</H3>
        <P>
          Transactions imported or projected but not yet confirmed — confirm or discard them individually or
          all at once.
        </P>
        <H3>Net Worth Breakdown &amp; Trend</H3>
        <P>A donut chart of current composition, and a historical line/area chart over 1/3/5 years or all time.</P>
        <H3>Sync Balances</H3>
        <P>
          The button in the top-right refreshes account balances from their linked data sources — run a full
          sync or just one category (Bank &amp; Cash, Investments, Pension, Holdings). The same button also
          appears, scoped to just the relevant categories, on the Cash Register and Investments pages.
        </P>
      </>
    ),
  },
  {
    id: 'register',
    label: 'Cash Register',
    body: (
      <>
        <H2>Cash Register</H2>
        <P>
          The transaction ledger. Filter by account, date range, and search text; add, edit, split, clear, and
          reconcile transactions here. Supports transfers between two accounts (including cross-currency),
          scheduled/future-dated entries, and draft transactions awaiting confirmation.
        </P>
        <Ul>
          <li><b>Cleared</b> marks a transaction as bank-confirmed — future/pending entries stay unmarked.</li>
          <li><b>Reconciled</b> is set when a transaction is matched during bank-statement import reconciliation.</li>
          <li>Splitting a transaction lets one payment cover several categories.</li>
        </Ul>
        <P>
          <b>Sync Balances</b> (top-right) refreshes Bank &amp; Cash balances only — the account types this
          page manages.
        </P>
      </>
    ),
  },
  {
    id: 'investments',
    label: 'Investments',
    body: (
      <>
        <H2>Investments</H2>
        <P>Manage brokerage/investment-account activity directly, in three views:</P>
        <Ul>
          <li><b>Holdings</b> — current positions, quantity, cost basis, market value, unrealized P&amp;L.</li>
          <li><b>Transactions</b> — the full Buy/Sell/Dividend/Reinvest/etc. history, editable.</li>
          <li><b>Cash</b> — the cash-side movements linked to investment accounts (deposits, withdrawals, fees).</li>
        </Ul>
        <P>
          <b>Sync Balances</b> (top-right) refreshes Investments, Pension, and Holdings — the account types this
          page manages (not Bank &amp; Cash).
        </P>
        <P>
          For deeper analysis — performance, risk, tax, dividend income, benchmarking — see the <b>Reports</b>{' '}
          section, which has several dedicated investment reports.
        </P>
      </>
    ),
  },
  {
    id: 'recurring',
    label: 'Recurring',
    body: (
      <>
        <H2>Recurring Transactions</H2>
        <P>
          Templates for entries that repeat on a schedule — rent, subscriptions, salary, loan installments.
          Each template defines the account, amount/splits, and frequency; running a template creates the
          actual transaction (as a draft or confirmed, depending on setup). The Dashboard's "Upcoming Bills"
          list and the Cash Flow Forecast report both project forward from these templates.
        </P>
      </>
    ),
  },
  {
    id: 'reports',
    label: 'Reports',
    body: (
      <>
        <H2>Reports</H2>
        <P>The Reports section has its own left-hand sub-navigation with ten report types:</P>

        <H3>📊 Net Worth</H3>
        <P>
          Historical net worth by Year/Quarter/Month grouping, or YTD mode. Four tabs: <b>Overview</b> (KPI
          cards + trend chart), <b>Account Balances</b> (per-account table with a Total row), <b>Summary per
          Type</b>, and <b>Detail Analysis</b> (breakdown for one period with a donut chart).
        </P>
        <Note>
          <b>Account Selection</b> lets you include/exclude specific accounts. <b>Show inactive accounts</b>{' '}
          (on by default here, since this is a historical report) controls whether closed accounts' past
          balances count — turning it off will understate historical change if a closed account held real
          money at some point in the range. <b>Show zero-balance accounts</b> just controls whether
          currently-zero accounts are hidden from the table, not the totals.
        </Note>

        <H3>💰 Income &amp; Expense</H3>
        <P>
          Income vs. expense by category and period, with drill-down to the underlying transactions. Filter by
          date range and cash/investment category types.
        </P>

        <H3>🔄 Cash Flow Forecast</H3>
        <P>Projects future cash flow from recurring templates and scheduled transactions.</P>

        <H3>🎯 Budget &amp; Spending</H3>
        <P>Three tabs: Budget vs. Actual, Spending Trends, and Savings Rate.</P>

        <H3>📈 Inv. Positions</H3>
        <P>Point-in-time holdings snapshot and historical positions detail, with allocation charts.</P>

        <H3>💹 Inv. Performance</H3>
        <P>
          Ten sub-tabs: <b>P&amp;L</b> (DTD/W/M/Q/YTD/All, per account with drill-down to security level),{' '}
          <b>Performance</b>, <b>Savings</b> (interest/APY on savings-type accounts), <b>Dividend Tracker</b>,{' '}
          <b>Bond Schedule</b>, <b>Benchmark</b> (compare against an index), <b>Risk Metrics</b>,{' '}
          <b>Correlation</b>, <b>Monte Carlo</b> projections, and <b>TWR/MWR</b> (time- and money-weighted
          return).
        </P>

        <H3>🧾 Investment Tax</H3>
        <P>
          Capital gains (by method), dividend income tax, and tax-loss harvesting candidates — based on the tax
          category rules configured in Static Data.
        </P>

        <H3>🔍 Securities Analysis</H3>
        <P>Four sub-tabs: Price Changes, Volatility, Investment Signals (risk/reward, Sharpe ratio), and Portfolio Action Signals.</P>

        <H3>🏖️ Financial Planning</H3>
        <P>Goals tracking, a FIRE (Financial Independence) calculator, and loan amortization schedules.</P>

        <H3>📋 Custom Reports</H3>
        <P>Build and save your own filtered report views.</P>
      </>
    ),
  },
  {
    id: 'static-data',
    label: 'Static Data',
    body: (
      <>
        <H2>Static Data</H2>
        <P>Master reference data, in six tabs:</P>
        <Ul>
          <li><b>Payees</b> — who you pay/receive from; supports merging duplicates.</li>
          <li><b>Categories</b> — the Income/Expense/Transfer/etc. taxonomy; also mergeable.</li>
          <li><b>Institutions</b> — banks, brokers, pension funds.</li>
          <li><b>Accounts</b> — the full account list with type, currency, institution, active status.</li>
          <li><b>Tax Rules</b> — capital-gains/dividend/income tax treatment per tax category.</li>
          <li><b>Instrument Tax</b> — overrides mapping a security's instrument type to a tax category.</li>
        </Ul>
      </>
    ),
  },
  {
    id: 'market-data',
    label: 'Market Data',
    body: (
      <>
        <H2>Market Data</H2>
        <P>Reference and price data, in eight tabs: Currencies, Securities, FX Prices, Securities Prices, Downloads (refresh from external sources), Anomalies (price data quality checks), Watchlist, and Alerts.</P>
      </>
    ),
  },
  {
    id: 'importers',
    label: 'Importers',
    body: (
      <>
        <H2>Importers</H2>
        <P>Bring in transactions from banks and brokers instead of entering them by hand, grouped into three families:</P>
        <Ul>
          <li>
            <b>Bank</b> — Import &amp; Reconcile (generic file import with matching against existing
            transactions), Salt Edge, Revolut Personal, Revolut Savings, Import Profiles (define column
            mappings for a bank's export format), Payee Rules (auto-categorize by payee), Import History.
          </li>
          <li>
            <b>Brokerage</b> — Interactive Brokers (Flex), Revolut Trading, Saxo Bank, Coinbase, Crypto.com,
            Capital.com, FXPro.
          </li>
          <li><b>QIF</b> — generic QIF file import, plus a Transfer Issues tool for fixing unmatched transfer pairs.</li>
        </Ul>
        <P>
          Import Profiles and Payee Rules are reusable — set a bank's column mapping or a payee's default
          category once, then every future import uses it automatically.
        </P>
      </>
    ),
  },
  {
    id: 'tools',
    label: 'Tools',
    body: (
      <>
        <H2>Tools</H2>
        <P>Admin/maintenance utilities, organized into four categories:</P>
        <Ul>
          <li>
            <b>💾 Database</b> — Backup &amp; Restore, DB Maintenance (table health, recalculate balances,
            referential integrity), SQL Interface (raw queries), Data Export, and several targeted "Fix …"
            tools for transfer mirrors, sign mismatches, and investment cash links.
          </li>
          <li><b>⚙️ System</b> — App Settings and Scheduled Tasks (cron-style jobs: e.g. the monthly/weekly AI summaries).</li>
          <li><b>📊 Market Data &amp; Prices</b> — fill missing prices from transactions, price quality checks, normalize investment prices, investment data quality.</li>
          <li><b>📋 Logs</b> — application log viewer.</li>
        </Ul>
        <Note>
          <b>Backup &amp; Restore</b> is here — take a backup before running any of the bulk "Fix …" tools,
          since they write directly to the database.
        </Note>
      </>
    ),
  },
  {
    id: 'ai-assistant',
    label: 'AI Assistant',
    body: (
      <>
        <H2>AI Assistant</H2>
        <P>
          A chat interface for asking questions about your finances in plain language — e.g. "What was my
          total spending last month?" or "How has my net worth changed over the last 12 months?". It reasons
          over your data using tools (visible via the collapsible "reasoning steps" under each answer) rather
          than guessing.
        </P>
        <P>
          The same underlying engine also generates the scheduled <b>Monthly</b> and <b>Weekly AI Summary</b>{' '}
          jobs (configurable under Tools → Scheduled Tasks), which write a short plain-English recap of cash
          flow, top payees, investment P&amp;L, and net worth — shown on the Dashboard.
        </P>
      </>
    ),
  },
  {
    id: 'tips',
    label: 'Tips & Interface',
    body: (
      <>
        <H2>Tips &amp; Interface</H2>
        <H3>Sidebar</H3>
        <P>
          Click the panel icon at the top of the sidebar (next to "Oikos") to collapse it to icon-only and
          reclaim horizontal space — the collapsed state is remembered across visits.
        </P>
        <H3>Theme</H3>
        <P>Light / System / Dark, switchable at the bottom of the sidebar.</P>
        <H3>Currency</H3>
        <P>All figures are converted to a single reporting currency (EUR by default) using historical FX rates as of each date, so historical comparisons stay meaningful even if you hold multi-currency accounts.</P>
        <H3>Persisted filters</H3>
        <P>
          Most report filters (date range, grouping, account selection, tab choice) are remembered per-browser
          via local storage, so returning to a report picks up where you left off.
        </P>
        <H3>Version info</H3>
        <P>
          The sidebar footer shows the build version and, below it, the exact git commit hash and date it was
          built from — useful when reporting an issue, since it pins down precisely which code is running. The{' '}
          <b>Release Notes</b> page (below Help in the sidebar) lists what's actually changed release to
          release.
        </P>
        <H3>Reasonable defaults, always overridable</H3>
        <P>
          Where a report has to choose a default (e.g. whether closed accounts count), it's chosen to match
          what that specific report is for — a historical report defaults to including closed accounts; a
          current-snapshot view defaults to excluding them. A checkbox is always available to flip it.
        </P>
      </>
    ),
  },
]

export default function Help() {
  const [active, setActive] = useState(SECTIONS[0].id)
  const current = SECTIONS.find(s => s.id === active) ?? SECTIONS[0]

  return (
    <div className="flex h-full">
      <nav className="w-52 shrink-0 border-r border-slate-200 bg-slate-50 flex flex-col py-4 overflow-y-auto">
        <p className="px-4 text-xs font-semibold text-slate-400 uppercase tracking-wide mb-2">User Guide</p>
        {SECTIONS.map(s => (
          <button key={s.id} onClick={() => setActive(s.id)}
            className={`text-left px-4 py-2 text-sm transition-colors ${active === s.id ? 'bg-blue-50 text-blue-700 font-semibold border-r-2 border-blue-600' : 'text-slate-600 hover:bg-slate-100'}`}>
            {s.label}
          </button>
        ))}
      </nav>

      <div className="flex-1 min-w-0 overflow-auto">
        <PageHeader title="Help & User Guide" subtitle="How Oikos is organized and how to use it" />
        <div className="px-6 py-6 max-w-3xl">
          {current.body}
        </div>
      </div>
    </div>
  )
}
