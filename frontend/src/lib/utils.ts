import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'
import { getSettings, getReportingFx } from './settings'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

function _formatNumber(abs: number, decimals: number, trimZeros = false): string {
  const { decimalSep, thousandSep } = getSettings()
  const [intPart, rawDec] = abs.toFixed(decimals).split('.')
  const thousands = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, thousandSep)
  if (!decimals) return thousands
  const decPart = trimZeros ? (rawDec ?? '').replace(/0+$/, '') : rawDec
  return decPart ? `${thousands}${decimalSep}${decPart}` : thousands
}

// fmtQty: like fmtNum but trims insignificant trailing zeros (good for quantities)
export function fmtQty(value: number | null | undefined, maxDecimals = 8): string {
  if (value == null) return '—'
  const n = Number(value)
  if (isNaN(n)) return '—'
  return (n < 0 ? '-' : '') + _formatNumber(Math.abs(n), maxDecimals, true)
}

export function fmt(value: number | null | undefined, decimals = 2, prefix = '') {
  if (value == null) return '—'
  const n = Number(value)
  if (isNaN(n)) return '—'
  const formatted = _formatNumber(Math.abs(n), decimals)
  return `${n < 0 ? '-' : ''}${prefix}${formatted}`
}

export function fmtEur(value: number | null | undefined) {
  if (value == null) return '—'
  const { rate, symbol } = getReportingFx()
  // rate = EUR per 1 unit of reporting currency (e.g. 0.8768 for USD means 1 USD = 0.8768 EUR)
  // so EUR → reporting currency = amount_eur / rate
  return fmt(rate === 0 ? 0 : Number(value) / rate, 2, symbol)
}

export function fmtNum(value: number | null | undefined, decimals = 2): string {
  return fmt(value, decimals)
}

export function fmtPct(value: number | null | undefined, decimals = 1): string {
  if (value == null) return '—'
  const n = Number(value)
  if (isNaN(n)) return '—'
  return (n < 0 ? '-' : '') + _formatNumber(Math.abs(n), decimals) + '%'
}

export function fmtDate(date: string | null | undefined): string {
  if (!date) return '—'
  const iso = date.slice(0, 10) // YYYY-MM-DD
  const { dateFormat } = getSettings()
  if (dateFormat === 'DD/MM/YYYY') return `${iso.slice(8, 10)}/${iso.slice(5, 7)}/${iso.slice(0, 4)}`
  if (dateFormat === 'MM/DD/YYYY') return `${iso.slice(5, 7)}/${iso.slice(8, 10)}/${iso.slice(0, 4)}`
  return iso // YYYY-MM-DD
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
