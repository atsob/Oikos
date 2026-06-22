import { useState, useEffect, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  getRecurringTemplates, getTemplateSplits, createRecurringTemplate,
  updateRecurringTemplate, deleteRecurringTemplate, runRecurringTemplate,
  getRecurringDrafts, generateRecurringDrafts, updateRecurringDraft,
  confirmRecurringDraft, deleteRecurringDraft,
  getAccounts, getPayees, getCategories, getSplits,
  getRecentTransactionsForTemplate, createTemplateFromTransaction,
} from '@/lib/api'
import { PageHeader, Card, CardBody, Button, Badge, Input, Spinner, ColHeader, useSortTable } from '@/components/ui'
import { fmtEur, fmtDate } from '@/lib/utils'
import { Play, Plus, Pencil, Trash2, X, Save, RefreshCw, Check, List, Calendar, Copy } from 'lucide-react'

type Row = Record<string, unknown>

const PERIODICITIES = ['Weekly', 'Bi-Weekly', 'Monthly', 'Bi-Monthly', 'Quarterly', 'Semi-Annual', 'Annual', 'Once']

// ── Template form ─────────────────────────────────────────────────────────────
interface TplForm {
  name: string
  accounts_id: string
  payees_id: string
  description: string
  total_amount: string
  periodicity: string
  next_due_date: string
  end_date: string
  auto_confirm: boolean
  active: boolean
  accounts_id_target: string
}

interface SplitRow { categories_id: string; amount: string; memo: string }

const emptyForm = (): TplForm => ({
  name: '',
  accounts_id: '',
  payees_id: '',
  description: '',
  total_amount: '',
  periodicity: 'Monthly',
  next_due_date: '',
  end_date: '',
  auto_confirm: false,
  active: true,
  accounts_id_target: '',
})

// ── Modal ─────────────────────────────────────────────────────────────────────
interface ModalProps {
  form: TplForm
  onChange: (f: TplForm) => void
  splits: SplitRow[]
  onSplitsChange: (s: SplitRow[]) => void
  accounts: Row[]
  payees: Row[]
  categories: Row[]
  onSave: () => void
  onClose: () => void
  saving: boolean
  error: string | null
  isEdit: boolean
}

function TemplateModal({ form, onChange, splits, onSplitsChange, accounts, payees, categories, onSave, onClose, saving, error, isEdit }: ModalProps) {
  const set = (k: keyof TplForm, v: unknown) => onChange({ ...form, [k]: v as string & boolean })

  const addSplit = () => onSplitsChange([...splits, { categories_id: '', amount: '', memo: '' }])
  const removeSplit = (i: number) => onSplitsChange(splits.filter((_, j) => j !== i))
  const setSplit = (i: number, k: keyof SplitRow, v: string) =>
    onSplitsChange(splits.map((s, j) => j === i ? { ...s, [k]: v } : s))

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-xl max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200">
          <h2 className="text-base font-semibold">{isEdit ? 'Edit Template' : 'New Template'}</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600"><X size={18} /></button>
        </div>

        <div className="px-5 py-4 space-y-3">
          {/* Name */}
          <div>
            <label className="text-xs font-medium text-slate-500 block mb-1">Name *</label>
            <Input value={form.name} onChange={e => set('name', e.target.value)} placeholder="Template name" />
          </div>

          {/* Account + Payee */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs font-medium text-slate-500 block mb-1">Account *</label>
              <select className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm" value={form.accounts_id} onChange={e => set('accounts_id', e.target.value)}>
                <option value="">— select —</option>
                {accounts.map(a => <option key={String(a.id)} value={String(a.id)}>{String(a.name)}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs font-medium text-slate-500 block mb-1">Payee</label>
              <select className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm" value={form.payees_id} onChange={e => set('payees_id', e.target.value)}>
                <option value="">— none —</option>
                {payees.map(p => <option key={String(p.id)} value={String(p.id)}>{String(p.name)}</option>)}
              </select>
            </div>
          </div>

          {/* Amount + Periodicity */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs font-medium text-slate-500 block mb-1">Amount</label>
              <Input type="number" step="0.01" value={form.total_amount} onChange={e => set('total_amount', e.target.value)} placeholder="0.00" />
            </div>
            <div>
              <label className="text-xs font-medium text-slate-500 block mb-1">Periodicity</label>
              <select className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm" value={form.periodicity} onChange={e => set('periodicity', e.target.value)}>
                {PERIODICITIES.map(p => <option key={p}>{p}</option>)}
              </select>
            </div>
          </div>

          {/* Dates */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs font-medium text-slate-500 block mb-1">Next Due Date</label>
              <Input type="date" value={form.next_due_date} onChange={e => set('next_due_date', e.target.value)} />
            </div>
            <div>
              <label className="text-xs font-medium text-slate-500 block mb-1">End Date (optional)</label>
              <Input type="date" value={form.end_date} onChange={e => set('end_date', e.target.value)} />
            </div>
          </div>

          {/* Description */}
          <div>
            <label className="text-xs font-medium text-slate-500 block mb-1">Description</label>
            <Input value={form.description} onChange={e => set('description', e.target.value)} placeholder="Description / memo" />
          </div>

          {/* Transfer account */}
          <div>
            <label className="text-xs font-medium text-slate-500 block mb-1">Transfer to Account (optional)</label>
            <select className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm" value={form.accounts_id_target} onChange={e => set('accounts_id_target', e.target.value)}>
              <option value="">— none —</option>
              {accounts.map(a => <option key={String(a.id)} value={String(a.id)}>{String(a.name)}</option>)}
            </select>
          </div>

          {/* Flags */}
          <div className="flex items-center gap-6">
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={form.active} onChange={e => set('active', e.target.checked)} className="rounded" />
              Active
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={form.auto_confirm} onChange={e => set('auto_confirm', e.target.checked)} className="rounded" />
              Auto-confirm drafts
            </label>
          </div>

          {/* Splits */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-medium text-slate-500">Category Splits</span>
              <Button size="sm" variant="secondary" onClick={addSplit}><Plus size={12} /> Add</Button>
            </div>
            {splits.length > 0 && (
              <div className="space-y-2 border border-slate-200 rounded-lg p-3">
                <div className="text-xs font-medium text-slate-400 grid grid-cols-12 gap-2">
                  <span className="col-span-5">Category</span>
                  <span className="col-span-3">Amount</span>
                  <span className="col-span-3">Memo</span>
                </div>
                {splits.map((sp, i) => (
                  <div key={i} className="grid grid-cols-12 gap-2 items-center">
                    <select className="col-span-5 rounded-md border border-slate-300 px-2 py-1 text-xs" value={sp.categories_id} onChange={e => setSplit(i, 'categories_id', e.target.value)}>
                      <option value="">— none —</option>
                      {categories.map(c => <option key={String(c.id)} value={String(c.id)}>{String(c.full_path)}</option>)}
                    </select>
                    <Input className="col-span-3 text-xs py-1" type="number" step="0.01" value={sp.amount} onChange={e => setSplit(i, 'amount', e.target.value)} placeholder="0.00" />
                    <Input className="col-span-3 text-xs py-1" value={sp.memo} onChange={e => setSplit(i, 'memo', e.target.value)} placeholder="Memo" />
                    <button onClick={() => removeSplit(i)} className="col-span-1 text-slate-400 hover:text-red-500"><X size={14} /></button>
                  </div>
                ))}
              </div>
            )}
          </div>

          {error && <p className="text-xs text-red-600 bg-red-50 rounded px-3 py-2">{error}</p>}
        </div>

        <div className="flex justify-end gap-2 px-5 py-3 border-t border-slate-200">
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button onClick={onSave} disabled={saving}><Save size={14} /> {saving ? 'Saving…' : 'Save'}</Button>
        </div>
      </div>
    </div>
  )
}

// ── Draft review modal ────────────────────────────────────────────────────────
interface DraftSplit { categories_id: string; amount: string; memo: string }
interface DraftForm {
  date: string; description: string; amount: string
  accounts_id: string; payees_id: string; accounts_id_target: string
}

function DraftReviewModal({ draft, accounts, payees, categories, onClose, onSaved, onConfirmed, onDeleted }: {
  draft: Row
  accounts: Row[]; payees: Row[]; categories: Row[]
  onClose: () => void
  onSaved: () => void
  onConfirmed: () => void
  onDeleted: () => void
}) {
  const [form, setForm] = useState<DraftForm>({
    date: String(draft.date ?? '').slice(0, 10),
    description: String(draft.description ?? ''),
    amount: draft.amount != null ? String(draft.amount) : '',
    accounts_id: draft.accounts_id != null ? String(draft.accounts_id) : '',
    payees_id: draft.payees_id != null ? String(draft.payees_id) : '',
    accounts_id_target: draft.accounts_id_target != null ? String(draft.accounts_id_target) : '',
  })
  const [splits, setSplits] = useState<DraftSplit[]>([])
  const [splitsLoaded, setSplitsLoaded] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const set = (k: keyof DraftForm, v: string) => setForm(f => ({ ...f, [k]: v }))

  useEffect(() => {
    getSplits(Number(draft.id)).then((rows: Row[]) => {
      setSplits(rows.map(r => ({
        categories_id: r.categories_id != null ? String(r.categories_id) : '',
        amount: r.amount != null ? String(r.amount) : '',
        memo: String(r.memo ?? ''),
      })))
      setSplitsLoaded(true)
    })
  }, [draft.id])

  const addSplit = () => setSplits(s => [...s, { categories_id: '', amount: '', memo: '' }])
  const removeSplit = (i: number) => setSplits(s => s.filter((_, j) => j !== i))
  const setSplit = (i: number, k: keyof DraftSplit, v: string) =>
    setSplits(s => s.map((sp, j) => j === i ? { ...sp, [k]: v } : sp))

  const buildPayload = () => ({
    date: form.date || null,
    description: form.description || null,
    amount: form.amount ? parseFloat(form.amount) : null,
    accounts_id: form.accounts_id ? Number(form.accounts_id) : null,
    payees_id: form.payees_id ? Number(form.payees_id) : null,
    accounts_id_target: form.accounts_id_target ? Number(form.accounts_id_target) : null,
    splits: splits.filter(s => s.amount).map(s => ({
      categories_id: s.categories_id ? Number(s.categories_id) : null,
      amount: parseFloat(s.amount),
      memo: s.memo || null,
    })),
  })

  const handleSave = async () => {
    setSaving(true); setError(null)
    try {
      await updateRecurringDraft(Number(draft.id), buildPayload())
      onSaved()
    } catch (e) { setError(e instanceof Error ? e.message : 'Save failed') }
    finally { setSaving(false) }
  }

  const handleSaveConfirm = async () => {
    setSaving(true); setError(null)
    try {
      await updateRecurringDraft(Number(draft.id), buildPayload())
      await confirmRecurringDraft(Number(draft.id))
      onConfirmed()
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed') }
    finally { setSaving(false) }
  }

  const handleDiscard = async () => {
    setSaving(true)
    try {
      await deleteRecurringDraft(Number(draft.id))
      onDeleted()
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed') }
    finally { setSaving(false) }
  }

  const amtNum = parseFloat(form.amount)

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl max-h-[92vh] overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-200 bg-slate-50 rounded-t-xl">
          <div className="flex items-center gap-2 flex-wrap text-sm font-medium text-slate-700 min-w-0">
            <Calendar size={14} className="text-slate-400 shrink-0" />
            <span>{form.date}</span>
            {form.accounts_id && <span className="text-slate-400">|</span>}
            <span className="truncate">{accounts.find(a => String(a.id) === form.accounts_id)?.name as string ?? ''}</span>
            {form.accounts_id_target
              ? <><span className="text-slate-400">|</span><span className="text-blue-600">⇒ {accounts.find(a => String(a.id) === form.accounts_id_target)?.name as string ?? ''}</span></>
              : form.payees_id && <><span className="text-slate-400">|</span><span>{payees.find(p => String(p.id) === form.payees_id)?.name as string ?? ''}</span></>
            }
            {form.amount && <><span className="text-slate-400">|</span>
              <span className={`font-semibold ${amtNum < 0 ? 'text-red-600' : 'text-green-600'}`}>{fmtEur(amtNum)}</span></>}
            {draft.template_name && <span className="text-slate-400 italic text-xs font-normal">(from: {String(draft.template_name)}{draft.template_periodicity ? ` · ${draft.template_periodicity}` : ''})</span>}
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600 ml-3 shrink-0"><X size={18} /></button>
        </div>

        <div className="px-5 py-4 space-y-4">
          {/* Date + Amount */}
          <div className="grid grid-cols-3 gap-4">
            <div>
              <label className="text-xs font-medium text-slate-500 block mb-1">Date</label>
              <Input type="date" value={form.date} onChange={e => set('date', e.target.value)} />
            </div>
            <div>
              <label className="text-xs font-medium text-slate-500 block mb-1">Amount</label>
              <Input type="number" step="0.01" value={form.amount} onChange={e => set('amount', e.target.value)} />
            </div>
            <div>
              <label className="text-xs font-medium text-slate-500 block mb-1">Description</label>
              <Input value={form.description} onChange={e => set('description', e.target.value)} placeholder="Description / memo" />
            </div>
          </div>

          {/* Account + Payee / Transfer To */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-xs font-medium text-slate-500 block mb-1">Account</label>
              <select className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm" value={form.accounts_id} onChange={e => set('accounts_id', e.target.value)}>
                <option value="">— select —</option>
                {accounts.map(a => <option key={String(a.id)} value={String(a.id)}>{String(a.name)}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs font-medium text-slate-500 block mb-1">
                Transfer To Account
                {!form.accounts_id_target && <span className="text-slate-400 font-normal"> — or use Payee below</span>}
              </label>
              <select className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm" value={form.accounts_id_target} onChange={e => { set('accounts_id_target', e.target.value); if (e.target.value) set('payees_id', '') }}>
                <option value="">— none (not a transfer) —</option>
                {accounts.map(a => <option key={String(a.id)} value={String(a.id)}>{String(a.name)}</option>)}
              </select>
            </div>
          </div>
          {!form.accounts_id_target && (
            <div className="grid grid-cols-2 gap-4">
              <div />
              <div>
                <label className="text-xs font-medium text-slate-500 block mb-1">Payee</label>
                <select className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm" value={form.payees_id} onChange={e => set('payees_id', e.target.value)}>
                  <option value="">— none —</option>
                  {payees.map(p => <option key={String(p.id)} value={String(p.id)}>{String(p.name)}</option>)}
                </select>
              </div>
            </div>
          )}

          {/* Splits */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-semibold text-slate-700">Splits</span>
              <Button size="sm" variant="secondary" onClick={addSplit}><Plus size={12} /> Add Row</Button>
            </div>
            {!splitsLoaded ? (
              <div className="flex justify-center py-3"><Spinner /></div>
            ) : (
              <div className="border border-slate-200 rounded-lg overflow-hidden">
                <table className="w-full text-sm">
                  <thead className="bg-slate-50">
                    <tr>
                      <th className="text-left px-3 py-2 text-xs font-medium text-slate-500 w-1/2">Category</th>
                      <th className="text-right px-3 py-2 text-xs font-medium text-slate-500 w-1/4">Amount</th>
                      <th className="text-left px-3 py-2 text-xs font-medium text-slate-500 w-1/4">Memo</th>
                      <th className="w-8" />
                    </tr>
                  </thead>
                  <tbody>
                    {splits.length === 0 ? (
                      <tr><td colSpan={4} className="text-center text-xs text-slate-400 py-4">No splits — add a row to categorise</td></tr>
                    ) : splits.map((sp, i) => (
                      <tr key={i} className="border-t border-slate-100">
                        <td className="px-2 py-1.5">
                          <select className="w-full rounded border border-slate-300 px-2 py-1 text-xs" value={sp.categories_id} onChange={e => setSplit(i, 'categories_id', e.target.value)}>
                            <option value="">— none —</option>
                            {(categories as Row[]).map(c => <option key={String(c.id)} value={String(c.id)}>{String(c.full_path)}</option>)}
                          </select>
                        </td>
                        <td className="px-2 py-1.5">
                          <Input className="text-right text-xs py-1" type="number" step="0.01" value={sp.amount} onChange={e => setSplit(i, 'amount', e.target.value)} />
                        </td>
                        <td className="px-2 py-1.5">
                          <Input className="text-xs py-1" value={sp.memo} onChange={e => setSplit(i, 'memo', e.target.value)} placeholder="Memo" />
                        </td>
                        <td className="px-1 py-1.5 text-center">
                          <button onClick={() => removeSplit(i)} className="text-slate-300 hover:text-red-500"><X size={14} /></button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {error && <p className="text-xs text-red-600 bg-red-50 rounded px-3 py-2">{error}</p>}
        </div>

        {/* Footer actions */}
        <div className="flex items-center justify-between px-5 py-3 border-t border-slate-200 gap-2">
          <Button variant="destructive" onClick={handleDiscard} disabled={saving}>
            <Trash2 size={14} /> Discard draft
          </Button>
          <div className="flex gap-2">
            <Button variant="secondary" onClick={handleSave} disabled={saving}>
              <Save size={14} /> {saving ? 'Saving…' : 'Save'}
            </Button>
            <Button onClick={handleSaveConfirm} disabled={saving}>
              <Check size={14} /> Save & Confirm
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Drafts tab ────────────────────────────────────────────────────────────────
function DraftsTab() {
  const qc = useQueryClient()
  const [reviewDraft, setReviewDraft] = useState<Row | null>(null)
  const { data: drafts = [], isLoading } = useQuery({ queryKey: ['recurring-drafts'], queryFn: getRecurringDrafts })
  const { data: accounts = [] } = useQuery({ queryKey: ['accounts'], queryFn: getAccounts })
  const { data: payees = [] } = useQuery({ queryKey: ['payees'], queryFn: () => getPayees() })
  const { data: categories = [] } = useQuery({ queryKey: ['categories'], queryFn: () => getCategories() })

  const invalidate = () => qc.invalidateQueries({ queryKey: ['recurring-drafts'] })

  const genMut = useMutation({
    mutationFn: generateRecurringDrafts,
    onSuccess: (res: Record<string, unknown>) => {
      invalidate()
      qc.invalidateQueries({ queryKey: ['recurring-templates'] })
      alert(`${res.generated} draft(s) generated.`)
    },
  })

  const confirmMut = useMutation({ mutationFn: confirmRecurringDraft, onSuccess: invalidate })
  const confirmAllMut = useMutation({
    mutationFn: async () => { for (const d of drafts as Row[]) await confirmRecurringDraft(Number(d.id)) },
    onSuccess: invalidate,
  })
  const deleteMut = useMutation({ mutationFn: deleteRecurringDraft, onSuccess: invalidate })

  return (
    <div className="space-y-4">
      <div className="flex gap-2">
        <Button size="sm" onClick={() => genMut.mutate()} disabled={genMut.isPending}>
          <RefreshCw size={13} /> Generate Due Drafts
        </Button>
        {(drafts as Row[]).length > 0 && (
          <Button size="sm" variant="secondary" onClick={() => confirmAllMut.mutate()} disabled={confirmAllMut.isPending}>
            <Check size={13} /> Confirm All ({(drafts as Row[]).length})
          </Button>
        )}
      </div>

      {isLoading ? (
        <div className="flex justify-center py-8"><Spinner /></div>
      ) : (drafts as Row[]).length === 0 ? (
        <div className="text-center text-slate-400 py-12">
          <List size={36} className="mx-auto mb-3 opacity-30" />
          <p className="text-sm">No pending drafts. Click Generate Due Drafts to create from templates.</p>
        </div>
      ) : (
        <div className="space-y-2 max-w-2xl">
          {(drafts as Row[]).map(d => (
            <Card key={String(d.id)}>
              <CardBody className="flex items-center justify-between py-3">
                <div className="space-y-0.5 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap text-sm">
                    <span className="font-medium text-slate-800">{String(d.description || d.payee || '—')}</span>
                    {d.template_name && <Badge label={String(d.template_name)} variant="blue" />}
                  </div>
                  <div className="flex gap-4 text-xs text-slate-500">
                    <span>{fmtDate(String(d.date))}</span>
                    {d.account && <span>{String(d.account)}</span>}
                    {d.payee && <span>{String(d.payee)}</span>}
                    <span className={`font-semibold ${Number(d.amount) < 0 ? 'text-red-600' : 'text-green-600'}`}>
                      {fmtEur(Number(d.amount))}
                    </span>
                  </div>
                </div>
                <div className="flex gap-1 ml-3 shrink-0">
                  <Button size="sm" variant="secondary" onClick={() => setReviewDraft(d)}><Pencil size={13} /> Review</Button>
                  <Button size="sm" onClick={() => confirmMut.mutate(Number(d.id))} disabled={confirmMut.isPending}><Check size={13} /> Confirm</Button>
                  <Button size="sm" variant="destructive" onClick={() => deleteMut.mutate(Number(d.id))}><Trash2 size={13} /></Button>
                </div>
              </CardBody>
            </Card>
          ))}
        </div>
      )}

      {reviewDraft && (
        <DraftReviewModal
          draft={reviewDraft}
          accounts={accounts as Row[]}
          payees={payees as Row[]}
          categories={categories as Row[]}
          onClose={() => setReviewDraft(null)}
          onSaved={() => { invalidate(); setReviewDraft(null) }}
          onConfirmed={() => { invalidate(); setReviewDraft(null) }}
          onDeleted={() => { invalidate(); setReviewDraft(null) }}
        />
      )}
    </div>
  )
}

// ── From-Transaction picker modal ─────────────────────────────────────────────
function FromTransactionModal({ onCreated, onClose }: { onCreated: (id: number) => void; onClose: () => void }) {
  const [search, setSearch] = useState('')
  const [months, setMonths] = useState(24)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const { data = [], isLoading } = useQuery({
    queryKey: ['recent-transactions-for-template', months],
    queryFn: () => getRecentTransactionsForTemplate(months),
    staleTime: 60_000,
  })
  const rows = data as Row[]

  const { sorted: sortedRows, sortKey: ftSK, sortDir: ftSD, toggleSort: ftSort } = useSortTable(rows, 'date', 'desc')

  const filtered = useMemo(() => {
    const q = search.toLowerCase()
    return sortedRows.filter(r =>
      !q ||
      String(r.description ?? '').toLowerCase().includes(q) ||
      String(r.payees_name ?? '').toLowerCase().includes(q) ||
      String(r.accounts_name ?? '').toLowerCase().includes(q)
    )
  }, [rows, search])

  const handleCreate = async () => {
    if (!selectedId) return
    setCreating(true); setError(null)
    try {
      const res = await createTemplateFromTransaction(selectedId) as { id: number }
      onCreated(res.id)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to create template')
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl mx-4 flex flex-col max-h-[80vh]">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200">
          <h2 className="text-base font-semibold text-slate-800">Create Template from Transaction</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600"><X size={18} /></button>
        </div>

        <div className="px-5 py-3 border-b border-slate-100 flex gap-2 items-center">
          <Input
            placeholder="Search by description, payee, account…"
            value={search} onChange={e => setSearch(e.target.value)}
            className="flex-1 text-sm"
          />
          <select value={months} onChange={e => setMonths(Number(e.target.value))}
            className="text-xs border border-slate-300 rounded px-2 py-1.5 bg-white focus:outline-none shrink-0">
            {[3, 6, 12, 24, 36, 60].map(m => <option key={m} value={m}>Last {m}mo</option>)}
          </select>
        </div>

        <div className="flex-1 overflow-y-auto">
          {isLoading ? (
            <div className="flex justify-center py-8"><Spinner /></div>
          ) : filtered.length === 0 ? (
            <p className="text-center text-slate-400 py-8 text-sm">No transactions found</p>
          ) : (
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-slate-50">
                <tr className="text-slate-600">
                  <ColHeader label="Date" sortKey="date" currentKey={ftSK} currentDir={ftSD} onSort={ftSort} className="px-3 py-2 border-b border-slate-200" />
                  <ColHeader label="Account" sortKey="accounts_name" currentKey={ftSK} currentDir={ftSD} onSort={ftSort} className="px-3 py-2 border-b border-slate-200" />
                  <ColHeader label="Payee" sortKey="payees_name" currentKey={ftSK} currentDir={ftSD} onSort={ftSort} className="px-3 py-2 border-b border-slate-200" />
                  <ColHeader label="Description" sortKey="description" currentKey={ftSK} currentDir={ftSD} onSort={ftSort} className="px-3 py-2 border-b border-slate-200" />
                  <ColHeader label="Amount" sortKey="total_amount" currentKey={ftSK} currentDir={ftSD} onSort={ftSort} align="right" className="px-3 py-2 border-b border-slate-200" />
                </tr>
              </thead>
              <tbody>
                {filtered.map(r => {
                  const id = Number(r.id)
                  const selected = selectedId === id
                  return (
                    <tr key={id} onClick={() => setSelectedId(id)}
                      className={`border-b border-slate-100 cursor-pointer ${selected ? 'bg-blue-50' : 'hover:bg-slate-50'}`}>
                      <td className="px-3 py-1.5 tabular-nums">{String(r.date ?? '').slice(0, 10)}</td>
                      <td className="px-3 py-1.5 text-slate-500">{String(r.accounts_name ?? '')}</td>
                      <td className="px-3 py-1.5">{String(r.payees_name ?? '')}</td>
                      <td className="px-3 py-1.5 max-w-[180px] truncate text-slate-600">{String(r.description ?? '')}</td>
                      <td className={`px-3 py-1.5 text-right tabular-nums font-medium ${Number(r.total_amount ?? 0) < 0 ? 'text-red-600' : 'text-green-700'}`}>
                        {fmtEur(Number(r.total_amount ?? 0))}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </div>

        {error && <p className="px-5 py-2 text-xs text-red-600 border-t border-red-100">{error}</p>}

        <div className="flex justify-end gap-2 px-5 py-4 border-t border-slate-200">
          <Button variant="secondary" size="sm" onClick={onClose}>Cancel</Button>
          <Button size="sm" onClick={handleCreate} disabled={!selectedId || creating}>
            {creating ? <Spinner /> : <Copy size={13} />}
            {creating ? 'Creating…' : 'Create Template'}
          </Button>
        </div>
      </div>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function Recurring() {
  const qc = useQueryClient()
  const [tab, setTab] = useState<'templates' | 'drafts'>('templates')
  const [modalOpen, setModalOpen] = useState(false)
  const [fromTxOpen, setFromTxOpen] = useState(false)
  const [editId, setEditId] = useState<number | null>(null)
  const [form, setForm] = useState<TplForm>(emptyForm())
  const [splits, setSplits] = useState<SplitRow[]>([])
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  const { data: templates = [], isLoading } = useQuery({ queryKey: ['recurring-templates'], queryFn: getRecurringTemplates })
  const { data: accounts = [] } = useQuery({ queryKey: ['accounts'], queryFn: getAccounts })
  const { data: payees = [] } = useQuery({ queryKey: ['payees'], queryFn: () => getPayees() })
  const { data: categories = [] } = useQuery({ queryKey: ['categories'], queryFn: () => getCategories() })
  const { data: drafts = [] } = useQuery({ queryKey: ['recurring-drafts'], queryFn: getRecurringDrafts })

  const runMut = useMutation({
    mutationFn: runRecurringTemplate,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['recurring-templates'] }),
  })

  const openNew = () => {
    setEditId(null)
    setForm(emptyForm())
    setSplits([])
    setSaveError(null)
    setModalOpen(true)
  }

  const openEdit = async (t: Row) => {
    const spls = await getTemplateSplits(Number(t.id)) as Row[]
    setEditId(Number(t.id))
    setForm({
      name: String(t.name ?? ''),
      accounts_id: t.account_id != null ? String(t.account_id) : '',
      payees_id: t.payee_id != null ? String(t.payee_id) : '',
      description: String(t.description ?? ''),
      total_amount: t.total_amount != null ? String(t.total_amount) : '',
      periodicity: String(t.frequency ?? 'Monthly'),
      next_due_date: String(t.next_date ?? '').slice(0, 10),
      end_date: String(t.end_date ?? '').slice(0, 10),
      auto_confirm: Boolean(t.auto_confirm),
      active: Boolean(t.is_active),
      accounts_id_target: t.accounts_id_target != null ? String(t.accounts_id_target) : '',
    })
    setSplits(spls.map(s => ({ categories_id: String(s.categories_id ?? ''), amount: String(s.amount ?? ''), memo: String(s.memo ?? '') })))
    setSaveError(null)
    setModalOpen(true)
  }

  const handleDelete = async (id: number) => {
    if (!confirm('Delete this template?')) return
    await deleteRecurringTemplate(id)
    qc.invalidateQueries({ queryKey: ['recurring-templates'] })
  }

  const handleSave = async () => {
    setSaving(true)
    setSaveError(null)
    try {
      const payload: Record<string, unknown> = {
        name: form.name,
        accounts_id: form.accounts_id ? Number(form.accounts_id) : null,
        payees_id: form.payees_id ? Number(form.payees_id) : null,
        description: form.description || null,
        total_amount: form.total_amount ? parseFloat(form.total_amount) : null,
        periodicity: form.periodicity,
        next_due_date: form.next_due_date || null,
        end_date: form.end_date || null,
        auto_confirm: form.auto_confirm,
        active: form.active,
        accounts_id_target: form.accounts_id_target ? Number(form.accounts_id_target) : null,
        splits: splits.filter(s => s.amount).map(s => ({
          categories_id: s.categories_id ? Number(s.categories_id) : null,
          amount: parseFloat(s.amount),
          memo: s.memo || null,
        })),
      }

      if (editId) {
        await updateRecurringTemplate(editId, payload)
      } else {
        await createRecurringTemplate(payload)
      }
      qc.invalidateQueries({ queryKey: ['recurring-templates'] })
      setModalOpen(false)
    } catch (e: unknown) {
      setSaveError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div>
      <PageHeader
        title="Recurring Transactions"
        subtitle="Templates for scheduled recurring entries"
        actions={
          tab === 'templates' ? (
            <div className="flex gap-2">
              <Button size="sm" variant="secondary" onClick={() => qc.invalidateQueries({ queryKey: ['recurring-templates'] })}>
                <RefreshCw size={13} /> Refresh
              </Button>
              <Button size="sm" variant="secondary" onClick={() => setFromTxOpen(true)}>
                <Copy size={13} /> From Transaction
              </Button>
              <Button size="sm" onClick={openNew}><Plus size={13} /> New Template</Button>
            </div>
          ) : undefined
        }
      />

      <div className="px-6 pt-4 pb-6 space-y-4">
        {/* Tabs */}
        <div className="flex gap-1 border-b border-slate-200">
          {([
            { k: 'templates', label: 'Templates' },
            { k: 'drafts',    label: `Pending Drafts${(drafts as Row[]).length ? ` (${(drafts as Row[]).length})` : ''}` },
          ] as const).map(t => (
            <button key={t.k} onClick={() => setTab(t.k)}
              className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${tab === t.k ? 'border-blue-600 text-blue-700' : 'border-transparent text-slate-500 hover:text-slate-700'}`}>
              {t.label}
            </button>
          ))}
        </div>

        {tab === 'drafts' ? <DraftsTab /> : (
          isLoading ? (
            <div className="flex justify-center py-12"><Spinner /></div>
          ) : (templates as Row[]).length === 0 ? (
            <div className="text-center text-slate-400 py-16">
              <RefreshCw size={40} className="mx-auto mb-3 opacity-30" />
              <p className="text-sm">No recurring templates configured</p>
              <Button className="mt-4" size="sm" onClick={openNew}><Plus size={13} /> Create Template</Button>
            </div>
          ) : (
            <div className="grid gap-3 max-w-3xl">
              {(templates as Row[]).map(t => (
                <Card key={String(t.id)}>
                  <CardBody className="flex items-center justify-between">
                    <div className="space-y-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-medium text-slate-800">{String(t.name ?? '—')}</span>
                        {t.frequency && <Badge label={String(t.frequency)} variant="blue" />}
                        <Badge label={t.is_active ? 'Active' : 'Inactive'} variant={t.is_active ? 'green' : 'gray'} />
                        {t.auto_confirm && <Badge label="Auto-confirm" variant="blue" />}
                      </div>
                      <div className="flex items-center gap-4 text-xs text-slate-500 flex-wrap">
                        {t.account_name && <span>Account: {String(t.account_name)}</span>}
                        {t.payee_name && <span>Payee: {String(t.payee_name)}</span>}
                        {t.total_amount != null && (
                          <span className={`font-semibold ${Number(t.total_amount) < 0 ? 'text-red-600' : 'text-green-600'}`}>
                            {fmtEur(Number(t.total_amount))}
                          </span>
                        )}
                        {t.next_date && <span>Next due: {fmtDate(String(t.next_date))}</span>}
                      </div>
                    </div>
                    <div className="flex items-center gap-1 ml-3 shrink-0">
                      <Button size="sm" variant="secondary" onClick={() => openEdit(t)}>
                        <Pencil size={13} />
                      </Button>
                      <Button size="sm" variant="secondary" onClick={() => runMut.mutate(Number(t.id))} disabled={runMut.isPending}>
                        <Play size={13} /> Run
                      </Button>
                      <Button size="sm" variant="destructive" onClick={() => handleDelete(Number(t.id))}>
                        <Trash2 size={13} />
                      </Button>
                    </div>
                  </CardBody>
                </Card>
              ))}
            </div>
          )
        )}
      </div>

      {modalOpen && (
        <TemplateModal
          form={form}
          onChange={setForm}
          splits={splits}
          onSplitsChange={setSplits}
          accounts={accounts as Row[]}
          payees={payees as Row[]}
          categories={categories as Row[]}
          onSave={handleSave}
          onClose={() => setModalOpen(false)}
          saving={saving}
          error={saveError}
          isEdit={editId !== null}
        />
      )}

      {fromTxOpen && (
        <FromTransactionModal
          onClose={() => setFromTxOpen(false)}
          onCreated={async (newId) => {
            setFromTxOpen(false)
            await qc.invalidateQueries({ queryKey: ['recurring-templates'] })
            // Load the newly created template into the edit modal
            const all = await qc.fetchQuery({ queryKey: ['recurring-templates'], queryFn: getRecurringTemplates }) as Row[]
            const t = all.find(r => Number(r.id) === newId)
            if (t) openEdit(t)
          }}
        />
      )}
    </div>
  )
}
