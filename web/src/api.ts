import type {
  AppConfigInput,
  AppConfigView,
  CellPatch,
  InsertCellFields,
  Meta,
  NewProjectInput,
  ProjectDetail,
  ProjectSummary,
  QueueItem,
} from './types'

// 携带 HTTP 状态码的错误,供调用方区分永久错误(404/401)与瞬时错误(退避重试)。
export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

// 同源部署时前端由后端托管;dev 期由 Vite 代理 /api → :8080。故 base 留空。
// credentials 用 'same-origin':登录态是 Starlette 签名 cookie,同源(含 Vite 代理转发)
// 请求需带上;dev 代理让浏览器仍视 /api 为同源,故不需要跨源的 'include'。
async function j<T>(res: Response): Promise<T> {
  if (!res.ok) {
    // HTTP/2 下 statusText 恒为空串(非 nullish,?? 兜底不触发);FastAPI 422 的 detail 是
    // 校验错误对象数组(直接塞进 Error 会变 "[object Object]")。故显式判空串、非字符串序列化。
    const body = await res.json().catch(() => null)
    const detail = body?.detail
    const msg =
      typeof detail === 'string' && detail !== ''
        ? detail
        : detail != null
          ? JSON.stringify(detail)
          : `HTTP ${res.status}`
    throw new ApiError(msg, res.status)
  }
  return res.json() as Promise<T>
}

const CREDS: RequestInit = { credentials: 'same-origin' }

export const api = {
  login: (username: string, password: string) =>
    fetch('/api/login', {
      ...CREDS,
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    }).then((r) => j<{ username: string }>(r)),

  logout: () => fetch('/api/logout', { ...CREDS, method: 'POST' }).then((r) => j<unknown>(r)),

  me: () => fetch('/api/me', CREDS).then((r) => j<{ username: string }>(r)),

  meta: () => fetch('/api/meta', CREDS).then((r) => j<Meta>(r)),

  list: () => fetch('/api/projects', CREDS).then((r) => j<ProjectSummary[]>(r)),

  get: (id: string) => fetch(`/api/projects/${id}`, CREDS).then((r) => j<ProjectDetail>(r)),

  create: (body: NewProjectInput) =>
    fetch('/api/projects', {
      ...CREDS,
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then((r) => j<{ project_id: string }>(r)),

  exportProject: (id: string) =>
    fetch(`/api/projects/${id}/export`, { ...CREDS, method: 'POST' }).then((r) =>
      j<{ pdf: string | null; zip: string | null }>(r)
    ),

  getQueue: () => fetch('/api/queue', CREDS).then((r) => j<QueueItem[]>(r)),

  cancelProject: (id: string) =>
    fetch(`/api/projects/${id}/cancel`, { ...CREDS, method: 'POST' }).then((r) =>
      j<{ cancelled?: boolean; cancelling?: boolean }>(r)
    ),

  updateCell: (id: string, index: number, patch: CellPatch) =>
    fetch(`/api/projects/${id}/cells/${index}`, {
      ...CREDS,
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    }).then((r) => j<ProjectDetail>(r)),

  redrawCell: (id: string, index: number) =>
    fetch(`/api/projects/${id}/cells/${index}/redraw`, { ...CREDS, method: 'POST' }).then((r) =>
      j<ProjectDetail>(r)
    ),

  revoiceCell: (id: string, index: number) =>
    fetch(`/api/projects/${id}/cells/${index}/revoice`, { ...CREDS, method: 'POST' }).then((r) =>
      j<ProjectDetail>(r)
    ),

  insertCell: (id: string, afterIndex: number, fields: InsertCellFields) =>
    fetch(`/api/projects/${id}/cells`, {
      ...CREDS,
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ after_index: afterIndex, ...fields }),
    }).then((r) => j<ProjectDetail>(r)),

  deleteCell: (id: string, index: number) =>
    fetch(`/api/projects/${id}/cells/${index}`, { ...CREDS, method: 'DELETE' }).then((r) =>
      j<ProjectDetail>(r)
    ),

  reorderCells: (id: string, order: number[]) =>
    fetch(`/api/projects/${id}/cells/reorder`, {
      ...CREDS,
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ order }),
    }).then((r) => j<ProjectDetail>(r)),

  redrawCharacter: (id: string, name: string) =>
    fetch(`/api/projects/${id}/characters/${encodeURIComponent(name)}/redraw`, {
      ...CREDS,
      method: 'POST',
    }).then((r) => j<ProjectDetail>(r)),

  runStep: (id: string, name: string) =>
    fetch(`/api/projects/${id}/steps/${name}`, { ...CREDS, method: 'POST' }).then((r) =>
      j<{ queued: boolean }>(r)
    ),

  getConfig: () => fetch('/api/config', CREDS).then((r) => j<AppConfigView>(r)),

  saveConfig: (body: AppConfigInput) =>
    fetch('/api/config', {
      ...CREDS,
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then((r) => j<AppConfigView>(r)),
}
