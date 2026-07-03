import { useState, useCallback, useEffect } from 'react'
import { getSettings, saveSettings, subscribeSettings } from './settings'
import type { AppSettings } from './settings'
import { getPref, setPref, subscribePref } from './preferences'

export function useSettings(): [AppSettings, (s: AppSettings) => void] {
  const [settings, setSettings] = useState<AppSettings>(getSettings)
  useEffect(() => subscribeSettings(setSettings), [])
  return [settings, saveSettings]
}

// Persists UI state (tab selections, saved filters, etc.) server-side via
// lib/preferences.ts, so it follows the user across devices/browsers/origins
// instead of being trapped in one browser's localStorage. Signature/behavior
// is unchanged from the old localStorage-only version, so call sites don't
// need to change.
export function usePersist<T>(key: string, defaultVal: T) {
  const [val, setVal] = useState<T>(() => getPref(key, defaultVal))
  useEffect(() => subscribePref(k => {
    if (k === key || k === '*') setVal(getPref(key, defaultVal))
  }), [key]) // eslint-disable-line react-hooks/exhaustive-deps
  const set = useCallback((v: T) => setPref(key, v), [key])
  return [val, set] as const
}
