import { useEffect, useState } from 'react'
import { get, post, upload } from '../api'
import { Alert, Badge, Button, Card, Field, FilePick, Spinner, inputCls, inr } from '../ui'

const EMPTY = { contract_id: '', supplier_id: '', supplier_name: '', start_date: '', end_date: '', value_cap: '', consumed_value: '0', source: 'manual', source_file: null, lines: [] as any[] }

export default function Contracts() {
  const [rows, setRows] = useState<any[]>([])
  const [form, setForm] = useState<any>(null)
  const [warnings, setWarnings] = useState<string[]>([])
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [saved, setSaved] = useState('')

  const refresh = () => get('/api/contracts').then(setRows).catch((e) => setError(e.message))
  useEffect(() => {
    refresh()
  }, [])

  async function fromPdf(f: File) {
    setBusy(`Reading ${f.name}…`)
    setError('')
    setSaved('')
    try {
      const r = await upload('/api/contracts/extract-pdf', { file: f })
      setForm({ ...r, value_cap: String(r.value_cap ?? ''), consumed_value: '0', source: 'pdf', lines: r.lines || [] })
      setWarnings(r.warnings || [])
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy('')
    }
  }

  async function save() {
    setBusy('Saving…')
    setError('')
    try {
      const r = await post('/api/contracts', {
        ...form,
        value_cap: Number(form.value_cap),
        consumed_value: Number(form.consumed_value || 0),
        lines: form.lines.map((l: any) => ({ ...l, rate: Number(l.rate) })),
      })
      setSaved(`Saved ${r.saved}.${r.warnings.length ? ' ' + r.warnings.join(' ') : ''} Re-run a batch to use it.`)
      setForm(null)
      refresh()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy('')
    }
  }

  const known = new Set(rows.map((r) => r.contract_id))

  return (
    <div className="space-y-4">
      <Card
        title="Add a contract that exists only as a signed PDF"
        actions={
          <>
            <Button kind="ghost" onClick={() => setForm({ ...EMPTY, lines: [{ item_code: '', description: '', uom: '', rate: '' }] })}>
              Enter by hand
            </Button>
            <FilePick accept=".pdf" label="Read contract PDF" onFile={fromPdf} disabled={!!busy} />
          </>
        }
      >
        <p className="text-sm text-slate-600">
          The register is updated monthly. Contracts signed after its last update are binding from their start date (policy 4.4) and start with nothing consumed.
        </p>
        {busy && (
          <div className="mt-2">
            <Spinner label={busy} />
          </div>
        )}
        {error && (
          <div className="mt-2">
            <Alert>{error}</Alert>
          </div>
        )}
        {saved && (
          <div className="mt-2">
            <Alert tone="green">{saved}</Alert>
          </div>
        )}
        {form && (
          <div className="mt-4 space-y-3 rounded-md border border-slate-200 p-3">
            {warnings.length > 0 && <Alert tone="amber">Check these fields: {warnings.join('; ')}</Alert>}
            {known.has(form.contract_id) && <Alert tone="amber">{form.contract_id} already exists; saving replaces it.</Alert>}
            <div className="grid gap-3 sm:grid-cols-3">
              {[
                ['contract_id', 'Contract no.'],
                ['supplier_id', 'Supplier ID'],
                ['supplier_name', 'Supplier name'],
                ['start_date', 'Valid from (YYYY-MM-DD)'],
                ['end_date', 'Valid to'],
                ['value_cap', 'Value ceiling (INR)'],
              ].map(([k, label]) => (
                <Field key={k} label={label}>
                  <input className={inputCls} value={form[k] ?? ''} onChange={(e) => setForm({ ...form, [k]: e.target.value })} />
                </Field>
              ))}
            </div>
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-slate-500">
                <tr>
                  <th>Item code</th>
                  <th>Description</th>
                  <th>UoM</th>
                  <th>Rate (INR)</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {form.lines.map((l: any, i: number) => (
                  <tr key={i}>
                    {['item_code', 'description', 'uom', 'rate'].map((k) => (
                      <td key={k} className="py-1 pr-2">
                        <input
                          className={inputCls}
                          value={l[k] ?? ''}
                          onChange={(e) => {
                            const lines = [...form.lines]
                            lines[i] = { ...l, [k]: e.target.value }
                            setForm({ ...form, lines })
                          }}
                        />
                      </td>
                    ))}
                    <td>
                      <Button kind="ghost" onClick={() => setForm({ ...form, lines: form.lines.filter((_: any, j: number) => j !== i) })}>
                        ✕
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="flex gap-2">
              <Button kind="ghost" onClick={() => setForm({ ...form, lines: [...form.lines, { item_code: '', description: '', uom: '', rate: '' }] })}>
                + Item
              </Button>
              <div className="ml-auto flex gap-2">
                <Button kind="ghost" onClick={() => setForm(null)}>
                  Cancel
                </Button>
                <Button kind="primary" onClick={save} disabled={!!busy}>
                  Save contract
                </Button>
              </div>
            </div>
          </div>
        )}
      </Card>

      <Card title={`Contracts (${rows.length})`}>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-slate-500">
              <tr>
                <th className="py-1">Contract</th>
                <th>Supplier</th>
                <th>Valid</th>
                <th className="text-right">Ceiling</th>
                <th className="text-right">Consumed (at load)</th>
                <th>Items and rates</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.contract_id} className="border-t border-slate-100 align-top">
                  <td className="py-2 font-medium">
                    {c.contract_id} <Badge tone={c.source === 'register' ? 'slate' : 'indigo'}>{c.source}</Badge>
                  </td>
                  <td>
                    {c.supplier_id} <span className="text-slate-500">{c.supplier_name}</span>
                  </td>
                  <td className="text-xs">
                    {c.start_date} to {c.end_date}
                  </td>
                  <td className="text-right">₹{inr(c.value_cap)}</td>
                  <td className="text-right">₹{inr(c.consumed_value)}</td>
                  <td className="text-xs">
                    {c.lines.map((l: any) => (
                      <div key={l.item_code}>
                        {l.item_code} {l.description}: ₹{inr(l.rate, 2)}
                      </div>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  )
}
