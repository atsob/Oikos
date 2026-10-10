// Market Watch — the four indicators worth following: hyperscaler free cash flow, credit spreads, the 10-year
// Treasury yield and market breadth. Computed by api/routers/market_watch.py; shown as a tab under Market Data and
// as a compact panel on the Dashboard.
import type React from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query'
import PlotlyReact from 'react-plotly.js'
import { RefreshCw } from 'lucide-react'
import { getMarketWatch, getMarketWatchSummary } from '@/lib/api'
import { Card, CardBody, Spinner, Button, Tooltip } from '@/components/ui'
import { plotLayout, fmtNum } from '@/lib/utils'
import { useTheme } from '@/lib/theme'

// react-plotly.js is CommonJS: depending on the bundler the component is the default export or its .default
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const Plot: React.ComponentType<any> = (PlotlyReact as any).default ?? PlotlyReact

type Level = 'good' | 'warn' | 'bad' | 'na'
type Trend = 'improving' | 'worsening' | 'flat' | undefined
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Ind = Record<string, any> & { status: Level; trend?: Trend; note?: string; series?: Record<string, { x: string[]; y: number[] }> }

const CHIP: Record<Level, { label: string; cls: string; dot: string }> = {
  good: { label: 'Healthy', cls: 'bg-green-50 text-green-700 border-green-200', dot: 'bg-green-500' },
  warn: { label: 'Watch', cls: 'bg-amber-50 text-amber-700 border-amber-200', dot: 'bg-amber-500' },
  bad: { label: 'Warning', cls: 'bg-red-50 text-red-700 border-red-200', dot: 'bg-red-500' },
  na: { label: 'No data', cls: 'bg-slate-50 text-slate-500 border-slate-200', dot: 'bg-slate-300' },
}
const TREND = { improving: { g: '▲', cls: 'text-green-700', t: 'improving' }, worsening: { g: '▼', cls: 'text-red-600', t: 'worsening' }, flat: { g: '▬', cls: 'text-slate-400', t: 'flat' } }

function Chip({ level, trend }: { level: Level; trend?: Trend }) {
  const c = CHIP[level]
  const t = trend ? TREND[trend] : null
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border text-xs font-medium ${c.cls}`}>
      <span className={`w-2 h-2 rounded-full ${c.dot}`} />{c.label}
      {t && <span className={t.cls} title={t.t}>{t.g}</span>}
    </span>
  )
}

function Stat({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: string }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-slate-400">{label}</div>
      <div className={`text-lg font-semibold tabular-nums ${tone ?? ''}`}>{value}</div>
      {sub && <div className="text-xs text-slate-400">{sub}</div>}
    </div>
  )
}

const signed = (v: number | null | undefined, d = 2, unit = '') => v == null ? '—' : `${v >= 0 ? '+' : '−'}${fmtNum(Math.abs(v), d)}${unit}`
const tone = (v: number | null | undefined, goodWhenDown = false) => v == null || v === 0 ? '' : ((v > 0) === goodWhenDown ? 'text-red-600' : 'text-green-700')

function Lines({ series, height = 220, lines }: { series: Record<string, { x: string[]; y: number[] }>; height?: number; lines?: { y: number; label: string }[] }) {
  const { isDark } = useTheme()
  const palette = ['#3b82f6', '#f59e0b', '#8b5cf6', '#10b981']
  return (
    <Plot
      data={Object.entries(series).map(([name, s], i) => ({ x: s.x, y: s.y, type: 'scatter' as const, mode: 'lines' as const, name, line: { width: 1.6, color: palette[i % palette.length] } }))}
      layout={{ height, margin: { t: 10, r: 15, b: 30, l: 45 }, legend: { orientation: 'h' as const, y: -0.2 }, showlegend: Object.keys(series).length > 1,
        shapes: (lines ?? []).map(l => ({ type: 'line' as const, xref: 'paper' as const, x0: 0, x1: 1, y0: l.y, y1: l.y, line: { color: '#ef4444', width: 1, dash: 'dot' as const } })),
        annotations: (lines ?? []).map(l => ({ xref: 'paper' as const, x: 1, y: l.y, text: l.label, showarrow: false, xanchor: 'right' as const, yanchor: 'bottom' as const, font: { size: 10, color: '#ef4444' } })),
        ...plotLayout(isDark) }}
      config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }} />
  )
}

function Shell({ title, why, good, warn, ind, children }: { title: string; why: string; good: string; warn: string; ind: Ind; children: React.ReactNode }) {
  return (
    <Card>
      <CardBody className="space-y-3">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="font-semibold">{title}</div>
            <div className="text-xs text-slate-500">{why}</div>
          </div>
          <Chip level={ind.status} trend={ind.trend} />
        </div>
        {ind.status === 'na' ? <p className="text-sm text-slate-400">{ind.note ?? 'No data.'}</p> : children}
        <div className="text-xs text-slate-500 border-t border-slate-100 pt-2 grid grid-cols-2 gap-2">
          <div><span className="text-green-700 font-medium">Good sign:</span> {good}</div>
          <div><span className="text-red-600 font-medium">Warning:</span> {warn}</div>
        </div>
      </CardBody>
    </Card>
  )
}

function FcfCard({ d }: { d: Ind }) {
  const { isDark } = useTheme()
  const cos = (d.companies ?? []) as { ticker: string; name: string; ttm: number | null; ttm_capex: number | null; last_q: number; last_q_yoy: number | null; q: { fcf: number }[]; last_quarter: string }[]
  return (
    <Shell title="Free cash flow of the hyperscalers" why="Shows whether the AI capex pays back" good="rising trend" warn="persistently negative FCF" ind={d}>
      <div className="grid grid-cols-4 gap-3">
        <Stat label="Last quarter" value={`$${fmtNum(d.last_q_bn, 1)}bn`} tone={d.last_q_bn < 0 ? 'text-red-600' : ''} sub="all five, summed" />
        <Stat label="vs a year ago" value={d.last_q_yoy_pct == null ? '—' : signed(d.last_q_yoy_pct, 0, '%')} tone={tone(d.last_q_yoy_pct)} sub="same quarter" />
        <Stat label="Trailing 4 quarters" value={`$${fmtNum(d.ttm_bn, 0)}bn`} />
        <Stat label="…capex" value={`$${fmtNum(d.capex_ttm_bn, 0)}bn`} sub={d.ttm_bn ? `${fmtNum(d.capex_ttm_bn / Math.max(d.ttm_bn + d.capex_ttm_bn, 1) * 100, 0)}% of operating cash flow` : undefined} />
      </div>
      <Plot
        data={[
          { type: 'bar', name: 'Same quarter a year ago', x: cos.filter(c => c.q.length > 4).map(c => c.ticker), y: cos.filter(c => c.q.length > 4).map(c => c.q[4].fcf), marker: { color: '#94a3b8' } },
          { type: 'bar', name: 'Latest quarter', x: cos.map(c => c.ticker), y: cos.map(c => c.last_q), marker: { color: '#3b82f6' } },
        ]}
        layout={{ height: 220, barmode: 'group', margin: { t: 10, r: 15, b: 30, l: 45 }, yaxis: { title: { text: '$bn' } }, legend: { orientation: 'h', y: -0.2 }, ...plotLayout(isDark) }}
        config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }} />
      <table className="w-full text-xs">
        <thead><tr className="text-slate-400 text-right border-b border-slate-100"><th className="text-left font-normal py-1">Company</th><th className="font-normal">Last quarter</th><th className="font-normal">vs year ago</th><th className="font-normal">Trailing 4Q FCF</th><th className="font-normal">Trailing 4Q capex</th></tr></thead>
        <tbody>
          {cos.map(c => (
            <tr key={c.ticker} className="text-right border-b border-slate-50">
              <td className="text-left py-1">{c.name} <span className="text-slate-400">{c.ticker}</span></td>
              <td className={`tabular-nums ${c.last_q < 0 ? 'text-red-600' : ''}`}>{fmtNum(c.last_q, 1)}</td>
              <td className={`tabular-nums ${tone(c.last_q_yoy)}`}>{signed(c.last_q_yoy, 1)}</td>
              <td className={`tabular-nums ${c.ttm != null && c.ttm < 0 ? 'text-red-600' : ''}`}>{c.ttm == null ? '—' : fmtNum(c.ttm, 1)}</td>
              <td className="tabular-nums">{c.ttm_capex == null ? '—' : fmtNum(c.ttm_capex, 1)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-[11px] text-slate-400">$bn, from Yahoo Finance quarterly cash-flow statements. Companies' fiscal quarters end a few weeks apart.</p>
    </Shell>
  )
}

function SpreadsCard({ d }: { d: Ind }) {
  const oas = d.kind === 'oas'
  return (
    <Shell title="Bond spreads / CDS" why="The cost of funding — credit stress shows up here first" good="stable spreads" warn="a sharp widening" ind={d}>
      {oas ? (
        <div className="grid grid-cols-4 gap-3">
          <Stat label="High yield OAS" value={`${fmtNum(d.hy, 2)}%`} /><Stat label="20-day change" value={signed(d.hy_chg_20d, 2, ' pp')} tone={tone(d.hy_chg_20d, true)} />
          <Stat label="Investment grade OAS" value={`${fmtNum(d.ig, 2)}%`} /><Stat label="20-day change" value={signed(d.ig_chg_20d, 2, ' pp')} tone={tone(d.ig_chg_20d, true)} />
        </div>
      ) : (
        <div className="grid grid-cols-3 gap-3">
          <Stat label="High yield vs Treasuries, 20 days" value={signed(d.hy_ratio_chg_20d_pct, 2, '%')} tone={tone(d.hy_ratio_chg_20d_pct)} sub="HYG ÷ IEF price" />
          <Stat label="Inv. grade vs Treasuries, 20 days" value={signed(d.ig_ratio_chg_20d_pct, 2, '%')} tone={tone(d.ig_ratio_chg_20d_pct)} sub="LQD ÷ IEF price" />
          <Stat label="High-yield ratio vs 200-day average" value={d.below_200d ? 'below' : 'above'} tone={d.below_200d ? 'text-red-600' : 'text-green-700'} />
        </div>
      )}
      {d.series && <Lines series={d.series} />}
      <p className="text-[11px] text-slate-400">{d.source}{oas ? '' : ' A falling line means widening spreads.'}</p>
    </Shell>
  )
}

function TenYearCard({ d }: { d: Ind }) {
  return (
    <Shell title="10-year Treasury" why="Sets the cost of capital" good="below 5% and stable" warn="above 5.5% and rising" ind={d}>
      <div className="grid grid-cols-4 gap-3">
        <Stat label="Yield" value={`${fmtNum(d.yield_pct, 2)}%`} tone={d.yield_pct > 5.5 ? 'text-red-600' : d.yield_pct >= 5 ? 'text-amber-600' : ''} />
        <Stat label="1 month" value={signed(d.chg_1m, 2, ' pp')} tone={tone(d.chg_1m, true)} />
        <Stat label="3 months" value={signed(d.chg_3m, 2, ' pp')} tone={tone(d.chg_3m, true)} />
        <Stat label="1-year high" value={`${fmtNum(d.high_1y, 2)}%`} />
      </div>
      {d.series && <Lines series={d.series} lines={[{ y: 5, label: '5%' }, { y: 5.5, label: '5.5%' }]} />}
    </Shell>
  )
}

function BreadthCard({ d }: { d: Ind }) {
  const has = d.pct_above_200d != null
  return (
    <Shell title="Market breadth" why="How wide the rally is" good="more stocks taking part" warn="a few stocks holding up the index" ind={d}>
      <div className="grid grid-cols-4 gap-3">
        {has && <Stat label="S&P 500 above 200-day" value={`${fmtNum(d.pct_above_200d, 1)}%`} tone={d.pct_above_200d < 40 ? 'text-red-600' : d.pct_above_200d < 50 ? 'text-amber-600' : ''} sub={`${signed(d.pct_above_200d_chg_20d, 1, ' pts')} in 20 days`} />}
        {has && <Stat label="…above 50-day" value={`${fmtNum(d.pct_above_50d, 1)}%`} />}
        <Stat label="Equal vs cap weight, 3M" value={signed(d.rsp_vs_spy_3m_pct, 1, '%')} tone={tone(d.rsp_vs_spy_3m_pct)} sub="RSP vs SPY" />
        <Stat label="S&P 500 vs its high" value={d.spx_below_high_pct < 0.5 ? 'at the high' : `−${fmtNum(d.spx_below_high_pct, 1)}%`} />
      </div>
      {d.series && <Lines series={d.series} lines={has ? [{ y: 40, label: '40%' }, { y: 50, label: '50%' }] : undefined} />}
      {d.note && <p className="text-[11px] text-amber-600">{d.note}</p>}
    </Shell>
  )
}

export function MarketWatchTab() {
  const qc = useQueryClient()
  const { data, isLoading, error } = useQuery({ queryKey: ['market-watch'], queryFn: () => getMarketWatch(false), staleTime: 30 * 60 * 1000 })
  const refresh = useMutation({
    mutationFn: () => getMarketWatch(true),
    onSuccess: d => { qc.setQueryData(['market-watch'], d); qc.invalidateQueries({ queryKey: ['market-watch-summary'] }) },
  })
  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  if (error || !data) return <p className="p-4 text-sm text-red-600">Could not load the Market Watch.</p>
  const o = data.overall
  const banner = { good: 'bg-green-50 border-green-200 text-green-800', warn: 'bg-amber-50 border-amber-200 text-amber-800', bad: 'bg-red-50 border-red-200 text-red-800', na: 'bg-slate-50 border-slate-200 text-slate-600' }[o.level as Level]
  return (
    <div className="p-4 space-y-4">
      <div className={`flex items-center justify-between gap-3 border rounded-lg px-4 py-3 ${banner}`}>
        <div>
          <div className="font-semibold">{o.verdict}</div>
          <div className="text-xs opacity-80">{o.improving} improving · {o.worsening} worsening · {o.warn} on watch · {o.bad} warning — if all four improve the market has reason to continue; if they worsen together the risk rises even while the S&amp;P 500 makes new highs.</div>
        </div>
        <Tooltip text={`Computed ${new Date(data.generated_at).toLocaleString()}; the scheduler refreshes it every few hours.`}>
          <Button size="sm" onClick={() => refresh.mutate()} disabled={refresh.isPending}>
            {refresh.isPending ? <Spinner size={12} /> : <RefreshCw size={12} />} Refresh
          </Button>
        </Tooltip>
      </div>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <FcfCard d={data.fcf} />
        <SpreadsCard d={data.spreads} />
        <TenYearCard d={data.ten_year} />
        <BreadthCard d={data.breadth} />
      </div>
    </div>
  )
}

// Dashboard: the four statuses at a glance, linking to the full tab.
export function MarketWatchPanel() {
  const navigate = useNavigate()
  const { data } = useQuery({ queryKey: ['market-watch-summary'], queryFn: getMarketWatchSummary, retry: false, staleTime: 30 * 60 * 1000 })
  if (!data) return null
  const items: [string, string][] = [['fcf', 'Hyperscaler FCF'], ['spreads', 'Credit spreads'], ['ten_year', '10-year yield'], ['breadth', 'Breadth']]
  return (
    <Card>
      <CardBody className="flex flex-wrap items-center justify-between gap-3 py-3">
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
          <span className="text-sm text-slate-500">Market Watch</span>
          {items.map(([k, label]) => (
            <span key={k} className="flex items-center gap-1.5 text-xs">
              <span className="text-slate-500">{label}</span>
              <Chip level={(data.indicators[k]?.status ?? 'na') as Level} trend={data.indicators[k]?.trend as Trend} />
            </span>
          ))}
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-slate-400 hidden lg:inline">{data.overall.verdict}</span>
          <button className="text-xs text-blue-600 hover:underline shrink-0" onClick={() => navigate(`/market-data?tab=${encodeURIComponent('Market Watch')}`)}>Details →</button>
        </div>
      </CardBody>
    </Card>
  )
}
