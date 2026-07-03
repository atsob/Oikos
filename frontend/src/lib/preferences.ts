import { getPreferences, setPreference } from './api'

// Server-backed UI preference store. localStorage is scoped per browser origin
// (and can be evicted by the browser, e.g. Safari's storage eviction), so it
// doesn't survive being accessed via a different hostname/IP or after a long
// idle period. This module mirrors the same key/value pairs to the backend
// (User_Preferences table) so they follow the user across devices/origins,
// while still hydrating instantly from localStorage on first paint.

const CACHE_KEY = 'oikos-prefs-cache'

let _cache: Record<string, unknown> = {}
try {
  const raw = localStorage.getItem(CACHE_KEY)
  if (raw) _cache = JSON.parse(raw)
} catch { /* ignore */ }

let _loaded = false

type Listener = (key: string) => void
const _listeners = new Set<Listener>()

function persistCache() {
  try { localStorage.setItem(CACHE_KEY, JSON.stringify(_cache)) } catch { /* ignore */ }
}

export function initPreferences(): Promise<void> {
  return getPreferences()
    .then(remote => {
      _cache = { ..._cache, ...remote }
      persistCache()
    })
    .catch(() => { /* offline / backend unavailable — fall back to local cache */ })
    .finally(() => {
      _loaded = true
      _listeners.forEach(fn => fn('*'))
    })
}

export function isPreferencesLoaded(): boolean {
  return _loaded
}

export function getPref<T>(key: string, defaultVal: T): T {
  return key in _cache ? (_cache[key] as T) : defaultVal
}

export function setPref<T>(key: string, value: T): void {
  _cache[key] = value
  persistCache()
  _listeners.forEach(fn => fn(key))
  setPreference(key, value).catch(() => { /* will retry via next initPreferences() reconcile */ })
}

export function subscribePref(fn: Listener): () => void {
  _listeners.add(fn)
  return () => _listeners.delete(fn)
}
