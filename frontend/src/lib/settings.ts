import { getPref, setPref, subscribePref } from './preferences'

export interface AppSettings {
  decimalSep: string       // ',' | '.'
  thousandSep: string      // '.' | ',' | ' ' | ''
  dateFormat: string       // 'DD/MM/YYYY' | 'MM/DD/YYYY' | 'YYYY-MM-DD'
  weekStartDay: number     // 0 = Sunday, 1 = Monday
  reportingCurrency: string // 'EUR' — display-only, does not affect stored data
  defaultTransferPayeeName: string // auto-filled as the Payee when a new transaction is marked Transfer, if left blank
  bondAlertLeadDays: number // Dashboard heads-up window for bond maturity/coupon dates
  dividendAlertLeadDays: number // Dashboard heads-up window for security dividend payments
}

export const SETTINGS_DEFAULTS: AppSettings = {
  decimalSep: ',',
  thousandSep: '.',
  dateFormat: 'DD/MM/YYYY',
  weekStartDay: 1,
  reportingCurrency: 'EUR',
  defaultTransferPayeeName: 'Transfer Money',
  bondAlertLeadDays: 7,
  dividendAlertLeadDays: 3,
}

// Backed by lib/preferences.ts (server-side storage) instead of a standalone
// localStorage key, so settings follow the user across devices/browsers/
// origins. LEGACY_KEY is only read once, to migrate anyone upgrading from the
// old localStorage-only version.
const PREF_KEY = 'app-settings'
const LEGACY_KEY = 'oikos-settings'

let _current: AppSettings = { ...SETTINGS_DEFAULTS, ...getPref(PREF_KEY, SETTINGS_DEFAULTS) }

type Listener = (s: AppSettings) => void
const _listeners = new Set<Listener>()

// Keep _current in sync whenever the underlying preference changes (e.g. once
// the initial backend fetch resolves, possibly with a value saved from a
// different device/browser).
subscribePref(k => {
  if (k === PREF_KEY || k === '*') {
    _current = { ...SETTINGS_DEFAULTS, ...getPref(PREF_KEY, SETTINGS_DEFAULTS) }
    _listeners.forEach(fn => fn(_current))
  }
})

export function loadSettings(): AppSettings {
  const hasMigrated = getPref<AppSettings | undefined>(PREF_KEY, undefined) !== undefined
  if (!hasMigrated) {
    try {
      const legacyRaw = localStorage.getItem(LEGACY_KEY)
      if (legacyRaw) {
        setPref(PREF_KEY, { ...SETTINGS_DEFAULTS, ...JSON.parse(legacyRaw) })
        localStorage.removeItem(LEGACY_KEY)
      }
    } catch { /* ignore */ }
  }
  _current = { ...SETTINGS_DEFAULTS, ...getPref(PREF_KEY, SETTINGS_DEFAULTS) }
  return _current
}

export function saveSettings(s: AppSettings): void {
  _current = { ...s }
  setPref(PREF_KEY, s)
  _listeners.forEach(fn => fn(_current))
}

export function getSettings(): AppSettings {
  return _current
}

// Simple pub/sub so React components can re-render when settings change
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

export function getCurrencySymbol(currencyCode: string): string {
  return CURRENCY_SYMBOLS[currencyCode] ?? currencyCode
}

export function setReportingFx(rate: number, currencyCode: string): void {
  _reportingRate = rate
  _reportingSymbol = getCurrencySymbol(currencyCode)
  _listeners.forEach(fn => fn(_current))
}

export function getReportingFx(): { rate: number; symbol: string } {
  return { rate: _reportingRate, symbol: _reportingSymbol }
}
