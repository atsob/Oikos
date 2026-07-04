import React, { useState, useCallback } from 'react'
import { usePersist } from '@/lib/hooks'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { AgGridReact } from 'ag-grid-react'
import type { ColDef, GridReadyEvent, GridApi, CellValueChangedEvent, RowClickedEvent } from 'ag-grid-community'
import {
  api,
  getPayees, getCategories, getInstitutions, getAccountsMaster,
  upsertPayee, upsertCategory, upsertInstitution, mergePayees, mergeCategories,
  getSecuritiesMaster, upsertSecurity,
  getCurrenciesMaster, upsertCurrency,
  getPayeeTransactions, getCategoryTransactions,
  getTaxCategoryRules, createTaxCategoryRule, updateTaxCategoryRule,
  getInstrumentTypeOverrides, createInstrumentTypeOverride, updateInstrumentTypeOverride,
} from '@/lib/api'
import { PageHeader, Input, Button, Spinner, Card, ColHeader, useSortTable, useEscapeKey } from '@/components/ui'
import { fmtNum } from '@/lib/utils'
import { Search, Plus, Trash2, Save, X, Pencil, ArrowRightLeft } from 'lucide-react'

const TABS = ['Payees', 'Categories', 'Institutions', 'Accounts', 'Tax Rules', 'Instrument Tax']

const ACCOUNT_TYPES = ['Cash', 'Checking', 'Savings', 'Credit Card', 'Brokerage', 'Pension', 'Other Investment', 'Margin', 'Loan', 'Real Estate', 'Vehicle', 'Asset', 'Liability', 'Other']
const CATEGORY_TYPES = ['Income', 'Expense', 'Transfer', 'Trading', 'Investment', 'Dividend', 'Interest', 'Tax', 'Fee']
const INSTITUTION_TYPES = ['Bank', 'Credit Union', 'Insurance', 'Pension Fund', 'Broker', 'Crypto Exchange', 'Internal', 'Other']
const SECURITY_TYPES = ['Stock', 'ETF', 'Bond', 'Mutual Fund', 'Crypto', 'Option', 'Commodity', 'PF_Unit', 'CD', 'Emp. Stock Opt.', 'FX Spot', 'Market Index', 'CFD', 'Closed-End Fund', 'Other']

// ── helpers ───────────────────────────────────────────────────────────────────
const deleteRow = (table: string, id: number) =>
  api.delete(`/static-data/${table}/${id}`).then(r => r.data)

const extractError = (e: unknown) =>
  (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
  (e instanceof Error ? e.message : 'Operation failed')

// ── Shared Modal shell ────────────────────────────────────────────────────────
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
        <div className="flex items-center gap-2 px-5 py-3 border-t border-slate-200">{footer}</div>
      </div>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="text-xs font-medium text-slate-500 block mb-1">{label}</label>
      {children}
    </div>
  )
}

// ── Payees Tab ────────────────────────────────────────────────────────────────
function PayeesTab({ search }: { search: string }) {
  const qc = useQueryClient()
  const [editRow, setEditRow] = useState<Record<string, unknown> | null>(null)
  const [editName, setEditName] = useState('')
  const [editCatId, setEditCatId] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [mergeOpen, setMergeOpen] = useState(false)
  const [mergeSource, setMergeSource] = useState('')
  const [mergeTarget, setMergeTarget] = useState('')
  const [merging, setMerging] = useState(false)
  const [mergeError, setMergeError] = useState<string | null>(null)

  const { data: payees = [], isLoading } = useQuery({ queryKey: ['payees'], queryFn: () => getPayees() })
  const { data: categories = [] } = useQuery({ queryKey: ['categories'], queryFn: () => getCategories() })
  const { data: mergePreview = [] } = useQuery({
    queryKey: ['payee-tx-preview', mergeSource],
    queryFn: () => getPayeeTransactions(Number(mergeSource)),
    enabled: !!mergeSource,
  })

  const filtered = search
    ? (payees as Record<string, unknown>[]).filter(r =>
        String(r.name ?? '').toLowerCase().includes(search.toLowerCase()) ||
        String(r.default_category ?? '').toLowerCase().includes(search.toLowerCase()))
    : payees as Record<string, unknown>[]

  const openEdit = (row: Record<string, unknown>) => {
    setEditRow(row)
    setEditName(String(row.name ?? ''))
    setEditCatId(row.categories_id != null ? String(row.categories_id) : '')
    setError(null)
  }

  const handleSave = async () => {
    if (!editRow) return
    setSaving(true); setError(null)
    try {
      await upsertPayee({ id: editRow.id ?? undefined, name: editName, categories_id: editCatId ? Number(editCatId) : null })
      qc.invalidateQueries({ queryKey: ['payees'] })
      setEditRow(null)
    } catch (e: unknown) { setError(e instanceof Error ? e.message : 'Save failed') }
    finally { setSaving(false) }
  }

  const [deleteError, setDeleteError] = useState<string | null>(null)

  const handleDelete = async (id: number) => {
    if (!confirm('Delete this payee?')) return
    setDeleteError(null)
    try {
      await deleteRow('payees', id)
      qc.invalidateQueries({ queryKey: ['payees'] })
    } catch (e) { setDeleteError(extractError(e)) }
  }

  const handleMerge = async () => {
    if (!mergeSource || !mergeTarget || mergeSource === mergeTarget) return
    setMerging(true); setMergeError(null)
    try {
      await mergePayees(Number(mergeSource), Number(mergeTarget))
      qc.invalidateQueries({ queryKey: ['payees'] })
      setMergeOpen(false); setMergeSource(''); setMergeTarget('')
    } catch (e: unknown) { setMergeError(e instanceof Error ? e.message : 'Merge failed') }
    finally { setMerging(false) }
  }

  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>

  return (
    <div>
      <div className="flex items-center gap-2 px-4 py-2 border-b border-slate-100 bg-slate-50 flex-wrap">
        <Button size="sm" variant="secondary" onClick={() => openEdit({ id: null, name: '', categories_id: null })}>
          <Plus size={13} /> Add Payee
        </Button>
        <Button size="sm" variant="secondary" onClick={() => { setMergeSource(''); setMergeTarget(''); setMergeError(null); setMergeOpen(true) }}>
          <ArrowRightLeft size={13} /> Merge Payees
        </Button>
        {deleteError && <span className="text-xs text-red-600 bg-red-50 rounded px-3 py-1">{deleteError}</span>}
        <span className="ml-auto text-xs text-slate-400">{filtered.length} payees · double-click to edit</span>
      </div>
      <div className="ag-theme-alpine" style={{ height: '560px', width: '100%' }}>
        <AgGridReact
          rowData={filtered}
          onRowClicked={(e: RowClickedEvent) => { if ((e.event as MouseEvent)?.detail === 2) openEdit(e.data as Record<string, unknown>) }}
          columnDefs={[
            { field: 'id', headerName: 'ID', width: 70 },
            { field: 'name', headerName: 'Payee Name', flex: 2, minWidth: 160 },
            { field: 'default_category', headerName: 'Default Category', flex: 2, minWidth: 180 },
            { field: 'transactions_count', headerName: '# Txns', width: 90, type: 'numericColumn' },
            { field: 'last_transaction', headerName: 'Last Used', width: 110, valueFormatter: p => p.value?.slice(0, 10) ?? '—' },
            {
              headerName: '', width: 80, sortable: false, filter: false,
              cellRenderer: (p: { data: Record<string, unknown> }) => (
                <div className="flex gap-1 items-center h-full">
                  <button onClick={() => openEdit(p.data)} className="text-blue-500 hover:text-blue-700 p-1"><Pencil size={13} /></button>
                  <button onClick={() => handleDelete(Number(p.data.id))} className="text-red-400 hover:text-red-600 p-1"><Trash2 size={13} /></button>
                </div>
              ),
            },
          ]}
          defaultColDef={{ resizable: true, sortable: true, filter: true }}
        />
      </div>

      {editRow && (
        <Modal title={editRow.id ? 'Edit Payee' : 'New Payee'} onClose={() => setEditRow(null)}
          footer={<>
            {editRow.id && <Button variant="destructive" onClick={() => { setEditRow(null); handleDelete(Number(editRow.id)) }} disabled={saving}><Trash2 size={14} /> Delete</Button>}
            <span className="flex-1" />
            <Button variant="secondary" onClick={() => setEditRow(null)}>Cancel</Button>
            <Button onClick={handleSave} disabled={saving || !editName.trim()}><Save size={14} /> {saving ? 'Saving…' : 'Save'}</Button>
          </>}>
          <Field label="Name *">
            <Input value={editName} onChange={e => setEditName(e.target.value)} />
          </Field>
          <Field label="Default Category">
            <select className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm" value={editCatId} onChange={e => setEditCatId(e.target.value)}>
              <option value="">— none —</option>
              {(categories as Record<string, unknown>[]).map(c => (
                <option key={String(c.id)} value={String(c.id)}>{String(c.full_path ?? c.name)}</option>
              ))}
            </select>
          </Field>
          {error && <p className="text-xs text-red-600 bg-red-50 rounded px-3 py-2">{error}</p>}
        </Modal>
      )}

      {mergeOpen && (
        <Modal title="Merge Payees" wide onClose={() => setMergeOpen(false)}
          footer={<>
            <Button variant="secondary" onClick={() => setMergeOpen(false)}>Cancel</Button>
            <Button variant="destructive" onClick={handleMerge} disabled={merging || !mergeSource || !mergeTarget || mergeSource === mergeTarget}>
              <ArrowRightLeft size={14} /> {merging ? 'Merging…' : 'Merge'}
            </Button>
          </>}>
          <p className="text-sm text-slate-600">All transactions from the <strong>source</strong> payee will be reassigned to the <strong>target</strong>, then the source is deleted.</p>
          <Field label="Source payee (will be deleted)">
            <select className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm" value={mergeSource} onChange={e => setMergeSource(e.target.value)}>
              <option value="">— select —</option>
              {(payees as Record<string, unknown>[]).map(p => (
                <option key={String(p.id)} value={String(p.id)}>{String(p.name)} ({p.transactions_count} txns)</option>
              ))}
            </select>
          </Field>
          <Field label="Target payee (will be kept)">
            <select className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm" value={mergeTarget} onChange={e => setMergeTarget(e.target.value)}>
              <option value="">— select —</option>
              {(payees as Record<string, unknown>[]).filter(p => String(p.id) !== mergeSource).map(p => (
                <option key={String(p.id)} value={String(p.id)}>{String(p.name)} ({p.transactions_count} txns)</option>
              ))}
            </select>
          </Field>
          {mergeSource && (
            <div className="mt-2">
              <p className="text-xs font-medium text-slate-500 mb-1">Transactions to be reassigned</p>
              {(mergePreview as Record<string, unknown>[]).length === 0
                ? <p className="text-xs text-slate-400 italic">No transactions found.</p>
                : <div className="max-h-48 overflow-y-auto rounded border border-slate-200">
                    <table className="w-full text-xs">
                      <thead className="bg-slate-50 sticky top-0">
                        <tr>
                          <th className="px-2 py-1 text-left font-medium text-slate-500">Date</th>
                          <th className="px-2 py-1 text-left font-medium text-slate-500">Account</th>
                          <th className="px-2 py-1 text-left font-medium text-slate-500">Description</th>
                          <th className="px-2 py-1 text-right font-medium text-slate-500">Amount</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-100">
                        {(mergePreview as Record<string, unknown>[]).map((tx, i) => (
                          <tr key={i} className="hover:bg-slate-50">
                            <td className="px-2 py-1 text-slate-600 whitespace-nowrap">{String(tx.date ?? '').slice(0, 10)}</td>
                            <td className="px-2 py-1 text-slate-600">{String(tx.account ?? '')}</td>
                            <td className="px-2 py-1 text-slate-600 truncate max-w-[180px]">{String(tx.description ?? '')}</td>
                            <td className="px-2 py-1 text-right font-mono text-slate-700">{fmtNum(Number(tx.amount ?? 0), 2)} {String(tx.currency ?? '')}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
              }
            </div>
          )}
          {mergeError && <p className="text-xs text-red-600 bg-red-50 rounded px-3 py-2">{mergeError}</p>}
        </Modal>
      )}
    </div>
  )
}

// ── Categories Tab ────────────────────────────────────────────────────────────
function CategoriesTab({ search }: { search: string }) {
  const qc = useQueryClient()
  const [editRow, setEditRow] = useState<Record<string, unknown> | null>(null)
  const [form, setForm] = useState({ name: '', parent_id: '', type: 'Expense' })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [mergeOpen, setMergeOpen] = useState(false)
  const [mergeSource, setMergeSource] = useState('')
  const [mergeTarget, setMergeTarget] = useState('')
  const [merging, setMerging] = useState(false)
  const [mergeError, setMergeError] = useState<string | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)

  const { data: categories = [], isLoading } = useQuery({ queryKey: ['categories'], queryFn: () => getCategories() })
  const { data: catMergePreview = [] } = useQuery({
    queryKey: ['category-tx-preview', mergeSource],
    queryFn: () => getCategoryTransactions(Number(mergeSource)),
    enabled: !!mergeSource,
  })

  const filtered = search
    ? (categories as Record<string, unknown>[]).filter(r =>
        String(r.full_path ?? '').toLowerCase().includes(search.toLowerCase()) ||
        String(r.type ?? '').toLowerCase().includes(search.toLowerCase()))
    : categories as Record<string, unknown>[]

  const openEdit = (row: Record<string, unknown>) => {
    setEditRow(row)
    const namePart = String(row.full_path ?? '').split(' : ').pop() ?? ''
    setForm({ name: namePart, parent_id: row.parent_id != null ? String(row.parent_id) : '', type: String(row.type ?? 'Expense') })
    setError(null)
  }

  const openNew = () => {
    setEditRow({})
    setForm({ name: '', parent_id: '', type: 'Expense' })
    setError(null)
  }

  const handleSave = async () => {
    setSaving(true); setError(null)
    try {
      await upsertCategory({
        id: editRow?.id ?? undefined,
        name: form.name,
        parent_id: form.parent_id ? Number(form.parent_id) : null,
        type: form.type,
      })
      qc.invalidateQueries({ queryKey: ['categories'] })
      setEditRow(null)
    } catch (e: unknown) { setError(e instanceof Error ? e.message : 'Save failed') }
    finally { setSaving(false) }
  }

  const handleDelete = async (id: number) => {
    if (!confirm('Delete this category? This will also delete all sub-categories.')) return
    setDeleteError(null)
    try {
      await deleteRow('categories', id)
      qc.invalidateQueries({ queryKey: ['categories'] })
    } catch (e) { setDeleteError(extractError(e)) }
  }

  const handleMerge = async () => {
    if (!mergeSource || !mergeTarget || mergeSource === mergeTarget) return
    setMerging(true); setMergeError(null)
    try {
      await mergeCategories(Number(mergeSource), Number(mergeTarget))
      qc.invalidateQueries({ queryKey: ['categories'] })
      setMergeOpen(false); setMergeSource(''); setMergeTarget('')
    } catch (e: unknown) { setMergeError(e instanceof Error ? e.message : 'Merge failed') }
    finally { setMerging(false) }
  }

  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>

  const catList = categories as Record<string, unknown>[]

  return (
    <div>
      <div className="flex items-center gap-2 px-4 py-2 border-b border-slate-100 bg-slate-50 flex-wrap">
        <Button size="sm" variant="secondary" onClick={openNew}><Plus size={13} /> Add Category</Button>
        <Button size="sm" variant="secondary" onClick={() => { setMergeSource(''); setMergeTarget(''); setMergeError(null); setMergeOpen(true) }}>
          <ArrowRightLeft size={13} /> Merge Categories
        </Button>
        {deleteError && <span className="text-xs text-red-600 bg-red-50 rounded px-3 py-1">{deleteError}</span>}
        <span className="ml-auto text-xs text-slate-400">{filtered.length} categories · double-click to edit</span>
      </div>
      <div className="ag-theme-alpine" style={{ height: '560px', width: '100%' }}>
        <AgGridReact
          rowData={filtered}
          onRowClicked={(e: RowClickedEvent) => { if ((e.event as MouseEvent)?.detail === 2) openEdit(e.data as Record<string, unknown>) }}
          columnDefs={[
            { field: 'id', headerName: 'ID', width: 70 },
            { field: 'full_path', headerName: 'Category', flex: 3, minWidth: 200 },
            { field: 'type', headerName: 'Type', width: 120 },
            { field: 'level', headerName: 'Level', width: 70, type: 'numericColumn' },
            { field: 'transactions_count', headerName: '# Txns', width: 90, type: 'numericColumn' },
            {
              headerName: '', width: 80, sortable: false, filter: false,
              cellRenderer: (p: { data: Record<string, unknown> }) => (
                <div className="flex gap-1 items-center h-full">
                  <button onClick={() => openEdit(p.data)} className="text-blue-500 hover:text-blue-700 p-1"><Pencil size={13} /></button>
                  <button onClick={() => handleDelete(Number(p.data.id))} className="text-red-400 hover:text-red-600 p-1"><Trash2 size={13} /></button>
                </div>
              ),
            },
          ]}
          defaultColDef={{ resizable: true, sortable: true, filter: true }}
        />
      </div>

      {editRow !== null && (
        <Modal title={editRow.id ? 'Edit Category' : 'New Category'} onClose={() => setEditRow(null)}
          footer={<>
            {editRow.id && <Button variant="destructive" onClick={() => { setEditRow(null); handleDelete(Number(editRow.id)) }} disabled={saving}><Trash2 size={14} /> Delete</Button>}
            <span className="flex-1" />
            <Button variant="secondary" onClick={() => setEditRow(null)}>Cancel</Button>
            <Button onClick={handleSave} disabled={saving || !form.name.trim()}><Save size={14} /> {saving ? 'Saving…' : 'Save'}</Button>
          </>}>
          <Field label="Name *">
            <Input value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} placeholder="Category name" />
          </Field>
          <Field label="Parent category">
            <select className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm" value={form.parent_id} onChange={e => setForm(f => ({ ...f, parent_id: e.target.value }))}>
              <option value="">— top level —</option>
              {(catList as Record<string, unknown>[])
                .filter(c => !editRow.id || c.id !== editRow.id)
                .map(c => (
                  <option key={String(c.id)} value={String(c.id)}>{String(c.full_path)}</option>
                ))}
            </select>
          </Field>
          <Field label="Type *">
            <select className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm" value={form.type} onChange={e => setForm(f => ({ ...f, type: e.target.value }))}>
              {CATEGORY_TYPES.map(t => <option key={t}>{t}</option>)}
            </select>
          </Field>
          {error && <p className="text-xs text-red-600 bg-red-50 rounded px-3 py-2">{error}</p>}
        </Modal>
      )}

      {mergeOpen && (
        <Modal title="Merge Categories" wide onClose={() => setMergeOpen(false)}
          footer={<>
            <Button variant="secondary" onClick={() => setMergeOpen(false)}>Cancel</Button>
            <Button variant="destructive" onClick={handleMerge} disabled={merging || !mergeSource || !mergeTarget || mergeSource === mergeTarget}>
              <ArrowRightLeft size={14} /> {merging ? 'Merging…' : 'Merge'}
            </Button>
          </>}>
          <p className="text-sm text-slate-600">All transaction splits assigned to the <strong>source</strong> category will be reassigned to the <strong>target</strong>, then the source is deleted.</p>
          <Field label="Source category (will be deleted)">
            <select className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm" value={mergeSource} onChange={e => setMergeSource(e.target.value)}>
              <option value="">— select —</option>
              {catList.map(c => (
                <option key={String(c.id)} value={String(c.id)}>{String(c.full_path)} ({c.transactions_count} splits)</option>
              ))}
            </select>
          </Field>
          <Field label="Target category (will be kept)">
            <select className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm" value={mergeTarget} onChange={e => setMergeTarget(e.target.value)}>
              <option value="">— select —</option>
              {catList.filter(c => String(c.id) !== mergeSource).map(c => (
                <option key={String(c.id)} value={String(c.id)}>{String(c.full_path)} ({c.transactions_count} splits)</option>
              ))}
            </select>
          </Field>
          {mergeSource && (
            <div className="mt-2">
              <p className="text-xs font-medium text-slate-500 mb-1">Splits to be reassigned</p>
              {(catMergePreview as Record<string, unknown>[]).length === 0
                ? <p className="text-xs text-slate-400 italic">No transactions found.</p>
                : <div className="max-h-48 overflow-y-auto rounded border border-slate-200">
                    <table className="w-full text-xs">
                      <thead className="bg-slate-50 sticky top-0">
                        <tr>
                          <th className="px-2 py-1 text-left font-medium text-slate-500 whitespace-nowrap">Date</th>
                          <th className="px-2 py-1 text-left font-medium text-slate-500">Account</th>
                          <th className="px-2 py-1 text-left font-medium text-slate-500">Payee</th>
                          <th className="px-2 py-1 text-left font-medium text-slate-500">Description</th>
                          <th className="px-2 py-1 text-right font-medium text-slate-500 whitespace-nowrap">Amount</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-100">
                        {(catMergePreview as Record<string, unknown>[]).map((tx, i) => (
                          <tr key={i} className="hover:bg-slate-50">
                            <td className="px-2 py-1 text-slate-600 whitespace-nowrap">{String(tx.date ?? '').slice(0, 10)}</td>
                            <td className="px-2 py-1 text-slate-600 whitespace-nowrap">{String(tx.account ?? '')}</td>
                            <td className="px-2 py-1 text-slate-600 whitespace-nowrap">{String(tx.payee ?? '')}</td>
                            <td className="px-2 py-1 text-slate-600 truncate max-w-[220px]">{String(tx.description ?? '')}</td>
                            <td className="px-2 py-1 text-right font-mono text-slate-700 whitespace-nowrap">{Number(tx.amount ?? 0).toFixed(2)} {String(tx.currency ?? '')}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
              }
            </div>
          )}
          {mergeError && <p className="text-xs text-red-600 bg-red-50 rounded px-3 py-2">{mergeError}</p>}
        </Modal>
      )}
    </div>
  )
}

// ── Accounts Tab ──────────────────────────────────────────────────────────────
function AccountsTab({ search }: { search: string }) {
  const qc = useQueryClient()
  const [editRow, setEditRow] = useState<Record<string, unknown> | null>(null)
  const [form, setForm] = useState<Record<string, unknown>>({})
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const { data: accounts = [], isLoading } = useQuery({ queryKey: ['accounts-master'], queryFn: () => getAccountsMaster() })
  const { data: institutions = [] } = useQuery({ queryKey: ['institutions'], queryFn: () => getInstitutions() })
  const { data: currencies = [] } = useQuery({ queryKey: ['currencies-master'], queryFn: () => getCurrenciesMaster() })

  const filtered = search
    ? (accounts as Record<string, unknown>[]).filter(r =>
        Object.values(r).some(v => String(v ?? '').toLowerCase().includes(search.toLowerCase())))
    : accounts as Record<string, unknown>[]

  const openEdit = (row: Record<string, unknown>) => {
    setEditRow(row)
    setForm({ ...row })
    setError(null)
  }

  const openNew = () => {
    setEditRow({})
    setForm({ name: '', type: 'Checking', currencies_id: '', institutions_id: '', iban: '', credit_limit: '', is_active: true, accounts_id_linked: '' })
    setError(null)
  }

  const handleSave = async () => {
    setSaving(true); setError(null)
    try {
      await api.post('/static-data/accounts', {
        id: form.id ?? undefined,
        name: form.name,
        type: form.type,
        currencies_id: form.currencies_id ? Number(form.currencies_id) : null,
        institutions_id: form.institutions_id ? Number(form.institutions_id) : null,
        iban: form.iban || null,
        credit_limit: form.credit_limit ? Number(form.credit_limit) : 0,
        is_active: form.is_active,
        accounts_id_linked: form.accounts_id_linked ? Number(form.accounts_id_linked) : null,
      })
      qc.invalidateQueries({ queryKey: ['accounts-master'] })
      qc.invalidateQueries({ queryKey: ['accounts'] })
      setEditRow(null)
    } catch (e: unknown) { setError(e instanceof Error ? e.message : 'Save failed') }
    finally { setSaving(false) }
  }

  const [deleteError, setDeleteError] = useState<string | null>(null)

  const handleDeactivate = async (id: number) => {
    if (!confirm('Delete this account?')) return
    setDeleteError(null)
    try {
      await deleteRow('accounts', id)
      qc.invalidateQueries({ queryKey: ['accounts-master'] })
      qc.invalidateQueries({ queryKey: ['accounts'] })
    } catch (e) { setDeleteError(extractError(e)) }
  }

  const set = (k: string, v: unknown) => setForm(f => ({ ...f, [k]: v }))

  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>

  const instList = institutions as Record<string, unknown>[]
  const currList = currencies as Record<string, unknown>[]
  const acctList = accounts as Record<string, unknown>[]

  return (
    <div>
      <div className="flex items-center gap-2 px-4 py-2 border-b border-slate-100 bg-slate-50">
        <Button size="sm" variant="secondary" onClick={openNew}><Plus size={13} /> Add Account</Button>
        {deleteError && <span className="text-xs text-red-600 bg-red-50 rounded px-3 py-1">{deleteError}</span>}
        <span className="ml-auto text-xs text-slate-400">{filtered.length} accounts · double-click to edit</span>
      </div>
      <div className="ag-theme-alpine" style={{ height: '560px', width: '100%' }}>
        <AgGridReact
          rowData={filtered}
          onRowClicked={(e: RowClickedEvent) => { if ((e.event as MouseEvent)?.detail === 2) openEdit(e.data as Record<string, unknown>) }}
          columnDefs={[
            { field: 'id', headerName: 'ID', width: 70 },
            { field: 'name', headerName: 'Account', flex: 2, minWidth: 160 },
            { field: 'type', headerName: 'Type', width: 130 },
            { field: 'currency', headerName: 'Currency', width: 90 },
            { field: 'balance', headerName: 'Balance', width: 120, type: 'numericColumn', valueFormatter: p => p.value != null ? fmtNum(Number(p.value), 2) : '—' },
            { field: 'institution', headerName: 'Institution', flex: 1, minWidth: 140 },
            { field: 'iban', headerName: 'IBAN', flex: 1, minWidth: 140 },
            { field: 'linked_account_name', headerName: 'Linked Account', flex: 1, minWidth: 140 },
            { field: 'is_active', headerName: 'Active', width: 80, cellRenderer: (p: { value: boolean }) => p.value ? '✓' : '' },
            {
              headerName: '', width: 80, sortable: false, filter: false,
              cellRenderer: (p: { data: Record<string, unknown> }) => (
                <div className="flex gap-1 items-center h-full">
                  <button onClick={() => openEdit(p.data)} className="text-blue-500 hover:text-blue-700 p-1"><Pencil size={13} /></button>
                  <button onClick={() => handleDeactivate(Number(p.data.id))} className="text-red-400 hover:text-red-600 p-1" title="Delete"><Trash2 size={13} /></button>
                </div>
              ),
            },
          ]}
          defaultColDef={{ resizable: true, sortable: true, filter: true }}
        />
      </div>

      {editRow !== null && (
        <Modal title={form.id ? 'Edit Account' : 'New Account'} onClose={() => setEditRow(null)}
          footer={<>
            {form.id && <Button variant="destructive" onClick={() => { setEditRow(null); handleDeactivate(Number(form.id)) }} disabled={saving}><Trash2 size={14} /> Delete</Button>}
            <span className="flex-1" />
            <Button variant="secondary" onClick={() => setEditRow(null)}>Cancel</Button>
            <Button onClick={handleSave} disabled={saving || !String(form.name ?? '').trim() || !form.currencies_id}>
              <Save size={14} /> {saving ? 'Saving…' : 'Save'}
            </Button>
          </>}>
          <div className="grid grid-cols-2 gap-3">
            <div className="col-span-2">
              <Field label="Account Name *">
                <Input value={String(form.name ?? '')} onChange={e => set('name', e.target.value)} placeholder="e.g. Revolut - Main" />
              </Field>
            </div>
            <Field label="Type *">
              <select className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm" value={String(form.type ?? '')} onChange={e => set('type', e.target.value)}>
                {ACCOUNT_TYPES.map(t => <option key={t}>{t}</option>)}
              </select>
            </Field>
            <Field label="Currency *">
              <select className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm" value={String(form.currencies_id ?? '')} onChange={e => set('currencies_id', e.target.value)}>
                <option value="">— select —</option>
                {currList.map(c => <option key={String(c.id)} value={String(c.id)}>{String(c.code)} – {String(c.name)}</option>)}
              </select>
            </Field>
            <div className="col-span-2">
              <Field label="Institution">
                <select className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm" value={String(form.institutions_id ?? '')} onChange={e => set('institutions_id', e.target.value)}>
                  <option value="">— none —</option>
                  {instList.map(i => <option key={String(i.id)} value={String(i.id)}>{String(i.name)}</option>)}
                </select>
              </Field>
            </div>
            <div className="col-span-2">
              <Field label="IBAN">
                <Input value={String(form.iban ?? '')} onChange={e => set('iban', e.target.value)} placeholder="GR12 3456 7890 1234 5678 901" />
              </Field>
            </div>
            <Field label="Credit Limit">
              <Input type="number" step="0.01" value={String(form.credit_limit ?? '')} onChange={e => set('credit_limit', e.target.value)} placeholder="0.00" />
            </Field>
            <Field label="Linked Account">
              <select className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm" value={String(form.accounts_id_linked ?? '')} onChange={e => set('accounts_id_linked', e.target.value)}>
                <option value="">— none —</option>
                {acctList.filter(a => String(a.id) !== String(form.id) && !['Brokerage', 'Pension', 'Other Investment', 'Margin'].includes(String(a.type ?? ''))).map(a => <option key={String(a.id)} value={String(a.id)}>{String(a.name)}</option>)}
              </select>
            </Field>
            <div className="col-span-2">
              <label className="flex items-center gap-2 text-sm text-slate-700 cursor-pointer">
                <input type="checkbox" checked={Boolean(form.is_active)} onChange={e => set('is_active', e.target.checked)} className="rounded" />
                Active
              </label>
            </div>
          </div>
          {error && <p className="text-xs text-red-600 bg-red-50 rounded px-3 py-2">{error}</p>}
        </Modal>
      )}
    </div>
  )
}

// ── GridTab (Institutions, Securities, Currencies) ────────────────────────────
const INSTITUTION_COLS: ColDef[] = [
  { field: 'id', headerName: 'ID', width: 70, editable: false },
  { field: 'name', headerName: 'Institution', flex: 2, minWidth: 160, editable: true },
  { field: 'type', headerName: 'Type', width: 130, editable: true },
  { field: 'bic', headerName: 'BIC', width: 110, editable: true },
  { field: 'moodys', headerName: "Moody's", width: 90, editable: true },
  { field: 'sp', headerName: 'S&P', width: 80, editable: true },
  { field: 'fitch', headerName: 'Fitch', width: 80, editable: true },
  { field: 'website', headerName: 'Website', flex: 1, minWidth: 140, editable: true },
  { field: 'contact', headerName: 'Contact', flex: 1, minWidth: 120, editable: true },
  { field: 'phone', headerName: 'Phone', width: 130, editable: true },
  { field: 'email', headerName: 'Email', flex: 1, minWidth: 140, editable: true },
  { field: 'notes', headerName: 'Notes', flex: 1, minWidth: 140, editable: true },
]

const SECURITY_COLS: ColDef[] = [
  { field: 'id', headerName: 'ID', width: 70, editable: false },
  { field: 'ticker', headerName: 'Ticker', width: 100, editable: true, cellStyle: { fontFamily: 'monospace', fontWeight: 600 } },
  { field: 'name', headerName: 'Name', flex: 2, minWidth: 180, editable: true },
  { field: 'type', headerName: 'Type', width: 130, editable: true },
  { field: 'instrument_type', headerName: 'Instrument', width: 120, editable: true },
  { field: 'currency', headerName: 'Currency', width: 90, editable: false },
  { field: 'latest_price', headerName: 'Last Price', width: 110, type: 'numericColumn', editable: false, valueFormatter: p => p.value != null ? fmtNum(Number(p.value), 4) : '—' },
  { field: 'price_date', headerName: 'Price Date', width: 110, editable: false, valueFormatter: p => p.value?.slice(0, 10) ?? '—' },
  { field: 'held_in_accounts', headerName: 'Held In', width: 80, type: 'numericColumn', editable: false },
]

const CURRENCY_COLS: ColDef[] = [
  { field: 'id', headerName: 'ID', width: 70, editable: false },
  { field: 'code', headerName: 'Code', width: 90, editable: true },
  { field: 'name', headerName: 'Name', flex: 2, minWidth: 160, editable: true },
  { field: 'symbol', headerName: 'Symbol', width: 80, editable: true },
  { field: 'latest_rate', headerName: 'Rate vs EUR', width: 120, type: 'numericColumn', editable: false, valueFormatter: p => p.value != null ? fmtNum(Number(p.value), 4) : '—' },
  { field: 'rate_date', headerName: 'Rate Date', width: 110, editable: false, valueFormatter: p => p.value?.slice(0, 10) ?? '—' },
]

const GRID_TAB_CONFIG: Record<string, {
  queryKey: string
  queryFn: (s?: string) => Promise<unknown>
  colDefs: ColDef[]
  upsertFn: (d: Record<string, unknown>) => Promise<unknown>
  deleteTable: string
  idField: string
  newRow: () => Record<string, unknown>
}> = {
  Institutions: {
    queryKey: 'institutions',
    queryFn: (s) => getInstitutions(s),
    colDefs: INSTITUTION_COLS,
    upsertFn: upsertInstitution,
    deleteTable: 'institutions',
    idField: 'id',
    newRow: () => ({ name: 'New Institution', type: 'Bank' }),
  },
  Securities: {
    queryKey: 'securities-master',
    queryFn: (s) => getSecuritiesMaster(s),
    colDefs: SECURITY_COLS,
    upsertFn: upsertSecurity,
    deleteTable: 'securities',
    idField: 'id',
    newRow: () => ({ ticker: 'NEW', name: 'New Security', type: 'Stock' }),
  },
  Currencies: {
    queryKey: 'currencies-master',
    queryFn: () => getCurrenciesMaster(),
    colDefs: CURRENCY_COLS,
    upsertFn: upsertCurrency,
    deleteTable: 'currencies',
    idField: 'id',
    newRow: () => ({ code: 'XXX', name: 'New Currency', symbol: '$' }),
  },
}

// ── Institutions modal for full edit ─────────────────────────────────────────
function InstitutionsTab({ search }: { search: string }) {
  const qc = useQueryClient()
  const [editRow, setEditRow] = useState<Record<string, unknown> | null>(null)
  const [form, setForm] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const { data = [], isLoading } = useQuery({ queryKey: ['institutions'], queryFn: () => getInstitutions() })

  const filtered = search
    ? (data as Record<string, unknown>[]).filter(r => Object.values(r).some(v => String(v ?? '').toLowerCase().includes(search.toLowerCase())))
    : data as Record<string, unknown>[]

  const openEdit = (row: Record<string, unknown>) => {
    setEditRow(row)
    setForm(Object.fromEntries(Object.entries(row).map(([k, v]) => [k, v != null ? String(v) : ''])))
    setError(null)
  }

  const openNew = () => {
    setEditRow({})
    setForm({ name: '', type: 'Bank', bic: '', moodys: '', sp: '', fitch: '', contact: '', phone: '', email: '', website: '', notes: '' })
    setError(null)
  }

  const handleSave = async () => {
    if ((form.bic ?? '').length > 11) { setError('BIC Code must be 11 characters or fewer'); return }
    setSaving(true); setError(null)
    try {
      await upsertInstitution({ id: editRow?.id ?? undefined, ...form })
      qc.invalidateQueries({ queryKey: ['institutions'] })
      setEditRow(null)
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(detail ?? (e instanceof Error ? e.message : 'Save failed'))
    }
    finally { setSaving(false) }
  }

  const [deleteError, setDeleteError] = useState<string | null>(null)

  const handleDelete = async (id: number) => {
    if (!confirm('Delete this institution?')) return
    setDeleteError(null)
    try {
      await deleteRow('institutions', id)
      qc.invalidateQueries({ queryKey: ['institutions'] })
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? (e instanceof Error ? e.message : 'Delete failed')
      setDeleteError(msg)
    }
  }

  const set = (k: string, v: string) => setForm(f => ({ ...f, [k]: v }))

  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>

  return (
    <div>
      <div className="flex items-center gap-2 px-4 py-2 border-b border-slate-100 bg-slate-50">
        <Button size="sm" variant="secondary" onClick={openNew}><Plus size={13} /> Add Institution</Button>
        {deleteError && <span className="text-xs text-red-600 bg-red-50 rounded px-3 py-1">{deleteError}</span>}
        <span className="ml-auto text-xs text-slate-400">{filtered.length} institutions · double-click to edit</span>
      </div>
      <div className="ag-theme-alpine" style={{ height: '560px', width: '100%' }}>
        <AgGridReact
          rowData={filtered}
          onRowClicked={(e: RowClickedEvent) => { if ((e.event as MouseEvent)?.detail === 2) openEdit(e.data as Record<string, unknown>) }}
          columnDefs={[
            { field: 'id', headerName: 'ID', width: 70 },
            { field: 'name', headerName: 'Institution', flex: 2, minWidth: 160 },
            { field: 'type', headerName: 'Type', width: 130 },
            { field: 'bic', headerName: 'BIC', width: 110 },
            { field: 'moodys', headerName: "Moody's", width: 90 },
            { field: 'sp', headerName: 'S&P', width: 80 },
            { field: 'fitch', headerName: 'Fitch', width: 80 },
            { field: 'website', headerName: 'Website', flex: 1, minWidth: 140 },
            { field: 'contact', headerName: 'Contact', flex: 1, minWidth: 120 },
            {
              headerName: '', width: 80, sortable: false, filter: false,
              cellRenderer: (p: { data: Record<string, unknown> }) => (
                <div className="flex gap-1 items-center h-full">
                  <button onClick={() => openEdit(p.data)} className="text-blue-500 hover:text-blue-700 p-1"><Pencil size={13} /></button>
                  <button onClick={() => handleDelete(Number(p.data.id))} className="text-red-400 hover:text-red-600 p-1"><Trash2 size={13} /></button>
                </div>
              ),
            },
          ]}
          defaultColDef={{ resizable: true, sortable: true, filter: true }}
        />
      </div>

      {editRow !== null && (
        <Modal title={form.id ? 'Edit Institution' : 'New Institution'} onClose={() => setEditRow(null)}
          footer={<>
            {form.id && <Button variant="destructive" onClick={() => { setEditRow(null); handleDelete(Number(form.id)) }} disabled={saving}><Trash2 size={14} /> Delete</Button>}
            <span className="flex-1" />
            <Button variant="secondary" onClick={() => setEditRow(null)}>Cancel</Button>
            <Button onClick={handleSave} disabled={saving || !form.name?.trim()}><Save size={14} /> {saving ? 'Saving…' : 'Save'}</Button>
          </>}>
          <div className="grid grid-cols-2 gap-3">
            <div className="col-span-2">
              <Field label="Name *"><Input value={form.name ?? ''} onChange={e => set('name', e.target.value)} /></Field>
            </div>
            <Field label="Type *">
              <select className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm" value={form.type ?? 'Bank'} onChange={e => set('type', e.target.value)}>
                {INSTITUTION_TYPES.map(t => <option key={t}>{t}</option>)}
              </select>
            </Field>
            <Field label="BIC Code"><Input value={form.bic ?? ''} onChange={e => set('bic', e.target.value)} placeholder="ABCDGRAA" maxLength={11} /></Field>
            <Field label="Moody's"><Input value={form.moodys ?? ''} onChange={e => set('moodys', e.target.value)} placeholder="Aaa" /></Field>
            <Field label="S&P"><Input value={form.sp ?? ''} onChange={e => set('sp', e.target.value)} placeholder="AAA" /></Field>
            <Field label="Fitch"><Input value={form.fitch ?? ''} onChange={e => set('fitch', e.target.value)} placeholder="AAA" /></Field>
            <Field label="Contact"><Input value={form.contact ?? ''} onChange={e => set('contact', e.target.value)} /></Field>
            <Field label="Phone"><Input value={form.phone ?? ''} onChange={e => set('phone', e.target.value)} /></Field>
            <div className="col-span-2">
              <Field label="Email"><Input value={form.email ?? ''} onChange={e => set('email', e.target.value)} /></Field>
            </div>
            <div className="col-span-2">
              <Field label="Website"><Input value={form.website ?? ''} onChange={e => set('website', e.target.value)} placeholder="https://…" /></Field>
            </div>
            <div className="col-span-2">
              <Field label="Notes">
                <textarea className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm resize-none" rows={2} value={form.notes ?? ''} onChange={e => set('notes', e.target.value)} />
              </Field>
            </div>
          </div>
          {error && <p className="text-xs text-red-600 bg-red-50 rounded px-3 py-2">{error}</p>}
        </Modal>
      )}
    </div>
  )
}

function GridTab({ tabName, search }: { tabName: string; search: string }) {
  const cfg = GRID_TAB_CONFIG[tabName]
  const qc = useQueryClient()
  const [gridApi, setGridApi] = useState<GridApi | null>(null)
  const [pendingChanges, setPendingChanges] = useState<Map<string, Record<string, unknown>>>(new Map())
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const { data = [], isLoading } = useQuery({ queryKey: [cfg.queryKey], queryFn: () => cfg.queryFn() })

  const filtered = search
    ? (data as Record<string, unknown>[]).filter(r => Object.values(r).some(v => String(v ?? '').toLowerCase().includes(search.toLowerCase())))
    : data as Record<string, unknown>[]

  const onGridReady = useCallback((e: GridReadyEvent) => setGridApi(e.api), [])

  const onCellValueChanged = useCallback((e: CellValueChangedEvent) => {
    const row = e.data as Record<string, unknown>
    const key = String(row[cfg.idField] ?? `_new_${Date.now()}`)
    setPendingChanges(prev => new Map(prev).set(key, { ...row }))
  }, [cfg.idField])

  const saveChanges = async () => {
    setSaving(true); setError(null)
    try {
      for (const row of pendingChanges.values()) await cfg.upsertFn(row)
      setPendingChanges(new Map())
      qc.invalidateQueries({ queryKey: [cfg.queryKey] })
    } catch (e: unknown) { setError(extractError(e)) }
    finally { setSaving(false) }
  }

  const deleteSelected = async () => {
    if (!gridApi) return
    const selected = gridApi.getSelectedRows() as Record<string, unknown>[]
    if (selected.length === 0) return
    if (!confirm(`Delete ${selected.length} row(s)?`)) return
    setSaving(true); setError(null)
    try {
      for (const row of selected) if (row[cfg.idField]) await deleteRow(cfg.deleteTable, Number(row[cfg.idField]))
      qc.invalidateQueries({ queryKey: [cfg.queryKey] })
    } catch (e: unknown) { setError(extractError(e)) }
    finally { setSaving(false) }
  }

  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>

  return (
    <div>
      <div className="flex items-center gap-2 px-4 py-2 border-b border-slate-100 bg-slate-50">
        <Button size="sm" variant="secondary" onClick={() => {
          if (!gridApi) return
          const newRow = cfg.newRow()
          gridApi.applyTransaction({ add: [newRow], addIndex: 0 })
          const key = `_new_${Date.now()}`
          setPendingChanges(prev => new Map(prev).set(key, newRow))
          gridApi.startEditingCell({ rowIndex: 0, colKey: cfg.colDefs.find(c => c.editable)?.field ?? '' })
        }}><Plus size={13} /> Add</Button>
        <Button size="sm" variant="destructive" onClick={deleteSelected} disabled={saving}><Trash2 size={13} /> Delete Selected</Button>
        {pendingChanges.size > 0 && (
          <Button size="sm" onClick={saveChanges} disabled={saving}>
            <Save size={13} /> {saving ? 'Saving…' : `Save (${pendingChanges.size} changed)`}
          </Button>
        )}
        {error && <span className="text-xs text-red-600 ml-2">{error}</span>}
        <span className="ml-auto text-xs text-slate-400">{filtered.length} rows · Double-click cell to edit</span>
      </div>
      <div className="ag-theme-alpine" style={{ height: '560px', width: '100%' }}>
        <AgGridReact
          rowData={filtered}
          columnDefs={cfg.colDefs}
          defaultColDef={{ resizable: true, sortable: true, filter: true }}
          rowSelection="multiple"
          onGridReady={onGridReady}
          onCellValueChanged={onCellValueChanged}
          stopEditingWhenCellsLoseFocus
        />
      </div>
    </div>
  )
}


// ── Currencies Tab ────────────────────────────────────────────────────────────
function CurrenciesTab({ search }: { search: string }) {
  const qc = useQueryClient()
  const [editRow, setEditRow] = useState<Record<string, unknown> | null>(null)
  const [form, setForm] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)

  const { data = [], isLoading } = useQuery({ queryKey: ['currencies-master'], queryFn: () => getCurrenciesMaster() })
  const filtered = search
    ? (data as Record<string, unknown>[]).filter(r => Object.values(r).some(v => String(v ?? '').toLowerCase().includes(search.toLowerCase())))
    : data as Record<string, unknown>[]

  const set = (k: string, v: string) => setForm(f => ({ ...f, [k]: v }))

  const openEdit = (row: Record<string, unknown>) => {
    setEditRow(row)
    setForm(Object.fromEntries(Object.entries(row).map(([k, v]) => [k, v != null ? String(v) : ''])))
    setError(null)
  }
  const openNew = () => { setEditRow({}); setForm({ code: '', name: '', symbol: '' }); setError(null) }

  const handleSave = async () => {
    setSaving(true); setError(null)
    try {
      await upsertCurrency({ id: editRow?.id ?? undefined, ...form })
      qc.invalidateQueries({ queryKey: ['currencies-master'] })
      setEditRow(null)
    } catch (e: unknown) { setError(e instanceof Error ? e.message : 'Save failed') }
    finally { setSaving(false) }
  }

  const handleDelete = async (id: number) => {
    if (!confirm('Delete this currency?')) return
    setDeleteError(null)
    try {
      await deleteRow('currencies', id)
      qc.invalidateQueries({ queryKey: ['currencies-master'] })
    } catch (e) { setDeleteError(extractError(e)) }
  }

  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>

  return (
    <div>
      <div className="flex items-center gap-2 px-4 py-2 border-b border-slate-100 bg-slate-50">
        <Button size="sm" variant="secondary" onClick={openNew}><Plus size={13} /> Add Currency</Button>
        {deleteError && <span className="text-xs text-red-600 bg-red-50 rounded px-3 py-1">{deleteError}</span>}
        <span className="ml-auto text-xs text-slate-400">{filtered.length} currencies · double-click to edit</span>
      </div>
      <div className="ag-theme-alpine" style={{ height: '560px', width: '100%' }}>
        <AgGridReact
          rowData={filtered}
          onRowClicked={(e: RowClickedEvent) => { if ((e.event as MouseEvent)?.detail === 2) openEdit(e.data as Record<string, unknown>) }}
          columnDefs={[
            { field: 'id', headerName: 'ID', width: 70 },
            { field: 'code', headerName: 'Code', width: 90 },
            { field: 'name', headerName: 'Name', flex: 2, minWidth: 160 },
            { field: 'symbol', headerName: 'Symbol', width: 80 },
            { field: 'latest_rate', headerName: 'Rate vs EUR', width: 130, type: 'numericColumn', valueFormatter: p => p.value != null ? Number(p.value).toFixed(4) : '—' },
            { field: 'rate_date', headerName: 'Rate Date', width: 110, valueFormatter: p => p.value?.slice(0, 10) ?? '—' },
            {
              headerName: '', width: 80, sortable: false, filter: false,
              cellRenderer: (p: { data: Record<string, unknown> }) => (
                <div className="flex gap-1 items-center h-full">
                  <button onClick={() => openEdit(p.data)} className="text-blue-500 hover:text-blue-700 p-1"><Pencil size={13} /></button>
                  <button onClick={() => handleDelete(Number(p.data.id))} className="text-red-400 hover:text-red-600 p-1"><Trash2 size={13} /></button>
                </div>
              ),
            },
          ]}
          defaultColDef={{ resizable: true, sortable: true, filter: true }}
        />
      </div>
      {editRow !== null && (
        <Modal title={form.id ? 'Edit Currency' : 'New Currency'} onClose={() => setEditRow(null)}
          footer={<>
            {form.id && <Button variant="destructive" onClick={() => { setEditRow(null); handleDelete(Number(form.id)) }} disabled={saving}><Trash2 size={14} /> Delete</Button>}
            <span className="flex-1" />
            <Button variant="secondary" onClick={() => setEditRow(null)}>Cancel</Button>
            <Button onClick={handleSave} disabled={saving || !form.code?.trim() || !form.name?.trim()}><Save size={14} /> {saving ? 'Saving…' : 'Save'}</Button>
          </>}>
          <Field label="Code *"><Input value={form.code ?? ''} onChange={e => set('code', e.target.value)} placeholder="EUR" /></Field>
          <Field label="Name *"><Input value={form.name ?? ''} onChange={e => set('name', e.target.value)} placeholder="Euro" /></Field>
          <Field label="Symbol"><Input value={form.symbol ?? ''} onChange={e => set('symbol', e.target.value)} placeholder="€" /></Field>
          {error && <p className="text-xs text-red-600 bg-red-50 rounded px-3 py-2">{error}</p>}
        </Modal>
      )}
    </div>
  )
}

// ── Securities Tab ────────────────────────────────────────────────────────────
function SecuritiesTab({ search }: { search: string }) {
  const qc = useQueryClient()
  const [editRow, setEditRow] = useState<Record<string, unknown> | null>(null)
  const [form, setForm] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)

  const { data = [], isLoading } = useQuery({ queryKey: ['securities-master'], queryFn: () => getSecuritiesMaster() })
  const { data: currencies = [] } = useQuery({ queryKey: ['currencies-master'], queryFn: () => getCurrenciesMaster() })

  const filtered = search
    ? (data as Record<string, unknown>[]).filter(r => Object.values(r).some(v => String(v ?? '').toLowerCase().includes(search.toLowerCase())))
    : data as Record<string, unknown>[]

  const set = (k: string, v: string) => setForm(f => ({ ...f, [k]: v }))

  const openEdit = (row: Record<string, unknown>) => {
    setEditRow(row)
    setForm(Object.fromEntries(Object.entries(row).map(([k, v]) => [k, v != null ? String(v) : ''])))
    setError(null)
  }
  const openNew = () => { setEditRow({}); setForm({ ticker: '', name: '', type: 'Stock', instrument_type: '' }); setError(null) }

  const handleSave = async () => {
    setSaving(true); setError(null)
    try {
      await upsertSecurity({ id: editRow?.id ?? undefined, ...form })
      qc.invalidateQueries({ queryKey: ['securities-master'] })
      setEditRow(null)
    } catch (e: unknown) { setError(e instanceof Error ? e.message : 'Save failed') }
    finally { setSaving(false) }
  }

  const handleDelete = async (id: number) => {
    if (!confirm('Delete this security? This cannot be undone.')) return
    setDeleteError(null)
    try {
      await deleteRow('securities', id)
      qc.invalidateQueries({ queryKey: ['securities-master'] })
    } catch (e) { setDeleteError(extractError(e)) }
  }

  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>

  return (
    <div>
      <div className="flex items-center gap-2 px-4 py-2 border-b border-slate-100 bg-slate-50">
        <Button size="sm" variant="secondary" onClick={openNew}><Plus size={13} /> Add Security</Button>
        {deleteError && <span className="text-xs text-red-600 bg-red-50 rounded px-3 py-1">{deleteError}</span>}
        <span className="ml-auto text-xs text-slate-400">{filtered.length} securities · double-click to edit</span>
      </div>
      <div className="ag-theme-alpine" style={{ height: '560px', width: '100%' }}>
        <AgGridReact
          rowData={filtered}
          onRowClicked={(e: RowClickedEvent) => { if ((e.event as MouseEvent)?.detail === 2) openEdit(e.data as Record<string, unknown>) }}
          columnDefs={[
            { field: 'id', headerName: 'ID', width: 70 },
            { field: 'ticker', headerName: 'Ticker', width: 100, cellStyle: { fontFamily: 'monospace', fontWeight: 600 } },
            { field: 'name', headerName: 'Name', flex: 2, minWidth: 180 },
            { field: 'type', headerName: 'Type', width: 130 },
            { field: 'instrument_type', headerName: 'Instrument', width: 120 },
            { field: 'currency', headerName: 'Currency', width: 90 },
            { field: 'latest_price', headerName: 'Last Price', width: 110, type: 'numericColumn', valueFormatter: p => p.value != null ? fmtNum(Number(p.value), 4) : '—' },
            { field: 'price_date', headerName: 'Price Date', width: 110, valueFormatter: p => p.value?.slice(0, 10) ?? '—' },
            { field: 'held_in_accounts', headerName: 'Held In', width: 80, type: 'numericColumn' },
            {
              headerName: '', width: 80, sortable: false, filter: false,
              cellRenderer: (p: { data: Record<string, unknown> }) => (
                <div className="flex gap-1 items-center h-full">
                  <button onClick={() => openEdit(p.data)} className="text-blue-500 hover:text-blue-700 p-1"><Pencil size={13} /></button>
                  <button onClick={() => handleDelete(Number(p.data.id))} className="text-red-400 hover:text-red-600 p-1"><Trash2 size={13} /></button>
                </div>
              ),
            },
          ]}
          defaultColDef={{ resizable: true, sortable: true, filter: true }}
        />
      </div>
      {editRow !== null && (
        <Modal title={form.id ? 'Edit Security' : 'New Security'} onClose={() => setEditRow(null)}
          footer={<>
            {form.id && <Button variant="destructive" onClick={() => { setEditRow(null); handleDelete(Number(form.id)) }} disabled={saving}><Trash2 size={14} /> Delete</Button>}
            <span className="flex-1" />
            <Button variant="secondary" onClick={() => setEditRow(null)}>Cancel</Button>
            <Button onClick={handleSave} disabled={saving || !form.ticker?.trim() || !form.name?.trim()}><Save size={14} /> {saving ? 'Saving…' : 'Save'}</Button>
          </>}>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Ticker *"><Input value={form.ticker ?? ''} onChange={e => set('ticker', e.target.value)} placeholder="AAPL" /></Field>
            <Field label="Type *">
              <select className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm" value={form.type ?? 'Stock'} onChange={e => set('type', e.target.value)}>
                {SECURITY_TYPES.map(t => <option key={t}>{t}</option>)}
              </select>
            </Field>
            <div className="col-span-2">
              <Field label="Name *"><Input value={form.name ?? ''} onChange={e => set('name', e.target.value)} placeholder="Apple Inc." /></Field>
            </div>
            <Field label="Instrument Type"><Input value={form.instrument_type ?? ''} onChange={e => set('instrument_type', e.target.value)} placeholder="Common Stock" /></Field>
            <Field label="Currency">
              <select className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm" value={form.currencies_id ?? ''} onChange={e => set('currencies_id', e.target.value)}>
                <option value="">— select —</option>
                {(currencies as Record<string, unknown>[]).map(c => <option key={String(c.id)} value={String(c.id)}>{String(c.code)} – {String(c.name)}</option>)}
              </select>
            </Field>
            <Field label="ISIN"><Input value={form.isin ?? ''} onChange={e => set('isin', e.target.value)} placeholder="US0378331005" /></Field>
            <Field label="Exchange"><Input value={form.exchange ?? ''} onChange={e => set('exchange', e.target.value)} placeholder="NASDAQ" /></Field>
          </div>
          {error && <p className="text-xs text-red-600 bg-red-50 rounded px-3 py-2">{error}</p>}
        </Modal>
      )}
    </div>
  )
}

// ── Tax Rules Tab ─────────────────────────────────────────────────────────────
type TaxRule = Record<string, unknown>

function TaxRuleField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-xs font-medium text-slate-500 mb-1">{label}</label>
      {children}
    </div>
  )
}

const EMPTY_TAX_RULE: TaxRule = {
  tax_category: '', display_name: '',
  gains_taxable: null, gains_rate: null, gains_tax_code: null,
  dividend_local_tax_rate: null, dividend_wht_creditable: null,
  reinvest_taxable: null, income_tax_rate: null, show_in_capital_gains: true, notes: null,
}

function TaxRulesTab() {
  const qc = useQueryClient()
  const { data: rules = [], isLoading } = useQuery<TaxRule[]>({
    queryKey: ['tax-category-rules'], queryFn: getTaxCategoryRules,
  })
  const [editKey, setEditKey] = useState<string | null>(null)
  const [isNew, setIsNew] = useState(false)
  const [form, setForm] = useState<TaxRule>({})
  const [saveMsg, setSaveMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const set = (k: string, v: unknown) => setForm(f => ({ ...f, [k]: v }))

  const openEdit = (r: TaxRule) => {
    setIsNew(false)
    setEditKey(String(r.tax_category))
    setForm({ ...r })
    setSaveMsg(null)
  }

  const openNew = () => {
    setIsNew(true)
    setEditKey('__new__')
    setForm({ ...EMPTY_TAX_RULE })
    setSaveMsg(null)
  }

  const saveMut = useMutation({
    mutationFn: () => isNew
      ? createTaxCategoryRule(form)
      : updateTaxCategoryRule(String(editKey), form),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['tax-category-rules'] })
      setSaveMsg({ ok: true, text: 'Saved.' })
      if (isNew) { setIsNew(false); setEditKey(String(form.tax_category)) }
    },
    onError: (e: Error) => setSaveMsg({ ok: false, text: e.message }),
  })

  const BoolCell = ({ val }: { val: unknown }) =>
    val === true ? <span className="text-green-600 font-medium">Yes</span>
    : val === false ? <span className="text-slate-400">No</span>
    : <span className="text-slate-300">—</span>

  const PctCell = ({ val }: { val: unknown }) =>
    val != null ? <span>{Number(val).toFixed(2)}%</span> : <span className="text-slate-300">—</span>

  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>

  return (
    <div className="p-4 space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-xs text-slate-500">
          Investment tax rates applied per security category in reports and corporate actions.
          Changes take effect immediately across all Investment Tax reports.
        </p>
        <Button size="sm" onClick={openNew}><Plus size={13} /> Add Category</Button>
      </div>

      <div className="border border-slate-200 rounded-lg overflow-hidden text-xs">
        <table className="w-full border-collapse">
          <thead>
            <tr className="bg-slate-50 text-slate-500 uppercase tracking-wide">
              <th className="text-left px-3 py-2 border-b border-slate-200">Category</th>
              <th className="text-center px-3 py-2 border-b border-slate-200">Gains Taxable</th>
              <th className="text-right px-3 py-2 border-b border-slate-200">Gains Rate</th>
              <th className="text-left px-3 py-2 border-b border-slate-200">Tax Code</th>
              <th className="text-right px-3 py-2 border-b border-slate-200">Div. Local Tax</th>
              <th className="text-center px-3 py-2 border-b border-slate-200">WHT Credit</th>
              <th className="text-center px-3 py-2 border-b border-slate-200">Reinvest Taxable</th>
              <th className="text-right px-3 py-2 border-b border-slate-200">Income Tax</th>
              <th className="text-center px-3 py-2 border-b border-slate-200">Show Cap. Gains</th>
              <th className="px-3 py-2 border-b border-slate-200 w-16"></th>
            </tr>
          </thead>
          <tbody>
            {(rules as TaxRule[]).map(r => (
              <tr key={String(r.tax_category)} className="border-b border-slate-100 hover:bg-slate-50">
                <td className="px-3 py-2 font-medium">
                  <div>{String(r.display_name)}</div>
                  <div className="text-slate-400 font-normal">{String(r.tax_category)}</div>
                </td>
                <td className="px-3 py-2 text-center"><BoolCell val={r.gains_taxable} /></td>
                <td className="px-3 py-2 text-right"><PctCell val={r.gains_rate} /></td>
                <td className="px-3 py-2 text-slate-500">{r.gains_tax_code ? String(r.gains_tax_code) : '—'}</td>
                <td className="px-3 py-2 text-right"><PctCell val={r.dividend_local_tax_rate} /></td>
                <td className="px-3 py-2 text-center"><BoolCell val={r.dividend_wht_creditable} /></td>
                <td className="px-3 py-2 text-center"><BoolCell val={r.reinvest_taxable} /></td>
                <td className="px-3 py-2 text-right"><PctCell val={r.income_tax_rate} /></td>
                <td className="px-3 py-2 text-center"><BoolCell val={r.show_in_capital_gains ?? true} /></td>
                <td className="px-3 py-2">
                  <button onClick={() => openEdit(r)} className="text-blue-600 hover:underline flex items-center gap-1">
                    <Pencil size={11} /> Edit
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {editKey && (
        <div className="border border-blue-200 rounded-lg p-4 bg-blue-50 space-y-3">
          <p className="text-sm font-semibold text-slate-700">
            {isNew ? 'New Tax Category' : <>Edit: <span className="text-blue-600">{String(form.display_name)}</span><span className="text-slate-400 font-normal ml-2 text-xs">({editKey})</span></>}
          </p>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {isNew && (
              <div className="col-span-2">
                <TaxRuleField label="Category Key (no spaces, e.g. My Category)">
                  <input className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm font-mono"
                    value={String(form.tax_category ?? '')} onChange={e => set('tax_category', e.target.value)}
                    placeholder="e.g. REITs" />
                </TaxRuleField>
              </div>
            )}
            <div className="col-span-2">
              <TaxRuleField label="Display Name">
                <input className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm"
                  value={String(form.display_name ?? '')} onChange={e => set('display_name', e.target.value)} />
              </TaxRuleField>
            </div>
            <TaxRuleField label="Gains Taxable">
              <select className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm"
                value={form.gains_taxable === true ? 'true' : form.gains_taxable === false ? 'false' : ''}
                onChange={e => set('gains_taxable', e.target.value === '' ? null : e.target.value === 'true')}>
                <option value="">— n/a —</option>
                <option value="true">Yes</option>
                <option value="false">No</option>
              </select>
            </TaxRuleField>
            <TaxRuleField label="Gains Rate (%)">
              <input type="number" step="0.01" className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm"
                value={form.gains_rate != null ? String(form.gains_rate) : ''}
                onChange={e => set('gains_rate', e.target.value === '' ? null : e.target.value)}
                placeholder="—" />
            </TaxRuleField>
            <TaxRuleField label="Gains Tax Code">
              <input className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm"
                value={String(form.gains_tax_code ?? '')} onChange={e => set('gains_tax_code', e.target.value)}
                placeholder="e.g. 659-660" />
            </TaxRuleField>
            <TaxRuleField label="Dividend Local Tax (%)">
              <input type="number" step="0.01" className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm"
                value={form.dividend_local_tax_rate != null ? String(form.dividend_local_tax_rate) : ''}
                onChange={e => set('dividend_local_tax_rate', e.target.value === '' ? null : e.target.value)}
                placeholder="—" />
            </TaxRuleField>
            <TaxRuleField label="WHT Creditable">
              <select className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm"
                value={form.dividend_wht_creditable === true ? 'true' : form.dividend_wht_creditable === false ? 'false' : ''}
                onChange={e => set('dividend_wht_creditable', e.target.value === '' ? null : e.target.value === 'true')}>
                <option value="">— n/a —</option>
                <option value="true">Yes</option>
                <option value="false">No</option>
              </select>
            </TaxRuleField>
            <TaxRuleField label="Reinvest Taxable">
              <select className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm"
                value={form.reinvest_taxable === true ? 'true' : form.reinvest_taxable === false ? 'false' : ''}
                onChange={e => set('reinvest_taxable', e.target.value === '' ? null : e.target.value === 'true')}>
                <option value="">— n/a —</option>
                <option value="true">Yes</option>
                <option value="false">No</option>
              </select>
            </TaxRuleField>
            <TaxRuleField label="Income Tax Rate (%)">
              <input type="number" step="0.01" className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm"
                value={form.income_tax_rate != null ? String(form.income_tax_rate) : ''}
                onChange={e => set('income_tax_rate', e.target.value === '' ? null : e.target.value)}
                placeholder="—" />
            </TaxRuleField>
            <TaxRuleField label="Show in Capital Gains">
              <select className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm"
                value={(form.show_in_capital_gains ?? true) ? 'true' : 'false'}
                onChange={e => set('show_in_capital_gains', e.target.value === 'true')}>
                <option value="true">Yes</option>
                <option value="false">No (e.g. CDs — principal return, not a gain event)</option>
              </select>
            </TaxRuleField>
            <div className="col-span-2 md:col-span-4">
              <TaxRuleField label="Notes">
                <textarea className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm" rows={2}
                  value={String(form.notes ?? '')} onChange={e => set('notes', e.target.value)} />
              </TaxRuleField>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <Button size="sm" disabled={saveMut.isPending} onClick={() => saveMut.mutate()}>
              <Save size={13} /> {saveMut.isPending ? 'Saving…' : 'Save'}
            </Button>
            <Button size="sm" variant="secondary" onClick={() => { setEditKey(null); setSaveMsg(null) }}>
              <X size={13} /> Cancel
            </Button>
            {saveMsg && (
              <span className={`text-xs ${saveMsg.ok ? 'text-green-600' : 'text-red-600'}`}>{saveMsg.text}</span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}


// ── Instrument Tax Override Tab ───────────────────────────────────────────────
type InstrumentOverride = { instrument_type: string; tax_category_override: string | null; notes: string | null }

function InstrumentTaxTab() {
  const qc = useQueryClient()
  const { data: overrides = [], isLoading } = useQuery<InstrumentOverride[]>({
    queryKey: ['instrument-type-overrides'], queryFn: getInstrumentTypeOverrides,
  })
  const { data: taxRules = [] } = useQuery<TaxRule[]>({
    queryKey: ['tax-category-rules'], queryFn: getTaxCategoryRules,
  })
  const [editKey, setEditKey] = useState<string | null>(null)
  const [isNew, setIsNew] = useState(false)
  const [form, setForm] = useState<InstrumentOverride>({ instrument_type: '', tax_category_override: null, notes: null })
  const [saveMsg, setSaveMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const openEdit = (r: InstrumentOverride) => {
    setIsNew(false)
    setEditKey(r.instrument_type)
    setForm({ ...r })
    setSaveMsg(null)
  }

  const openNew = () => {
    setIsNew(true)
    setEditKey('__new__')
    setForm({ instrument_type: '', tax_category_override: null, notes: null })
    setSaveMsg(null)
  }

  const saveMut = useMutation({
    mutationFn: () => isNew
      ? createInstrumentTypeOverride(form as Record<string, unknown>)
      : updateInstrumentTypeOverride(String(editKey), form as Record<string, unknown>),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['instrument-type-overrides'] })
      setSaveMsg({ ok: true, text: 'Saved.' })
      if (isNew) { setIsNew(false); setEditKey(form.instrument_type) }
    },
    onError: (e: Error) => setSaveMsg({ ok: false, text: e.message }),
  })

  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>

  return (
    <div className="p-4 space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-xs text-slate-500">
          When an investment's Instrument Type has an override set here, it takes precedence over the
          underlying security's Tax Category in all reports. Leave override blank to use the security's category.
        </p>
        <Button size="sm" onClick={openNew}><Plus size={13} /> Add Override</Button>
      </div>

      <div className="border border-slate-200 rounded-lg overflow-hidden text-xs">
        <table className="w-full border-collapse">
          <thead>
            <tr className="bg-slate-50 text-slate-500 uppercase tracking-wide">
              <th className="text-left px-3 py-2 border-b border-slate-200">Instrument Type</th>
              <th className="text-left px-3 py-2 border-b border-slate-200">Effective Tax Category</th>
              <th className="text-left px-3 py-2 border-b border-slate-200">Notes</th>
              <th className="px-3 py-2 border-b border-slate-200 w-16"></th>
            </tr>
          </thead>
          <tbody>
            {(overrides as InstrumentOverride[]).map(r => (
              <tr key={r.instrument_type} className="border-b border-slate-100 hover:bg-slate-50">
                <td className="px-3 py-2 font-medium font-mono">{r.instrument_type}</td>
                <td className="px-3 py-2">
                  {r.tax_category_override
                    ? <span className="inline-flex items-center px-2 py-0.5 rounded-full bg-blue-100 text-blue-700 font-medium">{r.tax_category_override}</span>
                    : <span className="text-slate-400 italic">use security's category</span>}
                </td>
                <td className="px-3 py-2 text-slate-500 max-w-xs truncate">{r.notes ?? '—'}</td>
                <td className="px-3 py-2">
                  <button onClick={() => openEdit(r)} className="text-blue-600 hover:underline flex items-center gap-1">
                    <Pencil size={11} /> Edit
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {editKey && (
        <div className="border border-blue-200 rounded-lg p-4 bg-blue-50 space-y-3">
          <p className="text-sm font-semibold text-slate-700">
            {isNew ? 'New Instrument Type Override' : <>Edit: <span className="font-mono text-blue-600">{editKey}</span></>}
          </p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {isNew && (
              <div>
                <label className="block text-xs font-medium text-slate-500 mb-1">Instrument Type</label>
                <input className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm font-mono"
                  value={form.instrument_type}
                  onChange={e => setForm(f => ({ ...f, instrument_type: e.target.value }))}
                  placeholder="e.g. CFDOnCrypto" />
              </div>
            )}
            <div>
              <label className="block text-xs font-medium text-slate-500 mb-1">Tax Category Override</label>
              <select className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm"
                value={form.tax_category_override ?? ''}
                onChange={e => setForm(f => ({ ...f, tax_category_override: e.target.value || null }))}>
                <option value="">— use security's Tax Category —</option>
                {(taxRules as TaxRule[]).map(r => (
                  <option key={String(r.tax_category)} value={String(r.tax_category)}>
                    {String(r.display_name)}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-500 mb-1">Notes</label>
              <input className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm"
                value={form.notes ?? ''}
                onChange={e => setForm(f => ({ ...f, notes: e.target.value || null }))} />
            </div>
          </div>
          <div className="flex items-center gap-3">
            <Button size="sm" disabled={saveMut.isPending} onClick={() => saveMut.mutate()}>
              <Save size={13} /> {saveMut.isPending ? 'Saving…' : 'Save'}
            </Button>
            <Button size="sm" variant="secondary" onClick={() => { setEditKey(null); setSaveMsg(null) }}>
              <X size={13} /> Cancel
            </Button>
            {saveMsg && (
              <span className={`text-xs ${saveMsg.ok ? 'text-green-600' : 'text-red-600'}`}>{saveMsg.text}</span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}


// ── Main page ─────────────────────────────────────────────────────────────────
export default function StaticData() {
  const [tab, setTab] = usePersist('static_data_tab', 'Payees')
  const [search, setSearch] = useState('')

  return (
    <div>
      <PageHeader title="Static Data" subtitle="Master reference data" />
      <div className="px-6 py-4 space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
          <div className="flex gap-1 border-b border-slate-200 overflow-x-auto">
            {TABS.map(t => (
              <button key={t} onClick={() => setTab(t)}
                className={`px-4 py-2 text-sm font-medium -mb-px border-b-2 transition-colors whitespace-nowrap ${tab === t ? 'border-blue-600 text-blue-600' : 'border-transparent text-slate-500 hover:text-slate-700'}`}>
                {t}
              </button>
            ))}
          </div>
          <div className="relative sm:ml-4 shrink-0">
            <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
            <Input className="pl-8 w-full sm:w-52" placeholder="Filter…" value={search} onChange={e => setSearch(e.target.value)} />
          </div>
        </div>

        <Card className="overflow-hidden">
          {tab === 'Payees'       && <PayeesTab search={search} />}
          {tab === 'Categories'   && <CategoriesTab search={search} />}
          {tab === 'Institutions' && <InstitutionsTab search={search} />}
          {tab === 'Accounts'     && <AccountsTab search={search} />}
          {tab === 'Tax Rules'      && <TaxRulesTab />}
          {tab === 'Instrument Tax' && <InstrumentTaxTab />}
        </Card>
      </div>
    </div>
  )
}
