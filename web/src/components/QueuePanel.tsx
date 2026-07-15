import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { QueueItem } from '../types'

// 全局生成队列:展示 GET /api/queue(基于内存态 _JOBS 实时组装),自身轮询保持新鲜。
// 队列为空时不占版面。
export function QueuePanel({
  user,
  onSelect,
}: {
  user: string
  onSelect: (id: string) => void
}) {
  const [items, setItems] = useState<QueueItem[]>([])
  const [busyId, setBusyId] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      setItems(await api.getQueue())
    } catch {
      /* 后端未启动时静默 */
    }
  }, [])

  useEffect(() => {
    refresh()
    const timer = window.setInterval(refresh, 3000)
    return () => window.clearInterval(timer)
  }, [refresh])

  async function cancel(id: string) {
    setBusyId(id)
    try {
      await api.cancelProject(id)
      await refresh()
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e))
    } finally {
      setBusyId(null)
    }
  }

  if (items.length === 0) return null

  return (
    <div className="rounded-2xl border border-band bg-paper p-5 shadow-paper">
      <div className="mb-3 flex items-center gap-2.5">
        <span className="flex h-7 w-7 items-center justify-center rounded-md bg-ink font-brush text-lg text-rice">
          队
        </span>
        <h2 className="font-serif text-base font-semibold tracking-wide text-ink">生成队列</h2>
      </div>

      <ul className="space-y-1.5">
        {items.map((it) => (
          <li
            key={it.project_id}
            onClick={() => onSelect(it.project_id)}
            className="flex cursor-pointer items-center justify-between gap-2 rounded-lg border border-transparent px-3 py-2.5 transition hover:border-line hover:bg-white/50"
          >
            <span className="min-w-0">
              <span className="block truncate font-serif text-sm text-ink">{it.scenic_spot}</span>
              <span className="block truncate text-[11px] text-muted">
                {it.project_id.slice(0, 8)} · {it.owner || '未知'} · {it.pipeline}
              </span>
            </span>
            {it.owner === user && (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation()
                  cancel(it.project_id)
                }}
                disabled={busyId === it.project_id}
                className="shrink-0 rounded-lg border border-line px-3 py-1.5 text-xs text-ink-soft transition hover:border-cinnabar hover:text-cinnabar disabled:cursor-not-allowed disabled:opacity-40"
              >
                {busyId === it.project_id ? '取消中…' : '取消'}
              </button>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}
