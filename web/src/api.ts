import type {
  AppConfigInput,
  AppConfigView,
  CellPatch,
  InsertCellFields,
  Meta,
  NewProjectInput,
  ProjectDetail,
  ProjectSummary,
} from './types'

// 同源部署时前端由后端托管;dev 期由 Vite 代理 /api → :8080。故 base 留空。
async function j<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(detail.detail ?? `HTTP ${res.status}`)
  }
  return res.json() as Promise<T>
}

export const api = {
  meta: () => fetch('/api/meta').then((r) => j<Meta>(r)),

  list: () => fetch('/api/projects').then((r) => j<ProjectSummary[]>(r)),

  get: (id: string) => fetch(`/api/projects/${id}`).then((r) => j<ProjectDetail>(r)),

  create: (body: NewProjectInput) =>
    fetch('/api/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then((r) => j<{ project_id: string }>(r)),

  exportProject: (id: string) =>
    fetch(`/api/projects/${id}/export`, { method: 'POST' }).then((r) =>
      j<{ pdf: string | null; zip: string | null }>(r)
    ),

  updateCell: (id: string, index: number, patch: CellPatch) =>
    fetch(`/api/projects/${id}/cells/${index}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    }).then((r) => j<ProjectDetail>(r)),

  redrawCell: (id: string, index: number) =>
    fetch(`/api/projects/${id}/cells/${index}/redraw`, { method: 'POST' }).then((r) =>
      j<ProjectDetail>(r)
    ),

  revoiceCell: (id: string, index: number) =>
    fetch(`/api/projects/${id}/cells/${index}/revoice`, { method: 'POST' }).then((r) =>
      j<ProjectDetail>(r)
    ),

  insertCell: (id: string, afterIndex: number, fields: InsertCellFields) =>
    fetch(`/api/projects/${id}/cells`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ after_index: afterIndex, ...fields }),
    }).then((r) => j<ProjectDetail>(r)),

  deleteCell: (id: string, index: number) =>
    fetch(`/api/projects/${id}/cells/${index}`, { method: 'DELETE' }).then((r) =>
      j<ProjectDetail>(r)
    ),

  reorderCells: (id: string, order: number[]) =>
    fetch(`/api/projects/${id}/cells/reorder`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ order }),
    }).then((r) => j<ProjectDetail>(r)),

  redrawCharacter: (id: string, name: string) =>
    fetch(`/api/projects/${id}/characters/${encodeURIComponent(name)}/redraw`, {
      method: 'POST',
    }).then((r) => j<ProjectDetail>(r)),

  runStep: (id: string, name: string) =>
    fetch(`/api/projects/${id}/steps/${name}`, { method: 'POST' }).then((r) =>
      j<{ queued: boolean }>(r)
    ),

  getConfig: () => fetch('/api/config').then((r) => j<AppConfigView>(r)),

  saveConfig: (body: AppConfigInput) =>
    fetch('/api/config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then((r) => j<AppConfigView>(r)),
}
