// Market Watch — the four indicators worth following: hyperscaler free cash flow, credit spreads, the 10-year
// Treasury yield and market breadth. Computed by api/routers/market_watch.py; shown as a tab under Market Data and
// as a compact panel on the Dashboard.
import type React from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query'
import Plot from '@/components/Plot'
import { RefreshCw } from 'lucide-react'
import { useState } from 'react'
import { getMarketWatch, getMarketWatchSummary, getMarketWatchEvents, getMarketWatchPlaybook, saveMarketWatchPlaybook, getLocalWatchEvents, getLocalWatchPlaybook, saveLocalWatchPlaybook, getMarketWatchBacktest, getMarketWatchBacktestHistory, runMarketWatchBacktest } from '@/lib/api'
import { Card, CardBody, Spinner, Button, Tooltip } from '@/components/ui'
import { plotLayout, fmtNum } from '@/lib/utils'
import { useTheme } from '@/lib/theme'

// react-plotly.js is CommonJS: depending on the bundler the component is the default export or its .default
// eslint-disable-next-line @typescript-eslint/no-explicit-any

export type Level = 'good' | 'warn' | 'bad' | 'na'
export type Trend = 'improving' | 'worsening' | 'flat' | undefined
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Ind = Record<string, any> & { status: Level; trend?: Trend; red_pending?: boolean; note?: string; series?: Record<string, { x: string[]; y: number[] }> }

const CHIP: Record<Level, { label: string; cls: string; dot: string }> = {
  good: { label: 'Healthy', cls: 'bg-green-50 text-green-700 border-green-200', dot: 'bg-green-500' },
  warn: { label: 'Watch', cls: 'bg-amber-50 text-amber-700 border-amber-200', dot: 'bg-amber-500' },
  bad: { label: 'Warning', cls: 'bg-red-50 text-red-700 border-red-200', dot: 'bg-red-500' },
  na: { label: 'No data', cls: 'bg-slate-50 text-slate-500 border-slate-200', dot: 'bg-slate-300' },
}
const TREND = { improving: { g: '▲', cls: 'text-green-700', t: 'improving' }, worsening: { g: '▼', cls: 'text-red-600', t: 'worsening' }, flat: { g: '▬', cls: 'text-slate-400', t: 'flat' } }

export function Chip({ level, trend }: { level: Level; trend?: Trend }) {
  const c = CHIP[level]
  const t = trend ? TREND[trend] : null
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border text-xs font-medium ${c.cls}`}>
      <span className={`w-2 h-2 rounded-full ${c.dot}`} />{c.label}
      {t && <span className={t.cls} title={trend === 'worsening' ? 'Moving toward its red line' : trend === 'improving' ? 'Moving away from its red line' : 'Not moving much'}>{t.g}</span>}
    </span>
  )
}

export function Stat({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: string }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-slate-400">{label}</div>
      <div className={`text-lg font-semibold tabular-nums ${tone ?? ''}`}>{value}</div>
      {sub && <div className="text-xs text-slate-400">{sub}</div>}
    </div>
  )
}

export const signed = (v: number | null | undefined, d = 2, unit = '') => v == null ? '—' : `${v >= 0 ? '+' : '−'}${fmtNum(Math.abs(v), d)}${unit}`
export const tone = (v: number | null | undefined, goodWhenDown = false) => v == null || v === 0 ? '' : ((v > 0) === goodWhenDown ? 'text-red-600' : 'text-green-700')

export function Lines({ series, height = 220, lines }: { series: Record<string, { x: string[]; y: number[] }>; height?: number; lines?: { y: number; label: string }[] }) {
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

export function Shell({ title, why, good, warn, ind, children }: { title: string; why: string; good: string; warn: string; ind: Ind; children: React.ReactNode }) {
  return (
    <Card>
      <CardBody className="space-y-3">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="font-semibold">{title}</div>
            <div className="text-xs text-slate-500">{why}</div>
          </div>
          <div className="flex flex-col items-end gap-1">
            <Chip level={ind.status} trend={ind.trend} />
            {ind.red_pending && <span className="text-[11px] text-red-600">red today — waiting for 3 days in a row</span>}
          </div>
        </div>
        {ind.status === 'na' ? <p className="text-sm text-slate-400">{ind.note ?? 'No data.'}</p> : children}
        <div className="text-xs text-slate-500 border-t border-slate-100 pt-2 grid grid-cols-2 gap-2">
          <div><span className="text-green-700 font-medium">Good sign:</span> {good}</div>
          <div><span className="text-red-600 font-medium">Red when:</span> {warn}</div>
        </div>
      </CardBody>
    </Card>
  )
}

function FcfCard({ d }: { d: Ind }) {
  const { isDark } = useTheme()
  const cos = (d.companies ?? []) as { ticker: string; name: string; ttm: number | null; ttm_capex: number | null; last_q: number; last_q_yoy: number | null; q: { fcf: number }[]; last_quarter: string }[]
  return (
    <Shell title="Free cash flow of the hyperscalers" why="Shows whether the AI capex pays back" good="rising trend" warn="two quarters in a row negative in total, or trailing 4 quarters below zero (amber: capex over 90% of operating cash flow)" ind={d}>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Stat label="Last quarter" value={`$${fmtNum(d.last_q_bn, 1)}bn`} tone={d.last_q_bn < 0 ? 'text-red-600' : ''} sub="all five, summed" />
        <Stat label="vs a year ago" value={d.last_q_yoy_pct == null ? '—' : signed(d.last_q_yoy_pct, 0, '%')} tone={tone(d.last_q_yoy_pct)} sub="same quarter" />
        <Stat label="Trailing 4 quarters" value={`$${fmtNum(d.ttm_bn, 0)}bn`} />
        <Stat label="…capex" value={`$${fmtNum(d.capex_ttm_bn, 0)}bn`} sub={d.capex_share_pct != null ? `${fmtNum(d.capex_share_pct, 0)}% of operating cash flow` : undefined} tone={d.capex_share_pct > 90 ? 'text-amber-600' : ''} />
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
    <Shell title="Bond spreads / CDS" why="The cost of funding — credit stress shows up here first" good="stable spreads" warn={oas ? "high yield up 0.75 pp in 20 days, or at 4.5%, or up 1.0 pp in 60 days; investment grade at 1.3%" : "high-yield ratio down 3% in 20 days"} ind={d}>
      {oas ? (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <Stat label="High yield OAS" value={`${fmtNum(d.hy, 2)}%`} sub={`${signed(d.hy_chg_60d, 2, ' pp')} in 60 days`} /><Stat label="20-day change" value={signed(d.hy_chg_20d, 2, ' pp')} tone={tone(d.hy_chg_20d, true)} />
          <Stat label="Investment grade OAS" value={`${fmtNum(d.ig, 2)}%`} /><Stat label="20-day change" value={signed(d.ig_chg_20d, 2, ' pp')} tone={tone(d.ig_chg_20d, true)} />
        </div>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
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
    <Shell title="10-year Treasury" why="Sets the cost of capital" good="below 5% and stable" warn="up 0.75 pp in about 3 months — or above 5.5% and up 0.5 pp (speed, not level)" ind={d}>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
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
    <Shell title="Market breadth" why="How wide the rally is" good="more stocks taking part" warn="the S&P 500 within 5% of its high while under 40% of stocks are above the 200-day average — or under 25% above the 50-day with the 200-day under 50%" ind={d}>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {has && <Stat label="S&P 500 above 200-day" value={`${fmtNum(d.pct_above_200d, 1)}%`} tone={d.pct_above_200d < 40 ? 'text-red-600' : d.pct_above_200d < 50 ? 'text-amber-600' : ''} sub={`${signed(d.pct_above_200d_chg_20d, 1, ' pts')} in 20 days`} />}
        {has && <Stat label="…above 50-day" value={`${fmtNum(d.pct_above_50d, 1)}%`} />}
        {d.sectors_total != null && <Stat label="Sector ETFs above 200-day" value={`${d.sectors_above_200d} of ${d.sectors_total}`} tone={d.sectors_above_200d / d.sectors_total < 0.5 ? 'text-amber-600' : ''} sub={`${d.sectors_above_50d} above 50-day`} />}
        <Stat label="Equal vs cap weight, 3M" value={signed(d.rsp_vs_spy_3m_pct, 1, '%')} tone={tone(d.rsp_vs_spy_3m_pct)} sub="RSP vs SPY" />
        <Stat label="S&P 500 vs its high" value={d.spx_below_high_pct < 0.5 ? 'at the high' : `−${fmtNum(d.spx_below_high_pct, 1)}%`} />
      </div>
      {d.series && <Lines series={d.series} lines={has ? [{ y: 40, label: '40%' }, { y: 50, label: '50%' }] : undefined} />}
      {d.note && <p className="text-[11px] text-amber-600">{d.note}</p>}
    </Shell>
  )
}

export function Playbook({ level, scope = 'market' }: { level: Level; scope?: 'market' | 'local' }) {
  const qc = useQueryClient()
  const g = scope === 'local'
  const { data } = useQuery({ queryKey: ['market-watch-playbook', scope], queryFn: g ? getLocalWatchPlaybook : getMarketWatchPlaybook })
  const [text, setText] = useState<string | null>(null)
  const save = useMutation({
    mutationFn: (t: string) => (g ? saveLocalWatchPlaybook : saveMarketWatchPlaybook)(t),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['market-watch-playbook', scope] }); setText(null) },
  })
  if (!data) return null
  const red = level === 'bad'
  const body = (
    <div className="space-y-2">
      {text === null ? (
        <>
          <pre className="whitespace-pre-wrap text-sm font-sans">{data.text}</pre>
          <button className="text-xs text-blue-600 hover:underline" onClick={() => setText(data.text)}>Edit the playbook</button>
        </>
      ) : (
        <>
          <textarea className="w-full border rounded px-3 py-2 text-sm font-sans" rows={5} value={text} onChange={e => setText(e.target.value)} />
          <div className="flex gap-2">
            <Button size="sm" onClick={() => save.mutate(text)} disabled={save.isPending}>Save</Button>
            <Button size="sm" variant="secondary" onClick={() => setText(null)}>Cancel</Button>
          </div>
        </>
      )}
    </div>
  )
  return red ? (
    <div className="border border-red-300 bg-red-50 text-red-900 rounded-lg px-4 py-3 space-y-1">
      <div className="font-semibold">Playbook — the crash rules are on</div>
      {body}
    </div>
  ) : (
    <details className="border border-slate-200 rounded-lg px-4 py-2 text-slate-600">
      <summary className="cursor-pointer text-sm font-medium">Playbook — what to do if the banner turns red</summary>
      <div className="pt-2">{body}</div>
    </details>
  )
}

export type Row = Record<string, string | number | boolean | null>
export function Grid({ rows, cols }: { rows: Row[]; cols: [string, string][] }) {
  if (!rows.length) return <p className="text-xs text-slate-400">None in the period.</p>
  return (
    <div className="overflow-x-auto max-h-72 overflow-y-auto">
      <table className="w-full text-xs">
        <thead className="sticky top-0 bg-white dark:bg-slate-900"><tr className="text-slate-400 text-right border-b border-slate-100">
          {cols.map(([h], i) => <th key={h} className={`font-normal py-1 pr-2 ${i === 0 ? 'text-left' : ''}`}>{h}</th>)}
        </tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="text-right border-b border-slate-50">
              {cols.map(([h, k], j) => {
                const v = r[k]
                const num = typeof v === 'number'
                const pct = k.endsWith('_pct')
                return <td key={h} className={`py-0.5 pr-2 tabular-nums ${j === 0 ? 'text-left' : ''} ${pct && num ? (v < 0 ? 'text-red-600' : 'text-green-700') : ''}`}>{v == null ? '—' : pct && num ? `${v > 0 ? '+' : ''}${v}%` : String(v)}</td>
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

const EP_COLS: [string, string][] = [['Episode', 'episode'], ['Index peak', 'peak'], ['Red first fired', 'first_red'], ['Days vs peak', 'days_vs_peak'], ['S&P 500 vs peak then', 'from_peak_pct'], ['Further fall to the trough', 'further_pct']]
export const SPELL_COLS: [string, string][] = [['Red from', 'from'], ['Until', 'until'], ['Days', 'days'], ['S&P 500', 'spx'], ['+3 months', 'm3_pct'], ['+6 months', 'm6_pct'], ['Worst fall in 6 months', 'worst_6m_pct']]

function BacktestCard() {
  const qc = useQueryClient()
  const { data } = useQuery({ queryKey: ['market-watch-backtest'], queryFn: getMarketWatchBacktest, staleTime: Infinity })
  const run = useMutation({
    mutationFn: runMarketWatchBacktest,
    onSuccess: d => { qc.setQueryData(['market-watch-backtest'], d); qc.invalidateQueries({ queryKey: ['market-watch-backtest-history'] }) },
  })
  const { data: history = [] } = useQuery({ queryKey: ['market-watch-backtest-history'], queryFn: getMarketWatchBacktestHistory, staleTime: 5 * 60 * 1000 })
  const err = (run.error as { response?: { data?: { detail?: string } }; message?: string } | null)
  const r = data
  return (
    <Card>
      <CardBody className="space-y-3">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="font-semibold">Back-test of the red rules</div>
            <div className="text-xs text-slate-500">How the credit-spread and 10-year rules would have behaved around 2000, 2008, 2020, 2022 and March 2026. Free cash flow and breadth have no free long history — the daily log will cover them. Needs the free FRED key. It re-runs by itself when a rule or threshold changes, on the first Sunday of the month, after each earnings season and when the log reaches 6 months — and it only reports: it never changes a threshold.</div>
          </div>
          <Button size="sm" onClick={() => run.mutate()} disabled={run.isPending}>
            {run.isPending ? <><Spinner size={12} /> Running…</> : <><RefreshCw size={12} /> {r ? 'Run again' : 'Run back-test'}</>}
          </Button>
        </div>
        {run.isError && <p className="text-sm text-red-600">{err?.response?.data?.detail ?? err?.message ?? 'The back-test failed.'}</p>}
        {r && (
          <div className="space-y-5">
            <p className="text-[11px] text-slate-400">Run {new Date(r.generated_at).toLocaleString()} · rule version <code>{r.rule_version}</code> · reason: {r.trigger} · S&amp;P 500 from {r.ranges.spx_from}, 10-year from {r.ranges.ten_year_from}, Moody's Baa spread from {r.ranges.baa_from}. FRED carries the ICE BofA spreads of the live credit rule only from {r.ranges.ice_from} (ICE's licence).</p>
            {r.material_change && <div className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded px-3 py-2"><b>Changed since the previous run:</b> {r.material_change}</div>}
            {r.log && (
              <div className="space-y-1">
                <div className="text-sm font-medium">From the daily log <span className="text-slate-400 font-normal">· {r.log.days} of {r.log.ready_days} days{r.log.ready ? ' — long enough to test the banner' : ' — about 6 months are needed to test breadth, free cash flow and the "two or more red" rule'}</span></div>
                {r.log.days > 0 && (
                  <p className="text-xs text-slate-600">
                    Banner: {r.log.banner_days.bad} red · {r.log.banner_days.warn} amber · {r.log.banner_days.good} green days ({r.log.banner_red_spells} red spells). Days with an indicator red — free cash flow {r.log.indicator_red_days.fcf}, credit {r.log.indicator_red_days.spreads}, 10-year {r.log.indicator_red_days.ten_year}, breadth {r.log.indicator_red_days.breadth}; two or more at once: {r.log.two_or_more_red_days}.
                  </p>
                )}
              </div>
            )}
            <div className="space-y-1">
              <div className="text-sm font-medium">10-year yield — up 0.75 pp in ~3 months, or above 5.5% and up 0.5 pp <span className="text-slate-400 font-normal">· red on {r.ten_year.share_red_pct}% of days (the old level rule: {r.ten_year.share_red_old_pct}%)</span></div>
              <Grid rows={r.ten_year.episodes} cols={EP_COLS} />
              <p className="text-[11px] text-slate-400">Red together with the credit surrogate on {r.ten_year.both_with_credit_days} days — the banner needs two indicators, so these two alone would never have turned it red.</p>
              <details><summary className="text-xs text-blue-600 cursor-pointer">Every red spell ({r.ten_year.spells.length})</summary><Grid rows={r.ten_year.spells} cols={SPELL_COLS} /></details>
            </div>
            <div className="space-y-1">
              <div className="text-sm font-medium">Credit — long-history surrogate (Moody's Baa − 10-year, speed only) <span className="text-slate-400 font-normal">· red on {r.credit_surrogate.share_red_pct}% of days</span></div>
              <Grid rows={r.credit_surrogate.episodes} cols={EP_COLS} />
              <details><summary className="text-xs text-blue-600 cursor-pointer">Every red spell ({r.credit_surrogate.spells.length})</summary><Grid rows={r.credit_surrogate.spells} cols={SPELL_COLS} /></details>
            </div>
            <div className="space-y-1">
              <div className="text-sm font-medium">Credit — the live rule (ICE BofA spreads), the window FRED allows</div>
              <Grid rows={r.credit_live.spells} cols={SPELL_COLS} />
            </div>
            {history.length > 0 && (
              <div className="space-y-1">
                <div className="text-sm font-medium">Past runs — compare rule versions</div>
                <Grid rows={history.map(h => ({ at: new Date(h.at).toLocaleDateString(), trigger: h.trigger, ver: h.rule_version, ty: h.summary?.share_red?.ten_year ?? null, cs: h.summary?.share_red?.credit_surrogate ?? null, cl: h.summary?.share_red?.credit_live ?? null, ld: h.summary?.log_days ?? null, ch: h.material_change ?? '' }))}
                  cols={[['Run', 'at'], ['Reason', 'trigger'], ['Rule version', 'ver'], ['10-year red days %', 'ty'], ['Credit surrogate red %', 'cs'], ['Credit (ICE) red %', 'cl'], ['Log days', 'ld'], ['Material change', 'ch']]} />
              </div>
            )}
          </div>
        )}
      </CardBody>
    </Card>
  )
}

export function RecentChanges({ scope = 'market' }: { scope?: 'market' | 'local' }) {
  const { data = [] } = useQuery({ queryKey: ['market-watch-events', scope], queryFn: scope === 'local' ? getLocalWatchEvents : getMarketWatchEvents, staleTime: 5 * 60 * 1000 })
  if (data.length === 0) return null
  return (
    <Card>
      <CardBody className="space-y-1">
        <div className="font-semibold text-sm">Recent changes</div>
        <ul className="text-xs text-slate-600 space-y-0.5">
          {data.map((e, i) => (
            <li key={i}><span className="text-slate-400 tabular-nums">{new Date(e.at).toLocaleDateString()} {new Date(e.at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })}</span> {e.message}</li>
          ))}
        </ul>
        <p className="text-[11px] text-slate-400">Only changes of status are listed — green → amber, amber → red, red → clear — not every refresh.</p>
      </CardBody>
    </Card>
  )
}

function FranceCard({ d }: { d: Ind }) {
  return (
    <Shell title="France–Germany 10-year spread" why="Context for the French-bond and counterparty rules — not part of the banner" good="below 0.9 pp" warn="1.3 pp or more, or +0.4 pp in 3 months" ind={d}>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        <Stat label="Spread" value={`${fmtNum(d.spread_pp, 2)} pp`} tone={d.spread_pp >= 1.3 ? 'text-red-600' : d.spread_pp >= 0.9 ? 'text-amber-600' : ''} sub={`monthly, as of ${d.as_of}`} />
        <Stat label="3 months" value={signed(d.chg_3m, 2, ' pp')} tone={tone(d.chg_3m, true)} />
        <Stat label="Amber from" value="0.9 pp" sub="2024–25 political stress peaked near 0.85" />
      </div>
      {d.series && <Lines series={d.series} lines={[{ y: 0.9, label: '0.9' }, { y: 1.3, label: '1.3' }]} height={180} />}
    </Shell>
  )
}

function ErpCard({ d }: { d: Ind }) {
  return (
    <Shell title="Equity risk premium" why="Valuation context — slow-moving, not a timing signal and not part of the banner" good="above 2%" warn="below 0 (shares yield less than inflation-protected Treasuries)" ind={d}>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Stat label="Risk premium" value={signed(d.erp_pp, 2, ' pp')} tone={d.erp_pp < 0 ? 'text-red-600' : d.erp_pp < 2 ? 'text-amber-600' : 'text-green-700'} />
        <Stat label="Earnings yield" value={`${fmtNum(d.earnings_yield_pct, 2)}%`} sub={`1 ÷ CAPE ${fmtNum(d.cape, 1)}`} />
        <Stat label="10-year real yield" value={`${fmtNum(d.real_yield_pct, 2)}%`} sub="TIPS, FRED DFII10" />
        <Stat label="CAPE as of" value={String(d.cape_as_of).slice(0, 7)} />
      </div>
      {d.series && <Lines series={d.series} height={180} lines={[{ y: 0, label: '0' }, { y: 2, label: '2' }]} />}
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
          <div className="text-xs opacity-80">{o.improving} improving · {o.worsening} worsening · {o.warn} on watch · {o.bad} in warning. ▼ means moving toward the red line, ▲ away from it (over 20 trading days; quarterly for free cash flow, 3 months for the monthly France spread). Red when two or more indicators are in Warning (a daily indicator must stay red for 3 days in a row, and clears only once well back past its line); amber when all of them are worsening — moving toward their red line over 20 trading days — together.</div>
        </div>
        <Tooltip text={`Computed ${new Date(data.generated_at).toLocaleString()}; the scheduler refreshes it every few hours.`}>
          <Button size="sm" onClick={() => refresh.mutate()} disabled={refresh.isPending}>
            {refresh.isPending ? <Spinner size={12} /> : <RefreshCw size={12} />} Refresh
          </Button>
        </Tooltip>
      </div>
      <Playbook level={o.level as Level} />
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <FcfCard d={data.fcf} />
        <SpreadsCard d={data.spreads} />
        <TenYearCard d={data.ten_year} />
        <BreadthCard d={data.breadth} />
      </div>
      <div className="text-xs text-slate-500 pt-1">Context — these two do not count toward the banner</div>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <FranceCard d={data.france ?? { status: 'na', note: 'Run a refresh.' }} />
        <ErpCard d={data.erp ?? { status: 'na', note: 'Run a refresh.' }} />
      </div>
      <RecentChanges />
      <BacktestCard />
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
          {data.recent_changes > 0 && <span className="text-xs text-amber-700">{data.recent_changes} change{data.recent_changes === 1 ? '' : 's'} this week</span>}
          <span className="text-xs text-slate-400 hidden lg:inline">{data.overall.verdict}</span>
          <button className="text-xs text-blue-600 hover:underline shrink-0" onClick={() => navigate(`/market-data?tab=${encodeURIComponent('Market Watch')}`)}>Details →</button>
        </div>
      </CardBody>
    </Card>
  )
}
