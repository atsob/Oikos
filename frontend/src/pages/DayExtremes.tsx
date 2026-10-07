import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Spinner, Tooltip, CopyToExcelButton } from '@/components/ui'
import { usePersist } from '@/lib/hooks'
import { fmt, fmtEur } from '@/lib/utils'
import { getReportingFx } from '@/lib/settings'
import { getAccounts, getDayExtremes, getDayExtremeYears } from '@/lib/api'

type Contributor = { ticker: string; name: string; pnl: number }
type Period = 'day' | 'week' | 'month'
type Row = {
  label: string; date: string; end: string; weekday: string; days: number; partial: boolean
  pnl: number; pct: number; base: number; contributors: Contributor[]
}
type Result = {
  account: string; year: number; n: number; period: Period; periods: number; up_periods: number; down_periods: number
  trading_days: number; total_pnl: number
  worst_eur: Row[]; worst_pct: Row[]; best_eur: Row[]; best_pct: Row[]; notes: string[]
  excluded: { label: string; date: string; securities: string[] }[]
}
const UNIT: Record<Period, { one: string; many: string; cap: string }> = {
  day: { one: 'day', many: 'days', cap: 'Day' },
  week: { one: 'week', many: 'weeks', cap: 'Week' },
  month: { one: 'month', many: 'months', cap: 'Month' },
}

const INV_TYPES = ['Brokerage', 'Margin', 'Other Investment']
const tone = (n: number) => n < 0 ? 'text-red-600' : n > 0 ? 'text-green-700' : 'text-slate-500'
const signedEur = (n: number) => `${n >= 0 ? '+' : '−'}${fmtEur(Math.abs(n))}`
const pctTxt = (n: number) => `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`
// Same conversion as fmtEur (amounts arrive in the storage base), number only, for the copy.
const rcNum = (v: number) => {
  const { rate } = getReportingFx()
  return fmt(rate === 0 ? 0 : v / rate, 2)
}

function DayTable({ title, tip, rows, period }: { title: string; tip: string; rows: Row[]; period: Period }) {
  const u = UNIT[period]
  return (
    <div>
      <div className="flex items-center justify-between gap-3 mb-1">
        <p className="text-sm font-semibold text-slate-700"><Tooltip text={tip}>{title}</Tooltip></p>
        {rows.length > 0 && (
          <CopyToExcelButton getRows={() => [
            period === 'day'
              ? ['#', 'Date', 'Day', 'P&L', 'P&L %', 'Base', 'Main movers']
              : ['#', u.cap, 'From', 'To', 'Trading days', 'P&L', 'P&L %', 'Base', 'Main movers'],
            ...rows.map((d, i) => period === 'day'
              ? [i + 1, d.date, d.weekday, rcNum(d.pnl), `${d.pct.toFixed(2)}`, rcNum(d.base),
                  d.contributors.map(c => `${c.ticker} ${rcNum(c.pnl)}`).join('; ')]
              : [i + 1, d.label + (d.partial ? ' (so far)' : ''), d.date, d.end, d.days, rcNum(d.pnl), `${d.pct.toFixed(2)}`, rcNum(d.base),
                  d.contributors.map(c => `${c.ticker} ${rcNum(c.pnl)}`).join('; ')]),
          ]} />
        )}
      </div>
      <div className="overflow-x-auto rounded-lg border border-slate-200">
        <table className="w-full text-xs">
          <thead className="bg-slate-50 text-slate-500"><tr>
            <th className="px-2 py-1.5 text-left">#</th><th className="px-2 py-1.5 text-left">{period === 'day' ? 'Date' : u.cap}</th>
            <th className="px-2 py-1.5 text-right">P&amp;L</th><th className="px-2 py-1.5 text-right">%</th>
            <th className="px-2 py-1.5 text-right"><Tooltip text={`The value at the previous ${period === 'day' ? 'close' : `${u.one}'s last close`} plus purchases made ${period === 'day' ? 'that day' : `during the ${u.one}`} — what the % is measured against.`}>Base</Tooltip></th>
            <th className="px-2 py-1.5 text-left">Main movers</th>
          </tr></thead>
          <tbody>
            {rows.length === 0 && <tr><td colSpan={6} className="px-3 py-4 text-center text-slate-400">No {u.many}.</td></tr>}
            {rows.map((d, i) => (
              <tr key={d.label} className="border-t border-slate-100 align-top">
                <td className="px-2 py-1.5 text-slate-400">{i + 1}</td>
                <td className="px-2 py-1.5 whitespace-nowrap">
                  {d.label}
                  {period === 'day' && <span className="text-slate-400"> {d.weekday}</span>}
                  {d.partial && <span className="text-amber-600"> (so far)</span>}
                </td>
                <td className={`px-2 py-1.5 text-right tabular-nums font-medium ${tone(d.pnl)}`}>{signedEur(d.pnl)}</td>
                <td className={`px-2 py-1.5 text-right tabular-nums ${tone(d.pct)}`}>{pctTxt(d.pct)}</td>
                <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{fmtEur(d.base)}</td>
                <td className="px-2 py-1.5 text-slate-600">
                  {d.contributors.map(c => (
                    <span key={c.ticker} className="mr-2 whitespace-nowrap">
                      <Tooltip text={c.name}><span className="font-mono">{c.ticker}</span></Tooltip>{' '}
                      <span className={tone(c.pnl)}>{signedEur(c.pnl)}</span>
                    </span>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export default function DayExtremesTab() {
  const [accountId, setAccountId] = usePersist<number | 0>('dx_account', 0)      // 0 = all investment accounts
  const [yearSel, setYearSel] = usePersist<number | 0>('dx_year', 0)             // 0 = latest year with activity
  const [n, setN] = usePersist('dx_n', 10)
  const [period, setPeriod] = usePersist<Period>('dx_period', 'day')
  const [showInactive, setShowInactive] = usePersist('dx_inactive', false)

  const { data: accountsRaw = [] } = useQuery({ queryKey: ['allAccountsForPreset'], queryFn: () => getAccounts() })
  const accounts = (accountsRaw as Record<string, unknown>[])
    .filter(a => INV_TYPES.includes(String(a.type)))
    .map(a => ({ id: Number(a.id), name: String(a.name ?? ''), closed: a.is_active === false || a.is_active === 0 || a.is_active === 'false' }))
    // Inactive accounts are hidden unless asked for — except the one currently selected, so the
    // list never drops the account whose report is on screen.
    .filter(a => showInactive || !a.closed || a.id === accountId)
    .sort((a, b) => a.name.localeCompare(b.name))
  const { data: years = [] } = useQuery({ queryKey: ['dx-years', accountId], queryFn: () => getDayExtremeYears(accountId || undefined) })
  const yearList = years as number[]
  const year = yearList.includes(yearSel) ? yearSel : (yearList[0] ?? new Date().getFullYear())
  useEffect(() => { if (yearList.length && !yearList.includes(yearSel)) setYearSel(yearList[0]) }, [yearList.join(',')]) // eslint-disable-line react-hooks/exhaustive-deps

  const count = Math.min(100, Math.max(1, Math.round(Number(n) || 10)))
  const { data, isLoading, error } = useQuery({
    queryKey: ['dx', accountId, year, count, period],
    queryFn: () => getDayExtremes({ accountId: accountId || undefined, year, n: count, period }),
    enabled: yearList.length > 0,
  })
  const res = data as Result | undefined
  const u = UNIT[period]
  const sel = 'rounded border border-slate-300 px-2 py-1 text-sm'

  return (
    <div className="space-y-4">
      <p className="text-xs text-slate-500">
        The best and worst {u.many} of a calendar year for one investment account — by profit/loss in € and in %, each with the positions that moved it most.
      </p>
      <div className="flex flex-wrap items-end gap-4">
        <label className="text-xs text-slate-500">Account
          <select className={`${sel} block min-w-64`} value={accountId} onChange={e => setAccountId(Number(e.target.value))}>
            <option value={0}>All investment accounts</option>
            {accounts.map(a => <option key={a.id} value={a.id}>{a.name}{a.closed ? ' (inactive)' : ''}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-1.5 text-xs text-slate-500 cursor-pointer select-none pb-1.5">
          <input type="checkbox" checked={showInactive} onChange={e => setShowInactive(e.target.checked)} className="rounded" />
          Show inactive
        </label>
        <label className="text-xs text-slate-500">Year
          <select className={`${sel} block`} value={year} onChange={e => setYearSel(Number(e.target.value))}>
            {yearList.map(y => <option key={y} value={y}>{y}</option>)}
          </select>
        </label>
        <div className="text-xs text-slate-500">
          <p>Period</p>
          <div className="mt-0.5 inline-flex rounded border border-slate-300 overflow-hidden">
            {(['day', 'week', 'month'] as Period[]).map(p => (
              <button key={p} type="button" onClick={() => setPeriod(p)}
                className={`px-3 py-1 text-sm ${period === p ? 'bg-slate-700 text-white' : 'bg-white text-slate-600 hover:bg-slate-50'}`}>
                {UNIT[p].cap}s
              </button>
            ))}
          </div>
        </div>
        <label className="text-xs text-slate-500"><Tooltip text={`How many ${u.many} to list in each of the four tables (1–100).`}>{u.cap}s per table</Tooltip>
          <input type="number" min={1} max={100} className={`${sel} block w-20 text-right`} value={n}
            onChange={e => setN(Number(e.target.value))} />
        </label>
      </div>

      {error && <p className="text-xs text-red-600 bg-red-50 rounded px-3 py-2">{String((error as { response?: { data?: { detail?: string } }; message?: string })?.response?.data?.detail ?? (error as Error).message)}</p>}
      {isLoading && <div className="flex justify-center py-10"><Spinner /></div>}

      {res && res.periods === 0 && <p className="text-sm text-slate-500">No {u.many} with enough value to measure in {year} for this selection.</p>}
      {res && res.periods > 0 && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {[
              [`${u.cap}s measured`, String(res.periods), '',
                `The ${u.many} of ${res.year} that were measured and ranked in the tables. Not counted: ${u.many} where the account held under €100 (nothing to measure a % against), and ${u.many} left out for a price mismatch (listed below the tables).`],
              [`Up / down ${u.many}`, `${res.up_periods} / ${res.down_periods}`, '',
                `How many of the measured ${u.many} ended with a gain and how many with a loss in price P&L.`],
              [`Price P&L ${res.year}`, signedEur(res.total_pnl), tone(res.total_pnl),
                `The total price profit/loss of the account over the measured ${u.many} of ${res.year}: the sum of the P&L column across them (equivalently, the change in market value after removing the money put in and taken out). It covers only price moves of the holdings — dividends, interest and fees are not in it, and neither are any ${u.many} left out. So it is not the account's full return for the year, and it can differ slightly between Days, Weeks and Months when some ${u.many} are left out.`],
              ['Account', res.account, '', 'The account the figures are for; “All investment accounts” combines every Brokerage, Margin and Other Investment account.'],
            ].map(([label, value, color, tip]) => (
              <div key={label} className="bg-slate-50 rounded-lg p-3">
                <p className="text-xs text-slate-500 mb-1"><Tooltip text={tip}>{label}</Tooltip></p>
                <p className={`text-lg font-bold truncate ${color}`} title={value}>{value}</p>
              </div>
            ))}
          </div>

          <div className="grid grid-cols-1 xl:grid-cols-2 gap-5">
            <DayTable title={`Worst ${count} ${u.many} by loss (€)`} tip={`Largest falls in market value in euros, after taking out money put in or taken out during the ${u.one}.`} rows={res.worst_eur} period={period} />
            <DayTable title={`Worst ${count} ${u.many} by loss (%)`} tip="Largest percentage falls. Differs from the € list when the account was bigger or smaller at other times of the year." rows={res.worst_pct} period={period} />
            <DayTable title={`Best ${count} ${u.many} by gain (€)`} tip="Largest rises in market value in euros." rows={res.best_eur} period={period} />
            <DayTable title={`Best ${count} ${u.many} by gain (%)`} tip="Largest percentage rises." rows={res.best_pct} period={period} />
          </div>

          {res.excluded.length > 0 && (
            <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-2">
              {res.excluded.length} {res.excluded.length === 1 ? u.one : u.many} left out because {period === 'day' ? 'a trade was' : 'a day in it had a trade'} at a price far from the stored close (the price history likely carries an adjustment the ledger doesn't):{' '}
              {res.excluded.slice(0, 8).map(e => `${e.label} (${e.securities.join(', ')})`).join('; ')}{res.excluded.length > 8 ? '…' : ''}
            </p>
          )}
          <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
            <p className="text-xs font-semibold text-slate-600 mb-1">How it's measured</p>
            <ul className="list-disc pl-4 space-y-1 text-xs text-slate-500">{res.notes.map((t, i) => <li key={i}>{t}</li>)}</ul>
          </div>
        </>
      )}
    </div>
  )
}
