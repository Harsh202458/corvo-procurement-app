import { useEffect, useState } from 'react'
import { download, get, post, upload } from '../api'
import { Alert, Button, Card, Empty, FilePick, RouteBadge, Spinner, inputCls } from '../ui'

/** PR batch upload, processing runs and exports (Run 1 / Run 2 of the blind test). */
export default function Batches({ compact = false }: { compact?: boolean }) {
  const [runs, setRuns] = useState<any[]>([])
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [last, setLast] = useState<any>(null)
  const [names, setNames] = useState<Record<number, string>>({})

  async function refresh() {
    try {
      setRuns(await get('/api/runs'))
    } catch (e) {
      setError((e as Error).message)
    }
  }
  useEffect(() => {
    refresh()
  }, [])

  async function onFile(f: File) {
    setBusy(`Processing ${f.name}…`)
    setError('')
    try {
      const r = await upload('/api/process-batch', { file: f })
      setLast(r)
      await refresh()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy('')
    }
  }

  async function reprocess(batchId: number) {
    setBusy('Re-running the batch under the current policy versions…')
    setError('')
    try {
      const r = await post(`/api/batches/${batchId}/process`)
      setLast({ run_id: r.run_id, summary: r.summary, issues: [] })
      await refresh()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy('')
    }
  }

  // default export names: nth run of a batch -> harsh__run{n}.csv
  function defaultName(run: any) {
    const same = runs.filter((r) => r.batch_id === run.batch_id).sort((a, b) => a.id - b.id)
    return `harsh__run${same.findIndex((r) => r.id === run.id) + 1}.csv`
  }

  return (
    <div className="space-y-4">
      <Card
        title="Process a PR batch"
        actions={<FilePick accept=".csv,text/csv" label="Upload PR batch CSV" onFile={onFile} disabled={!!busy} />}
      >
        <p className="text-sm text-slate-600">
          Upload a CSV in the sample format (pr_no, pr_line, created_at, requester, department, plant, item_code, description, quantity, uom,
          estimated_unit_price, need_by_date, urgency). Every line is decided under the policy version in force on its requirement date.
        </p>
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
        {last && (
          <div className="mt-3 space-y-2">
            <Alert tone="green">
              Run #{last.run_id}: {last.summary.lines} lines, {last.summary.requirements} requirements, {last.summary.purchase_orders} POs, under policy{' '}
              {last.summary.policy_versions.map((v: any) => 'v' + v.version).join(', ')}. Open the Buyer workspace to review.
            </Alert>
            {last.issues?.length > 0 && <Alert tone="amber">File issues: {last.issues.join('; ')}</Alert>}
          </div>
        )}
      </Card>

      {!compact && (
        <Card title="Runs and exports">
          {runs.length === 0 ? (
            <Empty>No runs yet. Upload a PR batch above.</Empty>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-xs text-slate-500">
                  <tr>
                    <th className="py-1.5">Run</th>
                    <th>Batch</th>
                    <th>When / who</th>
                    <th>Policy</th>
                    <th>Routes</th>
                    <th className="w-[320px]">Export decisions</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map((r) => (
                    <tr key={r.id} className="border-t border-slate-100 align-top">
                      <td className="py-2 font-medium">#{r.id}</td>
                      <td>
                        {r.batch_name}
                        <div className="text-xs text-slate-500">batch {r.batch_id}</div>
                        <button className="mt-1 text-xs font-medium text-indigo-600 hover:underline" onClick={() => reprocess(r.batch_id)} disabled={!!busy}>
                          Re-run under current policy
                        </button>
                      </td>
                      <td className="text-xs text-slate-600">
                        {new Date(r.run_at).toLocaleString('en-IN')}
                        <div>{r.run_by}</div>
                      </td>
                      <td className="text-xs">{r.summary.policy_versions?.map((v: any) => 'v' + v.version).join(', ')}</td>
                      <td>
                        <div className="flex max-w-[280px] flex-wrap gap-1">
                          {Object.entries(r.summary.by_route || {}).map(([k, v]) => (
                            <span key={k} className="inline-flex items-center gap-1 text-xs">
                              <RouteBadge route={k} />
                              {String(v)}
                            </span>
                          ))}
                        </div>
                      </td>
                      <td>
                        <div className="flex gap-2">
                          <input
                            className={inputCls}
                            value={names[r.id] ?? defaultName(r)}
                            onChange={(e) => setNames({ ...names, [r.id]: e.target.value })}
                          />
                          <Button onClick={() => download(`/api/runs/${r.id}/export.csv`, { filename: names[r.id] ?? defaultName(r) })}>CSV</Button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}
    </div>
  )
}
