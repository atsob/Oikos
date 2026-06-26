import { useState, useRef, useEffect } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { PageHeader, Card, Button, Input, Spinner } from '@/components/ui'
import { Send, BrainCircuit, RefreshCw, CheckCircle2, ChevronDown, ChevronRight } from 'lucide-react'

interface Step { thought: string; tool: string; tool_input: string; observation: string }
interface Message { role: 'user' | 'assistant'; text: string; steps?: Step[] }

const STARTERS = [
  'What was my total spending last month?',
  'Which categories did I overspend in Q1 2025?',
  'Show me my largest expenses this year',
  'What is my current portfolio value?',
  'How has my net worth changed over the last 12 months?',
]

function ReasoningSteps({ steps }: { steps: Step[] }) {
  const [open, setOpen] = useState(false)
  if (!steps.length) return null
  return (
    <div className="mt-2 border border-slate-200 rounded-lg text-xs">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center gap-1.5 px-3 py-2 text-slate-500 hover:text-slate-700 hover:bg-slate-50 rounded-lg transition-colors"
      >
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        <span className="font-medium">{steps.length} reasoning step{steps.length > 1 ? 's' : ''}</span>
      </button>
      {open && (
        <div className="border-t border-slate-100 divide-y divide-slate-100">
          {steps.map((s, i) => (
            <div key={i} className="px-3 py-2 space-y-1.5">
              {s.thought && (
                <p className="text-slate-600 whitespace-pre-wrap"><span className="font-semibold text-slate-400">Thought:</span> {s.thought.replace(/^Thought:\s*/i, '')}</p>
              )}
              {s.tool && (
                <p className="text-indigo-600"><span className="font-semibold">Tool:</span> {s.tool}({s.tool_input})</p>
              )}
              {s.observation && (
                <pre className="bg-slate-50 rounded p-2 overflow-x-auto text-slate-700 whitespace-pre-wrap break-all">{s.observation}</pre>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default function AIAssistant() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)
  const qc = useQueryClient()

  const { data: embStatus } = useQuery({
    queryKey: ['embedding-status'],
    queryFn: () => api.get('/ai/embedding-status').then(r => r.data as { total: number; indexed: number }),
    staleTime: 30_000,
  })

  const embedMut = useMutation({
    mutationFn: () => api.post('/ai/update-embeddings').then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['embedding-status'] }),
  })

  const askMut = useMutation({
    mutationFn: (q: string) => api.post('/ai/ask', { question: q }).then(r => r.data as { answer: string; steps?: Step[] }),
    onSuccess: data => {
      setMessages(m => [...m, { role: 'assistant', text: data.answer ?? JSON.stringify(data), steps: data.steps }])
    },
    onError: (e: unknown) => {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setMessages(m => [...m, { role: 'assistant', text: detail ?? 'Sorry, I encountered an error. Please try again.' }])
    },
  })

  const send = (q: string) => {
    if (!q.trim()) return
    setMessages(m => [...m, { role: 'user', text: q }])
    setInput('')
    askMut.mutate(q)
  }

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, askMut.isPending])

  const allIndexed = embStatus && embStatus.indexed >= embStatus.total && embStatus.total > 0

  return (
    <div className="flex flex-col h-full">
      <PageHeader title="AI Assistant" subtitle="Ask questions about your finances in natural language" />

      <div className="flex-1 flex flex-col p-6 gap-4 overflow-hidden">
        {/* Embedding status bar */}
        <div className="flex items-center gap-3">
          {embStatus ? (
            allIndexed ? (
              <div className="flex items-center gap-2 text-sm text-green-700 bg-green-50 border border-green-200 rounded-lg px-3 py-2">
                <CheckCircle2 size={15} />
                Semantic search ready — all {embStatus.indexed.toLocaleString()} transactions indexed.
              </div>
            ) : (
              <div className="text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
                {embStatus.indexed.toLocaleString()} / {embStatus.total.toLocaleString()} transactions indexed.
              </div>
            )
          ) : null}
          <Button
            variant="outline"
            size="sm"
            onClick={() => embedMut.mutate()}
            disabled={embedMut.isPending}
            className="flex items-center gap-1.5"
          >
            {embedMut.isPending ? <Spinner size={14} /> : <RefreshCw size={14} />}
            Update Embeddings
          </Button>
        </div>

        <Card className="flex-1 flex flex-col overflow-hidden">
          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            {messages.length === 0 && (
              <div className="flex flex-col items-center justify-center h-full gap-4 text-slate-400">
                <BrainCircuit size={48} className="opacity-30" />
                <p className="text-sm font-medium">Ask anything about your finances</p>
                <div className="grid grid-cols-1 gap-2 w-full max-w-lg mt-2">
                  {STARTERS.map(s => (
                    <button
                      key={s}
                      onClick={() => setInput(s)}
                      className="text-left text-sm text-slate-600 bg-slate-50 hover:bg-slate-100 rounded-lg px-4 py-2.5 transition-colors border border-slate-200"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((m, i) => (
              <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                <div className="max-w-2xl">
                  <div className={`rounded-2xl px-4 py-3 text-sm whitespace-pre-wrap ${
                    m.role === 'user'
                      ? 'bg-blue-600 text-white rounded-br-sm'
                      : 'bg-white border border-slate-200 text-slate-800 rounded-bl-sm shadow-sm'
                  }`}>
                    {m.text}
                  </div>
                  {m.role === 'assistant' && m.steps && <ReasoningSteps steps={m.steps} />}
                </div>
              </div>
            ))}

            {askMut.isPending && (
              <div className="flex justify-start">
                <div className="bg-white border border-slate-200 rounded-2xl rounded-bl-sm px-4 py-3 shadow-sm">
                  <Spinner size={18} />
                </div>
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          <div className="border-t border-slate-100 p-3">
            <div className="flex gap-2">
              <Input
                placeholder="Ask about your finances…"
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && !e.shiftKey && send(input)}
                className="flex-1"
                autoFocus
              />
              <Button onClick={() => send(input)} disabled={!input.trim() || askMut.isPending}>
                <Send size={15} />
              </Button>
            </div>
          </div>
        </Card>
      </div>
    </div>
  )
}
