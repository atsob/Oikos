import { useEffect } from 'react'

// A report table marked `data-cards` is shown as a list of cards on a phone (the CSS is in index.css): the header is hidden, each row becomes a card
// whose first cell is the title and whose other cells are small label-over-value pairs in two columns. The labels come from the table's own header,
// which is read here — each cell gets its column's name as `data-label`, long text is marked `data-wide` (it takes a full row) and an empty cell or a
// dash `data-empty` (it is left out). Runs only on a phone, and again whenever the page changes (a report loading, a sort, a drill-down).
const NOISE = /[↑↓▲▼⇅⇵ⓘ✏️✓]/g

function label(table: HTMLTableElement) {
  const headRow = [...table.querySelectorAll('thead tr')].pop()
  const names: string[] = []
  if (headRow) {
    for (const th of Array.from(headRow.children) as HTMLTableCellElement[]) {
      const text = (th.textContent ?? '').replace(NOISE, '').replace(/\s+/g, ' ').trim()
      for (let k = 0; k < (th.colSpan || 1); k++) names.push(text)
    }
  }
  table.querySelectorAll('tr').forEach(tr => {
    if (tr.closest('thead')) return
    let col = 0
    for (const td of Array.from(tr.children) as HTMLTableCellElement[]) {
      const name = names[col] ?? ''
      if (td.dataset.label !== name) td.dataset.label = name
      const text = (td.textContent ?? '').trim()
      const empty = text === '' || /^[—–-]$/.test(text)
      if (empty) td.setAttribute('data-empty', ''); else td.removeAttribute('data-empty')
      if (text.length > 24) td.setAttribute('data-wide', ''); else td.removeAttribute('data-wide')
      col += td.colSpan || 1
    }
  })
}

export function useCardTables() {
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 767px)')
    let raf = 0
    const run = () => { if (mq.matches) document.querySelectorAll<HTMLTableElement>('main table[data-cards]').forEach(label) }
    const schedule = () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(run) }
    const main = document.querySelector('main')
    const obs = new MutationObserver(schedule)       // childList only: setting an attribute does not trigger it again
    if (main) obs.observe(main, { childList: true, subtree: true, characterData: true })
    mq.addEventListener('change', schedule)
    run()
    return () => { obs.disconnect(); mq.removeEventListener('change', schedule); cancelAnimationFrame(raf) }
  }, [])
}
