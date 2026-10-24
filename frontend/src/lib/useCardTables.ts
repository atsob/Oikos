import { useEffect } from 'react'

// On a phone a list-like table is shown as a list of cards (the CSS is in index.css). A table qualifies when it has a header and between 4 and 10
// columns — fewer fit as they are, more are a matrix (periods across, holdings against holdings) that reads better scrolling with its first column
// pinned — unless it says `data-matrix`. A table can also ask for cards with `data-cards`. Here the header is read: each cell gets its column's name as
// `data-label`, the first cell with text is the card's title (`data-title`; a check box or blank cell before it is `data-pre` and sits in the corner),
// long text is marked `data-wide` (it takes a full row) and an empty cell or a dash `data-empty` (it is left out). It runs on a phone only, and again
// whenever the page changes (a report loading, a sort, a drill-down).
const NOISE = /[↑↓▲▼⇅⇵ⓘ✏️✓]/g
const MIN_COLS = 4, MAX_COLS = 10

function headerNames(table: HTMLTableElement): string[] {
  const headRow = [...table.querySelectorAll('thead tr')].pop()
  const names: string[] = []
  if (headRow) {
    for (const th of Array.from(headRow.children) as HTMLTableCellElement[]) {
      const text = (th.textContent ?? '').replace(NOISE, '').replace(/\s+/g, ' ').trim()
      for (let k = 0; k < (th.colSpan || 1); k++) names.push(text)
    }
  }
  return names
}

function eligible(table: HTMLTableElement, names: string[]): boolean {
  if (table.hasAttribute('data-matrix') || table.closest('.ag-theme-alpine, .phone-cards')) return false
  if (names.length < MIN_COLS || names.length > MAX_COLS) return false
  return !!table.querySelector('tbody tr')
}

function label(table: HTMLTableElement, names: string[]) {
  table.querySelectorAll('tr').forEach(tr => {
    if (tr.closest('thead')) return
    let col = 0, titled = false
    for (const td of Array.from(tr.children) as HTMLTableCellElement[]) {
      const name = names[col] ?? ''
      if (td.dataset.label !== name) td.dataset.label = name
      const text = (td.textContent ?? '').trim()
      const set = (attr: string, on: boolean) => (on ? td.setAttribute(attr, '') : td.removeAttribute(attr))
      const empty = text === '' || /^[—–-]$/.test(text)
      const hasControl = !!td.querySelector('input, button, select')
      const isTitle = !titled && text.length > 0 && !(td.querySelector('input[type=checkbox]') && text.length < 2)
      set('data-title', isTitle)
      set('data-pre', !titled && !isTitle && col === 0)
      set('data-empty', empty && !hasControl && !(!titled && col === 0))
      set('data-wide', text.length > 24)
      if (isTitle) titled = true
      col += td.colSpan || 1
    }
  })
}

export function useCardTables() {
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 767px)')
    let raf = 0
    const run = () => {
      // a row that is a check box or radio button and its text must keep them side by side (the text shrinks), not wrap under the box
      document.querySelectorAll('main .flex > input[type="checkbox"], main .flex > input[type="radio"]').forEach(i => i.parentElement?.setAttribute('data-nowrap', ''))
      const tables = document.querySelectorAll<HTMLTableElement>('main table')
      if (!mq.matches) { tables.forEach(t => { if (t.hasAttribute('data-auto')) { t.removeAttribute('data-auto'); t.removeAttribute('data-cards') } }); return }
      tables.forEach(t => {
        const names = headerNames(t)
        if (t.hasAttribute('data-cards') && !t.hasAttribute('data-auto')) { label(t, names); return }
        if (eligible(t, names)) { t.setAttribute('data-cards', ''); t.setAttribute('data-auto', ''); label(t, names) }
        else if (t.hasAttribute('data-auto')) { t.removeAttribute('data-auto'); t.removeAttribute('data-cards') }
      })
    }
    const schedule = () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(run) }
    const main = document.querySelector('main')
    const obs = new MutationObserver(schedule)       // childList only: setting an attribute does not trigger it again
    if (main) obs.observe(main, { childList: true, subtree: true, characterData: true })
    mq.addEventListener('change', schedule)
    run()
    return () => { obs.disconnect(); mq.removeEventListener('change', schedule); cancelAnimationFrame(raf) }
  }, [])
}
