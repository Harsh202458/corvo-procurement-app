import { useEffect, useRef, useState } from 'react'
import { post } from '../api'
import { Alert, Button, Card, RouteBadge, inr } from '../ui'

interface Msg {
  role: 'user' | 'assistant'
  text: string
  options?: string[]
  decision?: any
}

const HELLO: Msg = {
  role: 'assistant',
  text: 'Hi! Tell me what you need, in your own words. For example: "need 200 nitrile gloves for Chakan by next Friday".',
}

export default function Chat() {
  const [msgs, setMsgs] = useState<Msg[]>([HELLO])
  const [sessionId, setSessionId] = useState<number | null>(null)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' }) // newer browsers return a Promise here: never return it to React
  }, [msgs])

  async function send(message: string) {
    const m = message.trim()
    if (!m || busy) return
    if (m === 'Raise another request') {
      setMsgs([HELLO])
      setSessionId(null)
      return
    }
    setError('')
    setMsgs((x) => [...x, { role: 'user', text: m }])
    setText('')
    setBusy(true)
    try {
      const r = await post('/api/chat', { message: m.replace(/^\d+\.\s.*$/, (s) => s.split('.')[0]), session_id: sessionId })
      setSessionId(r.session_id)
      setMsgs((x) => [...x, { role: 'assistant', text: r.text, options: r.options, decision: r.decision }])
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const last = msgs[msgs.length - 1]

  return (
    <div className="mx-auto max-w-3xl">
      <Card title="New purchase request" actions={<span className="text-xs text-slate-500">Chat with the procurement assistant</span>}>
        <div className="flex h-[60vh] flex-col">
          <div className="flex-1 space-y-3 overflow-y-auto pr-1">
            {msgs.map((m, i) => (
              <div key={i} className={m.role === 'user' ? 'flex justify-end' : 'flex justify-start'}>
                <div
                  className={
                    m.role === 'user'
                      ? 'max-w-[80%] rounded-2xl rounded-br-sm bg-indigo-600 px-3.5 py-2 text-sm text-white'
                      : 'max-w-[85%] rounded-2xl rounded-bl-sm bg-slate-100 px-3.5 py-2 text-sm text-slate-800'
                  }
                >
                  <div className="whitespace-pre-wrap">{m.text}</div>
                  {m.decision && <DecisionCard d={m.decision} />}
                </div>
              </div>
            ))}
            {busy && (
              <div className="flex justify-start">
                <div className="rounded-2xl bg-slate-100 px-3.5 py-2 text-sm text-slate-500">…</div>
              </div>
            )}
            <div ref={endRef} />
          </div>
          {last.role === 'assistant' && last.options && last.options.length > 0 && !busy && (
            <div className="mt-3 flex flex-wrap gap-2">
              {last.options.map((o) => (
                <button
                  key={o}
                  onClick={() => send(o)}
                  className="rounded-full border border-indigo-200 bg-indigo-50 px-3 py-1 text-xs font-medium text-indigo-700 hover:bg-indigo-100"
                >
                  {o}
                </button>
              ))}
            </div>
          )}
          {error && (
            <div className="mt-3">
              <Alert>{error}</Alert>
            </div>
          )}
          <form
            className="mt-3 flex gap-2"
            onSubmit={(e) => {
              e.preventDefault()
              send(text)
            }}
          >
            <input
              autoFocus
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="Type your request or answer…"
              className="flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-200"
            />
            <Button kind="primary" type="submit" disabled={busy || !text.trim()}>
              Send
            </Button>
          </form>
        </div>
      </Card>
    </div>
  )
}

function DecisionCard({ d }: { d: any }) {
  return (
    <div className="mt-2 rounded-md border border-slate-200 bg-white p-2.5 text-xs text-slate-700">
      <div className="mb-1 flex items-center gap-2">
        <RouteBadge route={d.route} />
        <span className="font-medium">{d.resolved_item_code}</span>
        <span className="ml-auto">₹{inr(d.group_value_inr)}</span>
      </div>
      {d.supplier_id && (
        <div>
          Supplier <b>{d.supplier_id}</b> at ₹{inr(d.unit_price, 2)}
        </div>
      )}
      {d.suggested_supplier_ids && <div>Suggested: {d.suggested_supplier_ids.replaceAll(';', ', ')}</div>}
      <div className="mt-1 text-slate-500">{String(d.reason).split(' | ')[0]}</div>
    </div>
  )
}
