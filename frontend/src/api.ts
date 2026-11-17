// Thin API client. The acting user and department come from the header's role switcher.

export type Role = 'requester' | 'buyer' | 'procurement'

export interface Identity {
  name: string
  department: string
  role: Role
}

const KEY = 'corvo.identity'

export function loadIdentity(): Identity {
  try {
    const raw = localStorage.getItem(KEY)
    if (raw) return { name: 'harsh', department: 'Maintenance', role: 'buyer', ...JSON.parse(raw) }
  } catch {
    /* storage unavailable: use defaults */
  }
  return { name: 'harsh', department: 'Maintenance', role: 'buyer' }
}

export function saveIdentity(id: Identity) {
  try {
    localStorage.setItem(KEY, JSON.stringify(id))
  } catch {
    /* ignore */
  }
}

let current: Identity = loadIdentity()
export function setCurrentIdentity(id: Identity) {
  current = id
}

export class ApiError extends Error {
  status: number
  detail: unknown
  constructor(status: number, detail: unknown) {
    super(errorText(detail) || `Request failed (${status})`)
    this.status = status
    this.detail = detail
  }
}

function errorText(detail: unknown): string {
  if (!detail) return ''
  if (typeof detail === 'string') return detail
  const d = detail as { detail?: unknown; errors?: string[] }
  if (d.errors) return d.errors.join('; ')
  if (d.detail !== undefined) {
    if (typeof d.detail === 'string') return d.detail
    const inner = d.detail as { errors?: string[] }
    if (inner?.errors) return inner.errors.join('; ')
    if (Array.isArray(d.detail)) return d.detail.map((e: { msg?: string }) => e.msg).join('; ')
    return JSON.stringify(d.detail)
  }
  return JSON.stringify(detail)
}

function headers(extra: Record<string, string> = {}) {
  return { 'X-User': current.name || 'anonymous', 'X-Department': current.department || '', ...extra }
}

async function handle<T>(res: Response): Promise<T> {
  const text = await res.text()
  let body: unknown = text
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    /* not JSON */
  }
  if (!res.ok) throw new ApiError(res.status, body)
  return body as T
}

export async function get<T = any>(path: string, params?: Record<string, string | number | undefined | null>) {
  const q = params
    ? '?' + new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '') as [string, string][])
    : ''
  return handle<T>(await fetch(path + q, { headers: headers() }))
}

export async function post<T = any>(path: string, body?: unknown) {
  return handle<T>(
    await fetch(path, {
      method: 'POST',
      headers: headers({ 'Content-Type': 'application/json' }),
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  )
}

export async function upload<T = any>(path: string, files: Record<string, File>, fields: Record<string, string> = {}) {
  const fd = new FormData()
  for (const [k, f] of Object.entries(files)) fd.append(k, f)
  for (const [k, v] of Object.entries(fields)) fd.append(k, v)
  return handle<T>(await fetch(path, { method: 'POST', headers: headers(), body: fd }))
}

/** Download a server-generated file (CSV export) keeping the server's filename. */
export async function download(path: string, params: Record<string, string | number | undefined> = {}) {
  const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== '') as [string, string][])
  const res = await fetch(`${path}${q.toString() ? '?' + q : ''}`, { headers: headers() })
  if (!res.ok) throw new ApiError(res.status, await res.text())
  const blob = await res.blob()
  const cd = res.headers.get('content-disposition') || ''
  const name = /filename="?([^";]+)"?/.exec(cd)?.[1] || 'export.csv'
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
  return name
}
