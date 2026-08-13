import { useState, useCallback, useRef, useMemo, useEffect } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { usePersist, useGridColumnState, useLiveRefetchInterval } from '@/lib/hooks'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AgGridReact } from 'ag-grid-react'
import type { ColDef, GridReadyEvent, GridApi, RowClickedEvent, IDatasource, IGetRowsParams } from 'ag-grid-community'
import {
  getAccounts, getTransactions, getTransactionIds, getPayees, getCategories,
  clearAccount, reconcileAccount, searchAllTransactions,
  syncBalances, batchDeleteTransactions, batchMoveTransactions,
} from '@/lib/api'
import { PageHeader, Select, Input, Button, Spinner, Card, useEscapeKey, SyncBalancesButton, ColumnsMenu, CopyToExcelButton, AccountOptions, BatchAccountPicker } from '@/components/ui'
import { fmtCur, fmtDate } from '@/lib/utils'
import { Plus, Search, X, CheckCheck, ArrowLeft } from 'lucide-react'
import { TxModal, useTxModal, today } from '@/components/TxModal'
import { CASH_ACCOUNT_TYPES } from '@/lib/accountTypes'

const PAGE_SIZE = 200
const DEFAULT_TO_DATE = '2099-12-31'   // "no upper bound" — includes future-dated/scheduled transactions

// ── Date helpers ──────────────────────────────────────────────────────────────
function monthsAgo(n: number) {
  const d = new Date()
  d.setMonth(d.getMonth() - n)
  return d.toISOString().slice(0, 10)
}
function ytdStart() { return `${new Date().getFullYear()}-01-01` }

// ── Cell renderers ────────────────────────────────────────────────────────────
// Transaction amounts/balances are in the account's own native currency, not
// always EUR — currency is passed in via cellRendererParams (see makeColDefs).
function AmountCell({ value, currency }: { value: number; currency?: string }) {
  return <span className={`font-semibold tabular-nums ${value < 0 ? 'text-red-600' : 'text-green-700'}`}>{fmtCur(value, currency)}</span>
}
function BalanceCell({ value, currency }: { value: number; currency?: string }) {
  return <span className={`tabular-nums ${value < 0 ? 'text-red-600' : 'text-slate-800'}`}>{fmtCur(value, currency)}</span>
}
type SplitDetail = { category: string | null; amount: number }
// data is undefined while the infinite row model is still fetching the block
// that will contain this row — AG Grid renders a loading placeholder for it.
function CategoryCell({ value, data, currency }: { value: string; data?: Record<string, unknown>; currency?: string }) {
  if (!data) return null
  const splitCount = Number(data.split_count ?? 0)
  if (splitCount <= 1) return <span>{value ?? ''}</span>
  const splits = (data.splits as SplitDetail[]) ?? []
  const tooltip = splits.map(s => `${s.category ?? '(no category)'}: ${fmtCur(s.amount, currency)}`).join('\n')
  return <span className="italic text-slate-500 cursor-help" title={tooltip}>Split</span>
}
function ClearedCell({ data }: { data?: Record<string, unknown> }) {
  if (!data) return null
  if (data.is_draft) return <span className="inline-flex items-center px-1.5 py-0.5 rounded text-xs bg-slate-100 text-slate-500">Draft</span>
  if (!data.cleared && !data.reconciled) return <span className="inline-flex items-center px-1.5 py-0.5 rounded text-xs bg-yellow-100 text-yellow-700">Pending</span>
  return (
    <span className="inline-flex items-center gap-1">
      {Boolean(data.cleared) && <span className="inline-flex items-center px-1.5 py-0.5 rounded text-xs bg-green-100 text-green-700">Cleared</span>}
      {Boolean(data.reconciled) && <span className="inline-flex items-center px-1.5 py-0.5 rounded text-xs bg-blue-100 text-blue-700">Reconciled</span>}
    </span>
  )
}

// AG Grid hides its own built-in header checkbox for the Infinite Row Model (there's
// no way for it to know about rows it hasn't fetched, so it can't offer a real
// "select all"). This replaces it with our own, backed by /register/transactions/ids
// (see handleToggleSelectAll) so "select all" can mean every row matching the current
// account/search/date filters, not just whatever's currently loaded on screen.
function SelectAllHeaderCheckbox({ checked, indeterminate, disabled, onToggle }: { checked: boolean; indeterminate: boolean; disabled?: boolean; onToggle: () => void }) {
  const ref = useRef<HTMLInputElement>(null)
  useEffect(() => { if (ref.current) ref.current.indeterminate = indeterminate }, [indeterminate])
  return (
    <div className="flex items-center justify-center h-full w-full">
      <input ref={ref} type="checkbox" checked={checked} disabled={disabled} onChange={onToggle} />
    </div>
  )
}

// Sortable columns must match the backend's _SORTABLE_COLUMNS whitelist (register.py)
// — sorting is done server-side across the whole account now, not just the loaded
// rows, so a column with no server-side mapping is marked unsortable rather than
// silently sorting by date instead and looking broken.
const makeColDefs = (currency: string): ColDef[] => [
  { field: 'date', headerName: 'Date', width: 115, minWidth: 115, valueFormatter: p => fmtDate(p.value), sort: 'desc' },
  { field: 'payee', headerName: 'Payee', flex: 1, minWidth: 140 },
  { field: 'description', headerName: 'Description', flex: 2, minWidth: 180, maxWidth: 400, tooltipField: 'description' },
  { field: 'category', headerName: 'Category', flex: 1, minWidth: 140, cellRenderer: CategoryCell, cellRendererParams: { currency } },
  { field: 'target_account', headerName: 'Transfer To', width: 130, sortable: false },
  { field: 'memo', headerName: 'Memo', width: 140, sortable: false },
  { field: 'amount', headerName: 'Amount', width: 120, cellRenderer: AmountCell, cellRendererParams: { currency }, type: 'numericColumn' },
  { field: 'running_balance', headerName: 'Balance', width: 120, cellRenderer: BalanceCell, cellRendererParams: { currency }, type: 'numericColumn', sortable: false },
  { colId: 'status', headerName: 'Status', width: 170, cellRenderer: ClearedCell, sortable: false },
]



// ── Period shortcuts ──────────────────────────────────────────────────────────
const PERIODS = [
  { label: '1M', from: () => monthsAgo(1) },
  { label: '3M', from: () => monthsAgo(3) },
  { label: '6M', from: () => monthsAgo(6) },
  { label: 'YTD', from: ytdStart },
  { label: 'All', from: () => '1900-01-01' },
]


// ── Register page ─────────────────────────────────────────────────────────────
export default function Register() {
  const gridRef = useRef<AgGridReact>(null)
  const [gridApi, setGridApi] = useState<GridApi | null>(null)
  const qc = useQueryClient()
  const liveRefetchMs = useLiveRefetchInterval()

  const [accountId, setAccountId] = usePersist<number | null>('register_accountId', null)
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  // Arriving here via an AccountLink from a report ("?accountsId=123&from=report") —
  // scope to that account and show a Back button. Consumed once: the params are
  // cleared right after so navigating the grid/filters afterward doesn't keep
  // re-forcing the same account, and a plain page refresh doesn't re-trigger it.
  const cameFromReport = searchParams.get('from') === 'report'
  useEffect(() => {
    const incoming = searchParams.get('accountsId')
    if (incoming == null) return
    setAccountId(Number(incoming))
    setSearchParams(prev => { const p = new URLSearchParams(prev); p.delete('accountsId'); return p }, { replace: true })
  }, [searchParams, setAccountId, setSearchParams])
  const [showInactive, setShowInactive] = useState(false)
  const [search, setSearch] = useState('')
  const [fromDate, setFromDate] = useState(monthsAgo(1))
  const [toDate, setToDate] = useState(DEFAULT_TO_DATE)
  const [activePeriod, setActivePeriod] = useState<string>('1M')

  // Grid refresh (server-side, so no more query-cache invalidation for transactions —
  // just tell the infinite row model's currently-loaded blocks to refetch themselves).
  const refreshGrid = useCallback(() => {
    gridRef.current?.api?.refreshInfiniteCache()
  }, [])

  const tx = useTxModal({
    onSaved: () => {
      refreshGrid()
      qc.invalidateQueries({ queryKey: ['accounts'], exact: false })
    },
  })

  // Batch move/delete (multi-row selection toolbar)
  const [selectedIds, setSelectedIds] = useState<number[]>([])
  const [batchMoveOpen, setBatchMoveOpen] = useState(false)
  const [batchSaving, setBatchSaving] = useState(false)
  const [batchMsg, setBatchMsg] = useState<string | null>(null)

  const batchInvalidate = () => {
    refreshGrid()
    qc.invalidateQueries({ queryKey: ['accounts'], exact: false })
  }

  const handleBatchMove = async (targetAccountId: number) => {
    setBatchSaving(true); setBatchMsg(null)
    try {
      const res = await batchMoveTransactions(selectedIds, targetAccountId)
      batchInvalidate()
      gridRef.current?.api.deselectAll()
      setSelectedIds([])
      setBatchMoveOpen(false)
      setBatchMsg(`Moved ${res.moved} transaction${res.moved === 1 ? '' : 's'}.`)
    } catch (e: unknown) {
      setBatchMsg(e instanceof Error ? `Error: ${e.message}` : 'Batch move failed')
    } finally { setBatchSaving(false) }
  }

  const handleBatchDelete = async () => {
    if (!confirm(`Delete ${selectedIds.length} transaction${selectedIds.length === 1 ? '' : 's'}? This cannot be undone.`)) return
    setBatchSaving(true); setBatchMsg(null)
    try {
      const res = await batchDeleteTransactions(selectedIds)
      batchInvalidate()
      gridRef.current?.api.deselectAll()
      setSelectedIds([])
      const skippedMsg = res.skipped.length > 0 ? ` ${res.skipped.length} skipped: ${res.skipped.map(s => s.reason).join(' ')}` : ''
      setBatchMsg(`Deleted ${res.deleted} transaction${res.deleted === 1 ? '' : 's'}.${skippedMsg}`)
    } catch (e: unknown) {
      setBatchMsg(e instanceof Error ? `Error: ${e.message}` : 'Batch delete failed')
    } finally { setBatchSaving(false) }
  }

  // Global search state
  const [globalSearch, setGlobalSearch] = useState('')
  const [globalOpen, setGlobalOpen] = useState(false)
  // A pending "jump to this transaction" request from a global search hit — imperative,
  // not React state, since it's consumed once inside the datasource's getRows callback
  // rather than driving a render.
  const focusTxIdRef = useRef<number | null>(null)

  // Clear state
  const [clearOpen, setClearOpen] = useState(false)
  const [clearDate, setClearDate] = useState(today())
  const [clearMsg, setClearMsg] = useState<string | null>(null)
  const [clearing, setClearing] = useState(false)

  // Reconcile state
  const [reconcileOpen, setReconcileOpen] = useState(false)
  const [reconcileDate, setReconcileDate] = useState(today())
  const [reconcileMsg, setReconcileMsg] = useState<string | null>(null)
  const [reconciling, setReconciling] = useState(false)

  // Rows here can be inserted in the background — e.g. an Investment Transaction's
  // linked cash entry, or a bank import/sync run elsewhere — with no signal to this
  // open page. Periodically refresh the currently-loaded blocks so they show up
  // without a manual reload; skipped while there's an open edit or an active
  // selection so an in-progress edit/batch action isn't disrupted underneath the user.
  useEffect(() => {
    if (!liveRefetchMs || tx.modalOpen || batchMoveOpen || clearOpen || reconcileOpen || selectedIds.length > 0) return
    const id = setInterval(refreshGrid, liveRefetchMs)
    return () => clearInterval(id)
  }, [liveRefetchMs, tx.modalOpen, batchMoveOpen, clearOpen, reconcileOpen, selectedIds.length, refreshGrid])

  const { data: accounts = [] } = useQuery({ queryKey: ['accounts'], queryFn: () => getAccounts(), refetchInterval: liveRefetchMs })
  const { data: accountsFuture = [] } = useQuery({ queryKey: ['accounts', 'future'], queryFn: () => getAccounts(true), refetchInterval: liveRefetchMs })
  const cashAccounts = (accounts as Record<string, unknown>[])
    .filter(a => CASH_ACCOUNT_TYPES.includes(String(a.type ?? '')))
    .filter(a => showInactive || Boolean(a.is_active))
  const { data: payees = [] } = useQuery({ queryKey: ['payees', 'lite'], queryFn: () => getPayees(undefined, false) })
  const { data: categories = [] } = useQuery({ queryKey: ['categories'], queryFn: () => getCategories() })

  // Transaction count for the "N transactions" label — updated from whatever the
  // datasource's most recent response said; reset on any filter change below so a
  // stale count from the previous account/date range/search doesn't linger.
  const [totalCount, setTotalCount] = useState<number | null>(null)
  useEffect(() => { setTotalCount(null) }, [accountId, search, fromDate, toDate])

  // "Select all N filtered" (the custom header checkbox above) — true once selectedIds
  // covers every row matching the current filters, not just what's been scrolled past.
  // Kept in a ref too so the datasource's getRows closure (recreated only when the
  // filters themselves change, not on every selection change) can read the latest
  // value without needing selectedIds/totalCount in its own dependency array — that
  // would throw away the grid's loaded blocks and reload from scratch on every click.
  const allFilteredSelected = totalCount != null && totalCount > 0 && selectedIds.length >= totalCount
  const allFilteredSelectedRef = useRef(false)
  useEffect(() => { allFilteredSelectedRef.current = allFilteredSelected }, [allFilteredSelected])
  const [selectAllLoading, setSelectAllLoading] = useState(false)

  const handleToggleSelectAll = useCallback(async () => {
    if (allFilteredSelectedRef.current) {
      gridRef.current?.api.deselectAll()
      setSelectedIds([])
      return
    }
    if (!accountId) return
    setSelectAllLoading(true)
    try {
      const res = await getTransactionIds({ account_id: accountId, search: search || undefined, from_date: fromDate, to_date: toDate })
      setSelectedIds(res.ids)
      // 'api' source (see onSelectionChanged below) so this bulk sync doesn't get
      // read back as "the user selected exactly these loaded rows" and clobber the
      // full id list we just set.
      const idSet = new Set(res.ids)
      gridRef.current?.api.forEachNode(node => { if (node.data) node.setSelected(idSet.has(Number(node.data.id)), false, 'api') })
    } finally { setSelectAllLoading(false) }
  }, [accountId, search, fromDate, toDate])

  // AG Grid Infinite Row Model datasource: fetches one block of rows at a time as the
  // user scrolls, with sorting done server-side (see register.py's whitelist) rather
  // than only sorting whatever page happened to already be loaded. Recreated whenever
  // the account/search/date range changes, which — since it's a new object — is what
  // tells the grid to throw away its cache and reload from scratch.
  const datasource = useMemo<IDatasource>(() => ({
    getRows: (params: IGetRowsParams) => {
      const sortModel = params.sortModel[0]
      getTransactions({
        account_id: accountId,
        search: search || undefined,
        from_date: fromDate,
        to_date: toDate,
        limit: params.endRow - params.startRow,
        offset: params.startRow,
        sort_by: sortModel?.colId,
        sort_dir: sortModel?.sort,
      }).then(result => {
        setTotalCount(result.total)
        const lastRow = result.total <= params.endRow ? result.total : -1
        params.successCallback(result.transactions, lastRow)

        // "Select all filtered" is active — every row belongs to the selection by
        // definition (the datasource itself already applies the same filters), so
        // newly-loaded blocks just need their checkboxes checked to match.
        if (allFilteredSelectedRef.current) {
          requestAnimationFrame(() => {
            result.transactions.forEach((t: Record<string, unknown>) => {
              gridRef.current?.api.getRowNode(String(t.id))?.setSelected(true, false, 'api')
            })
          })
        }

        // If a global-search result asked us to land on a specific transaction, the
        // narrowed date range (see the search result's onClick) guarantees it's the
        // newest row in range, so it should be in this very first block — check once
        // and give up silently if not found, same as the old page-based version did.
        const wantedId = focusTxIdRef.current
        if (wantedId != null && params.startRow === 0) {
          focusTxIdRef.current = null
          requestAnimationFrame(() => {
            const node = gridRef.current?.api?.getRowNode(String(wantedId))
            if (node) {
              gridRef.current?.api.ensureNodeVisible(node, 'middle')
              node.setSelected(true, true)
            }
          })
        }
      }).catch(() => params.failCallback())
    },
  }), [accountId, search, fromDate, toDate])

  const globalQ = globalSearch.trim()
  const globalSearchQuery = useQuery({
    queryKey: ['global-search', globalQ],
    queryFn: () => searchAllTransactions(globalQ),
    enabled: globalQ.length >= 2,
    staleTime: 30_000,
  })
  const globalResults = (globalSearchQuery.data as Record<string, unknown>[] | undefined) ?? []
  // Grouped by currency rather than netted into one figure — these amounts are each in
  // their own account's native currency (see the currency note elsewhere on this page),
  // so summing across currencies without a conversion would just be a meaningless number.
  const globalSummary = useMemo(() => {
    const byCurrency = new Map<string, { count: number; income: number; expense: number }>()
    for (const row of globalResults) {
      const currency = String(row.currency ?? 'EUR')
      const amount = Number(row.amount ?? 0)
      const entry = byCurrency.get(currency) ?? { count: 0, income: 0, expense: 0 }
      entry.count++
      if (amount >= 0) entry.income += amount
      else entry.expense += amount
      byCurrency.set(currency, entry)
    }
    return [...byCurrency.entries()]
  }, [globalResults])

  // Escape closes whichever inline overlay is open
  useEscapeKey(useCallback(() => {
    if (globalOpen) setGlobalOpen(false)
    else if (clearOpen) setClearOpen(false)
    else if (reconcileOpen) setReconcileOpen(false)
  }, [globalOpen, clearOpen, reconcileOpen]))

  const onGridReady = useCallback((e: GridReadyEvent) => {
    setGridApi(e.api)
    e.api.autoSizeAllColumns()
  }, [])

  const setPeriod = (label: string, from: string) => {
    setFromDate(from)
    // A period shortcut always means "from X up to now" — reset the upper bound too, in
    // case it was left narrowed (e.g. by jumping in from a global search hit) and would
    // otherwise clash with the new lower bound and show nothing.
    setToDate(DEFAULT_TO_DATE)
    setActivePeriod(label)
  }

  const onRowClicked = (e: RowClickedEvent) => {
    if (e.event && (e.event as MouseEvent).detail === 2) {
      tx.openEdit(e.data as Record<string, unknown>, accountId!)
    }
  }

  const selectedAccount = (accounts as Record<string, unknown>[]).find(a => a.id === accountId)
  const selectedAccountFuture = (accountsFuture as Record<string, unknown>[]).find(a => a.id === accountId)
  const isCreditCard = selectedAccount && String(selectedAccount.type) === 'Credit Card'
  const accountCurrency = String(selectedAccount?.currency ?? 'EUR')
  const colDefs = useMemo(() => [
    {
      colId: 'select', checkboxSelection: true, width: 40, pinned: 'left' as const, sortable: false, filter: false, resizable: false,
      headerComponent: SelectAllHeaderCheckbox,
      headerComponentParams: {
        checked: allFilteredSelected,
        indeterminate: selectedIds.length > 0 && !allFilteredSelected,
        disabled: selectAllLoading,
        onToggle: handleToggleSelectAll,
      },
    },
    ...makeColDefs(accountCurrency),
  ], [accountCurrency, allFilteredSelected, selectedIds.length, selectAllLoading, handleToggleSelectAll])
  const gridCols = useGridColumnState('register', colDefs)

  return (
    <div className="flex flex-col h-full">
      <PageHeader
        title="Cash Register"
        subtitle={selectedAccount ? `${String(selectedAccount.name)} · ${fmtCur(Number(selectedAccount.balance), accountCurrency)}` : 'Select an account'}
        actions={
          <div className="flex items-center gap-2 flex-wrap">
            {cameFromReport && (
              <>
                <Button size="sm" variant="secondary" onClick={() => navigate(-1)}>
                  <ArrowLeft size={13} /> Back
                </Button>
                <div className="w-px h-5 bg-slate-200" />
              </>
            )}
            <div className="relative">
              <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
              <input
                className="pl-8 pr-3 py-1.5 text-sm rounded-md border border-slate-300 w-40 sm:w-56 focus:outline-none focus:ring-1 focus:ring-blue-500"
                placeholder="Payee, description, category…"
                value={globalSearch}
                onChange={e => setGlobalSearch(e.target.value)}
                onFocus={() => globalSearch.trim().length >= 2 && setGlobalOpen(true)}
                onKeyDown={e => e.key === 'Enter' && globalSearch.trim().length >= 2 && setGlobalOpen(true)}
              />
            </div>
            <button
              className="p-1.5 rounded border border-slate-300 text-slate-500 hover:bg-slate-50"
              onClick={() => { if (globalSearch.trim().length >= 2) setGlobalOpen(true) }}
            >
              <Search size={14} />
            </button>
            <div className="w-px h-5 bg-slate-200" />
            <Button size="sm" variant="secondary" onClick={() => { setClearMsg(null); setClearOpen(true) }} disabled={!accountId}>
              <CheckCheck size={14} /> Clear
            </Button>
            <Button size="sm" variant="secondary" onClick={() => { setReconcileMsg(null); setReconcileOpen(true) }} disabled={!accountId}>
              <CheckCheck size={14} /> Reconcile
            </Button>
            <Button size="sm" onClick={() => tx.openNew(accountId!)} disabled={!accountId}><Plus size={14} /> New Transaction</Button>
            <SyncBalancesButton
              options={[{ label: '🏦 Bank & Cash', target: 'cash' }]}
              onSync={async target => {
                await syncBalances(target)
                await qc.invalidateQueries({ queryKey: ['accounts'], exact: false })
                refreshGrid()
              }}
            />
          </div>
        }
      />

      {/* Filters */}
      <div className="flex items-center gap-3 px-6 py-3 bg-white border-b border-slate-200 flex-wrap">
        <Select className="w-56" value={accountId ?? ''} onChange={e => {
          setAccountId(Number(e.target.value) || null)
          // Same reasoning as setPeriod: don't carry over an upper bound narrowed by a
          // previous global-search jump — a fresh account should start from a normal view.
          setToDate(DEFAULT_TO_DATE)
        }}>
          <option value="">— Select account —</option>
          <AccountOptions accounts={cashAccounts} />
        </Select>
        <label className="flex items-center gap-1.5 text-xs text-slate-500 cursor-pointer select-none">
          <input type="checkbox" checked={showInactive} onChange={e => setShowInactive(e.target.checked)} className="rounded" />
          Show inactive
        </label>

        {/* Period shortcuts */}
        <div className="flex gap-1">
          {PERIODS.map(p => (
            <button
              key={p.label}
              onClick={() => setPeriod(p.label, p.from())}
              className={`px-2.5 py-1 text-xs rounded border transition-colors ${activePeriod === p.label ? 'bg-blue-600 text-white border-blue-600' : 'border-slate-300 text-slate-600 hover:bg-slate-50'}`}
            >
              {p.label}
            </button>
          ))}
        </div>

        <Input type="date" className="w-36" value={fromDate} onChange={e => { setFromDate(e.target.value); setActivePeriod('') }} />
        <span className="text-slate-400 text-sm">to</span>
        <div className="flex items-center gap-2">
          <Input type="date" className="w-36" value={toDate} onChange={e => setToDate(e.target.value)} />
          <label className="flex items-center gap-1.5 cursor-pointer whitespace-nowrap">
            <input
              type="checkbox"
              className="rounded"
              checked={toDate === today()}
              onChange={e => setToDate(e.target.checked ? today() : DEFAULT_TO_DATE)}
            />
            <span className="text-xs text-slate-500">Today</span>
          </label>
        </div>
      </div>

      {/* Credit Card / Loan info bar */}
      {accountId && selectedAccount && (isCreditCard || String(selectedAccount.type) === 'Loan') && (
        <div className="px-6 py-3 bg-blue-50 border-b border-blue-100 flex flex-wrap gap-6 items-center">
          {(() => {
            const balToDate = Number(selectedAccount.balance ?? 0)
            const balWithFuture = Number(selectedAccountFuture?.balance ?? selectedAccount.balance ?? 0)
            const creditLimit = Number(selectedAccount.credit_limit ?? 0)
            const availCredit = creditLimit > 0 ? creditLimit + balToDate : null  // balance is negative for CC
            return (
              <>
                <div>
                  <p className="text-xs text-slate-500 mb-0.5">Balance to Date</p>
                  <p className={`text-sm font-bold tabular-nums ${balToDate < 0 ? 'text-red-600' : 'text-green-700'}`}>{fmtCur(balToDate, accountCurrency)}</p>
                </div>
                <div>
                  <p className="text-xs text-slate-500 mb-0.5">Balance incl. Future Transactions</p>
                  <p className={`text-sm font-bold tabular-nums ${balWithFuture < 0 ? 'text-red-600' : 'text-green-700'}`}>{fmtCur(balWithFuture, accountCurrency)}</p>
                </div>
                {creditLimit > 0 && (
                  <>
                    <div>
                      <p className="text-xs text-slate-500 mb-0.5">Available Credit</p>
                      <p className={`text-sm font-bold tabular-nums ${(availCredit ?? 0) < creditLimit * 0.1 ? 'text-red-600' : 'text-green-700'}`}>{fmtCur(availCredit ?? 0, accountCurrency)}</p>
                    </div>
                    <div>
                      <p className="text-xs text-slate-500 mb-0.5">Credit Limit</p>
                      <p className="text-sm font-bold tabular-nums text-slate-700">{fmtCur(creditLimit, accountCurrency)}</p>
                    </div>
                    <div className="flex-1 min-w-48">
                      <p className="text-xs text-slate-500 mb-1">Credit Used</p>
                      <div className="flex items-center gap-2">
                        <div className="flex-1 h-2 bg-slate-200 rounded-full overflow-hidden">
                          {(() => {
                            const usedPct = Math.min(Math.abs(balToDate) / creditLimit * 100, 100)
                            return <div className={`h-full rounded-full transition-all ${usedPct > 90 ? 'bg-red-500' : usedPct > 70 ? 'bg-amber-500' : 'bg-green-500'}`} style={{ width: `${usedPct}%` }} />
                          })()}
                        </div>
                        <span className="text-xs tabular-nums text-slate-600 whitespace-nowrap">
                          {fmtCur(Math.abs(balToDate), accountCurrency)} / {fmtCur(creditLimit, accountCurrency)}
                        </span>
                      </div>
                    </div>
                  </>
                )}
              </>
            )
          })()}
        </div>
      )}

      {/* Grid */}
      <div className="px-6 py-4 flex-1">
        {!accountId ? (
          <div className="flex items-center justify-center h-64 text-slate-400 text-sm">Select an account to view transactions</div>
        ) : (
          <Card className="overflow-hidden">
            {batchMsg && (
              <div className={`mx-4 mt-2 rounded-lg px-4 py-2 text-sm flex items-start justify-between gap-3 ${batchMsg.startsWith('Error') ? 'bg-red-50 text-red-600' : 'bg-green-50 text-green-700'}`}>
                <div>{batchMsg}</div>
                <button onClick={() => setBatchMsg(null)} className="text-slate-400 hover:text-slate-600 shrink-0"><X size={14} /></button>
              </div>
            )}
            <div className="flex items-center justify-between gap-2 px-4 py-2 border-b border-slate-100 bg-slate-50 flex-wrap">
              <div className="flex items-center gap-3 flex-wrap">
                <div className="relative">
                  <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
                  <Input className="pl-8 w-56" placeholder="Search…" value={search} onChange={e => setSearch(e.target.value)} />
                </div>
                <span className="text-xs text-slate-400 whitespace-nowrap">{totalCount != null ? `${totalCount.toLocaleString()} transactions` : ''}</span>
              </div>
              <div className="flex items-center gap-2">
                {selectAllLoading && <span className="text-xs text-slate-400 whitespace-nowrap">Selecting…</span>}
                {selectedIds.length > 0 && (
                  <>
                    <span className="text-xs text-slate-500 whitespace-nowrap">
                      {allFilteredSelected ? `All ${selectedIds.length} selected` : `${selectedIds.length} selected`}
                    </span>
                    <Button variant="secondary" size="sm" onClick={() => setBatchMoveOpen(true)}>Move to Account…</Button>
                    <Button variant="destructive" size="sm" disabled={batchSaving} onClick={handleBatchDelete}>Delete Selected</Button>
                  </>
                )}
                <ColumnsMenu columns={gridCols.columns} onToggle={gridCols.toggleColumn} />
                <CopyToExcelButton gridApi={gridApi} />
              </div>
            </div>
            {/* Infinite row model: rows stream in as you scroll instead of clicking through
                numbered pages — see the `datasource` above for how blocks are fetched. Copy to
                Excel only copies whichever rows have scrolled into view and loaded so far. */}
            <div className="ag-theme-alpine" style={{ height: 'calc(100vh - 280px)', width: '100%' }}>
              <AgGridReact
                theme="legacy"
                ref={gridRef}
                rowModelType="infinite"
                datasource={datasource}
                cacheBlockSize={PAGE_SIZE}
                columnDefs={gridCols.colDefs}
                onGridReady={onGridReady}
                onRowClicked={onRowClicked}
                onColumnMoved={gridCols.onColumnMoved}
                onColumnResized={gridCols.onColumnResized}
                onSortChanged={gridCols.onSortChanged}
                onFirstDataRendered={e => e.api.sizeColumnsToFit()}
                defaultColDef={{ resizable: true, sortable: true }}
                rowSelection="multiple"
                onSelectionChanged={e => { if (e.source !== 'api') setSelectedIds(e.api.getSelectedRows().map((r: Record<string, unknown>) => Number(r.id))) }}
                suppressCellFocus={false}
                getRowId={p => p.data ? String(p.data.id) : `placeholder-${p.data?.__placeholderIndex}`}
              />
            </div>
          </Card>
        )}
      </div>

      {/* Transaction Modal */}
      {tx.modalOpen && tx.form && (
        <TxModal
          form={tx.form}
          splits={tx.splits}
          useSplits={tx.useSplits}
          setUseSplits={tx.setUseSplits}
          onFormChange={tx.setForm}
          onSplitsChange={tx.setSplits}
          payees={payees as Record<string, unknown>[]}
          categories={categories as Record<string, unknown>[]}
          accounts={accounts as Record<string, unknown>[]}
          onSave={tx.handleSave}
          onDelete={tx.form.id ? tx.handleDelete : undefined}
          onClose={tx.close}
          onPayeeCreated={p => qc.setQueryData(['payees'], (old: Record<string,unknown>[]) => [...(old ?? []), { id: p.id, name: p.name }])}
          onCategoryCreated={c => qc.setQueryData(['categories'], (old: Record<string,unknown>[]) => [...(old ?? []), c])}
          saving={tx.saving}
          error={tx.saveError}
          recurringEnabled={tx.recurringEnabled}
          setRecurringEnabled={tx.setRecurringEnabled}
          recurringName={tx.recurringName}
          setRecurringName={tx.setRecurringName}
          recurringFreq={tx.recurringFreq}
          setRecurringFreq={tx.setRecurringFreq}
          recurringNextDue={tx.recurringNextDue}
          setRecurringNextDue={tx.setRecurringNextDue}
          installmentEnabled={tx.installmentEnabled}
          setInstallmentEnabled={tx.setInstallmentEnabled}
          installmentCount={tx.installmentCount}
          setInstallmentCount={tx.setInstallmentCount}
          installmentFreq={tx.installmentFreq}
          setInstallmentFreq={tx.setInstallmentFreq}
        />
      )}

      {/* Global Search Overlay */}
      {globalOpen && globalQ.length >= 2 && (
        <div className="fixed inset-0 bg-black/30 z-50 flex items-start justify-center pt-20 px-4" onClick={() => setGlobalOpen(false)}>
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl max-h-[70vh] flex flex-col" onClick={e => e.stopPropagation()}>
            <div className="flex items-center gap-3 px-4 py-3 border-b border-slate-200">
              <Search size={16} className="text-slate-400 shrink-0" />
              <input
                autoFocus
                className="flex-1 text-sm outline-none"
                placeholder="Payee, description, category…"
                value={globalSearch}
                onChange={e => setGlobalSearch(e.target.value)}
              />
              <button onClick={() => setGlobalOpen(false)} className="text-slate-400 hover:text-slate-600"><X size={16} /></button>
            </div>
            {globalResults.length > 0 && (
              <div className="flex flex-wrap gap-x-5 gap-y-1.5 px-4 py-2 border-b border-slate-100 bg-slate-50 text-xs">
                {globalSummary.map(([currency, s]) => (
                  <div key={currency} className="flex items-center gap-3">
                    <span className="font-semibold text-slate-600">{s.count} {globalSummary.length > 1 ? currency : ''} {s.count === 1 ? 'result' : 'results'}</span>
                    {s.income > 0 && <span className="text-green-700 tabular-nums">+{fmtCur(s.income, currency)}</span>}
                    {s.expense < 0 && <span className="text-red-600 tabular-nums">{fmtCur(s.expense, currency)}</span>}
                    <span className="text-slate-500 tabular-nums">net {fmtCur(s.income + s.expense, currency)}</span>
                  </div>
                ))}
                {globalResults.length >= 50 && (
                  <span className="text-slate-400 italic">first 50 matches only — totals may be incomplete</span>
                )}
              </div>
            )}
            <div className="overflow-y-auto">
              {globalSearchQuery.isLoading && <div className="flex justify-center py-8"><Spinner /></div>}
              {!globalSearchQuery.isLoading && (!globalSearchQuery.data?.length) && (
                <div className="text-center text-sm text-slate-400 py-10">No results for "{globalQ}"</div>
              )}
              {globalResults.map((row, i) => (
                <button
                  key={i}
                  className="w-full flex items-center gap-4 px-4 py-3 hover:bg-slate-50 border-b border-slate-100 last:border-0 text-left"
                  onClick={() => {
                    setAccountId(Number(row.accounts_id))
                    // The account's own date filter (default: last month) usually won't cover an
                    // arbitrary search hit — and the transaction list needs to be sorted newest-first,
                    // capped to the first loaded block, so simply widening "from" to the beginning of
                    // time isn't enough on an active account: anything newer than the target still
                    // fills up that first block and pushes it off-screen. Capping "to" at the target's
                    // own date instead guarantees it's the newest (or tied-newest) row in range, so
                    // it's always in the first block the grid loads — but only if the grid is actually
                    // sorted by date; a user-applied sort on another column (e.g. Amount) would break
                    // that guarantee, so force it back to the default date-desc sort here too.
                    gridRef.current?.api?.applyColumnState({ state: [{ colId: 'date', sort: 'desc' }], defaultState: { sort: null } })
                    setFromDate('1900-01-01')
                    setToDate(String(row.date))
                    setActivePeriod('All')
                    setSearch('')
                    focusTxIdRef.current = Number(row.id)
                    setGlobalOpen(false)
                    setGlobalSearch('')
                  }}
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-slate-400 shrink-0">{fmtDate(String(row.date))}</span>
                      <span className="text-sm font-medium text-slate-800 truncate">{String(row.payee || row.description || '—')}</span>
                      {Boolean(row.payee) && Boolean(row.description) && <span className="text-xs text-slate-400 truncate">{String(row.description)}</span>}
                    </div>
                    <div className="flex items-center gap-2 mt-0.5">
                      <span className="text-xs text-slate-500 truncate">{String(row.account_name)}</span>
                      {Boolean(row.category) && <span className="text-xs text-slate-400 truncate">· {String(row.category)}</span>}
                    </div>
                  </div>
                  <span className={`text-sm font-semibold tabular-nums shrink-0 ${Number(row.amount) < 0 ? 'text-red-600' : 'text-green-700'}`}>
                    {fmtCur(Number(row.amount), row.currency as string)}
                  </span>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Clear Modal */}
      {clearOpen && (
        <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-sm">
            <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200">
              <h2 className="text-base font-semibold">Clear Transactions</h2>
              <button onClick={() => setClearOpen(false)} className="text-slate-400 hover:text-slate-600"><X size={18} /></button>
            </div>
            <div className="px-5 py-4 space-y-4">
              <p className="text-sm text-slate-600">Mark all pending (uncleared) transactions up to the selected date as <strong>Cleared</strong>.</p>
              <div>
                <label className="text-xs font-medium text-slate-500 block mb-1">Up to Date</label>
                <Input type="date" value={clearDate} onChange={e => setClearDate(e.target.value)} />
              </div>
              {clearMsg && (
                <p className={`text-sm px-3 py-2 rounded ${clearMsg.startsWith('Error') ? 'bg-red-50 text-red-600' : 'bg-green-50 text-green-700'}`}>
                  {clearMsg}
                </p>
              )}
            </div>
            <div className="flex justify-end gap-2 px-5 py-3 border-t border-slate-200">
              <Button variant="secondary" onClick={() => setClearOpen(false)}>Cancel</Button>
              <Button
                disabled={clearing}
                onClick={async () => {
                  if (!accountId) return
                  setClearing(true); setClearMsg(null)
                  try {
                    const result = await clearAccount(accountId, clearDate)
                    setClearMsg(`✓ ${result.cleared} transaction${result.cleared !== 1 ? 's' : ''} marked as cleared.`)
                    refreshGrid()
                  } catch (e: unknown) {
                    setClearMsg(`Error: ${e instanceof Error ? e.message : 'Failed'}`)
                  } finally { setClearing(false) }
                }}
              >
                <CheckCheck size={14} /> {clearing ? 'Clearing…' : 'Clear'}
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Reconcile Modal */}
      {reconcileOpen && (
        <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-sm">
            <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200">
              <h2 className="text-base font-semibold">Reconcile Account</h2>
              <button onClick={() => setReconcileOpen(false)} className="text-slate-400 hover:text-slate-600"><X size={18} /></button>
            </div>
            <div className="px-5 py-4 space-y-4">
              <p className="text-sm text-slate-600">Mark all cleared transactions up to the selected date as <strong>Reconciled</strong>.</p>
              <div>
                <label className="text-xs font-medium text-slate-500 block mb-1">Statement Date</label>
                <Input type="date" value={reconcileDate} onChange={e => setReconcileDate(e.target.value)} />
              </div>
              {reconcileMsg && (
                <p className={`text-sm px-3 py-2 rounded ${reconcileMsg.startsWith('Error') ? 'bg-red-50 text-red-600' : 'bg-green-50 text-green-700'}`}>
                  {reconcileMsg}
                </p>
              )}
            </div>
            <div className="flex justify-end gap-2 px-5 py-3 border-t border-slate-200">
              <Button variant="secondary" onClick={() => setReconcileOpen(false)}>Cancel</Button>
              <Button
                disabled={reconciling}
                onClick={async () => {
                  if (!accountId) return
                  setReconciling(true); setReconcileMsg(null)
                  try {
                    const result = await reconcileAccount(accountId, reconcileDate)
                    setReconcileMsg(`✓ ${result.reconciled} transaction${result.reconciled !== 1 ? 's' : ''} marked as reconciled.`)
                    refreshGrid()
                  } catch (e: unknown) {
                    setReconcileMsg(`Error: ${e instanceof Error ? e.message : 'Failed'}`)
                  } finally { setReconciling(false) }
                }}
              >
                <CheckCheck size={14} /> {reconciling ? 'Reconciling…' : 'Reconcile'}
              </Button>
            </div>
          </div>
        </div>
      )}

      {batchMoveOpen && (
        <BatchAccountPicker
          title="Move to Account"
          count={selectedIds.length}
          accounts={cashAccounts}
          saving={batchSaving}
          onCancel={() => setBatchMoveOpen(false)}
          onApply={handleBatchMove}
        />
      )}
    </div>
  )
}
