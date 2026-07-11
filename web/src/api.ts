import type { Meta, NewProjectInput, ProjectDetail, ProjectSummary } from './types'

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
}
