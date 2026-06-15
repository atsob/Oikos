import React, { useState, useMemo, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import PlotlyReact from 'react-plotly.js'
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const Plot: React.ComponentType<any> = (PlotlyReact as any).default ?? PlotlyReact
import {
  getIncomeExpense, getSavingsRate, getTopCategories, getPortfolioSummary, getAllocationReport,
  getIncomeExpenseDetail, getDividends, getCapitalGains,
  getBudgetVsActual, getCashFlowForecast, getPnl, getCategoryBreakdown,
  getNetWorthByAccount, getInvestmentPositionsHistory, getSectorAllocation, getFxExposure,
  getSpendingByPayee, getSpendingTrends, getSavingsRateDetail,
  getTwr, getRiskMetrics, getTaxLossHarvesting, getDividendIncomeTax, getPriceChanges,
  getGoals, upsertGoal, deleteGoal,
} from '@/lib/api'
import { PageHeader, Card, CardBody, Input, Spinner, Button } from '@/components/ui'
import { fmtEur, fmtPct } from '@/lib/utils'
import { Trash2, Plus, Check, X } from 'lucide-react'

type Row = Record<string, unknown>

function usePersist<T>(key: string, defaultVal: T) {
  const [val, setVal] = useState<T>(() => {
    try { const s = localStorage.getItem(key); return s !== null ? JSON.parse(s) : defaultVal } catch { return defaultVal }
  })
  const set = useCallback((v: T) => { setVal(v); try { localStorage.setItem(key, JSON.stringify(v)) } catch {} }, [key])
  return [val, set] as const
}

// ── Sidebar tabs ──────────────────────────────────────────────────────────────
const REPORT_TABS = [
  { key: 'net-worth',       label: '📊 Net Worth' },
  { key: 'inv-positions',   label: '📈 Inv. Positions' },
  { key: 'inv-performance', label: '💹 Performance' },
  { key: 'securities',      label: '🔍 Securities' },
  { key: 'income-expense',  label: '💰 Income & Expense' },
  { key: 'cashflow',        label: '🔄 Cash Flow' },
  { key: 'budget',          label: '🎯 Budget & Spending' },
  { key: 'tax',             label: '🧾 Investment Tax' },
  { key: 'planning',        label: '🏖️ Financial Planning' },
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
function GroupingPicker({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <div className="flex gap-1">
      {['month', 'quarter', 'year'].map(g => (
        <button key={g} onClick={() => onChange(g)}
          className={`px-2.5 py-1 rounded text-xs font-medium capitalize transition-colors ${value === g ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}>
          {g}
        </button>
      ))}
    </div>
  )
}

function KpiCard({ label, value, color = '' }: { label: string; value: string; color?: string }) {
  return (
    <div className="bg-slate-50 rounded-lg p-3">
      <p className="text-xs text-slate-500 mb-1">{label}</p>
      <p className={`text-sm font-bold tabular-nums ${color}`}>{value}</p>
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
      <div className="overflow-x-auto text-xs">
        <table className="w-full border-collapse">
          <thead>
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
          </tbody>
        </table>
      </div>
    </WithCopy>
  )
}

// ── Hierarchical Pivot table ──────────────────────────────────────────────────
function HierarchicalPivotTable({ data, catTypeFilter, periods }: {
  data: Row[]; catTypeFilter: string; periods: string[]
}) {
  const filtered = data.filter(r => !catTypeFilter || String(r.cat_type) === catTypeFilter)
  const leafMap: Record<string, Record<string, number>> = {}
  for (const r of filtered) {
    const cat = String(r.category); const period = String(r.period)
    if (!leafMap[cat]) leafMap[cat] = {}
    leafMap[cat][period] = (leafMap[cat][period] ?? 0) + Number(r.total ?? 0)
  }
  const leafPaths = Object.keys(leafMap)
  const allPaths = new Set<string>(leafPaths)
  for (const path of leafPaths) {
    const parts = path.split(' : ')
    for (let i = 1; i < parts.length; i++) allPaths.add(parts.slice(0, i).join(' : '))
  }
  const sumForPath = (path: string) => {
    const leaves = leafPaths.filter(p => p === path || p.startsWith(path + ' : '))
    const result: Record<string, number> = {}
    for (const p of periods) result[p] = leaves.reduce((s, l) => s + (leafMap[l]?.[p] ?? 0), 0)
    return result
  }
  const sorted = [...allPaths].sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()))
  const rows = sorted.map(path => {
    const level = path.split(' : ').length - 1
    const isParent = [...allPaths].some(p => p !== path && p.startsWith(path + ' : '))
    return { path, level, isParent, label: path.split(' : ')[level], totals: sumForPath(path) }
  })
  const grandTotal: Record<string, number> = {}
  for (const p of periods) grandTotal[p] = rows.filter(r => r.level === 0).reduce((s, r) => s + (r.totals[p] ?? 0), 0)

  if (rows.length === 0) return <p className="text-sm text-slate-400 py-4">No data</p>
  return (
    <WithCopy>
    <div className="overflow-x-auto text-xs">
      <table className="w-full border-collapse">
        <thead>
          <tr className="bg-slate-50">
            <th className="text-left px-2 py-1.5 border-b border-slate-200 font-semibold sticky left-0 bg-slate-50 min-w-56">Category</th>
            {periods.map(p => <th key={p} className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold whitespace-nowrap">{p.slice(0, 7)}</th>)}
            <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold">Total</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(r => {
            const rowTotal = periods.reduce((s, p) => s + (r.totals[p] ?? 0), 0)
            return (
              <tr key={r.path} className={`border-b border-slate-100 ${r.isParent ? 'bg-slate-50 font-semibold' : 'hover:bg-slate-50'}`}>
                <td className={`px-2 py-1 sticky left-0 ${r.isParent ? 'bg-slate-50' : 'bg-white'}`} style={{ paddingLeft: `${8 + r.level * 16}px` }}>{r.label}</td>
                {periods.map(p => {
                  const v = r.totals[p] ?? 0
                  return <td key={p} className={`text-right px-2 py-1 tabular-nums ${v === 0 ? 'text-slate-300' : ''}`}>{v !== 0 ? fmtEur(v) : '—'}</td>
                })}
                <td className="text-right px-2 py-1 tabular-nums font-semibold">{fmtEur(rowTotal)}</td>
              </tr>
            )
          })}
          <tr className="border-t-2 border-slate-400 bg-slate-100 font-bold">
            <td className="px-2 py-1.5 sticky left-0 bg-slate-100">TOTAL</td>
            {periods.map(p => <td key={p} className="text-right px-2 py-1.5 tabular-nums">{fmtEur(grandTotal[p] ?? 0)}</td>)}
            <td className="text-right px-2 py-1.5 tabular-nums">{fmtEur(periods.reduce((s, p) => s + (grandTotal[p] ?? 0), 0))}</td>
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
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <KpiCard label="Net Worth" value={fmtEur(netWorth)} color={netWorth >= 0 ? 'text-blue-700' : 'text-red-600'} />
        {NW_ASSET_GROUPS.map(g => <KpiCard key={g} label={g} value={fmtEur(latest[g] ?? 0)} />)}
      </div>
      <Plot
        data={[
          ...NW_ASSET_GROUPS.map(g => ({ x: xs, y: allPeriods.map(p => byPeriod[p]?.[g] ?? 0), name: g, type: 'bar' as const, marker: { color: NW_GROUP_COLORS[g] } })),
          ...NW_LIAB_GROUPS.map(g => ({ x: xs, y: allPeriods.map(p => byPeriod[p]?.[g] ?? 0), name: g, type: 'bar' as const, marker: { color: NW_GROUP_COLORS[g] } })),
          { x: xs, y: allPeriods.map(p => NW_ASSET_GROUPS.reduce((s,g) => s+(byPeriod[p]?.[g]??0),0) + NW_LIAB_GROUPS.reduce((s,g) => s+(byPeriod[p]?.[g]??0),0)), name: 'Net Worth', type: 'scatter' as const, mode: 'lines+markers' as const, line: { color: '#1e40af', width: 2 }, marker: { size: 4, color: '#1e40af' }, yaxis: 'y' },
        ]}
        layout={{ barmode: 'relative' as const, height: 380, margin: { t: 10, r: 10, b: 40, l: 70 }, yaxis: { tickformat: ',.0f', tickprefix: '€' }, legend: { orientation: 'h' as const, y: -0.25 }, plot_bgcolor: 'white', paper_bgcolor: 'white', hovermode: 'x unified' as const }}
        config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }} />
    </div>
  )
}

function NwAccountBalances({ rows, allPeriods, accountMeta, grouping }: { rows: Row[]; allPeriods: string[]; accountMeta: Record<string, string>; grouping: string }) {
  const lookup: Record<string, Record<string, number>> = {}
  for (const r of rows) {
    const a = String(r.accounts_name), p = String(r.period)
    if (!lookup[a]) lookup[a] = {}
    lookup[a][p] = Number(r.balance_eur ?? 0)
  }
  const accounts = Object.keys(accountMeta)
    .filter(a => lookup[a])
    .sort((a, b) => accountMeta[a].localeCompare(accountMeta[b]) || a.localeCompare(b))
  const typeGroups: Record<string, string[]> = {}
  for (const a of accounts) {
    const t = accountMeta[a] ?? 'Other'
    if (!typeGroups[t]) typeGroups[t] = []
    typeGroups[t].push(a)
  }
  const headers = allPeriods.map(p => fmtPeriodHeader(p, grouping))

  // Stacked bar chart by account type group
  const byPeriod: Record<string, Record<string, number>> = {}
  for (const r of rows) {
    const p = String(r.period), g = nwGroup(String(r.accounts_type)), v = Number(r.balance_eur ?? 0)
    if (!byPeriod[p]) byPeriod[p] = {}
    byPeriod[p][g] = (byPeriod[p][g] ?? 0) + v
  }
  const xs = allPeriods.map(p => fmtPeriodLabel(p, grouping))

  return (
    <div className="space-y-4">
      <Plot
        data={[
          ...NW_ALL_GROUPS.map(g => ({ x: xs, y: allPeriods.map(p => byPeriod[p]?.[g] ?? 0), name: g, type: 'bar' as const, marker: { color: NW_GROUP_COLORS[g] } })),
          { x: xs, y: allPeriods.map(p => NW_ASSET_GROUPS.reduce((s,g) => s+(byPeriod[p]?.[g]??0),0) + NW_LIAB_GROUPS.reduce((s,g) => s+(byPeriod[p]?.[g]??0),0)), name: 'Balance', type: 'scatter' as const, mode: 'lines+markers' as const, line: { color: '#e879f9', width: 2 }, marker: { size: 4, color: '#e879f9' } },
        ]}
        layout={{ barmode: 'relative' as const, height: 340, margin: { t: 10, r: 10, b: 40, l: 70 }, yaxis: { tickformat: ',.0f', tickprefix: '€' }, legend: { orientation: 'h' as const, y: -0.3 }, plot_bgcolor: 'white', paper_bgcolor: 'white', hovermode: 'x unified' as const }}
        config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }} />
      <WithCopy>
      <div className="overflow-x-auto text-xs">
        <table className="w-full border-collapse">
          <thead>
            <tr className="bg-slate-50">
              <th className="text-left px-2 py-1.5 border-b border-slate-200 font-semibold sticky left-0 bg-slate-50 min-w-52">Account</th>
              <th className="text-left px-2 py-1.5 border-b border-slate-200 font-semibold sticky left-52 bg-slate-50 whitespace-nowrap">row_type</th>
              {headers.map((h, i) => <th key={i} className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold whitespace-nowrap">{h}</th>)}
            </tr>
          </thead>
          <tbody>
            {Object.entries(typeGroups).map(([type, accs]) => (
              <React.Fragment key={type}>
                <tr className="bg-slate-100">
                  <td className="px-2 py-1 text-slate-500 uppercase text-xs tracking-wide sticky left-0 bg-slate-100">{type.toUpperCase()}</td>
                  <td className="px-2 py-1 text-slate-400 sticky left-52 bg-slate-100">group_header</td>
                  {allPeriods.map(p => <td key={p} className="px-2 py-1 text-slate-300 text-right">None</td>)}
                </tr>
                {accs.map(acc => (
                  <tr key={acc} className="border-b border-slate-100 hover:bg-slate-50">
                    <td className="px-2 py-1.5 pl-5 sticky left-0 bg-white">{acc}</td>
                    <td className="px-2 py-1.5 text-slate-400 sticky left-52 bg-white">account</td>
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
  const headers = allPeriods.map(p => fmtPeriodHeader(p, grouping))
  return (
    <div className="space-y-3">
      <WithCopy>
      <div className="overflow-x-auto text-xs">
        <table className="w-full border-collapse">
          <thead>
            <tr className="bg-slate-50">
              <th className="text-left px-2 py-1.5 border-b border-slate-200 font-semibold sticky left-0 bg-slate-50 min-w-16">Period</th>
              {NW_ASSET_GROUPS.map(g => <th key={g} className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold whitespace-nowrap">{g}</th>)}
              {NW_LIAB_GROUPS.map(g => <th key={g} className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold whitespace-nowrap text-red-600">{g}</th>)}
              <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold whitespace-nowrap border-l border-slate-300">Total Assets</th>
              <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold whitespace-nowrap text-red-600">Total Liabilities</th>
              <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold whitespace-nowrap text-blue-700">Net Worth</th>
            </tr>
          </thead>
          <tbody>
            {[...allPeriods].reverse().map((p, i) => {
              const assets = NW_ASSET_GROUPS.reduce((s,g) => s+(totals[g]?.[p]??0),0)
              const liab   = NW_LIAB_GROUPS.reduce((s,g)  => s+(totals[g]?.[p]??0),0)
              const nw = assets + liab
              return (
                <tr key={p} className="border-b border-slate-100 hover:bg-slate-50">
                  <td className="px-2 py-1.5 font-medium sticky left-0 bg-white">{fmtPeriodHeader([...allPeriods].reverse()[i], grouping)}</td>
                  {NW_ASSET_GROUPS.map(g => <td key={g} className="text-right px-2 py-1.5 tabular-nums">{fmtEur(totals[g]?.[p]??0)}</td>)}
                  {NW_LIAB_GROUPS.map(g => {
                    const v = totals[g]?.[p]??0
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

function NwDetailAnalysis({ rows, allPeriods, accountMeta, grouping }: { rows: Row[]; allPeriods: string[]; accountMeta: Record<string, string>; grouping: string }) {
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
            layout={{ height: 340, margin: { t: 10, b: 10, l: 10, r: 10 }, showlegend: false, paper_bgcolor: 'white' }}
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
  const [savedSelection, setSavedSelection] = usePersist<Record<string, boolean>>('nw_account_selection', {})
  const [draftSelection, setDraftSelection] = useState<Record<string, boolean> | null>(null)
  const [selOpen, setSelOpen] = useState(false)

  const { data: rawData = [], isLoading } = useQuery({
    queryKey: ['nw-by-account', startDate, endDate, grouping],
    queryFn: () => getNetWorthByAccount(startDate, endDate, grouping),
  })
  const allRows = rawData as Row[]

  const accountMeta = useMemo(() => {
    const m: Record<string, string> = {}
    for (const r of allRows) m[String(r.accounts_name)] = String(r.accounts_type)
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
      return isIncluded(n, savedSelection) && (showZeroBalance || !isZero(n))
    }), [allRows, savedSelection, showZeroBalance])

  const hiddenZeroCount = useMemo(() =>
    allAccountNames.filter(n => isIncluded(n, savedSelection) && isZero(n)).length,
    [allAccountNames, savedSelection, showZeroBalance])

  const openSel = () => { setDraftSelection({ ...savedSelection }); setSelOpen(true) }
  const saveSel = () => { setSavedSelection(draftSelection ?? {}); setSelOpen(false) }

  return (
    <div className="space-y-3">
      {/* Controls */}
      <div className="flex flex-wrap items-center gap-3 pb-3 border-b border-slate-100">
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-slate-500 whitespace-nowrap">Start Date</label>
          <input type="date" className="rounded border border-slate-300 px-2 py-1 text-xs" value={startDate} onChange={e => setStartDate(e.target.value)} />
        </div>
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-slate-500 whitespace-nowrap">End Date</label>
          <input type="date" className="rounded border border-slate-300 px-2 py-1 text-xs" value={endDate} onChange={e => setEndDate(e.target.value)} />
        </div>
        <div className="flex rounded border border-slate-300 overflow-hidden text-xs">
          {(['year','quarter','month'] as const).map(g => (
            <button key={g} onClick={() => setGrouping(g)}
              className={`px-3 py-1 font-medium capitalize ${grouping === g ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 hover:bg-slate-50'}`}>
              {g.charAt(0).toUpperCase() + g.slice(1)}
            </button>
          ))}
        </div>
        <ChkBox label="Show zero-balance accounts" checked={showZeroBalance} onChange={setShowZeroBalance} />
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
                      <td className="px-3 py-1.5">{name}</td>
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
        <div className="flex items-center gap-2 px-4 py-2 bg-amber-50 border border-amber-200 rounded-lg text-xs text-amber-800">
          <span>⚠️ {hiddenZeroCount} selected account(s) have zero balance and might be hidden (enable 'Show zero-balance accounts' or click </span>
          <button onClick={() => setShowZeroBalance(true)} className="text-blue-600 hover:underline whitespace-nowrap">🔄 Refresh Data</button>
          <span>)</span>
        </div>
      )}

      {/* Sub-tabs */}
      <SubTabs tabs={['Overview', 'Account Balances', 'Summary per Type', 'Detail Analysis']} active={String(tab)} onChange={v => setTab(v as typeof tab)} />

      {isLoading
        ? <div className="flex justify-center py-12"><Spinner /></div>
        : <>
            {tab === 'Overview'          && <NwOverview rows={filteredRows} allPeriods={allPeriods} grouping={grouping} />}
            {tab === 'Account Balances'  && <NwAccountBalances rows={filteredRows} allPeriods={allPeriods} accountMeta={Object.fromEntries(Object.entries(accountMeta).filter(([n]) => filteredRows.some(r => String(r.accounts_name) === n)))} grouping={grouping} />}
            {tab === 'Summary per Type'  && <NwSummaryByType rows={filteredRows} allPeriods={allPeriods} grouping={grouping} />}
            {tab === 'Detail Analysis'   && <NwDetailAnalysis rows={filteredRows} allPeriods={allPeriods} accountMeta={accountMeta} grouping={grouping} />}
          </>
      }
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════════
// 2. INVESTMENT POSITIONS
// ════════════════════════════════════════════════════════════════════════════
function InvPositionsGraph({ startDate }: { startDate: string }) {
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
  const colors = ['#3b82f6', '#10b981', '#f59e0b', '#8b5cf6', '#ef4444', '#06b6d4']
  const traces = accounts.map((a, i) => ({
    x: dates, y: dates.map(d => lookup[a]?.[d] ?? null),
    name: a, type: 'scatter' as const, mode: 'lines' as const,
    line: { color: colors[i % colors.length], width: 1.5 },
    connectgaps: true,
  }))
  const totalByDate = dates.map(d => accounts.reduce((s, a) => s + (lookup[a]?.[d] ?? 0), 0))
  traces.push({ x: dates, y: totalByDate, name: 'Total', type: 'scatter', mode: 'lines', line: { color: '#1e3a8a', width: 2.5, dash: 'dot' } as unknown as typeof traces[0]['line'], connectgaps: true })
  const latestTotal = totalByDate[totalByDate.length - 1] ?? 0
  return (
    <div className="space-y-4">
      <KpiCard label="Current Portfolio Value" value={fmtEur(latestTotal)} color="text-blue-700" />
      <Plot data={traces}
        layout={{ height: 380, margin: { t: 10, r: 10, b: 40, l: 70 }, yaxis: { tickformat: ',.0f', tickprefix: '€' }, legend: { orientation: 'h', y: -0.25 }, plot_bgcolor: 'white', paper_bgcolor: 'white', hovermode: 'x unified' }}
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
  const dates = [...new Set(rows.map(r => String(r.date)))].sort()
  const recent = dates.slice(-6)
  return <PivotTable data={rows.filter(r => recent.includes(String(r.date)))} groupBy="accounts_name" colKey="date" valKey="value_eur" />
}

function SectorTab() {
  const { data = [], isLoading } = useQuery({ queryKey: ['sector-allocation'], queryFn: getSectorAllocation })
  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  const rows = data as Row[]
  const bySector: Record<string, number> = {}
  for (const r of rows) bySector[String(r.sector)] = (bySector[String(r.sector)] ?? 0) + Number(r.value_eur ?? 0)
  const sectors = Object.entries(bySector).sort((a, b) => b[1] - a[1])
  return (
    <div className="space-y-4">
      <Plot
        data={[{ x: sectors.map(s => s[1]), y: sectors.map(s => s[0]), type: 'bar', orientation: 'h', marker: { color: '#3b82f6' }, text: sectors.map(s => fmtEur(s[1])), textposition: 'outside' }]}
        layout={{ height: Math.max(300, sectors.length * 28), margin: { t: 10, r: 100, b: 40, l: 200 }, xaxis: { tickformat: ',.0f', tickprefix: '€' }, plot_bgcolor: 'white', paper_bgcolor: 'white' }}
        config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }} />
      <WithCopy>
      <div className="overflow-x-auto text-xs">
        <table className="w-full border-collapse">
          <thead>
            <tr className="bg-slate-50">
              <th className="text-left px-2 py-1.5 border-b border-slate-200 font-semibold">Sector</th>
              <th className="text-left px-2 py-1.5 border-b border-slate-200 font-semibold">Industry</th>
              <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold">Value (€)</th>
              <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold">Weight %</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
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
  const { data = [], isLoading } = useQuery({ queryKey: ['fx-exposure'], queryFn: getFxExposure })
  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  const rows = data as Row[]
  return (
    <div className="space-y-4">
      <Plot
        data={[{ x: rows.map(r => Number(r.eur_exposure)), y: rows.map(r => String(r.currency)), type: 'bar', orientation: 'h', marker: { color: '#8b5cf6' }, text: rows.map(r => fmtEur(Number(r.eur_exposure))), textposition: 'outside' }]}
        layout={{ height: Math.max(240, rows.length * 40), margin: { t: 10, r: 100, b: 40, l: 60 }, xaxis: { tickformat: ',.0f', tickprefix: '€' }, plot_bgcolor: 'white', paper_bgcolor: 'white' }}
        config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }} />
      <WithCopy>
      <div className="overflow-x-auto text-xs">
        <table className="w-full border-collapse">
          <thead>
            <tr className="bg-slate-50">
              <th className="text-left px-2 py-1.5 border-b border-slate-200 font-semibold">Currency</th>
              <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold">Native Exposure</th>
              <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold">EUR Exposure</th>
              <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold">5% FX Move Impact</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="border-b border-slate-100 hover:bg-slate-50">
                <td className="px-2 py-1.5 font-mono font-medium">{String(r.currency)}</td>
                <td className="px-2 py-1.5 text-right tabular-nums">{Number(r.native_exposure).toLocaleString('el-GR', { maximumFractionDigits: 2 })}</td>
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
  const { data = [], isLoading } = useQuery({ queryKey: ['allocation'], queryFn: getAllocationReport })
  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  const d = data as Row[]
  return (
    <Plot
      data={[{ values: d.map(r => Number(r.value_eur)), labels: d.map(r => String(r.label)), type: 'pie', hole: 0.45, textinfo: 'label+percent' }]}
      layout={{ height: 420, margin: { t: 20, r: 20, b: 20, l: 20 }, showlegend: true, legend: { orientation: 'v' }, plot_bgcolor: 'white', paper_bgcolor: 'white' }}
      config={{ displayModeBar: false, responsive: true }}
      style={{ width: '100%' }}
    />
  )
}

function HoldingsSnapshotTab() {
  const { data = [], isLoading } = useQuery({ queryKey: ['portfolio-summary'], queryFn: getPortfolioSummary })
  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  const rows = data as Row[]
  const total = rows.reduce((s, r) => s + Number(r.value_eur ?? 0), 0)
  return (
    <div className="space-y-3">
      <KpiCard label="Total Portfolio Value" value={fmtEur(total)} color="text-blue-700" />
      <WithCopy>
      <div className="overflow-x-auto text-xs">
        <table className="w-full border-collapse">
          <thead><tr className="bg-slate-50">
            <th className="text-left px-2 py-1.5 border-b border-slate-200 font-semibold">Security</th>
            <th className="text-left px-2 py-1.5 border-b border-slate-200 font-semibold">Ticker</th>
            <th className="text-left px-2 py-1.5 border-b border-slate-200 font-semibold">Account</th>
            <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold">Quantity</th>
            <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold">Price</th>
            <th className="text-left px-2 py-1.5 border-b border-slate-200 font-semibold">Ccy</th>
            <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold">Value (€)</th>
            <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold">Weight %</th>
          </tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="border-b border-slate-100 hover:bg-slate-50">
                <td className="px-2 py-1.5 font-medium">{String(r.security)}</td>
                <td className="px-2 py-1.5 font-mono text-slate-500 text-xs">{String(r.ticker ?? '—')}</td>
                <td className="px-2 py-1.5 text-slate-500">{String(r.account)}</td>
                <td className="px-2 py-1.5 text-right tabular-nums">{Number(r.quantity).toLocaleString('el-GR', { maximumFractionDigits: 4 })}</td>
                <td className="px-2 py-1.5 text-right tabular-nums">{Number(r.last_price ?? 0).toLocaleString('el-GR', { minimumFractionDigits: 2, maximumFractionDigits: 4 })}</td>
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

function InvPositionsSection({ startDate }: { startDate: string }) {
  const [tab, setTab] = useState('Graph')
  return (
    <div>
      <SubTabs tabs={['Graph', 'Summary', 'Holdings', 'Allocation', 'Sector & Industry', 'FX Exposure']} active={tab} onChange={setTab} />
      {tab === 'Graph' && <InvPositionsGraph startDate={startDate} />}
      {tab === 'Summary' && <InvPositionsSummary startDate={startDate} />}
      {tab === 'Holdings' && <HoldingsSnapshotTab />}
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
function pctKey(w: PnlWindow) { return w === 'dtd' ? 'pnl_dtd_percent' : w === 'ytd' ? 'pnl_ytd_percent' : w === 'all' ? 'pnl_net_all_time_percent' : null }

function PnlCell({ val, pct }: { val: number; pct?: number | null }) {
  const color = val >= 0 ? 'text-green-700' : 'text-red-600'
  return (
    <td className={`px-3 py-2 text-right tabular-nums font-medium ${color}`}>
      {fmtEur(val)}{pct != null && <span className="ml-1 text-xs opacity-70">({fmtPct(pct)})</span>}
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
  const [win, setWin] = usePersist<PnlWindow>('pnl_win', 'ytd')
  const [showClosedAccounts, setShowClosedAccounts] = usePersist('pnl_showClosedAccounts', false)
  const [showFxSplit, setShowFxSplit] = usePersist('pnl_showFxSplit', false)
  const [showPct, setShowPct] = usePersist('pnl_showPct', true)
  const [showClosedPositions, setShowClosedPositions] = usePersist('pnl_showClosedPositions', false)
  const [selectedAccount, setSelectedAccount] = useState<string | null>(null)

  const { data = [], isLoading } = useQuery({ queryKey: ['pnl'], queryFn: () => getPnl('1900-01-01') })
  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>

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

  const isClosedAccount = (acRows: Row[]) => acRows.every(r => Number(r.current_value_eur ?? 0) === 0)
  const isClosedPosition = (r: Row) => Number(r.current_value_eur ?? 0) === 0

  const accounts = Array.from(accountMap.entries())
    .filter(([, acRows]) => showClosedAccounts || !isClosedAccount(acRows))
    .map(([name, acRows]) => ({
      name,
      closed: isClosedAccount(acRows),
      value: acRows.reduce((s, r) => s + Number(r.current_value_eur ?? 0), 0),
      pnl: acRows.reduce((s, r) => s + Number(r[pk] ?? 0), 0),
      unrealized: acRows.reduce((s, r) => s + Number(r.unrealized_pnl_eur ?? 0), 0),
      realized: acRows.reduce((s, r) => s + Number(r.realized_pnl_eur ?? 0), 0),
      market: mktKey ? acRows.reduce((s, r) => s + Number(r[mktKey] ?? 0), 0) : null,
      fx: fxKey ? acRows.reduce((s, r) => s + Number(r[fxKey] ?? 0), 0) : null,
    }))

  const totalValue = accounts.reduce((s, a) => s + a.value, 0)
  const totalPnl   = accounts.reduce((s, a) => s + a.pnl, 0)
  const totalUnreal = accounts.reduce((s, a) => s + a.unrealized, 0)
  const totalReal  = accounts.reduce((s, a) => s + a.realized, 0)

  const drillRows = selectedAccount
    ? (accountMap.get(selectedAccount) ?? []).filter(r => showClosedPositions || !isClosedPosition(r))
    : null

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
        <KpiCard label="Portfolio Value" value={fmtEur(totalValue)} color="text-blue-700" />
        <KpiCard label={`P&L (${win.toUpperCase()})`} value={fmtEur(totalPnl)} color={totalPnl >= 0 ? 'text-green-700' : 'text-red-600'} />
        <KpiCard label="Unrealized P&L" value={fmtEur(totalUnreal)} color={totalUnreal >= 0 ? 'text-green-700' : 'text-red-600'} />
        <KpiCard label="Realized P&L" value={fmtEur(totalReal)} color={totalReal >= 0 ? 'text-green-700' : 'text-red-600'} />
      </div>
      <div className="flex gap-1">
        {PNL_WINDOWS.map(w => (
          <button key={w.k} onClick={() => setWin(w.k)}
            className={`px-2.5 py-1 rounded text-xs font-medium ${win === w.k ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}>{w.label}</button>
        ))}
      </div>
      {checkboxBar}
      {drillRows ? (
        <div className="space-y-3">
          <button onClick={() => setSelectedAccount(null)} className="text-blue-600 hover:underline text-sm">← All Accounts</button>
          <WithCopy>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead><tr className="bg-slate-50 text-xs text-slate-500 uppercase tracking-wide">
                  <th className="px-3 py-2 text-left">Security</th>
                  <th className="px-3 py-2 text-right">Qty</th>
                  <th className="px-3 py-2 text-right">Price</th>
                  <th className="px-3 py-2 text-right">Value (€)</th>
                  <th className="px-3 py-2 text-right">P&L ({win.toUpperCase()})</th>
                  {showFxSplit && mktKey && <><th className="px-3 py-2 text-right">Market</th><th className="px-3 py-2 text-right">FX</th></>}
                  <th className="px-3 py-2 text-right">Unrealized</th>
                  <th className="px-3 py-2 text-right">Realized</th>
                  <th className="px-3 py-2 text-right">YOC %</th>
                </tr></thead>
                <tbody className="divide-y divide-slate-100">
                  {drillRows.map((r, i) => (
                    <tr key={i} className={`hover:bg-slate-50 ${isClosedPosition(r) ? 'opacity-60' : ''}`}>
                      <td className="px-3 py-2 font-medium">{String(r.securities_name)}{isClosedPosition(r) && <span className="ml-1.5 text-xs text-slate-400 font-normal">(closed)</span>}</td>
                      <td className="px-3 py-2 text-right tabular-nums text-slate-600">{r.qty_today != null ? Number(r.qty_today).toLocaleString('el-GR', { maximumFractionDigits: 4 }) : '—'}</td>
                      <td className="px-3 py-2 text-right tabular-nums text-slate-600">{r.price_today != null ? `${Number(r.price_today).toLocaleString('el-GR', { minimumFractionDigits: 2, maximumFractionDigits: 4 })} ${r.currency ?? ''}` : '—'}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{fmtEur(Number(r.current_value_eur ?? 0))}</td>
                      <PnlCell val={Number(r[pk] ?? 0)} pct={showPct && pctKey(win) ? Number(r[pctKey(win)!] ?? 0) : null} />
                      {showFxSplit && mktKey && <><PnlCell val={Number(r[mktKey] ?? 0)} /><PnlCell val={fxKey ? Number(r[fxKey] ?? 0) : 0} /></>}
                      <PnlCell val={Number(r.unrealized_pnl_eur ?? 0)} />
                      <PnlCell val={Number(r.realized_pnl_eur ?? 0)} />
                      <td className="px-3 py-2 text-right tabular-nums text-slate-500">{r.dividend_yoc_pct != null ? `${Number(r.dividend_yoc_pct).toFixed(2)}%` : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </WithCopy>
        </div>
      ) : (
        <WithCopy>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr className="bg-slate-50 text-xs text-slate-500 uppercase tracking-wide">
                <th className="px-3 py-2 text-left">Account</th>
                <th className="px-3 py-2 text-right">Value (€)</th>
                <th className="px-3 py-2 text-right">P&L ({win.toUpperCase()})</th>
                {showPct && <th className="px-3 py-2 text-right">P&L %</th>}
                {showFxSplit && mktKey && <><th className="px-3 py-2 text-right">Market</th><th className="px-3 py-2 text-right">FX</th></>}
                <th className="px-3 py-2 text-right">Unrealized</th>
                <th className="px-3 py-2 text-right">Realized</th>
              </tr></thead>
              <tbody className="divide-y divide-slate-100">
                {accounts.map(a => (
                  <tr key={a.name} className={`hover:bg-blue-50 cursor-pointer ${a.closed ? 'opacity-60' : ''}`} onClick={() => setSelectedAccount(a.name)}>
                    <td className="px-3 py-2 font-medium text-blue-700 hover:underline">{a.name}{a.closed && <span className="ml-1.5 text-xs text-slate-400 font-normal">(closed)</span>}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{fmtEur(a.value)}</td>
                    <PnlCell val={a.pnl} />
                    {showPct && <td className={`px-3 py-2 text-right tabular-nums text-xs ${a.value !== 0 ? (a.pnl >= 0 ? 'text-green-700' : 'text-red-600') : 'text-slate-400'}`}>{a.value !== 0 ? `${(a.pnl / a.value * 100).toFixed(2)}%` : '—'}</td>}
                    {showFxSplit && mktKey && <><PnlCell val={a.market ?? 0} /><PnlCell val={a.fx ?? 0} /></>}
                    <PnlCell val={a.unrealized} />
                    <PnlCell val={a.realized} />
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

function TwrTab({ startDate, endDate }: { startDate: string; endDate: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ['twr', startDate, endDate],
    queryFn: () => getTwr(startDate, endDate),
  })
  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  if (!data) return null
  const d = data as { summary: { twr_total_pct: number; twr_ann_pct: number; months: number }; chart: Row[] }
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-3 gap-3">
        <KpiCard label="TWR Total" value={`${d.summary.twr_total_pct.toFixed(2)}%`} color={d.summary.twr_total_pct >= 0 ? 'text-green-700' : 'text-red-600'} />
        <KpiCard label="TWR Annualised" value={`${d.summary.twr_ann_pct.toFixed(2)}%`} color={d.summary.twr_ann_pct >= 0 ? 'text-green-700' : 'text-red-600'} />
        <KpiCard label="Months" value={String(d.summary.months)} />
      </div>
      <Plot
        data={[{
          x: d.chart.map(r => String(r.date)), y: d.chart.map(r => Number(r.twr_cumulative_pct)),
          name: 'Cumulative TWR %', type: 'scatter', mode: 'lines',
          line: { color: '#3b82f6', width: 2 }, fill: 'tozeroy', fillcolor: 'rgba(59,130,246,0.08)',
        }]}
        layout={{ height: 360, margin: { t: 10, r: 10, b: 40, l: 60 }, yaxis: { ticksuffix: '%' }, plot_bgcolor: 'white', paper_bgcolor: 'white', hovermode: 'x unified' }}
        config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }} />
    </div>
  )
}

function RiskMetricsTab({ startDate, endDate }: { startDate: string; endDate: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ['risk-metrics', startDate, endDate],
    queryFn: () => getRiskMetrics(startDate, endDate),
  })
  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  if (!data) return null
  const d = data as { ann_vol_pct: number; ann_return_pct: number; sharpe: number; sortino: number; max_drawdown_pct: number; var_95_pct: number; cvar_95_pct: number; months: number; drawdown_chart: Row[] }
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <KpiCard label="Annual Return" value={`${d.ann_return_pct.toFixed(2)}%`} color={d.ann_return_pct >= 0 ? 'text-green-700' : 'text-red-600'} />
        <KpiCard label="Annual Volatility" value={`${d.ann_vol_pct.toFixed(2)}%`} />
        <KpiCard label="Sharpe Ratio" value={d.sharpe.toFixed(3)} color={d.sharpe >= 1 ? 'text-green-700' : d.sharpe < 0 ? 'text-red-600' : ''} />
        <KpiCard label="Sortino Ratio" value={d.sortino.toFixed(3)} color={d.sortino >= 1 ? 'text-green-700' : d.sortino < 0 ? 'text-red-600' : ''} />
        <KpiCard label="Max Drawdown" value={`${d.max_drawdown_pct.toFixed(2)}%`} color="text-red-600" />
        <KpiCard label="VaR 95%" value={`${d.var_95_pct.toFixed(2)}%`} color="text-amber-600" />
        <KpiCard label="CVaR 95%" value={`${d.cvar_95_pct.toFixed(2)}%`} color="text-amber-600" />
        <KpiCard label="Months" value={String(d.months)} />
      </div>
      {d.drawdown_chart.length > 0 && (
        <Plot
          data={[{
            x: d.drawdown_chart.map(r => String(r.date)), y: d.drawdown_chart.map(r => Number(r.drawdown_pct)),
            name: 'Drawdown %', type: 'scatter', mode: 'lines',
            fill: 'tozeroy', fillcolor: 'rgba(239,68,68,0.15)', line: { color: '#ef4444', width: 1.5 },
          }]}
          layout={{ height: 280, margin: { t: 10, r: 10, b: 40, l: 60 }, yaxis: { ticksuffix: '%' }, plot_bgcolor: 'white', paper_bgcolor: 'white', hovermode: 'x unified' }}
          config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }} />
      )}
    </div>
  )
}

function DividendsReport({ startDate, endDate }: { startDate: string; endDate: string }) {
  const { data = [], isLoading } = useQuery({
    queryKey: ['dividends', startDate, endDate],
    queryFn: () => getDividends(startDate, endDate),
  })
  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  const d = data as Row[]
  const total = d.reduce((s, r) => s + Number(r.amount_eur ?? 0), 0)
  const monthly: Record<string, number> = {}
  for (const r of d) {
    const m = String(r.date ?? '').slice(0, 7)
    monthly[m] = (monthly[m] ?? 0) + Number(r.amount_eur ?? 0)
  }
  const months = Object.keys(monthly).sort()
  return (
    <div className="space-y-4">
      <KpiCard label="Total Dividends (EUR)" value={fmtEur(total)} color="text-green-700" />
      <Plot
        data={[{ x: months, y: months.map(m => monthly[m]), type: 'bar', marker: { color: '#10b981' } }]}
        layout={{ height: 280, margin: { t: 10, r: 10, b: 40, l: 70 }, yaxis: { tickformat: ',.2f', tickprefix: '€' }, plot_bgcolor: 'white', paper_bgcolor: 'white' }}
        config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }} />
      <WithCopy>
      <div className="overflow-x-auto text-xs">
        <table className="w-full border-collapse">
          <thead><tr className="bg-slate-50">
            <th className="px-2 py-1.5 text-left border-b border-slate-200 font-semibold">Date</th>
            <th className="px-2 py-1.5 text-left border-b border-slate-200 font-semibold">Security</th>
            <th className="px-2 py-1.5 text-left border-b border-slate-200 font-semibold">Account</th>
            <th className="px-2 py-1.5 text-right border-b border-slate-200 font-semibold">EUR</th>
          </tr></thead>
          <tbody>
            {d.map((r, i) => (
              <tr key={i} className="border-b border-slate-100 hover:bg-slate-50">
                <td className="px-2 py-1.5 text-slate-500">{String(r.date ?? '').slice(0, 10)}</td>
                <td className="px-2 py-1.5">{String(r.security)}</td>
                <td className="px-2 py-1.5 text-slate-500">{String(r.account)}</td>
                <td className="px-2 py-1.5 text-right tabular-nums text-green-700 font-medium">{fmtEur(Number(r.amount_eur))}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      </WithCopy>
    </div>
  )
}

function InvPerformanceSection({ startDate, endDate }: { startDate: string; endDate: string }) {
  const [tab, setTab] = useState('P&L')
  return (
    <div>
      <SubTabs tabs={['P&L', 'TWR/MWR', 'Dividends', 'Risk Metrics']} active={tab} onChange={setTab} />
      {tab === 'P&L' && <PnlReport />}
      {tab === 'TWR/MWR' && <TwrTab startDate={startDate} endDate={endDate} />}
      {tab === 'Dividends' && <DividendsReport startDate={startDate} endDate={endDate} />}
      {tab === 'Risk Metrics' && <RiskMetricsTab startDate={startDate} endDate={endDate} />}
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
    <div className="overflow-x-auto text-xs">
      <table className="w-full border-collapse">
        <thead>
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
              <td className="px-2 py-1.5 font-medium">{String(r.securities_name)}</td>
              <td className="px-2 py-1.5 font-mono text-slate-500">{String(r.ticker ?? '—')}</td>
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

function SecuritiesSection() {
  return (
    <div>
      <SubTabs tabs={['Price Changes']} active="Price Changes" onChange={() => {}} />
      <PriceChangesTab />
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════════
// 5. INCOME & EXPENSE
// ════════════════════════════════════════════════════════════════════════════
function IncomeExpenseChartPivot({ startDate, endDate }: { startDate: string; endDate: string }) {
  const [grouping, setGrouping] = useState('month')
  const [view, setView] = useState<'chart' | 'income' | 'expense'>('chart')
  const { data: simple = [], isLoading } = useQuery({
    queryKey: ['income-expense', startDate, endDate],
    queryFn: () => getIncomeExpense(startDate, endDate),
  })
  const { data: detail = [], isLoading: detailLoading } = useQuery({
    queryKey: ['income-expense-detail', startDate, endDate, grouping],
    queryFn: () => getIncomeExpenseDetail(startDate, endDate, grouping),
    enabled: view !== 'chart',
  })
  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  const d = simple as Row[]
  const totIncome = d.reduce((s, r) => s + Number(r.income ?? 0), 0)
  const totExpense = d.reduce((s, r) => s + Number(r.expense ?? 0), 0)
  const net = totIncome - totExpense
  const detailRows = detail as Row[]
  const periods = [...new Set(detailRows.map(r => String(r.period)))].sort()
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4 flex-wrap">
        {view !== 'chart' && <GroupingPicker value={grouping} onChange={setGrouping} />}
        <div className="flex gap-1 ml-auto">
          {([['chart', 'Chart'], ['income', 'Income Detail'], ['expense', 'Expense Detail']] as const).map(([v, lbl]) => (
            <button key={v} onClick={() => setView(v)}
              className={`px-2.5 py-1 rounded text-xs font-medium ${view === v ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}>{lbl}</button>
          ))}
        </div>
      </div>
      <div className="grid grid-cols-3 gap-3">
        <div className="bg-green-50 rounded-lg p-3"><p className="text-xs text-slate-500">Total Income</p><p className="text-sm font-bold text-green-700 tabular-nums">{fmtEur(totIncome)}</p></div>
        <div className="bg-red-50 rounded-lg p-3"><p className="text-xs text-slate-500">Total Expense</p><p className="text-sm font-bold text-red-600 tabular-nums">{fmtEur(totExpense)}</p></div>
        <div className={`rounded-lg p-3 ${net >= 0 ? 'bg-blue-50' : 'bg-orange-50'}`}><p className="text-xs text-slate-500">Net</p><p className={`text-sm font-bold tabular-nums ${net >= 0 ? 'text-blue-700' : 'text-orange-600'}`}>{fmtEur(net)}</p></div>
      </div>
      {view === 'chart' ? (
        <Plot
          data={[
            { x: d.map(r => String(r.month)), y: d.map(r => Number(r.income)), name: 'Income', type: 'bar', marker: { color: '#10b981' } },
            { x: d.map(r => String(r.month)), y: d.map(r => Number(r.expense)), name: 'Expense', type: 'bar', marker: { color: '#ef4444' } },
            { x: d.map(r => String(r.month)), y: d.map(r => Number(r.income) - Number(r.expense)), name: 'Net', type: 'scatter', mode: 'lines+markers', line: { color: '#3b82f6', width: 2 } },
          ]}
          layout={{ barmode: 'group', height: 380, margin: { t: 10, r: 10, b: 40, l: 70 }, yaxis: { tickformat: ',.0f', tickprefix: '€' }, legend: { orientation: 'h', y: -0.2 }, plot_bgcolor: 'white', paper_bgcolor: 'white', hovermode: 'x unified' }}
          config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }} />
      ) : detailLoading ? (
        <div className="flex justify-center py-8"><Spinner /></div>
      ) : (
        <HierarchicalPivotTable data={detailRows} catTypeFilter={view === 'income' ? 'Income' : 'Expense'} periods={periods} />
      )}
    </div>
  )
}

function CategoriesReport({ startDate, endDate }: { startDate: string; endDate: string }) {
  const [catType, setCatType] = useState<'Expense' | 'Income'>('Expense')
  const { data = [], isLoading } = useQuery({
    queryKey: ['top-categories', startDate, endDate, catType],
    queryFn: () => getTopCategories(startDate, endDate, catType, 20),
  })
  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  const sorted = [...(data as Row[])].sort((a, b) => Number(a.total) - Number(b.total))
  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        {(['Expense', 'Income'] as const).map(t => (
          <button key={t} onClick={() => setCatType(t)}
            className={`px-3 py-1 rounded text-sm font-medium ${catType === t ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}>{t}</button>
        ))}
      </div>
      <Plot
        data={[{ x: sorted.map(r => Number(r.total)), y: sorted.map(r => String(r.category)), type: 'bar', orientation: 'h', marker: { color: catType === 'Expense' ? '#ef4444' : '#10b981' }, text: sorted.map(r => fmtEur(Number(r.total))), textposition: 'outside' }]}
        layout={{ height: Math.max(350, sorted.length * 22), margin: { t: 10, r: 120, b: 40, l: 220 }, xaxis: { tickformat: ',.0f', tickprefix: '€' }, plot_bgcolor: 'white', paper_bgcolor: 'white' }}
        config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }} />
    </div>
  )
}

function TopPayeesTab({ startDate, endDate }: { startDate: string; endDate: string }) {
  const { data = [], isLoading } = useQuery({
    queryKey: ['spending-by-payee', startDate, endDate],
    queryFn: () => getSpendingByPayee(startDate, endDate, 20),
  })
  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  const rows = data as Row[]
  const sorted = [...rows].sort((a, b) => Number(a.amount_eur) - Number(b.amount_eur))
  return (
    <div className="space-y-4">
      <Plot
        data={[{ x: sorted.map(r => Number(r.amount_eur)), y: sorted.map(r => String(r.payee)), type: 'bar', orientation: 'h', marker: { color: '#f59e0b' }, text: sorted.map(r => fmtEur(Number(r.amount_eur))), textposition: 'outside' }]}
        layout={{ height: Math.max(350, sorted.length * 22), margin: { t: 10, r: 120, b: 40, l: 200 }, xaxis: { tickformat: ',.0f', tickprefix: '€' }, plot_bgcolor: 'white', paper_bgcolor: 'white' }}
        config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }} />
      <WithCopy>
      <div className="overflow-x-auto text-xs">
        <table className="w-full border-collapse">
          <thead><tr className="bg-slate-50">
            <th className="text-left px-2 py-1.5 border-b border-slate-200 font-semibold">Payee</th>
            <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold"># Tx</th>
            <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold">Amount</th>
            <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold">First Seen</th>
            <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold">Last Seen</th>
          </tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="border-b border-slate-100 hover:bg-slate-50">
                <td className="px-2 py-1.5 font-medium">{String(r.payee)}</td>
                <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{String(r.tx_count)}</td>
                <td className="px-2 py-1.5 text-right tabular-nums font-medium">{fmtEur(Number(r.amount_eur))}</td>
                <td className="px-2 py-1.5 text-right text-slate-500">{String(r.first_seen ?? '').slice(0, 10)}</td>
                <td className="px-2 py-1.5 text-right text-slate-500">{String(r.last_seen ?? '').slice(0, 10)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      </WithCopy>
    </div>
  )
}

function IncomeExpenseSection({ startDate, endDate }: { startDate: string; endDate: string }) {
  const [tab, setTab] = useState('Chart & Pivot')
  return (
    <div>
      <SubTabs tabs={['Chart & Pivot', 'Top Categories', 'Top Payees']} active={tab} onChange={setTab} />
      {tab === 'Chart & Pivot' && <IncomeExpenseChartPivot startDate={startDate} endDate={endDate} />}
      {tab === 'Top Categories' && <CategoriesReport startDate={startDate} endDate={endDate} />}
      {tab === 'Top Payees' && <TopPayeesTab startDate={startDate} endDate={endDate} />}
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════════
// 6. CASH FLOW FORECAST
// ════════════════════════════════════════════════════════════════════════════
function CashFlowSection() {
  const [monthsAhead, setMonthsAhead] = useState(6)
  const { data = [], isLoading } = useQuery({
    queryKey: ['cash-flow-forecast', monthsAhead],
    queryFn: () => getCashFlowForecast(monthsAhead),
  })
  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  const d = data as Row[]
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <label className="text-sm text-slate-600">Months ahead</label>
        {[3, 6, 12].map(m => (
          <button key={m} onClick={() => setMonthsAhead(m)}
            className={`px-2.5 py-1 rounded text-xs font-medium ${monthsAhead === m ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-600'}`}>{m}M</button>
        ))}
      </div>
      <WithCopy>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead><tr className="bg-slate-50 text-xs text-slate-500 uppercase tracking-wide">
            <th className="px-3 py-2 text-left">Template</th>
            <th className="px-3 py-2 text-left">Account</th>
            <th className="px-3 py-2 text-left">Payee</th>
            <th className="px-3 py-2 text-left">Periodicity</th>
            <th className="px-3 py-2 text-left">Next Due</th>
            <th className="px-3 py-2 text-right">Amount</th>
          </tr></thead>
          <tbody className="divide-y divide-slate-100">
            {d.map((r, i) => (
              <tr key={i} className="hover:bg-slate-50">
                <td className="px-3 py-2 font-medium">{String(r.name)}</td>
                <td className="px-3 py-2 text-slate-600">{String(r.account ?? '—')}</td>
                <td className="px-3 py-2 text-slate-600">{String(r.payee ?? '—')}</td>
                <td className="px-3 py-2 text-slate-500">{String(r.periodicity ?? '—')}</td>
                <td className="px-3 py-2 text-slate-500">{String(r.next_due_date ?? '—').slice(0, 10)}</td>
                <td className={`px-3 py-2 text-right tabular-nums font-semibold ${Number(r.amount) < 0 ? 'text-red-600' : 'text-green-700'}`}>{fmtEur(Number(r.amount))}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      </WithCopy>
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════════
// 7. BUDGET & SPENDING
// ════════════════════════════════════════════════════════════════════════════
function BudgetReport() {
  const [year, setYear] = useState(new Date().getFullYear())
  const [month, setMonth] = useState<string>('')
  const { data = [], isLoading } = useQuery({
    queryKey: ['budget-vs-actual', year, month],
    queryFn: () => getBudgetVsActual(year, month ? Number(month) : undefined),
  })
  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  const d = data as Row[]
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <Input type="number" className="w-24" value={year} onChange={e => setYear(Number(e.target.value))} />
        <select className="rounded-md border border-slate-300 px-3 py-1.5 text-sm" value={month} onChange={e => setMonth(e.target.value)}>
          <option value="">All months</option>
          {[...Array(12)].map((_, i) => <option key={i+1} value={String(i+1)}>{new Date(2000, i).toLocaleString('default', { month: 'long' })}</option>)}
        </select>
      </div>
      <WithCopy>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead><tr className="bg-slate-50 text-xs text-slate-500 uppercase tracking-wide">
            <th className="px-3 py-2 text-left">Category</th>
            <th className="px-3 py-2 text-right">Budget</th>
            <th className="px-3 py-2 text-right">Actual</th>
            <th className="px-3 py-2 text-right">Variance</th>
            <th className="px-3 py-2 text-right">% Used</th>
          </tr></thead>
          <tbody className="divide-y divide-slate-100">
            {d.map((r, i) => {
              const budg = Number(r.budget ?? 0); const act = Number(r.actual ?? 0)
              const pct = budg > 0 ? act / budg * 100 : null
              return (
                <tr key={i} className="hover:bg-slate-50">
                  <td className="px-3 py-2 text-slate-700">{String(r.category)}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-slate-500">{fmtEur(budg)}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{fmtEur(act)}</td>
                  <td className={`px-3 py-2 text-right tabular-nums font-medium ${Number(r.variance) >= 0 ? 'text-green-600' : 'text-red-600'}`}>{fmtEur(Number(r.variance))}</td>
                  <td className="px-3 py-2 text-right">
                    {pct != null ? (
                      <div className="flex items-center gap-2 justify-end">
                        <div className="w-16 h-1.5 bg-slate-200 rounded-full overflow-hidden">
                          <div className={`h-full rounded-full ${pct > 100 ? 'bg-red-500' : pct > 80 ? 'bg-amber-500' : 'bg-green-500'}`} style={{ width: `${Math.min(pct, 100)}%` }} />
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
      </WithCopy>
    </div>
  )
}

function SpendingTrendsTab() {
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
        layout={{ height: 380, margin: { t: 10, r: 10, b: 40, l: 70 }, yaxis: { tickformat: ',.0f', tickprefix: '€' }, legend: { orientation: 'h', y: -0.3 }, plot_bgcolor: 'white', paper_bgcolor: 'white', hovermode: 'x unified' }}
        config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }} />
    </div>
  )
}

function SavingsRateTab() {
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
        layout={{ barmode: 'group', height: 380, margin: { t: 10, r: 60, b: 40, l: 70 }, yaxis: { tickformat: ',.0f', tickprefix: '€' }, yaxis2: { overlaying: 'y', side: 'right', ticksuffix: '%', showgrid: false }, legend: { orientation: 'h', y: -0.2 }, plot_bgcolor: 'white', paper_bgcolor: 'white', hovermode: 'x unified' }}
        config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }} />
      <WithCopy>
      <div className="overflow-x-auto text-xs">
        <table className="w-full border-collapse">
          <thead><tr className="bg-slate-50">
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
  const [tab, setTab] = useState('Budget vs Actual')
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
function CapitalGainsReport() {
  const [year, setYear] = useState(new Date().getFullYear())
  const { data = [], isLoading } = useQuery({ queryKey: ['capital-gains', year], queryFn: () => getCapitalGains(year) })
  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  const d = data as Row[]
  const totalGain = d.reduce((s, r) => s + Number(r.gain_loss_eur ?? r.gain_loss ?? 0), 0)
  const totalProceeds = d.reduce((s, r) => s + Number(r.proceeds_eur ?? 0), 0)
  const totalCost = d.reduce((s, r) => s + Number(r.cost_eur ?? 0), 0)
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 flex-wrap">
        <label className="text-sm font-medium text-slate-600">Tax Year</label>
        <Input type="number" className="w-24" value={year} onChange={e => setYear(Number(e.target.value))} />
        <div className={`rounded-lg px-3 py-2 ${totalGain >= 0 ? 'bg-green-50' : 'bg-red-50'}`}>
          <span className="text-xs text-slate-500">Total Gain / Loss: </span>
          <span className={`font-bold tabular-nums ${totalGain >= 0 ? 'text-green-700' : 'text-red-600'}`}>{fmtEur(totalGain)}</span>
        </div>
        {totalProceeds > 0 && (
          <>
            <div className="rounded-lg px-3 py-2 bg-slate-50">
              <span className="text-xs text-slate-500">Proceeds: </span>
              <span className="font-semibold tabular-nums">{fmtEur(totalProceeds)}</span>
            </div>
            <div className="rounded-lg px-3 py-2 bg-slate-50">
              <span className="text-xs text-slate-500">Cost: </span>
              <span className="font-semibold tabular-nums">{fmtEur(totalCost)}</span>
            </div>
          </>
        )}
      </div>
      {d.length === 0 ? (
        <p className="text-sm text-slate-500 py-4">No sell transactions found for {year}. Try a different year.</p>
      ) : (
        <WithCopy>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr className="bg-slate-50 text-xs text-slate-500 uppercase tracking-wide">
              <th className="px-3 py-2 text-left">Date</th><th className="px-3 py-2 text-left">Security</th>
              <th className="px-3 py-2 text-left">Ticker</th><th className="px-3 py-2 text-left">Account</th>
              <th className="px-3 py-2 text-left">Action</th>
              <th className="px-3 py-2 text-right">Qty</th><th className="px-3 py-2 text-right">Sell Price</th>
              <th className="px-3 py-2 text-right">WAC Cost</th>
              <th className="px-3 py-2 text-right">Proceeds (€)</th>
              <th className="px-3 py-2 text-right">Gain / Loss (€)</th>
            </tr></thead>
            <tbody className="divide-y divide-slate-100">
              {d.map((r, i) => {
                const gl = Number(r.gain_loss_eur ?? r.gain_loss ?? 0)
                return (
                  <tr key={i} className="hover:bg-slate-50">
                    <td className="px-3 py-2 text-slate-500">{String(r.date ?? '').slice(0, 10)}</td>
                    <td className="px-3 py-2 font-medium">{String(r.security)}</td>
                    <td className="px-3 py-2 font-mono text-xs text-slate-500">{String(r.ticker ?? '—')}</td>
                    <td className="px-3 py-2 text-slate-600">{String(r.account)}</td>
                    <td className="px-3 py-2 text-slate-500 text-xs">{String(r.action ?? '—')}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{Number(r.quantity).toLocaleString('el-GR', { maximumFractionDigits: 4 })}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{Number(r.sell_price ?? 0).toLocaleString('el-GR', { minimumFractionDigits: 2, maximumFractionDigits: 4 })}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{Number(r.avg_cost ?? 0).toLocaleString('el-GR', { minimumFractionDigits: 2, maximumFractionDigits: 4 })}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{fmtEur(Number(r.proceeds_eur ?? 0))}</td>
                    <td className={`px-3 py-2 text-right tabular-nums font-semibold ${gl >= 0 ? 'text-green-700' : 'text-red-600'}`}>{fmtEur(gl)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        </WithCopy>
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
        <div className="overflow-x-auto text-xs">
          <table className="w-full border-collapse">
            <thead><tr className="bg-slate-50">
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
                  <td className="px-2 py-1.5 font-medium">{String(r.securities_name)}</td>
                  <td className="px-2 py-1.5 font-mono text-slate-500">{String(r.ticker ?? '—')}</td>
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
    </div>
  )
}

function DividendIncomeTaxTab() {
  const [year, setYear] = useState(new Date().getFullYear())
  const { data = [], isLoading } = useQuery({
    queryKey: ['dividend-income-tax', year],
    queryFn: () => getDividendIncomeTax(year),
  })
  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  const rows = data as Row[]
  const total = rows.reduce((s, r) => s + Number(r.amount_eur ?? 0), 0)
  const taxable = rows.filter(r => !r.is_tax_exempt).reduce((s, r) => s + Number(r.amount_eur ?? 0), 0)
  const exempt = rows.filter(r => r.is_tax_exempt).reduce((s, r) => s + Number(r.amount_eur ?? 0), 0)
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <label className="text-sm font-medium text-slate-600">Tax Year</label>
        <Input type="number" className="w-24" value={year} onChange={e => setYear(Number(e.target.value))} />
      </div>
      <div className="grid grid-cols-3 gap-3">
        <KpiCard label="Total Income" value={fmtEur(total)} color="text-green-700" />
        <KpiCard label="Taxable" value={fmtEur(taxable)} color="text-amber-600" />
        <KpiCard label="Tax-Exempt" value={fmtEur(exempt)} color="text-blue-600" />
      </div>
      <WithCopy>
      <div className="overflow-x-auto text-xs">
        <table className="w-full border-collapse">
          <thead><tr className="bg-slate-50">
            <th className="text-left px-2 py-1.5 border-b border-slate-200 font-semibold">Date</th>
            <th className="text-left px-2 py-1.5 border-b border-slate-200 font-semibold">Security</th>
            <th className="text-left px-2 py-1.5 border-b border-slate-200 font-semibold">Account</th>
            <th className="text-left px-2 py-1.5 border-b border-slate-200 font-semibold">Action</th>
            <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold">Amount (€)</th>
            <th className="text-center px-2 py-1.5 border-b border-slate-200 font-semibold">Tax Exempt</th>
          </tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="border-b border-slate-100 hover:bg-slate-50">
                <td className="px-2 py-1.5 text-slate-500">{String(r.date ?? '').slice(0, 10)}</td>
                <td className="px-2 py-1.5 font-medium">{String(r.securities_name)}</td>
                <td className="px-2 py-1.5 text-slate-500">{String(r.account_name)}</td>
                <td className="px-2 py-1.5 text-slate-500">{String(r.action)}</td>
                <td className="px-2 py-1.5 text-right tabular-nums text-green-700 font-medium">{fmtEur(Number(r.amount_eur ?? 0))}</td>
                <td className="px-2 py-1.5 text-center">{r.is_tax_exempt ? <span className="text-blue-600">✓</span> : <span className="text-slate-400">—</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      </WithCopy>
    </div>
  )
}

function TaxSection() {
  const [tab, setTab] = useState('Capital Gains')
  return (
    <div>
      <SubTabs tabs={['Capital Gains', 'Tax-Loss Harvesting', 'Dividend Income']} active={tab} onChange={setTab} />
      {tab === 'Capital Gains' && <CapitalGainsReport />}
      {tab === 'Tax-Loss Harvesting' && <TaxLossHarvestingTab />}
      {tab === 'Dividend Income' && <DividendIncomeTaxTab />}
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
        layout={{ height: 320, margin: { t: 10, r: 10, b: 40, l: 80 }, yaxis: { tickformat: ',.0f', tickprefix: '€' }, legend: { orientation: 'h', y: -0.2 }, plot_bgcolor: 'white', paper_bgcolor: 'white' }}
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
      <div className="overflow-x-auto text-xs">
        <table className="w-full border-collapse">
          <thead><tr className="bg-slate-50">
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
  const [tab, setTab] = useState('Goals')
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
// MAIN
// ════════════════════════════════════════════════════════════════════════════
export default function Reports() {
  const [activeTab, setActiveTab] = useState('net-worth')
  const [startDate, setStartDate] = useState('2020-01-01')
  const [endDate, setEndDate] = useState(new Date().toISOString().slice(0, 10))
  const current = REPORT_TABS.find(t => t.key === activeTab)

  return (
    <div className="flex h-full">
      <nav className="w-48 shrink-0 border-r border-slate-200 bg-slate-50 flex flex-col py-4">
        <p className="px-4 text-xs font-semibold text-slate-400 uppercase tracking-wide mb-2">Reports</p>
        {REPORT_TABS.map(t => (
          <button key={t.key} onClick={() => setActiveTab(t.key)}
            className={`text-left px-4 py-2 text-sm transition-colors ${activeTab === t.key ? 'bg-blue-50 text-blue-700 font-semibold border-r-2 border-blue-600' : 'text-slate-600 hover:bg-slate-100'}`}>
            {t.label}
          </button>
        ))}
      </nav>

      <div className="flex-1 min-w-0 overflow-auto">
        <div className="flex items-center justify-between px-6 py-3 border-b border-slate-200 bg-white sticky top-0 z-10">
          <h2 className="text-base font-semibold text-slate-800">{current?.label}</h2>
          {activeTab !== 'net-worth' && (
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
              {activeTab === 'inv-performance' && <InvPerformanceSection startDate={startDate} endDate={endDate} />}
              {activeTab === 'securities' && <SecuritiesSection />}
              {activeTab === 'income-expense' && <IncomeExpenseSection startDate={startDate} endDate={endDate} />}
              {activeTab === 'cashflow' && <CashFlowSection />}
              {activeTab === 'budget' && <BudgetSection />}
              {activeTab === 'tax' && <TaxSection />}
              {activeTab === 'planning' && <PlanningSection />}
            </CardBody>
          </Card>
        </div>
      </div>
    </div>
  )
}
