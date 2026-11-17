import { useEffect, useState } from 'react'

export default function App() {
  const [health, setHealth] = useState<string>('checking…')
  useEffect(() => {
    fetch('/api/health')
      .then((r) => r.json())
      .then((d) => setHealth(`API ok · LLM: ${d.llm_provider}`))
      .catch(() => setHealth('API unreachable'))
  }, [])
  return (
    <main className="min-h-screen bg-slate-50 text-slate-900 p-8">
      <h1 className="text-2xl font-semibold">Corvo PR-to-PO</h1>
      <p className="mt-2 text-slate-600">Policy-driven purchase requests.</p>
      <p className="mt-6 text-sm">{health}</p>
    </main>
  )
}
