import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bell, Pencil, Plus, SkipForward, Trash2, Undo2, Upload, X } from 'lucide-react'
import { PageHeader, Button, Input, Select, Spinner } from '@/components/ui'
import {
  deletePlan, deletePlanItem, getPlanItems, importPlan, remindPlanNow, savePlanItem, setPlanStatus, type PlanItem,
} from '@/lib/api'
import { usePersist } from '@/lib/hooks'
import { todayLocal } from '@/lib/utils'
import { daysFromToday, planDayLabel } from '@/lib/plan'

// Dated to-do steps — a rebalancing plan, a tax-year checklist — with a Telegram reminder every morning (the Plan Reminders job, Tools → Scheduler).
// Tick a step when it is done; skip one you decided against; both leave the reminders.

const field = 'text-xs font-medium text-slate-500 block mb-1'

function StepModal({ item, plans, defaultPlan, onClose, onSave, saving, error }: {
  item: PlanItem | null; plans: string[]; defaultPlan: string; onClose: () => void
  onSave: (b: { plan: string; due_date: string; title: string; details: string }) => void; saving: boolean; error: string
}) {
  const [plan, setPlan] = useState(item?.plan ?? defaultPlan)
  const [due, setDue] = useState(item?.due_date ?? todayLocal())
  const [title, setTitle] = useState(item?.title ?? '')
  const [details, setDetails] = useState(item?.details ?? '')
  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-lg max-h-[90dvh] overflow-y-auto">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200">
          <h2 className="text-base font-semibold">{item ? 'Edit step' : 'New step'}</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600"><X size={18} /></button>
        </div>
        <div className="px-5 py-4 space-y-3">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className={field}>Plan</label>
              <Input list="plan-names" value={plan} onChange={e => setPlan(e.target.value)} placeholder="My plan" />
              <datalist id="plan-names">{plans.map(p => <option key={p} value={p} />)}</datalist>
            </div>
            <div>
              <label className={field}>Due date *</label>
              <Input type="date" value={due} onChange={e => setDue(e.target.value)} />
            </div>
          </div>
          <div>
            <label className={field}>What to do *</label>
            <Input value={title} onChange={e => setTitle(e.target.value)} placeholder="e.g. Sell 25 TRI, buy VWCE and 4GLD" autoFocus />
          </div>
          <div>
            <label className={field}>Details (shown in the reminder)</label>
            <textarea className="block w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm min-h-24" value={details} onChange={e => setDetails(e.target.value)}
              placeholder="Amounts, accounts, limit prices, why…" />
          </div>
          {error && <p className="text-sm text-red-600">{error}</p>}
        </div>
        <div className="flex justify-end gap-2 px-5 py-3 border-t border-slate-200">
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button disabled={saving || !title.trim() || !due} onClick={() => onSave({ plan, due_date: due, title, details })}>{saving ? 'Saving…' : 'Save'}</Button>
        </div>
      </div>
    </div>
  )
}

function ImportModal({ defaultPlan, onClose, onDone }: { defaultPlan: string; onClose: () => void; onDone: () => void }) {
  const [text, setText] = useState('')
  const [plan, setPlan] = useState(defaultPlan === 'all' ? '' : defaultPlan)
  const [result, setResult] = useState<{ created: number; errors: string[] } | null>(null)
  const run = useMutation({ mutationFn: () => importPlan(text, plan), onSuccess: r => { setResult(r); if (r.created) onDone() } })
  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl max-h-[90dvh] overflow-y-auto">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200">
          <h2 className="text-base font-semibold">Import steps</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600"><X size={18} /></button>
        </div>
        <div className="px-5 py-4 space-y-3">
          <p className="text-xs text-slate-500">
            One step per line: <code>date | what to do | details</code> (details optional). Dates can be <code>2026-10-26</code>, <code>26/10/2026</code>, <code>26 Oct</code> or <code>Mon 26 Oct 2026</code>.
            A line starting with <code>#</code> names the plan for the lines after it.
          </p>
          <div>
            <label className={field}>Plan name (used when the text has no # line)</label>
            <Input value={plan} onChange={e => setPlan(e.target.value)} placeholder="My plan" />
          </div>
          <textarea className="block w-full rounded-md border border-slate-300 px-3 py-2 text-sm font-mono min-h-56" value={text} onChange={e => { setText(e.target.value); setResult(null) }}
            placeholder={'# Rebalance Oct–Dec 2026\n2026-10-26 | Sell 25 TRI | €1,375 VWCE + €1,375 4GLD\n2026-10-30 | T-bill matures | €5k T-bill, €5k XEON, €5k EXVM'} />
          {run.isError && <p className="text-sm text-red-600">Could not import.</p>}
          {result && (
            <div className="text-sm">
              <p className={result.created ? 'text-green-700' : 'text-slate-600'}>{result.created} step{result.created === 1 ? '' : 's'} added.</p>
              {result.errors.length > 0 && <ul className="text-red-600 text-xs mt-1 list-disc pl-5">{result.errors.map(e => <li key={e}>{e}</li>)}</ul>}
            </div>
          )}
        </div>
        <div className="flex justify-end gap-2 px-5 py-3 border-t border-slate-200">
          <Button variant="secondary" onClick={onClose}>{result?.created ? 'Close' : 'Cancel'}</Button>
          <Button disabled={run.isPending || !text.trim()} onClick={() => run.mutate()}>{run.isPending ? 'Importing…' : 'Import'}</Button>
        </div>
      </div>
    </div>
  )
}

export default function Plan() {
  const qc = useQueryClient()
  const today = todayLocal()
  const [plan, setPlan] = usePersist('plan_selected', 'all')
  const [edit, setEdit] = useState<PlanItem | 'new' | null>(null)
  const [importing, setImporting] = useState(false)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const { data: items = [], isLoading } = useQuery({ queryKey: ['plan-items'], queryFn: getPlanItems, staleTime: 30_000 })
  const refresh = () => { qc.invalidateQueries({ queryKey: ['plan-items'] }); qc.invalidateQueries({ queryKey: ['plan-due'] }) }

  const save = useMutation({
    mutationFn: (b: { plan: string; due_date: string; title: string; details: string }) => savePlanItem(edit !== 'new' && edit ? edit.id : null, b),
    onSuccess: () => { setEdit(null); setErr(''); refresh() },
    onError: (e: unknown) => setErr((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Could not save.'),
  })
  const status = useMutation({ mutationFn: ({ id, s }: { id: number; s: PlanItem['status'] }) => setPlanStatus(id, s), onSuccess: refresh })
  const remove = useMutation({ mutationFn: (id: number) => deletePlanItem(id), onSuccess: refresh })
  const removePlan = useMutation({ mutationFn: (name: string) => deletePlan(name), onSuccess: () => { setPlan('all'); refresh() } })
  const remind = useMutation({
    mutationFn: remindPlanNow,
    onSuccess: r => setMsg({ ok: true, text: `Sent to Telegram — ${r.detail}.` }),
    onError: (e: unknown) => setMsg({ ok: false, text: (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Could not send.' }),
  })

  const plans = useMemo(() => [...new Set(items.map(i => i.plan))], [items])
  const selected = plan === 'all' || plans.includes(plan) ? plan : 'all'
  const shown = items.filter(i => selected === 'all' || i.plan === selected)
  const open = shown.filter(i => i.status === 'open')
  const closed = shown.filter(i => i.status !== 'open').sort((a, b) => (b.done_at ?? '').localeCompare(a.done_at ?? ''))
  const overdue = open.filter(i => i.due_date < today)
  const dueToday = open.filter(i => i.due_date === today)
  const later = open.filter(i => i.due_date > today)
  const byDay = useMemo(() => {
    const m = new Map<string, PlanItem[]>()
    later.forEach(i => m.set(i.due_date, [...(m.get(i.due_date) ?? []), i]))
    return [...m.entries()]
  }, [later])

  const row = (i: PlanItem, tone: 'overdue' | 'today' | 'later' | 'closed') => (
    <div key={i.id} className={`flex items-start gap-3 rounded-lg border px-3 py-2.5 bg-white ${tone === 'overdue' ? 'border-red-200' : tone === 'today' ? 'border-blue-300' : 'border-slate-200'} ${tone === 'closed' ? 'opacity-70' : ''}`}>
      <input type="checkbox" className="mt-1 rounded shrink-0 w-4 h-4" checked={i.status === 'done'} disabled={status.isPending}
        onChange={() => status.mutate({ id: i.id, s: i.status === 'open' ? 'done' : 'open' })} title={i.status === 'open' ? 'Mark as done' : 'Reopen'} />
      <div className="min-w-0 flex-1">
        <p className={`text-sm font-medium ${i.status === 'open' ? 'text-slate-800' : 'text-slate-500 line-through'}`}>{i.title}</p>
        {i.details && <p className="text-xs text-slate-500 mt-0.5 whitespace-pre-wrap">{i.details}</p>}
        <p className="text-[11px] text-slate-400 mt-1">
          {tone === 'overdue' && <span className="text-red-600 font-medium">{-daysFromToday(i.due_date, today)} day{daysFromToday(i.due_date, today) === -1 ? '' : 's'} overdue · </span>}
          {planDayLabel(i.due_date)}
          {selected === 'all' && plans.length > 1 ? ` · ${i.plan}` : ''}
          {i.status === 'skipped' ? ' · skipped' : ''}
        </p>
      </div>
      <div className="flex items-center gap-0.5 shrink-0 -mr-1">
        {i.status === 'open'
          ? <button className="p-1.5 text-slate-400 hover:text-slate-700" title="Skip — I decided not to do this" onClick={() => status.mutate({ id: i.id, s: 'skipped' })}><SkipForward size={14} /></button>
          : i.status === 'skipped' && <button className="p-1.5 text-slate-400 hover:text-slate-700" title="Reopen" onClick={() => status.mutate({ id: i.id, s: 'open' })}><Undo2 size={14} /></button>}
        <button className="p-1.5 text-slate-400 hover:text-slate-700" title="Edit" onClick={() => { setErr(''); setEdit(i) }}><Pencil size={14} /></button>
        <button className="p-1.5 text-slate-400 hover:text-red-600" title="Delete" onClick={() => { if (confirm(`Delete “${i.title}”?`)) remove.mutate(i.id) }}><Trash2 size={14} /></button>
      </div>
    </div>
  )
  const section = (label: string, list: PlanItem[], tone: 'overdue' | 'today' | 'later' | 'closed', color = 'text-slate-500') =>
    list.length > 0 && (
      <section className="space-y-1.5">
        <h2 className={`text-xs uppercase tracking-wide font-semibold ${color}`}>{label} <span className="font-normal text-slate-400">{list.length}</span></h2>
        {list.map(i => row(i, tone))}
      </section>
    )

  return (
    <div>
      <PageHeader title="Plan" subtitle="Dated steps, reminded on Telegram every morning"
        actions={<>
          <Button size="sm" variant="secondary" onClick={() => setImporting(true)}><Upload size={13} /> Import</Button>
          <Button size="sm" variant="secondary" disabled={remind.isPending} onClick={() => { setMsg(null); remind.mutate() }}
            title="Send today's reminder to Telegram now (steps due today or overdue, and tomorrow's)"><Bell size={13} /> Remind me now</Button>
          <Button size="sm" onClick={() => { setErr(''); setEdit('new') }}><Plus size={13} /> New step</Button>
        </>} />
      <div className="px-4 sm:px-6 py-4 space-y-5 max-w-3xl">
        {msg && <p className={`text-sm ${msg.ok ? 'text-green-700' : 'text-red-600'}`}>{msg.text}</p>}
        {isLoading ? <div className="flex items-center gap-2 py-6 text-sm text-slate-400"><Spinner size={14} /> Loading…</div>
          : items.length === 0 ? (
            <div className="text-center py-12 text-sm text-slate-500 space-y-2">
              <p>No steps yet.</p>
              <p className="text-xs text-slate-400">Add one with <b>New step</b>, or paste a whole plan with <b>Import</b>. Each morning (08:00 by default — Tools → Scheduler → Plan Reminders) Oikos messages you on Telegram with what is due.</p>
            </div>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-2 text-sm">
                {plans.length > 1 && (
                  <Select className="!w-auto" value={selected} onChange={e => setPlan(e.target.value)}>
                    <option value="all">All plans</option>
                    {plans.map(p => <option key={p} value={p}>{p}</option>)}
                  </Select>
                )}
                <span className="text-slate-500">{open.length} open{overdue.length ? <> · <span className="text-red-600 font-medium">{overdue.length} overdue</span></> : ''}{closed.length ? ` · ${closed.length} closed` : ''}</span>
                {selected !== 'all' && (
                  <button className="ml-auto text-xs text-slate-400 hover:text-red-600 underline"
                    onClick={() => { if (confirm(`Delete the whole plan “${selected}” (${shown.length} steps)?`)) removePlan.mutate(selected) }}>Delete this plan</button>
                )}
              </div>
              {open.length === 0 && <p className="text-sm text-green-700">Nothing left to do in {selected === 'all' ? 'any plan' : 'this plan'}. 🎉</p>}
              {section('Overdue', overdue, 'overdue', 'text-red-600')}
              {section('Today', dueToday, 'today', 'text-blue-700')}
              {byDay.map(([d, list]) => section(planDayLabel(d), list, 'later'))}
              {closed.length > 0 && (
                <details className="space-y-1.5">
                  <summary className="text-xs uppercase tracking-wide font-semibold text-slate-400 cursor-pointer">Done and skipped ({closed.length})</summary>
                  <div className="space-y-1.5 mt-1.5">{closed.map(i => row(i, 'closed'))}</div>
                </details>
              )}
            </>
          )}
      </div>
      {edit && <StepModal item={edit === 'new' ? null : edit} plans={plans} defaultPlan={selected === 'all' ? (plans[0] ?? '') : selected}
        onClose={() => setEdit(null)} onSave={b => save.mutate(b)} saving={save.isPending} error={err} />}
      {importing && <ImportModal defaultPlan={selected} onClose={() => setImporting(false)} onDone={refresh} />}
    </div>
  )
}
