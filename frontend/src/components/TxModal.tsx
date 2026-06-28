import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getPayeeTopCategories } from '@/lib/api'
import { Input, Button, SearchableSelect, useEscapeKey } from '@/components/ui'
import { fmtEur } from '@/lib/utils'
import { Plus, X, Save, ArrowLeftRight } from 'lucide-react'

export const PERIODICITIES = ['Daily', 'Weekly', 'Biweekly', 'Monthly', 'Quarterly', 'Semiannually', 'Annually']

export function today() { return new Date().toISOString().slice(0, 10) }

export type TxForm = {
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

export type SplitRow = { categories_id: string; amount: string; memo: string }

export function emptyForm(accountId: number): TxForm {
  return {
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
  }
}

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
  recurringEnabled: boolean
  setRecurringEnabled: (v: boolean) => void
  recurringName: string
  setRecurringName: (v: string) => void
  recurringFreq: string
  setRecurringFreq: (v: string) => void
  recurringNextDue: string
  setRecurringNextDue: (v: string) => void
  installmentEnabled: boolean
  setInstallmentEnabled: (v: boolean) => void
  installmentCount: string
  setInstallmentCount: (v: string) => void
  installmentFreq: string
  setInstallmentFreq: (v: string) => void
}

const CASH_ACCOUNT_TYPES = ['Cash', 'Checking', 'Savings', 'Credit Card', 'Loan', 'Real Estate', 'Vehicle', 'Asset', 'Other']

export function TxModal({
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
  useEscapeKey(onClose)
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
            <div className="space-y-3">
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
              <div>
                <label className="text-xs font-medium text-slate-500 block mb-1">Payee</label>
                <SearchableSelect value={form.payees_id} onChange={v => set('payees_id', v)}
                  options={payees.map(p => ({ value: String(p.id), label: String(p.name) }))} />
              </div>
            </div>
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
                  {(() => {
                    const splitsTotal = splits.reduce((sum, s) => sum + (parseFloat(s.amount) || 0), 0)
                    const txTotal = parseFloat(form.total_amount) || 0
                    const remaining = txTotal - splitsTotal
                    const pct = txTotal !== 0 ? Math.round((splitsTotal / txTotal) * 100) : 0
                    const isMatch = Math.round(remaining * 100) === 0
                    return (
                      <div className={`flex justify-between text-xs pt-1 border-t border-slate-100 mt-1 ${isMatch ? 'text-green-600' : 'text-red-500'}`}>
                        <span>{fmtEur(splitsTotal)} allocated ({pct}%)</span>
                        <span>{isMatch ? '✓ 100% covered' : `Unallocated: ${fmtEur(remaining)}`}</span>
                      </div>
                    )
                  })()}
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

          {/* Recurring / Installment only for new transactions */}
          {!form.id && (
            <div className="space-y-2">
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

// Dummy state hook for callers that don't use recurring/installment features
export function useNoOpRecurring() {
  const [recurringEnabled, setRecurringEnabled] = useState(false)
  const [recurringName, setRecurringName] = useState('')
  const [recurringFreq, setRecurringFreq] = useState('Monthly')
  const [recurringNextDue, setRecurringNextDue] = useState(today())
  const [installmentEnabled, setInstallmentEnabled] = useState(false)
  const [installmentCount, setInstallmentCount] = useState('')
  const [installmentFreq, setInstallmentFreq] = useState('Monthly')
  return {
    recurringEnabled, setRecurringEnabled,
    recurringName, setRecurringName,
    recurringFreq, setRecurringFreq,
    recurringNextDue, setRecurringNextDue,
    installmentEnabled, setInstallmentEnabled,
    installmentCount, setInstallmentCount,
    installmentFreq, setInstallmentFreq,
  }
}
