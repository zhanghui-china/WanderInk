import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { CardHead } from './decor'
import { GeneratingBars } from './GeneratingBars'
import { pipelineLabel } from '../pipeline'
import type { QueueItem } from '../types'

// 全局生成队列:展示 GET /api/queue(基于内存态 _JOBS 实时组装),自身轮询保持新鲜。
// 队列为空时不占版面。
export function QueuePanel({
  user,
  isAdmin,
  onSelect,
}: {
  user: string
  isAdmin: boolean
  onSelect: (id: string) => void
}) {
  const [items, setItems] = useState<QueueItem[]>([])
  const [busyId, setBusyId] = useState<string | null>(null)
  const [cancellingIds, setCancellingIds] = useState<Set<string>>(new Set())

  const refresh = useCallback(async () => {
    try {
      const fresh = await api.getQueue()
      setItems(fresh)
      setCancellingIds((prev) => new Set([...prev].filter((id) => fresh.some((it) => it.project_id === id))))
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
      const res = await api.cancelProject(id)
      if (res.cancelling === true) {
        setCancellingIds((prev) => new Set(prev).add(id))
      }
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
      <CardHead glyph="队" title="生成队列" />

      <ul className="space-y-1.5">
        {items.map((it) => (
          <li
            key={it.project_id}
            onClick={() => onSelect(it.project_id)}
            className="flex cursor-pointer items-center justify-between gap-2 rounded-lg border border-transparent px-3 py-2.5 transition hover:border-line hover:bg-white/50"
          >
            <span className="flex min-w-0 items-center gap-2.5">
              <GeneratingBars />
              <span className="min-w-0">
                <span className="block truncate font-serif text-sm text-ink">{it.scenic_spot}</span>
                <span className="block truncate text-[11px] text-muted">
                  {/* 状态走统一映射,不再渲染 running/queued 这种原值(见 ../pipeline.ts)。
                      这一行是紧凑的三段式,只取中文短标签、不带 detail。 */}
                  {it.project_id.slice(0, 8)} · {it.owner || '未知'} ·{' '}
                  {pipelineLabel(it.pipeline).text}
                </span>
              </span>
            </span>
            {/* 判据必须与后端 _may_edit 一致:自己的 / 管理员。
                2026-08-06 后端去掉了「无主则谁都能改」,这里跟着去掉 `!it.owner`——两侧任一
                多放行一颗按钮,下场都是点了报 403,而不是真能取消。 */}
            {(it.owner === user || isAdmin) &&
              (it.pipeline === 'queued' || it.pipeline === 'running') && (
              cancellingIds.has(it.project_id) ? (
                <button
                  type="button"
                  disabled
                  className="shrink-0 rounded-lg border border-line px-3 py-1.5 text-xs text-ink-soft transition hover:border-cinnabar hover:text-cinnabar disabled:cursor-not-allowed disabled:opacity-40"
                >
                  取消中…
                </button>
              ) : (
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
              )
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}
