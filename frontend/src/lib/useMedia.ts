import { useEffect, useState } from 'react'

/** True while a media query matches, and follows it as the window or the phone's orientation changes. */
export function useMedia(query: string): boolean {
  const [matches, setMatches] = useState(() => typeof window !== 'undefined' && window.matchMedia(query).matches)
  useEffect(() => {
    const mq = window.matchMedia(query)
    const on = () => setMatches(mq.matches)
    on()
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [query])
  return matches
}

/** A phone in portrait: narrower than Tailwind's `sm` breakpoint (640 px). Pages use it to show a list of cards instead of a wide table. */
export const usePhone = () => useMedia('(max-width: 639px)')
