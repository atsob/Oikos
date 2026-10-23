import React from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { AlertCircle, AlertTriangle, Info } from 'lucide-react'
import { getAlerts, dismissAlert, dismissAlertGroup } from '@/lib/api'

// What the Dashboard's alert panel and the Alerts page share: the groups, the way an alert row looks, and the
// triggered-alerts query with its instant (optimistic) dismissals.

export type AlertRow = Record<string, unknown>

// Alert groups, in display order — the same groups as database/alert_groups.py. Each has
// its own header, count, collapse state and "Dismiss group".
export const ALERT_GROUPS: { id: string; label: string; hint: string }[] = [
  { id: 'sr', label: 'Support / Resistance', hint: 'Holdings, Watchlist and the currencies you are exposed to approaching or breaking a support or resistance level (1-year daily closes).' },
  { id: 'price', label: 'Price Alerts', hint: 'Your own above/below thresholds (Market Data → Alerts). Dismissing one silences it until the price crosses back and triggers again.' },
  { id: 'signals', label: 'Signals', hint: 'Signal changes, golden/death cross, trailing stop and Altman Z-Score zone changes.' },
  { id: 'action', label: 'Action', hint: 'A held security whose Action verdict (Securities Analysis → Portfolio Action Signals) moves between Reduce / Sell, Hold and Add — smoothed, so it does not flap day to day.' },
  { id: 'events', label: 'Upcoming Events', hint: 'Bond maturities and coupons, dividend payments and loan payments due soon. Dismissing one silences that event only.' },
  { id: 'corporate', label: 'Corporate Actions & Data', hint: 'Stock splits and split-amount mismatches.' },
  { id: 'portfolio', label: 'Portfolio', hint: 'Allocation drift beyond your thresholds.' },
]
export const LEVEL_RANK: Record<string, number> = { error: 0, warning: 1, info: 2 }
const SCOPE_LABEL: Record<string, string> = { holding: 'Holding', watchlist: 'Watchlist', currency: 'Currency' }

export const alertGroupOf = (a: AlertRow) => (ALERT_GROUPS.some(x => x.id === a.group) ? String(a.group) : 'portfolio')
export const alertTarget = (a: AlertRow) => a.securities_id != null ? `/securities/${a.securities_id}`
  : a.currencies_id != null ? `/currencies/${a.currencies_id}` : null
export const worstLevel = (list: AlertRow[]) => list.reduce((w, a) => Math.min(w, LEVEL_RANK[String(a.level)] ?? 3), 3)
export const levelDot = (rank: number) => rank === 0 ? 'bg-red-500' : rank === 1 ? 'bg-amber-500' : 'bg-blue-500'

export function groupAlerts(rows: AlertRow[]) {
  const m = new Map<string, AlertRow[]>()
  for (const a of rows) {
    const g = alertGroupOf(a)
    m.set(g, [...(m.get(g) ?? []), a])
  }
  for (const list of m.values()) list.sort((x, y) => (LEVEL_RANK[String(x.level)] ?? 3) - (LEVEL_RANK[String(y.level)] ?? 3))
  return m
}

// Messages mark emphasis with **…** — render it as bold instead of showing the asterisks.
export function AlertText({ text }: { text: string }) {
  const parts = text.split(/\*\*(.+?)\*\*/g)
  return <>{parts.map((p, i) => (i % 2 ? <b key={i}>{p}</b> : <React.Fragment key={i}>{p.replace(/(^|\s)_(.+?)_(?=\s|$)/g, '$1$2')}</React.Fragment>))}</>
}

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

export function AlertItem({ a, onOpen, onDismiss }: { a: AlertRow; onOpen: (to: string) => void; onDismiss: (a: AlertRow) => void }) {
  const level = String(a.level ?? 'info')
  const to = alertTarget(a)
  return (
    <div className={`flex gap-2 p-3 rounded-lg border ${levelStyle(level)} ${to ? 'cursor-pointer hover:brightness-95' : ''}`}
      onClick={to ? () => onOpen(to) : undefined}
      title={to ? (a.currencies_id != null ? 'View currency details' : 'View security details') : undefined}>
      {levelIcon(level)}
      <p className="text-sm text-slate-700 flex-1">
        {a.scope != null && (
          <span className="mr-1.5 text-[10px] uppercase tracking-wide font-semibold text-slate-500 bg-white/70 border border-slate-200 rounded px-1 py-px">
            {SCOPE_LABEL[String(a.scope)] ?? String(a.scope)}
          </span>
        )}
        <AlertText text={String(a.message ?? '')} />
      </p>
      <button
        className="shrink-0 text-xs text-slate-400 hover:text-slate-600 underline self-start"
        onClick={(e) => { e.stopPropagation(); onDismiss(a) }}
        title="Dismiss this alert — it comes back only if the event happens afresh">
        Dismiss
      </button>
    </div>
  )
}

/** The triggered alerts, and the two dismissals. All alerts, whatever the Dashboard is set to show. */
export function useTriggeredAlerts() {
  const qc = useQueryClient()
  const { data: alerts = [], isLoading, isFetching, refetch } = useQuery({
    queryKey: ['triggered-alerts'],
    queryFn: getAlerts,
    staleTime: 5 * 60 * 1000,
    refetchInterval: 5 * 60 * 1000,
  })
  // Dismissing removes the just-dismissed row(s) from the cached list directly
  // instead of invalidating/refetching — /dashboard/alerts recomputes several
  // live checks (Golden/Death Cross, Trailing Stop, Support / Resistance, etc.) that
  // take several seconds, and a dismiss doesn't change any of that, only which rows
  // are dismissed. The 5-minute refetchInterval above still keeps the list
  // eventually consistent with the server.
  const removeAlerts = (predicate: (a: AlertRow) => boolean) => {
    qc.setQueryData<AlertRow[]>(['triggered-alerts'], (old) => (old ?? []).filter(a => !predicate(a)))
  }
  // Optimistic: the row(s) leave the list the moment the button is pressed, and come back only if the server refuses.
  const optimistic = (predicate: (a: AlertRow) => boolean) => async () => {
    await qc.cancelQueries({ queryKey: ['triggered-alerts'] })
    const previous = qc.getQueryData<AlertRow[]>(['triggered-alerts'])
    removeAlerts(predicate)
    return { previous }
  }
  const restore = (_e: unknown, _v: unknown, ctx?: { previous?: AlertRow[] }) => {
    if (ctx?.previous) qc.setQueryData(['triggered-alerts'], ctx.previous)
  }
  const dismissOne = useMutation({
    mutationFn: (a: AlertRow) => dismissAlert(a),
    onMutate: (a: AlertRow) => optimistic(x => x.key === a.key)(),
    onError: restore,
  })
  const dismissGroup = useMutation({
    mutationFn: ({ group, list }: { group: string; list: AlertRow[] }) => dismissAlertGroup(group, list),
    onMutate: ({ group }) => optimistic(x => alertGroupOf(x) === group)(),
    onError: restore,
  })
  return { alerts: alerts as AlertRow[], isLoading, isFetching, refetch, dismissOne, dismissGroup }
}
