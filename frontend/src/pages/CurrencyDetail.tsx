import React, { useState, useMemo } from 'react'
import { usePersist, useGridColumnState, useGridScrollState, useGridFilterState, useLiveRefetchInterval, useGridApi } from '@/lib/hooks'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { AgGridReact } from 'ag-grid-react'
import PlotlyReact from 'react-plotly.js'
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const Plot: React.ComponentType<any> = (PlotlyReact as any).default ?? PlotlyReact
import { ArrowLeft, Plus, Trash2, Search } from 'lucide-react'
import {
  Card, CardBody, PageHeader, Button, Input, Spinner, StatCard, ColumnsMenu, CopyToExcelButton, AG_GRID_COLUMN_TYPES, AccountLink, Tooltip,
} from '@/components/ui'
import { plotLayout, plotAxis, fmtNum, fmtPct, fmtEur, todayLocal } from '@/lib/utils'
import { useTheme } from '@/lib/theme'
import { getCurrencies, getCurrencyDetail, getFxRates, addFxRate, deleteFxRate, importFxFromFile } from '@/lib/api'
import type { CurrencyDetail as CurrencyDetailData } from '@/lib/api'
import { periodToFromDate, type ChartPeriod } from '@/lib/chartPeriods'
import { PeriodSelector } from '@/components/PeriodSelector'

// Currency counterpart of Security Detail: one currency's rate vs EUR, its history
// (moved here from the former Market Data → FX Prices tab) and your exposure to it.

const TABS = ['Overview', 'Prices', 'Exposure'] as const
type Tab = typeof TABS[number]

const signColor = (v: number | null | undefined) => v == null ? undefined : v >= 0 ? 'text-emerald-600' : 'text-red-500'
const signed = (v: number | null | undefined, dec = 2) => v == null ? '—' : `${v >= 0 ? '+' : ''}${fmtPct(v, dec)}`

export default function CurrencyDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [tab, setTab] = usePersist<Tab>('currency_detail_tab', 'Overview')
  const curId = Number(id)
  const liveRefetchMs = useLiveRefetchInterval()

  const { data: currenciesRaw = [], isLoading: currenciesLoading } = useQuery({ queryKey: ['currencies'], queryFn: getCurrencies })
  const currencies = currenciesRaw as Record<string, unknown>[]
  const { data: detail, isLoading } = useQuery({
    queryKey: ['currency-detail', curId],
    queryFn: () => getCurrencyDetail(curId),
    enabled: !!curId,
    refetchInterval: liveRefetchMs,
  })
  // EUR is the base every rate is quoted against, so it has no rate history of its own.
  const visibleTabs = TABS.filter(t => t !== 'Prices' || !detail?.is_base)
  const activeTab: Tab = visibleTabs.includes(tab) ? tab : 'Overview'

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
        <select
          className="w-full rounded-lg border border-slate-300 px-4 py-2.5 text-sm font-medium bg-white shadow-sm"
          value={curId || ''}
          disabled={currenciesLoading}
          onChange={e => { if (e.target.value) navigate(`/currencies/${e.target.value}`) }}>
          <option value="">{currenciesLoading ? 'Loading currencies…' : '— Select a currency —'}</option>
          {currencies.map(c => (
            <option key={String(c.id)} value={String(c.id)}>
              {String(c.code)} · {String(c.name)}
              {c.price_records ? ` (${Number(c.price_records).toLocaleString()} rates · last: ${String(c.rate_date ?? '').slice(0, 10)})` : ''}
            </option>
          ))}
        </select>

        {!curId ? (
          <p className="text-sm text-slate-400 py-8 text-center">Select a currency above to view its details.</p>
        ) : isLoading || !detail ? (
          <div className="flex justify-center py-12"><Spinner /></div>
        ) : (
          <Card>
            <div className="border-b border-slate-200 px-4">
              <div className="flex gap-1">
                {visibleTabs.map(t => (
                  <button key={t} onClick={() => setTab(t)}
                    className={`px-4 py-3 text-sm font-medium -mb-px border-b-2 transition-colors whitespace-nowrap ${activeTab === t ? 'border-blue-600 text-blue-600' : 'border-transparent text-slate-500 hover:text-slate-700'}`}>
                    {t}
                  </button>
                ))}
              </div>
            </div>
            <CardBody className="p-0">
              {activeTab === 'Overview' && <OverviewTab detail={detail} onShowExposure={() => setTab('Exposure')} />}
              {activeTab === 'Prices' && <PricesTab curId={curId} code={detail.code} />}
              {activeTab === 'Exposure' && <ExposureTab detail={detail} />}
            </CardBody>
          </Card>
        )}
      </div>
    </div>
  )
}

// ── Overview ─────────────────────────────────────────────────────────────────

function OverviewTab({ detail, onShowExposure }: { detail: CurrencyDetailData; onShowExposure: () => void }) {
  const navigate = useNavigate()
  const ex = detail.exposure
  const rate = detail.latest_rate
  return (
    <div className="p-4 space-y-5">
      <div>
        <h2 className="text-lg font-semibold text-slate-800">{detail.name} <span className="font-mono text-slate-400">{detail.code}</span></h2>
        {detail.is_base && (
          <p className="text-xs text-slate-500 mt-1">The euro is Oikos's base currency — every other currency's rate is quoted against it, so it has no rate history of its own.</p>
        )}
      </div>

      {!detail.is_base && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <StatCard label={`Rate (1 ${detail.code})`} value={rate != null ? `€${fmtNum(rate, 4)}` : '—'}
            subs={[
              { text: rate ? `1 EUR = ${fmtNum(1 / rate, 4)} ${detail.code}` : 'No rate stored yet' },
              ...(detail.rate_date ? [{ text: `as of ${detail.rate_date.slice(0, 10)}` }] : []),
            ]} />
          {(['1D', '1M', 'YTD', '1Y'] as const).map(k => {
            const v = detail.changes[k] ?? null
            return <StatCard key={k} label={`${k} change`} value={signed(v)} color={signColor(v)}
              sub={v == null ? 'Not enough history' : `${detail.code} ${v >= 0 ? 'stronger' : 'weaker'} vs EUR`} />
          })}
        </div>
      )}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard label="Your exposure" value={fmtEur(ex.total_eur)} onClick={onShowExposure}
          subs={[{ text: `${fmtNum(ex.total_native, 2)} ${detail.code}` }, { text: 'Cash + securities, all accounts' }]} />
        <StatCard label="Share of total" value={ex.share_pct != null ? fmtPct(ex.share_pct, 1) : '—'}
          sub="of your exposure across all currencies" />
        <StatCard label="Cash & accounts" value={fmtEur(ex.cash_eur)} onClick={onShowExposure}
          sub={`${detail.accounts.length} account(s) · ${fmtNum(ex.cash_native, 2)} ${detail.code}`} />
        <StatCard label="Securities" value={fmtEur(ex.securities_eur)} onClick={onShowExposure}
          sub={`${detail.holdings.length} position(s) · ${fmtNum(ex.securities_native, 2)} ${detail.code}`} />
      </div>
      {!detail.is_base && ex.total_eur !== 0 && (
        <p className="text-xs text-slate-500">
          <Tooltip text="Same figure as the 5% FX Move Impact column in Reports → Inv. Portfolio → FX Exposure: the EUR value of your exposure times 5%.">
            A 5% move in {detail.code} against EUR changes your wealth by about <b className="text-amber-600">{fmtEur(ex.sensitivity_5pct_eur)}</b>.
          </Tooltip>
        </p>
      )}

      <div>
        <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">Interest rates</p>
        {detail.interest_rates.length === 0 ? (
          <p className="text-xs text-slate-400">
            No rate series is defined for {detail.code}.{' '}
            <button className="text-blue-600 hover:underline" onClick={() => navigate(`/market-data?tab=${encodeURIComponent('Rates')}`)}>Add one under Market Data → Rates</button>.
          </p>
        ) : (
          <table className="text-sm">
            <tbody>
              {detail.interest_rates.map(r => (
                <tr key={r.id} className="border-b border-slate-100">
                  <td className="py-1.5 pr-6">{r.name} <span className="text-xs text-slate-400">({r.rate_type})</span></td>
                  <td className="py-1.5 pr-6 text-right tabular-nums font-semibold">{r.rate_pct != null ? `${fmtNum(r.rate_pct, 3)}%` : '—'}</td>
                  <td className="py-1.5 text-xs text-slate-400">{r.date ? r.date.slice(0, 10) : 'no data yet'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {!detail.is_base && <FxChart curId={detail.id} code={detail.code} />}
    </div>
  )
}

function FxChart({ curId, code }: { curId: number; code: string }) {
  const { isDark } = useTheme()
  const liveRefetchMs = useLiveRefetchInterval()
  const [period, setPeriod] = usePersist<ChartPeriod>('cur_chart_period', 'YTD')
  const fromDate = periodToFromDate(period)
  const { data: history = [], isLoading } = useQuery({
    queryKey: ['fx-history', curId, fromDate],
    queryFn: () => getFxRates(curId, fromDate),
    refetchInterval: liveRefetchMs,
  })
  const h = history as Record<string, unknown>[]
  const first = h.length > 1 ? Number(h[0].rate) : 0
  const pct = first ? (Number(h[h.length - 1].rate) / first - 1) * 100 : null
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-3 flex-wrap">
        <PeriodSelector value={period} onChange={setPeriod} />
        {pct != null && !isLoading && <span className={`text-sm font-semibold tabular-nums ${signColor(pct)}`}>{signed(pct)}</span>}
      </div>
      {isLoading ? <div className="flex justify-center py-12"><Spinner /></div> : h.length === 0 ? (
        <p className="text-sm text-slate-400 py-6">No {code} rates stored for this period.</p>
      ) : (
        <Plot
          data={[{
            x: h.map(r => r.date), y: h.map(r => r.rate),
            type: 'scatter', mode: 'lines', line: { color: '#10b981', width: 1.5 }, name: `EUR per 1 ${code}`,
          }]}
          layout={{ height: 320, margin: { t: 10, r: 10, b: 40, l: 70 }, yaxis: plotAxis(isDark, { tickformat: '.4f', title: `EUR per 1 ${code}` }), hovermode: 'x unified', ...plotLayout(isDark) }}
          config={{ displayModeBar: true, responsive: true }}
          style={{ width: '100%' }}
        />
      )}
    </div>
  )
}

// ── Prices (formerly Market Data → FX Prices) ────────────────────────────────

const FX_PRICES_COLS = [
  { colId: 'select', checkboxSelection: true, headerCheckboxSelection: true, width: 40, pinned: 'left' as const, sortable: false, filter: false, resizable: false },
  { field: 'date', headerName: 'Date', width: 130, sort: 'desc' as const },
  { field: 'rate', headerName: 'Rate vs EUR', flex: 1, valueFormatter: (p: { value: unknown }) => p.value != null ? fmtNum(Number(p.value), 6) : '' },
]

function PricesTab({ curId, code }: { curId: number; code: string }) {
  const gridCols = useGridColumnState('currency-detail-fx-prices', FX_PRICES_COLS)
  const gridScroll = useGridScrollState('currency-detail-fx-prices')
  const gridFilter = useGridFilterState('currency-detail-fx-prices')
  const { gridApi, onGridReady } = useGridApi(api => {
    if (gridFilter.filterModel) api.setFilterModel(gridFilter.filterModel)
  })
  const qc = useQueryClient()
  const liveRefetchMs = useLiveRefetchInterval()
  const [period] = usePersist<ChartPeriod>('cur_chart_period', 'YTD')
  const fromDate = periodToFromDate(period)
  const [fxSearch, setFxSearch] = useState('')
  const [selectedDates, setSelectedDates] = useState<string[]>([])
  const [action, setAction] = useState<'save' | 'delete'>('save')
  const [entryDate, setEntryDate] = useState(todayLocal())
  const [entryValue, setEntryValue] = useState('')
  const [msg, setMsg] = useState<string | null>(null)
  const [importFile, setImportFile] = useState<File | null>(null)
  const [importConflict, setImportConflict] = useState<'skip' | 'overwrite'>('skip')
  const [importMsg, setImportMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const { data: history = [], isLoading } = useQuery({
    queryKey: ['fx-history', curId, fromDate],
    queryFn: () => getFxRates(curId, fromDate),
    refetchInterval: liveRefetchMs,
  })
  const rows = useMemo(() => [...(history as Record<string, unknown>[])].reverse(), [history])

  // A rate change moves the latest-rate column on Market Data → Currencies and every
  // figure on this page's Overview/Exposure, so refresh those along with the history.
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['fx-history'] })
    qc.invalidateQueries({ queryKey: ['currencies'] })
    qc.invalidateQueries({ queryKey: ['currency-detail'] })
  }

  const importMut = useMutation({
    mutationFn: () => importFxFromFile(importFile!, curId, importConflict),
    onSuccess: (d) => {
      setImportMsg({ ok: true, text: `Imported ${d.inserted} row(s) — ${d.skipped} skipped (${d.total_rows} total in file).` })
      refresh()
    },
    onError: (e: { response?: { data?: { detail?: unknown } } }) => {
      const d = e.response?.data?.detail
      const text = Array.isArray(d) ? (d as { msg?: string }[]).map(x => x.msg ?? String(x)).join('; ') : (typeof d === 'string' ? d : 'Import failed')
      setImportMsg({ ok: false, text })
    },
  })
  const addFxMut = useMutation({
    mutationFn: addFxRate,
    onSuccess: () => { setMsg('FX rate saved.'); refresh(); setEntryValue('') },
    onError: (e: Error) => setMsg(`Error: ${e.message}`),
  })
  const delFxMut = useMutation({
    mutationFn: ({ cid, d }: { cid: number; d: string }) => deleteFxRate(cid, d),
    onSuccess: () => { setMsg('FX rate deleted.'); refresh() },
    onError: (e: Error) => setMsg(`Error: ${e.message}`),
  })

  const handleSubmit = () => {
    if (!entryDate) return
    setMsg(null)
    if (action === 'delete') delFxMut.mutate({ cid: curId, d: entryDate })
    else { if (!entryValue) return; addFxMut.mutate({ currency_id: curId, date: entryDate, rate: Number(entryValue) }) }
  }

  const deleteSelected = async () => {
    const dates = [...selectedDates]
    setMsg(null)
    for (const d of dates) await deleteFxRate(curId, d)
    setSelectedDates([])
    refresh()
    setMsg(`Deleted ${dates.length} record(s).`)
  }

  const isPending = addFxMut.isPending || delFxMut.isPending

  return (
    <div className="p-4 space-y-5">
      <FxChart curId={curId} code={code} />

      {isLoading ? <div className="flex justify-center py-12"><Spinner /></div> : (
        <div className="space-y-2">
          <div className="flex items-center justify-between gap-3">
            <div className="relative">
              <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
              <Input className="pl-8 w-56 h-7 text-xs" placeholder="Search…" value={fxSearch} onChange={e => setFxSearch(e.target.value)} />
            </div>
            <div className="flex items-center gap-2">
              {selectedDates.length > 0 && (
                <Button size="sm" variant="destructive" disabled={isPending} onClick={deleteSelected}>
                  <Trash2 size={13} /> Delete {selectedDates.length} selected
                </Button>
              )}
              {gridFilter.hasFilters && (
                <button onClick={() => gridFilter.clearFilters(gridApi)}
                  className="px-3 py-1.5 text-xs rounded border font-medium border-slate-300 text-slate-600 hover:bg-slate-50">
                  ✕ Clear Filters
                </button>
              )}
              <ColumnsMenu columns={gridCols.columns} onToggle={gridCols.toggleColumn} />
              <CopyToExcelButton gridApi={gridApi} />
            </div>
          </div>
          <div className="ag-theme-alpine" style={{ height: '360px', width: '100%' }}>
            <AgGridReact
              theme="legacy"
              onGridReady={onGridReady}
              rowData={rows}
              quickFilterText={fxSearch}
              rowSelection="multiple"
              onSelectionChanged={e => setSelectedDates(e.api.getSelectedRows().map((r: Record<string, unknown>) => r.date as string))}
              initialState={gridScroll.initialState}
              onStateUpdated={gridScroll.onStateUpdated}
              onFilterChanged={gridFilter.onFilterChanged}
              onColumnMoved={gridCols.onColumnMoved}
              onColumnResized={gridCols.onColumnResized}
              columnDefs={gridCols.colDefs}
              defaultColDef={{ resizable: true, sortable: true, filter: true }} columnTypes={AG_GRID_COLUMN_TYPES}
            />
          </div>
        </div>
      )}

      <div className="border-t border-slate-200 pt-4">
        <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-3">Import from File</p>
        <p className="text-xs text-slate-500 mb-3">
          Upload a tab-separated <code className="bg-slate-100 px-1 rounded">.txt</code> / <code className="bg-slate-100 px-1 rounded">.csv</code> / <code className="bg-slate-100 px-1 rounded">.tsv</code> file.
          Expected columns: <code className="bg-slate-100 px-1 rounded">Date</code> and <code className="bg-slate-100 px-1 rounded">Rate</code> (or Close), as EUR per 1 {code}.
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
                  <input type="radio" name="cdFxImportConflict" value={v} checked={importConflict === v} onChange={() => setImportConflict(v)} />
                  {v === 'skip' ? 'Skip' : 'Overwrite'}
                </label>
              ))}
            </div>
          </div>
          <Button variant="primary" disabled={!importFile || importMut.isPending}
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
              <label className="text-xs font-medium text-slate-500 block mb-1">EUR per 1 {code}</label>
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

// ── Exposure ─────────────────────────────────────────────────────────────────

function ExposureTab({ detail }: { detail: CurrencyDetailData }) {
  const navigate = useNavigate()
  const { code, exposure: ex, accounts, holdings } = detail
  const th = 'px-3 py-2 text-xs font-medium text-slate-500 uppercase tracking-wide'
  return (
    <div className="p-4 space-y-6">
      <p className="text-xs text-slate-500">
        Everything you hold in {code}, across all accounts: balances of active cash-side accounts (Brokerage and
        Margin cash excluded, as in Reports → Inv. Portfolio → FX Exposure) and securities quoted in {code}, valued
        at their latest close. EUR figures use the latest {code} rate.
      </p>

      <div>
        <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">Accounts in {code}</p>
        {accounts.length === 0 ? <p className="text-sm text-slate-400">No active account with a {code} balance.</p> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr className="bg-slate-50 border-b border-slate-200">
                <th className={`${th} text-left`}>Account</th>
                <th className={`${th} text-left`}>Type</th>
                <th className={`${th} text-right`}>Balance ({code})</th>
                <th className={`${th} text-right`}>Balance (EUR)</th>
              </tr></thead>
              <tbody className="divide-y divide-slate-100">
                {accounts.map(a => (
                  <tr key={a.id} className="hover:bg-slate-50">
                    <td className="px-3 py-2"><AccountLink id={a.id} name={a.name} type={a.type} /></td>
                    <td className="px-3 py-2 text-slate-500">{a.type}</td>
                    <td className={`px-3 py-2 text-right tabular-nums ${a.balance < 0 ? 'text-red-600' : ''}`}>{fmtNum(a.balance, 2)}</td>
                    <td className={`px-3 py-2 text-right tabular-nums ${a.balance_eur < 0 ? 'text-red-600' : ''}`}>{fmtEur(a.balance_eur)}</td>
                  </tr>
                ))}
                <tr className="font-semibold bg-slate-50">
                  <td className="px-3 py-2" colSpan={2}>Total</td>
                  <td className="px-3 py-2 text-right tabular-nums">{fmtNum(ex.cash_native, 2)}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{fmtEur(ex.cash_eur)}</td>
                </tr>
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div>
        <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">Securities quoted in {code}</p>
        {holdings.length === 0 ? <p className="text-sm text-slate-400">No held security is quoted in {code}.</p> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr className="bg-slate-50 border-b border-slate-200">
                <th className={`${th} text-left`}>Security</th>
                <th className={`${th} text-left`}>Type</th>
                <th className={`${th} text-left`}>Account</th>
                <th className={`${th} text-right`}>Quantity</th>
                <th className={`${th} text-right`}>Price ({code})</th>
                <th className={`${th} text-right`}>Value ({code})</th>
                <th className={`${th} text-right`}>Value (EUR)</th>
              </tr></thead>
              <tbody className="divide-y divide-slate-100">
                {holdings.map(h => (
                  <tr key={`${h.securities_id}-${h.accounts_id}`} className="hover:bg-slate-50">
                    <td className="px-3 py-2">
                      <button onClick={() => navigate(`/securities/${h.securities_id}`)} className="text-blue-600 hover:underline text-left">{h.name}</button>
                      {h.ticker && <span className="ml-1.5 text-xs font-mono text-slate-400">{h.ticker}</span>}
                    </td>
                    <td className="px-3 py-2 text-slate-500">{h.type}</td>
                    <td className="px-3 py-2"><AccountLink id={h.accounts_id} name={h.account} type={h.account_type} /></td>
                    <td className="px-3 py-2 text-right tabular-nums">{fmtNum(h.quantity, 4)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{h.price != null ? fmtNum(h.price, 4) : '—'}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{fmtNum(h.value, 2)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{fmtEur(h.value_eur)}</td>
                  </tr>
                ))}
                <tr className="font-semibold bg-slate-50">
                  <td className="px-3 py-2" colSpan={5}>Total</td>
                  <td className="px-3 py-2 text-right tabular-nums">{fmtNum(ex.securities_native, 2)}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{fmtEur(ex.securities_eur)}</td>
                </tr>
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
