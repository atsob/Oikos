import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Plot from '@/components/Plot'
import { Button, Card, CardBody, CardHeader, CardTitle, Input, Select, Spinner, Tooltip } from '@/components/ui'
import { useTheme } from '@/lib/theme'
import { usePersist } from '@/lib/hooks'
import { fmtEur, plotLayout } from '@/lib/utils'
import {
  compareRetirement, deleteRetirementPlan, getRetirementAssets, getRetirementPlans, getRetirementStart, projectRetirement, retirementLevers, saveRetirementPlan,
  type RetirementCompareRow, type RetirementLevers, type RetirementParams, type RetirementPlan, type RetirementResult, type RetirementStream,
} from '@/lib/api'

// The Retirement Planner: your money projected to the end of the plan, with the chance it lasts (Monte Carlo), what it would take to fix a
// shortfall, saved scenarios and what-if comparisons. The maths is on the server (database/retirement.py); this screen is the inputs and the results.

const THIS_YEAR = new Date().getFullYear()

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v))
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)

function setIn<T>(obj: T, path: (string | number)[], value: unknown): T {
  const out = clone(obj) as Record<string, unknown>
  let cur: Record<string, unknown> = out
  path.slice(0, -1).forEach(k => { cur = cur[k as string] as Record<string, unknown> })
  cur[path[path.length - 1] as string] = value
  return out as T
}

// A saved plan only holds what it set; anything a newer version added comes from the defaults.
function fill<T extends Record<string, unknown>>(base: T, over: Record<string, unknown> | undefined): T {
  const out = clone(base) as Record<string, unknown>
  for (const [k, v] of Object.entries(over ?? {})) {
    const b = out[k]
    out[k] = v && typeof v === 'object' && !Array.isArray(v) && b && typeof b === 'object' && !Array.isArray(b) ? fill(b as Record<string, unknown>, v as Record<string, unknown>) : v
  }
  return out as T
}

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value)
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t) }, [value, ms])
  return v
}

const eur = (v: number | null | undefined) => v == null ? '—' : fmtEur(v)

// ── small form pieces ────────────────────────────────────────────────────────────────────────────

function Field({ label, tip, children, className = '' }: { label: string; tip?: string; children: React.ReactNode; className?: string }) {
  return (
    <label className={`block ${className}`}>
      <span className="text-xs text-slate-500 block mb-1">{tip ? <Tooltip text={tip}>{label}</Tooltip> : label}</span>
      {children}
    </label>
  )
}

function Num({ label, tip, value, onChange, step = 1, suffix, min, className }: {
  label: string; tip?: string; value: number | null; onChange: (v: number) => void; step?: number; suffix?: string; min?: number; className?: string
}) {
  return (
    <Field label={label} tip={tip} className={className}>
      <div className="flex items-center gap-1.5">
        <Input type="number" value={value ?? ''} step={step} min={min} onChange={e => onChange(Number(e.target.value))} />
        {suffix && <span className="text-xs text-slate-400 whitespace-nowrap">{suffix}</span>}
      </div>
    </Field>
  )
}

function Section({ title, hint, children, open = true }: { title: string; hint?: string; children: React.ReactNode; open?: boolean }) {
  return (
    <details open={open} className="border border-slate-200 rounded-lg bg-white">
      <summary className="px-3 py-2 cursor-pointer text-sm font-semibold text-slate-700 select-none">{title}{hint && <span className="ml-2 font-normal text-xs text-slate-400">{hint}</span>}</summary>
      <div className="px-3 pb-3 pt-1 space-y-3">{children}</div>
    </details>
  )
}

const streamPresets = (statePension: string): { label: string; stream: (age: number) => RetirementStream }[] => [
  { label: statePension, stream: a => ({ name: statePension, annual: 9000, start_age: a, end_age: null, indexation: 'inflation', tax: 'income', source_rate: 0 }) },
  { label: 'Occupational / supplementary pension', stream: a => ({ name: 'Occupational pension', annual: 3000, start_age: a, end_age: null, indexation: 'inflation', tax: 'income', source_rate: 0 }) },
  { label: 'Pension from another EU country', stream: a => ({ name: 'Foreign pension', annual: 5000, start_age: a, end_age: null, indexation: 'inflation', tax: 'source', source_rate: 15 }) },
  { label: 'Rental income', stream: a => ({ name: 'Rental income', annual: 6000, start_age: a, end_age: null, indexation: 'inflation', tax: 'source', source_rate: 15 }) },
  { label: 'Other income', stream: a => ({ name: 'Other income', annual: 3000, start_age: a, end_age: null, indexation: 'inflation', tax: 'income', source_rate: 0 }) },
]

const ORDERS: { id: string; label: string; order: RetirementParams['order'] }[] = [
  { id: 'cit', label: 'Cash, then investments, then pension pots', order: ['cash', 'investments', 'pot'] },
  { id: 'cpi', label: 'Cash, then pension pots, then investments', order: ['cash', 'pot', 'investments'] },
  { id: 'ict', label: 'Investments, then cash, then pension pots', order: ['investments', 'cash', 'pot'] },
  { id: 'ipc', label: 'Investments, then pension pots, then cash', order: ['investments', 'pot', 'cash'] },
  { id: 'pci', label: 'Pension pots, then cash, then investments', order: ['pot', 'cash', 'investments'] },
  { id: 'pic', label: 'Pension pots, then investments, then cash', order: ['pot', 'investments', 'cash'] },
]

// ── what-if variants of the plan on screen ───────────────────────────────────────────────────────

function variants(p: RetirementParams): { name: string; params: RetirementParams }[] {
  const v = (name: string, f: (q: RetirementParams) => void) => { const q = clone(p); f(q); return { name, params: q } }
  const out = [
    v('Retire 2 years earlier', q => { q.retire_age = Math.max(q.retire_age - 2, THIS_YEAR - q.birth_year) }),
    v('Retire 2 years later', q => { q.retire_age += 2 }),
    v('Save 25% more', q => { q.contrib.investments *= 1.25; q.contrib.cash *= 1.25; q.contrib.pot *= 1.25 }),
    v('Save 25% less', q => { q.contrib.investments *= 0.75; q.contrib.cash *= 0.75; q.contrib.pot *= 0.75 }),
    v('Spend 10% less', q => { q.spend.annual *= 0.9 }),
    v('Spend 10% more', q => { q.spend.annual *= 1.1 }),
    v('Returns 2 points lower', q => { q.returns.pre_pct -= 2; q.returns.post_pct -= 2 }),
    v('Inflation 1 point higher', q => { q.inflation_pct += 1 }),
    v('Live 5 years longer', q => { q.end_age += 5 }),
  ]
  if (p.streams.length) out.push(v('No pensions or other income', q => { q.streams = [] }))
  return out
}

// ── the screen ───────────────────────────────────────────────────────────────────────────────────

export default function RetirementPlanner({ renderPresetBar }: { renderPresetBar?: (onChange: (ids: number[] | undefined) => void) => ReactNode }) {
  const { isDark } = useTheme()
  const qc = useQueryClient()
  const start = useQuery({ queryKey: ['retirement-start'], queryFn: getRetirementStart, staleTime: 5 * 60_000 })
  const plansQ = useQuery({ queryKey: ['retirement-plans'], queryFn: getRetirementPlans })
  const plans = plansQ.data ?? []

  const [params, setParams] = useState<RetirementParams | null>(null)
  const [planId, setPlanId] = useState<number | null>(null)
  const [saved, setSaved] = useState<RetirementParams | null>(null)           // what the plan on screen looked like when loaded / last saved
  const [naming, setNaming] = useState<string | null>(null)                   // "Save as" in progress
  const [showTable, setShowTable] = useState(false)
  const [real, setReal] = useState(true)
  const [levers, setLevers] = useState<RetirementLevers | null>(null)
  const [msg, setMsg] = useState<string | null>(null)

  // "What you have today" can follow an Account Preset (the same presets as the Net Worth report): the amounts come from the accounts it selects.
  const [presetName] = usePersist<string>('preset_sel_retirement', 'Full Portfolio')
  const [presetIds, setPresetIds] = useState<number[] | undefined>(undefined)
  const seenPreset = useRef<string | null>(null)
  const [applyFor, setApplyFor] = useState<string | null>(null)
  const assetsQ = useQuery({ queryKey: ['retirement-assets', presetIds ?? null], queryFn: () => getRetirementAssets(presetIds), staleTime: 60_000 })
  const onPreset = (ids: number[] | undefined) => {
    setPresetIds(ids)
    const key = JSON.stringify(ids ?? null)
    if (seenPreset.current === null) { seenPreset.current = key; return }       // the bar reports its saved choice on opening: that is not a change
    if (key !== seenPreset.current) { seenPreset.current = key; setApplyFor(key) }
  }

  const defaults = start.data?.params
  const load = (plan: RetirementPlan | null) => {
    if (!defaults) return
    const p = plan ? fill(defaults as unknown as Record<string, unknown>, plan.params as Record<string, unknown>) as unknown as RetirementParams : clone(defaults)
    setParams(p); setSaved(clone(p)); setPlanId(plan?.id ?? null); setLevers(null); setMsg(null)
  }

  // Open the base plan, or one built from your data, once both are in.
  useEffect(() => {
    if (params || !defaults || !plansQ.isSuccess) return
    load(plans.find(p => p.is_base) ?? plans[0] ?? null)
  }, [defaults, plansQ.isSuccess]) // eslint-disable-line react-hooks/exhaustive-deps

  // A preset chosen (or its accounts changed) on screen puts its amounts into the plan.
  useEffect(() => {
    if (!applyFor || !assetsQ.data || JSON.stringify(presetIds ?? null) !== applyFor) return
    const a = assetsQ.data.assets
    setParams(p => p && {
      ...p, assets_source: presetIds ? presetName : undefined,
      assets: { cash: Math.round(a.cash), investments: Math.round(a.investments), pot: Math.round(a.pot), investments_cost: a.investments_cost != null ? Math.round(a.investments_cost) : null },
    })
    setLevers(null); setApplyFor(null)
  }, [applyFor, assetsQ.data, presetIds, presetName])

  const dirty = !!params && !!saved && !same(params, saved)
  const debounced = useDebounced(params, 450)
  const proj = useQuery({
    queryKey: ['retirement-project', debounced],
    queryFn: () => projectRetirement(debounced, 1000),
    enabled: !!debounced,
    placeholderData: prev => prev,
  })
  const result: RetirementResult | undefined = proj.data
  const errorMsg = (proj.error as { response?: { data?: { detail?: string } } } | null)?.response?.data?.detail

  const set = (path: (string | number)[], value: unknown) => { setParams(p => p && setIn(p, path, value)); setLevers(null) }

  const save = useMutation({
    mutationFn: (v: { id: number | null; name: string; is_base?: boolean }) => saveRetirementPlan({ id: v.id, name: v.name, params, is_base: v.is_base }),
    onSuccess: (pl) => { qc.invalidateQueries({ queryKey: ['retirement-plans'] }); setPlanId(pl.id); setSaved(clone(params!)); setNaming(null); setMsg(`Saved “${pl.name}”.`) },
    onError: (e) => setMsg((e as { response?: { data?: { detail?: string } } }).response?.data?.detail ?? 'Could not save the plan.'),
  })
  const remove = useMutation({
    mutationFn: (id: number) => deleteRetirementPlan(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['retirement-plans'] }); setPlanId(null); setMsg('Plan deleted.') },
  })
  const levMut = useMutation({ mutationFn: () => retirementLevers(params), onSuccess: setLevers })

  const current = plans.find(p => p.id === planId)
  const age = params ? THIS_YEAR - params.birth_year : 0

  if (!params || !defaults) return <div className="flex justify-center py-12"><Spinner /></div>

  const sug = start.data!.suggestions
  const sel = assetsQ.data?.assets ?? sug.assets                        // the accounts the preset selects, else all of them
  const useSuggested = (what: 'assets' | 'saving' | 'spend') => {
    setParams(p => {
      if (!p) return p
      let q = clone(p)
      if (what === 'assets') {
        q.assets.cash = Math.round(sel.cash ?? q.assets.cash); q.assets.investments = Math.round(sel.investments ?? q.assets.investments)
        q.assets.pot = Math.round(sel.pot ?? q.assets.pot); q.assets.investments_cost = sel.investments_cost != null ? Math.round(sel.investments_cost) : null
        q.assets_source = presetIds ? presetName : undefined
      }
      if (what === 'saving' && sug.flows.saved != null) q = setIn(q, ['contrib', 'investments'], Math.round(sug.flows.saved))
      if (what === 'spend' && sug.flows.expense != null) q = setIn(q, ['spend', 'annual'], Math.round(sug.flows.expense))
      return q
    })
    setLevers(null)
  }

  const country = start.data!.country
  const hint = country.hints
  const presets = streamPresets(hint.state_pension)
  const taxMismatch = (params.tax.country || '') !== country.code
  const t = params.tax
  const orderId = ORDERS.find(o => same(o.order, params.order))?.id ?? 'cit'
  const retireIdx = result?.retire_index ?? 0
  const f = (i: number) => real ? 1 : (result?.rows[i]?.infl ?? 1)
  const ages = result?.ages ?? []

  // ── verdict ──
  const mc = result?.mc
  const success = mc?.success_pct
  const verdict = !result ? null : success == null ? (result.survives ? 'ok' : 'bad')
    : success >= params.target_success_pct ? 'ok' : success >= params.target_success_pct - 25 ? 'warn' : 'bad'
  const tone = { ok: 'bg-green-50 border-green-200 text-green-800', warn: 'bg-amber-50 border-amber-200 text-amber-800', bad: 'bg-red-50 border-red-200 text-red-700' }

  return (
    <div className="space-y-4">
      {/* scenario bar */}
      <Card>
        <CardBody className="flex flex-wrap items-end gap-3">
          <Field label="Plan" className="min-w-[14rem]">
            <Select value={planId ?? ''} onChange={e => {
              const id = e.target.value ? Number(e.target.value) : null
              if (dirty && !window.confirm('Discard the changes to this plan?')) return
              load(id ? plans.find(p => p.id === id) ?? null : null)
            }}>
              <option value="">New plan (from my data)</option>
              {plans.map(p => <option key={p.id} value={p.id}>{p.name}{p.is_base ? ' — base' : ''}</option>)}
            </Select>
          </Field>
          {naming == null ? (
            <>
              {planId != null && <Button size="sm" disabled={!dirty || save.isPending} onClick={() => save.mutate({ id: planId, name: current?.name ?? 'Plan' })}>Save{dirty ? ' changes' : ''}</Button>}
              <Button size="sm" variant="secondary" onClick={() => setNaming(planId != null ? `${current?.name ?? 'Plan'} (copy)` : plans.length ? 'Scenario' : 'Base plan')}>Save as new…</Button>
              {planId != null && !current?.is_base && <Button size="sm" variant="secondary" onClick={() => save.mutate({ id: planId, name: current?.name ?? 'Plan', is_base: true })}>Make base</Button>}
              {planId != null && <Button size="sm" variant="secondary" onClick={() => { if (window.confirm(`Delete “${current?.name}”?`)) remove.mutate(planId) }}>Delete</Button>}
              <Button size="sm" variant="secondary" onClick={() => { if (!dirty || window.confirm('Replace the inputs with ones built from your data?')) { load(null) } }}>Reset to my data</Button>
            </>
          ) : (
            <>
              <Field label="Name"><Input value={naming} onChange={e => setNaming(e.target.value)} autoFocus /></Field>
              <Button size="sm" disabled={!naming.trim() || save.isPending} onClick={() => save.mutate({ id: null, name: naming })}>Save</Button>
              <Button size="sm" variant="secondary" onClick={() => setNaming(null)}>Cancel</Button>
            </>
          )}
          {dirty && <span className="text-xs text-amber-600">unsaved changes</span>}
          {msg && <span className="text-xs text-slate-500">{msg}</span>}
        </CardBody>
      </Card>

      <div className="grid gap-4 grid-cols-1 lg:grid-cols-[minmax(320px,430px)_1fr] items-start">
        {/* ── inputs ── */}
        <div className="space-y-2 min-w-0">
          <Section title="You" hint={`${age} years old`}>
            <div className="grid grid-cols-3 gap-2">
              <Num label="Year of birth" value={params.birth_year} onChange={v => set(['birth_year'], v)} />
              <Num label="Retire at" tip={hint.retire_age} value={params.retire_age} onChange={v => set(['retire_age'], v)} />
              <Num label="Plan until" tip="Age to plan to. Life expectancy at 65 is about 20 years in the EU, but a plan should outlast the average: 90 covers most outcomes." value={params.end_age} onChange={v => set(['end_age'], v)} />
            </div>
          </Section>

          <Section title="What you have today" hint="today's euros">
            {renderPresetBar?.(onPreset)}
            <div className="grid grid-cols-2 gap-2">
              <Num label="Cash and savings (€)" value={params.assets.cash} onChange={v => set(['assets', 'cash'], v)} step={1000} />
              <Num label="Investments (€)" value={params.assets.investments} onChange={v => set(['assets', 'investments'], v)} step={1000} />
              <Num label="Pension pots (€)" tip="Occupational or private pension funds, tax-wrapped plans — money you reach through the pot's own rules." value={params.assets.pot} onChange={v => set(['assets', 'pot'], v)} step={1000} />
              <Num label="Cost of the investments (€)" tip="What you paid for them: only the gain on a sale can be taxed. Leave empty if unknown — then no gain is assumed." value={params.assets.investments_cost} onChange={v => set(['assets', 'investments_cost'], v)} step={1000} />
            </div>
            <button className="text-xs text-blue-600 hover:underline" onClick={() => useSuggested('assets')}>Use {presetIds ? `the accounts of “${presetName}”` : 'all my accounts'}: cash {eur(sel.cash)}, investments {eur(sel.investments)}, pension {eur(sel.pot)}</button>
            {params.assets_source && <p className="text-xs text-slate-400">Amounts taken from the account preset “{params.assets_source}”. Cash, investment and pension accounts count; real estate, vehicles, cards and loans do not.</p>}
            {(assetsQ.data?.notes ?? sug.notes).map(n => <p key={n} className="text-xs text-amber-600">{n}</p>)}
          </Section>

          <Section title="Until you retire" hint="per year, today's euros">
            <div className="grid grid-cols-2 gap-2">
              <Num label="Saved into investments (€)" value={params.contrib.investments} onChange={v => set(['contrib', 'investments'], v)} step={500} />
              <Num label="Saved as cash (€)" value={params.contrib.cash} onChange={v => set(['contrib', 'cash'], v)} step={500} />
              <Num label="Paid into pension pots (€)" value={params.contrib.pot} onChange={v => set(['contrib', 'pot'], v)} step={500} />
              <Num label="Yearly increase (%)" tip="How much your saving grows each year. Set it to the inflation rate to keep it constant in today's euros." value={params.contrib.growth_pct} onChange={v => set(['contrib', 'growth_pct'], v)} step={0.5} />
            </div>
            {sug.flows.saved != null && <button className="text-xs text-blue-600 hover:underline" onClick={() => useSuggested('saving')}>Use what I saved in the last 12 months: {eur(sug.flows.saved)} (income {eur(sug.flows.income)} − spending {eur(sug.flows.expense)} − tax {eur(sug.flows.tax)})</button>}
          </Section>

          <Section title="In retirement" hint="today's euros">
            <div className="grid grid-cols-2 gap-2">
              <Num label="Spending a year (€)" tip="What you want to spend each year once retired, before income tax, in today's money — it is raised with inflation." value={params.spend.annual} onChange={v => set(['spend', 'annual'], v)} step={500} />
              <Num label="Change vs inflation (% a year)" tip="Spending often falls a little in real terms as people age (for example −1). 0 keeps it level with inflation." value={params.spend.real_change_pct} onChange={v => set(['spend', 'real_change_pct'], v)} step={0.5} />
            </div>
            {sug.flows.expense != null && <button className="text-xs text-blue-600 hover:underline" onClick={() => useSuggested('spend')}>Use my spending over the last 12 months: {eur(sug.flows.expense)} (check it has no one-off purchases)</button>}
          </Section>

          <Section title="Income in retirement" hint={`${params.streams.length} source${params.streams.length === 1 ? '' : 's'}`}>
            {params.streams.map((s, i) => (
              <div key={i} className="border border-slate-100 rounded-md p-2 space-y-2 bg-slate-50">
                <div className="flex gap-2 items-end">
                  <Field label="Name" className="flex-1"><Input value={s.name} onChange={e => set(['streams', i, 'name'], e.target.value)} /></Field>
                  <button className="text-xs text-red-500 hover:underline pb-2" onClick={() => set(['streams'], params.streams.filter((_, j) => j !== i))}>Remove</button>
                </div>
                <div className="grid grid-cols-3 gap-2">
                  <Num label="€ a year" value={s.annual} onChange={v => set(['streams', i, 'annual'], v)} step={500} />
                  <Num label="From age" value={s.start_age} onChange={v => set(['streams', i, 'start_age'], v)} />
                  <Num label="To age" tip="Leave empty for life." value={s.end_age} onChange={v => set(['streams', i, 'end_age'], v || (null as unknown as number))} />
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <Field label="Grows with" tip="Pensions are normally indexed to inflation; a fixed sum loses value over the years.">
                    <Select value={s.indexation === 'inflation' || s.indexation === 'fixed' ? s.indexation : 'custom'} onChange={e => set(['streams', i, 'indexation'], e.target.value === 'custom' ? 1 : e.target.value)}>
                      <option value="inflation">Inflation</option><option value="fixed">Nothing (fixed)</option><option value="custom">A set % a year</option>
                    </Select>
                  </Field>
                  <Field label="Tax" tip="Income: taxed on the income-tax scale after the health contribution, like a pension at home. Withheld at source: a flat % is taken and no more is due (a foreign pension taxed where it is paid, rent taxed at a fixed rate). Tax-free: nothing is taken.">
                    <Select value={s.tax} onChange={e => set(['streams', i, 'tax'], e.target.value)}>
                      <option value="income">Taxed as income</option><option value="source">Withheld at source</option><option value="exempt">Tax-free</option>
                    </Select>
                  </Field>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  {typeof s.indexation === 'number' && <Num label="Growth (% a year)" value={s.indexation} onChange={v => set(['streams', i, 'indexation'], v)} step={0.5} />}
                  {s.tax === 'source' && <Num label="Withheld (%)" value={s.source_rate} onChange={v => set(['streams', i, 'source_rate'], v)} />}
                </div>
              </div>
            ))}
            <Select value="" onChange={e => { const pr = presets.find(x => x.label === e.target.value); if (pr) set(['streams'], [...params.streams, pr.stream(params.retire_age)]) }}>
              <option value="">+ Add an income source…</option>
              {presets.map(x => <option key={x.label} value={x.label}>{x.label}</option>)}
            </Select>
            <p className="text-xs text-slate-400">{hint.state_pension_help} Rights earned in several EU countries add up, but each country pays — and taxes — its own: enter each as a source.</p>

            <p className="text-xs font-semibold text-slate-600 pt-1">One-off sums (inheritance, property sale)</p>
            {params.lumps.map((l, i) => (
              <div key={i} className="grid grid-cols-2 sm:grid-cols-[1fr_5rem_6rem_4rem_auto] gap-2 items-end">
                <Field label="Name"><Input value={l.name} onChange={e => set(['lumps', i, 'name'], e.target.value)} /></Field>
                <Num label="At age" value={l.age} onChange={v => set(['lumps', i, 'age'], v)} />
                <Num label="€ (today's)" value={l.amount} onChange={v => set(['lumps', i, 'amount'], v)} step={1000} />
                <Num label="Tax %" value={l.tax_pct} onChange={v => set(['lumps', i, 'tax_pct'], v)} />
                <button className="text-xs text-red-500 hover:underline pb-2" onClick={() => set(['lumps'], params.lumps.filter((_, j) => j !== i))}>×</button>
              </div>
            ))}
            <button className="text-xs text-blue-600 hover:underline" onClick={() => set(['lumps'], [...params.lumps, { name: 'One-off', age: params.retire_age, amount: 50000, tax_pct: 0 }])}>+ Add a one-off sum (paid into investments)</button>
          </Section>

          <Section title="Markets and inflation" open={false}>
            <div className="grid grid-cols-2 gap-2">
              <Num label="Return before retiring (% a year)" tip="Expected yearly return of your investments and pension pots before retirement, before tax on distributions. Past returns are not a forecast." value={params.returns.pre_pct} onChange={v => set(['returns', 'pre_pct'], v)} step={0.5} />
              <Num label="Return in retirement (% a year)" tip="Usually lower: the portfolio shifts toward bonds." value={params.returns.post_pct} onChange={v => set(['returns', 'post_pct'], v)} step={0.5} />
              <Num label="Volatility before (% a year)" tip="How much a year's return swings. It drives the chance-of-success figure; around 12–15% for a stock-heavy portfolio, 6–8% for a bond-heavy one. Starts from your own portfolio's history, never below 12%." value={params.returns.vol_pre_pct} onChange={v => set(['returns', 'vol_pre_pct'], v)} step={0.5} />
              <Num label="Volatility in retirement (%)" value={params.returns.vol_post_pct} onChange={v => set(['returns', 'vol_post_pct'], v)} step={0.5} />
              <Num label="Cash interest (% a year)" value={params.returns.cash_pct} onChange={v => set(['returns', 'cash_pct'], v)} step={0.25} />
              <Num label="Dividend / distribution yield (%)" tip="The part of the investments' return paid out and taxed every year." value={params.returns.yield_pct} onChange={v => set(['returns', 'yield_pct'], v)} step={0.25} />
              <Num label="Inflation (% a year)" tip="The ECB aims for 2% in the euro area; your country may run higher. Spending, pensions and the tax scale follow it." value={params.inflation_pct} onChange={v => set(['inflation_pct'], v)} step={0.25} />
              <Num label="Target chance of success (%)" tip="The chance the money lasts to the planning age that counts as 'on track'." value={params.target_success_pct} onChange={v => set(['target_success_pct'], v)} />
            </div>
            {sug.calibration && <p className="text-xs text-slate-400">Your portfolio's own history: {sug.calibration.past_return_pct}% a year with {sug.calibration.vol_pct}% volatility — the volatility is the starting point above; the return is not used (the past is not a forecast).</p>}
          </Section>

          <Section title={`Tax rules (${t.country === country.code ? (country.name || 'country not set') : t.country === 'GR' ? 'Greece' : t.country || 'custom'})`} hint={taxMismatch ? 'not your country' : country.has_tax_profile ? 'defaults — check them' : 'enter the rates'} open={taxMismatch || !country.has_tax_profile}>
            {taxMismatch && (
              <div className="rounded-md border border-amber-200 bg-amber-50 p-2 text-xs text-amber-800 space-y-1">
                <p>This plan uses {t.country || 'custom'} tax rules; your Country of Usage is {country.name || 'not set'}.</p>
                <button className="text-blue-600 hover:underline" onClick={() => { set(['tax'], clone(defaults.tax)) }}>Use the rules for {country.name || 'my country'}{country.has_tax_profile ? '' : ' (no rates yet: taxes start at zero)'}</button>
              </div>
            )}
            {!country.has_tax_profile && !taxMismatch && (
              <p className="text-xs text-amber-700">{country.name ? `There is no tax profile for ${country.name} yet` : 'No Country of Usage is set (Tools → App Settings)'}: taxes start at zero until you enter the income-tax scale and the other rates below, so results are better than they should be.</p>
            )}
            <p className="text-xs text-slate-500">{country.has_tax_profile ? `${country.name} rules are filled in from the Country of Usage setting.` : 'Enter your own rates.'} Income tax is national, not EU-wide. The defaults are a starting point, not tax advice.</p>
            <div className="space-y-1">
              <p className="text-xs font-semibold text-slate-600">Income tax scale (pensions and income taxed as income)</p>
              {t.brackets.map(([ub, rate], i) => (
                <div key={i} className="grid grid-cols-[1fr_5rem_auto] gap-2 items-end">
                  <Field label={i === 0 ? 'Up to (€ a year)' : ''}>
                    <Input type="number" placeholder="no limit" value={ub ?? ''} onChange={e => set(['tax', 'brackets', i, 0], e.target.value === '' ? null : Number(e.target.value))} />
                  </Field>
                  <Field label={i === 0 ? 'Rate %' : ''}><Input type="number" value={rate} onChange={e => set(['tax', 'brackets', i, 1], Number(e.target.value))} /></Field>
                  <button className="text-xs text-red-500 hover:underline pb-2" onClick={() => set(['tax', 'brackets'], t.brackets.filter((_, j) => j !== i))}>×</button>
                </div>
              ))}
              <button className="text-xs text-blue-600 hover:underline" onClick={() => set(['tax', 'brackets'], [...t.brackets.slice(0, -1), [(t.brackets[t.brackets.length - 2]?.[0] ?? 0) + 10000, t.brackets[t.brackets.length - 1][1]], t.brackets[t.brackets.length - 1]])}>+ Add a band</button>
            </div>
            <div className="grid grid-cols-3 gap-2">
              <Num label="Tax credit (€)" tip={hint.credit} value={t.credit} onChange={v => set(['tax', 'credit'], v)} />
              <Num label="Reduced above (€)" value={t.credit_taper_from} onChange={v => set(['tax', 'credit_taper_from'], v)} />
              <Num label="… by € per 1,000" value={t.credit_taper_per_1000} onChange={v => set(['tax', 'credit_taper_per_1000'], v)} />
            </div>
            <label className="flex items-center gap-2 text-xs text-slate-600">
              <input type="checkbox" checked={t.index_brackets} onChange={e => set(['tax', 'index_brackets'], e.target.checked)} />
              <Tooltip text="The scale, the credit and the thresholds are kept constant in today's euros. Switched off, they stay fixed in nominal euros and inflation pushes a pension into higher bands over the years (bracket creep).">Index the scale and the credit with inflation</Tooltip>
            </label>
            <div className="grid grid-cols-3 gap-2">
              <Num label="Health contribution on pensions (%)" tip={hint.health} value={t.health_pct} onChange={v => set(['tax', 'health_pct'], v)} step={0.5} />
              <Num label="Tax on dividends (%)" tip={hint.div} value={t.div_pct} onChange={v => set(['tax', 'div_pct'], v)} />
              <Num label="Tax on interest (%)" value={t.interest_pct} onChange={v => set(['tax', 'interest_pct'], v)} />
              <Num label="Tax on gains from securities (%)" tip={hint.gains} value={t.gains_pct} onChange={v => set(['tax', 'gains_pct'], v)} />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <Field label="Tax on pension-pot withdrawals" tip="A flat rate, or added to your other income and taxed on the scale.">
                <Select value={t.pot_mode} onChange={e => set(['tax', 'pot_mode'], e.target.value)}><option value="flat">A flat rate</option><option value="income">On the income-tax scale</option></Select>
              </Field>
              {t.pot_mode === 'flat' && <Num label="Flat rate (%)" value={t.pot_flat_pct} onChange={v => set(['tax', 'pot_flat_pct'], v)} />}
              <Field label="Pension pots are paid out" tip="Drawdown: used like the other pots, in the order below. Lump sum: the whole pot is paid out at retirement, taxed, and moved into investments.">
                <Select value={t.pot_payout} onChange={e => set(['tax', 'pot_payout'], e.target.value)}><option value="drawdown">Gradually (drawdown)</option><option value="lump">As a lump sum at retirement</option></Select>
              </Field>
              <Field label="Spending is paid from" tip="The order the pots are drawn in. Drawing cash first keeps investments growing; drawing a taxed pot first can lower the tax bill.">
                <Select value={orderId} onChange={e => set(['order'], ORDERS.find(o => o.id === e.target.value)!.order)}>{ORDERS.map(o => <option key={o.id} value={o.id}>{o.label}</option>)}</Select>
              </Field>
            </div>
            <details className="text-xs text-slate-500">
              <summary className="cursor-pointer">What the {country.name || 'default'} {country.has_tax_profile ? 'defaults assume' : 'setup needs'}</summary>
              <ul className="mt-1 space-y-1.5">{start.data!.rules.map(r => <li key={r.label}><b>{r.label}.</b> {r.value} <span className="text-slate-400">{r.note}</span></li>)}</ul>
            </details>
          </Section>
        </div>

        {/* ── results ── */}
        <div className="space-y-4 min-w-0">
          {errorMsg && <p className="text-sm text-red-600">{errorMsg}</p>}
          {!result ? <div className="flex justify-center py-12"><Spinner /></div> : (
            <>
              <div className={`rounded-xl border p-4 ${tone[verdict ?? 'ok']}`}>
                <p className="text-lg font-semibold">
                  {verdict === 'ok' ? '✅ On track' : verdict === 'warn' ? '⚠️ Close, but short of your target' : '🔻 Not on track'}
                  {success != null ? ` — the money lasts to ${params.end_age} in ${success.toFixed(0)}% of ${mc!.sims.toLocaleString()} simulated markets` : result.survives ? ` — the money lasts to ${params.end_age}` : ` — the money runs out at ${result.depletion_age}`}
                </p>
                <p className="text-xs mt-1 opacity-80">Target {params.target_success_pct.toFixed(0)}%. {proj.isFetching && <span>Updating…</span>}</p>
              </div>

              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <Kpi label={`At ${params.retire_age > age ? params.retire_age : 'today'}`} value={eur(result.balance_at_retirement)} sub="you will have (today's €)" />
                <Kpi label="Needed then" value={eur(result.needed_at_retirement)} sub="to last on average returns every year" />
                <Kpi label={result.gap_at_retirement != null && result.gap_at_retirement < 0 ? 'Shortfall' : 'Surplus'} value={eur(result.gap_at_retirement == null ? null : Math.abs(result.gap_at_retirement))}
                  color={result.gap_at_retirement != null && result.gap_at_retirement < 0 ? 'text-red-600' : 'text-green-700'} sub="on average returns — see the chance above" />
                <Kpi label="Money lasts to" value={result.survives ? `${params.end_age}+` : `${result.depletion_age}`} color={result.survives ? 'text-green-700' : 'text-red-600'}
                  sub={result.first_year ? `first-year draw ${result.first_year.withdrawal_rate_pct.toFixed(1)}%` : undefined} />
              </div>

              <Card>
                <CardHeader><CardTitle>What the plan says</CardTitle></CardHeader>
                <CardBody><ul className="list-disc pl-5 space-y-1.5 text-sm text-slate-700">{result.insights.map(s => <li key={s}>{s}</li>)}</ul></CardBody>
              </Card>

              <Card>
                <CardHeader><CardTitle>Your money by age (today's euros)</CardTitle></CardHeader>
                <CardBody>
                  <Plot
                    data={[
                      ...(mc ? [
                        { x: ages, y: mc.p90, type: 'scatter' as const, mode: 'lines' as const, line: { width: 0 }, showlegend: false, hoverinfo: 'skip' as const },
                        { x: ages, y: mc.p10, type: 'scatter' as const, mode: 'lines' as const, line: { width: 0 }, fill: 'tonexty' as const, fillcolor: 'rgba(59,130,246,0.15)', name: '10th–90th percentile of markets', hoverinfo: 'skip' as const },
                        { x: ages, y: mc.p50, type: 'scatter' as const, mode: 'lines' as const, line: { color: '#3b82f6', width: 2 }, name: 'Median market', hovertemplate: 'age %{x}: €%{y:,.0f}<extra>median</extra>' },
                      ] : []),
                      { x: ages, y: result.real_total, type: 'scatter' as const, mode: 'lines' as const, line: { color: '#0f172a', width: 2, dash: 'dot' as const }, name: 'Straight-line projection', hovertemplate: 'age %{x}: €%{y:,.0f}<extra>straight line</extra>' },
                    ]}
                    layout={{
                      height: 340, margin: { t: 10, r: 10, b: 40, l: 70 }, hovermode: 'x unified' as const, yaxis: { tickformat: ',.0f', tickprefix: '€', rangemode: 'tozero' as const },
                      xaxis: { title: { text: 'age' } }, legend: { orientation: 'h' as const, y: -0.2 },
                      shapes: [{ type: 'line', x0: params.retire_age, x1: params.retire_age, yref: 'paper', y0: 0, y1: 1, line: { color: '#94a3b8', dash: 'dash' } }],
                      annotations: [{ x: params.retire_age, yref: 'paper', y: 1, text: 'retirement', showarrow: false, yanchor: 'bottom', font: { size: 11, color: '#64748b' } }],
                      ...plotLayout(isDark),
                    }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }}
                  />
                  <p className="text-xs text-slate-400">The band is where 8 in 10 simulated markets end up; the dotted line is the plan with the expected return every year.</p>
                </CardBody>
              </Card>

              {mc && (
                <Card>
                  <CardHeader><CardTitle>Chance the money is still there</CardTitle></CardHeader>
                  <CardBody>
                    <Plot
                      data={[{ x: ages.slice(retireIdx), y: mc.solvent_by_age.slice(retireIdx), type: 'scatter' as const, mode: 'lines' as const, fill: 'tozeroy' as const, line: { color: '#16a34a', width: 2 }, fillcolor: 'rgba(22,163,74,0.12)', hovertemplate: 'to age %{x}: %{y:.0f}%<extra></extra>' }]}
                      layout={{ height: 220, margin: { t: 10, r: 10, b: 40, l: 50 }, yaxis: { range: [0, 100], ticksuffix: '%' }, xaxis: { title: { text: 'age' } },
                        shapes: [{ type: 'line', xref: 'paper', x0: 0, x1: 1, y0: params.target_success_pct, y1: params.target_success_pct, line: { color: '#94a3b8', dash: 'dot' } }], ...plotLayout(isDark) }}
                      config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }}
                    />
                  </CardBody>
                </Card>
              )}

              {result.rows.length > 0 && (
                <Card>
                  <CardHeader><CardTitle>Where retirement income comes from (straight-line projection)</CardTitle></CardHeader>
                  <CardBody>
                    <IncomeChart rows={result.rows} isDark={isDark} />
                  </CardBody>
                </Card>
              )}

              <Card>
                <CardHeader><CardTitle>How could it be fixed?</CardTitle></CardHeader>
                <CardBody className="space-y-3">
                  {!levers ? (
                    <Button size="sm" onClick={() => levMut.mutate()} disabled={levMut.isPending}>{levMut.isPending ? 'Working it out…' : `What would it take to reach ${params.target_success_pct.toFixed(0)}%?`}</Button>
                  ) : levers.reached ? (
                    <p className="text-sm text-green-700">Already there — {levers.success_pct.toFixed(0)}% against a target of {levers.target_pct.toFixed(0)}%.</p>
                  ) : (
                    <div className="space-y-3">
                    {levers.capital_needed != null && <p className="text-sm text-slate-700">To reach {levers.target_pct.toFixed(0)}% you would want about <b>{eur(levers.capital_needed)}</b> at retirement (today's euros); the plan projects {eur(levers.capital_projected)}. The extra is the safety margin against a poor run of markets.</p>}
                    <div className="grid md:grid-cols-3 gap-3">
                      <Lever title="Save more" ok={!!levers.save_more} text={levers.save_more ? `${eur(levers.save_more.extra_per_month)} a month more (${eur(levers.save_more.extra_per_year)} a year, today's €) into investments` : retireNow(params) ? 'Not possible: you have already retired.' : 'Even 200,000 € a year more is not enough.'} sub={levers.save_more ? `→ ${levers.save_more.success_pct.toFixed(0)}% chance` : undefined} />
                      <Lever title="Retire later" ok={!!levers.retire_later} text={levers.retire_later ? `${levers.retire_later.years} year${levers.retire_later.years > 1 ? 's' : ''} later, at ${levers.retire_later.age}` : 'No retirement age up to 15 years later is enough.'} sub={levers.retire_later ? `→ ${levers.retire_later.success_pct.toFixed(0)}% chance` : undefined} />
                      <Lever title="Spend less" ok={!!levers.spend_less} text={levers.spend_less ? `Cut spending by ${levers.spend_less.cut_pct.toFixed(0)}% to ${eur(levers.spend_less.annual)} a year` : 'Even a 60% cut is not enough.'} sub={levers.spend_less ? `→ ${levers.spend_less.success_pct.toFixed(0)}% chance` : undefined} />
                    </div>
                    </div>
                  )}
                  {levers && !levers.reached && <p className="text-xs text-slate-400">Each is the change needed on its own, from {levers.success_pct.toFixed(0)}% now; a mix of smaller changes works too.</p>}
                </CardBody>
              </Card>

              <WhatIf params={params} plans={plans} planId={planId} />
            </>
          )}
        </div>
      </div>

      {/* the table is wide, so it gets the full width below the inputs and results */}
      {result && (
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between gap-2 w-full">
              <CardTitle>Year by year</CardTitle>
              <div className="flex items-center gap-3 text-xs text-slate-500">
                <label className="flex items-center gap-1"><input type="checkbox" checked={real} onChange={e => setReal(e.target.checked)} /> today's euros</label>
                <button className="text-blue-600 hover:underline" onClick={() => setShowTable(s => !s)}>{showTable ? 'Hide' : 'Show'}</button>
              </div>
            </div>
          </CardHeader>
          {showTable && (
            <CardBody className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead><tr className="text-slate-500 border-b">{['Age', 'Year', 'Cash', 'Investments', 'Pots', 'Saved', 'Spending', 'Pensions & income (net of health)', 'Income tax', 'From cash', 'From investments', 'From pots', 'Tax on sales / pots', 'Shortfall'].map(h => <th key={h} className="text-right first:text-left px-2 py-1 whitespace-nowrap">{h}</th>)}</tr></thead>
                <tbody>
                  {result.rows.map((r, i) => {
                    const d = f(i)
                    const n = (v?: number) => v == null || r.phase === 'end' ? '' : v === 0 ? '—' : fmtEur(v / d)
                    return (
                      <tr key={r.age} className={`border-b border-slate-100 ${r.phase === 'saving' ? '' : 'bg-slate-50'} ${(r.shortfall ?? 0) > 1 ? 'text-red-600' : ''}`}>
                        <td className="px-2 py-1">{r.age}</td><td className="px-2 py-1 text-right">{r.year}</td>
                        <td className="px-2 py-1 text-right tabular-nums">{fmtEur(r.cash / d)}</td><td className="px-2 py-1 text-right tabular-nums">{fmtEur(r.investments / d)}</td><td className="px-2 py-1 text-right tabular-nums">{fmtEur(r.pot / d)}</td>
                        <td className="px-2 py-1 text-right tabular-nums">{n(r.contrib)}</td><td className="px-2 py-1 text-right tabular-nums">{n(r.spend)}</td>
                        <td className="px-2 py-1 text-right tabular-nums">{r.income == null || r.phase === 'end' ? '' : r.income - (r.health ?? 0) === 0 ? '—' : fmtEur((r.income - (r.health ?? 0)) / d)}</td>
                        <td className="px-2 py-1 text-right tabular-nums">{n(r.income_tax)}</td><td className="px-2 py-1 text-right tabular-nums">{n(r.wd_cash)}</td>
                        <td className="px-2 py-1 text-right tabular-nums">{n(r.wd_investments)}</td><td className="px-2 py-1 text-right tabular-nums">{n(r.wd_pot)}</td>
                        <td className="px-2 py-1 text-right tabular-nums">{r.phase === 'end' ? '' : fmtEur(((r.tax_gains ?? 0) + (r.tax_pot ?? 0)) / d)}</td><td className="px-2 py-1 text-right tabular-nums">{n(r.shortfall)}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
              <p className="text-xs text-slate-400 mt-2">Balances are at the start of each year. A projection, not advice: tax rules and returns will differ.</p>
            </CardBody>
          )}
        </Card>
      )}
    </div>
  )
}

const retireNow = (p: RetirementParams) => p.retire_age <= THIS_YEAR - p.birth_year

function Kpi({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-3">
      <p className="text-xs font-medium text-slate-500 uppercase tracking-wide">{label}</p>
      <p className={`text-xl font-bold mt-1 ${color ?? 'text-slate-900'}`}>{value}</p>
      {sub && <p className="text-xs text-slate-400 mt-0.5">{sub}</p>}
    </div>
  )
}

function Lever({ title, text, sub, ok }: { title: string; text: string; sub?: string; ok: boolean }) {
  return (
    <div className={`rounded-lg border p-3 ${ok ? 'border-green-200 bg-green-50' : 'border-slate-200 bg-slate-50'}`}>
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</p>
      <p className="text-sm mt-1 text-slate-800">{text}</p>
      {sub && <p className="text-xs text-green-700 mt-1">{sub}</p>}
    </div>
  )
}

// Spending against what covers it, year by year in retirement, in today's euros.
function IncomeChart({ rows, isDark }: { rows: RetirementResult['rows']; isDark: boolean }) {
  const ret = rows.filter(r => r.phase === 'retired')
  if (!ret.length) return <p className="text-sm text-slate-400">The plan has no retired years yet.</p>
  const x = ret.map(r => r.age)
  const d = (r: RetirementResult['rows'][number], v: number | undefined) => ((v ?? 0) / r.infl)
  const bar = (name: string, color: string, y: number[]) => ({ x, y, name, type: 'bar' as const, marker: { color }, hovertemplate: `${name}: €%{y:,.0f}<extra></extra>` })
  return (
    <Plot
      data={[
        bar('Pensions & other income (after tax)', '#16a34a', ret.map(r => d(r, (r.income ?? 0) - (r.health ?? 0) - (r.income_tax ?? 0)))),
        bar('From cash', '#0ea5e9', ret.map(r => d(r, r.wd_cash))),
        bar('From investments (net of tax)', '#3b82f6', ret.map(r => d(r, (r.wd_investments ?? 0) - (r.tax_gains ?? 0)))),
        bar('From pension pots (net of tax)', '#8b5cf6', ret.map(r => d(r, (r.wd_pot ?? 0) - (r.tax_pot ?? 0)))),
        { x, y: ret.map(r => d(r, r.spend)), name: 'Spending', type: 'scatter' as const, mode: 'lines' as const, line: { color: '#0f172a', width: 2 }, hovertemplate: 'Spending: €%{y:,.0f}<extra></extra>' },
      ]}
      layout={{ barmode: 'stack' as const, height: 300, margin: { t: 10, r: 10, b: 40, l: 70 }, hovermode: 'x unified' as const, yaxis: { tickformat: ',.0f', tickprefix: '€' },
        xaxis: { title: { text: 'age' } }, legend: { orientation: 'h' as const, y: -0.25 }, ...plotLayout(isDark) }}
      config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }}
    />
  )
}

// Side-by-side numbers for what-if variants of the plan on screen and for every saved plan.
function WhatIf({ params, plans, planId }: { params: RetirementParams; plans: RetirementPlan[]; planId: number | null }) {
  const debounced = useDebounced(params, 800)
  const list = useMemo(() => [{ name: 'This plan', params: debounced }, ...variants(debounced)], [debounced])
  const q = useQuery({ queryKey: ['retirement-whatif', debounced], queryFn: () => compareRetirement(list, 500), placeholderData: prev => prev })
  const saved = useQuery({
    queryKey: ['retirement-saved-compare', plans.map(p => [p.id, p.params])],
    queryFn: () => compareRetirement(plans.map(p => ({ name: p.name + (p.is_base ? ' (base)' : ''), params: p.params })), 500),
    enabled: plans.length > 1,
  })
  return (
    <>
      <Card>
        <CardHeader><CardTitle>What if…</CardTitle></CardHeader>
        <CardBody className="overflow-x-auto">
          {!q.data ? <Spinner /> : <CompareTable rows={q.data} />}
          <p className="text-xs text-slate-400 mt-2">Each row changes one thing in the plan on screen. “Chance” is the share of simulated markets in which the money lasts to the planning age.</p>
        </CardBody>
      </Card>
      {plans.length > 1 && (
        <Card>
          <CardHeader><CardTitle>Saved scenarios</CardTitle></CardHeader>
          <CardBody className="overflow-x-auto">
            {!saved.data ? <Spinner /> : <CompareTable rows={saved.data} highlight={plans.find(p => p.id === planId)?.name} />}
            <p className="text-xs text-slate-400 mt-2">Save a variant with “Save as new…” to keep it here, next to the base plan.</p>
          </CardBody>
        </Card>
      )}
    </>
  )
}

function CompareTable({ rows, highlight }: { rows: RetirementCompareRow[]; highlight?: string }) {
  const base = rows[0]
  const delta = (v: number | null, b: number | null) => v == null || b == null ? '' : v === b ? '' : ` (${v > b ? '+' : '−'}${Math.abs(v - b).toFixed(0)})`
  return (
    <table className="w-full text-xs">
      <thead><tr className="text-slate-500 border-b">
        {['', 'Retire at', 'At retirement', 'Needed', 'Lasts to', 'Chance'].map(h => <th key={h} className="text-right first:text-left px-2 py-1 whitespace-nowrap">{h}</th>)}
      </tr></thead>
      <tbody>
        {rows.map((r, i) => r.error ? <tr key={i}><td className="px-2 py-1" colSpan={6}>{r.name}: {r.error}</td></tr> : (
          <tr key={i} className={`border-b border-slate-100 ${i === 0 ? 'font-semibold bg-slate-50' : ''} ${r.name === highlight ? 'bg-blue-50' : ''}`}>
            <td className="px-2 py-1 whitespace-nowrap">{r.name}</td>
            <td className="px-2 py-1 text-right">{r.retire_age}</td>
            <td className="px-2 py-1 text-right tabular-nums">{eur(r.balance_at_retirement)}</td>
            <td className="px-2 py-1 text-right tabular-nums">{eur(r.needed_at_retirement)}</td>
            <td className={`px-2 py-1 text-right ${r.survives ? 'text-green-700' : 'text-red-600'}`}>{r.survives ? `${r.end_age}+` : r.depletion_age}</td>
            <td className={`px-2 py-1 text-right tabular-nums font-semibold ${r.success_pct == null ? '' : r.success_pct >= r.target_pct ? 'text-green-700' : r.success_pct >= r.target_pct - 25 ? 'text-amber-600' : 'text-red-600'}`}>
              {r.success_pct == null ? '—' : `${r.success_pct.toFixed(0)}%`}<span className="font-normal text-slate-400">{i > 0 ? delta(r.success_pct, base?.success_pct ?? null) : ''}</span>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
