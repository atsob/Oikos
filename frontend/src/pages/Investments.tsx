import { useState, useCallback, useMemo, useRef, useEffect } from 'react'
import { usePersist, useGridColumnState, useLiveRefetchInterval, useGridApi } from '@/lib/hooks'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AgGridReact } from 'ag-grid-react'
import type { ColDef, IDatasource, IGetRowsParams } from 'ag-grid-community'
import {
  getHoldings, getInvestments, getAccounts, getSecurities,
  updateHolding, stakingReinvest, getLinkedAccount,
  getTransactions, getPayees, getCategories,
  syncBalances, batchUpdateInvestments, batchDeleteInvestments,
  batchDeleteTransactions, batchMoveTransactions,
} from '@/lib/api'
import { PageHeader, Input, Button, Spinner, Card, ColHeader, useSortTablePersisted, SyncBalancesButton, ColumnsMenu, CopyToExcelButton, AccountOptions, BatchAccountPicker, AG_GRID_COLUMN_TYPES } from '@/components/ui'
import { fmtEur, fmtCur, fmtDate, fmtNum, fmtQty } from '@/lib/utils'
import { Plus, Save, RefreshCw, ArrowLeftRight, ArrowLeft, Search, X } from 'lucide-react'
import { InvTransferModal } from '@/components/InvTransferModal'
import { InvTransactionModal, emptyInvForm, ACTIONS, createInvestment, updateInvestment, deleteInvestment } from '@/components/InvTransactionModal'
import type { InvFormData } from '@/components/InvTransactionModal'
import { TxModal, useTxModal } from '@/components/TxModal'
import { INVESTMENT_ACCOUNT_TYPES, LINKABLE_ACCOUNT_TYPES, CASH_ACCOUNT_TYPES } from '@/lib/accountTypes'


// ── Date helpers ──────────────────────────────────────────────────────────────
function today() { return new Date().toISOString().slice(0, 10) }
function monthsAgo(n: number) {
  const d = new Date(); d.setMonth(d.getMonth() - n); return d.toISOString().slice(0, 10)
}
function ytdStart() { return `${new Date().getFullYear()}-01-01` }

const PERIODS = [
  { label: '1M', from: () => monthsAgo(1) },
  { label: '3M', from: () => monthsAgo(3) },
  { label: '6M', from: () => monthsAgo(6) },
  { label: 'YTD', from: ytdStart },
  { label: 'All', from: () => '1900-01-01' },
]

// HOLDING_COLS removed — Holdings tab now uses an inline editable table

const makeInvCols = (navigate: ReturnType<typeof useNavigate>): ColDef[] => [
  { field: 'date', headerName: 'Date', width: 100, sort: 'desc', valueFormatter: p => fmtDate(p.value) },
  { field: 'action', headerName: 'Action', width: 100, cellStyle: p => ({
    color: ['Buy'].includes(p.value) ? '#1d4ed8' : ['Sell'].includes(p.value) ? '#dc2626' : ['Dividend','Reinvest','IntInc'].includes(p.value) ? '#15803d' : '#475569',
    fontWeight: 600,
  }) },
  { field: 'ticker', headerName: 'Ticker', width: 90, cellStyle: { fontFamily: 'monospace', fontWeight: 600 } },
  { field: 'security', headerName: 'Security', flex: 2, minWidth: 160,
    // data is undefined while the infinite row model is still fetching the block
    // that will contain this row — AG Grid renders a loading placeholder for it.
    cellRenderer: (p: { value: string; data?: Record<string, unknown> }) =>
      !p.data ? null : p.data.securities_id
        ? <button onClick={() => navigate(`/securities/${p.data!.securities_id}`)} className="text-blue-600 hover:underline text-left truncate w-full">{p.value}</button>
        : <span>{p.value}</span>
  },
  { field: 'quantity', headerName: 'Qty', width: 100, type: 'numericColumn', valueFormatter: p => p.value != null ? fmtQty(Number(p.value), 8) : '—' },
  { field: 'qty_held', headerName: 'Qty Held', width: 110, type: 'numericColumn', sortable: false, valueFormatter: p => p.value != null ? fmtQty(Number(p.value), 8) : '—' },
  // Price, Commission and Total (sec) are all in the security's own native currency
  // (row.currency) — never the account's currency, so they must NOT go through the
  // reporting-currency FX conversion that fmtEur applies.
  { field: 'price', headerName: 'Price', width: 100, type: 'numericColumn', valueFormatter: p => p.value != null ? fmtCur(Number(p.value), p.data?.currency) : '—' },
  { field: 'commission', headerName: 'Commission', width: 110, type: 'numericColumn', valueFormatter: p => p.value != null ? fmtCur(Number(p.value), p.data?.currency) : '—' },
  // Tax_Amount and Total_Amount_AccCur are both stored in the account's own currency.
  { field: 'tax_amount', headerName: 'W. Tax', width: 95, type: 'numericColumn', valueFormatter: p => p.value != null ? fmtCur(Number(p.value), p.data?.account_currency) : '—', cellStyle: p => p.value != null ? { color: '#dc2626' } : null },
  { field: 'total_seccur', headerName: 'Total (sec)', width: 120, type: 'numericColumn', valueFormatter: p => p.value != null ? fmtCur(Number(p.value), p.data?.currency) : '—' },
  { field: 'total', headerName: 'Total (acc)', width: 120, type: 'numericColumn', valueFormatter: p => fmtCur(Number(p.value), p.data?.account_currency), cellStyle: { fontWeight: 600 } },
  { field: 'fx_rate', headerName: 'FX', width: 80, type: 'numericColumn', valueFormatter: p => p.value ? fmtNum(Number(p.value), 4) : '—' },
  { field: 'currency', headerName: 'Curr', width: 65 },
  { field: 'instrument_type', headerName: 'Instrument', width: 110 },
  { field: 'account', headerName: 'Account', flex: 1, minWidth: 130 },
  { field: 'cash_account', headerName: 'Cash Account', flex: 1, minWidth: 120,
    valueFormatter: p => p.value ?? '—',
    cellStyle: p => p.value ? { color: '#2563eb', fontSize: '12px' } : { color: '#cbd5e1', fontSize: '12px' } },
  { field: 'notes', headerName: 'Notes', flex: 1, minWidth: 120 },
]

// ── Cash tab columns (mirrors Register) ──────────────────────────────────────
// Amounts are in the linked cash account's own native currency, not always EUR.
function CashAmountCell({ value, currency }: { value: number; currency?: string }) {
  return <span className={`font-semibold tabular-nums ${value < 0 ? 'text-red-600' : 'text-green-700'}`}>{fmtCur(value, currency)}</span>
}
function CashBalanceCell({ value, currency }: { value: number; currency?: string }) {
  return <span className={`tabular-nums ${value < 0 ? 'text-red-600' : 'text-slate-800'}`}>{fmtCur(value, currency)}</span>
}
function CashStatusCell({ data }: { data: Record<string, unknown> }) {
  if (data.is_draft) return <span className="inline-flex items-center px-1.5 py-0.5 rounded text-xs bg-slate-100 text-slate-500">Draft</span>
  if (!data.cleared && !data.reconciled) return <span className="inline-flex items-center px-1.5 py-0.5 rounded text-xs bg-yellow-100 text-yellow-700">Pending</span>
  return (
    <span className="inline-flex items-center gap-1">
      {Boolean(data.cleared) && <span className="inline-flex items-center px-1.5 py-0.5 rounded text-xs bg-green-100 text-green-700">Cleared</span>}
      {Boolean(data.reconciled) && <span className="inline-flex items-center px-1.5 py-0.5 rounded text-xs bg-blue-100 text-blue-700">Reconciled</span>}
    </span>
  )
}

const makeCashCols = (currency: string): ColDef[] => [
  { field: 'date', headerName: 'Date', width: 100, sort: 'desc', valueFormatter: p => fmtDate(p.value) },
  { field: 'payee', headerName: 'Payee', flex: 1, minWidth: 140 },
  { field: 'description', headerName: 'Description', flex: 2, minWidth: 180 },
  { field: 'category', headerName: 'Category', flex: 1, minWidth: 140 },
  { field: 'target_account', headerName: 'Transfer To', width: 130 },
  { field: 'memo', headerName: 'Memo', width: 140 },
  { field: 'amount', headerName: 'Amount', width: 120, cellRenderer: CashAmountCell, cellRendererParams: { currency }, type: 'numericColumn', filter: 'agNumberColumnFilter' },
  { field: 'running_balance', headerName: 'Balance', width: 120, cellRenderer: CashBalanceCell, cellRendererParams: { currency }, type: 'numericColumn', filter: 'agNumberColumnFilter' },
  { colId: 'status', headerName: 'Status', width: 170, cellRenderer: CashStatusCell },
]


// ── Holdings table (view + inline edit for qty & staking) ─────────────────────
type HoldingEdit = { quantity: string; staking: boolean }

function HoldingsTable({ holdings, onSaved }: { holdings: Record<string, unknown>[]; onSaved: () => void }) {
  const navigate = useNavigate()
  const [edits, setEdits] = useState<Record<number, HoldingEdit>>({})
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const { sorted: sortedHoldings, sortKey: hSK, sortDir: hSD, toggleSort: hSort } = useSortTablePersisted(holdings, 'investments-holdings-sort', 'value_eur', 'desc')

  const getEdit = (row: Record<string, unknown>): HoldingEdit =>
    edits[Number(row.id)] ?? { quantity: String(row.quantity ?? ''), staking: Boolean(row.staking) }

  const setField = (id: number, row: Record<string, unknown>, key: keyof HoldingEdit, val: string | boolean) =>
    setEdits(prev => ({ ...prev, [id]: { ...getEdit(row), [key]: val } }))

  const changedIds = Object.keys(edits).map(Number)

  const stakingEntries = changedIds.flatMap(id => {
    const row = holdings.find(h => Number(h.id) === id)
    if (!row) return []
    const edit = edits[id]
    if (!edit.staking) return []
    const diff = (parseFloat(edit.quantity) || 0) - (Number(row.quantity) || 0)
    if (diff <= 0) return []
    return [{ accounts_id: Number(row.account_id), securities_id: Number(row.securities_id), quantity: diff, price_per_share: null, date: today() }]
  })

  const handleSave = async () => {
    setSaving(true); setMsg(null)
    try {
      for (const id of changedIds) {
        const edit = edits[id]
        await updateHolding(id, { quantity: parseFloat(edit.quantity), staking: edit.staking })
      }
      if (stakingEntries.length > 0) {
        await stakingReinvest(stakingEntries as Record<string, unknown>[])
        setMsg(`✓ Saved. Created ${stakingEntries.length} Reinvest staking entr${stakingEntries.length === 1 ? 'y' : 'ies'}.`)
      } else {
        setMsg(`✓ Saved ${changedIds.length} holding(s).`)
      }
      setEdits({})
      onSaved()
    } catch (e: unknown) {
      setMsg(`Error: ${e instanceof Error ? e.message : 'Save failed'}`)
    } finally { setSaving(false) }
  }

  // Simple/FIFO avg cost and last price are all in the security's own native
  // currency (row.currency), not EUR — only "Value (EUR)"/"Gain-Loss" below are.
  const fmtP = (v: unknown, currency?: unknown) => v != null ? fmtCur(Number(v), String(currency ?? 'EUR')) : '—'
  const gain = (row: Record<string, unknown>) =>
    Number(row.quantity) * (Number(row.last_price ?? 0) - Number(row.fifo_avg_price ?? row.simple_avg_price ?? 0)) * Number(row.fx_rate ?? 1)

  return (
    <div className="space-y-3 p-4">
      {stakingEntries.length > 0 && (
        <div className="bg-blue-50 border border-blue-200 rounded-lg px-4 py-2 text-sm text-blue-700">
          Staking: will create {stakingEntries.length} Reinvest entr{stakingEntries.length === 1 ? 'y' : 'ies'} for the quantity increase{stakingEntries.length > 1 ? 's' : ''}.
        </div>
      )}
      {msg && <div className={`rounded-lg px-4 py-2 text-sm ${msg.startsWith('Error') ? 'bg-red-50 text-red-600' : 'bg-green-50 text-green-700'}`}>{msg}</div>}

      <div className="overflow-x-auto">
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="border-b-2 border-slate-200 text-xs text-slate-500">
              <ColHeader label="Account" sortKey="account" currentKey={hSK} currentDir={hSD} onSort={hSort} className="py-2 pr-3" />
              <ColHeader label="Ticker" sortKey="ticker" currentKey={hSK} currentDir={hSD} onSort={hSort} className="py-2 pr-3" />
              <ColHeader label="Security" sortKey="security" currentKey={hSK} currentDir={hSD} onSort={hSort} className="py-2 pr-3" />
              <ColHeader label="Type" sortKey="security_type" currentKey={hSK} currentDir={hSD} onSort={hSort} className="py-2 pr-3" />
              <th className="py-2 pr-3 font-medium w-36 text-left">Quantity ✎</th>
              <th className="py-2 pr-3 font-medium text-center">Staking ✎</th>
              <ColHeader label="Simple Avg" sortKey="simple_avg_price" currentKey={hSK} currentDir={hSD} onSort={hSort} align="right" className="py-2 pr-3" />
              <ColHeader label="FIFO Avg" sortKey="fifo_avg_price" currentKey={hSK} currentDir={hSD} onSort={hSort} align="right" className="py-2 pr-3" />
              <ColHeader label="Last Price" sortKey="last_price" currentKey={hSK} currentDir={hSD} onSort={hSort} align="right" className="py-2 pr-3" />
              <ColHeader label="Curr" sortKey="currency" currentKey={hSK} currentDir={hSD} onSort={hSort} className="py-2 pr-3" />
              <ColHeader label="Value (EUR)" sortKey="value_eur" currentKey={hSK} currentDir={hSD} onSort={hSort} align="right" className="py-2 pr-3" />
              <th className="py-2 pr-3 font-medium text-right">Gain/Loss</th>
              <ColHeader label="Price Date" sortKey="price_date" currentKey={hSK} currentDir={hSD} onSort={hSort} className="py-2" />
            </tr>
          </thead>
          <tbody>
            {sortedHoldings.map(row => {
              const id = Number(row.id)
              const edit = getEdit(row)
              const changed = Boolean(edits[id])
              const gl = gain(row)
              return (
                <tr key={id} className={`border-b border-slate-100 ${changed ? 'bg-yellow-50' : 'hover:bg-slate-50'}`}>
                  <td className="py-1.5 pr-3 text-slate-500 text-xs">{String(row.account)}</td>
                  <td className="py-1.5 pr-3 font-mono font-bold text-slate-800">
                    {row.securities_id
                      ? <button onClick={() => navigate(`/securities/${row.securities_id}`)} className="text-blue-600 hover:underline font-mono font-bold">{String(row.ticker)}</button>
                      : String(row.ticker)}
                  </td>
                  <td className="py-1.5 pr-3 text-slate-700 max-w-[180px] truncate">
                    {row.securities_id
                      ? <button onClick={() => navigate(`/securities/${row.securities_id}`)} className="text-blue-600 hover:underline text-left truncate">{String(row.security)}</button>
                      : String(row.security)}
                  </td>
                  <td className="py-1.5 pr-3 text-slate-500 text-xs">{String(row.security_type ?? '')}</td>
                  <td className="py-1 pr-3">
                    <input type="number" step="any"
                      className="w-full rounded border border-slate-300 px-2 py-0.5 text-sm tabular-nums focus:outline-none focus:ring-1 focus:ring-blue-500"
                      value={edit.quantity}
                      onChange={e => setField(id, row, 'quantity', e.target.value)}
                    />
                  </td>
                  <td className="py-1 pr-3 text-center">
                    <input type="checkbox" className="rounded"
                      checked={edit.staking}
                      onChange={e => setField(id, row, 'staking', e.target.checked)}
                    />
                  </td>
                  <td className="py-1.5 pr-3 text-right tabular-nums text-slate-600">{fmtP(row.simple_avg_price, row.currency)}</td>
                  <td className="py-1.5 pr-3 text-right tabular-nums text-slate-600">{fmtP(row.fifo_avg_price, row.currency)}</td>
                  <td className="py-1.5 pr-3 text-right tabular-nums text-slate-600">{fmtP(row.last_price, row.currency)}</td>
                  <td className="py-1.5 pr-3 text-slate-500 text-xs">{String(row.currency)}</td>
                  <td className="py-1.5 pr-3 text-right tabular-nums font-semibold">{fmtEur(Number(row.value_eur ?? 0))}</td>
                  <td className={`py-1.5 pr-3 text-right tabular-nums font-semibold ${gl >= 0 ? 'text-green-700' : 'text-red-600'}`}>{fmtEur(gl)}</td>
                  <td className="py-1.5 text-slate-400 text-xs">{row.price_date ? String(row.price_date).slice(0, 10) : '—'}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <div className="flex justify-end gap-2 pt-1">
        <Button variant="secondary" size="sm" disabled={changedIds.length === 0} onClick={() => { setEdits({}); setMsg(null) }}>Reset</Button>
        <Button size="sm" disabled={saving || changedIds.length === 0} onClick={handleSave}>
          <Save size={14} /> {saving ? 'Saving…' : `Save${changedIds.length > 0 ? ` (${changedIds.length})` : ''}`}
        </Button>
      </div>
    </div>
  )
}


// ── Main page ─────────────────────────────────────────────────────────────────
export default function Investments() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const liveRefetchMs = useLiveRefetchInterval()
  const [tab, setTab] = usePersist<'holdings' | 'transactions' | 'cash'>('investments_tab', 'holdings')
  const [accountId, setAccountId] = usePersist<number | null>('investments_accountId', null)
  const [searchParams, setSearchParams] = useSearchParams()
  // Arriving here via an AccountLink from a report ("?accountsId=123&tab=transactions
  // &from=report") — scope to that account/tab and show a Back button. Consumed once:
  // accountsId/tab are cleared right after so navigating around afterward doesn't keep
  // re-forcing them, and a plain page refresh doesn't re-trigger it.
  const cameFromReport = searchParams.get('from') === 'report'
  useEffect(() => {
    const incomingAccount = searchParams.get('accountsId')
    const incomingTab = searchParams.get('tab')
    if (incomingAccount == null && incomingTab == null) return
    if (incomingAccount != null) setAccountId(Number(incomingAccount))
    if (incomingTab === 'holdings' || incomingTab === 'transactions' || incomingTab === 'cash') setTab(incomingTab)
    setSearchParams(prev => { const p = new URLSearchParams(prev); p.delete('accountsId'); p.delete('tab'); return p }, { replace: true })
  }, [searchParams, setAccountId, setTab, setSearchParams])
  const [showInactive, setShowInactive] = useState(false)
  const [includeClosed, setIncludeClosed] = useState(false)
  const [fromDate, setFromDate] = useState(monthsAgo(6))
  const [toDate, setToDate] = useState('2099-12-31')
  const [activePeriod, setActivePeriod] = useState('6M')
  const [actionFilter, setActionFilter] = useState('')
  const [txSearch, setTxSearch] = useState('')
  const [cashSearch, setCashSearch] = useState('')
  const PAGE_SIZE = 200

  const [modalOpen, setModalOpen] = useState(false)
  const [editId, setEditId] = useState<number | null>(null)
  const [form, setForm] = useState<InvFormData>(emptyInvForm())
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [transferOpen, setTransferOpen] = useState(false)

  const [selectedTxIds, setSelectedTxIds] = useState<number[]>([])
  const [batchAction, setBatchAction] = useState<'account' | 'cash' | null>(null)
  const [batchSaving, setBatchSaving] = useState(false)
  const [batchMsg, setBatchMsg] = useState<string | null>(null)
  const [batchWarnings, setBatchWarnings] = useState<{ account: string; security: string; quantity: number }[]>([])
  const txGridRef = useRef<AgGridReact>(null)

  const [selectedCashIds, setSelectedCashIds] = useState<number[]>([])
  const [cashBatchMoveOpen, setCashBatchMoveOpen] = useState(false)
  const [cashBatchSaving, setCashBatchSaving] = useState(false)
  const [cashBatchMsg, setCashBatchMsg] = useState<string | null>(null)
  const cashGridRef = useRef<AgGridReact>(null)

  const { data: accounts = [] } = useQuery({ queryKey: ['accounts'], queryFn: () => getAccounts(), refetchInterval: liveRefetchMs })
  const { data: securities = [] } = useQuery({ queryKey: ['securities'], queryFn: () => getSecurities() })

  const investmentAccounts = (accounts as Record<string, unknown>[])
    .filter(a => INVESTMENT_ACCOUNT_TYPES.includes(String(a.type ?? '')))
    .filter(a => showInactive || Boolean(a.is_active))

  // Batch "Change Cash Account" target list — same account-type scope as an
  // investment account's Linked Account setting in Static Data (Cash, Checking,
  // Savings, Credit Card): the types that make sense as a cash settlement account.
  const cashAccountsForBatch = (accounts as Record<string, unknown>[])
    .filter(a => LINKABLE_ACCOUNT_TYPES.includes(String(a.type ?? '')))
    .filter(a => Boolean(a.is_active))

  // Cash tab's own "Move to Account" target list — the full cash-type universe
  // (same as Cash Register's own account picker), broader than the narrower
  // LINKABLE_ACCOUNT_TYPES above (which scopes an investment account's own
  // settlement-account setting, not general cash-transaction account moves).
  const cashMoveAccounts = (accounts as Record<string, unknown>[])
    .filter(a => CASH_ACCOUNT_TYPES.includes(String(a.type ?? '')))
    .filter(a => Boolean(a.is_active))

  // The transaction being edited may belong to an inactive account that's
  // filtered out of investmentAccounts above — without this, the modal's
  // account <select> would have no matching <option>, silently showing no
  // account selected even though form.accounts_id is set correctly.
  const modalAccounts = useMemo(() => {
    if (!form.accounts_id || investmentAccounts.some(a => String(a.id) === form.accounts_id)) return investmentAccounts
    const current = (accounts as Record<string, unknown>[]).find(a => String(a.id) === form.accounts_id)
    return current ? [...investmentAccounts, current] : investmentAccounts
  }, [investmentAccounts, accounts, form.accounts_id])

  const { data: holdings = [], isLoading: holdingsLoading } = useQuery({
    queryKey: ['holdings', accountId, includeClosed],
    queryFn: () => getHoldings(accountId ?? undefined, includeClosed),
    refetchInterval: liveRefetchMs,
  })

  // Grid refresh (server-side, so no more query-cache invalidation for the transactions
  // grid — just tell the infinite row model's currently-loaded blocks to refetch).
  const refreshTxGrid = useCallback(() => {
    txGridRef.current?.api?.refreshInfiniteCache()
  }, [])

  // Transaction count for the "N transactions" label — updated from whatever the
  // datasource's most recent response said; reset on any filter change so a stale
  // count from the previous account/date range/search/action doesn't linger.
  const [txTotalCount, setTxTotalCount] = useState<number | null>(null)
  useEffect(() => { setTxTotalCount(null) }, [accountId, fromDate, toDate, actionFilter, txSearch])

  // The account's own ledger balance shown in the page subtitle — the newest
  // transaction's running_balance, from whichever block happened to load row 0
  // (always the first block fetched, since the grid defaults to date-desc sort).
  const [invLatestBalance, setInvLatestBalance] = useState<number | null>(null)
  useEffect(() => { setInvLatestBalance(null) }, [accountId, fromDate, toDate, actionFilter, txSearch])

  // AG Grid Infinite Row Model datasource: fetches one block of rows at a time as the
  // user scrolls, with sorting done server-side (see investments.py's whitelist) rather
  // than only sorting whatever page happened to already be loaded. Recreated whenever
  // the account/date range/action/search changes, which — since it's a new object — is
  // what tells the grid to throw away its cache and reload from scratch.
  const invDatasource = useMemo<IDatasource>(() => ({
    getRows: (params: IGetRowsParams) => {
      const sortModel = params.sortModel[0]
      getInvestments({
        account_id: accountId ?? undefined,
        from_date: fromDate,
        to_date: toDate,
        action: actionFilter || undefined,
        search: txSearch || undefined,
        sort_by: sortModel?.colId,
        sort_dir: sortModel?.sort,
        limit: params.endRow - params.startRow,
        offset: params.startRow,
      }).then((result: { total: number; investments: Record<string, unknown>[] }) => {
        setTxTotalCount(result.total)
        if (params.startRow === 0) {
          setInvLatestBalance(result.investments.length > 0 ? Number(result.investments[0].running_balance ?? 0) : 0)
        }
        const lastRow = result.total <= params.endRow ? result.total : -1
        params.successCallback(result.investments, lastRow)
      }).catch(() => params.failCallback())
    },
  }), [accountId, fromDate, toDate, actionFilter, txSearch])

  // ── Cash tab state ────────────────────────────────────────────────────────
  const [cashFromDate, setCashFromDate] = useState(monthsAgo(6))
  const [cashToDate, setCashToDate] = useState('2099-12-31')
  const [cashActivePeriod, setCashActivePeriod] = useState('6M')
  // Shared with Cash Register — same modal, same save/delete/transfer/installment/
  // recurring-template logic, instead of a separately-maintained copy that can drift.
  const cashTx = useTxModal({
    onSaved: () => {
      qc.invalidateQueries({ queryKey: ['inv-cash'] })
      qc.invalidateQueries({ queryKey: ['accounts'], exact: false })
    },
  })

  const { data: payees = [] } = useQuery({ queryKey: ['payees'], queryFn: () => getPayees() })
  const { data: categories = [] } = useQuery({ queryKey: ['categories'], queryFn: () => getCategories() })

  const cashParams = { account_id: accountId ?? undefined, from_date: cashFromDate, to_date: cashToDate, limit: PAGE_SIZE }
  const { data: cashData, isLoading: cashLoading } = useQuery({
    queryKey: ['inv-cash', cashParams],
    queryFn: () => getTransactions(cashParams),
    enabled: tab === 'cash' && accountId != null,
    refetchInterval: liveRefetchMs,
  })
  const cashRows = ((cashData as { transactions?: Record<string, unknown>[] } | null)?.transactions ?? []) as Record<string, unknown>[]

  const totalValue = (holdings as Record<string, unknown>[]).reduce((s, h) => s + Number(h.value_eur ?? 0), 0)
  const totalGain = (holdings as Record<string, unknown>[]).reduce((s, h) =>
    s + Number(h.quantity) * (Number(h.last_price ?? 0) - Number(h.fifo_avg_price ?? h.simple_avg_price ?? 0)) * Number(h.fx_rate ?? 1), 0)

  const selectedAccount = investmentAccounts.find(a => Number(a.id) === accountId)
  const cashColDefs = useMemo(() => [
    { colId: 'select', checkboxSelection: true, headerCheckboxSelection: true, width: 40, pinned: 'left' as const, sortable: false, filter: false, resizable: false },
    ...makeCashCols(String(selectedAccount?.currency ?? 'EUR')),
  ], [selectedAccount])
  const cashGridCols = useGridColumnState('investments-cash', cashColDefs)
  const { gridApi: cashGridApi, onGridReady: onCashGridReady } = useGridApi(api => api.autoSizeAllColumns())

  const txColDefs = useMemo(() => [
    { colId: 'select', checkboxSelection: true, headerCheckboxSelection: true, width: 40, pinned: 'left' as const, sortable: false, filter: false, resizable: false },
    ...makeInvCols(navigate),
    { field: 'running_balance', headerName: 'Balance', width: 120, type: 'numericColumn' as const, pinned: 'right' as const, sortable: false,
      cellRenderer: CashBalanceCell,
    },
  ], [navigate])
  const txGridCols = useGridColumnState('investments-transactions', txColDefs)
  const { gridApi: txGridApi, onGridReady: onTxGridReady } = useGridApi(api => api.autoSizeAllColumns())

  const setPeriod = (label: string, from: string) => {
    setFromDate(from); setActivePeriod(label)
  }

  const handleSave = async () => {
    setSaving(true); setSaveError(null)
    const payload = {
      accounts_id: Number(form.accounts_id),
      securities_id: form.securities_id ? Number(form.securities_id) : null,
      date: form.date,
      action: form.action,
      quantity: form.quantity ? parseFloat(form.quantity) : null,
      price_per_share: form.price_per_share ? parseFloat(form.price_per_share) : null,
      commission: form.commission ? parseFloat(form.commission) : null,
      fx_rate: form.fx_rate ? parseFloat(form.fx_rate) : 1,
      total_amount_acccur: form.total_amount_acccur ? parseFloat(form.total_amount_acccur) : null,
      total_amount_seccur: form.total_amount_seccur ? parseFloat(form.total_amount_seccur) : null,
      tax_amount: form.tax_amount ? parseFloat(form.tax_amount) : null,
      instrument_type: form.instrument_type || null,
      description: form.description || null,
      cash_account_id: form.cash_account_id ? Number(form.cash_account_id) : null,
    }
    try {
      if (editId) { await updateInvestment(editId, payload) } else { await createInvestment(payload) }
      refreshTxGrid()
      qc.invalidateQueries({ queryKey: ['holdings'] })
      setModalOpen(false); setEditId(null); setForm(emptyInvForm())
    } catch (e: unknown) {
      setSaveError(e instanceof Error ? e.message : 'Save failed')
    } finally { setSaving(false) }
  }

  const handleInvDelete = async () => {
    if (!editId || !confirm('Delete this investment transaction?')) return
    setSaving(true); setSaveError(null)
    try {
      await deleteInvestment(editId)
      refreshTxGrid()
      qc.invalidateQueries({ queryKey: ['holdings'] })
      setModalOpen(false); setEditId(null); setForm(emptyInvForm())
    } catch (e: unknown) {
      setSaveError(e instanceof Error ? e.message : 'Delete failed')
    } finally { setSaving(false) }
  }

  const handleBatchApply = async (targetAccountId: number) => {
    setBatchSaving(true); setBatchMsg(null)
    try {
      const changes = batchAction === 'account' ? { accounts_id: targetAccountId } : { cash_account_id: targetAccountId }
      const res = await batchUpdateInvestments(selectedTxIds, changes)
      refreshTxGrid()
      qc.invalidateQueries({ queryKey: ['holdings'], exact: false })
      qc.invalidateQueries({ queryKey: ['accounts'], exact: false })
      qc.invalidateQueries({ queryKey: ['inv-cash'], exact: false })
      txGridRef.current?.api.deselectAll()
      setSelectedTxIds([])
      setBatchAction(null)
      setBatchWarnings(res.warnings ?? [])
      setBatchMsg(`Updated ${res.updated} transaction${res.updated === 1 ? '' : 's'}.`)
    } catch (e: unknown) {
      setBatchMsg(e instanceof Error ? `Error: ${e.message}` : 'Batch update failed')
    } finally { setBatchSaving(false) }
  }

  const handleBatchDelete = async () => {
    if (!confirm(`Delete ${selectedTxIds.length} investment transaction${selectedTxIds.length === 1 ? '' : 's'}? This cannot be undone.`)) return
    setBatchSaving(true); setBatchMsg(null)
    try {
      const res = await batchDeleteInvestments(selectedTxIds)
      refreshTxGrid()
      qc.invalidateQueries({ queryKey: ['holdings'], exact: false })
      qc.invalidateQueries({ queryKey: ['accounts'], exact: false })
      qc.invalidateQueries({ queryKey: ['inv-cash'], exact: false })
      txGridRef.current?.api.deselectAll()
      setSelectedTxIds([])
      setBatchWarnings([])
      setBatchMsg(`Deleted ${res.deleted} transaction${res.deleted === 1 ? '' : 's'}.`)
    } catch (e: unknown) {
      setBatchMsg(e instanceof Error ? `Error: ${e.message}` : 'Batch delete failed')
    } finally { setBatchSaving(false) }
  }

  const cashBatchInvalidate = () => {
    qc.invalidateQueries({ queryKey: ['inv-cash'], exact: false })
    qc.invalidateQueries({ queryKey: ['accounts'], exact: false })
    qc.invalidateQueries({ queryKey: ['holdings'], exact: false })
  }

  const handleCashBatchMove = async (targetAccountId: number) => {
    setCashBatchSaving(true); setCashBatchMsg(null)
    try {
      const res = await batchMoveTransactions(selectedCashIds, targetAccountId)
      cashBatchInvalidate()
      cashGridRef.current?.api.deselectAll()
      setSelectedCashIds([])
      setCashBatchMoveOpen(false)
      setCashBatchMsg(`Moved ${res.moved} transaction${res.moved === 1 ? '' : 's'}.`)
    } catch (e: unknown) {
      setCashBatchMsg(e instanceof Error ? `Error: ${e.message}` : 'Batch move failed')
    } finally { setCashBatchSaving(false) }
  }

  const handleCashBatchDelete = async () => {
    if (!confirm(`Delete ${selectedCashIds.length} transaction${selectedCashIds.length === 1 ? '' : 's'}? This cannot be undone.`)) return
    setCashBatchSaving(true); setCashBatchMsg(null)
    try {
      const res = await batchDeleteTransactions(selectedCashIds)
      cashBatchInvalidate()
      cashGridRef.current?.api.deselectAll()
      setSelectedCashIds([])
      const skippedMsg = res.skipped.length > 0 ? ` ${res.skipped.length} skipped: ${res.skipped.map(s => s.reason).join(' ')}` : ''
      setCashBatchMsg(`Deleted ${res.deleted} transaction${res.deleted === 1 ? '' : 's'}.${skippedMsg}`)
    } catch (e: unknown) {
      setCashBatchMsg(e instanceof Error ? `Error: ${e.message}` : 'Batch delete failed')
    } finally { setCashBatchSaving(false) }
  }

  const openEdit = useCallback((row: Record<string, unknown>) => {
    const acc = (accounts as Record<string, unknown>[]).find(a => String(a.name) === String(row.account))
    const sec = (securities as Record<string, unknown>[]).find(s => String(s.ticker) === String(row.ticker))
    const accId = acc ? String(acc.id) : ''
    const baseForm: InvFormData = {
      accounts_id: accId,
      securities_id: sec ? String(sec.id) : '',
      date: String(row.date ?? '').slice(0, 10),
      action: String(row.action ?? 'Buy'),
      quantity: row.quantity != null ? String(row.quantity) : '',
      price_per_share: row.price != null ? String(row.price) : '',
      commission: row.commission != null ? String(row.commission) : '',
      fx_rate: row.fx_rate != null ? String(row.fx_rate) : '1',
      total_amount_acccur: row.total != null ? String(row.total) : '',
      total_amount_seccur: row.total_seccur != null ? String(row.total_seccur) : '',
      tax_amount: row.tax_amount != null ? String(row.tax_amount) : '',
      instrument_type: String(row.instrument_type ?? ''),
      description: String(row.notes ?? ''),
      // Use the cash account from the existing linked transaction; fall back to the account's configured linked account
      cash_account_id: row.cash_account_id != null ? String(row.cash_account_id) : '',
    }
    setEditId(Number(row.id))
    setForm(baseForm)
    setSaveError(null); setModalOpen(true)
    // Only query linked account if there is no existing cash link
    if (accId && !row.cash_account_id) {
      getLinkedAccount(Number(accId)).then(r => {
        setForm(f => ({ ...f, cash_account_id: r.linked_account_id ? String(r.linked_account_id) : '' }))
      }).catch(() => {})
    }
  }, [accounts, securities])

  return (
    <div>
      <PageHeader
        title="Investments"
        subtitle={(() => {
          const accName = selectedAccount ? String(selectedAccount.name) + ' · ' : ''
          if (tab === 'holdings') return `${accName}Portfolio: ${fmtEur(totalValue)} · Unrealized: ${fmtEur(totalGain)}`
          // totalValue (EUR, summed across holdings) takes priority; the running-balance
          // fallback is the account's own ledger balance, in the account's native currency.
          if (selectedAccount) return `${accName}Balance: ${totalValue !== 0 ? fmtEur(totalValue) : fmtCur(invLatestBalance ?? 0, String(selectedAccount.currency ?? 'EUR'))}`
          return 'Investment transaction history'
        })()}
        actions={
          <div className="flex items-center gap-2">
            {cameFromReport && (
              <>
                <Button size="sm" variant="secondary" onClick={() => navigate(-1)}>
                  <ArrowLeft size={13} /> Back
                </Button>
                <div className="w-px h-5 bg-slate-200" />
              </>
            )}
            <Button size="sm" disabled={!accountId} title={!accountId ? 'Select an account first' : undefined}
              onClick={() => {
                setEditId(null)
                setForm({ ...emptyInvForm(), accounts_id: String(accountId ?? '') })
                setSaveError(null); setModalOpen(true)
                getLinkedAccount(accountId!).then(r => {
                  setForm(f => ({ ...f, cash_account_id: r.linked_account_id ? String(r.linked_account_id) : '' }))
                }).catch(() => {})
              }}>
              <Plus size={14} /> New Transaction
            </Button>
            <Button size="sm" variant="secondary" onClick={() => setTransferOpen(true)}>
              <ArrowLeftRight size={14} /> Transfer
            </Button>
            <SyncBalancesButton
              options={[
                { label: '📈 Investments', target: 'investment' },
                { label: '🏛️ Pension', target: 'pension' },
                { label: '📊 Holdings', target: 'holdings' },
              ]}
              onSync={async target => {
                await syncBalances(target)
                await qc.invalidateQueries({ queryKey: ['accounts'], exact: false })
                await qc.invalidateQueries({ queryKey: ['holdings'], exact: false })
                refreshTxGrid()
                await qc.invalidateQueries({ queryKey: ['inv-cash'], exact: false })
              }}
            />
          </div>
        }
      />

      {/* Tabs */}
      <div className="px-6 pt-4 flex gap-1 border-b border-slate-200">
        {(['holdings', 'transactions', 'cash'] as const).map(t => (
          <button key={t} onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm font-medium -mb-px border-b-2 capitalize transition-colors ${tab === t ? 'border-blue-600 text-blue-600' : 'border-transparent text-slate-500 hover:text-slate-700'}`}>
            {t}
          </button>
        ))}
      </div>

      <div className="px-6 py-4 space-y-4">
        {/* Filters */}
        <div className="flex items-center gap-3 flex-wrap">
          <select className="w-52 rounded-md border border-slate-300 px-3 py-1.5 text-sm" value={accountId ?? ''} onChange={e => setAccountId(Number(e.target.value) || null)}>
            <option value="">All accounts</option>
            <AccountOptions accounts={investmentAccounts as Record<string, unknown>[]} />
          </select>
          <label className="flex items-center gap-1.5 text-xs text-slate-500 cursor-pointer select-none">
            <input type="checkbox" checked={showInactive} onChange={e => setShowInactive(e.target.checked)} className="rounded" />
            Show inactive
          </label>

          {tab === 'transactions' && (
            <>
              {/* Period shortcuts */}
              <div className="flex gap-1">
                {PERIODS.map(p => (
                  <button key={p.label} onClick={() => setPeriod(p.label, p.from())}
                    className={`px-2.5 py-1 text-xs rounded border transition-colors ${activePeriod === p.label ? 'bg-blue-600 text-white border-blue-600' : 'border-slate-300 text-slate-600 hover:bg-slate-50'}`}>
                    {p.label}
                  </button>
                ))}
              </div>
              <Input type="date" className="w-36" value={fromDate} onChange={e => { setFromDate(e.target.value); setActivePeriod('') }} />
              <span className="text-slate-400 text-sm">to</span>
              <div className="flex items-center gap-2">
                <Input type="date" className="w-36" value={toDate} onChange={e => setToDate(e.target.value)} />
                <label className="flex items-center gap-1.5 cursor-pointer whitespace-nowrap">
                  <input type="checkbox" className="rounded" checked={toDate === today()} onChange={e => setToDate(e.target.checked ? today() : '2099-12-31')} />
                  <span className="text-xs text-slate-500">Today</span>
                </label>
              </div>
              <select className="rounded-md border border-slate-300 px-3 py-1.5 text-sm" value={actionFilter} onChange={e => setActionFilter(e.target.value)}>
                <option value="">All actions</option>
                {ACTIONS.map(a => <option key={a}>{a}</option>)}
              </select>
            </>
          )}

          {tab === 'cash' && accountId && (
            <>
              <div className="flex gap-1">
                {PERIODS.map(p => (
                  <button key={p.label} onClick={() => { setCashFromDate(p.from()); setCashActivePeriod(p.label) }}
                    className={`px-2.5 py-1 text-xs rounded border transition-colors ${cashActivePeriod === p.label ? 'bg-blue-600 text-white border-blue-600' : 'border-slate-300 text-slate-600 hover:bg-slate-50'}`}>
                    {p.label}
                  </button>
                ))}
              </div>
              <Input type="date" className="w-36" value={cashFromDate} onChange={e => { setCashFromDate(e.target.value); setCashActivePeriod('') }} />
              <span className="text-slate-400 text-sm">to</span>
              <div className="flex items-center gap-2">
                <Input type="date" className="w-36" value={cashToDate} onChange={e => setCashToDate(e.target.value)} />
                <label className="flex items-center gap-1.5 cursor-pointer whitespace-nowrap">
                  <input type="checkbox" className="rounded" checked={cashToDate === today()} onChange={e => setCashToDate(e.target.checked ? today() : '2099-12-31')} />
                  <span className="text-xs text-slate-500">Today</span>
                </label>
              </div>
              <Button size="sm" onClick={() => cashTx.openNew(accountId!)} disabled={!accountId}>
                <Plus size={14} /> New
              </Button>
            </>
          )}

          {tab === 'holdings' && (
            <>
              <label className="flex items-center gap-1.5 text-xs text-slate-500 cursor-pointer select-none">
                <input type="checkbox" checked={includeClosed} onChange={e => setIncludeClosed(e.target.checked)} className="rounded" />
                Include closed positions
              </label>
              <Button size="sm" variant="secondary" onClick={() => {
                import('@/lib/api').then(m => m.syncBalances('holdings')).then(() => {
                  qc.invalidateQueries({ queryKey: ['holdings'] })
                })
              }}>
                <RefreshCw size={13} /> Update Holdings
              </Button>
            </>
          )}
        </div>

        <Card className="overflow-hidden">
          {tab === 'holdings' && (
            holdingsLoading
              ? <div className="flex justify-center py-12"><Spinner /></div>
              : <HoldingsTable
                  holdings={holdings as Record<string, unknown>[]}
                  onSaved={() => qc.invalidateQueries({ queryKey: ['holdings'] })}
                />
          )}

          {tab === 'cash' && (
            <>
              {!accountId ? (
                <div className="flex justify-center py-12 text-slate-400 text-sm">Select an account to view cash transactions.</div>
              ) : cashLoading ? (
                <div className="flex justify-center py-12"><Spinner /></div>
              ) : (
                <>
                  {cashBatchMsg && (
                    <div className={`mx-4 mt-2 rounded-lg px-4 py-2 text-sm flex items-start justify-between gap-3 ${cashBatchMsg.startsWith('Error') ? 'bg-red-50 text-red-600' : 'bg-green-50 text-green-700'}`}>
                      <div>{cashBatchMsg}</div>
                      <button onClick={() => setCashBatchMsg(null)} className="text-slate-400 hover:text-slate-600 shrink-0"><X size={14} /></button>
                    </div>
                  )}
                  <div className="flex items-center justify-between gap-2 px-4 py-2 border-b border-slate-100 bg-slate-50 flex-wrap">
                    <div className="flex items-center gap-3 flex-wrap">
                      <div className="relative">
                        <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
                        <Input className="pl-8 w-56" placeholder="Search…" value={cashSearch} onChange={e => setCashSearch(e.target.value)} />
                      </div>
                      <span className="text-xs text-slate-400 whitespace-nowrap">{cashRows.length} transactions</span>
                    </div>
                    <div className="flex items-center gap-2">
                      {selectedCashIds.length > 0 && (
                        <>
                          <span className="text-xs text-slate-500 whitespace-nowrap">{selectedCashIds.length} selected</span>
                          <Button variant="secondary" size="sm" onClick={() => setCashBatchMoveOpen(true)}>Move to Account…</Button>
                          <Button variant="destructive" size="sm" disabled={cashBatchSaving} onClick={handleCashBatchDelete}>Delete Selected</Button>
                        </>
                      )}
                      <ColumnsMenu columns={cashGridCols.columns} onToggle={cashGridCols.toggleColumn} />
                      <CopyToExcelButton gridApi={cashGridApi} />
                    </div>
                  </div>
                  <div className="ag-theme-alpine" style={{ height: 'calc(100vh - 320px)', width: '100%' }}>
                    <AgGridReact
                      theme="legacy"
                      ref={cashGridRef}
                      rowData={cashRows}
                      quickFilterText={cashSearch}
                      columnDefs={cashGridCols.colDefs}
                      defaultColDef={{ resizable: true, sortable: true, filter: true }}
                      columnTypes={AG_GRID_COLUMN_TYPES}
                      rowSelection="multiple"
                      onSelectionChanged={e => setSelectedCashIds(e.api.getSelectedRows().map((r: Record<string, unknown>) => Number(r.id)))}
                      onRowClicked={e => { if (e.event && (e.event as MouseEvent).detail === 2) cashTx.openEdit(e.data as Record<string, unknown>, accountId!) }}
                      onGridReady={onCashGridReady}
                      onColumnMoved={cashGridCols.onColumnMoved}
                      onColumnResized={cashGridCols.onColumnResized}
                      onSortChanged={cashGridCols.onSortChanged}
                      onFirstDataRendered={e => e.api.autoSizeAllColumns()}
                      onRowDataUpdated={e => e.api.autoSizeAllColumns()}
                    />
                  </div>
                </>
              )}
            </>
          )}

          {tab === 'transactions' && (
            <>
              {batchMsg && (
                <div className={`mx-4 mt-2 rounded-lg px-4 py-2 text-sm flex items-start justify-between gap-3 ${batchMsg.startsWith('Error') ? 'bg-red-50 text-red-600' : 'bg-green-50 text-green-700'}`}>
                  <div>
                    <div>{batchMsg}</div>
                    {batchWarnings.length > 0 && (
                      <ul className="mt-1 text-amber-700 list-disc list-inside">
                        {batchWarnings.map((w, i) => (
                          <li key={i}>{w.security} in {w.account} is now negative ({fmtQty(w.quantity, 8)})</li>
                        ))}
                      </ul>
                    )}
                  </div>
                  <button onClick={() => { setBatchMsg(null); setBatchWarnings([]) }} className="text-slate-400 hover:text-slate-600 shrink-0"><X size={14} /></button>
                </div>
              )}
              <div className="flex items-center justify-between gap-2 px-4 py-2 border-b border-slate-100 bg-slate-50 flex-wrap">
                <div className="flex items-center gap-3 flex-wrap">
                  <div className="relative">
                    <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
                    <Input className="pl-8 w-56" placeholder="Search…" value={txSearch} onChange={e => setTxSearch(e.target.value)} />
                  </div>
                  <span className="text-xs text-slate-400 whitespace-nowrap">{txTotalCount != null ? `${txTotalCount.toLocaleString()} transactions` : ''}</span>
                </div>
                <div className="flex items-center gap-2">
                  {selectedTxIds.length > 0 && (
                    <>
                      <span className="text-xs text-slate-500 whitespace-nowrap">{selectedTxIds.length} selected</span>
                      <Button variant="secondary" size="sm" onClick={() => setBatchAction('account')}>Move to Account…</Button>
                      <Button variant="secondary" size="sm" onClick={() => setBatchAction('cash')}>Change Cash Account…</Button>
                      <Button variant="destructive" size="sm" disabled={batchSaving} onClick={handleBatchDelete}>Delete Selected</Button>
                    </>
                  )}
                  <ColumnsMenu columns={txGridCols.columns} onToggle={txGridCols.toggleColumn} />
                  <CopyToExcelButton gridApi={txGridApi} />
                </div>
              </div>
              {/* Infinite row model: rows stream in as you scroll instead of clicking through
                  numbered pages — see invDatasource above for how blocks are fetched. */}
              <div className="ag-theme-alpine" style={{ height: 'calc(100vh - 320px)', width: '100%' }}>
                <AgGridReact
                  theme="legacy"
                  ref={txGridRef}
                  rowModelType="infinite"
                  datasource={invDatasource}
                  cacheBlockSize={PAGE_SIZE}
                  columnDefs={txGridCols.colDefs}
                  defaultColDef={{ resizable: true, sortable: true }}
                  rowSelection="multiple"
                  onSelectionChanged={e => setSelectedTxIds(e.api.getSelectedRows().map((r: Record<string, unknown>) => Number(r.id)))}
                  onRowClicked={e => { if (e.data && e.event && (e.event as MouseEvent).detail === 2) openEdit(e.data as Record<string, unknown>) }}
                  onGridReady={onTxGridReady}
                  onColumnMoved={txGridCols.onColumnMoved}
                  onColumnResized={txGridCols.onColumnResized}
                  onSortChanged={txGridCols.onSortChanged}
                  onFirstDataRendered={e => e.api.autoSizeAllColumns()}
                  getRowId={p => p.data ? String(p.data.id) : `placeholder-${p.data?.__placeholderIndex}`}
                />
              </div>
            </>
          )}
        </Card>
      </div>

      {cashTx.modalOpen && cashTx.form && (
        <TxModal
          form={cashTx.form}
          splits={cashTx.splits}
          useSplits={cashTx.useSplits}
          setUseSplits={cashTx.setUseSplits}
          onFormChange={cashTx.setForm}
          onSplitsChange={cashTx.setSplits}
          payees={payees as Record<string, unknown>[]}
          categories={categories as Record<string, unknown>[]}
          accounts={accounts as Record<string, unknown>[]}
          onSave={cashTx.handleSave}
          onDelete={cashTx.form.id ? cashTx.handleDelete : undefined}
          onClose={cashTx.close}
          onPayeeCreated={p => qc.setQueryData(['payees'], (old: Record<string, unknown>[]) => [...(old ?? []), { id: p.id, name: p.name }])}
          onCategoryCreated={c => qc.setQueryData(['categories'], (old: Record<string, unknown>[]) => [...(old ?? []), c])}
          saving={cashTx.saving}
          error={cashTx.saveError}
          recurringEnabled={cashTx.recurringEnabled}
          setRecurringEnabled={cashTx.setRecurringEnabled}
          recurringName={cashTx.recurringName}
          setRecurringName={cashTx.setRecurringName}
          recurringFreq={cashTx.recurringFreq}
          setRecurringFreq={cashTx.setRecurringFreq}
          recurringNextDue={cashTx.recurringNextDue}
          setRecurringNextDue={cashTx.setRecurringNextDue}
          installmentEnabled={cashTx.installmentEnabled}
          setInstallmentEnabled={cashTx.setInstallmentEnabled}
          installmentCount={cashTx.installmentCount}
          setInstallmentCount={cashTx.setInstallmentCount}
          installmentFreq={cashTx.installmentFreq}
          setInstallmentFreq={cashTx.setInstallmentFreq}
        />
      )}

      {modalOpen && (
        <InvTransactionModal
          form={form}
          onChange={setForm}
          accounts={modalAccounts}
          allAccounts={accounts as Record<string, unknown>[]}
          securities={securities as Record<string,unknown>[]}
          onSave={handleSave}
          onDelete={editId ? handleInvDelete : undefined}
          onClose={() => { setModalOpen(false); setEditId(null) }}
          saving={saving}
          error={saveError}
          editId={editId}
        />
      )}

      {transferOpen && (
        <InvTransferModal
          accounts={accounts as Record<string, unknown>[]}
          onClose={() => setTransferOpen(false)}
          onDone={() => {
            qc.invalidateQueries({ queryKey: ['holdings'], exact: false })
            refreshTxGrid()
          }}
        />
      )}

      {batchAction && (
        <BatchAccountPicker
          title={batchAction === 'account' ? 'Move to Account' : 'Change Cash Account'}
          count={selectedTxIds.length}
          accounts={batchAction === 'account' ? investmentAccounts : cashAccountsForBatch}
          saving={batchSaving}
          onCancel={() => setBatchAction(null)}
          onApply={handleBatchApply}
        />
      )}

      {cashBatchMoveOpen && (
        <BatchAccountPicker
          title="Move to Account"
          count={selectedCashIds.length}
          accounts={cashMoveAccounts}
          saving={cashBatchSaving}
          onCancel={() => setCashBatchMoveOpen(false)}
          onApply={handleCashBatchMove}
        />
      )}

    </div>
  )
}
