import { useQuery } from '@tanstack/react-query'
import { getChangelog } from '@/lib/api'
import { PageHeader, Spinner } from '@/components/ui'
import React from 'react'

// ── Minimal markdown-lite renderer ──────────────────────────────────────────────
// CHANGELOG.md only ever uses #/##/### headings, "- " bullets, **bold**, and `code` —
// so a tiny hand-rolled renderer avoids pulling in a full markdown dependency just
// for this one page.
function renderInline(text: string, key: number): React.ReactNode {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter(Boolean)
  return (
    <React.Fragment key={key}>
      {parts.map((p, i) => {
        if (p.startsWith('**') && p.endsWith('**')) return <b key={i}>{p.slice(2, -2)}</b>
        if (p.startsWith('`') && p.endsWith('`')) return <code key={i} className="bg-slate-100 text-slate-700 rounded px-1 py-0.5 text-xs font-mono">{p.slice(1, -1)}</code>
        return p
      })}
    </React.Fragment>
  )
}

function renderMarkdownLite(text: string): React.ReactNode {
  const lines = text.split('\n')
  const blocks: React.ReactNode[] = []
  let listItems: string[] = []

  const flushList = () => {
    if (listItems.length) {
      blocks.push(
        <ul key={blocks.length} className="list-disc pl-5 text-sm text-slate-600 leading-relaxed space-y-1 mb-3">
          {listItems.map((li, i) => <li key={i}>{renderInline(li, i)}</li>)}
        </ul>,
      )
      listItems = []
    }
  }

  for (const rawLine of lines) {
    const line = rawLine.trimEnd()
    if (line.startsWith('### ')) { flushList(); blocks.push(<h3 key={blocks.length} className="text-sm font-semibold text-slate-700 mt-5 mb-1.5">{line.slice(4)}</h3>); continue }
    if (line.startsWith('## '))  { flushList(); blocks.push(<h2 key={blocks.length} className="text-lg font-semibold text-slate-800 mt-8 mb-2 first:mt-0">{line.slice(3)}</h2>); continue }
    if (line.startsWith('# '))   { flushList(); blocks.push(<h1 key={blocks.length} className="text-xl font-bold text-slate-900 mb-2">{line.slice(2)}</h1>); continue }
    if (line.startsWith('- '))   { listItems.push(line.slice(2)); continue }
    flushList()
    if (line.trim()) blocks.push(<p key={blocks.length} className="text-sm text-slate-600 leading-relaxed mb-2">{renderInline(line, 0)}</p>)
  }
  flushList()
  return blocks
}

export default function Releases() {
  const { data, isLoading } = useQuery({ queryKey: ['changelog'], queryFn: getChangelog })

  return (
    <div>
      <PageHeader title="Release Notes" subtitle="What's changed, most recent first" />
      <div className="px-6 py-6 max-w-3xl">
        {isLoading
          ? <div className="flex justify-center py-12"><Spinner /></div>
          : renderMarkdownLite(data?.content ?? '')}
      </div>
    </div>
  )
}
