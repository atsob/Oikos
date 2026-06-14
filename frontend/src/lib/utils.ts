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
