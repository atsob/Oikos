# Changelog

All notable changes to Oikos are recorded here, most recent first. Also viewable in-app under **Release Notes**.

## 2026-07-07

### Fixed
- **Cash Flow Forecast report ignored your Recurring Templates entirely**: the report only ever showed explicitly scheduled future transactions and statistically-guessed patterns from recent history — it never once queried the Recurring Templates table, so a rent, subscription, or salary template you'd actually configured was invisible to the forecast unless it happened to also get picked up by the guesswork. It now projects every active template forward from its own due date and frequency, and the statistical guesswork skips any payee already covered by a template (or a scheduled transaction) so nothing is counted twice.
- **Interactive Brokers importer — repeated same-day "Fetch & Preview" attempts always failed**: IB's Activity Statement Flex Queries only refresh once per day, so any fetch after the first one that day had nothing new to generate and reliably errored. The app now caches each day's successfully-fetched statement and reuses it automatically, with a link to force a fresh fetch from IB if genuinely needed. Also fixed the error message shown for IB Flex failures — it was displaying axios's generic "Request failed with status code 500" instead of IB's actual, much more informative error text.
- **Interactive Brokers importer — a stale error banner could persist across unrelated actions**: the error shown above the Fetch/Parse button checked both the "Fetch via API" and "Paste XML" actions' error state at once, so a failed API fetch earlier in the session kept showing even after successfully switching to Paste XML mode and parsing correctly. The banner now only reflects whichever mode is currently active, and switching modes clears both.
- **Interactive Brokers importer — FX Spot currency-conversion trades (e.g. `EUR.GBP`, `EUR.USD`) could be imported as fake securities**: these are IB's own internal trades to fund foreign-currency purchases, not real positions. Added an "Exclude FX Spot / currency-conversion trades" option (on by default) that filters them from the preview, the Security Mapping panel, and the actual import.
- **Interactive Brokers importer — interest income (including Stock Yield Enhancement Program payments) could spawn a new throwaway "security" every month**: IB's own description text for these cash transactions embeds the month (e.g. "...INTEREST FOR JUN-2026"), which the importer was using as the security-matching key — guaranteed to never match anything from a prior month. These are now booked against one stable placeholder security per settlement currency (matching this app's existing convention of separate securities per currency variant, e.g. `Thomson Reuters Corp (USD)` / `(GBP)`), so the same recurring interest correctly reuses the same security every time regardless of the month-specific wording IB sends.

### Added
- **Cash Flow Forecast report**: new "Recurring Templates" section, with its own chart series and In/Out KPI cards, alongside the existing Scheduled and statistically-detected sections.
- **Interactive Brokers importer**: "Force a fresh fetch from IB" link, shown when a cached statement is being reused, to bypass the daily cache when you specifically need up-to-the-minute data.

## 2026-07-06

### Fixed
- **Inv. Performance P&L report — same-security transfers between your own accounts were misattributed as investment performance**: the period P&L cash-flow adjustment (DTD/WTD/MTD/QTD/YTD) and the All-Time/YTD invested-capital figures both omitted `ShrIn`/`ShrOut` (custody-transfer) rows from their cash-flow calculations, while the all-time P&L figure already correctly included them. Caught in real data: an Ethereum position on Coinbase showed a one-day P&L of +€309 (+53.9%) immediately after receiving a transfer from Crypto.com — the true figure, once the transferred value is correctly excluded, was -€307 (-53.5%), just that day's actual price move. The same omission also affected the "All" column's realized/unrealized split and the TWR/MWR report's MWR/XIRR calculation when scoped to a subset of accounts (a transfer crossing the scope boundary wasn't recognized as a contribution/withdrawal for that scope). Fixing this also surfaced a pre-existing, unrelated data gap: a handful of historical share transfers predating this feature (e.g. a 2021 Reuters Group → Thomson Reuters Corp conversion) have no cost basis recorded at all, so their position still shows an inflated all-time gain — left as-is rather than inventing a cost basis, since a genuine one can't be reconstructed from the data on hand.
- **Coinbase importer — on-chain transfers arriving could import as an outgoing transfer (`ShrOut`) instead of incoming (`ShrIn`)**: Coinbase can report `type: "send"` with a *positive* amount for crypto arriving via certain transfer paths (e.g. L2/Base transfers), which the importer previously took at face value. It now derives direction from the amount's sign instead, confirmed against live Coinbase API data.
- **Investment Transfer/Convert — a fee taken during a cross-security conversion could silently vanish with no record**: when converting between different securities (not a same-security custody transfer), a fee taken in either the source or destination security simply shrank the resulting Buy quantity with nothing recorded to explain why — unlike a same-security transfer, where the fee has always been booked as its own explicit disposal. Verified with real data: converting 10 Ripple into Tezos with a 1-Tezos destination fee produced no trace of the fee anywhere in the transaction history. Both fee variants (source and destination) now record the fee as its own labeled row, matching how same-security transfers already handle it.

### Added
- **Investment Transfer / Convert**: a new **Transfer** action (Investments page, and Security Detail → Investment Transactions) that moves a holding from one account to another — same security (a pure custody transfer, cost basis carried over, no gain/loss) or a different security (a conversion/swap that realizes gain/loss on the source at its market price and establishes a fresh cost basis on the destination). Supports an optional fee taken in the source security, the destination security, or cash from any account, and respects "Show inactive accounts" filtering in its account dropdowns.
- **Inv. Performance P&L report**: **P&L %** and **Unrealized %** are now separate, independently sortable columns at both the account and security level (previously shown inline next to the € amount and not sortable). P&L % now also shows for every window — D/W/M/Q/YTD/All — instead of only DTD/YTD/All.
- Help page: new "Why Oikos vs. other personal finance apps?" section, and coverage of the new Transfer feature.

### Changed
- **Portfolio Action Signals** (Reports → Securities Analysis): merged the "⚪ NEUTRAL" signal into "🟡 HOLD", and "📊 ANALYST BUY"/"🔻 ANALYST UNDERPERFORM" into "📈 ANALYST UPGRADE"/"⚠️ ANALYST CAUTION". These were adjacent bands of the same continuous quant score split at zero, so a security sitting near that boundary would flip labels — and fire a "Signal Change" Dashboard alert — from trivial day-to-day score movement with no real change in outlook.

## 2026-07-05

### Fixed
- **Income & Expense report — Realized Investment P&L was wildly wrong**: the old figure could be inflated by hundreds of thousands of euros for accounts that ever sold before a matching buy existed (margin/CFD accounts opening a short position) — the calculation matched sells only against earlier buy lots and silently treated any unmatched portion as free profit. Realized P&L is now computed with a proper signed-lot FIFO simulation that tracks both long and short lots, correctly deferring recognition until a position is actually closed. The same bug, and the same fix, applied to the Investment Tax report's FIFO and LIFO methods.
- **Investment Tax report — WAC (Weighted Average Cost) overstated cost basis**: it recomputed an average from every buy since the position was last fully closed, which double-counted buy value/quantity already consumed by an earlier partial sell within the same still-open position. Now uses a proper running average that blends on every buy and correctly shrinks (without recomputing) as the position is drawn down.
- **Holdings — Simple Avg and FIFO Avg cost per share could be badly wrong**:
  - The database trigger that maintains Holdings guessed "long" vs "short" once from the aggregate lifetime total-buys-vs-total-sells sign, which broke for accounts with negative-quantity adjustment rows (e.g. daily interest-accrual reversals on cash-fund holdings) or any position that went both long and short over its history — in one case making a real holding disappear entirely (quantity computed as zero).
  - Staking-reward entries and corporate-action execution (splits, delistings) never triggered the accurate cost-basis recompute, leaving Holdings with stale figures after those actions.
  - **Simple Avg** was an unweighted mean of purchase prices (so a 0.001-unit buy counted as much as a 100-unit buy), then — after an initial fix — still wrong for any position fully sold and later rebought, since it kept blending in cost basis from units no longer held. It's now a running average that resets to zero whenever a position closes out exactly, matching FIFO Avg except where lot order genuinely matters (observed: a Bitcoin holding sold off entirely in 2017 and 2020 and rebought from 2025 onward now correctly shows ~€86,470 instead of ~€55,000 or ~€2,100 from the two earlier, still-wrong versions).
- **Income & Expense KPIs didn't reconcile**: "Savings by Investments" silently included realized trading P&L while "Net Savings" excluded it, so the two could disagree by tens of thousands of euros with no visible explanation. Realized P&L is now excluded from both and shown as its own clearly-labeled "Realized Investment P&L" figure instead.
- **Recurring transfer templates could silently drop the mirrored transaction**: three of four code paths that generate/confirm template-driven transfers never created the destination-account leg or linked the two rows via a shared transfer ID. All four now share the same logic; the one real affected transaction found in the data was repaired.
- **Investments → Holdings**: clicking a ticker or security name silently did nothing (a scoping bug meant the click handler called an undefined function). Now correctly opens the security's detail page.
- **Investments → Transactions**: editing a transaction linked to an inactive account showed a blank Account field, since the edit form's account dropdown excluded inactive accounts — could look like an orphaned/unlinked record. The dropdown now always includes the transaction's actual account, active or not.
- **Multi-currency amounts showed the wrong currency symbol**: Price/Commission/Total and account balances in Investments and Cash Register always showed the reporting-currency symbol (typically €) regardless of the security's or account's real currency — e.g. a USD stock's price, or a USD account's balance, both displayed with "€". These now show the correct native currency throughout (grids, page subtitles, the credit-card summary bar, and cross-account search results).
- Fixed a pandas `FutureWarning` about DataFrame concatenation dtype handling in the realized-P&L calculation (cosmetic — no behavior change).

### Added
- **Dashboard → Net Worth Trend**: optional Kondratieff wave phase overlay — a clearly-labeled reference/educational shading of long secular market "seasons," off by default, with editable phase boundaries.
- **Income & Expense report**: the Details table is now hierarchical — a parent category (e.g. "Vacation") shows the combined total of all its subcategories, collapsible, with drill-down across the whole subtree.
- Categories can now be created inline when entering a cash transaction, the same way payees already could — including creating a new subcategory under an existing one by typing a path like "Vacation : Skiing".
- **Security Detail**: quantity fields (holdings, transactions, corporate-action previews, and their Copy-to-clipboard exports) now show 8 decimal places instead of 4, matching how fractional crypto/share quantities are actually stored.

### Changed
- Consolidated the two categories `_RlzdGain`/`_RlzdLoss` into a single `_RlzdPnL` category for realized investment gains and losses.

## 2026-07-04

### Fixed
- **Mobile usability**: the app is now genuinely usable from a phone browser, not just technically reachable. Specifically:
  - The main sidebar now hides behind a hamburger button below tablet width and slides in as an overlay, instead of permanently eating 15–25% of a phone screen's width on every page.
  - Reports' and Help's report-list side rail — previously a *second* fixed-width sidebar stacked next to the main nav — now becomes a horizontally-scrollable tab strip on narrow screens.
  - Page headers (shared across nearly every page) now wrap their action buttons onto new rows instead of forcing them onto one unbreakable row that ran off-screen; Cash Register's and Recurring's own toolbars were fixed the same way.
  - Static Data's and Market Data's tab bars (Payees/Categories/Institutions/… and Currencies/Securities/…) no longer get squeezed down to near-nothing by a fixed-width search box next to them — several tabs were genuinely unreachable on a phone with no indication they existed. Search now sits below the tabs on narrow screens, and the tabs scroll horizontally with a visible scrollbar.
  - **Sync Balances** (Dashboard, Cash Register, Investments) opened its options via CSS hover only, which never triggers on a touchscreen — the feature was silently unusable on mobile. Now opens on tap, closes on tapping elsewhere, desktop click/hover unaffected.
  - The "ⓘ" info tooltips throughout Reports (Sharpe Ratio, VaR, Max Drawdown, etc. — over 100 of them) had the same hover-only problem. Now open on tap too.
- Fixed a silent-failure bug affecting **Sync Balances** and **Backup/Restore**: several backend functions caught their own errors and tried to display them via a leftover Streamlit call (dead code since the move to FastAPI), which either threw an unrelated error or silently no-op'd — either way, a real failure (e.g. a bad balance calculation) could be reported back as "ok" with nothing actually changed. These now log properly and let the error surface to the API response as intended.

## 2026-07-03

### Fixed
- **Net Worth report**: the annualized % and delta now always reconcile to the exact net change shown, instead of drifting from naive income-minus-expenses math when tax/interest/other cash movements were involved.
- **Net Worth report**: period comparisons (including YTD) now always compare against the first period of the selected date range, instead of silently comparing against the previous grouping bucket.
- Fixed a crash (`isDark is not defined`) on Reports → Securities Analysis → Investment Signals.
- **Monthly AI Summary**: the generated net change figure now always reconciles with reported income/expenses/tax/interest/other, and no longer fabricates a "previous month net worth" comparison that wasn't backed by real data.
- **Dashboard**: fixed the account-balance history reconstruction (used for "vs prev month" / YTD deltas) to correctly account for dividend reinvestments and share transfers, and to scope trade history per account — previously a security held in multiple accounts could have its history double-counted.
- **Dashboard**: the historical baseline used for "vs prev month" / YTD deltas now respects the same account selection as the current totals — previously the baseline always included every account regardless of what was selected in the Options panel.
- `database/Oikos.sql`: added the missing `Portfolio_Presets` table and corrected the `instrument_type` enum name to match the live database, so a fresh install now matches production exactly.
- **Security Detail**: editing an investment transaction now correctly shows its withholding tax (the API response and the edit form were both silently dropping it).
- **Dividend forecast**: securities with stale/garbage `Dividend Pay Date` data (e.g. from decades ago) no longer produce a bogus near-term forecast entry alongside the correct one — the pay date is now only trusted as an anchor when it falls within a sane window of the ex-dividend date, otherwise it's derived from the ex-dividend date instead.
- **Reports not updating after imports/edits**: removed 47 leftover caching decorators from the pre-FastAPI (Streamlit) version of the app that had no invalidation hooks anywhere in the API — any write (an import, a new/edited transaction) could stay invisible in Income & Expense and other reports for up to an hour. Reports now always reflect the latest data.
- **Settings not persisting**: app settings (decimal/thousands separators, date format, etc.) and all saved UI preferences (report filters, last-used tabs, account selections) are now stored server-side instead of only in browser local storage — previously they'd appear to "reset" whenever you accessed Oikos from a different URL (LAN IP vs. hostname vs. remote), a different device, or after a browser evicted local storage (e.g. Safari's storage eviction policy). Existing browser-only settings are migrated automatically the first time you load the updated app.
- Local dev server (`npm run dev`) now fails clearly instead of silently switching to a different port when the usual port is already taken — the silent switch was itself a cause of the settings-persistence problem above, since a different port is a different origin as far as browser storage is concerned.

### Added
- In-app **Help & User Guide** page, covering every section of the app.
- In-app **Release Notes** page (this file, rendered live).
- **Sync Balances** button on Cash Register (scoped to Bank & Cash) and Investments (scoped to Investments/Pension/Holdings) — previously only available on the Dashboard.
- **Net Worth report**: "Show inactive accounts" toggle, so closed/inactive accounts can be included or excluded consistently, matching Dashboard's own account-selection behavior.
- **Dashboard**: new Assets KPI card (Real Estate/Vehicle/Other Assets), completing the breakdown alongside Cash, Investments, and Pension.
- **Inv. Performance report**: P&L % now shown on the top KPI cards, not just in the per-account table.
- Sidebar now shows the exact build (git commit hash + date) below the app version, wired through to Docker builds via build args.
- Collapsible sidebar (icon-only rail) to reclaim horizontal space; state is remembered across visits.
- **Security Detail**: added a **New Transaction** button (reuses the same form as Investments → Transactions) and a **Tax** column showing withholding tax on each transaction, including in the copy-to-clipboard export.

### Changed
- Database renamed from `Finance` to `Oikos` (config, docker-compose, and backup-script references updated to match).
- Net Worth KPI cards on Dashboard and Reports → Net Worth are now sized so all cards fit on one line, with the primary Net Worth card kept visually larger than the rest.
- Cleaned up dead/orphaned database objects: a duplicate unused `Transactions.Template_Id` column and an orphaned `update_accounts_balance()` trigger function.
- **Investments → Cash tab** now reuses the exact same transaction form as Cash Register, instead of a separately-maintained copy — as a side effect, it gains the inline "add new payee" option that Cash Register already had.
- The Security setup form (ticker, type, dividends, fixed-income fields, etc.) is now one shared component used by both Security Detail and Market Data → Securities, instead of two independently-maintained copies of the same ~25 fields.
