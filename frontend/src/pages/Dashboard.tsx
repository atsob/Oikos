import React from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import PlotlyReact from 'react-plotly.js'
import {
  getNetWorth, getAccounts, getMonthlySummaries, getWeeklySummaries,
  getDraftTransactions, confirmDraft, confirmAllDrafts, deleteDraft, getInsights,
  generateMonthlySummary, generateWeeklySummary, getAlerts, acknowledgeSignal,
  getUpcomingBills, getAnomalies, syncBalances,
} from '@/lib/api'
import { PageHeader, StatCard, Card, CardHeader, CardTitle, CardBody, Button, Badge, Spinner } from '@/components/ui'
import { fmtEur, fmtDate } from '@/lib/utils'
import {
  CheckCheck, Check, Trash2, AlertTriangle, AlertCircle, Info, TrendingUp,
  ChevronDown, ChevronUp, RefreshCw, Calendar, SlidersHorizontal,
  RefreshCcw, CalendarClock, Zap,
} from 'lucide-react'

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const Plot: React.ComponentType<any> = (PlotlyReact as any).default ?? PlotlyReact

// ── localStorage helpers ──────────────────────────────────────────────────────
const OPTS_KEY = 'oikos-dashboard-opts'
interface DashOpts { includedAccounts: number[] | 'all'; includeFuture: boolean; showDisabled: boolean }
const defaultOpts = (): DashOpts => ({ includedAccounts: 'all', includeFuture: false, showDisabled: false })
function loadOpts(): DashOpts {
  try { return { ...defaultOpts(), ...JSON.parse(localStorage.getItem(OPTS_KEY) || '{}') } }
  catch { return defaultOpts() }
}
function saveOpts(o: DashOpts) { localStorage.setItem(OPTS_KEY, JSON.stringify(o)) }

// ── Insights panel (financial only) ──────────────────────────────────────────
type InsightType = 'warning' | 'danger' | 'success' | 'info'
interface Insight { type: InsightType; icon: string; title: string; message: string }

const I_STYLES: Record<InsightType, { bg: string; border: string; node: React.ReactNode }> = {
  warning: { bg: 'bg-amber-50', border: 'border-amber-300', node: <AlertTriangle size={15} className="text-amber-500 shrink-0" /> },
  danger:  { bg: 'bg-red-50',   border: 'border-red-300',   node: <AlertCircle   size={15} className="text-red-500 shrink-0" /> },
  success: { bg: 'bg-green-50', border: 'border-green-300', node: <TrendingUp    size={15} className="text-green-600 shrink-0" /> },
  info:    { bg: 'bg-blue-50',  border: 'border-blue-300',  node: <Info          size={15} className="text-blue-500 shrink-0" /> },
}

// Securities-related icons are "price_anomaly" or "stale_price" — split them out
const SEC_ICONS = new Set(['price_anomaly', 'stale_price', 'missing_price'])

function InsightsPanel({ insights }: { insights: Insight[] }) {
  const [open, setOpen] = React.useState(false)
  const financial = insights.filter(i => !SEC_ICONS.has(i.icon))
  if (!financial.length) return null
  const hasDanger = financial.some(i => i.type === 'danger')
  return (
    <Card>
      <button className="w-full flex items-center justify-between px-4 py-3 text-left" onClick={() => setOpen(o => !o)}>
        <span className={`text-sm font-semibold ${hasDanger ? 'text-red-700' : 'text-amber-700'}`}>
          {hasDanger ? '🚨' : '💡'} {financial.length} financial insight{financial.length !== 1 ? 's' : ''}
        </span>
        {open ? <ChevronUp size={15} className="text-slate-400" /> : <ChevronDown size={15} className="text-slate-400" />}
      </button>
      {open && (
        <CardBody className="pt-0 space-y-2">
          {financial.map((ins, i) => {
            const s = I_STYLES[ins.type]
            return (
              <div key={i} className={`flex gap-3 p-3 rounded-lg border ${s.bg} ${s.border}`}>
                <div className="mt-0.5">{s.node}</div>
                <div>
                  <p className="text-sm font-semibold text-slate-800">{ins.title}</p>
                  <p className="text-xs text-slate-600 mt-0.5">{ins.message}</p>
                </div>
              </div>
            )
          })}
          <p className="text-xs text-slate-400 pt-1">Based on last 90 days of transactions</p>
        </CardBody>
      )}
    </Card>
  )
}

function SecuritiesAlertsPanel() {
  const qc = useQueryClient()
  const { data: alerts = [] } = useQuery({
    queryKey: ['triggered-alerts'],
    queryFn: getAlerts,
    staleTime: 5 * 60 * 1000,
  })
  const ackMut = useMutation({
    mutationFn: (sid: number) => acknowledgeSignal(sid),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['triggered-alerts'] }),
  })
  const [open, setOpen] = React.useState(false)
  if (!(alerts as unknown[]).length) return null

  const levelStyle = (level: string) => {
    if (level === 'error') return 'bg-red-50 border-red-300'
    if (level === 'warning') return 'bg-amber-50 border-amber-300'
    return 'bg-blue-50 border-blue-300'
  }
  const levelIcon = (level: string) => {
    if (level === 'error') return <AlertCircle size={14} className="text-red-500 shrink-0 mt-0.5" />
    if (level === 'warning') return <AlertTriangle size={14} className="text-amber-500 shrink-0 mt-0.5" />
    return <Info size={14} className="text-blue-500 shrink-0 mt-0.5" />
  }

  return (
    <Card>
      <button className="w-full flex items-center justify-between px-4 py-3 text-left" onClick={() => setOpen(o => !o)}>
        <span className="text-sm font-semibold text-orange-700">
          🔔 {(alerts as unknown[]).length} triggered alert{(alerts as unknown[]).length !== 1 ? 's' : ''}
        </span>
        {open ? <ChevronUp size={15} className="text-slate-400" /> : <ChevronDown size={15} className="text-slate-400" />}
      </button>
      {open && (
        <CardBody className="pt-0 space-y-2">
          {(alerts as Record<string, unknown>[]).map((a, i) => {
            const level = String(a.level ?? 'info')
            const isSignal = a.type === 'signal_change' && a.securities_id != null
            return (
              <div key={i} className={`flex gap-2 p-3 rounded-lg border ${levelStyle(level)}`}>
                {levelIcon(level)}
                <p className="text-sm text-slate-700 flex-1">{String(a.message ?? '')}</p>
                {isSignal && (
                  <button
                    className="shrink-0 text-xs text-slate-400 hover:text-slate-600 underline"
                    onClick={() => ackMut.mutate(Number(a.securities_id))}
                    title="Dismiss this notification">
                    Dismiss
                  </button>
                )}
              </div>
            )
          })}
        </CardBody>
      )}
    </Card>
  )
}

// ── AI Summary panel (shared by weekly + monthly) ─────────────────────────────
function AISummaryPanel({
  label, summaries, periods, generateFn, queryKey, dateField,
}: {
  label: string
  summaries: Record<string, unknown>[]
  periods: string[]           // ISO date strings for the picker
  generateFn: (d: string) => Promise<unknown>
  queryKey: string
  dateField: string           // 'month_start' | 'week_start'
}) {
  const qc = useQueryClient()
  const [selected, setSelected] = React.useState('')
  const [busy, setBusy] = React.useState(false)
  const [err, setErr] = React.useState<string | null>(null)

  const storedSet = new Set(summaries.map(s => String(s[dateField]).slice(0, 10)))
  const active = selected || (periods[1] ?? periods[0])  // default: last complete period

  const current = summaries.find(s => String(s[dateField]).slice(0, 10) === active.slice(0, 10))

  const fmt = (d: string) => {
    const dt = new Date(d + 'T00:00:00')
    return dateField === 'week_start'
      ? `Week of ${dt.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })}`
      : dt.toLocaleDateString('en-GB', { month: 'long', year: 'numeric' })
  }

  const handleGen = async () => {
    setBusy(true); setErr(null)
    try {
      await generateFn(active)
      await qc.invalidateQueries({ queryKey: [queryKey] })
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Failed')
    } finally { setBusy(false) }
  }

  return (
    <Card>
      <CardHeader className="flex items-center justify-between flex-wrap gap-2">
        <CardTitle>{label}</CardTitle>
        <div className="flex items-center gap-2">
          <Calendar size={13} className="text-slate-400" />
          <select
            value={active}
            onChange={e => setSelected(e.target.value)}
            className="text-xs border border-slate-200 rounded px-2 py-1 bg-white text-slate-700"
          >
            {periods.map(p => (
              <option key={p} value={p}>{fmt(p)}{storedSet.has(p) ? ' ✓' : ''}</option>
            ))}
          </select>
          <Button size="sm" variant="secondary" onClick={handleGen} disabled={busy}>
            <RefreshCw size={12} className={busy ? 'animate-spin' : ''} />
            {busy ? 'Generating…' : current ? 'Regenerate' : 'Generate'}
          </Button>
        </div>
      </CardHeader>
      <CardBody className="pt-0">
        {err && <p className="text-xs text-red-600 bg-red-50 px-3 py-2 rounded mb-3">{err}</p>}
        {current ? (
          <div className="border-l-4 border-blue-400 pl-4">
            <Badge label={fmt(active)} variant="blue" />
            <p className="text-sm text-slate-700 mt-2 whitespace-pre-wrap leading-relaxed">
              {String(current.summary_text)}
            </p>
          </div>
        ) : (
          <p className="text-sm text-slate-400 text-center py-4">
            No summary for {fmt(active)} — click Generate to create one.
          </p>
        )}
      </CardBody>
    </Card>
  )
}

// ── Options + Account selector ────────────────────────────────────────────────
function OptionsPanel({
  accounts, opts, onChange,
}: {
  accounts: Record<string, unknown>[]
  opts: DashOpts
  onChange: (o: DashOpts) => void
}) {
  const [open, setOpen] = React.useState(false)
  const visible = opts.showDisabled ? accounts : accounts.filter(a => a.is_active !== false)
  const all = opts.includedAccounts === 'all'
  const selected = new Set<number>(all ? visible.map(a => Number(a.id)) : (opts.includedAccounts as number[]))

  const toggle = (id: number) => {
    const next = new Set(selected)
    next.has(id) ? next.delete(id) : next.add(id)
    const arr = [...next]
    onChange({ ...opts, includedAccounts: arr.length === visible.length ? 'all' : arr })
  }

  const toggleAll = () => onChange({ ...opts, includedAccounts: all ? [] : 'all' })

  const groups = visible.reduce((acc: Record<string, Record<string, unknown>[]>, a) => {
    const t = String(a.type || 'Other')
    if (!acc[t]) acc[t] = []
    acc[t].push(a)
    return acc
  }, {})

  return (
    <Card>
      <button className="w-full flex items-center justify-between px-4 py-3 text-left" onClick={() => setOpen(o => !o)}>
        <span className="flex items-center gap-2 text-sm font-semibold text-slate-700">
          <SlidersHorizontal size={14} />
          Options &amp; Account Selection
          {!all && <span className="text-xs font-normal text-blue-600">({(opts.includedAccounts as number[]).length} of {visible.length} accounts)</span>}
        </span>
        {open ? <ChevronUp size={15} className="text-slate-400" /> : <ChevronDown size={15} className="text-slate-400" />}
      </button>
      {open && (
        <CardBody className="pt-0 space-y-4">
          {/* Options */}
          <div className="space-y-2">
            <label className="flex items-center gap-2 text-sm text-slate-700 cursor-pointer">
              <input
                type="checkbox"
                checked={opts.includeFuture}
                onChange={e => onChange({ ...opts, includeFuture: e.target.checked })}
                className="rounded"
              />
              Include future-dated (non-draft) transactions in balances
            </label>
            <label className="flex items-center gap-2 text-sm text-slate-700 cursor-pointer">
              <input
                type="checkbox"
                checked={opts.showDisabled}
                onChange={e => onChange({ ...opts, showDisabled: e.target.checked })}
                className="rounded"
              />
              Show disabled accounts
            </label>
          </div>
          {/* Account selector */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide">Accounts in net worth</p>
              <button className="text-xs text-blue-600 hover:underline" onClick={toggleAll}>
                {all ? 'Deselect all' : 'Select all'}
              </button>
            </div>
            <div className="space-y-1 max-h-56 overflow-y-auto">
              {Object.entries(groups).map(([type, accs]) => (
                <div key={type}>
                  <p className="text-xs text-slate-400 uppercase tracking-wide py-1">{type}</p>
                  {accs.map(a => (
                    <label key={String(a.id)} className="flex items-center gap-2 text-sm text-slate-700 cursor-pointer py-0.5 hover:text-slate-900">
                      <input
                        type="checkbox"
                        checked={selected.has(Number(a.id))}
                        onChange={() => toggle(Number(a.id))}
                        className="rounded"
                      />
                      <span className="flex-1 truncate">{String(a.name)}</span>
                      <span className="text-xs text-slate-400 shrink-0">{String(a.currency)}</span>
                    </label>
                  ))}
                </div>
              ))}
            </div>
          </div>
        </CardBody>
      )}
    </Card>
  )
}

// ── Accounts list (collapsible) ───────────────────────────────────────────────
function AccountsPanel({ accounts, opts }: { accounts: Record<string, unknown>[]; opts: DashOpts }) {
  const [open, setOpen] = React.useState(false)

  const visible = opts.showDisabled ? accounts : accounts.filter(a => a.is_active !== false)
  const included = opts.includedAccounts === 'all'
    ? visible
    : visible.filter(a => (opts.includedAccounts as number[]).includes(Number(a.id)))

  const groups = included.reduce((acc: Record<string, Record<string, unknown>[]>, a) => {
    const t = String(a.type || 'Other')
    if (!acc[t]) acc[t] = []
    acc[t].push(a)
    return acc
  }, {})

  const total = included.reduce((s, a) => s + Number(a.balance_eur ?? a.balance ?? 0), 0)

  return (
    <Card>
      <button className="w-full flex items-center justify-between px-4 py-3 text-left" onClick={() => setOpen(o => !o)}>
        <span className="text-sm font-semibold text-slate-700">Accounts</span>
        <div className="flex items-center gap-3">
          <span className="text-sm font-semibold text-slate-700 tabular-nums">{fmtEur(total)}</span>
          {open ? <ChevronUp size={15} className="text-slate-400" /> : <ChevronDown size={15} className="text-slate-400" />}
        </div>
      </button>
      {open && (
        <div className="divide-y divide-slate-100 border-t border-slate-100">
          {Object.entries(groups).map(([type, accs]) => {
            const groupTotal = accs.reduce((s, a) => s + Number(a.balance_eur ?? a.balance ?? 0), 0)
            return (
              <div key={type}>
                <div className="flex items-center justify-between px-4 py-1.5 bg-slate-50">
                  <span className="text-xs font-semibold text-slate-500 uppercase tracking-wide">{type}</span>
                  <span className="text-xs font-semibold text-slate-500 tabular-nums">{fmtEur(groupTotal)}</span>
                </div>
                {accs.map(a => {
                  const isEur = String(a.currency) === 'EUR'
                  const isInvestment = ['Brokerage', 'Margin', 'Other Investment', 'Pension'].includes(String(a.type))
                  const localAmt = Number(a.balance ?? 0)
                  const eurAmt = Number(a.balance_eur ?? 0)
                  // "35,364 HUF" shown as secondary for non-EUR cash accounts
                  const localStr = `${Math.abs(localAmt).toLocaleString('el-GR', { minimumFractionDigits: 0, maximumFractionDigits: 0 })} ${String(a.currency)}`
                  return (
                    <div key={String(a.id)} className="flex items-center justify-between px-4 py-2 hover:bg-slate-50">
                      <div className="min-w-0 flex items-center gap-1.5">
                        <span className="text-sm text-slate-800 truncate">{String(a.name)}</span>
                        {a.institution ? <span className="text-xs text-slate-400 shrink-0">· {String(a.institution)}</span> : null}
                      </div>
                      <div className="flex items-center gap-2 ml-4 shrink-0">
                        {!isEur && !isInvestment && (
                          <span className="text-xs text-slate-400 tabular-nums">{localStr}</span>
                        )}
                        <span className={`text-sm font-semibold tabular-nums ${eurAmt < 0 ? 'text-red-600' : 'text-slate-800'}`}>
                          {fmtEur(eurAmt)}
                        </span>
                      </div>
                    </div>
                  )
                })}
              </div>
            )
          })}
        </div>
      )}
    </Card>
  )
}

// ── Upcoming Bills ────────────────────────────────────────────────────────────
function UpcomingBillsPanel() {
  const [days, setDays] = React.useState(14)
  const [open, setOpen] = React.useState(true)
  const { data: bills = [], isLoading } = useQuery({
    queryKey: ['upcoming-bills', days],
    queryFn: () => getUpcomingBills(days),
    staleTime: 5 * 60 * 1000,
  })
  const list = bills as Record<string, unknown>[]

  return (
    <Card>
      <button className="w-full flex items-center justify-between px-4 py-3 text-left" onClick={() => setOpen(o => !o)}>
        <span className="flex items-center gap-2 text-sm font-semibold text-slate-700">
          <CalendarClock size={14} className="text-blue-500" />
          Upcoming Bills
          {list.length > 0 && <span className="text-xs bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded-full">{list.length}</span>}
        </span>
        <div className="flex items-center gap-2">
          <select
            value={days}
            onChange={e => { e.stopPropagation(); setDays(Number(e.target.value)) }}
            onClick={e => e.stopPropagation()}
            className="text-xs border border-slate-200 rounded px-1.5 py-0.5 bg-white text-slate-600"
          >
            {[7, 14, 30, 60].map(d => <option key={d} value={d}>Next {d}d</option>)}
          </select>
          {open ? <ChevronUp size={14} className="text-slate-400" /> : <ChevronDown size={14} className="text-slate-400" />}
        </div>
      </button>
      {open && (
        <CardBody className="p-0">
          {isLoading ? (
            <div className="flex justify-center py-4"><Spinner /></div>
          ) : list.length === 0 ? (
            <p className="text-sm text-slate-400 px-4 py-5 text-center">No bills due in the next {days} days</p>
          ) : (
            <div className="divide-y divide-slate-100 max-h-64 overflow-y-auto">
              {list.map((b, idx) => {
                const dueDate = String(b.date ?? '').slice(0, 10)
                const daysUntil = dueDate
                  ? Math.ceil((new Date(dueDate + 'T00:00:00').getTime() - Date.now()) / 86400000)
                  : NaN
                const overdue = daysUntil < 0
                const isProjected = String(b.type) === 'Projected'
                return (
                  <div key={idx} className="flex items-center justify-between px-4 py-2.5">
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-slate-800 truncate">{String(b.payee || '—')}</p>
                      <p className="text-xs text-slate-400">
                        {dueDate}
                        {b.category ? ` · ${String(b.category)}` : ''}
                        {isProjected ? ' · projected' : ''}
                      </p>
                    </div>
                    <div className="text-right ml-3 shrink-0">
                      <p className={`text-sm font-semibold tabular-nums ${Number(b.amount_eur) < 0 ? 'text-red-600' : 'text-green-600'}`}>
                        {fmtEur(Number(b.amount_eur))}
                      </p>
                      <p className={`text-xs ${overdue ? 'text-red-500 font-semibold' : daysUntil <= 3 ? 'text-amber-500' : 'text-slate-400'}`}>
                        {isNaN(daysUntil) ? '' : overdue ? `${Math.abs(daysUntil)}d overdue` : daysUntil === 0 ? 'Due today' : `in ${daysUntil}d`}
                      </p>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </CardBody>
      )}
    </Card>
  )
}

// ── Unusual Transactions ──────────────────────────────────────────────────────
function AnomaliesPanel() {
  const [days, setDays] = React.useState(30)
  const [open, setOpen] = React.useState(false)
  const { data: anomalies = [], isLoading } = useQuery({
    queryKey: ['anomalies', days],
    queryFn: () => getAnomalies(days),
    staleTime: 10 * 60 * 1000,
  })
  const list = anomalies as Record<string, unknown>[]

  return (
    <Card>
      <button className="w-full flex items-center justify-between px-4 py-3 text-left" onClick={() => setOpen(o => !o)}>
        <span className="flex items-center gap-2 text-sm font-semibold text-slate-700">
          <Zap size={14} className="text-amber-500" />
          Unusual Transactions
          {list.length > 0 && <span className="text-xs bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded-full">{list.length}</span>}
        </span>
        <div className="flex items-center gap-2">
          <select
            value={days}
            onChange={e => { e.stopPropagation(); setDays(Number(e.target.value)) }}
            onClick={e => e.stopPropagation()}
            className="text-xs border border-slate-200 rounded px-1.5 py-0.5 bg-white text-slate-600"
          >
            {[14, 30, 60, 90].map(d => <option key={d} value={d}>Last {d}d</option>)}
          </select>
          {open ? <ChevronUp size={14} className="text-slate-400" /> : <ChevronDown size={14} className="text-slate-400" />}
        </div>
      </button>
      {open && (
        <CardBody className="p-0">
          {isLoading ? (
            <div className="flex justify-center py-4"><Spinner /></div>
          ) : list.length === 0 ? (
            <p className="text-sm text-green-600 px-4 py-5 text-center">No unusual transactions in the last {days} days</p>
          ) : (
            <div className="divide-y divide-slate-100 max-h-64 overflow-y-auto">
              {list.map((a, i) => (
                <div key={i} className="flex items-center justify-between px-4 py-2.5">
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-slate-800 truncate">
                      {String(a.payees_name || a.payee || '—')}
                    </p>
                    <p className="text-xs text-slate-400">{fmtDate(String(a.date))} · {String(a.category || '')}</p>
                  </div>
                  <div className="text-right ml-3 shrink-0">
                    <p className="text-sm font-semibold text-red-600 tabular-nums">{fmtEur(Math.abs(Number(a.amount_eur ?? a.amount ?? 0)))}</p>
                    <p className="text-xs text-amber-600">z={Number(a.z_score ?? 0).toFixed(1)}σ (typical {fmtEur(Number(a.mean_eur ?? 0))})</p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardBody>
      )}
    </Card>
  )
}

// ── Main Dashboard ────────────────────────────────────────────────────────────
export default function Dashboard() {
  const qc = useQueryClient()
  const [opts, setOptsState] = React.useState<DashOpts>(loadOpts)

  const setOpts = (o: DashOpts) => { setOptsState(o); saveOpts(o) }

  const [nwPeriod, setNwPeriod] = React.useState<string>(() =>
    localStorage.getItem('oikos-nw-period') ?? 'All'
  )
  const setNwPeriodSaved = (v: string) => { setNwPeriod(v); localStorage.setItem('oikos-nw-period', v) }

  const NW_PERIODS: Record<string, string> = {
    '1Y': new Date(Date.now() - 365 * 86400000).toISOString().slice(0, 10),
    '3Y': new Date(Date.now() - 3 * 365 * 86400000).toISOString().slice(0, 10),
    '5Y': new Date(Date.now() - 5 * 365 * 86400000).toISOString().slice(0, 10),
    'All': '2000-01-01',
  }

  const { data: netWorth = [], isLoading: nwLoading } = useQuery({
    queryKey: ['net-worth', nwPeriod],
    queryFn: () => getNetWorth(NW_PERIODS[nwPeriod] ?? '2000-01-01'),
  })

  const { data: accounts = [] } = useQuery({
    queryKey: ['accounts', opts.includeFuture],
    queryFn: () => getAccounts(opts.includeFuture),
  })

  const { data: monthlySummaries = [] } = useQuery({
    queryKey: ['monthly-summaries'],
    queryFn: () => getMonthlySummaries(24),
  })

  const { data: weeklySummaries = [] } = useQuery({
    queryKey: ['weekly-summaries'],
    queryFn: () => getWeeklySummaries(12),
  })

  const { data: drafts = [] } = useQuery({
    queryKey: ['draft-transactions'],
    queryFn: getDraftTransactions,
  })

  const { data: insights = [] } = useQuery({
    queryKey: ['insights'],
    queryFn: getInsights,
    staleTime: 5 * 60 * 1000,
  })

  const confirmOne = useMutation({
    mutationFn: confirmDraft,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['draft-transactions'] }),
  })

  const confirmAll = useMutation({
    mutationFn: confirmAllDrafts,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['draft-transactions'] })
      qc.invalidateQueries({ queryKey: ['accounts'] })
    },
  })

  const deleteOne = useMutation({
    mutationFn: deleteDraft,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['draft-transactions'] }),
  })

  // Balance sync
  const [syncing, setSyncing] = React.useState<string | null>(null)
  const [syncMsg, setSyncMsg] = React.useState<string | null>(null)

  const handleSync = async (target: string) => {
    setSyncing(target); setSyncMsg(null)
    try {
      await syncBalances(target)
      setSyncMsg(`${target === 'all' ? 'All balances' : target} synced`)
      await qc.invalidateQueries({ queryKey: ['accounts'], exact: false })
      await qc.invalidateQueries({ queryKey: ['net-worth'], exact: false })
    } catch { setSyncMsg('Sync failed') }
    finally { setSyncing(null) }
  }

  // Filtered accounts based on options
  const accs = accounts as Record<string, unknown>[]
  const visibleAccs = opts.showDisabled ? accs : accs.filter(a => a.is_active !== false)
  const included = opts.includedAccounts === 'all'
    ? visibleAccs
    : visibleAccs.filter(a => (opts.includedAccounts as number[]).includes(Number(a.id)))

  // Aggregate KPIs from included accounts (grouped by type)
  const CASH_TYPES = new Set(['Cash', 'Checking', 'Savings', 'Credit Card', 'Loan', 'Other'])
  const INV_TYPES  = new Set(['Brokerage', 'Margin', 'Other Investment'])
  const PEN_TYPES  = new Set(['Pension'])
  const ASSET_TYPES = new Set(['Real Estate', 'Vehicle', 'Asset', 'Liability'])

  const sumByType = (types: Set<string>) =>
    included.filter(a => types.has(String(a.type))).reduce((s, a) => s + Number(a.balance_eur ?? a.balance ?? 0), 0)

  const totalCash     = sumByType(CASH_TYPES)
  const totalInv      = sumByType(INV_TYPES)
  const totalPension  = sumByType(PEN_TYPES)
  const totalAssets   = sumByType(ASSET_TYPES)
  const totalNetWorth = totalCash + totalInv + totalPension + totalAssets

  // For trend chart — use backend data (all accounts)
  const nwData = netWorth as Record<string, unknown>[]

  const today      = new Date().toISOString().slice(0, 10)
  const yesterday  = new Date(Date.now() - 86400000).toISOString().slice(0, 10)
  const thisMonth  = today.slice(0, 7)
  const thisYear   = today.slice(0, 4)

  const nwReversed = [...nwData].reverse()
  const prevMonthPoint   = nwReversed.find(d => String(d.date).slice(0, 7) < thisMonth)
  const ytdPoint         = nwReversed.find(d => String(d.date).slice(0, 4) < thisYear)
  const yesterdayPoint   = nwData.find(d => String(d.date).slice(0, 10) === yesterday)

  const fmtDelta  = (v: number) => `${v >= 0 ? '+' : ''}${fmtEur(v)}`
  const deltaColor = (v: number | null) => v == null ? 'text-slate-400' : v >= 0 ? 'text-green-600' : 'text-red-600'

  // Net Worth deltas
  const deltaPrevMonth = prevMonthPoint != null ? totalNetWorth - Number(prevMonthPoint.total_net_worth) : null
  const deltaYTD       = ytdPoint       != null ? totalNetWorth - Number(ytdPoint.total_net_worth)       : null

  // Cash & Savings deltas
  const deltaCashPrevMonth = prevMonthPoint != null ? totalCash - Number(prevMonthPoint.total_cash) : null
  const deltaCashYTD       = ytdPoint       != null ? totalCash - Number(ytdPoint.total_cash)       : null

  // Investments deltas
  const deltaInvDaily = yesterdayPoint != null ? totalInv - Number(yesterdayPoint.total_invested) : null
  const deltaInvYTD   = ytdPoint       != null ? totalInv - Number(ytdPoint.total_invested)       : null

  // Pension deltas
  const deltaPenPrevMonth = prevMonthPoint != null ? totalPension - Number(prevMonthPoint.total_pension) : null
  const deltaPenYTD       = ytdPoint       != null ? totalPension - Number(ytdPoint.total_pension)       : null

  // Build period lists for AI summaries
  const monthPeriods: string[] = []
  for (let i = 0; i < 13; i++) {
    const d = new Date(); d.setDate(1); d.setMonth(d.getMonth() - i)
    monthPeriods.push(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-01`)
  }
  // Merge stored older months
  ;(monthlySummaries as Record<string, unknown>[]).forEach(s => {
    const ms = String(s.month_start).slice(0, 10)
    if (!monthPeriods.some(p => p.slice(0, 7) === ms.slice(0, 7))) monthPeriods.push(ms)
  })

  const weekPeriods: string[] = []
  for (let i = 0; i < 12; i++) {
    const d = new Date()
    const dow = d.getDay() || 7
    d.setDate(d.getDate() - dow + 1 - i * 7)  // Monday of week i weeks ago
    weekPeriods.push(d.toISOString().slice(0, 10))
  }
  ;(weeklySummaries as Record<string, unknown>[]).forEach(s => {
    const ws = String(s.week_start).slice(0, 10)
    if (!weekPeriods.includes(ws)) weekPeriods.push(ws)
  })

  return (
    <div>
      <PageHeader
        title="Dashboard"
        subtitle="Net worth overview"
        actions={
          <div className="flex items-center gap-2">
            {syncMsg && <span className="text-xs text-slate-500">{syncMsg}</span>}
            <div className="relative group">
              <Button variant="secondary" size="sm" disabled={!!syncing}>
                <RefreshCcw size={13} className={syncing ? 'animate-spin' : ''} />
                {syncing ? 'Syncing…' : 'Sync Balances'}
                <ChevronDown size={12} />
              </Button>
              <div className="absolute right-0 top-full mt-1 w-52 bg-white border border-slate-200 rounded-lg shadow-lg z-20 hidden group-hover:block">
                {[
                  { label: '🏦 Bank & Cash', target: 'cash' },
                  { label: '📈 Investments', target: 'investment' },
                  { label: '🏛️ Pension', target: 'pension' },
                  { label: '📊 Holdings', target: 'holdings' },
                  { label: '🚀 Run Full Sync', target: 'all' },
                ].map(o => (
                  <button
                    key={o.target}
                    onClick={() => handleSync(o.target)}
                    className={`w-full text-left px-4 py-2 text-sm hover:bg-slate-50 first:rounded-t-lg last:rounded-b-lg ${o.target === 'all' ? 'font-semibold text-blue-600 border-t border-slate-100' : 'text-slate-700'}`}
                  >
                    {o.label}
                  </button>
                ))}
              </div>
            </div>
          </div>
        }
      />

      <div className="p-6 space-y-4">
        {/* KPI row */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard
            label="Net Worth"
            value={fmtEur(totalNetWorth)}
            subs={[
              deltaPrevMonth != null ? { text: `${fmtDelta(deltaPrevMonth)} vs prev month`, color: deltaColor(deltaPrevMonth) } : { text: '— vs prev month' },
              deltaYTD != null ? { text: `${fmtDelta(deltaYTD)} YTD`, color: deltaColor(deltaYTD) } : { text: '— YTD' },
            ]}
          />
          <StatCard
            label="Cash & Savings"
            value={fmtEur(totalCash)}
            subs={[
              deltaCashPrevMonth != null ? { text: `${fmtDelta(deltaCashPrevMonth)} vs prev month`, color: deltaColor(deltaCashPrevMonth) } : { text: '— vs prev month' },
              deltaCashYTD != null ? { text: `${fmtDelta(deltaCashYTD)} YTD`, color: deltaColor(deltaCashYTD) } : { text: '— YTD' },
            ]}
          />
          <StatCard
            label="Investments"
            value={fmtEur(totalInv)}
            subs={[
              deltaInvDaily != null ? { text: `${fmtDelta(deltaInvDaily)} daily`, color: deltaColor(deltaInvDaily) } : { text: '— daily' },
              deltaInvYTD != null ? { text: `${fmtDelta(deltaInvYTD)} YTD`, color: deltaColor(deltaInvYTD) } : { text: '— YTD' },
            ]}
          />
          <StatCard
            label="Pension"
            value={fmtEur(totalPension)}
            subs={[
              deltaPenPrevMonth != null ? { text: `${fmtDelta(deltaPenPrevMonth)} vs prev month`, color: deltaColor(deltaPenPrevMonth) } : { text: '— vs prev month' },
              deltaPenYTD != null ? { text: `${fmtDelta(deltaPenYTD)} YTD`, color: deltaColor(deltaPenYTD) } : { text: '— YTD' },
            ]}
          />
        </div>

        {/* Insights + Drafts */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <div className="lg:col-span-2 space-y-3">
            <InsightsPanel insights={insights as Insight[]} />
            <SecuritiesAlertsPanel />
          </div>

          {/* Pending Drafts */}
          <Card>
            <CardHeader className="flex items-center justify-between">
              <CardTitle>Pending Drafts ({drafts.length})</CardTitle>
              {(drafts as unknown[]).length > 0 && (
                <Button size="sm" variant="secondary" onClick={() => confirmAll.mutate()}>
                  <CheckCheck size={13} /> Confirm All
                </Button>
              )}
            </CardHeader>
            <CardBody className="p-0 max-h-64 overflow-y-auto">
              {(drafts as unknown[]).length === 0 ? (
                <p className="text-sm text-slate-400 px-4 py-6 text-center">No pending drafts</p>
              ) : (
                (drafts as Record<string, unknown>[]).map((d) => (
                  <div key={String(d.id)} className="flex items-center justify-between px-4 py-2.5 border-b border-slate-100 last:border-0">
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-slate-800 truncate">{String(d.description || d.payee || '—')}</p>
                      <p className="text-xs text-slate-400">{fmtDate(String(d.date))} · {String(d.account || '')}</p>
                    </div>
                    <div className="flex items-center gap-1.5 ml-2 shrink-0">
                      <span className={`text-sm font-semibold tabular-nums ${Number(d.amount) < 0 ? 'text-red-600' : 'text-green-600'}`}>
                        {fmtEur(Number(d.amount))}
                      </span>
                      <button className="text-green-600 hover:text-green-700 p-0.5" title="Confirm"
                        onClick={() => confirmOne.mutate(Number(d.id))}>
                        <Check size={15} />
                      </button>
                      <button className="text-slate-400 hover:text-red-500 p-0.5" title="Discard"
                        onClick={() => deleteOne.mutate(Number(d.id))}>
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </div>
                ))
              )}
            </CardBody>
          </Card>
        </div>

        {/* Net Worth Breakdown */}
        <Card>
          <CardHeader><CardTitle>Net Worth Breakdown</CardTitle></CardHeader>
          <CardBody className="p-0">
            {nwLoading ? (
              <div className="flex items-center justify-center h-64"><Spinner /></div>
            ) : (() => {
              const slices = [
                { label: 'Cash & Savings', value: totalCash,    color: '#3b82f6' },  // blue
                { label: 'Investments',    value: totalInv,     color: '#10b981' },  // emerald
                { label: 'Pension',        value: totalPension, color: '#f59e0b' },  // amber
                { label: 'Real Assets',    value: totalAssets,  color: '#ef4444' },  // red
              ].filter(s => s.value > 0)
              return (
                <Plot
                  data={[{
                    values: slices.map(s => s.value),
                    labels: slices.map(s => s.label),
                    marker: { colors: slices.map(s => s.color) },
                    type: 'pie', hole: 0.52,
                    textinfo: 'label+percent',
                    hovertemplate: '<b>%{label}</b><br>€%{value:,.0f}<br>%{percent}<extra></extra>',
                  }]}
                  layout={{
                    height: 280, margin: { t: 16, r: 16, b: 16, l: 16 },
                    showlegend: true,
                    legend: { orientation: 'h', y: -0.05, x: 0.5, xanchor: 'center' },
                    plot_bgcolor: 'white', paper_bgcolor: 'white',
                    annotations: [{
                      text: `€${totalNetWorth.toLocaleString('el-GR', { maximumFractionDigits: 0 })}`,
                      x: 0.5, y: 0.5, xref: 'paper', yref: 'paper',
                      showarrow: false, font: { size: 15, color: '#1e293b', family: 'inherit' },
                    }],
                  }}
                  config={{ displayModeBar: false, responsive: true }}
                  style={{ width: '100%' }}
                />
              )
            })()}
          </CardBody>
        </Card>

        {/* Net Worth Trend */}
        {!nwLoading && nwData.length > 1 && (
          <Card>
            <CardHeader className="flex items-center justify-between">
              <CardTitle>Net Worth Trend</CardTitle>
              <div className="flex gap-1">
                {['1Y', '3Y', '5Y', 'All'].map(p => (
                  <button
                    key={p}
                    onClick={() => setNwPeriodSaved(p)}
                    className={`text-xs px-2 py-0.5 rounded font-medium transition-colors ${
                      nwPeriod === p ? 'bg-blue-600 text-white' : 'text-slate-500 hover:bg-slate-100'
                    }`}
                  >
                    {p}
                  </button>
                ))}
              </div>
            </CardHeader>
            <CardBody className="p-0">
              <Plot
                data={[
                  { x: nwData.map(r => r.date), y: nwData.map(r => Number(r.total_cash ?? 0)), name: 'Cash', stackgroup: 'one', fillcolor: '#3b82f6', line: { color: '#3b82f6' } },
                  { x: nwData.map(r => r.date), y: nwData.map(r => Number(r.total_invested ?? 0)), name: 'Investments', stackgroup: 'one', fillcolor: '#10b981', line: { color: '#10b981' } },
                  { x: nwData.map(r => r.date), y: nwData.map(r => Number(r.total_pension ?? 0)), name: 'Pension', stackgroup: 'one', fillcolor: '#f59e0b', line: { color: '#f59e0b' } },
                  { x: nwData.map(r => r.date), y: nwData.map(r => Number(r.total_assets ?? 0)), name: 'Assets', stackgroup: 'one', fillcolor: '#ef4444', line: { color: '#ef4444' } },
                  { x: nwData.map(r => r.date), y: nwData.map(r => Number(r.total_net_worth ?? 0)), name: 'Net Worth', type: 'scatter', mode: 'lines', line: { color: '#1e40af', width: 2, dash: 'dot' } },
                ]}
                layout={{
                  height: 260, margin: { t: 10, r: 10, b: 40, l: 70 },
                  yaxis: { tickformat: ',.0f', tickprefix: '€' },
                  legend: { orientation: 'h', y: -0.28, x: 0.5, xanchor: 'center' },
                  plot_bgcolor: 'white', paper_bgcolor: 'white', hovermode: 'x unified',
                }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: '100%' }}
              />
            </CardBody>
          </Card>
        )}

        {/* Options & Account selection */}
        <OptionsPanel accounts={visibleAccs} opts={opts} onChange={setOpts} />

        {/* Accounts (collapsible, folded by default) */}
        <AccountsPanel accounts={accs} opts={opts} />

        {/* Upcoming Bills + Unusual Transactions */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <UpcomingBillsPanel />
          <AnomaliesPanel />
        </div>

        {/* AI Summaries */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <AISummaryPanel
            label="Weekly AI Summary"
            summaries={weeklySummaries as Record<string, unknown>[]}
            periods={weekPeriods}
            generateFn={generateWeeklySummary}
            queryKey="weekly-summaries"
            dateField="week_start"
          />
          <AISummaryPanel
            label="Monthly AI Summary"
            summaries={monthlySummaries as Record<string, unknown>[]}
            periods={monthPeriods}
            generateFn={generateMonthlySummary}
            queryKey="monthly-summaries"
            dateField="month_start"
          />
        </div>
      </div>
    </div>
  )
}
