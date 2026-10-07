import React, { useState, useMemo } from 'react'
import { usePersist, useGridColumnState, useGridScrollState, useGridFilterState, useLiveRefetchInterval, useGridApi, useSettings } from '@/lib/hooks'
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
import { getCurrencies, getCurrencyDetail, getCurrencyHistory, getCurrencyFxEffect, getFxRates, addFxRate, deleteFxRate, importFxFromFile, FX_EFFECT_PERIODS } from '@/lib/api'
import type { CurrencyDetail as CurrencyDetailData, FxEffectPeriod, FxEffectPosition } from '@/lib/api'
import { periodToFromDate, type ChartPeriod } from '@/lib/chartPeriods'
import { PeriodSelector } from '@/components/PeriodSelector'
import { CurrencyLink } from '@/components/CurrencyLink'

// Currency counterpart of Security Detail: one currency's rate in your reporting currency
// (Tools → App Settings), its stored rate history (moved here from the former Market
// Data → FX Prices tab), your exposure to it and the currency effect on your P&L.
//
// Two different "base" currencies matter here. Rates are *shown* in the reporting
// currency, so that currency's own page has no rate. Rates are *stored* against one
// storage base (EUR) whatever the reporting currency is, so only the storage base has
// no stored history to maintain — every other currency keeps its Prices tab.

const TABS = ['Overview', 'Prices', 'Exposure', 'FX Effect'] as const
type Tab = typeof TABS[number]

const signColor = (v: number | null | undefined) => v == null ? undefined : v >= 0 ? 'text-emerald-600' : 'text-red-500'
const signed = (v: number | null | undefined, dec = 2) => v == null ? '—' : `${v >= 0 ? '+' : ''}${fmtPct(v, dec)}`

export default function CurrencyDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [tab, setTab] = usePersist<Tab>('currency_detail_tab', 'Overview')
  const curId = Number(id)
  const liveRefetchMs = useLiveRefetchInterval()
  const [{ reportingCurrency }] = useSettings()

  const { data: currenciesRaw = [], isLoading: currenciesLoading } = useQuery({ queryKey: ['currencies'], queryFn: getCurrencies })
  const currencies = currenciesRaw as Record<string, unknown>[]
  const { data: detail, isLoading } = useQuery({
    queryKey: ['currency-detail', curId, reportingCurrency],
    queryFn: () => getCurrencyDetail(curId, reportingCurrency),
    enabled: !!curId,
    refetchInterval: liveRefetchMs,
  })
  // The storage base (EUR) is what every stored rate is quoted against, so it has no
  // stored history of its own to maintain.
  const visibleTabs = TABS.filter(t => t !== 'Prices' || !detail?.is_storage_base)
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
              {activeTab === 'Prices' && <PricesTab curId={curId} code={detail.code} base={detail.storage_base} />}
              {activeTab === 'Exposure' && <ExposureTab detail={detail} />}
              {activeTab === 'FX Effect' && <FxEffectTab detail={detail} />}
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
  const q = detail.quote
  return (
    <div className="p-4 space-y-5">
      <div>
        <h2 className="text-lg font-semibold text-slate-800">{detail.name} <span className="font-mono text-slate-400">{detail.code}</span></h2>
        {detail.is_quote && (
          <p className="text-xs text-slate-500 mt-1">
            {detail.code} is your reporting currency (Tools → App Settings): amounts are shown in it, so it has no rate of its own here.
          </p>
        )}
        {detail.is_storage_base && !detail.is_quote && (
          <p className="text-xs text-slate-500 mt-1">
            Oikos stores every FX rate against {detail.storage_base}, so {detail.code} has no stored rates of its own. Its rate in {q}{' '}
            below comes from {q}'s stored rates, which you maintain on <CurrencyLink code={q} /> → Prices.
          </p>
        )}
      </div>

      {!detail.is_quote && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <StatCard label={`Rate (1 ${detail.code})`} value={rate != null ? `${fmtNum(rate, 4)} ${q}` : '—'}
            subs={[
              { text: rate ? `1 ${q} = ${fmtNum(1 / rate, 4)} ${detail.code}` : 'No rate stored yet' },
              ...(detail.rate_date ? [{ text: `as of ${detail.rate_date.slice(0, 10)}` }] : []),
            ]} />
          {(['1D', '1M', 'YTD', '1Y'] as const).map(k => {
            const v = detail.changes[k] ?? null
            return <StatCard key={k} label={`${k} change`} value={signed(v)} color={signColor(v)}
              sub={v == null ? 'Not enough history' : `${detail.code} ${v >= 0 ? 'stronger' : 'weaker'} vs ${q}`} />
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
      {!detail.is_quote && ex.total_eur !== 0 && (
        <p className="text-xs text-slate-500">
          <Tooltip text="The value of your exposure times 5% — the same figure as the 5% FX Move Impact column in Reports → Inv. Portfolio → FX Exposure.">
            A 5% move in {detail.code} against {q} changes your wealth by about <b className="text-amber-600">{fmtEur(ex.sensitivity_5pct_eur)}</b>.
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

      {!detail.is_quote && (
        <FxChart label={`${q} per 1 ${detail.code}`}
          fetch={from => getCurrencyHistory(detail.id, q, from)} queryKey={['currency-history', detail.id, q]} />
      )}
    </div>
  )
}

// Rate chart with a period selector. `fetch` decides what is plotted: Overview plots the
// rate in the reporting currency, Prices the stored rate against the storage base.
function FxChart({ label, fetch, queryKey }: {
  label: string
  fetch: (fromDate: string) => Promise<unknown>
  queryKey: unknown[]
}) {
  const { isDark } = useTheme()
  const liveRefetchMs = useLiveRefetchInterval()
  const [period, setPeriod] = usePersist<ChartPeriod>('cur_chart_period', 'YTD')
  const fromDate = periodToFromDate(period)
  const { data: history = [], isLoading } = useQuery({
    queryKey: [...queryKey, fromDate],
    queryFn: () => fetch(fromDate),
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
        <p className="text-sm text-slate-400 py-6">No rates for this period.</p>
      ) : (
        <Plot
          data={[{
            x: h.map(r => r.date), y: h.map(r => r.rate),
            type: 'scatter', mode: 'lines', line: { color: '#10b981', width: 1.5 }, name: label,
          }]}
          layout={{ height: 320, margin: { t: 10, r: 10, b: 40, l: 70 }, yaxis: plotAxis(isDark, { tickformat: '.4f', title: label }), hovermode: 'x unified', ...plotLayout(isDark) }}
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
  { field: 'rate', headerName: 'Stored Rate', flex: 1, valueFormatter: (p: { value: unknown }) => p.value != null ? fmtNum(Number(p.value), 6) : '' },
]

function PricesTab({ curId, code, base }: { curId: number; code: string; base: string }) {
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
      <p className="text-xs text-slate-500">The rates Oikos stores for {code}, as {base} per 1 {code} — every amount in {code} is converted with these.</p>
      <FxChart label={`${base} per 1 ${code} (stored)`}
        fetch={from => getFxRates(curId, from)} queryKey={['fx-history', curId]} />

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
          Expected columns: <code className="bg-slate-100 px-1 rounded">Date</code> and <code className="bg-slate-100 px-1 rounded">Rate</code> (or Close), as {base} per 1 {code}.
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
              <label className="text-xs font-medium text-slate-500 block mb-1">{base} per 1 {code}</label>
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
  const [{ reportingCurrency: rc }] = useSettings()
  const { code, exposure: ex, accounts, holdings } = detail
  const th = 'px-3 py-2 text-xs font-medium text-slate-500 uppercase tracking-wide'
  return (
    <div className="p-4 space-y-6">
      <p className="text-xs text-slate-500">
        Everything you hold in {code}, across all accounts: balances of active cash-side accounts (Brokerage and
        Margin cash excluded, as in Reports → Inv. Portfolio → FX Exposure) and securities quoted in {code}, valued
        at their latest close, converted to your reporting currency ({rc}) at the latest rates.
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
                <th className={`${th} text-right`}>Balance ({rc})</th>
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
                <th className={`${th} text-right`}>Value ({rc})</th>
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

// ── FX Effect ────────────────────────────────────────────────────────────────
// How much of your P&L came from this currency moving rather than from prices, over a
// chosen period. Per account/security quoted in the currency: realized (units sold in the
// period) and unrealized (units still held) P&L, each split into a market part (the price
// move in the currency) and an FX part (the currency's move against EUR), plus income —
// computed by GET /market-data/currencies/{id}/fx-effect (database/fx_effect.py). Cash: the
// currency's balances today revalued at the rate change over the period (an estimate).

type FxFilter = 'all' | 'open' | 'closed'

function FxEffectTab({ detail }: { detail: CurrencyDetailData }) {
  const navigate = useNavigate()
  const [{ reportingCurrency: rc }] = useSettings()
  const [period, setPeriod] = usePersist<FxEffectPeriod>('cur_fx_effect_period', 'YTD')
  const [filter, setFilter] = usePersist<FxFilter>('cur_fx_effect_filter', 'all')
  const { data, isLoading } = useQuery({
    queryKey: ['currency-fx-effect', detail.id, period],
    queryFn: () => getCurrencyFxEffect(detail.id, period),
    enabled: !detail.is_storage_base,
  })
  const positions = data?.positions ?? []
  const rows = positions.filter(p => filter === 'all' || p.status === filter)
  type Key = 'realized_market' | 'realized_fx' | 'unrealized_market' | 'unrealized_fx' | 'income'
  const sum = (list: FxEffectPosition[], k: Key) => list.reduce((s, r) => s + r[k], 0)
  const cash = data?.cash_fx ?? null
  const realizedFx = sum(positions, 'realized_fx')
  const unrealizedFx = sum(positions, 'unrealized_fx')
  const fxTotal = realizedFx + unrealizedFx + (cash ?? 0)
  const market = sum(positions, 'realized_market') + sum(positions, 'unrealized_market')
  const th = 'px-3 py-2 text-xs font-medium text-slate-500 uppercase tracking-wide whitespace-nowrap'
  const cell = (v: number, bold = false) => (
    <td className={`px-3 py-2 text-right tabular-nums whitespace-nowrap ${bold ? 'font-semibold ' : ''}${v > 0.005 ? 'text-green-700' : v < -0.005 ? 'text-red-600' : 'text-slate-400'}`}>{fmtEur(v)}</td>
  )
  const total = (r: FxEffectPosition) => r.realized_market + r.realized_fx + r.unrealized_market + r.unrealized_fx + r.income
  const anyFromCost = rows.some(r => r.from_cost)

  if (detail.is_storage_base) {
    return (
      <p className="p-4 text-sm text-slate-500">
        P&amp;L is measured in {detail.storage_base}, so holdings in {detail.code} have no currency effect of their own here. The effect of
        other currencies is on each of their pages (e.g. via Reports → Inv. Portfolio → FX Exposure).
      </p>
    )
  }

  return (
    <div className="p-4 space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex gap-1">
          {FX_EFFECT_PERIODS.map(p => (
            <button key={p} onClick={() => setPeriod(p)}
              className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${period === p ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}>
              {p}
            </button>
          ))}
        </div>
        {data && <span className="text-xs text-slate-400">{data.start ? `since the close of ${data.start}` : 'since each position was opened'}</span>}
      </div>

      {isLoading || !data ? <div className="flex justify-center py-12"><Spinner /></div> : (<>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <StatCard label={`FX effect — ${period}`} value={fmtEur(fxTotal)} color={signColor(fxTotal)}
            subs={[{ text: `Securities ${fmtEur(realizedFx + unrealizedFx)}` }, { text: `Cash ${cash != null ? fmtEur(cash) : '—'}` }]} />
          <StatCard label="Realized FX" value={fmtEur(realizedFx)} color={signColor(realizedFx)} sub="on units sold in the period" />
          <StatCard label="Unrealized FX" value={fmtEur(unrealizedFx)} color={signColor(unrealizedFx)} sub="on units still held" />
          <StatCard label="Market effect" value={fmtEur(market)} color={signColor(market)}
            sub={`price moves in ${detail.code} (realized + unrealized)`} />
          <StatCard label="Income" value={fmtEur(sum(positions, 'income'))} sub="dividends & interest received" />
        </div>
        <p className="text-xs text-slate-500">
          Per position, average cost is carried in {detail.code} and in {detail.storage_base}, starting from the position's value at the start of
          the period (for All, from its real cost). <b>Realized</b> covers units sold in the period: proceeds minus average cost. <b>Unrealized</b>{' '}
          covers units still held: today's value minus remaining cost. The <b>market</b> part is the gain in {detail.code} converted at the rate
          of the day; the <b>FX</b> part is the rest — the effect of {detail.code} moving against {detail.storage_base}. Transfers between accounts
          move units at cost without realizing a gain; splits only change the unit count. Cash is today's {detail.code} balance revalued at the
          rate change over the period, an estimate that assumes the balance stayed the same (none for All). Amounts are shown in {rc}.
        </p>

        <div>
          <div className="flex items-center justify-between gap-3 mb-2">
            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide">Securities quoted in {detail.code}</p>
            <div className="flex gap-1">
              {([['all', 'All'], ['open', 'Open'], ['closed', 'Closed']] as const).map(([k, lbl]) => (
                <button key={k} onClick={() => setFilter(k)}
                  className={`px-2.5 py-1 rounded text-xs font-medium ${filter === k ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}>
                  {lbl} ({k === 'all' ? positions.length : positions.filter(p => p.status === k).length})
                </button>
              ))}
            </div>
          </div>
          {rows.length === 0 ? <p className="text-sm text-slate-400">No {filter === 'all' ? '' : `${filter} `}positions in {detail.code} with P&amp;L in this period.</p> : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-slate-50">
                    <th className={`${th} text-left`} rowSpan={2}>Security</th>
                    <th className={`${th} text-left`} rowSpan={2}>Account</th>
                    <th className={`${th} text-left`} rowSpan={2}>Status</th>
                    <th className={`${th} text-center border-l border-slate-200`} colSpan={2}>Realized</th>
                    <th className={`${th} text-center border-l border-slate-200`} colSpan={2}>Unrealized</th>
                    <th className={`${th} text-right border-l border-slate-200`} rowSpan={2}>Income</th>
                    <th className={`${th} text-right`} rowSpan={2}>Total</th>
                  </tr>
                  <tr className="bg-slate-50 border-b border-slate-200">
                    <th className={`${th} text-right border-l border-slate-200`}>Market</th>
                    <th className={`${th} text-right`}>FX</th>
                    <th className={`${th} text-right border-l border-slate-200`}>Market</th>
                    <th className={`${th} text-right`}>FX</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {rows.map(r => (
                    <tr key={`${r.securities_id}-${r.accounts_id}`} className="hover:bg-slate-50">
                      <td className="px-3 py-2">
                        <button onClick={() => navigate(`/securities/${r.securities_id}`)} className="text-blue-600 hover:underline text-left">{r.name}</button>
                        {r.from_cost && <Tooltip text="No price or rate at the start of the period, so this position is measured from its real cost instead."><span className="ml-1 text-amber-600">*</span></Tooltip>}
                      </td>
                      <td className="px-3 py-2 text-slate-500"><AccountLink id={r.accounts_id} name={r.account} type={r.account_type} /></td>
                      <td className="px-3 py-2">
                        <span className={`text-xs px-1.5 py-0.5 rounded ${r.status === 'open' ? 'bg-blue-50 text-blue-700' : 'bg-slate-100 text-slate-500'}`}>
                          {r.status === 'open' ? 'Open' : 'Closed'}
                        </span>
                      </td>
                      {cell(r.realized_market)}{cell(r.realized_fx)}{cell(r.unrealized_market)}{cell(r.unrealized_fx)}{cell(r.income)}{cell(total(r), true)}
                    </tr>
                  ))}
                  <tr className="font-semibold bg-slate-50">
                    <td className="px-3 py-2" colSpan={3}>Total</td>
                    {cell(sum(rows, 'realized_market'), true)}{cell(sum(rows, 'realized_fx'), true)}{cell(sum(rows, 'unrealized_market'), true)}
                    {cell(sum(rows, 'unrealized_fx'), true)}{cell(sum(rows, 'income'), true)}{cell(rows.reduce((s, r) => s + total(r), 0), true)}
                  </tr>
                </tbody>
              </table>
              {anyFromCost && <p className="text-xs text-amber-600 mt-1">* No price or rate at the start of the period — measured from real cost.</p>}
            </div>
          )}
        </div>

        <div>
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">Cash in {detail.code}</p>
          <table className="text-sm">
            <tbody>
              <tr className="border-b border-slate-100"><td className="py-1.5 pr-6">Balance today</td><td className="py-1.5 text-right tabular-nums">{fmtNum(data.cash_balance, 2)} {detail.code}</td></tr>
              <tr><td className="py-1.5 pr-6">FX effect — {period}</td><td className={`py-1.5 text-right tabular-nums ${signColor(cash)}`}>{cash != null ? fmtEur(cash) : '—'}</td></tr>
            </tbody>
          </table>
        </div>
      </>)}
    </div>
  )
}
