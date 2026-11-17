import { useEffect, useState } from 'react'
import { get, upload } from '../api'
import { Alert, Button, Card, FilePick, Spinner } from '../ui'
import Batches from './Batches'

const LABELS: Record<string, string> = {
  items: 'Items',
  suppliers: 'Suppliers',
  plants: 'Plants',
  po_history: 'PO history',
  contracts: 'Contracts',
  contracts_pdf_or_manual: '…of which PDF / manual',
  contract_lines: 'Contract lines',
  batches: 'PR batches',
  policy_versions: 'Policy versions',
}

export default function Data() {
  const [counts, setCounts] = useState<Record<string, number> | null>(null)
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState<{ tone: 'green' | 'red' | 'amber'; text: string } | null>(null)
  const [reg, setReg] = useState<File | null>(null)

  const refresh = () => get('/api/master-data/summary').then(setCounts).catch(() => setCounts(null))
  useEffect(() => {
    refresh()
  }, [])

  async function run(label: string, fn: () => Promise<any>) {
    setBusy(label)
    setMsg(null)
    try {
      const r = await fn()
      const issues = r.issues?.length ? ` ${r.issues.length} issue(s): ${r.issues.slice(0, 5).join('; ')}` : ''
      setMsg({ tone: r.issues?.length ? 'amber' : 'green', text: `Loaded ${typeof r.loaded === 'object' ? JSON.stringify(r.loaded) : r.loaded}.${issues}` })
      refresh()
    } catch (e) {
      setMsg({ tone: 'red', text: (e as Error).message })
    } finally {
      setBusy('')
    }
  }

  const empty = counts && counts.items === 0

  return (
    <div className="space-y-4">
      <Card
        title="Master data"
        actions={<FilePick accept=".zip" label={empty ? 'Load the task kit (.zip)' : 'Reload from kit .zip'} onFile={(f) => run('Loading kit…', () => upload('/api/master-data/kit-zip', { file: f }))} disabled={!!busy} />}
      >
        {empty && (
          <div className="mb-3">
            <Alert tone="amber">No master data yet. Load the task kit zip (item master, suppliers, PO history, plants, contract register and lines).</Alert>
          </div>
        )}
        {counts && (
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
            {Object.entries(counts).map(([k, v]) => (
              <div key={k} className="rounded-md bg-slate-50 px-3 py-2">
                <div className="text-xs text-slate-500">{LABELS[k] || k}</div>
                <div className="text-lg font-semibold">{v}</div>
              </div>
            ))}
          </div>
        )}
        <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3 text-sm">
          <span className="text-xs text-slate-500">Or one file at a time:</span>
          {[
            ['items', 'Item master'],
            ['suppliers', 'Supplier master'],
            ['plants', 'Plants'],
            ['po-history', 'PO history'],
          ].map(([kind, label]) => (
            <FilePick key={kind} accept=".csv" label={label} onFile={(f) => run(`Loading ${label}…`, () => upload(`/api/master-data/${kind}`, { file: f }))} disabled={!!busy} />
          ))}
          <FilePick accept=".csv" label={reg ? `Register: ${reg.name}` : 'Contract register'} onFile={setReg} />
          <FilePick
            accept=".csv"
            label="Contract lines"
            disabled={!reg || !!busy}
            onFile={(f) => run('Loading contracts…', () => upload('/api/master-data/contracts', { register_csv: reg!, lines_csv: f }))}
          />
        </div>
        <div className="mt-2 text-xs text-slate-500">
          Contracts that exist only as signed PDFs are added under Buyer → Contracts. {counts && counts.contracts_pdf_or_manual === 0 && <b className="text-amber-700">None added yet.</b>}
        </div>
        {busy && (
          <div className="mt-3">
            <Spinner label={busy} />
          </div>
        )}
        {msg && (
          <div className="mt-3">
            <Alert tone={msg.tone}>{msg.text}</Alert>
          </div>
        )}
      </Card>
      <Batches />
      <Card title="Blind-test checklist">
        <ol className="ml-4 list-decimal space-y-1 text-sm text-slate-700">
          <li>Master data loaded (above) and both PDF-only contracts added under Buyer → Contracts.</li>
          <li>Run 1: upload the new PR batch above under policy v5.0, export as harsh__run1.csv.</li>
          <li>Procurement → Policy: upload the amendment, check the effective date and the changes, save the draft, approve.</li>
          <li>Run 2: “Re-run under current policy” on the same batch, export as harsh__run2.csv.</li>
          <li>Buyer → Suppliers: set the given date, export harsh__preferred.csv.</li>
        </ol>
        <div className="mt-3">
          <Button kind="ghost" onClick={refresh}>
            Refresh counts
          </Button>
        </div>
      </Card>
    </div>
  )
}
