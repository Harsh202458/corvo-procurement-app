import { useEffect, useMemo, useState } from 'react'
import { download, get } from '../api'
import { Alert, Badge, Button, Card, Field, Spinner, cx, inputCls } from '../ui'
import { PreferredTable } from './Workspace'

const today = () => new Date().toISOString().slice(0, 10)

export default function Suppliers() {
  const [asOf, setAsOf] = useState(today())
  const [data, setData] = useState<any>(null)
  const [q, setQ] = useState('')
  const [sel, setSel] = useState<string | null>(null)
  const [one, setOne] = useState<any>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    setLoading(true)
    get('/api/preferred-suppliers', { as_of: asOf })
      .then((d) => {
        setData(d)
        setError('')
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [asOf])
  useEffect(() => {
    if (sel) get(`/api/preferred-suppliers/${sel}`, { as_of: asOf }).then(setOne).catch((e) => setError(e.message))
  }, [sel, asOf])

  const items = useMemo(
    () => (data?.items || []).filter((i: any) => !q || `${i.item_code} ${i.description} ${i.category}`.toLowerCase().includes(q.toLowerCase())),
    [data, q],
  )

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <Field label="As of date">
          <input type="date" className={inputCls} value={asOf} onChange={(e) => e.target.value && setAsOf(e.target.value)} />
        </Field>
        <Field label="Find item">
          <input className={inputCls} value={q} onChange={(e) => setQ(e.target.value)} placeholder="code, description, category" />
        </Field>
        {loading && <Spinner />}
        <div className="ml-auto">
          <Button kind="primary" onClick={() => download('/api/preferred-suppliers/export.csv', { as_of: asOf })}>
            Export top 3 as CSV (harsh__preferred.csv)
          </Button>
        </div>
      </div>
      {error && <Alert>{error}</Alert>}
      {data && (
        <div className="text-xs text-slate-500">
          Ranking under policy v{data.policy_version}: score = {Object.entries(data.weights).map(([k, w]) => `${w} × ${k}`).join(' + ')} (each signal scaled 0 to 1).
        </div>
      )}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)]">
        <Card title={`Items (${items.length})`}>
          <ul className="max-h-[70vh] divide-y divide-slate-100 overflow-y-auto">
            {items.map((i: any) => (
              <li key={i.item_code}>
                <button onClick={() => setSel(i.item_code)} className={cx('w-full px-2 py-2 text-left hover:bg-slate-50', sel === i.item_code && 'bg-indigo-50')}>
                  <div className="text-sm font-medium">
                    {i.item_code} {i.description}
                  </div>
                  <div className="mt-0.5 flex flex-wrap gap-1 text-xs text-slate-500">
                    {i.suppliers.length ? (
                      i.suppliers.map((s: any) => (
                        <Badge key={s.supplier_id} tone={s.rank === 1 ? 'green' : 'slate'}>
                          {s.rank}. {s.supplier_id}
                        </Badge>
                      ))
                    ) : (
                      <span>no eligible supplier</span>
                    )}
                    {i.excluded > 0 && <Badge tone="red">{i.excluded} excluded</Badge>}
                  </div>
                </button>
              </li>
            ))}
          </ul>
        </Card>
        <div>
          {one ? (
            <Card title={`${one.item_code} ${one.description} · ${one.category}`}>
              {one.suppliers.length ? <PreferredTable rows={one.suppliers} /> : <div className="text-sm text-slate-500">No eligible supplier on the list.</div>}
              <ul className="mt-3 space-y-0.5 text-xs text-slate-600">
                {one.suppliers.map((s: any) => (
                  <li key={s.supplier_id}>• {s.why}</li>
                ))}
              </ul>
              {one.excluded.length > 0 && (
                <div className="mt-3">
                  <Alert tone="amber">Excluded (never get a PO or a suggestion, 5.7): {one.excluded.map((e: any) => `${e.supplier_id} (${e.reason})`).join(', ')}</Alert>
                </div>
              )}
              {one.category_top_up.length > 0 && (
                <div className="mt-3 text-xs text-slate-600">
                  Category top-up for sourcing (6.4): {one.category_top_up.map((s: any) => `${s.supplier_id} (${s.on_time_pct}%, ${s.quality_rating})`).join(', ')}
                </div>
              )}
              <div className="mt-3 text-xs text-slate-500">{one.method.explanation}</div>
            </Card>
          ) : (
            <Card>
              <div className="text-sm text-slate-500">Select an item to see why each supplier is ranked where it is.</div>
            </Card>
          )}
        </div>
      </div>
    </div>
  )
}
