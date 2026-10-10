import React, { useState, useMemo, useEffect } from 'react'
import { usePersist, useGridColumnState, useGridScrollState, useGridFilterState, useLiveRefetchInterval, useGridApi } from '@/lib/hooks'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient, keepPreviousData } from '@tanstack/react-query'
import { AgGridReact } from 'ag-grid-react'
import type { ColDef, RowClickedEvent } from 'ag-grid-community'
import PlotlyReact from 'react-plotly.js'
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const Plot: React.ComponentType<any> = (PlotlyReact as any).default ?? PlotlyReact
import { getCurrencies, getSecurities, getPriceAnomalies, refreshFx, upsertSecurity, upsertCurrency, api, downloadCountryExposure, downloadYahooInfo, downloadYahooDividends, downloadStockSplits, downloadFundComposition, downloadFundamentals, downloadYahooPrices, downloadTvInfo, downloadTvPrices, downloadSolidusBonds, downloadIsin, getWatchlist, upsertWatchlistItem, deleteWatchlistItem, getAlertsDefinitions, saveAlert, toggleAlert, deleteAlert, searchTicker, lookupTicker, getTaxCategoryRules, getIssuers, getShillerCape, getShillerCapeSummary, downloadShillerCape, getCountryCapeRatios, upsertCountryCapeRatio, deleteCountryCapeRatio, downloadCountryCapeRatios, getInterestRates, getInterestRatesSummary, downloadInterestRates, getRateSeriesDefs, saveRateSeries, deleteRateSeries, addRateValue, getRateTracking, saveRateTracking, deleteRateTracking, getRateFundDurations } from '@/lib/api'
import { PageHeader, Input, Button, Spinner, Card, CardBody, ColHeader, useSortTable, useEscapeKey, ColumnsMenu, CopyToExcelButton, AG_GRID_COLUMN_TYPES, Tooltip } from '@/components/ui'
import PricesUpdatedAuto from '@/components/PricesUpdatedAuto'
import { plotLayout, plotAxis, fmtNum, fmtPct, todayLocal } from '@/lib/utils'
import { useTheme } from '@/lib/theme'
import { Search, Plus, Trash2, Pencil, Save, X, Copy } from 'lucide-react'
import { SecurityFormFields, EMPTY_SECURITY_FORM } from '@/components/SecurityForm'
import { CurrencyLink, CurrencyCell } from '@/components/CurrencyLink'

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
  useEscapeKey(onClose)
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

const TABS = ['Currencies', 'Securities', 'Rates', 'Downloads', 'Anomalies', 'Watchlist', 'CAPE Ratios', 'Alerts']

const ANOMALY_COLS: ColDef[] = [
  { field: 'security_name', headerName: 'Security', flex: 2 },
  { field: 'date', headerName: 'Date', width: 110 },
  { field: 'close', headerName: 'Close', width: 110, type: 'numericColumn', filter: 'agNumberColumnFilter', valueFormatter: p => fmtNum(Number(p.value), 4) },
  { field: 'prev_close', headerName: 'Prev', width: 110, type: 'numericColumn', filter: 'agNumberColumnFilter', valueFormatter: p => p.value != null ? fmtNum(Number(p.value), 4) : '—' },
  { field: 'next_close', headerName: 'Next', width: 110, type: 'numericColumn', filter: 'agNumberColumnFilter', valueFormatter: p => p.value != null ? fmtNum(Number(p.value), 4) : '—' },
  { field: 'ratio_prev', headerName: 'Ratio Prev', width: 110, type: 'numericColumn' },
  { field: 'ratio_next', headerName: 'Ratio Next', width: 110, type: 'numericColumn' },
]

type TickerSearchResult = { symbol: string; name: string; type: string; exchange: string }

// ── Securities CRUD tab ───────────────────────────────────────────────────────
function SecuritiesTab({ search, onSearchChange }: { search: string; onSearchChange: (v: string) => void }) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [editRow, setEditRow] = useState<Record<string, unknown> | null>(null)
  const [form, setForm] = useState<Record<string, string>>(EMPTY_SECURITY_FORM)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [lookupQuery, setLookupQuery] = useState('')
  const [lookupLoading, setLookupLoading] = useState(false)
  const [lookupError, setLookupError] = useState<string | null>(null)
  const [lookupOk, setLookupOk] = useState(false)
  const [searchResults, setSearchResults] = useState<TickerSearchResult[]>([])

  const { data: securities = [], isLoading } = useQuery({
    queryKey: ['securities', search],
    queryFn: () => getSecurities(search || undefined),
    placeholderData: keepPreviousData,
  })
  const { data: currencies = [] } = useQuery({ queryKey: ['currencies'], queryFn: getCurrencies })
  const { data: taxRules = [] } = useQuery({ queryKey: ['tax-category-rules'], queryFn: getTaxCategoryRules })
  const { data: issuers = [] } = useQuery({ queryKey: ['issuers'], queryFn: () => getIssuers() })

  const set = (k: string, v: string) => setForm(f => ({ ...f, [k]: v }))

  const resetLookup = () => { setLookupQuery(''); setLookupError(null); setLookupOk(false); setSearchResults([]) }

  const openNew = () => { setEditRow({}); setForm(EMPTY_SECURITY_FORM); setError(null); resetLookup() }

  const openEdit = (row: Record<string, unknown>) => {
    setEditRow(row)
    setForm({ ...EMPTY_SECURITY_FORM, ...Object.fromEntries(Object.entries(row).map(([k, v]) => [k, v != null ? String(v) : ''])) })
    setError(null)
    resetLookup()
  }

  const applyLookup = async (symbol: string) => {
    setLookupLoading(true)
    setLookupError(null)
    setSearchResults([])
    try {
      const d = await lookupTicker(symbol)
      setForm(f => ({
        ...f,
        ticker: d.ticker || f.ticker,
        name: d.name || f.name,
        type: d.type || f.type,
        currencies_id: d.currencies_id != null ? String(d.currencies_id) : f.currencies_id,
        isin: d.isin || f.isin,
        sector: d.sector || f.sector,
        industry: d.industry || f.industry,
        yahoo_ticker: d.yahoo_ticker || f.yahoo_ticker,
        dividend_yield: d.dividend_yield != null ? String(d.dividend_yield) : f.dividend_yield,
        dividend_rate: d.dividend_rate != null ? String(d.dividend_rate) : f.dividend_rate,
        ex_dividend_date: d.ex_dividend_date || f.ex_dividend_date,
        dividend_pay_date: d.dividend_pay_date || f.dividend_pay_date,
        payout_ratio: d.payout_ratio != null ? String(d.payout_ratio) : f.payout_ratio,
        five_year_avg_yield: d.five_year_avg_yield != null ? String(d.five_year_avg_yield) : f.five_year_avg_yield,
        analyst_rating: d.analyst_rating || f.analyst_rating,
        analyst_target_price: d.analyst_target_price != null ? String(d.analyst_target_price) : f.analyst_target_price,
      }))
      setLookupOk(true)
    } catch (e) {
      setLookupError(extractError(e))
    } finally {
      setLookupLoading(false)
    }
  }

  const handleSearch = async () => {
    const q = lookupQuery.trim()
    if (!q) return
    setLookupLoading(true)
    setLookupError(null)
    setLookupOk(false)
    setSearchResults([])
    try {
      const results = await searchTicker(q) as TickerSearchResult[]
      if (results.length === 0) {
        setLookupError('No results found. Try a different ticker or name.')
        setLookupLoading(false)
      } else if (results.length === 1) {
        await applyLookup(results[0].symbol)  // applyLookup handles setLookupLoading(false)
      } else {
        setSearchResults(results)
        setLookupLoading(false)
      }
    } catch (e) {
      setLookupError(extractError(e))
      setLookupLoading(false)
    }
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

  // Turns the currently-open edit into a fresh, unsaved copy — same fields (ticker,
  // name, type, dividend info, etc.), no id. handleSave's payload id comes from
  // editRow?.id (not form.id), so both must be cleared together or Save would silently
  // overwrite the original security instead of creating a new one.
  const handleDuplicate = () => {
    if (!editRow) return
    setForm(f => ({ ...f, id: '' }))
    setEditRow(er => er ? { ...er, id: undefined } : er)
    setError(null)
  }

  // A stable reference matters here: onColumnResized/onColumnMoved persist column
  // state, which re-renders this component — a fresh array literal on every render
  // would make ag-Grid treat columnDefs as "changed" and reset it back to these
  // literal widths, undoing any resize the user just made.
  const colDefs = useMemo(() => {
    const cols: ColDef[] = [
    { field: 'ticker', headerName: 'Ticker Symbol', width: 120, cellStyle: { fontFamily: 'monospace', fontWeight: 600 } },
    { field: 'name', headerName: 'Security Name', flex: 2, minWidth: 180,
      cellRenderer: (p: { value: string; data: Record<string, unknown> }) => (
        <button onClick={() => navigate(`/securities/${p.data.id}`)}
          className="text-blue-600 hover:underline text-left truncate w-full">{p.value}</button>
      ) },
    { field: 'is_active', headerName: 'Is Active', width: 90, cellRenderer: (p: {value: unknown}) => <input type="checkbox" readOnly checked={!!p.value} className="mt-2.5" /> },
    { field: 'is_tax_exempt', headerName: 'Tax Exempt', width: 100, cellRenderer: (p: {value: unknown}) => <input type="checkbox" readOnly checked={!!p.value} className="mt-2.5" /> },
    { field: 'tax_category', headerName: 'Tax Category', width: 130, hide: true },
    { field: 'sector', headerName: 'Sector', width: 140, hide: true },
    { field: 'industry', headerName: 'Industry', width: 140, hide: true },
    { field: 'yahoo_ticker', headerName: 'Yahoo Ticker', width: 110, cellStyle: { fontFamily: 'monospace' } },
    { field: 'tv_symbol', headerName: 'TV Symbol', width: 100 },
    { field: 'tv_exchange', headerName: 'TV Exchange', width: 110 },
    { field: 'isin', headerName: 'ISIN', width: 130 },
    { field: 'maturity_date', headerName: 'Maturity Date', width: 115, valueFormatter: p => p.value?.slice(0, 10) ?? '' },
    { field: 'coupon_rate', headerName: 'Coupon Rate', width: 110, type: 'numericColumn', filter: 'agNumberColumnFilter', valueFormatter: p => p.value != null ? fmtPct(Number(p.value), 2) : '' },
    { field: 'coupon_frequency', headerName: 'Coupon Frequency', width: 130, hide: true },
    { field: 'face_value', headerName: 'Face Value', width: 100, type: 'numericColumn', filter: 'agNumberColumnFilter', valueFormatter: p => p.value != null ? fmtNum(Number(p.value), 2) : '' },
    { field: 'type', headerName: 'Type', width: 110 },
    { field: 'currency', headerName: 'Ccy', width: 65, cellRenderer: CurrencyCell },
    { field: 'latest_price', headerName: 'Last Price', width: 110, type: 'numericColumn', filter: 'agNumberColumnFilter', valueFormatter: p => p.value != null ? fmtNum(Number(p.value), 4) : '—' },
    { field: 'price_date', headerName: 'Price Date', width: 100, valueFormatter: p => p.value?.slice(0, 10) ?? '—' },
    { field: 'dividend_yield', headerName: 'Div Yield', width: 90, type: 'numericColumn', filter: 'agNumberColumnFilter', valueFormatter: p => p.value != null ? fmtPct(Number(p.value), 2) : '' },
    { field: 'dividend_rate', headerName: 'Dividend Rate', width: 110, type: 'numericColumn', filter: 'agNumberColumnFilter', hide: true, valueFormatter: p => p.value != null ? fmtNum(Number(p.value), 4) : '' },
    { field: 'dividend_frequency', headerName: 'Dividend Frequency', width: 140, hide: true },
    { field: 'ex_dividend_date', headerName: 'Ex-Dividend Date', width: 130, hide: true, valueFormatter: p => p.value?.slice(0, 10) ?? '' },
    { field: 'dividend_pay_date', headerName: 'Dividend Pay Date', width: 140, hide: true, valueFormatter: p => p.value?.slice(0, 10) ?? '' },
    { field: 'payout_ratio', headerName: 'Payout Ratio', width: 110, type: 'numericColumn', filter: 'agNumberColumnFilter', hide: true, valueFormatter: p => p.value != null ? fmtPct(Number(p.value), 2) : '' },
    { field: 'five_year_avg_yield', headerName: '5Y Avg Yield', width: 110, type: 'numericColumn', filter: 'agNumberColumnFilter', hide: true, valueFormatter: p => p.value != null ? fmtPct(Number(p.value), 2) : '' },
    { field: 'analyst_rating', headerName: 'Analyst Rating', width: 120, hide: true },
    { field: 'analyst_target_price', headerName: 'Target Price', width: 110, type: 'numericColumn', filter: 'agNumberColumnFilter', hide: true, valueFormatter: p => p.value != null ? fmtNum(Number(p.value), 2) : '' },
    { field: 'price_records', headerName: '# Prices', width: 80, type: 'numericColumn' },
    { field: 'held_quantity', headerName: 'Held Qty', width: 80, type: 'numericColumn' },
    { field: 'investment_count', headerName: '# Investments', width: 100, type: 'numericColumn', filter: 'agNumberColumnFilter', hide: true },
    {
      colId: 'actions', headerName: '', width: 70, sortable: false, filter: false, pinned: 'right',
      cellRenderer: (p: { data: Record<string, unknown> }) => (
        <div className="flex gap-1 items-center h-full">
          <button onClick={() => openEdit(p.data)} className="text-blue-500 hover:text-blue-700 p-1"><Pencil size={13} /></button>
          <button onClick={() => handleDelete(Number(p.data.id))} className="text-red-400 hover:text-red-600 p-1"><Trash2 size={13} /></button>
        </div>
      ),
    },
    ]
    return cols
  }, [navigate]) // eslint-disable-line react-hooks/exhaustive-deps
  const gridCols = useGridColumnState('market-data-securities', colDefs)
  const gridScroll = useGridScrollState('market-data-securities')
  const gridFilter = useGridFilterState('market-data-securities')
  const { gridApi, onGridReady } = useGridApi(api => {
    if (gridFilter.filterModel) api.setFilterModel(gridFilter.filterModel)
  })

  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>

  return (
    <div>
      <div className="flex items-center justify-between gap-2 px-4 py-2 border-b border-slate-100 bg-slate-50 flex-wrap">
        <div className="flex items-center gap-3 flex-wrap">
          <div className="relative">
            <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
            <Input className="pl-8 w-56" placeholder="Search…" value={search} onChange={e => onSearchChange(e.target.value)} />
          </div>
          {deleteError && <span className="text-xs text-red-600 bg-red-50 rounded px-3 py-1">{deleteError}</span>}
          <span className="text-xs text-slate-400 whitespace-nowrap">{(securities as unknown[]).length} securities</span>
        </div>
        <div className="flex items-center gap-2">
          {gridFilter.hasFilters && (
            <button onClick={() => gridFilter.clearFilters(gridApi)}
              className="px-3 py-1.5 text-xs rounded border font-medium border-slate-300 text-slate-600 hover:bg-slate-50">
              ✕ Clear Filters
            </button>
          )}
          <Button size="sm" variant="secondary" onClick={openNew}><Plus size={13} /> Add Security</Button>
          <ColumnsMenu columns={gridCols.columns} onToggle={gridCols.toggleColumn} />
          <CopyToExcelButton gridApi={gridApi} />
        </div>
      </div>
      <div className="ag-theme-alpine" style={{ height: 'calc(100vh - 220px)', width: '100%' }}>
        <AgGridReact theme="legacy" rowData={securities} columnDefs={gridCols.colDefs} onGridReady={onGridReady}
          defaultColDef={{ resizable: true, sortable: true, filter: true }} columnTypes={AG_GRID_COLUMN_TYPES}
          initialState={gridScroll.initialState}
          onStateUpdated={gridScroll.onStateUpdated}
          onFilterChanged={gridFilter.onFilterChanged}
          onColumnMoved={gridCols.onColumnMoved}
          onColumnResized={gridCols.onColumnResized}
          onRowClicked={(e: RowClickedEvent) => { if ((e.event as MouseEvent)?.detail === 2) openEdit(e.data as Record<string, unknown>) }} />
      </div>

      {editRow !== null && (
        <Modal title={form.id ? `Edit Security — ${form.ticker}` : 'New Security'} wide onClose={() => setEditRow(null)}
          footer={<>
            {form.id && <Button variant="destructive" onClick={() => { setEditRow(null); handleDelete(Number(form.id)) }} disabled={saving}><Trash2 size={14} /> Delete</Button>}
            {form.id && <Button variant="secondary" onClick={handleDuplicate} disabled={saving}><Copy size={14} /> Duplicate</Button>}
            <span className="flex-1" />
            <Button variant="secondary" onClick={() => setEditRow(null)}>Cancel</Button>
            <Button onClick={handleSave} disabled={saving || !form.name?.trim() || !form.ticker?.trim()}>
              <Save size={14} /> {saving ? 'Saving…' : 'Save'}
            </Button>
          </>}>
          {/* Yahoo Finance auto-fill */}
          <div className="p-3 bg-blue-50 rounded-lg border border-blue-100 mb-1 space-y-2">
            <div className="flex gap-2 items-end">
              <div className="flex-1">
                <label className="block text-xs font-medium text-blue-700 mb-1">Auto-fill from Yahoo Finance — ticker or company name</label>
                <Input
                  value={lookupQuery}
                  onChange={e => { setLookupQuery(e.target.value); setLookupOk(false); setSearchResults([]) }}
                  onKeyDown={e => { if (e.key === 'Enter') handleSearch() }}
                  placeholder="e.g. AAPL · JPMorgan Chase · VWCE.DE"
                />
              </div>
              <Button size="sm" variant="secondary" onClick={handleSearch} disabled={lookupLoading || !lookupQuery.trim()}>
                {lookupLoading ? <Spinner /> : 'Search'}
              </Button>
            </div>
            {searchResults.length > 0 && (
              <div className="border border-blue-200 rounded-md bg-white overflow-hidden">
                <p className="text-xs text-blue-600 font-medium px-3 py-1.5 bg-blue-50 border-b border-blue-100">{searchResults.length} results — click to select</p>
                <div className="divide-y divide-slate-100 max-h-48 overflow-y-auto">
                  {searchResults.map(r => (
                    <button key={r.symbol} onClick={() => applyLookup(r.symbol)} disabled={lookupLoading}
                      className="w-full flex items-center gap-3 px-3 py-2 text-left hover:bg-blue-50 transition-colors">
                      <span className="font-mono text-sm font-semibold text-blue-700 w-24 shrink-0">{r.symbol}</span>
                      <span className="text-sm text-slate-700 flex-1 truncate">{r.name}</span>
                      <span className="text-xs text-slate-400 shrink-0">{r.type}</span>
                      <span className="text-xs text-slate-400 shrink-0 w-20 text-right">{r.exchange}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
          {lookupError && <p className="text-xs text-red-600 bg-red-50 rounded px-3 py-2 mb-1">{lookupError}</p>}
          {lookupOk && <p className="text-xs text-green-700 bg-green-50 rounded px-3 py-2 mb-1">Fields auto-filled — review and adjust before saving.</p>}
          <SecurityFormFields form={form} set={set} currencies={currencies as Record<string, unknown>[]} taxRules={taxRules as Record<string, unknown>[]} issuers={issuers as Record<string, unknown>[]} />
          {error && <p className="text-xs text-red-600 bg-red-50 rounded px-3 py-2 mt-2">{error}</p>}
        </Modal>
      )}
    </div>
  )
}

// ── Currencies CRUD tab ───────────────────────────────────────────────────────
function CurrenciesTab({ search, onSearchChange }: { search: string; onSearchChange: (v: string) => void }) {
  const qc = useQueryClient()
  const navigate = useNavigate()
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

  // Same "Edit" → "New" flip as SecuritiesTab's handleDuplicate (see its comment for why
  // both form.id and editRow.id must be cleared together).
  const handleDuplicate = () => {
    if (!editRow) return
    setForm(f => ({ ...f, id: '' }))
    setEditRow(er => er ? { ...er, id: undefined } : er)
    setError(null)
  }

  // Stable reference — see the identical note in SecuritiesTab's colDefs above.
  const colDefs = useMemo(() => {
    const cols: ColDef[] = [
      { field: 'code', headerName: 'Code', width: 90, cellStyle: { fontFamily: 'monospace', fontWeight: 600 } },
      {
        field: 'name', headerName: 'Currency', flex: 2,
        cellRenderer: (p: { data: Record<string, unknown>; value: unknown }) => (
          <button onClick={() => navigate(`/currencies/${p.data.id}`)}
            className="text-blue-600 hover:underline text-left">{String(p.value ?? '')}</button>
        ),
      },
      { field: 'latest_rate', headerName: 'Rate vs EUR', width: 130, type: 'numericColumn', filter: 'agNumberColumnFilter', valueFormatter: p => p.value != null ? fmtNum(Number(p.value), 4) : '—' },
      { field: 'rate_date', headerName: 'Rate Date', width: 110, valueFormatter: p => p.value?.slice(0, 10) ?? '—' },
      { field: 'price_records', headerName: '# Records', width: 100, type: 'numericColumn' },
      {
        colId: 'actions', headerName: '', width: 80, sortable: false, filter: false,
        cellRenderer: (p: { data: Record<string, unknown> }) => (
          <div className="flex gap-1 items-center h-full">
            <button onClick={() => openEdit(p.data)} className="text-blue-500 hover:text-blue-700 p-1"><Pencil size={13} /></button>
            <button onClick={() => handleDelete(Number(p.data.id))} className="text-red-400 hover:text-red-600 p-1"><Trash2 size={13} /></button>
          </div>
        ),
      },
    ]
    return cols
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
  const gridCols = useGridColumnState('market-data-currencies', colDefs)
  const gridScroll = useGridScrollState('market-data-currencies')
  const gridFilter = useGridFilterState('market-data-currencies')
  const { gridApi, onGridReady } = useGridApi(api => {
    if (gridFilter.filterModel) api.setFilterModel(gridFilter.filterModel)
  })

  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>

  return (
    <div>
      <div className="flex items-center justify-between gap-2 px-4 py-2 border-b border-slate-100 bg-slate-50 flex-wrap">
        <div className="flex items-center gap-3 flex-wrap">
          <div className="relative">
            <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
            <Input className="pl-8 w-56" placeholder="Search…" value={search} onChange={e => onSearchChange(e.target.value)} />
          </div>
          {deleteError && <span className="text-xs text-red-600 bg-red-50 rounded px-3 py-1">{deleteError}</span>}
          <span className="text-xs text-slate-400 whitespace-nowrap">{filtered.length} currencies</span>
        </div>
        <div className="flex items-center gap-2">
          {gridFilter.hasFilters && (
            <button onClick={() => gridFilter.clearFilters(gridApi)}
              className="px-3 py-1.5 text-xs rounded border font-medium border-slate-300 text-slate-600 hover:bg-slate-50">
              ✕ Clear Filters
            </button>
          )}
          <Button size="sm" variant="secondary" onClick={openNew}><Plus size={13} /> Add Currency</Button>
          <ColumnsMenu columns={gridCols.columns} onToggle={gridCols.toggleColumn} />
          <CopyToExcelButton gridApi={gridApi} />
        </div>
      </div>
      <div className="ag-theme-alpine" style={{ height: '420px', width: '100%' }}>
        <AgGridReact theme="legacy" rowData={filtered} columnDefs={gridCols.colDefs} onGridReady={onGridReady}
          defaultColDef={{ resizable: true, sortable: true, filter: true }} columnTypes={AG_GRID_COLUMN_TYPES}
          initialState={gridScroll.initialState}
          onStateUpdated={gridScroll.onStateUpdated}
          onFilterChanged={gridFilter.onFilterChanged}
          onColumnMoved={gridCols.onColumnMoved}
          onColumnResized={gridCols.onColumnResized}
          onRowClicked={(e: RowClickedEvent) => { if ((e.event as MouseEvent)?.detail === 2) openEdit(e.data as Record<string, unknown>) }} />
      </div>

      {editRow !== null && (
        <Modal title={form.id ? 'Edit Currency' : 'New Currency'} onClose={() => setEditRow(null)}
          footer={<>
            {form.id && <Button variant="destructive" onClick={() => { setEditRow(null); handleDelete(Number(form.id)) }} disabled={saving}><Trash2 size={14} /> Delete</Button>}
            {form.id && <Button variant="secondary" onClick={handleDuplicate} disabled={saving}><Copy size={14} /> Duplicate</Button>}
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

// ── Downloads tab ─────────────────────────────────────────────────────────────
const PERIODS = ['1d', '5d', '1mo', '3mo', '6mo', '1y', '2y', '5y', 'max']

function DownloadsTab() {
  const qc = useQueryClient()
  const { data: securities = [] } = useQuery({ queryKey: ['securities'], queryFn: () => getSecurities() })
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
      qc.invalidateQueries({ queryKey: ['xray'] })
      qc.invalidateQueries({ queryKey: ['shiller-cape'] })
      qc.invalidateQueries({ queryKey: ['shiller-cape-summary'] })
      qc.invalidateQueries({ queryKey: ['country-cape'] })
      qc.invalidateQueries({ queryKey: ['interest-rates'] })
      qc.invalidateQueries({ queryKey: ['interest-rates-summary'] })
      qc.invalidateQueries({ queryKey: ['insights'] })
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
          <ActionRow id="yahoo-splits" label="Download Split History" onClick={() => run('yahoo-splits', () => downloadStockSplits(sid))} />
          <ActionRow id="fund-composition" label="Download Fund Composition (X-Ray)" onClick={() => run('fund-composition', () => downloadFundComposition(sid))} />
          <ActionRow id="fund-countries" label="Download Fund Country Exposure" onClick={() => run('fund-countries', () => downloadCountryExposure(sid))} />
          <ActionRow id="fundamentals" label="Download Fundamentals (F-Score/Z-Score)" onClick={() => run('fundamentals', () => downloadFundamentals(sid))} />
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

      {/* EODHD */}
      <div>
        <p className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-2">EODHD</p>
        <div className="rounded-lg border border-slate-200 bg-white divide-y divide-slate-100 px-4">
          <ActionRow id="eodhd-isin" label="Fetch Missing ISINs" onClick={() => run('eodhd-isin', () => downloadIsin(sid))} />
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

      {/* Shiller CAPE */}
      <div>
        <p className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-2">Market Valuation</p>
        <div className="rounded-lg border border-slate-200 bg-white divide-y divide-slate-100 px-4">
          <ActionRow id="shiller-cape" label="Download Shiller CAPE (shillerdata.com)" onClick={() => run('shiller-cape', downloadShillerCape)} />
          <ActionRow id="country-cape" label="Download Country CAPE Ratios (Siblis Research, free tier)" onClick={() => run('country-cape', downloadCountryCapeRatios)} />
        </div>
      </div>

      {/* Interest rates */}
      <div>
        <p className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-2">Interest Rates</p>
        <div className="rounded-lg border border-slate-200 bg-white divide-y divide-slate-100 px-4">
          <ActionRow id="interest-rates" label="Download Interest Rates (€STR, ECB, SOFR, Fed)" onClick={() => run('interest-rates', downloadInterestRates)} />
        </div>
      </div>

    </div>
  )
}

// ── Watchlist Tab ─────────────────────────────────────────────────────────────
function WatchlistTab() {
  const qc = useQueryClient()
  const liveRefetchMs = useLiveRefetchInterval()
  const { data = [], isLoading } = useQuery({ queryKey: ['watchlist'], queryFn: getWatchlist, refetchInterval: liveRefetchMs })
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

  // Turns the currently-open edit into a fresh, unsaved copy (same target/stop/note),
  // clearing editId so Save adds a new watchlist item instead of updating this one — the
  // security still needs picking again since a security can only be watched once.
  const duplicate = () => {
    setForm(f => ({ ...f, securities_id: '' }))
    setEditId(null)
    setErr('')
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
          footer={<>
            {editId && <Button variant="destructive" onClick={() => { deleteMut.mutate(editId); setShowAdd(false); setErr('') }} disabled={upsertMut.isPending}><Trash2 size={14} /> Delete</Button>}
            {editId && <Button variant="secondary" onClick={duplicate} disabled={upsertMut.isPending}><Copy size={14} /> Duplicate</Button>}
            <span className="flex-1" />
            <Button variant="secondary" onClick={() => { setShowAdd(false); setErr('') }}>Cancel</Button>
            <Button onClick={save} disabled={upsertMut.isPending}>Save</Button>
          </>}>
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
              <tr key={String(row.watchlist_id)} className="hover:bg-slate-50 cursor-pointer" onDoubleClick={() => openEdit(row)}>
                <td className="px-3 py-2 font-medium">
                  {String(row.securities_name)}
                  {Boolean(row.already_held) && <span className="ml-1.5 text-[10px] bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded-full font-medium">held</span>}
                </td>
                <td className="px-3 py-2 text-slate-500 text-xs">{String(row.securities_type ?? '—')}</td>
                <td className="px-3 py-2 text-slate-500 text-xs"><CurrencyLink code={row.currency} /></td>
                <td className="px-3 py-2 text-right tabular-nums">{row.current_price != null ? fmtNum(Number(row.current_price), 4) : '—'}</td>
                <td className="px-3 py-2 text-right tabular-nums text-slate-600">{row.target_price != null ? fmtNum(Number(row.target_price), 4) : '—'}</td>
                <td className="px-3 py-2 text-right tabular-nums text-slate-600">{row.stop_loss != null ? fmtNum(Number(row.stop_loss), 4) : '—'}</td>
                <td className="px-3 py-2 text-right tabular-nums">{fmtPct(row.pct_from_target)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{fmtPct(row.pct_from_stop)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{fmtPct(row.upside_to_analyst)}</td>
                <td className="px-3 py-2 text-right tabular-nums text-slate-500">{fmtPct(row.dividend_yield)}</td>
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

// ── CAPE Ratios Tab ───────────────────────────────────────────────────────────
// U.S. Shiller CAPE (auto-imported monthly from shillerdata.com) plus
// country-level CAPE ratios — 10 countries auto-import from Siblis Research's
// free-tier API (siblisresearch.com/global-valuations-database/api), a handful
// of month-end snapshots per country rather than a continuous series; anything
// outside that set has no free structured source and is entered by hand below.
// Both auto-imports live under the "Market Valuation" section of the
// Downloads tab.
function ShillerCapeSection() {
  const { isDark } = useTheme()
  const { data: summary } = useQuery({ queryKey: ['shiller-cape-summary'], queryFn: getShillerCapeSummary, retry: false })
  const { data: series = [] } = useQuery({ queryKey: ['shiller-cape'], queryFn: () => getShillerCape() })
  const rows = series as { date: string; cape_ratio: number }[]
  const s = summary as { date: string; cape_ratio: number; percentile: number; median: number; min: number; max: number; months: number } | undefined
  const [range, setRange] = usePersist<'1Y' | '3Y' | '5Y' | '10Y' | 'All'>('cape_range', 'All')
  const shown = useMemo(() => {
    const years = range === 'All' ? 0 : parseInt(range)
    if (!years || rows.length === 0) return rows
    const last = new Date(rows[rows.length - 1].date)
    const cutoff = new Date(last.getFullYear() - years, last.getMonth(), last.getDate()).toISOString().slice(0, 10)
    return rows.filter(r => String(r.date).slice(0, 10) >= cutoff)
  }, [rows, range])

  const zone = (pctile: number) => pctile >= 90 ? { label: 'Historically Expensive', cls: 'text-red-600' }
    : pctile >= 60 ? { label: 'Above Average', cls: 'text-amber-600' }
    : pctile >= 40 ? { label: 'Fair Value', cls: 'text-slate-600' }
    : { label: 'Below Average / Cheap', cls: 'text-green-700' }

  if (!s) {
    return (
      <div className="p-5 text-sm text-slate-500">
        No Shiller CAPE data yet — use <span className="font-mono">Download Shiller CAPE</span> on the Downloads tab to fetch it from shillerdata.com.
      </div>
    )
  }
  const z = zone(s.percentile)

  return (
    <div className="p-5 space-y-4">
      <div className="flex flex-wrap gap-3">
        <div className="bg-slate-50 rounded-lg p-4 text-center min-w-[160px] flex-1">
          <p className="text-xs text-slate-500 mb-1"><Tooltip text="Price of the S&P 500 divided by the 10-year moving average of inflation-adjusted earnings — Robert Shiller's cyclically adjusted P/E. Source: shillerdata.com.">Shiller CAPE ({String(s.date).slice(0, 7)})</Tooltip></p>
          <p className="text-2xl font-bold">{s.cape_ratio.toFixed(2)}×</p>
          <p className={`text-xs font-medium mt-1 ${z.cls}`}>{z.label}</p>
        </div>
        <div className="bg-slate-50 rounded-lg p-4 text-center min-w-[160px] flex-1">
          <p className="text-xs text-slate-500 mb-1"><Tooltip text="Where today's CAPE ranks against every month since 1881 — 98th percentile means only ~2% of months in the record were higher.">Percentile since 1881</Tooltip></p>
          <p className="text-2xl font-bold">{s.percentile.toFixed(1)}<span className="text-base">th</span></p>
        </div>
        <div className="bg-slate-50 rounded-lg p-4 text-center min-w-[160px] flex-1">
          <p className="text-xs text-slate-500 mb-1">Long-run Median</p>
          <p className="text-2xl font-bold">{s.median.toFixed(1)}×</p>
        </div>
        <div className="bg-slate-50 rounded-lg p-4 text-center min-w-[160px] flex-1">
          <p className="text-xs text-slate-500 mb-1">All-time Range</p>
          <p className="text-2xl font-bold">{s.min.toFixed(1)}× – {s.max.toFixed(1)}×</p>
        </div>
      </div>
      <p className="text-xs text-slate-400">
        A high CAPE describes the next decade's starting valuation base rate — it has never reliably timed a correction, so treat it as long-horizon context, not a trading signal.
      </p>
      {rows.length > 0 && (
        <div className="flex items-center gap-1">
          {(['1Y', '3Y', '5Y', '10Y', 'All'] as const).map(r => (
            <button key={r} type="button" onClick={() => setRange(r)}
              className={`px-2.5 py-1 text-xs rounded border ${range === r ? 'bg-slate-700 border-slate-700 text-white' : 'border-slate-300 text-slate-600 hover:bg-slate-50'}`}>
              {r === 'All' ? 'All Time' : r}
            </button>
          ))}
        </div>
      )}
      {rows.length > 0 && (
        <Plot
          data={[{ x: shown.map(r => r.date), y: shown.map(r => r.cape_ratio), type: 'scatter', mode: 'lines', line: { color: '#3b82f6', width: 1.5 }, name: 'CAPE' }]}
          layout={{ height: 340, yaxis: { title: 'CAPE Ratio' }, xaxis: { title: '' }, margin: { t: 20, b: 40, l: 60, r: 20 }, shapes: [
            { type: 'line', x0: 0, x1: 1, xref: 'paper', y0: s.median, y1: s.median, line: { color: '#94a3b8', width: 1, dash: 'dot' } },
          ], ...plotLayout(isDark) }}
          config={{ displayModeBar: false }} style={{ width: '100%' }}
        />
      )}
    </div>
  )
}

function CountryCapeSection() {
  const { isDark } = useTheme()
  const qc = useQueryClient()
  const { data = [], isLoading } = useQuery({ queryKey: ['country-cape'], queryFn: getCountryCapeRatios })
  const rows = data as Record<string, unknown>[]

  const [showAdd, setShowAdd] = useState(false)
  const [editId, setEditId] = useState<number | null>(null)
  const [form, setForm] = useState({ country: '', as_of_date: todayLocal(), cape_ratio: '', source: 'Siblis Research' })
  const [err, setErr] = useState('')

  const countries = useMemo(() => [...new Set(rows.map(r => String(r.country)))].sort(), [rows])
  const [filterCountry, setFilterCountry] = useState('')
  const filteredRows = filterCountry ? rows.filter(r => String(r.country) === filterCountry) : rows
  // Chart wants oldest-first; the table below (unaffected by this sort) shows
  // newest-first, which reads better as a log of snapshots as they're added.
  const chartRows = useMemo(() =>
    filterCountry
      ? rows.filter(r => String(r.country) === filterCountry)
          .slice()
          .sort((a, b) => String(a.as_of_date).localeCompare(String(b.as_of_date)))
      : [],
    [rows, filterCountry]
  )

  const upsertMut = useMutation({
    mutationFn: upsertCountryCapeRatio,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['country-cape'] }); setShowAdd(false); setEditId(null); setErr('') },
    onError: (e) => setErr(extractError(e)),
  })
  const deleteMut = useMutation({
    mutationFn: deleteCountryCapeRatio,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['country-cape'] }),
  })

  const openAdd = () => { setForm({ country: '', as_of_date: todayLocal(), cape_ratio: '', source: 'Siblis Research' }); setEditId(null); setShowAdd(true) }
  const openEdit = (row: Record<string, unknown>) => {
    setForm({
      country: String(row.country ?? ''),
      as_of_date: String(row.as_of_date ?? '').slice(0, 10),
      cape_ratio: String(row.cape_ratio ?? ''),
      source: String(row.source ?? ''),
    })
    setEditId(Number(row.id))
    setShowAdd(true)
  }

  const save = () => {
    if (!form.country.trim()) return setErr('Country is required')
    if (!form.cape_ratio) return setErr('CAPE ratio is required')
    upsertMut.mutate({
      id: editId ?? undefined,
      country: form.country.trim(),
      as_of_date: form.as_of_date,
      cape_ratio: Number(form.cape_ratio),
      source: form.source || undefined,
    })
  }

  if (isLoading) return <div className="flex justify-center py-8"><Spinner /></div>

  return (
    <div className="p-5 pt-0 space-y-3">
      <div className="flex justify-between items-center">
        <div>
          <p className="text-sm font-medium text-slate-700">Country CAPE Ratios</p>
          <p className="text-xs text-slate-400">10 countries auto-import via Siblis Research's free API (Downloads tab) — everything else (e.g. Taiwan, Turkey) has no free structured source and is entered by hand below.</p>
        </div>
        <div className="flex items-center gap-2">
          <select className="rounded-md border border-slate-300 px-2 py-1.5 text-sm bg-white" value={filterCountry} onChange={e => setFilterCountry(e.target.value)}>
            <option value="">— All countries —</option>
            {countries.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
          <Button size="sm" onClick={openAdd}><Plus size={14} /> Add Snapshot</Button>
        </div>
      </div>

      {filterCountry && (
        chartRows.length > 1 ? (
          <Plot
            data={[{ x: chartRows.map(r => String(r.as_of_date).slice(0, 10)), y: chartRows.map(r => Number(r.cape_ratio)), type: 'scatter', mode: 'lines+markers', line: { color: '#3b82f6', width: 1.5 }, marker: { size: 5 }, name: filterCountry }]}
            layout={{ height: 280, yaxis: { title: 'CAPE Ratio' }, xaxis: { title: '' }, margin: { t: 20, b: 40, l: 60, r: 20 }, ...plotLayout(isDark) }}
            config={{ displayModeBar: false }} style={{ width: '100%' }}
          />
        ) : (
          <p className="text-xs text-slate-400 py-4 text-center">Only one snapshot recorded for {filterCountry} — need at least two to draw a chart.</p>
        )
      )}

      {showAdd && (
        <Modal title={editId ? 'Edit Country CAPE' : 'Add Country CAPE'} onClose={() => { setShowAdd(false); setErr('') }}
          footer={<>
            {editId && <Button variant="destructive" onClick={() => { deleteMut.mutate(editId); setShowAdd(false); setErr('') }} disabled={upsertMut.isPending}><Trash2 size={14} /> Delete</Button>}
            <span className="flex-1" />
            <Button variant="secondary" onClick={() => { setShowAdd(false); setErr('') }}>Cancel</Button>
            <Button onClick={save} disabled={upsertMut.isPending}>Save</Button>
          </>}>
          {err && <p className="text-xs text-red-600">{err}</p>}
          <Field label="Country *"><Input value={form.country} onChange={e => setForm(f => ({ ...f, country: e.target.value }))} placeholder="e.g. Japan" /></Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="As Of *"><Input type="date" value={form.as_of_date} onChange={e => setForm(f => ({ ...f, as_of_date: e.target.value }))} /></Field>
            <Field label="CAPE Ratio *"><Input type="number" step="0.1" value={form.cape_ratio} onChange={e => setForm(f => ({ ...f, cape_ratio: e.target.value }))} placeholder="e.g. 24.0" /></Field>
          </div>
          <Field label="Source"><Input value={form.source} onChange={e => setForm(f => ({ ...f, source: e.target.value }))} placeholder="e.g. Siblis Research" /></Field>
        </Modal>
      )}

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead><tr className="bg-slate-50 text-xs text-slate-500 border-b border-slate-200">
            <th className="px-3 py-2 text-left font-medium">Country</th>
            <th className="px-3 py-2 text-right font-medium">CAPE Ratio</th>
            <th className="px-3 py-2 text-left font-medium">As Of</th>
            <th className="px-3 py-2 text-left font-medium">Source</th>
            <th className="px-3 py-2"></th>
          </tr></thead>
          <tbody>
            {filteredRows.map(r => (
              <tr key={String(r.id)} className="border-b border-slate-100 hover:bg-slate-50 cursor-pointer" onClick={() => openEdit(r)}>
                <td className="px-3 py-2 font-medium">{String(r.country)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{Number(r.cape_ratio).toFixed(1)}×</td>
                <td className="px-3 py-2">{String(r.as_of_date ?? '').slice(0, 10)}</td>
                <td className="px-3 py-2 text-slate-500">{String(r.source ?? '—')}</td>
                <td className="px-3 py-2 text-right"><Pencil size={13} className="text-slate-400" /></td>
              </tr>
            ))}
            {filteredRows.length === 0 && (
              <tr><td colSpan={5} className="px-3 py-8 text-center text-slate-400 text-sm">
                {rows.length === 0 ? 'No country CAPE snapshots recorded yet.' : `No snapshots for ${filterCountry}.`}
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Rates Tab ─────────────────────────────────────────────────────────────────
// Everything here is driven by the Rate_Series definitions (database/rates.py), not hard-coded
// to €STR/SOFR: the cards, the per-currency charts and the Dashboard alerts all follow whatever
// series are active, so tracking another rate (a JPY one, say) is a new definition under
// "Configure" below. Values come from the scheduler (every 2 h), the Downloads tab, or — for a
// MANUAL series — typed in here. Tracked funds (XEON vs €STR by default) are configurable too.
interface RateSeriesSummary {
  id: number; code: string; name: string; currency: string | null; rate_type: string; alerts_enabled: boolean
  latest: { date: string; rate: number; previous: number | null } | null
  change: { date: string; from: number; to: number; days_ago: number } | null
  move: { recent: number; average: number; diff: number; as_of: string } | null
}
interface RateTrackingSummary {
  id: number; ticker: string; security_name: string; series_code: string; series_name: string
  window_days_setting: number; threshold_pp: number; alert: boolean; duration_years: number | null
  result: { window_days: number; from: string; to: string; security_annualised_pct: number; series_annualised_pct: number; gap_pp: number } | null
}
interface RatesSummary { series: RateSeriesSummary[]; tracking: RateTrackingSummary[]; alerts: { type: string; title: string; message: string }[] }
interface RateSeriesDef {
  id: number; code: string; name: string; currencies_id: number | null; currency: string | null; rate_type: string
  provider: string; provider_key: string | null; provider_field: string | null; is_active: boolean; alerts_enabled: boolean
  sort_order: number; notes: string | null; last_date: string | null; row_count: number
}
interface RateTrackingDef {
  id: number; securities_id: number; ticker: string; security_name: string; rate_series_id: number
  series_code: string; series_name: string; window_days: number; threshold_pp: number; is_active: boolean; duration_years: number | null
}

const RATE_TYPES = ['Overnight', 'Policy', 'Target Upper', 'Target Lower', 'Other']
const RATE_PROVIDERS = ['ECB', 'NYFED', 'FRED', 'MANUAL']
const KEY_HINTS: Record<string, string> = {
  ECB: 'ECB Data Portal key, e.g. EST/B.EU000A2X2A25.WT',
  NYFED: 'NY Fed path, e.g. secured/sofr or unsecured/effr',
  FRED: 'FRED series id, e.g. IRSTCI01JPM156N (needs FRED_API_KEY)',
  MANUAL: '',
}
const SERIES_COLORS = ['#3b82f6', '#ef4444', '#10b981', '#f59e0b', '#8b5cf6', '#ec4899', '#14b8a6']

const shortName = (n: string) => n.match(/\(([^)]+)\)\s*$/)?.[1] ?? n
const rateDp = (r: number) => Math.min(3, Math.max(2, (String(r).split('.')[1] ?? '').length))

function RatesTab() {
  const { isDark } = useTheme()
  const [years, setYears] = useState<number | undefined>(3)
  const { data: summary } = useQuery({ queryKey: ['interest-rates-summary'], queryFn: getInterestRatesSummary, retry: false })
  const { data: raw = [] } = useQuery({ queryKey: ['interest-rates', years], queryFn: () => getInterestRates(undefined, years) })
  const { data: defs = [] } = useQuery({ queryKey: ['rate-series-defs'], queryFn: getRateSeriesDefs })
  const s = summary as RatesSummary | undefined
  const rows = raw as { series: string; date: string; rate: number }[]
  const defByCode = useMemo(() => new Map((defs as RateSeriesDef[]).map(d => [d.code, d])), [defs])

  const body = (() => {
    if (!s) {
      return (
        <p className="text-sm text-slate-500">
          No interest-rate data yet — use <span className="font-mono">Download Interest Rates</span> on the Downloads tab to fetch it.
        </p>
      )
    }
    const lower = new Map(s.series.filter(x => x.rate_type === 'Target Lower' && x.latest).map(x => [x.currency, x]))
    const cards = s.series.filter(x => x.latest && x.rate_type !== 'Target Lower')

    const card = (key: string, label: string, tip: string, value: React.ReactNode, date: string | undefined, extra: React.ReactNode) => (
      <div key={key} className="bg-slate-50 rounded-lg p-4 text-center min-w-[160px] flex-1">
        <p className="text-xs text-slate-500 mb-1"><Tooltip text={tip}>{label}</Tooltip></p>
        <p className="text-2xl font-bold">{value}</p>
        {date && <p className="text-xs text-slate-400 mt-0.5">{date}</p>}
        {extra}
      </div>
    )

    const groups = new Map<string, RateSeriesSummary[]>()
    for (const x of s.series) {
      if (!x.latest) continue
      const k = x.currency ?? 'Other'
      groups.set(k, [...(groups.get(k) ?? []), x])
    }
    const traceFor = (x: RateSeriesSummary, color: string) => {
      const pts = rows.filter(r => r.series === x.code)
      const stepped = x.rate_type !== 'Overnight' && x.rate_type !== 'Other'
      const dotted = x.rate_type.startsWith('Target')
      return {
        x: pts.map(r => r.date), y: pts.map(r => r.rate), name: shortName(x.name), type: 'scatter', mode: 'lines',
        line: { color, width: dotted ? 1.2 : 1.5, ...(stepped ? { shape: 'hv' } : {}), ...(dotted ? { dash: 'dot' } : {}) },
      }
    }
    const chartLayout = (title: string) => ({
      height: 300, title: { text: title, font: { size: 13 } }, margin: { t: 40, b: 40, l: 55, r: 20 },
      yaxis: plotAxis(isDark, { title: '%', ticksuffix: '%', tickformat: '.2f' }), hovermode: 'x unified',
      legend: { orientation: 'h', y: -0.15 }, ...plotLayout(isDark),
    })

    return (
      <>
        {s.alerts.length > 0 && (
          <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 space-y-2">
            <p className="text-sm font-semibold text-amber-800">Active rate alerts</p>
            {s.alerts.map((a, i) => (
              <div key={i}>
                <p className="text-sm font-medium text-slate-800">{a.title}</p>
                <p className="text-xs text-slate-600">{a.message}</p>
              </div>
            ))}
          </div>
        )}

        <div className="flex flex-wrap gap-3">
          {cards.map(x => {
            const l = x.latest!
            const lo = x.rate_type === 'Target Upper' ? lower.get(x.currency) : undefined
            const value = lo?.latest
              ? `${lo.latest.rate.toFixed(2)}–${l.rate.toFixed(2)}%`
              : `${l.rate.toFixed(rateDp(l.rate))}%`
            const label = lo ? x.name.replace(/\s*\(upper bound\)/i, '') : shortName(x.name)
            let extra: React.ReactNode = null
            if (x.change) {
              const recent = x.change.days_ago <= 14
              extra = <p className={`text-xs mt-1 ${recent ? 'text-amber-600 font-medium' : 'text-slate-400'}`}>
                {x.change.to > x.change.from ? 'Raised' : 'Cut'} {x.change.from.toFixed(2)}% → {x.change.to.toFixed(2)}% on {x.change.date}
              </p>
            } else if (x.move) {
              const big = Math.abs(x.move.diff) >= 0.15
              extra = <p className={`text-xs mt-1 ${big ? 'text-amber-600 font-medium' : 'text-slate-400'}`}>
                {x.move.diff >= 0 ? '+' : ''}{x.move.diff.toFixed(2)} pp vs 30-day avg
              </p>
            }
            return card(x.code, label, defByCode.get(x.code)?.notes ?? x.name, value, l.date, extra)
          })}
          {s.tracking.map(t => card(`trk-${t.id}`, `${t.ticker} vs ${shortName(t.series_name)}`,
            `${t.security_name} should return roughly ${t.series_name} minus its fee. Compared over the last ${t.window_days_setting} days, annualised; alerts at ${t.threshold_pp.toFixed(2)} pp or more behind.`,
            t.result ? `${t.result.gap_pp >= 0 ? '+' : ''}${t.result.gap_pp.toFixed(2)} pp` : '—',
            t.result ? `${t.result.window_days}-day gap` : 'not enough data',
            t.result && (
              <>
                <p className={`text-xs mt-1 ${t.alert ? 'text-amber-600 font-medium' : 'text-slate-400'}`}>
                  {t.ticker} {t.result.security_annualised_pct.toFixed(2)}% · {shortName(t.series_name)} {t.result.series_annualised_pct.toFixed(2)}%
                </p>
                {t.duration_years != null && <p className="text-xs mt-1 text-amber-600">⚠ {t.duration_years.toFixed(1)}-yr duration — not comparable</p>}
              </>
            )))}
        </div>

        <div className="flex items-center gap-1">
          {([['1Y', 1], ['3Y', 3], ['All', undefined]] as [string, number | undefined][]).map(([lbl, y]) => (
            <button key={lbl} onClick={() => setYears(y)}
              className={`px-3 py-1 text-xs rounded border font-medium ${years === y ? 'bg-blue-600 text-white border-blue-600' : 'border-slate-300 text-slate-600 hover:bg-slate-50'}`}>
              {lbl}
            </button>
          ))}
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          {[...groups.entries()].map(([ccy, list]) => (
            <div key={ccy}>
              <p className="text-sm font-semibold"><CurrencyLink code={ccy} /></p>
              <Plot data={list.map((x, i) => traceFor(x, SERIES_COLORS[i % SERIES_COLORS.length]))}
                layout={chartLayout('')} config={{ displayModeBar: false }} style={{ width: '100%' }} />
            </div>
          ))}
        </div>
        <p className="text-xs text-slate-400">
          Alerts (shown on the Dashboard too): a policy-rate change in the last 14 days; an overnight rate 0.15 pp or more away from its 30-day average
          (unless a policy change in the same currency already explains it); and a tracked fund lagging its rate by more than its own threshold.
        </p>
      </>
    )
  })()

  return (
    <div className="p-5 space-y-4">
      {body}
      <RatesConfig defs={defs as RateSeriesDef[]} />
    </div>
  )
}

function RatesConfig({ defs }: { defs: RateSeriesDef[] }) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [open, setOpen] = usePersist('rates_config_open', false)
  const { data: currencies = [] } = useQuery({ queryKey: ['currencies'], queryFn: getCurrencies })
  const { data: securities = [] } = useQuery({ queryKey: ['securities'], queryFn: () => getSecurities(), enabled: open })
  const { data: tracking = [] } = useQuery({ queryKey: ['rate-tracking'], queryFn: getRateTracking, enabled: open })
  const { data: durations = {} } = useQuery({ queryKey: ['rate-fund-durations'], queryFn: getRateFundDurations, enabled: open })
  const [err, setErr] = useState('')

  type SeriesForm = { id?: number; code: string; name: string; currencies_id: string; rate_type: string; provider: string
    provider_key: string; provider_field: string; sort_order: string; is_active: boolean; alerts_enabled: boolean; notes: string }
  const blankSeries: SeriesForm = { code: '', name: '', currencies_id: '', rate_type: 'Overnight', provider: 'MANUAL', provider_key: '',
    provider_field: '', sort_order: '100', is_active: true, alerts_enabled: true, notes: '' }
  const [sForm, setSForm] = useState<SeriesForm | null>(null)
  const [valueFor, setValueFor] = useState<RateSeriesDef | null>(null)
  const [vForm, setVForm] = useState({ date: todayLocal(), rate: '' })
  type TrackForm = { id?: number; securities_id: string; rate_series_id: string; window_days: string; threshold_pp: string; is_active: boolean }
  const [tForm, setTForm] = useState<TrackForm | null>(null)
  // Narrows the fund picker in the "Track a fund" modal; not part of what's saved.
  const [fundType, setFundType] = useState('')
  const [fundSearch, setFundSearch] = useState('')
  const secRows = securities as Record<string, unknown>[]
  const secTypes = useMemo(() => [...new Set(secRows.map(x => String(x.type ?? '')).filter(Boolean))].sort(), [secRows])
  const fundOptions = useMemo(() => {
    const q = fundSearch.trim().toLowerCase()
    const list = secRows.filter(x =>
      (!fundType || String(x.type) === fundType) &&
      (!q || `${x.ticker} ${x.name}`.toLowerCase().includes(q)))
    // Keep the already-chosen fund selectable even when it falls outside the current filter.
    const chosen = tForm?.securities_id ? secRows.find(x => String(x.id) === tForm.securities_id) : undefined
    return chosen && !list.includes(chosen) ? [chosen, ...list] : list
  }, [secRows, fundType, fundSearch, tForm?.securities_id])
  const openTrack = (f: TrackForm) => { setFundType(''); setFundSearch(''); setTForm(f) }

  const refresh = () => {
    for (const k of ['rate-series-defs', 'rate-tracking', 'interest-rates-summary', 'interest-rates', 'insights'])
      qc.invalidateQueries({ queryKey: [k] })
  }
  const run = async (fn: () => Promise<unknown>, done?: () => void) => {
    setErr('')
    try { await fn(); refresh(); done?.() } catch (e) { setErr(extractError(e)) }
  }

  const saveSeries = () => sForm && run(() => saveRateSeries({
    ...sForm, currencies_id: sForm.currencies_id ? Number(sForm.currencies_id) : null, sort_order: Number(sForm.sort_order) || 100,
  }), () => setSForm(null))
  const editSeries = (d: RateSeriesDef) => setSForm({
    id: d.id, code: d.code, name: d.name, currencies_id: d.currencies_id ? String(d.currencies_id) : '', rate_type: d.rate_type,
    provider: d.provider, provider_key: d.provider_key ?? '', provider_field: d.provider_field ?? '', sort_order: String(d.sort_order),
    is_active: d.is_active, alerts_enabled: d.alerts_enabled, notes: d.notes ?? '',
  })
  const removeSeries = (d: RateSeriesDef) => {
    if (window.confirm(`Delete ${d.code} and its ${d.row_count.toLocaleString()} stored values? Any fund tracking against it is removed too.`))
      run(() => deleteRateSeries(d.id))
  }
  const saveValue = () => valueFor && run(
    () => addRateValue({ series_id: valueFor.id, date: vForm.date, rate: Number(vForm.rate) }), () => { setValueFor(null); setVForm({ date: todayLocal(), rate: '' }) })
  const saveTrack = () => tForm && run(() => saveRateTracking({
    id: tForm.id, securities_id: Number(tForm.securities_id), rate_series_id: Number(tForm.rate_series_id),
    window_days: Number(tForm.window_days), threshold_pp: Number(tForm.threshold_pp), is_active: tForm.is_active,
  }), () => setTForm(null))

  const th = 'px-2 py-1.5 text-left text-xs font-medium text-slate-500'
  const td = 'px-2 py-1.5 text-xs text-slate-700'
  const sel = 'w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm'
  const overnight = defs.filter(d => d.rate_type === 'Overnight' && d.is_active)

  return (
    <div className="border-t border-slate-200 pt-4">
      <button className="text-sm font-medium text-slate-600 hover:text-slate-800" onClick={() => setOpen(!open)}>
        {open ? '▾' : '▸'} Configure rate series and tracked funds
      </button>
      {open && (
        <div className="mt-3 space-y-6">
          {err && <p className="text-xs text-red-600 bg-red-50 rounded px-3 py-1.5">{err}</p>}

          <div>
            <div className="flex items-center justify-between mb-2">
              <p className="text-sm font-medium text-slate-700">Rate series</p>
              <Button size="sm" variant="secondary" onClick={() => setSForm(blankSeries)}><Plus size={13} /> Add series</Button>
            </div>
            <div className="overflow-x-auto rounded-lg border border-slate-200">
              <table className="w-full">
                <thead className="bg-slate-50"><tr>
                  <th className={th}>Code</th><th className={th}>Name</th><th className={th}>Ccy</th><th className={th}>Type</th>
                  <th className={th}>Provider</th><th className={th}>Key</th><th className={th}>Latest</th><th className={th}>Values</th>
                  <th className={th}>Active</th><th className={th}>Alerts</th><th className={th}></th>
                </tr></thead>
                <tbody>
                  {defs.map(d => (
                    <tr key={d.id} className="border-t border-slate-100">
                      <td className={`${td} font-mono`}>{d.code}</td><td className={td}>{d.name}</td><td className={td}><CurrencyLink code={d.currency} /></td>
                      <td className={td}>{d.rate_type}</td><td className={td}>{d.provider}</td>
                      <td className={`${td} font-mono max-w-[220px] truncate`} title={`${d.provider_key ?? ''} ${d.provider_field ?? ''}`}>{d.provider_key ?? '—'}{d.provider_field ? ` · ${d.provider_field}` : ''}</td>
                      <td className={td}>{d.last_date ? String(d.last_date).slice(0, 10) : '—'}</td><td className={td}>{d.row_count.toLocaleString()}</td>
                      <td className={td}>{d.is_active ? 'Yes' : 'No'}</td><td className={td}>{d.alerts_enabled ? 'Yes' : 'No'}</td>
                      <td className={`${td} whitespace-nowrap`}>
                        {d.provider === 'MANUAL' && <button className="text-blue-600 hover:underline mr-2" onClick={() => setValueFor(d)}>+ value</button>}
                        <button onClick={() => editSeries(d)} className="text-blue-500 hover:text-blue-700 p-1" title="Edit"><Pencil size={13} /></button>
                        <button onClick={() => removeSeries(d)} className="text-red-400 hover:text-red-600 p-1" title="Delete"><Trash2 size={13} /></button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="text-xs text-slate-400 mt-1">
              To follow another rate — in any currency — add a series: ECB (an ECB Data Portal key), NYFED (a NY Fed Markets API path), FRED (a series id; needs a free
              FRED_API_KEY in the server environment — the route for JPY, GBP and others) or MANUAL (type values in with “+ value”). Set the type to Policy
              or Target Upper for a rate that changes at meetings (raises a change alert), Overnight for a daily market rate (raises a move alert).
            </p>
          </div>

          <div>
            <div className="flex items-center justify-between mb-2">
              <p className="text-sm font-medium text-slate-700">Tracked funds</p>
              <Button size="sm" variant="secondary" disabled={!overnight.length}
                onClick={() => openTrack({ securities_id: '', rate_series_id: String(overnight[0]?.id ?? ''), window_days: '90', threshold_pp: '0.25', is_active: true })}>
                <Plus size={13} /> Track a fund
              </Button>
            </div>
            <div className="overflow-x-auto rounded-lg border border-slate-200">
              <table className="w-full">
                <thead className="bg-slate-50"><tr>
                  <th className={th}>Fund</th><th className={th}>Compared with</th><th className={th}>Window (days)</th><th className={th}>Alert at (pp behind)</th><th className={th}>Active</th><th className={th}></th>
                </tr></thead>
                <tbody>
                  {(tracking as RateTrackingDef[]).length === 0 && <tr><td colSpan={6} className="px-3 py-4 text-center text-xs text-slate-400">No funds tracked.</td></tr>}
                  {(tracking as RateTrackingDef[]).map(t => (
                    <tr key={t.id} className="border-t border-slate-100">
                      <td className={td}>
                        <button onClick={() => navigate(`/securities/${t.securities_id}`)} className="text-blue-600 hover:underline text-left">
                          <span className="font-mono">{t.ticker}</span> {t.security_name}
                        </button>
                        {t.duration_years != null && (
                          <span className="ml-2 text-amber-600" title={`Duration ${t.duration_years.toFixed(1)} years — this bond fund moves with bond yields, not an overnight rate, so the comparison isn't meaningful.`}>⚠ bond fund</span>
                        )}
                      </td>
                      <td className={td}>{t.series_name}</td><td className={td}>{t.window_days}</td><td className={td}>{t.threshold_pp.toFixed(2)}</td>
                      <td className={td}>{t.is_active ? 'Yes' : 'No'}</td>
                      <td className={`${td} whitespace-nowrap`}>
                        <button onClick={() => openTrack({ id: t.id, securities_id: String(t.securities_id), rate_series_id: String(t.rate_series_id), window_days: String(t.window_days), threshold_pp: String(t.threshold_pp), is_active: t.is_active })}
                          className="text-blue-500 hover:text-blue-700 p-1" title="Edit"><Pencil size={13} /></button>
                        <button onClick={() => run(() => deleteRateTracking(t.id))} className="text-red-400 hover:text-red-600 p-1" title="Remove"><Trash2 size={13} /></button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="text-xs text-slate-400 mt-1">
              A fund that should track an overnight rate (a money-market or overnight-swap ETF) is compared with that rate compounded over the same window, annualised.
            </p>
          </div>
        </div>
      )}

      {sForm && (
        <Modal title={sForm.id ? `Edit rate series — ${sForm.code}` : 'New rate series'} onClose={() => setSForm(null)}
          footer={<>
            <Button variant="secondary" onClick={() => setSForm(null)}>Cancel</Button>
            <Button onClick={saveSeries} disabled={!sForm.code.trim() || !sForm.name.trim() || (sForm.provider !== 'MANUAL' && !sForm.provider_key.trim())}><Save size={14} /> Save</Button>
          </>}>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Code"><Input value={sForm.code} onChange={e => setSForm({ ...sForm, code: e.target.value.toUpperCase() })} placeholder="e.g. SONIA" /></Field>
            <Field label="Currency">
              <select className={sel} value={sForm.currencies_id} onChange={e => setSForm({ ...sForm, currencies_id: e.target.value })}>
                <option value="">—</option>
                {(currencies as Record<string, unknown>[]).map(c => <option key={String(c.id)} value={String(c.id)}>{String(c.code)}</option>)}
              </select>
            </Field>
          </div>
          <Field label="Name"><Input value={sForm.name} onChange={e => setSForm({ ...sForm, name: e.target.value })} placeholder="e.g. Sterling overnight index average (SONIA)" /></Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Type">
              <select className={sel} value={sForm.rate_type} onChange={e => setSForm({ ...sForm, rate_type: e.target.value })}>
                {RATE_TYPES.map(t => <option key={t}>{t}</option>)}
              </select>
            </Field>
            <Field label="Provider">
              <select className={sel} value={sForm.provider} onChange={e => setSForm({ ...sForm, provider: e.target.value })}>
                {RATE_PROVIDERS.map(p => <option key={p}>{p}</option>)}
              </select>
            </Field>
          </div>
          {sForm.provider !== 'MANUAL' && (
            <Field label="Provider key"><Input value={sForm.provider_key} onChange={e => setSForm({ ...sForm, provider_key: e.target.value })} placeholder={KEY_HINTS[sForm.provider]} /></Field>
          )}
          {sForm.provider === 'NYFED' && (
            <Field label="NY Fed field (blank = percentRate)"><Input value={sForm.provider_field} onChange={e => setSForm({ ...sForm, provider_field: e.target.value })} placeholder="percentRate, targetRateTo, targetRateFrom" /></Field>
          )}
          <div className="grid grid-cols-3 gap-3 items-end">
            <Field label="Sort order"><Input value={sForm.sort_order} onChange={e => setSForm({ ...sForm, sort_order: e.target.value })} /></Field>
            <label className="flex items-center gap-2 text-sm pb-2"><input type="checkbox" checked={sForm.is_active} onChange={e => setSForm({ ...sForm, is_active: e.target.checked })} /> Active</label>
            <label className="flex items-center gap-2 text-sm pb-2"><input type="checkbox" checked={sForm.alerts_enabled} onChange={e => setSForm({ ...sForm, alerts_enabled: e.target.checked })} /> Alerts</label>
          </div>
          <Field label="Notes"><Input value={sForm.notes} onChange={e => setSForm({ ...sForm, notes: e.target.value })} /></Field>
        </Modal>
      )}

      {valueFor && (
        <Modal title={`Add value — ${valueFor.code}`} onClose={() => setValueFor(null)}
          footer={<><Button variant="secondary" onClick={() => setValueFor(null)}>Cancel</Button>
            <Button onClick={saveValue} disabled={!vForm.date || vForm.rate === '' || Number.isNaN(Number(vForm.rate))}><Save size={14} /> Save</Button></>}>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Date"><Input type="date" value={vForm.date} onChange={e => setVForm({ ...vForm, date: e.target.value })} /></Field>
            <Field label="Rate (%)"><Input value={vForm.rate} onChange={e => setVForm({ ...vForm, rate: e.target.value })} placeholder="e.g. 0.75" /></Field>
          </div>
          <p className="text-xs text-slate-400">Saving a date that already has a value overwrites it.</p>
        </Modal>
      )}

      {tForm && (
        <Modal title="Track a fund against a rate" onClose={() => setTForm(null)}
          footer={<><Button variant="secondary" onClick={() => setTForm(null)}>Cancel</Button>
            <Button onClick={saveTrack} disabled={!tForm.securities_id || !tForm.rate_series_id || Number(tForm.window_days) < 7}><Save size={14} /> Save</Button></>}>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Show only">
              <select className={sel} value={fundType} onChange={e => setFundType(e.target.value)}>
                <option value="">All types</option>
                {secTypes.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
            </Field>
            <Field label="Search"><Input value={fundSearch} onChange={e => setFundSearch(e.target.value)} placeholder="ticker or name" /></Field>
          </div>
          <Field label={`Fund (${fundOptions.length} shown)`}>
            <select className={sel} value={tForm.securities_id} onChange={e => setTForm({ ...tForm, securities_id: e.target.value })}>
              <option value="">— choose a security —</option>
              {fundOptions.map(x => <option key={String(x.id)} value={String(x.id)}>{String(x.ticker)} — {String(x.name)} ({String(x.type)}){durations[String(x.id)] ? ' ⚠' : ''}</option>)}
            </select>
          </Field>
          {tForm.securities_id && durations[tForm.securities_id] != null && (
            <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-2">
              ⚠ This is a bond fund with a {durations[tForm.securities_id].toFixed(1)}-year duration, so its price moves with bond yields rather than an overnight rate — comparing it with one isn't meaningful and will likely raise a false alert. This check suits cash-like funds (money-market or overnight-swap ETFs, e.g. XEON).
            </p>
          )}
          <Field label="Compare with (overnight rate)">
            <select className={sel} value={tForm.rate_series_id} onChange={e => setTForm({ ...tForm, rate_series_id: e.target.value })}>
              {overnight.map(d => <option key={d.id} value={String(d.id)}>{d.name}</option>)}
            </select>
          </Field>
          <div className="grid grid-cols-3 gap-3 items-end">
            <Field label="Window (days, min 7)"><Input value={tForm.window_days} onChange={e => setTForm({ ...tForm, window_days: e.target.value })} /></Field>
            <Field label="Alert at (pp behind)"><Input value={tForm.threshold_pp} onChange={e => setTForm({ ...tForm, threshold_pp: e.target.value })} /></Field>
            <label className="flex items-center gap-2 text-sm pb-2"><input type="checkbox" checked={tForm.is_active} onChange={e => setTForm({ ...tForm, is_active: e.target.checked })} /> Active</label>
          </div>
        </Modal>
      )}
    </div>
  )
}

function CapeRatiosTab() {
  return (
    <div>
      <ShillerCapeSection />
      <div className="border-t border-slate-200" />
      <CountryCapeSection />
    </div>
  )
}

// ── Alerts Tab ────────────────────────────────────────────────────────────────
const ALERT_TYPES = ['price_above', 'price_below', 'allocation_drift']
const ASSET_TYPES_FOR_DRIFT = ['Stock', 'ETF', 'Bond', 'Mutual Fund', 'Crypto', 'Other']

function AlertsTab() {
  const qc = useQueryClient()
  const liveRefetchMs = useLiveRefetchInterval()
  const { data = [], isLoading } = useQuery({ queryKey: ['alert-definitions'], queryFn: getAlertsDefinitions, refetchInterval: liveRefetchMs })
  const { data: securities = [] } = useQuery({ queryKey: ['securities'], queryFn: () => getSecurities() })
  const rows = data as Record<string, unknown>[]
  const secs = securities as Record<string, unknown>[]

  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState<Record<string, string>>({ alert_type: 'price_above', securities_id: '', asset_type: '', threshold: '', note: '' })
  const [editId, setEditId] = useState<number | null>(null)
  const [err, setErr] = useState('')
  const [search, setSearch] = useState('')

  const { sorted, sortKey, sortDir, toggleSort } = useSortTable(rows, 'created_at', 'desc')
  const filtered = search.trim()
    ? sorted.filter(r => {
        const q = search.toLowerCase()
        return (
          String(r.securities_name ?? '').toLowerCase().includes(q) ||
          String(r.asset_type ?? '').toLowerCase().includes(q) ||
          String(r.alert_type ?? '').toLowerCase().includes(q) ||
          String(r.note ?? '').toLowerCase().includes(q)
        )
      })
    : sorted

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

  // Turns the currently-open edit into a fresh, unsaved copy — same type/security/
  // threshold/note — clearing editId so Save creates a new alert instead of updating
  // this one.
  const duplicate = () => {
    setEditId(null)
    setErr('')
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
      <div className="flex justify-between items-center gap-3">
        <div className="flex items-center gap-3 flex-1">
          <div className="relative">
            <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
            <Input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search alerts…" className="pl-8 max-w-xs" />
          </div>
          <p className="text-sm text-slate-500 whitespace-nowrap">{filtered.length} of {rows.length} alert{rows.length !== 1 ? 's' : ''}</p>
        </div>
        <Button size="sm" onClick={openAdd}><Plus size={14} /> Add Alert</Button>
      </div>

      {showForm && (
        <Modal title={editId ? 'Edit Alert' : 'Add Alert'} onClose={() => { setShowForm(false); setErr('') }}
          footer={<>
            {editId && <Button variant="destructive" onClick={() => { deleteMut.mutate(editId); setShowForm(false); setErr('') }} disabled={saveMut.isPending}><Trash2 size={14} /> Delete</Button>}
            {editId && <Button variant="secondary" onClick={duplicate} disabled={saveMut.isPending}><Copy size={14} /> Duplicate</Button>}
            <span className="flex-1" />
            <Button variant="secondary" onClick={() => { setShowForm(false); setErr('') }}>Cancel</Button>
            <Button onClick={doSave} disabled={saveMut.isPending}>Save</Button>
          </>}>
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
            {filtered.map(row => {
              const isActive = Boolean(row.is_active)
              const triggered = row.current_price != null && row.threshold != null && (
                (row.alert_type === 'price_above' && Number(row.current_price) > Number(row.threshold)) ||
                (row.alert_type === 'price_below' && Number(row.current_price) < Number(row.threshold))
              )
              return (
                <tr key={String(row.alert_id)} className={`hover:bg-slate-50 cursor-pointer ${!isActive ? 'opacity-50' : ''}`} onDoubleClick={() => openEdit(row)}>
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
                  <td className="px-3 py-2 text-right tabular-nums font-medium">{row.threshold != null ? fmtNum(Number(row.threshold), 4) : '—'}</td>
                  <td className={`px-3 py-2 text-right tabular-nums ${triggered ? 'text-red-600 font-semibold' : 'text-slate-600'}`}>
                    {row.current_price != null ? fmtNum(Number(row.current_price), 4) : '—'}
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
            {filtered.length === 0 && <tr><td colSpan={9} className="px-3 py-8 text-center text-slate-400 text-sm">{rows.length === 0 ? 'No alerts defined yet.' : 'No alerts match your search.'}</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export default function MarketData() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [savedTab, setTab] = usePersist('market_data_tab', searchParams.get('tab') ?? 'Currencies')
  // A tab saved before it was removed (e.g. the old Securities Prices / FX Prices tabs,
  // now on each Security Detail / Currency Detail → Prices) would otherwise open a blank page.
  const tab = TABS.includes(savedTab) ? savedTab : 'Currencies'
  // Deep-link support: "?tab=CAPE Ratios" (used by Dashboard's Market Valuation
  // tile) switches to that tab even when a different one was last persisted —
  // usePersist's initial value above only wins on a completely fresh visit with
  // nothing saved yet, so a returning visitor needs this to actually force the
  // switch. Cleared once consumed, so it doesn't re-fire on a later re-render
  // or a manual tab switch back.
  const deepLinkTab = searchParams.get('tab')
  useEffect(() => {
    if (deepLinkTab && TABS.includes(deepLinkTab)) {
      setTab(deepLinkTab)
      setSearchParams({}, { replace: true })
    }
  }, [deepLinkTab]) // eslint-disable-line react-hooks/exhaustive-deps
  // Persisted like `tab` above — plain useState would reset on remount, which is exactly
  // what happens when you drill into a security's own Security Detail page and hit Back,
  // silently dropping a filter you'd just typed. Still cleared explicitly on tab switches
  // below, same as before; this only fixes it surviving a navigate-away-and-back.
  const [search, setSearch] = usePersist('market_data_search', '')
  const anomalyGridCols = useGridColumnState('market-data-anomalies', ANOMALY_COLS)
  const anomalyGridScroll = useGridScrollState('market-data-anomalies')
  const anomalyGridFilter = useGridFilterState('market-data-anomalies')
  const { gridApi: anomalyGridApi, onGridReady: onAnomalyGridReady } = useGridApi(api => {
    if (anomalyGridFilter.filterModel) api.setFilterModel(anomalyGridFilter.filterModel)
  })

  const { data: anomalies = [], isLoading: anomLoading } = useQuery({
    queryKey: ['price-anomalies'],
    queryFn: () => getPriceAnomalies(100),
    enabled: tab === 'Anomalies',
  })

  return (
    <div>
      <PageHeader title="Market Data" actions={<PricesUpdatedAuto />} />

      <div className="px-6 py-4 space-y-4">
        <div className="flex gap-1 border-b border-slate-200 overflow-x-auto">
          {TABS.map(t => (
            <button key={t} onClick={() => { setTab(t); setSearch(''); setSearchParams({ tab: t }) }}
              className={`px-4 py-2 text-sm font-medium -mb-px border-b-2 transition-colors whitespace-nowrap ${tab === t ? 'border-blue-600 text-blue-600' : 'border-transparent text-slate-500 hover:text-slate-700'}`}>
              {t}
            </button>
          ))}
        </div>

        <Card>
          <CardBody className="p-0">
            {tab === 'Currencies' && <CurrenciesTab search={search} onSearchChange={setSearch} />}
            {tab === 'Securities' && <SecuritiesTab search={search} onSearchChange={setSearch} />}
            {tab === 'Downloads' && <DownloadsTab />}
            {tab === 'Anomalies' && (
              anomLoading ? <div className="flex justify-center py-12"><Spinner /></div> : (
                <div>
                  <div className="flex items-center justify-end gap-2 px-4 py-2 border-b border-slate-100 bg-slate-50">
                    {anomalyGridFilter.hasFilters && (
                      <button onClick={() => anomalyGridFilter.clearFilters(anomalyGridApi)}
                        className="px-3 py-1.5 text-xs rounded border font-medium border-slate-300 text-slate-600 hover:bg-slate-50">
                        ✕ Clear Filters
                      </button>
                    )}
                    <ColumnsMenu columns={anomalyGridCols.columns} onToggle={anomalyGridCols.toggleColumn} />
                    <CopyToExcelButton gridApi={anomalyGridApi} />
                  </div>
                  <div className="ag-theme-alpine" style={{ height: 'calc(100vh - 220px)', width: '100%' }}>
                    <AgGridReact theme="legacy" rowData={anomalies} columnDefs={anomalyGridCols.colDefs} onGridReady={onAnomalyGridReady}
                      defaultColDef={{ resizable: true, sortable: true, filter: true }} columnTypes={AG_GRID_COLUMN_TYPES}
                      initialState={anomalyGridScroll.initialState}
                      onStateUpdated={anomalyGridScroll.onStateUpdated}
                      onFilterChanged={anomalyGridFilter.onFilterChanged}
                      onColumnMoved={anomalyGridCols.onColumnMoved}
                      onColumnResized={anomalyGridCols.onColumnResized} />
                  </div>
                </div>
              )
            )}
            {tab === 'Watchlist' && <WatchlistTab />}
            {tab === 'CAPE Ratios' && <CapeRatiosTab />}
            {tab === 'Rates' && <RatesTab />}
            {tab === 'Alerts' && <AlertsTab />}
          </CardBody>
        </Card>
      </div>
    </div>
  )
}
