import { useEffect, useState } from 'react'
import type { Identity, Role } from './api'
import { loadIdentity, saveIdentity, setCurrentIdentity } from './api'
import { cx, inputCls } from './ui'
import Batches from './pages/Batches'
import Chat from './pages/Chat'
import Contracts from './pages/Contracts'
import Data from './pages/Data'
import Policy from './pages/Policy'
import Suppliers from './pages/Suppliers'
import Workspace from './pages/Workspace'

type Page = { id: string; label: string; el: () => React.ReactElement }

const PAGES: Record<Role, Page[]> = {
  requester: [
    { id: 'chat', label: 'New request', el: () => <Chat /> },
    { id: 'upload', label: 'Upload batch', el: () => <Batches compact /> },
  ],
  buyer: [
    { id: 'workspace', label: 'Workspace', el: () => <Workspace /> },
    { id: 'contracts', label: 'Contracts', el: () => <Contracts /> },
    { id: 'suppliers', label: 'Preferred suppliers', el: () => <Suppliers /> },
  ],
  procurement: [
    { id: 'policy', label: 'Policy', el: () => <Policy /> },
    { id: 'data', label: 'Data & runs', el: () => <Data /> },
  ],
}

const ROLE_LABEL: Record<Role, string> = { requester: 'Requester', buyer: 'Buyer', procurement: 'Procurement' }

function readHash(): { role: Role; page: string } | null {
  const m = /^#\/(requester|buyer|procurement)\/(\w+)/.exec(window.location.hash)
  return m ? { role: m[1] as Role, page: m[2] } : null
}

export default function App() {
  const [id, setId] = useState<Identity>(() => {
    const base = loadIdentity()
    const h = readHash()
    return h ? { ...base, role: h.role } : base
  })
  const [page, setPage] = useState<string>(() => readHash()?.page || PAGES[id.role][0].id)

  useEffect(() => {
    setCurrentIdentity(id)
    saveIdentity(id)
  }, [id])
  useEffect(() => {
    const valid = PAGES[id.role].some((p) => p.id === page) ? page : PAGES[id.role][0].id
    if (valid !== page) setPage(valid)
    window.history.replaceState(null, '', `#/${id.role}/${valid}`)
  }, [id.role, page])
  useEffect(() => {
    const onHash = () => {
      const h = readHash()
      if (h) {
        setId((x) => ({ ...x, role: h.role }))
        setPage(h.page)
      }
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const current = PAGES[id.role].find((p) => p.id === page) || PAGES[id.role][0]

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-2.5">
          <div className="flex items-center gap-2">
            <div className="flex h-7 w-7 items-center justify-center rounded-md bg-indigo-600 text-xs font-bold text-white">C</div>
            <div className="leading-tight">
              <div className="text-sm font-semibold">Corvo PR-to-PO</div>
              <div className="text-[11px] text-slate-500">policy-driven purchasing</div>
            </div>
          </div>
          <nav className="flex gap-1">
            {PAGES[id.role].map((p) => (
              <button
                key={p.id}
                onClick={() => setPage(p.id)}
                className={cx('rounded-md px-3 py-1.5 text-sm font-medium', p.id === current.id ? 'bg-indigo-50 text-indigo-700' : 'text-slate-600 hover:bg-slate-100')}
              >
                {p.label}
              </button>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-2 text-sm">
            <span className="text-xs text-slate-500">Acting as</span>
            <div className="flex rounded-md border border-slate-300 p-0.5">
              {(Object.keys(PAGES) as Role[]).map((r) => (
                <button
                  key={r}
                  onClick={() => {
                    setId({ ...id, role: r })
                    setPage(PAGES[r][0].id)
                  }}
                  className={cx('rounded px-2.5 py-1 text-xs font-medium', id.role === r ? 'bg-slate-900 text-white' : 'text-slate-600 hover:bg-slate-100')}
                >
                  {ROLE_LABEL[r]}
                </button>
              ))}
            </div>
            <input className={cx(inputCls, 'w-28 py-1 text-xs')} value={id.name} onChange={(e) => setId({ ...id, name: e.target.value })} title="Your name (recorded on every action)" />
            {id.role === 'requester' && (
              <select className={cx(inputCls, 'w-32 py-1 text-xs')} value={id.department} onChange={(e) => setId({ ...id, department: e.target.value })}>
                {['Maintenance', 'Production', 'Stores', 'Quality', 'Tool Room', 'IT', 'Admin'].map((d) => (
                  <option key={d}>{d}</option>
                ))}
              </select>
            )}
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-5">{current.el()}</main>
    </div>
  )
}
