import { useState, useMemo, useRef, useEffect, useCallback } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  getPayeeTopCategories, getSplits, upsertSplits,
  createTransaction, updateTransaction, deleteTransaction,
  createTransfer, createRecurringTemplate,
} from '@/lib/api'
import { Input, Button, useEscapeKey } from '@/components/ui'
import { fmtEur, cn } from '@/lib/utils'
import { Plus, X, Save, ArrowLeftRight } from 'lucide-react'
import { api } from '@/lib/api'

export const PERIODICITIES = ['Daily', 'Weekly', 'Biweekly', 'Monthly', 'Quarterly', 'Semiannually', 'Annually']

export const CATEGORY_TYPES = ['Income', 'Expense', 'Transfer', 'Trading', 'Investment', 'Dividend', 'Interest', 'Tax', 'Fee']

export function today() { return new Date().toISOString().slice(0, 10) }

/** Offsets dateStr by n periods of freq — shared by installment-series creation. */
export function addPeriod(dateStr: string, freq: string, n: number): string {
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
  onPayeeCreated: (p: { id: number; name: string }) => void
  onCategoryCreated?: (c: { id: number; full_path: string; type: string }) => void
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

/** Payee selector with inline "＋ Add new payee" when no match is found. */
function PayeeSelect({ value, onChange, payees, onPayeeCreated }: {
  value: string
  onChange: (v: string) => void
  payees: Record<string, unknown>[]
  onPayeeCreated: (p: { id: number; name: string }) => void
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [creating, setCreating] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const selectedLabel = payees.find(p => String(p.id) === value)?.name as string ?? ''
  const filtered = query
    ? payees.filter(p => String(p.name).toLowerCase().includes(query.toLowerCase()))
    : payees
  const noMatch = query.trim() !== '' && filtered.length === 0

  const handleOpen = () => { setOpen(true); setQuery(''); setTimeout(() => inputRef.current?.focus(), 0) }
  const handleSelect = useCallback((val: string) => { onChange(val); setOpen(false); setQuery('') }, [onChange])

  const handleCreate = async () => {
    const name = query.trim()
    if (!name) return
    setCreating(true)
    try {
      const res = await api.post('/static-data/payees', { name })
      const newPayee = { id: res.data.id, name }
      onPayeeCreated(newPayee)
      handleSelect(String(res.data.id))
    } finally {
      setCreating(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') { setOpen(false); setQuery('') }
    if (e.key === 'Enter') {
      if (noMatch) { handleCreate(); return }
      if (filtered[0]) handleSelect(String(filtered[0].id))
    }
  }

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => { if (!containerRef.current?.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  return (
    <div ref={containerRef} className="relative">
      <button type="button" onClick={handleOpen}
        className="w-full flex items-center justify-between rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm text-left focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500">
        <span className={selectedLabel ? 'text-slate-900' : 'text-slate-400'}>{selectedLabel || '— none —'}</span>
        <svg className="w-4 h-4 text-slate-400 shrink-0 ml-2" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" /></svg>
      </button>
      {open && (
        <div className="absolute z-50 mt-1 w-full bg-white border border-slate-200 rounded-lg shadow-lg">
          <div className="p-2 border-b border-slate-100">
            <input ref={inputRef} type="text" value={query} onChange={e => setQuery(e.target.value)}
              onKeyDown={handleKeyDown} placeholder="Search or type new name…"
              className="w-full rounded border border-slate-300 px-2 py-1 text-sm focus:outline-none focus:ring-1 focus:ring-blue-500" />
          </div>
          <ul className="max-h-52 overflow-y-auto py-1">
            <li className="px-3 py-1.5 text-sm text-slate-400 hover:bg-slate-50 cursor-pointer" onMouseDown={() => handleSelect('')}>— none —</li>
            {filtered.map(p => (
              <li key={String(p.id)} onMouseDown={() => handleSelect(String(p.id))}
                className={`px-3 py-1.5 text-sm cursor-pointer hover:bg-blue-50 hover:text-blue-700 ${String(p.id) === value ? 'bg-blue-50 text-blue-700 font-medium' : ''}`}>
                {String(p.name)}
              </li>
            ))}
            {noMatch && (
              <li onMouseDown={handleCreate}
                className="px-3 py-2 text-sm cursor-pointer text-blue-600 hover:bg-blue-50 flex items-center gap-1.5 border-t border-slate-100">
                <Plus size={13} /> {creating ? 'Adding…' : `Add "${query.trim()}"`}
              </li>
            )}
            {!noMatch && filtered.length === 0 && (
              <li className="px-3 py-3 text-sm text-slate-400 text-center">No results</li>
            )}
          </ul>
        </div>
      )}
    </div>
  )
}

/**
 * Category selector with inline "＋ Add" when no match is found — like PayeeSelect,
 * but categories are hierarchical (full_path uses " : " as a separator) and require
 * a Categories_Type, so creating one is a small two-step affair: pick/confirm a
 * type, then create. Typing "Vacation : Skiing" walks each " : "-separated segment,
 * reusing any that already exist as an exact full_path match and only creating the
 * missing tail — so nesting a new category under an existing parent needs no
 * separate "choose parent" step.
 */
function CategorySelect({ value, onChange, categories, onCategoryCreated, className }: {
  value: string
  onChange: (v: string) => void
  categories: Record<string, unknown>[]
  onCategoryCreated?: (c: { id: number; full_path: string; type: string }) => void
  className?: string
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [creating, setCreating] = useState(false)
  const [newType, setNewType] = useState('Expense')
  const containerRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const selectable = categories.filter(c => !c._disabled)
  const selectedLabel = selectable.find(c => String(c.id) === value)?.full_path as string ?? ''
  const filtered = query
    ? categories.filter(c => c._disabled || String(c.full_path).toLowerCase().includes(query.toLowerCase()))
    : categories
  const trimmedQuery = query.trim()
  const exactMatch = selectable.some(c => String(c.full_path).toLowerCase() === trimmedQuery.toLowerCase())
  const noMatch = trimmedQuery !== '' && !exactMatch

  const handleOpen = () => { setOpen(true); setQuery(''); setTimeout(() => inputRef.current?.focus(), 0) }
  const handleClose = () => { setOpen(false); setQuery('') }
  const handleSelect = useCallback((val: string) => { onChange(val); handleClose() }, [onChange])

  const handleCreate = async () => {
    const segments = trimmedQuery.split(':').map(s => s.trim()).filter(Boolean)
    if (!segments.length) return
    setCreating(true)
    try {
      let parentId: number | null = null
      let pathSoFar = ''
      let leafId = ''
      let known = selectable
      for (const seg of segments) {
        pathSoFar = pathSoFar ? `${pathSoFar} : ${seg}` : seg
        const existing = known.find(c => String(c.full_path).toLowerCase() === pathSoFar.toLowerCase())
        if (existing) {
          parentId = Number(existing.id)
          leafId = String(existing.id)
          continue
        }
        const res = await api.post('/static-data/categories', { name: seg, parent_id: parentId, type: newType })
        const newCat = { id: res.data.id, full_path: pathSoFar, type: newType }
        onCategoryCreated?.(newCat)
        known = [...known, newCat]
        parentId = res.data.id
        leafId = String(res.data.id)
      }
      handleSelect(leafId)
    } finally {
      setCreating(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') handleClose()
    if (e.key === 'Enter') {
      if (noMatch) { handleCreate(); return }
      const first = filtered.find(c => !c._disabled)
      if (first) handleSelect(String(first.id))
    }
  }

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => { if (!containerRef.current?.contains(e.target as Node)) handleClose() }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  return (
    <div ref={containerRef} className={cn('relative', className)}>
      <button type="button" onClick={handleOpen}
        className="w-full flex items-center justify-between rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm text-left focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500">
        <span className={selectedLabel ? 'text-slate-900' : 'text-slate-400'}>{selectedLabel || '— none —'}</span>
        <svg className="w-4 h-4 text-slate-400 shrink-0 ml-2" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" /></svg>
      </button>
      {open && (
        <div className="absolute z-50 mt-1 w-full bg-white border border-slate-200 rounded-lg shadow-lg">
          <div className="p-2 border-b border-slate-100">
            <input ref={inputRef} type="text" value={query} onChange={e => setQuery(e.target.value)}
              onKeyDown={handleKeyDown} placeholder="Search, or type new (e.g. Vacation : Skiing)…"
              className="w-full rounded border border-slate-300 px-2 py-1 text-sm focus:outline-none focus:ring-1 focus:ring-blue-500" />
          </div>
          <ul className="max-h-52 overflow-y-auto py-1">
            <li className="px-3 py-1.5 text-sm text-slate-400 hover:bg-slate-50 cursor-pointer" onMouseDown={() => handleSelect('')}>— none —</li>
            {filtered.map((c, i) => c._disabled
              ? <li key={i} className="px-3 py-1 text-xs text-slate-400 italic select-none">{String(c.full_path)}</li>
              : <li key={String(c.id)} onMouseDown={() => handleSelect(String(c.id))}
                  className={`px-3 py-1.5 text-sm cursor-pointer hover:bg-blue-50 hover:text-blue-700 ${String(c.id) === value ? 'bg-blue-50 text-blue-700 font-medium' : ''}`}>
                  {String(c.full_path)}
                </li>
            )}
            {!noMatch && filtered.filter(c => !c._disabled).length === 0 && (
              <li className="px-3 py-3 text-sm text-slate-400 text-center">No results</li>
            )}
            {noMatch && (
              <li className="border-t border-slate-100 p-2 flex items-center gap-1.5" onMouseDown={e => e.preventDefault()}>
                <select value={newType} onChange={e => setNewType(e.target.value)}
                  className="rounded border border-slate-300 px-1.5 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-blue-500">
                  {CATEGORY_TYPES.map(t => <option key={t}>{t}</option>)}
                </select>
                <button onClick={handleCreate} disabled={creating}
                  className="flex-1 flex items-center gap-1 text-sm text-blue-600 hover:text-blue-700 disabled:opacity-50">
                  <Plus size={13} /> {creating ? 'Adding…' : `Add "${trimmedQuery}"`}
                </button>
              </li>
            )}
          </ul>
        </div>
      )}
    </div>
  )
}

export function TxModal({
  form, splits, useSplits, setUseSplits,
  onFormChange, onSplitsChange,
  payees, categories, accounts,
  onSave, onDelete, onClose, onPayeeCreated, onCategoryCreated, saving, error,
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
                <PayeeSelect value={form.payees_id} onChange={v => set('payees_id', v)}
                  payees={payees} onPayeeCreated={onPayeeCreated} />
              </div>
            </div>
          ) : (
            <>
              <div>
                <label className="text-xs font-medium text-slate-500 block mb-1">Payee</label>
                <PayeeSelect value={form.payees_id} onChange={v => set('payees_id', v)}
                  payees={payees} onPayeeCreated={onPayeeCreated} />
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
                    <CategorySelect value={form.categories_id} onChange={v => set('categories_id', v)}
                      categories={sortedCategories} onCategoryCreated={onCategoryCreated} />
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
                      <CategorySelect className="col-span-5" value={sp.categories_id} onChange={v => setSplit(i, 'categories_id', v)}
                        categories={sortedCategories} onCategoryCreated={onCategoryCreated} />
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

// ── Shared create/edit/save/delete logic for a cash Transaction ────────────────
// Single source of truth for the full TxModal workflow (transfers, split categories,
// installment series, "save as recurring template") — used by both the Cash Register
// and the Investments page's Cash tab so the two never drift apart on which fields or
// transaction types are supported (previously Investments.tsx had its own ~250-line
// copy of this modal + save logic that had already drifted, e.g. missing the inline
// "add new payee" feature and the tax_amount field on the investment-transaction side).
export function useTxModal({ onSaved, onDeleted }: { onSaved: () => void; onDeleted?: () => void }) {
  const [modalOpen, setModalOpen] = useState(false)
  const [form, setForm] = useState<TxForm | null>(null)
  const [splits, setSplits] = useState<SplitRow[]>([])
  const [useSplits, setUseSplits] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  const [recurringEnabled, setRecurringEnabled] = useState(false)
  const [recurringName, setRecurringName] = useState('')
  const [recurringFreq, setRecurringFreq] = useState('Monthly')
  const [recurringNextDue, setRecurringNextDue] = useState(today())

  const [installmentEnabled, setInstallmentEnabled] = useState(false)
  const [installmentCount, setInstallmentCount] = useState('')
  const [installmentFreq, setInstallmentFreq] = useState('Monthly')

  const resetExtras = () => {
    setRecurringEnabled(false); setRecurringName(''); setRecurringFreq('Monthly'); setRecurringNextDue(today())
    setInstallmentEnabled(false); setInstallmentCount(''); setInstallmentFreq('Monthly')
  }

  const openNew = useCallback((accountId: number) => {
    setForm(emptyForm(accountId))
    setSplits([{ categories_id: '', amount: '', memo: '' }])
    setUseSplits(false)
    setSaveError(null)
    resetExtras()
    setModalOpen(true)
  }, [])

  const openEdit = useCallback(async (row: Record<string, unknown>, accountId: number) => {
    const txSplits = await getSplits(Number(row.id))
    setForm({
      id: Number(row.id),
      accounts_id: accountId,
      date: String(row.date ?? '').slice(0, 10),
      description: String(row.description ?? ''),
      total_amount: String(row.amount ?? row.total_amount ?? ''),
      payees_id: String(row.payees_id ?? ''),
      categories_id: String((txSplits as Record<string, unknown>[])?.[0]?.categories_id ?? ''),
      memo: String((txSplits as Record<string, unknown>[])?.[0]?.memo ?? ''),
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
  }, [])

  const close = useCallback(() => setModalOpen(false), [])

  const handleSave = useCallback(async () => {
    if (!form) return

    if (useSplits && !form.is_transfer) {
      const validSplits = splits.filter(s => s.amount !== '' && s.amount !== '0')
      const splitsTotal = validSplits.reduce((sum, s) => sum + parseFloat(s.amount), 0)
      const txTotal = parseFloat(form.total_amount)
      if (Math.round(splitsTotal * 100) !== Math.round(txTotal * 100)) {
        setSaveError(`Split amounts (${fmtEur(splitsTotal)}) must equal total amount (${fmtEur(txTotal)})`)
        return
      }
    }

    setSaving(true); setSaveError(null)
    try {
      const statusFields = { is_draft: form.is_draft, cleared: form.cleared, reconciled: form.reconciled }

      if (form.is_transfer && !form.id) {
        if (!form.transfer_account_id) throw new Error('Select a target account for the transfer')
        await createTransfer({
          from_account_id: form.accounts_id,
          to_account_id: Number(form.transfer_account_id),
          date: form.date,
          amount: parseFloat(form.total_amount),
          description: form.description || null,
          payees_id: form.payees_id ? Number(form.payees_id) : null,
          ...statusFields,
        })
      } else if (form.is_transfer && form.id) {
        await updateTransaction(form.id, {
          date: form.date,
          description: form.description || null,
          total_amount: parseFloat(form.total_amount),
          payees_id: form.payees_id ? Number(form.payees_id) : null,
          accounts_id_target: form.transfer_account_id ? Number(form.transfer_account_id) : null,
          ...statusFields,
        })
      } else if (!form.id && installmentEnabled && parseInt(installmentCount) >= 2) {
        const n = parseInt(installmentCount)
        const baseDesc = form.description || ''
        const splitPayload = useSplits
          ? splits.filter(s => s.amount !== '' && s.amount !== '0').map(s => ({ categories_id: s.categories_id ? Number(s.categories_id) : null, amount: parseFloat(s.amount), memo: s.memo || null }))
          : [{ categories_id: form.categories_id ? Number(form.categories_id) : null, amount: parseFloat(form.total_amount), memo: form.memo || null }]
        if (useSplits) {
          const splitsTotal = splitPayload.reduce((sum, s) => sum + s.amount, 0)
          const txTotal = parseFloat(form.total_amount)
          if (Math.round(splitsTotal * 100) !== Math.round(txTotal * 100)) throw new Error(`Split amounts (${fmtEur(splitsTotal)}) must equal total amount (${fmtEur(txTotal)})`)
        }
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

      onSaved()
      setModalOpen(false)
    } catch (e: unknown) {
      setSaveError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }, [form, splits, useSplits, installmentEnabled, installmentCount, installmentFreq, recurringEnabled, recurringName, recurringFreq, recurringNextDue, onSaved])

  const handleDelete = useCallback(async () => {
    if (!form?.id || !confirm('Delete this transaction?')) return
    setSaving(true)
    try {
      await deleteTransaction(form.id)
      ;(onDeleted ?? onSaved)()
      setModalOpen(false)
    } catch (e: unknown) {
      setSaveError(e instanceof Error ? e.message : 'Delete failed')
    } finally {
      setSaving(false)
    }
  }, [form, onDeleted, onSaved])

  return {
    modalOpen, form, setForm, splits, setSplits, useSplits, setUseSplits, saving, saveError,
    recurringEnabled, setRecurringEnabled, recurringName, setRecurringName,
    recurringFreq, setRecurringFreq, recurringNextDue, setRecurringNextDue,
    installmentEnabled, setInstallmentEnabled, installmentCount, setInstallmentCount, installmentFreq, setInstallmentFreq,
    openNew, openEdit, close, handleSave, handleDelete,
  }
}
