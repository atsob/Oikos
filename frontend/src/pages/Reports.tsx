import React, { useState, useMemo, useCallback, useEffect } from 'react'
import { usePersist, useLiveRefetchInterval, useGridColumnState, useGridApi, useGridFilterState } from '@/lib/hooks'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import PlotlyReact from 'react-plotly.js'
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const Plot: React.ComponentType<any> = (PlotlyReact as any).default ?? PlotlyReact
import {
  getPortfolioSummary,
  getHoldingsSnapshot,
  getCapitalGains,
  getBudgetVsActual, getAnnualIncome, getYtdExpenseTransactions, saveBudget,
  getCashFlowForecastFull, getPnl, getPnlPeriod,
  getNetWorthByAccount, getInvestmentPositionsHistory, getFxExposure,
  getXraySectorWeighting, getXrayAssetAllocation, getXrayAssetAllocationTargets, saveXrayAssetAllocationTargets, getXrayStyleBox, getXrayBondQuality, getXrayStockOverlap, getXrayExpenseRatio,
  getSpendingTrends, getSavingsRateDetail,
  getTwr, getRiskMetrics, getTaxLossHarvesting, getDividendIncomeTax, getPriceChanges, getPortfolioSignals,
  getGoals, upsertGoal, deleteGoal,
  getBondSchedule, getBenchmarkCandidates, getBenchmark, getCorrelation, getSavingsAccounts,
  getSavingsForecast, getSavingsRecommendations,
  getDividendsTracker, getDividendsForecast, getDividendRecommendations, getAccounts,
  getPortfolioPresets, upsertPortfolioPreset, deletePortfolioPreset, getMonteCarlo,
  getIncomeExpenseFull,
  getCustomReportPresets, saveCustomReportPreset, deleteCustomReportPreset,
  getCustomReportFilterData, runCustomReport, runCustomReportDrillDown, runCustomReportInvestmentDrillDown,
  updateTransaction, upsertSplits, getSplits, getCategories, getPayees, deleteTransaction,
  getTransactionById,
  addPrice,
  getSecurities, lookupTicker, upsertSecurity,
  api,
} from '@/lib/api'
import { Card, CardBody, Input, Select, Spinner, Button, Tooltip, ColHeader, ColumnsMenu, CopyToExcelButton, useSortTable, useSortTablePersisted, ACCOUNT_TYPE_ORDER, AG_GRID_COLUMN_TYPES, AccountLink } from '@/components/ui'
import { fmtEur, fmtPct, fmtNum, plotLayout } from '@/lib/utils'
import { getCurrencySymbol } from '@/lib/settings'
import { useTheme } from '@/lib/theme'
import { Trash2, Plus, Pencil, RefreshCw, ChevronRight, ChevronDown } from 'lucide-react'
import { TxModal, useNoOpRecurring } from '@/components/TxModal'
import type { TxForm, SplitRow } from '@/components/TxModal'
import { AgGridReact } from 'ag-grid-react'
import type { ColDef } from 'ag-grid-community'

type Row = Record<string, unknown>

function SecLink({ id, children }: { id: unknown; children: React.ReactNode }) {
  const navigate = useNavigate()
  if (!id) return <>{children}</>
  return (
    <button onClick={() => navigate(`/securities/${id}`)}
      className="text-blue-600 hover:underline text-left">{children}</button>
  )
}

// ── Sidebar tabs ──────────────────────────────────────────────────────────────
const REPORT_TABS = [
  { key: 'net-worth',       label: '📊 Net Worth' },
  { key: 'income-expense',  label: '💰 Income & Expense' },
  { key: 'cashflow',        label: '🔄 Cash Flow Forecast' },
  { key: 'budget',          label: '🎯 Budget & Spending' },
  { key: 'inv-positions',   label: '📈 Inv. Portfolio' },
  { key: 'inv-performance', label: '💹 Inv. Performance' },
  { key: 'tax',             label: '🧾 Investment Tax' },
  { key: 'securities',      label: '🔍 Securities Analysis' },
  { key: 'planning',        label: '🏖️ Financial Planning' },
  { key: 'custom',          label: '📋 Custom Reports' },
]

// ── SubTabs ───────────────────────────────────────────────────────────────────
function SubTabs({ tabs, active, onChange }: { tabs: string[]; active: string; onChange: (t: string) => void }) {
  return (
    <div className="flex gap-0.5 border-b border-slate-200 mb-4 overflow-x-auto">
      {tabs.map(t => (
        <button key={t} onClick={() => onChange(t)}
          className={`px-3 py-1.5 text-sm font-medium border-b-2 -mb-px transition-colors whitespace-nowrap ${active === t ? 'border-blue-600 text-blue-700' : 'border-transparent text-slate-500 hover:text-slate-700'}`}>
          {t}
        </button>
      ))}
    </div>
  )
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function KpiCard({ label, value, color = '', subtitle, subtitleNode, tooltip, compact }: { label: string; value: string; color?: string; subtitle?: string; subtitleNode?: React.ReactNode; tooltip?: string; compact?: boolean }) {
  return (
    <div className={`bg-slate-50 rounded-lg ${compact ? 'p-2.5' : 'p-3'}`}>
      <p className="text-slate-500 mb-1 text-xs">
        {tooltip ? <Tooltip text={tooltip}>{label}</Tooltip> : label}
      </p>
      <p className={`font-bold tabular-nums ${compact ? 'text-sm' : 'text-sm'} ${color}`}>{value}</p>
      {subtitle && <p className="text-slate-400 mt-0.5 truncate text-xs">{subtitle}</p>}
      {subtitleNode && <div className="mt-0.5 text-xs">{subtitleNode}</div>}
    </div>
  )
}

function PctCell({ val }: { val: number | null | undefined }) {
  if (val == null) return <td className="px-2 py-1.5 text-right text-slate-400">—</td>
  const color = val > 0 ? 'text-green-700' : val < 0 ? 'text-red-600' : 'text-slate-500'
  return <td className={`px-2 py-1.5 text-right tabular-nums font-medium ${color}`}>{val > 0 ? '+' : ''}{val.toFixed(2)}%</td>
}

// ── Copy-to-Excel wrapper ─────────────────────────────────────────────────────
function WithCopy({ children }: { children: React.ReactNode }) {
  const ref = React.useRef<HTMLDivElement>(null)
  const [copied, setCopied] = React.useState(false)
  const copy = () => {
    const table = ref.current?.querySelector('table')
    if (!table) return
    const tsv = Array.from(table.querySelectorAll('tr'))
      .map(tr => Array.from(tr.querySelectorAll('th,td')).map(c => c.textContent?.trim() ?? '').join('\t'))
      .join('\n')
    navigator.clipboard.writeText(tsv).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1800) })
  }
  return (
    <div className="space-y-2" ref={ref}>
      {children}
      <button onClick={copy} className={`flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium transition-colors ${copied ? 'bg-green-600 text-white' : 'bg-slate-700 text-white hover:bg-slate-800'}`}>
        {copied ? '✓ Copied!' : '📋 Copy to Excel'}
      </button>
    </div>
  )
}

// ── Pivot table ───────────────────────────────────────────────────────────────
function PivotTable({ data, groupBy, colKey, valKey, showTotal = true, idKey, linkType }: {
  data: Row[]; groupBy: string; colKey: string; valKey: string; showTotal?: boolean
  idKey?: string; linkType?: string
}) {
  const periods = [...new Set(data.map(r => String(r[colKey])))].sort()
  const categories = [...new Set(data.map(r => String(r[groupBy])))]
  const lookup: Record<string, Record<string, number>> = {}
  const idByCategory: Record<string, number> = {}
  for (const r of data) {
    const g = String(r[groupBy]); const c = String(r[colKey])
    if (!lookup[g]) lookup[g] = {}
    lookup[g][c] = (lookup[g][c] ?? 0) + Number(r[valKey] ?? 0)
    if (idKey && r[idKey] != null) idByCategory[g] = Number(r[idKey])
  }
  return (
    <WithCopy>
      <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-300px)] text-xs">
        <table className="w-full border-collapse">
          <thead className="sticky top-0 z-10 bg-slate-50">
            <tr className="bg-slate-50">
              <th className="text-left px-2 py-1.5 border-b border-slate-200 font-semibold sticky left-0 bg-slate-50 min-w-40">{groupBy}</th>
              {periods.map(p => <th key={p} className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold whitespace-nowrap">{p.slice(0, 7)}</th>)}
              {showTotal && <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold">Total</th>}
            </tr>
          </thead>
          <tbody>
            {categories.map(cat => {
              const rowTotal = periods.reduce((s, p) => s + (lookup[cat]?.[p] ?? 0), 0)
              return (
                <tr key={cat} className="border-b border-slate-100 hover:bg-slate-50">
                  <td className="px-2 py-1.5 sticky left-0 bg-white">
                    {idKey && linkType ? <AccountLink id={idByCategory[cat]} name={cat} type={linkType} /> : cat}
                  </td>
                  {periods.map(p => {
                    const v = lookup[cat]?.[p]
                    const c = v != null ? (v < 0 ? 'text-red-600' : '') : ''
                    return <td key={p} className={`text-right px-2 py-1.5 tabular-nums ${c}`}>{v != null ? fmtEur(v) : '—'}</td>
                  })}
                  {showTotal && <td className="text-right px-2 py-1.5 tabular-nums font-semibold">{fmtEur(rowTotal)}</td>}
                </tr>
              )
            })}
            {/* Column totals row */}
            <tr className="border-t-2 border-slate-300 bg-slate-50 font-semibold text-xs">
              <td className="px-2 py-1.5 sticky left-0 bg-slate-50 text-slate-600">Total</td>
              {periods.map(p => {
                const colTotal = categories.reduce((s, cat) => s + (lookup[cat]?.[p] ?? 0), 0)
                return <td key={p} className="text-right px-2 py-1.5 tabular-nums">{fmtEur(colTotal)}</td>
              })}
              {showTotal && (
                <td className="text-right px-2 py-1.5 tabular-nums">
                  {fmtEur(categories.reduce((s, cat) => s + periods.reduce((ss, p) => ss + (lookup[cat]?.[p] ?? 0), 0), 0))}
                </td>
              )}
            </tr>
          </tbody>
        </table>
      </div>
    </WithCopy>
  )
}

// ════════════════════════════════════════════════════════════════════════════
// 1. NET WORTH
// ════════════════════════════════════════════════════════════════════════════

const NW_GROUP_MAP: Record<string, string> = {
  'Cash': 'Cash & Bank', 'Checking': 'Cash & Bank', 'Savings': 'Cash & Bank', 'Bank': 'Cash & Bank',
  'Brokerage': 'Investments', 'Margin': 'Investments',
  'Pension': 'Pension', 'Other Investment': 'Investments',
  'Real Estate': 'Other Assets', 'Vehicle': 'Other Assets', 'Asset': 'Other Assets',
  'Credit Card': 'Credit Cards',
  'Loan': 'Loans', 'Mortgage': 'Loans',
  'Liability': 'Other Liabilities',
}
const NW_ASSET_GROUPS = ['Cash & Bank', 'Investments', 'Pension', 'Other Assets']
const NW_LIAB_GROUPS  = ['Credit Cards', 'Loans', 'Other Liabilities']
const NW_ALL_GROUPS   = [...NW_ASSET_GROUPS, ...NW_LIAB_GROUPS]
const NW_GROUP_COLORS: Record<string, string> = {
  'Cash & Bank': '#eab308', 'Investments': '#1e40af', 'Pension': '#06b6d4',
  'Other Assets': '#8b5cf6', 'Credit Cards': '#ef4444', 'Loans': '#f97316', 'Other Liabilities': '#dc2626',
}
function nwGroup(type: string) { return NW_GROUP_MAP[type] ?? 'Other Assets' }
function fmtPeriodLabel(p: string, grouping: string) {
  if (grouping === 'year') return p.slice(0, 4)
  if (grouping === 'quarter') {
    const [y, m] = p.split('-'); const q = Math.ceil(Number(m) / 3); return `Q${q}/${y}`
  }
  const d = new Date(p + 'T00:00:00'); return d.toLocaleDateString('en-GB', { month: 'short', year: '2-digit' })
}
function fmtPeriodHeader(p: string, grouping: string) {
  if (grouping === 'year') return p.slice(0, 4)
  const d = new Date(p + 'T00:00:00')
  if (grouping === 'quarter') { const q = Math.ceil((d.getMonth() + 1) / 3); return `Q${q}/${d.getFullYear()}` }
  return `${String(d.getDate()).padStart(2,'0')}/${String(d.getMonth()+1).padStart(2,'0')}/${d.getFullYear()}`
}

function NwOverview({ rows, baselineRows, allPeriods, grouping }: { rows: Row[]; baselineRows: Row[]; allPeriods: string[]; grouping: string }) {
  const { isDark } = useTheme()
  const byPeriod: Record<string, Record<string, number>> = {}
  for (const r of rows) {
    const p = String(r.period), g = nwGroup(String(r.accounts_type)), v = Number(r.balance_eur ?? 0)
    if (!byPeriod[p]) byPeriod[p] = {}
    byPeriod[p][g] = (byPeriod[p][g] ?? 0) + v
  }
  const latest = allPeriods.length ? byPeriod[allPeriods[allPeriods.length - 1]] ?? {} : {}
  const totalAssets = NW_ASSET_GROUPS.reduce((s, g) => s + (latest[g] ?? 0), 0)
  const totalLiab   = NW_LIAB_GROUPS.reduce((s, g)  => s + (latest[g] ?? 0), 0)
  const netWorth = totalAssets + totalLiab
  const xs = allPeriods.map(p => fmtPeriodLabel(p, grouping))

  // The comparison baseline is the exact Start Date (a dedicated data point the backend
  // always includes, kept out of `rows`/`allPeriods` so it never becomes a spurious chart
  // bar) — not allPeriods[0], since a bucket's end date lands a full grouping-interval after
  // Start Date, which used to make the KPI's "change since Start Date" figure disagree
  // depending on the Year/Quarter/Month toggle.
  const baseByGroup: Record<string, number> = {}
  for (const r of baselineRows) {
    const g = nwGroup(String(r.accounts_type))
    baseByGroup[g] = (baseByGroup[g] ?? 0) + Number(r.balance_eur ?? 0)
  }
  const basePeriod = baselineRows.length ? String(baselineRows[0].period) : (allPeriods.length ? allPeriods[0] : null)
  const baseNetWorth = baselineRows.length
    ? NW_ASSET_GROUPS.reduce((s, g) => s + (baseByGroup[g] ?? 0), 0) + NW_LIAB_GROUPS.reduce((s, g) => s + (baseByGroup[g] ?? 0), 0)
    : (basePeriod != null ? NW_ASSET_GROUPS.reduce((s, g) => s + (byPeriod[basePeriod]?.[g] ?? 0), 0) + NW_LIAB_GROUPS.reduce((s, g) => s + (byPeriod[basePeriod]?.[g] ?? 0), 0) : null)
  const delta = baseNetWorth != null ? netWorth - baseNetWorth : null
  const pctChange = delta != null && baseNetWorth ? (delta / Math.abs(baseNetWorth)) * 100 : null
  // Periods are actual dates, and the range can span anywhere from a few weeks to decades,
  // so derive the annualization factor from real elapsed days rather than assuming a fixed
  // period length.
  const daysElapsed = basePeriod != null && allPeriods.length && basePeriod !== allPeriods[allPeriods.length - 1]
    ? (new Date(allPeriods[allPeriods.length - 1] + 'T00:00:00').getTime() - new Date(basePeriod + 'T00:00:00').getTime()) / 86400000
    : null
  const periodsPerYear = daysElapsed && daysElapsed > 0 ? 365.25 / daysElapsed : null
  const annualizedPct = pctChange != null && periodsPerYear != null ? (Math.pow(1 + pctChange / 100, periodsPerYear) - 1) * 100 : null

  return (
    <div className="space-y-4">
      {/* Net Worth gets extra width as the "hero" card; the rest stay compact so all
          six fit on one line instead of wrapping. Liabilities is shown as its own card
          (balances are stored negative) so the cards visually reconcile to Net Worth
          instead of the four asset cards alone summing to more than it. */}
      <div className="grid grid-cols-2 md:grid-cols-[1.3fr_1fr_1fr_1fr_1fr_1fr] gap-3">
        <KpiCard label="Net Worth" value={fmtEur(netWorth)} color={netWorth >= 0 ? 'text-blue-700' : 'text-red-600'}
          tooltip={basePeriod ? `Change since ${fmtPeriodHeader(basePeriod, grouping)}, and that rate of change annualised.` : undefined}
          subtitleNode={delta != null ? (
            <span className="flex gap-2 tabular-nums">
              <span className={delta >= 0 ? 'text-green-700' : 'text-red-600'}>{delta >= 0 ? '+' : ''}{fmtEur(delta)}</span>
              {annualizedPct != null && (
                <span className={annualizedPct >= 0 ? 'text-green-700' : 'text-red-600'}>({annualizedPct >= 0 ? '+' : ''}{annualizedPct.toFixed(1)}% ann.)</span>
              )}
            </span>
          ) : undefined} />
        {NW_ASSET_GROUPS.map(g => <KpiCard key={g} label={g} value={fmtEur(latest[g] ?? 0)} compact />)}
        <KpiCard label="Liabilities" value={fmtEur(totalLiab)} color={totalLiab < 0 ? 'text-red-600' : ''} compact />
      </div>
      <Plot
        data={[
          ...NW_ASSET_GROUPS.map(g => ({ x: xs, y: allPeriods.map(p => byPeriod[p]?.[g] ?? 0), name: g, type: 'bar' as const, marker: { color: NW_GROUP_COLORS[g] } })),
          ...NW_LIAB_GROUPS.map(g => ({ x: xs, y: allPeriods.map(p => byPeriod[p]?.[g] ?? 0), name: g, type: 'bar' as const, marker: { color: NW_GROUP_COLORS[g] } })),
          { x: xs, y: allPeriods.map(p => NW_ASSET_GROUPS.reduce((s,g) => s+(byPeriod[p]?.[g]??0),0) + NW_LIAB_GROUPS.reduce((s,g) => s+(byPeriod[p]?.[g]??0),0)), name: 'Net Worth', type: 'scatter' as const, mode: 'lines+markers' as const, line: { color: '#1e40af', width: 2 }, marker: { size: 4, color: '#1e40af' }, yaxis: 'y' },
        ]}
        layout={{ barmode: 'relative' as const, height: 380, margin: { t: 10, r: 10, b: 40, l: 70 }, yaxis: { tickformat: ',.0f', tickprefix: '€' }, legend: { orientation: 'h' as const, y: -0.25 }, ...plotLayout(isDark), hovermode: 'x unified' as const }}
        config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }} />
    </div>
  )
}

function NwAccountBalances({ rows, allPeriods, accountMeta, grouping }: { rows: Row[]; allPeriods: string[]; accountMeta: Record<string, string>; grouping: string }) {
  const [accSortKey, setAccSortKey] = useState<'name' | 'latest'>('latest')
  const [accSortDir, setAccSortDir] = useState<'asc' | 'desc'>('desc')

  const toggleAccSort = (key: 'name' | 'latest') => {
    if (accSortKey === key) setAccSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setAccSortKey(key); setAccSortDir(key === 'name' ? 'asc' : 'desc') }
  }

  const lookup: Record<string, Record<string, number>> = {}
  const idByAccount: Record<string, number> = {}
  for (const r of rows) {
    const a = String(r.accounts_name), p = String(r.period)
    if (!lookup[a]) lookup[a] = {}
    lookup[a][p] = Number(r.balance_eur ?? 0)
    if (r.accounts_id != null) idByAccount[a] = Number(r.accounts_id)
  }
  const lastPeriod = allPeriods[allPeriods.length - 1]
  const accounts = Object.keys(accountMeta)
    .filter(a => lookup[a])
    .sort((a, b) => accountMeta[a].localeCompare(accountMeta[b]) || a.localeCompare(b))
  const typeGroups: Record<string, string[]> = {}
  for (const a of accounts) {
    const t = accountMeta[a] ?? 'Other'
    if (!typeGroups[t]) typeGroups[t] = []
    typeGroups[t].push(a)
  }
  const sortAccounts = (accs: string[]) => [...accs].sort((a, b) => {
    if (accSortKey === 'name') return accSortDir === 'asc' ? a.localeCompare(b) : b.localeCompare(a)
    const av = lookup[a]?.[lastPeriod] ?? 0
    const bv = lookup[b]?.[lastPeriod] ?? 0
    return accSortDir === 'asc' ? av - bv : bv - av
  })
  const headers = allPeriods.map(p => fmtPeriodHeader(p, grouping))
  const totalsByPeriod: Record<string, number> = {}
  for (const p of allPeriods) totalsByPeriod[p] = accounts.reduce((s, a) => s + (lookup[a]?.[p] ?? 0), 0)

  return (
    <div className="space-y-4">
      <WithCopy>
      <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-300px)] text-xs">
        <table className="w-full border-collapse">
          <thead className="sticky top-0 z-10 bg-slate-50">
            <tr className="bg-slate-50">
              <th className="text-left px-2 py-1.5 border-b border-slate-200 sticky left-0 bg-slate-50 min-w-52">
                <button type="button" onClick={() => toggleAccSort('name')} className="inline-flex items-center gap-0.5 font-semibold cursor-pointer hover:text-slate-700 select-none">
                  Account <span className={`text-[9px] ml-0.5 ${accSortKey === 'name' ? 'text-blue-500' : 'text-slate-300'}`}>{accSortKey === 'name' ? (accSortDir === 'asc' ? '▲' : '▼') : '⇅'}</span>
                </button>
              </th>
              <th className="text-left px-2 py-1.5 border-b border-slate-200 font-semibold sticky left-52 bg-slate-50 whitespace-nowrap text-slate-400 text-[10px]">type</th>
              {allPeriods.map((p, i) => {
                const isLast = i === allPeriods.length - 1
                return isLast
                  ? <th key={p} className="text-right px-2 py-1.5 border-b border-slate-200 whitespace-nowrap">
                      <button type="button" onClick={() => toggleAccSort('latest')} className="inline-flex items-center gap-0.5 font-semibold cursor-pointer hover:text-slate-700 select-none flex-row-reverse w-full justify-start">
                        {headers[i]} <span className={`text-[9px] ml-0.5 ${accSortKey === 'latest' ? 'text-blue-500' : 'text-slate-300'}`}>{accSortKey === 'latest' ? (accSortDir === 'asc' ? '▲' : '▼') : '⇅'}</span>
                      </button>
                    </th>
                  : <th key={p} className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold whitespace-nowrap">{headers[i]}</th>
              })}
            </tr>
          </thead>
          <tbody>
            {Object.entries(typeGroups).map(([type, accs]) => (
              <React.Fragment key={type}>
                <tr className="bg-slate-100">
                  <td className="px-2 py-1 text-slate-500 uppercase text-xs tracking-wide sticky left-0 bg-slate-100">{type.toUpperCase()}</td>
                  <td className="px-2 py-1 text-slate-400 sticky left-52 bg-slate-100 text-[10px]">group</td>
                  {allPeriods.map(p => <td key={p} className="px-2 py-1 text-slate-300 text-right">—</td>)}
                </tr>
                {sortAccounts(accs).map(acc => (
                  <tr key={acc} className="border-b border-slate-100 hover:bg-slate-50">
                    <td className="px-2 py-1.5 pl-5 sticky left-0 bg-white"><AccountLink id={idByAccount[acc]} name={acc} type={accountMeta[acc]} /></td>
                    <td className="px-2 py-1.5 text-slate-400 sticky left-52 bg-white text-[10px]">account</td>
                    {allPeriods.map(p => {
                      const v = lookup[acc]?.[p]
                      if (v == null) return <td key={p} className="text-right px-2 py-1.5 text-slate-300">—</td>
                      return <td key={p} className={`text-right px-2 py-1.5 tabular-nums ${v < 0 ? 'text-red-600' : 'text-slate-700'}`}>{fmtEur(v)}</td>
                    })}
                  </tr>
                ))}
              </React.Fragment>
            ))}
          </tbody>
          <tfoot className="sticky bottom-0 z-10">
            <tr className="bg-slate-100 border-t-2 border-slate-300 font-semibold">
              <td className="px-2 py-1.5 sticky left-0 bg-slate-100">TOTAL</td>
              <td className="px-2 py-1.5 text-slate-400 sticky left-52 bg-slate-100 text-[10px]"></td>
              {allPeriods.map(p => {
                const v = totalsByPeriod[p]
                return <td key={p} className={`text-right px-2 py-1.5 tabular-nums ${v < 0 ? 'text-red-600' : 'text-slate-700'}`}>{fmtEur(v)}</td>
              })}
            </tr>
          </tfoot>
        </table>
      </div>
      </WithCopy>
    </div>
  )
}

function NwSummaryByType({ rows, allPeriods, grouping }: { rows: Row[]; allPeriods: string[]; grouping: string }) {
  const totals: Record<string, Record<string, number>> = {}
  for (const r of rows) {
    const g = nwGroup(String(r.accounts_type)), p = String(r.period), v = Number(r.balance_eur ?? 0)
    if (!totals[g]) totals[g] = {}
    totals[g][p] = (totals[g][p] ?? 0) + v
  }

  const periodData = allPeriods.map(p => {
    const assets = NW_ASSET_GROUPS.reduce((s,g) => s+(totals[g]?.[p]??0),0)
    const liab   = NW_LIAB_GROUPS.reduce((s,g)  => s+(totals[g]?.[p]??0),0)
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const row: Record<string, any> = { period: p, total_assets: assets, total_liabilities: liab, net_worth: assets + liab }
    NW_ASSET_GROUPS.forEach(g => { row[g] = totals[g]?.[p]??0 })
    NW_LIAB_GROUPS.forEach(g  => { row[g] = totals[g]?.[p]??0 })
    return row
  })
  const { sorted: sortedPeriods, sortKey: nwSK, sortDir: nwSD, toggleSort: nwSort } = useSortTable(periodData, 'period', 'desc')

  return (
    <div className="space-y-3">
      <WithCopy>
      <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-300px)] text-xs">
        <table className="w-full border-collapse">
          <thead className="sticky top-0 z-10 bg-slate-50">
            <tr className="bg-slate-50">
              <ColHeader label="Period" sortKey="period" currentKey={nwSK} currentDir={nwSD} onSort={nwSort} align="left" className="border-b border-slate-200 sticky left-0 bg-slate-50 min-w-16" />
              {NW_ASSET_GROUPS.map(g => <ColHeader key={g} label={g} sortKey={g} currentKey={nwSK} currentDir={nwSD} onSort={nwSort} align="right" className="border-b border-slate-200 whitespace-nowrap" />)}
              {NW_LIAB_GROUPS.map(g => <ColHeader key={g} label={g} sortKey={g} currentKey={nwSK} currentDir={nwSD} onSort={nwSort} align="right" className="border-b border-slate-200 whitespace-nowrap text-red-600" />)}
              <ColHeader label="Total Assets" sortKey="total_assets" currentKey={nwSK} currentDir={nwSD} onSort={nwSort} align="right" className="border-b border-slate-200 whitespace-nowrap border-l border-slate-300" />
              <ColHeader label="Total Liabilities" sortKey="total_liabilities" currentKey={nwSK} currentDir={nwSD} onSort={nwSort} align="right" className="border-b border-slate-200 whitespace-nowrap text-red-600" />
              <ColHeader label="Net Worth" sortKey="net_worth" currentKey={nwSK} currentDir={nwSD} onSort={nwSort} align="right" className="border-b border-slate-200 whitespace-nowrap text-blue-700" />
            </tr>
          </thead>
          <tbody>
            {sortedPeriods.map(pr => {
              const { period: p, total_assets: assets, total_liabilities: liab, net_worth: nw } = pr
              return (
                <tr key={p} className="border-b border-slate-100 hover:bg-slate-50">
                  <td className="px-2 py-1.5 font-medium sticky left-0 bg-white">{fmtPeriodHeader(p, grouping)}</td>
                  {NW_ASSET_GROUPS.map(g => <td key={g} className="text-right px-2 py-1.5 tabular-nums">{fmtEur(pr[g])}</td>)}
                  {NW_LIAB_GROUPS.map(g => {
                    const v = pr[g]
                    return <td key={g} className={`text-right px-2 py-1.5 tabular-nums ${v < 0 ? 'text-red-600' : ''}`}>{fmtEur(v)}</td>
                  })}
                  <td className="text-right px-2 py-1.5 tabular-nums font-medium text-blue-700 border-l border-slate-200">{fmtEur(assets)}</td>
                  <td className={`text-right px-2 py-1.5 tabular-nums font-medium ${liab < 0 ? 'text-red-600' : ''}`}>{fmtEur(liab)}</td>
                  <td className={`text-right px-2 py-1.5 tabular-nums font-bold ${nw >= 0 ? 'text-green-700' : 'text-red-600'}`}>{fmtEur(nw)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      </WithCopy>
    </div>
  )
}

function NwDetailAnalysis({ rows, allPeriods, grouping }: { rows: Row[]; allPeriods: string[]; accountMeta: Record<string, string>; grouping: string }) {
  const { isDark } = useTheme()
  const [selectedPeriod, setSelectedPeriod] = usePersist('nw_detail_period', '')
  const period = (selectedPeriod && allPeriods.includes(selectedPeriod)) ? selectedPeriod : (allPeriods[allPeriods.length - 1] ?? '')
  const periodRows = rows.filter(r => String(r.period) === period)
  const byGroup: Record<string, number> = {}
  for (const r of periodRows) {
    const g = nwGroup(String(r.accounts_type))
    byGroup[g] = (byGroup[g] ?? 0) + Number(r.balance_eur ?? 0)
  }
  const assets = NW_ASSET_GROUPS.reduce((s,g) => s+(byGroup[g]??0),0)
  const liab   = NW_LIAB_GROUPS.reduce((s,g)  => s+(byGroup[g]??0),0)
  const nw = assets + liab
  const donutGroups = NW_ASSET_GROUPS.filter(g => (byGroup[g] ?? 0) > 0)
  return (
    <div className="space-y-4">
      <div>
        <label className="text-xs font-medium text-slate-500 block mb-1">Select Period:</label>
        <select className="rounded-md border border-slate-300 px-3 py-1.5 text-sm w-48"
          value={period} onChange={e => setSelectedPeriod(e.target.value)}>
          {[...allPeriods].reverse().map(p => <option key={p} value={p}>{fmtPeriodHeader(p, grouping)}</option>)}
        </select>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        <KpiCard label="Net Worth" value={fmtEur(nw)} color={nw >= 0 ? 'text-blue-700' : 'text-red-600'} />
        {NW_ASSET_GROUPS.map(g => <KpiCard key={g} label={g} value={fmtEur(byGroup[g] ?? 0)} />)}
        <KpiCard label="Liabilities" value={fmtEur(liab)} color={liab < 0 ? 'text-red-600' : ''} />
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6 items-start">
        <div>
          <h3 className="text-sm font-semibold text-slate-700 mb-2">Net Worth Breakdown — {fmtPeriodHeader(period, grouping)}</h3>
          <Plot
            data={[{ values: donutGroups.map(g => byGroup[g]??0), labels: donutGroups, type: 'pie' as const, hole: 0.45,
              marker: { colors: donutGroups.map(g => NW_GROUP_COLORS[g]) },
              textinfo: 'label+percent' as const, hovertemplate: '%{label}: €%{value:,.2f}<extra></extra>' }]}
            layout={{ height: 340, margin: { t: 10, b: 10, l: 10, r: 10 }, showlegend: false, ...plotLayout(isDark) }}
            config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }} />
        </div>
        <WithCopy>
          <table className="w-full text-sm border-collapse">
            <thead><tr className="bg-slate-50 text-xs text-slate-500 uppercase">
              <th className="px-3 py-2 text-left">Category</th>
              <th className="px-3 py-2 text-right">Value (€)</th>
              <th className="px-3 py-2 text-right">% of NW</th>
            </tr></thead>
            <tbody className="divide-y divide-slate-100">
              {NW_ALL_GROUPS.map(g => {
                const v = byGroup[g] ?? 0
                if (v === 0) return null
                const pct = nw !== 0 ? (v / nw * 100).toFixed(2) + '%' : '—'
                return (
                  <tr key={g} className="hover:bg-slate-50">
                    <td className="px-3 py-2 flex items-center gap-2">
                      <span className="w-3 h-3 rounded-sm inline-block shrink-0" style={{ backgroundColor: NW_GROUP_COLORS[g] }} />
                      {g}
                    </td>
                    <td className={`px-3 py-2 text-right tabular-nums ${v < 0 ? 'text-red-600' : ''}`}>{fmtEur(v)}</td>
                    <td className={`px-3 py-2 text-right tabular-nums ${v < 0 ? 'text-red-600' : ''}`}>{pct}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </WithCopy>
      </div>
    </div>
  )
}

function NetWorthSection() {
  const [tab, setTab] = usePersist('nw_tab', 'Overview')
  const [startDate, setStartDate] = usePersist('nw_startDate', '2000-01-01')
  const today = new Date().toISOString().slice(0, 10)
  const [endDate, setEndDate] = usePersist('nw_endDate', today)
  const [grouping, setGrouping] = usePersist<'year'|'quarter'|'month'>('nw_grouping', 'year')
  const [showZeroBalance, setShowZeroBalance] = usePersist('nw_showZeroBalance', false)
  // Unlike Dashboard's own "Show Disabled" toggle (which defaults off, since it's a current-
  // snapshot view), this report is historical — closed/inactive accounts still had real
  // balances in the past, so excluding them by default would silently understate history.
  const [showInactive, setShowInactive] = usePersist('nw_showInactive', true)
  const [ytdMode, setYtdMode] = usePersist('nw_ytdMode', false)

//  const ytdStart = `${today.slice(0, 4)}-01-01`
  const ytdStart = `${parseInt(today.slice(0, 4)) - 1}-12-31`;
  const effStart   = ytdMode ? ytdStart : startDate
  const effEnd     = ytdMode ? today    : endDate
  const effGrouping: 'year'|'quarter'|'month' = ytdMode ? 'month' : grouping
  const [presetAccountIds, setPresetAccountIds] = useState<number[] | undefined>(undefined)

  const { data: rawData = [], isLoading } = useQuery({
    queryKey: ['nw-by-account', effStart, effEnd, effGrouping],
    queryFn: () => getNetWorthByAccount(effStart, effEnd, effGrouping),
  })
  // is_baseline rows are a dedicated "as of Start Date" data point used only for the Overview
  // KPI's delta calc. is_display_period marks the genuine chart/table buckets — usually the
  // complement of is_baseline, but when Start Date falls exactly on a bucket boundary (e.g.
  // Year grouping with a Dec 31 start) the backend returns ONE row that's both, so it's
  // filtered independently here rather than assuming the two are mutually exclusive.
  const allRows = useMemo(() => (rawData as Row[]).filter(r => r.is_display_period), [rawData])
  const baselineRowsRaw = useMemo(() => (rawData as Row[]).filter(r => r.is_baseline), [rawData])

  const accountMeta = useMemo(() => {
    const m: Record<string, string> = {}
    for (const r of allRows) m[String(r.accounts_name)] = String(r.accounts_type)
    return m
  }, [allRows])
  const accountActive = useMemo(() => {
    const m: Record<string, boolean> = {}
    for (const r of allRows) m[String(r.accounts_name)] = r.is_active !== false
    return m
  }, [allRows])
  const allPeriods = useMemo(() => [...new Set(allRows.map(r => String(r.period)))].sort(), [allRows])

  // presetAccountIds === undefined means "Full Portfolio" (the preset bar's own
  // sentinel) — include everything, matching this report's original default.
  const isIncluded = useCallback((r: Row) =>
    presetAccountIds === undefined || presetAccountIds.includes(Number(r.accounts_id)), [presetAccountIds])

  const accountTotals = useMemo(() => {
    const t: Record<string, number> = {}
    for (const r of allRows) { const n = String(r.accounts_name); t[n] = (t[n]??0) + Math.abs(Number(r.balance_eur??0)) }
    return t
  }, [allRows])
  const isZero = (name: string) => (accountTotals[name] ?? 0) < 0.01

  const filteredRows = useMemo(() =>
    allRows.filter(r => {
      const n = String(r.accounts_name)
      return isIncluded(r) && (showZeroBalance || !isZero(n)) && (showInactive || accountActive[n] !== false)
    }), [allRows, isIncluded, showZeroBalance, showInactive, accountActive])

  // Preset and active/inactive filters apply here too, but deliberately NOT the zero-balance
  // one: isZero() classifies an account by summing its balance across allRows' sampled dates,
  // which differ by grouping (Year sampling only checks each Dec 31, Quarter checks every
  // quarter-end, etc.) — so the same account could be "zero" under one grouping's sample and
  // not under another's, silently changing which accounts count toward the KPI's Start Date
  // baseline depending on the Year/Quarter/Month toggle. The zero filter's actual job (hiding
  // uninteresting rows from the chart/table) doesn't apply here anyway since baseline rows are
  // never rendered as their own row — only summed into one KPI number.
  const filteredBaselineRows = useMemo(() =>
    baselineRowsRaw.filter(r => {
      const n = String(r.accounts_name)
      return isIncluded(r) && (showInactive || accountActive[n] !== false)
    }), [baselineRowsRaw, isIncluded, showInactive, accountActive])

  const hiddenZeroCount = useMemo(() =>
    allRows.filter((r, i, arr) => arr.findIndex(x => x.accounts_name === r.accounts_name) === i)
      .filter(r => isIncluded(r) && isZero(String(r.accounts_name)) && (showInactive || accountActive[String(r.accounts_name)] !== false)).length,
    [allRows, isIncluded, showZeroBalance, showInactive, accountActive])

  const hiddenInactiveCount = useMemo(() =>
    allRows.filter((r, i, arr) => arr.findIndex(x => x.accounts_name === r.accounts_name) === i)
      .filter(r => isIncluded(r) && accountActive[String(r.accounts_name)] === false).length,
    [allRows, isIncluded, accountActive])

  return (
    <div className="space-y-3">
      {/* Controls */}
      <div className="flex flex-wrap items-center gap-3 pb-3 border-b border-slate-100">
        <ChkBox label="YTD" checked={ytdMode} onChange={setYtdMode} />
        <div className={`flex items-center gap-1.5 ${ytdMode ? 'opacity-40 pointer-events-none' : ''}`}>
          <label className="text-xs text-slate-500 whitespace-nowrap">Start Date</label>
          <input type="date" className="rounded border border-slate-300 px-2 py-1 text-xs" value={effStart} onChange={e => setStartDate(e.target.value)} />
        </div>
        <div className={`flex items-center gap-1.5 ${ytdMode ? 'opacity-40 pointer-events-none' : ''}`}>
          <label className="text-xs text-slate-500 whitespace-nowrap">End Date</label>
          <input type="date" className="rounded border border-slate-300 px-2 py-1 text-xs" value={effEnd} onChange={e => setEndDate(e.target.value)} />
        </div>
        <div className={`flex rounded border border-slate-300 overflow-hidden text-xs ${ytdMode ? 'opacity-40 pointer-events-none' : ''}`}>
          {(['year','quarter','month'] as const).map(g => (
            <button key={g} onClick={() => setGrouping(g)}
              className={`px-3 py-1 font-medium capitalize ${effGrouping === g ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 hover:bg-slate-50'}`}>
              {g.charAt(0).toUpperCase() + g.slice(1)}
            </button>
          ))}
        </div>
        <ChkBox label="Show zero-balance accounts" checked={showZeroBalance} onChange={setShowZeroBalance} />
        <ChkBox label="Show inactive accounts" checked={showInactive} onChange={setShowInactive} />
      </div>

      {/* Account Selection */}
      <PortfolioPresetBar reportScope="net_worth" eligibleTypes={ALL_ACCOUNT_TYPES} onChange={setPresetAccountIds} />

      {/* Zero-balance warning */}
      {!showZeroBalance && hiddenZeroCount > 0 && (
        <div className="flex items-center gap-2 px-4 py-2 bg-amber-50 border border-amber-200 rounded-lg text-base text-amber-800">
          <span>⚠️ {hiddenZeroCount} selected account(s) have zero balance and might be hidden (enable 'Show zero-balance accounts' or click </span>
          <button onClick={() => setShowZeroBalance(true)} className="text-blue-600 hover:underline whitespace-nowrap">🔄 Refresh Data</button>
          <span>)</span>
        </div>
      )}

      {/* Inactive-accounts warning */}
      {!showInactive && hiddenInactiveCount > 0 && (
        <div className="flex items-center gap-2 px-4 py-2 bg-amber-50 border border-amber-200 rounded-lg text-base text-amber-800">
          <span>⚠️ {hiddenInactiveCount} selected account(s) are inactive and excluded — this may not match Dashboard, which has its own separate "Show Disabled" toggle (enable 'Show inactive accounts' here to include them).</span>
        </div>
      )}

      {/* Sub-tabs */}
      <SubTabs tabs={['Overview', 'Account Balances', 'Summary per Type', 'Detail Analysis']} active={String(tab)} onChange={v => setTab(v as typeof tab)} />

      {isLoading
        ? <div className="flex justify-center py-12"><Spinner /></div>
        : <>
            {tab === 'Overview'          && <NwOverview rows={filteredRows} baselineRows={filteredBaselineRows} allPeriods={allPeriods} grouping={effGrouping} />}
            {tab === 'Account Balances'  && <NwAccountBalances rows={filteredRows} allPeriods={allPeriods} accountMeta={Object.fromEntries(Object.entries(accountMeta).filter(([n]) => filteredRows.some(r => String(r.accounts_name) === n)))} grouping={effGrouping} />}
            {tab === 'Summary per Type'  && <NwSummaryByType rows={filteredRows} allPeriods={allPeriods} grouping={effGrouping} />}
            {tab === 'Detail Analysis'   && <NwDetailAnalysis rows={filteredRows} allPeriods={allPeriods} accountMeta={accountMeta} grouping={effGrouping} />}
          </>
      }
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════════
// 2. INVESTMENT POSITIONS
// ════════════════════════════════════════════════════════════════════════════
function InvPositionsGraph({ startDate, accountIds }: { startDate: string; accountIds?: number[] }) {
  const { isDark } = useTheme()
  const liveRefetchMs = useLiveRefetchInterval()
  const { data = [], isLoading } = useQuery({
    queryKey: ['inv-positions-history', startDate, accountIds],
    queryFn: () => getInvestmentPositionsHistory(startDate, accountIds),
    refetchInterval: liveRefetchMs,
  })
  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  const rows = data as Row[]
  const accounts = [...new Set(rows.map(r => String(r.accounts_name)))]
  const dates = [...new Set(rows.map(r => String(r.date)))].sort()
  const lookup: Record<string, Record<string, number>> = {}
  for (const r of rows) {
    const a = String(r.accounts_name); const d = String(r.date)
    if (!lookup[a]) lookup[a] = {}
    lookup[a][d] = Number(r.value_eur ?? 0)
  }
  // Forward-fill each account only between its first and last data points.
  // Before the first point → null (account not yet open).
  // After the last point  → null (account closed / no holdings).
  // Gaps in between       → carry the last known value (sparse snapshots).
  function forwardFill(a: string): (number | null)[] {
    const lastIdx = dates.reduce((max, d, i) => (lookup[a]?.[d] != null ? i : max), -1)
    let last: number | null = null
    return dates.map((d, i) => {
      if (lookup[a]?.[d] != null) last = lookup[a][d]
      return i <= lastIdx ? last : null
    })
  }
  const colors = ['#3b82f6', '#10b981', '#f59e0b', '#8b5cf6', '#ef4444', '#06b6d4']
  const filled = Object.fromEntries(accounts.map(a => [a, forwardFill(a)]))
  const traces = accounts.map((a, i) => ({
    x: dates, y: filled[a],
    name: a, type: 'scatter' as const, mode: 'lines' as const,
    line: { color: colors[i % colors.length], width: 1.5 },
    connectgaps: false,
  }))
  const totalByDate = dates.map((_, i) => accounts.reduce((s, a) => s + (filled[a][i] ?? 0), 0))
  traces.push({ x: dates, y: totalByDate, name: 'Total', type: 'scatter', mode: 'lines', line: { color: '#1e3a8a', width: 2.5, dash: 'dot' } as unknown as typeof traces[0]['line'], connectgaps: false })
  const latestTotal = totalByDate[totalByDate.length - 1] ?? 0
  return (
    <div className="space-y-4">
      <KpiCard label="Current Portfolio Value" value={fmtEur(latestTotal)} color="text-blue-700" />
      <Plot data={traces}
        layout={{ height: 380, margin: { t: 10, r: 10, b: 160, l: 70 }, yaxis: { tickformat: ',.0f', tickprefix: '€' }, legend: { orientation: 'h', y: -0.45, x: 0.5, xanchor: 'center' }, hovermode: 'x unified', ...plotLayout(isDark) }}
        config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }} />
    </div>
  )
}

function InvPositionsSummary({ startDate, accountIds }: { startDate: string; accountIds?: number[] }) {
  const liveRefetchMs = useLiveRefetchInterval()
  const { data = [], isLoading } = useQuery({
    queryKey: ['inv-positions-history', startDate, accountIds],
    queryFn: () => getInvestmentPositionsHistory(startDate, accountIds),
    refetchInterval: liveRefetchMs,
  })
  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  const rows = data as Row[]
  return <PivotTable data={rows} groupBy="accounts_name" colKey="date" valKey="value_eur" showTotal={false} idKey="accounts_id" linkType="Brokerage" />
}

function FxExposureTab({ accountIds }: { accountIds?: number[] }) {
  const { isDark } = useTheme()
  const liveRefetchMs = useLiveRefetchInterval()
  const { data = [], isLoading } = useQuery({ queryKey: ['fx-exposure', accountIds], queryFn: () => getFxExposure(accountIds), refetchInterval: liveRefetchMs })
  const rows = data as Row[]
  const { sorted: fxSorted, sortKey: fxSK, sortDir: fxSD, toggleSort: fxSort } = useSortTable(rows, 'eur_exposure', 'desc')
  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  return (
    <div className="space-y-4">
      <Plot
        data={[{ x: rows.map(r => Number(r.eur_exposure)), y: rows.map(r => String(r.currency)), type: 'bar', orientation: 'h', marker: { color: '#8b5cf6' }, text: rows.map(r => fmtEur(Number(r.eur_exposure))), textposition: 'outside' }]}
        layout={{ height: Math.max(240, rows.length * 40), margin: { t: 10, r: 100, b: 40, l: 60 }, xaxis: { tickformat: ',.0f', tickprefix: '€' }, ...plotLayout(isDark) }}
        config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }} />
      <WithCopy>
      <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-300px)] text-xs">
        <table className="w-full border-collapse">
          <thead className="sticky top-0 z-10 bg-slate-50">
            <tr className="bg-slate-50 text-xs text-slate-500">
              <ColHeader label="Currency" sortKey="currency" currentKey={fxSK} currentDir={fxSD} onSort={fxSort} className="text-left px-2 py-1.5 border-b border-slate-200" />
              <ColHeader label="Native Exposure" sortKey="native_exposure" currentKey={fxSK} currentDir={fxSD} onSort={fxSort} align="right" className="px-2 py-1.5 border-b border-slate-200" />
              <ColHeader label="EUR Exposure" sortKey="eur_exposure" currentKey={fxSK} currentDir={fxSD} onSort={fxSort} align="right" className="px-2 py-1.5 border-b border-slate-200" />
              <ColHeader label="5% FX Move Impact" sortKey="sensitivity_5pct_eur" currentKey={fxSK} currentDir={fxSD} onSort={fxSort} align="right" className="px-2 py-1.5 border-b border-slate-200" />
            </tr>
          </thead>
          <tbody>
            {fxSorted.map((r, i) => (
              <tr key={i} className="border-b border-slate-100 hover:bg-slate-50">
                <td className="px-2 py-1.5 font-mono font-medium">{String(r.currency)}</td>
                <td className="px-2 py-1.5 text-right tabular-nums">{fmtNum(Number(r.native_exposure), 2)}</td>
                <td className="px-2 py-1.5 text-right tabular-nums">{fmtEur(Number(r.eur_exposure))}</td>
                <td className="px-2 py-1.5 text-right tabular-nums text-amber-600">{fmtEur(Number(r.sensitivity_5pct_eur))}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      </WithCopy>
    </div>
  )
}

// ── Portfolio X-Ray ──────────────────────────────────────────────────────────
// Look-through ETF/Mutual Fund composition (cached via "Download Fund Composition"
// on MarketData/SecurityDetail Downloads tabs) blended with direct stock/bond
// holdings — see api/routers/reports.py's xray/* endpoints for the blend logic.

const SECTOR_NO_INDUSTRY_BUCKET = 'Fund Look-Through (No Industry Data)'

function XraySectorWeightingTab({ accountIds, compareDate }: { accountIds?: number[]; compareDate?: string }) {
  const { isDark } = useTheme()
  const liveRefetchMs = useLiveRefetchInterval()
  const { data, isLoading } = useQuery({ queryKey: ['xray', 'sector-weighting', accountIds, compareDate], queryFn: () => getXraySectorWeighting(accountIds, compareDate), refetchInterval: liveRefetchMs })
  const resp = data as { summary: Row[]; detail: Row[]; compare?: { summary: Row[] }; compare_date?: string } | undefined
  const rows = resp?.summary ?? []
  const detail = resp?.detail ?? []
  const compareMap = useMemo(() => {
    const m: Record<string, Row> = {}
    for (const r of resp?.compare?.summary ?? []) m[String(r.sector)] = r
    return m
  }, [resp])
  const { sorted, sortKey, sortDir, toggleSort } = useSortTable(rows, 'value_eur', 'desc')
  const [selectedSector, setSelectedSector] = useState<string | null>(null)
  const [selectedIndustry, setSelectedIndustry] = useState<string | null>(null)
  const selectSector = (sec: string) => {
    setSelectedSector(c => c === sec ? null : sec)
    setSelectedIndustry(null)
  }
  const sectorDetailRows = selectedSector ? detail.filter(r => String(r.sector) === selectedSector) : []
  const sectorTotal = sectorDetailRows.reduce((s, r) => s + Number(r.value_eur ?? 0), 0)

  // Industry-level rollup within the selected sector — direct holdings carry a
  // real Industry; fund look-through rows (Yahoo's sector weightings have no
  // industry breakdown) group into one bucket instead of being dropped.
  const industryRows = useMemo(() => {
    const byIndustry = new Map<string, number>()
    for (const r of sectorDetailRows) {
      const ind = r.industry ? String(r.industry) : SECTOR_NO_INDUSTRY_BUCKET
      byIndustry.set(ind, (byIndustry.get(ind) ?? 0) + Number(r.value_eur ?? 0))
    }
    return [...byIndustry.entries()]
      .map(([industry, value_eur]) => ({ industry, value_eur, pct: sectorTotal ? value_eur / sectorTotal * 100 : 0 }))
      .sort((a, b) => b.value_eur - a.value_eur)
  }, [sectorDetailRows, sectorTotal])

  const industryDetailRows = selectedIndustry
    ? sectorDetailRows.filter(r => (r.industry ? String(r.industry) : SECTOR_NO_INDUSTRY_BUCKET) === selectedIndustry)
    : []
  const industryTotal = industryDetailRows.reduce((s, r) => s + Number(r.value_eur ?? 0), 0)

  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  return (
    <div className="space-y-4">
      <Plot
        data={[{
          x: rows.map(r => Number(r.value_eur)), y: rows.map(r => String(r.sector)),
          type: 'bar', orientation: 'h', text: rows.map(r => fmtEur(Number(r.value_eur))), textposition: 'outside',
          marker: { color: rows.map(r => String(r.sector) === selectedSector ? '#1d4ed8' : '#3b82f6') },
        }]}
        layout={{ height: Math.max(280, rows.length * 32), margin: { t: 10, r: 100, b: 40, l: 220 }, xaxis: { tickformat: ',.0f', tickprefix: '€' }, ...plotLayout(isDark) }}
        config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }}
        onClick={(e: { points?: { y?: string }[] }) => {
          const label = e?.points?.[0]?.y
          if (label) selectSector(label)
        }} />
      <WithCopy>
      <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-300px)] text-xs">
        <table className="w-full border-collapse">
          <thead className="sticky top-0 z-10 bg-slate-50">
            <tr className="bg-slate-50 text-xs text-slate-500">
              <ColHeader label="Sector" sortKey="sector" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} className="text-left px-2 py-1.5 border-b border-slate-200" />
              <ColHeader label="Value (€)" sortKey="value_eur" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} align="right" className="px-2 py-1.5 border-b border-slate-200" />
              <ColHeader label="Weight %" sortKey="pct" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} align="right" className="px-2 py-1.5 border-b border-slate-200" />
              {compareDate && <>
                <th className="text-right px-2 py-1.5 border-b border-slate-200 bg-amber-50">As of {compareDate} (€)</th>
                <th className="text-right px-2 py-1.5 border-b border-slate-200 bg-amber-50">Δ Value (€)</th>
                <th className="text-right px-2 py-1.5 border-b border-slate-200 bg-amber-50">Δ pp</th>
              </>}
            </tr>
          </thead>
          <tbody>
            {sorted.map((r, i) => {
              const sec = String(r.sector)
              const cmp = compareMap[sec]
              const cmpValue = cmp ? Number(cmp.value_eur) : null
              const cmpDeltaValue = cmpValue != null ? Number(r.value_eur) - cmpValue : null
              const cmpDeltaPct = cmp ? Number(r.pct) - Number(cmp.pct) : null
              return (
                <tr key={i} onClick={() => selectSector(sec)}
                  className={`border-b border-slate-100 hover:bg-slate-50 cursor-pointer ${selectedSector === sec ? 'bg-blue-50' : ''}`}>
                  <td className="px-2 py-1.5">{sec}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums">{fmtEur(Number(r.value_eur))}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{fmtPct(Number(r.pct))}</td>
                  {compareDate && <>
                    <td className="px-2 py-1.5 text-right tabular-nums text-slate-500 bg-amber-50/50">{cmpValue != null ? fmtEur(cmpValue) : '—'}</td>
                    <td className={`px-2 py-1.5 text-right tabular-nums font-medium bg-amber-50/50 ${cmpDeltaValue != null && cmpDeltaValue > 0 ? 'text-green-600' : cmpDeltaValue != null && cmpDeltaValue < 0 ? 'text-red-500' : ''}`}>
                      {cmpDeltaValue != null ? `${cmpDeltaValue > 0 ? '+' : ''}${fmtEur(cmpDeltaValue)}` : '—'}
                    </td>
                    <td className={`px-2 py-1.5 text-right tabular-nums font-medium bg-amber-50/50 ${cmpDeltaPct != null && cmpDeltaPct > 0 ? 'text-green-600' : cmpDeltaPct != null && cmpDeltaPct < 0 ? 'text-red-500' : ''}`}>
                      {cmpDeltaPct != null ? `${cmpDeltaPct > 0 ? '+' : ''}${cmpDeltaPct.toFixed(2)}%` : '—'}
                    </td>
                  </>}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      </WithCopy>

      {selectedSector && (
        <div>
          <div className="flex items-center justify-between mb-1">
            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide">Industries in "{selectedSector}"</p>
            <button onClick={() => { setSelectedSector(null); setSelectedIndustry(null) }} className="text-xs text-slate-400 hover:text-slate-600 underline">Clear</button>
          </div>
          <WithCopy>
          <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-500px)] text-xs">
            <table className="w-full border-collapse">
              <thead className="sticky top-0 z-10 bg-slate-50">
                <tr className="bg-slate-50 text-xs text-slate-500">
                  <th className="text-left px-2 py-1.5 border-b border-slate-200">Industry</th>
                  <th className="text-right px-2 py-1.5 border-b border-slate-200">Value (€)</th>
                  <th className="text-right px-2 py-1.5 border-b border-slate-200">Weight % (of {selectedSector})</th>
                </tr>
              </thead>
              <tbody>
                {industryRows.map((r, i) => (
                  <tr key={i} onClick={() => setSelectedIndustry(c => c === r.industry ? null : r.industry)}
                    className={`border-b border-slate-100 hover:bg-slate-50 cursor-pointer ${selectedIndustry === r.industry ? 'bg-blue-50' : ''}`}>
                    <td className="px-2 py-1.5">{r.industry}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums">{fmtEur(r.value_eur)}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{fmtPct(r.pct)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          </WithCopy>
        </div>
      )}

      {selectedIndustry && (
        <div>
          <div className="flex items-center justify-between mb-1">
            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide">Securities in "{selectedIndustry}" · {selectedSector}</p>
            <button onClick={() => setSelectedIndustry(null)} className="text-xs text-slate-400 hover:text-slate-600 underline">Clear</button>
          </div>
          <WithCopy>
          <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-500px)] text-xs">
            <table className="w-full border-collapse">
              <thead className="sticky top-0 z-10 bg-slate-50">
                <tr className="bg-slate-50 text-xs text-slate-500">
                  <th className="text-left px-2 py-1.5 border-b border-slate-200">Security</th>
                  <th className="text-right px-2 py-1.5 border-b border-slate-200">Value (€)</th>
                  <th className="text-right px-2 py-1.5 border-b border-slate-200">Weight % (of {selectedIndustry})</th>
                </tr>
              </thead>
              <tbody>
                {industryDetailRows.map((r, i) => (
                  <tr key={i} className="border-b border-slate-100 hover:bg-slate-50">
                    <td className="px-2 py-1.5"><SecLink id={r.securities_id}>{String(r.name)}</SecLink></td>
                    <td className="px-2 py-1.5 text-right tabular-nums">{fmtEur(Number(r.value_eur))}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{fmtPct(industryTotal ? Number(r.value_eur) / industryTotal * 100 : 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          </WithCopy>
        </div>
      )}
    </div>
  )
}

function XrayStyleBoxTab({ accountIds, compareDate }: { accountIds?: number[]; compareDate?: string }) {
  const { isDark } = useTheme()
  const liveRefetchMs = useLiveRefetchInterval()
  const { data, isLoading } = useQuery({ queryKey: ['xray', 'style-box', accountIds, compareDate], queryFn: () => getXrayStyleBox(accountIds, compareDate), refetchInterval: liveRefetchMs })
  const resp = data as { summary: Row[]; detail: Row[]; compare?: { summary: Row[] }; compare_date?: string } | undefined
  const rows = resp?.summary ?? []
  const detail = resp?.detail ?? []
  const compareMap = useMemo(() => {
    const m: Record<string, Row> = {}
    for (const r of resp?.compare?.summary ?? []) m[String(r.style)] = r
    return m
  }, [resp])
  const { sorted, sortKey, sortDir, toggleSort } = useSortTable(rows, 'value_eur', 'desc')
  const [selectedStyle, setSelectedStyle] = useState<string | null>(null)
  const detailRows = selectedStyle ? detail.filter(r => String(r.style) === selectedStyle) : []

  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  return (
    <div className="space-y-4">
      <Plot
        data={[{
          x: rows.map(r => Number(r.value_eur)), y: rows.map(r => String(r.style)),
          type: 'bar', orientation: 'h', text: rows.map(r => fmtEur(Number(r.value_eur))), textposition: 'outside',
          marker: { color: rows.map(r => String(r.style) === selectedStyle ? '#047857' : '#10b981') },
        }]}
        layout={{ height: Math.max(280, rows.length * 32), margin: { t: 10, r: 100, b: 40, l: 220 }, xaxis: { tickformat: ',.0f', tickprefix: '€' }, ...plotLayout(isDark) }}
        config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }}
        onClick={(e: { points?: { y?: string }[] }) => {
          const label = e?.points?.[0]?.y
          if (label) setSelectedStyle(c => c === label ? null : label)
        }} />
      <WithCopy>
      <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-300px)] text-xs">
        <table className="w-full border-collapse">
          <thead className="sticky top-0 z-10 bg-slate-50">
            <tr className="bg-slate-50 text-xs text-slate-500">
              <ColHeader label="Category" sortKey="style" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} className="text-left px-2 py-1.5 border-b border-slate-200" />
              <ColHeader label="Value (€)" sortKey="value_eur" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} align="right" className="px-2 py-1.5 border-b border-slate-200" />
              <ColHeader label="Weight %" sortKey="pct" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} align="right" className="px-2 py-1.5 border-b border-slate-200" />
              {compareDate && <>
                <th className="text-right px-2 py-1.5 border-b border-slate-200 bg-amber-50">As of {compareDate} (€)</th>
                <th className="text-right px-2 py-1.5 border-b border-slate-200 bg-amber-50">Δ Value (€)</th>
                <th className="text-right px-2 py-1.5 border-b border-slate-200 bg-amber-50">Δ pp</th>
              </>}
            </tr>
          </thead>
          <tbody>
            {sorted.map((r, i) => {
              const style = String(r.style)
              const cmp = compareMap[style]
              const cmpValue = cmp ? Number(cmp.value_eur) : null
              const cmpDeltaValue = cmpValue != null ? Number(r.value_eur) - cmpValue : null
              const cmpDeltaPct = cmp ? Number(r.pct) - Number(cmp.pct) : null
              return (
                <tr key={i} onClick={() => setSelectedStyle(c => c === style ? null : style)}
                  className={`border-b border-slate-100 hover:bg-slate-50 cursor-pointer ${selectedStyle === style ? 'bg-emerald-50' : ''}`}>
                  <td className="px-2 py-1.5">{style}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums">{fmtEur(Number(r.value_eur))}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{fmtPct(Number(r.pct))}</td>
                  {compareDate && <>
                    <td className="px-2 py-1.5 text-right tabular-nums text-slate-500 bg-amber-50/50">{cmpValue != null ? fmtEur(cmpValue) : '—'}</td>
                    <td className={`px-2 py-1.5 text-right tabular-nums font-medium bg-amber-50/50 ${cmpDeltaValue != null && cmpDeltaValue > 0 ? 'text-green-600' : cmpDeltaValue != null && cmpDeltaValue < 0 ? 'text-red-500' : ''}`}>
                      {cmpDeltaValue != null ? `${cmpDeltaValue > 0 ? '+' : ''}${fmtEur(cmpDeltaValue)}` : '—'}
                    </td>
                    <td className={`px-2 py-1.5 text-right tabular-nums font-medium bg-amber-50/50 ${cmpDeltaPct != null && cmpDeltaPct > 0 ? 'text-green-600' : cmpDeltaPct != null && cmpDeltaPct < 0 ? 'text-red-500' : ''}`}>
                      {cmpDeltaPct != null ? `${cmpDeltaPct > 0 ? '+' : ''}${cmpDeltaPct.toFixed(2)}%` : '—'}
                    </td>
                  </>}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      </WithCopy>

      {selectedStyle && (
        <div>
          <div className="flex items-center justify-between mb-1">
            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide">Securities in "{selectedStyle}"</p>
            <button onClick={() => setSelectedStyle(null)} className="text-xs text-slate-400 hover:text-slate-600 underline">Clear</button>
          </div>
          <WithCopy>
          <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-500px)] text-xs">
            <table className="w-full border-collapse">
              <thead className="sticky top-0 z-10 bg-slate-50">
                <tr className="bg-slate-50 text-xs text-slate-500">
                  <th className="text-left px-2 py-1.5 border-b border-slate-200">Security</th>
                  <th className="text-right px-2 py-1.5 border-b border-slate-200">Value (€)</th>
                  <th className="text-right px-2 py-1.5 border-b border-slate-200">Weight % (of {selectedStyle})</th>
                </tr>
              </thead>
              <tbody>
                {detailRows.map((r, i) => (
                  <tr key={i} className="border-b border-slate-100 hover:bg-slate-50">
                    <td className="px-2 py-1.5"><SecLink id={r.securities_id}>{String(r.name)}</SecLink></td>
                    <td className="px-2 py-1.5 text-right tabular-nums">{fmtEur(Number(r.value_eur))}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{fmtPct(Number(r.pct))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          </WithCopy>
        </div>
      )}
    </div>
  )
}

function XrayAssetAllocationTab({ accountIds, compareDate }: { accountIds?: number[]; compareDate?: string }) {
  const { isDark } = useTheme()
  const qc = useQueryClient()
  const liveRefetchMs = useLiveRefetchInterval()
  const { data, isLoading } = useQuery({ queryKey: ['xray', 'asset-allocation', accountIds, compareDate], queryFn: () => getXrayAssetAllocation(accountIds, compareDate), refetchInterval: liveRefetchMs })
  const { data: targets = [], isLoading: targetsLoading } = useQuery({ queryKey: ['xray-allocation-targets'], queryFn: getXrayAssetAllocationTargets })
  const resp = data as { summary: Row[]; detail: Row[]; compare?: { summary: Row[] }; compare_date?: string } | undefined
  const rows = resp?.summary ?? []
  const detail = resp?.detail ?? []
  const compareMap = useMemo(() => {
    const m: Record<string, Row> = {}
    for (const r of resp?.compare?.summary ?? []) m[String(r.asset_class)] = r
    return m
  }, [resp])
  const [selectedClass, setSelectedClass] = useState<string | null>(null)
  const [editOpen, setEditOpen] = useState(false)
  const detailRows = selectedClass ? detail.filter(r => String(r.asset_class) === selectedClass) : []
  const total = rows.reduce((s, r) => s + Number(r.value_eur ?? 0), 0)

  // Local editable target state, pre-filled from current holdings' classes and any
  // previously saved targets — same pattern as the older Allocation tab's editor.
  const [localTargets, setLocalTargets] = useState<Record<string, number>>({})
  useEffect(() => {
    if ((targets as Row[]).length > 0) {
      const m: Record<string, number> = {}
      ;(targets as Row[]).forEach(r => { m[String(r.asset_class)] = Number(r.target_pct) })
      setLocalTargets(m)
    }
  }, [targets])

  const saveMutation = useMutation({
    mutationFn: saveXrayAssetAllocationTargets,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['xray-allocation-targets'] })
      qc.invalidateQueries({ queryKey: ['xray', 'asset-allocation'] })
      setEditOpen(false)
    },
  })

  const sumTargets = Object.values(localTargets).reduce((s, v) => s + v, 0)
  const sumOk = Math.abs(sumTargets - 100) < 0.01

  if (isLoading || targetsLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  return (
    <div className="space-y-4">
      {/* Edit Target Allocations */}
      <div className="border border-slate-200 rounded-lg overflow-hidden">
        <button
          className="w-full flex items-center gap-2 px-4 py-3 bg-slate-50 hover:bg-slate-100 text-sm font-medium text-left"
          onClick={() => setEditOpen(v => !v)}
        >
          <span>{editOpen ? '▼' : '▶'}</span>
          <span>⚙️ Edit Target Allocations</span>
        </button>
        {editOpen && (
          <div className="p-4 space-y-3">
            <p className="text-xs text-slate-500">Rows are pre-filled from your current look-through holdings and any previously saved targets. All changes are saved on click.</p>
            <div className="overflow-x-auto text-xs">
              <table className="w-full border-collapse">
                <thead><tr className="bg-slate-50 text-slate-500">
                  <th className="text-left px-3 py-2 border-b border-slate-200">Asset Class</th>
                  <th className="text-right px-3 py-2 border-b border-slate-200">Actual %</th>
                  <th className="text-right px-3 py-2 border-b border-slate-200">Target %</th>
                </tr></thead>
                <tbody>
                  {rows.map((r, i) => {
                    const key = String(r.asset_class)
                    return (
                      <tr key={i} className="border-b border-slate-100">
                        <td className="px-3 py-2 font-medium">{key}</td>
                        <td className="px-3 py-2 text-right tabular-nums text-slate-500">{fmtPct(Number(r.pct))}</td>
                        <td className="px-3 py-2 text-right">
                          <input
                            type="number" min={0} max={100} step={0.5}
                            value={localTargets[key] ?? 0}
                            onChange={e => setLocalTargets(prev => ({ ...prev, [key]: parseFloat(e.target.value) || 0 }))}
                            className="w-20 text-right border border-slate-200 rounded px-2 py-0.5 text-xs focus:outline-none focus:ring-1 focus:ring-blue-400"
                          />
                          %
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
            <div className="flex items-center justify-between">
              <span className={`text-sm font-medium ${sumOk ? 'text-green-600' : 'text-red-500'}`}>
                Sum of targets: {sumTargets.toFixed(1)}% {sumOk ? '✓' : '✗ (must equal 100%)'}
              </span>
              <button
                disabled={!sumOk || saveMutation.isPending}
                onClick={() => saveMutation.mutate(localTargets)}
                className="px-4 py-1.5 bg-blue-600 text-white text-xs rounded hover:bg-blue-700 disabled:opacity-40"
              >
                {saveMutation.isPending ? 'Saving…' : 'Save Targets'}
              </button>
            </div>
          </div>
        )}
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <h3 className="text-sm font-semibold text-slate-600 mb-2">Current Allocation</h3>
          <Plot
            data={[{
              values: rows.map(r => Number(r.value_eur)), labels: rows.map(r => String(r.asset_class)),
              type: 'pie', hole: 0.45, textinfo: 'label+percent',
              pull: rows.map(r => String(r.asset_class) === selectedClass ? 0.06 : 0),
            }]}
            layout={{
              height: 360, margin: { t: 10, r: 10, b: 10, l: 10 }, showlegend: true, legend: { orientation: 'v' },
              annotations: [{ text: `<b>${fmtEur(total)}</b><br>Total`, showarrow: false, font: { size: 14 }, x: 0.5, y: 0.5 }],
              ...plotLayout(isDark),
            }}
            config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }}
            onClick={(e: { points?: { label?: string }[] }) => {
              const label = e?.points?.[0]?.label
              if (label) setSelectedClass(c => c === label ? null : label)
            }} />
        </div>
        <div>
          <h3 className="text-sm font-semibold text-slate-600 mb-2">Actual vs. Target (%)</h3>
          <Plot
            data={[
              { x: rows.map(r => String(r.asset_class)), y: rows.map(r => Number(r.pct)), name: 'Actual %', type: 'bar', marker: { color: '#3b82f6' } },
              { x: rows.map(r => String(r.asset_class)), y: rows.map(r => Number(r.target_pct)), name: 'Target %', type: 'bar', marker: { color: '#f59e0b' } },
            ]}
            layout={{ height: 360, margin: { t: 10, r: 10, b: 60, l: 40 }, barmode: 'group', yaxis: { title: '%' }, legend: { orientation: 'h', y: -0.3 }, ...plotLayout(isDark) }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: '100%' }}
          />
        </div>
      </div>

      <WithCopy>
      <div className="overflow-x-auto text-xs">
        <table className="w-full border-collapse">
          <thead><tr className="bg-slate-50 text-xs text-slate-500">
            <th className="text-left px-2 py-1.5 border-b border-slate-200">Asset Class</th>
            <th className="text-right px-2 py-1.5 border-b border-slate-200">Value (€)</th>
            <th className="text-right px-2 py-1.5 border-b border-slate-200">Actual %</th>
            <th className="text-right px-2 py-1.5 border-b border-slate-200">Target %</th>
            <th className="text-right px-2 py-1.5 border-b border-slate-200">Delta %</th>
            <th className="text-right px-2 py-1.5 border-b border-slate-200">Rebalance €</th>
            {compareDate && <>
              <th className="text-right px-2 py-1.5 border-b border-slate-200 bg-amber-50">As of {compareDate} (€)</th>
              <th className="text-right px-2 py-1.5 border-b border-slate-200 bg-amber-50">Δ Value (€)</th>
              <th className="text-right px-2 py-1.5 border-b border-slate-200 bg-amber-50">Δ %</th>
            </>}
          </tr></thead>
          <tbody>
            {rows.map((r, i) => {
              const cls = String(r.asset_class)
              const deltaPct = Number(r.delta_pct ?? 0)
              const reb = Number(r.rebalance_eur ?? 0)
              const cmp = compareMap[cls]
              const cmpValue = cmp ? Number(cmp.value_eur) : null
              const cmpDeltaValue = cmpValue != null ? Number(r.value_eur) - cmpValue : null
              const cmpDeltaPct = cmp ? Number(r.pct) - Number(cmp.pct) : null
              return (
                <tr key={i} onClick={() => setSelectedClass(c => c === cls ? null : cls)}
                  className={`border-b border-slate-100 hover:bg-slate-50 cursor-pointer ${selectedClass === cls ? 'bg-blue-50' : ''}`}>
                  <td className="px-2 py-1.5">{cls}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums">{fmtEur(Number(r.value_eur))}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{fmtPct(Number(r.pct))}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{fmtPct(Number(r.target_pct))}</td>
                  <td className={`px-2 py-1.5 text-right tabular-nums font-medium ${deltaPct > 0 ? 'text-red-500' : deltaPct < 0 ? 'text-green-600' : ''}`}>{deltaPct > 0 ? '+' : ''}{deltaPct.toFixed(2)}%</td>
                  <td className={`px-2 py-1.5 text-right tabular-nums font-medium ${reb > 0 ? 'text-green-600' : reb < 0 ? 'text-red-500' : ''}`}>{reb > 0 ? '+' : ''}{fmtEur(reb)}</td>
                  {compareDate && <>
                    <td className="px-2 py-1.5 text-right tabular-nums text-slate-500 bg-amber-50/50">{cmpValue != null ? fmtEur(cmpValue) : '—'}</td>
                    <td className={`px-2 py-1.5 text-right tabular-nums font-medium bg-amber-50/50 ${cmpDeltaValue != null && cmpDeltaValue > 0 ? 'text-green-600' : cmpDeltaValue != null && cmpDeltaValue < 0 ? 'text-red-500' : ''}`}>
                      {cmpDeltaValue != null ? `${cmpDeltaValue > 0 ? '+' : ''}${fmtEur(cmpDeltaValue)}` : '—'}
                    </td>
                    <td className={`px-2 py-1.5 text-right tabular-nums font-medium bg-amber-50/50 ${cmpDeltaPct != null && cmpDeltaPct > 0 ? 'text-green-600' : cmpDeltaPct != null && cmpDeltaPct < 0 ? 'text-red-500' : ''}`}>
                      {cmpDeltaPct != null ? `${cmpDeltaPct > 0 ? '+' : ''}${cmpDeltaPct.toFixed(2)}%` : '—'}
                    </td>
                  </>}
                </tr>
              )
            })}
          </tbody>
          <tfoot>
            <tr className="font-semibold text-slate-700">
              <td className="px-2 py-1.5 border-t border-slate-200">Total</td>
              <td className="px-2 py-1.5 text-right tabular-nums border-t border-slate-200">{fmtEur(total)}</td>
              <td className="px-2 py-1.5 text-right tabular-nums border-t border-slate-200">100.0%</td>
              <td className="px-2 py-1.5 border-t border-slate-200" colSpan={compareDate ? 6 : 3}></td>
            </tr>
          </tfoot>
        </table>
      </div>
      </WithCopy>

      {selectedClass && (
        <div>
          <div className="flex items-center justify-between mb-1">
            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide">Securities in "{selectedClass}"</p>
            <button onClick={() => setSelectedClass(null)} className="text-xs text-slate-400 hover:text-slate-600 underline">Clear</button>
          </div>
          <WithCopy>
          <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-500px)] text-xs">
            <table className="w-full border-collapse">
              <thead className="sticky top-0 z-10 bg-slate-50">
                <tr className="bg-slate-50 text-xs text-slate-500">
                  <th className="text-left px-2 py-1.5 border-b border-slate-200">Security</th>
                  <th className="text-right px-2 py-1.5 border-b border-slate-200">Value (€)</th>
                  <th className="text-right px-2 py-1.5 border-b border-slate-200">Weight % (of {selectedClass})</th>
                </tr>
              </thead>
              <tbody>
                {detailRows.map((r, i) => (
                  <tr key={i} className="border-b border-slate-100 hover:bg-slate-50">
                    <td className="px-2 py-1.5"><SecLink id={r.securities_id}>{String(r.name)}</SecLink></td>
                    <td className="px-2 py-1.5 text-right tabular-nums">{fmtEur(Number(r.value_eur))}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{fmtPct(Number(r.pct))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          </WithCopy>
        </div>
      )}
    </div>
  )
}

function XrayBondQualityTab({ accountIds, compareDate }: { accountIds?: number[]; compareDate?: string }) {
  const { isDark } = useTheme()
  const liveRefetchMs = useLiveRefetchInterval()
  const { data, isLoading } = useQuery({ queryKey: ['xray', 'bond-quality', accountIds, compareDate], queryFn: () => getXrayBondQuality(accountIds, compareDate), refetchInterval: liveRefetchMs })
  const resp = data as { summary: Row[]; detail: Row[]; us_government: Row | null; compare?: { summary: Row[] }; compare_date?: string } | undefined
  const rows = resp?.summary ?? []
  const detail = resp?.detail ?? []
  const usGov = resp?.us_government ?? null
  const compareMap = useMemo(() => {
    const m: Record<string, Row> = {}
    for (const r of resp?.compare?.summary ?? []) m[String(r.quality)] = r
    return m
  }, [resp])
  const [selectedQuality, setSelectedQuality] = useState<string | null>(null)
  const detailRows = selectedQuality ? detail.filter(r => String(r.quality) === selectedQuality) : []
  // Rows arrive highest-quality-first for the table; the horizontal bar chart plots
  // its first entry at the bottom, so reverse just for the chart to keep AAA on top.
  const chartRows = [...rows].reverse()

  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  return (
    <div className="space-y-4">
      <Plot
        data={[{
          x: chartRows.map(r => Number(r.value_eur)), y: chartRows.map(r => String(r.quality)),
          type: 'bar', orientation: 'h', text: chartRows.map(r => fmtEur(Number(r.value_eur))), textposition: 'outside',
          marker: { color: chartRows.map(r => String(r.quality) === selectedQuality ? '#b45309' : '#f59e0b') },
        }]}
        layout={{ height: Math.max(240, chartRows.length * 32), margin: { t: 10, r: 100, b: 40, l: 180 }, xaxis: { tickformat: ',.0f', tickprefix: '€' }, ...plotLayout(isDark) }}
        config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }}
        onClick={(e: { points?: { y?: string }[] }) => {
          const label = e?.points?.[0]?.y
          if (label) setSelectedQuality(c => c === label ? null : label)
        }} />
      <WithCopy>
      <div className="overflow-x-auto text-xs">
        <table className="w-full border-collapse">
          <thead><tr className="bg-slate-50 text-xs text-slate-500">
            <th className="text-left px-2 py-1.5 border-b border-slate-200">Credit Quality</th>
            <th className="text-right px-2 py-1.5 border-b border-slate-200">Value (€)</th>
            <th className="text-right px-2 py-1.5 border-b border-slate-200">Weight %</th>
            <th className="text-right px-2 py-1.5 border-b border-slate-200">Avg. Duration (yrs)</th>
            {compareDate && <>
              <th className="text-right px-2 py-1.5 border-b border-slate-200 bg-amber-50">As of {compareDate} (€)</th>
              <th className="text-right px-2 py-1.5 border-b border-slate-200 bg-amber-50">Δ Value (€)</th>
              <th className="text-right px-2 py-1.5 border-b border-slate-200 bg-amber-50">Δ pp</th>
            </>}
          </tr></thead>
          <tbody>
            {rows.map((r, i) => {
              const quality = String(r.quality)
              const cmp = compareMap[quality]
              const cmpValue = cmp ? Number(cmp.value_eur) : null
              const cmpDeltaValue = cmpValue != null ? Number(r.value_eur) - cmpValue : null
              const cmpDeltaPct = cmp ? Number(r.pct) - Number(cmp.pct) : null
              return (
                <tr key={i} onClick={() => setSelectedQuality(c => c === quality ? null : quality)}
                  className={`border-b border-slate-100 hover:bg-slate-50 cursor-pointer ${selectedQuality === quality ? 'bg-amber-50' : ''}`}>
                  <td className="px-2 py-1.5">{quality}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums">{fmtEur(Number(r.value_eur))}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{fmtPct(Number(r.pct))}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{r.avg_duration_years != null ? fmtNum(Number(r.avg_duration_years), 1) : '—'}</td>
                  {compareDate && <>
                    <td className="px-2 py-1.5 text-right tabular-nums text-slate-500 bg-amber-50/50">{cmpValue != null ? fmtEur(cmpValue) : '—'}</td>
                    <td className={`px-2 py-1.5 text-right tabular-nums font-medium bg-amber-50/50 ${cmpDeltaValue != null && cmpDeltaValue > 0 ? 'text-green-600' : cmpDeltaValue != null && cmpDeltaValue < 0 ? 'text-red-500' : ''}`}>
                      {cmpDeltaValue != null ? `${cmpDeltaValue > 0 ? '+' : ''}${fmtEur(cmpDeltaValue)}` : '—'}
                    </td>
                    <td className={`px-2 py-1.5 text-right tabular-nums font-medium bg-amber-50/50 ${cmpDeltaPct != null && cmpDeltaPct > 0 ? 'text-green-600' : cmpDeltaPct != null && cmpDeltaPct < 0 ? 'text-red-500' : ''}`}>
                      {cmpDeltaPct != null ? `${cmpDeltaPct > 0 ? '+' : ''}${cmpDeltaPct.toFixed(2)}%` : '—'}
                    </td>
                  </>}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      </WithCopy>
      <p className="text-xs text-slate-400">
        Percentages are of total bond-like exposure (direct bonds + the bond portion of held funds), not the whole portfolio.
        "Direct / Unrated" duration is an approximation (years to maturity), not modified duration like the fund-sourced figures.
      </p>

      {usGov && (
        <button type="button" onClick={() => setSelectedQuality(c => c === 'Us Government' ? null : 'Us Government')}
          className={`w-full text-left text-xs rounded-md border px-3 py-2 ${selectedQuality === 'Us Government' ? 'bg-amber-50 border-amber-300' : 'bg-slate-50 border-slate-200 hover:bg-slate-100'}`}>
          <span className="text-slate-500">Of this, </span>
          <span className="font-semibold text-slate-700">{fmtEur(Number(usGov.value_eur))} ({fmtPct(Number(usGov.pct))})</span>
          <span className="text-slate-500"> is also US Government-issued — an issuer-type flag, not a rating rung, so it overlaps the buckets above rather than adding a separate slice.</span>
          {usGov.avg_duration_years != null && (
            <span className="text-slate-500"> Avg. duration {fmtNum(Number(usGov.avg_duration_years), 1)}yrs.</span>
          )}
        </button>
      )}

      {selectedQuality && (
        <div>
          <div className="flex items-center justify-between mb-1">
            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide">Securities rated "{selectedQuality}"</p>
            <button onClick={() => setSelectedQuality(null)} className="text-xs text-slate-400 hover:text-slate-600 underline">Clear</button>
          </div>
          <WithCopy>
          <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-500px)] text-xs">
            <table className="w-full border-collapse">
              <thead className="sticky top-0 z-10 bg-slate-50">
                <tr className="bg-slate-50 text-xs text-slate-500">
                  <th className="text-left px-2 py-1.5 border-b border-slate-200">Security</th>
                  <th className="text-right px-2 py-1.5 border-b border-slate-200">Value (€)</th>
                  <th className="text-right px-2 py-1.5 border-b border-slate-200">Weight % (of {selectedQuality})</th>
                  <th className="text-right px-2 py-1.5 border-b border-slate-200">Duration (yrs)</th>
                </tr>
              </thead>
              <tbody>
                {detailRows.map((r, i) => (
                  <tr key={i} className="border-b border-slate-100 hover:bg-slate-50">
                    <td className="px-2 py-1.5"><SecLink id={r.securities_id}>{String(r.name)}</SecLink></td>
                    <td className="px-2 py-1.5 text-right tabular-nums">{fmtEur(Number(r.value_eur))}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{fmtPct(Number(r.pct))}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{r.duration_years != null ? fmtNum(Number(r.duration_years), 1) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          </WithCopy>
        </div>
      )}
    </div>
  )
}

function XrayStockOverlapTab({ accountIds, compareDate }: { accountIds?: number[]; compareDate?: string }) {
  const qc = useQueryClient()
  const liveRefetchMs = useLiveRefetchInterval()
  const { data, isLoading } = useQuery({ queryKey: ['xray', 'stock-overlap', accountIds, compareDate], queryFn: () => getXrayStockOverlap(accountIds, compareDate), refetchInterval: liveRefetchMs })
  const resp = data as { rows: Row[]; compare_rows?: Row[]; compare_date?: string } | undefined
  const rows = resp?.rows ?? []
  const totalPortfolio = rows.length > 0 ? Number(rows[0].total_portfolio_eur ?? 0) : 0
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null)
  const [importingSymbol, setImportingSymbol] = useState<string | null>(null)
  const [importErrors, setImportErrors] = useState<Record<string, string>>({})

  const bySymbol = useMemo(() => {
    const m: Record<string, { name: string; total: number; securitiesId: number | null; sources: { label: string; value: number }[] }> = {}
    for (const r of rows) {
      const sym = String(r.symbol ?? '—')
      if (!m[sym]) m[sym] = { name: String(r.name ?? ''), total: 0, securitiesId: null, sources: [] }
      const val = Number(r.value_eur ?? 0)
      m[sym].total += val
      if (r.securities_id != null) m[sym].securitiesId = Number(r.securities_id)
      m[sym].sources.push({ label: r.source_type === 'Direct' ? 'Direct' : `via ${String(r.source_label)}`, value: val })
    }
    return Object.entries(m).sort((a, b) => b[1].total - a[1].total)
  }, [rows])

  const compareBySymbol = useMemo(() => {
    const m: Record<string, number> = {}
    for (const r of resp?.compare_rows ?? []) {
      const sym = String(r.symbol ?? '—')
      m[sym] = (m[sym] ?? 0) + Number(r.value_eur ?? 0)
    }
    return m
  }, [resp])

  const detailRows = selectedSymbol ? bySymbol.find(([sym]) => sym === selectedSymbol)?.[1].sources ?? [] : []

  // One-click import: a fund's top-10 constituent that isn't already a Securities row
  // (you don't hold it directly, and it's never been imported before) — fetches its
  // Yahoo Finance metadata and creates the row directly, no form to fill in, since the
  // exact ticker is already known from the fund's holdings data.
  const handleImport = async (sym: string, e: React.MouseEvent) => {
    e.stopPropagation()
    setImportingSymbol(sym)
    setImportErrors(prev => { const next = { ...prev }; delete next[sym]; return next })
    try {
      const info = await lookupTicker(sym) as Record<string, unknown>
      await upsertSecurity({ ...info, ticker: info.ticker || sym })
      qc.invalidateQueries({ queryKey: ['xray', 'stock-overlap'] })
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? (err instanceof Error ? err.message : 'Import failed')
      setImportErrors(prev => ({ ...prev, [sym]: msg }))
    } finally {
      setImportingSymbol(null)
    }
  }

  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  return (
    <div className="space-y-4">
      <WithCopy>
      <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-300px)] text-xs">
        <table className="w-full border-collapse">
          <thead className="sticky top-0 z-10 bg-slate-50">
            <tr className="bg-slate-50 text-xs text-slate-500">
              <th className="text-left px-2 py-1.5 border-b border-slate-200">Symbol</th>
              <th className="text-left px-2 py-1.5 border-b border-slate-200">Name</th>
              <th className="text-right px-2 py-1.5 border-b border-slate-200">Total Value (€)</th>
              <th className="text-right px-2 py-1.5 border-b border-slate-200">% of Portfolio</th>
              <th className="text-left px-2 py-1.5 border-b border-slate-200">Sources</th>
              <th className="text-left px-2 py-1.5 border-b border-slate-200"></th>
              {compareDate && <>
                <th className="text-right px-2 py-1.5 border-b border-slate-200 bg-amber-50">As of {compareDate} (€)</th>
                <th className="text-right px-2 py-1.5 border-b border-slate-200 bg-amber-50">Δ Value (€)</th>
              </>}
            </tr>
          </thead>
          <tbody>
            {bySymbol.map(([sym, v], i) => {
              const cmpValue = compareDate ? (compareBySymbol[sym] ?? 0) : null
              const cmpDelta = cmpValue != null ? v.total - cmpValue : null
              return (
              <tr key={i} onClick={() => setSelectedSymbol(c => c === sym ? null : sym)}
                className={`border-b border-slate-100 hover:bg-slate-50 cursor-pointer ${selectedSymbol === sym ? 'bg-slate-100' : ''}`}>
                <td className="px-2 py-1.5 font-mono font-medium">{sym}</td>
                <td className="px-2 py-1.5 text-slate-500"><SecLink id={v.securitiesId}>{v.name}</SecLink></td>
                <td className="px-2 py-1.5 text-right tabular-nums">{fmtEur(v.total)}</td>
                <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{fmtPct(totalPortfolio > 0 ? v.total / totalPortfolio * 100 : 0)}</td>
                <td className="px-2 py-1.5 text-slate-500">
                  {v.sources.map((s) => `${s.label} (${fmtPct(totalPortfolio > 0 ? s.value / totalPortfolio * 100 : 0)})`).join(', ')}
                </td>
                <td className="px-2 py-1.5">
                  {v.securitiesId == null && sym !== '—' && (
                    <button
                      onClick={e => handleImport(sym, e)}
                      disabled={importingSymbol === sym}
                      title={`Look up ${sym} on Yahoo Finance and add it to Securities`}
                      className="text-blue-600 hover:text-blue-800 underline disabled:opacity-50 disabled:no-underline whitespace-nowrap">
                      {importingSymbol === sym ? 'Importing…' : 'Import from Yahoo'}
                    </button>
                  )}
                  {importErrors[sym] && <span className="text-red-600 block mt-0.5">{importErrors[sym]}</span>}
                </td>
                {compareDate && <>
                  <td className="px-2 py-1.5 text-right tabular-nums text-slate-500 bg-amber-50/50">{cmpValue != null ? fmtEur(cmpValue) : '—'}</td>
                  <td className={`px-2 py-1.5 text-right tabular-nums font-medium bg-amber-50/50 ${cmpDelta != null && cmpDelta > 0 ? 'text-green-600' : cmpDelta != null && cmpDelta < 0 ? 'text-red-500' : ''}`}>
                    {cmpDelta != null ? `${cmpDelta > 0 ? '+' : ''}${fmtEur(cmpDelta)}` : '—'}
                  </td>
                </>}
              </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      </WithCopy>
      <p className="text-xs text-slate-400">
        Fund contributions only capture each fund's top 10 constituents — true overlap for broad-market funds may be higher than shown here.
        Constituents not already in Securities (not held directly, never imported) show an <b>Import from Yahoo</b> button to add them.
      </p>

      {selectedSymbol && (
        <div>
          <div className="flex items-center justify-between mb-1">
            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide">Sources of "{selectedSymbol}" exposure</p>
            <button onClick={() => setSelectedSymbol(null)} className="text-xs text-slate-400 hover:text-slate-600 underline">Clear</button>
          </div>
          <WithCopy>
          <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-500px)] text-xs">
            <table className="w-full border-collapse">
              <thead className="sticky top-0 z-10 bg-slate-50">
                <tr className="bg-slate-50 text-xs text-slate-500">
                  <th className="text-left px-2 py-1.5 border-b border-slate-200">Source</th>
                  <th className="text-right px-2 py-1.5 border-b border-slate-200">Value (€)</th>
                  <th className="text-right px-2 py-1.5 border-b border-slate-200">Weight % (of Portfolio)</th>
                </tr>
              </thead>
              <tbody>
                {detailRows.map((s, i) => (
                  <tr key={i} className="border-b border-slate-100 hover:bg-slate-50">
                    <td className="px-2 py-1.5">{s.label}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums">{fmtEur(s.value)}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{fmtPct(totalPortfolio > 0 ? s.value / totalPortfolio * 100 : 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          </WithCopy>
        </div>
      )}
    </div>
  )
}

function XrayExpenseRatioNotes() {
  return (
    <div className="text-xs text-slate-500 space-y-2 mt-3">
      <p><b>What this measures:</b> the value-weighted average annual expense ratio across your ETF/Mutual Fund holdings — what each fund charges you per year, expressed as a % of assets, blended by how much you hold of each.</p>
      <p><b>Why it only covers funds:</b> direct stock/bond holdings have no expense ratio; this metric only applies to the ETF/Mutual Fund portion of your portfolio.</p>
      <p><b>Coverage %:</b> the share of your fund holdings (by value) for which an expense ratio is actually known — a low coverage % means the headline number may not reflect your full fund exposure (some funds haven't had their composition downloaded yet, or Yahoo doesn't report an expense ratio for them).</p>
    </div>
  )
}

function XrayExpenseRatioTab({ accountIds, compareDate }: { accountIds?: number[]; compareDate?: string }) {
  const liveRefetchMs = useLiveRefetchInterval()
  const { data, isLoading } = useQuery({ queryKey: ['xray', 'expense-ratio', accountIds, compareDate], queryFn: () => getXrayExpenseRatio(accountIds, compareDate), refetchInterval: liveRefetchMs })
  const resp = data as { summary: Row | null; funds: Row[]; compare?: { summary: Row | null; funds: Row[] }; compare_date?: string } | undefined
  const summary = resp?.summary
  const funds = resp?.funds ?? []
  const compareSummary = resp?.compare?.summary
  const compareFundsMap = useMemo(() => {
    const m: Record<number, Row> = {}
    for (const r of resp?.compare?.funds ?? []) if (r.securities_id != null) m[Number(r.securities_id)] = r
    return m
  }, [resp])
  const { sorted: fundsSorted, sortKey, sortDir, toggleSort } = useSortTable(funds, 'value_eur', 'desc')

  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  if (!summary || summary.weighted_expense_ratio_pct == null) return (
    <div className="max-w-md">
      <div className="text-sm text-slate-400 py-8 text-center">No fund holdings with a known expense ratio.</div>
      <XrayExpenseRatioNotes />
    </div>
  )
  return (
    <div className="space-y-4">
      <div className="max-w-md">
        <Card>
          <CardBody>
            <p className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-1">Weighted Average Expense Ratio</p>
            <p className="text-3xl font-bold text-slate-700">{fmtPct(Number(summary.weighted_expense_ratio_pct), 3)}</p>
            <p className="text-xs text-slate-400 mt-2">
              Covers {fmtPct(Number(summary.coverage_pct))} of fund holdings by value ({fmtEur(Number(summary.total_fund_value_eur))} total in ETFs/Mutual Funds).
            </p>
            {compareDate && compareSummary && compareSummary.weighted_expense_ratio_pct != null && (
              <p className="text-xs text-amber-700 mt-2 bg-amber-50 rounded px-2 py-1.5">
                As of {compareDate}: {fmtPct(Number(compareSummary.weighted_expense_ratio_pct), 3)}
                {' '}(Δ {(() => {
                  const d = Number(summary.weighted_expense_ratio_pct) - Number(compareSummary.weighted_expense_ratio_pct)
                  return `${d > 0 ? '+' : ''}${d.toFixed(3)}pp`
                })()})
              </p>
            )}
          </CardBody>
        </Card>
        <XrayExpenseRatioNotes />
      </div>
      <WithCopy>
      <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-500px)] text-xs">
        <table className="w-full border-collapse">
          <thead className="sticky top-0 z-10 bg-slate-50">
            <tr className="bg-slate-50 text-xs text-slate-500">
              <ColHeader label="Fund" sortKey="name" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} className="text-left px-2 py-1.5 border-b border-slate-200" />
              <ColHeader label="Value (€)" sortKey="value_eur" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} align="right" className="px-2 py-1.5 border-b border-slate-200" />
              <ColHeader label="Weight %" sortKey="pct" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} align="right" className="px-2 py-1.5 border-b border-slate-200" />
              <ColHeader label="Expense Ratio" sortKey="expense_ratio_pct" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} align="right" className="px-2 py-1.5 border-b border-slate-200" />
              {compareDate && <>
                <th className="text-right px-2 py-1.5 border-b border-slate-200 bg-amber-50">As of {compareDate} (€)</th>
                <th className="text-right px-2 py-1.5 border-b border-slate-200 bg-amber-50">Δ Value (€)</th>
              </>}
            </tr>
          </thead>
          <tbody>
            {fundsSorted.map((r, i) => {
              const cmp = r.securities_id != null ? compareFundsMap[Number(r.securities_id)] : undefined
              const cmpValue = cmp ? Number(cmp.value_eur) : null
              const cmpDelta = cmpValue != null ? Number(r.value_eur) - cmpValue : null
              return (
              <tr key={i} className="border-b border-slate-100 hover:bg-slate-50">
                <td className="px-2 py-1.5"><SecLink id={r.securities_id}>{String(r.name)}</SecLink></td>
                <td className="px-2 py-1.5 text-right tabular-nums">{fmtEur(Number(r.value_eur))}</td>
                <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{fmtPct(Number(r.pct))}</td>
                <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{r.expense_ratio_pct != null ? fmtPct(Number(r.expense_ratio_pct), 3) : '—'}</td>
                {compareDate && <>
                  <td className="px-2 py-1.5 text-right tabular-nums text-slate-500 bg-amber-50/50">{cmpValue != null ? fmtEur(cmpValue) : '—'}</td>
                  <td className={`px-2 py-1.5 text-right tabular-nums font-medium bg-amber-50/50 ${cmpDelta != null && cmpDelta > 0 ? 'text-green-600' : cmpDelta != null && cmpDelta < 0 ? 'text-red-500' : ''}`}>
                    {cmpDelta != null ? `${cmpDelta > 0 ? '+' : ''}${fmtEur(cmpDelta)}` : '—'}
                  </td>
                </>}
              </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      </WithCopy>
    </div>
  )
}

// End of the previous calendar month, e.g. run on any day in August -> 31 July.
function lastMonthEnd(): string {
  const d = new Date()
  d.setDate(0)
  return d.toISOString().slice(0, 10)
}

function XRayTab({ accountIds }: { accountIds?: number[] }) {
  const [tab, setTab] = usePersist('xray_tab', 'Asset Allocation')
  const [compareDate, setCompareDate] = usePersist('xray_compare_date', '')
  const today = new Date().toISOString().slice(0, 10)
  return (
    <div className="space-y-3">
      <SubTabs tabs={['Asset Allocation', 'Sector Weighting', 'Style Box', 'Bond Quality', 'Stock Overlap', 'Expense Ratio']} active={tab} onChange={setTab} />
      <div className="flex flex-wrap items-center gap-2 text-sm bg-slate-50 border border-slate-200 rounded-lg px-3 py-2">
        <Tooltip text="Show current values alongside a past date's, reconstructed from your transaction history and historical prices/FX. Sector weights, asset mix, credit ratings, category, and expense ratio always reflect today's fund data — Oikos has no historical version of a fund's own internal makeup.">
          <span className="text-slate-500 cursor-help underline decoration-dotted">Compare vs</span>
        </Tooltip>
        <input type="date" max={today} value={compareDate}
          onChange={e => setCompareDate(e.target.value)}
          className="rounded border border-slate-300 px-2 py-1 text-xs" />
        <button onClick={() => setCompareDate(lastMonthEnd())} className="text-xs text-blue-600 hover:underline">End of last month</button>
        {compareDate && <button onClick={() => setCompareDate('')} className="text-xs text-slate-400 hover:text-slate-600 underline">Clear</button>}
      </div>
      {tab === 'Asset Allocation' && <XrayAssetAllocationTab accountIds={accountIds} compareDate={compareDate || undefined} />}
      {tab === 'Sector Weighting' && <XraySectorWeightingTab accountIds={accountIds} compareDate={compareDate || undefined} />}
      {tab === 'Style Box' && <XrayStyleBoxTab accountIds={accountIds} compareDate={compareDate || undefined} />}
      {tab === 'Bond Quality' && <XrayBondQualityTab accountIds={accountIds} compareDate={compareDate || undefined} />}
      {tab === 'Stock Overlap' && <XrayStockOverlapTab accountIds={accountIds} compareDate={compareDate || undefined} />}
      {tab === 'Expense Ratio' && <XrayExpenseRatioTab accountIds={accountIds} compareDate={compareDate || undefined} />}
    </div>
  )
}

function HoldingsSnapshotTab({ accountIds }: { accountIds?: number[] }) {
  const liveRefetchMs = useLiveRefetchInterval()
  const { data = [], isLoading } = useQuery({ queryKey: ['portfolio-summary', accountIds], queryFn: () => getPortfolioSummary(accountIds), refetchInterval: liveRefetchMs })
  const rows = data as Row[]
  const { sorted: holdSorted, sortKey: holdSK, sortDir: holdSD, toggleSort: holdSort } = useSortTablePersisted(rows, 'holdings-snapshot-sort', 'value_eur', 'desc')
  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  const total = rows.reduce((s, r) => s + Number(r.value_eur ?? 0), 0)
  return (
    <div className="space-y-3">
      <KpiCard label="Total Portfolio Value" value={fmtEur(total)} color="text-blue-700" />
      <WithCopy>
      <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-300px)] text-xs">
        <table className="w-full border-collapse">
          <thead className="sticky top-0 z-10"><tr className="bg-slate-50 text-xs text-slate-500">
            <ColHeader label="Security" sortKey="security" currentKey={holdSK} currentDir={holdSD} onSort={holdSort} className="text-left px-2 py-1.5 border-b border-slate-200" />
            <ColHeader label="Ticker" sortKey="ticker" currentKey={holdSK} currentDir={holdSD} onSort={holdSort} className="text-left px-2 py-1.5 border-b border-slate-200" />
            <ColHeader label="Account" sortKey="account" currentKey={holdSK} currentDir={holdSD} onSort={holdSort} className="text-left px-2 py-1.5 border-b border-slate-200" />
            <ColHeader label="Quantity" sortKey="quantity" currentKey={holdSK} currentDir={holdSD} onSort={holdSort} align="right" className="px-2 py-1.5 border-b border-slate-200" />
            <ColHeader label="Price" sortKey="last_price" currentKey={holdSK} currentDir={holdSD} onSort={holdSort} align="right" className="px-2 py-1.5 border-b border-slate-200" />
            <ColHeader label="Ccy" sortKey="currency" currentKey={holdSK} currentDir={holdSD} onSort={holdSort} className="text-left px-2 py-1.5 border-b border-slate-200" />
            <ColHeader label="Value (€)" sortKey="value_eur" currentKey={holdSK} currentDir={holdSD} onSort={holdSort} align="right" className="px-2 py-1.5 border-b border-slate-200" />
            <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold">Weight %</th>
          </tr></thead>
          <tbody>
            {holdSorted.map((r, i) => (
              <tr key={i} className="border-b border-slate-100 hover:bg-slate-50">
                <td className="px-2 py-1.5 font-medium"><SecLink id={r.securities_id}>{String(r.security)}</SecLink></td>
                <td className="px-2 py-1.5 font-mono text-slate-500 text-xs"><SecLink id={r.securities_id}>{String(r.ticker ?? '—')}</SecLink></td>
                <td className="px-2 py-1.5 text-slate-500"><AccountLink id={r.accounts_id as number} name={String(r.account)} type={String(r.account_type ?? '')} /></td>
                <td className="px-2 py-1.5 text-right tabular-nums">{fmtNum(Number(r.quantity), 4)}</td>
                <td className="px-2 py-1.5 text-right tabular-nums">{fmtNum(Number(r.last_price ?? 0), 4)}</td>
                <td className="px-2 py-1.5 text-slate-500">{String(r.currency ?? '—')}</td>
                <td className="px-2 py-1.5 text-right tabular-nums font-medium">{fmtEur(Number(r.value_eur ?? 0))}</td>
                <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{total > 0 ? (Number(r.value_eur ?? 0) / total * 100).toFixed(1) + '%' : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      </WithCopy>
    </div>
  )
}

function DetailAnalysisTab({ asOf, accountIds }: { asOf: string; accountIds?: number[] }) {
  // Fetch available month-end dates within the reporting period
  const { data: histData = [] } = useQuery({
    queryKey: ['inv-positions-history', asOf, accountIds],
    queryFn: () => getInvestmentPositionsHistory(asOf, accountIds),
  })
  const availableDates = [...new Set((histData as Row[]).map(r => String(r.date)))].sort().reverse()
  const [selectedDate, setSelectedDate] = useState<string>('')
  const snapshotDate = selectedDate || availableDates[0] || asOf

  const { data = [], isLoading } = useQuery({
    queryKey: ['holdings-snapshot', snapshotDate, accountIds],
    queryFn: () => getHoldingsSnapshot(snapshotDate, accountIds),
    enabled: !!snapshotDate,
  })
  const rows = data as Row[]
  const { sorted, sortKey: sk, sortDir: sd, toggleSort } = useSortTablePersisted(rows, 'detail-analysis-sort', 'value_eur', 'desc')
  const total = rows.reduce((s, r) => s + Number(r.value_eur ?? 0), 0)
  return (
    <div className="space-y-3">
      {availableDates.length > 0 && (
        <div className="flex items-center gap-3">
          <label className="text-sm text-slate-500 font-medium whitespace-nowrap">Snapshot Date:</label>
          <select
            value={snapshotDate}
            onChange={e => setSelectedDate(e.target.value)}
            className="border border-slate-200 rounded px-3 py-1.5 text-sm bg-white focus:outline-none focus:ring-1 focus:ring-blue-400"
          >
            {availableDates.map(d => <option key={d} value={d}>{d}</option>)}
          </select>
        </div>
      )}
    {isLoading ? <div className="flex justify-center py-12"><Spinner /></div> : (
    <WithCopy>
    <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-320px)] text-xs">
      <table className="w-full border-collapse">
        <thead className="sticky top-0 z-10 bg-slate-50">
          <tr className="text-xs text-slate-500">
            <ColHeader label="Account" sortKey="account" currentKey={sk} currentDir={sd} onSort={toggleSort} className="text-left px-2 py-1.5 border-b border-slate-200" />
            <ColHeader label="Security" sortKey="security" currentKey={sk} currentDir={sd} onSort={toggleSort} className="text-left px-2 py-1.5 border-b border-slate-200" />
            <ColHeader label="Ticker" sortKey="ticker" currentKey={sk} currentDir={sd} onSort={toggleSort} className="text-left px-2 py-1.5 border-b border-slate-200" />
            <ColHeader label="Type" sortKey="type" currentKey={sk} currentDir={sd} onSort={toggleSort} className="text-left px-2 py-1.5 border-b border-slate-200" />
            <ColHeader label="Ccy" sortKey="currency" currentKey={sk} currentDir={sd} onSort={toggleSort} className="text-left px-2 py-1.5 border-b border-slate-200" />
            <ColHeader label="Quantity" sortKey="quantity" currentKey={sk} currentDir={sd} onSort={toggleSort} align="right" className="px-2 py-1.5 border-b border-slate-200" />
            <ColHeader label="Price" sortKey="price" currentKey={sk} currentDir={sd} onSort={toggleSort} align="right" className="px-2 py-1.5 border-b border-slate-200" />
            <ColHeader label="Price Date" sortKey="price_date" currentKey={sk} currentDir={sd} onSort={toggleSort} className="text-left px-2 py-1.5 border-b border-slate-200" />
            <ColHeader label="Value (€)" sortKey="value_eur" currentKey={sk} currentDir={sd} onSort={toggleSort} align="right" className="px-2 py-1.5 border-b border-slate-200" />
          </tr>
        </thead>
        <tbody>
          {sorted.map((r, i) => (
            <tr key={i} className="border-b border-slate-100 hover:bg-slate-50">
              <td className="px-2 py-1.5 text-slate-500"><AccountLink id={r.accounts_id as number} name={String(r.account)} type={String(r.account_type ?? '')} /></td>
              <td className="px-2 py-1.5 font-medium"><SecLink id={r.securities_id}>{String(r.security)}</SecLink></td>
              <td className="px-2 py-1.5 font-mono text-slate-400 text-xs">{String(r.ticker ?? '—')}</td>
              <td className="px-2 py-1.5 text-slate-500">{String(r.type ?? '—')}</td>
              <td className="px-2 py-1.5 text-slate-500">{String(r.currency ?? '—')}</td>
              <td className="px-2 py-1.5 text-right tabular-nums">{fmtNum(Number(r.quantity), 8)}</td>
              <td className="px-2 py-1.5 text-right tabular-nums">{fmtNum(Number(r.price), 4)}</td>
              <td className="px-2 py-1.5 text-slate-400 text-xs">{String(r.price_date ?? '—')}</td>
              <td className="px-2 py-1.5 text-right tabular-nums font-medium">{fmtEur(Number(r.value_eur))}</td>
            </tr>
          ))}
          <tr className="border-t-2 border-slate-300 bg-slate-50 font-semibold">
            <td colSpan={8} className="px-2 py-1.5 text-right text-xs text-slate-600">Total</td>
            <td className="px-2 py-1.5 text-right tabular-nums">{fmtEur(total)}</td>
          </tr>
        </tbody>
      </table>
    </div>
    </WithCopy>
    )}
    </div>
  )
}

function InvPositionsSection({ startDate: initialStartDate }: { startDate: string }) {
  const [tab, setTab] = usePersist('inv_positions_tab', 'Graph')
  // One-time migration: this sub-tab was labeled 'X-Ray' before the rename to
  // 'Portfolio Analysis' — a user with that value already persisted would
  // otherwise land on a blank pane, since nothing below matches the old label.
  useEffect(() => { if (tab === 'X-Ray') setTab('Portfolio Analysis') }, [tab, setTab])
  const [presetAccountIds, setPresetAccountIds] = useState<number[] | undefined>(undefined)

  // Default to Dec 31 of the previous calendar year
  const defaultDate = `${new Date().getFullYear() - 1}-12-31`
  const [asOf, setAsOf] = useState(initialStartDate || defaultDate)

  return (
    <div className="space-y-3">
      <PortfolioPresetBar reportScope="inv_positions" eligibleTypes={INV_POSITION_ACCOUNT_TYPES} onChange={setPresetAccountIds} />

      {/* Shared date control — applies to Graph, Summary and Detail Analysis only; hidden
          elsewhere since Current Holdings, FX Exposure, and Portfolio Analysis always
          show live data and ignore it, which was confusing to leave visible. */}
      {(tab === 'Graph' || tab === 'Summary' || tab === 'Detail Analysis') && (
        <div className="flex items-center gap-3 pb-1 border-b border-slate-100">
          <label className="text-sm text-slate-500 font-medium whitespace-nowrap">As of date:</label>
          <input
            type="date"
            value={asOf}
            onChange={e => setAsOf(e.target.value)}
            className="border border-slate-200 rounded px-3 py-1 text-sm bg-white focus:outline-none focus:ring-1 focus:ring-blue-400"
          />
          <button
            onClick={() => setAsOf(defaultDate)}
            className="text-xs text-slate-400 hover:text-slate-600 underline"
          >
            Reset to {defaultDate}
          </button>
        </div>
      )}

      <SubTabs tabs={['Graph', 'Summary', 'Detail Analysis', 'Current Holdings', 'FX Exposure', 'Portfolio Analysis']} active={tab} onChange={setTab} />
      {tab === 'Graph' && <InvPositionsGraph startDate={asOf} accountIds={presetAccountIds} />}
      {tab === 'Summary' && <InvPositionsSummary startDate={asOf} accountIds={presetAccountIds} />}
      {tab === 'Detail Analysis' && <DetailAnalysisTab asOf={asOf} accountIds={presetAccountIds} />}
      {tab === 'Current Holdings' && <HoldingsSnapshotTab accountIds={presetAccountIds} />}
      {tab === 'FX Exposure' && <FxExposureTab accountIds={presetAccountIds} />}
      {tab === 'Portfolio Analysis' && <XRayTab accountIds={presetAccountIds} />}
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════════
// 3. INVESTMENT PERFORMANCE
// ════════════════════════════════════════════════════════════════════════════
type PnlWindow = 'dtd' | 'wtd' | 'mtd' | 'qtd' | 'ytd' | '1y' | '3y' | '5y' | 'all'
const PNL_WINDOWS = [
  { k: 'dtd' as PnlWindow, label: 'D' }, { k: 'wtd' as PnlWindow, label: 'W' },
  { k: 'mtd' as PnlWindow, label: 'M' }, { k: 'qtd' as PnlWindow, label: 'Q' },
  { k: 'ytd' as PnlWindow, label: 'YTD' },
  { k: '1y' as PnlWindow, label: '1Y' }, { k: '3y' as PnlWindow, label: '3Y' }, { k: '5y' as PnlWindow, label: '5Y' },
  { k: 'all' as PnlWindow, label: 'All' },
]
function pnlKey(w: PnlWindow) { return w === 'all' ? 'pnl_net_all_time_eur' : `pnl_${w}_eur` }
const LONG_TERM_YEARS: Record<string, number> = { '1y': 1, '3y': 3, '5y': 5, '1Y': 1, '3Y': 3, '5Y': 5 }

// Long-term P&L (1Y/3Y/5Y) lives in its own endpoint/query, separate from the base P&L
// data (which covers DTD/WTD/MTD/QTD/YTD/All-Time and is live-refetched) — this only
// fetches when one of those long-term periods is actually selected (`years` non-null),
// and merges its per-(account,security) result into the base rows on demand.
function useLongTermPnl(years: number | null) {
  const { data, isLoading } = useQuery({
    queryKey: ['pnl-period', years],
    queryFn: () => getPnlPeriod(years as number),
    enabled: years != null,
  })
  return useMemo(() => {
    const map = new Map<string, { eur: number; pct: number | null }>()
    for (const r of (data ?? []) as Row[]) {
      map.set(`${r.accounts_id}-${r.securities_id}`, { eur: Number(r.pnl_eur ?? 0), pct: r.pnl_percent != null ? Number(r.pnl_percent) : null })
    }
    return { map, isLoading: years != null && isLoading }
  }, [data, isLoading, years])
}
function mergeLongTermPnl(rows: Row[], eurKey: string | null, pctKey: string | null, map: Map<string, { eur: number; pct: number | null }>): Row[] {
  if (!eurKey) return rows
  return rows.map(r => {
    const hit = map.get(`${r.accounts_id}-${r.securities_id}`)
    return { ...r, [eurKey]: hit?.eur ?? 0, ...(pctKey ? { [pctKey]: hit?.pct ?? null } : {}) }
  })
}

function PnlCell({ val, pct }: { val: number; pct?: number | null }) {
  const color = val >= 0 ? 'text-green-700' : 'text-red-600'
  return (
    <td className={`px-3 py-2 text-right tabular-nums font-medium ${color}`}>
      {fmtEur(val)}{pct != null && <span className="ml-1 text-xs opacity-70">({fmtPct(pct)})</span>}
    </td>
  )
}

// Lets you type a manual price directly into the P&L drill-down table; on Enter/blur it's
// saved as today's Historical_Prices row for that security (upsert — overwrites any price
// already recorded for today) and the P&L numbers refetch to reflect it.
function EditablePriceCell({ securitiesId, price, currency, onSaved }: {
  securitiesId: unknown; price: number | null; currency: string; onSaved: () => void
}) {
  const [value, setValue] = useState(price != null ? String(price) : '')
  const [saving, setSaving] = useState(false)
  useEffect(() => { setValue(price != null ? String(price) : '') }, [price])

  const save = async () => {
    const num = Number(value)
    if (!securitiesId || !value.trim() || isNaN(num) || num === price) return
    setSaving(true)
    try {
      await addPrice({ security_id: Number(securitiesId), date: new Date().toISOString().slice(0, 10), close: num })
      onSaved()
    } finally {
      setSaving(false)
    }
  }

  return (
    <td className="px-2 py-1 text-right">
      <div className="flex items-center justify-end gap-1">
        <span className="text-slate-400 text-xs">{getCurrencySymbol(currency)}</span>
        <input
          type="number"
          step="any"
          value={value}
          disabled={saving}
          onChange={e => setValue(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }}
          onBlur={save}
          className="w-20 text-right tabular-nums text-slate-700 bg-transparent border border-transparent hover:border-slate-200 focus:border-blue-400 focus:bg-white rounded px-1 py-0.5 text-sm outline-none disabled:opacity-50"
        />
      </div>
    </td>
  )
}

function ChkBox({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-center gap-1.5 text-xs text-slate-600 cursor-pointer select-none">
      <input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} className="rounded" />
      {label}
    </label>
  )
}

function PnlReport() {
  const qc = useQueryClient()
  const liveRefetchMs = useLiveRefetchInterval()
  const [win, setWin] = usePersist<PnlWindow>('pnl_win', 'ytd')
  const [showClosedAccounts, setShowClosedAccounts] = usePersist('pnl_showClosedAccounts', false)
  const [showFxSplit, setShowFxSplit] = usePersist('pnl_showFxSplit', false)
  const [showPct, setShowPct] = usePersist('pnl_showPct', true)
  const [showClosedPositions, setShowClosedPositions] = usePersist('pnl_showClosedPositions', false)
  const [showBenchmark, setShowBenchmark] = useState(false)
  const [searchParams, setSearchParams] = useSearchParams()
  const selectedAccount = searchParams.get('pnl_account')
  const setSelectedAccount = (acc: string | null) => {
    setShowBenchmark(false)
    setSearchParams(prev => {
      const next = new URLSearchParams(prev)
      acc ? next.set('pnl_account', acc) : next.delete('pnl_account')
      return next
    }, { replace: false })
  }

  const { data = [], isLoading } = useQuery({ queryKey: ['pnl'], queryFn: () => getPnl('1900-01-01'), refetchInterval: liveRefetchMs })
  const ltYears = LONG_TERM_YEARS[win] ?? null
  const { map: ltMap, isLoading: ltLoading } = useLongTermPnl(ltYears)

  // Derive all data BEFORE any early return so hooks are always called in the same order
  const rows = useMemo(
    () => mergeLongTermPnl(data as Row[], ltYears ? pnlKey(win) : null, ltYears ? `pnl_${win}_percent` : null, ltMap),
    [data, win, ltYears, ltMap],
  )
  const pk = pnlKey(win)
  const mktKey = win === 'dtd' ? 'pnl_dtd_market_eur' : win === 'ytd' ? 'pnl_ytd_market_eur' : null
  const fxKey  = win === 'dtd' ? 'pnl_dtd_fx_eur'     : win === 'ytd' ? 'pnl_ytd_fx_eur'     : null

  const accountMap = new Map<string, Row[]>()
  for (const r of rows) {
    const acc = String(r.accounts_name)
    if (!accountMap.has(acc)) accountMap.set(acc, [])
    accountMap.get(acc)!.push(r)
  }

  // Cumulative buy/sell quantities on fully-closed positions rarely net to exactly 0 —
  // floating-point residue (e.g. 1e-15) survives the sum, so compare against a cent
  // tolerance rather than exact equality.
  const isClosedAccount = (acRows: Row[]) => acRows.every(r => Math.abs(Number(r.current_value_eur ?? 0)) < 0.01)
  const isClosedPosition = (r: Row) => Math.abs(Number(r.current_value_eur ?? 0)) < 0.01

  const accounts = Array.from(accountMap.entries())
    .filter(([, acRows]) => showClosedAccounts || !isClosedAccount(acRows))
    .map(([name, acRows]) => {
      const value = acRows.reduce((s, r) => s + Number(r.current_value_eur ?? 0), 0)
      const pnl = acRows.reduce((s, r) => s + Number(r[pk] ?? 0), 0)
      const unrealized = acRows.reduce((s, r) => s + Number(r.unrealized_pnl_eur ?? 0), 0)
      const cost = value - unrealized
      return {
        name,
        closed: isClosedAccount(acRows),
        value,
        pnl,
        pnl_pct: value !== 0 ? (pnl / value) * 100 : null,
        unrealized,
        unrealized_pct: cost !== 0 ? (unrealized / cost) * 100 : null,
        realized: acRows.reduce((s, r) => s + Number(r.realized_pnl_eur ?? 0), 0),
        market: mktKey ? acRows.reduce((s, r) => s + Number(r[mktKey] ?? 0), 0) : null,
        fx: fxKey ? acRows.reduce((s, r) => s + Number(r[fxKey] ?? 0), 0) : null,
      }
    })

  const totalValue = accounts.reduce((s, a) => s + a.value, 0)
  const totalPnl   = accounts.reduce((s, a) => s + a.pnl, 0)
  const totalUnreal = accounts.reduce((s, a) => s + a.unrealized, 0)
  const totalReal  = accounts.reduce((s, a) => s + a.realized, 0)
  const totalMkt   = mktKey ? accounts.reduce((s, a) => s + (a.market ?? 0), 0) : null
  const totalFx    = fxKey  ? accounts.reduce((s, a) => s + (a.fx    ?? 0), 0) : null
  // Same conventions already used per-row below: P&L% vs. current value, Unrealized% vs. cost basis.
  // Realized P&L has no cost-basis denominator available (positions are already closed), so it's
  // left without a percentage rather than showing a made-up figure.
  const totalPnlPct    = totalValue !== 0 ? (totalPnl / totalValue) * 100 : null
  const totalCostBasis = totalValue - totalUnreal
  const totalUnrealPct = totalCostBasis !== 0 ? (totalUnreal / totalCostBasis) * 100 : null

  const selectedAccountId: number | null = selectedAccount
    ? Number(accountMap.get(selectedAccount)?.[0]?.accounts_id ?? NaN) || null
    : null

  const drillRows = selectedAccount
    ? (accountMap.get(selectedAccount) ?? [])
        .filter(r => showClosedPositions || !isClosedPosition(r))
        .map((r): Row => {
          const unreal = Number(r.unrealized_pnl_eur ?? 0)
          const value = Number(r.current_value_eur ?? 0)
          const cost = value - unreal
          const pnl = Number(r[pk] ?? 0)
          return {
            ...r,
            unrealized_pnl_pct: cost !== 0 ? (unreal / cost) * 100 : null,
            pnl_pct: value !== 0 ? (pnl / value) * 100 : null,
          }
        })
    : null

  const { sorted: sortedAccounts, sortKey: acSK, sortDir: acSD, toggleSort: acSort } = useSortTablePersisted(accounts, 'pnl-accounts-sort', 'value', 'desc')
  const { sorted: sortedDrill,    sortKey: drSK, sortDir: drSD, toggleSort: drSort } = useSortTablePersisted(drillRows ?? [], 'pnl-drill-sort', 'current_value_eur', 'desc')

  if (isLoading || ltLoading) return <div className="flex justify-center py-12"><Spinner /></div>

  const checkboxBar = (
    <div className="flex flex-wrap gap-4 items-center py-2 px-1 border-b border-slate-100">
      <ChkBox label="Show P&L %" checked={showPct} onChange={setShowPct} />
      <ChkBox label="Show Market / FX Split" checked={showFxSplit} onChange={setShowFxSplit} />
      <ChkBox label="Show Closed Accounts" checked={showClosedAccounts} onChange={setShowClosedAccounts} />
      {drillRows && <ChkBox label="Show Closed Positions" checked={showClosedPositions} onChange={setShowClosedPositions} />}
    </div>
  )

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <KpiCard label="Portfolio Value" value={fmtEur(totalValue)} color="text-blue-700" tooltip="Current market value of all investment holdings across all accounts, converted to EUR." />
        <KpiCard label={`P&L (${win.toUpperCase()})`} value={fmtEur(totalPnl)} color={totalPnl >= 0 ? 'text-green-700' : 'text-red-600'} tooltip={`Total profit or loss for the ${win.toUpperCase()} window — includes both unrealized mark-to-market changes and any realized gains.`}
          subtitleNode={(showPct && totalPnlPct != null) || (showFxSplit && totalMkt != null && totalFx != null) ? (
            <span className="flex gap-2 tabular-nums flex-wrap">
              {showPct && totalPnlPct != null && (
                <span className={totalPnlPct >= 0 ? 'text-green-700' : 'text-red-600'}>({totalPnlPct >= 0 ? '+' : ''}{totalPnlPct.toFixed(2)}%)</span>
              )}
              {showFxSplit && totalMkt != null && totalFx != null && (
                <>
                  <span>Mkt: <span className={totalMkt >= 0 ? 'text-green-700' : 'text-red-600'}>{fmtEur(totalMkt)}</span></span>
                  <span>FX: <span className={totalFx >= 0 ? 'text-green-700' : 'text-red-600'}>{fmtEur(totalFx)}</span></span>
                </>
              )}
            </span>
          ) : undefined} />
        <KpiCard label="Unrealized P&L" value={fmtEur(totalUnreal)} color={totalUnreal >= 0 ? 'text-green-700' : 'text-red-600'} tooltip="Open position gain/loss: current market value minus the cost basis of all currently held securities."
          subtitleNode={totalUnrealPct != null ? (
            <span className={`tabular-nums ${totalUnrealPct >= 0 ? 'text-green-700' : 'text-red-600'}`}>({totalUnrealPct >= 0 ? '+' : ''}{totalUnrealPct.toFixed(2)}%)</span>
          ) : undefined} />
        <KpiCard label="Realized P&L" value={fmtEur(totalReal)} color={totalReal >= 0 ? 'text-green-700' : 'text-red-600'} tooltip="Locked-in profit or loss from positions that have already been sold or closed. No cost-basis percentage is shown here — the original cost basis of already-closed positions isn't tracked separately from unrealized P&L." />
      </div>
      <div className="flex gap-1">
        {PNL_WINDOWS.map(w => (
          <button key={w.k} onClick={() => setWin(w.k)}
            className={`px-2.5 py-1 rounded text-xs font-medium ${win === w.k ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}>{w.label}</button>
        ))}
      </div>
      {checkboxBar}
      {drillRows ? (() => {
        const drillValue    = drillRows.reduce((s, r) => s + Number(r.current_value_eur ?? 0), 0)
        const drillPnl      = drillRows.reduce((s, r) => s + Number(r[pk] ?? 0), 0)
        const drillPnlPct   = drillValue !== 0 ? (drillPnl / drillValue) * 100 : null
        const drillUnreal   = drillRows.reduce((s, r) => s + Number(r.unrealized_pnl_eur ?? 0), 0)
        const drillCost     = drillValue - drillUnreal
        const drillUnrealPct = drillCost !== 0 ? (drillUnreal / drillCost) * 100 : null
        const drillReal     = drillRows.reduce((s, r) => s + Number(r.realized_pnl_eur ?? 0), 0)
        const drillMkt      = mktKey ? drillRows.reduce((s, r) => s + Number(r[mktKey] ?? 0), 0) : null
        const drillFx       = fxKey  ? drillRows.reduce((s, r) => s + Number(r[fxKey]  ?? 0), 0) : null
        return (
        <div className="space-y-3">
          <div className="flex items-center gap-3">
            <button onClick={() => setSelectedAccount(null)} className="text-blue-600 hover:underline text-sm">← All Accounts</button>
            <span className="text-slate-400 text-sm">/</span>
            <span className="text-sm font-semibold text-slate-700">{selectedAccount}</span>
            {selectedAccountId != null && (
              <button onClick={() => setShowBenchmark(v => !v)}
                className={`ml-auto px-2.5 py-1 rounded text-xs font-medium ${showBenchmark ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}>
                📊 Benchmark
              </button>
            )}
          </div>
          {showBenchmark && selectedAccountId != null && (
            <div className="p-3 bg-white border border-slate-200 rounded-lg">
              <BenchmarkTab accountIds={[selectedAccountId]} keyPrefix={`pnl_bench_${selectedAccountId}`} defaultYtd />
            </div>
          )}
          <div className="flex flex-wrap gap-x-6 gap-y-1 px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-xs">
            <span className="text-slate-500 font-medium">Totals:</span>
            <span className="tabular-nums">Value: <strong>{fmtEur(drillValue)}</strong></span>
            <span className={`tabular-nums ${drillPnl >= 0 ? 'text-green-700' : 'text-red-600'}`}>P&amp;L ({win.toUpperCase()}): <strong>{fmtEur(drillPnl)}</strong>{drillPnlPct != null && <span className="ml-1 opacity-75">({drillPnlPct >= 0 ? '+' : ''}{drillPnlPct.toFixed(2)}%)</span>}</span>
            {drillMkt != null && <span className={`tabular-nums ${drillMkt >= 0 ? 'text-green-700' : 'text-red-600'}`}>Mkt: <strong>{fmtEur(drillMkt)}</strong></span>}
            {drillFx  != null && <span className={`tabular-nums ${drillFx  >= 0 ? 'text-green-700' : 'text-red-600'}`}>FX: <strong>{fmtEur(drillFx)}</strong></span>}
            <span className={`tabular-nums ${drillUnreal >= 0 ? 'text-green-700' : 'text-red-600'}`}>Unrealized: <strong>{fmtEur(drillUnreal)}</strong>{drillUnrealPct != null && <span className="ml-1 opacity-75">({drillUnrealPct >= 0 ? '+' : ''}{drillUnrealPct.toFixed(2)}%)</span>}</span>
            <span className={`tabular-nums ${drillReal >= 0 ? 'text-green-700' : 'text-red-600'}`}>Realized: <strong>{fmtEur(drillReal)}</strong></span>
          </div>
          <WithCopy>
            <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-280px)]">
              <table className="w-full text-sm">
                <thead className="sticky top-0 z-10"><tr className="bg-slate-50 text-xs text-slate-500 uppercase tracking-wide">
                  <ColHeader label="Security" sortKey="securities_name" currentKey={drSK} currentDir={drSD} onSort={drSort} tooltip="Security name as recorded in your portfolio." />
                  <ColHeader label="Qty" sortKey="qty_today" currentKey={drSK} currentDir={drSD} onSort={drSort} align="right" tooltip="Current quantity held." />
                  <ColHeader label="Price" sortKey="price_today" currentKey={drSK} currentDir={drSD} onSort={drSort} align="right" tooltip="Last available market price in the security's native currency." />
                  <ColHeader label="Value (€)" sortKey="current_value_eur" currentKey={drSK} currentDir={drSD} onSort={drSort} align="right" tooltip="Current market value of the position in EUR." />
                  <ColHeader label={`P&L (${win.toUpperCase()})`} sortKey={pk} currentKey={drSK} currentDir={drSD} onSort={drSort} align="right" tooltip={`P&L for the ${win.toUpperCase()} window — change in market value plus realised gains.`} />
                  {showPct && <ColHeader label="P&L %" sortKey="pnl_pct" currentKey={drSK} currentDir={drSD} onSort={drSort} align="right" tooltip={`P&L for the ${win.toUpperCase()} window as a percentage of current value.`} />}
                  {showFxSplit && mktKey && <><ColHeader label="Market" sortKey={mktKey} currentKey={drSK} currentDir={drSD} onSort={drSort} align="right" tooltip="Part of the P&L attributable to the security's price movement in its local currency." /><ColHeader label="FX" sortKey={fxKey ?? ''} currentKey={drSK} currentDir={drSD} onSort={drSort} align="right" tooltip="Part of the P&L attributable to currency (FX) rate movements when converting to EUR." /></>}
                  <ColHeader label="Unrealized" sortKey="unrealized_pnl_eur" currentKey={drSK} currentDir={drSD} onSort={drSort} align="right" tooltip="Unrealized gain/loss: current value minus cost basis for still-open positions." />
                  <ColHeader label="Unreal. %" sortKey="unrealized_pnl_pct" currentKey={drSK} currentDir={drSD} onSort={drSort} align="right" tooltip="Unrealized gain/loss as a percentage of cost basis." />
                  <ColHeader label="Realized" sortKey="realized_pnl_eur" currentKey={drSK} currentDir={drSD} onSort={drSort} align="right" tooltip="Realized gain/loss from already-closed (sold) positions in this security." />
                  <ColHeader label="YOC %" sortKey="dividend_yoc_pct" currentKey={drSK} currentDir={drSD} onSort={drSort} align="right" tooltip="Dividend Yield on Cost: annual dividends received divided by your cost basis, as a percentage." />
                </tr></thead>
                <tbody className="divide-y divide-slate-100">
                  {sortedDrill.map((r, i) => (
                    <tr key={i} className={`hover:bg-slate-50 ${isClosedPosition(r) ? 'opacity-60' : ''}`}>
                      <td className="px-3 py-2 font-medium"><SecLink id={r.securities_id}>{String(r.securities_name)}</SecLink>{isClosedPosition(r) && <span className="ml-1.5 text-xs text-slate-400 font-normal">(closed)</span>}</td>
                      <td className="px-3 py-2 text-right tabular-nums text-slate-600">{r.qty_today != null ? fmtNum(Number(r.qty_today), 4) : '—'}</td>
                      <EditablePriceCell
                        securitiesId={r.securities_id}
                        price={r.price_today != null ? Number(r.price_today) : null}
                        currency={String(r.currency ?? 'EUR')}
                        onSaved={() => qc.invalidateQueries({ queryKey: ['pnl'] })}
                      />
                      <td className="px-3 py-2 text-right tabular-nums">{fmtEur(Number(r.current_value_eur ?? 0))}</td>
                      <PnlCell val={Number(r[pk] ?? 0)} />
                      {showPct && (() => {
                        const pct = r.pnl_pct != null ? Number(r.pnl_pct) : null
                        return (
                          <td className={`px-3 py-2 text-right tabular-nums ${pct == null ? 'text-slate-400' : pct >= 0 ? 'text-green-700' : 'text-red-600'}`}>
                            {pct != null ? fmtPct(pct, 2) : '—'}
                          </td>
                        )
                      })()}
                      {showFxSplit && mktKey && <><PnlCell val={Number(r[mktKey] ?? 0)} /><PnlCell val={fxKey ? Number(r[fxKey] ?? 0) : 0} /></>}
                      {(() => {
                        const unreal = Number(r.unrealized_pnl_eur ?? 0)
                        const pct = r.unrealized_pnl_pct != null ? Number(r.unrealized_pnl_pct) : null
                        return (
                          <>
                            <td className={`px-3 py-2 text-right tabular-nums ${unreal >= 0 ? 'text-green-700' : 'text-red-600'}`}>
                              {fmtEur(unreal)}
                            </td>
                            <td className={`px-3 py-2 text-right tabular-nums ${pct == null ? 'text-slate-400' : pct >= 0 ? 'text-green-700' : 'text-red-600'}`}>
                              {pct != null ? `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%` : '—'}
                            </td>
                          </>
                        )
                      })()}
                      <PnlCell val={Number(r.realized_pnl_eur ?? 0)} />
                      <td className="px-3 py-2 text-right tabular-nums text-slate-500">{r.dividend_yoc_pct != null ? `${Number(r.dividend_yoc_pct).toFixed(2)}%` : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </WithCopy>
        </div>
        )
      })() : (
        <div className="space-y-3">
          <WithCopy>
          <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-280px)]">
            <table className="w-full text-sm">
              <thead className="sticky top-0 z-10"><tr className="bg-slate-50 text-xs text-slate-500 uppercase tracking-wide">
                <ColHeader label="Account" sortKey="name" currentKey={acSK} currentDir={acSD} onSort={acSort} tooltip="Brokerage or investment account. Click a row to drill into individual security positions." />
                <ColHeader label="Value (€)" sortKey="value" currentKey={acSK} currentDir={acSD} onSort={acSort} align="right" tooltip="Current total market value of all holdings in this account, in EUR." />
                <ColHeader label={`P&L (${win.toUpperCase()})`} sortKey="pnl" currentKey={acSK} currentDir={acSD} onSort={acSort} align="right" tooltip={`Total P&L for the ${win.toUpperCase()} window across all holdings in this account.`} />
                {showPct && <ColHeader label="P&L %" sortKey="pnl_pct" currentKey={acSK} currentDir={acSD} onSort={acSort} align="right" tooltip="P&L as a percentage of the account's current market value." />}
                {showFxSplit && mktKey && <><ColHeader label="Market" sortKey="market" currentKey={acSK} currentDir={acSD} onSort={acSort} align="right" tooltip="P&L from price moves in local currency, excluding FX effects." /><ColHeader label="FX" sortKey="fx" currentKey={acSK} currentDir={acSD} onSort={acSort} align="right" tooltip="P&L from EUR/foreign-currency exchange rate movements." /></>}
                <ColHeader label="Unrealized" sortKey="unrealized" currentKey={acSK} currentDir={acSD} onSort={acSort} align="right" tooltip="Unrealized gain/loss: current value minus cost basis for open positions." />
                <ColHeader label="Unrealized %" sortKey="unrealized_pct" currentKey={acSK} currentDir={acSD} onSort={acSort} align="right" tooltip="Unrealized gain/loss as a percentage of cost basis." />
                <ColHeader label="Realized" sortKey="realized" currentKey={acSK} currentDir={acSD} onSort={acSort} align="right" tooltip="Realized gain/loss from closed positions in this account." />
              </tr></thead>
              <tbody className="divide-y divide-slate-100">
                {sortedAccounts.map(a => (
                  <tr key={a.name} className={`hover:bg-blue-50 cursor-pointer ${a.closed ? 'opacity-60' : ''}`} onClick={() => setSelectedAccount(a.name)}>
                    <td className="px-3 py-2 font-medium text-blue-700 hover:underline">{a.name}{a.closed && <span className="ml-1.5 text-xs text-slate-400 font-normal">(closed)</span>}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{fmtEur(a.value)}</td>
                    <PnlCell val={a.pnl} />
                    {showPct && <td className={`px-3 py-2 text-right tabular-nums text-xs ${a.pnl_pct == null ? 'text-slate-400' : a.pnl_pct >= 0 ? 'text-green-700' : 'text-red-600'}`}>{a.pnl_pct != null ? fmtPct(a.pnl_pct, 2) : '—'}</td>}
                    {showFxSplit && mktKey && <><PnlCell val={a.market ?? 0} /><PnlCell val={a.fx ?? 0} /></>}
                    <td className={`px-3 py-2 text-right tabular-nums ${a.unrealized >= 0 ? 'text-green-700' : 'text-red-600'}`}>
                      {fmtEur(a.unrealized)}
                    </td>
                    <td className={`px-3 py-2 text-right tabular-nums ${a.unrealized_pct == null ? 'text-slate-400' : a.unrealized_pct >= 0 ? 'text-green-700' : 'text-red-600'}`}>
                      {a.unrealized_pct != null ? `${a.unrealized_pct >= 0 ? '+' : ''}${a.unrealized_pct.toFixed(2)}%` : '—'}
                    </td>
                    <PnlCell val={a.realized} />
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          </WithCopy>
        </div>
      )}
    </div>
  )
}

function TwrTab({ accountIds }: { accountIds?: number[] }) {
  const { isDark } = useTheme()
  const liveRefetchMs = useLiveRefetchInterval()
  const [lookback, setLookback] = usePersist('twr_lookback', 730)
  const [cfOpen, setCfOpen] = useState(false)

  const { data, isLoading } = useQuery({
    queryKey: ['twr', lookback, accountIds],
    queryFn: () => getTwr(lookback, accountIds),
    refetchInterval: liveRefetchMs,
  })

  type TwrData = {
    twr_window_pct: number; twr_ann_pct: number; mwr_pct: number | null
    trading_days: number; date_from: string; date_to: string
    chart: { date: string; twr_cumulative_pct: number }[]
    cashflows: { date: string; action: string; account: string; security: string; amount_eur: number }[]
    insufficient: boolean
  }
  const d = data as TwrData | undefined

  return (
    <div className="space-y-5">
      {/* Description */}
      <div className="text-xs text-slate-500 space-y-1">
        <p className="font-medium text-slate-600">Two complementary measures of portfolio performance:</p>
        <ul className="list-disc pl-4 space-y-1">
          <li><strong>TWR (Time-Weighted Return)</strong>: eliminates the effect of <em>when</em> you deposited or withdrew money. It measures the portfolio manager's performance — directly comparable to an index return.</li>
          <li><strong>MWR (Money-Weighted Return / XIRR)</strong>: reflects <em>your actual experience</em> — the return you personally earned given the size and timing of your deposits and withdrawals. If you invested heavily before a downturn, MWR will be lower than TWR.</li>
        </ul>
        <p>TWR is computed from daily price-based portfolio returns. MWR uses all recorded Buy/Sell/Dividend cash flows plus the current portfolio value.</p>
      </div>

      {/* Lookback slider */}
      <div>
        <label className="block text-xs font-medium text-slate-600 mb-1">
          <Tooltip text="How many calendar days of price history to use for TWR. MWR always uses all-time cash flows regardless of this setting.">TWR Lookback</Tooltip>
        </label>
        <div className="flex gap-2">
          {([91, 182, 365, 730, 1095, 1825, 3650] as const).map(d => (
            <button key={d} onClick={() => setLookback(d)}
              className={`px-2 py-1 text-xs rounded border ${lookback === d ? 'bg-blue-600 text-white border-blue-600' : 'border-slate-300 text-slate-600 hover:bg-slate-50'}`}>
              {d === 91 ? '3M' : d === 182 ? '6M' : d === 365 ? '1Y' : d === 730 ? '2Y' : d === 1095 ? '3Y' : d === 1825 ? '5Y' : '10Y'}
            </button>
          ))}
        </div>
      </div>

      {isLoading && <div className="flex justify-center py-12"><Spinner /></div>}

      {d && (
        <>
          {/* KPI cards */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <KpiCard
              label={`TWR (${lookback}-day window)`}
              value={`${d.twr_window_pct >= 0 ? '+' : ''}${d.twr_window_pct.toFixed(2)}%`}
              color={d.twr_window_pct >= 0 ? 'text-green-700' : 'text-red-600'}
              tooltip="Total Time-Weighted Return over the selected lookback window. Eliminates the distortion caused by deposit/withdrawal timing." />
            <KpiCard
              label="TWR (Annualised)"
              value={`${d.twr_ann_pct >= 0 ? '+' : ''}${d.twr_ann_pct.toFixed(2)}%`}
              color={d.twr_ann_pct >= 0 ? 'text-green-700' : 'text-red-600'}
              tooltip="TWR scaled to a one-year equivalent compound rate, comparable across periods of different lengths." />
            <KpiCard
              label="MWR / XIRR (All-time)"
              value={d.mwr_pct != null ? `${d.mwr_pct >= 0 ? '+' : ''}${d.mwr_pct.toFixed(2)}%` : '—'}
              color={d.mwr_pct != null ? (d.mwr_pct >= 0 ? 'text-green-700' : 'text-red-600') : ''}
              tooltip="Money-Weighted Return (XIRR) computed from all-time cash flows. Reflects your personal return given the actual size and timing of each deposit and withdrawal." />
            <KpiCard
              label="Trading Days Used (TWR)"
              value={String(d.trading_days)}
              tooltip="Number of trading days with price data used to compute TWR in the selected lookback window." />
          </div>

          {d.trading_days > 0 && d.date_from && (
            <p className="text-xs text-slate-500">TWR window: <strong>{d.date_from}</strong> → <strong>{d.date_to}</strong>.</p>
          )}

          {d.insufficient && (
            <div className="bg-amber-50 border border-amber-200 text-amber-800 text-xs rounded px-3 py-2">
              ⚠️ Less than 10 days of price data available for the selected window. Extend the lookback or download more historical prices.
            </div>
          )}

          {/* Cumulative TWR chart */}
          {d.chart.length > 1 && (
            <div>
              <p className="text-sm font-semibold text-slate-700 mb-1">Cumulative Time-Weighted Return (%)</p>
              <Plot
                data={[{
                  x: d.chart.map(r => r.date),
                  y: d.chart.map(r => r.twr_cumulative_pct),
                  name: 'TWR (%)', type: 'scatter', mode: 'lines',
                  line: { color: '#6366f1', width: 1.5 },
                }]}
                layout={{
                  height: 360,
                  margin: { t: 10, r: 20, b: 50, l: 60 },
                  yaxis: { title: 'TWR (%)', zeroline: false },
                  xaxis: { title: 'Date' },
                  shapes: [{ type: 'line', x0: 0, x1: 1, xref: 'paper', y0: 0, y1: 0, line: { color: '#94a3b8', dash: 'dash', width: 1 } }],
                  ...plotLayout(isDark), hovermode: 'x unified',
                }}
                config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }} />
            </div>
          )}

          {/* Cash Flow Detail collapsible */}
          {d.cashflows.length > 0 && (
            <div className="border border-slate-200 rounded-lg overflow-hidden">
              <button onClick={() => setCfOpen(!cfOpen)}
                className="w-full flex items-center gap-2 px-4 py-2 text-sm font-medium text-slate-700 bg-slate-50 hover:bg-slate-100 text-left">
                <span className="text-xs">{cfOpen ? '▼' : '▶'}</span>
                <span>📋 Cash Flow Detail (MWR inputs)</span>
              </button>
              {cfOpen && (
                <div className="p-3">
                  <WithCopy>
                    <div className="overflow-x-auto overflow-y-auto max-h-96">
                      <table className="w-full text-sm">
                        <thead className="sticky top-0 z-10"><tr className="bg-slate-50 text-xs text-slate-500 uppercase tracking-wide">
                          <th className="px-3 py-2 text-left">Date</th>
                          <th className="px-3 py-2 text-left">Action</th>
                          <th className="px-3 py-2 text-left">Account</th>
                          <th className="px-3 py-2 text-left">Security</th>
                          <th className="px-3 py-2 text-right">Amount (€)</th>
                          <th className="px-3 py-2 text-right">CF Sign</th>
                        </tr></thead>
                        <tbody className="divide-y divide-slate-100">
                          {d.cashflows.map((r, i) => {
                            const isOut = ['Buy', 'MiscExp'].includes(r.action)
                            const actionColor = isOut
                              ? 'bg-red-50 text-red-700'
                              : ['Sell'].includes(r.action) ? 'bg-green-50 text-green-700'
                              : ['Dividend', 'IntInc', 'RtrnCap'].includes(r.action) ? 'bg-blue-50 text-blue-700'
                              : 'bg-slate-100 text-slate-600'
                            return (
                              <tr key={i} className="hover:bg-slate-50">
                                <td className="px-3 py-2 text-slate-500">{r.date}</td>
                                <td className="px-3 py-2">
                                  <span className={`text-xs font-medium px-1.5 py-0.5 rounded ${actionColor}`}>{r.action}</span>
                                </td>
                                <td className="px-3 py-2 text-slate-600 text-xs">{r.account}</td>
                                <td className="px-3 py-2 text-slate-500 text-xs">{r.security || '—'}</td>
                                <td className="px-3 py-2 text-right tabular-nums">{fmtEur(Math.abs(r.amount_eur))}</td>
                                <td className={`px-3 py-2 text-right tabular-nums font-medium ${isOut ? 'text-red-600' : 'text-green-700'}`}>
                                  {isOut ? '−' : '+'}{fmtEur(Math.abs(r.amount_eur))}
                                </td>
                              </tr>
                            )
                          })}
                        </tbody>
                      </table>
                    </div>
                  </WithCopy>
                </div>
              )}
            </div>
          )}

          {/* Interpretation guide */}
          <div className="bg-blue-50 border border-blue-100 rounded-lg px-4 py-3 text-xs text-blue-800">
            <strong>Interpretation guide:</strong> If TWR &gt; MWR, you tended to invest more capital <em>before</em> underperforming periods. If MWR &gt; TWR, your larger investments coincided with stronger performance — good market timing added personal value beyond the portfolio's intrinsic return.
          </div>
        </>
      )}
    </div>
  )
}

function RiskMetricsTab({ accountIds }: { accountIds?: number[] }) {
  const { isDark } = useTheme()
  const liveRefetchMs = useLiveRefetchInterval()
  const [lookback, setLookback] = usePersist('risk_lookback', 730)
  const [benchSecId, setBenchSecId] = usePersist<number | null>('risk_bench_sec_id', null)

  const { data: bmCandidates = [] } = useQuery({
    queryKey: ['benchmark-candidates'], queryFn: getBenchmarkCandidates, staleTime: 3_600_000,
  })
  const bms = bmCandidates as Row[]

  const { data, isLoading } = useQuery({
    queryKey: ['risk-metrics', lookback, benchSecId, accountIds],
    queryFn: () => getRiskMetrics(lookback, benchSecId, accountIds),
    refetchInterval: liveRefetchMs,
  })

  type RiskData = {
    ann_vol_pct: number; sharpe: number; sortino: number; max_drawdown_pct: number
    var_95_pct: number; cvar_95_pct: number; var_95_eur: number; cvar_95_eur: number
    beta: number | null; alpha: number | null
    trading_days: number; date_from: string; date_to: string
    portfolio_value: number; rolling_sharpe: { date: string; sharpe: number }[]
    insufficient: boolean
  }
  const d = data as RiskData | undefined

  return (
    <div className="space-y-5">
      {/* Description */}
      <div className="text-xs text-slate-500">
        <p>Quantifies the risk profile of your current portfolio using historical price data. Returns are <strong>value-weighted</strong> by current position size and use a <strong>3% risk-free rate</strong>. Hover any metric below for its definition.</p>
      </div>

      {/* Controls */}
      <div className="grid grid-cols-2 gap-6">
        <div>
          <label className="block text-xs font-medium text-slate-600 mb-1">
            <Tooltip text="How many calendar days of price history to use. Longer windows smooth out short-term noise but may include outdated market regimes.">Lookback</Tooltip>
          </label>
          <div className="flex gap-2">
            {([91, 182, 365, 730, 1095, 1825, 3650] as const).map(d => (
              <button key={d} onClick={() => setLookback(d)}
                className={`px-2 py-1 text-xs rounded border ${lookback === d ? 'bg-blue-600 text-white border-blue-600' : 'border-slate-300 text-slate-600 hover:bg-slate-50'}`}>
                {d === 91 ? '3M' : d === 182 ? '6M' : d === 365 ? '1Y' : d === 730 ? '2Y' : d === 1095 ? '3Y' : d === 1825 ? '5Y' : '10Y'}
              </button>
            ))}
          </div>
        </div>
        <div>
          <label className="block text-xs font-medium text-slate-600 mb-1"><Tooltip text="Market index used to compute Beta (sensitivity) and Jensen's Alpha (excess return vs CAPM prediction). Leave blank to skip both.">Benchmark for Beta / Alpha</Tooltip></label>
          <select className="w-full border border-slate-300 rounded px-2 py-1.5 text-sm focus:outline-none focus:border-blue-400"
            value={benchSecId ?? ''}
            onChange={e => setBenchSecId(e.target.value ? Number(e.target.value) : null)}>
            <option value="">— None —</option>
            {bms.map(b => <option key={b.id as number} value={b.id as number}>{b.name as string}</option>)}
          </select>
        </div>
      </div>

      {isLoading && <div className="flex justify-center py-12"><Spinner /></div>}

      {d && (
        <>
          {/* Data range info / warning */}
          {d.insufficient
            ? <div className="bg-amber-50 border border-amber-200 text-amber-800 text-xs rounded px-3 py-2">
                ⚠️ Only <strong>{d.trading_days} trading days</strong> of data available ({d.date_from} → {d.date_to}), covering less than half the requested {lookback} calendar-day window. Download more historical prices to extend the analysis.
              </div>
            : d.trading_days > 0
              ? <p className="text-xs text-slate-500">Using <strong>{d.trading_days} trading days</strong> of return data ({d.date_from} → {d.date_to}) within a {lookback}-calendar-day window.</p>
              : <p className="text-xs text-slate-400">Insufficient price history — need at least 30 days of data for current holdings.</p>
          }

          {d.trading_days > 0 && (
            <>
              {/* Metrics grid — 2 rows of 4 */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <KpiCard label="Ann. Volatility"    value={`${d.ann_vol_pct.toFixed(2)}%`} tooltip="Annualised standard deviation of daily returns, value-weighted by current position size. Higher = more volatile portfolio." />
                <KpiCard label="Sharpe Ratio"       value={d.sharpe.toFixed(2)}  color={d.sharpe >= 1 ? 'text-green-700' : d.sharpe < 0 ? 'text-red-600' : ''} tooltip="Excess return over the 3% risk-free rate, divided by total volatility. Above 1.0 is good; above 2.0 is excellent." />
                <KpiCard label="Sortino Ratio"      value={d.sortino.toFixed(2)} color={d.sortino >= 1 ? 'text-green-700' : d.sortino < 0 ? 'text-red-600' : ''} tooltip="Like Sharpe but only penalises downside volatility, ignoring upside swings. Better metric when return distribution is positively skewed." />
                <KpiCard label="Max Drawdown"       value={`${d.max_drawdown_pct.toFixed(2)}%`} color="text-red-600" tooltip="Largest peak-to-trough decline in portfolio value during the selected lookback period." />
                <KpiCard label="VaR 95% (daily)"    value={`${d.var_95_pct.toFixed(2)}%  ·  € ${fmtNum(d.var_95_eur, 0)}`}  color="text-amber-600" tooltip="Value at Risk: on a typical day, there is only a 5% chance of losing more than this amount. Shown as % and EUR at current portfolio value." />
                <KpiCard label="CVaR 95% (daily)"   value={`${d.cvar_95_pct.toFixed(2)}%  ·  € ${fmtNum(d.cvar_95_eur, 0)}`} color="text-amber-600" tooltip="Conditional VaR (Expected Shortfall): average loss on the worst 5% of days. A more conservative tail-risk measure than plain VaR." />
                <KpiCard label="Beta"               value={d.beta  != null ? d.beta.toFixed(2)   : '—'} subtitle={benchSecId ? bms.find(b => b.id === benchSecId)?.name as string : undefined} tooltip="Sensitivity of your portfolio's returns to the chosen benchmark. Beta > 1 means the portfolio amplifies benchmark moves; < 1 means it dampens them." />
                <KpiCard label="Alpha (annualised)" value={d.alpha != null ? `${d.alpha.toFixed(2)}%` : '—'} color={d.alpha != null ? (d.alpha > 0 ? 'text-green-700' : 'text-red-600') : ''} tooltip="Jensen's Alpha: annualised excess return above what CAPM predicts given your Beta. Positive = genuine outperformance after adjusting for market risk." />
              </div>

              {/* Rolling 30-day Sharpe chart */}
              {d.rolling_sharpe.length > 0 && (
                <div>
                  <p className="text-sm font-semibold text-slate-700 mb-1">Rolling 30-Day Sharpe Ratio</p>
                  <Plot
                    data={[{
                      x: d.rolling_sharpe.map(r => r.date),
                      y: d.rolling_sharpe.map(r => r.sharpe),
                      type: 'scatter', mode: 'lines',
                      line: { color: '#6366f1', width: 1.5 },
                      name: 'Sharpe',
                    }]}
                    layout={{
                      height: 300,
                      margin: { t: 10, r: 10, b: 40, l: 60 },
                      yaxis: { title: 'Sharpe Ratio', zeroline: false },
                      xaxis: { title: 'Date' },
                      shapes: [{ type: 'line', x0: 0, x1: 1, xref: 'paper', y0: 0, y1: 0, line: { color: '#E74C3C', dash: 'dash', width: 1.5 } }],
                      ...plotLayout(isDark), hovermode: 'x unified',
                    }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }} />
                  {d.portfolio_value > 0 && (
                    <p className="text-xs text-slate-400 mt-1">
                      Returns are value-weighted by current position size (total: € {fmtNum(d.portfolio_value, 0)}). VaR/CVaR EUR figures assume this portfolio size.
                    </p>
                  )}
                </div>
              )}
            </>
          )}
        </>
      )}
    </div>
  )
}

const DIV_PERIODS = ['YTD', 'Previous Year', '1 Year', '2 Years', '3 Years', '5 Years', 'All Time', 'Custom']
const PIE_COLORS = ['#6366f1', '#ef4444', '#10b981', '#a855f7', '#f59e0b', '#3b82f6', '#ec4899', '#84cc16']

function DividendTrackerTab() {
  const { isDark } = useTheme()
  const liveRefetchMs = useLiveRefetchInterval()
  const [divView, setDivView] = usePersist<'actual' | 'forecast' | 'recommendations'>('div_view', 'actual')

  // ── Actual state ─────────────────────────────────────────────────────────────
  const [period, setPeriod] = usePersist('div_period', 'YTD')
  const [customFrom, setCustomFrom] = usePersist('div_from', new Date(Date.now() - 365 * 86400000).toISOString().slice(0, 10))
  const [customTo, setCustomTo] = usePersist('div_to', new Date().toISOString().slice(0, 10))
  const [detailOpen, setDetailOpen] = useState(false)

  const { data, isLoading } = useQuery({
    queryKey: ['dividends-tracker', period, period === 'Custom' ? customFrom : null, period === 'Custom' ? customTo : null],
    queryFn: () => getDividendsTracker(period, period === 'Custom' ? customFrom : undefined, period === 'Custom' ? customTo : undefined),
    refetchInterval: liveRefetchMs,
  })

  // ── Forecast state ────────────────────────────────────────────────────────────
  const [upcomingOpen, setUpcomingOpen] = useState(false)
  const [fcPeriod, setFcPeriod] = usePersist<'eoy' | '6m' | '12m'>('div_forecast_period', '12m')

  const { data: fcData, isLoading: fcLoading } = useQuery({
    queryKey: ['dividends-forecast', fcPeriod],
    queryFn: () => getDividendsForecast(fcPeriod),
    enabled: divView === 'forecast',
  })

  // ── Recommendations state ─────────────────────────────────────────────────────
  const [recHolding, setRecHolding] = usePersist<'all' | 'new' | 'held'>('rec_holding', 'all')
  const [recMinYield, setRecMinYield] = usePersist('rec_min_yield', 0)
  const [recType, setRecType] = usePersist('rec_type', 'All')

  const { data: recData, isLoading: recLoading } = useQuery({
    queryKey: ['dividend-recommendations'],
    queryFn: getDividendRecommendations,
    enabled: divView === 'recommendations',
  })

  type RecRow = {
    securities_id: number; securities_name: string; securities_type: string; sector: string | null
    effective_yield_pct: number; five_year_avg_yield: number | null; dividend_frequency: string | null
    analyst_rating: string | null; sharpe_ratio: number | null; div_payments_3yr: number
    trailing_12m_eur: number; market_value_eur: number | null; cost_basis_eur: number | null
    is_held: boolean; yield_score: number; sharpe_score: number | null; consistency_score: number
    growth_score: number | null; analyst_score: number | null; composite_score: number; tags: string[]
  }
  const recRows = (recData ?? []) as RecRow[]
  const recTypes = useMemo(() => ['All', ...Array.from(new Set(recRows.map(r => r.securities_type))).sort()], [recRows])
  const filteredRec = useMemo(() => recRows.filter(r => {
    if (recType !== 'All' && r.securities_type !== recType) return false
    if (recHolding === 'new' && r.is_held) return false
    if (recHolding === 'held' && !r.is_held) return false
    if (r.effective_yield_pct < recMinYield) return false
    return true
  }), [recRows, recType, recHolding, recMinYield])
  const { sorted: recSorted, sortKey: recSK, sortDir: recSD, toggleSort: recSort } = useSortTablePersisted(filteredRec, 'div-tracker-recommendations-sort', 'composite_score', 'desc')

  type TrackerResult = {
    period_label: string
    monthly: { month: string; income_eur: number }[]
    by_security: Row[]
    by_type: { securities_type: string; period_income_eur: number }[]
    detail: Row[]
    summary: Row
  }
  type ForecastResult = {
    period: 'eoy' | '6m' | '12m'
    summary: { total_period_eur: number; total_annual_eur: number; total_monthly_eur: number; securities_count: number; portfolio_yoc_pct: number }
    monthly_forecast: { month: string; income_eur: number }[]
    by_security: Row[]
    upcoming: Row[]
  }

  const result   = data   as TrackerResult  | undefined
  const fcResult = fcData as ForecastResult | undefined

  // Per-month "which securities paid" breakdown for the monthly chart's hover —
  // built client-side from the same per-transaction `detail` rows that back the
  // Full Transaction Detail table below, so it always matches. Capped at the top
  // 8 payers per month (by income) so a month with dozens of small payers doesn't
  // produce an unreadably tall tooltip.
  const monthlyBreakdown = useMemo(() => {
    const byMonth = new Map<string, Map<string, number>>()
    for (const r of result?.detail ?? []) {
      const month = String(r.month)
      const name = String(r.securities_name)
      const income = Number(r.income_eur ?? 0)
      if (!byMonth.has(month)) byMonth.set(month, new Map())
      const bySec = byMonth.get(month)!
      bySec.set(name, (bySec.get(name) ?? 0) + income)
    }
    const out: Record<string, string> = {}
    for (const [month, bySec] of byMonth) {
      const sorted = Array.from(bySec.entries()).sort((a, b) => b[1] - a[1])
      const top = sorted.slice(0, 8).map(([name, inc]) => `${name}: ${fmtEur(inc)}`).join('<br>')
      out[month] = sorted.length > 8 ? `${top}<br>+${sorted.length - 8} more` : top
    }
    return out
  }, [result])

  const { sorted: divSorted,  sortKey: divSK,  sortDir: divSD,  toggleSort: divSort  } = useSortTablePersisted(result?.by_security   ?? [], 'div-tracker-actual-sort', 'period_income_eur',    'desc')
  const { sorted: fcSorted,   sortKey: fcSK,   sortDir: fcSD,   toggleSort: fcSort   } = useSortTablePersisted(fcResult?.by_security ?? [], 'div-tracker-forecast-sort', 'period_forecast_eur',  'desc')

  // Income-by-Security rows are aggregated per security across the whole period —
  // expanding one shows the underlying per-transaction rows from `detail` (same
  // source as the monthly chart's hover and the Full Transaction Detail table),
  // grouped by securities_id so two differently-named-but-same-id edge cases
  // (shouldn't happen, but detail also has securities_name as a fallback key)
  // can't cross-contaminate.
  const [expandedSec, setExpandedSec] = useState<Set<number>>(new Set())
  const toggleSecExpand = (id: number) => setExpandedSec(prev => {
    const s = new Set(prev)
    s.has(id) ? s.delete(id) : s.add(id)
    return s
  })
  const detailBySecId = useMemo(() => {
    const map = new Map<number, Row[]>()
    for (const r of result?.detail ?? []) {
      const id = Number(r.securities_id)
      if (!map.has(id)) map.set(id, [])
      map.get(id)!.push(r)
    }
    for (const rows of map.values()) rows.sort((a, b) => String(b.date).localeCompare(String(a.date)))
    return map
  }, [result])

  // ── View toggle ───────────────────────────────────────────────────────────────
  const VIEW_LABELS: Record<string, string> = { actual: '📋 Actual', forecast: '🔮 Forecast', recommendations: '💡 Recommendations' }
  const ViewToggle = (
    <div className="flex gap-1 mb-4">
      {(['actual', 'forecast', 'recommendations'] as const).map(v => (
        <button key={v} onClick={() => setDivView(v)}
          className={`px-4 py-1.5 text-xs rounded-full font-medium border transition-colors ${divView === v ? 'bg-blue-600 text-white border-blue-600' : 'border-slate-300 text-slate-600 hover:bg-slate-50'}`}>
          {VIEW_LABELS[v]}
        </button>
      ))}
    </div>
  )

  // ── Forecast view ─────────────────────────────────────────────────────────────
  const FC_PERIOD_LABELS: Record<'eoy' | '6m' | '12m', string> = { eoy: 'Till EOY', '6m': 'Next 6 Months', '12m': 'Next 12 Months' }
  const FC_PERIOD_SHORT:  Record<'eoy' | '6m' | '12m', string> = { eoy: 'EOY', '6m': '6mo', '12m': '12mo' }
  if (divView === 'forecast') {
    return (
      <div className="space-y-4">
        {ViewToggle}
        <div className="flex gap-1.5">
          {(['eoy', '6m', '12m'] as const).map(p => (
            <button key={p} onClick={() => setFcPeriod(p)}
              className={`px-3 py-1.5 text-xs rounded border font-medium ${fcPeriod === p ? 'bg-blue-600 text-white border-blue-600' : 'border-slate-300 text-slate-600 hover:bg-slate-50'}`}>
              {FC_PERIOD_LABELS[p]}
            </button>
          ))}
        </div>
        {fcLoading ? <div className="flex justify-center py-12"><Spinner /></div>
          : !fcResult || !fcResult.by_security.length ? (
            <p className="text-slate-400 text-sm py-8 text-center">No forecast data for this period — no holdings with an expected dividend payment before {FC_PERIOD_LABELS[fcPeriod].toLowerCase()}.</p>
          ) : (
          <>
            <div className="grid grid-cols-4 gap-4">
              <div className="bg-slate-50 rounded-lg p-4 text-center">
                <p className="text-xs text-slate-500 mb-1"><Tooltip text={`Total projected dividend and interest income ${fcPeriod === 'eoy' ? 'between now and the end of this year' : `over the ${fcPeriod === '6m' ? 'next 6 months' : 'next 12 months'}`}, based on current holdings' real expected payment dates.`}>Projected ({FC_PERIOD_SHORT[fcPeriod]})</Tooltip></p>
                <p className="text-xl font-bold text-green-600">{fmtEur(fcResult.summary.total_period_eur)}</p>
              </div>
              <div className="bg-slate-50 rounded-lg p-4 text-center">
                <p className="text-xs text-slate-500 mb-1"><Tooltip text="Projected period total divided by the number of months in the period — average expected monthly income.">Monthly Average</Tooltip></p>
                <p className="text-xl font-bold">{fmtEur(fcResult.summary.total_monthly_eur)}</p>
              </div>
              <div className="bg-slate-50 rounded-lg p-4 text-center">
                <p className="text-xs text-slate-500 mb-1"><Tooltip text="Number of currently-held securities with at least one dividend payment expected within this period.">Securities</Tooltip></p>
                <p className="text-xl font-bold">{fcResult.summary.securities_count}</p>
              </div>
              <div className="bg-slate-50 rounded-lg p-4 text-center">
                <p className="text-xs text-slate-500 mb-1"><Tooltip text="Full-year projected income (regardless of the period selected above) divided by the total cost basis of forecasted holdings — the standard annualized Yield-on-Cost metric.">Portfolio YOC</Tooltip></p>
                <p className="text-xl font-bold">{fcResult.summary.portfolio_yoc_pct.toFixed(2)}%</p>
              </div>
            </div>

            {fcResult.monthly_forecast.length > 0 && (
              <Plot
                data={[{ x: fcResult.monthly_forecast.map(m => m.month), y: fcResult.monthly_forecast.map(m => m.income_eur), type: 'bar', marker: { color: '#3b82f6' }, name: 'Projected' }]}
                layout={{
                  title: `Projected Monthly Dividend Income (€) — ${FC_PERIOD_LABELS[fcPeriod]}`,
                  height: 320, margin: { t: 50, r: 20, b: 40, l: 60 },
                  yaxis: { title: 'Projected Income (€)' },
                  xaxis: { tickformat: '%b %Y', dtick: 'M1', type: 'date' as const },
                  ...plotLayout(isDark),
                }}
                config={{ displayModeBar: false }} style={{ width: '100%' }}
              />
            )}

            <WithCopy>
              <div className="overflow-y-auto max-h-[calc(100vh-300px)]">
                <table className="w-full text-sm table-fixed">
                  <colgroup>
                    <col className="w-[20%]" />
                    <col className="w-[9%]" />
                    <col className="w-[8%]" />
                    <col className="w-[8%]" />
                    <col className="w-[10%]" />
                    <col className="w-[6%]" />
                    <col className="w-[8%]" />
                    <col className="w-[8%]" />
                    <col className="w-[8%]" />
                    <col className="w-[8%]" />
                    <col className="w-[7%]" />
                  </colgroup>
                  <thead className="sticky top-0 z-10"><tr className="bg-slate-50 text-xs text-slate-500 uppercase tracking-wide">
                    <ColHeader label="Security"      sortKey="securities_name"        currentKey={fcSK} currentDir={fcSD} onSort={fcSort} tooltip="Security name." />
                    <ColHeader label={`Amt (${FC_PERIOD_SHORT[fcPeriod]}) (€)`} sortKey="period_forecast_eur" currentKey={fcSK} currentDir={fcSD} onSort={fcSort} align="right" tooltip="Sum of actual expected payments landing within the selected period." />
                    <ColHeader label="Ann. Rate (€)" sortKey="annual_forecast_eur"    currentKey={fcSK} currentDir={fcSD} onSort={fcSort} align="right" tooltip="Full-year projected dividend income in EUR, regardless of the period selected above." />
                    <ColHeader label="Per Pmt (€)"   sortKey="per_payment_eur"        currentKey={fcSK} currentDir={fcSD} onSort={fcSort} align="right" tooltip="Expected income per dividend payment (annual ÷ payments per year)." />
                    <ColHeader label="Frequency"     sortKey="frequency"              currentKey={fcSK} currentDir={fcSD} onSort={fcSort} tooltip="How often dividends are paid." />
                    <ColHeader label="Yield %"       sortKey="dividend_yield"         currentKey={fcSK} currentDir={fcSD} onSort={fcSort} align="right" tooltip="Forward dividend yield from securities metadata." />
                    <ColHeader label="Ex-Div"        sortKey="next_expected_ex_date"  currentKey={fcSK} currentDir={fcSD} onSort={fcSort} align="right" tooltip="Projected next ex-dividend date. Must hold shares before this date." />
                    <ColHeader label="Pay Date"      sortKey="next_expected_pay_date" currentKey={fcSK} currentDir={fcSD} onSort={fcSort} align="right" tooltip="Projected date cash arrives in your account." />
                    <ColHeader label="Mkt Val (€)"   sortKey="market_value_eur"       currentKey={fcSK} currentDir={fcSD} onSort={fcSort} align="right" tooltip="Current market value of held position." />
                    <ColHeader label="Cost (€)"      sortKey="cost_basis_eur"         currentKey={fcSK} currentDir={fcSD} onSort={fcSort} align="right" tooltip="Total acquisition cost of held position." />
                    <ColHeader label="Basis"         sortKey="method"                 currentKey={fcSK} currentDir={fcSD} onSort={fcSort} tooltip="How the forecast was calculated: Dividend Rate (most accurate), Fwd Yield, or Trailing 12m actual income." />
                  </tr></thead>
                  <tbody className="divide-y divide-slate-100">
                    {fcSorted.map((r, i) => (
                      <tr key={i} className="hover:bg-slate-50">
                        <td className="px-2 py-1.5 font-medium truncate" title={String(r.securities_name)}><SecLink id={r.securities_id}>{String(r.securities_name)}</SecLink></td>
                        <td className="px-2 py-1.5 text-right tabular-nums font-semibold text-green-600">{fmtEur(Number(r.period_forecast_eur))}</td>
                        <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{fmtEur(Number(r.annual_forecast_eur))}</td>
                        <td className="px-2 py-1.5 text-right tabular-nums">{fmtEur(Number(r.per_payment_eur))}</td>
                        <td className="px-2 py-1.5 text-slate-500 truncate" title={String(r.frequency)}>{String(r.frequency)}</td>
                        <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{r.dividend_yield != null ? `${Number(r.dividend_yield).toFixed(2)}%` : '—'}</td>
                        <td className="px-2 py-1.5 text-right text-slate-500 whitespace-nowrap">{r.next_expected_ex_date ? String(r.next_expected_ex_date).slice(0, 10) : '—'}</td>
                        <td className="px-2 py-1.5 text-right text-slate-500 whitespace-nowrap">{r.next_expected_pay_date ? String(r.next_expected_pay_date).slice(0, 10) : '—'}</td>
                        <td className="px-2 py-1.5 text-right tabular-nums">{fmtEur(Number(r.market_value_eur))}</td>
                        <td className="px-2 py-1.5 text-right tabular-nums">{fmtEur(Number(r.cost_basis_eur))}</td>
                        <td className="px-2 py-1.5">
                          <span className={`whitespace-nowrap px-1 py-0.5 rounded text-[10px] ${r.method === 'Dividend Rate' ? 'bg-green-100 text-green-700' : r.method === 'Fwd Yield' ? 'bg-blue-100 text-blue-700' : 'bg-slate-100 text-slate-600'}`}>
                            {r.method === 'Dividend Rate' ? 'Div Rate' : r.method === 'Trailing 12m' ? 'Trail 12m' : String(r.method)}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </WithCopy>

            <div className="border border-slate-200 rounded-lg overflow-hidden">
              <button onClick={() => setUpcomingOpen(!upcomingOpen)}
                className="w-full flex items-center gap-2 px-4 py-2 text-sm font-medium text-slate-700 bg-slate-50 hover:bg-slate-100 text-left">
                <span className="text-xs">{upcomingOpen ? '▼' : '▶'}</span>
                <span>📅 Upcoming payments (next 3 months)</span>
              </button>
              {upcomingOpen && (
                <div className="p-3">
                  {fcResult.upcoming.length === 0 ? (
                    <p className="text-slate-400 text-sm text-center py-4">No payments expected in the next 3 months.</p>
                  ) : (
                    <div className="overflow-x-auto overflow-y-auto max-h-80">
                      <table className="w-full text-sm">
                        <thead className="sticky top-0 z-10"><tr className="bg-slate-50 text-xs text-slate-500 uppercase tracking-wide">
                          <th className="px-3 py-2 text-left">Ex-Date</th>
                          <th className="px-3 py-2 text-left">Pay Date</th>
                          <th className="px-3 py-2 text-left">Security</th>
                          <th className="px-3 py-2 text-right">Amount (€)</th>
                          <th className="px-3 py-2 text-left">Frequency</th>
                          <th className="px-3 py-2 text-left">Basis</th>
                        </tr></thead>
                        <tbody className="divide-y divide-slate-100">
                          {fcResult.upcoming.map((r, i) => (
                            <tr key={i} className="hover:bg-slate-50">
                              <td className="px-3 py-2 text-slate-500">{String(r.ex_date).slice(0, 10)}</td>
                              <td className="px-3 py-2 text-slate-500">{String(r.pay_date).slice(0, 10)}</td>
                              <td className="px-3 py-2 font-medium">{String(r.securities_name)}</td>
                              <td className="px-3 py-2 text-right tabular-nums font-semibold text-green-600">{fmtEur(Number(r.per_payment_eur))}</td>
                              <td className="px-3 py-2 text-slate-500">{String(r.frequency)}</td>
                              <td className="px-3 py-2 text-slate-500 text-xs">{String(r.method)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}
            </div>
          </>
        )}
      </div>
    )
  }

  // ── Recommendations view ──────────────────────────────────────────────────────
  const _ANALYST_COLOR: Record<string, string> = {
    strong_buy: 'bg-green-100 text-green-800', buy: 'bg-green-100 text-green-700', outperform: 'bg-green-100 text-green-700',
    hold: 'bg-yellow-100 text-yellow-700', neutral: 'bg-yellow-100 text-yellow-700', market_perform: 'bg-yellow-100 text-yellow-700',
    underperform: 'bg-orange-100 text-orange-700', sell: 'bg-red-100 text-red-700', strong_sell: 'bg-red-100 text-red-800',
  }
  const _scoreBadge = (s: number) =>
    s >= 70 ? 'bg-green-100 text-green-800' : s >= 50 ? 'bg-yellow-100 text-yellow-700' : 'bg-slate-100 text-slate-600'
  const _sharpeColor = (v: number | null) =>
    v === null ? 'text-slate-400' : v >= 1.0 ? 'text-green-600 font-medium' : v >= 0 ? 'text-slate-700' : 'text-red-500'

  if (divView === 'recommendations') {
    return (
      <div className="space-y-4">
        {ViewToggle}
        <p className="text-xs text-slate-400">Scores are data-driven (yield · Sharpe · consistency · analyst signal · dividend growth). Not financial advice.</p>

        {/* Filters */}
        <div className="flex flex-wrap gap-4 items-end">
          <div>
            <p className="text-xs text-slate-500 mb-1">Holdings</p>
            <div className="flex gap-1">
              {(['all', 'new', 'held'] as const).map(v => (
                <button key={v} onClick={() => setRecHolding(v)}
                  className={`px-3 py-1 text-xs rounded border font-medium ${recHolding === v ? 'bg-blue-600 text-white border-blue-600' : 'border-slate-300 text-slate-600 hover:bg-slate-50'}`}>
                  {v === 'all' ? 'All' : v === 'new' ? 'Not held' : 'Held'}
                </button>
              ))}
            </div>
          </div>
          <div>
            <p className="text-xs text-slate-500 mb-1">Min Yield</p>
            <div className="flex gap-1">
              {[0, 1, 2, 3, 5].map(y => (
                <button key={y} onClick={() => setRecMinYield(y)}
                  className={`px-3 py-1 text-xs rounded border font-medium ${recMinYield === y ? 'bg-blue-600 text-white border-blue-600' : 'border-slate-300 text-slate-600 hover:bg-slate-50'}`}>
                  {y === 0 ? 'Any' : `${y}%+`}
                </button>
              ))}
            </div>
          </div>
          {recTypes.length > 1 && (
            <div>
              <p className="text-xs text-slate-500 mb-1">Type</p>
              <div className="flex flex-wrap gap-1">
                {recTypes.map(t => (
                  <button key={t} onClick={() => setRecType(t)}
                    className={`px-3 py-1 text-xs rounded border font-medium ${recType === t ? 'bg-blue-600 text-white border-blue-600' : 'border-slate-300 text-slate-600 hover:bg-slate-50'}`}>
                    {t}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        {recLoading ? <div className="flex justify-center py-12"><Spinner /></div>
          : !recSorted.length ? (
            <p className="text-slate-400 text-sm py-8 text-center">No securities match the selected filters.</p>
          ) : (
          <WithCopy>
            <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-260px)]">
              <table className="w-full text-sm">
                <thead className="sticky top-0 z-10"><tr className="bg-slate-50 text-xs text-slate-500 uppercase tracking-wide">
                  <ColHeader label="Security"     sortKey="securities_name"     currentKey={recSK} currentDir={recSD} onSort={recSort} />
                  <ColHeader label="Type"         sortKey="securities_type"     currentKey={recSK} currentDir={recSD} onSort={recSort} />
                  <ColHeader label="Score"        sortKey="composite_score"     currentKey={recSK} currentDir={recSD} onSort={recSort} align="right"
                    tooltip="Composite score (0–100): Yield 35% · Sharpe 25% · Consistency 25% · Analyst 10% · Yield Growth 5%. Missing factors are excluded and remaining weights renormalised." />
                  <ColHeader label="Yield %"      sortKey="effective_yield_pct" currentKey={recSK} currentDir={recSD} onSort={recSort} align="right"
                    tooltip="Forward dividend yield (from securities metadata). Falls back to trailing 12-month income ÷ market value when forward yield is unavailable." />
                  <ColHeader label="5yr Avg %"    sortKey="five_year_avg_yield" currentKey={recSK} currentDir={recSD} onSort={recSort} align="right"
                    tooltip="5-year average dividend yield — used to assess yield stability and growth trend." />
                  <ColHeader label="Sharpe (1yr)" sortKey="sharpe_ratio"        currentKey={recSK} currentDir={recSD} onSort={recSort} align="right"
                    tooltip="Annualised Sharpe ratio from the last 365 days of daily prices, using 3% as risk-free rate. Requires ≥30 price points." />
                  <ColHeader label="Consistency"  sortKey="div_payments_3yr"    currentKey={recSK} currentDir={recSD} onSort={recSort} align="right"
                    tooltip="Number of dividend payments recorded in the last 3 years. Higher = more reliable payer." />
                  <ColHeader label="Analyst"      sortKey="analyst_score"       currentKey={recSK} currentDir={recSD} onSort={recSort}
                    tooltip="Analyst consensus rating from securities metadata (Strong Buy / Buy / Hold / Sell)." />
                  <ColHeader label="Held"         sortKey="market_value_eur"    currentKey={recSK} currentDir={recSD} onSort={recSort} align="right"
                    tooltip="Current market value of your holding, or — if not held." />
                  <th className="px-3 py-2 text-left text-xs font-semibold text-slate-500 uppercase tracking-wide">Tags</th>
                </tr></thead>
                <tbody className="divide-y divide-slate-100">
                  {recSorted.map((r, i) => (
                    <tr key={i} className="hover:bg-slate-50">
                      <td className="px-3 py-2 font-medium max-w-[180px] truncate"><SecLink id={r.securities_id}>{r.securities_name}</SecLink></td>
                      <td className="px-3 py-2 text-slate-500 text-xs">{r.securities_type}</td>
                      <td className="px-3 py-2 text-right">
                        <Tooltip text={`Yield: ${r.yield_score.toFixed(0)} · Sharpe: ${r.sharpe_score?.toFixed(0) ?? 'n/a'} · Consistency: ${r.consistency_score.toFixed(0)} · Analyst: ${r.analyst_score ?? 'n/a'} · Growth: ${r.growth_score?.toFixed(0) ?? 'n/a'}`}>
                          <span className={`text-xs font-bold px-2 py-0.5 rounded-full ${_scoreBadge(r.composite_score)}`}>{r.composite_score.toFixed(0)}</span>
                        </Tooltip>
                      </td>
                      <td className={`px-3 py-2 text-right tabular-nums font-medium ${r.effective_yield_pct >= 4 ? 'text-green-600' : 'text-slate-700'}`}>
                        {r.effective_yield_pct > 0 ? `${r.effective_yield_pct.toFixed(2)}%` : '—'}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums text-slate-500 text-xs">
                        {r.five_year_avg_yield != null ? `${r.five_year_avg_yield.toFixed(2)}%` : '—'}
                      </td>
                      <td className={`px-3 py-2 text-right tabular-nums text-xs ${_sharpeColor(r.sharpe_ratio)}`}>
                        {r.sharpe_ratio != null ? r.sharpe_ratio.toFixed(2) : '—'}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums text-xs text-slate-600">{r.div_payments_3yr || '—'}</td>
                      <td className="px-3 py-2">
                        {r.analyst_rating ? (
                          <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${_ANALYST_COLOR[r.analyst_rating] ?? 'bg-slate-100 text-slate-600'}`}>
                            {r.analyst_rating.replace(/_/g, ' ')}
                          </span>
                        ) : '—'}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums text-xs">
                        {r.is_held && r.market_value_eur != null ? (
                          <span className="text-blue-600 font-medium">{fmtEur(r.market_value_eur)}</span>
                        ) : <span className="text-slate-400">—</span>}
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex flex-wrap gap-1">
                          {r.tags.map(tag => (
                            <span key={tag} className="text-xs px-1.5 py-0.5 rounded bg-blue-50 text-blue-700 font-medium">{tag}</span>
                          ))}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </WithCopy>
        )}
      </div>
    )
  }

  // ── Actual view ───────────────────────────────────────────────────────────────
  return (
    <div className="space-y-4">
      {ViewToggle}
      <div>
        <label className="text-xs text-slate-500 block mb-1"><Tooltip text="Time window for aggregating dividend and interest income. Custom lets you pick any date range.">Period:</Tooltip></label>
        <div className="flex flex-wrap gap-1.5">
          {DIV_PERIODS.map(p => (
            <button key={p} onClick={() => setPeriod(p)}
              className={`px-3 py-1.5 text-xs rounded border font-medium ${period === p ? 'bg-blue-600 text-white border-blue-600' : 'border-slate-300 text-slate-600 hover:bg-slate-50'}`}>
              {p}
            </button>
          ))}
        </div>
        {period === 'Custom' && (
          <div className="flex items-center gap-2 mt-2">
            <input type="date" className="rounded border border-slate-300 px-2 py-1 text-xs" value={customFrom} onChange={e => setCustomFrom(e.target.value)} />
            <span className="text-slate-400 text-xs">to</span>
            <input type="date" className="rounded border border-slate-300 px-2 py-1 text-xs" value={customTo} onChange={e => setCustomTo(e.target.value)} />
          </div>
        )}
      </div>

      {isLoading ? <div className="flex justify-center py-12"><Spinner /></div> : !result || !result.monthly.length ? (
        <p className="text-slate-400 text-sm py-8 text-center">No dividend or interest income found for the selected period.</p>
      ) : (
        <>
          <h4 className="text-sm font-semibold text-slate-700">Income by Security — {result.period_label}</h4>
          <div className="grid grid-cols-4 gap-4">
            <div className="bg-slate-50 rounded-lg p-4 text-center"><p className="text-xs text-slate-500 mb-1"><Tooltip text="Total dividend and interest income received in the selected period, in EUR.">Total ({result.period_label})</Tooltip></p><p className="text-xl font-bold">{fmtEur(Number(result.summary.total_income_eur ?? 0))}</p></div>
            <div className="bg-slate-50 rounded-lg p-4 text-center"><p className="text-xs text-slate-500 mb-1"><Tooltip text="Total income for the selected period divided by the number of months it spans.">Monthly Average</Tooltip></p><p className="text-xl font-bold">{fmtEur(Number(result.summary.avg_monthly_income_eur ?? 0))}</p></div>
            <div className="bg-slate-50 rounded-lg p-4 text-center"><p className="text-xs text-slate-500 mb-1"><Tooltip text="Number of distinct securities that paid dividends or interest in the selected period.">Securities paying</Tooltip></p><p className="text-xl font-bold">{Number(result.summary.securities_paying ?? 0)}</p></div>
            <div className="bg-slate-50 rounded-lg p-4 text-center"><p className="text-xs text-slate-500 mb-1"><Tooltip text="Average annualised Yield on Cost across all paying securities — income received divided by your cost basis, scaled to a yearly rate.">Avg Ann. YOC</Tooltip></p><p className="text-xl font-bold">{result.summary.avg_yoc_pct != null ? `${Number(result.summary.avg_yoc_pct).toFixed(2)}%` : 'N/A'}</p></div>
          </div>

          <Plot
            data={[{
              x: result.monthly.map(m => m.month),
              y: result.monthly.map(m => m.income_eur),
              type: 'bar', marker: { color: '#2ecc71' },
              customdata: result.monthly.map(m => monthlyBreakdown[String(m.month)] ?? '(no detail)'),
              hovertemplate: '<b>%{x|%b %Y}</b> — Total €%{y:,.2f}<br>%{customdata}<extra></extra>',
            }]}
            layout={{
              title: `Monthly Dividend & Interest Income (€) — ${result.period_label}`,
              height: 320, margin: { t: 50, r: 20, b: 40, l: 60 },
              yaxis: { title: 'Income (€)' },
              xaxis: { tickformat: '%b %Y', dtick: 'M1', type: 'date' as const },
              ...plotLayout(isDark),
            }}
            config={{ displayModeBar: false }} style={{ width: '100%' }}
          />

          <WithCopy>
            <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-300px)]">
              <table className="w-full text-sm">
                <thead className="sticky top-0 z-10"><tr className="bg-slate-50 text-xs text-slate-500 uppercase tracking-wide">
                  <th className="w-6 px-1 py-2 border-b border-slate-200"></th>
                  <ColHeader label="Security" sortKey="securities_name" currentKey={divSK} currentDir={divSD} onSort={divSort} tooltip="Security name." />
                  <ColHeader label="Type" sortKey="securities_type" currentKey={divSK} currentDir={divSD} onSort={divSort} tooltip="Asset type — Stock, ETF, Bond, etc." />
                  <ColHeader label={`Income (${result.period_label})`} sortKey="period_income_eur" currentKey={divSK} currentDir={divSD} onSort={divSort} align="right" tooltip="Total dividends and interest received from this security in the selected period." />
                  <ColHeader label="Cost Basis (€)" sortKey="cost_basis_eur" currentKey={divSK} currentDir={divSD} onSort={divSort} align="right" tooltip="Your total cost to acquire current holdings (purchase price × quantity)." />
                  <ColHeader label="Ann. YOC %" sortKey="yoc_pct" currentKey={divSK} currentDir={divSD} onSort={divSort} align="right" tooltip="Annualised Yield on Cost: period income scaled to a yearly rate, divided by your cost basis." />
                  <ColHeader label="Fwd. Yield %" sortKey="fwd_yield_pct" currentKey={divSK} currentDir={divSD} onSort={divSort} align="right" tooltip="Forward dividend yield based on the most recently declared dividend and the current market price." />
                  <ColHeader label="Ex-Div Date" sortKey="ex_div_date" currentKey={divSK} currentDir={divSD} onSort={divSort} align="right" tooltip="Last known ex-dividend date. You must hold the security before this date to qualify for the dividend." />
                  <ColHeader label="Frequency" sortKey="div_frequency" currentKey={divSK} currentDir={divSD} onSort={divSort} tooltip="How often dividends are paid — monthly, quarterly, semi-annually, or annually." />
                </tr></thead>
                <tbody className="divide-y divide-slate-100">
                  {divSorted.map((r, i) => {
                    const secId = Number(r.securities_id)
                    const txns = detailBySecId.get(secId) ?? []
                    const isOpen = expandedSec.has(secId)
                    return (
                      <React.Fragment key={i}>
                        <tr className="hover:bg-slate-50 cursor-pointer" onClick={() => toggleSecExpand(secId)}>
                          <td className="px-1 py-2 text-center text-slate-400">{txns.length > 0 ? (isOpen ? '▾' : '▸') : ''}</td>
                          <td className="px-3 py-2 font-medium"><SecLink id={r.securities_id}>{String(r.securities_name)}</SecLink></td>
                          <td className="px-3 py-2 text-slate-500">{String(r.securities_type)}</td>
                          <td className="px-3 py-2 text-right tabular-nums font-semibold">{fmtEur(Number(r.period_income_eur ?? 0))}</td>
                          <td className="px-3 py-2 text-right tabular-nums">{fmtEur(Number(r.cost_basis_eur ?? 0))}</td>
                          <td className="px-3 py-2 text-right tabular-nums">{r.yoc_pct != null ? `${Number(r.yoc_pct).toFixed(2)}%` : '—'}</td>
                          <td className="px-3 py-2 text-right tabular-nums text-slate-500">{r.fwd_yield_pct != null ? `${Number(r.fwd_yield_pct).toFixed(2)}%` : '—'}</td>
                          <td className="px-3 py-2 text-right text-slate-500">{r.ex_div_date ? String(r.ex_div_date).slice(0, 10) : '—'}</td>
                          <td className="px-3 py-2 text-slate-500">{r.div_frequency != null ? String(r.div_frequency) : '—'}</td>
                        </tr>
                        {isOpen && txns.length > 0 && (
                          <tr>
                            <td></td>
                            <td colSpan={8} className="px-3 pb-3">
                              <table className="w-full text-xs border-collapse">
                                <thead><tr className="bg-slate-100 text-slate-500 uppercase tracking-wide">
                                  <th className="text-left px-2 py-1 border-b border-slate-200">Date</th>
                                  <th className="text-left px-2 py-1 border-b border-slate-200">Account</th>
                                  <th className="text-left px-2 py-1 border-b border-slate-200">Type</th>
                                  <th className="text-right px-2 py-1 border-b border-slate-200">Income (€)</th>
                                </tr></thead>
                                <tbody>
                                  {txns.map((t, j) => (
                                    <tr key={j} className="border-b border-slate-100">
                                      <td className="px-2 py-1 text-slate-500">{String(t.date ?? '').slice(0, 10)}</td>
                                      <td className="px-2 py-1 text-blue-700"><AccountLink id={t.accounts_id as number} name={String(t.accounts_name ?? '')} type="Brokerage" /></td>
                                      <td className="px-2 py-1 text-slate-500">{String(t.action ?? '')}</td>
                                      <td className={`px-2 py-1 text-right tabular-nums font-medium ${Number(t.income_eur) < 0 ? 'text-red-600' : 'text-green-700'}`}>{fmtEur(Number(t.income_eur ?? 0))}</td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </td>
                          </tr>
                        )}
                      </React.Fragment>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </WithCopy>

          {result.by_type.length > 0 && (
            <Plot
              data={[{
                type: 'pie', hole: 0.35,
                labels: result.by_type.map(t => t.securities_type),
                values: result.by_type.map(t => t.period_income_eur),
                marker: { colors: PIE_COLORS },
                textinfo: 'percent+label',
                hovertemplate: '<b>%{label}</b><br>€ %{value:,.2f}<br>%{percent}<extra></extra>',
              }]}
              layout={{ title: `Income Allocation by Security Type — ${result.period_label}`, height: 380, margin: { t: 50, l: 20, r: 20, b: 20 }, ...plotLayout(isDark) }}
              config={{ displayModeBar: false }} style={{ width: '100%' }}
            />
          )}

          <div className="border border-slate-200 rounded-lg overflow-hidden">
            <button onClick={() => setDetailOpen(!detailOpen)}
              className="w-full flex items-center gap-2 px-4 py-2 text-sm font-medium text-slate-700 bg-slate-50 hover:bg-slate-100 text-left">
              <span className="text-xs">{detailOpen ? '▼' : '▶'}</span>
              <span>Full transaction detail</span>
            </button>
            {detailOpen && (
              <div className="p-3">
                <WithCopy>
                  <div className="overflow-x-auto overflow-y-auto max-h-96">
                    <table className="w-full text-sm">
                      <thead className="sticky top-0 z-10"><tr className="bg-slate-50 text-xs text-slate-500 uppercase tracking-wide">
                        <th className="px-3 py-2 text-left">Month</th>
                        <th className="px-3 py-2 text-left">Security</th>
                        <th className="px-3 py-2 text-left">Account</th>
                        <th className="px-3 py-2 text-left">Action</th>
                        <th className="px-3 py-2 text-right">Income (€)</th>
                      </tr></thead>
                      <tbody className="divide-y divide-slate-100">
                        {result.detail.map((r, i) => (
                          <tr key={i} className="hover:bg-slate-50">
                            <td className="px-3 py-2 text-slate-500">{String(r.month).slice(0, 10)}</td>
                            <td className="px-3 py-2 font-medium"><SecLink id={r.securities_id}>{String(r.securities_name)}</SecLink></td>
                            <td className="px-3 py-2 text-blue-700"><AccountLink id={r.accounts_id as number} name={String(r.accounts_name)} type="Brokerage" /></td>
                            <td className="px-3 py-2 text-slate-500">{String(r.action)}</td>
                            <td className="px-3 py-2 text-right tabular-nums">{fmtEur(Number(r.income_eur ?? 0))}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </WithCopy>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}

const PERF_PERIOD_MAP: Record<string, [string, string, string | null]> = {
  'Daily':    ['pnl_dtd_eur',          'pnl_dtd_pct',              null],
  'WTD':      ['pnl_wtd_eur',          'pnl_wtd_pct',              null],
  'MTD':      ['pnl_mtd_eur',          'pnl_mtd_pct',              null],
  'QTD':      ['pnl_qtd_eur',          'pnl_qtd_pct',              null],
  'YTD':      ['pnl_ytd_eur',          'pnl_ytd_percent',          null],
  '1Y':       ['pnl_1y_eur',           'pnl_1y_percent',           null],
  '3Y':       ['pnl_3y_eur',           'pnl_3y_percent',           null],
  '5Y':       ['pnl_5y_eur',           'pnl_5y_percent',           null],
  'All-Time': ['pnl_net_all_time_eur', 'pnl_net_all_time_percent', 'gross_invested_all_time_eur'],
}

function PerformanceTab() {
  const { isDark } = useTheme()
  const liveRefetchMs = useLiveRefetchInterval()
  const { data = [], isLoading } = useQuery({ queryKey: ['pnl-all'], queryFn: () => getPnl(), refetchInterval: liveRefetchMs })
  const [period, setPeriod] = usePersist('perf_period', 'Daily')
  const [viewPct, setViewPct] = usePersist('perf_view_pct', false)
  const [topN, setTopN] = usePersist('perf_top_n', 15)
  const [rankedOpen, setRankedOpen] = useState(false)

  const ltYears = LONG_TERM_YEARS[period] ?? null
  const { map: ltMap, isLoading: ltLoading } = useLongTermPnl(ltYears)
  const rows = useMemo(
    () => mergeLongTermPnl(data as Row[], ltYears ? PERF_PERIOD_MAP[period][0] : null, null, ltMap),
    [data, period, ltYears, ltMap],
  )

  const bySec = useMemo(() => {
    const agg: Record<string, Record<string, number>> = {}
    const secId: Record<string, unknown> = {}
    const sumCols = ['current_value_eur', 'gross_invested_all_time_eur', 'pnl_net_all_time_eur',
      'unrealized_pnl_eur', 'realized_pnl_eur', 'pnl_dtd_eur', 'pnl_ytd_eur', 'pnl_qtd_eur', 'pnl_mtd_eur', 'pnl_wtd_eur',
      'pnl_1y_eur', 'pnl_3y_eur', 'pnl_5y_eur']
    for (const r of rows) {
      const name = String(r.securities_name)
      if (!agg[name]) agg[name] = {}
      if (secId[name] == null) secId[name] = r.securities_id
      for (const c of sumCols) agg[name][c] = (agg[name][c] ?? 0) + Number(r[c] ?? 0)
    }
    const list: (Record<string, number> & { securities_name: string; securities_id: unknown })[] =
      Object.entries(agg).map(([name, vals]) => ({ ...vals, securities_name: name, securities_id: secId[name] }) as Record<string, number> & { securities_name: string; securities_id: unknown })
    for (const v of list) {
      const inv = v.gross_invested_all_time_eur
      if (inv) {
        // % of total capital ever invested in this security — not "vs. yesterday's
        // value", which breaks the moment a position is fully closed within the
        // period (current value drops to 0, making that denominator meaningless
        // and the result always exactly -100% regardless of the real P&L).
        v.pnl_dtd_pct = v.pnl_dtd_eur / inv * 100
        v.pnl_net_all_time_percent = v.pnl_net_all_time_eur / inv * 100
        v.pnl_ytd_percent = v.pnl_ytd_eur / inv * 100
        v.pnl_wtd_pct = v.pnl_wtd_eur / inv * 100
        v.pnl_mtd_pct = v.pnl_mtd_eur / inv * 100
        v.pnl_qtd_pct = v.pnl_qtd_eur / inv * 100
        v.pnl_1y_percent = v.pnl_1y_eur / inv * 100
        v.pnl_3y_percent = v.pnl_3y_eur / inv * 100
        v.pnl_5y_percent = v.pnl_5y_eur / inv * 100
      } else {
        v.pnl_dtd_pct = NaN
        v.pnl_net_all_time_percent = NaN; v.pnl_ytd_percent = NaN
        v.pnl_wtd_pct = NaN; v.pnl_mtd_pct = NaN; v.pnl_qtd_pct = NaN
        v.pnl_1y_percent = NaN; v.pnl_3y_percent = NaN; v.pnl_5y_percent = NaN
      }
    }
    return list
  }, [rows])

  const [eurCol, pctCol, invCol] = PERF_PERIOD_MAP[period]
  const primary = viewPct ? pctCol : eurCol

  const valid = bySec.filter(v => !isNaN(v[eurCol]))
  const sortable = valid.filter(v => !isNaN(v[primary]))
  const top = [...sortable].sort((a, b) => b[primary] - a[primary]).slice(0, topN)
  const bottom = [...sortable].sort((a, b) => a[primary] - b[primary]).slice(0, topN)

  const totalPnl = valid.reduce((s, v) => s + (v[eurCol] ?? 0), 0)
  const winners = valid.filter(v => v[eurCol] > 0).length
  const losers = valid.filter(v => v[eurCol] < 0).length

  const chartMap = new Map<string, Record<string, number>>()
  for (const v of [...top, ...bottom]) chartMap.set(v.securities_name as unknown as string, v)
  const chartRows = [...chartMap.values()].sort((a, b) => a[primary] - b[primary])

  const allRanked = [...sortable].sort((a, b) => b[primary] - a[primary])

  if (isLoading || ltLoading) return <div className="flex justify-center py-12"><Spinner /></div>

  const PerfRow = ({ v, rank }: { v: Record<string, unknown>; rank?: number }) => (
    <tr className="hover:bg-slate-50">
      {rank != null && <td className="px-3 py-2 text-slate-400">{rank}</td>}
      <td className="px-3 py-2 font-medium text-blue-700 whitespace-nowrap"><SecLink id={v.securities_id}>{String(v.securities_name)}</SecLink></td>
      {viewPct ? (
        <>
          <td className={`px-3 py-2 text-right tabular-nums font-semibold whitespace-nowrap ${Number(v[pctCol] ?? 0) >= 0 ? 'text-green-700' : 'text-red-600'}`}>{Number(v[pctCol] ?? 0) >= 0 ? '+' : ''}{Number(v[pctCol] ?? 0).toFixed(2)}%</td>
          <td className={`px-3 py-2 text-right tabular-nums whitespace-nowrap ${Number(v[eurCol] ?? 0) >= 0 ? 'text-green-700' : 'text-red-600'}`}>{fmtDelta(Number(v[eurCol] ?? 0))}</td>
        </>
      ) : (
        <>
          <td className={`px-3 py-2 text-right tabular-nums font-semibold whitespace-nowrap ${Number(v[eurCol] ?? 0) >= 0 ? 'text-green-700' : 'text-red-600'}`}>{fmtDelta(Number(v[eurCol] ?? 0))}</td>
          {pctCol && !isNaN(Number(v[pctCol])) && <td className={`px-3 py-2 text-right tabular-nums whitespace-nowrap ${Number(v[pctCol] ?? 0) >= 0 ? 'text-green-700' : 'text-red-600'}`}>{Number(v[pctCol] ?? 0) >= 0 ? '+' : ''}{Number(v[pctCol] ?? 0).toFixed(2)}%</td>}
        </>
      )}
      {invCol && <td className="px-3 py-2 text-right tabular-nums text-slate-500 whitespace-nowrap">{fmtEur(Number(v[invCol] ?? 0))}</td>}
    </tr>
  )

  const fmtDelta = (v: number) => `${v >= 0 ? '+' : ''}${fmtEur(v)}`

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <label className="text-xs text-slate-500 block mb-1">
            <Tooltip text="Which time window to measure P&L over. Daily = today vs yesterday's close; WTD/MTD/QTD/YTD = since the start of the current week/month/quarter/year; All-Time = since first purchase.">Period</Tooltip>
          </label>
          <div className="flex rounded border border-slate-300 overflow-hidden text-xs">
            {([
              ['Daily',    'Change since yesterday\'s close'],
              ['WTD',      'Week-to-date: since Monday\'s open'],
              ['MTD',      'Month-to-date: since 1st of this month'],
              ['QTD',      'Quarter-to-date: since start of this quarter'],
              ['YTD',      'Year-to-date: since 1 Jan'],
              ['1Y',       'Since 1 year ago today'],
              ['3Y',       'Since 3 years ago today'],
              ['5Y',       'Since 5 years ago today'],
              ['All-Time', 'Total P&L since the first recorded purchase'],
            ] as const).map(([p, tip]) => (
              <button key={p} title={tip} onClick={() => setPeriod(p)}
                className={`px-3 py-1.5 font-medium ${period === p ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 hover:bg-slate-50'}`}>
                {p}
              </button>
            ))}
          </div>
        </div>
        <div>
          <label className="text-xs text-slate-500 block mb-1">
            <Tooltip text="Sort and display P&L as an absolute euro change, or as a percentage of invested capital for the selected period.">View by</Tooltip>
          </label>
          <div className="flex rounded border border-slate-300 overflow-hidden text-xs">
            <button onClick={() => setViewPct(false)} className={`px-3 py-1.5 font-medium ${!viewPct ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 hover:bg-slate-50'}`}>€ Change</button>
            <button onClick={() => setViewPct(true)} className={`px-3 py-1.5 font-medium ${viewPct ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 hover:bg-slate-50'}`}>% Change</button>
          </div>
        </div>
        <div>
          <label className="text-xs text-slate-500 block mb-1">
            <Tooltip text="How many securities to show in the Top Gainers and Top Losers lists and the bar chart.">Top N</Tooltip>
          </label>
          <div className="flex items-center gap-1">
            <button onClick={() => setTopN(Math.max(3, topN - 1))} className="px-2 py-1 text-xs rounded border border-slate-300 hover:bg-slate-50">−</button>
            <span className="w-10 text-center text-sm tabular-nums">{topN}</span>
            <button onClick={() => setTopN(Math.min(50, topN + 1))} className="px-2 py-1 text-xs rounded border border-slate-300 hover:bg-slate-50">+</button>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-4 gap-4">
        <div className="bg-slate-50 rounded-lg p-4 text-center">
          <p className="text-xs text-slate-500 mb-1"><Tooltip text="Number of distinct securities with a P&L value for the selected period.">Securities</Tooltip></p>
          <p className="text-xl font-bold">{valid.length}</p>
        </div>
        <div className="bg-slate-50 rounded-lg p-4 text-center">
          <p className="text-xs text-slate-500 mb-1"><Tooltip text={`Sum of P&L across all securities for the ${period} period in euros.`}>Total P&L ({period})</Tooltip></p>
          <p className={`text-xl font-bold ${totalPnl >= 0 ? 'text-green-700' : 'text-red-600'}`}>{fmtDelta(totalPnl)}</p>
        </div>
        <div className="bg-slate-50 rounded-lg p-4 text-center">
          <p className="text-xs text-slate-500 mb-1"><Tooltip text={`Securities with a positive P&L for the ${period} period.`}>Winners</Tooltip></p>
          <p className="text-xl font-bold text-green-700">{winners}</p>
        </div>
        <div className="bg-slate-50 rounded-lg p-4 text-center">
          <p className="text-xs text-slate-500 mb-1"><Tooltip text={`Securities with a negative P&L for the ${period} period.`}>Losers</Tooltip></p>
          <p className="text-xl font-bold text-red-600">{losers}</p>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <div className="bg-green-50 border border-green-100 rounded-lg px-4 py-2 mb-2 text-sm font-medium text-green-700">📈 Top {topN} Gainers</div>
          <WithCopy>
            <div className="overflow-x-auto overflow-y-auto max-h-96">
              <table className="w-full text-sm">
                <thead className="sticky top-0 z-10"><tr className="bg-slate-50 text-xs text-slate-500 uppercase tracking-wide">
                  <th className="px-3 py-2 text-left whitespace-nowrap"><Tooltip text="Security name as recorded in your holdings.">Security</Tooltip></th>
                  {viewPct ? <>
                    <th className="px-3 py-2 text-right whitespace-nowrap"><Tooltip text="Percentage change in value over the selected period, relative to invested capital.">Change %</Tooltip></th>
                    <th className="px-3 py-2 text-right whitespace-nowrap"><Tooltip text="Absolute profit or loss in euros over the selected period.">P&L (€)</Tooltip></th>
                  </> : <>
                    <th className="px-3 py-2 text-right whitespace-nowrap"><Tooltip text="Absolute profit or loss in euros over the selected period.">P&L (€)</Tooltip></th>
                    {pctCol && <th className="px-3 py-2 text-right whitespace-nowrap"><Tooltip text="Percentage change relative to invested capital.">Change %</Tooltip></th>}
                  </>}
                  {invCol && <th className="px-3 py-2 text-right whitespace-nowrap"><Tooltip text="Total capital invested in this security (gross cost basis, excluding fees).">Invested (€)</Tooltip></th>}
                </tr></thead>
                <tbody className="divide-y divide-slate-100">{top.map((v, i) => <PerfRow key={i} v={v} />)}</tbody>
              </table>
            </div>
          </WithCopy>
        </div>
        <div>
          <div className="bg-red-50 border border-red-100 rounded-lg px-4 py-2 mb-2 text-sm font-medium text-red-600">📉 Top {topN} Losers</div>
          <WithCopy>
            <div className="overflow-x-auto overflow-y-auto max-h-96">
              <table className="w-full text-sm">
                <thead className="sticky top-0 z-10"><tr className="bg-slate-50 text-xs text-slate-500 uppercase tracking-wide">
                  <th className="px-3 py-2 text-left whitespace-nowrap"><Tooltip text="Security name as recorded in your holdings.">Security</Tooltip></th>
                  {viewPct ? <>
                    <th className="px-3 py-2 text-right whitespace-nowrap"><Tooltip text="Percentage change in value over the selected period, relative to invested capital.">Change %</Tooltip></th>
                    <th className="px-3 py-2 text-right whitespace-nowrap"><Tooltip text="Absolute profit or loss in euros over the selected period.">P&L (€)</Tooltip></th>
                  </> : <>
                    <th className="px-3 py-2 text-right whitespace-nowrap"><Tooltip text="Absolute profit or loss in euros over the selected period.">P&L (€)</Tooltip></th>
                    {pctCol && <th className="px-3 py-2 text-right whitespace-nowrap"><Tooltip text="Percentage change relative to invested capital.">Change %</Tooltip></th>}
                  </>}
                  {invCol && <th className="px-3 py-2 text-right whitespace-nowrap"><Tooltip text="Total capital invested in this security (gross cost basis, excluding fees).">Invested (€)</Tooltip></th>}
                </tr></thead>
                <tbody className="divide-y divide-slate-100">{bottom.map((v, i) => <PerfRow key={i} v={v} />)}</tbody>
              </table>
            </div>
          </WithCopy>
        </div>
      </div>

      {chartRows.length > 0 && (
        <Plot
          data={[{
            type: 'bar', orientation: 'h',
            x: chartRows.map(v => v[primary]),
            y: chartRows.map(v => v.securities_name),
            marker: { color: chartRows.map(v => v[primary] >= 0 ? '#2ecc71' : '#e74c3c') },
          }]}
          layout={{
            title: `Top & Least Performers — ${period} (${viewPct ? '% Change' : '€ Change'})`,
            height: Math.max(320, chartRows.length * 28),
            margin: { t: 40, l: 10, r: 40, b: 40 },
            yaxis: { automargin: true },
            xaxis: { title: viewPct ? 'Change %' : `P&L (€) — ${period}`, ticksuffix: viewPct ? '%' : '' },
            ...plotLayout(isDark),
          }}
          config={{ displayModeBar: false }} style={{ width: '100%' }}
        />
      )}

      <div className="border border-slate-200 rounded-lg overflow-hidden">
        <button onClick={() => setRankedOpen(!rankedOpen)}
          className="w-full flex items-center gap-2 px-4 py-2 text-sm font-medium text-slate-700 bg-slate-50 hover:bg-slate-100 text-left">
          <span className="text-xs">{rankedOpen ? '▼' : '▶'}</span>
          <span>📋 All Securities Ranked</span>
        </button>
        {rankedOpen && (
          <div className="p-3">
            <WithCopy>
              <div className="overflow-x-auto overflow-y-auto max-h-96">
                <table className="w-full text-sm">
                  <thead className="sticky top-0 z-10"><tr className="bg-slate-50 text-xs text-slate-500 uppercase tracking-wide">
                    <th className="px-3 py-2 text-left w-12 whitespace-nowrap"><Tooltip text="Performance rank for the selected period — 1 = best performer.">Rank</Tooltip></th>
                    <th className="px-3 py-2 text-left whitespace-nowrap"><Tooltip text="Security name as recorded in your holdings.">Security</Tooltip></th>
                    {viewPct ? <>
                      <th className="px-3 py-2 text-right whitespace-nowrap"><Tooltip text="Percentage change in value over the selected period, relative to invested capital.">Change %</Tooltip></th>
                      <th className="px-3 py-2 text-right whitespace-nowrap"><Tooltip text="Absolute profit or loss in euros over the selected period.">P&L (€)</Tooltip></th>
                    </> : <>
                      <th className="px-3 py-2 text-right whitespace-nowrap"><Tooltip text="Absolute profit or loss in euros over the selected period.">P&L (€)</Tooltip></th>
                      {pctCol && <th className="px-3 py-2 text-right whitespace-nowrap"><Tooltip text="Percentage change relative to invested capital.">Change %</Tooltip></th>}
                    </>}
                    {invCol && <th className="px-3 py-2 text-right whitespace-nowrap"><Tooltip text="Total capital invested in this security (gross cost basis, excluding fees).">Invested (€)</Tooltip></th>}
                  </tr></thead>
                  <tbody className="divide-y divide-slate-100">
                    {allRanked.map((v, i) => <PerfRow key={i} v={v} rank={i + 1} />)}
                  </tbody>
                </table>
              </div>
            </WithCopy>
          </div>
        )}
      </div>
    </div>
  )
}

function SavingsAccountsTab() {
  const { isDark } = useTheme()
  const liveRefetchMs = useLiveRefetchInterval()
  const [savView, setSavView] = usePersist<'actual' | 'forecast' | 'recommendations'>('sav_view', 'actual')

  // ── Actual state ─────────────────────────────────────────────────────────────
  const [period, setPeriod] = usePersist('sav_period', 'All Time')
  const [customFrom, setCustomFrom] = usePersist('sav_from', new Date(Date.now() - 365 * 86400000).toISOString().slice(0, 10))
  const [customTo, setCustomTo] = usePersist('sav_to', new Date().toISOString().slice(0, 10))

  const { data, isLoading } = useQuery({
    queryKey: ['savings-accounts', period, period === 'Custom' ? customFrom : null, period === 'Custom' ? customTo : null],
    queryFn: () => getSavingsAccounts(period, period === 'Custom' ? customFrom : undefined, period === 'Custom' ? customTo : undefined),
    refetchInterval: liveRefetchMs,
  })

  // ── Forecast state ────────────────────────────────────────────────────────────
  const [fcPeriod, setFcPeriod] = usePersist<'eoy' | '6m' | '12m'>('sav_forecast_period', '12m')
  const { data: fcData, isLoading: fcLoading } = useQuery({
    queryKey: ['savings-forecast', fcPeriod],
    queryFn: () => getSavingsForecast(fcPeriod),
    enabled: savView === 'forecast',
  })

  // ── Recommendations state ─────────────────────────────────────────────────────
  const { data: recData, isLoading: recLoading } = useQuery({
    queryKey: ['savings-recommendations'],
    queryFn: getSavingsRecommendations,
    enabled: savView === 'recommendations',
  })

  type ActualResult = {
    period_label: string
    summary: Row; detail: Row[]; detail_last: Row[]; monthly: { month: string; income_eur: number }[]
  }
  type ForecastResult = {
    summary: { total_period_eur: number; total_annual_eur: number; total_monthly_eur: number; accounts_count: number; portfolio_apy_pct: number }
    monthly_forecast: { month: string; income_eur: number }[]
    by_account: Row[]
  }
  type RecResult = { ranking: Row[]; idle_opportunities: Row[]; total_potential_gain_eur: number }

  const result    = data    as ActualResult   | undefined
  const fcResult  = fcData  as ForecastResult | undefined
  const recResult = recData as RecResult      | undefined

  const { sorted: detailSorted, sortKey: detailSK, sortDir: detailSD, toggleSort: detailSort } =
    useSortTablePersisted(result?.detail ?? [], 'savings-actual-sort', 'total_interest_eur', 'desc')
  const { sorted: fcSorted, sortKey: fcSK, sortDir: fcSD, toggleSort: fcSort } =
    useSortTablePersisted(fcResult?.by_account ?? [], 'savings-forecast-sort', 'period_forecast_eur', 'desc')
  const { sorted: rankSorted, sortKey: rankSK, sortDir: rankSD, toggleSort: rankSort } =
    useSortTablePersisted(recResult?.ranking ?? [], 'savings-ranking-sort', 'apy_pct', 'desc')
  const { sorted: idleSorted, sortKey: idleSK, sortDir: idleSD, toggleSort: idleSort } =
    useSortTablePersisted(recResult?.idle_opportunities ?? [], 'savings-idle-sort', 'potential_annual_gain_eur', 'desc')

  const pct = (v: unknown) => v != null ? `${Number(v).toFixed(2)}%` : '—'
  const days = (v: unknown) => v != null ? String(Math.round(Number(v))) : '—'
  const dateStr = (v: unknown) => v ? String(v).slice(0, 10) : '—'

  // ── View toggle ───────────────────────────────────────────────────────────────
  const VIEW_LABELS: Record<string, string> = { actual: '📋 Actual', forecast: '🔮 Forecast', recommendations: '💡 Recommendations' }
  const ViewToggle = (
    <div className="flex gap-1 mb-4">
      {(['actual', 'forecast', 'recommendations'] as const).map(v => (
        <button key={v} onClick={() => setSavView(v)}
          className={`px-4 py-1.5 text-xs rounded-full font-medium border transition-colors ${savView === v ? 'bg-blue-600 text-white border-blue-600' : 'border-slate-300 text-slate-600 hover:bg-slate-50'}`}>
          {VIEW_LABELS[v]}
        </button>
      ))}
    </div>
  )

  // ── Forecast view ─────────────────────────────────────────────────────────────
  const FC_PERIOD_LABELS: Record<'eoy' | '6m' | '12m', string> = { eoy: 'Till EOY', '6m': 'Next 6 Months', '12m': 'Next 12 Months' }
  const FC_PERIOD_SHORT:  Record<'eoy' | '6m' | '12m', string> = { eoy: 'EOY', '6m': '6mo', '12m': '12mo' }
  if (savView === 'forecast') {
    return (
      <div className="space-y-4">
        {ViewToggle}
        <div className="flex gap-1.5">
          {(['eoy', '6m', '12m'] as const).map(p => (
            <button key={p} onClick={() => setFcPeriod(p)}
              className={`px-3 py-1.5 text-xs rounded border font-medium ${fcPeriod === p ? 'bg-blue-600 text-white border-blue-600' : 'border-slate-300 text-slate-600 hover:bg-slate-50'}`}>
              {FC_PERIOD_LABELS[p]}
            </button>
          ))}
        </div>
        {fcLoading ? <div className="flex justify-center py-12"><Spinner /></div>
          : !fcResult || !fcResult.by_account?.length ? (
            <p className="text-slate-400 text-sm py-8 text-center">No forecast data for this period — no savings account has a last real interest period to project from yet.</p>
          ) : (
          <>
            <div className="grid grid-cols-4 gap-4">
              <div className="bg-slate-50 rounded-lg p-4 text-center">
                <p className="text-xs text-slate-500 mb-1"><Tooltip text={`Total projected interest ${fcPeriod === 'eoy' ? 'between now and the end of this year' : `over the ${fcPeriod === '6m' ? 'next 6 months' : 'next 12 months'}`}, compounding each account's current balance forward at its last real interest period's APY%.`}>Projected ({FC_PERIOD_SHORT[fcPeriod]})</Tooltip></p>
                <p className="text-xl font-bold text-green-600">{fmtEur(fcResult.summary.total_period_eur)}</p>
              </div>
              <div className="bg-slate-50 rounded-lg p-4 text-center">
                <p className="text-xs text-slate-500 mb-1"><Tooltip text="Projected period total divided by the number of months in the period.">Monthly Average</Tooltip></p>
                <p className="text-xl font-bold">{fmtEur(fcResult.summary.total_monthly_eur)}</p>
              </div>
              <div className="bg-slate-50 rounded-lg p-4 text-center">
                <p className="text-xs text-slate-500 mb-1"><Tooltip text="Number of savings accounts with a projected payment within this period.">Accounts</Tooltip></p>
                <p className="text-xl font-bold">{fcResult.summary.accounts_count}</p>
              </div>
              <div className="bg-slate-50 rounded-lg p-4 text-center">
                <p className="text-xs text-slate-500 mb-1"><Tooltip text="Balance-weighted average of each account's last real interest period APY%, regardless of the period selected above.">Portfolio APY</Tooltip></p>
                <p className="text-xl font-bold">{fcResult.summary.portfolio_apy_pct.toFixed(2)}%</p>
              </div>
            </div>

            {fcResult.monthly_forecast.length > 0 && (
              <Plot
                data={[{ x: fcResult.monthly_forecast.map(m => m.month), y: fcResult.monthly_forecast.map(m => m.income_eur), type: 'bar', marker: { color: '#3b82f6' }, name: 'Projected' }]}
                layout={{
                  title: `Projected Monthly Interest Income (€) — ${FC_PERIOD_LABELS[fcPeriod]}`,
                  height: 320, margin: { t: 50, r: 20, b: 40, l: 60 },
                  yaxis: { title: 'Projected Income (€)' },
                  xaxis: { tickformat: '%b %Y', dtick: 'M1', type: 'date' as const },
                  ...plotLayout(isDark),
                }}
                config={{ displayModeBar: false }} style={{ width: '100%' }}
              />
            )}

            <WithCopy>
              <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-300px)]">
                <table className="w-full text-sm">
                  <thead className="sticky top-0 z-10"><tr className="bg-slate-50 text-xs text-slate-500 uppercase tracking-wide">
                    <ColHeader label="Account" sortKey="accounts_name" currentKey={fcSK} currentDir={fcSD} onSort={fcSort} tooltip="Savings account name." />
                    <ColHeader label="Curr" sortKey="currency" currentKey={fcSK} currentDir={fcSD} onSort={fcSort} tooltip="Account currency." />
                    <ColHeader label="Current Balance" sortKey="current_balance" currentKey={fcSK} currentDir={fcSD} onSort={fcSort} align="right" tooltip="Current ledger balance — the principal this forecast compounds forward." />
                    <ColHeader label="APY %" sortKey="apy_pct" currentKey={fcSK} currentDir={fcSD} onSort={fcSort} align="right" tooltip="Compound annualised rate from the account's last real interest period — the rate this forecast assumes going forward." />
                    <ColHeader label="Cadence (days)" sortKey="cadence_days" currentKey={fcSK} currentDir={fcSD} onSort={fcSort} align="right" tooltip="Days between this account's last two interest payments — used as the assumed payment interval going forward." />
                    <ColHeader label={`Amt (${FC_PERIOD_SHORT[fcPeriod]})`} sortKey="period_forecast_eur" currentKey={fcSK} currentDir={fcSD} onSort={fcSort} align="right" tooltip="Sum of projected interest payments landing within the selected period, compounding on the running balance." />
                    <ColHeader label="Projected Balance" sortKey="projected_balance_eur" currentKey={fcSK} currentDir={fcSD} onSort={fcSort} align="right" tooltip="Current balance plus all projected interest payments through the end of the period." />
                    <ColHeader label="Next Payment" sortKey="next_payment_date" currentKey={fcSK} currentDir={fcSD} onSort={fcSort} align="right" tooltip="Projected date of the next interest payment, based on the account's historical cadence." />
                  </tr></thead>
                  <tbody className="divide-y divide-slate-100">
                    {fcSorted.map((r, i) => (
                      <tr key={i} className="hover:bg-slate-50">
                        <td className="px-3 py-2 font-medium"><AccountLink id={r.accounts_id as number} name={String(r.accounts_name)} type="Savings" /></td>
                        <td className="px-3 py-2 text-slate-500">{String(r.currency)}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{fmtNum(Number(r.current_balance), 2)}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{pct(r.apy_pct)}</td>
                        <td className="px-3 py-2 text-right tabular-nums text-slate-500">{days(r.cadence_days)}</td>
                        <td className="px-3 py-2 text-right tabular-nums font-semibold text-green-600">{fmtEur(Number(r.period_forecast_eur))}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{fmtNum(Number(r.projected_balance_eur), 2)}</td>
                        <td className="px-3 py-2 text-right text-slate-500 whitespace-nowrap">{dateStr(r.next_payment_date)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </WithCopy>
          </>
        )}
      </div>
    )
  }

  // ── Recommendations view ──────────────────────────────────────────────────────
  if (savView === 'recommendations') {
    return (
      <div className="space-y-4">
        {ViewToggle}
        <p className="text-xs text-slate-400">Ranks your own savings accounts and flags idle cash — there's no external market of savings accounts to recommend opening, only what you already have.</p>

        {recLoading ? <div className="flex justify-center py-12"><Spinner /></div>
          : !recResult || !recResult.ranking?.length ? (
            <p className="text-slate-400 text-sm py-8 text-center">No savings accounts with a last real interest period to rank yet.</p>
          ) : (
          <>
            <div>
              <h4 className="text-sm font-semibold text-slate-700 mb-2">Your Savings Accounts, Ranked by APY%</h4>
              <WithCopy>
                <div className="overflow-x-auto overflow-y-auto max-h-96">
                  <table className="w-full text-sm">
                    <thead className="sticky top-0 z-10"><tr className="bg-slate-50 text-xs text-slate-500 uppercase tracking-wide">
                      <ColHeader label="Account" sortKey="accounts_name" currentKey={rankSK} currentDir={rankSD} onSort={rankSort} tooltip="Savings account name." />
                      <ColHeader label="Curr" sortKey="currency" currentKey={rankSK} currentDir={rankSD} onSort={rankSort} tooltip="Account currency." />
                      <ColHeader label="Current Balance" sortKey="current_balance" currentKey={rankSK} currentDir={rankSD} onSort={rankSort} align="right" tooltip="Current ledger balance." />
                      <ColHeader label="APY %" sortKey="apy_pct" currentKey={rankSK} currentDir={rankSD} onSort={rankSort} align="right" tooltip="Compound annualised rate from the account's last real interest period." />
                      <ColHeader label="Ann. YOC %" sortKey="annual_yoc_pct" currentKey={rankSK} currentDir={rankSD} onSort={rankSort} align="right" tooltip="Simple annualised Yield on Cost from the same last real interest period." />
                      <ColHeader label="Last Interest" sortKey="last_interest_date" currentKey={rankSK} currentDir={rankSD} onSort={rankSort} align="right" tooltip="Date of the most recent interest payment this ranking is based on." />
                    </tr></thead>
                    <tbody className="divide-y divide-slate-100">
                      {rankSorted.map((r, i) => (
                        <tr key={i} className={`hover:bg-slate-50 ${i === 0 ? 'bg-green-50' : ''}`}>
                          <td className="px-3 py-2 font-medium">{i === 0 && '🏆 '}<AccountLink id={r.accounts_id as number} name={String(r.accounts_name)} type="Savings" /></td>
                          <td className="px-3 py-2 text-slate-500">{String(r.currency)}</td>
                          <td className="px-3 py-2 text-right tabular-nums">{fmtNum(Number(r.current_balance), 2)}</td>
                          <td className="px-3 py-2 text-right tabular-nums font-semibold">{pct(r.apy_pct)}</td>
                          <td className="px-3 py-2 text-right tabular-nums text-slate-500">{pct(r.annual_yoc_pct)}</td>
                          <td className="px-3 py-2 text-right text-slate-500 whitespace-nowrap">{dateStr(r.last_interest_date)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </WithCopy>
            </div>

            <div>
              <div className="flex items-center justify-between mb-2">
                <h4 className="text-sm font-semibold text-slate-700">Idle Cash Opportunities</h4>
                {recResult.idle_opportunities.length > 0 && (
                  <span className="text-xs text-slate-500">Total potential gain: <span className="font-semibold text-green-600">{fmtEur(recResult.total_potential_gain_eur)}/yr</span></span>
                )}
              </div>
              {recResult.idle_opportunities.length === 0 ? (
                <p className="text-slate-400 text-sm py-8 text-center">No material idle balances found in Cash/Checking accounts — nothing to redirect right now.</p>
              ) : (
                <WithCopy>
                  <div className="overflow-x-auto overflow-y-auto max-h-96">
                    <table className="w-full text-sm">
                      <thead className="sticky top-0 z-10"><tr className="bg-slate-50 text-xs text-slate-500 uppercase tracking-wide">
                        <ColHeader label="Idle In" sortKey="accounts_name" currentKey={idleSK} currentDir={idleSD} onSort={idleSort} tooltip="Cash/Checking account currently holding the idle balance." />
                        <ColHeader label="Type" sortKey="accounts_type" currentKey={idleSK} currentDir={idleSD} onSort={idleSort} tooltip="Account type." />
                        <ColHeader label="Curr" sortKey="currency" currentKey={idleSK} currentDir={idleSD} onSort={idleSort} tooltip="Account currency." />
                        <ColHeader label="Balance" sortKey="balance" currentKey={idleSK} currentDir={idleSD} onSort={idleSort} align="right" tooltip="Current balance sitting idle, earning no structured interest." />
                        <ColHeader label="Move To" sortKey="target_accounts_name" currentKey={idleSK} currentDir={idleSD} onSort={idleSort} tooltip="Your best-performing savings account in the same currency." />
                        <ColHeader label="Target APY %" sortKey="target_apy_pct" currentKey={idleSK} currentDir={idleSD} onSort={idleSort} align="right" tooltip="That account's last real interest period APY%." />
                        <ColHeader label="Potential Gain/yr" sortKey="potential_annual_gain_eur" currentKey={idleSK} currentDir={idleSD} onSort={idleSort} align="right" tooltip="Estimated additional annual interest if this balance earned the target account's APY% instead of sitting idle." />
                      </tr></thead>
                      <tbody className="divide-y divide-slate-100">
                        {idleSorted.map((r, i) => (
                          <tr key={i} className="hover:bg-slate-50">
                            <td className="px-3 py-2 font-medium"><AccountLink id={r.accounts_id as number} name={String(r.accounts_name)} type={String(r.accounts_type)} /></td>
                            <td className="px-3 py-2 text-slate-500">{String(r.accounts_type)}</td>
                            <td className="px-3 py-2 text-slate-500">{String(r.currency)}</td>
                            <td className="px-3 py-2 text-right tabular-nums">{fmtNum(Number(r.balance), 2)}</td>
                            <td className="px-3 py-2 text-blue-700">→ <AccountLink id={r.target_accounts_id as number} name={String(r.target_accounts_name)} type="Savings" /></td>
                            <td className="px-3 py-2 text-right tabular-nums">{pct(r.target_apy_pct)}</td>
                            <td className="px-3 py-2 text-right tabular-nums font-semibold text-green-600">{fmtEur(Number(r.potential_annual_gain_eur))}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </WithCopy>
              )}
            </div>
          </>
        )}
      </div>
    )
  }

  // ── Actual view ───────────────────────────────────────────────────────────────
  if (isLoading) return <div className="space-y-4">{ViewToggle}<div className="flex justify-center py-12"><Spinner /></div></div>

  return (
    <div className="space-y-4">
      {ViewToggle}
      <div>
        <label className="text-xs text-slate-500 block mb-1"><Tooltip text="Time window for aggregating savings interest income. Custom lets you pick any date range.">Period:</Tooltip></label>
        <div className="flex flex-wrap gap-1.5">
          {DIV_PERIODS.map(p => (
            <button key={p} onClick={() => setPeriod(p)}
              className={`px-3 py-1.5 text-xs rounded border font-medium ${period === p ? 'bg-blue-600 text-white border-blue-600' : 'border-slate-300 text-slate-600 hover:bg-slate-50'}`}>
              {p}
            </button>
          ))}
        </div>
        {period === 'Custom' && (
          <div className="flex items-center gap-2 mt-2">
            <input type="date" className="rounded border border-slate-300 px-2 py-1 text-xs" value={customFrom} onChange={e => setCustomFrom(e.target.value)} />
            <span className="text-slate-400 text-xs">to</span>
            <input type="date" className="rounded border border-slate-300 px-2 py-1 text-xs" value={customTo} onChange={e => setCustomTo(e.target.value)} />
          </div>
        )}
      </div>

      {!result || !result.detail.length ? (
        <p className="text-slate-400 text-sm py-8 text-center">No savings interest found for the selected period.</p>
      ) : (
        <>
          <div className="bg-slate-50 rounded-lg p-3 text-xs text-slate-500">
            <strong>Principal</strong> = non-interest cash inflows within the selected period (deposits/transfers in, excluding interest). <strong>Total Interest</strong> = interest received within the selected period. <strong>Ann. YOC%</strong> = annualised interest ÷ time-weighted average balance over the period × 100. <strong>APY%</strong> = (1 + interest/avg balance) ^ (365/period days) − 1, the compound annualised rate implied by actual interest earned over the period.
          </div>

          <h4 className="text-sm font-semibold text-slate-700">Interest by Account — {result.period_label}</h4>
          <div className="grid grid-cols-4 gap-4">
            <div className="bg-slate-50 rounded-lg p-4 text-center"><p className="text-xs text-slate-500 mb-1"><Tooltip text="Total interest received across all savings accounts in the selected period.">Total ({result.period_label})</Tooltip></p><p className="text-xl font-bold text-green-700">{fmtEur(Number(result.summary.total_interest_eur ?? 0))}</p></div>
            <div className="bg-slate-50 rounded-lg p-4 text-center"><p className="text-xs text-slate-500 mb-1"><Tooltip text="Total interest for the selected period divided by the number of calendar months it spans.">Monthly Average</Tooltip></p><p className="text-xl font-bold">{fmtEur(Number(result.summary.avg_monthly_interest_eur ?? 0))}</p></div>
            <div className="bg-slate-50 rounded-lg p-4 text-center"><p className="text-xs text-slate-500 mb-1"><Tooltip text="Number of savings accounts with principal or interest activity in the selected period.">Accounts</Tooltip></p><p className="text-xl font-bold">{Number(result.summary.savings_accounts_count ?? 0)}</p></div>
            <div className="bg-slate-50 rounded-lg p-4 text-center"><p className="text-xs text-slate-500 mb-1"><Tooltip text="Average Annual Percentage Yield across accounts for the selected period.">Avg APY</Tooltip></p><p className="text-xl font-bold">{pct(result.summary.avg_apy_pct)}</p></div>
          </div>

          {result.monthly.length > 0 && (
            <Plot
              data={[{
                x: result.monthly.map(m => m.month),
                y: result.monthly.map(m => m.income_eur),
                type: 'bar', marker: { color: '#2ecc71' },
              }]}
              layout={{
                title: `Monthly Interest Income (€) — ${result.period_label}`,
                height: 320, margin: { t: 50, r: 20, b: 40, l: 60 },
                yaxis: { title: 'Income (€)' },
                xaxis: { tickformat: '%b %Y', dtick: 'M1', type: 'date' as const },
                ...plotLayout(isDark),
              }}
              config={{ displayModeBar: false }} style={{ width: '100%' }}
            />
          )}

          <div>
            <h4 className="text-sm font-semibold text-slate-700 mb-2">Detail — {result.period_label}</h4>
            <WithCopy>
              <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-300px)]">
                <table className="w-full text-sm">
                  <thead className="sticky top-0 z-10"><tr className="bg-slate-50 text-xs text-slate-500 uppercase tracking-wide">
                    <ColHeader label="Account" sortKey="accounts_name" currentKey={detailSK} currentDir={detailSD} onSort={detailSort} tooltip="Savings account name." />
                    <ColHeader label="Type" sortKey="accounts_type" currentKey={detailSK} currentDir={detailSD} onSort={detailSort} tooltip="Account type." />
                    <ColHeader label="Curr" sortKey="currency" currentKey={detailSK} currentDir={detailSD} onSort={detailSort} tooltip="Account currency." />
                    <ColHeader label="Principal" sortKey="principal" currentKey={detailSK} currentDir={detailSD} onSort={detailSort} align="right" tooltip="Non-interest cash inflows within the selected period." />
                    <ColHeader label="Total Interest" sortKey="total_interest" currentKey={detailSK} currentDir={detailSD} onSort={detailSort} align="right" tooltip="Interest received within the selected period." />
                    <ColHeader label="Avg Balance" sortKey="avg_balance" currentKey={detailSK} currentDir={detailSD} onSort={detailSort} align="right" tooltip="Time-weighted average balance during the selected period — the base the yield figures below are computed against." />
                    <ColHeader label="Current Balance" sortKey="current_balance" currentKey={detailSK} currentDir={detailSD} onSort={detailSort} align="right" tooltip="Today's ledger balance (not scoped to the period)." />
                    <ColHeader label="Ann. YOC%" sortKey="annual_yoc_pct" currentKey={detailSK} currentDir={detailSD} onSort={detailSort} align="right" tooltip="Annualised Yield on Cost for the period: annualised interest ÷ average balance × 100." />
                    <ColHeader label="APY%" sortKey="apy_pct" currentKey={detailSK} currentDir={detailSD} onSort={detailSort} align="right" tooltip="Compound annualised rate for the period." />
                    <ColHeader label="Days" sortKey="holding_days" currentKey={detailSK} currentDir={detailSD} onSort={detailSort} align="right" tooltip="Number of days in the selected period." />
                  </tr></thead>
                  <tbody className="divide-y divide-slate-100">
                    {detailSorted.map((r, i) => (
                      <tr key={i} className="hover:bg-slate-50">
                        <td className="px-3 py-2 font-medium"><AccountLink id={r.accounts_id as number} name={String(r.accounts_name)} type={String(r.accounts_type)} /></td>
                        <td className="px-3 py-2 text-slate-500">{String(r.accounts_type)}</td>
                        <td className="px-3 py-2 text-slate-500">{String(r.currency)}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{fmtNum(Number(r.principal ?? 0), 2)}</td>
                        <td className="px-3 py-2 text-right tabular-nums text-green-700">{fmtNum(Number(r.total_interest ?? 0), 2)}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{fmtNum(Number(r.avg_balance ?? 0), 2)}</td>
                        <td className="px-3 py-2 text-right tabular-nums font-semibold">{fmtNum(Number(r.current_balance ?? 0), 2)}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{pct(r.annual_yoc_pct)}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{pct(r.apy_pct)}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{days(r.holding_days)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </WithCopy>
          </div>
        </>
      )}

      <div>
        <h4 className="text-sm font-semibold text-slate-700 mb-2">Detail for Last Interest Period</h4>
        <WithCopy>
          <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-300px)]">
            <table className="w-full text-sm">
              <thead className="sticky top-0 z-10"><tr className="bg-slate-50 text-xs text-slate-500 uppercase tracking-wide">
                <th className="px-3 py-2 text-left whitespace-nowrap"><Tooltip text="Savings account name.">Account</Tooltip></th>
                <th className="px-3 py-2 text-left whitespace-nowrap"><Tooltip text="Account type.">Type</Tooltip></th>
                <th className="px-3 py-2 text-left whitespace-nowrap"><Tooltip text="Account currency.">Curr</Tooltip></th>
                <th className="px-3 py-2 text-right whitespace-nowrap"><Tooltip text="Average principal balance during the last interest period.">Avg Principal</Tooltip></th>
                <th className="px-3 py-2 text-right whitespace-nowrap"><Tooltip text="Total interest received in the most recent interest period.">Last Interest</Tooltip></th>
                <th className="px-3 py-2 text-right whitespace-nowrap"><Tooltip text="Last period's interest extrapolated to a full year.">Annual Interest (cash)</Tooltip></th>
                <th className="px-3 py-2 text-right whitespace-nowrap"><Tooltip text="Annual Yield on Cost for the last period: interest ÷ average principal × 100.">Annual YOC%</Tooltip></th>
                <th className="px-3 py-2 text-right whitespace-nowrap"><Tooltip text="Compound annualised rate for the last period — (1 + interest/principal)^(365/days) − 1. This is what Forecast projects forward.">APY%</Tooltip></th>
                <th className="px-3 py-2 text-right whitespace-nowrap"><Tooltip text="Number of days in the last interest period.">Holding Days</Tooltip></th>
                <th className="px-3 py-2 text-right whitespace-nowrap"><Tooltip text="Start date of the last interest period.">Period Start</Tooltip></th>
                <th className="px-3 py-2 text-right whitespace-nowrap"><Tooltip text="Date when the last interest payment was credited.">Last Interest Date</Tooltip></th>
              </tr></thead>
              <tbody className="divide-y divide-slate-100">
                {(result?.detail_last ?? []).map((r, i) => (
                  <tr key={i} className="hover:bg-slate-50">
                    <td className="px-3 py-2 font-medium whitespace-nowrap"><AccountLink id={r.accounts_id as number} name={String(r.accounts_name)} type={String(r.accounts_type)} /></td>
                    <td className="px-3 py-2 text-slate-500 whitespace-nowrap">{String(r.accounts_type)}</td>
                    <td className="px-3 py-2 text-slate-500 whitespace-nowrap">{String(r.currency)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{r.avg_principal_last != null ? fmtNum(Number(r.avg_principal_last), 2) : '—'}</td>
                    <td className="px-3 py-2 text-right tabular-nums text-green-700">{r.last_interest_sum != null ? fmtNum(Number(r.last_interest_sum), 2) : '—'}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{r.annual_interest_cash_last != null ? fmtNum(Number(r.annual_interest_cash_last), 2) : '—'}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{pct(r.annual_yoc_pct_last)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{pct(r.apy_pct_last)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{days(r.holding_days_last)}</td>
                    <td className="px-3 py-2 text-right text-slate-500 whitespace-nowrap">{dateStr(r.period_start_date)}</td>
                    <td className="px-3 py-2 text-right text-slate-500 whitespace-nowrap">{dateStr(r.last_interest_date)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </WithCopy>
      </div>
    </div>
  )
}

function BondScheduleTab() {
  const { isDark } = useTheme()
  const liveRefetchMs = useLiveRefetchInterval()
  const { data = [], isLoading } = useQuery({ queryKey: ['bond-schedule'], queryFn: getBondSchedule, refetchInterval: liveRefetchMs })
  const rows = data as Row[]
  const { sorted: bondSorted, sortKey: bondSK, sortDir: bondSD, toggleSort: bondSort } = useSortTablePersisted(rows, 'bond-schedule-sort', 'days_to_maturity', 'asc')
  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  if (!rows.length) return <p className="text-slate-400 text-sm py-8 text-center">No bond holdings found.</p>

  const totalFace = rows.reduce((s, r) => s + Number(r.total_face_eur ?? 0), 0)
  const totalCoupon = rows.reduce((s, r) => s + Number(r.annual_coupon_eur ?? 0), 0)
  const maturingIn12m = rows.filter(r => r.days_to_maturity != null && Number(r.days_to_maturity) <= 365).length

  const chartData = rows.filter(r => r.maturity_date).map(r => ({
    x: String(r.maturity_date),
    y: Number(r.total_face_eur ?? 0),
    name: String(r.securities_name),
  }))

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-3 gap-4">
        <div className="bg-slate-50 rounded-lg p-4 text-center"><p className="text-xs text-slate-500 mb-1"><Tooltip text="Sum of face (par) values across all held bonds, converted to EUR. This is the amount you will receive back at maturity for each bond.">Total Face Value (EUR)</Tooltip></p><p className="text-xl font-bold">{fmtEur(totalFace)}</p></div>
        <div className="bg-slate-50 rounded-lg p-4 text-center"><p className="text-xs text-slate-500 mb-1"><Tooltip text="Expected annual coupon payments from all held bonds based on stated coupon rates and current quantities.">Annual Coupon Income (EUR)</Tooltip></p><p className="text-xl font-bold text-green-700">{fmtEur(totalCoupon)}</p></div>
        <div className="bg-slate-50 rounded-lg p-4 text-center"><p className="text-xs text-slate-500 mb-1"><Tooltip text="Number of bond positions maturing within the next 12 months — these will return face value and stop paying coupons.">Maturing in 12 months</Tooltip></p><p className="text-xl font-bold text-amber-600">{maturingIn12m}</p></div>
      </div>
      <Plot
        data={[{ type: 'bar', x: chartData.map(d => d.x), y: chartData.map(d => d.y), text: chartData.map(d => d.name), hovertemplate: '%{text}<br>%{x}<br>%{y:,.0f} EUR<extra></extra>', marker: { color: '#3b82f6' } }]}
        layout={{ title: 'Maturity Timeline', height: 300, xaxis: { title: 'Maturity Date' }, yaxis: { title: 'Face Value (EUR)' }, margin: { t: 40, b: 60, l: 80, r: 20 }, ...plotLayout(isDark) }}
        config={{ displayModeBar: false }} style={{ width: '100%' }}
      />
      <WithCopy>
        <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-300px)]">
          <table className="w-full text-sm">
            <thead className="sticky top-0 z-10"><tr className="bg-slate-50 text-xs text-slate-500 uppercase tracking-wide">
              <ColHeader label="Security" sortKey="securities_name" currentKey={bondSK} currentDir={bondSD} onSort={bondSort} tooltip="Bond security name." />
              <ColHeader label="Qty" sortKey="quantity" currentKey={bondSK} currentDir={bondSD} onSort={bondSort} align="right" tooltip="Number of units held." />
              <ColHeader label="Face Value" sortKey="face_value" currentKey={bondSK} currentDir={bondSD} onSort={bondSort} align="right" tooltip="Par (face) value per unit — the amount repaid at maturity per bond." />
              <ColHeader label="Total Face (EUR)" sortKey="total_face_eur" currentKey={bondSK} currentDir={bondSD} onSort={bondSort} align="right" tooltip="Total par value of your position (quantity × face value), converted to EUR." />
              <ColHeader label="Coupon %" sortKey="coupon_rate" currentKey={bondSK} currentDir={bondSD} onSort={bondSort} align="right" tooltip="Annual coupon rate stated on the bond, as a percentage of face value." />
              <ColHeader label="Frequency" sortKey="coupon_frequency" currentKey={bondSK} currentDir={bondSD} onSort={bondSort} align="right" tooltip="How often coupon payments are made — annual, semi-annual, quarterly, or monthly." />
              <ColHeader label="Next Coupon (EUR)" sortKey="next_coupon_eur" currentKey={bondSK} currentDir={bondSD} onSort={bondSort} align="right" tooltip="Estimated next coupon payment in EUR based on your quantity and coupon rate." />
              <ColHeader label="Annual Coupon (EUR)" sortKey="annual_coupon_eur" currentKey={bondSK} currentDir={bondSD} onSort={bondSort} align="right" tooltip="Total expected coupon income from this bond over a full year." />
              <ColHeader label="Maturity" sortKey="maturity_date" currentKey={bondSK} currentDir={bondSD} onSort={bondSort} align="right" tooltip="Date when the bond matures and face value is repaid." />
              <ColHeader label="Days Left" sortKey="days_to_maturity" currentKey={bondSK} currentDir={bondSD} onSort={bondSort} align="right" tooltip="Calendar days remaining until maturity. Highlighted amber when under 365 days." />
              <ColHeader label="Ccy" sortKey="currency" currentKey={bondSK} currentDir={bondSD} onSort={bondSort} align="right" tooltip="Currency the bond is denominated in." />
            </tr></thead>
            <tbody className="divide-y divide-slate-100">
              {bondSorted.map((r, i) => (
                <tr key={i} className="hover:bg-slate-50">
                  <td className="px-3 py-2 font-medium"><SecLink id={r.securities_id}>{String(r.securities_name)}</SecLink></td>
                  <td className="px-3 py-2 text-right tabular-nums">{fmtNum(Number(r.quantity), 4)}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{fmtNum(Number(r.face_value ?? 0), 2)}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{fmtEur(Number(r.total_face_eur ?? 0))}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{r.coupon_rate != null ? `${Number(r.coupon_rate).toFixed(2)}%` : '—'}</td>
                  <td className="px-3 py-2 text-right">{String(r.coupon_frequency ?? '—')}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-green-700">{fmtEur(Number(r.next_coupon_eur ?? 0))}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-green-700">{fmtEur(Number(r.annual_coupon_eur ?? 0))}</td>
                  <td className="px-3 py-2 text-right">{r.maturity_date ? String(r.maturity_date).slice(0, 10) : '—'}</td>
                  <td className={`px-3 py-2 text-right tabular-nums ${Number(r.days_to_maturity ?? 999) <= 365 ? 'text-amber-600 font-semibold' : ''}`}>{r.days_to_maturity != null ? Number(r.days_to_maturity) : '—'}</td>
                  <td className="px-3 py-2 text-right text-slate-400">{String(r.currency ?? '')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </WithCopy>
    </div>
  )
}

function BenchmarkTab({ accountIds, keyPrefix = 'bench', defaultYtd = false }: { accountIds?: number[]; keyPrefix?: string; defaultYtd?: boolean }) {
  const { isDark } = useTheme()
  const liveRefetchMs = useLiveRefetchInterval()
  const { data: candidates = [] } = useQuery({ queryKey: ['benchmark-candidates'], queryFn: getBenchmarkCandidates })
  const { data: allAccounts = [] } = useQuery({ queryKey: ['allAccountsForPreset'], queryFn: () => getAccounts() })
  const [compareMode, setCompareMode] = usePersist<'index' | 'account'>(`${keyPrefix}_compare_mode`, 'index')
  const [benchmarkId, setBenchmarkId] = usePersist<number | null>(`${keyPrefix}_id`, null)
  const [compareAccountId, setCompareAccountId] = usePersist<number | null>(`${keyPrefix}_cmp_account`, null)
  const [lookback, setLookback] = usePersist(`${keyPrefix}_lookback`, 365)
  const [ytd, setYtd] = usePersist(`${keyPrefix}_ytd`, defaultYtd)
  const [resample, setResample] = usePersist(`${keyPrefix}_resample`, 'Daily')
  const cands = candidates as Row[]
  const otherAccounts = (allAccounts as Row[])
    .filter(a => INV_ACCOUNT_TYPES.includes(String(a.type)))
    .filter(a => a.is_active !== false && a.is_active !== 0 && a.is_active !== 'false')
    .map(a => ({ id: Number(a.id), name: String(a.name ?? '') }))
    .filter(a => !accountIds || !accountIds.includes(a.id))
    .sort((a, b) => a.name.localeCompare(b.name))

  const effId = benchmarkId ?? (cands[0] ? Number(cands[0].id) : null)
  const effCompareAccountId = compareAccountId ?? (otherAccounts[0]?.id ?? null)
  const isAccountMode = compareMode === 'account'

  const { data = [], isLoading } = useQuery({
    queryKey: ['benchmark', isAccountMode ? null : effId, isAccountMode ? effCompareAccountId : null, lookback, ytd, accountIds, resample],
    queryFn: () => getBenchmark(
      isAccountMode ? null : effId!, lookback, accountIds, resample,
      isAccountMode && effCompareAccountId != null ? [effCompareAccountId] : undefined,
      ytd,
    ),
    enabled: isAccountMode ? effCompareAccountId != null : effId != null,
    refetchInterval: liveRefetchMs,
  })
  const rows = data as { date: string; portfolio: number; benchmark: number | null }[]

  const compareLabel = isAccountMode
    ? (otherAccounts.find(a => a.id === effCompareAccountId)?.name ?? 'Account')
    : (cands.find(c => Number(c.id) === effId)?.name as string ?? 'Benchmark')

  const portReturn  = rows.length ? ((rows[rows.length - 1].portfolio / 100 - 1) * 100).toFixed(2) : null
  const benchReturn = rows.length && rows[rows.length - 1].benchmark != null
    ? ((rows[rows.length - 1].benchmark! / 100 - 1) * 100).toFixed(2) : null

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <label className="text-xs text-slate-500"><Tooltip text="Compare against a market index/security, or against another account's own holdings-weighted performance.">Compare vs</Tooltip></label>
          <div className="flex rounded border border-slate-300 overflow-hidden text-xs">
            <button onClick={() => setCompareMode('index')} className={`px-2 py-1 ${!isAccountMode ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 hover:bg-slate-50'}`}>Market Index</button>
            <button onClick={() => setCompareMode('account')} className={`px-2 py-1 ${isAccountMode ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 hover:bg-slate-50'}`}>Account</button>
          </div>
        </div>
        {isAccountMode ? (
          <div className="flex items-center gap-2">
            <select className="rounded border border-slate-300 px-2 py-1 text-sm"
              value={effCompareAccountId ?? ''} onChange={e => setCompareAccountId(Number(e.target.value))}>
              {otherAccounts.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
          </div>
        ) : (
          <div className="flex items-center gap-2">
            <select className="rounded border border-slate-300 px-2 py-1 text-sm"
              value={effId ?? ''} onChange={e => setBenchmarkId(Number(e.target.value))}>
              {cands.map(c => <option key={String(c.id)} value={String(c.id)}>{String(c.name)}{c.ticker ? ` (${c.ticker})` : ''}</option>)}
            </select>
          </div>
        )}
        <div className="flex items-center gap-2">
          <label className="text-xs text-slate-500"><Tooltip text="Lookback window: YTD = since Jan 1 this year, 3M = 91 days, 6M = 182, 1Y = 365, 2Y = 730, 3Y = 1095. Both series are indexed to 100 at the start date.">Lookback</Tooltip></label>
          <button onClick={() => setYtd(true)}
            className={`px-2 py-1 text-xs rounded border ${ytd ? 'bg-blue-600 text-white border-blue-600' : 'border-slate-300 text-slate-600 hover:bg-slate-50'}`}>
            YTD
          </button>
          {([91, 182, 365, 730, 1095] as const).map(d => (
            <button key={d} onClick={() => { setYtd(false); setLookback(d) }}
              className={`px-2 py-1 text-xs rounded border ${!ytd && lookback === d ? 'bg-blue-600 text-white border-blue-600' : 'border-slate-300 text-slate-600 hover:bg-slate-50'}`}>
              {d === 91 ? '3M' : d === 182 ? '6M' : d === 365 ? '1Y' : d === 730 ? '2Y' : '3Y'}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <label className="text-xs text-slate-500"><Tooltip text="Frequency at which data points are plotted. Daily shows every trading day; Weekly/Monthly reduce noise and improve readability for long windows.">Resample</Tooltip></label>
          <select className="rounded border border-slate-300 px-2 py-1 text-sm" value={resample} onChange={e => setResample(e.target.value)}>
            <option value="Daily">Daily</option>
            <option value="Weekly">Weekly</option>
            <option value="Monthly">Monthly</option>
          </select>
        </div>
      </div>
      {portReturn != null && benchReturn != null && (
        <div className="grid grid-cols-2 gap-4">
          <div className="bg-slate-50 rounded-lg p-4 text-center"><p className="text-xs text-slate-500 mb-1"><Tooltip text="Portfolio's total return over the selected period (indexed: end value ÷ start value − 1). Value-weighted by current holdings.">Portfolio Return</Tooltip></p><p className={`text-xl font-bold ${Number(portReturn) >= 0 ? 'text-green-700' : 'text-red-600'}`}>{Number(portReturn) >= 0 ? '+' : ''}{portReturn}%</p></div>
          <div className="bg-slate-50 rounded-lg p-4 text-center"><p className="text-xs text-slate-500 mb-1"><Tooltip text={isAccountMode ? "The comparison account's own total return over the same period, indexed to the same start date." : "Selected benchmark's total return over the same period, indexed to the same start date as your portfolio."}>{compareLabel} Return</Tooltip></p><p className={`text-xl font-bold ${Number(benchReturn) >= 0 ? 'text-green-700' : 'text-red-600'}`}>{Number(benchReturn) >= 0 ? '+' : ''}{benchReturn}%</p></div>
        </div>
      )}
      {isLoading ? <div className="flex justify-center py-12"><Spinner /></div> : rows.length > 0 && (
        <Plot
          data={[
            { x: rows.map(r => r.date), y: rows.map(r => r.portfolio), name: 'Portfolio', type: 'scatter', mode: 'lines', line: { color: '#3b82f6', width: 2 } },
            { x: rows.map(r => r.date), y: rows.map(r => r.benchmark), name: compareLabel, type: 'scatter', mode: 'lines', line: { color: '#f59e0b', width: 2, dash: 'dot' } },
          ]}
          layout={{ height: 380, yaxis: { title: 'Indexed (100 = start)', tickformat: '.1f' }, xaxis: { title: '' }, legend: { orientation: 'h', y: -0.2 }, margin: { t: 20, b: 60, l: 70, r: 20 }, ...plotLayout(isDark) }}
          config={{ displayModeBar: false }} style={{ width: '100%' }}
        />
      )}
    </div>
  )
}

function CorrelationTab({ accountIds }: { accountIds?: number[] }) {
  const liveRefetchMs = useLiveRefetchInterval()
  const [lookback, setLookback] = usePersist('corr_lookback', 252)
  const [maxH, setMaxH] = usePersist('corr_max', 20)

  const { data, isLoading } = useQuery({
    queryKey: ['correlation', lookback, maxH, accountIds],
    queryFn: () => getCorrelation(lookback, maxH, accountIds),
    refetchInterval: liveRefetchMs,
  })
  const result = data as { tickers: string[]; matrix: (number | null)[][] } | undefined

  const colorScale = (v: number | null) => {
    if (v === null) return '#e2e8f0'
    const r = v >= 0 ? Math.round(v * 220) : 0
    const b = v < 0 ? Math.round(-v * 220) : 0
    const g = Math.round((1 - Math.abs(v)) * 180)
    return `rgb(${r},${g},${b})`
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <label className="text-xs text-slate-500"><Tooltip text="Number of trading days of daily price returns used to compute pairwise correlations. Shorter windows are more reactive to recent market regimes.">Lookback</Tooltip></label>
          {([60, 126, 252, 504] as const).map(d => (
            <button key={d} onClick={() => setLookback(d)}
              className={`px-2 py-1 text-xs rounded border ${lookback === d ? 'bg-blue-600 text-white border-blue-600' : 'border-slate-300 text-slate-600 hover:bg-slate-50'}`}>
              {d === 60 ? '3M' : d === 126 ? '6M' : d === 252 ? '1Y' : '2Y'}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <label className="text-xs text-slate-500"><Tooltip text="Limit the matrix to your top N holdings by value. Larger numbers can make the matrix harder to read.">Max Holdings</Tooltip></label>
          {([10, 15, 20, 30] as const).map(n => (
            <button key={n} onClick={() => setMaxH(n)}
              className={`px-2 py-1 text-xs rounded border ${maxH === n ? 'bg-blue-600 text-white border-blue-600' : 'border-slate-300 text-slate-600 hover:bg-slate-50'}`}>
              {n}
            </button>
          ))}
        </div>
      </div>
      {isLoading ? <div className="flex justify-center py-12"><Spinner /></div>
        : !result || !result.tickers.length ? <p className="text-slate-400 text-sm py-8 text-center">No price data available.</p>
        : (
          <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-300px)]">
            <table className="text-xs border-collapse">
              <thead className="sticky top-0 z-10 bg-white">
                <tr>
                  <th className="px-2 py-1 text-left text-slate-500 font-normal min-w-32"></th>
                  {result.tickers.map(t => (
                    <th key={t} className="px-1 py-1 text-center font-medium text-slate-600" style={{ minWidth: 60, maxWidth: 90 }}>
                      <div style={{ writingMode: 'vertical-rl', transform: 'rotate(180deg)', height: 80 }} className="text-xs">{t.length > 20 ? t.slice(0, 18) + '…' : t}</div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.matrix.map((row, i) => (
                  <tr key={i}>
                    <td className="px-2 py-1 font-medium text-slate-700 whitespace-nowrap">{result.tickers[i].length > 28 ? result.tickers[i].slice(0, 26) + '…' : result.tickers[i]}</td>
                    {row.map((v, j) => (
                      <td key={j} className="text-center tabular-nums font-mono" style={{ backgroundColor: colorScale(v), padding: '4px 6px', border: '1px solid #f1f5f9' }}>
                        <span style={{ color: v != null && Math.abs(v) > 0.5 ? '#fff' : '#1e293b' }}>
                          {v != null ? v.toFixed(2) : '—'}
                        </span>
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="flex items-center gap-3 mt-3 text-xs text-slate-500">
              <div className="flex items-center gap-1"><div className="w-4 h-4 rounded" style={{ background: 'rgb(220,0,0)' }} />Strong positive</div>
              <div className="flex items-center gap-1"><div className="w-4 h-4 rounded" style={{ background: 'rgb(0,180,0)' }} />Uncorrelated</div>
              <div className="flex items-center gap-1"><div className="w-4 h-4 rounded" style={{ background: 'rgb(0,0,220)' }} />Strong negative</div>
            </div>
          </div>
        )}
    </div>
  )
}

const FULL_PORTFOLIO = 'Full Portfolio'
const INV_ACCOUNT_TYPES = ['Brokerage', 'Margin', 'Pension', 'Other Investment']
// Investment Position's preset picker also offers Cash/Bank accounts, since a
// position/allocation report can legitimately want to include cash as a bucket
// (see X-Ray Asset Allocation's Cash bucket) — not just investment accounts.
const INV_POSITION_ACCOUNT_TYPES = ['Brokerage', 'Margin', 'Pension', 'Other Investment', 'Cash', 'Checking', 'Savings', 'Credit Card']
// Net Worth can meaningfully include any account type at all — reuses the
// pre-existing ALL_ACCOUNT_TYPES constant defined further down this file.

// Shared "which accounts to include" preset picker, backed by Portfolio_Presets
// (Report_Scope keeps each report section's saved preset names in their own
// namespace — see api/routers/reports.py's portfolio-presets endpoints).
const EMPTY_PRESET_ROWS: never[] = []
function PortfolioPresetBar({ reportScope, eligibleTypes, onChange }: { reportScope: string; eligibleTypes: string[]; onChange: (ids: number[] | undefined) => void }) {
  const [open, setOpen] = useState(false)
  const [selPreset, setSelPreset] = usePersist(`preset_sel_${reportScope}`, FULL_PORTFOLIO)
  const [nameInput, setNameInput] = useState('')
  const [draftIds, setDraftIds] = useState<Set<number> | null>(null)
  const [showInactive, setShowInactive] = useState(false)

  // A stable empty-array default matters here: while the query is still loading,
  // `data` is undefined — destructuring a fresh `[]` literal every render would give
  // presetList/presetMap a new reference each time, retriggering the onChange effect
  // below in an infinite loop (setState -> re-render -> "new" empty deps -> setState...).
  const { data: accounts = EMPTY_PRESET_ROWS } = useQuery({ queryKey: ['allAccountsForPreset'], queryFn: () => getAccounts() })
  const { data: presets = EMPTY_PRESET_ROWS, refetch: refetchPresets } = useQuery({ queryKey: ['portfolio-presets', reportScope], queryFn: () => getPortfolioPresets(reportScope) })

  // Inactive accounts stay selectable (an already-saved preset can still include one,
  // even with the checkbox off) — showInactive only controls whether they're offered
  // in the list for a *new* selection, matching the Show Inactive pattern used elsewhere.
  const isActive = (a: Row) => a.is_active !== false && a.is_active !== 0 && a.is_active !== 'false'
  const eligibleAccounts = (accounts as Row[]).filter(a => eligibleTypes.includes(String(a.type)) && (showInactive || isActive(a)))
  const groupedEligible = useMemo(() => {
    const byType = new Map<string, Row[]>()
    for (const a of eligibleAccounts) {
      const t = String(a.type ?? 'Other')
      if (!byType.has(t)) byType.set(t, [])
      byType.get(t)!.push(a)
    }
    const orderedTypes = [
      ...ACCOUNT_TYPE_ORDER.filter(t => byType.has(t)),
      ...[...byType.keys()].filter(t => !ACCOUNT_TYPE_ORDER.includes(t)),
    ]
    return orderedTypes.map(t => [t, byType.get(t)!] as const)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accounts, eligibleTypes, showInactive])
  const presetList = presets as { preset_id: number; preset_name: string; account_ids: number[] }[]
  const presetMap = useMemo(() => {
    const m: Record<string, number[]> = {}
    for (const p of presetList) m[p.preset_name] = p.account_ids ?? []
    return m
  }, [presetList])

  const savedIds = selPreset === FULL_PORTFOLIO ? eligibleAccounts.map(a => Number(a.id)) : (presetMap[selPreset] ?? [])
  const currentIds = draftIds ?? new Set(savedIds)

  useEffect(() => {
    onChange(selPreset === FULL_PORTFOLIO ? undefined : Array.from(currentIds))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selPreset, presetMap])

  const toggleAccount = (id: number) => {
    const next = new Set(draftIds ?? savedIds)
    if (next.has(id)) next.delete(id); else next.add(id)
    setDraftIds(next)
    if (selPreset !== FULL_PORTFOLIO) onChange(Array.from(next))
  }

  const handleSave = async () => {
    const name = nameInput.trim()
    if (!name || name === FULL_PORTFOLIO) { alert("Please enter a valid preset name (not 'Full Portfolio')."); return }
    const ids = Array.from(currentIds)
    if (!ids.length) { alert('Select at least one account before saving.'); return }
    await upsertPortfolioPreset(name, ids, reportScope)
    await refetchPresets()
    setSelPreset(name)
    setDraftIds(null)
    onChange(ids)
  }

  const handleDelete = async () => {
    const match = presetList.find(p => p.preset_name === selPreset)
    if (!match) return
    if (!window.confirm(`Delete preset '${selPreset}'? This cannot be undone.`)) return
    await deletePortfolioPreset(match.preset_id)
    await refetchPresets()
    setSelPreset(FULL_PORTFOLIO)
    setDraftIds(null)
    onChange(undefined)
  }

  return (
    <div className="border border-slate-200 rounded-lg overflow-hidden mb-4">
      <button onClick={() => setOpen(!open)} className="w-full flex items-center gap-2 px-4 py-2 text-sm font-medium text-slate-700 bg-slate-50 hover:bg-slate-100 text-left">
        <span className="text-xs">{open ? '▼' : '▶'}</span>
        <span>⚙️ Account Preset {selPreset !== FULL_PORTFOLIO && <span className="text-blue-600">— {selPreset}</span>}</span>
      </button>
      {open && (
        <div className="p-3 space-y-3 border-t border-slate-200">
          <div className="flex flex-wrap items-center gap-2">
            <select className="rounded border border-slate-300 px-2 py-1 text-sm" value={selPreset}
              onChange={e => { setSelPreset(e.target.value); setDraftIds(null); setNameInput(e.target.value === FULL_PORTFOLIO ? '' : e.target.value) }}>
              <option value={FULL_PORTFOLIO}>{FULL_PORTFOLIO}</option>
              {[...presetList].sort((a, b) => a.preset_name.localeCompare(b.preset_name)).map(p => (
                <option key={p.preset_id} value={p.preset_name}>{p.preset_name}</option>
              ))}
            </select>
            <input className="rounded border border-slate-300 px-2 py-1 text-sm flex-1 min-w-[160px]" placeholder="Name to save as…"
              value={nameInput} onChange={e => setNameInput(e.target.value)} />
            <button onClick={handleSave} className="px-3 py-1.5 text-xs rounded bg-blue-600 text-white font-medium hover:bg-blue-700">💾 Save</button>
            <button onClick={handleDelete} disabled={selPreset === FULL_PORTFOLIO}
              className="px-3 py-1.5 text-xs rounded bg-red-50 text-red-600 font-medium hover:bg-red-100 disabled:opacity-40 disabled:cursor-not-allowed">🗑️ Delete</button>
            <label className="flex items-center gap-1.5 text-xs text-slate-500 cursor-pointer select-none ml-auto">
              <input type="checkbox" className="rounded" checked={showInactive} onChange={e => setShowInactive(e.target.checked)} />
              Show inactive
            </label>
          </div>
          <div className="max-h-60 overflow-y-auto border border-slate-200 rounded">
            {groupedEligible.map(([type, accs]) => (
              <div key={type}>
                <div className="px-3 py-1 text-xs font-semibold text-slate-500 bg-slate-50 sticky top-0">{type}</div>
                {accs.map(a => {
                  const id = Number(a.id)
                  const checked = selPreset === FULL_PORTFOLIO ? true : currentIds.has(id)
                  return (
                    <label key={id} className={`flex items-center gap-2 px-3 py-1.5 text-sm border-b border-slate-100 last:border-0 ${selPreset === FULL_PORTFOLIO ? 'opacity-50' : 'hover:bg-slate-50 cursor-pointer'}`}>
                      <input type="checkbox" className="rounded" checked={checked} disabled={selPreset === FULL_PORTFOLIO} onChange={() => toggleAccount(id)} />
                      <span>{String(a.name)}</span>
                      {a.is_active === false && <span className="text-xs text-slate-400">(inactive)</span>}
                    </label>
                  )
                })}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function MonteCarloTab({ accountIds }: { accountIds?: number[] }) {
  const { isDark } = useTheme()
  const [yearsAhead, setYearsAhead] = usePersist('mc_years', 10)
  const [numSims, setNumSims] = usePersist('mc_sims', 500)
  const [monthlyContrib, setMonthlyContrib] = usePersist('mc_contrib', 500)
  const [lookbackMc, setLookbackMc] = usePersist('mc_lookback', 730)
  const [overrideOpen, setOverrideOpen] = useState(false)
  const [overrideReturn, setOverrideReturn] = useState<string>('')
  const [overrideVol, setOverrideVol] = useState<string>('')
  const [initialOverride, setInitialOverride] = useState<string>('')

  const { data, isLoading } = useQuery({
    queryKey: ['monte-carlo', yearsAhead, numSims, monthlyContrib, lookbackMc, accountIds, overrideReturn, overrideVol, initialOverride],
    queryFn: () => getMonteCarlo({
      yearsAhead, numSims, monthlyContrib, lookbackDays: lookbackMc, accountIds,
      overrideReturnPct: overrideReturn ? Number(overrideReturn) : undefined,
      overrideVolPct: overrideVol ? Number(overrideVol) : undefined,
      initialValue: initialOverride ? Number(initialOverride) : undefined,
    }),
  })

  type MCResult = {
    calibration: { ann_return_pct: number; ann_vol_pct: number }
    used: { ann_return_pct: number; ann_vol_pct: number; initial_value: number }
    chart: { month: number; p10: number; p50: number; p90: number }[]
    probabilities: { target: number; probability_pct: number }[]
  }
  const result = data as MCResult | undefined
  const unrealistic = result != null && Math.abs(result.calibration.ann_return_pct) > 20

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div>
          <label className="text-xs text-slate-500 block mb-1"><Tooltip text="How many years into the future to project the portfolio. Longer horizons show wider uncertainty bands.">Years Ahead</Tooltip></label>
          <input type="range" min={1} max={30} value={yearsAhead} onChange={e => setYearsAhead(Number(e.target.value))} className="w-full" />
          <span className="text-xs text-slate-600">{yearsAhead} years</span>
        </div>
        <div>
          <label className="text-xs text-slate-500 block mb-1"><Tooltip text="Number of random scenarios to run. More simulations give smoother percentile bands but take longer to compute.">Simulations</Tooltip></label>
          <input type="range" min={100} max={2000} step={100} value={numSims} onChange={e => setNumSims(Number(e.target.value))} className="w-full" />
          <span className="text-xs text-slate-600">{numSims}</span>
        </div>
        <div>
          <label className="text-xs text-slate-500 block mb-1"><Tooltip text="Fixed amount added to the portfolio each month throughout the projection. Set to 0 to model a buy-and-hold scenario.">Monthly Contribution (€)</Tooltip></label>
          <input type="number" className="w-full rounded border border-slate-300 px-2 py-1 text-sm" value={monthlyContrib} onChange={e => setMonthlyContrib(Number(e.target.value))} />
        </div>
        <div>
          <label className="text-xs text-slate-500 block mb-1"><Tooltip text="Historical window used to estimate expected return and volatility for the simulation. Shorter windows react faster to recent market conditions.">Calibration Window</Tooltip></label>
          <div className="flex gap-2 flex-wrap">
            {([182, 365, 730, 1095, 1825] as const).map(d => (
              <button key={d} onClick={() => setLookbackMc(d)}
                className={`px-2 py-1 text-xs rounded border ${lookbackMc === d ? 'bg-blue-600 text-white border-blue-600' : 'border-slate-300 text-slate-600 hover:bg-slate-50'}`}>
                {d === 182 ? '6M' : d === 365 ? '1Y' : d === 730 ? '2Y' : d === 1095 ? '3Y' : '5Y'}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="border border-slate-200 rounded-lg overflow-hidden">
        <button onClick={() => setOverrideOpen(!overrideOpen)} className="w-full flex items-center gap-2 px-4 py-2 text-sm font-medium text-slate-700 bg-slate-50 hover:bg-slate-100 text-left">
          <span className="text-xs">{overrideOpen || unrealistic ? '▼' : '▶'}</span>
          <span>Calibration & Overrides</span>
          {result && <span className="text-xs text-slate-400 ml-2">historical: {result.calibration.ann_return_pct.toFixed(2)}% return / {result.calibration.ann_vol_pct.toFixed(2)}% vol</span>}
        </button>
        {(overrideOpen || unrealistic) && (
          <div className="p-3 border-t border-slate-200 space-y-2">
            {unrealistic && <p className="text-xs text-amber-600 bg-amber-50 rounded px-2 py-1.5">⚠️ Calibrated return looks unrealistic — consider overriding below.</p>}
            <div className="grid grid-cols-3 gap-3">
              <div>
                <label className="text-xs text-slate-500 block mb-1">Override Return %</label>
                <input type="number" step="0.1" className="w-full rounded border border-slate-300 px-2 py-1 text-sm" placeholder={result ? result.calibration.ann_return_pct.toFixed(2) : ''} value={overrideReturn} onChange={e => setOverrideReturn(e.target.value)} />
              </div>
              <div>
                <label className="text-xs text-slate-500 block mb-1">Override Volatility %</label>
                <input type="number" step="0.1" className="w-full rounded border border-slate-300 px-2 py-1 text-sm" placeholder={result ? result.calibration.ann_vol_pct.toFixed(2) : ''} value={overrideVol} onChange={e => setOverrideVol(e.target.value)} />
              </div>
              <div>
                <label className="text-xs text-slate-500 block mb-1">Initial Value (€)</label>
                <input type="number" className="w-full rounded border border-slate-300 px-2 py-1 text-sm" placeholder={result ? String(result.used.initial_value) : ''} value={initialOverride} onChange={e => setInitialOverride(e.target.value)} />
              </div>
            </div>
          </div>
        )}
      </div>

      {isLoading ? <div className="flex justify-center py-12"><Spinner /></div> : result && (
        <>
          <Plot
            data={[
              { x: result.chart.map(c => c.month), y: result.chart.map(c => c.p90), name: 'p90', type: 'scatter', mode: 'lines', line: { width: 0 }, showlegend: false },
              { x: result.chart.map(c => c.month), y: result.chart.map(c => c.p10), name: '10th–90th percentile', type: 'scatter', mode: 'lines', fill: 'tonexty', fillcolor: 'rgba(59,130,246,0.15)', line: { width: 0 } },
              { x: result.chart.map(c => c.month), y: result.chart.map(c => c.p50), name: 'Median (p50)', type: 'scatter', mode: 'lines', line: { color: '#3b82f6', width: 2.5 } },
            ]}
            layout={{ height: 400, margin: { t: 30, r: 20, b: 50, l: 70 }, xaxis: { title: 'Months ahead' }, yaxis: { title: 'Portfolio Value (€)', tickformat: ',.0f' }, legend: { orientation: 'h', y: -0.2 }, ...plotLayout(isDark) }}
            config={{ displayModeBar: false }} style={{ width: '100%' }}
          />
          <h4 className="text-sm font-semibold text-slate-700"><Tooltip text="Percentage of simulated paths that reach or exceed each target value at any point within the projection horizon.">Probability of Reaching Target Amounts</Tooltip></h4>
          <div className="grid grid-cols-5 gap-3">
            {result.probabilities.map(p => (
              <div key={p.target} className="bg-slate-50 rounded-lg p-3 text-center">
                <p className="text-xs text-slate-500 mb-1"><Tooltip text={`Probability that the portfolio reaches €${fmtNum(p.target, 0)} within ${yearsAhead} years across ${numSims} simulated scenarios.`}>€{fmtNum(p.target, 0)}</Tooltip></p>
                <p className="text-lg font-bold">{p.probability_pct.toFixed(1)}%</p>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

function InvPerformanceSection() {
  const [tab, setTab] = usePersist('inv_perf_tab', 'P&L')
  const [presetAccountIds, setPresetAccountIds] = useState<number[] | undefined>(undefined)
  const TABS = ['P&L', 'Performance', 'Savings', 'Dividend Tracker', 'Bond Schedule', 'Benchmark', 'Risk Metrics', 'Correlation', 'Monte Carlo', 'TWR/MWR']
  const qc = useQueryClient()
  useEffect(() => {
    qc.prefetchQuery({ queryKey: ['pnl'], queryFn: () => getPnl('1900-01-01') })
    qc.prefetchQuery({ queryKey: ['price-changes'], queryFn: getPriceChanges })
    qc.prefetchQuery({ queryKey: ['bond-schedule'], queryFn: getBondSchedule })
    qc.prefetchQuery({ queryKey: ['dividends-tracker', 'YTD', null, null], queryFn: () => getDividendsTracker('YTD') })
  }, [])
  const needsPreset = ['Benchmark', 'Risk Metrics', 'Correlation', 'Monte Carlo', 'TWR/MWR'].includes(tab)
  return (
    <div>
      <SubTabs tabs={TABS} active={tab} onChange={setTab} />
      {needsPreset && <PortfolioPresetBar reportScope="inv_performance" eligibleTypes={INV_ACCOUNT_TYPES} onChange={setPresetAccountIds} />}
      {tab === 'P&L'              && <PnlReport />}
      {tab === 'Performance'      && <PerformanceTab />}
      {tab === 'TWR/MWR'          && <TwrTab accountIds={presetAccountIds} />}
      {tab === 'Savings'          && <SavingsAccountsTab />}
      {tab === 'Dividend Tracker' && <DividendTrackerTab />}
      {tab === 'Bond Schedule'    && <BondScheduleTab />}
      {tab === 'Benchmark'        && <BenchmarkTab accountIds={presetAccountIds} />}
      {tab === 'Risk Metrics'     && <RiskMetricsTab accountIds={presetAccountIds} />}
      {tab === 'Correlation'      && <CorrelationTab accountIds={presetAccountIds} />}
      {tab === 'Monte Carlo'      && <MonteCarloTab accountIds={presetAccountIds} />}
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════════
// 4. SECURITIES ANALYSIS
// ════════════════════════════════════════════════════════════════════════════
function PriceChangesTab() {
  const { data = [], isLoading } = useQuery({ queryKey: ['price-changes'], queryFn: getPriceChanges })
  const { sorted: rows, sortKey: pcSK, sortDir: pcSD, toggleSort: pcSort } = useSortTablePersisted(data as Row[], 'price-changes-sort', 'value_eur', 'desc')
  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  const col = (key: string, label: string, align: 'left' | 'right' = 'right') => (
    <ColHeader label={label} sortKey={key} currentKey={pcSK} currentDir={pcSD} onSort={pcSort} align={align}
      className="px-2 py-1.5 border-b border-slate-200 hover:bg-slate-100" />
  )
  return (
    <WithCopy>
    <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-300px)] text-xs">
      <table className="w-full border-collapse">
        <thead className="sticky top-0 z-10 bg-slate-50">
          <tr className="bg-slate-50">
            {col('securities_name', 'Security', 'left')}
            {col('ticker', 'Ticker', 'left')}
            {col('value_eur', 'Value (€)')}
            {col('dtd_pct', 'D%')}
            {col('wtd_pct', 'W%')}
            {col('mtd_pct', 'M%')}
            {col('qtd_pct', 'Q%')}
            {col('ytd_pct', 'YTD%')}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-b border-slate-100 hover:bg-slate-50">
              <td className="px-2 py-1.5 font-medium"><SecLink id={r.securities_id}>{String(r.securities_name)}</SecLink></td>
              <td className="px-2 py-1.5 font-mono text-slate-500"><SecLink id={r.securities_id}>{String(r.ticker ?? '—')}</SecLink></td>
              <td className="px-2 py-1.5 text-right tabular-nums">{fmtEur(Number(r.value_eur ?? 0))}</td>
              <PctCell val={r.dtd_pct != null ? Number(r.dtd_pct) : null} />
              <PctCell val={r.wtd_pct != null ? Number(r.wtd_pct) : null} />
              <PctCell val={r.mtd_pct != null ? Number(r.mtd_pct) : null} />
              <PctCell val={r.qtd_pct != null ? Number(r.qtd_pct) : null} />
              <PctCell val={r.ytd_pct != null ? Number(r.ytd_pct) : null} />
            </tr>
          ))}
        </tbody>
      </table>
    </div>
    </WithCopy>
  )
}

// ── Shared hook for portfolio signals data ────────────────────────────────────
function usePortfolioSignals() {
  return useQuery({ queryKey: ['portfolio-signals'], queryFn: getPortfolioSignals, staleTime: 300_000 })
}

type Signal = {
  securities_id: number
  securities_name: string
  price_today: number | null
  price_today_date: string | null
  daily_chg_pct: number | null
  weekly_chg_pct: number | null
  monthly_chg_pct: number | null
  quarterly_chg_pct: number | null
  semiannual_chg_pct: number | null
  annual_chg_pct: number | null
  triannual_chg_pct: number | null
  ytd_chg_pct: number | null
  vol_1m_ann: number | null
  vol_3m_ann: number | null
  vol_1y_ann: number | null
  vol_ytd_ann: number | null
  quality_score: number | null
  sharpe_ratio: number | null
  current_value_eur: number | null
  unrealized_pnl_eur: number | null
  total_cost_eur: number | null
  wall_street_view: string | null
  target_price: number | null
  upside_pct: number | null
  fair_value: number | null
  fair_value_upside_pct: number | null
  high_3y: number | null
  low_3y: number | null
  pct_from_high_3y: number | null
  pct_from_low_3y: number | null
  recommendation_signal: string | null
  final_signal: string | null
  fwd_yield_pct: number | null
  current_qty: number | null
}

// ── Volatility Tab ────────────────────────────────────────────────────────────
function VolatilityTab() {
  const { data = [], isLoading } = usePortfolioSignals()
  const [volPeriod, setVolPeriod] = usePersist('vol_period', 'Annual Vol (ann)')
  const [volCount, setVolCount] = usePersist('vol_count', 10)
  const rows = data as Signal[]

  const VOL_MAP: Record<string, keyof Signal> = {
    'Monthly Vol (ann)':   'vol_1m_ann',
    'Quarterly Vol (ann)': 'vol_3m_ann',
    'Annual Vol (ann)':    'vol_1y_ann',
    'YTD Vol (ann)':       'vol_ytd_ann',
  }

  const col = VOL_MAP[volPeriod]
  const filtered = rows
    .filter(r => r[col] != null && Number(r[col]) > 0)
    .map(r => ({ id: r.securities_id, name: r.securities_name, vol: Number(r[col]) }))

  const highVol = [...filtered].sort((a, b) => b.vol - a.vol).slice(0, volCount)
  const lowVol  = [...filtered].sort((a, b) => a.vol - b.vol).slice(0, volCount)

  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>

  const VolTable = ({ items, label, style }: { items: typeof highVol; label: string; style: string }) => (
    <div>
      <div className={`rounded-lg px-4 py-2 mb-2 text-sm font-medium ${style}`}>{label}</div>
      <WithCopy>
        <table className="w-full text-sm">
          <thead><tr className="bg-slate-50 text-xs text-slate-500 uppercase tracking-wide">
            <th className="px-3 py-2 text-left">Security</th>
            <th className="px-3 py-2 text-right">Volatility %</th>
          </tr></thead>
          <tbody className="divide-y divide-slate-100">
            {items.map((r, i) => (
              <tr key={i} className="hover:bg-slate-50">
                <td className="px-3 py-2 font-medium"><SecLink id={r.id}>{r.name}</SecLink></td>
                <td className="px-3 py-2 text-right tabular-nums">{r.vol.toFixed(2)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </WithCopy>
    </div>
  )

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex flex-wrap gap-1.5">
          {Object.keys(VOL_MAP).map(p => (
            <button key={p} onClick={() => setVolPeriod(p)}
              className={`px-3 py-1.5 text-xs rounded border font-medium ${volPeriod === p ? 'bg-blue-600 text-white border-blue-600' : 'border-slate-300 text-slate-600 hover:bg-slate-50'}`}>
              {p}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-1.5 text-xs text-slate-500">
          Show top
          <input type="number" min={1} max={100} value={volCount}
            onChange={e => setVolCount(Math.max(1, parseInt(e.target.value) || 10))}
            className="w-16 border border-slate-300 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-blue-400" />
          securities
        </label>
      </div>
      {filtered.length === 0
        ? <p className="text-slate-400 text-sm py-8 text-center">No volatility data available.</p>
        : <div className="grid grid-cols-2 gap-6">
            <VolTable items={highVol} label="⚡ High Volatility" style="bg-amber-50 border border-amber-200 text-amber-800" />
            <VolTable items={lowVol}  label="🛡️ Low Volatility"  style="bg-blue-50 border border-blue-100 text-blue-800" />
          </div>
      }
    </div>
  )
}

// ── Investment Signals Tab ────────────────────────────────────────────────────
function InvestmentSignalsTab() {
  const { isDark } = useTheme()
  const { data = [], isLoading } = usePortfolioSignals()
  const [volCap, setVolCap] = usePersist('inv_sig_vol_cap', 95)
  const rows = data as Signal[]

  const plotRows = rows.filter(r => r.vol_1y_ann != null && r.annual_chg_pct != null && Number(r.vol_1y_ann) > 0)

  const vols = [...plotRows.map(r => Number(r.vol_1y_ann))].sort((a, b) => a - b)
  const capValue = volCap >= 100
    ? Infinity
    : Math.max(vols[Math.floor(vols.length * volCap / 100)] ?? 10, 10)

  const chartRows = volCap >= 100 ? plotRows : plotRows.filter(r => Number(r.vol_1y_ann) <= capValue)
  const hiddenNames = plotRows.filter(r => Number(r.vol_1y_ann) > capValue).map(r => r.securities_name)

  const topPicks = [...rows]
    .filter(r => r.sharpe_ratio != null)
    .sort((a, b) => Number(b.sharpe_ratio) - Number(a.sharpe_ratio))
    .slice(0, 20)

  const { sorted: sortedTopPicks, sortKey: tpSK, sortDir: tpSD, toggleSort: tpSort } = useSortTablePersisted(topPicks, 'investment-signals-sort', 'sharpe_ratio', 'desc')

  const sharpeValues = chartRows.map(r => r.sharpe_ratio ?? 0)
  const minSharpe = Math.min(...sharpeValues)
  const maxSharpe = Math.max(...sharpeValues)

  const sharpeColor = (v: number) => {
    if (maxSharpe === minSharpe) return '#94a3b8'
    const t = (v - minSharpe) / (maxSharpe - minSharpe)
    if (t < 0.5) {
      const r = Math.round(220 + (255 - 220) * (1 - t * 2))
      const g = Math.round(38 + (200 - 38) * (t * 2))
      return `rgb(${r},${g},38)`
    }
    const t2 = (t - 0.5) * 2
    const r2 = Math.round(255 - (255 - 34) * t2)
    const g2 = Math.round(200 - (200 - 197) * t2)
    return `rgb(${r2},${g2},${Math.round(38 + (94 - 38) * t2)})`
  }

  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-4">
        <div className="flex-1">
          <label className="text-xs font-medium text-slate-600 block mb-1">
            <Tooltip text="Securities above this volatility percentile are hidden from the scatter chart for readability. They always appear in the table below.">
              Volatility cap (percentile)
            </Tooltip>
            {' '}— <span className="text-blue-600 font-semibold">{volCap}%</span>
          </label>
          <input type="range" min={50} max={100} step={1} value={volCap}
            onChange={e => setVolCap(Number(e.target.value))}
            className="w-full accent-blue-600" />
        </div>
        {volCap < 100 && (
          <div className="text-right">
            <p className="text-xs text-slate-500">Cap at</p>
            <p className="text-sm font-bold text-blue-600">{capValue === Infinity ? '∞' : `${capValue.toFixed(0)}%`}</p>
          </div>
        )}
      </div>

      {hiddenNames.length > 0 && (
        <p className="text-xs text-slate-400">
          ℹ️ {hiddenNames.length} securit{hiddenNames.length === 1 ? 'y' : 'ies'} with volatility &gt; {capValue.toFixed(0)}% hidden from chart ({hiddenNames.join(', ')}). They appear in the table below.
        </p>
      )}

      {/* Risk vs Reward scatter */}
      {chartRows.length > 0 && (
        <div>
          <p className="text-sm font-semibold text-slate-700 mb-1">Risk vs. Reward Matrix</p>
          <Plot
            data={[{
              type: 'scatter',
              mode: 'markers',
              x: chartRows.map(r => r.vol_1y_ann),
              y: chartRows.map(r => r.annual_chg_pct),
              text: chartRows.map(r => r.securities_name),
              hovertemplate: '<b>%{text}</b><br>Vol: %{x:.1f}%<br>Return: %{y:.1f}%<extra></extra>',
              marker: {
                size: chartRows.map(r => Math.max((r.quality_score ?? 0) + 5, 5)),
                color: chartRows.map(r => r.sharpe_ratio ?? 0),
                colorscale: [
                  [0, '#ef4444'], [0.25, '#f97316'], [0.5, '#eab308'],
                  [0.75, '#22c55e'], [1, '#16a34a'],
                ],
                colorbar: { title: 'Sharpe', thickness: 12, len: 0.6 },
                showscale: true,
                line: { width: 0.5, color: '#ffffff' },
              },
            }]}
            layout={{
              height: 420,
              margin: { t: 20, r: 80, b: 60, l: 70 },
              xaxis: { title: 'Annual Volatility (%)' },
              yaxis: { title: 'Annual Return (%)' },
              shapes: [
                { type: 'line', x0: 0, x1: 1, xref: 'paper', y0: 0, y1: 0, line: { color: '#94a3b8', dash: 'dash', width: 1 } },
                { type: 'line', x0: chartRows.reduce((s, r) => s + Number(r.vol_1y_ann ?? 0), 0) / chartRows.length, x1: chartRows.reduce((s, r) => s + Number(r.vol_1y_ann ?? 0), 0) / chartRows.length, y0: 0, y1: 1, yref: 'paper', line: { color: '#94a3b8', dash: 'dash', width: 1 } },
              ],
              ...plotLayout(isDark), hovermode: 'closest',
            }}
            config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }} />
        </div>
      )}

      {/* Top efficiency picks table */}
      <div>
        <p className="text-sm font-semibold text-slate-700 mb-2">🏆 Top Efficiency Picks (High Sharpe Ratio)</p>
        <WithCopy>
          <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-300px)]">
            <table className="w-full text-sm">
              <thead className="sticky top-0 z-10"><tr className="bg-slate-50 text-xs text-slate-500 uppercase tracking-wide">
                <ColHeader label={<Tooltip text="Security name.">Security</Tooltip>} sortKey="securities_name" currentKey={tpSK} currentDir={tpSD} onSort={tpSort} align="left" className="text-xs text-slate-500 uppercase tracking-wide" />
                <ColHeader label={<Tooltip text="Annual price return over the last 12 months.">Return 1Y</Tooltip>} sortKey="annual_chg_pct" currentKey={tpSK} currentDir={tpSD} onSort={tpSort} align="right" className="text-xs text-slate-500 uppercase tracking-wide" />
                <ColHeader label={<Tooltip text="Annualised volatility over the last 12 months.">Vol 1Y</Tooltip>} sortKey="vol_1y_ann" currentKey={tpSK} currentDir={tpSD} onSort={tpSort} align="right" className="text-xs text-slate-500 uppercase tracking-wide" />
                <ColHeader label={<Tooltip text="Excess return over risk-free rate divided by volatility. Higher is better.">Sharpe</Tooltip>} sortKey="sharpe_ratio" currentKey={tpSK} currentDir={tpSD} onSort={tpSort} align="right" className="text-xs text-slate-500 uppercase tracking-wide" />
                <ColHeader label={<Tooltip text="Composite momentum score: 50% 1M + 30% 3M + 20% 1Y return.">Quality Score</Tooltip>} sortKey="quality_score" currentKey={tpSK} currentDir={tpSD} onSort={tpSort} align="right" className="text-xs text-slate-500 uppercase tracking-wide" />
              </tr></thead>
              <tbody className="divide-y divide-slate-100">
                {sortedTopPicks.map((r, i) => (
                  <tr key={i} className="hover:bg-slate-50">
                    <td className="px-3 py-2 font-medium"><SecLink id={r.securities_id}>{String(r.securities_name)}</SecLink></td>
                    <td className={`px-3 py-2 text-right tabular-nums ${Number(r.annual_chg_pct ?? 0) >= 0 ? 'text-green-700' : 'text-red-600'}`}>
                      {r.annual_chg_pct != null ? `${Number(r.annual_chg_pct) >= 0 ? '+' : ''}${Number(r.annual_chg_pct).toFixed(2)}%` : '—'}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-amber-600">
                      {r.vol_1y_ann != null ? `${Number(r.vol_1y_ann).toFixed(2)}%` : '—'}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums font-semibold" style={{ color: sharpeColor(Number(r.sharpe_ratio ?? 0)) }}>
                      {r.sharpe_ratio != null ? Number(r.sharpe_ratio).toFixed(2) : '—'}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-slate-600">
                      {r.quality_score != null ? Number(r.quality_score).toFixed(2) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </WithCopy>
      </div>
    </div>
  )
}

// A fresh object literal every render (the usual inline defaultColDef={{...}} pattern)
// makes ag-Grid treat column config as "changed" on any re-render, which normally goes
// unnoticed since nothing re-renders this component while a filter popup is open — but
// this grid's own filter-change handler now saves the filter server-side on every
// keystroke, re-rendering the component mid-typing and closing the open popup as a
// result. A stable reference sidesteps that entirely.
const PORTFOLIO_SIGNALS_DEFAULT_COL_DEF = { resizable: true, sortable: true, filter: true }

// ── Portfolio Action Signals Tab ──────────────────────────────────────────────
function PortfolioActionSignalsTab() {
  const navigate = useNavigate()
  const { data = [], isLoading } = usePortfolioSignals()
  const { data: securities = [] } = useQuery({ queryKey: ['securities'], queryFn: () => getSecurities() })
  // All-time realized P&L per security, same FIFO-based figure Security Detail's
  // Overview/Investment Transactions tabs show, summed across every account.
  const { data: pnlData = [] } = useQuery({ queryKey: ['pnl-all-time'], queryFn: () => getPnl(), staleTime: 300_000 })
  const [view, setView] = usePersist<'all' | 'open_only'>('sig_view', 'all')

  const secById = useMemo(() => {
    const m = new Map<number, Row>()
    for (const s of securities as Row[]) m.set(Number(s.id), s)
    return m
  }, [securities])

  const realizedById = useMemo(() => {
    const m = new Map<number, number>()
    for (const r of pnlData as Row[]) {
      const sid = r.securities_id != null ? Number(r.securities_id) : null
      if (sid == null) continue
      m.set(sid, (m.get(sid) ?? 0) + Number(r.realized_pnl_eur ?? 0))
    }
    return m
  }, [pnlData])

  // Merges in every Security Detail Overview field not already covered by the
  // signal query itself — Security Details (symbol/type/industry/currency/exchange),
  // My Holdings (shares held, cost basis, avg cost/share, realized P&L), and Quote
  // (open/prev close/high/low/52-week range/volume/avg volume/P-E/market cap/ann
  // div per share/ex-div date) — reusing the same getSecurities()/getPnl() data
  // Security Detail's own Overview tab is built from, rather than duplicating that
  // logic in the portfolio-signals SQL query.
  const rows = useMemo(() => (data as Signal[]).map(r => {
    const sec = secById.get(r.securities_id)
    const qty = r.current_qty != null ? Number(r.current_qty) : null
    const costBasis = r.total_cost_eur != null ? Number(r.total_cost_eur) : null
    const prevClose = sec?.prev_close != null ? Number(sec.prev_close) : null
    const change = r.price_today != null && prevClose != null ? Number(r.price_today) - prevClose : null
    return {
      ...r,
      unrealized_pnl_pct: r.unrealized_pnl_eur != null && costBasis != null && costBasis > 0
        ? Number(r.unrealized_pnl_eur) / costBasis * 100
        : null,
      ticker: sec?.ticker ?? null,
      sec_type: sec?.type ?? null,
      industry: sec?.industry ?? null,
      currency: sec?.currency ?? null,
      exchange: sec?.tv_exchange ?? null,
      shares_held: qty,
      cost_basis_eur: costBasis,
      avg_cost_per_share_eur: qty ? (costBasis ?? 0) / qty : null,
      realized_pnl_eur: realizedById.get(r.securities_id) ?? null,
      change,
      pct_change: change != null && prevClose ? (change / prevClose) * 100 : null,
      day_open: sec?.day_open != null ? Number(sec.day_open) : null,
      prev_close: prevClose,
      day_high: sec?.day_high != null ? Number(sec.day_high) : null,
      day_low: sec?.day_low != null ? Number(sec.day_low) : null,
      week52_high: sec?.week52_high != null ? Number(sec.week52_high) : null,
      week52_low: sec?.week52_low != null ? Number(sec.week52_low) : null,
      volume: sec?.volume != null ? Number(sec.volume) : null,
      avg_volume: sec?.avg_volume != null ? Number(sec.avg_volume) : null,
      trailing_pe: sec?.trailing_pe != null ? Number(sec.trailing_pe) : null,
      market_cap: sec?.market_cap != null ? Number(sec.market_cap) : null,
      dividend_rate: sec?.dividend_rate != null ? Number(sec.dividend_rate) : null,
      ex_dividend_date: sec?.ex_dividend_date ?? null,
    }
  }), [data, secById, realizedById])

  const filtered = useMemo(() => rows.filter(r => {
    if (view === 'open_only') return Number(r.current_value_eur ?? 0) > 0
    return true
  }), [rows, view])

  const [search, setSearch] = usePersist('sig_search', '')

  const signalStyle = (sig: string | null): string => {
    if (!sig) return ''
    const v = sig.toUpperCase()
    if (v.includes('CONVICTION SELL') || v.includes('UNDERPERFORM')) return 'text-red-900 font-bold'
    if (v.includes('SELL') || v.includes('CAUTION'))                  return 'text-red-600 font-bold'
    if (v.includes('HIGH CONVICTION BUY'))                            return 'text-green-900 font-bold'
    if (v.includes('STRONG') || v.includes('CONVICTION BUY'))        return 'text-green-700 font-bold'
    if (v.includes('BUY') || v.includes('UPGRADE'))                  return 'text-green-600 font-semibold'
    if (v.includes('CONTRARIAN'))                                     return 'text-orange-600 font-semibold'
    return 'text-slate-500'
  }

  const analystBadge = (v: string | null) => {
    if (!v) return null
    const color = v === 'strong_buy' ? 'bg-green-100 text-green-800'
      : v === 'buy' ? 'bg-emerald-50 text-emerald-700'
      : v === 'hold' ? 'bg-yellow-50 text-yellow-700'
      : v === 'sell' ? 'bg-red-50 text-red-700'
      : v === 'underperform' ? 'bg-red-100 text-red-900'
      : 'bg-slate-100 text-slate-600'
    return <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${color}`}>{v.replace('_', ' ')}</span>
  }

  const pnlCellClass = (p: { value: unknown }) => p.value != null && Number(p.value) < 0 ? 'text-red-600' : p.value != null ? 'text-green-700' : ''
  const pctFmt = (v: unknown) => v != null ? `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(2)}%` : '—'
  const numFmt = (dec = 4) => (p: { value: unknown }) => p.value != null ? fmtNum(Number(p.value), dec) : '—'
  const eurFmt = (p: { value: unknown }) => p.value != null ? fmtEur(Number(p.value)) : '—'

  // Stable reference matters — see MarketData.tsx's identical colDefs comment:
  // onColumnResized/onColumnMoved persist column state and re-render this component,
  // and a fresh array literal every render would make ag-Grid treat columnDefs as
  // "changed" and reset it back to these defaults, undoing the user's own resize/reorder.
  const colDefs = useMemo(() => {
    const cols: ColDef[] = [
      { field: 'securities_name', headerName: 'Security', pinned: 'left', minWidth: 200, flex: 2,
        cellRenderer: (p: { value: string; data: Row }) => (
          <button onClick={() => navigate(`/securities/${p.data.securities_id}`)} className="text-blue-600 hover:underline text-left truncate w-full">{p.value}</button>
        ) },
      { field: 'ticker', headerName: 'Symbol', width: 90, cellStyle: { fontFamily: 'monospace' } },
      { field: 'final_signal', headerName: 'Final Signal', width: 160, sort: 'asc' as const,
        headerTooltip: 'Combined signal: math signal + analyst rating. Conviction signals appear when both agree.',
        cellClass: (p: { value: string | null }) => `text-xs font-semibold ${signalStyle(p.value)}` },
      { field: 'recommendation_signal', headerName: 'Math Signal', width: 140,
        headerTooltip: 'Quantitative signal derived from Sharpe ratio and quality score.',
        cellClass: (p: { value: string | null }) => `text-xs font-semibold ${signalStyle(p.value)}` },
      { field: 'wall_street_view', headerName: 'Analyst View', width: 130,
        headerTooltip: 'Wall Street analyst consensus rating.',
        cellRenderer: (p: { value: string | null }) => analystBadge(p.value) },
      { field: 'current_value_eur', headerName: 'Value (€)', type: 'numericColumn', filter: 'agNumberColumnFilter', width: 110,
        headerTooltip: 'Current market value of the position in EUR.', valueFormatter: eurFmt },
      { field: 'unrealized_pnl_eur', headerName: 'Unreal. P&L', type: 'numericColumn', filter: 'agNumberColumnFilter', width: 110,
        headerTooltip: 'Unrealized P&L: market value minus FIFO cost basis.', valueFormatter: eurFmt, cellClass: pnlCellClass },
      { field: 'unrealized_pnl_pct', headerName: 'P&L %', type: 'numericColumn', filter: 'agNumberColumnFilter', width: 90,
        headerTooltip: 'Unrealized P&L as % of cost basis.', valueFormatter: p => pctFmt(p.value), cellClass: pnlCellClass },
      { field: 'realized_pnl_eur', headerName: 'Realized P&L', type: 'numericColumn', filter: 'agNumberColumnFilter', width: 120,
        headerTooltip: 'All-time realized P&L (FIFO), summed across every account this security has ever been held in.',
        valueFormatter: eurFmt, cellClass: pnlCellClass },
      { field: 'shares_held', headerName: 'Shares Held', type: 'numericColumn', filter: 'agNumberColumnFilter', width: 100,
        headerTooltip: 'Current quantity held across all accounts.', valueFormatter: numFmt(4) },
      { field: 'cost_basis_eur', headerName: 'Cost Basis (€)', type: 'numericColumn', filter: 'agNumberColumnFilter', width: 110,
        headerTooltip: 'FIFO cost basis in EUR.', valueFormatter: eurFmt },
      { field: 'avg_cost_per_share_eur', headerName: 'Avg Cost/Share (€)', type: 'numericColumn', filter: 'agNumberColumnFilter', width: 130, hide: true,
        headerTooltip: 'Cost basis divided by shares held, in EUR.', valueFormatter: numFmt(4) },
      { field: 'change', headerName: 'Change', type: 'numericColumn', filter: 'agNumberColumnFilter', width: 100, hide: true,
        headerTooltip: 'Price change vs previous close, in the security’s own currency.', valueFormatter: numFmt(4), cellClass: pnlCellClass },
      { field: 'pct_change', headerName: '% Change', type: 'numericColumn', filter: 'agNumberColumnFilter', width: 100, hide: true,
        headerTooltip: 'Price change vs previous close, as a percentage.', valueFormatter: p => pctFmt(p.value), cellClass: pnlCellClass },
      { field: 'fwd_yield_pct', headerName: 'Fwd Yield %', type: 'numericColumn', filter: 'agNumberColumnFilter', width: 110,
        headerTooltip: 'Forward dividend yield based on analyst estimates.',
        valueFormatter: p => p.value != null && Number(p.value) > 0 ? `${Number(p.value).toFixed(2)}%` : '—', cellClass: () => 'text-blue-700' },
      { field: 'sharpe_ratio', headerName: 'Sharpe', type: 'numericColumn', filter: 'agNumberColumnFilter', width: 90,
        headerTooltip: 'Sharpe ratio: excess return divided by annual volatility.',
        valueFormatter: p => p.value != null ? Number(p.value).toFixed(2) : '—',
        cellClass: (p: { value: unknown }) => `font-semibold ${Number(p.value ?? 0) >= 1 ? 'text-green-700' : Number(p.value ?? 0) < 0 ? 'text-red-600' : 'text-slate-600'}` },
      { field: 'quality_score', headerName: 'Quality', type: 'numericColumn', filter: 'agNumberColumnFilter', width: 90,
        headerTooltip: 'Quality score: composite momentum (50% 1M + 30% 3M + 20% 1Y return).',
        valueFormatter: p => p.value != null ? Number(p.value).toFixed(2) : '—' },
      { field: 'vol_1y_ann', headerName: 'Volatility (1Y)', type: 'numericColumn', filter: 'agNumberColumnFilter', width: 120,
        headerTooltip: 'Annualized volatility over the last 1 year.',
        valueFormatter: p => p.value != null ? `${Number(p.value).toFixed(2)}%` : '—' },
      { field: 'vol_1m_ann', headerName: 'Volatility (1M)', type: 'numericColumn', filter: 'agNumberColumnFilter', width: 120, hide: true,
        headerTooltip: 'Annualized volatility over the last 1 month.',
        valueFormatter: p => p.value != null ? `${Number(p.value).toFixed(2)}%` : '—' },
      { field: 'vol_3m_ann', headerName: 'Volatility (3M)', type: 'numericColumn', filter: 'agNumberColumnFilter', width: 120, hide: true,
        headerTooltip: 'Annualized volatility over the last 3 months.',
        valueFormatter: p => p.value != null ? `${Number(p.value).toFixed(2)}%` : '—' },
      { field: 'vol_ytd_ann', headerName: 'Volatility (YTD)', type: 'numericColumn', filter: 'agNumberColumnFilter', width: 120, hide: true,
        headerTooltip: 'Annualized volatility year-to-date.',
        valueFormatter: p => p.value != null ? `${Number(p.value).toFixed(2)}%` : '—' },
      { field: 'price_today', headerName: 'Price', type: 'numericColumn', filter: 'agNumberColumnFilter', width: 100,
        headerTooltip: 'Most recent available market price, in the security’s own currency.', valueFormatter: numFmt(4) },
      { field: 'day_open', headerName: 'Open', type: 'numericColumn', filter: 'agNumberColumnFilter', width: 100, hide: true, valueFormatter: numFmt(4) },
      { field: 'prev_close', headerName: 'Prev Close', type: 'numericColumn', filter: 'agNumberColumnFilter', width: 100, hide: true, valueFormatter: numFmt(4) },
      { field: 'day_high', headerName: 'Day High', type: 'numericColumn', filter: 'agNumberColumnFilter', width: 100, hide: true, valueFormatter: numFmt(4) },
      { field: 'day_low', headerName: 'Day Low', type: 'numericColumn', filter: 'agNumberColumnFilter', width: 100, hide: true, valueFormatter: numFmt(4) },
      { field: 'high_3y', headerName: '3Y High', type: 'numericColumn', filter: 'agNumberColumnFilter', width: 100,
        headerTooltip: 'Highest price in the last 3 years (post-split adjusted).', valueFormatter: numFmt(4) },
      { field: 'pct_from_high_3y', headerName: '% from High', type: 'numericColumn', filter: 'agNumberColumnFilter', width: 110,
        headerTooltip: 'Current price vs 3-year high as a percentage.', valueFormatter: p => pctFmt(p.value),
        cellClass: (p: { value: unknown }) => Number(p.value ?? 0) >= 0 ? 'text-green-700' : 'text-red-600' },
      { field: 'low_3y', headerName: '3Y Low', type: 'numericColumn', filter: 'agNumberColumnFilter', width: 100,
        headerTooltip: 'Lowest price in the last 3 years (post-split adjusted).', valueFormatter: numFmt(4) },
      { field: 'pct_from_low_3y', headerName: '% from Low', type: 'numericColumn', filter: 'agNumberColumnFilter', width: 110,
        headerTooltip: 'Current price vs 3-year low as a percentage.', valueFormatter: p => pctFmt(p.value),
        cellClass: (p: { value: unknown }) => Number(p.value ?? 0) >= 0 ? 'text-green-700' : 'text-red-600' },
      { field: 'week52_high', headerName: '52-Week High', type: 'numericColumn', filter: 'agNumberColumnFilter', width: 120, hide: true, valueFormatter: numFmt(4) },
      { field: 'week52_low', headerName: '52-Week Low', type: 'numericColumn', filter: 'agNumberColumnFilter', width: 120, hide: true, valueFormatter: numFmt(4) },
      { field: 'volume', headerName: 'Volume', type: 'numericColumn', filter: 'agNumberColumnFilter', width: 110, hide: true,
        valueFormatter: p => p.value != null ? Number(p.value).toLocaleString() : '—' },
      { field: 'avg_volume', headerName: 'Avg Vol', type: 'numericColumn', filter: 'agNumberColumnFilter', width: 110, hide: true,
        valueFormatter: p => p.value != null ? Number(p.value).toLocaleString() : '—' },
      { field: 'trailing_pe', headerName: 'P/E', type: 'numericColumn', filter: 'agNumberColumnFilter', width: 90, hide: true, valueFormatter: numFmt(2) },
      { field: 'market_cap', headerName: 'Market Cap', type: 'numericColumn', filter: 'agNumberColumnFilter', width: 140, hide: true,
        valueFormatter: p => p.value != null ? Number(p.value).toLocaleString() : '—' },
      { field: 'dividend_rate', headerName: 'Ann Div/Shr', type: 'numericColumn', filter: 'agNumberColumnFilter', width: 110, hide: true, valueFormatter: numFmt(4) },
      { field: 'ex_dividend_date', headerName: 'Ex-Div Date', width: 110, hide: true,
        valueFormatter: p => typeof p.value === 'string' ? p.value.slice(0, 10) : '—' },
      { field: 'sec_type', headerName: 'Type', width: 100, hide: true },
      { field: 'industry', headerName: 'Industry', width: 160, hide: true },
      { field: 'currency', headerName: 'Currency', width: 90, hide: true },
      { field: 'exchange', headerName: 'Exchange', width: 100, hide: true },
      { field: 'upside_pct', headerName: 'Upside %', type: 'numericColumn', filter: 'agNumberColumnFilter', width: 100,
        headerTooltip: 'Analyst target price vs current price — expected upside.', valueFormatter: p => pctFmt(p.value),
        cellClass: (p: { value: unknown }) => `font-semibold ${Number(p.value ?? 0) >= 0 ? 'text-green-700' : 'text-red-600'}` },
      { field: 'target_price', headerName: 'Target', type: 'numericColumn', filter: 'agNumberColumnFilter', width: 100,
        headerTooltip: 'Analyst consensus target price.', valueFormatter: p => p.value != null ? Number(p.value).toFixed(2) : '—' },
      { field: 'fair_value_upside_pct', headerName: 'vs Fair Value %', type: 'numericColumn', filter: 'agNumberColumnFilter', width: 130,
        headerTooltip: "Oikos's own fair-value estimate vs current price — this security's own historical median P/E times its current normalized EPS. Approximates the idea behind services like GuruFocus's GF Value, not a reproduction of their actual (undisclosed) formula. Blank for bonds/ETFs/crypto or loss-making companies.",
        valueFormatter: p => pctFmt(p.value),
        cellClass: (p: { value: unknown }) => `font-semibold ${Number(p.value ?? 0) >= 0 ? 'text-green-700' : 'text-red-600'}` },
      { field: 'fair_value', headerName: 'Fair Value (Est.)', type: 'numericColumn', filter: 'agNumberColumnFilter', width: 130,
        headerTooltip: "Oikos's own fair-value estimate: historical median P/E × current normalized EPS. See vs Fair Value % for the full explanation.",
        valueFormatter: p => p.value != null ? Number(p.value).toFixed(2) : '—' },
    ]
    return cols
  }, [navigate]) // eslint-disable-line react-hooks/exhaustive-deps
  const gridCols = useGridColumnState('portfolio-action-signals', colDefs)
  const gridFilter = useGridFilterState('portfolio-action-signals')
  const { gridApi, onGridReady } = useGridApi(api => {
    if (gridFilter.filterModel) api.setFilterModel(gridFilter.filterModel)
  })

  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>

  return (
    <div className="space-y-4">
      {/* Filter + Search */}
      <div className="flex flex-wrap items-center gap-1.5">
        {([
          ['all',       'Show All'],
          ['open_only', 'Open Positions Only'],
        ] as const).map(([v, label]) => (
          <button key={v} onClick={() => setView(v)}
            className={`px-3 py-1.5 text-xs rounded border font-medium ${view === v ? 'bg-blue-600 text-white border-blue-600' : 'border-slate-300 text-slate-600 hover:bg-slate-50'}`}>
            {label}
          </button>
        ))}
        <input
          type="text"
          placeholder="Search…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="px-2.5 py-1.5 text-xs border border-slate-300 rounded w-44 focus:outline-none focus:border-blue-400"
        />
        {gridFilter.hasFilters && (
          <button onClick={() => gridFilter.clearFilters(gridApi)}
            className="px-3 py-1.5 text-xs rounded border font-medium border-slate-300 text-slate-600 hover:bg-slate-50">
            ✕ Clear Filters
          </button>
        )}
        <ColumnsMenu columns={gridCols.columns} onToggle={gridCols.toggleColumn} />
        <CopyToExcelButton gridApi={gridApi} />
      </div>

      <div className="ag-theme-alpine" style={{ height: 'calc(100vh - 300px)', width: '100%' }}>
        <AgGridReact
          theme="legacy"
          onGridReady={onGridReady}
          rowData={filtered}
          columnDefs={gridCols.colDefs}
          defaultColDef={PORTFOLIO_SIGNALS_DEFAULT_COL_DEF}
          columnTypes={AG_GRID_COLUMN_TYPES}
          quickFilterText={search}
          onColumnMoved={gridCols.onColumnMoved}
          onColumnResized={gridCols.onColumnResized}
          onSortChanged={gridCols.onSortChanged}
          onFilterChanged={gridFilter.onFilterChanged}
          getRowClass={(p: { data?: Row }) => Number(p.data?.current_value_eur ?? 0) === 0 ? 'opacity-60' : ''}
        />
      </div>
    </div>
  )
}

// ── Securities Section ────────────────────────────────────────────────────────
function SecuritiesSection() {
  const [tab, setTab] = usePersist('sec_tab', 'Price Changes')
  const TABS = ['Price Changes', 'Volatility', 'Investment Signals', 'Portfolio Action Signals']
  return (
    <div>
      <SubTabs tabs={TABS} active={tab} onChange={setTab} />
      {tab === 'Price Changes'             && <PriceChangesTab />}
      {tab === 'Volatility'                && <VolatilityTab />}
      {tab === 'Investment Signals'        && <InvestmentSignalsTab />}
      {tab === 'Portfolio Action Signals'  && <PortfolioActionSignalsTab />}
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════════
// 5. INCOME & EXPENSE
// ════════════════════════════════════════════════════════════════════════════

const DEFAULT_CASH_TYPES = ['Cash', 'Checking', 'Savings', 'Credit Card', 'Loan', 'Real Estate', 'Vehicle', 'Asset', 'Liability', 'Other']
const DEFAULT_INV_TYPES = ['Brokerage', 'Other Investment', 'Margin']
const ALL_ACCOUNT_TYPES = ['Cash', 'Checking', 'Savings', 'Credit Card', 'Brokerage', 'Pension', 'Other Investment', 'Margin', 'Loan', 'Real Estate', 'Vehicle', 'Asset', 'Liability', 'Other']
const REPORT_TYPES = ['Total Summary', 'Income Analysis', 'Expense Analysis', 'Tax Analysis', 'Dividend Analysis', 'Interest Analysis'] as const
type ReportType = typeof REPORT_TYPES[number]
const PERIOD_TYPES = ['Monthly', 'Quarterly', 'Yearly'] as const
type PeriodType = typeof PERIOD_TYPES[number]

const TYPE_COLORS: Record<string, string> = {
  Income: '#27AE60', Dividend: '#1ABC9C', Interest: '#2980B9', Expense: '#E74C3C', Tax: '#8E44AD',
}
const INCOME_TYPES = ['Income', 'Dividend', 'Interest']
const EXPENSE_TYPES = ['Expense', 'Tax']

function catTypeForReport(rt: ReportType): string | null {
  if (rt === 'Income Analysis') return 'Income'
  if (rt === 'Expense Analysis') return 'Expense'
  if (rt === 'Tax Analysis') return 'Tax'
  if (rt === 'Dividend Analysis') return 'Dividend'
  if (rt === 'Interest Analysis') return 'Interest'
  return null
}

function getPeriodKey(dateStr: string, pt: PeriodType): string {
  const d = new Date(dateStr)
  if (pt === 'Monthly') return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
  if (pt === 'Quarterly') return `${d.getFullYear()}-Q${Math.floor(d.getMonth() / 3) + 1}`
  return String(d.getFullYear())
}

function IEMultiSelect({ label, options, value, onChange }: {
  label: string; options: string[]; value: string[]; onChange: (v: string[]) => void
}) {
  const [open, setOpen] = useState(false)
  return (
    <div className="relative">
      <button onClick={() => setOpen(o => !o)}
        className="flex items-center gap-1 px-3 py-1.5 text-xs border border-slate-300 rounded bg-white hover:bg-slate-50 min-w-[160px] justify-between">
        <span className="text-slate-600 truncate">{label}: {value.length} selected</span>
        <span className="text-slate-400">{open ? '▲' : '▼'}</span>
      </button>
      {open && (
        <div className="absolute top-full left-0 mt-1 z-30 bg-white border border-slate-200 rounded shadow-lg p-2 min-w-[200px] max-h-64 overflow-y-auto">
          {options.map(opt => (
            <label key={opt} className="flex items-center gap-2 px-1 py-0.5 text-xs cursor-pointer hover:bg-slate-50 rounded">
              <input type="checkbox" checked={value.includes(opt)}
                onChange={e => onChange(e.target.checked ? [...value, opt] : value.filter(v => v !== opt))} />
              {opt}
            </label>
          ))}
        </div>
      )}
    </div>
  )
}

type IEDrillCell = { category: string; period: string } | null

type CatTreeRow = {
  path: string
  name: string
  depth: number
  cat_type: string
  periods: Record<string, number>
  total: number
  direct: number
  hasChildren: boolean
}

type CatTree = { roots: string[]; nodes: Record<string, CatTreeRow>; childrenOf: Record<string, string[]> }

// Builds a rollup tree from flat "A : B : C" category paths — each ancestor level
// accumulates the totals of every descendant so a parent row (e.g. "Vacation")
// shows the sum of all its subcategories, not just amounts posted directly to it.
function buildCategoryTree(pivotMap: { category: string; cat_type: string; periods: Record<string, number>; total: number }[]): CatTree {
  const nodes: Record<string, CatTreeRow> = {}
  const childrenOf: Record<string, string[]> = {}
  const rootSet = new Set<string>()

  for (const r of pivotMap) {
    const segs = r.category.split(' : ')
    let path = ''
    for (let d = 0; d < segs.length; d++) {
      const parentPath = path
      path = path ? `${path} : ${segs[d]}` : segs[d]
      if (!nodes[path]) {
        nodes[path] = { path, name: segs[d], depth: d, cat_type: r.cat_type, periods: {}, total: 0, direct: 0, hasChildren: false }
        if (d === 0) rootSet.add(path)
        else {
          if (!childrenOf[parentPath]) childrenOf[parentPath] = []
          if (!childrenOf[parentPath].includes(path)) childrenOf[parentPath].push(path)
          nodes[parentPath].hasChildren = true
        }
      }
      for (const [pk, amt] of Object.entries(r.periods)) nodes[path].periods[pk] = (nodes[path].periods[pk] ?? 0) + amt
      nodes[path].total += r.total
    }
    nodes[path].direct += r.total
  }
  return { roots: [...rootSet], nodes, childrenOf }
}

// A category cell matches either the exact leaf path, or is a descendant of a
// selected parent path (so drilling into a rollup row shows all its subcategories' transactions).
function categoryMatches(fullPath: string, selected: string): boolean {
  return fullPath === selected || fullPath.startsWith(selected + ' : ')
}

function IncomeExpenseSection({ startDate: _outerStart, endDate: _outerEnd }: { startDate: string; endDate: string }) {
  const { isDark } = useTheme()
  const qc = useQueryClient()
  const today = new Date().toISOString().slice(0, 10)
  const ytdStart = `${new Date().getFullYear()}-01-01`
  // YTD mode (default on, like Net Worth's) recomputes start/end fresh on every render
  // instead of trusting the persisted dates below, which would otherwise go stale — "today"
  // saved from a week ago is no longer today. Turning it off falls back to those persisted
  // dates, so a deliberately-picked custom range still survives a reload.
  const [ytdMode, setYtdMode] = usePersist('ie_ytd_mode', true)
  const [startDate, setStartDate] = usePersist('ie_start_date', ytdStart)
  const [endDate, setEndDate] = usePersist('ie_end_date', today)
  const effStart = ytdMode ? ytdStart : startDate
  const effEnd   = ytdMode ? today    : endDate
  const [reportType, setReportType] = usePersist<ReportType>('ie_report_type', 'Total Summary')
  const [periodType, setPeriodType] = usePersist<PeriodType>('ie_period_type', 'Monthly')
  const [cashTypes, setCashTypes] = useState<string[]>(DEFAULT_CASH_TYPES)
  const [invTypes, setInvTypes] = useState<string[]>(DEFAULT_INV_TYPES)
  const [topN, setTopN] = useState(10)
  const [ieTab, setIeTab] = usePersist('ie_tab', 'Chart')
  const [drillCat, setDrillCat] = useState<string>('All Categories')
  const [drillPayee, setDrillPayee] = useState<string>('All Payees')

  // Committed params — query only runs when user clicks "Update"
  const [qStart, setQStart] = useState(effStart)
  const [qEnd, setQEnd] = useState(effEnd)
  const [qCash, setQCash] = useState<string[]>(cashTypes)
  const [qInv, setQInv] = useState<string[]>(invTypes)
  const isDirty = effStart !== qStart || effEnd !== qEnd || cashTypes.join(',') !== qCash.join(',') || invTypes.join(',') !== qInv.join(',')
  const commitParams = () => { setQStart(effStart); setQEnd(effEnd); setQCash([...cashTypes]); setQInv([...invTypes]) }

  // Drill-down state
  const [drillCell, setDrillCell] = useState<IEDrillCell>(null)

  // TxModal state (reuses Cash Register modal)
  const [modalOpen, setModalOpen] = useState(false)
  const [modalForm, setModalForm] = useState<TxForm | null>(null)
  const [modalSplits, setModalSplits] = useState<SplitRow[]>([])
  const [modalUseSplits, setModalUseSplits] = useState(false)
  const [modalSaving, setModalSaving] = useState(false)
  const [modalError, setModalError] = useState<string | null>(null)
  const recurring = useNoOpRecurring()

  const { data: categoriesRaw = [] } = useQuery({ queryKey: ['categories'], queryFn: () => getCategories() })
  const { data: payeesRaw = [] } = useQuery({ queryKey: ['payees'], queryFn: () => getPayees() })
  const { data: accountsRaw = [] } = useQuery({ queryKey: ['accounts'], queryFn: () => getAccounts() })
  const categories = categoriesRaw as Record<string, unknown>[]
  const payees = payeesRaw as Record<string, unknown>[]
  const accounts = accountsRaw as Record<string, unknown>[]

  const openEdit = async (r: Row) => {
    if (!r.transaction_id) return
    const txId = Number(r.transaction_id)
    type ApiTx = { id: number; accounts_id: number; date: string; description: string | null; total_amount: number; payees_id: number | null; is_draft: boolean; cleared: boolean; reconciled: boolean; transfer_account_id: number | null }
    type ApiSplit = { id: number; categories_id: number | null; category: string; amount: number; memo: string | null }
    const [tx, txSplits] = await Promise.all([
      getTransactionById(txId) as Promise<ApiTx>,
      getSplits(txId) as Promise<ApiSplit[]>,
    ])
    const loadedSplits: SplitRow[] = txSplits.length > 0
      ? txSplits.map(s => ({
          categories_id: s.categories_id != null ? String(s.categories_id) : '',
          amount: String(s.amount),
          memo: s.memo ?? '',
        }))
      : [{ categories_id: '', amount: '0', memo: '' }]
    const splitsTotal = txSplits.reduce((sum, s) => sum + (s.amount || 0), 0)
    setModalForm({
      id: txId,
      accounts_id: tx.accounts_id,
      date: String(tx.date ?? '').slice(0, 10),
      description: tx.description ?? '',
      total_amount: String(tx.total_amount ?? splitsTotal),
      payees_id: tx.payees_id != null ? String(tx.payees_id) : '',
      categories_id: loadedSplits[0]?.categories_id ?? '',
      memo: loadedSplits[0]?.memo ?? '',
      is_draft: Boolean(tx.is_draft),
      cleared: Boolean(tx.cleared),
      reconciled: Boolean(tx.reconciled),
      is_transfer: tx.transfer_account_id != null,
      transfer_account_id: tx.transfer_account_id != null ? String(tx.transfer_account_id) : '',
    })
    setModalSplits(loadedSplits)
    setModalUseSplits(loadedSplits.length > 1)
    setModalError(null)
    setModalOpen(true)
  }

  const handleModalSave = async () => {
    if (!modalForm?.id) return

    // Only transfers move money without a spending/income category — everything
    // else must be categorized, or it silently falls out of every spending report.
    // Drafts are exempt — they're explicitly pending review before being confirmed.
    if (!modalForm.is_transfer && !modalForm.is_draft) {
      const hasCategory = modalUseSplits
        ? modalSplits.some(s => s.amount !== '' && s.amount !== '0' && s.categories_id)
        : !!modalForm.categories_id
      if (!hasCategory) {
        setModalError('Choose a category before saving — only transfers can be left uncategorized')
        return
      }
    }

    setModalSaving(true); setModalError(null)
    try {
      const statusFields = { is_draft: modalForm.is_draft, cleared: modalForm.cleared, reconciled: modalForm.reconciled }
      await updateTransaction(modalForm.id, {
        date: modalForm.date,
        description: modalForm.description || null,
        total_amount: parseFloat(modalForm.total_amount),
        payees_id: modalForm.payees_id ? Number(modalForm.payees_id) : null,
        ...statusFields,
      })
      if (modalUseSplits) {
        const validSplits = modalSplits.filter(s => s.amount !== '' && s.amount !== '0')
        const splitsTotal = validSplits.reduce((sum, s) => sum + parseFloat(s.amount), 0)
        const txTotal = parseFloat(modalForm.total_amount)
        if (Math.round(splitsTotal * 100) !== Math.round(txTotal * 100))
          throw new Error(`Split amounts (${fmtEur(splitsTotal)}) must equal total amount (${fmtEur(txTotal)})`)
        await upsertSplits(modalForm.id, validSplits.map(s => ({
          categories_id: s.categories_id ? Number(s.categories_id) : null,
          amount: parseFloat(s.amount),
          memo: s.memo || null,
        })))
      } else {
        await upsertSplits(modalForm.id, [{
          categories_id: modalForm.categories_id ? Number(modalForm.categories_id) : null,
          amount: parseFloat(modalForm.total_amount),
          memo: modalForm.memo || modalForm.description || null,
        }])
      }
      await qc.refetchQueries({ queryKey: ['ie-full'], type: 'active' })
      setModalOpen(false)
    } catch (e: unknown) {
      setModalError(e instanceof Error ? e.message : 'Save failed')
    } finally { setModalSaving(false) }
  }

  const handleModalDelete = async () => {
    if (!modalForm?.id || !confirm('Delete this transaction?')) return
    await deleteTransaction(modalForm.id)
    await qc.refetchQueries({ queryKey: ['ie-full'], type: 'active' })
    setModalOpen(false)
  }

  const { data: rawData = [], isLoading } = useQuery({
    queryKey: ['ie-full', qStart, qEnd, qCash.join(','), qInv.join(',')],
    queryFn: () => getIncomeExpenseFull(qStart, qEnd, qCash, qInv),
    staleTime: 60_000,
  })

  const allRows = rawData as Row[]

  // Filter by report type
  const ctFilter = catTypeForReport(reportType)
  const rows = ctFilter ? allRows.filter(r => String(r.categories_type).toLowerCase() === ctFilter.toLowerCase()) : allRows

  // Summary metrics
  const bankIncome = allRows.filter(r => r.source_type === 'Bank' && r.categories_type === 'Income').reduce((s, r) => s + Number(r.split_amount ?? 0), 0)
  const bankInterest = allRows.filter(r => r.source_type === 'Bank' && r.categories_type === 'Interest').reduce((s, r) => s + Number(r.split_amount ?? 0), 0)
  const invIncome = allRows.filter(r => r.source_type === 'Investment' && r.categories_type === 'Income').reduce((s, r) => s + Number(r.split_amount ?? 0), 0)
  const invDiv = allRows.filter(r => r.source_type === 'Investment' && r.categories_type === 'Dividend').reduce((s, r) => s + Number(r.split_amount ?? 0), 0)
  const invInt = allRows.filter(r => r.source_type === 'Investment' && r.categories_type === 'Interest').reduce((s, r) => s + Number(r.split_amount ?? 0), 0)
  const overallIncome = bankIncome + bankInterest + invIncome + invDiv + invInt

  const bankExpense = allRows.filter(r => r.source_type === 'Bank' && r.categories_type === 'Expense').reduce((s, r) => s + Number(r.split_amount ?? 0), 0)
  const taxTotal = allRows.filter(r => r.categories_type === 'Tax').reduce((s, r) => s + Number(r.split_amount ?? 0), 0)
  const invExpense = allRows.filter(r => r.source_type === 'Investment' && r.categories_type === 'Expense').reduce((s, r) => s + Number(r.split_amount ?? 0), 0)
  const overallExpense = bankExpense + taxTotal + invExpense

  const netSavings = overallIncome + overallExpense
  const savingsRate = overallIncome > 0 ? (netSavings / overallIncome) * 100 : 0

  const bankTotal = allRows.filter(r => r.source_type === 'Bank').reduce((s, r) => s + Number(r.split_amount ?? 0), 0)
  // Excludes realized investment P&L (categories_type 'Trading') so this reconciles
  // exactly with Net Savings (bankTotal + invTotal === netSavings) — realized gains/
  // losses are shown separately below instead, since they're lumpy one-off amounts
  // rather than recurring cash flow and would otherwise distort the savings rate.
  const invTotal = allRows.filter(r => r.source_type === 'Investment' && r.categories_type !== 'Trading').reduce((s, r) => s + Number(r.split_amount ?? 0), 0)
  const realizedPnl = allRows.filter(r => r.categories_type === 'Trading').reduce((s, r) => s + Number(r.split_amount ?? 0), 0)
  // Pivot rows by period
  type PivotRow = { category: string; cat_type: string; periods: Record<string, number>; total: number }
  const pivotMap = useMemo<PivotRow[]>(() => {
    const map: Record<string, PivotRow> = {}
    for (const r of rows) {
      const cat = String(r.category_full_path ?? 'Uncategorized')
      const ct = String(r.categories_type ?? '')
      const pk = getPeriodKey(String(r.date ?? ''), periodType)
      const amt = Number(r.split_amount ?? 0)
      if (!map[cat]) map[cat] = { category: cat, cat_type: ct, periods: {}, total: 0 }
      map[cat].periods[pk] = (map[cat].periods[pk] ?? 0) + amt
      map[cat].total += amt
    }
    return Object.values(map).sort((a, b) => Math.abs(b.total) - Math.abs(a.total))
  }, [rows, periodType])

  // Category-hierarchy rollup for the Details table (e.g. a "Vacation" row summing all its subcategories)
  const [collapsedCats, setCollapsedCats] = useState<Set<string>>(new Set())
  const catTree = useMemo(() => buildCategoryTree(pivotMap), [pivotMap])
  const treeRows = useMemo(() => {
    const out: CatTreeRow[] = []
    const visit = (path: string) => {
      const node = catTree.nodes[path]
      if (!node) return
      out.push(node)
      if (node.hasChildren && !collapsedCats.has(path)) {
        const kids = [...(catTree.childrenOf[path] ?? [])]
          .sort((a, b) => Math.abs(catTree.nodes[b].total) - Math.abs(catTree.nodes[a].total))
        kids.forEach(visit)
      }
    }
    const sortedRoots = [...catTree.roots].sort((a, b) => Math.abs(catTree.nodes[b].total) - Math.abs(catTree.nodes[a].total))
    sortedRoots.forEach(visit)
    return out
  }, [catTree, collapsedCats])

  const allPeriods = useMemo(() => {
    const s = new Set<string>()
    for (const r of pivotMap) Object.keys(r.periods).forEach(p => s.add(p))
    return [...s].sort()
  }, [pivotMap])

  // Bar chart data grouped by categories_type
  const barByType = useMemo(() => {
    const map: Record<string, Record<string, number>> = {}
    for (const r of rows) {
      const ct = String(r.categories_type ?? 'Other')
      const pk = getPeriodKey(String(r.date ?? ''), periodType)
      if (!map[ct]) map[ct] = {}
      map[ct][pk] = (map[ct][pk] ?? 0) + Math.abs(Number(r.split_amount ?? 0))
    }
    return map
  }, [rows, periodType])

  // All categories for drill-down
  const allCats = useMemo(() => ['All Categories', ...new Set(rows.map(r => String(r.category_full_path ?? '')).filter(Boolean)).values()].sort(), [rows])
  const allPayees = useMemo(() => ['All Payees', ...new Set(rows.filter(r => r.payees_name).map(r => String(r.payees_name))).values()].sort(), [rows])

  // Category summary for top-cats
  const catSummary = useMemo(() => {
    const map: Record<string, { total: number; count: number; cat_type: string }> = {}
    for (const r of rows) {
      const cat = String(r.category_full_path ?? '')
      const ct = String(r.categories_type ?? '')
      if (!map[cat]) map[cat] = { total: 0, count: 0, cat_type: ct }
      map[cat].total += Number(r.split_amount ?? 0)
      map[cat].count++
    }
    return Object.entries(map).map(([cat, v]) => ({ cat, ...v, abs: Math.abs(v.total) }))
  }, [rows])

  // Payee summary for top-payees
  const payeeSummary = useMemo(() => {
    const map: Record<string, { total: number; count: number; top_cat: string }> = {}
    for (const r of rows) {
      if (!r.payees_name) continue
      const p = String(r.payees_name)
      const cat = String(r.category_full_path ?? '')
      if (!map[p]) map[p] = { total: 0, count: 0, top_cat: cat }
      map[p].total += Number(r.split_amount ?? 0)
      map[p].count++
    }
    return Object.entries(map).map(([p, v]) => ({ payee: p, ...v, abs: Math.abs(v.total) }))
      .sort((a, b) => b.abs - a.abs)
  }, [rows])

  // Monthly trend for top 8 cats
  const trendData = useMemo(() => {
    const map: Record<string, Record<string, number>> = {}
    const catTotals: Record<string, number> = {}
    for (const r of rows) {
      if (!r.date) continue
      const cat = String(r.category_full_path ?? '')
      const mo = String(r.date ?? '').slice(0, 7)
      if (!map[cat]) map[cat] = {}
      map[cat][mo] = (map[cat][mo] ?? 0) + Number(r.split_amount ?? 0)
      catTotals[cat] = (catTotals[cat] ?? 0) + Math.abs(Number(r.split_amount ?? 0))
    }
    const top8 = Object.entries(catTotals).sort((a, b) => b[1] - a[1]).slice(0, 8).map(([c]) => c)
    const months = [...new Set(rows.map(r => String(r.date ?? '').slice(0, 7)).filter(Boolean))].sort()
    return { top8, months, map }
  }, [rows])

  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>

  return (
    <div className="space-y-4">
      {/* Controls */}
      <div className="flex flex-wrap gap-3 items-center">
        <div className="mt-4">
          <ChkBox label="YTD" checked={ytdMode} onChange={setYtdMode} />
        </div>
        <div className={ytdMode ? 'opacity-40 pointer-events-none' : ''}>
          <label className="block text-xs text-slate-500 mb-0.5">Start Date</label>
          <input type="date" value={effStart} onChange={e => setStartDate(e.target.value)}
            className="text-xs border border-slate-300 rounded px-2 py-1.5 bg-white focus:outline-none focus:ring-1 focus:ring-blue-400" />
        </div>
        <div className={ytdMode ? 'opacity-40 pointer-events-none' : ''}>
          <label className="block text-xs text-slate-500 mb-0.5">End Date</label>
          <input type="date" value={effEnd} onChange={e => setEndDate(e.target.value)}
            className="text-xs border border-slate-300 rounded px-2 py-1.5 bg-white focus:outline-none focus:ring-1 focus:ring-blue-400" />
        </div>
        <div>
          <label className="block text-xs text-slate-500 mb-0.5">Report Type</label>
          <select value={reportType} onChange={e => setReportType(e.target.value as ReportType)}
            className="text-xs border border-slate-300 rounded px-2 py-1.5 bg-white focus:outline-none focus:ring-1 focus:ring-blue-400">
            {REPORT_TYPES.map(t => <option key={t}>{t}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-xs text-slate-500 mb-0.5">Period</label>
          <select value={periodType} onChange={e => setPeriodType(e.target.value as PeriodType)}
            className="text-xs border border-slate-300 rounded px-2 py-1.5 bg-white focus:outline-none focus:ring-1 focus:ring-blue-400">
            {PERIOD_TYPES.map(t => <option key={t}>{t}</option>)}
          </select>
        </div>
        <div className="mt-4">
          <IEMultiSelect label="Cash Accounts" options={ALL_ACCOUNT_TYPES.filter(t => !invTypes.includes(t))}
            value={cashTypes} onChange={setCashTypes} />
        </div>
        <div className="mt-4">
          <IEMultiSelect label="Investment Accounts" options={ALL_ACCOUNT_TYPES.filter(t => !cashTypes.includes(t))}
            value={invTypes} onChange={setInvTypes} />
        </div>
        <button onClick={() => {
          setYtdMode(true); setStartDate(ytdStart); setEndDate(today)
          setReportType('Total Summary'); setPeriodType('Monthly')
          setCashTypes(DEFAULT_CASH_TYPES); setInvTypes(DEFAULT_INV_TYPES)
          setQStart(ytdStart); setQEnd(today); setQCash(DEFAULT_CASH_TYPES); setQInv(DEFAULT_INV_TYPES)
        }}
          className="mt-4 px-3 py-1.5 text-xs bg-slate-100 text-slate-600 rounded hover:bg-slate-200 border border-slate-300">
          Reset Defaults
        </button>
        <button onClick={commitParams} disabled={!isDirty}
          className={`mt-4 flex items-center gap-1.5 px-3 py-1.5 text-xs rounded border font-medium transition-colors ${isDirty ? 'bg-blue-600 text-white border-blue-700 hover:bg-blue-700' : 'bg-slate-100 text-slate-400 border-slate-300 cursor-not-allowed'}`}>
          <RefreshCw size={11} />
          Update
        </button>
      </div>

      {/* KPI row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="bg-green-50 rounded-lg p-3">
          <p className="text-xs text-slate-500">Overall Income</p>
          <p className="text-base font-bold text-green-700 tabular-nums">{fmtEur(overallIncome)}</p>
        </div>
        <div className="bg-red-50 rounded-lg p-3">
          <p className="text-xs text-slate-500">Overall Expenses</p>
          <p className="text-base font-bold text-red-600 tabular-nums">{fmtEur(Math.abs(overallExpense))}</p>
        </div>
        {reportType === 'Total Summary' && <>
          <div className={`rounded-lg p-3 ${netSavings >= 0 ? 'bg-blue-50' : 'bg-orange-50'}`}>
            <p className="text-xs text-slate-500">Net Savings</p>
            <p className={`text-base font-bold tabular-nums ${netSavings >= 0 ? 'text-blue-700' : 'text-orange-600'}`}>{fmtEur(netSavings)}</p>
          </div>
          <div className={`rounded-lg p-3 ${savingsRate >= 0 ? 'bg-teal-50' : 'bg-orange-50'}`}>
            <p className="text-xs text-slate-500">Savings Rate</p>
            <p className={`text-base font-bold tabular-nums ${savingsRate >= 0 ? 'text-teal-700' : 'text-orange-600'}`}>{savingsRate.toFixed(1)}%</p>
          </div>
        </>}
      </div>

      {/* Sub-breakdown row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
        <div className="bg-slate-50 rounded p-2 text-center">
          <p className="text-slate-400 mb-0.5">Earned & Reimbursed / Investments</p>
          <p className="font-semibold">
            <span className={bankIncome + bankInterest >= 0 ? 'text-green-600' : 'text-red-600'}>{fmtEur(bankIncome + bankInterest)}</span>
            {' / '}
            <span className={invIncome + invDiv + invInt >= 0 ? 'text-green-600' : 'text-red-600'}>{fmtEur(invIncome + invDiv + invInt)}</span>
          </p>
        </div>
        <div className="bg-slate-50 rounded p-2 text-center">
          <p className="text-slate-400 mb-0.5">Expenses / Taxes / Investments</p>
          <p className="font-semibold">
            <span className={bankExpense >= 0 ? 'text-green-600' : 'text-red-600'}>{fmtEur(bankExpense)}</span>
            {' / '}
            <span className={taxTotal >= 0 ? 'text-green-600' : 'text-red-600'}>{fmtEur(taxTotal)}</span>
            {' / '}
            <span className={invExpense >= 0 ? 'text-green-600' : 'text-red-600'}>{fmtEur(invExpense)}</span>
          </p>
        </div>
        <div className="bg-slate-50 rounded p-2 text-center">
          <p className="text-slate-400 mb-0.5">Savings by Cash / Investments</p>
          <p className="font-semibold">
            <span className={bankTotal >= 0 ? 'text-green-600' : 'text-red-600'}>{fmtEur(bankTotal)}</span>
            {' / '}
            <span className={invTotal >= 0 ? 'text-green-600' : 'text-red-600'}>{fmtEur(invTotal)}</span>
          </p>
        </div>
        <div className="bg-slate-50 rounded p-2 text-center">
          <Tooltip text="Realized gains/losses from closed investment trades (FIFO). Shown separately — excluded from Net Savings and Savings Rate above since it's a lumpy, one-off amount rather than recurring cash flow.">
            <p className="text-slate-400 mb-0.5">Realized Investment P&L</p>
          </Tooltip>
          <p className={`font-semibold ${realizedPnl >= 0 ? 'text-green-600' : 'text-red-600'}`}>{fmtEur(realizedPnl)}</p>
        </div>
      </div>

      <div className="border-t border-slate-200" />

      {/* Inner tabs */}
      <SubTabs tabs={['Chart', 'Details', 'Trend Analysis', 'Top Categories', 'Top Payees']} active={ieTab} onChange={setIeTab} />

      {/* ── CHART TAB ── */}
      {ieTab === 'Chart' && (
        <div className="space-y-6">
          {/* Stacked bar: income vs expense */}
          <div>
            <p className="text-sm font-semibold text-slate-700 mb-2">Income vs Expenses Comparison</p>
            <div className="flex items-center gap-2 mb-2">
              <label className="text-xs text-slate-500">Top N categories</label>
              <input type="range" min={5} max={20} value={topN} onChange={e => setTopN(Number(e.target.value))} className="w-28" />
              <span className="text-xs text-slate-600 font-medium">{topN}</span>
            </div>
            {(() => {
              const incomeTypes = Object.keys(barByType).filter(t => INCOME_TYPES.includes(t))
              const expenseTypes = Object.keys(barByType).filter(t => EXPENSE_TYPES.includes(t))
              const periods = [...new Set([...incomeTypes, ...expenseTypes].flatMap(t => Object.keys(barByType[t] ?? {})))].sort()
              const traces: object[] = []
              incomeTypes.forEach((ct, i) => {
                traces.push({ x: periods, y: periods.map(p => barByType[ct]?.[p] ?? 0), name: ct, type: 'bar', offsetgroup: 'Income', legendgroup: 'Income', marker: { color: TYPE_COLORS[ct] ?? '#16A085' }, hovertemplate: `%{x}<br>${ct}: %{y:,.2f}<extra></extra>`, ...(i === 0 ? { legendgrouptitle: { text: 'Income' } } : {}) })
              })
              expenseTypes.forEach((ct, i) => {
                traces.push({ x: periods, y: periods.map(p => barByType[ct]?.[p] ?? 0), name: ct, type: 'bar', offsetgroup: 'Expenses', legendgroup: 'Expenses', marker: { color: TYPE_COLORS[ct] ?? '#922B21' }, hovertemplate: `%{x}<br>${ct}: %{y:,.2f}<extra></extra>`, ...(i === 0 ? { legendgrouptitle: { text: 'Expenses' } } : {}) })
              })
              return <Plot data={traces} layout={{ barmode: 'stack', height: 420, margin: { t: 10, r: 20, b: 60, l: 70 }, xaxis: { type: 'category', tickangle: -45 }, yaxis: { title: 'Amount (€)', tickformat: ',.0f' }, hovermode: 'x unified', legend: { groupclick: 'toggleitem' }, ...plotLayout(isDark) }} config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }} />
            })()}
          </div>

          {/* Side-by-side pie charts */}
          <div>
            <p className="text-sm font-semibold text-slate-700 mb-2">Distribution Analysis</p>
            <div className="grid grid-cols-2 gap-4">
              {([['Income', INCOME_TYPES, '#10b981'] as const, ['Expenses', EXPENSE_TYPES, '#ef4444'] as const]).map(([label, types]) => {
                const subset = catSummary.filter(r => types.includes(r.cat_type as string)).sort((a, b) => b.abs - a.abs)
                const topCats = subset.slice(0, topN).map(r => r.cat)
                const agg: Record<string, number> = {}
                let other = 0
                subset.forEach(r => { if (topCats.includes(r.cat)) agg[r.cat] = (agg[r.cat] ?? 0) + r.abs; else other += r.abs })
                if (other > 0) agg['Other'] = other
                const vals = Object.values(agg), labs = Object.keys(agg)
                if (vals.length === 0) return <div key={label} className="text-xs text-slate-400 py-4 text-center">No {label} data</div>
                return <Plot key={label} data={[{ type: 'pie', values: vals, labels: labs, hole: 0.4, textposition: 'inside', textinfo: 'percent+label' }]}
                  layout={{ title: { text: `${label} Breakdown`, font: { size: 14 } }, showlegend: false, height: 380, margin: { t: 50, b: 10, l: 10, r: 10 }, ...plotLayout(isDark) }}
                  config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }} />
              })}
            </div>
          </div>
        </div>
      )}

      {/* ── DETAILED TABLE TAB ── */}
      {ieTab === 'Details' && (
        <div className="space-y-3">
          <p className="text-sm font-semibold text-slate-700">{reportType} — {periodType} Breakdown</p>
          <p className="text-xs text-slate-400">Click any period cell to drill down into the underlying transactions.</p>
          <WithCopy>
          <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-340px)] text-xs">
            <table className="w-full border-collapse">
              <thead className="sticky top-0 z-10 bg-slate-50">
                <tr className="bg-slate-50">
                  <th className="text-left px-2 py-1.5 border-b border-slate-200 font-semibold sticky left-0 bg-slate-50">Category</th>
                  <th className="text-left px-2 py-1.5 border-b border-slate-200 font-semibold">Type</th>
                  {allPeriods.map(p => <th key={p} className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold">{p}</th>)}
                  <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold">Total</th>
                </tr>
              </thead>
              <tbody>
                {treeRows.map((r) => (
                  <tr key={r.path} className={`border-b border-slate-100 hover:bg-slate-50 ${r.hasChildren ? 'bg-slate-50/70 font-semibold' : ''}`}>
                    <td className="px-2 py-1 sticky left-0 bg-white font-medium" style={{ background: r.hasChildren ? 'rgba(248,250,252,0.9)' : undefined }}>
                      <span style={{ paddingLeft: r.depth * 16 }} className="inline-flex items-center gap-1">
                        {r.hasChildren ? (
                          <button
                            onClick={() => setCollapsedCats(prev => {
                              const next = new Set(prev)
                              if (next.has(r.path)) next.delete(r.path); else next.add(r.path)
                              return next
                            })}
                            className="text-slate-400 hover:text-slate-600"
                          >
                            {collapsedCats.has(r.path) ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
                          </button>
                        ) : <span className="inline-block w-3" />}
                        {r.name}
                      </span>
                    </td>
                    <td className="px-2 py-1 text-slate-500">{r.cat_type}</td>
                    {allPeriods.map(p => {
                      const val = r.periods[p] ?? 0
                      const isActive = drillCell?.category === r.path && drillCell?.period === p
                      return (
                        <td key={p}
                          onClick={() => setDrillCell(isActive ? null : { category: r.path, period: p })}
                          className={`px-2 py-1 text-right tabular-nums cursor-pointer rounded transition-colors ${isActive ? 'bg-blue-100 ring-1 ring-blue-400' : 'hover:bg-blue-50'} ${val >= 0 ? 'text-green-700' : 'text-red-600'}`}>
                          {val !== 0 ? fmtEur(val) : ''}
                        </td>
                      )
                    })}
                    <td className={`px-2 py-1 text-right tabular-nums font-semibold ${r.total >= 0 ? 'text-green-700' : 'text-red-600'}`}>{fmtEur(r.total)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          </WithCopy>

          {/* Drill-down panel */}
          {drillCell && (() => {
            const drillRows = rows.filter(r => categoryMatches(String(r.category_full_path ?? ''), drillCell.category) && getPeriodKey(String(r.date ?? ''), periodType) === drillCell.period)
            const drillTotal = drillRows.reduce((s, r) => s + Number(r.split_amount ?? 0), 0)
            return (
              <div className="border border-blue-200 rounded-lg bg-blue-50">
                <div className="flex items-center justify-between px-3 py-2 border-b border-blue-200">
                  <p className="text-xs font-semibold text-blue-800">{drillCell.category} — {drillCell.period} <span className="font-normal text-blue-600">({drillRows.length} transactions, total: {fmtEur(drillTotal)})</span></p>
                  <button onClick={() => setDrillCell(null)} className="text-blue-400 hover:text-blue-600 text-xs">✕ Close</button>
                </div>
                <div className="overflow-x-auto max-h-72 overflow-y-auto">
                  <table className="w-full border-collapse text-xs">
                    <thead className="sticky top-0 bg-blue-50">
                      <tr>
                        <th className="text-left px-2 py-1.5 font-semibold text-slate-600 border-b border-blue-200 whitespace-nowrap">Date</th>
                        <th className="text-left px-2 py-1.5 font-semibold text-slate-600 border-b border-blue-200">Description</th>
                        <th className="text-left px-2 py-1.5 font-semibold text-slate-600 border-b border-blue-200">Payee</th>
                        <th className="text-right px-2 py-1.5 font-semibold text-slate-600 border-b border-blue-200">Amount</th>
                        <th className="text-left px-2 py-1.5 font-semibold text-slate-600 border-b border-blue-200">Account</th>
                        <th className="text-left px-2 py-1.5 font-semibold text-slate-600 border-b border-blue-200">Source</th>
                        <th className="px-2 py-1.5 border-b border-blue-200" />
                      </tr>
                    </thead>
                    <tbody>
                      {drillRows.map((r, i) => (
                        <tr key={i} className="border-b border-blue-100 hover:bg-white">
                          <td className="px-2 py-1 whitespace-nowrap">{String(r.date ?? '').slice(0, 10)}</td>
                          <td className="px-2 py-1 max-w-[160px] truncate">{String(r.description ?? '')}</td>
                          <td className="px-2 py-1">{String(r.payees_name ?? '')}</td>
                          <td className={`px-2 py-1 text-right tabular-nums font-medium ${Number(r.split_amount ?? 0) >= 0 ? 'text-green-700' : 'text-red-600'}`}>
                            {Number(r.split_amount_original ?? r.split_amount) !== 0
                              ? `${fmtEur(Number(r.split_amount_original ?? r.split_amount))} ${String(r.original_currency ?? 'EUR') !== 'EUR' ? `(${String(r.original_currency)})` : ''}`
                              : ''}
                          </td>
                          <td className="px-2 py-1"><AccountLink id={r.accounts_id as number} name={String(r.accounts_name ?? '')} type={String(r.accounts_type ?? '')} /></td>
                          <td className="px-2 py-1 text-slate-400">{String(r.source_type ?? '')}</td>
                          <td className="px-2 py-1">
                            {Boolean(r.transaction_id) && (
                              <button onClick={() => openEdit(r)} className="text-blue-500 hover:text-blue-700 p-0.5 rounded">
                                <Pencil size={11} />
                              </button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

              </div>
            )
          })()}
        </div>
      )}

      {/* ── TREND TAB ── */}
      {ieTab === 'Trend Analysis' && (
        <div>
          <p className="text-sm font-semibold text-slate-700 mb-2">Monthly Trend — Top 8 Categories</p>
          {trendData.top8.length === 0
            ? <p className="text-xs text-slate-400">No trend data available.</p>
            : <Plot
                data={trendData.top8.map(cat => ({
                  x: trendData.months,
                  y: trendData.months.map(m => trendData.map[cat]?.[m] ?? 0),
                  name: cat, type: 'scatter', mode: 'lines+markers',
                }))}
                layout={{ height: 480, margin: { t: 10, r: 20, b: 80, l: 70 }, xaxis: { tickangle: -45, title: 'Month' }, yaxis: { title: 'Amount (€)', tickformat: ',.0f' }, hovermode: 'x unified', ...plotLayout(isDark), legend: { orientation: 'h', y: -0.35 } }}
                config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }} />
          }
        </div>
      )}

      {/* ── TOP CATEGORIES TAB ── */}
      {ieTab === 'Top Categories' && (
        <div className="space-y-4">
          <div className="flex items-center gap-2">
            <label className="text-xs text-slate-500">Show Top N</label>
            <input type="range" min={5} max={30} value={topN} onChange={e => setTopN(Number(e.target.value))} className="w-28" />
            <span className="text-xs text-slate-600 font-medium">{topN}</span>
          </div>

          <div className="grid grid-cols-2 gap-4">
            {/* Income */}
            {(() => {
              const inc = catSummary.filter(r => INCOME_TYPES.includes(r.cat_type)).sort((a, b) => a.total - b.total).slice(-topN)
              return inc.length === 0
                ? <p className="text-xs text-slate-400">No income categories.</p>
                : <>
                  <div>
                    <p className="text-xs font-semibold text-green-700 mb-1">Top Income Categories</p>
                    <Plot data={[{ x: inc.map(r => r.total), y: inc.map(r => r.cat), type: 'bar', orientation: 'h', marker: { color: '#27AE60' }, text: inc.map(r => fmtEur(r.total)), textposition: 'auto', hovertemplate: '%{y}<br>€ %{x:,.2f}<extra></extra>' }]}
                      layout={{ height: Math.max(250, inc.length * 30), margin: { t: 5, r: 100, b: 30, l: 10 }, xaxis: { tickformat: ',.0f' }, ...plotLayout(isDark) }}
                      config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }} />
                  </div>
                </>
            })()}
            {/* Expense */}
            {(() => {
              const exp = catSummary.filter(r => EXPENSE_TYPES.includes(r.cat_type)).sort((a, b) => a.abs - b.abs).slice(-topN)
              return exp.length === 0
                ? <p className="text-xs text-slate-400">No expense categories.</p>
                : <div>
                    <p className="text-xs font-semibold text-red-600 mb-1">Top Expense Categories</p>
                    <Plot data={[{ x: exp.map(r => r.abs), y: exp.map(r => r.cat), type: 'bar', orientation: 'h', marker: { color: '#E74C3C' }, text: exp.map(r => fmtEur(r.total)), textposition: 'auto', hovertemplate: '%{y}<br>€ %{x:,.2f}<extra></extra>' }]}
                      layout={{ height: Math.max(250, exp.length * 30), margin: { t: 5, r: 100, b: 30, l: 10 }, xaxis: { tickformat: ',.0f' }, ...plotLayout(isDark) }}
                      config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }} />
                  </div>
            })()}
          </div>

          {/* Category detail table */}
          <div>
            <p className="text-xs font-semibold text-slate-600 mb-1">Category Detail</p>
            <WithCopy>
            <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-300px)] text-xs">
              <table className="w-full border-collapse">
                <thead className="sticky top-0 z-10 bg-slate-50">
                  <tr className="bg-slate-50">
                    <th className="text-left px-2 py-1.5 border-b border-slate-200 font-semibold sticky left-0 bg-slate-50">Category</th>
                    <th className="text-left px-2 py-1.5 border-b border-slate-200 font-semibold">Type</th>
                    {allPeriods.map(p => <th key={p} className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold">{p}</th>)}
                    <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold">Total</th>
                    <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold"># Txs</th>
                  </tr>
                </thead>
                <tbody>
                  {pivotMap.map((r, i) => {
                    const cnt = catSummary.find(c => c.cat === r.category)?.count ?? 0
                    return (
                      <tr key={i} className="border-b border-slate-100 hover:bg-slate-50">
                        <td className="px-2 py-1 sticky left-0 bg-white font-medium">{r.category}</td>
                        <td className="px-2 py-1 text-slate-500">{r.cat_type}</td>
                        {allPeriods.map(p => <td key={p} className={`px-2 py-1 text-right tabular-nums ${(r.periods[p] ?? 0) >= 0 ? 'text-green-700' : 'text-red-600'}`}>{fmtEur(r.periods[p] ?? 0)}</td>)}
                        <td className={`px-2 py-1 text-right tabular-nums font-semibold ${r.total >= 0 ? 'text-green-700' : 'text-red-600'}`}>{fmtEur(r.total)}</td>
                        <td className="px-2 py-1 text-right text-slate-500">{cnt}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
            </WithCopy>
          </div>

          {/* Drill-down */}
          <div>
            <p className="text-xs font-semibold text-slate-600 mb-1">Category Drill Down</p>
            <select value={drillCat} onChange={e => setDrillCat(e.target.value)}
              className="text-xs border border-slate-300 rounded px-2 py-1.5 bg-white mb-2 focus:outline-none">
              {allCats.map(c => <option key={c}>{c}</option>)}
            </select>
            {(() => {
              const drillRows = drillCat === 'All Categories' ? rows : rows.filter(r => r.category_full_path === drillCat)
              return (
                <WithCopy>
                <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-300px)] text-xs">
                  <table className="w-full border-collapse">
                    <thead className="sticky top-0 z-10"><tr className="bg-slate-50">
                      <th className="text-left px-2 py-1.5 border-b border-slate-200 font-semibold whitespace-nowrap">Date</th>
                      <th className="text-left px-2 py-1.5 border-b border-slate-200 font-semibold">Description</th>
                      <th className="text-left px-2 py-1.5 border-b border-slate-200 font-semibold">Payee</th>
                      <th className="text-left px-2 py-1.5 border-b border-slate-200 font-semibold">Category</th>
                      <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold">Amount (€)</th>
                      <th className="text-left px-2 py-1.5 border-b border-slate-200 font-semibold">Account</th>
                      <th className="text-left px-2 py-1.5 border-b border-slate-200 font-semibold">Source</th>
                      <th className="px-2 py-1.5 border-b border-slate-200" />
                    </tr></thead>
                    <tbody>
                      {drillRows.map((r, i) => (
                        <tr key={i} className="border-b border-slate-100 hover:bg-slate-50">
                          <td className="px-2 py-1 whitespace-nowrap">{String(r.date ?? '').slice(0, 10)}</td>
                          <td className="px-2 py-1 max-w-[180px] truncate">{String(r.description ?? '')}</td>
                          <td className="px-2 py-1">{String(r.payees_name ?? '')}</td>
                          <td className="px-2 py-1">{String(r.category_full_path ?? '')}</td>
                          <td className={`px-2 py-1 text-right tabular-nums font-medium ${Number(r.split_amount ?? 0) >= 0 ? 'text-green-700' : 'text-red-600'}`}>{fmtEur(Number(r.split_amount ?? 0))}</td>
                          <td className="px-2 py-1"><AccountLink id={r.accounts_id as number} name={String(r.accounts_name ?? '')} type={String(r.accounts_type ?? '')} /></td>
                          <td className="px-2 py-1 text-slate-500">{String(r.source_type ?? '')}</td>
                          <td className="px-2 py-1">
                            {Boolean(r.transaction_id) && (
                              <button onClick={() => openEdit(r)} className="text-blue-500 hover:text-blue-700 p-0.5 rounded">
                                <Pencil size={11} />
                              </button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                </WithCopy>
              )
            })()}
          </div>
        </div>
      )}

      {/* ── TOP PAYEES TAB ── */}
      {ieTab === 'Top Payees' && (
        <div className="space-y-4">
          <div className="flex items-center gap-2">
            <label className="text-xs text-slate-500">Show Top N</label>
            <input type="range" min={5} max={30} value={topN} onChange={e => setTopN(Number(e.target.value))} className="w-28" />
            <span className="text-xs text-slate-600 font-medium">{topN}</span>
          </div>
          <div className="grid grid-cols-2 gap-4">
            {(() => {
              const inc = payeeSummary.filter(r => r.total > 0).slice(0, topN).sort((a, b) => a.total - b.total)
              return inc.length === 0
                ? <p className="text-xs text-slate-400">No income payees.</p>
                : <div>
                    <p className="text-xs font-semibold text-green-700 mb-1">Top Income Payees</p>
                    <Plot data={[{ x: inc.map(r => r.total), y: inc.map(r => r.payee), type: 'bar', orientation: 'h', marker: { color: '#27AE60' }, text: inc.map(r => fmtEur(r.total)), textposition: 'auto', hovertemplate: '%{y}<br>€ %{x:,.2f}<extra></extra>' }]}
                      layout={{ height: Math.max(250, inc.length * 30), margin: { t: 5, r: 100, b: 30, l: 10 }, xaxis: { tickformat: ',.0f' }, ...plotLayout(isDark) }}
                      config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }} />
                  </div>
            })()}
            {(() => {
              const exp = payeeSummary.filter(r => r.total < 0).slice(0, topN).sort((a, b) => a.abs - b.abs)
              return exp.length === 0
                ? <p className="text-xs text-slate-400">No expense payees.</p>
                : <div>
                    <p className="text-xs font-semibold text-red-600 mb-1">Top Expense Payees</p>
                    <Plot data={[{ x: exp.map(r => r.abs), y: exp.map(r => r.payee), type: 'bar', orientation: 'h', marker: { color: '#E74C3C' }, text: exp.map(r => fmtEur(r.total)), textposition: 'auto', hovertemplate: '%{y}<br>€ %{x:,.2f}<extra></extra>' }]}
                      layout={{ height: Math.max(250, exp.length * 30), margin: { t: 5, r: 100, b: 30, l: 10 }, xaxis: { tickformat: ',.0f' }, ...plotLayout(isDark) }}
                      config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }} />
                  </div>
            })()}
          </div>

          {/* Payee summary table */}
          <div>
            <p className="text-xs font-semibold text-slate-600 mb-1">Payee Summary</p>
            <WithCopy>
            <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-300px)] text-xs">
              <table className="w-full border-collapse">
                <thead className="sticky top-0 z-10"><tr className="bg-slate-50">
                  <th className="text-left px-2 py-1.5 border-b border-slate-200 font-semibold sticky left-0 bg-slate-50">Payee</th>
                  <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold">Total (€)</th>
                  <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold"># Txs</th>
                  <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold">Avg / Tx</th>
                  <th className="text-left px-2 py-1.5 border-b border-slate-200 font-semibold">Top Category</th>
                </tr></thead>
                <tbody>
                  {payeeSummary.map((r, i) => (
                    <tr key={i} className="border-b border-slate-100 hover:bg-slate-50">
                      <td className="px-2 py-1 sticky left-0 bg-white font-medium">{r.payee}</td>
                      <td className={`px-2 py-1 text-right tabular-nums font-medium ${r.total >= 0 ? 'text-green-700' : 'text-red-600'}`}>{fmtEur(r.total)}</td>
                      <td className="px-2 py-1 text-right text-slate-500">{r.count}</td>
                      <td className={`px-2 py-1 text-right tabular-nums ${r.total / r.count >= 0 ? 'text-green-700' : 'text-red-600'}`}>{fmtEur(r.total / r.count)}</td>
                      <td className="px-2 py-1 text-slate-500 truncate max-w-[180px]">{r.top_cat}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            </WithCopy>
          </div>

          {/* Payee drill-down */}
          <div>
            <p className="text-xs font-semibold text-slate-600 mb-1">Payee Drill Down</p>
            <select value={drillPayee} onChange={e => setDrillPayee(e.target.value)}
              className="text-xs border border-slate-300 rounded px-2 py-1.5 bg-white mb-2 focus:outline-none">
              {allPayees.map(p => <option key={p}>{p}</option>)}
            </select>
            {(() => {
              const drillRows = drillPayee === 'All Payees' ? rows.filter(r => r.payees_name) : rows.filter(r => r.payees_name === drillPayee)
              const dTotal = drillRows.reduce((s, r) => s + Number(r.split_amount ?? 0), 0)
              return (
                <div className="space-y-2">
                  {drillPayee !== 'All Payees' && (
                    <div className="flex gap-4 text-xs">
                      <span className={`font-bold ${dTotal >= 0 ? 'text-green-700' : 'text-red-600'}`}>Total: {fmtEur(dTotal)}</span>
                      <span className="text-slate-500">Transactions: {drillRows.length}</span>
                    </div>
                  )}
                  <WithCopy>
                  <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-300px)] text-xs">
                    <table className="w-full border-collapse">
                      <thead className="sticky top-0 z-10"><tr className="bg-slate-50">
                        <th className="text-left px-2 py-1.5 border-b border-slate-200 font-semibold whitespace-nowrap">Date</th>
                        <th className="text-left px-2 py-1.5 border-b border-slate-200 font-semibold">Description</th>
                        <th className="text-left px-2 py-1.5 border-b border-slate-200 font-semibold">Category</th>
                        <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold">Amount (€)</th>
                        <th className="text-left px-2 py-1.5 border-b border-slate-200 font-semibold">Account</th>
                        <th className="px-2 py-1.5 border-b border-slate-200" />
                      </tr></thead>
                      <tbody>
                        {drillRows.map((r, i) => (
                          <tr key={i} className="border-b border-slate-100 hover:bg-slate-50">
                            <td className="px-2 py-1 whitespace-nowrap">{String(r.date ?? '').slice(0, 10)}</td>
                            <td className="px-2 py-1 max-w-[180px] truncate">{String(r.description ?? '')}</td>
                            <td className="px-2 py-1">{String(r.category_full_path ?? '')}</td>
                            <td className={`px-2 py-1 text-right tabular-nums font-medium ${Number(r.split_amount ?? 0) >= 0 ? 'text-green-700' : 'text-red-600'}`}>{fmtEur(Number(r.split_amount ?? 0))}</td>
                            <td className="px-2 py-1"><AccountLink id={r.accounts_id as number} name={String(r.accounts_name ?? '')} type={String(r.accounts_type ?? '')} /></td>
                            <td className="px-2 py-1">
                              {Boolean(r.transaction_id) && (
                                <button onClick={() => openEdit(r)} className="text-blue-500 hover:text-blue-700 p-0.5 rounded">
                                  <Pencil size={11} />
                                </button>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  </WithCopy>
                </div>
              )
            })()}
          </div>
        </div>
      )}

      {modalOpen && modalForm && (
        <TxModal
          form={modalForm}
          splits={modalSplits}
          useSplits={modalUseSplits}
          setUseSplits={setModalUseSplits}
          onFormChange={setModalForm}
          onSplitsChange={setModalSplits}
          payees={payees}
          categories={categories}
          accounts={accounts}
          onSave={handleModalSave}
          onDelete={handleModalDelete}
          onClose={() => setModalOpen(false)}
          onPayeeCreated={p => qc.setQueryData(['payees'], (old: Record<string,unknown>[]) => [...(old ?? []), { id: p.id, name: p.name }])}
          onCategoryCreated={c => qc.setQueryData(['categories'], (old: Record<string,unknown>[]) => [...(old ?? []), c])}
          saving={modalSaving}
          error={modalError}
          {...recurring}
        />
      )}
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════════
// 6. CASH FLOW FORECAST
// ════════════════════════════════════════════════════════════════════════════
const CF_HORIZONS = [
  { label: '30d', days: 30 },
  { label: '60d', days: 60 },
  { label: '3m',  days: 90 },
  { label: '6m',  days: 180 },
  { label: '12m', days: 365 },
]
// Days from today through Dec 31 of the current year — recomputed on each use (not a
// fixed CF_HORIZONS entry) since "today" changes daily, unlike the other fixed presets.
function daysUntilEndOfYear(): number {
  const now = new Date()
  const eoy = new Date(now.getFullYear(), 11, 31)
  return Math.max(1, Math.ceil((eoy.getTime() - now.getTime()) / 86400000))
}
// Complete calendar months elapsed so far this year (Jan = 0 of them, Aug = 7, Dec = 11) —
// floored at the slider's own minimum of 2 so early-year YTD doesn't request a degenerately
// short recurring window.
function ytdMonthsBack(): number {
  return Math.max(2, new Date().getMonth())
}
const CF_COLOR_MAP: Record<string, string> = {
  'Income · Scheduled':           '#2ECC71',
  'Expense · Scheduled':          '#E74C3C',
  'Income · Recurring Template':  '#3498DB',
  'Expense · Recurring Template': '#F39C12',
  'Income · Recurring (est.)':    '#82E0AA',
  'Expense · Recurring (est.)':   '#F1948A',
  'Income · Dividends (est.)':    '#9B59B6',
  'Income · Interest (est.)':     '#1ABC9C',
  'Income · Bonds (est.)':        '#B7950B',
}

function CashFlowSection() {
  const { isDark } = useTheme()
  const [days, setDays] = usePersist<number>('cf_days', 60)
  const [monthsBack, setMonthsBack] = usePersist<number>('cf_months_back', 2)
  const [ytdRecurring, setYtdRecurring] = usePersist<boolean>('cf_recurring_ytd', false)
  const [includeBonds, setIncludeBonds] = usePersist<boolean>('cf_include_bonds', false)
  const [highlighted, setHighlighted] = useState<string | null>(null)
  const [presetAccountIds, setPresetAccountIds] = useState<number[] | undefined>(undefined)
  const eoyDays = daysUntilEndOfYear()
  const effectiveMonthsBack = ytdRecurring ? ytdMonthsBack() : monthsBack

  const { data, isLoading } = useQuery({
    queryKey: ['cash-flow-forecast-full', days, effectiveMonthsBack, presetAccountIds],
    queryFn: () => getCashFlowForecastFull(days, effectiveMonthsBack, presetAccountIds),
  })

  const result = data as {
    scheduled: Row[]
    templates: Row[]
    recurring: Row[]
    dividends: Row[]
    interest: Row[]
    bonds: Row[]
    metrics: { sched_in: number; sched_out: number; tmpl_in: number; tmpl_out: number; recur_in: number; recur_out: number; div_in: number; int_in: number; bond_in: number; net_total: number }
  } | undefined

  // Build chart data: aggregate scheduled + templates + recurring + dividends by calendar month
  const chartTraces = useMemo(() => {
    if (!result) return []
    const bySeriesMonth: Record<string, Record<string, number>> = {}
    const addRow = (date: string, amt: number, source: string) => {
      const flow = amt >= 0 ? 'Income' : 'Expense'
      const series = `${flow} · ${source}`
      const month = date.slice(0, 7) // YYYY-MM
      if (!bySeriesMonth[series]) bySeriesMonth[series] = {}
      bySeriesMonth[series][month] = (bySeriesMonth[series][month] ?? 0) + amt
    }
    for (const r of result.scheduled) addRow(String(r.date), Number(r.amount_eur), 'Scheduled')
    for (const r of result.templates) addRow(String(r.date), Number(r.amount_eur), 'Recurring Template')
    for (const r of result.recurring) addRow(String(r.date), Number(r.amount_eur), 'Recurring (est.)')
    for (const r of result.dividends ?? []) addRow(String(r.date), Number(r.amount_eur), 'Dividends (est.)')
    for (const r of result.interest ?? []) addRow(String(r.date), Number(r.amount_eur), 'Interest (est.)')
    if (includeBonds) for (const r of result.bonds ?? []) addRow(String(r.date), Number(r.amount_eur), 'Bonds (est.)')

    const allMonths = [...new Set([
      ...Object.values(bySeriesMonth).flatMap(m => Object.keys(m))
    ])].sort()

    return Object.entries(bySeriesMonth).map(([series, monthMap]) => ({
      x: allMonths.map(m => `${m}-01`),
      y: allMonths.map(m => monthMap[m] ?? 0),
      name: series,
      type: 'bar' as const,
      marker: { color: CF_COLOR_MAP[series] ?? '#94a3b8' },
    }))
  }, [result, includeBonds])

  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>

  const m = result?.metrics
  const scheduled = result?.scheduled ?? []
  const templates = result?.templates ?? []
  const recurring = result?.recurring ?? []
  const dividends = result?.dividends ?? []
  const interest = result?.interest ?? []
  const bonds = includeBonds ? (result?.bonds ?? []) : []
  const effectiveBondIn = includeBonds ? (m?.bond_in ?? 0) : 0
  const effectiveNetTotal = m ? (includeBonds ? m.net_total : m.net_total - m.bond_in) : 0

  const KPI_METRICS = m ? [
    { label: 'Scheduled In',  value: fmtEur(m.sched_in),  color: 'text-green-700', tip: 'Total income from explicitly scheduled future transactions within the horizon.', target: 'cf-scheduled' },
    { label: 'Scheduled Out', value: fmtEur(m.sched_out), color: 'text-red-600',   tip: 'Total expenses from explicitly scheduled future transactions within the horizon.', target: 'cf-scheduled' },
    { label: 'Template In',   value: fmtEur(m.tmpl_in),   color: 'text-blue-700',  tip: 'Total income projected from your active Recurring Templates within the horizon.', target: 'cf-templates' },
    { label: 'Template Out',  value: fmtEur(m.tmpl_out),  color: 'text-orange-600', tip: 'Total expenses projected from your active Recurring Templates within the horizon.', target: 'cf-templates' },
    { label: 'Recurring In',  value: fmtEur(m.recur_in),  color: 'text-green-600', tip: 'Estimated income from statistically-detected recurring patterns not already covered by a template, projected forward.', target: 'cf-recurring' },
    { label: 'Recurring Out', value: fmtEur(m.recur_out), color: 'text-red-500',   tip: 'Estimated expenses from statistically-detected recurring patterns not already covered by a template, projected forward.', target: 'cf-recurring' },
    { label: 'Dividend Income', value: fmtEur(m.div_in),  color: 'text-purple-700', tip: 'Projected dividend income from currently-held securities within the horizon (Dividend Rate > Fwd Yield > Trailing 12m actual income).', target: 'cf-dividends' },
    { label: 'Interest Income', value: fmtEur(m.int_in),  color: 'text-teal-700', tip: "Projected interest within the horizon. Uses each account's manually-defined rate schedule (Static Data → Accounts → Interest Rates) where one exists, otherwise falls back to compounding the current balance forward at its last real interest period's APY% and payment cadence — same basis as the Savings tab's own Forecast view.", target: 'cf-interest' },
    { label: 'Bond Cash Flow', value: fmtEur(effectiveBondIn), color: 'text-amber-700', tip: includeBonds ? "Coupon payments and face-value maturity redemptions for currently-held bonds falling within the horizon. Zero-coupon instruments (e.g. T-Bills, Coupon Frequency = 'At Maturity') only contribute their face value at maturity — their return is embedded in the discount purchase price, not paid as a separate coupon." : "Excluded — enable 'Include Bonds' above to project bond coupon payments and maturity redemptions within the horizon.", target: 'cf-bonds' },
    { label: 'Total Net',     value: fmtEur(effectiveNetTotal), color: effectiveNetTotal >= 0 ? 'text-green-700' : 'text-red-600', tip: `Net cash flow: sum of all scheduled, template, recurring, dividend, and interest in/out amounts within the horizon${includeBonds ? ', plus bonds' : ' (bonds excluded — enable \'Include Bonds\' above)'}.`, target: 'cf-chart' },
  ] : []

  const scrollToSection = (id: string) => {
    const el = document.getElementById(id)
    if (!el) return
    el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    setHighlighted(id)
    window.setTimeout(() => setHighlighted(h => h === id ? null : h), 1500)
  }

  return (
    <div className="space-y-6">
      {/* Controls */}
      <div className="flex flex-wrap items-center gap-6">
        <div className="flex items-center gap-2">
          <Tooltip text="How far ahead to project cash flows. Scheduled transactions are filtered to this window; recurring patterns are projected until the cutoff date.">
            <span className="text-sm text-slate-500 cursor-help underline decoration-dotted">Horizon</span>
          </Tooltip>
          <div className="flex gap-1">
            {CF_HORIZONS.map(h => (
              <button key={h.days} onClick={() => setDays(h.days)}
                className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${days === h.days ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}>
                {h.label}
              </button>
            ))}
            <Tooltip text="Project through Dec 31 of the current year.">
              <button onClick={() => setDays(eoyDays)}
                className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${days === eoyDays ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}>
                EOY
              </button>
            </Tooltip>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Tooltip text={`A payee + category pair must appear in every one of the last ${effectiveMonthsBack} complete calendar months to be classified as recurring. Increase to require a longer consistent history; decrease to catch newer patterns.`}>
            <span className="text-sm text-slate-500 cursor-help underline decoration-dotted">Recurring window: <strong>{effectiveMonthsBack}m</strong></span>
          </Tooltip>
          <input type="range" min={2} max={6} step={1} value={monthsBack} disabled={ytdRecurring}
            onChange={e => setMonthsBack(Number(e.target.value))}
            className="w-24 accent-blue-600 disabled:opacity-40" />
          <Tooltip text="Use every complete calendar month so far this year instead of the slider above.">
            <label className="flex items-center gap-1.5 text-sm text-slate-500 cursor-pointer">
              <input type="checkbox" checked={ytdRecurring} onChange={e => setYtdRecurring(e.target.checked)}
                className="accent-blue-600" />
              YTD
            </label>
          </Tooltip>
          <Tooltip text="Project coupon payments and face-value maturity redemptions for currently-held bonds within the horizon. Off by default since a maturing bond's face value can be a large lump sum that dominates the chart.">
            <label className="flex items-center gap-1.5 text-sm text-slate-500 cursor-pointer">
              <input type="checkbox" checked={includeBonds} onChange={e => setIncludeBonds(e.target.checked)}
                className="accent-blue-600" />
              Include Bonds
            </label>
          </Tooltip>
        </div>
      </div>

      {/* Account Selection — shares Net Worth's saved presets rather than keeping a separate set */}
      <PortfolioPresetBar reportScope="net_worth" eligibleTypes={ALL_ACCOUNT_TYPES} onChange={setPresetAccountIds} />

      {/* KPI metrics */}
      {KPI_METRICS.length > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-10 gap-3">
          {KPI_METRICS.map(k => (
            <button
              key={k.label} type="button" onClick={() => scrollToSection(k.target)}
              className="bg-slate-50 border border-slate-200 rounded-lg px-3 py-2.5 text-left hover:bg-slate-100 hover:border-slate-300 transition-colors cursor-pointer"
            >
              <Tooltip text={k.tip}>
                <div className="text-xs text-slate-500 mb-0.5 cursor-help underline decoration-dotted">{k.label}</div>
              </Tooltip>
              <div className={`text-sm font-bold tabular-nums ${k.color}`}>{k.value}</div>
            </button>
          ))}
        </div>
      )}

      {/* Bar chart */}
      <div id="cf-chart" className={highlighted === 'cf-chart' ? 'ring-2 ring-blue-400 rounded-lg transition-shadow' : 'transition-shadow'}>
      {chartTraces.length > 0 ? (
        <Plot
          data={chartTraces}
          layout={{
            barmode: 'relative' as const,
            height: 320,
            margin: { t: 10, r: 10, b: 50, l: 70 },
            yaxis: { tickformat: ',.0f', tickprefix: '€' },
            xaxis: { tickformat: '%b %Y', dtick: 'M1', type: 'date' as const },
            legend: { orientation: 'h' as const, y: -0.35, x: 0.5, xanchor: 'center' as const },
            hovermode: 'x unified' as const,
            ...plotLayout(isDark),
          }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: '100%' }}
        />
      ) : (
        <p className="text-sm text-slate-400 text-center py-6">No cash flows found within the selected horizon.</p>
      )}
      </div>

      {/* Explicitly Scheduled Future Transactions */}
      <div id="cf-scheduled" className={highlighted === 'cf-scheduled' ? 'ring-2 ring-blue-400 rounded-lg p-2 -m-2 transition-shadow' : 'p-2 -m-2 transition-shadow'}>
        <h3 className="text-sm font-semibold text-slate-700 mb-2">📅 Explicitly Scheduled Future Transactions</h3>
        {scheduled.length === 0 ? (
          <p className="text-sm text-slate-400">No transactions scheduled within this horizon.</p>
        ) : (
          <WithCopy>
          <div className="overflow-x-auto overflow-y-auto max-h-72 border border-slate-200 rounded-lg">
            <table className="w-full text-sm">
              <thead className="sticky top-0 z-10 bg-slate-50">
                <tr className="text-xs text-slate-500 uppercase tracking-wide">
                  <th className="px-3 py-2 text-left">Date</th>
                  <th className="px-3 py-2 text-left">Payee</th>
                  <th className="px-3 py-2 text-left">Account</th>
                  <th className="px-3 py-2 text-left">Category</th>
                  <th className="px-3 py-2 text-right">Amount (€)</th>
                  <th className="px-3 py-2 text-left">Currency</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {scheduled.map((r, i) => (
                  <tr key={i} className="hover:bg-slate-50">
                    <td className="px-3 py-2 tabular-nums text-slate-600">{String(r.date)}</td>
                    <td className="px-3 py-2 font-medium">{String(r.payees_name || '—')}</td>
                    <td className="px-3 py-2 text-slate-500 text-xs"><AccountLink id={r.accounts_id as number} name={String(r.accounts_name || '—')} type={String(r.accounts_type ?? '')} /></td>
                    <td className="px-3 py-2 text-slate-500 text-xs">{String(r.category || '—')}</td>
                    <td className={`px-3 py-2 text-right tabular-nums font-semibold ${Number(r.amount_eur) < 0 ? 'text-red-600' : 'text-green-700'}`}>
                      {fmtEur(Number(r.amount_eur))}
                    </td>
                    <td className="px-3 py-2 text-slate-400 text-xs">{String(r.currency || 'EUR')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          </WithCopy>
        )}
      </div>

      {/* Recurring Templates */}
      <div id="cf-templates" className={highlighted === 'cf-templates' ? 'ring-2 ring-blue-400 rounded-lg p-2 -m-2 transition-shadow' : 'p-2 -m-2 transition-shadow'}>
        <h3 className="text-sm font-semibold text-slate-700 mb-1">🔂 Recurring Templates</h3>
        <p className="text-xs text-slate-400 mb-2">
          Every future occurrence of your active <strong>Recurring Templates</strong> (see Recurring page) within this horizon,
          projected forward from each template's own next due date and frequency.
        </p>
        {templates.length === 0 ? (
          <p className="text-sm text-slate-400">No active recurring templates due within this horizon.</p>
        ) : (
          <WithCopy>
          <div className="overflow-x-auto overflow-y-auto max-h-72 border border-slate-200 rounded-lg">
            <table className="w-full text-sm">
              <thead className="sticky top-0 z-10 bg-slate-50">
                <tr className="text-xs text-slate-500 uppercase tracking-wide">
                  <th className="px-3 py-2 text-left">Due Date</th>
                  <th className="px-3 py-2 text-left">Payee</th>
                  <th className="px-3 py-2 text-left">Account</th>
                  <th className="px-3 py-2 text-left">Category</th>
                  <th className="px-3 py-2 text-right">Amount (€)</th>
                  <th className="px-3 py-2 text-left">Frequency</th>
                  <th className="px-3 py-2 text-left">Currency</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {templates.map((r, i) => (
                  <tr key={i} className="hover:bg-slate-50">
                    <td className="px-3 py-2 tabular-nums text-slate-600">{String(r.date)}</td>
                    <td className="px-3 py-2 font-medium">{String(r.payees_name || '—')}</td>
                    <td className="px-3 py-2 text-slate-500 text-xs"><AccountLink id={r.accounts_id as number} name={String(r.accounts_name || '—')} type={String(r.accounts_type ?? '')} /></td>
                    <td className="px-3 py-2 text-slate-500 text-xs">{String(r.category || '—')}</td>
                    <td className={`px-3 py-2 text-right tabular-nums font-semibold ${Number(r.amount_eur) < 0 ? 'text-red-600' : 'text-green-700'}`}>
                      {fmtEur(Number(r.amount_eur))}
                    </td>
                    <td className="px-3 py-2 text-slate-500 text-xs">{String(r.periodicity || '—')}</td>
                    <td className="px-3 py-2 text-slate-400 text-xs">{String(r.currency || 'EUR')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          </WithCopy>
        )}
      </div>

      {/* Projected Recurring Payments */}
      <div id="cf-recurring" className={highlighted === 'cf-recurring' ? 'ring-2 ring-blue-400 rounded-lg p-2 -m-2 transition-shadow' : 'p-2 -m-2 transition-shadow'}>
        <h3 className="text-sm font-semibold text-slate-700 mb-1">🔁 Projected Recurring Payments</h3>
        <p className="text-xs text-slate-400 mb-2">
          Payee + Category combinations detected in <strong>every one</strong> of the last <strong>{monthsBack} complete months</strong>,
          projected forward at their average payment interval. Payees already covered by an explicit scheduled entry or an active
          Recurring Template above are excluded to avoid double-counting.
        </p>
        {recurring.length === 0 ? (
          <p className="text-sm text-slate-400">No recurring payments projected within this horizon.</p>
        ) : (
          <WithCopy>
          <div className="overflow-x-auto overflow-y-auto max-h-72 border border-slate-200 rounded-lg">
            <table className="w-full text-sm">
              <thead className="sticky top-0 z-10 bg-slate-50">
                <tr className="text-xs text-slate-500 uppercase tracking-wide">
                  <th className="px-3 py-2 text-left">Projected Date</th>
                  <th className="px-3 py-2 text-left">Payee</th>
                  <th className="px-3 py-2 text-left">Category</th>
                  <th className="px-3 py-2 text-right">Est. Amount (€)</th>
                  <th className="px-3 py-2 text-right">Interval (days)</th>
                  <th className="px-3 py-2 text-left">Currency</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {recurring.map((r, i) => (
                  <tr key={i} className="hover:bg-slate-50">
                    <td className="px-3 py-2 tabular-nums text-slate-600">{String(r.date)}</td>
                    <td className="px-3 py-2 font-medium">{String(r.payees_name || '—')}</td>
                    <td className="px-3 py-2 text-slate-500 text-xs">{String(r.category || '—')}</td>
                    <td className={`px-3 py-2 text-right tabular-nums font-semibold ${Number(r.amount_eur) < 0 ? 'text-red-600' : 'text-green-700'}`}>
                      {fmtEur(Number(r.amount_eur))}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-slate-500">{String(r.avg_days_between)}</td>
                    <td className="px-3 py-2 text-slate-400 text-xs">{String(r.currency || 'EUR')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          </WithCopy>
        )}
      </div>

      {/* Dividend Income */}
      <div id="cf-dividends" className={highlighted === 'cf-dividends' ? 'ring-2 ring-blue-400 rounded-lg p-2 -m-2 transition-shadow' : 'p-2 -m-2 transition-shadow'}>
        <h3 className="text-sm font-semibold text-slate-700 mb-1">💰 Expected Dividend Income</h3>
        <p className="text-xs text-slate-400 mb-2">
          Projected dividend payments for currently-held securities within this horizon, using the same forecast
          logic as the Dividend Tracker (Dividend Rate &gt; Fwd Yield &gt; Trailing 12m actual income).
        </p>
        {dividends.length === 0 ? (
          <p className="text-sm text-slate-400">No dividend payments projected within this horizon.</p>
        ) : (
          <WithCopy>
          <div className="overflow-x-auto overflow-y-auto max-h-72 border border-slate-200 rounded-lg">
            <table className="w-full text-sm">
              <thead className="sticky top-0 z-10 bg-slate-50">
                <tr className="text-xs text-slate-500 uppercase tracking-wide">
                  <th className="px-3 py-2 text-left">Pay Date</th>
                  <th className="px-3 py-2 text-left">Security</th>
                  <th className="px-3 py-2 text-right">Amount (€)</th>
                  <th className="px-3 py-2 text-left">Frequency</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {dividends.map((r, i) => (
                  <tr key={i} className="hover:bg-slate-50">
                    <td className="px-3 py-2 tabular-nums text-slate-600 whitespace-nowrap">{String(r.date)}</td>
                    <td className="px-3 py-2 font-medium"><SecLink id={r.securities_id}>{String(r.payees_name || '—')}</SecLink></td>
                    <td className="px-3 py-2 text-right tabular-nums font-semibold text-purple-700">
                      {fmtEur(Number(r.amount_eur))}
                    </td>
                    <td className="px-3 py-2 text-slate-500 text-xs">{String(r.frequency || '—')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          </WithCopy>
        )}
      </div>

      {/* Interest Income */}
      <div id="cf-interest" className={highlighted === 'cf-interest' ? 'ring-2 ring-blue-400 rounded-lg p-2 -m-2 transition-shadow' : 'p-2 -m-2 transition-shadow'}>
        <h3 className="text-sm font-semibold text-slate-700 mb-1">🏦 Expected Interest Income</h3>
        <p className="text-xs text-slate-400 mb-2">
          Projected interest within this horizon. Accounts with a manually-defined rate schedule (Static Data →
          Accounts → Interest Rates) use that balance-tiered rate; other Savings accounts fall back to compounding
          their current balance forward at the last real interest period's APY% and payment cadence — same basis as
          the Savings tab's own Forecast view.
        </p>
        {interest.length === 0 ? (
          <p className="text-sm text-slate-400">No interest projected within this horizon.</p>
        ) : (
          <WithCopy>
          <div className="overflow-x-auto overflow-y-auto max-h-72 border border-slate-200 rounded-lg">
            <table className="w-full text-sm">
              <thead className="sticky top-0 z-10 bg-slate-50">
                <tr className="text-xs text-slate-500 uppercase tracking-wide">
                  <th className="px-3 py-2 text-left">Pay Date</th>
                  <th className="px-3 py-2 text-left">Account</th>
                  <th className="px-3 py-2 text-right">Amount (€)</th>
                  <th className="px-3 py-2 text-left">Cadence</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {interest.map((r, i) => (
                  <tr key={i} className="hover:bg-slate-50">
                    <td className="px-3 py-2 tabular-nums text-slate-600 whitespace-nowrap">{String(r.date)}</td>
                    <td className="px-3 py-2 font-medium">{String(r.payees_name || '—')}</td>
                    <td className="px-3 py-2 text-right tabular-nums font-semibold text-teal-700">
                      {fmtEur(Number(r.amount_eur))}
                    </td>
                    <td className="px-3 py-2 text-slate-500 text-xs">{String(r.frequency || '—')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          </WithCopy>
        )}
      </div>

      {/* Bond Coupons & Maturities */}
      <div id="cf-bonds" className={highlighted === 'cf-bonds' ? 'ring-2 ring-blue-400 rounded-lg p-2 -m-2 transition-shadow' : 'p-2 -m-2 transition-shadow'}>
        <h3 className="text-sm font-semibold text-slate-700 mb-1">🏛️ Bond Coupons &amp; Maturities</h3>
        <p className="text-xs text-slate-400 mb-2">
          Coupon payments and face-value redemptions for currently-held bonds within this horizon, projected from
          each bond's own Maturity Date, Coupon Rate, and Coupon Frequency (Static Data → Securities). Zero-coupon
          instruments (Coupon Frequency = "At Maturity", e.g. T-Bills) only contribute their face value at
          maturity — their return is embedded in the discount purchase price rather than paid separately.
        </p>
        {bonds.length === 0 ? (
          <p className="text-sm text-slate-400">
            {includeBonds ? 'No bond coupons or maturities projected within this horizon.' : "Excluded — enable 'Include Bonds' above to project bond coupon payments and maturity redemptions within this horizon."}
          </p>
        ) : (
          <WithCopy>
          <div className="overflow-x-auto overflow-y-auto max-h-72 border border-slate-200 rounded-lg">
            <table className="w-full text-sm">
              <thead className="sticky top-0 z-10 bg-slate-50">
                <tr className="text-xs text-slate-500 uppercase tracking-wide">
                  <th className="px-3 py-2 text-left">Date</th>
                  <th className="px-3 py-2 text-left">Security</th>
                  <th className="px-3 py-2 text-left">Account</th>
                  <th className="px-3 py-2 text-left">Type</th>
                  <th className="px-3 py-2 text-right">Amount (€)</th>
                  <th className="px-3 py-2 text-left">Currency</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {bonds.map((r, i) => (
                  <tr key={i} className="hover:bg-slate-50">
                    <td className="px-3 py-2 tabular-nums text-slate-600 whitespace-nowrap">{String(r.date)}</td>
                    <td className="px-3 py-2 font-medium"><SecLink id={r.securities_id}>{String(r.payees_name || '—')}</SecLink></td>
                    <td className="px-3 py-2 text-slate-500 text-xs"><AccountLink id={r.accounts_id as number} name={String(r.accounts_name || '—')} type={String(r.accounts_type ?? '')} /></td>
                    <td className="px-3 py-2 text-xs">
                      <span className={`px-1.5 py-0.5 rounded-full font-medium ${r.kind === 'Maturity' ? 'bg-amber-100 text-amber-800' : 'bg-slate-100 text-slate-600'}`}>
                        {String(r.kind)}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums font-semibold text-amber-700">
                      {fmtEur(Number(r.amount_eur))}
                    </td>
                    <td className="px-3 py-2 text-slate-400 text-xs">{String(r.currency || 'EUR')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          </WithCopy>
        )}
      </div>
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════════
// 7. BUDGET & SPENDING
// ════════════════════════════════════════════════════════════════════════════
function BudgetReport() {
  const { isDark } = useTheme()
  const qc = useQueryClient()
  const now = new Date()
  const [year, setYear] = useState(now.getFullYear())
  const [refYears, setRefYears] = useState(2)
  const [budgetEdits, setBudgetEdits] = useState<Record<number, string>>({})
  const [saveMsg, setSaveMsg] = useState<string | null>(null)
  const [drillCat, setDrillCat] = useState<string | null>(null)
  const [copyFromYear, setCopyFromYear] = useState(now.getFullYear() - 1)
  const [copySource, setCopySource] = useState<'budget' | 'actual'>('budget')

  const isCurrentYear = year === now.getFullYear()
  const ytdLabel = isCurrentYear ? 'YTD Actual' : 'Actual'
  const priorLabel = `${year - 1} Actual`
  const avgCol = `Avg/Year (${refYears}y)`

  const { data = [], isLoading } = useQuery({
    queryKey: ['budget-vs-actual', year, refYears],
    queryFn: () => getBudgetVsActual(year, refYears),
  })
  const { data: incomeData } = useQuery({
    queryKey: ['annual-income', year],
    queryFn: () => getAnnualIncome(year),
  })
  const { data: txData = [], isLoading: txLoading } = useQuery({
    queryKey: ['ytd-expense-tx', year],
    queryFn: () => getYtdExpenseTransactions(year),
    staleTime: 120_000,
  })

  const rows = data as Row[]
  const txRows = txData as Row[]

  // Summary KPIs
  const totalAvg    = rows.reduce((s, r) => s + Number(r.avg_annual_hist ?? 0), 0)
  const totalPrior  = rows.reduce((s, r) => s + Number(r.prior_year_amount ?? 0), 0)
  const totalBudget = rows.reduce((s, r) => s + Number(r.budget_amount ?? 0), 0)
  const totalActual = rows.reduce((s, r) => s + Number(r.actual_amount ?? 0), 0)
  const variance    = totalBudget - totalActual
  const totalIncome = Number((incomeData as { total_income_eur?: number } | undefined)?.total_income_eur ?? 0)

  // Budget rows with editable amounts
  const budgetedRows = rows.filter(r => Number(r.budget_amount) > 0)
  const pctOfYear = isCurrentYear ? now.getTime() / new Date(year + 1, 0, 1).getTime() : 1

  const saveMut = useMutation({
    mutationFn: async () => {
      const promises = Object.entries(budgetEdits).map(([catId, val]) =>
        saveBudget({ year, categories_id: Number(catId), budget_amount: parseFloat(val) || 0 })
      )
      await Promise.all(promises)
    },
    onSuccess: () => {
      setSaveMsg('✅ Budgets saved!')
      setBudgetEdits({})
      qc.invalidateQueries({ queryKey: ['budget-vs-actual'] })
      setTimeout(() => setSaveMsg(null), 3000)
    },
    onError: () => setSaveMsg('❌ Save failed'),
  })

  const copyMut = useMutation({
    mutationFn: async () => {
      const srcRows = (await getBudgetVsActual(copyFromYear, refYears)) as Row[]
      const srcField = copySource === 'budget' ? 'budget_amount' : 'actual_amount'
      const toCopy = srcRows.filter(r => Number(r[srcField]) > 0)
      await Promise.all(toCopy.map(r =>
        saveBudget({ year, categories_id: Number(r.categories_id), budget_amount: Number(r[srcField]) })
      ))
      return toCopy.length
    },
    onSuccess: (n) => {
      const srcLabel = copySource === 'budget' ? 'budget' : 'actuals'
      setSaveMsg(`✅ Copied ${n} ${srcLabel === 'actuals' ? 'categories from' : 'budgets from'} ${copyFromYear} ${srcLabel} to ${year} budget!`)
      setBudgetEdits({})
      qc.invalidateQueries({ queryKey: ['budget-vs-actual'] })
      setTimeout(() => setSaveMsg(null), 3000)
    },
    onError: () => setSaveMsg('❌ Copy failed'),
  })

  // Drill-down categories
  const catTotals: Record<string, number> = {}
  for (const r of txRows) {
    const cat = String(r.category)
    catTotals[cat] = (catTotals[cat] ?? 0) + Number(r.amount_eur ?? 0)
  }
  const allCats = Object.keys(catTotals).sort()
  const drillRows = drillCat ? txRows.filter(r => String(r.category) === drillCat) : []

  // Bar chart for budgeted categories
  const chartRows = rows.filter(r => Number(r.budget_amount) > 0)
  const catNames = chartRows.map(r => String(r.categories_name))

  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>

  return (
    <div className="space-y-6">
      {/* Controls */}
      <div className="flex flex-wrap items-center gap-6">
        <div className="flex items-center gap-2">
          <label className="text-sm font-medium text-slate-600">Year</label>
          <Input type="number" className="w-24" value={year}
            onChange={e => { setYear(Number(e.target.value)); setDrillCat(null) }} />
        </div>
        <div className="flex items-center gap-3">
          <label className="text-sm font-medium text-slate-600">Reference years (hist avg): {refYears}</label>
          <input type="range" min={1} max={5} value={refYears}
            onChange={e => setRefYears(Number(e.target.value))} className="w-28" />
        </div>
        <div className="flex items-center gap-1.5">
          <label className="text-sm font-medium text-slate-600">Copy</label>
          <Select className="w-20" value={copySource} onChange={e => setCopySource(e.target.value as 'budget' | 'actual')}>
            <option value="budget">Budget</option>
            <option value="actual">Actual</option>
          </Select>
          <Input type="number" className="w-20" value={copyFromYear}
            onChange={e => setCopyFromYear(Number(e.target.value))} />
          <Button size="sm" variant="secondary" disabled={(copySource === 'budget' && copyFromYear === year) || copyMut.isPending}
            onClick={() => {
              const srcLabel = copySource === 'budget' ? 'budget' : 'actual spend'
              if (window.confirm(`Copy ${srcLabel} from ${copyFromYear} into ${year}'s budget? This will overwrite any existing ${year} budget for those categories.`)) {
                setSaveMsg(null)
                copyMut.mutate()
              }
            }}>
            {copyMut.isPending ? <Spinner size={12} /> : null} 📋 Copy
          </Button>
        </div>
      </div>

      {/* Summary KPIs */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        {[
          { label: avgCol,         value: fmtEur(totalAvg),    color: 'text-slate-700' },
          { label: priorLabel,     value: fmtEur(totalPrior),  color: 'text-slate-700' },
          { label: 'Annual Budget',value: fmtEur(totalBudget), color: 'text-blue-700'  },
          { label: ytdLabel,       value: fmtEur(totalActual), color: 'text-slate-700' },
          { label: 'Variance',     value: fmtEur(variance),    color: variance >= 0 ? 'text-green-700' : 'text-red-600' },
          { label: isCurrentYear ? 'YTD Income' : 'Annual Income', value: fmtEur(totalIncome), color: 'text-green-700' },
        ].map(m => (
          <div key={m.label} className="bg-slate-50 border border-slate-200 rounded-lg px-3 py-2.5">
            <div className="text-xs text-slate-500 mb-0.5">{m.label}</div>
            <div className={`text-sm font-bold tabular-nums ${m.color}`}>{m.value}</div>
          </div>
        ))}
      </div>

      {/* Editable budget table */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-semibold text-slate-700">Set Annual Budgets</h3>
          {Object.keys(budgetEdits).length > 0 && (
            <Button size="sm" onClick={() => { setSaveMsg(null); saveMut.mutate() }} disabled={saveMut.isPending}>
              {saveMut.isPending ? <Spinner size={12} /> : null} 💾 Save All Budgets
            </Button>
          )}
        </div>
        {saveMsg && <div className="text-sm mb-2 text-green-700">{saveMsg}</div>}
        <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-300px)] border border-slate-200 rounded-lg">
          <table className="w-full text-sm">
            <thead className="sticky top-0 z-10"><tr className="bg-slate-50 text-xs text-slate-500 uppercase tracking-wide">
              <th className="px-3 py-2 text-left">Category</th>
              <th className="px-3 py-2 text-right">{avgCol}</th>
              <th className="px-3 py-2 text-right">{priorLabel}</th>
              <th className="px-3 py-2 text-right">Budget (€) ✏️</th>
              <th className="px-3 py-2 text-right">{ytdLabel}</th>
              <th className="px-3 py-2 text-right">Variance</th>
              <th className="px-3 py-2 text-right">% Used</th>
            </tr></thead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((r, i) => {
                const catId = Number(r.categories_id)
                const budg = catId in budgetEdits ? parseFloat(budgetEdits[catId]) || 0 : Number(r.budget_amount ?? 0)
                const act  = Number(r.actual_amount ?? 0)
                const varE = budg - act
                const pct  = budg > 0 ? act / budg * 100 : null
                return (
                  <tr key={i} className={`hover:bg-slate-50 ${r.over_budget ? 'bg-red-50/30' : ''}`}>
                    <td className="px-3 py-1.5 text-slate-700 text-xs">{String(r.categories_name)}</td>
                    <td className="px-3 py-1.5 text-right tabular-nums text-slate-400 text-xs">{fmtEur(Number(r.avg_annual_hist ?? 0))}</td>
                    <td className="px-3 py-1.5 text-right tabular-nums text-slate-400 text-xs">{fmtEur(Number(r.prior_year_amount ?? 0))}</td>
                    <td className="px-2 py-1">
                      <input
                        type="number" min={0} step={100}
                        value={catId in budgetEdits ? budgetEdits[catId] : String(Number(r.budget_amount ?? 0))}
                        onChange={e => setBudgetEdits(prev => ({ ...prev, [catId]: e.target.value }))}
                        className="w-28 text-right text-xs border border-slate-300 rounded px-2 py-1 focus:border-blue-400 focus:outline-none font-medium"
                      />
                    </td>
                    <td className="px-3 py-1.5 text-right tabular-nums text-xs">{fmtEur(act)}</td>
                    <td className={`px-3 py-1.5 text-right tabular-nums text-xs font-medium ${varE >= 0 ? 'text-green-600' : 'text-red-600'}`}>{fmtEur(varE)}</td>
                    <td className="px-3 py-1.5 text-right">
                      {pct != null ? (
                        <div className="flex items-center gap-1.5 justify-end">
                          <div className="w-14 h-1.5 bg-slate-200 rounded-full overflow-hidden">
                            <div className={`h-full rounded-full ${pct > 100 ? 'bg-red-500' : pct > 80 ? 'bg-amber-500' : 'bg-green-500'}`}
                              style={{ width: `${Math.min(pct, 100)}%` }} />
                          </div>
                          <span className="text-xs tabular-nums text-slate-500">{pct.toFixed(0)}%</span>
                        </div>
                      ) : '—'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Bar chart — budgeted categories only */}
      {chartRows.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-slate-700 mb-2">Budget vs Actual — {year}</h3>
          <Plot
            data={[
              { x: catNames, y: chartRows.map(r => Number(r.avg_annual_hist ?? 0)), name: `Avg (${refYears}y)`, type: 'bar', marker: { color: '#94a3b8' } },
              { x: catNames, y: chartRows.map(r => Number(r.prior_year_amount ?? 0)), name: `${year - 1} Actual`, type: 'bar', marker: { color: '#f59e0b' } },
              { x: catNames, y: chartRows.map(r => Number(r.budget_amount ?? 0)), name: 'Budget', type: 'bar', marker: { color: '#3b82f6' } },
              { x: catNames, y: chartRows.map(r => Number(r.actual_amount ?? 0)), name: ytdLabel, type: 'bar', marker: { color: '#ef4444' } },
            ]}
            layout={{ barmode: 'group', height: 340, margin: { t: 10, r: 10, b: 100, l: 70 },
              xaxis: { tickangle: -35 }, yaxis: { tickformat: ',.0f', tickprefix: '€' },
              legend: { orientation: 'h', y: -0.45 }, ...plotLayout(isDark) }}
            config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }} />
        </div>
      )}

      {/* Progress bars */}
      {budgetedRows.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-slate-700 mb-3">Progress per Category</h3>
          <div className="space-y-3">
            {budgetedRows.map((r, i) => {
              const catId = Number(r.categories_id)
              const budget = catId in budgetEdits ? (parseFloat(budgetEdits[catId]) || 0) : Number(r.budget_amount ?? 0)
              const actual = Number(r.actual_amount ?? 0)
              const pct    = budget > 0 ? Math.min(actual / budget, 1) : 0
              const over   = Boolean(r.over_budget)
              const expected = isCurrentYear ? budget * pctOfYear : null
              return (
                <div key={i}>
                  <div className="flex justify-between text-xs mb-1">
                    <span className={`font-medium ${over ? 'text-red-600' : 'text-slate-700'}`}>
                      {over ? '🔴' : '🟢'} {String(r.categories_name)}
                    </span>
                    <span className="tabular-nums text-slate-500">
                      {fmtEur(actual)} / {fmtEur(budget)}
                      {expected != null && <span className="text-slate-400 ml-1">(expected {fmtEur(expected)})</span>}
                    </span>
                  </div>
                  <div className="w-full h-2 bg-slate-200 rounded-full overflow-hidden">
                    <div className={`h-full rounded-full transition-all ${over ? 'bg-red-500' : pct > 0.8 ? 'bg-amber-400' : 'bg-green-500'}`}
                      style={{ width: `${pct * 100}%` }} />
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Transaction drill-down */}
      <div>
        <h3 className="text-sm font-semibold text-slate-700 mb-2">{year} Transactions by Category</h3>
        {txLoading ? <Spinner /> : (
          <>
            <select value={drillCat ?? ''} onChange={e => setDrillCat(e.target.value || null)}
              className="border border-slate-300 rounded-md px-3 py-1.5 text-sm w-full max-w-md mb-3">
              <option value="">Select category…</option>
              {allCats.map(cat => (
                <option key={cat} value={cat}>{cat} — {fmtEur(catTotals[cat])}</option>
              ))}
            </select>
            {drillCat && drillRows.length > 0 && (
              <div>
                <p className="text-xs text-slate-500 mb-2">{drillRows.length} transaction(s) · total {fmtEur(catTotals[drillCat] ?? 0)}</p>
                <WithCopy>
                <div className="overflow-x-auto border border-slate-200 rounded-lg max-h-72">
                  <table className="w-full text-xs">
                    <thead><tr className="bg-slate-50 sticky top-0">
                      <th className="px-3 py-2 text-left font-medium text-slate-600">Date</th>
                      <th className="px-3 py-2 text-left font-medium text-slate-600">Payee</th>
                      <th className="px-3 py-2 text-right font-medium text-slate-600">Amount (€)</th>
                      <th className="px-3 py-2 text-left font-medium text-slate-600">Notes</th>
                    </tr></thead>
                    <tbody>
                      {drillRows.map((r, i) => (
                        <tr key={i} className={i % 2 === 0 ? 'bg-white' : 'bg-slate-50'}>
                          <td className="px-3 py-1.5 text-slate-500">{String(r.date)}</td>
                          <td className="px-3 py-1.5 text-slate-700">{String(r.payee ?? '')}</td>
                          <td className="px-3 py-1.5 text-right tabular-nums font-medium">{fmtEur(Number(r.amount_eur ?? 0))}</td>
                          <td className="px-3 py-1.5 text-slate-500 max-w-xs truncate">{String(r.notes ?? '')}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                </WithCopy>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

function SpendingTrendsTab() {
  const { isDark } = useTheme()
  const [months, setMonths] = useState(12)
  const { data = [], isLoading } = useQuery({
    queryKey: ['spending-trends', months],
    queryFn: () => getSpendingTrends(months),
  })
  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  const rows = data as Row[]
  const allCats = [...new Set(rows.map(r => String(r.category)))]
  const catTotals: Record<string, number> = {}
  for (const r of rows) catTotals[String(r.category)] = (catTotals[String(r.category)] ?? 0) + Number(r.amount_eur ?? 0)
  const topCats = allCats.sort((a, b) => (catTotals[b] ?? 0) - (catTotals[a] ?? 0)).slice(0, 10)
  const dates = [...new Set(rows.map(r => String(r.month)))].sort()
  const colors = ['#3b82f6','#10b981','#f59e0b','#8b5cf6','#ef4444','#06b6d4','#f97316','#84cc16','#ec4899','#6366f1']
  const traces = topCats.map((cat, i) => {
    const lookup: Record<string, number> = {}
    for (const r of rows) if (String(r.category) === cat) lookup[String(r.month)] = Number(r.amount_eur ?? 0)
    return { x: dates, y: dates.map(d => lookup[d] ?? 0), name: cat, stackgroup: 'one', fillcolor: colors[i % colors.length], line: { color: colors[i % colors.length] } }
  })
  return (
    <div className="space-y-4">
      <div className="flex gap-2 items-center">
        <span className="text-sm text-slate-600">Months:</span>
        {[6, 12, 24].map(m => (
          <button key={m} onClick={() => setMonths(m)}
            className={`px-2.5 py-1 rounded text-xs font-medium ${months === m ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-600'}`}>{m}</button>
        ))}
      </div>
      <Plot data={traces}
        layout={{ height: 380, margin: { t: 10, r: 10, b: 40, l: 70 }, yaxis: { tickformat: ',.0f', tickprefix: '€' }, legend: { orientation: 'h', y: -0.3 }, ...plotLayout(isDark), hovermode: 'x unified' }}
        config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }} />
    </div>
  )
}

function SavingsRateTab() {
  const { isDark } = useTheme()
  const [months, setMonths] = useState(24)
  const { data = [], isLoading } = useQuery({
    queryKey: ['savings-rate-detail', months],
    queryFn: () => getSavingsRateDetail(months),
  })
  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  const d = data as Row[]
  return (
    <div className="space-y-4">
      <div className="flex gap-2 items-center">
        <span className="text-sm text-slate-600">Months:</span>
        {[12, 24, 36].map(m => (
          <button key={m} onClick={() => setMonths(m)}
            className={`px-2.5 py-1 rounded text-xs font-medium ${months === m ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-600'}`}>{m}</button>
        ))}
      </div>
      <Plot
        data={[
          { x: d.map(r => String(r.month)), y: d.map(r => Number(r.income_eur)), name: 'Income', type: 'bar', marker: { color: '#10b981' } },
          { x: d.map(r => String(r.month)), y: d.map(r => Number(r.expenses_eur)), name: 'Expenses', type: 'bar', marker: { color: '#ef4444' } },
          { x: d.map(r => String(r.month)), y: d.map(r => Number(r.savings_rate_pct)), name: 'Savings Rate %', type: 'scatter', mode: 'lines+markers', yaxis: 'y2', line: { color: '#3b82f6', width: 2 }, marker: { size: 5 } },
        ]}
        layout={{ barmode: 'group', height: 380, margin: { t: 10, r: 60, b: 40, l: 70 }, yaxis: { tickformat: ',.0f', tickprefix: '€' }, yaxis2: { overlaying: 'y', side: 'right', ticksuffix: '%', showgrid: false }, legend: { orientation: 'h', y: -0.2 }, ...plotLayout(isDark), hovermode: 'x unified' }}
        config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }} />
      <WithCopy>
      <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-300px)] text-xs">
        <table className="w-full border-collapse">
          <thead className="sticky top-0 z-10"><tr className="bg-slate-50">
            <th className="text-left px-2 py-1.5 border-b border-slate-200 font-semibold">Month</th>
            <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold">Income</th>
            <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold">Expenses</th>
            <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold">Savings</th>
            <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold">Rate %</th>
          </tr></thead>
          <tbody>
            {d.map((r, i) => (
              <tr key={i} className="border-b border-slate-100 hover:bg-slate-50">
                <td className="px-2 py-1.5">{String(r.month).slice(0, 7)}</td>
                <td className="px-2 py-1.5 text-right tabular-nums text-green-700">{fmtEur(Number(r.income_eur))}</td>
                <td className="px-2 py-1.5 text-right tabular-nums text-red-600">{fmtEur(Number(r.expenses_eur))}</td>
                <td className={`px-2 py-1.5 text-right tabular-nums font-medium ${Number(r.savings_eur) >= 0 ? 'text-blue-700' : 'text-orange-600'}`}>{fmtEur(Number(r.savings_eur))}</td>
                <td className={`px-2 py-1.5 text-right tabular-nums ${Number(r.savings_rate_pct) >= 0 ? 'text-blue-600' : 'text-orange-600'}`}>{Number(r.savings_rate_pct).toFixed(1)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      </WithCopy>
    </div>
  )
}

function BudgetSection() {
  const [tab, setTab] = usePersist('budget_tab', 'Budget vs Actual')
  return (
    <div>
      <SubTabs tabs={['Budget vs Actual', 'Spending Trends', 'Savings Rate']} active={tab} onChange={setTab} />
      {tab === 'Budget vs Actual' && <BudgetReport />}
      {tab === 'Spending Trends' && <SpendingTrendsTab />}
      {tab === 'Savings Rate' && <SavingsRateTab />}
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════════
// 8. INVESTMENT TAX
// ════════════════════════════════════════════════════════════════════════════
function CgTable({ rows, method }: { rows: Row[]; method: string }) {
  const [expanded, setExpanded] = React.useState<Set<string>>(new Set())
  const costLabel = method === 'WAC' ? 'WAC Cost (€)' : method === 'FIFO' ? 'FIFO Cost (€)' : 'LIFO Cost (€)'

  // Group by security + account
  type Group = { key: string; secId: unknown; security: string; ticker: string; accountId: unknown; account: string; rows: Row[]; proceeds: number; cost: number; gl: number }
  const groups: Group[] = []
  const groupMap = new Map<string, Group>()
  for (const r of rows) {
    const key = `${r.securities_id}__${r.account}`
    if (!groupMap.has(key)) {
      const g: Group = { key, secId: r.securities_id, security: String(r.security), ticker: String(r.ticker ?? '—'), accountId: r.accounts_id, account: String(r.account), rows: [], proceeds: 0, cost: 0, gl: 0 }
      groupMap.set(key, g)
      groups.push(g)
    }
    const g = groupMap.get(key)!
    g.rows.push(r)
    g.proceeds += Number(r.proceeds_eur ?? 0)
    g.cost     += Number(r.cost_eur ?? 0)
    g.gl       += Number(r.gain_loss_eur ?? 0)
  }

  const toggle = (key: string) => setExpanded(prev => { const s = new Set(prev); s.has(key) ? s.delete(key) : s.add(key); return s })

  return (
    <WithCopy>
    <div className="overflow-x-auto overflow-y-auto max-h-[600px]">
      <table className="w-full text-xs">
        <thead className="sticky top-0 z-10"><tr className="bg-slate-50 text-xs text-slate-500 uppercase tracking-wide">
          <th className="px-2 py-1.5 text-left w-6"></th>
          <th className="px-2 py-1.5 text-left">Security</th>
          <th className="px-2 py-1.5 text-left">Account</th>
          <th className="px-2 py-1.5 text-right">Txns</th>
          <th className="px-2 py-1.5 text-right">Proceeds (€)</th>
          <th className="px-2 py-1.5 text-right">Cost Basis (€)</th>
          <th className="px-2 py-1.5 text-right">Gain / Loss (€)</th>
        </tr></thead>
        <tbody className="divide-y divide-slate-100">
          {groups.map(g => {
            const isOpen = expanded.has(g.key)
            return (
              <React.Fragment key={g.key}>
                {/* Summary row */}
                <tr className="hover:bg-slate-50 cursor-pointer" onClick={() => toggle(g.key)}>
                  <td className="px-2 py-1.5 text-slate-400">{isOpen ? '▾' : '▸'}</td>
                  <td className="px-2 py-1.5 font-medium"><SecLink id={g.secId}>{g.security}</SecLink>{g.ticker !== '—' && <span className="ml-1 text-slate-400 font-mono">{g.ticker}</span>}</td>
                  <td className="px-2 py-1.5 text-slate-600"><AccountLink id={g.accountId as number} name={g.account} type="Brokerage" /></td>
                  <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{g.rows.length}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums">{fmtEur(g.proceeds)}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums">{fmtEur(g.cost)}</td>
                  <td className={`px-2 py-1.5 text-right tabular-nums font-bold ${g.gl >= 0 ? 'text-green-700' : 'text-red-600'}`}>{g.gl >= 0 ? '+' : ''}{fmtEur(g.gl)}</td>
                </tr>
                {/* Detail rows */}
                {isOpen && (
                  <tr>
                    <td colSpan={7} className="p-0">
                      <table className="w-full text-xs bg-slate-50 border-l-4 border-slate-200">
                        <thead><tr className="text-slate-400 uppercase tracking-wide">
                          <th className="px-3 py-1 text-left">Date</th>
                          <th className="px-3 py-1 text-right">Qty</th>
                          <th className="px-3 py-1 text-right">Sell Price</th>
                          <th className="px-3 py-1 text-right">{costLabel}</th>
                          <th className="px-3 py-1 text-right">Proceeds (€)</th>
                          <th className="px-3 py-1 text-right">Cost Basis (€)</th>
                          <th className="px-3 py-1 text-right">Gain / Loss (€)</th>
                          <th className="px-3 py-1 text-left">Holding</th>
                        </tr></thead>
                        <tbody className="divide-y divide-slate-100">
                          {g.rows.map((r, i) => {
                            const gl = Number(r.gain_loss_eur ?? 0)
                            return (
                              <tr key={i} className="hover:bg-slate-100">
                                <td className="px-3 py-1 text-slate-500">{String(r.date ?? '').slice(0, 10)}</td>
                                <td className="px-3 py-1 text-right tabular-nums">{fmtNum(Number(r.quantity), 4)}</td>
                                <td className="px-3 py-1 text-right tabular-nums">{fmtNum(Number(r.sell_price ?? 0), 4)}</td>
                                <td className="px-3 py-1 text-right tabular-nums">{fmtEur(Number(r.avg_cost ?? 0))}</td>
                                <td className="px-3 py-1 text-right tabular-nums">{fmtEur(Number(r.proceeds_eur ?? 0))}</td>
                                <td className="px-3 py-1 text-right tabular-nums">{fmtEur(Number(r.cost_eur ?? 0))}</td>
                                <td className={`px-3 py-1 text-right tabular-nums font-semibold ${gl >= 0 ? 'text-green-700' : 'text-red-600'}`}>{gl >= 0 ? '+' : ''}{fmtEur(gl)}</td>
                                <td className="px-3 py-1">
                                  <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${r.holding_type === 'Long-term' ? 'bg-blue-50 text-blue-700' : 'bg-amber-50 text-amber-700'}`}>
                                    {String(r.holding_type ?? '—')}
                                  </span>
                                </td>
                              </tr>
                            )
                          })}
                        </tbody>
                      </table>
                    </td>
                  </tr>
                )}
              </React.Fragment>
            )
          })}
        </tbody>
      </table>
    </div>
    </WithCopy>
  )
}

function CapitalGainsReport({ year }: { year: number }) {
  const [method, setMethod] = useState<'WAC' | 'FIFO' | 'LIFO'>('FIFO')
  const [showExempt, setShowExempt] = useState(false)
  const [highlighted, setHighlighted] = useState<string | null>(null)
  const scrollToSection = (id: string, expand?: () => void) => {
    expand?.()
    window.setTimeout(() => {
      const el = document.getElementById(id)
      if (!el) return
      el.scrollIntoView({ behavior: 'smooth', block: 'start' })
      setHighlighted(id)
      window.setTimeout(() => setHighlighted(h => h === id ? null : h), 1500)
    }, expand ? 50 : 0)
  }
  const { data = [], isLoading } = useQuery({ queryKey: ['capital-gains', year, method], queryFn: () => getCapitalGains(year, method) })
  const d = data as Row[]

  const sum = (rows: Row[]) => rows.reduce((s, r) => s + Number(r.gain_loss_eur ?? 0), 0)
  const fmt2 = (n: number) => `€ ${n >= 0 ? '+' : ''}${fmtNum(n, 2)}`

  const isExempt = (r: Row) => r.is_tax_exempt === true || r.is_tax_exempt === 'true' || r.is_tax_exempt === 1
  const isTaxable = (r: Row) => r.gains_taxable === true || r.gains_taxable === 'true'
  const isEffExempt = (r: Row) => isExempt(r) || (!isTaxable(r))

  const dExempt  = d.filter(r =>  isEffExempt(r))
  const dTaxable = d.filter(r => !isEffExempt(r))

  // Group taxable rows by tax_category for separate sections
  const taxableCategories = useMemo(() => {
    const map = new Map<string, { rows: Row[]; rate: number | null; taxCode: string | null }>()
    for (const r of dTaxable) {
      const cat = String(r.tax_category ?? 'Other')
      if (!map.has(cat)) map.set(cat, { rows: [], rate: r.gains_rate != null ? Number(r.gains_rate) : null, taxCode: r.gains_tax_code ? String(r.gains_tax_code) : null })
      map.get(cat)!.rows.push(r)
    }
    return [...map.entries()].sort((a, b) => Math.abs(sum(b[1].rows)) - Math.abs(sum(a[1].rows)))
  }, [dTaxable])

  // Tax estimate on gross gains only — losses are informational, not deducted
  const totalTaxEst = taxableCategories.reduce((s, [, { rows, rate }]) => {
    const grossGains = rows.filter(r => Number(r.gain_loss_eur ?? 0) > 0).reduce((a, r) => a + Number(r.gain_loss_eur ?? 0), 0)
    return s + (rate != null ? grossGains * (rate / 100) : 0)
  }, 0)

  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>

  return (
    <div className="space-y-6">
      <p className="text-xs text-slate-500">
        Realized gains/losses for the selected tax year. Tax treatment is driven by each position's{' '}
        <strong>effective tax category</strong> (instrument-type override → security tax category → Tax Rules settings).
        Exempt categories (e.g. Local Listed, UCITS) are shown separately. All amounts in EUR.
      </p>

      {/* Controls */}
      <div className="flex flex-wrap items-end gap-4">
        <div>
          <label className="text-xs text-slate-500 block mb-1">Cost Basis Method</label>
          <div className="flex gap-3 items-center h-9">
            {([['WAC', 'WAC (Weighted Avg)'], ['FIFO', 'FIFO (First-In First-Out)'], ['LIFO', 'LIFO (Last-In First-Out)']] as const).map(([m, label]) => (
              <label key={m} className="flex items-center gap-1.5 text-sm cursor-pointer">
                <input type="radio" name="cg-method" value={m} checked={method === m} onChange={() => setMethod(m)} className="accent-blue-600" />
                {label}
              </label>
            ))}
          </div>
        </div>
      </div>

      {d.length === 0 ? (
        <p className="text-sm text-slate-500 py-4">No sell transactions found for {year}. Try a different year.</p>
      ) : (
        <>
          {/* Exempt banner */}
          {dExempt.length > 0 && (
            <div className="bg-green-50 border border-green-200 rounded-lg px-4 py-3 text-green-700 text-sm font-medium">
              <strong>{dExempt.length} tax-exempt sale(s) excluded</strong>{' '}
              ({fmt2(sum(dExempt))} net G/L) — categories with Gains Taxable = No. Shown separately below.
            </div>
          )}

          {/* Headline summary */}
          {(() => {
            const grossTaxableGains = dTaxable.filter(r => Number(r.gain_loss_eur ?? 0) > 0).reduce((s, r) => s + Number(r.gain_loss_eur ?? 0), 0)
            const grossTaxableLosses = dTaxable.filter(r => Number(r.gain_loss_eur ?? 0) < 0).reduce((s, r) => s + Number(r.gain_loss_eur ?? 0), 0)
            return (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <button type="button" disabled={dExempt.length === 0}
                  onClick={() => scrollToSection('cg-exempt', () => setShowExempt(true))}
                  className="bg-slate-50 rounded-lg px-4 py-3 text-left hover:bg-slate-100 disabled:hover:bg-slate-50 disabled:cursor-default transition-colors">
                  <div className="text-xs text-slate-500 mb-1">Exempt Net G/L</div>
                  <div className={`text-xl font-bold tabular-nums ${sum(dExempt) >= 0 ? 'text-green-700' : 'text-red-600'}`}>{fmt2(sum(dExempt))}</div>
                  <div className="text-xs text-slate-400 mt-0.5">0% tax</div>
                </button>
                <button type="button" disabled={taxableCategories.length === 0}
                  onClick={() => scrollToSection('cg-taxable')}
                  className="bg-slate-50 rounded-lg px-4 py-3 text-left hover:bg-slate-100 disabled:hover:bg-slate-50 disabled:cursor-default transition-colors">
                  <div className="text-xs text-slate-500 mb-1">Taxable Gross Gains</div>
                  <div className="text-xl font-bold tabular-nums text-green-700">{fmt2(grossTaxableGains)}</div>
                  <div className="text-xs text-slate-400 mt-0.5">across all taxable categories</div>
                </button>
                <button type="button" disabled={taxableCategories.length === 0}
                  onClick={() => scrollToSection('cg-taxable')}
                  className="bg-slate-50 rounded-lg px-4 py-3 text-left hover:bg-slate-100 disabled:hover:bg-slate-50 disabled:cursor-default transition-colors">
                  <div className="text-xs text-slate-500 mb-1">Capital Losses (info)</div>
                  <div className="text-xl font-bold tabular-nums text-red-600">{fmt2(grossTaxableLosses)}</div>
                  <div className="text-xs text-slate-400 mt-0.5">not deducted from tax estimate</div>
                </button>
                <button type="button" disabled={taxableCategories.length === 0}
                  onClick={() => scrollToSection('cg-taxable')}
                  className="bg-amber-50 rounded-lg px-4 py-3 text-left hover:bg-amber-100 disabled:hover:bg-amber-50 disabled:cursor-default transition-colors">
                  <div className="text-xs text-amber-600 mb-1">Est. Capital Gains Tax</div>
                  <div className="text-xl font-bold tabular-nums text-amber-700">{fmt2(totalTaxEst)}</div>
                  <div className="text-xs text-slate-400 mt-0.5">on gross gains, per category rates</div>
                </button>
              </div>
            )
          })()}

          <hr className="border-slate-200" />

          {/* Taxable sections — one per tax category */}
          <div id="cg-taxable" className={highlighted === 'cg-taxable' ? 'ring-2 ring-blue-400 rounded-lg p-2 -m-2 space-y-6 transition-shadow' : 'space-y-6 p-2 -m-2 transition-shadow'}>
          {taxableCategories.length === 0 ? (
            <p className="text-sm text-slate-400">No taxable sell transactions found for {year}.</p>
          ) : taxableCategories.map(([cat, { rows: catRows, rate, taxCode }]) => {
            const grossG = catRows.filter(r => Number(r.gain_loss_eur ?? 0) > 0).reduce((s, r) => s + Number(r.gain_loss_eur ?? 0), 0)
            const grossL = catRows.filter(r => Number(r.gain_loss_eur ?? 0) < 0).reduce((s, r) => s + Number(r.gain_loss_eur ?? 0), 0)
            const net    = sum(catRows)
            const taxEst = rate != null ? grossG * (rate / 100) : null
            return (
              <div key={cat} className="space-y-3">
                <h3 className="text-base font-semibold text-red-600">
                  {cat}
                  {rate != null ? ` — ${rate}% CGT` : ' — taxable'}
                  {taxCode && <span className="ml-2 text-xs font-normal text-slate-500">(E1: {taxCode})</span>}
                </h3>
                <div className="flex flex-wrap gap-2">
                  {[
                    { label: 'Gross Gains',       val: grossG,  color: 'text-green-700' },
                    { label: 'Losses (info only)', val: grossL,  color: 'text-red-600' },
                    { label: 'Net G/L',            val: net,     color: net >= 0 ? 'text-green-700' : 'text-red-600' },
                    ...(taxEst != null ? [{ label: `Est. Tax on Gains @ ${rate}%`, val: taxEst, color: 'text-amber-700' }] : []),
                  ].map(({ label, val, color }) => (
                    <div key={label} className="bg-slate-50 rounded px-3 py-2 min-w-[130px]">
                      <div className="text-xs text-slate-500">{label}</div>
                      <div className={`font-semibold tabular-nums text-sm ${color}`}>{fmt2(val)}</div>
                    </div>
                  ))}
                </div>
                <CgTable rows={catRows} method={method} />
                <hr className="border-slate-200" />
              </div>
            )
          })}
          </div>

          {/* Exempt section expander */}
          {dExempt.length > 0 && (() => {
            const exemptGrossG = dExempt.filter(r => Number(r.gain_loss_eur ?? 0) > 0).reduce((s, r) => s + Number(r.gain_loss_eur ?? 0), 0)
            const exemptGrossL = dExempt.filter(r => Number(r.gain_loss_eur ?? 0) < 0).reduce((s, r) => s + Number(r.gain_loss_eur ?? 0), 0)
            const exemptNet    = sum(dExempt)
            return (
              <div id="cg-exempt" className={highlighted === 'cg-exempt' ? 'ring-2 ring-blue-400 rounded-lg p-2 -m-2 space-y-3 transition-shadow' : 'space-y-3 p-2 -m-2 transition-shadow'}>
                <button
                  className="flex items-center gap-2 text-sm font-semibold text-green-700 hover:text-green-800"
                  onClick={() => setShowExempt(v => !v)}
                >
                  <span>{showExempt ? '▾' : '▸'}</span>
                  Tax-Exempt Sales — {dExempt.length} transaction(s) (excluded from all totals)
                </button>
                <div className="flex flex-wrap gap-2">
                  {[
                    { label: 'Gross Gains',        val: exemptGrossG, color: 'text-green-700' },
                    { label: 'Losses (info only)',  val: exemptGrossL, color: 'text-red-600' },
                    { label: 'Net G/L',             val: exemptNet,    color: exemptNet >= 0 ? 'text-green-700' : 'text-red-600' },
                  ].map(({ label, val, color }) => (
                    <div key={label} className="bg-slate-50 rounded px-3 py-2 min-w-[130px]">
                      <div className="text-xs text-slate-500">{label}</div>
                      <div className={`font-semibold tabular-nums text-sm ${color}`}>{fmt2(val)}</div>
                    </div>
                  ))}
                </div>
                {showExempt && (
                  <div className="space-y-2">
                    <p className="text-xs text-slate-500">
                      Categories with <strong>Gains Taxable = No</strong> (e.g. Local Listed, Foreign Listed, UCITS) or securities marked Tax Exempt. Not included in any taxable total above.
                    </p>
                    <CgTable rows={dExempt} method={method} />
                  </div>
                )}
              </div>
            )
          })()}

          {/* Reference note */}
          <div className="bg-blue-50 border border-blue-200 rounded-lg px-4 py-3 text-xs text-blue-800 space-y-1">
            <div><strong>Greek CGT Quick Reference — all figures are indicative, consult a certified Greek tax advisor.</strong></div>
            <div>
              <strong>Exempt (0% CGT):</strong> Local Listed, Foreign Listed, UCITS — direct holdings only (Art. 42, L.4172/2013).
              Gains must still be declared in <strong>E1 Table 4E, Codes 659–660</strong> to clear living-standard presumptions (<em>τεκμήρια</em>).
            </div>
            <div>
              <strong>Taxable (15% CGT):</strong> Non-UCITS funds/ETFs, CFDs, FX Spot — gains → <strong>E1 Codes 865–866</strong>.
              Losses within the same category → <strong>E1 Codes 869–870</strong> (carry-forward within same category, ≤ 5 years). Tax is computed on <em>gross gains</em>; losses do not offset gains in this report.
            </div>
            <div>
              <strong>Crypto (15% CGT):</strong> Digital assets taxed under Art. 42A, L.4172/2013. Same 15% rate; separate E1 declaration.
            </div>
            <div>
              <strong>Bonds / CDs:</strong> Excluded from Capital Gains — coupon and maturity interest reported under Interest &amp; Dividend Income.
              Tax rates and categories are configurable in <strong>Static Data → Tax Rules</strong>.
            </div>
          </div>

        </>
      )}
    </div>
  )
}

function TaxLossHarvestingTab() {
  const { data = [], isLoading } = useQuery({ queryKey: ['tax-loss-harvesting'], queryFn: getTaxLossHarvesting })
  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  const rows = data as Row[]
  const totalLoss = rows.reduce((s, r) => s + Number(r.unrealized_loss_eur ?? 0), 0)
  return (
    <div className="space-y-4">
      <KpiCard label="Total Harvestable Loss" value={fmtEur(totalLoss)} color="text-red-600" />
      {rows.length === 0 ? (
        <p className="text-slate-500 text-sm py-4">No positions with unrealized losses.</p>
      ) : (
        <WithCopy>
        <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-300px)] text-xs">
          <table className="w-full border-collapse">
            <thead className="sticky top-0 z-10"><tr className="bg-slate-50">
              <th className="text-left px-2 py-1.5 border-b border-slate-200 font-semibold">Security</th>
              <th className="text-left px-2 py-1.5 border-b border-slate-200 font-semibold">Ticker</th>
              <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold">Qty</th>
              <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold">Cur. Price</th>
              <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold">Cost Basis</th>
              <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold">Cur. Value</th>
              <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold">Cost Total</th>
              <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold">Loss (€)</th>
              <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold">Loss %</th>
            </tr></thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i} className="border-b border-slate-100 hover:bg-slate-50">
                  <td className="px-2 py-1.5 font-medium"><SecLink id={r.securities_id}>{String(r.securities_name)}</SecLink></td>
                  <td className="px-2 py-1.5 font-mono text-slate-500"><SecLink id={r.securities_id}>{String(r.ticker ?? '—')}</SecLink></td>
                  <td className="px-2 py-1.5 text-right tabular-nums">{Number(r.quantity).toFixed(4)}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums">{fmtEur(Number(r.current_price ?? 0))}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums">{fmtEur(Number(r.cost_basis ?? 0))}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums">{fmtEur(Number(r.current_value_eur ?? 0))}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums">{fmtEur(Number(r.cost_basis_eur ?? 0))}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums text-red-600 font-medium">{fmtEur(Number(r.unrealized_loss_eur ?? 0))}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums text-red-600">{Number(r.loss_pct ?? 0).toFixed(2)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        </WithCopy>
      )}

      {/* Reference note */}
      <div className="bg-blue-50 border border-blue-200 rounded-lg px-4 py-3 text-xs text-blue-800 space-y-1">
        <div><strong>Tax-Loss Harvesting Quick Reference — all figures are indicative, consult a certified Greek tax advisor.</strong></div>
        <div>
          <strong>What is it?</strong> Selling a position at a loss before year-end to realise a taxable loss.
          Under Greek law, losses within a taxable category (e.g. Non-UCITS, CFD, FX Spot, Crypto) can be carried forward for up to <strong>5 years</strong> and offset future gains in the <em>same category</em> (Art. 42, L.4172/2013).
        </div>
        <div>
          <strong>Exempt categories (Local Listed, Foreign Listed, UCITS):</strong> No CGT applies, so realising losses in these categories has no tax benefit. Losses cannot offset gains in taxable categories.
        </div>
        <div>
          <strong>Wash-sale rule:</strong> Greece does not have an explicit wash-sale rule equivalent to the US 30-day rule, but repurchasing the same security immediately may be challenged by AADE on substance grounds. Consult your advisor before re-entering a harvested position.
        </div>
        <div>
          <strong>Timing:</strong> The sale must settle before 31 December to count for the current tax year. Losses are declared in <strong>E1 Codes 869–870</strong> for derivatives / CFDs / FX Spot / Crypto.
        </div>
      </div>
    </div>
  )
}

function IncomeDetailRows({ rows, showSecLink, showIncomeTax = false }: { rows: Row[]; showSecLink: boolean; showIncomeTax?: boolean }) {
  const hasTax = rows.some(r => r.tax_amount_eur != null)
  const hasLib = rows.some(r => r.local_tax_liability != null && Number(r.local_tax_liability) > 0)
  const hasIntTax = showIncomeTax && rows.some(r => r.income_tax_liability != null && Number(r.income_tax_liability) > 0)
  return (
    <div className="overflow-x-auto text-xs ml-4 mt-1 mb-2">
      <table className="w-full border-collapse">
        <thead><tr className="bg-slate-100 text-slate-500 uppercase tracking-wide">
          <th className="text-left px-2 py-1 border-b border-slate-200">Date</th>
          {showSecLink && <th className="text-left px-2 py-1 border-b border-slate-200">Security</th>}
          {!showSecLink && <th className="text-left px-2 py-1 border-b border-slate-200">Bank / Payee</th>}
          {!showSecLink && <th className="text-left px-2 py-1 border-b border-slate-200">Category</th>}
          <th className="text-left px-2 py-1 border-b border-slate-200">Account</th>
          <th className="text-left px-2 py-1 border-b border-slate-200">Type</th>
          {showSecLink && <th className="text-left px-2 py-1 border-b border-slate-200">Tax Cat.</th>}
          <th className="text-right px-2 py-1 border-b border-slate-200">Gross (€)</th>
          {hasTax && <th className="text-right px-2 py-1 border-b border-slate-200">WHT (€)</th>}
          {hasTax && <th className="text-right px-2 py-1 border-b border-slate-200">Net (€)</th>}
          {hasLib && <th className="text-right px-2 py-1 border-b border-slate-200 text-amber-600">Div Local Tax (€)</th>}
          {hasIntTax && <th className="text-right px-2 py-1 border-b border-slate-200 text-amber-600">Int. Tax (€)</th>}
        </tr></thead>
        <tbody>
          {rows.map((r, i) => {
            const gross = Number(r.amount_eur ?? 0)
            const tax = r.tax_amount_eur != null ? Number(r.tax_amount_eur) : null
            const net = tax != null ? gross + tax : null
            const lib = r.local_tax_liability != null ? Number(r.local_tax_liability) : null
            const itax = r.income_tax_liability != null ? Number(r.income_tax_liability) : null
            return (
              <tr key={i} className="border-b border-slate-100 hover:bg-slate-50">
                <td className="px-2 py-1 text-slate-500">{String(r.date ?? '').slice(0, 10)}</td>
                {showSecLink && <td className="px-2 py-1 font-medium"><SecLink id={r.securities_id}>{String(r.securities_name ?? r.security ?? '')}</SecLink></td>}
                {!showSecLink && <td className="px-2 py-1 text-slate-500">{String(r.payee ?? '—')}</td>}
                {!showSecLink && <td className="px-2 py-1 text-slate-500">{String(r.category ?? '')}</td>}
                <td className="px-2 py-1 text-slate-500"><AccountLink id={r.accounts_id as number} name={String(r.account_name ?? '')} type={showSecLink ? 'Brokerage' : String(r.account_type ?? '')} /></td>
                <td className="px-2 py-1 text-slate-500">{String(r.action ?? r.currency ?? '')}</td>
                {showSecLink && <td className="px-2 py-1 text-slate-400 text-xs">{String(r.tax_category ?? '—')}</td>}
                <td className={`px-2 py-1 text-right tabular-nums font-medium ${gross < 0 ? 'text-red-600' : 'text-green-700'}`}>{fmtEur(gross)}</td>
                {hasTax && <td className="px-2 py-1 text-right tabular-nums text-red-600">{tax != null ? fmtEur(tax) : '—'}</td>}
                {hasTax && <td className={`px-2 py-1 text-right tabular-nums font-semibold ${(net ?? gross) < 0 ? 'text-red-600' : 'text-green-700'}`}>{net != null ? fmtEur(net) : fmtEur(gross)}</td>}
                {hasLib && <td className={`px-2 py-1 text-right tabular-nums ${lib != null && lib > 0 ? 'text-amber-700 font-semibold' : 'text-slate-300'}`}>{lib != null && lib > 0 ? fmtEur(lib) : '—'}</td>}
                {hasIntTax && <td className={`px-2 py-1 text-right tabular-nums ${itax != null && itax > 0 ? 'text-amber-700 font-semibold' : 'text-slate-300'}`}>{itax != null && itax > 0 ? fmtEur(itax) : '—'}</td>}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function IncomeTable({ rows, showSecLink = true, showIncomeTax = false }: { rows: Row[]; showSecLink?: boolean; showIncomeTax?: boolean }) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const toggle = (key: string) => setExpanded(prev => { const s = new Set(prev); s.has(key) ? s.delete(key) : s.add(key); return s })

  type Group = { key: string; label: string; account: string; total: number; taxTotal: number | null; libTotal: number | null; intTaxTotal: number | null; rows: Row[] }
  const groups = useMemo<Group[]>(() => {
    const map = new Map<string, Group>()
    for (const r of rows) {
      const label = showSecLink
        ? String(r.securities_name ?? r.security ?? '—')
        : String(r.payee ?? r.category ?? '—')
      const account = String(r.account_name ?? '')
      const key = `${label}||${account}`
      if (!map.has(key)) map.set(key, { key, label, account, total: 0, taxTotal: null, libTotal: null, intTaxTotal: null, rows: [] })
      const g = map.get(key)!
      g.total += Number(r.amount_eur ?? 0)
      if (r.tax_amount_eur != null) g.taxTotal = (g.taxTotal ?? 0) + Number(r.tax_amount_eur)
      if (r.local_tax_liability != null) g.libTotal = (g.libTotal ?? 0) + Number(r.local_tax_liability)
      if (r.income_tax_liability != null) g.intTaxTotal = (g.intTaxTotal ?? 0) + Number(r.income_tax_liability)
      g.rows.push(r)
    }
    return [...map.values()].sort((a, b) => Math.abs(b.total) - Math.abs(a.total))
  }, [rows, showSecLink])

  const grandTotal = groups.reduce((s, g) => s + g.total, 0)
  const grandTax = groups.some(g => g.taxTotal != null) ? groups.reduce((s, g) => s + (g.taxTotal ?? 0), 0) : null
  const grandLib = groups.some(g => g.libTotal != null && g.libTotal > 0) ? groups.reduce((s, g) => s + (g.libTotal ?? 0), 0) : null
  const grandIntTax = showIncomeTax && groups.some(g => g.intTaxTotal != null && g.intTaxTotal > 0) ? groups.reduce((s, g) => s + (g.intTaxTotal ?? 0), 0) : null
  const hasTax = grandTax != null
  const hasLib = grandLib != null && grandLib > 0
  const hasIntTax = grandIntTax != null && grandIntTax > 0
  const colSpanTotal = 4 + (hasTax ? 2 : 0) + (hasLib ? 1 : 0) + (hasIntTax ? 1 : 0)

  return (
    <div className="text-xs border border-slate-200 rounded-lg overflow-hidden">
      <table className="w-full border-collapse">
        <thead><tr className="bg-slate-50 text-slate-500 uppercase tracking-wide text-xs">
          <th className="text-left px-3 py-2 border-b border-slate-200 w-6"></th>
          <th className="text-left px-3 py-2 border-b border-slate-200">{showSecLink ? 'Security' : 'Payee / Source'}</th>
          <th className="text-left px-3 py-2 border-b border-slate-200">Account</th>
          <th className="text-right px-3 py-2 border-b border-slate-200">Txns</th>
          <th className="text-right px-3 py-2 border-b border-slate-200">Gross (€)</th>
          {hasTax && <th className="text-right px-3 py-2 border-b border-slate-200">WHT (€)</th>}
          {hasTax && <th className="text-right px-3 py-2 border-b border-slate-200">Net (€)</th>}
          {hasLib && <th className="text-right px-3 py-2 border-b border-slate-200 text-amber-600">Div Local Tax (€)</th>}
          {hasIntTax && <th className="text-right px-3 py-2 border-b border-slate-200 text-amber-600">Int. Tax (€)</th>}
        </tr></thead>
        <tbody>
          {groups.map(g => {
            const net = g.taxTotal != null ? g.total + g.taxTotal : null
            return (
              <>
                <tr key={g.key} className="border-b border-slate-100 hover:bg-slate-50 cursor-pointer" onClick={() => toggle(g.key)}>
                  <td className="px-3 py-2 text-slate-400">{expanded.has(g.key) ? '▾' : '▸'}</td>
                  <td className="px-3 py-2 font-medium">
                    {showSecLink ? <SecLink id={g.rows[0].securities_id}>{g.label}</SecLink> : g.label}
                  </td>
                  <td className="px-3 py-2 text-slate-500"><AccountLink id={g.rows[0]?.accounts_id as number} name={g.account} type={showSecLink ? 'Brokerage' : String(g.rows[0]?.account_type ?? '')} /></td>
                  <td className="px-3 py-2 text-right text-slate-500">{g.rows.length}</td>
                  <td className={`px-3 py-2 text-right tabular-nums font-semibold ${g.total < 0 ? 'text-red-600' : 'text-green-700'}`}>{fmtEur(g.total)}</td>
                  {hasTax && <td className="px-3 py-2 text-right tabular-nums text-red-600">{g.taxTotal != null ? fmtEur(g.taxTotal) : '—'}</td>}
                  {hasTax && <td className={`px-3 py-2 text-right tabular-nums font-semibold ${(net ?? g.total) < 0 ? 'text-red-600' : 'text-green-700'}`}>{net != null ? fmtEur(net) : fmtEur(g.total)}</td>}
                  {hasLib && <td className={`px-3 py-2 text-right tabular-nums ${(g.libTotal ?? 0) > 0 ? 'text-amber-700 font-semibold' : 'text-slate-300'}`}>{(g.libTotal ?? 0) > 0 ? fmtEur(g.libTotal!) : '—'}</td>}
                  {hasIntTax && <td className={`px-3 py-2 text-right tabular-nums ${(g.intTaxTotal ?? 0) > 0 ? 'text-amber-700 font-semibold' : 'text-slate-300'}`}>{(g.intTaxTotal ?? 0) > 0 ? fmtEur(g.intTaxTotal!) : '—'}</td>}
                </tr>
                {expanded.has(g.key) && (
                  <tr key={g.key + '_detail'}>
                    <td colSpan={colSpanTotal + 1} className="bg-slate-50 border-b border-slate-200 p-0">
                      <IncomeDetailRows rows={g.rows} showSecLink={showSecLink} showIncomeTax={showIncomeTax} />
                    </td>
                  </tr>
                )}
              </>
            )
          })}
          <tr className="bg-slate-50 font-semibold border-t-2 border-slate-300">
            <td className="px-3 py-2" colSpan={4}>Total</td>
            <td className={`px-3 py-2 text-right tabular-nums ${grandTotal < 0 ? 'text-red-600' : 'text-green-700'}`}>{fmtEur(grandTotal)}</td>
            {hasTax && <td className="px-3 py-2 text-right tabular-nums text-red-600">{fmtEur(grandTax!)}</td>}
            {hasTax && <td className={`px-3 py-2 text-right tabular-nums ${(grandTotal + grandTax!) < 0 ? 'text-red-600' : 'text-green-700'}`}>{fmtEur(grandTotal + grandTax!)}</td>}
            {hasLib && <td className="px-3 py-2 text-right tabular-nums text-amber-700">{fmtEur(grandLib!)}</td>}
            {hasIntTax && <td className="px-3 py-2 text-right tabular-nums text-amber-700">{fmtEur(grandIntTax!)}</td>}
          </tr>
        </tbody>
      </table>
    </div>
  )
}

function DividendIncomeTaxTab({ year }: { year: number }) {
  const [showRoc, setShowRoc] = useState(true)
  const [highlighted, setHighlighted] = useState<string | null>(null)
  const scrollToSection = (id: string, expand?: () => void) => {
    expand?.()
    window.setTimeout(() => {
      const el = document.getElementById(id)
      if (!el) return
      el.scrollIntoView({ behavior: 'smooth', block: 'start' })
      setHighlighted(id)
      window.setTimeout(() => setHighlighted(h => h === id ? null : h), 1500)
    }, expand ? 50 : 0)
  }
  const qc = useQueryClient()
  useEffect(() => { qc.removeQueries({ queryKey: ['bank-interest-tax'] }) }, [])
  const invQ = useQuery({ queryKey: ['dividend-income-tax', year], queryFn: () => getDividendIncomeTax(year) })
  const bankQ = useQuery({ queryKey: ['bank-interest-tax', year], queryFn: () => api.get('/reports/bank-interest-tax', { params: { year } }).then(r => r.data), staleTime: 0, gcTime: 0 })
  if (invQ.isLoading || bankQ.isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  const invRows  = Array.isArray(invQ.data)  ? invQ.data  as Row[] : []
  const bankRows = Array.isArray(bankQ.data) ? bankQ.data as Row[] : []

  const isExempt = (r: Row) => r.is_tax_exempt === true || r.is_tax_exempt === 'true' || r.is_tax_exempt === 1

  // Backend already filtered out non-taxable Reinvest; split by section + exempt flag
  const divRows      = invRows.filter(r => r.section === 'dividend' && !isExempt(r) && r.action !== 'RtrnCap')
  const divExempt    = invRows.filter(r => r.section === 'dividend' &&  isExempt(r) && r.action !== 'RtrnCap')
  const intInvRows   = invRows.filter(r => r.section === 'interest'  && !isExempt(r))
  const intExempt    = invRows.filter(r => r.section === 'interest'  &&  isExempt(r))  // T-bills & exempt bonds
  const invRoc       = invRows.filter(r => r.action  === 'RtrnCap')

  const sum       = (rows: Row[]) => rows.reduce((s, r) => s + Number(r.amount_eur ?? 0), 0)
  const sumTax    = (rows: Row[]) => rows.reduce((s, r) => s + Number(r.tax_amount_eur ?? 0), 0)
  const sumLib    = (rows: Row[]) => rows.reduce((s, r) => s + Number(r.local_tax_liability ?? 0), 0)
  const sumIntTax = (rows: Row[]) => rows.reduce((s, r) => s + Number(r.income_tax_liability ?? 0), 0)

  const totalDiv     = sum(divRows)
  const totalIntInv  = sum(intInvRows)
  const totalExempt  = sum(divExempt) + sum(intExempt)
  const totalRoc     = sum(invRoc)
  const totalBank    = sum(bankRows)
  const grandTotal   = totalDiv + totalIntInv + totalBank
  const totalWithheld        = sumTax([...divRows, ...divExempt])
  const totalLocalLiability  = sumLib([...divRows, ...intInvRows])
  const totalIntTaxLiability = sumIntTax(intInvRows)

  const fmt2 = (n: number) => `€ ${fmtNum(n, 2)}`

  return (
    <div className="space-y-5">
      <p className="text-xs text-slate-500">
        Taxable income for the selected tax year.{' '}
        <strong>Dividend Income</strong> uses the effective tax category per transaction (instrument-type override → security category).
        Reinvested dividends are excluded for UCITS and Local/Foreign Listed (not a taxable event).
        CD/Bond interest appears in its own section at the applicable income tax rate.
        All amounts are converted to EUR.
      </p>

      {/* Headline metrics */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[
          { label: 'Dividend Income',         val: totalDiv,    target: 'dit-dividend' },
          { label: 'CD / Bond Interest',      val: totalIntInv, target: 'dit-cdbond' },
          { label: 'Bank / Savings Interest', val: totalBank,   target: 'dit-bank' },
          { label: 'Taxable Total',           val: grandTotal,  target: 'dit-dividend' },
        ].map(({ label, val, target }) => (
          <button key={label} type="button" onClick={() => scrollToSection(target)}
            className="bg-slate-50 rounded-lg px-4 py-3 text-left hover:bg-slate-100 transition-colors">
            <div className="text-xs text-slate-500 mb-1">{label}</div>
            <div className="text-xl font-bold tabular-nums text-slate-800">{fmt2(val)}</div>
          </button>
        ))}
      </div>

      {/* WHT + local liability row */}
      {(totalWithheld !== 0 || totalLocalLiability > 0) && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {totalWithheld !== 0 && (
            <button type="button" onClick={() => scrollToSection('dit-dividend')}
              className="bg-red-50 rounded-lg px-4 py-3 text-left hover:bg-red-100 transition-colors">
              <div className="text-xs text-red-500 mb-1">Total Withholding Tax</div>
              <div className="text-xl font-bold tabular-nums text-red-700">{fmt2(totalWithheld)}</div>
            </button>
          )}
          {totalWithheld !== 0 && (
            <button type="button" onClick={() => scrollToSection('dit-dividend')}
              className="bg-slate-50 rounded-lg px-4 py-3 text-left hover:bg-slate-100 transition-colors">
              <div className="text-xs text-slate-500 mb-1">Net After Withholding</div>
              <div className="text-xl font-bold tabular-nums text-slate-800">{fmt2(grandTotal + totalWithheld)}</div>
            </button>
          )}
          {totalLocalLiability > 0 && (
            <button type="button" onClick={() => scrollToSection('dit-dividend')}
              className="bg-amber-50 rounded-lg px-4 py-3 text-left hover:bg-amber-100 transition-colors">
              <div className="text-xs text-amber-600 mb-1">Dividend Local Tax Liability</div>
              <div className="text-xl font-bold tabular-nums text-amber-700">{fmt2(totalLocalLiability)}</div>
              <div className="text-xs text-amber-500 mt-1">max(0, gross × local rate − WHT credited)</div>
            </button>
          )}
          {totalIntTaxLiability > 0 && (
            <button type="button" onClick={() => scrollToSection('dit-cdbond')}
              className="bg-amber-50 rounded-lg px-4 py-3 text-left hover:bg-amber-100 transition-colors">
              <div className="text-xs text-amber-600 mb-1">CD / Bond Interest Tax (15%)</div>
              <div className="text-xl font-bold tabular-nums text-amber-700">{fmt2(totalIntTaxLiability)}</div>
              <div className="text-xs text-amber-500 mt-1">max(0, gross × 15% − WHT withheld)</div>
            </button>
          )}
        </div>
      )}

      {/* Tax-exempt banner */}
      {totalExempt !== 0 && (
        <div className="bg-green-50 border border-green-200 rounded-lg px-4 py-3 text-sm text-green-800">
          <strong>Tax-Exempt Investment Income: {fmt2(totalExempt)}</strong> — excluded from taxable total. Shown separately below.
        </div>
      )}

      {/* RtrnCap banner */}
      {totalRoc !== 0 && (
        <div className="bg-blue-50 border border-blue-200 rounded-lg px-4 py-3 text-sm text-blue-800">
          <strong>Return of Capital (RtrnCap): {fmt2(totalRoc)}</strong> — not taxable income; reduces cost basis. Shown below for reference.
        </div>
      )}

      {/* Dividend Income */}
      <div id="dit-dividend" className={highlighted === 'dit-dividend' ? 'ring-2 ring-blue-400 rounded-lg p-2 -m-2 space-y-2 transition-shadow' : 'space-y-2 p-2 -m-2 transition-shadow'}>
        <h3 className="text-sm font-semibold text-slate-700">Dividend Income (incl. taxable Reinvest)</h3>
        {divRows.length === 0
          ? <p className="text-xs text-slate-400">No taxable dividend income for {year}.</p>
          : <IncomeTable rows={divRows} />}
      </div>

      {/* CD / Bond Interest */}
      {(intInvRows.length > 0 || intExempt.length > 0) && (
        <div id="dit-cdbond" className={highlighted === 'dit-cdbond' ? 'ring-2 ring-blue-400 rounded-lg p-2 -m-2 space-y-2 transition-shadow' : 'space-y-2 p-2 -m-2 transition-shadow'}>
          <h3 className="text-sm font-semibold text-slate-700">CD / Bond Interest Income</h3>
          {intInvRows.length > 0 && <IncomeTable rows={intInvRows} showIncomeTax />}
          {intExempt.length > 0 && (
            <>
              <p className="text-xs text-green-700 font-medium mt-2">Tax-Exempt Interest (T-Bills, Exempt Bonds)</p>
              <IncomeTable rows={intExempt} />
            </>
          )}
        </div>
      )}

      {/* Tax-Exempt */}
      {divExempt.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-sm font-semibold text-green-700">Tax-Exempt Investment Income (reference only)</h3>
          <IncomeTable rows={divExempt} />
        </div>
      )}

      {/* Return of Capital */}
      {invRoc.length > 0 && (
        <div>
          <button className="flex items-center gap-2 text-sm font-medium text-slate-600 hover:text-slate-800" onClick={() => setShowRoc(v => !v)}>
            <span>{showRoc ? '▾' : '▸'}</span>
            Return of Capital — {fmt2(totalRoc)} (not taxable income)
          </button>
          {showRoc && <div className="mt-2"><IncomeTable rows={invRoc} /></div>}
        </div>
      )}

      {/* Bank & Savings Interest */}
      <div id="dit-bank" className={highlighted === 'dit-bank' ? 'ring-2 ring-blue-400 rounded-lg p-2 -m-2 space-y-2 transition-shadow' : 'space-y-2 p-2 -m-2 transition-shadow'}>
        <h3 className="text-sm font-semibold text-slate-700">Bank &amp; Savings Interest</h3>
        {bankRows.length === 0
          ? <p className="text-xs text-slate-400">No bank or savings interest found for {year}.</p>
          : <IncomeTable rows={bankRows} showSecLink={false} />}
      </div>

      {/* Reference note */}
      <div className="bg-blue-50 border border-blue-200 rounded-lg px-4 py-3 text-xs text-blue-800 space-y-1">
        <div><strong>Greek Income Tax Quick Reference — all figures are indicative, consult a certified Greek tax advisor.</strong></div>
        <div>
          <strong>Dividends (5%):</strong> Greek-source dividends are subject to <strong>5% withholding tax</strong> (Art. 36, L.4172/2013), withheld at source.
          Foreign dividends are grossed up and taxed at 5%; foreign WHT is credited up to the Greek rate.
          Declare in <strong>E1 Table 4D, Codes 289–294</strong> (foreign) or <strong>Codes 285–288</strong> (domestic).
        </div>
        <div>
          <strong>CD / Bond Coupon Interest (15%):</strong> Interest from time deposits and bonds is taxed at <strong>15%</strong>, withheld at source by the paying institution (Art. 40, L.4172/2013).
          T-bill discount at maturity is tax-exempt for Greek government securities (Is_Tax_Exempt flag).
          Declare interest income in <strong>E1 Table 4Δ, Codes 595–596</strong>.
        </div>
        <div>
          <strong>Bank / Savings Interest (15%):</strong> Taxed at <strong>15%</strong>, withheld at source. Same declaration as bond interest.
        </div>
        <div>
          <strong>Return of Capital:</strong> Not income — reduces your cost basis in the security. No tax due in the year received; affects capital gains calculation on future sale.
        </div>
        <div>
          <strong>Reinvested dividends:</strong> Excluded for UCITS, Local Listed and Foreign Listed (scrip/DRIP — not a taxable income event in Greece). Configurable per category in <strong>Static Data → Tax Rules</strong>.
        </div>
      </div>
    </div>
  )
}

function TaxSection() {
  const [tab, setTab] = usePersist('tax_tab', 'Capital Gains')
  const [year, setYear] = usePersist('tax_year', new Date().getFullYear() - 1)
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <label className="text-xs text-slate-500 font-medium">Tax Year</label>
        <Input type="number" className="w-24" value={year} onChange={e => setYear(Number(e.target.value))} />
      </div>
      <SubTabs tabs={['Capital Gains', 'Interest & Dividend Income', 'Tax-Loss Harvesting']} active={tab} onChange={setTab} />
      {tab === 'Capital Gains' && <CapitalGainsReport year={year} />}
      {(tab === 'Interest & Dividend Income' || tab === 'Dividend Income') && <DividendIncomeTaxTab year={year} />}
      {tab === 'Tax-Loss Harvesting' && <TaxLossHarvestingTab />}
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════════
// 9. FINANCIAL PLANNING
// ════════════════════════════════════════════════════════════════════════════
interface GoalRow { goal_id: number; goal_name: string; target_amount: number; current_amount: number; target_date: string | null; progress_pct: number; notes: string | null }

function GoalsTab() {
  const qc = useQueryClient()
  const { data = [], isLoading } = useQuery({ queryKey: ['goals'], queryFn: getGoals })
  const upsertMut = useMutation({ mutationFn: upsertGoal, onSuccess: () => qc.invalidateQueries({ queryKey: ['goals'] }) })
  const deleteMut = useMutation({ mutationFn: deleteGoal, onSuccess: () => qc.invalidateQueries({ queryKey: ['goals'] }) })

  const [editId, setEditId] = useState<number | null>(null)
  const [showAdd, setShowAdd] = useState(false)
  const emptyForm = { goal_name: '', target_amount: '', current_amount: '0', target_date: '', notes: '' }
  const [form, setForm] = useState(emptyForm)

  const goals = data as GoalRow[]

  const save = (goalId?: number) => {
    upsertMut.mutate({ ...(goalId ? { goal_id: goalId } : {}), goal_name: form.goal_name, target_amount: Number(form.target_amount), current_amount: Number(form.current_amount), target_date: form.target_date || null, notes: form.notes || null })
    setEditId(null); setShowAdd(false); setForm(emptyForm)
  }

  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>

  return (
    <div className="space-y-4">
      <Button size="sm" onClick={() => { setShowAdd(true); setEditId(null); setForm(emptyForm) }} className="flex items-center gap-1">
        <Plus size={14} /> Add Goal
      </Button>

      {showAdd && (
        <div className="border border-blue-200 rounded-lg p-4 bg-blue-50 space-y-3">
          <p className="text-sm font-semibold text-blue-800">New Goal</p>
          <div className="grid grid-cols-2 gap-3">
            <div><label className="text-xs text-slate-500">Name</label><Input value={form.goal_name} onChange={e => setForm(f => ({ ...f, goal_name: e.target.value }))} placeholder="e.g. Emergency Fund" /></div>
            <div><label className="text-xs text-slate-500">Target (€)</label><Input type="number" value={form.target_amount} onChange={e => setForm(f => ({ ...f, target_amount: e.target.value }))} /></div>
            <div><label className="text-xs text-slate-500">Current (€)</label><Input type="number" value={form.current_amount} onChange={e => setForm(f => ({ ...f, current_amount: e.target.value }))} /></div>
            <div><label className="text-xs text-slate-500">Target Date</label><Input type="date" value={form.target_date} onChange={e => setForm(f => ({ ...f, target_date: e.target.value }))} /></div>
            <div className="col-span-2"><label className="text-xs text-slate-500">Notes</label><Input value={form.notes} onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} /></div>
          </div>
          <div className="flex gap-2">
            <Button size="sm" onClick={() => save()}>Save</Button>
            <Button size="sm" variant="ghost" onClick={() => setShowAdd(false)}>Cancel</Button>
          </div>
        </div>
      )}

      <div className="space-y-3">
        {goals.map(g => {
          const remaining = g.target_amount - g.current_amount
          const isEdit = editId === g.goal_id
          return (
            <div key={g.goal_id} className="border border-slate-200 rounded-lg p-4">
              {isEdit ? (
                <div className="space-y-3">
                  <div className="grid grid-cols-2 gap-3">
                    <div><label className="text-xs text-slate-500">Name</label><Input value={form.goal_name} onChange={e => setForm(f => ({ ...f, goal_name: e.target.value }))} /></div>
                    <div><label className="text-xs text-slate-500">Target (€)</label><Input type="number" value={form.target_amount} onChange={e => setForm(f => ({ ...f, target_amount: e.target.value }))} /></div>
                    <div><label className="text-xs text-slate-500">Current (€)</label><Input type="number" value={form.current_amount} onChange={e => setForm(f => ({ ...f, current_amount: e.target.value }))} /></div>
                    <div><label className="text-xs text-slate-500">Target Date</label><Input type="date" value={form.target_date} onChange={e => setForm(f => ({ ...f, target_date: e.target.value }))} /></div>
                    <div className="col-span-2"><label className="text-xs text-slate-500">Notes</label><Input value={form.notes} onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} /></div>
                  </div>
                  <div className="flex gap-2">
                    <Button size="sm" onClick={() => save(g.goal_id)}>Save</Button>
                    <Button size="sm" variant="ghost" onClick={() => setEditId(null)}>Cancel</Button>
                  </div>
                </div>
              ) : (
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <h3 className="font-semibold text-slate-800">{g.goal_name}</h3>
                    <div className="flex items-center gap-2">
                      <button onClick={() => { setEditId(g.goal_id); setShowAdd(false); setForm({ goal_name: g.goal_name, target_amount: String(g.target_amount), current_amount: String(g.current_amount), target_date: g.target_date ?? '', notes: g.notes ?? '' }) }}
                        className="text-xs text-blue-600 hover:underline">Edit</button>
                      <button onClick={() => deleteMut.mutate(g.goal_id)} className="text-slate-400 hover:text-red-500"><Trash2 size={14} /></button>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="flex-1 h-2 bg-slate-200 rounded-full overflow-hidden">
                      <div className={`h-full rounded-full ${g.progress_pct >= 100 ? 'bg-green-500' : 'bg-blue-500'}`} style={{ width: `${Math.min(g.progress_pct, 100)}%` }} />
                    </div>
                    <span className="text-xs font-medium text-slate-600 tabular-nums">{g.progress_pct.toFixed(1)}%</span>
                  </div>
                  <div className="grid grid-cols-3 gap-3 text-xs">
                    <div><span className="text-slate-500">Current: </span><span className="font-medium">{fmtEur(g.current_amount)}</span></div>
                    <div><span className="text-slate-500">Target: </span><span className="font-medium">{fmtEur(g.target_amount)}</span></div>
                    <div><span className="text-slate-500">Remaining: </span><span className="font-medium text-amber-600">{fmtEur(remaining)}</span></div>
                    {g.target_date && <div className="col-span-2"><span className="text-slate-500">By: </span><span>{g.target_date.slice(0, 10)}</span></div>}
                  </div>
                  {g.notes && <p className="text-xs text-slate-500 italic">{g.notes}</p>}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

function FireCalculatorTab() {
  const { isDark } = useTheme()
  const [portfolio, setPortfolio] = useState(500000)
  const [monthlySavings, setMonthlySavings] = useState(2000)
  const [annualReturn, setAnnualReturn] = useState(7)
  const [annualExpenses, setAnnualExpenses] = useState(36000)
  const [swr, setSwr] = useState(4)

  const fireNumber = annualExpenses / (swr / 100)
  const progress = portfolio / fireNumber * 100
  const r = annualReturn / 100 / 12

  // Simulate months to FIRE
  const yearsToFire = useMemo(() => {
    if (portfolio >= fireNumber) return 0
    let val = portfolio; let months = 0
    while (val < fireNumber && months < 600) {
      val = val * (1 + r) + monthlySavings
      months++
    }
    return months < 600 ? months / 12 : null
  }, [portfolio, monthlySavings, r, fireNumber])

  // Projection chart (10 years)
  const projYears = Math.max(Math.ceil(yearsToFire ?? 30), 10)
  const chartX: string[] = []; const chartY: number[] = []
  let val = portfolio
  for (let m = 0; m <= projYears * 12; m++) {
    if (m % 6 === 0) { chartX.push(`Y${(m/12).toFixed(1)}`); chartY.push(Math.round(val)) }
    val = val * (1 + r) + monthlySavings
  }

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
        <div><label className="text-xs text-slate-500 block mb-1">Current Portfolio (€)</label><Input type="number" value={portfolio} onChange={e => setPortfolio(Number(e.target.value))} /></div>
        <div><label className="text-xs text-slate-500 block mb-1">Monthly Savings (€)</label><Input type="number" value={monthlySavings} onChange={e => setMonthlySavings(Number(e.target.value))} /></div>
        <div><label className="text-xs text-slate-500 block mb-1">Annual Return (%)</label><Input type="number" value={annualReturn} onChange={e => setAnnualReturn(Number(e.target.value))} step="0.5" /></div>
        <div><label className="text-xs text-slate-500 block mb-1">Annual Expenses (€)</label><Input type="number" value={annualExpenses} onChange={e => setAnnualExpenses(Number(e.target.value))} /></div>
        <div><label className="text-xs text-slate-500 block mb-1">Withdrawal Rate (%)</label><Input type="number" value={swr} onChange={e => setSwr(Number(e.target.value))} step="0.25" /></div>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <KpiCard label="FIRE Number" value={fmtEur(fireNumber)} color="text-purple-700" />
        <KpiCard label="Progress to FIRE" value={`${Math.min(progress, 100).toFixed(1)}%`} color={progress >= 100 ? 'text-green-700' : 'text-blue-700'} />
        <KpiCard label="Years to FIRE" value={yearsToFire != null ? yearsToFire.toFixed(1) : '>50'} color={yearsToFire != null && yearsToFire <= 10 ? 'text-green-700' : ''} />
        <KpiCard label="Monthly in Retirement" value={fmtEur(annualExpenses / 12)} />
      </div>
      <Plot
        data={[
          { x: chartX, y: chartY, name: 'Portfolio', type: 'scatter', mode: 'lines', fill: 'tozeroy', fillcolor: 'rgba(59,130,246,0.1)', line: { color: '#3b82f6', width: 2 } },
          { x: chartX, y: chartX.map(() => fireNumber), name: 'FIRE Number', type: 'scatter', mode: 'lines', line: { color: '#8b5cf6', width: 2, dash: 'dot' } },
        ]}
        layout={{ height: 320, margin: { t: 10, r: 10, b: 40, l: 80 }, yaxis: { tickformat: ',.0f', tickprefix: '€' }, legend: { orientation: 'h', y: -0.2 }, ...plotLayout(isDark) }}
        config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }} />
      <div>
        <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">SWR Sensitivity</p>
        <table className="text-xs border-collapse">
          <thead><tr className="bg-slate-50">{[3, 3.5, 4, 4.5, 5].map(s => <th key={s} className="px-3 py-1.5 border border-slate-200 text-center font-semibold">{s}% SWR</th>)}</tr></thead>
          <tbody><tr>{[3, 3.5, 4, 4.5, 5].map(s => <td key={s} className="px-3 py-1.5 border border-slate-200 text-center tabular-nums">{fmtEur(annualExpenses / (s / 100))}</td>)}</tr></tbody>
        </table>
      </div>
    </div>
  )
}

function LoanAmortizationTab() {
  const [principal, setPrincipal] = useState(200000)
  const [rate, setRate] = useState(4.5)
  const [termMonths, setTermMonths] = useState(240)
  const [showAll, setShowAll] = useState(false)

  const r = rate / 100 / 12
  const payment = r > 0 ? principal * r * Math.pow(1 + r, termMonths) / (Math.pow(1 + r, termMonths) - 1) : principal / termMonths
  const totalPaid = payment * termMonths
  const totalInterest = totalPaid - principal

  const schedule: { month: number; payment: number; principal: number; interest: number; balance: number }[] = []
  let balance = principal
  for (let m = 1; m <= termMonths; m++) {
    const int = balance * r; const prin = payment - int; balance -= prin
    schedule.push({ month: m, payment, principal: prin, interest: int, balance: Math.max(balance, 0) })
  }
  const display = showAll ? schedule : schedule.slice(0, 24)

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
        <div><label className="text-xs text-slate-500 block mb-1">Loan Amount (€)</label><Input type="number" value={principal} onChange={e => setPrincipal(Number(e.target.value))} /></div>
        <div><label className="text-xs text-slate-500 block mb-1">Annual Rate (%)</label><Input type="number" value={rate} onChange={e => setRate(Number(e.target.value))} step="0.1" /></div>
        <div><label className="text-xs text-slate-500 block mb-1">Term (months)</label><Input type="number" value={termMonths} onChange={e => setTermMonths(Number(e.target.value))} /></div>
      </div>
      <div className="grid grid-cols-3 gap-3">
        <KpiCard label="Monthly Payment" value={fmtEur(payment)} color="text-blue-700" />
        <KpiCard label="Total Interest" value={fmtEur(totalInterest)} color="text-red-600" />
        <KpiCard label="Total Paid" value={fmtEur(totalPaid)} />
      </div>
      <WithCopy>
      <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-300px)] text-xs">
        <table className="w-full border-collapse">
          <thead className="sticky top-0 z-10"><tr className="bg-slate-50">
            <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold">Month</th>
            <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold">Payment</th>
            <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold">Principal</th>
            <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold">Interest</th>
            <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold">Balance</th>
          </tr></thead>
          <tbody>
            {display.map(row => (
              <tr key={row.month} className="border-b border-slate-100 hover:bg-slate-50">
                <td className="px-2 py-1.5 text-right text-slate-500">{row.month}</td>
                <td className="px-2 py-1.5 text-right tabular-nums">{fmtEur(row.payment)}</td>
                <td className="px-2 py-1.5 text-right tabular-nums text-green-700">{fmtEur(row.principal)}</td>
                <td className="px-2 py-1.5 text-right tabular-nums text-red-600">{fmtEur(row.interest)}</td>
                <td className="px-2 py-1.5 text-right tabular-nums font-medium">{fmtEur(row.balance)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {schedule.length > 24 && (
          <button onClick={() => setShowAll(!showAll)} className="mt-2 text-xs text-blue-600 hover:underline">
            {showAll ? 'Show less' : `Show all ${schedule.length} months`}
          </button>
        )}
      </div>
      </WithCopy>
    </div>
  )
}

function PlanningSection() {
  const [tab, setTab] = usePersist('planning_tab', 'Goals')
  return (
    <div>
      <SubTabs tabs={['Goals', 'FIRE Calculator', 'Loan Amortization']} active={tab} onChange={setTab} />
      {tab === 'Goals' && <GoalsTab />}
      {tab === 'FIRE Calculator' && <FireCalculatorTab />}
      {tab === 'Loan Amortization' && <LoanAmortizationTab />}
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════════
// CUSTOM REPORTS
// ════════════════════════════════════════════════════════════════════════════

type CRConfig = {
  date_range_type?: string
  date_from?: string
  date_to?: string
  date_from_is_today?: boolean
  date_to_is_today?: boolean
  column_grouping?: string
  acct_mode?: 'all' | 'selected'
  account_ids?: number[]
  cat_mode?: 'all' | 'selected'
  category_ids?: number[]
  payee_mode?: 'all' | 'selected'
  payee_names?: string[]
  sec_mode?: 'all' | 'selected'
  security_ids?: number[]
  include_transfers?: boolean
  use_account_currency?: boolean
}

type CRPreset = { preset_id: number; preset_name: string; config: CRConfig }
type CRFilterData = {
  accounts: { accounts_id: number; accounts_name: string }[]
  categories: { categories_id: number; full_path: string; categories_type: string }[]
  payees: { payees_id: number; payees_name: string }[]
  securities: { securities_id: number; securities_name: string }[]
}

const DR_OPTIONS = ['Year to Date', 'Last Year', 'Last 12 Months', 'Last 24 Months', 'All Time', 'Custom']

function drDates(type: string): { dateFrom: string; dateTo: string } {
  const now = new Date()
  const fmt = (d: Date) => d.toISOString().slice(0, 10)
  const y = now.getFullYear()
  if (type === 'Year to Date') return { dateFrom: `${y}-01-01`, dateTo: fmt(now) }
  if (type === 'Last Year')    return { dateFrom: `${y - 1}-01-01`, dateTo: `${y - 1}-12-31` }
  if (type === 'Last 12 Months') { const d = new Date(now); d.setFullYear(d.getFullYear() - 1); return { dateFrom: fmt(d), dateTo: fmt(now) } }
  if (type === 'Last 24 Months') { const d = new Date(now); d.setFullYear(d.getFullYear() - 2); return { dateFrom: fmt(d), dateTo: fmt(now) } }
  if (type === 'All Time') return { dateFrom: '2000-01-01', dateTo: fmt(now) }
  return { dateFrom: `${y}-01-01`, dateTo: fmt(now) }
}

function FilterSection({ title, children }: { title: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(true)
  return (
    <div className="border border-slate-200 rounded-lg">
      <button type="button" onClick={() => setOpen(o => !o)}
        className="w-full flex items-center gap-2 px-4 py-3 bg-slate-50 hover:bg-slate-100 text-sm font-medium text-slate-700 text-left rounded-lg">
        <span className="text-slate-400 text-xs">{open ? '▼' : '▶'}</span>
        {title}
      </button>
      {open && <div className="px-4 py-3 border-t border-slate-100">{children}</div>}
    </div>
  )
}

function MultiSelect({ label, options, selected, onChange, placeholder }: {
  label: string; options: string[]; selected: string[]
  onChange: (v: string[]) => void; placeholder?: string
}) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const ref = React.useRef<HTMLDivElement>(null)

  const filtered = search.trim()
    ? options.filter(o => o.toLowerCase().includes(search.toLowerCase()))
    : options

  const toggle = (v: string) => onChange(selected.includes(v) ? selected.filter(x => x !== v) : [...selected, v])

  // Close on outside click
  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => { setOpen(o => !o); setSearch('') }}
        className="w-full text-left border border-slate-200 rounded px-3 py-2 text-sm bg-white hover:border-slate-400 flex items-center justify-between"
      >
        <span className="truncate text-slate-600">
          {selected.length === 0 ? (placeholder ?? `All ${label}`) : `${selected.length} selected`}
        </span>
        <span className="text-slate-400 ml-2">▾</span>
      </button>
      {open && (
        <div className="absolute z-50 mt-1 w-full bg-white border border-slate-200 rounded shadow-lg flex flex-col" style={{ maxHeight: 280 }}>
          {/* Search box */}
          <div className="p-2 border-b border-slate-100 flex-shrink-0">
            <input
              autoFocus
              type="text"
              placeholder={`Search ${label}…`}
              value={search}
              onChange={e => setSearch(e.target.value)}
              className="w-full border border-slate-200 rounded px-2 py-1.5 text-sm outline-none focus:border-blue-400"
              onClick={e => e.stopPropagation()}
            />
          </div>
          {/* Select all / none row */}
          <div className="flex items-center gap-3 px-3 py-1.5 border-b border-slate-100 flex-shrink-0">
            <button type="button" className="text-xs text-blue-600 hover:underline"
              onClick={() => onChange(options)}>All</button>
            <button type="button" className="text-xs text-slate-500 hover:underline"
              onClick={() => onChange([])}>None</button>
            {search && filtered.length > 0 && (
              <button type="button" className="text-xs text-slate-500 hover:underline ml-auto"
                onClick={() => onChange([...new Set([...selected, ...filtered])])}>
                + select {filtered.length} shown
              </button>
            )}
          </div>
          {/* Options list */}
          <div className="overflow-y-auto flex-1">
            {filtered.length === 0 && <div className="px-3 py-2 text-sm text-slate-400">No matches</div>}
            {filtered.map(o => (
              <label key={o} className="flex items-center gap-2 px-3 py-1.5 hover:bg-slate-50 cursor-pointer text-sm">
                <input type="checkbox" className="accent-blue-600 flex-shrink-0" checked={selected.includes(o)} onChange={() => toggle(o)} />
                <span className="truncate" title={o}>{o}</span>
              </label>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function CustomReportsSection() {
  const today = new Date().toISOString().slice(0, 10)

  // Filter data
  const { data: filterData } = useQuery<CRFilterData>({
    queryKey: ['cr-filter-data'], queryFn: getCustomReportFilterData, staleTime: 300_000,
  })
  const { data: presets = [], refetch: refetchPresets } = useQuery<CRPreset[]>({
    queryKey: ['cr-presets'], queryFn: getCustomReportPresets,
  })

  // Preset selection
  const [selPreset, setSelPreset] = useState<string>('(New Report)')
  const [presetName, setPresetName] = useState('')

  // Config state
  const [drType, setDrType] = useState('Last 12 Months')
  const [customFrom, setCustomFrom] = useState(today)
  const [customTo, setCustomTo] = useState(today)
  const [fromIsToday, setFromIsToday] = useState(false)
  const [toIsToday, setToIsToday] = useState(false)
  const [grouping, setGrouping] = useState('month')
  const [acctMode, setAcctMode] = useState<'all' | 'selected'>('all')
  const [acctIds, setAcctIds] = useState<number[]>([])
  const [catMode, setCatMode] = useState<'all' | 'selected'>('all')
  const [catIds, setCatIds] = useState<number[]>([])
  const [payeeMode, setPayeeMode] = useState<'all' | 'selected'>('all')
  const [payeeNames, setPayeeNames] = useState<string[]>([])
  const [secMode, setSecMode] = useState<'all' | 'selected'>('all')
  const [secIds, setSecIds] = useState<number[]>([])
  const [includeTransfers, setIncludeTransfers] = useState(false)
  const [useAcctCcy, setUseAcctCcy] = useState(false)

  // Result state
  const [result, setResult] = useState<Row[] | null>(null)
  const [resultParams, setResultParams] = useState<Record<string, unknown>>({})
  const [running, setRunning] = useState(false)
  const [saving, setSaving] = useState(false)
  const [deleteConfirm, setDeleteConfirm] = useState(false)

  // Drill-down state
  const [ddCategory, setDdCategory] = useState('— All —')
  const [ddPeriod, setDdPeriod] = useState('— All Periods —')
  const [ddResult, setDdResult] = useState<Row[] | null>(null)
  const [ddRunning, setDdRunning] = useState(false)

  const accounts   = filterData?.accounts ?? []
  const categories = filterData?.categories ?? []
  const payees     = filterData?.payees ?? []
  const securities = filterData?.securities ?? []

  function loadPreset(name: string) {
    setSelPreset(name)
    setPresetName(name === '(New Report)' ? '' : name)
    setDeleteConfirm(false)
    if (name === '(New Report)') return
    const p = presets.find(p => p.preset_name === name)
    if (!p) return
    const c = p.config
    setDrType(c.date_range_type ?? 'Last 12 Months')
    setCustomFrom(c.date_from ?? today)
    setCustomTo(c.date_to ?? today)
    setGrouping(c.column_grouping ?? 'month')
    const loadedAcctIds = c.account_ids ?? []
    const loadedCatIds  = c.category_ids ?? []
    const loadedPayees  = c.payee_names ?? []
    const loadedSecIds  = c.security_ids ?? []
    setAcctMode(c.acct_mode ?? (loadedAcctIds.length > 0 ? 'selected' : 'all'))
    setAcctIds(loadedAcctIds)
    setCatMode(c.cat_mode ?? (loadedCatIds.length > 0 ? 'selected' : 'all'))
    setCatIds(loadedCatIds)
    setPayeeMode(c.payee_mode ?? (loadedPayees.length > 0 ? 'selected' : 'all'))
    setPayeeNames(loadedPayees)
    setSecMode(c.sec_mode ?? (loadedSecIds.length > 0 ? 'selected' : 'all'))
    setSecIds(loadedSecIds)
    setFromIsToday(c.date_from_is_today ?? false)
    setToIsToday(c.date_to_is_today ?? false)
    setIncludeTransfers(c.include_transfers ?? false)
    setUseAcctCcy(c.use_account_currency ?? false)
    setResult(null); setDdResult(null)
  }

  const { dateFrom, dateTo } = drType === 'Custom'
    ? { dateFrom: fromIsToday ? today : customFrom, dateTo: toIsToday ? today : customTo }
    : drDates(drType)

  async function handleSave() {
    const name = presetName.trim()
    if (!name || name === '(New Report)') return
    setSaving(true)
    try {
      await saveCustomReportPreset(name, {
        date_range_type: drType, date_from: customFrom, date_to: customTo,
        date_from_is_today: fromIsToday, date_to_is_today: toIsToday,
        column_grouping: grouping,
        acct_mode: acctMode, account_ids: acctIds,
        cat_mode: catMode, category_ids: catIds,
        payee_mode: payeeMode, payee_names: payeeNames,
        sec_mode: secMode, security_ids: secIds,
        include_transfers: includeTransfers, use_account_currency: useAcctCcy,
      })
      await refetchPresets()
      setSelPreset(name)
    } finally { setSaving(false) }
  }

  async function handleDelete() {
    const p = presets.find(p => p.preset_name === selPreset)
    if (!p) return
    await deleteCustomReportPreset(p.preset_id)
    await refetchPresets()
    loadPreset('(New Report)')
  }

  const investmentMode = catMode === 'selected' && catIds.length === 0

  async function handleRun() {
    setRunning(true); setResult(null); setDdResult(null)
    try {
      const effAcctIds  = acctMode  === 'selected' ? (acctIds.length  ? acctIds   : null) : null
      const effCatIds   = catMode   === 'selected' ? (catIds.length   ? catIds    : null) : null
      const effPayees   = payeeMode === 'selected' ? (payeeNames.length ? payeeNames : null) : null
      const effSecIds   = secMode   === 'selected' ? (secIds.length   ? secIds    : null) : null
      const rows = await runCustomReport({
        date_from: dateFrom, date_to: dateTo, grouping,
        account_ids: effAcctIds, category_ids: effCatIds,
        payee_names: effPayees, security_ids: effSecIds,
        include_transfers: includeTransfers,
        use_account_currency: useAcctCcy,
        investment_mode: investmentMode,
      })
      setResult(rows)
      setResultParams({
        date_from: dateFrom, date_to: dateTo, grouping,
        account_ids: effAcctIds, category_ids: effCatIds,
        payee_names: effPayees, security_ids: effSecIds,
        include_transfers: includeTransfers,
        use_account_currency: useAcctCcy,
        investment_mode: investmentMode,
      })
      setDdCategory('— All —'); setDdPeriod('— All Periods —')
    } finally { setRunning(false) }
  }

  // Derived pivot data
  const periods = useMemo(() => {
    if (!result) return []
    const seen = new Map<string, string>()
    result.forEach(r => seen.set(String(r.period_order ?? r.period), String(r.period)))
    return [...seen.entries()].sort((a, b) => a[0].localeCompare(b[0])).map(e => e[1])
  }, [result])

  const categories_in_result = useMemo(() => {
    if (!result) return []
    return [...new Set(result.map(r => String(r.category)))].sort()
  }, [result])

  const pivot = useMemo(() => {
    if (!result || periods.length === 0) return {}
    const map: Record<string, Record<string, number>> = {}
    result.forEach(r => {
      const cat = String(r.category)
      const per = String(r.period)
      if (!map[cat]) map[cat] = {}
      map[cat][per] = (map[cat][per] ?? 0) + Number(r.amount_eur ?? 0)
    })
    return map
  }, [result, periods])

  const periodTotals = useMemo(() => {
    const t: Record<string, number> = {}
    periods.forEach(p => { t[p] = categories_in_result.reduce((s, c) => s + (pivot[c]?.[p] ?? 0), 0) })
    return t
  }, [pivot, periods, categories_in_result])

  const grandTotal = useMemo(() => Object.values(periodTotals).reduce((s, v) => s + v, 0), [periodTotals])

  function periodDates(period: string): { from: string; to: string } {
    const grp = String(resultParams.grouping ?? 'month')
    if (grp === 'year') {
      return { from: `${period}-01-01`, to: `${period}-12-31` }
    } else if (grp === 'quarter') {
      const [yr, q] = period.split(' Q')
      const qNum = parseInt(q)
      const mStart = (qNum - 1) * 3 + 1
      const mEnd = mStart + 2
      const lastDay = new Date(parseInt(yr), mEnd, 0).getDate()
      return { from: `${yr}-${String(mStart).padStart(2, '0')}-01`, to: `${yr}-${String(mEnd).padStart(2, '0')}-${lastDay}` }
    } else {
      const lastDay = new Date(parseInt(period.slice(0, 4)), parseInt(period.slice(5, 7)), 0).getDate()
      return { from: `${period}-01`, to: `${period}-${lastDay}` }
    }
  }

  async function handleDrillDown() {
    setDdRunning(true); setDdResult(null)
    try {
      const ddDates = ddPeriod === '— All Periods —'
        ? { date_from: resultParams.date_from, date_to: resultParams.date_to }
        : (() => { const p = periodDates(ddPeriod); return { date_from: p.from, date_to: p.to } })()
      const base = {
        date_from: ddDates.date_from,
        date_to: ddDates.date_to,
        account_ids: resultParams.account_ids,
        use_account_currency: resultParams.use_account_currency,
      }
      if (resultParams.investment_mode) {
        const rows = await runCustomReportInvestmentDrillDown({
          ...base, security_name: ddCategory === '— All —' ? null : ddCategory,
        })
        setDdResult(rows)
      } else {
        const rows = await runCustomReportDrillDown({
          ...base,
          category_path: ddCategory === '— All —' ? null : ddCategory,
          category_ids: resultParams.category_ids,
          payee_names: resultParams.payee_names,
          security_ids: resultParams.security_ids,
          include_transfers: resultParams.include_transfers,
        })
        setDdResult(rows)
      }
    } finally { setDdRunning(false) }
  }

  const grpLabel = grouping === 'year' ? 'Year' : grouping === 'quarter' ? 'Quarter' : 'Month'
  const catLabel = investmentMode ? 'Securities' : 'Categories'

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-slate-800">Custom Reports</h2>
        <p className="text-sm text-slate-500 mt-0.5">
          Build a spending report for any date range, accounts, categories, and payees. Save configurations as named presets.
        </p>
      </div>

      {/* Preset bar */}
      <div className="flex gap-2 items-end flex-wrap">
        <div className="flex-1 min-w-[200px]">
          <label className="block text-xs text-slate-500 mb-1">Preset</label>
          <select className="w-full border border-slate-200 rounded px-3 py-2 text-sm bg-white"
            value={selPreset} onChange={e => loadPreset(e.target.value)}>
            <option>(New Report)</option>
            {[...presets].sort((a, b) => a.preset_name.localeCompare(b.preset_name)).map(p => (
              <option key={p.preset_id}>{p.preset_name}</option>
            ))}
          </select>
        </div>
        <div className="flex-1 min-w-[200px]">
          <label className="block text-xs text-slate-500 mb-1">Name</label>
          <Input placeholder="Preset name to save as…" value={presetName}
            onChange={e => setPresetName(e.target.value)} className="text-sm" />
        </div>
        <Button onClick={handleSave} disabled={saving || !presetName.trim()} className="self-end">
          {saving ? 'Saving…' : '💾 Save'}
        </Button>
        {selPreset !== '(New Report)' && !deleteConfirm && (
          <Button variant="destructive" onClick={() => setDeleteConfirm(true)} className="self-end">
            🗑️ Delete
          </Button>
        )}
        {deleteConfirm && (
          <div className="flex items-center gap-2 self-end">
            <span className="text-sm text-red-600">Delete "{selPreset}"?</span>
            <Button variant="destructive" onClick={() => { handleDelete(); setDeleteConfirm(false) }}>Yes</Button>
            <Button variant="secondary" onClick={() => setDeleteConfirm(false)}>Cancel</Button>
          </div>
        )}
      </div>

      {/* Date range + grouping */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div>
          <label className="block text-xs text-slate-500 mb-1">Date Range</label>
          <select className="w-full border border-slate-200 rounded px-3 py-2 text-sm bg-white"
            value={drType} onChange={e => setDrType(e.target.value)}>
            {DR_OPTIONS.map(o => <option key={o}>{o}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-xs text-slate-500 mb-1">Column Grouping</label>
          <select className="w-full border border-slate-200 rounded px-3 py-2 text-sm bg-white"
            value={grouping} onChange={e => setGrouping(e.target.value)}>
            <option value="year">Year</option>
            <option value="quarter">Quarter</option>
            <option value="month">Month</option>
          </select>
        </div>
        {drType === 'Custom' && (
          <>
            <div>
              <label className="flex items-center gap-1.5 text-xs text-slate-500 mb-1 cursor-pointer">
                <input type="checkbox" className="accent-blue-600" checked={fromIsToday}
                  onChange={e => setFromIsToday(e.target.checked)} />
                Use today
                <Tooltip text="When saved, this preset will always use today's date as the From date.">
                  <span className="text-slate-400 cursor-default">ⓘ</span>
                </Tooltip>
              </label>
              <div>
                <div className="block text-xs text-slate-500 mb-1">From</div>
                <Input type="date" value={fromIsToday ? today : customFrom}
                  disabled={fromIsToday}
                  onChange={e => setCustomFrom(e.target.value)} className="text-sm" />
              </div>
            </div>
            <div>
              <label className="flex items-center gap-1.5 text-xs text-slate-500 mb-1 cursor-pointer">
                <input type="checkbox" className="accent-blue-600" checked={toIsToday}
                  onChange={e => setToIsToday(e.target.checked)} />
                Use today
                <Tooltip text="When saved, this preset will always use today's date as the To date.">
                  <span className="text-slate-400 cursor-default">ⓘ</span>
                </Tooltip>
              </label>
              <div>
                <div className="block text-xs text-slate-500 mb-1">To</div>
                <Input type="date" value={toIsToday ? today : customTo}
                  disabled={toIsToday}
                  onChange={e => setCustomTo(e.target.value)} className="text-sm" />
              </div>
            </div>
          </>
        )}
      </div>

      {/* Filters */}
      <div className="space-y-3">
        {/* Accounts */}
        <FilterSection title="🏦 Accounts">
          <div className="text-xs text-slate-500 mb-2">Accounts to include</div>
          <div className="flex gap-4 mb-3">
            {(['all', 'selected'] as const).map(m => (
              <label key={m} className="flex items-center gap-1.5 text-sm cursor-pointer">
                <input type="radio" className="accent-blue-600" checked={acctMode === m}
                  onChange={() => { setAcctMode(m); if (m === 'all') setAcctIds([]) }} />
                {m === 'all' ? 'All Accounts' : 'Selected Accounts'}
              </label>
            ))}
          </div>
          {acctMode === 'selected' && (
            <MultiSelect label="Accounts"
              options={accounts.map(a => a.accounts_name)}
              selected={accounts.filter(a => acctIds.includes(a.accounts_id)).map(a => a.accounts_name)}
              onChange={names => setAcctIds(names.map(n => accounts.find(a => a.accounts_name === n)!.accounts_id))}
            />
          )}
        </FilterSection>

        {/* Categories */}
        <FilterSection title="🏷️ Categories">
          <div className="text-xs text-slate-500 mb-2">Categories to include</div>
          <div className="flex gap-4 mb-3">
            {(['all', 'selected'] as const).map(m => (
              <label key={m} className="flex items-center gap-1.5 text-sm cursor-pointer">
                <input type="radio" className="accent-blue-600" checked={catMode === m}
                  onChange={() => { setCatMode(m); if (m === 'all') setCatIds([]) }} />
                {m === 'all' ? 'All Expense Categories' : 'Selected Categories'}
              </label>
            ))}
          </div>
          {catMode === 'selected' && (
            <>
              <p className="text-xs text-slate-400 mb-1">Selecting a parent category includes all its sub-categories.</p>
              <MultiSelect label="Categories"
                options={categories.map(c => c.full_path)}
                selected={categories.filter(c => catIds.includes(c.categories_id)).map(c => c.full_path)}
                onChange={paths => setCatIds(paths.map(p => categories.find(c => c.full_path === p)!.categories_id))}
              />
            </>
          )}
        </FilterSection>

        {/* Payees */}
        <FilterSection title="👤 Payees">
          <div className="text-xs text-slate-500 mb-2">Payees to include</div>
          <div className="flex gap-4 mb-3">
            {(['all', 'selected'] as const).map(m => (
              <label key={m} className="flex items-center gap-1.5 text-sm cursor-pointer">
                <input type="radio" className="accent-blue-600" checked={payeeMode === m}
                  onChange={() => { setPayeeMode(m); if (m === 'all') setPayeeNames([]) }} />
                {m === 'all' ? 'All Payees' : 'Selected Payees'}
              </label>
            ))}
          </div>
          {payeeMode === 'selected' && (
            <MultiSelect label="Payees"
              options={payees.map(p => p.payees_name)}
              selected={payeeNames}
              onChange={setPayeeNames}
            />
          )}
        </FilterSection>

        {/* Securities */}
        <FilterSection title="📈 Securities">
          <p className="text-xs text-slate-400 mb-2">
            Filter to transactions linked to specific securities (e.g. dividend income, interest, or fees). Leave empty to include all.
            Select "Selected Categories" with no categories chosen to switch to investment cashflow mode.
          </p>
          <div className="text-xs text-slate-500 mb-2">Securities to include</div>
          <div className="flex gap-4 mb-3">
            {(['all', 'selected'] as const).map(m => (
              <label key={m} className="flex items-center gap-1.5 text-sm cursor-pointer">
                <input type="radio" className="accent-blue-600" checked={secMode === m}
                  onChange={() => { setSecMode(m); if (m === 'all') setSecIds([]) }} />
                {m === 'all' ? 'All Securities' : 'Selected Securities'}
              </label>
            ))}
          </div>
          {secMode === 'selected' && (
            <MultiSelect label="Securities"
              options={securities.map(s => s.securities_name)}
              selected={securities.filter(s => secIds.includes(s.securities_id)).map(s => s.securities_name)}
              onChange={names => setSecIds(names.map(n => securities.find(s => s.securities_name === n)!.securities_id))}
            />
          )}
        </FilterSection>
      </div>

      {/* Additional options */}
      <div className="flex gap-6">
        <label className="flex items-center gap-2 text-sm cursor-pointer">
          <input type="checkbox" className="accent-blue-600" checked={includeTransfers}
            onChange={e => setIncludeTransfers(e.target.checked)} />
          Include transfer transactions
        </label>
        <label className="flex items-center gap-2 text-sm cursor-pointer">
          <input type="checkbox" className="accent-blue-600" checked={useAcctCcy}
            onChange={e => setUseAcctCcy(e.target.checked)} />
          Use account native currency (no EUR conversion)
        </label>
      </div>

      {investmentMode && (
        <div className="text-xs text-blue-700 bg-blue-50 border border-blue-200 rounded px-3 py-2">
          Investment mode: "Selected Categories" chosen with no categories picked — report will show investment cashflows grouped by security.
          {secMode === 'selected' && secIds.length > 0 && ` Filtered to ${secIds.length} selected securit${secIds.length === 1 ? 'y' : 'ies'}.`}
        </div>
      )}

      {/* Run button */}
      <div>
        <Button onClick={handleRun} disabled={running} className="px-6">
          {running ? <><Spinner size={14} /> Running…</> : '▶ Run Report'}
        </Button>
      </div>

      {/* Results */}
      {result !== null && (
        result.length === 0
          ? <div className="text-sm text-slate-400 py-4">No data found for the selected filters and date range.</div>
          : (
            <div className="space-y-6">
              {/* KPIs */}
              <div className="grid grid-cols-3 gap-4">
                <div className="border border-slate-200 rounded-lg p-4">
                  <div className="text-xs text-slate-500 uppercase tracking-wide">Grand Total</div>
                  <div className={`text-2xl font-bold mt-1 ${grandTotal >= 0 ? 'text-slate-800' : 'text-red-600'}`}>{fmtEur(grandTotal)}</div>
                </div>
                <div className="border border-slate-200 rounded-lg p-4">
                  <div className="text-xs text-slate-500 uppercase tracking-wide">Periods</div>
                  <div className="text-2xl font-bold mt-1 text-slate-800">{periods.length}</div>
                </div>
                <div className="border border-slate-200 rounded-lg p-4">
                  <div className="text-xs text-slate-500 uppercase tracking-wide">{catLabel}</div>
                  <div className="text-2xl font-bold mt-1 text-slate-800">{categories_in_result.length}</div>
                </div>
              </div>

              {/* Bar chart */}
              <Plot
                data={[{
                  type: 'bar', x: periods, y: periods.map(p => periodTotals[p] ?? 0),
                  text: periods.map(p => fmtEur(periodTotals[p] ?? 0)),
                  textposition: 'outside',
                  marker: { color: periods.map(p => (periodTotals[p] ?? 0) >= 0 ? '#3b82f6' : '#ef4444') },
                }]}
                layout={{
                  title: { text: `Total ${investmentMode ? 'Cashflow' : 'Spending'} by ${grpLabel}`, font: { size: 14 } },
                  margin: { l: 60, r: 20, t: 40, b: 60 }, height: 280,
                  paper_bgcolor: 'transparent', plot_bgcolor: 'transparent',
                  yaxis: { tickformat: ',.0f', tickprefix: '€' },
                }}
                config={{ displayModeBar: false }}
                style={{ width: '100%' }}
              />

              {/* Pivot table */}
              <div>
                <h3 className="text-sm font-semibold text-slate-700 mb-2">{investmentMode ? 'Cashflow by Security' : 'Spending by Category'}</h3>
                <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-300px)]">
                  <table className="w-full text-sm border-collapse">
                    <thead className="sticky top-0 z-10 bg-white">
                      <tr className="border-b-2 border-slate-200">
                        <th className="text-left px-3 py-2 text-xs text-slate-500 uppercase tracking-wide font-medium sticky left-0 bg-white">{catLabel}</th>
                        {periods.map(p => <th key={p} className="text-right px-3 py-2 text-xs text-slate-500 uppercase tracking-wide font-medium whitespace-nowrap">{p}</th>)}
                        <th className="text-right px-3 py-2 text-xs text-slate-500 uppercase tracking-wide font-medium">TOTAL</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {categories_in_result.map(cat => {
                        const rowTotal = periods.reduce((s, p) => s + (pivot[cat]?.[p] ?? 0), 0)
                        return (
                          <tr key={cat} className="hover:bg-slate-50">
                            <td className="px-3 py-1.5 text-slate-700 sticky left-0 bg-white max-w-[300px] truncate" title={cat}>{cat}</td>
                            {periods.map(p => {
                              const v = pivot[cat]?.[p] ?? 0
                              return <td key={p} className={`px-3 py-1.5 text-right tabular-nums whitespace-nowrap ${v < 0 ? 'text-red-600' : ''}`}>{v !== 0 ? fmtEur(v) : '—'}</td>
                            })}
                            <td className={`px-3 py-1.5 text-right tabular-nums whitespace-nowrap font-medium ${rowTotal < 0 ? 'text-red-600' : ''}`}>{fmtEur(rowTotal)}</td>
                          </tr>
                        )
                      })}
                    </tbody>
                    <tfoot>
                      <tr className="border-t-2 border-slate-200 font-semibold bg-slate-50">
                        <td className="px-3 py-2 sticky left-0 bg-slate-50">TOTAL</td>
                        {periods.map(p => <td key={p} className={`px-3 py-2 text-right tabular-nums whitespace-nowrap ${(periodTotals[p] ?? 0) < 0 ? 'text-red-600' : ''}`}>{fmtEur(periodTotals[p] ?? 0)}</td>)}
                        <td className={`px-3 py-2 text-right tabular-nums whitespace-nowrap ${grandTotal < 0 ? 'text-red-600' : ''}`}>{fmtEur(grandTotal)}</td>
                      </tr>
                    </tfoot>
                  </table>
                </div>
              </div>

              {/* Drill-down */}
              <div>
                <h3 className="text-sm font-semibold text-slate-700 mb-3">🔍 {investmentMode ? 'Investment' : 'Transaction'} Drill-Down</h3>
                <div className="flex gap-3 items-end flex-wrap">
                  <div className="flex-1 min-w-[200px]">
                    <label className="block text-xs text-slate-500 mb-1">{investmentMode ? 'Security' : 'Category'}</label>
                    <select className="w-full border border-slate-200 rounded px-3 py-2 text-sm bg-white"
                      value={ddCategory} onChange={e => setDdCategory(e.target.value)}>
                      <option>— All —</option>
                      {categories_in_result.map(c => <option key={c}>{c}</option>)}
                    </select>
                  </div>
                  <div className="flex-1 min-w-[160px]">
                    <label className="block text-xs text-slate-500 mb-1">Period</label>
                    <select className="w-full border border-slate-200 rounded px-3 py-2 text-sm bg-white"
                      value={ddPeriod} onChange={e => setDdPeriod(e.target.value)}>
                      <option>— All Periods —</option>
                      {periods.map(p => <option key={p}>{p}</option>)}
                    </select>
                  </div>
                  <Button onClick={handleDrillDown} disabled={ddRunning} className="self-end">
                    {ddRunning ? <><Spinner size={14} /> Loading…</> : 'Load'}
                  </Button>
                </div>

                {ddResult !== null && (
                  ddResult.length === 0
                    ? <div className="text-sm text-slate-400 mt-3">No entries found.</div>
                    : (
                      <div className="mt-3 overflow-x-auto overflow-y-auto max-h-96">
                        <div className="text-xs text-slate-500 mb-1">
                          {ddResult.length} {investmentMode ? 'entr' : 'transaction'}
                          {ddResult.length === 1 ? (investmentMode ? 'y' : '') : (investmentMode ? 'ies' : 's')}
                          {' · '}net total {fmtEur(ddResult.reduce((s, r) => s + Number(r.amount_eur ?? 0), 0))}
                        </div>
                        <table className="w-full text-sm border-collapse">
                          <thead className="sticky top-0 z-10 bg-white">
                            <tr className="border-b border-slate-200">
                              <th className="text-left px-2 py-1.5 text-xs text-slate-500">Date</th>
                              {investmentMode
                                ? <>
                                    <th className="text-left px-2 py-1.5 text-xs text-slate-500">Security</th>
                                    <th className="text-left px-2 py-1.5 text-xs text-slate-500">Action</th>
                                    <th className="text-right px-2 py-1.5 text-xs text-slate-500">Qty</th>
                                    <th className="text-right px-2 py-1.5 text-xs text-slate-500">Price</th>
                                    <th className="text-right px-2 py-1.5 text-xs text-slate-500">Amount</th>
                                  </>
                                : <>
                                    <th className="text-left px-2 py-1.5 text-xs text-slate-500">Payee</th>
                                    <th className="text-left px-2 py-1.5 text-xs text-slate-500">Category</th>
                                    <th className="text-left px-2 py-1.5 text-xs text-slate-500">Notes</th>
                                  </>
                              }
                              <th className="text-right px-2 py-1.5 text-xs text-slate-500">Amount (€)</th>
                              <th className="text-left px-2 py-1.5 text-xs text-slate-500">Account</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-slate-100">
                            {ddResult.map((r, i) => (
                              <tr key={i} className="hover:bg-slate-50">
                                <td className="px-2 py-1.5 text-slate-500 whitespace-nowrap">{String(r.date ?? '').slice(0, 10)}</td>
                                {investmentMode
                                  ? <>
                                      <td className="px-2 py-1.5 font-medium max-w-[180px] truncate">{String(r.security ?? '')}</td>
                                      <td className="px-2 py-1.5 text-slate-500">{String(r.action ?? '')}</td>
                                      <td className="px-2 py-1.5 text-right tabular-nums whitespace-nowrap">{r.quantity != null ? fmtNum(Number(r.quantity), 4) : '—'}</td>
                                      <td className="px-2 py-1.5 text-right tabular-nums whitespace-nowrap">{r.price != null ? fmtNum(Number(r.price), 4) : '—'}</td>
                                      <td className="px-2 py-1.5 text-right tabular-nums whitespace-nowrap">{r.amount != null ? fmtEur(Number(r.amount)) : '—'}</td>
                                    </>
                                  : <>
                                      <td className="px-2 py-1.5 max-w-[160px] truncate">{String(r.payee ?? '')}</td>
                                      <td className="px-2 py-1.5 text-slate-500 max-w-[200px] truncate text-xs">{String(r.category ?? '')}</td>
                                      <td className="px-2 py-1.5 text-slate-400 text-xs max-w-[200px] truncate">{String(r.notes ?? '')}</td>
                                    </>
                                }
                                <td className={`px-2 py-1.5 text-right tabular-nums whitespace-nowrap font-medium ${Number(r.amount_eur ?? 0) < 0 ? 'text-red-600' : 'text-slate-800'}`}>
                                  {fmtEur(Number(r.amount_eur ?? 0))}
                                </td>
                                <td className="px-2 py-1.5 text-slate-500 text-xs"><AccountLink id={r.accounts_id as number} name={String(r.account ?? '')} type={investmentMode ? 'Brokerage' : String(r.account_type ?? '')} /></td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )
                )}
              </div>
            </div>
          )
      )}
    </div>
  )
}


// ════════════════════════════════════════════════════════════════════════════
// MAIN
// ════════════════════════════════════════════════════════════════════════════
export default function Reports() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [activeTab, setActiveTab] = usePersist('reports_active_tab', searchParams.get('tab') ?? 'net-worth')
  const [startDate, setStartDate] = useState('2020-01-01')
  const [endDate, setEndDate] = useState(new Date().toISOString().slice(0, 10))
  const current = REPORT_TABS.find(t => t.key === activeTab)

  const switchTab = (key: string) => { setActiveTab(key); setSearchParams({ tab: key }, { replace: true }) }

  return (
    <div className="flex flex-col md:flex-row h-full">
      {/* Vertical rail on desktop; a horizontally-scrollable tab strip on mobile,
          so the report list doesn't permanently eat a third sidebar's worth of
          width alongside the app nav on small screens. */}
      <nav className="shrink-0 md:w-48 border-b md:border-b-0 md:border-r border-slate-200 bg-slate-50 flex flex-row md:flex-col overflow-x-auto md:overflow-visible py-1 md:py-4">
        <p className="hidden md:block px-4 text-xs font-semibold text-slate-400 uppercase tracking-wide mb-2">Reports</p>
        {REPORT_TABS.map(t => (
          <button key={t.key} onClick={() => switchTab(t.key)}
            className={`text-left px-4 py-2 text-sm whitespace-nowrap transition-colors border-b-2 md:border-b-0 md:border-r-2 ${activeTab === t.key ? 'bg-blue-50 text-blue-700 font-semibold border-blue-600' : 'text-slate-600 hover:bg-slate-100 border-transparent'}`}>
            {t.label}
          </button>
        ))}
      </nav>

      <div className="flex-1 min-w-0 overflow-auto">
        <div className="flex items-center justify-between px-6 py-3 border-b border-slate-200 bg-white sticky top-0 z-10">
          <h2 className="text-base font-semibold text-slate-800">{current?.label}</h2>
          {activeTab !== 'net-worth' && activeTab !== 'inv-performance' && activeTab !== 'income-expense' && activeTab !== 'securities' && activeTab !== 'custom' && activeTab !== 'inv-positions' && activeTab !== 'cashflow' && activeTab !== 'tax' && activeTab !== 'budget' && activeTab !== 'planning' && (
            <div className="flex items-center gap-2">
              <Input type="date" className="w-36 text-sm" value={startDate} onChange={e => setStartDate(e.target.value)} />
              <span className="text-slate-400 text-sm">to</span>
              <Input type="date" className="w-36 text-sm" value={endDate} onChange={e => setEndDate(e.target.value)} />
            </div>
          )}
        </div>

        <div className="p-6">
          <Card>
            <CardBody>
              {activeTab === 'net-worth' && <NetWorthSection />}
              {activeTab === 'inv-positions' && <InvPositionsSection startDate={startDate} />}
              {activeTab === 'inv-performance' && <InvPerformanceSection />}
              {activeTab === 'securities' && <SecuritiesSection />}
              {activeTab === 'income-expense' && <IncomeExpenseSection startDate={startDate} endDate={endDate} />}
              {activeTab === 'cashflow' && <CashFlowSection />}
              {activeTab === 'budget' && <BudgetSection />}
              {activeTab === 'tax' && <TaxSection />}
              {activeTab === 'planning' && <PlanningSection />}
              {activeTab === 'custom' && <CustomReportsSection />}
            </CardBody>
          </Card>
        </div>
      </div>
    </div>
  )
}
