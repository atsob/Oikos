import { useState, useRef, useEffect } from 'react'
import { usePersist } from '@/lib/hooks'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  PageHeader, Card, CardHeader, CardTitle, CardBody, Button, Select, Spinner, ColHeader, useSortTable,
} from '@/components/ui'
import { Upload, CheckCircle, XCircle, Trash2, Plus, Edit2, RefreshCw, ChevronDown, ChevronUp } from 'lucide-react'
import { fmtNum } from '@/lib/utils'
import {
  getBankAccounts, getAllAccounts, getImportProfiles, createImportProfile, deleteImportProfile,
  getPayeeRules, createPayeeRule, updatePayeeRule, deletePayeeRule,
  getBankPayees as getPayees, getBankCategories as getCategories, getPayeeCategoryUsage,
  parseStatement, getAppTransactions, applyBankImport,
  getReconciliationHistoryAccounts, getReconciliationHistory, ibFlexFetch, ibFlexParse, ibFlexImport, saveSecurityMappings,
  revtParse, revtImport, revsParse, revsImport, importFile,
  getImporterSettings, saveImporterSettings, getLinkedAccount,
  saxoGetSettings, saxoSaveAccountMap, saxoSaveChargePayee, saxoGetAuthUrl, saxoExchangeCode, saxoRefreshToken,
  saxoFetchAccounts, saxoFetchTrades, saxoImport,
  saxoPdfPreview, saxoPdfImport,
  coinbaseGetSettings, coinbaseTest, coinbaseFetch, coinbaseImport,
  getSecurities,
} from '@/lib/api'

// ── Helpers ───────────────────────────────────────────────────────────────────

const fmt2 = (n: number) => fmtNum(n, 2)

/** Load saved importer settings and return a save function. Settings are applied via the returned `apply` callback. */
function useImporterSettings(key: string, apply: (s: Record<string, unknown>) => void) {
  const q = useQuery({ queryKey: ['importer-settings', key], queryFn: () => getImporterSettings(key), staleTime: Infinity })
  useEffect(() => { if (q.data && Object.keys(q.data).length > 0) apply(q.data) }, [q.data])
  const save = (data: Record<string, unknown>) => saveImporterSettings(key, data).catch(() => {})
  return save
}

function FileDropZone({ accept, onChange, label }: { accept: string; onChange: (f: File) => void; label?: string }) {
  const ref = useRef<HTMLInputElement>(null)
  return (
    <div
      className="border-2 border-dashed border-slate-300 rounded-lg p-6 text-center cursor-pointer hover:border-blue-400 transition-colors"
      onClick={() => ref.current?.click()}
    >
      <Upload size={20} className="mx-auto text-slate-400 mb-1" />
      <p className="text-sm text-slate-500">{label ?? 'Click to select file'}</p>
      <input ref={ref} type="file" className="hidden" accept={accept}
        onChange={e => { const f = e.target.files?.[0]; if (f) onChange(f) }} />
    </div>
  )
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    exists: 'bg-green-100 text-green-700',
    likely_dup: 'bg-yellow-100 text-yellow-700',
    new: 'bg-blue-100 text-blue-700',
    ignored: 'bg-gray-100 text-gray-500',
  }
  const label: Record<string, string> = {
    exists: '✅ Exists', likely_dup: '⚠️ Likely Dup', new: '🆕 New', ignored: '⏭️ Ignored',
  }
  return <span className={`px-2 py-0.5 rounded text-xs font-medium ${map[status] ?? ''}`}>{label[status] ?? status}</span>
}

function SubTabs({ tabs, active, onChange }: { tabs: string[]; active: string; onChange: (t: string) => void }) {
  return (
    <div className="flex gap-1 border-b border-slate-200 mb-4 flex-wrap">
      {tabs.map(t => (
        <button key={t} onClick={() => onChange(t)}
          className={`px-3 py-1.5 text-sm font-medium rounded-t border-b-2 transition-colors ${active === t
            ? 'border-blue-500 text-blue-700 bg-blue-50'
            : 'border-transparent text-slate-500 hover:text-slate-700'}`}
        >{t}</button>
      ))}
    </div>
  )
}

function InfoBox({ children }: { children: React.ReactNode }) {
  return <div className="bg-blue-50 text-blue-800 rounded-lg p-3 text-sm mb-4">{children}</div>
}

function ErrorBox({ msg }: { msg: string }) {
  return <div className="bg-red-50 text-red-700 rounded-lg p-3 text-sm flex gap-2 whitespace-pre-line"><XCircle size={16} className="shrink-0 mt-0.5" />{msg}</div>
}

// Prefer the backend's actual error detail (FastAPI's HTTPException body) over
// axios's generic "Request failed with status code NNN" message.
function apiErrorMsg(err: unknown): string {
  if (!err) return ''
  const axiosErr = err as { response?: { data?: { detail?: string } }; message?: string }
  return axiosErr.response?.data?.detail || axiosErr.message || 'Unknown error'
}

function SuccessBox({ msg }: { msg: string }) {
  return <div className="bg-green-50 text-green-700 rounded-lg p-3 text-sm flex gap-2"><CheckCircle size={16} className="shrink-0 mt-0.5" />{msg}</div>
}

// ── Bank → Import & Reconcile ─────────────────────────────────────────────────

function ImportReconcileTab() {
  const [step, setStep] = useState(1)
  const [accountId, setAccountId] = useState<number | null>(null)
  const [profileId, setProfileId] = useState<number | null>(null)
  const [file, setFile] = useState<File | null>(null)
  // profilePerAccount: {account_id: profile_id} — last profile used per account
  const [profilePerAccount, setProfilePerAccount] = useState<Record<number, number>>({})
  const saveSettings = useImporterSettings('bank_import', s => {
    if (s.account_id) setAccountId(s.account_id as number)
    if (s.profile_per_account) setProfilePerAccount(s.profile_per_account as Record<number, number>)
  })

  // When account changes, restore last-used profile for that account
  useEffect(() => {
    if (accountId && profilePerAccount[accountId]) setProfileId(profilePerAccount[accountId])
    else setProfileId(null)
  }, [accountId])
  const [parsedRows, setParsedRows] = useState<Record<string, unknown>[]>([])
  const [appTxns, setAppTxns] = useState<Record<string, unknown>[]>([])
  const [reviewRows, setReviewRows] = useState<Record<string, unknown>[]>([])
  const [payeeAssign, setPayeeAssign] = useState<Record<number, { payee_name: string; category_id: number | null }>>({})
  const [notes, setNotes] = useState('')
  const [result, setResult] = useState<Record<string, unknown> | null>(null)

  const { data: accounts = [] } = useQuery({ queryKey: ['bank-accounts'], queryFn: getBankAccounts })
  const { data: profiles = [] } = useQuery({ queryKey: ['import-profiles'], queryFn: getImportProfiles })
  const { data: payees = [] } = useQuery({ queryKey: ['payees'], queryFn: getPayees })
  const { data: categories = [] } = useQuery({ queryKey: ['categories'], queryFn: getCategories })
  const { data: payeeRules = [] } = useQuery({ queryKey: ['payee-rules'], queryFn: getPayeeRules })
  const { data: payeeCatUsage = [] } = useQuery({ queryKey: ['payee-category-usage'], queryFn: getPayeeCategoryUsage })

  // Apply payee rules to a description — mirrors backend apply_payee_rules logic
  function applyRules(description: string): { payee_name: string; category_id: number | null } {
    const descUp = description.toUpperCase()
    const rules = payeeRules as Record<string, unknown>[]
    for (const rule of rules) {
      const pat = String(rule.pattern ?? '').toUpperCase()
      const mtyp = String(rule.match_type ?? 'contains').toLowerCase()
      let hit = false
      if      (mtyp === 'contains')    hit = descUp.includes(pat)
      else if (mtyp === 'starts_with') hit = descUp.startsWith(pat)
      else if (mtyp === 'exact')       hit = descUp === pat
      else if (mtyp === 'regex') {
        try { hit = new RegExp(String(rule.pattern), 'i').test(description) } catch { hit = false }
      }
      if (hit) {
        return {
          payee_name: (rule.payee_name as string) ?? '',
          category_id: rule.categories_id != null ? Number(rule.categories_id) : null,
        }
      }
    }
    return { payee_name: '', category_id: null }
  }

  const parseMut = useMutation({
    mutationFn: () => parseStatement(profileId!, file!),
    onSuccess: async (data) => {
      // Save account + profile mapping so next visit restores both
      if (accountId && profileId) {
        const newMap = { ...profilePerAccount, [accountId]: profileId }
        setProfilePerAccount(newMap)
        saveSettings({ account_id: accountId, profile_per_account: newMap })
      }
      const rows: Record<string, unknown>[] = data.rows ?? []
      if (!rows.length) return
      setParsedRows(rows)

      const dates = rows.map(r => r.date as string).sort()
      const appData = await getAppTransactions(accountId!, dates[0], dates[dates.length - 1])
      setAppTxns(appData)

      // Simple matching: exact date + amount
      const initialPayeeAssign: Record<number, { payee_name: string; category_id: number | null }> = {}
      const matched = rows.map((r, i) => {
        const amount = Number(r.amount)
        const exact = (appData as Record<string, unknown>[]).find(a =>
          a.date === r.date && Math.abs(Number(a.amount) - amount) < 0.02
        )
        const fuzzy = !exact && (appData as Record<string, unknown>[]).find(a =>
          Math.abs(Number(a.amount) - amount) < 0.02
        )
        const status = exact ? 'matched' : fuzzy ? 'possible_dup' : 'new'
        const defaultAction = exact ? 'Reconcile' : fuzzy ? 'Skip' : 'Import'
        if (defaultAction === 'Import') {
          initialPayeeAssign[i] = applyRules(String(r.description ?? ''))
        }
        return {
          ...r,
          _idx: i,
          status,
          match_tx_id: exact ? exact.id : fuzzy ? fuzzy.id : null,
          already_reconciled: (exact ?? fuzzy)?.reconciled ?? false,
          action: defaultAction,
        }
      })
      setPayeeAssign(initialPayeeAssign)
      setReviewRows(matched)
      setStep(2)
    },
  })

  const applyMut = useMutation({
    mutationFn: () => {
      const acc = (accounts as Record<string, unknown>[]).find(a => a.id === accountId)
      return applyBankImport({
        account_id: accountId,
        stmt_date: parsedRows.length ? parsedRows[parsedRows.length - 1].date : null,
        stmt_balance: null,
        app_balance: acc ? Number(acc.balance) : null,
        notes,
        rows: reviewRows.map(r => ({
          date: r.date,
          description: r.description,
          amount: r.amount,
          action: r.action,
          match_tx_id: r.match_tx_id,
          already_reconciled: r.already_reconciled,
          payee_name: payeeAssign[r._idx as number]?.payee_name ?? '',
          category_id: payeeAssign[r._idx as number]?.category_id ?? null,
        })),
      })
    },
    onSuccess: (data) => { setResult(data); setStep(3) },
  })

  const updateRow = (idx: number, field: string, value: unknown) => {
    setReviewRows(prev => prev.map(r => r._idx === idx ? { ...r, [field]: value } : r))
  }

  const accObj = (accounts as Record<string, unknown>[]).find(a => a.id === accountId)
  const nMatched = reviewRows.filter(r => r.action === 'Reconcile').length
  const nImport = reviewRows.filter(r => r.action === 'Import').length

  return (
    <div className="space-y-6">
      <InfoBox>
        Import a CSV or Excel bank statement, match transactions already in the app, import new ones, and mark everything reconciled.
      </InfoBox>

      {/* Step 1: Account + Profile + File */}
      {step >= 1 && (
        <Card>
          <CardHeader><CardTitle>Step 1 — Account, Profile & File</CardTitle></CardHeader>
          <CardBody className="space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-medium text-slate-600 mb-1">Account</label>
                <Select value={accountId ?? ''} onChange={e => setAccountId(Number(e.target.value))}>
                  <option value="">— select account —</option>
                  {(accounts as Record<string, unknown>[]).map(a => (
                    <option key={a.id as number} value={a.id as number}>{a.name as string} ({a.type as string})</option>
                  ))}
                </Select>
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-600 mb-1">Import Profile</label>
                <Select value={profileId ?? ''} onChange={e => setProfileId(Number(e.target.value))}>
                  <option value="">— select profile —</option>
                  {(profiles as Record<string, unknown>[]).map(p => (
                    <option key={p.profile_id as number} value={p.profile_id as number}>{p.profile_name as string}</option>
                  ))}
                </Select>
              </div>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Statement File (CSV / XLSX)</label>
              {file ? (
                <div className="flex items-center gap-3 p-3 bg-slate-50 rounded-lg border border-slate-200">
                  <CheckCircle size={16} className="text-green-500" />
                  <span className="text-sm font-medium">{file.name}</span>
                  <span className="text-xs text-slate-400">{(file.size / 1024).toFixed(1)} KB</span>
                  <button className="ml-auto text-xs text-red-500 hover:underline" onClick={() => setFile(null)}>Remove</button>
                </div>
              ) : (
                <FileDropZone accept=".csv,.xlsx,.xls" onChange={setFile} label="Click to select CSV or Excel file" />
              )}
            </div>
            <Button
              disabled={!accountId || !profileId || !file || parseMut.isPending}
              onClick={() => parseMut.mutate()}
            >
              {parseMut.isPending ? <><Spinner size={14} /> Parsing…</> : <>Parse & Match</>}
            </Button>
            {parseMut.isError && <ErrorBox msg={apiErrorMsg(parseMut.error)} />}
          </CardBody>
        </Card>
      )}

      {/* Step 2: Review */}
      {step >= 2 && reviewRows.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Step 2 — Review & Assign</CardTitle>
            <div className="flex gap-4 text-sm text-slate-600 mt-1">
              <span className="text-green-600">✅ {reviewRows.filter(r => r.status === 'matched').length} matched</span>
              <span className="text-yellow-600">⚠️ {reviewRows.filter(r => r.status === 'possible_dup').length} possible dup</span>
              <span className="text-blue-600">🆕 {reviewRows.filter(r => r.status === 'new').length} new</span>
            </div>
          </CardHeader>
          <CardBody>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-slate-200 text-slate-500">
                    <th className="py-1 px-2 text-left">Date</th>
                    <th className="py-1 px-2 text-left">Description</th>
                    <th className="py-1 px-2 text-right">Amount</th>
                    <th className="py-1 px-2 text-left">Status</th>
                    <th className="py-1 px-2 text-left">Action</th>
                    <th className="py-1 px-2 text-left">Payee</th>
                    <th className="py-1 px-2 text-left">Category</th>
                  </tr>
                </thead>
                <tbody>
                  {reviewRows.map(r => (
                    <tr key={r._idx as number} className="border-b border-slate-100 hover:bg-slate-50">
                      <td className="py-1 px-2 whitespace-nowrap">{r.date as string}</td>
                      <td className="py-1 px-2 max-w-[200px] truncate" title={r.description as string}>{r.description as string}</td>
                      <td className={`py-1 px-2 text-right font-mono ${Number(r.amount) < 0 ? 'text-red-600' : 'text-green-600'}`}>
                        {fmt2(Number(r.amount))}
                      </td>
                      <td className="py-1 px-2"><StatusBadge status={r.status as string} /></td>
                      <td className="py-1 px-2">
                        <select
                          value={r.action as string}
                          onChange={e => updateRow(r._idx as number, 'action', e.target.value)}
                          className="text-xs border border-slate-200 rounded px-1 py-0.5"
                        >
                          {['Reconcile', 'Import', 'Skip'].map(a => <option key={a}>{a}</option>)}
                        </select>
                      </td>
                      <td className="py-1 px-2">
                        {r.action === 'Import' && (
                          <select
                            value={payeeAssign[r._idx as number]?.payee_name ?? ''}
                            onChange={e => setPayeeAssign(prev => ({
                              ...prev, [r._idx as number]: { ...prev[r._idx as number], payee_name: e.target.value }
                            }))}
                            className="text-xs border border-slate-200 rounded px-1 py-0.5 w-32"
                          >
                            <option value="">(none)</option>
                            {(payees as Record<string, unknown>[]).map(p => (
                              <option key={p.id as number} value={p.name as string}>{p.name as string}</option>
                            ))}
                          </select>
                        )}
                      </td>
                      <td className="py-1 px-2">
                        {r.action === 'Import' && (
                          <select
                            value={payeeAssign[r._idx as number]?.category_id ?? ''}
                            onChange={e => setPayeeAssign(prev => ({
                              ...prev, [r._idx as number]: { ...prev[r._idx as number], category_id: e.target.value ? Number(e.target.value) : null }
                            }))}
                            className="text-xs border border-slate-200 rounded px-1 py-0.5 w-40"
                          >
                            <option value="">(none)</option>
                            {(() => {
                              const cats = categories as Record<string, unknown>[]
                              const selectedPayee = payeeAssign[r._idx as number]?.payee_name ?? ''
                              // Find top categories for the selected payee, then fall back to overall usage
                              const usageRows = (payeeCatUsage as Record<string, unknown>[])
                                .filter(u => String(u.payee_name ?? '') === selectedPayee)
                              const topIds = usageRows.map(u => Number(u.category_id))
                              const top = topIds.length > 0
                                ? topIds.slice(0, 8).map(id => cats.find(c => Number(c.id) === id)).filter(Boolean) as Record<string, unknown>[]
                                : cats.filter(c => Number(c.usage_count ?? 0) > 0).slice(0, 8)
                              const topIdSet = new Set(top.map(c => Number(c.id)))
                              const rest = cats.filter(c => !topIdSet.has(Number(c.id)))
                              const topLabel = topIds.length > 0 ? `── ${selectedPayee} ──` : '── Most used ──'
                              return <>
                                {top.length > 0 && <option disabled>{topLabel}</option>}
                                {top.map(c => <option key={`top-${c.id}`} value={c.id as number}>{c.name as string}</option>)}
                                {top.length > 0 && rest.length > 0 && <option disabled>── All ──</option>}
                                {rest.map(c => <option key={c.id as number} value={c.id as number}>{c.name as string}</option>)}
                              </>
                            })()}
                          </select>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="mt-4 pt-4 border-t border-slate-100 space-y-3">
              <div className="text-sm text-slate-600">
                Ready to <strong>reconcile {nMatched}</strong> and <strong>import {nImport}</strong> transaction(s).
              </div>
              <input
                type="text" value={notes} onChange={e => setNotes(e.target.value)}
                placeholder="Session notes (optional, e.g. 'Alpha Bank Feb 2026')"
                className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm"
              />
              <Button onClick={() => applyMut.mutate()} disabled={applyMut.isPending}>
                {applyMut.isPending ? <><Spinner size={14} /> Applying…</> : <>✅ Apply & Reconcile</>}
              </Button>
              {applyMut.isError && <ErrorBox msg={apiErrorMsg(applyMut.error)} />}
            </div>
          </CardBody>
        </Card>
      )}

      {/* Step 3: Done */}
      {step === 3 && result && (
        <Card>
          <CardBody>
            <SuccessBox msg={`Done! Reconciled ${result.reconciled} and imported ${result.imported} transaction(s).${(result.errors as string[])?.length ? ` Errors: ${(result.errors as string[]).join(', ')}` : ''}`} />
            <Button variant="secondary" className="mt-3" onClick={() => { setStep(1); setFile(null); setParsedRows([]); setReviewRows([]); setResult(null) }}>
              Start New Import
            </Button>
          </CardBody>
        </Card>
      )}
    </div>
  )
}

// ── Bank → Import Profiles ────────────────────────────────────────────────────

const EMPTY_PROFILE = {
  profile_name: '', bank_name: '', file_type: 'xlsx', sign_convention: 'debit_credit',
  date_column: '', description_column: '', debit_column: '', credit_column: '',
  amount_column: '', balance_column: '', installment_column: '', secondary_date_column: '',
  date_format: '%d/%m/%Y', encoding: 'utf-8', skip_rows: 0,
  decimal_separator: ',', thousands_separator: '.', invert_amounts: false,
}

function ImportProfilesTab() {
  const qc = useQueryClient()
  const [editing, setEditing] = useState<Record<string, unknown>>(EMPTY_PROFILE)
  const { data: profiles = [], isLoading } = useQuery({ queryKey: ['import-profiles'], queryFn: getImportProfiles })
  const { sorted: ipSorted, sortKey: ipSK, sortDir: ipSD, toggleSort: ipSort } = useSortTable(profiles as Record<string, unknown>[], 'profile_name', 'asc')

  const saveMut = useMutation({
    mutationFn: (d: Record<string, unknown>) => createImportProfile(d),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['import-profiles'] }); setEditing(EMPTY_PROFILE) },
  })
  const delMut = useMutation({
    mutationFn: (id: number) => deleteImportProfile(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['import-profiles'] }),
  })

  const set = (field: string, value: unknown) => setEditing(prev => ({ ...prev, [field]: value }))

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader><CardTitle>Import Profiles ({(profiles as Record<string, unknown>[]).length})</CardTitle></CardHeader>
        <CardBody>
          {isLoading ? <Spinner /> : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead><tr className="border-b border-slate-200 text-slate-500">
                  <ColHeader label="Name" sortKey="profile_name" currentKey={ipSK} currentDir={ipSD} onSort={ipSort} className="py-1 px-2 text-slate-500" />
                  <ColHeader label="Bank" sortKey="bank_name" currentKey={ipSK} currentDir={ipSD} onSort={ipSort} className="py-1 px-2 text-slate-500" />
                  <ColHeader label="Type" sortKey="file_type" currentKey={ipSK} currentDir={ipSD} onSort={ipSort} className="py-1 px-2 text-slate-500" />
                  <ColHeader label="Convention" sortKey="sign_convention" currentKey={ipSK} currentDir={ipSD} onSort={ipSort} className="py-1 px-2 text-slate-500" />
                  <ColHeader label="Date Format" sortKey="date_format" currentKey={ipSK} currentDir={ipSD} onSort={ipSort} className="py-1 px-2 text-slate-500" />
                  <th className="py-1 px-2" />
                </tr></thead>
                <tbody>
                  {ipSorted.map(p => (
                    <tr key={p.profile_id as number} className="border-b border-slate-100 hover:bg-slate-50">
                      <td className="py-1 px-2 font-medium">{p.profile_name as string}</td>
                      <td className="py-1 px-2">{p.bank_name as string}</td>
                      <td className="py-1 px-2">{p.file_type as string}</td>
                      <td className="py-1 px-2">{p.sign_convention as string}</td>
                      <td className="py-1 px-2">{p.date_format as string}</td>
                      <td className="py-1 px-2 flex gap-1">
                        <button onClick={() => setEditing({ ...p })} className="text-blue-500 hover:text-blue-700"><Edit2 size={13} /></button>
                        <button onClick={() => { if (confirm('Delete profile?')) delMut.mutate(p.profile_id as number) }} className="text-red-500 hover:text-red-700"><Trash2 size={13} /></button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!(profiles as Record<string, unknown>[]).length && <p className="text-sm text-slate-400 py-4 text-center">No profiles yet. Create one below.</p>}
            </div>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader><CardTitle>{editing.profile_id ? 'Edit Profile' : 'Create Profile'}</CardTitle></CardHeader>
        <CardBody className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Profile Name *</label>
              <input type="text" value={editing.profile_name as string} onChange={e => set('profile_name', e.target.value)}
                className="w-full border border-slate-300 rounded px-2 py-1.5 text-sm" />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Bank Name</label>
              <input type="text" value={editing.bank_name as string} onChange={e => set('bank_name', e.target.value)}
                className="w-full border border-slate-300 rounded px-2 py-1.5 text-sm" />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">File Type</label>
              <Select value={editing.file_type as string} onChange={e => set('file_type', e.target.value)}>
                {['xlsx', 'csv', 'xls'].map(v => <option key={v} value={v}>{v}</option>)}
              </Select>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Sign Convention</label>
              <Select value={editing.sign_convention as string} onChange={e => set('sign_convention', e.target.value)}>
                <option value="debit_credit">Separate Debit/Credit columns</option>
                <option value="signed_amount">Single signed Amount column</option>
              </Select>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Date Column</label>
              <input type="text" value={editing.date_column as string} onChange={e => set('date_column', e.target.value)}
                className="w-full border border-slate-300 rounded px-2 py-1.5 text-sm" />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Description Column</label>
              <input type="text" value={editing.description_column as string} onChange={e => set('description_column', e.target.value)}
                className="w-full border border-slate-300 rounded px-2 py-1.5 text-sm" />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Debit Column</label>
              <input type="text" value={editing.debit_column as string} onChange={e => set('debit_column', e.target.value)}
                className="w-full border border-slate-300 rounded px-2 py-1.5 text-sm" />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Credit Column</label>
              <input type="text" value={editing.credit_column as string} onChange={e => set('credit_column', e.target.value)}
                className="w-full border border-slate-300 rounded px-2 py-1.5 text-sm" />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Amount Column (signed_amount)</label>
              <input type="text" value={editing.amount_column as string} onChange={e => set('amount_column', e.target.value)}
                className="w-full border border-slate-300 rounded px-2 py-1.5 text-sm" />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Balance Column</label>
              <input type="text" value={editing.balance_column as string} onChange={e => set('balance_column', e.target.value)}
                className="w-full border border-slate-300 rounded px-2 py-1.5 text-sm" />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Date Format</label>
              <input type="text" value={editing.date_format as string} onChange={e => set('date_format', e.target.value)}
                className="w-full border border-slate-300 rounded px-2 py-1.5 text-sm" placeholder="%d/%m/%Y" />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Skip Rows (header offset)</label>
              <input type="number" min={0} value={editing.skip_rows as number} onChange={e => set('skip_rows', Number(e.target.value))}
                className="w-full border border-slate-300 rounded px-2 py-1.5 text-sm" />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Decimal Separator</label>
              <Select value={editing.decimal_separator as string} onChange={e => set('decimal_separator', e.target.value)}>
                <option value=",">,</option>
                <option value=".">.</option>
              </Select>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Thousands Separator</label>
              <Select value={editing.thousands_separator as string} onChange={e => set('thousands_separator', e.target.value)}>
                <option value=".">.</option>
                <option value=",">,</option>
                <option value="">(none)</option>
              </Select>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Installment Column (optional)</label>
              <input type="text" value={editing.installment_column as string} onChange={e => set('installment_column', e.target.value)}
                className="w-full border border-slate-300 rounded px-2 py-1.5 text-sm" placeholder="e.g. ΔΟΣΗ" />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Original Date Column (optional)</label>
              <input type="text" value={editing.secondary_date_column as string} onChange={e => set('secondary_date_column', e.target.value)}
                className="w-full border border-slate-300 rounded px-2 py-1.5 text-sm" />
            </div>
          </div>
          <div className="flex items-center gap-2">
            <input type="checkbox" id="invert" checked={!!editing.invert_amounts} onChange={e => set('invert_amounts', e.target.checked)} />
            <label htmlFor="invert" className="text-sm text-slate-700">Invert amounts (for credit-card exports where purchases are positive)</label>
          </div>
          <div className="flex gap-2">
            <Button onClick={() => saveMut.mutate(editing)} disabled={!editing.profile_name || saveMut.isPending}>
              {saveMut.isPending ? <Spinner size={14} /> : null} Save Profile
            </Button>
            <Button variant="secondary" onClick={() => setEditing(EMPTY_PROFILE)}>Clear</Button>
          </div>
          {saveMut.isError && <ErrorBox msg={apiErrorMsg(saveMut.error)} />}
          {saveMut.isSuccess && <SuccessBox msg="Profile saved." />}
        </CardBody>
      </Card>
    </div>
  )
}

// ── Bank → Payee Rules ────────────────────────────────────────────────────────

const EMPTY_RULE = { pattern: '', match_type: 'contains', payees_id: null, categories_id: null, priority: 0 }

function PayeeRulesTab() {
  const qc = useQueryClient()
  const [editing, setEditing] = useState<Record<string, unknown>>(EMPTY_RULE)
  const { data: rules = [] } = useQuery({ queryKey: ['payee-rules'], queryFn: getPayeeRules })
  const { sorted: prSorted, sortKey: prSK, sortDir: prSD, toggleSort: prSort } = useSortTable(rules as Record<string, unknown>[], 'priority', 'desc')
  const { data: payees = [] } = useQuery({ queryKey: ['payees'], queryFn: getPayees })
  const { data: categories = [] } = useQuery({ queryKey: ['categories'], queryFn: getCategories })

  const saveMut = useMutation({
    mutationFn: (d: Record<string, unknown>) => d.rule_id
      ? updatePayeeRule(d.rule_id as number, d)
      : createPayeeRule(d),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['payee-rules'] }); setEditing(EMPTY_RULE) },
  })
  const delMut = useMutation({
    mutationFn: (id: number) => deletePayeeRule(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['payee-rules'] }),
  })

  const [ruleSearch, setRuleSearch] = useState('')
  const set = (field: string, value: unknown) => setEditing(prev => ({ ...prev, [field]: value }))

  const filteredRules = ruleSearch.trim()
    ? prSorted.filter(r =>
        String(r.pattern ?? '').toLowerCase().includes(ruleSearch.toLowerCase()) ||
        String(r.payee_name ?? '').toLowerCase().includes(ruleSearch.toLowerCase()) ||
        String(r.category_name ?? '').toLowerCase().includes(ruleSearch.toLowerCase())
      )
    : prSorted

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between gap-2">
            <CardTitle>Payee Rules ({(rules as Record<string, unknown>[]).length})</CardTitle>
            <input
              type="text"
              placeholder="Search…"
              value={ruleSearch}
              onChange={e => setRuleSearch(e.target.value)}
              className="px-2.5 py-1.5 text-xs border border-slate-300 rounded w-44 focus:outline-none focus:border-blue-400"
            />
          </div>
        </CardHeader>
        <CardBody>
          <p className="text-sm text-slate-500 mb-3">Rules automatically assign a Payee and/or Category to imported transactions based on their description. Rules are evaluated by Priority (highest first).</p>
          <div className="overflow-x-auto overflow-y-auto max-h-72">
            <table className="w-full text-xs">
              <thead className="sticky top-0 z-10"><tr className="border-b border-slate-200 bg-slate-50 text-slate-500">
                <ColHeader label="Pattern" sortKey="pattern" currentKey={prSK} currentDir={prSD} onSort={prSort} className="py-1 px-2 text-slate-500 bg-slate-50" />
                <ColHeader label="Match" sortKey="match_type" currentKey={prSK} currentDir={prSD} onSort={prSort} className="py-1 px-2 text-slate-500 bg-slate-50" />
                <ColHeader label="Payee" sortKey="payee_name" currentKey={prSK} currentDir={prSD} onSort={prSort} className="py-1 px-2 text-slate-500 bg-slate-50" />
                <ColHeader label="Category" sortKey="category_name" currentKey={prSK} currentDir={prSD} onSort={prSort} className="py-1 px-2 text-slate-500 bg-slate-50" />
                <ColHeader label="Priority" sortKey="priority" currentKey={prSK} currentDir={prSD} onSort={prSort} align="right" className="py-1 px-2 text-slate-500 bg-slate-50" />
                <th className="py-1 px-2 bg-slate-50" />
              </tr></thead>
              <tbody>
                {filteredRules.map(r => (
                  <tr key={r.rule_id as number} className="border-b border-slate-100 hover:bg-slate-50">
                    <td className="py-1 px-2 font-mono">{r.pattern as string}</td>
                    <td className="py-1 px-2">{r.match_type as string}</td>
                    <td className="py-1 px-2">{r.payee_name as string ?? '—'}</td>
                    <td className="py-1 px-2 max-w-[150px] truncate">{r.category_name as string ?? '—'}</td>
                    <td className="py-1 px-2 text-right">{r.priority as number}</td>
                    <td className="py-1 px-2 flex gap-1">
                      <button onClick={() => setEditing({ ...r })} className="text-blue-500 hover:text-blue-700"><Edit2 size={13} /></button>
                      <button onClick={() => { if (confirm('Delete rule?')) delMut.mutate(r.rule_id as number) }} className="text-red-500 hover:text-red-700"><Trash2 size={13} /></button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!(rules as Record<string, unknown>[]).length && <p className="text-sm text-slate-400 py-4 text-center">No rules yet.</p>}
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader><CardTitle>{editing.rule_id ? 'Edit Rule' : 'Add Rule'}</CardTitle></CardHeader>
        <CardBody className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Pattern *</label>
              <input type="text" value={editing.pattern as string} onChange={e => set('pattern', e.target.value)}
                className="w-full border border-slate-300 rounded px-2 py-1.5 text-sm" placeholder="Text to match in description" />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Match Type</label>
              <Select value={editing.match_type as string} onChange={e => set('match_type', e.target.value)}>
                {['contains', 'starts_with', 'exact', 'regex'].map(v => <option key={v} value={v}>{v}</option>)}
              </Select>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Assign Payee (optional)</label>
              <Select value={editing.payees_id as number ?? ''} onChange={e => set('payees_id', e.target.value ? Number(e.target.value) : null)}>
                <option value="">— none —</option>
                {(payees as Record<string, unknown>[]).map(p => <option key={p.id as number} value={p.id as number}>{p.name as string}</option>)}
              </Select>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Assign Category (optional)</label>
              <Select value={editing.categories_id as number ?? ''} onChange={e => set('categories_id', e.target.value ? Number(e.target.value) : null)}>
                <option value="">— none —</option>
                {(categories as Record<string, unknown>[]).map(c => <option key={c.id as number} value={c.id as number}>{c.name as string}</option>)}
              </Select>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Priority (higher = checked first)</label>
              <input type="number" value={editing.priority as number} onChange={e => set('priority', Number(e.target.value))}
                className="w-full border border-slate-300 rounded px-2 py-1.5 text-sm" />
            </div>
          </div>
          <div className="flex gap-2">
            <Button onClick={() => saveMut.mutate(editing)} disabled={!editing.pattern || saveMut.isPending}>
              {saveMut.isPending ? <Spinner size={14} /> : null} {editing.rule_id ? 'Update Rule' : 'Add Rule'}
            </Button>
            <Button variant="secondary" onClick={() => setEditing(EMPTY_RULE)}>Clear</Button>
          </div>
          {saveMut.isError && <ErrorBox msg={apiErrorMsg(saveMut.error)} />}
          {saveMut.isSuccess && <SuccessBox msg="Rule saved." />}
        </CardBody>
      </Card>
    </div>
  )
}

// ── Bank → Import History ─────────────────────────────────────────────────────

function ImportHistoryTab() {
  const { data: accounts = [] } = useQuery({ queryKey: ['import-history-accounts'], queryFn: getReconciliationHistoryAccounts })
  const [accountId, setAccountId] = useState<number | null>(null)
  const { data: history = [], isLoading } = useQuery({
    queryKey: ['reconciliation-history', accountId],
    queryFn: () => getReconciliationHistory(accountId!),
    enabled: !!accountId,
  })
  const { sorted: ihSorted, sortKey: ihSK, sortDir: ihSD, toggleSort: ihSort } = useSortTable(history as Record<string, unknown>[], 'session_date', 'desc')

  return (
    <div className="space-y-4">
      <Card>
        <CardBody>
          <div className="flex gap-4 items-end">
            <div className="flex-1">
              <label className="block text-xs font-medium text-slate-600 mb-1">Account</label>
              <Select value={accountId ?? ''} onChange={e => setAccountId(Number(e.target.value))}>
                <option value="">— select account —</option>
                {(accounts as Record<string, unknown>[]).map(a => (
                  <option key={a.id as number} value={a.id as number}>{a.name as string}</option>
                ))}
              </Select>
            </div>
          </div>
        </CardBody>
      </Card>
      {isLoading && <Spinner />}
      {accountId && !isLoading && (
        <Card>
          <CardBody>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead><tr className="border-b border-slate-200 text-slate-500">
                  <ColHeader label="Session Date" sortKey="session_date" currentKey={ihSK} currentDir={ihSD} onSort={ihSort} className="py-1 px-2 text-slate-500" />
                  <ColHeader label="Statement Date" sortKey="statement_date" currentKey={ihSK} currentDir={ihSD} onSort={ihSort} className="py-1 px-2 text-slate-500" />
                  <ColHeader label="Statement Bal" sortKey="statement_balance" currentKey={ihSK} currentDir={ihSD} onSort={ihSort} align="right" className="py-1 px-2 text-slate-500" />
                  <ColHeader label="App Bal" sortKey="app_balance" currentKey={ihSK} currentDir={ihSD} onSort={ihSort} align="right" className="py-1 px-2 text-slate-500" />
                  <ColHeader label="Difference" sortKey="difference" currentKey={ihSK} currentDir={ihSD} onSort={ihSort} align="right" className="py-1 px-2 text-slate-500" />
                  <ColHeader label="# Txns" sortKey="tx_count" currentKey={ihSK} currentDir={ihSD} onSort={ihSort} align="right" className="py-1 px-2 text-slate-500" />
                  <ColHeader label="Notes" sortKey="notes" currentKey={ihSK} currentDir={ihSD} onSort={ihSort} className="py-1 px-2 text-slate-500" />
                </tr></thead>
                <tbody>
                  {ihSorted.map(h => (
                    <tr key={h.id as number} className="border-b border-slate-100">
                      <td className="py-1 px-2">{String(h.session_date ?? '').substring(0, 19)}</td>
                      <td className="py-1 px-2">{h.statement_date as string}</td>
                      <td className="py-1 px-2 text-right">{h.statement_balance != null ? fmt2(Number(h.statement_balance)) : 'N/A'}</td>
                      <td className="py-1 px-2 text-right">{h.app_balance != null ? fmt2(Number(h.app_balance)) : 'N/A'}</td>
                      <td className={`py-1 px-2 text-right ${Number(h.difference) === 0 ? 'text-green-600' : 'text-red-600'}`}>
                        {h.difference != null ? (Number(h.difference) >= 0 ? '+' : '') + fmt2(Number(h.difference)) : 'N/A'}
                      </td>
                      <td className="py-1 px-2 text-right">{h.tx_count as number}</td>
                      <td className="py-1 px-2 text-slate-500">{h.notes as string}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!(history as Record<string, unknown>[]).length && <p className="text-sm text-slate-400 py-4 text-center">No reconciliation sessions found.</p>}
            </div>
          </CardBody>
        </Card>
      )}
    </div>
  )
}

// ── Bank → Salt Edge ──────────────────────────────────────────────────────────

function SaltEdgeTab() {
  return (
    <div className="space-y-4">
      <InfoBox>
        Connect directly to your bank via <strong>Salt Edge</strong> (PSD2 open banking). Supports Greek banks (Alpha Bank, Eurobank, Piraeus, NBG) and 5,000+ institutions across Europe.
      </InfoBox>
      <Card>
        <CardBody className="space-y-3">
          <p className="text-sm text-slate-600">You need a <strong>free Salt Edge developer account</strong> to use this feature.</p>
          <ol className="text-sm text-slate-600 space-y-1 list-decimal list-inside">
            <li>Sign up at <strong>saltedge.com/dashboard</strong> and create an application</li>
            <li>Copy your <strong>App-id</strong> and <strong>Secret</strong></li>
            <li>Use the credentials below to connect to your bank</li>
          </ol>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">App-id</label>
              <input type="password" className="w-full border border-slate-300 rounded px-2 py-1.5 text-sm" placeholder="Salt Edge App-id" />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Secret</label>
              <input type="password" className="w-full border border-slate-300 rounded px-2 py-1.5 text-sm" placeholder="Salt Edge Secret" />
            </div>
          </div>
          <p className="text-xs text-slate-400">Salt Edge open banking connection requires backend integration. Configure credentials in Tools → Settings.</p>
        </CardBody>
      </Card>
    </div>
  )
}

// ── Bank → Revolut Personal ───────────────────────────────────────────────────

function RevolutPersonalTab() {
  const [file, setFile] = useState<File | null>(null)
  const [result, setResult] = useState<Record<string, unknown> | null>(null)
  const { data: accounts = [] } = useQuery({ queryKey: ['bank-accounts'], queryFn: getBankAccounts })
  const [accountId, setAccountId] = useState<number | null>(null)
  const saveSettings = useImporterSettings('revp', s => {
    if (s.account_id) setAccountId(s.account_id as number)
  })

  const importMut = useMutation({
    mutationFn: () => {
      const fd = new FormData(); fd.append('file', file!)
      return importFile('revolut', fd)
    },
    onSuccess: (d) => { setResult(d); saveSettings({ account_id: accountId }) },
  })

  return (
    <div className="space-y-4">
      <InfoBox>
        Import transactions from a <strong>Revolut Personal</strong> account CSV export.<br />
        In the Revolut app: <strong>Account → Statements → Download CSV</strong>
      </InfoBox>
      <Card>
        <CardHeader><CardTitle>Revolut Personal Import</CardTitle></CardHeader>
        <CardBody className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-slate-600 mb-1">Target Account</label>
            <Select value={accountId ?? ''} onChange={e => setAccountId(Number(e.target.value))}>
              <option value="">— select account —</option>
              {(accounts as Record<string, unknown>[]).map(a => <option key={a.id as number} value={a.id as number}>{a.name as string}</option>)}
            </Select>
          </div>
          {file ? (
            <div className="flex items-center gap-3 p-3 bg-slate-50 rounded border border-slate-200">
              <CheckCircle size={16} className="text-green-500" />
              <span className="text-sm font-medium">{file.name}</span>
              <button className="ml-auto text-xs text-red-500" onClick={() => setFile(null)}>Remove</button>
            </div>
          ) : (
            <FileDropZone accept=".csv" onChange={setFile} label="Upload Revolut CSV export" />
          )}
          <Button onClick={() => importMut.mutate()} disabled={!file || importMut.isPending}>
            {importMut.isPending ? <><Spinner size={14} /> Importing…</> : <>Import</>}
          </Button>
          {importMut.isError && <ErrorBox msg={apiErrorMsg(importMut.error)} />}
          {result && <SuccessBox msg={result.message as string ?? `Imported ${result.imported ?? 0}, skipped ${result.skipped ?? 0}`} />}
        </CardBody>
      </Card>
    </div>
  )
}

// ── Bank → Revolut Savings ────────────────────────────────────────────────────

function RevolutSavingsTab() {
  const [file, setFile] = useState<File | null>(null)
  const [mode, setMode] = useState<'tx' | 'inv'>('inv')
  const [accountId, setAccountId] = useState<number | null>(null)
  const [replaceMode, setReplaceMode] = useState(false)
  const [parseResult, setParseResult] = useState<Record<string, unknown> | null>(null)
  const [importResult, setImportResult] = useState<Record<string, unknown> | null>(null)
  const [expandedInv, setExpandedInv] = useState(true)
  const [expandedTx, setExpandedTx] = useState(true)

  const { data: allAccounts = [] } = useQuery({ queryKey: ['all-accounts'], queryFn: getAllAccounts })
  const brokerAccTypes = ['Brokerage', 'Margin', 'Other Investment', 'Pension']
  const invAccounts  = (allAccounts as Record<string, unknown>[]).filter(a => brokerAccTypes.includes(a.type as string))
  const cashAccounts = (allAccounts as Record<string, unknown>[]).filter(a => !brokerAccTypes.includes(a.type as string))
  const accounts = mode === 'inv' ? invAccounts : cashAccounts

  const saveSettings = useImporterSettings('revs', s => {
    if (s.account_id) setAccountId(s.account_id as number)
    if (s.mode) setMode(s.mode as 'tx' | 'inv')
  })

  // Reset account when mode changes to avoid showing wrong account type
  const handleModeChange = (m: 'tx' | 'inv') => {
    setMode(m)
    setAccountId(null)
    setParseResult(null)
  }

  const parseMut = useMutation({
    mutationFn: () => revsParse(file!, accountId!, mode),
    onSuccess: (d) => { setParseResult(d); saveSettings({ account_id: accountId, mode }) },
  })

  const importMut = useMutation({
    mutationFn: () => revsImport(file!, accountId!, mode, replaceMode),
    onSuccess: (d) => { setImportResult(d); saveSettings({ account_id: accountId, mode }) },
  })

  const inv = (parseResult?.inv_records as Record<string, unknown>[]) ?? []
  const tx  = (parseResult?.tx_records  as Record<string, unknown>[]) ?? []
  const newInv = inv.filter(r => r.status === 'new').length
  const newTx  = tx.filter(r => r.status === 'new').length
  const summary = parseResult?.summary as Record<string, unknown> ?? {}
  const secInfo = parseResult?.sec_info as Record<string, unknown> | null ?? null

  return (
    <div className="space-y-4">
      <InfoBox>
        Import from a <strong>Revolut Savings</strong> (Flexible Cash Funds) account CSV statement.<br />
        In the Revolut app: <strong>Savings → ⋮ → Statement → Download CSV</strong>
      </InfoBox>
      <Card>
        <CardHeader><CardTitle>Revolut Savings Import</CardTitle></CardHeader>
        <CardBody className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-slate-600 mb-2">Import Mode</label>
            <div className="flex gap-3">
              {[
                { value: 'inv', label: '📈 Investment mode', desc: 'Record fund units as Buy/Dividend (Brokerage / Other Investment account)' },
                { value: 'tx',  label: '💳 Transaction mode', desc: 'Map events to plain cash transactions (Savings/Checking account)' },
              ].map(o => (
                <label key={o.value} className={`flex-1 border rounded-lg p-3 cursor-pointer ${mode === o.value ? 'border-blue-500 bg-blue-50' : 'border-slate-200'}`}>
                  <input type="radio" name="revs_mode" value={o.value} checked={mode === o.value} onChange={() => handleModeChange(o.value as 'tx' | 'inv')} className="sr-only" />
                  <div className="text-sm font-medium">{o.label}</div>
                  <div className="text-xs text-slate-500 mt-1">{o.desc}</div>
                </label>
              ))}
            </div>
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-600 mb-1">
              Target Account {mode === 'inv' ? '(Brokerage / Other Investment)' : '(Savings / Checking)'}
            </label>
            <Select value={accountId ?? ''} onChange={e => setAccountId(Number(e.target.value))}>
              <option value="">— select account —</option>
              {accounts.map(a => <option key={a.id as number} value={a.id as number}>{a.name as string}</option>)}
            </Select>
          </div>
          {file ? (
            <div className="flex items-center gap-3 p-3 bg-slate-50 rounded border border-slate-200">
              <CheckCircle size={16} className="text-green-500" />
              <span className="text-sm font-medium">{file.name}</span>
              <button className="ml-auto text-xs text-red-500" onClick={() => { setFile(null); setParseResult(null) }}>Remove</button>
            </div>
          ) : (
            <FileDropZone accept=".csv" onChange={setFile} label="Upload Revolut Savings CSV statement" />
          )}
          <div className="flex items-center gap-2">
            <input type="checkbox" id="revs_replace" checked={replaceMode} onChange={e => setReplaceMode(e.target.checked)} />
            <label htmlFor="revs_replace" className="text-sm text-slate-700">Replace mode (delete existing Revolut Savings records)</label>
          </div>
          <Button onClick={() => parseMut.mutate()} disabled={!file || !accountId || parseMut.isPending}>
            {parseMut.isPending ? <><Spinner size={14} /> Parsing…</> : <>🔍 Parse & Preview</>}
          </Button>
          {parseMut.isError && <ErrorBox msg={apiErrorMsg(parseMut.error)} />}
        </CardBody>
      </Card>

      {parseResult && (
        <div className="space-y-4">
          {summary.rows != null && (
            <div className="grid grid-cols-3 gap-3">
              <div className="bg-slate-50 rounded-lg p-3"><p className="text-xs text-slate-500">Rows in file</p><p className="text-2xl font-bold">{summary.rows as number}</p></div>
              <div className="bg-slate-50 rounded-lg p-3"><p className="text-xs text-slate-500">Date range from</p><p className="text-sm font-medium">{summary.date_from as string}</p></div>
              <div className="bg-slate-50 rounded-lg p-3"><p className="text-xs text-slate-500">Date range to</p><p className="text-sm font-medium">{summary.date_to as string}</p></div>
            </div>
          )}
          {secInfo && (
            <div className="text-xs bg-blue-50 border border-blue-200 rounded px-3 py-2 text-blue-700">
              ℹ️ Security match: <strong>{secInfo.name as string}</strong> ({secInfo.match_type as string})
            </div>
          )}

          <div className="grid grid-cols-4 gap-3">
            {[
              { label: '🆕 New investments', value: newInv },
              { label: '🔄 Skip investments', value: inv.length - newInv },
              { label: '🆕 New transactions', value: newTx },
              { label: '🔄 Skip transactions', value: tx.length - newTx },
            ].map(s => (
              <div key={s.label} className="bg-slate-50 rounded-lg p-3">
                <p className="text-xs text-slate-500">{s.label}</p>
                <p className="text-2xl font-bold">{s.value}</p>
              </div>
            ))}
          </div>

          {inv.length > 0 && (
            <Card>
              <CardHeader>
                <button className="flex items-center gap-2 w-full text-left" onClick={() => setExpandedInv(x => !x)}>
                  <CardTitle>Preview — Investments ({inv.length})</CardTitle>
                  {expandedInv ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                </button>
              </CardHeader>
              {expandedInv && (
                <CardBody>
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                      <thead><tr className="border-b border-slate-200 text-slate-500">
                        <th className="py-1 px-2 text-left">Status</th>
                        <th className="py-1 px-2 text-left">Date</th>
                        <th className="py-1 px-2 text-left">Action</th>
                        <th className="py-1 px-2 text-right">Qty</th>
                        <th className="py-1 px-2 text-right">Price</th>
                        <th className="py-1 px-2 text-right">Total (€)</th>
                      </tr></thead>
                      <tbody>
                        {inv.slice(0, 100).map((r, i) => (
                          <tr key={i} className="border-b border-slate-100">
                            <td className="py-1 px-2"><StatusBadge status={r.status as string} /></td>
                            <td className="py-1 px-2">{r.date as string}</td>
                            <td className="py-1 px-2">{r.action as string}</td>
                            <td className="py-1 px-2 text-right">{fmtNum(Number(r.quantity ?? 0), 4)}</td>
                            <td className="py-1 px-2 text-right">{fmtNum(Number(r.price ?? 0), 4)}</td>
                            <td className="py-1 px-2 text-right">{fmtNum(Number(r.total_eur ?? 0), 2)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {inv.length > 100 && <p className="text-xs text-slate-400 py-2 text-center">Showing 100 of {inv.length} records</p>}
                  </div>
                </CardBody>
              )}
            </Card>
          )}

          {tx.length > 0 && (
            <Card>
              <CardHeader>
                <button className="flex items-center gap-2 w-full text-left" onClick={() => setExpandedTx(x => !x)}>
                  <CardTitle>Preview — Cash Transactions ({tx.length})</CardTitle>
                  {expandedTx ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                </button>
              </CardHeader>
              {expandedTx && (
                <CardBody>
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                      <thead><tr className="border-b border-slate-200 text-slate-500">
                        <th className="py-1 px-2 text-left">Status</th>
                        <th className="py-1 px-2 text-left">Date</th>
                        <th className="py-1 px-2 text-left">Description</th>
                        <th className="py-1 px-2 text-right">Amount (€)</th>
                      </tr></thead>
                      <tbody>
                        {tx.slice(0, 100).map((r, i) => (
                          <tr key={i} className="border-b border-slate-100">
                            <td className="py-1 px-2"><StatusBadge status={r.status as string} /></td>
                            <td className="py-1 px-2">{r.date as string}</td>
                            <td className="py-1 px-2">{r.description as string}</td>
                            <td className="py-1 px-2 text-right">{fmtNum(Number(r.amount ?? 0), 2)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {tx.length > 100 && <p className="text-xs text-slate-400 py-2 text-center">Showing 100 of {tx.length} records</p>}
                  </div>
                </CardBody>
              )}
            </Card>
          )}

          {newInv + newTx > 0 ? (
            <Button onClick={() => importMut.mutate()} disabled={importMut.isPending}>
              {importMut.isPending ? <><Spinner size={14} /> Importing…</> : <>✅ Confirm Import ({newInv + newTx} records)</>}
            </Button>
          ) : (
            <InfoBox>Nothing new to import. All records already exist in the database.</InfoBox>
          )}
          {importMut.isError && <ErrorBox msg={apiErrorMsg(importMut.error)} />}
          {importResult && <SuccessBox msg={`Import complete! Investments: ${(importResult as Record<string, unknown>).investments ?? 0} imported, ${(importResult as Record<string, unknown>).investments_skip ?? 0} skipped. Transactions: ${(importResult as Record<string, unknown>).transactions ?? 0} imported.`} />}
        </div>
      )}
    </div>
  )
}

// ── Shared: Security Mapping Panel ───────────────────────────────────────────
// sec_matches: { [isin_or_name]: [sec_id | null, match_type_str] }
// overrides:   user choices { [isin_or_name]: sec_id | 0 (=create new) }

function SecurityMappingPanel({
  secMatches,
  overrides,
  onChange,
}: {
  secMatches: Record<string, { sec_id: number | null; match_type: string }>
  overrides: Record<string, number>
  onChange: (key: string, val: number) => void
}) {
  const { data: allSecs = [] } = useQuery({
    queryKey: ['all-securities'],
    queryFn: () => getSecurities(),
    staleTime: 60_000,
  })
  const newEntries = Object.entries(secMatches).filter(([, v]) => v.sec_id === null)
  if (newEntries.length === 0) return null

  return (
    <Card>
      <CardHeader>
        <CardTitle>🔗 Security Mapping ({newEntries.length} unmatched)</CardTitle>
      </CardHeader>
      <CardBody>
        <p className="text-xs text-slate-500 mb-3">
          These securities were not found in your database. Map each to an existing security or leave as "Create new" to add it automatically.
        </p>
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-slate-200 text-slate-500">
              <th className="py-1 px-2 text-left">IB Name / ISIN</th>
              <th className="py-1 px-2 text-left">Map to existing security</th>
            </tr>
          </thead>
          <tbody>
            {newEntries.map(([key]) => (
              <tr key={key} className="border-b border-slate-100">
                <td className="py-1.5 px-2 font-mono text-slate-700">{key}</td>
                <td className="py-1.5 px-2">
                  <Select
                    value={String(overrides[key] ?? '')}
                    onChange={e => onChange(key, Number(e.target.value))}
                  >
                    <option value="">— Create new security —</option>
                    {(allSecs as Record<string, unknown>[]).map(s => (
                      <option key={s.id as number} value={String(s.id)}>
                        {s.name as string} ({s.ticker as string})
                      </option>
                    ))}
                  </Select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </CardBody>
    </Card>
  )
}

// ── Brokerage → Interactive Brokers ──────────────────────────────────────────

function IBFlexTab() {
  const [sourceMode, setSourceMode] = useState<'api' | 'paste'>('api')
  const [token, setToken] = useState('')
  const [queryId, setQueryId] = useState('')
  const [showToken, setShowToken] = useState(false)
  const [rawXml, setRawXml] = useState('')
  const [xml, setXml] = useState('')
  const [accountId, setAccountId] = useState<number | null>(null)
  const [cashAccountId, setCashAccountId] = useState<number | null>(null)
  const [excludeFxSpot, setExcludeFxSpot] = useState(true)
  const saveSettings = useImporterSettings('ib', s => {
    if (s.token)           setToken(s.token as string)
    if (s.query_id)        setQueryId(s.query_id as string)
    if (s.account_id)      setAccountId(s.account_id as number)
    if (s.cash_account_id) setCashAccountId(s.cash_account_id as number)
    if (s.exclude_fx_spot != null) setExcludeFxSpot(Boolean(s.exclude_fx_spot))
  })
  const [filterFrom, setFilterFrom] = useState('')
  const [filterTo, setFilterTo] = useState('')
  const [replaceMode, setReplaceMode] = useState(false)
  const [parseResult, setParseResult] = useState<Record<string, unknown> | null>(null)
  const [secMappingOverrides, setSecMappingOverrides] = useState<Record<string, number>>({})
  const [importResult, setImportResult] = useState<Record<string, unknown> | null>(null)
  const [expandedInv, setExpandedInv] = useState(true)
  const [expandedTx, setExpandedTx] = useState(true)

  const { data: allAccounts = [] } = useQuery({ queryKey: ['all-accounts'], queryFn: getAllAccounts })
  const brokerageAccTypes = ['Brokerage', 'Margin', 'Other Investment', 'Pension']
  const brokerAccounts = (allAccounts as Record<string, unknown>[]).filter(a => brokerageAccTypes.includes(a.type as string))
  const cashAccounts = (allAccounts as Record<string, unknown>[]).filter(a => !brokerageAccTypes.includes(a.type as string))

  const _persistSettings = () =>
    saveSettings({ token, query_id: queryId, account_id: accountId, cash_account_id: cashAccountId, exclude_fx_spot: excludeFxSpot })

  const [wasCached, setWasCached] = useState<boolean | null>(null)
  const fetchMut = useMutation({
    mutationFn: (forceRefresh: boolean) => ibFlexFetch(token, queryId, forceRefresh),
    onSuccess: async (data) => {
      setXml(data.xml)
      setWasCached(Boolean(data.cached))
      _persistSettings()
      if (accountId) {
        const parsed = await ibFlexParse(data.xml, accountId, cashAccountId ?? undefined)
        setParseResult(parsed)
        setSecMappingOverrides({})
      }
    },
  })

  const parseMut = useMutation({
    mutationFn: () => ibFlexParse(rawXml, accountId!, cashAccountId ?? undefined),
    onSuccess: (d) => { setParseResult(d); setSecMappingOverrides({}); _persistSettings() },
  })

  const importMut = useMutation({
    mutationFn: async () => {
      // Save any user-defined security mappings before importing so _get_or_create_security picks them up
      const toSave = Object.fromEntries(
        Object.entries(secMappingOverrides).filter(([, v]) => v > 0)
      ) as Record<string, number>
      if (Object.keys(toSave).length > 0) {
        await saveSecurityMappings('Interactive Brokers', toSave)
      }
      return ibFlexImport({
        xml: xml || rawXml,
        account_id: accountId,
        cash_account_id: cashAccountId,
        replace_mode: replaceMode,
        import_inv: true,
        import_tx: true,
        filter_from: filterFrom || null,
        filter_to: filterTo || null,
        exclude_fx_spot: excludeFxSpot,
      })
    },
    onSuccess: (d) => {
      setImportResult(d)
      _persistSettings()
    },
  })

  const inv = (parseResult?.inv_records as Record<string, unknown>[]) ?? []
  const tx = (parseResult?.tx_records as Record<string, unknown>[]) ?? []
  const meta = parseResult?.meta as Record<string, unknown> ?? {}
  const rawSecMatches = (parseResult?.sec_matches ?? {}) as Record<string, { sec_id: number | null; match_type: string }>

  // Apply date + FX-spot filters to preview display (same logic as server-side import filter)
  const FX_SPOT_CATS = new Set(['CASH', 'FX', 'FXSPOT'])
  const isFxSpot = (r: Record<string, unknown>) => FX_SPOT_CATS.has(String(r.asset_category ?? '').toUpperCase())
  const filterRecord = (r: Record<string, unknown>) => {
    const d = r.date as string
    if (filterFrom && d < filterFrom) return false
    if (filterTo && d > filterTo) return false
    if (excludeFxSpot && isFxSpot(r)) return false
    return true
  }

  // The Security Mapping panel is keyed the same way the backend matches
  // securities (ISIN if present, else name) — drop the keys that belong only
  // to FX-spot rows once those are excluded, so this panel doesn't ask you to
  // map/create a security for something that won't actually be imported.
  const secMatchKey = (r: Record<string, unknown>) => (r.isin as string) || (r.name as string) || ''
  const fxSpotKeys = new Set(inv.filter(isFxSpot).map(secMatchKey))
  const secMatches = excludeFxSpot
    ? Object.fromEntries(Object.entries(rawSecMatches).filter(([k]) => !fxSpotKeys.has(k)))
    : rawSecMatches
  const visibleInv = (filterFrom || filterTo || excludeFxSpot) ? inv.filter(filterRecord) : inv
  const visibleTx  = (filterFrom || filterTo) ? tx.filter(filterRecord)  : tx

  const newInv = visibleInv.filter(r => r.status === 'new').length
  const newTx = visibleTx.filter(r => r.status === 'new').length

  return (
    <div className="space-y-6">
      <details className="bg-slate-50 border border-slate-200 rounded-lg p-4">
        <summary className="cursor-pointer text-sm font-medium text-slate-700">ℹ️ One-time IB setup (click to expand)</summary>
        <ol className="mt-3 text-sm text-slate-600 space-y-1 list-decimal list-inside">
          <li>Log in to IB Client Portal → <strong>Reports → Flex Queries</strong></li>
          <li>Click <strong>+</strong> → Activity Flex Query</li>
          <li>Enable <strong>Trades</strong> and <strong>Cash Transactions</strong> sections</li>
          <li>Set Date Format: <code>yyyyMMdd</code>, Separator: <code>;</code></li>
          <li>Save — note the numeric <strong>Query ID</strong></li>
          <li>Edit the query → tick <strong>"Allow Web Service Access"</strong> → Save</li>
          <li>Go to <strong>Reports → Flex Web Service</strong> → create/copy your <strong>Token</strong></li>
        </ol>
      </details>

      <Card>
        <CardHeader><CardTitle>Statement Source</CardTitle></CardHeader>
        <CardBody className="space-y-4">
          <div className="flex gap-3">
            {[{ v: 'api', l: '🌐 Fetch via API (Token + Query ID)' }, { v: 'paste', l: '📋 Paste XML from IB portal' }].map(o => (
              <button key={o.v} onClick={() => { setSourceMode(o.v as 'api' | 'paste'); fetchMut.reset(); parseMut.reset() }}
                className={`px-3 py-2 rounded-lg text-sm border transition-colors ${sourceMode === o.v ? 'border-blue-500 bg-blue-50 text-blue-700' : 'border-slate-200 text-slate-600 hover:bg-slate-50'}`}>
                {o.l}
              </button>
            ))}
          </div>

          {sourceMode === 'api' ? (
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-medium text-slate-600 mb-1">Flex Token</label>
                <div className="relative">
                  <input type={showToken ? 'text' : 'password'} value={token} onChange={e => setToken(e.target.value)}
                    className="w-full border border-slate-300 rounded px-2 py-1.5 text-sm pr-16" />
                  <button type="button" onClick={() => setShowToken(x => !x)}
                    className="absolute right-2 top-1/2 -translate-y-1/2 text-xs text-slate-500 hover:text-slate-700">
                    {showToken ? 'Hide' : 'Show'}
                  </button>
                </div>
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-600 mb-1">Query ID</label>
                <input type="text" value={queryId} onChange={e => setQueryId(e.target.value)}
                  className="w-full border border-slate-300 rounded px-2 py-1.5 text-sm" />
              </div>
            </div>
          ) : (
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Flex XML</label>
              <textarea value={rawXml} onChange={e => setRawXml(e.target.value)} rows={6}
                className="w-full border border-slate-300 rounded px-2 py-1.5 text-sm font-mono"
                placeholder="<FlexQueryResponse queryName=...>...</FlexQueryResponse>" />
            </div>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader><CardTitle>Account Mapping</CardTitle></CardHeader>
        <CardBody className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Import into account (investments)</label>
              <Select value={accountId ?? ''} onChange={e => setAccountId(Number(e.target.value))}>
                <option value="">— select account —</option>
                {brokerAccounts.map(a => <option key={a.id as number} value={a.id as number}>{a.name as string}</option>)}
              </Select>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Cash account for deposits/fees (optional)</label>
              <Select value={cashAccountId ?? ''} onChange={e => setCashAccountId(e.target.value ? Number(e.target.value) : null)}>
                <option value="">— None (use margin account) —</option>
                {cashAccounts.map(a => <option key={a.id as number} value={a.id as number}>{a.name as string}</option>)}
              </Select>
            </div>
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader><CardTitle>Date Filter</CardTitle></CardHeader>
        <CardBody>
          <p className="text-xs text-slate-500 mb-3">Use these filters to narrow the import to a specific period after the statement is fetched.</p>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Import from</label>
              <input type="date" value={filterFrom} onChange={e => setFilterFrom(e.target.value)}
                className="w-full border border-slate-300 rounded px-2 py-1.5 text-sm" />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Import to</label>
              <input type="date" value={filterTo} onChange={e => setFilterTo(e.target.value)}
                className="w-full border border-slate-300 rounded px-2 py-1.5 text-sm" />
            </div>
          </div>
        </CardBody>
      </Card>

      <div className="flex flex-col gap-2">
        <label className="flex items-center gap-2 text-sm text-slate-700 cursor-pointer">
          <input type="checkbox" checked={excludeFxSpot} onChange={e => {
            setExcludeFxSpot(e.target.checked)
            saveSettings({ token, query_id: queryId, account_id: accountId, cash_account_id: cashAccountId, exclude_fx_spot: e.target.checked })
          }} />
          Exclude FX Spot / currency-conversion trades (e.g. EUR.GBP, EUR.USD — IB's own housekeeping to fund foreign-currency buys, not real positions)
        </label>
        <label className="flex items-center gap-2 text-sm text-slate-700 cursor-pointer">
          <input type="checkbox" checked={replaceMode} onChange={e => setReplaceMode(e.target.checked)} />
          Replace mode (delete all existing IB records for this account)
        </label>
      </div>

      <div className="flex items-center gap-3">
        <Button
          onClick={() => sourceMode === 'api' ? fetchMut.mutate(false) : parseMut.mutate()}
          disabled={
          (sourceMode === 'api' ? (!token || !queryId || !accountId) : (!rawXml || !accountId))
          || fetchMut.isPending || parseMut.isPending
        }
        >
          {fetchMut.isPending || parseMut.isPending ? <><Spinner size={14} /> {sourceMode === 'api' ? 'Fetching…' : 'Parsing…'}</> : sourceMode === 'api' ? '📡 Fetch & Preview' : '🔍 Parse XML'}
        </Button>
        {sourceMode === 'api' && wasCached && (
          <button
            onClick={() => fetchMut.mutate(true)}
            disabled={fetchMut.isPending}
            className="text-xs text-blue-600 hover:underline"
          >
            ↻ Using today's cached statement — force a fresh fetch from IB instead
          </button>
        )}
      </div>
      {sourceMode === 'api' && wasCached && !fetchMut.isError && (
        <p className="text-xs text-slate-400">
          Reused the statement already fetched today — IB's Activity Statements only refresh once daily, so this avoids an unnecessary request.
        </p>
      )}
      {sourceMode === 'api' && fetchMut.isError && <ErrorBox msg={apiErrorMsg(fetchMut.error)} />}
      {sourceMode === 'paste' && parseMut.isError && <ErrorBox msg={apiErrorMsg(parseMut.error)} />}

      {parseResult && (
        <div className="space-y-4">
          {Object.keys(meta).length > 0 && (
            <div className="grid grid-cols-3 gap-3">
              {['account_id', 'from_date', 'to_date'].map(k => meta[k] != null && (
                <div key={k} className="bg-slate-50 rounded-lg p-3">
                  <p className="text-xs text-slate-500">{k.replace('_', ' ')}</p>
                  <p className="text-sm font-medium">{meta[k] as string}</p>
                </div>
              ))}
            </div>
          )}

          <div className="grid grid-cols-4 gap-3">
            {[
              { label: '🆕 New investments', value: newInv },
              { label: '🔄 Skip investments', value: visibleInv.length - newInv },
              { label: '🆕 New transactions', value: newTx },
              { label: '🔄 Skip transactions', value: visibleTx.length - newTx },
            ].map(s => (
              <div key={s.label} className="bg-slate-50 rounded-lg p-3">
                <p className="text-xs text-slate-500">{s.label}</p>
                <p className="text-2xl font-bold">{s.value}</p>
              </div>
            ))}
          </div>
          {(filterFrom || filterTo) && (inv.length !== visibleInv.length || tx.length !== visibleTx.length) && (
            <p className="text-xs text-amber-600 bg-amber-50 border border-amber-200 rounded px-3 py-2">
              ⚠️ Date filter active: showing {visibleInv.length} of {inv.length} investment records and {visibleTx.length} of {tx.length} cash records from the XML.
            </p>
          )}

          <Card>
            <CardHeader>
              <button className="flex items-center gap-2 w-full text-left" onClick={() => setExpandedInv(x => !x)}>
                <CardTitle>Preview — Investments ({visibleInv.length})</CardTitle>
                {expandedInv ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
              </button>
            </CardHeader>
            {expandedInv && (
              <CardBody>
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead><tr className="border-b border-slate-200 text-slate-500">
                      <th className="py-1 px-2 text-left">Status</th>
                      <th className="py-1 px-2 text-left">Date</th>
                      <th className="py-1 px-2 text-left">Action</th>
                      <th className="py-1 px-2 text-left">Symbol</th>
                      <th className="py-1 px-2 text-right">Qty</th>
                      <th className="py-1 px-2 text-right">Price</th>
                      <th className="py-1 px-2 text-right">Total (€)</th>
                    </tr></thead>
                    <tbody>
                      {visibleInv.slice(0, 100).map((r, i) => (
                        <tr key={i} className="border-b border-slate-100">
                          <td className="py-1 px-2"><StatusBadge status={r.status as string} /></td>
                          <td className="py-1 px-2">{r.date as string}</td>
                          <td className="py-1 px-2">{r.action as string}</td>
                          <td className="py-1 px-2 font-mono">{r.symbol as string}</td>
                          <td className="py-1 px-2 text-right">{fmtNum(Number(r.quantity ?? 0), 4)}</td>
                          <td className="py-1 px-2 text-right">{fmtNum(Number(r.price ?? 0), 4)}</td>
                          <td className="py-1 px-2 text-right">{fmtNum(Number(r.total_eur ?? 0), 2)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {visibleInv.length > 100 && <p className="text-xs text-slate-400 py-2 text-center">Showing 100 of {visibleInv.length} records</p>}
                </div>
              </CardBody>
            )}
          </Card>

          {visibleTx.length > 0 && (
            <Card>
              <CardHeader>
                <button className="flex items-center gap-2 w-full text-left" onClick={() => setExpandedTx(x => !x)}>
                  <CardTitle>Preview — Cash Transactions ({visibleTx.length})</CardTitle>
                  {expandedTx ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                </button>
              </CardHeader>
              {expandedTx && (
                <CardBody>
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                      <thead><tr className="border-b border-slate-200 text-slate-500">
                        <th className="py-1 px-2 text-left">Status</th>
                        <th className="py-1 px-2 text-left">Date</th>
                        <th className="py-1 px-2 text-left">Description</th>
                        <th className="py-1 px-2 text-right">Amount (€)</th>
                      </tr></thead>
                      <tbody>
                        {visibleTx.slice(0, 100).map((r, i) => (
                          <tr key={i} className="border-b border-slate-100">
                            <td className="py-1 px-2"><StatusBadge status={r.status as string} /></td>
                            <td className="py-1 px-2">{r.date as string}</td>
                            <td className="py-1 px-2">{r.description as string}</td>
                            <td className="py-1 px-2 text-right">{fmtNum(Number(r.amount ?? 0), 2)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {visibleTx.length > 100 && <p className="text-xs text-slate-400 py-2 text-center">Showing 100 of {visibleTx.length} records</p>}
                  </div>
                </CardBody>
              )}
            </Card>
          )}

          {Object.keys(secMatches).length > 0 && (
            <SecurityMappingPanel
              secMatches={secMatches}
              overrides={secMappingOverrides}
              onChange={(key, val) => setSecMappingOverrides(prev => ({ ...prev, [key]: val }))}
            />
          )}

          {newInv + newTx > 0 ? (
            <div className="flex gap-2 items-center">
              <Button onClick={() => importMut.mutate()} disabled={importMut.isPending}>
                {importMut.isPending ? <><Spinner size={14} /> Importing…</> : <>✅ Confirm Import ({newInv + newTx} records)</>}
              </Button>
            </div>
          ) : (
            <InfoBox>Nothing new to import. All records already exist in the database.</InfoBox>
          )}
          {importMut.isError && <ErrorBox msg={apiErrorMsg(importMut.error)} />}
          {importResult && <SuccessBox msg={`Import complete! Investments: ${(importResult as Record<string, unknown>).investments ?? 0} imported, ${(importResult as Record<string, unknown>).investments_skip ?? 0} skipped. Transactions: ${(importResult as Record<string, unknown>).transactions ?? 0} imported.`} />}
        </div>
      )}
    </div>
  )
}

// ── Brokerage → Revolut Trading ───────────────────────────────────────────────

function RevolutTradingTab() {
  const [file, setFile] = useState<File | null>(null)
  const [accountId, setAccountId] = useState<number | null>(null)
  const [replaceMode, setReplaceMode] = useState(false)
  const [importInv, setImportInv] = useState(true)
  const [importTx, setImportTx] = useState(true)
  const saveSettings = useImporterSettings('revt', s => {
    if (s.account_id) setAccountId(s.account_id as number)
  })
  const [parseResult, setParseResult] = useState<Record<string, unknown> | null>(null)
  const [importResult, setImportResult] = useState<Record<string, unknown> | null>(null)

  const { data: allAccounts = [] } = useQuery({ queryKey: ['all-accounts'], queryFn: getAllAccounts })
  const brokerAccounts = (allAccounts as Record<string, unknown>[]).filter(a =>
    ['Brokerage', 'Margin', 'Other Investment', 'Pension'].includes(a.type as string))

  const { data: linkedAccountData } = useQuery({
    queryKey: ['linked-account', accountId],
    queryFn: () => getLinkedAccount(accountId!),
    enabled: !!accountId,
  })
  const linkedAccountName = linkedAccountData?.linked_account_id
    ? (allAccounts as Record<string, unknown>[]).find(a => a.id === linkedAccountData.linked_account_id)?.name as string | undefined
    : undefined

  const [expandedInv, setExpandedInv] = useState(true)
  const [expandedTx, setExpandedTx] = useState(true)

  const parseMut = useMutation({
    mutationFn: () => revtParse(file!, accountId!),
    onSuccess: (d) => { setParseResult(d); saveSettings({ account_id: accountId }) },
  })

  const importMut = useMutation({
    mutationFn: () => revtImport(file!, accountId!, replaceMode, importInv, importTx),
    onSuccess: (d) => { setImportResult(d); saveSettings({ account_id: accountId }) },
  })

  const inv = (parseResult?.inv_records as Record<string, unknown>[]) ?? []
  const tx = (parseResult?.tx_records as Record<string, unknown>[]) ?? []
  const newInv = inv.filter(r => r.status === 'new').length
  const newTx = tx.filter(r => r.status === 'new').length
  const summary = parseResult?.summary as Record<string, unknown> ?? {}

  return (
    <div className="space-y-6">
      <details className="bg-slate-50 border border-slate-200 rounded-lg p-4">
        <summary className="cursor-pointer text-sm font-medium text-slate-700">ℹ️ How to export from Revolut Trading</summary>
        <ol className="mt-3 text-sm text-slate-600 space-y-1 list-decimal list-inside">
          <li>Open the Revolut app → tap <strong>Stocks</strong> (or Investing)</li>
          <li>Tap the <strong>clock / History</strong> icon (top-right)</li>
          <li>Tap the <strong>↓ Download / Export</strong> button</li>
          <li>Choose format <strong>CSV</strong> and a date range → tap Download</li>
        </ol>
      </details>

      <Card>
        <CardBody className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-slate-600 mb-1">Import into account</label>
            <Select value={accountId ?? ''} onChange={e => setAccountId(Number(e.target.value))}>
              <option value="">— select account —</option>
              {brokerAccounts.map(a => <option key={a.id as number} value={a.id as number}>{a.name as string}</option>)}
            </Select>
          </div>
          {file ? (
            <div className="flex items-center gap-3 p-3 bg-slate-50 rounded border border-slate-200">
              <CheckCircle size={16} className="text-green-500" />
              <span className="text-sm font-medium">{file.name}</span>
              <button className="ml-auto text-xs text-red-500" onClick={() => { setFile(null); setParseResult(null) }}>Remove</button>
            </div>
          ) : (
            <FileDropZone accept=".csv" onChange={setFile} label="Upload Revolut Trading CSV export" />
          )}
          <div className="space-y-2">
            <p className="text-xs font-medium text-slate-600">What to import</p>
            <div className="flex gap-4">
              <label className="flex items-center gap-2 text-sm text-slate-700 cursor-pointer">
                <input type="checkbox" checked={importInv} onChange={e => setImportInv(e.target.checked)} />
                Investments (trades, dividends)
              </label>
              <label className="flex items-center gap-2 text-sm text-slate-700 cursor-pointer">
                <input type="checkbox" checked={importTx} onChange={e => setImportTx(e.target.checked)} />
                Cash transactions (top-ups, withdrawals)
              </label>
            </div>
            {importTx && linkedAccountName && (
              <p className="text-xs text-blue-700 bg-blue-50 border border-blue-200 rounded px-3 py-2">
                ℹ️ Cash transactions will be recorded on <strong>{linkedAccountName}</strong> (linked cash account). Matching entries in that account will be auto-created.
              </p>
            )}
            {importTx && !linkedAccountName && accountId && (
              <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-2">
                ⚠️ No linked cash account configured for this brokerage account. Cash transactions will be recorded directly on the brokerage account.
              </p>
            )}
          </div>
          <div className="flex items-center gap-2">
            <input type="checkbox" id="revt_replace" checked={replaceMode} onChange={e => setReplaceMode(e.target.checked)} />
            <label htmlFor="revt_replace" className="text-sm text-slate-700">Replace mode (delete existing Revolut Trading records)</label>
          </div>
          <Button onClick={() => parseMut.mutate()} disabled={!file || !accountId || parseMut.isPending}>
            {parseMut.isPending ? <><Spinner size={14} /> Parsing…</> : <>🔍 Parse & Preview</>}
          </Button>
          {parseMut.isError && <ErrorBox msg={apiErrorMsg(parseMut.error)} />}
        </CardBody>
      </Card>

      {parseResult && (
        <div className="space-y-4">
          {summary.rows != null && (
            <div className="grid grid-cols-3 gap-3">
              <div className="bg-slate-50 rounded-lg p-3">
                <p className="text-xs text-slate-500">Rows in file</p>
                <p className="text-2xl font-bold">{summary.rows as number}</p>
              </div>
              <div className="bg-slate-50 rounded-lg p-3">
                <p className="text-xs text-slate-500">Date range from</p>
                <p className="text-sm font-medium">{summary.date_from as string}</p>
              </div>
              <div className="bg-slate-50 rounded-lg p-3">
                <p className="text-xs text-slate-500">Date range to</p>
                <p className="text-sm font-medium">{summary.date_to as string}</p>
              </div>
            </div>
          )}

          <div className="grid grid-cols-4 gap-3">
            {[
              { label: '🆕 New investments', value: newInv },
              { label: '🔄 Skip investments', value: inv.length - newInv },
              { label: '🆕 New transactions', value: newTx },
              { label: '🔄 Skip transactions', value: tx.length - newTx },
            ].map(s => (
              <div key={s.label} className="bg-slate-50 rounded-lg p-3">
                <p className="text-xs text-slate-500">{s.label}</p>
                <p className="text-2xl font-bold">{s.value}</p>
              </div>
            ))}
          </div>

          <Card>
            <CardHeader>
              <button className="flex items-center gap-2 w-full text-left" onClick={() => setExpandedInv(x => !x)}>
                <CardTitle>Preview — Investments ({inv.length})</CardTitle>
                {expandedInv ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
              </button>
            </CardHeader>
            {expandedInv && (
              <CardBody>
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead><tr className="border-b border-slate-200 text-slate-500">
                      <th className="py-1 px-2 text-left">Status</th>
                      <th className="py-1 px-2 text-left">Date</th>
                      <th className="py-1 px-2 text-left">Action</th>
                      <th className="py-1 px-2 text-left">Symbol</th>
                      <th className="py-1 px-2 text-right">Qty</th>
                      <th className="py-1 px-2 text-right">Price</th>
                      <th className="py-1 px-2 text-right">Total</th>
                    </tr></thead>
                    <tbody>
                      {inv.slice(0, 100).map((r, i) => (
                        <tr key={i} className="border-b border-slate-100">
                          <td className="py-1 px-2"><StatusBadge status={r.status as string} /></td>
                          <td className="py-1 px-2">{r.date as string}</td>
                          <td className="py-1 px-2">{r.action as string}</td>
                          <td className="py-1 px-2 font-mono">{r.symbol as string}</td>
                          <td className="py-1 px-2 text-right">{fmtNum(Number(r.quantity ?? 0), 4)}</td>
                          <td className="py-1 px-2 text-right">{fmtNum(Number(r.price ?? 0), 4)}</td>
                          <td className="py-1 px-2 text-right">{fmtNum(Number(r.total_eur ?? 0), 2)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {inv.length > 100 && <p className="text-xs text-slate-400 py-2 text-center">Showing 100 of {inv.length} records</p>}
                </div>
              </CardBody>
            )}
          </Card>

          {tx.length > 0 && (
            <Card>
              <CardHeader>
                <button className="flex items-center gap-2 w-full text-left" onClick={() => setExpandedTx(x => !x)}>
                  <CardTitle>Preview — Cash Transactions ({tx.length})</CardTitle>
                  {expandedTx ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                </button>
              </CardHeader>
              {expandedTx && (
                <CardBody>
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                      <thead><tr className="border-b border-slate-200 text-slate-500">
                        <th className="py-1 px-2 text-left">Status</th>
                        <th className="py-1 px-2 text-left">Date</th>
                        <th className="py-1 px-2 text-left">Description</th>
                        <th className="py-1 px-2 text-right">Amount (€)</th>
                      </tr></thead>
                      <tbody>
                        {tx.slice(0, 100).map((r, i) => (
                          <tr key={i} className="border-b border-slate-100">
                            <td className="py-1 px-2"><StatusBadge status={r.status as string} /></td>
                            <td className="py-1 px-2">{r.date as string}</td>
                            <td className="py-1 px-2">{r.description as string}</td>
                            <td className="py-1 px-2 text-right">{fmtNum(Number(r.amount ?? 0), 2)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {tx.length > 100 && <p className="text-xs text-slate-400 py-2 text-center">Showing 100 of {tx.length} records</p>}
                  </div>
                </CardBody>
              )}
            </Card>
          )}

          <div className="flex flex-wrap items-center gap-4 p-3 bg-slate-50 border border-slate-200 rounded-lg">
            <label className="flex items-center gap-2 text-sm text-slate-700 cursor-pointer">
              <input type="checkbox" checked={importInv} onChange={e => setImportInv(e.target.checked)} />
              Investments ({newInv} new)
            </label>
            <label className="flex items-center gap-2 text-sm text-slate-700 cursor-pointer">
              <input type="checkbox" checked={importTx} onChange={e => setImportTx(e.target.checked)} />
              Cash transactions ({newTx} new)
            </label>
            {linkedAccountName && importTx && (
              <span className="text-xs text-blue-600">→ will land on <strong>{linkedAccountName}</strong></span>
            )}
          </div>

          {(importInv ? newInv : 0) + (importTx ? newTx : 0) > 0 ? (
            <Button onClick={() => importMut.mutate()} disabled={importMut.isPending}>
              {importMut.isPending ? <><Spinner size={14} /> Importing…</> : <>✅ Confirm Import ({(importInv ? newInv : 0) + (importTx ? newTx : 0)} records)</>}
            </Button>
          ) : (
            <InfoBox>Nothing new to import.</InfoBox>
          )}
          {importMut.isError && <ErrorBox msg={apiErrorMsg(importMut.error)} />}
          {importResult && <SuccessBox msg={`Import complete! Investments: ${(importResult as Record<string, unknown>).investments ?? 0} imported, ${(importResult as Record<string, unknown>).investments_skip ?? 0} skipped. Transactions: ${(importResult as Record<string, unknown>).transactions ?? 0} imported.`} />}
        </div>
      )}
    </div>
  )
}

// ── Brokerage → Saxo Bank ─────────────────────────────────────────────────────

function SaxoTab() {
  const allAccounts = useQuery({ queryKey: ['allAccounts'], queryFn: getAllAccounts })
  const settingsQuery = useQuery({ queryKey: ['saxoSettings'], queryFn: saxoGetSettings })

  // Credentials
  const [appKey, setAppKey] = useState('')
  const [appSecret, setAppSecret] = useState('')
  const [useSim, setUseSim] = useState(false)
  const [redirectUri, setRedirectUri] = useState('http://localhost:8501')
  const [remember, setRemember] = useState(true)

  // Auth state
  const [accessToken, setAccessToken] = useState('')
  const [refreshToken, setRefreshToken] = useState('')
  const [expiresAt, setExpiresAt] = useState<number | null>(null)
  const [authCode, setAuthCode] = useState('')
  const [authUrl, setAuthUrl] = useState('')
  const [autoRefreshAttempted, setAutoRefreshAttempted] = useState(false)

  // Saxo accounts from API
  const [clientKey, setClientKey] = useState('')
  const [saxoAccounts, setSaxoAccounts] = useState<Array<{ AccountId: string; AccountKey: string; AccountType: string; Currency: string; app_account_id?: number }>>([])
  const [savedAccountMap, setSavedAccountMap] = useState<Record<string, number>>({})

  // Pre-fill credentials + account map from saved settings
  useEffect(() => {
    if (!settingsQuery.data) return
    const s = settingsQuery.data
    if (s.app_key) setAppKey(s.app_key)
    if (s.app_secret) setAppSecret(s.app_secret)
    if (s.redirect_uri) setRedirectUri(s.redirect_uri)
    setUseSim(!!s.use_sim)
    if (s.account_map && Object.keys(s.account_map).length > 0) setSavedAccountMap(s.account_map)
  }, [settingsQuery.data])

  // Auto-refresh token on load if we have a saved refresh token
  useEffect(() => {
    if (autoRefreshAttempted) return
    if (!settingsQuery.data) return
    const s = settingsQuery.data
    if (s.refresh_token && s.app_key && s.app_secret) {
      setAutoRefreshAttempted(true)
      setRefreshToken(s.refresh_token)
      // Attempt silent token refresh
      saxoRefreshToken({ app_key: s.app_key, app_secret: s.app_secret, refresh_token: s.refresh_token, use_sim: s.use_sim })
        .then((d: { access_token: string; refresh_token: string; expires_at: number }) => {
          setAccessToken(d.access_token)
          setRefreshToken(d.refresh_token)
          setExpiresAt(d.expires_at)
          // Fetch accounts right away so mapping is ready
          return saxoFetchAccounts(d.access_token, s.use_sim).then((ad: { client_key: string; accounts: Record<string, unknown>[] }) => {
            setClientKey(ad.client_key)
            const map = s.account_map as Record<string, number>
            setSaxoAccounts(ad.accounts.map((a: Record<string, unknown>) => ({
              ...a,
              app_account_id: map[a.AccountId as string] ?? undefined,
            })))
          })
        })
        .catch(() => { /* silent fail — user can re-auth manually */ })
    }
  }, [settingsQuery.data, autoRefreshAttempted])

  // Import options
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [replaceMode, setReplaceMode] = useState(false)
  const [importInv, setImportInv] = useState(true)
  const [importCharges, setImportCharges] = useState(true)

  // Preview + import
  const [preview, setPreview] = useState<{ inv_records: unknown[]; charge_records: unknown[]; sec_matches: Record<string, unknown> } | null>(null)
  const [fuzzySelected, setFuzzySelected] = useState<Set<number>>(new Set())
  const [importResult, setImportResult] = useState<Record<string, unknown> | null>(null)

  const isAuthenticated = !!accessToken && !!expiresAt && Date.now() / 1000 < expiresAt
  const minutesLeft = expiresAt ? Math.max(0, Math.round((expiresAt - Date.now() / 1000) / 60)) : 0

  const authUrlMut = useMutation({
    mutationFn: () => saxoGetAuthUrl(appKey, appSecret, redirectUri, useSim),
    onSuccess: (d) => setAuthUrl(d.url),
  })
  const exchangeMut = useMutation({
    mutationFn: () => saxoExchangeCode({ app_key: appKey, app_secret: appSecret, code: authCode, redirect_uri: redirectUri, use_sim: useSim, remember }),
    onSuccess: (d) => {
      setAccessToken(d.access_token); setRefreshToken(d.refresh_token); setExpiresAt(d.expires_at)
      setAuthCode(''); setAuthUrl('')
      fetchAccountsMut.mutate()
    },
  })
  const refreshMut = useMutation({
    mutationFn: () => saxoRefreshToken({ app_key: appKey, app_secret: appSecret, refresh_token: refreshToken, use_sim: useSim }),
    onSuccess: (d) => { setAccessToken(d.access_token); setRefreshToken(d.refresh_token); setExpiresAt(d.expires_at) },
  })
  const fetchAccountsMut = useMutation({
    mutationFn: () => saxoFetchAccounts(accessToken, useSim),
    onSuccess: (d) => {
      setClientKey(d.client_key)
      // Apply saved mapping when populating accounts
      const map = savedAccountMap
      setSaxoAccounts(d.accounts.map((a: Record<string, unknown>) => ({
        ...a,
        app_account_id: map[a.AccountId as string] ?? undefined,
      })))
    },
  })

  // Persist account mapping whenever it changes
  useEffect(() => {
    if (saxoAccounts.length === 0) return
    const map: Record<string, number> = {}
    saxoAccounts.forEach(a => { if (a.app_account_id) map[a.AccountId] = a.app_account_id })
    setSavedAccountMap(map)
    saxoSaveAccountMap(map).catch(() => {})
  }, [saxoAccounts])
  const fetchTradesMut = useMutation({
    mutationFn: () => saxoFetchTrades({ access_token: accessToken, client_key: clientKey, saxo_accounts: saxoAccounts, date_from: dateFrom, date_to: dateTo, use_sim: useSim }),
    onSuccess: (d) => {
      setPreview(d)
      // Default: all fuzzy dups selected (checked) — matches old behaviour
      const fuzzyIdxs = new Set((d.inv_records as Array<Record<string, unknown>>)
        .map((r, i) => r.status === 'likely_dup' ? i : -1).filter(i => i >= 0))
      setFuzzySelected(fuzzyIdxs)
    },
  })
  const importMut = useMutation({
    mutationFn: () => {
      const selectedDescs = preview
        ? (preview.inv_records as Array<Record<string, unknown>>)
            .filter((r, i) => r.status === 'new' || (r.status === 'likely_dup' && fuzzySelected.has(i)))
            .map(r => r.desc as string)
        : null
      return saxoImport({ access_token: accessToken, client_key: clientKey, saxo_accounts: saxoAccounts, date_from: dateFrom, date_to: dateTo, use_sim: useSim, replace_mode: replaceMode, import_inv: importInv, import_charges: importCharges, selected_descs: selectedDescs })
    },
    onSuccess: (d) => { setImportResult(d); setPreview(null) },
  })

  const statusColor = (s: string) => ({ new: 'text-green-600 bg-green-50', exists: 'text-slate-400 bg-slate-50', likely_dup: 'text-orange-500 bg-orange-50' }[s] || 'text-slate-600 bg-slate-100')
  const statusLabel = (s: string) => ({ new: 'New', exists: 'Exists', likely_dup: 'Fuzzy Dup' }[s] || s)

  return (
    <div className="space-y-4">
      <InfoBox>
        Import trading history from <strong>Saxo Bank</strong> via the OpenAPI (OAuth2 flow). Enter your app credentials, authorize, then fetch and import trades.
      </InfoBox>

      {/* Credentials */}
      <Card>
        <CardHeader><CardTitle>🔐 Credentials</CardTitle></CardHeader>
        <CardBody className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">App Key (Client ID)</label>
              <input className="w-full border rounded px-3 py-1.5 text-sm" placeholder="AppKey…" value={appKey} onChange={e => setAppKey(e.target.value)} />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">App Secret</label>
              <input type="password" className="w-full border rounded px-3 py-1.5 text-sm" placeholder="AppSecret…" value={appSecret} onChange={e => setAppSecret(e.target.value)} />
            </div>
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-600 mb-1">Redirect URI</label>
            <input className="w-full border rounded px-3 py-1.5 text-sm" value={redirectUri} onChange={e => setRedirectUri(e.target.value)} />
          </div>
          <div className="flex gap-4 text-sm">
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={useSim} onChange={e => setUseSim(e.target.checked)} className="rounded" />
              Use Simulation environment
            </label>
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={remember} onChange={e => setRemember(e.target.checked)} className="rounded" />
              Remember credentials
            </label>
          </div>
          {useSim && <div className="text-xs text-amber-600 bg-amber-50 border border-amber-200 rounded p-2">⚠️ Simulation mode active — connects to sim.logonvalidation.net</div>}
        </CardBody>
      </Card>

      {/* Authentication */}
      <Card>
        <CardHeader><CardTitle>🔑 Authentication</CardTitle></CardHeader>
        <CardBody className="space-y-3">
          {isAuthenticated ? (
            <div className="flex items-center gap-3">
              <span className="text-green-600 font-medium text-sm">✅ Authenticated</span>
              <span className="text-xs bg-slate-100 px-2 py-0.5 rounded">{useSim ? 'Simulation' : 'Live'}</span>
              <span className="text-xs text-slate-500">Token expires in {minutesLeft} min</span>
              {minutesLeft < 5 && (
                <Button size="sm" onClick={() => refreshMut.mutate()} disabled={refreshMut.isPending}>
                  {refreshMut.isPending ? <Spinner size={12} /> : <RefreshCw size={12} />} Refresh
                </Button>
              )}
              <button className="ml-auto text-xs text-red-500" onClick={() => { setAccessToken(''); setRefreshToken(''); setExpiresAt(null); setSaxoAccounts([]) }}>
                Logout
              </button>
            </div>
          ) : (
            <div className="space-y-3">
              <div className="flex gap-2">
                <Button onClick={() => authUrlMut.mutate()} disabled={!appKey || !appSecret || authUrlMut.isPending}>
                  {authUrlMut.isPending ? <Spinner size={14} /> : null} Get Authorization URL
                </Button>
                {authUrl && (
                  <a href={authUrl} target="_blank" rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 px-3 py-1.5 bg-blue-600 text-white text-sm rounded hover:bg-blue-700">
                    🔑 Authorize with Saxo
                  </a>
                )}
              </div>
              {authUrl && <p className="text-xs text-slate-500">After authorizing, paste the <code>code</code> parameter from the redirect URL below.</p>}
              <div className="flex gap-2">
                <input className="flex-1 border rounded px-3 py-1.5 text-sm" placeholder="Paste authorization code…" value={authCode} onChange={e => setAuthCode(e.target.value)} />
                <Button onClick={() => exchangeMut.mutate()} disabled={!authCode || !appKey || !appSecret || exchangeMut.isPending}>
                  {exchangeMut.isPending ? <Spinner size={14} /> : null} Exchange
                </Button>
              </div>
            </div>
          )}
          {(authUrlMut.isError || exchangeMut.isError || refreshMut.isError) && (
            <ErrorBox msg={(() => {
              const err = authUrlMut.error || exchangeMut.error || refreshMut.error
              if (!err) return ''
              const axiosErr = err as { response?: { data?: { detail?: string } }; message?: string }
              return axiosErr.response?.data?.detail || axiosErr.message || 'Unknown error'
            })()} />
          )}
        </CardBody>
      </Card>

      {/* Account Mapping */}
      {isAuthenticated && (
        <Card>
          <CardHeader>
            <CardTitle>🏦 Account Mapping</CardTitle>
            <Button size="sm" onClick={() => fetchAccountsMut.mutate()} disabled={fetchAccountsMut.isPending}>
              {fetchAccountsMut.isPending ? <Spinner size={12} /> : <RefreshCw size={12} />} Fetch Saxo Accounts
            </Button>
          </CardHeader>
          <CardBody>
            {fetchAccountsMut.isError && <ErrorBox msg={apiErrorMsg(fetchAccountsMut.error)} />}
            {saxoAccounts.length === 0 ? (
              <p className="text-sm text-slate-400">No accounts fetched yet. Click "Fetch Saxo Accounts" above.</p>
            ) : (
              <table className="w-full text-sm">
                <thead><tr className="text-xs text-slate-500 border-b">
                  <th className="text-left py-1 pr-3">Saxo Account</th>
                  <th className="text-left py-1 pr-3">Type</th>
                  <th className="text-left py-1 pr-3">Currency</th>
                  <th className="text-left py-1">Map to App Account</th>
                </tr></thead>
                <tbody>
                  {saxoAccounts.map((acc, i) => (
                    <tr key={acc.AccountId} className="border-b last:border-0">
                      <td className="py-1.5 pr-3 font-mono text-xs">{acc.AccountId}</td>
                      <td className="py-1.5 pr-3 text-slate-500">{acc.AccountType}</td>
                      <td className="py-1.5 pr-3 text-slate-500">{acc.Currency}</td>
                      <td className="py-1.5">
                        <Select value={acc.app_account_id ?? ''} onChange={e => {
                          const v = e.target.value ? Number(e.target.value) : undefined
                          setSaxoAccounts(prev => prev.map((a, j) => j === i ? { ...a, app_account_id: v } : a))
                        }}>
                          <option value="">— not mapped —</option>
                          {(allAccounts.data || []).map((a: { id: number; name: string }) => (
                            <option key={a.id} value={a.id}>{a.name}</option>
                          ))}
                        </Select>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </CardBody>
        </Card>
      )}

      {/* Import Options */}
      {isAuthenticated && saxoAccounts.some(a => a.app_account_id) && (
        <Card>
          <CardHeader><CardTitle>📅 Import Options</CardTitle></CardHeader>
          <CardBody className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-medium text-slate-600 mb-1">Date From</label>
                <input type="date" className="w-full border rounded px-3 py-1.5 text-sm" value={dateFrom} onChange={e => setDateFrom(e.target.value)} />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-600 mb-1">Date To</label>
                <input type="date" className="w-full border rounded px-3 py-1.5 text-sm" value={dateTo} onChange={e => setDateTo(e.target.value)} />
              </div>
            </div>
            <div className="flex gap-4 text-sm">
              <label className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" checked={replaceMode} onChange={e => setReplaceMode(e.target.checked)} className="rounded" />
                Replace mode (re-import existing)
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" checked={importInv} onChange={e => setImportInv(e.target.checked)} className="rounded" />
                Import trades
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" checked={importCharges} onChange={e => setImportCharges(e.target.checked)} className="rounded" />
                Import charges
              </label>
            </div>
            <Button onClick={() => { setPreview(null); setImportResult(null); fetchTradesMut.mutate() }}
              disabled={fetchTradesMut.isPending}>
              {fetchTradesMut.isPending ? <><Spinner size={14} /> Fetching…</> : <>🔍 Fetch & Preview</>}
            </Button>
            {fetchTradesMut.isError && <ErrorBox msg={apiErrorMsg(fetchTradesMut.error)} />}
          </CardBody>
        </Card>
      )}

      {/* Preview */}
      {preview && (
        <Card>
          <CardHeader>
            {(() => {
              const recs = preview.inv_records as Array<Record<string, unknown>>
              const importCount = recs.filter((r, i) => r.status === 'new' || (r.status === 'likely_dup' && fuzzySelected.has(i))).length
              return <CardTitle>Preview — {recs.length} trades, {(preview.charge_records as unknown[]).length} charges · <span className="text-green-600">{importCount} to import</span></CardTitle>
            })()}
          </CardHeader>
          <CardBody className="space-y-3">
            {(preview.inv_records as unknown[]).length > 0 && (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead><tr className="text-slate-500 border-b">
                    {['Import', 'Status', 'Date', 'Account', 'Action', 'Security', 'Qty', 'Price', 'Amount'].map(h => (
                      <th key={h} className="text-left py-1 pr-3">{h}</th>
                    ))}
                  </tr></thead>
                  <tbody>
                    {(preview.inv_records as Array<Record<string, unknown>>).map((r, i) => {
                      const isFuzzy = r.status === 'likely_dup'
                      const isNew = r.status === 'new'
                      const willImport = isNew || (isFuzzy && fuzzySelected.has(i))
                      return (
                        <tr key={i} className={`border-b last:border-0 ${!willImport && r.status !== 'exists' ? 'opacity-50' : ''}`}>
                          <td className="py-1 pr-3">
                            {isNew ? (
                              <span className="text-xs text-slate-400">✓</span>
                            ) : isFuzzy ? (
                              <input type="checkbox" checked={fuzzySelected.has(i)}
                                onChange={e => setFuzzySelected(prev => {
                                  const next = new Set(prev)
                                  e.target.checked ? next.add(i) : next.delete(i)
                                  return next
                                })} className="rounded" />
                            ) : (
                              <span className="text-xs text-slate-300">—</span>
                            )}
                          </td>
                          <td className="py-1 pr-3">
                            <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${statusColor(r.status as string)}`}>
                              {statusLabel(r.status as string)}
                            </span>
                          </td>
                          <td className="py-1 pr-3 font-mono">{r.date as string}</td>
                          <td className="py-1 pr-3">{r.account as string}</td>
                          <td className="py-1 pr-3">{r.action as string}</td>
                          <td className="py-1 pr-3">{r.symbol as string ?? r.description as string}</td>
                          <td className="py-1 pr-3 text-right">{typeof r.quantity === 'number' ? fmtNum(r.quantity, 4) : ''}</td>
                          <td className="py-1 pr-3 text-right">{typeof r.price === 'number' ? fmtNum(r.price, 4) : ''}</td>
                          <td className="py-1 pr-3 text-right">{typeof r.total === 'number' ? fmtNum(r.total, 2) : ''}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
            {(() => {
              const recs = preview.inv_records as Array<Record<string, unknown>>
              const importCount = recs.filter((r, i) => r.status === 'new' || (r.status === 'likely_dup' && fuzzySelected.has(i))).length
              return (
                <Button onClick={() => importMut.mutate()} disabled={importMut.isPending || importCount === 0}>
                  {importMut.isPending ? <><Spinner size={14} /> Importing…</> : <>✅ Confirm Import ({importCount} records)</>}
                </Button>
              )
            })()}
            {importMut.isError && <ErrorBox msg={apiErrorMsg(importMut.error)} />}
          </CardBody>
        </Card>
      )}

      {importResult && (
        <SuccessBox msg={
          (importResult.imported as number ?? 0) === 0 && (importResult.investments_inserted as number ?? 0) === 0
            ? `All records already exist in the database — nothing new to import.`
            : `Import complete. Investments: ${importResult.investments_inserted ?? importResult.imported ?? 0} inserted. Charges: ${importResult.charges_inserted ?? 0} inserted.`
        } />
      )}

      {/* PDF Reconciliation for charges/fees */}
      <SaxoPdfSection allAccounts={allAccounts.data || []} saxoAccounts={saxoAccounts} />
    </div>
  )
}

function SaxoPdfSection({ allAccounts, saxoAccounts }: { allAccounts: Array<{ id: number; name: string }>; saxoAccounts: Array<{ AccountId: string; app_account_id?: number }> }) {
  const qc = useQueryClient()
  const [pdfFile, setPdfFile] = useState<File | null>(null)
  const [pdfAccountId, setPdfAccountId] = useState<number | ''>('')
  const [replaceMode, setReplaceMode] = useState(false)
  const [pdfPreview, setPdfPreview] = useState<{ records: Array<Record<string, unknown>>; sec_matches: Record<string, unknown> } | null>(null)
  const [pdfResult, setPdfResult] = useState<Record<string, unknown> | null>(null)

  // Pre-fill account from saxo mapping if only one mapped
  const mappedAppId = saxoAccounts.find(a => a.app_account_id)?.app_account_id
  const effectiveAccountId = pdfAccountId !== '' ? pdfAccountId : (mappedAppId ?? '')

  const settingsQuery = useQuery({ queryKey: ['saxoSettings'], queryFn: saxoGetSettings })
  const { data: payees = [] } = useQuery({ queryKey: ['payees'], queryFn: getPayees })
  const chargePayeeId: number | '' = settingsQuery.data?.charge_payee_id ?? ''
  const chargePayeeMut = useMutation({
    mutationFn: (payeeId: number | null) => saxoSaveChargePayee(payeeId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['saxoSettings'] }),
  })

  const previewMut = useMutation({
    mutationFn: () => saxoPdfPreview(pdfFile!),
    onSuccess: setPdfPreview,
  })
  const importMut = useMutation({
    mutationFn: () => saxoPdfImport(pdfFile!, effectiveAccountId as number, '', replaceMode),
    onSuccess: (d) => { setPdfResult(d); setPdfPreview(null) },
  })

  const statusColor = (s: string) => s === 'exists' ? 'text-slate-400 bg-slate-50' : 'text-green-600 bg-green-50'
  const newCount = pdfPreview ? pdfPreview.records.filter(r => r.status === 'new').length : 0

  return (
    <Card>
      <CardHeader><CardTitle>💸 Account Charges (CFD Finance · Dividends · Fees)</CardTitle></CardHeader>
      <CardBody className="space-y-3">
        <InfoBox>
          The Saxo <code>/cs/v1/reports/trades/</code> endpoint returns trade executions only — overnight CFD financing, custody fees, dividends, and other account entries are in the <strong>Transaction and Balance Report PDF</strong> (Saxo → My Portfolio → Reports).
        </InfoBox>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-medium text-slate-600 mb-1">Target App Account</label>
            <Select value={effectiveAccountId} onChange={e => setPdfAccountId(e.target.value ? Number(e.target.value) : '')}>
              <option value="">— select account —</option>
              {allAccounts.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
            </Select>
          </div>
          <div className="flex items-end">
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input type="checkbox" checked={replaceMode} onChange={e => setReplaceMode(e.target.checked)} className="rounded" />
              Replace mode
            </label>
          </div>
        </div>

        <div>
          <label className="block text-xs font-medium text-slate-600 mb-1">
            Charge Payee <span className="font-normal text-slate-400">— used for account-level entries (VAT, CustodyFee, FinancingCost, …) that have no security</span>
          </label>
          <Select
            value={chargePayeeId}
            onChange={e => chargePayeeMut.mutate(e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">— default (auto-create "Saxo Bank") —</option>
            {(payees as Record<string, unknown>[]).map(p => (
              <option key={String(p.id)} value={String(p.id)}>{String(p.name)}</option>
            ))}
          </Select>
        </div>

        {pdfFile ? (
          <div className="flex items-center gap-3 p-3 bg-slate-50 rounded border border-slate-200">
            <CheckCircle size={16} className="text-green-500" />
            <span className="text-sm font-medium">{pdfFile.name}</span>
            <button className="ml-auto text-xs text-red-500" onClick={() => { setPdfFile(null); setPdfPreview(null); setPdfResult(null) }}>Remove</button>
          </div>
        ) : (
          <FileDropZone accept=".pdf" onChange={f => { setPdfFile(f); setPdfPreview(null); setPdfResult(null) }}
            label="Upload Transaction and Balance Report (PDF)" />
        )}

        {pdfFile && (
          <Button onClick={() => { setPdfResult(null); previewMut.mutate() }} disabled={previewMut.isPending}>
            {previewMut.isPending ? <><Spinner size={14} /> Parsing…</> : <>🔍 Preview Charges</>}
          </Button>
        )}
        {previewMut.isError && <ErrorBox msg={apiErrorMsg(previewMut.error)} />}

        {pdfPreview && (
          <div className="space-y-3">
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead><tr className="text-slate-500 border-b">
                  {['Status', 'Date', 'Type', 'Description', 'Amount', 'Currency'].map(h => (
                    <th key={h} className="text-left py-1 pr-3">{h}</th>
                  ))}
                </tr></thead>
                <tbody>
                  {pdfPreview.records.map((r, i) => (
                    <tr key={i} className="border-b last:border-0">
                      <td className="py-1 pr-3">
                        <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${statusColor(r.status as string)}`}>
                          {r.status === 'exists' ? 'Exists' : 'New'}
                        </span>
                      </td>
                      <td className="py-1 pr-3 font-mono">{r.date as string}</td>
                      <td className="py-1 pr-3">{r.charge_type as string}</td>
                      <td className="py-1 pr-3 max-w-xs truncate">{r.name as string ?? r.desc as string}</td>
                      <td className="py-1 pr-3 text-right">{typeof r.total_eur === 'number' ? fmtNum(r.total_eur, 2) : ''}</td>
                      <td className="py-1 pr-3">{r.currency as string}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Button onClick={() => importMut.mutate()} disabled={importMut.isPending || !effectiveAccountId || newCount === 0}>
              {importMut.isPending ? <><Spinner size={14} /> Importing…</> : <>✅ Import {newCount} new charges</>}
            </Button>
            {importMut.isError && <ErrorBox msg={apiErrorMsg(importMut.error)} />}
          </div>
        )}

        {pdfResult && (
          <SuccessBox msg={`PDF import complete. ${pdfResult.imported ?? 0} charges inserted, ${pdfResult.skipped ?? 0} skipped.`} />
        )}
      </CardBody>
    </Card>
  )
}

// ── Brokerage → Coinbase ──────────────────────────────────────────────────────

function CoinbaseTab() {
  const allAccounts = useQuery({ queryKey: ['allAccounts'], queryFn: getAllAccounts })
  const settingsQuery = useQuery({ queryKey: ['coinbaseSettings'], queryFn: coinbaseGetSettings })

  const [apiKey, setApiKey] = useState('')
  const [apiSecret, setApiSecret] = useState('')
  const [remember, setRemember] = useState(true)
  const [accountId, setAccountId] = useState<number | ''>('')
  const [cashAccountId, setCashAccountId] = useState<number | ''>('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [replaceMode, setReplaceMode] = useState(false)

  const [testResult, setTestResult] = useState<Array<Record<string, unknown>> | null>(null)
  const [preview, setPreview] = useState<{ raw_count: number; inv_records: Array<Record<string, unknown>>; tx_records: Array<Record<string, unknown>>; sec_matches: Record<string, unknown> } | null>(null)
  const [fuzzyInvSelected, setFuzzyInvSelected] = useState<Set<number>>(new Set())
  const [fuzzyTxSelected, setFuzzyTxSelected] = useState<Set<number>>(new Set())
  const [importResult, setImportResult] = useState<Record<string, unknown> | null>(null)

  useEffect(() => {
    if (!settingsQuery.data) return
    const s = settingsQuery.data
    if (s.api_key)    setApiKey(s.api_key)
    if (s.api_secret) setApiSecret(s.api_secret)
    if (s.account_id)      setAccountId(Number(s.account_id))
    if (s.cash_account_id) setCashAccountId(Number(s.cash_account_id))
  }, [settingsQuery.data])

  const testMut = useMutation({
    mutationFn: () => coinbaseTest({ api_key: apiKey, api_secret: apiSecret, remember }),
    onSuccess: (d) => setTestResult(d.accounts),
  })
  const fetchMut = useMutation({
    mutationFn: () => coinbaseFetch({ api_key: apiKey, api_secret: apiSecret, account_id: accountId || null, cash_account_id: cashAccountId || null, date_from: dateFrom, date_to: dateTo, remember }),
    onSuccess: (d) => {
      setPreview(d)
      setFuzzyInvSelected(new Set((d.inv_records as Array<Record<string, unknown>>).map((r, i) => r.status === 'likely_dup' ? i : -1).filter((i: number) => i >= 0)))
      setFuzzyTxSelected(new Set((d.tx_records as Array<Record<string, unknown>>).map((r, i) => r.status === 'likely_dup' ? i : -1).filter((i: number) => i >= 0)))
    },
  })
  const importMut = useMutation({
    mutationFn: () => {
      const invRecs = preview!.inv_records
      const txRecs  = preview!.tx_records
      return coinbaseImport({
        api_key: apiKey, api_secret: apiSecret,
        account_id: accountId || null, cash_account_id: cashAccountId || null,
        date_from: dateFrom, date_to: dateTo, replace_mode: replaceMode,
        selected_inv: invRecs.filter((r, i) => r.status === 'new' || (r.status === 'likely_dup' && fuzzyInvSelected.has(i))).map(r => r.desc),
        selected_tx:  txRecs.filter((r, i)  => r.status === 'new' || (r.status === 'likely_dup' && fuzzyTxSelected.has(i))).map(r => r.desc),
      })
    },
    onSuccess: (d) => { setImportResult(d); setPreview(null) },
  })

  const sc = (s: string) => ({ new: 'text-green-600 bg-green-50', exists: 'text-slate-400 bg-slate-50', likely_dup: 'text-orange-500 bg-orange-50' }[s] ?? 'text-slate-600 bg-slate-100')
  const sl = (s: string) => ({ new: 'New', exists: 'Exists', likely_dup: 'Fuzzy Dup' }[s] ?? s)

  const PreviewTable = ({ records, fuzzySelected, setFuzzySelected, cols }: {
    records: Array<Record<string, unknown>>; fuzzySelected: Set<number>; setFuzzySelected: (s: Set<number>) => void
    cols: Array<{ key: string; label: string; right?: boolean }>
  }) => (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead><tr className="text-slate-500 border-b">
          <th className="text-left py-1 pr-3">Import</th>
          <th className="text-left py-1 pr-3">Status</th>
          {cols.map(c => <th key={c.key} className={`${c.right ? 'text-right' : 'text-left'} py-1 pr-3`}>{c.label}</th>)}
        </tr></thead>
        <tbody>
          {records.map((r, i) => {
            const isFuzzy = r.status === 'likely_dup'; const isNew = r.status === 'new'
            return (
              <tr key={i} className={`border-b last:border-0 ${!isNew && !isFuzzy ? 'opacity-40' : ''}`}>
                <td className="py-1 pr-3">
                  {isNew ? <span className="text-xs text-slate-400">✓</span>
                    : isFuzzy ? <input type="checkbox" checked={fuzzySelected.has(i)} className="rounded"
                        onChange={e => { const n = new Set(fuzzySelected); e.target.checked ? n.add(i) : n.delete(i); setFuzzySelected(n) }} />
                    : <span className="text-xs text-slate-300">—</span>}
                </td>
                <td className="py-1 pr-3"><span className={`px-1.5 py-0.5 rounded text-xs font-medium ${sc(r.status as string)}`}>{sl(r.status as string)}</span></td>
                {cols.map(c => <td key={c.key} className={`py-1 pr-3 ${c.right ? 'text-right font-mono' : ''}`}>{String(r[c.key] ?? '')}</td>)}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )

  return (
    <div className="space-y-4">
      <InfoBox>
        Import trades, staking rewards, and transfers from <strong>Coinbase</strong> using the REST API. Staking rewards are recorded as <strong>Reinvest</strong> (new units received as income, cost basis set at FMV on receipt date).
      </InfoBox>

      <details className="border rounded-lg p-3 text-sm">
        <summary className="cursor-pointer text-slate-600 font-medium">ℹ️ How to create Coinbase API keys (click to expand)</summary>
        <div className="mt-3 space-y-2 text-xs text-slate-600">
          <p><strong>Option A — CDP / Cloud API keys (recommended)</strong></p>
          <p>Go to <code>cloud.coinbase.com/access/api</code> → Create API Key. Enable <code>wallet:accounts:read</code> + <code>wallet:transactions:read</code>. Paste the <strong>Key Name</strong> into API Key and the full <strong>PEM block</strong> into API Secret.</p>
          <p><strong>Option B — Legacy API keys</strong></p>
          <p>Log in to coinbase.com → avatar → Settings → API → New API Key. Tick the read permissions and paste the short key + secret below.</p>
        </div>
      </details>

      {/* Credentials */}
      <Card>
        <CardHeader><CardTitle>🔑 API Credentials</CardTitle></CardHeader>
        <CardBody className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">API Key (or CDP Key Name)</label>
              <input className="w-full border rounded px-3 py-1.5 text-sm font-mono" placeholder="organizations/…/apiKeys/…" value={apiKey} onChange={e => setApiKey(e.target.value)} />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">API Secret (or CDP Private Key PEM)</label>
              <textarea className="w-full border rounded px-3 py-1.5 text-sm font-mono h-20 resize-none" placeholder="-----BEGIN EC PRIVATE KEY-----…" value={apiSecret} onChange={e => setApiSecret(e.target.value)} />
            </div>
          </div>
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input type="checkbox" checked={remember} onChange={e => setRemember(e.target.checked)} className="rounded" />
            💾 Remember credentials (stored in app settings)
          </label>
          <div className="flex gap-2">
            <Button onClick={() => testMut.mutate()} disabled={!apiKey || !apiSecret || testMut.isPending} size="sm">
              {testMut.isPending ? <Spinner size={12} /> : null} 🔌 Test Connection
            </Button>
          </div>
          {testMut.isError && <ErrorBox msg={apiErrorMsg(testMut.error)} />}
          {testResult && (
            <div className="text-xs text-green-700 bg-green-50 border border-green-200 rounded p-2">
              ✅ Connected — {testResult.length} account(s): {testResult.map(a => `${a.currency_code as string} (${(a.balance as number)?.toFixed(4)})`).join(', ')}
            </div>
          )}
        </CardBody>
      </Card>

      {/* Account Mapping */}
      <Card>
        <CardHeader><CardTitle>🏦 Account Mapping</CardTitle></CardHeader>
        <CardBody className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Investment account (buys, sells, staking)</label>
              <Select value={accountId} onChange={e => setAccountId(e.target.value ? Number(e.target.value) : '')}>
                <option value="">— select account —</option>
                {(allAccounts.data || []).map((a: { id: number; name: string }) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </Select>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Cash account <span className="text-slate-400">(optional — fiat deposits/withdrawals)</span></label>
              <Select value={cashAccountId} onChange={e => setCashAccountId(e.target.value ? Number(e.target.value) : '')}>
                <option value="">— none (use investment account) —</option>
                {(allAccounts.data || []).map((a: { id: number; name: string }) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </Select>
            </div>
          </div>
        </CardBody>
      </Card>

      {/* Date + Options */}
      <Card>
        <CardHeader><CardTitle>📅 Date Filter & Options</CardTitle></CardHeader>
        <CardBody className="space-y-3">
          <p className="text-xs text-slate-500">The Coinbase API has no server-side date filter — all pages are fetched then filtered client-side. Set a narrow range to reduce fetch time.</p>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">From</label>
              <input type="date" className="w-full border rounded px-3 py-1.5 text-sm" value={dateFrom} onChange={e => setDateFrom(e.target.value)} />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">To</label>
              <input type="date" className="w-full border rounded px-3 py-1.5 text-sm" value={dateTo} onChange={e => setDateTo(e.target.value)} />
            </div>
          </div>
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input type="checkbox" checked={replaceMode} onChange={e => setReplaceMode(e.target.checked)} className="rounded" />
            Replace mode — delete all existing Coinbase records before importing
          </label>
          <Button onClick={() => { setPreview(null); setImportResult(null); fetchMut.mutate() }}
            disabled={!apiKey || !apiSecret || !accountId || fetchMut.isPending}>
            {fetchMut.isPending ? <><Spinner size={14} /> Fetching…</> : <>📡 Fetch & Preview</>}
          </Button>
          {fetchMut.isError && <ErrorBox msg={apiErrorMsg(fetchMut.error)} />}
        </CardBody>
      </Card>

      {/* Preview */}
      {preview && (() => {
        const invImportCount = preview.inv_records.filter((r, i) => r.status === 'new' || (r.status === 'likely_dup' && fuzzyInvSelected.has(i))).length
        const txImportCount  = preview.tx_records.filter((r, i)  => r.status === 'new' || (r.status === 'likely_dup' && fuzzyTxSelected.has(i))).length
        return (
          <Card>
            <CardHeader>
              <CardTitle>Preview — {preview.raw_count} raw · {preview.inv_records.length} inv · {preview.tx_records.length} tx · <span className="text-green-600">{invImportCount + txImportCount} to import</span></CardTitle>
            </CardHeader>
            <CardBody className="space-y-4">
              {preview.inv_records.length > 0 && (
                <>
                  <p className="text-xs font-medium text-slate-600">Investment records</p>
                  <PreviewTable records={preview.inv_records} fuzzySelected={fuzzyInvSelected} setFuzzySelected={setFuzzyInvSelected}
                    cols={[{ key: 'date', label: 'Date' }, { key: 'action', label: 'Action' }, { key: 'symbol', label: 'Symbol' }, { key: 'quantity', label: 'Qty', right: true }, { key: 'total_eur', label: 'Amount', right: true }]} />
                </>
              )}
              {preview.tx_records.length > 0 && (
                <>
                  <p className="text-xs font-medium text-slate-600">Cash transactions</p>
                  <PreviewTable records={preview.tx_records} fuzzySelected={fuzzyTxSelected} setFuzzySelected={setFuzzyTxSelected}
                    cols={[{ key: 'date', label: 'Date' }, { key: 'description', label: 'Description' }, { key: 'amount', label: 'Amount', right: true }, { key: 'currency', label: 'Ccy' }]} />
                </>
              )}
              <Button onClick={() => importMut.mutate()} disabled={importMut.isPending || (invImportCount + txImportCount === 0)}>
                {importMut.isPending ? <><Spinner size={14} /> Importing…</> : <>✅ Confirm Import ({invImportCount + txImportCount} records)</>}
              </Button>
              {importMut.isError && <ErrorBox msg={apiErrorMsg(importMut.error)} />}
            </CardBody>
          </Card>
        )
      })()}

      {importResult && (
        <SuccessBox msg={`Import complete. Investments: ${importResult.investments ?? 0} imported. Transactions: ${importResult.transactions ?? 0} imported. Skipped: ${importResult.investments_skip ?? 0}.`} />
      )}
    </div>
  )
}

// ── Brokerage → Crypto.com ────────────────────────────────────────────────────

function CryptoComTab() {
  const { data: allAccounts = [] } = useQuery({ queryKey: ['allAccounts'], queryFn: getAllAccounts })
  const [file, setFile] = useState<File | null>(null)
  const [accountId, setAccountId] = useState<number | null>(null)
  const [result, setResult] = useState<Record<string, unknown> | null>(null)
  const saveSettings = useImporterSettings('cryptocom', s => { if (s.account_id) setAccountId(s.account_id as number) })
  const importMut = useMutation({
    mutationFn: () => { const fd = new FormData(); fd.append('file', file!); return importFile('cryptocom', fd) },
    onSuccess: (d) => { setResult(d); saveSettings({ account_id: accountId }) },
  })
  return (
    <div className="space-y-4">
      <InfoBox>
        Import trading history from <strong>Crypto.com Exchange</strong>.<br />
        In Crypto.com Exchange: <strong>Orders → Trade History → Export → CSV</strong>
      </InfoBox>
      <Card>
        <CardBody className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-slate-600 mb-1">Target Account</label>
            <Select value={accountId ?? ''} onChange={e => setAccountId(Number(e.target.value))}>
              <option value="">— select account —</option>
              {(allAccounts as Record<string, unknown>[]).map(a => <option key={a.id as number} value={a.id as number}>{a.name as string}</option>)}
            </Select>
          </div>
          {file ? (
            <div className="flex items-center gap-3 p-3 bg-slate-50 rounded border border-slate-200">
              <CheckCircle size={16} className="text-green-500" />
              <span className="text-sm font-medium">{file.name}</span>
              <button className="ml-auto text-xs text-red-500" onClick={() => { setFile(null); setResult(null) }}>Remove</button>
            </div>
          ) : (
            <FileDropZone accept=".csv" onChange={setFile} label="Upload Crypto.com CSV export" />
          )}
          <Button onClick={() => importMut.mutate()} disabled={!file || importMut.isPending}>
            {importMut.isPending ? <><Spinner size={14} /> Importing…</> : <>Import</>}
          </Button>
          {importMut.isError && <ErrorBox msg={apiErrorMsg(importMut.error)} />}
          {result && <SuccessBox msg={result.message as string ?? `Imported ${result.imported ?? 0}, skipped ${result.skipped ?? 0}`} />}
        </CardBody>
      </Card>
    </div>
  )
}

// ── Brokerage → Capital.com ───────────────────────────────────────────────────

function CapitalComTab() {
  const { data: allAccounts = [] } = useQuery({ queryKey: ['allAccounts'], queryFn: getAllAccounts })
  const [file, setFile] = useState<File | null>(null)
  const [accountId, setAccountId] = useState<number | null>(null)
  const [result, setResult] = useState<Record<string, unknown> | null>(null)
  const saveSettings = useImporterSettings('capitalcom', s => { if (s.account_id) setAccountId(s.account_id as number) })
  const importMut = useMutation({
    mutationFn: () => { const fd = new FormData(); fd.append('file', file!); return importFile('capitalcom', fd) },
    onSuccess: (d) => { setResult(d); saveSettings({ account_id: accountId }) },
  })
  return (
    <div className="space-y-4">
      <InfoBox>Import trading history from a <strong>Capital.com</strong> CSV export.<br />In Capital.com: <strong>History → Download report (CSV)</strong></InfoBox>
      <Card>
        <CardBody className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-slate-600 mb-1">Target Account</label>
            <Select value={accountId ?? ''} onChange={e => setAccountId(Number(e.target.value))}>
              <option value="">— select account —</option>
              {(allAccounts as Record<string, unknown>[]).map(a => <option key={a.id as number} value={a.id as number}>{a.name as string}</option>)}
            </Select>
          </div>
          {file ? (
            <div className="flex items-center gap-3 p-3 bg-slate-50 rounded border border-slate-200">
              <CheckCircle size={16} className="text-green-500" />
              <span className="text-sm font-medium">{file.name}</span>
              <button className="ml-auto text-xs text-red-500" onClick={() => setFile(null)}>Remove</button>
            </div>
          ) : (
            <FileDropZone accept=".csv" onChange={setFile} label="Upload Capital.com CSV export" />
          )}
          <Button onClick={() => importMut.mutate()} disabled={!file || importMut.isPending}>
            {importMut.isPending ? <><Spinner size={14} /> Importing…</> : <>Import</>}
          </Button>
          {importMut.isError && <ErrorBox msg={apiErrorMsg(importMut.error)} />}
          {result && <SuccessBox msg={result.message as string ?? `Imported ${result.imported ?? 0}, skipped ${result.skipped ?? 0}`} />}
        </CardBody>
      </Card>
    </div>
  )
}

// ── Brokerage → FXPro ─────────────────────────────────────────────────────────

function FxProTab() {
  const { data: allAccounts = [] } = useQuery({ queryKey: ['allAccounts'], queryFn: getAllAccounts })
  const [file, setFile] = useState<File | null>(null)
  const [accountId, setAccountId] = useState<number | null>(null)
  const [result, setResult] = useState<Record<string, unknown> | null>(null)
  const saveSettings = useImporterSettings('fxpro', s => { if (s.account_id) setAccountId(s.account_id as number) })
  const importMut = useMutation({
    mutationFn: () => { const fd = new FormData(); fd.append('file', file!); return importFile('fxpro', fd) },
    onSuccess: (d) => { setResult(d); saveSettings({ account_id: accountId }) },
  })
  return (
    <div className="space-y-4">
      <InfoBox>Import trading history from an <strong>FxPro</strong> PDF statement.</InfoBox>
      <Card>
        <CardBody className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-slate-600 mb-1">Target Account</label>
            <Select value={accountId ?? ''} onChange={e => setAccountId(Number(e.target.value))}>
              <option value="">— select account —</option>
              {(allAccounts as Record<string, unknown>[]).map(a => <option key={a.id as number} value={a.id as number}>{a.name as string}</option>)}
            </Select>
          </div>
          {file ? (
            <div className="flex items-center gap-3 p-3 bg-slate-50 rounded border border-slate-200">
              <CheckCircle size={16} className="text-green-500" />
              <span className="text-sm font-medium">{file.name}</span>
              <button className="ml-auto text-xs text-red-500" onClick={() => setFile(null)}>Remove</button>
            </div>
          ) : (
            <FileDropZone accept=".pdf" onChange={setFile} label="Upload FxPro PDF statement" />
          )}
          <Button onClick={() => importMut.mutate()} disabled={!file || importMut.isPending}>
            {importMut.isPending ? <><Spinner size={14} /> Importing…</> : <>Import</>}
          </Button>
          {importMut.isError && <ErrorBox msg={apiErrorMsg(importMut.error)} />}
          {result && <SuccessBox msg={result.message as string ?? `Imported ${result.imported ?? 0}, skipped ${result.skipped ?? 0}`} />}
        </CardBody>
      </Card>
    </div>
  )
}

// ── QIF Importer ──────────────────────────────────────────────────────────────

function QIFTab() {
  const [file, setFile] = useState<File | null>(null)
  const [result, setResult] = useState<Record<string, unknown> | null>(null)
  const importMut = useMutation({
    mutationFn: () => { const fd = new FormData(); fd.append('file', file!); return importFile('qif', fd) },
    onSuccess: setResult,
  })
  return (
    <div className="space-y-4">
      <InfoBox>
        Import transactions from a <strong>QIF</strong> (Quicken Interchange Format) file.<br />
        Most personal finance apps (Quicken, MS Money, Banktivity) support QIF export.
      </InfoBox>
      <Card>
        <CardBody className="space-y-4">
          {file ? (
            <div className="flex items-center gap-3 p-3 bg-slate-50 rounded border border-slate-200">
              <CheckCircle size={16} className="text-green-500" />
              <span className="text-sm font-medium">{file.name}</span>
              <button className="ml-auto text-xs text-red-500" onClick={() => setFile(null)}>Remove</button>
            </div>
          ) : (
            <FileDropZone accept=".qif" onChange={setFile} label="Upload QIF file" />
          )}
          <Button onClick={() => importMut.mutate()} disabled={!file || importMut.isPending}>
            {importMut.isPending ? <><Spinner size={14} /> Importing…</> : <>Import</>}
          </Button>
          {importMut.isError && <ErrorBox msg={apiErrorMsg(importMut.error)} />}
          {result && <SuccessBox msg={result.message as string ?? `Imported ${result.imported ?? 0}, skipped ${result.skipped ?? 0}`} />}
        </CardBody>
      </Card>
    </div>
  )
}

// ── QIF Transfer Issues ───────────────────────────────────────────────────────

function TransferIssuesTab() {
  return (
    <div className="space-y-4">
      <InfoBox>
        Review and fix transfer transaction issues that arise when importing QIF files with inter-account transfers.
        Transfer records that couldn't be matched to both sides appear here for manual resolution.
      </InfoBox>
      <Card>
        <CardBody>
          <p className="text-sm text-slate-500 py-4 text-center">Transfer issue detection coming soon.</p>
        </CardBody>
      </Card>
    </div>
  )
}

// ── Main Importers Page ───────────────────────────────────────────────────────

const BANK_TABS = ['📥 Import & Reconcile', '🏦 Salt Edge', '💚 Revolut Personal', '🐣 Revolut Savings', '⚙️ Import Profiles', '🏷️ Payee Rules', '📋 Import History']
const BROKERAGE_TABS = ['📊 Interactive Brokers', '💚 Revolut Trading', '📈 Saxo Bank', '₿ Coinbase', '🔷 Crypto.com', '📉 Capital.com', '💱 FXPro']
const QIF_TABS = ['📁 QIF Importer', '🔁 Transfer Issues']

export default function Importers() {
  const [section, setSection] = usePersist<'bank' | 'brokerage' | 'qif'>('importers_section', 'bank')
  const [bankTab, setBankTab] = usePersist('importers_bank_tab', BANK_TABS[0])
  const [brokerTab, setBrokerTab] = usePersist('importers_broker_tab', BROKERAGE_TABS[0])
  const [qifTab, setQifTab] = usePersist('importers_qif_tab', QIF_TABS[0])

  return (
    <div>
      <PageHeader title="Importers" subtitle="Import transactions from banks and brokers" />

      <div className="px-6 py-4">
        {/* Section selector */}
        <div className="flex gap-2 mb-6">
          {([
            { key: 'bank', label: '🏦 Bank' },
            { key: 'brokerage', label: '📊 Brokerage' },
            { key: 'qif', label: '📁 QIF' },
          ] as { key: 'bank' | 'brokerage' | 'qif'; label: string }[]).map(s => (
            <button key={s.key} onClick={() => setSection(s.key)}
              className={`px-5 py-2.5 rounded-lg font-medium text-sm border transition-colors ${section === s.key
                ? 'bg-blue-600 text-white border-blue-600'
                : 'bg-white text-slate-600 border-slate-200 hover:bg-slate-50'}`}>
              {s.label}
            </button>
          ))}
        </div>

        {/* Bank section */}
        {section === 'bank' && (
          <>
            <SubTabs tabs={BANK_TABS} active={bankTab} onChange={setBankTab} />
            {bankTab === BANK_TABS[0] && <ImportReconcileTab />}
            {bankTab === BANK_TABS[1] && <SaltEdgeTab />}
            {bankTab === BANK_TABS[2] && <RevolutPersonalTab />}
            {bankTab === BANK_TABS[3] && <RevolutSavingsTab />}
            {bankTab === BANK_TABS[4] && <ImportProfilesTab />}
            {bankTab === BANK_TABS[5] && <PayeeRulesTab />}
            {bankTab === BANK_TABS[6] && <ImportHistoryTab />}
          </>
        )}

        {/* Brokerage section */}
        {section === 'brokerage' && (
          <>
            <SubTabs tabs={BROKERAGE_TABS} active={brokerTab} onChange={setBrokerTab} />
            {brokerTab === BROKERAGE_TABS[0] && <IBFlexTab />}
            {brokerTab === BROKERAGE_TABS[1] && <RevolutTradingTab />}
            {brokerTab === BROKERAGE_TABS[2] && <SaxoTab />}
            {brokerTab === BROKERAGE_TABS[3] && <CoinbaseTab />}
            {brokerTab === BROKERAGE_TABS[4] && <CryptoComTab />}
            {brokerTab === BROKERAGE_TABS[5] && <CapitalComTab />}
            {brokerTab === BROKERAGE_TABS[6] && <FxProTab />}
          </>
        )}

        {/* QIF section */}
        {section === 'qif' && (
          <>
            <SubTabs tabs={QIF_TABS} active={qifTab} onChange={setQifTab} />
            {qifTab === QIF_TABS[0] && <QIFTab />}
            {qifTab === QIF_TABS[1] && <TransferIssuesTab />}
          </>
        )}
      </div>
    </div>
  )
}
