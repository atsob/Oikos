export interface AppSettings {
  decimalSep: string       // ',' | '.'
  thousandSep: string      // '.' | ',' | ' ' | ''
  dateFormat: string       // 'DD/MM/YYYY' | 'MM/DD/YYYY' | 'YYYY-MM-DD'
  weekStartDay: number     // 0 = Sunday, 1 = Monday
  reportingCurrency: string // 'EUR' — display-only, does not affect stored data
}

export const SETTINGS_DEFAULTS: AppSettings = {
  decimalSep: ',',
  thousandSep: '.',
  dateFormat: 'DD/MM/YYYY',
  weekStartDay: 1,
  reportingCurrency: 'EUR',
}

const KEY = 'oikos-settings'

let _current: AppSettings = { ...SETTINGS_DEFAULTS }

export function loadSettings(): AppSettings {
  try {
    const raw = localStorage.getItem(KEY)
    if (raw) _current = { ...SETTINGS_DEFAULTS, ...JSON.parse(raw) }
  } catch { /* ignore */ }
  return _current
}

export function saveSettings(s: AppSettings): void {
  _current = { ...s }
  localStorage.setItem(KEY, JSON.stringify(s))
  // Notify subscribers
  _listeners.forEach(fn => fn(_current))
}

export function getSettings(): AppSettings {
  return _current
}

// Simple pub/sub so React components can re-render when settings change
type Listener = (s: AppSettings) => void
const _listeners = new Set<Listener>()
export function subscribeSettings(fn: Listener): () => void {
  _listeners.add(fn)
  return () => _listeners.delete(fn)
}

// ── Reporting currency FX (runtime-only, not persisted) ───────────────────────
// rate = units of reportingCurrency per 1 EUR  (EUR→EUR = 1.0)
const CURRENCY_SYMBOLS: Record<string, string> = {
  EUR: '€', USD: '$', GBP: '£', JPY: '¥', CHF: 'Fr',
  CAD: 'C$', AUD: 'A$', NZD: 'NZ$', SEK: 'kr', NOK: 'kr',
  DKK: 'kr', HUF: 'Ft', CZK: 'Kč', PLN: 'zł', RON: 'lei',
  BGN: 'лв', HRK: 'kn', TRY: '₺', RUB: '₽', CNY: '¥',
  HKD: 'HK$', SGD: 'S$', KRW: '₩', INR: '₹', BRL: 'R$',
  MXN: '$', ZAR: 'R', SAR: '﷼', AED: 'د.إ', ILS: '₪',
  THB: '฿', IDR: 'Rp', MYR: 'RM', PHP: '₱', PKR: '₨',
}

let _reportingRate = 1.0
let _reportingSymbol = '€'

export function setReportingFx(rate: number, currencyCode: string): void {
  _reportingRate = rate
  _reportingSymbol = CURRENCY_SYMBOLS[currencyCode] ?? currencyCode
  _listeners.forEach(fn => fn(_current))
}

export function getReportingFx(): { rate: number; symbol: string } {
  return { rate: _reportingRate, symbol: _reportingSymbol }
}
