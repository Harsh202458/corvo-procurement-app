import { useEffect, useState } from 'react'
import { get, post, upload } from '../api'
import { Alert, Badge, Button, Card, Empty, Field, FilePick, RouteBadge, Spinner, StatusBadge, cx, inputCls } from '../ui'

export default function Policy() {
  const [versions, setVersions] = useState<any[]>([])
  const [inForce, setInForce] = useState<number | null>(null)
  const [viewId, setViewId] = useState<number | null>(null)
  const [view, setView] = useState<any>(null)
  const [error, setError] = useState('')
  const [proposing, setProposing] = useState(false)
  const [approved, setApproved] = useState<any>(null)

  async function refresh(select?: number) {
    try {
      const r = await get('/api/policy/versions')
      setVersions(r.versions)
      setInForce(r.in_force_id)
      setViewId(select ?? viewId ?? r.in_force_id)
    } catch (e) {
      setError((e as Error).message)
    }
  }
  useEffect(() => {
    refresh()
  }, [])
  useEffect(() => {
    if (viewId) get(`/api/policy/versions/${viewId}`).then(setView).catch((e) => setError(e.message))
  }, [viewId, versions])

  const drafts = versions.filter((v) => v.status === 'draft')

  return (
    <div className="space-y-4">
      {error && <Alert>{error}</Alert>}
      {approved && (
        <div className="flex items-center justify-between rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
          <span>
            v{approved.version} is approved and applies to requirements dated from {approved.effective_from}. Existing runs keep their old decisions until you re-run the batch.
          </span>
          <Button kind="primary" onClick={() => (window.location.hash = '#/procurement/data')}>
            Go to Data & runs to re-run
          </Button>
        </div>
      )}
      {!proposing && (
        <div className="flex items-center justify-between rounded-lg border border-indigo-200 bg-indigo-50 px-4 py-3">
          <div className="text-sm text-indigo-900">
            Procurement changed the policy? Upload the amendment or describe the change. You will see exactly what changes, and what it does to the last batch, before
            anything takes effect.
          </div>
          <Button kind="primary" onClick={() => setProposing(true)}>
            Propose a policy change
          </Button>
        </div>
      )}
      {proposing && (
        <Proposer
          onClose={() => setProposing(false)}
          onSaved={(id) => {
            setProposing(false)
            refresh(id)
          }}
        />
      )}

      {drafts.length > 0 && !proposing && (
        <Card title="Drafts waiting for approval">
          {drafts.map((d) => (
            <DraftRow
              key={d.id}
              d={d}
              onDone={(id, kind) => {
                if (kind === 'approve') setApproved(d)
                refresh(id)
              }}
            />
          ))}
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-[300px_minmax(0,1fr)]">
        <Card title="Versions">
          <ul className="space-y-1">
            {versions.map((v) => (
              <li key={v.id}>
                <button
                  onClick={() => setViewId(v.id)}
                  className={cx('w-full rounded-md px-2 py-2 text-left hover:bg-slate-50', viewId === v.id && 'bg-indigo-50 hover:bg-indigo-50')}
                >
                  <div className="flex items-center gap-2">
                    <span className="font-semibold">v{v.version}</span>
                    <StatusBadge status={v.status} />
                    {v.id === inForce && <Badge tone="green">in force today</Badge>}
                  </div>
                  <div className="mt-0.5 text-xs text-slate-500">
                    effective {v.effective_from} · {v.approved_by ? `approved by ${v.approved_by}` : `by ${v.created_by}`}
                  </div>
                </button>
              </li>
            ))}
          </ul>
        </Card>
        {view ? <VersionView v={view} /> : <Empty>Select a version.</Empty>}
      </div>
    </div>
  )
}

function VersionView({ v }: { v: any }) {
  const [showJson, setShowJson] = useState(false)
  return (
    <Card
      title={`Policy v${v.version} · effective ${v.effective_from}`}
      actions={
        <Button kind="ghost" onClick={() => setShowJson(!showJson)}>
          {showJson ? 'Plain language' : 'JSON'}
        </Button>
      }
    >
      {v.changes?.length > 0 && v.parent_id && (
        <div className="mb-4">
          <div className="mb-1 text-xs font-medium text-slate-500">Changes from the previous version</div>
          <ChangeList changes={v.changes} />
        </div>
      )}
      {showJson ? (
        <pre className="max-h-[60vh] overflow-auto rounded bg-slate-900 p-3 text-xs text-slate-100">{JSON.stringify(v.document, null, 2)}</pre>
      ) : (
        <div className="space-y-0.5 text-sm text-slate-700">
          {v.summary.map((line: string, i: number) =>
            line === '' ? (
              <div key={i} className="h-2" />
            ) : line.endsWith(':') ? (
              <div key={i} className="pt-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
                {line}
              </div>
            ) : (
              <div key={i}>{line}</div>
            ),
          )}
        </div>
      )}
      <div className="mt-4 text-xs text-slate-500">
        Source: {v.source_name || '–'} · created by {v.created_by}
        {v.approved_at && ` · approved by ${v.approved_by} on ${new Date(v.approved_at).toLocaleString('en-IN')}`}
      </div>
    </Card>
  )
}

function ChangeList({ changes }: { changes: any[] }) {
  return (
    <ul className="space-y-1">
      {changes.map((c, i) => (
        <li key={i} className="flex gap-2 text-sm">
          <span className={cx('mt-1.5 h-2 w-2 shrink-0 rounded-full', c.kind.includes('added') ? 'bg-emerald-500' : c.kind.includes('removed') ? 'bg-rose-500' : 'bg-amber-500')} />
          <span className="text-slate-800">{c.text}</span>
        </li>
      ))}
    </ul>
  )
}

function DraftRow({ d, onDone }: { d: any; onDone: (id: number, kind: 'approve' | 'reject') => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [impact, setImpact] = useState<any>(null)
  useEffect(() => {
    get(`/api/policy/versions/${d.id}/impact`).then(setImpact).catch(() => setImpact(null))
  }, [d.id])
  async function act(kind: 'approve' | 'reject') {
    setBusy(true)
    try {
      await post(`/api/policy/versions/${d.id}/${kind}`, { comment: null })
      onDone(d.id, kind)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold">Draft v{d.version}</span>
        <Badge tone="indigo">effective {d.effective_from}</Badge>
        <span className="text-xs text-slate-500">from {d.source_name || 'hand edit'} by {d.created_by}</span>
        <div className="ml-auto flex gap-2">
          <Button kind="danger" onClick={() => act('reject')} disabled={busy}>
            Reject
          </Button>
          <Button kind="primary" onClick={() => act('approve')} disabled={busy}>
            Approve & activate from {d.effective_from}
          </Button>
        </div>
      </div>
      <ChangeList changes={d.changes} />
      {impact && <Impact impact={impact} />}
      {error && <Alert>{error}</Alert>}
    </div>
  )
}

function Impact({ impact }: { impact: any }) {
  if (!impact) return null
  if (impact.batch_id == null) return <Alert tone="blue">{impact.note}</Alert>
  return (
    <div className="rounded-md border border-slate-200 p-3">
      <div className="mb-2 text-sm font-medium">
        Impact on batch {impact.batch_id}: {impact.changed_count} of {impact.lines} lines change · {impact.lines_on_new_version} lines would be decided under the new
        version
      </div>
      {impact.note && (
        <div className="mb-2">
          <Alert tone="amber">{impact.note}</Alert>
        </div>
      )}
      {impact.changed?.length > 0 && (
        <div className="max-h-72 overflow-auto">
          <table className="w-full text-xs">
            <thead className="text-left text-slate-500">
              <tr>
                <th className="py-1">PR line</th>
                <th>Before</th>
                <th>After</th>
                <th>What changed</th>
              </tr>
            </thead>
            <tbody>
              {impact.changed.map((c: any) => (
                <tr key={`${c.pr_no}/${c.pr_line}`} className="border-t border-slate-100">
                  <td className="py-1">
                    {c.pr_no}/{c.pr_line}
                  </td>
                  <td>
                    <RouteBadge route={c.before.route} /> {c.before.supplier_id}
                  </td>
                  <td>
                    <RouteBadge route={c.after.route} /> {c.after.supplier_id}
                  </td>
                  <td className="text-slate-500">{c.changed.join(', ')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function Proposer({ onClose, onSaved }: { onClose: () => void; onSaved: (id: number) => void }) {
  const [mode, setMode] = useState<'doc' | 'text'>('doc')
  const [text, setText] = useState('')
  const [useAi, setUseAi] = useState(true)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [p, setP] = useState<any>(null)
  const [opsText, setOpsText] = useState('')
  const [editing, setEditing] = useState(false)

  function accept(r: any) {
    setP(r)
    setOpsText(JSON.stringify(r.operations, null, 2))
  }

  async function readDoc(f: File) {
    setBusy(`Reading ${f.name}${useAi ? ' with AI (can take up to a minute when the AI service is busy)' : ''}…`)
    setError('')
    try {
      accept(await upload('/api/policy/read-document', { file: f }, { use_ai: String(useAi) }))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy('')
    }
  }
  async function readText() {
    setBusy(useAi ? 'Reading the change with AI…' : 'Reading the change…')
    setError('')
    try {
      accept(await post('/api/policy/read-text', { text, use_ai: useAi }))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy('')
    }
  }

  function currentOps(): any[] | null {
    try {
      const ops = JSON.parse(opsText)
      if (!Array.isArray(ops)) throw new Error('operations must be a list')
      return ops
    } catch (e) {
      setError(`Operations JSON: ${(e as Error).message}`)
      return null
    }
  }

  function setMeta(key: 'version' | 'effective_from', value: string) {
    const ops = currentOps()
    if (!ops) return
    let meta = ops.find((o) => o.op === 'set_meta')
    if (!meta) {
      meta = { op: 'set_meta' }
      ops.unshift(meta)
    }
    meta[key] = value
    setOpsText(JSON.stringify(ops, null, 2))
    recheck(ops)
  }

  async function recheck(opsArg?: any[]) {
    const ops = opsArg ?? currentOps()
    if (!ops) return
    setBusy('Checking…')
    setError('')
    try {
      const pv = await post('/api/policy/preview', { base_version_id: p.base_version_id, operations: ops })
      let impact = null
      if (pv.valid) impact = await post('/api/policy/impact', { base_version_id: p.base_version_id, operations: ops })
      setP({ ...p, operations: ops, valid: pv.valid, errors: pv.errors, changes: pv.changes, document: pv.document, impact })
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy('')
    }
  }

  async function save() {
    const ops = currentOps()
    if (!ops) return
    setBusy('Saving draft…')
    try {
      const d = await post('/api/policy/drafts', { base_version_id: p.base_version_id, operations: ops, source_name: p.source_name, source_text: p.source_text })
      onSaved(d.id)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy('')
    }
  }

  const meta = (p?.operations || []).find((o: any) => o.op === 'set_meta') || {}

  return (
    <Card
      title="Propose a policy change"
      actions={
        <Button kind="ghost" onClick={onClose}>
          Close
        </Button>
      }
    >
      {!p && (
        <div className="space-y-3">
          <div className="flex gap-2">
            <Button kind={mode === 'doc' ? 'primary' : 'secondary'} onClick={() => setMode('doc')}>
              Upload amendment document
            </Button>
            <Button kind={mode === 'text' ? 'primary' : 'secondary'} onClick={() => setMode('text')}>
              Describe the change
            </Button>
            <label className="ml-auto flex items-center gap-2 text-sm text-slate-600">
              <input type="checkbox" checked={useAi} onChange={(e) => setUseAi(e.target.checked)} /> Use AI reader
            </label>
          </div>
          {mode === 'doc' ? (
            <div className="flex items-center gap-3">
              <FilePick accept=".pdf,.docx,.txt,.md" label="Choose PDF, DOCX or TXT" onFile={readDoc} disabled={!!busy} />
              <span className="text-xs text-slate-500">The document is read, not applied. You review everything before it takes effect.</span>
            </div>
          ) : (
            <div className="space-y-2">
              <textarea
                className={inputCls}
                rows={4}
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder="e.g. From 20 Nov 2026, the spot PO limit at Chakan (CHK1) is 1,50,000. Version 5.1."
              />
              <Button kind="primary" onClick={readText} disabled={!!busy || !text.trim()}>
                Read this change
              </Button>
            </div>
          )}
        </div>
      )}
      {busy && (
        <div className="mt-3">
          <Spinner label={busy} />
        </div>
      )}
      {error && (
        <div className="mt-3">
          <Alert>{error}</Alert>
        </div>
      )}

      {p && (
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-[1fr_1fr_2fr]">
            <Field label="New version">
              <input className={inputCls} defaultValue={meta.version || ''} onBlur={(e) => e.target.value !== meta.version && setMeta('version', e.target.value)} />
            </Field>
            <Field label="Effective from" hint="Requirements dated on or after this date use the new version">
              <input
                type="date"
                className={inputCls}
                defaultValue={meta.effective_from || ''}
                onChange={(e) => e.target.value && e.target.value !== meta.effective_from && setMeta('effective_from', e.target.value)}
              />
            </Field>
            <div className="text-xs text-slate-500">
              Read by: <b>{p.method === 'llm' ? 'AI reader' : 'rule-based reader'}</b>
              {p.llm_note && <div className="mt-1 text-amber-700">{p.llm_note}</div>}
              <div className="mt-1">Base: v{p.base_version} · source: {p.source_name}</div>
            </div>
          </div>

          {p.summary && (
            <div className="rounded-md bg-slate-50 p-3 text-sm text-slate-700">
              <span className="font-medium">What the reader understood: </span>
              {p.summary}
            </div>
          )}

          <div>
            <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">What will change (computed by the app)</div>
            {p.changes?.length ? <ChangeList changes={p.changes} /> : <div className="text-sm text-slate-500">No change.</div>}
          </div>

          {p.errors?.length > 0 && (
            <Alert>
              <b>This proposal does not validate yet:</b>
              <ul className="ml-4 list-disc">
                {p.errors.map((e: string, i: number) => (
                  <li key={i}>{e}</li>
                ))}
              </ul>
              Fix the operations below and press Re-check.
            </Alert>
          )}
          {(p.assumptions?.length > 0 || p.unclear?.length > 0) && (
            <Alert tone="amber">
              {p.assumptions?.length > 0 && (
                <>
                  <b>Assumptions</b>
                  <ul className="mb-1 ml-4 list-disc">
                    {p.assumptions.map((a: string, i: number) => (
                      <li key={i}>{a}</li>
                    ))}
                  </ul>
                </>
              )}
              {p.unclear?.length > 0 && (
                <>
                  <b>Not understood: check and add by hand if needed</b>
                  <ul className="ml-4 list-disc">
                    {p.unclear.map((a: string, i: number) => (
                      <li key={i}>{a}</li>
                    ))}
                  </ul>
                </>
              )}
            </Alert>
          )}

          {p.impact && <Impact impact={p.impact} />}

          <div>
            <button className="text-xs font-medium text-indigo-600 hover:underline" onClick={() => setEditing(!editing)}>
              {editing ? 'Hide' : 'Edit'} the operations (JSON)
            </button>
            {editing && (
              <div className="mt-2 space-y-2">
                <textarea className={cx(inputCls, 'font-mono text-xs')} rows={14} value={opsText} onChange={(e) => setOpsText(e.target.value)} />
                <Button onClick={() => recheck()}>Re-check</Button>
              </div>
            )}
          </div>

          <div className="flex gap-2 border-t border-slate-100 pt-3">
            <Button kind="primary" onClick={save} disabled={!p.valid || !!busy}>
              Save as draft for approval
            </Button>
            <Button kind="ghost" onClick={() => setP(null)}>
              Start over
            </Button>
          </div>
        </div>
      )}
    </Card>
  )
}
