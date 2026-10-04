import React, { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import PlotlyReact from 'react-plotly.js'
import { Button, Spinner, Tooltip } from '@/components/ui'
import { fmtEur, plotLayout } from '@/lib/utils'
import { useTheme } from '@/lib/theme'
import { getStressAssumptions, runRateShock, runStressReplay } from '@/lib/api'

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const Plot: React.ComponentType<any> = (PlotlyReact as any).default ?? PlotlyReact

// ── Types (mirror api/routers/stress.py) ─────────────────────────────────────
type Scenario = { id: string; name: string; start: string; end: string; yield_change_pp: number; desc: string }
type Assumptions = {
  rate_shock: { equity_sensitivity: Record<string, number>; asset_sensitivity: Record<string, number>; bond: Record<string, number> }
  replay: { scenarios: Scenario[]; fallback_returns: Record<string, Record<string, number>>; yield_change_pp?: Record<string, number> }
}
type Rollup = { account?: string; asset_class?: string; value: number; price_impact?: number; income_impact?: number; impact: number; pct: number | null }
type Row = {
  account_id: number; account: string; securities_id: number; ticker: string; security: string; asset_class: string; value: number
  basis: string; impact: number; pct: number
  sensitivity_pct_per_pp?: number; price_impact?: number; income_impact?: number; recoverable_at_maturity?: boolean
  detail?: string; peers?: number | null; local_return_pct?: number; fx_pct?: number; return_pct?: number
}
type RateResult = {
  params: { shock_pp: number; period_months: number }
  summary: { value: number; price_impact: number; income_impact: number; impact: number; pct: number; price_pct: number; recoverable_at_maturity: number } | null
  by_account: Rollup[]; by_class: Rollup[]; rows: Row[]; notes: string[]
}
type ReplayScenario = {
  id: string; name: string; start: string; end: string; yield_change_pp: number; desc: string
  summary: { value: number; impact: number; pct: number; coverage: Record<'own' | 'peers' | 'rule' | 'assumption', number> }
  by_account: Rollup[]; by_class: Rollup[]; rows: Row[]
}
type ReplayResult = { scenarios: ReplayScenario[]; notes: string[] }

const CLASS_LABELS: Record<string, string> = {
  Stock: 'Stocks', EquityFund: 'Equity funds', BondFund: 'Bond funds', CashFund: 'Money-market funds',
  Bond: 'Direct bonds / T-bills', Commodity: 'Commodities', Crypto: 'Crypto', Other: 'Other',
}
const BASIS_STYLE: Record<string, { label: string; color: string; tip: string }> = {
  own: { label: 'Own history', color: '#10b981', tip: 'Measured from the security\'s own prices over the window.' },
  peers: { label: 'Similar securities', color: '#3b82f6', tip: 'No history then — borrowed from the index it tracks or the median of similar securities that existed.' },
  rule: { label: 'Rate rule', color: '#8b5cf6', tip: 'Bonds / money-market funds without history: duration × an assumed yield change.' },
  assumption: { label: 'Assumption', color: '#f59e0b', tip: 'No history and nothing similar to borrow from — a rough round-number assumption (editable).' },
}

const signed = (n: number) => `${n >= 0 ? '+' : '−'}${fmtEur(Math.abs(n))}`
const pctTxt = (n: number | null | undefined, d = 1) => n == null ? '—' : `${n >= 0 ? '+' : ''}${n.toFixed(d)}%`
const tone = (n: number) => n < 0 ? 'text-red-600' : n > 0 ? 'text-green-700' : 'text-slate-500'

function Kpi({ label, value, sub, tip, color }: { label: string; value: string; sub?: string; tip?: string; color?: string }) {
  return (
    <div className="bg-slate-50 rounded-lg p-3">
      <p className="text-xs text-slate-500 mb-1">{tip ? <Tooltip text={tip}>{label}</Tooltip> : label}</p>
      <p className={`text-lg font-bold ${color ?? ''}`}>{value}</p>
      {sub && <p className="text-xs text-slate-400 mt-0.5">{sub}</p>}
    </div>
  )
}

// A number input that lets you type "-" or "1." without fighting the controlled value.
function NumField({ value, onChange, className = 'w-20' }: { value: number; onChange: (n: number) => void; className?: string }) {
  const [txt, setTxt] = useState(String(value))
  useEffect(() => { setTxt(String(value)) }, [value])
  return (
    <input type="text" inputMode="decimal" value={txt} className={`${className} rounded border border-slate-300 px-2 py-1 text-sm text-right`}
      onChange={e => {
        setTxt(e.target.value)
        const n = Number(e.target.value)
        if (e.target.value.trim() !== '' && !Number.isNaN(n)) onChange(n)
      }} />
  )
}

function RollupTable({ title, rows, first, hasParts }: { title: string; rows: Rollup[]; first: 'account' | 'asset_class'; hasParts?: boolean }) {
  const sorted = [...rows].sort((a, b) => a.impact - b.impact)
  return (
    <div>
      <p className="text-sm font-semibold text-slate-700 mb-1">{title}</p>
      <div className="overflow-x-auto rounded-lg border border-slate-200">
        <table className="w-full text-xs">
          <thead className="bg-slate-50 text-slate-500"><tr>
            <th className="px-2 py-1.5 text-left">{first === 'account' ? 'Account' : 'Type'}</th>
            <th className="px-2 py-1.5 text-right">Value</th>
            {hasParts && <th className="px-2 py-1.5 text-right">Price</th>}
            {hasParts && <th className="px-2 py-1.5 text-right">Income</th>}
            <th className="px-2 py-1.5 text-right">Impact</th><th className="px-2 py-1.5 text-right">%</th>
          </tr></thead>
          <tbody>
            {sorted.map(r => {
              const key = (first === 'account' ? r.account : r.asset_class) ?? ''
              return (
                <tr key={key} className="border-t border-slate-100">
                  <td className="px-2 py-1.5">{first === 'account' ? key : (CLASS_LABELS[key] ?? key)}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums">{fmtEur(r.value)}</td>
                  {hasParts && <td className={`px-2 py-1.5 text-right tabular-nums ${tone(r.price_impact ?? 0)}`}>{signed(r.price_impact ?? 0)}</td>}
                  {hasParts && <td className={`px-2 py-1.5 text-right tabular-nums ${tone(r.income_impact ?? 0)}`}>{signed(r.income_impact ?? 0)}</td>}
                  <td className={`px-2 py-1.5 text-right tabular-nums font-medium ${tone(r.impact)}`}>{signed(r.impact)}</td>
                  <td className={`px-2 py-1.5 text-right tabular-nums ${tone(r.impact)}`}>{pctTxt(r.pct, 2)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function SecurityTable({ rows, mode }: { rows: Row[]; mode: 'rate' | 'replay' }) {
  const navigate = useNavigate()
  const [account, setAccount] = useState('')
  const [cls, setCls] = useState('')
  const [all, setAll] = useState(false)
  const accounts = useMemo(() => [...new Set(rows.map(r => r.account))].sort(), [rows])
  const classes = useMemo(() => [...new Set(rows.map(r => r.asset_class))].sort(), [rows])
  const shown = useMemo(() => rows.filter(r => (!account || r.account === account) && (!cls || r.asset_class === cls))
    .sort((a, b) => a.impact - b.impact), [rows, account, cls])
  const list = all ? shown : shown.slice(0, 30)
  const sel = 'rounded border border-slate-300 px-2 py-1 text-xs'
  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 mb-1">
        <p className="text-sm font-semibold text-slate-700">Per security</p>
        <select className={sel} value={account} onChange={e => setAccount(e.target.value)}>
          <option value="">All accounts</option>{accounts.map(a => <option key={a}>{a}</option>)}
        </select>
        <select className={sel} value={cls} onChange={e => setCls(e.target.value)}>
          <option value="">All types</option>{classes.map(c => <option key={c} value={c}>{CLASS_LABELS[c] ?? c}</option>)}
        </select>
        <span className="text-xs text-slate-400">{shown.length} positions, worst first</span>
      </div>
      <div className="overflow-x-auto rounded-lg border border-slate-200 max-h-[28rem] overflow-y-auto">
        <table className="w-full text-xs">
          <thead className="bg-slate-50 text-slate-500 sticky top-0"><tr>
            <th className="px-2 py-1.5 text-left">Account</th><th className="px-2 py-1.5 text-left">Security</th>
            <th className="px-2 py-1.5 text-left">Type</th><th className="px-2 py-1.5 text-right">Value</th>
            {mode === 'rate'
              ? <><th className="px-2 py-1.5 text-right">%/pp</th><th className="px-2 py-1.5 text-right">Price</th><th className="px-2 py-1.5 text-right">Income</th></>
              : <><th className="px-2 py-1.5 text-right">Return</th><th className="px-2 py-1.5 text-left">Basis</th></>}
            <th className="px-2 py-1.5 text-right">Impact</th><th className="px-2 py-1.5 text-right">%</th>
            {mode === 'rate' && <th className="px-2 py-1.5 text-left">Assumption</th>}
          </tr></thead>
          <tbody>
            {list.map((r, i) => (
              <tr key={`${r.account_id}-${r.securities_id}-${i}`} className="border-t border-slate-100 align-top">
                <td className="px-2 py-1.5 text-slate-600">{r.account}</td>
                <td className="px-2 py-1.5">
                  <button onClick={() => navigate(`/securities/${r.securities_id}`)} className="text-blue-600 hover:underline text-left">
                    <span className="font-mono">{r.ticker}</span> <span className="text-slate-500">{r.security}</span>
                  </button>
                </td>
                <td className="px-2 py-1.5 text-slate-500">{CLASS_LABELS[r.asset_class] ?? r.asset_class}</td>
                <td className="px-2 py-1.5 text-right tabular-nums">{fmtEur(r.value)}</td>
                {mode === 'rate' ? (
                  <>
                    <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{(r.sensitivity_pct_per_pp ?? 0).toFixed(2)}</td>
                    <td className={`px-2 py-1.5 text-right tabular-nums ${tone(r.price_impact ?? 0)}`}>{signed(r.price_impact ?? 0)}</td>
                    <td className={`px-2 py-1.5 text-right tabular-nums ${tone(r.income_impact ?? 0)}`}>{signed(r.income_impact ?? 0)}</td>
                  </>
                ) : (
                  <>
                    <td className={`px-2 py-1.5 text-right tabular-nums ${tone(r.return_pct ?? 0)}`}>
                      <Tooltip text={`Local price change ${pctTxt(r.local_return_pct, 1)}, currency effect ${pctTxt(r.fx_pct, 1)}`}>{pctTxt(r.return_pct, 1)}</Tooltip>
                    </td>
                    <td className="px-2 py-1.5">
                      <Tooltip text={r.detail ?? ''}>
                        <span className="inline-flex items-center gap-1">
                          <span className="inline-block w-2 h-2 rounded-full" style={{ background: BASIS_STYLE[r.basis]?.color }} />
                          {BASIS_STYLE[r.basis]?.label ?? r.basis}{r.peers ? ` (${r.peers})` : ''}
                        </span>
                      </Tooltip>
                    </td>
                  </>
                )}
                <td className={`px-2 py-1.5 text-right tabular-nums font-medium ${tone(r.impact)}`}>{signed(r.impact)}</td>
                <td className={`px-2 py-1.5 text-right tabular-nums ${tone(r.impact)}`}>{pctTxt(r.pct, 2)}</td>
                {mode === 'rate' && <td className="px-2 py-1.5 text-slate-400 max-w-[22rem]">{r.basis}{r.recoverable_at_maturity ? ' · recovers at maturity' : ''}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {shown.length > 30 && (
        <button className="text-xs text-blue-600 hover:underline mt-1" onClick={() => setAll(!all)}>{all ? 'Show worst 30 only' : `Show all ${shown.length}`}</button>
      )}
    </div>
  )
}

function Notes({ items, title = 'Assumptions & limits' }: { items: string[]; title?: string }) {
  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2">
      <p className="text-xs font-semibold text-amber-800 mb-1">{title}</p>
      <ul className="list-disc pl-4 space-y-1 text-xs text-slate-600">{items.map((n, i) => <li key={i}>{n}</li>)}</ul>
    </div>
  )
}

function Collapsible({ title, children, defaultOpen = false }: { title: string; children: React.ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="border border-slate-200 rounded-lg overflow-hidden">
      <button onClick={() => setOpen(!open)} className="w-full flex items-center gap-2 px-4 py-2 text-sm font-medium text-slate-700 bg-slate-50 hover:bg-slate-100 text-left">
        <span className="text-xs">{open ? '▼' : '▶'}</span><span>{title}</span>
      </button>
      {open && <div className="p-3">{children}</div>}
    </div>
  )
}

// ═════════════════════════════════════════════════════════════════════════════
// Rate shock
// ═════════════════════════════════════════════════════════════════════════════
function RateShockSection({ accountIds, defaults }: { accountIds?: number[]; defaults: Assumptions }) {
  const { isDark } = useTheme()
  const [shock, setShock] = useState(1)
  const [months, setMonths] = useState(12)
  const [draft, setDraft] = useState<Assumptions['rate_shock']>(defaults.rate_shock)
  const [applied, setApplied] = useState<{ shock: number; months: number; rs: Assumptions['rate_shock'] } | null>(null)

  const { data, isFetching, error } = useQuery({
    queryKey: ['stress-rate', accountIds, applied],
    queryFn: () => runRateShock({ accountIds, shockPp: applied!.shock, periodMonths: applied!.months, assumptions: { rate_shock: applied!.rs } }),
    enabled: applied != null,
  })
  useEffect(() => { setApplied({ shock: 1, months: 12, rs: defaults.rate_shock }) }, []) // eslint-disable-line react-hooks/exhaustive-deps
  const res = data as RateResult | undefined
  const run = () => setApplied({ shock, months, rs: draft })
  const setEq = (k: string, n: number) => setDraft(d => ({ ...d, equity_sensitivity: { ...d.equity_sensitivity, [k]: n } }))
  const setAsset = (k: string, n: number) => setDraft(d => ({ ...d, asset_sensitivity: { ...d.asset_sensitivity, [k]: n } }))
  const setBond = (k: string, n: number) => setDraft(d => ({ ...d, bond: { ...d.bond, [k]: n } }))
  const chip = (active: boolean) => `px-2 py-1 text-xs rounded border ${active ? 'bg-blue-600 text-white border-blue-600' : 'border-slate-300 text-slate-600 hover:bg-slate-50'}`
  const S = res?.summary

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-6">
        <div>
          <p className="text-xs font-medium text-slate-600 mb-1"><Tooltip text="A parallel, immediate move in interest rates, in percentage points. Negative = rates fall.">Rate change (pp)</Tooltip></p>
          <div className="flex items-center gap-1 flex-wrap">
            {[-2, -1, -0.5, 0.5, 1, 2, 3].map(v => <button key={v} onClick={() => setShock(v)} className={chip(shock === v)}>{v > 0 ? '+' : ''}{v}</button>)}
            <NumField value={shock} onChange={setShock} className="w-16" />
          </div>
        </div>
        <div>
          <p className="text-xs font-medium text-slate-600 mb-1"><Tooltip text="How long the new rate level lasts. Bonds and T-bills that mature inside this period are rolled at the new rate for the rest of it, which is where the income effect comes from.">Period (months)</Tooltip></p>
          <div className="flex items-center gap-1">
            {[3, 6, 12, 24, 36].map(v => <button key={v} onClick={() => setMonths(v)} className={chip(months === v)}>{v}</button>)}
            <NumField value={months} onChange={setMonths} className="w-14" />
          </div>
        </div>
        <Button onClick={run} disabled={isFetching}>{isFetching ? 'Running…' : 'Run'}</Button>
      </div>

      {error && <p className="text-xs text-red-600 bg-red-50 rounded px-3 py-2">{String((error as Error).message)}</p>}
      {isFetching && !res && <div className="flex justify-center py-8"><Spinner /></div>}
      {res && !S && <p className="text-sm text-slate-500">No holdings in the selected accounts.</p>}

      {res && S && (
        <>
          <p className="text-sm text-slate-600">
            If rates {res.params.shock_pp >= 0 ? 'rise' : 'fall'} by <strong>{Math.abs(res.params.shock_pp)} pp</strong>, measured over <strong>{res.params.period_months} months</strong>:
          </p>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Kpi label="Portfolio value" value={fmtEur(S.value)} />
            <Kpi label="Price impact" value={signed(S.price_impact)} sub={pctTxt(S.price_pct, 2)} color={tone(S.price_impact)}
              tip="Immediate mark-to-market change in the value of what you hold." />
            <Kpi label={`Income over ${res.params.period_months} months`} value={signed(S.income_impact)} color={tone(S.income_impact)}
              tip="Extra (or lost) income from bonds/T-bills rolled at the new rate, and money-market funds repricing." />
            <Kpi label="Total impact" value={signed(S.impact)} sub={pctTxt(S.pct, 2)} color={tone(S.impact)} />
          </div>
          {S.recoverable_at_maturity !== 0 && (
            <p className="text-xs text-slate-500">
              Of the price impact, <strong>{signed(S.recoverable_at_maturity)}</strong> is on direct bonds and T-bills — a paper mark-to-market effect that reverses if you hold them to maturity.
            </p>
          )}
          {res.by_account.length > 0 && (
            <Plot data={[{ type: 'bar', orientation: 'h', y: [...res.by_account].sort((a, b) => b.impact - a.impact).map(a => a.account),
                x: [...res.by_account].sort((a, b) => b.impact - a.impact).map(a => a.impact),
                marker: { color: [...res.by_account].sort((a, b) => b.impact - a.impact).map(a => a.impact < 0 ? '#ef4444' : '#10b981') } }]}
              layout={{ height: Math.max(180, 40 + res.by_account.length * 28), margin: { t: 10, r: 20, b: 30, l: 220 }, xaxis: { title: 'Total impact (€)', tickformat: ',.0f' }, ...plotLayout(isDark) }}
              config={{ displayModeBar: false }} style={{ width: '100%' }} />
          )}
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
            <RollupTable title="By account" rows={res.by_account} first="account" hasParts />
            <RollupTable title="By asset type" rows={res.by_class} first="asset_class" hasParts />
          </div>
          <SecurityTable rows={res.rows} mode="rate" />
          <Notes items={res.notes} />
        </>
      )}

      <Collapsible title="Assumptions you can change (applied on the next Run)">
        <div className="space-y-4">
          <div>
            <p className="text-xs font-semibold text-slate-600 mb-1">Stocks and equity funds — % price change for a +1 pp rise in rates, by sector</p>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-x-6 gap-y-1">
              {Object.entries(draft.equity_sensitivity).map(([k, v]) => (
                <label key={k} className="flex items-center justify-between gap-2 text-xs text-slate-600">{k}<NumField value={v} onChange={n => setEq(k, n)} /></label>
              ))}
            </div>
          </div>
          <div>
            <p className="text-xs font-semibold text-slate-600 mb-1">Other assets — % price change for a +1 pp rise</p>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-x-6 gap-y-1">
              {Object.entries(draft.asset_sensitivity).map(([k, v]) => (
                <label key={k} className="flex items-center justify-between gap-2 text-xs text-slate-600">{k}<NumField value={v} onChange={n => setAsset(k, n)} /></label>
              ))}
            </div>
          </div>
          <div>
            <p className="text-xs font-semibold text-slate-600 mb-1">Bond assumptions (years of duration, used only when no better figure exists)</p>
            <div className="grid grid-cols-2 gap-x-6 gap-y-1">
              {Object.entries(draft.bond).map(([k, v]) => (
                <label key={k} className="flex items-center justify-between gap-2 text-xs text-slate-600">{k.replace(/_/g, ' ')}<NumField value={v} onChange={n => setBond(k, n)} /></label>
              ))}
            </div>
          </div>
          <button className="text-xs text-blue-600 hover:underline" onClick={() => setDraft(defaults.rate_shock)}>Reset to defaults</button>
        </div>
      </Collapsible>
    </div>
  )
}

// ═════════════════════════════════════════════════════════════════════════════
// Historical crash replay
// ═════════════════════════════════════════════════════════════════════════════
function CoverageBar({ coverage }: { coverage: ReplayScenario['summary']['coverage'] }) {
  return (
    <div>
      <div className="flex h-2.5 rounded overflow-hidden bg-slate-100">
        {(['own', 'peers', 'rule', 'assumption'] as const).map(k => coverage[k] > 0 && (
          <div key={k} style={{ width: `${coverage[k]}%`, background: BASIS_STYLE[k].color }} title={`${BASIS_STYLE[k].label}: ${coverage[k]}%`} />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-0.5 mt-1 text-xs text-slate-500">
        {(['own', 'peers', 'rule', 'assumption'] as const).map(k => (
          <Tooltip key={k} text={BASIS_STYLE[k].tip}>
            <span className="inline-flex items-center gap-1"><span className="inline-block w-2 h-2 rounded-full" style={{ background: BASIS_STYLE[k].color }} />{BASIS_STYLE[k].label} {coverage[k].toFixed(0)}%</span>
          </Tooltip>
        ))}
      </div>
    </div>
  )
}

function ReplaySection({ accountIds, defaults }: { accountIds?: number[]; defaults: Assumptions }) {
  const { isDark } = useTheme()
  const scenarios = defaults.replay.scenarios
  const [picked, setPicked] = useState<string[]>(scenarios.map(s => s.id))
  const [useCustom, setUseCustom] = useState(false)
  const [custom, setCustom] = useState({ name: 'My window', start: '2015-06-01', end: '2016-02-11', yield: 0 })
  const [fallback, setFallback] = useState(defaults.replay.fallback_returns)
  const [yields, setYields] = useState<Record<string, number>>(Object.fromEntries(scenarios.map(s => [s.id, s.yield_change_pp])))
  const [applied, setApplied] = useState<{ picked: string[]; custom: typeof custom | null; fallback: typeof fallback; yields: typeof yields } | null>(null)
  const [openId, setOpenId] = useState<string | null>(null)

  const { data, isFetching, error } = useQuery({
    queryKey: ['stress-replay', accountIds, applied],
    queryFn: () => runStressReplay({
      accountIds, scenarios: applied!.picked,
      custom: applied!.custom ? { name: applied!.custom.name, start: applied!.custom.start, end: applied!.custom.end, yield_change_pp: applied!.custom.yield } : undefined,
      assumptions: { replay: { fallback_returns: applied!.fallback, yield_change_pp: applied!.yields } },
    }),
    enabled: applied != null,
  })
  useEffect(() => { setApplied({ picked: scenarios.map(s => s.id), custom: null, fallback: defaults.replay.fallback_returns, yields }) }, []) // eslint-disable-line react-hooks/exhaustive-deps
  const res = data as ReplayResult | undefined
  const run = () => setApplied({ picked, custom: useCustom ? custom : null, fallback, yields })
  const toggle = (id: string) => setPicked(p => p.includes(id) ? p.filter(x => x !== id) : [...p, id])
  const classes = ['Stock', 'EquityFund', 'Crypto', 'Gold', 'Commodity', 'Other']

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <p className="text-xs font-medium text-slate-600"><Tooltip text="Each window runs from a pre-crash level to the low. The replay applies that window's moves to what you hold today.">Crashes to replay</Tooltip></p>
        <div className="flex flex-wrap gap-x-5 gap-y-1">
          {scenarios.map(s => (
            <label key={s.id} className="flex items-center gap-1.5 text-sm text-slate-700">
              <input type="checkbox" checked={picked.includes(s.id)} onChange={() => toggle(s.id)} />
              <Tooltip text={`${s.desc} (${s.start} → ${s.end})`}>{s.name}</Tooltip>
            </label>
          ))}
          <label className="flex items-center gap-1.5 text-sm text-slate-700">
            <input type="checkbox" checked={useCustom} onChange={() => setUseCustom(!useCustom)} /> Custom window
          </label>
        </div>
        {useCustom && (
          <div className="flex flex-wrap items-end gap-3">
            <label className="text-xs text-slate-500">Name<input className="block w-40 rounded border border-slate-300 px-2 py-1 text-sm" value={custom.name} onChange={e => setCustom({ ...custom, name: e.target.value })} /></label>
            <label className="text-xs text-slate-500">From<input type="date" className="block rounded border border-slate-300 px-2 py-1 text-sm" value={custom.start} onChange={e => setCustom({ ...custom, start: e.target.value })} /></label>
            <label className="text-xs text-slate-500">To<input type="date" className="block rounded border border-slate-300 px-2 py-1 text-sm" value={custom.end} onChange={e => setCustom({ ...custom, end: e.target.value })} /></label>
            <label className="text-xs text-slate-500">Rates moved (pp)<span className="block"><NumField value={custom.yield} onChange={n => setCustom({ ...custom, yield: n })} /></span></label>
          </div>
        )}
        <Button onClick={run} disabled={isFetching || (picked.length === 0 && !useCustom)}>{isFetching ? 'Running…' : 'Run'}</Button>
      </div>

      {error && <p className="text-xs text-red-600 bg-red-50 rounded px-3 py-2">{String((error as { response?: { data?: { detail?: string } }; message?: string })?.response?.data?.detail ?? (error as Error).message)}</p>}
      {isFetching && !res && <div className="flex justify-center py-8"><Spinner /></div>}

      {res && res.scenarios.length === 0 && <p className="text-sm text-slate-500">No holdings in the selected accounts.</p>}
      {res && res.scenarios.length > 0 && (
        <>
          <Plot data={[{ type: 'bar', x: res.scenarios.map(s => s.name), y: res.scenarios.map(s => s.summary.pct),
              text: res.scenarios.map(s => `${pctTxt(s.summary.pct, 1)}<br>${signed(s.summary.impact)}`), textposition: 'outside',
              marker: { color: res.scenarios.map(s => s.summary.pct < 0 ? '#ef4444' : '#10b981') } }]}
            layout={{ height: 280, margin: { t: 20, r: 20, b: 60, l: 60 }, yaxis: { title: 'Portfolio change (%)', ticksuffix: '%' }, ...plotLayout(isDark) }}
            config={{ displayModeBar: false }} style={{ width: '100%' }} />

          {res.scenarios.map(sc => (
            <div key={sc.id} className="border border-slate-200 rounded-lg p-3 space-y-3">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <div>
                  <p className="text-sm font-semibold text-slate-800">{sc.name}</p>
                  <p className="text-xs text-slate-400">{sc.start} → {sc.end}{sc.desc ? ` · ${sc.desc}` : ''} · assumed rate move {sc.yield_change_pp >= 0 ? '+' : ''}{sc.yield_change_pp} pp</p>
                </div>
                <div className="text-right">
                  <p className={`text-lg font-bold ${tone(sc.summary.impact)}`}>{signed(sc.summary.impact)} <span className="text-sm">({pctTxt(sc.summary.pct, 2)})</span></p>
                  <p className="text-xs text-slate-400">of {fmtEur(sc.summary.value)}</p>
                </div>
              </div>
              <CoverageBar coverage={sc.summary.coverage} />
              <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
                <RollupTable title="By account" rows={sc.by_account} first="account" />
                <RollupTable title="By asset type" rows={sc.by_class} first="asset_class" />
              </div>
              <button className="text-xs text-blue-600 hover:underline" onClick={() => setOpenId(openId === sc.id ? null : sc.id)}>
                {openId === sc.id ? 'Hide per-security detail' : 'Show per-security detail (with the basis used for each)'}
              </button>
              {openId === sc.id && <SecurityTable rows={sc.rows} mode="replay" />}
            </div>
          ))}
          <Notes items={res.notes} />
        </>
      )}

      <Collapsible title="Assumptions you can change (applied on the next Run)">
        <div className="space-y-4">
          <div>
            <p className="text-xs font-semibold text-slate-600 mb-1">Assumed average change in euro yields during each window (pp) — drives bonds, T-bills and bond funds that have no history</p>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-x-6 gap-y-1">
              {scenarios.map(s => (
                <label key={s.id} className="flex items-center justify-between gap-2 text-xs text-slate-600">{s.name}<NumField value={yields[s.id] ?? 0} onChange={n => setYields(y => ({ ...y, [s.id]: n }))} /></label>
              ))}
            </div>
          </div>
          <div>
            <p className="text-xs font-semibold text-slate-600 mb-1">Last-resort return (%) for a holding with no history and nothing similar to borrow from</p>
            <div className="overflow-x-auto">
              <table className="text-xs">
                <thead><tr><th className="px-2 py-1 text-left text-slate-500">Window</th>{classes.map(c => <th key={c} className="px-2 py-1 text-right text-slate-500">{c === 'EquityFund' ? 'Equity fund' : c}</th>)}</tr></thead>
                <tbody>
                  {Object.entries(fallback).map(([sid, row]) => (
                    <tr key={sid} className="border-t border-slate-100">
                      <td className="px-2 py-1 text-slate-600">{scenarios.find(s => s.id === sid)?.name ?? 'Custom window'}</td>
                      {classes.map(c => (
                        <td key={c} className="px-1 py-1"><NumField value={row[c] ?? 0} className="w-16"
                          onChange={n => setFallback(f => ({ ...f, [sid]: { ...f[sid], [c]: n } }))} /></td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          <button className="text-xs text-blue-600 hover:underline" onClick={() => { setFallback(defaults.replay.fallback_returns); setYields(Object.fromEntries(scenarios.map(s => [s.id, s.yield_change_pp]))) }}>Reset to defaults</button>
        </div>
      </Collapsible>
    </div>
  )
}

// ═════════════════════════════════════════════════════════════════════════════
export default function StressTestTab({ accountIds }: { accountIds?: number[] }) {
  const [mode, setMode] = useState<'rate' | 'replay'>('rate')
  const { data: defaults, isLoading } = useQuery({ queryKey: ['stress-assumptions'], queryFn: getStressAssumptions, staleTime: Infinity })
  const d = defaults as Assumptions | undefined
  return (
    <div className="space-y-4">
      <div className="text-xs text-slate-500">
        <p>What-ifs, not forecasts: how your holdings would react to a change in interest rates, or to a repeat of a past crash. Both follow the account preset selected above, show every assumption they rely on, and let you change them.</p>
      </div>
      <div className="flex gap-2">
        {([['rate', 'Rate shock'], ['replay', 'Historical crash replay']] as const).map(([k, label]) => (
          <button key={k} onClick={() => setMode(k)}
            className={`px-3 py-1.5 text-sm rounded border ${mode === k ? 'bg-blue-600 text-white border-blue-600' : 'border-slate-300 text-slate-600 hover:bg-slate-50'}`}>{label}</button>
        ))}
      </div>
      {isLoading || !d ? <div className="flex justify-center py-12"><Spinner /></div> : (
        <>
          <div style={{ display: mode === 'rate' ? 'block' : 'none' }}><RateShockSection accountIds={accountIds} defaults={d} /></div>
          <div style={{ display: mode === 'replay' ? 'block' : 'none' }}><ReplaySection accountIds={accountIds} defaults={d} /></div>
        </>
      )}
    </div>
  )
}
