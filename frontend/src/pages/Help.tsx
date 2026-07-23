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

        <H2>Why Oikos?</H2>
        <P>
          Oikos (Ancient Greek: οἶκος, plural: οἶκοι) is the ancient Greek word for three intertwined concepts: 
          a house, the family living inside it, and the family's property or estate. In ancient Greece, 
          the oikos was the fundamental, self-sufficient unit of society and the economy.
        </P>          

        <P>
          Most personal finance tools force a choice: a budgeting app (YNAB, Mint/Credit Karma, EveryDollar,
          Monarch) with little to no investment analytics, or an investment tracker (Personal Capital/Empower,
          Portfolio Performance, Kubera) with little to no cash-flow budgeting — and almost none handle
          multi-currency accounts, non-US tax rules, or manually-tracked assets (crypto wallets, private
          holdings, real estate) well. Oikos covers all of it in one self-hosted place:
        </P>
        <Ul>
          <li>
            <b>You own the data.</b> Everything lives in your own PostgreSQL database, not a vendor's servers —
            no subscription, no "we're shutting down" (Mint's fate in 2024), and no handing your actual bank
            login to a third-party aggregator the way Plaid-based apps require.
          </li>
          <li>
            <b>Multi-currency is a first-class citizen, not an afterthought.</b> Every account and security
            tracks its own native currency; reports convert through actual historical FX rates to whichever
            reporting currency you choose. Most US-built apps barely tolerate a second currency.
          </li>
          <li>
            <b>Investment depth most budgeting apps don't have</b>, in the same app as everyday expense
            tracking: FIFO/LIFO/WAC cost basis (correctly handling short positions and partial-sell episodes),
            realized/unrealized P&amp;L, dividend tracking, corporate actions, TWR/MWR, benchmarking, Monte
            Carlo projections, and risk metrics (Sharpe, VaR, correlation) — the kind of analysis usually
            reserved for a dedicated portfolio tool.
          </li>
          <li>
            <b>Tax rules you configure</b>, not a US-only black box — capital-gains categories, rates, and
            holding-period rules are defined per instrument type in Static Data, built around specifics
            mainstream commercial apps don't support, but general enough to adapt to other jurisdictions.
          </li>
          <li>
            <b>Tracks everything a bank-aggregator can't see</b> — real estate, vehicles, pensions, and
            manually-entered or exchange-imported crypto holdings sit alongside bank/brokerage accounts in the
            same net-worth model.
          </li>
          <li>
            An <b>AI Assistant</b> that answers questions about <i>your actual data</i>, not a generic chatbot
            bolted onto marketing copy — see the AI Assistant section.
          </li>
          <li>
            <b>No product roadmap to wait on.</b> It's your own FastAPI + React + PostgreSQL stack, so a bug or
            a missing report is something you can fix, not a feature request in a vendor's backlog.
          </li>
        </Ul>
        <Note>
          The trade-off is real: you're responsible for hosting, backups, and updates yourself, there's no
          official support line, and a bank-aggregator app will always have a slicker "connect your bank in 10
          seconds" onboarding than manual/CSV/API imports. Oikos is for people who want full control and depth
          and are willing to run their own server for it.
        </Note>

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

        <Note>
          This instance carries <b>AllAbout360C</b>'s branding — its logo in the sidebar and as the browser
          favicon/app icon, and its colors throughout. Questions or need support? Reach out at{' '}
          <a href="mailto:info@allabout360c.com" className="underline hover:no-underline">info@allabout360c.com</a>
          {' '}or <a href="https://allabout360c.com" target="_blank" rel="noopener noreferrer" className="underline hover:no-underline">allabout360c.com</a>.
        </Note>
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
          Each card is a shortcut to where that number comes from: <b>Net Worth</b> opens Reports → Net Worth,{' '}
          <b>Investments</b> opens Reports → Inv. Performance → P&amp;L, <b>Cash &amp; Savings</b> and{' '}
          <b>Assets</b> open Cash Register, and <b>Pension</b> opens Investments → Transactions, pre-filtered to
          the pension account.
        </Note>
        <Note>
          The <b>Options &amp; Account Selection</b> panel lets you include/exclude specific accounts and
          toggle "Show Disabled" for closed accounts — both the current totals and the historical baseline used
          for the deltas respect this same selection, so what you see stays internally consistent.
        </Note>
        <H3>Insights &amp; Alerts</H3>
        <P>
          Auto-generated observations (unusual spending, upcoming bills, low balances, etc.) and any alert
          rules you've triggered, both collapsible. <b>Bond maturity and coupon alerts appear automatically</b> for
          every bond you currently hold — no setup needed, unlike price/allocation alerts — once the event is
          within 7 days.
        </P>
        <Note>
          The <b>uncategorized transactions</b> panel lists non-transfer cash transactions with no category —
          click any row to open it directly in the transaction editor and fix it. New transactions can't be
          saved without a category anymore (see Cash Register), so this is mainly for cleaning up ones that
          predate that rule, or came in through an import.
        </Note>
        <H3>Pending Drafts</H3>
        <P>
          Transactions imported or projected but not yet confirmed — confirm or discard them individually or
          all at once.
        </P>
        <H3>Net Worth Breakdown &amp; Trend</H3>
        <P>A donut chart of current composition, and a historical line/area chart over 1/3/5 years or all time.</P>
        <Note>
          The <b>Kondratieff wave phases</b> checkbox on the Trend chart shades one commonly-cited framework for
          long secular market "seasons" (Spring/Summer/Autumn/Winter, ~40-60 years each) onto the chart, purely
          as historical/educational context. It's off by default and comes with an on-screen disclaimer — this
          is a contested, non-consensus theory with no rigorous way to predict phase changes, so treat it as a
          talking point, not a signal.
        </Note>
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
          <li>
            Splitting a transaction lets one payment cover several categories — the Category column shows
            "Split" for these (instead of just the first category), and hovering it lists every split's
            category and amount.
          </li>
        </Ul>
        <P>
          <b>Sync Balances</b> (top-right) refreshes Bank &amp; Cash balances only — the account types this
          page manages.
        </P>
        <Note>
          Amounts and balances are always shown in <b>that account's own currency</b> — a USD account shows
          "$", a EUR account shows "€", regardless of your reporting-currency setting elsewhere in the app.
        </Note>
        <Note>
          The <b>search box in the top-right header</b> (separate from the account-scoped search below the
          filters) searches every account at once. Clicking a result jumps to that account and transaction
          directly — the date range narrows automatically to make sure the transaction is actually on screen,
          and it's scrolled to and highlighted. Above the results, a summary shows the count and
          income/expense/net total for what matched, grouped by currency (each account's own, not converted) —
          if 50 or more transactions match, a note flags that the totals only cover the ones shown.
        </Note>
        <Note>
          The <b>Transfer To Account</b> field, when recording a transfer, hides inactive accounts by
          default — check <b>Show inactive</b> next to it to bring them back if you need to transfer to one.
          An inactive account already set as the target (when editing an existing transfer) always stays
          visible regardless of the checkbox.
        </Note>
        <Note>
          <b>Installment series</b>: when creating a new transaction, "Create installment series" generates
          every installment immediately, dated forward at whatever frequency you pick, with the description
          suffixed <code>(1/N)</code>, <code>(2/N)</code>, etc. When editing an <i>existing</i> transaction,
          the same option reads "Convert to installment series" — it turns that transaction into installment
          1/N in place and creates the remaining N-1 as new transactions, for when a plain purchase turns out
          to actually be a payment plan after the fact.
        </Note>
        <Note>
          When choosing a payee or category, typing a name with no match shows an inline <b>"+ Add"</b> option.
          Categories can be nested by typing a path like <code>Vacation : Skiing</code> — this reuses whichever
          part of the path already exists and creates only the missing segment(s), rather than requiring you to
          create each level separately.
        </Note>
        <Note>
          A non-transfer transaction now requires a category before it can be saved — otherwise it would
          silently fall out of every spending report. Transfers and drafts (still pending review) are exempt.
          Marking a transaction as a <b>Transfer</b> auto-fills the Payee with a configurable default (see
          System → App Settings → Transfers) whenever none is chosen yet. And for a single-category
          transaction, leaving the split's Memo blank now reuses the transaction's own Description instead of
          saving an empty memo.
        </Note>
      </>
    ),
  },
  {
    id: 'investments',
    label: 'Investments',
    body: (
      <>
        <H2>Investments</H2>
        <P>Manage brokerage/investment-account activity directly, in three views (each with its own search box):</P>
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
          <b>Transfer</b> (also available from a security's own page, under Investment Transactions) moves a
          holding from one account to another: the <b>same security</b> — a pure custody transfer, cost basis
          carried over — or a <b>different security</b>, a conversion/swap. An optional fee can be taken in the
          source security, the destination security, or cash from any account. Tick <b>Transfer all</b> to move
          the entire held quantity instead of typing it in — it stays pinned to the full amount if you switch
          which security you're sending.
        </P>
        <Note>
          A same-security transfer with <b>no fee</b> creates zero P&amp;L <i>overall</i> — nothing is gained or
          lost just by moving a position. But it does change each <b>individual account's own</b> figures: the
          source account's contribution drops to zero, while the destination immediately shows the position's
          full accrued Unrealized P&amp;L — inherited from its history, not newly created, but real for that
          account's own numbers. Day/period P&amp;L (DTD, WTD, etc.) stays flat for both accounts on the transfer
          date itself. A <b>fee taken in the security itself</b> (either side), or converting to a{' '}
          <b>different security</b>, is a genuine disposal at market price and does create a real, new gain or
          loss, exactly like an ordinary sale.
        </Note>
        <Note>
          Holdings shows two cost-basis figures: <b>Simple Avg</b>, a running average cost that blends on every
          buy and resets to zero whenever a position is fully closed out (so units sold long ago never drag down
          today's average), and <b>FIFO Avg</b>, the weighted cost of the specific lots still held assuming
          oldest-purchased-first. They usually match — they only diverge when a position was partially sold and
          later topped up at a different price <i>before</i> fully closing out, since FIFO and a blended average
          then disagree on which cost basis remains. Click a ticker or security name to open its full detail
          page (price history, all transactions, corporate actions).
        </Note>
        <Note>
          Price, Commission and Total (sec) are shown in the <b>security's own currency</b> (see the Curr
          column) — e.g. a USD stock inside a EUR-denominated account still shows its price in USD. W. Tax and
          Total (acc) are in the account's currency instead, since that's what was actually withheld/settled.
        </Note>
        <Note>
          In the New/Edit Transaction form, actions that require an already-outstanding position — Sell,
          Dividend, Reinvest, Split, ShrOut, Exercise, Expire, RtrnCap — narrow the Security picker to only what's
          actually held in the selected account. The exception is <b>short selling</b>: check{' '}
          <b>🔻 Short sell</b> to set Action to Sell and list every security again, since opening a short
          position doesn't require holding it first.
        </Note>
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
        <Note>
          When reviewing a Pending Draft, the category dropdown in Splits leads with that payee's most-used
          categories (a "Recent for this payee" group), the same suggestion behavior as the regular New
          Transaction form — handy for filling in a template's splits for the first time.
        </Note>
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
        <Note>
          The Details table is hierarchical — a parent category (e.g. "Vacation") shows the combined total of
          all its subcategories, with an arrow to expand/collapse them. Clicking a period cell on a parent row
          drills into every transaction across all its subcategories, not just ones posted directly to the
          parent.
        </Note>
        <Note>
          <b>Realized Investment P&amp;L</b> (gains/losses from closed trades) is shown as its own KPI figure,
          separate from Net Savings and Savings Rate above it — those stay purely cash-flow-based (salary,
          bills, dividends, interest) since capital gains are lumpy, one-off amounts that would otherwise
          distort a savings rate.
        </Note>

        <H3>🔄 Cash Flow Forecast</H3>
        <P>
          Projects future cash flow from three sources, shown separately: <b>explicitly scheduled</b> future
          transactions already entered, <b>active Recurring Templates</b> (see Recurring) projected forward from
          each template's own due date and frequency, and <b>statistically-detected patterns</b> — payee/category
          combinations seen in every one of the last few complete months, for recurring bills you haven't set up
          a template for.
        </P>
        <Note>
          A bill only ever counts once: statistically-detected patterns are skipped for any payee already covered
          by a scheduled transaction or an active Recurring Template, so setting up a template for something
          doesn't cause it to double up in the forecast.
        </Note>

        <H3>🎯 Budget &amp; Spending</H3>
        <P>Three tabs: Budget vs. Actual, Spending Trends, and Savings Rate.</P>
        <Note>
          On Budget vs. Actual, the <b>Copy</b> control fills the selected year's budget from another year's
          figures in one step: either that year's <b>budget</b> (e.g. carry 2025's budget into 2026), or its{' '}
          <b>actual</b> spend — including the same year, so you can turn this year's real spending into next
          year's starting budget. It overwrites any existing budget for the categories being copied.
        </Note>

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
        <Note>
          In the P&amp;L tab, <b>P&amp;L %</b> and <b>Unrealized %</b> are separate, sortable columns at both the
          account and security level, available for every window (D/W/M/Q/YTD/All), shown to two decimal places.
          A fee-free, same-security
          transfer between your own accounts (see Investments → Transfer) keeps <b>P&amp;L %</b> flat for both
          accounts on the transfer date — but <b>Unrealized %</b> relocates: it drops to zero for the source
          account and appears in the destination, reflecting the position's whole accrued history rather than
          new gain or loss. A transfer fee or a cross-security conversion, by contrast, does create real, new
          gain or loss.
        </Note>
        <Note>
          In an account's security drill-down, the <b>Price</b> column is directly editable — type a value and
          press Enter to save it as today's price for that security. This upserts today's row in{' '}
          <b>Historical_Prices</b>, the same table automatic price downloads write to, so it's a same-day
          override rather than a permanent edit: the next automatic refresh overwrites it again. Useful for
          illiquid or manually-tracked securities where you already know today's real price ahead of the next
          scheduled download.
        </Note>

        <H3>🧾 Investment Tax</H3>
        <P>
          Capital gains (by method), dividend income tax, and tax-loss harvesting candidates — based on the tax
          category rules configured in Static Data.
        </P>
        <Note>
          All three cost-basis methods — <b>WAC</b>, <b>FIFO</b>, and <b>LIFO</b> — correctly handle margin/short
          positions and positions that were fully closed and later reopened: cost basis is tracked per lot
          (FIFO/LIFO) or as a running average that resets at zero (WAC), so units sold off long ago never blend
          into the cost basis of a position rebuilt from scratch.
        </Note>

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
        <P>
          Clicking a security's name (here or anywhere else it's shown as a link) opens its <b>Security Detail</b>{' '}
          page — Setup, Prices, Investment Transactions (with a <b>New Transaction</b> button and a Tax column
          for withholding tax), Price Anomalies, Dividends, Corporate Actions, News, Alerts, and Downloads, all
          for that one security.
        </P>
        <Note>
          The Investment Transactions tab's KPI row also shows <b>Sharpe Ratio</b>, <b>Agent Signal</b>, and{' '}
          <b>Analyst Target</b> (with upside %) for the security, when available — the same figures as the
          Securities Analysis → Portfolio Action Signals report, at a glance without leaving the security.
        </Note>
        <Note>
          A security's <b>Alerts</b> tab shows the same price-above/price-below alerts as Market Data → Alerts,
          pre-scoped to that security — adding one here skips picking it from a dropdown. Allocation-drift
          alerts (tied to an asset type, not one security) only show up on the Market Data page.
        </Note>
      </>
    ),
  },
  {
    id: 'news',
    label: 'News',
    body: (
      <>
        <H2>News</H2>
        <P>
          A single feed of news relevant to your finances, filterable by <b>Securities</b>, <b>Institutions</b>, and{' '}
          <b>Companies</b>. Clicking an item marks it read and opens the source article in a new tab. A{' '}
          <b>Refresh</b> button on the page triggers an immediate fetch; otherwise it refreshes automatically in
          the background (see System → Scheduled Tasks, job <b>News Fetch</b>). A <b>Security Detail</b> page's{' '}
          own <b>News</b> tab shows the same feed pre-filtered to just that security.
        </P>
        <Ul>
          <li><b>Securities</b> — every security currently held or on your Market Data → Watchlist, sourced from Yahoo Finance.</li>
          <li><b>Institutions</b> — every bank/broker/exchange you have an active account with (Static Data → Institutions), sourced from a web news search on the institution's name (DuckDuckGo, Bing, and Yahoo News, combined).</li>
          <li>
            <b>Companies</b> — payees explicitly opted in via a <b>Track for news</b> checkbox on Static Data →{' '}
            Payees (e.g. an employer paying your salary) — nothing is tracked automatically. Also sourced from
            the same web news search.
          </li>
        </Ul>
        <P>
          The <b>search box</b> at the top of the page looks up news for anything, tracked or not — type a
          security, ticker, institution, or company name and press <b>Search</b>. If it matches a known security
          it uses Yahoo Finance (same quality as the tracked feed); otherwise it runs the same live web news
          search on whatever you typed. Search results aren't saved to the tracked feed. <b>Clear</b> returns to
          the normal filtered view.
        </P>
        <Note>
          Institution and company news — and any search that falls back to a web search — comes from a
          name-based query across several general search engines rather than a dedicated news API, so it's
          noisier and less reliable than the ticker-based securities news, and any one of those engines can
          occasionally time out or rate-limit under heavy use (the News Fetch job already paces its own requests
          and retries automatically; a manual search that fails is usually worth just retrying a bit later).
        </Note>
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
          <li><b>QIF</b> — bulk import from a Quicken/QIF file, plus a Transfer Issues tool for fixing unmatched transfer pairs.</li>
        </Ul>
        <P>
          Import Profiles and Payee Rules are reusable — set a bank's column mapping or a payee's default
          category once, then every future import uses it automatically.
        </P>
        <Note>
          <b>QIF Importer</b> is a bulk migration tool for a from-scratch move from Quicken, MS Money, or
          similar — a 3-step wizard rather than an incremental connector like the others above. <b>1. Parse</b>:
          upload a file to see a raw preview and a read-only breakdown of every account it contains, grouped as
          Cash/Bank, Credit, Investment, or Asset/Liability. <b>2. Map/Define</b>: for each account, choose to
          skip it, map it onto an existing Oikos account, or create a new one (name and type both editable —
          QIF can't tell Checking from Savings apart, so that's always a manual pick); set a date range to limit
          which transactions come in; and optionally <b>clear existing tables first</b> for a true from-scratch
          reimport (nothing is deleted unless you explicitly check a table — everything defaults to off — with
          per-account exclusion pickers to preserve specific accounts' data even while clearing the rest).
          Clearing a table cascades to every other table that references it — checking a box shows the
          complete, real list of everything that will be wiped, with row counts, computed from the database's
          actual foreign keys rather than a fixed list, and related checkboxes auto-check themselves so the
          selection can't understate the impact. <b>3. Import</b>: review a summary of every choice, then confirm.
        </Note>
        <Note>
          <b>Interactive Brokers (Flex)</b>: IB only refreshes an Activity Statement once per day, so a second
          "Fetch &amp; Preview" the same day would normally fail — the app caches that day's statement
          automatically and reuses it, with a link to force a fresh fetch from IB if you really need one.
          "Exclude FX Spot / currency-conversion trades" (on by default) filters out the internal{' '}
          <code>EUR.GBP</code>-style trades IB books to fund foreign-currency purchases — these aren't real
          positions. Interest income
          (including Stock Yield Enhancement Program payments) is booked against one placeholder security per
          settlement currency rather than one per month, so it doesn't spawn a new "security" every time it recurs.
          A dividend's withholding tax — which IB reports as its own separate line — is merged into that
          dividend's own row instead of importing as a second, disconnected record; the preview table's
          "Tax (€)" column shows it before you confirm. A dividend's Quantity is filled in from the actual
          position size held on that date (based on already-imported trades) rather than a placeholder of 1 —
          this only applies at the moment of import, so the preview table still shows the placeholder.
        </Note>
        <Note>
          <b>Saxo Bank → Account Charges</b>: account-level charges (VAT, CustodyFee, FinancingCost, …) have no
          underlying security, so they're linked to a single <b>Charge Payee</b> instead — configurable in that
          section, defaulting to auto-creating a "Saxo Bank" payee the first time you import if left unset.
          A dividend's Quantity is filled in from the actual position held on that date, same as Interactive
          Brokers — but Saxo's own dividend figure already has withholding tax deducted before it reaches the
          statement, reported only as a period total rather than per-dividend, so there's no separate tax
          amount to record on the row.
        </Note>
        <Note>
          <b>Saxo Bank → Authentication</b>: after authorizing with Saxo, the redirect back to Oikos carries the
          authorization code in the URL — it's picked up and pasted into the "authorization code" field
          automatically, so you don't need to copy it out of the address bar by hand.
        </Note>
        <Note>
          <b>Security Mapping</b>: on Interactive Brokers, Coinbase, Saxo Bank, Revolut Trading, Capital.com, and
          FxPro, any imported security that isn't found in your database appears in a mapping panel before you
          confirm the import — map it to an existing security (e.g. an imported "ATOM" to your existing
          "ATOMUSD") instead of letting it create a duplicate under a different ticker. The mapping is
          remembered per source, so the same symbol resolves automatically next time.
        </Note>
        <Note>
          <b>Choosing what to import</b>: every Brokerage importer's preview table has a per-row checkbox in
          the "Import" column, so you can include or exclude individual records regardless of how they're
          flagged — even a row marked "New" or "⚠️ Likely Dup". <b>New</b> rows are checked by default;{' '}
          <b>Likely Dup</b> rows start unchecked (opt in only if you're sure it isn't actually a duplicate);
          rows already in the database ("Exists") can't be re-selected. A checkbox in the column header
          selects/deselects everything in that table at once.
        </Note>
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
          <li>
            <b>⚙️ System</b> — App Settings (decimal/thousands separators, date format, week-start day, reporting
            currency, default transfer payee name) and Scheduled Tasks (cron-style jobs: e.g. the monthly/weekly
            AI summaries). App Settings — and every other saved preference across the app (report filters,
            last-used tabs, account selections, etc.) — are stored server-side, so they follow you across
            browsers, devices, and however you access Oikos (LAN IP, hostname, or remotely), instead of being
            tied to one browser's local storage.
          </li>
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
          reclaim horizontal space — the collapsed state is remembered across visits. On a phone or narrow
          window, the sidebar hides behind a hamburger button (top-left) and slides in as an overlay when tapped,
          instead of a permanent icon-only rail.
        </P>
        <H3>Theme</H3>
        <P>Light / System / Dark, switchable at the bottom of the sidebar.</P>
        <H3>Currency</H3>
        <P>All figures are converted to a single reporting currency (EUR by default) using historical FX rates as of each date, so historical comparisons stay meaningful even if you hold multi-currency accounts.</P>
        <H3>Persisted filters</H3>
        <P>
          Most report filters (date range, grouping, account selection, tab choice) are remembered server-side,
          so returning to a report picks up where you left off — on any browser, device, or however you access
          Oikos (LAN IP, hostname, or remotely).
        </P>
        <H3>Rearranging and hiding table columns</H3>
        <P>
          Drag a column header left or right to reorder it in any data table (Cash Register, Investments,
          Static Data, Market Data, Security Detail, and more). The <b>Columns</b> button next to each table
          opens a checklist to show or hide individual columns. The new order, visibility, and any manual
          column width are remembered the same way as other saved view settings, so they follow you across
          reloads and devices.
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
    <div className="flex flex-col md:flex-row h-full">
      <nav className="shrink-0 md:w-52 border-b md:border-b-0 md:border-r border-slate-200 bg-slate-50 flex flex-row md:flex-col overflow-x-auto md:overflow-y-auto py-1 md:py-4">
        <p className="hidden md:block px-4 text-xs font-semibold text-slate-400 uppercase tracking-wide mb-2">User Guide</p>
        {SECTIONS.map(s => (
          <button key={s.id} onClick={() => setActive(s.id)}
            className={`text-left px-4 py-2 text-sm whitespace-nowrap transition-colors border-b-2 md:border-b-0 md:border-r-2 ${active === s.id ? 'bg-blue-50 text-blue-700 font-semibold border-blue-600' : 'text-slate-600 hover:bg-slate-100 border-transparent'}`}>
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
