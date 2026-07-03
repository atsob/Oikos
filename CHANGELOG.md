# Changelog

All notable changes to Oikos are recorded here, most recent first. Also viewable in-app under **Release Notes**.

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
