import React, { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { AgGridReact } from 'ag-grid-react'
import type { ColDef, RowClickedEvent } from 'ag-grid-community'
import PlotlyReact from 'react-plotly.js'
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const Plot: React.ComponentType<any> = (PlotlyReact as any).default ?? PlotlyReact
import { getCurrencies, getSecurities, getPriceHistory, getFxRates, getPriceAnomalies, refreshPrices, refreshFx, addPrice, deletePrice, addFxRate, deleteFxRate, upsertSecurity, upsertCurrency, api, downloadYahooInfo, downloadYahooDividends, downloadYahooPrices, downloadTvInfo, downloadTvPrices, downloadSolidusBonds, getWatchlist, upsertWatchlistItem, deleteWatchlistItem, getAlertsDefinitions, saveAlert, toggleAlert, deleteAlert, importPricesFromFile } from '@/lib/api'
import { PageHeader, Input, Button, Spinner, Card, CardBody, ColHeader, useSortTable } from '@/components/ui'
import { Search, RefreshCw, Plus, Trash2, Pencil, Save, X } from 'lucide-react'

const SECURITY_TYPES = ['Stock', 'ETF', 'Bond', 'Mutual Fund', 'Crypto', 'Option', 'Commodity', 'PF_Unit', 'CD', 'Emp. Stock Opt.', 'FX Spot', 'Market Index', 'CFD', 'Closed-End Fund', 'Other']

// ── helpers ───────────────────────────────────────────────────────────────────
const extractError = (e: unknown) =>
  (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
  (e instanceof Error ? e.message : 'Operation failed')

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-xs font-medium text-slate-500 mb-1">{label}</label>
      {children}
    </div>
  )
}

function Modal({ title, onClose, children, footer, wide }: { title: string; onClose: () => void; children: React.ReactNode; footer: React.ReactNode; wide?: boolean }) {
  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
      <div className={`bg-white rounded-xl shadow-2xl w-full max-h-[92vh] overflow-y-auto ${wide ? 'max-w-3xl' : 'max-w-lg'}`}>
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200">
          <h2 className="text-base font-semibold">{title}</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600"><X size={18} /></button>
        </div>
        <div className="px-5 py-4 space-y-3">{children}</div>
        <div className="flex justify-end gap-2 px-5 py-3 border-t border-slate-200">{footer}</div>
      </div>
    </div>
  )
}

const TABS = ['Currencies', 'Securities', 'FX Prices', 'Securities Prices', 'Downloads', 'Anomalies', 'Watchlist', 'Alerts']

const SECURITY_COLS: ColDef[] = [
  { field: 'ticker', headerName: 'Ticker', width: 90, cellStyle: { fontFamily: 'monospace', fontWeight: 600 } },
  { field: 'name', headerName: 'Name', flex: 2, minWidth: 180 },
  { field: 'type', headerName: 'Type', width: 110 },
  { field: 'currency', headerName: 'Currency', width: 90 },
  { field: 'latest_price', headerName: 'Last Price', width: 110, type: 'numericColumn', valueFormatter: p => p.value != null ? Number(p.value).toLocaleString('el-GR', { minimumFractionDigits: 2 }) : '—' },
  { field: 'price_date', headerName: 'Price Date', width: 110, valueFormatter: p => p.value?.slice(0, 10) ?? '—' },
  { field: 'dividend_yield', headerName: 'Div Yield', width: 90, type: 'numericColumn', valueFormatter: p => p.value != null ? `${Number(p.value).toFixed(2)}%` : '—' },
  { field: 'price_records', headerName: '# Prices', width: 90, type: 'numericColumn' },
  { field: 'held_quantity', headerName: 'Held Qty', width: 90, type: 'numericColumn' },
]

const CURRENCY_COLS: ColDef[] = [
  { field: 'code', headerName: 'Code', width: 90 },
  { field: 'name', headerName: 'Currency', flex: 2 },
  { field: 'latest_rate', headerName: 'Rate vs EUR', width: 130, type: 'numericColumn', valueFormatter: p => p.value != null ? Number(p.value).toFixed(4) : '—' },
  { field: 'rate_date', headerName: 'Rate Date', width: 110, valueFormatter: p => p.value?.slice(0, 10) ?? '—' },
  { field: 'price_records', headerName: '# Records', width: 100, type: 'numericColumn' },
]

const ANOMALY_COLS: ColDef[] = [
  { field: 'security_name', headerName: 'Security', flex: 2 },
  { field: 'date', headerName: 'Date', width: 110 },
  { field: 'close', headerName: 'Close', width: 110, type: 'numericColumn', valueFormatter: p => Number(p.value).toFixed(4) },
  { field: 'prev_close', headerName: 'Prev', width: 110, type: 'numericColumn', valueFormatter: p => p.value != null ? Number(p.value).toFixed(4) : '—' },
  { field: 'next_close', headerName: 'Next', width: 110, type: 'numericColumn', valueFormatter: p => p.value != null ? Number(p.value).toFixed(4) : '—' },
  { field: 'ratio_prev', headerName: 'Ratio Prev', width: 110, type: 'numericColumn' },
  { field: 'ratio_next', headerName: 'Ratio Next', width: 110, type: 'numericColumn' },
]

const EMPTY_SECURITY = {
  ticker: '', name: '', type: 'Stock', currencies_id: '', is_active: 'true', is_tax_exempt: 'false',
  isin: '', sector: '', industry: '', yahoo_ticker: '', tv_symbol: '', tv_exchange: '',
  maturity_date: '', coupon_rate: '', coupon_frequency: '', face_value: '',
  dividend_yield: '', dividend_rate: '', dividend_frequency: '', ex_dividend_date: '',
  dividend_pay_date: '', payout_ratio: '', five_year_avg_yield: '',
  analyst_rating: '', analyst_target_price: '',
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return <p className="col-span-3 text-xs font-semibold text-slate-400 uppercase tracking-wide pt-2 border-t border-slate-100">{children}</p>
}

// ── Securities CRUD tab ───────────────────────────────────────────────────────
function SecuritiesTab({ search }: { search: string }) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [editRow, setEditRow] = useState<Record<string, unknown> | null>(null)
  const [form, setForm] = useState<Record<string, string>>(EMPTY_SECURITY)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)

  const { data: securities = [], isLoading } = useQuery({
    queryKey: ['securities', search],
    queryFn: () => getSecurities(search || undefined),
  })
  const { data: currencies = [] } = useQuery({ queryKey: ['currencies'], queryFn: getCurrencies })

  const set = (k: string, v: string) => setForm(f => ({ ...f, [k]: v }))

  const openNew = () => { setEditRow({}); setForm(EMPTY_SECURITY); setError(null) }

  const openEdit = (row: Record<string, unknown>) => {
    setEditRow(row)
    setForm({ ...EMPTY_SECURITY, ...Object.fromEntries(Object.entries(row).map(([k, v]) => [k, v != null ? String(v) : ''])) })
    setError(null)
  }

  const handleSave = async () => {
    setSaving(true); setError(null)
    try {
      await upsertSecurity({ ...form, id: editRow?.id ?? undefined, currencies_id: form.currencies_id ? Number(form.currencies_id) : null })
      qc.invalidateQueries({ queryKey: ['securities'] })
      setEditRow(null)
    } catch (e) { setError(extractError(e)) }
    finally { setSaving(false) }
  }

  const handleDelete = async (id: number) => {
    if (!confirm('Delete this security? This will also remove its price history.')) return
    setDeleteError(null)
    try {
      await api.delete(`/static-data/securities/${id}`)
      qc.invalidateQueries({ queryKey: ['securities'] })
    } catch (e) { setDeleteError(extractError(e)) }
  }

  const colDefs: ColDef[] = [
    { field: 'ticker', headerName: 'Ticker Symbol', width: 120, cellStyle: { fontFamily: 'monospace', fontWeight: 600 } },
    { field: 'name', headerName: 'Security Name', flex: 2, minWidth: 180,
      cellRenderer: (p: { value: string; data: Record<string, unknown> }) => (
        <button onClick={() => navigate(`/securities/${p.data.id}`)}
          className="text-blue-600 hover:underline text-left truncate w-full">{p.value}</button>
      ) },
    { field: 'is_active', headerName: 'Is Active', width: 90, cellRenderer: (p: {value: unknown}) => <input type="checkbox" readOnly checked={!!p.value} className="mt-2.5" /> },
    { field: 'is_tax_exempt', headerName: 'Tax Exempt', width: 100, cellRenderer: (p: {value: unknown}) => <input type="checkbox" readOnly checked={!!p.value} className="mt-2.5" /> },
    { field: 'yahoo_ticker', headerName: 'Yahoo Ticker', width: 110, cellStyle: { fontFamily: 'monospace' } },
    { field: 'tv_symbol', headerName: 'TV Symbol', width: 100 },
    { field: 'tv_exchange', headerName: 'TV Exchange', width: 110 },
    { field: 'isin', headerName: 'ISIN', width: 130 },
    { field: 'maturity_date', headerName: 'Maturity Date', width: 115, valueFormatter: p => p.value?.slice(0, 10) ?? '' },
    { field: 'coupon_rate', headerName: 'Coupon Rate', width: 110, type: 'numericColumn', valueFormatter: p => p.value != null ? `${Number(p.value).toFixed(2)}%` : '' },
    { field: 'face_value', headerName: 'Face Value', width: 100, type: 'numericColumn', valueFormatter: p => p.value != null ? Number(p.value).toLocaleString('el-GR', { minimumFractionDigits: 2 }) : '' },
    { field: 'type', headerName: 'Type', width: 110 },
    { field: 'currency', headerName: 'Ccy', width: 65 },
    { field: 'latest_price', headerName: 'Last Price', width: 110, type: 'numericColumn', valueFormatter: p => p.value != null ? Number(p.value).toLocaleString('el-GR', { minimumFractionDigits: 4 }) : '—' },
    { field: 'price_date', headerName: 'Price Date', width: 100, valueFormatter: p => p.value?.slice(0, 10) ?? '—' },
    { field: 'dividend_yield', headerName: 'Div Yield', width: 90, type: 'numericColumn', valueFormatter: p => p.value != null ? `${Number(p.value).toFixed(2)}%` : '' },
    { field: 'price_records', headerName: '# Prices', width: 80, type: 'numericColumn' },
    { field: 'held_quantity', headerName: 'Held Qty', width: 80, type: 'numericColumn' },
    {
      headerName: '', width: 70, sortable: false, filter: false, pinned: 'right',
      cellRenderer: (p: { data: Record<string, unknown> }) => (
        <div className="flex gap-1 items-center h-full">
          <button onClick={() => openEdit(p.data)} className="text-blue-500 hover:text-blue-700 p-1"><Pencil size={13} /></button>
          <button onClick={() => handleDelete(Number(p.data.id))} className="text-red-400 hover:text-red-600 p-1"><Trash2 size={13} /></button>
        </div>
      ),
    },
  ]

  const BoolField = ({ label, k }: { label: string; k: string }) => (
    <Field label={label}>
      <select className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm" value={form[k] ?? 'false'} onChange={e => set(k, e.target.value)}>
        <option value="true">Yes</option>
        <option value="false">No</option>
      </select>
    </Field>
  )

  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>

  return (
    <div>
      <div className="flex items-center gap-2 px-4 py-2 border-b border-slate-100 bg-slate-50">
        <Button size="sm" variant="secondary" onClick={openNew}><Plus size={13} /> Add Security</Button>
        {deleteError && <span className="text-xs text-red-600 bg-red-50 rounded px-3 py-1">{deleteError}</span>}
        <span className="ml-auto text-xs text-slate-400">{(securities as unknown[]).length} securities</span>
      </div>
      <div className="ag-theme-alpine" style={{ height: '560px', width: '100%' }}>
        <AgGridReact rowData={securities} columnDefs={colDefs}
          defaultColDef={{ resizable: true, sortable: true, filter: true }}
          onRowClicked={(e: RowClickedEvent) => { if ((e.event as MouseEvent)?.detail === 2) openEdit(e.data as Record<string, unknown>) }} />
      </div>

      {editRow !== null && (
        <Modal title={form.id ? `Edit Security — ${form.ticker}` : 'New Security'} wide onClose={() => setEditRow(null)}
          footer={<>
            {form.id && <Button variant="destructive" onClick={() => { setEditRow(null); handleDelete(Number(form.id)) }} disabled={saving}><Trash2 size={14} /> Delete</Button>}
            <span className="flex-1" />
            <Button variant="secondary" onClick={() => setEditRow(null)}>Cancel</Button>
            <Button onClick={handleSave} disabled={saving || !form.name?.trim() || !form.ticker?.trim()}>
              <Save size={14} /> {saving ? 'Saving…' : 'Save'}
            </Button>
          </>}>
          <div className="grid grid-cols-3 gap-3">
            {/* Identity */}
            <SectionLabel>Identity</SectionLabel>
            <Field label="Ticker *"><Input value={form.ticker} onChange={e => set('ticker', e.target.value)} placeholder="AAPL" className="font-mono" /></Field>
            <div className="col-span-2">
              <Field label="Name *"><Input value={form.name} onChange={e => set('name', e.target.value)} /></Field>
            </div>
            <Field label="Type *">
              <select className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm" value={form.type} onChange={e => set('type', e.target.value)}>
                {SECURITY_TYPES.map(t => <option key={t}>{t}</option>)}
              </select>
            </Field>
            <Field label="Currency">
              <select className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm" value={form.currencies_id} onChange={e => set('currencies_id', e.target.value)}>
                <option value="">— select —</option>
                {(currencies as Record<string,unknown>[]).map(c => <option key={String(c.id)} value={String(c.id)}>{String(c.code)} · {String(c.name)}</option>)}
              </select>
            </Field>
            <Field label="ISIN"><Input value={form.isin} onChange={e => set('isin', e.target.value)} placeholder="US0378331005" className="font-mono" /></Field>
            <BoolField label="Is Active" k="is_active" />
            <BoolField label="Tax Exempt" k="is_tax_exempt" />
            <Field label="Sector"><Input value={form.sector} onChange={e => set('sector', e.target.value)} /></Field>
            <Field label="Industry"><Input value={form.industry} onChange={e => set('industry', e.target.value)} /></Field>

            {/* Data sources */}
            <SectionLabel>Data Sources</SectionLabel>
            <Field label="Yahoo Ticker"><Input value={form.yahoo_ticker} onChange={e => set('yahoo_ticker', e.target.value)} placeholder="AAPL" className="font-mono" /></Field>
            <Field label="TV Symbol"><Input value={form.tv_symbol} onChange={e => set('tv_symbol', e.target.value)} placeholder="AAPL" className="font-mono" /></Field>
            <Field label="TV Exchange"><Input value={form.tv_exchange} onChange={e => set('tv_exchange', e.target.value)} placeholder="NASDAQ" /></Field>

            {/* Fixed income */}
            <SectionLabel>Fixed Income</SectionLabel>
            <Field label="Maturity Date"><Input type="date" value={form.maturity_date} onChange={e => set('maturity_date', e.target.value)} /></Field>
            <Field label="Coupon Rate %"><Input type="number" step="0.001" value={form.coupon_rate} onChange={e => set('coupon_rate', e.target.value)} placeholder="0.000" /></Field>
            <Field label="Coupon Frequency"><Input value={form.coupon_frequency} onChange={e => set('coupon_frequency', e.target.value)} placeholder="Annual" /></Field>
            <Field label="Face Value"><Input type="number" step="0.01" value={form.face_value} onChange={e => set('face_value', e.target.value)} placeholder="1000.00" /></Field>

            {/* Dividends */}
            <SectionLabel>Dividends</SectionLabel>
            <Field label="Dividend Yield %"><Input type="number" step="0.0001" value={form.dividend_yield} onChange={e => set('dividend_yield', e.target.value)} placeholder="0.0000" /></Field>
            <Field label="Dividend Rate"><Input type="number" step="0.0001" value={form.dividend_rate} onChange={e => set('dividend_rate', e.target.value)} placeholder="0.0000" /></Field>
            <Field label="Dividend Frequency"><Input value={form.dividend_frequency} onChange={e => set('dividend_frequency', e.target.value)} placeholder="Quarterly" /></Field>
            <Field label="Ex-Dividend Date"><Input type="date" value={form.ex_dividend_date} onChange={e => set('ex_dividend_date', e.target.value)} /></Field>
            <Field label="Dividend Pay Date"><Input type="date" value={form.dividend_pay_date} onChange={e => set('dividend_pay_date', e.target.value)} /></Field>
            <Field label="Payout Ratio %"><Input type="number" step="0.01" value={form.payout_ratio} onChange={e => set('payout_ratio', e.target.value)} placeholder="0.00" /></Field>
            <Field label="5Y Avg Yield %"><Input type="number" step="0.0001" value={form.five_year_avg_yield} onChange={e => set('five_year_avg_yield', e.target.value)} placeholder="0.0000" /></Field>

            {/* Analyst */}
            <SectionLabel>Analyst</SectionLabel>
            <Field label="Rating"><Input value={form.analyst_rating} onChange={e => set('analyst_rating', e.target.value)} placeholder="Buy" /></Field>
            <Field label="Target Price"><Input type="number" step="0.01" value={form.analyst_target_price} onChange={e => set('analyst_target_price', e.target.value)} placeholder="0.00" /></Field>
          </div>
          {error && <p className="text-xs text-red-600 bg-red-50 rounded px-3 py-2 mt-2">{error}</p>}
        </Modal>
      )}
    </div>
  )
}

// ── Currencies CRUD tab ───────────────────────────────────────────────────────
function CurrenciesTab({ search }: { search: string }) {
  const qc = useQueryClient()
  const [editRow, setEditRow] = useState<Record<string, unknown> | null>(null)
  const [form, setForm] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)

  const { data: currencies = [], isLoading } = useQuery({
    queryKey: ['currencies'],
    queryFn: getCurrencies,
  })

  const filtered = search
    ? (currencies as Record<string,unknown>[]).filter(r =>
        String(r.code ?? '').toLowerCase().includes(search.toLowerCase()) ||
        String(r.name ?? '').toLowerCase().includes(search.toLowerCase()))
    : currencies as Record<string,unknown>[]

  const set = (k: string, v: string) => setForm(f => ({ ...f, [k]: v }))

  const openNew = () => {
    setEditRow({})
    setForm({ code: '', name: '' })
    setError(null)
  }

  const openEdit = (row: Record<string, unknown>) => {
    setEditRow(row)
    setForm(Object.fromEntries(Object.entries(row).map(([k, v]) => [k, v != null ? String(v) : ''])))
    setError(null)
  }

  const handleSave = async () => {
    setSaving(true); setError(null)
    try {
      await upsertCurrency({ id: editRow?.id ?? undefined, code: form.code, name: form.name })
      qc.invalidateQueries({ queryKey: ['currencies'] })
      setEditRow(null)
    } catch (e) { setError(extractError(e)) }
    finally { setSaving(false) }
  }

  const handleDelete = async (id: number) => {
    if (!confirm('Delete this currency? This will also remove its FX rate history.')) return
    setDeleteError(null)
    try {
      await api.delete(`/static-data/currencies/${id}`)
      qc.invalidateQueries({ queryKey: ['currencies'] })
    } catch (e) { setDeleteError(extractError(e)) }
  }

  const colDefs: ColDef[] = [
    { field: 'code', headerName: 'Code', width: 90, cellStyle: { fontFamily: 'monospace', fontWeight: 600 } },
    { field: 'name', headerName: 'Currency', flex: 2 },
    { field: 'latest_rate', headerName: 'Rate vs EUR', width: 130, type: 'numericColumn', valueFormatter: p => p.value != null ? Number(p.value).toFixed(4) : '—' },
    { field: 'rate_date', headerName: 'Rate Date', width: 110, valueFormatter: p => p.value?.slice(0, 10) ?? '—' },
    { field: 'price_records', headerName: '# Records', width: 100, type: 'numericColumn' },
    {
      headerName: '', width: 80, sortable: false, filter: false,
      cellRenderer: (p: { data: Record<string, unknown> }) => (
        <div className="flex gap-1 items-center h-full">
          <button onClick={() => openEdit(p.data)} className="text-blue-500 hover:text-blue-700 p-1"><Pencil size={13} /></button>
          <button onClick={() => handleDelete(Number(p.data.id))} className="text-red-400 hover:text-red-600 p-1"><Trash2 size={13} /></button>
        </div>
      ),
    },
  ]

  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>

  return (
    <div>
      <div className="flex items-center gap-2 px-4 py-2 border-b border-slate-100 bg-slate-50">
        <Button size="sm" variant="secondary" onClick={openNew}><Plus size={13} /> Add Currency</Button>
        {deleteError && <span className="text-xs text-red-600 bg-red-50 rounded px-3 py-1">{deleteError}</span>}
        <span className="ml-auto text-xs text-slate-400">{filtered.length} currencies</span>
      </div>
      <div className="ag-theme-alpine" style={{ height: '420px', width: '100%' }}>
        <AgGridReact rowData={filtered} columnDefs={colDefs}
          defaultColDef={{ resizable: true, sortable: true, filter: true }}
          onRowClicked={(e: RowClickedEvent) => { if ((e.event as MouseEvent)?.detail === 2) openEdit(e.data as Record<string, unknown>) }} />
      </div>

      {editRow !== null && (
        <Modal title={form.id ? 'Edit Currency' : 'New Currency'} onClose={() => setEditRow(null)}
          footer={<>
            {form.id && <Button variant="destructive" onClick={() => { setEditRow(null); handleDelete(Number(form.id)) }} disabled={saving}><Trash2 size={14} /> Delete</Button>}
            <span className="flex-1" />
            <Button variant="secondary" onClick={() => setEditRow(null)}>Cancel</Button>
            <Button onClick={handleSave} disabled={saving || !form.code?.trim() || !form.name?.trim()}>
              <Save size={14} /> {saving ? 'Saving…' : 'Save'}
            </Button>
          </>}>
          <Field label="Code *"><Input value={form.code ?? ''} onChange={e => set('code', e.target.value)} placeholder="USD" className="font-mono" /></Field>
          <Field label="Name *"><Input value={form.name ?? ''} onChange={e => set('name', e.target.value)} placeholder="US Dollar" /></Field>
          {error && <p className="text-xs text-red-600 bg-red-50 rounded px-3 py-2">{error}</p>}
        </Modal>
      )}
    </div>
  )
}

// ── Shared period helper ──────────────────────────────────────────────────────
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

// ── FX Prices tab: history chart + manual entry ───────────────────────────────
function FxPricesTab() {
  const qc = useQueryClient()
  const [curId, setCurId] = useState<number | null>(null)
  const [period, setPeriod] = useState<ChartPeriod>('All')
  const fromDate = periodToFromDate(period)
  const [fxSearch, setFxSearch] = useState('')
  const [action, setAction] = useState<'save' | 'delete'>('save')
  const [entryDate, setEntryDate] = useState(new Date().toISOString().slice(0, 10))
  const [entryValue, setEntryValue] = useState('')
  const [msg, setMsg] = useState<string | null>(null)

  const { data: currencies = [] } = useQuery({ queryKey: ['currencies'], queryFn: getCurrencies })
  const { data: history = [], isLoading } = useQuery({
    queryKey: ['fx-history', curId, fromDate],
    queryFn: () => getFxRates(curId!, fromDate),
    enabled: !!curId,
  })

  const addFxMut = useMutation({
    mutationFn: addFxRate,
    onSuccess: () => { setMsg('FX rate saved.'); qc.invalidateQueries({ queryKey: ['fx-history'] }); qc.invalidateQueries({ queryKey: ['currencies'] }); setEntryValue('') },
    onError: (e: Error) => setMsg(`Error: ${e.message}`),
  })
  const delFxMut = useMutation({
    mutationFn: ({ cid, d }: { cid: number; d: string }) => deleteFxRate(cid, d),
    onSuccess: () => { setMsg('FX rate deleted.'); qc.invalidateQueries({ queryKey: ['fx-history'] }) },
    onError: (e: Error) => setMsg(`Error: ${e.message}`),
  })

  const handleSubmit = () => {
    if (!curId || !entryDate) return
    setMsg(null)
    if (action === 'delete') delFxMut.mutate({ cid: curId, d: entryDate })
    else { if (!entryValue) return; addFxMut.mutate({ currency_id: curId, date: entryDate, rate: Number(entryValue) }) }
  }

  const isPending = addFxMut.isPending || delFxMut.isPending

  return (
    <div className="p-4 space-y-5">
      <div className="flex flex-wrap gap-3 items-end">
        <div>
          <label className="text-xs font-medium text-slate-500 block mb-1">Currency</label>
          <select className="w-64 rounded-md border border-slate-300 px-3 py-1.5 text-sm"
            value={curId ?? ''} onChange={e => { setCurId(Number(e.target.value) || null); setMsg(null) }}>
            <option value="">— Select currency —</option>
            {(currencies as Record<string,unknown>[]).map(c => (
              <option key={String(c.id)} value={String(c.id)}>{String(c.code)} · {String(c.name)}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="text-xs font-medium text-slate-500 block mb-1">Period</label>
          <div className="flex items-center gap-3">
            <PeriodSelector value={period} onChange={setPeriod} />
            {(() => {
              const h = history as Record<string,unknown>[]
              if (h.length < 2) return null
              const first = Number(h[0].rate), last = Number(h[h.length - 1].rate)
              if (!first) return null
              const pct = ((last - first) / first) * 100
              return <span className={`text-sm font-semibold tabular-nums ${pct >= 0 ? 'text-emerald-600' : 'text-red-500'}`}>{pct >= 0 ? '+' : ''}{pct.toFixed(2)}%</span>
            })()}
          </div>
        </div>
      </div>

      {!curId ? (
        <p className="text-sm text-slate-400">Select a currency to view FX rate history</p>
      ) : isLoading ? (
        <div className="flex justify-center py-12"><Spinner /></div>
      ) : (
        <div className="space-y-4">
          <Plot
            data={[{
              x: (history as Record<string,unknown>[]).map(r => r.date),
              y: (history as Record<string,unknown>[]).map(r => r.rate),
              type: 'scatter', mode: 'lines',
              line: { color: '#10b981', width: 1.5 },
              name: 'FX Rate vs EUR',
            }]}
            layout={{ height: 320, margin: { t: 10, r: 10, b: 40, l: 70 }, plot_bgcolor: 'white', paper_bgcolor: 'white', yaxis: { tickformat: '.4f' }, hovermode: 'x unified' }}
            config={{ displayModeBar: true, responsive: true }}
            style={{ width: '100%' }}
          />
          <div className="flex items-center gap-2 mb-1">
            <Search size={14} className="text-slate-400" />
            <Input className="w-56 h-7 text-xs" placeholder="Search…" value={fxSearch} onChange={e => setFxSearch(e.target.value)} />
          </div>
          <div className="ag-theme-alpine" style={{ height: '360px', width: '100%' }}>
            <AgGridReact
              rowData={[...(history as Record<string,unknown>[])].reverse()}
              quickFilterText={fxSearch}
              columnDefs={[
                { field: 'date', headerName: 'Date', width: 130, sort: 'desc' },
                { field: 'rate', headerName: 'Rate vs EUR', flex: 1, valueFormatter: (p: {value: unknown}) => p.value != null ? Number(p.value).toFixed(6) : '' },
              ]}
              defaultColDef={{ resizable: true, sortable: true, filter: true }}
            />
          </div>
        </div>
      )}

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
              <label className="text-xs font-medium text-slate-500 block mb-1">Rate vs EUR</label>
              <Input type="number" step="any" className="w-32" value={entryValue} onChange={e => setEntryValue(e.target.value)} placeholder="0.0000" />
            </div>
          )}
          <Button onClick={handleSubmit} disabled={isPending || !curId} variant={action === 'delete' ? 'destructive' : 'primary'}>
            {action === 'delete' ? <><Trash2 size={14} /> Delete</> : <><Plus size={14} /> Save</>}
          </Button>
          {msg && <span className={`text-xs px-3 py-1.5 rounded ${msg.startsWith('Error') ? 'bg-red-50 text-red-600' : 'bg-green-50 text-green-700'}`}>{msg}</span>}
        </div>
      </div>
    </div>
  )
}

// ── Securities Prices tab: history chart + manual entry ───────────────────────
function SecuritiesPricesTab() {
  const qc = useQueryClient()
  const [secId, setSecId] = useState<number | null>(null)
  const [period, setPeriod] = useState<ChartPeriod>('All')
  const fromDate = periodToFromDate(period)
  const [priceSearch, setPriceSearch] = useState('')
  const [action, setAction] = useState<'save' | 'delete'>('save')
  const [entryDate, setEntryDate] = useState(new Date().toISOString().slice(0, 10))
  const [entryValue, setEntryValue] = useState('')
  const [msg, setMsg] = useState<string | null>(null)
  const [importFile, setImportFile] = useState<File | null>(null)
  const [importConflict, setImportConflict] = useState<'skip' | 'overwrite'>('skip')
  const [importMsg, setImportMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const importMut = useMutation({
    mutationFn: () => importPricesFromFile(importFile!, secId!, importConflict),
    onSuccess: (d) => {
      setImportMsg({ ok: true, text: `Imported ${d.inserted} row(s) — ${d.skipped} skipped (${d.total_rows} total in file).` })
      qc.invalidateQueries({ queryKey: ['price-history'] })
    },
    onError: (e: { response?: { data?: { detail?: unknown } } }) => {
      const d = e.response?.data?.detail
      const text = Array.isArray(d) ? (d as { msg?: string }[]).map(x => x.msg ?? String(x)).join('; ') : (typeof d === 'string' ? d : 'Import failed')
      setImportMsg({ ok: false, text })
    },
  })

  const { data: securities = [] } = useQuery({ queryKey: ['securities', ''], queryFn: () => getSecurities() })
  const { data: history = [], isLoading } = useQuery({
    queryKey: ['price-history', secId, fromDate],
    queryFn: () => getPriceHistory(secId!, fromDate),
    enabled: !!secId,
  })

  const addPriceMut = useMutation({
    mutationFn: addPrice,
    onSuccess: () => { setMsg('Price saved.'); qc.invalidateQueries({ queryKey: ['price-history'] }); qc.invalidateQueries({ queryKey: ['securities'] }); setEntryValue('') },
    onError: (e: Error) => setMsg(`Error: ${e.message}`),
  })
  const delPriceMut = useMutation({
    mutationFn: ({ sid, d }: { sid: number; d: string }) => deletePrice(sid, d),
    onSuccess: () => { setMsg('Price deleted.'); qc.invalidateQueries({ queryKey: ['price-history'] }) },
    onError: (e: Error) => setMsg(`Error: ${e.message}`),
  })

  const handleSubmit = () => {
    if (!secId || !entryDate) return
    setMsg(null)
    if (action === 'delete') delPriceMut.mutate({ sid: secId, d: entryDate })
    else { if (!entryValue) return; addPriceMut.mutate({ security_id: secId, date: entryDate, close: Number(entryValue) }) }
  }

  const isPending = addPriceMut.isPending || delPriceMut.isPending

  return (
    <div className="p-4 space-y-5">
      <div className="flex flex-wrap gap-3 items-end">
        <div>
          <label className="text-xs font-medium text-slate-500 block mb-1">Security</label>
          <select className="w-72 rounded-md border border-slate-300 px-3 py-1.5 text-sm"
            value={secId ?? ''} onChange={e => { setSecId(Number(e.target.value) || null); setMsg(null) }}>
            <option value="">— Select security —</option>
            {(securities as Record<string,unknown>[]).map(s => (
              <option key={String(s.id)} value={String(s.id)}>{String(s.ticker || '')} · {String(s.name)}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="text-xs font-medium text-slate-500 block mb-1">Period</label>
          <div className="flex items-center gap-3">
            <PeriodSelector value={period} onChange={setPeriod} />
            {(() => {
              const h = history as Record<string,unknown>[]
              if (h.length < 2) return null
              const first = Number(h[0].close), last = Number(h[h.length - 1].close)
              if (!first) return null
              const pct = ((last - first) / first) * 100
              return <span className={`text-sm font-semibold tabular-nums ${pct >= 0 ? 'text-emerald-600' : 'text-red-500'}`}>{pct >= 0 ? '+' : ''}{pct.toFixed(2)}%</span>
            })()}
          </div>
        </div>
      </div>

      {!secId ? (
        <p className="text-sm text-slate-400">Select a security to view price history</p>
      ) : isLoading ? (
        <div className="flex justify-center py-12"><Spinner /></div>
      ) : (
        <div className="space-y-4">
          <Plot
            data={[{
              x: (history as Record<string,unknown>[]).map(r => r.date),
              y: (history as Record<string,unknown>[]).map(r => r.close),
              type: 'scatter', mode: 'lines',
              line: { color: '#3b82f6', width: 1.5 },
            }]}
            layout={{ height: 320, margin: { t: 10, r: 10, b: 40, l: 70 }, plot_bgcolor: 'white', paper_bgcolor: 'white', yaxis: { tickformat: '.4f' }, hovermode: 'x unified' }}
            config={{ displayModeBar: true, responsive: true }}
            style={{ width: '100%' }}
          />
          <div className="flex items-center gap-2 mb-1">
            <Search size={14} className="text-slate-400" />
            <Input className="w-56 h-7 text-xs" placeholder="Search…" value={priceSearch} onChange={e => setPriceSearch(e.target.value)} />
          </div>
          <div className="ag-theme-alpine" style={{ height: '360px', width: '100%' }}>
            <AgGridReact
              rowData={[...(history as Record<string,unknown>[])].reverse()}
              quickFilterText={priceSearch}
              columnDefs={[
                { field: 'date', headerName: 'Date', width: 130, sort: 'desc' },
                { field: 'close', headerName: 'Close Price', width: 130, valueFormatter: (p: {value: unknown}) => p.value != null ? Number(p.value).toFixed(6) : '' },
                { field: 'source', headerName: 'Source', width: 120 },
                { field: 'downloaded_at', headerName: 'Downloaded At', flex: 1 },
              ]}
              defaultColDef={{ resizable: true, sortable: true, filter: true }}
            />
          </div>
        </div>
      )}

      <div className="border-t border-slate-200 pt-4">
        <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-3">Import from File</p>
        <p className="text-xs text-slate-500 mb-3">
          Upload a tab-separated <code className="bg-slate-100 px-1 rounded">.txt</code> / <code className="bg-slate-100 px-1 rounded">.csv</code> / <code className="bg-slate-100 px-1 rounded">.tsv</code> file.
          The importer finds the <code className="bg-slate-100 px-1 rounded">Date</code> header row automatically. Select a security above first.
        </p>
        <div className="flex flex-wrap gap-4 items-end">
          <div>
            <label className="text-xs font-medium text-slate-500 block mb-1">File</label>
            <label className="cursor-pointer flex items-center gap-2">
              <span className="px-3 py-1.5 bg-slate-800 text-white text-xs rounded hover:bg-slate-700 transition-colors">⬆ Choose file</span>
              <span className="text-xs text-slate-500">{importFile ? importFile.name : 'TXT, CSV, TSV'}</span>
              <input type="file" accept=".txt,.csv,.tsv" className="hidden"
                onChange={e => { setImportFile(e.target.files?.[0] ?? null); setImportMsg(null) }} />
            </label>
          </div>
          <div>
            <label className="text-xs font-medium text-slate-500 block mb-1">If date exists</label>
            <div className="flex gap-3">
              {(['skip', 'overwrite'] as const).map(v => (
                <label key={v} className="flex items-center gap-1.5 text-xs cursor-pointer">
                  <input type="radio" name="importConflict" value={v} checked={importConflict === v} onChange={() => setImportConflict(v)} />
                  {v === 'skip' ? 'Skip' : 'Overwrite'}
                </label>
              ))}
            </div>
          </div>
          <Button variant="primary" disabled={!importFile || !secId || importMut.isPending}
            onClick={() => { setImportMsg(null); importMut.mutate() }}>
            {importMut.isPending ? <><Spinner size={12} /> Importing…</> : '📂 Import'}
          </Button>
          {importMsg && (
            <span className={`text-xs px-3 py-1.5 rounded ${importMsg.ok ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-600'}`}>
              {importMsg.text}
            </span>
          )}
        </div>
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
          <Button onClick={handleSubmit} disabled={isPending || !secId} variant={action === 'delete' ? 'destructive' : 'primary'}>
            {action === 'delete' ? <><Trash2 size={14} /> Delete</> : <><Plus size={14} /> Save</>}
          </Button>
          {msg && <span className={`text-xs px-3 py-1.5 rounded ${msg.startsWith('Error') ? 'bg-red-50 text-red-600' : 'bg-green-50 text-green-700'}`}>{msg}</span>}
        </div>
      </div>
    </div>
  )
}

// ── Downloads tab ─────────────────────────────────────────────────────────────
const PERIODS = ['1d', '5d', '1mo', '3mo', '6mo', '1y', '2y', '5y', 'max']

function DownloadsTab() {
  const qc = useQueryClient()
  const { data: securities = [] } = useQuery({ queryKey: ['securities', ''], queryFn: () => getSecurities() })
  const { data: currencies = [] } = useQuery({ queryKey: ['currencies'], queryFn: getCurrencies })

  const [period, setPeriod] = useState('1mo')
  const [secId, setSecId] = useState('')
  const [overwrite, setOverwrite] = useState(false)
  const [fxPeriod, setFxPeriod] = useState('1mo')
  const [fxCurrencyId, setFxCurrencyId] = useState('')
  const [status, setStatus] = useState<Record<string, 'idle' | 'running' | 'ok' | 'error'>>({})
  const [messages, setMessages] = useState<Record<string, string>>({})

  const run = async (key: string, fn: () => Promise<unknown>) => {
    setStatus(s => ({ ...s, [key]: 'running' }))
    setMessages(m => ({ ...m, [key]: '' }))
    try {
      const res = await fn() as { message?: string }
      setStatus(s => ({ ...s, [key]: 'ok' }))
      setMessages(m => ({ ...m, [key]: res?.message ?? 'Done' }))
      qc.invalidateQueries({ queryKey: ['securities'] })
      qc.invalidateQueries({ queryKey: ['price-history'] })
      qc.invalidateQueries({ queryKey: ['currencies'] })
      qc.invalidateQueries({ queryKey: ['fx-history'] })
    } catch (e) {
      setStatus(s => ({ ...s, [key]: 'error' }))
      setMessages(m => ({ ...m, [key]: extractError(e) }))
    }
  }

  const sid = secId ? Number(secId) : undefined

  const statusIcon = (key: string) => {
    const s = status[key]
    if (s === 'running') return <span className="inline-block w-4 h-4 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
    if (s === 'ok') return <span className="text-green-600 font-bold">✓</span>
    if (s === 'error') return <span className="text-red-500 font-bold">✗</span>
    return null
  }

  const ActionRow = ({ id, label, onClick }: { id: string; label: string; onClick: () => void }) => (
    <div className="flex items-center gap-3 py-2 border-b border-slate-100 last:border-0">
      <Button size="sm" variant="secondary" onClick={onClick} disabled={status[id] === 'running'}>
        {status[id] === 'running' ? <><span className="inline-block w-3 h-3 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" /> Running…</> : label}
      </Button>
      <span className="flex items-center gap-1.5 text-xs">
        {statusIcon(id)}
        {messages[id] && <span className={status[id] === 'error' ? 'text-red-600' : 'text-slate-500'}>{messages[id]}</span>}
      </span>
    </div>
  )

  return (
    <div className="p-5 space-y-6 max-w-2xl">

      {/* Common controls */}
      <div className="flex flex-wrap gap-4 items-end p-4 bg-slate-50 rounded-lg border border-slate-200">
        <div>
          <label className="text-xs font-medium text-slate-500 block mb-1">Period (for price downloads)</label>
          <select className="rounded-md border border-slate-300 px-3 py-1.5 text-sm" value={period} onChange={e => setPeriod(e.target.value)}>
            {PERIODS.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
        </div>
        <div>
          <label className="text-xs font-medium text-slate-500 block mb-1">Single security (optional)</label>
          <select className="w-64 rounded-md border border-slate-300 px-3 py-1.5 text-sm" value={secId} onChange={e => setSecId(e.target.value)}>
            <option value="">— All securities —</option>
            {(securities as Record<string,unknown>[]).map(s => (
              <option key={String(s.id)} value={String(s.id)}>{String(s.ticker || '')} · {String(s.name)}</option>
            ))}
          </select>
        </div>
        <div className="flex items-center gap-2">
          <input type="checkbox" id="overwrite" checked={overwrite} onChange={e => setOverwrite(e.target.checked)} className="w-4 h-4" />
          <label htmlFor="overwrite" className="text-sm text-slate-600">Overwrite existing TV data</label>
        </div>
      </div>

      {/* Yahoo Finance */}
      <div>
        <p className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-2">Yahoo Finance</p>
        <div className="rounded-lg border border-slate-200 bg-white divide-y divide-slate-100 px-4">
          <ActionRow id="yahoo-info"  label="Update Securities Info"   onClick={() => run('yahoo-info',  () => downloadYahooInfo(sid))} />
          <ActionRow id="yahoo-divs"  label="Download Dividend History" onClick={() => run('yahoo-divs',  () => downloadYahooDividends(sid))} />
          <ActionRow id="yahoo-px"    label={`Download Prices (${period})`} onClick={() => run('yahoo-px', () => downloadYahooPrices(period, sid))} />
        </div>
      </div>

      {/* TradingView */}
      <div>
        <p className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-2">TradingView</p>
        <div className="rounded-lg border border-slate-200 bg-white divide-y divide-slate-100 px-4">
          <ActionRow id="tv-info" label={`Update Securities Info${overwrite ? ' (overwrite)' : ''}`} onClick={() => run('tv-info', () => downloadTvInfo(sid, overwrite))} />
          <ActionRow id="tv-px"   label={`Download Prices (${period})`} onClick={() => run('tv-px', () => downloadTvPrices(period, sid))} />
        </div>
      </div>

      {/* FX */}
      <div>
        <p className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-2">FX Rates</p>
        <div className="rounded-lg border border-slate-200 bg-white px-4">
          <div className="flex flex-wrap items-end gap-3 py-3">
            <Button size="sm" variant="secondary"
              onClick={() => run('fx', () => refreshFx(fxPeriod, fxCurrencyId ? Number(fxCurrencyId) : undefined))}
              disabled={status['fx'] === 'running'}>
              {status['fx'] === 'running'
                ? <><span className="inline-block w-3 h-3 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" /> Running…</>
                : 'Refresh FX Rates from Yahoo'}
            </Button>
            <div>
              <label className="text-xs font-medium text-slate-500 block mb-1">Period</label>
              <select className="rounded-md border border-slate-300 px-2 py-1 text-sm" value={fxPeriod} onChange={e => setFxPeriod(e.target.value)}>
                {PERIODS.map(p => <option key={p} value={p}>{p}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs font-medium text-slate-500 block mb-1">Currency (optional)</label>
              <select className="w-48 rounded-md border border-slate-300 px-2 py-1 text-sm" value={fxCurrencyId} onChange={e => setFxCurrencyId(e.target.value)}>
                <option value="">— All currencies —</option>
                {(currencies as Record<string,unknown>[]).map(c => (
                  <option key={String(c.id)} value={String(c.id)}>{String(c.code)} · {String(c.name)}</option>
                ))}
              </select>
            </div>
            <span className="flex items-center gap-1.5 text-xs self-end pb-0.5">
              {status['fx'] === 'running' && <span className="inline-block w-4 h-4 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />}
              {status['fx'] === 'ok' && <span className="text-green-600 font-bold">✓</span>}
              {status['fx'] === 'error' && <span className="text-red-500 font-bold">✗</span>}
              {messages['fx'] && <span className={status['fx'] === 'error' ? 'text-red-600' : 'text-slate-500'}>{messages['fx']}</span>}
            </span>
          </div>
        </div>
      </div>

      {/* Solidus */}
      <div>
        <p className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-2">Greek Bonds</p>
        <div className="rounded-lg border border-slate-200 bg-white divide-y divide-slate-100 px-4">
          <ActionRow id="solidus" label="Download Bond Prices from Solidus PDF" onClick={() => run('solidus', downloadSolidusBonds)} />
        </div>
      </div>

    </div>
  )
}

// ── Watchlist Tab ─────────────────────────────────────────────────────────────
function WatchlistTab() {
  const qc = useQueryClient()
  const { data = [], isLoading } = useQuery({ queryKey: ['watchlist'], queryFn: getWatchlist })
  const { data: securities = [] } = useQuery({ queryKey: ['securities'], queryFn: () => getSecurities() })
  const rows = data as Record<string, unknown>[]
  const secs = securities as Record<string, unknown>[]

  const [showAdd, setShowAdd] = useState(false)
  const [form, setForm] = useState<Record<string, string>>({ securities_id: '', target_price: '', stop_loss: '', note: '' })
  const [editId, setEditId] = useState<number | null>(null)
  const [err, setErr] = useState('')

  const { sorted, sortKey, sortDir, toggleSort } = useSortTable(rows, 'securities_name', 'asc')

  const watchedIds = new Set(rows.map(r => Number(r.securities_id)))
  const availableSecs = secs.filter(s => !watchedIds.has(Number(s.id)) || Number(s.id) === Number(form.securities_id))

  const upsertMut = useMutation({
    mutationFn: upsertWatchlistItem,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['watchlist'] }); setShowAdd(false); setEditId(null); setErr('') },
    onError: (e) => setErr(extractError(e)),
  })
  const deleteMut = useMutation({
    mutationFn: deleteWatchlistItem,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['watchlist'] }),
  })

  const openAdd = () => { setForm({ securities_id: '', target_price: '', stop_loss: '', note: '' }); setEditId(null); setShowAdd(true) }
  const openEdit = (row: Record<string, unknown>) => {
    setForm({
      securities_id: String(row.securities_id ?? ''),
      target_price: String(row.target_price ?? ''),
      stop_loss: String(row.stop_loss ?? ''),
      note: String(row.note ?? ''),
    })
    setEditId(Number(row.watchlist_id))
    setShowAdd(true)
  }

  const save = () => {
    if (!form.securities_id) return setErr('Security is required')
    upsertMut.mutate({
      securities_id: Number(form.securities_id),
      target_price: form.target_price ? Number(form.target_price) : null,
      stop_loss: form.stop_loss ? Number(form.stop_loss) : null,
      note: form.note || null,
    })
  }

  const fmtPct = (v: unknown) => {
    if (v == null) return '—'
    const n = Number(v)
    return <span className={n >= 0 ? 'text-green-700' : 'text-red-600'}>{n >= 0 ? '+' : ''}{n.toFixed(2)}%</span>
  }

  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>

  return (
    <div className="p-4 space-y-4">
      <div className="flex justify-between items-center">
        <p className="text-sm text-slate-500">{rows.length} securities on watchlist</p>
        <Button size="sm" onClick={openAdd}><Plus size={14} /> Add to Watchlist</Button>
      </div>

      {showAdd && (
        <Modal title={editId ? 'Edit Watchlist Item' : 'Add to Watchlist'} onClose={() => { setShowAdd(false); setErr('') }}
          footer={<><Button variant="secondary" onClick={() => { setShowAdd(false); setErr('') }}>Cancel</Button><Button onClick={save} disabled={upsertMut.isPending}>Save</Button></>}>
          {err && <p className="text-xs text-red-600">{err}</p>}
          <Field label="Security *">
            <select className="block w-full rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm"
              value={form.securities_id} onChange={e => setForm(f => ({ ...f, securities_id: e.target.value }))}>
              <option value="">— select —</option>
              {availableSecs.map(s => <option key={String(s.id)} value={String(s.id)}>{String(s.name)} ({String(s.ticker ?? '')})</option>)}
            </select>
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Target Price"><Input type="number" step="any" value={form.target_price} onChange={e => setForm(f => ({ ...f, target_price: e.target.value }))} placeholder="optional" /></Field>
            <Field label="Stop Loss"><Input type="number" step="any" value={form.stop_loss} onChange={e => setForm(f => ({ ...f, stop_loss: e.target.value }))} placeholder="optional" /></Field>
          </div>
          <Field label="Note"><Input value={form.note} onChange={e => setForm(f => ({ ...f, note: e.target.value }))} placeholder="optional" /></Field>
        </Modal>
      )}

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead><tr className="bg-slate-50 text-xs text-slate-500 border-b border-slate-200">
            <ColHeader label="Security" sortKey="securities_name" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} />
            <ColHeader label="Type" sortKey="securities_type" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} />
            <ColHeader label="Curr." sortKey="currency" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} />
            <ColHeader label="Price" sortKey="current_price" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} align="right" />
            <ColHeader label="Target" sortKey="target_price" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} align="right" />
            <ColHeader label="Stop Loss" sortKey="stop_loss" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} align="right" />
            <ColHeader label="vs Target" sortKey="pct_from_target" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} align="right" />
            <ColHeader label="vs Stop" sortKey="pct_from_stop" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} align="right" />
            <ColHeader label="Analyst Upside" sortKey="upside_to_analyst" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} align="right" />
            <ColHeader label="Div Yield" sortKey="dividend_yield" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} align="right" />
            <ColHeader label="Added" sortKey="added_date" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} />
            <th className="px-3 py-2"></th>
          </tr></thead>
          <tbody className="divide-y divide-slate-100">
            {sorted.map(row => (
              <tr key={String(row.watchlist_id)} className="hover:bg-slate-50">
                <td className="px-3 py-2 font-medium">
                  {String(row.securities_name)}
                  {row.already_held && <span className="ml-1.5 text-[10px] bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded-full font-medium">held</span>}
                </td>
                <td className="px-3 py-2 text-slate-500 text-xs">{String(row.securities_type ?? '—')}</td>
                <td className="px-3 py-2 text-slate-500 text-xs">{String(row.currency ?? '—')}</td>
                <td className="px-3 py-2 text-right tabular-nums">{row.current_price != null ? Number(row.current_price).toLocaleString('el-GR', { minimumFractionDigits: 2, maximumFractionDigits: 4 }) : '—'}</td>
                <td className="px-3 py-2 text-right tabular-nums text-slate-600">{row.target_price != null ? Number(row.target_price).toLocaleString('el-GR', { minimumFractionDigits: 2, maximumFractionDigits: 4 }) : '—'}</td>
                <td className="px-3 py-2 text-right tabular-nums text-slate-600">{row.stop_loss != null ? Number(row.stop_loss).toLocaleString('el-GR', { minimumFractionDigits: 2, maximumFractionDigits: 4 }) : '—'}</td>
                <td className="px-3 py-2 text-right tabular-nums">{fmtPct(row.pct_from_target)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{fmtPct(row.pct_from_stop)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{fmtPct(row.upside_to_analyst)}</td>
                <td className="px-3 py-2 text-right tabular-nums text-slate-500">{row.dividend_yield != null ? `${Number(row.dividend_yield).toFixed(2)}%` : '—'}</td>
                <td className="px-3 py-2 text-slate-400 text-xs whitespace-nowrap">{String(row.added_date ?? '—')}</td>
                <td className="px-3 py-2">
                  <div className="flex gap-1">
                    <button onClick={() => openEdit(row)} className="p-1 text-slate-400 hover:text-blue-600"><Pencil size={13} /></button>
                    <button onClick={() => deleteMut.mutate(Number(row.watchlist_id))} className="p-1 text-slate-400 hover:text-red-600"><Trash2 size={13} /></button>
                  </div>
                </td>
              </tr>
            ))}
            {sorted.length === 0 && <tr><td colSpan={12} className="px-3 py-8 text-center text-slate-400 text-sm">No securities on watchlist yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Alerts Tab ────────────────────────────────────────────────────────────────
const ALERT_TYPES = ['price_above', 'price_below', 'allocation_drift']
const ASSET_TYPES_FOR_DRIFT = ['Stock', 'ETF', 'Bond', 'Mutual Fund', 'Crypto', 'Other']

function AlertsTab() {
  const qc = useQueryClient()
  const { data = [], isLoading } = useQuery({ queryKey: ['alert-definitions'], queryFn: getAlertsDefinitions })
  const { data: securities = [] } = useQuery({ queryKey: ['securities'], queryFn: () => getSecurities() })
  const rows = data as Record<string, unknown>[]
  const secs = securities as Record<string, unknown>[]

  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState<Record<string, string>>({ alert_type: 'price_above', securities_id: '', asset_type: '', threshold: '', note: '' })
  const [editId, setEditId] = useState<number | null>(null)
  const [err, setErr] = useState('')

  const { sorted, sortKey, sortDir, toggleSort } = useSortTable(rows, 'created_at', 'desc')

  const saveMut = useMutation({
    mutationFn: saveAlert,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['alert-definitions'] }); setShowForm(false); setEditId(null); setErr('') },
    onError: (e) => setErr(extractError(e)),
  })
  const toggleMut = useMutation({
    mutationFn: ({ id, is_active }: { id: number; is_active: boolean }) => toggleAlert(id, is_active),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['alert-definitions'] }),
  })
  const deleteMut = useMutation({
    mutationFn: deleteAlert,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['alert-definitions'] }),
  })

  const openAdd = () => { setForm({ alert_type: 'price_above', securities_id: '', asset_type: '', threshold: '', note: '' }); setEditId(null); setShowForm(true) }
  const openEdit = (row: Record<string, unknown>) => {
    setForm({
      alert_type: String(row.alert_type ?? 'price_above'),
      securities_id: String(row.securities_id ?? ''),
      asset_type: String(row.asset_type ?? ''),
      threshold: String(row.threshold ?? ''),
      note: String(row.note ?? ''),
    })
    setEditId(Number(row.alert_id))
    setShowForm(true)
  }

  const doSave = () => {
    if (!form.alert_type) return setErr('Alert type is required')
    if (!form.threshold) return setErr('Threshold is required')
    const isPriceAlert = form.alert_type === 'price_above' || form.alert_type === 'price_below'
    if (isPriceAlert && !form.securities_id) return setErr('Security is required for price alerts')
    if (form.alert_type === 'allocation_drift' && !form.asset_type) return setErr('Asset type is required for allocation drift alerts')
    saveMut.mutate({
      alert_id: editId ?? undefined,
      alert_type: form.alert_type,
      securities_id: form.securities_id ? Number(form.securities_id) : null,
      asset_type: form.asset_type || null,
      threshold: Number(form.threshold),
      direction: form.alert_type === 'price_above' ? 'above' : form.alert_type === 'price_below' ? 'below' : 'drift',
      note: form.note || null,
    })
  }

  const alertTypeBadge = (t: unknown) => {
    const s = String(t ?? '')
    const color = s === 'price_above' ? 'bg-green-100 text-green-700' : s === 'price_below' ? 'bg-red-100 text-red-700' : 'bg-amber-100 text-amber-700'
    const label = s === 'price_above' ? '▲ Price Above' : s === 'price_below' ? '▼ Price Below' : '⚖ Alloc Drift'
    return <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${color}`}>{label}</span>
  }

  const isPriceType = form.alert_type === 'price_above' || form.alert_type === 'price_below'

  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>

  return (
    <div className="p-4 space-y-4">
      <div className="flex justify-between items-center">
        <p className="text-sm text-slate-500">{rows.length} alert{rows.length !== 1 ? 's' : ''} defined</p>
        <Button size="sm" onClick={openAdd}><Plus size={14} /> Add Alert</Button>
      </div>

      {showForm && (
        <Modal title={editId ? 'Edit Alert' : 'Add Alert'} onClose={() => { setShowForm(false); setErr('') }}
          footer={<><Button variant="secondary" onClick={() => { setShowForm(false); setErr('') }}>Cancel</Button><Button onClick={doSave} disabled={saveMut.isPending}>Save</Button></>}>
          {err && <p className="text-xs text-red-600">{err}</p>}
          <Field label="Alert Type *">
            <select className="block w-full rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm"
              value={form.alert_type} onChange={e => setForm(f => ({ ...f, alert_type: e.target.value, securities_id: '', asset_type: '' }))}>
              {ALERT_TYPES.map(t => <option key={t} value={t}>{t === 'price_above' ? '▲ Price Above Threshold' : t === 'price_below' ? '▼ Price Below Threshold' : '⚖ Allocation Drift'}</option>)}
            </select>
          </Field>
          {isPriceType && (
            <Field label="Security *">
              <select className="block w-full rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm"
                value={form.securities_id} onChange={e => setForm(f => ({ ...f, securities_id: e.target.value }))}>
                <option value="">— select —</option>
                {secs.map(s => <option key={String(s.id)} value={String(s.id)}>{String(s.name)} ({String(s.ticker ?? '')})</option>)}
              </select>
            </Field>
          )}
          {form.alert_type === 'allocation_drift' && (
            <Field label="Asset Type *">
              <select className="block w-full rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm"
                value={form.asset_type} onChange={e => setForm(f => ({ ...f, asset_type: e.target.value }))}>
                <option value="">— select —</option>
                {ASSET_TYPES_FOR_DRIFT.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
            </Field>
          )}
          <Field label={form.alert_type === 'allocation_drift' ? 'Drift Threshold (%)' : 'Price Threshold *'}>
            <Input type="number" step="any" value={form.threshold} onChange={e => setForm(f => ({ ...f, threshold: e.target.value }))} />
          </Field>
          <Field label="Note"><Input value={form.note} onChange={e => setForm(f => ({ ...f, note: e.target.value }))} placeholder="optional" /></Field>
        </Modal>
      )}

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead><tr className="bg-slate-50 text-xs text-slate-500 border-b border-slate-200">
            <th className="px-3 py-2 text-left font-medium">Status</th>
            <ColHeader label="Type" sortKey="alert_type" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} />
            <ColHeader label="Security" sortKey="securities_name" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} />
            <ColHeader label="Asset Type" sortKey="asset_type" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} />
            <ColHeader label="Threshold" sortKey="threshold" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} align="right" />
            <ColHeader label="Current Price" sortKey="current_price" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} align="right" />
            <th className="px-3 py-2 text-left font-medium">Note</th>
            <ColHeader label="Created" sortKey="created_at" currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} />
            <th className="px-3 py-2"></th>
          </tr></thead>
          <tbody className="divide-y divide-slate-100">
            {sorted.map(row => {
              const isActive = Boolean(row.is_active)
              const triggered = row.current_price != null && row.threshold != null && (
                (row.alert_type === 'price_above' && Number(row.current_price) > Number(row.threshold)) ||
                (row.alert_type === 'price_below' && Number(row.current_price) < Number(row.threshold))
              )
              return (
                <tr key={String(row.alert_id)} className={`hover:bg-slate-50 ${!isActive ? 'opacity-50' : ''}`}>
                  <td className="px-3 py-2">
                    <button onClick={() => toggleMut.mutate({ id: Number(row.alert_id), is_active: !isActive })}
                      className={`w-8 h-4 rounded-full transition-colors ${isActive ? 'bg-blue-500' : 'bg-slate-300'} relative`}>
                      <span className={`absolute top-0.5 w-3 h-3 rounded-full bg-white transition-transform ${isActive ? 'left-4.5' : 'left-0.5'}`} />
                    </button>
                    {triggered && <span className="ml-1.5 text-[10px] bg-red-100 text-red-700 px-1.5 py-0.5 rounded-full font-medium">🔔 triggered</span>}
                  </td>
                  <td className="px-3 py-2">{alertTypeBadge(row.alert_type)}</td>
                  <td className="px-3 py-2 font-medium">{row.securities_name != null ? String(row.securities_name) : '—'}</td>
                  <td className="px-3 py-2 text-slate-500 text-xs">{row.asset_type != null ? String(row.asset_type) : '—'}</td>
                  <td className="px-3 py-2 text-right tabular-nums font-medium">{row.threshold != null ? Number(row.threshold).toLocaleString('el-GR', { minimumFractionDigits: 2, maximumFractionDigits: 4 }) : '—'}</td>
                  <td className={`px-3 py-2 text-right tabular-nums ${triggered ? 'text-red-600 font-semibold' : 'text-slate-600'}`}>
                    {row.current_price != null ? Number(row.current_price).toLocaleString('el-GR', { minimumFractionDigits: 2, maximumFractionDigits: 4 }) : '—'}
                  </td>
                  <td className="px-3 py-2 text-slate-400 text-xs">{row.note != null ? String(row.note) : ''}</td>
                  <td className="px-3 py-2 text-slate-400 text-xs whitespace-nowrap">{row.created_at != null ? String(row.created_at).slice(0, 16) : '—'}</td>
                  <td className="px-3 py-2">
                    <div className="flex gap-1">
                      <button onClick={() => openEdit(row)} className="p-1 text-slate-400 hover:text-blue-600"><Pencil size={13} /></button>
                      <button onClick={() => deleteMut.mutate(Number(row.alert_id))} className="p-1 text-slate-400 hover:text-red-600"><Trash2 size={13} /></button>
                    </div>
                  </td>
                </tr>
              )
            })}
            {sorted.length === 0 && <tr><td colSpan={9} className="px-3 py-8 text-center text-slate-400 text-sm">No alerts defined yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export default function MarketData() {
  const [searchParams, setSearchParams] = useSearchParams()
  const initialTab = searchParams.get('tab') ?? 'Currencies'
  const [tab, setTab] = useState(initialTab)
  const [search, setSearch] = useState('')

  const { data: anomalies = [], isLoading: anomLoading } = useQuery({
    queryKey: ['price-anomalies'],
    queryFn: () => getPriceAnomalies(100),
    enabled: tab === 'Anomalies',
  })

  return (
    <div>
      <PageHeader title="Market Data" />

      <div className="px-6 py-4 space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex gap-1 border-b border-slate-200 flex-1">
            {TABS.map(t => (
              <button key={t} onClick={() => { setTab(t); setSearch(''); setSearchParams({ tab: t }) }}
                className={`px-4 py-2 text-sm font-medium -mb-px border-b-2 transition-colors ${tab === t ? 'border-blue-600 text-blue-600' : 'border-transparent text-slate-500 hover:text-slate-700'}`}>
                {t}
              </button>
            ))}
          </div>
          {(tab === 'Securities' || tab === 'Currencies') && (
            <div className="relative ml-4">
              <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
              <Input className="pl-8 w-52" placeholder="Search…" value={search} onChange={e => setSearch(e.target.value)} />
            </div>
          )}
        </div>

        <Card>
          <CardBody className="p-0">
            {tab === 'Currencies' && <CurrenciesTab search={search} />}
            {tab === 'Securities' && <SecuritiesTab search={search} />}
            {tab === 'FX Prices' && <FxPricesTab />}
            {tab === 'Securities Prices' && <SecuritiesPricesTab />}
            {tab === 'Downloads' && <DownloadsTab />}
            {tab === 'Anomalies' && (
              anomLoading ? <div className="flex justify-center py-12"><Spinner /></div> : (
                <div className="ag-theme-alpine" style={{ height: '560px', width: '100%' }}>
                  <AgGridReact rowData={anomalies} columnDefs={ANOMALY_COLS}
                    defaultColDef={{ resizable: true, sortable: true, filter: true }} />
                </div>
              )
            )}
            {tab === 'Watchlist' && <WatchlistTab />}
            {tab === 'Alerts' && <AlertsTab />}
          </CardBody>
        </Card>
      </div>
    </div>
  )
}
