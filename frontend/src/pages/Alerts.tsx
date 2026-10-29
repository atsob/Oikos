import { useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { PageHeader, Button, Spinner } from '@/components/ui'
import { usePersist, useSettings } from '@/lib/hooks'
import { useRole } from '@/lib/auth'
import {
  ALERT_GROUPS, AlertItem, groupAlerts, levelDot, useTriggeredAlerts, worstLevel,
} from '@/components/alerts'

// Every triggered alert, a tab per group — whatever the Dashboard is set to show (Tools → App Settings → Dashboard Alerts).
// Dismissing here is the same as on the Dashboard.
export default function Alerts() {
  const navigate = useNavigate()
  const { isAdmin } = useRole()
  const [settings] = useSettings()
  const { alerts, isLoading, isFetching, refetch, dismissOne, dismissGroup } = useTriggeredAlerts()
  const [tab, setTab] = usePersist('alerts_tab', 'all')
  const byGroup = useMemo(() => groupAlerts(alerts), [alerts])

  const hiddenOnDashboard = (id: string) => settings.dashboardAlertGroups?.[id] === false
  const active = tab === 'all' || ALERT_GROUPS.some(g => g.id === tab) ? tab : 'all'
  const tabs = [{ id: 'all', label: 'All', count: alerts.length, rank: worstLevel(alerts) },
    ...ALERT_GROUPS.map(g => ({ id: g.id, label: g.label, count: byGroup.get(g.id)?.length ?? 0, rank: worstLevel(byGroup.get(g.id) ?? []) }))]

  const section = (id: string, withHeader: boolean) => {
    const g = ALERT_GROUPS.find(x => x.id === id)!
    const list = byGroup.get(id) ?? []
    if (!list.length && withHeader) return null
    return (
      <section key={id} className="space-y-2">
        <div className="flex items-center gap-2">
          {withHeader && (
            <>
              <span className={`w-2 h-2 rounded-full ${levelDot(worstLevel(list))}`} />
              <h2 className="text-sm font-semibold text-slate-700">{g.label}</h2>
              <span className="text-xs text-slate-400">{list.length}</span>
            </>
          )}
          {!withHeader && <p className="text-xs text-slate-500">{g.hint}</p>}
          {hiddenOnDashboard(id) && <span className="text-[10px] uppercase tracking-wide font-semibold text-slate-500 bg-slate-100 border border-slate-200 rounded px-1.5 py-px">Hidden on Dashboard</span>}
          {list.length > 0 && isAdmin && (
            <button className="ml-auto shrink-0 text-xs text-slate-400 hover:text-slate-600 underline"
              onClick={() => dismissGroup.mutate({ group: id, list })}
              title={`Dismiss all ${list.length} ${g.label} alert${list.length !== 1 ? 's' : ''} — the other groups are untouched`}>
              {list.length > 1 ? `Dismiss all ${list.length}` : 'Dismiss'}
            </button>
          )}
        </div>
        {list.length === 0
          ? <p className="text-sm text-slate-400 py-6 text-center">No {g.label} alerts right now.</p>
          : <div className="space-y-2">{list.map(a => <AlertItem key={String(a.key)} a={a} onOpen={navigate} onDismiss={x => dismissOne.mutate(x)} />)}</div>}
      </section>
    )
  }

  return (
    <div>
      <PageHeader title="Alerts" subtitle="Every triggered alert, by group — including the groups hidden on the Dashboard. Dismissing here is the same as on the Dashboard."
        actions={<Button size="sm" variant="secondary" disabled={isFetching} onClick={() => refetch()}>{isFetching ? 'Checking…' : 'Check again'}</Button>} />
      <div className="px-4 sm:px-6 py-4 space-y-4">
        <div className="flex gap-0.5 border-b border-slate-200 overflow-x-auto">
          {tabs.map(t => (
            <button key={t.id} onClick={() => setTab(t.id)}
              className={`px-3 py-1.5 text-sm font-medium border-b-2 -mb-px transition-colors whitespace-nowrap inline-flex items-center gap-1.5 ${active === t.id ? 'border-blue-600 text-blue-700' : 'border-transparent text-slate-500 hover:text-slate-700'}`}>
              {t.count > 0 && <span className={`w-1.5 h-1.5 rounded-full ${levelDot(t.rank)}`} />}
              {t.label}
              <span className={`text-xs ${t.count ? 'text-slate-500' : 'text-slate-300'}`}>{t.count}</span>
            </button>
          ))}
        </div>

        {isLoading ? (
          <div className="flex items-center gap-2 py-6 text-sm text-slate-400"><Spinner size={14} /> Loading alerts… (it recomputes several live checks and can take a few seconds)</div>
        ) : active === 'all' ? (
          alerts.length === 0
            ? <p className="text-sm text-slate-400 py-6 text-center">No triggered alerts right now.</p>
            : <div className="space-y-5">{ALERT_GROUPS.map(g => section(g.id, true))}</div>
        ) : section(active, false)}
      </div>
    </div>
  )
}
