// Local Market Watch — the health check of the market where the user lives (the country is a setting: Tools → App Settings →
// Country of usage): benchmark index trend, breadth of its large caps, the systemic banks and the 10-year spread to Germany, plus
// context (liquidity, how much of YOUR portfolio is in the country). api/routers/local_watch.py.
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query'
import { RefreshCw } from 'lucide-react'
import { getLocalWatch, getLocalWatchSummary } from '@/lib/api'
import { Card, CardBody, Spinner, Button, Tooltip } from '@/components/ui'
import { fmtNum, fmtEur } from '@/lib/utils'
import { Chip, Stat, Lines, Shell, Playbook, RecentChanges, signed, tone } from '@/components/MarketWatch'
import type { Ind, Level, Trend } from '@/components/MarketWatch'

type Profile = { code: string; name: string; index_name: string; bank_note: string; banks: string[] }

function TrendCard({ d, p }: { d: Ind; p: Profile }) {
  return (
    <Shell title={`${p.index_name} trend`} why="The benchmark index against its 52-week high and its 200-day average" good="near the high, above the 200-day average"
      warn="15% or more below the 52-week high and under the 200-day average" ind={d}>
      <div className="grid grid-cols-4 gap-3">
        <Stat label={p.index_name} value={fmtNum(d.level, 0)} sub={`${signed(d.ret_3m_pct, 1, '%')} in 3 months`} tone={tone(d.ret_3m_pct)} />
        <Stat label="Below its high" value={`−${fmtNum(d.below_high_pct, 1)}%`} tone={d.below_high_pct >= 15 ? 'text-red-600' : d.below_high_pct >= 8 ? 'text-amber-600' : ''} />
        <Stat label="vs 200-day average" value={signed(d.vs_200d_pct, 1, '%')} tone={d.vs_200d_pct < 0 ? 'text-red-600' : 'text-green-700'} />
        <Stat label="vs Europe, 3 months" value={signed(d.vs_europe_3m_pct, 1, ' pts')} tone={tone(d.vs_europe_3m_pct)} sub="against the STOXX 600" />
      </div>
      {d.series && <Lines series={d.series} />}
    </Shell>
  )
}

function BreadthCard({ d, p }: { d: Ind; p: Profile }) {
  return (
    <Shell title={`${p.name} market breadth`} why={`The share of ${d.stocks ?? 20} large-cap stocks above their moving averages`} good="most stocks above the 200-day average"
      warn="the index within 5% of its high while under 40% are above the 200-day — or under 25% above the 50-day with the 200-day under 50%" ind={d}>
      <div className="grid grid-cols-3 gap-3">
        <Stat label="Above 200-day" value={`${fmtNum(d.pct_above_200d, 0)}%`} tone={d.pct_above_200d < 40 ? 'text-red-600' : d.pct_above_200d < 50 ? 'text-amber-600' : ''} sub={`${signed(d.pct_above_200d_chg_20d, 0, ' pts')} in 20 days`} />
        <Stat label="Above 50-day" value={`${fmtNum(d.pct_above_50d, 0)}%`} tone={d.pct_above_50d < 25 ? 'text-red-600' : d.pct_above_50d < 30 ? 'text-amber-600' : ''} />
        <Stat label="Index near its high" value={d.index_near_high ? 'yes' : 'no'} sub="within 5%" />
      </div>
      {d.series && <Lines series={d.series} lines={[{ y: 40, label: '40%' }, { y: 50, label: '50%' }]} />}
    </Shell>
  )
}

function BanksCard({ d, p }: { d: Ind; p: Profile }) {
  const banks = (d.banks ?? []) as { name: string; ticker: string; ret_3m_pct: number; below_high_pct: number }[]
  return (
    <Shell title={`${p.name} banks`} why={`${p.banks.join(', ')}${p.bank_note ? ` — ${p.bank_note}` : ''}`} good="above the 200-day average, near the high"
      warn="20% or more below the 52-week high and under the 200-day average" ind={d}>
      <div className="grid grid-cols-4 gap-3">
        <Stat label="Below their high" value={`−${fmtNum(d.below_high_pct, 1)}%`} tone={d.below_high_pct >= 20 ? 'text-red-600' : d.below_high_pct >= 10 ? 'text-amber-600' : ''} />
        <Stat label="1 month" value={signed(d.ret_1m_pct, 1, '%')} tone={tone(d.ret_1m_pct)} />
        <Stat label="3 months" value={signed(d.ret_3m_pct, 1, '%')} tone={tone(d.ret_3m_pct)} />
        <Stat label="vs the market, 3M" value={signed(d.vs_market_3m_pct, 1, ' pts')} tone={tone(d.vs_market_3m_pct)} />
      </div>
      {d.series && <Lines series={d.series} />}
      <table className="w-full text-xs">
        <thead><tr className="text-slate-400 text-right border-b border-slate-100"><th className="text-left font-normal py-1">Bank</th><th className="font-normal">3 months</th><th className="font-normal">Below its high</th></tr></thead>
        <tbody>{banks.map(b => (
          <tr key={b.ticker} className="text-right border-b border-slate-50">
            <td className="text-left py-1">{b.name} <span className="text-slate-400">{b.ticker}</span></td>
            <td className={`tabular-nums ${tone(b.ret_3m_pct)}`}>{signed(b.ret_3m_pct, 1, '%')}</td>
            <td className="tabular-nums">−{fmtNum(b.below_high_pct, 1)}%</td>
          </tr>))}
        </tbody>
      </table>
    </Shell>
  )
}

function SovereignCard({ d, p }: { d: Ind; p: Profile }) {
  return (
    <Shell title={`${p.name} sovereign spread`} why={`${p.name} minus German 10-year yield — monthly, about a 6-week lag, so a slow, lagging leg`} good="below 1.0 pp"
      warn="1.8 pp or more, or up 0.5 pp in 3 months" ind={d}>
      <div className="grid grid-cols-4 gap-3">
        <Stat label="Spread to Germany" value={`${fmtNum(d.spread_pp, 2)} pp`} tone={d.spread_pp >= 1.8 ? 'text-red-600' : d.spread_pp >= 1 ? 'text-amber-600' : ''} sub={`as of ${d.as_of}`} />
        <Stat label="3 months" value={signed(d.chg_3m, 2, ' pp')} tone={tone(d.chg_3m, true)} />
        <Stat label={`${p.name} 10-year`} value={`${fmtNum(d.home_yield, 2)}%`} />
        <Stat label={`vs ${d.peer}`} value={signed(d.vs_peer_pp, 2, ' pp')} sub="negative = yields less" />
      </div>
      {d.series && <Lines series={d.series} lines={[{ y: 1, label: '1.0' }, { y: 1.8, label: '1.8' }]} height={200} />}
    </Shell>
  )
}

function LiquidityCard({ d }: { d: Ind }) {
  return (
    <Shell title="Liquidity" why="The index's traded volume — a market that dries up is hard to leave. Context, not part of the banner" good="20-day volume at least 80% of the 120-day"
      warn="under 60%" ind={d}>
      <div className="grid grid-cols-2 gap-3">
        <Stat label="20 days vs 120 days" value={`${fmtNum((d.ratio_20_120 ?? 0) * 100, 0)}%`} tone={d.ratio_20_120 < 0.6 ? 'text-red-600' : d.ratio_20_120 < 0.8 ? 'text-amber-600' : ''} />
        <Stat label="Average volume, 20 days" value={`${fmtNum(d.avg_20d_m, 1)}m shares`} />
      </div>
      {d.series && <Lines series={d.series} height={160} />}
    </Shell>
  )
}

function ExposureCard({ d, p }: { d: Ind; p: Profile }) {
  const cats = (d.categories ?? []) as { category: string; value_eur: number; pct: number }[]
  return (
    <Card>
      <CardBody className="space-y-3">
        <div>
          <div className="font-semibold">Your {p.name} exposure</div>
          <div className="text-xs text-slate-500">From Reports → Inv. Portfolio → Country Exposure: stocks, bonds, cash and the {p.name} share of your funds. Context for the playbook, not an indicator.</div>
        </div>
        {d.value_eur == null ? <p className="text-sm text-slate-400">{d.note ?? 'No data.'}</p> : (
          <>
            <div className="grid grid-cols-2 gap-3">
              <Stat label={`${p.name} assets`} value={fmtEur(d.value_eur)} />
              <Stat label="Share of the portfolio" value={`${fmtNum(d.pct, 1)}%`} sub={`of ${fmtEur(d.total_eur)}`} tone={d.pct > 50 ? 'text-amber-600' : ''} />
            </div>
            <table className="w-full text-xs">
              <thead><tr className="text-slate-400 text-right border-b border-slate-100"><th className="text-left font-normal py-1">Category</th><th className="font-normal">Value</th><th className="font-normal">% of portfolio</th></tr></thead>
              <tbody>{cats.map(c => (
                <tr key={c.category} className="text-right border-b border-slate-50"><td className="text-left py-1">{c.category}</td><td className="tabular-nums">{fmtEur(c.value_eur)}</td><td className="tabular-nums">{fmtNum(c.pct, 1)}%</td></tr>))}
              </tbody>
            </table>
          </>
        )}
      </CardBody>
    </Card>
  )
}

// Nothing to show until a country with a profile is chosen
function NoCountry({ d }: { d: { country: string | null; unsupported?: boolean; supported: { code: string; name: string }[] } }) {
  const navigate = useNavigate()
  return (
    <div className="p-6 space-y-3 text-sm text-slate-600">
      {d.country == null
        ? <p><b>Local Market Watch</b> follows the market where you live. Choose your <b>Country of usage</b> in Tools → App Settings.</p>
        : <p>There is no built-in profile for <b>{d.country}</b> yet.</p>}
      <p className="text-xs text-slate-500">Available now: {d.supported.map(c => c.name).join(', ')}. A country is a few lines in <code>database/local_markets.py</code> — its benchmark index, systemic banks and large caps (Yahoo Finance symbols).</p>
      <Button size="sm" onClick={() => navigate('/tools')}>Open Tools → App Settings</Button>
    </div>
  )
}

export function LocalWatchTab() {
  const qc = useQueryClient()
  const { data, isLoading, error } = useQuery({ queryKey: ['local-watch'], queryFn: () => getLocalWatch(false), staleTime: 30 * 60 * 1000 })
  const refresh = useMutation({
    mutationFn: () => getLocalWatch(true),
    onSuccess: d => { qc.setQueryData(['local-watch'], d); qc.invalidateQueries({ queryKey: ['local-watch-summary'] }); qc.invalidateQueries({ queryKey: ['market-watch-events', 'local'] }) },
  })
  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  if (error || !data) return <p className="p-4 text-sm text-red-600">Could not load Local Market Watch.</p>
  if (!data.overall) return <NoCountry d={data} />
  const p = data.profile as Profile
  const o = data.overall
  const banner = { good: 'bg-green-50 border-green-200 text-green-800', warn: 'bg-amber-50 border-amber-200 text-amber-800', bad: 'bg-red-50 border-red-200 text-red-800', na: 'bg-slate-50 border-slate-200 text-slate-600' }[o.level as Level]
  return (
    <div className="p-4 space-y-4">
      <div className={`flex items-center justify-between gap-3 border rounded-lg px-4 py-3 ${banner}`}>
        <div>
          <div className="font-semibold">{o.verdict}</div>
          <div className="text-xs opacity-80">Local market: {p.name} ({p.index_name}). {o.improving} improving · {o.worsening} worsening · {o.warn} on watch · {o.bad} in warning. ▼ means moving toward the red line, ▲ away from it. Red when two or more indicators are in Warning (a daily indicator must stay red for 3 days in a row and clears only once well back past its line); amber when all are worsening together. Thresholds are starting values — rule version <code>{data.rule_version}</code>. Change the country in Tools → App Settings.</div>
        </div>
        <Tooltip text={`Computed ${new Date(data.generated_at).toLocaleString()}; the scheduler refreshes it every few hours.`}>
          <Button size="sm" onClick={() => refresh.mutate()} disabled={refresh.isPending}>
            {refresh.isPending ? <Spinner size={12} /> : <RefreshCw size={12} />} Refresh
          </Button>
        </Tooltip>
      </div>
      <Playbook level={o.level as Level} scope="local" />
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <TrendCard d={data.trend} p={p} />
        <BreadthCard d={data.breadth} p={p} />
        <BanksCard d={data.banks} p={p} />
        <SovereignCard d={data.sovereign} p={p} />
      </div>
      <div className="text-xs text-slate-500 pt-1">Context — these do not count toward the banner</div>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <LiquidityCard d={data.liquidity ?? { status: 'na' }} />
        <ExposureCard d={data.exposure ?? { status: 'na' }} p={p} />
      </div>
      <RecentChanges scope="local" />
    </div>
  )
}

export function LocalWatchPanel() {
  const navigate = useNavigate()
  const { data } = useQuery({ queryKey: ['local-watch-summary'], queryFn: getLocalWatchSummary, retry: false, staleTime: 30 * 60 * 1000 })
  if (!data || !data.overall) return null                      // no country chosen (or none with a profile): stay out of the way
  const items: [string, string][] = [['trend', data.profile.index_name], ['breadth', 'Breadth'], ['banks', 'Banks'], ['sovereign', 'Sovereign spread']]
  return (
    <Card>
      <CardBody className="flex flex-wrap items-center justify-between gap-3 py-3">
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
          <span className="text-sm text-slate-500">Local Market Watch · {data.profile.name}</span>
          {items.map(([k, label]) => (
            <span key={k} className="flex items-center gap-1.5 text-xs">
              <span className="text-slate-500">{label}</span>
              <Chip level={(data.indicators[k]?.status ?? 'na') as Level} trend={data.indicators[k]?.trend as Trend} />
            </span>
          ))}
        </div>
        <div className="flex items-center gap-3">
          {data.recent_changes > 0 && <span className="text-xs text-amber-700">{data.recent_changes} change{data.recent_changes === 1 ? '' : 's'} this week</span>}
          <span className="text-xs text-slate-400 hidden lg:inline">{data.overall.verdict}</span>
          <button className="text-xs text-blue-600 hover:underline shrink-0" onClick={() => navigate(`/market-data?tab=${encodeURIComponent('Local Market Watch')}`)}>Details →</button>
        </div>
      </CardBody>
    </Card>
  )
}
