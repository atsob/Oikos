// The Dashboard's own options (which accounts count, future-dated items, switched-off accounts) — kept in the browser, shared with the pages
// that open from its KPI cards so that they count the same accounts.
export const OPTS_KEY = 'oikos-dashboard-opts'
export interface DashOpts { includedAccounts: number[] | 'all'; includeFuture: boolean; showDisabled: boolean }
export const defaultOpts = (): DashOpts => ({ includedAccounts: 'all', includeFuture: false, showDisabled: false })
export function loadOpts(): DashOpts {
  try { return { ...defaultOpts(), ...JSON.parse(localStorage.getItem(OPTS_KEY) || '{}') } }
  catch { return defaultOpts() }
}
export function saveOpts(o: DashOpts) { localStorage.setItem(OPTS_KEY, JSON.stringify(o)) }

// An account that is switched off but now holds (almost) nothing — a closed brokerage, matured time deposits — adds nothing to the "now"
// total whether or not it is counted, but its balance in the past is part of the history: leaving it out of the baseline made
// "YTD" look like new money. Only a switched-off account that still holds something is left out, to match the "now" total.
export const holdsNothing = (a: Record<string, unknown>) => Math.abs(Number(a.balance_eur ?? a.balance ?? 0)) < 0.5

// The accounts the history (vs previous month / daily / YTD) is computed over.
export function historyAccountIds(accounts: Record<string, unknown>[], opts: DashOpts): number[] {
  return accounts
    .filter(a => opts.showDisabled || a.is_active !== false || holdsNothing(a))
    .filter(a => opts.includedAccounts === 'all' || (opts.includedAccounts as number[]).includes(Number(a.id)))
    .map(a => Number(a.id)).sort((a, b) => a - b)
}
