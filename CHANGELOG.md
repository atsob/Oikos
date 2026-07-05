# Changelog

All notable changes to Oikos are recorded here, most recent first. Also viewable in-app under **Release Notes**.

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
