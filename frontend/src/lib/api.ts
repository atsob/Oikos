import axios from 'axios'
import { queryClient } from './queryClient'

export const api = axios.create({ baseURL: '/api', withCredentials: true })

// Every endpoint that inserts/updates/deletes Investments rows must call this so cached
// P&L figures (Reports 'pnl'/'pnl-all', SecurityDetail 'pnl-all-time') and portfolio
// signals (current qty/value driving 'portfolio-signals', e.g. the Securities Analysis
// -> Portfolio Action Signals grid) don't keep showing pre-transaction numbers until
// their staleTime lapses and something happens to remount them.
export const invalidatePnl = () =>
  queryClient.invalidateQueries({
    predicate: q => typeof q.queryKey[0] === 'string'
      && ((q.queryKey[0] as string).startsWith('pnl') || q.queryKey[0] === 'portfolio-signals'),
  })

// On session expiry/invalidation, redirect to /login — except for the /auth/me
// probe's own 401 (that's the expected "not logged in yet" response RequireAuth
// checks for, not a session that just expired), which would otherwise loop.
api.interceptors.response.use(
  res => res,
  err => {
    if (err.response?.status === 401 && !String(err.config?.url ?? '').endsWith('/auth/me')) {
      queryClient.clear()
      if (window.location.pathname !== '/login') window.location.href = '/login'
    }
    // A change refused for the user's role (read-only, or an account that is not theirs): the Layout shows a notice.
    if (err.response?.status === 403 && String(err.config?.method ?? 'get') !== 'get') {
      window.dispatchEvent(new CustomEvent('oikos-forbidden', { detail: String(err.response?.data?.detail ?? 'Not permitted for your role') }))
    }
    return Promise.reject(err)
  },
)

// ── Auth ─────────────────────────────────────────────────────────────────────
export const login = (username: string, password: string): Promise<{ username: string }> =>
  api.post('/auth/login', { username, password }).then(r => r.data)

export const logout = (): Promise<{ ok: boolean }> =>
  api.post('/auth/logout').then(r => r.data)

export const getMe = (): Promise<{ username: string; role: 'admin' | 'limited' | 'readonly'; accounts: number[] | null }> =>
  api.get('/auth/me').then(r => r.data)

export const changePassword = (currentPassword: string, newPassword: string): Promise<{ ok: boolean }> =>
  api.post('/auth/change-password', { current_password: currentPassword, new_password: newPassword }).then(r => r.data)

export const getUsers = (): Promise<{ users_id: number; username: string; created_at: string; role: 'admin' | 'limited' | 'readonly'; account_ids: number[] }[]> =>
  api.get('/auth/users').then(r => r.data)

export const addUser = (username: string, password: string, role: 'admin' | 'limited' | 'readonly' = 'admin', accountIds: number[] = []): Promise<{ users_id: number; username: string }> =>
  api.post('/auth/users', { username, password, role, account_ids: accountIds }).then(r => r.data)

export const updateUserAccess = (usersId: number, body: { role: 'admin' | 'limited' | 'readonly'; account_ids: number[]; new_password?: string }): Promise<{ ok: boolean }> =>
  api.put(`/auth/users/${usersId}`, body).then(r => r.data)

export const deleteUser = (usersId: number): Promise<{ ok: boolean }> =>
  api.delete(`/auth/users/${usersId}`).then(r => r.data)

export const getChangelog = (): Promise<{ content: string }> =>
  api.get('/changelog').then(r => r.data)

// ── User Preferences ────────────────────────────────────────────────────────
export const getPreferences = (): Promise<Record<string, unknown>> =>
  api.get('/preferences').then(r => r.data)

export const setPreference = (key: string, value: unknown) =>
  api.put(`/preferences/${encodeURIComponent(key)}`, { value }).then(r => r.data)

// ── Dashboard ────────────────────────────────────────────────────────────────
export const getNetWorth = (startDate = '2020-01-01', accountIds?: number[]) =>
  api.get('/dashboard/net-worth', {
    params: { start_date: startDate, ...(accountIds ? { account_ids: accountIds.join(',') } : {}) },
  }).then(r => r.data)

export const getAccounts = (includeFuture = false) =>
  api.get('/dashboard/accounts', { params: { include_future: includeFuture } }).then(r => r.data)

// One Dashboard KPI split by account (api/routers/dashboard.py::get_account_breakdown)
export type KpiAccount = {
  id: number; name: string; type: string; currency: string; institution: string | null; is_active: boolean; balance_eur: number; balance_native: number | null
  delta_daily: number; delta_prev_month: number; delta_ytd: number
}
export type KpiBreakdown = { group: string; label: string; accounts: KpiAccount[]; total: { balance_eur: number; delta_daily: number; delta_prev_month: number; delta_ytd: number }; as_of: string }
export const getAccountBreakdown = (group: string, accountIds: number[], includeFuture = false) =>
  api.get('/dashboard/account-breakdown', { params: { group, account_ids: accountIds.join(','), include_future: includeFuture } }).then(r => r.data as KpiBreakdown)

export const getMonthlySummaries = (limit = 24) =>
  api.get('/dashboard/monthly-summaries', { params: { limit } }).then(r => r.data)

export const generateMonthlySummary = (month_start: string) =>
  api.post('/dashboard/monthly-summaries/generate', { month_start }).then(r => r.data)

export const getWeeklySummaries = (limit = 12) =>
  api.get('/dashboard/weekly-summaries', { params: { limit } }).then(r => r.data)

export const generateWeeklySummary = (week_start: string) =>
  api.post('/dashboard/weekly-summaries/generate', { week_start }).then(r => r.data)

export const getDraftTransactions = () =>
  api.get('/dashboard/draft-transactions').then(r => r.data)

export const getUncategorizedTransactions = () =>
  api.get('/dashboard/uncategorized-transactions').then(r => r.data)

export const getInsights = () =>
  api.get('/dashboard/insights').then(r => r.data)

export const getAlerts = () =>
  api.get('/dashboard/alerts').then(r => r.data)
export const acknowledgeSignal = (securitiesId: number) =>
  api.post(`/dashboard/alerts/acknowledge-signal/${securitiesId}`).then(r => r.data)
export const acknowledgeSplit = (corporateActionsId: number) =>
  api.post(`/dashboard/alerts/acknowledge-split/${corporateActionsId}`).then(r => r.data)
// Dismiss one alert (the row as GET /dashboard/alerts returned it) or a whole group —
// each alert by its own type's mechanism, see database/alert_groups.py.
export const dismissAlert = (alert: Record<string, unknown>) =>
  api.post('/dashboard/alerts/dismiss', alert).then(r => r.data)
// The group's rows are sent along so the server need not recompute every alert (several seconds) to learn which ones to dismiss.
export const dismissAlertGroup = (group: string, alerts: Record<string, unknown>[] = []) =>
  api.post(`/dashboard/alerts/dismiss-group/${group}`, { alerts }).then(r => r.data)
export const dismissTrendAlert = (securitiesId: number, alertType: 'ma_cross' | 'trailing_stop') =>
  api.post(`/dashboard/alerts/dismiss-trend/${securitiesId}/${alertType}`).then(r => r.data)
export const acknowledgeZoneChange = (securitiesId: number) =>
  api.post(`/dashboard/alerts/acknowledge-zone-change/${securitiesId}`).then(r => r.data)

export const getUpcomingBills = (days = 14) =>
  api.get('/dashboard/upcoming-bills', { params: { days } }).then(r => r.data)

export const getAnomalies = (days = 30, z = 2.5) =>
  api.get('/dashboard/anomalies', { params: { days, z } }).then(r => r.data)

export const syncBalances = (target = 'all') =>
  api.post('/tools/sync-balances', { target }).then(r => r.data)

export const confirmDraft = (id: number) =>
  api.post(`/dashboard/confirm-draft/${id}`)

export const confirmAllDrafts = () =>
  api.post('/dashboard/confirm-all-drafts')

export const deleteDraft = (id: number) =>
  api.delete(`/dashboard/delete-draft/${id}`).then(r => r.data)

// ── Register ─────────────────────────────────────────────────────────────────
export const getTransactions = (params: Record<string, unknown>) =>
  api.get('/register/transactions', { params }).then(r => r.data)

export const getTransactionIds = (params: Record<string, unknown>): Promise<{ ids: number[] }> =>
  api.get('/register/transactions/ids', { params }).then(r => r.data)

export const getTransactionById = (id: number) =>
  api.get(`/register/transactions/${id}`).then(r => r.data)

export const createTransaction = (data: Record<string, unknown>) =>
  api.post('/register/transactions', data).then(r => r.data)

export const updateTransaction = (id: number, data: Record<string, unknown>) =>
  api.put(`/register/transactions/${id}`, data).then(r => r.data)

export const deleteTransaction = (id: number) =>
  api.delete(`/register/transactions/${id}`)

export const batchDeleteTransactions = (ids: number[]): Promise<{ deleted: number; skipped: { id: number; reason: string }[] }> =>
  api.post('/register/transactions/batch-delete', { ids }).then(r => r.data)

export const batchMoveTransactions = (ids: number[], accountsId: number): Promise<{ moved: number }> =>
  api.post('/register/transactions/batch-move', { ids, accounts_id: accountsId }).then(r => r.data)

// ── Investments ───────────────────────────────────────────────────────────────
export const getInvestments = (params: Record<string, unknown>) =>
  api.get('/investments/list', { params }).then(r => r.data)

export const getHoldings = (accountId?: number, includeClosed = false) =>
  api.get('/investments/holdings', { params: { ...(accountId ? { account_id: accountId } : {}), include_closed: includeClosed } }).then(r => r.data)

export const updateHolding = (id: number, data: Record<string, unknown>) =>
  api.put(`/investments/holdings/${id}`, data).then(r => r.data)

export const getLinkedAccount = (accountId: number) =>
  api.get(`/investments/linked-account/${accountId}`).then(r => r.data)

export const batchUpdateInvestments = (ids: number[], changes: { accounts_id?: number; cash_account_id?: number }): Promise<{ updated: number; warnings: { account: string; security: string; quantity: number }[] }> =>
  api.post('/investments/transactions/batch-update', { ids, ...changes }).then(r => { invalidatePnl(); return r.data })

export const batchDeleteInvestments = (ids: number[]): Promise<{ deleted: number }> =>
  api.post('/investments/transactions/batch-delete', { ids }).then(r => { invalidatePnl(); return r.data })

export const previewInvestmentTransfer = (data: Record<string, unknown>) =>
  api.post('/investments/transfer/preview', data).then(r => r.data)
export const executeInvestmentTransfer = (data: Record<string, unknown>) =>
  api.post('/investments/transfer/execute', data).then(r => { invalidatePnl(); return r.data })

export const stakingReinvest = (entries: Record<string, unknown>[]) =>
  api.post('/investments/staking-reinvest', entries).then(r => { invalidatePnl(); return r.data })

// ── Reports ───────────────────────────────────────────────────────────────────
export const getIncomeExpense = (startDate: string, endDate: string) =>
  api.get('/reports/income-expense', { params: { start_date: startDate, end_date: endDate } }).then(r => r.data)

export const getTopCategories = (startDate: string, endDate: string, catType = 'Expense', topN = 10) =>
  api.get('/reports/top-categories', { params: { start_date: startDate, end_date: endDate, cat_type: catType, top_n: topN } }).then(r => r.data)

export const getCostsByBroker = (startDate: string, endDate: string) =>
  api.get('/reports/costs-by-broker', { params: { start_date: startDate, end_date: endDate } }).then(r => r.data)

export const getSavingsRate = (months = 12) =>
  api.get('/reports/savings-rate', { params: { months } }).then(r => r.data)

export const getPortfolioSummary = (accountIds?: number[]) =>
  api.get('/reports/portfolio-summary', { params: { account_ids: accountIds?.join(',') || undefined } }).then(r => r.data)

export const getCategoryBreakdown = (startDate: string, endDate: string) =>
  api.get('/reports/category-breakdown', { params: { start_date: startDate, end_date: endDate } }).then(r => r.data)

export const getBudgets = (year: number, month?: number) =>
  api.get('/reports/budgets', { params: { year, ...(month ? { month } : {}) } }).then(r => r.data)

export const getCapitalGains = (year: number, method: string = 'WAC', accountIds?: number[]) =>
  api.get('/reports/capital-gains', { params: { year, method, account_ids: accountIds?.join(',') || undefined } }).then(r => r.data)

export const getDividends = (startDate: string, endDate: string) =>
  api.get('/reports/dividends', { params: { start_date: startDate, end_date: endDate } }).then(r => r.data)

export const getNetWorthReport = (startDate: string, endDate: string, grouping = 'month') =>
  api.get('/reports/net-worth-report', { params: { start_date: startDate, end_date: endDate, grouping } }).then(r => r.data)

export const getPnl = (startDate = '1900-01-01', endDate?: string) =>
  api.get('/reports/pnl', { params: { start_date: startDate, ...(endDate ? { end_date: endDate } : {}) } }).then(r => r.data)

// Single-period P&L (since N years ago) — kept separate from getPnl so selecting a
// long-term window (1Y/3Y/5Y) doesn't add cost to that already-heavy, live-refetched
// query; only fetched on demand when one of those periods is actually selected.
export const getPnlPeriod = (years: number) =>
  api.get('/reports/pnl-period', { params: { years } }).then(r => r.data)

export const getIncomeExpenseDetail = (startDate: string, endDate: string, grouping = 'month') =>
  api.get('/reports/income-expense-detail', { params: { start_date: startDate, end_date: endDate, grouping } }).then(r => r.data)

export const getBudgetVsActual = (year: number, refYears = 2) =>
  api.get('/reports/budget-vs-actual', { params: { year, ref_years: refYears } }).then(r => r.data)
export const getAnnualIncome = (year: number) =>
  api.get('/reports/annual-income', { params: { year } }).then(r => r.data)
export const getYtdExpenseTransactions = (year: number) =>
  api.get('/reports/ytd-expense-transactions', { params: { year } }).then(r => r.data)
export const saveBudget = (data: { year: number; categories_id: number; budget_amount: number; id?: number }) =>
  api.post('/reports/budgets', data).then(r => r.data)

export const getCashFlowForecast = (monthsAhead = 6) =>
  api.get('/reports/cash-flow-forecast', { params: { months_ahead: monthsAhead } }).then(r => r.data)

export const getCashFlowForecastFull = (days = 60, monthsBack = 2, accountIds?: number[], minBalance = 0) =>
  api.get('/reports/cash-flow-forecast-full', { params: { days, months_back: monthsBack, account_ids: accountIds?.join(',') || undefined, min_balance: minBalance } }).then(r => r.data)

export const searchAllTransactions = (q: string, limit = 50) =>
  api.get('/register/search', { params: { q, limit } }).then(r => r.data)

export const clearAccount = (account_id: number, up_to_date: string) =>
  api.post('/register/clear', { account_id, up_to_date }).then(r => r.data)

export const reconcileAccount = (account_id: number, up_to_date: string) =>
  api.post('/register/reconcile', { account_id, up_to_date }).then(r => r.data)

export const createTransfer = (data: Record<string, unknown>) =>
  api.post('/register/transfers', data).then(r => r.data)

export const getSplits = (txId: number) =>
  api.get(`/register/transactions/${txId}/splits`).then(r => r.data)

export const upsertSplits = (txId: number, splits: Record<string, unknown>[]) =>
  api.put(`/register/transactions/${txId}/splits`, splits).then(r => r.data)

// ── Static Data ───────────────────────────────────────────────────────────────
export const getInstitutions = (search?: string) =>
  api.get('/static-data/institutions', { params: search ? { search } : {} }).then(r => r.data)

export const getCategories = (search?: string) =>
  api.get('/static-data/categories', { params: search ? { search } : {} }).then(r => r.data)

// stats=false skips the join+aggregate that computes # Txns / Last Used — pass it for
// plain payee pickers (dropdowns/autocomplete) that don't display those columns; only
// the Static Data admin grid needs the default stats=true.
export const getPayees = (search?: string, stats = true) =>
  api.get('/static-data/payees', { params: { ...(search ? { search } : {}), stats } }).then(r => r.data)

export const getAccountsMaster = (search?: string) =>
  api.get('/static-data/accounts-master', { params: search ? { search } : {} }).then(r => r.data)

export const getPayeeTopCategories = (payeeId: number, limit = 5) =>
  api.get(`/static-data/payees/${payeeId}/top-categories`, { params: { limit } }).then(r => r.data)

export const getPayeeTransactions = (payeeId: number) =>
  api.get(`/static-data/payees/${payeeId}/transactions`).then(r => r.data)

export const getCategoryTransactions = (categoryId: number) =>
  api.get(`/static-data/categories/${categoryId}/transactions`).then(r => r.data)

export const upsertPayee = (data: Record<string, unknown>) =>
  api.post('/static-data/payees', data).then(r => r.data)

export const mergePayees = (source_id: number, target_id: number) =>
  api.post('/static-data/payees/merge', { source_id, target_id }).then(r => r.data)

// Recomputes every payee's Default Category as whichever category its transaction
// splits use most often, overwriting whatever was there before. Returns how many
// payees' Default Category actually changed.
export const autoDefaultCategory = () =>
  api.post('/static-data/payees/auto-default-category').then(r => r.data)

export const mergeCategories = (source_id: number, target_id: number) =>
  api.post('/static-data/categories/merge', { source_id, target_id }).then(r => r.data)

export const upsertCategory = (data: Record<string, unknown>) =>
  api.post('/static-data/categories', data).then(r => r.data)

export const upsertInstitution = (data: Record<string, unknown>) =>
  api.post('/static-data/institutions', data).then(r => r.data)

export const getCreditRatings = () =>
  api.get('/static-data/credit-ratings').then(r => r.data)

export const getIssuers = (search?: string) =>
  api.get('/static-data/issuers', { params: search ? { search } : {} }).then(r => r.data)

export const upsertIssuer = (data: Record<string, unknown>) =>
  api.post('/static-data/issuers', data).then(r => r.data)

export const getSecuritiesMaster = (search?: string) =>
  api.get('/static-data/securities', { params: search ? { search } : {} }).then(r => r.data)

export const upsertSecurity = (data: Record<string, unknown>) =>
  api.post('/static-data/securities', data).then(r => r.data)

export const getCurrenciesMaster = () =>
  api.get('/static-data/currencies').then(r => r.data)

export const upsertCurrency = (data: Record<string, unknown>) =>
  api.post('/static-data/currencies', data).then(r => r.data)

export const getTaxCategoryRules = () =>
  api.get('/static-data/tax-category-rules').then(r => r.data)
export const createTaxCategoryRule = (data: Record<string, unknown>) =>
  api.post('/static-data/tax-category-rules', data).then(r => r.data)
export const updateTaxCategoryRule = (taxCategory: string, data: Record<string, unknown>) =>
  api.put(`/static-data/tax-category-rules/${encodeURIComponent(taxCategory)}`, data).then(r => r.data)
export const getInstrumentTypeOverrides = () =>
  api.get('/static-data/instrument-type-overrides').then(r => r.data)
export const createInstrumentTypeOverride = (data: Record<string, unknown>) =>
  api.post('/static-data/instrument-type-overrides', data).then(r => r.data)
export const updateInstrumentTypeOverride = (instrumentType: string, data: Record<string, unknown>) =>
  api.put(`/static-data/instrument-type-overrides/${encodeURIComponent(instrumentType)}`, data).then(r => r.data)

export const getAccountInterestRates = (accountsId: number) =>
  api.get('/static-data/account-interest-rates', { params: { accounts_id: accountsId } }).then(r => r.data)
export const upsertAccountInterestRateSchedule = (data: Record<string, unknown>) =>
  api.post('/static-data/account-interest-rates', data).then(r => r.data)
export const deleteAccountInterestRateSchedule = (scheduleId: number) =>
  api.delete(`/static-data/account-interest-rates/${scheduleId}`).then(r => r.data)

// ── Market Data ───────────────────────────────────────────────────────────────
export const getCurrencies = () =>
  api.get('/market-data/currencies').then(r => r.data)

export const getSecurities = (search?: string) =>
  api.get('/market-data/securities', { params: search ? { search } : {} }).then(r => r.data)

export const searchTicker = (q: string) =>
  api.get('/market-data/search-ticker', { params: { q } }).then(r => r.data)

export const lookupTicker = (symbol: string) =>
  api.get('/market-data/lookup-ticker', { params: { symbol } }).then(r => r.data)

export const getPriceHistory = (securityId: number, fromDate = '2020-01-01') =>
  api.get('/market-data/price-history', { params: { security_id: securityId, from_date: fromDate } }).then(r => r.data)

export const getPriceAnomalies = (thresholdPct = 100) =>
  api.get('/market-data/price-anomalies', { params: { threshold_pct: thresholdPct } }).then(r => r.data)

export const getCurrencyDetail = (currencyId: number, quote?: string) =>
  api.get(`/market-data/currencies/${currencyId}/detail`, { params: { quote } }).then(r => r.data as CurrencyDetail)

// Rate history of one currency in the quote (reporting) currency, converted through the
// storage base — unlike getFxRates, which returns the stored rates themselves.
// Support / resistance levels of the series a chart plots — see database/technical.py.
export type SrSource = { kind: 'security' | 'currency' | 'currency_stored'; id: number; quote?: string }
export const getTechnicalLevels = (src: SrSource, fromDate: string) =>
  api.get('/market-data/technical/levels', { params: { kind: src.kind, id: src.id, quote: src.quote, from_date: fromDate } })
    .then(r => r.data as import('@/lib/technical').SrLevel[])

export const getCurrencyHistory = (currencyId: number, quote?: string, fromDate = '1900-01-01') =>
  api.get(`/market-data/currencies/${currencyId}/history`, { params: { quote, from_date: fromDate } })
    .then(r => r.data as { date: string; rate: number }[])

export const FX_EFFECT_PERIODS = ['DTD', 'WTD', 'MTD', 'QTD', 'YTD', '1Y', '3Y', '5Y', 'All'] as const
export type FxEffectPeriod = typeof FX_EFFECT_PERIODS[number]
export type FxEffectPosition = {
  accounts_id: number; account: string; account_type: string; securities_id: number; name: string; ticker: string | null
  status: 'open' | 'closed'; quantity: number; value_eur: number
  realized_market: number; realized_fx: number; unrealized_market: number; unrealized_fx: number; income: number
  from_cost: boolean
}
export const getCurrencyFxEffect = (currencyId: number, period: FxEffectPeriod) =>
  api.get(`/market-data/currencies/${currencyId}/fx-effect`, { params: { period } })
    .then(r => r.data as { period: string; start: string | null; positions: FxEffectPosition[]; cash_balance: number; cash_fx: number | null })

export type CurrencyDetail = {
  id: number; code: string; name: string
  storage_base: string; is_storage_base: boolean; quote: string; is_quote: boolean
  latest_rate: number | null; rate_date: string | null; stored_rate: number | null; price_records: number
  changes: Partial<Record<'1D' | '1M' | 'YTD' | '1Y', number | null>>
  interest_rates: { id: number; code: string; name: string; rate_type: string; date: string | null; rate_pct: number | null }[]
  exposure: {
    cash_native: number; cash_eur: number; securities_native: number; securities_eur: number
    total_native: number; total_eur: number; share_pct: number | null; sensitivity_5pct_eur: number
  }
  cash_fx_effect: { '1D': number | null; YTD: number | null }
  accounts: { id: number; name: string; type: string; balance: number; balance_eur: number }[]
  holdings: {
    securities_id: number; name: string; ticker: string | null; type: string; accounts_id: number; account: string; account_type: string
    quantity: number; price: number | null; value: number; value_eur: number
  }[]
}

export const getFxRates = (currencyId?: number, fromDate = '2020-01-01') =>
  api.get('/market-data/fx-rates', { params: { currency_id: currencyId, from_date: fromDate } }).then(r => r.data)

export const refreshPrices = () =>
  api.post('/market-data/refresh-prices').then(r => r.data)

export const refreshFx = (period?: string, currencyId?: number) =>
  api.post('/market-data/refresh-fx', { ...(period ? { period } : {}), ...(currencyId ? { currency_id: currencyId } : {}) }).then(r => r.data)

export const downloadYahooInfo = (securityId?: number) =>
  api.post('/market-data/download/yahoo-info', securityId ? { security_id: securityId } : {}).then(r => r.data)

export const downloadYahooDividends = (securityId?: number) =>
  api.post('/market-data/download/yahoo-dividends', securityId ? { security_id: securityId } : {}).then(r => r.data)

export const downloadStockSplits = (securityId?: number) =>
  api.post('/market-data/download/stock-splits', securityId ? { security_id: securityId } : {}).then(r => r.data)

export const downloadFundComposition = (securityId?: number) =>
  api.post('/market-data/download/fund-composition', securityId ? { security_id: securityId } : {}).then(r => r.data)

export const downloadMorningstar = (securityId?: number) =>
  api.post('/market-data/download/morningstar', securityId ? { security_id: securityId } : {}).then(r => r.data)

export const downloadFundamentals = (securityId?: number) =>
  api.post('/market-data/download/fundamentals', securityId ? { security_id: securityId } : {}).then(r => r.data)

export const downloadYahooPrices = (period: string, securityId?: number) =>
  api.post('/market-data/download/yahoo-prices', { period, ...(securityId ? { security_id: securityId } : {}) }).then(r => r.data)

export const getShillerCape = (years?: number) =>
  api.get('/market-data/shiller-cape', { params: { years } }).then(r => r.data)

// Market Watch: hyperscaler FCF, credit spreads, the 10-year yield, market breadth (api/routers/market_watch.py)
export const getMarketWatch = (refresh = false) =>
  api.get('/market-watch', { params: refresh ? { refresh: 1 } : undefined, timeout: 120000 }).then(r => r.data)
export type MarketWatchEvent = { at: string; indicator: string; from: string; to: string; message: string }
export const getMarketWatchEvents = () => api.get('/market-watch/events').then(r => r.data as MarketWatchEvent[])
export const getMarketWatchPlaybook = () => api.get('/market-watch/playbook').then(r => r.data as { text: string; is_default: boolean })
export const saveMarketWatchPlaybook = (text: string) => api.put('/market-watch/playbook', { text }).then(r => r.data)
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type MarketWatchBacktest = Record<string, any>
export const getMarketWatchBacktest = () => api.get('/market-watch/backtest').then(r => r.data as MarketWatchBacktest | null)
export const getMarketWatchBacktestHistory = () => api.get('/market-watch/backtest/history').then(r => r.data as MarketWatchBacktest[])
export const runMarketWatchBacktest = () => api.post('/market-watch/backtest', {}, { timeout: 180000 }).then(r => r.data as MarketWatchBacktest)
// Telegram alerts (api/routers/notify.py)
export const getNotifyStatus = () => api.get('/notify/status').then(r => r.data as { configured: boolean; enabled: boolean })
export const sendNotifyTest = () => api.post('/notify/test').then(r => r.data)
// Local Market Watch — the country of usage's own health check (api/routers/local_watch.py)
export const getLocalWatch = (refresh = false) => api.get('/local-watch', { params: refresh ? { refresh: 1 } : undefined, timeout: 120000 }).then(r => r.data)
export const getLocalWatchSummary = () => api.get('/local-watch/summary', { timeout: 120000 }).then(r => r.data)
export const getLocalWatchEvents = () => api.get('/local-watch/events').then(r => r.data as MarketWatchEvent[])
export const getLocalWatchExposure = (accountIds?: number[]) => api.get('/local-watch/exposure', { params: { account_ids: accountIds?.join(',') || undefined } }).then(r => r.data)
export const getLocalWatchBacktest = () => api.get('/local-watch/backtest').then(r => r.data as MarketWatchBacktest | null)
export const getLocalWatchBacktestHistory = () => api.get('/local-watch/backtest/history').then(r => r.data as MarketWatchBacktest[])
export const runLocalWatchBacktest = () => api.post('/local-watch/backtest', {}, { timeout: 180000 }).then(r => r.data as MarketWatchBacktest)
export const getLocalWatchPlaybook = () => api.get('/local-watch/playbook').then(r => r.data as { text: string; is_default: boolean })
export const saveLocalWatchPlaybook = (text: string) => api.put('/local-watch/playbook', { text }).then(r => r.data)
export const getMarketWatchSummary = () => api.get('/market-watch/summary', { timeout: 120000 }).then(r => r.data)
export const getShillerCapeSummary = () =>
  api.get('/market-data/shiller-cape/summary').then(r => r.data)

export const downloadShillerCape = () =>
  api.post('/market-data/download/shiller-cape').then(r => r.data)

export const getInterestRates = (series?: string, years?: number) =>
  api.get('/market-data/rates', { params: { series, years } }).then(r => r.data)

export const getRateSeriesDefs = () =>
  api.get('/market-data/rates/series').then(r => r.data)

export const saveRateSeries = (data: Record<string, unknown>) =>
  api.post('/market-data/rates/series', data).then(r => r.data)

export const deleteRateSeries = (id: number) =>
  api.delete(`/market-data/rates/series/${id}`).then(r => r.data)

export const addRateValue = (data: { series_id: number; date: string; rate: number }) =>
  api.post('/market-data/rates/values', data).then(r => r.data)

export const getRateFundDurations = () =>
  api.get('/market-data/rates/fund-durations').then(r => r.data as Record<string, number>)

export const getRateTracking = () =>
  api.get('/market-data/rates/tracking').then(r => r.data)

export const saveRateTracking = (data: Record<string, unknown>) =>
  api.post('/market-data/rates/tracking', data).then(r => r.data)

export const deleteRateTracking = (id: number) =>
  api.delete(`/market-data/rates/tracking/${id}`).then(r => r.data)

export const getInterestRatesSummary = () =>
  api.get('/market-data/rates/summary').then(r => r.data)

export const downloadInterestRates = () =>
  api.post('/market-data/download/rates').then(r => r.data)

export const downloadCountryCapeRatios = () =>
  api.post('/market-data/download/country-cape').then(r => r.data)

export const getCountryCapeRatios = () =>
  api.get('/market-data/country-cape').then(r => r.data)

export const upsertCountryCapeRatio = (data: { id?: number; country: string; as_of_date: string; cape_ratio: number; source?: string }) =>
  api.post('/market-data/country-cape', data).then(r => r.data)

export const deleteCountryCapeRatio = (id: number) =>
  api.delete(`/market-data/country-cape/${id}`).then(r => r.data)

export const downloadTvInfo = (securityId?: number, overwrite = false) =>
  api.post('/market-data/download/tv-info', { overwrite, ...(securityId ? { security_id: securityId } : {}) }).then(r => r.data)

export const downloadIsin = (securityId?: number) =>
  api.post('/market-data/download/isin', securityId ? { security_id: securityId } : {}).then(r => r.data)

export const downloadTvPrices = (period: string, securityId?: number) =>
  api.post('/market-data/download/tv-prices', { period, ...(securityId ? { security_id: securityId } : {}) }).then(r => r.data)

export const downloadSolidusBonds = (securityId?: number) =>
  api.post('/market-data/download/solidus-bonds', securityId ? { security_id: securityId } : {}).then(r => r.data)

export const getWatchlist = () =>
  api.get('/market-data/watchlist').then(r => r.data)

export const upsertWatchlistItem = (data: Record<string, unknown>) =>
  api.post('/market-data/watchlist', data).then(r => r.data)

export const deleteWatchlistItem = (id: number) =>
  api.delete(`/market-data/watchlist/${id}`).then(r => r.data)

// ── News ─────────────────────────────────────────────────────────────────────
export const getNews = (params?: { source_type?: string; source_id?: number; unread_only?: boolean; limit?: number }) =>
  api.get('/news', { params }).then(r => r.data)

export const searchNews = (q: string, limit = 15) =>
  api.get('/news/search', { params: { q, limit } }).then(r => r.data)

export const generateNews = () =>
  api.post('/news/generate').then(r => r.data)

export const markNewsRead = (id: number, isRead = true) =>
  api.patch(`/news/${id}/read`, { is_read: isRead }).then(r => r.data)

export const getAlertsDefinitions = () =>
  api.get('/market-data/alerts').then(r => r.data)

export const saveAlert = (data: Record<string, unknown>) =>
  api.post('/market-data/alerts', data).then(r => r.data)

export const toggleAlert = (id: number, is_active: boolean) =>
  api.patch(`/market-data/alerts/${id}/toggle`, { is_active }).then(r => r.data)

export const deleteAlert = (id: number) =>
  api.delete(`/market-data/alerts/${id}`).then(r => r.data)

export const addPrice = (data: { security_id: number; date: string; close: number }) =>
  api.post('/market-data/prices', data).then(r => r.data)

export const deletePrice = (security_id: number, date: string) =>
  api.delete('/market-data/prices', { params: { security_id, date } }).then(r => r.data)

export const deletePricesBulk = (security_id: number, dates: string[]) =>
  api.delete('/market-data/prices/bulk', { data: { security_id, dates } }).then(r => r.data)

export const addFxRate = (data: { currency_id: number; date: string; rate: number }) =>
  api.post('/market-data/fx', data).then(r => r.data)

export const deleteFxRate = (currency_id: number, date: string) =>
  api.delete('/market-data/fx', { params: { currency_id, date } }).then(r => r.data)

// ── Recurring Templates ───────────────────────────────────────────────────────
export const getRecurringTemplates = () =>
  api.get('/recurring/templates').then(r => r.data)

export type CardPaymentPreview = {
  error?: string; amount?: number; owed?: number; statement_date?: string; statement_day?: number
  balance_at_statement?: number; credits_since?: number; issued?: boolean
}
export const getCardPaymentPreview = (cardId: number, dueDate: string) =>
  api.get('/recurring/card-payment-preview', { params: { card_id: cardId, due_date: dueDate } }).then(r => r.data as CardPaymentPreview)

export const getTemplateSplits = (id: number) =>
  api.get(`/recurring/templates/${id}/splits`).then(r => r.data)

export const createRecurringTemplate = (data: Record<string, unknown>) =>
  api.post('/recurring/templates', data).then(r => r.data)

export const updateRecurringTemplate = (id: number, data: Record<string, unknown>) =>
  api.put(`/recurring/templates/${id}`, data).then(r => r.data)

export const deleteRecurringTemplate = (id: number) =>
  api.delete(`/recurring/templates/${id}`).then(r => r.data)

export const runRecurringTemplate = (id: number) =>
  api.post(`/recurring/templates/${id}/run`).then(r => r.data)

export const getRecurringDrafts = () =>
  api.get('/recurring/drafts').then(r => r.data)

export const generateRecurringDrafts = () =>
  api.post('/recurring/generate-drafts').then(r => r.data)

export const updateRecurringDraft = (id: number, data: object) =>
  api.put(`/recurring/drafts/${id}`, data).then(r => r.data)

export const confirmRecurringDraft = (id: number) =>
  api.post(`/recurring/drafts/${id}/confirm`).then(r => r.data)

export const deleteRecurringDraft = (id: number) =>
  api.delete(`/recurring/drafts/${id}`).then(r => r.data)

export const upsertRecurringTemplate = (data: Record<string, unknown>) =>
  api.post('/recurring/templates', data).then(r => r.data)

export const getRecentTransactionsForTemplate = (months = 24) =>
  api.get('/recurring/recent-transactions', { params: { months } }).then(r => r.data)

export const createTemplateFromTransaction = (txId: number) =>
  api.post(`/recurring/templates/from-transaction/${txId}`).then(r => r.data)

// ── Importers ─────────────────────────────────────────────────────────────────
export const importFile = (source: string, formData: FormData) =>
  api.post(`/importers/${source}`, formData, { headers: { 'Content-Type': 'multipart/form-data' } }).then(r => r.data)

// ── AI Assistant ──────────────────────────────────────────────────────────────
export const askAI = (question: string) =>
  api.post('/ai/ask', { question }).then(r => r.data)

// ── Security Detail ───────────────────────────────────────────────────────────
export const getSecurityTransactions = (secId: number) =>
  api.get(`/securities/${secId}/transactions`).then(r => r.data)

export const getSecurityHoldings = (secId: number) =>
  api.get(`/securities/${secId}/holdings`).then(r => r.data)

export const getSecurityFundComposition = (secId: number) =>
  api.get(`/securities/${secId}/fund-composition`).then(r => r.data)

export const getSecurityFundMembership = (secId: number) =>
  api.get(`/securities/${secId}/fund-membership`).then(r => r.data)

export const setSecurityCategoryOverride = (secId: number, categoryOverride: string | null) =>
  api.patch(`/securities/${secId}/fund-composition/category-override`, { category_override: categoryOverride }).then(r => r.data)

export const setSecurityMorningstarCategory = (secId: number, category: string | null) =>
  api.patch(`/securities/${secId}/fund-composition/morningstar-category-override`, { category }).then(r => r.data)

export const setSecurityAssetClassOverride = (secId: number, assetClassOverride: string | null) =>
  api.patch(`/securities/${secId}/fund-composition/asset-class-override`, { asset_class_override: assetClassOverride }).then(r => r.data)

// expenseRatioOverride is a percentage (e.g. 0.20 for 0.20%), matching how the Expense
// Ratio report displays it — the backend stores it as the same fraction Yahoo's own
// Expense_Ratio_Pct uses.
export const setSecurityExpenseRatioOverride = (secId: number, expenseRatioOverride: number | null) =>
  api.patch(`/securities/${secId}/fund-composition/expense-ratio-override`, { expense_ratio_override: expenseRatioOverride }).then(r => r.data)

// Generic manual override for any other Fund_Composition scalar field (family, legal
// type, total net assets, holdings turnover, bond duration/maturity, equity P/E-P/B-
// P/S-P/CF, median market cap, 3yr earnings growth). `field` is the snake_case key the
// backend's _OVERRIDABLE_FIELDS map recognizes (e.g. 'bond_duration'); `value` is a
// percentage for the "_pct" fields, raw otherwise — pass null to clear the override.
export const setSecurityFieldOverride = (secId: number, field: string, value: number | string | null) =>
  api.patch(`/securities/${secId}/fund-composition/field-override`, { field, value }).then(r => r.data)

// Manual Asset Mix — value is { cash, stock, bond, preferred, convertible, other } in percent, adding up to 100; null clears it.
export const setSecurityAssetMixOverride = (secId: number, value: Record<string, number> | null) =>
  api.patch(`/securities/${secId}/fund-composition/asset-mix-override`, { value }).then(r => r.data)

// Manual override for the Sector Weightings / Bond Ratings breakdown — value is a
// {bucket: percentage} map (e.g. { technology: 25 } for 25%); pass {} or null to clear.
export const setSecurityBreakdownOverride = (secId: number, field: 'sector_weightings' | 'bond_ratings', value: Record<string, number | string> | null) =>
  api.patch(`/securities/${secId}/fund-composition/breakdown-override`, { field, value }).then(r => r.data)

// weight_pct is a percentage (e.g. 5.2 for 5.2%). A manually added/edited row is never
// touched by a future "Download Fund Composition" re-run.
export const addSecurityTopHolding = (secId: number, data: { symbol: string; holding_name?: string | null; weight_pct: number }) =>
  api.post(`/securities/${secId}/fund-top-holdings`, data).then(r => r.data)
export const updateSecurityTopHolding = (secId: number, holdingId: number, data: { symbol: string; holding_name?: string | null; weight_pct: number }) =>
  api.patch(`/securities/${secId}/fund-top-holdings/${holdingId}`, data).then(r => r.data)
export const deleteSecurityTopHolding = (secId: number, holdingId: number) =>
  api.delete(`/securities/${secId}/fund-top-holdings/${holdingId}`).then(r => r.data)

export const getSecurityDividends = (secId: number) =>
  api.get(`/securities/${secId}/dividends`).then(r => r.data)

export const createSecurityDividend = (secId: number, data: Record<string, unknown>) =>
  api.post(`/securities/${secId}/dividends`, data).then(r => r.data)

export const updateSecurityDividend = (secId: number, dividendId: number, data: Record<string, unknown>) =>
  api.put(`/securities/${secId}/dividends/${dividendId}`, data).then(r => r.data)

export const deleteSecurityDividend = (secId: number, dividendId: number) =>
  api.delete(`/securities/${secId}/dividends/${dividendId}`).then(r => r.data)

export const deleteSecurityDividendsBulk = (secId: number, ids: number[]) =>
  api.delete(`/securities/${secId}/dividends/bulk`, { data: { ids } }).then(r => r.data)

export const getSecurityCorporateActions = (secId: number) =>
  api.get(`/securities/${secId}/corporate-actions`).then(r => r.data)

export const createCorporateAction = (secId: number, data: Record<string, unknown>) =>
  api.post(`/securities/${secId}/corporate-actions`, data).then(r => r.data)

export const updateCorporateAction = (secId: number, caId: number, data: Record<string, unknown>) =>
  api.put(`/securities/${secId}/corporate-actions/${caId}`, data).then(r => r.data)

export const deleteCorporateAction = (secId: number, caId: number) =>
  api.delete(`/securities/${secId}/corporate-actions/${caId}`).then(r => r.data)

export const previewCorporateAction = (secId: number, data: Record<string, unknown>) =>
  api.post(`/securities/${secId}/corporate-actions/preview`, data).then(r => r.data)

export const executeCorporateAction = (secId: number, data: Record<string, unknown>) =>
  api.post(`/securities/${secId}/corporate-actions/execute`, data).then(r => { invalidatePnl(); return r.data })

export const applySplitCorporateAction = (secId: number, caId: number) =>
  api.post(`/securities/${secId}/corporate-actions/${caId}/apply-split`).then(r => { invalidatePnl(); return r.data })

export const getSecurityPriceAnomalies = (secId: number, thresholdPct = 100) =>
  api.get(`/securities/${secId}/price-anomalies`, { params: { threshold_pct: thresholdPct } }).then(r => r.data)

export const deleteSecurityPrice = (secId: number, date: string) =>
  api.delete(`/securities/${secId}/prices/${date}`).then(r => r.data)

// ── Bank Import ───────────────────────────────────────────────────────────────
export const getBankAccounts = () => api.get('/bank/accounts').then(r => r.data)
export const getAllAccounts = () => api.get('/bank/all-accounts').then(r => r.data)
export const getImportProfiles = () => api.get('/bank/import-profiles').then(r => r.data)
export const createImportProfile = (data: Record<string, unknown>) => api.post('/bank/import-profiles', data).then(r => r.data)
export const deleteImportProfile = (id: number) => api.delete(`/bank/import-profiles/${id}`).then(r => r.data)
export const getPayeeRules = () => api.get('/bank/payee-rules').then(r => r.data)
export const createPayeeRule = (data: Record<string, unknown>) => api.post('/bank/payee-rules', data).then(r => r.data)
export const updatePayeeRule = (id: number, data: Record<string, unknown>) => api.put(`/bank/payee-rules/${id}`, data).then(r => r.data)
export const deletePayeeRule = (id: number) => api.delete(`/bank/payee-rules/${id}`).then(r => r.data)
export const getBankPayees = () => api.get('/bank/payees').then(r => r.data)
export const getBankCategories = () => api.get('/bank/categories').then(r => r.data)
export const getPayeeCategoryUsage = () => api.get('/bank/payee-category-usage').then(r => r.data)
export const parseStatement = (profileId: number, file: File) => {
  const fd = new FormData(); fd.append('file', file)
  return api.post('/bank/parse-statement', fd, { params: { profile_id: profileId }, headers: { 'Content-Type': 'multipart/form-data' } }).then(r => r.data)
}
export const getAppTransactions = (accountId: number, dateFrom: string, dateTo: string) =>
  api.get('/bank/app-transactions', { params: { account_id: accountId, date_from: dateFrom, date_to: dateTo } }).then(r => r.data)
export const applyBankImport = (data: Record<string, unknown>) => api.post('/bank/apply-import', data).then(r => r.data)
export const getReconciliationHistoryAccounts = () => api.get('/bank/reconciliation-history-accounts').then(r => r.data)
export const getReconciliationHistory = (accountId: number) => api.get(`/bank/reconciliation-history/${accountId}`).then(r => r.data)
export const saveSecurityMappings = (source: string, mappings: Record<string, number>) =>
  api.post('/bank/save-security-mappings', { source, mappings }).then(r => r.data)

export const ibFlexFetch = (token: string, queryId: string, forceRefresh = false) =>
  api.post('/bank/ib-flex-fetch', { token, query_id: queryId, force_refresh: forceRefresh }).then(r => r.data)
export const ibFlexParse = (xml: string, accountId: number, cashAccountId?: number) =>
  api.post('/bank/ib-flex-parse', { xml, account_id: accountId, cash_account_id: cashAccountId ?? null }).then(r => r.data)
export const ibFlexImport = (data: Record<string, unknown>) => api.post('/bank/ib-flex-import', data).then(r => r.data)
export const revtParse = (file: File, accountId: number) => {
  const fd = new FormData(); fd.append('file', file)
  return api.post('/bank/revolut-trading-parse', fd, { params: { account_id: accountId }, headers: { 'Content-Type': 'multipart/form-data' } }).then(r => r.data)
}
export const revtImport = (file: File, accountId: number, replaceMode: boolean, importInv: boolean, importTx: boolean, selectedInv?: string[], selectedTx?: string[]) => {
  const fd = new FormData(); fd.append('file', file)
  return api.post('/bank/revolut-trading-import', fd, {
    params: {
      account_id: accountId, replace_mode: replaceMode, import_inv: importInv, import_tx: importTx,
      selected_inv: selectedInv ? JSON.stringify(selectedInv) : undefined,
      selected_tx: selectedTx ? JSON.stringify(selectedTx) : undefined,
    },
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then(r => r.data)
}

export const revsParse = (file: File, accountId: number, mode: 'inv' | 'tx') => {
  const fd = new FormData(); fd.append('file', file)
  return api.post('/bank/revolut-savings-parse', fd, { params: { account_id: accountId, mode }, headers: { 'Content-Type': 'multipart/form-data' } }).then(r => r.data)
}
export const revsImport = (file: File, accountId: number, mode: 'inv' | 'tx', replaceMode: boolean) => {
  const fd = new FormData(); fd.append('file', file)
  return api.post('/bank/revolut-savings-import', fd, { params: { account_id: accountId, mode, replace_mode: replaceMode }, headers: { 'Content-Type': 'multipart/form-data' } }).then(r => r.data)
}

export const capitalcomParse = (trades: File, funds: File, accountId: number, includeSwaps: boolean, includeDividends: boolean) => {
  const fd = new FormData(); fd.append('trades', trades); fd.append('funds', funds)
  return api.post('/bank/capitalcom-parse', fd, {
    params: { account_id: accountId, include_swaps: includeSwaps, include_dividends: includeDividends },
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then(r => r.data)
}
export const capitalcomImport = (trades: File, funds: File, accountId: number, includeSwaps: boolean, includeDividends: boolean, replaceMode: boolean, selectedInv?: string[], selectedTx?: string[]) => {
  const fd = new FormData(); fd.append('trades', trades); fd.append('funds', funds)
  return api.post('/bank/capitalcom-import', fd, {
    params: {
      account_id: accountId, include_swaps: includeSwaps, include_dividends: includeDividends, replace_mode: replaceMode,
      selected_inv: selectedInv ? JSON.stringify(selectedInv) : undefined,
      selected_tx: selectedTx ? JSON.stringify(selectedTx) : undefined,
    },
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then(r => r.data)
}

export const fxproParse = (file: File, accountId: number) => {
  const fd = new FormData(); fd.append('files', file)
  return api.post('/bank/fxpro-parse', fd, { params: { account_id: accountId }, headers: { 'Content-Type': 'multipart/form-data' } }).then(r => r.data)
}
export const fxproImport = (file: File, accountId: number, replaceMode: boolean, selectedInv?: string[], selectedTx?: string[]) => {
  const fd = new FormData(); fd.append('files', file)
  return api.post('/bank/fxpro-import', fd, {
    params: {
      account_id: accountId, replace_mode: replaceMode,
      selected_inv: selectedInv ? JSON.stringify(selectedInv) : undefined,
      selected_tx: selectedTx ? JSON.stringify(selectedTx) : undefined,
    },
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then(r => r.data)
}

// ── Saxo Bank ─────────────────────────────────────────────────────────────────
export const saxoGetSettings = () => api.get('/bank/saxo-settings').then(r => r.data)
export const saxoSaveAccountMap = (accountMap: Record<string, number>) =>
  api.post('/bank/saxo-save-account-map', { account_map: accountMap }).then(r => r.data)
export const saxoSaveChargePayee = (payeeId: number | null) =>
  api.post('/bank/saxo-save-charge-payee', { payee_id: payeeId }).then(r => r.data)
export type SaxoSession = { connected: boolean; access_token?: string; expires_at?: number; use_sim?: boolean; status: string; reason?: string; transient?: boolean }
export const saxoSession = (force = false) =>
  api.get('/bank/saxo-session', { params: force ? { force: 1 } : undefined }).then(r => r.data as SaxoSession)
export const saxoSaveCredentials = (data: { app_key: string; app_secret: string; use_sim: boolean }) =>
  api.post('/bank/saxo-save-credentials', data).then(r => r.data)
export const saxoGetAuthUrl = (appKey: string, appSecret: string, redirectUri: string, useSim: boolean) =>
  api.post('/bank/saxo-auth-url', { app_key: appKey, app_secret: appSecret, redirect_uri: redirectUri, use_sim: useSim }).then(r => r.data)
export const saxoExchangeCode = (data: { app_key: string; app_secret: string; code: string; redirect_uri: string; use_sim: boolean; remember: boolean }) =>
  api.post('/bank/saxo-exchange-code', data).then(r => r.data)
export const saxoRefreshToken = (data: { app_key: string; app_secret: string; refresh_token: string; use_sim: boolean }) =>
  api.post('/bank/saxo-refresh-token', data).then(r => r.data)
export const saxoFetchAccounts = (accessToken: string, useSim: boolean) =>
  api.post('/bank/saxo-fetch-accounts', { access_token: accessToken, use_sim: useSim }).then(r => r.data)
export const saxoFetchTrades = (data: object) => api.post('/bank/saxo-fetch-trades', data).then(r => r.data)
export const saxoImport = (data: object) => api.post('/bank/saxo-import', data).then(r => r.data)
export const saxoPdfPreview = (file: File, accountIdOverride = '') => {
  const fd = new FormData(); fd.append('file', file)
  return api.post('/bank/saxo-pdf-preview', fd, { params: { account_id_override: accountIdOverride }, headers: { 'Content-Type': 'multipart/form-data' } }).then(r => r.data)
}
export const saxoPdfImport = (file: File, accountId: number, accountIdSaxo = '', replaceMode = false) => {
  const fd = new FormData(); fd.append('file', file)
  return api.post('/bank/saxo-pdf-import', fd, { params: { account_id: accountId, account_id_saxo: accountIdSaxo, replace_mode: replaceMode }, headers: { 'Content-Type': 'multipart/form-data' } }).then(r => r.data)
}

// ── QIF Importer ────────────────────────────────────────────────────────────
export const getQifOptions = () => api.get('/bank/qif-options').then(r => r.data)

export const qifParse = (file: File) => {
  const fd = new FormData(); fd.append('file', file)
  return api.post('/bank/qif-parse', fd, { headers: { 'Content-Type': 'multipart/form-data' } }).then(r => r.data)
}

export type QifAccountAction = {
  action: 'skip' | 'map' | 'create'
  oikos_account_id?: number | null
  new_name?: string | null
  new_type?: string | null
}

export const qifImport = (
  file: File,
  tablesToClear: string[],
  importOptions: Record<string, boolean>,
  excludeBankAccountIds: number[] | null,
  excludeInvAccountIds: number[] | null,
  saveExclusions: boolean,
  accountMap: Record<string, QifAccountAction>,
  dateFrom: string | null,
  dateTo: string | null,
) => {
  const fd = new FormData(); fd.append('file', file)
  return api.post('/bank/qif-import', fd, {
    params: {
      tables_to_clear: JSON.stringify(tablesToClear),
      import_options: JSON.stringify(importOptions),
      exclude_bank_account_ids: excludeBankAccountIds ? JSON.stringify(excludeBankAccountIds) : undefined,
      exclude_inv_account_ids: excludeInvAccountIds ? JSON.stringify(excludeInvAccountIds) : undefined,
      save_exclusions: saveExclusions,
      account_map: JSON.stringify(accountMap),
      date_from: dateFrom ?? undefined,
      date_to: dateTo ?? undefined,
    },
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then(r => r.data)
}
export const qifClearImpact = (
  tablesToClear: string[],
  excludeBankAccountIds: number[] | null,
  excludeInvAccountIds: number[] | null,
) => api.post('/bank/qif-clear-impact', {
  tables_to_clear: tablesToClear,
  exclude_bank_account_ids: excludeBankAccountIds,
  exclude_inv_account_ids: excludeInvAccountIds,
}).then(r => r.data)

// ── Coinbase ──────────────────────────────────────────────────────────────────
export const getImporterSettings = (key: string) => api.get(`/bank/importer-settings/${key}`).then(r => r.data)
export const saveImporterSettings = (key: string, data: Record<string, unknown>) => api.post(`/bank/importer-settings/${key}`, data).then(r => r.data)

export const coinbaseGetSettings = () => api.get('/bank/coinbase-settings').then(r => r.data)
export const coinbaseTest = (data: object) => api.post('/bank/coinbase-test', data).then(r => r.data)
export const coinbaseFetch = (data: object) => api.post('/bank/coinbase-fetch', data).then(r => r.data)
export const coinbaseImport = (data: object) => api.post('/bank/coinbase-import', data).then(r => r.data)

// ── Crypto.com ────────────────────────────────────────────────────────────────
export const cryptocomGetSettings = () => api.get('/bank/cryptocom-settings').then(r => r.data)
export const cryptocomTest = (data: object) => api.post('/bank/cryptocom-test', data).then(r => r.data)
export const cryptocomFetch = (data: object) => api.post('/bank/cryptocom-fetch', data).then(r => r.data)
export const cryptocomImport = (data: object) => api.post('/bank/cryptocom-import', data).then(r => r.data)

// ── Tools ─────────────────────────────────────────────────────────────────────
export const runVacuum = () => api.post('/tools/vacuum').then(r => r.data)
export const getBackupDbInfo = () => api.get('/tools/backup/db-info').then(r => r.data)
export const runBackup = (params?: { custom_name?: string; exclude_blobs?: boolean }) =>
  api.post('/tools/backup', null, { params }).then(r => r.data)
export const listBackups = () => api.get('/tools/backup/list').then(r => r.data)
export const downloadBackupUrl = (filename: string) => `/api/tools/backup/download/${encodeURIComponent(filename)}`
export const deleteBackup = (filename: string) => api.delete(`/tools/backup/${encodeURIComponent(filename)}`).then(r => r.data)
export const restoreBackup = (filename: string) => api.post(`/tools/backup/restore/${encodeURIComponent(filename)}`).then(r => r.data)
export const restoreBackupUpload = (file: File) => {
  const form = new FormData(); form.append('file', file)
  return api.post('/tools/backup/restore-upload', form).then(r => r.data)
}
export const getSchedulerStatus = () => api.get('/tools/scheduler-status').then(r => r.data)
export const runSql = (sql: string) => api.post('/tools/run-sql', { sql }).then(r => r.data)
export const getDbHealth = () => api.get('/tools/db-health').then(r => r.data)
export const runDbMaintenance = (operation: string, table?: string, dbName?: string) =>
  api.post('/tools/db-maintenance', { operation, table, db_name: dbName }).then(r => r.data)
export const toolsSyncBalances = (target: string) => api.post('/tools/sync-balances', { target }).then(r => r.data)
export const getReferentialIntegrity = () => api.get('/tools/referential-integrity').then(r => r.data)
export const exportExcel = () => api.get('/tools/export-excel', { responseType: 'blob' }).then(r => r.data)
export const getToolsPriceAnomalies = (threshold: number) =>
  api.get('/tools/price-anomalies', { params: { threshold } }).then(r => r.data)
export const deleteHistoricalPrices = (rows: {securities_id: number, date: string}[]) =>
  api.delete('/tools/historical-prices', { data: { rows } }).then(r => r.data)
export const getMissingTxPrices = () => api.get('/tools/missing-tx-prices').then(r => r.data)
export const insertMissingPrices = (rows: {securities_id: number, date: string, price: number}[]) =>
  api.post('/tools/insert-missing-prices', { rows }).then(r => r.data)
export const getDummyPrices = (tolerancePct: number) =>
  api.get('/tools/dummy-prices', { params: { tolerance_pct: tolerancePct } }).then(r => r.data)
export const normalizeInvestments = (ids: number[]) =>
  api.post('/tools/normalize-investments', { ids }).then(r => r.data)
export const refreshHoldings = () => api.post('/tools/refresh-holdings').then(r => r.data)
export const getInvestmentConsistency = (accountIds?: number[]) =>
  api.get('/tools/investment-consistency', { params: accountIds?.length ? { account_ids: accountIds.join(',') } : {} }).then(r => r.data)
export const updateInvestmentRow = (investments_id: number, fields: Record<string, number | null>) =>
  api.put('/tools/investment-row', { investments_id, fields }).then(r => r.data)
export const getMissingTransferMirrors = () => api.get('/tools/missing-transfer-mirrors').then(r => r.data)
export const fixTransferMirrors = (ids: number[]) =>
  api.post('/tools/fix-transfer-mirrors', { ids }).then(r => r.data)
export const getMissingSplitTransactions = () => api.get('/tools/missing-split-transactions').then(r => r.data)
export const fixSplitTransactions = (ids: number[]) =>
  api.post('/tools/fix-split-transactions', { ids }).then(r => r.data)
export const getSplitAmountMismatches = () => api.get('/tools/split-amount-mismatches').then(r => r.data)
export const fixSplitAmountMismatches = (ids: number[]) =>
  api.post('/tools/fix-split-amount-mismatches', { ids }).then(r => r.data)
export const getUnlinkedTransferPairs = () => api.get('/tools/unlinked-transfer-pairs').then(r => r.data)
export const linkTransferPairs = (pairs: Record<string, number>[]) =>
  api.post('/tools/link-transfer-pairs', { pairs }).then(r => r.data)
export const getTransferSignMismatches = () => api.get('/tools/transfer-sign-mismatches').then(r => r.data)
export const fixTransferSign = (txIds: number[], allAccIds: number[]) =>
  api.post('/tools/fix-transfer-sign', { tx_ids: txIds, all_acc_ids: allAccIds }).then(r => r.data)
export const getMissingInvCashLinks = () => api.get('/tools/missing-investment-cash-links').then(r => r.data)
export const fixInvCashLinks = (pairs: {investments_id: number, candidate_tx_id: number}[]) =>
  api.post('/tools/fix-investment-cash-links', { pairs }).then(r => r.data)
export const getMissingInvAccountTarget = () => api.get('/tools/missing-inv-account-target').then(r => r.data)
export const fixInvAccountTarget = () => api.post('/tools/fix-inv-account-target').then(r => r.data as { updated: number })
export const getDuplicateInvCashLinks = () => api.get('/tools/duplicate-investment-cash-links').then(r => r.data)
export const fixDuplicateInvCashLinks = (investmentsIds: number[]) =>
  api.post('/tools/fix-duplicate-investment-cash-links', { investments_ids: investmentsIds }).then(r => r.data as { fixed: number; errors: string[] })

export const importPricesFromFile = (file: File, securitiesId: number, onConflict: 'skip' | 'overwrite') => {
  const fd = new FormData()
  fd.append('file', file)
  fd.append('securities_id', String(securitiesId))
  fd.append('on_conflict', onConflict)
  return api.post('/tools/import-prices-from-file', fd).then(r => r.data as { inserted: number; skipped: number; total_rows: number })
}

export const importFxFromFile = (file: File, currencyId: number, onConflict: 'skip' | 'overwrite') => {
  const fd = new FormData()
  fd.append('file', file)
  fd.append('currency_id', String(currencyId))
  fd.append('on_conflict', onConflict)
  return api.post('/tools/import-fx-from-file', fd).then(r => r.data as { inserted: number; skipped: number; total_rows: number })
}
export const getLogs = (lines: number, level?: string, search?: string, file?: string) =>
  api.get('/tools/logs', { params: { lines, level, search, file } }).then(r => r.data)

export const getSchedulerJobs = () =>
  api.get('/tools/scheduler-jobs').then(r => r.data)

export const createSchedulerJob = (data: Record<string, unknown>) =>
  api.post('/tools/scheduler-jobs', data).then(r => r.data)

export const updateSchedulerJob = (jobId: string, data: Record<string, unknown>) =>
  api.put(`/tools/scheduler-jobs/${jobId}`, data).then(r => r.data)

export const deleteSchedulerJob = (jobId: string) =>
  api.delete(`/tools/scheduler-jobs/${jobId}`).then(r => r.data)

export const triggerSchedulerJob = (jobId: string) =>
  api.post(`/tools/run-scheduler-job/${jobId}`).then(r => r.data)

// ── Budgets (write) ───────────────────────────────────────────────────────────
export const upsertBudget = (data: Record<string, unknown>) =>
  api.post('/reports/budgets', data).then(r => r.data)

export const deleteBudget = (id: number) =>
  api.delete(`/reports/budgets/${id}`).then(r => r.data)

export const getNetWorthByAccount = (startDate: string, endDate: string, grouping: string) =>
  api.get('/reports/net-worth-by-account', { params: { start_date: startDate, end_date: endDate, grouping } }).then(r => r.data)

export const getInvestmentPositionsHistory = (startDate: string, accountIds?: number[]) =>
  api.get('/reports/investment-positions-history', { params: { start_date: startDate, account_ids: accountIds?.join(',') || undefined } }).then(r => r.data)
export const getHoldingsSnapshot = (asOf?: string, accountIds?: number[]) =>
  api.get('/reports/holdings-snapshot', { params: { ...(asOf ? { as_of: asOf } : {}), account_ids: accountIds?.join(',') || undefined } }).then(r => r.data)


export const getXraySectorWeighting = (accountIds?: number[], compareDate?: string) =>
  api.get('/reports/xray/sector-weighting', { params: { account_ids: accountIds?.join(',') || undefined, compare_date: compareDate || undefined } }).then(r => r.data)

export const getXrayAssetAllocation = (accountIds?: number[], compareDate?: string) =>
  api.get('/reports/xray/asset-allocation', { params: { account_ids: accountIds?.join(',') || undefined, compare_date: compareDate || undefined } }).then(r => r.data)

export const getXrayAssetAllocationTargets = () =>
  api.get('/reports/xray/asset-allocation-targets').then(r => r.data)
export const saveXrayAssetAllocationTargets = (payload: Record<string, number>) =>
  api.post('/reports/xray/asset-allocation-targets', payload).then(r => r.data)

export const getXrayStyleBox = (accountIds?: number[], compareDate?: string) =>
  api.get('/reports/xray/style-box', { params: { account_ids: accountIds?.join(',') || undefined, compare_date: compareDate || undefined } }).then(r => r.data)

// Exposure by country (Reports -> Inv. Portfolio -> Portfolio Analysis -> Country Exposure)
export type CountryExposureItem = { securities_id: number | null; account_id?: number | null; name: string; ticker: string; via: 'Direct' | 'Fund'; value_eur: number; pct: number }
export type CountryExposureCategory = { category: string; value_eur: number; pct: number; items: CountryExposureItem[] }
export type CountryExposureCountry = {
  code: string; name: string; value_eur: number; pct: number; special: boolean; categories: CountryExposureCategory[]
  // currency report only: rate to EUR, the amount in the currency itself, and the EUR impact of a 5% move in it
  rate?: number | null; native_value?: number | null; impact_5pct_eur?: number | null
}
export type CountryExposure = {
  total_eur: number; countries: CountryExposureCountry[]; notes: string[]
  uncovered_funds: { securities_id: number; name: string; ticker: string; value_eur: number }[]
  derived_funds?: string[]
}
export const getCountryExposure = (accountIds?: number[]) =>
  api.get('/reports/country-exposure', { params: { account_ids: accountIds?.join(',') || undefined } }).then(r => r.data as CountryExposure)
export type FundCountryRow = { kind: string; country: string; name?: string; weight_pct: number }
export type FundCountryExposure = {
  rows: FundCountryRow[]; source: string | null; as_of: string | null; total_pct: number; origin: string | null; commodity?: boolean
  asset_mix: { stock?: number | null; bond?: number | null; category?: string | null }
}
export const getFundCountryExposure = (secId: number) =>
  api.get(`/reports/country-exposure/funds/${secId}`).then(r => r.data as FundCountryExposure)
export const saveFundCountryExposure = (secId: number, body: { rows: FundCountryRow[]; source?: string; as_of?: string }) =>
  api.put(`/reports/country-exposure/funds/${secId}`, body).then(r => r.data)
// Currency exposure (same tree shape as the country report)
export const getCurrencyExposure = (accountIds?: number[], includeLiabilities = false) =>
  api.get('/reports/currency-exposure', { params: { account_ids: accountIds?.join(',') || undefined, include_liabilities: includeLiabilities || undefined } }).then(r => r.data as CountryExposure)
export type FundCurrencyExposure = { rows: { currency: string; weight_pct: number }[]; source: string | null; as_of: string | null; origin: string | null; total_pct: number }
export const getFundCurrencyExposure = (secId: number) =>
  api.get(`/reports/currency-exposure/funds/${secId}`).then(r => r.data as FundCurrencyExposure)
export const saveFundCurrencyExposure = (secId: number, body: { rows: { currency: string; weight_pct: number }[]; source?: string; as_of?: string }) =>
  api.put(`/reports/currency-exposure/funds/${secId}`, body).then(r => r.data)
export const clearFundCurrencyExposure = (secId: number) =>
  api.delete(`/reports/currency-exposure/funds/${secId}`).then(r => r.data)

export type FundCountrySource = { provider: string | null; url: string | null; kind: string | null }
export const getFundCountrySource = (secId: number) =>
  api.get(`/reports/country-exposure/funds/${secId}/source`).then(r => r.data as FundCountrySource)
export const setFundCountrySource = (secId: number, body: { url: string; kind?: string | null }) =>
  api.put(`/reports/country-exposure/funds/${secId}/source`, body).then(r => r.data)
// Refresh country rows from the providers: one fund, or every held fund. force also replaces rows typed in by hand.
export const downloadCountryExposure = (securityId?: number, force?: boolean) =>
  api.post('/reports/country-exposure/download', { ...(securityId ? { security_id: securityId } : {}), ...(force ? { force: true } : {}) }).then(r => r.data as { ok: boolean; message: string })
export const clearFundCountryExposure = (secId: number) =>
  api.delete(`/reports/country-exposure/funds/${secId}`).then(r => r.data)
export const parseCountryText = (text: string) =>
  api.post('/reports/country-exposure/parse', { text }).then(r => r.data as { rows: { country: string; name: string; weight_pct: number }[]; unrecognised: string[] })
export const getCountryList = () =>
  api.get('/reports/country-exposure/countries').then(r => r.data as { code: string; name: string }[])

export const getXrayBondQuality = (accountIds?: number[], compareDate?: string) =>
  api.get('/reports/xray/bond-quality', { params: { account_ids: accountIds?.join(',') || undefined, compare_date: compareDate || undefined } }).then(r => r.data)

export const getXrayStockOverlap = (accountIds?: number[], compareDate?: string) =>
  api.get('/reports/xray/stock-overlap', { params: { account_ids: accountIds?.join(',') || undefined, compare_date: compareDate || undefined } }).then(r => r.data)

export const getXrayExpenseRatio = (accountIds?: number[], compareDate?: string) =>
  api.get('/reports/xray/expense-ratio', { params: { account_ids: accountIds?.join(',') || undefined, compare_date: compareDate || undefined } }).then(r => r.data)

export const getSpendingByPayee = (startDate: string, endDate: string, topN = 20) =>
  api.get('/reports/spending-by-payee', { params: { start_date: startDate, end_date: endDate, top_n: topN } }).then(r => r.data)

export const getSpendingTrends = (months: number) =>
  api.get('/reports/spending-trends', { params: { months } }).then(r => r.data)

export const getSavingsRateDetail = (months: number) =>
  api.get('/reports/savings-rate-detail', { params: { months } }).then(r => r.data)

export const getTwr = (lookbackDays: number, accountIds?: number[]) =>
  api.get('/reports/twr', { params: { lookback_days: lookbackDays, account_ids: accountIds?.join(',') || undefined } }).then(r => r.data)

export const getRiskMetrics = (lookbackDays: number, benchmarkSecId?: number | null, accountIds?: number[]) =>
  api.get('/reports/risk-metrics', { params: { lookback_days: lookbackDays, benchmark_sec_id: benchmarkSecId ?? undefined, account_ids: accountIds?.join(',') || undefined } }).then(r => r.data)

export const getBenchmarkCandidates = (types?: string[], excludeId?: number) =>
  api.get('/reports/benchmark-candidates', { params: {
    types: types?.length ? types.join(',') : undefined,
    exclude_id: excludeId ?? undefined,
  } }).then(r => r.data)

export const getTaxLossHarvesting = (accountIds?: number[]) =>
  api.get('/reports/tax-loss-harvesting', { params: { account_ids: accountIds?.join(',') || undefined } }).then(r => r.data)

export interface TaxLossSummary {
  year: number; method: string
  categories: { category: string; display_name: string; rate: number; loss_eur: number; positions: number; gains_eur: number; capacity_eur: number; saving_eur: number; carry_forward_eur: number | null; note: string }[]
  exempt: { loss_eur: number; positions: number; categories: { category: string; loss_eur: number; positions: number }[] }
  totals: { taxable_loss_eur: number; saving_eur: number; exempt_loss_eur: number }
}
// `crypto` carries the Crypto tab's own settings, so the crypto figures here are the same ones that tab shows.
export interface CryptoSettings { rewardsIncome?: boolean | null; exemption?: number; lossesFrom?: number; rate?: number | null; excluded?: number[] }
export const getTaxLossHarvestingSummary = (year: number, method: string, crypto: CryptoSettings = {}, accountIds?: number[]): Promise<TaxLossSummary> =>
  api.get('/reports/tax-loss-harvesting/summary', { params: {
    year, method, account_ids: accountIds?.join(',') || undefined, rewards_income: crypto.rewardsIncome ?? undefined, exemption: crypto.exemption || undefined, losses_from: crypto.lossesFrom || undefined,
    crypto_rate: crypto.rate ?? undefined, exclude_account_ids: crypto.excluded?.join(',') || undefined } }).then(r => r.data)

export const getDividendIncomeTax = (year: number, accountIds?: number[]) =>
  api.get('/reports/dividend-income-tax', { params: { year, account_ids: accountIds?.join(',') || undefined } }).then(r => r.data)
export const getBankInterestTax = (year: number, accountIds?: number[]) =>
  api.get('/reports/bank-interest-tax', { params: { year, account_ids: accountIds?.join(',') || undefined } }).then(r => r.data)

export const getPriceChanges = () =>
  api.get('/reports/price-changes').then(r => r.data)

export const getPortfolioSignals = () =>
  api.get('/reports/portfolio-signals').then(r => r.data)

export const getFundamentalScores = () =>
  api.get('/reports/fundamental-scores').then(r => r.data)

export const getGoals = () =>
  api.get('/reports/goals').then(r => r.data)

export const upsertGoal = (data: object) =>
  api.post('/reports/goals', data).then(r => r.data)

export const deleteGoal = (id: number) =>
  api.delete(`/reports/goals/${id}`).then(r => r.data)

export const getSavingsAccounts = (period = 'All Time', startDate?: string, endDate?: string) =>
  api.get('/reports/savings-accounts', { params: { period, start_date: startDate, end_date: endDate } }).then(r => r.data)

export const getSavingsForecast = (period: 'eoy' | '6m' | '12m' = '12m') =>
  api.get('/reports/savings-forecast', { params: { period } }).then(r => r.data)

export const getSavingsRecommendations = () =>
  api.get('/reports/savings-recommendations').then(r => r.data)

export const getDividendsTracker = (period: string, startDate?: string, endDate?: string, accountIds?: number[]) =>
  api.get('/reports/dividends-tracker', { params: { period, start_date: startDate, end_date: endDate, account_ids: accountIds?.join(',') || undefined } }).then(r => r.data)

export const getDividendsForecast = (period: 'eoy' | '6m' | '12m' = '12m', accountIds?: number[]) =>
  api.get('/reports/dividends-forecast', { params: { period, account_ids: accountIds?.join(',') || undefined } }).then(r => r.data)

export const getDividendRecommendations = () =>
  api.get('/reports/dividend-recommendations').then(r => r.data)

export const getBondSchedule = (accountIds?: number[]) =>
  api.get('/reports/bond-schedule', { params: { account_ids: accountIds?.join(',') || undefined } }).then(r => r.data)

export const getBenchmark = (
  benchmarkIds: number[], compareAccountIds: number[], lookbackDays = 252,
  accountIds?: number[], resample = 'Daily', ytd = false, baseSecuritiesId?: number | null, totalReturn = false,
) =>
  api.get('/reports/benchmark', { params: {
    benchmark_ids: benchmarkIds.length ? benchmarkIds.join(',') : undefined,
    compare_account_ids: compareAccountIds.length ? compareAccountIds.join(',') : undefined,
    lookback_days: lookbackDays,
    account_ids: accountIds?.join(',') || undefined, resample,
    ytd: ytd || undefined,
    base_securities_id: baseSecuritiesId ?? undefined,
    total_return: totalReturn || undefined,
  } }).then(r => r.data)

export const getCorrelation = (lookbackDays = 252, maxHoldings = 20, accountIds?: number[]) =>
  api.get('/reports/correlation', { params: { lookback_days: lookbackDays, max_holdings: maxHoldings, account_ids: accountIds?.join(',') || undefined } }).then(r => r.data)

export const getPortfolioPresets = (reportScope: string = 'inv_performance') =>
  api.get('/reports/portfolio-presets', { params: { report_scope: reportScope } }).then(r => r.data)

export const upsertPortfolioPreset = (name: string, accountIds: number[], reportScope: string = 'inv_performance') =>
  api.post('/reports/portfolio-presets', { name, account_ids: accountIds, report_scope: reportScope }).then(r => r.data)

export const deletePortfolioPreset = (presetId: number) =>
  api.delete(`/reports/portfolio-presets/${presetId}`).then(r => r.data)

export const getIncomeExpenseFull = (
  startDate: string, endDate: string,
  cashTypes?: string[], invTypes?: string[], categoryId?: number | null,
) =>
  api.get('/reports/income-expense-full', { params: {
    start_date: startDate,
    end_date: endDate,
    cash_account_types: cashTypes?.join(',') || undefined,
    inv_account_types: invTypes?.join(',') || undefined,
    category_id: categoryId || undefined,
  }}).then(r => r.data)

export type PricesFreshness = {
  security_last_download: string | null; job_last_run: string | null
  job_status: string | null; job_message: string | null; job_interval_min: number | null
}
export const getPricesFreshness = (securityId?: number) =>
  api.get('/market-data/prices-freshness', { params: { security_id: securityId } }).then(r => r.data as PricesFreshness)

// Best / Worst Days (Reports -> Inv. Performance -> Best / Worst Days)
export const getDayExtremeYears = (accountId?: number, accountIds?: number[]) =>
  api.get('/reports/day-extremes/years', { params: { account_id: accountId, account_ids: accountIds?.join(',') || undefined } }).then(r => r.data as number[])

export const getDayExtremes = (p: { accountId?: number; accountIds?: number[]; year: number; n: number; period?: 'day' | 'week' | 'month' }) =>
  api.get('/reports/day-extremes', { params: { account_id: p.accountId, account_ids: p.accountIds?.join(',') || undefined, year: p.year, n: p.n, period: p.period } }).then(r => r.data)

// Stress Test (Reports -> Inv. Performance -> Stress Test)
export const getStressAssumptions = () =>
  api.get('/reports/stress/assumptions').then(r => r.data)

export const runRateShock = (p: { accountIds?: number[]; shockPp: number; periodMonths: number; assumptions?: unknown }) =>
  api.post('/reports/stress/rate-shock', {
    account_ids: p.accountIds?.length ? p.accountIds : undefined,
    shock_pp: p.shockPp, period_months: p.periodMonths, assumptions: p.assumptions,
  }).then(r => r.data)

export const runStressReplay = (p: {
  accountIds?: number[]; scenarios: string[]; assumptions?: unknown
  custom?: { name: string; start: string; end: string; yield_change_pp: number }
}) =>
  api.post('/reports/stress/replay', {
    account_ids: p.accountIds?.length ? p.accountIds : undefined,
    scenarios: p.scenarios, custom: p.custom, assumptions: p.assumptions,
  }).then(r => r.data)

export const getMonteCarlo = (params: {
  yearsAhead?: number; numSims?: number; monthlyContrib?: number; lookbackDays?: number;
  accountIds?: number[]; initialValue?: number; overrideReturnPct?: number; overrideVolPct?: number;
}) =>
  api.get('/reports/monte-carlo', { params: {
    years_ahead: params.yearsAhead, num_sims: params.numSims, monthly_contrib: params.monthlyContrib,
    lookback_days: params.lookbackDays, account_ids: params.accountIds?.join(',') || undefined,
    initial_value: params.initialValue, override_return_pct: params.overrideReturnPct, override_vol_pct: params.overrideVolPct,
  } }).then(r => r.data)


// ── Custom Reports ────────────────────────────────────────────────────────────
export const getCustomReportPresets = () =>
  api.get('/reports/custom-report-presets').then(r => r.data)
export const saveCustomReportPreset = (preset_name: string, config: Record<string, unknown>) =>
  api.post('/reports/custom-report-presets', { preset_name, config }).then(r => r.data)
export const deleteCustomReportPreset = (preset_id: number) =>
  api.delete(`/reports/custom-report-presets/${preset_id}`).then(r => r.data)
export const getCustomReportFilterData = () =>
  api.get('/reports/custom-report-filter-data').then(r => r.data)
export const runCustomReport = (params: Record<string, unknown>) =>
  api.post('/reports/custom-report/run', params).then(r => r.data)
export const runCustomReportDrillDown = (params: Record<string, unknown>) =>
  api.post('/reports/custom-report/drill-down', params).then(r => r.data)
export const runCustomReportInvestmentDrillDown = (params: Record<string, unknown>) =>
  api.post('/reports/custom-report/investment-drill-down', params).then(r => r.data)

// ── Plan (dated to-do steps with a morning Telegram reminder) ───────────────────
export interface PlanItem { id: number; plan: string; due_date: string; title: string; details: string | null; status: 'open' | 'done' | 'skipped'; done_at: string | null }
export interface PlanDue { today: string; overdue: PlanItem[]; due_today: PlanItem[]; upcoming: PlanItem[] }
export const getPlanItems = (): Promise<PlanItem[]> => api.get('/plan/items').then(r => r.data)
export const getPlanDue = (days = 7): Promise<PlanDue> => api.get('/plan/due', { params: { days } }).then(r => r.data)
export const savePlanItem = (id: number | null, body: { plan: string; due_date: string; title: string; details: string }): Promise<PlanItem> =>
  (id == null ? api.post('/plan/items', body) : api.put(`/plan/items/${id}`, body)).then(r => r.data)
export const setPlanStatus = (id: number, status: PlanItem['status']): Promise<PlanItem> => api.post(`/plan/items/${id}/status`, { status }).then(r => r.data)
export const deletePlanItem = (id: number) => api.delete(`/plan/items/${id}`).then(r => r.data)
export const deletePlan = (name: string): Promise<{ deleted: number }> => api.delete('/plan/plans', { params: { name } }).then(r => r.data)
export const importPlan = (text: string, plan: string): Promise<{ created: number; errors: string[]; plan: string }> => api.post('/plan/import', { text, plan: plan || null }).then(r => r.data)
export const remindPlanNow = (): Promise<{ sent: boolean; detail: string }> => api.post('/plan/remind-now').then(r => r.data)

// ── Retirement planner (database/retirement.py) ────────────────────────────────
export interface RetirementStream { name: string; annual: number; start_age: number; end_age: number | null; indexation: 'inflation' | 'fixed' | number; tax: 'income' | 'source' | 'exempt'; source_rate: number }
export interface RetirementLump { name: string; age: number; amount: number; tax_pct: number }
export interface RetirementTax {
  country: string; brackets: [number | null, number][]; credit: number; credit_taper_from: number; credit_taper_per_1000: number; health_pct: number; index_brackets: boolean
  div_pct: number; interest_pct: number; gains_pct: number; pot_mode: 'flat' | 'income'; pot_flat_pct: number; pot_payout: 'drawdown' | 'lump'
}
export interface RetirementParams {
  birth_year: number; retire_age: number; end_age: number
  assets: { cash: number; investments: number; pot: number; investments_cost: number | null }
  contrib: { cash: number; investments: number; pot: number; growth_pct: number }
  spend: { annual: number; real_change_pct: number }
  streams: RetirementStream[]; lumps: RetirementLump[]
  returns: { cash_pct: number; pre_pct: number; post_pct: number; vol_pre_pct: number; vol_post_pct: number; yield_pct: number }
  inflation_pct: number; tax: RetirementTax; order: ('cash' | 'investments' | 'pot')[]; target_success_pct: number
  assets_source?: string
}
export interface RetirementRow {
  age: number; year: number; infl: number; phase: string; cash: number; investments: number; pot: number; contrib?: number; spend?: number; income?: number; health?: number
  income_tax?: number; wd_cash?: number; wd_investments?: number; wd_pot?: number; tax_gains?: number; tax_pot?: number; tax_drag?: number; surplus?: number; shortfall?: number
}
export interface RetirementResult {
  params: RetirementParams; ages: number[]; retire_index: number; balance_at_retirement: number; needed_at_retirement: number | null; gap_at_retirement: number | null
  depletion_age: number | null; survives: boolean; end_balance_real: number; rows: RetirementRow[]; real_total: number[]; insights: string[]
  first_year?: { age: number; spend_real: number; income_real: number; withdrawal_real: number; income_tax_real: number; withdrawal_rate_pct: number }
  mc: null | { sims: number; success_pct: number; p10: number[]; p50: number[]; p90: number[]; solvent_by_age: number[]; end_p10: number; end_p50: number; median_at_retirement: number }
}
export interface RetirementStart {
  params: RetirementParams; rules: { label: string; value: string; note: string }[]
  country: { code: string; name: string; has_tax_profile: boolean; hints: Record<string, string> }
  suggestions: { assets: Record<string, number | null>; flows: { from?: string; to?: string; income?: number; expense?: number; tax?: number; saved?: number }; calibration: { past_return_pct: number; vol_pct: number } | null; notes: string[] }
}
export interface RetirementPlan { id: number; name: string; params: Partial<RetirementParams>; is_base: boolean; updated_at: string }
export interface RetirementLevers {
  success_pct: number; target_pct: number; reached: boolean; capital_needed: number | null; capital_projected: number
  save_more: { extra_per_year: number; extra_per_month: number; success_pct: number } | null
  retire_later: { years: number; age: number; success_pct: number } | null
  spend_less: { cut_pct: number; annual: number; success_pct: number } | null
}
export interface RetirementCompareRow {
  name: string; error?: string; retire_age: number; end_age: number; balance_at_retirement: number; needed_at_retirement: number | null; gap_at_retirement: number | null
  survives: boolean; depletion_age: number | null; end_balance_real: number; success_pct: number | null; target_pct: number; withdrawal_rate_pct: number | null; spend_real: number | null
}
export interface RetirementAssets { assets: { cash: number; investments: number; pot: number; investments_cost: number | null }; notes: string[]; accounts: number }
export const getRetirementAssets = (accountIds?: number[]): Promise<RetirementAssets> =>
  api.get('/retirement/assets', { params: accountIds ? { account_ids: accountIds.join(',') } : {} }).then(r => r.data)
export const getRetirementStart = (): Promise<RetirementStart> => api.get('/retirement/starting-plan').then(r => r.data)
export const projectRetirement = (params: unknown, sims = 1000): Promise<RetirementResult> => api.post('/retirement/project', { params, sims }).then(r => r.data)
export const retirementLevers = (params: unknown): Promise<RetirementLevers> => api.post('/retirement/levers', { params }).then(r => r.data)
export const compareRetirement = (scenarios: { name: string; params: unknown }[], sims = 600): Promise<RetirementCompareRow[]> => api.post('/retirement/compare', { scenarios, sims }).then(r => r.data)
export const getRetirementPlans = (): Promise<RetirementPlan[]> => api.get('/retirement/plans').then(r => r.data)
export const saveRetirementPlan = (body: { id?: number | null; name: string; params: unknown; is_base?: boolean }): Promise<RetirementPlan> => api.post('/retirement/plans', body).then(r => r.data)
export const deleteRetirementPlan = (id: number) => api.delete(`/retirement/plans/${id}`).then(r => r.data)

// Crypto tax (Reports -> Investment Tax -> Crypto)
export interface CryptoTaxResult {
  year: number; method: string; rate: number; exemption: number; income_rate: number; transfers_removed: number
  accounts: { id: number; name: string; rows: number }[]
  summary: { disposals: number; proceeds_eur: number; cost_eur: number; result_eur: number; losses_used_eur: number; taxable_eur: number; tax_eur: number; losses_available_eur: number; purchases_eur: number }
  years: { year: number; result: number; losses_used: number; taxable: number; tax: number; losses_available: number; loss_years: { year: number; remaining: number; expires: number }[] }[]
  disposals: { date: string; security: string; account: string | null; kind: string; quantity: number; proceeds_eur: number; cost_eur: number; gain_loss_eur: number; holding_type: string
    lots: { date: string; quantity: number; cost_eur: number; action: string; how: string; account: string | null }[]; lots_total: number }[]
  rewards: { total: number; taxable: boolean; rows: { security: string; quantity: number; value_eur: number; payments: number }[]; tax_at_rate?: number; tax_progressive?: number }
  e1: { codes: string; label: string; amount: number }[]; notes: string[]
}
export const getCryptoTax = (p: { year: number; method: string; exemption: number; rate?: number; accountIds?: number[]; lossesFrom?: number; rewardsIncome?: boolean }): Promise<CryptoTaxResult> =>
  api.get('/reports/crypto-tax', { params: { year: p.year, method: p.method, exemption: p.exemption, rate: p.rate, account_ids: p.accountIds?.join(',') || undefined, losses_from: p.lossesFrom || undefined, rewards_income: p.rewardsIncome } }).then(r => r.data)

// Tax return summary (Reports -> Investment Tax -> Tax Return)
export interface TaxReturnLine { account: string; security: string; date: string; label: string; amount: number; proceeds: number | null; cost: number | null; withheld: number | null }
export interface TaxReturnResult {
  year: number; method: string; country: { code: string; name: string; form: string; has_profile: boolean }
  sections: { title: string; rows: { key: string; label: string; amount: number; detail: string; code: string; default: string; custom: boolean; lines: TaxReturnLine[]; withheld: number | null; info: boolean; kind: string | null }[] }[]
  codes_set: Record<string, string>; notes: string[]
}
export interface TaxReturnCrypto { rewardsIncome?: boolean; exemption?: number; rate?: number; lossesFrom?: number; exclude?: number[] }
export const getTaxReturn = (year: number, method: string, accountIds?: number[], crypto?: TaxReturnCrypto): Promise<TaxReturnResult> =>
  api.get('/reports/tax-return', { params: { year, method, account_ids: accountIds?.join(',') || undefined, rewards_income: crypto?.rewardsIncome, crypto_exemption: crypto?.exemption || undefined,
    crypto_rate: crypto?.rate, crypto_losses_from: crypto?.lossesFrom || undefined, crypto_exclude: crypto?.exclude?.length ? crypto.exclude.join(',') : undefined } }).then(r => r.data)
export const putTaxReturnCodes = (codes: Record<string, string>) => api.put('/reports/tax-return/codes', { codes }).then(r => r.data)

// Private funds (Reports -> Private Funds)
export interface PrivateFundIn { name: string; manager?: string | null; fund_type?: string | null; vintage_year?: number | null; currency?: string | null; commitment: number; accounts_id?: number | null; status?: string | null; notes?: string | null }
export interface PrivateFundMetrics {
  paid_in: number; distributions: number; nav: number; nav_date: string | null; unfunded: number; called_pct: number | null; dpi: number | null; rvpi: number | null; tvpi: number | null; gain: number; irr_pct: number | null
  next_call: { date: string; amount: number; days: number } | null; upcoming_calls: { id: number; date: string; amount: number; days: number; note: string | null }[]
}
export interface PrivateFundRow extends PrivateFundMetrics { id: number; name: string; manager: string | null; fund_type: string; vintage_year: number | null; currency: string; commitment: number; accounts_id: number | null; status: string; notes: string | null; events: number; rate_eur: number }
export interface PrivateFundDetail extends PrivateFundMetrics { id: number; name: string; manager: string | null; fund_type: string; vintage_year: number | null; currency: string; commitment: number; accounts_id: number | null; status: string; notes: string | null
  events: { id: number; funds_id: number; date: string; type: string; amount: number; note: string | null }[] }
export interface PrivateFundsList {
  funds: PrivateFundRow[]
  summary: { commitment: number; paid_in: number; unfunded: number; nav: number; distributions: number; tvpi: number | null; dpi: number | null; irr_pct: number | null }
  upcoming: { id: number; date: string; amount: number; days: number; note: string | null; fund: string; currency: string; funds_id: number }[]
}
export const getPrivateFunds = (): Promise<PrivateFundsList> => api.get('/private-funds').then(r => r.data)
export const getPrivateFund = (id: number): Promise<PrivateFundDetail> => api.get(`/private-funds/${id}`).then(r => r.data)
export const savePrivateFund = (id: number | null, body: PrivateFundIn): Promise<PrivateFundDetail> => (id == null ? api.post('/private-funds', body) : api.put(`/private-funds/${id}`, body)).then(r => r.data)
export const deletePrivateFund = (id: number) => api.delete(`/private-funds/${id}`).then(r => r.data)
export const addPrivateFundEvent = (id: number, body: { date: string; type: string; amount: number; note?: string | null }): Promise<PrivateFundDetail> => api.post(`/private-funds/${id}/events`, body).then(r => r.data)
export const updatePrivateFundEvent = (id: number, eventId: number, body: { date: string; type: string; amount: number; note?: string | null }): Promise<PrivateFundDetail> => api.put(`/private-funds/${id}/events/${eventId}`, body).then(r => r.data)
export const deletePrivateFundEvent = (id: number, eventId: number): Promise<PrivateFundDetail> => api.delete(`/private-funds/${id}/events/${eventId}`).then(r => r.data)
