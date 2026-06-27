import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function fmt(value: number | null | undefined, decimals = 2, prefix = '') {
  if (value == null) return '—'
  const n = Number(value)
  if (isNaN(n)) return '—'
  const abs = Math.abs(n)
  const formatted = abs.toLocaleString('el-GR', { minimumFractionDigits: decimals, maximumFractionDigits: decimals })
  return `${n < 0 ? '-' : ''}${prefix}${formatted}`
}

export function fmtEur(value: number | null | undefined) {
  return fmt(value, 2, '€')
}

export function fmtPct(value: number | null | undefined) {
  if (value == null) return '—'
  return `${Number(value).toFixed(1)}%`
}

export function fmtDate(date: string | null | undefined) {
  if (!date) return '—'
  return date.slice(0, 10)
}

// ── Plotly dark-mode helpers ──────────────────────────────────────────────────
const DARK_BG   = '#1e293b'
const DARK_GRID = '#334155'
const DARK_TICK = '#94a3b8'
const DARK_TEXT = '#e2e8f0'

export function plotLayout(isDark: boolean): Record<string, unknown> {
  if (!isDark) return { paper_bgcolor: 'white', plot_bgcolor: 'white' }
  return {
    paper_bgcolor: DARK_BG,
    plot_bgcolor:  DARK_BG,
    font: { color: DARK_TEXT },
  }
}

export function plotAxis(isDark: boolean, overrides: Record<string, unknown> = {}): Record<string, unknown> {
  if (!isDark) return overrides
  return { gridcolor: DARK_GRID, linecolor: DARK_GRID, zerolinecolor: DARK_GRID, color: DARK_TICK, ...overrides }
}
