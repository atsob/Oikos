// Support / resistance levels for a price (or FX-rate) series, computed from exactly the
// series a chart plots — so a security's chart, a currency's stored-rate chart and its
// reporting-currency chart all use one method.
//
// Method:
//   1. Turning points (zig-zag): a peak counts once the price has since fallen from it by
//      at least a reversal threshold, a trough once it has risen by that much — so daily
//      noise is ignored and only real swings are kept. The threshold scales with the
//      series' own volatility: 5× its average daily move, between 1% and 15% (about 2%
//      for a currency, 5–8% for a typical stock).
//   2. Grouping: peaks and troughs are pooled (a broken resistance tends to become
//      support and vice versa) and grouped when they lie within a zone half the
//      threshold wide (at least 0.5%), measured from the zone's lowest point so zones
//      never chain into one wide band.
//   3. A level is a zone's average price; its strength is how many turning points it
//      holds ("touches"). The period's own high and low always count as levels.
//   4. Each level is support if below the last price, resistance if above; the three
//      nearest on each side are returned.

export type SrPoint = { date: string; close: number; high?: number | null; low?: number | null }
export type SrLevel = {
  kind: 'support' | 'resistance'
  price: number
  touches: number
  lastTouch: string
  distancePct: number // from the last price, signed
  extreme?: 'high' | 'low' // the window's own high / low
}

export function supportResistance(points: SrPoint[], perSide = 3): SrLevel[] {
  const pts = points.filter(p => Number.isFinite(p.close) && p.close > 0)
  const n = pts.length
  if (n < 20) return []
  const hi = pts.map(p => (p.high != null && Number.isFinite(p.high) && p.high > 0 ? p.high : p.close))
  const lo = pts.map(p => (p.low != null && Number.isFinite(p.low) && p.low > 0 ? p.low : p.close))
  const last = pts[n - 1].close

  let movePct = 0
  for (let i = 1; i < n; i++) movePct += Math.abs(pts[i].close / pts[i - 1].close - 1)
  const threshold = Math.min(0.15, Math.max(0.01, 5 * movePct / (n - 1)))

  // Zig-zag: follow the move, and confirm its extreme as a turning point once the price
  // has reversed from it by the threshold.
  type Swing = { price: number; date: string; i: number }
  const swings: Swing[] = []
  let dir: 1 | -1 | 0 = 0
  let exHi = 0, exLo = 0 // index of the running extreme (high while rising, low while falling)
  for (let i = 1; i < n; i++) {
    if (dir === 0) {
      if (hi[i] > hi[exHi]) exHi = i
      if (lo[i] < lo[exLo]) exLo = i
      if (lo[i] <= hi[exHi] * (1 - threshold)) { swings.push({ price: hi[exHi], date: pts[exHi].date, i: exHi }); dir = -1; exLo = i }
      else if (hi[i] >= lo[exLo] * (1 + threshold)) { swings.push({ price: lo[exLo], date: pts[exLo].date, i: exLo }); dir = 1; exHi = i }
    } else if (dir === 1) {
      if (hi[i] >= hi[exHi]) exHi = i
      else if (lo[i] <= hi[exHi] * (1 - threshold)) { swings.push({ price: hi[exHi], date: pts[exHi].date, i: exHi }); dir = -1; exLo = i }
    } else {
      if (lo[i] <= lo[exLo]) exLo = i
      else if (hi[i] >= lo[exLo] * (1 + threshold)) { swings.push({ price: lo[exLo], date: pts[exLo].date, i: exLo }); dir = 1; exHi = i }
    }
  }

  const tol = last * Math.max(0.005, threshold / 2)
  type Cluster = { start: number; sum: number; count: number; last: string }
  const clusters: Cluster[] = []
  // The first few bars are where the series starts, not a turn the price made.
  for (const s of swings.filter(w => w.i > 2).sort((a, b) => a.price - b.price)) {
    const c = clusters[clusters.length - 1]
    if (c && s.price - c.start <= tol) {
      c.sum += s.price; c.count += 1
      if (s.date > c.last) c.last = s.date
    } else {
      clusters.push({ start: s.price, sum: s.price, count: 1, last: s.date })
    }
  }
  const levels: Omit<SrLevel, 'kind' | 'distancePct'>[] = clusters
    .map(c => ({ price: c.sum / c.count, touches: c.count, lastTouch: c.last }))

  // The window's own high and low always count — the obvious ceiling and floor — unless
  // a clustered level already sits on them.
  const iMax = hi.indexOf(Math.max(...hi)), iMin = lo.indexOf(Math.min(...lo))
  for (const [price, date, extreme] of [[hi[iMax], pts[iMax].date, 'high'], [lo[iMin], pts[iMin].date, 'low']] as const) {
    const near = levels.find(l => Math.abs(l.price - price) <= tol)
    if (near) Object.assign(near, { extreme })
    else levels.push({ price, touches: 1, lastTouch: date, extreme })
  }

  const out: SrLevel[] = levels
    .filter(l => l.price !== last) // a level exactly at the last price is neither
    .map(l => ({ ...l, kind: l.price < last ? 'support' as const : 'resistance' as const, distancePct: (l.price / last - 1) * 100 }))
  const side = (kind: SrLevel['kind']) => out
    .filter(l => l.kind === kind)
    .sort((a, b) => Math.abs(a.distancePct) - Math.abs(b.distancePct))
    .slice(0, perSide)
  return [...side('support'), ...side('resistance')]
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
      hovertemplate: `${label} (${l.distancePct >= 0 ? '+' : ''}${l.distancePct.toFixed(2)}% from last)<extra></extra>`,
    }
  })
}
