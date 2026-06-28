import { useState, useCallback } from 'react'

export function usePersist<T>(key: string, defaultVal: T) {
  const [val, setVal] = useState<T>(() => {
    try { const s = localStorage.getItem(key); return s !== null ? JSON.parse(s) : defaultVal } catch { return defaultVal }
  })
  const set = useCallback((v: T) => { setVal(v); try { localStorage.setItem(key, JSON.stringify(v)) } catch {} }, [key])
  return [val, set] as const
}
