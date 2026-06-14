import { createContext, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'

export type Theme = 'light' | 'dark' | 'system'

interface ThemeCtx { theme: Theme; setTheme: (t: Theme) => void; isDark: boolean }
const Ctx = createContext<ThemeCtx>({ theme: 'system', setTheme: () => {}, isDark: false })

function resolveIsDark(theme: Theme) {
  if (theme === 'dark') return true
  if (theme === 'light') return false
  return window.matchMedia('(prefers-color-scheme: dark)').matches
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(
    () => (localStorage.getItem('oikos-theme') as Theme) ?? 'system'
  )
  const [isDark, setIsDark] = useState(() => resolveIsDark(
    (localStorage.getItem('oikos-theme') as Theme) ?? 'system'
  ))

  const setTheme = (t: Theme) => {
    localStorage.setItem('oikos-theme', t)
    setThemeState(t)
    setIsDark(resolveIsDark(t))
  }

  // Apply / remove .dark on <html>
  useEffect(() => {
    document.documentElement.classList.toggle('dark', isDark)
  }, [isDark])

  // Track system preference changes when in 'system' mode
  useEffect(() => {
    if (theme !== 'system') return
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const handler = () => setIsDark(mq.matches)
    mq.addEventListener('change', handler)
    return () => mq.removeEventListener('change', handler)
  }, [theme])

  return <Ctx.Provider value={{ theme, setTheme, isDark }}>{children}</Ctx.Provider>
}

export const useTheme = () => useContext(Ctx)
