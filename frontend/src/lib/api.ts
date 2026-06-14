import axios from 'axios'

export const api = axios.create({ baseURL: '/api' })

// ── Dashboard ────────────────────────────────────────────────────────────────
export const getNetWorth = (startDate = '2020-01-01') =>
  api.get('/dashboard/net-worth', { params: { start_date: startDate } }).then(r => r.data)

export const getAccounts = (includeFuture = false) =>
  api.get('/dashboard/accounts', { params: { include_future: includeFuture } }).then(r => r.data)

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

export const getInsights = () =>
  api.get('/dashboard/insights').then(r => r.data)

export const getAlerts = () =>
  api.get('/dashboard/alerts').then(r => r.data)

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

export const createTransaction = (data: Record<string, unknown>) =>
  api.post('/register/transactions', data).then(r => r.data)

export const updateTransaction = (id: number, data: Record<string, unknown>) =>
  api.put(`/register/transactions/${id}`, data).then(r => r.data)

export const deleteTransaction = (id: number) =>
  api.delete(`/register/transactions/${id}`)

// ── Investments ───────────────────────────────────────────────────────────────
export const getInvestments = (params: Record<string, unknown>) =>
  api.get('/investments/list', { params }).then(r => r.data)

export const getHoldings = (accountId?: number, includeClosed = false) =>
  api.get('/investments/holdings', { params: { ...(accountId ? { account_id: accountId } : {}), include_closed: includeClosed } }).then(r => r.data)

export const updateHolding = (id: number, data: Record<string, unknown>) =>
  api.put(`/investments/holdings/${id}`, data).then(r => r.data)

export const getLinkedAccount = (accountId: number) =>
  api.get(`/investments/linked-account/${accountId}`).then(r => r.data)

export const stakingReinvest = (entries: Record<string, unknown>[]) =>
  api.post('/investments/staking-reinvest', entries).then(r => r.data)

// ── Reports ───────────────────────────────────────────────────────────────────
export const getIncomeExpense = (startDate: string, endDate: string) =>
  api.get('/reports/income-expense', { params: { start_date: startDate, end_date: endDate } }).then(r => r.data)

export const getTopCategories = (startDate: string, endDate: string, catType = 'Expense', topN = 10) =>
  api.get('/reports/top-categories', { params: { start_date: startDate, end_date: endDate, cat_type: catType, top_n: topN } }).then(r => r.data)

export const getSavingsRate = (months = 12) =>
  api.get('/reports/savings-rate', { params: { months } }).then(r => r.data)

export const getPortfolioSummary = () =>
  api.get('/reports/portfolio-summary').then(r => r.data)

export const getCategoryBreakdown = (startDate: string, endDate: string) =>
  api.get('/reports/category-breakdown', { params: { start_date: startDate, end_date: endDate } }).then(r => r.data)

export const getBudgets = (year: number, month?: number) =>
  api.get('/reports/budgets', { params: { year, ...(month ? { month } : {}) } }).then(r => r.data)

export const getCapitalGains = (year: number) =>
  api.get('/reports/capital-gains', { params: { year } }).then(r => r.data)

export const getDividends = (startDate: string, endDate: string) =>
  api.get('/reports/dividends', { params: { start_date: startDate, end_date: endDate } }).then(r => r.data)

export const getAllocationReport = () =>
  api.get('/reports/allocation').then(r => r.data)

export const getNetWorthReport = (startDate: string, endDate: string, grouping = 'month') =>
  api.get('/reports/net-worth-report', { params: { start_date: startDate, end_date: endDate, grouping } }).then(r => r.data)

export const getPnl = (startDate = '1900-01-01', endDate?: string) =>
  api.get('/reports/pnl', { params: { start_date: startDate, ...(endDate ? { end_date: endDate } : {}) } }).then(r => r.data)

export const getIncomeExpenseDetail = (startDate: string, endDate: string, grouping = 'month') =>
  api.get('/reports/income-expense-detail', { params: { start_date: startDate, end_date: endDate, grouping } }).then(r => r.data)

export const getBudgetVsActual = (year: number, month?: number) =>
  api.get('/reports/budget-vs-actual', { params: { year, ...(month ? { month } : {}) } }).then(r => r.data)

export const getCashFlowForecast = (monthsAhead = 6) =>
  api.get('/reports/cash-flow-forecast', { params: { months_ahead: monthsAhead } }).then(r => r.data)

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

export const getPayees = (search?: string) =>
  api.get('/static-data/payees', { params: search ? { search } : {} }).then(r => r.data)

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

export const mergeCategories = (source_id: number, target_id: number) =>
  api.post('/static-data/categories/merge', { source_id, target_id }).then(r => r.data)

export const upsertCategory = (data: Record<string, unknown>) =>
  api.post('/static-data/categories', data).then(r => r.data)

export const upsertInstitution = (data: Record<string, unknown>) =>
  api.post('/static-data/institutions', data).then(r => r.data)

export const getSecuritiesMaster = (search?: string) =>
  api.get('/static-data/securities', { params: search ? { search } : {} }).then(r => r.data)

export const upsertSecurity = (data: Record<string, unknown>) =>
  api.post('/static-data/securities', data).then(r => r.data)

export const getCurrenciesMaster = () =>
  api.get('/static-data/currencies').then(r => r.data)

export const upsertCurrency = (data: Record<string, unknown>) =>
  api.post('/static-data/currencies', data).then(r => r.data)

// ── Market Data ───────────────────────────────────────────────────────────────
export const getCurrencies = () =>
  api.get('/market-data/currencies').then(r => r.data)

export const getSecurities = (search?: string) =>
  api.get('/market-data/securities', { params: search ? { search } : {} }).then(r => r.data)

export const getPriceHistory = (securityId: number, fromDate = '2020-01-01') =>
  api.get('/market-data/price-history', { params: { security_id: securityId, from_date: fromDate } }).then(r => r.data)

export const getPriceAnomalies = (thresholdPct = 100) =>
  api.get('/market-data/price-anomalies', { params: { threshold_pct: thresholdPct } }).then(r => r.data)

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

export const downloadYahooPrices = (period: string, securityId?: number) =>
  api.post('/market-data/download/yahoo-prices', { period, ...(securityId ? { security_id: securityId } : {}) }).then(r => r.data)

export const downloadTvInfo = (securityId?: number, overwrite = false) =>
  api.post('/market-data/download/tv-info', { overwrite, ...(securityId ? { security_id: securityId } : {}) }).then(r => r.data)

export const downloadTvPrices = (period: string, securityId?: number) =>
  api.post('/market-data/download/tv-prices', { period, ...(securityId ? { security_id: securityId } : {}) }).then(r => r.data)

export const downloadSolidusBonds = () =>
  api.post('/market-data/download/solidus-bonds').then(r => r.data)

export const addPrice = (data: { security_id: number; date: string; close: number }) =>
  api.post('/market-data/prices', data).then(r => r.data)

export const deletePrice = (security_id: number, date: string) =>
  api.delete('/market-data/prices', { params: { security_id, date } }).then(r => r.data)

export const addFxRate = (data: { currency_id: number; date: string; rate: number }) =>
  api.post('/market-data/fx', data).then(r => r.data)

export const deleteFxRate = (currency_id: number, date: string) =>
  api.delete('/market-data/fx', { params: { currency_id, date } }).then(r => r.data)

// ── Recurring Templates ───────────────────────────────────────────────────────
export const getRecurringTemplates = () =>
  api.get('/recurring/templates').then(r => r.data)

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

export const getSecurityDividends = (secId: number) =>
  api.get(`/securities/${secId}/dividends`).then(r => r.data)

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
  api.post(`/securities/${secId}/corporate-actions/execute`, data).then(r => r.data)

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
export const parseStatement = (profileId: number, file: File) => {
  const fd = new FormData(); fd.append('file', file)
  return api.post('/bank/parse-statement', fd, { params: { profile_id: profileId }, headers: { 'Content-Type': 'multipart/form-data' } }).then(r => r.data)
}
export const getAppTransactions = (accountId: number, dateFrom: string, dateTo: string) =>
  api.get('/bank/app-transactions', { params: { account_id: accountId, date_from: dateFrom, date_to: dateTo } }).then(r => r.data)
export const applyBankImport = (data: Record<string, unknown>) => api.post('/bank/apply-import', data).then(r => r.data)
export const getReconciliationHistoryAccounts = () => api.get('/bank/reconciliation-history-accounts').then(r => r.data)
export const getReconciliationHistory = (accountId: number) => api.get(`/bank/reconciliation-history/${accountId}`).then(r => r.data)
export const ibFlexFetch = (token: string, queryId: string) => api.post('/bank/ib-flex-fetch', { token, query_id: queryId }).then(r => r.data)
export const ibFlexParse = (xml: string, accountId: number, cashAccountId?: number) =>
  api.post('/bank/ib-flex-parse', { xml, account_id: accountId, cash_account_id: cashAccountId ?? null }).then(r => r.data)
export const ibFlexImport = (data: Record<string, unknown>) => api.post('/bank/ib-flex-import', data).then(r => r.data)
export const revtParse = (file: File, accountId: number) => {
  const fd = new FormData(); fd.append('file', file)
  return api.post('/bank/revolut-trading-parse', fd, { params: { account_id: accountId }, headers: { 'Content-Type': 'multipart/form-data' } }).then(r => r.data)
}
export const revtImport = (file: File, accountId: number, replaceMode: boolean, importInv: boolean, importTx: boolean) => {
  const fd = new FormData(); fd.append('file', file)
  return api.post('/bank/revolut-trading-import', fd, {
    params: { account_id: accountId, replace_mode: replaceMode, import_inv: importInv, import_tx: importTx },
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then(r => r.data)
}

// ── Saxo Bank ─────────────────────────────────────────────────────────────────
export const saxoGetSettings = () => api.get('/bank/saxo-settings').then(r => r.data)
export const saxoSaveAccountMap = (accountMap: Record<string, number>) =>
  api.post('/bank/saxo-save-account-map', { account_map: accountMap }).then(r => r.data)
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

// ── Coinbase ──────────────────────────────────────────────────────────────────
export const getImporterSettings = (key: string) => api.get(`/bank/importer-settings/${key}`).then(r => r.data)
export const saveImporterSettings = (key: string, data: Record<string, unknown>) => api.post(`/bank/importer-settings/${key}`, data).then(r => r.data)

export const coinbaseGetSettings = () => api.get('/bank/coinbase-settings').then(r => r.data)
export const coinbaseTest = (data: object) => api.post('/bank/coinbase-test', data).then(r => r.data)
export const coinbaseFetch = (data: object) => api.post('/bank/coinbase-fetch', data).then(r => r.data)
export const coinbaseImport = (data: object) => api.post('/bank/coinbase-import', data).then(r => r.data)

// ── Tools ─────────────────────────────────────────────────────────────────────
export const runVacuum = () => api.post('/tools/vacuum').then(r => r.data)
export const runBackup = () => api.post('/tools/backup').then(r => r.data)
export const getSchedulerStatus = () => api.get('/tools/scheduler-status').then(r => r.data)
export const runSql = (sql: string) => api.post('/tools/run-sql', { sql }).then(r => r.data)

// ── Budgets (write) ───────────────────────────────────────────────────────────
export const upsertBudget = (data: Record<string, unknown>) =>
  api.post('/reports/budgets', data).then(r => r.data)

export const deleteBudget = (id: number) =>
  api.delete(`/reports/budgets/${id}`).then(r => r.data)

export const getNetWorthByAccount = (startDate: string, endDate: string, grouping: string) =>
  api.get('/reports/net-worth-by-account', { params: { start_date: startDate, end_date: endDate, grouping } }).then(r => r.data)

export const getInvestmentPositionsHistory = (startDate: string) =>
  api.get('/reports/investment-positions-history', { params: { start_date: startDate } }).then(r => r.data)

export const getSectorAllocation = () =>
  api.get('/reports/sector-allocation').then(r => r.data)

export const getFxExposure = () =>
  api.get('/reports/fx-exposure').then(r => r.data)

export const getSpendingByPayee = (startDate: string, endDate: string, topN = 20) =>
  api.get('/reports/spending-by-payee', { params: { start_date: startDate, end_date: endDate, top_n: topN } }).then(r => r.data)

export const getSpendingTrends = (months: number) =>
  api.get('/reports/spending-trends', { params: { months } }).then(r => r.data)

export const getSavingsRateDetail = (months: number) =>
  api.get('/reports/savings-rate-detail', { params: { months } }).then(r => r.data)

export const getTwr = (startDate: string, endDate: string) =>
  api.get('/reports/twr', { params: { start_date: startDate, end_date: endDate } }).then(r => r.data)

export const getRiskMetrics = (startDate: string, endDate: string) =>
  api.get('/reports/risk-metrics', { params: { start_date: startDate, end_date: endDate } }).then(r => r.data)

export const getTaxLossHarvesting = () =>
  api.get('/reports/tax-loss-harvesting').then(r => r.data)

export const getDividendIncomeTax = (year: number) =>
  api.get('/reports/dividend-income-tax', { params: { year } }).then(r => r.data)

export const getPriceChanges = () =>
  api.get('/reports/price-changes').then(r => r.data)

export const getGoals = () =>
  api.get('/reports/goals').then(r => r.data)

export const upsertGoal = (data: object) =>
  api.post('/reports/goals', data).then(r => r.data)

export const deleteGoal = (id: number) =>
  api.delete(`/reports/goals/${id}`).then(r => r.data)
