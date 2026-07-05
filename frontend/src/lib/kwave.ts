// ── Kondratieff wave reference overlay ────────────────────────────────────────
// Kondratieff ("K-wave") theory describes ~40-60 year secular cycles in capital
// markets, split into four "seasons". This is a contested, non-consensus economic
// theory — different analysts date the same turning points a decade or more apart,
// and with only a handful of full cycles ever observed there's no statistically
// meaningful way to validate or predict it. This module exists purely to render
// a clearly-labelled historical/educational overlay — never as a forecast or
// trading signal.
//
// The phase list itself is stored server-side (see lib/preferences.ts, key
// 'kwave_phases') and editable in the UI, not hardcoded, so the boundaries can be
// kept current — e.g. adding a new phase once a secular regime change is broadly
// agreed on — without a code change. DEFAULT_KWAVE_PHASES below is only the
// starting point for a fresh install / a "Reset to defaults" action.

export type KWaveSeason = 'Spring' | 'Summer' | 'Autumn' | 'Winter'

export interface KWavePhase {
  label: string
  season: KWaveSeason
  start: string        // ISO date
  end: string | null   // ISO date, or null if still ongoing
  description: string
}

// Translucent fill per season, so a new/edited phase always gets a sensible
// colour without the user having to pick one.
export const SEASON_COLORS: Record<KWaveSeason, string> = {
  Spring: 'rgba(34,197,94,0.08)',
  Summer: 'rgba(239,68,68,0.08)',
  Autumn: 'rgba(234,179,8,0.08)',
  Winter: 'rgba(59,130,246,0.08)',
}

// Matches the widely-cited secular bull/bear market cycle dating for major equity
// indices (e.g. the Dow) — 1949/1966/1982/2000/2013 turning points — rather than
// the more niche "Winter never ended" reading some Kondratieff commentators still
// argue for; the 2013-present period has behaved like a genuine secular bull on
// realised price data, so it's shown as a new Spring rather than folded into Winter.
export const DEFAULT_KWAVE_PHASES: KWavePhase[] = [
  {
    label: 'Spring — Inflationary Growth',
    season: 'Spring',
    start: '1949-01-01',
    end: '1966-01-01',
    description: 'Post-war recovery and rebuilding; new technologies commercialised, credit expands from a low base.',
  },
  {
    label: 'Summer — Stagflationary Shock',
    season: 'Summer',
    start: '1966-01-01',
    end: '1982-01-01',
    description: 'Growth continues but with rising inflation, commodity shocks, and geopolitical conflict eroding real returns.',
  },
  {
    label: 'Autumn — Credit Boom / Plateau',
    season: 'Autumn',
    start: '1982-01-01',
    end: '2000-01-01',
    description: 'Disinflation, easy credit, and rising asset prices — the "roaring" phase of the cycle, often mistaken for a new normal.',
  },
  {
    label: 'Winter — Deleveraging',
    season: 'Winter',
    start: '2000-01-01',
    end: '2013-01-01',
    description: 'Dot-com bust, financial crisis, and debt deleveraging — two secular bear markets inside one K-wave winter.',
  },
  {
    label: 'Spring — New Secular Bull',
    season: 'Spring',
    start: '2013-01-01',
    end: null,
    description: 'Sustained multi-year advance in real equity prices since 2013, per this framework a new secular bull / early K-wave spring.',
  },
]

export const KWAVE_DISCLAIMER =
  'Kondratieff wave "seasons" are a contested, non-mainstream economic theory — different analysts date the same ' +
  'turning points a decade or more apart, and with only a handful of full ~50-60 year cycles ever observed there is ' +
  'no statistically rigorous way to validate or predict them. This overlay shows one editable dating for ' +
  'historical/educational context only — it is not a forecast, and no specific date is given for when the current ' +
  'phase might end.'

// Plotly shapes + annotations for whichever phases overlap [fromDate, toDate].
// toDate is used as the right edge for any still-open (end: null) phase.
export function getKWaveOverlay(phases: KWavePhase[], fromDate: string, toDate: string) {
  const from = new Date(fromDate).getTime()
  const to = new Date(toDate).getTime()
  const shapes: Record<string, unknown>[] = []
  const annotations: Record<string, unknown>[] = []

  for (const phase of phases) {
    const phaseStart = new Date(phase.start).getTime()
    const phaseEnd = phase.end ? new Date(phase.end).getTime() : to
    if (phaseEnd < from || phaseStart > to) continue

    const x0 = phaseStart < from ? fromDate : phase.start
    const x1 = phaseEnd > to ? toDate : (phase.end ?? toDate)

    shapes.push({
      type: 'rect', xref: 'x', yref: 'paper',
      x0, x1, y0: 0, y1: 1,
      fillcolor: SEASON_COLORS[phase.season], line: { width: 0 },
      layer: 'below',
    })
    annotations.push({
      x: x0, y: 1, xref: 'x', yref: 'paper',
      xanchor: 'left', yanchor: 'top',
      text: phase.season, showarrow: false,
      font: { size: 10, color: '#64748b' },
      bgcolor: 'rgba(255,255,255,0.6)',
    })
  }
  return { shapes, annotations }
}
