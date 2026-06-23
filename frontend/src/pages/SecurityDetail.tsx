import React, { useState, useMemo, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { AgGridReact } from 'ag-grid-react'
import PlotlyReact from 'react-plotly.js'
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const Plot: React.ComponentType<any> = (PlotlyReact as any).default ?? PlotlyReact
import { ArrowLeft, Plus, Trash2, Pencil, Save, X, Search, Copy } from 'lucide-react'
import {
  Card, CardBody, PageHeader, Button, Input, Spinner, StatCard,
} from '@/components/ui'
import {
  getSecurities, getPriceHistory, addPrice, deletePrice,
  getSecurityTransactions, getSecurityHoldings,
  getSecurityDividends,
  getSecurityCorporateActions, createCorporateAction, updateCorporateAction, deleteCorporateAction,
  previewCorporateAction, executeCorporateAction,
  getSecurityPriceAnomalies, deleteSecurityPrice,
} from '@/lib/api'

// ── Shared period helper (mirrors MarketData) ─────────────────────────────────
const CHART_PERIODS = ['3M', '6M', 'YTD', '1Y', '3Y', '5Y', 'All'] as const
type ChartPeriod = typeof CHART_PERIODS[number]

function periodToFromDate(p: ChartPeriod): string {
  const now = new Date()
  if (p === 'All') return '1900-01-01'
  if (p === 'YTD') return `${now.getFullYear()}-01-01`
  const months: Record<string, number> = { '3M': 3, '6M': 6, '1Y': 12, '3Y': 36, '5Y': 60 }
  const d = new Date(now)
  d.setMonth(d.getMonth() - months[p])
  return d.toISOString().slice(0, 10)
}

function PeriodSelector({ value, onChange }: { value: ChartPeriod; onChange: (p: ChartPeriod) => void }) {
  return (
    <div className="flex gap-1">
      {CHART_PERIODS.map(p => (
        <button key={p} onClick={() => onChange(p)}
          className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${value === p ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}>
          {p}
        </button>
      ))}
    </div>
  )
}

function fmt(n: unknown, dec = 4) {
  if (n == null) return '—'
  return Number(n).toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec })
}
function fmtPct(n: unknown) {
  if (n == null) return '—'
  return `${Number(n).toFixed(2)}%`
}

// ── Prices Tab ────────────────────────────────────────────────────────────────
function PricesTab({ secId }: { secId: number }) {
  const qc = useQueryClient()
  const [period, setPeriod] = useState<ChartPeriod>('All')
  const fromDate = periodToFromDate(period)
  const [priceSearch, setPriceSearch] = useState('')
  const [action, setAction] = useState<'save' | 'delete'>('save')
  const [entryDate, setEntryDate] = useState(new Date().toISOString().slice(0, 10))
  const [entryValue, setEntryValue] = useState('')
  const [msg, setMsg] = useState<string | null>(null)

  const { data: history = [], isLoading } = useQuery({
    queryKey: ['price-history', secId, fromDate],
    queryFn: () => getPriceHistory(secId, fromDate),
  })

  const addMut = useMutation({
    mutationFn: addPrice,
    onSuccess: () => { setMsg('Saved.'); qc.invalidateQueries({ queryKey: ['price-history', secId] }); setEntryValue('') },
    onError: (e: Error) => setMsg(`Error: ${e.message}`),
  })
  const delMut = useMutation({
    mutationFn: ({ sid, d }: { sid: number; d: string }) => deletePrice(sid, d),
    onSuccess: () => { setMsg('Deleted.'); qc.invalidateQueries({ queryKey: ['price-history', secId] }) },
    onError: (e: Error) => setMsg(`Error: ${e.message}`),
  })

  const handleSubmit = () => {
    if (!entryDate) return
    setMsg(null)
    if (action === 'delete') delMut.mutate({ sid: secId, d: entryDate })
    else { if (!entryValue) return; addMut.mutate({ security_id: secId, date: entryDate, close: Number(entryValue) }) }
  }

  const isPending = addMut.isPending || delMut.isPending
  const rows = [...(history as Record<string, unknown>[])].reverse()

  return (
    <div className="p-4 space-y-4">
      <div className="flex items-center gap-4">
        <PeriodSelector value={period} onChange={setPeriod} />
      </div>

      {isLoading ? <div className="flex justify-center py-12"><Spinner /></div> : (
        <Plot
          data={[{
            x: (history as Record<string, unknown>[]).map(r => r.date),
            y: (history as Record<string, unknown>[]).map(r => r.close),
            type: 'scatter', mode: 'lines',
            line: { color: '#3b82f6', width: 1.5 },
          }]}
          layout={{ height: 300, margin: { t: 10, r: 10, b: 40, l: 70 }, plot_bgcolor: 'white', paper_bgcolor: 'white', hovermode: 'x unified' }}
          config={{ displayModeBar: true, responsive: true }}
          style={{ width: '100%' }}
        />
      )}

      <div className="flex items-center gap-2">
        <Search size={14} className="text-slate-400" />
        <Input className="w-56 h-7 text-xs" placeholder="Search…" value={priceSearch} onChange={e => setPriceSearch(e.target.value)} />
      </div>
      <div className="ag-theme-alpine" style={{ height: '300px', width: '100%' }}>
        <AgGridReact
          rowData={rows}
          quickFilterText={priceSearch}
          columnDefs={[
            { field: 'date', headerName: 'Date', width: 130, sort: 'desc' },
            { field: 'close', headerName: 'Close', width: 130, valueFormatter: (p: { value: unknown }) => p.value != null ? Number(p.value).toFixed(6) : '' },
            { field: 'source', headerName: 'Source', width: 120 },
            { field: 'downloaded_at', headerName: 'Downloaded At', flex: 1 },
          ]}
          defaultColDef={{ resizable: true, sortable: true, filter: true }}
        />
      </div>

      <div className="border-t border-slate-200 pt-4">
        <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-3">Manual Entry</p>
        <div className="flex flex-wrap gap-3 items-end">
          <div className="flex gap-1">
            {(['save', 'delete'] as const).map(a => (
              <button key={a} onClick={() => { setAction(a); setMsg(null) }}
                className={`px-3 py-1.5 rounded text-xs font-medium ${action === a ? (a === 'delete' ? 'bg-red-600 text-white' : 'bg-blue-600 text-white') : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}>
                {a === 'save' ? 'Save / Upsert' : 'Delete Record'}
              </button>
            ))}
          </div>
          <div>
            <label className="text-xs font-medium text-slate-500 block mb-1">Date</label>
            <Input type="date" className="w-36" value={entryDate} onChange={e => setEntryDate(e.target.value)} />
          </div>
          {action === 'save' && (
            <div>
              <label className="text-xs font-medium text-slate-500 block mb-1">Close Price</label>
              <Input type="number" step="any" className="w-32" value={entryValue} onChange={e => setEntryValue(e.target.value)} placeholder="0.0000" />
            </div>
          )}
          <Button onClick={handleSubmit} disabled={isPending} variant={action === 'delete' ? 'destructive' : 'primary'}>
            {action === 'delete' ? <><Trash2 size={14} /> Delete</> : <><Plus size={14} /> Save</>}
          </Button>
          {msg && <span className={`text-xs px-3 py-1.5 rounded ${msg.startsWith('Error') ? 'bg-red-50 text-red-600' : 'bg-green-50 text-green-700'}`}>{msg}</span>}
        </div>
      </div>
    </div>
  )
}

// ── Investment Transactions Tab ────────────────────────────────────────────────
function InvestmentTransactionsTab({ secId, security }: { secId: number; security: Record<string, unknown> }) {
  const { data: txData = [], isLoading: txLoading } = useQuery({
    queryKey: ['sec-transactions', secId],
    queryFn: () => getSecurityTransactions(secId),
  })
  const { data: holdingsData, isLoading: holdingsLoading } = useQuery({
    queryKey: ['sec-holdings', secId],
    queryFn: () => getSecurityHoldings(secId),
  })

  const transactions = txData as Record<string, unknown>[]
  const holdings = (holdingsData as { holdings: Record<string, unknown>[]; latest_price: number | null; price_date: string | null }) ?? { holdings: [], latest_price: null, price_date: null }
  const totalQty = holdings.holdings.reduce((s, r) => s + Number(r.qty_held ?? 0), 0)
  const totalValue = holdings.holdings.reduce((s, r) => s + Number(r.current_value ?? 0), 0)
  const totalCost = holdings.holdings.reduce((s, r) => s + Number(r.cost_basis ?? 0), 0)
  const totalPnl = totalValue - totalCost

  const copyText = useCallback(() => {
    const header = 'Account\tQty Held\tCost Basis\tCur. Value\tUnrealised P&L'
    const rows = holdings.holdings.map(r =>
      `${r.account}\t${fmt(r.qty_held)}\t${fmt(r.cost_basis, 2)}\t${fmt(r.current_value, 2)}\t${fmt(r.unrealised_pnl, 2)}`
    ).join('\n')
    navigator.clipboard.writeText(header + '\n' + rows)
  }, [holdings.holdings])

  if (txLoading || holdingsLoading) return <div className="flex justify-center py-12"><Spinner /></div>

  return (
    <div className="p-4 space-y-6">
      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Transactions" value={String(transactions.length)} />
        <StatCard label="Total Qty Held" value={fmt(totalQty)} />
        <StatCard
          label={holdings.latest_price != null ? `Price (${holdings.price_date})` : 'Price'}
          value={holdings.latest_price != null ? fmt(holdings.latest_price, 4) : '—'}
        />
        <StatCard
          label="Est. Current Value"
          value={totalValue ? fmt(totalValue, 2) : '—'}
          subs={totalPnl !== 0 ? [{ text: `${totalPnl >= 0 ? '+' : ''}${fmt(totalPnl, 2)} P&L`, color: totalPnl >= 0 ? 'text-green-600' : 'text-red-600' }] : undefined}
        />
      </div>

      {/* Holdings by Account */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <p className="text-sm font-semibold text-slate-700">Holdings by Account</p>
          <Button size="sm" variant="secondary" onClick={copyText}><Copy size={13} /> Copy</Button>
        </div>
        <div className="ag-theme-alpine" style={{ height: `${Math.min(holdings.holdings.length * 42 + 48, 300)}px`, width: '100%' }}>
          <AgGridReact
            rowData={holdings.holdings}
            columnDefs={[
              { field: 'account', headerName: 'Account', flex: 2 },
              { field: 'qty_held', headerName: 'Qty Held', flex: 1, valueFormatter: (p: { value: unknown }) => fmt(p.value) },
              { field: 'cost_basis', headerName: 'Cost Basis', flex: 1, valueFormatter: (p: { value: unknown }) => fmt(p.value, 2) },
              { field: 'current_value', headerName: 'Cur. Value', flex: 1, valueFormatter: (p: { value: unknown }) => fmt(p.value, 2) },
              {
                field: 'unrealised_pnl', headerName: 'Unrealised P&L', flex: 1,
                valueFormatter: (p: { value: unknown }) => fmt(p.value, 2),
                cellStyle: (p: { value: unknown }) => ({ color: Number(p.value) >= 0 ? '#16a34a' : '#dc2626' }),
              },
            ]}
            defaultColDef={{ resizable: true, sortable: true }}
          />
        </div>
      </div>

      {/* All Transactions */}
      <div>
        <p className="text-sm font-semibold text-slate-700 mb-2">All Transactions ({transactions.length})</p>
        <div className="ag-theme-alpine" style={{ height: '400px', width: '100%' }}>
          <AgGridReact
            rowData={transactions}
            columnDefs={[
              { field: 'account', headerName: 'Account', width: 180 },
              { field: 'date', headerName: 'Date', width: 120 },
              { field: 'action', headerName: 'Action', width: 100 },
              { field: 'quantity', headerName: 'Quantity', width: 110, valueFormatter: (p: { value: unknown }) => fmt(p.value) },
              { field: 'price_per_share', headerName: 'Price/Share', width: 120, valueFormatter: (p: { value: unknown }) => fmt(p.value) },
              { field: 'commission', headerName: 'Commission', width: 120, valueFormatter: (p: { value: unknown }) => fmt(p.value, 2) },
              { field: 'total_sec_cur', headerName: 'Total (Sec. Cur.)', width: 140, valueFormatter: (p: { value: unknown }) => fmt(p.value, 2) },
              { field: 'total_acc_cur', headerName: 'Total (Acc. Cur.)', width: 140, valueFormatter: (p: { value: unknown }) => fmt(p.value, 2) },
              { field: 'currency', headerName: 'Currency', width: 90 },
              { field: 'description', headerName: 'Description', flex: 1 },
            ]}
            defaultColDef={{ resizable: true, sortable: true, filter: true }}
          />
        </div>
      </div>
    </div>
  )
}

// ── Price Anomalies Tab ───────────────────────────────────────────────────────
function PriceAnomaliesTab({ secId }: { secId: number }) {
  const qc = useQueryClient()
  const [threshold, setThreshold] = useState(100)
  const [selected, setSelected] = useState<string[]>([])

  const { data = [], isLoading } = useQuery({
    queryKey: ['sec-anomalies', secId, threshold],
    queryFn: () => getSecurityPriceAnomalies(secId, threshold),
  })
  const anomalies = data as Record<string, unknown>[]

  const deleteMut = useMutation({
    mutationFn: (date: string) => deleteSecurityPrice(secId, date),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['sec-anomalies', secId] })
      qc.invalidateQueries({ queryKey: ['price-history', secId] })
      setSelected([])
    },
  })

  const deleteSelected = async () => {
    for (const d of selected) await deleteMut.mutateAsync(d)
  }
  const deleteAll = async () => {
    for (const r of anomalies) await deleteMut.mutateAsync(r.date as string)
  }

  const pctFmt = (v: unknown) => v != null ? `${((Number(v) - 1) * 100).toFixed(1)}%` : '—'

  return (
    <div className="p-4 space-y-4">
      <p className="text-sm text-slate-500">
        Flags prices that changed by more than the chosen threshold vs the previous or next trading day.
      </p>
      <div className="space-y-1">
        <label className="text-xs font-medium text-slate-500">Flag when move exceeds (%): <span className="font-bold text-slate-700">{threshold}</span></label>
        <input type="range" min={5} max={500} step={5} value={threshold} onChange={e => setThreshold(Number(e.target.value))}
          className="w-full max-w-md accent-red-500" />
      </div>

      {isLoading ? <div className="flex justify-center py-8"><Spinner /></div> : (
        <>
          {anomalies.length > 0 && (
            <div className="bg-yellow-50 border border-yellow-200 rounded-lg px-4 py-2 text-sm text-yellow-800 font-medium">
              {anomalies.length} suspicious price record(s).
            </div>
          )}
          <div className="ag-theme-alpine" style={{ height: '400px', width: '100%' }}>
            <AgGridReact
              rowData={anomalies}
              rowSelection="multiple"
              onSelectionChanged={e => setSelected(e.api.getSelectedRows().map((r: Record<string, unknown>) => r.date as string))}
              columnDefs={[
                { checkboxSelection: true, width: 50, pinned: 'left' as const },
                { field: 'date', headerName: 'Date', width: 120 },
                { field: 'close', headerName: 'Price', width: 110, valueFormatter: (p: { value: unknown }) => fmt(p.value) },
                { field: 'prev_close', headerName: 'Prev Close', width: 120, valueFormatter: (p: { value: unknown }) => fmt(p.value) },
                { field: 'next_close', headerName: 'Next Close', width: 120, valueFormatter: (p: { value: unknown }) => fmt(p.value) },
                { field: 'ratio_prev', headerName: '% vs Prev', width: 110, valueFormatter: (p: { value: unknown }) => pctFmt(p.value) },
                { field: 'ratio_next', headerName: '% vs Next', width: 110, valueFormatter: (p: { value: unknown }) => pctFmt(p.value) },
              ]}
              defaultColDef={{ resizable: true, sortable: true }}
            />
          </div>
          <div className="flex gap-3">
            <Button size="sm" variant="secondary" disabled={selected.length === 0 || deleteMut.isPending} onClick={deleteSelected}>
              <Trash2 size={13} /> Delete selected
            </Button>
            <Button size="sm" variant="destructive" disabled={anomalies.length === 0 || deleteMut.isPending} onClick={deleteAll}>
              <Trash2 size={13} /> Delete all {anomalies.length} listed
            </Button>
          </div>
        </>
      )}
    </div>
  )
}

// ── Dividends Tab ─────────────────────────────────────────────────────────────
function DividendsTab({ secId, security }: { secId: number; security: Record<string, unknown> }) {
  const { data = [], isLoading } = useQuery({
    queryKey: ['sec-dividends', secId],
    queryFn: () => getSecurityDividends(secId),
  })
  const dividends = data as Record<string, unknown>[]

  // Bar chart: total per year
  const byYear = useMemo(() => {
    const map: Record<string, number> = {}
    for (const d of dividends) {
      const yr = String(d.ex_date ?? '').slice(0, 4)
      if (yr) map[yr] = (map[yr] ?? 0) + Number(d.amount ?? 0)
    }
    return Object.entries(map).sort(([a], [b]) => a.localeCompare(b))
  }, [dividends])

  return (
    <div className="p-4 space-y-5">
      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Dividend Yield" value={fmtPct(security.dividend_yield)} />
        <StatCard label="Annual Rate" value={security.dividend_rate != null ? String(Number(security.dividend_rate).toFixed(4)) : '—'} />
        <StatCard label="5Y Avg Yield" value={fmtPct(security.five_year_avg_yield)} />
        <StatCard label="Payout Ratio" value={fmtPct(security.payout_ratio)} />
      </div>
      <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
        <StatCard label="Ex-Dividend Date" value={String(security.ex_dividend_date ?? '—')} />
        <StatCard label="Payment Date" value={String(security.dividend_pay_date ?? '—')} />
        <StatCard label="Frequency" value={String(security.dividend_frequency ?? '—')} />
      </div>

      {isLoading ? <div className="flex justify-center py-8"><Spinner /></div> : (
        <>
          <div>
            <p className="text-sm font-semibold text-slate-700 mb-2">Dividend History</p>
            {byYear.length > 0 ? (
              <Plot
                data={[{
                  x: byYear.map(([yr]) => yr),
                  y: byYear.map(([, v]) => v),
                  type: 'bar',
                  marker: { color: '#3b82f6' },
                  name: 'Total Dividend per Share',
                }]}
                layout={{ height: 300, margin: { t: 10, r: 10, b: 40, l: 60 }, plot_bgcolor: 'white', paper_bgcolor: 'white', yaxis: { title: { text: 'Total Dividend per Share' } } }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: '100%' }}
              />
            ) : <p className="text-sm text-slate-400">No dividend history found.</p>}
          </div>

          <div className="ag-theme-alpine" style={{ height: '300px', width: '100%' }}>
            <AgGridReact
              rowData={dividends}
              columnDefs={[
                { field: 'ex_date', headerName: 'Ex-Date', width: 130 },
                { field: 'pay_date', headerName: 'Pay Date', width: 130 },
                { field: 'amount', headerName: 'Amount per Share', flex: 1, valueFormatter: (p: { value: unknown }) => fmt(p.value) },
              ]}
              defaultColDef={{ resizable: true, sortable: true, filter: true }}
            />
          </div>
        </>
      )}
    </div>
  )
}

// ── Corporate Actions Tab ─────────────────────────────────────────────────────
type EventGroup = 'split' | 'default_delisting' | 'dividend'

const TODAY = new Date().toISOString().slice(0, 10)

function CorporateActionsTab({ secId, security }: { secId: number; security: Record<string, unknown> }) {
  const qc = useQueryClient()
  const secName = String(security.name ?? '') + (security.currency ? ` (${security.currency})` : '')

  // ── existing CA list ──────────────────────────────────────────────────────
  const [deleteId, setDeleteId] = useState('')
  const [editRow, setEditRow] = useState<Record<string, unknown> | null>(null)
  const [editForm, setEditForm] = useState<Record<string, string>>({})

  const { data = [], isLoading } = useQuery({
    queryKey: ['sec-corporate-actions', secId],
    queryFn: () => getSecurityCorporateActions(secId),
  })
  const actions = data as Record<string, unknown>[]
  const invalidateCA = () => qc.invalidateQueries({ queryKey: ['sec-corporate-actions', secId] })

  const deleteMut = useMutation({ mutationFn: (id: number) => deleteCorporateAction(secId, id), onSuccess: () => { invalidateCA(); setDeleteId('') } })
  const updateMut = useMutation({ mutationFn: ({ id, d }: { id: number; d: Record<string, unknown> }) => updateCorporateAction(secId, id, d), onSuccess: () => { invalidateCA(); setEditRow(null) } })

  // ── new CA form ───────────────────────────────────────────────────────────
  const [eventGroup, setEventGroup] = useState<EventGroup>('split')
  const [selectedAccounts, setSelectedAccounts] = useState<string[]>([])
  const [date, setDate] = useState(TODAY)
  // split
  const [ratioNew, setRatioNew] = useState('2')
  const [ratioOld, setRatioOld] = useState('1')
  // default/delisting
  const [ddType, setDdType] = useState('Default')
  // dividend
  const [grossPerShare, setGrossPerShare] = useState('0')
  const [taxRate, setTaxRate] = useState('15')

  const [preview, setPreview] = useState<Record<string, unknown>[] | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [executeMsg, setExecuteMsg] = useState<{ ok: boolean; text: string } | null>(null)

  // Holdings to populate account multi-select
  const { data: holdingsData } = useQuery({ queryKey: ['sec-holdings', secId], queryFn: () => getSecurityHoldings(secId) })
  const holdingAccounts = ((holdingsData as { holdings: Record<string, unknown>[] })?.holdings ?? [])
    .filter(h => Number(h.qty_held) !== 0)

  // Auto-description
  const autoDesc = useMemo(() => {
    if (eventGroup === 'split') {
      const rn = ratioNew || '?', ro = ratioOld || '?'
      const isForward = Number(rn) >= Number(ro)
      return `${secName} ${rn}-for-${ro} ${isForward ? 'Stock Split' : 'Reverse Split'}`
    }
    if (eventGroup === 'default_delisting') return `${secName} — ${ddType}`
    const g = Number(grossPerShare).toFixed(10)
    const tax = Number(taxRate)
    const net = (Number(grossPerShare) * (1 - tax / 100)).toFixed(10)
    return `${secName} Dividend — ${g} gross / ${net} net per share`
  }, [eventGroup, secName, ratioNew, ratioOld, ddType, grossPerShare, taxRate])

  const buildPayload = () => ({
    event_group: eventGroup,
    account_names: selectedAccounts.length ? selectedAccounts : null,
    date,
    description: autoDesc,
    // split
    ratio_new: eventGroup === 'split' ? ratioNew : null,
    ratio_old: eventGroup === 'split' ? ratioOld : null,
    // default/delisting
    action_type: eventGroup === 'default_delisting' ? ddType : null,
    // dividend
    gross_per_share: eventGroup === 'dividend' ? grossPerShare : null,
    tax_rate: eventGroup === 'dividend' ? taxRate : null,
  })

  const handlePreview = async () => {
    setPreviewLoading(true)
    setPreview(null)
    setExecuteMsg(null)
    try {
      const rows = await previewCorporateAction(secId, buildPayload()) as Record<string, unknown>[]
      setPreview(rows)
    } finally {
      setPreviewLoading(false)
    }
  }

  const executeMut = useMutation({
    mutationFn: () => executeCorporateAction(secId, buildPayload()),
    onSuccess: (res: unknown) => {
      const r = res as { transactions_inserted: number }
      setExecuteMsg({ ok: true, text: `Done — ${r.transactions_inserted} transaction(s) inserted.` })
      setPreview(null)
      invalidateCA()
      qc.invalidateQueries({ queryKey: ['sec-transactions', secId] })
      qc.invalidateQueries({ queryKey: ['sec-holdings', secId] })
    },
    onError: (e: Error) => setExecuteMsg({ ok: false, text: e.message }),
  })

  // Preview column defs vary by event group
  const previewCols = useMemo(() => {
    if (eventGroup === 'split' || eventGroup === 'default_delisting') return [
      { field: 'account', headerName: 'Account', flex: 2 },
      { field: 'action', headerName: 'Action', width: 90 },
      { field: 'qty_before', headerName: 'Qty Before', width: 120, valueFormatter: (p: { value: unknown }) => fmt(p.value) },
      { field: 'delta', headerName: 'Delta', width: 120, valueFormatter: (p: { value: unknown }) => fmt(p.value) },
      { field: 'qty_after', headerName: 'Qty After', width: 120, valueFormatter: (p: { value: unknown }) => fmt(p.value) },
    ]
    return [
      { field: 'account', headerName: 'Account', flex: 2 },
      { field: 'qty_held', headerName: 'Qty Held', width: 110, valueFormatter: (p: { value: unknown }) => fmt(p.value) },
      { field: 'gross_per_share', headerName: 'Gross/Share', width: 120, valueFormatter: (p: { value: unknown }) => fmt(p.value) },
      { field: 'gross_total', headerName: 'Gross Total', width: 120, valueFormatter: (p: { value: unknown }) => fmt(p.value, 2) },
      { field: 'tax', headerName: 'Tax', width: 100, valueFormatter: (p: { value: unknown }) => fmt(p.value, 2) },
      { field: 'net_total', headerName: 'Net Total', width: 110, valueFormatter: (p: { value: unknown }) => fmt(p.value, 2) },
      { field: 'currency', headerName: 'Ccy', width: 70 },
    ]
  }, [eventGroup])

  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>

  return (
    <div className="p-4 space-y-6">

      {/* Existing CA list */}
      <div>
        <p className="text-sm text-slate-500 mb-2">
          {actions.length} corporate action(s) on record
          {actions.length > 0 ? ' — click Edit to modify, or delete individual rows.' : '.'}
        </p>
        {actions.length > 0 && (
          <>
            <div className="ag-theme-alpine" style={{ height: `${Math.min(actions.length * 42 + 52, 260)}px`, width: '100%' }}>
              <AgGridReact
                rowData={actions}
                columnDefs={[
                  { field: 'id', headerName: 'ID', width: 65 },
                  { field: 'date', headerName: 'Date', width: 120 },
                  { field: 'type', headerName: 'Type', width: 140 },
                  { field: 'ratio_new', headerName: 'Ratio New', width: 100, valueFormatter: (p: { value: unknown }) => String(p.value ?? 'None') },
                  { field: 'ratio_old', headerName: 'Ratio Old', width: 100, valueFormatter: (p: { value: unknown }) => String(p.value ?? 'None') },
                  { field: 'description', headerName: 'Description', flex: 1 },
                  { field: 'recorded_at', headerName: 'Recorded At', width: 180 },
                  {
                    headerName: '', width: 75, pinned: 'right' as const,
                    cellRenderer: (p: { data: Record<string, unknown> }) => (
                      <button onClick={() => { setEditRow(p.data); setEditForm({ type: String(p.data.type), date: String(p.data.date), ratio_new: String(p.data.ratio_new ?? ''), ratio_old: String(p.data.ratio_old ?? ''), description: String(p.data.description ?? '') }) }}
                        className="text-blue-600 hover:underline text-xs flex items-center gap-1 mt-2">
                        <Pencil size={11} /> Edit
                      </button>
                    ),
                  },
                ]}
                defaultColDef={{ resizable: true, sortable: true }}
              />
            </div>

            {editRow && (
              <div className="border border-blue-200 rounded-lg p-4 bg-blue-50 space-y-3 mt-3">
                <p className="text-sm font-semibold text-slate-700">Edit Corporate Action #{editRow.id}</p>
                <div className="flex flex-wrap gap-3">
                  <div><label className="text-xs text-slate-500 block mb-1">Date</label>
                    <Input type="date" className="w-36" value={editForm.date} onChange={e => setEditForm(f => ({ ...f, date: e.target.value }))} /></div>
                  <div><label className="text-xs text-slate-500 block mb-1">Ratio New</label>
                    <Input className="w-24" value={editForm.ratio_new} onChange={e => setEditForm(f => ({ ...f, ratio_new: e.target.value }))} /></div>
                  <div><label className="text-xs text-slate-500 block mb-1">Ratio Old</label>
                    <Input className="w-24" value={editForm.ratio_old} onChange={e => setEditForm(f => ({ ...f, ratio_old: e.target.value }))} /></div>
                  <div className="flex-1 min-w-48"><label className="text-xs text-slate-500 block mb-1">Description</label>
                    <Input value={editForm.description} onChange={e => setEditForm(f => ({ ...f, description: e.target.value }))} /></div>
                </div>
                <div className="flex gap-2">
                  <Button size="sm" disabled={updateMut.isPending} onClick={() => updateMut.mutate({ id: Number(editRow.id), d: { type: editForm.type, date: editForm.date, ratio_new: editForm.ratio_new || null, ratio_old: editForm.ratio_old || null, description: editForm.description || null } })}>
                    <Save size={13} /> Save
                  </Button>
                  <Button size="sm" variant="secondary" onClick={() => setEditRow(null)}><X size={13} /> Cancel</Button>
                </div>
              </div>
            )}

            <div className="border-t border-slate-200 pt-3 space-y-2">
              <p className="text-xs font-semibold text-slate-500">Delete a corporate action:</p>
              <div className="flex gap-3 items-center">
                <select className="rounded-md border border-slate-300 px-3 py-1.5 text-sm w-72" value={deleteId} onChange={e => setDeleteId(e.target.value)}>
                  <option value="">— choose —</option>
                  {actions.map(a => <option key={String(a.id)} value={String(a.id)}>#{a.id} · {String(a.date)} · {String(a.type)}</option>)}
                </select>
                <Button size="sm" variant="destructive" disabled={!deleteId || deleteMut.isPending} onClick={() => deleteMut.mutate(Number(deleteId))}>
                  <Trash2 size={13} /> Delete
                </Button>
              </div>
            </div>
          </>
        )}
      </div>

      {/* Record new corporate action */}
      <div className="border-t border-slate-200 pt-4 space-y-5">
        <p className="text-sm font-semibold text-slate-700">Record New Corporate Action</p>

        {/* Event type radio */}
        <div>
          <p className="text-xs font-medium text-slate-500 mb-2">Event type</p>
          <div className="flex gap-6">
            {([['split', 'Stock Split / Reverse Split'], ['default_delisting', 'Default / Delisting'], ['dividend', 'Dividend']] as [EventGroup, string][]).map(([v, label]) => (
              <label key={v} className="flex items-center gap-2 cursor-pointer">
                <input type="radio" name="event-group" value={v} checked={eventGroup === v}
                  onChange={() => { setEventGroup(v); setPreview(null); setExecuteMsg(null); setSelectedAccounts([]) }}
                  className="accent-red-500 w-4 h-4" />
                <span className={`text-sm ${eventGroup === v ? 'font-semibold text-blue-700' : 'text-slate-600'}`}>{label}</span>
              </label>
            ))}
          </div>
        </div>

        {/* Account limit */}
        {holdingAccounts.length > 0 && (
          <div>
            <div className="flex items-center justify-between mb-2">
              <label className="text-xs font-medium text-slate-500">
                Limit to accounts <span className="text-slate-400">(none selected = all accounts)</span>
              </label>
              {selectedAccounts.length > 0 && (
                <button className="text-xs text-blue-600 hover:underline" onClick={() => setSelectedAccounts([])}>Clear selection</button>
              )}
            </div>
            <div className="flex flex-wrap gap-3">
              {holdingAccounts.map(h => {
                const name = String(h.account)
                const checked = selectedAccounts.includes(name)
                return (
                  <label key={name} className={`flex items-center gap-2 px-3 py-1.5 rounded-full border cursor-pointer text-sm transition-colors ${checked ? 'bg-blue-600 border-blue-600 text-white' : 'bg-white border-slate-300 text-slate-600 hover:border-blue-400'}`}>
                    <input type="checkbox" className="hidden" checked={checked}
                      onChange={() => setSelectedAccounts(prev => checked ? prev.filter(x => x !== name) : [...prev, name])} />
                    {name}
                    {h.qty_held != null && <span className={`text-xs ${checked ? 'text-blue-100' : 'text-slate-400'}`}>({fmt(h.qty_held, 2)})</span>}
                  </label>
                )
              })}
            </div>
          </div>
        )}

        {/* Instructional text */}
        {eventGroup === 'split' && (
          <p className="text-xs text-slate-500">
            Records a split by inserting a <strong>ShrIn</strong> (forward split) or <strong>ShrOut</strong> (reverse split) entry
            for the delta shares on the effective date. All existing broker-imported records are left untouched.
          </p>
        )}
        {eventGroup === 'default_delisting' && (
          <p className="text-xs text-slate-500">
            Records a company default or delisting by inserting a <strong>ShrOut</strong> for the <strong>full remaining quantity</strong> in
            each account, bringing the position to zero. All existing broker-imported records are left untouched.
          </p>
        )}
        {eventGroup === 'dividend' && (
          <p className="text-xs text-slate-500">
            Records a dividend payment by inserting a <strong>Dividend</strong> entry per account based on current holdings.
            Enter the gross amount per share and the applicable withholding tax rate; the net amount is computed automatically.
          </p>
        )}

        {/* Type-specific fields */}
        {eventGroup === 'split' && (
          <div className="space-y-3">
            <p className="text-sm font-semibold text-slate-700">Split ratio</p>
            <div className="flex gap-6">
              <div className="flex-1 max-w-xs">
                <label className="text-xs text-slate-500 block mb-1">New shares</label>
                <div className="flex items-center border border-slate-300 rounded-md">
                  <input type="number" step="any" className="flex-1 px-3 py-2 text-sm rounded-l-md outline-none" value={ratioNew} onChange={e => { setRatioNew(e.target.value); setPreview(null) }} />
                  <button className="px-3 py-2 text-slate-500 hover:bg-slate-100" onClick={() => setRatioNew(v => String(Math.max(0, Number(v) - 1)))}>−</button>
                  <button className="px-3 py-2 text-slate-500 hover:bg-slate-100" onClick={() => setRatioNew(v => String(Number(v) + 1))}>+</button>
                </div>
              </div>
              <div className="flex-1 max-w-xs">
                <label className="text-xs text-slate-500 block mb-1">Old shares</label>
                <div className="flex items-center border border-slate-300 rounded-md">
                  <input type="number" step="any" className="flex-1 px-3 py-2 text-sm rounded-l-md outline-none" value={ratioOld} onChange={e => { setRatioOld(e.target.value); setPreview(null) }} />
                  <button className="px-3 py-2 text-slate-500 hover:bg-slate-100" onClick={() => setRatioOld(v => String(Math.max(0, Number(v) - 1)))}>−</button>
                  <button className="px-3 py-2 text-slate-500 hover:bg-slate-100" onClick={() => setRatioOld(v => String(Number(v) + 1))}>+</button>
                </div>
              </div>
            </div>
          </div>
        )}

        {eventGroup === 'default_delisting' && (
          <div>
            <label className="text-xs text-slate-500 block mb-1">Event type</label>
            <select className="rounded-md border border-slate-300 px-3 py-1.5 text-sm w-48" value={ddType}
              onChange={e => { setDdType(e.target.value); setPreview(null) }}>
              <option>Default</option>
              <option>Delisting</option>
            </select>
          </div>
        )}

        {eventGroup === 'dividend' && (
          <div className="space-y-3">
            <div className="flex gap-6">
              <div className="flex-1 max-w-xs">
                <label className="text-xs text-slate-500 block mb-1">Gross amount per share</label>
                <div className="flex items-center border border-slate-300 rounded-md">
                  <input type="number" step="any" className="flex-1 px-3 py-2 text-sm rounded-l-md outline-none" value={grossPerShare} onChange={e => { setGrossPerShare(e.target.value); setPreview(null) }} />
                  <button className="px-3 py-2 text-slate-500 hover:bg-slate-100" onClick={() => setGrossPerShare(v => String(Math.max(0, Number(v) - 0.01).toFixed(10)))}>−</button>
                  <button className="px-3 py-2 text-slate-500 hover:bg-slate-100" onClick={() => setGrossPerShare(v => String((Number(v) + 0.01).toFixed(10)))}>+</button>
                </div>
              </div>
              <div className="flex-1 max-w-xs">
                <label className="text-xs text-slate-500 block mb-1">Withholding tax rate (%)</label>
                <div className="flex items-center border border-slate-300 rounded-md">
                  <input type="number" step="any" className="flex-1 px-3 py-2 text-sm rounded-l-md outline-none" value={taxRate} onChange={e => { setTaxRate(e.target.value); setPreview(null) }} />
                  <button className="px-3 py-2 text-slate-500 hover:bg-slate-100" onClick={() => setTaxRate(v => String(Math.max(0, Number(v) - 1)))}>−</button>
                  <button className="px-3 py-2 text-slate-500 hover:bg-slate-100" onClick={() => setTaxRate(v => String(Number(v) + 1))}>+</button>
                </div>
              </div>
            </div>
            {eventGroup === 'dividend' && (
              <p className="text-xs text-slate-500">
                Net per share: <strong>{(Number(grossPerShare) * (1 - Number(taxRate) / 100)).toFixed(10)}</strong>{' '}
                (gross {Number(grossPerShare).toFixed(10)} − {taxRate}% tax)
              </p>
            )}
          </div>
        )}

        {/* Effective/Payment date */}
        <div>
          <label className="text-xs text-slate-500 block mb-1">{eventGroup === 'dividend' ? 'Payment date' : 'Effective date'}</label>
          <Input type="date" className="w-36" value={date} onChange={e => { setDate(e.target.value); setPreview(null) }} />
        </div>

        {/* Auto description */}
        <div>
          <label className="text-xs text-slate-500 block mb-1">Description</label>
          <div className="w-full max-w-2xl rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-blue-700 font-medium">{autoDesc}</div>
        </div>

        {/* Preview button */}
        <div className="flex gap-3 items-center">
          <Button size="sm" variant="secondary" onClick={handlePreview} disabled={previewLoading}>
            {previewLoading ? <><Spinner size={14} /> Computing…</> : 'Preview'}
          </Button>
          {preview && preview.length === 0 && (
            <span className="text-xs text-slate-400">No accounts with holdings found for this selection.</span>
          )}
        </div>

        {/* Preview table */}
        {preview && preview.length > 0 && (
          <div className="space-y-3">
            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide">Preview — transactions to be created</p>
            <div className="ag-theme-alpine" style={{ height: `${Math.min(preview.length * 42 + 52, 300)}px`, width: '100%' }}>
              <AgGridReact rowData={preview} columnDefs={previewCols} defaultColDef={{ resizable: true }} />
            </div>
            <div className="flex gap-3 items-center">
              <Button onClick={() => executeMut.mutate()} disabled={executeMut.isPending}>
                {executeMut.isPending ? <><Spinner size={14} /> Executing…</> : 'Execute'}
              </Button>
              <Button size="sm" variant="secondary" onClick={() => setPreview(null)}><X size={13} /> Cancel</Button>
            </div>
          </div>
        )}

        {executeMsg && (
          <div className={`text-sm px-4 py-2 rounded-lg ${executeMsg.ok ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-600'}`}>
            {executeMsg.text}
          </div>
        )}
      </div>
    </div>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────
const TABS = ['Prices', 'Investment Transactions', 'Price Anomalies', 'Dividends', 'Corporate Actions'] as const
type Tab = typeof TABS[number]

export default function SecurityDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [tab, setTab] = useState<Tab>('Prices')

  const secId = Number(id)

  const { data: securitiesRaw = [] } = useQuery({
    queryKey: ['securities', ''],
    queryFn: () => getSecurities(),
  })
  const securities = securitiesRaw as Record<string, unknown>[]
  const security = securities.find(s => Number(s.id) === secId) ?? {} as Record<string, unknown>

  const priceCount = security.price_records != null ? Number(security.price_records) : null
  const priceDate = security.price_date ? String(security.price_date).slice(0, 10) : null
  const subtitle = [
    security.currency ? String(security.currency) : null,
    priceCount != null ? `${priceCount.toLocaleString()} prices` : null,
    priceDate ? `last: ${priceDate}` : null,
  ].filter(Boolean).join(' · ')

  return (
    <div>
      <PageHeader
        title=""
        actions={
          <Button size="sm" variant="secondary" onClick={() => navigate(-1)}>
            <ArrowLeft size={13} /> Back
          </Button>
        }
      />

      <div className="px-6 pb-6 space-y-4">
        {/* Security selector */}
        <div>
          <select
            className="w-full rounded-lg border border-slate-300 px-4 py-2.5 text-sm font-medium bg-white shadow-sm"
            value={secId || ''}
            onChange={e => { if (e.target.value) navigate(`/securities/${e.target.value}`) }}>
            <option value="">— Select a security —</option>
            {securities.map(s => (
              <option key={String(s.id)} value={String(s.id)}>
                {String(s.name)} ({String(s.currency ?? '?')})
                {s.price_records ? ` (${Number(s.price_records).toLocaleString()} prices · last: ${String(s.price_date ?? '').slice(0, 10)})` : ''}
              </option>
            ))}
          </select>
        </div>

        {!secId ? (
          <p className="text-sm text-slate-400 py-8 text-center">Select a security above to view its details.</p>
        ) : (
          <Card>
            {/* Sub-tabs */}
            <div className="border-b border-slate-200 px-4">
              <div className="flex gap-1">
                {TABS.map(t => (
                  <button key={t} onClick={() => setTab(t)}
                    className={`px-4 py-3 text-sm font-medium -mb-px border-b-2 transition-colors whitespace-nowrap ${tab === t ? 'border-blue-600 text-blue-600' : 'border-transparent text-slate-500 hover:text-slate-700'}`}>
                    {t}
                  </button>
                ))}
              </div>
            </div>

            <CardBody className="p-0">
              {tab === 'Prices' && <PricesTab secId={secId} />}
              {tab === 'Investment Transactions' && <InvestmentTransactionsTab secId={secId} security={security} />}
              {tab === 'Price Anomalies' && <PriceAnomaliesTab secId={secId} />}
              {tab === 'Dividends' && <DividendsTab secId={secId} security={security} />}
              {tab === 'Corporate Actions' && <CorporateActionsTab secId={secId} security={security} />}
            </CardBody>
          </Card>
        )}
      </div>
    </div>
  )
}
