import { useState, useCallback, useRef, useMemo } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AgGridReact } from 'ag-grid-react'
import type { ColDef, GridReadyEvent, GridApi, RowClickedEvent } from 'ag-grid-community'
import {
  getAccounts, getTransactions, getPayees, getCategories, getPayeeTopCategories,
  createTransaction, updateTransaction, deleteTransaction,
  getSplits, upsertSplits, clearAccount, reconcileAccount,
  createTransfer, createRecurringTemplate, searchAllTransactions,
} from '@/lib/api'
import { PageHeader, Select, Input, Button, Spinner, Card, SearchableSelect } from '@/components/ui'
import { fmtEur, fmtDate } from '@/lib/utils'
import { Plus, Search, X, Save, CheckCheck, ArrowLeftRight } from 'lucide-react'

const PAGE_SIZE = 200

const CASH_ACCOUNT_TYPES = ['Cash', 'Checking', 'Savings', 'Credit Card', 'Loan', 'Real Estate', 'Vehicle', 'Asset', 'Other']

const PERIODICITIES = ['Daily', 'Weekly', 'Biweekly', 'Monthly', 'Quarterly', 'Semiannually', 'Annually']

// ── Date helpers ──────────────────────────────────────────────────────────────
function today() { return new Date().toISOString().slice(0, 10) }
function monthsAgo(n: number) {
  const d = new Date()
  d.setMonth(d.getMonth() - n)
  return d.toISOString().slice(0, 10)
}
function ytdStart() { return `${new Date().getFullYear()}-01-01` }

// ── Cell renderers ────────────────────────────────────────────────────────────
function AmountCell({ value }: { value: number }) {
  return <span className={`font-semibold tabular-nums ${value < 0 ? 'text-red-600' : 'text-green-700'}`}>{fmtEur(value)}</span>
}
function BalanceCell({ value }: { value: number }) {
  return <span className={`tabular-nums ${value < 0 ? 'text-red-600' : 'text-slate-800'}`}>{fmtEur(value)}</span>
}
function ClearedCell({ data }: { data: Record<string, unknown> }) {
  if (data.is_draft) return <span className="inline-flex items-center px-1.5 py-0.5 rounded text-xs bg-slate-100 text-slate-500">Draft</span>
  if (!data.cleared && !data.reconciled) return <span className="inline-flex items-center px-1.5 py-0.5 rounded text-xs bg-yellow-100 text-yellow-700">Pending</span>
  return (
    <span className="inline-flex items-center gap-1">
      {data.cleared && <span className="inline-flex items-center px-1.5 py-0.5 rounded text-xs bg-green-100 text-green-700">Cleared</span>}
      {data.reconciled && <span className="inline-flex items-center px-1.5 py-0.5 rounded text-xs bg-blue-100 text-blue-700">Reconciled</span>}
    </span>
  )
}

const COL_DEFS: ColDef[] = [
  { field: 'date', headerName: 'Date', width: 100, valueFormatter: p => fmtDate(p.value), sort: 'desc' },
  { field: 'payee', headerName: 'Payee', flex: 1, minWidth: 140 },
  { field: 'description', headerName: 'Description', flex: 2, minWidth: 180 },
  { field: 'category', headerName: 'Category', flex: 1, minWidth: 140 },
  { field: 'target_account', headerName: 'Transfer To', width: 130 },
  { field: 'memo', headerName: 'Memo', width: 140 },
  { field: 'amount', headerName: 'Amount', width: 120, cellRenderer: AmountCell, type: 'numericColumn' },
  { field: 'running_balance', headerName: 'Balance', width: 120, cellRenderer: BalanceCell, type: 'numericColumn' },
  { headerName: 'Status', width: 170, cellRenderer: ClearedCell },
]

// ── Transaction Modal ─────────────────────────────────────────────────────────
type TxForm = {
  id?: number
  accounts_id: number
  date: string
  description: string
  total_amount: string
  payees_id: string
  categories_id: string
  memo: string
  is_draft: boolean
  cleared: boolean
  reconciled: boolean
  is_transfer: boolean
  transfer_account_id: string
}

type SplitRow = { categories_id: string; amount: string; memo: string }

const emptyForm = (accountId: number): TxForm => ({
  accounts_id: accountId,
  date: today(),
  description: '',
  total_amount: '',
  payees_id: '',
  categories_id: '',
  memo: '',
  is_draft: false,
  cleared: false,
  reconciled: false,
  is_transfer: false,
  transfer_account_id: '',
})

interface ModalProps {
  form: TxForm
  splits: SplitRow[]
  useSplits: boolean
  setUseSplits: (v: boolean) => void
  onFormChange: (f: TxForm) => void
  onSplitsChange: (s: SplitRow[]) => void
  payees: Record<string, unknown>[]
  categories: Record<string, unknown>[]
  accounts: Record<string, unknown>[]
  onSave: () => void
  onDelete?: () => void
  onClose: () => void
  saving: boolean
  error: string | null
  // Recurring template
  recurringEnabled: boolean
  setRecurringEnabled: (v: boolean) => void
  recurringName: string
  setRecurringName: (v: string) => void
  recurringFreq: string
  setRecurringFreq: (v: string) => void
  recurringNextDue: string
  setRecurringNextDue: (v: string) => void
  // Installment series
  installmentEnabled: boolean
  setInstallmentEnabled: (v: boolean) => void
  installmentCount: string
  setInstallmentCount: (v: string) => void
  installmentFreq: string
  setInstallmentFreq: (v: string) => void
}

function TxModal({
  form, splits, useSplits, setUseSplits,
  onFormChange, onSplitsChange,
  payees, categories, accounts,
  onSave, onDelete, onClose, saving, error,
  recurringEnabled, setRecurringEnabled,
  recurringName, setRecurringName,
  recurringFreq, setRecurringFreq,
  recurringNextDue, setRecurringNextDue,
  installmentEnabled, setInstallmentEnabled,
  installmentCount, setInstallmentCount,
  installmentFreq, setInstallmentFreq,
}: ModalProps) {

  const set = (k: keyof TxForm, v: unknown) => onFormChange({ ...form, [k]: v })

  const payeeId = form.payees_id ? Number(form.payees_id) : null
  const { data: topCats = [] } = useQuery({
    queryKey: ['payee-top-categories', payeeId],
    queryFn: () => getPayeeTopCategories(payeeId!),
    enabled: !!payeeId,
    staleTime: 60_000,
  })

  const sortedCategories = useMemo(() => {
    if (!payeeId || !(topCats as Record<string,unknown>[]).length) return categories
    const topIds = new Set((topCats as Record<string,unknown>[]).map(c => String(c.id)))
    const top = (topCats as Record<string,unknown>[]).map(tc =>
      categories.find(c => String(c.id) === String(tc.id))
    ).filter(Boolean) as Record<string,unknown>[]
    const rest = categories.filter(c => !topIds.has(String(c.id)))
    return top.length ? [
      { id: '__sep__', full_path: '── Recent for this payee ──', _disabled: true },
      ...top,
      { id: '__sep2__', full_path: '── All categories ──', _disabled: true },
      ...rest,
    ] : categories
  }, [categories, topCats, payeeId])

  const addSplit = () => onSplitsChange([...splits, { categories_id: '', amount: '', memo: '' }])
  const removeSplit = (i: number) => onSplitsChange(splits.filter((_, j) => j !== i))
  const setSplit = (i: number, k: keyof SplitRow, v: string) =>
    onSplitsChange(splits.map((s, j) => j === i ? { ...s, [k]: v } : s))

  const otherAccounts = accounts.filter(a => a.id !== form.accounts_id && CASH_ACCOUNT_TYPES.includes(String(a.type ?? '')))

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-xl max-h-[92vh] overflow-y-auto">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200">
          <h2 className="text-base font-semibold">{form.id ? 'Edit Transaction' : 'New Transaction'}</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600"><X size={18} /></button>
        </div>

        <div className="px-5 py-4 space-y-3">
          {/* Transfer toggle */}
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => set('is_transfer', false)}
              className={`flex-1 py-1.5 text-sm rounded-md border transition-colors ${!form.is_transfer ? 'bg-blue-600 text-white border-blue-600' : 'border-slate-300 text-slate-600 hover:bg-slate-50'}`}
            >
              Pay/Receive
            </button>
            <button
              type="button"
              onClick={() => set('is_transfer', true)}
              className={`flex-1 py-1.5 text-sm rounded-md border transition-colors flex items-center justify-center gap-1.5 ${form.is_transfer ? 'bg-blue-600 text-white border-blue-600' : 'border-slate-300 text-slate-600 hover:bg-slate-50'}`}
            >
              <ArrowLeftRight size={13} /> Transfer
            </button>
          </div>

          {/* Row 1: Date + Amount */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs font-medium text-slate-500 block mb-1">Date *</label>
              <Input type="date" value={form.date} onChange={e => set('date', e.target.value)} />
              <label className="flex items-center gap-1.5 mt-1 cursor-pointer w-fit">
                <input
                  type="checkbox"
                  className="rounded"
                  checked={form.date === today()}
                  onChange={e => { if (e.target.checked) set('date', today()) }}
                />
                <span className="text-xs text-slate-500">Today</span>
              </label>
            </div>
            <div>
              <label className="text-xs font-medium text-slate-500 block mb-1">Amount *</label>
              <Input type="number" step="0.01" placeholder="0.00" value={form.total_amount} onChange={e => set('total_amount', e.target.value)} />
            </div>
          </div>

          {form.is_transfer ? (
            /* Transfer fields */
            <div>
              <label className="text-xs font-medium text-slate-500 block mb-1">Transfer To Account *</label>
              <select
                className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm"
                value={form.transfer_account_id}
                onChange={e => set('transfer_account_id', e.target.value)}
              >
                <option value="">— select target account —</option>
                {otherAccounts.map(a => <option key={String(a.id)} value={String(a.id)}>{String(a.name)}</option>)}
              </select>
            </div>
          ) : (
            /* Regular transaction fields */
            <>
              <div>
                <label className="text-xs font-medium text-slate-500 block mb-1">Payee</label>
                <SearchableSelect value={form.payees_id} onChange={v => set('payees_id', v)}
                  options={payees.map(p => ({ value: String(p.id), label: String(p.name) }))} />
              </div>

              <div>
                <label className="text-xs font-medium text-slate-500 block mb-1">Description</label>
                <Input value={form.description} onChange={e => set('description', e.target.value)} placeholder="Description / note" />
              </div>

              <div className="flex items-center gap-2">
                <input type="checkbox" id="use-splits" checked={useSplits} onChange={e => {
                  setUseSplits(e.target.checked)
                  if (!e.target.checked && splits.length === 0) onSplitsChange([{ categories_id: '', amount: '', memo: '' }])
                }} className="rounded" />
                <label htmlFor="use-splits" className="text-sm text-slate-600">Split categories</label>
              </div>

              {!useSplits ? (
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="text-xs font-medium text-slate-500 block mb-1">Category</label>
                    <SearchableSelect value={form.categories_id} onChange={v => set('categories_id', v)}
                      options={sortedCategories.map(c => ({ value: String(c.id), label: String(c.full_path), disabled: !!c._disabled }))} />
                  </div>
                  <div>
                    <label className="text-xs font-medium text-slate-500 block mb-1">Memo</label>
                    <Input value={form.memo} onChange={e => set('memo', e.target.value)} placeholder="Memo" />
                  </div>
                </div>
              ) : (
                <div className="space-y-2 border border-slate-200 rounded-lg p-3">
                  <div className="text-xs font-medium text-slate-500 grid grid-cols-12 gap-2">
                    <span className="col-span-5">Category</span>
                    <span className="col-span-3">Amount</span>
                    <span className="col-span-3">Memo</span>
                  </div>
                  {splits.map((sp, i) => (
                    <div key={i} className="grid grid-cols-12 gap-2 items-center">
                      <SearchableSelect className="col-span-5" value={sp.categories_id} onChange={v => setSplit(i, 'categories_id', v)}
                        options={sortedCategories.map(c => ({ value: String(c.id), label: String(c.full_path), disabled: !!c._disabled }))} />
                      <Input className="col-span-3 text-xs py-1" type="number" step="0.01" value={sp.amount} onChange={e => setSplit(i, 'amount', e.target.value)} placeholder="0.00" />
                      <Input className="col-span-3 text-xs py-1" value={sp.memo} onChange={e => setSplit(i, 'memo', e.target.value)} placeholder="Memo" />
                      <button onClick={() => removeSplit(i)} className="col-span-1 text-slate-400 hover:text-red-500"><X size={14} /></button>
                    </div>
                  ))}
                  <Button size="sm" variant="secondary" onClick={addSplit} className="mt-1"><Plus size={12} /> Add split</Button>
                </div>
              )}
            </>
          )}

          {/* Description for transfers */}
          {form.is_transfer && (
            <div>
              <label className="text-xs font-medium text-slate-500 block mb-1">Description</label>
              <Input value={form.description} onChange={e => set('description', e.target.value)} placeholder="Description / note" />
            </div>
          )}

          {/* Status checkboxes */}
          <div className="flex items-center gap-5">
            <label className="flex items-center gap-2 text-sm text-slate-700 cursor-pointer">
              <input type="checkbox" checked={form.is_draft} onChange={e => set('is_draft', e.target.checked)} className="rounded" />
              Draft
            </label>
            <label className="flex items-center gap-2 text-sm text-slate-700 cursor-pointer">
              <input type="checkbox" checked={form.cleared} onChange={e => set('cleared', e.target.checked)} className="rounded" />
              Cleared
            </label>
            <label className="flex items-center gap-2 text-sm text-slate-700 cursor-pointer">
              <input type="checkbox" checked={form.reconciled} onChange={e => set('reconciled', e.target.checked)} className="rounded" />
              Reconciled
            </label>
          </div>

          {/* Recurring template / Installment series — mutually exclusive, new tx only */}
          {!form.id && (
            <div className="space-y-2">
              {/* Recurring template */}
              <div className="border border-slate-200 rounded-lg">
                <button type="button"
                  className="w-full flex items-center gap-2 px-3 py-2 text-sm text-slate-600 hover:bg-slate-50 rounded-lg"
                  onClick={() => { setRecurringEnabled(!recurringEnabled); if (!recurringEnabled) setInstallmentEnabled(false) }}>
                  <input type="checkbox" checked={recurringEnabled} onChange={() => {}} className="rounded pointer-events-none" />
                  Save as recurring template
                </button>
                {recurringEnabled && (
                  <div className="px-3 pb-3 space-y-2 border-t border-slate-100">
                    <div className="pt-2">
                      <label className="text-xs font-medium text-slate-500 block mb-1">Template Name *</label>
                      <Input value={recurringName} onChange={e => setRecurringName(e.target.value)} placeholder="e.g. Monthly Rent" />
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      <div>
                        <label className="text-xs font-medium text-slate-500 block mb-1">Frequency</label>
                        <select className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm" value={recurringFreq} onChange={e => setRecurringFreq(e.target.value)}>
                          {PERIODICITIES.map(p => <option key={p}>{p}</option>)}
                        </select>
                      </div>
                      <div>
                        <label className="text-xs font-medium text-slate-500 block mb-1">Next Due Date</label>
                        <Input type="date" value={recurringNextDue} onChange={e => setRecurringNextDue(e.target.value)} />
                      </div>
                    </div>
                  </div>
                )}
              </div>

              {/* Installment series */}
              <div className="border border-slate-200 rounded-lg">
                <button type="button"
                  className="w-full flex items-center gap-2 px-3 py-2 text-sm text-slate-600 hover:bg-slate-50 rounded-lg"
                  onClick={() => { setInstallmentEnabled(!installmentEnabled); if (!installmentEnabled) setRecurringEnabled(false) }}>
                  <input type="checkbox" checked={installmentEnabled} onChange={() => {}} className="rounded pointer-events-none" />
                  Create installment series
                </button>
                {installmentEnabled && (
                  <div className="px-3 pb-3 space-y-2 border-t border-slate-100">
                    <p className="pt-2 text-xs text-slate-500">Creates all transactions immediately. Description will be suffixed with (1/{installmentCount || 'N'}), (2/{installmentCount || 'N'}), …</p>
                    <div className="grid grid-cols-2 gap-2">
                      <div>
                        <label className="text-xs font-medium text-slate-500 block mb-1">Number of installments *</label>
                        <Input type="number" min="2" step="1" value={installmentCount} onChange={e => setInstallmentCount(e.target.value)} placeholder="e.g. 6" />
                      </div>
                      <div>
                        <label className="text-xs font-medium text-slate-500 block mb-1">Frequency</label>
                        <select className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm" value={installmentFreq} onChange={e => setInstallmentFreq(e.target.value)}>
                          {PERIODICITIES.map(p => <option key={p}>{p}</option>)}
                        </select>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          {error && <p className="text-xs text-red-600 bg-red-50 rounded px-3 py-2">{error}</p>}
        </div>

        <div className="flex items-center justify-between px-5 py-3 border-t border-slate-200">
          <div>{form.id && onDelete && <Button variant="destructive" size="sm" onClick={onDelete} disabled={saving}>Delete</Button>}</div>
          <div className="flex gap-2">
            <Button variant="secondary" onClick={onClose}>Cancel</Button>
            <Button onClick={onSave} disabled={saving}>
              <Save size={14} /> {saving ? 'Saving…' : 'Save'}
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Date offset helper for installments ──────────────────────────────────────
function addPeriod(dateStr: string, freq: string, n: number): string {
  const d = new Date(dateStr)
  switch (freq) {
    case 'Daily':        d.setDate(d.getDate() + n); break
    case 'Weekly':       d.setDate(d.getDate() + 7 * n); break
    case 'Biweekly':     d.setDate(d.getDate() + 14 * n); break
    case 'Monthly':      d.setMonth(d.getMonth() + n); break
    case 'Quarterly':    d.setMonth(d.getMonth() + 3 * n); break
    case 'Semiannually': d.setMonth(d.getMonth() + 6 * n); break
    case 'Annually':     d.setFullYear(d.getFullYear() + n); break
  }
  return d.toISOString().slice(0, 10)
}

// ── Period shortcuts ──────────────────────────────────────────────────────────
const PERIODS = [
  { label: '1M', from: () => monthsAgo(1) },
  { label: '3M', from: () => monthsAgo(3) },
  { label: '6M', from: () => monthsAgo(6) },
  { label: 'YTD', from: ytdStart },
  { label: 'All', from: () => '2000-01-01' },
]

// ── Register page ─────────────────────────────────────────────────────────────
export default function Register() {
  const gridRef = useRef<AgGridReact>(null)
  const [, setGridApi] = useState<GridApi | null>(null)
  const qc = useQueryClient()

  const [accountId, setAccountId] = useState<number | null>(null)
  const [showInactive, setShowInactive] = useState(false)
  const [search, setSearch] = useState('')
  const [fromDate, setFromDate] = useState(monthsAgo(1))
  const [toDate, setToDate] = useState('2099-12-31')
  const [activePeriod, setActivePeriod] = useState<string>('1M')
  const [offset, setOffset] = useState(0)

  // Modal state
  const [modalOpen, setModalOpen] = useState(false)
  const [form, setForm] = useState<TxForm | null>(null)
  const [splits, setSplits] = useState<SplitRow[]>([])
  const [useSplits, setUseSplits] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  // Recurring template state
  const [recurringEnabled, setRecurringEnabled] = useState(false)
  const [recurringName, setRecurringName] = useState('')
  const [recurringFreq, setRecurringFreq] = useState('Monthly')
  const [recurringNextDue, setRecurringNextDue] = useState(today())

  // Installment series state
  const [installmentEnabled, setInstallmentEnabled] = useState(false)
  const [installmentCount, setInstallmentCount] = useState('')
  const [installmentFreq, setInstallmentFreq] = useState('Monthly')

  // Global search state
  const [globalSearch, setGlobalSearch] = useState('')
  const [globalOpen, setGlobalOpen] = useState(false)

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

  const { data: accounts = [] } = useQuery({ queryKey: ['accounts'], queryFn: getAccounts })
  const cashAccounts = (accounts as Record<string, unknown>[])
    .filter(a => CASH_ACCOUNT_TYPES.includes(String(a.type ?? '')))
    .filter(a => showInactive || Boolean(a.is_active))
  const { data: payees = [] } = useQuery({ queryKey: ['payees'], queryFn: () => getPayees() })
  const { data: categories = [] } = useQuery({ queryKey: ['categories'], queryFn: () => getCategories() })

  const queryParams = { account_id: accountId, search: search || undefined, from_date: fromDate, to_date: toDate, limit: PAGE_SIZE, offset }
  const txQuery = useQuery({ queryKey: ['transactions', queryParams], queryFn: () => getTransactions(queryParams), enabled: !!accountId })

  const globalQ = globalSearch.trim()
  const globalSearchQuery = useQuery({
    queryKey: ['global-search', globalQ],
    queryFn: () => searchAllTransactions(globalQ),
    enabled: globalQ.length >= 2,
    staleTime: 30_000,
  })

  const onGridReady = useCallback((e: GridReadyEvent) => setGridApi(e.api), [])

  const setPeriod = (label: string, from: string) => {
    setFromDate(from)
    setActivePeriod(label)
    setOffset(0)
  }

  const openNew = () => {
    if (!accountId) return
    setForm(emptyForm(accountId))
    setSplits([{ categories_id: '', amount: '', memo: '' }])
    setUseSplits(false)
    setSaveError(null)
    setRecurringEnabled(false)
    setRecurringName('')
    setRecurringFreq('Monthly')
    setRecurringNextDue(today())
    setInstallmentEnabled(false)
    setInstallmentCount('')
    setInstallmentFreq('Monthly')
    setModalOpen(true)
  }

  const openEdit = async (row: Record<string, unknown>) => {
    const txSplits = await getSplits(Number(row.id))
    setForm({
      id: Number(row.id),
      accounts_id: accountId!,
      date: String(row.date ?? '').slice(0, 10),
      description: String(row.description ?? ''),
      total_amount: String(row.amount ?? ''),
      payees_id: String(row.payees_id ?? ''),
      categories_id: String((txSplits as Record<string,unknown>[])?.[0]?.categories_id ?? ''),
      memo: String((txSplits as Record<string,unknown>[])?.[0]?.memo ?? ''),
      is_draft: Boolean(row.is_draft),
      cleared: Boolean(row.cleared),
      reconciled: Boolean(row.reconciled),
      is_transfer: Boolean(row.accounts_id_target),
      transfer_account_id: String(row.accounts_id_target ?? ''),
    })
    const loadedSplits = Array.isArray(txSplits) && txSplits.length > 0
      ? (txSplits as Record<string, unknown>[]).map(s => ({
          categories_id: String(s.categories_id ?? ''),
          amount: String(s.amount ?? ''),
          memo: String(s.memo ?? ''),
        }))
      : [{ categories_id: '', amount: '', memo: '' }]
    setSplits(loadedSplits)
    setUseSplits(loadedSplits.length > 1)
    setSaveError(null)
    setModalOpen(true)
  }

  const handleSave = async () => {
    if (!form) return
    setSaving(true)
    setSaveError(null)
    try {
      const statusFields = { is_draft: form.is_draft, cleared: form.cleared, reconciled: form.reconciled }

      if (form.is_transfer && !form.id) {
        // New transfer: create paired transactions
        if (!form.transfer_account_id) throw new Error('Select a target account for the transfer')
        await createTransfer({
          from_account_id: form.accounts_id,
          to_account_id: Number(form.transfer_account_id),
          date: form.date,
          amount: parseFloat(form.total_amount),
          description: form.description || null,
          ...statusFields,
        })
      } else if (form.is_transfer && form.id) {
        // Edit transfer leg
        await updateTransaction(form.id, {
          date: form.date,
          description: form.description || null,
          total_amount: parseFloat(form.total_amount),
          accounts_id_target: form.transfer_account_id ? Number(form.transfer_account_id) : null,
          ...statusFields,
        })
      } else if (!form.id && installmentEnabled && parseInt(installmentCount) >= 2) {
        // Installment series: create N transactions immediately
        const n = parseInt(installmentCount)
        const baseDesc = form.description || ''
        const splitPayload = useSplits
          ? splits.filter(s => s.amount !== '' && s.amount !== '0').map(s => ({ categories_id: s.categories_id ? Number(s.categories_id) : null, amount: parseFloat(s.amount), memo: s.memo || null }))
          : [{ categories_id: form.categories_id ? Number(form.categories_id) : null, amount: parseFloat(form.total_amount), memo: form.memo || null }]
        for (let i = 0; i < n; i++) {
          const instDate = addPeriod(form.date, installmentFreq, i)
          const instDesc = baseDesc ? `${baseDesc} (${i + 1}/${n})` : `(${i + 1}/${n})`
          const res = await createTransaction({
            accounts_id: form.accounts_id,
            date: instDate,
            description: instDesc,
            total_amount: parseFloat(form.total_amount),
            payees_id: form.payees_id ? Number(form.payees_id) : null,
            accounts_id_target: null,
            ...statusFields,
          }) as { id: number }
          if (splitPayload.length > 0) await upsertSplits(res.id, splitPayload)
        }
      } else {
        // Regular transaction
        const payload: Record<string, unknown> = {
          accounts_id: form.accounts_id,
          date: form.date,
          description: form.description || null,
          total_amount: parseFloat(form.total_amount),
          payees_id: form.payees_id ? Number(form.payees_id) : null,
          accounts_id_target: null,
          ...statusFields,
        }

        let txId: number
        if (form.id) {
          await updateTransaction(form.id, payload)
          txId = form.id
        } else {
          const res = await createTransaction(payload) as { id: number }
          txId = res.id
        }

        // Upsert splits
        if (useSplits) {
          const validSplits = splits
            .filter(s => s.amount !== '' && s.amount !== '0')
            .map(s => ({ categories_id: s.categories_id ? Number(s.categories_id) : null, amount: parseFloat(s.amount), memo: s.memo || null }))
          if (validSplits.length > 0) await upsertSplits(txId, validSplits)
        } else {
          await upsertSplits(txId, [{
            categories_id: form.categories_id ? Number(form.categories_id) : null,
            amount: parseFloat(form.total_amount),
            memo: form.memo || null,
          }])
        }

        // Create recurring template if requested
        if (!form.id && recurringEnabled && recurringName.trim()) {
          await createRecurringTemplate({
            name: recurringName.trim(),
            accounts_id: form.accounts_id,
            payees_id: form.payees_id ? Number(form.payees_id) : null,
            description: form.description || null,
            total_amount: parseFloat(form.total_amount),
            periodicity: recurringFreq,
            next_due_date: recurringNextDue,
            accounts_id_target: null,
          })
        }
      }

      qc.invalidateQueries({ queryKey: ['transactions'] })
      setModalOpen(false)
    } catch (e: unknown) {
      setSaveError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (id: number) => {
    if (!confirm('Delete this transaction?')) return
    await deleteTransaction(id)
    qc.invalidateQueries({ queryKey: ['transactions'] })
  }

  const onRowClicked = (e: RowClickedEvent) => {
    if (e.event && (e.event as MouseEvent).detail === 2) {
      openEdit(e.data as Record<string, unknown>)
    }
  }

  const selectedAccount = (accounts as Record<string, unknown>[]).find(a => a.id === accountId)

  return (
    <div className="flex flex-col h-full">
      <PageHeader
        title="Cash Register"
        subtitle={selectedAccount ? `${String(selectedAccount.name)} · ${fmtEur(Number(selectedAccount.balance))}` : 'Select an account'}
        actions={
          <div className="flex items-center gap-2">
            <div className="relative">
              <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
              <input
                className="pl-8 pr-3 py-1.5 text-sm rounded-md border border-slate-300 w-56 focus:outline-none focus:ring-1 focus:ring-blue-500"
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
            <Button size="sm" onClick={openNew} disabled={!accountId}><Plus size={14} /> Add</Button>
          </div>
        }
      />

      {/* Filters */}
      <div className="flex items-center gap-3 px-6 py-3 bg-white border-b border-slate-200 flex-wrap">
        <Select className="w-56" value={accountId ?? ''} onChange={e => { setAccountId(Number(e.target.value) || null); setOffset(0) }}>
          <option value="">— Select account —</option>
          {cashAccounts.map((a) => (
            <option key={String(a.id)} value={String(a.id)}>
              {String(a.name)}{!a.is_active ? ' (inactive)' : ''}
            </option>
          ))}
        </Select>
        <label className="flex items-center gap-1.5 text-xs text-slate-500 cursor-pointer select-none">
          <input type="checkbox" checked={showInactive} onChange={e => setShowInactive(e.target.checked)} className="rounded" />
          Show inactive
        </label>

        <div className="relative">
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
          <Input className="pl-8 w-52" placeholder="Search payee / description…" value={search} onChange={e => { setSearch(e.target.value); setOffset(0) }} />
        </div>

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
              onChange={e => setToDate(e.target.checked ? today() : '2099-12-31')}
            />
            <span className="text-xs text-slate-500">Today</span>
          </label>
        </div>

        <span className="text-xs text-slate-400 ml-auto">{txQuery.data?.total != null ? `${txQuery.data.total.toLocaleString()} transactions` : ''}</span>
      </div>

      {/* Grid */}
      <div className="px-6 py-4 flex-1">
        {!accountId ? (
          <div className="flex items-center justify-center h-64 text-slate-400 text-sm">Select an account to view transactions</div>
        ) : txQuery.isLoading ? (
          <div className="flex items-center justify-center h-64"><Spinner /></div>
        ) : (
          <Card className="overflow-hidden">
            <div className="ag-theme-alpine" style={{ height: 'calc(100vh - 240px)', width: '100%' }}>
              <AgGridReact
                ref={gridRef}
                rowData={txQuery.data?.transactions ?? []}
                columnDefs={COL_DEFS}
                onGridReady={onGridReady}
                onRowClicked={onRowClicked}
                defaultColDef={{ resizable: true, sortable: true, filter: true }}
                rowSelection="single"
                suppressCellFocus={false}
                getRowId={p => p.data.id}
                context={{ onEdit: openEdit, onDelete: handleDelete }}
              />
            </div>

            {txQuery.data?.total > PAGE_SIZE && (
              <div className="flex items-center justify-between px-4 py-2 border-t border-slate-100 text-sm text-slate-600">
                <Button variant="secondary" size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</Button>
                <span>Page {Math.floor(offset / PAGE_SIZE) + 1} of {Math.ceil(txQuery.data.total / PAGE_SIZE)}</span>
                <Button variant="secondary" size="sm" disabled={offset + PAGE_SIZE >= txQuery.data.total} onClick={() => setOffset(offset + PAGE_SIZE)}>Next</Button>
              </div>
            )}
          </Card>
        )}
      </div>

      {/* Transaction Modal */}
      {modalOpen && form && (
        <TxModal
          form={form}
          splits={splits}
          useSplits={useSplits}
          setUseSplits={setUseSplits}
          onFormChange={setForm}
          onSplitsChange={setSplits}
          payees={payees as Record<string, unknown>[]}
          categories={categories as Record<string, unknown>[]}
          accounts={accounts as Record<string, unknown>[]}
          onSave={handleSave}
          onDelete={form.id ? async () => { if (confirm('Delete this transaction?')) { await handleDelete(form.id!); setModalOpen(false) } } : undefined}
          onClose={() => setModalOpen(false)}
          saving={saving}
          error={saveError}
          recurringEnabled={recurringEnabled}
          setRecurringEnabled={setRecurringEnabled}
          recurringName={recurringName}
          setRecurringName={setRecurringName}
          recurringFreq={recurringFreq}
          setRecurringFreq={setRecurringFreq}
          recurringNextDue={recurringNextDue}
          setRecurringNextDue={setRecurringNextDue}
          installmentEnabled={installmentEnabled}
          setInstallmentEnabled={setInstallmentEnabled}
          installmentCount={installmentCount}
          setInstallmentCount={setInstallmentCount}
          installmentFreq={installmentFreq}
          setInstallmentFreq={setInstallmentFreq}
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
            <div className="overflow-y-auto">
              {globalSearchQuery.isLoading && <div className="flex justify-center py-8"><Spinner /></div>}
              {!globalSearchQuery.isLoading && (!globalSearchQuery.data?.length) && (
                <div className="text-center text-sm text-slate-400 py-10">No results for "{globalQ}"</div>
              )}
              {(globalSearchQuery.data as Record<string, unknown>[] ?? []).map((row, i) => (
                <button
                  key={i}
                  className="w-full flex items-center gap-4 px-4 py-3 hover:bg-slate-50 border-b border-slate-100 last:border-0 text-left"
                  onClick={() => {
                    setAccountId(Number(row.accounts_id))
                    setGlobalOpen(false)
                    setGlobalSearch('')
                  }}
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-slate-400 shrink-0">{fmtDate(String(row.date))}</span>
                      <span className="text-sm font-medium text-slate-800 truncate">{String(row.payee || row.description || '—')}</span>
                      {row.payee && row.description && <span className="text-xs text-slate-400 truncate">{String(row.description)}</span>}
                    </div>
                    <div className="flex items-center gap-2 mt-0.5">
                      <span className="text-xs text-slate-500 truncate">{String(row.account_name)}</span>
                      {row.category && <span className="text-xs text-slate-400 truncate">· {String(row.category)}</span>}
                    </div>
                  </div>
                  <span className={`text-sm font-semibold tabular-nums shrink-0 ${Number(row.amount) < 0 ? 'text-red-600' : 'text-green-700'}`}>
                    {fmtEur(Number(row.amount))}
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
                    qc.invalidateQueries({ queryKey: ['transactions'] })
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
                    qc.invalidateQueries({ queryKey: ['transactions'] })
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
    </div>
  )
}
