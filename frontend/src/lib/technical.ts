// Support / resistance levels are computed on the server (database/technical.py, GET
// /market-data/technical/levels) — one calculation for the price charts and the
// Dashboard's Support / Resistance alerts. This file holds the level type and the chart
// line traces.

export type SrLevel = {
  kind: 'support' | 'resistance'
  price: number
  touches: number
  last_touch: string
  distance_pct: number // from the last price, signed
  extreme: 'high' | 'low' | null // the window's own high / low
}

// Horizontal Plotly line traces for the levels across [from, to] — legend-toggleable,
// like the chart's other reference lines (alerts, average cost, trailing stop).
// The nearest level of a kind is the darkest, the further ones lighter — and every line is solid-coloured and 2+ px, so each reads on its own.
const SUPPORT_COLORS = ['#14532d', '#15803d', '#22c55e']
const RESISTANCE_COLORS = ['#7f1d1d', '#b91c1c', '#ef4444']

export function srTraces(levels: SrLevel[], from: string, to: string, fmt: (v: number) => string, yaxis = 'y') {
  const rank = { support: 0, resistance: 0 }
  return levels.map(l => {
    rank[l.kind] += 1
    const tag = `${l.kind === 'support' ? 'S' : 'R'}${rank[l.kind]}`
    const what = l.extreme ? (l.extreme === 'high' ? 'period high' : 'period low') : `${l.touches} touches`
    const label = `${tag} ${fmt(l.price)} · ${what}${l.extreme && l.touches > 1 ? `, ${l.touches} touches` : ''}`
    return {
      x: [from, to], y: [l.price, l.price], type: 'scatter' as const, mode: 'lines' as const, name: label, yaxis,
      legendgroup: l.kind === 'support' ? 'sr-support' : 'sr-resistance',
      line: { color: (l.kind === 'support' ? SUPPORT_COLORS : RESISTANCE_COLORS)[Math.min(rank[l.kind], 3) - 1], width: Math.min(1.8 + 0.35 * l.touches, 3.2), dash: 'longdash' as const },
      opacity: 1,
      hovertemplate: `${label} (${l.distance_pct >= 0 ? '+' : ''}${l.distance_pct.toFixed(2)}% from last)<extra></extra>`,
    }
  })
}

// ── Grouped legend ──────────────────────────────────────────────────────────────────────────────
// A price chart carries many lines; one long legend run together is hard to read. This splits it into columns — the price and its averages,
// the reference lines (average cost, trailing stop, your alerts), the supports, the resistances — each its own Plotly legend (still click-to-hide),
// laid out in two rows under the chart: Price | Reference lines on top, Support | Resistance below. Returns the traces assigned to their
// legends, the layout for them and the extra height they need (the chart is then `baseHeight + extra` tall, with `extra` added to its bottom margin).
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyTrace = Record<string, any>

const REFERENCE_NAME = /^(Avg Cost|Trailing Stop|▲ Alert|▼ Alert)/
const CHAR_PX = 6.6            // a legend entry at 12px, per character
const SAMPLE_PX = 70           // the line sample, its padding and the column gap
const ROW_PX = 19
const TITLE_PX = 26
const CHART_PX = 820           // the width a chart usually has: a column's x position is its pixel offset over this

export function groupLegends(traces: AnyTrace[], isDark: boolean, baseHeight: number) {
  const keyOf = (t: AnyTrace) =>
    t.legendgroup === 'sr-support' ? 'support' : t.legendgroup === 'sr-resistance' ? 'resistance'
    : REFERENCE_NAME.test(String(t.name ?? '')) ? 'reference' : 'price'
  const neutral = isDark ? '#cbd5e1' : '#334155'
  const defs: Record<string, { title: string; color: string }> = {
    price: { title: 'Price', color: neutral },
    reference: { title: 'Reference lines', color: neutral },
    support: { title: 'Support', color: isDark ? '#4ade80' : '#15803d' },
    resistance: { title: 'Resistance', color: isDark ? '#f87171' : '#b91c1c' },
  }
  const members = (k: string) => traces.filter(t => keyOf(t) === k && t.showlegend !== false)
  const rowsDef = [['price', 'reference'], ['support', 'resistance']]
    .map(r => r.filter(k => members(k).length > 0)).filter(r => r.length > 0)
  const rowHeight = (r: string[]) => TITLE_PX + ROW_PX * Math.max(...r.map(k => members(k).length))
  const heights = rowsDef.map(rowHeight)
  const extra = heights.reduce((s, h) => s + h, 0) + 8
  const height = baseHeight + extra

  const layout: Record<string, unknown> = {}
  const legendName = new Map<string, string>()
  let n = 0
  let below = 0                                              // pixels taken by the rows beneath this one
  for (let ri = rowsDef.length - 1; ri >= 0; ri--) {
    const row = rowsDef[ri]
    let used = 0
    row.forEach(k => {
      const name = n === 0 ? 'legend' : `legend${n + 1}`
      n += 1
      legendName.set(k, name)
      layout[name] = {
        orientation: 'v', xref: 'container', yref: 'container', x: Math.min(used / CHART_PX, 0.9), y: below / height, xanchor: 'left', yanchor: 'bottom',
        title: { text: `<b>${defs[k].title}</b>`, font: { size: 12, color: defs[k].color } }, font: { size: 12, color: isDark ? '#e2e8f0' : '#1e293b' },
        itemsizing: 'constant', itemwidth: 44, tracegroupgap: 0, bgcolor: 'rgba(0,0,0,0)',
      }
      used += Math.max(...members(k).map(t => String(t.name ?? '').length), 8) * CHAR_PX + SAMPLE_PX
    })
    below += heights[ri]
  }
  const data = traces.map(t => ({ ...t, legend: legendName.get(keyOf(t)) ?? 'legend' }))
  return { data, layout, extra }
}
