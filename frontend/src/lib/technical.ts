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
export function srTraces(levels: SrLevel[], from: string, to: string, fmt: (v: number) => string, yaxis = 'y') {
  const rank = { support: 0, resistance: 0 }
  return levels.map(l => {
    rank[l.kind] += 1
    const tag = `${l.kind === 'support' ? 'S' : 'R'}${rank[l.kind]}`
    const what = l.extreme ? (l.extreme === 'high' ? 'period high' : 'period low') : `${l.touches} touches`
    const label = `${tag} ${fmt(l.price)} · ${what}${l.extreme && l.touches > 1 ? `, ${l.touches} touches` : ''}`
    return {
      x: [from, to], y: [l.price, l.price], type: 'scatter' as const, mode: 'lines' as const, name: label, yaxis,
      line: { color: l.kind === 'support' ? '#16a34a' : '#dc2626', width: Math.min(1 + 0.4 * l.touches, 2.6), dash: 'longdash' as const },
      opacity: 0.75,
      hovertemplate: `${label} (${l.distance_pct >= 0 ? '+' : ''}${l.distance_pct.toFixed(2)}% from last)<extra></extra>`,
    }
  })
}
