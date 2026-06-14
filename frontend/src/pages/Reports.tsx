import React, { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import PlotlyReact from 'react-plotly.js'
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const Plot: React.ComponentType<any> = (PlotlyReact as any).default ?? PlotlyReact
import {
  getIncomeExpense, getSavingsRate, getTopCategories, getPortfolioSummary, getAllocationReport,
  getNetWorthReport, getIncomeExpenseDetail, getDividends, getCapitalGains,
  getBudgetVsActual, getCashFlowForecast, getPnl, getCategoryBreakdown,
} from '@/lib/api'
import { PageHeader, Card, CardHeader, CardTitle, CardBody, Input, Spinner, Button } from '@/components/ui'
import { fmtEur, fmtPct } from '@/lib/utils'

// ── Types ─────────────────────────────────────────────────────────────────────
type Row = Record<string, unknown>

// ── Sidebar tabs ─────────────────────────────────────────────────────────────
const REPORT_TABS = [
  { key: 'net-worth', label: 'Net Worth' },
  { key: 'income-expense', label: 'Income & Expense' },
  { key: 'pnl', label: 'P&L' },
  { key: 'savings', label: 'Savings Rate' },
  { key: 'categories', label: 'Top Categories' },
  { key: 'portfolio', label: 'Portfolio' },
  { key: 'allocation', label: 'Allocation' },
  { key: 'dividends', label: 'Dividends' },
  { key: 'capital-gains', label: 'Capital Gains' },
  { key: 'budget', label: 'Budget vs Actual' },
  { key: 'cashflow', label: 'Cash Flow Forecast' },
  { key: 'category-breakdown', label: 'Category Breakdown' },
]

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

// ── Net Worth Report ──────────────────────────────────────────────────────────
function NetWorthReport({ startDate, endDate }: { startDate: string; endDate: string }) {
  const [grouping, setGrouping] = useState('month')
  const { data = [], isLoading } = useQuery({
    queryKey: ['net-worth-report', startDate, endDate, grouping],
    queryFn: () => getNetWorthReport(startDate, endDate, grouping),
  })
  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  const d = data as Row[]
  const latest = d[d.length - 1]
  return (
    <div className="space-y-4">
      <GroupingPicker value={grouping} onChange={setGrouping} />
      {latest && (
        <div className="grid grid-cols-3 md:grid-cols-5 gap-3">
          {[
            { label: 'Net Worth', val: Number(latest.net_worth), color: 'text-blue-700' },
            { label: 'Cash', val: Number(latest.cash) },
            { label: 'Investments', val: Number(latest.investments) },
            { label: 'Pension', val: Number(latest.pension) },
            { label: 'Assets', val: Number(latest.assets) },
          ].map(k => (
            <div key={k.label} className="bg-slate-50 rounded-lg p-3">
              <p className="text-xs text-slate-500 mb-1">{k.label}</p>
              <p className={`text-sm font-bold tabular-nums ${k.color ?? ''}`}>{fmtEur(k.val)}</p>
            </div>
          ))}
        </div>
      )}
      <Plot
        data={[
          { x: d.map(r => String(r.period)), y: d.map(r => Number(r.cash)), name: 'Cash', stackgroup: 'one', fillcolor: '#3b82f6', line: { color: '#3b82f6' } },
          { x: d.map(r => String(r.period)), y: d.map(r => Number(r.investments)), name: 'Investments', stackgroup: 'one', fillcolor: '#10b981', line: { color: '#10b981' } },
          { x: d.map(r => String(r.period)), y: d.map(r => Number(r.pension)), name: 'Pension', stackgroup: 'one', fillcolor: '#f59e0b', line: { color: '#f59e0b' } },
          { x: d.map(r => String(r.period)), y: d.map(r => Number(r.assets)), name: 'Assets', stackgroup: 'one', fillcolor: '#8b5cf6', line: { color: '#8b5cf6' } },
          { x: d.map(r => String(r.period)), y: d.map(r => Number(r.net_worth)), name: 'Net Worth', type: 'scatter', mode: 'lines', line: { color: '#1e40af', width: 2.5, dash: 'dot' } },
        ]}
        layout={{ height: 380, margin: { t: 10, r: 10, b: 40, l: 70 }, yaxis: { tickformat: ',.0f', tickprefix: '€' }, legend: { orientation: 'h', y: -0.2 }, plot_bgcolor: 'white', paper_bgcolor: 'white', hovermode: 'x unified' }}
        config={{ displayModeBar: false, responsive: true }}
        style={{ width: '100%' }}
      />
    </div>
  )
}

// ── Income & Expense ──────────────────────────────────────────────────────────
function IncomeExpenseReport({ startDate, endDate }: { startDate: string; endDate: string }) {
  const [grouping, setGrouping] = useState('month')
  const [view, setView] = useState<'chart' | 'pivot'>('chart')
  const { data: simple = [], isLoading } = useQuery({
    queryKey: ['income-expense', startDate, endDate],
    queryFn: () => getIncomeExpense(startDate, endDate),
  })
  const { data: detail = [] } = useQuery({
    queryKey: ['income-expense-detail', startDate, endDate, grouping],
    queryFn: () => getIncomeExpenseDetail(startDate, endDate, grouping),
    enabled: view === 'pivot',
  })

  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  const d = simple as Row[]
  const totIncome = d.reduce((s, r) => s + Number(r.income ?? 0), 0)
  const totExpense = d.reduce((s, r) => s + Number(r.expense ?? 0), 0)
  const net = totIncome - totExpense

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4">
        <GroupingPicker value={grouping} onChange={setGrouping} />
        <div className="flex gap-1 ml-auto">
          {(['chart', 'pivot'] as const).map(v => (
            <button key={v} onClick={() => setView(v)}
              className={`px-2.5 py-1 rounded text-xs font-medium capitalize ${view === v ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-600'}`}>
              {v}
            </button>
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
            { x: d.map(r => String(r.month)), y: d.map(r => Number(r.income) - Number(r.expense)), name: 'Net', type: 'scatter', mode: 'lines+markers', line: { color: '#3b82f6', width: 2 }, marker: { size: 5 } },
          ]}
          layout={{ barmode: 'group', height: 380, margin: { t: 10, r: 10, b: 40, l: 70 }, yaxis: { tickformat: ',.0f', tickprefix: '€' }, legend: { orientation: 'h', y: -0.2 }, plot_bgcolor: 'white', paper_bgcolor: 'white', hovermode: 'x unified' }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: '100%' }}
        />
      ) : (
        <PivotTable data={detail as Row[]} groupBy="category" colKey="period" valKey="total" />
      )}
    </div>
  )
}

// ── Pivot table ───────────────────────────────────────────────────────────────
function PivotTable({ data, groupBy, colKey, valKey }: { data: Row[]; groupBy: string; colKey: string; valKey: string }) {
  const periods = [...new Set(data.map(r => String(r[colKey])))].sort()
  const categories = [...new Set(data.map(r => String(r[groupBy])))]
  const lookup: Record<string, Record<string, number>> = {}
  for (const r of data) {
    const g = String(r[groupBy])
    const c = String(r[colKey])
    if (!lookup[g]) lookup[g] = {}
    lookup[g][c] = (lookup[g][c] ?? 0) + Number(r[valKey] ?? 0)
  }
  return (
    <div className="overflow-x-auto text-xs">
      <table className="w-full border-collapse">
        <thead>
          <tr className="bg-slate-50">
            <th className="text-left px-2 py-1.5 border-b border-slate-200 font-semibold text-slate-600 sticky left-0 bg-slate-50 min-w-40">Category</th>
            {periods.map(p => <th key={p} className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold text-slate-600 whitespace-nowrap">{p.slice(0, 7)}</th>)}
            <th className="text-right px-2 py-1.5 border-b border-slate-200 font-semibold text-slate-600">Total</th>
          </tr>
        </thead>
        <tbody>
          {categories.map(cat => {
            const rowTotal = periods.reduce((s, p) => s + (lookup[cat]?.[p] ?? 0), 0)
            return (
              <tr key={cat} className="border-b border-slate-100 hover:bg-slate-50">
                <td className="px-2 py-1.5 text-slate-700 sticky left-0 bg-white">{cat}</td>
                {periods.map(p => <td key={p} className="text-right px-2 py-1.5 tabular-nums text-slate-600">{lookup[cat]?.[p] ? fmtEur(lookup[cat][p]) : '—'}</td>)}
                <td className="text-right px-2 py-1.5 tabular-nums font-semibold text-slate-800">{fmtEur(rowTotal)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ── Savings Rate ──────────────────────────────────────────────────────────────
function SavingsReport() {
  const { data = [], isLoading } = useQuery({ queryKey: ['savings-rate'], queryFn: () => getSavingsRate(24) })
  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  const d = data as Row[]
  return (
    <Plot
      data={[{
        x: d.map(r => String(r.month)), y: d.map(r => Number(r.savings_rate)),
        name: 'Savings Rate %', type: 'scatter', mode: 'lines+markers',
        line: { color: '#3b82f6', width: 2 }, marker: { size: 5 },
        fill: 'tozeroy', fillcolor: 'rgba(59,130,246,0.1)',
      }]}
      layout={{ height: 380, margin: { t: 10, r: 10, b: 40, l: 60 }, yaxis: { ticksuffix: '%', rangemode: 'tozero' }, plot_bgcolor: 'white', paper_bgcolor: 'white', hovermode: 'x unified' }}
      config={{ displayModeBar: false, responsive: true }}
      style={{ width: '100%' }}
    />
  )
}

// ── Top Categories ────────────────────────────────────────────────────────────
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
            className={`px-3 py-1 rounded text-sm font-medium ${catType === t ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}>
            {t}
          </button>
        ))}
      </div>
      <Plot
        data={[{ x: sorted.map(r => Number(r.total)), y: sorted.map(r => String(r.category)), type: 'bar', orientation: 'h', marker: { color: catType === 'Expense' ? '#ef4444' : '#10b981' }, text: sorted.map(r => fmtEur(Number(r.total))), textposition: 'outside' }]}
        layout={{ height: Math.max(350, sorted.length * 22), margin: { t: 10, r: 120, b: 40, l: 220 }, xaxis: { tickformat: ',.0f', tickprefix: '€' }, plot_bgcolor: 'white', paper_bgcolor: 'white' }}
        config={{ displayModeBar: false, responsive: true }}
        style={{ width: '100%' }}
      />
    </div>
  )
}

// ── P&L ──────────────────────────────────────────────────────────────────────
type PnlWindow = 'dtd' | 'wtd' | 'mtd' | 'qtd' | 'ytd' | 'all'

const PNL_WINDOWS = [
  { k: 'dtd' as PnlWindow, label: 'D' },
  { k: 'wtd' as PnlWindow, label: 'W' },
  { k: 'mtd' as PnlWindow, label: 'M' },
  { k: 'qtd' as PnlWindow, label: 'Q' },
  { k: 'ytd' as PnlWindow, label: 'YTD' },
  { k: 'all' as PnlWindow, label: 'All' },
]

function pnlKey(w: PnlWindow) { return w === 'all' ? 'pnl_net_all_time_eur' : `pnl_${w}_eur` }
function pctKey(w: PnlWindow) { return w === 'dtd' ? 'pnl_dtd_percent' : w === 'ytd' ? 'pnl_ytd_percent' : w === 'all' ? 'pnl_net_all_time_percent' : null }

function PnlCell({ val, pct }: { val: number; pct?: number | null }) {
  const color = val >= 0 ? 'text-green-700' : 'text-red-600'
  return (
    <td className={`px-3 py-2 text-right tabular-nums font-medium ${color}`}>
      {fmtEur(val)}
      {pct != null && <span className="ml-1 text-xs opacity-70">({fmtPct(pct)})</span>}
    </td>
  )
}

function PnlSecurityTable({ rows, win }: { rows: Row[]; win: PnlWindow }) {
  const pk = pnlKey(win)
  const pck = pctKey(win)
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="bg-slate-50 text-xs text-slate-500 uppercase tracking-wide">
            <th className="px-3 py-2 text-left">Security</th>
            <th className="px-3 py-2 text-right">Value (€)</th>
            <th className="px-3 py-2 text-right">P&L ({win.toUpperCase()})</th>
            <th className="px-3 py-2 text-right">Unrealized €</th>
            <th className="px-3 py-2 text-right">Realized €</th>
            <th className="px-3 py-2 text-right">YOC %</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((r, i) => (
            <tr key={i} className="hover:bg-slate-50">
              <td className="px-3 py-2 font-medium">{String(r.securities_name)}</td>
              <td className="px-3 py-2 text-right tabular-nums">{fmtEur(Number(r.current_value_eur ?? 0))}</td>
              <PnlCell val={Number(r[pk] ?? 0)} pct={pck ? Number(r[pck] ?? 0) : null} />
              <PnlCell val={Number(r.unrealized_pnl_eur ?? 0)} />
              <PnlCell val={Number(r.realized_pnl_eur ?? 0)} />
              <td className="px-3 py-2 text-right tabular-nums text-slate-500">
                {r.dividend_yoc_pct != null ? `${Number(r.dividend_yoc_pct).toFixed(2)}%` : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function PnlReport(_props: { startDate: string; endDate: string }) {
  const [win, setWin] = useState<PnlWindow>('ytd')
  const [selectedAccount, setSelectedAccount] = useState<string | null>(null)

  const { data = [], isLoading } = useQuery({
    queryKey: ['pnl'],
    queryFn: () => getPnl('1900-01-01'),
  })

  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>

  const rows = data as Row[]
  const pk = pnlKey(win)

  // Group rows by account
  const accountMap = new Map<string, Row[]>()
  for (const r of rows) {
    const acc = String(r.accounts_name)
    if (!accountMap.has(acc)) accountMap.set(acc, [])
    accountMap.get(acc)!.push(r)
  }

  // Account-level aggregates
  const accounts = Array.from(accountMap.entries()).map(([name, acRows]) => ({
    name,
    value:     acRows.reduce((s, r) => s + Number(r.current_value_eur ?? 0), 0),
    pnl:       acRows.reduce((s, r) => s + Number(r[pk] ?? 0), 0),
    unrealized: acRows.reduce((s, r) => s + Number(r.unrealized_pnl_eur ?? 0), 0),
    realized:   acRows.reduce((s, r) => s + Number(r.realized_pnl_eur ?? 0), 0),
  }))

  const totalValue   = accounts.reduce((s, a) => s + a.value, 0)
  const totalPnl     = accounts.reduce((s, a) => s + a.pnl, 0)
  const totalUnreal  = accounts.reduce((s, a) => s + a.unrealized, 0)
  const totalReal    = accounts.reduce((s, a) => s + a.realized, 0)

  const drillRows = selectedAccount ? (accountMap.get(selectedAccount) ?? []) : null

  return (
    <div className="space-y-4">
      {/* KPI row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[
          { label: 'Portfolio Value', val: totalValue, color: 'text-blue-700' },
          { label: `P&L (${win.toUpperCase()})`, val: totalPnl, color: totalPnl >= 0 ? 'text-green-700' : 'text-red-600' },
          { label: 'Unrealized P&L', val: totalUnreal, color: totalUnreal >= 0 ? 'text-green-700' : 'text-red-600' },
          { label: 'Realized P&L', val: totalReal, color: totalReal >= 0 ? 'text-green-700' : 'text-red-600' },
        ].map(k => (
          <div key={k.label} className="bg-slate-50 rounded-lg p-3">
            <p className="text-xs text-slate-500 mb-1">{k.label}</p>
            <p className={`text-sm font-bold tabular-nums ${k.color}`}>{fmtEur(k.val)}</p>
          </div>
        ))}
      </div>

      {/* Window picker */}
      <div className="flex gap-1">
        {PNL_WINDOWS.map(w => (
          <button key={w.k} onClick={() => setWin(w.k)}
            className={`px-2.5 py-1 rounded text-xs font-medium ${win === w.k ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}>
            {w.label}
          </button>
        ))}
      </div>

      {drillRows ? (
        /* ── Security drill-down ── */
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <button onClick={() => setSelectedAccount(null)}
              className="text-blue-600 hover:underline text-sm flex items-center gap-1">
              ← All Accounts
            </button>
            <span className="text-slate-400 text-sm">/</span>
            <span className="text-sm font-medium text-slate-700">{selectedAccount}</span>
          </div>
          <PnlSecurityTable rows={drillRows} win={win} />
        </div>
      ) : (
        /* ── Account-level table ── */
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-slate-50 text-xs text-slate-500 uppercase tracking-wide">
                <th className="px-3 py-2 text-left">Account</th>
                <th className="px-3 py-2 text-right">Value (€)</th>
                <th className="px-3 py-2 text-right">P&L ({win.toUpperCase()})</th>
                <th className="px-3 py-2 text-right">Unrealized €</th>
                <th className="px-3 py-2 text-right">Realized €</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {accounts.map(a => (
                <tr key={a.name}
                  className="hover:bg-blue-50 cursor-pointer"
                  onClick={() => setSelectedAccount(a.name)}>
                  <td className="px-3 py-2 font-medium text-blue-700 hover:underline">{a.name}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{fmtEur(a.value)}</td>
                  <PnlCell val={a.pnl} />
                  <PnlCell val={a.unrealized} />
                  <PnlCell val={a.realized} />
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ── Portfolio ─────────────────────────────────────────────────────────────────
function PortfolioReport() {
  const { data = [], isLoading } = useQuery({ queryKey: ['portfolio-summary'], queryFn: getPortfolioSummary })
  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  const d = data as Row[]
  const total = d.reduce((s, r) => s + Number(r.value_eur ?? 0), 0)
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="bg-slate-50 text-slate-500 text-xs uppercase tracking-wide">
            <th className="px-3 py-2 text-left">Account</th>
            <th className="px-3 py-2 text-left">Security</th>
            <th className="px-3 py-2 text-left">Ticker</th>
            <th className="px-3 py-2 text-right">Qty</th>
            <th className="px-3 py-2 text-right">Price</th>
            <th className="px-3 py-2 text-right">FX</th>
            <th className="px-3 py-2 text-right">Value (EUR)</th>
            <th className="px-3 py-2 text-right">Weight</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {d.map((r, i) => (
            <tr key={i} className="hover:bg-slate-50">
              <td className="px-3 py-2 text-slate-600">{String(r.account)}</td>
              <td className="px-3 py-2 font-medium">{String(r.security)}</td>
              <td className="px-3 py-2 text-slate-500 font-mono text-xs">{String(r.ticker ?? '—')}</td>
              <td className="px-3 py-2 text-right tabular-nums">{Number(r.quantity).toLocaleString('el-GR', { maximumFractionDigits: 4 })}</td>
              <td className="px-3 py-2 text-right tabular-nums">{fmtEur(Number(r.last_price))}</td>
              <td className="px-3 py-2 text-right tabular-nums">{Number(r.fx_rate).toFixed(4)}</td>
              <td className="px-3 py-2 text-right tabular-nums font-semibold">{fmtEur(Number(r.value_eur))}</td>
              <td className="px-3 py-2 text-right tabular-nums text-slate-500">{total > 0 ? fmtPct(Number(r.value_eur) / total * 100) : '—'}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr className="bg-slate-50 font-semibold">
            <td colSpan={6} className="px-3 py-2">Total</td>
            <td className="px-3 py-2 text-right tabular-nums">{fmtEur(total)}</td>
            <td className="px-3 py-2 text-right">100%</td>
          </tr>
        </tfoot>
      </table>
    </div>
  )
}

// ── Allocation ────────────────────────────────────────────────────────────────
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

// ── Dividends ─────────────────────────────────────────────────────────────────
function DividendsReport({ startDate, endDate }: { startDate: string; endDate: string }) {
  const { data = [], isLoading } = useQuery({
    queryKey: ['dividends', startDate, endDate],
    queryFn: () => getDividends(startDate, endDate),
  })
  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  const d = data as Row[]
  const total = d.reduce((s, r) => s + Number(r.amount_eur ?? 0), 0)

  // Monthly bar chart
  const monthly: Record<string, number> = {}
  for (const r of d) {
    const m = String(r.date ?? '').slice(0, 7)
    monthly[m] = (monthly[m] ?? 0) + Number(r.amount_eur ?? 0)
  }
  const months = Object.keys(monthly).sort()

  return (
    <div className="space-y-4">
      <div className="bg-green-50 rounded-lg p-3 inline-block">
        <p className="text-xs text-slate-500">Total Dividends (EUR)</p>
        <p className="text-xl font-bold text-green-700 tabular-nums">{fmtEur(total)}</p>
      </div>
      <Plot
        data={[{ x: months, y: months.map(m => monthly[m]), type: 'bar', marker: { color: '#10b981' }, name: 'Dividend Income' }]}
        layout={{ height: 280, margin: { t: 10, r: 10, b: 40, l: 70 }, yaxis: { tickformat: ',.2f', tickprefix: '€' }, plot_bgcolor: 'white', paper_bgcolor: 'white' }}
        config={{ displayModeBar: false, responsive: true }}
        style={{ width: '100%' }}
      />
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-slate-50 text-xs text-slate-500 uppercase tracking-wide">
              <th className="px-3 py-2 text-left">Date</th>
              <th className="px-3 py-2 text-left">Security</th>
              <th className="px-3 py-2 text-left">Ticker</th>
              <th className="px-3 py-2 text-left">Account</th>
              <th className="px-3 py-2 text-right">Amount</th>
              <th className="px-3 py-2 text-right">EUR</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {d.map((r, i) => (
              <tr key={i} className="hover:bg-slate-50">
                <td className="px-3 py-2 text-slate-500">{String(r.date ?? '').slice(0, 10)}</td>
                <td className="px-3 py-2 font-medium">{String(r.security)}</td>
                <td className="px-3 py-2 font-mono text-xs text-slate-500">{String(r.ticker ?? '—')}</td>
                <td className="px-3 py-2 text-slate-600">{String(r.account)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{fmtEur(Number(r.amount))} {String(r.currency)}</td>
                <td className="px-3 py-2 text-right tabular-nums font-semibold text-green-700">{fmtEur(Number(r.amount_eur))}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Capital Gains ─────────────────────────────────────────────────────────────
function CapitalGainsReport() {
  const [year, setYear] = useState(new Date().getFullYear())
  const { data = [], isLoading } = useQuery({
    queryKey: ['capital-gains', year],
    queryFn: () => getCapitalGains(year),
  })
  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  const d = data as Row[]
  const totalGain = d.reduce((s, r) => s + Number(r.gain_loss ?? 0), 0)
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <label className="text-sm font-medium text-slate-600">Tax Year</label>
        <Input type="number" className="w-24" value={year} onChange={e => setYear(Number(e.target.value))} />
        <div className={`ml-4 rounded-lg px-3 py-2 ${totalGain >= 0 ? 'bg-green-50' : 'bg-red-50'}`}>
          <span className="text-xs text-slate-500">Total Gain / Loss: </span>
          <span className={`font-bold tabular-nums ${totalGain >= 0 ? 'text-green-700' : 'text-red-600'}`}>{fmtEur(totalGain)}</span>
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-slate-50 text-xs text-slate-500 uppercase tracking-wide">
              <th className="px-3 py-2 text-left">Date</th>
              <th className="px-3 py-2 text-left">Security</th>
              <th className="px-3 py-2 text-left">Ticker</th>
              <th className="px-3 py-2 text-left">Account</th>
              <th className="px-3 py-2 text-right">Qty</th>
              <th className="px-3 py-2 text-right">Sell Price</th>
              <th className="px-3 py-2 text-right">Avg Cost</th>
              <th className="px-3 py-2 text-right">Gain / Loss</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {d.map((r, i) => (
              <tr key={i} className="hover:bg-slate-50">
                <td className="px-3 py-2 text-slate-500">{String(r.date ?? '').slice(0, 10)}</td>
                <td className="px-3 py-2 font-medium">{String(r.security)}</td>
                <td className="px-3 py-2 font-mono text-xs text-slate-500">{String(r.ticker ?? '—')}</td>
                <td className="px-3 py-2 text-slate-600">{String(r.account)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{Number(r.quantity).toLocaleString('el-GR', { maximumFractionDigits: 4 })}</td>
                <td className="px-3 py-2 text-right tabular-nums">{fmtEur(Number(r.sell_price))}</td>
                <td className="px-3 py-2 text-right tabular-nums">{fmtEur(Number(r.avg_cost))}</td>
                <td className={`px-3 py-2 text-right tabular-nums font-semibold ${Number(r.gain_loss) >= 0 ? 'text-green-700' : 'text-red-600'}`}>{fmtEur(Number(r.gain_loss))}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Budget vs Actual ──────────────────────────────────────────────────────────
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
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-slate-50 text-xs text-slate-500 uppercase tracking-wide">
              <th className="px-3 py-2 text-left">Category</th>
              <th className="px-3 py-2 text-right">Budget</th>
              <th className="px-3 py-2 text-right">Actual</th>
              <th className="px-3 py-2 text-right">Variance</th>
              <th className="px-3 py-2 text-right">% Used</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {d.map((r, i) => {
              const budg = Number(r.budget ?? 0)
              const act = Number(r.actual ?? 0)
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
    </div>
  )
}

// ── Cash Flow Forecast ────────────────────────────────────────────────────────
function CashFlowReport() {
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
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-slate-50 text-xs text-slate-500 uppercase tracking-wide">
              <th className="px-3 py-2 text-left">Template</th>
              <th className="px-3 py-2 text-left">Account</th>
              <th className="px-3 py-2 text-left">Payee</th>
              <th className="px-3 py-2 text-left">Periodicity</th>
              <th className="px-3 py-2 text-left">Next Due</th>
              <th className="px-3 py-2 text-right">Amount</th>
            </tr>
          </thead>
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
    </div>
  )
}

// ── Category Breakdown ────────────────────────────────────────────────────────
function CategoryBreakdownReport({ startDate, endDate }: { startDate: string; endDate: string }) {
  const { data = [], isLoading } = useQuery({
    queryKey: ['category-breakdown', startDate, endDate],
    queryFn: () => getCategoryBreakdown(startDate, endDate),
  })
  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  const rows = (data as Row[]).sort((a, b) => Number(b.total ?? 0) - Number(a.total ?? 0))
  const grandTotal = rows.reduce((s, r) => s + Number(r.total ?? 0), 0)

  return (
    <div className="space-y-3">
      <div className="bg-slate-50 rounded-lg p-3 inline-flex gap-6">
        <div><p className="text-xs text-slate-500">Total Spending</p><p className="font-bold text-slate-800 tabular-nums">{fmtEur(grandTotal)}</p></div>
        <div><p className="text-xs text-slate-500">Categories</p><p className="font-bold text-slate-800">{rows.length}</p></div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-slate-50 text-xs text-slate-500 uppercase tracking-wide">
              <th className="px-3 py-2 text-left">Category</th>
              <th className="px-3 py-2 text-left w-24">Type</th>
              <th className="px-3 py-2 text-right w-32">Amount</th>
              <th className="px-3 py-2 text-right w-24">% of Total</th>
              <th className="px-3 py-2 w-40">Share</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {rows.map((r, i) => {
              const amt = Number(r.total ?? 0)
              const pct = grandTotal > 0 ? amt / grandTotal * 100 : 0
              const depth = (String(r.category ?? '').match(/ : /g) || []).length
              return (
                <tr key={i} className="hover:bg-slate-50">
                  <td className="px-3 py-1.5 text-slate-700" style={{ paddingLeft: `${depth * 16 + 12}px` }}>
                    {String(r.category ?? '').split(' : ').pop()}
                  </td>
                  <td className="px-3 py-1.5 text-xs text-slate-500">{String(r.type ?? '')}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums font-medium">{fmtEur(amt)}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums text-slate-500">{pct.toFixed(1)}%</td>
                  <td className="px-3 py-1.5">
                    <div className="w-full h-1.5 bg-slate-100 rounded-full overflow-hidden">
                      <div className="h-full bg-blue-500 rounded-full" style={{ width: `${Math.min(pct, 100)}%` }} />
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Main Reports page ─────────────────────────────────────────────────────────
export default function Reports() {
  const [activeTab, setActiveTab] = useState('net-worth')
  const [startDate, setStartDate] = useState('2024-01-01')
  const [endDate, setEndDate] = useState(new Date().toISOString().slice(0, 10))

  const current = REPORT_TABS.find(t => t.key === activeTab)

  return (
    <div className="flex h-full">
      {/* Sidebar nav */}
      <nav className="w-44 shrink-0 border-r border-slate-200 bg-slate-50 flex flex-col py-4">
        <p className="px-4 text-xs font-semibold text-slate-400 uppercase tracking-wide mb-2">Reports</p>
        {REPORT_TABS.map(t => (
          <button
            key={t.key}
            onClick={() => setActiveTab(t.key)}
            className={`text-left px-4 py-2 text-sm transition-colors ${activeTab === t.key ? 'bg-blue-50 text-blue-700 font-semibold border-r-2 border-blue-600' : 'text-slate-600 hover:bg-slate-100'}`}
          >
            {t.label}
          </button>
        ))}
      </nav>

      {/* Content */}
      <div className="flex-1 min-w-0 overflow-auto">
        <div className="flex items-center justify-between px-6 py-3 border-b border-slate-200 bg-white sticky top-0 z-10">
          <h2 className="text-base font-semibold text-slate-800">{current?.label}</h2>
          <div className="flex items-center gap-2">
            <Input type="date" className="w-36 text-sm" value={startDate} onChange={e => setStartDate(e.target.value)} />
            <span className="text-slate-400 text-sm">to</span>
            <Input type="date" className="w-36 text-sm" value={endDate} onChange={e => setEndDate(e.target.value)} />
          </div>
        </div>

        <div className="p-6">
          <Card>
            <CardBody>
              {activeTab === 'net-worth' && <NetWorthReport startDate={startDate} endDate={endDate} />}
              {activeTab === 'income-expense' && <IncomeExpenseReport startDate={startDate} endDate={endDate} />}
              {activeTab === 'pnl' && <PnlReport startDate={startDate} endDate={endDate} />}
              {activeTab === 'savings' && <SavingsReport />}
              {activeTab === 'categories' && <CategoriesReport startDate={startDate} endDate={endDate} />}
              {activeTab === 'portfolio' && <PortfolioReport />}
              {activeTab === 'allocation' && <AllocationReport />}
              {activeTab === 'dividends' && <DividendsReport startDate={startDate} endDate={endDate} />}
              {activeTab === 'capital-gains' && <CapitalGainsReport />}
              {activeTab === 'budget' && <BudgetReport />}
              {activeTab === 'cashflow' && <CashFlowReport />}
              {activeTab === 'category-breakdown' && <CategoryBreakdownReport startDate={startDate} endDate={endDate} />}
            </CardBody>
          </Card>
        </div>
      </div>
    </div>
  )
}
