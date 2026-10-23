import { useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { getAccounts, getAccountBreakdown, type KpiAccount } from '@/lib/api'
import { PageHeader, StatCard, Button, Spinner } from '@/components/ui'
import { fmtEur, fmtCur } from '@/lib/utils'
import { useLiveRefetchInterval } from '@/lib/hooks'
import { loadOpts, historyAccountIds } from '@/lib/dashboardOpts'
import { INVESTMENT_ACCOUNT_TYPES } from '@/lib/accountTypes'
import { setPref } from '@/lib/preferences'

// A Dashboard KPI card (Cash, Investments, Pension & Savings, Assets) opened up: the same card for every account that makes it up — value, change
// vs previous month and daily on one line, YTD on the next — and a click on an account opens its transactions.
const TITLES: Record<string, string> = { cash: 'Cash', investments: 'Investments', pension: 'Pension & Savings', assets: 'Assets' }

// The account types of each KPI, in the order they are listed. An account type not named here still shows, after these.
const TYPE_ORDER: Record<string, string[]> = {
  cash: ['Checking', 'Cash', 'Credit Card', 'Loan', 'Other'],
  investments: ['Brokerage', 'Margin', 'Other Investment'],
  pension: ['Pension', 'Savings'],
  assets: ['Real Estate', 'Vehicle', 'Asset', 'Liability'],
}

const fmtDelta = (v: number) => `${v >= 0 ? '+' : ''}${fmtEur(v)}`
const deltaColor = (v: number) => v === 0 ? 'text-slate-400' : v > 0 ? 'text-green-600' : 'text-red-600'

function kpiSubs(d: { delta_prev_month: number; delta_daily: number; delta_ytd: number }, extra?: string) {
  return [
    { text: '', parts: [
      { text: `${fmtDelta(d.delta_prev_month)} vs prev month`, color: deltaColor(d.delta_prev_month) },
      { text: `${fmtDelta(d.delta_daily)} daily`, color: deltaColor(d.delta_daily) },
    ] },
    { text: `${fmtDelta(d.delta_ytd)} YTD`, color: deltaColor(d.delta_ytd) },
    ...(extra ? [{ text: extra }] : []),
  ]
}

export default function DashboardAccounts() {
  const { group = 'cash' } = useParams()
  const navigate = useNavigate()
  const liveRefetchMs = useLiveRefetchInterval()
  const opts = useMemo(loadOpts, [])
  const title = TITLES[group] ?? 'Accounts'
  const [showEmpty, setShowEmpty] = useState(false)

  const { data: accounts = [] } = useQuery({ queryKey: ['accounts', opts.includeFuture], queryFn: () => getAccounts(opts.includeFuture) })
  const ids = useMemo(() => historyAccountIds(accounts as Record<string, unknown>[], opts), [accounts, opts])
  const { data, isLoading, isError } = useQuery({
    queryKey: ['dash-account-breakdown', group, ids.join(','), opts.includeFuture],
    queryFn: () => getAccountBreakdown(group, ids, opts.includeFuture),
    enabled: ids.length > 0 && group in TITLES,
    refetchInterval: liveRefetchMs,
  })

  const all = data?.accounts ?? []
  const isEmpty = (a: KpiAccount) => a.balance_eur === 0 && a.delta_daily === 0 && a.delta_prev_month === 0 && a.delta_ytd === 0
  const shown = showEmpty ? all : all.filter(a => !isEmpty(a))
  // by account type, in the KPI's own order; the accounts of a type largest first (the endpoint already sorts them so)
  const sections = useMemo(() => {
    const order = TYPE_ORDER[group] ?? []
    const byType = new Map<string, KpiAccount[]>()
    for (const a of shown) byType.set(a.type, [...(byType.get(a.type) ?? []), a])
    const types = [...byType.keys()].sort((x, y) => (order.indexOf(x) === -1 ? 99 : order.indexOf(x)) - (order.indexOf(y) === -1 ? 99 : order.indexOf(y)))
    return types.map(t => {
      const list = byType.get(t)!
      const sum = (k: 'balance_eur' | 'delta_daily' | 'delta_prev_month' | 'delta_ytd') => list.reduce((s, a) => s + a[k], 0)
      return { type: t, list, total: { balance_eur: sum('balance_eur'), delta_daily: sum('delta_daily'), delta_prev_month: sum('delta_prev_month'), delta_ytd: sum('delta_ytd') } }
    })
  }, [shown, group])
  const open = (a: KpiAccount) => navigate(INVESTMENT_ACCOUNT_TYPES.includes(a.type)
    ? `/investments?accountsId=${a.id}&tab=transactions&from=report`
    : `/register?accountsId=${a.id}&from=report`)

  return (
    <div>
      <PageHeader title={`${title} accounts`} subtitle={`Every account in the ${title} card — its value, and its change vs the previous month, today and since the start of the year. Click one to open its transactions.`}
        actions={<>
          {group === 'investments' && <Button size="sm" variant="secondary" onClick={() => { setPref('reports_active_tab', 'inv-performance'); setPref('inv_perf_tab', 'P&L'); navigate('/reports') }}>Performance (P&amp;L)</Button>}
          <Button size="sm" variant="secondary" onClick={() => navigate('/')}>← Dashboard</Button>
        </>} />
      <div className="p-4 sm:p-6 space-y-4">
        {!(group in TITLES) && <p className="text-sm text-red-600">Unknown group.</p>}
        {isLoading && <div className="flex justify-center py-12"><Spinner /></div>}
        {isError && <p className="text-sm text-red-600">Could not load the accounts.</p>}
        {data && (
          <>
            <div className="max-w-sm">
              <StatCard label={`${title} — all accounts`} value={fmtEur(data.total.balance_eur)} subs={kpiSubs(data.total, `${shown.length} account${shown.length === 1 ? '' : 's'} shown`)} />
            </div>
            <label className="flex items-center gap-2 text-xs text-slate-600 cursor-pointer">
              <input type="checkbox" className="rounded" checked={showEmpty} onChange={e => setShowEmpty(e.target.checked)} />
              Show accounts that are empty and did not move ({all.length - all.filter(a => !isEmpty(a)).length})
            </label>
            {sections.map(sec => (
              <section key={sec.type} className="space-y-2">
                <div className="flex flex-wrap items-baseline gap-x-4 gap-y-0.5 border-b border-slate-200 pb-1">
                  <h2 className="text-sm font-semibold text-slate-700">{sec.type} <span className="font-normal text-slate-400">· {sec.list.length} account{sec.list.length === 1 ? '' : 's'}</span></h2>
                  <span className="text-sm font-semibold tabular-nums text-slate-800">{fmtEur(sec.total.balance_eur)}</span>
                  <span className={`text-xs tabular-nums ${deltaColor(sec.total.delta_prev_month)}`}>{fmtDelta(sec.total.delta_prev_month)} vs prev month</span>
                  <span className={`text-xs tabular-nums ${deltaColor(sec.total.delta_daily)}`}>{fmtDelta(sec.total.delta_daily)} daily</span>
                  <span className={`text-xs tabular-nums ${deltaColor(sec.total.delta_ytd)}`}>{fmtDelta(sec.total.delta_ytd)} YTD</span>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-3">
                  {sec.list.map(a => (
                    <StatCard key={a.id} compact label={a.name} value={fmtEur(a.balance_eur)}
                      subs={kpiSubs(a, `${a.currency && a.currency !== 'EUR' ? (a.balance_native != null ? fmtCur(a.balance_native, a.currency) : a.currency) : ''}${a.is_active ? '' : `${a.currency && a.currency !== 'EUR' ? ' · ' : ''}switched off`}` || undefined)}
                      onClick={() => open(a)} />
                  ))}
                </div>
              </section>
            ))}
            {shown.length === 0 && <p className="text-sm text-slate-400">No accounts to show.</p>}
          </>
        )}
      </div>
    </div>
  )
}
