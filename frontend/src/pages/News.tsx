import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getNews, generateNews, markNewsRead, searchNews } from '@/lib/api'
import { PageHeader, Card, Button, Badge, Spinner, Tooltip, Input } from '@/components/ui'
import { fmtDate } from '@/lib/utils'
import { usePersist } from '@/lib/hooks'
import { RefreshCw, ExternalLink, Newspaper, Search, X } from 'lucide-react'

type SourceType = 'Security' | 'Institution' | 'Payee'

export type NewsItem = {
  id: number | null
  source_type: SourceType | null
  source_id: number | null
  title: string
  url: string
  publisher: string | null
  summary: string | null
  published_at: string | null
  fetched_at?: string
  is_read: boolean
  source_name: string | null
  ticker: string | null
}

const FILTERS: { key: string; label: string; sourceType?: string }[] = [
  { key: 'all',         label: 'All' },
  { key: 'securities',  label: 'Securities',  sourceType: 'Security' },
  { key: 'institutions', label: 'Institutions', sourceType: 'Institution' },
  { key: 'companies',   label: 'Companies',    sourceType: 'Payee' },
]

const BADGE_VARIANT: Record<string, 'blue' | 'green' | 'yellow' | 'gray'> = {
  Security: 'blue',
  Institution: 'green',
  Payee: 'yellow',
}

function fmtWhen(item: NewsItem): string {
  const iso = item.published_at ?? item.fetched_at
  if (!iso) return '—'
  const time = new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  return `${fmtDate(iso)} · ${time}`
}

export function NewsList({ items, onOpen }: { items: NewsItem[]; onOpen: (item: NewsItem) => void }) {
  return (
    <Card className="divide-y divide-slate-100">
      {items.map((item, i) => (
        <button
          key={item.id ?? `${item.url}-${i}`}
          onClick={() => onOpen(item)}
          className="w-full text-left px-5 py-3.5 hover:bg-slate-50 transition-colors flex items-start gap-3"
        >
          <span className={`mt-1.5 h-2 w-2 rounded-full shrink-0 ${item.is_read ? 'bg-transparent' : 'bg-blue-500'}`} />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap mb-0.5">
              <Badge label={item.source_name ?? item.source_type ?? 'Web'} variant={BADGE_VARIANT[item.source_type ?? ''] ?? 'gray'} />
              {item.ticker && <span className="text-xs text-slate-400">{item.ticker}</span>}
              <span className="text-xs text-slate-400">{fmtWhen(item)}</span>
              {item.publisher && <span className="text-xs text-slate-400">· {item.publisher}</span>}
            </div>
            <p className={`text-sm ${item.is_read ? 'text-slate-600' : 'text-slate-900 font-medium'}`}>{item.title}</p>
            {item.summary && <p className="text-xs text-slate-500 mt-1 line-clamp-2">{item.summary}</p>}
          </div>
          <ExternalLink size={14} className="text-slate-300 shrink-0 mt-1" />
        </button>
      ))}
    </Card>
  )
}

export default function News() {
  const qc = useQueryClient()
  const [filter, setFilter] = usePersist('news-filter', 'all')
  const [refreshing, setRefreshing] = useState(false)
  const [searchInput, setSearchInput] = useState('')
  const [searchQuery, setSearchQuery] = useState<string | null>(null)

  const activeFilter = FILTERS.find(f => f.key === filter) ?? FILTERS[0]

  const { data: items = [], isLoading } = useQuery<NewsItem[]>({
    queryKey: ['news', activeFilter.sourceType ?? 'all'],
    queryFn: () => getNews({ source_type: activeFilter.sourceType, limit: 200 }),
    refetchInterval: refreshing ? 5000 : false,
    enabled: !searchQuery,
  })

  const { data: searchResults = [], isLoading: searchLoading, isError: searchFailed } = useQuery<NewsItem[]>({
    queryKey: ['news-search', searchQuery],
    queryFn: () => searchNews(searchQuery as string),
    enabled: !!searchQuery,
  })

  const handleRefresh = async () => {
    setRefreshing(true)
    try {
      await generateNews()
    } finally {
      // The fetch runs in the background — poll a few times, then stop.
      setTimeout(() => setRefreshing(false), 30_000)
    }
  }

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const q = searchInput.trim()
    if (q) setSearchQuery(q)
  }

  const clearSearch = () => {
    setSearchQuery(null)
    setSearchInput('')
  }

  const handleOpen = (item: NewsItem) => {
    if (item.id != null && !item.is_read) {
      markNewsRead(item.id, true).then(() => qc.invalidateQueries({ queryKey: ['news'] }))
    }
    window.open(item.url, '_blank', 'noopener,noreferrer')
  }

  const unreadCount = items.filter(i => !i.is_read).length

  return (
    <div>
      <PageHeader
        title="News"
        subtitle="Institutions, companies, and securities relevant to your finances"
        actions={
          <>
            <form onSubmit={handleSearchSubmit} className="flex items-center gap-1.5">
              <Input
                value={searchInput}
                onChange={e => setSearchInput(e.target.value)}
                placeholder="Search any security, institution, company…"
                className="w-64"
              />
              <Button type="submit" size="sm" variant="secondary" disabled={!searchInput.trim()}>
                <Search size={13} /> Search
              </Button>
            </form>
            <Button size="sm" variant="secondary" onClick={handleRefresh} disabled={refreshing}>
              <RefreshCw size={13} className={refreshing ? 'animate-spin' : ''} /> {refreshing ? 'Refreshing…' : 'Refresh'}
            </Button>
          </>
        }
      />
      <div className="p-4 sm:p-6 space-y-4">
        {searchQuery ? (
          <>
            <div className="flex items-center gap-2 flex-wrap">
              <Badge label={`Search: "${searchQuery}"`} variant="blue" />
              <Button size="sm" variant="ghost" onClick={clearSearch}><X size={13} /> Clear</Button>
            </div>
            {searchLoading ? (
              <div className="flex justify-center py-16"><Spinner /></div>
            ) : searchFailed ? (
              <Card><p className="text-sm text-red-600 px-5 py-8 text-center">Search failed — try again.</p></Card>
            ) : searchResults.length === 0 ? (
              <Card><p className="text-sm text-slate-400 px-5 py-8 text-center">No news found for "{searchQuery}".</p></Card>
            ) : (
              <NewsList items={searchResults} onOpen={handleOpen} />
            )}
          </>
        ) : (
          <>
            <div className="flex items-center gap-2 flex-wrap">
              {FILTERS.map(f => (
                <button
                  key={f.key}
                  onClick={() => setFilter(f.key)}
                  className={`px-3 py-1.5 text-sm font-medium rounded-full border transition-colors ${
                    filter === f.key
                      ? 'bg-blue-600 text-white border-blue-600'
                      : 'bg-white text-slate-600 border-slate-200 hover:bg-slate-50'
                  }`}
                >
                  {f.label}
                </button>
              ))}
              {unreadCount > 0 && <Badge label={`${unreadCount} unread`} variant="blue" />}
            </div>

            {isLoading ? (
              <div className="flex justify-center py-16"><Spinner /></div>
            ) : items.length === 0 ? (
              <Card>
                <div className="flex flex-col items-center gap-2 py-16 text-slate-400">
                  <Newspaper size={28} />
                  <p className="text-sm">No news yet. Click Refresh to fetch the latest.</p>
                </div>
              </Card>
            ) : (
              <NewsList items={items} onOpen={handleOpen} />
            )}
          </>
        )}

        <Tooltip text="Securities/watchlist news comes from Yahoo Finance. Institution and company news, and search results for anything not already tracked, come from a DuckDuckGo search on the name, so they can be noisier.">
          <p className="text-xs text-slate-400 w-fit">About this page's sources</p>
        </Tooltip>
      </div>
    </div>
  )
}
