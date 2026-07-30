import { useState } from 'react'
import { api } from '../api'
import { CardHead } from './decor'
import { pipelineLabel } from '../pipeline'
import type { ProjectSummary } from '../types'

export function ProjectList({
  items,
  selectedId,
  onSelect,
  isAdmin,
  onDeleted,
}: {
  items: ProjectSummary[]
  selectedId: string | null
  onSelect: (id: string) => void
  isAdmin?: boolean
  onDeleted?: (id: string) => void
}) {
  const [deletingId, setDeletingId] = useState<string | null>(null)

  async function handleDelete(e: React.MouseEvent, id: string, label: string) {
    e.stopPropagation()
    if (!window.confirm(`确定删除作品「${label}」?生成产物会一并清除,不可恢复。`)) return
    setDeletingId(id)
    try {
      await api.deleteProject(id)
      onDeleted?.(id)
    } catch (err) {
      alert(err instanceof Error ? err.message : String(err))
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <div className="rounded-2xl border border-band bg-paper p-5 shadow-paper">
      <CardHead glyph="集" title="作品列表" />

      {items.length === 0 && (
        <p className="py-6 text-center text-sm text-muted">还没有作品</p>
      )}

      <ul className="space-y-1.5">
        {items.map((p) => {
          const b = pipelineLabel(p.pipeline)
          const on = selectedId === p.project_id
          return (
            <li key={p.project_id} className="flex items-stretch gap-1.5">
              <button
                onClick={() => onSelect(p.project_id)}
                className={`flex min-w-0 flex-1 items-center justify-between gap-2 rounded-lg border px-3 py-2.5 text-left transition ${
                  on
                    ? 'border-cinnabar bg-white shadow-paper'
                    : 'border-transparent hover:border-line hover:bg-white/50'
                }`}
              >
                <span className="min-w-0">
                  <span className="block truncate font-serif text-sm text-ink">
                    {p.scenic_spot}
                    <span className="ml-1.5 text-[11px] font-sans font-normal text-muted">
                      {p.owner || '未知'}
                    </span>
                  </span>
                  {/* 排版方式用紧凑标记而不是详情页那两个整词:侧栏每行只有两行的密集
                      布局,给每个未分格作品都挂上「整页单图」会把列表淹掉。▦ 与进度卡的
                      「▦ 分格 4/10」同符号,两处呼应。 */}
                  <span className="block truncate text-[11px] text-muted">
                    {p.project_id}
                    <span className="ml-1.5" title={p.multi_panel ? '分格排版' : '整页单图'}>
                      {p.multi_panel ? '▦ 分格' : '▤ 单图'}
                    </span>
                  </span>
                </span>
                <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] ${b.cls}`}>
                  {b.text}
                </span>
              </button>
              {isAdmin && (
                <button
                  type="button"
                  onClick={(e) => handleDelete(e, p.project_id, p.scenic_spot)}
                  disabled={deletingId === p.project_id}
                  title="删除作品"
                  className="shrink-0 rounded-lg border border-line px-2.5 text-sm text-muted transition hover:border-alarm hover:text-alarm disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {deletingId === p.project_id ? '…' : '×'}
                </button>
              )}
            </li>
          )
        })}
      </ul>
    </div>
  )
}
