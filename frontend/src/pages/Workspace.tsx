import { useEffect, useMemo, useState } from 'react'
import { download, get, post } from '../api'
import { Alert, Badge, Button, Card, Empty, Field, ReasonList, RouteBadge, Spinner, StatusBadge, cx, inputCls, inr } from '../ui'

const PO_ROUTES = ['CONTRACT_PO', 'EMERGENCY_PO', 'SPOT_PO']
const ALL_ROUTES = [...PO_ROUTES, 'RFQ', 'RFP', 'CAPEX_SOURCING', 'NEW_ITEM_SOURCING', 'NOT_PROCESSED']

export default function Workspace() {
  const [runs, setRuns] = useState<any[]>([])
  const [runId, setRunId] = useState<number | null>(null)
  const [reqs, setReqs] = useState<any[]>([])
  const [sel, setSel] = useState<number | null>(null)
  const [detail, setDetail] = useState<any>(null)
  const [filter, setFilter] = useState({ route: '', status: 'pending', q: '' })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    get('/api/runs')
      .then((r) => {
        setRuns(r)
        if (r.length) setRunId(r[0].id)
      })
      .catch((e) => setError(e.message))
  }, [])

  async function loadReqs(id: number, keep?: number | null) {
    setLoading(true)
    try {
      const r = await get(`/api/runs/${id}/requirements`)
      setReqs(r)
      setSel(keep ?? r.find((x: any) => x.status === 'pending')?.id ?? r[0]?.id ?? null)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => {
    if (runId) loadReqs(runId)
  }, [runId])

  useEffect(() => {
    if (!sel) {
      setDetail(null)
      return
    }
    get(`/api/requirements/${sel}`).then(setDetail).catch((e) => setError(e.message))
  }, [sel])

  const shown = useMemo(() => {
    const q = filter.q.toLowerCase()
    return reqs.filter(
      (r) =>
        (!filter.route || r.route === filter.route) &&
        (!filter.status || r.status === filter.status) &&
        (!q ||
          [r.group_id, r.item_code, r.supplier_id, r.po_id, ...r.lines.map((l: any) => `${l.pr_no} ${l.description}`)]
            .join(' ')
            .toLowerCase()
            .includes(q)),
    )
  }, [reqs, filter])

  const counts = useMemo(() => {
    const c: Record<string, number> = { pending: 0, approved: 0, overridden: 0 }
    reqs.forEach((r) => (c[r.status] = (c[r.status] || 0) + 1))
    return c
  }, [reqs])

  async function afterAction() {
    if (!runId) return
    const r = await get(`/api/runs/${runId}/requirements`)
    setReqs(r)
    const idx = shown.findIndex((x) => x.id === sel)
    const nextPending = [...shown.slice(idx + 1), ...shown.slice(0, idx)].find((x) => r.find((y: any) => y.id === x.id)?.status === 'pending')
    setSel(nextPending?.id ?? sel)
    if (nextPending?.id === sel || !nextPending) setDetail(await get(`/api/requirements/${sel}`))
  }

  const run = runs.find((r) => r.id === runId)

  if (!runs.length && !error) return <Empty>No processed batches yet. Upload a PR batch (Data setup or Upload batch) or raise a request by chat.</Empty>

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <select className={cx(inputCls, 'w-auto')} value={runId ?? ''} onChange={(e) => setRunId(Number(e.target.value))}>
          {runs.map((r) => (
            <option key={r.id} value={r.id}>
              Run #{r.id}: {r.batch_name} ({r.summary.lines} lines, v{r.summary.policy_versions?.map((v: any) => v.version).join('/')})
            </option>
          ))}
        </select>
        <span className="text-sm text-slate-600">
          <b>{counts.pending || 0}</b> to review · {counts.approved || 0} approved · {counts.overridden || 0} overridden
        </span>
        <div className="ml-auto">
          {run && <Button onClick={() => download(`/api/runs/${run.id}/export.csv`)}>Export decisions CSV</Button>}
        </div>
      </div>
      {error && <Alert>{error}</Alert>}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]">
        <Card
          title={`Requirements (${shown.length})`}
          actions={loading ? <Spinner /> : null}
          className="min-w-0"
        >
          <div className="mb-3 grid grid-cols-3 gap-2">
            <select className={inputCls} value={filter.status} onChange={(e) => setFilter({ ...filter, status: e.target.value })}>
              <option value="">All statuses</option>
              <option value="pending">To review</option>
              <option value="approved">Approved</option>
              <option value="overridden">Overridden</option>
            </select>
            <select className={inputCls} value={filter.route} onChange={(e) => setFilter({ ...filter, route: e.target.value })}>
              <option value="">All routes</option>
              {ALL_ROUTES.map((r) => (
                <option key={r}>{r}</option>
              ))}
            </select>
            <input className={inputCls} placeholder="Search PR, item, supplier" value={filter.q} onChange={(e) => setFilter({ ...filter, q: e.target.value })} />
          </div>
          <div className="max-h-[68vh] overflow-y-auto">
            {shown.length === 0 ? (
              <Empty>{filter.status === 'pending' ? 'Nothing left to review in this run.' : 'No requirements match.'}</Empty>
            ) : (
              <ul className="divide-y divide-slate-100">
                {shown.map((r) => (
                  <li key={r.id}>
                    <button
                      onClick={() => setSel(r.id)}
                      className={cx('flex w-full items-start gap-3 px-2 py-2 text-left hover:bg-slate-50', sel === r.id && 'bg-indigo-50 hover:bg-indigo-50')}
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <RouteBadge route={r.route} />
                          <span className="truncate text-sm font-medium text-slate-800">
                            {r.item_code} {r.lines[0]?.description}
                          </span>
                        </div>
                        <div className="mt-0.5 text-xs text-slate-500">
                          {r.group_id} · {r.plant} · {r.lines.length} line{r.lines.length > 1 ? 's' : ''} · {r.lines.map((l: any) => l.pr_no).join(', ')}
                        </div>
                      </div>
                      <div className="text-right">
                        <div className="text-sm font-medium">₹{inr(r.value_inr)}</div>
                        <div className="mt-0.5">
                          <StatusBadge status={r.status} />
                        </div>
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </Card>

        <div className="min-w-0">{detail ? <Detail d={detail} onDone={afterAction} /> : <Empty>Select a requirement.</Empty>}</div>
      </div>
    </div>
  )
}

function Detail({ d, onDone }: { d: any; onDone: () => void }) {
  const [mode, setMode] = useState<'view' | 'override'>('view')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [ov, setOv] = useState({ route: '', supplier_id: '', unit_price: '', comment: '' })

  useEffect(() => {
    setMode('view')
    setError('')
    setOv({ route: d.route === 'NOT_PROCESSED' ? 'RFQ' : d.route, supplier_id: d.supplier_id || '', unit_price: d.unit_price ? String(d.unit_price) : '', comment: '' })
  }, [d.id])

  const isPO = d.decision === 'PO'
  const ev = d.evidence || {}
  const pending = d.status === 'pending'

  async function approve() {
    setBusy(true)
    setError('')
    try {
      await post(`/api/requirements/${d.id}/approve`)
      onDone()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  async function override() {
    setBusy(true)
    setError('')
    try {
      await post(`/api/requirements/${d.id}/override`, {
        route: ov.route,
        supplier_id: ov.supplier_id || null,
        unit_price: ov.unit_price ? Number(ov.unit_price) : null,
        comment: ov.comment,
      })
      onDone()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const actionLabel = isPO ? `Approve & create PO${d.po_id ? ` (${d.po_id})` : ''}` : d.decision === 'SOURCING_REQUEST' ? `Approve & create ${d.route.replaceAll('_', ' ')}` : ''

  return (
    <div className="space-y-4">
      <Card
        title={
          <span className="flex items-center gap-2">
            <RouteBadge route={d.route} /> {d.group_id} · {d.item_code}
          </span>
        }
        actions={<StatusBadge status={d.status} />}
      >
        <div className="grid gap-4 sm:grid-cols-3">
          <Stat label="Requirement value" value={`₹${inr(d.value_inr)}`} />
          {isPO ? (
            <>
              <Stat label="Supplier" value={d.supplier_id} />
              <Stat label="Unit price" value={`₹${inr(d.unit_price, 2)}`} />
            </>
          ) : (
            <div className="sm:col-span-2">
              <div className="text-xs text-slate-500">Suggested suppliers</div>
              <div className="mt-1 flex flex-wrap gap-1">
                {(d.suggested_supplier_ids || []).length ? (
                  d.suggested_supplier_ids.map((s: string, i: number) => (
                    <Badge key={s} tone="indigo">
                      {i + 1}. {s}
                    </Badge>
                  ))
                ) : (
                  <span className="text-sm text-slate-500">{d.route === 'NEW_ITEM_SOURCING' ? 'New item: the buyer finds suppliers' : 'none'}</span>
                )}
              </div>
            </div>
          )}
        </div>
        <div className="mt-3 flex flex-wrap gap-2 text-xs text-slate-600">
          <Badge>Policy v{d.policy_version}</Badge>
          <Badge>Clause §{d.clause}</Badge>
          <Badge>{d.plant}</Badge>
          <Badge>Requirement date {String(d.requirement_date).slice(0, 10)}</Badge>
          {d.is_emergency && <Badge tone="red">Emergency</Badge>}
          {d.po_id && <Badge tone="blue">{d.po_id}{d.po_shared_with?.length ? ` with ${d.po_shared_with.join(', ')}` : ''}</Badge>}
          {ev.contract && <Badge tone="green">Contract {String(ev.contract).split(' ')[0]}</Badge>}
        </div>

        {d.override && (
          <div className="mt-3">
            <Alert tone="amber">
              Overridden to <b>{d.override.route}</b>
              {d.override.supplier_id && ` (${d.override.supplier_id} at ₹${inr(d.override.unit_price, 2)})`} by {d.override.by}: “{d.override.comment}”. The original decision above is
              kept.
            </Alert>
          </div>
        )}
        {d.documents?.length > 0 && (
          <div className="mt-3">
            <Alert tone="green">
              Created {d.documents.map((x: any) => `${x.type} ${x.number}`).join(', ')}
            </Alert>
          </div>
        )}
        {error && (
          <div className="mt-3">
            <Alert>{error}</Alert>
          </div>
        )}

        {pending && mode === 'view' && (
          <div className="mt-4 flex flex-wrap gap-2">
            {actionLabel && (
              <Button kind="primary" onClick={approve} disabled={busy}>
                {busy ? <Spinner /> : actionLabel}
              </Button>
            )}
            <Button kind={actionLabel ? 'ghost' : 'primary'} onClick={() => setMode('override')}>
              Override…
            </Button>
          </div>
        )}
        {pending && mode === 'override' && (
          <div className="mt-4 space-y-3 rounded-md border border-amber-200 bg-amber-50/50 p-3">
            <div className="grid gap-3 sm:grid-cols-3">
              <Field label="New route">
                <select className={inputCls} value={ov.route} onChange={(e) => setOv({ ...ov, route: e.target.value })}>
                  {ALL_ROUTES.map((r) => (
                    <option key={r}>{r}</option>
                  ))}
                </select>
              </Field>
              {PO_ROUTES.includes(ov.route) && (
                <>
                  <Field label="Supplier">
                    <input className={inputCls} value={ov.supplier_id} onChange={(e) => setOv({ ...ov, supplier_id: e.target.value.toUpperCase() })} placeholder="S0xx" />
                  </Field>
                  <Field label="Unit price (INR)">
                    <input className={inputCls} value={ov.unit_price} onChange={(e) => setOv({ ...ov, unit_price: e.target.value })} inputMode="decimal" />
                  </Field>
                </>
              )}
            </div>
            <Field label="Reason (required, kept with the original decision)">
              <textarea className={inputCls} rows={2} value={ov.comment} onChange={(e) => setOv({ ...ov, comment: e.target.value })} />
            </Field>
            <div className="flex gap-2">
              <Button kind="primary" onClick={override} disabled={busy || ov.comment.trim().length < 10}>
                Override & create document
              </Button>
              <Button kind="ghost" onClick={() => setMode('view')}>
                Cancel
              </Button>
            </div>
          </div>
        )}
      </Card>

      <Card title="Why">
        <ReasonList reason={d.reason} />
        {d.suggestion_notes?.length > 0 && (
          <div className="mt-3">
            <div className="text-xs font-medium text-slate-500">How suppliers were suggested (6.4)</div>
            <ul className="mt-1 space-y-0.5 text-xs text-slate-600">
              {d.suggestion_notes.map((n: string, i: number) => (
                <li key={i}>• {n}</li>
              ))}
            </ul>
          </div>
        )}
      </Card>

      <Card title={`PR lines (${d.lines.length})`}>
        <table className="w-full text-xs">
          <thead className="text-left text-slate-500">
            <tr>
              <th className="py-1">PR / line</th>
              <th>Created</th>
              <th>Requester</th>
              <th>Description</th>
              <th className="text-right">Qty</th>
              <th>Need by</th>
              <th>Match</th>
            </tr>
          </thead>
          <tbody>
            {d.lines.map((l: any) => (
              <tr key={`${l.pr_no}/${l.pr_line}`} className="border-t border-slate-100">
                <td className="py-1.5 font-medium">
                  {l.pr_no}/{l.pr_line}
                </td>
                <td>{String(l.created_at).replace('T', ' ').slice(0, 16)}</td>
                <td>
                  {l.requester}
                  <div className="text-slate-400">{l.department}</div>
                </td>
                <td>
                  {l.description}
                  {l.urgency === 'Emergency' && <span className="ml-1 text-rose-600">Emergency</span>}
                </td>
                <td className="text-right">
                  {inr(l.quantity)} {l.uom}
                </td>
                <td>{l.need_by_date}</td>
                <td>{l.match_method === 'code' ? 'code' : l.match_method === 'description' ? `text (${l.match_score})` : 'new'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      {d.preferred?.length > 0 && (
        <Card title="Preferred supplier list on the requirement date (policy 6)">
          <PreferredTable rows={d.preferred} />
        </Card>
      )}

      {d.audit?.length > 0 && (
        <Card title="History">
          <ul className="space-y-1 text-xs text-slate-600">
            {d.audit.map((a: any, i: number) => (
              <li key={i}>
                <b>{new Date(a.at).toLocaleString('en-IN')}</b> · {a.actor} · {a.action.replace('requirement.', '')}
                {a.comment && `: “${a.comment}”`}
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs text-slate-500">{label}</div>
      <div className="mt-0.5 text-lg font-semibold text-slate-900">{value || '–'}</div>
    </div>
  )
}

export function PreferredTable({ rows }: { rows: any[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead className="text-left text-slate-500">
          <tr>
            <th className="py-1">#</th>
            <th>Supplier</th>
            <th className="text-right">Score</th>
            <th className="text-right">POs (548 d)</th>
            <th>Last PO</th>
            <th className="text-right">Recency</th>
            <th>Contract</th>
            <th className="text-right">On-time</th>
            <th className="text-right">Quality</th>
            <th className="text-right">Ref. price</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r: any, i: number) => (
            <tr key={r.supplier_id} className="border-t border-slate-100" title={r.why}>
              <td className="py-1.5">{r.rank ?? i + 1}</td>
              <td className="font-medium">
                {r.supplier_id} <span className="font-normal text-slate-500">{r.name}</span>
              </td>
              <td className="text-right font-semibold">{Number(r.score).toFixed(3)}</td>
              <td className="text-right">{r.po_count}</td>
              <td>{r.last_po_date || '–'}</td>
              <td className="text-right">{r.recency}</td>
              <td>{r.has_contract ? 'yes' : 'no'}</td>
              <td className="text-right">{r.on_time_pct}%</td>
              <td className="text-right">{r.quality_rating}</td>
              <td className="text-right">
                {r.reference_price != null ? `₹${inr(r.reference_price, 2)}` : '–'}
                <div className="text-[10px] text-slate-400">{r.reference_source}</div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
