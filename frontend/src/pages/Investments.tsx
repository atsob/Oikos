import { useState, useCallback, useRef, useMemo, useEffect } from 'react'
import { usePersist } from '@/lib/hooks'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AgGridReact } from 'ag-grid-react'
import type { ColDef } from 'ag-grid-community'
import {
  getHoldings, getInvestments, getAccounts, getSecurities, getFxRates,
  updateHolding, stakingReinvest, getLinkedAccount,
  getTransactions, createTransaction, updateTransaction, deleteTransaction,
  getSplits, upsertSplits, createTransfer, getPayees, getCategories, getPayeeTopCategories,
} from '@/lib/api'
import { api } from '@/lib/api'
import { PageHeader, Input, Button, Spinner, Card, SearchableSelect, ColHeader, useSortTable, useEscapeKey } from '@/components/ui'
import { fmtEur, fmtDate, fmtNum, fmtQty } from '@/lib/utils'
import { Plus, X, Save, RefreshCw } from 'lucide-react'
import { InvTransactionModal, emptyInvForm, ACTIONS, INSTRUMENT_TYPES, CASH_ACTIONS, createInvestment, updateInvestment, deleteInvestment } from '@/components/InvTransactionModal'
import type { InvFormData } from '@/components/InvTransactionModal'

const INVESTMENT_ACCOUNT_TYPES = ['Brokerage', 'Pension', 'Other Investment', 'Margin']


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
    cellRenderer: (p: { value: string; data: Record<string, unknown> }) =>
      p.data.securities_id
        ? <button onClick={() => navigate(`/securities/${p.data.securities_id}`)} className="text-blue-600 hover:underline text-left truncate w-full">{p.value}</button>
        : <span>{p.value}</span>
  },
  { field: 'quantity', headerName: 'Qty', width: 100, type: 'numericColumn', valueFormatter: p => p.value != null ? fmtQty(Number(p.value), 8) : '—' },
  { field: 'price', headerName: 'Price', width: 100, type: 'numericColumn', valueFormatter: p => p.value != null ? fmtEur(Number(p.value)) : '—' },
  { field: 'commission', headerName: 'Commission', width: 110, type: 'numericColumn', valueFormatter: p => p.value != null ? fmtEur(Number(p.value)) : '—' },
  { field: 'total_seccur', headerName: 'Total (sec)', width: 120, type: 'numericColumn', valueFormatter: p => p.value != null ? fmtEur(Number(p.value)) : '—' },
  { field: 'total', headerName: 'Total (acc)', width: 120, type: 'numericColumn', valueFormatter: p => fmtEur(Number(p.value)), cellStyle: { fontWeight: 600 } },
  { field: 'fx_rate', headerName: 'FX', width: 80, type: 'numericColumn', valueFormatter: p => p.value ? fmtNum(Number(p.value), 4) : '—' },
  { field: 'currency', headerName: 'Curr', width: 65 },
  { field: 'instrument_type', headerName: 'Instrument', width: 110 },
  { field: 'account', headerName: 'Account', flex: 1, minWidth: 130 },
  { field: 'cash_account', headerName: 'Cash Account', flex: 1, minWidth: 120,
    valueFormatter: p => p.value ?? '—',
    cellStyle: p => p.value ? { color: '#2563eb', fontSize: '12px' } : { color: '#cbd5e1', fontSize: '12px' } },
  { field: 'notes', headerName: 'Notes', flex: 1, minWidth: 120 },
]

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

// ── Cash tab columns (mirrors Register) ──────────────────────────────────────
function CashAmountCell({ value }: { value: number }) {
  return <span className={`font-semibold tabular-nums ${value < 0 ? 'text-red-600' : 'text-green-700'}`}>{fmtEur(value)}</span>
}
function CashBalanceCell({ value }: { value: number }) {
  return <span className={`tabular-nums ${value < 0 ? 'text-red-600' : 'text-slate-800'}`}>{fmtEur(value)}</span>
}
function CashStatusCell({ data }: { data: Record<string, unknown> }) {
  if (data.is_draft) return <span className="inline-flex items-center px-1.5 py-0.5 rounded text-xs bg-slate-100 text-slate-500">Draft</span>
  if (!data.cleared && !data.reconciled) return <span className="inline-flex items-center px-1.5 py-0.5 rounded text-xs bg-yellow-100 text-yellow-700">Pending</span>
  return (
    <span className="inline-flex items-center gap-1">
      {data.cleared && <span className="inline-flex items-center px-1.5 py-0.5 rounded text-xs bg-green-100 text-green-700">Cleared</span>}
      {data.reconciled && <span className="inline-flex items-center px-1.5 py-0.5 rounded text-xs bg-blue-100 text-blue-700">Reconciled</span>}
    </span>
  )
}

const CASH_COLS: ColDef[] = [
  { field: 'date', headerName: 'Date', width: 100, sort: 'desc', valueFormatter: p => fmtDate(p.value) },
  { field: 'payee', headerName: 'Payee', flex: 1, minWidth: 140 },
  { field: 'description', headerName: 'Description', flex: 2, minWidth: 180 },
  { field: 'category', headerName: 'Category', flex: 1, minWidth: 140 },
  { field: 'target_account', headerName: 'Transfer To', width: 130 },
  { field: 'memo', headerName: 'Memo', width: 140 },
  { field: 'amount', headerName: 'Amount', width: 120, cellRenderer: CashAmountCell, type: 'numericColumn' },
  { field: 'running_balance', headerName: 'Balance', width: 120, cellRenderer: CashBalanceCell, type: 'numericColumn' },
  { headerName: 'Status', width: 170, cellRenderer: CashStatusCell },
]

const CASH_ACCOUNT_TYPES_FOR_TRANSFER = ['Cash', 'Checking', 'Savings', 'Credit Card', 'Loan', 'Real Estate', 'Vehicle', 'Asset', 'Other']
const PERIODICITIES = ['Daily', 'Weekly', 'Biweekly', 'Monthly', 'Quarterly', 'Semiannually', 'Annually']

type CashTxForm = {
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

const emptyCashForm = (accountId: number): CashTxForm => ({
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

function CashTxModal({
  form, splits, useSplits, setUseSplits,
  onFormChange, onSplitsChange,
  payees, categories, allAccounts,
  onSave, onDelete, onClose, saving, error,
  recurringEnabled, setRecurringEnabled,
  recurringName, setRecurringName,
  recurringFreq, setRecurringFreq,
  recurringNextDue, setRecurringNextDue,
  installmentEnabled, setInstallmentEnabled,
  installmentCount, setInstallmentCount,
  installmentFreq, setInstallmentFreq,
}: {
  form: CashTxForm; splits: SplitRow[]; useSplits: boolean; setUseSplits: (v: boolean) => void
  onFormChange: (f: CashTxForm) => void; onSplitsChange: (s: SplitRow[]) => void
  payees: Record<string, unknown>[]; categories: Record<string, unknown>[]
  allAccounts: Record<string, unknown>[]
  onSave: () => void; onDelete?: () => void; onClose: () => void
  saving: boolean; error: string | null
  recurringEnabled: boolean; setRecurringEnabled: (v: boolean) => void
  recurringName: string; setRecurringName: (v: string) => void
  recurringFreq: string; setRecurringFreq: (v: string) => void
  recurringNextDue: string; setRecurringNextDue: (v: string) => void
  installmentEnabled: boolean; setInstallmentEnabled: (v: boolean) => void
  installmentCount: string; setInstallmentCount: (v: string) => void
  installmentFreq: string; setInstallmentFreq: (v: string) => void
}) {
  useEscapeKey(onClose)
  const set = (k: keyof CashTxForm, v: unknown) => onFormChange({ ...form, [k]: v })
  const addSplit = () => onSplitsChange([...splits, { categories_id: '', amount: '', memo: '' }])
  const removeSplit = (i: number) => onSplitsChange(splits.filter((_, j) => j !== i))
  const setSplit = (i: number, k: keyof SplitRow, v: string) =>
    onSplitsChange(splits.map((s, j) => j === i ? { ...s, [k]: v } : s))
  const transferAccounts = allAccounts.filter(a => a.id !== form.accounts_id && CASH_ACCOUNT_TYPES_FOR_TRANSFER.includes(String(a.type ?? '')))

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

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-xl max-h-[92vh] overflow-y-auto">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200">
          <h2 className="text-base font-semibold">{form.id ? 'Edit Transaction' : 'New Transaction'}</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600"><X size={18} /></button>
        </div>
        <div className="px-5 py-4 space-y-3">
          {/* Regular / Transfer toggle */}
          <div className="flex gap-2">
            <button type="button" onClick={() => set('is_transfer', false)}
              className={`flex-1 py-1.5 text-sm rounded-md border transition-colors ${!form.is_transfer ? 'bg-blue-600 text-white border-blue-600' : 'border-slate-300 text-slate-600 hover:bg-slate-50'}`}>
              Pay/Receive
            </button>
            <button type="button" onClick={() => set('is_transfer', true)}
              className={`flex-1 py-1.5 text-sm rounded-md border transition-colors ${form.is_transfer ? 'bg-blue-600 text-white border-blue-600' : 'border-slate-300 text-slate-600 hover:bg-slate-50'}`}>
              Transfer
            </button>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs font-medium text-slate-500 block mb-1">Date *</label>
              <Input type="date" value={form.date} onChange={e => set('date', e.target.value)} />
              <label className="flex items-center gap-1.5 mt-1 cursor-pointer w-fit">
                <input type="checkbox" className="rounded" checked={form.date === today()} onChange={e => { if (e.target.checked) set('date', today()) }} />
                <span className="text-xs text-slate-500">Today</span>
              </label>
            </div>
            <div>
              <label className="text-xs font-medium text-slate-500 block mb-1">Amount *</label>
              <Input type="number" step="0.01" placeholder="0.00" value={form.total_amount} onChange={e => set('total_amount', e.target.value)} />
            </div>
          </div>

          {form.is_transfer ? (
            <>
              <div>
                <label className="text-xs font-medium text-slate-500 block mb-1">Transfer To Account *</label>
                <select className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm" value={form.transfer_account_id} onChange={e => set('transfer_account_id', e.target.value)}>
                  <option value="">— select target account —</option>
                  {transferAccounts.map(a => <option key={String(a.id)} value={String(a.id)}>{String(a.name)}</option>)}
                </select>
              </div>
              <div>
                <label className="text-xs font-medium text-slate-500 block mb-1">Description</label>
                <Input value={form.description} onChange={e => set('description', e.target.value)} placeholder="Description / note" />
              </div>
            </>
          ) : (
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
                <input type="checkbox" id="cash-use-splits" checked={useSplits} onChange={e => setUseSplits(e.target.checked)} className="rounded" />
                <label htmlFor="cash-use-splits" className="text-sm text-slate-600">Split categories</label>
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

          {!form.id && (
            <div className="space-y-2">
              <div className="border border-slate-200 rounded-lg">
                <button type="button" className="w-full flex items-center gap-2 px-3 py-2 text-sm text-slate-600 hover:bg-slate-50 rounded-lg"
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

              <div className="border border-slate-200 rounded-lg">
                <button type="button" className="w-full flex items-center gap-2 px-3 py-2 text-sm text-slate-600 hover:bg-slate-50 rounded-lg"
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
        <div className="flex justify-between px-5 py-3 border-t border-slate-200">
          <div>{form.id && onDelete && <Button variant="destructive" size="sm" onClick={onDelete} disabled={saving}>Delete</Button>}</div>
          <div className="flex gap-2">
            <Button variant="secondary" onClick={onClose}>Cancel</Button>
            <Button onClick={onSave} disabled={saving}><Save size={14} /> {saving ? 'Saving…' : 'Save'}</Button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Holdings table (view + inline edit for qty & staking) ─────────────────────
type HoldingEdit = { quantity: string; staking: boolean }

function HoldingsTable({ holdings, onSaved }: { holdings: Record<string, unknown>[]; onSaved: () => void }) {
  const [edits, setEdits] = useState<Record<number, HoldingEdit>>({})
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const { sorted: sortedHoldings, sortKey: hSK, sortDir: hSD, toggleSort: hSort } = useSortTable(holdings, 'value_eur', 'desc')

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
        const row = holdings.find(h => Number(h.id) === id)!
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

  const fmt6 = (v: unknown) => v != null ? fmtQty(Number(v), 6) : '—'
  const fmtP = (v: unknown) => v != null ? fmtEur(Number(v)) : '—'
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
                  <td className="py-1.5 pr-3 text-right tabular-nums text-slate-600">{fmtP(row.simple_avg_price)}</td>
                  <td className="py-1.5 pr-3 text-right tabular-nums text-slate-600">{fmtP(row.fifo_avg_price)}</td>
                  <td className="py-1.5 pr-3 text-right tabular-nums text-slate-600">{fmtP(row.last_price)}</td>
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
  const [tab, setTab] = usePersist<'holdings' | 'transactions' | 'cash'>('investments_tab', 'holdings')
  const [accountId, setAccountId] = usePersist<number | null>('investments_accountId', null)
  const [showInactive, setShowInactive] = useState(false)
  const [includeClosed, setIncludeClosed] = useState(false)
  const [fromDate, setFromDate] = useState(monthsAgo(6))
  const [toDate, setToDate] = useState('2099-12-31')
  const [activePeriod, setActivePeriod] = useState('6M')
  const [actionFilter, setActionFilter] = useState('')
  const [offset, setOffset] = useState(0)
  const PAGE_SIZE = 200

  const [modalOpen, setModalOpen] = useState(false)
  const [editId, setEditId] = useState<number | null>(null)
  const [form, setForm] = useState<InvFormData>(emptyInvForm())
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  const { data: accounts = [] } = useQuery({ queryKey: ['accounts'], queryFn: () => import('@/lib/api').then(m => m.getAccounts()) })
  const { data: securities = [] } = useQuery({ queryKey: ['securities'], queryFn: () => import('@/lib/api').then(m => m.getSecurities()) })

  const investmentAccounts = (accounts as Record<string, unknown>[])
    .filter(a => INVESTMENT_ACCOUNT_TYPES.includes(String(a.type ?? '')))
    .filter(a => showInactive || Boolean(a.is_active))

  const { data: holdings = [], isLoading: holdingsLoading } = useQuery({
    queryKey: ['holdings', accountId, includeClosed],
    queryFn: () => getHoldings(accountId ?? undefined, includeClosed),
  })

  const invParams = { account_id: accountId ?? undefined, from_date: fromDate, to_date: toDate, action: actionFilter || undefined, limit: PAGE_SIZE, offset }
  const { data: invData, isLoading: invLoading } = useQuery({
    queryKey: ['investments', invParams],
    queryFn: () => getInvestments(invParams),
    enabled: tab === 'transactions',
  })

  // ── Cash tab state ────────────────────────────────────────────────────────
  const [cashFromDate, setCashFromDate] = useState(monthsAgo(6))
  const [cashToDate, setCashToDate] = useState('2099-12-31')
  const [cashActivePeriod, setCashActivePeriod] = useState('6M')
  const [cashModalOpen, setCashModalOpen] = useState(false)
  const [cashForm, setCashForm] = useState<CashTxForm | null>(null)
  const [cashSplits, setCashSplits] = useState<SplitRow[]>([{ categories_id: '', amount: '', memo: '' }])
  const [cashUseSplits, setCashUseSplits] = useState(false)
  const [cashSaving, setCashSaving] = useState(false)
  const [cashSaveError, setCashSaveError] = useState<string | null>(null)
  const [cashRecurringEnabled, setCashRecurringEnabled] = useState(false)
  const [cashRecurringName, setCashRecurringName] = useState('')
  const [cashRecurringFreq, setCashRecurringFreq] = useState('Monthly')
  const [cashRecurringNextDue, setCashRecurringNextDue] = useState(today())
  const [cashInstallmentEnabled, setCashInstallmentEnabled] = useState(false)
  const [cashInstallmentCount, setCashInstallmentCount] = useState('')
  const [cashInstallmentFreq, setCashInstallmentFreq] = useState('Monthly')

  const { data: payees = [] } = useQuery({ queryKey: ['payees'], queryFn: () => getPayees() })
  const { data: categories = [] } = useQuery({ queryKey: ['categories'], queryFn: () => getCategories() })

  const { data: linkedAccountData } = useQuery({
    queryKey: ['linked-account', accountId],
    queryFn: () => getLinkedAccount(accountId!),
    enabled: accountId != null,
  })
  const linkedCashAccountId: number | null = (linkedAccountData as { linked_account_id: number | null } | undefined)?.linked_account_id ?? null

  const cashParams = { account_id: accountId ?? undefined, from_date: cashFromDate, to_date: cashToDate, limit: PAGE_SIZE }
  const { data: cashData, isLoading: cashLoading } = useQuery({
    queryKey: ['inv-cash', cashParams],
    queryFn: () => getTransactions(cashParams),
    enabled: tab === 'cash' && accountId != null,
  })
  const cashRows = ((cashData as { transactions?: Record<string, unknown>[] } | null)?.transactions ?? []) as Record<string, unknown>[]

  const handleCashSave = async () => {
    if (!cashForm || !accountId) return
    setCashSaving(true); setCashSaveError(null)
    try {
      const statusFields = { is_draft: cashForm.is_draft, cleared: cashForm.cleared, reconciled: cashForm.reconciled }
      if (cashForm.is_transfer && !cashForm.id) {
        if (!cashForm.transfer_account_id) throw new Error('Select a target account for the transfer')
        await createTransfer({
          from_account_id: cashForm.accounts_id,
          to_account_id: Number(cashForm.transfer_account_id),
          date: cashForm.date,
          amount: parseFloat(cashForm.total_amount),
          description: cashForm.description || null,
          ...statusFields,
        })
      } else if (cashForm.is_transfer && cashForm.id) {
        await updateTransaction(cashForm.id, {
          date: cashForm.date,
          description: cashForm.description || null,
          total_amount: parseFloat(cashForm.total_amount),
          accounts_id_target: cashForm.transfer_account_id ? Number(cashForm.transfer_account_id) : null,
          ...statusFields,
        })
      } else if (!cashForm.id && cashInstallmentEnabled && parseInt(cashInstallmentCount) >= 2) {
        const n = parseInt(cashInstallmentCount)
        const baseDesc = cashForm.description || ''
        const splitPayload = cashUseSplits
          ? cashSplits.filter(s => s.amount !== '' && s.amount !== '0').map(s => ({ categories_id: s.categories_id ? Number(s.categories_id) : null, amount: parseFloat(s.amount), memo: s.memo || null }))
          : [{ categories_id: cashForm.categories_id ? Number(cashForm.categories_id) : null, amount: parseFloat(cashForm.total_amount), memo: cashForm.memo || null }]
        for (let i = 0; i < n; i++) {
          const instDate = addPeriod(cashForm.date, cashInstallmentFreq, i)
          const instDesc = baseDesc ? `${baseDesc} (${i + 1}/${n})` : `(${i + 1}/${n})`
          const res = await createTransaction({
            accounts_id: cashForm.accounts_id,
            date: instDate,
            description: instDesc,
            total_amount: parseFloat(cashForm.total_amount),
            payees_id: cashForm.payees_id ? Number(cashForm.payees_id) : null,
            accounts_id_target: null,
            ...statusFields,
          }) as { id: number }
          if (splitPayload.length > 0) await upsertSplits(res.id, splitPayload)
        }
      } else {
        const payload: Record<string, unknown> = {
          accounts_id: cashForm.accounts_id,
          date: cashForm.date,
          description: cashForm.description || null,
          total_amount: parseFloat(cashForm.total_amount),
          payees_id: cashForm.payees_id ? Number(cashForm.payees_id) : null,
          accounts_id_target: null,
          ...statusFields,
        }
        let txId: number
        if (cashForm.id) {
          await updateTransaction(cashForm.id, payload)
          txId = cashForm.id
        } else {
          const res = await createTransaction(payload) as { id: number }
          txId = res.id
        }
        if (cashUseSplits) {
          const validSplits = cashSplits
            .filter(s => s.amount !== '' && s.amount !== '0')
            .map(s => ({ categories_id: s.categories_id ? Number(s.categories_id) : null, amount: parseFloat(s.amount), memo: s.memo || null }))
          if (validSplits.length > 0) await upsertSplits(txId, validSplits)
        } else {
          await upsertSplits(txId, [{
            categories_id: cashForm.categories_id ? Number(cashForm.categories_id) : null,
            amount: parseFloat(cashForm.total_amount),
            memo: cashForm.memo || null,
          }])
        }
        if (!cashForm.id && cashRecurringEnabled && cashRecurringName.trim()) {
          const { createRecurringTemplate } = await import('@/lib/api')
          await createRecurringTemplate({
            name: cashRecurringName.trim(),
            accounts_id: cashForm.accounts_id,
            payees_id: cashForm.payees_id ? Number(cashForm.payees_id) : null,
            description: cashForm.description || null,
            total_amount: parseFloat(cashForm.total_amount),
            periodicity: cashRecurringFreq,
            next_due_date: cashRecurringNextDue,
            accounts_id_target: null,
          })
        }
      }
      qc.invalidateQueries({ queryKey: ['inv-cash'] })
      setCashModalOpen(false); setCashForm(null)
    } catch (e) { setCashSaveError(e instanceof Error ? e.message : 'Save failed') }
    finally { setCashSaving(false) }
  }

  const handleCashDelete = async () => {
    if (!cashForm?.id) return
    setCashSaving(true)
    try {
      await deleteTransaction(cashForm.id)
      qc.invalidateQueries({ queryKey: ['inv-cash'] })
      setCashModalOpen(false); setCashForm(null)
    } catch (e) { setCashSaveError(e instanceof Error ? e.message : 'Delete failed') }
    finally { setCashSaving(false) }
  }

  const openCashNew = () => {
    if (!accountId) return
    setCashForm(emptyCashForm(accountId))
    setCashSplits([{ categories_id: '', amount: '', memo: '' }])
    setCashUseSplits(false); setCashSaveError(null)
    setCashRecurringEnabled(false); setCashRecurringName(''); setCashRecurringFreq('Monthly'); setCashRecurringNextDue(today())
    setCashInstallmentEnabled(false); setCashInstallmentCount(''); setCashInstallmentFreq('Monthly')
    setCashModalOpen(true)
  }

  const openCashEdit = useCallback(async (row: Record<string, unknown>) => {
    const txSplits = await getSplits(Number(row.id))
    const loadedSplits = Array.isArray(txSplits) && txSplits.length > 0
      ? (txSplits as Record<string, unknown>[]).map(s => ({ categories_id: String(s.categories_id ?? ''), amount: String(s.amount ?? ''), memo: String(s.memo ?? '') }))
      : [{ categories_id: '', amount: '', memo: '' }]
    setCashForm({
      id: Number(row.id),
      accounts_id: accountId!,
      date: String(row.date ?? '').slice(0, 10),
      description: String(row.description ?? ''),
      total_amount: row.amount != null ? String(row.amount) : '',
      payees_id: String(row.payees_id ?? ''),
      categories_id: String((txSplits as Record<string,unknown>[])?.[0]?.categories_id ?? ''),
      memo: String(loadedSplits[0]?.memo ?? ''),
      is_draft: Boolean(row.is_draft),
      cleared: Boolean(row.cleared),
      reconciled: Boolean(row.reconciled),
      is_transfer: Boolean(row.accounts_id_target),
      transfer_account_id: String(row.accounts_id_target ?? ''),
    })
    setCashSplits(loadedSplits)
    setCashUseSplits(loadedSplits.length > 1)
    setCashSaveError(null); setCashModalOpen(true)
  }, [accountId])

  const totalValue = (holdings as Record<string, unknown>[]).reduce((s, h) => s + Number(h.value_eur ?? 0), 0)
  const totalGain = (holdings as Record<string, unknown>[]).reduce((s, h) =>
    s + Number(h.quantity) * (Number(h.last_price ?? 0) - Number(h.fifo_avg_price ?? h.simple_avg_price ?? 0)) * Number(h.fx_rate ?? 1), 0)

  const selectedAccount = investmentAccounts.find(a => Number(a.id) === accountId)

  const CASH_OUT_ACTIONS = new Set(['Buy', 'MiscExp', 'CashOut'])
  const CASH_IN_ACTIONS = new Set(['Sell', 'Dividend', 'IntInc', 'CashIn', 'RtrnCap', 'MiscInc'])
  const invWithBalance = useMemo(() => {
    const rows = [...(invData?.investments ?? [])] as Record<string, unknown>[]
    let balance = 0
    const ascending = [...rows].reverse().map(r => {
      const action = String(r.action)
      const amount = Number(r.total ?? 0)
      if (CASH_OUT_ACTIONS.has(action)) balance -= amount
      else if (CASH_IN_ACTIONS.has(action)) balance += amount
      // ShrIn, ShrOut, Split, Grant, Vest, Exercise, Expire — no cash movement
      return { ...r, running_balance: Math.round(balance * 100) / 100 }
    })
    return ascending.reverse()
  }, [invData])

  const setPeriod = (label: string, from: string) => {
    setFromDate(from); setActivePeriod(label); setOffset(0)
  }

  const handleSave = async () => {
    setSaving(true); setSaveError(null)
    const payload = {
      accounts_id: Number(form.accounts_id),
      securities_id: Number(form.securities_id),
      date: form.date,
      action: form.action,
      quantity: form.quantity ? parseFloat(form.quantity) : null,
      price_per_share: form.price_per_share ? parseFloat(form.price_per_share) : null,
      commission: form.commission ? parseFloat(form.commission) : null,
      fx_rate: form.fx_rate ? parseFloat(form.fx_rate) : 1,
      total_amount_acccur: form.total_amount_acccur ? parseFloat(form.total_amount_acccur) : null,
      total_amount_seccur: form.total_amount_seccur ? parseFloat(form.total_amount_seccur) : null,
      instrument_type: form.instrument_type || null,
      description: form.description || null,
      cash_account_id: form.cash_account_id ? Number(form.cash_account_id) : null,
    }
    try {
      if (editId) { await updateInvestment(editId, payload) } else { await createInvestment(payload) }
      qc.invalidateQueries({ queryKey: ['investments'] })
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
      qc.invalidateQueries({ queryKey: ['investments'] })
      qc.invalidateQueries({ queryKey: ['holdings'] })
      setModalOpen(false); setEditId(null); setForm(emptyInvForm())
    } catch (e: unknown) {
      setSaveError(e instanceof Error ? e.message : 'Delete failed')
    } finally { setSaving(false) }
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
      commission: row.commission != null ? String(row.commission) : '0',
      fx_rate: row.fx_rate != null ? String(row.fx_rate) : '1',
      total_amount_acccur: row.total != null ? String(row.total) : '',
      total_amount_seccur: row.total_seccur != null ? String(row.total_seccur) : '',
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
          const runningBalance = invWithBalance.length > 0 ? Number((invWithBalance[0] as Record<string, unknown>).running_balance ?? 0) : 0
          const displayValue = totalValue !== 0 ? totalValue : runningBalance
          if (tab === 'holdings') return `${accName}Portfolio: ${fmtEur(totalValue)} · Unrealized: ${fmtEur(totalGain)}`
          if (selectedAccount) return `${accName}Balance: ${fmtEur(displayValue)}`
          return 'Investment transaction history'
        })()}
        actions={
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
          <select className="w-52 rounded-md border border-slate-300 px-3 py-1.5 text-sm" value={accountId ?? ''} onChange={e => { setAccountId(Number(e.target.value) || null); setOffset(0) }}>
            <option value="">All accounts</option>
            {investmentAccounts.map(a => (
              <option key={String(a.id)} value={String(a.id)}>
                {String(a.name)}{!a.is_active ? ' (inactive)' : ''}
              </option>
            ))}
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
              <Button size="sm" onClick={openCashNew}>
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
                <div className="ag-theme-alpine" style={{ height: 'calc(100vh - 280px)', width: '100%' }}>
                  <AgGridReact
                    rowData={cashRows}
                    columnDefs={CASH_COLS}
                    defaultColDef={{ resizable: true, sortable: true, filter: true }}
                    onRowClicked={e => { if (e.event && (e.event as MouseEvent).detail === 2) openCashEdit(e.data as Record<string, unknown>) }}
                    onGridReady={e => e.api.autoSizeAllColumns()}
                    onFirstDataRendered={e => e.api.autoSizeAllColumns()}
                    onRowDataUpdated={e => e.api.autoSizeAllColumns()}
                  />
                </div>
              )}
            </>
          )}

          {tab === 'transactions' && (
            invLoading ? <div className="flex justify-center py-12"><Spinner /></div> : (
              <>
                <div className="ag-theme-alpine" style={{ height: 'calc(100vh - 280px)', width: '100%' }}>
                  <AgGridReact
                    rowData={invWithBalance}
                    columnDefs={[
                      ...makeInvCols(navigate),
                      { field: 'running_balance', headerName: 'Balance', width: 120, type: 'numericColumn', pinned: 'right',
                        cellRenderer: CashBalanceCell,
                      },
                    ]}
                    defaultColDef={{ resizable: true, sortable: true, filter: true }}
                    onRowClicked={e => { if (e.event && (e.event as MouseEvent).detail === 2) openEdit(e.data as Record<string, unknown>) }}
                    onGridReady={e => e.api.autoSizeAllColumns()}
                    onFirstDataRendered={e => e.api.autoSizeAllColumns()}
                    onRowDataUpdated={e => e.api.autoSizeAllColumns()}
                  />
                </div>
                {(invData?.total ?? 0) > PAGE_SIZE && (
                  <div className="flex items-center justify-between px-4 py-2 border-t border-slate-100 text-sm text-slate-600">
                    <Button variant="secondary" size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</Button>
                    <span>Page {Math.floor(offset / PAGE_SIZE) + 1} of {Math.ceil((invData?.total ?? 0) / PAGE_SIZE)}</span>
                    <Button variant="secondary" size="sm" disabled={offset + PAGE_SIZE >= (invData?.total ?? 0)} onClick={() => setOffset(offset + PAGE_SIZE)}>Next</Button>
                  </div>
                )}
              </>
            )
          )}
        </Card>
      </div>

      {cashModalOpen && cashForm && (
        <CashTxModal
          form={cashForm}
          splits={cashSplits}
          useSplits={cashUseSplits}
          setUseSplits={setCashUseSplits}
          onFormChange={setCashForm}
          onSplitsChange={setCashSplits}
          payees={payees as Record<string, unknown>[]}
          categories={categories as Record<string, unknown>[]}
          allAccounts={accounts as Record<string, unknown>[]}
          onSave={handleCashSave}
          onDelete={cashForm.id ? handleCashDelete : undefined}
          onClose={() => { setCashModalOpen(false); setCashForm(null) }}
          saving={cashSaving}
          error={cashSaveError}
          recurringEnabled={cashRecurringEnabled}
          setRecurringEnabled={setCashRecurringEnabled}
          recurringName={cashRecurringName}
          setRecurringName={setCashRecurringName}
          recurringFreq={cashRecurringFreq}
          setRecurringFreq={setCashRecurringFreq}
          recurringNextDue={cashRecurringNextDue}
          setRecurringNextDue={setCashRecurringNextDue}
          installmentEnabled={cashInstallmentEnabled}
          setInstallmentEnabled={setCashInstallmentEnabled}
          installmentCount={cashInstallmentCount}
          setInstallmentCount={setCashInstallmentCount}
          installmentFreq={cashInstallmentFreq}
          setInstallmentFreq={setCashInstallmentFreq}
        />
      )}

      {modalOpen && (
        <InvTransactionModal
          form={form}
          onChange={setForm}
          accounts={investmentAccounts}
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

    </div>
  )
}
