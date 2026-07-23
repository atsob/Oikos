import React, { useState, useMemo, useCallback, useEffect } from 'react'
import { usePersist } from '@/lib/hooks'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import PlotlyReact from 'react-plotly.js'
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const Plot: React.ComponentType<any> = (PlotlyReact as any).default ?? PlotlyReact
import {
  getPortfolioSummary, getAllocationReport,
  getAllocationTargets, saveAllocationTargets, getAllocationDelta, getRebalancingPlan,
  getHoldingsSnapshot,
  getCapitalGains,
  getBudgetVsActual, getAnnualIncome, getYtdExpenseTransactions, saveBudget,
  getCashFlowForecastFull, getPnl,
  getNetWorthByAccount, getInvestmentPositionsHistory, getSectorAllocation, getFxExposure,
  getSpendingTrends, getSavingsRateDetail,
  getTwr, getRiskMetrics, getTaxLossHarvesting, getDividendIncomeTax, getPriceChanges, getPortfolioSignals,
  getGoals, upsertGoal, deleteGoal,
  getBondSchedule, getBenchmarkCandidates, getBenchmark, getCorrelation, getSavingsAccounts,
  getDividendsTracker, getDividendsForecast, getDividendRecommendations, getAccounts,
  getPortfolioPresets, upsertPortfolioPreset, deletePortfolioPreset, getMonteCarlo,
  getIncomeExpenseFull,
  getCustomReportPresets, saveCustomReportPreset, deleteCustomReportPreset,
  getCustomReportFilterData, runCustomReport, runCustomReportDrillDown, runCustomReportInvestmentDrillDown,
  updateTransaction, upsertSplits, getSplits, getCategories, getPayees, deleteTransaction,
  getTransactionById,
  addPrice,
  api,
} from '@/lib/api'
import { Card, CardBody, Input, Select, Spinner, Button, Tooltip, ColHeader, useSortTable, useSortTablePersisted } from '@/components/ui'
import { fmtEur, fmtPct, fmtNum, plotLayout } from '@/lib/utils'
import { getCurrencySymbol } from '@/lib/settings'
import { useTheme } from '@/lib/theme'
import { Trash2, Plus, Pencil, RefreshCw, ChevronRight, ChevronDown } from 'lucide-react'
import { TxModal, useNoOpRecurring } from '@/components/TxModal'
import type { TxForm, SplitRow } from '@/components/TxModal'

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
  { key: 'inv-positions',   label: '📈 Inv. Positions' },
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
function PivotTable({ data, groupBy, colKey, valKey, showTotal = true }: {
  data: Row[]; groupBy: string; colKey: string; valKey: string; showTotal?: boolean
}) {
  const periods = [...new Set(data.map(r => String(r[colKey])))].sort()
  const categories = [...new Set(data.map(r => String(r[groupBy])))]
  const lookup: Record<string, Record<string, number>> = {}
  for (const r of data) {
    const g = String(r[groupBy]); const c = String(r[colKey])
    if (!lookup[g]) lookup[g] = {}
    lookup[g][c] = (lookup[g][c] ?? 0) + Number(r[valKey] ?? 0)
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
                  <td className="px-2 py-1.5 sticky left-0 bg-white">{cat}</td>
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

function NwOverview({ rows, allPeriods, grouping }: { rows: Row[]; allPeriods: string[]; grouping: string }) {
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

  // The comparison baseline is always the first period of the selected reporting range
  // (Start Date), so the KPI reflects the whole range rather than just the last bucket.
  const basePeriod = allPeriods.length ? allPeriods[0] : null
  const baseNetWorth = basePeriod != null
    ? NW_ASSET_GROUPS.reduce((s, g) => s + (byPeriod[basePeriod]?.[g] ?? 0), 0) + NW_LIAB_GROUPS.reduce((s, g) => s + (byPeriod[basePeriod]?.[g] ?? 0), 0)
    : null
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
          five fit on one line instead of wrapping. */}
      <div className="grid grid-cols-2 md:grid-cols-[1.3fr_1fr_1fr_1fr_1fr] gap-3">
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
  for (const r of rows) {
    const a = String(r.accounts_name), p = String(r.period)
    if (!lookup[a]) lookup[a] = {}
    lookup[a][p] = Number(r.balance_eur ?? 0)
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
                    <td className="px-2 py-1.5 pl-5 sticky left-0 bg-white">{acc}</td>
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
  const [savedSelection, setSavedSelection] = usePersist<Record<string, boolean>>('nw_account_selection', {})
  const [draftSelection, setDraftSelection] = useState<Record<string, boolean> | null>(null)
  const [selOpen, setSelOpen] = useState(false)

  const { data: rawData = [], isLoading } = useQuery({
    queryKey: ['nw-by-account', effStart, effEnd, effGrouping],
    queryFn: () => getNetWorthByAccount(effStart, effEnd, effGrouping),
  })
  const allRows = rawData as Row[]

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
  const allAccountNames = useMemo(() => Object.keys(accountMeta).sort(), [accountMeta])
  const allPeriods = useMemo(() => [...new Set(allRows.map(r => String(r.period)))].sort(), [allRows])

  const isIncluded = useCallback((name: string, sel: Record<string, boolean>) =>
    sel[name] === undefined ? true : sel[name], [])

  const accountTotals = useMemo(() => {
    const t: Record<string, number> = {}
    for (const r of allRows) { const n = String(r.accounts_name); t[n] = (t[n]??0) + Math.abs(Number(r.balance_eur??0)) }
    return t
  }, [allRows])
  const isZero = (name: string) => (accountTotals[name] ?? 0) < 0.01

  const filteredRows = useMemo(() =>
    allRows.filter(r => {
      const n = String(r.accounts_name)
      return isIncluded(n, savedSelection) && (showZeroBalance || !isZero(n)) && (showInactive || accountActive[n] !== false)
    }), [allRows, savedSelection, showZeroBalance, showInactive, accountActive])

  const hiddenZeroCount = useMemo(() =>
    allAccountNames.filter(n => isIncluded(n, savedSelection) && isZero(n) && (showInactive || accountActive[n] !== false)).length,
    [allAccountNames, savedSelection, showZeroBalance, showInactive, accountActive])

  const hiddenInactiveCount = useMemo(() =>
    allAccountNames.filter(n => isIncluded(n, savedSelection) && accountActive[n] === false).length,
    [allAccountNames, savedSelection, accountActive])

  const openSel = () => { setDraftSelection({ ...savedSelection }); setSelOpen(true) }
  const saveSel = () => { setSavedSelection(draftSelection ?? {}); setSelOpen(false) }

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
      <div className="border border-slate-200 rounded-lg overflow-hidden">
        <button onClick={() => selOpen ? setSelOpen(false) : openSel()}
          className="w-full flex items-center gap-2 px-4 py-2 text-sm font-medium text-slate-700 bg-slate-50 hover:bg-slate-100 text-left">
          <span className="text-xs">{selOpen ? '▼' : '▶'}</span>
          <span>⚙️ Account Selection</span>
        </button>
        {selOpen && draftSelection !== null && (
          <div className="p-3 border-t border-slate-200">
            <div className="max-h-60 overflow-y-auto border border-slate-200 rounded">
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-slate-50">
                  <tr>
                    <th className="px-3 py-1.5 text-center w-20 text-slate-500">
                      <button className="text-blue-600 hover:underline" onClick={() => { const a: Record<string,boolean>={};allAccountNames.forEach(n=>{a[n]=true});setDraftSelection(a) }}>All</button>
                      {' / '}
                      <button className="text-blue-600 hover:underline" onClick={() => { const a: Record<string,boolean>={};allAccountNames.forEach(n=>{a[n]=false});setDraftSelection(a) }}>None</button>
                    </th>
                    <th className="px-3 py-1.5 text-left font-semibold text-slate-500">Account</th>
                    <th className="px-3 py-1.5 text-left font-semibold text-slate-500">Type</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {allAccountNames.map(name => (
                    <tr key={name} className="hover:bg-slate-50">
                      <td className="px-3 py-1.5 text-center">
                        <input type="checkbox" className="rounded"
                          checked={draftSelection[name] === undefined ? true : draftSelection[name]}
                          onChange={e => setDraftSelection(prev => ({ ...prev!, [name]: e.target.checked }))} />
                      </td>
                      <td className="px-3 py-1.5">{name}{accountActive[name] === false && <span className="ml-1.5 text-slate-400">(inactive)</span>}</td>
                      <td className="px-3 py-1.5 text-slate-500">{accountMeta[name]}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="mt-2 flex gap-2">
              <button onClick={saveSel} className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-slate-700 text-white text-xs font-medium hover:bg-slate-800">💾 Save Selection</button>
              <button onClick={() => setSelOpen(false)} className="px-3 py-1.5 rounded border border-slate-300 text-xs text-slate-600 hover:bg-slate-50">Cancel</button>
            </div>
          </div>
        )}
      </div>

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
            {tab === 'Overview'          && <NwOverview rows={filteredRows} allPeriods={allPeriods} grouping={effGrouping} />}
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
function InvPositionsGraph({ startDate }: { startDate: string }) {
  const { isDark } = useTheme()
  const { data = [], isLoading } = useQuery({
    queryKey: ['inv-positions-history', startDate],
    queryFn: () => getInvestmentPositionsHistory(startDate),
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

function InvPositionsSummary({ startDate }: { startDate: string }) {
  const { data = [], isLoading } = useQuery({
    queryKey: ['inv-positions-history', startDate],
    queryFn: () => getInvestmentPositionsHistory(startDate),
  })
  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  const rows = data as Row[]
  return <PivotTable data={rows} groupBy="accounts_name" colKey="date" valKey="value_eur" showTotal={false} />
}

function SectorTab() {
  const { isDark } = useTheme()
  const { data = [], isLoading } = useQuery({ queryKey: ['sector-allocation'], queryFn: getSectorAllocation })
  const rows = data as Row[]
  const { sorted: sectorSorted, sortKey: sectorSK, sortDir: sectorSD, toggleSort: sectorSort } = useSortTable(rows, 'value_eur', 'desc')
  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  const bySector: Record<string, number> = {}
  for (const r of rows) bySector[String(r.sector)] = (bySector[String(r.sector)] ?? 0) + Number(r.value_eur ?? 0)
  const sectors = Object.entries(bySector).sort((a, b) => b[1] - a[1])
  return (
    <div className="space-y-4">
      <Plot
        data={[{ x: sectors.map(s => s[1]), y: sectors.map(s => s[0]), type: 'bar', orientation: 'h', marker: { color: '#3b82f6' }, text: sectors.map(s => fmtEur(s[1])), textposition: 'outside' }]}
        layout={{ height: Math.max(300, sectors.length * 28), margin: { t: 10, r: 100, b: 40, l: 200 }, xaxis: { tickformat: ',.0f', tickprefix: '€' }, ...plotLayout(isDark) }}
        config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }} />
      <WithCopy>
      <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-300px)] text-xs">
        <table className="w-full border-collapse">
          <thead className="sticky top-0 z-10 bg-slate-50">
            <tr className="bg-slate-50 text-xs text-slate-500">
              <ColHeader label="Sector" sortKey="sector" currentKey={sectorSK} currentDir={sectorSD} onSort={sectorSort} className="text-left px-2 py-1.5 border-b border-slate-200" />
              <ColHeader label="Industry" sortKey="industry" currentKey={sectorSK} currentDir={sectorSD} onSort={sectorSort} className="text-left px-2 py-1.5 border-b border-slate-200" />
              <ColHeader label="Value (€)" sortKey="value_eur" currentKey={sectorSK} currentDir={sectorSD} onSort={sectorSort} align="right" className="px-2 py-1.5 border-b border-slate-200" />
              <ColHeader label="Weight %" sortKey="actual_pct" currentKey={sectorSK} currentDir={sectorSD} onSort={sectorSort} align="right" className="px-2 py-1.5 border-b border-slate-200" />
            </tr>
          </thead>
          <tbody>
            {sectorSorted.map((r, i) => (
              <tr key={i} className="border-b border-slate-100 hover:bg-slate-50">
                <td className="px-2 py-1.5">{String(r.sector)}</td>
                <td className="px-2 py-1.5 text-slate-500">{String(r.industry)}</td>
                <td className="px-2 py-1.5 text-right tabular-nums">{fmtEur(Number(r.value_eur))}</td>
                <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{Number(r.actual_pct).toFixed(1)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      </WithCopy>
    </div>
  )
}

function FxExposureTab() {
  const { isDark } = useTheme()
  const { data = [], isLoading } = useQuery({ queryKey: ['fx-exposure'], queryFn: getFxExposure })
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

function AllocationReport() {
  const { isDark } = useTheme()
  const qc = useQueryClient()
  const [editOpen, setEditOpen] = useState(false)
  const [cash, setCash] = useState(0)

  const { data: donut = [], isLoading: donutLoading } = useQuery({ queryKey: ['allocation', 'investments'], queryFn: () => getAllocationReport('investments') })
  const { data: targets = [], isLoading: targetsLoading } = useQuery({ queryKey: ['allocation-targets'], queryFn: getAllocationTargets })
  const { data: delta = [], isLoading: deltaLoading } = useQuery({ queryKey: ['allocation-delta'], queryFn: getAllocationDelta })
  const { data: plan = [], isLoading: planLoading } = useQuery({ queryKey: ['rebalancing-plan'], queryFn: getRebalancingPlan })

  // Local editable target state
  const [localTargets, setLocalTargets] = useState<Record<string, number>>({})
  useEffect(() => {
    if ((targets as Row[]).length > 0) {
      const m: Record<string, number> = {}
      ;(targets as Row[]).forEach(r => { m[String(r.securities_type)] = Number(r.target_pct) })
      setLocalTargets(m)
    }
  }, [targets])

  const saveMutation = useMutation({
    mutationFn: saveAllocationTargets,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['allocation-targets'] })
      qc.invalidateQueries({ queryKey: ['allocation-delta'] })
      qc.invalidateQueries({ queryKey: ['rebalancing-plan'] })
      setEditOpen(false)
    },
  })

  const d = donut as Row[]
  const deltaRows = delta as Row[]
  const planRows = plan as Row[]

  const sumTargets = Object.values(localTargets).reduce((s, v) => s + v, 0)
  const sumOk = Math.abs(sumTargets - 100) < 0.01

  // Actual vs target bar chart data
  const barTypes = deltaRows.map(r => String(r.securities_type))
  const actualPcts = deltaRows.map(r => Number(r.actual_pct))
  const targetPcts = deltaRows.map(r => Number(r.target_pct))

  // Rebalancing plan with optional extra cash
  const portfolioTotal = planRows.length > 0 ? Number(planRows[0].portfolio_total_eur ?? 0) : 0
  const planWithCash: (Row & { total_delta_eur: number; est_shares: number })[] = planRows.map(r => {
    const baseDelta = Number(r.suggested_delta_eur ?? 0)
    const typeTgt = Number(r.type_target_pct ?? 0)
    const cashForType = portfolioTotal > 0 ? (typeTgt / 100) * cash : 0
    const typeWeight = Number(r.value_eur ?? 0) / (deltaRows.find(d2 => String(d2.securities_type) === String(r.type))?.value_eur as number || 1)
    const totalDelta = baseDelta + typeWeight * cashForType
    const estShares = Number(r.price ?? 0) > 0 ? totalDelta / (Number(r.price) * Number(r.fx_rate ?? 1)) : 0
    return { ...r, total_delta_eur: totalDelta, est_shares: estShares }
  }).filter(r => Math.abs(r.total_delta_eur) > 0.5)

  const totalBuy = planWithCash.filter(r => r.total_delta_eur > 0).reduce((s, r) => s + r.total_delta_eur, 0)
  const totalSell = planWithCash.filter(r => r.total_delta_eur < 0).reduce((s, r) => s + r.total_delta_eur, 0)

  if (donutLoading || targetsLoading || deltaLoading || planLoading) return <div className="flex justify-center py-12"><Spinner /></div>

  return (
    <div className="space-y-6">
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
            <p className="text-xs text-slate-500">Rows are pre-filled from your current holdings and any previously saved targets. All changes are saved on click.</p>
            <div className="overflow-x-auto text-xs">
              <table className="w-full border-collapse">
                <thead><tr className="bg-slate-50 text-slate-500">
                  <th className="text-left px-3 py-2 border-b border-slate-200">Asset Type</th>
                  <th className="text-right px-3 py-2 border-b border-slate-200">Actual %</th>
                  <th className="text-right px-3 py-2 border-b border-slate-200">Target %</th>
                </tr></thead>
                <tbody>
                  {d.map((r, i) => {
                    const key = String(r.label)
                    return (
                      <tr key={i} className="border-b border-slate-100">
                        <td className="px-3 py-2 font-medium">{key}</td>
                        <td className="px-3 py-2 text-right tabular-nums text-slate-500">
                          {(Number(r.value_eur) / d.reduce((s, x) => s + Number(x.value_eur), 0) * 100).toFixed(2)}%
                        </td>
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

      {/* Donut + Bar charts side by side */}
      <div className="grid grid-cols-2 gap-4">
        <div>
          <h3 className="text-sm font-semibold text-slate-600 mb-2">Current Allocation</h3>
          <Plot
            data={[{ values: d.map(r => Number(r.value_eur)), labels: d.map(r => String(r.label)), type: 'pie', hole: 0.45, textinfo: 'label+percent' }]}
            layout={{ height: 360, margin: { t: 10, r: 10, b: 10, l: 10 }, showlegend: true, legend: { orientation: 'v' }, ...plotLayout(isDark) }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: '100%' }}
          />
        </div>
        <div>
          <h3 className="text-sm font-semibold text-slate-600 mb-2">Actual vs. Target (%)</h3>
          <Plot
            data={[
              { x: barTypes, y: actualPcts, name: 'Actual %', type: 'bar', marker: { color: '#3b82f6' } },
              { x: barTypes, y: targetPcts, name: 'Target %', type: 'bar', marker: { color: '#f59e0b' } },
            ]}
            layout={{ height: 360, margin: { t: 10, r: 10, b: 60, l: 40 }, barmode: 'group', yaxis: { title: '%' }, legend: { orientation: 'h', y: -0.3 }, ...plotLayout(isDark) }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: '100%' }}
          />
        </div>
      </div>

      {/* Rebalancing Delta */}
      {deltaRows.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-slate-700 mb-2">Rebalancing Delta</h3>
          <WithCopy>
          <div className="overflow-x-auto text-xs">
            <table className="w-full border-collapse">
              <thead><tr className="bg-slate-50 text-slate-500 text-xs">
                <th className="text-left px-3 py-2 border-b border-slate-200">Asset Type</th>
                <th className="text-right px-3 py-2 border-b border-slate-200">Value (€)</th>
                <th className="text-right px-3 py-2 border-b border-slate-200">Actual %</th>
                <th className="text-right px-3 py-2 border-b border-slate-200">Target %</th>
                <th className="text-right px-3 py-2 border-b border-slate-200">Delta %</th>
                <th className="text-right px-3 py-2 border-b border-slate-200">Rebalance €</th>
              </tr></thead>
              <tbody>
                {deltaRows.map((r, i) => {
                  const delta = Number(r.delta_pct ?? 0)
                  const reb = Number(r.rebalance_eur ?? 0)
                  return (
                    <tr key={i} className="border-b border-slate-100 hover:bg-slate-50">
                      <td className="px-3 py-2 font-medium">{String(r.securities_type)}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{fmtEur(Number(r.value_eur))}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{Number(r.actual_pct).toFixed(2)}%</td>
                      <td className="px-3 py-2 text-right tabular-nums">{Number(r.target_pct).toFixed(2)}%</td>
                      <td className={`px-3 py-2 text-right tabular-nums font-medium ${delta > 0 ? 'text-red-500' : delta < 0 ? 'text-green-600' : ''}`}>{delta > 0 ? '+' : ''}{delta.toFixed(2)}%</td>
                      <td className={`px-3 py-2 text-right tabular-nums font-medium ${reb > 0 ? 'text-green-600' : reb < 0 ? 'text-red-500' : ''}`}>{reb > 0 ? '+' : ''}{fmtEur(reb)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          </WithCopy>
        </div>
      )}

      {/* Rebalancing Action Plan */}
      {planRows.length > 0 && (
        <div>
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-sm font-semibold text-slate-700">⚖️ Rebalancing Action Plan</h3>
          </div>
          <p className="text-xs text-slate-500 mb-3">Trades are distributed proportionally within each asset type. Only types with a saved target are included. Positive = buy, negative = sell.</p>
          <div className="flex items-center gap-3 mb-3">
            <label className="text-xs text-slate-600 font-medium">Available cash to deploy (€)</label>
            <div className="flex items-center border border-slate-200 rounded overflow-hidden">
              <button onClick={() => setCash(c => Math.max(0, c - 100))} className="px-2 py-1 text-slate-500 hover:bg-slate-100 text-sm">−</button>
              <input type="number" min={0} value={cash} onChange={e => setCash(parseFloat(e.target.value) || 0)}
                className="w-28 text-center border-none text-xs px-2 py-1 focus:outline-none" />
              <button onClick={() => setCash(c => c + 100)} className="px-2 py-1 text-slate-500 hover:bg-slate-100 text-sm">+</button>
            </div>
          </div>
          <WithCopy>
          <div className="overflow-x-auto text-xs max-h-[520px] overflow-y-auto">
            <table className="border-collapse" style={{ minWidth: '100%' }}>
              <thead className="sticky top-0 z-20"><tr className="bg-slate-50 text-slate-500 text-xs">
                <th className="sticky left-0 z-30 bg-slate-50 text-left px-2 py-2 border-b border-slate-200 whitespace-nowrap">Action</th>
                <th className="sticky left-[72px] z-30 bg-slate-50 text-left px-2 py-2 border-b border-slate-200 whitespace-nowrap">Security</th>
                <th className="text-left px-2 py-2 border-b border-slate-200 whitespace-nowrap">Type</th>
                <th className="text-left px-2 py-2 border-b border-slate-200 whitespace-nowrap">Ticker</th>
                <th className="text-left px-2 py-2 border-b border-slate-200 whitespace-nowrap">Ccy</th>
                <th className="text-right px-2 py-2 border-b border-slate-200 whitespace-nowrap">Qty</th>
                <th className="text-right px-2 py-2 border-b border-slate-200 whitespace-nowrap">Price</th>
                <th className="text-right px-2 py-2 border-b border-slate-200 whitespace-nowrap">Value (€)</th>
                <th className="text-right px-2 py-2 border-b border-slate-200 whitespace-nowrap">Weight %</th>
                <th className="text-right px-2 py-2 border-b border-slate-200 whitespace-nowrap">Type Act %</th>
                <th className="text-right px-2 py-2 border-b border-slate-200 whitespace-nowrap">Type Tgt %</th>
                <th className="text-right px-2 py-2 border-b border-slate-200 whitespace-nowrap">Type Δ %</th>
                <th className="text-right px-2 py-2 border-b border-slate-200 whitespace-nowrap">Trade (€)</th>
                <th className="text-right px-2 py-2 border-b border-slate-200 whitespace-nowrap">Est. Shares</th>
              </tr></thead>
              <tbody>
                {planWithCash.map((r, i) => {
                  const isBuy = r.total_delta_eur > 0
                  const typeDelta = Number(r.type_delta_pct ?? 0)
                  const rowBg = i % 2 === 0 ? 'bg-white' : 'bg-slate-50'
                  return (
                    <tr key={i} className={`border-b border-slate-100 hover:bg-blue-50 ${rowBg}`}>
                      <td className={`sticky left-0 z-10 ${rowBg} px-2 py-1.5 whitespace-nowrap`}>
                        <span className={`inline-flex items-center gap-1 font-bold text-xs px-1.5 py-0.5 rounded ${isBuy ? 'text-green-600' : 'text-red-500'}`}>
                          <span className={`w-2 h-2 rounded-full ${isBuy ? 'bg-green-500' : 'bg-red-500'}`}></span>
                          {isBuy ? 'BUY' : 'SELL'}
                        </span>
                      </td>
                      <td className={`sticky left-[72px] z-10 ${rowBg} px-2 py-1.5 font-medium max-w-[200px] truncate whitespace-nowrap`}><SecLink id={r.securities_id}>{String(r.security)}</SecLink></td>
                      <td className="px-2 py-1.5 text-slate-500">{String(r.type)}</td>
                      <td className="px-2 py-1.5 font-mono text-slate-500"><SecLink id={r.securities_id}>{String(r.ticker ?? '—')}</SecLink></td>
                      <td className="px-2 py-1.5 text-slate-500">{String(r.currency ?? '—')}</td>
                      <td className="px-2 py-1.5 text-right tabular-nums">{fmtNum(Number(r.qty), 4)}</td>
                      <td className="px-2 py-1.5 text-right tabular-nums">{fmtNum(Number(r.price), 4)}</td>
                      <td className="px-2 py-1.5 text-right tabular-nums">{fmtEur(Number(r.value_eur))}</td>
                      <td className="px-2 py-1.5 text-right tabular-nums">{Number(r.weight_pct).toFixed(2)}%</td>
                      <td className="px-2 py-1.5 text-right tabular-nums">{Number(r.type_actual_pct).toFixed(2)}%</td>
                      <td className="px-2 py-1.5 text-right tabular-nums">{Number(r.type_target_pct).toFixed(2)}%</td>
                      <td className={`px-2 py-1.5 text-right tabular-nums font-medium ${typeDelta > 0 ? 'text-red-500' : 'text-green-600'}`}>
                        {typeDelta > 0 ? '+' : ''}{typeDelta.toFixed(2)}%
                      </td>
                      <td className={`px-2 py-1.5 text-right tabular-nums font-medium ${isBuy ? 'text-green-600' : 'text-red-500'}`}>
                        {isBuy ? '+' : ''}{fmtEur(r.total_delta_eur)}
                      </td>
                      <td className={`px-2 py-1.5 text-right tabular-nums ${isBuy ? 'text-green-600' : 'text-red-500'}`}>
                        {isBuy ? '+' : ''}{r.est_shares.toFixed(4)}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          </WithCopy>
          <div className="grid grid-cols-3 gap-4 mt-4">
            <KpiCard label="Total to Buy" value={fmtEur(totalBuy)} color="text-green-600" />
            <KpiCard label="Total to Sell" value={fmtEur(totalSell)} color="text-red-500" />
            <KpiCard label="Net Cash Needed" value={fmtEur(totalBuy + totalSell)} color="text-slate-700" />
          </div>
        </div>
      )}
    </div>
  )
}

function HoldingsSnapshotTab() {
  const { data = [], isLoading } = useQuery({ queryKey: ['portfolio-summary'], queryFn: getPortfolioSummary })
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
                <td className="px-2 py-1.5 text-slate-500">{String(r.account)}</td>
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

function DetailAnalysisTab({ asOf }: { asOf: string }) {
  // Fetch available month-end dates within the reporting period
  const { data: histData = [] } = useQuery({
    queryKey: ['inv-positions-history', asOf],
    queryFn: () => getInvestmentPositionsHistory(asOf),
  })
  const availableDates = [...new Set((histData as Row[]).map(r => String(r.date)))].sort().reverse()
  const [selectedDate, setSelectedDate] = useState<string>('')
  const snapshotDate = selectedDate || availableDates[0] || asOf

  const { data = [], isLoading } = useQuery({
    queryKey: ['holdings-snapshot', snapshotDate],
    queryFn: () => getHoldingsSnapshot(snapshotDate),
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
              <td className="px-2 py-1.5 text-slate-500">{String(r.account)}</td>
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

  // Default to Dec 31 of the previous calendar year
  const defaultDate = `${new Date().getFullYear() - 1}-12-31`
  const [asOf, setAsOf] = useState(initialStartDate || defaultDate)

  return (
    <div className="space-y-3">
      {/* Shared date control — applies to Graph, Summary and Detail Analysis */}
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

      <SubTabs tabs={['Graph', 'Summary', 'Detail Analysis', 'Current Holdings', 'Allocation', 'Sector & Industry', 'FX Exposure']} active={tab} onChange={setTab} />
      {tab === 'Graph' && <InvPositionsGraph startDate={asOf} />}
      {tab === 'Summary' && <InvPositionsSummary startDate={asOf} />}
      {tab === 'Detail Analysis' && <DetailAnalysisTab asOf={asOf} />}
      {tab === 'Current Holdings' && <HoldingsSnapshotTab />}
      {tab === 'Allocation' && <AllocationReport />}
      {tab === 'Sector & Industry' && <SectorTab />}
      {tab === 'FX Exposure' && <FxExposureTab />}
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════════
// 3. INVESTMENT PERFORMANCE
// ════════════════════════════════════════════════════════════════════════════
type PnlWindow = 'dtd' | 'wtd' | 'mtd' | 'qtd' | 'ytd' | 'all'
const PNL_WINDOWS = [
  { k: 'dtd' as PnlWindow, label: 'D' }, { k: 'wtd' as PnlWindow, label: 'W' },
  { k: 'mtd' as PnlWindow, label: 'M' }, { k: 'qtd' as PnlWindow, label: 'Q' },
  { k: 'ytd' as PnlWindow, label: 'YTD' }, { k: 'all' as PnlWindow, label: 'All' },
]
function pnlKey(w: PnlWindow) { return w === 'all' ? 'pnl_net_all_time_eur' : `pnl_${w}_eur` }

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
  const [win, setWin] = usePersist<PnlWindow>('pnl_win', 'ytd')
  const [showClosedAccounts, setShowClosedAccounts] = usePersist('pnl_showClosedAccounts', false)
  const [showFxSplit, setShowFxSplit] = usePersist('pnl_showFxSplit', false)
  const [showPct, setShowPct] = usePersist('pnl_showPct', true)
  const [showClosedPositions, setShowClosedPositions] = usePersist('pnl_showClosedPositions', false)
  const [searchParams, setSearchParams] = useSearchParams()
  const selectedAccount = searchParams.get('pnl_account')
  const setSelectedAccount = (acc: string | null) => {
    setSearchParams(prev => {
      const next = new URLSearchParams(prev)
      acc ? next.set('pnl_account', acc) : next.delete('pnl_account')
      return next
    }, { replace: false })
  }

  const { data = [], isLoading } = useQuery({ queryKey: ['pnl'], queryFn: () => getPnl('1900-01-01') })

  // Derive all data BEFORE any early return so hooks are always called in the same order
  const rows = data as Row[]
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

  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>

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
          </div>
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
  const [lookback, setLookback] = usePersist('twr_lookback', 730)
  const [cfOpen, setCfOpen] = useState(false)

  const { data, isLoading } = useQuery({
    queryKey: ['twr', lookback, accountIds],
    queryFn: () => getTwr(lookback, accountIds),
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
  const [lookback, setLookback] = usePersist('risk_lookback', 730)
  const [benchSecId, setBenchSecId] = usePersist<number | null>('risk_bench_sec_id', null)

  const { data: bmCandidates = [] } = useQuery({
    queryKey: ['benchmark-candidates'], queryFn: getBenchmarkCandidates, staleTime: 3_600_000,
  })
  const bms = bmCandidates as Row[]

  const { data, isLoading } = useQuery({
    queryKey: ['risk-metrics', lookback, benchSecId, accountIds],
    queryFn: () => getRiskMetrics(lookback, benchSecId, accountIds),
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
  const [divView, setDivView] = usePersist<'actual' | 'forecast' | 'recommendations'>('div_view', 'actual')

  // ── Actual state ─────────────────────────────────────────────────────────────
  const [period, setPeriod] = usePersist('div_period', 'YTD')
  const [customFrom, setCustomFrom] = usePersist('div_from', new Date(Date.now() - 365 * 86400000).toISOString().slice(0, 10))
  const [customTo, setCustomTo] = usePersist('div_to', new Date().toISOString().slice(0, 10))
  const [detailOpen, setDetailOpen] = useState(false)

  const { data, isLoading } = useQuery({
    queryKey: ['dividends-tracker', period, period === 'Custom' ? customFrom : null, period === 'Custom' ? customTo : null],
    queryFn: () => getDividendsTracker(period, period === 'Custom' ? customFrom : undefined, period === 'Custom' ? customTo : undefined),
  })

  // ── Forecast state ────────────────────────────────────────────────────────────
  const [upcomingOpen, setUpcomingOpen] = useState(false)

  const { data: fcData, isLoading: fcLoading } = useQuery({
    queryKey: ['dividends-forecast'],
    queryFn: getDividendsForecast,
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
    summary: { total_annual_eur: number; total_monthly_eur: number; securities_count: number; portfolio_yoc_pct: number }
    monthly_forecast: { month: string; income_eur: number }[]
    by_security: Row[]
    upcoming: Row[]
  }

  const result   = data   as TrackerResult  | undefined
  const fcResult = fcData as ForecastResult | undefined

  const { sorted: divSorted,  sortKey: divSK,  sortDir: divSD,  toggleSort: divSort  } = useSortTablePersisted(result?.by_security   ?? [], 'div-tracker-actual-sort', 'period_income_eur',    'desc')
  const { sorted: fcSorted,   sortKey: fcSK,   sortDir: fcSD,   toggleSort: fcSort   } = useSortTablePersisted(fcResult?.by_security ?? [], 'div-tracker-forecast-sort', 'annual_forecast_eur',  'desc')

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
  if (divView === 'forecast') {
    return (
      <div className="space-y-4">
        {ViewToggle}
        {fcLoading ? <div className="flex justify-center py-12"><Spinner /></div>
          : !fcResult || !fcResult.by_security.length ? (
            <p className="text-slate-400 text-sm py-8 text-center">No forecast data — no holdings with dividend yield, rate, or trailing income.</p>
          ) : (
          <>
            <div className="grid grid-cols-4 gap-4">
              <div className="bg-slate-50 rounded-lg p-4 text-center">
                <p className="text-xs text-slate-500 mb-1"><Tooltip text="Total projected dividend and interest income over the next 12 months, based on current holdings and forward yields.">Projected Annual</Tooltip></p>
                <p className="text-xl font-bold text-green-600">{fmtEur(fcResult.summary.total_annual_eur)}</p>
              </div>
              <div className="bg-slate-50 rounded-lg p-4 text-center">
                <p className="text-xs text-slate-500 mb-1"><Tooltip text="Annual forecast divided by 12 — average expected monthly income.">Monthly Average</Tooltip></p>
                <p className="text-xl font-bold">{fmtEur(fcResult.summary.total_monthly_eur)}</p>
              </div>
              <div className="bg-slate-50 rounded-lg p-4 text-center">
                <p className="text-xs text-slate-500 mb-1"><Tooltip text="Number of currently-held securities with enough data to forecast dividends.">Securities</Tooltip></p>
                <p className="text-xl font-bold">{fcResult.summary.securities_count}</p>
              </div>
              <div className="bg-slate-50 rounded-lg p-4 text-center">
                <p className="text-xs text-slate-500 mb-1"><Tooltip text="Projected annual income divided by the total cost basis of forecasted holdings.">Portfolio YOC</Tooltip></p>
                <p className="text-xl font-bold">{fcResult.summary.portfolio_yoc_pct.toFixed(2)}%</p>
              </div>
            </div>

            {fcResult.monthly_forecast.length > 0 && (
              <Plot
                data={[{ x: fcResult.monthly_forecast.map(m => m.month), y: fcResult.monthly_forecast.map(m => m.income_eur), type: 'bar', marker: { color: '#3b82f6' }, name: 'Projected' }]}
                layout={{
                  title: 'Projected Monthly Dividend Income (€) — Next 12 Months',
                  height: 320, margin: { t: 50, r: 20, b: 40, l: 60 },
                  yaxis: { title: 'Projected Income (€)' },
                  ...plotLayout(isDark),
                }}
                config={{ displayModeBar: false }} style={{ width: '100%' }}
              />
            )}

            <WithCopy>
              <div className="overflow-y-auto max-h-[calc(100vh-300px)]">
                <table className="w-full text-sm table-fixed">
                  <colgroup>
                    <col className="w-[22%]" />
                    <col className="w-[9%]" />
                    <col className="w-[9%]" />
                    <col className="w-[11%]" />
                    <col className="w-[7%]" />
                    <col className="w-[9%]" />
                    <col className="w-[9%]" />
                    <col className="w-[9%]" />
                    <col className="w-[8%]" />
                    <col className="w-[7%]" />
                  </colgroup>
                  <thead className="sticky top-0 z-10"><tr className="bg-slate-50 text-xs text-slate-500 uppercase tracking-wide">
                    <ColHeader label="Security"      sortKey="securities_name"        currentKey={fcSK} currentDir={fcSD} onSort={fcSort} tooltip="Security name." />
                    <ColHeader label="Annual (€)"    sortKey="annual_forecast_eur"    currentKey={fcSK} currentDir={fcSD} onSort={fcSort} align="right" tooltip="Projected annual dividend income in EUR based on current holdings." />
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
                        <td className="px-2 py-1.5 text-right tabular-nums font-semibold text-green-600">{fmtEur(Number(r.annual_forecast_eur))}</td>
                        <td className="px-2 py-1.5 text-right tabular-nums">{fmtEur(Number(r.per_payment_eur))}</td>
                        <td className="px-2 py-1.5 text-slate-500 truncate" title={String(r.frequency)}>{String(r.frequency)}</td>
                        <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{r.dividend_yield != null ? `${Number(r.dividend_yield).toFixed(2)}%` : '—'}</td>
                        <td className="px-2 py-1.5 text-right text-slate-500">{r.next_expected_ex_date ? String(r.next_expected_ex_date).slice(0, 10) : '—'}</td>
                        <td className="px-2 py-1.5 text-right text-slate-500">{r.next_expected_pay_date ? String(r.next_expected_pay_date).slice(0, 10) : '—'}</td>
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
            data={[{ x: result.monthly.map(m => m.month), y: result.monthly.map(m => m.income_eur), type: 'bar', marker: { color: '#2ecc71' } }]}
            layout={{
              title: `Monthly Dividend & Interest Income (€) — ${result.period_label}`,
              height: 320, margin: { t: 50, r: 20, b: 40, l: 60 },
              yaxis: { title: 'Income (€)' },
              ...plotLayout(isDark),
            }}
            config={{ displayModeBar: false }} style={{ width: '100%' }}
          />

          <WithCopy>
            <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-300px)]">
              <table className="w-full text-sm">
                <thead className="sticky top-0 z-10"><tr className="bg-slate-50 text-xs text-slate-500 uppercase tracking-wide">
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
                  {divSorted.map((r, i) => (
                    <tr key={i} className="hover:bg-slate-50">
                      <td className="px-3 py-2 font-medium"><SecLink id={r.securities_id}>{String(r.securities_name)}</SecLink></td>
                      <td className="px-3 py-2 text-slate-500">{String(r.securities_type)}</td>
                      <td className="px-3 py-2 text-right tabular-nums font-semibold">{fmtEur(Number(r.period_income_eur ?? 0))}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{fmtEur(Number(r.cost_basis_eur ?? 0))}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{r.yoc_pct != null ? `${Number(r.yoc_pct).toFixed(2)}%` : '—'}</td>
                      <td className="px-3 py-2 text-right tabular-nums text-slate-500">{r.fwd_yield_pct != null ? `${Number(r.fwd_yield_pct).toFixed(2)}%` : '—'}</td>
                      <td className="px-3 py-2 text-right text-slate-500">{r.ex_div_date ? String(r.ex_div_date).slice(0, 10) : '—'}</td>
                      <td className="px-3 py-2 text-slate-500">{r.div_frequency != null ? String(r.div_frequency) : '—'}</td>
                    </tr>
                  ))}
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
                            <td className="px-3 py-2 text-blue-700">{String(r.accounts_name)}</td>
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
  'All-Time': ['pnl_net_all_time_eur', 'pnl_net_all_time_percent', 'gross_invested_all_time_eur'],
}

function PerformanceTab() {
  const { isDark } = useTheme()
  const { data = [], isLoading } = useQuery({ queryKey: ['pnl-all'], queryFn: () => getPnl() })
  const [period, setPeriod] = usePersist('perf_period', 'Daily')
  const [viewPct, setViewPct] = usePersist('perf_view_pct', false)
  const [topN, setTopN] = usePersist('perf_top_n', 15)
  const [rankedOpen, setRankedOpen] = useState(false)

  const rows = data as Row[]

  const bySec = useMemo(() => {
    const agg: Record<string, Record<string, number>> = {}
    const sumCols = ['current_value_eur', 'gross_invested_all_time_eur', 'pnl_net_all_time_eur',
      'unrealized_pnl_eur', 'realized_pnl_eur', 'pnl_dtd_eur', 'pnl_ytd_eur', 'pnl_qtd_eur', 'pnl_mtd_eur', 'pnl_wtd_eur']
    for (const r of rows) {
      const name = String(r.securities_name)
      if (!agg[name]) agg[name] = {}
      for (const c of sumCols) agg[name][c] = (agg[name][c] ?? 0) + Number(r[c] ?? 0)
    }
    const list: (Record<string, number> & { securities_name: string })[] =
      Object.entries(agg).map(([name, vals]) => ({ ...vals, securities_name: name }) as Record<string, number> & { securities_name: string })
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
      } else {
        v.pnl_dtd_pct = NaN
        v.pnl_net_all_time_percent = NaN; v.pnl_ytd_percent = NaN
        v.pnl_wtd_pct = NaN; v.pnl_mtd_pct = NaN; v.pnl_qtd_pct = NaN
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

  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>

  const PerfRow = ({ v, rank }: { v: Record<string, unknown>; rank?: number }) => (
    <tr className="hover:bg-slate-50">
      {rank != null && <td className="px-3 py-2 text-slate-400">{rank}</td>}
      <td className="px-3 py-2 font-medium text-blue-700">{String(v.securities_name)}</td>
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
                  <th className="px-3 py-2 text-left"><Tooltip text="Security name as recorded in your holdings.">Security</Tooltip></th>
                  {viewPct ? <>
                    <th className="px-3 py-2 text-right"><Tooltip text="Percentage change in value over the selected period, relative to invested capital.">Change %</Tooltip></th>
                    <th className="px-3 py-2 text-right"><Tooltip text="Absolute profit or loss in euros over the selected period.">P&L (€)</Tooltip></th>
                  </> : <>
                    <th className="px-3 py-2 text-right"><Tooltip text="Absolute profit or loss in euros over the selected period.">P&L (€)</Tooltip></th>
                    {pctCol && <th className="px-3 py-2 text-right"><Tooltip text="Percentage change relative to invested capital.">Change %</Tooltip></th>}
                  </>}
                  {invCol && <th className="px-3 py-2 text-right"><Tooltip text="Total capital invested in this security (gross cost basis, excluding fees).">Invested (€)</Tooltip></th>}
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
                  <th className="px-3 py-2 text-left"><Tooltip text="Security name as recorded in your holdings.">Security</Tooltip></th>
                  {viewPct ? <>
                    <th className="px-3 py-2 text-right"><Tooltip text="Percentage change in value over the selected period, relative to invested capital.">Change %</Tooltip></th>
                    <th className="px-3 py-2 text-right"><Tooltip text="Absolute profit or loss in euros over the selected period.">P&L (€)</Tooltip></th>
                  </> : <>
                    <th className="px-3 py-2 text-right"><Tooltip text="Absolute profit or loss in euros over the selected period.">P&L (€)</Tooltip></th>
                    {pctCol && <th className="px-3 py-2 text-right"><Tooltip text="Percentage change relative to invested capital.">Change %</Tooltip></th>}
                  </>}
                  {invCol && <th className="px-3 py-2 text-right"><Tooltip text="Total capital invested in this security (gross cost basis, excluding fees).">Invested (€)</Tooltip></th>}
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
                    <th className="px-3 py-2 text-left w-12"><Tooltip text="Performance rank for the selected period — 1 = best performer.">Rank</Tooltip></th>
                    <th className="px-3 py-2 text-left"><Tooltip text="Security name as recorded in your holdings.">Security</Tooltip></th>
                    {viewPct ? <>
                      <th className="px-3 py-2 text-right"><Tooltip text="Percentage change in value over the selected period, relative to invested capital.">Change %</Tooltip></th>
                      <th className="px-3 py-2 text-right"><Tooltip text="Absolute profit or loss in euros over the selected period.">P&L (€)</Tooltip></th>
                    </> : <>
                      <th className="px-3 py-2 text-right"><Tooltip text="Absolute profit or loss in euros over the selected period.">P&L (€)</Tooltip></th>
                      {pctCol && <th className="px-3 py-2 text-right"><Tooltip text="Percentage change relative to invested capital.">Change %</Tooltip></th>}
                    </>}
                    {invCol && <th className="px-3 py-2 text-right"><Tooltip text="Total capital invested in this security (gross cost basis, excluding fees).">Invested (€)</Tooltip></th>}
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
  const { data, isLoading } = useQuery({ queryKey: ['savings-accounts'], queryFn: getSavingsAccounts })
  const result = data as { summary: Row; detail: Row[]; detail_last: Row[] } | undefined

  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  if (!result || !result.detail?.length) return <p className="text-slate-400 text-sm py-8 text-center">No savings accounts found.</p>

  const s = result.summary
  const pct = (v: unknown) => v != null ? `${Number(v).toFixed(2)}%` : '—'
  const days = (v: unknown) => v != null ? String(Math.round(Number(v))) : '—'
  const dateStr = (v: unknown) => v ? String(v).slice(0, 10) : '—'
  const chart = (s.chart as unknown as { accounts_name: string; annual_yoc_pct: number }[]) ?? []

  return (
    <div className="space-y-4">
      <div className="bg-slate-50 rounded-lg p-3 text-xs text-slate-500">
        <strong>Principal</strong> = non-interest cash inflows (deposits/transfers in, excluding interest). <strong>Total Interest</strong> = sum of splits categorised as 'Interest'. <strong>Cumulative YoC</strong> = Total Interest ÷ Principal × 100. <strong>APY</strong> = (1 + Total Interest / Principal) ^ (365 / holding days) − 1, i.e. the compound annualised rate implied by actual interest earned over the holding period.
      </div>

      <div className="grid grid-cols-5 gap-3">
        <div className="bg-slate-50 rounded-lg p-4 text-center"><p className="text-xs text-slate-500 mb-1"><Tooltip text="Number of savings accounts tracked (accounts whose transactions include interest income).">Savings Accounts</Tooltip></p><p className="text-xl font-bold">{Number(s.savings_accounts_count ?? 0)}</p></div>
        <div className="bg-slate-50 rounded-lg p-4 text-center"><p className="text-xs text-slate-500 mb-1"><Tooltip text="Sum of non-interest inflows (deposits and transfers in) across all savings accounts.">Total Principal</Tooltip></p><p className="text-xl font-bold">{fmtEur(Number(s.total_principal_eur ?? 0))}</p></div>
        <div className="bg-slate-50 rounded-lg p-4 text-center"><p className="text-xs text-slate-500 mb-1"><Tooltip text="Total interest credited to all savings accounts, from all time.">Total Interest Received</Tooltip></p><p className="text-xl font-bold text-green-700">{fmtEur(Number(s.total_interest_eur ?? 0))}</p></div>
        <div className="bg-slate-50 rounded-lg p-4 text-center"><p className="text-xs text-slate-500 mb-1"><Tooltip text="Average Annual Yield on Cost: interest ÷ principal × 100, averaged across all accounts.">Avg Annual YOC</Tooltip></p><p className="text-xl font-bold">{pct(s.avg_yoc_pct)}</p></div>
        <div className="bg-slate-50 rounded-lg p-4 text-center"><p className="text-xs text-slate-500 mb-1"><Tooltip text="Average Annual Percentage Yield: compound annualised rate implied by actual interest earned over the holding period — (1 + interest/principal)^(365/days) − 1.">Avg APY</Tooltip></p><p className="text-xl font-bold">{pct(s.avg_apy_pct)}</p></div>
      </div>

      {chart.length > 0 && (
        <Plot
          data={[{
            type: 'bar', orientation: 'h',
            x: chart.map(c => c.annual_yoc_pct),
            y: chart.map(c => c.accounts_name),
            text: chart.map(c => `${c.annual_yoc_pct.toFixed(2)}%`),
            textposition: 'outside',
            marker: { color: chart.map(c => c.annual_yoc_pct), colorscale: 'RdYlGn' },
          }]}
          layout={{ title: 'Annual Yield over Cost (%) per Savings Account', height: Math.max(280, chart.length * 45), margin: { t: 40, l: 10, r: 40, b: 40 }, yaxis: { automargin: true }, xaxis: { title: '%' }, ...plotLayout(isDark) }}
          config={{ displayModeBar: false }} style={{ width: '100%' }}
        />
      )}

      <div>
        <h4 className="text-sm font-semibold text-slate-700 mb-2">Detail</h4>
        <WithCopy>
          <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-300px)]">
            <table className="w-full text-sm">
              <thead className="sticky top-0 z-10"><tr className="bg-slate-50 text-xs text-slate-500 uppercase tracking-wide">
                <th className="px-3 py-2 text-left"><Tooltip text="Savings account name.">Account</Tooltip></th>
                <th className="px-3 py-2 text-left"><Tooltip text="Account type (e.g. Savings, Fixed Deposit).">Type</Tooltip></th>
                <th className="px-3 py-2 text-left"><Tooltip text="Account currency.">Curr</Tooltip></th>
                <th className="px-3 py-2 text-right"><Tooltip text="Total non-interest cash inflows (deposits and transfers in).">Principal</Tooltip></th>
                <th className="px-3 py-2 text-right"><Tooltip text="Sum of all interest income credited to this account.">Total Interest</Tooltip></th>
                <th className="px-3 py-2 text-right"><Tooltip text="Annualised interest based on the most recent interest payment, extrapolated over a full year.">Annual Interest (cash)</Tooltip></th>
                <th className="px-3 py-2 text-right"><Tooltip text="Current ledger balance of the account.">Current Balance</Tooltip></th>
                <th className="px-3 py-2 text-right"><Tooltip text="Annual Yield on Cost: total interest ÷ principal × 100. Reflects what the account has actually returned relative to deposits.">Annual YOC%</Tooltip></th>
                <th className="px-3 py-2 text-right"><Tooltip text="Annual Percentage Yield: compound annualised rate — (1 + interest/principal)^(365/holding_days) − 1.">APY%</Tooltip></th>
                <th className="px-3 py-2 text-right"><Tooltip text="Total number of days between the first and last transaction recorded for this account.">Holding Days</Tooltip></th>
                <th className="px-3 py-2 text-right"><Tooltip text="Date of the earliest recorded transaction.">First Tx</Tooltip></th>
                <th className="px-3 py-2 text-right"><Tooltip text="Date of the most recent recorded transaction.">Last Tx</Tooltip></th>
              </tr></thead>
              <tbody className="divide-y divide-slate-100">
                {result.detail.map((r, i) => (
                  <tr key={i} className="hover:bg-slate-50">
                    <td className="px-3 py-2 font-medium">{String(r.accounts_name)}</td>
                    <td className="px-3 py-2 text-slate-500">{String(r.accounts_type)}</td>
                    <td className="px-3 py-2 text-slate-500">{String(r.currency)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{fmtNum(Number(r.principal ?? 0), 2)}</td>
                    <td className="px-3 py-2 text-right tabular-nums text-green-700">{fmtNum(Number(r.total_interest ?? 0), 2)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{fmtNum(Number(r.annual_interest_cash ?? 0), 2)}</td>
                    <td className="px-3 py-2 text-right tabular-nums font-semibold">{fmtNum(Number(r.current_balance ?? 0), 2)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{pct(r.annual_yoc_pct)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{pct(r.apy_pct)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{days(r.holding_days_total)}</td>
                    <td className="px-3 py-2 text-right text-slate-500 whitespace-nowrap">{dateStr(r.first_tx_date)}</td>
                    <td className="px-3 py-2 text-right text-slate-500 whitespace-nowrap">{dateStr(r.last_tx_date)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </WithCopy>
      </div>

      <div>
        <h4 className="text-sm font-semibold text-slate-700 mb-2">Detail for Last Interest Period</h4>
        <WithCopy>
          <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-300px)]">
            <table className="w-full text-sm">
              <thead className="sticky top-0 z-10"><tr className="bg-slate-50 text-xs text-slate-500 uppercase tracking-wide">
                <th className="px-3 py-2 text-left"><Tooltip text="Savings account name.">Account</Tooltip></th>
                <th className="px-3 py-2 text-left"><Tooltip text="Account type.">Type</Tooltip></th>
                <th className="px-3 py-2 text-left"><Tooltip text="Account currency.">Curr</Tooltip></th>
                <th className="px-3 py-2 text-right"><Tooltip text="Average principal balance during the last interest period.">Avg Principal</Tooltip></th>
                <th className="px-3 py-2 text-right"><Tooltip text="Total interest received in the most recent interest period.">Last Interest</Tooltip></th>
                <th className="px-3 py-2 text-right"><Tooltip text="Last period's interest extrapolated to a full year.">Annual Interest (cash)</Tooltip></th>
                <th className="px-3 py-2 text-right"><Tooltip text="Annual Yield on Cost for the last period: interest ÷ average principal × 100.">Annual YOC%</Tooltip></th>
                <th className="px-3 py-2 text-right"><Tooltip text="Compound annualised rate for the last period — (1 + interest/principal)^(365/days) − 1.">APY%</Tooltip></th>
                <th className="px-3 py-2 text-right"><Tooltip text="Number of days in the last interest period.">Holding Days</Tooltip></th>
                <th className="px-3 py-2 text-right"><Tooltip text="Start date of the last interest period.">Period Start</Tooltip></th>
                <th className="px-3 py-2 text-right"><Tooltip text="Date when the last interest payment was credited.">Last Interest Date</Tooltip></th>
              </tr></thead>
              <tbody className="divide-y divide-slate-100">
                {result.detail_last.map((r, i) => (
                  <tr key={i} className="hover:bg-slate-50">
                    <td className="px-3 py-2 font-medium">{String(r.accounts_name)}</td>
                    <td className="px-3 py-2 text-slate-500">{String(r.accounts_type)}</td>
                    <td className="px-3 py-2 text-slate-500">{String(r.currency)}</td>
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
  const { data = [], isLoading } = useQuery({ queryKey: ['bond-schedule'], queryFn: getBondSchedule })
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

function BenchmarkTab({ accountIds }: { accountIds?: number[] }) {
  const { isDark } = useTheme()
  const { data: candidates = [] } = useQuery({ queryKey: ['benchmark-candidates'], queryFn: getBenchmarkCandidates })
  const [benchmarkId, setBenchmarkId] = usePersist<number | null>('bench_id', null)
  const [lookback, setLookback] = usePersist('bench_lookback', 365)
  const [resample, setResample] = usePersist('bench_resample', 'Daily')
  const cands = candidates as Row[]

  const effId = benchmarkId ?? (cands[0] ? Number(cands[0].id) : null)

  const { data = [], isLoading } = useQuery({
    queryKey: ['benchmark', effId, lookback, accountIds, resample],
    queryFn: () => getBenchmark(effId!, lookback, accountIds, resample),
    enabled: effId != null,
  })
  const rows = data as { date: string; portfolio: number; benchmark: number | null }[]

  const portReturn  = rows.length ? ((rows[rows.length - 1].portfolio / 100 - 1) * 100).toFixed(2) : null
  const benchReturn = rows.length && rows[rows.length - 1].benchmark != null
    ? ((rows[rows.length - 1].benchmark! / 100 - 1) * 100).toFixed(2) : null

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <label className="text-xs text-slate-500"><Tooltip text="Market index or security to compare your portfolio against. Both series are indexed to 100 at the start date.">Benchmark</Tooltip></label>
          <select className="rounded border border-slate-300 px-2 py-1 text-sm"
            value={effId ?? ''} onChange={e => setBenchmarkId(Number(e.target.value))}>
            {cands.map(c => <option key={String(c.id)} value={String(c.id)}>{String(c.name)}{c.ticker ? ` (${c.ticker})` : ''}</option>)}
          </select>
        </div>
        <div className="flex items-center gap-2">
          <label className="text-xs text-slate-500"><Tooltip text="Lookback window in calendar days: 3M = 91, 6M = 182, 1Y = 365, 2Y = 730, 3Y = 1095. Both portfolio and benchmark are indexed to 100 at the start date.">Lookback</Tooltip></label>
          {([91, 182, 365, 730, 1095] as const).map(d => (
            <button key={d} onClick={() => setLookback(d)}
              className={`px-2 py-1 text-xs rounded border ${lookback === d ? 'bg-blue-600 text-white border-blue-600' : 'border-slate-300 text-slate-600 hover:bg-slate-50'}`}>
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
          <div className="bg-slate-50 rounded-lg p-4 text-center"><p className="text-xs text-slate-500 mb-1"><Tooltip text="Your portfolio's total return over the selected period (indexed: end value ÷ start value − 1). Value-weighted by current holdings.">Portfolio Return</Tooltip></p><p className={`text-xl font-bold ${Number(portReturn) >= 0 ? 'text-green-700' : 'text-red-600'}`}>{Number(portReturn) >= 0 ? '+' : ''}{portReturn}%</p></div>
          <div className="bg-slate-50 rounded-lg p-4 text-center"><p className="text-xs text-slate-500 mb-1"><Tooltip text="Selected benchmark's total return over the same period, indexed to the same start date as your portfolio.">Benchmark Return</Tooltip></p><p className={`text-xl font-bold ${Number(benchReturn) >= 0 ? 'text-green-700' : 'text-red-600'}`}>{Number(benchReturn) >= 0 ? '+' : ''}{benchReturn}%</p></div>
        </div>
      )}
      {isLoading ? <div className="flex justify-center py-12"><Spinner /></div> : rows.length > 0 && (
        <Plot
          data={[
            { x: rows.map(r => r.date), y: rows.map(r => r.portfolio), name: 'Portfolio', type: 'scatter', mode: 'lines', line: { color: '#3b82f6', width: 2 } },
            { x: rows.map(r => r.date), y: rows.map(r => r.benchmark), name: cands.find(c => Number(c.id) === effId)?.name as string ?? 'Benchmark', type: 'scatter', mode: 'lines', line: { color: '#f59e0b', width: 2, dash: 'dot' } },
          ]}
          layout={{ height: 380, yaxis: { title: 'Indexed (100 = start)', tickformat: '.1f' }, xaxis: { title: '' }, legend: { orientation: 'h', y: -0.2 }, margin: { t: 20, b: 60, l: 70, r: 20 }, ...plotLayout(isDark) }}
          config={{ displayModeBar: false }} style={{ width: '100%' }}
        />
      )}
    </div>
  )
}

function CorrelationTab({ accountIds }: { accountIds?: number[] }) {
  const [lookback, setLookback] = usePersist('corr_lookback', 252)
  const [maxH, setMaxH] = usePersist('corr_max', 20)

  const { data, isLoading } = useQuery({
    queryKey: ['correlation', lookback, maxH, accountIds],
    queryFn: () => getCorrelation(lookback, maxH, accountIds),
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

function PortfolioPresetBar({ onChange }: { onChange: (ids: number[] | undefined) => void }) {
  const [open, setOpen] = useState(false)
  const [selPreset, setSelPreset] = usePersist('perf_preset_sel', FULL_PORTFOLIO)
  const [nameInput, setNameInput] = useState('')
  const [draftIds, setDraftIds] = useState<Set<number> | null>(null)

  const { data: accounts = [] } = useQuery({ queryKey: ['allAccountsForPreset'], queryFn: () => getAccounts() })
  const { data: presets = [], refetch: refetchPresets } = useQuery({ queryKey: ['portfolio-presets'], queryFn: getPortfolioPresets })

  const invAccounts = (accounts as Row[]).filter(a => INV_ACCOUNT_TYPES.includes(String(a.type)) && a.is_active !== false && a.is_active !== 0 && a.is_active !== 'false')
  const presetList = presets as { preset_id: number; preset_name: string; account_ids: number[] }[]
  const presetMap = useMemo(() => {
    const m: Record<string, number[]> = {}
    for (const p of presetList) m[p.preset_name] = p.account_ids ?? []
    return m
  }, [presetList])

  const savedIds = selPreset === FULL_PORTFOLIO ? invAccounts.map(a => Number(a.id)) : (presetMap[selPreset] ?? [])
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
    await upsertPortfolioPreset(name, ids)
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
        <span>⚙️ Portfolio Preset {selPreset !== FULL_PORTFOLIO && <span className="text-blue-600">— {selPreset}</span>}</span>
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
          </div>
          <div className="max-h-48 overflow-y-auto border border-slate-200 rounded">
            {invAccounts.map(a => {
              const id = Number(a.id)
              const checked = selPreset === FULL_PORTFOLIO ? true : currentIds.has(id)
              return (
                <label key={id} className={`flex items-center gap-2 px-3 py-1.5 text-sm border-b border-slate-100 last:border-0 ${selPreset === FULL_PORTFOLIO ? 'opacity-50' : 'hover:bg-slate-50 cursor-pointer'}`}>
                  <input type="checkbox" className="rounded" checked={checked} disabled={selPreset === FULL_PORTFOLIO} onChange={() => toggleAccount(id)} />
                  <span>{String(a.name)}</span>
                  <span className="text-xs text-slate-400">({String(a.type)})</span>
                </label>
              )
            })}
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
      {needsPreset && <PortfolioPresetBar onChange={setPresetAccountIds} />}
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
  const [sortKey, setSortKey] = useState<string>('value_eur')
  const [sortAsc, setSortAsc] = useState(false)
  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  const rows = (data as Row[]).slice().sort((a, b) => {
    const av = Number(a[sortKey] ?? 0); const bv = Number(b[sortKey] ?? 0)
    return sortAsc ? av - bv : bv - av
  })
  const col = (key: string, label: string, align = 'right') => (
    <th className={`px-2 py-1.5 border-b border-slate-200 font-semibold cursor-pointer select-none text-${align} hover:bg-slate-100`}
      onClick={() => { if (sortKey === key) setSortAsc(!sortAsc); else { setSortKey(key); setSortAsc(false) } }}>
      {label}{sortKey === key ? (sortAsc ? ' ↑' : ' ↓') : ''}
    </th>
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
  high_3y: number | null
  low_3y: number | null
  pct_from_high_3y: number | null
  pct_from_low_3y: number | null
  recommendation_signal: string | null
  final_signal: string | null
  fwd_yield_pct: number | null
}

// ── Volatility Tab ────────────────────────────────────────────────────────────
function VolatilityTab() {
  const { data = [], isLoading } = usePortfolioSignals()
  const [volPeriod, setVolPeriod] = usePersist('vol_period', 'Annual Vol (ann)')
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
    .map(r => ({ name: r.securities_name, vol: Number(r[col]) }))

  const highVol = [...filtered].sort((a, b) => b.vol - a.vol).slice(0, 10)
  const lowVol  = [...filtered].sort((a, b) => a.vol - b.vol).slice(0, 10)

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
                <td className="px-3 py-2 font-medium">{r.name}</td>
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
      <div className="flex flex-wrap gap-1.5">
        {Object.keys(VOL_MAP).map(p => (
          <button key={p} onClick={() => setVolPeriod(p)}
            className={`px-3 py-1.5 text-xs rounded border font-medium ${volPeriod === p ? 'bg-blue-600 text-white border-blue-600' : 'border-slate-300 text-slate-600 hover:bg-slate-50'}`}>
            {p}
          </button>
        ))}
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

// ── Portfolio Action Signals Tab ──────────────────────────────────────────────
function PortfolioActionSignalsTab() {
  const { data = [], isLoading } = usePortfolioSignals()
  const [view, setView] = usePersist<'all' | 'open_only'>('sig_view', 'all')
  const rows = (data as Signal[]).map(r => ({
    ...r,
    unrealized_pnl_pct: r.unrealized_pnl_eur != null && r.total_cost_eur != null && Number(r.total_cost_eur) > 0
      ? Number(r.unrealized_pnl_eur) / Number(r.total_cost_eur) * 100
      : null,
  }))

  const filtered = rows.filter(r => {
    if (view === 'open_only') return Number(r.current_value_eur ?? 0) > 0
    return true
  })

  const [search, setSearch] = useState('')
  const searchFiltered = search.trim()
    ? filtered.filter(r => String(r.securities_name).toLowerCase().includes(search.trim().toLowerCase()))
    : filtered

  const { sorted: sortedFiltered, sortKey: pasSK, sortDir: pasSD, toggleSort: pasSort } = useSortTablePersisted(searchFiltered, 'portfolio-action-signals-sort', 'final_signal', 'asc')

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
          placeholder="Search security…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="ml-auto px-2.5 py-1.5 text-xs border border-slate-300 rounded w-44 focus:outline-none focus:border-blue-400"
        />
      </div>

      <WithCopy>
        <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-300px)]">
          <table className="w-full text-sm">
            <thead className="sticky top-0 z-10"><tr className="bg-slate-50 text-xs text-slate-500 uppercase tracking-wide">
              <ColHeader label={<Tooltip text="Security name.">Security</Tooltip>} sortKey="securities_name" currentKey={pasSK} currentDir={pasSD} onSort={pasSort} align="left" className="sticky left-0 bg-slate-50 text-xs text-slate-500 uppercase tracking-wide" />
              <ColHeader label={<Tooltip text="Combined signal: math signal + analyst rating. Conviction signals appear when both agree.">Final Signal</Tooltip>} sortKey="final_signal" currentKey={pasSK} currentDir={pasSD} onSort={pasSort} align="left" className="text-xs text-slate-500 uppercase tracking-wide" />
              <ColHeader label={<Tooltip text="Quantitative signal derived from Sharpe ratio and quality score.">Math Signal</Tooltip>} sortKey="recommendation_signal" currentKey={pasSK} currentDir={pasSD} onSort={pasSort} align="left" className="text-xs text-slate-500 uppercase tracking-wide" />
              <ColHeader label={<Tooltip text="Wall Street analyst consensus rating.">Analyst View</Tooltip>} sortKey="wall_street_view" currentKey={pasSK} currentDir={pasSD} onSort={pasSort} align="left" className="text-xs text-slate-500 uppercase tracking-wide" />
              <ColHeader label={<Tooltip text="Current market value of the position in EUR.">Value (€)</Tooltip>} sortKey="current_value_eur" currentKey={pasSK} currentDir={pasSD} onSort={pasSort} align="right" className="text-xs text-slate-500 uppercase tracking-wide" />
              <ColHeader label={<Tooltip text="Unrealized P&L: market value minus FIFO cost basis.">Unreal. P&L</Tooltip>} sortKey="unrealized_pnl_eur" currentKey={pasSK} currentDir={pasSD} onSort={pasSort} align="right" className="text-xs text-slate-500 uppercase tracking-wide" />
              <ColHeader label={<Tooltip text="Unrealized P&L as % of cost basis.">P&L %</Tooltip>} sortKey="unrealized_pnl_pct" currentKey={pasSK} currentDir={pasSD} onSort={pasSort} align="right" className="text-xs text-slate-500 uppercase tracking-wide" />
              <ColHeader label={<Tooltip text="Forward dividend yield based on analyst estimates.">Fwd Yield %</Tooltip>} sortKey="fwd_yield_pct" currentKey={pasSK} currentDir={pasSD} onSort={pasSort} align="right" className="text-xs text-slate-500 uppercase tracking-wide" />
              <ColHeader label={<Tooltip text="Sharpe ratio: excess return divided by annual volatility.">Sharpe</Tooltip>} sortKey="sharpe_ratio" currentKey={pasSK} currentDir={pasSD} onSort={pasSort} align="right" className="text-xs text-slate-500 uppercase tracking-wide" />
              <ColHeader label={<Tooltip text="Quality score: composite momentum (50% 1M + 30% 3M + 20% 1Y return).">Quality</Tooltip>} sortKey="quality_score" currentKey={pasSK} currentDir={pasSD} onSort={pasSort} align="right" className="text-xs text-slate-500 uppercase tracking-wide" />
              <ColHeader label={<Tooltip text="Most recent available market price.">Price</Tooltip>} sortKey="price_today" currentKey={pasSK} currentDir={pasSD} onSort={pasSort} align="right" className="text-xs text-slate-500 uppercase tracking-wide" />
              <ColHeader label={<Tooltip text="Highest price in the last 3 years (post-split adjusted).">3Y High</Tooltip>} sortKey="high_3y" currentKey={pasSK} currentDir={pasSD} onSort={pasSort} align="right" className="text-xs text-slate-500 uppercase tracking-wide" />
              <ColHeader label={<Tooltip text="Current price vs 3-year high as a percentage.">% from High</Tooltip>} sortKey="pct_from_high_3y" currentKey={pasSK} currentDir={pasSD} onSort={pasSort} align="right" className="text-xs text-slate-500 uppercase tracking-wide" />
              <ColHeader label={<Tooltip text="Lowest price in the last 3 years (post-split adjusted).">3Y Low</Tooltip>} sortKey="low_3y" currentKey={pasSK} currentDir={pasSD} onSort={pasSort} align="right" className="text-xs text-slate-500 uppercase tracking-wide" />
              <ColHeader label={<Tooltip text="Current price vs 3-year low as a percentage.">% from Low</Tooltip>} sortKey="pct_from_low_3y" currentKey={pasSK} currentDir={pasSD} onSort={pasSort} align="right" className="text-xs text-slate-500 uppercase tracking-wide" />
              <ColHeader label={<Tooltip text="Analyst target price vs current price — expected upside.">Upside %</Tooltip>} sortKey="upside_pct" currentKey={pasSK} currentDir={pasSD} onSort={pasSort} align="right" className="text-xs text-slate-500 uppercase tracking-wide" />
              <ColHeader label={<Tooltip text="Analyst consensus target price.">Target</Tooltip>} sortKey="target_price" currentKey={pasSK} currentDir={pasSD} onSort={pasSort} align="right" className="text-xs text-slate-500 uppercase tracking-wide" />
            </tr></thead>
            <tbody className="divide-y divide-slate-100">
              {sortedFiltered.map((r, i) => {
                const pnl = r.unrealized_pnl_eur
                const cost = r.total_cost_eur
                const pnlPct = r.unrealized_pnl_pct
                return (
                  <tr key={i} className={`hover:bg-slate-50 ${Number(r.current_value_eur ?? 0) === 0 ? 'opacity-60' : ''}`}>
                    <td className="px-3 py-2 font-medium text-blue-700 whitespace-nowrap sticky left-0 bg-white"><SecLink id={r.securities_id}>{String(r.securities_name)}</SecLink></td>
                    <td className={`px-3 py-2 whitespace-nowrap text-xs ${signalStyle(r.final_signal)}`}>{r.final_signal ?? '—'}</td>
                    <td className={`px-3 py-2 whitespace-nowrap text-xs ${signalStyle(r.recommendation_signal)}`}>{r.recommendation_signal ?? '—'}</td>
                    <td className="px-3 py-2">{analystBadge(r.wall_street_view)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{r.current_value_eur != null && Number(r.current_value_eur) > 0 ? fmtEur(Number(r.current_value_eur)) : '—'}</td>
                    <td className={`px-3 py-2 text-right tabular-nums ${pnl != null ? (Number(pnl) >= 0 ? 'text-green-700' : 'text-red-600') : ''}`}>
                      {pnl != null && cost != null && Number(cost) > 0 ? `${Number(pnl) >= 0 ? '+' : ''}${fmtEur(Number(pnl))}` : '—'}
                    </td>
                    <td className={`px-3 py-2 text-right tabular-nums ${pnlPct != null ? (pnlPct >= 0 ? 'text-green-700' : 'text-red-600') : ''}`}>
                      {pnlPct != null ? `${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%` : '—'}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-blue-700">
                      {r.fwd_yield_pct != null && Number(r.fwd_yield_pct) > 0 ? `${Number(r.fwd_yield_pct).toFixed(2)}%` : '—'}
                    </td>
                    <td className={`px-3 py-2 text-right tabular-nums font-semibold ${Number(r.sharpe_ratio ?? 0) >= 1 ? 'text-green-700' : Number(r.sharpe_ratio ?? 0) < 0 ? 'text-red-600' : 'text-slate-600'}`}>
                      {r.sharpe_ratio != null ? Number(r.sharpe_ratio).toFixed(2) : '—'}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-slate-600">
                      {r.quality_score != null ? Number(r.quality_score).toFixed(2) : '—'}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-slate-500">
                      {r.price_today != null ? fmtNum(Number(r.price_today), 4) : '—'}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-slate-500">
                      {r.high_3y != null ? fmtNum(Number(r.high_3y), 4) : '—'}
                    </td>
                    <td className={`px-3 py-2 text-right tabular-nums ${Number(r.pct_from_high_3y ?? 0) >= 0 ? 'text-green-700' : 'text-red-600'}`}>
                      {r.pct_from_high_3y != null ? `${Number(r.pct_from_high_3y) >= 0 ? '+' : ''}${Number(r.pct_from_high_3y).toFixed(2)}%` : '—'}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-slate-500">
                      {r.low_3y != null ? fmtNum(Number(r.low_3y), 4) : '—'}
                    </td>
                    <td className={`px-3 py-2 text-right tabular-nums ${Number(r.pct_from_low_3y ?? 0) >= 0 ? 'text-green-700' : 'text-red-600'}`}>
                      {r.pct_from_low_3y != null ? `${Number(r.pct_from_low_3y) >= 0 ? '+' : ''}${Number(r.pct_from_low_3y).toFixed(2)}%` : '—'}
                    </td>
                    <td className={`px-3 py-2 text-right tabular-nums font-semibold ${Number(r.upside_pct ?? 0) >= 0 ? 'text-green-700' : 'text-red-600'}`}>
                      {r.upside_pct != null ? `${Number(r.upside_pct) >= 0 ? '+' : ''}${Number(r.upside_pct).toFixed(2)}%` : '—'}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-slate-500">
                      {r.target_price != null ? Number(r.target_price).toFixed(2) : '—'}
                    </td>
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
                          <td className="px-2 py-1">{String(r.accounts_name ?? '')}</td>
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
                          <td className="px-2 py-1">{String(r.accounts_name ?? '')}</td>
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
                            <td className="px-2 py-1">{String(r.accounts_name ?? '')}</td>
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
const CF_COLOR_MAP: Record<string, string> = {
  'Income · Scheduled':           '#2ECC71',
  'Expense · Scheduled':          '#E74C3C',
  'Income · Recurring Template':  '#3498DB',
  'Expense · Recurring Template': '#F39C12',
  'Income · Recurring (est.)':    '#82E0AA',
  'Expense · Recurring (est.)':   '#F1948A',
}

function CashFlowSection() {
  const { isDark } = useTheme()
  const [days, setDays] = usePersist<number>('cf_days', 60)
  const [monthsBack, setMonthsBack] = usePersist<number>('cf_months_back', 2)

  const { data, isLoading } = useQuery({
    queryKey: ['cash-flow-forecast-full', days, monthsBack],
    queryFn: () => getCashFlowForecastFull(days, monthsBack),
  })

  const result = data as {
    scheduled: Row[]
    templates: Row[]
    recurring: Row[]
    metrics: { sched_in: number; sched_out: number; tmpl_in: number; tmpl_out: number; recur_in: number; recur_out: number; net_total: number }
  } | undefined

  // Build chart data: aggregate scheduled + templates + recurring by calendar month
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
  }, [result])

  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>

  const m = result?.metrics
  const scheduled = result?.scheduled ?? []
  const templates = result?.templates ?? []
  const recurring = result?.recurring ?? []

  const KPI_METRICS = m ? [
    { label: 'Scheduled In',  value: fmtEur(m.sched_in),  color: 'text-green-700', tip: 'Total income from explicitly scheduled future transactions within the horizon.' },
    { label: 'Scheduled Out', value: fmtEur(m.sched_out), color: 'text-red-600',   tip: 'Total expenses from explicitly scheduled future transactions within the horizon.' },
    { label: 'Template In',   value: fmtEur(m.tmpl_in),   color: 'text-blue-700',  tip: 'Total income projected from your active Recurring Templates within the horizon.' },
    { label: 'Template Out',  value: fmtEur(m.tmpl_out),  color: 'text-orange-600', tip: 'Total expenses projected from your active Recurring Templates within the horizon.' },
    { label: 'Recurring In',  value: fmtEur(m.recur_in),  color: 'text-green-600', tip: 'Estimated income from statistically-detected recurring patterns not already covered by a template, projected forward.' },
    { label: 'Recurring Out', value: fmtEur(m.recur_out), color: 'text-red-500',   tip: 'Estimated expenses from statistically-detected recurring patterns not already covered by a template, projected forward.' },
    { label: 'Total Net',     value: fmtEur(m.net_total), color: m.net_total >= 0 ? 'text-green-700' : 'text-red-600', tip: 'Net cash flow: sum of all scheduled, template, and recurring in/out amounts within the horizon.' },
  ] : []

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
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Tooltip text={`A payee + category pair must appear in every one of the last ${monthsBack} complete calendar months to be classified as recurring. Increase to require a longer consistent history; decrease to catch newer patterns.`}>
            <span className="text-sm text-slate-500 cursor-help underline decoration-dotted">Recurring window: <strong>{monthsBack}m</strong></span>
          </Tooltip>
          <input type="range" min={2} max={6} step={1} value={monthsBack}
            onChange={e => setMonthsBack(Number(e.target.value))}
            className="w-24 accent-blue-600" />
        </div>
      </div>

      {/* KPI metrics */}
      {KPI_METRICS.length > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3">
          {KPI_METRICS.map(k => (
            <div key={k.label} className="bg-slate-50 border border-slate-200 rounded-lg px-3 py-2.5">
              <Tooltip text={k.tip}>
                <div className="text-xs text-slate-500 mb-0.5 cursor-help underline decoration-dotted">{k.label}</div>
              </Tooltip>
              <div className={`text-sm font-bold tabular-nums ${k.color}`}>{k.value}</div>
            </div>
          ))}
        </div>
      )}

      {/* Bar chart */}
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

      {/* Explicitly Scheduled Future Transactions */}
      <div>
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
                    <td className="px-3 py-2 text-slate-500 text-xs">{String(r.accounts_name || '—')}</td>
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
      <div>
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
                    <td className="px-3 py-2 text-slate-500 text-xs">{String(r.accounts_name || '—')}</td>
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
      <div>
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
  type Group = { key: string; secId: unknown; security: string; ticker: string; account: string; rows: Row[]; proceeds: number; cost: number; gl: number }
  const groups: Group[] = []
  const groupMap = new Map<string, Group>()
  for (const r of rows) {
    const key = `${r.securities_id}__${r.account}`
    if (!groupMap.has(key)) {
      const g: Group = { key, secId: r.securities_id, security: String(r.security), ticker: String(r.ticker ?? '—'), account: String(r.account), rows: [], proceeds: 0, cost: 0, gl: 0 }
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
                  <td className="px-2 py-1.5 text-slate-600">{g.account}</td>
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
                <div className="bg-slate-50 rounded-lg px-4 py-3">
                  <div className="text-xs text-slate-500 mb-1">Exempt Net G/L</div>
                  <div className={`text-xl font-bold tabular-nums ${sum(dExempt) >= 0 ? 'text-green-700' : 'text-red-600'}`}>{fmt2(sum(dExempt))}</div>
                  <div className="text-xs text-slate-400 mt-0.5">0% tax</div>
                </div>
                <div className="bg-slate-50 rounded-lg px-4 py-3">
                  <div className="text-xs text-slate-500 mb-1">Taxable Gross Gains</div>
                  <div className="text-xl font-bold tabular-nums text-green-700">{fmt2(grossTaxableGains)}</div>
                  <div className="text-xs text-slate-400 mt-0.5">across all taxable categories</div>
                </div>
                <div className="bg-slate-50 rounded-lg px-4 py-3">
                  <div className="text-xs text-slate-500 mb-1">Capital Losses (info)</div>
                  <div className="text-xl font-bold tabular-nums text-red-600">{fmt2(grossTaxableLosses)}</div>
                  <div className="text-xs text-slate-400 mt-0.5">not deducted from tax estimate</div>
                </div>
                <div className="bg-amber-50 rounded-lg px-4 py-3">
                  <div className="text-xs text-amber-600 mb-1">Est. Capital Gains Tax</div>
                  <div className="text-xl font-bold tabular-nums text-amber-700">{fmt2(totalTaxEst)}</div>
                  <div className="text-xs text-slate-400 mt-0.5">on gross gains, per category rates</div>
                </div>
              </div>
            )
          })()}

          <hr className="border-slate-200" />

          {/* Taxable sections — one per tax category */}
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

          {/* Exempt section expander */}
          {dExempt.length > 0 && (() => {
            const exemptGrossG = dExempt.filter(r => Number(r.gain_loss_eur ?? 0) > 0).reduce((s, r) => s + Number(r.gain_loss_eur ?? 0), 0)
            const exemptGrossL = dExempt.filter(r => Number(r.gain_loss_eur ?? 0) < 0).reduce((s, r) => s + Number(r.gain_loss_eur ?? 0), 0)
            const exemptNet    = sum(dExempt)
            return (
              <div className="space-y-3">
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
                <td className="px-2 py-1 text-slate-500">{String(r.account_name ?? '')}</td>
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
                  <td className="px-3 py-2 text-slate-500">{g.account}</td>
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
  const totalExempt  = sum(divExempt)
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
          { label: 'Dividend Income',         val: totalDiv },
          { label: 'CD / Bond Interest',      val: totalIntInv },
          { label: 'Bank / Savings Interest', val: totalBank },
          { label: 'Taxable Total',           val: grandTotal },
        ].map(({ label, val }) => (
          <div key={label} className="bg-slate-50 rounded-lg px-4 py-3">
            <div className="text-xs text-slate-500 mb-1">{label}</div>
            <div className="text-xl font-bold tabular-nums text-slate-800">{fmt2(val)}</div>
          </div>
        ))}
      </div>

      {/* WHT + local liability row */}
      {(totalWithheld !== 0 || totalLocalLiability > 0) && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {totalWithheld !== 0 && (
            <div className="bg-red-50 rounded-lg px-4 py-3">
              <div className="text-xs text-red-500 mb-1">Total Withholding Tax</div>
              <div className="text-xl font-bold tabular-nums text-red-700">{fmt2(totalWithheld)}</div>
            </div>
          )}
          {totalWithheld !== 0 && (
            <div className="bg-slate-50 rounded-lg px-4 py-3">
              <div className="text-xs text-slate-500 mb-1">Net After Withholding</div>
              <div className="text-xl font-bold tabular-nums text-slate-800">{fmt2(grandTotal + totalWithheld)}</div>
            </div>
          )}
          {totalLocalLiability > 0 && (
            <div className="bg-amber-50 rounded-lg px-4 py-3">
              <div className="text-xs text-amber-600 mb-1">Dividend Local Tax Liability</div>
              <div className="text-xl font-bold tabular-nums text-amber-700">{fmt2(totalLocalLiability)}</div>
              <div className="text-xs text-amber-500 mt-1">max(0, gross × local rate − WHT credited)</div>
            </div>
          )}
          {totalIntTaxLiability > 0 && (
            <div className="bg-amber-50 rounded-lg px-4 py-3">
              <div className="text-xs text-amber-600 mb-1">CD / Bond Interest Tax (15%)</div>
              <div className="text-xl font-bold tabular-nums text-amber-700">{fmt2(totalIntTaxLiability)}</div>
              <div className="text-xs text-amber-500 mt-1">max(0, gross × 15% − WHT withheld)</div>
            </div>
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
      <div className="space-y-2">
        <h3 className="text-sm font-semibold text-slate-700">Dividend Income (incl. taxable Reinvest)</h3>
        {divRows.length === 0
          ? <p className="text-xs text-slate-400">No taxable dividend income for {year}.</p>
          : <IncomeTable rows={divRows} />}
      </div>

      {/* CD / Bond Interest */}
      {(intInvRows.length > 0 || intExempt.length > 0) && (
        <div className="space-y-2">
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
      <div className="space-y-2">
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
                                <td className="px-2 py-1.5 text-slate-500 text-xs">{String(r.account ?? '')}</td>
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
