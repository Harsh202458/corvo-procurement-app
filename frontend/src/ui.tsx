import type { ReactNode } from 'react'

export function inr(v: number | string | null | undefined, decimals?: number): string {
  if (v === null || v === undefined || v === '') return '–'
  const n = typeof v === 'string' ? Number(v) : v
  if (!Number.isFinite(n)) return String(v)
  const d = decimals ?? (Math.abs(n) < 1000 && Math.round(n) !== n ? 2 : 0)
  return n.toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d })
}

export function cx(...parts: (string | false | null | undefined)[]) {
  return parts.filter(Boolean).join(' ')
}

type BtnKind = 'primary' | 'secondary' | 'ghost' | 'danger'

export function Button({
  kind = 'secondary',
  className,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { kind?: BtnKind }) {
  const styles: Record<BtnKind, string> = {
    primary: 'bg-indigo-600 text-white hover:bg-indigo-700 shadow-sm disabled:bg-indigo-300',
    secondary: 'bg-white text-slate-700 border border-slate-300 hover:bg-slate-50 disabled:text-slate-400',
    ghost: 'text-slate-600 hover:bg-slate-100 disabled:text-slate-300',
    danger: 'bg-white text-rose-700 border border-rose-300 hover:bg-rose-50',
  }
  return (
    <button
      {...props}
      className={cx(
        'inline-flex items-center justify-center gap-2 rounded-md px-3.5 py-2 text-sm font-medium transition disabled:cursor-not-allowed',
        styles[kind],
        className,
      )}
    />
  )
}

export function Card({ title, actions, children, className }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cx('rounded-lg border border-slate-200 bg-white shadow-sm', className)}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-3 border-b border-slate-100 px-4 py-3">
          <h2 className="text-sm font-semibold text-slate-800">{title}</h2>
          <div className="flex items-center gap-2">{actions}</div>
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  )
}

const ROUTE_STYLE: Record<string, string> = {
  CONTRACT_PO: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  SPOT_PO: 'bg-sky-50 text-sky-700 ring-sky-200',
  EMERGENCY_PO: 'bg-rose-50 text-rose-700 ring-rose-200',
  RFQ: 'bg-amber-50 text-amber-800 ring-amber-200',
  RFP: 'bg-orange-50 text-orange-800 ring-orange-200',
  CAPEX_SOURCING: 'bg-violet-50 text-violet-700 ring-violet-200',
  NEW_ITEM_SOURCING: 'bg-fuchsia-50 text-fuchsia-700 ring-fuchsia-200',
  NOT_PROCESSED: 'bg-slate-100 text-slate-600 ring-slate-200',
}

export function RouteBadge({ route }: { route: string }) {
  return (
    <span className={cx('inline-flex rounded px-1.5 py-0.5 text-[11px] font-semibold ring-1 ring-inset whitespace-nowrap', ROUTE_STYLE[route] || 'bg-indigo-50 text-indigo-700 ring-indigo-200')}>
      {route.replaceAll('_', ' ')}
    </span>
  )
}

export function Badge({ children, tone = 'slate' }: { children: ReactNode; tone?: 'slate' | 'green' | 'amber' | 'red' | 'indigo' | 'blue' }) {
  const t = {
    slate: 'bg-slate-100 text-slate-700',
    green: 'bg-emerald-100 text-emerald-800',
    amber: 'bg-amber-100 text-amber-800',
    red: 'bg-rose-100 text-rose-800',
    indigo: 'bg-indigo-100 text-indigo-800',
    blue: 'bg-sky-100 text-sky-800',
  }[tone]
  return <span className={cx('inline-flex rounded-full px-2 py-0.5 text-[11px] font-medium whitespace-nowrap', t)}>{children}</span>
}

export function StatusBadge({ status }: { status: string }) {
  const tone = status === 'approved' ? 'green' : status === 'overridden' ? 'amber' : status === 'rejected' ? 'red' : status === 'draft' ? 'indigo' : 'slate'
  return <Badge tone={tone}>{status}</Badge>
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-sm text-slate-500">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-indigo-600" />
      {label}
    </span>
  )
}

export function Alert({ tone = 'red', children }: { tone?: 'red' | 'amber' | 'green' | 'blue'; children: ReactNode }) {
  const t = {
    red: 'border-rose-200 bg-rose-50 text-rose-800',
    amber: 'border-amber-200 bg-amber-50 text-amber-900',
    green: 'border-emerald-200 bg-emerald-50 text-emerald-800',
    blue: 'border-sky-200 bg-sky-50 text-sky-900',
  }[tone]
  return <div className={cx('rounded-md border px-3 py-2 text-sm', t)}>{children}</div>
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-md border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500">{children}</div>
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="block text-sm">
      <span className="mb-1 block text-xs font-medium text-slate-600">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-slate-500">{hint}</span>}
    </label>
  )
}

export const inputCls =
  'w-full rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-900 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-200'

export function FilePick({ accept, onFile, label, disabled }: { accept: string; onFile: (f: File) => void; label: string; disabled?: boolean }) {
  return (
    <label className={cx('inline-flex cursor-pointer items-center gap-2 rounded-md border border-slate-300 bg-white px-3.5 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50', disabled && 'pointer-events-none opacity-50')}>
      {label}
      <input
        type="file"
        accept={accept}
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0]
          if (f) onFile(f)
          e.target.value = ''
        }}
      />
    </label>
  )
}

/** Reason strings are " | "-separated parts; show them as a list. */
export function ReasonList({ reason }: { reason: string }) {
  const parts = reason.split(' | ').filter(Boolean)
  return (
    <ul className="space-y-1 text-sm text-slate-700">
      {parts.map((p, i) => (
        <li key={i} className={cx(i === 0 && 'font-medium text-slate-900')}>
          {i > 0 && <span className="mr-1 text-slate-400">•</span>}
          {p}
        </li>
      ))}
    </ul>
  )
}
