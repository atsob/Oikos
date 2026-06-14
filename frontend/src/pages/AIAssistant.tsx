import { useState, useRef, useEffect } from 'react'
import { useMutation } from '@tanstack/react-query'
import { askAI } from '@/lib/api'
import { PageHeader, Card, Button, Input, Spinner } from '@/components/ui'
import { Send, BrainCircuit } from 'lucide-react'

interface Message { role: 'user' | 'assistant'; text: string }

const STARTERS = [
  'What was my total spending last month?',
  'Which categories did I overspend in Q1 2025?',
  'Show me my largest expenses this year',
  'What is my current portfolio value?',
  'How has my net worth changed over the last 12 months?',
]

export default function AIAssistant() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)

  const askMut = useMutation({
    mutationFn: askAI,
    onSuccess: data => {
      setMessages(m => [...m, { role: 'assistant', text: data.answer ?? data.result ?? JSON.stringify(data) }])
    },
    onError: () => {
      setMessages(m => [...m, { role: 'assistant', text: 'Sorry, I encountered an error. Please try again.' }])
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

  return (
    <div className="flex flex-col h-full">
      <PageHeader title="AI Assistant" subtitle="Ask questions about your finances in natural language" />

      <div className="flex-1 flex flex-col p-6 gap-4 overflow-hidden">
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
                      onClick={() => send(s)}
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
                <div className={`max-w-2xl rounded-2xl px-4 py-3 text-sm whitespace-pre-wrap ${
                  m.role === 'user'
                    ? 'bg-blue-600 text-white rounded-br-sm'
                    : 'bg-white border border-slate-200 text-slate-800 rounded-bl-sm shadow-sm'
                }`}>
                  {m.text}
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
