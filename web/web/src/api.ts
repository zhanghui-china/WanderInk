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
import { xhrUpload, type UploadTarget } from './upload'

// 携带 HTTP 状态码的错误,供调用方区分永久错误(404/401)与瞬时错误(退避重试)。
export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

// 从错误响应体文本还原 ApiError。抽成纯函数是为了让 fetch 和 XHR(上传要进度事件,只能用 XHR)
// 两条路径共用同一套错误语义,否则同一个后端错误在两处会给出不同 message。
// HTTP/2 下 statusText 恒为空串(非 nullish,?? 兜底不触发);FastAPI 422 的 detail 是
// 校验错误对象数组(直接塞进 Error 会变 "[object Object]")。故显式判空串、非字符串序列化。
export function apiErrorFrom(bodyText: string | null, status: number): ApiError {
  let body: { detail?: unknown } | null = null
  try {
    body = bodyText === null ? null : JSON.parse(bodyText)
  } catch {
    body = null
  }
  const detail = body?.detail
  const msg =
    typeof detail === 'string' && detail !== ''
      ? detail
      : detail != null
        ? JSON.stringify(detail)
        : `HTTP ${status}`
  return new ApiError(msg, status)
}

// 同源部署时前端由后端托管;dev 期由 Vite 代理 /api → :8080。故 base 留空。
// credentials 用 'same-origin':登录态是 Starlette 签名 cookie,同源(含 Vite 代理转发)
// 请求需带上;dev 代理让浏览器仍视 /api 为同源,故不需要跨源的 'include'。
async function j<T>(res: Response): Promise<T> {
  if (!res.ok) {
    throw apiErrorFrom(await res.text().catch(() => null), res.status)
  }
  return res.json() as Promise<T>
}

const CREDS: RequestInit = { credentials: 'same-origin' }

export const characterReferenceTarget = (id: string, name: string): UploadTarget => ({
  url: `/api/projects/${id}/characters/${encodeURIComponent(name)}/reference`,
})

// 录音上传不绑定任何作品:新建表单(那时还没有 project_id)和作品详情页共用这一个端点,
// 只在拿到返回的 voice 之后才分叉成"建作品"或"改 params"。
export const voiceSampleTarget = (): UploadTarget => ({ url: '/api/voice-samples' })

export interface VoiceSample {
  voice: string
  sample_url: string
  duration_ms: number
}

export const api = {
  login: (username: string, password: string) =>
    fetch('/api/login', {
      ...CREDS,
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    }).then((r) => j<{ username: string }>(r)),

  logout: () => fetch('/api/logout', { ...CREDS, method: 'POST' }).then((r) => j<unknown>(r)),

  me: () => fetch('/api/me', CREDS).then((r) => j<{ username: string; is_admin: boolean }>(r)),

  meta: () => fetch('/api/meta', CREDS).then((r) => j<Meta>(r)),

  list: () => fetch('/api/projects', CREDS).then((r) => j<ProjectSummary[]>(r)),

  get: (id: string) => fetch(`/api/projects/${id}`, CREDS).then((r) => j<ProjectDetail>(r)),

  deleteProject: (id: string) =>
    fetch(`/api/projects/${id}`, { ...CREDS, method: 'DELETE' }).then((r) =>
      j<{ deleted: boolean }>(r)
    ),

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

  // ---- 附加语种轨(如英文版):翻译 + 配音 + 成片 ----
  runTrack: (id: string, lang: string) =>
    fetch(`/api/projects/${id}/tracks/${lang}`, { ...CREDS, method: 'POST' }).then((r) =>
      j<{ queued: boolean }>(r)
    ),

  patchCellTrack: (id: string, index: number, lang: string, caption: string) =>
    fetch(`/api/projects/${id}/cells/${index}/tracks/${lang}`, {
      ...CREDS,
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ caption }),
    }).then((r) => j<ProjectDetail>(r)),

  revoiceCellTrack: (id: string, index: number, lang: string) =>
    fetch(`/api/projects/${id}/cells/${index}/tracks/${lang}/revoice`, {
      ...CREDS,
      method: 'POST',
    }).then((r) => j<ProjectDetail>(r)),

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

  // 角色参考图:上传后端会解码/旋正/缩放并落盘,返回更新后的 ProjectDetail。
  // 走 xhrUpload 而非 fetch,因为要给弹窗上报上传进度。
  // 用 useUpload 托管状态的调用方直接把 characterReferenceTarget(id, name) 交给 start(),
  // URL 只在 characterReferenceTarget 一处拼,两条路径不会走偏。
  uploadCharacterReference: (
    id: string,
    name: string,
    file: File,
    onProgress: (loaded: number, total: number, lengthComputable: boolean) => void,
  ) => xhrUpload<ProjectDetail>(characterReferenceTarget(id, name), file, file.name, onProgress),

  updateProjectVoice: (id: string, voice: string) =>
    fetch(`/api/projects/${id}/params/voice`, {
      ...CREDS,
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ voice }),
    }).then((r) => j<ProjectDetail>(r)),

  removeCharacterReference: (id: string, name: string) =>
    fetch(`/api/projects/${id}/characters/${encodeURIComponent(name)}/reference`, {
      ...CREDS,
      method: 'DELETE',
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
